"""Disulfides read from SG-SG geometry when the structure file records none (binder_review.disulfides), the pairing rule,
the precedence of a recorded bond, and how the cysteine liabilities say which of the two they rest on."""
from __future__ import annotations

import pytest

from binder_review import disulfides as D
from binder_review import interface as I
from binder_review import liabilities as L
from binder_review import structure as S

from test_interface_recomputed import _bb, _pdb


def _sg(**by_res):
    """{residue: x} -> SG coordinates on a line, so that the distance between two sulfurs is |x1 - x2|."""
    return {int(k[1:]): (float(v), 0.0, 0.0) for k, v in by_res.items()}


# ------------------------------------------------------------------------------------------------ pairing and bands
def test_the_cutoffs_sit_between_a_bond_and_a_van_der_waals_contact():
    assert 2.03 < D.CLOSE_MAX < D.LOOSE_MAX < 3.6        # a closed bond, then the sum of the two sulfur radii


@pytest.mark.parametrize("d, band", [(2.05, "close"), (2.9, "close"), (3.0, "close"), (3.2, "loose"), (3.4, "loose")])
def test_a_pair_inside_the_limit_is_kept_and_banded(d, band):
    out = D.infer_pairs(_sg(r22=0.0, r96=d))
    assert out == [{"a": 22, "b": 96, "d": pytest.approx(d, abs=0.005), "band": band}]


@pytest.mark.parametrize("d", [3.41, 3.6, 4.5])
def test_cysteines_that_are_merely_near_are_not_paired(d):
    assert D.infer_pairs(_sg(r22=0.0, r96=d)) == []


def test_a_sulfur_takes_part_in_one_disulfide_and_the_tighter_bond_wins():
    # B is within reach of both A (2.9 A) and C (2.1 A): only B-C is a bond, A stays free.
    out = D.infer_pairs(_sg(r10=0.0, r20=2.9, r30=5.0))
    assert [(p["a"], p["b"]) for p in out] == [(20, 30)] and out[0]["d"] == pytest.approx(2.1, abs=0.005)
    used = [x for p in out for x in (p["a"], p["b"])]
    assert len(used) == len(set(used))


def test_two_loose_pairs_beat_one_tight_pair_only_when_that_is_the_better_pairing():
    # A-B 2.0, B-C 3.3, C-D 2.0 on a line: the two tight bonds use all four sulfurs, the loose B-C bond is not chosen.
    out = D.infer_pairs(_sg(r1=0.0, r2=2.0, r3=5.3, r4=7.3))
    assert [(p["a"], p["b"]) for p in out] == [(1, 2), (3, 4)]
    # A-B 2.9, B-C 2.1, C-D 2.9: the tight middle bond (margin 1.3) beats the two outer ones (0.5 each is 1.0).
    out = D.infer_pairs(_sg(r1=0.0, r2=2.9, r3=5.0, r4=7.9))
    assert [(p["a"], p["b"]) for p in out] == [(2, 3)]


# ------------------------------------------------------------------------------------------------ recorded beats inferred
def test_a_recorded_disulfide_is_used_as_it_is_even_when_geometry_disagrees():
    sg = _sg(r1=0.0, r2=2.5, r3=9.0)                     # geometry would pair 1-2
    out = D.assess(sg, recorded=[(3, 1)])
    assert out["basis"] == "recorded" and [(p["a"], p["b"], p["band"]) for p in out["pairs"]] == [(1, 3, "recorded")]
    assert out["pairs"][0]["d"] == pytest.approx(9.0)    # the distance is reported, not used to overrule the record


def test_with_no_record_the_geometry_is_the_answer_and_with_neither_nothing_is_judged():
    assert D.assess(_sg(r1=0.0, r2=2.5))["basis"] == "inferred"
    assert D.assess({}, recorded=None) == {"basis": "none", "pairs": [], "nearest": {}}
    assert D.assess({}, recorded=[(1, 2)])["pairs"] == [{"a": 1, "b": 2, "d": None, "band": "recorded"}]


def test_nearest_sulfur():
    assert D.nearest_sg(_sg(r1=0.0, r5=4.0, r9=11.0)) == {1: 4.0, 5: 4.0, 9: 7.0}
    assert D.nearest_sg(_sg(r1=0.0)) == {1: None}


# ------------------------------------------------------------------------------------------------ liabilities
SEQ = "AACAAAAACAAA"       # Cys at 3 and 9


def _free(liabs):
    return [e for e in liabs if e["kind"] == "Unpaired cysteine"]


def test_inferred_pair_leaves_no_unpaired_cysteine():
    ds = D.assess({3: (0.0, 0.0, 0.0), 9: (2.8, 0.0, 0.0)})
    assert _free(L.find_liabilities(SEQ, disulfides=ds)) == []


def test_a_cysteine_with_no_partner_is_still_a_bad_liability_that_says_how_it_was_judged():
    ds = D.assess({3: (0.0, 0.0, 0.0), 9: (4.6, 0.0, 0.0)})
    free = _free(L.find_liabilities(SEQ, disulfides=ds))
    assert [e["pos"] for e in free] == [3, 9] and all(e["sev"] == "bad" and e["basis"] == "inferred" for e in free)
    assert "4.6 A" in free[0]["reason"] and "records no disulfides" in free[0]["reason"]


