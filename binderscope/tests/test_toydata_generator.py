"""The generator itself: the toy target, the construct files, the runs and the ground truth it writes.

These tests say what the other tests may rely on (an exact two-fold, standard geometry, files that match the
truth file), so a failure here means the synthetic data changed, not the code under test."""
from __future__ import annotations

import json
from pathlib import Path

import gemmi
import numpy as np
import pytest
from scipy.spatial import cKDTree

from toydata import geometry as G
from toydata import target as T
from toydata.testing import OFFSET


def _chain_xyz(st: gemmi.Structure, chain: str):
    rows = [(r.seqid.num, r.name, a.name, a.element.name, np.array(a.pos.tolist())) for r in st[0][chain] for a in r]
    return rows


def _read(path: Path) -> gemmi.Structure:
    st = gemmi.read_structure(str(path))
    st.setup_entities()
    return st


@pytest.fixture(scope="module")
def reference(toy_reference):
    return _read(toy_reference)


def _ref_atoms(reference, chain):
    rows = _chain_xyz(reference, chain)
    return (np.array([r[0] for r in rows]), np.array([r[2] for r in rows]), np.array([r[3] for r in rows]),
            np.array([r[4] for r in rows]), np.array([r[1] for r in rows]))


def test_target_is_deterministic():
    a, b = T.build_target(8), T.build_target(8)
    assert np.array_equal(a.atoms_a.xyz, b.atoms_a.xyz) and a.sequence == b.sequence
    assert not np.array_equal(T.build_target(9).atoms_a.xyz[:5], a.atoms_a.xyz[:5])


def test_reference_is_a_two_fold_homodimer(reference):
    chains = [c.name for c in reference[0]]
    assert chains == ["A", "B"]
    na, la, ea, xa, ra = _ref_atoms(reference, "A")
    nb, lb, eb, xb, rb = _ref_atoms(reference, "B")
    assert len(set(na)) == len(set(nb)) == 250 and list(sorted(set(na)))[0] == 1
    assert list(la) == list(lb) and list(ra) == list(rb)
    # chain B is chain A turned by 180 degrees about z: (x, y, z) -> (-x, -y, z), to the precision of the files
    assert np.allclose(xb, xa * np.array([-1, -1, 1]), atol=2e-3)
    # no clash between the subunits, but they touch
    d, _ = cKDTree(xb).query(xa)
    assert d.min() > 3.0 and (d < 4.5).sum() >= 6


def test_reference_folder_holds_the_toy_profile_and_the_offline_cache(toy_dir):
    ref = toy_dir / "reference"
    prof = json.loads((ref / "toy.profile.json").read_text(encoding="utf-8"))
    assert prof["id"] == "toy" and prof["target"]["uniprot"] == "Z0TOY1"
    assert [m["acc"] for m in prof["specificity"]["paralogs"]["members"]] == ["Z0TOY5", "Z0TOY6"] and prof["specificity"]["paralogs"]["focus"]
    cache = ref / "refdata"
    panel = json.loads((cache / "panel.json").read_text(encoding="utf-8"))
    assert panel["target"]["accession"] == "Z0TOY1" and len(panel["target"]["accession"]) == 6
    accs = ["Z0TOY1"] + [o["accession"] for o in panel["orthologs"]]
    assert accs == ["Z0TOY1", "Z0TOY2", "Z0TOY3", "Z0TOY4"]                      # three orthologs; Z0TOY5 and Z0TOY6 are the paralogs
    for acc in (f"Z0TOY{i}" for i in range(1, 7)):
        assert (cache / "uniprot" / f"{acc}.fasta").is_file() and json.loads((cache / "uniprot" / f"{acc}.json").read_text())
    assert sorted(p.name for p in (cache / "alphafold").iterdir()) == ["Z0TOY1", "Z0TOY5", "Z0TOY6"]
    own = "".join(l.strip() for l in (cache / "uniprot" / "Z0TOY1.fasta").read_text().splitlines() if not l.startswith(">"))
    assert own == "".join(l.strip() for l in (ref / "toy_target_chain_A.fasta").read_text().splitlines() if not l.startswith(">"))


