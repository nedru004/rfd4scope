"""Small protein geometry kit for the toy data: NeRF backbone builder, CB, O and side chains.

Pure numpy. Standard bond lengths and angles (Engh and Huber): N-CA 1.458, CA-C 1.525, C-N 1.329, C=O 1.231,
N-CA-C 111.0, CA-C-N 116.2, C-N-CA 121.7, trans peptide bonds. Every function is deterministic; random choices
take a numpy Generator from the caller.
"""
from __future__ import annotations

import numpy as np

DEG = np.pi / 180.0

B_N_CA, B_CA_C, B_C_N, B_C_O = 1.458, 1.525, 1.329, 1.231
A_N_CA_C, A_CA_C_N, A_C_N_CA, A_CA_C_O = 111.0 * DEG, 116.2 * DEG, 121.7 * DEG, 120.8 * DEG

AA1 = "ACDEFGHIKLMNPQRSTVWY"
AA3 = {"A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "F": "PHE", "G": "GLY", "H": "HIS", "I": "ILE", "K": "LYS",
       "L": "LEU", "M": "MET", "N": "ASN", "P": "PRO", "Q": "GLN", "R": "ARG", "S": "SER", "T": "THR", "V": "VAL",
       "W": "TRP", "Y": "TYR"}
ONE = {v: k for k, v in AA3.items()}

# ideal secondary structure torsions (degrees)
HELIX = (-57.0, -47.0)
STRAND = (-120.0, 130.0)


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def nerf(a, b, c, length, angle, torsion):
    """Position of atom d with |c-d| = length, angle(b, c, d) = angle and torsion(a, b, c, d) = torsion.

    a, b, c are (..., 3); length, angle, torsion are scalars or arrays that broadcast against the leading dims."""
    bc = unit(c - b)
    n = unit(np.cross(b - a, bc))
    m = np.cross(n, bc)
    length, angle, torsion = (np.asarray(x, dtype=np.float64) for x in (length, angle, torsion))
    x = (-length * np.cos(angle))[..., None]
    y = (length * np.sin(angle) * np.cos(torsion))[..., None]
    z = (length * np.sin(angle) * np.sin(torsion))[..., None]
    return c + x * bc + y * m + z * n


def canonical_frame() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """N, CA, C of a first residue: N at the origin, CA on +x, C in the xy plane."""
    n0 = np.zeros(3)
    ca0 = np.array([B_N_CA, 0.0, 0.0])
    c0 = ca0 + B_CA_C * np.array([-np.cos(A_N_CA_C), np.sin(A_N_CA_C), 0.0])
    return n0, ca0, c0


def build_frames(phi: np.ndarray, psi: np.ndarray, first=None):
    """N, CA, C arrays (K, n, 3) for K chains of n residues with torsions phi, psi (K, n) in radians.

    phi[:, 0] is not used when the chain starts from the canonical frame; with `first` = (N, CA, C) of residue 0
    (each (K, 3) or (3,)) the chain continues from that frame. psi[:, i] places residue i+1."""
    phi, psi = np.atleast_2d(phi), np.atleast_2d(psi)
    k, n = phi.shape
    N, CA, C = (np.empty((k, n, 3)) for _ in range(3))
    f = canonical_frame() if first is None else first
    N[:, 0], CA[:, 0], C[:, 0] = (np.broadcast_to(np.asarray(x, dtype=np.float64), (k, 3)) for x in f)
    for i in range(1, n):
        N[:, i] = nerf(N[:, i - 1], CA[:, i - 1], C[:, i - 1], B_C_N, A_CA_C_N, psi[:, i - 1])
        CA[:, i] = nerf(CA[:, i - 1], C[:, i - 1], N[:, i], B_N_CA, A_C_N_CA, np.pi)
        C[:, i] = nerf(C[:, i - 1], N[:, i], CA[:, i], B_CA_C, A_N_CA_C, phi[:, i])
    return N, CA, C


def place_o(N, CA, C, psi):
    """Carbonyl O in the peptide plane, trans to the next N (torsion N-CA-C-O = psi + 180)."""
    return nerf(N, CA, C, B_C_O, A_CA_C_O, np.asarray(psi) + np.pi)


def place_cb(N, CA, C):
    """Ideal CB of an L-amino acid from N, CA, C (the AlphaFold / trRosetta construction)."""
    b = CA - N
    c = C - CA
    a = np.cross(b, c)
    return -0.58273431 * a + 0.56802827 * b - 0.54067466 * c + CA


