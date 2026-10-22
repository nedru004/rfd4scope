"""The BindCraft2 web service.

Serves the single-page UI and the API behind it. Bound to the loopback interface
by bc2-serve.sh, so the SSH tunnel is the only way in.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import campaign as campaign_builder
from . import designs as design_listing
from . import imports as run_imports
from . import notify
from . import sge
from . import review
from . import storage
from . import structure
from . import tables
from .jobs import JobStore

REPO = Path(os.environ.get("BINDCRAFT_HOME", Path(__file__).resolve().parent.parent))
STATE_DIR = Path(os.environ.get("BC2SERVE_STATE", REPO / "bc2serve_state"))
RESULTS_ROOT = Path(os.environ.get("BC2SERVE_RESULTS", REPO / "results"))
UPLOAD_DIR = STATE_DIR / "uploads"
RESERVED = {c.strip() for c in os.environ.get("BC2SERVE_RESERVED_GPUS", "").split(",") if c.strip()}
NTFY_SERVER = os.environ.get("BC2SERVE_NTFY_SERVER", notify.DEFAULT_SERVER)

# Where campaigns may be written. Results grow large, and this machine keeps its
# space on /ssd and /data rather than under home, so those are offered when they
# exist and can be written.
def _result_roots() -> list[Path]:
    named = os.environ.get("BC2SERVE_RESULT_ROOTS", "")
    if named:
        roots = [Path(p.strip()).expanduser() for p in named.split(",") if p.strip()]
    else:
        roots = [RESULTS_ROOT] + [Path(p) / os.environ.get("USER", "bindcraft")
                                  for p in ("/ssd", "/data")]
    keep = []
    for root in roots:
        base = root if root.exists() else root.parent
        if base.is_dir() and os.access(base, os.W_OK):
            keep.append(root)
    return keep or [RESULTS_ROOT]

RESULT_ROOTS = _result_roots()
EXTRA_ROOTS = [r for r in RESULT_ROOTS if r != RESULTS_ROOT]

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = 64 << 20
STRUCTURE_SUFFIXES = {".pdb", ".ent", ".cif", ".mmcif", ".fasta", ".fa", ".faa"}

for directory in (STATE_DIR, RESULTS_ROOT, UPLOAD_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="BindCraft2")
store = JobStore(REPO, STATE_DIR, reserved=RESERVED)
# Runs BindCraft wrote elsewhere, linked into the results root rather than copied.
imported = run_imports.Imports(STATE_DIR, RESULTS_ROOT, UPLOAD_DIR)
reviews = review.Reviews(store, [RESULTS_ROOT, *EXTRA_ROOTS], UPLOAD_DIR, REPO,
                         constructs=imported.construct_for)


def _structure_roots() -> tuple:
    """Every place a structure may be read from, for `_inside`.

    An imported run is reached through a link in the results root, and `_inside`
    resolves before it compares, so the run's own folder has to be named here or
    its structures would be refused. Recomputed per request: a run imported since
    the service started must work without a restart.
    """
    return (UPLOAD_DIR, REPO / "settings", RESULTS_ROOT, *EXTRA_ROOTS, *imported.paths())


# Methods that change something. The guard below only looks at these: a GET is
# readable cross-origin anyway and refusing one would break nothing an attacker
# needs and plenty that the page does.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _foreign(request: Request) -> str | None:
    """Why this request looks like it came from another site, or None.

    The service has no authentication and is reached over an SSH tunnel, which
    is a boundary against the network but not against the browser: while the
    tunnel is up, *any* page open in that browser can post to
    `localhost:<port>`, and the port is derived from the user id so it is
    guessable. A form post and a multipart upload are both "simple" requests,
    so the browser sends them without asking this service first — which means
    a page on any site could have started a GPU campaign.

    This is not authentication and does not pretend to be. It only insists that
    a request claiming to come from a page came from *this* page:

    * `Sec-Fetch-Site` is set by the browser itself and cannot be forged by the
      page making the request. `same-origin` is our own page and `none` is a
      typed address; anything else, `cross-site` and `same-site` alike, is
      another origin. Same-site matters here too: a page on another *port* of
      localhost is same-site and still not us.
    * `Origin` is the older check, for browsers that send no `Sec-Fetch-Site`.
      It must match the host this request was addressed to.

    A request with neither header is not from a browser — curl, a script, the
    tests — and is left alone, because locking those out would break the way
    the service is actually driven without closing anything a browser can do.
    """
    site = request.headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return (f"This request came from another site ({site}). Binderscope only accepts "
                "changes from its own pages.")
    origin = request.headers.get("origin")
    if origin:
        host = request.headers.get("host", "")
        try:
            came_from = urllib.parse.urlsplit(origin).netloc
        except ValueError:
            came_from = ""
        if not came_from or not host or came_from.lower() != host.lower():
            return (f"This request says it came from {origin}, which is not this service. "
                    "Binderscope only accepts changes from its own pages.")
    return None


@app.middleware("http")
async def _own_pages_only(request: Request, call_next):
    """Refuse a state-changing request that another site's page made.

    In the middleware rather than in each handler because there are fourteen of
    them, three take multipart rather than JSON, and the ones that are easiest
    to forget — a DELETE with no body — are the ones with no other check.
    """
    if request.method in UNSAFE_METHODS:
        refusal = _foreign(request)
        if refusal:
            return JSONResponse({"detail": refusal}, status_code=403)
    return await call_next(request)


async def _body(request: Request) -> dict:
    """The JSON a request carries, or a clean refusal.

    Without this an unparseable body raises out of the handler and the caller gets
    an opaque 500, which says nothing about what was wrong with what they sent.

    The content type is insisted on, which is the second half of the guard
    above: `application/json` is not a content type a cross-origin page can set
    without the browser asking this service for permission first, and this
    service never grants it. So a JSON route is closed to another site's page
    even where the headers the guard reads are missing.
    """
    kind = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if kind != "application/json":
        raise HTTPException(415, "Send this as application/json.")
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as problem:
        raise HTTPException(400, f"That request was not valid JSON: {problem}")
    if not isinstance(payload, dict):
        raise HTTPException(400, "Expected a JSON object.")
    return payload


def _inside(path: Path, *roots: Path) -> bool:
    """True when path sits under one of roots, which is what keeps the file
    endpoints from being talked into serving something elsewhere."""
    try:
        resolved = path.resolve()
        # Compared as paths, not as strings: as strings, /results_old would pass for
        # a file under /results.
        return any(resolved == base or base in resolved.parents
                   for base in (Path(root).resolve() for root in roots))
    except (OSError, ValueError):  # ValueError is what a NUL byte in a URL raises
        return False


# ------------------------------------------------------------------ presets

@app.get("/api/presets")
def api_presets() -> dict:
    data = campaign_builder.presets(REPO)
    data["results_root"] = str(RESULTS_ROOT)
    data["result_roots"] = [str(r) for r in RESULT_ROOTS]
    return data


@app.get("/api/gpus")
def api_gpus() -> dict:
    return {"cards": store.gpu_status()}


# Cached because the Design page polls it and qstat walks every queue instance
# on the cluster — about 2 s and 840 rows on the one it was written on. Nobody choosing a queue
# needs it fresher than this, and a page open in three tabs should not ask the
# scheduler three times.
_cluster_cache: dict = {"at": 0.0, "data": None}
CLUSTER_TTL_S = 20.0


@app.get("/api/cluster")
async def api_cluster(fresh: bool = False) -> dict:
    """Queue and GPU availability, for deciding where to send a campaign.

    Returns ``backend: "local"`` with empty lists on a machine that submits to
    its own cards, so the page can tell "no cluster here" from "the cluster did
    not answer" — the first is normal and the second is worth showing.
    """
    import time as _time
    if not sge.available():
        return {"backend": "local", "queues": [], "gpus": [], "read_at": _time.time()}
    now = _time.time()
    if not fresh and _cluster_cache["data"] and now - _cluster_cache["at"] < CLUSTER_TTL_S:
        return {**_cluster_cache["data"], "cached": True}
    # qstat blocks for seconds; off the event loop so the rest of the UI stays live.
    data = await run_in_threadpool(sge.snapshot)
    data["backend"] = "sge"
    _cluster_cache.update(at=now, data=data)
    return data


# ------------------------------------------------------------------ targets

@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> dict:
    name = Path(file.filename or "target").name
    suffix = Path(name).suffix.lower()
    if suffix not in STRUCTURE_SUFFIXES:
        raise HTTPException(400, f"{suffix or 'that'} is not a structure or sequence file. "
                                 "Use PDB, mmCIF or FASTA.")
    safe = re.sub(r"[^\w.-]+", "_", name)
    destination = UPLOAD_DIR / safe
    size = 0
    with open(destination, "wb") as handle:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                handle.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(400, "That file is larger than 64 MB.")
            handle.write(chunk)
    return {"name": Path(safe).stem, "path": str(destination), "chains": _chains(destination)}


@app.post("/api/fetch-pdb")
def api_fetch_pdb(entry: str = Form(...)) -> dict:
    """Fetch a structure from RCSB, which this machine can reach."""
    code = entry.strip().lower()
    if not re.fullmatch(r"[0-9a-z]{4}", code):
        raise HTTPException(400, "A PDB ID is four characters, like 4z18.")
    destination = UPLOAD_DIR / f"{code}.cif"
    if not destination.is_file():
        url = f"https://files.rcsb.org/download/{code}.cif"
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = response.read(MAX_UPLOAD_BYTES + 1)
        except (urllib.error.URLError, OSError) as problem:
            raise HTTPException(502, f"Could not fetch {code.upper()} from RCSB: {problem}")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"{code.upper()} is larger than 64 MB.")
        destination.write_bytes(payload)
    return {"name": code.upper(), "path": str(destination), "chains": _chains(destination)}


ALPHAFOLD = "https://alphafold.ebi.ac.uk"


@app.post("/api/fetch-af")
def api_fetch_af(uniprot: str = Form(...)) -> dict:
    """Fetch the AlphaFold model of a UniProt entry, which is the usual full-length reference.

    The structure's address is asked of AlphaFold's own API rather than built from
    the accession, because the model version is part of the filename and changes
    (it is v6 now, and the v4 file this was first written against is gone).
    """
    accession = uniprot.strip().upper()
    if not review.UNIPROT.fullmatch(accession):
        raise HTTPException(400, "A UniProt accession looks like P69905 or P0DTC2.")
    # AlphaFold's file host turns away some default client identifiers (curl's, for
    # one), so this says plainly what it is.
    agent = {"User-Agent": "bc2serve (BindCraft2 web service)"}
    try:
        lookup = urllib.request.Request(f"{ALPHAFOLD}/api/prediction/{accession}", headers=agent)
        with urllib.request.urlopen(lookup, timeout=30) as response:
            models = json.loads(response.read(1 << 20))
        address = str(models[0]["cifUrl"])
        # Only follow the address if it is still AlphaFold's file host.
        filename = address.rsplit("/", 1)[-1]
        if not address.startswith(f"{ALPHAFOLD}/files/") or \
                not re.fullmatch(r"AF-[A-Z0-9]+-F\d+-model_v\d+\.cif", filename):
            raise ValueError("an unexpected address")
        destination = UPLOAD_DIR / filename
        if not destination.is_file():
            with urllib.request.urlopen(
                    urllib.request.Request(address, headers=agent), timeout=60) as response:
                payload = response.read(MAX_UPLOAD_BYTES + 1)
            if len(payload) > MAX_UPLOAD_BYTES:
                raise HTTPException(400, f"The AlphaFold model of {accession} is larger than 64 MB.")
            # Through a temporary name, so an interrupted download is never mistaken
            # for the model next time.
            partial = destination.with_name(destination.name + ".part")
            partial.write_bytes(payload)
            os.replace(partial, destination)
    except urllib.error.HTTPError as problem:
        if problem.code == 404:
            raise HTTPException(404, f"AlphaFold has no model for {accession}.")
        raise HTTPException(502, f"Could not fetch {accession} from AlphaFold: {problem}")
    except (urllib.error.URLError, OSError) as problem:
        raise HTTPException(502, f"Could not reach AlphaFold: {problem}")
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, f"AlphaFold's answer about {accession} was not what was expected.")
    return {"name": destination.stem, "path": str(destination), "chains": _chains(destination),
            "uniprot": accession}


@app.get("/api/analysis")
def api_analysis(path: str, chains: str = "") -> dict:
    """Per-residue sequence, exposure and hotspot suggestions for the picker."""
    candidate = Path(path)
    if not _inside(candidate, *_structure_roots()):
        raise HTTPException(403, "That file is not one this service serves.")
    try:
        return structure.analyse(candidate, chains)
    except structure.BadStructure as problem:
        raise HTTPException(400, str(problem))


@app.post("/api/epitope-check")
async def api_epitope_check(request: Request) -> dict:
    """What is worth saying about a chosen set of hotspots. Advice, never a refusal."""
    form = await _body(request)
    candidate = Path(form.get("path") or "")
    if not _inside(candidate, *_structure_roots()):
        raise HTTPException(403, "That file is not one this service serves.")
    picked = [str(n)[:12] for n in (form.get("hotspots") or []) if str(n).strip()]
    try:
        return {"notes": structure.judge_epitope(candidate, form.get("chains") or "", picked)}
    except structure.BadStructure as problem:
        raise HTTPException(400, str(problem))


@app.post("/api/estimate")
async def api_estimate(request: Request) -> dict:
    """What the largest complex of this campaign will cost on one card."""
    form = await _body(request)
    memory, basis = _card_memory_gb(form.get("card_gb"))
    answer = structure.estimate(
        int(form.get("target_residues") or 0),
        int(form.get("length_min") or 0),
        int(form.get("length_max") or 0),
        memory,
    )
    # Which card the answer is about. On a cluster "will it fit" has no single
    # answer — a free 2080 Ti holds 10.8 GB and a free A100 holds 79.2 — so the
    # estimate has to name the card it assumed, or it is advice about nothing.
    answer["card_gb"] = round(memory, 1)
    answer["card_basis"] = basis
    return answer


def _card_memory_gb(asked: object) -> tuple[float, str]:
    """The card to size the estimate against, and how that was decided."""
    try:
        if asked is not None and float(asked) > 0:
            return float(asked), "chosen"
    except (TypeError, ValueError):
        pass
    cards = store.gpu_status()
    if cards:
        return max(c["memory_total_mib"] for c in cards) / 1024, "local"
    if sge.available():
        snap = _cluster_cache["data"] or sge.snapshot()
        # The roomiest card actually free to this user, not the roomiest the
        # cluster owns: an estimate against an A100 nobody can have would say a
        # campaign fits when the cards on offer cannot take it.
        free = [g["free_mem_gb"] for g in snap.get("gpus", []) if g.get("free")]
        if free:
            return max(free), "largest free on the cluster"
        any_card = [g["card_gb"] for g in snap.get("gpus", []) if g.get("card_gb")]
        if any_card:
            return max(any_card), "largest on the cluster, none free now"
    return 48.0, "assumed"


def _choose_destination(gpu_type: str, queue: str, form: dict) -> tuple[str, str]:
    """Settle which cards a campaign may land on. Returns (gpu_type, what to say).

    Naming a model that cannot hold the campaign was already refused. Naming
    nothing was not, and "no preference" submits as `-l ngpus=1` — any card,
    including one too small, chosen by the scheduler rather than by anyone. A
    campaign needing 34.9 GB could land on an 11 GB card because its owner did
    not make a choice, which is the worst way to reach that outcome.

    So when nothing is named and some reachable model is too small, the request
    is narrowed to a model that fits. That is still "no preference" — it is a
    preference among the cards that can actually run the job — and the answer
    says which was chosen, because a silent choice is the problem being fixed.
    """
    _check_destination(gpu_type, queue, form)
    if gpu_type or not sge.available():
        return gpu_type, ""
    snap = _cluster_cache["data"] or sge.snapshot()
    if snap.get("error"):
        return gpu_type, ""
    cost = _campaign_cost(form)
    if cost is None:
        return gpu_type, ""
    need, reserve = cost
    reachable = [g for g in snap.get("gpus", []) if g.get("flag") and (g.get("free") or g.get("evictable"))]
    if not reachable:
        return gpu_type, ""
    fits = [g for g in reachable if (g.get("card_gb") or 0) - reserve >= need]
    if not fits:
        raise HTTPException(400,
            f"This campaign needs {need:.1f} GB for its largest complex and no card you can "
            "reach is big enough. Trim the target, or shorten the binder.")
    if len(fits) == len(reachable):
        return gpu_type, ""          # every card that could take it can take it
    pick = max(fits, key=lambda g: (g.get("free") or 0, g.get("card_gb") or 0))
    too_small = len(reachable) - len(fits)
    return pick["flag"], (
        f"Sent to {pick['model']}: this campaign needs {need:.1f} GB and {too_small} of the "
        f"{len(reachable)} models with cards free are too small to hold it.")


def _campaign_cost(form: dict) -> tuple[float, float] | None:
    """(GB per design, GB a card keeps back), or None when the form cannot say.

    Both come out of structure.estimate rather than being restated here. The
    reserve is read as card_gb - usable_gb, so if BindCraft's 4 GB ever changes
    this follows it: a campaign the estimate calls a fit stays one this allows.
    """
    try:
        residues = int(form.get("target_residues") or 0)
        low, high = int(form.get("length_min") or 0), int(form.get("length_max") or 0)
    except (TypeError, ValueError):
        return None
    if not residues:
        return None
    # The per-design cost does not depend on the card, so any card size answers it.
    probe = structure.estimate(residues, low, high, 80.0)
    return float(probe["per_worker_gb"]), 80.0 - float(probe["usable_gb"])


def _check_destination(gpu_type: str, queue: str, form: dict) -> None:
    """Refuse a destination that cannot run this campaign.

    The page already shows which models are too small, but a page is advice and
    this is the gate. Without it the widget could say a 2080 Ti will not hold a
    34.9 GB design and the submission could still ask for one — which was true
    of this service until the check existed.
    """
    snap = _cluster_cache["data"] or (sge.snapshot() if sge.available() else None)
    if not snap or snap.get("error"):
        return                      # cannot check; not a reason to block the user
    if queue and not any(q["name"] == queue and q["mine"] for q in snap.get("queues", [])):
        raise HTTPException(400, f"{queue} is not a queue you can submit to.")
    if not gpu_type:
        return
    card = next((g for g in snap.get("gpus", []) if g.get("flag") == gpu_type), None)
    if card is None:
        raise HTTPException(400, f"No GPU model on this cluster matches {gpu_type}.")
    try:
        residues = int(form.get("target_residues") or 0)
        low, high = int(form.get("length_min") or 0), int(form.get("length_max") or 0)
    except (TypeError, ValueError):
        return
    if not residues:
        return                      # nothing to size against; let it through
    # Against the card's own size, not what is free this second: the campaign
    # waits in the queue and will land on an empty card of that model.
    need = structure.estimate(residues, low, high, card["card_gb"] or 0.0)
    if not need["fits"]:
        # "A A10" and "A RTX 6000" both occur in this list, so the article is
        # chosen from the sound of the first letter, not hardcoded.
        article = "An" if card["model"][:1].upper() in "AEFHILMNORSX" else "A"
        raise HTTPException(400,
            f"{article} {card['model']} holds {card['card_gb']:.0f} GB and this campaign needs "
            f"{need['per_worker_gb']:.1f} GB for its largest complex. Choose a bigger card, "
            "or trim the target.")


@app.post("/api/trim")
async def api_trim(request: Request) -> dict:
    """Write a trimmed copy of a target, keeping its residue numbering."""
    form = await _body(request)
    source = Path(form.get("path") or "")
    if not _inside(source, *_structure_roots()):
        raise HTTPException(403, "That file is not one this service serves.")
    keep = [int(n) for n in (form.get("keep") or []) if str(n).lstrip("-").isdigit()]
    if not keep:
        raise HTTPException(400, "Nothing selected to keep.")
    stem = re.sub(r"[^\w.-]+", "_", (form.get("name") or source.stem))[:60]
    destination = UPLOAD_DIR / f"{stem}_trimmed.pdb"
    count = 1
    while destination.exists():
        count += 1
        destination = UPLOAD_DIR / f"{stem}_trimmed{count}.pdb"
    try:
        return structure.trim(source, form.get("chains") or "", keep, destination)
    except structure.BadStructure as problem:
        raise HTTPException(400, str(problem))


@app.get("/api/chains")
def api_chains(path: str) -> dict:
    """The chains in a structure, so the picker and the viewer agree about it."""
    candidate = Path(path)
    if not _inside(candidate, *_structure_roots()):
        raise HTTPException(403, "That file is not one this service serves.")
    if not candidate.is_file():
        raise HTTPException(404, "No such structure.")
    return {"chains": _chains(candidate)}


@app.get("/api/structure")
def api_structure(path: str) -> PlainTextResponse:
    """Serve a structure to the viewer, from the places a structure may come from."""
    candidate = Path(path)
    if not _inside(candidate, *_structure_roots()):
        raise HTTPException(403, "That file is not one this service serves.")
    if not candidate.is_file():
        raise HTTPException(404, "No such structure.")
    return PlainTextResponse(candidate.read_text(errors="ignore"))


def _chains(path: Path) -> list[dict]:
    """Chains and their residue numbers, for the viewer and the hotspot picker."""
    if path.suffix.lower() in (".fasta", ".fa", ".faa"):
        return []
    chains: dict[str, list[int]] = {}
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return []
    if path.suffix.lower() in (".cif", ".mmcif"):
        columns: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("_atom_site."):
                columns.append(stripped.split(".", 1)[1])
            elif stripped.startswith(("ATOM", "HETATM")) and columns:
                parts = stripped.split()
                try:
                    atom = parts[columns.index("label_atom_id")]
                    if atom.strip('"') != "CA":
                        continue
                    chain = parts[columns.index("auth_asym_id")] if "auth_asym_id" in columns \
                        else parts[columns.index("label_asym_id")]
                    number = parts[columns.index("auth_seq_id")] if "auth_seq_id" in columns \
                        else parts[columns.index("label_seq_id")]
                    chains.setdefault(chain, []).append(int(number))
                except (ValueError, IndexError):
                    continue
    else:
        for line in text.splitlines():
            if line.startswith("ATOM") and len(line) > 26 and line[12:16].strip() == "CA":
                chain = line[21].strip() or "A"
                try:
                    chains.setdefault(chain, []).append(int(line[22:26]))
                except ValueError:
                    continue
    return [{"chain": chain, "residues": sorted(set(numbers))}
            for chain, numbers in sorted(chains.items())]


# ------------------------------------------------------------------ jobs

@app.post("/api/jobs")
async def api_submit(request: Request) -> dict:
    form = await _body(request)
    try:
        campaign, campaign_file, project = campaign_builder.build(
            REPO, RESULTS_ROOT, form, RESULT_ROOTS)
    except campaign_builder.BadCampaign as problem:
        raise HTTPException(400, str(problem))
    gpu_type = (form.get("gpu_type") or "").strip()
    queue = (form.get("queue") or "").strip()
    gpu_type, placed = _choose_destination(gpu_type, queue, form)
    job_id = store.submit(
        name=campaign["campaign_name"],
        owner=(form.get("owner") or "").strip()[:64],
        project=(form.get("project") or "").strip()[:64],
        campaign_path=campaign_file,
        project_folder=project,
        gpus_wanted=int(form.get("gpus") or 1),
        notify_topic=(form.get("notify_topic") or "").strip()[:128],
        wanted=campaign["number_of_final_designs"],
        budget=campaign["max_trajectories"],
        gpu_type=gpu_type,
        queue=queue,
    )
    return {"id": job_id, "campaign": campaign, "placed": placed}


@app.get("/api/notify/topic")
def api_new_topic() -> dict:
    """A fresh topic. Whoever holds it can read the notifications, so it is long."""
    return {"topic": notify.new_topic(), "server": NTFY_SERVER}


@app.post("/api/notify/test")
async def api_test_notification(request: Request) -> dict:
    form = await _body(request)
    topic = (form.get("topic") or "").strip()
    if not topic:
        raise HTTPException(400, "No topic to send to.")
    sent = notify.send(
        topic, "BindCraft test",
        "Notifications are working. You will get one when a campaign passes halfway, "
        "accepts its first design, and finishes.",
        priority="default", tags="bell")
    if not sent:
        raise HTTPException(502, "Could not reach the notification service.")
    return {"sent": True}


@app.get("/api/jobs")
def api_jobs(archived: bool = False) -> dict:
    return {"jobs": store.list_jobs(archived=archived),
            "projects": store.projects()}


@app.post("/api/jobs/project")
async def api_set_project(request: Request) -> dict:
    """Move one or more campaigns into a project.

    One endpoint for one row and for forty: the Jobs page needs both, and a
    bulk path that is a loop over a single path is a bulk path that half-fails.
    """
    form = await _body(request)
    ids = form.get("ids")
    if not isinstance(ids, list) or not all(str(i).lstrip("-").isdigit() for i in ids):
        raise HTTPException(400, "Give the campaigns to move as a list of job ids.")
    changed = store.set_project([int(i) for i in ids], str(form.get("project") or ""))
    return {"changed": changed, "projects": store.projects()}


@app.post("/api/jobs/archive")
async def api_archive(request: Request) -> dict:
    """Hide finished rows, or bring them back. Deletes nothing."""
    form = await _body(request)
    ids = form.get("ids")
    if not isinstance(ids, list) or not all(str(i).lstrip("-").isdigit() for i in ids):
        raise HTTPException(400, "Give the campaigns to archive as a list of job ids.")
    want = bool(form.get("archived", True))
    changed = store.set_archived([int(i) for i in ids], want)
    skipped = len(ids) - changed
    # The note has to follow the direction. `set_archived` refuses a live row
    # either way, but "cannot be archived" said after a restore describes the
    # wrong action, and a row is also skipped simply for already being where it
    # was asked to go — which is not a refusal at all and should not read as one.
    note = ""
    if skipped:
        note = ("A campaign that is still queued or running cannot be archived."
                if want else
                "Rows that were already active were left alone; a queued or running one cannot be restored, "
                "because it was never archived.")
    return {"changed": changed, "skipped": skipped, "note": note}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: int) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    return job


@app.delete("/api/jobs/{job_id}")
def api_cancel(job_id: int) -> dict:
    if not store.cancel(job_id):
        raise HTTPException(409, "That job is not queued or running.")
    return {"cancelled": job_id}


@app.get("/api/jobs/{job_id}/log")
def api_log(job_id: int, tail: int = 400) -> PlainTextResponse:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    log = store.log_path(job)
    if not log.is_file():
        return PlainTextResponse("Nothing logged yet.")
    # Read the end only: a long campaign's log outgrows the page that shows it.
    with open(log, "rb") as handle:
        handle.seek(0, io.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - 200_000))
        text = handle.read().decode(errors="ignore")
    return PlainTextResponse("\n".join(text.splitlines()[-tail:]))


@app.get("/api/jobs/{job_id}/results")
def api_results(job_id: int) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    folder = Path(job["project_folder"])
    ranked = folder / "3_Ranked" / "!_Ranked.csv"
    if not ranked.is_file():
        return {"designs": [], "note": "No sequences have passed yet."}
    with open(ranked, newline="") as table:
        designs = list(csv.DictReader(table))
    complexes = {}
    for structure in (folder / "3_Ranked").glob("*.cif"):
        complexes[structure.stem] = str(structure)
    for design in designs:
        label = design.get("design", "")
        design["_structure"] = next(
            (path for stem, path in complexes.items() if stem.startswith(label)), "")
    return {"designs": designs}


@app.get("/api/jobs/{job_id}/campaign")
def api_campaign(job_id: int) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    path = Path(job["campaign_path"])
    if not path.is_file():
        raise HTTPException(404, "The campaign file is gone.")
    return json.loads(path.read_text())


# ------------------------------------------------------------------ designs

ARCHIVE_VIEWS = ("live", "only", "all")


def _designs_payload(project: str | None, campaign: str | None, archived: str) -> dict:
    """Everything /api/designs returns. Blocking: it reads the campaigns' CSVs."""
    wanted = store.campaigns_by_folder(project=project, archived=archived)
    if campaign:
        # Basename or full path: the page knows the first, a link may carry the second,
        # and only the full path tells apart two roots that hold a campaign of one name.
        # The stored folder is resolved, so a path with a symlink in it is resolved too.
        spelled = {campaign, str(Path(campaign).resolve()) if os.sep in campaign else campaign}
        wanted = [c for c in wanted if c["name"] in spelled or c["folder"] in spelled]
    found = design_listing.collect([(c["name"], Path(c["folder"])) for c in wanted])
    # collect returns one entry per folder it was given, in the order it was given them.
    for row, entry in zip(wanted, found["campaigns"]):
        entry.update(project=row["project"], job_id=row["job_id"], state=row["state"],
                     archived=row["archived"])
        folder = Path(row["folder"])
        # A review is linked only where /review/<name>/ would really open this folder.
        # A campaign outside the results roots has no such address, and a link to a
        # 404 is worse than none.
        entry["review"] = (reviews.status(folder, entry["counts"])
                           if reviews.serves(folder) else None)
    found["projects"] = store.projects()
    return found


