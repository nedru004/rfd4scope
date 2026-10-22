"""Export (binder_review.export): the blobs of the toy project validate against docs/schema, carry the contract shape, copy the BindCraft
scores unchanged, number everything in the reference numbering, never carry a path or a binder sequence where it must not, and are
deterministic. The exported structures are the designs placed on the reference chain."""
from __future__ import annotations

import copy
import json

import gemmi
import numpy as np
import pytest

from binder_review import export as E
from toydata.testing import OFFSET, load_validator

CORE = ("plddt", "ptm", "iptm", "iptm_b", "ipae_norm", "ipae_mean", "min_ipae", "ipsae", "complex_rmsd", "binder_rmsd", "hotspot_rmsd",
        "target_rmsd", "dsasa", "sc", "dg", "n_hbonds", "n_salt", "n_unsat", "hyd_frac", "hphob_patch", "surf_hyd", "loop_frac", "helix_frac",
        "strand_frac", "iface_helix", "pI", "charge", "gravy", "n_cys", "n_met", "n_trp", "epi_prec", "epi_rec", "mem_excess", "nterm_d",
        "cterm_d", "seed_spread", "n_agree")


# ---------------------------------------------------------------------------------------------------- pure functions
def test_clean_makes_plain_json_types():
    out = E.clean({"a": np.float64(1.5), "b": np.int64(3), "c": (1, 2), "d": np.array([1.0, np.nan])})
    assert out == {"a": 1.5, "b": 3, "c": [1, 2], "d": [1.0, None]} and type(out["a"]) is float and type(out["b"]) is int


def test_dumps_refuses_nan_and_is_compact():
    assert E.dumps({"a": [1, 2]}) == '{"a":[1,2]}'
    with pytest.raises(ValueError):
        E.dumps({"a": float("nan")})


def test_structure_filenames_and_cap_flags():
    assert E.structure_filename("binder_l9_s1_mpnn1").endswith(".pdb") and "/" not in E.structure_filename("run01/binder_l9_s1_mpnn1")
    assert len(E.CAP_FLAGS) == 38 and len(set(E.CAP_FLAGS)) == 38


# ---------------------------------------------------------------------------------------------------- blobs of the toy project
def test_blobs_validate_and_carry_the_contract_shape(toy_blobs):
    b = toy_blobs
    assert b.validation is not None and not b.validation.errors and b.warnings == []
    assert b.campaign["schema"] == "binderscope.campaign/1" and b.inspect["schema"] == "binderscope.inspect/1"
    assert b.homolog["schema"] == "binderscope.homolog/1" and b.manifest["schema"] == "binderscope.manifest/1"
    assert b.campaign["profile"] == b.inspect["profile"] == b.profile
    ds = b.campaign["designs"]
    assert len(ds) == 30 and sum(d["status"] == "accepted" for d in ds) == 22 and len({d["id"] for d in ds}) == 30
    for d in ds:
        assert set(CORE) <= set(d["m"]), d["id"]
        assert d["m"]["ipsae"] is None and d["m"]["min_ipae"] is None and d["m"]["complex_rmsd"] is None      # BindCraft 1 does not write them
        assert d["hot"] == sorted(set(d["hot"])) and set(d["hot"]) <= set(d["tc"]) and all(60 <= t <= 169 for t in d["tc"])
        assert len(d["ca"]) == 3 * d["len"] and len(d["pl"]) == d["len"] and all(0 <= i < d["len"] for i in d["bc"])
    acc = [d for d in ds if d["status"] == "accepted"]
    rej = [d for d in ds if d["status"] == "rejected"]
    assert all(d["fail"] == [] and "nm" not in d for d in acc) and all(d["fail"] and "nm" in d for d in rej)
    assert b.campaign["fold"]["n"] == 22 and len(b.inspect["designs"]) == b.manifest["counts"]["inspected"]
    assert b.manifest["counts"] == {"designs": 30, "accepted": 22, "rejected": 8, "families": 16, "runs": 2, "inspected": len(b.inspect["designs"])}


def test_every_blob_validates_against_its_schema(toy_blobs):
    vs = load_validator()
    reports = {"campaign": vs.check_campaign(toy_blobs.campaign), "inspect": vs.check_inspect(toy_blobs.inspect, toy_blobs.campaign),
               "homolog": vs.check_homolog(toy_blobs.homolog, toy_blobs.campaign, toy_blobs.profile), "manifest": vs.check_manifest(toy_blobs.manifest),
               "profile": vs.check_profile(toy_blobs.profile)}
    for kind, rep in reports.items():
        assert rep.ok and not rep.errors, (kind, rep.errors[:3])