# --------------------------------------------------------------------------------------------- side chains
# name, element, a, b, c, bond length, bond angle (deg, at atom c... i.e. angle b-c-new), torsion (a-b-c-new).
# Torsion strings: "chi1".."chi4" take the residue's chi values; "chiN+120" adds an offset; numbers are degrees.
_SC = {
    "SER": [("OG", "O", "N", "CA", "CB", 1.43, 110.8, "chi1")],
    "CYS": [("SG", "S", "N", "CA", "CB", 1.81, 114.0, "chi1")],
    "VAL": [("CG1", "C", "N", "CA", "CB", 1.52, 110.5, "chi1"), ("CG2", "C", "N", "CA", "CB", 1.52, 110.5, "chi1+120")],
    "THR": [("OG1", "O", "N", "CA", "CB", 1.43, 109.6, "chi1"), ("CG2", "C", "N", "CA", "CB", 1.52, 111.5, "chi1+120")],
    "ILE": [("CG1", "C", "N", "CA", "CB", 1.53, 110.4, "chi1"), ("CG2", "C", "N", "CA", "CB", 1.52, 110.5, "chi1+120"),
            ("CD1", "C", "CA", "CB", "CG1", 1.51, 113.8, "chi2")],
    "LEU": [("CG", "C", "N", "CA", "CB", 1.53, 116.3, "chi1"), ("CD1", "C", "CA", "CB", "CG", 1.52, 110.7, "chi2"),
            ("CD2", "C", "CA", "CB", "CG", 1.52, 110.7, "chi2+120")],
    "ASP": [("CG", "C", "N", "CA", "CB", 1.52, 112.6, "chi1"), ("OD1", "O", "CA", "CB", "CG", 1.25, 118.4, "chi2"),
            ("OD2", "O", "CA", "CB", "CG", 1.25, 118.4, "chi2+180")],
    "ASN": [("CG", "C", "N", "CA", "CB", 1.52, 112.6, "chi1"), ("OD1", "O", "CA", "CB", "CG", 1.23, 120.8, "chi2"),
            ("ND2", "N", "CA", "CB", "CG", 1.33, 116.4, "chi2+180")],
    "GLU": [("CG", "C", "N", "CA", "CB", 1.52, 113.8, "chi1"), ("CD", "C", "CA", "CB", "CG", 1.52, 112.6, "chi2"),
            ("OE1", "O", "CB", "CG", "CD", 1.25, 118.4, "chi3"), ("OE2", "O", "CB", "CG", "CD", 1.25, 118.4, "chi3+180")],
    "GLN": [("CG", "C", "N", "CA", "CB", 1.52, 113.8, "chi1"), ("CD", "C", "CA", "CB", "CG", 1.52, 112.6, "chi2"),
            ("OE1", "O", "CB", "CG", "CD", 1.23, 120.8, "chi3"), ("NE2", "N", "CB", "CG", "CD", 1.33, 116.4, "chi3+180")],
    "MET": [("CG", "C", "N", "CA", "CB", 1.52, 113.8, "chi1"), ("SD", "S", "CA", "CB", "CG", 1.81, 112.7, "chi2"),
            ("CE", "C", "CB", "CG", "SD", 1.79, 100.9, "chi3")],
    "LYS": [("CG", "C", "N", "CA", "CB", 1.52, 113.8, "chi1"), ("CD", "C", "CA", "CB", "CG", 1.52, 111.4, "chi2"),
            ("CE", "C", "CB", "CG", "CD", 1.52, 111.4, "chi3"), ("NZ", "N", "CG", "CD", "CE", 1.49, 111.9, "chi4")],
    "ARG": [("CG", "C", "N", "CA", "CB", 1.52, 113.8, "chi1"), ("CD", "C", "CA", "CB", "CG", 1.52, 111.0, "chi2"),
            ("NE", "N", "CB", "CG", "CD", 1.46, 111.8, "chi3"), ("CZ", "C", "CG", "CD", "NE", 1.33, 124.2, "chi4"),
            ("NH1", "N", "CD", "NE", "CZ", 1.33, 120.0, 180.0), ("NH2", "N", "CD", "NE", "CZ", 1.33, 120.0, 0.0)],
    "HIS": [("CG", "C", "N", "CA", "CB", 1.50, 113.8, "chi1"), ("ND1", "N", "CA", "CB", "CG", 1.38, 122.7, "chi2"),
            ("CD2", "C", "CA", "CB", "CG", 1.36, 131.2, "chi2+180"), ("CE1", "C", "CB", "CG", "ND1", 1.32, 108.5, 180.0),
            ("NE2", "N", "CB", "CG", "CD2", 1.37, 107.0, 180.0)],
    "PHE": [("CG", "C", "N", "CA", "CB", 1.51, 113.8, "chi1"), ("CD1", "C", "CA", "CB", "CG", 1.39, 120.7, "chi2"),
            ("CD2", "C", "CA", "CB", "CG", 1.39, 120.7, "chi2+180"), ("CE1", "C", "CB", "CG", "CD1", 1.39, 120.7, 180.0),
            ("CE2", "C", "CB", "CG", "CD2", 1.39, 120.7, 180.0), ("CZ", "C", "CG", "CD1", "CE1", 1.39, 120.0, 0.0)],
    "TYR": [("CG", "C", "N", "CA", "CB", 1.51, 113.8, "chi1"), ("CD1", "C", "CA", "CB", "CG", 1.39, 120.7, "chi2"),
            ("CD2", "C", "CA", "CB", "CG", 1.39, 120.7, "chi2+180"), ("CE1", "C", "CB", "CG", "CD1", 1.39, 120.7, 180.0),
            ("CE2", "C", "CB", "CG", "CD2", 1.39, 120.7, 180.0), ("CZ", "C", "CG", "CD1", "CE1", 1.39, 120.0, 0.0),
            ("OH", "O", "CD1", "CE1", "CZ", 1.38, 120.0, 180.0)],
    "TRP": [("CG", "C", "N", "CA", "CB", 1.50, 113.8, "chi1"), ("CD1", "C", "CA", "CB", "CG", 1.37, 126.9, "chi2"),
            ("CD2", "C", "CA", "CB", "CG", 1.43, 126.6, "chi2+180"), ("NE1", "N", "CB", "CG", "CD1", 1.38, 110.2, 180.0),
            ("CE2", "C", "CG", "CD1", "NE1", 1.37, 108.9, 0.0), ("CE3", "C", "CB", "CG", "CD2", 1.40, 133.9, 0.0),
            ("CZ2", "C", "CD1", "NE1", "CE2", 1.40, 130.4, 180.0), ("CZ3", "C", "CG", "CD2", "CE3", 1.38, 118.8, 180.0),
            ("CH2", "C", "NE1", "CE2", "CZ2", 1.37, 117.4, 180.0)],
    "PRO": [("CG", "C", "N", "CA", "CB", 1.50, 104.5, 25.0), ("CD", "C", "CA", "CB", "CG", 1.50, 103.2, -20.0)],
}

