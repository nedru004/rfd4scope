"""The shared CSV reader, the definition of "passed", and the parse cache."""

from __future__ import annotations

import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bc2serve import tables


def write_csv(path: Path, header: list[str], rows: list[list[str]], tail: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
        handle.write(tail)


class TableTest(unittest.TestCase):
    def setUp(self):
        tables.clear_cache()
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name)


class ReadTableTests(TableTest):
    def test_reads_rows_and_strips_keys_and_values(self):
        path = self.dir / "t.csv"
        path.write_text(" design , outcome \n  a ,  passed  \n")
        self.assertEqual(tables.read_table(path), [{"design": "a", "outcome": "passed"}])

    def test_missing_file_is_empty(self):
        self.assertEqual(tables.read_table(self.dir / "nope.csv"), [])

    def test_missing_folder_is_empty(self):
        self.assertEqual(tables.read_table(self.dir / "gone" / "deeper" / "t.csv"), [])

    def test_empty_file_is_empty(self):
        path = self.dir / "t.csv"
        path.write_text("")
        self.assertEqual(tables.read_table(path), [])

    def test_header_only_is_empty(self):
        path = self.dir / "t.csv"
        path.write_text("design,outcome\n")
        self.assertEqual(tables.read_table(path), [])

    def test_directory_where_a_file_should_be_is_empty_not_an_error(self):
        (self.dir / "t.csv").mkdir()
        self.assertEqual(tables.read_table(self.dir / "t.csv"), [])

    def test_truncated_final_row_is_dropped(self):
        path = self.dir / "t.csv"
        # The campaign was mid-write: the last row stops after two of three cells.
        path.write_text("design,outcome,hash\na,passed,h1\nb,passed,h2\nc,pass")
        rows = tables.read_table(path)
        self.assertEqual([row["design"] for row in rows], ["a", "b"])
        for row in rows:
            self.assertTrue(all(isinstance(v, str) for v in row.values()))

    def test_truncated_final_row_with_no_commas_is_dropped(self):
        path = self.dir / "t.csv"
        path.write_text("design,outcome,hash\na,passed,h1\nb_cand")
        self.assertEqual([row["design"] for row in tables.read_table(path)], ["a"])

    def test_complete_final_row_without_newline_is_kept(self):
        path = self.dir / "t.csv"
        path.write_text("design,outcome\na,passed\nb,rejected")
        self.assertEqual(len(tables.read_table(path)), 2)

    def test_short_row_in_the_middle_is_kept_with_empty_cells(self):
        path = self.dir / "t.csv"
        path.write_text("design,outcome,hash\na,passed\nb,passed,h2\n")
        rows = tables.read_table(path)
        self.assertEqual(rows[0], {"design": "a", "outcome": "passed", "hash": ""})
        self.assertEqual(len(rows), 2)

    def test_overlong_row_does_not_leak_a_none_key(self):
        path = self.dir / "t.csv"
        path.write_text("design,outcome\na,passed,extra\nb,passed\n")
        rows = tables.read_table(path)
        self.assertEqual(rows[0], {"design": "a", "outcome": "passed"})

    def test_byte_order_mark_and_bad_bytes_do_not_raise(self):
        path = self.dir / "t.csv"
        path.write_bytes(b"\xef\xbb\xbfdesign,outcome\na\xff,passed\n")
        rows = tables.read_table(path)
        self.assertEqual(len(rows), 1)
        self.assertIn("design", rows[0])

    def test_oserror_on_open_is_swallowed(self):
        path = self.dir / "t.csv"
        path.write_text("design\na\n")
        with mock.patch("builtins.open", side_effect=PermissionError):
            self.assertEqual(tables.read_table(path), [])

    def test_returned_rows_are_copies(self):
        path = self.dir / "t.csv"
        path.write_text("design\na\n")
        tables.read_table(path)[0]["design"] = "changed"
        self.assertEqual(tables.read_table(path)[0]["design"], "a")


class IsPassedTests(unittest.TestCase):
    def test_exact_definition(self):
        self.assertTrue(tables.is_passed({"outcome": "passed"}))
        self.assertTrue(tables.is_passed({"outcome": " passed "}))
        self.assertFalse(tables.is_passed({"outcome": "rejected"}))
        self.assertFalse(tables.is_passed({"outcome": "Passed"}))
        self.assertFalse(tables.is_passed({"outcome": ""}))
        self.assertFalse(tables.is_passed({}))
        self.assertFalse(tables.is_passed({"outcome": None}))


