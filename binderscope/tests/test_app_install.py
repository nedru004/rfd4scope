"""The installer of the Binderscope app (install.sh and the double-click wrapper "Install Binderscope.command").

Nothing here runs pip or touches the real home: the installer runs against a made-up source folder, a stand-in for Python 3.12 that only
answers its questions and records the calls, and stub programs for curl, open and xcode-select (so a Binderscope that runs on this
computer is never asked to quit and no window opens). HOME, the program folder and the app folder are temporary folders.
The app creation itself is macOS only; on other systems the installer says so and the tests check that.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from toydata import appkit
from toydata.testing import repo_root

from binder_review import cli
from binder_review.app import DEFAULT_PORT

ROOT = repo_root()
INSTALL = next((p for p in (ROOT / "install.sh", ROOT / "public" / "install.sh") if p.is_file()), None)
COMMAND = next((p for p in (ROOT / "Install Binderscope.command", ROOT / "public" / "Install Binderscope.command") if p.is_file()), None)
ICON = ROOT / "src" / "binder_review" / "app" / "static" / "binderscope-icon.png"
DARWIN = sys.platform == "darwin"

pytestmark = pytest.mark.skipif(INSTALL is None or COMMAND is None or shutil.which("bash") is None, reason="install.sh and the .command wrapper are not in this tree")

ANSI = re.compile(r"\x1b\[[0-9;]*m")

FAKE_PYTHON = r'''#!/bin/bash
# A stand-in for Python 3.12 or newer: answers what install.sh asks, makes "environments" and records the pip calls.
log() { printf '%s\n' "$*" >> "$FAKE_LOG"; }
case "$1" in
  -c) case "$2" in
        *"version_info >= (3, 12)"*) exit 0;;
        *) echo "3.14.0"; exit 0;;
      esac;;
  -m) case "$2" in
        venv) mkdir -p "$3/bin" && cp "$0" "$3/bin/python" && chmod +x "$3/bin/python"; log "venv $3"; exit 0;;
        pip) log "pip ${*:3}"
             if [ -n "${FAKE_PIP_FAIL:-}" ] && case " $* " in *" -e "*) true;; *) false;; esac; then echo "ERROR: could not build a wheel (made-up)" >&2; exit 1; fi
             exit 0;;
        binder_review) echo "binder-review 9.9.9"; exit 0;;
      esac;;
esac
log "unexpected call: $*"
exit 3
'''


def make_stub(folder: Path, name: str, body: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)
    return path


def clean(text: str) -> str:
    return ANSI.sub("", text)


class Box:
    """A made-up computer: HOME, a source folder to install from, the stubs, and a way to run install.sh."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "my home"                                                            # spaces everywhere, as in ~/Library/Application Support
        self.home.mkdir()
        self.prefix = root / "Application Support" / "Binderscope"
        self.apps = root / "my apps"
        self.log = root / "fake.log"
        self.stubs = root / "stubs"
        self.calls = root / "calls.log"
        make_stub(self.stubs, "xcode-select", "exit 0")                                         # the Command Line Tools "are installed"
        make_stub(self.stubs, "open", f'printf "open %s\\n" "$*" >> "{self.calls}"')
        make_stub(self.stubs, "curl", f'for a in "$@"; do printf "%s\\n" "$a" >> "{self.calls}.curl"; done; printf -- "--end\\n" >> "{self.calls}.curl"; exit "${{FAKE_CURL_EXIT:-7}}"')
        self.python = make_stub(root / "fakepy", "python3.14", FAKE_PYTHON.split("\n", 1)[1])
        self.src = root / "source"
        self._make_source()

    def _make_source(self) -> None:
        src = self.src
        (src / "src" / "binder_review" / "app" / "static").mkdir(parents=True)
        (src / "viewer" / "pages").mkdir(parents=True)
        (src / "docs" / "schema").mkdir(parents=True)                                           # the installer needs the schemas too: every build is validated against them
        (src / "docs" / "schema" / "campaign.schema.json").write_text("{}")
        (src / "pyproject.toml").write_text("[project]\nname = 'binder-review'\nversion = '9.9.9'\n")
        (src / "src" / "binder_review" / "__init__.py").write_text("")
        (src / "viewer" / "pages" / "index.html").write_text("<html></html>")
        shutil.copy(ICON, src / "src" / "binder_review" / "app" / "static" / "binderscope-icon.png")
        shutil.copy(INSTALL, src / "install.sh")
        shutil.copy(COMMAND, src / "Install Binderscope.command")
        for junk in ("tests", "work", "bundles", "data", ".venv", ".git", "src/binder_review/__pycache__"):
            (src / junk).mkdir(parents=True, exist_ok=True)
            (src / junk / "junk.txt").write_text("not for the program folder")
        (src / ".DS_Store").write_text("x")
        (src / "src" / "binder_review" / ".pytest_cache").mkdir()

    def env(self, **extra: str) -> dict[str, str]:
        path = os.pathsep.join([str(self.stubs), str(self.home / ".local" / "bin"), os.environ.get("PATH", "")])
        return {**os.environ, "HOME": str(self.home), "PATH": path, "FAKE_LOG": str(self.log), **extra}

    def run(self, *args: str, script: Path | None = None, env: dict | None = None, cwd: Path | None = None, timeout: float = 120.0) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(script or self.src / "install.sh"), *args], capture_output=True, text=True, env=env or self.env(), cwd=str(cwd or self.root),
                              stdin=subprocess.DEVNULL, timeout=timeout)

    def install(self, *extra: str, **env: str) -> subprocess.CompletedProcess:
        return self.run("--python", str(self.python), "--prefix", str(self.prefix), "--app-dir", str(self.apps), "--no-open", "-y", *extra, env=self.env(**env))

    def fake_calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.is_file() else []

    def curl_calls(self) -> list[list[str]]:
        f = Path(str(self.calls) + ".curl")
        calls, cur = [], []
        for line in (f.read_text().splitlines() if f.is_file() else []):
            if line == "--end":
                calls.append(cur)
                cur = []
            else:
                cur.append(line)
        return calls


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