@app.get("/api/designs")
async def api_designs(project: str | None = None, campaign: str | None = None,
                      archived: str = "live") -> dict:
    """The sequences that passed, across a project's campaigns, for the Results page.

    The project is a query parameter and not a path segment because its label is
    free text: Unfiled is the empty string, and a label may hold "/", "?" or "#",
    none of which survive as a path. Leave `project` out for every project;
    `project=` with nothing after it means Unfiled. A campaign whose folder has
    gone is listed with `missing: true` and no designs, never an error.
    """
    if archived not in ARCHIVE_VIEWS:
        raise HTTPException(400, "archived must be live, only or all.")
    # CSV parsing, kept off the event loop like the other slow reads.
    return await run_in_threadpool(_designs_payload, project, campaign, archived)


# ------------------------------------------------------------------ storage

# The sizes are measured by background walks and cached; a request never waits for
# one. See storage.py for why, and for what the cache key and token mean.
_storage_cache = storage.StorageCache()
SERVER_LOG = REPO / "bc2serve.log"
# A running campaign writes deep inside folders whose own timestamps do not move, so
# nothing about it would ever look changed. Its token carries a clock bucket instead,
# which makes the page re-measure it now and then while it runs and never again once
# it has stopped.
LIVE_REMEASURE_S = 300


