"""Build the whole toy project: target, construct, two BindCraft 1 runs, two BindCraft 2 campaigns, reference data.

`make_project(out_dir)` is deterministic in `seed`. It only writes below `out_dir`. The numbers are invented; the
coordinates are generated here (see target.py and binder.py), nothing is copied from anywhere.
"""
from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import binder as B
from . import geometry as G
from . import poses as P
from . import runs as RN
from . import selfcheck as SC
from . import structures as ST
from .plans import CONSTRUCT_DELETED_REF, RUN1, RUN2, Spec
from .scene import choose_hotspots, make_scene, patch_point, surface_of
from .target import CONSTRUCT_FIRST, CONSTRUCT_LAST, build_target

DEFAULT_SEED = 8
MAX_VARIANT_REDRAWS = 8              # a variant that misses its plan gets another sequence at most this many times
MAX_REDRAWS = 6                       # a backbone whose variants miss the plan is drawn again at most this many times
TERMINUS_NEAR_MAX = 10                # a terminus design has at most this many target residues near it (the whole-target fallback starts below 12)
OFFSET = CONSTRUCT_FIRST - 1          # construct number + OFFSET = reference number
TOOL_PATH = "/work/BindCraft_v1_5"    # a made-up path recorded in the toy settings files


def _unit(v):
    return v / np.linalg.norm(v)


def _candidates(kind: str, sc: P.Scene, surf: list[dict], hot_pt: np.ndarray, hot_nrm: np.ndarray,
                rng: np.random.Generator, n: int = 10, site: str | None = None) -> list[tuple[np.ndarray, np.ndarray]]:
    if site == "terminus":
        return _terminus_candidates(kind, sc, surf, hot_pt, rng, n)
    if kind == "clean_hot":
        return [(hot_pt + rng.normal(size=3) * 1.0, hot_nrm) for _ in range(n)]
    if kind == "clean_away":
        far = [s for s in surf if np.linalg.norm(s["cb"] - hot_pt) > 16 and s["free"] >= 11]
        far.sort(key=lambda s: -s["groove"])
        pick = far[: max(2 * n, 10)]
        order = rng.permutation(len(pick))
        return [(pick[i]["cb"], pick[i]["normal"]) for i in order[:n]]
    name = {"collide_out": "out", "touch_other": "oth", "collide_gap": "gap"}[kind]
    tree = sc.trees[name]
    lo, hi = (10.0, 15.0) if kind == "touch_other" else (5.0, 10.0)
    cands = []
    for s in surf:
        d, _ = tree.query(s["cb"] + 11.0 * s["normal"])        # where the body of a docked binder would sit
        if lo <= d <= hi:
            cands.append((abs(d - 0.5 * (lo + hi)), s["cb"], s["normal"]))
    cands.sort(key=lambda c: c[0])
    return [(c[1], c[2]) for c in cands[:n]]


