"""The parts of the Binderscope app that need no server: the project store, the build jobs and their messages, the native dialogs (with the
system calls replaced), the command line entry and the saved decisions.

Nothing here starts a build of the toy runs or opens a window; the few processes are one-line Python programs."""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from binder_review import cli
from binder_review.app import APP_NAME, BUILD_REVISION, DEFAULT_PORT, dialogs, jobs, server, store


# ---------------------------------------------------------------------------------------------------------------- store: names and folders
def test_home_folder_comes_from_the_environment_or_the_users_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BINDERSCOPE_HOME", str(tmp_path / "chosen"))
    assert store.home_dir() == (tmp_path / "chosen").resolve() and (tmp_path / "chosen").is_dir()
    (tmp_path / "link").symlink_to(tmp_path / "chosen", target_is_directory=True)
    monkeypatch.setenv("BINDERSCOPE_HOME", str(tmp_path / "link"))
    assert store.home_dir() == (tmp_path / "chosen").resolve()                                        # real paths: the app compares them
    monkeypatch.delenv("BINDERSCOPE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "me"))
    (tmp_path / "me").mkdir()
    assert store.home_dir() == (tmp_path / "me" / "Binderscope").resolve() and (tmp_path / "me" / "Binderscope").is_dir()
    assert (APP_NAME, DEFAULT_PORT) == ("binderscope", 51731)


@pytest.mark.parametrize("name, slug", [("Toy demo", "toy-demo"), ("  My Project 2  ", "my-project-2"), ("v1.2 final", "v1.2-final"), ("A/B\\C", "a-b-c"),
                                        ("", "project"), ("***", "project"), ("\u00dcn\u00ef", "n"), ("x" * 100, "x" * 60), ("--a--", "a"), ("a_b", "a_b"),
                                        ("Round 1..3", "round-1-3"), ("a..b", "a-b"), ("v1...x", "v1-x"), ("..", "project"), ("1..2..3", "1-2-3"), ("a. b", "a.-b"),
                                        ("a--b", "a-b"), ("a-..-b", "a-b"), (".x.", "x")])
def test_slugify(name, slug):
    assert store.slugify(name) == slug


@pytest.mark.parametrize("slug, ok", [("toy-demo", True), ("a", True), ("v1.2", True), ("a_b", True), ("0x", True), ("x" * 60, True), ("x" * 61, False), ("", False),
                                      ("Up", False), (".hidden", False), ("-x", False), ("_x", False), ("a b", False), ("a/b", False), ("..", False),
                                      ("a..b", False), (None, False)])
def test_valid_slug(slug, ok):
    assert store.valid_slug(slug) is ok


def test_every_name_makes_a_slug_the_app_accepts():
    for name in ("My Project", "x" * 100, "\u00fc", "A/B", " . ", "-.-", "a.-.b", "a. b", "1.2.3", "...", ".x.", "a--b", "tricky name!", "Round 1..3", "a..b", "v1...x", "..", "a\x00b"):
        slug = store.slugify(name)
        assert store.valid_slug(slug) and ".." not in slug, name


def test_slugs_of_any_text_are_valid():
    """Names are free text: whatever is typed, the slug is a name the app accepts (a run of dots once made one that it did not)."""
    rng = random.Random(7)
    pieces = ["a", "B", "1", ".", "-", "_", " ", "/", "\\", "\u00e9", "!", "\x00", "..", "--"]
    for _ in range(5000):
        name = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 14)))
        slug = store.slugify(name)
        assert store.valid_slug(slug) and ".." not in slug and slug == slug.lower(), repr(name)


def test_unique_slugs_count_up(tmp_path):
    assert store.unique_slug(tmp_path, "Demo") == "demo"
    (tmp_path / "demo").mkdir()
    assert store.unique_slug(tmp_path, "Demo") == "demo-2"
    (tmp_path / "demo-2").mkdir()
    (tmp_path / "demo-3").write_text("a file also takes the name")
    assert store.unique_slug(tmp_path, "demo") == "demo-4"


def test_create_writes_the_project_record_atomically(tmp_path):
    slug, d = store.create(tmp_path, " Toy ", {"runs": []})
    assert (slug, d) == ("toy", tmp_path / "toy") and store.project_dir(tmp_path, "toy") == d
    meta = store.read_meta(d)
    assert meta["schema"] == store.SCHEMA == "binderscope.app.project/1" and meta["name"] == "Toy" and meta["build"] == {"status": "new"} and meta["inputs"] == {"runs": []}
    meta["build"] = {"status": "ready"}
    store.write_meta(d, meta)
    assert store.read_meta(d)["build"]["status"] == "ready" and not (d / (store.META + ".tmp")).exists() and meta["updated"]
    assert store.create(tmp_path, "Toy", {})[0] == "toy-2"


def test_timestamps_are_iso_with_a_colon_in_the_offset(monkeypatch):
    stamp = store.now()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", stamp), stamp                      # 2026-10-02T04:26:18-05:00, which every browser parses
    parsed = datetime.fromisoformat(stamp)
    assert parsed.utcoffset() == datetime.now().astimezone().utcoffset() and abs((datetime.now().astimezone() - parsed).total_seconds()) < 5
    if hasattr(time, "tzset"):
        for tz in ("UTC", "Asia/Kolkata", "America/St_Johns"):                                       # whole hours or not, east or west
            monkeypatch.setenv("TZ", tz)
            time.tzset()
            assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", store.now()), tz
        monkeypatch.undo()
        time.tzset()


