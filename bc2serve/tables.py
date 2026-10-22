"""Reading the CSV tables BindCraft writes, and what "passed" means.

Results and Review both report how many designs a campaign has, and they must
never report different numbers. Two copies of "count the rows where outcome is
passed" will drift, so this is the one place the tables are read and the one
definition of a passed design. It imports nothing outside the standard library,
which is deliberate: it can be tested, and used, without the scientific stack
that review.py pulls in.

Three things about the files shape the reader.

A running campaign appends to them while we read. The last line can be cut off
mid-row, and `csv.DictReader` reports a short row by filling the missing columns
with None rather than failing. Taken at face value that row would be counted as a
design and would blow up the first `.strip()` on it, so a short final row is
dropped: it is not a design yet. A short row anywhere else is a real, if odd,
row and is kept with the missing cells empty.

The files are wide (about 45 columns, hundreds of rows) and several callers want
the same one within a second of each other: the counts, the listing, the review
status. Parsing is cached per file on `(path, mtime_ns, size)`, which is the same
test review.py applies to structure files. A file that is rewritten with the
same size inside one timestamp tick would be missed; BindCraft only ever appends,
which changes the size.

Nothing here raises for a missing, unreadable or empty file. A campaign folder
that has not produced a table yet, or that someone moved while the page was open,
is an ordinary state that reads as zero rows, not an error.
"""

from __future__ import annotations

import csv
import threading
from collections import OrderedDict
from pathlib import Path

REFOLDED = Path("2_Refolded") / "!_Refolded.csv"
RANKED = Path("3_Ranked") / "!_Ranked.csv"
TRAJECTORIES = Path("1_Trajectories") / "!_Trajectories.csv"

# A campaign is three tables, so sixteen campaigns is forty-eight files. The
# bound is what keeps a long-running server that has listed many projects from
# holding every table it ever saw.
CACHED_CAMPAIGNS = 16
TABLES_PER_CAMPAIGN = 3

_cache: "OrderedDict[tuple[str, int, int], list[dict[str, str]]]" = OrderedDict()
_lock = threading.Lock()


def is_passed(row: dict) -> bool:
    """Whether the campaign accepted this refolded design.

    This is the only copy. review.py used to carry its own and now imports
    `design_counts` from here, which is the point: when the two existed
    separately, Results and Review could disagree about how many designs a
    campaign has, and that difference is exactly the kind nobody notices until
    a user asks which number is right.
    """
    return (row.get("outcome") or "").strip() == "passed"


def _parse(path: Path) -> list[dict[str, str]]:
    # utf-8-sig tolerates a spreadsheet's byte-order mark; errors="replace"
    # because a decode error is a ValueError, not an OSError, and nothing here
    # should raise over one stray byte in a free-text column.
    try:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as handle:
            raw = list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return []
    # DictReader files overflow cells under the key None; that is not a column.
    rows = [{(key or "").strip(): (value or "").strip()
             for key, value in row.items() if key is not None}
            for row in raw]
    if raw and any(value is None for value in raw[-1].values()):
        rows.pop()
    return rows


def _cached(path: Path) -> list[dict[str, str]]:
    """The parsed rows, shared with the cache. Callers must not mutate them."""
    try:
        info = path.stat()
    except OSError:
        return []
    # Stat before reading: if the file grows in between, the newer rows are filed
    # under the older stamp and simply re-read next time, never the other way round.
    key = (str(path), info.st_mtime_ns, info.st_size)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    rows = _parse(path)
    with _lock:
        # A changed file leaves its old stamp behind; drop it so it cannot be
        # mistaken for current if the file ever returns to that size and time.
        for stale in [k for k in _cache if k[0] == key[0] and k != key]:
            del _cache[stale]
        _cache[key] = rows
        while len(_cache) > CACHED_CAMPAIGNS * TABLES_PER_CAMPAIGN:
            _cache.popitem(last=False)
    return rows


def read_table(path: Path | str) -> list[dict[str, str]]:
    """Every row of a BindCraft table as {column: text}, or [] if there is none.

    Rows are copies, so a caller may edit them without corrupting the cache.
    """
    return [dict(row) for row in _cached(Path(path))]


def is_campaign_folder(folder: Path | str) -> bool:
    """Whether this folder is a BindCraft run.

    The file BindCraft writes into every campaign, or the one this service
    writes when it starts one, so a folder that merely carries a campaign's
    name is not mistaken for one. One definition, here with the other things
    every reader of a campaign needs, because the Review tab's listing and what
    an import accepts must not drift apart.
    """
    folder = Path(folder)
    return (folder / "campaign_metadata.json").is_file() or (folder / "campaign.json").is_file()


def design_counts(folder: Path | str) -> dict:
    """How far a campaign has got, from the tables BindCraft writes as it goes.

    Same four numbers, from the same tables, as review.py's version: every
    trajectory, every refolded candidate, those that passed the filters, and the
    final ranked shortlist. One difference, on purpose: a half-written last row
    of a table being appended to is not counted here, where review.py's reader
    counts it.
    """
    folder = Path(folder)
    refolded = _cached(folder / REFOLDED)
    return {
        "trajectories": len(_cached(folder / TRAJECTORIES)),
        "refolded": len(refolded),
        "passed": sum(1 for row in refolded if is_passed(row)),
        "ranked": len(_cached(folder / RANKED)),
    }


def clear_cache() -> None:
    with _lock:
        _cache.clear()
