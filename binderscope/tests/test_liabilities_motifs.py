"""Sequence liabilities (binder_review.liabilities): motifs, positions, severities, exposure rules, odd input, and the toy designs
that carry planted motifs (a glycosylation sequon, an unpaired cysteine, an NG and a DG)."""
from __future__ import annotations

import json

import pytest

from binder_review import liabilities as L


def find(liabs, kind):
    return [e for e in liabs if e["kind"] == kind]


def test_clean_sequence_has_no_liabilities():
    assert L.find_liabilities("AAEELLKKAAEELLKKAAEELLKKAA") == []


def test_sequon_ng_and_dg_positions_are_one_based():
    liabs = L.find_liabilities("AANGSAAANATAAAANPTAADGAA")
    sequon = find(liabs, "N-glycosylation sequon")
    assert [e["pos"] for e in sequon] == [3, 9] and [e["motif"] for e in sequon] == ["NGS", "NAT"]       # NPT is not a sequon
    assert all(e["sev"] == "warn" for e in sequon)
    ng = find(liabs, "Deamidation (NG)")
    assert [e["pos"] for e in ng] == [3] and ng[0]["motif"] == "NG"
    dg = find(liabs, "Isomerisation/cleavage (DG)")
    assert [e["pos"] for e in dg] == [21] and dg[0]["sev"] == "warn"


def test_kind_strings_keep_the_substrings_the_pages_match_on():
    allk = " | ".join(e["kind"] for e in L.find_liabilities("CAANGSAADPAAVVVVVVAA"))
    for needle in ("Unpaired", "N-glycosylation", "NG", "DP", "Hydrophobic"):
        assert needle in allk, needle
    patch = L.find_liabilities("AAAAAAAAAA", patches=[{"area": 650.0, "res": [2, 3, 5]}])
    assert patch[0]["kind"] == "Exposed hydrophobic patch" and "Hydrophobic" not in patch[0]["kind"]


def test_overlapping_motifs_are_all_found():
    assert [e["pos"] for e in find(L.find_liabilities("DGDG"), "Isomerisation/cleavage (DG)")] == [1, 3]
    assert [e["pos"] for e in find(L.find_liabilities("NGNG"), "Deamidation (NG)")] == [1, 3]


def test_ns_ds_dp_are_info():
    liabs = L.find_liabilities("AANSAADSAADPAA")
    for k in ("Deamidation (NS)", "Isomerisation/cleavage (DS)", "Isomerisation/cleavage (DP)"):
        e = find(liabs, k)
        assert len(e) == 1 and e[0]["sev"] == "info", k


def test_cysteine_parity_without_structure_and_pairs_with_it():
    assert [e["pos"] for e in find(L.find_liabilities("AACAA"), "Unpaired cysteine")] == [3]
    assert find(L.find_liabilities("AACAACAA"), "Unpaired cysteine") == []
    assert len(find(L.find_liabilities("CAACAACAA"), "Unpaired cysteine")) == 1                  # three Cys: the parity rule flags one
    liabs = L.find_liabilities("CAACAACAAC", ssbonds=[[1, 4]])                                    # with pairs known, a Cys outside a pair is unpaired
    assert [e["pos"] for e in find(liabs, "Unpaired cysteine")] == [7, 10] and all(e["sev"] == "bad" for e in find(liabs, "Unpaired cysteine"))
    assert find(L.find_liabilities("CAAC", ssbonds=[(1, 4)]), "Unpaired cysteine") == []


def test_oxidation_follows_exposure():
    seq = "AAMAAWAAMAAW"
    rsa = [0.0] * 12
    rsa[2], rsa[5] = 0.6, 0.6                                       # M3 and W6 exposed, M9 and W12 buried
    liabs = L.find_liabilities(seq, rsa=rsa)
    met, trp = find(liabs, "Met oxidation"), find(liabs, "Trp oxidation")
    assert [e["pos"] for e in met] == [3] and met[0]["sev"] == "warn" and [e["pos"] for e in trp] == [6] and trp[0]["sev"] == "info"
    liabs = L.find_liabilities(seq)                                 # without exposure data both are info and say so
    assert len(find(liabs, "Met oxidation")) == 2 and all(e["sev"] == "info" for e in find(liabs, "Met oxidation"))
    assert "unknown" in find(liabs, "Met oxidation")[0]["reason"]
    assert all(e["sev"] == "info" for e in find(L.find_liabilities(seq, rsa=[float("nan")] * 12), "Met oxidation"))


def test_hydrophobic_stretch_needs_six_and_follows_exposure():
    seq = "AAAVILMFWAAA"
    assert [e["pos"] for e in find(L.find_liabilities(seq), "Hydrophobic stretch")] == [4]
    assert find(L.find_liabilities("AAAVILMFAAA"), "Hydrophobic stretch") == []                  # only 5
    assert find(L.find_liabilities(seq, rsa=[0.5] * 12), "Hydrophobic stretch")[0]["sev"] == "warn"
    assert find(L.find_liabilities(seq, rsa=[0.0] * 12), "Hydrophobic stretch")[0]["sev"] == "info"


