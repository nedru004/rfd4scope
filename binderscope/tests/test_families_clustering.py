"""Families, clustering and diversity statistics (binder_review.families): the statistics (worked example), average linkage
against scipy, the fold matrix, family records, diversity blocks, replay and flags, on synthetic input and on the toy runs.
Nothing here uses the network."""
from __future__ import annotations

import base64
import dataclasses
import json
import math

import numpy as np
import pytest
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from binder_review import families as F
from binder_review.model import BackboneRecord, DesignRecord
from toydata.testing import OFFSET, load_validator, repo_root, with_offset


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("BINDERSCOPE_OFFLINE", "1")


# ------------------------------------------------------------------------------------------------ statistics
class TestStatistics:
    SIZES = [1] * 15 + [2] * 5 + [3] * 5            # n 40, S 25, f1 15, f2 5

    def test_worked_example(self):
        cj = F.chao_jost(self.SIZES)
        assert (cj.n, cj.S, cj.f1, cj.f2) == (40, 25, 15, 5)
        assert cj.C == pytest.approx(0.6313, abs=5e-5) and cj.f0 == pytest.approx(21.94, abs=5e-3)
        assert cj.S + cj.snew(40) == pytest.approx(35.8, abs=0.05) and cj.chao1 == pytest.approx(25 + 21.94, abs=5e-3)

    def test_coverage_formula_by_hand(self):
        n, f1, f2 = 40, 15, 5
        assert F.coverage(self.SIZES) == pytest.approx(1 - (f1 / n) * ((n - 1) * f1) / ((n - 1) * f1 + 2 * f2))

    def test_coverage_without_doubletons_uses_the_corrected_form(self):
        sizes = [1, 1, 1, 4]
        n, f1 = 7, 3
        assert F.coverage(sizes) == pytest.approx(1 - (f1 / n) * ((n - 1) * (f1 - 1)) / ((n - 1) * (f1 - 1) + 2))
        assert F.chao_jost(sizes).f0 == pytest.approx((n - 1) / n * f1 * (f1 - 1) / 2)

    def test_no_singletons_means_full_coverage(self):
        assert F.coverage([3]) == 1.0 and F.chao_jost([3]).f0 == 0.0 and F.coverage([2, 2, 4]) == 1.0

    def test_degenerate_inputs(self):
        assert F.coverage([]) == 0.0
        cj = F.chao_jost([])
        assert (cj.n, cj.S, cj.C, cj.f0) == (0, 0, 0.0, 0.0)
        one = F.chao_jost([1])
        assert one.C == 1.0 and one.f0 == 0.0 and one.snew(5) == 0.0

    def test_snew_is_monotone_and_bounded_by_f0(self):
        cj = F.chao_jost(self.SIZES)
        vals = [cj.snew(m) for m in range(0, 41)]
        assert vals[0] == 0.0 and all(b >= a for a, b in zip(vals, vals[1:])) and vals[-1] < cj.f0

    def test_levels_follow_the_gates(self):
        assert F.level_for(9, 0.1) == "early" and F.level_for(10, 0.69) == "keep" and F.level_for(10, 0.7) == "maybe"
        assert F.level_for(30, 0.89) == "maybe" and F.level_for(30, 0.9) == "enough"

    def test_wilson_interval(self):
        lo, hi = F.wilson(5, 50)
        p, z = 0.1, 1.2816
        centre = (p + z * z / 100) / (1 + z * z / 50)
        assert (lo + hi) / 2 == pytest.approx(centre) and lo < p < hi and F.wilson(0, 0) == (0, 0)
        from scipy.stats import norm

        lo2, hi2 = F.wilson(5, 50, norm.ppf(0.9))
        assert lo2 == pytest.approx(lo, abs=1e-3) and hi2 == pytest.approx(hi, abs=1e-3)

    def test_hill_numbers(self):
        assert F.hill([1, 1, 1, 1], 0) == 4 and F.hill([1, 1, 1, 1], 1) == pytest.approx(4) and F.hill([1, 1, 1, 1], 2) == pytest.approx(4)
        assert F.hill([6, 2], 2) == pytest.approx(1 / (0.75 ** 2 + 0.25 ** 2))

    def test_advice_matches_hand_computation(self):
        order = [(5, "a"), (8, "a"), (20, "b"), (30, "a"), (36, "c")]       # five families in three clusters found at trajectories 5 ... 36 of 40
        adv = F.advice(order, [3, 1, 1], 40)
        assert (adv.n_families, adv.S, adv.f1, adv.f2) == (5, 3, 2, 0) and adv.yield_p == pytest.approx(0.125)
        assert adv.level == "early" and adv.low_conf and adv.dominance == pytest.approx(0.6)
        assert [p["clusters"] for p in adv.pts] == [0, 1, 1, 2, 2, 3] and adv.recent_new == 1 and adv.early_new == 1
        assert adv.f0 == pytest.approx(0.8) and adv.at(40) == adv.S and adv.at(80) > adv.S
        assert len(adv.snew_list) == 5 and adv.snew_list[-1] < adv.f0 and adv.traj_to_families(30) == pytest.approx((30 - 5) / 0.125)


