"""The Binderscope app server (binder_review.app.server) without a browser, on the toy runs.

The server runs in a thread of this process on a free port with a temporary Binderscope folder; builds are the real thing (a
`python -m binder_review import` process per build, a few seconds on the toy runs). Nothing opens a native dialog or a Finder window
(both are replaced), and the only network is 127.0.0.1: the RCSB is a small local server.

What is checked: the access rules (host names, the X-Binderscope header, origins, files outside a bundle), the input checks of a
new project, a build from start to finish (progress, summary, the pages with the bar added at serve time only), failed builds and
retries, uploads, the RCSB download, cancel, rebuild, update-pages, saved decisions, delete, the file browser and probes, quitting
and starting the app.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import gemmi
import pytest
from toydata import appkit
from toydata.variants import scrambled_chain_b, short_chain_b

from binder_review import __version__, cli
from binder_review.app import BUILD_REVISION, dialogs, jobs, server, store

REAL_PICK = dialogs.pick


def enc(path) -> str:
    """A path or a name for a query string."""
    return quote(str(path))


APPBAR = server.APPBAR
PAGES = ("index", "board", "inspector", "map", "query")


# ---------------------------------------------------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def revealed(monkeypatch):
    """Nothing here may open a native file dialog or a Finder window: dialogs are replaced; the folders that were to be shown are recorded."""
    shown: list[Path] = []

    def refuse(*a, **k):
        raise AssertionError("a test opened a native file dialog")

    monkeypatch.setattr(dialogs, "pick", refuse)
    monkeypatch.setattr(dialogs, "reveal", lambda path: shown.append(Path(path)))
    return shown


@pytest.fixture(autouse=True)
def no_real_rcsb(monkeypatch):
    """A download that a test did not prepare goes to a closed local port, never to the real RCSB."""
    monkeypatch.setenv("BINDERSCOPE_RCSB_URL", f"http://127.0.0.1:{appkit.free_port()}/download")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    """One app server for the module: (srv, api, home, port)."""
    home = tmp_path_factory.mktemp("app") / "Binderscope"
    with appkit.running(home) as (srv, api):
        yield SimpleNamespace(srv=srv, api=api, home=home, port=srv.port)


@pytest.fixture(scope="module")
def inputs(toy_dir):
    """What the import page sends for the toy project: two runs, the construct and the full-length reference (paths, nothing uploaded)."""
    return {"runs": [{"id": "run01", "path": str(toy_dir / "runs" / "run01")}, {"id": "run02", "path": str(toy_dir / "runs" / "run02")}],
            "target_pdb": str(toy_dir / "construct" / "toy_construct.pdb"), "reference": str(toy_dir / "reference" / "toy_target.cif")}


def one_run(inputs):
    """The same inputs with run02 only (a build of about 5 s instead of 11 s)."""
    return {**inputs, "runs": [inputs["runs"][1]]}


@pytest.fixture(scope="module")
def toy_project(app, inputs, toy_dir):
    """The two toy runs built once through the API: the answer of POST /api/projects, the finished job and a snapshot of the input files."""
    before = appkit.snapshot(toy_dir / "runs", toy_dir / "construct", toy_dir / "reference")
    r = app.api.post("/api/projects", {"name": "Toy demo", "inputs": inputs})
    assert r.status == 201, r.text
    job = appkit.wait_job(app.api, r.json["job"]["id"])
    assert job["status"] == "done", "\n".join(job["log"][-15:])
    return SimpleNamespace(slug=r.json["slug"], first=r.json, job=job, before=before, dir=app.home / r.json["slug"])


def homes_folders(app) -> set[str]:
    return {p.name for p in app.home.iterdir()}


# ---------------------------------------------------------------------------------------------------------------- the pages and the access rules
def test_ping_state_and_the_app_pages(app):
    api = app.api
    assert api.get("/api/ping").json == {"app": "binderscope", "version": __version__}
    st = api.get("/api/state").json
    assert st["app"] == "binderscope" and st["version"] == __version__ and st["home"] == str(app.home) and st["platform"] == sys.platform
    assert st["native_dialogs"] is dialogs.available() and isinstance(st["projects"], list) and st["job"] is None
    for path, ctype, marker in (("/", "text/html", b'id="welcome"'), ("/new", "text/html", b'id="build"'), ("/new/", "text/html", b'id="build"'),
                                ("/_app/app.css", "text/css", b".ahdr"), ("/_app/app.js", "text/javascript", b"BSA"),
                                ("/_app/shared.css", "text/css", b"--font-ui"), ("/_app/binderscope-mark.svg", "image/svg+xml", b"<svg"),
                                ("/favicon.ico", "image/svg+xml", b"<svg")):
        r = api.get(path)
        assert r.status == 200 and r.headers["content-type"].startswith(ctype) and marker in r.body, path
        assert r.headers["cache-control"] == "no-cache" and r.headers["x-content-type-options"] == "nosniff", path
    for path in ("/nope", "/_app/nope.js", "/_app/", "/index.html", "/api/nope"):
        r = api.get(path)
        assert r.status == 404 and r.error, path
    head = api.call("HEAD", "/")
    assert head.status == 200 and head.body == b"" and int(head.headers["content-length"]) == len(api.get("/").body)


def test_the_app_pages_load_nothing_from_the_web():
    static = Path(server.__file__).resolve().parent / "static"
    pages = [p for p in static.iterdir() if p.suffix in (".html", ".js", ".css")]
    assert {p.name for p in pages} >= {"index.html", "new.html", "app.js", "home.js", "new.js", "appbar.js", "app.css"}
    for p in pages:
        assert not re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", p.read_text(encoding="utf-8")), p.name


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "127.0.0.1", "localhost", "127.0.0.1.evil.example:{port}", "127.0.0.1:1", "[::2]:{port}", ""])
def test_requests_that_do_not_name_this_computer_are_refused(app, host):
    host = host.format(port=app.port)
    for method, path in (("GET", "/api/ping"), ("GET", "/"), ("GET", "/api/state"), ("GET", "/api/ls?path=/"), ("POST", "/api/quit"), ("GET", "/p/any/")):
        r = app.api.call(method, path, host=host, json_body={} if method == "POST" else None)
        assert r.status == 403 and "not allowed" in r.error, (method, path)


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "LocalHost:{port}", "[::1]:{port}"])
def test_the_names_of_this_computer_are_accepted(app, host):
    assert app.api.get("/api/ping", host=host.format(port=app.port)).status == 200


def test_posts_need_the_binderscope_header_and_an_own_origin(app, inputs):
    own = f"http://127.0.0.1:{app.port}"
    refused = [dict(trusted=False), dict(headers={"X-Binderscope": ""}), dict(headers={"Origin": "http://evil.example"}), dict(headers={"Origin": "null"}),
               dict(headers={"Origin": f"http://127.0.0.1:{app.port + 1}"}), dict(headers={"Origin": own, "Sec-Fetch-Site": "cross-site"}),
               dict(headers={"Sec-Fetch-Site": "same-site"}), dict(headers={"Origin": "https://127.0.0.1:%d" % app.port})]
    before = homes_folders(app)
    for kw in refused:
        for path, body in (("/api/jobs/x/cancel", {}), ("/api/projects", {"name": "Sneaky", "inputs": inputs}), ("/api/quit", {}),
                           ("/api/reveal-home", {}), ("/api/projects/any/delete", {})):
            r = app.api.post(path, body, **kw)
            assert r.status == 403 and "did not come from the Binderscope page" in r.error, (kw, path)
    assert homes_folders(app) == before and app.srv.jobs.active() is None
    for kw in (dict(), dict(headers={"Origin": own}), dict(headers={"Origin": f"http://localhost:{app.port}"}), dict(headers={"Sec-Fetch-Site": "same-origin"}),
               dict(headers={"Origin": own, "Sec-Fetch-Site": "none"})):
        assert app.api.post("/api/jobs/x/cancel", {}, **kw).json == {"ok": False}, kw


def test_no_cross_origin_access_and_no_other_methods(app):
    for kw in (dict(), dict(host="evil.example")):
        r = app.api.get("/api/ping", headers={"Origin": "http://evil.example"}, **kw)
        assert not [h for h in r.headers if h.startswith("access-control-")]
    for method in ("OPTIONS", "PUT", "DELETE", "PATCH"):
        r = app.api.call(method, "/api/projects", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
        assert r.status == 501 and not [h for h in r.headers if h.startswith("access-control-")], method        # no preflight can succeed


def test_bad_request_bodies(app):
    for raw, status, word in ((b"not json", 400, "not valid JSON"), (b"[1, 2]", 400, "JSON object"), (b"\xff\xfe", 400, "not valid JSON")):
        r = app.api.post("/api/projects", body=raw)
        assert r.status == status and word in r.error, raw
    r = app.api.post("/api/projects", headers={"Content-Length": "1000000000"}, body=b"")                      # refused before anything is read
    assert r.status == 413 and "too large" in r.error


def test_pick_is_501_without_native_dialogs_and_forwards_the_choice_with_them(app, monkeypatch):
    monkeypatch.setattr(dialogs, "pick", REAL_PICK)
    monkeypatch.setattr(dialogs, "available", lambda: False)
    r = app.api.post("/api/pick", {"kind": "folder"})
    assert r.status == 501 and "no native dialog" in r.error
    assert app.api.get("/api/state").json["native_dialogs"] is False
    asked = []
    answers = iter([["/a/b", "/c"], None])

    def fake_pick(kind, prompt, multiple=False, default=None, timeout=900.0):
        asked.append((kind, prompt, multiple, default))
        return next(answers)

    monkeypatch.setattr(dialogs, "pick", fake_pick)
    assert app.api.post("/api/pick", {"kind": "file", "multiple": True, "prompt": "Choose files", "default": "/x"}).json == {"paths": ["/a/b", "/c"]}
    assert app.api.post("/api/pick", {}).json == {"cancelled": True}
    assert asked == [("file", "Choose files", True, "/x"), ("folder", "Choose", False, None)]


def test_reveal_shows_folders_of_the_app_only(app, toy_project, revealed):
    assert app.api.post("/api/reveal-home").json == {"ok": True}
    assert app.api.post(f"/api/projects/{toy_project.slug}/reveal").json == {"ok": True}
    assert app.api.post("/api/projects/nope/reveal").status == 404
    assert revealed == [app.home, toy_project.dir]                       # the unknown project showed nothing


# ---------------------------------------------------------------------------------------------------------------- creating a project: input checks
def test_the_input_checks_of_a_new_project_give_friendly_400s(app, inputs, tmp_path):
    good_run = inputs["runs"][0]["path"]
    missing = str(tmp_path / "nowhere")

    def project(**changes):
        body = {"name": "Checks", "inputs": {**inputs, **changes.pop("inputs", {})}}
        body.update(changes)
        return body

    cases = [
        (project(name=""), "name"), (project(name="   "), "name"), ({"inputs": inputs}, "name"), ({"name": "x"}, "name"), ({"name": "x", "inputs": []}, "name"),
        (project(inputs={"runs": []}), "at least one BindCraft run"), (project(inputs={"runs": None}), "at least one BindCraft run"),
        (project(inputs={"runs": {"id": "run01"}}), "at least one BindCraft run"),
        (project(inputs={"runs": [{"id": "bad id", "path": good_run}]}), "run name 'bad id'"), (project(inputs={"runs": [{"id": "", "path": good_run}]}), "run name ''"),
        (project(inputs={"runs": [{"id": "-x", "path": good_run}]}), "run name '-x'"), (project(inputs={"runs": [{"id": "a" * 32, "path": good_run}]}), "run name"),
        (project(inputs={"runs": [{"id": "run01", "path": good_run}, {"id": "run01", "path": inputs["runs"][1]["path"]}]}), "Two runs are called run01"),
        (project(inputs={"runs": [{"id": "run01", "path": missing}]}), f"The run folder {missing} does not exist"),
        (project(inputs={"runs": [{"id": "run01", "path": str(Path(good_run) / "final_design_stats.csv")}]}), "does not exist"),    # a file, not a folder
        (project(inputs={"runs": [{"id": "run01", "path": good_run, "settings": missing}]}), f"The file {missing} does not exist"),
        (project(inputs={"target_pdb": None}), "Choose the target structure"), (project(inputs={"target_pdb": missing}), "Choose the target structure"),
        (project(inputs={"reference": ""}), "Choose the full-length reference structure"), (project(inputs={"reference": missing}), "Choose the full-length reference"),
        (project(inputs={"runs": [{"id": "run01", "path": str(app.home.parent)}]}), "Binderscope folder lies inside this run folder"),
    ]
    before = homes_folders(app)
    for body, word in cases:
        r = app.api.post("/api/projects", body)
        assert r.status == 400 and word in r.error, (body, r.status, r.text)
        assert "\n" not in r.error and "Traceback" not in r.error
    assert homes_folders(app) == before and app.srv.jobs.active() is None                 # nothing was created, nothing started


def test_unknown_projects_and_builds_are_404(app):
    for method, path in (("GET", "/api/projects/nope"), ("GET", "/api/projects/nope/state"), ("GET", "/api/projects/..%2f"), ("GET", "/api/projects/Up"),
                         ("POST", "/api/projects/nope/rebuild"), ("POST", "/api/projects/nope/update-pages"), ("POST", "/api/projects/nope/delete"),
                         ("POST", "/api/projects/nope/state"), ("GET", "/p/nope/"), ("GET", "/p/..%2f..%2fetc/passwd")):
        r = app.api.call(method, path, json_body={} if method == "POST" else None)
        assert r.status == 404 and "no such project" in r.error, (method, path)
    assert app.api.get("/api/jobs/nope").status == 404 and "no such build" in app.api.get("/api/jobs/nope").error
    assert app.api.post("/api/jobs/nope/cancel").json == {"ok": False}


# ---------------------------------------------------------------------------------------------------------------- a build from start to finish
def test_a_build_reports_its_stages_and_ends_in_a_ready_project(app, toy_project, toy_run1, toy_run2, inputs):
    first, job = toy_project.first, toy_project.job
    assert set(first) == {"slug", "job", "inputs"} and first["slug"] == "toy-demo" and first["job"]["status"] == "running" and first["job"]["kind"] == "build"
    assert first["inputs"]["target_pdb"] == inputs["target_pdb"] and first["inputs"]["reference"] == inputs["reference"]       # paths that stay where they are stay as given
    assert [Path(r["path"]) for r in first["inputs"]["runs"]] == [Path(r["path"]).resolve() for r in inputs["runs"]] and first["inputs"]["profile"] is None
    assert (first["inputs"]["reference_chain"], first["inputs"]["partner_chains"], first["inputs"]["online"], first["inputs"]["consurf"]) == ("A", "auto", False, False)
    assert first["inputs"]["placement"] == "interface"                                                      # the default of the Options select
    assert len(first["job"]["stages"]) == 9 and first["job"]["stage_label"] == "Reading the run folders"
    assert job["status"] == "done" and job["returncode"] == 0 and job["error"] is None and job["error_kind"] is None
    assert job["done"] == list(range(9)) and job["progress"] == 1.0 and job["stage"] == 9 and job["elapsed"] > 1
    log = "\n".join(job["log"])
    for name, _label in jobs.STAGES:                        # the progress bar follows the wording of the pipeline's own log
        assert f"] {name} ..." in log and f"] {name} done in" in log, name
    assert job["log_total"] == len(job["log"]) and "finished in" in log
    tail = app.api.get(f"/api/jobs/{job['id']}?since={len(job['log']) - 3}").json
    assert tail["log_from"] == len(job["log"]) - 3 and tail["log"] == job["log"][-3:]

    p = app.api.get(f"/api/projects/{toy_project.slug}").json
    n_acc = len(toy_run1.accepted()) + len(toy_run2.accepted())
    n_fam = len(toy_run1.accepted_backbones()) + len(toy_run2.accepted_backbones())
    assert p["status"] == "ready" and p["ready"] is True and p["name"] == "Toy demo" and p["slug"] == "toy-demo" and p["runs"] == ["run01", "run02"]
    assert p["counts"]["accepted"] == n_acc == 22 and p["counts"]["families"] == n_fam == 16 and p["counts"]["runs"] == 2
    assert p["counts"]["designs"] == p["counts"]["accepted"] + p["counts"]["rejected"]
    assert p["highlights"] and all(h["text"] for h in p["highlights"]) and len(p["highlights"]) <= 3
    assert p["version"] == __version__ and p["outdated"] is False and p["error"] is None and p["seconds"] > 1 and p["finished"]
    assert p["rebuild_needed"] is False and store.read_meta(toy_project.dir)["build"]["revision"] == BUILD_REVISION               # built by the analysis of this version
    assert (p["inputs"]["placement"], p["inputs"]["reference_chain"]) == ("interface", "A")
    assert [Path(r["path"]) for r in p["inputs"]["runs"]] == [Path(r["path"]).resolve() for r in inputs["runs"]]
    assert (p["inputs"]["reference_chain"], p["inputs"]["partner_chains"], p["inputs"]["profile"], p["inputs"]["online"]) == ("A", "auto", None, False)
    assert p["inputs"]["target_pdb"] == inputs["target_pdb"] and p["inputs"]["reference"] == inputs["reference"]       # given by path: not copied
    assert any("finished in" in x for x in p["log"])
    listed = [x for x in app.api.get("/api/state").json["projects"] if x["slug"] == toy_project.slug]
    assert len(listed) == 1 and listed[0]["counts"] == p["counts"] and listed[0]["folder"] == str(toy_project.dir)
    assert app.api.get("/api/state").json["job"] is None


def test_the_project_folder_holds_what_the_docs_say(toy_project):
    d = toy_project.dir
    assert {x.name for x in d.iterdir()} == {"project.app.json", "work", "bundle", "build.log"}        # no inputs/: run folders are never copied
    meta = json.loads((d / "project.app.json").read_text(encoding="utf-8"))
    assert meta["schema"] == "binderscope.app.project/1" and meta["slug"] == "toy-demo" and meta["build"]["status"] == "ready"
    assert meta["build"]["job"] == toy_project.job["id"] and meta["build"]["counts"]["accepted"] == 22
    assert (d / "work" / "export").is_dir() and (d / "work" / "project.json").is_file() and (d / "bundle" / "index.html").is_file()
    assert "finished in" in (d / "build.log").read_text(encoding="utf-8")


def test_pages_are_served_with_the_app_bar_added_at_serve_time_only(app, toy_project):
    api, bundle = app.api, toy_project.dir / "bundle"
    moved = api.get(f"/p/{toy_project.slug}")
    assert moved.status == 301 and moved.headers["location"] == f"/p/{toy_project.slug}/"
    for page in PAGES + ("guide",):
        disk = (bundle / f"{page}.html").read_bytes()
        assert b"appbar" not in disk and b"/_app/" not in disk, page                                   # the files on disk are the plain bundle
        r = api.get(f"/p/{toy_project.slug}/{page}.html")
        assert r.status == 200 and r.headers["content-type"] == "text/html; charset=utf-8" and r.body.count(APPBAR) == 1, page
        assert r.body.replace(APPBAR, b"", 1) == disk and r.body[r.body.index(APPBAR) + len(APPBAR):].startswith(b"</body>"), page
        assert int(r.headers["content-length"]) == len(r.body)
    assert api.get(f"/p/{toy_project.slug}/").body == api.get(f"/p/{toy_project.slug}/index.html").body == api.get(f"/p/{toy_project.slug}/index.html?x=1").body
    assert api.get(f"/p/{toy_project.slug}/index.html").body.count(b"<title>Overview") == 1
    bar = api.get("/_app/appbar.js")
    assert bar.status == 200 and b"bs-appbar" in bar.body and b"/api/projects/" in bar.body


def test_data_and_structure_files_are_served_unchanged(app, toy_project):
    api, bundle = app.api, toy_project.dir / "bundle"
    for name in ("campaign", "inspect", "homolog", "profile", "manifest"):
        for ext, ctype in (("js", "text/javascript"), ("json", "application/json")):
            r = api.get(f"/p/{toy_project.slug}/data/{name}.{ext}")
            assert r.status == 200 and r.headers["content-type"].startswith(ctype) and r.body == (bundle / "data" / f"{name}.{ext}").read_bytes(), (name, ext)
            assert APPBAR not in r.body
    pdb = sorted((bundle / "structures").glob("*.pdb"))[0]
    r = api.get(f"/p/{toy_project.slug}/structures/{pdb.name}")
    assert r.status == 200 and r.headers["content-type"].startswith("text/plain") and r.body == pdb.read_bytes()
    head = api.call("HEAD", f"/p/{toy_project.slug}/data/campaign.js")
    assert head.status == 200 and head.body == b"" and int(head.headers["content-length"]) == (bundle / "data" / "campaign.js").stat().st_size
    for path in ("nope.html", "data/", "data/nope.js", "structures"):
        assert api.get(f"/p/{toy_project.slug}/{path}").status == 404, path
    assert api.get(f"/p/{toy_project.slug}/data/campaign.js").headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("tail", ["../project.app.json", "..%2fproject.app.json", "%2e%2e/work/project.json", "../../project.app.json", "//etc/passwd",
                                  "data/../../project.app.json", "..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd", "..\\project.app.json"])
def test_files_outside_the_bundle_are_not_served(app, toy_project, tail):
    r = app.api.get(f"/p/{toy_project.slug}/{tail}")
    assert r.status in (403, 404) and b"binderscope.app.project" not in r.body and b"root:" not in r.body


def test_a_link_inside_a_bundle_cannot_lead_out_of_it(app, toy_project, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("not for the browser")
    link = toy_project.dir / "bundle" / "link.txt"
    link.symlink_to(outside)
    try:
        r = app.api.get(f"/p/{toy_project.slug}/link.txt")
        assert r.status == 403 and b"not for the browser" not in r.body
    finally:
        link.unlink()


# ---------------------------------------------------------------------------------------------------------------- uploads and the RCSB
def test_uploads_are_stored_below_the_home_under_clean_names(app, toy_dir):
    up = app.home / "_uploads"
    data = (toy_dir / "construct" / "toy_construct.pdb").read_bytes()
    r = app.api.post("/api/upload?name=my%20file.pdb", body=data)
    assert r.status == 200 and r.json["name"] == "my file.pdb"
    stored = Path(r.json["path"])
    assert stored.read_bytes() == data and stored.parent.parent == up and re.fullmatch(r"[0-9a-f]{8}", stored.parent.name)
    for given, clean in (("../../evil name$.cif", "evil name_.cif"), ("C:\\x\\y.pdb", "y.pdb"), ("", "file"), ("...", "file"), ("a/b/c.pdb", "c.pdb"),
                         ("t\u00e4rget.pdb", "t_rget.pdb"), ("x" * 300 + ".pdb", None)):
        r = app.api.post("/api/upload?name=" + enc(given), body=b"data")
        assert r.status == 200, given
        path = Path(r.json["path"])
        assert path.parent.parent == up and path.read_bytes() == b"data" and len(path.name) <= 120, given
        if clean:
            assert path.name == clean == r.json["name"], given
    check = app.api.get("/api/check-structure?path=" + enc(str(stored))).json
    assert check["ok"] and check["format"] == "PDB" and check["chains"] == [{"id": "A", "residues": 110}]       # an uploaded file is usable as it is


def test_bad_uploads_leave_nothing_behind(app):
    up = app.home / "_uploads"
    files = lambda: sorted(str(p) for p in up.rglob("*") if p.is_file())       # noqa: E731
    before = files()
    assert app.api.post("/api/upload?name=a.cif", body=b"").status == 413
    big = app.api.raw(f"POST /api/upload?name=a.cif HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nX-Binderscope: 1\r\nContent-Length: 1000000000\r\n\r\n".encode())
    assert big.startswith(b"HTTP/1.1 413")
    cut = app.api.raw(f"POST /api/upload?name=cut.pdb HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nX-Binderscope: 1\r\nContent-Length: 100\r\n\r\n0123456789".encode(),
                      close_write=True)
    assert cut.startswith(b"HTTP/1.1 400") and b"cut short" in cut
    assert app.api.post("/api/upload?name=a.cif", body=b"x", trusted=False).status == 403
    assert files() == before


def test_the_rcsb_download_is_a_local_request_to_the_configured_address(app, toy_dir, monkeypatch):
    cif = (toy_dir / "reference" / "toy_target.cif").read_bytes()
    ups = app.home / "_uploads"
    with appkit.MockRcsb({"1ABC": cif}, broken=("5XXX",)) as rcsb:
        monkeypatch.setenv("BINDERSCOPE_RCSB_URL", rcsb.url)
        dirs = len(list(ups.iterdir())) if ups.exists() else 0
        r = app.api.post("/api/fetch-reference", {"id": "1abc"})
        assert r.status == 200 and (r.json["id"], r.json["name"]) == ("1abc", "1abc.cif")
        got = Path(r.json["path"])
        assert got.read_bytes() == cif and got.parent.parent == ups
        assert rcsb.requests == [("/download/1ABC.cif", f"binderscope/{__version__}")]
        chk = app.api.get("/api/check-structure?path=" + enc(str(got))).json
        assert chk["ok"] and chk["format"] == "mmCIF" and [c["id"] for c in chk["chains"]] == ["A", "B"]
        assert app.api.post("/api/fetch-reference", {"id": "1AbC"}).json["id"] == "1abc"                    # the id is case-insensitive
        r = app.api.post("/api/fetch-reference", {"id": "9zzz"})
        assert r.status == 404 and r.error == "The RCSB has no entry 9ZZZ."
        r = app.api.post("/api/fetch-reference", {"id": "5xxx"})
        assert r.status == 502 and r.error == "The RCSB answered 500."
        for bad in ("", "abc", "abcde", "../x", "ab-1", "1ab c", None):
            r = app.api.post("/api/fetch-reference", {"id": bad})
            assert r.status == 400 and "four characters" in r.error, bad
        assert [p for p, _ in rcsb.requests].count("/download/9ZZZ.cif") == 1 and len(rcsb.requests) == 4        # bad ids never reach the network
        assert len(list(ups.iterdir())) == dirs + 2                                                          # only the two good downloads are kept
    monkeypatch.setenv("BINDERSCOPE_RCSB_URL", f"http://127.0.0.1:{appkit.free_port()}/download")
    r = app.api.post("/api/fetch-reference", {"id": "1abc"})
    assert r.status == 502 and r.error.startswith("Could not reach the RCSB") and "choose a file instead" in r.error
    assert len(list(ups.iterdir())) == dirs + 2


def test_the_rcsb_address_defaults_to_the_public_download_site(monkeypatch):
    monkeypatch.delenv("BINDERSCOPE_RCSB_URL", raising=False)
    assert server._rcsb_url("1abc") == "https://files.rcsb.org/download/1ABC.cif"
    monkeypatch.setenv("BINDERSCOPE_RCSB_URL", "http://localhost:9/dl/")
    assert server._rcsb_url("1abc") == "http://localhost:9/dl/1ABC.cif"


# ---------------------------------------------------------------------------------------------------------------- the file browser and the probes
def test_the_file_browser_lists_folders_first_and_skips_hidden_files(app, tmp_path, monkeypatch):
    d = tmp_path / "browse"
    (d / "b_dir").mkdir(parents=True)
    (d / "A_dir").mkdir()
    (d / ".hidden").write_text("x")
    (d / "z.txt").write_text("x")
    (d / "a.pdb").write_text("x")
    q = lambda p: "/api/ls?path=" + enc(str(p))      # noqa: E731
    r = app.api.get(q(d)).json
    assert r["path"] == str(d.resolve()) and r["parent"] == str(d.resolve().parent)
    assert [(e["name"], e["dir"]) for e in r["entries"]] == [("A_dir", True), ("b_dir", True), ("a.pdb", False), ("z.txt", False)]
    assert all(e["path"] == str(d / e["name"]) for e in r["entries"])
    assert app.api.get(q(d / "z.txt")).json["path"] == str(d.resolve())               # a file: its folder
    assert app.api.get(q(d / "there")).json["path"] == str(d.resolve())               # a missing name in a folder: that folder
    root = app.api.get("/api/ls?path=/").json
    assert root["path"] == "/" and root["parent"] is None
    home = tmp_path / "home"
    home.mkdir()
    (home / "mine.txt").write_text("x")
    monkeypatch.setenv("HOME", str(home))
    for path in ("/api/ls", "/api/ls?path=", q(tmp_path / "missing" / "deeper")):
        r = app.api.get(path).json
        assert r["path"] == str(home.resolve()) and r["home"] == str(home) and [e["name"] for e in r["entries"]] == ["mine.txt"], path


@pytest.mark.skipif(os.geteuid() == 0 if hasattr(os, "geteuid") else True, reason="needs a user that cannot read a folder")
def test_an_unreadable_folder_is_a_403_with_a_message(app, tmp_path):
    d = tmp_path / "closed"
    d.mkdir()
    d.chmod(0)
    try:
        r = app.api.get("/api/ls?path=" + enc(str(d)))
        assert r.status == 403 and "Cannot read this folder" in r.error
    finally:
        d.chmod(0o700)


def test_inspect_run_describes_the_toy_runs(app, toy_dir, toy_run1, toy_run2, toy_truth):
    for rid, run, settings, length in (("run01", toy_run1, True, [60, 100]), ("run02", toy_run2, False, [64, 108])):
        folder = toy_dir / "runs" / rid
        info = app.api.get("/api/inspect-run?path=" + enc(folder)).json
        assert info["ok"] is True and info["id"] == rid and info["path"] == str(folder.resolve()) and info["experimental"] is False
        assert info["reader"] == {"run01": "BindCraft 1 1.5", "run02": "BindCraft 1"}[rid]                  # the settings of run01 say 1.5, run02 has none
        assert (info["accepted"], info["rejected"], info["families"]) == (len(run.accepted()), len(run.rejected()), len(run.accepted_backbones()))
        assert info["hotspots"] == [str(n) for n in toy_truth["hotspots_construct"][rid]] and info["binder_length"] == length     # construct numbers
        assert info["settings_found"] is settings and info["warnings"] == [] and info["target_candidates"] == []


def test_inspect_run_names_a_bindcraft_2_campaign_without_a_version_number(app, toy_dir):
    for name in ("toya", "toyb"):
        info = app.api.get("/api/inspect-run?path=" + enc(toy_dir / "bindcraft2" / name)).json
        assert info["ok"] and info["experimental"] is True and info["reader"] == "BindCraft 2", name       # its tool version ('2 v1.0.1') is not repeated


def test_inspect_run_on_a_folder_of_runs_offers_them(app, toy_dir):
    r = app.api.get("/api/inspect-run?path=" + enc(toy_dir / "runs")).json
    assert r["ok"] is False and "does not look like a BindCraft run folder" in r["error"]
    assert r["children"] == [str((toy_dir / "runs" / x).resolve()) for x in ("run01", "run02")]
    r = app.api.get("/api/inspect-run?path=" + enc(toy_dir / "bindcraft2")).json
    assert r["ok"] is False and [Path(c).name for c in r["children"]] == ["toya", "toyb"]
    for path in (toy_dir / "construct", toy_dir / "reference" / "refdata"):
        r = app.api.get("/api/inspect-run?path=" + enc(path)).json
        assert r["ok"] is False and r["children"] == [] and r["error"]


def test_inspect_run_reports_files_and_missing_folders(app, toy_dir, tmp_path):
    for path in (toy_dir / "construct" / "toy_construct.pdb", tmp_path / "nowhere"):
        r = app.api.get("/api/inspect-run?path=" + enc(path)).json
        assert r["ok"] is False and r["error"].endswith("is not a folder.")
    assert app.api.get("/api/inspect-run").json["ok"] is False


def test_inspect_run_finds_the_structure_named_in_the_settings(app, toy_dir, tmp_path):
    campaign = tmp_path / "campaign"
    shutil.copytree(toy_dir / "runs" / "run01", campaign / "run01")
    shutil.copy(toy_dir / "construct" / "toy_construct.pdb", campaign / "toy_construct.pdb")
    shutil.copy(toy_dir / "construct" / "toy_construct_gapped.pdb", campaign / "a_other.pdb")
    r = app.api.get("/api/inspect-run?path=" + enc(campaign / "run01")).json
    assert r["ok"] and [(c["name"], c["match"]) for c in r["target_candidates"]] == [("toy_construct.pdb", True), ("a_other.pdb", False)]   # the named one first
    assert r["target_candidates"][0]["path"] == str(campaign / "toy_construct.pdb")


def test_check_structure_finds_the_chains_and_the_one_that_matches_the_construct(app, toy_dir):
    ref, con = toy_dir / "reference" / "toy_target.cif", toy_dir / "construct" / "toy_construct.pdb"
    r = app.api.get("/api/check-structure?path=" + enc(ref)).json
    assert r == {"ok": True, "name": "toy_target.cif", "format": "mmCIF", "chains": [{"id": "A", "residues": 250}, {"id": "B", "residues": 250}],
                 "suggest_chain": None, "partners": []}
    r = app.api.get("/api/check-structure?path=" + enc(ref) + "&against=" + enc(con)).json
    assert r["suggest_chain"] == "A" and r["partners"] == ["B"]                         # the construct lies in chain A; B has the same sequence
    r = app.api.get("/api/check-structure?path=" + enc(toy_dir / "reference" / "toy_target.pdb") + "&against=" + enc(con)).json
    assert r["ok"] and r["format"] == "PDB" and r["suggest_chain"] == "A" and r["partners"] == ["B"]
    r = app.api.get("/api/check-structure?path=" + enc(con)).json
    assert r["ok"] and r["format"] == "PDB" and r["chains"] == [{"id": "A", "residues": 110}] and r["suggest_chain"] is None
    r = app.api.get("/api/check-structure?path=" + enc(con) + "&against=" + enc(ref)).json     # the other way round: 110 residues are no match for 250
    assert r["ok"] and r["suggest_chain"] is None and r["partners"] == []


def test_check_structure_says_so_when_nothing_matches_or_nothing_reads(app, toy_dir, tmp_path):
    ref = toy_dir / "reference" / "toy_target.cif"
    glycines = gemmi.read_structure(str(toy_dir / "construct" / "toy_construct.pdb"))
    for ch in glycines[0]:
        for res in ch:
            res.name = "GLY"
    odd = tmp_path / "glycines.pdb"
    glycines.write_pdb(str(odd))
    r = app.api.get("/api/check-structure?path=" + enc(ref) + "&against=" + enc(odd)).json
    assert r["ok"] and r["suggest_chain"] is None and r["partners"] == []              # no chain looks like the construct
    r = app.api.get("/api/check-structure?path=" + enc(ref) + "&against=" + enc(tmp_path / "missing.pdb")).json
    assert r["ok"] and r["suggest_chain"] is None
    notes, not_cif, garbage, water = tmp_path / "notes.txt", tmp_path / "not.cif", tmp_path / "garbage.pdb", tmp_path / "water.pdb"
    notes.write_text("hello\n")
    not_cif.write_text("not a cif at all")
    garbage.write_text("this is not a structure\n" * 5)
    water.write_text("HETATM    1  O   HOH A   1       0.000   0.000   0.000  1.00  0.00           O\nEND\n")
    for path, word in ((notes, "cannot read"), (not_cif, "cannot read"), (garbage, "No protein chain"), (water, "No protein chain"),
                       (tmp_path / "missing.pdb", "is not a file"), (tmp_path, "is not a file")):
        r = app.api.get("/api/check-structure?path=" + enc(path)).json
        assert r["ok"] is False and word in r["error"], path
    assert app.api.get("/api/check-structure").json["ok"] is False


@pytest.fixture(scope="module")
def variants(toy_dir, tmp_path_factory):
    """Two references whose chain A is the target and whose chain B is not: one with the residue types of B shuffled, one that holds only its end."""
    d = tmp_path_factory.mktemp("variants")
    return SimpleNamespace(scrambled=scrambled_chain_b(toy_dir / "reference" / "toy_target.cif", d / "scrambled_b.cif"),
                           short=short_chain_b(toy_dir / "reference" / "toy_target.cif", d / "short_b.cif", first=140))


def page_verdict(fit: dict) -> str:
    """What the import page's note under the reference (new.js, fitNote) says about a fit: "good" (green), "warn" (yellow) or "bad" (red)."""
    if not fit["ok"]:
        return "warn"
    n, total = fit["n"], fit["n_construct"]
    if fit["rmsd"] is None or n < 10 or n < 0.6 * total or fit["identity"] < 0.7:
        return "bad"
    return "warn" if fit["rmsd"] > 3 or fit["identity"] < 0.9 else "good"


