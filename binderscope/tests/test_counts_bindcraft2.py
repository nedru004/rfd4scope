"""The independent count check, on a BindCraft 2 campaign.

`check_counts` compared only BindCraft 1 runs, so every BindCraft 2 review --
every campaign this program is actually used on -- printed "counts against the
CSVs: not checked", and a modality was called unverified on the grounds that
nobody had looked. These tests read the toy v1.0.3 campaign and hold the six
comparisons to the numbers in its own tables.

The check must be able to fail, so each one is also run against an export that
is deliberately wrong.
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from binder_review import export
from binder_review.readers import bindcraft2


@pytest.fixture(scope="module")
def toyc_run(toy_dir):
    return bindcraft2.read_run(toy_dir / "bindcraft2_v103" / "toyc", "toyc")


def exported(run, **wrong):
    """What the export would hold for this run, or a wrong version of it."""
    rows = [{"run": run.run_id, "status": d.status, "rank": d.rank} for d in run.designs]
    if wrong.get("drop_accepted"):
        rows = [r for r in rows if r["status"] != "accepted"][:] + \
               [r for r in rows if r["status"] == "accepted"][:-wrong["drop_accepted"]]
    if wrong.get("drop_rank"):
        for r in rows:
            if r["rank"] is not None:
                r["rank"] = None
                break
    return rows


def test_a_bindcraft2_run_is_compared_at_all(toyc_run):
    rows = export._counts_bindcraft2(toyc_run, exported(toyc_run))
    assert set(rows) == {"accepted designs", "rejected designs", "sequence rows",
                         "trajectory rows", "families", "ranked designs"}
    # Every quantity is a real number on both sides: a check that reads None for
    # everything would "pass" while comparing nothing.
    for name, (from_files, from_export, _) in rows.items():
        assert from_files is not None and from_export is not None, name


def test_the_six_counts_are_the_campaign_s_own_numbers(toyc_run, toy_dir):
    root = toy_dir / "bindcraft2_v103" / "toyc"
    with open(root / "2_Refolded" / "!_Refolded.csv", newline="") as handle:
        refolded = list(csv.DictReader(handle))
    passed = [r for r in refolded if (r.get("outcome") or "").strip() == "passed"]
    rejected = [r for r in refolded
                if (r.get("outcome") or "").strip() not in ("", "passed")]
    with open(root / "3_Ranked" / "!_Ranked.csv", newline="") as handle:
        ranked = list(csv.DictReader(handle))

    rows = export._counts_bindcraft2(toyc_run, exported(toyc_run))
    assert rows["accepted designs"][0] == len(passed)
    assert rows["rejected designs"][0] == len(rejected)
    assert rows["sequence rows"][0] == len(refolded)
    assert rows["ranked designs"][0] == len(ranked)
    assert rows["families"][0] == len({r["hash"] for r in passed})
    assert all(a == b for a, b, _ in rows.values()), rows


def test_an_export_that_lost_a_design_is_caught(toyc_run):
    rows = export._counts_bindcraft2(toyc_run, exported(toyc_run, drop_accepted=1))
    files, sent, _ = rows["accepted designs"]
    assert files == sent + 1 and files != sent

def test_an_export_that_lost_a_rank_is_caught(toyc_run):
    rows = export._counts_bindcraft2(toyc_run, exported(toyc_run, drop_rank=True))
    files, sent, _ = rows["ranked designs"]
    assert files == sent + 1


def test_a_table_that_is_not_there_reads_as_unknown_on_both_sides(toyc_run, tmp_path):
    """A campaign missing a table is not a mismatch: nothing to compare is not a disagreement."""
    root = tmp_path / "toyc"
    shutil.copytree(Path(toyc_run.source_dir), root)
    (root / "3_Ranked" / "!_Ranked.csv").unlink()
    stripped = bindcraft2.read_run(root, "toyc")
    rows = export._counts_bindcraft2(stripped, exported(stripped))
    assert rows["ranked designs"][0] is None
    assert rows["accepted designs"][0] is not None          # the rest is still compared


# ------------------------------------------------------------------ modality words

@pytest.mark.parametrize("recorded, expected", [
    ("binder", "de novo"),              # the de novo settings word; no key matches, the default answers
    ("peptide", "peptide"),
    ("cyclic_peptide", "cyclic peptide"),   # must not read as "peptide": the key order decides it
    ("VHH", "VHH"),
    ("scFv", "scFv"),
    ("Fab", "Fab"),
    ("ARP", "ARP"),
    ("large_binder", "large binder"),
    ("homo_oligomer", "homo-oligomer"),
    ("multidomain", "multidomain"),
])
def test_every_modality_bindcraft_ships_is_named(recorded, expected):
    got, _ = bindcraft2._modality_of(None, set(), 1, recorded)
    assert got == expected


def test_a_homo_oligomer_is_not_mistaken_for_an_scfv():
    """Its binder is several copies of one chain, which is what the scFv heuristic looks for.

    Without a word for it the metadata fell through to the columns, where two or
    more binder chains means scFv, and the campaign was reported as an antibody
    fragment it has nothing to do with."""
    got, source = bindcraft2._modality_of(None, set(), 3, "homo_oligomer")
    assert (got, source) == ("homo-oligomer", "campaign_metadata")
    # The heuristic is still there for a campaign that records no modality at all.
    assert bindcraft2._modality_of(None, set(), 3, None)[0] == "scFv"


# ------------------------------------------------------- fold distance on short binders

from types import SimpleNamespace                                   # noqa: E402

from binder_review import families                                  # noqa: E402


def run_of(lengths, modality=None):
    designs = [SimpleNamespace(length=n) for n in lengths]
    return SimpleNamespace(annotations={"modality": modality} if modality else {},
                           accepted=lambda: designs)


def test_fold_is_reported_for_binders_long_enough_to_mean_something():
    assert families.fold_unreliable(run_of([60, 85, 120], "de novo")) is None
    assert families.fold_unreliable(run_of([30, 30, 30])) is None    # exactly at the floor


def test_fold_is_not_reported_for_a_peptide_campaign():
    why = families.fold_unreliable(run_of([12, 15, 18, 22, 25], "peptide"))
    assert why and "12 to 25 residues" in why and "calibrated on protein domains" in why


def test_the_median_decides_so_one_long_design_does_not_turn_it_back_on():
    assert families.fold_unreliable(run_of([12, 14, 16, 18, 200])) is not None
    assert families.fold_unreliable(run_of([12, 40, 60, 80, 200])) is None


def test_a_cyclic_campaign_is_refused_on_its_own_grounds():
    """Not length: a closed backbone compared as an open chain is the wrong molecule."""
    why = families.fold_unreliable(run_of([40, 45, 50], "cyclic peptide"))
    assert why == families.FOLD_CYCLIC_WHY


def test_the_reason_reaches_the_block_and_the_site_axis_survives():
    block = {"clusters": {"fold": [1], "both": [1], "site": [2]}, "counts": {"fold": 1, "site": 2}}
    families._switch_fold_off(block, "because")
    assert block["fold"] == {"status": "off", "why": "because"}
    assert block["basis_default"] == families.SITE_BASIS
    assert "fold" not in block["clusters"] and "site" in block["clusters"]
