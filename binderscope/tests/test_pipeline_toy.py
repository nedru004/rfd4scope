"""The pipeline on the toy project: stages, caches, the target-frame and interface hand-offs, and the command line that drives it.

The toy runs go through every stage with the default profile and no UniProt accession: no network, no private data. The expectations
come from the plans and the truth file of the generator."""
from __future__ import annotations

import copy
import io
import json
import time
from pathlib import Path

import numpy as np
import pytest

from binder_review import cli
from binder_review import pipeline as P
from binder_review import target_frame as TF
from toydata.testing import OFFSET, repo_root


def _dump(by_key: dict) -> str:
    """Records keyed by (run, design id) as comparable JSON text."""
    return json.dumps({f"{k[0]}/{k[1]}": v for k, v in by_key.items()}, sort_keys=True)


# ---------------------------------------------------------------------------------------------------- pure functions
def test_log_records_timings_and_throttles_progress():
    out = io.StringIO()
    log = P.Log(stream=out)
    with log.stage("alpha"):
        pass
    cb = log.progress("items", step=25)
    for i in range(1, 101):
        cb(i, 100)
    text = out.getvalue()
    assert "alpha ..." in text and "alpha done in" in text and "alpha" in log.timings
    assert text.count("items:") <= 6 and "items: 100/100" in text                        # one line per quarter, and always the last
    assert P.Log(quiet=True).stream is not None


def test_hashes_do_not_depend_on_dict_order_and_code_hash_follows_source():
    assert P.sha256_json({"a": 1, "b": [1, 2]}) == P.sha256_json({"b": [1, 2], "a": 1}) and P.sha256_json({"a": 1}) != P.sha256_json({"a": 2})
    assert P.sha256_json({"x": np.int64(3), "y": np.float64(0.5)}) == P.sha256_json({"x": 3, "y": 0.5})

    def f():
        return 1

    def g():
        return 2

    assert P.code_hash(f) == P.code_hash(f) and P.code_hash(f) != P.code_hash(g) and P.code_hash(P.pose_one, P.canonical_view) != P.code_hash(P.pose_one)


def test_cache_read_and_write_are_atomic_and_key_checked(tmp_path):
    path = tmp_path / "sub" / "x.json"
    assert P._read_cached(path, "k") is None                                              # a missing file is a miss
    back = P._write_cached(path, "k", {"a": (1, 2), "n": np.float64(1.5)})
    assert back == {"a": [1, 2], "n": 1.5} and P._read_cached(path, "k") == back and P._read_cached(path, "other") is None
    path.write_text("{not json")
    assert P._read_cached(path, "k") is None and not list(path.parent.glob("*.tmp"))      # a corrupt file is a miss, not an error


def test_canonical_view_is_a_proper_rotation_and_centres_on_the_interface():
    rng = np.random.default_rng(3)
    tgt = rng.normal(size=(200, 3)) * 12
    bnd = rng.normal(size=(80, 3)) * 5 + np.array([14.0, 3.0, -2.0])
    view, ic = P.canonical_view(tgt, bnd)
    assert view.shape == (3, 3) and np.allclose(view @ view.T, np.eye(3), atol=1e-6) and np.linalg.det(view) == pytest.approx(1.0, abs=1e-6)
    d = np.linalg.norm(tgt[:, None] - bnd[None], axis=2)
    ia, jb = np.nonzero(d <= 4.5)
    assert len(ia) > 0 and np.allclose(ic, (0.5 * (tgt[ia] + bnd[jb])).mean(axis=0), atol=1e-9)
    a = bnd.mean(axis=0) - ic
    assert np.allclose(view[0], a / np.linalg.norm(a), atol=1e-9)                          # the x axis points from the interface to the binder
    view2, ic2 = P.canonical_view(tgt, bnd)
    assert np.array_equal(view, view2) and np.array_equal(ic, ic2)
    v3, _ = P.canonical_view(tgt, bnd + 100.0)                                             # no contact: still a rotation
    assert np.allclose(v3 @ v3.T, np.eye(3), atol=1e-6)


