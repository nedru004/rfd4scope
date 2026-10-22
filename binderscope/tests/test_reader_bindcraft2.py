"""BindCraft 2 reader on the toy campaigns, which are written in the shape of real BindCraft 2 v1.0.3 output (tests/toydata/bc2.py).

toya  stamped 1.0.1 (binder numbering zero-based), six families, passed designs that are not ranked, an unknown column
toyb  no metadata, no version, pLDDT written 0 to 100, one-based, the gapped target, no trajectory table
toyc  stamped 1.0.3: the verified version and modality, the only one the reader does not flag as untested
What was checked on a real campaign is in docs/bindcraft2_reader.md; the numbers here come from what the
generator wrote (toy_truth), not from the reader.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import gemmi
import pandas as pd
import pytest

from binder_review.model import METRIC_STEMS
from binder_review.readers import bindcraft1, bindcraft2, detect_reader, get_reader

REF, RANK, TRAJ = "2_Refolded/!_Refolded.csv", "3_Ranked/!_Ranked.csv", "1_Trajectories/!_Trajectories.csv"


def first_binder_number(path: str, chain: str = "B") -> int:
    return gemmi.read_structure(path)[0][chain][0].seqid.num


def csv(p: Path) -> pd.DataFrame:
    return pd.read_csv(p, dtype=str, keep_default_na=False)


def copy_campaign(toy_dir: Path, rel: str, tmp_path: Path) -> Path:
    dst = tmp_path / Path(rel).name
    shutil.copytree(toy_dir / rel, dst)
    return dst


def edit_csv(root: Path, rel: str, fn) -> None:
    df = csv(root / rel)
    fn(df)
    df.to_csv(root / rel, index=False)


@pytest.fixture(scope="module")
def truth(toy_truth):
    return toy_truth["bindcraft2"]


@pytest.fixture(scope="module")
def toya(toy_dir):
    return toy_dir / "bindcraft2" / "toya"


@pytest.fixture(scope="module")
def toyc(toy_dir):
    return toy_dir / "bindcraft2_v103" / "toyc"


@pytest.fixture(scope="module")
def toy_bc2_c(toyc):
    return bindcraft2.read_run(toyc, "toyc")                      # no construct_path: the metadata records where the file is


# ---------------------------------------------------------------------------------------------- the fixture itself
@pytest.mark.parametrize("rel", ["bindcraft2/toya", "bindcraft2_v103/toyc"])
def test_the_toy_campaigns_have_the_shape_of_real_output(toy_dir, rel):
    """If this fails the fixture has drifted from what BindCraft 2 writes and the tests below say nothing about real output."""
    root = toy_dir / rel
    ref, rank, traj = csv(root / REF), csv(root / RANK), csv(root / TRAJ)
    assert "trajectory" not in ref.columns and "trajectory" not in rank.columns          # the link to a trajectory is the hash
    assert {"hash", "outcome", "failed_filters"} <= set(ref.columns) and "hash" in rank.columns and "hash" in traj.columns
    assert set(ref["outcome"]) == {"passed", "rejected"}
    assert all(re.fullmatch(r".+_denovo_l\d+_[0-9a-f]{16}_candidate\d+", d) for d in ref["design"])
    assert all(re.fullmatch(r".+_denovo_l\d+_[0-9a-f]{16}_seq0", d) for d in rank["design"])
    assert not set(rank["design"]) & set(ref["design"]) and set(rank["hash"]) <= set(ref["hash"]) <= set(traj["hash"])
    assert all(re.fullmatch(r"[A-Z]\d+", t) for cell in ref["Interface_Binder_Residues"] for t in cell.split(",") if t)   # amino acid + number
    assert (ref["failed_filters"] != "").any() and ref["failed_filters"].str.contains(",").any() and (ref["failed_filters"][ref["outcome"] == "passed"] == "").all()
    assert (traj["terminated"] != "").any() and (traj["terminated"] == "").any()
    meta = json.loads((root / "campaign_metadata.json").read_text())
    assert meta["version"].startswith("BindCraft 2 v") and meta["settings"]["filters"]["Surface_Hydrophobicity"]["threshold"] == "Infinity"
    assert (root / "2_Refolded" / "BinderMonomer").is_dir() and (root / "2_Refolded" / "Complexes").is_dir()
    some = next(p for p in (root / "1_Trajectories").iterdir() if p.is_dir())
    assert [p.name for p in some.iterdir()] == [f"{some.name}_losses.csv"]


# ---------------------------------------------------------------------------------------------- detection
@pytest.mark.parametrize("rel", ["bindcraft2/toya", "bindcraft2/toyb", "bindcraft2_v103/toyc"])
def test_detects_toy_campaigns_and_registry_picks_bindcraft2(toy_dir, rel):
    p = toy_dir / rel
    assert bindcraft2.detect(p) and not bindcraft1.detect(p)
    assert detect_reader(p) is bindcraft2 and get_reader("bindcraft2") is bindcraft2


@pytest.mark.parametrize("name", ["run01", "run02"])
def test_bindcraft1_folders_are_not_bindcraft2(toy_dir, name):
    p = toy_dir / "runs" / name
    assert not bindcraft2.detect(p) and detect_reader(p) is bindcraft1


def test_detection_edge_cases(tmp_path):
    assert not bindcraft2.detect(tmp_path) and not bindcraft2.detect(tmp_path / "missing")
    (tmp_path / "2_Refolded").mkdir()
    assert not bindcraft2.detect(tmp_path)                     # one stage folder, no table, no metadata
    (tmp_path / "2_Refolded" / "!_Refolded.csv").write_text("design\n")
    assert bindcraft2.detect(tmp_path)                         # one folder plus its table
    other = tmp_path / "b"
    (other / "1_Trajectories").mkdir(parents=True)
    (other / "3_Ranked").mkdir()
    assert bindcraft2.detect(other)                            # two stage folders
    for n in ("final_design_stats.csv", "mpnn_design_stats.csv", "trajectory_stats.csv"):
        (other / n).write_text("Design\n")
    assert not bindcraft2.detect(other)                        # a BindCraft 1 signature wins


def test_read_run_rejects_non_campaign(tmp_path):
    with pytest.raises(ValueError):
        bindcraft2.read_run(tmp_path, "x")


# ---------------------------------------------------------------------------------------------- helpers
def test_parse_name():
    info = bindcraft2.parse_name("camp_one_denovo_l80_0123456789abcdef")
    assert info["campaign"] == "camp_one" and info["modality"] == "de novo" and info["length"] == 80 and info["hash"] == "0123456789abcdef"
    assert info["kind"] is None and info["n"] is None
    cand = bindcraft2.parse_name("camp_two_denovo_l85_fedcba9876543210_candidate10")
    assert cand["hash"] == "fedcba9876543210" and cand["kind"] == "candidate" and cand["n"] == 10 and cand["campaign"] == "camp_two"
    ranked = bindcraft2.parse_name("camp_two_denovo_l116_89abcdef01234567_seq0")
    assert ranked["kind"] == "seq" and ranked["n"] == 0 and ranked["length"] == 116
    assert bindcraft2.parse_name("c_vhh_l120_0123456789ABCDEF")["modality"] == "VHH"
    assert bindcraft2.parse_name("c_weird_l9_0123456789abcdef")["modality"] is None
    assert bindcraft2.parse_name("binder_l77_s305024_mpnn4") is None          # a BindCraft 1 name
    assert bindcraft2.parse_name("c_denovo_l80_short") is None


@pytest.mark.parametrize("cell,want", [
    ("i_pTM,pLDDT,Unbound_Binder_pLDDT", ["i_pTM", "pLDDT", "Unbound_Binder_pLDDT"]), ("i_pTM", ["i_pTM"]),
    ("i_pTM;i_pAE", ["i_pTM", "i_pAE"]), ("i_pTM, i_pAE | pLDDT", ["i_pTM", "i_pAE", "pLDDT"]),
    ("['i_pTM', 'pLDDT']", ["i_pTM", "pLDDT"]), ('["i_pTM"]', ["i_pTM"]), ("[i_pTM; pLDDT]", ["i_pTM", "pLDDT"]),
    ("", []), ("none", []), ("nan", []), ("[]", []), (None, []), ("i_pTM < 0.7", ["i_pTM < 0.7"]),
    ("Binder_RMSD (not measured),i_pTM", ["Binder_RMSD (not measured)", "i_pTM"]),
])
def test_split_failed_filters(cell, want):
    assert bindcraft2.split_failed_filters(cell) == want


def test_interface_residues_are_amino_acid_letters_and_numbers():
    parts, bad = bindcraft2.parse_residue_list("P1,A25,H26")
    assert parts == [[("P", 1), ("A", 25), ("H", 26)]] and bad == []
    assert bindcraft2.parse_residue_list("") == ([], []) and bindcraft2.parse_residue_list("[R61, junk!, W127]")[1] == ["junk!"]
    assert bindcraft2.parse_residue_list("A1,C2/D1,E9")[0] == [[("A", 1), ("C", 2)], [("D", 1), ("E", 9)]]       # chains of a multi-chain binder
    assert bindcraft2.parse_residue_list("25,26")[0] == [[("", 25), ("", 26)]]                                  # no letter: nothing to check
    seq = "PGSDKNQEVLTRIYWMCEKNSDVFAHQTL"
    # one-based file: residue 25 of this sequence is A
    assert bindcraft2.interface_residues_from("P1,A25,H26", seq, ["B"]) == (["B1", "B25", "B26"], 3, 0, 0)
    # a zero-based file numbers the same residues one lower; the reader returns one-based numbers
    assert bindcraft2.interface_residues_from("P0,A24,H25", seq, ["B"], shift=1) == (["B1", "B25", "B26"], 3, 0, 0)
    # chain letters in place of amino acids ('B25') are noticed: B is no amino acid of this sequence
    assert bindcraft2.interface_residues_from("B25,B26", seq, ["B"]) == (["B25", "B26"], 2, 2, 0)
    assert bindcraft2.interface_residues_from("A99", seq, ["B"])[2] == 1                                       # past the end of the sequence
    assert bindcraft2.interface_residues_from("A1,C2/D1", "AC/DE", ["B", "C"]) == (["B1", "B2", "C1"], 3, 0, 0)


def test_match_structure_never_matches_a_longer_candidate_number(tmp_path):
    idx = {"x_candidate10": tmp_path / "10.cif", "3_x_candidate2": tmp_path / "2.cif", "x_candidate2_monomer": tmp_path / "m.cif"}
    assert bindcraft2._match_structure(idx, "x_candidate1") is None             # not candidate10
    assert bindcraft2._match_structure(idx, "x_candidate10") == tmp_path / "10.cif"
    assert bindcraft2._match_structure(idx, "x_candidate2") == tmp_path / "2.cif"       # behind a rank prefix, not the monomer


def test_parse_filters_reads_the_real_shape():
    raw = {"i_pTM": {"higher": True, "threshold": 0.7}, "i_pAE": {"higher": False, "threshold": 0.35},
           "Surface_Hydrophobicity": {"higher": False, "mandatory": False, "threshold": "Infinity"},
           "Hotspot_Contact_Fraction": {"higher": True, "mandatory": False, "threshold": 0.0},
           "Interface_Residues": {"higher": True, "threshold": 7}, "Odd": {"higher": True, "threshold": "-Infinity", "mandatory": False}}
    filters, unlimited = bindcraft2._parse_filters(raw)
    by = {f["name"]: f for f in filters}
    assert unlimited == ["Surface_Hydrophobicity", "Odd"] and set(by) == {"i_pTM", "i_pAE", "Hotspot_Contact_Fraction", "Interface_Residues"}
    assert (by["i_pTM"]["op"], by["i_pTM"]["threshold"], by["i_pTM"]["higher"], by["i_pTM"]["mandatory"]) == (">=", 0.7, True, True)
    assert (by["i_pAE"]["op"], by["i_pAE"]["threshold"]) == ("<=", 0.35)
    assert by["Hotspot_Contact_Fraction"]["mandatory"] is False and by["Hotspot_Contact_Fraction"]["threshold"] == 0.0     # 0.0 is a threshold, not "none"
    assert by["Interface_Residues"]["mandatory"] is True                                                                  # a missing key means mandatory
    # other shapes are still understood
    legacy, _ = bindcraft2._parse_filters({"i_pTM": {"min": 0.7}, "i_pAE": 0.3, "x": {"threshold": 2, "op": "<="}, "y": {"max": 1}, "z": 5})
    assert {f["name"]: (f["op"], f["threshold"]) for f in legacy} == {"i_pTM": (">=", 0.7), "i_pAE": ("<=", 0.3), "x": ("<=", 2.0), "y": ("<=", 1.0)}
    assert bindcraft2._parse_filters("nothing")[0] == [] and bindcraft2._parse_filters([1, 2])[0] == []


def test_clean_version_does_not_repeat_the_tool_name():
    assert bindcraft2._clean_version("BindCraft 2 v1.0.3") == "1.0.3" and bindcraft2._clean_version("1.0.1") == "1.0.1"
    assert bindcraft2._clean_version("") is None and bindcraft2._clean_version("nightly") == "nightly"


# ---------------------------------------------------------------------------------------------- toya
def test_toya_is_an_untested_version_and_says_why(toy_bc2_a):
    run = toy_bc2_a
    assert run.experimental is True and run.reader == "bindcraft2"
    assert run.warnings[0].startswith("UNTESTED") and "BindCraft 2 v1.0.1" in run.warnings[0] and "v1.0.3" in run.warnings[0]
    assert run.tool_version == "2 v1.0.1 (revision a8d0f20)"                      # the stamp's own "BindCraft 2 v" is not added twice


def test_families_are_keyed_by_hash_not_by_design_id(toy_bc2_a, truth):
    run, t = toy_bc2_a, truth["toya"]
    assert len(run.designs) == sum(len(v) for v in t["families"].values()) and len(t["families"]) < len(run.designs)
    assert any(len(v) > 1 for v in t["families"].values())
    assert len(run.backbones) == t["n_trajectories"]                               # one per trajectory, finished or not
    by_bb = {b.id: b for b in run.backbones}
    for traj_id, ids in t["families"].items():
        assert by_bb[traj_id].design_ids == ids
        assert all(run.design(i).backbone_id == traj_id for i in ids)
    assert sum(1 for b in run.backbones if b.design_ids) == len(t["families"])
    assert sum(1 for b in run.backbones if not b.design_ids) == t["n_terminated"]
    assert {len(b.design_ids) for b in run.backbones if b.design_ids} == {len(v) for v in t["families"].values()}


def test_trajectory_order_follows_the_trajectory_number(toy_bc2_a, truth, toya):
    run = toy_bc2_a
    assert [b.order for b in run.backbones] == list(range(len(run.backbones))) and [b.traj for b in run.backbones] == list(range(1, len(run.backbones) + 1))
    assert [b.extras["trajectory"] for b in run.backbones] == truth["toya"]["trajectory_numbers"]
    assert csv(toya / TRAJ)["trajectory"].astype(int).tolist() != truth["toya"]["trajectory_numbers"]          # the file is in finish order


def test_accepted_is_outcome_passed_and_ranked_is_a_rank(toy_bc2_a, truth):
    run, t = toy_bc2_a, truth["toya"]
    assert sorted(d.id for d in run.accepted()) == sorted(t["passed"]) and len(t["passed"]) > len(t["ranked"]) > 0
    assert [d.id for d in run.accepted()][: len(t["ranked"])] == [i for i, _ in sorted(t["ranked"].items(), key=lambda kv: kv[1])]   # ranked first, by rank
    assert [d.rank for d in run.accepted()] == sorted(t["ranked"].values()) + [None] * (len(t["passed"]) - len(t["ranked"]))
    assert not [d for d in run.designs if "_seq" in d.id] and len(run.designs) == len(csv(Path(run.source_dir) / REF))     # no second design
    for did, rid in t["ranked_id"].items():
        d = run.design(did)
        assert d.extras["ranked"] is True and d.extras["ranked_id"] == rid and Path(d.ranked_path).name == f"{rid}.cif"
        assert Path(d.structure_path).name == f"{did}.cif" and "Complexes" in d.structure_path       # the refolded complex, not the _seq0 file
        assert d.extras["cif_bindcraft"]["_bindcraft.rank"] == str(t["ranked"][did])
    others = [d for d in run.designs if d.id not in t["ranked"]]
    assert all(d.rank is None and d.ranked_path is None and d.extras["ranked"] is False for d in others)
    assert len(run.rejected()) == len(t["rejected"]) and all(r.status == "rejected" for r in map(run.design, t["rejected"]))
    assert len(run.accepted_backbones()) == len({run.design(i).backbone_id for i in t["passed"]})


def test_ranked_row_matches_on_hash_and_sequence_not_on_id(toya, tmp_path):
    root = copy_campaign(toya.parent, "toya", tmp_path)
    ranked = csv(root / RANK)
    for i in range(len(ranked)):                                           # another suffix: only (hash, Binder_Sequence) still link the rows
        ranked.loc[i, "design"] = ranked.loc[i, "design"].replace("_seq0", "_seq7")
    ranked.to_csv(root / RANK, index=False)
    for p in (root / "3_Ranked").glob("*_seq0.cif"):
        p.rename(p.with_name(p.name.replace("_seq0", "_seq7")))
    run = bindcraft2.read_run(root, "x", construct_path=None)
    assert len(run.designs) == len(csv(root / REF)) and sum(1 for d in run.designs if d.rank is not None) == len(ranked)
    assert not [w for w in run.warnings if "match no refolded row" in w]
    # a ranked row whose sequence is not any refolded sequence is its own design (and says so)
    ranked.loc[0, "Binder_Sequence"] = "M" * len(ranked.loc[0, "Binder_Sequence"])
    ranked.to_csv(root / RANK, index=False)
    run = bindcraft2.read_run(root, "x")
    assert len(run.designs) == len(csv(root / REF)) + 1 and any("match no refolded row" in w for w in run.warnings)
    lone = next(d for d in run.designs if d.id.endswith("_seq7") and d.rank == 1)
    assert lone.status == "accepted" and lone.structure_path and lone.structure_path.endswith("_seq7.cif")


def test_failed_filters_are_counted_from_the_file_and_keep_their_own_names(toy_bc2_a, truth):
    run, t = toy_bc2_a, truth["toya"]
    want: dict[str, int] = {}
    for names in t["rejected"].values():
        for n in names:
            want[n] = want.get(n, 0) + 1
    assert run.failure_counters == want and "Binder_pLDDT" not in run.failure_counters             # Unbound_Binder_pLDDT is not renamed
    from collections import Counter
    assert dict(Counter(s for d in run.rejected() for s in d.failed_stems)) == want                   # what `inspect` prints
    for did, names in t["rejected"].items():
        d = run.design(did)
        assert d.extras["failed_filter_names"] == names and d.failed_stems == names
        by = {f.stem: f for f in d.failed_filters}
        assert all(f.gap >= 0 for f in d.failed_filters)
        if "Unbound_Binder_pLDDT" in by:
            f = by["Unbound_Binder_pLDDT"]               # the CSV mean (0.8 and more) passes 0.7: a validation model failed it
            assert (f.scope, f.gap, f.threshold, f.higher_is_better) == ("Model", 0.0, 0.7, True) and f.value > 0.7
            assert d.extras["failed_filter_not_in_mean"] == ["Unbound_Binder_pLDDT"] and "Binder_pLDDT" not in by
        if "i_pTM" in by:
            assert by["i_pTM"].scope == "Average" and by["i_pTM"].gap > 0 and by["i_pTM"].threshold == 0.7
        if "pTM" in by:
            assert (by["pTM"].scope, by["pTM"].gap) == ("Model", 0.0) and d.extras["failed_filter_not_in_mean"] == ["pTM"]
        if "i_pAE" in by:
            assert by["i_pAE"].gap > 0 and not by["i_pAE"].higher_is_better and by["i_pAE"].threshold == 0.3
    assert all(not d.failed_filters and not d.extras["failed_filter_names"] for d in run.accepted())
    assert run.annotations.get("designs_with_failure_not_in_mean", 0) == sum(1 for n in t["rejected"].values() if "pTM" in n or "Unbound_Binder_pLDDT" in n)


def test_the_campaigns_own_filters_are_used(toy_bc2_a):
    by = {f["name"]: f for f in toy_bc2_a.annotations["filters"]}
    assert {"i_pTM", "i_pAE", "pTM", "Unbound_Binder_pLDDT", "Binder_RMSD", "Interface_Residues", "pLDDT"} <= set(by)
    assert by["pLDDT"]["source"] == "settings.min_plddt_final" and by["pLDDT"]["threshold"] == 0.7        # pLDDT is a setting, not a filter entry
    assert by["i_pAE"]["op"] == "<=" and by["i_pTM"]["op"] == ">=" and by["Binder_RMSD"]["mandatory"] is True
    assert by["Hotspot_Contact_Fraction"]["mandatory"] is False
    assert "Surface_Hydrophobicity" not in by and toy_bc2_a.annotations["filters_unlimited"] == ["Surface_Hydrophobicity"]    # "Infinity": no limit
    assert toy_bc2_a.settings.filters_name == "campaign_metadata"


def test_metrics_are_mapped_only_where_the_quantity_is_the_same(toy_bc2_a, toya):
    run = toy_bc2_a
    ref = csv(toya / REF).set_index("design")
    d = run.accepted()[0]
    row = ref.loc[d.id]
    f = lambda c: float(row[c])  # noqa: E731
    m = d.metrics
    assert (m["i_pTM"], m["pLDDT"], m["pTM"], m["i_pAE"]) == (f("i_pTM"), f("pLDDT"), f("pTM"), f("i_pAE"))
    assert m["Unbound_Binder_pLDDT"] == m["Binder_pLDDT"] == f("Unbound_Binder_pLDDT") and m["Target_pLDDT"] == f("Target_pLDDT")
    assert m["n_InterfaceResidues"] == f("Interface_Residues") == len(d.interface_residues)
    assert m["ss_pLDDT"] == f("SS_pLDDT") and m["Surface_Hydrophobicity"] == f("Surface_Hydrophobicity")
    assert m["Binder_RMSD"] == f("Binder_RMSD") and m["Target_RMSD"] == f("Target_RMSD") and "i_pDAE" in m
    # fractions become the percent stems of BindCraft 1: x100 (Interface_Hydrophobicity is a percent stem as well)
    for src, stem in (("Binder_Helix_Fraction", "Binder_Helix%"), ("Binder_BetaSheet_Fraction", "Binder_BetaSheet%"), ("Binder_Loop_Fraction", "Binder_Loop%"),
                      ("Interface_Helix_Fraction", "Interface_Helix%"), ("Interface_BetaSheet_Fraction", "Interface_BetaSheet%"),
                      ("Interface_Loop_Fraction", "Interface_Loop%"), ("Interface_Hydrophobicity", "Interface_Hydrophobicity")):
        assert m[stem] == pytest.approx(100 * f(src), abs=1e-6), stem
    assert m["Binder_Helix%"] + m["Binder_BetaSheet%"] + m["Binder_Loop%"] == pytest.approx(100, abs=0.5)
    # columns that are not the same quantity as a BindCraft 1 stem stay in extras under their own names
    for col in ("Interface_BuriedArea", "Interface_BuriedArea_Fraction", "Backbone_Clashes", "All_Atom_Clashes", "Hotspot_Contact_Fraction",
                "Off_Epitope_Contact_Fraction", "Epitope_Residues_Contacted", "Binder_Mass_kDa"):
        assert d.extras[col] == f(col) and col not in m
    for stem in ("dSASA", "Interface_SASA_%", "Unrelaxed_Clashes"):
        assert stem not in m and stem in run.annotations["capabilities"]["unmapped_bindcraft1_stems"]
    caps = run.caps()
    assert set(caps) == set(METRIC_STEMS)
    present = {"pLDDT", "pTM", "i_pTM", "i_pAE", "Binder_pLDDT", "ss_pLDDT", "n_InterfaceResidues", "Surface_Hydrophobicity", "Interface_Hydrophobicity",
               "Binder_Helix%", "Binder_BetaSheet%", "Binder_Loop%", "Interface_Helix%", "Interface_BetaSheet%", "Interface_Loop%", "Target_RMSD", "Binder_RMSD"}
    unmapped = set(bindcraft2.UNMAPPED_STEMS)
    absent = {"dG", "ShapeComplementarity", "PackStat", "n_InterfaceHbonds", "InterfaceHbondsPercentage", "n_InterfaceUnsatHbonds",
              "InterfaceUnsatHbondsPercentage", "Binder_Energy_Score", "Hotspot_RMSD", "i_pLDDT", "dG/dSASA", "Relaxed_Clashes", "MPNN_score",
              "MPNN_seq_recovery", "pAE", "Binder_pTM", "Binder_pAE"}
    assert {s for s, v in caps.items() if v} == present and present | unmapped | absent == set(METRIC_STEMS) and len(present) + len(unmapped) + len(absent) == len(METRIC_STEMS)
    assert set(run.annotations["capabilities"]["absent_bindcraft1_stems"]) == absent
    assert {"i_pDAE", "Target_pLDDT", "Unbound_Binder_pLDDT"} <= set(run.annotations["capabilities"]["present"])


def test_interface_residues_are_read_as_amino_acids_and_checked(toy_bc2_a, toya):
    run = toy_bc2_a
    ref = csv(toya / REF).set_index("design")
    assert run.annotations["binder_numbering"]["offset_to_one_based"] == 1                     # toya counts from 0
    for d in run.designs:
        row = ref.loc[d.id]
        toks = [t for t in row["Interface_Binder_Residues"].split(",") if t]
        assert [int(t[1:]) + 1 for t in toks] == [int(x[1:]) for x in d.interface_residues] and all(x[0] == "B" for x in d.interface_residues)
        assert all(row["Binder_Sequence"][int(t[1:])] == t[0] for t in toks)                    # the letters are the residues of the sequence
        assert not toks or d.sequence[int(d.interface_residues[0][1:]) - 1] == toks[0][0]
        assert d.extras["Interface_Target_Residues"] == row["Interface_Target_Residues"] and "Interface_Binder_Residues" not in d.extras
    assert not [w for w in run.warnings if "letters" in w]


def test_interface_residues_match_the_structure(toy_bc2_a):
    from binder_review import structure as S

    d = toy_bc2_a.accepted()[0]
    cx = S.load_complex(d.structure_path, "A", "B")
    in_file = sorted(S.residue_contacts(cx.target, cx.binder, 4.0).b_residues)
    assert sorted(int(x[1:]) for x in d.interface_residues) == [n + 1 for n in in_file]


def test_residue_letters_that_disagree_with_the_sequence_are_reported_and_not_read(toy_dir, tmp_path):
    root = copy_campaign(toy_dir, "bindcraft2_v103/toyc", tmp_path)

    def swap(df):
        cell = df.loc[0, "Interface_Binder_Residues"]
        first = cell.split(",")[0]
        wrong = "W" if first[0] != "W" else "Y"
        df.loc[0, "Interface_Binder_Residues"] = wrong + cell[1:]
    edit_csv(root, REF, swap)
    run = bindcraft2.read_run(root, "x")
    first = run.designs[0]
    assert first.interface_residues == [] and first.extras["Interface_Binder_Residues"][0] in "WY"
    assert any("do not match Binder_Sequence" in w and "format" in w for w in run.warnings)
    assert all(d.interface_residues for d in run.designs[1:] if d.extras["n_binder_chains"] and csv(root / REF).set_index("design").loc[d.id, "Interface_Binder_Residues"])
    # chain letters in place of amino acids, as BindCraft 1's style would be: also noticed
    root2 = copy_campaign(toy_dir, "bindcraft2_v103/toyc", tmp_path / "again")
    edit_csv(root2, REF, lambda df: df.__setitem__("Interface_Binder_Residues", df["Interface_Binder_Residues"].map(lambda c: ",".join("B" + t[1:] for t in c.split(",") if t))))
    run2 = bindcraft2.read_run(root2, "x")
    assert any("do not match Binder_Sequence" in w for w in run2.warnings) and not any(d.interface_residues for d in run2.designs)


def test_unbound_binder_prediction_is_recorded_on_the_design(toy_bc2_a):
    for d in toy_bc2_a.designs:
        p = Path(d.extras["unbound_binder_path"])
        assert p.name == f"{d.id}_monomer.cif" and p.parent.name == "BinderMonomer" and p.is_file()
        st = gemmi.read_structure(str(p))
        assert [c.name for c in st[0]] == ["A"] and len(st[0]["A"]) == d.length                 # the binder alone, chain A
        assert d.extras["candidate"] >= 1 and d.extras["hash"] == d.backbone_id.rsplit("_", 1)[1]


def test_absent_trajectory_structures_are_normal(toy_bc2_a):
    run = toy_bc2_a
    assert any("hold no structures" in n and "archive_trajectories is false" in n for n in run.notes)
    assert run.warnings[1:] == []                                                       # nothing but the untested notice
    assert all(d.structure_path for d in run.designs)                                   # the design structures are the refolded complexes


def test_funnel_counts_the_tiers_and_the_trajectories(toy_bc2_a, truth):
    f, t = toy_bc2_a.funnel, truth["toya"]
    assert (f.trajectories_attempted, f.trajectory_rows, f.trajectories_terminated) == (t["n_trajectories"], t["n_completed"], t["n_terminated"])
    assert f.trajectories_attempted == f.trajectory_rows + f.trajectories_terminated
    assert (f.mpnn_rows, f.accepted_designs, f.mpnn_rows_rejected) == (len(toy_bc2_a.designs), len(t["passed"]), len(t["rejected"]))
    assert f.backbones_with_mpnn_rows == len(t["families"]) and f.accepted_backbones == len({toy_bc2_a.design(i).backbone_id for i in t["passed"]})
    assert f.status["trajectories_attempted"] == "observed" and f.final_designs_target == len(t["ranked"])
    assert toy_bc2_a.annotations["tiers"] == {"trajectories": t["n_trajectories"], "refolded": len(toy_bc2_a.designs), "passed": len(t["passed"]), "ranked": len(t["ranked"])}
    assert any("same" in n and "trajectory hashes" in n for n in toy_bc2_a.notes)         # .campaign_state.json agrees with the table


def test_funnel_without_the_terminated_column_estimates_the_attempts(toya, tmp_path):
    root = copy_campaign(toya.parent, "toya", tmp_path)
    edit_csv(root, TRAJ, lambda df: df.drop(columns=["terminated"], inplace=True))
    f = bindcraft2.read_run(root, "x").funnel
    assert f.trajectories_attempted is None and f.status["trajectories_attempted"] == "unknown"
    assert f.trajectory_rows == f.trajectories_attempted_estimate and f.status["trajectories_attempted_estimate"] == "derived"


def test_zero_based_binder_numbering_is_normalised(toy_bc2_a, toya):
    run = toy_bc2_a
    d = run.accepted()[0]
    assert first_binder_number(d.structure_path) == 0
    bn = run.annotations["binder_numbering"]
    assert (bn["source_zero_based"], bn["basis"], bn["offset_to_one_based"]) == (True, "structure", 1)
    ref = csv(toya / REF)
    raw = [int(t[1:]) for cell in ref["Interface_Binder_Residues"] for t in cell.split(",") if t]
    assert min(raw) + 1 == min(int(x[1:]) for dd in run.designs for x in dd.interface_residues)                        # the table counts from zero, the reader from one
    assert all(int(x[1:]) >= 1 for dd in run.designs for x in dd.interface_residues)
    assert any("zero-based" in n for n in run.notes)
    for dd in run.designs:
        assert dd.interface_residues and int(dd.interface_residues[-1][1:]) <= dd.length


def test_one_based_copy_renumbers_binder_only(toy_bc2_a, tmp_path):
    d = toy_bc2_a.accepted()[0]
    out = bindcraft2.one_based_copy(toy_bc2_a, d, tmp_path / "copy.cif")
    assert first_binder_number(str(out)) == 1 and first_binder_number(str(out), "A") == first_binder_number(d.structure_path, "A")
    assert first_binder_number(d.structure_path) == 0                      # the original is untouched


def test_structures_target_and_provenance(toy_bc2_a, toy_truth):
    run = toy_bc2_a
    t = run.target
    assert t.n_present == 110 and (t.first, t.last) == (1, 110) and t.deleted_ranges == [] and t.uniprot_offset is None
    assert t.consistent and t.sequence_matches_reference and t.n_structures_checked == len(run.designs)
    assert run.settings.hotspots == toy_truth["hotspots_construct"]["run01"] and run.settings.hotspot_source == "campaign_metadata"
    assert (run.settings.length_min, run.settings.length_max) == (60, 110) and run.settings.final_designs_target == len(toy_truth["bindcraft2"]["toya"]["ranked"])
    assert run.target.hotspot_residues[run.settings.hotspots[0]] != ""
    assert {"csv", "settings", "state", "accepted_structure", "rejected_structure"} <= {f.role for f in run.files}


def test_modality_unknown_columns_and_extras(toy_bc2_a):
    run = toy_bc2_a
    assert run.annotations["modality"] == "de novo" and run.settings.protocol == "de novo"
    d = run.accepted()[0]
    assert d.extras["Interface_Target_Residues"][0].isalpha() and d.extras["outcome"] == "passed" and d.extras["settings_core"] == "default"
    assert d.extras["n_binder_chains"] == 1 and d.extras["modality"] == "de novo"
    assert run.backbones[0].extras["Mystery_Score"] == pytest.approx(0.1 * run.backbones[0].extras["trajectory"])
    assert run.annotations["unknown_columns"] == {"1_Trajectories": ["Mystery_Score"]}        # the columns of the real campaign are all known
    assert next(b for b in run.backbones if not b.design_ids).trajectory_metrics["i_pTM"] is None


def test_target_hash_in_the_metadata_is_compared_with_the_construct(toya, tmp_path, toy_construct):
    import hashlib

    root = copy_campaign(toya.parent, "toya", tmp_path)
    meta = json.loads((root / "campaign_metadata.json").read_text())
    meta["settings"]["targets"][0]["target_file_hash"] = hashlib.sha256(toy_construct.read_bytes()).hexdigest()
    (root / "campaign_metadata.json").write_text(json.dumps(meta))
    run = bindcraft2.read_run(root, "x", construct_path=toy_construct)
    assert any("matches the hash recorded" in n for n in run.notes)


def test_to_dict_drops_sequences(toy_bc2_a):
    out = toy_bc2_a.to_dict()
    assert all(d["sequence"] is None for d in out["designs"]) and all(b["start_sequence"] is None for b in out["backbones"])
    json.dumps(out)
    assert all(d["sequence"] for d in toy_bc2_a.to_dict(include_sequences=True)["designs"])


def test_check_structures_off(toya, truth):
    run = bindcraft2.read_run(toya, "a", check_structures=False, hash_structures=False)
    assert run.target.numbers == [] and len(run.accepted()) == len(truth["toya"]["passed"]) and not any(f.role.endswith("_structure") for f in run.files)
    assert run.annotations["binder_numbering"]["basis"] == "version"            # stamped 1.0.1
    assert run.annotations["binder_numbering"]["offset_to_one_based"] == 1
    assert run.experimental and run.warnings[0].startswith("UNTESTED")


# ---------------------------------------------------------------------------------------------- toyb
def test_toyb_no_metadata_scale_numbering_and_gapped_target(toy_bc2_b, toy_dir, truth):
    run, t = toy_bc2_b, truth["toyb"]
    assert run.experimental and run.warnings[0].startswith("UNTESTED") and "version is not recorded" in run.warnings[0]
    assert "no readable campaign_metadata.json" in run.warnings[0]
    assert run.tool_version == "2 (version not recorded)"
    assert run.annotations["modality"] == "de novo" and sorted(d.id for d in run.accepted()) == sorted(t["passed"]) and len(t["passed"]) >= 1
    bn = run.annotations["binder_numbering"]
    assert bn["offset_to_one_based"] == 0 and bn["basis"] == "structure"
    d = run.accepted()[0]
    assert first_binder_number(d.structure_path) == 1
    raw = csv(toy_dir / "bindcraft2" / "toyb" / REF)
    assert float(raw["pLDDT"][0]) > 1.5                                        # written 0 to 100 ...
    assert 0.8 < d.metrics["pLDDT"] < 0.95 and 0.8 < d.metrics["Target_pLDDT"] < 1.0 and 0.8 < d.metrics["ss_pLDDT"] < 0.95      # ... and rescaled to 0 to 1
    assert any("0-100" in n for n in run.notes)
    assert d.extras["Brand_New_Column"] == "kept as an extra"
    assert run.annotations["unknown_columns"] == {"3_Ranked": ["Brand_New_Column"]}
    assert run.target.n_present == 102 and run.target.deleted_ranges == [(20, 23), (80, 83)]
    assert run.settings.hotspots == [] and any("No campaign_metadata.json" in n for n in run.notes)
    assert run.funnel.trajectory_rows is None and run.funnel.status["trajectory_rows"] == "unknown"
    assert [w for w in run.warnings[1:]] == []
    # no trajectory table: the families come from the hash and carry no row order; the id is the trajectory's design name
    assert {b.id for b in run.backbones} == set(t["families"]) and all(b.order == -1 and b.design_ids for b in run.backbones)


# ---------------------------------------------------------------------------------------------- toyc: the verified version
def test_the_verified_version_and_modality_is_not_flagged(toy_bc2_c, truth):
    run, t = toy_bc2_c, truth["toyc"]
    assert run.experimental is False and run.warnings == []                         # nothing to warn about: layout, version and modality recognised
    assert run.tool_version == "2 v1.0.3 (revision 0123456789abcdef0123456789abcdef01234567-dirty)" and run.annotations["modality"] == "de novo"
    assert sorted(d.id for d in run.accepted()) == sorted(t["passed"]) and run.annotations["binder_numbering"]["offset_to_one_based"] == 0
    assert len(run.backbones) == t["n_trajectories"] and {len(b.design_ids) for b in run.backbones if b.design_ids} == {len(v) for v in t["families"].values()}
    assert len(run.accepted_backbones()) == len({run.design(i).backbone_id for i in t["passed"]})
    assert [d.rank for d in run.accepted()][: len(t["ranked"])] == sorted(t["ranked"].values())


def test_the_construct_file_is_taken_from_the_metadata(toy_bc2_c, truth, toy_construct):
    t = toy_bc2_c.target
    assert t.reference_path == str(toy_construct.resolve()) and t.sequence_matches_reference
    assert [list(x) for x in t.deleted_ranges] == truth["toyc"]["deleted_construct"] and t.n_present == 102
    assert any("Target construct taken from the metadata" in n for n in toy_bc2_c.notes)
    assert toy_bc2_c.settings.starting_pdb_name == "toy_construct.pdb" and toy_bc2_c.settings.hotspots == truth["toyc"]["hotspots_construct"]
    explicit = bindcraft2.read_run(toy_bc2_c.source_dir, "x", construct_path=toy_construct)
    assert explicit.target.reference_path == t.reference_path and not any("taken from the metadata" in n for n in explicit.notes)


def test_inspect_numbers_of_the_verified_toy_campaign(toy_bc2_c, truth):
    """The figures `binder-review inspect` prints, from the generator's own counts."""
    t = truth["toyc"]
    fails = {}
    for names in t["rejected"].values():
        for n in names:
            fails[n] = fails.get(n, 0) + 1
    from collections import Counter
    assert dict(Counter(s for d in toy_bc2_c.rejected() for s in d.failed_stems)) == fails
    assert (len(toy_bc2_c.accepted()), len(toy_bc2_c.rejected())) == (len(t["passed"]), len(t["rejected"]))
    assert len(toy_bc2_c.accepted_backbones()) == len({toy_bc2_c.design(i).backbone_id for i in t["passed"]})


