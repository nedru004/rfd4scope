"""Idealised helical-bundle binders (two or three helices joined by short loops) and their sequences.

The helices are ideal (phi -57, psi -47). The loops are found by a seeded random search that makes consecutive
helices antiparallel and about 10 A apart, which is what a designed bundle looks like. The backbone is a
function of the seed; the sequence follows the role of each position (core, interface, surface, loop).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import geometry as G
from .target import _grow_unit, element_torsions

HELIX_AA = {
    "core": dict(L=.34, I=.14, V=.10, A=.10, F=.12, M=.04, W=.03, Y=.05, C=.0, T=.03, S=.02, N=.0, G=.0, Q=.03),
    "iface": dict(L=.12, I=.05, V=.03, F=.12, Y=.14, W=.09, M=.02, A=.02, R=.11, K=.03, E=.05, D=.03, S=.03, T=.03, N=.04, Q=.06, H=.05, G=.0),
    "surf": dict(E=.24, K=.16, R=.10, Q=.10, D=.08, A=.08, S=.07, T=.05, N=.04, L=.03, G=.01, H=.02, V=.02),
    "loop": dict(G=.20, S=.14, D=.14, N=.10, P=.10, T=.08, K=.08, E=.06, A=.04, R=.04, Q=.02),
}


@dataclass
class Bundle:
    """A designed backbone in its own frame."""

    phi: np.ndarray                 # radians, (n,)
    psi: np.ndarray
    N: np.ndarray                   # (n, 3)
    CA: np.ndarray
    C: np.ndarray
    ss: str                         # H or L per residue
    helices: list = field(default_factory=list)      # [(first, last)] 0-based inclusive
    axes: list = field(default_factory=list)         # [(anchor, direction)] of every helix

    @property
    def length(self) -> int:
        return len(self.ss)

    def transformed(self, R: np.ndarray, t: np.ndarray) -> "Bundle":
        f = lambda x: x @ R.T + t
        return Bundle(self.phi, self.psi, f(self.N), f(self.CA), f(self.C), self.ss, self.helices,
                      [(f(a), d @ R.T) for a, d in self.axes])

    def cb(self) -> np.ndarray:
        return G.place_cb(self.N, self.CA, self.C)


_axis = G.helix_axis
_perp = G.perp_distance


def plan_bundle(length: int, n_helices: int, rng: np.random.Generator) -> tuple[list[int], list[int]]:
    """Helix and loop lengths that add up to `length` with one N-cap and one C-cap residue."""
    loops = [int(rng.choice([3, 4, 4, 5])) for _ in range(n_helices - 1)]
    total = length - 2 - sum(loops)
    base = total // n_helices
    helices = [base] * n_helices
    for i in range(total - base * n_helices):
        helices[i % n_helices] += 1
    # a little irregularity, keeping the sum
    for _ in range(2):
        i, j = rng.choice(n_helices, size=2, replace=False)
        step = int(rng.integers(0, 3))
        if helices[i] - step >= 14:
            helices[i] -= step
            helices[j] += step
    return helices, loops


def build_bundle(length: int, n_helices: int, rng: np.random.Generator, k: int = 24000) -> Bundle:
    """Backbone of a helix bundle of `length` residues (see module docstring)."""
    h_lens, l_lens = plan_bundle(length, n_helices, rng)
    phi_h, psi_h = G.HELIX[0] * G.DEG, G.HELIX[1] * G.DEG
    # N-cap residue + first helix, ideal torsions
    n_first = 1 + h_lens[0]
    phi = list(np.full(n_first, phi_h))
    psi = list(np.full(n_first, psi_h))
    ss = ["L"] + ["H"] * h_lens[0]
    helices = [(1, h_lens[0])]
    axes: list[tuple[np.ndarray, np.ndarray]] = []
    N, CA, C = (x[0] for x in G.build_frames(np.array([phi]), np.array([psi])))
    a, d = _axis(CA[None, helices[0][0]: helices[0][1] + 1])
    axes.append((a[0], d[0]))
    for hi in range(1, n_helices):
        loop_n, n_el = l_lens[hi - 1], h_lens[hi]
        found = None
        kk = k
        relax = 1.0
        for attempt in range(6):
            Nn, CAn, Cn, ph, ps = _grow_unit({"N": N, "CA": CA, "C": C}, loop_n, "H", n_el, kk, rng)
            hel = CAn[:, loop_n:]
            a2, d2 = _axis(hel)
            score = np.zeros(kk)
            ok = np.ones(kk, dtype=bool)
            prev = [(axes[-1], 10.0, 30.0, 8.0)]
            if hi >= 2:
                # third helix: also near the first one and parallel to it
                prev.append((axes[0], 11.0, 35.0, 8.0))
            for j, ((a1, d1), want, max_ang, slack) in enumerate(prev):
                sign = -1.0 if j == 0 else 1.0
                cosang = np.clip(sign * (d2 * d1).sum(-1), -1, 1)
                ang = np.degrees(np.arccos(cosang))
                dist = _perp(a1, d1, a2)
                off = np.abs(((a2 - a1) * d1).sum(-1))
                ok &= (ang <= max_ang * relax) & (np.abs(dist - want) <= slack * 0.25 * relax) & (off <= 0.45 * h_lens[hi - 1] * 1.5 + 4)
                score += np.abs(dist - want) + 0.08 * ang + 0.03 * off
            # loops and the new helix must not touch what exists
            old_ca = CA[: helices[-1][1] + 1]
            lo = CAn[:, :loop_n]
            dl = np.linalg.norm(lo[:, :, None, :] - old_ca[None, None, :-2, :], axis=-1).min(axis=(1, 2)) if loop_n else np.full(kk, 99.0)
            dh = np.linalg.norm(hel[:, ::2, None, :] - old_ca[None, None, :, :], axis=-1).min(axis=(1, 2))
            ok &= (dl >= 4.2) & (dh >= 5.0)
            score = np.where(ok, score, np.inf)
            if np.isfinite(score).any():
                found = int(np.argmin(score))
                break
            kk *= 2
            relax *= 1.4
        if found is None:
            raise RuntimeError("no loop closes the bundle; try another seed")
        # commit: psi of the previous last residue, then the loop and the helix
        psi[-1] = float(ps[found, 0])
        for j in range(loop_n + n_el):
            phi.append(float(ph[found, j + 1]))
            psi.append(float(ps[found, j + 1]))
            ss.append("L" if j < loop_n else "H")
        start = len(phi) - n_el
        helices.append((start, len(phi) - 1))
        N, CA, C = (x[0] for x in G.build_frames(np.array([phi]), np.array([psi])))
        a, d = _axis(CA[None, start:])
        axes.append((a[0], d[0]))
    # C-cap residue
    phi.append(phi_h)
    psi.append(psi_h)
    ss.append("L")
    psi[-1] = psi_h
    arr_phi, arr_psi = np.array(phi), np.array(psi)
    N, CA, C = (x[0] for x in G.build_frames(arr_phi[None], arr_psi[None]))
    return Bundle(arr_phi, arr_psi, N, CA, C, "".join(ss), helices, axes)


def perturb_bundle(b: Bundle, rng: np.random.Generator, sigma_helix: float = 1.2, sigma_loop: float = 4.0) -> Bundle:
    """A neighbour of a backbone: the same plan with small torsion noise (another trajectory of the same fold)."""
    isl = np.array([c == "L" for c in b.ss])
    sig = np.where(isl, sigma_loop, sigma_helix) * G.DEG
    phi = b.phi + rng.normal(size=len(b.phi)) * sig
    psi = b.psi + rng.normal(size=len(b.psi)) * sig
    N, CA, C = (x[0] for x in G.build_frames(phi[None], psi[None]))
    axes = [_axis(CA[None, a: z + 1]) for a, z in b.helices]
    return Bundle(phi, psi, N, CA, C, b.ss, b.helices, [(x[0][0], x[1][0]) for x in axes])


def burial(b: Bundle, radius: float = 10.0) -> np.ndarray:
    """Number of other CB within `radius` of each CB: a cheap burial measure inside the bundle."""
    cb = b.cb()
    d = np.linalg.norm(cb[:, None, :] - cb[None, :, :], axis=-1)
    return (d < radius).sum(1) - 1


def roles(b: Bundle, interface: set[int]) -> list[str]:
    bur = burial(b)
    out = []
    for i, c in enumerate(b.ss):
        if c == "L":
            out.append("loop")
        elif i in interface:
            out.append("iface")
        elif bur[i] >= np.percentile(bur, 55):
            out.append("core")
        else:
            out.append("surf")
    return out


def _draw(role: str, rng: np.random.Generator) -> str:
    d = HELIX_AA[role]
    aas, w = zip(*d.items())
    w = np.array(w, dtype=float)
    return str(rng.choice(aas, p=w / w.sum()))


def design_sequence(role_list: list[str], rng: np.random.Generator, base: str | None = None, recovery: float = 0.0,
                    max_iface_k: int = 2, max_iface_m: int = 0, n_term: str = "M") -> str:
    """One sequence for the roles. With `base`, every position keeps the base residue with probability `recovery`
    (the way ProteinMPNN sequences of one backbone resemble the trajectory sequence)."""
    seq = []
    for i, r in enumerate(role_list):
        if base is not None and rng.random() < recovery:
            seq.append(base[i])
        else:
            seq.append(_draw(r, rng))
    # the first residue is a Met in most expression constructs; the toy keeps it out of the interface
    if role_list[0] != "iface" and n_term:
        seq[0] = n_term
    # BindCraft caps interface Lys and Met at 3 on the average; keep the toy designs under their own caps
    ik = [i for i, r in enumerate(role_list) if r == "iface" and seq[i] == "K"]
    for i in ik[max_iface_k:]:
        seq[i] = "R"
    im = [i for i, r in enumerate(role_list) if r == "iface" and seq[i] == "M"]
    for i in im[max_iface_m:]:
        seq[i] = "L"
    # no free cysteine unless a quirk asks for one
    return "".join("A" if c == "C" else c for c in seq)
