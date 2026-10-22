"""Helpers of the app tests: an HTTP client that can send any headers, the app server in a thread, a stand-in for the RCSB and job polling.

The app is src/binder_review/app. Nothing here uses a real network: every request goes to 127.0.0.1."""
from __future__ import annotations

import hashlib
import http.client
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


def find_node(minimum: int = 22) -> Optional[str]:
    """Node.js of version `minimum` or later (the browser tests use its WebSocket and fetch), or None."""
    node = shutil.which("node")
    if not node:
        return None
    try:
        major = int(subprocess.run([node, "--version"], capture_output=True, text=True, timeout=15).stdout.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return node if major >= minimum else None


def find_chrome() -> Optional[str]:
    """Google Chrome or Chromium: $CHROME_PATH, the usual places on macOS, then the PATH."""
    for c in (os.environ.get("CHROME_PATH", ""), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium",
              shutil.which("google-chrome") or "", shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if c and Path(c).is_file():
            return c
    return None


NODE = find_node()
CHROME = find_chrome()


@dataclass
class Reply:
    status: int
    headers: dict[str, str]          # lower-case names
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    @property
    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    @property
    def error(self) -> str:
        """The message of an error answer ({"error": ...})."""
        return str(self.json.get("error"))


class Client:
    """Sends requests to the app on 127.0.0.1. The Host header is the app's own unless `host` says otherwise; POST requests carry the
    X-Binderscope header unless `trusted=False`; a header given with the value None is left out."""

    def __init__(self, port: int):
        self.port = port

    def call(self, method: str, path: str, *, json_body: Any = None, body: Optional[bytes] = None, headers: Optional[dict[str, Optional[str]]] = None,
             host: Optional[str] = None, trusted: Optional[bool] = None, timeout: float = 60.0) -> Reply:
        hdrs: dict[str, Optional[str]] = {"Host": host if host is not None else f"127.0.0.1:{self.port}"}
        if (method == "POST") if trusted is None else trusted:
            hdrs["X-Binderscope"] = "1"
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers={k: v for k, v in hdrs.items() if v is not None})
            r = conn.getresponse()
            return Reply(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        finally:
            conn.close()

    def get(self, path: str, **kw: Any) -> Reply:
        return self.call("GET", path, **kw)

    def post(self, path: str, json_body: Any = None, **kw: Any) -> Reply:
        return self.call("POST", path, json_body={} if json_body is None and "body" not in kw else json_body, **kw)

    def raw(self, request: bytes, wait: float = 3.0, close_write: bool = False) -> bytes:
        """Send bytes as they are and return the answer (headers and body) or what came within `wait` seconds: for requests that
        http.client would not send, such as a body shorter than its Content-Length."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=wait) as s:
            s.sendall(request)
            if close_write:
                s.shutdown(socket.SHUT_WR)
            data = b""
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                    head, sep, rest = data.partition(b"\r\n\r\n")
                    if sep:
                        m = re.search(rb"(?i)content-length:\s*(\d+)", head)
                        if len(rest) >= (int(m.group(1)) if m else 0):
                            break
            except (socket.timeout, TimeoutError, ConnectionResetError):
                pass
            return data


@contextmanager
def running(home: Path, **kw: Any) -> Iterator[tuple[Any, Client]]:
    """The app server on a free port in a thread, with `home` as its Binderscope folder; stopped (and a running build ended) on exit."""
    from binder_review.app import server as S

    Path(home).mkdir(parents=True, exist_ok=True)
    srv = S.AppServer(("127.0.0.1", 0), Path(home), **kw)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    th.start()
    srv.thread = th
    client = Client(srv.port)
    for _ in range(100):                                       # serve_forever is running once a request is answered
        try:
            if client.get("/api/ping", timeout=2).status == 200:
                break
        except OSError:
            time.sleep(0.05)
    try:
        yield srv, client
    finally:
        end_jobs(srv)
        if th.is_alive():
            srv.shutdown()
        th.join(10)
        srv.server_close()


def end_jobs(srv: Any) -> None:
    """Stop the build that still runs (a failed test must not leave a process behind)."""
    act = srv.jobs.active()
    if act is not None:
        srv.jobs.cancel(act.id)
    for job in list(srv.jobs.jobs.values()):
        if job.proc is not None and job.proc.poll() is None:
            try:
                job.proc.kill()
                job.proc.wait(10)
            except OSError:
                pass


def wait_job(client: Client, job_id: str, timeout: float = 240.0, poll: float = 0.15) -> dict[str, Any]:
    """The last view of a build once it has stopped and the project's own record says so too (the log lines from the start are included)."""
    t0 = time.time()
    while True:
        view = client.get(f"/api/jobs/{job_id}").json
        if view["status"] != "running":
            break
        if time.time() - t0 > timeout:
            raise TimeoutError(f"build {job_id} still runs after {timeout:.0f} s; last lines:\n" + "\n".join(view["log"][-8:]))
        time.sleep(poll)
    while time.time() - t0 < timeout:                         # the project is updated a moment after the job: wait for that, too
        p = client.get(f"/api/projects/{view['slug']}")
        if p.status != 200 or p.json["status"] != "building":
            break
        time.sleep(poll / 3)
    return view


def wait_until(predicate, timeout: float = 10.0, poll: float = 0.05, what: str = "the condition") -> None:
    t0 = time.time()
    while not predicate():
        if time.time() - t0 > timeout:
            raise TimeoutError(f"{what} not true after {timeout:.0f} s")
        time.sleep(poll)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def snapshot(*roots: Path) -> dict[str, tuple[int, str]]:
    """{path: (size, sha1)} of every file below the roots: inputs must be the same after the app has worked on them."""
    out = {}
    for root in roots:
        root = Path(root)
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for p in files:
            out[str(p)] = (p.stat().st_size, hashlib.sha1(p.read_bytes()).hexdigest())
    return out


class MockRcsb:
    """A local stand-in for files.rcsb.org: GET <url>/<ID>.cif answers from `files`; an id in `broken` gets a 500, an unknown id a 404.
    The requests it received are kept in `requests` as (path, user agent)."""

    def __init__(self, files: dict[str, bytes], broken: tuple[str, ...] = ()):
        self.files = {k.upper(): v for k, v in files.items()}
        self.broken = {b.upper() for b in broken}
        self.requests: list[tuple[str, str]] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:                           # noqa: N802
                outer.requests.append((self.path, self.headers.get("User-Agent", "")))
                name = self.path.rsplit("/", 1)[-1]
                pdb = name[:-4].upper() if name.lower().endswith(".cif") else ""
                if pdb in outer.broken:
                    self.send_error(500)
                elif pdb in outer.files:
                    data = outer.files[pdb]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404)

            def log_message(self, *a: Any) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/download"

    def __enter__(self) -> "MockRcsb":
        # The app's download follows the proxy of the environment (as it should for the real RCSB on a company network); this stand-in is on 127.0.0.1, which a proxy
        # must not be asked for: the loopback addresses go on the no-proxy list for as long as it runs.
        self._env = {name: os.environ.get(name) for name in ("NO_PROXY", "no_proxy")}
        for name, old in self._env.items():
            os.environ[name] = ",".join(x for x in ("127.0.0.1", "localhost", old or "") if x)
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.httpd.shutdown()
        self.thread.join(5)
        self.httpd.server_close()
        for name, old in self._env.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