def test_parallel_writes_of_the_project_record_never_collide(tmp_path):
    slug, d = store.create(tmp_path, "p", {"runs": []})
    errors: list[Exception] = []

    def worker(k: int) -> None:
        for i in range(25):
            try:
                meta = store.read_meta(d)
                meta["build"] = {"status": "building", "k": k, "i": i}
                store.write_meta(d, meta)
            except Exception as ex:                      # noqa: BLE001
                errors.append(ex)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and store.read_meta(d)["build"]["status"] == "building" and not list(d.glob("*.tmp"))


def test_a_project_is_a_folder_with_its_record(tmp_path):
    (tmp_path / "bare").mkdir()
    for slug in ("bare", "missing", "..", "Up", "a b", "", ".hidden"):
        with pytest.raises(KeyError):
            store.project_dir(tmp_path, slug)
    store.create(tmp_path, "real", {})
    assert store.project_dir(tmp_path, "real") == tmp_path / "real"


def test_the_list_shows_projects_newest_first_and_skips_what_is_not_one(tmp_path):
    for i, name in enumerate(("old", "mid", "new")):
        slug, d = store.create(tmp_path, name, {"runs": [{"id": f"run{i}"}]})
        meta = store.read_meta(d)
        meta["updated"] = f"2026-01-0{i + 1}T00:00:00+0000"
        (d / store.META).write_text(json.dumps(meta), encoding="utf-8")          # a fixed time, not write_meta's now
    (tmp_path / "plain-folder").mkdir()
    (tmp_path / "file.txt").write_text("x")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / store.META).write_text("{not json", encoding="utf-8")
    (tmp_path / "_uploads").mkdir()
    listed = store.list_projects(tmp_path, "0.1.0")
    assert [p["slug"] for p in listed] == ["new", "mid", "old"] and listed[0]["runs"] == ["run2"]
    assert store.list_projects(tmp_path / "nowhere", "0.1.0") == []


def test_summary_marks_missing_pages_and_outdated_builds(tmp_path):
    slug, d = store.create(tmp_path, "S", {"runs": [{"id": "run01"}]})
    meta = store.read_meta(d)
    meta["build"] = {"status": "ready", "version": "0.0.9", "revision": BUILD_REVISION, "counts": {"accepted": 3}, "highlights": [{"text": str(i)} for i in range(5)],
                     "error": None, "seconds": 4.2}
    store.write_meta(d, meta)
    s = store.summary(d, "0.1.0")
    assert s["status"] == "missing" and s["ready"] is False and s["outdated"] is False and s["rebuild_needed"] is False                    # built, but the pages are gone
    assert [h["text"] for h in s["highlights"]] == ["0", "1", "2"] and s["counts"] == {"accepted": 3} and s["seconds"] == 4.2 and s["folder"] == str(d)
    (d / "bundle").mkdir()
    (d / "bundle" / "index.html").write_text("<html></html>")
    s = store.summary(d, "0.1.0")
    assert s["status"] == "ready" and s["ready"] is True and s["outdated"] is True and s["rebuild_needed"] is False                       # another program version: only "older"
    assert store.summary(d, "0.0.9")["outdated"] is False
    meta["build"] = {"status": "building"}
    store.write_meta(d, meta)
    assert store.summary(d, "0.1.0")["status"] == "building" and store.summary(d, "0.1.0")["outdated"] is False


def test_a_project_built_by_an_older_analysis_is_marked_for_a_rebuild(tmp_path):
    """BUILD_REVISION is raised when an analysis change makes older projects worth rebuilding (revision 2: designs are placed by a fit around the interface).
    A build that says nothing about its revision is revision 1."""
    assert isinstance(BUILD_REVISION, int) and BUILD_REVISION >= 2
    slug, d = store.create(tmp_path, "S", {"runs": [{"id": "run01"}]})
    (d / "bundle").mkdir()
    (d / "bundle" / "index.html").write_text("<html></html>")
    meta = store.read_meta(d)
    seen = {}
    for label, revision in (("none", None), ("old", BUILD_REVISION - 1), ("current", BUILD_REVISION), ("newer", BUILD_REVISION + 1)):
        meta["build"] = {"status": "ready", "version": "0.1.0", "counts": {"accepted": 3}, **({} if revision is None else {"revision": revision})}
        store.write_meta(d, meta)
        s = store.summary(d, "0.1.0")                                                                  # the same program version: only the revision differs
        seen[label] = (s["outdated"], s["rebuild_needed"])
    assert seen == {"none": (True, True), "old": (True, True), "current": (False, False), "newer": (False, False)}
    meta["build"] = {"status": "ready", "version": "0.1.0", "revision": 1}                              # a project without pages has nothing to rebuild for the page
    store.write_meta(d, meta)
    (d / "bundle" / "index.html").unlink()
    s = store.summary(d, "0.1.0")
    assert s["status"] == "missing" and s["rebuild_needed"] is False and s["outdated"] is False
    (d / "bundle" / "index.html").write_text("<html></html>")                                         # the pages of the older build are still there
    for status in ("building", "failed", "cancelled", "new"):                                           # only a project that is ready is outdated: not while it is rebuilt, not after a failure
        meta["build"] = {"status": status, **({"error": "x"} if status == "failed" else {})}              # (a build that starts drops what the earlier one recorded, the revision too)
        store.write_meta(d, meta)
        s = store.summary(d, "0.1.0")
        assert (s["status"], s["ready"], s["outdated"], s["rebuild_needed"]) == (status, True, False, False), status


