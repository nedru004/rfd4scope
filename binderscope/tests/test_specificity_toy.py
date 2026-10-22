"""Specificity panel (binder_review.specificity): alignments, grades, identity at residues and the homolog blob, on toy sequences and on
the toy reference (orthologs and paralogs from the toy table in reference/refdata, no network)."""
from __future__ import annotations

import json
import random
import socket

import numpy as np
import pytest

from binder_review import refdata as rd
from binder_review import specificity as sp
from toydata.reference import TOY_ACCESSIONS
from toydata.testing import load_validator, repo_root, toy_profile

REPO = repo_root()


@pytest.fixture(autouse=True)
def offline(monkeypatch, toy_refdata):
    monkeypatch.setenv(rd.OFFLINE_ENV, "1")
    monkeypatch.setenv(rd.CACHE_ENV, str(toy_refdata))


@pytest.fixture(scope="module")
def panel(toy_refdata):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(rd.OFFLINE_ENV, "1")
        mp.setenv(rd.CACHE_ENV, str(toy_refdata))
        return sp.build_panel(list(range(60, 170)), toy_profile(), accession="Z0TOY1", include_consurf=False, observed=(1, 250))


@pytest.fixture(scope="module")
def hom(panel):
    return sp.to_homolog_json(panel, date="2026-01-01")


# ----------------------------------------------------------------------------- toy alignments
def test_toy_alignment_identity_gaps_and_insertions():
    ref = "MKTAYIAKQRQISFVKSHFSRQ"
    assert sp.align_map(ref, ref) == ref
    other = ref[:5] + "W" + ref[6:10] + ref[11:15] + "GGG" + ref[15:]          # one residue changed, one deleted, one inserted
    m = sp.align_map(ref, other)
    assert len(m) == len(ref) and m[5] == "W" and m[0:5] == ref[0:5] and m.count("-") >= 1 and "GGG" not in m


def test_columns_and_grades_are_monotone():
    rng = np.random.default_rng(3)
    cons, cover = rng.random(60), rng.choice([0.4, 0.8, 1.0], 60)
    g = sp.grades_from(cons, cover)
    order = np.argsort(cons * np.maximum(cover, sp.COVER_FLOOR), kind="stable")
    assert all(g[a] <= g[b] for a, b in zip(order[:-1], order[1:])) and g.min() >= 1 and g.max() <= 9 and set(range(1, 10)) <= set(g)
    tie = sp.grades_from(np.array([1, 1, 1, 0.5, 0.2, 0.1]), np.ones(6))
    assert len(set(tie[:3])) == 1                                                  # equal scores never split


def test_invariant_majority_grades_nine_and_variable_ones_spread():
    cons = np.array([1.0] * 80 + [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05] * 2)
    g = sp.grades_from(cons, np.ones(cons.size))
    assert set(g[:80]) == {9} and g[80:].max() <= 8 and g[80:].min() == 1 and list(g[80:90]) == sorted(g[80:90], reverse=True)


def test_column_stats_toy():
    ref, maps = "ACDE", ["ACDE", "ACEE", "AC-E"]
    cons, ident, cover = sp.column_stats(ref, [1, 2, 3, 4], maps)
    assert cons[0] == pytest.approx(1.0, abs=1e-6) and cons[3] == pytest.approx(1.0, abs=1e-6) and cons[2] < cons[0]
    assert cover.tolist() == pytest.approx([1, 1, 2 / 3, 1]) and ident[2] == pytest.approx(0.5)
    _, _, cover2 = sp.column_stats(ref, [1, 2, 3, 4], maps, usable=[[True] * 4, [True, True, False, True], [True] * 4])
    assert cover2[2] == pytest.approx(1 / 3)                                       # low-confidence residues can be excluded as gaps


def test_build_panel_from_sequences_toy():
    ref = "MKTAYIAKQRQISFVKSHFSRQ" * 3
    mut = list(ref)
    mut[30] = "W"
    panel = sp.build_panel_from_sequences(
        ref, [{"sequence": ref, "key": "a", "acc": "A1"}, {"sequence": "".join(mut), "key": "b", "acc": "B1"}],
        [{"sequence": "".join(random.Random(5).sample(ref, len(ref))), "key": "P1", "acc": "P1", "gene": "P1"}], range(10, 50))
    h = sp.to_homolog_json(panel)
    assert h["n_orth"] == 2 and len(h["cons"]) == 40
    i = h["resis"].index(31)
    assert h["ident"][i] == 0.5 and h["cons"][i] < 1 and h["grade"][i] < h["grade"][h["resis"].index(20)]
    out = sp.identity_at(panel, "b", [30, 31, 32])
    assert out["identical"] == 2 and out["differing"] == [f"{ref[30]}31W"] and sp.identity_at(panel, "a", range(10, 50))["identity"] == 1.0
    with pytest.raises(ValueError):
        sp.identity_at(panel, "a", [500])


