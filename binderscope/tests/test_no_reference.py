"""Importing without a full-length reference structure, and the Context check's standing (advisory unless the reference is experimental).

Without a reference the construct is the frame: designs are superposed on it so they share one frame, numbering stays the construct's, and every
check that needs something outside the construct is absent (not zero) and says why. Everything computed from the designs' own structures must
be identical to the build that had a reference. A reference that holds nothing outside the construct is a different situation with a
different reason, and neither may read as "checked and clear"."""
from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest

from binder_review import cli
from binder_review import export as E
from binder_review import pipeline as P
from binder_review import structure as S
from binder_review import target_frame as TF
from toydata.testing import OFFSET

CONTEXT_METRICS = ("olig_min", "olig_n3", "olig_n4", "olig_n", "rest_min", "rest_n3", "rest_n4", "cut_n", "cov_n", "mem_excess")
#: the placement numbers are against the construct instead of the reference chain, so they are not compared with the reference build
PLACEMENT_METRICS = ("place_rmsd", "place_iface_rmsd", "place_iface_n", "place_global_rmsd")


def _spec(toy_dir: Path, reference, runs=("run01", "run02"), **kw) -> P.ProjectSpec:
    return P.ProjectSpec(name="noref", runs=[P.RunSpec(path=toy_dir / "runs" / r, run_id=r) for r in runs],
                         target_pdb=toy_dir / "construct" / "toy_construct.pdb", reference_cif=reference, jobs=2, **kw)


@pytest.fixture(scope="module")
def noref_project(toy_dir, tmp_path_factory):
    return P.run_pipeline(_spec(toy_dir, None), tmp_path_factory.mktemp("noref_work"), log=P.Log(quiet=True))


@pytest.fixture(scope="module")
def noref_blobs(noref_project):
    return E.build_blobs(noref_project)


@pytest.fixture(scope="module")
def construct_ref_blobs(toy_dir, tmp_path_factory):
    """The construct given as its own reference, run01 only (it deletes nothing): a reference that holds nothing outside the construct."""
    spec = _spec(toy_dir, toy_dir / "construct" / "toy_construct.pdb", runs=("run01",))
    return E.build_blobs(P.run_pipeline(spec, tmp_path_factory.mktemp("selfref_work"), log=P.Log(quiet=True)))


# ---------------------------------------------------------------------------------------------------------------- the frame
def test_the_frame_without_a_reference_is_the_construct(noref_project, toy_construct):
    f = noref_project.frame
    assert not f.reference_given and f.partner_chains == [] and f.glycans == [] and f.offset == 0
    assert f.reference_class["kind"] == "none" and f.meta["reference"]["label"].endswith("no full-length reference was given)")
    assert any("no reference structure was given" in n for n in f.meta["notes"])
    # nothing outside the construct is known: its ends are not reported as cuts, only the loops a run deleted inside it
    assert f.native_range == [1, 110]
    assert f.run_info("run01")["cut_points"] == [] and f.run_info("run02")["cut_points"] != []
    assert f.meta["membrane"]["known"] is False and "no reference" in f.meta["membrane"]["label"]


def test_the_context_has_no_metric_at_all(noref_project):
    for (rid, did), pose in noref_project.poses.items():
        assert all(pose["metrics"][k] is None for k in CONTEXT_METRICS), (rid, did)          # absent, never 0: a 0 reads as "checked, clear"
        assert pose["glycan"] == {} and pose["ctx"] == {} and set(pose["octx"]) == {"rms_head"}


def test_deleted_loops_are_not_the_rest_of_the_protein(noref_project):
    """run02 deleted loops from the construct; the construct file still holds them, but they are not what the check is about."""
    assert noref_project.frame.run_info("run02")["removed"] and noref_project.frame._rest_env("run02") is None
    assert noref_project.frame._rest_block(["run02"])["traces"] == []


def test_the_frame_refuses_reference_arguments_without_a_reference(toy_construct):
    runs = {"r": {"hotspots": [10], "numbers": list(range(1, 111))}}
    with pytest.raises(TF.FrameError, match="construct is the frame"):
        TF.build_frame(None, toy_construct, runs, partner_chains=["B"])
    with pytest.raises(TF.FrameError, match="construct is the frame"):
        TF.build_frame(None, toy_construct, runs, chain="B")


