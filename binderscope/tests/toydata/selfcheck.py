"""The contract of the toy project, checked on the finished artefacts at the end of `make_project`.

The tests of the package assert properties of the toy data (a clean_hot design touches two hotspots, one accepted
design lies within 3 A of the other subunit, ...). The generator used to reach them by luck of the draw: the docking
search works on a coarse proxy of the binder and verifies its goal on all atoms only for the representative design of a
backbone, so a draw could miss a property three test modules away from the cause. Here every property is measured on
what was written (the PDB files of the designs, in the construct frame, with the re-predicted target of that model)
and `verify` raises with the measurements when one is missing, so a fixture that cannot meet its contract fails when it
is built.

The guarantees (G1 to G4) and the margins they are held to:

  G1  every clean_hot design (every variant of a backbone, rejected ones included) touches at least two hotspot
      residues, with a heavy atom within HOT_CUT - HOT_MARGIN (4.5 A is the cut the pages use; the margin is what the
      placement fit and the re-predicted target of the saved model can move a contact by).
  G2  at least one run01 design has fewer than IFACE_MIN_N target residues near the binder at the weight cut of the
      whole-target fallback of `place_design` (CA within 14.6 A of a binder atom), so that code path runs; every other
      design has at least IFACE_MIN_N at the weight cut the pages report (CA within 13.0 A). The fallbacks are few.
  G3  exactly one accepted design (of both runs) has a binder atom within PARTNER_CUT (3.0 A) of chain B, with
      PARTNER_MARGIN to spare, and the other accepted touch_other design lies at least PARTNER_CUT + PARTNER_MARGIN away.
  G4  exactly NEAR_MISS_N of the rejected run01 designs that have a structure are near misses: one stem missed by at most
      a quarter of its threshold on every scope (the contract rule of binder_review.model.near_miss_record, restated here
      from the generator's own copy of the thresholds so that the fixture does not depend on the code under test).
"""
from __future__ import annotations

from pathlib import Path

import gemmi
import numpy as np
from scipy.spatial import cKDTree

from . import runs as RN
from .poses import HOT_CUT, HOT_MARGIN, HOT_MIN, NEAR_WEIGHT, REPORT_WEIGHT, near_distance

IFACE_MIN_N = 12                                    # iface_min_n of the interface fit
FALLBACK_MAX = 5
PARTNER_CUT = 3.0
PARTNER_MARGIN = 0.25
NEAR_MISS_N = 4


