"""Binding modes, the 2-D embedding, hotspot coverage and near-miss rescue (binder_review.modes): synthetic footprints with a known
structure of three faces, and the modes of the toy project (every design assigned, hotspot coverage and near-miss rescue against the
plan of the generator)."""
from __future__ import annotations

import importlib.util
import json
import sys

import numpy as np
import pytest

from binder_review import config, model, modes
from binder_review.modes import RunInfo, build_modes
from toydata.plans import RUN1
from toydata.testing import repo_root

REPO = repo_root()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("BINDERSCOPE_OFFLINE", "1")


# ---------------------------------------------------------------------------------------------
# Synthetic project: three faces of a fake target, two runs with different hotspots
# ---------------------------------------------------------------------------------------------

FACES = {"a": range(10, 20), "b": range(40, 50), "c": range(70, 80)}
REF_CA = {r: np.array([float(r), 0.5 * (r % 7), 0.2 * r]) for r in range(1, 100)}
NAMES = {r: "ACDEFGHIKLMNPQRSTVWY"[r % 20] for r in range(1, 100)}


def _fp(face: str, shift: int, rng: np.random.Generator, drop: int = 2) -> list[int]:
    base = [r + shift for r in FACES[face]]
    return sorted(rng.choice(base, size=len(base) - drop, replace=False).tolist())


def synthetic(n_per_face: int = 8, n_rej: int = 6, seed: int = 0):
    rng = np.random.default_rng(seed)
    foot: dict[str, list[int]] = {}
    r1 = RunInfo("r1", hotspots=[12, 45], label="first")
    r2 = RunInfo("r2", hotspots=[14, 73], label="second")
    i = 0
    for face in FACES:
        for j in range(n_per_face):
            run = r1 if j % 2 == 0 else r2
            k = f"{run.run_id}_{face}{j}"
            foot[k] = _fp(face, int(rng.integers(-1, 2)), rng)
            run.designs[k] = "accepted"
            i += 1
    rej_truth = {}
    for j in range(n_rej):
        face = list(FACES)[j % 3]
        k = f"r1_rej{j}"
        foot[k] = _fp(face, 0, rng, drop=3)
        r1.designs[k] = "rejected"
        rej_truth[k] = face
    return foot, {"r1": r1, "r2": r2}, rej_truth


def test_jaccard_and_mds_basics():
    a = np.array([[1, 1, 0, 0], [1, 0, 1, 0], [0, 0, 0, 0]], float)
    d = modes.jaccard_distance(a, a)
    assert d[0, 0] == 0 and d[0, 1] == pytest.approx(1 - 1 / 3)
    assert d[2, 2] == 1.0                      # two empty sets: nothing shared
    pts = np.array([[0, 0], [3, 0], [0, 4], [3, 4.0]])
    dist = np.sqrt(((pts[:, None] - pts[None]) ** 2).sum(-1))
    x = modes.classical_mds(dist, 2)
    back = np.sqrt(((x[:, None] - x[None]) ** 2).sum(-1))
    assert np.allclose(back, dist, atol=1e-8)
    assert np.array_equal(x, modes.classical_mds(dist, 2))


def test_embedding_range_determinism_and_small_sets():
    foot, infos, _ = synthetic()
    res = build_modes(foot, None, infos, residue_names=NAMES, ref_ca=REF_CA)
    again = build_modes(foot, None, infos, residue_names=NAMES, ref_ca=REF_CA)
    assert res.to_dict() == again.to_dict()
    assert res.embedding["method"] == "tsne"
    xy = np.array([v["emb"] for v in res.assign.values()])
    assert xy.min() >= 0 and xy.max() <= 1
    acc = np.array([v["emb"] for k, v in res.assign.items() if v["status"] == "accepted"])
    assert np.allclose(acc.min(0), 0, atol=1e-5) and np.allclose(acc.max(0), 1, atol=1e-5)
    # small sets fall back to MDS and trivial layouts
    for n in (1, 2, 5):
        d = np.abs(np.subtract.outer(np.arange(n, dtype=float), np.arange(n, dtype=float)))
        e, method = modes.embed_2d(d)
        assert e.shape == (n, 2) and 0 <= e.min() and e.max() <= 1
        assert method in ("trivial", "mds")