# ---------------------------------------------------------------------------------------------------------------- the designs' own numbers
def _without_epoch(x):
    """The epoch hash and its flag text carry the run's hotspot and construct numbers, which are construct numbers here and reference numbers there."""
    if isinstance(x, dict):
        return {k: _without_epoch(v) for k, v in x.items() if k not in ("epoch", "flags")}
    return x



def test_everything_computed_from_the_designs_is_unchanged(noref_blobs, toy_blobs):
    a = {d["id"]: d for d in noref_blobs.campaign["designs"]}
    b = {d["id"]: d for d in toy_blobs.campaign["designs"]}
    assert set(a) == set(b)
    for k in a:
        assert {m: v for m, v in a[k]["m"].items() if m not in CONTEXT_METRICS + PLACEMENT_METRICS} == \
               {m: v for m, v in b[k]["m"].items() if m not in CONTEXT_METRICS + PLACEMENT_METRICS}, k
        for field in ("bb", "mode", "liab", "status"):
            assert a[k].get(field) == b[k].get(field), (k, field)
        for field in ("tc", "hot"):               # the toy construct is numbered 1 to 110 and sits at OFFSET in its reference; without one the numbers stay the construct's
            assert [n + OFFSET for n in a[k][field]] == b[k][field], (k, field)
    assert noref_blobs.campaign["modes"].keys() == toy_blobs.campaign["modes"].keys()
    assert _without_epoch(noref_blobs.campaign["diversity"]) == _without_epoch(toy_blobs.campaign["diversity"])
    assert {d["bb"] for d in a.values() if d["status"] == "accepted"} == {d["bb"] for d in b.values() if d["status"] == "accepted"}
    # the context metrics, by contrast, differ because the reference build has them
    assert any(d["m"]["rest_n3"] is not None for d in b.values()) and all(d["m"]["rest_n3"] is None for d in a.values())


def test_the_target_carries_the_construct_numbering(noref_blobs):
    t = noref_blobs.campaign["target"]
    assert t["resi"][0] == 1 and t["numbering"]["input_offset"] == 0 and t["reference"]["given"] is False and t["reference"]["pdb"] == ""
    assert t["olig"] == {} and t["rest"]["traces"] == [] and t["glycan"] == []


# ---------------------------------------------------------------------------------------------------------------- saying which situation it is
def test_no_reference_is_recorded_everywhere_the_pages_read(noref_blobs):
    prof, man, camp, ins = noref_blobs.profile, noref_blobs.manifest, noref_blobs.campaign, noref_blobs.inspect
    assert not any(prof["caps"][k] for k in ("olig", "rest_ctx", "glycan", "membrane"))
    assert set(prof["caps_off"]) == {"olig", "rest_ctx", "glycan", "membrane"} and {v["why"] for v in prof["caps_off"].values()} == {"no_reference"}
    assert prof["target"]["reference"]["given"] is False and prof["target"]["reference"]["kind"] == "none"
    assert man["target"]["reference_given"] is False and man["target"]["reference_pdb"] == "" and man["caps_off"] == prof["caps_off"]
    assert all(s["role"] != "reference_structure" for s in man["sources"])                  # there is no such input, and none is invented
    assert any("superposed on the construct file" in n for n in man["notes"]) and not any("crystal chain" in n for n in man["notes"])
    assert all(r["target"]["reference_given"] is False for r in camp["runs"]) and all("No reference structure was given" in " ".join(r["notes"]) for r in man["runs"])
    hl = {h["id"]: h for h in camp["highlights"]}
    assert "context_off" in hl and hl["context_off"]["severity"] == "warn" and "was not tested" in hl["context_off"]["text"] and "rest_overlap" not in hl
    assert all(d["ctx"] is None for d in ins["designs"].values())                            # null, not {}: an empty block is truthy in the pages


def test_a_reference_that_holds_nothing_is_a_different_situation(construct_ref_blobs):
    prof, camp = construct_ref_blobs.profile, construct_ref_blobs.campaign
    off = prof["caps_off"]
    assert {k: v["why"] for k, v in off.items()}["olig"] == "reference_lacks" and off["rest_ctx"]["why"] == "reference_lacks"
    assert "nothing outside the construct" in off["rest_ctx"]["note"] and "no other chain" in off["olig"]["note"]
    assert prof["target"]["reference"]["given"] is True
    hl = {h["id"]: h for h in camp["highlights"]}
    assert "holds nothing outside the construct" in hl["context_off"]["text"] and "No reference structure was given" not in hl["context_off"]["text"]


