"""The viewer's decision log, run under Node rather than a browser.

decisions_carry.mjs loads viewer/js/bs.js in a vm context with the few DOM
things it touches at load, and exercises the part that cannot be checked from
Python: which other logs in this browser belong to the review on screen.

Needs Node 22 or later; skipped otherwise, which is the case on the workstation
(Chrome is installed there, Node is not), so run it where Node is.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from toydata import appkit

HERE = Path(__file__).resolve().parent
NODE = appkit.NODE

pytestmark = pytest.mark.skipif(not NODE, reason="needs Node 22 or later")


def test_the_carry_over_rule_holds_in_every_case():
    r = subprocess.run([NODE, str(HERE / "decisions_carry.mjs")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = [l for l in r.stdout.splitlines() if l.startswith(("ok ", "FAIL"))]
    assert lines and all(l.startswith("ok ") for l in lines), r.stdout
    assert len(lines) >= 11, r.stdout            # every case ran, not an early exit that printed a few
