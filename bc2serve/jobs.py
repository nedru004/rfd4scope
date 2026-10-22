"""Job queue and GPU allocation for the BindCraft2 web service.

Campaigns are long (hours) and the machine is shared, so submissions go into a
queue rather than straight onto the cards. The scheduler hands out GPUs that are
both unassigned here and genuinely idle on the machine, which is what keeps a
campaign off a card cryoSPARC is already using.

State lives in SQLite so a server restart does not lose the queue: running jobs
are re-adopted by PID, and any that died while the server was down are recorded
as failed rather than left looking alive.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from . import notify, review, sge

# A card holding more than this much memory is treated as somebody else's, which
# is how campaigns stay off the GPUs cryoSPARC is working on.
FOREIGN_MEMORY_MIB = 1024
POLL_SECONDS = 5

# A review is CPU and memory hungry (several cores for tens of seconds to minutes),
# and the machine is shared, so only this many run together. It does not look at
# the GPUs at all: design campaigns keep the cards to themselves.
MAX_ANALYSES = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    owner         TEXT NOT NULL DEFAULT '',
    campaign_path TEXT NOT NULL,
    project_folder TEXT NOT NULL,
    gpus_wanted   INTEGER NOT NULL DEFAULT 1,
    gpus_assigned TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL DEFAULT 'queued',
    pid           INTEGER,
    exit_code     INTEGER,
    message       TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL
);
"""

# Added after the first databases existed, so they are applied one at a time to
# whatever is already on disk rather than assumed present.
MIGRATIONS = {
    "notify_topic": "ALTER TABLE jobs ADD COLUMN notify_topic TEXT NOT NULL DEFAULT ''",
    "notified": "ALTER TABLE jobs ADD COLUMN notified TEXT NOT NULL DEFAULT ''",
    "wanted": "ALTER TABLE jobs ADD COLUMN wanted INTEGER NOT NULL DEFAULT 0",
    "budget": "ALTER TABLE jobs ADD COLUMN budget INTEGER NOT NULL DEFAULT 0",
    # What the job is: a design campaign, or the analysis behind a review. Every row
    # that predates the column is a campaign, which is what the default says.
    "kind": "ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'design'",
    # Which scheduler started the job. Rows written before this column were
    # started as children of this service, which is what the default says; on a
    # cluster the "pid" is an SGE job id instead, and the two are told apart by
    # this rather than by hoping the numbers never collide.
    "sched": "ALTER TABLE jobs ADD COLUMN sched TEXT NOT NULL DEFAULT 'local'",
    "gpu_type": "ALTER TABLE jobs ADD COLUMN gpu_type TEXT NOT NULL DEFAULT ''",
    "queue": "ALTER TABLE jobs ADD COLUMN queue TEXT NOT NULL DEFAULT ''",
    # What the campaign is for, not who started it. Binderscope runs one instance
    # per person, under that person's own account, so every row here is already
    # theirs and a name on it says nothing. What a list of your own campaigns
    # needs is the project they belong to. The old 'owner' column is left alone
    # rather than renamed: rows written before this change hold a person's name,
    # and relabelling those as projects would be a quiet lie about old data.
    "project": "ALTER TABLE jobs ADD COLUMN project TEXT NOT NULL DEFAULT ''",
    # When the row was put out of the way. Archiving hides finished work from the
    # lists; it deletes nothing and touches no file on disk. Kept as a timestamp
    # rather than a flag so "archived last March" is answerable.
    "archived_at": "ALTER TABLE jobs ADD COLUMN archived_at REAL",
}

LIVE_STATES = ("queued", "running")


@dataclass
class Card:
    index: str
    name: str
    memory_total_mib: int
    memory_used_mib: int

    @property
    def foreign(self) -> bool:
        return self.memory_used_mib > FOREIGN_MEMORY_MIB


