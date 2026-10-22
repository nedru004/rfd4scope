"""Structure utilities (binder_review.structure) on synthetic geometry and on the toy designs: loading, geometry,
contacts, SASA, secondary structure, hydrogen bonds, TM-align and sequence helpers."""
from __future__ import annotations

import glob
import re
import time
from pathlib import Path

import gemmi
import numpy as np
import pytest

from binder_review import structure as S
from toydata import geometry as G


# ------------------------------------------------------------------------------------------------ builders
def ideal_backbone(n_res: int, phi: float, psi: float, number0: int = 1, chain: str = "A") -> S.Atoms:
    """Poly-alanine backbone (N, CA, C, O) with constant torsions (degrees), built with the toy NeRF kit."""
    N, CA, C = G.build_frames(np.full((1, n_res), phi * G.DEG), np.full((1, n_res), psi * G.DEG))
    O = G.place_o(N[0], CA[0], C[0], np.full(n_res, psi * G.DEG))
    xyz = np.stack([N[0], CA[0], C[0], O], axis=1).reshape(-1, 3)
    names = ["N", "CA", "C", "O"] * n_res
    elems = ["N", "C", "C", "O"] * n_res
    return S.Atoms(xyz, names, elems, ["ALA"] * len(names), np.repeat(np.arange(number0, number0 + n_res), 4),
                   [chain] * len(names), np.full(len(names), 90.0))


def random_rotation(rng):
    return G.random_rotation(rng)


def make_atoms(rows):
    """rows: (x, y, z, name, element, resname, resnum, chain)."""
    xyz = np.array([r[:3] for r in rows], dtype=float)
    return S.Atoms(xyz, [r[3] for r in rows], [r[4] for r in rows], [r[5] for r in rows], [r[6] for r in rows], [r[7] for r in rows])


def design_files(run_dir: Path, folder: str = "*") -> list[str]:
    return sorted(glob.glob(str(run_dir / folder / "*_model*.pdb")))


@pytest.fixture(scope="module")
def run1_files(toy_dir):
    return sorted(glob.glob(str(toy_dir / "runs" / "run01" / "Accepted" / "*_model*.pdb")))


# ------------------------------------------------------------------------------------------------ geometry
def test_kabsch_recovers_random_rotation_exactly():
    rng = np.random.default_rng(1)
    P = rng.normal(size=(40, 3)) * 8
    R0, t0 = random_rotation(rng), rng.normal(size=3) * 20
    Q = P @ R0.T + t0
    R, t, r = S.kabsch(P, Q)
    assert np.allclose(R, R0, atol=1e-10) and np.allclose(t, t0, atol=1e-9) and r < 1e-9 and np.isclose(np.linalg.det(R), 1.0)
    assert np.allclose(S.apply_transform(P, R, t), Q, atol=1e-9)
    Ri, ti = S.invert_transform(R, t)
    assert np.allclose(S.apply_transform(Q, Ri, ti), P, atol=1e-9)


def test_kabsch_never_returns_a_reflection_and_reports_rmsd():
    rng = np.random.default_rng(2)
    P = rng.normal(size=(30, 3))
    Q = P * np.array([1, 1, -1]) + 0.05 * rng.normal(size=P.shape)       # a mirror image: the best proper rotation is imperfect
    R, t, r = S.kabsch(P, Q)
    assert np.isclose(np.linalg.det(R), 1.0) and r > 0.3 and np.isclose(r, S.rmsd(S.apply_transform(P, R, t), Q))
    with pytest.raises(ValueError):
        S.kabsch(P[:2], Q[:2])
    with pytest.raises(ValueError):
        S.kabsch(P, Q[:5])


def test_kabsch_weights():
    rng = np.random.default_rng(3)
    P = rng.normal(size=(20, 3))
    R0 = random_rotation(rng)
    Q = P @ R0.T
    Q[0] += 50.0                                                          # one wild outlier
    w = np.ones(20)
    w[0] = 1e-9
    R, _, _ = S.kabsch(P, Q, weights=w)
    assert np.allclose(R, R0, atol=1e-4)


def test_superpose_residues_by_number_with_gaps_and_mapping():
    bb = ideal_backbone(30, -57, -47)
    rng = np.random.default_rng(4)
    R0, t0 = random_rotation(rng), rng.normal(size=3) * 10
    moved = bb.transformed(R0, t0)
    keep = ~np.isin(moved.resnum, np.arange(10, 15))                      # drop residues 10 to 14: shared residues still match by number
    sp = S.superpose_residues(moved.select(keep), bb)
    assert sp.n == 25 and sp.rmsd < 1e-8 and not set(range(10, 15)) & set(sp.numbers)
    assert np.allclose(sp.apply(moved.xyz), bb.xyz, atol=1e-8)
    assert S.superpose_residues(moved, bb, numbers=range(1, 8)).n == 7
    shifted = moved.copy()
    shifted.resnum = shifted.resnum + 100
    shifted._res_index = shifted._residues = None
    sp3 = S.superpose_residues(shifted, bb, mapping={n + 100: n for n in range(1, 31)})
    assert sp3.n == 30 and sp3.rmsd < 1e-8
    assert S.rmsd_by_number(moved, bb) < 1e-8 and S.rmsd_by_number(moved, bb, superpose=False) > 1.0


