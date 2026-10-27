"""The local server behind the Binderscope app (docs/app.md).

It listens on 127.0.0.1 only, answers requests whose Host header names this computer, and refuses state-changing requests that do not
come from its own pages (a custom header, and an Origin that is its own). The API:

    GET  /api/ping                        {app, version}
    GET  /api/state                       {version, home, native_dialogs, projects, job}
    POST /api/pick                        {kind: folder|file, multiple, prompt, default} -> {paths} | {cancelled} | 501
    GET  /api/ls?path=                    folder listing for the page's own file browser
    GET  /api/inspect-run?path=           what a run folder holds
    GET  /api/check-structure?path=&against=   chains of a structure file, the chain that matches the construct
    POST /api/upload?name=                a file body saved to <home>/_uploads (target or reference structure dropped on the page)
    POST /api/fetch-reference {id}        download a PDB entry (mmCIF) from the RCSB when asked to
    POST /api/projects                    {name, inputs} -> creates the project and starts the build -> {slug, job}
    POST /api/projects/<slug>/rebuild     {no_cache}
    POST /api/projects/<slug>/update-pages
    POST /api/projects/<slug>/delete | /reveal
    GET|POST /api/projects/<slug>/state   the decisions saved from the review pages (a union of events by id), kept in <project>/review-events.json
    GET  /api/projects/<slug>             project summary and its inputs
    GET  /api/jobs/<id>?since=N           progress and the log lines from N
    POST /api/jobs/<id>/cancel
    POST /api/reveal-home                 show the Binderscope folder in the Finder
    POST /api/quit
    GET  /p/<slug>/...                    the review pages of a project (a small bar is added to HTML pages)
    GET  /_app/<file>                     the app's own pages and assets
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from .. import __version__
from . import APP_NAME, BUILD_REVISION, DEFAULT_PORT, dialogs, jobs, probe, store

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 300 * 1024 * 1024
MIME = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".pdb": "text/plain; charset=utf-8", ".cif": "text/plain; charset=utf-8",
        ".csv": "text/csv; charset=utf-8", ".fasta": "text/plain; charset=utf-8", ".jsonl": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
        ".png": "image/png", ".woff2": "font/woff2", ".ico": "image/x-icon"}
APPBAR = b'<script src="/_app/appbar.js" defer></script>'


def _rcsb_url(pdb_id: str) -> str:
    base = os.environ.get("BINDERSCOPE_RCSB_URL", "https://files.rcsb.org/download")
    return f"{base.rstrip('/')}/{pdb_id.upper()}.cif"


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], home: Path, quit_after: Optional[float] = None, verbose: bool = False):
        super().__init__(address, Handler)
        self.home = home
        self.port = self.server_address[1]
        self.verbose = verbose
        store.mark_interrupted(home)
        store.clean_uploads(home)
        self.jobs = jobs.JobManager(self._job_finished)
        self.last_seen = time.monotonic()
        self.seen_any = False
        self.quit_after = quit_after
        self._lock = threading.Lock()
        if quit_after:
            threading.Thread(target=self._idle_watch, daemon=True).start()

    # ------------------------------------------------------------------ housekeeping
    def touch(self) -> None:
        self.last_seen = time.monotonic()
        self.seen_any = True

    def _idle_watch(self) -> None:
        while True:
            time.sleep(5)
            if self.seen_any and not self.jobs.active() and time.monotonic() - self.last_seen > (self.quit_after or 0):
                threading.Thread(target=self.shutdown, daemon=True).start()
                return

    def _job_finished(self, job: jobs.Job) -> None:
        try:
            d = store.project_dir(self.home, job.slug)
        except KeyError:
            return
        meta = store.read_meta(d)
        b = meta.setdefault("build", {})
        b["finished"] = store.now()
        b["seconds"] = round(job.finished - job.started, 1) if job.finished else None
        b["job"] = job.id
        if job.status == "done":
            b["status"], b["error"] = "ready", None
            if job.kind == "build":
                b["revision"] = BUILD_REVISION                        # a pages-only update does not make the analysis newer
            pj = d / "work" / "project.json"
            try:
                info = json.loads(pj.read_text(encoding="utf-8"))
                b.update({"version": info.get("version"), "counts": info.get("counts"), "highlights": info.get("highlights", []), "warnings": len(info.get("warnings", []))})
            except (OSError, ValueError):
                b["version"] = __version__
            if job.kind == "bundle":
                b["version"] = __version__
        elif job.status == "cancelled":
            b["status"], b["error"] = "cancelled", "The build was cancelled."
        else:
            b["status"], b["error"], b["error_kind"] = "failed", job.error, job.error_kind
        store.write_meta(d, meta)

    # ------------------------------------------------------------------ actions
    def start_build(self, slug: str, no_cache: bool = False) -> jobs.Job:
        d = store.project_dir(self.home, slug)
        meta = store.read_meta(d)
        cmd = jobs.import_command(meta["inputs"], d, meta["name"], no_cache=no_cache)
        job = self.jobs.start("build", slug, cmd, d, jobs.STAGES, d / "build.log")
        meta["build"] = {"status": "building", "started": store.now(), "job": job.id}
        store.write_meta(d, meta)
        return job

    def start_pages(self, slug: str) -> jobs.Job:
        d = store.project_dir(self.home, slug)
        if not (d / "work" / "export").is_dir():
            raise ValueError("this project has no analysis yet: rebuild it")
        job = self.jobs.start("bundle", slug, jobs.bundle_command(d), d, jobs.BUNDLE_ONLY_STAGES, d / "build.log")
        meta = store.read_meta(d)
        meta.setdefault("build", {})["status"] = "building"
        store.write_meta(d, meta)
        return job

    def state(self) -> dict[str, Any]:
        job = self.jobs.active()
        return {"app": APP_NAME, "version": __version__, "home": str(self.home), "native_dialogs": dialogs.available(), "platform": sys.platform,
                "projects": store.list_projects(self.home, __version__), "job": job.view() if job else None}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Handler(BaseHTTPRequestHandler):
    server: AppServer
    server_version = "Binderscope"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.server.verbose:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------------ plumbing
    def _origin_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        names = {f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}", f"[::1]:{self.server.port}"}
        return host in names

    def _send(self, status: int, body: bytes, ctype: str, extra: Optional[dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _fail(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 25_000_000:
            raise ApiError(413, "The request is too large.")
        raw = self.rfile.read(n) if n else b""
        try:
            obj = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise ApiError(400, "The request is not valid JSON.")
        if not isinstance(obj, dict):
            raise ApiError(400, "The request must be a JSON object.")
        return obj

    def _post_allowed(self) -> bool:
        if not self.headers.get("X-Binderscope"):
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{self.server.port}", f"http://localhost:{self.server.port}"):
            return False
        site = self.headers.get("Sec-Fetch-Site")
        return site in (None, "same-origin", "none")

    # ------------------------------------------------------------------ routing
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._origin_ok():
            return self._fail(403, "This address is not allowed.")
        self.server.touch()
        url = urllib.parse.urlsplit(self.path)
        path, query = urllib.parse.unquote(url.path), urllib.parse.parse_qs(url.query)
        try:
            if path.startswith("/api/"):
                return self._api_get(path[5:], query)
            if path == "/":
                return self._static("index.html")
            if path in ("/new", "/new/"):
                return self._static("new.html")
            if path.startswith("/_app/"):
                return self._static(path[6:])
            if path.startswith("/p/"):
                return self._project_file(path[3:])
            if path == "/favicon.ico":
                return self._static("binderscope-mark.svg")
            self._fail(404, "Not found.")
        except ApiError as ex:
            self._fail(ex.status, ex.message)
        except KeyError:
            self._fail(404, "There is no such project.")
        except ValueError:
            self._fail(400, "A value in the request is not valid (for example a path with a null character).")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as ex:
            self._fail(500, f"Something went wrong inside Binderscope: {ex}")

    def do_POST(self) -> None:
        if not self._origin_ok():
            return self._fail(403, "This address is not allowed.")
        if not self._post_allowed():
            return self._fail(403, "This request did not come from the Binderscope page.")
        self.server.touch()
        url = urllib.parse.urlsplit(self.path)
        path, query = urllib.parse.unquote(url.path), urllib.parse.parse_qs(url.query)
        try:
            if not path.startswith("/api/"):
                return self._fail(404, "Not found.")
            self._api_post(path[5:], query)
        except ApiError as ex:
            self._fail(ex.status, ex.message)
        except KeyError:
            self._fail(404, "There is no such project.")
        except ValueError:
            self._fail(400, "A value in the request is not valid (for example a path with a null character).")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as ex:                                  # never leave a request hanging
            self._fail(500, f"Something went wrong inside Binderscope: {ex}")

    # ------------------------------------------------------------------ static files
    def _static(self, name: str) -> None:
        name = name.lstrip("/")
        f = STATIC / name
        if not name or ".." in name or "/" in name or not f.is_file():
            from ..viewer_build import viewer_dir
            vd = viewer_dir()
            alt = {"shared.css": vd / "css" / "shared.css", "binderscope-mark.svg": vd / "assets" / "binderscope-mark.svg"}.get(name)
            if not alt or not alt.is_file():
                return self._fail(404, "Not found.")
            f = alt
        self._send(200, f.read_bytes(), MIME.get(f.suffix.lower()) or mimetypes.guess_type(str(f))[0] or "application/octet-stream")

    def _project_file(self, rest: str) -> None:
        slug, sep, sub = rest.partition("/")
        try:
            d = store.project_dir(self.server.home, slug)
        except KeyError:
            return self._fail(404, "There is no such project.")
        root = (d / "bundle").resolve()
        if not sep:
            return self._send(301, b"", "text/plain", {"Location": f"/p/{slug}/"})
        sub = sub or "index.html"
        target = (root / sub).resolve()
        if root != target and root not in target.parents:
            return self._fail(403, "Not allowed.")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return self._fail(404, "Not found." if root.is_dir() else "This project has no pages yet: build it first.")
        ctype = MIME.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        size = target.stat().st_size
        if target.suffix.lower() == ".html":
            body = target.read_bytes()
            i = body.rfind(b"</body>")
            body = body[:i] + APPBAR + body[i:] if i >= 0 else body + APPBAR
            return self._send(200, body, ctype)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            with target.open("rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 256 * 1024)

    # ------------------------------------------------------------------ API: GET
    def _api_get(self, route: str, q: dict[str, list[str]]) -> None:
        arg = lambda k, d="": (q.get(k) or [d])[0]            # noqa: E731
        if route == "ping":
            return self._json({"app": APP_NAME, "version": __version__})
        if route == "state":
            return self._json(self.server.state())
        if route == "ls":
            return self._json(self._ls(arg("path") or str(Path.home())))
        if route == "inspect-run":
            return self._json(probe.inspect_run(arg("path")))
        if route == "check-structure":
            return self._json(probe.check_structure(arg("path"), arg("against") or None, arg("chain") or None))
        m = re.fullmatch(r"projects/([^/]+)/state", route)
        if m:
            d = store.project_dir(self.server.home, m.group(1))
            f = d / store.EVENTS
            return self._json({"events": f.read_text(encoding="utf-8") if f.is_file() else None})
        if route.startswith("projects/"):
            slug = route.split("/", 1)[1]
            d = store.project_dir(self.server.home, slug)
            meta = store.read_meta(d)
            return self._json({**store.summary(d, __version__), "inputs": meta.get("inputs"), "log": store.latest_log(d)})
        if route.startswith("jobs/"):
            job = self.server.jobs.get(route.split("/", 1)[1])
            if not job:
                raise ApiError(404, "There is no such build.")
            try:
                since = max(0, int(arg("since", "0") or 0))
            except ValueError:
                since = 0
            return self._json(job.view(since))
        raise ApiError(404, "Not found.")

    def _ls(self, path: str) -> dict[str, Any]:
        p = Path(path).expanduser()
        if not p.is_dir():
            p = p.parent if p.parent.is_dir() else Path.home()
        entries = []
        try:
            for c in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if c.name.startswith("."):
                    continue
                try:
                    entries.append({"name": c.name, "dir": c.is_dir(), "path": str(c)})
                except OSError:
                    continue
        except OSError as ex:
            raise ApiError(403, f"Cannot read this folder: {ex.strerror or ex}")
        return {"path": str(p.resolve()), "parent": str(p.resolve().parent) if p.resolve() != p.resolve().parent else None, "entries": entries[:2000], "home": str(Path.home())}

    # ------------------------------------------------------------------ API: POST
    def _api_post(self, route: str, q: dict[str, list[str]]) -> None:
        srv = self.server
        if route == "upload":
            return self._upload(q)
        body = self._body_json()
        if route == "pick":
            try:
                paths = dialogs.pick(str(body.get("kind") or "folder"), str(body.get("prompt") or "Choose"), bool(body.get("multiple")), body.get("default"))
            except RuntimeError as ex:
                return self._fail(501, str(ex))
            return self._json({"cancelled": True} if paths is None else {"paths": paths})
        if route == "fetch-reference":
            return self._fetch_reference(str(body.get("id") or ""))
        if route == "reveal-home":
            dialogs.reveal(srv.home)
            return self._json({"ok": True})
        if route == "quit":
            self._json({"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.3), srv.shutdown()), daemon=True).start()
            return
        if route == "projects":
            return self._create_project(body)
        m = re.fullmatch(r"projects/([^/]+)/state", route)
        if m:
            d = store.project_dir(srv.home, m.group(1))
            try:
                incoming = json.loads(str(body.get("events") or "[]"))
            except ValueError:
                raise ApiError(400, "The events are not valid JSON.")
            if not isinstance(incoming, list) or not all(isinstance(e, dict) and isinstance(e.get("id"), str) for e in incoming):
                raise ApiError(400, "The events must be a list of objects with an id.")
            return self._json({"count": store.merge_events(d, incoming)})
        m = re.fullmatch(r"projects/([^/]+)/(rebuild|update-pages|delete|reveal)", route)
        if m:
            slug, action = m.groups()
            d = store.project_dir(srv.home, slug)
            if action == "delete":
                if srv.jobs.active() and srv.jobs.active().slug == slug:          # type: ignore[union-attr]
                    raise ApiError(409, "This project is being built: cancel the build first.")
                store.delete(srv.home, slug)
                return self._json({"ok": True})
            if action == "reveal":
                dialogs.reveal(d)
                return self._json({"ok": True})
            if srv.jobs.active():
                raise ApiError(409, "Another build is running: wait for it to finish.")
            try:
                job = srv.start_build(slug, no_cache=bool(body.get("no_cache"))) if action == "rebuild" else srv.start_pages(slug)
            except ValueError as ex:
                raise ApiError(400, str(ex))
            return self._json({"slug": slug, "job": job.view()})
        m = re.fullmatch(r"jobs/([^/]+)/cancel", route)
        if m:
            return self._json({"ok": srv.jobs.cancel(m.group(1))})
        raise ApiError(404, "Not found.")

    def _upload(self, q: dict[str, list[str]]) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_UPLOAD:
            raise ApiError(413, "The file is empty or too large (limit 300 MB).")
        name = store.clean_name((q.get("name") or ["file"])[0])
        dest_dir = store.uploads_dir(self.server.home) / os.urandom(4).hex()
        dest_dir.mkdir()
        dest = dest_dir / name
        left = n
        with dest.open("wb") as fh:
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                fh.write(chunk)
                left -= len(chunk)
        if left:
            dest.unlink(missing_ok=True)
            raise ApiError(400, "The upload was cut short.")
        self._json({"path": str(dest), "name": name})

    def _fetch_reference(self, pdb_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9]{4}", pdb_id):
            raise ApiError(400, "A PDB id has four characters, for example 1abc.")
        dest_dir = store.uploads_dir(self.server.home) / os.urandom(4).hex()
        dest_dir.mkdir()
        dest = dest_dir / f"{pdb_id.lower()}.cif"
        try:
            with urllib.request.urlopen(urllib.request.Request(_rcsb_url(pdb_id), headers={"User-Agent": f"binderscope/{__version__}"}), timeout=60) as r:
                data = r.read(60 * 1024 * 1024)
        except urllib.error.HTTPError as ex:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise ApiError(404 if ex.code == 404 else 502, f"The RCSB has no entry {pdb_id.upper()}." if ex.code == 404 else f"The RCSB answered {ex.code}.")
        except (urllib.error.URLError, OSError) as ex:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise ApiError(502, f"Could not reach the RCSB ({getattr(ex, 'reason', ex)}). Check the connection, or choose a file instead.")
        dest.write_bytes(data)
        self._json({"path": str(dest), "name": dest.name, "id": pdb_id.lower()})

    def _create_project(self, body: dict[str, Any]) -> None:
        srv = self.server
        name = str(body.get("name") or "").strip()
        inputs = body.get("inputs")
        if not name or not isinstance(inputs, dict):
            raise ApiError(400, "Give the project a name and choose its inputs.")
        runs_in = inputs.get("runs")
        if not isinstance(runs_in, list) or not runs_in:
            raise ApiError(400, "Add at least one design run folder.")
        runs, seen = [], set()
        for r in runs_in:
            if not isinstance(r, dict):
                raise ApiError(400, "Each run must have an id and a folder.")
            rid, path = str(r.get("id") or "").strip(), str(r.get("path") or "")
            if not jobs.run_id_ok(rid):
                raise ApiError(400, f"The run name {rid!r} may use letters, digits, dot, dash and underscore only.")
            if rid in seen:
                raise ApiError(400, f"Two runs are called {rid}: give each run its own name.")
            seen.add(rid)
            if not Path(path).expanduser().is_dir():
                raise ApiError(400, f"The run folder {path} does not exist.")
            for k in ("settings",):
                if r.get(k) and not Path(str(r[k])).expanduser().is_file():
                    raise ApiError(400, f"The file {r[k]} does not exist.")
            runs.append({"id": rid, "path": str(Path(path).expanduser().resolve()), "label": (str(r["label"]).strip() or None) if r.get("label") else None,
                         "settings": str(Path(str(r["settings"])).expanduser()) if r.get("settings") else None})
        for key, label in (("target_pdb", "the target structure"), ("reference", "the full-length reference structure")):
            if not inputs.get(key) or not Path(str(inputs[key])).expanduser().is_file():
                raise ApiError(400, f"Choose {label}.")
        if srv.jobs.active():
            raise ApiError(409, "Another build is running: wait for it to finish.")
        clean = {"runs": runs, "target_pdb": str(Path(str(inputs["target_pdb"])).expanduser()), "reference": str(Path(str(inputs["reference"])).expanduser()),
                 "reference_id": (str(inputs.get("reference_id") or "").strip() or None), "reference_chain": (str(inputs.get("reference_chain") or "A").strip() or "A"),
                 "partner_chains": (str(inputs.get("partner_chains") or "auto").strip() or "auto"), "uniprot": (str(inputs.get("uniprot") or "").strip() or None),
                 "profile": (str(inputs.get("profile")).strip() if inputs.get("profile") else None), "online": bool(inputs.get("online")), "consurf": bool(inputs.get("consurf")),
                 "placement": (str(inputs.get("placement")) if inputs.get("placement") in ("interface", "whole", "core") else "interface")}
        for r in runs:
            rp = Path(r["path"]).resolve()
            if rp == srv.home.resolve() or rp in srv.home.resolve().parents:
                raise ApiError(400, "The Binderscope folder lies inside this run folder. Move the Binderscope folder or choose another run folder.")
        retry = str(body.get("retry") or "")
        reuse = None
        if retry:                                           # a failed or cancelled build is run again in the same folder instead of making another project
            try:
                rd = store.project_dir(srv.home, retry)
                if store.read_meta(rd).get("build", {}).get("status") in ("failed", "cancelled", "new"):
                    reuse = (retry, rd)
            except KeyError:
                reuse = None
        slug, d = reuse if reuse else store.create(srv.home, name, clean)
        moved: list[tuple[str, str]] = []                        # (where the file was, where the project keeps it)
        try:
            for key, role in (("target_pdb", "target"), ("reference", "reference"), ("profile", "profile")):
                if clean.get(key):
                    before = clean[key]
                    clean[key] = store.adopt(d, before, role)
                    if clean[key] != before:
                        moved.append((before, clean[key]))
            meta = store.read_meta(d)
            meta["inputs"], meta["name"] = clean, name
            store.write_meta(d, meta)
            job = srv.start_build(slug)
        except Exception:
            for orig, kept in moved:                             # a dropped or downloaded file goes back where the page still expects it
                try:
                    if Path(kept).is_file() and not Path(orig).exists():
                        Path(orig).parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(kept, orig)
                except OSError:
                    pass
            if not reuse:                                        # no half-made project is left behind
                shutil.rmtree(d, ignore_errors=True)
            raise
        self._json({"slug": slug, "job": job.view(), "inputs": clean}, 201)


# ---------------------------------------------------------------------------------------------------------------------
# starting the app
# ---------------------------------------------------------------------------------------------------------------------
def _ping(port: int) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))       # a system or environment proxy must not intercept the local address
        with opener.open(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return json.loads(r.read().decode()).get("app") == APP_NAME
    except (OSError, ValueError):
        return False


CHROMIUM = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser", "/Applications/Chromium.app/Contents/MacOS/Chromium"]


def open_window(url: str, mode: str = "auto") -> str:
    """Open the app: in its own window when a Chromium-based browser is installed (``app`` mode), otherwise in the default browser."""
    if mode in ("auto", "app"):
        for exe in CHROMIUM:
            if Path(exe).is_file():
                subprocess.Popen([exe, f"--app={url}", "--window-size=1440,920"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return "app window"
    import webbrowser
    webbrowser.open(url)
    return "browser"


def run(port: int = DEFAULT_PORT, home: Optional[Path] = None, open_mode: str = "auto", quit_when_closed: bool = False, verbose: bool = False) -> int:
    home = Path(home).expanduser().resolve() if home else store.home_dir()
    home.mkdir(parents=True, exist_ok=True)
    if _ping(port):                                          # already running: show it instead of starting a second server
        url = f"http://127.0.0.1:{port}/"
        if open_mode != "none":
            open_window(url, open_mode)
        print(f"Binderscope is already running at {url}")
        return 0
    server = None
    for p in range(port, port + 25):
        try:
            server = AppServer(("127.0.0.1", p), home, quit_after=75.0 if quit_when_closed else None, verbose=verbose)
            break
        except OSError:
            continue
    if server is None:
        print(f"binder-review app: no free port from {port} to {port + 24}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.port}/"
    print(f"Binderscope {__version__} is running at {url}  (projects in {home}; press Ctrl+C to stop)", flush=True)
    if server.port != port:
        print(f"note: port {port} is in use, Binderscope uses {server.port} instead", file=sys.stderr)
    if open_mode != "none":
        threading.Thread(target=lambda: (time.sleep(0.4), open_window(url, open_mode)), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        act = server.jobs.active()
        if act and act.proc:
            try:
                act.proc.terminate()
            except OSError:
                pass
        server.server_close()
    return 0
