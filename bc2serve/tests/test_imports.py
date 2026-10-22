"""Runs brought in from elsewhere: what is refused, what the link does, and the target stand-in."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path

from bc2serve import imports, tables


def make_run(folder: Path, passed: int = 2, ranked: int = 1, metadata: bool = True,
             target: str = "/elsewhere/target.cif") -> Path:
    """A folder shaped like a BindCraft 2 campaign, with as many finished designs as asked."""
    folder.mkdir(parents=True, exist_ok=True)
    if metadata:
        (folder / "campaign_metadata.json").write_text(json.dumps(
            {"version": "BindCraft 2 v1.0.3",
             "settings": {"modality": "binder", "targets": [{"target_path": target, "chains": "A"}]}}))
    rows = [[f"d{i}", "passed" if i < passed else "rejected", f"h{i}"] for i in range(max(passed, 3))]
    _csv(folder / "2_Refolded" / "!_Refolded.csv", ["design", "outcome", "hash"], rows)
    _csv(folder / "3_Ranked" / "!_Ranked.csv", ["design", "rank", "hash"],
         [[f"d{i}_seq0", str(i + 1), f"h{i}"] for i in range(ranked)])
    return folder


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


class ImportTest(unittest.TestCase):
    def setUp(self):
        tables.clear_cache()
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name)
        self.root = self.dir / "results"
        self.state = self.dir / "state"
        self.uploads = self.state / "uploads"
        self.root.mkdir(parents=True)
        self.imports = imports.Imports(self.state, self.root, self.uploads)

    # ---------------------------------------------------------------- what is refused

    def test_a_path_that_is_not_there_is_named_in_the_refusal(self):
        with self.assertRaises(imports.BadImport) as caught:
            self.imports.add(str(self.dir / "nowhere"))
        self.assertIn(str(self.dir / "nowhere"), str(caught.exception))

    def test_a_relative_path_is_refused_before_anything_is_looked_for(self):
        with self.assertRaises(imports.BadImport) as caught:
            self.imports.add("some/run")
        self.assertIn("starting at /", str(caught.exception))

    def test_a_folder_that_is_not_a_run_says_what_a_run_folder_is(self):
        plain = self.dir / "notarun"
        plain.mkdir()
        with self.assertRaises(imports.BadImport) as caught:
            self.imports.add(str(plain))
        self.assertIn("2_Refolded", str(caught.exception))

    def test_a_run_with_nothing_finished_is_refused_with_its_counts(self):
        run = make_run(self.dir / "early", passed=0, ranked=0)
        with self.assertRaises(imports.BadImport) as caught:
            self.imports.add(str(run))
        self.assertIn("nothing to review", str(caught.exception))

    def test_a_file_is_not_a_run_folder(self):
        handle = self.dir / "run.tar.gz"
        handle.write_text("not a folder")
        with self.assertRaises(imports.BadImport) as caught:
            self.imports.add(str(handle))
        self.assertIn("is a file", str(caught.exception))

    def test_a_run_already_in_the_results_folder_needs_no_import(self):
        run = make_run(self.root / "already")
        with self.assertRaises(imports.BadImport) as caught:
            self.imports.add(str(run))
        self.assertIn("already in the results folder", str(caught.exception))

    def test_a_second_run_cannot_take_a_name_that_is_taken(self):
        self.imports.add(str(make_run(self.dir / "a" / "run1")))
        with self.assertRaises(imports.BadImport) as caught:
            self.imports.add(str(make_run(self.dir / "b" / "run1")))
        self.assertIn("already a campaign called run1", str(caught.exception))

    def test_a_name_that_cannot_be_a_campaign_name_is_refused(self):
        run = make_run(self.dir / "ok")
        with self.assertRaises(imports.BadImport) as caught:
            self.imports.add(str(run), name="...")
        self.assertIn("starts with a letter", str(caught.exception))

    # ---------------------------------------------------------------- what it does

    def test_the_run_is_linked_and_not_copied(self):
        run = make_run(self.dir / "runs" / "spike_run")
        row = self.imports.add(str(run))
        link = self.root / "spike_run"
        self.assertEqual(row["name"], "spike_run")
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), run.resolve())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["spike_run"])

    def test_a_folder_name_is_shaped_into_a_campaign_name(self):
        row = self.imports.add(str(make_run(self.dir / "Spike run 2")))
        self.assertEqual(row["name"], "Spike_run_2")
        self.assertTrue((self.root / "Spike_run_2").is_symlink())

    def test_the_real_path_is_what_the_file_endpoints_are_told(self):
        run = make_run(self.dir / "runs" / "one")
        self.imports.add(str(run))
        self.assertEqual([str(p) for p in self.imports.paths()], [str(run.resolve())])

    def test_removing_takes_the_link_and_leaves_the_run(self):
        run = make_run(self.dir / "runs" / "two")
        self.imports.add(str(run))
        self.imports.remove("two")
        self.assertFalse((self.root / "two").exists())
        self.assertTrue((run / "campaign_metadata.json").is_file())
        self.assertEqual(self.imports.rows(), [])

    def test_removing_something_that_was_not_imported_is_refused(self):
        (self.root / "mine").mkdir()
        with self.assertRaises(imports.BadImport):
            self.imports.remove("mine")
        self.assertTrue((self.root / "mine").is_dir())

    def test_a_row_says_when_the_run_has_gone_from_disk(self):
        run = make_run(self.dir / "runs" / "three")
        self.imports.add(str(run))
        for path in sorted(run.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        run.rmdir()
        row = self.imports.rows()[0]
        self.assertFalse(row["here"])
        self.assertEqual(self.imports.paths(), [])      # nothing to widen the file endpoints with

    def test_the_registry_survives_a_restart(self):
        run = make_run(self.dir / "runs" / "four")
        self.imports.add(str(run))
        again = imports.Imports(self.state, self.root, self.uploads)
        self.assertEqual([r["name"] for r in again.rows()], ["four"])

    # ---------------------------------------------------------------- the target structure

    def test_a_target_given_at_import_is_kept_and_found_again(self):
        run = make_run(self.dir / "runs" / "five")
        target = self.dir / "target.cif"
        target.write_text("data_x\n")
        self.imports.add(str(run), construct=str(target))
        kept = self.uploads / "target.cif"
        self.assertTrue(kept.is_file())
        self.assertEqual(self.imports.construct_for(self.root / "five"), str(kept))
        self.assertEqual(self.imports.construct_for(run), str(kept))

    def test_a_run_with_no_target_given_has_none_to_find(self):
        run = make_run(self.dir / "runs" / "six")
        self.imports.add(str(run))
        self.assertIsNone(self.imports.construct_for(self.root / "six"))

    def test_a_target_that_is_not_a_structure_is_refused_before_the_link_is_made(self):
        run = make_run(self.dir / "runs" / "seven")
        notes = self.dir / "notes.txt"
        notes.write_text("hello")
        with self.assertRaises(imports.BadImport) as caught:
            self.imports.add(str(run), construct=str(notes))
        self.assertIn("PDB or mmCIF", str(caught.exception))
        self.assertFalse((self.root / "seven").exists())


if __name__ == "__main__":
    unittest.main()
