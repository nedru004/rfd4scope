"""BindCraft default filters (binder_review.config): the rule table, inclusive thresholds, gaps, missing metrics, and the toy runs
(every accepted design passes, every rejected one fails what it was built to fail)."""
from __future__ import annotations

import pytest

from binder_review import config
from binder_review.config import DEFAULT_FILTERS, FilterRule, check_filters, failed_stems, rules_by_stem
from binder_review.model import filter_rule_to_dict, failure_to_dict, near_miss_record
from toydata.plans import RUN1, RUN2


def _ok_metrics():
    avg = {"pLDDT": 0.9, "pTM": 0.8, "i_pTM": 0.8, "i_pAE": 0.2, "ShapeComplementarity": 0.7, "n_InterfaceHbonds": 5,
           "n_InterfaceUnsatHbonds": 2, "Surface_Hydrophobicity": 0.3, "Binder_pLDDT": 0.9, "Binder_RMSD": 1.0}
    return avg, {1: dict(avg), 2: dict(avg)}


def test_every_rule_has_a_provenance_and_a_direction():
    assert {r.provenance for r in DEFAULT_FILTERS} <= {"counter-verified", "bounded", "memory"}
    assert all(r.op in (">=", "<=") and r.threshold is not None for r in DEFAULT_FILTERS)
    stems = rules_by_stem()
    assert len(stems) == len(DEFAULT_FILTERS)                                  # one rule per stem
    assert stems["ShapeComplementarity"].threshold == 0.60 and stems["ShapeComplementarity"].per_model == 0.55
    assert stems["n_InterfaceUnsatHbonds"].threshold == 4 and not stems["n_InterfaceUnsatHbonds"].higher_is_better
    assert stems["pLDDT"].higher_is_better and stems["i_pAE"].higher_is_better is False
    assert "Hotspot_RMSD" not in stems                                         # looks like a filter in BindCraft's file but is not enforced


def test_folder_names_and_columns_the_readers_use():
    assert (config.ACCEPTED_DIR, config.REJECTED_DIR) == ("accepted", "rejected")
    assert config.CSV_FINAL == "final_design_stats.csv" and config.CSV_MPNN == "mpnn_design_stats.csv"
    assert config.CSV_TRAJECTORY == "trajectory_stats.csv" and config.CSV_FAILURE == "failure_csv.csv"
    assert config.MODEL_NUMBERS == (1, 2, 3, 4, 5) and "Trajectory_Clashes" in config.TRAJECTORY_COUNTERS


def test_check_filters_passes_a_clean_design():
    avg, models = _ok_metrics()
    assert check_filters(avg, models, {"avg": {"K": 1, "M": 2}}) == []


def test_thresholds_are_inclusive_and_report_gaps():
    avg, models = _ok_metrics()
    avg.update(ShapeComplementarity=0.60, n_InterfaceUnsatHbonds=4, n_InterfaceHbonds=3)
    models[1].update(ShapeComplementarity=0.55)
    assert check_filters(avg, models, {"avg": {"K": 3, "M": 3}}) == []
    avg.update(ShapeComplementarity=0.59, n_InterfaceUnsatHbonds=6)
    models[2].update(ShapeComplementarity=0.54, n_InterfaceUnsatHbonds=5)
    fails = check_filters(avg, models, {"avg": {"K": 4}})
    got = {(f.stem, f.scope): round(f.gap, 6) for f in fails}
    assert got[("ShapeComplementarity", "Average")] == 0.01 and got[("ShapeComplementarity", "2")] == 0.01
    assert got[("n_InterfaceUnsatHbonds", "Average")] == 2 and got[("n_InterfaceUnsatHbonds", "2")] == 1 and got[("InterfaceAAs_K", "Average")] == 1
    assert ("ShapeComplementarity", "1") not in got and failed_stems(fails) == ["ShapeComplementarity", "n_InterfaceUnsatHbonds", "InterfaceAAs_K"]
    assert all(f.gap > 0 for f in fails)


def test_missing_metrics_are_skipped_not_failed():
    assert check_filters({}, {}, None) == [] and check_filters({"pLDDT": None}, {1: {"pLDDT": None}}) == []


def test_custom_rules():
    rule = FilterRule("dG", -40.0, False)
    assert [f.stem for f in check_filters({"dG": -30.0}, {}, rules=[rule])] == ["dG"] and check_filters({"dG": -50.0}, {}, rules=[rule]) == []


def test_rule_and_failure_blobs_are_json_ready():
    rule = rules_by_stem()["ShapeComplementarity"]
    d = filter_rule_to_dict(rule)
    assert d["stem"] == "ShapeComplementarity" and d["op"] == ">=" and d["metric"] == "sc" and d["per_model"] == 0.55
    avg, models = _ok_metrics()
    avg["ShapeComplementarity"] = 0.57
    f = failure_to_dict(check_filters(avg, models)[0])
    assert f["stem"] == "ShapeComplementarity" and f["scope"] == "Average" and f["rel"] == pytest.approx(0.05, abs=1e-3)


def test_near_miss_rule_counts_distinct_stems():
    avg, models = _ok_metrics()
    avg["ShapeComplementarity"] = 0.57                                           # misses by 5 percent: a near miss
    models[1]["ShapeComplementarity"] = 0.50
    nm = near_miss_record(check_filters(avg, models))
    assert nm["n_failed"] == 1 and nm["stems"] == ["ShapeComplementarity"] and nm["worst_rel"] <= 0.25 and nm["near"] is True
    avg["n_InterfaceUnsatHbonds"] = 9
    assert near_miss_record(check_filters(avg, models))["near"] is False         # two stems: not a near miss
    assert near_miss_record([]) is None


@pytest.mark.parametrize("plan, fixture", [(RUN1, "toy_run1"), (RUN2, "toy_run2")])
def test_toy_accepted_pass_and_rejected_fail_the_stems_they_were_built_for(plan, fixture, request):
    run = request.getfixturevalue(fixture)
    built = {}
    for spec in plan:
        for mp, stems in spec.rejected.items():
            built[mp, spec.length] = stems
    assert all(d.failed_filters == [] for d in run.accepted())
    assert run.rejected() and all(d.failed_filters for d in run.rejected())
    for d in run.rejected():
        assert all(f.gap > 0 for f in d.failed_filters)
        want = built[d.mpnn_index, d.length]
        assert set(want) <= set(d.failed_stems), (d.id, want, d.failed_stems)


def test_toy_failure_counters_match_the_failing_rows(toy_run1):
    from collections import Counter

    rows = Counter(s for d in toy_run1.designs for s in d.failed_stems)
    for stem in ("ShapeComplementarity", "n_InterfaceHbonds", "n_InterfaceUnsatHbonds", "Surface_Hydrophobicity", "Binder_pLDDT", "Binder_RMSD", "InterfaceAAs_K"):
        assert rows[stem] == toy_run1.failure_counters[stem] and rows[stem] >= 1, stem