# common rotamers: (chi1 choices, weights) and default later chis
_CHI1 = np.array([-65.0, 180.0, 60.0])
_CHI1_W = np.array([0.5, 0.35, 0.15])
_CHI_DEFAULT = {"chi2": 180.0, "chi3": 180.0, "chi4": 180.0}
_CHI2_SPECIAL = {"PHE": 90.0, "TYR": 90.0, "HIS": 90.0, "TRP": 90.0, "ASP": 0.0, "ASN": 0.0, "LEU": 175.0, "ILE": 170.0}

SIDE_CHAIN_ATOMS = {aa: ["CB"] + [t[0] for t in _SC.get(AA3[aa], [])] for aa in AA1}
SIDE_CHAIN_ATOMS["G"] = []


def _chi_values(aa3: str, rng: np.random.Generator) -> dict[str, float]:
    chi = dict(_CHI_DEFAULT)
    chi["chi1"] = float(rng.choice(_CHI1, p=_CHI1_W))
    if aa3 in _CHI2_SPECIAL:
        chi["chi2"] = _CHI2_SPECIAL[aa3]
    elif aa3 in ("GLU", "GLN", "MET", "LYS", "ARG"):
        chi["chi2"] = float(rng.choice([180.0, -65.0, 65.0], p=[0.6, 0.3, 0.1]))
    if aa3 in ("GLU", "GLN"):
        chi["chi3"] = 0.0
    return chi