def test_residue_table_keys_on_numbers_not_positions():
    a = ideal_backbone(10, -57, -47)
    b = a.select(~np.isin(a.resnum, [4, 5, 6]))
    r = b.residues
    assert len(r) == 7 and list(r.number) == [1, 2, 3, 7, 8, 9, 10]
    assert r.gaps() == [(3, 7)] and r.missing_numbers() == [4, 5, 6]
    assert r.index_of(7) == 3 and not r.has(5)
    with pytest.raises(KeyError):
        r.index_of(5)
    assert list(r.indices_of([1, 5, 10])) == [0, 6]
    assert b.atom(7, "CA") == int(r.ca[3]) and b.sequence == "A" * 7
    assert list(b.select_residues(numbers=[7, 8]).residues.number) == [7, 8]
    assert np.allclose(b.per_residue(np.ones(len(b))), 4.0)


# ------------------------------------------------------------------------------------------------ loading
def test_toy_construct_loads_as_one_chain(toy_construct):
    cx = S.load_complex(str(toy_construct), "A", None)
    assert cx.target.n_residues == 110 and list(cx.target.residues.number) == list(range(1, 111))
    assert cx.binder.n_residues == 0 and not cx.errors and not cx.has_hydrogens


def test_toy_design_layout(run1_files, toy_construct):
    ref = S.load_complex(str(toy_construct), "A", None).target
    first = None
    for p in run1_files:
        cx = S.load_complex(p)
        assert cx.target.n_residues == 110 and set(cx.target.chain) == {"A"} and set(cx.binder.chain) == {"B"}
        assert list(cx.binder.residues.number) == list(range(1, cx.binder.n_residues + 1)) and 60 <= cx.binder.n_residues <= 100
        assert not cx.atoms.is_hydrogen.any() and not cx.has_hydrogens
        assert cx.target.sequence == ref.sequence                          # the target is the construct in every design
        for pl in (cx.target_plddt, cx.binder_plddt):
            assert np.nanmin(pl) >= 0 and np.nanmax(pl) <= 100 and np.nanmax(pl) > 1.0
        nums = tuple(cx.target.residues.number)
        first = first or nums
        assert nums == first                                               # identical numbering across the designs of a run


def test_run2_target_has_two_gaps(toy_dir, toy_truth):
    p = sorted(glob.glob(str(toy_dir / "runs" / "run02" / "accepted" / "*_model*.pdb")))[0]
    cx = S.load_complex(p)
    gaps = toy_truth["deleted_construct"]
    nums = set(int(n) for n in cx.target.residues.number)
    assert cx.target.n_residues == 102 and not nums & {n for a, b in gaps for n in range(a, b + 1)}
    assert cx.target.residues.gaps() == [(a - 1, b + 1) for a, b in gaps]
    assert any(i.code == "numbering_gaps" for i in cx.issues)


def test_reference_groups_dimer(toy_reference):
    sf = S.read_structure(str(toy_reference))
    assert sf.polymer_chains() == ["A", "B"] and sf.glycan_groups() == []
    for c in "AB":
        r = sf.groups[c].residues
        assert len(r) == 250 and int(r.number.min()) == 1 and int(r.number.max()) == 250 and r.gaps() == []
    assert sf.groups["A"].sequence == sf.groups["B"].sequence
    assert sf.find_atom("A", 5, "CA") is not None and sf.find_atom("A", 999, "CA") is None


def test_missing_chain_error_and_non_strict(tmp_path):
    a = ideal_backbone(5, -57, -47)
    p = tmp_path / "x.pdb"
    S.write_pdb(str(p), a)
    with pytest.raises(S.StructureError, match="chain 'B'"):
        S.load_complex(str(p))
    cx = S.load_complex(str(p), strict=False)
    assert cx.errors and cx.errors[0].code == "missing_chain" and cx.binder.n_residues == 0 and cx.target.n_residues == 5
    with pytest.raises(S.StructureError):
        S.load_complex(str(tmp_path / "nope.pdb"))