def test_the_verdicts_of_the_tests_are_those_of_the_page():
    """page_verdict above repeats the thresholds of fitNote in new.js: if the page changes them, this test says so and the mirror is updated."""
    js = (Path(server.__file__).resolve().parent / "static" / "new.js").read_text(encoding="utf-8")
    body = js[js.index("function fitNote"):js.index("async function refreshFit")]
    assert "n < 10 || n < 0.6 * N || f.identity < 0.7" in body and "f.rmsd > 3 || f.identity < 0.9" in body and "if (!f.ok)" in body
    assert "msg bad" in body and "msg warn" in body and "msg good" in body


def test_check_structure_previews_how_the_target_structure_fits_the_chain(app, toy_dir):
    """With the structure given to BindCraft (against) the answer carries `fit`: the numbering offset, how many residues match by number, how many of them are the
    same amino acid and the CA RMSD of the fit (all of them, and without what a trimmed fit leaves out), the numbers the build itself uses."""
    ref, con = toy_dir / "reference" / "toy_target.cif", toy_dir / "construct" / "toy_construct.pdb"
    q = "/api/check-structure?path=" + enc(ref) + "&against=" + enc(con)
    r = app.api.get(q).json
    assert r["suggest_chain"] == "A" and r["partners"] == ["B"]
    fit = r["fit"]
    assert fit["ok"] is True and fit["chain"] == "A" and fit["offset"] == 59 and (fit["n"], fit["n_construct"]) == (110, 110) and fit["identity"] == 1.0
    assert fit["rmsd"] is not None and fit["rmsd"] < 0.05 and fit["core_rmsd"] < 0.05 and fit["core_n"] == 110                       # the construct is cut out of the chain
    assert set(fit) == {"ok", "chain", "offset", "n", "n_construct", "identity", "rmsd", "core_rmsd", "core_n"} and page_verdict(fit) == "good"
    assert r == app.api.get(q + "&chain=A").json                                                                                       # A is the suggestion
    assert "fit" not in app.api.get("/api/check-structure?path=" + enc(ref)).json                                                      # nothing to fit without the construct
    assert "fit" not in app.api.get("/api/check-structure?path=" + enc(ref) + "&chain=B").json
    assert "fit" not in app.api.get("/api/check-structure?path=" + enc(con)).json
    assert "fit" not in app.api.get("/api/check-structure?path=" + enc(ref) + "&against=" + enc(Path(toy_dir) / "missing.pdb")).json