def _untested(root: Path):
    run = bindcraft2.read_run(root, "x")
    assert run.experimental and len(run.warnings) == 1 and run.warnings[0].startswith("UNTESTED")
    warning = run.warnings[0]
    assert "de novo (binder modality)" in warning and "v1.0.3" in warning        # what the reader was checked on
    # and not a catalogue of modalities this campaign has nothing to do with: the
    # warning used to list "VHH, scFv, Fab, cyclic-peptide" whichever campaign it was.
    assert "VHH, scFv, Fab" not in warning
    assert "counts_match_csv" in warning                                         # what is checked for you anyway
    return warning


def test_another_version_stays_untested_with_a_narrow_warning(toyc, tmp_path):
    root = copy_campaign(toyc.parent, "toyc", tmp_path)
    meta = json.loads((root / "campaign_metadata.json").read_text())
    meta["version"] = "BindCraft 2 v1.0.4"
    (root / "campaign_metadata.json").write_text(json.dumps(meta))
    for rel in (REF, RANK, TRAJ):
        edit_csv(root, rel, lambda df: df.__setitem__("bindcraft_version", "BindCraft 2 v1.0.4"))
    assert "written by BindCraft 2 v1.0.4" in _untested(root)


def test_a_version_recorded_nowhere_stays_untested(toyc, tmp_path):
    root = copy_campaign(toyc.parent, "toyc", tmp_path)
    meta = json.loads((root / "campaign_metadata.json").read_text())
    del meta["version"]
    (root / "campaign_metadata.json").write_text(json.dumps(meta))
    for rel in (REF, RANK, TRAJ):
        edit_csv(root, rel, lambda df: df.drop(columns=["bindcraft_version"], inplace=True, errors="ignore"))
    assert "version is not recorded" in _untested(root)