def residue_atoms(aa: str, N, CA, C, O, psi_hint: float, rng: np.random.Generator) -> list[tuple[str, str, np.ndarray]]:
    """Heavy atoms (name, element, xyz) of one residue in PDB order: N CA C O CB then the side chain."""
    out = [("N", "N", N), ("CA", "C", CA), ("C", "C", C), ("O", "O", O)]
    if aa == "G":
        return out
    aa3 = AA3[aa]
    cb = place_cb(N, CA, C)
    pos = {"N": N, "CA": CA, "C": C, "CB": cb}
    out.append(("CB", "C", cb))
    chi = _chi_values(aa3, rng)
    for name, el, a, b, c, bond, ang, tor in _SC.get(aa3, []):
        if isinstance(tor, str):
            base, _, off = tor.partition("+")
            t = chi[base] + (float(off) if off else 0.0)
        else:
            t = tor
        xyz = nerf(pos[a], pos[b], pos[c], bond, ang * DEG, t * DEG)
        pos[name] = xyz
        out.append((name, el, xyz))
    return out


class Atoms:
    """Plain atom table used by the generator (not the library's Atoms): parallel python lists and an array."""

    def __init__(self):
        self.name: list[str] = []
        self.element: list[str] = []
        self.resname: list[str] = []
        self.resnum: list[int] = []
        self.chain: list[str] = []
        self.bfactor: list[float] = []
        self.xyz_list: list[np.ndarray] = []

    @property
    def xyz(self) -> np.ndarray:
        return np.array(self.xyz_list, dtype=np.float64).reshape(-1, 3)

    def __len__(self) -> int:
        return len(self.name)

    def copy(self, xyz=None, chain: str | None = None, bfactor=None, resnum=None) -> "Atoms":
        """Copy with new coordinates, chain id, B-factors or residue numbers (parallel arrays of the same length)."""
        out = Atoms()
        out.name, out.element, out.resname = list(self.name), list(self.element), list(self.resname)
        out.resnum = list(self.resnum) if resnum is None else [int(x) for x in resnum]
        out.chain = list(self.chain) if chain is None else [chain] * len(self.name)
        out.bfactor = list(self.bfactor) if bfactor is None else [float(x) for x in np.broadcast_to(bfactor, (len(self.name),))]
        pts = self.xyz if xyz is None else np.asarray(xyz, dtype=np.float64)
        out.xyz_list = [p for p in pts]
        return out

    def transformed(self, R: np.ndarray, t: np.ndarray, chain: str | None = None) -> "Atoms":
        return self.copy(self.xyz @ np.asarray(R).T + np.asarray(t), chain)

    def select(self, mask) -> "Atoms":
        m = np.asarray(mask, dtype=bool)
        out = Atoms()
        for f in ("name", "element", "resname", "resnum", "chain", "bfactor"):
            setattr(out, f, [v for v, k in zip(getattr(self, f), m) if k])
        out.xyz_list = [p for p, k in zip(self.xyz_list, m) if k]
        return out


def build_chain_atoms(sequence: str, N, CA, C, psi, numbers, chain: str, rng: np.random.Generator,
                      bfactor=0.0, sidechains: bool = True) -> Atoms:
    """Full heavy-atom model of one chain from backbone frames (n, 3) and the sequence."""
    at = Atoms()
    O = place_o(N, CA, C, psi)
    bf = np.broadcast_to(np.asarray(bfactor, dtype=np.float64), (len(sequence),))
    for i, aa in enumerate(sequence):
        if sidechains:
            rows = residue_atoms(aa, N[i], CA[i], C[i], O[i], float(psi[i]), rng)
        else:
            rows = [("N", "N", N[i]), ("CA", "C", CA[i]), ("C", "C", C[i]), ("O", "O", O[i])]
            if aa != "G":
                rows.append(("CB", "C", place_cb(N[i], CA[i], C[i])))
        for name, el, xyz in rows:
            at.name.append(name)
            at.element.append(el)
            at.resname.append(AA3[aa])
            at.resnum.append(int(numbers[i]))
            at.chain.append(chain)
            at.bfactor.append(float(bf[i]))
            at.xyz_list.append(np.asarray(xyz, dtype=np.float64))
    return at


