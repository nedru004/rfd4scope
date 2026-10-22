"""One headless-browser smoke test: the built toy bundle (with a specificity panel) is opened from file:// in Chrome, one page after the
other. Every page must settle without console errors or exceptions and without a request that leaves the folder; the Overview, Board,
Inspector, Map and Query pages must show their key elements and the numbers of the project; the order-list exports (FASTA and CSV) of the
Query page must carry the design ids and sequences of the toy runs.

Needs Node 22 or later and Google Chrome (or Chromium); set CHROME_PATH when Chrome is not in a standard place. Skipped otherwise.
The driver is browser_smoke.mjs next to this file (DevTools protocol through cdp.mjs, one Chrome at a time)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from toydata import appkit

HERE = Path(__file__).resolve().parent
NODE, CHROME = appkit.NODE, appkit.CHROME

pytestmark = pytest.mark.skipif(not (NODE and CHROME), reason="needs Node 22+ and Google Chrome or Chromium (set CHROME_PATH)")


@pytest.fixture(scope="module")
def smoke(toy_panel_bundle, toy_panel_blobs, tmp_path_factory):
    n_inspected = len(toy_panel_blobs.inspect["designs"])
    checks = {
        "index": {"designs": "BS.blob('campaign').designs.length", "runs": "BS.blob('campaign').runs.map((r) => r.id)",
                  "highlights": "document.querySelectorAll('#hlList > *').length", "title": "document.title",
                  "text_has": "['2 runs, 22 accepted designs', 'rest-of-protein', 'run01', 'run02'].map((s) => document.body.innerText.includes(s))"},
        "board": {"designs": "window.__fb.D.length", "families": "window.__fb.FAMS.length",
                  "order_buttons": "['btnCsv', 'btnFasta', 'btnFastaFile'].map((i) => !!document.getElementById(i))",
                  "canvases": "document.querySelectorAll('canvas').length",
                  "text_has": "['run funnel', 'order list'].map((s) => document.body.innerText.toLowerCase().includes(s))"},
        "inspector": {"order": "window.__ins.listOrder().length", "tag": "window.__ins.st.tag", "canvases": "document.querySelectorAll('canvas').length"},
        "map": {"points": "window.__map.pts.length", "canvases": "document.querySelectorAll('canvas').length"},
        "query": {"designs": "window.__q.D().length", "rules": "window.__q.RULES.length",
                  "first": "window.__q.D().slice(0, 3).map((d) => [d.id, d.seq])",
                  "fasta": "window.__q.fastaOf(window.__q.D().slice(0, 3))", "csv": "window.__q.csvOf(window.__q.D().slice(0, 3))",
                  "species": "BS.blob('homolog').orth.map((o) => o.key)"},
    }
    f = tmp_path_factory.mktemp("smoke") / "checks.json"
    f.write_text(json.dumps(checks))
    r = subprocess.run([NODE, str(HERE / "browser_smoke.mjs"), str(toy_panel_bundle), "--chrome", CHROME, "--checks", str(f)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout)
    out["n_inspected"] = n_inspected
    return out


def test_every_page_settles_without_errors_or_outside_requests(smoke):
    assert set(smoke["pages"]) == {"index", "board", "inspector", "map", "query"}
    for name, p in smoke["pages"].items():
        assert not p.get("missing"), name
        assert p["settled"] is True, name
        assert p["errors"] == [], (name, p["errors"])                      # no console error, no exception, no failed load
        assert p["external"] == [], (name, p["external"])                  # nothing but files of the bundle was requested
        assert p["overflow_x"] <= 1 and p["n_text"] > 500 and p["scripts"] >= 4, name
        assert not re.search(r"\{@?[A-Za-z_.]+\}", p["text"]), name          # every template token of the page script was filled in


def test_overview_page(smoke):
    p = smoke["pages"]["index"]
    assert p["title"].startswith("Overview") and p["checks"]["text_has"] == [True, True, True, True]       # the highlights come from the data
    assert p["checks"]["designs"] == 30 and p["checks"]["runs"] == ["run01", "run02"] and p["checks"]["highlights"] >= 5
    assert p["n_table"] >= 1 and p["n_link"] >= 8


def test_board_page(smoke):
    p = smoke["pages"]["board"]
    c = p["checks"]
    assert p["title"] == "Binder Family Board" and c["designs"] == 22 and c["families"] == 16 and c["order_buttons"] == [True, True, True]
    assert p["n_svg"] > 10 and c["canvases"] >= 1 and p["n_button"] >= 20 and c["text_has"] == [True, True]


def test_inspector_page(smoke):
    p = smoke["pages"]["inspector"]
    c = p["checks"]
    assert p["title"] == "Binder Design Inspector" and c["order"] == smoke["n_inspected"] and c["canvases"] >= 1
    assert c["tag"].startswith("binder_l") and "Prev" in p["text"] and f"1 / {smoke['n_inspected']}" in p["text"]


def test_map_page(smoke):
    p = smoke["pages"]["map"]
    assert p["title"] == "Binder Campaign Map" and p["checks"]["points"] == 22 and p["checks"]["canvases"] >= 1
    assert "22 designs in 16 families" in p["text"] and p["n_input"] >= 2


def test_query_page_and_the_fasta_and_csv_exports(smoke, toy_run1, toy_run2):
    p = smoke["pages"]["query"]
    c = p["checks"]
    assert p["title"] == "Binder Structure Query" and c["designs"] == 22 and c["rules"] >= 10 and c["species"] == ["orth1", "orth2", "orth3"]
    seq_of = {d.id: d.sequence for run in (toy_run1, toy_run2) for d in run.designs}
    fasta = c["fasta"].strip().split("\n")
    assert len(fasta) == 6 and all(l.startswith(">") for l in fasta[0::2])
    for (did, seq), head, body in zip(c["first"], fasta[0::2], fasta[1::2]):
        assert head.startswith(f">{did} run=run0") and "family=" in head and "mode=M" in head and f"length={len(seq)}" in head
        assert body == seq == seq_of[did]                                  # the exported sequence is the designed one
    rows = [r for r in c["csv"].strip().split("\r\n")]
    assert rows[0].startswith("id,run,status,family,sequence_index,rank,mode") and len(rows) == 4
    assert [r.split(",")[0] for r in rows[1:]] == [d for d, _ in c["first"]] and all(",accepted," in r for r in rows[1:])