def test_another_modality_stays_untested(toyc, tmp_path):
    root = copy_campaign(toyc.parent, "toyc", tmp_path)
    meta = json.loads((root / "campaign_metadata.json").read_text())
    meta["settings"]["modality"] = meta["metadata"]["settings_modality"] = "vhh"
    (root / "campaign_metadata.json").write_text(json.dumps(meta))
    w = _untested(root)
    assert "designs are named de novo but its settings name the modality 'vhh'" in w
    # a campaign whose designs are named for another modality is untested for that reason alone
    renamed = copy_campaign(toyc.parent, "toyc", tmp_path / "b")
    for rel in (REF, RANK, TRAJ):
        edit_csv(renamed, rel, lambda df: df.__setitem__("design", df["design"].str.replace("_denovo_", "_vhh_")))
    w = bindcraft2.read_run(renamed, "x").warnings[0]
    assert "its modality is VHH" in w and "settings name" not in w


def test_a_layout_change_makes_the_run_untested(toyc, tmp_path):
    root = copy_campaign(toyc.parent, "toyc", tmp_path)
    edit_csv(root, REF, lambda df: df.drop(columns=["outcome"], inplace=True))
    run = bindcraft2.read_run(root, "x")
    assert run.experimental and "lacks the column(s) outcome" in run.warnings[0]
    assert any("no outcome column" in w for w in run.warnings) and len(run.accepted()) == len(csv(root / RANK))         # only the ranked are known
    (root / "campaign_metadata.json").unlink()
    assert "no readable campaign_metadata.json" in bindcraft2.read_run(root, "x").warnings[0]