def _storage_token(state: str | None, finished_at) -> str:
    if storage.is_terminal(state):
        return f"{state}:{finished_at}"
    return f"{state}:{finished_at}:{int(time.time() // LIVE_REMEASURE_S)}"


def _listed(directory: Path) -> set[str]:
    """Names of the subfolders of a directory; none if it cannot be read."""
    try:
        with os.scandir(directory) as entries:
            return {entry.name for entry in entries if entry.is_dir()}
    except OSError:
        return set()


def _storage_targets() -> dict:
    """What the Storage page can ask about: campaigns, reviews, and the server's own files.

    Campaigns come from the job database with archived ones included, because those
    are the ones worth sizing and they are invisible to every other list. Reviews
    come from the disk, because the interesting ones (a review whose campaign is
    gone) are exactly the ones the database cannot lead you to.
    """
    campaigns = store.campaigns_by_folder(archived="all")
    for row in campaigns:
        row["key"] = f"campaign:{row['folder']}"
        row["token"] = _storage_token(row["state"], row["finished_at"])
    by_folder = {row["folder"]: row for row in campaigns}

    found = []
    for root in reviews.roots:
        present = _listed(root)
        names = sorted(_listed(root / review.REVIEWS))
        # A project's review has no campaign folder to be missing, so it is judged by
        # whether any campaign it was pooled from is left (see storage.orphaned_reviews).
        left = {}
        for name in names:
            if name.startswith("@"):
                survivors = review.members_left(root / name)
                if survivors is not None:
                    left[name] = survivors
        orphans = set(storage.orphaned_reviews(names, present, left))
        for name in names:
            folder = root / name
            record = review.read_record(folder) or {}
            path = str(review.home(folder))
            try:
                owner = by_folder.get(str(folder.resolve()))
            except (OSError, RuntimeError):  # RuntimeError: a symlink loop, before 3.13
                owner = None
            found.append({
                "key": f"review:{path}", "name": name, "root": str(root), "path": path,
                "campaign": owner["folder"] if owner else None,
                "orphan": name in orphans, "state": record.get("state"),
                "token": _storage_token(record.get("state"), record.get("finished_at")),
            })
    return {"campaigns": campaigns, "reviews": found}