def _terminus_candidates(kind: str, sc: P.Scene, surf: list[dict], hot_pt: np.ndarray, rng: np.random.Generator,
                         n: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Anchors at an end of the construct (its first and last residues that have free space above them), pushed out
    sideways by 0, 6 or 12 A away from the construct: a binder docked there rests on the end of the construct and
    hangs over its edge, so that only a few residues of the construct lie near it."""
    centre = sc.con_ca.mean(0)
    lo, hi = CONSTRUCT_FIRST + 6, CONSTRUCT_LAST - 6
    ends = [s for s in surf if (s["res"] <= lo or s["res"] >= hi) and s["free"] >= 11 and
            (kind != "clean_away" or np.linalg.norm(s["cb"] - hot_pt) > 16)]
    anchors = []
    for s in ends:
        side = s["cb"] - centre
        side = _unit(side - (side @ s["normal"]) * s["normal"])
        anchors += [(s["cb"] + shift * side, s["normal"]) for shift in (0.0, 6.0, 12.0)]
    order = rng.permutation(len(anchors))
    return [anchors[i] for i in order[:n]]


def _screen(kind: str, info: dict, n_hot: int = 3, margin: float = 6.0, strict: bool = True, tight: bool = False,
            n_min: int = 6) -> bool:
    """Cheap test on the proxy docking result before the all-atom model is built. `tight` (a pose that is drawn again after a
    variant of it missed its goal) asks of a clean_hot pose that two hotspots have a backbone or CB point well inside the cut."""
    if info["n_con"] < n_min or info["min_con"] < 3.0:
        return False
    if kind in ("clean_hot", "clean_away"):
        if min(info["min_out"], info["min_oth"], info["min_gap"]) < margin:
            return False
        if kind == "clean_hot":
            if tight and info["hot_tight"] < P.HOT_MIN:
                return False
            return info["hot_touched"] >= (max(P.HOT_MIN, int(np.ceil(0.6 * n_hot))) if strict else P.HOT_MIN if tight else 1)
        return info["min_hot"] >= 9.0
    if kind == "collide_out":
        return info["min_out"] <= 3.2 and min(info["min_oth"], info["min_gap"]) >= 4.5
    if kind == "collide_gap":
        return info["min_gap"] <= 3.2 and min(info["min_oth"], info["min_out"]) >= 4.5
    return 2.9 <= info["min_oth"] <= 4.5 and min(info["min_out"], info["min_gap"]) >= 3.2


def _kind_ok(kind: str, cls: dict, sc: P.Scene, n_con_min: int = 5, n_if_min: int = 5, strict: bool = True, tight: bool = False,
             n_near_max: int | None = None) -> bool:
    """The goal of a docked backbone on its all-atom model. `tight` asks of a clean_hot model that its hotspots are inside the cut
    with the margin the contract holds them to; `n_near_max` caps the target residues that lie near the binder (a terminus site)."""
    if cls["min_con"] < 2.9 or cls["n_con"] < n_con_min or cls.get("n_if", 99) < n_if_min:
        return False
    if n_near_max is not None and cls.get("n_near", 0) > n_near_max:
        return False
    clean = cls["n_out"] == 0 and cls["n_oth"] == 0 and cls["n_gap"] == 0
    hot = cls.get("hot_touched", [])
    if kind == "clean_hot":
        need = max(P.HOT_MIN, int(np.ceil(0.6 * len(sc.hot_res)))) if strict else P.HOT_MIN if tight else 1
        return clean and len(hot) >= need and (not tight or len(cls.get("hot_safe", [])) >= P.HOT_MIN)
    if kind == "clean_away":
        return clean and len(hot) == 0 and cls.get("min_hot", 99.0) >= 8.0
    if kind == "collide_out":
        return cls["clash_out"] >= 3 and cls["n_oth"] == 0 and cls["n_gap"] == 0
    if kind == "touch_other":
        return cls["n_oth"] >= 1 and cls["min_oth"] >= 2.2 and cls["clash_out"] == 0 and cls["clash_gap"] == 0
    if kind == "collide_gap":
        return cls["clash_gap"] >= 3 and cls["n_out"] == 0 and cls["n_oth"] == 0
    raise ValueError(kind)


def _superpose_pose(child: B.Bundle, parent: B.Bundle, pose: P.Pose) -> P.Pose:
    R, t = G.kabsch(child.CA, parent.CA)               # child -> parent frame
    return P.Pose(pose.R @ R, t @ pose.R.T + pose.t)


def _fake_ss(length: int, n_helices: int, rng: np.random.Generator) -> str:
    h, l = B.plan_bundle(length, n_helices, rng)
    s = "L"
    for i, x in enumerate(h):
        s += "H" * x + ("L" * l[i] if i < len(l) else "")
    return s + "L"


def _roles_from_ss(ss: str) -> list[str]:
    out = []
    for i, c in enumerate(ss):
        out.append("loop" if c == "L" else ("core" if i % 7 in (0, 3, 4) else "surf"))
    return out


def _apply_quirk(seq: str, roles: list[str], quirk: str, rng: np.random.Generator) -> str:
    s = list(seq)
    loops = [i for i, r in enumerate(roles) if r == "loop" and 2 < i < len(s) - 5]
    surf = [i for i, r in enumerate(roles) if r == "surf"]
    if quirk == "sequon" and loops:
        i = loops[len(loops) // 2]
        s[i:i + 3] = list("NAT")
    elif quirk == "odd_cys" and surf:
        s[surf[len(surf) // 2]] = "C"
    elif quirk == "ng_dg" and len(loops) >= 2:
        s[loops[0]:loops[0] + 2] = list("NG")
        s[loops[-1]:loops[-1] + 2] = list("DG")
    return "".join(s)


def _bfactors(atoms: G.Atoms, plddt: float, bundle_ss: str, rng: np.random.Generator) -> np.ndarray:
    """Per-atom B-factor: per-residue pLDDT (0 to 100) whose atom-weighted mean equals plddt * 100."""
    n = len(bundle_ss)
    prof = 100 * plddt + rng.normal(size=n) * 2.5
    for i, c in enumerate(bundle_ss):
        if c == "L":
            prof[i] -= 4.0
    prof[0] -= 6.0
    prof[-1] -= 6.0
    res = np.array(atoms.resnum) - 1
    per_atom = np.clip(prof, 35.0, 99.5)[res]
    for _ in range(8):                                        # shift and clip until the mean sits on the target
        per_atom = np.clip(per_atom + (100 * plddt - per_atom.mean()), 35.0, 99.5)
    return per_atom


class _Redraw(Exception):
    """A backbone one of whose variants cannot keep the plan: draw the backbone again."""


class RunBuilder:
    def __init__(self, name: str, specs: list[Spec], tg, sc: P.Scene, surf: list[dict], hot_ref: list[int],
                 T, rng: np.random.Generator, deleted: set[int], settings_name: str, seeds: list[int], contract: bool = True):
        self.contract = contract              # hold every variant to its plan (see _design_problem); the schema examples are not
        self.name, self.specs, self.tg, self.sc, self.surf = name, specs, tg, sc, surf
        self.hot_ref, self.T, self.rng = hot_ref, T, rng
        self.deleted = deleted
        self.settings_name = settings_name
        self.seeds = seeds
        self.present = [r for r in range(CONSTRUCT_FIRST, CONSTRUCT_LAST + 1) if r not in deleted]
        self.hot_c = [r - OFFSET for r in hot_ref]
        self.hot_str = ",".join(str(x) for x in self.hot_c)
        self.R = RN.Realiser(tg, sc, rng, T, OFFSET, self.hot_c, self.present, name)
        self.hot_pt, self.hot_nrm = patch_point(surf, hot_ref)
        self.log = lambda *a: None
        self.bundles: dict[str, B.Bundle] = {}
        self.poses: dict[str, P.Pose] = {}
        self.anchor: dict[str, tuple] = {}
        self._oth_tree = cKDTree(sc.oth_xyz)

    # ------------------------------------------------------------------------------------ docking
    def dock(self, spec: Spec, tight: bool = False) -> tuple[B.Bundle, P.Pose, dict]:
        """Dock one backbone against the goal of its spec. `tight` (a backbone that is drawn again because a variant of the
        first one missed its goal) asks of a clean_hot pose that two hotspots sit well inside the cut: a backbone or CB point
        within HOT_TIGHT of each, so that the contact survives whatever side chain a variant puts there."""
        rng, sc = self.rng, self.sc
        terminus = spec.site == "terminus"
        near_max = TERMINUS_NEAR_MAX if terminus else None
        if spec.family_of:
            parent = self.bundles[spec.family_of]
            point, normal = self.anchor[spec.family_of]
            for attempt in range(8):                              # a new neighbour of the parent, with less torsion noise each time
                shrink = 0.75 ** attempt
                bundle = B.perturb_bundle(parent, rng, sigma_helix=1.2 * shrink, sigma_loop=4.0 * shrink)
                start = _superpose_pose(bundle, parent, self.poses[spec.family_of])
                dk = P.Docker(bundle, sc, spec.kind, rng, count_range=(12, 40))
                for _ in range(5):
                    pose = dk.refine(start, point, normal)
                    cls = self._classify(bundle, pose, spec.kind)
                    if _kind_ok(spec.kind, cls, sc, 5, 4 if attempt < 4 else 3, attempt < 5, tight):
                        self.anchor[spec.name] = (point, normal)
                        return bundle, pose, cls
                    start = P.Pose(G.rotation_matrix(rng.normal(size=3), np.radians(2.0)) @ start.R, start.t + rng.normal(size=3) * 0.4)
            raise RuntimeError(f"{self.name}/{spec.name}: sibling backbone could not be docked like its parent")
        last = None
        for attempt in range(9):
            strict = attempt < 5                                  # later attempts accept a weaker version of the goal
            bundle = B.build_bundle(spec.length, spec.helices, rng, k=6000)
            dk = P.Docker(bundle, sc, spec.kind, rng, count_range=(5, 14) if terminus else (12, 40))
            scouts = []                                      # quick proxy dockings of several surface patches
            for point, normal in _candidates(spec.kind, sc, self.surf, self.hot_pt, self.hot_nrm, rng, 8, spec.site):
                pose = dk.dock(point, normal, n_starts=20, n_iter=15)
                if _screen(spec.kind, pose.info, len(self.hot_ref), 6.0 if attempt < 3 else 5.0, strict, tight, 5 if terminus else 6):
                    scouts.append((-pose.info["n_con"], len(scouts), point, normal, pose))
            scouts.sort(key=lambda s: s[:2])
            for _, _, point, normal, pose in scouts[:3]:     # the best three get an all-atom check
                pose = dk.refine(pose, point, normal, n_iter=40, step_r=2.0, step_t=0.5)
                cls = self._classify(bundle, pose, spec.kind)
                last = cls
                if _kind_ok(spec.kind, cls, sc, 5, 5 if attempt < 3 else 4 if attempt < 6 else 3, strict, tight, near_max):
                    self.anchor[spec.name] = (point, normal)
                    return bundle, pose, cls
        raise RuntimeError(f"{self.name}/{spec.name} ({spec.kind}): no pose met the goal; last {last}")

    def _nudge(self, atoms: G.Atoms, window: tuple[float, float]) -> G.Atoms:
        """The model moved rigidly along the line between its closest atom and chain B until that distance lies inside `window`
        (a few tenths of an angstrom: each model of a design is its own prediction, and a plan can say where it sits)."""
        lo, hi = window
        goal = 0.5 * (lo + hi)
        xyz = atoms.xyz
        for _ in range(30):
            d, j = self._oth_tree.query(xyz)
            i = int(d.argmin())
            if lo <= d[i] <= hi:
                break
            xyz = xyz + (self.sc.oth_xyz[j[i]] - xyz[i]) / d[i] * (d[i] - goal) * 0.8
        return atoms.copy(xyz=xyz)

    def _realise(self, bundle: B.Bundle, pose: P.Pose, seq: str, fail: list[str], mp: int, accepted: bool, flags: str,
                 clean: bool = False, gap: tuple[float, float] | None = None, rmsd_near: bool | None = None):
        """Atoms, scores and the final sequence of one design, adjusted until the interface Lys and Met counts agree
        with the plan (at most three on the average for an accepted design, more than three for the one that fails).
        `gap`: the window for the closest approach of each model to chain B (Spec.partner_gap)."""
        R = self.R
        want_k = "InterfaceAAs_K" in fail
        stems_fail = [s for s in fail if s != "InterfaceAAs_K"]
        shape = None if gap is None else (lambda at: self._nudge(at, gap))
        for attempt in range(4):
            if want_k and attempt > 0:
                atoms = R.build_atoms(bundle, pose, seq)             # no repair: the Lys stay
            else:
                seq, atoms = R.repair(bundle, pose, seq, clean)
            vals, models = R.score(atoms, bundle, seq, stems_fail, mp, accepted, flags, shape, rmsd_near)
            avg = RN.average_row(vals)["InterfaceAAs"]
            ok = (avg["K"] >= 4 if self.contract else avg["K"] > 3) if want_k else (avg["K"] <= 3 and avg["M"] <= 3)   # 4 or more: a miss by over a quarter, never a near miss
            if ok:
                return seq, atoms, vals, models
            cand = sorted(set(models[1]["iface"]) | set(models[2]["iface"]))
            s = list(seq)
            if want_k:
                for r in cand[: 5 + 2 * attempt]:
                    s[r - 1] = "K"
            else:
                for r in cand:
                    s[r - 1] = {"K": "R", "M": "L"}.get(s[r - 1], s[r - 1])
            seq = "".join(s)
        raise RuntimeError(f"{self.name}: the interface Lys and Met counts could not be set for mpnn{mp}")

    def _classify(self, bundle: B.Bundle, pose: P.Pose, kind: str = "") -> dict:
        roles = B.roles(bundle, self.R.interface_roles(bundle, pose))
        seq = B.design_sequence(roles, self.rng)
        seq, atoms = self.R.repair(bundle, pose, seq, kind in ("clean_hot", "clean_away"))
        cls = P.classify(atoms.xyz, np.array(atoms.resnum), self.sc)
        cls["n_if"] = len(ST.interface_residues(atoms, self.sc.con_xyz, 4.0))
        return cls

    # ------------------------------------------------------------------------------------ one backbone
    def _backbone(self, spec: Spec, si: int, template: dict | None, tight: bool):
        """Dock one backbone and write all its variants: (designs, trajectory row, truth record, template, geometry)."""
        rng, R = self.rng, self.R
        designs: list[RN.DesignOut] = []
        seed = self.seeds[si]
        bb_id = f"binder_l{spec.length}_s{seed}"
        bundle = pose = None
        cls = {}
        if spec.kind is not None and spec.structure:
            bundle, pose, cls = self.dock(spec, tight)
            self.bundles[spec.name], self.poses[spec.name] = bundle, pose
            roles = B.roles(bundle, R.interface_roles(bundle, pose))
            ss = bundle.ss
            flags = RN.helix_flags(bundle)
        else:
            ss = _fake_ss(spec.length, spec.helices, rng)
            roles = _roles_from_ss(ss)
            flags = ss
        start_seq = B.design_sequence(roles, rng)
        first_vals = None
        base_iface: list[int] = []
        for mp in sorted(set(spec.accepted) | set(spec.rejected)):
            accepted = mp in spec.accepted
            fail = [] if accepted else list(spec.rejected[mp])
            did = f"{bb_id}_mpnn{mp}"
            if pose is not None:
                seq, vals, models, plain_clash = self._variant(spec, si, did, bundle, pose, roles, start_seq, mp, accepted, fail, flags)
                k = 1 if vals[1]["pLDDT"] >= vals[2]["pLDDT"] else 2
                iface2 = models[2]["iface"]
                base_iface = base_iface or models[1]["iface"]
                template = template or vals
            else:
                recovery = float(rng.uniform(0.35, 0.6))
                seq = B.design_sequence(roles, rng, base=start_seq, recovery=recovery)
                if mp in spec.quirks:
                    seq = _apply_quirk(seq, roles, spec.quirks[mp], rng)
                vals = RN.clone_vals(template, rng)
                RN.apply_fail(vals, {1: 18, 2: 18}, fail, rng)
                models = {}
                k = 1 if vals[1]["pLDDT"] >= vals[2]["pLDDT"] else 2
                iface2 = sorted(int(x) for x in rng.choice(np.nonzero(np.array(list(ss)) == "H")[0] + 1, size=18, replace=False))
                for kk in (1, 2):
                    vals[kk]["n_InterfaceResidues"] = len(iface2)
                plain_clash = vals[1]["Unrelaxed_Clashes"] > 2
            miss = RN.violations(vals)
            if accepted and miss:
                raise RuntimeError(f"{did}: accepted design misses default filters {sorted(miss)}")
            if not accepted and not (set(fail) <= miss):
                raise RuntimeError(f"{did}: rejected design does not miss {sorted(set(fail) - miss)}")
            same = sum(a == b for a, b in zip(seq, start_seq)) / len(seq)
            mpnn_score, design_time = float(rng.uniform(0.9, 1.3)), float(rng.uniform(100, 300))
            roll = rng.random() if plain_clash else None             # the draw the first sequence of this variant would have made
            note = "Relaxed structure contains clashes." if vals[1]["Unrelaxed_Clashes"] > 2 and roll is not None and roll < 0.3 else ""
            row = RN.design_row(did, "4stage", spec.length, seed, self.hot_str, seq, iface2, mpnn_score, same, vals, design_time, note,
                                self.settings_name)
            designs.append(RN.DesignOut(did, "accepted" if accepted else "rejected", spec.name, mp, seq, row, models, k,
                                        truth={"vals": vals, "fail": fail, "spec": spec.name, "kind": spec.kind}))
            first_vals = first_vals or vals
        truth = {"id": bb_id, "kind": spec.kind, "length": spec.length, "family_of": spec.family_of,
                 "accepted": spec.accepted, "rejected": sorted(spec.rejected),
                 "geometry": _plain({k: v for k, v in cls.items() if k not in ("hot_safe", "n_near")}),
                 "structure": bool(pose is not None)}
        # trajectory row: the backbone before sequence design
        tv = {s: (dict(first_vals[1][s]) if s == "InterfaceAAs" else first_vals[1][s]) for s in RN.STEMS} if first_vals else None
        if tv is None:
            src = template[1]
            tv = {s: (dict(src[s]) if s == "InterfaceAAs" else src[s]) for s in RN.STEMS}
            for s in ("pLDDT", "pTM", "i_pTM", "i_pLDDT", "ss_pLDDT"):
                tv[s] = RN._round2(tv[s] * float(rng.uniform(0.93, 1.0)))
            ti = sorted(int(x) for x in rng.choice(np.nonzero(np.array(list(ss)) == "H")[0] + 1, size=16, replace=False))
        else:
            ti = base_iface
        for s in ("pLDDT", "pTM"):
            tv[s] = RN._round2(min(tv[s], 0.95))
        traj_row = RN.trajectory_row(bb_id, spec.length, seed, self.hot_str, start_seq, ti, tv,
                                     float(rng.uniform(300, 900)), self.settings_name)
        return designs, traj_row, truth, template, cls

    def _variant(self, spec: Spec, si: int, did: str, bundle: B.Bundle, pose: P.Pose, roles: list[str], start_seq: str, mp: int,
                 accepted: bool, fail: list[str], flags: str):
        """One ProteinMPNN variant of a docked backbone, all atoms: (sequence, scores, models, whether the first sequence drew a note).

        The docking search works on a coarse proxy of the binder and the goal was checked on all atoms for one design only, so
        every variant is measured against what the plan promises (_design_problem), on both models and with the re-predicted
        target the model is written with. A variant that misses it gets another sequence, drawn from a generator of its own (so that
        the draws of every later backbone stay what they were); a backbone none of whose sequences keep the plan is drawn again."""
        plain = None
        why = None
        for retry in range(MAX_VARIANT_REDRAWS + 1):
            if retry == 0:
                got = self._sequence_and_atoms(spec, bundle, pose, roles, start_seq, mp, accepted, fail, flags)
            else:
                with self._own_generator(si, mp, retry):
                    got = self._sequence_and_atoms(spec, bundle, pose, roles, start_seq, mp, accepted, fail, flags)
            plain = plain if plain is not None else got[1][1]["Unrelaxed_Clashes"] > 2
            why = self._design_problem(spec, did, mp, got[2], 1 if got[1][1]["pLDDT"] >= got[1][2]["pLDDT"] else 2)
            if why is None:
                return got[0], got[1], got[2], plain
            self.log(f"    {did}: another sequence ({why})")
        raise _Redraw(why)

    def _sequence_and_atoms(self, spec, bundle, pose, roles, start_seq, mp, accepted, fail, flags):
        rng = self.rng
        recovery = float(rng.uniform(0.35, 0.6))
        seq = B.design_sequence(roles, rng, base=start_seq, recovery=recovery)
        if mp in spec.quirks:
            seq = _apply_quirk(seq, roles, spec.quirks[mp], rng)
        seq, atoms, vals, models = self._realise(bundle, pose, seq, fail, mp, accepted, flags, spec.kind in ("clean_hot", "clean_away"),
                                                 spec.partner_gap.get(mp), None if accepted or not self.contract else mp in spec.near_miss)
        return seq, vals, models

    def _own_generator(self, si: int, mp: int, retry: int):
        """Context: the builder and its realiser draw from a generator keyed by (run, backbone, variant, retry) and the run's own
        generator is left exactly where it was."""
        import contextlib
        import zlib

        @contextlib.contextmanager
        def swap():
            main = self.rng
            own = np.random.default_rng([zlib.crc32(self.name.encode()), si, mp, retry])
            self.rng = self.R.rng = own
            try:
                yield
            finally:
                self.rng = self.R.rng = main

        return swap()

    def _design_problem(self, spec: Spec, did: str, mp: int, models: dict, saved: int) -> str | None:
        """Why a variant misses what the plan promises, or None. Only the model that is written to disk (`saved`) is an artefact
        that a test can see, so only that one is held to the plan."""
        if not self.contract:
            return None
        tgt = self.R.target_ref
        res, is_ca = np.array(tgt.resnum), np.array(tgt.name) == "CA"
        gap = spec.partner_gap.get(mp)
        for k, m in ((saved, models[saved]),):
            bx = m["atoms"].xyz
            tx = tgt.xyz + m["tnoise"]
            if spec.kind == "clean_hot":
                hot = SC.hotspots_touched(res, tx, bx, self.hot_ref, P.HOT_CUT - P.HOT_MARGIN)
                if len(hot) < P.HOT_MIN:
                    return f"model {k} touches hotspots {hot} within {P.HOT_CUT - P.HOT_MARGIN} A, needs {P.HOT_MIN}"
            if spec.site == "terminus":
                n_near = SC.near_counts(tx[is_ca], bx)[0]
                if n_near > TERMINUS_NEAR_MAX:
                    return f"model {k} has {n_near} target residues near it, a terminus design has at most {TERMINUS_NEAR_MAX}"
            if gap is not None:
                dmin = float(self._oth_tree.query(bx)[0].min())
                if not gap[0] - 1e-6 <= dmin <= gap[1] + 1e-6:
                    return f"model {k} lies {dmin:.2f} A from chain B, the plan says {gap}"
                cls = P.classify(bx, np.array(m["atoms"].resnum), self.sc)
                if cls["min_con"] < 2.8 or cls["clash_out"] or cls["clash_gap"]:
                    return f"model {k} was moved into the construct or the rest of the chain (min_con {cls['min_con']:.2f})"
        return None

    # ------------------------------------------------------------------------------------ one run
    def build(self) -> RN.RunOut:
        designs: list[RN.DesignOut] = []
        traj_rows: list[dict] = []
        template = None
        truth_bb: dict[str, dict] = {}
        import time

        for si, spec in enumerate(self.specs):
            t0 = time.time()
            reasons: list[str] = []
            for attempt in range(MAX_REDRAWS):
                # the first draw is the plain one; a backbone that is drawn again asks more of the pose (see dock)
                try:
                    got = self._backbone(spec, si, template, tight=attempt > 0)
                    break
                except _Redraw as e:
                    reasons.append(str(e))
                    self.log(f"  {self.name}/{spec.name}: drawn again ({e})")
            else:
                raise RuntimeError(f"{self.name}/{spec.name}: no draw of this backbone kept the plan: " + "; ".join(reasons))
            new_designs, traj_row, truth, template, cls = got
            designs.extend(new_designs)
            truth_bb[spec.name] = truth
            traj_rows.append(traj_row)
            self.log(f"  {self.name}/{spec.name} {spec.kind} {time.time() - t0:.1f}s {cls.get('n_if', '')}")
        # ranks: by Average_i_pTM, then by name
        acc = sorted((d for d in designs if d.status == "accepted"), key=lambda d: (-d.row["Average_i_pTM"], d.id))
        for i, d in enumerate(acc, start=1):
            d.rank = i
        n_acc = len(acc)
        final_target = n_acc
        return RN.RunOut(self.name, designs, traj_rows, list(self.hot_c), [], [r - OFFSET for r in self.present], final_target,
                         truth={"backbones": truth_bb})


def _plain(o):
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return o


# ----------------------------------------------------------------------------------------------------- writing
def write_run(out: Path, run: RN.RunOut, builder: RunBuilder, folder: str, with_rejected: bool, accepted_dirname: str,
              rejected_dirname: str, settings: dict | None, rng: np.random.Generator, with_xlsx: bool) -> dict:
    root = out / folder
    acc_dir = root / accepted_dirname
    rank_dir = acc_dir / "Ranked"
    rank_dir.mkdir(parents=True, exist_ok=True)
    if with_rejected:
        (root / rejected_dirname).mkdir(parents=True, exist_ok=True)
    R = builder.R
    files: dict[str, str] = {}
    for d in run.designs:
        if not d.models:
            continue
        if d.status == "rejected" and not with_rejected:
            continue
        k = d.saved_model
        at = d.models[k]["atoms"]
        plddt = d.truth["vals"][k]["pLDDT"]
        spec_ss = next(s for s in builder.specs if s.name == d.spec)
        bundle = builder.bundles[d.spec]
        bf = _bfactors(at, plddt, bundle.ss, rng)
        binder = at.copy(xyz=R.to_construct_frame(at.xyz), bfactor=bf)
        tgt = R.target_ref
        tgt_xyz = R.to_construct_frame(tgt.xyz + d.models[k]["tnoise"])
        tgt = tgt.copy(xyz=tgt_xyz, chain="A", resnum=[n - OFFSET for n in tgt.resnum],
                       bfactor=np.clip(90 + rng.normal(size=len(tgt)) * 2.0, 60, 98))
        name = f"{d.id}_model{k}.pdb"
        target_dir = acc_dir if d.status == "accepted" else root / rejected_dirname
        ST.write_pdb(target_dir / name, [tgt, binder], d.id)
        files[d.id] = str(target_dir.relative_to(out) / name)
        d.truth["file"] = files[d.id]
        d.truth["binder_construct_xyz"] = binder.xyz
        if d.status == "accepted":
            shutil.copy(target_dir / name, rank_dir / f"{d.rank}_{name}")
    acc = sorted((d for d in run.designs if d.status == "accepted"), key=lambda d: d.rank)
    final_rows = [dict(d.row, **{}) for d in acc]
    final_rows = [{"Rank": d.rank, **d.row} for d in acc]
    pd.DataFrame(final_rows).to_csv(root / "final_design_stats.csv", index=False)
    pd.DataFrame([d.row for d in run.designs]).to_csv(root / "mpnn_design_stats.csv", index=False)
    pd.DataFrame(run.traj_rows).to_csv(root / "trajectory_stats.csv", index=False)
    # failure counters: rows that fail a stem count once per stem; the AlphaFold2 pre-filters act before a row exists
    c = Counter(s for d in run.designs if d.status == "rejected" for s in d.truth["fail"])
    fr = {col: 0 for col in RN.FAIL_COLUMNS}
    fr.update({"Trajectory_logits_pLDDT": 195, "Trajectory_softmax_pLDDT": 83, "Trajectory_one-hot_pLDDT": 158,
               "Trajectory_final_pLDDT": 217, "Trajectory_Clashes": 140})
    fr.update({"pLDDT": 1210, "i_pTM": 2840, "i_pAE": 3105})
    fr.update(dict(c))
    pd.DataFrame([fr]).to_csv(root / "failure_csv.csv", index=False)
    if settings is not None:
        (root / f"{builder.settings_name}.json").write_text(json.dumps(settings, indent=4) + "\n")
    if with_xlsx:
        import openpyxl
        from openpyxl.styles import PatternFill

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "final_design_stats"
        cols = list(final_rows[0].keys())
        ws.append(cols)
        fill = PatternFill("solid", fgColor="FFFFC000")
        for i, r in enumerate(final_rows):
            ws.append([None if (isinstance(v, float) and v != v) else v for v in (r[c_] for c_ in cols)])
            if i == 0:
                for cell in ws[ws.max_row]:
                    cell.fill = fill
        _save_xlsx(wb, root / f"{folder}_final_design_stats.xlsx")
    return files


def _save_xlsx(wb, path: Path) -> None:
    """Save a workbook with fixed time stamps in the archive and in docProps/core.xml (the file is the same bytes on every run)."""
    import io
    import re
    import zipfile

    buf = io.BytesIO()
    wb.save(buf)
    stamp = b"2026-01-01T00:00:00Z"
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as src, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*", rb"\g<1>" + stamp, data)
            item = zipfile.ZipInfo(info.filename, date_time=(2026, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o644 << 16
            dst.writestr(item, data)


def make_project(out_dir: str | Path, seed: int = DEFAULT_SEED, with_bindcraft2: bool = True, log=lambda *a: None,
                 plans: tuple[list[Spec], list[Spec]] | None = None) -> dict:
    """Write the toy project below out_dir and return its ground truth (also saved as toy_truth.json).

    plans: the backbones of the two runs (default: plans.RUN1 and plans.RUN2; plans.RUN1_EXAMPLE and RUN2_EXAMPLE make a small project)."""
    plan1, plan2 = plans if plans is not None else (RUN1, RUN2)
    contract = plans is None          # the default project is held to selfcheck.py; a project from other plans (the schema examples) is not
    from . import bc2 as BC2
    from . import reference as REF

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("reference", "construct", "runs", "bindcraft2"):      # only the folders this function owns
        if (out / sub).exists():
            shutil.rmtree(out / sub)
    rng = np.random.default_rng(seed + 1000)
    tg = build_target(seed)
    log("target built")
    # the construct frame is the reference frame turned and moved (BindCraft gets a file in its own frame)
    Rt = G.random_rotation(rng)
    tt = rng.normal(size=3) * 15.0
    T = (Rt, tt)
    deleted_ref = {r for a, b in CONSTRUCT_DELETED_REF for r in range(a, b + 1)}
    sc_tmp = make_scene(tg, deleted_ref)
    surf_tmp = surface_of(tg, sc_tmp)
    hot3 = choose_hotspots(surf_tmp, rng, 3)
    centre = np.mean([next(s["cb"] for s in surf_tmp if s["res"] == r) for r in hot3], axis=0)
    extra = sorted((s for s in surf_tmp if s["res"] not in hot3), key=lambda s: np.linalg.norm(s["cb"] - centre))
    hot4 = sorted(hot3 + [extra[0]["res"]])
    sc1 = make_scene(tg, set(), hot3)
    sc2 = make_scene(tg, deleted_ref, hot4)
    surf1, surf2 = surface_of(tg, sc1), surface_of(tg, sc2)
    log("scenes ready; hotspots", hot3, hot4)
    seeds1 = [int(x) for x in rng.choice(np.arange(100000, 999999), size=len(plan1), replace=False)]
    seeds2 = [int(x) for x in rng.choice(np.arange(100000, 999999), size=len(plan2), replace=False)]
    b1 = RunBuilder("run01", plan1, tg, sc1, surf1, hot3, T, np.random.default_rng(seed + 11), set(), "run01", seeds1, contract)
    b1.log = log
    r1 = b1.build()
    log("run01 built", len(r1.designs), "designs")
    b2 = RunBuilder("run02", plan2, tg, sc2, surf2, hot4, T, np.random.default_rng(seed + 12), deleted_ref, "run02", seeds2, contract)
    b2.log = log
    r2 = b2.build()
    log("run02 built", len(r2.designs), "designs")

    runs_dir = out / "runs"
    construct_dir = out / "construct"
    # construct files
    A = tg.atoms_a
    res = np.array(A.resnum)
    con = A.select((res >= CONSTRUCT_FIRST) & (res <= CONSTRUCT_LAST))
    full = con.copy(xyz=con.xyz @ Rt.T + tt, resnum=[n - OFFSET for n in con.resnum], bfactor=85.0)
    ST.write_pdb(construct_dir / "toy_construct.pdb", [full], "toy_construct")
    keep = ~np.isin(np.array(con.resnum), list(deleted_ref))
    gapped = con.select(keep)
    gapped = gapped.copy(xyz=gapped.xyz @ Rt.T + tt, resnum=[n - OFFSET for n in gapped.resnum], bfactor=85.0)
    ST.write_pdb(construct_dir / "toy_construct_gapped.pdb", [gapped], "toy_construct_gapped")

    s1 = {"design_path": f"{TOOL_PATH}/runs/toy/run01/", "binder_name": "binder",
          "starting_pdb": f"{TOOL_PATH}/inputs/toy_construct.pdb", "chains": "A",
          "target_hotspot_residues": b1.hot_str, "lengths": [60, 100], "number_of_final_designs": r1.final_target}
    files1 = write_run(runs_dir, r1, b1, "run01", True, "Accepted", "Rejected", s1, np.random.default_rng(seed + 21), True)
    files2 = write_run(runs_dir, r2, b2, "run02", False, "accepted", "rejected", None, np.random.default_rng(seed + 22), False)
    files1 = {k: f"runs/{v}" for k, v in files1.items()}          # relative to the project folder
    files2 = {k: f"runs/{v}" for k, v in files2.items()}
    log("runs written")
    if contract:
        measured = SC.measure(out, {"run01": r1, "run02": r2}, {"run01": b1, "run02": b2}, T)
        SC.verify(measured)                                 # the contract of the fixture: fail here, not in a test far away
        log("contract holds")

    truth: dict = {
        "seed": seed, "offset": OFFSET, "construct_reference_range": [CONSTRUCT_FIRST, CONSTRUCT_LAST],
        "construct_length": CONSTRUCT_LAST - CONSTRUCT_FIRST + 1,
        "deleted_reference": CONSTRUCT_DELETED_REF,
        "deleted_construct": [[a - OFFSET, b - OFFSET] for a, b in CONSTRUCT_DELETED_REF],
        "frame_rotation": Rt.tolist(), "frame_translation": tt.tolist(),
        "frame_note": "x_construct = x_reference @ R.T + t",
        "two_fold": "chain B = (-x, -y, z) of chain A in the reference frame",
        "hotspots_construct": {"run01": b1.hot_c, "run02": b2.hot_c},
        "hotspots_reference": {"run01": hot3, "run02": hot4},
        "runs": {"run01": {**r1.truth, "files": files1, "final_designs_target": r1.final_target,
                           "target_numbers": r1.target_numbers},
                 "run02": {**r2.truth, "files": files2, "target_numbers": r2.target_numbers}},
        "accepted": {"run01": [d.id for d in sorted(r1.designs, key=lambda d: d.rank or 99) if d.status == "accepted"],
                     "run02": [d.id for d in sorted(r2.designs, key=lambda d: d.rank or 99) if d.status == "accepted"]},
        "design_kind": {"run01": {d.id: d.truth["kind"] for d in r1.designs}, "run02": {d.id: d.truth["kind"] for d in r2.designs}},
    }
    REF.write_reference(out, tg, seed)
    try:                                                    # the toy profile needs the package (it completes the built-in default one)
        from .testing import toy_profile

        (out / "reference" / "toy.profile.json").write_text(json.dumps(toy_profile(), indent=2) + "\n")
    except ImportError:
        pass
    if with_bindcraft2:
        truth["bindcraft2"] = BC2.write_campaigns(out, r1, r2, b1, b2, T, seed)
    truth["reference"] = {"cif": "reference/toy_target.cif", "pdb": "reference/toy_target.pdb", "chains": ["A", "B"],
                          "sequence_length": len(tg.sequence), "cif_chain_b": "reference/toy_target_chain_b.cif",
                          "chain_b_note": "the same structure with the chain ids swapped: the target is chain B, its partner chain A"}
    truth["construct"] = {"pdb": "construct/toy_construct.pdb", "gapped_pdb": "construct/toy_construct_gapped.pdb"}
    (out / "toy_truth.json").write_text(json.dumps(_plain(truth), indent=1))
    (out / "README.txt").write_text(README)
    return truth


README = """Synthetic toy project (generated by tests/toydata/make.py; every number and coordinate is invented).

  reference/   the full-length toy target: a homodimer (chains A and B, 250 residues each, related by an exact
               two-fold rotation), as mmCIF and PDB (toy_target_chain_b.cif: the same with the chain ids swapped, so that
               the target is chain B), with its sequence and a few related sequences; refdata/ is a toy
               offline reference cache (UniProt-style records of the toy accessions Z0TOY1 to Z0TOY6, a panel table,
               toy models of the paralogs) and toy.profile.json a profile that names its orthologs and paralogs
  construct/   what BindCraft was given: residues 60 to 169 of chain A renumbered 1 to 110, in its own frame
               (toy_construct.pdb), and a variant that lacks two short loops but keeps the numbering
  runs/        two BindCraft 1 run folders (run01: Accepted, Rejected, hotspots in a settings file;
               run02: the gapped construct, no settings file, no Rejected folder)
  bindcraft2/  two BindCraft 2 campaign folders built from the same toy coordinates
  toy_truth.json  what the generator did: frame, hotspots, the goal of every pose, which designs are related

Try:  binder-review inspect runs/run01
      binder-review import run01=runs/run01 run02=runs/run02 --target-pdb construct/toy_construct.pdb \\
          --reference-cif reference/toy_target.cif --name toy --out work/toy --bundle work/toy_bundle
      # with a specificity panel (offline, from the toy reference cache):
      BINDERSCOPE_REFDATA_DIR=reference/refdata BINDERSCOPE_OFFLINE=1 binder-review import run01=runs/run01 run02=runs/run02 \\
          --target-pdb construct/toy_construct.pdb --reference-cif reference/toy_target.cif --uniprot Z0TOY1 \\
          --profile reference/toy.profile.json --name toy --out work/toy_panel
"""