def test_hetatm_and_water_dropped_with_warning(tmp_path):
    a = ideal_backbone(4, -57, -47)
    lines = S.to_pdb_lines(a)
    het = "HETATM%5d  O   HOH A 101       1.000   2.000   3.000  1.00 30.00           O  " % 999
    lig = "HETATM%5d  C1  NAG A 102       5.000   2.000   3.000  1.00 30.00           C  " % 1000
    b = ideal_backbone(3, -57, -47, chain="B")
    p = tmp_path / "h.pdb"
    p.write_text("\n".join(lines + [het, lig, "TER"] + S.to_pdb_lines(b, 100) + ["TER", "END"]) + "\n")
    cx = S.load_complex(str(p))
    assert cx.target.n_residues == 4
    warn = [i for i in cx.issues if i.code == "hetatm"]
    assert warn and warn[0].severity == "warning"
    kept = S.load_complex(str(p), keep_hetero=True)
    assert kept.target.n_residues == 5 and kept.target.hetero.any()                # NAG kept as HETATM, water still dropped


def test_altloc_first_conformer_and_modified_residue(tmp_path):
    lines = [
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 10.00           N  ",
        "ATOM      2  CA AALA A   1       1.458   0.000   0.000  0.60 10.00           C  ",
        "ATOM      3  CA BALA A   1       9.000   9.000   9.000  0.40 10.00           C  ",
        "ATOM      4  C   ALA A   1       2.000   1.400   0.000  1.00 10.00           C  ",
        "HETATM    5  N   MSE A   2       3.000   1.500   0.000  1.00 10.00           N  ",
        "HETATM    6  CA  MSE A   2       4.000   2.500   0.000  1.00 10.00           C  ",
        "HETATM    7 SE   MSE A   2       5.000   3.500   1.000  1.00 10.00          SE  ",
        "END",
    ]
    p = tmp_path / "alt.pdb"
    p.write_text("\n".join(lines) + "\n")
    t = S.load_complex(str(p), binder_chain=None).target
    assert (t.name == "CA").sum() == 2 and np.allclose(t.xyz[t.atom(1, "CA")], [1.458, 0, 0]) and t.sequence == "AM"
    assert t.resname[-1] == "MET" and t.name[-1] == "SD" and t.element[-1] == "S"


def test_hydrogens_are_stripped_or_kept_on_request(tmp_path):
    tgt = make_atoms([(0, 0, 0, "CA", "C", "ALA", 1, "A"), (0.5, 0, 0, "H", "H", "ALA", 1, "A")])
    bnd = make_atoms([(0, 4.3, 0, "CA", "C", "ALA", 1, "B"), (0, 3.8, 0, "H", "H", "ALA", 1, "B"), (0, 30, 0, "CA", "C", "ALA", 2, "B")])
    p = tmp_path / "s.pdb"
    S.write_pdb(str(p), tgt, bnd)
    stripped, kept = S.load_complex(str(p)), S.load_complex(str(p), strip_h=False)
    assert not stripped.atoms.is_hydrogen.any() and kept.atoms.is_hydrogen.any() and stripped.has_hydrogens
    assert len(stripped.hydrogens) == len(kept.hydrogens) == 2
    assert S.bindcraft_interface_residues(stripped, include_hydrogens=True) == [1]           # the H pair is 3.8 A
    assert S.bindcraft_interface_residues(stripped, include_hydrogens=False) == []           # heavy atoms are 4.3 A apart
    assert S.bindcraft_interface_residues(kept, include_hydrogens=True) == [1]
    assert S.format_bindcraft_interface([5, 1, 12]) == "B1,B5,B12"


def test_mmcif_roundtrip_matches_pdb(run1_files, tmp_path):
    p = run1_files[0]
    st = gemmi.read_structure(p)
    st.setup_entities()
    cif = tmp_path / "x.cif"
    st.make_mmcif_document().write_file(str(cif))
    a, b = S.load_complex(p), S.load_complex(str(cif))
    assert b.target.sequence == a.target.sequence and b.binder.sequence == a.binder.sequence
    assert np.allclose(b.binder.xyz, a.binder.xyz, atol=1e-3) and np.allclose(b.binder_plddt, a.binder_plddt, atol=0.01)


def test_bindcraft2_cif_and_bindcraft1_pdb_of_the_same_design_agree(toy_dir, toy_bc2_a):
    d = toy_bc2_a.accepted()[0]
    cx = S.load_complex(d.structure_path, "A", "B")
    assert cx.target.n_residues == 110 and list(cx.binder.residues.number)[0] == 0         # zero-based binder numbering
    assert np.nanmax(cx.binder_plddt) <= 100 and not cx.has_hydrogens


