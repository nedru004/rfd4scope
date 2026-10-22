"""The toy target: a full-length homodimer built from ideal secondary-structure segments.

Chain A has 250 residues: helices (phi -57, psi -47), strands (phi -120, psi 130) and loops drawn from the
allowed regions of the Ramachandran plot, assembled by a seeded greedy growth with clash rejection and a
compactness score. Residues 60 to 169 form the "construct" domain that the toy BindCraft runs see; the
residues before and after it are the "rest of the chain". Chain B is chain A turned by an exact two-fold
rotation about the z axis, placed so that the two subunits touch without clashing.

Everything is a function of the seed. No random number is drawn outside the Generator that is passed in.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from . import geometry as G

N_RES = 250
CONSTRUCT_FIRST, CONSTRUCT_LAST = 60, 169          # reference numbers of the construct (110 residues)

# (kind, length, domain). Loops between elements are separate entries; the construct domain is number 1.
PLAN = [
    ("L", 4, 0), ("H", 16, 0), ("L", 4, 0), ("E", 6, 0), ("L", 3, 0), ("E", 6, 0), ("L", 4, 0), ("H", 14, 0), ("L", 5, 0),
    ("H", 16, 1), ("L", 4, 1), ("H", 14, 1), ("L", 5, 1), ("E", 6, 1), ("L", 3, 1), ("E", 6, 1), ("L", 4, 1), ("H", 18, 1),
    ("L", 4, 1), ("E", 6, 1), ("L", 3, 1), ("H", 14, 1), ("L", 5, 1),
    ("H", 16, 2), ("L", 4, 2), ("E", 6, 2), ("L", 3, 2), ("E", 6, 2), ("L", 4, 2), ("H", 18, 2), ("L", 4, 2), ("H", 16, 2),
    ("L", 3, 2),
]
# domain of the residue that closes each loop: the loop belongs to the domain written in PLAN. The loop at residues
# 58 to 62 straddles the start of the construct and the loop at 166 to 170 its end.

_LOOP_REGIONS = [  # (phi, psi, sd, weight)
    (-63.0, -41.0, 12.0, 0.30), (-100.0, 135.0, 25.0, 0.35), (-130.0, 140.0, 15.0, 0.15),
    (-90.0, 0.0, 15.0, 0.10), (60.0, 40.0, 15.0, 0.10),
]

_AA_PROPENSITY = {
    "H": dict(A=.16, L=.14, E=.12, K=.10, R=.08, Q=.08, I=.06, M=.04, D=.04, S=.04, V=.04, T=.03, F=.03, Y=.02, N=.02, H=.01, W=.01),
    "E": dict(V=.16, I=.14, T=.10, Y=.08, F=.08, L=.08, K=.06, R=.05, S=.05, A=.05, E=.05, Q=.04, W=.02, M=.02, N=.01, C=.01),
    "L": dict(G=.14, P=.12, D=.10, N=.10, S=.10, T=.07, K=.07, E=.06, A=.05, Q=.05, R=.04, H=.03, L=.03, V=.02, Y=.01, I=.01),
}


@dataclass
class Chain:
    """Backbone frames and sequence of one chain (generator-side object, reference frame)."""

    N: np.ndarray
    CA: np.ndarray
    C: np.ndarray
    psi: np.ndarray            # radians, per residue
    sequence: str
    ss: str                    # H, E, L per residue
    numbers: np.ndarray        # residue numbers

    def __len__(self) -> int:
        return len(self.sequence)

    def transformed(self, R: np.ndarray, t: np.ndarray) -> "Chain":
        f = lambda x: x @ R.T + t
        return Chain(f(self.N), f(self.CA), f(self.C), self.psi, self.sequence, self.ss, self.numbers)

    def atoms(self, chain_id: str, rng: np.random.Generator, bfactor=0.0, extra_xyz=None, extra_element=None) -> G.Atoms:
        return G.pack_chain(self.sequence, self.N, self.CA, self.C, self.psi, self.numbers, chain_id, rng, bfactor=bfactor,
                            extra_xyz=extra_xyz, extra_element=extra_element)


def sample_loop_torsions(rng: np.random.Generator, k: int, n: int) -> tuple[np.ndarray, np.ndarray]:
    w = np.array([r[3] for r in _LOOP_REGIONS])
    pick = rng.choice(len(_LOOP_REGIONS), size=(k, n), p=w / w.sum())
    mu_phi = np.array([r[0] for r in _LOOP_REGIONS])[pick]
    mu_psi = np.array([r[1] for r in _LOOP_REGIONS])[pick]
    sd = np.array([r[2] for r in _LOOP_REGIONS])[pick]
    return (mu_phi + sd * rng.normal(size=(k, n))) * G.DEG, (mu_psi + sd * rng.normal(size=(k, n))) * G.DEG


def element_torsions(rng: np.random.Generator, k: int, kind: str, n: int, noise: float = 1.0):
    phi0, psi0 = G.HELIX if kind == "H" else G.STRAND
    return ((phi0 + noise * rng.normal(size=(k, n))) * G.DEG, (psi0 + noise * rng.normal(size=(k, n))) * G.DEG)


def _cb(N, CA, C):
    return G.place_cb(N, CA, C)


def _grow_unit(state, loop_n: int, kind: str, n_elem: int, k: int, rng: np.random.Generator):
    """Candidate extensions of the chain by a loop of loop_n residues and an element of n_elem residues."""
    Np, CAp, Cp = state["N"], state["CA"], state["C"]
    m = loop_n + n_elem
    phi = np.zeros((k, m + 1))
    psi = np.zeros((k, m + 1))
    if loop_n:
        lp, ls = sample_loop_torsions(rng, k, loop_n + 1)     # the first entry is psi of the existing last residue
        psi[:, 0] = ls[:, 0]
        phi[:, 1:1 + loop_n] = lp[:, 1:]
        psi[:, 1:1 + loop_n] = ls[:, 1:]
    if n_elem:
        ep, es = element_torsions(rng, k, kind, n_elem)
        phi[:, 1 + loop_n:] = ep
        psi[:, 1 + loop_n:] = es
        if not loop_n:
            psi[:, 0] = es[:, 0]
    first = (Np[-1], CAp[-1], Cp[-1])
    N, CA, C = G.build_frames(phi, psi, first)
    return N[:, 1:], CA[:, 1:], C[:, 1:], phi, psi


def grow_chain(rng: np.random.Generator, plan=PLAN, n_candidates: int = 1500, min_dist: float = 4.0) -> tuple[Chain, list[int]]:
    """Seeded greedy growth of one chain: returns the chain and the domain number of every residue."""
    n0, ca0, c0 = G.canonical_frame()
    st = {"N": [n0], "CA": [ca0], "C": [c0]}
    psi_list: list[float] = [0.0]
    ss_list: list[str] = ["L"]
    dom_list: list[int] = [plan[0][2]]
    # growth runs in units "loop then element"; the first residue of the chain is part of the first loop
    units: list[tuple[int, str, int, int]] = []
    i = 0
    while i < len(plan):
        kind, length, dom = plan[i]
        if kind == "L":
            if i + 1 < len(plan) and plan[i + 1][0] != "L":
                units.append((length, plan[i + 1][0], plan[i + 1][1], plan[i + 1][2]))
                i += 2
            else:
                units.append((length, "L", 0, dom))
                i += 1
        else:
            units.append((0, kind, length, dom))
            i += 1
    units[0] = (units[0][0] - 1, *units[0][1:])
    prev_dom = plan[0][2]
    helix_axes: dict[int, list] = {d: [] for d in range(4)}
    dom_centroid_sum = {d: np.zeros(3) for d in range(4)}
    dom_count = {d: 0 for d in range(4)}

    def add_dom(d, pts):
        dom_centroid_sum[d] = dom_centroid_sum[d] + pts.sum(0)
        dom_count[d] += len(pts)

    add_dom(plan[0][2], np.array([ca0]))
    for loop_n, kind, n_elem, dom in units:
        arr = {kk: np.array(v) for kk, v in st.items()}
        done = False
        k = n_candidates
        for attempt in range(5):
            Nn, CAn, Cn, phi, psi = _grow_unit(arr, loop_n, kind, n_elem, k, rng)
            m = loop_n + n_elem
            CBn = _cb(Nn, CAn, Cn)
            # clash with existing residues except the last three, and within the candidate (separation >= 3)
            ex_n = len(arr["N"])
            ex_atoms = np.concatenate([arr["N"][:-3], arr["CA"][:-3], arr["C"][:-3],
                                       _cb(arr["N"][:-3], arr["CA"][:-3], arr["C"][:-3])]) if ex_n > 3 else np.zeros((0, 3))
            ok = np.ones(k, dtype=bool)
            if len(ex_atoms):
                tree = cKDTree(ex_atoms)
                new_pts = np.concatenate([Nn, CAn, Cn, CBn], axis=1).reshape(k, -1, 3)
                d, _ = tree.query(new_pts.reshape(-1, 3), distance_upper_bound=min_dist)
                ok &= ~(np.isfinite(d).reshape(k, -1).any(axis=1))
            # local: last three existing residues plus the candidate, CA and CB, residues at separation >= 3
            tail = min(3, ex_n)
            allCA = np.concatenate([np.broadcast_to(arr["CA"][-tail:], (k, tail, 3)), CAn], axis=1)
            allCB = np.concatenate([np.broadcast_to(_cb(arr["N"][-tail:], arr["CA"][-tail:], arr["C"][-tail:]), (k, tail, 3)), CBn], axis=1)
            pts = np.concatenate([allCA, allCB], axis=1)                      # (k, 2L, 3)
            L = allCA.shape[1]
            idx = np.concatenate([np.arange(L), np.arange(L)])
            D = np.linalg.norm(pts[:, :, None, :] - pts[:, None, :, :], axis=-1)
            sep = np.abs(idx[:, None] - idx[None, :])
            bad = ((D < min_dist + 0.1) & (sep >= 3)).any(axis=(1, 2))
            ok &= ~bad
            if ok.any():
                done = True
                break
            k *= 2
        if not done:
            raise RuntimeError("toy target growth got stuck; try another seed")
        # score: compactness of the element around its own domain
        elem_ca = CAn[:, loop_n:] if n_elem else CAn
        if dom != prev_dom or dom_count[dom] == 0:
            ref = np.array([dom_centroid_sum[prev_dom] / max(dom_count[prev_dom], 1)])[0]
            score = np.abs(np.linalg.norm(elem_ca.mean(1) - ref, axis=1) - 17.0)
        else:
            ref = dom_centroid_sum[dom] / dom_count[dom]
            score = np.linalg.norm(elem_ca.mean(1) - ref, axis=1) + 0.3 * np.linalg.norm(CAn.mean(1) - ref, axis=1)
        if kind == "H" and n_elem >= 12 and helix_axes[dom]:
            # helices of a domain like to lie side by side (parallel or antiparallel, about 10 A apart): flat layers
            a2, d2 = G.helix_axis(elem_ca)
            dev = np.full(k, np.inf)
            for a1, d1 in helix_axes[dom]:
                ang = np.degrees(np.arccos(np.clip(np.abs((d2 * d1).sum(-1)), 0.0, 1.0)))
                dev = np.minimum(dev, np.abs(G.perp_distance(a1, d1, a2) - 10.0) + 0.1 * ang)
            score = score + 0.8 * dev
        score = np.where(ok, score, np.inf)
        best = np.argsort(score)[:4]
        pick = int(rng.choice(best))
        # commit
        psi_list[-1] = float(psi[pick, 0])
        for j in range(m):
            st["N"].append(Nn[pick, j])
            st["CA"].append(CAn[pick, j])
            st["C"].append(Cn[pick, j])
            psi_list.append(float(psi[pick, j + 1]))
            ss_list.append("L" if j < loop_n else kind)
            dom_list.append(dom)
        add_dom(dom, CAn[pick])
        if kind == "H" and n_elem >= 12:
            a_, d_ = G.helix_axis(CAn[pick][None, loop_n:])
            helix_axes[dom].append((a_[0], d_[0]))
        prev_dom = dom
    psi_arr = np.array(psi_list)
    psi_arr[-1] = -47.0 * G.DEG
    ch = Chain(np.array(st["N"]), np.array(st["CA"]), np.array(st["C"]), psi_arr, "", "".join(ss_list), np.arange(1, len(psi_arr) + 1))
    return ch, dom_list


def random_sequence(ss: str, rng: np.random.Generator) -> str:
    out = []
    for c in ss:
        d = _AA_PROPENSITY[c]
        aas, w = zip(*d.items())
        w = np.array(w) / sum(w)
        out.append(str(rng.choice(aas, p=w)))
    return "".join(out)


@dataclass
class ToyTarget:
    """The reference homodimer and everything the generator needs to know about it (reference frame)."""

    chain_a: Chain
    chain_b: Chain
    atoms_a: G.Atoms
    atoms_b: G.Atoms
    first: int = CONSTRUCT_FIRST
    last: int = CONSTRUCT_LAST
    domains: list = field(default_factory=list)
    dimer_axis_point: np.ndarray = field(default_factory=lambda: np.zeros(3))

    @property
    def sequence(self) -> str:
        return self.chain_a.sequence

    def construct_numbers(self) -> np.ndarray:
        return np.arange(self.first, self.last + 1)


C2Z = np.diag([-1.0, -1.0, 1.0])       # exact two-fold rotation about the z axis through the origin


def _dimer_contacts(A_xyz: np.ndarray, cutoff: float = 4.5) -> tuple[float, int]:
    B_xyz = A_xyz @ C2Z.T
    tree = cKDTree(B_xyz)
    d, _ = tree.query(A_xyz, k=1)
    return float(d.min()), int(len(tree.query_ball_point(A_xyz, cutoff, return_length=True).nonzero()[0]))


def _clash_free(tree: cKDTree, xyz: np.ndarray, d: float, min_dist: float) -> bool:
    """Chain A shifted by +d along x against its two-fold image: the image of a point (x, y, z) is (-x, -y, z)
    shifted by -d, so the distance to a shifted copy equals the distance of the point moved by 2d to the
    (unshifted) image, whose tree is built once per orientation."""
    dist, _ = tree.query(xyz + np.array([2.0 * d, 0.0, 0.0]), distance_upper_bound=min_dist)
    return not np.isfinite(dist).any()


def place_dimer(chain: Chain, atoms: G.Atoms, rng: np.random.Generator, n_rot: int = 150, min_dist: float = 3.3):
    """Orient and shift chain A so that chain B = C2z(A) touches it without clashing and the construct domain
    contributes to the interface. Returns the moved chain and atoms."""
    xyz0 = atoms.xyz
    res_of = np.array(atoms.resnum) - 1
    in_con = (res_of + 1 >= CONSTRUCT_FIRST) & (res_of + 1 <= CONSTRUCT_LAST)
    center = chain.CA.mean(0)
    best, best_score = None, -1.0
    for _ in range(n_rot):
        R = G.random_rotation(rng)
        xyz = (xyz0 - center) @ R.T
        tree = cKDTree(xyz @ C2Z.T)
        lo, hi = 0.0, 80.0
        if not _clash_free(tree, xyz, hi, min_dist):
            continue
        while hi - lo > 0.25:               # smallest shift without a clash (bisection; the clash is monotone enough)
            mid = 0.5 * (lo + hi)
            if _clash_free(tree, xyz, mid, min_dist):
                hi = mid
            else:
                lo = mid
        a = xyz + np.array([hi, 0.0, 0.0])
        hits = cKDTree(a @ C2Z.T).query_ball_point(a, 4.5)
        n_pairs = sum(len(h) for h in hits)
        touching = np.array([len(h) > 0 for h in hits])
        con_res = len(set(res_of[touching & in_con]))
        out_res = len(set(res_of[touching & ~in_con]))
        if con_res < 3 or out_res < 3:
            continue
        score = min(n_pairs, 250) + 2.0 * min(con_res, 12)
        if score > best_score:
            best_score, best = score, (R, hi)
    if best is None:
        raise RuntimeError("toy dimer could not be placed; try another seed")
    R, d = best
    t = np.array([d, 0.0, 0.0])
    f = lambda x: (x - center) @ R.T + t
    moved = Chain(f(chain.N), f(chain.CA), f(chain.C), chain.psi, chain.sequence, chain.ss, chain.numbers)
    return moved, atoms.copy(xyz=f(xyz0))


def build_target(seed: int = 8) -> ToyTarget:
    """The reference homodimer, deterministic in the seed (a growth that gets stuck is retried with the next sub-seed)."""
    last = None
    for attempt in range(40):
        rng = np.random.default_rng(seed * 1000 + attempt)
        try:
            ch, doms = grow_chain(rng)
            ch.sequence = random_sequence(ch.ss, rng)
            atoms = ch.atoms("A", rng, bfactor=92.0)
            ch, atoms = place_dimer(ch, atoms, rng)
        except RuntimeError as ex:
            last = ex
            continue
        # (x, y, z) -> (-x, -y, z) is a proper rotation (determinant +1), so B is an exact two-fold image of A
        chb = ch.transformed(C2Z, np.zeros(3))
        atoms_b = atoms.transformed(C2Z, np.zeros(3), chain="B")
        return ToyTarget(ch, chb, atoms, atoms_b, domains=doms)
    raise RuntimeError(f"toy target could not be built: {last}")