# ---------------------------------------------------------------------------------------------------------------- the files themselves
def test_the_scripts_are_valid_bash_and_can_be_started_by_a_double_click():
    for path in (INSTALL, COMMAND):
        out = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert out.returncode == 0, (path.name, out.stderr)
        assert path.read_text(encoding="utf-8").startswith("#!/bin/bash\n") and os.access(path, os.X_OK), path.name       # a .command opens only when it can be run
    assert INSTALL.name == "install.sh" and COMMAND.name == "Install Binderscope.command"


def test_the_installer_promises_no_administrator_rights_and_removes_only_its_own_folders():
    text = INSTALL.read_text(encoding="utf-8")
    assert "set -u" in text
    # It never RUNS sudo. It may name it: with no Python 3.12 and no root, the
    # way out is a package the user installs themselves, and a message that
    # cannot say "sudo dnf install python3.12" is a dead end. So the check is on
    # sudo in a command position, not on the word anywhere in the file.
    for n, line in enumerate(text.splitlines(), 1):
        if "sudo" in line:
            assert not re.match(r"\s*sudo\b", line), (n, line)
            assert not re.search(r"(?:^|[;&|]|\$\(|`)\s*sudo\b", line), (n, line)
    targets = []
    for line in text.splitlines():
        if "rm -rf" in line:
            arg = line.split("rm -rf", 1)[1].strip()
            targets.append(re.split(r"\s*(?:&&|;|\|\|)", arg)[0].strip())
    assert targets and set(targets) <= {'"$APP"', '"$PREFIX"', '"$VENV"', '"$(dirname "$IS")"'}, targets          # the app, the program folder, the environment, a scratch folder
    assert re.search(r"never needs administrator rights", text) and "~/Binderscope" in text


def test_the_installer_agrees_with_the_app_about_port_icon_and_command_line():
    text = INSTALL.read_text(encoding="utf-8")
    assert f"127.0.0.1:{DEFAULT_PORT}/api/quit" in text                                       # the uninstaller asks a running app to quit on its fixed port
    m = re.search(r'binder_review (app [^>]*?)(?: "\\\$@")?\s*>>', text)
    assert m, "the launcher line of the app was not found"
    args = cli.build_parser().parse_args(m.group(1).split())
    assert args.command == "app" and args.window == "auto" and args.quit_when_closed is True        # the options the launcher gives exist
    assert 'static/binderscope-icon.png' in text and ICON.is_file()
    with ICON.open("rb") as fh:
        head = fh.read(24)
    width, height = struct.unpack(">II", head[16:24])
    assert head[:8] == b"\x89PNG\r\n\x1a\n" and width == height >= 512                          # large enough for the 512 pt icon at 2x