def test_a_lone_cysteine_is_unpaired_and_a_recorded_basis_says_so():
    ds = D.assess({3: (0.0, 0.0, 0.0)})
    free = _free(L.find_liabilities("AACAAAA", disulfides=ds))
    assert len(free) == 1 and "No other Cys sulfur" in free[0]["reason"]
    rec = D.assess({3: (0.0, 0.0, 0.0), 9: (2.0, 0.0, 0.0)}, recorded=[(3, 4)])
    free = _free(L.find_liabilities(SEQ, disulfides=rec))
    assert [e["pos"] for e in free] == [9] and free[0]["basis"] == "recorded" and "records disulfides" in free[0]["reason"]


def test_a_loose_pair_is_noted_not_failed():
    ds = D.assess({3: (0.0, 0.0, 0.0), 9: (3.3, 0.0, 0.0)})
    out = L.find_liabilities(SEQ, disulfides=ds)
    loose = [e for e in out if e["kind"] == "Disulfide geometry loose"]
    assert _free(out) == [] and len(loose) == 1 and loose[0]["sev"] == "info" and loose[0]["basis"] == "inferred"
    assert not [e for e in L.find_liabilities(SEQ, disulfides=D.assess({3: (0, 0, 0), 9: (2.2, 0, 0)}))
                if e["kind"] == "Disulfide geometry loose"]


def test_with_no_record_and_no_coordinates_the_cysteine_check_is_off():
    assert _free(L.find_liabilities(SEQ, disulfides=D.assess({}))) == []


def test_a_cysteine_without_an_sg_atom_is_not_judged():
    ds = D.assess({3: (0.0, 0.0, 0.0), 9: (2.5, 0.0, 0.0)})
    assert _free(L.find_liabilities("AACAAAAACAAAC", disulfides=ds)) == []       # Cys13 has no SG in the model


def test_the_basis_reaches_the_campaign_and_inspector_blobs_only_on_disulfide_entries():
    ds = D.assess({3: (0.0, 0.0, 0.0), 9: (4.6, 0.0, 0.0)})
    out = L.find_liabilities(SEQ + "NGS", disulfides=ds)
    camp = {e["k"]: e for e in L.to_campaign(out)}
    insp = {e["kind"]: e for e in L.to_inspector(out)}
    assert camp["Unpaired cysteine"]["b"] == "inferred" and "b" not in camp["Deamidation (NG)"]
    assert insp["Unpaired cysteine"]["basis"] == "inferred" and "basis" not in insp["Deamidation (NG)"]


def test_the_older_ssbonds_argument_behaves_as_before():
    assert [e["pos"] for e in _free(L.find_liabilities("CAACAACAAC", ssbonds=[[1, 4]]))] == [7, 10]
    assert "basis" not in _free(L.find_liabilities("AACAA"))[0]


# ------------------------------------------------------------------------------------------------ end to end
def _binder(d, third=False, ssbond=None):
    """A target ALA and a binder CYS-ALA-CYS (optionally CYS-CYS-CYS) whose first and last sulfurs are ``d`` A apart."""
    t = [("A", 1, "ALA", _bb(0.0, 0.0, cb=(0.0, -2.0, 0.0)))]
    mid = ("B", 2, "CYS" if third else "ALA", _bb(10.0, 0.0, cb=(10.0, -2.0, 0.0), extra=[("SG", "S", 10.0, -3.0, 0.0)] if third else ()))
    b = [("B", 1, "CYS", _bb(3.0, 0.0, cb=(3.0, -1.0, 0.0), extra=[("SG", "S", 3.0, -3.0, 0.0)])), mid,
         ("B", 3, "CYS", _bb(3.0, 8.0, cb=(3.0, 6.0, 0.0), extra=[("SG", "S", 3.0, -3.0 - d, 0.0)]))]
    text = _pdb(t + b)
    if ssbond:
        text = "".join("SSBOND%4d CYS B %4d    CYS B %4d                       1555   1555  2.03\n" % (k + 1, i, j)
                       for k, (i, j) in enumerate(ssbond)) + text
    return text


def _analyse(tmp_path, text):
    p = tmp_path / "x.pdb"
    p.write_text(text)
    return I.analyze_design(S.load_complex(str(p), strip_h=False), None, [], n_points="default")


def test_a_pair_past_the_old_2_6_limit_is_a_disulfide_not_two_unpaired_cysteines(tmp_path):
    a = _analyse(tmp_path, _binder(2.9))
    assert a.ssbonds == [[1, 3]] and a.ssbond_basis == "inferred" and a.disulfides[0]["band"] == "close"
    assert not _free(a.liab)


def test_cysteines_a_few_angstrom_too_far_apart_are_unpaired(tmp_path):
    a = _analyse(tmp_path, _binder(4.2))
    assert a.ssbonds == [] and a.ssbond_basis == "inferred"
    assert [e["pos"] for e in _free(a.liab)] == [1, 3] and all(e["basis"] == "inferred" for e in _free(a.liab))


def test_the_file_record_wins_over_the_geometry(tmp_path):
    # Sulfurs 1 and 3 are 6 A apart but the file says they are bonded; the record is kept, with a note, and Cys 2 is free.
    a = _analyse(tmp_path, _binder(6.0, third=True, ssbond=[(1, 3)]))
    assert a.ssbond_basis == "recorded" and a.ssbonds == [[1, 3]] and a.disulfides[0]["band"] == "recorded"
    assert [e["pos"] for e in _free(a.liab)] == [2] and _free(a.liab)[0]["basis"] == "recorded"
    assert any("records a disulfide between binder residues 1 and 3" in n for n in a.meta["notes"])


def test_the_basis_survives_the_analysis_round_trip(tmp_path):
    a = _analyse(tmp_path, _binder(3.3))
    b = I.DesignAnalysis.from_dict(a.to_dict())
    assert b.ssbond_basis == "inferred" and b.disulfides == a.disulfides and b.disulfides[0]["band"] == "loose"