def _measured(view: dict) -> dict | None:
    """The measurement in a cache view, unless it is empty because the path is gone."""
    measurement = view.get("measurement")
    return measurement if measurement and not measurement.get("missing") else None


def _bytes(view: dict) -> int:
    measurement = _measured(view)
    return measurement["bytes"] if measurement else 0


def _storage_payload() -> dict:
    """Everything /api/storage returns. Never walks a tree, only reads the cache."""
    targets = _storage_targets()
    campaigns, found = targets["campaigns"], targets["reviews"]
    views = _storage_cache.get_many(
        [(c["key"], c["folder"], c["token"]) for c in campaigns]
        + [(r["key"], r["path"], r["token"]) for r in found]
        + [("server_log", str(SERVER_LOG), ""), ("uploads", str(UPLOAD_DIR), "")])

    review_bytes: dict[str, int] = {}
    for item in found:
        item["size"] = views[item["key"]]
        if item["campaign"]:
            review_bytes[item["campaign"]] = (review_bytes.get(item["campaign"], 0)
                                              + _bytes(item["size"]))

    rows, archived, projects = [], [], {}
    for c in campaigns:
        size = views[c["key"]]
        # Counts only for live campaigns: the table cache is small, and an archived
        # campaign is being sized, not read.
        counts = None if c["archived"] else tables.design_counts(c["folder"])
        row = {"key": c["key"], "folder": c["folder"], "name": c["name"],
               "project": c["project"], "job_id": c["job_id"], "state": c["state"],
               "created_at": c["created_at"], "finished_at": c["finished_at"],
               "archived": c["archived"], "design_ids": c["design_ids"],
               "review_ids": c["review_ids"], "counts": counts, "size": size,
               "review_bytes": review_bytes.get(c["folder"], 0)}
        rows.append(row)
        if c["archived"]:
            # What Restore needs and nothing more: both kinds of id, because archiving
            # a project has to send the reviews' ids along with the designs'.
            archived.append({k: row[k] for k in (
                "folder", "name", "project", "design_ids", "review_ids", "finished_at")}
                | {"bytes": _bytes(size), "review_bytes": row["review_bytes"]})
            continue
        group = projects.setdefault(c["project"], {
            "name": c["project"], "campaigns": 0, "passed": 0, "campaign_bytes": 0,
            "review_bytes": 0, "unmeasured": 0, "last_activity": None})
        group["campaigns"] += 1
        group["passed"] += counts["passed"]
        group["campaign_bytes"] += _bytes(size)
        group["review_bytes"] += row["review_bytes"]
        group["unmeasured"] += 0 if size["measurement"] else 1
        touched = c["finished_at"] or c["created_at"]
        if touched and (group["last_activity"] or 0) < touched:
            group["last_activity"] = touched

    # Reports only. Nothing here builds a command or deletes anything, by decision.
    reclaimable = []
    for c in rows:
        measurement = _measured(c["size"])
        if measurement:
            reclaimable += [{**item, "campaign": c["name"], "folder": c["folder"],
                             "archived": c["archived"]}
                            for item in storage.classify_campaign(
                                measurement["children"], c["state"], c["folder"])]
    for item in found:
        if item["orphan"] and _measured(item["size"]):
            reclaimable.append(storage.classify_orphan_review(
                item["name"], _bytes(item["size"]), item["path"]))
    if _measured(views["server_log"]):
        reclaimable.append(storage.classify_server_log(
            _bytes(views["server_log"]), str(SERVER_LOG)))
    if _measured(views["uploads"]):
        reclaimable.append(storage.classify_uploads(
            _bytes(views["uploads"]), str(UPLOAD_DIR)))
    reclaimable.sort(key=lambda item: item["bytes"], reverse=True)

    return {
        "projects": sorted(projects.values(),
                           key=lambda p: (p["name"] == "", p["name"].lower())),
        "campaigns": rows,
        "reviews": found,
        "reclaimable": reclaimable,
        "disks": storage.free_space(RESULT_ROOTS),
        "archived": archived,
        "measuring": _storage_cache.measuring(),
    }


