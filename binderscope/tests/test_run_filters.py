"""The filter set is a property of the run (config.filters_for_run): a run that recorded its own thresholds (BindCraft 2:
campaign_metadata.json) is judged by them, a run that did not keeps the reconstructed BindCraft 1 defaults, and nothing about a
BindCraft 1 run changes. Covers the set, the verdicts (BindCraft's own failed_filters stand), the near-miss rule on a set that
records unmeasured failures, and what the exporter writes about where each threshold came from."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from binder_review import config, export as E, families, model, modes, pipeline as P
from binder_review.config import FilterFailure
from binder_review.readers import bindcraft2


# ------------------------------------------------------------------------------------------------ helpers
@pytest.fixture(scope="module")
def toya(toy_dir, toy_construct):
    return bindcraft2.read_run(toy_dir / "bindcraft2" / "toya", "toya", construct_path=toy_construct)


@pytest.fixture(scope="module")
def toyc(toy_dir):
    return bindcraft2.read_run(toy_dir / "bindcraft2_v103" / "toyc", "toyc")


def _design(did="d1", status="rejected", metrics=None, failed=(), names=(), models_=None):
    d = model.DesignRecord(id=did, run_id="r", backbone_id=did, status=status, binder_name="x", length=60, seed=0, mpnn_index=None,
                           sequence="A" * 60, metrics=dict(metrics or {}), models=dict(models_ or {}), structure_path="x.cif")
    d.failed_filters = list(failed)
    d.extras["failed_filter_names"] = list(names)
    return d


def _run(designs, filters=None, unlimited=()):
    ann = {}
    if filters is not None:
        ann["filters"] = filters
    if unlimited:
        ann["filters_unlimited"] = list(unlimited)
    return model.RunData(run_id="r", label="r", reader="bindcraft2", source_dir=".", settings=model.RunSettings("r", "r"),
                         target=model.TargetConstruct("r"), designs=list(designs), annotations=ann)


def _f(name, op, thr, mandatory=True, source="campaign_metadata"):
    return {"name": name, "op": op, "threshold": thr, "higher": op == ">=", "mandatory": mandatory, "source": source}


CAMPAIGN = [_f("i_pTM", ">=", 0.7), _f("pTM", ">=", 0.55), _f("i_pAE", "<=", 0.35), _f("Binder_RMSD", "<=", 3.5)]
OK = {"i_pTM": 0.8, "pTM": 0.8, "i_pAE": 0.2, "Binder_RMSD": 1.0}


# ------------------------------------------------------------------------------------------------ the set
def test_a_run_that_records_nothing_keeps_the_reconstructed_defaults(toy_run1, toy_bc2_b):
    for run in (toy_run1, toy_bc2_b):                                         # BindCraft 1; BindCraft 2 without a metadata file
        fs = config.filters_for_run(run)
        assert fs is config.DEFAULT_FILTER_SET and fs.rules == config.DEFAULT_FILTERS and fs.source == "default" and not fs.from_campaign
        assert fs.unevaluated == () and fs.unlimited == ()
    assert {r.provenance for r in config.DEFAULT_FILTERS} <= {"counter-verified", "bounded", "memory"}      # none of them claims to be the campaign's


def test_a_campaign_set_is_what_the_metadata_recorded(toya):
    fs = config.filters_for_run(toya)
    assert fs.source == "campaign" and fs.from_campaign and {r.provenance for r in fs.rules} == {config.PROVENANCE_CAMPAIGN}
    by = {r.stem: r for r in fs.rules}
    assert by["i_pAE"].threshold == 0.3 and not by["i_pAE"].higher_is_better              # the toy campaign's number, not BindCraft 1's 0.35
    assert by["i_pTM"].threshold == 0.7 and by["pTM"].threshold == 0.55 and by["Binder_RMSD"].threshold == 3.5
    assert by["pLDDT"].threshold == 0.7 and by["pLDDT"].source == "settings.min_plddt_final"   # from min_plddt_final, not an entry of settings.filters
    assert by["i_pTM"].source == "campaign_metadata" and by["i_pTM"].mandatory is True
    assert all(r.per_model is None for r in fs.rules)                                      # the CSV carries no per-model values to check
    assert {r.stem for r in fs.rules if r.judged_per_model} == {"pLDDT", "pTM", "i_pTM", "Unbound_Binder_pLDDT"}
    assert "Surface_Hydrophobicity" not in by and "Binder_pLDDT" not in by                  # BindCraft 1 rules this campaign never applied
    assert all("recorded" in r.note for r in fs.rules)


def test_an_infinite_threshold_is_no_limit_not_a_rule_and_not_unevaluated(toya):
    fs = config.filters_for_run(toya)
    assert "Surface_Hydrophobicity" in fs.unlimited
    assert "Surface_Hydrophobicity" not in {r.stem for r in fs.rules} | {u.name for u in fs.unevaluated}
    assert not any(r.threshold in (float("inf"), float("-inf")) for r in fs.rules)


def test_a_threshold_on_a_metric_that_is_not_carried_is_recorded_as_unevaluated(toya):
    fs = config.filters_for_run(toya)
    un = {u.name: u for u in fs.unevaluated}
    assert "Hotspot_Contact_Fraction" in un and un["Hotspot_Contact_Fraction"].mandatory is False and un["Hotspot_Contact_Fraction"].op == ">="
    assert un["Hotspot_Contact_Fraction"].reason and un["Hotspot_Contact_Fraction"].source == "campaign_metadata"
    assert "Hotspot_Contact_Fraction" not in {r.stem for r in fs.rules}


def test_a_campaign_name_that_is_not_the_stem_is_stored_under_the_stem(toya):
    rule = {r.stem: r for r in config.filters_for_run(toya).rules}["Interface_Residues"]
    assert rule.metric == "n_InterfaceResidues" and rule.value_stem == "n_InterfaceResidues" and rule.threshold == 3
    fails = config.check_filters({"n_InterfaceResidues": 2.0}, {}, None, [rule])
    assert [(f.stem, f.value, f.gap) for f in fails] == [("Interface_Residues", 2.0, 1.0)]    # the failure carries the campaign's name
    assert config.check_filters({"n_InterfaceResidues": 3.0}, {}, None, [rule]) == []


def test_a_recorded_filter_nothing_can_be_evaluated_on_is_unevaluated_not_dropped():
    run = _run([_design(metrics=OK)], [_f("pTM", ">=", 0.55), _f("Backbone_Clashes", "<=", 0.0), _f("Nonexistent", ">=", 1.0)])
    fs = config.filters_for_run(run)
    assert [r.stem for r in fs.rules] == ["pTM"]
    assert {u.name for u in fs.unevaluated} == {"Backbone_Clashes", "Nonexistent"} and all(u.reason for u in fs.unevaluated)
    # a mapped metric that no design carries cannot be evaluated either
    run2 = _run([_design(metrics={"i_pTM": 0.8})], [_f("pTM", ">=", 0.55)])
    assert config.filters_for_run(run2).rules == () and config.filters_for_run(run2).unevaluated[0].name == "pTM"
    # one that Binderscope stores on another scale is not compared against the recorded threshold
    run3 = _run([_design(metrics={"Interface_Hydrophobicity": 30.0})], [_f("Interface_Hydrophobicity", "<=", 0.5)])
    assert config.filters_for_run(run3).rules == () and "multiplied by 100" in config.filters_for_run(run3).unevaluated[0].reason


def test_a_campaign_that_recorded_only_no_limit_is_a_campaign_with_no_rules():
    fs = config.filters_for_run(_run([_design(metrics=OK)], None, unlimited=["Surface_Hydrophobicity"]))
    assert fs.from_campaign and fs.rules == () and fs.unlimited == ("Surface_Hydrophobicity",)
    assert config.as_filter_set(config.DEFAULT_FILTERS) is config.DEFAULT_FILTER_SET
    assert config.as_filter_set(config.DEFAULT_FILTERS[:2]).source == "explicit"


# ------------------------------------------------------------------------------------------------ verdicts
def test_the_campaign_verdict_is_judged_by_the_campaign_not_by_the_defaults():
    # Surface_Hydrophobicity 0.42 passes a campaign that has no such rule; BindCraft 1's default would fail it
    d = _design("acc", "accepted", metrics={**OK, "Surface_Hydrophobicity": 0.42})
    run = _run([d], CAMPAIGN)
    assert modes.accepted_failures(run) == {}
    assert [x for x in modes.accepted_failures(run, config.DEFAULT_FILTERS)["acc"] if x["stem"] == "Surface_Hydrophobicity"]


def test_where_bindcraft_said_a_design_failed_it_failed():
    # BindCraft failed pTM on one validation model; the CSV mean (0.69) sits above the threshold (0.55)
    rec = FilterFailure("pTM", "Model", 0.69, 0.55, True, 0.0)
    d = _design(metrics={**OK, "pTM": 0.69}, failed=[rec], names=["pTM"])
    run = _run([d], CAMPAIGN)
    assert [f.stem for f in modes.design_failures(d, config.filters_for_run(run))] == ["pTM"]
    assert modes.design_failures(d, config.filters_for_run(run))[0] is rec                      # BindCraft's failure, as the reader recorded it
    assert modes.design_failures(d, config.filters_for_run(run).rules) == []                    # a plain re-evaluation of the metrics alone would lose it
    out = modes.rescue_records(run)["d1"]
    assert [x["stem"] for x in out["fail"]] == ["pTM"] and out["fail"][0]["measured"] is False and out["fail"][0]["reported"] is True
    assert out["fail"][0]["rel"] is None and out["fail"][0]["gap"] == 0.0 and out["fail"][0]["scope"] == "Model"


def test_a_miss_that_is_not_measured_is_not_a_near_miss():
    rec = FilterFailure("pTM", "Model", 0.69, 0.55, True, 0.0)
    d = _design(metrics={**OK, "pTM": 0.69}, failed=[rec], names=["pTM"])
    nm = modes.rescue_records(_run([d], CAMPAIGN))["d1"]["nm"]
    assert nm["near"] is False and nm["score"] == 0.0 and nm["unmeasured"] == ["pTM"] and nm["stems"] == ["pTM"] and nm["n_failed"] == 1
    assert set(nm) >= {"n_failed", "stems", "worst_rel", "score", "near"}                       # the contract keys are all there
    # the same design judged without BindCraft's verdict would read as a perfect near miss; that is what is being avoided
    assert model.near_miss_record([rec])["near"] is True


def test_a_measured_single_miss_is_a_near_miss_and_a_second_one_is_not():
    rec = FilterFailure("i_pTM", "Average", 0.62, 0.7, True, 0.08)
    d = _design(metrics={**OK, "i_pTM": 0.62}, failed=[rec], names=["i_pTM"])
    out = modes.rescue_records(_run([d], CAMPAIGN))["d1"]
    assert out["nm"]["near"] is True and out["nm"]["stems"] == ["i_pTM"] and out["fail"][0]["measured"] is True
    assert out["nm"]["worst_rel"] == pytest.approx(0.114, abs=1e-3) and "unmeasured" not in out["nm"]
    # BindCraft stopped at i_pTM; the CSV mean shows Binder_RMSD above its limit as well: measured here, not named by BindCraft
    d2 = _design("d2", metrics={**OK, "i_pTM": 0.62, "Binder_RMSD": 4.0}, failed=[rec], names=["i_pTM"])
    out2 = modes.rescue_records(_run([d2], CAMPAIGN))["d2"]
    assert [(f["stem"], f["reported"], f["measured"]) for f in out2["fail"]] == [("i_pTM", True, True), ("Binder_RMSD", False, True)]
    assert out2["nm"]["near"] is False and out2["nm"]["n_failed"] == 2
    # a stem BindCraft named is listed once, whatever the metrics say
    d3 = _design("d3", metrics={**OK, "i_pTM": 0.62}, failed=[rec], names=["i_pTM"])
    assert [f.stem for f in modes.design_failures(d3, config.filters_for_run(_run([d3], CAMPAIGN)))] == ["i_pTM"]


def test_a_failure_bindcraft_named_that_cannot_be_scored_is_not_lost():
    d = _design(metrics=OK, names=["Backbone_Clashes (not measured)"])
    run = _run([d], CAMPAIGN + [_f("Backbone_Clashes", "<=", 0.0)])
    assert modes.unscored_failures(d, []) == ["Backbone_Clashes (not measured)"]
    out = modes.rescue_records(run)["d1"]
    assert out["fail"] == [] and out["nm"]["near"] is False and out["nm"]["stems"] == ["Backbone_Clashes"] and out["nm"]["unmeasured"] == ["Backbone_Clashes"]
    summary = modes.rescue_summary(run)
    assert summary["no_failure"] == 0 and summary["filters"]["unevaluated"] == ["Backbone_Clashes"] and summary["n_near"] == 0


def test_the_near_miss_count_and_the_summary_use_one_set(toya):
    rec = modes.rescue_records(toya)
    summary = modes.rescue_summary(toya)
    assert summary["n_near"] == sum(1 for v in rec.values() if v["nm"] and v["nm"]["near"])
    assert [p["stem"] for p in summary["per_stem"]] == [r.stem for r in config.filters_for_run(toya).rules]
    assert {p["provenance"] for p in summary["per_stem"]} == {"campaign"}
    assert modes.accepted_failures(toya) == {}                                   # every design BindCraft passed passes its own thresholds
    # every failure BindCraft recorded is listed, and a name it gave that no failure represents makes the design not near
    for d in toya.rejected_with_structure():
        have = {f["stem"] for f in rec[d.id]["fail"]}
        assert {f.stem for f in d.failed_filters} <= have
        left = {modes._bare_name(n) for n in d.extras.get("failed_filter_names", [])} - have
        if left:
            assert rec[d.id]["nm"]["near"] is False and left <= set(rec[d.id]["nm"]["stems"])


# ------------------------------------------------------------------------------------------------ BindCraft 1 does not change
def _old_rescue_records(run, rules=config.DEFAULT_FILTERS):
    """What modes.rescue_records did before the filter set became a property of the run."""
    out = {}
    for d in run.rejected_with_structure():
        fl = config.check_filters(d.metrics, d.models, d.interface_aas, rules)
        nm = model.near_miss_record(fl)
        if nm:
            nm["worst_rel"] = round(nm["worst_rel"], 3)
        out[d.id] = {"fail": [modes._round_fail(model.failure_to_dict(f)) for f in fl], "nm": nm}
    return out


def test_a_bindcraft1_run_gets_exactly_the_old_records(toy_run1, toy_run2):
    for run in (toy_run1, toy_run2):
        assert modes.rescue_records(run) == _old_rescue_records(run)
        assert json.dumps(modes.rescue_records(run), sort_keys=True) == json.dumps(_old_rescue_records(run), sort_keys=True)
        assert modes.rescue_records(run) == modes.rescue_records(run, config.DEFAULT_FILTERS) == modes.rescue_records(run, config.DEFAULT_FILTER_SET)
        assert all("reported" not in f and "measured" not in f for v in modes.rescue_records(run).values() for f in v["fail"])
        assert all("unmeasured" not in (v["nm"] or {}) for v in modes.rescue_records(run).values())
        s = modes.rescue_summary(run)
        assert "filters" not in s and [p["stem"] for p in s["per_stem"]] == [r.stem for r in config.DEFAULT_FILTERS]
        assert {p["provenance"] for p in s["per_stem"]} <= {"counter-verified", "bounded", "memory"}


def test_a_bindcraft1_epoch_still_hashes_the_default_rules(toy_run1):
    comp = families.epoch_components(toy_run1, 59)
    assert comp["filters"]["rules"] == [[r.stem, r.threshold, r.op, r.per_model] for r in config.DEFAULT_FILTERS]


def test_the_epoch_of_a_campaign_run_follows_its_own_thresholds(toya, toy_run1):
    rules = families.epoch_components(toya, 59)["filters"]["rules"]
    assert ["i_pAE", 0.3, "<=", None] in rules and ["Surface_Hydrophobicity", 0.35, "<=", 0.35] not in rules
    assert families.epoch_hash(toya, 59) != families.epoch_hash(toy_run1, 59)


# ------------------------------------------------------------------------------------------------ what is exported
def test_exported_rules_say_where_the_threshold_came_from(toya, toy_run1):
    camp = [E.filter_rule_dict(r) for r in config.filters_for_run(toya).rules]
    assert {r["provenance"] for r in camp} == {"campaign"} and all({"source", "mandatory", "judged_per_model", "value_stem"} <= set(r) for r in camp)
    by = {r["stem"]: r for r in camp}
    assert by["i_pTM"]["metric"] == "iptm_b" and by["i_pTM"]["judged_per_model"] is True and by["i_pAE"]["judged_per_model"] is False
    assert by["Interface_Residues"]["value_stem"] == "n_InterfaceResidues" and by["pLDDT"]["source"] == "settings.min_plddt_final"
    assert all(r["note"] and r["per_model"] is None for r in camp)
    # a reconstructed default has exactly the shape it always had
    default = [E.filter_rule_dict(r) for r in config.filters_for_run(toy_run1).rules]
    assert default == [model.filter_rule_to_dict(r) for r in config.DEFAULT_FILTERS]
    assert all(set(r) == {"stem", "metric", "threshold", "op", "per_model", "provenance", "note"} for r in default)


def test_the_filter_set_block_and_the_note_of_a_campaign_run(toya):
    fs = config.filters_for_run(toya)
    blk = E.filter_set_dict(toya, fs)
    assert blk["source"] == "campaign" and blk["n_rules"] == len(fs.rules) and blk["unlimited"] == ["Surface_Hydrophobicity"]
    assert [u["name"] for u in blk["unevaluated"]] == [u.name for u in fs.unevaluated] and "screens, not verdicts" in blk["note"]
    assert E._filter_notes(fs) and "recorded itself" in E._filter_notes(fs)[0]
    assert E._filter_notes(config.DEFAULT_FILTER_SET) == []


def test_the_manifest_lists_the_default_rules_unless_a_run_records_its_own(toy_run1, toy_run2, toya):
    default = E.project_filter_rules(SimpleNamespace(runs={"run01": toy_run1, "run02": toy_run2}))
    assert default == [model.filter_rule_to_dict(r) for r in config.DEFAULT_FILTERS]
    mixed = E.project_filter_rules(SimpleNamespace(runs={"run01": toy_run1, "toya": toya}))
    assert {r["run"] for r in mixed} == {"run01", "toya"}
    assert {r["provenance"] for r in mixed if r["run"] == "toya"} == {"campaign"} and {r["provenance"] for r in mixed if r["run"] == "run01"} <= {"counter-verified", "bounded", "memory"}


def test_the_src_repeats_a_value_under_the_campaigns_own_name():
    d = _design(metrics={"n_InterfaceResidues": 12.0, "i_pTM": 0.8})
    run = _run([d], CAMPAIGN)
    src = E._src(d, run, {"Interface_Residues": "n_InterfaceResidues", "SS_pLDDT": "ss_pLDDT"})
    assert src["average"] == {"n_InterfaceResidues": 12.0, "i_pTM": 0.8, "Interface_Residues": 12.0}
    assert E._src(d, run)["average"] == {"n_InterfaceResidues": 12.0, "i_pTM": 0.8}


# ------------------------------------------------------------------------------------------------ the whole pipeline on a campaign
@pytest.fixture(scope="module")
def toyc_blobs(toy_dir, tmp_path_factory):
    spec = P.ProjectSpec(name="bc2", runs=[P.RunSpec(toy_dir / "bindcraft2_v103" / "toyc", "toyc")],
                         target_pdb=toy_dir / "construct" / "toy_construct.pdb", reference_cif=toy_dir / "reference" / "toy_target.cif", jobs=1)
    project = P.run_pipeline(spec, tmp_path_factory.mktemp("bc2_work"), log=P.Log(quiet=True))
    return project, E.build_blobs(project)


def test_a_campaign_exports_its_own_thresholds_with_their_provenance(toyc_blobs):
    project, blobs = toyc_blobs
    run = blobs.campaign["runs"][0]
    assert run["filters"] and {f["provenance"] for f in run["filters"]} == {"campaign"}
    assert run["filter_set"]["source"] == "campaign" and any("recorded itself" in n for n in run["notes"])
    assert blobs.manifest["runs"][0]["filters"] == run["filters"] and blobs.manifest["runs"][0].get("filter_set") == run["filter_set"]
    assert all(f["run"] == "toyc" and f["provenance"] == "campaign" for f in blobs.manifest["thresholds"]["filters"])
    by = {f["stem"]: f for f in run["filters"]}
    assert by["i_pAE"]["threshold"] == 0.3 and "Surface_Hydrophobicity" not in by


def test_the_exported_verdicts_agree_with_the_campaigns_own_failed_filters(toyc_blobs):
    project, blobs = toyc_blobs
    run = project.runs["toyc"]
    designs = {d["name"]: d for d in blobs.campaign["designs"]}
    assert not any(d["fail"] for d in designs.values() if d["status"] == "accepted")
    rej = [d for d in run.rejected_with_structure()]
    assert rej
    for r in rej:
        out = designs[r.id]
        stems = [f["stem"] for f in out["fail"]]
        named = {n.replace(" (not measured)", "") for n in r.extras.get("failed_filter_names", [])}
        assert named <= set(stems) | set(out["nm"]["stems"]), r.id                                  # what BindCraft named is there
        assert len(stems) == len(set(stems)) or all(f["scope"] != "Average" for f in out["fail"])
        measured = all(f["measured"] for f in out["fail"])
        one = len(set(stems)) == 1
        assert out["nm"]["near"] == bool(one and measured and max(f["rel"] for f in out["fail"]) <= model.NEAR_MISS_MAX_REL), r.id
    # the highlight counts the same rescues the designs carry
    n_near = sum(1 for d in designs.values() if (d.get("nm") or {}).get("near"))
    texts = [h["text"] for h in blobs.campaign["highlights"] if h["id"] == "near_misses"]
    assert (not texts and n_near == 0) or texts[0].startswith(f"{n_near} of the {len(rej)} rejected designs")


def test_a_bindcraft1_project_exports_what_it_always_did(toy_blobs):
    for run in toy_blobs.campaign["runs"]:
        assert "filter_set" not in run
        assert all(set(f) == {"stem", "metric", "threshold", "op", "per_model", "provenance", "note"} for f in run["filters"])
        assert [f["stem"] for f in run["filters"]] == [r.stem for r in config.DEFAULT_FILTERS]
    assert toy_blobs.manifest["thresholds"]["filters"] == [model.filter_rule_to_dict(r) for r in config.DEFAULT_FILTERS]
    for d in toy_blobs.campaign["designs"]:
        assert all(set(f) <= {"stem", "metric", "scope", "value", "threshold", "op", "gap", "rel"} for f in d["fail"])
