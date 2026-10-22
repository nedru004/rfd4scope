"""Campaign notifications over ntfy.

A campaign runs for hours, and the point of this is to be told how it went without
keeping a tunnel open to look. ntfy was chosen because it needs no account and is
one of the few outbound hosts this machine's firewall permits: SMTP is blocked in
every direction, so email would mean a third-party HTTP API and an account to go
with it.

A topic name is the only thing standing between a campaign's progress and anyone
who guesses it, so topics are generated long and random rather than chosen.
"""

from __future__ import annotations

import csv
import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path

from .tables import is_passed

DEFAULT_SERVER = "https://ntfy.sh"
TIMEOUT_SECONDS = 15

# What a campaign can be told about. The halfway mark is deliberately defined as
# whichever of the two finishing conditions is further along: a campaign stops when
# it has enough designs OR has spent its attempts, and which one binds is not known
# in advance.
EVENTS = ("running", "first", "halfway", "done")


def new_topic(prefix: str = "bindcraft") -> str:
    """A topic nobody will guess. Anyone who knows it can read and post to it."""
    return f"{prefix}-{secrets.token_hex(8)}"


def send(topic: str, title: str, message: str, *, server: str = "",
         priority: str = "default", tags: str = "") -> bool:
    """Post one notification. Never raises: a campaign must not fail over this."""
    topic = (topic or "").strip()
    if not topic:
        return False
    base = (server or os.environ.get("BC2SERVE_NTFY_SERVER") or DEFAULT_SERVER).rstrip("/")
    request = urllib.request.Request(
        f"{base}/{topic}",
        data=message.encode("utf-8"),
        method="POST",
        headers={
            "Title": title.encode("ascii", "replace").decode(),
            "Priority": priority,
            **({"Tags": tags} if tags else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError) as problem:
        print(f"bc2serve: notification to {topic} failed: {problem}", flush=True)
        return False


# ---------------------------------------------------------------- reading progress

def _rows(path: Path) -> int:
    try:
        with open(path) as table:
            return max(0, sum(1 for _ in table) - 1)
    except OSError:
        return 0


def _passed(path: Path) -> int:
    """Candidates that passed the campaign's filters, by the one definition of it."""
    try:
        with open(path, newline="") as table:
            return sum(1 for row in csv.DictReader(table) if is_passed(row))
    except OSError:
        return 0


def _rejected(path: Path) -> int:
    """Candidates that were scored and did not pass. A blank outcome is not yet scored."""
    try:
        with open(path, newline="") as table:
            return sum(1 for row in csv.DictReader(table)
                       if not is_passed(row) and (row.get("outcome") or "").strip())
    except OSError:
        return 0


def progress(project_folder: str | Path) -> dict:
    """Where a campaign has got to, read from the tables it writes as it goes.

    Four quantities, each under its own name. `ranked` used to be called
    `accepted`, which was the word the rest of the program uses for `passed` --
    so a phone said "11 accepted" about a campaign the Results page called 23
    passed, and the tally beside it ("11 accepted, 318 rejected") mixed the
    ranked shortlist with a count taken from the refolded table, where the
    numbers that add up are passed + rejected.
    """
    folder = Path(project_folder)
    return {
        "ranked": _rows(folder / "3_Ranked" / "!_Ranked.csv"),
        "passed": _passed(folder / "2_Refolded" / "!_Refolded.csv"),
        "rejected": _rejected(folder / "2_Refolded" / "!_Refolded.csv"),
        "attempts": _rows(folder / "1_Trajectories" / "!_Trajectories.csv"),
    }


def best_design(project_folder: str | Path) -> dict | None:
    """The top-ranked design, for putting a number in the message."""
    ranked = Path(project_folder) / "3_Ranked" / "!_Ranked.csv"
    try:
        with open(ranked, newline="") as table:
            return next(iter(csv.DictReader(table)), None)
    except OSError:
        return None


def fraction_done(counts: dict, wanted: int, budget: int) -> float:
    """How far along a campaign is, as the nearer of its two finishing lines."""
    # Against the ranked shortlist, because that is what BindCraft's
    # final-designs target counts.
    by_designs = (counts["ranked"] / wanted) if wanted else 0.0
    by_attempts = (counts["attempts"] / budget) if budget else 0.0
    return max(by_designs, by_attempts)


# ---------------------------------------------------------------- the messages

def _spell(seconds: float | None) -> str:
    if not seconds or seconds < 0:
        return ""
    seconds = int(seconds)
    hours, minutes = seconds // 3600, (seconds % 3600) // 60
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def compose(event: str, job: dict, counts: dict) -> tuple[str, str, str, str]:
    """Returns (title, message, priority, tags) for one event."""
    name = job["name"]
    ran_for = _spell((job.get("finished_at") or job.get("_now", 0)) - (job.get("started_at") or 0))
    # passed + rejected is every refolded candidate, so the tally adds up; the
    # shortlist is said beside it rather than in place of it.
    tally = (f"{counts['passed']} passed ({counts['ranked']} ranked), "
             f"{counts['rejected']} rejected, {counts['attempts']} attempts")

    if event == "running":
        return (f"{name} started",
                f"Designing on GPU {job.get('gpus_assigned', '?')}.",
                "low", "rocket")

    if event == "first":
        best = best_design(job["project_folder"])
        extra = ""
        if best and best.get("i_pTM"):
            extra = f"\nTop design i_pTM {best['i_pTM']}, i_pDAE {best.get('i_pDAE', '?')}"
        return (f"{name}: first design on the shortlist",
                f"It is working. {tally} so far{(', ' + ran_for + ' in') if ran_for else ''}.{extra}",
                "default", "sparkles")

    if event == "halfway":
        return (f"{name} is about halfway",
                f"{tally}"
                f"{(', ' + ran_for + ' in') if ran_for else ''}."
                f"\nTarget is {job.get('wanted', '?')} designs or {job.get('budget', '?')} attempts.",
                "low", "hourglass")

    # done, in all its varieties
    state = job.get("state", "done")
    if state == "done":
        best = best_design(job["project_folder"])
        extra = ""
        if best and best.get("i_pTM"):
            extra = f"\nBest i_pTM {best['i_pTM']}, i_pDAE {best.get('i_pDAE', '?')}"
        if counts["passed"] == 0:
            return (f"{name} finished with nothing passed",
                    f"{tally}{(', ' + ran_for) if ran_for else ''}."
                    "\nNo design passed the filters. Loosening i_pTM, or allowing more "
                    "attempts, is the usual next step.",
                    "default", "warning")
        return (f"{name} finished: {counts['passed']} passed",
                f"{tally}{(', ' + ran_for) if ran_for else ''}.{extra}",
                "default", "white_check_mark")

    if state == "cancelled":
        return (f"{name} was stopped",
                f"{tally}{(', ' + ran_for) if ran_for else ''}. "
                "Designs that have already passed are kept.",
                "default", "octagonal_sign")

    reason = job.get("message") or "see the log"
    return (f"{name} failed",
            f"{reason}\n{tally}{(', ' + ran_for) if ran_for else ''}.",
            "high", "rotating_light")
