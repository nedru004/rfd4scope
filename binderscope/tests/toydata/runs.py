"""Toy BindCraft 1 run folders: poses, sequences, structures, metric tables, settings and failure counters."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import binder as B
from . import geometry as G
from . import poses as P
from . import structures as ST
from .plans import Spec
from .scene import choose_hotspots, make_scene, patch_point, surface_of
from .target import CONSTRUCT_FIRST, CONSTRUCT_LAST, ToyTarget

STEMS = [  # BindCraft's column order; the per-model stems have Average_, 1_ ... 5_ columns
    "pLDDT", "pTM", "i_pTM", "pAE", "i_pAE", "i_pLDDT", "ss_pLDDT", "Unrelaxed_Clashes", "Relaxed_Clashes",
    "Binder_Energy_Score", "Surface_Hydrophobicity", "ShapeComplementarity", "PackStat", "dG", "dSASA", "dG/dSASA",
    "Interface_SASA_%", "Interface_Hydrophobicity", "n_InterfaceResidues", "n_InterfaceHbonds",
    "InterfaceHbondsPercentage", "n_InterfaceUnsatHbonds", "InterfaceUnsatHbondsPercentage", "Interface_Helix%",
    "Interface_BetaSheet%", "Interface_Loop%", "Binder_Helix%", "Binder_BetaSheet%", "Binder_Loop%", "InterfaceAAs",
    "Hotspot_RMSD", "Target_RMSD", "Binder_pLDDT", "Binder_pTM", "Binder_pAE", "Binder_RMSD",
]
TRAJ_STEMS = [s for s in STEMS if s not in ("Hotspot_RMSD", "Binder_pLDDT", "Binder_pTM", "Binder_pAE", "Binder_RMSD")]
FAIL_COLUMNS = ["Trajectory_logits_pLDDT", "Trajectory_softmax_pLDDT", "Trajectory_one-hot_pLDDT", "Trajectory_final_pLDDT",
                "Trajectory_Contacts", "Trajectory_Clashes", "Trajectory_WrongHotspot", "MPNN_score", "MPNN_seq_recovery"] + \
               [s for s in STEMS if s not in ("InterfaceAAs", "Hotspot_RMSD")] + \
               [f"InterfaceAAs_{a}" for a in G.AA1] + ["HotspotRMSD"]
HELIX_END_LOOP = 1     # the first and last residue of a helix read as loop (DSSP does the same for the caps)


def duration(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600} hours, {(s % 3600) // 60} minutes, {s % 60} seconds"


@dataclass
class DesignOut:
    id: str
    status: str
    spec: str
    mpnn: int
    sequence: str
    row: dict
    models: dict            # k -> {"atoms": binder atoms in the construct frame, "iface": [...]}
    saved_model: int
    rank: int | None = None
    truth: dict = field(default_factory=dict)


@dataclass
class RunOut:
    name: str
    designs: list[DesignOut]
    traj_rows: list[dict]
    hotspots_construct: list[int]
    deleted_construct: list[tuple[int, int]]
    target_numbers: list[int]
    final_target: int
    truth: dict = field(default_factory=dict)


def _round2(x: float) -> float:
    return float(np.round(x, 2))


class Realiser:
    """Turns a docked backbone and a sequence into all-atom models, scores and table rows."""

    def __init__(self, tg: ToyTarget, scene: P.Scene, rng: np.random.Generator, T: tuple[np.ndarray, np.ndarray],
                 offset: int, hotspots_construct: list[int], present_ref: list[int], run_name: str):
        self.tg, self.sc, self.rng, self.T, self.offset = tg, scene, rng, T, offset
        self.hot_c = hotspots_construct
        self.present = present_ref
        self.run_name = run_name
        A = tg.atoms_a
        res = np.array(A.resnum)
        keep = np.isin(res, present_ref)
        self.target_ref = A.select(keep)                       # run's target in reference numbering
        self.target_con_xyz = scene.con_xyz
        self.target_con_element = np.array([e for e, n in zip(self.tg.atoms_a.element, self.tg.atoms_a.resnum) if n in set(present_ref)])
        from scipy.spatial import cKDTree as _KDT
        pol = np.isin(self.target_con_element, ("N", "O"))
        self.polar_tree = _KDT(self.target_con_xyz[pol]) if pol.any() else None      # polar target atoms, for a hydrogen-bond count
        parts = [x for x in (scene.out_xyz, scene.oth_xyz, scene.gap_xyz) if len(x)]
        from scipy.spatial import cKDTree as _KD
        self.noncon_tree = _KD(np.concatenate(parts)) if parts else None     # what a clean pose must stay away from
        self.sasa_target_alone, _ = ST.sasa_total([self.target_ref])

    # ------------------------------------------------------------------------------------ atoms
    def build_atoms(self, bundle: B.Bundle, pose: P.Pose, seq: str, extra_ok: bool = True) -> G.Atoms:
        pb = bundle.transformed(pose.R, pose.t)
        return G.pack_chain(seq, pb.N, pb.CA, pb.C, pb.psi, np.arange(1, bundle.length + 1), "B", self.rng,
                            extra_xyz=self.target_con_xyz, extra_element=self.target_con_element, attract=1.0, hbond_reward=1.5,
                            passes=1)

    def repair(self, bundle: B.Bundle, pose: P.Pose, seq: str, keep_clear: bool = False) -> tuple[str, G.Atoms]:
        """Shorten side chains that overlap the construct (BindCraft only sees the construct). With keep_clear the
        side chains that come within 4.3 A of anything outside the construct are shortened too (a clean pose must
        not touch it). The returned sequence is the one the returned atoms were built from."""
        from scipy.spatial import cKDTree

        tree = cKDTree(self.target_con_xyz)
        seq = list(seq)
        for _ in range(5):
            at = self.build_atoms(bundle, pose, "".join(seq))
            d, _ = tree.query(at.xyz)
            res = np.array(at.resnum) - 1
            rmin = np.full(len(seq), 99.0)
            np.minimum.at(rmin, res, d)
            bad = [i for i in range(len(seq)) if rmin[i] < 3.0 and seq[i] not in "AG"]
            far = []
            if keep_clear and self.noncon_tree is not None:
                dn, _ = self.noncon_tree.query(at.xyz)
                nmin = np.full(len(seq), 99.0)
                np.minimum.at(nmin, res, dn)
                far = [i for i in range(len(seq)) if nmin[i] < 4.3 and seq[i] not in "AG"]
            if not bad and not far:
                return "".join(seq), at
            for i in bad:
                seq[i] = "A" if (rmin[i] < 2.4 or seq[i] == "S") else "S"
            for i in far:
                seq[i] = "A"
        return "".join(seq), self.build_atoms(bundle, pose, "".join(seq))

    def interface_roles(self, bundle: B.Bundle, pose: P.Pose) -> set[int]:
        pb = bundle.transformed(pose.R, pose.t)
        X, _, _ = P.proxy(pb)
        tree = self.sc.trees["con"]
        d = tree.query(X.reshape(-1, 3))[0].reshape(bundle.length, 6)
        return set(np.nonzero((d[:, 4] < 4.8) | (d[:, 5] < 5.2) | (d[:, :4].min(1) < 4.4))[0].tolist())

    # ------------------------------------------------------------------------------------ scoring
    def to_construct_frame(self, xyz: np.ndarray) -> np.ndarray:
        R, t = self.T
        return xyz @ R.T + t

    def target_shift(self, rng: np.random.Generator) -> np.ndarray:
        """Displacement of every target atom in a re-predicted target: a rigid motion of 0.2 A and 0.4 degrees plus 0.02 A of noise."""
        xyz = self.target_con_xyz
        c = xyz.mean(0)
        rot = G.rotation_matrix(rng.normal(size=3), np.radians(0.4) * abs(rng.normal()))
        return (xyz - c) @ rot.T + c + rng.normal(size=3) * 0.12 - xyz + rng.normal(size=xyz.shape) * 0.02

    def jitter(self, atoms: G.Atoms, scale: float = 0.02) -> np.ndarray:
        """A second prediction of the same complex: the binder moved a little as a whole (a coherent shift keeps the
        backbone hydrogen-bond geometry that DSSP reads) plus a very small per-atom noise."""
        xyz = atoms.xyz
        r = G.rotation_matrix(self.rng.normal(size=3), np.radians(0.8) * abs(self.rng.normal()))
        c = xyz.mean(0)
        return (xyz - c) @ r.T + c + self.rng.normal(size=3) * 0.15 + self.rng.normal(size=xyz.shape) * scale

    def score(self, atoms: G.Atoms, bundle: B.Bundle, seq: str, fail: list[str], mpnn: int, accepted: bool,
              helix_flags: str, shape=None, rmsd_near: bool | None = None) -> tuple[dict, dict]:
        """Row values (per model and averages) and the two models' coordinates (reference frame). `shape` (optional) is applied to
        the atoms of each model before it is measured (a plan that fixes where a model sits, see project.RunBuilder._nudge);
        `rmsd_near` says how a Binder_RMSD miss is drawn (see apply_fail)."""
        rng = self.rng
        n = len(seq)
        letters = {i + 1: c for i, c in enumerate(seq)}
        models: dict[int, dict] = {}
        for k in (1, 2):
            xyz = atoms.xyz if k == 1 else self.jitter(atoms)
            at_k = atoms.copy(xyz=xyz)
            if shape is not None:
                at_k = shape(at_k)
            tnoise = self.target_shift(rng)        # the predicted target differs a little from the input
            iface = ST.interface_residues(at_k, self.target_con_xyz + tnoise, 4.0)
            sasa_b_alone, _ = ST.sasa_total([at_k])
            sasa_cx, _ = ST.sasa_total([self.target_ref, at_k])
            dsasa = self.sasa_target_alone + sasa_b_alone - sasa_cx
            bpol = np.isin(np.array(at_k.element), ("N", "O"))
            n_hb = int(sum(len(h) for h in self.polar_tree.query_ball_point(at_k.xyz[bpol], 3.4))) if self.polar_tree is not None else 0
            models[k] = {"atoms": at_k, "iface": iface, "dsasa": float(dsasa), "sasa_b": float(sasa_b_alone), "tnoise": tnoise, "n_hb": n_hb}
        q = float(rng.uniform(0, 1))
        vals: dict[int, dict] = {}
        base = {
            "pLDDT": 0.855 + 0.075 * q, "i_pTM": 0.56 + 0.26 * rng.uniform(), "i_pAE": 0.14 + 0.15 * rng.uniform(),
            "Surface_Hydrophobicity": 0.22 + 0.10 * rng.uniform(), "ShapeComplementarity": 0.63 + 0.12 * rng.uniform(),
            "PackStat": 0.56 + 0.10 * rng.uniform(), "n_InterfaceHbonds": int(rng.integers(3, 8)),
            "n_InterfaceUnsatHbonds": int(rng.integers(0, 4)), "Binder_pLDDT": 0.85 + 0.07 * rng.uniform(),
            "Binder_RMSD": 0.6 + 1.8 * rng.uniform(), "Hotspot_RMSD": 0.5 + 2.5 * rng.uniform(),
            "Target_RMSD": 0.3 + 0.8 * rng.uniform(), "pAE": 0.12 + 0.12 * rng.uniform(),
        }
        for k in (1, 2):
            m = models[k]
            noise = lambda s: float(rng.normal() * s)
            v: dict = {}
            v["pLDDT"] = _round2(np.clip(base["pLDDT"] + noise(0.012), 0.81, 0.97))
            v["pTM"] = _round2(np.clip(v["pLDDT"] - rng.uniform(0.04, 0.09), 0.6, 0.95))
            v["i_pTM"] = _round2(np.clip(base["i_pTM"] + noise(0.02), 0.52, 0.92))
            v["pAE"] = _round2(np.clip(base["pAE"] + noise(0.01), 0.08, 0.3))
            v["i_pAE"] = _round2(np.clip(base["i_pAE"] + noise(0.015), 0.08, 0.33))
            v["i_pLDDT"] = _round2(np.clip(v["pLDDT"] - rng.uniform(0.02, 0.08), 0.6, 0.95))
            v["ss_pLDDT"] = _round2(np.clip(v["pLDDT"] + rng.uniform(0.0, 0.03), 0.6, 0.98))
            v["Unrelaxed_Clashes"] = int(rng.integers(0, 4))
            v["Relaxed_Clashes"] = int(rng.integers(0, 2))
            v["Binder_Energy_Score"] = _round2(-(2.9 * n + rng.normal() * 8))
            v["Surface_Hydrophobicity"] = _round2(np.clip(base["Surface_Hydrophobicity"] + noise(0.008), 0.15, 0.34))
            v["ShapeComplementarity"] = _round2(np.clip(base["ShapeComplementarity"] + noise(0.012), 0.6, 0.85))
            v["PackStat"] = _round2(np.clip(base["PackStat"] + noise(0.015), 0.4, 0.8))
            ds = m["dsasa"]
            v["dSASA"] = _round2(ds)
            v["dG"] = _round2(-0.026 * ds * (1 + noise(0.06)) - 8)
            v["dG/dSASA"] = _round2(100 * v["dG"] / ds) if ds else 0.0
            v["Interface_SASA_%"] = _round2(100 * ds / max(m["sasa_b"], 1.0) * 0.5)
            ifc = m["iface"]
            v["n_InterfaceResidues"] = len(ifc)
            v["Interface_Hydrophobicity"] = _round2(100 * sum(letters[r] in ST.HYDROPHOBIC for r in ifc) / max(len(ifc), 1))
            v["n_InterfaceHbonds"] = int(max(m["n_hb"] + rng.integers(0, 3), 3))      # polar atom pairs within 3.4 A, at least BindCraft's minimum
            v["InterfaceHbondsPercentage"] = _round2(100 * v["n_InterfaceHbonds"] / max(len(ifc), 1))
            v["n_InterfaceUnsatHbonds"] = int(np.clip(base["n_InterfaceUnsatHbonds"] + rng.integers(-1, 2), 0, 4))
            v["InterfaceUnsatHbondsPercentage"] = _round2(100 * v["n_InterfaceUnsatHbonds"] / max(len(ifc), 1))
            ifs = [i for i in ifc if helix_flags[i - 1] == "H"]
            v["Interface_Helix%"] = _round2(100 * len(ifs) / max(len(ifc), 1))
            v["Interface_BetaSheet%"] = 0.0
            v["Interface_Loop%"] = _round2(100 - v["Interface_Helix%"])
            hel = sum(c == "H" for c in helix_flags)
            v["Binder_Helix%"] = _round2(100 * hel / n)
            v["Binder_BetaSheet%"] = 0.0
            v["Binder_Loop%"] = _round2(100 - v["Binder_Helix%"])
            v["InterfaceAAs"] = ST.interface_aas(letters, ifc)
            v["Hotspot_RMSD"] = _round2(np.clip(base["Hotspot_RMSD"] + noise(0.3), 0.2, 6.0))
            v["Target_RMSD"] = _round2(np.clip(base["Target_RMSD"] + noise(0.05), 0.2, 2.0))
            v["Binder_pLDDT"] = _round2(np.clip(base["Binder_pLDDT"] + noise(0.01), 0.81, 0.96))
            v["Binder_pTM"] = _round2(np.clip(v["Binder_pLDDT"] - rng.uniform(0.08, 0.2), 0.5, 0.9))
            v["Binder_pAE"] = _round2(np.clip(0.28 - 0.2 * (v["Binder_pLDDT"] - 0.8) + noise(0.01), 0.08, 0.3))
            v["Binder_RMSD"] = _round2(np.clip(base["Binder_RMSD"] + noise(0.15), 0.3, 3.3))
            vals[k] = v
        apply_fail(vals, {k: len(models[k]["iface"]) for k in (1, 2)}, fail, rng, rmsd_near)
        return vals, models

    def interface_k_override(self, seq: str, iface: list[int], n: int = 5) -> str:
        """Put Lys at five interface positions (a sequence that fails the K cap)."""
        s = list(seq)
        for r in iface[:n]:
            s[r - 1] = "K"
        return "".join(s)


#: The contract's near-miss rule (binder_review.model): one stem missed by at most this share of its threshold.
NEAR_MISS_MAX_REL = 0.25
RMSD_FAR = 3.5 * (1 + NEAR_MISS_MAX_REL) + 0.1      # a Binder_RMSD that is certainly not a near miss


def apply_fail(vals: dict[int, dict], n_iface: dict[int, int], fail: list[str], rng: np.random.Generator,
               rmsd_near: bool | None = None) -> None:
    """Push the named stems past their default thresholds (in place).

    Binder_RMSD (and, at its tail, Surface_Hydrophobicity) is a stem whose miss could land on either side of the near-miss line
    by luck, so a rejected design of a project that keeps a contract says which side it is on: rmsd_near True draws both models
    of Binder_RMSD within a quarter of the 3.5 A threshold, False makes sure that at least one model is well beyond it (the
    draws are the same either way, only a value that would not do is replaced). None draws the original 3.7 to 5.3 A and leaves
    every value alone (accepted designs, designs without a structure, which are never rescued, and projects without a contract).
    Surface_Hydrophobicity is capped inside the near-miss line whenever rmsd_near is not None."""
    for stem in fail:
        for k in (1, 2):
            v = vals[k]
            n = max(n_iface[k], 1)
            if stem == "ShapeComplementarity":
                v[stem] = _round2(0.50 + 0.07 * rng.uniform())
            elif stem == "n_InterfaceHbonds":
                v[stem] = int(rng.integers(0, 3))
                v["InterfaceHbondsPercentage"] = _round2(100 * v[stem] / n)
            elif stem == "n_InterfaceUnsatHbonds":
                v[stem] = int(rng.integers(6, 10))
                v["InterfaceUnsatHbondsPercentage"] = _round2(100 * v[stem] / n)
            elif stem == "Surface_Hydrophobicity":
                v[stem] = _round2(0.37 + 0.07 * rng.uniform())
                if rmsd_near is not None:
                    v[stem] = min(v[stem], 0.43)                 # 0.43 is 23 % over the threshold: still a near miss
            elif stem == "Binder_pLDDT":
                v[stem] = _round2(0.70 + 0.08 * rng.uniform())
            elif stem == "Binder_RMSD":
                v[stem] = _round2(3.7 + (0.6 if rmsd_near else 1.6) * rng.uniform())
    if rmsd_near is False and "Binder_RMSD" in fail and max(vals[k]["Binder_RMSD"] for k in vals) < RMSD_FAR:
        vals[max(vals, key=lambda k: vals[k]["Binder_RMSD"])]["Binder_RMSD"] = _round2(RMSD_FAR)


def clone_vals(template: dict[int, dict], rng: np.random.Generator) -> dict[int, dict]:
    """A plausible metric set for a design without a structure: a perturbed copy of another design's values."""
    out: dict[int, dict] = {}
    f = {s: float(1 + rng.normal() * 0.02) for s in STEMS if s != "InterfaceAAs"}
    for k, v in template.items():
        c = {}
        for stem, x in v.items():
            if stem == "InterfaceAAs":
                c[stem] = dict(x)
            elif isinstance(x, int):
                c[stem] = x
            else:
                y = x * f[stem]
                if stem in ("pLDDT", "pTM", "i_pTM", "i_pLDDT", "ss_pLDDT", "Binder_pLDDT", "Binder_pTM"):
                    y = min(y, 0.97)
                c[stem] = _round2(y)
        out[k] = c
    return out