def test_delete_removes_a_project_and_never_anything_outside_the_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    slug, d = store.create(home, "gone", {})
    (d / "work").mkdir()
    (d / "work" / "x").write_text("x")
    store.delete(home, slug)
    assert not d.exists()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / store.META).write_text(json.dumps({"slug": "linked", "name": "L"}), encoding="utf-8")
    (elsewhere / "precious.txt").write_text("keep")
    (home / "linked").symlink_to(elsewhere, target_is_directory=True)                               # a project folder that leads out of the home
    assert store.project_dir(home, "linked") == home / "linked"
    with pytest.raises(ValueError):
        store.delete(home, "linked")
    assert (elsewhere / "precious.txt").read_text() == "keep"


@pytest.mark.parametrize("given, clean", [("a.pdb", "a.pdb"), ("my file.cif", "my file.cif"), ("../../x.pdb", "x.pdb"), ("C:\\dir\\y.pdb", "y.pdb"), ("", "file"), ("...", "file"),
                                          ("a$b#c.pdb", "a_b_c.pdb"), (" .hidden", "hidden"), ("t\u00e4.pdb", "t_.pdb"), ("/abs/z.cif", "z.cif")])
def test_clean_name(given, clean):
    assert store.clean_name(given) == clean
    assert len(store.clean_name("n" * 500 + ".pdb")) == 120


