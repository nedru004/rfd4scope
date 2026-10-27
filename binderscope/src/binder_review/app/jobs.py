"""Builds run as a separate ``binder-review import`` process: a crash or a cancel never takes the app down, and the page can show its log.

The progress comes from the log lines of ``pipeline.Log`` (``[ 12.3s] stage ...`` and ``stage done in 4.5 s``).
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from . import store

# (log name, what the page shows)
STAGES = [
    ("read runs", "Reading the run folders"),
    ("target frame", "Placing the target on the full-length structure"),
    ("poses and context checks", "Checking every design against the rest of the protein"),
    ("interface analysis", "Analysing the interfaces"),
    ("families and diversity", "Grouping designs into backbone families"),
    ("modes and near-miss records", "Finding binding modes and near-misses"),
    ("specificity panel", "Screening specificity"),
    ("export and validation", "Writing and validating the data"),
    ("bundle", "Writing the review pages"),
]
BUNDLE_ONLY_STAGES = [("bundle", "Writing the review pages")]
_START = re.compile(r"^\[\s*[\d.]+s\]\s+(?P<name>.+?) \.\.\.$")
_DONE = re.compile(r"^\[\s*[\d.]+s\]\s+(?P<name>.+?) done in (?P<sec>[\d.]+) s$")
_SUB = re.compile(r"^\[\s*[\d.]+s\]\s+(?P<detail>\s+.+)$")
_ERR = re.compile(r"^binder-review (?:import|build-bundle|inspect): (?P<msg>.+)$")


def launcher() -> list[str]:
    """How to invoke ``binder_review`` for a build, from ``$BINDERSCOPE_LAUNCHER``.

    Defaults to this interpreter, so a build is an ordinary child process and
    nothing about this app changes. It is a whole command rather than a prefix
    because on a cluster both halves have to move at once: the work belongs on a
    compute node *and* it needs an interpreter that is not this one. On the cluster this
    was built on, the server runs on a login node under the system Python — which can reach ``qrsh``
    but could never import numpy on that glibc — while the analysis runs in a
    container on a compute node:

        BINDERSCOPE_LAUNCHER="qrsh -q <your queue> -pe smp 9
                              apptainer exec -e ~/containers/python312.sif
                              ~/binderscope-venv/bin/python -m binder_review"

    Whatever is set has to pass stdout through line by line, because that is what
    drives the progress stages: ``qrsh`` does, ``qsub`` does not. Read on each
    build rather than at import, so it can be changed without a restart.
    """
    words = shlex.split(os.environ.get("BINDERSCOPE_LAUNCHER", ""))
    return words or [sys.executable, "-m", "binder_review"]


def run_id_ok(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,30}", s or ""))


def import_command(inputs: dict[str, Any], d: Path, name: str, no_cache: bool = False) -> list[str]:
    """The ``binder-review import`` command for the choices made in the import page."""
    cmd = launcher() + ["import"]
    runs = inputs["runs"]
    cmd += [f"{r['id']}={r['path']}" for r in runs]
    cmd += ["--target-pdb", inputs["target_pdb"]]
    if inputs.get("reference"):          # optional: without one the command takes the construct as the frame, and then these three options do not apply
        cmd += ["--reference-cif", inputs["reference"]]
        if inputs.get("reference_id"):
            cmd += ["--reference-id", inputs["reference_id"]]
        cmd += ["--reference-chain", inputs.get("reference_chain") or "A", "--partner-chains", inputs.get("partner_chains") or "auto"]
    for r in runs:
        if r.get("settings"):
            cmd += ["--settings", f"{r['id']}={r['settings']}"]
        if r.get("label"):
            cmd += ["--label", f"{r['id']}={r['label']}"]
    if inputs.get("uniprot"):
        cmd += ["--uniprot", inputs["uniprot"]]
    if inputs.get("profile"):
        cmd += ["--profile", inputs["profile"]]
    if inputs.get("online"):
        cmd.append("--online")
    if inputs.get("consurf"):
        cmd.append("--consurf")
    if inputs.get("placement") in ("whole", "core"):             # "interface" is the command's default
        cmd += ["--placement", inputs["placement"]]
    for c in inputs.get("cutoffs") or []:
        cmd += ["--cutoff", str(c)]
    if inputs.get("oracle"):
        cmd += ["--oracle", str(inputs["oracle"])]
    for item in inputs.get("chain_roles") or []:
        cmd += ["--chain-role", str(item)]
    cmd += ["--name", name, "--out", str(d / "work"), "--bundle", str(d / "bundle")]
    if no_cache:
        cmd.append("--no-cache")
    return cmd


def bundle_command(d: Path) -> list[str]:
    return launcher() + ["build-bundle", str(d / "work"), "--out", str(d / "bundle")]


class Job:
    def __init__(self, kind: str, slug: str, cmd: list[str], cwd: Path, stages: list[tuple[str, str]]):
        self.id = uuid.uuid4().hex[:12]
        self.kind, self.slug, self.cmd, self.cwd, self.stages = kind, slug, cmd, cwd, stages
        self.status = "running"
        self.lines: list[str] = []
        self.stage = 0                  # index of the stage that runs now (len(stages) when all are done)
        self.done: set[str] = set()
        self.detail = ""
        self.started = time.time()
        self.finished: Optional[float] = None
        self.error: Optional[str] = None
        self.error_kind: Optional[str] = None
        self.returncode: Optional[int] = None
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()

    def view(self, since: int = 0) -> dict[str, Any]:
        with self.lock:
            n = len(self.stages)
            cur = self.stages[self.stage][1] if self.stage < n else ""
            return {"id": self.id, "kind": self.kind, "slug": self.slug, "status": self.status, "stage": self.stage, "stages": [s[1] for s in self.stages],
                    "stage_label": cur, "detail": self.detail, "done": [i for i, s in enumerate(self.stages) if s[0] in self.done],
                    "progress": round(len(self.done) / n, 3) if n else 1.0, "elapsed": round((self.finished or time.time()) - self.started, 1),
                    "error": self.error, "error_kind": self.error_kind, "returncode": self.returncode,
                    "log_from": since, "log": self.lines[since:since + 500], "log_total": len(self.lines)}


class JobManager:
    def __init__(self, on_finish: Callable[[Job], None]):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.on_finish = on_finish

    def active(self) -> Optional[Job]:
        with self.lock:
            for j in self.jobs.values():
                if j.status == "running":
                    return j
        return None

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def start(self, kind: str, slug: str, cmd: list[str], cwd: Path, stages: list[tuple[str, str]], log_file: Path) -> Job:
        if self.active():
            raise RuntimeError("another build is running")
        job = Job(kind, slug, cmd, cwd, stages)
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        job.proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
                                    start_new_session=True, encoding="utf-8", errors="replace")
        with self.lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._pump, args=(job, log_file), daemon=True).start()
        return job

    def _pump(self, job: Job, log_file: Path) -> None:
        assert job.proc is not None and job.proc.stdout is not None
        try:
            fh = log_file.open("w", encoding="utf-8")
        except OSError:
            fh = None
        names = [s[0] for s in job.stages]
        for raw in job.proc.stdout:
            line = raw.rstrip("\n")
            with job.lock:
                job.lines.append(line)
                if len(job.lines) > 20000:
                    del job.lines[:5000]
                m = _START.match(line)
                if m and m["name"] in names:
                    job.stage, job.detail = names.index(m["name"]), ""
                else:
                    m = _DONE.match(line)
                    if m and m["name"] in names:
                        job.done.add(m["name"])
                        job.stage = min(names.index(m["name"]) + 1, len(names))
                        job.detail = ""
                    else:
                        m = _SUB.match(line)
                        if m:
                            job.detail = m["detail"].strip()
            if fh:
                fh.write(line + "\n")
                fh.flush()
        code = job.proc.wait()
        if fh:
            fh.close()
        with job.lock:
            job.returncode = code
            job.finished = time.time()
            if job.status == "cancelled":
                pass
            elif code == 0:
                job.status = "done"
                job.stage = len(job.stages)
            else:
                job.status = "failed"
                job.error, job.error_kind = _explain(job.lines, code)
        try:
            self.on_finish(job)
        except Exception:                                   # a bug in the callback must not hide the job's result
            pass

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or job.status != "running" or not job.proc:
            return False
        with job.lock:
            job.status = "cancelled"
            job.error, job.error_kind = "The build was cancelled.", "cancelled"
        try:
            os.killpg(os.getpgid(job.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            job.proc.terminate()
        return True


_FLAGS = [("--reference-chain", "the chain in Options"), ("--partner-chains", "the other subunits in Options"), ("--target-pdb", "the target structure"),
          ("--reference-cif", "the reference structure"), ("--reference-id", "the reference name"), ("--uniprot", "the UniProt accession in Options"),
          ("--profile", "the profile file in Options"), ("--settings", "the settings file"), ("--online", "the online lookup in Options")]


def friendly(msg: str) -> str:
    """The command line's own wording made readable for the page: option names become the page's labels, long file paths become file names."""
    msg = msg.replace("name the chain of the target with --reference-chain", "choose the chain of your target in Options")
    for flag, label in _FLAGS:
        msg = msg.replace(flag, label)
    return re.sub(r"(?<![\w.:/])/(?:[^\s/:;,'\"]+/)+([^\s/:;,'\"]+\.\w{1,6})(?=[:;,\s)]|$)", r"\1", msg)


def _explain(lines: list[str], code: int) -> tuple[str, str]:
    """A message for the page from the end of the log: the command's own one-line error when there is one."""
    for line in reversed(lines[-40:]):
        m = _ERR.match(line.strip())
        if m:
            return friendly(m["msg"]), ("inputs" if code == 2 else "failed")
    tail = [x for x in lines[-12:] if x.strip()]
    if any("BrokenProcessPool" in x or "MemoryError" in x or "Killed" in x for x in tail):
        return "The analysis ran out of memory or a worker stopped. Close other apps and build again, or build with fewer runs at once.", "crash"
    return (tail[-1] if tail else f"The build stopped with exit code {code}."), "crash"