def test_reference_sequence_is_valid_and_matches_the_fasta(reference, toy_dir):
    na, la, ea, xa, ra = _ref_atoms(reference, "A")
    first = {int(n): r for n, r in zip(na, ra)}
    seq = "".join(G.ONE[first[i]] for i in sorted(first))
    fasta = "".join(l.strip() for l in (toy_dir / "reference" / "toy_target_chain_A.fasta").read_text().splitlines() if not l.startswith(">"))
    assert seq == fasta and len(seq) == 250 and set(seq) <= set(G.AA1)
    related = [l for l in (toy_dir / "reference" / "related_sequences.fasta").read_text().splitlines() if l.startswith(">")]
    assert len(related) == 5 and any("paralog" in r for r in related) and any("ortholog" in r for r in related)


def test_backbone_geometry_is_standard(reference):
    st = reference
    prev_c = None
    bad = 0
    for r in st[0]["A"]:
        at = {a.name: np.array(a.pos.tolist()) for a in r}
        assert {"N", "CA", "C", "O"} <= set(at)
        assert abs(np.linalg.norm(at["N"] - at["CA"]) - 1.458) < 0.01
        assert abs(np.linalg.norm(at["CA"] - at["C"]) - 1.525) < 0.01
        assert abs(np.linalg.norm(at["C"] - at["O"]) - 1.231) < 0.01
        v1, v2 = at["N"] - at["CA"], at["C"] - at["CA"]
        ang = np.degrees(np.arccos(v1 @ v2 / np.linalg.norm(v1) / np.linalg.norm(v2)))
        assert abs(ang - 111.0) < 0.5
        if prev_c is not None:
            bad += abs(np.linalg.norm(prev_c - at["N"]) - 1.329) > 0.01
        prev_c = at["C"]
    assert bad == 0


def test_secondary_structure_segments_have_ideal_torsions():
    tg = T.build_target(8)
    ss, ch = tg.chain_a.ss, tg.chain_a
    assert set(ss) == {"H", "E", "L"} and len(ss) == 250
    # torsions of the first helix interior: phi -57, psi -47 within the 1 degree noise of the builder
    from scipy.spatial.transform import Rotation  # noqa: F401  (scipy is a dependency; keeps the import honest)
    start = ss.index("H")
    n = np.arange(start + 2, start + 10)
    def dihedral(p0, p1, p2, p3):
        b0, b1, b2 = p0 - p1, p2 - p1, p3 - p2
        b1 = b1 / np.linalg.norm(b1)
        v = b0 - (b0 @ b1) * b1
        w = b2 - (b2 @ b1) * b1
        return np.degrees(np.arctan2(np.cross(b1, v) @ w, v @ w))
    phis = [dihedral(ch.C[i - 1], ch.N[i], ch.CA[i], ch.C[i]) for i in n]
    psis = [dihedral(ch.N[i], ch.CA[i], ch.C[i], ch.N[i + 1]) for i in n]
    assert np.allclose(phis, -57, atol=6) and np.allclose(psis, -47, atol=6)


def test_construct_is_a_renumbered_fragment_in_its_own_frame(toy_construct, toy_truth, reference):
    con = _read(toy_construct)
    ch = con[0]["A"]
    assert [r.seqid.num for r in ch] == list(range(1, 111))
    na, la, ea, xa, ra = _ref_atoms(reference, "A")
    ref_seq = {int(n): r for n, r in zip(na, ra)}
    assert [r.name for r in ch] == [ref_seq[i + OFFSET] for i in range(1, 111)]
    assert toy_truth["offset"] == OFFSET and toy_truth["construct_reference_range"] == [60, 169]
    # x_construct = x_reference @ R.T + t, exactly the truth file's frame
    R, t = np.array(toy_truth["frame_rotation"]), np.array(toy_truth["frame_translation"])
    cx = np.array([a.pos.tolist() for r in ch for a in r])
    sel = (na >= 60) & (na <= 169)
    assert np.allclose(xa[sel] @ R.T + t, cx, atol=2e-3)
    assert np.isclose(np.linalg.det(R), 1.0)


def test_gapped_construct_keeps_numbering(toy_construct_gapped, toy_truth):
    ch = _read(toy_construct_gapped)[0]["A"]
    nums = [r.seqid.num for r in ch]
    gaps = toy_truth["deleted_construct"]
    assert gaps == [[20, 23], [80, 83]]
    assert nums == [n for n in range(1, 111) if not any(a <= n <= b for a, b in gaps)] and len(nums) == 102


