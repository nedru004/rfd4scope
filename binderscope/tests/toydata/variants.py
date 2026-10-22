"""Variants of the toy reference: a second chain that is not the target (for the tests of the import page), a domain that is turned so that the structure given to
BindCraft fits it only loosely, a residue number that two residues carry and a sugar that carries the chain id of the protein.

The toy reference is a homodimer, so the construct fits chain B as well as chain A (the two-fold turns one into the other). To see what the page
says about a chain that is not the target, one chain of a copy is made something else: the same coordinates with another sequence, or a short
piece of the chain.
"""
from __future__ import annotations

import copy
import random
from pathlib import Path

import gemmi
import numpy as np

from .hinge import hinge_reference


def _write(st: gemmi.Structure, out: Path) -> Path:
    st.setup_entities()
    st.assign_label_seq_id()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    st.make_mmcif_document().write_file(str(out))
    return out


def scrambled_chain_b(reference: Path, out: Path, seed: int = 1) -> Path:
    """The reference with the residue types of chain B shuffled: the same coordinates, another sequence. Chain A stays the target."""
    st = gemmi.read_structure(str(reference))
    st.setup_entities()
    chain = st[0]["B"]
    names = [r.name for r in chain]
    random.Random(seed).shuffle(names)
    for res, name in zip(chain, names):
        res.name = name
    return _write(st, out)


def short_chain_b(reference: Path, out: Path, first: int = 140) -> Path:
    """The reference with chain B cut to its residues from `first` on: only the end of the construct overlaps it by number."""
    st = gemmi.read_structure(str(reference))
    st.setup_entities()
    chain = st[0]["B"]
    for i in reversed(range(len(chain))):
        if chain[i].seqid.num < first:
            del chain[i]
    return _write(st, out)


def loosely_fitting_reference(reference: Path, out: Path, angle: float = 20.0, chain: str = "A") -> Path:
    """The reference with the domain around residues 100 to 110 of `chain` turned by `angle` degrees (toydata.hinge, about a stand-in binder 4 A beside those residues):
    the structure given to BindCraft, the toy construct, fits it only loosely, with a CA RMSD of about 5 A over all of its residues at 20 degrees."""
    st = gemmi.read_structure(str(reference))
    st.setup_entities()
    ca = np.array([r["CA"][0].pos.tolist() for r in st[0][chain] if r.find_atom("CA", "*") and 100 <= r.seqid.num <= 110])
    hinge_reference(reference, ca + np.array([4.0, 0.0, 0.0]), out, angle=angle, chain=chain)
    return Path(out)


def twinned_residue(reference: Path, out: Path, number: int = 150, shift: float = 20.0, chain: str = "A") -> Path:
    """The reference with a second residue that carries the same number: a copy of residue `number` of `chain`, with the insertion code A, moved by `shift` A
    along x and placed right behind the original (150 and 150A, as in a crystal structure with an insertion). Designs are matched to the reference by residue
    number, so a placement that paired a design residue with the wrong one of the two would be off by `shift` at that residue."""
    st = gemmi.read_structure(str(reference))
    st.setup_entities()
    ch = st[0][chain]
    at = next(i for i, res in enumerate(ch) if res.seqid.num == number)
    twin = copy.deepcopy(ch[at])
    twin.seqid = gemmi.SeqId(number, "A")
    for atom in twin:
        atom.pos = gemmi.Position(atom.pos.x + shift, atom.pos.y, atom.pos.z)
    ch.add_residue(twin, at + 1)
    return _write(st, out)


#: Atom names, elements and rough positions (A, relative to C1) of an N-acetylglucosamine: enough for a sugar that the frame can follow and draw, not a refined one.
_NAG = [("C1", "C", 0, 0, 0), ("C2", "C", 1.5, 0, 0), ("C3", "C", 2.2, 1.3, 0), ("C4", "C", 1.5, 2.5, 0), ("C5", "C", 0, 2.5, 0), ("C6", "C", -0.7, 3.8, 0),
        ("C7", "C", 2.2, -1.3, 0.5), ("C8", "C", 3.6, -1.3, 0.5), ("N2", "N", 1.5, -1.3, 0.3), ("O3", "O", 3.6, 1.3, 0), ("O4", "O", 2.2, 3.7, 0),
        ("O5", "O", -0.7, 1.3, 0), ("O6", "O", -2.0, 3.8, 0), ("O7", "O", 1.5, -2.3, 0.9)]


def glycosylated_in_chain(reference: Path, out: Path, chain: str = "A", first: int = 70, last: int = 160, number: int = 901) -> tuple[Path, int]:
    """The reference with an N-acetylglucosamine on the first asparagine of `chain` between the residue numbers `first` and `last`, written as newer entries do:
    a branched entity whose residue carries the chain id of the protein (here `chain`, number `number`), with the covalent link ND2 to C1. Returns the file and the
    number of the asparagine. A reader that takes every chain id for one kind of chain sees a sugar chain where the protein is."""
    st = gemmi.read_structure(str(reference))
    st.setup_entities()
    ch = st[0][chain]
    asn = next(r for r in ch if r.name == "ASN" and first <= r.seqid.num <= last and r.find_atom("ND2", "*"))
    nd2, cg = np.array(asn["ND2"][0].pos.tolist()), np.array(asn["CG"][0].pos.tolist())
    c1 = nd2 + 1.45 * (nd2 - cg) / np.linalg.norm(nd2 - cg)                                  # C1 lies 1.45 A from ND2, away from the side chain
    nag = gemmi.Residue()
    nag.name, nag.seqid, nag.het_flag = "NAG", gemmi.SeqId(number, " "), "H"
    for name, element, x, y, z in _NAG:
        atom = gemmi.Atom()
        atom.name, atom.element, atom.occ, atom.b_iso = name, gemmi.Element(element), 1.0, 30.0
        atom.pos = gemmi.Position(float(c1[0] + x), float(c1[1] + y), float(c1[2] + z))
        nag.add_atom(atom)
    ch.add_residue(nag)
    st.setup_entities()
    for entity in st.entities:                                                               # the sugar is a branched entity, as in the entries of the PDB
        if entity.entity_type == gemmi.EntityType.NonPolymer:
            entity.entity_type = gemmi.EntityType.Branched

    def address(res: gemmi.Residue, atom: str) -> gemmi.AtomAddress:
        a = gemmi.AtomAddress()
        a.chain_name, a.atom_name, a.res_id = chain, atom, gemmi.ResidueId()
        a.res_id.seqid, a.res_id.name = res.seqid, res.name
        return a

    link = gemmi.Connection()
    link.name, link.type, link.reported_distance = "covale1", gemmi.ConnectionType.Covale, 1.45
    link.partner1, link.partner2 = address(asn, "ND2"), address(nag, "C1")
    st.connections.append(link)
    return _write(st, out), int(asn.seqid.num)
