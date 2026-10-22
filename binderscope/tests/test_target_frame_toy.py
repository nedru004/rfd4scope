"""Target frame (binder_review.target_frame) on the toy project: numbering found by sequence, the frame that maps the construct onto
the full-length reference, partner chain, per-run removed loops, placement of every design and the context checks against the truth
the generator wrote (which designs sit clean, collide with the rest of chain A, touch the other subunit or overlap a deleted loop)."""
from __future__ import annotations

import numpy as np
import pytest

from binder_review import structure as S
from binder_review import target_frame as TF
from toydata.testing import OFFSET


@pytest.fixture(scope="module")
def frame(toy_project):
    return toy_project.frame


def reference_xyz(truth, path):
    """Binder heavy atoms of a design file in the reference frame (the generator's ground truth)."""
    R, t = np.array(truth["frame_rotation"]), np.array(truth["frame_translation"])
    return (S.load_complex(path).binder.xyz - t) @ R


def test_numbering_is_found_by_sequence(frame):
    n = frame.meta["numbering"]
    assert frame.offset == OFFSET and n["offset"] == OFFSET and n["accession"] == "" and n["reference_offset"] == 0
    assert (n["construct_first"], n["construct_last"], n["n_construct"], n["length"]) == (1, 110, 110, 250)
    assert (n["uniprot_first"], n["uniprot_last"]) == (60, 169) and n["checks"]["construct_vs_reference"] == {"identical": 110, "n": 110}
    assert frame.partner_chains == ["B"] and frame.meta["chain"] == "A"
    assert any("no UniProt accession" in x for x in frame.meta["notes"])             # no accession: no domain strip, no membrane plane


def test_labels_and_conversions(frame, toy_run1, toy_project):
    assert frame.construct_to_uniprot(1) == 60 and frame.uniprot_to_construct(60) == 1 and frame.uniprot_to_construct(59) is None
    seq = toy_run1.target.sequence                                                       # construct residue 1 is reference residue 60
    assert frame.uniprot_sequence == "" and frame.residue(60) == seq[0] and frame.label(60) == f"{seq[0]}60"
    assert frame.labels([60, 61]) == [f"{seq[0]}60", f"{seq[1]}61"] and frame.has_residue(250) and not frame.has_residue(251)
    assert len(frame.ref_one) == 250
    run = toy_project.runs["run01"]
    assert run.target.uniprot_offset == OFFSET and toy_run1.target.uniprot_offset is None     # the frame assigns the offset to the pipeline's runs
    assert run.hotspot_labels("uniprot") == frame.labels(run.hotspots_uniprot())


def test_run_targets_and_removed_loops(frame, toy_truth):
    r1, r2 = frame.run_info("run01"), frame.run_info("run02")
    assert r1["removed"] == [] and r1["n_resi"] == 110 and (r1["first"], r1["last"]) == (60, 169)
    assert r2["removed"] == toy_truth["deleted_reference"] and r2["n_resi"] == 102
    assert r2["resi_ranges"] == [[60, 78], [83, 138], [143, 169]]
    assert r2["cut_points"] == [60, 78, 83, 138, 143, 169]                             # both flanks of every removed loop, plus the ends
    assert r1["hotspots"] == toy_truth["hotspots_reference"]["run01"] and r2["hotspots"] == toy_truth["hotspots_reference"]["run02"]
    assert r1["hotspots_construct"] == toy_truth["hotspots_construct"]["run01"]
    assert set(r1["hotspots"]) < set(r2["hotspots"]) and r2["sequence_matches_reference"] and r2["consistent"]
    assert [list(x) for x in r2["removed_native"]] == [[1, 59], [79, 82], [139, 142], [170, 250]]


def test_reference_geometry_and_dimer(frame, toy_reference):
    a = frame.chain_a
    assert a.n_residues == 250 and list(a.residues.number) == list(range(1, 251))
    sf = S.read_structure(str(toy_reference))
    assert np.allclose(a.xyz, sf.groups["A"].xyz)                                      # the frame holds the file coordinates, untouched
    assert frame.meta["dimer"] is not None
    buried = frame.partner_buried()
    assert buried and all(v >= 0 for v in buried.values()) and max(buried) <= 250


def test_save_and_load_round_trip(frame, tmp_path):
    frame.save(tmp_path)
    again = TF.TargetFrame.load(tmp_path)
    assert again.meta == frame.meta and np.array_equal(again.numbers, frame.numbers) and np.allclose(again.chain_a.xyz, frame.chain_a.xyz)
    assert again.run_info("run02") == frame.run_info("run02")


def test_every_design_is_placed_on_the_reference_chain(toy_project, toy_truth):
    n = 0
    for (rid, did), rec in toy_project.poses.items():
        pl = rec["placement"]
        assert pl["rmsd"] < 0.3 and pl["n"] == (110 if rid == "run01" else 102), (did, pl["rmsd"])
        n += 1
        # the placed binder sits where the generator put it in the reference frame
        placed = np.array(rec["ca"]).reshape(-1, 3)
        cx = S.load_complex(toy_project.runs[rid].design(did).structure_path)
        truth_ca = (cx.binder.ca_xyz() - np.array(toy_truth["frame_translation"])) @ np.array(toy_truth["frame_rotation"])
        assert placed.shape == truth_ca.shape and np.abs(placed - truth_ca).max() < 0.8, did      # the design's own target differs a little from the construct
    assert n == 30