def test_the_chain_of_the_query_decides_which_chain_the_fit_is_for(app, toy_dir, variants):
    ref, con = toy_dir / "reference" / "toy_target.cif", toy_dir / "construct" / "toy_construct.pdb"
    q = "/api/check-structure?path=" + enc(ref) + "&against=" + enc(con)
    b = app.api.get(q + "&chain=B").json
    assert b["suggest_chain"] == "A" and b["partners"] == ["B"] and b["fit"]["chain"] == "B"          # what is suggested does not follow the chain that is asked about
    assert (b["fit"]["n"], b["fit"]["identity"]) == (110, 1.0) and b["fit"]["rmsd"] < 0.05 and page_verdict(b["fit"]) == "good"           # the toy reference is a homodimer
    q = "/api/check-structure?path=" + enc(variants.scrambled) + "&against=" + enc(con)
    a, s = app.api.get(q).json, app.api.get(q + "&chain=B").json
    assert a["suggest_chain"] == "A" and a["partners"] == [] and a["fit"]["chain"] == "A" and page_verdict(a["fit"]) == "good"           # chain B has another sequence
    assert s["suggest_chain"] == "A" and s["fit"]["chain"] == "B"
    assert s["fit"]["identity"] < 0.3 and s["fit"]["n"] >= 100 and page_verdict(s["fit"]) == "bad"                                      # the wrong chain: another sequence
    q = "/api/check-structure?path=" + enc(variants.short) + "&against=" + enc(con)
    a, s = app.api.get(q).json, app.api.get(q + "&chain=B").json
    assert a["fit"]["chain"] == "A" and page_verdict(a["fit"]) == "good" and a["partners"] == []
    assert s["fit"]["chain"] == "B" and s["fit"]["identity"] == 1.0 and 10 <= s["fit"]["n"] <= 40 and s["fit"]["n_construct"] == 110      # only the end of the construct overlaps it
    assert page_verdict(s["fit"]) == "bad"                                                                                               # fewer than 60% of the residues


