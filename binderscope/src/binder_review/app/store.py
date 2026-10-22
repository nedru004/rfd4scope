"""Projects of the app: one folder per project below the Binderscope home folder.

    <home>/<slug>/project.app.json   what was chosen in the import page, and how the last build went
    <home>/<slug>/work/              the analysis project written by ``binder-review import`` (caches and export)
    <home>/<slug>/bundle/            the review pages
    <home>/<slug>/inputs/            copies of files that were dropped or downloaded into the app (never of run folders)
    <home>/<slug>/build.log          the log of the last build
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SCHEMA = "binderscope.app.project/1"
META = "project.app.json"
EVENTS = "review-events.json"


def home_dir() -> Path:
    """The Binderscope folder: $BINDERSCOPE_HOME, else ~/Binderscope (created on demand)."""
    p = Path(os.environ.get("BINDERSCOPE_HOME") or Path.home() / "Binderscope").expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def now() -> str:
    """A local timestamp with a colon in the offset (2026-10-02T04:26:18-05:00), which every browser parses."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower())
    s = re.sub(r"\.{2,}", "-", s).strip("-._")                  # a run of dots would look like a parent folder
    s = re.sub(r"-{2,}", "-", s)
    return (s[:60].strip("-._")) or "project"


def unique_slug(home: Path, name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while (home / slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def valid_slug(slug: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,59}", slug or "")) and ".." not in slug


def project_dir(home: Path, slug: str) -> Path:
    if not valid_slug(slug):
        raise KeyError(slug)
    d = home / slug
    if not (d / META).is_file():
        raise KeyError(slug)
    return d


def read_meta(d: Path) -> dict[str, Any]:
    return json.loads((d / META).read_text(encoding="utf-8"))


def write_meta(d: Path, meta: dict[str, Any]) -> None:
    meta["updated"] = now()
    tmp = d / f"{META}.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, d / META)


def create(home: Path, name: str, inputs: dict[str, Any]) -> tuple[str, Path]:
    slug = unique_slug(home, name)
    d = home / slug
    d.mkdir(parents=True)
    meta = {"schema": SCHEMA, "name": name.strip() or slug, "slug": slug, "created": now(), "updated": now(), "inputs": inputs,
            "build": {"status": "new"}}
    write_meta(d, meta)
    return slug, d


def summary(d: Path, version: str) -> dict[str, Any]:
    """What the project list shows."""
    meta = read_meta(d)
    b = meta.get("build", {})
    bundle = d / "bundle"
    ready = (bundle / "index.html").is_file()
    status = b.get("status", "new")
    if status == "ready" and not ready:
        status = "missing"
    from . import BUILD_REVISION

    runs = [r.get("id") for r in meta.get("inputs", {}).get("runs", [])]
    rev = int(b.get("revision") or 1)
    return {"slug": meta["slug"], "name": meta["name"], "created": meta.get("created"), "updated": meta.get("updated"),
            "status": status, "ready": ready, "runs": runs, "counts": b.get("counts"), "highlights": (b.get("highlights") or [])[:3],
            "error": b.get("error"), "seconds": b.get("seconds"), "version": b.get("version"),
            "outdated": bool(ready and status == "ready" and ((b.get("version") and b.get("version") != version) or rev < BUILD_REVISION)),
            "rebuild_needed": bool(ready and status == "ready" and rev < BUILD_REVISION),
            "finished": b.get("finished"), "folder": str(d)}


def list_projects(home: Path, version: str) -> list[dict[str, Any]]:
    out = []
    for d in sorted(home.iterdir()) if home.is_dir() else []:
        if d.is_dir() and (d / META).is_file():
            try:
                out.append(summary(d, version))
            except (OSError, ValueError, KeyError):
                continue
    out.sort(key=lambda p: p.get("updated") or "", reverse=True)
    return out


def delete(home: Path, slug: str) -> None:
    d = project_dir(home, slug)
    if home.resolve() not in d.resolve().parents:
        raise ValueError("not a project folder of this app")
    shutil.rmtree(d)


def uploads_dir(home: Path) -> Path:
    d = home / "_uploads"
    d.mkdir(exist_ok=True)
    return d


def clean_name(name: str, default: str = "file") -> str:
    """A file name without folders or odd characters (uploads)."""
    base = os.path.basename(name.replace("\\", "/")) or default
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .") or default
    return base[:120]


def _unique(dest_dir: Path, name: str, role: str) -> Path:
    base = f"{role}-{name}" if role and not name.startswith(role + "-") else name       # a copy of a copy keeps the name it had
    dest, n = dest_dir / base, 2
    while dest.exists():
        stem, dot, ext = base.rpartition(".")
        dest = dest_dir / (f"{stem}-{n}.{ext}" if dot else f"{base}-{n}")
        n += 1
    return dest


def adopt(d: Path, path: str, role: str = "") -> str:
    """Make a file that lives in the app's own folders part of this project: an upload is moved into the project's inputs/ (nothing is kept twice),
    a file from another project's inputs/ (Edit the inputs) is copied, so deleting that project never breaks this one. Other paths stay as they are."""
    p = Path(path)
    home = d.parent.resolve()
    try:
        rp = p.resolve()
    except OSError:
        return path
    if not rp.is_file() or home not in rp.parents:
        return path
    rel = rp.relative_to(home).parts
    if rp.parents[0] == (d / "inputs").resolve() or (len(rel) > 1 and rel[0] == d.name):
        return path                                           # already in this project
    from_upload = rel[0] == "_uploads"
    from_inputs = len(rel) > 1 and rel[1] == "inputs"
    if not (from_upload or from_inputs):
        return path
    dest_dir = d / "inputs"
    dest_dir.mkdir(exist_ok=True)
    dest = _unique(dest_dir, rp.name, role)
    if from_upload:
        shutil.move(str(rp), str(dest))
        try:
            rp.parent.rmdir()                                  # the upload's own folder
        except OSError:
            pass
    else:
        shutil.copy2(rp, dest)
    return str(dest)


def clean_uploads(home: Path, days: float = 7.0) -> None:
    """Uploads that no project adopted (the page was left before building) are removed after a week."""
    up = home / "_uploads"
    if not up.is_dir():
        return
    cutoff = time.time() - days * 86400
    for c in up.iterdir():
        try:
            if c.is_dir() and c.stat().st_mtime < cutoff:
                shutil.rmtree(c, ignore_errors=True)
        except OSError:
            continue


def latest_log(d: Path, n: int = 80) -> Optional[list[str]]:
    f = d / "build.log"
    if not f.is_file():
        return None
    return f.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


_EVENT_LOCK = threading.Lock()


def merge_events(d: Path, incoming: list[dict[str, Any]]) -> int:
    """Add the decisions of a review page to the project's saved log (union by event id, oldest first). Returns how many events are kept."""
    f = d / EVENTS
    with _EVENT_LOCK:                                          # two pages (or tabs) may save at the same moment
        try:
            have = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []
        except (OSError, ValueError):
            have = []
        seen, out = set(), []
        for e in list(have) + list(incoming):
            if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"] not in seen:
                seen.add(e["id"])
                out.append(e)
        out.sort(key=lambda e: str(e.get("ts") or ""))
        tmp = d / f"{EVENTS}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    return len(out)


def mark_interrupted(home: Path) -> int:
    """Builds that were still running when the app stopped are shown as interrupted (the analysis caches make a rebuild quick). Returns how many."""
    n = 0
    for d in home.iterdir() if home.is_dir() else []:
        if d.is_dir() and (d / META).is_file():
            try:
                meta = read_meta(d)
                b = meta.get("build", {})
                if b.get("status") == "building":
                    meta["build"] = {**b, "status": "failed", "error": "The build was interrupted when Binderscope was closed. Rebuild to continue: the finished steps are kept.", "error_kind": "crash"}
                    write_meta(d, meta)
                    n += 1
            except (OSError, ValueError, KeyError):
                continue
    return n