def test_run_notes_have_no_duplicates(toy_blobs):
    runs = toy_blobs.campaign["runs"]
    assert any("No settings file" in n for r in runs for n in r["notes"])
    for r in runs:
        assert len(r["notes"]) == len(set(r["notes"])), r["id"]
    for r in toy_blobs.manifest["runs"]:
        assert len(r["notes"]) == len(set(r["notes"])), r["id"]


def test_bindcraft_scores_are_copied_unchanged(toy_project, toy_blobs):
    for d in toy_blobs.campaign["designs"]:
        rec = toy_project.runs[d["run"]].design(d["name"])
        M, m = rec.metrics, d["m"]
        assert (m["plddt"], m["ptm"], m["iptm"], m["iptm_b"], m["ipae_norm"], m["sc"], m["dg"], m["surf_hyd"], m["binder_rmsd"],
                m["hotspot_rmsd"], m["target_rmsd"], m["ciplddt"]) == (
            M["pLDDT"], M["pTM"], M["i_pTM"], M["i_pTM"], M["i_pAE"], M["ShapeComplementarity"], M["dG"], M["Surface_Hydrophobicity"],
            M["Binder_RMSD"], M["Hotspot_RMSD"], M["Target_RMSD"], M["i_pLDDT"])
        assert m["ipae_mean"] == round(M["i_pAE"] * 31, 1)                                   # PAE divided by 31 comes back to angstrom
        assert d["src"]["average"] == {k: v for k, v in M.items() if v is not None} and set(d["src"]["models"]) == {str(k) for k in rec.models}
        assert d["seq"] == rec.sequence and d["len"] == rec.length and d["seed"] == rec.seed


def test_target_numbering_and_run_blocks(toy_blobs, toy_truth):
    c = toy_blobs.campaign
    t = c["target"]
    assert t["resi"] == list(range(60, 170)) and t["numbering"]["input_offset"] == -OFFSET and t["orig_resi"][:2] == [1, 2]
    assert t["length"] == 250 and t["hotspots_by_run"] == {"run01": toy_truth["hotspots_reference"]["run01"], "run02": toy_truth["hotspots_reference"]["run02"]}
    runs = {r["id"]: r for r in c["runs"]}
    assert runs["run01"]["target"]["resi_ranges"] == [[60, 169]] and runs["run01"]["target"]["removed"] == []
    assert runs["run02"]["target"]["resi_ranges"] == [[60, 78], [83, 138], [143, 169]]
    assert runs["run02"]["target"]["removed"] == toy_truth["deleted_reference"] and runs["run02"]["target"]["cut_points"] == [60, 78, 83, 138, 143, 169]
    assert runs["run01"]["settings"]["hotspots"] == toy_truth["hotspots_reference"]["run01"] and runs["run01"]["settings"]["hotspots_input"] == toy_truth["hotspots_construct"]["run01"]
    assert runs["run02"]["settings"]["sources"]["hotspots"] == "csv" and runs["run01"]["settings"]["sources"]["hotspots"] == "settings_json"
    assert [d["hot_of"] for d in c["designs"] if d["run"] == "run01"] == [3] * 20 and all(d["hot_of"] == 4 for d in c["designs"] if d["run"] == "run02")


def test_funnel_keeps_unknowns_unknown(toy_blobs):
    for r in toy_blobs.campaign["runs"]:
        f = {x["key"]: x for x in r["funnel"]}
        assert f["trajectories_attempted"]["n"] is None and f["trajectories_attempted"]["status"] == "unknown"
        est = f["trajectories_attempted_estimate"]
        assert est["status"] == "derived" and isinstance(est["n"], int) and est["n"] >= 12
    r1 = next(r for r in toy_blobs.campaign["runs"] if r["id"] == "run01")
    assert r1["n_traj"] is None and r1["n_traj_pass"] == 13 and r1["n_accepted"] == 12 and r1["settings"]["final_designs_target"] == 12


def test_manifest_never_carries_a_binder_sequence_and_records_privacy(toy_blobs):
    text = json.dumps(toy_blobs.manifest) + json.dumps(toy_blobs.profile) + json.dumps(toy_blobs.homolog)
    for d in toy_blobs.campaign["designs"]:
        assert d["seq"] not in text, d["id"]
    for t in toy_blobs.inspect["designs"].values():
        assert t["seq"] not in text
    assert toy_blobs.manifest["privacy"] == {"network_requests": 0, "binder_sequences_sent": False, "design_ids_sent": False}