def test_default_run_id():
    assert P.default_run_id(Path("/x/250101_run01")) == "run01" and P.default_run_id(Path("/x/RUN7_final")) == "run7"
    assert P.default_run_id(Path("/x/campaign")) == "campaign"


def test_the_accession_comes_from_the_project_else_the_profile_else_nothing():
    spec = P.ProjectSpec(name="x", runs=[], target_pdb=Path("a"), reference_cif=Path("b"))
    assert P.DEFAULT_PROFILE == "default" and P.BUILTIN_PROFILES == ("default",) and spec.uniprot is None and spec.partner_chains is None
    assert P.effective_uniprot(spec, {"target": {"uniprot": " Z0TOY1 "}}) == "Z0TOY1"
    assert P.effective_uniprot(spec, {"target": {}}) is None and P.effective_uniprot(spec, P.load_profile("default")) is None
    spec.uniprot = "Z0TOY2"
    assert P.effective_uniprot(spec, {"target": {"uniprot": "Z0TOY1"}}) == "Z0TOY2"        # the project's own wins


def test_the_builtin_profile_is_the_packaged_copy_of_the_schema_example():
    root = repo_root()
    for pid in P.BUILTIN_PROFILES:
        packaged = json.loads((P.profiles_dir() / f"{pid}.profile.json").read_text(encoding="utf-8"))
        example = root / "docs" / "schema" / "examples" / f"{pid}.profile.json"
        if example.is_file():
            assert packaged == json.loads(example.read_text(encoding="utf-8")), f"{pid}: copy the example again"
        assert P.load_profile(pid) == packaged
    d = {"id": "x", "caps": {}}
    assert P.load_profile(d) == d and P.load_profile(d) is not d                           # a dict is copied
    with pytest.raises(P.PipelineError):
        P.load_profile("no-such-profile")


def test_frame_context_thresholds_equal_the_default_profile():
    ctx = P.load_profile("default")["checks"]["context"]
    for part, vals in TF.DEFAULT_CONFIG["checks"].items():
        assert {k: float(v) for k, v in ctx[part].items()} == {k: float(v) for k, v in vals.items()}, part


# ---------------------------------------------------------------------------------------------------- readers
def test_read_runs_reads_every_run_and_rejects_bad_specs(toy_spec, toy_dir, tmp_path):
    runs = P.read_runs(toy_spec)
    assert list(runs) == ["run01", "run02"] and len(runs["run01"].accepted()) == 12 and len(runs["run02"].accepted()) == 10
    assert len(runs["run01"].rejected_with_structure()) == 8 and len(runs["run02"].rejected_with_structure()) == 0
    dup = P.ProjectSpec(name="d", runs=[P.RunSpec(toy_dir / "runs" / "run01", "x"), P.RunSpec(toy_dir / "runs" / "run02", "x")],
                        target_pdb=toy_spec.target_pdb, reference_cif=toy_spec.reference_cif)
    with pytest.raises(P.PipelineError, match="used twice"):
        P.read_runs(dup)
    with pytest.raises(P.PipelineError, match="not a folder"):
        P.read_runs(P.ProjectSpec(name="d", runs=[P.RunSpec(tmp_path / "missing", "x")], target_pdb=toy_spec.target_pdb, reference_cif=toy_spec.reference_cif))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(P.PipelineError, match="known reader"):
        P.read_runs(P.ProjectSpec(name="d", runs=[P.RunSpec(empty, "x")], target_pdb=toy_spec.target_pdb, reference_cif=toy_spec.reference_cif))