def test_a_chain_that_does_not_exist_gives_a_fit_the_page_shows_as_a_warning(app, toy_dir):
    ref, con = toy_dir / "reference" / "toy_target.cif", toy_dir / "construct" / "toy_construct.pdb"
    r = app.api.get("/api/check-structure?path=" + enc(ref) + "&against=" + enc(con) + "&chain=Z").json
    assert r["ok"] is True and r["suggest_chain"] == "A" and r["fit"] == {"ok": False, "error": "No protein chain was found."} and page_verdict(r["fit"]) == "warn"


def test_a_reference_of_other_residue_types_gives_no_fit_and_the_answer_says_why(app, toy_dir, tmp_path):
    """The numbering offset is found by the residue types: a reference of glycines gives the vote nothing to go by, and the preview says that in a sentence of its own
    (not the raw message of the numbering vote) instead of a fit."""
    glycines = gemmi.read_structure(str(toy_dir / "reference" / "toy_target.pdb"))
    for ch in glycines[0]:
        for res in ch:
            res.name = "GLY"
    odd = tmp_path / "glycines.pdb"
    glycines.write_pdb(str(odd))
    r = app.api.get("/api/check-structure?path=" + enc(odd) + "&against=" + enc(toy_dir / "construct" / "toy_construct.pdb")).json
    assert r["ok"] and r["suggest_chain"] is None and r["fit"]["ok"] is False and page_verdict(r["fit"]) == "warn", r
    assert r["fit"]["error"] == ("Binderscope cannot match the numbering of your target structure to this chain: the two sequences share too little to line up. "
                                 "Check the chain, or that this is the same protein.")
    assert "ambiguous" not in r["fit"]["error"] and "(" not in r["fit"]["error"]                                                     # no tuple of the vote in it


# ---------------------------------------------------------------------------------------------------------------- failed builds, retries, cancel
@pytest.fixture(scope="module")
def retried(app, inputs, toy_dir, tmp_path_factory):
    """A project whose first build fails (a reference with another sequence) and that is built again in the same folder with the right
    reference. The three files come in through /api/upload, as when they are dropped on the import page."""
    work = tmp_path_factory.mktemp("scrambled")
    st = gemmi.read_structure(str(toy_dir / "reference" / "toy_target.pdb"))
    rng = random.Random(1)
    for chain in st[0]:
        names = [r.name for r in chain]
        rng.shuffle(names)
        for res, name in zip(chain, names):
            res.name = name
    st.write_pdb(str(work / "scrambled.pdb"))

    def upload(path, name):
        r = app.api.post("/api/upload?name=" + enc(name), body=Path(path).read_bytes())
        assert r.status == 200
        return r.json["path"]

    up = SimpleNamespace(bad=upload(work / "scrambled.pdb", "scrambled reference.pdb"), construct=upload(toy_dir / "construct" / "toy_construct.pdb", "my construct.pdb"),
                         good=upload(toy_dir / "reference" / "toy_target.cif", "good reference.cif"))
    body = {"name": "Retry demo", "inputs": {**one_run(inputs), "target_pdb": up.construct, "reference": up.bad}}
    r1 = app.api.post("/api/projects", body)
    assert r1.status == 201, r1.text
    slug = r1.json["slug"]
    job1 = appkit.wait_job(app.api, r1.json["job"]["id"])
    kept = store.read_meta(app.home / slug)["inputs"]                                       # the first attempt moved its two uploads into the project
    failed = SimpleNamespace(first=r1.json, job=job1, summary=app.api.get(f"/api/projects/{slug}").json, page=app.api.get(f"/p/{slug}/"),
                             pages=app.api.post(f"/api/projects/{slug}/update-pages"), contents=sorted(p.name for p in (app.home / slug).iterdir()),
                             folders=homes_folders(app), inputs=sorted(p.name for p in (app.home / slug / "inputs").iterdir()), kept=kept,
                             uploads={k: Path(v).exists() for k, v in vars(up).items()})
    answer = r1.json["inputs"]                                                              # the page keeps these paths for a retry
    body2 = {"name": "Retry demo", "inputs": {**body["inputs"], "target_pdb": answer["target_pdb"], "reference": up.good}, "retry": slug}   # and a new upload
    r2 = app.api.post("/api/projects", body2)
    assert r2.status == 201, r2.text
    job2 = appkit.wait_job(app.api, r2.json["job"]["id"])
    return SimpleNamespace(slug=slug, up=up, failed=failed, second=r2.json, job=job2, summary=app.api.get(f"/api/projects/{slug}").json,
                           dir=app.home / slug, folders=homes_folders(app))