def test_modes_recover_the_faces_and_are_ordered_by_size():
    foot, infos, rej_truth = synthetic(n_per_face=8)
    res = build_modes(foot, None, infos, residue_names=NAMES, ref_ca=REF_CA)
    assert 3 <= len(res.modes) <= modes.MAX_MODES
    assert list(res.modes) == [f"M{i + 1}" for i in range(len(res.modes))]
    sizes = [m["n"] for m in res.modes.values()]
    assert sizes == sorted(sizes, reverse=True) and sum(sizes) == 24
    # every face lands in its own mode(s): no mode mixes faces
    face_of_mode: dict[str, set] = {}
    for k, a in res.assign.items():
        if a["status"] == "accepted":
            face_of_mode.setdefault(a["mode"], set()).add(k.split("_")[1][0])
    assert all(len(v) == 1 for v in face_of_mode.values())
    # rejected designs go to a mode of their own face
    for k, face in rej_truth.items():
        assert face in face_of_mode[res.assign[k]["mode"]]
    assert res.k_selection["k"] == len(res.modes)


def test_rejected_designs_never_change_the_clustering():
    foot, infos, _ = synthetic()
    with_rej = build_modes(foot, None, infos, residue_names=NAMES, ref_ca=REF_CA)
    acc_only = {k: v for k, v in foot.items() if "rej" not in k}
    infos2 = {rid: RunInfo(i.run_id, i.hotspots, {k: s for k, s in i.designs.items() if k in acc_only}) for rid, i in infos.items()}
    without = build_modes(acc_only, None, infos2, residue_names=NAMES, ref_ca=REF_CA)
    for k in acc_only:
        assert with_rej.assign[k]["mode"] == without.assign[k]["mode"]
        assert with_rej.assign[k]["emb"] == without.assign[k]["emb"]
    assert {m: v["n"] for m, v in with_rej.modes.items()} == {m: v["n"] for m, v in without.modes.items()}
    assert sum(v["n_rejected"] for v in with_rej.modes.values()) == 6


def test_names_notes_and_hotspots_are_generated_from_the_data():
    foot, infos, _ = synthetic()
    res = build_modes(foot, None, infos, residue_names=NAMES, ref_ca=REF_CA)
    names = [m["name"] for m in res.modes.values()]
    assert len(set(names)) == len(names)
    for m in res.modes.values():
        assert m["auto_name"] is True and m["name"] and len(m["name"]) <= modes.NAME_MAX_CHARS
        assert m["enr"] and m["top"] and m["note"].endswith(".")
        # the name quotes residues by letter and number from the names table
        assert any(f"{NAMES[r]}{r}" in m["name"] for r in m["enr"])
        assert set(m["top"]) <= {r for fp in foot.values() for r in fp}
    # hotspot touch rates only list hotspots some design of the mode could touch
    for mid, m in res.modes.items():
        for h, share in m["hot_share"].items():
            assert 0 <= share <= 1 and int(h) in (12, 45, 14, 73)
    # hotspot 45 sits on face b: the mode(s) of face b touch it more than the mode(s) of face a
    face_b = [m for m in res.modes.values() if 45 in m["enr"] or 45 in m["top"]]
    assert face_b and all("45" in m["hot_share"] for m in face_b)


def test_hotspot_coverage_and_wrong_site():
    foot, infos, _ = synthetic()
    foot["r1_wrong"] = [90, 91, 92]
    infos["r1"].designs["r1_wrong"] = "accepted"
    foot["r2_edge"] = [14, 73]
    infos["r2"].designs["r2_edge"] = "accepted"
    res = build_modes(foot, None, infos, residue_names=NAMES, ref_ca=REF_CA)
    w = res.hotspots_touched["r1_wrong"]
    assert w == {"run": "r1", "hot": [], "hot_of": 2, "frac": 0.0, "wrong_site": True}
    e = res.hotspots_touched["r2_edge"]
    assert e["hot"] == [14, 73] and e["frac"] == 1.0 and not e["wrong_site"]
    # a run without hotspots can neither touch nor miss them
    infos["r2"].hotspots = []
    res2 = build_modes(foot, None, infos, residue_names=NAMES, ref_ca=REF_CA)
    assert res2.hotspots_touched["r2_edge"]["frac"] is None and res2.hotspots_touched["r2_edge"]["wrong_site"] is False
    expected = sum(1 for k, v in res.hotspots_touched.items() if v["wrong_site"] and res.assign[k]["status"] == "accepted")
    assert expected >= 1 and "r1_wrong" in res.hotspots_touched
    assert sum(m["n_wrong_site"] for m in res.modes.values()) == expected


