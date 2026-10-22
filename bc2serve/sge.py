"""Talking to Son of Grid Engine, for a bc2serve running on a cluster's login node.

Two jobs, and they are separate on purpose:

  snapshot()  reads what the cluster has free right now, so the Design page can
              say where a campaign could run before anyone presses the button.
  submit()/state()/cancel()
              run campaigns as batch jobs instead of as children of this process.

On a cluster bc2serve must *stop* allocating rather than allocate differently.
SGE already decides which card a job gets, and a second allocator racing it would
hand out the same GPU twice. So the SGE backend never reads nvidia-smi, never
holds a card, and submits everything at once: the queue is the queue.

This module shells out to the SGE clients rather than using DRMAA, because they
are what is installed and what the cluster's own documentation describes. It must
run OUTSIDE a container: qstat and friends link against libmunge.so.2, a host
library, so a containerised caller cannot reach the scheduler at all.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field

TIMEOUT_S = 45

# A GPU model as qstat reports it, and the -l flag that asks for one. Ordered
# longest-first on match so "A100 80GB" is not caught by a looser "A10" rule.
GPU_FLAGS: list[tuple[str, str, str]] = [
    # (match in the reported name, display name, qsub flag)
    ("A100", "A100 80GB", "gpu_a100"),
    ("A800", "A800 40GB", "gpu_a800"),
    ("H100", "H100", "gpu_h100"),
    ("L40S", "L40S", "gpu_l40s"),
    ("RTX 6000 Ada", "RTX 6000 Ada", "gpu_rtx6000ada"),
    ("RTX 4500 Ada", "RTX 4500 Ada", "gpu_rtx4500ada"),
    ("RTX A6000", "RTX A6000", "gpu_rtxa6000"),
    ("RTX 8000", "Quadro RTX 8000", "gpu_rtx8000"),
    ("RTX 2080 Ti", "RTX 2080 Ti", "gpu_2080ti"),
    ("GTX 1080 Ti", "GTX 1080 Ti", "gpu_1080ti"),
    ("V100S", "V100S 32GB", "gpu_v100"),
    ("V100", "V100 32GB", "gpu_v100"),
    ("A40", "A40", "gpu_a40"),
    ("A30", "A30", "gpu_a30"),
    ("A10", "A10", "gpu_a10"),
    ("L4", "L4", "gpu_l4"),
    ("TITAN V", "TITAN V", "gpu_titanv"),
    ("P100", "P100", "gpu_p100"),
]

# all.q reaches every node in the cluster, which is why it is never short of
# hardware and never a promise: an investor job arriving on that node evicts
# yours (SIGTERM, exit 143). Capacity reachable only through it is counted
# separately everywhere below, never folded into "free".
EVICTABLE_QUEUE = "all.q"

_CARD_MEM = re.compile(r"^hl:gpu\.cuda\.(?P<index>\d+)\.mem_free=(?P<value>[\d.]+[KMGT]?)$")

# How many cards the scheduler can describe: gpu.cuda.0..7, eight to a node, on the cluster this was written for.
MAX_CARDS = 8


def _to_gb(text: str) -> float:
    """SGE memory values carry a unit suffix: 79.148G, 10752M, sometimes none."""
    scale = {"K": 1 / 1048576, "M": 1 / 1024, "G": 1.0, "T": 1024.0}
    unit = text[-1:].upper()
    try:
        return float(text[:-1]) * scale[unit] if unit in scale else float(text) / 1073741824
    except ValueError:
        return 0.0


_HEAD = re.compile(
    r"^(?P<queue>[^\s@]+)@(?P<host>\S+)\s+\S+\s+"
    r"(?P<resv>\d+)/(?P<used>\d+)/(?P<total>\d+)\s+\S+\s+\S+(?:\s+(?P<state>\S+))?\s*$"
)


def available() -> bool:
    """Whether this machine can submit to a grid engine."""
    return bool(shutil.which("qsub") and shutil.which("qstat"))


@dataclass
class Instance:
    """One queue instance — a queue as it appears on one node."""
    queue: str
    host: str
    used: int
    reserved: int
    total: int
    state: str
    ngpus: int | None = None          # free GPUs, as this queue instance sees them
    gpu_names: list[str] = field(default_factory=list)
    card_free_gb: dict[int, float] = field(default_factory=dict)   # gpu.cuda.N.mem_free

    @property
    def usable(self) -> bool:
        # Any state flag at all means the scheduler will not place work here:
        # a=alarm, A=suspended-by-alarm, d=disabled, u=unreachable, E=error,
        # S/s=suspended. Treating "no flags" as the only good state is why this
        # reads the field rather than listing the bad letters.
        return not self.state

    @property
    def free_slots(self) -> int:
        return max(self.total - self.used - self.reserved, 0)


def _run(args: list[str], stdin: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(args, input=stdin, capture_output=True, text=True,
                           timeout=TIMEOUT_S)
    except (subprocess.SubprocessError, OSError) as problem:
        return 1, str(problem)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def parse_instances(text: str) -> list[Instance]:
    """Parse ``qstat -f -F ngpus,gpu.names``.

    The resource lines belong to the queue instance above them, so this keeps a
    cursor rather than splitting on the dashed rules: a node with no resources
    reported has no lines between the rules at all.
    """
    out: list[Instance] = []
    current: Instance | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("-") or line.startswith("queuename"):
            continue
        if line[:1].isspace():
            if current is None:
                continue
            body = line.strip()
            if body.startswith("qc:ngpus="):
                try:
                    current.ngpus = int(body.split("=", 1)[1])
                except ValueError:
                    pass
            elif body.startswith("hl:gpu.names="):
                names = body.split("=", 1)[1]
                current.gpu_names = [n.strip() for n in names.split(";") if n.strip()]
            else:
                m = _CARD_MEM.match(body)
                if m:
                    current.card_free_gb[int(m["index"])] = _to_gb(m["value"])
            continue
        m = _HEAD.match(line)
        if not m:
            current = None          # a job line, or something unexpected: stop attributing
            continue
        current = Instance(
            queue=m["queue"], host=m["host"], used=int(m["used"]),
            reserved=int(m["resv"]), total=int(m["total"]), state=m["state"] or "",
        )
        out.append(current)
    return out


def parse_whichq(text: str) -> set[str]:
    """Queue names from ``whichq``, which prints a sentence and then a list."""
    names = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("-") or " " in line or ":" in line:
            continue
        names.add(line)
    return names


def _gpu_model(name: str) -> tuple[str, str | None]:
    for needle, display, flag in GPU_FLAGS:
        if needle.lower() in name.lower():
            return display, flag
    return name, None


def build_snapshot(instances: list[Instance], mine: set[str]) -> dict:
    """Queues and GPU models, counted the way someone choosing a queue needs.

    Counting has to go through the *host*, not the queue instance. One node
    appears once per queue it belongs to, so aggregating over instances counts
    the same physical card several times; and ``gpu.names`` lists one entry per
    card, so a node with two A100s reports the model twice and must be counted
    twice. An earlier version aggregated over instances and took the maximum
    free count, which produced "8 free of 7" for the A10s — self-evidently wrong
    and exactly the kind of number someone would act on.
    """
    queues: dict[str, dict] = {}
    for i in instances:
        q = queues.setdefault(i.queue, {
            "name": i.queue, "nodes": 0, "idle": 0, "partial": 0, "full": 0,
            "offline": 0, "slots_free": 0, "mine": i.queue in mine,
        })
        q["nodes"] += 1
        if not i.usable:
            q["offline"] += 1
            continue
        q["slots_free"] += i.free_slots
        if i.used + i.reserved == 0:
            q["idle"] += 1
        elif i.free_slots > 0:
            q["partial"] += 1
        else:
            q["full"] += 1

    # One record per physical node, merged from however many queues list it.
    hosts: dict[str, dict] = {}
    for i in instances:
        if not i.gpu_names:
            continue
        h = hosts.setdefault(i.host, {"models": {}, "free": 0, "reach": set(),
                                      "evict": False, "cards": {}})
        h["cards"].update(i.card_free_gb)
        for name in i.gpu_names:
            display, _ = _gpu_model(name)
            h["models"][display] = h["models"].get(display, 0) + 1
        # gpu.names repeats identically on every queue instance of a node, so
        # the counts above are per-instance; keep the largest, not the sum.
        if len(i.gpu_names) and h.get("_per_instance") is None:
            h["_per_instance"] = True
        if i.usable:
            h["free"] = max(h["free"], i.ngpus or 0)
            if i.queue in mine:
                if i.queue == EVICTABLE_QUEUE:
                    h["evict"] = True
                else:
                    h["reach"].add(i.queue)

    # Undo the per-instance repetition: a node listed in k queues reported its
    # cards k times, so divide by how many instances mentioned it.
    seen = {}
    for i in instances:
        if i.gpu_names:
            seen[i.host] = seen.get(i.host, 0) + 1
    for host, h in hosts.items():
        k = max(1, seen.get(host, 1))
        h["models"] = {m: max(1, c // k) for m, c in h["models"].items()}

    gpus: dict[str, dict] = {}
    for host, h in hosts.items():
        single = len(h["models"]) == 1
        for model, count in h["models"].items():
            g = gpus.setdefault(model, {
                "model": model, "flag": _flag_for(model), "total": 0, "free": 0,
                "evictable": 0, "nodes": 0, "nodes_free": 0, "via": [],
                "free_mem_gb": 0.0, "evictable_mem_gb": 0.0, "card_gb": 0.0,
            })
            g["total"] += count
            g["nodes"] += 1
            # A node's free count is not per-model. On a node carrying one model
            # it is unambiguous; on a mixed node it is not, and guessing a split
            # would overstate something. Mixed nodes contribute capacity but no
            # free count, which understates rather than misleads.
            if not single or not h["free"]:
                continue
            # The largest free card on this node. SGE says how many cards are
            # free and how much is free on each, but not WHICH are free, so the
            # biggest is the honest answer to "what could I be given here".
            roomiest = max(h["cards"].values(), default=0.0)
            if h["reach"]:
                g["free"] += h["free"]
                g["nodes_free"] += 1
                g["free_mem_gb"] = max(g["free_mem_gb"], roomiest)
                for q in sorted(h["reach"]):
                    if q not in g["via"]:
                        g["via"].append(q)
            elif h["evict"]:
                g["evictable"] += h["free"]
                g["evictable_mem_gb"] = max(g["evictable_mem_gb"], roomiest)
                if EVICTABLE_QUEUE not in g["via"]:
                    g["via"].append(EVICTABLE_QUEUE)

    # A model's card size, taken from the emptiest card seen anywhere, reachable
    # or not. It is a lower bound: if every card of a model is busy cluster-wide,
    # this reads low. Shown as capacity, never used to decide what fits — that is
    # what free_mem_gb is for, and it is measured rather than inferred.
    for host, h in hosts.items():
        if len(h["models"]) != 1 or not h["cards"]:
            continue
        model = next(iter(h["models"]))
        g = gpus.get(model)
        if g:
            g["card_gb"] = max(g["card_gb"], max(h["cards"].values()))

    for g in gpus.values():
        for key in ("free_mem_gb", "evictable_mem_gb", "card_gb"):
            g[key] = round(g[key], 1)

    return {
        "queues": sorted(queues.values(), key=lambda q: (not q["mine"], q["name"])),
        "gpus": sorted(gpus.values(), key=lambda g: (-(g["free"] + g["evictable"]), g["model"])),
        "read_at": time.time(),
    }


def _flag_for(display: str) -> str | None:
    for _, name, flag in GPU_FLAGS:
        if name == display:
            return flag
    return None


def snapshot() -> dict:
    """What the cluster has free now, or an error the page can show as-is."""
    if not available():
        return {"error": "This machine does not submit to a grid engine.",
                "queues": [], "gpus": [], "read_at": time.time()}
    env = dict(os.environ, SGE_LONG_QNAMES="-1")   # else queue@host is truncated
    try:
        fields = "ngpus,gpu.names," + ",".join(
            f"gpu.cuda.{n}.mem_free" for n in range(MAX_CARDS))
        p = subprocess.run(["qstat", "-f", "-F", fields, "-q", "*"],
                           capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
    except (subprocess.SubprocessError, OSError) as problem:
        return {"error": f"Could not read the queues: {problem}",
                "queues": [], "gpus": [], "read_at": time.time()}
    instances = parse_instances(p.stdout)
    if not instances:
        return {"error": "The scheduler returned nothing readable.",
                "queues": [], "gpus": [], "read_at": time.time()}
    code, out = _run(["whichq"])
    mine = parse_whichq(out) if code == 0 else set()
    snap = build_snapshot(instances, mine)
    if not mine:
        # Without whichq nothing can be called reachable, and showing every
        # queue as available would send people at queues they cannot use.
        snap["note"] = "Could not read which queues you may use, so none is marked yours."
    return snap


# --------------------------------------------------------------- submission

# Written from a recipe proven on a production SGE cluster: the cuda12 image, AF2 params bound in, a durable
# XLA cache, and CUDA_VISIBLE_DEVICES passed through APPTAINERENV_ because
# `apptainer exec -e` strips the environment it would otherwise inherit.
JOB_TEMPLATE = """#!/bin/bash
#$ -N {name}
#$ -cwd
#$ -j y
#$ -o {log}
{resources}