# ------------------------------------------------------------------------------------------------ clustering
class TestAverageLinkage:
    def test_matches_scipy_on_random_distances(self):
        rng = np.random.default_rng(3)
        for n in (2, 5, 12, 40):
            X = rng.random((n, 3))
            D = np.linalg.norm(X[:, None] - X[None], axis=2)
            D = D / D.max()
            Z = linkage(squareform(D, checks=False), method="average")
            for theta in (0.15, 0.3, 0.5, 0.7):
                mine = {frozenset(m) for m in F.average_linkage(D, theta)}
                ref = fcluster(Z, theta, criterion="distance")
                assert mine == {frozenset(np.flatnonzero(ref == c)) for c in set(ref)}, (n, theta)

    def test_cut_is_inclusive_and_clusters_are_sorted_by_size(self):
        D = np.array([[0, 0.5, 0.9, 0.9], [0.5, 0, 0.9, 0.9], [0.9, 0.9, 0, 0.1], [0.9, 0.9, 0.1, 0]])
        assert [sorted(m) for m in F.average_linkage(D, 0.5)] == [[0, 1], [2, 3]]
        assert [len(m) for m in F.average_linkage(D, 0.4)] == [2, 1, 1]
        assert F.average_linkage(D, 0.0) == [[0], [1], [2], [3]] and [sorted(m) for m in F.average_linkage(D, 1.0)] == [[0, 1, 2, 3]]
        assert F.average_linkage(np.zeros((0, 0)), 0.5) == []

    def test_ties_break_like_the_mockup(self):
        D = np.full((3, 3), 0.2)
        np.fill_diagonal(D, 0)
        assert F.average_linkage(D, 0.1) == [[0], [1], [2]] and F.average_linkage(D, 0.2) == [[0, 1, 2]]

    def test_invariants_on_a_random_matrix(self):
        rng = np.random.default_rng(11)
        X = rng.random((25, 4))
        D = np.linalg.norm(X[:, None] - X[None], axis=2)
        D /= D.max()
        last = None
        for theta in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
            cl = F.average_linkage(D, theta)
            assert sorted(i for m in cl for i in m) == list(range(25))                  # a partition
            assert [len(m) for m in cl] == sorted((len(m) for m in cl), reverse=True)
            if last is not None:
                assert len(cl) <= last                                                    # a larger cut never makes more clusters
            last = len(cl)
        assert len(F.average_linkage(D, 1.0)) == 1 and len(F.average_linkage(D, 0.0)) == 25


# ------------------------------------------------------------------------------------------------ fold matrix
def _helix(n=20, shift=0.0):
    t = np.arange(n)
    return np.stack([2.3 * np.cos(100 * np.pi / 180 * t), 2.3 * np.sin(100 * np.pi / 180 * t), 1.5 * t + shift], axis=1)


def _strand(n=20):
    t = np.arange(n)
    return np.stack([3.3 * t, 1.0 * (t % 2), 0.2 * t], axis=1)