def test_choose_k_balance_rule_and_small_inputs():
    # one huge blob plus two small ones: pure silhouette would keep the blob whole
    rng = np.random.default_rng(1)
    pts = np.vstack([rng.normal(0, 1, (40, 2)), rng.normal(8, 0.2, (4, 2)), rng.normal(-8, 0.2, (4, 2))])
    d = np.sqrt(((pts[:, None] - pts[None]) ** 2).sum(-1))
    lab, kc = modes.choose_k(d, pts, (3, 7), max_share=0.4, min_size=3)
    assert kc.k >= 4 and max(np.bincount(lab)[1:]) / len(pts) <= 0.4 and "no mode above" in kc.rule
    lab, kc = modes.choose_k(d, pts, (3, 7), max_share=1.0, min_size=1)
    assert kc.k == max(r["k"] for r in kc.table if r["silhouette"] == max(t["silhouette"] for t in kc.table))
    lab, kc = modes.choose_k(d[:2, :2], pts[:2], (3, 7))
    assert kc.k == 1 and set(lab) == {1}
    foot = {"a": [1, 2], "b": [2, 3], "c": [50, 51]}
    info = {"r": RunInfo("r", [2], {"a": "accepted", "b": "accepted", "c": "accepted"})}
    res = build_modes(foot, None, info, ref_ca=REF_CA)
    assert 1 <= len(res.modes) <= 2 and sum(m["n"] for m in res.modes.values()) == 3
    with pytest.raises(ValueError):
        build_modes({"a": [1]}, None, {"r": RunInfo("r", [], {"a": "rejected"})}, ref_ca=REF_CA)
    with pytest.raises(ValueError):
        build_modes({"zz": [1]}, None, info, ref_ca=REF_CA)
    with pytest.raises(ValueError):                        # footprint outside the run's target
        build_modes({"a": [1, 400]}, None, {"r": RunInfo("r", [], {"a": "accepted"}, residues=set(range(1, 100)))}, ref_ca=REF_CA)


def test_empty_footprint_does_not_break():
    foot, infos, _ = synthetic()
    foot["r1_empty"] = []
    infos["r1"].designs["r1_empty"] = "rejected"
    res = build_modes(foot, None, infos, residue_names=NAMES, ref_ca=REF_CA)
    assert res.assign["r1_empty"]["no_contacts"] is True and res.hotspots_touched["r1_empty"]["wrong_site"] is True


def test_modes_block_matches_the_campaign_schema():
    spec = importlib.util.spec_from_file_location("validate_schema", REPO / "scripts" / "validate_schema.py")
    if spec is None or not (REPO / "scripts" / "validate_schema.py").is_file():
        pytest.skip("schema validator not available")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_schema"] = mod
    spec.loader.exec_module(mod)
    foot, infos, _ = synthetic()
    res = build_modes(foot, None, infos, residue_names=NAMES, ref_ca=REF_CA)
    schema_dir = REPO / "docs" / "schema"
    campaign = json.loads((schema_dir / "campaign.schema.json").read_text())
    from jsonschema import Draft202012Validator

    sub = {"$ref": campaign["$id"] + "#/$defs/modes"}
    v = Draft202012Validator(sub, registry=mod.registry())
    errs = [e.message for e in v.iter_errors(json.loads(json.dumps(res.modes)))]
    assert not errs, errs
    emb = Draft202012Validator({"$ref": campaign["$id"] + "#/$defs/design/properties/emb"}, registry=mod.registry())
    assert not [e.message for k, a in res.assign.items() for e in emb.iter_errors(a["emb"])]
    json.dumps(res.to_dict())