def test_caps_are_written_from_the_data(toy_blobs):
    caps = toy_blobs.profile["caps"]
    assert list(caps) == list(E.CAP_FLAGS) and all(isinstance(v, bool) for v in caps.values()) and toy_blobs.manifest["caps"] == caps
    assert all(not caps[k] for k in ("outcomes", "weights", "has_pae", "has_boltz", "ipsae", "min_ipae", "complex_rmsd", "seed_spread", "n_agree",
                                     "esm_plddt", "consurf", "models", "has_seeds", "ddg_scan", "flag_region", "footprints", "decoy_cofold",
                                     "traj_total", "species_panel", "paralog_panel", "glycan", "membrane"))
    assert all(caps[k] for k in ("olig", "rest_ctx", "has_traj", "has_fold", "families", "multi_run", "rejected", "reviews", "dg", "sc", "iptm_b",
                                 "ipae_norm", "surf_hyd", "hotspot_rmsd", "binder_rmsd", "ciplddt"))
    assert "real" not in caps


def test_user_profile_can_switch_a_capability_off_but_not_on(toy_project, toy_blobs):
    c = toy_blobs.campaign
    parts = {"designs": c["designs"], "target": c["target"], "homolog": toy_blobs.homolog, "fold": c["fold"], "runs": c["runs"], "rest": c["target"]["rest"]}
    p = copy.copy(toy_project)
    p.profile = copy.deepcopy(toy_project.profile)
    p.profile["caps"]["rest_ctx"] = False                                              # a user switches a layer off
    p.profile["caps"]["ipsae"] = True                                                  # and claims a metric the data lack
    p.profile["caps"]["outcomes"] = True
    caps = E.build_profile(p, parts, [])["caps"]
    assert caps["rest_ctx"] is False and caps["ipsae"] is False and caps["outcomes"] is False and caps["dg"] is True


def test_validation_errors_stop_the_build(toy_blobs):
    bad = copy.deepcopy(toy_blobs)
    bad.campaign["designs"][0]["tc"].append(9999)                                       # a contact outside the target
    with pytest.raises(E.ExportError, match="tc"):
        E.validate_blobs(bad)
    assert E.validate_blobs(bad, strict=False).errors                                   # not strict: reported, not raised
    bad2 = copy.deepcopy(toy_blobs)
    bad2.campaign["profile"]["caps"]["ipsae"] = True                                    # a flag that promises a metric nobody has
    bad2.inspect["profile"]["caps"]["ipsae"] = True
    with pytest.raises(E.ExportError):
        E.validate_blobs(bad2)


def test_blobs_are_deterministic(toy_project, toy_blobs):
    again = E.build_blobs(toy_project, created=toy_blobs.manifest["created"])
    for k in ("campaign", "inspect", "homolog", "manifest", "profile"):
        assert E.dumps(getattr(again, k)) == E.dumps(getattr(toy_blobs, k)), k
    assert again.structures == toy_blobs.structures