def read_design(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Target CA numbers and positions, target atoms (numbers, positions) and binder atoms of a written design file."""
    st = gemmi.read_structure(str(path))
    model = st[0]
    tn, tx, bx, can, cax = [], [], [], [], []
    for chain in model:
        for res in chain:
            for atom in res:
                p = [atom.pos.x, atom.pos.y, atom.pos.z]
                if chain.name == "A":
                    tn.append(res.seqid.num)
                    tx.append(p)
                    if atom.name == "CA":
                        can.append(res.seqid.num)
                        cax.append(p)
                else:
                    bx.append(p)
    return np.array(can), np.array(cax), np.array(tn), np.array(tx), np.array(bx)


def hotspots_touched(tn: np.ndarray, tx: np.ndarray, bx: np.ndarray, hot: list[int], cut: float) -> list[int]:
    tree = cKDTree(bx)
    out = []
    for h in hot:
        sel = tx[tn == h]
        if len(sel) and tree.query(sel)[0].min() < cut:
            out.append(int(h))
    return out


def near_counts(cax: np.ndarray, bx: np.ndarray) -> tuple[int, int]:
    """Target residues near the binder at the fallback's weight cut and at the reported one."""
    d = cKDTree(bx).query(cax)[0]
    return int((d <= near_distance(NEAR_WEIGHT)).sum()), int((d <= near_distance(REPORT_WEIGHT)).sum())


def near_miss(vals: dict[int, dict]) -> dict:
    """{"stems": [...], "worst_rel": x, "near": bool} of a rejected design, from the generator's copy of the default filters."""
    avg = RN.average_row(vals)
    rel: dict[str, float] = {}
    for stem, (thr, higher, per_model) in RN.DEFAULT_THRESHOLDS.items():
        for v, t in [(avg[stem], thr)] + [(vals[k][stem], per_model) for k in vals]:
            gap = (t - v) if higher else (v - t)
            if gap > 1e-9:
                rel[stem] = max(rel.get(stem, 0.0), gap / max(abs(t), 1e-9))
    for letter in "KM":
        v = avg["InterfaceAAs"][letter]
        if v > 3:
            rel[f"InterfaceAAs_{letter}"] = (v - 3) / 3
    worst = max(rel.values()) if rel else 0.0
    return {"stems": sorted(rel), "worst_rel": round(worst, 3), "near": len(rel) == 1 and round(worst, 3) <= RN.NEAR_MISS_MAX_REL}


def measure(out: Path, runs: dict, builders: dict, frame: tuple[np.ndarray, np.ndarray]) -> dict:
    """Measure the four guarantees on the written files. runs/builders: {"run01": RunOut, ...}, {"run01": RunBuilder, ...};
    frame: (R, t) with x_construct = x_reference @ R.T + t."""
    Rt, tt = frame
    m: dict = {"hot": {}, "near_n": {}, "partner": {}, "nm": {}}
    for rid, run in runs.items():
        b = builders[rid]
        chain_b = b.sc.oth_xyz @ Rt.T + tt
        tree_b = cKDTree(chain_b)
        for d in run.designs:
            f = d.truth.get("file")
            if not f:
                continue
            can, cax, tn, tx, bx = read_design(out / "runs" / f)
            key = (rid, d.id)
            kind = d.truth["kind"]
            if kind == "clean_hot":
                m["hot"][key] = hotspots_touched(tn, tx, bx, b.hot_c, HOT_CUT - HOT_MARGIN)
            if rid == "run01":
                m["near_n"][key] = near_counts(cax, bx)
                if d.status == "rejected":
                    m["nm"][key] = near_miss(d.truth["vals"])
            if d.status == "accepted":
                m["partner"][key] = (kind, float(tree_b.query(bx)[0].min()))
    return m


def verify(m: dict) -> None:
    """Raise AssertionError naming every guarantee that does not hold, with what was measured."""
    problems: list[str] = []
    short = {k: v for k, v in m["hot"].items() if len(v) < HOT_MIN}
    if short:
        problems.append(f"G1: {len(short)} clean_hot design(s) touch fewer than {HOT_MIN} hotspots within "
                        f"{HOT_CUT - HOT_MARGIN:.2f} A: {short}")
    problems += _plan_problems(m)
    if problems:
        raise AssertionError("the toy project does not meet its contract:\n  " + "\n  ".join(problems))


def _plan_problems(m: dict) -> list[str]:
    problems: list[str] = []
    fallback = [k for k, (n_near, n_rep) in m["near_n"].items() if n_near < IFACE_MIN_N]
    odd = {k: v for k, v in m["near_n"].items() if v[0] >= IFACE_MIN_N and v[1] < IFACE_MIN_N}
    if not 1 <= len(fallback) <= FALLBACK_MAX:
        problems.append(f"G2: {len(fallback)} run01 design(s) fall back on the whole-target fit (want 1 to {FALLBACK_MAX}); "
                        f"residues near the binder per design (fallback cut, reported cut): {m['near_n']}")
    if odd:
        problems.append(f"G2: designs with enough residues for the interface fit but fewer than {IFACE_MIN_N} at the reported cut: {odd}")
    close = {k: v for k, v in m["partner"].items() if v[1] < PARTNER_CUT}
    touch = {k: v for k, v in m["partner"].items() if v[0] == "touch_other"}
    inside = [k for k, v in close.items() if v[1] <= PARTNER_CUT - PARTNER_MARGIN]
    outside = [k for k, v in touch.items() if v[1] >= PARTNER_CUT + PARTNER_MARGIN]
    if len(close) != 1 or len(inside) != 1 or len(outside) != len(touch) - 1 or next(iter(close), None) not in touch:
        problems.append(f"G3: accepted designs within {PARTNER_CUT} A of chain B: {close} (want exactly one, a touch_other design, "
                        f"at most {PARTNER_CUT - PARTNER_MARGIN} A; the other touch_other design at least {PARTNER_CUT + PARTNER_MARGIN} A); "
                        f"accepted touch_other designs: {touch}")
    near = [k for k, v in m["nm"].items() if v["near"]]
    if len(near) != NEAR_MISS_N:
        problems.append(f"G4: {len(near)} near-miss rescue(s) among {len(m['nm'])} rejected run01 designs with a structure "
                        f"(want exactly {NEAR_MISS_N}): {m['nm']}")
    return problems