def make_home(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def upload(home: Path, name: str, text: str = "data", folder: str = "ab12cd34") -> Path:
    up = store.uploads_dir(home) / folder
    up.mkdir(exist_ok=True)
    (up / name).write_text(text)
    return up / name


def test_adopt_moves_an_upload_into_the_project_named_by_its_role(tmp_path):
    home = make_home(tmp_path)
    slug, d = store.create(home, "p", {})
    src = upload(home, "ref.cif")
    got = store.adopt(d, str(src), "reference")
    assert got == str(d / "inputs" / "reference-ref.cif") and Path(got).read_text() == "data"
    assert not src.exists() and not src.parent.exists()                                              # moved: nothing is kept twice, the upload's own folder is gone
    assert store.uploads_dir(home).is_dir() and list(store.uploads_dir(home).iterdir()) == []
    kept = upload(home, "other.cif", folder="ef56ab78")
    (kept.parent / "second.txt").write_text("a second file in the upload folder")
    store.adopt(d, str(kept), "target")
    assert (kept.parent / "second.txt").is_file() and not kept.exists()                              # a folder that holds more is not removed
    assert store.adopt(d, str(upload(home, "x.pdb", folder="11111111")), "") == str(d / "inputs" / "x.pdb")        # no role: no prefix
    assert store.adopt(d, str(upload(home, "profile.json", folder="22222222")), "profile") == str(d / "inputs" / "profile-profile.json")


def test_adopt_leaves_every_other_path_alone(tmp_path):
    home = make_home(tmp_path)
    slug, d = store.create(home, "p", {})
    other_slug, other = store.create(home, "q", {})
    outside = tmp_path / "ref.cif"
    outside.write_text("mine")
    (home / "notes").mkdir()
    (home / "notes" / "ref.cif").write_text("in the home, but not in an upload or an inputs folder")
    (other / "bundle").mkdir()
    (other / "bundle" / "index.html").write_text("another project's page")
    (d / "work").mkdir()
    (d / "work" / "x.pdb").write_text("this project's own work folder")
    adopted = Path(store.adopt(d, str(upload(home, "mine.cif")), "reference"))
    secret = tmp_path / "secret.cif"
    secret.write_text("outside the app's folders")
    link = store.uploads_dir(home) / "linkdir"
    link.mkdir()
    (link / "evil.cif").symlink_to(secret)                                                           # an upload that is a link to a file elsewhere
    for path in (outside, home / "notes" / "ref.cif", other / "bundle" / "index.html", d / "work" / "x.pdb", link / "evil.cif", adopted, store.uploads_dir(home),
                 home / "_uploads" / "nowhere.cif", tmp_path / "missing.cif"):
        assert store.adopt(d, str(path), "reference") == str(path), path
    assert secret.read_text() == "outside the app's folders" and (link / "evil.cif").is_symlink()


def test_adopt_never_overwrites_a_file_of_the_project(tmp_path):
    home = make_home(tmp_path)
    slug, d = store.create(home, "p", {})
    names = [Path(store.adopt(d, str(upload(home, "x.cif", text=f"file {i}", folder=f"0000000{i}")), "reference")).name for i in range(3)]
    assert names == ["reference-x.cif", "reference-x-2.cif", "reference-x-3.cif"]
    assert [(d / "inputs" / n).read_text() for n in names] == ["file 0", "file 1", "file 2"]
    assert Path(store.adopt(d, str(upload(home, "x.cif", text="target", folder="00000009")), "target")).name == "target-x.cif"          # another role: another name
    assert [Path(store.adopt(d, str(upload(home, "plain", text=str(i), folder=f"1000000{i}")), "profile")).name for i in range(2)] == ["profile-plain", "profile-plain-2"]
    assert [Path(store.adopt(d, str(upload(home, "my.tar.gz", text=str(i), folder=f"2000000{i}")), "")).name for i in range(2)] == ["my.tar.gz", "my.tar-2.gz"]


def test_adopt_copies_a_file_from_another_projects_inputs_so_deleting_that_project_is_safe(tmp_path):
    home = make_home(tmp_path)
    first_slug, first = store.create(home, "first", {})
    original = Path(store.adopt(first, str(upload(home, "ref.cif", text="the reference")), "reference"))
    second_slug, second = store.create(home, "second", {})
    copy = Path(store.adopt(second, str(original), "reference"))
    assert copy.parent == second / "inputs" and copy.read_text() == "the reference" and original.read_text() == "the reference"     # copied, not moved
    store.delete(home, first_slug)
    assert copy.read_text() == "the reference" and not original.exists()
    assert store.adopt(second, str(copy), "reference") == str(copy)                                  # already here: not copied again


def test_a_copy_of_a_copy_keeps_the_name_it_had(tmp_path):
    home = make_home(tmp_path)
    slug, d = store.create(home, "gen1", {})
    path = store.adopt(d, str(upload(home, "ref.cif")), "reference")
    assert Path(path).name == "reference-ref.cif"
    for generation in (2, 3, 4):                                                                     # each Edit the inputs copies the file again
        slug, d = store.create(home, f"gen{generation}", {})
        path = store.adopt(d, path, "reference")
        assert Path(path).name == "reference-ref.cif" and Path(path).parent == d / "inputs", generation


def test_the_role_is_added_once_to_a_name_and_only_for_that_role(tmp_path):
    home = make_home(tmp_path)
    slug, d = store.create(home, "p", {})
    names = {}
    for i, (name, role) in enumerate((("target-notes.pdb", "target"), ("reference-x.cif", "target"), ("targets.pdb", "target"), ("target-.pdb", "target"),
                                      ("reference-x.cif", "reference"), ("profile-a.json", "profile"))):
        names[(name, role)] = Path(store.adopt(d, str(upload(home, name, folder=f"0000000{i}")), role)).name
    assert names == {("target-notes.pdb", "target"): "target-notes.pdb",                            # it already starts with the role: left as it is
                     ("reference-x.cif", "target"): "target-reference-x.cif",                       # another role's prefix is a name like any other
                     ("targets.pdb", "target"): "target-targets.pdb", ("target-.pdb", "target"): "target-.pdb",
                     ("reference-x.cif", "reference"): "reference-x.cif", ("profile-a.json", "profile"): "profile-a.json"}
    again = Path(store.adopt(d, str(upload(home, "target-notes.pdb", text="second", folder="00000099")), "target"))
    assert again.name == "target-notes-2.pdb" and again.read_text() == "second"                      # a clash still gets a number


def test_old_uploads_are_removed_at_start_up_and_new_ones_are_kept(tmp_path):
    home = make_home(tmp_path)
    old, new = upload(home, "old.cif", folder="00000001").parent, upload(home, "new.cif", folder="00000002").parent
    middle = upload(home, "middle.cif", folder="00000003").parent
    (store.uploads_dir(home) / "stray-file.txt").write_text("a file, not an upload folder")
    for folder, days in ((old, 8), (middle, 6)):
        stamp = time.time() - days * 86400
        os.utime(folder, (stamp, stamp))
    store.clean_uploads(home)
    assert not old.exists() and middle.is_dir() and new.is_dir() and (store.uploads_dir(home) / "stray-file.txt").is_file()
    store.clean_uploads(home, days=5)                                                                # the limit can be given
    assert not middle.is_dir() and new.is_dir()
    store.clean_uploads(tmp_path / "nowhere")                                                        # no folder, no error
    slug, d = store.create(home, "p", {})
    adopted = store.adopt(d, str(upload(home, "adopted.cif", folder="00000004")), "reference")
    assert Path(adopted).is_file()                                                                   # a project's own copy is never in the uploads folder


def test_latest_log_is_the_end_of_the_build_log(tmp_path):
    slug, d = store.create(tmp_path, "p", {})
    assert store.latest_log(d) is None
    (d / "build.log").write_text("\n".join(f"line {i}" for i in range(200)) + "\n", encoding="utf-8")
    assert store.latest_log(d) == [f"line {i}" for i in range(120, 200)] and store.latest_log(d, 3) == ["line 197", "line 198", "line 199"]


def test_builds_that_were_running_when_the_app_stopped_are_shown_as_interrupted(tmp_path):
    folders = {}
    for status in ("building", "ready", "new", "failed", "cancelled"):
        slug, d = store.create(tmp_path, status, {"runs": []})
        meta = store.read_meta(d)
        meta["build"] = {"status": status, "job": "j1", "started": "2026-01-01T00:00:00+0000"}
        store.write_meta(d, meta)
        folders[status] = d
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / store.META).write_text("{not json", encoding="utf-8")
    (tmp_path / "plain").mkdir()
    assert store.mark_interrupted(tmp_path) == 1
    b = store.read_meta(folders["building"])["build"]
    assert b["status"] == "failed" and b["error_kind"] == "crash" and "interrupted" in b["error"] and "Rebuild" in b["error"] and b["job"] == "j1"     # the rest is kept
    assert {k: store.read_meta(d)["build"]["status"] for k, d in folders.items() if k != "building"} == {"ready": "ready", "new": "new", "failed": "failed",
                                                                                                      "cancelled": "cancelled"}
    assert store.summary(folders["building"], "0.1.0")["status"] == "failed" and store.mark_interrupted(tmp_path) == 0 and store.mark_interrupted(tmp_path / "nowhere") == 0


