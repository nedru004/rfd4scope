"""One headless-browser smoke test of the Binderscope app: a first session from the welcome page to a deleted project.

The app server runs in a thread of this process on a free port with an empty Binderscope folder. Chrome (one, headless, driven by
app_smoke.mjs over the DevTools protocol) opens the welcome page, follows its link to the import page and fills it in: the runs through
the page's own file browser (the server answers 501 for the native dialog, as on a system without one), the target through window.BSNEW,
the reference as a dropped file. It clicks Build, waits for the success state, opens the project and its five review pages, makes a
decision on the Board page, checks that the decision reached the project folder and comes back after the browser's own copy was removed,
returns to the project list and uses the card's menu (Edit the inputs, Update the review pages, Delete project).
Console errors are collected on every page; the only address Chrome may reach is the app.

The import page also says how the structure given to BindCraft fits the chosen chain of the reference (the same numbering vote and CA fit the build uses)
and offers how designs are placed on the full-length protein (Options).

A second, shorter session (app_retry.mjs) is a person who made a mistake: the reference has a second chain with another sequence, the wrong chain is chosen
(the page says that the target structure does not fit it) and fails the first build, "Back to the inputs", the chain is fixed in Options, the placement is
changed to the whole-target fit and the second build must start in the same project, with the dropped reference that the first try moved into it. The
placement chosen on the page must reach the build, and the note under the reference names it. "Edit the inputs" of a project whose chain is not the suggested
one shows the fit of that chain; a reference that the target structure fits only loosely gets a yellow note that says what the chosen placement does about it.
The list of projects shows a project that an older Binderscope built with the advice to rebuild it.

Needs Node 22 or later and Google Chrome (or Chromium); set CHROME_PATH when Chrome is not in a standard place. Skipped otherwise."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from toydata import appkit
from toydata.variants import loosely_fitting_reference, scrambled_chain_b

from binder_review import __version__
from binder_review.app import dialogs, store

HERE = Path(__file__).resolve().parent
NODE, CHROME = appkit.NODE, appkit.CHROME

pytestmark = pytest.mark.skipif(not (NODE and CHROME), reason="needs Node 22+ and Google Chrome or Chromium (set CHROME_PATH)")


def no_native_windows(mp, toy_dir):
    """The page falls back on its own file browser when the server says there is no native dialog; no Finder window is opened."""
    def no_dialog(*a, **k):
        raise RuntimeError("no native dialog on this system")                                                      # as on a system without one

    mp.setattr(dialogs, "pick", no_dialog)
    mp.setattr(dialogs, "reveal", lambda path: pytest.fail("a Finder window was opened"))
    mp.setattr(Path, "home", classmethod(lambda cls: toy_dir))                                                     # where the page's file browser starts (this process only)


@pytest.fixture(scope="module")
def session(toy_dir, tmp_path_factory):
    home = tmp_path_factory.mktemp("browser_app") / "Binderscope"
    with pytest.MonkeyPatch.context() as mp, appkit.running(home) as (srv, api):
        no_native_windows(mp, toy_dir)
        cmd = [NODE, str(HERE / "app_smoke.mjs"), "--url", f"http://127.0.0.1:{srv.port}", "--chrome", CHROME, "--name", "Toy demo",
               "--run", f"run01={toy_dir / 'runs' / 'run01'}", "--run", f"run02={toy_dir / 'runs' / 'run02'}",
               "--target", str(toy_dir / "construct" / "toy_construct.pdb"), "--reference", str(toy_dir / "reference" / "toy_target.cif")]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert done.returncode == 0, done.stderr[-2000:]
        out = json.loads(done.stdout)
        assert out["failed_at"] is None, json.dumps(out["steps"].get(out["failed_at"]))
        yield SimpleNamespace(out=out, steps=out["steps"], api=api, home=home, srv=srv)


def test_the_empty_app_shows_the_welcome_page(session):
    s = session.steps["home_empty"]
    assert s["title"] == "Binderscope" and s["welcome_hidden"] is False and s["list_hidden"] is True and s["n_cards"] == 0
    assert s["h1"].startswith("Decide which designs") and s["start_href"] == "/new" and s["home"] == str(session.home) and s["version"] == f"version {__version__}"


def test_the_page_falls_back_on_its_own_file_browser_and_finds_the_runs_in_a_folder(session):
    b = session.steps["browse_runs"]
    assert b["native"] is False and b["dialog_open"] is False                                  # the server said 501: no native dialog
    assert {"bindcraft2", "construct", "reference", "runs"} <= set(b["listed"])                  # it starts in the home folder (the toy project here)
    assert b["inside"] == ["..", "run01", "run02"] and b["toast"] == "Found 2 runs inside runs." and b["run_ids"] == ["run01", "run02"]


def test_the_import_page_reads_the_runs_and_checks_the_structures(session):
    new, s = session.steps["new_page"], session.steps["inputs"]
    assert new["title"].startswith("New project") and new["build_disabled"] is True and "add a run folder" in new["need"] and new["n_inputs"] > 10
    assert s["run_ids"] == ["run01", "run02"] and s["run_errors"] == ["", ""]
    assert "BindCraft 1" in s["run_chips"][0] and "12 accepted" in s["run_chips"][0] and "8 rejected" in s["run_chips"][0] and "8 families" in s["run_chips"][0]
    assert "10 accepted" in s["run_chips"][1] and "hotspots 54, 56, 62, 66" in s["run_chips"][1] and "hotspots read from the tables" in s["run_chips"][1]     # no settings file
    assert "toy_construct.pdb" in s["target"] and "chain A, 110 residues" in s["target"]
    assert "dropped reference.cif" in s["reference"] and "chains A (250), B (250)" in s["reference"] and "/_uploads/" in s["reference_path"]       # dropped = uploaded
    assert "Chain A matches your target structure" in s["reference"] and "chain B will be treated as the other subunit" in s["reference"]
    assert s["chain"] == "A" and s["chains"] == ["A", "B"] and s["name"] == "Toy demo" and s["build_disabled"] is False and s["need"].startswith("Ready.")
    chain_note, fit_note = s["notes"]                                                                    # under the reference: the chain that matches, how the structure fits it
    assert chain_note["cls"] == "good" and chain_note["text"].startswith("Chain A matches your target structure")
    assert fit_note["cls"] == "good" and re.fullmatch(r"Your target structure fits chain A: CA RMSD [0-9.]+ \u00c5 over 110 residues\. Each design is placed by a fit around its own interface\.",
                                                      fit_note["text"]), fit_note
    assert s["placement"] == {"value": "interface", "options": ["interface", "whole", "core"], "in_options": True, "opt_open": False}     # in Options, which are closed


def test_the_build_runs_in_the_page_and_ends_with_a_link_to_the_project(session):
    b = session.steps["build"]
    assert b["error"] is False and b["title"] == "Your project is ready" and b["open_href"] == "/p/toy-demo/" and b["form_hidden"] is True
    assert "All 9 steps done" in b["out"] and "what stands out" in b["out"].lower() and b["stages_hidden"] is True and b["meter"] == "100%" and b["log_lines"] > 20


def test_the_served_overview_has_the_projects_link_in_its_header(session):
    o = session.steps["overview"]
    assert o["path"] == "/p/toy-demo/" and o["title"].startswith("Overview")
    assert (o["bar_text"], o["bar_href"], o["bar_in_header"], o["bar_count"], o["bar_script"]) == ("‹ Projects", "/", True, 1, 1)
    assert o["designs"] == 30 and o["runs"] == ["run01", "run02"]                               # 22 accepted and 8 rejected designs with a structure and o["n_text"] > 500 and o["text_has_runs"] and o["n_link"] >= 8


def test_every_review_page_works_when_served_by_the_app(session):
    pages = session.steps["pages"]
    assert list(pages) == ["index", "board", "inspector", "map", "query"]
    titles = {"index": "Overview", "board": "Binder Family Board", "inspector": "Binder Design Inspector", "map": "Binder Campaign Map", "query": "Binder Structure Query"}
    for name, p in pages.items():
        assert p["title"].startswith(titles[name]) and p["errors"] == [] and p["n_text"] > 500, name
        assert p["bar_count"] == 1 and p["bar_text"] == "\u2039 Projects", name                 # the bar is on every page, once
    assert pages["board"]["n_canvas"] >= 1 and pages["inspector"]["n_canvas"] >= 1 and pages["map"]["n_canvas"] >= 1
    assert all(p["bar_in_header"] for p in pages.values())                                        # in the page's header, not floating over it


def test_a_decision_is_saved_to_the_project_folder_and_comes_back_after_the_browser_forgot_it(session):
    saved, back = session.steps["decision_saved"], session.steps["decision_restored"]
    event_id, family = saved["added"]["id"], saved["added"]["family"]
    assert saved["added"]["count"] == 1 and saved["local_ids"] == [event_id] and saved["seconds"] < 8                         # the bar sends every 2.5 s
    assert [(e["id"], e["unit"], e["design"], e["decision"]) for e in saved["server_events"]] == [(event_id, "family", family, "keep")]   # kept by the app
    assert back["removed"] is True and back["count"] == 1 and back["state"] == {family: "keep"} and back["local_ids"] == [event_id]
    assert back["seconds"] < 15 and back["path"] == "/p/toy-demo/board.html" and back["bar_count"] == 1


def test_a_dropped_file_is_moved_into_the_project_and_a_chosen_one_is_left_where_it_is(session, toy_dir):
    p = session.steps["project_inputs"]
    assert p["runs"] == ["run01", "run02"] and p["status"] == "ready"
    assert Path(p["reference"]) == session.home / "toy-demo" / "inputs" / "reference-dropped reference.cif"          # moved from _uploads, named by its role
    assert p["target_pdb"] == str(toy_dir / "construct" / "toy_construct.pdb")                                       # chosen by path: not copied, not moved


def test_the_project_list_shows_the_new_project_with_its_counts(session):
    c = session.steps["home_card"]
    assert (c["slug"], c["name"], c["open_href"]) == ("toy-demo", "Toy demo", "/p/toy-demo/") and c["n_cards"] == 1
    assert c["welcome_hidden"] is True and c["list_hidden"] is False and c["home"] == str(session.home)
    assert c["chips"] == ["2 runs", "22 accepted designs", "16 families"] and c["when"].startswith("Built ") and c["note"]


def test_edit_the_inputs_opens_the_import_page_with_the_projects_choices(session):
    e = session.steps["edit_inputs"]
    assert e["search"] == "?from=toy-demo" and e["name"] == "Toy demo 2" and e["run_ids"] == ["run01", "run02"] and e["retry"] is None       # a ready project is copied, not overwritten
    assert "toy_construct.pdb" in e["target"] and "dropped reference.cif" in e["reference"] and "Chain A matches your target structure" in e["reference"]
    assert e["chain"] == "A" and e["partners"] == "auto" and e["need"].startswith("Ready.")
    assert e["placement"] == "interface" and e["opt_open"] is False and [n["cls"] for n in e["notes"]] == ["good", "good"] and e["notes"][1]["text"].startswith("Your target structure fits chain A")


def test_the_card_menu_updates_the_pages_and_deletes_the_project(session):
    assert session.steps["menu"] == ["Edit the inputs\u2026", "Update the review pages", "Rebuild", "Rebuild from scratch", "Show in Finder", "Delete project\u2026"]
    up = session.steps["update_pages"]
    assert up["status"] == "ready" and up["after_finished"] > up["before_finished"] and up["menu_closed"] is True
    gone = session.steps["delete"]
    assert gone["asked"]["title"] == "Delete \u201cToy demo\u201d?" and "Your BindCraft run folders are not touched" in gone["asked"]["body"]
    assert gone["dialog_open"] is False and gone["list_hidden"] is True
    assert not (session.home / "toy-demo").exists() and session.api.get("/api/state").json["projects"] == []
    assert list((session.home / "_uploads").iterdir()) == []                                                         # the dropped file was moved, so it left with the project


def test_no_page_had_a_console_error_and_nothing_left_the_app(session):
    port = session.srv.port
    # the one line Chrome logs for the answer 501 of /api/pick, which is how the page learns that there is no native dialog (as on Linux)
    assert session.out["errors"] == [f"log.network: Failed to load resource: the server responded with a status of 501 (Not Implemented) http://127.0.0.1:{port}/api/pick"]
    assert session.out["external"] == []
    assert session.api.get("/api/state").json["job"] is None


# ---------------------------------------------------------------------------------------------------------------- a build that fails, Back to the inputs, Build again
@pytest.fixture(scope="module")
def retry(toy_dir, tmp_path_factory):
    work = tmp_path_factory.mktemp("browser_retry")
    home = work / "Binderscope"
    home.mkdir()
    slug, d = store.create(home, "Older analysis", {"runs": [{"id": "run01"}]})                  # a project that an older Binderscope built: no revision in its record
    meta = store.read_meta(d)
    meta["build"] = {"status": "ready", "version": __version__, "counts": {"accepted": 3, "families": 2}, "highlights": [{"text": "Built before the placement change"}],
                     "finished": store.now(), "seconds": 4.0}
    store.write_meta(d, meta)
    (d / "bundle").mkdir()
    (d / "bundle" / "index.html").write_text("<html></html>")
    store.create(home, "Chain B", {"runs": [{"id": "run02", "path": str(toy_dir / "runs" / "run02")}], "target_pdb": str(toy_dir / "construct" / "toy_construct.pdb"),
                                   "reference": str(toy_dir / "reference" / "toy_target_chain_b.cif"), "reference_chain": "B", "partner_chains": "A",
                                   "placement": "interface"})                               # the target of this reference is chain B; the page suggests chain A (the first of two alike)
    reference = scrambled_chain_b(toy_dir / "reference" / "toy_target.cif", work / "reference" / "reference.cif")        # chain A is the target, chain B another sequence
    loose = loosely_fitting_reference(toy_dir / "reference" / "toy_target.cif", work / "reference" / "turned.cif")       # a domain turned by 20 degrees: a loose fit
    with pytest.MonkeyPatch.context() as mp, appkit.running(home) as (srv, api):
        no_native_windows(mp, toy_dir)
        cmd = [NODE, str(HERE / "app_retry.mjs"), "--url", f"http://127.0.0.1:{srv.port}", "--chrome", CHROME, "--name", "Retry flow", "--run", str(toy_dir / "runs" / "run02"),
               "--target", str(toy_dir / "construct" / "toy_construct.pdb"), "--reference", str(reference), "--loose-reference", str(loose)]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert done.returncode == 0, done.stderr[-2000:]
        out = json.loads(done.stdout)
        assert out["failed_at"] is None, json.dumps(out["steps"].get(out["failed_at"]))
        yield SimpleNamespace(out=out, steps=out["steps"], api=api, home=home, srv=srv)


def test_the_page_says_which_chain_fits_and_a_wrong_chain_fails_the_first_build(retry, toy_dir):
    first, ready = retry.steps["first_try"], retry.steps["inputs"]
    assert ready["chains"] == ["A", "B"] and ready["chain"] == "A" and "/_uploads/" in ready["reference_path"] and ready["build_disabled"] is False
    assert [n["cls"] for n in ready["notes"]] == ["good", "good"] and ready["notes"][0]["text"].startswith("Chain A matches your target structure")        # chain A is suggested
    assert ready["notes"][1]["text"].startswith("Your target structure fits chain A: CA RMSD") and "over 110 residues" in ready["notes"][1]["text"]
    assert ready["placement"] == {"value": "interface", "options": ["interface", "whole", "core"], "in_options": True, "opt_open": False}
    assert first["chosen"] == "B" and [n["cls"] for n in first["notes"]] == ["good", "bad"]               # the suggestion stays; the note about the fit is red now
    bad = first["notes"][1]["text"]
    assert bad.startswith("Your target structure does not fit chain B: ") and bad.endswith("Choose the chain in Options, or another reference.") and "same amino acid" in bad
    assert first["title"] == "The build did not finish" and first["back_button"] is True and first["hint"] is True              # the hint for files that do not fit
    assert re.fullmatch(r"the construct matches the reference chain at only \d+ of 110 residues\s*The files do not fit together\..*", first["out"], flags=re.S), first["out"]
    assert first["retry"] == "retry-flow" and first["projects"] == [{"slug": "retry-flow", "name": "Retry flow", "status": "failed"}]
    assert first["reference_path"] == str(retry.home / "retry-flow" / "inputs" / "reference-reference.cif")                    # the page holds the answer's path
    assert first["target_path"] == str(toy_dir / "construct" / "toy_construct.pdb")                                           # chosen by path: unchanged


def test_back_to_the_inputs_shows_the_form_and_leaves_a_clean_progress_panel_without_a_script_error(retry):
    back = retry.steps["back"]
    assert back["form_hidden"] is False and back["bar_hidden"] is False and back["chain"] == "B"                               # the form is back as it was
    assert [n["cls"] for n in back["notes"]] == ["good", "bad"] and back["notes"][1]["text"].startswith("Your target structure does not fit chain B")
    assert back["errors"] == []
    assert back["pout_text"] == "" and back["pacts_ids"] == ["pcancel"] and back["pstages_hidden"] is False and back["plog_length"] == 0 and back["ptitle"] == "Building\u2026"


def test_build_is_enabled_by_the_page_after_back_to_the_inputs_and_after_the_fix_in_options(retry):
    assert retry.steps["back"]["build_disabled"] is False and retry.steps["fix"]["build_disabled"] is False
    fix = retry.steps["fix"]
    assert fix["chosen"] == "A" and fix["chain"] == "A" and fix["placement"] == "whole" and fix["need"].startswith("Ready.") and retry.steps["second"]["build_disabled"] is False
    assert [n["cls"] for n in fix["notes"]] == ["good", "good"] and fix["notes"][1]["text"].startswith("Your target structure fits chain A")   # the note follows the chain


def test_the_fit_note_names_the_placement_that_is_chosen_in_options(retry):
    fix, ready = retry.steps["fix"], retry.steps["inputs"]
    sentence = {"interface": "Each design is placed by a fit around its own interface.", "core": "Each design is placed by a fit of the rigid core of the target.",
                "whole": "Each design is placed by a fit of the whole target."}
    for notes, choice in ((ready["notes"], "interface"), (fix["notes"], "interface"), (fix["notes_core"], "core"), (fix["notes_whole"], "whole")):
        assert [n["cls"] for n in notes] == ["good", "good"], choice
        assert re.fullmatch(r"Your target structure fits chain A: CA RMSD [0-9.]+ \u00c5 over 110 residues\. " + re.escape(sentence[choice]), notes[1]["text"]), (choice, notes[1]["text"])


def test_the_second_build_starts_in_the_same_project_and_finishes(retry):
    second = retry.steps["second"]
    assert second["started"]["title"] == "Building Retry flow\u2026" and second["started"]["reference_path"] == retry.steps["first_try"]["reference_path"]
    assert second["started"]["stale_errors"] == 0 and second["started"]["pacts_ids"] == ["pcancel"]                         # the first failure is not on the panel any more
    assert second["projects"] == [{"slug": "retry-flow", "name": "Retry flow", "status": "ready"}]                             # one project: the retry reused the folder
    assert sorted(p.name for p in retry.home.iterdir()) == ["_uploads", "chain-b", "older-analysis", "retry-flow"] and list((retry.home / "_uploads").iterdir()) == []
    assert [p.name for p in (retry.home / "retry-flow" / "inputs").iterdir()] == ["reference-reference.cif"]                 # moved once, not copied again
    p = retry.api.get("/api/projects/retry-flow").json
    assert p["status"] == "ready" and p["error"] is None and p["counts"]["accepted"] == 10 and retry.api.get("/p/retry-flow/").status == 200


def test_the_page_shows_the_success_state_of_the_second_build(retry):
    ended = retry.steps["second"]["ended"]
    assert ended["title"] == "Your project is ready" and "All 9 steps done" in ended["out"] and ended["open_href"] == "/p/retry-flow/"
    assert ended["pacts"] == ["openp", "All projects"] and "reference-reference.cif" not in ended["out"] and "does not fit" not in ended["out"]       # no trace of the first failure


def test_the_placement_chosen_in_options_reaches_the_inputs_the_command_and_the_result(retry):
    """Fit the whole target was chosen on the page: the project records it, the command line carries it, the build used it."""
    p = retry.api.get("/api/projects/retry-flow").json
    assert p["inputs"]["placement"] == "whole" and p["inputs"]["reference_chain"] == "A"
    work = retry.home / "retry-flow" / "work"
    info = json.loads((work / "project.json").read_text(encoding="utf-8"))
    assert info["spec"]["placement"] == "whole" and info["spec"]["reference_chain"] == "A" and "--placement whole" in info["command"]
    manifest = json.loads((work / "export" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["thresholds"]["placement"]["mode"] == "whole" and manifest["thresholds"]["placement"]["fit"] == "all"
    campaign = json.loads((work / "export" / "campaign.json").read_text(encoding="utf-8"))
    assert len(campaign["designs"]) == 10 and {d["prov"]["fit"] for d in campaign["designs"]} == {"all"}                  # every design, none by a fit around the interface


def test_the_import_page_of_the_finished_project_opens_with_the_placement_and_the_options_open(retry):
    e = retry.steps["edit_again"]
    assert e["name"] == "Retry flow 2" and e["chain"] == "A" and e["placement"] == "whole" and e["opt_open"] is True and e["retry"] is None
    assert [n["cls"] for n in e["notes"]] == ["good", "good"] and e["notes"][1]["text"].startswith("Your target structure fits chain A")


def test_the_fit_note_after_edit_the_inputs_names_the_stored_placement(retry):
    """The stored placement is put into the Options select after the reference was read; the note must be drawn again for it (a value that is set in code fires no
    change event)."""
    e = retry.steps["edit_again"]
    assert e["placement"] == "whole" and e["notes"][1]["text"].endswith("Each design is placed by a fit of the whole target.")


def test_edit_the_inputs_shows_the_fit_for_the_chain_the_project_was_made_with(retry):
    e = retry.steps["edit_chain_b"]
    assert e["name"] == "Chain B 2" and e["chain"] == "B" and e["chains"] == ["A", "B"] and e["placement"] == "interface"
    assert [n["cls"] for n in e["notes"]] == ["good", "good"]
    assert e["notes"][0]["text"].startswith("Chain A matches your target structure")                       # what the page suggests is chain A, the first of two alike
    assert re.fullmatch(r"Your target structure fits chain B: CA RMSD [0-9.]+ \u00c5 over 110 residues\. Each design is placed by a fit around its own interface\.", e["notes"][1]["text"]), e


def test_a_loose_fit_gets_a_yellow_note_that_says_what_the_chosen_placement_does_about_it(retry):
    loose = retry.steps["loose_fit"]
    keeps = "Each design is placed on the reference by a fit around its interface, so the binder stays on the epitope even when other parts differ."
    warns = "The placement option in Options is not the fit around the interface, so the binder can sit off the epitope where the structures differ."
    for choice, ending in (("interface", keeps), ("whole", warns), ("core", warns), ("interface_again", keeps)):
        notes = loose[choice]
        assert [n["cls"] for n in notes] == ["good", "warn"], (choice, notes)                                 # chain A matches; the fit is loose
        text = notes[1]["text"]
        assert re.fullmatch(r"Your target structure fits chain A only loosely: CA RMSD [3-9][0-9.]* \u00c5 over 110 residues, 100% identical\. Check that this is the same protein\. .*", text), text
        assert text.endswith(ending), (choice, text)


def test_the_project_list_advises_a_rebuild_of_a_project_built_by_an_older_analysis(retry):
    cards = {c["slug"]: c for c in retry.steps["home_cards"]}
    assert set(cards) == {"older-analysis", "retry-flow", "chain-b"}
    new, old = cards["retry-flow"], cards["older-analysis"]
    assert new["warn"] == [] and [(a["text"], a["primary"]) for a in new["acts"]] == [("Open", True)] and "older" not in " ".join(new["chips"])
    assert old["warn"] == ["Built with an older analysis. Rebuild to get the improved placement of designs on the full-length protein and the exact binder-target view in the Inspector."]
    assert [(a["text"], a["primary"], a["act"]) for a in old["acts"]] == [("Rebuild", True, "rebuild"), ("Open", False, "open")]                                      # Rebuild first
    assert old["chips"] == ["1 run", "3 accepted designs", "2 families", "built with an older version"] and old["note"] == "Built before the placement change"


def test_the_retry_session_had_no_console_error_and_nothing_left_the_app(retry):
    assert retry.out["errors"] == [] and retry.out["external"] == []