def test_write_pdb_round_trips(tmp_path):
    a, b = ideal_backbone(6, -57, -47), ideal_backbone(4, -57, -47, chain="B")
    p = tmp_path / "w.pdb"
    S.write_pdb(str(p), a, b)
    cx = S.load_complex(str(p))
    assert cx.target.n_residues == 6 and cx.binder.n_residues == 4 and np.allclose(cx.target.xyz, a.xyz, atol=1e-3)


# ------------------------------------------------------------------------------------------------ contacts
def test_residue_contacts_synthetic():
    a = make_atoms([(0, 0, 0, "CA", "C", "ALA", 5, "A"), (0, 0, 0.1, "CB", "C", "ALA", 5, "A"), (10, 0, 0, "CA", "C", "GLY", 9, "A")])
    b = make_atoms([(0, 3.0, 0, "CA", "C", "SER", 1, "B"), (0, 3.5, 0, "CB", "C", "SER", 1, "B"),
                    (10, 4.5, 0, "CA", "C", "SER", 2, "B"), (0, 0, 20, "CA", "C", "SER", 3, "B")])
    c = S.residue_contacts(a, b, 4.0)
    assert c.pairs() == [(5, 1)] and c.n_atom_contacts == 4 and np.isclose(c.min_dist[0], 3.0, atol=0.01)
    assert c.a_residues == [5] and c.b_residues == [1]
    assert S.residue_contacts(a, b, 5.0).pairs() == [(5, 1), (9, 2)] and len(S.residue_contacts(a, b, 1.0)) == 0
    assert c.as_records()[0]["a"] == 5


def test_contacts_heavy_only_flag():
    a = make_atoms([(0, 0, 0, "CA", "C", "ALA", 1, "A")])
    b = make_atoms([(0, 3.0, 0, "H", "H", "ALA", 1, "B"), (0, 8.0, 0, "CA", "C", "ALA", 1, "B")])
    assert len(S.residue_contacts(a, b, 4.0, heavy_only=True)) == 0 and len(S.residue_contacts(a, b, 4.0, heavy_only=False)) == 1


def test_toy_interface_residues_equal_the_csv_for_model2_files(toy_run1):
    """The CSV InterfaceResidues column is model 2's interface by the BindCraft rule (4 A); a file that is model 2 reproduces it,
    a file that is model 1 only resembles it."""
    checked, jac = 0, []
    for d in toy_run1.designs:
        if not d.structure_path:
            continue
        mine = set(S.bindcraft_interface_residues(S.load_complex(d.structure_path)))
        ref = {int(x[1:]) for x in d.interface_residues}
        if d.structure_model == 2:
            checked += 1
            assert mine == ref, d.id
        else:
            jac.append(len(mine & ref) / len(mine | ref))
    assert checked >= 2 and jac and np.mean(jac) > 0.5


def test_toy_contacts_are_at_the_interface_and_symmetric(run1_files):
    cx = S.load_complex(run1_files[0])
    c = S.residue_contacts(cx.target, cx.binder, 4.5)
    assert len(c) > 0 and set(c.a_residues) <= set(range(1, 111)) and set(c.b_residues) <= set(range(1, cx.binder.n_residues + 1))
    assert c.n_atom_contacts >= len(c) and min(c.min_dist) < 4.5
    rev = S.residue_contacts(cx.binder, cx.target, 4.5)
    assert sorted(rev.a_residues) == sorted(c.b_residues) and sorted(rev.b_residues) == sorted(c.a_residues)


# ------------------------------------------------------------------------------------------------ SASA
def test_sasa_single_atom_and_buried_atom():
    r = S.VDW_RADII["C"] + 1.4
    assert np.isclose(S.sasa_atoms(np.zeros((1, 3)), ["C"], n_points=500)[0], 4 * np.pi * r * r)
    two = S.sasa_atoms(np.array([[0, 0, 0], [1.5, 0, 0]]), ["C", "C"], n_points=960)
    assert two.sum() < 2 * 4 * np.pi * r * r
    ring = [(0, 0, 0)] + [tuple(2.2 * v) for v in S._unit_sphere(40)]
    xyz = np.array(ring)
    assert S.sasa_atoms(xyz, ["C"] * len(xyz), n_points=200)[0] == 0.0                # an atom inside a shell of neighbours is buried
    big = S.sasa_atoms(np.zeros((1, 3)), ["C"], radii=np.array([3.0]), probe=0.0, n_points="fast")
    assert np.isclose(big[0], 4 * np.pi * 9) and len(S.sasa_atoms(np.zeros((0, 3)), [])) == 0