class TestFoldMatrix:
    def _fold(self):
        chains = {"h1": _helix(22), "h2": _helix(22) + [5, 5, 5], "s1": _strand(22)}
        ds, geo = [], {}
        for name, xyz in chains.items():
            d = DesignRecord(id=name, run_id="r", backbone_id=name, status="accepted", binder_name="x", length=len(xyz), seed=1,
                             mpnn_index=1, sequence="A" * len(xyz))
            ds.append(d)
            geo[d.key] = F.DesignGeometry("r", name, xyz, "A" * len(xyz), (1, 2))
        return ds, geo, F.fold_matrix(ds, geo, n_jobs=1)

    def test_shape_symmetry_and_diagonal(self):
        ds, geo, fold = self._fold()
        assert fold["n"] == 3 and fold["ids"] == ["h1", "h2", "s1"]
        M = np.frombuffer(base64.b64decode(fold["tm"]), dtype=np.uint8).reshape(3, 3)
        assert np.array_equal(M, M.T) and list(np.diag(M)) == [100, 100, 100] and M[0, 1] == 100 and M[0, 2] < 60
        assert fold["runs"] == ["r"] * 3 and fold["names"] == ["h1", "h2", "s1"]

    def test_labels_and_nearest_label(self):
        ds, geo, fold = self._fold()
        lab = F.fold_labels(fold, 0.5)
        assert lab["h1"] == lab["h2"] == "F1" and lab["s1"] == "F2"
        reps = F.build_fold_reps(ds, fold, geo)
        probe = (_helix(18) @ np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1.0]]).T, "A" * 18)
        label, tm, near = F.fold_label_details(probe, reps)
        assert label == "F1" and tm > 80 and near in ("h1", "h2")
        coil = np.cumsum(np.random.default_rng(1).normal(size=(30, 3)) * 2.0, axis=0)
        assert F.fold_label_for((coil, "A" * 30), reps) in ("unclustered", "F1", "F2")
        assert F.fold_label_for((coil, "A" * 30), dataclasses.replace(reps, cut=0.05)) == "unclustered"

    def test_duplicate_ids_are_rejected(self):
        ds, geo, _ = self._fold()
        with pytest.raises(ValueError):
            F.fold_matrix(ds, geo, id_of=lambda d: "same", n_jobs=1)

    def test_decode_lookup(self):
        ds, geo, fold = self._fold()
        fl = F.decode_fold(fold)
        assert fl.distance(("r", "h1"), ("r", "h2")) == 0.0
        with pytest.raises(KeyError):
            fl.row("r", "missing")


# ------------------------------------------------------------------------------------------------ toy families
class TestToyFamilies:
    def test_run1_families(self, toy_run1):
        fams = F.build_families(with_offset(toy_run1))
        assert len(fams) == 8 and sum(f.n_accepted for f in fams) == len(toy_run1.accepted()) == 12
        assert [f.traj for f in fams] == sorted(f.traj for f in fams) and all(f.traj > 0 for f in fams)
        for f in fams:
            assert f.best == f.accepted[0] and 1 <= f.n_accepted <= 2 and f.at_sequence_cap == (f.n_accepted >= 2)
            assert f.footprint and all(60 <= r <= 169 for r in f.footprint) and f.footprint_numbering == "uniprot"
            assert set(f.members) == set(f.accepted) | set(f.rejected)

    def test_without_offset_construct_numbers_and_a_warning(self, toy_run1):
        warn: list[str] = []
        fams = F.build_families(toy_run1, warnings=warn)
        assert warn and "construct numbers" in warn[0] and all(f.footprint_numbering == "construct" for f in fams)
        assert all(1 <= r <= 110 for f in fams for r in f.footprint)
        offs = F.build_families(with_offset(toy_run1))
        assert [sorted(r + OFFSET for r in a.footprint) for a in fams] == [f.footprint for f in offs]

    def test_include_rejected_only(self, toy_run1):
        acc = F.build_families(with_offset(toy_run1))
        allf = F.build_families(with_offset(toy_run1), include_rejected_only=True)
        assert len(allf) == len(acc) + 2                                                # the two backbones whose every sequence was rejected
        extra = [f for f in allf if not f.accepted]
        assert extra and all(f.best is None and f.footprint == [] for f in extra) and all(f.n_rejected_with_structure > 0 for f in extra)

    def test_run2_footprints_avoid_the_deleted_loops(self, toy_run2, toy_truth):
        removed = {n + OFFSET for a, b in toy_truth["deleted_construct"] for n in range(a, b + 1)}
        fams = F.build_families(with_offset(toy_run2))
        assert len(fams) == 8 and not {r for f in fams for r in f.footprint} & removed
        big = max(fams, key=lambda f: len(f.members))
        assert sum(f.n_rejected for f in fams) > 0 and all(f.n_rejected_with_structure == 0 for f in fams) and big.n_rejected >= 0

    def test_quality_scores_agree_with_best(self, toy_run1):
        q = F.quality_scores(toy_run1)
        for f in F.build_families(with_offset(toy_run1)):
            assert max(f.accepted, key=lambda i: q[i]) == f.best

    def test_epoch_hash_changes_with_each_component(self, toy_run1, toy_run2):
        a, b = with_offset(toy_run1), with_offset(toy_run2)
        assert F.epoch_hash(a) == F.epoch_hash(with_offset(toy_run1)) and F.epoch_hash(a) != F.epoch_hash(b)
        assert set(F.epoch_differences([a, b])) >= {"hotspots", "construct", "length"}
        hs = dataclasses.replace(a, settings=dataclasses.replace(a.settings, hotspots=a.settings.hotspots[:2]))
        assert F.epoch_hash(hs) != F.epoch_hash(a)
        ln = dataclasses.replace(a, settings=dataclasses.replace(a.settings, length_max=(a.settings.length_max or 100) + 5))
        assert F.epoch_hash(ln) != F.epoch_hash(a) and F.epoch_differences([a, with_offset(toy_run1)]) == []

    def test_sequence_cap_is_two(self, toy_run1):
        assert F._sequence_cap(toy_run1)[0] == 2

    def test_chronology_statement(self, toy_run1):
        c = F.chronology(toy_run1)
        assert c["verified"] is False and c["n_rows"] == len(toy_run1.backbones) and "duration" in c["time_is"]