def test_version_from_the_rows_when_the_metadata_has_none(toyc, tmp_path):
    root = copy_campaign(toyc.parent, "toyc", tmp_path)
    meta = json.loads((root / "campaign_metadata.json").read_text())
    del meta["version"]
    (root / "campaign_metadata.json").write_text(json.dumps(meta))
    run = bindcraft2.read_run(root, "x")
    assert run.tool_version.startswith("2 v1.0.3") and not run.experimental
    meta["version"] = "BindCraft 2 v1.0.2"
    (root / "campaign_metadata.json").write_text(json.dumps(meta))
    run = bindcraft2.read_run(root, "x")
    assert any("metadata says BindCraft 1.0.2" in w for w in run.warnings) and run.experimental


# ---------------------------------------------------------------------------------------------- tolerance
def test_designs_with_no_hash_and_no_trajectory_are_flagged_not_silently_split(toyc, tmp_path):
    """The failure that made every design its own family: a name that carries no hash and no trajectory column."""
    root = copy_campaign(toyc.parent, "toyc", tmp_path)
    edit_csv(root, REF, lambda df: (df.drop(columns=["hash"], inplace=True), df.__setitem__("design", [f"plain{i}" for i in range(len(df))])))
    run = bindcraft2.read_run(root, "x")
    assert len({d.backbone_id for d in run.designs}) == len(run.designs)
    assert any("neither a hash nor a trajectory value" in w for w in run.warnings) and run.experimental