def test_sasa_is_rotation_invariant():
    rng = np.random.default_rng(7)
    xyz = rng.normal(size=(60, 3)) * 3.0
    el = rng.choice(["C", "N", "O", "S"], 60)
    a = S.sasa_atoms(xyz, el, n_points=960)
    b = S.sasa_atoms(xyz @ random_rotation(rng).T + 5.0, el, n_points=960)
    assert abs(a.sum() - b.sum()) / a.sum() < 0.01


def test_relative_sasa_table_and_residue_sums():
    assert set(S.MAX_ASA) == set("ACDEFGHIKLMNPQRSTVWY")
    rel = S.relative_sasa([129.0, 52.0], ["A", "W"])
    assert np.isclose(rel[0], 1.0) and np.isclose(rel[1], 52 / 285)
    res = S.sasa(ideal_backbone(12, -57, -47))
    assert np.isclose(res.residue.sum(), res.total) and np.isclose(res.atom.sum(), res.total) and np.all(res.relative > 0)
    assert max(res.relative[0], res.relative[-1]) > res.relative[6]                   # a terminus is more exposed than the middle of a helix


def test_toy_sasa_matches_biopython(run1_files, tmp_path):
    from Bio.PDB import PDBParser
    from Bio.PDB.SASA import ShrakeRupley

    cx = S.load_complex(run1_files[0])
    both = cx.combined()
    p = tmp_path / "heavy.pdb"
    S.write_pdb(str(p), cx.target, cx.binder)
    model = PDBParser(QUIET=True).get_structure("x", str(p))[0]
    ShrakeRupley(n_points=960).compute(model, level="A")
    bio = sum(a.sasa for a in model.get_atoms())
    assert abs(S.sasa_atoms(both.xyz, both.element, n_points=960).sum() - bio) / bio < 0.001      # same algorithm and radii
    for preset in ("default", "fast"):
        assert abs(S.sasa_atoms(both.xyz, both.element, n_points=preset).sum() - bio) / bio < 0.03, preset


def test_toy_interface_dsasa_consistency_and_speed(run1_files):
    cx = S.load_complex(run1_files[3])
    t0 = time.process_time()
    ids = S.interface_dsasa(cx)
    contacts = S.residue_contacts(cx.target, cx.binder)
    assert time.process_time() - t0 < 2.0
    assert ids.dsasa_binder > 100 and ids.dsasa_target > 100
    assert abs(ids.dsasa_binder - ids.dsasa_target) / ids.dsasa_binder < 0.35               # both sides bury a similar area
    assert np.isclose(ids.dsasa_total, ids.dsasa_binder + ids.dsasa_target) and 0 < ids.binder_buried_fraction < 1
    assert np.isclose(ids.binder_residue.sum(), ids.dsasa_binder)
    assert np.isclose(ids.binder_sasa_alone.sum() - ids.binder_sasa_complex.sum(), ids.dsasa_binder)
    buried = {int(n) for n, v in zip(cx.binder.residues.number, ids.binder_residue) if v > 0.5}
    assert len(set(contacts.b_residues) & buried) / len(contacts.b_residues) > 0.7
    ids2 = S.interface_dsasa(cx, target_alone=ids.target_sasa_alone)
    assert np.isclose(ids2.dsasa_total, ids.dsasa_total)                                        # a cached target-alone SASA gives the same answer


def test_toy_dsasa_tracks_the_csv_column(toy_run1):
    mine, ref = [], []
    for d in toy_run1.designs:
        if d.structure_path:
            mine.append(S.interface_dsasa(S.load_complex(d.structure_path)).dsasa_total)
            ref.append(d.pose_metric("dSASA"))
    r = np.corrcoef(mine, ref)[0, 1]
    ratio = np.median(np.array(mine) / np.array(ref))
    assert r > 0.9 and 0.8 < ratio < 1.25, (r, ratio)


# ------------------------------------------------------------------------------------------------ secondary structure
def test_dssp_synthetic_alpha_helix_and_extended():
    ss = S.assign_ss(ideal_backbone(30, -57, -47))
    assert len(ss) == 30 and ss[3:26].count("H") >= 20 and "E" not in ss
    assert S.assign_dssp(ideal_backbone(30, -57, -47))[8:22].count("H") >= 12
    e = ideal_backbone(12, -120, 130)
    assert "H" not in S.assign_ss(e) and S.assign_ss(e).count("E") == 0                      # a straight strand alone has no ladders


def test_dssp_breaks_at_gaps():
    h = ideal_backbone(30, -57, -47)
    cut = h.select(~np.isin(h.resnum, [15, 16, 17]))
    ss = S.assign_ss(cut)
    assert len(ss) == 27 and S.assign_dssp(cut)[14] != "H" and S.assign_ss(h).count("H") > ss.count("H")