def test_write_export_layout(toy_blobs, tmp_path):
    paths = E.write_export(toy_blobs, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["campaign.json", "homolog.json", "inspect.json", "manifest.json", "profile.json", "structures"]
    n = len(toy_blobs.inspect["designs"])
    assert len(list((tmp_path / "structures").glob("*.pdb"))) == n == len(toy_blobs.structures) and len(paths) == 5 + n
    assert json.loads((tmp_path / "campaign.json").read_text()) == toy_blobs.campaign
    (tmp_path / "structures" / "stale.pdb").write_text("END\n")
    E.write_export(toy_blobs, tmp_path)                                                 # rewriting leaves no stale structure behind
    assert len(list((tmp_path / "structures").glob("*.pdb"))) == n


def test_blobs_carry_no_absolute_path(toy_blobs, toy_dir):
    for name in ("campaign", "inspect", "homolog", "manifest", "profile"):
        text = json.dumps(getattr(toy_blobs, name))
        assert str(toy_dir) not in text and "/private/" not in text and "/var/folders" not in text and "/Users/" not in text, name
    assert all("/" not in s["path"] for s in toy_blobs.manifest["sources"])             # file names only
    assert E.portable_paths({"p": "/some/dir/final_design_stats.csv", "q": ["/a/b/c.pdb"]}) == {"p": "final_design_stats.csv", "q": ["c.pdb"]}


def test_exported_structures_are_placed_complexes_with_reference_numbering(toy_blobs, toy_export_dir, toy_truth, toy_reference):
    ref = gemmi.read_structure(str(toy_reference))[0]["A"]
    ref_ca = {r.seqid.num: np.array(r["CA"][0].pos.tolist()) for r in ref}
    files = sorted((toy_export_dir / "structures").glob("*.pdb"))
    assert len(files) == len(toy_blobs.inspect["designs"])
    for f in files[:6]:
        st = gemmi.read_structure(str(f))[0]
        assert [c.name for c in st] == ["A", "B"]
        nums = [r.seqid.num for r in st["A"]]
        assert min(nums) >= 60 and max(nums) <= 169                                     # numbered as the reference chain
        dev = max(np.linalg.norm(np.array(r["CA"][0].pos.tolist()) - ref_ca[r.seqid.num]) for r in st["A"])
        assert dev < 0.6, (f.name, dev)                                                 # the target part sits on the reference chain
        assert [r.seqid.num for r in st["B"]] == list(range(1, len(st["B"]) + 1))


def test_exported_context_follows_the_goals_of_the_poses(toy_blobs, toy_truth):
    by_id = {d["id"]: d for d in toy_blobs.campaign["designs"] if d["status"] == "accepted"}
    for rid in ("run01", "run02"):
        for did, kind in toy_truth["design_kind"][rid].items():
            d = by_id.get(did)
            if d is None or kind is None:
                continue
            m = d["m"]
            if kind in ("clean_hot", "clean_away"):
                assert m["rest_n3"] == 0 and m["olig_n3"] == 0
            if kind in ("collide_out", "collide_gap"):
                assert m["rest_n3"] >= 10, (did, kind)
            if kind == "clean_hot":
                assert len(d["hot"]) >= 2
    assert all(d["m"]["place_rmsd"] < 0.5 for d in toy_blobs.campaign["designs"])


def test_highlights_are_counted_from_the_data(toy_blobs):
    h = {x["id"]: x for x in toy_blobs.campaign["highlights"]}
    assert "3 of 22 accepted designs fail the rest-of-protein check (1 of 12 in run01 and 2 of 10 in run02)" in h["rest_overlap"]["text"]
    assert h["partner_overlap"]["text"].startswith("1 of 22 accepted designs put binder atoms within 3 Å of the other subunit")
    assert {"hotspots", "run_differences", "families", "near_misses"} <= set(h)
    assert "run02 lacks 79-82 and 139-142" in h["run_differences"]["text"] and "4 of the 8 rejected designs" in h["near_misses"]["text"]


def test_manifest_counts_match_the_csvs_and_name_every_input(toy_blobs, toy_project):
    v = toy_blobs.manifest["validation"]
    assert v["counts_match_csv"] is True and v["mismatches"] == []
    roles = {s["role"] for s in toy_blobs.manifest["sources"]}
    assert {"csv", "settings", "accepted_structure", "rejected_structure", "xlsx"} <= roles
    agree = v["metric_agreement"]
    assert agree["n_InterfaceResidues"]["max_abs_diff"] == 0.0 and agree["Interface_Hydrophobicity"]["max_abs_diff"] < 0.01
    assert agree["dsasa"]["r"] > 0.9 and 0.0 <= abs(agree["n_hbonds"]["mean_diff"]) < 10


def test_single_run_projects_export_and_switch_features_from_the_data(toy_dir, tmp_path):
    from binder_review import pipeline as P

    spec = P.ProjectSpec(name="one", runs=[P.RunSpec(toy_dir / "runs" / "run02", "run02")], target_pdb=toy_dir / "construct" / "toy_construct.pdb",
                         reference_cif=toy_dir / "reference" / "toy_target.cif", jobs=1)
    project = P.run_pipeline(spec, tmp_path / "w", log=P.Log(quiet=True))
    blobs = E.build_blobs(project)
    caps = blobs.profile["caps"]
    assert caps["multi_run"] is False and caps["rejected"] is False and len(blobs.campaign["runs"]) == 1     # run02 has no rejected structures
    assert len(blobs.campaign["designs"]) == 10 and all(d["status"] == "accepted" for d in blobs.campaign["designs"])   # metric-only rows are not designs
    assert blobs.campaign["target"]["resi"] == [n for n in range(60, 170) if not 79 <= n <= 82 and not 139 <= n <= 142]