def test_a_failed_build_ends_with_a_one_line_message_and_keeps_the_project(app, retried):
    f = retried.failed
    assert f.first["slug"] == "retry-demo" and f.job["status"] == "failed" and f.job["returncode"] == 2 and f.job["error_kind"] == "inputs"
    assert re.fullmatch(r"the construct matches the reference chain at only \d+ of 110 residues", f.job["error"])
    assert f.job["done"][:1] == [0] and 1 <= f.job["stage"] < 9                            # the runs were read, a later stage stopped the build
    assert f.summary["status"] == "failed" and f.summary["error"] == f.job["error"] and f.summary["ready"] is False and f.summary["counts"] is None
    assert f.page.status == 404 and "no pages yet" in f.page.error
    assert f.pages.status == 400 and "no analysis yet" in f.pages.error                    # update-pages needs an export: rebuild first
    assert f.contents == ["build.log", "inputs", "project.app.json", "work"]
    assert f.job["log"][-1] == "binder-review import: " + f.job["error"]                  # the message is the command's last line
    assert f.inputs == ["reference-scrambled reference.pdb", "target-my construct.pdb"]    # the two uploads of the build, named by their role
    assert f.uploads == {"bad": False, "construct": False, "good": True}                   # moved into the project; the third upload is still waiting
    assert f.first["inputs"] == f.kept                                                      # the answer says where the files are now, as the project keeps them
    assert f.first["inputs"]["target_pdb"] == str(app.home / "retry-demo" / "inputs" / "target-my construct.pdb")
    assert f.first["inputs"]["reference"] == str(app.home / "retry-demo" / "inputs" / "reference-scrambled reference.pdb")


def test_a_retry_builds_again_in_the_same_folder_and_adopts_the_new_upload(app, retried, toy_dir):
    assert retried.second["slug"] == retried.slug == "retry-demo" and retried.folders == retried.failed.folders        # no second project
    assert retried.job["status"] == "done" and retried.summary["status"] == "ready" and retried.summary["error"] is None
    assert retried.summary["counts"]["accepted"] == 10 and retried.summary["counts"]["families"] == 8 and retried.summary["runs"] == ["run02"]
    inputs_dir = retried.dir / "inputs"
    assert {p.name for p in inputs_dir.iterdir()} == {"target-my construct.pdb", "reference-scrambled reference.pdb", "reference-good reference.cif"}
    meta = json.loads((retried.dir / "project.app.json").read_text(encoding="utf-8"))
    assert Path(meta["inputs"]["target_pdb"]) == inputs_dir / "target-my construct.pdb" and Path(meta["inputs"]["reference"]) == inputs_dir / "reference-good reference.cif"
    assert meta["inputs"]["target_pdb"] == retried.failed.kept["target_pdb"]                  # the same file as in the first attempt, not copied again
    assert (inputs_dir / "target-my construct.pdb").read_bytes() == (toy_dir / "construct" / "toy_construct.pdb").read_bytes()
    assert (inputs_dir / "reference-good reference.cif").read_bytes() == (toy_dir / "reference" / "toy_target.cif").read_bytes()
    assert not any(Path(v).parent.exists() for v in vars(retried.up).values())                           # all moved, folders included: nothing is kept twice
    assert app.api.get(f"/p/{retried.slug}/").status == 200


def test_build_errors_are_worded_for_the_page(app, inputs, toy_dir):
    r = app.api.post("/api/projects", {"name": "Wrong chain", "inputs": {**one_run(inputs), "reference_chain": "Z"}})
    assert r.status == 201
    job = appkit.wait_job(app.api, r.json["job"]["id"])
    assert job["status"] == "failed" and job["error_kind"] == "inputs" and job["returncode"] == 2
    err = job["error"]
    assert "chain 'Z'" in err and "choose the chain of your target in Options" in err and "--reference-chain" not in err        # the page's words, not the option's
    assert err.startswith("toy_target.cif: ") and str(toy_dir) not in err                                                    # the path is cut to the file name
    r = app.api.post("/api/projects", {"name": "Wrong partner", "inputs": {**one_run(inputs), "partner_chains": "Q"}})
    job = appkit.wait_job(app.api, r.json["job"]["id"])
    assert job["status"] == "failed" and job["error_kind"] == "inputs" and "partner chain 'Q'" in job["error"] and "\n" not in job["error"]


def test_cancel_ends_the_build_refuses_conflicting_actions_and_a_retry_never_reuses_a_ready_project(app, inputs, toy_project):
    api, slug_ready = app.api, toy_project.slug
    r = api.post("/api/projects", {"name": "Cancel me", "inputs": one_run(inputs)})
    assert r.status == 201
    slug, jid = r.json["slug"], r.json["job"]["id"]
    try:
        state = api.get("/api/state").json
        assert state["job"]["id"] == jid and state["job"]["slug"] == slug and [p["status"] for p in state["projects"] if p["slug"] == slug] == ["building"]
        for path, body in (("/api/projects", {"name": "Other", "inputs": one_run(inputs)}), (f"/api/projects/{slug_ready}/rebuild", {}),
                           (f"/api/projects/{slug_ready}/update-pages", {})):
            x = api.post(path, body)
            assert x.status == 409 and "Another build is running" in x.error, path
        x = api.post(f"/api/projects/{slug}/delete")
        assert x.status == 409 and "cancel the build first" in x.error and (app.home / slug).is_dir()
        idle, _ = store.create(app.home, "Idle project", {"runs": []})                    # another project can be deleted meanwhile
        assert api.post(f"/api/projects/{idle}/delete").json == {"ok": True} and not (app.home / idle).exists()
        assert api.post(f"/api/jobs/{jid}/cancel").json == {"ok": True}
        job = appkit.wait_job(api, jid)
        assert job["status"] == "cancelled" and job["error"] == "The build was cancelled." and job["error_kind"] == "cancelled"
        appkit.wait_until(lambda: app.srv.jobs.get(jid).proc.poll() is not None, what="the build process to end")
        assert app.srv.jobs.get(jid).proc.returncode != 0 and api.get("/api/state").json["job"] is None
        p = api.get(f"/api/projects/{slug}").json
        assert p["status"] == "cancelled" and p["error"] == "The build was cancelled." and p["ready"] is False
        assert api.post(f"/api/jobs/{jid}/cancel").json == {"ok": False}                  # over already
        # a cancelled project is built again in its own folder ...
        n = len(homes_folders(app))
        r = api.post("/api/projects", {"name": "Cancel me", "inputs": one_run(inputs), "retry": slug})
        assert r.status == 201 and r.json["slug"] == slug and len(homes_folders(app)) == n
        api.post(f"/api/jobs/{r.json['job']['id']}/cancel")
        appkit.wait_job(api, r.json["job"]["id"])
        # ... but a project that is ready is never overwritten by a retry: a new project gets the next free name
        r = api.post("/api/projects", {"name": "Cancel me", "inputs": one_run(inputs), "retry": slug_ready})
        assert r.status == 201 and r.json["slug"] == "cancel-me-2" and api.get(f"/api/projects/{slug_ready}").json["status"] == "ready"
        api.post(f"/api/jobs/{r.json['job']['id']}/cancel")
        appkit.wait_job(api, r.json["job"]["id"])
    finally:
        appkit.end_jobs(app.srv)
        for name in ("cancel-me", "cancel-me-2"):
            if (app.home / name).exists():
                appkit.wait_until(lambda: app.srv.jobs.active() is None, what="no build")
                api.post(f"/api/projects/{name}/delete")
    assert not (app.home / "cancel-me").exists() and not (app.home / "cancel-me-2").exists()


# ---------------------------------------------------------------------------------------------------------------- update pages, rebuild, saved decisions
def ev(i: int, ts: str, **kw):
    """A decision event as the review pages write it."""
    return {"v": 1, "type": "decision", "id": f"ev{i:014x}", "reviewer": "r", "ts": ts, "unit": "family", "design": "binder_x", "decision": "keep", **kw}


def test_saved_decisions_are_merged_by_event_id_and_sorted_by_time(app, toy_project):
    api, url = app.api, f"/api/projects/{toy_project.slug}/state"
    assert api.get(url).json == {"events": None}
    assert api.post(url, {"events": json.dumps([ev(1, "2026-01-01T10:00:00Z"), ev(2, "2026-01-01T09:00:00Z")])}).json == {"count": 2}
    assert api.post(url, {"events": json.dumps([ev(1, "2026-01-01T10:00:00Z"), ev(3, "2026-01-01T11:00:00Z")])}).json == {"count": 3}
    saved = json.loads(api.get(url).json["events"])
    assert [e["id"] for e in saved] == [ev(i, "")["id"] for i in (2, 1, 3)]                  # oldest first, the duplicate counted once
    assert json.loads((toy_project.dir / "review-events.json").read_text(encoding="utf-8")) == saved
    api.post(url, {"events": json.dumps([ev(1, "2026-05-05T00:00:00Z", decision="reject")])})        # the same id again: the stored event stays
    assert [e for e in json.loads(api.get(url).json["events"]) if e["id"] == ev(1, "")["id"]] == [ev(1, "2026-01-01T10:00:00Z")]
    assert api.post(url, {}).json == {"count": 3} and api.post(url, {"events": "[]"}).json == {"count": 3}   # nothing to add keeps everything
    for bad in ("not json", "{}", "[1, 2]", '[{"id": 5}]', '[{"x": 1}]', '"text"', "null", "[[]]"):
        r = api.post(url, {"events": bad})
        assert r.status == 400 and r.error == ("The events are not valid JSON." if bad == "not json" else "The events must be a list of objects with an id."), bad
    assert api.post(url, {"events": [ev(9, "2026")]}).status == 400                           # a JSON list, not the text of one
    assert api.get(url).json["events"] == json.dumps(saved, ensure_ascii=False)               # the bad bodies changed nothing