def test_run_folders_have_the_layouts_the_reader_expects(toy_dir):
    r1, r2 = toy_dir / "runs" / "run01", toy_dir / "runs" / "run02"
    for r in (r1, r2):
        for f in ("final_design_stats.csv", "mpnn_design_stats.csv", "trajectory_stats.csv", "failure_csv.csv"):
            assert (r / f).is_file()
    assert (r1 / "Accepted").is_dir() and (r1 / "Accepted" / "Ranked").is_dir() and (r1 / "Rejected").is_dir()
    assert (r1 / "run01.json").is_file() and not list(r2.glob("*.json"))
    assert (r2 / "accepted").is_dir() and not (r2 / "rejected").exists() and not (r2 / "Rejected").exists()
    settings = json.loads((r1 / "run01.json").read_text())
    assert settings["target_hotspot_residues"].count(",") == 2 and settings["lengths"] == [60, 100]
    assert "starting_pdb" in settings and "binder_name" in settings


def test_accepted_models_are_pdb_files_with_plddt_in_the_b_factor(toy_dir, toy_run1):
    for d in toy_run1.accepted():
        st = gemmi.read_structure(d.structure_path)
        assert [c.name for c in st[0]] == ["A", "B"]
        bs = [a.b_iso for r in st[0]["B"] for a in r]
        assert 40 < np.mean(bs) < 100 and abs(np.mean(bs) / 100 - d.pose_metric("pLDDT")) <= 0.02
        assert [r.seqid.num for r in st[0]["B"]] == list(range(1, d.length + 1))


def test_poses_do_what_the_truth_file_says(toy_dir, toy_truth, toy_reference):
    """Every docked backbone is placed against its goal: clean, clashing with the rest of chain A, touching chain B,
    overlapping a deleted loop; hotspots touched or not. Checked on the written files, in the reference frame."""
    ref = _read(toy_reference)
    R, t = np.array(toy_truth["frame_rotation"]), np.array(toy_truth["frame_translation"])
    na, la, ea, xa, ra = _ref_atoms(ref, "A")
    nb, lb, eb, xb, rb = _ref_atoms(ref, "B")
    tree = {"out": cKDTree(xa[(na < 60) | (na > 169)]), "oth": cKDTree(xb)}
    n_kind = {}
    for run, hot_key in (("run01", "run01"), ("run02", "run02")):
        gap_sel = np.zeros(len(na), dtype=bool)
        if run == "run02":
            for a, b in toy_truth["deleted_reference"]:
                gap_sel |= (na >= a) & (na <= b)
        gap_tree = cKDTree(xa[gap_sel]) if gap_sel.any() else None
        hot_ref = toy_truth["hotspots_reference"][hot_key]
        hot_tree = cKDTree(xa[np.isin(na, hot_ref)])
        for did, rel in toy_truth["runs"][run]["files"].items():
            kind = toy_truth["design_kind"][run][did]
            if kind is None:
                continue
            st = _read(toy_dir / rel)
            bx = np.array([a.pos.tolist() for r in st[0]["B"] for a in r])
            bx = (bx - t) @ R                                  # back to the reference frame
            d_out, d_oth = tree["out"].query(bx)[0], tree["oth"].query(bx)[0]
            d_gap = gap_tree.query(bx)[0] if gap_tree is not None else np.full(len(bx), 99.0)
            d_hot = hot_tree.query(bx)[0]
            n_kind[kind] = n_kind.get(kind, 0) + 1
            if kind in ("clean_hot", "clean_away"):
                assert d_out.min() > 4.0 and d_oth.min() > 4.0 and d_gap.min() > 4.0, (did, kind)
            if kind == "clean_hot":
                assert d_hot.min() < 5.0, did
            if kind == "clean_away":
                assert d_hot.min() > 4.5, did
            if kind == "collide_out":
                assert (d_out < 2.5).sum() >= 3, did
            if kind == "touch_other":
                assert d_oth.min() < 4.5 and (d_out < 2.5).sum() == 0, did
            if kind == "collide_gap":
                assert (d_gap < 2.5).sum() >= 3, did
    assert set(n_kind) == {"clean_hot", "clean_away", "collide_out", "touch_other", "collide_gap"}