def test_dssp_missing_backbone_gives_dash_and_loop():
    h = ideal_backbone(12, -57, -47)
    h = h.select(~((h.resnum == 6) & (h.name == "O")))
    d8 = S.assign_dssp(h)
    assert d8[5] == "-" and S.ss3_from_dssp(d8)[5] == "L" and S.ss_fractions("HHEL") == {"H": 0.5, "E": 0.25, "L": 0.25}


def test_toy_binders_are_helical_and_the_target_is_mostly_not_a_sheet(run1_files):
    cx = S.load_complex(run1_files[0])
    ssb = S.assign_ss(cx.binder)
    f = S.ss_fractions(ssb)
    assert len(ssb) == cx.binder.n_residues and set(ssb) <= set("HEL") and f["H"] > 0.7 and f["E"] == 0
    sst = S.assign_ss(cx.target)
    assert len(sst) == 110 and S.ss_fractions(sst)["H"] > 0.3                                  # helices of the toy domain


def test_toy_reference_secondary_structure_follows_the_construction(toy_reference):
    a = S.read_structure(str(toy_reference)).groups["A"]
    ss = S.assign_ss(a)
    assert len(ss) == 250 and ss.count("H") > 60                                               # the toy chain has about 150 helical residues


# ------------------------------------------------------------------------------------------------ hydrogen bonds, salt bridges
def _res(rows, resname, resnum, chain, atoms):
    for name, el, xyz in atoms:
        rows.append((*xyz, name, el, resname, resnum, chain))


def test_hbond_heavy_atom_geometry_synthetic():
    rows = []
    _res(rows, "SER", 1, "A", [("N", "N", (0, 0, 0)), ("CA", "C", (1.4, 0.4, 0)), ("C", "C", (2.2, -0.9, 0)), ("O", "O", (1.8, -2.0, 0)),
                               ("CB", "C", (1.6, 1.9, 0)), ("OG", "O", (2.9, 2.4, 0))])
    a = make_atoms(rows)
    og = a.xyz[a.atom(1, "OG")]
    rows = []
    _res(rows, "ASP", 5, "B", [("N", "N", (9, 9, 9)), ("CA", "C", (9, 9, 10.5)), ("C", "C", (9, 9, 12)), ("O", "O", (9, 10, 12.7)),
                               ("CG", "C", tuple(og + [3.9, 0, 0])), ("OD1", "O", tuple(og + [2.7, 0, 0])), ("OD2", "O", tuple(og + [4.4, 1.2, 0]))])
    b = make_atoms(rows)
    h = S.hbonds(a, b)
    assert [(r["a_atom"], r["b_atom"], r["donor"]) for r in h.records(a, b)] == [("OG", "OD1", "a")]
    assert h.method[0] == "heavy" and np.isclose(h.distance[0], 2.7) and h.angle_acc[0] > 170 and np.isnan(h.angle_dha[0])
    assert h.residue_pairs(a, b) == [(1, 5)]
    b2 = b.copy()
    b2.xyz[b2.atom(5, "CG")] = og + [1.9, 0.3, 0.0]                                            # a carbon between the oxygens: no bond
    assert len(S.hbonds(a, b2)) == 0
    b3 = b.copy()
    b3.xyz[:, 0] += 3.0                                                                          # too far
    assert len(S.hbonds(a, b3)) == 0
    assert [(r["a_atom"], r["b_atom"], r["donor"]) for r in S.hbonds(b, a).records(b, a)] == [("OD1", "OG", "b")]
    acc = make_atoms([(2.8, 0, 0, "O", "O", "GLY", 9, "B"), (3.9, 0, 0, "C", "C", "GLY", 9, "B")])
    pro = make_atoms([(0, 0, 0, "N", "N", "PRO", 1, "A"), (0, 0, 1.4, "CA", "C", "PRO", 1, "A")])
    assert len(S.hbonds(pro, acc)) == 0                                                          # Pro N is not a donor
    assert len(S.hbonds(make_atoms([(0, 0, 0, "NH1", "N", "ARG", 1, "A")]), acc)) == 1          # Arg NH1 is


def test_hbonds_in_ideal_helix_are_i_to_i_plus_4():
    h = ideal_backbone(24, -57, -47)
    recs = S.hbonds(h).records(h, h)
    assert len(recs) >= 14
    i4 = [r for r in recs if r["a_resnum"] - r["b_resnum"] == 4 and r["a_atom"] == "N" and r["b_atom"] == "O"]
    assert len(i4) >= 0.7 * len(recs) and "computed" in {r["method"] for r in recs} and all(r["distance"] <= 3.5 for r in recs)