# --------------------------------------------------------------------------------------------- side-chain packing
def _chi_grid(aa3: str) -> list[tuple[float, ...]]:
    """Candidate values per chi angle of a residue type."""
    n = len({t[7].partition("+")[0] for t in _SC.get(aa3, []) if isinstance(t[7], str) and t[7].startswith("chi")})
    if aa3 == "PRO" or n == 0:
        return []
    grid = [(-65.0, 180.0, 60.0)]
    if n >= 2:
        if aa3 in ("PHE", "TYR", "HIS", "TRP"):
            grid.append((90.0, -90.0))
        elif aa3 in ("ASP", "ASN"):
            grid.append((0.0, 60.0, -60.0, 180.0))
        else:
            grid.append((180.0, 65.0, -65.0))
    if n >= 3:
        grid.append((0.0, 180.0, 60.0, -60.0) if aa3 in ("GLU", "GLN") else (180.0, 65.0, -65.0))
    if n >= 4:
        grid.append((180.0, 65.0, -65.0))
    return grid


_GRID_CACHE: dict[str, np.ndarray] = {}


def _chi_combos(aa3: str) -> np.ndarray:
    if aa3 not in _GRID_CACHE:
        grid = _chi_grid(aa3)
        if not grid:
            _GRID_CACHE[aa3] = np.zeros((1, 0))
        else:
            mesh = np.meshgrid(*[np.array(g) for g in grid], indexing="ij")
            _GRID_CACHE[aa3] = np.stack([m.ravel() for m in mesh], axis=1)
    return _GRID_CACHE[aa3]


def side_chain_batch(aa3: str, N, CA, C, chis: np.ndarray) -> tuple[list[str], list[str], np.ndarray]:
    """Side-chain atoms (names, elements, (K, n, 3)) for K sets of chi angles (K, nchi), CB included first."""
    cb = place_cb(N, CA, C)
    k = len(chis)
    names, elems = ["CB"], ["C"]
    pos = {"N": np.broadcast_to(N, (k, 3)), "CA": np.broadcast_to(CA, (k, 3)), "C": np.broadcast_to(C, (k, 3)),
           "CB": np.broadcast_to(cb, (k, 3))}
    cols = [pos["CB"]]
    for name, el, a, b, c, bond, ang, tor in _SC.get(aa3, []):
        if isinstance(tor, str):
            base, _, off = tor.partition("+")
            t = chis[:, int(base[3:]) - 1] + (float(off) if off else 0.0)
        else:
            t = np.full(k, tor)
        xyz = nerf(pos[a], pos[b], pos[c], bond, ang * DEG, t * DEG)
        pos[name] = xyz
        names.append(name)
        elems.append(el)
        cols.append(xyz)
    return names, elems, np.stack(cols, axis=1)


