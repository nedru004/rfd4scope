"""Writing structures with gemmi and a few light geometry helpers (no dependency on the binder_review package)."""
from __future__ import annotations

from pathlib import Path

import gemmi
import numpy as np
from scipy.spatial import cKDTree

from . import geometry as G

HYDROPHOBIC = frozenset("ACFILMPVWY")                  # BindCraft's set for Interface_Hydrophobicity


def gemmi_structure(groups: list[G.Atoms], name: str = "toy") -> gemmi.Structure:
    """One model; every group becomes one chain (chain id taken from the group's first atom)."""
    st = gemmi.Structure()
    st.name = name
    model = gemmi.Model("1")
    for at in groups:
        if not len(at):
            continue
        chain = gemmi.Chain(at.chain[0])
        last_key = None
        res = None
        xyz = at.xyz
        for i in range(len(at)):
            key = (at.resnum[i], at.resname[i])
            if key != last_key:
                res = gemmi.Residue()
                res.name = at.resname[i]
                res.seqid = gemmi.SeqId(int(at.resnum[i]), " ")
                res.het_flag = "A"
                chain.add_residue(res)
                res = chain[len(chain) - 1]
                last_key = key
            a = gemmi.Atom()
            a.name = at.name[i]
            a.element = gemmi.Element(at.element[i])
            a.pos = gemmi.Position(float(xyz[i, 0]), float(xyz[i, 1]), float(xyz[i, 2]))
            a.occ = 1.0
            a.b_iso = float(at.bfactor[i])
            res.add_atom(a)
        model.add_chain(chain)
    st.add_model(model)
    st.setup_entities()
    return st


def write_pdb(path: Path, groups: list[G.Atoms], name: str = "toy") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    st = gemmi_structure(groups, name)
    st.write_pdb(str(path))


def write_cif(path: Path, groups: list[G.Atoms], name: str = "toy", extra_pairs: dict | None = None,
              helices: list[tuple[str, int, int]] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    st = gemmi_structure(groups, name)
    st.assign_label_seq_id()
    doc = st.make_mmcif_document()
    block = doc.sole_block()
    for k, v in (extra_pairs or {}).items():
        block.set_pair(k, v)
    doc.write_file(str(path))


def interface_residues(binder: G.Atoms, target_xyz: np.ndarray, cutoff: float = 4.0) -> list[int]:
    """Binder residue numbers with a heavy atom within `cutoff` of a target atom."""
    tree = cKDTree(target_xyz)
    d, _ = tree.query(binder.xyz)
    res = np.array(binder.resnum)
    return sorted(set(int(x) for x in res[d < cutoff]))


def residue_letters(binder: G.Atoms) -> dict[int, str]:
    out: dict[int, str] = {}
    for n, rn in zip(binder.resnum, binder.resname):
        out[int(n)] = G.ONE[rn]
    return out


def sequence_of(at: G.Atoms) -> str:
    return "".join(G.ONE[rn] for rn in _unique_in_order(zip(at.resnum, at.resname)))


def _unique_in_order(pairs):
    last = None
    for p in pairs:
        if p != last:
            yield p[1]
            last = p


def interface_aas(letters: dict[int, str], residues: list[int]) -> dict[str, int]:
    out = {a: 0 for a in G.AA1}
    for r in residues:
        out[letters[r]] += 1
    return out


def sasa_total(groups: list[G.Atoms]) -> tuple[float, np.ndarray]:
    """Shrake-Rupley SASA with Biopython (an implementation independent of binder_review): total and per atom."""
    from Bio.PDB.Atom import Atom
    from Bio.PDB.Chain import Chain
    from Bio.PDB.Model import Model
    from Bio.PDB.Residue import Residue
    from Bio.PDB.SASA import ShrakeRupley
    from Bio.PDB.Structure import Structure

    st = Structure("x")
    m = Model(0)
    st.add(m)
    atoms = []
    serial = 0
    for gi, at in enumerate(groups):
        c = Chain(chr(ord("A") + gi))
        m.add(c)
        xyz = at.xyz
        last = None
        r = None
        for i in range(len(at)):
            key = (at.resnum[i], at.resname[i])
            if key != last:
                r = Residue((" ", int(at.resnum[i]), " "), at.resname[i], " ")
                c.add(r)
                last = key
            serial += 1
            a = Atom(at.name[i], xyz[i], 0.0, 1.0, " ", f" {at.name[i]:<3}", serial, element=at.element[i])
            r.add(a)
            atoms.append(a)
    ShrakeRupley(n_points=100).compute(st, level="A")
    per = np.array([a.sasa for a in atoms])
    return float(per.sum()), per