def test_family_pair_is_similar_in_fold_and_site(toy_truth, toy_run1, toy_run2):
    from binder_review import families as F
    from toydata.testing import with_offset

    for run, a, b in ((toy_run1, "a1", "a2"), (toy_run2, "c1", "c2")):
        truth = toy_truth["runs"][run.run_id]["backbones"]
        assert truth[b]["family_of"] == a
        fams = {f.id: f for f in F.build_families(with_offset(run))}
        fa, fb = fams[truth[a]["id"]], fams[truth[b]["id"]]
        inter, union = set(fa.footprint) & set(fb.footprint), set(fa.footprint) | set(fb.footprint)
        assert len(inter) / len(union) >= 0.4


def test_every_design_name_follows_the_bindcraft_scheme(toy_run1, toy_run2):
    import re

    for run in (toy_run1, toy_run2):
        assert all(re.fullmatch(r"binder_l\d+_s\d{6}_mpnn\d+", d.id) for d in run.designs)
        assert len({d.backbone_id for d in run.designs}) >= 8


def test_binders_have_right_handed_helices_and_proper_poses(toy_dir, toy_truth):
    """Every docked binder keeps its handedness: the hydrogen-bond pattern N(i+4) to O(i) of an alpha helix is there.
    (A mirror image would turn every L-amino acid into a D-amino acid and no helix would be recognised.)"""
    for run in ("run01", "run02"):
        for did, rel in toy_truth["runs"][run]["files"].items():
            ch = _read(toy_dir / rel)[0]["B"]
            N = np.array([r["N"][0].pos.tolist() for r in ch])
            O = np.array([r["O"][0].pos.tolist() for r in ch])
            d = np.linalg.norm(N[4:] - O[:-4], axis=1)
            assert (d < 3.6).mean() > 0.5, (did, float(np.median(d)))


# ---------------------------------------------------------------------------------------------------------------- variants of the reference (placement and import page tests)
def test_the_reference_with_the_target_called_b_is_the_same_structure_with_the_chain_ids_swapped(toy_dir, toy_truth, reference):
    swapped = _read(toy_dir / "reference" / "toy_target_chain_b.cif")
    assert [c.name for c in swapped[0]] == ["A", "B"] and toy_truth["reference"]["cif_chain_b"] == "reference/toy_target_chain_b.cif"
    for new, old in (("B", "A"), ("A", "B")):                                              # the target (chain A of the plain reference) is chain B now
        na, la, ea, xa, ra = _ref_atoms(swapped, new)
        nb, lb, eb, xb, rb = _ref_atoms(reference, old)
        assert list(na) == list(nb) and list(la) == list(lb) and list(ra) == list(rb) and np.allclose(xa, xb, atol=1e-3), (new, old)


def test_the_hinge_reference_turns_the_domain_of_the_binder_about_a_pivot_far_from_it(toy_dir, reference, tmp_path):
    from toydata.hinge import hinge_reference

    plain = _ref_atoms(reference, "A")
    ca = {int(n): x for n, a, x in zip(plain[0], plain[1], plain[3]) if a == "CA"}
    binder = np.array([ca[n] for n in range(100, 111)]) + np.array([4.0, 0.0, 0.0])                # a "binder" beside residues 100 to 110
    info = hinge_reference(toy_dir / "reference" / "toy_target.cif", binder, tmp_path / "hinge.cif", radius=18.0, angle=12.0)
    turned = _read(tmp_path / "hinge.cif")
    tr = _ref_atoms(turned, "A")
    new = {int(n): x for n, a, x in zip(tr[0], tr[1], tr[3]) if a == "CA"}
    near = {n for n, x in ca.items() if cKDTree(binder).query(x)[0] < 18.0}
    assert set(info["turned"]) == near and info["n_turned"] == len(near) and info["chain"] == "A" and info["angle"] == 12.0 and 20 < len(near) < 150
    pivot, axis = np.array(info["pivot"]), np.array(info["axis"])
    assert np.isclose(np.linalg.norm(axis), 1.0) and cKDTree(binder).query(pivot)[0] > 18.0 and info["n_chain"] == 250        # the pivot is a residue that stays
    for n, x in ca.items():
        if n in near:
            before, after = x - pivot, new[n] - pivot
            flat = lambda v: v - axis * (v @ axis)                                           # noqa: E731  the part of a vector across the axis
            u, w = flat(before), flat(after)
            if np.linalg.norm(u) > 5.0:
                turn = np.degrees(np.arccos(np.clip(u @ w / (np.linalg.norm(u) * np.linalg.norm(w)), -1, 1)))
                assert turn == pytest.approx(12.0, abs=0.05), n
            assert abs(before @ axis - after @ axis) < 2e-3 and abs(np.linalg.norm(before) - np.linalg.norm(after)) < 2e-3, n          # a rotation about the axis
        else:
            assert np.allclose(new[n], x, atol=2e-3), n                                      # the rest of the chain does not move
    b0, b1 = _ref_atoms(reference, "B"), _ref_atoms(turned, "B")
    assert np.allclose(b0[3], b1[3], atol=1e-3)                                              # the other subunit does not move either