def test_read_runs_detects_the_reader_of_each_folder_bindcraft_2_is_experimental(toy_dir):
    spec = P.ProjectSpec(name="mixed", runs=[P.RunSpec(toy_dir / "runs" / "run01", "a"), P.RunSpec(toy_dir / "bindcraft2" / "toya", "bc2")],
                         target_pdb=toy_dir / "construct" / "toy_construct.pdb", reference_cif=toy_dir / "reference" / "toy_target.cif")
    runs = P.read_runs(spec)
    assert runs["a"].reader == "bindcraft1" and not runs["a"].experimental and runs["bc2"].reader == "bindcraft2" and runs["bc2"].experimental


def test_input_digests_are_stable_and_name_every_input(toy_spec):
    runs = P.read_runs(toy_spec)
    a, b = P.input_digests(toy_spec, runs), P.input_digests(toy_spec, P.read_runs(toy_spec))
    assert a == b and set(a["runs"]) == {"run01", "run02"} and a["runs"]["run01"]["files"] != a["runs"]["run02"]["files"]
    assert a["target_pdb"] == P.sha256_file(toy_spec.target_pdb) and a["reference_cif"] == P.sha256_file(toy_spec.reference_cif)
    assert a["runs"]["run01"]["definition"] != a["runs"]["run02"]["definition"]            # different hotspots and constructs


def test_missing_input_file_is_a_clear_error(toy_spec, tmp_path):
    spec = copy.copy(toy_spec)
    spec.target_pdb = tmp_path / "nope.pdb"
    with pytest.raises(P.PipelineError, match="not found"):
        P.run_pipeline(spec, tmp_path / "w", log=P.Log(quiet=True))


# ---------------------------------------------------------------------------------------------------- the whole analysis
def test_pipeline_produces_every_stage(toy_project):
    p = toy_project
    assert set(p.runs) == {"run01", "run02"} and p.runs["run01"].target.uniprot_offset == OFFSET == p.runs["run02"].target.uniprot_offset
    n = sum(len(r.accepted()) + len(r.rejected_with_structure()) for r in p.runs.values())
    assert n == 30 == len(p.poses) == len(p.analyses)
    assert len(p.project_ids) == sum(len(r.designs) for r in p.runs.values()) == 38         # every row has a project id, metric-only rows included
    assert set(p.timings) == {"read runs", "target frame", "poses and context checks", "interface analysis", "families and diversity",
                              "modes and near-miss records", "specificity panel"}
    assert p.homolog["n_orth"] == 0 and p.homolog["n_par"] == 0 and len(p.homolog["resis"]) == 110
    assert p.families["fold"]["n"] == 22 and p.modes["assign"] and len(p.families["families"]["run01"]) == 8
    assert set(p.rescue) == {(r, d.id) for r, run in p.runs.items() for d in run.rejected_with_structure()}
    assert p.network_requests == 0 and p.warnings == []                                       # nothing was looked up anywhere
    s = p.summary()
    assert s["runs"]["run02"]["rejected"] == 8 and s["runs"]["run01"]["families"] == 8 and s["designs_exported"] == 30
    assert (p.work / "runs" / "run01" / "runrecord.json").is_file()


def test_pose_records_are_in_the_reference_frame(toy_project, toy_truth):
    removed = {r for a, b in toy_truth["deleted_reference"] for r in range(a, b + 1)}
    for (rid, did), rec in toy_project.poses.items():
        pl = rec["placement"]
        R = np.array(pl["R"]).reshape(3, 3)
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-5) and np.linalg.det(R) == pytest.approx(1.0, abs=1e-5)
        assert pl["rmsd"] < 0.3 and pl["n"] in (110, 102) and all(60 <= c <= 169 for c in rec["contacted"])     # reference numbers, not construct numbers
        assert len(rec["ca"]) == 3 * rec["n_binder"]                                                            # x, y, z of every binder CA
        assert np.allclose(np.linalg.det(np.array(rec["view"]).reshape(3, 3)), 1.0, atol=1e-3) and set(rec["metrics"]) == set(TF.METRIC_KEYS)
        if rid == "run02":
            assert not set(rec["contacted"]) & removed
            assert [79, 82] in rec["ctx"]["removed"] and [139, 142] in rec["ctx"]["removed"]