SIF={sif}
[ -n "${{CUDA_VISIBLE_DEVICES:-}}" ] && export APPTAINERENV_CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES"
export APPTAINERENV_BINDCRAFT_AF2_PARAMS=/af2params
export APPTAINERENV_JAX_COMPILATION_CACHE_DIR=/xla_cache
mkdir -p {cache}

echo "host $(hostname)  CUDA_VISIBLE_DEVICES=${{CUDA_VISIBLE_DEVICES:-unset}}  $(date)"
nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader 2>/dev/null | head -2

apptainer exec -e \\
  --bind {params}:/af2params --bind {cache}:/xla_cache{binds} --pwd {project} \\
  "$SIF" bindcraft design {campaign}
status=$?
echo "== exit $status at $(date)"
exit $status
"""


def job_script(name: str, campaign: str, project: str, log: str, *, sif: str,
               params: str, cache: str, slots: int, queue: str = "",
               gpu_flag: str | None, gpus: int = 1, binds: tuple[str, ...] = ()) -> str:
    """The batch script for one campaign.

    Slots buy memory on clusters set up this way, not just cores: each is one core plus the node's
    RAM divided by its cores, so ~4 GB. The smoke runs measured maxvmem between
    16 and 33 GB with a single worker, which is why the default is 8 rather than
    the 6 that looked sufficient on the first node tried.
    """
    # No queue named, no `-q`: the scheduler then uses the site's own default
    # queue. A queue name belongs to one cluster, and a wrong one is a job that
    # is refused or never runs, so shipping somebody's queue as the fallback is
    # worse than shipping none.
    lines = [f"#$ -pe smp {slots}"]
    if queue:
        lines.insert(0, f"#$ -q {queue}")
    # A gpu_*=true already implies ngpus=1; asking for both is how you end up
    # requesting two cards without meaning to.
    lines.append(f"#$ -l {gpu_flag}=true" if gpu_flag else f"#$ -l ngpus={gpus}")
    # Every path here reaches a shell, and two of them are typed by a person:
    # the results root and the campaign name. Unquoted, a path with a space
    # split into two arguments and a path containing $( ) ran as a command when
    # the job started. The scheduler directives above are read by qsub rather
    # than by the shell, so they are not quoted -- quoting them would make the
    # queue name part of the value.
    quoted = {key: shlex.quote(str(value)) for key, value in
              (("campaign", campaign), ("project", project),
               ("sif", sif), ("params", params), ("cache", cache))}
    # Every host folder the campaign reads or writes is bound at its own path. The
    # container sees only what is bound, and a home reached through a symlink (as on
    # clusters that moved their home directories) is bound by its real path, so a
    # project folder spelled the way the user's home is spelled does not exist in
    # there: Apptainer could not even enter it to start. Binding each one under its
    # own spelling is what the hand-written batch jobs on such a cluster did.
    extra = "".join(f" --bind {shlex.quote(f'{b}:{b}')}" for b in dict.fromkeys(binds))
    # `log` is deliberately NOT quoted: it fills `#$ -o`, which qsub reads
    # itself. Quotes there become part of the path rather than disappearing.
    return JOB_TEMPLATE.format(name=name, log=log, resources="\n".join(lines), binds=extra, **quoted)


def submit(script_path: str) -> tuple[str | None, str]:
    """qsub a script. Returns (job id, message)."""
    code, out = _run(["qsub", "-terse", script_path])
    text = out.strip()
    if code != 0 or not text:
        return None, text or f"qsub exited {code}"
    # -terse prints the id alone; an array job prints id.task, and the id is
    # what qstat and qdel both take.
    return text.splitlines()[0].split(".")[0], ""


# SGE's own letters, mapped to the three words bc2serve's UI knows. 'E' is an
# error state the scheduler will not clear by itself, so it is a failure and not
# a kind of waiting: a job sitting in Eqw never starts.
_RUNNING = set("rt")
_QUEUED = set("qw h")


def state(job_id: str) -> tuple[str, str]:
    """('queued'|'running'|'gone', message) for a live job.

    'gone' means the scheduler no longer lists it, which is the only way a
    finished job reports here — qstat drops a job the moment it ends, and what
    happened to it then has to come from qacct.
    """
    code, out = _run(["qstat", "-j", str(job_id)])
    if code != 0 or "jobs do not exist" in out.lower():
        return "gone", ""
    code, out = _run(["qstat", "-u", os.environ.get("USER", "")])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 4 and parts[0] == str(job_id):
            flags = parts[4]
            if "E" in flags:
                return "gone", "the scheduler put the job in an error state"
            if set(flags) & _RUNNING:
                return "running", ""
            return "queued", ""
    return "queued", ""


def finished(job_id: str) -> tuple[int | None, str]:
    """Exit status from qacct, once the job has left the queue.

    Accounting lags the queue by a few seconds, so a job that has just gone may
    not be there yet; (None, '') means "ask again shortly", not "no record".
    """
    code, out = _run(["qacct", "-j", str(job_id)])
    if code != 0:
        return None, ""
    status, failed = None, ""
    for line in out.splitlines():
        if line.startswith("exit_status"):
            try:
                status = int(line.split()[1])
            except (IndexError, ValueError):
                pass
        elif line.startswith("failed") and line.split()[1:2] not in ([], ["0"]):
            failed = " ".join(line.split()[1:])
    # 143 is SIGTERM, which on all.q means the job was evicted rather than that
    # it went wrong. Worth saying, because the answer is "resubmit elsewhere".
    if status == 143 and not failed:
        failed = "stopped by the scheduler (evicted, or cancelled)"
    return status, failed


def cancel(job_id: str) -> bool:
    code, _ = _run(["qdel", str(job_id)])
    return code == 0
