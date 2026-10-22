"""Route tests for bc2serve.app, through FastAPI's TestClient.

This module needs the full stack (fastapi, httpx, numpy, biotite) and skips
cleanly without it, so a run on a machine with only the standard library stays
green.

IMPORTING `bc2serve.app` HAS SIDE EFFECTS. It makes its state, results and upload
directories, builds a `JobStore`, and that starts a scheduler thread and a
notification thread. So the three directory variables are pointed at a temporary
sandbox BEFORE the import, the backend is forced to `local`, and the card reader
is replaced right after, so that nothing here can touch a real state directory,
a real results root, a GPU, or the network. The module refuses to run if `app`
was already imported with other settings, because then the sandbox would not be
the one in force.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_NEEDED = ("fastapi", "httpx", "numpy", "biotite")
_MISSING = [name for name in _NEEDED if importlib.util.find_spec(name) is None]
if _MISSING:
    raise unittest.SkipTest("bc2serve.app needs " + ", ".join(_MISSING))

# --------------------------------------------------------------------- sandbox

_HOLDER = tempfile.TemporaryDirectory(prefix="bc2serve-routes-")
SANDBOX = Path(os.path.realpath(_HOLDER.name))
STATE = SANDBOX / "state"
RESULTS = SANDBOX / "results"
REPO = SANDBOX / "repo"
OUTSIDE = SANDBOX / "outside"
for _directory in (STATE, RESULTS, REPO / "settings", OUTSIDE):
    _directory.mkdir(parents=True)


def pdb_text(count: int = 3, chain: str = "A") -> str:
    """A few CA atoms in fixed columns, enough for the chain reader."""
    return "".join(
        f"ATOM  {n:5d} {' CA '} ALA {chain}{n:4d}    {float(n):8.3f}{0.0:8.3f}{0.0:8.3f}"
        "  1.00  0.00           C\n" for n in range(1, count + 1))


(REPO / "settings" / "shipped.pdb").write_text(pdb_text())
(OUTSIDE / "secret.pdb").write_text("SECRET-OUTSIDE-THE-ROOTS\n")

_ENV = {
    "BC2SERVE_STATE": str(STATE),
    "BC2SERVE_RESULTS": str(RESULTS),
    "BINDCRAFT_HOME": str(REPO),
    "BC2SERVE_BACKEND": "local",
    "BC2SERVE_RESULT_ROOTS": str(RESULTS),
    "BC2SERVE_RESERVED_GPUS": "",
}
_SAVED = {key: os.environ.get(key) for key in _ENV}
os.environ.update(_ENV)

if "bc2serve.app" in sys.modules:
    _already = sys.modules["bc2serve.app"]
    if Path(_already.STATE_DIR).resolve() != STATE:
        raise RuntimeError("bc2serve.app was imported before this module set its sandbox; "
                           "refusing to run route tests against someone else's directories")

from fastapi.testclient import TestClient            # noqa: E402

from bc2serve import app as app_module                # noqa: E402
from bc2serve import jobs as jobs_module              # noqa: E402

# The scheduler's first tick is five seconds after construction. Whatever it
# finds, it must find no cards, so no campaign can be launched.
_NO_CARDS = mock.patch.object(jobs_module, "read_cards", lambda: [])
_NO_CARDS.start()

assert Path(app_module.STATE_DIR).resolve() == STATE
assert Path(app_module.RESULTS_ROOT).resolve() == RESULTS
assert Path(app_module.REPO).resolve() == REPO


def tearDownModule() -> None:
    app_module.store.stop()
    _NO_CARDS.stop()
    for key, old in _SAVED.items():
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old
    shutil.rmtree(SANDBOX, ignore_errors=True)


store = app_module.store


def clear_jobs() -> None:
    with sqlite3.connect(store.database) as db:
        db.execute("DELETE FROM jobs")


def add_job(name: str, folder: Path, *, project: str = "", state: str = "done") -> int:
    """A finished campaign row. Set to a settled state at once so the scheduler,
    which looks at queued rows, has nothing to pick up."""
    job_id = store.submit(name, "", folder / "campaign.json", folder, 1, project=project)
    with sqlite3.connect(store.database) as db:
        db.execute("UPDATE jobs SET state=?, finished_at=? WHERE id=?",
                   (state, time.time(), job_id))
    return job_id


class Routes(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app_module.app)
        clear_jobs()


# ----------------------------------------------------------------- origin guard
#
# Two harmless state-changing routes are used as probes. Both reach their handler
# and answer without doing anything: a handler that was reached answers 200 or
# 400, and a refused request answers 403 from the middleware before any handler.
#
#   JSON  POST /api/jobs/archive   {"ids": []}  -> 200
#   form  POST /api/fetch-pdb      entry=zz     -> 400 (not a PDB id; never fetched)
#   multipart upload  POST /api/upload, a .txt  -> 400 (wrong suffix; nothing written)

class OriginGuardTests(Routes):
    def json_post(self, **headers):
        return self.client.post("/api/jobs/archive", json={"ids": []}, headers=headers)

    def form_post(self, **headers):
        return self.client.post("/api/fetch-pdb", data={"entry": "zz"}, headers=headers)

    def multipart_post(self, **headers):
        return self.client.post("/api/upload", files={"file": ("note.txt", b"hello")},
                                headers=headers)

    def each(self):
        yield "json", self.json_post
        yield "form", self.form_post
        yield "multipart", self.multipart_post

    REACHED = {"json": 200, "form": 400, "multipart": 400}

    def test_a_request_with_no_browser_headers_passes(self):
        for kind, post in self.each():
            self.assertEqual(post().status_code, self.REACHED[kind], kind)

    def test_cross_site_is_refused(self):
        for kind, post in self.each():
            response = post(**{"Sec-Fetch-Site": "cross-site"})
            self.assertEqual(response.status_code, 403, kind)
            self.assertIn("another site", response.json()["detail"])

    def test_same_site_is_refused_too(self):
        # A page on another port of localhost is same-site, and still not us.
        for kind, post in self.each():
            self.assertEqual(post(**{"Sec-Fetch-Site": "same-site"}).status_code, 403, kind)

    def test_same_origin_and_none_pass(self):
        for kind, post in self.each():
            for value in ("same-origin", "none"):
                self.assertEqual(post(**{"Sec-Fetch-Site": value}).status_code,
                                 self.REACHED[kind], f"{kind} {value}")

    def test_an_unrecognised_sec_fetch_site_value_is_refused(self):
        for kind, post in self.each():
            self.assertEqual(post(**{"Sec-Fetch-Site": "banana"}).status_code, 403, kind)

    def test_an_origin_for_another_host_is_refused(self):
        for kind, post in self.each():
            response = post(Origin="http://evil.example.invalid")
            self.assertEqual(response.status_code, 403, kind)
            self.assertIn("not this service", response.json()["detail"])

    def test_an_origin_for_the_same_host_on_another_port_is_refused(self):
        for kind, post in self.each():
            self.assertEqual(post(Origin="http://testserver:9999").status_code, 403, kind)

    def test_an_origin_matching_the_host_passes(self):
        for kind, post in self.each():
            self.assertEqual(post(Origin="http://testserver").status_code,
                             self.REACHED[kind], kind)

    def test_origin_matching_ignores_case(self):
        self.assertEqual(self.json_post(Origin="HTTP://TESTSERVER").status_code, 200)

    def test_a_matching_origin_with_a_port(self):
        response = self.client.post("/api/jobs/archive", json={"ids": []},
                                    headers={"Host": "localhost:8123",
                                             "Origin": "http://localhost:8123"})
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/jobs/archive", json={"ids": []},
                                    headers={"Host": "localhost:8123",
                                             "Origin": "http://localhost:8124"})
        self.assertEqual(response.status_code, 403)

    def test_origin_null_and_garbage_are_refused_not_crashed_on(self):
        for origin in ("null", "http://[broken", "not a url", "http://"):
            self.assertEqual(self.json_post(Origin=origin).status_code, 403, repr(origin))

    def test_both_headers_are_checked_so_one_cannot_vouch_for_the_other(self):
        self.assertEqual(self.json_post(**{"Sec-Fetch-Site": "same-origin",
                                           "Origin": "http://evil.example.invalid"}).status_code, 403)
        self.assertEqual(self.json_post(**{"Sec-Fetch-Site": "cross-site",
                                           "Origin": "http://testserver"}).status_code, 403)

    def test_a_refused_request_changes_nothing(self):
        job = add_job("kept", RESULTS / "kept")
        refused = self.client.post("/api/jobs/archive", json={"ids": [job]},
                                   headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(refused.status_code, 403)
        self.assertIsNone(store.get(job)["archived_at"])
        allowed = self.client.post("/api/jobs/archive", json={"ids": [job]})
        self.assertEqual(allowed.json()["changed"], 1)
        self.assertIsNotNone(store.get(job)["archived_at"])

    def test_a_refused_upload_writes_no_file(self):
        before = set(app_module.UPLOAD_DIR.iterdir())
        response = self.client.post("/api/upload", files={"file": ("x.pdb", pdb_text().encode())},
                                    headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(set(app_module.UPLOAD_DIR.iterdir()), before)

    def test_every_unsafe_method_is_guarded_even_on_routes_that_do_not_exist(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = self.client.request(method, "/api/no-such-route",
                                           headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual(response.status_code, 403, method)

    def test_a_bodyless_delete_is_guarded(self):
        # The easiest to forget: no body, so no content-type check to fall back on.
        self.assertEqual(self.client.delete("/api/jobs/999",
                                            headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.delete("/api/review/anything",
                                            headers={"Origin": "http://evil.example.invalid"}).status_code, 403)
        # Same-origin gets to the handler, which says there is no such job.
        self.assertEqual(self.client.delete("/api/jobs/999",
                                            headers={"Sec-Fetch-Site": "same-origin"}).status_code, 409)

    def test_a_get_is_never_refused_however_foreign_it_looks(self):
        foreign = {"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example.invalid"}
        for url in ("/api/gpus", "/api/presets", "/api/jobs", "/api/designs",
                    "/api/notify/topic", "/api/storage"):
            self.assertEqual(self.client.get(url, headers=foreign).status_code, 200, url)
        self.assertEqual(self.client.get("/api/gpus", headers={"Sec-Fetch-Site": "same-site"}).status_code, 200)

    def test_head_and_options_are_not_treated_as_state_changing(self):
        foreign = {"Sec-Fetch-Site": "cross-site"}
        for method in ("HEAD", "OPTIONS"):
            self.assertNotEqual(self.client.request(method, "/api/gpus", headers=foreign).status_code,
                                403, method)

    def test_a_cross_site_request_cannot_start_a_campaign(self):
        before = len(store.list_jobs())
        response = self.client.post("/api/jobs", json={"name": "x"},
                                    headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(store.list_jobs()), before)


class ForeignFunctionTests(unittest.TestCase):
    """`_foreign` on its own, with no HTTP in the way."""

    def check(self, **headers):
        return app_module._foreign(SimpleNamespace(headers={k.lower().replace("_", "-"): v
                                                            for k, v in headers.items()}))

    def test_no_headers(self):
        self.assertIsNone(self.check())

    def test_site_values(self):
        self.assertIsNone(self.check(sec_fetch_site="same-origin"))
        self.assertIsNone(self.check(sec_fetch_site="none"))
        self.assertIn("cross-site", self.check(sec_fetch_site="cross-site"))
        self.assertIn("same-site", self.check(sec_fetch_site="same-site"))

    def test_origin_without_a_host_header_is_refused(self):
        self.assertIsNotNone(self.check(origin="http://localhost:1"))

    def test_origin_and_host_agree(self):
        self.assertIsNone(self.check(origin="http://localhost:8123", host="localhost:8123"))
        self.assertIsNone(self.check(origin="http://LOCALHOST:8123", host="localhost:8123"))
        self.assertIsNotNone(self.check(origin="https://localhost:8123/x", host="localhost:8124"))

    def test_a_path_on_the_origin_does_not_confuse_the_comparison(self):
        self.assertIsNone(self.check(origin="http://localhost:8123/some/page", host="localhost:8123"))

    def test_malformed_origin_is_a_refusal_not_an_exception(self):
        for origin in ("http://[broken", "null", "x"):
            self.assertIsNotNone(self.check(origin=origin, host="localhost:8123"), origin)


# ------------------------------------------------------------------------ _body

class BodyTests(Routes):
    URL = "/api/jobs/archive"
    BODY = '{"ids": []}'

    def post(self, content, content_type=None):
        headers = {"Content-Type": content_type} if content_type else {}
        return self.client.post(self.URL, content=content, headers=headers)

    def test_plain_text_carrying_valid_json_is_refused_with_415(self):
        response = self.post(self.BODY, "text/plain")
        self.assertEqual(response.status_code, 415)
        self.assertIn("application/json", response.json()["detail"])

    def test_json_content_type_works(self):
        self.assertEqual(self.post(self.BODY, "application/json").status_code, 200)

    def test_parameters_and_case_on_the_content_type_are_tolerated(self):
        for kind in ("application/json; charset=utf-8", "APPLICATION/JSON", "application/json ;x=y"):
            self.assertEqual(self.post(self.BODY, kind).status_code, 200, kind)

    def test_other_content_types_are_refused(self):
        for kind in ("application/x-www-form-urlencoded", "multipart/form-data; boundary=x",
                     "application/jsonp", "text/json", "application/ld+json"):
            self.assertEqual(self.post(self.BODY, kind).status_code, 415, kind)

    def test_a_missing_content_type_is_refused(self):
        self.assertEqual(self.post(self.BODY).status_code, 415)

    def test_unparseable_json_is_a_400_not_a_500(self):
        for text in ("{not json", "", "[1, 2", "\xff"):
            response = self.post(text.encode("latin-1"), "application/json")
            self.assertEqual(response.status_code, 400, repr(text))
            self.assertIn("not valid JSON", response.json()["detail"])

    def test_json_that_is_not_an_object_is_a_400(self):
        for text in ("[]", "[1]", '"text"', "7", "null", "true"):
            response = self.post(text, "application/json")
            self.assertEqual(response.status_code, 400, text)
            self.assertIn("JSON object", response.json()["detail"])

    def test_the_same_rule_holds_on_every_json_route(self):
        for url in ("/api/epitope-check", "/api/estimate", "/api/trim", "/api/jobs",
                    "/api/jobs/project", "/api/notify/test", "/api/review/anything"):
            self.assertEqual(self.client.post(url, content="{}", headers={"Content-Type": "text/plain"}
                                              ).status_code, 415, url)

    def test_storage_refresh_with_no_body_at_all_still_works(self):
        response = self.client.post("/api/storage/refresh")
        self.assertEqual(response.status_code, 200)
        self.assertIn("refreshing", response.json())

    def test_storage_refresh_accepts_json_and_an_empty_object(self):
        self.assertEqual(self.client.post("/api/storage/refresh", json={}).status_code, 200)

    def test_storage_refresh_with_a_body_still_needs_the_content_type(self):
        response = self.client.post("/api/storage/refresh", content="{}",
                                    headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status_code, 415)

    def test_storage_refresh_key_must_be_a_known_string(self):
        self.assertEqual(self.client.post("/api/storage/refresh", json={"key": 5}).status_code, 400)
        self.assertEqual(self.client.post("/api/storage/refresh", json={"key": "nope"}).status_code, 404)
        self.assertEqual(self.client.post("/api/storage/refresh", json={"key": "uploads"}).status_code, 200)


# ---------------------------------------------------------------------- _inside

class InsideTests(Routes):
    """`_inside` is reached through the file routes. A path is served only from
    the upload folder, the repository's settings, and the results roots."""

    def structure(self, path) -> object:
        return self.client.get("/api/structure", params={"path": str(path)})

    def test_a_file_in_the_upload_folder_is_served(self):
        target = app_module.UPLOAD_DIR / "uploaded.pdb"
        target.write_text(pdb_text())
        response = self.structure(target)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, pdb_text())

    def test_a_file_in_the_results_root_is_served(self):
        target = RESULTS / "camp" / "model.cif"
        target.parent.mkdir(exist_ok=True)
        target.write_text("data_model\n")
        self.assertEqual(self.structure(target).text, "data_model\n")

    def test_a_shipped_preset_structure_is_served(self):
        self.assertEqual(self.structure(REPO / "settings" / "shipped.pdb").status_code, 200)

    def test_a_file_outside_every_root_is_refused(self):
        response = self.structure(OUTSIDE / "secret.pdb")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("SECRET", response.text)

    def test_traversal_out_of_an_allowed_root_is_refused(self):
        for climb in (f"{app_module.UPLOAD_DIR}/../../outside/secret.pdb",
                      f"{RESULTS}/../outside/secret.pdb",
                      f"{REPO}/settings/../../outside/secret.pdb"):
            response = self.structure(climb)
            self.assertEqual(response.status_code, 403, climb)
            self.assertNotIn("SECRET", response.text)

    def test_a_sibling_whose_name_starts_like_a_root_is_refused(self):
        # As strings /results_old would pass for a file under /results.
        sibling = SANDBOX / (RESULTS.name + "_old")
        sibling.mkdir()
        (sibling / "x.pdb").write_text("SECRET-SIBLING\n")
        response = self.structure(sibling / "x.pdb")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("SECRET", response.text)

    def test_a_symlink_that_leaves_the_root_is_refused(self):
        link = app_module.UPLOAD_DIR / "sneaky.pdb"
        link.symlink_to(OUTSIDE / "secret.pdb")
        self.addCleanup(link.unlink)
        response = self.structure(link)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("SECRET", response.text)

    def test_a_directory_symlink_that_leaves_the_root_is_refused(self):
        link = RESULTS / "linkdir"
        link.symlink_to(OUTSIDE, target_is_directory=True)
        self.addCleanup(link.unlink)
        self.assertEqual(self.structure(link / "secret.pdb").status_code, 403)

    def test_a_missing_file_inside_a_root_is_a_404_not_a_403(self):
        self.assertEqual(self.structure(RESULTS / "nothing.pdb").status_code, 404)

    def test_the_root_itself_is_not_a_file(self):
        self.assertEqual(self.structure(RESULTS).status_code, 404)

    def test_a_nul_byte_is_refused_and_serves_nothing(self):
        response = self.client.get("/api/structure", params={"path": f"{RESULTS}/a\x00b"})
        self.assertIn(response.status_code, (403, 404))
        self.assertNotIn("SECRET", response.text)

    def test_a_relative_path_is_not_resolved_against_a_root(self):
        response = self.client.get("/api/structure", params={"path": "secret.pdb"})
        self.assertIn(response.status_code, (403, 404))

    def test_the_chains_route_uses_the_same_rule(self):
        inside = app_module.UPLOAD_DIR / "chains.pdb"
        inside.write_text(pdb_text(3, "B"))
        response = self.client.get("/api/chains", params={"path": str(inside)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chains"], [{"chain": "B", "residues": [1, 2, 3]}])
        self.assertEqual(self.client.get("/api/chains", params={"path": str(OUTSIDE / "secret.pdb")}
                                         ).status_code, 403)

    def test_every_other_file_route_refuses_a_path_outside_the_roots(self):
        outside = str(OUTSIDE / "secret.pdb")
        self.assertEqual(self.client.get("/api/analysis", params={"path": outside}).status_code, 403)
        self.assertEqual(self.client.post("/api/epitope-check", json={"path": outside}).status_code, 403)
        self.assertEqual(self.client.post("/api/trim", json={"path": outside, "keep": [1]}).status_code, 403)
        self.assertEqual(self.client.get("/api/review/anything/reference",
                                         params={"path": outside}).status_code, 400)
        self.assertEqual(self.client.get("/api/review/anything/chains",
                                         params={"path": outside}).status_code, 400)
        self.assertEqual(self.client.post("/api/review/anything",
                                          json={"reference_path": outside}).status_code, 400)

    def test_a_refused_trim_writes_nothing(self):
        before = set(app_module.UPLOAD_DIR.iterdir())
        self.client.post("/api/trim", json={"path": str(OUTSIDE / "secret.pdb"), "keep": [1]})
        self.assertEqual(set(app_module.UPLOAD_DIR.iterdir()), before)


class InsideFunctionTests(unittest.TestCase):
    def test_direct_cases(self):
        root = SANDBOX / "direct"
        (root / "a").mkdir(parents=True)
        (root / "a" / "f").write_text("x")
        self.assertTrue(app_module._inside(root / "a" / "f", root))
        self.assertTrue(app_module._inside(root, root))
        self.assertTrue(app_module._inside(root / "a" / "f", SANDBOX / "elsewhere", root))
        self.assertFalse(app_module._inside(root / ".." / "outside", root))
        self.assertFalse(app_module._inside(Path(str(root) + "_old") / "f", root))
        self.assertFalse(app_module._inside(root / "a" / "f"))
        self.assertFalse(app_module._inside(Path("a\x00b"), root))


# -------------------------------------------------------------------- the review

class ReviewNameTests(Routes):
    """A campaign name arrives in a URL and is shaped before it touches a path."""

    def test_names_the_pattern_refuses_are_a_404(self):
        for name in (".hidden", "_reviews", "-x", "x" * 129, "a%20b", "%2e%2e", "a%2fb",
                     "%2e%2e%2foutside"):
            for url in (f"/api/review/{name}", f"/api/review/{name}/log", f"/review/{name}/index.html"):
                self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_a_well_formed_name_with_no_campaign_is_a_404(self):
        self.assertEqual(self.client.get("/api/review/never-ran").status_code, 404)

    def make_review(self):
        folder = RESULTS / "camp"
        folder.mkdir(exist_ok=True)
        (folder / "campaign.json").write_text("{}")
        home = RESULTS / "_reviews" / "camp"
        (home / "bundle").mkdir(parents=True, exist_ok=True)
        (home / "review.json").write_text('{"state": "done"}')
        (home / "bundle" / "index.html").write_text("<p>review</p>")
        (home / "bundle" / "app.js").write_text("// js")
        (home / "secret.txt").write_text("SECRET-BESIDE-THE-BUNDLE")
        return home

    def test_the_bundle_is_served_and_a_climb_out_of_it_is_not(self):
        self.make_review()
        self.assertEqual(self.client.get("/review/camp/index.html").status_code, 200)
        self.assertEqual(self.client.get("/review/camp/").text, "<p>review</p>")
        self.assertEqual(self.client.get("/review/camp/app.js").text, "// js")
        for climb in ("%2e%2e/secret.txt", "..%2fsecret.txt", "%2e%2e/%2e%2e/review.json",
                      "sub/%2e%2e/%2e%2e/secret.txt"):
            response = self.client.get(f"/review/camp/{climb}")
            self.assertEqual(response.status_code, 404, climb)
            self.assertNotIn("SECRET", response.text)

    def test_the_address_without_a_slash_redirects_to_the_one_with(self):
        response = self.client.get("/review/camp", follow_redirects=False)
        self.assertIn(response.status_code, (302, 307))
        self.assertEqual(response.headers["location"], "/review/camp/")


# ----------------------------------------------------------------- the queue

class QueueDefaultTests(unittest.TestCase):
    """No cluster's queue name is shipped as a fallback (jobs.py needs numpy, so it is tested here)."""

    def test_nothing_configured_means_no_queue_is_named(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BC2SERVE_SGE_QUEUE", None)
            self.assertEqual(jobs_module._queue_name(None), "")
            self.assertEqual(jobs_module._queue_name(""), "")
            self.assertEqual(jobs_module._queue_words(None), "waiting in the queue")

    def test_the_environment_names_one_and_the_job_may_override_it(self):
        with mock.patch.dict(os.environ, {"BC2SERVE_SGE_QUEUE": "SITE-GPU"}):
            self.assertEqual(jobs_module._queue_name(None), "SITE-GPU")
            self.assertEqual(jobs_module._queue_words(None), "waiting in the SITE-GPU queue")
            self.assertEqual(jobs_module._queue_name("OTHER"), "OTHER")
            self.assertEqual(jobs_module._queue_words("OTHER"), "waiting in the OTHER queue")


# ----------------------------------------------------------- importing a run

class ImportRouteTests(Routes):
    """A run linked in from outside the results root, through the HTTP surface.

    The ordering matters as much as the behaviour: `/api/review/{campaign}`
    matches any one segment, so these routes only work because they are
    registered first, and the words are refused as campaign names.
    """

    def make_run(self, name: str = "theirs") -> Path:
        folder = OUTSIDE / name
        (folder / "2_Refolded").mkdir(parents=True, exist_ok=True)
        (folder / "3_Ranked").mkdir(parents=True, exist_ok=True)
        (folder / "campaign_metadata.json").write_text(
            '{"settings": {"targets": [{"target_path": "/gone/t.cif", "chains": "A"}]}}')
        (folder / "2_Refolded" / "!_Refolded.csv").write_text("design,outcome,hash\nd1,passed,h1\n")
        (folder / "3_Ranked" / "!_Ranked.csv").write_text("design,rank,hash\nd1_seq0,1,h1\n")
        return folder

    def tearDown(self) -> None:
        for row in app_module.imported.rows():
            try:
                app_module.imported.remove(row["name"])
            except Exception:                        # the test may have removed it already
                pass

    def test_the_list_route_is_not_taken_for_a_campaign_called_imports(self):
        response = self.client.get("/api/review/imports")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_a_run_outside_the_roots_is_linked_and_then_listed_as_a_campaign(self):
        folder = self.make_run()
        answer = self.client.post("/api/review/import", json={"path": str(folder)})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["name"], "theirs")
        self.assertTrue((RESULTS / "theirs").is_symlink())
        listed = self.client.get("/api/review").json()
        self.assertIn("theirs", [row["campaign"] for row in listed])
        self.assertEqual([row["name"] for row in self.client.get("/api/review/imports").json()], ["theirs"])

    def test_a_folder_that_is_not_a_run_is_a_400_that_says_so(self):
        answer = self.client.post("/api/review/import", json={"path": str(OUTSIDE)})
        self.assertEqual(answer.status_code, 400)
        self.assertIn("campaign_metadata.json", answer.json()["detail"])

    def test_a_relative_path_is_refused(self):
        answer = self.client.post("/api/review/import", json={"path": "runs/mine"})
        self.assertEqual(answer.status_code, 400)

    def test_the_structures_of_an_imported_run_may_be_read(self):
        folder = self.make_run("withfile")
        structure = folder / "2_Refolded" / "Complexes" / "d1_candidate1.cif"
        structure.parent.mkdir(parents=True, exist_ok=True)
        structure.write_text("data_x\n")
        # Before the import this path is outside every root the service reads from.
        self.assertFalse(app_module._inside(structure, *app_module._structure_roots()))
        self.client.post("/api/review/import", json={"path": str(folder)})
        self.assertTrue(app_module._inside(structure, *app_module._structure_roots()))

    def test_a_target_given_at_import_stands_in_for_the_one_that_is_not_here(self):
        folder = self.make_run("needstarget")
        target = OUTSIDE / "given.pdb"
        target.write_text(pdb_text())
        self.client.post("/api/review/import", json={"path": str(folder), "construct": str(target)})
        described = self.client.get("/api/review/needstarget").json()
        self.assertTrue(described["construct"]["exists"])
        self.assertEqual(Path(described["construct"]["path"]).name, "given.pdb")

    def test_without_a_target_the_run_says_what_is_missing_rather_than_failing(self):
        self.client.post("/api/review/import", json={"path": str(self.make_run("notarget"))})
        described = self.client.get("/api/review/notarget").json()
        self.assertFalse(described["construct"]["exists"])
        self.assertIn("no longer where it was", described["why_not"])

    def test_forgetting_takes_the_link_and_leaves_the_run(self):
        folder = self.make_run("temporary")
        self.client.post("/api/review/import", json={"path": str(folder)})
        answer = self.client.delete("/api/review/import/temporary")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertFalse((RESULTS / "temporary").exists())
        self.assertTrue((folder / "campaign_metadata.json").is_file())

    def test_forgetting_a_campaign_that_was_never_imported_is_refused(self):
        (RESULTS / "ourown").mkdir(exist_ok=True)
        answer = self.client.delete("/api/review/import/ourown")
        self.assertEqual(answer.status_code, 400)
        self.assertTrue((RESULTS / "ourown").is_dir())


# ----------------------------------------------------------------- /api/designs

class DesignsRouteTests(Routes):
    def setUp(self) -> None:
        super().setUp()
        self.alpha = RESULTS / "alpha"
        self.alpha.mkdir(exist_ok=True)
        (self.alpha / "campaign.json").write_text("{}")
        self.ghost = RESULTS / "ghost"           # never created: the folder is gone
        self.beta = RESULTS / "beta"
        self.beta.mkdir(exist_ok=True)
        self.a = add_job("alpha", self.alpha, project="Alpha project")
        self.g = add_job("ghost", self.ghost, project="")
        self.b = add_job("beta", self.beta, project="odd/label?with#chars")

    def names(self, **params):
        response = self.client.get("/api/designs", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return sorted(c["name"] for c in response.json()["campaigns"]), response.json()

    def test_an_unknown_archived_value_is_a_400(self):
        for value in ("bogus", "Live", "", "true", "1", "all "):
            response = self.client.get("/api/designs", params={"archived": value})
            self.assertEqual(response.status_code, 400, repr(value))
            self.assertIn("live, only or all", response.json()["detail"])

    def test_the_three_archive_views(self):
        for value in ("live", "only", "all"):
            self.assertEqual(self.client.get("/api/designs", params={"archived": value}).status_code, 200)

    def test_the_default_view_is_live(self):
        names, _ = self.names()
        self.assertEqual(names, ["alpha", "beta", "ghost"])
        store.set_archived([self.b], True)
        self.assertEqual(self.names()[0], ["alpha", "ghost"])
        self.assertEqual(self.names(archived="only")[0], ["beta"])
        self.assertEqual(self.names(archived="all")[0], ["alpha", "beta", "ghost"])

    def test_leaving_project_out_means_every_project(self):
        self.assertEqual(self.names()[0], ["alpha", "beta", "ghost"])

    def test_an_empty_project_means_unfiled_and_only_unfiled(self):
        # `project=` with nothing after it is a value, not an omission.
        response = self.client.get("/api/designs?project=")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c["name"] for c in response.json()["campaigns"]], ["ghost"])

    def test_a_named_project(self):
        self.assertEqual(self.names(project="Alpha project")[0], ["alpha"])

    def test_a_label_with_a_slash_a_question_mark_and_a_hash_survives_the_query_string(self):
        self.assertEqual(self.names(project="odd/label?with#chars")[0], ["beta"])

    def test_an_unknown_project_is_an_empty_list_not_an_error(self):
        names, body = self.names(project="no such project")
        self.assertEqual((names, body["designs"]), ([], []))

    def test_a_campaign_whose_folder_is_gone_is_listed_as_missing_not_a_500(self):
        _, body = self.names()
        by_name = {c["name"]: c for c in body["campaigns"]}
        self.assertIs(by_name["ghost"]["missing"], True)
        self.assertIs(by_name["alpha"]["missing"], False)
        self.assertEqual(by_name["ghost"]["counts"]["passed"], 0)
        self.assertIsNone(by_name["ghost"]["review"])

    def test_the_row_carries_what_the_page_needs(self):
        _, body = self.names()
        row = next(c for c in body["campaigns"] if c["name"] == "alpha")
        self.assertEqual((row["project"], row["job_id"], row["state"], row["archived"]),
                         ("Alpha project", self.a, "done", False))
        self.assertIn("projects", body)

    def test_filtering_by_campaign_name_and_by_full_path(self):
        self.assertEqual(self.names(campaign="alpha")[0], ["alpha"])
        self.assertEqual(self.names(campaign=str(self.alpha))[0], ["alpha"])
        self.assertEqual(self.names(campaign="ghost")[0], ["ghost"])
        self.assertEqual(self.names(campaign="nothing")[0], [])

    def test_a_campaign_filter_cannot_widen_the_list(self):
        self.assertEqual(self.names(campaign="../outside")[0], [])
        self.assertEqual(self.names(campaign="alpha", project="odd/label?with#chars")[0], [])

    def test_accepted_designs_are_listed_for_a_folder_that_has_them(self):
        from bc2serve import tables
        from bc2serve.tests.test_designs import make_campaign, refolded_row, ranked_row
        tables.clear_cache()
        folder = make_campaign(RESULTS, "withdesigns",
                               [refolded_row("x_seq0", "AAAA", "h1"),
                                refolded_row("x_seq1", "CCCC", "h2", outcome="rejected")],
                               ranked=[ranked_row(1, "x_seq0", "AAAA", "h1")])
        add_job("withdesigns", folder)
        _, body = self.names(campaign="withdesigns")
        self.assertEqual(len(body["designs"]), 1)
        (entry,) = body["campaigns"]
        self.assertEqual((entry["counts"]["passed"], entry["counts"]["ranked"]), (1, 1))
        self.assertIs(entry["missing"], False)

    def test_an_empty_database_is_a_valid_answer(self):
        clear_jobs()
        response = self.client.get("/api/designs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["campaigns"], response.json()["designs"]), ([], []))


# ------------------------------------------------------------- other small routes

class MiscRouteTests(Routes):
    def test_presets_come_from_the_repository_the_app_was_pointed_at(self):
        (REPO / "settings" / "target").mkdir(parents=True, exist_ok=True)
        (REPO / "settings" / "target" / "route_probe.json").write_text('{"description": "probe", "targets": []}')
        self.addCleanup((REPO / "settings" / "target" / "route_probe.json").unlink)
        body = self.client.get("/api/presets").json()
        self.assertIn("route_probe", [t["name"] for t in body["targets"]])
        self.assertEqual(body["results_root"], str(RESULTS))

    def test_gpus_with_no_cards(self):
        self.assertEqual(self.client.get("/api/gpus").json(), {"cards": []})

    def test_the_cluster_is_reported_as_local_without_a_scheduler(self):
        with mock.patch.object(app_module.sge, "available", return_value=False):
            body = self.client.get("/api/cluster").json()
        self.assertEqual((body["backend"], body["queues"], body["gpus"]), ("local", [], []))

    def test_the_cluster_box_follows_the_backend_setting_not_just_qstat(self):
        with mock.patch.dict(os.environ, {"BC2SERVE_BACKEND": "local"}), \
             mock.patch.object(app_module.sge, "available", return_value=True):
            body = self.client.get("/api/cluster?fresh=true").json()
        self.assertEqual(body["backend"], "local")

    def test_a_cluster_with_no_image_or_parameters_set_says_so_on_the_page(self):
        snap = {"queues": [], "gpus": [], "read_at": 0}
        with mock.patch.dict(os.environ, {"BC2SERVE_BACKEND": "sge"}), \
             mock.patch.object(app_module.sge, "snapshot", return_value=dict(snap)):
            for key in ("BC2SERVE_SGE_SIF", "BC2SERVE_SGE_PARAMS"):
                os.environ.pop(key, None)          # restored with the rest when the patch ends
            body = self.client.get("/api/cluster?fresh=true").json()
        self.assertEqual(body["backend"], "sge")
        self.assertIn("BC2SERVE_SGE_SIF and BC2SERVE_SGE_PARAMS", body["note"])

    def test_a_new_topic_is_long_and_not_repeated(self):
        first = self.client.get("/api/notify/topic").json()["topic"]
        second = self.client.get("/api/notify/topic").json()["topic"]
        self.assertTrue(len(first.split("-", 1)[1]) >= 16)       # never printed on failure
        self.assertTrue(first != second)

    def test_the_test_notification_never_leaves_the_machine_in_these_tests(self):
        with mock.patch.object(app_module.notify, "send", return_value=True) as sent:
            ok = self.client.post("/api/notify/test", json={"topic": "fixture-topic"})
        self.assertEqual((ok.status_code, ok.json()), (200, {"sent": True}))
        self.assertEqual(sent.call_args.args[0], "fixture-topic")
        with mock.patch.object(app_module.notify, "send", return_value=False):
            self.assertEqual(self.client.post("/api/notify/test", json={"topic": "t"}).status_code, 502)
        with mock.patch.object(app_module.notify, "send") as sent:
            self.assertEqual(self.client.post("/api/notify/test", json={"topic": "  "}).status_code, 400)
        sent.assert_not_called()

    def test_project_move_and_archive_validate_their_ids(self):
        for url in ("/api/jobs/project", "/api/jobs/archive"):
            for ids in ("1", [1, "x"], None, {"a": 1}):
                self.assertEqual(self.client.post(url, json={"ids": ids}).status_code, 400, (url, ids))

    def test_an_unknown_job_is_a_404(self):
        for tail in ("", "/log", "/results", "/campaign"):
            self.assertEqual(self.client.get(f"/api/jobs/987654{tail}").status_code, 404, tail)

    def test_a_non_numeric_job_id_is_a_422_not_a_500(self):
        self.assertEqual(self.client.get("/api/jobs/abc").status_code, 422)

    def test_storage_answers_at_once(self):
        response = self.client.get("/api/storage")
        self.assertEqual(response.status_code, 200)
        for key in ("projects", "campaigns", "reviews", "reclaimable", "disks", "archived", "measuring"):
            self.assertIn(key, response.json())

    def test_upload_keeps_only_the_basename_of_what_it_is_given(self):
        response = self.client.post("/api/upload", files={
            "file": ("../../../outside/evil.pdb", pdb_text().encode())})
        self.assertEqual(response.status_code, 200)
        saved = Path(response.json()["path"])
        self.assertEqual(saved.parent, app_module.UPLOAD_DIR)
        self.assertFalse((OUTSIDE / "evil.pdb").exists())
        self.addCleanup(lambda: saved.unlink(missing_ok=True))

    def test_upload_refuses_other_file_types(self):
        for name in ("run.sh", "x.txt", "noext", "x.pdb.exe"):
            self.assertEqual(self.client.post("/api/upload", files={"file": (name, b"x")}).status_code,
                             400, name)

    def test_fetch_routes_validate_before_going_anywhere(self):
        # Invalid identifiers are refused before any URL is built, so no network is touched.
        with mock.patch.object(app_module.urllib.request, "urlopen",
                               side_effect=AssertionError("network")):
            for entry in ("abc", "abcde", "../x1", "4z1!"):
                self.assertEqual(self.client.post("/api/fetch-pdb", data={"entry": entry}).status_code,
                                 400, entry)
            for accession in ("nope", "P1", "../../x"):
                self.assertEqual(self.client.post("/api/fetch-af", data={"uniprot": accession}).status_code,
                                 400, accession)


# ------------------------------------------------------- a review across a project
#
# One review pooled across the campaigns of a project. Everything here is at the level
# of the service: records, routes and the command the job queue would run. None of it
# starts a build; the scheduler's pass over queued reviews is switched off so that a
# queued row stays queued, and the build's own results are written by hand.

import json                                                    # noqa: E402

from bc2serve import review as review_module                   # noqa: E402
from bc2serve import storage as storage_module                 # noqa: E402
from bc2serve import tables as tables_module                   # noqa: E402
from starlette.routing import Match                            # noqa: E402

CONSTRUCTS = REPO / "settings"
(CONSTRUCTS / "pool_a.pdb").write_text(pdb_text(3))
(CONSTRUCTS / "pool_b.pdb").write_text(pdb_text(5))


class ProjectSlugTests(unittest.TestCase):
    def test_the_slug_is_the_one_the_design_names(self):
        import hashlib
        label = "Pooled / Spike round 2"
        expected = "@" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] + "-" \
                   + hashlib.sha1(label.encode()).hexdigest()[:6]
        self.assertEqual(review_module.project_slug(label), expected)
        self.assertEqual(expected.split("-")[0], "@pooled")

    def test_every_slug_is_a_valid_project_name_and_never_a_campaign_name(self):
        for label in ("x", "Pooled / Spike round 2", "???", "日本語", "-lead-", "a" * 64,
                      "../../etc", "A B", "with\nnewline", "x" * 40 + "-" + "y" * 23):
            slug = review_module.project_slug(label)
            self.assertRegex(slug, review_module.PROJECT_NAME.pattern, label)
            self.assertTrue(review_module.PROJECT_NAME.fullmatch(slug), label)
            self.assertIsNone(review_module.CAMPAIGN_NAME.fullmatch(slug), label)
            self.assertLessEqual(len(slug), 48)

    def test_a_label_with_nothing_to_slug_is_still_addressable(self):
        self.assertTrue(review_module.project_slug("???").startswith("@project-"))

    def test_labels_that_slug_alike_do_not_share_a_review(self):
        self.assertNotEqual(review_module.project_slug("Run 1"), review_module.project_slug("run-1"))

    def test_the_same_label_always_gets_the_same_slug(self):
        self.assertEqual(review_module.project_slug("A"), review_module.project_slug("A"))

    def test_unfiled_is_refused_and_says_so(self):
        for label in ("", "   ", None):
            with self.assertRaises(review_module.ReviewError) as caught:
                review_module.project_slug(label)
            self.assertIn("Unfiled", str(caught.exception))

    def test_names_that_are_not_project_names(self):
        for name in ("camp", "@", "@-x", "@A", "@a/b", "@a b", "../x", "@..", "@" + "a" * 49, "@a\n",
                     "_reviews", ""):
            self.assertIsNone(review_module.PROJECT_NAME.fullmatch(name), repr(name))
        for name in ("@a", "@a-1", "@" + "a" * 48):
            self.assertTrue(review_module.PROJECT_NAME.fullmatch(name), name)


class RunIdTests(unittest.TestCase):
    def test_the_campaigns_own_name_is_the_id(self):
        self.assertEqual(review_module.run_ids(["/r/denovo", "/r/vhh"]), ["denovo", "vhh"])

    def test_an_id_is_never_a_path_or_an_option(self):
        ids = review_module.run_ids(["/r/a b", "/r/-lead", "/r/x:y", "/r/.."])
        for run_id in ids:
            self.assertRegex(run_id, r"[A-Za-z0-9_-]+")
            self.assertFalse(run_id.startswith("-"))
        self.assertEqual(ids[0], "a_b")

    def test_ids_that_collide_are_numbered_in_the_order_given(self):
        self.assertEqual(review_module.run_ids(["/r1/x y", "/r2/x_y", "/r3/x y"]),
                         ["x_y", "x_y-2", "x_y-3"])

    def test_a_numbered_id_does_not_collide_with_a_real_name(self):
        ids = review_module.run_ids(["/r/x", "/r/x-2", "/r/x"])
        self.assertEqual(len(set(ids)), 3)

    def test_pooled_counts_sum_each_key(self):
        from bc2serve.tests.test_designs import make_campaign, refolded_row, ranked_row
        tables_module.clear_cache()
        with tempfile.TemporaryDirectory() as scratch:
            first = make_campaign(Path(scratch), "one", [refolded_row("a_seq0", "AAAA", "h1"),
                                                         refolded_row("a_seq1", "CCCC", "h2", outcome="rejected")],
                                  ranked=[ranked_row(1, "a_seq0", "AAAA", "h1")])
            second = make_campaign(Path(scratch), "two", [refolded_row("b_seq0", "GGGG", "h3")], ranked=[])
            total = review_module.pooled_counts([first, second])
        self.assertEqual((total["refolded"], total["passed"], total["ranked"]), (3, 2, 1))
        self.assertEqual(set(total), {"trajectories", "refolded", "passed", "ranked"})


class ProjectReviewTests(Routes):
    """The three routes, the listing, staleness, the bundle and the command."""

    LABEL = "Pooled / Spike round 2"

    def setUp(self) -> None:
        super().setUp()
        tables_module.clear_cache()
        # A queued review must stay queued: the scheduler would otherwise try to run it.
        patch = mock.patch.object(store, "_start_analyses", lambda: None)
        patch.start()
        self.addCleanup(patch.stop)
        self.slug = review_module.project_slug(self.LABEL)
        self.notional = RESULTS / self.slug
        self.home = RESULTS / "_reviews" / self.slug
        self.made: list[Path] = []
        self.addCleanup(self.tidy)
        self.a = self.member("pool-a", "pool_a.pdb", self.LABEL, rows=2)
        self.b = self.member("pool-b", "pool_a.pdb", self.LABEL, rows=3)

    def tidy(self) -> None:
        for folder in self.made:
            shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def member(self, name, construct, project, *, rows=1, state="done"):
        from bc2serve.tests.test_designs import make_campaign, refolded_row, ranked_row
        refolded = [refolded_row(f"{name}_seq{n}", "ACDE" + "G" * n, f"h-{name}-{n}") for n in range(rows)]
        ranked = [ranked_row(1, f"{name}_seq0", "ACDE", f"h-{name}-0")] if rows else []
        folder = make_campaign(RESULTS, name, refolded, ranked=ranked)
        (folder / "campaign_metadata.json").write_text(json.dumps({
            "version": "2.0", "settings": {"modality": "vhh", "targets": [
                {"name": "t", "target_path": str(CONSTRUCTS / construct), "chains": "A"}]}}))
        self.made.append(folder)
        return folder, add_job(name, folder, project=project, state=state)

    def url(self, tail=""):
        return f"/api/review/project/{self.slug}{tail}"

    def build(self, **form):
        form = form or {"no_reference": True}
        response = self.client.post(self.url(), json=form)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def finish(self):
        """What a successful build leaves: a bundle, and a record that says done."""
        (self.home / "bundle").mkdir(parents=True, exist_ok=True)
        (self.home / "bundle" / "index.html").write_text("<p>pooled</p>")
        (self.home / "bundle" / "app.js").write_text("// pooled js")
        (self.home / "secret.txt").write_text("SECRET-BESIDE-THE-BUNDLE")
        record = json.loads((self.home / "review.json").read_text())
        record.update(state="done", finished_at=time.time())
        (self.home / "review.json").write_text(json.dumps(record))

    # ---------------------------------------------------------------- describing

    def test_describe_names_the_members_the_construct_and_the_pooled_counts(self):
        body = self.client.get(self.url()).json()
        self.assertEqual((body["project"], body["slug"]), (self.LABEL, self.slug))
        self.assertIs(body["can_build"], True, body["why_not"])
        self.assertEqual([m["name"] for m in body["members"]], ["pool-a", "pool-b"])
        self.assertEqual(body["construct"]["path"], str(CONSTRUCTS / "pool_a.pdb"))
        self.assertEqual((body["designs"]["passed"], body["designs"]["ranked"]), (5, 2))
        self.assertEqual(body["project_folder"], str(self.notional))
        self.assertIsNone(body["review"])
        self.assertIs(body["reference"]["none"], False)
        self.assertIsInstance(body["suggestions"], list)

    def test_the_notional_folder_is_never_made(self):
        self.client.get(self.url())
        self.build()
        self.assertFalse(self.notional.exists(), "<root>/@slug must stay a name")
        self.assertTrue((self.home / "review.json").is_file())

    def test_members_that_disagree_about_the_construct_are_refused_by_name_and_file(self):
        self.member("pool-c", "pool_b.pdb", self.LABEL)
        body = self.client.get(self.url()).json()
        self.assertIs(body["can_build"], False)
        for needle in ("pool-a", "pool-b", "pool-c", "pool_a.pdb", "pool_b.pdb"):
            self.assertIn(needle, body["why_not"])
        response = self.client.post(self.url(), json={"no_reference": True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("pool_b.pdb", response.json()["detail"])
        self.assertFalse(self.home.exists(), "a refused build writes nothing")

    def test_fewer_than_two_buildable_members_is_refused_and_says_why(self):
        self.member("pool-empty", "pool_a.pdb", "Lonely", rows=0)
        self.member("pool-lone", "pool_a.pdb", "Lonely", rows=2)
        lonely = review_module.project_slug("Lonely")
        body = self.client.get(f"/api/review/project/{lonely}").json()
        self.assertIs(body["can_build"], False)
        self.assertIn("at least two", body["why_not"])
        self.assertIn("pool-empty", body["why_not"])
        self.assertEqual([m["buildable"] for m in body["members"]], [False, True])
        self.assertEqual(self.client.post(f"/api/review/project/{lonely}",
                                          json={"no_reference": True}).status_code, 400)

    def test_an_archived_member_is_not_one(self):
        store.set_archived([self.b[1]], True)
        body = self.client.get(self.url()).json()
        self.assertEqual([m["name"] for m in body["members"]], ["pool-a"])
        self.assertIs(body["can_build"], False)

    def test_an_unknown_project_is_a_404(self):
        self.assertEqual(self.client.get("/api/review/project/@nothing-here-abc123").status_code, 404)

    def test_the_slug_is_checked_before_it_touches_a_path(self):
        for slug in ("camp", "pool-a", "@A", "@" + "a" * 60, "_reviews", "@a.b", "@_x"):
            for method, tail in (("get", ""), ("post", ""), ("delete", ""), ("get", "/log")):
                kwargs = {"json": {"no_reference": True}} if method == "post" else {}
                response = getattr(self.client, method)(f"/api/review/project/{slug}{tail}", **kwargs)
                self.assertEqual(response.status_code, 404, (method, slug, tail))
        # An encoded separator or climb is decoded before routing and so never reaches a slug at all.
        for slug in ("@x%2fy", "@%2e%2e", "%40%2e%2e%2f%2e%2e"):
            self.assertEqual(self.client.get(f"/api/review/project/{slug}").status_code, 404, slug)

    # ---------------------------------------------------------------- starting

    def test_a_build_records_who_was_pooled_and_what_they_held(self):
        self.member("pool-c", "pool_a.pdb", "Other project", rows=4)       # another project: not a member
        answer = self.build()
        self.assertEqual((answer["state"], answer["reference"]), ("queued", "none"))
        record = json.loads((self.home / "review.json").read_text())
        self.assertEqual(record["inputs"]["members"], [
            {"id": "pool-a", "folder": str(self.a[0].resolve())},
            {"id": "pool-b", "folder": str(self.b[0].resolve())}])
        self.assertEqual(record["inputs"]["construct"], str(CONSTRUCTS / "pool_a.pdb"))
        self.assertEqual(record["project"], self.LABEL)
        self.assertEqual((record["counts"]["passed"], record["counts"]["ranked"]), (5, 2))
        row = store.get(answer["id"])
        self.assertEqual((row["kind"], row["project"], row["gpus_wanted"]), ("analysis", self.LABEL, 0))
        self.assertEqual(Path(row["project_folder"]), self.notional)

    def test_a_build_needs_a_reference_choice_just_as_a_campaign_does(self):
        response = self.client.post(self.url(), json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("full-length reference", response.json()["detail"])
        both = self.client.post(self.url(), json={"no_reference": True, "reference_path": str(CONSTRUCTS / "pool_b.pdb")})
        self.assertEqual(both.status_code, 400)
        self.assertFalse(self.home.exists())

    def test_the_construct_cannot_be_its_own_reference(self):
        response = self.client.post(self.url(), json={"reference_path": str(CONSTRUCTS / "pool_a.pdb")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("construct itself", response.json()["detail"])

    def test_a_reference_outside_the_roots_is_refused_by_the_route(self):
        response = self.client.post(self.url(), json={"reference_path": str(OUTSIDE / "secret.pdb")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not somewhere this service reads", response.json()["detail"])

    def test_a_second_build_replaces_the_first(self):
        first = self.build()["id"]
        (self.home / "leftover.txt").write_text("from the first build")
        second = self.build()["id"]
        self.assertNotEqual(first, second)
        self.assertFalse((self.home / "leftover.txt").exists())
        self.assertEqual(store.get(first)["state"], "cancelled")

    def test_a_campaign_review_and_a_project_review_do_not_touch(self):
        folder = self.a[0]
        self.assertEqual(self.client.post(f"/api/review/{folder.name}", json={"no_reference": True}).status_code, 200)
        self.build()
        self.assertTrue((RESULTS / "_reviews" / folder.name / "review.json").is_file())
        self.assertTrue((self.home / "review.json").is_file())
        self.assertEqual(self.client.delete(self.url()).status_code, 200)
        self.assertTrue((RESULTS / "_reviews" / folder.name / "review.json").is_file(),
                        "discarding the project's review must leave a campaign's alone")
        self.assertFalse(self.home.exists())
        self.client.delete(f"/api/review/{folder.name}")

    # ---------------------------------------------------------------- the command

    def test_a_review_takes_every_core_but_one_unless_told_otherwise(self):
        answer = self.build()
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("BC2SERVE_REVIEW_JOBS", None)
            command, _ = review_module.launch_spec(REPO, store.get(answer["id"]))
        self.assertNotIn("--jobs", command)
        with mock.patch.dict(os.environ, {"BC2SERVE_REVIEW_JOBS": "2"}):
            command, _ = review_module.launch_spec(REPO, store.get(answer["id"]))
        self.assertEqual(command[command.index("--jobs") + 1], "2")
        with mock.patch.dict(os.environ, {"BC2SERVE_REVIEW_JOBS": "lots"}):
            command, _ = review_module.launch_spec(REPO, store.get(answer["id"]))
        self.assertNotIn("--jobs", command)

    def test_the_command_reads_each_member_as_id_equals_path_and_never_the_notional_folder(self):
        answer = self.build()
        command, log = review_module.launch_spec(REPO, store.get(answer["id"]))
        self.assertEqual(log, self.home / "build.log")
        words = command[command.index("import") + 1:]
        self.assertEqual(words[:2], [f"pool-a={self.a[0].resolve()}", f"pool-b={self.b[0].resolve()}"])
        self.assertFalse([w for w in command if str(self.notional) in w],
                         "the project's notional folder must never be handed to the build as a place to read")
        self.assertEqual(words[words.index("--target-pdb") + 1], str(CONSTRUCTS / "pool_a.pdb"))
        # The bundle's title is the label a person typed, never the slug (see launch_spec).
        self.assertEqual(words[words.index("--name") + 1], self.LABEL)
        self.assertEqual(words[words.index("--out") + 1], str(self.home / "project"))
        self.assertEqual(words[words.index("--bundle") + 1], str(self.home / "bundle"))
        self.assertNotIn("--reference-cif", words)
        self.assertEqual(sum(1 for w in words if w == "--target-pdb"), 1)

    def test_the_other_flags_are_exactly_as_a_campaign_gets_them(self):
        self.build()
        record = json.loads((self.home / "review.json").read_text())
        record["inputs"].update(reference=str(CONSTRUCTS / "pool_b.pdb"), no_reference=False, reference_chain="A",
                                partner_chains="auto", placement="whole", uniprot="P69905", online=True)
        (self.home / "review.json").write_text(json.dumps(record))
        command, _ = review_module.launch_spec(REPO, {"project_folder": str(self.notional), "id": 1})
        words = command[command.index("import") + 1:]
        for flag, value in (("--reference-cif", str(CONSTRUCTS / "pool_b.pdb")), ("--placement", "whole"),
                            ("--reference-chain", "A"), ("--partner-chains", "auto"), ("--uniprot", "P69905")):
            self.assertEqual(words[words.index(flag) + 1], value, flag)
        self.assertIn("--online", words)

    def test_a_campaign_review_still_gets_its_one_positional_run_folder(self):
        folder = self.a[0]
        self.assertEqual(self.client.post(f"/api/review/{folder.name}", json={"no_reference": True}).status_code, 200)
        self.addCleanup(self.client.delete, f"/api/review/{folder.name}")
        command, _ = review_module.launch_spec(REPO, {"project_folder": str(folder), "id": 1})
        words = command[command.index("import") + 1:]
        self.assertEqual(words[0], str(folder))
        self.assertEqual(words[1], "--target-pdb")

    def test_starting_a_build_records_the_pooled_counts_and_not_zeros(self):
        answer = self.build()
        review_module.on_start(store.get(answer["id"]))
        record = json.loads((self.home / "review.json").read_text())
        self.assertEqual((record["state"], record["counts"]["passed"]), ("running", 5))

    # ---------------------------------------------------------------- staleness

    def built(self):
        self.build()
        self.finish()
        return self.client.get(self.url()).json()["review"]

    def test_a_finished_review_is_fresh_and_links_to_its_own_address(self):
        review = self.built()
        self.assertEqual((review["state"], review["stale"]), ("done", False))
        self.assertEqual(review["bundle_url"], f"/review/{self.slug}/")

    def test_a_design_arriving_in_any_member_makes_it_stale(self):
        self.built()
        from bc2serve.tests.test_designs import refolded_row
        table = self.b[0] / "2_Refolded" / "!_Refolded.csv"
        import csv as csv_module
        with open(table, "a", newline="") as handle:
            csv_module.writer(handle).writerow(refolded_row("pool-b_new", "WWWW", "h-new"))
        tables_module.clear_cache()
        review = self.client.get(self.url()).json()["review"]
        self.assertEqual((review["stale"], review["stale_reason"]), (True, "designs"))

    def test_a_member_added_reads_as_stale(self):
        self.built()
        self.member("pool-c", "pool_a.pdb", self.LABEL, rows=1)
        review = self.client.get(self.url()).json()["review"]
        self.assertEqual((review["stale"], review["stale_reason"]), (True, "designs"))

    def test_a_member_removed_reads_as_stale(self):
        self.built()
        self.member("pool-c", "pool_a.pdb", self.LABEL, rows=1)
        self.build()
        self.finish()
        self.assertFalse(self.client.get(self.url()).json()["review"]["stale"])
        store.set_project([self.b[1]], "Somewhere else")
        # Two members are left, so the project can still be described, and it has changed.
        review = self.client.get(self.url()).json()["review"]
        self.assertEqual((review["stale"], review["stale_reason"]), (True, "designs"))

    def test_a_member_swapped_for_one_with_the_same_numbers_is_still_stale(self):
        self.built()
        store.set_project([self.b[1]], "Somewhere else")
        self.member("pool-b2", "pool_a.pdb", self.LABEL, rows=3)
        body = self.client.get(self.url()).json()
        self.assertEqual(body["designs"], json.loads((self.home / "review.json").read_text())["counts"])
        self.assertIs(body["review"]["stale"], True)

    def fake_viewer(self):
        """Point viewer_dir() at a throwaway viewer and return it.

        A real viewer is not edited to test this: the fingerprint follows
        BINDERSCOPE_VIEWER, so a folder with the two things viewer_dir() insists on
        is enough, and the test never writes into the checkout it runs from.
        """
        holder = tempfile.TemporaryDirectory(prefix="bc2serve-viewer-")
        self.addCleanup(holder.cleanup)
        vdir = Path(os.path.realpath(holder.name))
        (vdir / "pages").mkdir()
        (vdir / "js").mkdir()
        (vdir / "js" / "bs.js").write_text("// shared code\n")
        (vdir / "pages" / "query.html").write_text("<h2>Pick residues on the target</h2>\n")
        patcher = mock.patch.dict(os.environ, {"BINDERSCOPE_VIEWER": str(vdir)})
        patcher.start()
        self.addCleanup(patcher.stop)
        # Nothing clears the memo on the way in or out: it is keyed on the file list
        # with sizes and mtimes, so a changed viewer is a changed key. A test that
        # cleared it by hand would pass even if that stopped being true.
        self.addCleanup(review_module._hash_cache.clear)
        return vdir

    def test_a_page_edited_makes_the_review_stale(self):
        """A bundle is a copy of the pages, so a changed page is a changed review.

        Nothing about a built review moves when a template does -- the counts, the
        members and the designs are all as they were -- which is exactly why this
        has to be the code fingerprint's job and not the data's.
        """
        vdir = self.fake_viewer()
        self.built()
        self.assertFalse(self.client.get(self.url()).json()["review"]["stale"])
        (vdir / "pages" / "query.html").write_text("<h2>Pick residues on the target</h2>\n<p>and more</p>\n")
        review = self.client.get(self.url()).json()["review"]
        self.assertEqual((review["stale"], review["stale_reason"]), (True, "code"))

    def test_a_file_added_to_the_viewer_makes_the_review_stale(self):
        """Any file, not only the ones with suffixes this code knows about."""
        vdir = self.fake_viewer()
        self.built()
        self.assertFalse(self.client.get(self.url()).json()["review"]["stale"])
        (vdir / "assets").mkdir()
        (vdir / "assets" / "binderscope-mark.svg").write_text("<svg/>")
        review = self.client.get(self.url()).json()["review"]
        self.assertEqual((review["stale"], review["stale_reason"]), (True, "code"))

    def test_the_listing_reports_the_same_staleness(self):
        self.built()
        (entry,) = [p for p in self.client.get("/api/review/projects").json() if p["slug"] == self.slug]
        self.assertFalse(entry["review"]["stale"])
        self.member("pool-c", "pool_a.pdb", self.LABEL, rows=1)
        (entry,) = [p for p in self.client.get("/api/review/projects").json() if p["slug"] == self.slug]
        self.assertTrue(entry["review"]["stale"])

    # ---------------------------------------------------------------- the listing

    def test_the_listing_offers_projects_with_two_or_more_campaigns(self):
        self.member("lone", "pool_a.pdb", "Alone", rows=2)
        self.member("unfiled-1", "pool_a.pdb", "", rows=2)
        self.member("unfiled-2", "pool_a.pdb", "", rows=2)
        response = self.client.get("/api/review/projects")
        self.assertEqual(response.status_code, 200, response.text)
        listed = response.json()
        self.assertEqual([p["project"] for p in listed], [self.LABEL])
        entry = listed[0]
        self.assertEqual((entry["slug"], entry["members"], entry["buildable"], entry["can_build"]),
                         (self.slug, 2, 2, True))
        self.assertEqual(entry["designs"]["passed"], 5)
        self.assertIsNone(entry["review"])

    def test_the_listing_shows_a_project_it_cannot_build_and_why(self):
        self.member("pool-c", "pool_b.pdb", self.LABEL)
        (entry,) = self.client.get("/api/review/projects").json()
        self.assertEqual((entry["members"], entry["can_build"]), (3, False))
        self.assertIn("pool_b.pdb", entry["why_not"])

    def test_the_listing_shows_the_review_state(self):
        self.build()
        (entry,) = self.client.get("/api/review/projects").json()
        self.assertEqual(entry["review"]["state"], "queued")

    # ---------------------------------------------------------------- asking about a reference

    # `/reference` and `/chains` take a campaign's name or a project's slug in the same slot.
    # For a project the construct is the one every buildable member names, so asking by slug
    # must give what asking a member gives, and must refuse when there is no such one.

    ASKED = (("reference", {}), ("chains", {"partners": "auto"}))

    def ask(self, name, route, extra):
        return self.client.get(f"/api/review/{name}/{route}",
                               params={"path": str(CONSTRUCTS / "pool_a.pdb"), **extra})

    def test_a_project_slug_answers_the_reference_questions_as_a_member_does(self):
        for route, extra in self.ASKED:
            with self.subTest(route=route):
                member = self.ask("pool-a", route, extra)
                project = self.ask(self.slug, route, extra)
                self.assertEqual(member.status_code, 200, member.text)
                self.assertEqual(project.status_code, 200, project.text)
                # `checked_at` is the moment of asking, so it is the one field that differs.
                answers = [r.json() for r in (member, project, self.ask("pool-b", route, extra))]
                for answer in answers:
                    answer.pop("checked_at", None)
                self.assertEqual(answers[1], answers[0])
                self.assertEqual(answers[2], answers[0])

    def test_members_that_disagree_leave_the_reference_questions_unanswerable_by_name(self):
        self.member("pool-c", "pool_b.pdb", self.LABEL)
        refusal = self.client.get(self.url()).json()["why_not"]
        for route, extra in self.ASKED:
            with self.subTest(route=route):
                response = self.ask(self.slug, route, extra)
                self.assertEqual(response.status_code, 400, response.text)
                said = response.json()["detail"]
                for needle in ("pool-a", "pool-b", "pool-c", "pool_a.pdb", "pool_b.pdb"):
                    self.assertIn(needle, said)
                self.assertEqual(said, refusal, "one phrasing of the refusal, not two")
                self.assertEqual(self.ask("pool-a", route, extra).status_code, 200,
                                 "a member campaign is still answered on its own construct")

    def test_a_project_with_too_few_buildable_members_is_refused_for_the_reference_questions(self):
        self.member("pool-empty", "pool_a.pdb", "Lonely", rows=0)
        self.member("pool-lone", "pool_a.pdb", "Lonely", rows=2)
        lonely = review_module.project_slug("Lonely")
        for route, extra in self.ASKED:
            with self.subTest(route=route):
                response = self.ask(lonely, route, extra)
                self.assertEqual(response.status_code, 400)
                self.assertIn("at least two", response.json()["detail"])

    def test_an_unknown_slug_is_a_404_for_the_reference_questions(self):
        for route, extra in self.ASKED:
            self.assertEqual(self.ask("@nothing-here-abc123", route, extra).status_code, 404, route)
            self.assertEqual(self.ask("@A", route, extra).status_code, 404, route)

    def test_a_campaign_whose_construct_is_gone_keeps_its_own_refusal(self):
        folder, _ = self.a
        (folder / "campaign_metadata.json").write_text(json.dumps({"version": "2.0", "settings": {"targets": [
            {"name": "t", "target_path": str(CONSTRUCTS / "gone.pdb"), "chains": "A"}]}}))
        for route, extra in self.ASKED:
            response = self.ask("pool-a", route, extra)
            self.assertEqual(response.status_code, 400)
            self.assertIn("construct file is missing", response.json()["detail"])

    # ---------------------------------------------------------------- the pages

    def test_the_bundle_is_served_only_when_done_and_a_climb_out_of_it_is_not(self):
        self.assertEqual(self.client.get(f"/review/{self.slug}/").status_code, 404)
        self.build()
        self.assertEqual(self.client.get(f"/review/{self.slug}/index.html").status_code, 404)
        self.finish()
        self.assertEqual(self.client.get(f"/review/{self.slug}/").text, "<p>pooled</p>")
        self.assertEqual(self.client.get(f"/review/{self.slug}/app.js").text, "// pooled js")
        for climb in ("%2e%2e/secret.txt", "..%2fsecret.txt", "%2e%2e/%2e%2e/review.json"):
            response = self.client.get(f"/review/{self.slug}/{climb}")
            self.assertEqual(response.status_code, 404, climb)
            self.assertNotIn("SECRET", response.text)

    def test_the_address_without_a_slash_redirects_to_the_one_with(self):
        response = self.client.get(f"/review/{self.slug}", follow_redirects=False)
        self.assertIn(response.status_code, (302, 307))
        self.assertEqual(response.headers["location"], f"/review/{self.slug}/")

    def test_what_is_not_a_project_name_is_not_served(self):
        for name in ("@A", "@", "@-x", "@x%2fy", "@%2e%2e"):
            self.assertEqual(self.client.get(f"/review/{name}/index.html").status_code, 404, name)

    def test_a_campaign_is_not_served_as_a_project_nor_the_reverse(self):
        folder = self.a[0]
        self.client.post(f"/api/review/{folder.name}", json={"no_reference": True})
        self.addCleanup(self.client.delete, f"/api/review/{folder.name}")
        home = RESULTS / "_reviews" / folder.name
        (home / "bundle").mkdir(parents=True, exist_ok=True)
        (home / "bundle" / "index.html").write_text("<p>campaign</p>")
        record = json.loads((home / "review.json").read_text())
        record["state"] = "done"
        (home / "review.json").write_text(json.dumps(record))
        self.build()
        self.finish()
        self.assertEqual(self.client.get(f"/review/{folder.name}/").text, "<p>campaign</p>")
        self.assertEqual(self.client.get(f"/review/{self.slug}/").text, "<p>pooled</p>")
        self.assertEqual(self.client.get(f"/review/@{folder.name}/").status_code, 404)

    def test_the_log_is_reachable(self):
        self.build()
        (self.home / "build.log").write_text("[   1.0s] read runs ...\n")
        body = self.client.get(self.url("/log")).json()
        self.assertEqual(body["state"], "queued")
        self.assertIn("read runs", body["text"])

    # ---------------------------------------------------------------- discarding

    def test_discarding_removes_the_review_and_nothing_else(self):
        self.build()
        self.finish()
        self.assertEqual(self.client.delete(self.url()).json(), {"ok": True})
        self.assertFalse(self.home.exists())
        for folder, _ in (self.a, self.b):
            self.assertTrue((folder / "campaign.json").is_file() or (folder / "campaign_metadata.json").is_file())
        self.assertEqual(self.client.get(self.url()).json()["review"], None)

    def test_a_review_can_be_discarded_after_every_member_is_gone(self):
        self.build()
        for _, job in (self.a, self.b):
            store.set_project([job], "Moved away")
        self.assertEqual(self.client.delete(self.url()).status_code, 200)
        self.assertFalse(self.home.exists())

    def test_discarding_a_project_that_never_existed_is_a_404(self):
        self.assertEqual(self.client.delete("/api/review/project/@never-0abcde").status_code, 404)

    def test_unfiled_cannot_be_pooled(self):
        self.assertEqual(self.client.get("/api/review/project/%40").status_code, 404)
        result = app_module.reviews.describe_project("")
        self.assertIs(result["can_build"], False)
        self.assertIn("Unfiled", result["why_not"])


class ProjectRoutePrecedenceTests(unittest.TestCase):
    """Starlette takes the first route that matches, so the order routes were added in is the behaviour."""

    @staticmethod
    def endpoint(method, path):
        scope = {"type": "http", "method": method, "path": path, "root_path": "", "headers": []}
        for route in app_module.app.routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                return getattr(route, "name", None)
        return None

    def test_the_listing_is_not_taken_for_a_campaign_called_projects(self):
        self.assertEqual(self.endpoint("GET", "/api/review/projects"), "api_review_projects")

    def test_the_project_routes_are_not_swallowed_by_the_campaign_ones(self):
        slug = "/api/review/project/@pooled-abc123"
        self.assertEqual(self.endpoint("GET", slug), "api_review_project")
        self.assertEqual(self.endpoint("POST", slug), "api_review_project_start")
        self.assertEqual(self.endpoint("DELETE", slug), "api_review_project_delete")
        self.assertEqual(self.endpoint("GET", slug + "/log"), "api_review_project_log")

    def test_the_campaign_routes_are_still_the_campaign_routes(self):
        self.assertEqual(self.endpoint("GET", "/api/review"), "api_review_list")
        self.assertEqual(self.endpoint("GET", "/api/review/camp"), "api_review_form")
        self.assertEqual(self.endpoint("POST", "/api/review/camp"), "api_review_start")
        self.assertEqual(self.endpoint("DELETE", "/api/review/camp"), "api_review_delete")
        self.assertEqual(self.endpoint("GET", "/api/review/camp/log"), "api_review_log")

    def test_a_campaign_named_project_keeps_its_own_second_segment_routes(self):
        for tail, name in (("log", "api_review_log"), ("reference", "api_review_reference"),
                           ("chains", "api_review_chains")):
            self.assertEqual(self.endpoint("GET", f"/api/review/project/{tail}"), name)
        self.assertEqual(self.endpoint("GET", "/api/review/project"), "api_review_form")

    def test_the_static_route_takes_a_slug_as_it_takes_a_name(self):
        self.assertEqual(self.endpoint("GET", "/review/@pooled-abc123/index.html"), "review_file")
        self.assertEqual(self.endpoint("GET", "/review/@pooled-abc123"), "review_slash")


class ProjectStorageTests(Routes):
    """A project review has no campaign folder, and must not be offered for deletion for that."""

    def test_the_rule_by_itself(self):
        names, present = ["camp", "gone", "@p-111111", "@q-222222", "@r-333333"], ["camp"]
        self.assertEqual(storage_module.orphaned_reviews(names, present), ["gone"])
        self.assertEqual(storage_module.orphaned_reviews(names, present, {"@p-111111": 2, "@q-222222": 0}),
                         ["@q-222222", "gone"])

    def test_the_orphan_label_for_a_project_does_not_blame_a_campaign(self):
        item = storage_module.classify_orphan_review("@p-111111", 10, "/r/_reviews/@p-111111")
        self.assertIn("every campaign", item["cost"])
        self.assertNotIn("Its campaign folder", item["reason"])

    def reviews_listed(self):
        found = app_module._storage_targets()["reviews"]
        return {r["name"]: r for r in found}

    def plant(self, name, members):
        home = RESULTS / "_reviews" / name
        home.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, home, True)
        (home / "review.json").write_text(json.dumps(
            {"state": "done", "inputs": {"members": members}} if members is not None else {"state": "done"}))

    def test_a_project_review_with_a_member_left_is_not_an_orphan(self):
        alive = RESULTS / "storage-alive"
        alive.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, alive, True)
        self.plant("@kept-aaaaaa", [{"id": "x", "folder": str(alive)}, {"id": "y", "folder": str(RESULTS / "nope")}])
        self.assertIs(self.reviews_listed()["@kept-aaaaaa"]["orphan"], False)

    def test_a_project_review_whose_members_are_all_gone_is_an_orphan(self):
        self.plant("@lost-bbbbbb", [{"id": "x", "folder": str(RESULTS / "nope-1")},
                                    {"id": "y", "folder": str(RESULTS / "nope-2")}])
        self.assertIs(self.reviews_listed()["@lost-bbbbbb"]["orphan"], True)

    def test_a_project_review_whose_record_cannot_say_is_not_reported(self):
        self.plant("@vague-cccccc", None)
        self.assertIs(self.reviews_listed()["@vague-cccccc"]["orphan"], False)

    def test_a_campaign_review_with_no_campaign_is_still_an_orphan(self):
        self.plant("ghost-camp", None)
        self.assertIs(self.reviews_listed()["ghost-camp"]["orphan"], True)


if __name__ == "__main__":
    unittest.main()


class ClusterSettingsTests(unittest.TestCase):
    """A cluster job runs with two paths only its site knows, so neither has a default."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="bc2serve-sge-")
        self.addCleanup(holder.cleanup)
        self.tmp = Path(os.path.realpath(holder.name))
        patcher = mock.patch.dict(os.environ, {})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("BC2SERVE_SGE_SIF", "BC2SERVE_SGE_PARAMS"):
            os.environ.pop(key, None)

    def test_nothing_set_names_both_settings_and_the_installer(self):
        why = jobs_module.cluster_settings()[2]
        self.assertIn("BC2SERVE_SGE_SIF and BC2SERVE_SGE_PARAMS", why)
        self.assertIn("./install.sh --sge", why)

    def test_an_image_that_is_not_there_is_named(self):
        (self.tmp / "params").mkdir()
        os.environ.update(BC2SERVE_SGE_SIF=str(self.tmp / "missing.sif"), BC2SERVE_SGE_PARAMS=str(self.tmp / "params"))
        self.assertIn("no BindCraft 2 image at", jobs_module.cluster_settings()[2])

    def test_a_parameters_folder_that_is_not_there_is_named(self):
        (self.tmp / "bc2.sif").write_bytes(b"")
        os.environ.update(BC2SERVE_SGE_SIF=str(self.tmp / "bc2.sif"), BC2SERVE_SGE_PARAMS=str(self.tmp / "nowhere"))
        self.assertIn("no AlphaFold parameters folder at", jobs_module.cluster_settings()[2])

    def test_both_present_is_fine(self):
        (self.tmp / "bc2.sif").write_bytes(b"")
        (self.tmp / "params").mkdir()
        os.environ.update(BC2SERVE_SGE_SIF=str(self.tmp / "bc2.sif"), BC2SERVE_SGE_PARAMS=str(self.tmp / "params"))
        self.assertEqual(jobs_module.cluster_settings(), (str(self.tmp / "bc2.sif"), str(self.tmp / "params"), None))

    def test_a_campaign_is_refused_before_anything_reaches_the_scheduler(self):
        row = {"project_folder": str(self.tmp / "project"), "id": 7, "campaign_path": str(self.tmp / "c.json"),
               "queue": "", "gpu_type": "", "gpus_wanted": 1}
        with mock.patch.object(jobs_module.sge, "submit") as submit:
            job_id, why = store._submit_to_cluster(row)
        self.assertIsNone(job_id)
        self.assertIn("BC2SERVE_SGE_SIF", why)
        submit.assert_not_called()