@app.get("/api/storage")
async def api_storage() -> dict:
    """What is on disk and what could be given back. Returns at once, whatever is cached.

    Sizes come from background walks that this request may start but never waits
    for: every entry says when it was measured, whether it is being measured now and
    whether it is out of date, and the page polls until `measuring` is false. The
    request itself still stats a few paths and parses the project's tables, so it
    runs off the event loop.
    """
    return await run_in_threadpool(_storage_payload)


def _storage_refresh(key: str | None) -> dict:
    targets = _storage_targets()
    known = {c["key"]: (c["folder"], c["token"]) for c in targets["campaigns"]}
    known.update({r["key"]: (r["path"], r["token"]) for r in targets["reviews"]})
    known["server_log"] = (str(SERVER_LOG), "")
    known["uploads"] = (str(UPLOAD_DIR), "")
    if key is not None and key not in known:
        raise HTTPException(404, "Nothing by that name to measure.")
    wanted = [key] if key is not None else list(known)
    for name in wanted:
        _storage_cache.refresh(name, *known[name])
    return {"refreshing": wanted, "measuring": _storage_cache.measuring()}


@app.post("/api/storage/refresh")
async def api_storage_refresh(request: Request) -> dict:
    """Measure again now: one entry by its `key`, or everything when no key is given.

    A manual refresh is the only thing that overrides the back-off after a failed
    walk. The body is optional, so a bare POST means "everything".
    """
    form = await _body(request) if await request.body() else {}
    key = form.get("key")
    if key is not None and not isinstance(key, str):
        raise HTTPException(400, "key must be a string.")
    return await run_in_threadpool(_storage_refresh, key)