def test_update_pages_rewrites_the_pages_and_keeps_the_saved_decisions(app, toy_project):
    api, slug, d = app.api, toy_project.slug, toy_project.dir
    api.post(f"/api/projects/{slug}/state", {"events": json.dumps([ev(7, "2026-02-01T00:00:00Z")])})
    meta = store.read_meta(d)
    counts = meta["build"]["counts"]
    meta["build"]["version"] = "0.0.1"                                                        # as if an older Binderscope had built it
    store.write_meta(d, meta)
    assert api.get(f"/api/projects/{slug}").json["outdated"] is True
    index = d / "bundle" / "index.html"
    before = index.stat().st_mtime_ns
    r = api.post(f"/api/projects/{slug}/update-pages")
    assert r.status == 200 and r.json["slug"] == slug and r.json["job"]["kind"] == "bundle" and r.json["job"]["stages"] == ["Writing the review pages"]
    job = appkit.wait_job(api, r.json["job"]["id"])
    assert job["status"] == "done" and job["returncode"] == 0 and job["stage"] == 1
    p = api.get(f"/api/projects/{slug}").json
    assert p["status"] == "ready" and p["outdated"] is False and p["version"] == __version__ and p["counts"] == counts
    assert index.stat().st_mtime_ns > before and b"appbar" not in index.read_bytes()
    assert ev(7, "")["id"] in api.get(f"/api/projects/{slug}/state").json["events"]
    assert api.get(f"/p/{slug}/").status == 200


def test_rebuild_runs_the_analysis_again_in_the_same_folder(app, toy_project):
    api, slug = app.api, toy_project.slug
    meta = store.read_meta(toy_project.dir)
    meta["build"].pop("revision")                                                       # as if an older analysis had built it (before the placement around the interface)
    store.write_meta(toy_project.dir, meta)
    old = api.get(f"/api/projects/{slug}").json
    listed = [x for x in api.get("/api/state").json["projects"] if x["slug"] == slug][0]
    assert old["rebuild_needed"] is True and old["outdated"] is True and listed["rebuild_needed"] is True and old["version"] == __version__
    r = api.post(f"/api/projects/{slug}/update-pages", {})                              # new pages do not make the analysis newer
    assert appkit.wait_job(api, r.json["job"]["id"])["status"] == "done"
    pages = api.get(f"/api/projects/{slug}").json
    assert pages["rebuild_needed"] is True and pages["outdated"] is True and "revision" not in store.read_meta(toy_project.dir)["build"]
    before = pages
    r = api.post(f"/api/projects/{slug}/rebuild", {})
    assert r.status == 200 and r.json["slug"] == slug and r.json["job"]["kind"] == "build" and len(r.json["job"]["stages"]) == 9
    during = api.get(f"/api/projects/{slug}").json                                       # the old pages are still there, but a project that is being rebuilt is not "older"
    assert during["status"] == "building" and during["ready"] is True and during["outdated"] is False and during["rebuild_needed"] is False
    job = appkit.wait_job(api, r.json["job"]["id"])
    after = api.get(f"/api/projects/{slug}").json
    assert job["status"] == "done" and after["status"] == "ready" and after["counts"] == before["counts"] and after["finished"] > before["finished"]
    assert after["rebuild_needed"] is False and after["outdated"] is False and store.read_meta(toy_project.dir)["build"]["revision"] == BUILD_REVISION       # only a build does
    assert store.read_meta(toy_project.dir)["build"]["job"] == job["id"] and ev(7, "")["id"] in api.get(f"/api/projects/{slug}/state").json["events"]
    assert api.get(f"/p/{slug}/index.html").status == 200


def test_the_rebuild_options_reach_the_import_command(app, inputs, monkeypatch):
    slug, d = store.create(app.home, "Wiring", {**inputs, "reference_id": "1abc", "reference_chain": "B", "partner_chains": "A", "uniprot": "P12345",
                                                "profile": "/p/x.json", "online": True, "consurf": True})
    started = []

    def fake_start(kind, slug_, cmd, cwd, stages, log_file):
        started.append((kind, slug_, cmd, cwd, log_file))
        job = jobs.Job(kind, slug_, cmd, cwd, stages)
        job.status = "done"
        return job

    monkeypatch.setattr(app.srv.jobs, "start", fake_start)
    try:
        assert app.api.post(f"/api/projects/{slug}/rebuild", {"no_cache": True}).status == 200
        assert app.api.post(f"/api/projects/{slug}/rebuild", {}).status == 200
        assert app.api.post(f"/api/projects/{slug}/update-pages", {}).status == 400                  # no export yet
    finally:
        app.api.post(f"/api/projects/{slug}/delete")
    (_, _, cold, cwd, log), (_, _, warm, _, _) = started
    assert cold == warm + ["--no-cache"] and cwd == d and log == d / "build.log"
    assert cold[:4] == [sys.executable, "-m", "binder_review", "import"] and cold[4:6] == [f"run01={inputs['runs'][0]['path']}", f"run02={inputs['runs'][1]['path']}"]
    for flag, value in (("--target-pdb", inputs["target_pdb"]), ("--reference-cif", inputs["reference"]), ("--reference-id", "1abc"), ("--reference-chain", "B"),
                        ("--partner-chains", "A"), ("--uniprot", "P12345"), ("--profile", "/p/x.json"), ("--name", "Wiring"), ("--out", str(d / "work")),
                        ("--bundle", str(d / "bundle"))):
        assert cold[cold.index(flag) + 1] == value, flag
    assert "--online" in cold and "--consurf" in cold


def recording_start(app, monkeypatch):
    """Builds are not started: the commands that would be run are recorded (as in test_the_rebuild_options_reach_the_import_command)."""
    started: list[list[str]] = []

    def fake_start(kind, slug_, cmd, cwd, stages, log_file):
        started.append(cmd)
        job = jobs.Job(kind, slug_, cmd, cwd, stages)
        job.status = "done"
        return job

    monkeypatch.setattr(app.srv.jobs, "start", fake_start)
    return started


def flag_value(cmd: list[str], flag: str):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def test_the_placement_of_the_options_reaches_the_command_and_the_project(app, inputs, monkeypatch):
    """The import page sends how designs are placed on the full-length protein. Only the two choices that are not the default are passed to the command (the
    command's own default is the fit around the interface); anything else is the default, in the answer and in what the project keeps."""
    started = recording_start(app, monkeypatch)
    cases = [("none", None), ("interface", "interface"), ("whole", "whole"), ("core", "core"), ("unknown word", "best"), ("empty", ""), ("a number", 3), ("a list", ["whole"]),
             ("upper case", "Whole")]
    seen, slugs = {}, []
    try:
        for label, value in cases:
            extra = {} if value is None else {"placement": value}
            r = app.api.post("/api/projects", {"name": f"Placement {label}", "inputs": {**one_run(inputs), **extra}})
            assert r.status == 201, (label, r.text)
            slugs.append(r.json["slug"])
            kept = store.read_meta(app.home / r.json["slug"])["inputs"]["placement"]
            seen[label] = (r.json["inputs"]["placement"], kept, flag_value(started[-1], "--placement"))
    finally:
        for slug in slugs:
            app.api.post(f"/api/projects/{slug}/delete")
    assert len(started) == len(cases)
    assert seen == {"none": ("interface", "interface", None), "interface": ("interface", "interface", None), "whole": ("whole", "whole", "whole"),
                    "core": ("core", "core", "core"), "unknown word": ("interface", "interface", None), "empty": ("interface", "interface", None),
                    "a number": ("interface", "interface", None), "a list": ("interface", "interface", None), "upper case": ("interface", "interface", None)}
    for cmd in started:
        args = cli.build_parser().parse_args(cmd[3:])                                                   # what a build would run: the command line's own parser takes it
        assert args.placement == (flag_value(cmd, "--placement") or "interface")
        assert "--placement" not in cmd or cmd.index("--placement") + 2 == cmd.index("--name")


def test_a_rebuild_passes_the_placement_the_project_was_built_with(app, inputs, monkeypatch):
    started = recording_start(app, monkeypatch)
    made = []
    try:
        for label, extra in (("older", {}), ("whole", {"placement": "whole"}), ("core", {"placement": "core"}), ("default", {"placement": "interface"})):
            slug, d = store.create(app.home, f"Rebuild {label}", {**one_run(inputs), **extra})                   # an older project has no placement in its record
            made.append(slug)
            assert app.api.post(f"/api/projects/{slug}/rebuild", {}).status == 200
    finally:
        for slug in made:
            app.api.post(f"/api/projects/{slug}/delete")
    assert [flag_value(cmd, "--placement") for cmd in started] == [None, "whole", "core", None]


# ---------------------------------------------------------------------------------------------------------------- quitting and starting the app
def test_a_build_that_was_running_when_the_app_stopped_is_shown_as_interrupted_after_the_restart(tmp_path):
    home = tmp_path / "Binderscope"
    home.mkdir()
    slug, d = store.create(home, "Left running", {"runs": []})
    meta = store.read_meta(d)
    meta["build"] = {"status": "building", "job": "gone", "started": "2026-01-01T00:00:00+0000"}
    store.write_meta(d, meta)
    with appkit.running(home) as (srv, api):
        p = api.get(f"/api/projects/{slug}").json
        assert p["status"] == "failed" and "interrupted when Binderscope was closed" in p["error"] and srv.jobs.active() is None
        assert [x["status"] for x in api.get("/api/state").json["projects"]] == ["failed"] and api.get("/api/state").json["job"] is None


def test_quit_stops_the_server(tmp_path):
    with appkit.running(tmp_path / "h") as (srv, api):
        assert api.post("/api/quit").json == {"ok": True}
        appkit.wait_until(lambda: not srv.thread.is_alive(), timeout=5, what="the server to stop")
        with pytest.raises(OSError):
            api.get("/api/ping", timeout=2)


class FastTime:
    """The server's view of the time module with a short sleep: its idle watch looks every 5 s, which a test cannot wait for."""

    def __init__(self, real):
        self._real = real

    def sleep(self, seconds):
        self._real.sleep(min(seconds, 0.05))

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_idle_quit_waits_for_the_first_visit_and_for_running_builds(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "time", FastTime(time))
    srv = server.AppServer(("127.0.0.1", 0), tmp_path / "h", quit_after=0.3)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    th.start()
    try:
        time.sleep(0.8)
        assert th.is_alive()                                                                # nobody has opened a page yet: no quit
        busy = jobs.Job("build", "x", [], tmp_path, [])
        srv.jobs.jobs[busy.id] = busy                                                       # a build that is running
        assert appkit.Client(srv.port).get("/api/ping").status == 200
        time.sleep(1.2)
        assert th.is_alive() and srv.jobs.active() is busy                                  # idle for four times quit_after, but a build runs
        busy.status = "done"
        appkit.wait_until(lambda: not th.is_alive(), timeout=5, what="the idle server to stop")
    finally:
        if th.is_alive():
            srv.shutdown()
        th.join(5)
        srv.server_close()


def test_run_does_not_start_a_second_server_when_one_answers(app, tmp_path, capsys, monkeypatch):
    opened = []
    monkeypatch.setattr(server, "open_window", lambda url, mode="auto": opened.append((url, mode)) or "browser")
    assert server.run(port=app.port, home=tmp_path / "other", open_mode="none") == 0
    assert f"already running at http://127.0.0.1:{app.port}/" in capsys.readouterr().out and opened == []
    assert server.run(port=app.port, home=tmp_path / "other", open_mode="app") == 0
    assert opened == [(f"http://127.0.0.1:{app.port}/", "app")]                           # it shows the running app instead