def test_toy_interface_hbonds_lie_across_the_interface(toy_run1):
    n_total = 0
    for d in toy_run1.accepted():
        cx = S.load_complex(d.structure_path)
        hb = S.hbonds(cx.target, cx.binder)
        pairs = set(S.residue_contacts(cx.target, cx.binder, 4.5).pairs())
        for t, b in hb.residue_pairs(cx.target, cx.binder):
            assert (t, b) in pairs
        n_total += len(hb)
    assert n_total >= 3                                                                          # the packer rewards polar contacts


def test_salt_bridges_synthetic():
    rows = []
    _res(rows, "LYS", 1, "A", [("CA", "C", (0, 0, 0)), ("NZ", "N", (0, 0, 0))])
    _res(rows, "ARG", 2, "A", [("NE", "N", (20, 0, 0)), ("CZ", "C", (21, 0, 0)), ("NH1", "N", (22, 0, 0)), ("NH2", "N", (21, 1, 0))])
    a = make_atoms(rows)
    rows = []
    _res(rows, "ASP", 7, "B", [("OD1", "O", (0, 3.0, 0)), ("OD2", "O", (0, 3.4, 0))])
    _res(rows, "GLU", 8, "B", [("OE1", "O", (21, 8.0, 0)), ("OE2", "O", (21, 8.5, 0))])
    _res(rows, "LYS", 9, "B", [("NZ", "N", (0.5, 3.0, 0))])
    b = make_atoms(rows)
    sb = S.salt_bridges(a, b, 4.0)
    assert [(x.a_resnum, x.b_resnum) for x in sb] == [(1, 7)] and sb[0].positive_in_a and 3.0 < sb[0].distance < 3.4
    far = S.salt_bridges(a, b, 10.0)
    assert (2, 8) in [(x.a_resnum, x.b_resnum) for x in far] and all(x.a_resnum != 1 or x.b_resnum != 9 for x in far)   # same charge never pairs
    inside = S.salt_bridges(S.Atoms.concat([a, b]), None, 4.0)
    assert {frozenset((x.a_resnum, x.b_resnum)) for x in inside} == {frozenset((1, 7)), frozenset((7, 9))}


def test_toy_salt_bridges_are_charged_pairs_within_the_cutoff(toy_run1):
    for d in toy_run1.accepted()[:6]:
        cx = S.load_complex(d.structure_path)
        for x in S.salt_bridges(cx.target, cx.binder):
            assert x.distance <= 4.0 and x.positive_in_a == (x.a_resname in ("ARG", "LYS"))


# ------------------------------------------------------------------------------------------------ TM-align
def _helix_ca(n=60, seed=0):
    return ideal_backbone(n, -57, -47).ca_xyz() + np.random.default_rng(seed).normal(scale=0.01, size=(n, 3))


def test_tm_align_self_is_one_and_rotation_invariant():
    ca, seq = _helix_ca(50), "A" * 50
    r = S.tm_align(ca, seq, ca, seq)
    assert np.isclose(r.tm_shorter, 1.0, atol=1e-3) and np.isclose(r.tm_longer, 1.0, atol=1e-3) and r.rmsd < 0.01 and r.aligned_length == 50
    moved = ca @ random_rotation(np.random.default_rng(5)).T + 30.0
    r2 = S.tm_align(ca, seq, moved, seq)
    assert np.isclose(r2.tm_shorter, 1.0, atol=1e-3) and np.allclose(S.apply_transform(ca, r2.R, r2.t), moved, atol=0.05)
    assert np.isclose(S.tm_align(ca, None, ca, None).tm_shorter, 1.0, atol=1e-3)
    with pytest.raises(ValueError):
        S.tm_align(ca, "AAA", ca, seq)


def test_tm_align_normalisation_shorter_vs_longer():
    long_ = ideal_backbone(80, -57, -47).ca_xyz()
    r = S.tm_align(long_, None, long_[:40], None)
    assert r.len_1 == 80 and r.len_2 == 40 and r.tm_shorter > r.tm_longer
    assert np.isclose(r.tm_shorter, r.tm_2) and np.isclose(r.tm_longer, r.tm_1)
    assert r.tm_shorter > 0.9 and 0.4 < r.tm_longer < 0.6
    assert np.isclose(S.tm_align(long_[:40], None, long_, None).tm_shorter, r.tm_shorter, atol=0.02)


def test_tm_align_unrelated_chains_score_low():
    rng = np.random.default_rng(6)
    a, b = (np.cumsum(rng.normal(size=(60, 3)) * 2.2, axis=0) for _ in range(2))
    assert S.tm_align(a, None, b, None).tm_shorter < 0.5