# ------------------------------------------------------------------ review

def _reviewing(function, *args):
    """Run a review call, turning its refusals into the usual {"detail": ...} reply."""
    try:
        return function(*args)
    except review.ReviewError as problem:
        raise HTTPException(problem.status, str(problem))


@app.get("/api/review")
def api_review_list() -> list:
    return _reviewing(reviews.campaigns)


# Registered before `/api/review/{campaign}`, which would otherwise take "projects" for the
# name of a campaign: Starlette matches in the order routes are added, and `{campaign}` is
# any one segment. The cost is that a campaign actually named "projects" can no longer be
# described at this address, so the form should not let one be made (campaign.py).
@app.get("/api/review/projects")
def api_review_projects() -> list:
    """The projects that could be reviewed as one, with their member counts and review state."""
    return _reviewing(reviews.projects)


# Also registered before `/api/review/{campaign}`, and for the same reason. The
# two words are refused as campaign names in campaign.RESERVED_NAMES.
@app.get("/api/review/imports")
def api_review_imports() -> list:
    """The runs brought in from elsewhere: where each really is, and whether it is still there."""
    return imported.rows()


@app.post("/api/review/import")
async def api_review_import(request: Request) -> dict:
    """Link a BindCraft run into the results folder so that it can be reviewed.

    The folder is named by its path on this machine, which is the case worth
    serving: BindCraft wrote it here, or on a disk this machine mounts. Nothing
    is uploaded and nothing in the run is written to.
    """
    form = await _body(request)
    try:
        return await run_in_threadpool(
            imported.add, str(form.get("path") or ""), str(form.get("name") or ""),
            str(form.get("construct") or ""))
    except run_imports.BadImport as problem:
        raise HTTPException(400, str(problem))