def test_low_confidence_flags_unrelated_but_not_identical_alignment():
    rng = random.Random(11)
    ref = "".join(rng.choice(sp.AA) for _ in range(300))
    assert not sp.low_confidence(ref, ref).any()
    m = sp.align_map(ref, "".join(rng.sample(ref, len(ref))))
    aligned = np.array([c != "-" for c in m])
    assert sp.low_confidence(ref, m).sum() / aligned.sum() > 0.4 and not sp.low_confidence(ref, "-" * len(ref)).any()


def test_ortholog_ranking_is_generic():
    rng = random.Random(2)
    ref = "".join(rng.choice(sp.AA) for _ in range(70))
    far = "GS" * 35
    row = lambda acc, seq, **kw: {"accession": acc, "entry_name": acc, "reviewed": False, "gene_names": ["GENEX"], "organism": "o", "taxon_id": 1,   # noqa: E731
                                  "length": len(seq), "protein_name": "p", "fragment": False, "sequence": seq, **kw}
    rows = [row("A1", ref[:-2] + "AA"), row("A2", ref), row("A3", far), row("A4", ref, fragment=True), row("A5", ref, reviewed=True)]
    ranked = rd.rank_ortholog_candidates(rows, ref, gene="genex")
    assert [r["accession"] for r in ranked] == ["A5", "A2", "A1"]                    # reviewed first, then identity; the unrelated and the fragment are out
    assert ranked[0]["identity_to_reference"] == 1.0 and ranked[0]["gene_matches"] is True and "identity_to_paralog" not in ranked[0]
    assert rd.rank_ortholog_candidates(rows, ref, ref) == []                         # as close to the paralog as to the reference: not an ortholog
    assert rd.rank_ortholog_candidates(rows, ref, far)[0]["identity_to_paralog"] < 0.5
    assert rd.rank_ortholog_candidates(rows, ref, min_length=len(ref) + 1) == []


# ----------------------------------------------------------------------------- the toy panel
def test_panel_members_accessions_and_lengths(panel):
    assert panel.reference["acc"] == "Z0TOY1" and len(panel.ref_seq) == 250
    assert {m.key: (m.acc, m.length) for m in panel.orthologs} == {"orth1": ("Z0TOY2", 250), "orth2": ("Z0TOY3", 250), "orth3": ("Z0TOY4", 250)}
    assert {m.key: (m.acc, m.length) for m in panel.paralogs} == {"PARA1": ("Z0TOY5", 250), "PARA2": ("Z0TOY6", 250)}
    assert [m.key for m in panel.paralogs] == ["PARA1", "PARA2"]
    assert [m.optional for m in panel.orthologs] == [False, False, True] and panel.warnings == []
    assert panel.observed == (1, 250) and panel.resis == list(range(60, 170))


def test_identities_follow_how_the_related_sequences_were_made(panel):
    ident = {m.key: m.identical_count(panel.ref_seq) / len(panel.ref_seq) for m in panel.members}
    assert ident["orth1"] == pytest.approx(0.92, abs=0.04) and ident["orth2"] == pytest.approx(0.82, abs=0.05) and ident["orth3"] == pytest.approx(0.77, abs=0.05)
    assert ident["PARA1"] == pytest.approx(0.46, abs=0.06) and ident["PARA2"] == pytest.approx(0.30, abs=0.06)
    assert ident["orth1"] > ident["orth2"] > ident["orth3"] > ident["PARA1"] > ident["PARA2"]