#: BindCraft's default filter thresholds as the generator knows them (the tests read the package's own copy)
DEFAULT_THRESHOLDS = {  # stem: (threshold, higher is better, per-model threshold)
    "pLDDT": (0.80, True, 0.80), "pTM": (0.55, True, 0.55), "i_pTM": (0.50, True, 0.50), "i_pAE": (0.35, False, 0.35),
    "ShapeComplementarity": (0.60, True, 0.55), "n_InterfaceHbonds": (3, True, 3), "n_InterfaceUnsatHbonds": (4, False, 4),
    "Surface_Hydrophobicity": (0.35, False, 0.35), "Binder_pLDDT": (0.80, True, 0.80), "Binder_RMSD": (3.5, False, 3.5),
}


def violations(vals: dict[int, dict]) -> set[str]:
    """Stems that miss a default threshold on the average or on a model (what BindCraft's filters would reject)."""
    out = set()
    avg = average_row(vals)
    for stem, (thr, higher, per_model) in DEFAULT_THRESHOLDS.items():
        for v, t in [(avg[stem], thr)] + [(vals[k][stem], per_model) for k in vals]:
            if (v < t - 1e-9) if higher else (v > t + 1e-9):
                out.add(stem)
    for letter in "KM":
        if average_row(vals)["InterfaceAAs"][letter] > 3:
            out.add(f"InterfaceAAs_{letter}")
    return out