@app.delete("/api/review/import/{name}")
def api_review_import_remove(name: str) -> dict:
    """Forget an imported run. The link goes; the run itself is not touched."""
    try:
        return imported.remove(name)
    except run_imports.BadImport as problem:
        raise HTTPException(400, str(problem))


@app.get("/api/review/{campaign}")
def api_review_form(campaign: str) -> dict:
    return _reviewing(reviews.describe, campaign)


@app.post("/api/review/{campaign}")
async def api_review_start(campaign: str, request: Request) -> dict:
    form = await _body(request)
    # "No reference" is a choice made out loud (`no_reference: true`), not a reference
    # left blank; reviews.start refuses a request that makes neither choice, or both.
    if form.get("reference_path"):
        reference = Path(str(form["reference_path"]).strip())
        # The same places every other structure endpoint reads from. Without this a
        # reference could name any file this account can read.
        if not _inside(reference, *_structure_roots()):
            raise HTTPException(400, "That file is not somewhere this service reads structures "
                                     "from. Upload it, or fetch it by ID, and use that.")
    # Stopping an old build and clearing its folder can take a few seconds; that
    # must not hold up every other request.
    try:
        return await run_in_threadpool(reviews.start, campaign, form)
    except review.ReviewError as problem:
        raise HTTPException(problem.status, str(problem))