# ---------------------------------------------------------------------------------------------------------------- help and refusals
def test_help_lists_every_option_and_changes_nothing(box):
    r = box.run("--help")
    assert r.returncode == 0 and r.stdout.startswith("Binderscope installer")
    flags = set()
    for alt in re.findall(r"^\s+(-[\w-]+(?:\|-[\w-]+)*)\)", INSTALL.read_text(encoding="utf-8"), re.M):
        flags |= set(alt.split("|"))
    assert {"--prefix", "--app-dir", "--python", "--dev", "--no-app", "--no-link", "--no-open", "--uninstall", "-y", "-h", "--help"} <= flags
    for flag in flags - {"-h", "--help", "--yes"}:
        assert flag in r.stdout, flag
    assert "never needs administrator rights" in r.stdout and not list(box.home.iterdir()) and not box.prefix.exists() and not box.apps.exists()
    assert box.run("-h").stdout == r.stdout


def test_the_wrapper_runs_install_sh_from_its_own_folder_and_passes_the_status_on(box):
    wrapper = box.src / "Install Binderscope.command"
    r = box.run("--help", script=wrapper, cwd=box.home)                                       # started from another folder, as a double click does
    assert r.returncode == 0 and r.stdout.startswith("Binderscope installer")
    r = box.run("--bogus", script=wrapper, cwd=box.home)
    assert r.returncode == 1 and "unknown option --bogus" in clean(r.stderr)


def test_wrong_options_and_wrong_places_stop_with_one_message(box, tmp_path):
    r = box.run("--bogus")
    assert r.returncode == 1 and "unknown option --bogus" in clean(r.stderr) and "--help" in clean(r.stderr)
    lone = tmp_path / "lone"
    lone.mkdir()
    shutil.copy(INSTALL, lone / "install.sh")
    r = box.run("--no-open", "-y", script=lone / "install.sh")
    err = clean(r.stderr)
    assert r.returncode == 1 and "this folder is incomplete. Missing: pyproject.toml src/binder_review viewer/pages docs/schema" in err
    assert "It holds: install.sh" in err and "open the folder that holds install.sh, pyproject.toml, src, viewer and docs" in err
    r = box.run("--python", "/usr/bin/false", "--prefix", str(box.prefix), "--app-dir", str(box.apps), "--no-open", "-y")
    assert r.returncode == 1 and "/usr/bin/false is not Python 3.12 or newer" in clean(r.stderr)
    assert not box.prefix.exists() and not box.apps.exists() and not list(box.home.iterdir())


# ---------------------------------------------------------------------------------------------------------------- uninstall
def fake_installation(box: Box) -> dict[str, Path]:
    """What an installation leaves behind, and what must survive an uninstall."""
    (box.prefix / "venv" / "bin").mkdir(parents=True)
    (box.prefix / "app" / "src").mkdir(parents=True)
    (box.prefix / "bin").mkdir()
    (box.prefix / "bin" / "binderscope").write_text("#!/bin/bash\n")
    (box.prefix / "install.log").write_text("log")
    app = box.apps / "Binderscope.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "Binderscope").write_text("#!/bin/bash\n")
    other = box.apps / "Other.app"
    (other / "Contents").mkdir(parents=True)
    link = box.home / ".local" / "bin" / "binderscope"
    link.parent.mkdir(parents=True)
    link.symlink_to(box.prefix / "bin" / "binderscope")
    project = box.home / "Binderscope" / "my-project"
    project.mkdir(parents=True)
    (project / "project.app.json").write_text("{}")
    return {"app": app, "other": other, "link": link, "project": project}


@pytest.mark.parametrize("curl_exit", ["7", "0"])
def test_uninstall_removes_the_app_and_its_environment_but_keeps_the_projects(box, curl_exit):
    kept = fake_installation(box)
    r = box.run("--uninstall", "--prefix", str(box.prefix), "--app-dir", str(box.apps), env=box.env(FAKE_CURL_EXIT=curl_exit))
    out = clean(r.stdout)
    assert r.returncode == 0 and not kept["app"].exists() and not box.prefix.exists() and not kept["link"].is_symlink() and not kept["link"].exists()
    assert kept["other"].is_dir() and (kept["project"] / "project.app.json").is_file() and (box.home / ".local" / "bin").is_dir()       # nothing else is touched
    assert f"removed {kept['app']}" in out and f"removed {box.prefix}" in out and f"Your projects are still in {box.home}/Binderscope" in out
    assert ("stopped the running app" in out) == (curl_exit == "0")
    calls = box.curl_calls()
    assert len(calls) == 1 and calls[0][-1] == f"http://127.0.0.1:{DEFAULT_PORT}/api/quit" and "X-Binderscope: 1" in calls[0] and calls[0][calls[0].index("-X") + 1] == "POST"
    again = box.run("--uninstall", "--prefix", str(box.prefix), "--app-dir", str(box.apps))                    # nothing left: still fine
    assert again.returncode == 0 and (kept["project"] / "project.app.json").is_file()