def test_paralogs_are_matched_by_structure_and_orthologs_by_sequence(panel, hom):
    assert {m.key: m.aligner for m in panel.members} == {"orth1": "sequence", "orth2": "sequence", "orth3": "sequence", "PARA1": "structure", "PARA2": "structure"}
    info = hom["generated"]["params"]["paralog_alignment"]
    assert info["method"].startswith("TM-align") and set(info["members"]) == {"PARA1", "PARA2"} and all(v["tm"] > 0.9 for v in info["members"].values())
    # the toy models carry the reference backbone, so the structural map is the identity map and agrees with the substitutions
    for m in panel.paralogs:
        assert sum(1 for a, b in zip(m.full_map, panel.ref_seq) if a == b) / 250 == pytest.approx(m.identical_count(panel.ref_seq) / 250, abs=0.03)


def test_orthologs_conserved_where_they_should_be(panel, hom):
    cons = np.array(hom["cons"])
    assert len(cons) == 110 and 0.6 < cons.mean() < 1.0 and hom["n_orth"] == 3
    assert min(hom["grade"]) >= 1 and max(hom["grade"]) <= 9 and len(set(hom["grade"])) > 3
    assert hom["n_par"] == 2 and hom["par_keys"] == ["PARA1", "PARA2"] and len(hom["par_grade"]) == 110
    # identity at residues: the member and the reference differ exactly where the sequences differ
    first = panel.member("orth1")
    diff = [i + 1 for i, (a, b) in enumerate(zip(panel.ref_seq, first.full_map)) if a != b and b != "-" and 60 <= i + 1 <= 169]
    out = sp.identity_at(panel, "orth1", list(range(60, 170)))
    assert len(out["differing"]) == len(diff) and out["n"] == 110


def test_hotspot_identity_table(panel, toy_truth):
    hot = toy_truth["hotspots_reference"]
    rows = sp.hotspot_identity_table(panel, hot)
    assert len(rows) == 2 * 5 and {r["run"] for r in rows} == {"run01", "run02"} and {r["kind"] for r in rows} == {"ortholog", "paralog"}
    r = next(x for x in rows if x["run"] == "run02" and x["key"] == "orth1")
    assert len(r["hotspots"]) == 4 and len(r["aligned"]) == 4 and r["n"] == 4 and 0 <= r["identity"] <= 1


def test_homolog_blob_is_valid_and_consistent(hom, panel):
    vs = load_validator()
    rep = vs.check_homolog(hom, profile=toy_profile())
    assert rep.ok, rep.errors
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO / "docs" / "schema" / "homolog.schema.json").read_text())
    jsonschema.Draft202012Validator(schema, registry=vs.registry()).validate(json.loads(json.dumps(hom)))
    assert hom["resis"] == list(range(60, 170)) and hom["seq"] == panel.seq and hom["subject"] == "Toy target" and hom["show"] == ["orth1", "orth2"]
    assert [s[2] for s in hom["species"]] == ["orth1", "orth2", "orth3"]
    assert max(len(o["map"]) for o in hom["orth"] + hom["par"]) == len(hom["resis"])          # only the cut to the target residues is published


def test_blob_is_deterministic_and_has_no_member_sequence(hom, panel, toy_refdata):
    again = sp.to_homolog_json(panel, date="2026-01-01")
    assert json.dumps(hom, sort_keys=True) == json.dumps(again, sort_keys=True)
    text = json.dumps(hom)
    for key in ("orth1", "PARA1"):
        full = panel.member(key).full_map.replace("-", "")
        assert full[:40] not in text                                                        # no stretch of a member sequence beyond the cut