def test_interface_and_pose_contacts_agree_by_number(toy_project):
    for key, a in toy_project.analyses.items():
        off = toy_project.runs[key[0]].target.uniprot_offset
        assert sorted(c + off for c in a.tc) == sorted(toy_project.poses[key]["contacted"])
        assert sorted(h + off for h in a.hot) == sorted(toy_project.poses[key]["hot"])


def test_second_run_reads_every_stage_from_the_cache(toy_project, toy_spec):
    work = toy_project.work
    out = io.StringIO()
    t = time.perf_counter()
    b = P.run_pipeline(toy_spec, work, log=P.Log(stream=out))                                   # the work folder of the session project: every stage is warm
    warm = time.perf_counter() - t
    text = out.getvalue()
    for stage in ("target frame", "families", "modes", "specificity"):
        assert f"{stage}: cache hit" in text, stage
    a = toy_project
    assert _dump(a.poses) == _dump(b.poses) and a.families == b.families and a.modes == b.modes and a.homolog == b.homolog and _dump(a.rescue) == _dump(b.rescue)
    assert {k: v.to_dict() for k, v in a.analyses.items()} == {k: v.to_dict() for k, v in b.analyses.items()}
    assert (work / "target" / "frame.key").is_file() and (work / "stages" / "families.json").is_file()
    assert len(list((work / "poses").rglob("*.json"))) == 30 and len(list((work / "analysis").rglob("*.json"))) == 30
    assert warm < 60 and b.timings["interface analysis"] < a.timings["interface analysis"] + 5


def test_a_changed_input_changes_the_cache_keys(toy_project):
    p = toy_project
    d = next(iter(p.runs["run01"].accepted()))
    fkey = P.frame_key(p.spec, p.runs, p.digests)
    sha = P.structure_hashes(p.runs)[str(Path(d.structure_path).resolve())]
    assert P.pose_key(sha, fkey) == P.pose_key(sha, fkey) and P.pose_key(sha, fkey) != P.pose_key("0" * 64, fkey) and P.pose_key(sha, fkey) != P.pose_key(sha, "f" * 64)
    runs2 = dict(p.runs)
    r = copy.deepcopy(p.runs["run01"])
    r.settings.hotspots = r.settings.hotspots[:2]                                            # a different hotspot set is a different frame
    runs2["run01"] = r
    assert P.frame_key(p.spec, runs2, p.digests) != fkey
    other = copy.copy(p.spec)
    other.uniprot = "Z0TOY1"
    assert P.frame_key(other, p.runs, p.digests) != fkey


def test_drop_caches_removes_only_cache_files(tmp_path):
    (tmp_path / "analysis" / "run01").mkdir(parents=True)
    (tmp_path / "analysis" / "run01" / "x.json").write_text("{}")
    (tmp_path / "analysis" / "notes.txt").write_text("mine")
    (tmp_path / "poses" / "run01").mkdir(parents=True)
    (tmp_path / "poses" / "run01" / "x.json").write_text("{}")
    (tmp_path / "poses" / "run01" / "x.json.tmp").write_text("{}")
    (tmp_path / "stages").mkdir()
    (tmp_path / "stages" / "families.json").write_text("{}")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "frame.key").write_text("k")
    (tmp_path / "target" / "frame.json").write_text("{}")
    (tmp_path / "export").mkdir()
    (tmp_path / "export" / "campaign.json").write_text("{}")
    assert P.drop_caches(tmp_path) == 5
    assert (tmp_path / "analysis" / "notes.txt").read_text() == "mine"                       # anything else survives, with its folder
    assert not (tmp_path / "poses").exists() and not (tmp_path / "stages").exists() and not (tmp_path / "target" / "frame.key").exists()
    assert (tmp_path / "target" / "frame.json").is_file() and (tmp_path / "export" / "campaign.json").is_file() and P.drop_caches(tmp_path / "nothing") == 0