# ---------------------------------------------------------------------------------------------------------------- store: saved decisions
def event(i: int, ts: str = "2026-01-01T00:00:00Z") -> dict:
    return {"v": 1, "type": "decision", "id": f"ev{i:014x}", "ts": ts, "unit": "family", "design": "d", "decision": "keep"}


def test_merge_events_is_a_union_by_id_oldest_first(tmp_path):
    slug, d = store.create(tmp_path, "p", {})
    assert store.merge_events(d, []) == 0 and json.loads((d / store.EVENTS).read_text()) == []
    assert store.merge_events(d, [event(1, "2026-01-02T00:00:00Z"), event(2, "2026-01-01T00:00:00Z")]) == 2
    assert store.merge_events(d, [event(2, "2026-01-01T00:00:00Z"), event(3, "2026-01-03T00:00:00Z"), event(3, "2026-01-03T00:00:00Z")]) == 3
    assert [e["id"] for e in json.loads((d / store.EVENTS).read_text())] == [event(i)["id"] for i in (2, 1, 3)]
    assert store.merge_events(d, [{"x": 1}, {"id": 5}, "text", None, event(4, "2026-01-04T00:00:00Z")]) == 4                    # items without a text id are dropped
    assert not (d / (store.EVENTS + ".tmp")).exists()


def test_merge_events_ignores_an_unreadable_saved_file(tmp_path):
    slug, d = store.create(tmp_path, "p", {})
    (d / store.EVENTS).write_text("{broken", encoding="utf-8")
    assert store.merge_events(d, [event(1)]) == 1 and [e["id"] for e in json.loads((d / store.EVENTS).read_text())] == [event(1)["id"]]


def test_parallel_saves_all_succeed_and_lose_no_event(tmp_path):
    """Two review pages (tabs) may save at the same moment: 12 writers of 25 events each, all kept."""
    slug, d = store.create(tmp_path, "p", {})
    errors: list[Exception] = []

    def worker(k: int) -> None:
        for i in range(25):
            try:
                store.merge_events(d, [event(k * 1000 + i, f"2026-02-01T00:{k:02d}:{i:02d}Z")])
            except Exception as ex:                      # noqa: BLE001
                errors.append(ex)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    saved = json.loads((d / store.EVENTS).read_text())
    assert {e["id"] for e in saved} == {event(k * 1000 + i)["id"] for k in range(12) for i in range(25)} and len(saved) == 300
    assert [e["ts"] for e in saved] == sorted(e["ts"] for e in saved) and not list(d.glob("*.tmp"))


# ---------------------------------------------------------------------------------------------------------------- jobs: commands, messages, processes
@pytest.mark.parametrize("rid, ok", [("run01", True), ("a", True), ("A.b-c_d", True), ("x" * 31, True), ("x" * 32, False), ("", False), ("-a", False), (".a", False),
                                     ("a b", False), ("a/b", False), ("\u00e4", False), (None, False)])
def test_run_ids(rid, ok):
    assert jobs.run_id_ok(rid) is ok


def test_import_command_follows_the_choices_of_the_import_page(tmp_path):
    d = tmp_path / "p"
    inputs = {"runs": [{"id": "run01", "path": "/r/one", "settings": "/s/one.json", "label": "First"}, {"id": "run02", "path": "/r/two"}],
              "target_pdb": "/t/construct.pdb", "reference": "/t/ref.cif"}
    cmd = jobs.import_command(inputs, d, "My project")
    assert cmd == [sys.executable, "-m", "binder_review", "import", "run01=/r/one", "run02=/r/two", "--target-pdb", "/t/construct.pdb", "--reference-cif", "/t/ref.cif",
                   "--reference-chain", "A", "--partner-chains", "auto", "--settings", "run01=/s/one.json", "--label", "run01=First",
                   "--name", "My project", "--out", str(d / "work"), "--bundle", str(d / "bundle")]
    full = jobs.import_command({**inputs, "reference_id": "1abc", "reference_chain": "B", "partner_chains": "A,C", "uniprot": "P12345", "profile": "/p.json", "online": True,
                                "consurf": True}, d, "n", no_cache=True)
    for flag, value in (("--reference-id", "1abc"), ("--reference-chain", "B"), ("--partner-chains", "A,C"), ("--uniprot", "P12345"), ("--profile", "/p.json")):
        assert full[full.index(flag) + 1] == value
    assert {"--online", "--consurf", "--no-cache"} <= set(full) and full[-1] == "--no-cache"
    assert jobs.bundle_command(d) == [sys.executable, "-m", "binder_review", "build-bundle", str(d / "work"), "--out", str(d / "bundle")]


@pytest.mark.parametrize("placement, passed", [("whole", ["--placement", "whole"]), ("core", ["--placement", "core"]), ("interface", []), (None, []), ("", []), ("best", []),
                                               ("Whole", []), (3, [])])