def test_uninstall_leaves_a_binderscope_command_that_is_not_its_link(box):
    link = box.home / ".local" / "bin" / "binderscope"
    link.parent.mkdir(parents=True)
    link.write_text("#!/bin/bash\necho mine\n")                                                              # the user's own script, not a symbolic link
    r = box.run("--uninstall", "--prefix", str(box.prefix), "--app-dir", str(box.apps))
    assert r.returncode == 0 and link.read_text() == "#!/bin/bash\necho mine\n"


@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
def test_the_quit_request_of_the_uninstaller_is_accepted_by_the_app(box, tmp_path):
    """What install.sh --uninstall sends to a running app, replayed against a test server on another port."""
    box.run("--uninstall", "--prefix", str(box.prefix), "--app-dir", str(box.apps))
    args = box.curl_calls()[0]
    with appkit.running(tmp_path / "h") as (srv, api):
        url = args[-1].replace(f":{DEFAULT_PORT}/", f":{srv.port}/")
        r = subprocess.run(["curl", *args[:-1], url], capture_output=True, text=True, timeout=20)
        assert r.returncode == 0 and json.loads(r.stdout) == {"ok": True} and r.stderr == ""                 # not the 403 of a refused request
        appkit.wait_until(lambda: not srv.thread.is_alive(), timeout=5, what="the app to quit after the request")


# ---------------------------------------------------------------------------------------------------------------- install (no pip, no network)
def test_install_copies_the_program_makes_the_command_and_the_app(box):
    r = box.install()
    out = clean(r.stdout)
    assert r.returncode == 0, out + r.stderr
    assert "Binderscope 9.9.9 is installed" in out and f"Python 3.14.0 at {box.python}" in out
    app_src = box.prefix / "app"
    assert (app_src / "pyproject.toml").is_file() and (app_src / "src" / "binder_review" / "__init__.py").is_file() and (app_src / "viewer" / "pages" / "index.html").is_file()
    assert (app_src / "src" / "binder_review" / "app" / "static" / "binderscope-icon.png").is_file()
    if shutil.which("rsync"):                                                                                # without rsync everything is copied
        for junk in ("tests", "work", "bundles", "data", ".venv", ".git", ".DS_Store", "install.sh", "Install Binderscope.command", "src/binder_review/__pycache__",
                     "src/binder_review/.pytest_cache"):
            assert not (app_src / junk).exists(), junk
    venv = box.prefix / "venv"
    assert (venv / "bin" / "python").is_file()
    calls = box.fake_calls()
    assert calls[0] == f"venv {venv}" and calls[1] == "pip install --quiet --upgrade pip" and calls[2] == f"pip install --quiet -e {app_src}" and len(calls) == 3
    command = box.prefix / "bin" / "binderscope"
    assert os.access(command, os.X_OK) and command.read_text() == f'#!/bin/bash\nexec "{venv}/bin/python" -m binder_review "$@"\n'
    link = box.home / ".local" / "bin" / "binderscope"
    assert link.is_symlink() and link.resolve() == command.resolve() and "command: binderscope" in out                       # ~/.local/bin is on the PATH here
    assert (box.prefix / "install.log").is_file() and not (box.prefix / "install.log.status").exists()
    assert not Path(str(box.calls)).exists()                                                                 # --no-open: nothing was opened
    app = box.apps / "Binderscope.app"
    if not DARWIN:
        assert "this is not macOS" in out and not box.apps.exists()                                          # the command is installed, the app is not
        return
    launcher = app / "Contents" / "MacOS" / "Binderscope"
    assert os.access(launcher, os.X_OK) and subprocess.run(["bash", "-n", str(launcher)]).returncode == 0
    text = launcher.read_text()
    assert f'exec "{venv}/bin/python" -m binder_review app --window auto --quit-when-closed "$@" >>"{box.home}/Library/Logs/Binderscope.log" 2>&1' in text
    assert 'export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"' in text
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert (info["CFBundleName"], info["CFBundleExecutable"], info["CFBundleShortVersionString"], info["CFBundleVersion"], info["CFBundlePackageType"]) == (
        "Binderscope", "Binderscope", "9.9.9", "9.9.9", "APPL")
    assert info["LSUIElement"] is True and info["CFBundleIconFile"] == "Binderscope" and info["CFBundleIdentifier"] == "app.binderscope.launcher"
    if shutil.which("iconutil") and shutil.which("sips"):
        icns = app / "Contents" / "Resources" / "Binderscope.icns"
        assert icns.is_file() and icns.read_bytes()[:4] == b"icns"
    assert f"app: {app}" in out