def pack_chain(sequence: str, N, CA, C, psi, numbers, chain: str, rng: np.random.Generator, bfactor=0.0,
               extra_xyz: np.ndarray | None = None, extra_element=None, passes: int = 2,
               prior: float = 0.002, attract: float = 0.0, hbond_reward: float = 0.0) -> Atoms:
    """Full heavy-atom model; side-chain rotamers are picked greedily to avoid overlaps (a poor man's packer).

    extra_xyz (and extra_element): fixed atoms of another molecule that the side chains must avoid, for
    example the target when a binder is built in its docked pose; with attract > 0 a side-chain atom that sits
    3.4 to 4.2 A from one of them is rewarded, which makes the rotamers reach for the target. `rng` is kept for
    symmetry with the other builders; the choice itself is deterministic."""
    from scipy.spatial import cKDTree

    n = len(sequence)
    O = place_o(N, CA, C, psi)
    fx, fres, fback, fpol = [], [], [], []
    for i, aa in enumerate(sequence):
        names_i = ["N", "CA", "C", "O"] + ([] if aa == "G" else ["CB"])
        pos_i = [N[i], CA[i], C[i], O[i]] + ([] if aa == "G" else [place_cb(N[i], CA[i], C[i])])
        for nm, q in zip(names_i, pos_i):
            fx.append(q)
            fres.append(i)
            fback.append(nm != "CB")
            fpol.append(nm in ("N", "O"))
    fx, fres, fback, fpol = np.array(fx), np.array(fres), np.array(fback), np.array(fpol)
    tree = cKDTree(fx)
    ex = np.zeros((0, 3)) if extra_xyz is None else np.asarray(extra_xyz, dtype=np.float64).reshape(-1, 3)
    ex_pol = (np.zeros(len(ex), dtype=bool) if extra_element is None
              else np.array([e in ("N", "O") for e in extra_element], dtype=bool))
    ex_tree = cKDTree(ex) if len(ex) else None
    placed: dict[int, tuple[list, list, np.ndarray]] = {}      # residue -> (names, elements, (na, 3)) of the chosen rotamer
    for _ in range(passes):
        for i, aa in enumerate(sequence):
            if aa in "GA":
                continue
            aa3 = AA3[aa]
            combos = _chi_combos(aa3)
            names, elems, xyz = side_chain_batch(aa3, N[i], CA[i], C[i], combos)
            if aa3 == "PRO" or len(combos) == 1:
                placed[i] = (names, elems, xyz[0])
                continue
            cand = xyz[:, 1:]
            cpol = np.array([e in ("N", "O") for e in elems[1:]], dtype=bool)
            near = [j for j in tree.query_ball_point(CA[i], 11.0)
                    if fres[j] != i and not (abs(fres[j] - i) == 1 and fback[j])]
            pts, pol = [fx[near]], [fpol[near]]
            for q, (_, el, xx) in placed.items():
                if q != i and np.linalg.norm(CA[q] - CA[i]) < 14.0:
                    pts.append(xx[1:])
                    pol.append(np.array([e in ("N", "O") for e in el[1:]], dtype=bool))
            if ex_tree is not None:
                idx = ex_tree.query_ball_point(CA[i], 16.0)
                pts.append(ex[idx])
                pol.append(ex_pol[idx])
            P, Ppol = np.concatenate(pts), np.concatenate(pol)
            if not len(P):
                placed[i] = (names, elems, xyz[0])
                continue
            d = np.linalg.norm(cand[:, :, None, :] - P[None, None, :, :], axis=-1)       # (K, na, M)
            r0 = np.where(cpol[:, None] & Ppol[None, :], 2.6, 3.3)
            pen = (np.clip(r0[None] - d, 0.0, None) ** 2).sum(axis=(1, 2)) + prior * np.arange(len(combos))
            if (attract or hbond_reward) and ex_tree is not None:
                n_ex = len(idx)
                dex = d[:, :, -n_ex:] if n_ex else d[:, :, :0]
                pen = pen - attract * ((dex > 3.3) & (dex < 4.2)).any(axis=2).sum(axis=1)
                if hbond_reward and n_ex:
                    pol_pair = cpol[:, None] & ex_pol[idx][None, :]                        # polar side-chain atom, polar target atom
                    pen = pen - hbond_reward * (((dex > 2.6) & (dex < 3.4)) & pol_pair[None]).any(axis=2).sum(axis=1)
            placed[i] = (names, elems, xyz[int(np.argmin(pen))])
    bf = np.broadcast_to(np.asarray(bfactor, dtype=np.float64), (n,))
    at = Atoms()
    for i, aa in enumerate(sequence):
        rows = [("N", "N", N[i]), ("CA", "C", CA[i]), ("C", "C", C[i]), ("O", "O", O[i])]
        if aa != "G":
            names, elems, xx = placed.get(i, (["CB"], ["C"], np.array([place_cb(N[i], CA[i], C[i])])))
            rows += [(nm, el, xx[j]) for j, (nm, el) in enumerate(zip(names, elems))]
        for name, el, q in rows:
            at.name.append(name)
            at.element.append(el)
            at.resname.append(AA3[aa])
            at.resnum.append(int(numbers[i]))
            at.chain.append(chain)
            at.bfactor.append(float(bf[i]))
            at.xyz_list.append(np.asarray(q, dtype=np.float64))
    return at


def helix_axis(ca: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Anchor and direction of a helix from its CA atoms (K, m, 3): windows of four CA lie on the axis."""
    p = (ca[:, :-3] + ca[:, 1:-2] + ca[:, 2:-1] + ca[:, 3:]) / 4.0
    return p.mean(1), unit(p[:, -1] - p[:, 0])


def perp_distance(a1, d1, a2) -> np.ndarray:
    """Distance of the point a2 from the line through a1 with direction d1."""
    v = a2 - a1
    return np.linalg.norm(v - (v * d1).sum(-1, keepdims=True) * d1, axis=-1)


def rotation_matrix(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = unit(np.asarray(axis, dtype=np.float64))
    x, y, z = axis
    c, s = np.cos(angle), np.sin(angle)
    C = 1 - c
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def random_rotation(rng: np.random.Generator) -> np.ndarray:
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def kabsch(P: np.ndarray, Q: np.ndarray):
    """R, t minimising |P R^T + t - Q| (proper rotation)."""
    pc, qc = P.mean(0), Q.mean(0)
    H = (P - pc).T @ (Q - qc)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T
    return R, qc - pc @ R.T