def helix_flags(bundle: B.Bundle) -> str:
    """H for helix residues except the first and the last of every helix (they read as loop)."""
    flags = list(bundle.ss)
    for a, z in bundle.helices:
        for i in range(HELIX_END_LOOP):
            flags[a + i] = "L"
            flags[z - i] = "L"
    return "".join(flags)


def average_row(vals: dict[int, dict]) -> dict:
    avg = {}
    for stem in STEMS:
        if stem == "InterfaceAAs":
            keys = vals[1][stem].keys()
            avg[stem] = {a: (vals[1][stem][a] + vals[2][stem][a]) / 2.0 for a in keys}
        else:
            avg[stem] = _round2((vals[1][stem] + vals[2][stem]) / 2.0)
    return avg


def design_row(did: str, protocol: str, length: int, seed: int, hotspot: str, seq: str, iface2: list[int], mpnn_score: float,
               recovery: float, vals: dict[int, dict], design_time: float, notes: str, settings_name: str,
               rank: int | None = None) -> dict:
    avg = average_row(vals)
    row: dict = {}
    if rank is not None:
        row["Rank"] = rank
    row.update({"Design": did, "Protocol": protocol, "Length": length, "Seed": seed, "Helicity": -0.3,
                "Target_Hotspot": hotspot, "Sequence": seq,
                "InterfaceResidues": ",".join(f"B{r}" for r in iface2), "MPNN_score": _round2(mpnn_score),
                "MPNN_seq_recovery": _round2(recovery)})
    for stem in STEMS:
        row[f"Average_{stem}"] = _dict_text(avg[stem]) if stem == "InterfaceAAs" else avg[stem]
        for k in (1, 2, 3, 4, 5):
            if k in vals:
                row[f"{k}_{stem}"] = _dict_text(vals[k][stem]) if stem == "InterfaceAAs" else vals[k][stem]
            else:
                row[f"{k}_{stem}"] = np.nan
    row.update({"DesignTime": duration(design_time), "Notes": notes, "TargetSettings": settings_name,
                "Filters": "default_filters", "AdvancedSettings": "default_4stage_multimer"})
    return row


def _dict_text(d: dict) -> str:
    return "{" + ", ".join(f"'{k}': {v}" for k, v in d.items()) + "}"


def trajectory_row(name: str, length: int, seed: int, hotspot: str, seq: str, iface: list[int], vals: dict, t_seconds: float,
                   settings_name: str) -> dict:
    row = {"Design": name, "Protocol": "4stage", "Length": length, "Seed": seed, "Helicity": -0.3, "Target_Hotspot": hotspot,
           "Sequence": seq, "InterfaceResidues": ",".join(f"B{r}" for r in iface)}
    for stem in TRAJ_STEMS:
        row[stem] = _dict_text(vals[stem]) if stem == "InterfaceAAs" else vals[stem]
    row.update({"TrajectoryTime": duration(t_seconds), "Notes": "", "TargetSettings": settings_name,
                "Filters": "default_filters", "AdvancedSettings": "default_4stage_multimer"})
    return row