@pytest.mark.skipif(not DARWIN, reason="the app is created on macOS only")
def test_the_launcher_the_installer_wrote_starts_the_app_and_logs_to_the_library(box, tmp_path):
    """Install with the stand-in Python, then let the launcher run the real one: the command line it was written with must start the server."""
    assert box.install().returncode == 0
    venv_python = box.prefix / "venv" / "bin" / "python"
    venv_python.write_text(f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')                         # the environment's Python is this one
    launcher = box.apps / "Binderscope.app" / "Contents" / "MacOS" / "Binderscope"
    port = appkit.free_port()
    home = tmp_path / "projects"
    proc = subprocess.Popen(["bash", str(launcher), "--port", str(port), "--window", "none", "--home", str(home)], env=box.env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        api = appkit.Client(port)

        def answers() -> bool:
            try:
                return api.get("/api/ping", timeout=2).status == 200
            except OSError:
                return False

        appkit.wait_until(answers, timeout=30, what="the app started by the launcher")
        assert api.get("/api/state").json["home"] == str(home.resolve())                             # the arguments after the launcher reach the app
        assert api.post("/api/quit").json == {"ok": True}
        assert proc.wait(15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    log = (box.home / "Library" / "Logs" / "Binderscope.log").read_text()
    assert f"is running at http://127.0.0.1:{port}/" in log and f"projects in {home.resolve()}" in log


def test_installing_again_updates_in_place_and_the_uninstall_then_removes_it_all(box):
    assert box.install().returncode == 0
    (box.prefix / "app" / "stale.txt").write_text("from an older version")
    app = box.apps / "Binderscope.app"
    if DARWIN:
        (app / "Contents" / "stale.txt").write_text("old")
    assert box.install().returncode == 0
    calls = box.fake_calls()
    assert sum(c.startswith("venv ") for c in calls) == 1 and sum("-e" in c.split() for c in calls) == 2                  # the environment is kept, the program installed again
    if shutil.which("rsync"):
        assert not (box.prefix / "app" / "stale.txt").exists()                                              # the copy mirrors the source
    assert not (app / "Contents" / "stale.txt").exists()
    project = box.home / "Binderscope" / "p"
    project.mkdir(parents=True)
    (project / "project.app.json").write_text("{}")
    r = box.run("--uninstall", "--prefix", str(box.prefix), "--app-dir", str(box.apps))
    assert r.returncode == 0 and not box.prefix.exists() and not app.exists() and not (box.home / ".local" / "bin" / "binderscope").is_symlink()
    assert (project / "project.app.json").is_file()


def test_dev_mode_uses_the_source_folder_in_place_and_the_options_switch_parts_off(box):
    r = box.install("--dev", "--no-app", "--no-link")
    out = clean(r.stdout)
    assert r.returncode == 0, out + r.stderr
    assert (box.src / ".venv" / "bin" / "python").is_file() and not (box.prefix / "app").exists() and not box.apps.exists()
    assert box.fake_calls()[-1] == f"pip install --quiet -e {box.src}"
    assert not (box.home / ".local" / "bin" / "binderscope").exists() and "using this folder in place" in out
    assert (box.prefix / "bin" / "binderscope").read_text().splitlines()[1] == f'exec "{box.src}/.venv/bin/python" -m binder_review "$@"'


def test_a_failing_pip_stops_the_installer_with_the_log_and_a_hint(box):
    r = box.install(FAKE_PIP_FAIL="1")
    out, err = clean(r.stdout), clean(r.stderr)
    assert r.returncode == 1 and "could not build a wheel" in out and "installing the libraries failed" in err and str(box.prefix / "install.log") in err
    assert "xcode-select --install" in err and not (box.apps / "Binderscope.app").exists() and not (box.prefix / "bin").exists()


def test_a_python_that_does_not_start_binderscope_is_reported(box):
    make_stub(box.root / "fakepy2", "python3.14", FAKE_PYTHON.split("\n", 1)[1].replace('echo "binder-review 9.9.9"; exit 0', 'exit 1'))
    r = box.run("--python", str(box.root / "fakepy2" / "python3.14"), "--prefix", str(box.prefix), "--app-dir", str(box.apps), "--no-open", "-y", env=box.env())
    assert r.returncode == 1 and "was installed but does not start" in clean(r.stderr)