def test_a_reference_with_the_data_has_no_context_reason(toy_blobs):
    assert not set(toy_blobs.profile.get("caps_off", {})) & {"olig", "rest_ctx"}
    assert toy_blobs.profile["caps"]["olig"] and toy_blobs.profile["caps"]["rest_ctx"]


# ---------------------------------------------------------------------------------------------------------------- the Context check's standing
def _write_pdb(path: Path, header: list[str], bfactor) -> Path:
    lines = list(header)
    serial = 1
    for i in range(1, 41):
        for name, el in (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")):
            lines.append(f"ATOM  {serial:5d} {name:^4s} ALA A{i:4d}    {i * 3.8:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00{bfactor(i, name):6.2f}           {el}")
            serial += 1
    path.write_text("\n".join(lines + ["END"]) + "\n")
    return path


def test_the_reference_is_classified_from_its_own_file(tmp_path):
    varied = lambda i, n: 20.0 + (hash(n) % 7) + i * 0.1      # noqa: E731 - atom-wise B-factors: an experimental structure
    plddt = lambda i, n: 70.0 + (i % 20)                       # noqa: E731 - one value per residue: how a pLDDT is stored
    exp = TF.classify_reference(_write_pdb(tmp_path / "x.pdb", ["EXPDTA    X-RAY DIFFRACTION", "REMARK   2 RESOLUTION.    1.80 ANGSTROMS."], varied))
    assert (exp["kind"], exp["treated_as"], exp["resolution"]) == ("experimental", "experimental", 1.8) and "X-RAY DIFFRACTION" in exp["evidence"][0]
    pred = TF.classify_reference(_write_pdb(tmp_path / "p.pdb", [], plddt))
    assert (pred["kind"], pred["treated_as"]) == ("predicted", "predicted") and "pLDDT" in pred["evidence"][0]
    unk = TF.classify_reference(_write_pdb(tmp_path / "u.pdb", [], varied))                  # no method, nothing that looks like a pLDDT: cannot tell
    assert (unk["kind"], unk["treated_as"]) == ("unknown", "predicted") and "cannot tell" in unk["evidence"][-1]
    both = TF.classify_reference(_write_pdb(tmp_path / "b.pdb", ["EXPDTA    THEORETICAL MODEL"], varied))
    assert both["kind"] == "predicted"
    assert TF.classify_reference(tmp_path / "missing.pdb")["treated_as"] == "predicted" and TF.classify_reference(None)["kind"] == "none"


def test_an_mmcif_with_a_model_record_is_predicted_even_when_it_names_a_method(tmp_path):
    cif = tmp_path / "m.cif"
    cif.write_text("data_m\n_exptl.method 'X-RAY DIFFRACTION'\n_ma_model_list.model_id 1\n_ma_model_list.model_type 'Ab initio model'\n#\n")
    c = TF.classify_reference(cif)
    assert c["kind"] == "predicted" and any("both a method and a model" in e for e in c["evidence"])
    xr = tmp_path / "x.cif"
    xr.write_text("data_x\n_exptl.method 'ELECTRON MICROSCOPY'\n_em_3d_reconstruction.resolution 2.9\n#\n")
    assert TF.classify_reference(xr)["kind"] == "experimental" and TF.classify_reference(xr)["resolution"] == 2.9


def test_context_is_advisory_by_default_and_hard_on_evidence_or_by_choice(toy_project):
    """The toy reference records no method and its B-factors are one value per residue: predicted, so advisory. An experimental reference makes it
    hard; so does the user."""
    def ctx_of(project):
        return E.build_blobs(project).profile["checks"]["context"]

    base = ctx_of(toy_project)
    assert base["hard"] is False and base["hard_setting"] == "auto" and "predicted model" in base["hard_basis"]
    frame = TF.TargetFrame(copy.deepcopy(toy_project.frame.meta), toy_project.frame.arrays)
    frame.meta["reference"]["class"] = {"kind": "experimental", "treated_as": "experimental", "method": "X-RAY DIFFRACTION", "resolution": 1.8,
                                       "evidence": ["deposited method: X-RAY DIFFRACTION, resolution 1.8 A"]}
    exp = ctx_of(dataclasses.replace(toy_project, frame=frame))
    assert exp["hard"] is True and "experimental" in exp["hard_basis"]
    forced = ctx_of(dataclasses.replace(toy_project, spec=dataclasses.replace(toy_project.spec, context_hard="hard")))
    assert forced["hard"] is True and forced["hard_setting"] == "hard" and "--context-check" in forced["hard_basis"]
    off = ctx_of(dataclasses.replace(dataclasses.replace(toy_project, frame=frame), spec=dataclasses.replace(toy_project.spec, context_hard="advisory")))
    assert off["hard"] is False and off["hard_setting"] == "advisory"
    profiled = copy.deepcopy(toy_project.profile)
    profiled.setdefault("checks", {}).setdefault("context", {})["hard"] = True                  # the profile is the other home of the switch
    assert ctx_of(dataclasses.replace(toy_project, profile=profiled))["hard"] is True and "the profile" in ctx_of(dataclasses.replace(toy_project, profile=profiled))["hard_basis"]
    with pytest.raises(E.ExportError, match="auto, hard or advisory"):
        ctx_of(dataclasses.replace(toy_project, spec=dataclasses.replace(toy_project.spec, context_hard="maybe")))


def test_the_manifest_records_what_was_concluded_and_on_what_evidence(toy_blobs):
    t = toy_blobs.manifest["target"]
    assert t["reference_class"]["kind"] == "predicted" and t["reference_class"]["evidence"] and t["context_check"]["hard"] is False
    ref = toy_blobs.profile["target"]["reference"]
    assert ref["kind"] == "predicted" and ref["treated_as"] == "predicted" and ref["evidence"] == t["reference_class"]["evidence"]


def test_with_no_reference_the_context_check_is_absent_not_advisory(noref_blobs):
    ctx = noref_blobs.profile["checks"]["context"]
    assert ctx["hard"] is False and "not run" in ctx["hard_basis"]


# ---------------------------------------------------------------------------------------------------------------- the command line
def test_cli_without_a_reference(toy_dir, tmp_path, capsys):
    out, bundle = tmp_path / "proj", tmp_path / "bundle"
    args = ["import", f"run01={toy_dir / 'runs' / 'run01'}", "--target-pdb", str(toy_dir / "construct" / "toy_construct.pdb"), "--name", "noref",
            "--out", str(out), "--bundle", str(bundle), "--quiet", "--no-guide", "--jobs", "2"]
    assert cli.main(args) == 0
    text = capsys.readouterr().out
    assert "not tested" in text and (bundle / "index.html").is_file()
    info = json.loads((out / "project.json").read_text())
    assert info["spec"]["reference_cif"] is None and info["spec"]["reference_given"] is False
    rec = json.loads((out / "runs" / "run01" / "runrecord.json").read_text())
    assert rec["target_frame"]["reference_given"] is False and "not run" in rec["target_frame"]["context_checks"]
    assert (bundle / "data" / "campaign.js").is_file()


def test_cli_reference_arguments_need_a_reference(toy_dir, tmp_path, capsys):
    base = ["import", f"run01={toy_dir / 'runs' / 'run01'}", "--target-pdb", str(toy_dir / "construct" / "toy_construct.pdb"), "--out", str(tmp_path / "p"), "--quiet"]
    for extra, flag in ((["--reference-chain", "B"], "--reference-chain"), (["--reference-id", "1abc"], "--reference-id"), (["--partner-chains", "B"], "--partner-chains")):
        assert cli.main(base + extra) == 2
        err = capsys.readouterr().err
        assert flag in err and "describe the reference structure" in err
    assert cli.main(["import", f"run01={toy_dir / 'runs' / 'run01'}", "--out", str(tmp_path / "p")]) == 2 and "--target-pdb is required" in capsys.readouterr().err


def test_cli_context_check_option_is_validated(toy_dir, tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["import", "x", "--target-pdb", "a", "--context-check", "maybe"])
    capsys.readouterr()