def test_the_placement_is_passed_to_the_command_only_when_it_is_not_the_default(tmp_path, placement, passed):
    """How designs are placed on the full-length protein: the command's own default is the fit around the interface, so only whole and core are named."""
    inputs = {"runs": [{"id": "run01", "path": "/r/one"}], "target_pdb": "/t/construct.pdb", "reference": "/t/ref.cif", "placement": placement}
    cmd = jobs.import_command(inputs, tmp_path, "n")
    assert [x for x in cmd if x == "--placement"] == passed[:1]
    if passed:
        assert cmd[cmd.index("--name") - 2:cmd.index("--name")] == passed                                                   # just before --name
    again = jobs.import_command({k: v for k, v in inputs.items() if k != "placement"}, tmp_path, "n")                 # an older project has no choice recorded
    assert "--placement" not in again


def test_the_command_is_one_the_cli_understands(tmp_path):
    inputs = {"runs": [{"id": "run01", "path": "/r/one", "settings": "/s.json", "label": "L"}], "target_pdb": "/t.pdb", "reference": "/r.cif", "uniprot": "P1", "profile": "/p.json",
              "online": True, "consurf": True, "reference_id": "1abc"}
    argv = jobs.import_command(inputs, tmp_path, "n", no_cache=True)[3:]
    args = cli.build_parser().parse_args(argv)
    assert args.run_dirs == ["run01=/r/one"] and args.target_pdb == "/t.pdb" and args.reference_cif == "/r.cif" and args.reference_id == "1abc" and args.no_cache
    assert args.settings == ["run01=/s.json"] and args.label == ["run01=L"] and args.out == str(tmp_path / "work") and args.bundle == str(tmp_path / "bundle")
    assert cli.build_parser().parse_args(jobs.bundle_command(tmp_path)[3:]).project_dir == str(tmp_path / "work")
    assert args.placement == "interface"                                                                                 # nothing named: the default
    for choice in ("whole", "core"):
        named = cli.build_parser().parse_args(jobs.import_command({**inputs, "placement": choice}, tmp_path, "n")[3:])
        assert named.placement == choice and named.run_dirs == ["run01=/r/one"]


@pytest.mark.parametrize("line, kind, name", [("[  0.0s] read runs ...", "start", "read runs"), ("[ 12.3s] interface analysis ...", "start", "interface analysis"),
                                              ("[1234.5s] bundle done in 0.3 s", "done", "bundle"), ("[  0.6s] read runs done in 0.6 s", "done", "read runs"),
                                              ("[  3.9s]   poses: 1/30", "sub", None)])
def test_log_lines_of_the_pipeline_are_recognised(line, kind, name):
    found = {"start": jobs._START.match(line), "done": jobs._DONE.match(line), "sub": jobs._SUB.match(line)}
    assert {k: m is not None for k, m in found.items()} == {k: k == kind for k in found}                      # exactly one kind of line
    if name:
        assert found[kind]["name"] == name
    else:
        assert found[kind]["detail"].strip() == "poses: 1/30"


@pytest.mark.parametrize("lines, code, message, kind", [
    (["[ 1.0s] x ...", "binder-review import: the construct matches the reference chain at only 16 of 110 residues"], 2, "the construct matches the reference chain at only 16 of 110 residues", "inputs"),
    (["binder-review import: something failed"], 1, "something failed", "failed"),
    (["binder-review build-bundle: no export here", "more", "lines"], 1, "no export here", "failed"),
    (["binder-review inspect: not a run folder"], 2, "not a run folder", "inputs"),
    (["Traceback (most recent call last):", "  File x", "ValueError: boom"], 1, "ValueError: boom", "crash"),
    (["concurrent.futures.process.BrokenProcessPool: A process in the process pool was terminated"], 1, "ran out of memory", "crash"),
    (["Killed"], 137, "ran out of memory", "crash"),
    ([], 7, "The build stopped with exit code 7.", "crash"),
    (["", "  ", ""], 3, "The build stopped with exit code 3.", "crash"),
    (["binder-review import: --target-pdb is wrong"] + [f"line {i}" for i in range(30)], 2, "the target structure is wrong", "inputs"),
    (["binder-review import: far back"] + [f"line {i}" for i in range(45)], 2, "line 44", "crash"),
])
def test_the_message_of_a_failed_build(lines, code, message, kind):
    got, got_kind = jobs._explain(lines, code)
    assert message in got and got_kind == kind and "\n" not in got


@pytest.mark.parametrize("raw, shown", [
    ("name the chain of the target with --reference-chain", "choose the chain of your target in Options"),
    ("/Users/me/data/run01/final_design_stats.csv: missing column X", "final_design_stats.csv: missing column X"),
    ("see (/Users/me/data/run01/x.json) for details", "see (x.json) for details"),
    ("--target-pdb and --reference-cif are required", "the target structure and the reference structure are required"),
    ("use --uniprot, --profile, --online or --settings", "use the UniProt accession in Options, the profile file in Options, the online lookup in Options or the settings file"),
    ("--reference-id and --partner-chains", "the reference name and the other subunits in Options"),
    ("nothing to change here: 12 of 110", "nothing to change here: 12 of 110"),
    ("relative/path/x.csv stays", "relative/path/x.csv stays"),
    ("/a/b/c.cif: bad, /d/e/f.pdb; and /g/h/i.csv", "c.cif: bad, f.pdb; and i.csv"),
    ("could not fetch https://rest.uniprot.org/uniprotkb/P12345.fasta: timed out", "could not fetch https://rest.uniprot.org/uniprotkb/P12345.fasta: timed out"),
    ("see http://127.0.0.1:51731/p/x/data/campaign.js and /a/b/c.cif", "see http://127.0.0.1:51731/p/x/data/campaign.js and c.cif"),
    ("/a/b/c.cif is not at https://files.rcsb.org/download/1ABC.cif", "c.cif is not at https://files.rcsb.org/download/1ABC.cif"),
    ("ftp://host/dir/file.txt (and /x/y/z.cif)", "ftp://host/dir/file.txt (and z.cif)"),
])
def test_friendly_makes_the_commands_wording_readable(raw, shown):
    assert jobs.friendly(raw) == shown