# ---------------------------------------------------------------------------------------------------- the command line
def test_cli_requires_the_target_files(toy_dir, capsys):
    r1, r2 = toy_dir / "runs" / "run01", toy_dir / "runs" / "run02"
    assert cli.main(["import", str(r1)]) == 2 and "--target-pdb is required" in capsys.readouterr().err       # the reference is optional, the construct is not
    assert cli.main(["import", f"x={r1}", f"x={r2}", "--target-pdb", "a", "--reference-cif", "b"]) == 2 and "used twice" in capsys.readouterr().err
    assert cli.main(["import", f"zz={r1}", "--target-pdb", "a", "--reference-cif", "b", "--settings", "nope=x.json"]) == 2
    assert "name runs that are not given" in capsys.readouterr().err


def test_cli_command_line_uses_relative_paths_and_keeps_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.pdb").write_text("x")
    line = cli.command_line(["import", f"run01={tmp_path / 'data' / 'a.pdb'}", "--out", "work/x", "--target-pdb", str(tmp_path / "data" / "a.pdb")])
    assert line == "binder-review import run01=data/a.pdb --out work/x --target-pdb data/a.pdb"


def test_cli_parse_run_arguments(toy_dir, tmp_path):
    ns = cli.build_parser().parse_args(["import", f"run01={toy_dir / 'runs' / 'run01'}", str(toy_dir / "runs" / "run02"), "--settings",
                                        f"run01={tmp_path / 's.json'}", "--label", "run01=First run"])
    specs = cli._parse_run_args(ns)
    assert [s.run_id for s in specs] == ["run01", "run02"] and specs[0].label == "First run" and specs[0].settings == tmp_path / "s.json"
    assert specs[1].path == toy_dir / "runs" / "run02"


def test_cli_import_inspect_and_build_bundle_end_to_end(toy_dir, tmp_path, capsys):
    out, bundle = tmp_path / "proj", tmp_path / "bundle"
    args = ["import", f"run01={toy_dir / 'runs' / 'run01'}", f"run02={toy_dir / 'runs' / 'run02'}", "--target-pdb", str(toy_dir / "construct" / "toy_construct.pdb"),
            "--reference-cif", str(toy_dir / "reference" / "toy_target.cif"), "--name", "toy", "--out", str(out), "--bundle", str(bundle), "--quiet",
            "--no-guide", "--jobs", "2"]
    assert cli.main(args) == 0
    text = capsys.readouterr().out
    assert "Project toy" in text and "counts against the CSVs: all match" in text and (bundle / "index.html").is_file()
    info = json.loads((out / "project.json").read_text())
    assert info["schema"] == "binderscope.project/1" and info["counts"]["accepted"] == 22 and info["timings"]["total"] > 0 and info["warnings"] == []
    assert (out / "export" / "campaign.json").is_file() and (bundle / "data" / "campaign.js").is_file()
    assert "fonts.googleapis" not in (bundle / "index.html").read_text() and not (bundle / "guide.html").exists()
    assert cli.main(["inspect", str(out)]) == 0 and "Project toy" in capsys.readouterr().out
    assert cli.main(["inspect", str(bundle)]) == 0 and "Bundle bundle" in capsys.readouterr().out
    assert cli.main(["inspect", str(bundle), "--json"]) == 0 and json.loads(capsys.readouterr().out)["counts"]["accepted"] == 22
    other = tmp_path / "bundle2"
    assert cli.main(["build-bundle", str(out), "--out", str(other), "--no-guide"]) == 0 and (other / "board.html").is_file()
    assert cli.main(["build-bundle", str(tmp_path / "nothing"), "--out", str(tmp_path / "x")]) == 1 and "not an export folder" in capsys.readouterr().err