def test_sites_name_the_overlapping_site():
    foot, infos, _ = synthetic()
    sites = {"A1": {"residues": list(range(10, 20))}, "A2": {"residues": list(range(40, 50))}}
    res = build_modes(foot, None, infos, residue_names=NAMES, ref_ca=REF_CA, sites=sites)
    for k, a in res.assign.items():
        if a["status"] == "accepted":
            assert a["site"] == {"a": "A1", "b": "A2"}.get(k.split("_")[1][0], a["site"])
    assert {m["site"] for m in res.modes.values()} >= {"A1", "A2"}
    # no overlap at all: the site with the nearest residue centroid wins
    far = {"A1": {"residues": [95, 96]}, "A2": {"residues": [1, 2]}}
    res = build_modes({"x": [3, 4], "y": [3, 5], "z": [4, 5]}, None, {"r": RunInfo("r", [], {"x": "accepted", "y": "accepted", "z": "accepted"})}, ref_ca=REF_CA, sites=far)
    assert {a["site"] for a in res.assign.values()} == {"A2"}


# ---------------------------------------------------------------------------------------------


def test_near_miss_rule_is_the_contract_rule():
    rules = config.DEFAULT_FILTERS
    avg = {"pLDDT": 0.9, "pTM": 0.9, "i_pTM": 0.8, "i_pAE": 0.2, "ShapeComplementarity": 0.57, "n_InterfaceHbonds": 8,
           "n_InterfaceUnsatHbonds": 2, "Surface_Hydrophobicity": 0.3, "Binder_pLDDT": 0.9, "Binder_RMSD": 1.0}
    d = model.DesignRecord(id="x", run_id="r", backbone_id="x", status="rejected", binder_name="x", length=60, seed=1, mpnn_index=1,
                           sequence="A" * 60, metrics=dict(avg), models={1: dict(avg), 2: dict(avg)}, interface_aas={"avg": {"K": 1, "M": 1}})
    run = model.RunData(run_id="r", label="r", reader="t", source_dir=".", settings=model.RunSettings("r", "r"),
                        target=model.TargetConstruct("r"), designs=[d])
    d.structure_path = "x.pdb"
    rec = modes.rescue_records(run)["x"]
    assert rec["nm"]["near"] is True and rec["nm"]["n_failed"] == 1 and rec["nm"]["stems"] == ["ShapeComplementarity"]
    assert rec["nm"]["worst_rel"] == pytest.approx(0.05) and rec["fail"][0]["scope"] == "Average"
    d.metrics["ShapeComplementarity"] = 0.40                              # 33 percent miss: no longer near
    assert modes.rescue_records(run)["x"]["nm"]["near"] is False
    d.metrics["ShapeComplementarity"] = 0.57
    d.metrics["i_pAE"] = 0.36                                             # second stem
    assert modes.rescue_records(run)["x"]["nm"]["near"] is False
    # a custom rule set is honoured
    only = tuple(r for r in rules if r.stem == "i_pAE")
    assert modes.rescue_records(run, only)["x"]["nm"]["stems"] == ["i_pAE"]


# ---------------------------------------------------------------------------------------------
# The toy project
# ---------------------------------------------------------------------------------------------


def test_every_toy_design_is_assigned_and_modes_are_ordered(toy_project):
    block = toy_project.modes
    res = block["modes"]
    assert len(block["assign"]) == 30 and sum(1 for a in block["assign"].values() if a["status"] == "accepted") == 22
    assert list(res) == [f"M{i + 1}" for i in range(len(res))] and 3 <= len(res) <= modes.MAX_MODES
    sizes = [m["n"] for m in res.values()]
    assert sizes == sorted(sizes, reverse=True) and sum(sizes) == 22 and block["k_selection"]["k"] == len(res)
    assert sum(m["n_rejected"] for m in res.values()) == 8
    xy = np.array([a["emb"] for a in block["assign"].values()])
    assert xy.min() >= 0 and xy.max() <= 1