def run_job(code: str, stages=(("read runs", "Reading"), ("target frame", "Frame")), cwd: Path | None = None, log: Path | None = None):
    """A job manager running a one-line Python program; returns (manager, job, finished jobs)."""
    finished: list[jobs.Job] = []
    mgr = jobs.JobManager(finished.append)
    job = mgr.start("build", "demo", [sys.executable, "-c", code], cwd or Path.cwd(), list(stages), log or Path("/dev/null"))
    return mgr, job, finished


def wait_stopped(job: jobs.Job, finished: list, timeout: float = 20.0) -> None:
    t0 = time.time()
    while (job.status == "running" or not finished) and time.time() - t0 < timeout:
        time.sleep(0.02)
    assert finished and finished[0] is job, "the job did not finish"


def test_a_job_follows_the_log_of_its_process(tmp_path):
    code = ("print('[  0.0s] read runs ...'); print('[  0.1s]   run01: 12 accepted'); print('[  0.2s] read runs done in 0.2 s'); print('[  0.3s] target frame ...'); "
            "print('[  0.4s]   placing: 5/9', flush=True); import time; time.sleep(0.6)")
    mgr, job, finished = run_job(code, log=tmp_path / "build.log")
    assert mgr.active() is job and job.view()["status"] == "running"
    with pytest.raises(RuntimeError, match="another build is running"):
        mgr.start("build", "other", [sys.executable, "-c", "pass"], tmp_path, [], tmp_path / "x.log")
    wait_stopped(job, finished)
    v = job.view()
    assert v["status"] == "done" and v["returncode"] == 0 and v["error"] is None and v["stage"] == 2 and v["done"] == [0] and v["progress"] == 0.5
    assert v["stages"] == ["Reading", "Frame"] and v["detail"] == "placing: 5/9" and v["log_total"] == 5 and v["elapsed"] >= 0 and mgr.active() is None
    assert (tmp_path / "build.log").read_text().splitlines() == v["log"] and job.view(3)["log"] == v["log"][3:] and job.view(3)["log_from"] == 3
    assert mgr.get(job.id) is job and mgr.get("nope") is None


def test_the_detail_line_is_the_latest_indented_progress_message():
    code = "print('[  0.0s] read runs ...'); print('[  0.1s]   run01: 12 accepted', flush=True); import time; time.sleep(1.0)"
    mgr, job, finished = run_job(code)
    t0 = time.time()
    while job.view()["detail"] == "" and time.time() - t0 < 10:
        time.sleep(0.02)
    assert job.view()["detail"] == "run01: 12 accepted" and job.view()["stage"] == 0
    mgr.cancel(job.id)
    wait_stopped(job, finished)


def test_a_failing_process_ends_with_the_commands_own_message():
    mgr, job, finished = run_job("import sys; print('[  0.0s] read runs ...'); print('[  0.1s] read runs done in 0.1 s'); print('binder-review import: the files do not fit'); sys.exit(2)")
    wait_stopped(job, finished)
    v = job.view()
    assert (v["status"], v["error"], v["error_kind"], v["returncode"], v["done"], v["stage"]) == ("failed", "the files do not fit", "inputs", 2, [0], 1)


def test_a_crashing_process_ends_with_its_last_line():
    mgr, job, finished = run_job("raise ValueError('boom')")
    wait_stopped(job, finished)
    v = job.view()
    assert v["status"] == "failed" and v["error_kind"] == "crash" and v["error"] == "ValueError: boom" and v["returncode"] == 1


def test_cancel_stops_the_process_and_keeps_the_cancelled_status():
    mgr, job, finished = run_job("import time; print('start', flush=True); time.sleep(60)")
    t0 = time.time()
    while not job.lines and time.time() - t0 < 10:
        time.sleep(0.02)
    assert mgr.cancel("nope") is False and mgr.cancel(job.id) is True and mgr.cancel(job.id) is False
    assert job.status == "cancelled" and mgr.active() is None
    wait_stopped(job, finished)
    assert job.proc.returncode == -15 and job.view()["error"] == "The build was cancelled." and job.view()["error_kind"] == "cancelled" and job.status == "cancelled"


def test_a_callback_that_raises_does_not_hide_the_result():
    def bad(job):
        raise RuntimeError("callback bug")

    mgr = jobs.JobManager(bad)
    job = mgr.start("build", "demo", [sys.executable, "-c", "pass"], Path.cwd(), [], Path("/dev/null"))
    t0 = time.time()
    while job.status == "running" and time.time() - t0 < 10:
        time.sleep(0.02)
    assert job.status == "done" and mgr.active() is None