def test_tm_matrix_symmetric_progress_and_inprocess():
    rng = np.random.default_rng(8)
    chains = []
    for i in range(7):
        n = 40 + 5 * i
        chains.append((ideal_backbone(n, -57 + i, -47).ca_xyz() + rng.normal(scale=0.05, size=(n, 3)), None))
    seen = []
    m = S.tm_matrix(chains, n_jobs=1, progress=lambda d, t: seen.append((d, t)))
    assert m.tm.shape == (7, 7) and np.array_equal(m.tm, m.tm.T) and np.array_equal(m.rmsd, m.rmsd.T)
    assert np.allclose(np.diag(m.tm), 1.0) and np.allclose(np.diag(m.tm_longer), 1.0) and np.all(m.tm >= m.tm_longer - 1e-12)
    assert seen[-1] == (6, 6) and len(seen) == 6 and np.array_equal(np.diag(m.aligned), [len(c[0]) for c in chains])
    assert np.allclose(S.tm_matrix(chains, n_jobs=2).tm, m.tm)
    assert S.tm_matrix([]).n == 0 and S.tm_matrix([chains[0]]).tm[0, 0] == 1.0


def test_toy_binders_tm_scores_follow_the_construction(toy_run1, toy_truth):
    binders = {d.id: S.load_complex(d.structure_path).binder for d in toy_run1.accepted()}
    bb = toy_truth["runs"]["run01"]["backbones"]
    a1, a2, a3 = bb["a1"]["id"], bb["a2"]["id"], bb["a3"]["id"]
    pick = lambda bid: next(i for i in binders if i.startswith(bid))
    same_fold = S.tm_align_atoms(binders[pick(a1)], binders[pick(a2)]).tm_shorter       # a2 is a neighbour of a1 (same plan, small torsion noise)
    other = S.tm_align_atoms(binders[pick(a1)], binders[pick(a3)]).tm_shorter
    assert same_fold > 0.6 and same_fold > other - 0.05
    m = S.tm_matrix(list(binders.values()))
    assert np.array_equal(m.tm, m.tm.T) and np.allclose(np.diag(m.tm), 1.0, atol=1e-3)
    assert 0.2 < np.median(m.tm[np.triu_indices(len(binders), 1)]) < 0.95
    assert np.isclose(S.tm_align_atoms(binders[pick(a1)], binders[pick(a1)]).tm_shorter, 1.0, atol=1e-3)


# ------------------------------------------------------------------------------------------------ sequence helpers
def test_three_to_one_and_back():
    for one, three in S.ONE_TO_THREE.items():
        assert S.three_to_one(three) == one and S.one_to_three(one) == three
    assert len(S.THREE_TO_ONE) == 20 and S.three_to_one("MSE") == "M" and S.three_to_one("HIE") == "H" and S.three_to_one("NAG") == "X"
    with pytest.raises(KeyError):
        S.three_to_one("NAG", strict=True)
    assert S.one_to_three("Z") == "UNK" and S.three_to_one("ala") == "A"


def test_hydrophobicity_charge_and_pi():
    assert np.isclose(S.gravy("IIII"), 4.5) and np.isclose(S.gravy("RRRR"), -4.5) and np.isclose(S.gravy("AG"), (1.8 - 0.4) / 2) and S.gravy("") == 0.0
    assert S.net_charge("KKKKK", 7.4) > 4.5 and S.net_charge("DDDDD", 7.4) < -4.5 and abs(S.net_charge("GGGG", 7.4)) < 0.3
    assert S.net_charge("KKKKK", 14) < S.net_charge("KKKKK", 7.4)
    assert S.isoelectric_point("KKKKKKKK") > 10.5 and S.isoelectric_point("DDDDDDDD") < 3.9
    pi = S.isoelectric_point("AKDEHRGGYC")
    assert abs(S.net_charge("AKDEHRGGYC", pi)) < 1e-6
    assert 0 < S.sequence_hydrophobic_fraction("AAGKK") < 1 and S.sequence_hydrophobic_fraction("KKDD") == 0.0 and S.BINDCRAFT_APOLAR >= set("AILMVFWYPG")


def test_toy_binder_properties_are_in_range(toy_run1):
    for d in toy_run1.designs:
        assert -2.5 < S.gravy(d.sequence) < 2.5 and -30 < S.net_charge(d.sequence) < 30 and 3 < S.isoelectric_point(d.sequence) < 12
        assert 0 < S.sequence_hydrophobic_fraction(d.sequence) < 0.8
    cx = S.load_complex(toy_run1.accepted()[0].structure_path)
    assert 0.0 <= S.surface_hydrophobicity(cx.binder) <= 1.0


def test_sc_neighbors_buried_vs_exposed_synthetic():
    scn = S.sc_neighbors(ideal_backbone(30, -57, -47))
    assert len(scn) == 30 and not np.isnan(scn).any() and scn[10:20].mean() > scn[:2].mean()