def read_cards() -> list[Card]:
    """Every GPU on the machine with its current memory use."""
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    cards = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            cards.append(Card(parts[0], parts[1], int(parts[2]), int(parts[3])))
        except ValueError:
            continue
    return cards


def _queue_name(asked) -> str:
    """The SGE queue to submit to: what the job asked for, else the configured one.

    There is deliberately no fallback. One site's own queue name used to be the
    default, shipped to everybody: on any other cluster it is a job
    that is refused or waits for a queue that does not exist. With nothing set,
    no `-q` is written and the scheduler uses the site's own default queue.
    Set `BC2SERVE_SGE_QUEUE` to choose one.
    """
    return str(asked or os.environ.get("BC2SERVE_SGE_QUEUE", "") or "")


def _queue_words(asked) -> str:
    """What the Jobs page says while the scheduler holds a campaign."""
    queue = _queue_name(asked)
    return f"waiting in the {queue} queue" if queue else "waiting in the queue"


class JobStore:
    """The queue, its scheduler, and the processes it has started."""

    def __init__(self, repo: Path, state_dir: Path, reserved: set[str] | None = None):
        self.repo = Path(repo)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.database = self.state_dir / "jobs.db"
        self.reserved = set(reserved or set())
        self._lock = threading.Lock()
        self._stop = threading.Event()
        with self._connect() as db:
            db.executescript(SCHEMA)
            have = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            for column, statement in MIGRATIONS.items():
                if column not in have:
                    db.execute(statement)
        self._recover()
        # Notifications go out on their own thread: ntfy is a network call with a
        # timeout, and the scheduler must not wait on it to hand out a GPU.
        self._notices: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._schedule_forever, daemon=True)
        self._thread.start()
        self._postman = threading.Thread(target=self._send_forever, daemon=True)
        self._postman.start()

    # ---------------------------------------------------------------- notifying

    def _announce(self, db: sqlite3.Connection, row, event: str) -> None:
        """Record that an event has been told, and hand it to the postman.

        Marking it told before it is sent is deliberate: a campaign that notifies
        twice is worse than one that misses a notice, and a failing ntfy would
        otherwise be retried on every scheduler tick for the life of the job.
        """
        topic = (row["notify_topic"] or "").strip()
        if not topic:
            return
        told = {part for part in (row["notified"] or "").split(",") if part}
        if event in told:
            return
        told.add(event)
        db.execute("UPDATE jobs SET notified=? WHERE id=?", (",".join(sorted(told)), row["id"]))
        job = dict(row)
        job["_now"] = time.time()
        if event == "done":  # the row still says running at the moment we decide
            job["state"] = job.get("state") or "done"
        self._notices.put((topic, event, job))

    def _send_forever(self) -> None:
        while not self._stop.is_set():
            try:
                topic, event, job = self._notices.get(timeout=2)
            except queue.Empty:
                continue
            try:
                if job.get("kind") == "analysis":
                    title, message, priority, tags = review.compose(event, job)
                else:
                    counts = notify.progress(job["project_folder"])
                    title, message, priority, tags = notify.compose(event, job, counts)
                notify.send(topic, title, message, priority=priority, tags=tags)
            except Exception as problem:  # a bad notice must not kill the postman
                print(f"bc2serve: could not send {event} for job {job.get('id')}: {problem}",
                      flush=True)

    # ---------------------------------------------------------------- storage

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _still_running(self, row: sqlite3.Row) -> bool:
        """Whether the work behind a row is still going.

        A batch job outlives this service by design, so the question is asked of
        the scheduler rather than of the process table — and a queued job that
        has not started yet is still 'running' to bc2serve, because from the
        page's point of view it has been handed over and not yet come back.
        """
        if not row["pid"]:
            return False
        if row["sched"] == "sge":
            return sge.state(str(row["pid"]))[0] != "gone"
        return _alive(row["pid"])

    def _recover(self) -> None:
        """After a restart, decide what became of jobs that were running."""
        with self._lock, self._connect() as db:
            for row in db.execute("SELECT * FROM jobs WHERE state = 'running'"):
                if self._still_running(row):
                    continue  # still going; the scheduler re-adopts and keeps watching it
                state, note = self._outcome(row, None)
                db.execute(
                    "UPDATE jobs SET state=?, message=?, finished_at=? WHERE id=?",
                    (state, note, time.time(), row["id"]),
                )
                if row["kind"] == "analysis":
                    review.on_finish(row, state, note)

    def _outcome(self, row: sqlite3.Row, code: int | None) -> tuple[str, str]:
        """What to record for a job whose process has gone.

        A campaign the service started itself hands back an exit status. One that
        outlived a restart does not: it was reparented away, so waitpid has nothing
        to say about it and the campaign's own output is the only evidence. BindCraft
        writes summary.csv when a campaign ends, so that file is the finish line.
        """
        if row["kind"] == "analysis":
            # A review has no summary.csv; it leaves its exit status in a file instead.
            return review.outcome(row, code)
        if code == 0:
            return "done", ""
        if code == 2:
            return "failed", "campaign refused: the reasons are at the top of the log"
        if code is not None:
            return "failed", f"the campaign exited {code}"
        if (Path(row["project_folder"]) / "summary.csv").is_file():
            return "done", "finished while the service was restarting"
        return "failed", "stopped when the service restarted; check the log and submit it again to carry on"

    # ---------------------------------------------------------------- queries

    def list_jobs(self, limit: int = 100, archived: bool = False) -> list[dict]:
        """The job list. Archived rows are left out unless asked for.

        The limit is applied after the filter, not before, or a long tail of
        archived rows would push live work off the end of the list — which is
        the thing archiving exists to prevent.
        """
        where = "" if archived else " WHERE archived_at IS NULL"
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM jobs{where} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def projects(self) -> list[dict]:
        """Every project with a count, for the filter row. Unfiled is a real
        group here rather than an absence, because it is one you file from."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT COALESCE(NULLIF(project, ''), '') AS name, kind,"
                " COUNT(*) AS n FROM jobs WHERE archived_at IS NULL"
                " GROUP BY name, kind").fetchall()
        out: dict[str, dict] = {}
        for row in rows:
            entry = out.setdefault(row["name"], {"name": row["name"], "campaigns": 0, "reviews": 0})
            entry["reviews" if row["kind"] == "analysis" else "campaigns"] += row["n"]
        return sorted(out.values(), key=lambda p: (p["name"] == "", p["name"].lower()))

    def get(self, job_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def campaigns_by_folder(self, project: str | None = None,
                            archived: str = "live") -> list[dict]:
        """One entry per campaign *folder*, newest first, for the pages that
        work across a project rather than down a list of jobs.

        Why not `list_jobs`: that one answers "what has this service been
        asked to do", caps at 100 rows and is ordered by job. A folder is the
        thing Results and Storage are about, and rows are not folders. A
        campaign resubmitted under the same name reuses its folder
        (`campaign.build` mkdirs with exist_ok and overwrites campaign.json),
        so several design rows can share one `project_folder` — and because
        `set_project` only touches the ids it was given, they can even carry
        different labels. The newest design row wins, which is the same rule
        `find` uses.

        Keyed by the resolved path, not the name: two results roots can hold a
        campaign of the same name, and `Reviews.campaigns` already silently
        dedupes those by name. Displaying the basename is fine; deciding by it
        is not.

        `archived` is three-valued on purpose — "live", "only" and "all".
        `list_jobs(archived=True)` means "include archived" and so cannot
        answer "show me what I archived", which is exactly what a Storage page
        offering Restore has to ask.
        """
        where = {"live": " WHERE archived_at IS NULL",
                 "only": " WHERE archived_at IS NOT NULL",
                 "all": ""}[archived]
        with self._connect() as db:
            rows = [dict(r) for r in db.execute(
                f"SELECT * FROM jobs{where} ORDER BY id DESC")]

        out: dict[str, dict] = {}
        for row in rows:
            folder = (row["project_folder"] or "").strip()
            if not folder:
                continue
            # resolve() on a path that is gone still normalises it, which is
            # what we want: a deleted campaign must still group with its own
            # review rather than appearing twice.
            try:
                key = str(Path(folder).resolve())
            except OSError:
                key = folder
            entry = out.get(key)
            if entry is None:
                entry = out[key] = {"folder": key, "name": Path(key).name,
                                    "project": "", "job_id": None, "state": "",
                                    "created_at": None, "finished_at": None,
                                    "archived": False,
                                    "design_ids": [], "review_ids": []}
            if row["kind"] == "analysis":
                entry["review_ids"].append(row["id"])
                continue
            entry["design_ids"].append(row["id"])
            if entry["job_id"] is None:          # rows arrive newest first
                entry.update(project=row["project"] or "", job_id=row["id"],
                             state=row["state"], created_at=row["created_at"],
                             finished_at=row["finished_at"],
                             archived=row["archived_at"] is not None)

        entries = [e for e in out.values() if e["design_ids"]]
        if project is not None:
            entries = [e for e in entries if e["project"] == project]
        entries.sort(key=lambda e: e["job_id"], reverse=True)
        return entries

    def find(self, kind: str, project_folder: str | Path, live: bool = False) -> dict | None:
        """The newest job of this kind for a campaign folder."""
        wanted = Path(project_folder).resolve()
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE kind = ? ORDER BY id DESC", (kind,)).fetchall()
        for row in rows:
            if live and row["state"] not in LIVE_STATES:
                continue
            if Path(row["project_folder"]).resolve() == wanted:
                return dict(row)
        return None

    def gpu_status(self) -> list[dict]:
        """Each card, what the machine says about it, and which job holds it here."""
        held: dict[str, int] = {}
        with self._connect() as db:
            for row in db.execute("SELECT id, gpus_assigned FROM jobs WHERE state='running'"):
                for index in filter(None, row["gpus_assigned"].split(",")):
                    held[index] = row["id"]
        status = []
        for card in read_cards():
            status.append({
                "index": card.index,
                "name": card.name,
                "memory_total_mib": card.memory_total_mib,
                "memory_used_mib": card.memory_used_mib,
                "reserved": card.index in self.reserved,
                "held_by_job": held.get(card.index),
                "busy_elsewhere": card.foreign and card.index not in held,
            })
        return status

    # ---------------------------------------------------------------- actions

    def submit(self, name: str, owner: str, campaign_path: Path,
               project_folder: Path, gpus_wanted: int, notify_topic: str = "",
               wanted: int = 0, budget: int = 0, kind: str = "design",
               gpu_type: str = "", queue: str = "", project: str = "") -> int:
        # A campaign always needs a card; an analysis asks for none, and the zero is
        # what keeps it out of the allocator rather than a flag the allocator checks.
        gpus = 0 if kind == "analysis" else max(1, int(gpus_wanted))
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "INSERT INTO jobs (name, owner, campaign_path, project_folder, gpus_wanted,"
                " notify_topic, wanted, budget, kind, gpu_type, queue, project,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, owner, str(campaign_path), str(project_folder), gpus,
                 notify_topic.strip(), int(wanted), int(budget), kind,
                 gpu_type.strip(), queue.strip(), project.strip(), time.time()),
            )
            return int(cursor.lastrowid)

    def set_project(self, job_ids: list[int], project: str) -> int:
        """Move campaigns into a project. Returns how many rows changed.

        A project is a label and nothing more: no file moves, no results are
        rewritten, and a running campaign is not disturbed. That is the whole
        reason this is safe to do in bulk from a list.
        """
        label = (project or "").strip()[:64]
        ids = [int(i) for i in job_ids]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        with self._lock, self._connect() as db:
            cur = db.execute(
                f"UPDATE jobs SET project = ? WHERE id IN ({marks})", [label, *ids])
            changed = cur.rowcount
            # A review belongs to the campaign it is about, so it follows it.
            # Without this the Jobs page files the campaigns and leaves their
            # reviews behind under Unfiled — visibly wrong the first time
            # anyone files existing work, which is the day this gets used.
            # The link is the folder: a review's project_folder IS the
            # campaign's, which is also how the page pairs them already.
            folders = [r["project_folder"] for r in db.execute(
                f"SELECT project_folder FROM jobs WHERE id IN ({marks})", ids)]
            folders = [f for f in folders if f]
            if folders:
                fmarks = ",".join("?" * len(folders))
                cur = db.execute(
                    f"UPDATE jobs SET project = ? WHERE kind = 'analysis'"
                    f" AND project_folder IN ({fmarks})", [label, *folders])
                changed += cur.rowcount
            return changed

    def set_archived(self, job_ids: list[int], archived: bool) -> int:
        """Hide finished rows from the lists, or bring them back.

        Refuses anything still live: a queued or running campaign that vanished
        from the page would still be holding a card, and the only way to notice
        would be the GPU strip. Archive is for work that has stopped.
        """
        ids = [int(i) for i in job_ids]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        when = time.time() if archived else None
        with self._lock, self._connect() as db:
            cur = db.execute(
                f"UPDATE jobs SET archived_at = ? WHERE id IN ({marks})"
                f" AND state NOT IN ({','.join('?' * len(LIVE_STATES))})",
                [when, *ids, *LIVE_STATES])
            return cur.rowcount

    def cancel(self, job_id: int, quiet: bool = False) -> bool:
        """Stop a running job, or drop a queued one.

        Quiet is for a review being replaced by a newer one, which is not news.
        """
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None or row["state"] not in LIVE_STATES:
                return False
            if row["state"] == "queued":
                # A queued job used to mean "not handed to anything yet", so
                # dropping the row was the whole of cancelling it. On a cluster
                # that is false: a campaign waits in SGE's queue with a job id,
                # and forgetting the row here left it to start on a GPU minutes
                # later with nothing watching it. Found exactly that way.
                if row["pid"] and row["sched"] == "sge":
                    sge.cancel(str(row["pid"]))
                db.execute("UPDATE jobs SET state='cancelled', finished_at=? WHERE id=?",
                           (time.time(), job_id))
                if row["kind"] == "analysis":
                    review.on_finish(row, "cancelled")
                return True
            if row["pid"] and row["sched"] == "sge":
                # qdel reaches the job wherever the scheduler put it. Signalling
                # a pid here would at best hit nothing and at worst hit an
                # unrelated local process that reused the number.
                sge.cancel(str(row["pid"]))
            elif row["pid"]:
                # The campaign is its own process group, so this reaches any worker
                # it started rather than only the launcher.
                try:
                    os.killpg(os.getpgid(row["pid"]), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            finished = time.time()
            db.execute("UPDATE jobs SET state='cancelled', finished_at=? WHERE id=?",
                       (finished, job_id))
            settled = dict(row)
            settled.update(state="cancelled", finished_at=finished)
            if row["kind"] == "analysis":
                review.on_finish(row, "cancelled")
            if not quiet:
                self._announce(db, settled, "done")
            return True

    def stop_and_wait(self, job_id: int, seconds: float = 15) -> None:
        """Cancel a job and do not return until its whole process group is gone.

        A review being replaced has its folder deleted straight afterwards, and
        a build still writing into it would leave the folder half-removed.
        """
        row = self.get(job_id)
        if row is None:
            return
        self.cancel(job_id, quiet=True)
        pid = row["pid"]
        if not pid:
            return
        deadline = time.time() + seconds
        while time.time() < deadline:
            _exit_code(pid)  # nothing else reaps a job that is no longer 'running'
            if _group_gone(pid):
                return
            time.sleep(0.2)
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        time.sleep(1)
        _exit_code(pid)

    def log_path(self, job: dict) -> Path:
        if job.get("kind") == "analysis":
            return review.log_file(Path(job["project_folder"]))
        return Path(job["project_folder"]) / "campaign.log"

    def stop(self) -> None:
        self._stop.set()

    # ---------------------------------------------------------------- scheduler

    def _free_cards(self, db: sqlite3.Connection) -> list[str]:
        """Cards this service may use that nothing is using right now."""
        taken: set[str] = set()
        for row in db.execute("SELECT gpus_assigned FROM jobs WHERE state='running'"):
            taken.update(filter(None, row["gpus_assigned"].split(",")))
        free = []
        for card in read_cards():
            if card.index in self.reserved or card.index in taken or card.foreign:
                continue
            free.append(card.index)
        return free

    def _schedule_forever(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            # Each step guards itself: reviews and campaigns are scheduled
            # independently, so one that cannot be started (nvidia-smi hanging, a
            # review whose folder vanished) must not hold up the other kind.
            # _follow_cluster before _reap: a campaign SGE has just started must
            # be promoted out of 'queued' before anything asks whether it ended,
            # or a job that was handed over seconds ago reads as one that
            # vanished without running.
            for step in (self._follow_cluster, self._reap, self._watch_progress,
                         self._start_analyses, self._start_what_fits):
                try:
                    step()
                except Exception as problem:  # a scheduler that dies stops every future job
                    print(f"bc2serve: scheduler error in {step.__name__}: {problem}", flush=True)

    def _reap(self) -> None:
        """Record jobs whose process has ended."""
        with self._lock, self._connect() as db:
            for row in db.execute("SELECT * FROM jobs WHERE state='running'"):
                pid = row["pid"]
                if self._still_running(row):
                    continue
                if row["sched"] == "sge":
                    code, why = sge.finished(str(pid)) if pid else (None, "")
                    if code is None and pid:
                        continue   # accounting lags the queue; ask again next tick
                    if why:
                        db.execute("UPDATE jobs SET message=? WHERE id=?", (why, row["id"]))
                else:
                    code = _exit_code(pid) if pid else None
                if code is None and row["kind"] == "analysis":
                    code = review.read_exit(row)  # a review that outlived a restart
                state, note = self._outcome(row, code)
                finished = time.time()
                db.execute(
                    "UPDATE jobs SET state=?, exit_code=?, message=?, finished_at=? WHERE id=?",
                    (state, code, note, finished, row["id"]),
                )
                settled = dict(row)
                settled.update(state=state, message=note, finished_at=finished)
                if row["kind"] == "analysis":
                    review.on_finish(row, state, note)
                self._announce(db, settled, "done")

    def _watch_progress(self) -> None:
        """Milestones a running campaign passes, told once each.

        The tables a campaign writes as it goes are the only progress signal, so
        these are read rather than reported: nothing is injected into BindCraft.
        """
        with self._lock, self._connect() as db:
            for row in db.execute(
                    "SELECT * FROM jobs WHERE state='running' AND kind='design'"
                    " AND notify_topic != ''"):
                told = {part for part in (row["notified"] or "").split(",") if part}
                if {"first", "halfway"} <= told:
                    continue
                counts = notify.progress(row["project_folder"])
                if counts["ranked"] >= 1 and "first" not in told:
                    self._announce(db, row, "first")
                    told.add("first")
                if "halfway" not in told:
                    done = notify.fraction_done(counts, row["wanted"], row["budget"])
                    # Only worth saying while there is still a campaign left to run.
                    if 0.5 <= done < 1.0:
                        self._announce(db, row, "halfway")

    def _start_analyses(self) -> None:
        """Start queued reviews, up to the cap, without consulting the cards.

        This is deliberately a separate pass and not a branch of the one below. That
        pass is strict first-in-first-out on GPUs, and a review sitting in its
        queue would either wait behind a campaign it has nothing to compete with
        or, worse, be the thing a campaign waits behind.
        """
        with self._lock, self._connect() as db:
            running = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE state='running' AND kind='analysis'"
            ).fetchone()[0]
            if running >= MAX_ANALYSES:
                return
            queued = db.execute(
                "SELECT * FROM jobs WHERE state='queued' AND kind='analysis' ORDER BY id ASC"
            ).fetchall()
            for row in queued[:MAX_ANALYSES - running]:
                spec = review.launch_spec(self.repo, row)
                started = self._spawn(row, *spec, "") if spec else None
                if started is None:
                    db.execute(
                        "UPDATE jobs SET state='failed', message=?, finished_at=? WHERE id=?",
                        ("could not be started; see the server log", time.time(), row["id"]))
                    review.on_finish(row, "failed", "The build could not be started.")
                    continue
                db.execute("UPDATE jobs SET state='running', pid=?, started_at=? WHERE id=?",
                           (started, time.time(), row["id"]))
                review.on_start(row)

    def _start_what_fits(self) -> None:
        with self._lock, self._connect() as db:
            queued = db.execute(
                "SELECT * FROM jobs WHERE state='queued' AND kind='design' ORDER BY id ASC"
            ).fetchall()
            if not queued:
                return
            if cluster_mode():
                return self._hand_all_to_cluster(db, queued)
            for row in queued:
                free = self._free_cards(db)
                wanted = max(1, int(row["gpus_wanted"]))
                if len(free) < wanted:
                    # Strict queueing: a big job waits rather than being overtaken,
                    # so a campaign asking for several cards is not starved by a
                    # steady trickle of single-card ones.
                    return
                assigned = free[:wanted]
                started = self._launch(row, assigned)
                if started is None:
                    db.execute(
                        "UPDATE jobs SET state='failed', message=?, finished_at=? WHERE id=?",
                        ("could not be started; see the server log", time.time(), row["id"]),
                    )
                    continue
                db.execute(
                    "UPDATE jobs SET state='running', pid=?, gpus_assigned=?, started_at=?"
                    " WHERE id=?",
                    (started, ",".join(assigned), time.time(), row["id"]),
                )

    def _hand_all_to_cluster(self, db, queued) -> None:
        """Submit every waiting campaign and let the scheduler decide.

        No gating on free cards, and no card assignment. SGE already allocates,
        and a second allocator running beside it would hand the same GPU to two
        jobs — so on a cluster the right behaviour is to stop choosing, not to
        choose differently. Queue position stops being this service's business
        too: a campaign that cannot run yet waits in SGE's queue, where the rest
        of the cluster's work is weighed against it.
        """
        for row in queued:
            if row["pid"]:
                continue        # already handed over, just not started yet
            job_id, why = self._submit_to_cluster(row)
            if job_id is None:
                db.execute(
                    "UPDATE jobs SET state='failed', message=?, finished_at=? WHERE id=?",
                    (why or "the scheduler would not take it", time.time(), row["id"]))
                continue
            # Still 'queued', because it is: handing a campaign to SGE is not
            # starting it. Recording the job id and leaving the state alone is
            # what lets the page say "waiting in the queue" truthfully, and what
            # stops the next tick submitting it a second time.
            db.execute(
                "UPDATE jobs SET pid=?, sched='sge', message=? WHERE id=?",
                (int(job_id),
                 _queue_words(row["queue"]),
                 row["id"]))

    def _follow_cluster(self) -> None:
        """Promote handed-over campaigns once the scheduler actually starts them."""
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE state='queued' AND sched='sge' AND pid IS NOT NULL"
            ).fetchall()
            for row in rows:
                where, why = sge.state(str(row["pid"]))
                if where == "running":
                    db.execute(
                        "UPDATE jobs SET state='running', started_at=?, message='' WHERE id=?",
                        (time.time(), row["id"]))
                elif where == "gone":
                    # Left the queue without ever running: rejected, deleted, or
                    # parked in an error state the scheduler will not clear.
                    code, note = sge.finished(str(row["pid"]))
                    if code is None and not why:
                        continue
                    db.execute(
                        "UPDATE jobs SET state=?, exit_code=?, message=?, finished_at=? WHERE id=?",
                        ("cancelled" if code is None else ("done" if code == 0 else "failed"),
                         code, why or note or "left the queue without starting",
                         time.time(), row["id"]))

    def _submit_to_cluster(self, row: sqlite3.Row) -> tuple[str | None, str]:
        project = Path(row["project_folder"])
        try:
            project.mkdir(parents=True, exist_ok=True)
        except OSError as problem:
            return None, str(problem)
        home = Path(os.environ.get("HOME", "~")).expanduser()
        script = sge.job_script(
            name=f"bc2_{row['id']}",
            campaign=str(row["campaign_path"]),
            project=str(project),
            log=str(project / "campaign.log"),
            sif=os.environ.get("BC2SERVE_SGE_SIF", str(home / "containers/bindcraft2-cuda12.sif")),
            params=os.environ.get("BC2SERVE_SGE_PARAMS", str(home / "bc2-test/af2params")),
            cache=str(project / "xla_cache"),
            queue=_queue_name(row["queue"]),
            slots=int(os.environ.get("BC2SERVE_SGE_SLOTS", "8")),
            gpu_flag=row["gpu_type"] or None,
            gpus=max(1, int(row["gpus_wanted"] or 1)),
        )
        path = project / "submit.job"
        try:
            path.write_text(script, encoding="utf-8")
        except OSError as problem:
            return None, str(problem)
        return sge.submit(str(path))

    def _launch(self, row: sqlite3.Row, cards: list[str]) -> int | None:
        project = Path(row["project_folder"])
        project.mkdir(parents=True, exist_ok=True)
        command = [str(self.repo / ".venv" / "bin" / "bindcraft"), "design",
                   str(row["campaign_path"])]
        return self._spawn(row, command, project / "campaign.log",
                           f" on GPU {','.join(cards)}", {"CUDA_VISIBLE_DEVICES": ",".join(cards)})

    def _spawn(self, row: sqlite3.Row, command: list[str], log: Path, where: str,
               extra_environment: dict | None = None) -> int | None:
        """Start a job as a process of its own and return its pid.

        Its own session, so that a service restart does not take the job down with
        it, and so cancelling can signal the whole group rather than a launcher.
        """
        environment = {**os.environ, "PYTHONUNBUFFERED": "1", **(extra_environment or {})}
        try:
            with open(log, "ab") as handle:
                handle.write(
                    f"\n=== job {row['id']} starting{where} "
                    f"at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode()
                )
                handle.flush()
                process = subprocess.Popen(
                    command, cwd=self.repo, env=environment, stdout=handle,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            return process.pid
        except OSError as problem:
            print(f"bc2serve: could not launch job {row['id']}: {problem}", flush=True)
            return None


def cluster_mode() -> bool:
    """Whether campaigns go to a batch queue instead of this machine's cards.

    Opt out with BC2SERVE_BACKEND=local, which is what you want on a submit host
    that also happens to have GPUs you would rather drive directly.
    """
    choice = os.environ.get("BC2SERVE_BACKEND", "auto").lower()
    if choice == "local":
        return False
    if choice == "sge":
        return True
    return sge.available()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie is not alive for our purposes: it has exited and is awaiting a reap.
    try:
        status = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()
        return status[0] != "Z"
    except (OSError, IndexError):
        return True


def _group_gone(pgid: int) -> bool:
    """True when no process of the group is left (a zombie still counts as one)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def _exit_code(pid: int) -> int | None:
    """Reap a child we started, if it is ours and has finished."""
    try:
        reaped, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return None
    except OSError:
        return None
    if reaped == 0:
        return None
    return os.waitstatus_to_exitcode(status)
