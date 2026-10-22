"""The designs a project has accepted, listed across its campaigns.

A design row here is a refolded candidate that passed (`outcome == passed`), not
a ranked one. The ranked table is only the shortlist, one best sequence per
backbone; the review already counts every passed sequence, and the sibling
sequences are what a person orders as hedges. So passed is the population, and
whether a row also made the shortlist travels with it as `ranked` and `rank`.

The three joins each have a trap.

Ranked to refolded is on `(hash, Binder_Sequence)`, never on `design`. The same
molecule is `..._seq0` in one table and `..._candidateN` in the other. Both parts
of the key are needed: a hash alone matches every sibling of a ranked backbone,
and a sequence alone can collide across backbones.

Structures are matched on the exact file name. The campaign holds both
`x_candidate1.cif` and `x_candidate10.cif`, and a prefix match gives each the
other's file. Looking names up in the directory listing rather than testing
`<design>.cif` for existence also means a design id from a CSV is never used to
build a path, so one containing a separator finds nothing instead of something.

Family is the `hash`. In this codebase a backbone is a trajectory and its hash
is the one key all three tables share, so grouping passed rows by hash needs no
review bundle. The structural fold cluster does need one, and is left out: the
review board recomputes clusters in the browser at a distance the person picks,
and a number computed here would contradict it.

The client takes its columns from the first row, so every row carries every key,
with None for what is absent. Numbers are sent as numbers because the client
sorts them, and a blank is None rather than 0.0, which would sort as the best
value of a lower-is-better metric.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path

from .tables import REFOLDED, RANKED, design_counts, is_passed, read_table

COMPLEXES = Path("2_Refolded") / "Complexes"
MONOMERS = Path("2_Refolded") / "BinderMonomer"

# Counts of things, which read better as 3 than 3.0 when the CSV spells them so.
INTEGER_COLUMNS = (
    "length", "Interface_Residues", "Binder_Net_Charge", "Binder_Free_Cysteines",
    "Binder_Disulfides", "Backbone_Clashes", "All_Atom_Clashes",
)
FLOAT_COLUMNS = (
    "i_pTM", "i_pDAE", "i_pAE", "pLDDT", "Interface_BuriedArea",
    "Binder_Mass_kDa", "Binder_pI",
)
TEXT_COLUMNS = ("design", "Binder_Sequence", "hash")

# Everything a row carries, and nothing else: refolded rows are 43 columns wide
# and most of them are not shown.
ROW_KEYS = (
    "design", "campaign", "campaign_path", "length", "Binder_Sequence", "hash",
    "family", "siblings", "ranked", "rank",
    "i_pTM", "i_pDAE", "i_pAE", "pLDDT", "Interface_Residues", "Interface_BuriedArea",
    "Binder_Mass_kDa", "Binder_pI", "Binder_Net_Charge", "Binder_Free_Cysteines",
    "Binder_Disulfides", "Backbone_Clashes", "All_Atom_Clashes",
    "structure", "monomer",
)


def _number(text: str | None, whole: bool = False):
    """A float (or an int, for counts), or None for a blank or anything unreadable.

    NaN and infinity are None as well: they are valid floats but not valid JSON,
    and a browser would refuse the whole response over one of them.
    """
    if text is None or not text.strip():
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    if whole and value.is_integer():
        return int(value)
    return value


def _names(directory: Path) -> set[str]:
    # The folder can vanish between the caller looking at it and this listing.
    try:
        with os.scandir(directory) as entries:
            return {entry.name for entry in entries}
    except OSError:
        return set()


def _ranks(folder: Path) -> dict[tuple[str, str], int]:
    """{(hash, sequence): rank} for the shortlist. The best rank wins a repeat."""
    ranks: dict[tuple[str, str], int] = {}
    for row in read_table(folder / RANKED):
        rank = _number(row.get("rank"), whole=True)
        if rank is None:
            continue
        key = (row.get("hash", ""), row.get("Binder_Sequence", ""))
        rank = int(rank)
        if key not in ranks or rank < ranks[key]:
            ranks[key] = rank
    return ranks


def campaign_designs(folder: Path | str, name: str | None = None) -> list[dict]:
    """Every passed design in one campaign folder, in the order BindCraft wrote them.

    A folder that is gone, or has no refolded table yet, has no designs.
    """
    folder = Path(folder)
    passed = [row for row in read_table(folder / REFOLDED) if is_passed(row)]
    if not passed:
        return []
    ranks = _ranks(folder)
    siblings = Counter(row.get("hash", "") for row in passed if row.get("hash"))
    complexes = _names(folder / COMPLEXES)
    monomers = _names(folder / MONOMERS)
    campaign = name if name is not None else folder.name

    designs = []
    for row in passed:
        design = row.get("design", "")
        digest = row.get("hash", "")
        rank = ranks.get((digest, row.get("Binder_Sequence", "")))
        entry: dict = {key: None for key in ROW_KEYS}
        for key in TEXT_COLUMNS:
            entry[key] = row.get(key, "")
        for key in INTEGER_COLUMNS:
            entry[key] = _number(row.get(key), whole=True)
        for key in FLOAT_COLUMNS:
            entry[key] = _number(row.get(key))
        entry.update(
            campaign=campaign,
            campaign_path=str(folder),
            # A passed row with no hash belongs to no family; grouping all such
            # rows together would invent one.
            family=digest or None,
            siblings=siblings[digest] if digest else 1,
            ranked=rank is not None,
            rank=rank,
            structure=(str(folder / COMPLEXES / f"{design}.cif")
                       if f"{design}.cif" in complexes else None),
            monomer=(str(folder / MONOMERS / f"{design}_monomer.cif")
                     if f"{design}_monomer.cif" in monomers else None),
        )
        designs.append(entry)
    return designs


def campaign_meta(folder: Path | str) -> dict:
    """What a campaign was asked to do, from its own `campaign_metadata.json`.

    This exists for one reason: nothing else on a cross-campaign page tells the
    reader which numbers may be compared. Two campaigns in a project can differ
    in target, in modality and in the filters they were judged by, and a table
    that lines their designs up without saying so invites exactly the wrong
    comparison. `i_pTM` means something between a mini-binder and a VHH only if
    you know which is which.

    `hotspots` carries the same weight the other way round. A campaign with
    none had no intended epitope, so its hotspot columns are *not measured*
    rather than zero — and a blank cell reads as "bad" unless the page is told
    to say otherwise. `hotspots_set` is that flag, kept apart from the value so
    "none were asked for" and "the file does not say" stay distinguishable.

    Tolerant throughout: a campaign may predate the file, or be mid-write, and
    neither is a reason to fail a page that is mostly about something else.
    """
    path = Path(folder) / "campaign_metadata.json"
    # A campaign whose metadata cannot be read is untested for the same reason
    # the reader gives it: nothing says what wrote it or what it was designing.
    blank = {"modality": None, "target": None, "chains": None,
             "hotspots": None, "hotspots_set": None, "version": None,
             "experimental": True,
             "experimental_why": ["there is no readable campaign_metadata.json"]}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return blank
    if not isinstance(data, dict):
        return blank
    settings = data.get("settings")
    settings = settings if isinstance(settings, dict) else {}
    targets = settings.get("targets")
    first = targets[0] if isinstance(targets, list) and targets and isinstance(targets[0], dict) else {}
    hotspots = first.get("hotspots")
    hotspots = str(hotspots).strip() if hotspots not in (None, "") else ""
    modality, version = _text(settings.get("modality")), _text(data.get("version"))
    why = _untested(version, modality)
    return {"modality": modality,
            "target": _text(first.get("name")),
            "chains": _text(first.get("chains")),
            "hotspots": hotspots or None,
            # None when the campaign has no target record at all: that is "the
            # file does not say", which is not the same as "none were asked for".
            "hotspots_set": bool(hotspots) if first else None,
            "version": version,
            "experimental": bool(why),
            "experimental_why": why}


# The authority for this is binder_review's own reader, which flags a campaign
# outside what it was verified against and says why. Imported rather than
# copied, because two definitions of "verified" would drift and the pages would
# then disagree about whether the same campaign is trustworthy. The fallback
# exists so this module keeps working — and keeps being testable — without the
# scientific stack installed; it is the same pair of values, and if it is ever
# reached with the package present the import is the one that wins.
_VERIFIED_VERSIONS = ((1, 0, 3),)
_VERIFIED_MODALITY = "binder"


def _verified() -> tuple:
    try:
        from binder_review.readers import bindcraft2 as reader
        return reader.VERIFIED_VERSIONS, reader.VERIFIED_SETTINGS_MODALITY
    except Exception:            # not installed, or an older copy without them
        return _VERIFIED_VERSIONS, _VERIFIED_MODALITY


def _version_numbers(text: str | None) -> tuple | None:
    """`"BindCraft 2 v1.0.3"` -> `(1, 0, 3)`, by the reader's own rule.

    The first version of this took the first three integers in the string and
    read that stamp as 2.1.0, because "BindCraft 2" contributes a digit before
    the version does. That is the drift this module tries to avoid everywhere
    else, so the reader's parser is used when it is importable and the fallback
    is its regex, not a second idea of what a version looks like.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        from binder_review.readers.bindcraft2 import _version_tuple
        return _version_tuple(raw)
    except Exception:
        pass
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", raw)
    return (int(found.group(1)), int(found.group(2)), int(found.group(3) or 0)) if found else None


