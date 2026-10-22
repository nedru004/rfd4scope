"""BindCraft 1 reader on the toy runs: counts, joins, naming rules, scales, settings, deleted loops and tolerance.

The toy runs come from tests/toydata (see conftest.py); every expected number is derived from the plans there
or from the files themselves."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import gemmi
import numpy as np
import pandas as pd
import pytest

from binder_review.model import METRIC_INFO, METRIC_STEMS, RunData
from binder_review.readers import bindcraft1, detect_reader
from toydata.plans import RUN1, RUN2


def n_accepted(plan):
    return sum(len(s.accepted) for s in plan)


def n_rejected(plan):
    return sum(len(s.rejected) for s in plan)


def copy_run(toy_dir: Path, name: str, tmp_path: Path) -> Path:
    dst = tmp_path / name
    shutil.copytree(toy_dir / "runs" / name, dst)
    return dst


# ---------------------------------------------------------------------------------------------- naming and helpers
def test_parse_name_design_and_files():
    info = bindcraft1.parse_name("binder_l77_s305024_mpnn4_model2")
    assert info == {"binder": "binder", "length": 77, "seed": 305024, "mpnn": 4, "model": 2,
                    "design_id": "binder_l77_s305024_mpnn4", "backbone_id": "binder_l77_s305024"}
    bb = bindcraft1.parse_name("binder_l100_s94921")
    assert bb["mpnn"] is None and bb["model"] is None and bb["backbone_id"] == "binder_l100_s94921"
    assert bindcraft1.parse_name("some_protein_name") is None
    assert bindcraft1.parse_name("my_binder_l12_s5_mpnn1")["binder"] == "my_binder"
    # the _model suffix is stripped from the design id, never from the binder name
    assert bindcraft1.parse_name("binder_l9_s1_mpnn2_model1")["design_id"] == "binder_l9_s1_mpnn2"


def test_parse_duration():
    assert bindcraft1.parse_duration("0 hours, 9 minutes, 2 seconds") == 542.0
    assert bindcraft1.parse_duration("2 hours, 0 minutes, 5 seconds") == 7205.0
    assert bindcraft1.parse_duration("") is None
    assert bindcraft1.parse_duration(float("nan")) is None


def test_parse_hotspots():
    assert bindcraft1.parse_hotspots("26,122,125") == [26, 122, 125]
    assert bindcraft1.parse_hotspots("A26, B30-32") == [26, 30, 31, 32]
    assert bindcraft1.parse_hotspots(None) == []
    assert bindcraft1.parse_hotspots("") == []


def test_detect_and_registry(toy_dir, tmp_path):
    assert bindcraft1.detect(toy_dir / "runs" / "run01") and bindcraft1.detect(toy_dir / "runs" / "run02")
    assert not bindcraft1.detect(tmp_path) and not bindcraft1.detect(tmp_path / "nope")
    assert detect_reader(toy_dir / "runs" / "run02") is bindcraft1
    assert detect_reader(tmp_path) is None
    assert detect_reader(toy_dir / "bindcraft2" / "toya") is not bindcraft1


def test_read_run_rejects_other_folders(tmp_path):
    with pytest.raises(ValueError):
        bindcraft1.read_run(tmp_path, "x")


# ---------------------------------------------------------------------------------------------- run 1
def test_run1_counts_and_joins(toy_run1: RunData):
    run = toy_run1
    assert len(run.accepted()) == n_accepted(RUN1) == 12
    assert len(run.rejected()) == n_rejected(RUN1) == 8
    assert len(run.rejected_with_structure()) == 8 and len(run.designs) == 20
    assert run.warnings == [] and run.reader == "bindcraft1" and not run.experimental
    assert len(run.accepted_backbones()) == sum(1 for s in RUN1 if s.accepted)
    for d in run.accepted():
        assert Path(d.structure_path).is_file() and d.structure_path.endswith(f"{d.id}_model{d.structure_model}.pdb")
        assert Path(d.ranked_path).name == f"{d.rank}_{d.id}_model{d.structure_model}.pdb" and Path(d.ranked_path).is_file()
        assert d.failed_filters == [] and d.backbone_id == d.id.rsplit("_mpnn", 1)[0] and d.status == "accepted"
    for d in run.rejected():
        assert Path(d.structure_path).is_file() and d.rank is None and d.failed_filters and d.ranked_path is None
        assert all(f.gap > 0 for f in d.failed_filters)
    assert [d.rank for d in run.accepted()] == list(range(1, 13))        # accepted() is sorted by rank
    assert {d.structure_model for d in run.designs} <= {1, 2}


def test_run1_id_and_backbone_rules(toy_run1: RunData):
    ids = [d.id for d in toy_run1.designs]
    assert len(set(ids)) == len(ids)
    for d in toy_run1.designs:
        info = bindcraft1.parse_name(d.id)
        assert info["design_id"] == d.id and info["backbone_id"] == d.backbone_id and info["mpnn"] == d.mpnn_index
        assert d.binder_name == "binder" and info["length"] == d.length and info["seed"] == d.seed
    by_bb = Counter(d.backbone_id for d in toy_run1.accepted())
    assert max(by_bb.values()) == 2                       # BindCraft keeps at most two sequences per backbone
    for b in toy_run1.backbones:
        assert set(b.accepted_ids) <= set(b.design_ids) and all(toy_run1.design(i).backbone_id == b.id for i in b.design_ids)
    orders = [b.order for b in toy_run1.backbones]
    assert orders == sorted(orders) and orders[0] == 0 and len(orders) == len(RUN1)
    assert toy_run1.backbone(toy_run1.accepted()[0].backbone_id).traj >= 1


def test_run1_metrics_scales_and_models(toy_run1: RunData):
    d = toy_run1.accepted()[0]
    assert set(d.metrics) == set(METRIC_STEMS)
    assert set(d.models) == {1, 2}                       # models 3 to 5 exist as empty columns
    assert 0.5 < d.metric("pLDDT") <= 1.0 and 0 < d.metric("i_pAE") < 1.0 and 0 < d.metric("i_pTM") <= 1.0
    assert d.metric("Interface_Hydrophobicity") > 1.0    # percent, 0 to 100
    assert d.metric("pLDDT", model=3) is None
    assert d.pose_metric("pLDDT") == d.metric("pLDDT", model=d.structure_model)
    for x in toy_run1.designs:
        m1, m2 = x.models[1]["pLDDT"], x.models[2]["pLDDT"]
        assert x.structure_model == (1 if m1 >= m2 else 2)          # the saved model is the one with the higher pLDDT
        assert x.metric("pLDDT") == pytest.approx((m1 + m2) / 2, abs=0.006)
        assert set(x.interface_aas) >= {"avg", "1", "2"} and sum(x.interface_aas["1"].values()) == x.models[1]["n_InterfaceResidues"]
    assert all(s in METRIC_INFO for s in METRIC_STEMS) and all(toy_run1.caps().values())


def test_run1_settings_target_and_funnel(toy_run1: RunData, toy_truth):
    st = toy_run1.settings
    hot = toy_truth["hotspots_construct"]["run01"]
    assert st.hotspots == hot and st.hotspot_source == "settings_json" and len(hot) == 3
    assert (st.length_min, st.length_max, st.length_source) == (60, 100, "settings_json")
    assert st.final_designs_target == 12 and st.final_designs_target_source == "settings_json"
    assert st.filters_name == "default_filters" and st.protocol == "4stage" and st.target_settings_name == "run01"
    assert st.starting_pdb_name == "toy_construct.pdb" and st.binder_name == "binder" and st.chains == "A"
    t = toy_run1.target
    assert t.n_present == 110 and (t.first, t.last) == (1, 110) and t.deleted_ranges == []
    assert t.consistent and t.sequence_matches_reference and t.n_structures_checked == 20
    assert toy_run1.hotspot_labels() == [f"{t.residue_at(n)}{n}" for n in hot]
    f = toy_run1.funnel
    assert (f.trajectory_rows, f.mpnn_rows, f.accepted_designs) == (len(RUN1), 20, 12)
    assert f.accepted_backbones == sum(1 for s in RUN1 if s.accepted)
    assert f.backbones_with_mpnn_rows == sum(1 for s in RUN1 if s.accepted or s.rejected)
    assert f.trajectories_attempted is None and f.status["trajectories_attempted"] == "unknown"
    assert f.status["trajectories_attempted_estimate"] == "derived" and f.final_designs_target == 12
    assert f.mpnn_sequences_per_trajectory == max(m for s in RUN1 for m in list(s.accepted) + list(s.rejected))


def test_run1_hashes_match_files(toy_run1: RunData):
    assert toy_run1.files
    for f in toy_run1.files:
        assert hashlib.sha256(Path(f.path).read_bytes()).hexdigest() == f.sha256 and Path(f.path).stat().st_size == f.size
    roles = Counter(f.role for f in toy_run1.files)
    assert roles["settings"] == 1 and roles["csv"] == 4 and roles["xlsx"] == 1
    assert roles["accepted_structure"] == 12 and roles["rejected_structure"] == 8


def test_run1_xlsx_highlight(toy_run1: RunData):
    marked = [d.id for d in toy_run1.designs if d.xlsx_marked]
    assert marked == [toy_run1.accepted()[0].id]                         # the workbook highlights the rank 1 row
    assert toy_run1.annotations["xlsx_highlight"][0]["colours"] == {"FFFFC000": 1}


def test_run_data_serialises_without_sequences(toy_run1: RunData):
    blob = json.dumps(toy_run1.to_dict())
    seqs = [d.sequence for d in toy_run1.designs]
    assert all(s and s not in blob for s in seqs)
    assert seqs[0] in json.dumps(toy_run1.to_dict(include_sequences=True))


# ---------------------------------------------------------------------------------------------- run 2
def test_run2_has_no_settings_no_rejected_and_two_deleted_loops(toy_run2: RunData, toy_truth):
    st = toy_run2.settings
    assert st.hotspots == toy_truth["hotspots_construct"]["run02"] and st.hotspot_source == "csv" and len(st.hotspots) == 4
    assert st.length_source.startswith("inferred") and st.settings_path is None
    assert (st.length_min, st.length_max) == (min(s.length for s in RUN2), max(s.length for s in RUN2))
    assert any("No settings file" in n for n in toy_run2.notes) and any("No rejected/ folder" in n for n in toy_run2.notes)
    assert toy_run2.warnings == []
    t = toy_run2.target
    gaps = [tuple(x) for x in toy_truth["deleted_construct"]]
    assert t.n_present == 102 and t.deleted_ranges == gaps == [(20, 23), (80, 83)] and t.consistent
    assert t.sequence_matches_reference and (t.first, t.last) == (1, 110)
    assert len(toy_run2.accepted()) == n_accepted(RUN2) and len(toy_run2.rejected()) == n_rejected(RUN2)
    assert toy_run2.rejected_with_structure() == []
    assert all(d.structure_path is None and d.metrics for d in toy_run2.rejected())
    assert all(d.structure_path and d.ranked_path for d in toy_run2.accepted())


def test_run2_final_target_is_inferred_only_when_unique(toy_run2: RunData):
    assert toy_run2.funnel.final_designs_target == n_accepted(RUN2)
    assert toy_run2.funnel.status["final_designs_target"] == "derived"
    assert toy_run2.settings.final_designs_target_source.startswith("inferred")


def test_hotspots_and_construct_differ_between_runs(toy_run1, toy_run2):
    assert set(toy_run1.settings.hotspots) < set(toy_run2.settings.hotspots)
    assert set(toy_run2.target.numbers) < set(toy_run1.target.numbers)


def test_run2_without_the_construct_file_reports_gaps_from_the_numbers(toy_dir):
    run = bindcraft1.read_run(toy_dir / "runs" / "run02", "r2")
    assert run.target.deleted_ranges == [(20, 23), (80, 83)] and run.target.reference_path is None
    # with the starting_pdb name recorded in a settings file next to the data the reader would look it up; here none exists
    assert run.target.sequence_matches_reference is None


# ---------------------------------------------------------------------------------------------- tolerance
def test_available_metrics_respects_missing_columns(toy_dir, tmp_path):
    run = copy_run(toy_dir, "run01", tmp_path)
    for name in ("final_design_stats.csv", "mpnn_design_stats.csv"):
        df = pd.read_csv(run / name)
        df = df[[c for c in df.columns if not c.endswith("_PackStat")]]
        df.to_csv(run / name, index=False)
    data = bindcraft1.read_run(run, "r", check_structures=False)
    assert "PackStat" not in data.available_metrics() and not data.caps()["PackStat"] and data.caps()["pLDDT"]
    assert data.warnings == []


def test_unknown_columns_are_ignored(toy_dir, tmp_path):
    run = copy_run(toy_dir, "run01", tmp_path)
    for name in ("final_design_stats.csv", "mpnn_design_stats.csv", "trajectory_stats.csv"):
        df = pd.read_csv(run / name)
        extra = pd.DataFrame({"Future_Metric": 1.5, "7_pLDDT": 0.5}, index=df.index)   # an unknown model number too
        pd.concat([df, extra], axis=1).to_csv(run / name, index=False)
    data = bindcraft1.read_run(run, "r")
    assert len(data.accepted()) == 12 and data.warnings == []


def test_missing_and_stray_structures_are_warned_not_fatal(toy_dir, tmp_path):
    run = copy_run(toy_dir, "run01", tmp_path)
    acc = sorted((run / "Accepted").glob("*.pdb"))
    rej = sorted((run / "Rejected").glob("*.pdb"))
    acc[0].unlink()
    rej[0].unlink()
    shutil.copy(acc[1], run / "Accepted" / "binder_l11_s999_mpnn1_model1.pdb")
    shutil.copy(rej[1], run / "Rejected" / "binder_l12_s998_mpnn2_model2.pdb")
    data = bindcraft1.read_run(run, "r")
    text = "\n".join(data.warnings)
    assert "accepted designs have a CSV row but no PDB in accepted/" in text
    assert "not-accepted designs have a CSV row but no PDB in rejected/" in text
    assert "PDBs in accepted/ have no CSV row" in text and "PDBs in rejected/ have no CSV row" in text
    assert len(data.accepted()) == 12 and sum(d.structure_path is None for d in data.accepted()) == 1


def test_missing_optional_files_become_notes(toy_dir, tmp_path):
    run = copy_run(toy_dir, "run01", tmp_path)
    (run / "failure_csv.csv").unlink()
    (run / "run01.json").unlink()
    shutil.rmtree(run / "Accepted" / "Ranked")
    shutil.rmtree(run / "Rejected")
    data = bindcraft1.read_run(run, "r")
    assert any("failure_csv.csv is missing" in n for n in data.notes) and data.failure_counters == {}
    assert any("No settings file" in n for n in data.notes) and data.settings.hotspot_source == "csv"
    assert any("No rejected/ folder" in n for n in data.notes)
    assert all(d.ranked_path is None for d in data.accepted()) and len(data.accepted()) == 12


def test_structure_checks_flag_wrong_length_and_sequence(toy_dir, tmp_path):
    run = copy_run(toy_dir, "run01", tmp_path)
    df = pd.read_csv(run / "final_design_stats.csv")
    df.loc[0, "Length"] = int(df.loc[0, "Length"]) + 1
    seq = list(df.loc[1, "Sequence"])
    seq[3] = "W" if seq[3] != "W" else "A"
    df.loc[1, "Sequence"] = "".join(seq)
    df.to_csv(run / "final_design_stats.csv", index=False)
    data = bindcraft1.read_run(run, "r")
    text = "\n".join(data.warnings)
    assert "binder chain length differs" in text and "binder sequence differs" in text


def test_b_factor_scale_mismatch_is_flagged(toy_dir, tmp_path):
    """A model whose B-factors are 0 to 1 instead of pLDDT on 0 to 100 disagrees with the CSV pLDDT."""
    run = copy_run(toy_dir, "run01", tmp_path)
    victim = sorted((run / "Accepted").glob("*_model*.pdb"))[0]
    st = gemmi.read_structure(str(victim))
    for r in st[0]["B"]:
        for a in r:
            a.b_iso = a.b_iso / 100.0
    st.write_pdb(str(victim))
    data = bindcraft1.read_run(run, "r")
    assert any("binder mean B-factor / 100 differs" in w for w in data.warnings)


def test_pdb_with_wrong_chains_is_flagged(toy_dir, tmp_path):
    run = copy_run(toy_dir, "run01", tmp_path)
    victim = sorted((run / "Accepted").glob("*_model*.pdb"))[0]
    st = gemmi.read_structure(str(victim))
    st[0]["B"].name = "C"
    st.write_pdb(str(victim))
    data = bindcraft1.read_run(run, "r")
    assert any("do not have exactly chains A and B" in w for w in data.warnings)


def test_accepted_failing_filters_is_a_warning(toy_dir, tmp_path):
    run = copy_run(toy_dir, "run01", tmp_path)
    df = pd.read_csv(run / "final_design_stats.csv")
    df.loc[0, "Average_n_InterfaceUnsatHbonds"] = 9
    df.to_csv(run / "final_design_stats.csv", index=False)
    data = bindcraft1.read_run(run, "r", check_structures=False)
    assert any("accepted designs fail DEFAULT_FILTERS" in w for w in data.warnings)


def test_hotspot_column_that_disagrees_with_the_settings_file_is_noted(toy_dir, tmp_path):
    run = copy_run(toy_dir, "run01", tmp_path)
    for name in ("final_design_stats.csv", "mpnn_design_stats.csv", "trajectory_stats.csv"):
        df = pd.read_csv(run / name, dtype=str)
        df = df.assign(Target_Hotspot="1,2,3")
        df.to_csv(run / name, index=False)
    data = bindcraft1.read_run(run, "r", check_structures=False)
    assert data.settings.hotspot_source == "settings_json" and any("differs from the settings file" in n for n in data.notes)


def test_case_insensitive_folder_names(toy_dir, tmp_path):
    run = copy_run(toy_dir, "run01", tmp_path)
    (run / "Accepted").rename(run / "tmp_a")
    (run / "tmp_a").rename(run / "ACCEPTED")
    (run / "Rejected").rename(run / "tmp_r")
    (run / "tmp_r").rename(run / "rejected")
    data = bindcraft1.read_run(run, "r", check_structures=False)
    assert len(data.rejected_with_structure()) == 8 and data.accepted()[0].structure_path


def test_run2_lowercase_folders_and_other_run_ids(toy_dir):
    data = bindcraft1.read_run(toy_dir / "runs" / "run02", "second", label="Second run", hash_structures=False)
    assert data.run_id == "second" and data.label == "Second run" and all(d.run_id == "second" for d in data.designs)
    assert not any(f.role.endswith("_structure") for f in data.files)
    assert data.designs[0].key == ("second", data.designs[0].id)


def test_failure_counters_and_trajectory_estimates(toy_run1: RunData):
    c = toy_run1.failure_counters
    assert c["Trajectory_Clashes"] > 0 and c["i_pTM"] > len(toy_run1.designs)
    rows = Counter(s for d in toy_run1.designs for s in d.failed_stems)
    for stem in ("ShapeComplementarity", "n_InterfaceUnsatHbonds", "Surface_Hydrophobicity", "Binder_RMSD", "Binder_pLDDT",
                 "n_InterfaceHbonds"):
        assert rows[stem] == c[stem], stem                    # the counters were written from the rows
    f = toy_run1.funnel
    assert f.trajectories_terminated == sum(v for k, v in c.items() if k.startswith("Trajectory_"))
    assert f.trajectories_attempted_estimate == f.trajectory_rows + f.trajectories_terminated