def test_unknown_columns_and_odd_metadata_never_crash(toy_dir, tmp_path):
    root = copy_campaign(toy_dir, "bindcraft2/toyb", tmp_path)
    for rel in (REF, RANK):
        p = root / rel
        lines = p.read_text().splitlines()
        lines[0] += ",Totally_New,Another One"
        lines[1:] = [ln + ",1.5,text" for ln in lines[1:]]
        p.write_text("\n".join(lines) + "\n")
    (root / "campaign_metadata.json").write_text(json.dumps(
        {"version": ["not", "a", "string"], "filters": [1, 2, 3], "hotspots": {"x": 1}, "lengths": "??"}))
    run = bindcraft2.read_run(root, "x")
    d = run.accepted()[0]
    assert d.extras["Totally_New"] == 1.5 and d.extras["Another One"] == "text"
    assert "Totally_New" in run.annotations["unknown_columns"]["3_Ranked"] and run.settings.hotspots == []


def test_broken_metadata_and_missing_tables_give_warnings(toy_dir, tmp_path):
    root = copy_campaign(toy_dir, "bindcraft2/toya", tmp_path)
    (root / "campaign_metadata.json").write_text("{not json")
    run = bindcraft2.read_run(root, "x")
    assert any("could not be parsed" in w for w in run.warnings) and run.accepted()
    shutil.rmtree(root / "3_Ranked")
    (root / "2_Refolded" / "!_Refolded.csv").write_text(
        "design,outcome,failed_filters,hash\n"
        "c_denovo_l5_0123456789abcdef_candidate1,passed,,0123456789abcdef\n"
        "c_denovo_l6_fedcba9876543210_candidate1,passed,i_pTM,fedcba9876543210\n"
        "c_denovo_l6_fedcba9876543210_candidate2,rejected,i_pTM,fedcba9876543210\n")
    run = bindcraft2.read_run(root, "x")
    assert [d.id for d in run.accepted()] == ["c_denovo_l5_0123456789abcdef_candidate1"]       # a design that names a failed filter is not accepted
    assert run.accepted()[0].structure_path is None and any("no mmCIF" in w for w in run.warnings)
    assert {d.backbone_id for d in run.designs} == {"c_denovo_l5_0123456789abcdef", "c_denovo_l6_fedcba9876543210"}


