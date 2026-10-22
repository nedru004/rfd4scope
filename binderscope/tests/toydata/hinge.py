"""A reference structure in which one domain is turned against the rest, for the placement tests.

A BindCraft model of the target is never the crystal chain: parts of it hinge. When the part that moved is the one the binder sits on, a fit
over all shared residues splits the difference and leaves the epitope off by a few angstrom, while a fit weighted around the binder keeps it in
place. `hinge_reference` makes that situation from the toy reference and one design: the residues within `radius` of the design's binder form
the domain, which is turned by `angle` degrees about an axis through the residue farthest from the binder, so the rest of the chain, the
construct's other residues, stays where the design has it.
"""
from __future__ import annotations

from pathlib import Path

import gemmi
import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation


def hinge_reference(reference: Path, binder_xyz: np.ndarray, out: Path, *, chain: str = "A", radius: float = 18.0, angle: float = 12.0,
                    swap_chains: bool = False) -> dict:
    """Write a copy of `reference` (mmCIF or PDB) whose chain `chain` has the residues within `radius` A (CA to the nearest binder atom) turned by
    `angle` degrees. `binder_xyz` is the binder in the reference frame (the design placed by an unmodified frame). With `swap_chains` the chain
    ids A and B are exchanged in the copy, so that the target is called B. Returns what was done: the numbers of the turned residues, the pivot, the
    axis and the new name of the target chain."""
    st = gemmi.read_structure(str(reference))
    st.setup_entities()
    ch = st[0][chain]
    ca = {r.seqid.num: np.array(r["CA"][0].pos.tolist()) for r in ch if r.find_atom("CA", "*")}
    nums = np.array(sorted(ca))
    xyz = np.array([ca[n] for n in nums])
    dist = cKDTree(np.asarray(binder_xyz, dtype=float)).query(xyz)[0]
    inside = dist < radius
    pivot = xyz[int(np.argmax(dist))]                                              # a residue far from the binder: the hinge lies on the far side
    toward = np.asarray(binder_xyz, dtype=float).mean(axis=0) - pivot
    toward /= np.linalg.norm(toward)
    helper = np.array([0.0, 0.0, 1.0]) if abs(toward[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    axis = np.cross(toward, helper)
    axis /= np.linalg.norm(axis)
    rot = Rotation.from_rotvec(np.radians(angle) * axis).as_matrix()
    turned = {int(n) for n in nums[inside]}
    for res in ch:
        if res.seqid.num not in turned:
            continue
        for atom in res:
            p = (np.array(atom.pos.tolist()) - pivot) @ rot.T + pivot
            atom.pos = gemmi.Position(float(p[0]), float(p[1]), float(p[2]))
    target = chain
    if swap_chains:                                                                # the target becomes the second chain, its partner the first
        other = "B" if chain == "A" else "A"
        assert {c.name for c in st[0]} == {"A", "B"}, "swapping needs a homodimer of chains A and B"
        was = {chain: other, other: chain}                                         # old chain id -> new chain id
        model = gemmi.Model("1")
        for new_id in ("A", "B"):
            old_id = next(k for k, v in was.items() if v == new_id)
            moved = gemmi.Chain(new_id)
            for res in st[0][old_id]:
                moved.add_residue(res)
            model.add_chain(moved)
        swapped = gemmi.Structure()
        swapped.name = st.name
        swapped.add_model(model)
        st, target = swapped, other
    st.setup_entities()
    st.assign_label_seq_id()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    st.make_mmcif_document().write_file(str(out))
    return {"turned": sorted(turned), "n_turned": len(turned), "n_chain": len(nums), "pivot": pivot.tolist(), "axis": axis.tolist(), "angle": angle,
            "radius": radius, "chain": target}