def test_cli_inspect_a_run_folder(toy_dir, capsys):
    assert cli.main(["inspect", str(toy_dir / "runs" / "run02"), "--construct", str(toy_dir / "construct" / "toy_construct.pdb")]) == 0
    out = capsys.readouterr().out
    assert "chain A: 102 residues, numbers 1-110, deleted 20-23, 80-83" in out and "construct file toy_construct.pdb: 110 residues" in out
    assert "accepted designs" in out and "hotspots (construct numbers)" in out and "[csv]" in out and "Warnings: none" in out
    assert cli.main(["inspect", str(toy_dir / "runs" / "run01"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["reader"] == "bindcraft1" and all(d["sequence"] is None for d in data["designs"])         # binder sequences are omitted


def test_cli_reports_inputs_that_do_not_fit_in_one_line_and_exit_2(toy_dir, tmp_path, capsys):
    """A FrameError (a chain the reference lacks, a construct that is not the target) is a problem of the arguments: one line, no traceback."""
    base = ["import", f"run01={toy_dir / 'runs' / 'run01'}", "--target-pdb", str(toy_dir / "construct" / "toy_construct.pdb"),
            "--reference-cif", str(toy_dir / "reference" / "toy_target.cif"), "--out", str(tmp_path / "p"), "--quiet"]
    assert cli.main(base + ["--reference-chain", "Q"]) == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("binder-review import: ") and "chain 'Q'" in err and "--reference-chain" in err and "\n" not in err and "Traceback" not in err
    assert cli.main(base + ["--partner-chains", "Z"]) == 2
    assert "partner chain" in capsys.readouterr().err


def test_cli_refuses_to_write_into_an_input_folder(toy_dir, tmp_path, capsys):
    run = toy_dir / "runs" / "run01"
    base = ["import", f"run01={run}", "--target-pdb", str(toy_dir / "construct" / "toy_construct.pdb"), "--reference-cif", str(toy_dir / "reference" / "toy_target.cif"), "--quiet"]
    before = sorted(p.name for p in run.iterdir())
    assert cli.main(base + ["--out", str(run / "work")]) == 2 and "input folders are never written to" in capsys.readouterr().err
    assert cli.main(base + ["--out", str(tmp_path / "p"), "--bundle", str(run)]) == 2
    assert sorted(p.name for p in run.iterdir()) == before and not (run / "work").exists() and not (run / "index.html").exists()


# ---------------------------------------------------------------------------------------------------------------- runs without hotspots
def test_a_run_without_any_hotspot_can_be_imported(toy_dir, tmp_path):
    """BindCraft can be run without hotspot residues: the Target_Hotspot column is then empty and there is no settings file. The first site
    of the target is then where the designs sit, said so in its source, and it still has a unit normal (the pages orient their views by it)."""
    import shutil

    import pandas as pd

    from binder_review import export as E

    run = tmp_path / "run02"
    shutil.copytree(toy_dir / "runs" / "run02", run)
    for name in ("final_design_stats.csv", "mpnn_design_stats.csv", "trajectory_stats.csv"):
        table = pd.read_csv(run / name)
        table["Target_Hotspot"] = ""
        table.to_csv(run / name, index=False)
    spec = P.ProjectSpec(name="nohot", runs=[P.RunSpec(path=run, run_id="run02")], target_pdb=toy_dir / "construct" / "toy_construct.pdb",
                         reference_cif=toy_dir / "reference" / "toy_target.cif", jobs=1)
    project = P.run_pipeline(spec, tmp_path / "work", log=P.Log(quiet=True))
    blobs = E.build_blobs(project)
    target = blobs.campaign["target"]
    site = target["sites"]["A1"]
    assert target["hotspots"] == [] and target["epitope"] == []
    assert np.linalg.norm(site["normal"]) == pytest.approx(1.0, abs=1e-3) and site["source"] == "no hotspots recorded: the residues the designs contact most"
    assert site["residues"] and set(site["residues"]) <= set(target["resi"])                      # reference numbers of residues that the designs contact
    E.write_export(blobs, tmp_path / "export")                                                    # validates every blob against the schemas
