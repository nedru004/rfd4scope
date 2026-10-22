"""The toy example project in docs/schema/examples (toy.*): it validates against the schemas, hangs together, carries no local paths
and, when asked, is exactly what the generator writes now.

The files are made by tests/toydata/make_schema_examples.py (see its docstring). Set BINDERSCOPE_CHECK_EXAMPLES=1 to also regenerate
them and compare (about half a minute; the comparison holds on the platform and library versions that wrote the files, because the
toy runs come from a stochastic search whose floating point details can differ between machines).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from toydata.testing import load_validator, repo_root

ROOT = repo_root()
EXAMPLES = ROOT / "docs" / "schema" / "examples"
FILES = ("toy.campaign.json", "toy.inspect.json", "toy.homolog.json", "toy.manifest.json", "toy.profile.json", "toy.reviews.jsonl")

pytestmark = pytest.mark.skipif(not (EXAMPLES / "toy.campaign.json").is_file(), reason="docs/schema/examples/toy.* is not in this tree")


@pytest.fixture(scope="module")
def blobs():
    return {n.split(".", 1)[1].split(".")[0]: (EXAMPLES / n).read_text(encoding="utf-8") for n in FILES}


def test_all_files_are_there():
    missing = [n for n in FILES if not (EXAMPLES / n).is_file()]
    assert not missing, missing


def test_toy_examples_validate(tmp_path):
    """Only the toy files are checked, so the result does not depend on other examples next to them."""
    for n in FILES:
        shutil.copy(EXAMPLES / n, tmp_path / n)
    rep = load_validator().check_examples(tmp_path)
    assert rep.errors == []
    assert rep.stats.get("campaign.designs", 0) >= 10
    assert rep.stats.get("inspect.designs", 0) >= 10
    assert rep.stats.get("homolog.orthologs", 0) >= 2 and rep.stats.get("homolog.paralogs", 0) >= 1
    assert rep.stats.get("profile.text_keys", 0) >= 10


def test_toy_examples_hang_together(blobs):
    campaign, inspect, manifest = json.loads(blobs["campaign"]), json.loads(blobs["inspect"]), json.loads(blobs["manifest"])
    profile = json.loads(blobs["profile"])
    events = [json.loads(x) for x in blobs["reviews"].splitlines() if x.strip()]
    ids = {d["id"] for d in campaign["designs"]}
    fams = {(d["run"], d["fam"]) for d in campaign["designs"]}
    assert set(inspect["designs"]) <= ids and inspect["designs"]
    assert {d["run"] for d in campaign["designs"]} == {"run01", "run02"}
    assert profile["id"] == "toy" and manifest["profile"]["id"] == "toy" and manifest["target"]["uniprot"] == "Z0TOY1"
    assert events and {e["bundle"] for e in events} == {manifest["bundle"]["id"]}
    for e in events:
        known = (e["run"], e["design"]) in fams if e["unit"] == "family" else e["design"] in ids
        assert known, e
        assert all(s in ids for s in e["sequences"]), e
    assert {e["decision"] for e in events} >= {"keep", "maybe", "reject", "clear"}


def test_toy_examples_carry_no_local_details(blobs):
    local = re.compile(r"/Users/|/home/|/private/|/tmp/|/var/folders|[A-Za-z]:\\\\|\\\\\\\\")
    for name, text in blobs.items():
        assert not local.search(text), name
    manifest = json.loads(blobs["manifest"])
    assert manifest["tool"]["git_commit"] is None and manifest["tool"]["libraries"] == {}
    assert manifest["created"].startswith("2026-01-01")
    assert all(not Path(s["path"]).is_absolute() for s in manifest["sources"])


@pytest.mark.skipif(os.environ.get("BINDERSCOPE_CHECK_EXAMPLES") != "1", reason="set BINDERSCOPE_CHECK_EXAMPLES=1 to regenerate and compare")
def test_toy_examples_are_what_the_generator_writes():
    script = Path(__file__).resolve().parent / "toydata" / "make_schema_examples.py"
    done = subprocess.run([sys.executable, str(script), "--check", "--out", str(EXAMPLES)], capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, (done.stdout + done.stderr)[-1500:]
