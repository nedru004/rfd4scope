"""Docking the toy binders onto the toy target: seeded rigid-body search with explicit geometric goals.

The search works on a coarse proxy of the binder (CA, CB and a pseudo side-chain centroid per residue) and on
the full heavy-atom target. A pose is (R, t) with x_reference = x_local @ R.T + t. Goals ("kinds"):

  clean_hot     touches at least two of the run's hotspot residues, nothing outside the construct is touched
  clean_away    sits on the construct far from every hotspot, nothing outside the construct is touched
  collide_out   sits on the construct but overlaps the part of chain A that lies outside the construct
  touch_other   sits on the construct and touches the other subunit (chain B) without a clash
  collide_gap   sits on the construct and overlaps a stretch that this run deleted from its target

The final test of every goal is done by the caller on the finished all-atom model (`classify`). That test covers the
representative design of a backbone only; every sibling variant has side chains of its own, so the caller measures each
variant against the plan again (project.RunBuilder._design_problem) and selfcheck.py measures the written files.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from . import geometry as G
from .binder import Bundle

KINDS = ("clean_hot", "clean_away", "collide_out", "touch_other", "collide_gap")

HOT_CUT = 4.5            # a hotspot is touched when a binder heavy atom is this close (the cut the pages use)
HOT_MARGIN = 0.3         # what a contact is held inside the cut by: the placement fit and the re-predicted target move it this much
HOT_MIN = 2              # a clean_hot design touches at least this many hotspots
HOT_TIGHT = 3.7          # proxy level: a CB or side-chain centroid point this close to a hotspot residue is a contact with room to spare
IFACE_FULL, IFACE_DECAY = 8.0, 6.0     # the defaults of the interface fit of binder_review.target_frame
NEAR_WEIGHT, REPORT_WEIGHT = 0.3, 0.5  # interface weight that counts a target residue as near the binder (fallback) and as reported


def near_distance(weight: float) -> float:
    """The CA-to-binder distance at which the interface weight of the placement fit falls to `weight`."""
    return IFACE_FULL + IFACE_DECAY * float(np.sqrt(-np.log(weight)))


@dataclass
class Scene:
    """Atom sets of the reference frame (angstrom)."""

    con_xyz: np.ndarray                     # heavy atoms of the construct residues that BindCraft sees
    con_res: np.ndarray                     # reference number per construct atom
    out_xyz: np.ndarray                     # chain A outside the construct
    oth_xyz: np.ndarray                     # chain B
    gap_xyz: np.ndarray                     # residues the run deleted (empty for a run that kept them all)
    hot_res: list                           # hotspot residues (reference numbers)
    hot_xyz: np.ndarray
    hot_label: np.ndarray                   # which hotspot residue each hot atom belongs to
    surface: list = field(default_factory=list)   # per construct residue dicts, see make_surface
    con_ca: np.ndarray | None = None        # CA of the construct residues that BindCraft sees
    trees: dict = field(default_factory=dict)

    def __post_init__(self):
        for k in ("con", "out", "oth", "gap", "hot"):
            xyz = getattr(self, f"{k}_xyz")
            self.trees[k] = cKDTree(xyz) if len(xyz) else None


def make_surface(scene_atoms: dict, construct: range) -> list[dict]:
    """Per construct residue: CB (or CA), outward normal and how free the space above it is."""
    xyz, res, name = scene_atoms["xyz"], scene_atoms["res"], scene_atoms["name"]
    tree = cKDTree(xyz)
    allxyz = xyz
    out = []
    for r in construct:
        idx = np.nonzero(res == r)[0]
        if not len(idx):
            continue
        names = name[idx]
        cb = xyz[idx[names == "CB"][0]] if (names == "CB").any() else xyz[idx[names == "CA"][0]]
        nb = tree.query_ball_point(cb, 10.0)
        nbx = allxyz[nb]
        n_nb = len(nb)
        cen = nbx.mean(0)
        nrm = G.unit(cb - cen)
        out.append({"res": int(r), "cb": cb, "normal": nrm, "n_nb": n_nb})
    return out


def free_space(sc: Scene, p: np.ndarray, n: np.ndarray, h: float = 14.0) -> float:
    """Distance from the point 14 A above a surface point to the nearest atom that is not part of the construct."""
    q = p + h * n
    d = []
    for k in ("out", "oth", "gap"):
        t = sc.trees[k]
        if t is not None:
            d.append(t.query(q)[0])
    return float(min(d)) if d else 99.0


def proxy(b: Bundle) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """N, CA, C, O, CB and a pseudo side-chain centroid per residue: (n, 6, 3), residue index and type per point."""
    cb = b.cb()
    cen = cb + 1.6 * G.unit(cb - b.CA)
    o = G.place_o(b.N, b.CA, b.C, b.psi)
    pts = np.stack([b.N, b.CA, b.C, o, cb, cen], axis=1)
    return pts, np.repeat(np.arange(b.length), 6), np.tile(np.arange(6), b.length)


@dataclass
class Pose:
    R: np.ndarray
    t: np.ndarray
    loss: float = np.inf
    info: dict = field(default_factory=dict)

    def apply(self, xyz: np.ndarray) -> np.ndarray:
        return xyz @ self.R.T + self.t


def _min_d(tree, x: np.ndarray) -> np.ndarray:
    if tree is None:
        return np.full(len(x), 99.0)
    return tree.query(x)[0]


class Docker:
    def __init__(self, bundle: Bundle, scene: Scene, kind: str, rng: np.random.Generator, count_range=(16, 32),
                 d_if: float = 5.0, gap: float = 3.3):
        self.b, self.sc, self.kind, self.rng = bundle, scene, kind, rng
        self.lo, self.hi = count_range
        self.d_if, self.gap = d_if, gap
        pts, self.res_of, self.typ = proxy(bundle)
        self.local = pts.reshape(-1, 3)
        self.c_local = self.local.mean(0)
        self.slide = np.nonzero(np.isin(self.typ, (1, 3, 4, 5)))[0]       # points that decide how close the binder may sit
        _, _, vt = np.linalg.svd(bundle.CA - bundle.CA.mean(0), full_matrices=False)
        if np.linalg.det(vt) < 0:                             # keep the frame right-handed: a mirror image would turn L into D
            vt[2] *= -1.0
        self.axes_local = vt                                  # rows: long, middle and short axis of the binder
        self.n = bundle.length
        self.need_hot = max(2, int(np.ceil(len(scene.hot_res) * 0.6))) if scene.hot_res else 0

    # ----------------------------------------------------------------------------------------- evaluation
    def _contacts(self, d: np.ndarray, thr: float) -> int:
        """Residues whose side chain (centroid within thr, CB within thr - 0.4) or backbone (within thr - 0.8) is close."""
        cut = np.where(self.typ == 5, thr, np.where(self.typ == 4, thr - 0.4, thr - 0.8))
        flag = np.zeros(self.n, dtype=bool)
        flag[self.res_of[d < cut]] = True
        return int(flag.sum())

    def evaluate(self, X: np.ndarray) -> tuple[float, dict]:
        sc = self.sc
        dcon = _min_d(sc.trees["con"], X)
        dout = _min_d(sc.trees["out"], X)
        doth = _min_d(sc.trees["oth"], X)
        dgap = _min_d(sc.trees["gap"], X)
        dhot = _min_d(sc.trees["hot"], X)
        n_con = self._contacts(dcon, self.d_if)
        loss = 3.0 * max(0, self.lo - n_con, n_con - self.hi)
        loss += 40.0 * max(0.0, self.gap - dcon.min())
        info = {"n_con": n_con, "min_con": float(dcon.min()), "min_out": float(dout.min()), "min_oth": float(doth.min()),
                "min_gap": float(dgap.min())}
        # hotspots touched: a CB or centroid point within 5.5 A of a hotspot residue's atoms
        touched = 0
        if sc.trees["hot"] is not None:
            _, idx = sc.trees["hot"].query(X)
            near = (dhot < 5.0) & (self.typ >= 4)
            touched = len(set(sc.hot_label[idx[near]].tolist()))
        info["hot_touched"] = touched
        tight = 0                                              # hotspots with a CB or centroid point well inside the cut
        if sc.trees["hot"] is not None:
            _, idx = sc.trees["hot"].query(X)
            close = (dhot < HOT_TIGHT) & (self.typ >= 4)
            tight = len(set(sc.hot_label[idx[close]].tolist()))
        info["hot_tight"] = tight
        info["min_hot"] = float(dhot.min())
        k = self.kind
        non_con = np.minimum(np.minimum(dout, doth), dgap)
        if k == "clean_hot":
            loss += 5.0 * max(0, self.need_hot - touched) + 2.0 * max(0.0, 6.5 - non_con.min()) + 2.0 * np.maximum(0.0, 6.5 - non_con).sum() / 10
        elif k == "clean_away":
            loss += 0.8 * max(0.0, 12.0 - dhot.min()) + 2.0 * max(0.0, 6.5 - non_con.min()) + 2.0 * np.maximum(0.0, 6.5 - non_con).sum() / 10
        elif k == "collide_out":
            close = int((dout < 3.0).sum())
            loss += 2.0 * max(0, 4 - close) + 1.5 * max(0.0, dout.min() - 2.2)
            loss += 3.0 * max(0.0, 5.5 - min(doth.min(), dgap.min()))
        elif k == "collide_gap":
            close = int((dgap < 3.0).sum())
            loss += 2.0 * max(0, 4 - close) + 1.5 * max(0.0, dgap.min() - 2.2)
            loss += 3.0 * max(0.0, 5.5 - min(dout.min(), doth.min()))
        elif k == "touch_other":
            n_oth = self._contacts(doth, self.d_if)
            info["n_oth"] = n_oth
            loss += 2.0 * max(0, 3 - n_oth) + 20.0 * max(0.0, 3.0 - doth.min())
            loss += 3.0 * max(0.0, 3.4 - min(dout.min(), dgap.min()))
        info["loss"] = float(loss)
        return float(loss), info

    # ----------------------------------------------------------------------------------------- moves
    def _place(self, R: np.ndarray, centre: np.ndarray, normal: np.ndarray, lateral: np.ndarray):
        """Binder centroid on the ray centre + normal * s, moved toward the target until the construct is `gap` away.
        Returns the proxy coordinates and the translation t (x_ref = x_local @ R.T + t)."""
        t0 = centre + lateral - self.c_local @ R.T
        base = self.local @ R.T + t0
        sub = base[self.slide]
        tree = self.sc.trees["con"]

        def dmin(s: float) -> float:
            return float(tree.query(sub + s * normal, distance_upper_bound=self.gap + 1.0)[0].min())

        lo, hi = -25.0, 45.0
        if dmin(hi) >= self.gap:
            for _ in range(10):
                mid = 0.5 * (lo + hi)
                if dmin(mid) >= self.gap:
                    hi = mid
                else:
                    lo = mid
        return base + hi * normal, t0 + hi * normal

    def _lying(self, normal: np.ndarray) -> np.ndarray:
        """Random rotation that lays the long axis of the binder along the surface (a bundle lying on the target)."""
        rng = self.rng
        tvec = rng.normal(size=3)
        tvec -= (tvec @ normal) * normal
        tvec = G.unit(tvec)
        bvec = np.cross(normal, tvec)
        phi = rng.uniform(0, 2 * np.pi)
        v2 = np.cos(phi) * bvec + np.sin(phi) * normal
        v3 = np.cross(tvec, v2)
        R = np.stack([tvec, v2, v3], axis=1) @ self.axes_local     # local axes -> target directions
        tilt = G.rotation_matrix(rng.normal(size=3), np.radians(12.0) * abs(rng.normal()))
        return tilt @ R

    def dock(self, point: np.ndarray, normal: np.ndarray, n_starts: int = 150, n_iter: int = 140) -> Pose:
        rng = self.rng
        starts = []
        for _ in range(n_starts):
            R = self._lying(normal)
            lat = rng.normal(size=3) * 3.0
            lat -= (lat @ normal) * normal
            X, t = self._place(R, point, normal, lat)
            loss, info = self.evaluate(X)
            starts.append((loss, R, lat, t, info))
        starts.sort(key=lambda s: s[0])
        best = None
        for cur in starts[:2]:
            step_r, step_t = 8.0, 1.5
            for it in range(n_iter):
                if cur[0] <= 0.0:
                    break
                dR = G.rotation_matrix(rng.normal(size=3), np.radians(step_r) * abs(rng.normal()))
                R2 = dR @ cur[1]
                lat2 = cur[2] + rng.normal(size=3) * step_t * abs(rng.normal())
                lat2 -= (lat2 @ normal) * normal
                X, t2 = self._place(R2, point, normal, lat2)
                l2, i2 = self.evaluate(X)
                if l2 <= cur[0]:
                    cur = (l2, R2, lat2, t2, i2)
                if it % 30 == 29:
                    step_r, step_t = step_r * 0.6, step_t * 0.6
            if best is None or cur[0] < best[0]:
                best = cur
            if best[0] <= 0.0:
                break
        loss, R, lat, t, info = best
        return Pose(R, t, float(loss), info)

    def refine(self, pose: Pose, point: np.ndarray, normal: np.ndarray, n_iter: int = 80, step_r: float = 3.0,
               step_t: float = 0.8) -> Pose:
        """Local search around an existing pose (a sibling backbone docked near a sibling's pose)."""
        rng = self.rng
        c = self.c_local @ pose.R.T + pose.t
        lat = c - point
        lat -= (lat @ normal) * normal
        X, t = self._place(pose.R, point, normal, lat)
        loss, info = self.evaluate(X)
        cur = (loss, pose.R, lat, t, info)
        for _ in range(n_iter):
            if cur[0] <= 0.0:
                break
            dR = G.rotation_matrix(rng.normal(size=3), np.radians(step_r) * abs(rng.normal()))
            R2 = dR @ cur[1]
            lat2 = cur[2] + rng.normal(size=3) * step_t * abs(rng.normal())
            lat2 -= (lat2 @ normal) * normal
            X, t2 = self._place(R2, point, normal, lat2)
            l2, i2 = self.evaluate(X)
            if l2 <= cur[0]:
                cur = (l2, R2, lat2, t2, i2)
        return Pose(cur[1], cur[3], float(cur[0]), cur[4])


def classify(binder_xyz: np.ndarray, binder_res: np.ndarray, sc: Scene) -> dict:
    """Geometry of a finished all-atom binder against the scene sets (heavy atoms, angstrom)."""
    out = {}
    for k in ("con", "out", "oth", "gap"):
        t = sc.trees[k]
        if t is None:
            out[f"min_{k}"] = 99.0
            out[f"n_{k}"] = 0
            out[f"clash_{k}"] = 0
            continue
        d, _ = t.query(binder_xyz)
        out[f"min_{k}"] = float(d.min())
        out[f"n_{k}"] = int(len(set(binder_res[d < 4.5].tolist())))
        out[f"clash_{k}"] = int((d < 2.5).sum())
    if sc.trees["hot"] is not None:
        d, idx = sc.trees["hot"].query(binder_xyz)
        out["hot_touched"] = sorted(set(int(x) for x in sc.hot_label[idx[d < HOT_CUT]].tolist()))
        out["hot_safe"] = sorted(set(int(x) for x in sc.hot_label[idx[d < HOT_CUT - HOT_MARGIN]].tolist()))
        out["min_hot"] = float(d.min())
    if sc.con_ca is not None and len(sc.con_ca):
        out["n_near"] = int((cKDTree(binder_xyz).query(sc.con_ca)[0] <= near_distance(NEAR_WEIGHT)).sum())
    return out
