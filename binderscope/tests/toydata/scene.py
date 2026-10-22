"""Scene construction: which atoms a docked binder may touch, the surface patches and the hotspots."""
from __future__ import annotations

import numpy as np

from .poses import Scene, free_space, make_surface
from .target import CONSTRUCT_FIRST, CONSTRUCT_LAST, ToyTarget


def make_scene(tg: ToyTarget, deleted: set[int] = frozenset(), hot_res: list[int] = ()) -> Scene:
    """Atom sets for docking. `deleted` are reference numbers of residues that the run's target lacks."""
    A, B = tg.atoms_a, tg.atoms_b
    res = np.array(A.resnum)
    xyz = A.xyz
    inside = (res >= CONSTRUCT_FIRST) & (res <= CONSTRUCT_LAST)
    gone = np.isin(res, list(deleted))
    con = inside & ~gone
    hot_mask = np.isin(res, list(hot_res)) if len(hot_res) else np.zeros(len(res), dtype=bool)
    ca = np.array(A.name) == "CA"
    return Scene(
        con_xyz=xyz[con], con_res=res[con], out_xyz=xyz[~inside], oth_xyz=B.xyz, gap_xyz=xyz[gone],
        hot_res=list(hot_res), hot_xyz=xyz[hot_mask], hot_label=res[hot_mask], con_ca=xyz[con & ca],
    )


def surface_of(tg: ToyTarget, sc: Scene) -> list[dict]:
    """Construct residues with outward normal, neighbour count and free space above them."""
    A, B = tg.atoms_a, tg.atoms_b
    atoms = {"xyz": np.concatenate([A.xyz, B.xyz]),
             "res": np.concatenate([np.array(A.resnum), -np.ones(len(B), dtype=int)]),
             "name": np.concatenate([np.array(A.name), np.array(B.name)])}
    surf = make_surface(atoms, sorted(set(int(r) for r in sc.con_res)))
    for s in surf:
        s["free"] = free_space(sc, s["cb"], s["normal"])
        s["groove"] = len(sc.trees["con"].query_ball_point(s["cb"] + 8.0 * s["normal"], 12.0))
    return surf


def choose_hotspots(surf: list[dict], rng: np.random.Generator, n: int = 3) -> list[int]:
    """Three exposed construct residues that sit close together on a patch with free space above it."""
    med = np.median([s["n_nb"] for s in surf])
    exposed = [s for s in surf if s["n_nb"] <= med * 1.0 and s["free"] >= 11]
    exposed.sort(key=lambda s: -s["groove"])
    seed = exposed[int(rng.integers(0, min(3, len(exposed))))]
    chosen = [seed]
    pool = sorted(exposed, key=lambda s: -s["groove"])
    for s in pool:
        if len(chosen) >= n:
            break
        if all(4.5 <= np.linalg.norm(s["cb"] - c["cb"]) <= 12.0 for c in chosen):
            chosen.append(s)
    return sorted(c["res"] for c in chosen)


def patch_point(surf: list[dict], residues: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Centre and outward normal of a set of surface residues."""
    by = {s["res"]: s for s in surf}
    pts = np.array([by[r]["cb"] for r in residues])
    nrm = np.array([by[r]["normal"] for r in residues]).mean(0)
    return pts.mean(0), nrm / np.linalg.norm(nrm)
