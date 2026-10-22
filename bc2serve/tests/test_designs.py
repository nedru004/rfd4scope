"""The project design listing, against toy campaign folders."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from bc2serve import designs, tables

REFOLDED_HEADER = [
    "design", "length", "outcome", "Binder_Sequence", "i_pDAE", "i_pTM", "pLDDT", "i_pAE",
    "Interface_Residues", "Interface_BuriedArea", "Backbone_Clashes", "All_Atom_Clashes",
    "Binder_Disulfides", "Binder_Mass_kDa", "Binder_pI", "Binder_Net_Charge",
    "Binder_Free_Cysteines", "Timing", "failed_filters", "hash",
]
RANKED_HEADER = ["rank", "design", "length", "Binder_Sequence", "i_pTM", "hash",
                 "settings_core", "settings_modality"]


def refolded_row(design, sequence, digest, outcome="passed", **extra):
    values = {"design": design, "length": "85", "outcome": outcome,
              "Binder_Sequence": sequence, "i_pDAE": "5.1", "i_pTM": "0.82",
              "pLDDT": "0.9", "i_pAE": "0.2", "Interface_Residues": "30",
              "Interface_BuriedArea": "850.5", "Backbone_Clashes": "0",
              "All_Atom_Clashes": "2", "Binder_Disulfides": "0", "Binder_Mass_kDa": "9.4",
              "Binder_pI": "6.7", "Binder_Net_Charge": "-1.0", "Binder_Free_Cysteines": "1",
              "Timing": "t", "failed_filters": "", "hash": digest}
    values.update(extra)
    return [values[column] for column in REFOLDED_HEADER]


def ranked_row(rank, design, sequence, digest):
    return [str(rank), design, "85", sequence, "0.82", digest, "core", "mod"]


def write(path: Path, header, rows, tail=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
        handle.write(tail)


def make_campaign(root: Path, name: str, refolded, ranked=(), complexes=(), monomers=()) -> Path:
    folder = root / name
    write(folder / "2_Refolded" / "!_Refolded.csv", REFOLDED_HEADER, refolded)
    if ranked is not None:
        write(folder / "3_Ranked" / "!_Ranked.csv", RANKED_HEADER, ranked)
    for stem in complexes:
        (folder / "2_Refolded" / "Complexes").mkdir(parents=True, exist_ok=True)
        (folder / "2_Refolded" / "Complexes" / f"{stem}.cif").write_text("data_x\n")
    for stem in monomers:
        (folder / "2_Refolded" / "BinderMonomer").mkdir(parents=True, exist_ok=True)
        (folder / "2_Refolded" / "BinderMonomer" / f"{stem}_monomer.cif").write_text("data_x\n")
    return folder


class DesignTest(unittest.TestCase):
    def setUp(self):
        tables.clear_cache()
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)


class StructureTests(DesignTest):
    def test_exact_stem_never_matches_a_longer_name(self):
        folder = make_campaign(
            self.root, "c",
            [refolded_row("x_candidate1", "AAA", "h1"),
             refolded_row("x_candidate10", "CCC", "h2")],
            complexes=["x_candidate1", "x_candidate10"],
            monomers=["x_candidate1", "x_candidate10"])
        by_design = {d["design"]: d for d in designs.campaign_designs(folder)}
        one, ten = by_design["x_candidate1"], by_design["x_candidate10"]
        self.assertTrue(one["structure"].endswith("Complexes/x_candidate1.cif"))
        self.assertTrue(ten["structure"].endswith("Complexes/x_candidate10.cif"))
        self.assertTrue(one["monomer"].endswith("BinderMonomer/x_candidate1_monomer.cif"))
        self.assertTrue(ten["monomer"].endswith("BinderMonomer/x_candidate10_monomer.cif"))

    def test_the_shorter_name_does_not_borrow_the_longer_file(self):
        folder = make_campaign(
            self.root, "c", [refolded_row("x_candidate1", "AAA", "h1")],
            complexes=["x_candidate10"], monomers=["x_candidate10"])
        (row,) = designs.campaign_designs(folder)
        self.assertIsNone(row["structure"])
        self.assertIsNone(row["monomer"])

    def test_absent_structure_is_none_and_present_is_a_real_file(self):
        folder = make_campaign(self.root, "c", [refolded_row("a", "AAA", "h1")], complexes=["a"])
        (row,) = designs.campaign_designs(folder)
        self.assertTrue(Path(row["structure"]).is_file())
        self.assertIsNone(row["monomer"])

    def test_only_passed_rows_get_structures_and_listing(self):
        folder = make_campaign(
            self.root, "c",
            [refolded_row("a", "AAA", "h1"), refolded_row("b", "BBB", "h2", outcome="rejected")],
            complexes=["a", "b"])
        rows = designs.campaign_designs(folder)
        self.assertEqual([r["design"] for r in rows], ["a"])

    def test_a_design_id_cannot_climb_out_of_the_folder(self):
        folder = make_campaign(self.root, "c", [refolded_row("../../secret", "AAA", "h1")])
        (self.root / "secret.cif").write_text("data_x\n")
        (row,) = designs.campaign_designs(folder)
        self.assertIsNone(row["structure"])


class RankedJoinTests(DesignTest):
    def test_join_is_on_hash_and_sequence_not_design(self):
        folder = make_campaign(
            self.root, "c",
            [refolded_row("x_candidate3", "AAA", "h1")],
            ranked=[ranked_row(1, "x_seq0", "AAA", "h1")])
        (row,) = designs.campaign_designs(folder)
        self.assertTrue(row["ranked"])
        self.assertEqual(row["rank"], 1)
        self.assertIsInstance(row["rank"], int)

    def test_two_passed_rows_share_a_hash_but_only_one_is_ranked(self):
        folder = make_campaign(
            self.root, "c",
            [refolded_row("x_candidate1", "AAA", "h1"), refolded_row("x_candidate2", "AAB", "h1")],
            ranked=[ranked_row(4, "x_seq0", "AAB", "h1")])
        by_design = {d["design"]: d for d in designs.campaign_designs(folder)}
        self.assertFalse(by_design["x_candidate1"]["ranked"])
        self.assertIsNone(by_design["x_candidate1"]["rank"])
        self.assertTrue(by_design["x_candidate2"]["ranked"])
        self.assertEqual(by_design["x_candidate2"]["rank"], 4)

    def test_sequence_collision_across_hashes_does_not_join(self):
        folder = make_campaign(
            self.root, "c",
            [refolded_row("a_candidate1", "SAME", "h1"), refolded_row("b_candidate1", "SAME", "h2")],
            ranked=[ranked_row(1, "b_seq0", "SAME", "h2")])
        by_design = {d["design"]: d for d in designs.campaign_designs(folder)}
        self.assertFalse(by_design["a_candidate1"]["ranked"])
        self.assertTrue(by_design["b_candidate1"]["ranked"])

    def test_same_hash_different_sequence_does_not_join(self):
        folder = make_campaign(
            self.root, "c", [refolded_row("a_candidate1", "AAA", "h1")],
            ranked=[ranked_row(1, "a_seq0", "ZZZ", "h1")])
        (row,) = designs.campaign_designs(folder)
        self.assertFalse(row["ranked"])
        self.assertIsNone(row["rank"])

    def test_no_ranked_table_means_nothing_ranked(self):
        folder = make_campaign(self.root, "c", [refolded_row("a", "AAA", "h1")], ranked=None)
        (row,) = designs.campaign_designs(folder)
        self.assertFalse(row["ranked"])

    def test_truncated_ranked_row_is_ignored(self):
        folder = make_campaign(
            self.root, "c", [refolded_row("a", "AAA", "h1"), refolded_row("b", "BBB", "h2")],
            ranked=[ranked_row(1, "a_seq0", "AAA", "h1")], )
        with open(folder / "3_Ranked" / "!_Ranked.csv", "a") as handle:
            handle.write("2,b_seq0,85,BBB")  # cut off before hash
        by_design = {d["design"]: d for d in designs.campaign_designs(folder)}
        self.assertTrue(by_design["a"]["ranked"])
        self.assertFalse(by_design["b"]["ranked"])


class FamilyTests(DesignTest):
    def test_five_passed_over_three_hashes(self):
        folder = make_campaign(self.root, "c", [
            refolded_row("a_candidate1", "S1", "h1"),
            refolded_row("a_candidate2", "S2", "h1"),
            refolded_row("b_candidate1", "S3", "h2"),
            refolded_row("b_candidate2", "S4", "h2"),
            refolded_row("c_candidate1", "S5", "h3"),
            refolded_row("c_candidate2", "S6", "h3", outcome="rejected"),  # not counted
        ])
        rows = designs.campaign_designs(folder)
        self.assertEqual(len(rows), 5)
        self.assertEqual([(r["family"], r["siblings"]) for r in rows],
                         [("h1", 2), ("h1", 2), ("h2", 2), ("h2", 2), ("h3", 1)])

    def test_rejected_siblings_do_not_inflate_the_count(self):
        folder = make_campaign(self.root, "c", [
            refolded_row("a1", "S1", "h1"),
            refolded_row("a2", "S2", "h1", outcome="rejected"),
            refolded_row("a3", "S3", "h1", outcome="rejected"),
        ])
        (row,) = designs.campaign_designs(folder)
        self.assertEqual(row["siblings"], 1)

    def test_blank_hash_is_no_family(self):
        folder = make_campaign(self.root, "c", [
            refolded_row("a", "S1", ""), refolded_row("b", "S2", "")])
        for row in designs.campaign_designs(folder):
            self.assertIsNone(row["family"])
            self.assertEqual(row["siblings"], 1)


class ShapeTests(DesignTest):
    def test_numbers_are_numbers_and_blanks_are_none(self):
        folder = make_campaign(self.root, "c", [
            refolded_row("a", "AAA", "h1", i_pTM="", i_pDAE="nan", Binder_Mass_kDa="0",
                         Binder_Free_Cysteines="", length="85", Binder_Net_Charge="-1.0",
                         Backbone_Clashes="2.0", Binder_pI="inf")])
        (row,) = designs.campaign_designs(folder)
        self.assertIsNone(row["i_pTM"])
        self.assertIsNone(row["i_pDAE"])        # nan is not JSON
        self.assertIsNone(row["Binder_pI"])     # nor is inf
        self.assertIsNone(row["Binder_Free_Cysteines"])
        self.assertEqual(row["Binder_Mass_kDa"], 0.0)  # a real zero stays a zero
        self.assertIsNotNone(row["Binder_Mass_kDa"])
        self.assertIsInstance(row["length"], int)
        self.assertEqual(row["Binder_Net_Charge"], -1)
        self.assertIsInstance(row["Binder_Net_Charge"], int)
        self.assertEqual(row["Backbone_Clashes"], 2)
        self.assertIsInstance(row["pLDDT"], float)
        self.assertIsInstance(row["Interface_BuriedArea"], float)
        for column in designs.INTEGER_COLUMNS + designs.FLOAT_COLUMNS:
            self.assertNotIsInstance(row[column], str, column)
        json.dumps(row, allow_nan=False)  # must survive a strict JSON encoder

    def test_garbage_in_a_numeric_column_is_none(self):
        folder = make_campaign(self.root, "c", [refolded_row("a", "AAA", "h1", i_pTM="n/a")])
        (row,) = designs.campaign_designs(folder)
        self.assertIsNone(row["i_pTM"])

    def test_every_row_has_exactly_the_agreed_keys(self):
        folder = make_campaign(self.root, "c", [
            refolded_row("a", "AAA", "h1", i_pAE=""), refolded_row("b", "BBB", "h2", hash="")],
            ranked=[ranked_row(1, "a_seq0", "AAA", "h1")], complexes=["a"])
        rows = designs.campaign_designs(folder)
        for row in rows:
            self.assertEqual(set(row), set(designs.ROW_KEYS))
            self.assertNotIn("Timing", row)
            self.assertNotIn("failed_filters", row)
            self.assertNotIn("outcome", row)
        self.assertEqual(set(designs.ROW_KEYS), {
            "design", "campaign", "campaign_path", "length", "Binder_Sequence", "hash",
            "family", "siblings", "ranked", "rank", "i_pTM", "i_pDAE", "i_pAE", "pLDDT",
            "Interface_Residues", "Interface_BuriedArea", "Binder_Mass_kDa", "Binder_pI",
            "Binder_Net_Charge", "Binder_Free_Cysteines", "Binder_Disulfides",
            "Backbone_Clashes", "All_Atom_Clashes", "structure", "monomer"})

    def test_campaign_name_and_path_on_each_row(self):
        folder = make_campaign(self.root, "folder_name", [refolded_row("a", "AAA", "h1")])
        (default,) = designs.campaign_designs(folder)
        (named,) = designs.campaign_designs(folder, "Display")
        self.assertEqual(default["campaign"], "folder_name")
        self.assertEqual(named["campaign"], "Display")
        self.assertEqual(named["campaign_path"], str(folder))

    def test_columns_missing_from_the_csv_become_none(self):
        folder = self.root / "old"
        write(folder / "2_Refolded" / "!_Refolded.csv",
              ["design", "outcome", "Binder_Sequence", "hash"], [["a", "passed", "AAA", "h1"]])
        (row,) = designs.campaign_designs(folder)
        self.assertEqual(set(row), set(designs.ROW_KEYS))
        self.assertIsNone(row["i_pTM"])
        self.assertIsNone(row["length"])

    def test_listing_does_not_corrupt_the_cached_tables(self):
        folder = make_campaign(self.root, "c", [refolded_row("a", "AAA", "h1")])
        first = designs.campaign_designs(folder)
        first[0]["design"] = "mutated"
        self.assertEqual(designs.campaign_designs(folder)[0]["design"], "a")


class DegenerateInputTests(DesignTest):
    def test_header_only_refolded(self):
        folder = make_campaign(self.root, "c", [], ranked=[])
        self.assertEqual(designs.campaign_designs(folder), [])

    def test_empty_refolded(self):
        folder = self.root / "c"
        (folder / "2_Refolded").mkdir(parents=True)
        (folder / "2_Refolded" / "!_Refolded.csv").write_text("")
        self.assertEqual(designs.campaign_designs(folder), [])

    def test_folder_with_no_tables(self):
        (self.root / "empty").mkdir()
        self.assertEqual(designs.campaign_designs(self.root / "empty"), [])

    def test_missing_folder(self):
        self.assertEqual(designs.campaign_designs(self.root / "gone"), [])

    def test_truncated_final_refolded_row_is_not_a_design(self):
        folder = make_campaign(self.root, "c", [refolded_row("a", "AAA", "h1")])
        with open(folder / "2_Refolded" / "!_Refolded.csv", "a") as handle:
            handle.write("b,85,passed,BBB")
        self.assertEqual([r["design"] for r in designs.campaign_designs(folder)], ["a"])

    def test_structure_directories_missing_do_not_raise(self):
        folder = make_campaign(self.root, "c", [refolded_row("a", "AAA", "h1")])
        (row,) = designs.campaign_designs(folder)
        self.assertIsNone(row["structure"])


class CollectTests(DesignTest):
    def test_two_campaigns_in_order_with_counts(self):
        one = make_campaign(self.root, "one", [refolded_row("a", "AAA", "h1"),
                                               refolded_row("b", "BBB", "h2", outcome="rejected")],
                            ranked=[ranked_row(1, "a_seq0", "AAA", "h1")])
        two = make_campaign(self.root, "two", [refolded_row("c", "CCC", "h3")])
        result = designs.collect([("First", one), ("Second", two)])
        self.assertEqual([c["name"] for c in result["campaigns"]], ["First", "Second"])
        self.assertEqual(result["campaigns"][0]["counts"],
                         {"trajectories": 0, "refolded": 2, "passed": 1, "ranked": 1})
        self.assertEqual(result["campaigns"][0]["path"], str(one))
        self.assertFalse(result["campaigns"][0]["missing"])
        self.assertEqual([(d["campaign"], d["design"]) for d in result["designs"]],
                         [("First", "a"), ("Second", "c")])

    def test_missing_folder_is_listed_not_dropped_and_does_not_raise(self):
        one = make_campaign(self.root, "one", [refolded_row("a", "AAA", "h1")])
        result = designs.collect([("Gone", self.root / "gone"), ("One", one)])
        gone, present = result["campaigns"]
        self.assertTrue(gone["missing"])
        self.assertEqual(gone["name"], "Gone")
        self.assertEqual(gone["counts"], {"trajectories": 0, "refolded": 0, "passed": 0, "ranked": 0})
        self.assertFalse(present["missing"])
        self.assertEqual([d["campaign"] for d in result["designs"]], ["One"])

    def test_a_file_where_a_folder_should_be_is_missing(self):
        (self.root / "file").write_text("x")
        result = designs.collect([("F", self.root / "file")])
        self.assertTrue(result["campaigns"][0]["missing"])
        self.assertEqual(result["designs"], [])

    def test_an_existing_folder_with_no_tables_is_present_with_zero(self):
        (self.root / "fresh").mkdir()
        result = designs.collect([("Fresh", self.root / "fresh")])
        self.assertFalse(result["campaigns"][0]["missing"])
        self.assertEqual(result["campaigns"][0]["counts"]["passed"], 0)

    def test_nothing_in_nothing_out(self):
        self.assertEqual(designs.collect([]), {"designs": [], "campaigns": []})

    def test_same_basename_in_two_roots_stays_two_campaigns(self):
        a = make_campaign(self.root / "r1", "dup", [refolded_row("a", "AAA", "h1")])
        b = make_campaign(self.root / "r2", "dup", [refolded_row("b", "BBB", "h2")])
        result = designs.collect([("dup", a), ("dup", b)])
        self.assertEqual([c["path"] for c in result["campaigns"]], [str(a), str(b)])
        self.assertEqual([d["campaign_path"] for d in result["designs"]], [str(a), str(b)])

    def test_a_running_campaign_updates_between_calls(self):
        folder = make_campaign(self.root, "c", [refolded_row("a", "AAA", "h1")])
        self.assertEqual(len(designs.collect([("c", folder)])["designs"]), 1)
        with open(folder / "2_Refolded" / "!_Refolded.csv", "a", newline="") as handle:
            csv.writer(handle).writerow(refolded_row("b", "BBB", "h2"))
        self.assertEqual(len(designs.collect([("c", folder)])["designs"]), 2)

    def test_result_is_json_serialisable(self):
        folder = make_campaign(self.root, "c", [refolded_row("a", "AAA", "h1")], complexes=["a"])
        json.dumps(designs.collect([("c", folder)]), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