@pytest.fixture(scope="module")
def analysis(toy_run1, toy_run2):
    return F.analyze_families([with_offset(toy_run1), with_offset(toy_run2)], n_jobs=1, rejected_labels=True)


class TestToyAnalysis:
    def test_blocks_are_consistent(self, analysis):
        div = analysis.diversity
        assert set(div["runs"]) == {"run01", "run02"} and div["pooled"]["estimates_valid"] is False
        assert "advice" not in div["pooled"] and "replay" not in div["pooled"]
        assert div["distance_default"] == {"fold": 0.5, "site": 0.5, "both": 0.5} and div["basis_default"] == "both"
        for rid, blk in div["runs"].items():
            n = blk["n_families"]
            assert n == 8 and blk["estimates_valid"] is True and blk["n_designs"] == (12 if rid == "run01" else 10)
            for basis in F.BASES:
                cl = blk["clusters"][basis]
                assert sum(c["size"] for c in cl) == n == blk["counts"][basis]["n"] and blk["counts"][basis]["S"] == len(cl)
                assert [c["size"] for c in cl] == sorted((c["size"] for c in cl), reverse=True)
                adv = blk["advice"][basis]
                cj = F.chao_jost([c["size"] for c in cl])
                assert adv["coverage"] == pytest.approx(cj.C, abs=1e-6) and adv["f0"] == pytest.approx(cj.f0, abs=1e-6)
                assert len(adv["snew"]) == n and adv["traj_basis"] == "passed" and adv["level"] == F.level_for(n, cj.C)
                assert adv["n_traj"] == (13 if rid == "run01" else 12)
            assert len(blk["replay"]) == n and blk["replay"][-1]["found_later"] == 0
            assert blk["sweep"]["fold"][0]["S"] >= blk["counts"]["fold"]["S"]

    def test_pooled_block_is_descriptive_and_epochs_differ(self, analysis):
        d = analysis.diversity
        e1, e2 = d["runs"]["run01"]["epoch"]["hash"], d["runs"]["run02"]["epoch"]["hash"]
        pooled = d["pooled"]
        assert e1 != e2 and pooled["n_families"] == 16 and pooled["n_designs"] == 22
        assert set(pooled["epoch"]["differences"]) == {"hotspots", "construct", "length"} and any(f["code"] == "epoch" for f in pooled["flags"])
        comps = d["runs"]["run02"]["epoch"]["components"]
        assert len(comps["hotspots"]) == 4 and len(d["runs"]["run01"]["epoch"]["components"]["hotspots"]) == 3
        removed = [79, 82, 139, 142]                              # reference numbers of the two loops run02 lacks
        assert all(not (a <= r <= b) for a, b in comps["construct"] for r in removed)

    def test_the_designed_neighbour_pair_shares_a_cluster(self, analysis, toy_truth):
        for rid, a, b in (("run01", "a1", "a2"), ("run02", "c1", "c2")):
            bb = toy_truth["runs"][rid]["backbones"]
            for basis in ("fold", "both"):
                together = [c for c in analysis.diversity["runs"][rid]["clusters"][basis] if bb[a]["id"] in c["fams"] and bb[b]["id"] in c["fams"]]
                assert together, (rid, basis)

    def test_flags_present(self, analysis):
        for rid, blk in analysis.diversity["runs"].items():
            codes = {f["code"] for f in blk["flags"]}
            assert {"unknown_attempts", "small_sample", "sibling_cap", "unlogged_af2_failures", "restart_counters",
                    "extrapolation_limit", "predicted_only"} <= codes
            unk = next(f for f in blk["flags"] if f["code"] == "unknown_attempts")
            assert unk["data"]["n_traj"] == (13 if rid == "run01" else 12) and "attempted_estimate" in unk["data"]

    def test_fold_matrix(self, analysis):
        M = F.decode_fold(analysis.fold).M
        assert M.shape == (22, 22) and np.array_equal(M, M.T) and set(np.diag(M)) == {100} and M.min() >= 0 and M.max() <= 100
        assert len(set(analysis.fold["ids"])) == 22 and set(analysis.fold_labels) == set(analysis.fold["ids"])
        assert len(analysis.rejected_fold) == 8 and all(v["run"] == "run01" for v in analysis.rejected_fold.values())
        labels = set(analysis.fold_labels.values()) | {"unclustered"}
        assert {v["label"] for v in analysis.rejected_fold.values()} <= labels

    def test_blobs_validate_against_the_campaign_schema(self, analysis):
        jsonschema = pytest.importorskip("jsonschema")
        vs = load_validator()
        campaign = json.loads((repo_root() / "docs" / "schema" / "campaign.schema.json").read_text())
        for part, inst in (("diversity", analysis.diversity), ("fold", analysis.fold)):
            v = jsonschema.Draft202012Validator({"$ref": campaign["$id"] + f"#/$defs/{part}"}, registry=vs.registry())
            assert [e.message for e in v.iter_errors(json.loads(json.dumps(inst)))] == []

    def test_results_are_json_free_of_sequences_and_deterministic(self, analysis, toy_run1, toy_run2, tmp_path):
        paths = F.write_artefacts(analysis, tmp_path)
        assert {p.name for p in paths} == {"run01.json", "run02.json", "fold.json"}
        text = "".join(p.read_text() for p in paths)
        for run in (toy_run1, toy_run2):
            assert all(d.sequence not in text for d in run.designs)
        again = F.analyze_families([with_offset(toy_run1), with_offset(toy_run2)], n_jobs=1, rejected_labels=True)
        assert json.dumps(analysis.to_dict(), sort_keys=True) == json.dumps(again.to_dict(), sort_keys=True)