def test_the_hinge_reference_can_call_the_target_chain_b(toy_dir, reference, tmp_path):
    from toydata.hinge import hinge_reference

    plain = _ref_atoms(reference, "A")
    binder = np.array([x for n, a, x in zip(plain[0], plain[1], plain[3]) if a == "CA" and 100 <= n <= 110]) + np.array([4.0, 0.0, 0.0])
    one = hinge_reference(toy_dir / "reference" / "toy_target.cif", binder, tmp_path / "a.cif")
    two = hinge_reference(toy_dir / "reference" / "toy_target.cif", binder, tmp_path / "b.cif", swap_chains=True)
    assert (one["chain"], two["chain"]) == ("A", "B") and one["turned"] == two["turned"]
    a, b = _read(tmp_path / "a.cif"), _read(tmp_path / "b.cif")
    assert [c.name for c in b[0]] == ["A", "B"]
    for chain_a, chain_b in (("A", "B"), ("B", "A")):                                        # the turned target is chain B now; its partner is chain A
        xa, xb = _ref_atoms(a, chain_a)[3], _ref_atoms(b, chain_b)[3]
        assert np.allclose(xa, xb, atol=1e-3)


def test_the_reference_variants_of_the_import_page_tests(toy_dir, reference, tmp_path):
    from toydata.variants import scrambled_chain_b, short_chain_b, twinned_residue

    ref = toy_dir / "reference" / "toy_target.cif"
    plain_a, plain_b = _ref_atoms(reference, "A"), _ref_atoms(reference, "B")
    scrambled = _read(scrambled_chain_b(ref, tmp_path / "s.cif"))
    sa, sb = _ref_atoms(scrambled, "A"), _ref_atoms(scrambled, "B")
    names = lambda st, c: [r.name for r in st[0][c]]                                                                          # noqa: E731
    assert names(scrambled, "A") == names(reference, "A") and sorted(names(scrambled, "B")) == sorted(names(reference, "B"))
    assert names(scrambled, "B") != names(reference, "B")                                                                     # B: the same residues in another order
    assert np.allclose(sb[3], plain_b[3], atol=1e-3) and np.allclose(sa[3], plain_a[3], atol=1e-3)                            # the coordinates are the same
    short = _read(short_chain_b(ref, tmp_path / "t.cif", first=140))
    ta, tb = _ref_atoms(short, "A"), _ref_atoms(short, "B")
    assert list(ta[0]) == list(plain_a[0]) and set(tb[0]) == set(range(140, 251))                                              # B holds only its end
    twin = _read(twinned_residue(ref, tmp_path / "u.cif", number=150, shift=20.0))
    chain = twin[0]["A"]
    ids = [(r.seqid.num, r.seqid.icode) for r in chain]
    assert len(chain) == 251 and ids.count((150, " ")) == 1 and ids.count((150, "A")) == 1 and ids.index((150, "A")) == ids.index((150, " ")) + 1
    first, second = chain[ids.index((150, " "))], chain[ids.index((150, "A"))]
    assert first.name == second.name and np.allclose(np.array(second["CA"][0].pos.tolist()) - np.array(first["CA"][0].pos.tolist()), [20.0, 0, 0], atol=2e-3)
    assert len(twin[0]["B"]) == 250