def test_version_stamp_that_lags_does_not_override_the_structures(toya, tmp_path):
    root = copy_campaign(toya.parent, "toya", tmp_path)
    meta = json.loads((root / "campaign_metadata.json").read_text())
    meta["version"] = "BindCraft 2 v1.0.3"                                  # newer stamp, but the files are zero-based
    (root / "campaign_metadata.json").write_text(json.dumps(meta))
    for rel in (REF, RANK, TRAJ):
        edit_csv(root, rel, lambda df: df.__setitem__("bindcraft_version", "BindCraft 2 v1.0.3"))
    run = bindcraft2.read_run(root, "x")
    assert run.annotations["binder_numbering"]["offset_to_one_based"] == 1 and any("stamps can lag" in n for n in run.notes)


def test_settings_path_option_and_unknown_options(toy_dir, tmp_path):
    meta = tmp_path / "other_metadata.json"
    meta.write_text(json.dumps({"campaign_name": "named", "hotspots": [5, 7], "version": "BindCraft 2 v1.0.2"}))
    run = bindcraft2.read_run(toy_dir / "bindcraft2" / "toyb", "x", settings_path=meta, some_future_option=1)
    assert run.settings.binder_name == "named" and run.settings.hotspots == [5, 7]
    assert run.annotations["binder_numbering"]["offset_to_one_based"] == 0   # the structures win over the stamp
    missing = bindcraft2.read_run(toy_dir / "bindcraft2" / "toyb", "x", settings_path=tmp_path / "nope.json")
    assert any("not found" in w for w in missing.warnings)


def test_inspect_cli_on_toy_campaigns(toy_dir, capsys):
    from binder_review import cli

    assert cli.main(["inspect", str(toy_dir / "bindcraft2" / "toya"), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reader"] == "bindcraft2" and out["experimental"] is True
    assert cli.main(["inspect", str(toy_dir / "bindcraft2" / "toyb"), "--no-structure-check"]) == 0
    text = capsys.readouterr().out
    assert "[experimental]" in text and "UNTESTED campaign" in text
    assert cli.main(["inspect", str(toy_dir / "bindcraft2_v103" / "toyc")]) == 0
    text = capsys.readouterr().out
    assert "[experimental]" not in text and "UNTESTED" not in text and "Warnings: none" in text and "BindCraft 2 v1.0.3" in text
    assert "BindCraft BindCraft" not in text and "vBindCraft" not in text