# ------------------------------------------------------------------------------------------------ replay and flags
class TestReplayAndFlags:
    def _synthetic(self):
        site = [{1, 2}, {1, 2}, {3, 4}, {1, 2}, {5, 6}, {1, 2}, {3, 4}, {7, 8}, {1, 2}, {3, 4}, {1, 2}, {5, 6}]
        return [F.FamilyRecord(id=f"bb{i}", run_id="r", traj=3 * (i + 1), length=60, seed=i, members=[f"d{i}"], accepted=[f"d{i}"], rejected=[],
                               n_rejected_with_structure=0, best=f"d{i}", ranks=[i + 1], footprint=sorted(fp), footprint_numbering="uniprot",
                               summary={}, best_metrics={}, trajectory_metrics={}, cum_time_h=None, at_sequence_cap=(i % 2 == 0))
                for i, fp in enumerate(site)]

    def test_replay_checkpoints(self):
        fams = self._synthetic()
        cl = [{"id": k + 1, "size": len(m), "fams": m} for k, m in enumerate(
            [[f.id for f in fams if set(f.footprint) == s] for s in ({1, 2}, {3, 4}, {5, 6}, {7, 8})])]
        rp = F._replay(fams, cl, 40)
        assert [r["upto"] for r in rp] == [3 * (i + 1) for i in range(12)] and [r["n_families"] for r in rp] == list(range(1, 13))
        assert [r["S"] for r in rp][:5] == [1, 1, 2, 2, 3]
        assert all(r["coverage"] is None and r["level"] == "early" for r in rp[:9]) and all(r["coverage"] is not None for r in rp[9:])
        assert rp[-1]["found_later"] == 0 and rp[0]["found_later"] == 3 and all(a["found_later"] >= b["found_later"] for a, b in zip(rp, rp[1:]))
        cj = F.chao_jost([6, 3, 2, 1])
        assert rp[-1]["coverage"] == pytest.approx(cj.C, abs=1e-6) and rp[-1]["chao1"] == pytest.approx(cj.chao1, abs=1e-6)

    def test_flags_on_a_toy_run(self, toy_run1):
        run = with_offset(toy_run1)
        fams = F.build_families(run)
        stub = {"sweep": {"fold": [{"distance": 0.2, "S": len(fams), "f1": len(fams), "f2": 0}]}}
        codes = {f["code"] for f in F.pitfall_flags(run, fams, stub)}
        assert {"unknown_attempts", "small_sample", "restart_counters", "extrapolation_limit", "predicted_only"} <= codes
        four = [dataclasses.replace(f, id=f"{f.id}_{i}") for i in range(2) for f in fams[:2]]
        stub = {"sweep": {"fold": [{"distance": 0.2, "S": 4, "f1": 4, "f2": 0}]}}
        assert "threshold_artefact" in {f["code"] for f in F.pitfall_flags(run, four, stub)}