def test_no_network_during_build(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    sp.build_panel(list(range(60, 170)), toy_profile(), accession="Z0TOY1", include_consurf=False)


def test_profile_selects_the_panel():
    prof = {"specificity": {"subject": "Toy", "kind": "host", "show": ["orth2"],
                            "species": [{"match": "acc:Z0TOY3", "label": "Two", "key": "orth2"}, {"match": "acc:Z0TOY2", "label": "One", "key": "orth1"}],
                            "paralogs": {"order": ["PARA2", "PARA1"]}}}
    h = sp.to_homolog_json(sp.build_panel([100, 101, 102, 150], prof, accession="Z0TOY1"))
    assert [o["key"] for o in h["orth"]] == ["orth2", "orth1"] and h["n_orth"] == 2 and h["show"] == ["orth2"]
    assert [p["gene"] for p in h["par"]][:2] == ["PARA2", "PARA1"] and h["resis"] == [100, 101, 102, 150] and len(h["seq"]) == 4
    prof["specificity"]["species"].append({"match": "acc:Z0TOY9", "label": "X", "key": "x"})          # not in the cache, offline: skipped with a warning
    assert any("Z0TOY9" in w for w in sp.build_panel([100], prof, accession="Z0TOY1").warnings)


def test_a_panel_needs_an_accession_and_takes_paralog_members_from_the_profile():
    prof = {"specificity": {"species": [{"match": "acc:Z0TOY2", "label": "One", "key": "orth1"}],
                            "paralogs": {"members": [{"acc": "Z0TOY5", "gene": "PARA1"}, {"acc": "Z0TOY6", "gene": "PARA2", "optional": True}]}}}
    with pytest.raises(ValueError, match="accession"):
        sp.build_panel([100, 101], prof, panel={})
    panel = sp.build_panel([100, 101, 102], prof, accession="Z0TOY1", panel={})                    # panel={}: nothing from the panel file of the cache
    assert [m.key for m in panel.orthologs] == ["orth1"] and [m.key for m in panel.paralogs] == ["PARA1", "PARA2"]
    assert [m.optional for m in panel.paralogs] == [False, True] and panel.observed is None
    only_orth = {"specificity": {"species": prof["specificity"]["species"]}}
    assert sp.build_panel([100], only_orth, accession="Z0TOY1").paralogs == []                    # no paralogs block: no paralogs
    named = {"specificity": {"species": [{"match": "Toyus nonexistens", "label": "Nope", "key": "nope"}]}}
    assert any("Toyus nonexistens" in w for w in sp.build_panel([100], named, accession="Z0TOY1", panel={}).warnings)


def test_default_species_come_from_the_panel_table(toy_refdata):
    got = sp.default_species(rd.get_panel())
    assert [g["match"] for g in got] == ["acc:Z0TOY2", "acc:Z0TOY3", "acc:Z0TOY4"] and [bool(g.get("optional")) for g in got] == [False, False, True]
    assert [g["label"] for g in got] == ["Ortholog 1", "Ortholog 2", "Ortholog 3"] and rd.get_panel()["target"]["accession"] == "Z0TOY1"
    assert set(TOY_ACCESSIONS) == {"target", "orth1", "orth2", "orth3", "PARA1", "PARA2"}


def test_the_default_profile_asks_for_no_panel_and_its_empty_blob_is_valid():
    from binder_review import pipeline

    default = json.loads((pipeline.profiles_dir() / "default.profile.json").read_text())
    assert not sp.panel_requested(default) and sp.panel_requested(toy_profile())
    assert not sp.panel_requested({"specificity": {"species": [], "paralogs": None}}) and sp.panel_requested({"specificity": {"paralogs": {"order": ["X"]}}})
    blob = sp.empty_homolog_json([10, 11, 12], {10: "A", 11: "C", 12: "D"}, subject="Target")
    assert blob["seq"] == "ACD" and blob["n_orth"] == 0 and blob["orth"] == [] and blob["par"] == [] and blob["species"] == []
    rep = load_validator().check_homolog(blob, profile=default)
    assert rep.ok, rep.errors


# ----------------------------------------------------------------------------- in the pipeline
def test_the_pipeline_panel_matches_the_builder(toy_panel_project, hom):
    blob = toy_panel_project.homolog
    assert blob["n_orth"] == 3 and blob["n_par"] == 2 and blob["resis"] == list(range(60, 170)) and blob["seq"] == hom["seq"]
    assert blob["cons"] == hom["cons"] and [o["key"] for o in blob["orth"]] == ["orth1", "orth2", "orth3"]
    assert toy_panel_project.network_requests == 0 and toy_panel_project.frame.meta["numbering"]["accession"] == "Z0TOY1"
    assert toy_panel_project.frame.uniprot_sequence == toy_panel_project.frame.chain_a.sequence        # the toy record is the reference chain


def test_the_default_project_has_an_empty_panel(toy_project):
    blob = toy_project.homolog
    assert blob["n_orth"] == 0 and blob["orth"] == [] and blob["par"] == [] and blob["note"].startswith("No specificity panel")


def test_exported_panel_flags_and_highlight(toy_panel_blobs):
    caps = toy_panel_blobs.profile["caps"]
    assert caps["species_panel"] is True and caps["paralog_panel"] is True
    assert toy_panel_blobs.homolog["n_orth"] == 3 and toy_panel_blobs.campaign["profile"]["caps"]["species_panel"] is True
    assert toy_panel_blobs.manifest["privacy"]["network_requests"] == 0