def test_run_serves_until_quit_and_takes_the_next_port_when_one_is_busy(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(server, "open_window", lambda *a, **k: pytest.fail("a window was opened"))
    busy = socket.socket()                                                                   # something that is not Binderscope listens here
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    box: dict = {}
    th = threading.Thread(target=lambda: box.update(code=server.run(port=port, home=tmp_path / "h", open_mode="none")), daemon=True)
    th.start()
    out = ""
    try:
        t0 = time.time()
        while "is running at" not in out and time.time() - t0 < 15:
            time.sleep(0.1)
            cap = capsys.readouterr()
            out += cap.out + "\n" + cap.err
        m = re.search(r"running at http://127\.0\.0\.1:(\d+)/", out)
        assert m, out
        actual = int(m.group(1))
        assert port < actual <= port + 24 and f"port {port} is in use" in out
        api = appkit.Client(actual)
        assert api.get("/api/ping").json["app"] == "binderscope"
        assert api.get("/api/state").json["home"] == str(tmp_path / "h") and (tmp_path / "h").is_dir()
        assert api.post("/api/quit").json == {"ok": True}
        th.join(10)
        assert not th.is_alive() and box["code"] == 0
        with pytest.raises(OSError):
            api.get("/api/ping", timeout=2)
    finally:
        busy.close()


def test_open_window_uses_a_chromium_app_window_when_there_is_one(tmp_path, monkeypatch):
    launched, browsed = [], []
    fake = tmp_path / "Chromium"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    monkeypatch.setattr("webbrowser.open", lambda url: browsed.append(url))
    monkeypatch.setattr(server, "CHROMIUM", [str(tmp_path / "missing"), str(fake)])
    assert server.open_window("http://127.0.0.1:1/", "auto") == "app window"
    assert launched == [[str(fake), "--app=http://127.0.0.1:1/", "--window-size=1440,920"]] and browsed == []
    assert server.open_window("http://127.0.0.1:1/", "browser") == "browser" and browsed == ["http://127.0.0.1:1/"]
    monkeypatch.setattr(server, "CHROMIUM", [])
    assert server.open_window("http://127.0.0.1:2/", "auto") == "browser" and browsed[-1] == "http://127.0.0.1:2/" and len(launched) == 1


# ---------------------------------------------------------------------------------------------------------------- the end of the story
def test_delete_removes_the_project_folder_and_leaves_the_inputs_alone(app, retried, toy_dir):
    before = appkit.snapshot(toy_dir / "runs", toy_dir / "construct", toy_dir / "reference")
    assert retried.dir.is_dir() and retried.slug in [p["slug"] for p in app.api.get("/api/state").json["projects"]]
    assert app.api.post(f"/api/projects/{retried.slug}/delete").json == {"ok": True}
    assert not retried.dir.exists() and retried.slug not in [p["slug"] for p in app.api.get("/api/state").json["projects"]]
    assert app.api.get(f"/api/projects/{retried.slug}").status == 404 and app.api.post(f"/api/projects/{retried.slug}/delete").status == 404
    assert app.api.get(f"/p/{retried.slug}/").status == 404
    assert appkit.snapshot(toy_dir / "runs", toy_dir / "construct", toy_dir / "reference") == before and (app.home / "_uploads").is_dir()


def test_the_input_files_were_never_touched(toy_project, toy_dir):
    assert appkit.snapshot(toy_dir / "runs", toy_dir / "construct", toy_dir / "reference") == toy_project.before


# ---------------------------------------------------------------------------------------------------------------- names, uploads, odd requests
def test_a_project_name_with_two_dots_gives_a_usable_project(app, inputs):
    r = app.api.post("/api/projects", {"name": "Round 1..3", "inputs": one_run(inputs)})
    assert r.status == 201, r.text
    try:
        assert r.json["slug"] == "round-1-3" and (app.home / "round-1-3" / store.META).is_file()
        p = app.api.get("/api/projects/round-1-3")
        assert p.status == 200 and p.json["name"] == "Round 1..3"
    finally:
        app.api.post(f"/api/jobs/{r.json['job']['id']}/cancel")
        appkit.wait_job(app.api, r.json["job"]["id"])
        assert app.api.post("/api/projects/round-1-3/delete").json == {"ok": True}


def test_uploads_with_the_same_name_keep_their_own_content(app, inputs, toy_dir):
    def up(path):
        return app.api.post("/api/upload?name=structure.pdb", body=Path(path).read_bytes()).json["path"]

    construct, reference = toy_dir / "construct" / "toy_construct.pdb", toy_dir / "reference" / "toy_target.pdb"
    first, second = up(construct), up(reference)
    r = app.api.post("/api/projects", {"name": "Same names", "inputs": {**one_run(inputs), "target_pdb": first, "reference": second}})
    assert r.status == 201
    try:
        kept = store.read_meta(app.home / r.json["slug"])["inputs"]
        d = app.home / r.json["slug"] / "inputs"
        assert (Path(kept["target_pdb"]), Path(kept["reference"])) == (d / "target-structure.pdb", d / "reference-structure.pdb")
        assert (Path(kept["target_pdb"]).read_bytes(), Path(kept["reference"]).read_bytes()) == (construct.read_bytes(), reference.read_bytes())
        assert not Path(first).exists() and not Path(second).exists() and not Path(first).parent.exists()          # moved, with their folders
    finally:
        app.api.post(f"/api/jobs/{r.json['job']['id']}/cancel")
        appkit.wait_job(app.api, r.json["job"]["id"])
        app.api.post(f"/api/projects/{r.json['slug']}/delete")


def test_a_profile_file_is_adopted_like_the_structures(app, inputs, toy_dir):
    profile = app.api.post("/api/upload?name=" + enc("my profile.json"), body=(toy_dir / "reference" / "toy.profile.json").read_bytes()).json["path"]
    r = app.api.post("/api/projects", {"name": "With profile", "inputs": {**one_run(inputs), "profile": profile}})
    assert r.status == 201
    try:
        kept = store.read_meta(app.home / r.json["slug"])["inputs"]["profile"]
        assert Path(kept) == app.home / r.json["slug"] / "inputs" / "profile-my profile.json" and Path(kept).read_bytes() == (toy_dir / "reference" / "toy.profile.json").read_bytes()
        assert not Path(profile).exists() and r.json["inputs"]["profile"] == kept                   # the answer names the new path
    finally:
        app.api.post(f"/api/jobs/{r.json['job']['id']}/cancel")
        appkit.wait_job(app.api, r.json["job"]["id"])
        app.api.post(f"/api/projects/{r.json['slug']}/delete")


def test_a_copy_made_by_editing_the_inputs_keeps_its_own_input_files(app, inputs, toy_dir):
    def up(path, name):
        return app.api.post("/api/upload?name=" + enc(name), body=Path(path).read_bytes()).json["path"]

    construct, reference = toy_dir / "construct" / "toy_construct.pdb", toy_dir / "reference" / "toy_target.cif"
    base = {**one_run(inputs), "target_pdb": up(construct, "construct.pdb"), "reference": up(reference, "reference.cif")}
    slugs = []

    def start(name, given, retry=""):
        r = app.api.post("/api/projects", {"name": name, "inputs": given, "retry": retry})
        assert r.status == 201, r.text
        slugs.append(r.json["slug"])
        app.api.post(f"/api/jobs/{r.json['job']['id']}/cancel")
        appkit.wait_job(app.api, r.json["job"]["id"])
        return r.json["slug"]

    try:
        first = start("Original", base)
        kept = store.read_meta(app.home / first)["inputs"]
        copy = start("Original 2", {**base, "target_pdb": kept["target_pdb"], "reference": kept["reference"]})        # what the import page sends after Edit the inputs
        own = store.read_meta(app.home / copy)["inputs"]
        assert Path(own["target_pdb"]).parent == app.home / copy / "inputs" and Path(own["reference"]).parent == app.home / copy / "inputs"
        assert Path(kept["target_pdb"]).is_file() and Path(kept["reference"]).is_file()                      # copied: the original keeps its files
        assert app.api.post(f"/api/projects/{first}/delete").json == {"ok": True}
        own = store.read_meta(app.home / copy)["inputs"]
        assert Path(own["target_pdb"]).read_bytes() == construct.read_bytes() and Path(own["reference"]).read_bytes() == reference.read_bytes()
    finally:
        appkit.end_jobs(app.srv)
        for slug in slugs:
            if (app.home / slug).exists():
                app.api.post(f"/api/projects/{slug}/delete")


def test_old_uploads_are_removed_when_the_app_starts(tmp_path):
    home = tmp_path / "Binderscope"
    for name, days in (("old", 8), ("fresh", 1)):
        folder = store.uploads_dir(home.mkdir(exist_ok=True) or home) / name
        folder.mkdir()
        (folder / "x.cif").write_text("data")
        stamp = time.time() - days * 86400
        os.utime(folder, (stamp, stamp))
    with appkit.running(home):
        assert not (home / "_uploads" / "old").exists() and (home / "_uploads" / "fresh" / "x.cif").is_file()


def test_parallel_saves_of_decisions_all_succeed(app):
    """Two review pages saving at the same moment: 12 writers of 25 events each through the server, none refused, none lost."""
    slug, d = store.create(app.home, "Parallel", {"runs": []})
    failures: list = []

    def worker(k: int) -> None:
        for i in range(25):
            r = app.api.post(f"/api/projects/{slug}/state", {"events": json.dumps([ev(k * 1000 + i, f"2026-03-01T{k:02d}:00:{i:02d}Z")])})
            if r.status != 200:
                failures.append((r.status, r.text))

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    try:
        assert failures == []
        saved = json.loads(app.api.get(f"/api/projects/{slug}/state").json["events"])
        assert {e["id"] for e in saved} == {ev(k * 1000 + i, "")["id"] for k in range(12) for i in range(25)} and len(saved) == 300
    finally:
        app.api.post(f"/api/projects/{slug}/delete")


def test_a_null_byte_in_a_path_or_a_name_is_a_400_with_a_message(app, toy_project, inputs):
    r = app.api.get(f"/p/{toy_project.slug}/%00")
    assert r.status == 400 and "not valid" in r.error and "null character" in r.error
    before = homes_folders(app)
    r = app.api.post("/api/projects", {"name": "x\x00y", "inputs": inputs})
    assert r.status == 400 and "null character" in r.error
    assert homes_folders(app) == before                                                     # the project that was begun is removed again


def test_a_since_that_is_not_a_number_counts_from_the_start(app, toy_project):
    api, url = app.api, f"/api/jobs/{toy_project.job['id']}"
    full = toy_project.job["log"]
    assert len(full) > 20
    for since in ("abc", "", "1.5", "-5", "0x10", "1e3"):
        v = api.get(f"{url}?since={since}")
        assert v.status == 200 and v.json["log_from"] == 0 and v.json["log"] == full, since
    v = api.get(f"{url}?since=3").json
    assert v["log_from"] == 3 and v["log"] == full[3:]
    v = api.get(f"{url}?since=99999").json
    assert v["log_from"] == 99999 and v["log"] == [] and v["log_total"] == len(full)


@pytest.mark.parametrize("run", ["run01", None, 5, [1], True])
def test_a_run_that_is_not_an_object_is_a_400(app, inputs, run):
    before = homes_folders(app)
    r = app.api.post("/api/projects", {"name": "x", "inputs": {**inputs, "runs": [run]}})
    assert r.status == 400 and r.error == "Each run must have an id and a folder." and homes_folders(app) == before


def test_the_answer_to_a_new_project_names_the_files_and_a_retry_with_them_is_accepted(app, inputs, toy_dir):
    """What the import page does after Back to the inputs: it sends the paths the first answer gave, with the project's slug as retry."""
    def up(path, name):
        return app.api.post("/api/upload?name=" + enc(name), body=Path(path).read_bytes()).json["path"]

    uploads = {"target_pdb": up(toy_dir / "construct" / "toy_construct.pdb", "construct.pdb"), "reference": up(toy_dir / "reference" / "toy_target.cif", "reference.cif")}
    given = {**one_run(inputs), **uploads}
    first = app.api.post("/api/projects", {"name": "Page retry", "inputs": given})
    assert first.status == 201
    slug = first.json["slug"]
    try:
        kept = first.json["inputs"]
        assert kept["target_pdb"] == str(app.home / slug / "inputs" / "target-construct.pdb") and kept["reference"] == str(app.home / slug / "inputs" / "reference-reference.cif")
        assert kept["runs"][0]["id"] == "run02" and Path(kept["target_pdb"]).is_file() and not any(Path(v).exists() for v in uploads.values())
        app.api.post(f"/api/jobs/{first.json['job']['id']}/cancel")                         # a build that did not finish: the page offers Back to the inputs
        appkit.wait_job(app.api, first.json["job"]["id"])
        stale = app.api.post("/api/projects", {"name": "Page retry", "inputs": given, "retry": slug})
        assert stale.status == 400 and stale.error == "Choose the target structure."         # the upload paths of the first try are gone
        again = app.api.post("/api/projects", {"name": "Page retry", "inputs": {**given, "target_pdb": kept["target_pdb"], "reference": kept["reference"]}, "retry": slug})
        assert again.status == 201 and again.json["slug"] == slug and again.json["inputs"] == kept          # same project, the same files, nothing copied again
        assert sorted(p.name for p in (app.home / slug / "inputs").iterdir()) == ["reference-reference.cif", "target-construct.pdb"]
        app.api.post(f"/api/jobs/{again.json['job']['id']}/cancel")
        appkit.wait_job(app.api, again.json["job"]["id"])
    finally:
        appkit.end_jobs(app.srv)
        app.api.post(f"/api/projects/{slug}/delete")


def test_a_project_that_could_not_be_started_is_removed_again(app, inputs, toy_dir, monkeypatch):
    def fails(message):
        def fail(*a, **k):
            raise OSError(message)
        return fail

    def upload():
        return app.api.post("/api/upload?name=ref.cif", body=(toy_dir / "reference" / "toy_target.cif").read_bytes()).json["path"]

    store.uploads_dir(app.home)                                                             # the folder of uploads is there before and after
    before = homes_folders(app)
    path = upload()
    with monkeypatch.context() as mp:
        mp.setattr(store, "adopt", fails("disk full"))                                      # the files cannot be taken into the project
        r = app.api.post("/api/projects", {"name": "Never made", "inputs": {**one_run(inputs), "reference": path}})
        assert r.status == 500 and "disk full" in r.error and homes_folders(app) == before and Path(path).is_file()
    with monkeypatch.context() as mp:
        mp.setattr(app.srv, "start_build", fails("no process"))                             # the build cannot start
        r = app.api.post("/api/projects", {"name": "Never built", "inputs": {**one_run(inputs), "reference": upload()}})
        assert r.status == 500 and "no process" in r.error and homes_folders(app) == before
    assert app.srv.jobs.active() is None and app.api.get("/api/state").json["job"] is None
    kept_slug, kept_dir = store.create(app.home, "Kept", {**one_run(inputs)})               # a project that exists already is not the request's to remove
    meta = store.read_meta(kept_dir)
    meta["build"] = {"status": "failed", "error": "earlier problem"}
    store.write_meta(kept_dir, meta)
    try:
        with monkeypatch.context() as mp:
            mp.setattr(store, "adopt", fails("disk full"))
            r = app.api.post("/api/projects", {"name": "Kept", "inputs": {**one_run(inputs), "reference": upload()}, "retry": kept_slug})
        assert r.status == 500 and kept_dir.is_dir() and store.read_meta(kept_dir)["build"]["error"] == "earlier problem"
    finally:
        app.api.post(f"/api/projects/{kept_slug}/delete")
    assert homes_folders(app) == before


def test_a_failed_start_gives_the_moved_files_back_where_the_page_expects_them(app, inputs, toy_dir, monkeypatch):
    """The page still holds the upload paths when a start fails, so the files that were moved into the new project are moved back; then the folder is removed."""
    construct, reference = (toy_dir / "construct" / "toy_construct.pdb").read_bytes(), (toy_dir / "reference" / "toy_target.cif").read_bytes()

    def uploads(tag):
        up = lambda data, name: app.api.post("/api/upload?name=" + enc(name), body=data).json["path"]       # noqa: E731
        return {"target_pdb": up(construct, f"{tag}-construct.pdb"), "reference": up(reference, f"{tag}-reference.cif")}

    def no_process(*a, **k):
        raise OSError("no process")

    store.uploads_dir(app.home)                                                             # the folder of uploads is there before and after
    before = homes_folders(app)
    given = uploads("a")
    with monkeypatch.context() as mp:
        mp.setattr(app.srv, "start_build", no_process)
        r = app.api.post("/api/projects", {"name": "Never built", "inputs": {**one_run(inputs), **given}})
    assert r.status == 500 and "no process" in r.error and homes_folders(app) == before                      # the project folder is gone
    assert (Path(given["target_pdb"]).read_bytes(), Path(given["reference"]).read_bytes()) == (construct, reference)       # the files are where the page has them
    ok = app.api.post("/api/projects", {"name": "Built after all", "inputs": {**one_run(inputs), **given}})            # so the page can send the same paths again
    try:
        assert ok.status == 201 and ok.json["inputs"]["target_pdb"].endswith("/built-after-all/inputs/target-a-construct.pdb")
    finally:
        app.api.post(f"/api/jobs/{ok.json['job']['id']}/cancel")
        appkit.wait_job(app.api, ok.json["job"]["id"])
        app.api.post(f"/api/projects/{ok.json['slug']}/delete")

    given = uploads("b")                                                                                      # the second file cannot be taken in: the first is given back
    real, calls = store.adopt, []

    def second_fails(d, path, role=""):
        calls.append(role)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(d, path, role)

    with monkeypatch.context() as mp:
        mp.setattr(store, "adopt", second_fails)
        r = app.api.post("/api/projects", {"name": "Half made", "inputs": {**one_run(inputs), **given}})
    assert r.status == 500 and "disk full" in r.error and calls == ["target", "reference"] and homes_folders(app) == before
    assert (Path(given["target_pdb"]).read_bytes(), Path(given["reference"]).read_bytes()) == (construct, reference)

    given = uploads("c")                                                                                      # a retry: the project stays, the files go back out of its inputs/
    kept_slug, kept_dir = store.create(app.home, "Kept", {**one_run(inputs)})
    meta = store.read_meta(kept_dir)
    meta["build"] = {"status": "failed", "error": "earlier problem"}
    store.write_meta(kept_dir, meta)
    try:
        with monkeypatch.context() as mp:
            mp.setattr(app.srv, "start_build", no_process)
            r = app.api.post("/api/projects", {"name": "Kept", "inputs": {**one_run(inputs), **given}, "retry": kept_slug})
        assert r.status == 500 and kept_dir.is_dir() and store.read_meta(kept_dir)["build"]["error"] == "earlier problem"
        assert not [p for p in (kept_dir / "inputs").rglob("*") if p.is_file()]
        assert (Path(given["target_pdb"]).read_bytes(), Path(given["reference"]).read_bytes()) == (construct, reference)
    finally:
        app.api.post(f"/api/projects/{kept_slug}/delete")
    assert homes_folders(app) == before


def test_a_failed_start_leaves_the_files_of_another_project_alone(app, inputs, toy_dir, monkeypatch):
    """Edit the inputs copies the first project's files; when that copy's start fails only the copy goes."""
    up = app.api.post("/api/upload?name=ref.cif", body=(toy_dir / "reference" / "toy_target.cif").read_bytes()).json["path"]
    first = app.api.post("/api/projects", {"name": "First", "inputs": {**one_run(inputs), "reference": up}})
    assert first.status == 201
    try:
        app.api.post(f"/api/jobs/{first.json['job']['id']}/cancel")
        appkit.wait_job(app.api, first.json["job"]["id"])
        theirs = first.json["inputs"]["reference"]
        before = homes_folders(app)
        with monkeypatch.context() as mp:
            mp.setattr(app.srv, "start_build", lambda *a, **k: (_ for _ in ()).throw(OSError("no process")))
            r = app.api.post("/api/projects", {"name": "Second", "inputs": {**one_run(inputs), "reference": theirs}})
        assert r.status == 500 and homes_folders(app) == before and Path(theirs).is_file()
    finally:
        app.api.post(f"/api/projects/{first.json['slug']}/delete")


@pytest.mark.parametrize("campaign", ["toya", "toyb"])
def test_bindcraft_2_campaigns_can_be_built(app, inputs, toy_dir, toy_bc2_a, toy_bc2_b, campaign):
    """toya counts its binder residues from 0 (BindCraft 2 v1.0.x), toyb has no hotspot and no metadata: both are built, the campaign folder is not touched."""
    run = {"toya": toy_bc2_a, "toyb": toy_bc2_b}[campaign]
    folder = toy_dir / "bindcraft2" / campaign
    before = appkit.snapshot(folder)
    r = app.api.post("/api/projects", {"name": f"Campaign {campaign}", "inputs": {**inputs, "runs": [{"id": campaign, "path": str(folder)}]}})
    assert r.status == 201
    slug = r.json["slug"]
    try:
        job = appkit.wait_job(app.api, r.json["job"]["id"])
        assert job["status"] == "done", job["error"]
        p = app.api.get(f"/api/projects/{slug}").json
        assert p["status"] == "ready" and p["runs"] == [campaign] and (p["counts"]["accepted"], p["counts"]["families"]) == (len(run.accepted()), len(run.accepted_backbones()))
        zero_based = any("renumbered to one-based copies" in line for line in job["log"])
        assert zero_based is (campaign == "toya")
        pdbs = sorted((app.home / slug / "bundle" / "structures").glob("*.pdb"))
        assert pdbs
        for f in pdbs:
            chain_b = gemmi.read_structure(str(f))[0]["B"]
            assert [res.seqid.num for res in chain_b] == list(range(1, len(chain_b) + 1)), f.name          # the pages get binder residues 1 to n
        assert app.api.get(f"/p/{slug}/index.html").status == 200
    finally:
        app.api.post(f"/api/projects/{slug}/delete")
    assert appkit.snapshot(folder) == before
