"""Native file and folder dialogs (macOS, through AppleScript). Elsewhere, or when the dialog cannot open, the page falls back to its own browser."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def available() -> bool:
    return sys.platform == "darwin" and shutil.which("osascript") is not None


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


_JXA = """
ObjC.import('AppKit');
var app = $.NSApplication.sharedApplication;
app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
app.activateIgnoringOtherApps(true);
var panel = $.NSOpenPanel.openPanel;
panel.canChooseFiles = %(files)s;
panel.canChooseDirectories = %(dirs)s;
panel.allowsMultipleSelection = %(multi)s;
panel.message = %(prompt)s;
panel.prompt = "Choose";
%(where)s
var r = panel.runModal();
var out = [];
if (r == $.NSModalResponseOK || r == 1) { var urls = panel.URLs; for (var i = 0; i < urls.count; i++) out.push(ObjC.unwrap(urls.objectAtIndex(i).path)); }
out.join("\\n");
"""


def pick(kind: str, prompt: str, multiple: bool = False, default: Optional[str] = None, timeout: float = 900.0) -> Optional[list[str]]:
    """The chosen paths, or None when the person cancelled. Raises RuntimeError when no native dialog can be shown.

    The panel is an NSOpenPanel run by a small JXA script that makes ``osascript`` the active application first, so the panel opens in front of
    the browser window instead of behind it (a plain AppleScript ``choose folder`` can stay hidden when it is started from a background process).
    """
    import json

    if not available():
        raise RuntimeError("no native dialog on this system")
    where = ""
    if default and Path(default).expanduser().is_dir():
        where = "panel.directoryURL = $.NSURL.fileURLWithPath(%s);" % json.dumps(str(Path(default).expanduser()))
    script = _JXA % {"files": "true" if kind == "file" else "false", "dirs": "false" if kind == "file" else "true", "multi": "true" if multiple else "false",
                     "prompt": json.dumps(prompt), "where": where}
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "the dialog failed")
    paths = [p.rstrip("/") or "/" for p in r.stdout.splitlines() if p.strip()]
    return paths or None


def reveal(path: Path) -> None:
    """Show a file or folder in the Finder (macOS) or open it with the system opener."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R" if path.is_file() else "", str(path)] if path.is_file() else ["open", str(path)])
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