def test_interface_lys_met_and_patches_and_protease():
    seq = "MKAAKMAARAKRAA"
    liabs = L.find_liabilities(seq, contact_residues=[1, 2, 6])
    assert [e["pos"] for e in find(liabs, "Interface Met")] == [1, 6] and [e["pos"] for e in find(liabs, "Interface Lys")] == [2]
    assert find(L.find_liabilities(seq), "Interface Lys") == [] and [e["pos"] for e in find(liabs, "Protease site (furin)")] == [9]
    p = L.find_liabilities("AVLIAAAAAA", patches=[{"area": 520, "res": [2, 3]}, {"area": 320, "res": [4]}, {"area": 100, "res": [5]}])
    assert [(e["pos"], e["sev"]) for e in find(p, "Exposed hydrophobic patch")] == [(2, "warn"), (4, "info")]


def test_dibasic_site_only_when_clearly_exposed():
    seq = "AAKKAAAA"
    assert find(L.find_liabilities(seq), "Protease site (dibasic)") == [] and find(L.find_liabilities(seq, rsa=[0.1] * 8), "Protease site (dibasic)") == []
    assert len(find(L.find_liabilities(seq, rsa=[0.6] * 8), "Protease site (dibasic)")) == 1


def test_nonstandard_and_odd_characters_do_not_raise():
    seq = "AAxUBZ*-NGSAA acd"
    assert find(L.find_liabilities(seq), "N-glycosylation sequon")[0]["pos"] == 9                  # position counts every character
    props = L.sequence_properties(seq)
    assert props["length"] == len(seq) and props["n_cys"] == 1
    assert L.find_liabilities("") == [] and L.sequence_properties("")["length"] == 0 and L.sequence_properties("")["pI"] == 0.0


def test_sequence_properties_known_values():
    p = L.sequence_properties("KKKKKKKKKK")
    assert p["charge"] > 8 and p["pI"] > 10 and p["gravy"] == pytest.approx(-3.9)
    q = L.sequence_properties("DDDDDDDDDD")
    assert q["charge"] < -8 and q["pI"] < 4.5
    r = L.sequence_properties("MCWMKWC")
    assert (r["n_met"], r["n_cys"], r["n_trp"], r["n_lys"]) == (2, 2, 2, 1)


def test_unusual_charge_is_global_and_optional():
    flags = L.global_flags("D" * 14)
    assert len(flags) == 1 and flags[0]["sev"] == "warn" and flags[0]["scope"] == "global" and L.global_flags("AAAAEEKKAAAA") == []
    assert find(L.find_liabilities("D" * 14), "Unusual charge") == []                           # not in the residue-level list unless asked for
    assert find(L.find_liabilities("D" * 14, include_global=True), "Unusual charge")[0]["pos"] == 1
    mild = L.global_flags("D" * 9 + "A" * 4)
    assert len(mild) == 1 and mild[0]["sev"] == "info"


def test_output_shape_and_blob_forms():
    liabs = L.find_liabilities("AANGSAAMC", rsa=[0.5] * 9, contact_residues=[8])
    for e in liabs:
        assert set(e) >= {"kind", "pos", "motif", "sev", "reason", "scope"}
        assert e["sev"] in L.SEVERITIES and isinstance(e["pos"], int) and e["pos"] >= 1 and e["reason"]
    assert liabs == sorted(liabs, key=lambda e: (e["pos"], e["kind"]))
    camp = L.to_campaign(liabs)
    assert all(set(c) == {"k", "p", "m", "s"} for c in camp)
    ins = L.to_inspector(liabs)
    assert all({"kind", "pos", "motif", "sev"} <= set(i) for i in ins)
    json.dumps(camp), json.dumps(ins)
    assert sum(L.severity_counts(liabs).values()) == len(liabs)


def test_the_planted_motifs_of_the_toy_designs_are_found(toy_run1, toy_truth):
    bb = toy_truth["runs"]["run01"]["backbones"]
    design = lambda name, mp: toy_run1.design(f"{bb[name]['id']}_mpnn{mp}")
    seq = design("a1", 7).sequence
    sequon = find(L.find_liabilities(seq), "N-glycosylation sequon")
    assert any(e["motif"] == "NAT" for e in sequon) and all(seq[e["pos"] - 1] == "N" for e in sequon)
    cys = find(L.find_liabilities(design("a2", 2).sequence), "Unpaired cysteine")
    assert len(cys) == 1 and cys[0]["sev"] == "bad" and design("a2", 2).sequence.count("C") == 1
    kinds = {e["kind"] for e in L.find_liabilities(design("a4", 4).sequence)}
    assert {"Deamidation (NG)", "Isomerisation/cleavage (DG)"} <= kinds


def test_toy_designs_give_sane_liability_lists(toy_run1):
    for d in toy_run1.designs:
        liabs = L.find_liabilities(d.sequence, contact_residues=[int(x[1:]) for x in d.interface_residues], include_global=True)
        assert all(1 <= e["pos"] <= d.length and e["sev"] in L.SEVERITIES for e in liabs)
        props = L.sequence_properties(d.sequence)
        assert props["length"] == d.length and props["n_cys"] == d.sequence.count("C") and -10 < props["gravy"] < 4
    # the toy sequences have no free cysteine except the planted one
    assert sum(d.sequence.count("C") for d in toy_run1.designs) == 1