class DesignCountsTests(TableTest):
    def test_counts_all_four_tiers(self):
        write_csv(self.dir / "1_Trajectories" / "!_Trajectories.csv", ["trajectory", "hash"],
                  [["1", "a"], ["2", "b"], ["3", "c"]])
        write_csv(self.dir / "2_Refolded" / "!_Refolded.csv", ["design", "outcome"],
                  [["a1", "passed"], ["a2", "rejected"], ["b1", "passed"], ["c1", " passed "]])
        write_csv(self.dir / "3_Ranked" / "!_Ranked.csv", ["rank", "design"],
                  [["1", "a_seq0"], ["2", "b_seq0"]])
        self.assertEqual(tables.design_counts(self.dir), {
            "trajectories": 3, "refolded": 4, "passed": 3, "ranked": 2})

    def test_empty_and_missing_folders_count_zero(self):
        zero = {"trajectories": 0, "refolded": 0, "passed": 0, "ranked": 0}
        self.assertEqual(tables.design_counts(self.dir), zero)
        self.assertEqual(tables.design_counts(self.dir / "gone"), zero)

    def test_agrees_with_review_py_on_a_finished_campaign(self):
        # review.py cannot be imported here (numpy, biotite), so its definition is
        # reproduced from review.py:104-126 and compared on a complete table.
        def review_table(path):
            try:
                with open(path, newline="") as handle:
                    return list(csv.DictReader(handle))
            except (OSError, csv.Error):
                return []

        write_csv(self.dir / "2_Refolded" / "!_Refolded.csv", ["design", "outcome"],
                  [["a", "passed"], ["b", "rejected"], ["c", "passed "]])
        write_csv(self.dir / "3_Ranked" / "!_Ranked.csv", ["rank", "design"], [["1", "a"]])
        refolded = review_table(self.dir / "2_Refolded" / "!_Refolded.csv")
        theirs = {
            "trajectories": len(review_table(self.dir / "1_Trajectories" / "!_Trajectories.csv")),
            "refolded": len(refolded),
            "passed": sum(1 for r in refolded if (r.get("outcome") or "").strip() == "passed"),
            "ranked": len(review_table(self.dir / "3_Ranked" / "!_Ranked.csv")),
        }
        self.assertEqual(tables.design_counts(self.dir), theirs)

    def test_truncated_last_row_is_not_counted(self):
        write_csv(self.dir / "2_Refolded" / "!_Refolded.csv", ["design", "outcome", "hash"],
                  [["a", "passed", "h"]], tail="b,pass")
        counts = tables.design_counts(self.dir)
        self.assertEqual((counts["refolded"], counts["passed"]), (1, 1))


class CacheTests(TableTest):
    def test_parsed_once_while_unchanged(self):
        path = self.dir / "t.csv"
        path.write_text("design\na\n")
        with mock.patch.object(tables, "_parse", wraps=tables._parse) as parse:
            tables.read_table(path)
            tables.read_table(path)
            tables.design_counts(self.dir)  # three other files, none of which exist
        self.assertEqual(parse.call_count, 1)

    def test_fresh_data_after_mtime_and_size_change(self):
        path = self.dir / "t.csv"
        path.write_text("design\na\n")
        self.assertEqual(len(tables.read_table(path)), 1)
        path.write_text("design\na\nb\nc\n")
        stamp = path.stat().st_mtime_ns + 5_000_000_000
        os.utime(path, ns=(stamp, stamp))
        self.assertEqual([r["design"] for r in tables.read_table(path)], ["a", "b", "c"])

    def test_fresh_data_when_only_size_changes(self):
        path = self.dir / "t.csv"
        path.write_text("design\na\n")
        before = path.stat()
        tables.read_table(path)
        path.write_text("design\na\nb\n")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(len(tables.read_table(path)), 2)

    def test_cache_is_bounded(self):
        limit = tables.CACHED_CAMPAIGNS * tables.TABLES_PER_CAMPAIGN
        for index in range(limit + 10):
            path = self.dir / f"t{index}.csv"
            path.write_text("design\na\n")
            tables.read_table(path)
        self.assertEqual(len(tables._cache), limit)

    def test_least_recently_used_goes_first(self):
        limit = tables.CACHED_CAMPAIGNS * tables.TABLES_PER_CAMPAIGN
        paths = []
        for index in range(limit):
            path = self.dir / f"t{index}.csv"
            path.write_text("design\na\n")
            tables.read_table(path)
            paths.append(path)
        tables.read_table(paths[0])  # touch the oldest
        extra = self.dir / "extra.csv"
        extra.write_text("design\na\n")
        tables.read_table(extra)
        cached = {key[0] for key in tables._cache}
        self.assertIn(str(paths[0]), cached)
        self.assertNotIn(str(paths[1]), cached)

    def test_a_changed_file_does_not_keep_its_old_entry(self):
        path = self.dir / "t.csv"
        path.write_text("design\na\n")
        tables.read_table(path)
        path.write_text("design\na\nb\n")
        tables.read_table(path)
        self.assertEqual(sum(1 for key in tables._cache if key[0] == str(path)), 1)


if __name__ == "__main__":
    unittest.main()