# ---------------------------------------------------------------------------------------------------------------- native dialogs, with the system calls replaced
def test_dialogs_exist_only_on_macos_with_osascript(monkeypatch):
    monkeypatch.setattr(dialogs, "sys", SimpleNamespace(platform="linux"))                      # the module's own names are replaced, not the system's
    monkeypatch.setattr(dialogs, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/osascript"))
    assert dialogs.available() is False
    with pytest.raises(RuntimeError, match="no native dialog"):
        dialogs.pick("folder", "x")
    monkeypatch.setattr(dialogs, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(dialogs, "shutil", SimpleNamespace(which=lambda name: None))
    assert dialogs.available() is False
    monkeypatch.setattr(dialogs, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/osascript"))
    assert dialogs.available() is True


def test_pick_builds_an_open_panel_script_and_reads_the_choice(monkeypatch, tmp_path):
    monkeypatch.setattr(dialogs, "available", lambda: True)
    calls: list[tuple] = []
    replies = iter([subprocess.CompletedProcess([], 0, "/a/b/\n/c d\n\n/\n", ""), subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 1, "", "boom\n"), subprocess.TimeoutExpired("osascript", 1)])

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(dialogs, "subprocess", SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired))
    assert dialogs.pick("file", 'Say "hi"', multiple=True, default=str(tmp_path)) == ["/a/b", "/c d", "/"]
    cmd, kw = calls[0]
    script = cmd[cmd.index("-e") + 1]
    assert cmd[:3] == ["osascript", "-l", "JavaScript"] and kw["capture_output"] and kw["text"] and kw["timeout"] == 900.0
    assert "panel.canChooseFiles = true;" in script and "panel.canChooseDirectories = false;" in script and "panel.allowsMultipleSelection = true;" in script
    assert 'panel.message = "Say \\"hi\\"";' in script and json.dumps(str(tmp_path)) in script and "directoryURL" in script
    assert dialogs.pick("folder", "p") is None                                                  # cancelled: nothing printed
    script = calls[1][0][calls[1][0].index("-e") + 1]
    assert "panel.canChooseFiles = false;" in script and "panel.canChooseDirectories = true;" in script and "allowsMultipleSelection = false;" in script and "directoryURL" not in script
    with pytest.raises(RuntimeError, match="boom"):
        dialogs.pick("folder", "p")
    assert dialogs.pick("folder", "p", timeout=1) is None                                       # no answer in time counts as cancelled


def test_reveal_opens_folders_and_files_the_way_the_system_does(monkeypatch, tmp_path):
    opened: list[list[str]] = []
    monkeypatch.setattr(dialogs, "subprocess", SimpleNamespace(Popen=lambda cmd, **kw: opened.append(cmd)))
    f = tmp_path / "f.txt"
    f.write_text("x")
    monkeypatch.setattr(dialogs, "sys", SimpleNamespace(platform="darwin"))
    dialogs.reveal(tmp_path)
    dialogs.reveal(f)
    assert opened == [["open", str(tmp_path)], ["open", "-R", str(f)]]
    opened.clear()
    monkeypatch.setattr(dialogs, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(dialogs, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/xdg-open"))
    dialogs.reveal(f)
    assert opened == [["xdg-open", str(tmp_path)]]
    opened.clear()
    monkeypatch.setattr(dialogs, "shutil", SimpleNamespace(which=lambda name: None))
    dialogs.reveal(tmp_path)
    assert opened == []


# ---------------------------------------------------------------------------------------------------------------- the command line entry
def test_the_app_command_and_its_options():
    p = cli.build_parser()
    a = p.parse_args(["app"])
    assert (a.port, a.home, a.window, a.quit_when_closed, a.verbose) == (DEFAULT_PORT, None, "auto", False, False) and a.func is cli._cmd_app
    a = p.parse_args(["app", "--port", "1234", "--home", "/x", "--window", "none", "--quit-when-closed", "-v"])
    assert (a.port, a.home, a.window, a.quit_when_closed, a.verbose) == (1234, "/x", "none", True, True)
    for bad in (["app", "--window", "tab"], ["app", "--port", "x"]):
        with pytest.raises(SystemExit) as ex:
            p.parse_args(bad)
        assert ex.value.code == 2


def test_the_app_command_starts_the_server_with_its_options(monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "run", lambda **kw: seen.update(kw) or 0)
    assert cli.main(["app", "--port", "1234", "--home", "/x", "--window", "none", "--quit-when-closed", "-v"]) == 0
    assert seen == {"port": 1234, "home": "/x", "open_mode": "none", "quit_when_closed": True, "verbose": True}
    seen.clear()
    monkeypatch.setattr(server, "run", lambda **kw: 3)
    assert cli.main(["app"]) == 3


def test_the_app_command_is_in_the_help_of_the_module():
    for args, words in ((["--help"], ["{app,import,chains,inspect,build-bundle}"]), (["app", "--help"], ["--quit-when-closed", "--window", "51731", "BINDERSCOPE_HOME", "127.0.0.1"])):
        out = subprocess.run([sys.executable, "-m", "binder_review", *args], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0 and all(w in " ".join(out.stdout.split()) for w in words), (args, out.stdout[-400:], out.stderr[-400:])