def test_toy_modes_are_named_from_the_data(toy_project):
    names = {i: toy_project.frame.residue(i) for i in range(1, 251)}
    for m in toy_project.modes["modes"].values():
        assert m["auto_name"] is True and 0 < len(m["name"]) <= modes.NAME_MAX_CHARS and m["note"].endswith(".")
        assert m["enr"] and m["top"] and set(m["top"]) <= set(range(60, 170))             # footprints use reference numbers, inside the construct
        assert any(f"{names[r]}{r}" in m["name"] for r in m["enr"])


def test_the_designed_neighbours_share_a_mode(toy_project, toy_truth):
    assign = toy_project.modes["assign"]
    for rid, a, b in (("run01", "a1", "a2"), ("run02", "c1", "c2")):
        bb = toy_truth["runs"][rid]["backbones"]
        modes_of = {assign[d]["mode"] for d in toy_truth["accepted"][rid] if d.startswith((bb[a]["id"], bb[b]["id"]))}
        assert len(modes_of) == 1, (rid, modes_of)


def test_hotspot_coverage_follows_the_poses(toy_project, toy_truth):
    for rid in ("run01", "run02"):
        hot = toy_truth["hotspots_reference"][rid]
        for did, kind in toy_truth["design_kind"][rid].items():
            rec = toy_project.modes["hotspots_touched"].get(did)
            if rec is None:
                continue
            assert rec["run"] == rid and rec["hot_of"] == len(hot)
            if kind == "clean_hot":
                assert len(rec["hot"]) >= 2 and rec["wrong_site"] is False
            if kind == "clean_away":
                assert rec["hot"] == [] and rec["wrong_site"] is True
    block = toy_project.modes["modes"]
    assert sum(m["n_wrong_site"] for m in block.values()) >= 4
    for m in block.values():
        assert all(0 <= s <= 1 for s in m["hot_share"].values())


def test_modes_block_matches_the_campaign_schema_on_the_toy_project(toy_blobs):
    jsonschema = pytest.importorskip("jsonschema")
    from toydata.testing import load_validator

    vs = load_validator()
    campaign = json.loads((REPO / "docs" / "schema" / "campaign.schema.json").read_text())
    v = jsonschema.Draft202012Validator({"$ref": campaign["$id"] + "#/$defs/modes"}, registry=vs.registry())
    assert not [e.message for e in v.iter_errors(json.loads(json.dumps(toy_blobs.campaign["modes"])))]


def test_rescue_records_follow_the_plan(toy_project):
    rec = toy_project.rescue
    run1 = toy_project.runs["run01"]
    assert set(k[1] for k in rec) == {d.id for d in run1.rejected_with_structure()} and len(rec) == 8
    plan = {}
    for spec in RUN1:
        for mp, stems in spec.rejected.items():
            plan[(spec.length, mp)] = stems
    for (rid, did), r in rec.items():
        d = run1.design(did)
        want = plan[(d.length, d.mpnn_index)]
        stems = {f["stem"] for f in r["fail"]}
        assert set(want) <= stems and r["nm"]["n_failed"] == len(stems)
        assert all(f["gap"] > 0 and f["rel"] >= 0 for f in r["fail"])
        assert r["nm"]["near"] == (len(stems) == 1 and r["nm"]["worst_rel"] <= model.NEAR_MISS_MAX_REL)
    assert sum(r["nm"]["near"] for r in rec.values()) >= 3
    order = modes.rescue_order({k[1]: v for k, v in rec.items()})
    near = [rec[("run01", i)]["nm"]["near"] for i in order]
    assert near == sorted(near, reverse=True)                                   # near misses come first


def test_accepted_designs_fail_nothing_and_run2_rejected_are_metric_only(toy_project):
    for rid in ("run01", "run02"):
        assert modes.accepted_failures(toy_project.runs[rid]) == {}
    run2 = toy_project.runs["run02"]
    assert modes.rescue_records(run2) == {}
    metric_only = modes.rescue_records(run2, include_metric_only=True)
    assert len(metric_only) == len(run2.rejected()) == 8 and all(v["fail"] for v in metric_only.values())