def test_context_flags_follow_the_goal_of_every_pose(toy_project, toy_truth):
    seen = {k: 0 for k in ("clean_hot", "clean_away", "collide_out", "touch_other", "collide_gap")}
    for (rid, did), rec in toy_project.poses.items():
        kind = toy_truth["design_kind"][rid][did]
        if kind is None:
            continue
        m = rec["metrics"]
        seen[kind] += 1
        if kind in ("clean_hot", "clean_away"):
            assert m["rest_n3"] == 0 and m["olig_n3"] == 0 and m["rest_min"] > 3.0, (did, kind, m)
        if kind == "collide_out":
            assert m["rest_n3"] >= 10, (did, m)                                        # fails the rest-of-protein check
        if kind == "collide_gap":
            assert m["rest_n3"] >= 10, (did, m)                                        # the removed loop is part of the rest for this run
        if kind == "touch_other":
            assert m["olig_min"] < 5.0 and m["rest_n3"] == 0, (did, m)
    assert all(v >= 1 for v in seen.values())


def test_hotspots_touched_match_the_truth(toy_project, toy_truth):
    for (rid, did), rec in toy_project.poses.items():
        kind = toy_truth["design_kind"][rid][did]
        if kind == "clean_hot":
            assert len(rec["hot"]) >= 2, did
        if kind == "clean_away":
            assert rec["hot"] == [], did
        assert set(rec["hot"]) <= set(toy_truth["hotspots_reference"][rid]) and set(rec["hot"]) <= set(rec["contacted"])
    # a hotspot that run02 lacks never counts for run01-only designs and vice versa
    assert all(max(rec["contacted"], default=60) <= 169 and min(rec["contacted"], default=169) >= 60 for rec in toy_project.poses.values())


def test_contacts_are_inside_each_runs_own_target(toy_project, toy_truth):
    removed = {r for a, b in toy_truth["deleted_reference"] for r in range(a, b + 1)}
    for (rid, did), rec in toy_project.poses.items():
        if rid == "run02":
            assert not set(rec["contacted"]) & removed, did


def test_check_design_verdicts_follow_the_truth(toy_project, toy_truth):
    frame, run = toy_project.frame, toy_project.runs["run01"]
    levels = {}
    for d in run.accepted():
        kind = toy_truth["design_kind"]["run01"][d.id]
        chk = TF.check_design(frame, d.structure_path, "run01", design_id=d.id, status=d.status, rank=d.rank, model_k=d.structure_model).to_dict()
        v = chk["verdicts"]
        levels.setdefault(kind, set()).add((v["rest"]["level"], v["partner"]["level"]))
        assert chk["id"] == d.id and chk["run"] == "run01" and chk["place_rmsd"] < 0.3
        if kind in ("clean_hot", "clean_away"):
            assert v["rest"]["level"] == "pass" and v["partner"]["level"] == "pass", (d.id, v)
        if kind == "collide_out":
            assert v["rest"]["level"] == "fail", (d.id, v["rest"])
        if kind == "touch_other":
            assert v["partner"]["level"] in ("warn", "fail"), (d.id, v["partner"])
    assert {"clean_hot", "clean_away", "collide_out", "touch_other"} <= set(levels)


def test_check_run_rows_are_plain_json_without_sequences(toy_project):
    import json

    run = toy_project.runs["run01"]
    rows = TF.check_run(toy_project.frame, run, run_id="run01", include_rejected=True)
    assert len(rows) == 20 and [r.status for r in rows[:12]] == ["accepted"] * 12 and rows[0].rank == 1
    blob = json.dumps([r.to_dict() for r in rows])
    assert all(x.sequence not in blob for x in run.designs)
    table = TF.report_tables(toy_project.frame, [r.to_dict() for r in rows])
    assert isinstance(table, str) and "rest" in table.lower()


def test_build_rejects_inputs_that_do_not_fit(toy_dir, toy_run1, tmp_path):
    ref, con = toy_dir / "reference" / "toy_target.cif", toy_dir / "construct" / "toy_construct.pdb"
    with pytest.raises(TF.FrameError):
        TF.build_frame(ref, con, {"run01": toy_run1}, None, chain="Z")                 # a chain the reference does not have
    # a construct whose sequence is not part of the reference
    from toydata.structures import write_pdb
    from toydata import geometry as G

    at = G.Atoms()
    for i, c in enumerate("WWWWWWWWWWWWWWWWWWWWWWWWWWWWWW", start=1):
        for nm, el in (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")):
            at.name.append(nm), at.element.append(el), at.resname.append(G.AA3[c]), at.resnum.append(i), at.chain.append("A")
            at.bfactor.append(1.0), at.xyz_list.append(np.array([i * 3.8, 0.0, float(len(at.name))]))
    bad = tmp_path / "bad.pdb"
    write_pdb(bad, [at])
    with pytest.raises(TF.FrameError):
        TF.build_frame(ref, bad, {}, None)


def test_frame_without_a_partner_still_works(toy_dir, toy_run1, toy_run2):
    frame = TF.build_frame(toy_dir / "reference" / "toy_target.cif", toy_dir / "construct" / "toy_construct.pdb",
                           {"run01": toy_run1, "run02": toy_run2}, None, partner_chains=())
    assert frame.partner_chains == [] and frame.offset == OFFSET
    rec = TF.check_design(frame, toy_run1.accepted()[0].structure_path, "run01").to_dict()
    assert rec["metrics"].get("olig_n3") in (None, 0) and rec["verdicts"]["rest"]["level"] in ("pass", "warn", "fail")


def test_block_shapes_for_the_blobs(frame):
    tb = frame.target_block(["run01", "run02"])
    assert tb["resi"] == list(range(60, 170)) and tb["length"] == 250
    assert tb["numbering"]["input_offset"] == -OFFSET and set(tb["hotspots_by_run"]) == {"run01", "run02"}
    ib = frame.inspector_block(["run01", "run02"])
    assert ib["target_resi"] == tb["resi"] and len(ib["target_seq"]) == len(tb["resi"])