def _untested(version: str | None, modality: str | None) -> list[str]:
    """Why this campaign sits outside what the reader was checked on, in words.

    Only the two reasons a campaign's own metadata can give. The reader has
    others that need its tables (a missing column, a trajectory file with no
    hash), and those belong to the reader: this is a hint on a listing page,
    not a second verdict. An empty list means nothing here says it is untested,
    which is weaker than the reader's "verified" and is worded that way in the UI.
    """
    versions, want = _verified()
    out = []
    numbers = _version_numbers(version)
    if numbers is None:
        out.append("its BindCraft 2 version is not recorded")
    elif numbers not in versions:
        out.append(f"it was written by BindCraft 2 v{'.'.join(map(str, numbers))}")
    if modality and modality.lower() != want:
        out.append(f"its modality is {modality}")
    return out


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def collect(folders: list[tuple[str, Path]]) -> dict:
    """The designs of several campaigns, and an entry for every campaign asked about.

    A folder the database still remembers but that is gone from disk is listed
    with `missing: True` and nothing in it. It is never dropped: a campaign that
    quietly vanishes from the page reads as though it had no designs, and the
    person needs to be told it is the folder that is gone.
    """
    designs: list[dict] = []
    campaigns: list[dict] = []
    for name, folder in folders:
        folder = Path(folder)
        try:
            present = folder.is_dir()
        except OSError:  # a permission error is not a reason to fail the page
            present = False
        entry = {"name": name, "path": str(folder), "missing": not present,
                 "counts": {"trajectories": 0, "refolded": 0, "passed": 0, "ranked": 0},
                 # A folder that is gone tells us nothing, which is not the
                 # same as telling us it is untested: experimental is None here.
                 "meta": {"modality": None, "target": None, "chains": None,
                          "hotspots": None, "hotspots_set": None, "version": None,
                          "experimental": None, "experimental_why": []}}
        if present:
            entry["counts"] = design_counts(folder)
            entry["meta"] = campaign_meta(folder)
            designs.extend(campaign_designs(folder, name))
        campaigns.append(entry)
    return {"designs": designs, "campaigns": campaigns}