@app.get("/api/review/{campaign}/log")
def api_review_log(campaign: str, tail: int = 400) -> dict:
    return _reviewing(reviews.log, campaign, tail)


@app.get("/api/review/{campaign}/reference")
async def api_review_reference(campaign: str, path: str, chain: str = "", uniprot: str = "") -> dict:
    """How far a chosen reference can be trusted outside the construct (AlphaFold PAE).

    `campaign` is a campaign's name or a project's `@slug`; for a project the construct is the
    one its members share. It may ask AlphaFold for the PAE document, so it runs off the event loop.
    """
    if not _inside(Path(path), *_structure_roots()):
        raise HTTPException(400, "That file is not somewhere this service reads structures from.")
    try:
        return await run_in_threadpool(reviews.check_reference, campaign, path, chain, uniprot)
    except review.ReviewError as problem:
        raise HTTPException(problem.status, str(problem))


@app.get("/api/review/{campaign}/chains")
async def api_review_chains(campaign: str, path: str, chain: str = "", partners: str = "auto",
                            assembly: str = "") -> dict:
    """What a reference holds: which chain is the target, which are partners, and why.

    `campaign` is a campaign's name or a project's `@slug`, as for `/reference`.

    The same answer the build acts on, asked before it. It reads the structure file,
    which can be large, so it runs off the event loop.
    """
    if not _inside(Path(path), *_structure_roots()):
        raise HTTPException(400, "That file is not somewhere this service reads structures from.")
    try:
        return await run_in_threadpool(reviews.chains, campaign, path, chain, partners, assembly)
    except review.ReviewError as problem:
        raise HTTPException(problem.status, str(problem))


@app.delete("/api/review/{campaign}")
def api_review_delete(campaign: str) -> dict:
    return _reviewing(reviews.remove, campaign)


# A project's review: one build across every campaign filed under a project label. The
# slug is `@<something>`, which no campaign name can be, so these addresses and the
# campaign ones above never name the same review (see review.PROJECT_NAME).
#
# Order: `/api/review/project/{slug}` has one more segment than `/api/review/{campaign}`, so
# it cannot be taken for a campaign named "project". The campaign routes that DO have a
# second segment (`/log`, `/reference`, `/chains`) are registered above, ahead of these, so
# a campaign that really is named "project" keeps its own log, reference and chains: they
# match first, and a slug never equals any of those three words because it begins with "@".
# `/reference` and `/chains` are the exception to "a slug goes under /project/": they take a
# campaign name or an `@slug` in the same slot, since what they ask is the same for both.

@app.get("/api/review/project/{slug}")
def api_review_project(slug: str) -> dict:
    """What the form needs to review a project, and whether it can: members, construct, references."""
    return _reviewing(reviews.project_form, slug)


@app.post("/api/review/project/{slug}")
async def api_review_project_start(slug: str, request: Request) -> dict:
    form = await _body(request)
    if form.get("reference_path"):
        reference = Path(str(form["reference_path"]).strip())
        # As for a campaign: a reference is read only from the places structures are read from.
        if not _inside(reference, *_structure_roots()):
            raise HTTPException(400, "That file is not somewhere this service reads structures "
                                     "from. Upload it, or fetch it by ID, and use that.")
    try:
        return await run_in_threadpool(reviews.project_start, slug, form)
    except review.ReviewError as problem:
        raise HTTPException(problem.status, str(problem))


@app.get("/api/review/project/{slug}/log")
def api_review_project_log(slug: str, tail: int = 400) -> dict:
    return _reviewing(reviews.project_log, slug, tail)


@app.delete("/api/review/project/{slug}")
def api_review_project_delete(slug: str) -> dict:
    return _reviewing(reviews.project_remove, slug)


@app.get("/review/{campaign}")
def review_slash(campaign: str) -> RedirectResponse:
    # The bundle's pages find their data by relative addresses (data/campaign.js),
    # which only resolve against a folder, so the address must end in a slash.
    return RedirectResponse(f"/review/{campaign}/")


@app.api_route("/review/{campaign}/{path:path}", methods=["GET", "HEAD"])
def review_file(campaign: str, path: str) -> FileResponse:
    """A file of a finished review's static bundle, and nothing outside it.

    `campaign` is a campaign's name or a project's `@slug`; `reviews.bundle` tells them
    apart by the "@" and refuses anything that is neither.
    """
    bundle = _reviewing(reviews.bundle, campaign)
    target = bundle / path if path else bundle
    if target.is_dir():
        target = target / "index.html"
    if not _inside(target, bundle) or not target.is_file():
        raise HTTPException(404, "There is no such page in this review.")
    # Revalidate every time: a rebuilt review reuses the same addresses, and a
    # browser holding the old data/campaign.js would show the previous analysis.
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ the page


class _Revalidating(StaticFiles):
    """The page's own files, told to revalidate every time.

    These are served from disk over a loopback tunnel to a handful of people,
    so a cached stylesheet buys nothing and costs a real failure: an edit that
    is live on the server but invisible in the browser, which reads as "the
    change did not work" and gets debugged in the wrong place. It cost an hour
    once already. The alternative, a hand-written ?v= on each tag, only works
    while everyone remembers to bump it.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", _Revalidating(directory=str(STATIC), html=True), name="static")
