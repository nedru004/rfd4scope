#!/bin/bash
# Binderscope installer. macOS gets the app; on Linux it installs the same
# program and the `binderscope` command, and makes no app.
#
#   ./install.sh               install or update: tools check, private Python environment, the Binderscope app in ~/Applications
#   ./install.sh --uninstall   remove the app and its environment (your projects in ~/Binderscope are kept)
#
# Options: --prefix DIR   where the program lives     (default ~/Library/Application Support/Binderscope
#                         on macOS, $XDG_DATA_HOME/binderscope -- usually ~/.local/share -- elsewhere)
#          --app-dir DIR  where Binderscope.app goes  (default ~/Applications)
#          --python PATH  Python 3.12 or newer to use (default: found automatically)
#          --dev          use this folder in place (editable install, .venv here) instead of a copy
#          --no-app       do not create the app, --no-link do not add a binderscope command to ~/.local/bin,
#          --no-open      do not start the app at the end, -y answer yes to questions
#
# It never needs administrator rights and never writes outside the folders above.
set -u

PREFIX=""                      # filled in once the system is known; --prefix wins
APP_DIR="${HOME}/Applications"
PY=""
DEV=0; MAKE_APP=1; OPEN=1; YES=0; UNINSTALL=0; LINK=1

say()  { printf '%s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mThe installation stopped:\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { # ask "question" -> 0 for yes
  [ "$YES" = 1 ] && return 0
  printf '  %s [Y/n] ' "$1"; read -r a || a=""; case "$a" in [nN]*) return 1;; *) return 0;; esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2;;
    --app-dir) APP_DIR="$2"; shift 2;;
    --python) PY="$2"; shift 2;;
    --dev) DEV=1; shift;;
    --no-app) MAKE_APP=0; shift;;
    --no-link) LINK=0; shift;;
    --no-open) OPEN=0; shift;;
    -y|--yes) YES=1; shift;;
    --uninstall) UNINSTALL=1; shift;;
    # The whole comment header, however long it is: a fixed line range silently
    # stopped printing the last line of it the first time the header grew.
    -h|--help) sed -n '2,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0;;
    *) die "unknown option $1 (see ./install.sh --help)";;
  esac
done

SRC="$(cd "$(dirname "$0")" && pwd)"
OS="$(uname -s)"
# "~/Library/Application Support" is a macOS convention and means nothing on
# Linux, where a program that makes that folder has simply left litter in the
# home directory. Elsewhere, keep to the XDG data directory.
if [ -z "$PREFIX" ]; then
  if [ "$OS" = "Darwin" ]; then PREFIX="${HOME}/Library/Application Support/Binderscope"
  else PREFIX="${XDG_DATA_HOME:-${HOME}/.local/share}/binderscope"; fi
fi
APP="${APP_DIR}/Binderscope.app"
BIN_LINK="${HOME}/.local/bin/binderscope"

if [ "$UNINSTALL" = 1 ]; then
  step "Removing Binderscope"
  curl -s --noproxy '*' -m 2 -X POST -H "X-Binderscope: 1" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:51731/api/quit >/dev/null 2>&1 && ok "stopped the running app"
  [ -d "$APP" ] && rm -rf "$APP" && ok "removed $APP"
  [ -L "$BIN_LINK" ] && rm -f "$BIN_LINK" && ok "removed $BIN_LINK"
  if [ -d "$PREFIX" ]; then rm -rf "$PREFIX" && ok "removed $PREFIX"; fi
  say; say "Your projects are still in ${HOME}/Binderscope. Delete that folder yourself if you no longer need them."
  exit 0
fi

say "Binderscope installer"
say "Installing from: $SRC"
MISSING=""
[ -f "$SRC/pyproject.toml" ]        || MISSING="$MISSING pyproject.toml"
[ -d "$SRC/src/binder_review" ]     || MISSING="$MISSING src/binder_review"
[ -d "$SRC/viewer/pages" ]          || MISSING="$MISSING viewer/pages"
[ -d "$SRC/docs/schema" ]           || MISSING="$MISSING docs/schema"
if [ -n "$MISSING" ]; then
  printf '\n\033[31mThe installation stopped:\033[0m this folder is incomplete. Missing:%s\n' "$MISSING" >&2
  printf '  It holds: %s\n' "$(ls -A "$SRC" | tr '\n' ' ')" >&2
  if ls -A "$SRC" "$SRC"/* 2>/dev/null | grep -q '\.icloud$'; then
    printf '  Some files are still in iCloud and not on this machine yet: right-click the folder in Finder and choose Download Now, then run this again.\n' >&2
  fi
  printf '  Copy the whole Binderscope folder again (one archive, not file by file), open the folder that holds install.sh, pyproject.toml, src, viewer and docs, and run ./install.sh there.\n' >&2
  exit 1
fi

# ---------------------------------------------------------------- 1. tools
WHERE="this Mac"; [ "$OS" = "Darwin" ] || WHERE="this machine"
step "1 of 5  Checking $WHERE"
if [ "$OS" = "Darwin" ]; then
  if ! xcode-select -p >/dev/null 2>&1; then
    say "  Binderscope needs Apple's Command Line Tools once (a small download, a few minutes)."
    say "  A system window is opening. Click Install in it, wait until it finishes, then run this installer again."
    xcode-select --install >/dev/null 2>&1
    exit 1
  fi
  ok "Command Line Tools found"
else
  warn "this is not macOS: the command line is installed, the app is not created"; MAKE_APP=0
fi

py_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; }
find_python() {
  [ -n "$PY" ] && { py_ok "$PY" && return 0; die "$PY is not Python 3.12 or newer."; }
  for c in python3.14 python3.13 python3.12 /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.14 /usr/local/bin/python3.13 /usr/local/bin/python3.12 python3; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    py_ok "$p" && { PY="$p"; return 0; }
  done
  return 1
}
if ! find_python; then
  warn "no Python 3.12 or newer found"
  if command -v brew >/dev/null 2>&1; then
    if ask "Install Python 3.14 with Homebrew now?"; then
      brew install python@3.14 || die "Homebrew could not install Python. Run: brew install python@3.14"
      find_python || die "Python 3.14 was installed but is not on the path. Open a new Terminal window and run this installer again."
    else die "Python 3.12 or newer is needed."; fi
  elif command -v uv >/dev/null 2>&1; then
    # uv can fetch a Python of its own into the home folder, which is the way
    # through on a managed machine where nobody has root.
    if ask "Install Python 3.12 with uv now?"; then
      uv python install 3.12 >>"${LOG:-/dev/null}" 2>&1 || die "uv could not install Python. Run: uv python install 3.12"
      PY="$(uv python find 3.12 2>/dev/null)"
      [ -n "$PY" ] && py_ok "$PY" || die "uv installed Python but it could not be used. Run: uv python find 3.12"
    else die "Python 3.12 or newer is needed."; fi
  elif [ "$OS" = "Darwin" ]; then
    die "Python 3.12 or newer is needed. Install it from https://www.python.org/downloads/ (the macOS installer), or with Homebrew (brew install python@3.14), then run this installer again."
  else
    HAVE="$(python3 -V 2>&1 | awk '{print $2}')"
    die "Python 3.12 or newer is needed${HAVE:+, and the python3 here is $HAVE}.

  With root:     RHEL, Rocky, Fedora:  sudo dnf install python3.12
  With root:     Debian, Ubuntu:       sudo apt install python3.12
  Without root:  pip install --user uv && uv python install 3.12
                 then: ./install.sh --python \"\$(uv python find 3.12)\"

  Then run this installer again."
  fi
fi
ok "Python $("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') at $PY"

# ---------------------------------------------------------------- 2. program files
# an update must not leave the old program running: ask a running app to quit, so the next start uses the new files
if curl -s --noproxy '*' -m 2 -X POST -H "X-Binderscope: 1" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:51731/api/quit >/dev/null 2>&1; then
  sleep 1; say "  Stopped the running Binderscope so the update takes effect."
fi
step "2 of 5  Copying Binderscope"
if [ "$DEV" = 1 ]; then
  APP_SRC="$SRC"; VENV="$SRC/.venv"
  ok "using this folder in place ($SRC)"
else
  APP_SRC="$PREFIX/app"; VENV="$PREFIX/venv"
  mkdir -p "$APP_SRC" || die "cannot create $APP_SRC"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude='.git' --exclude='.venv' --exclude='/work' --exclude='/bundles' --exclude='/data' --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.DS_Store' --exclude='/tests' --exclude='/install.sh' --exclude='/Install Binderscope.command' "$SRC/" "$APP_SRC/" || die "could not copy the program files."
  else
    cp -R "$SRC/." "$APP_SRC/" || die "could not copy the program files."
  fi
  ok "program files in $APP_SRC"
fi

# ---------------------------------------------------------------- 3. environment
step "3 of 5  Setting up Python (a few minutes the first time)"
mkdir -p "$PREFIX"
LOG="$PREFIX/install.log"; : > "$LOG"
if [ -x "$VENV/bin/python" ] && ! py_ok "$VENV/bin/python"; then rm -rf "$VENV"; fi
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV" >>"$LOG" 2>&1 || { tail -n 12 "$LOG"; die "could not create the Python environment (details in $LOG)."; }
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip >>"$LOG" 2>&1 || warn "could not update pip (continuing)"
say "  Downloading and installing the scientific libraries from PyPI. One of them is compiled here, so this can take a few minutes."
( "$VENV/bin/python" -m pip install --quiet -e "$APP_SRC" >>"$LOG" 2>&1; echo $? > "$LOG.status" ) &
pid=$!
n=0; while kill -0 "$pid" 2>/dev/null; do printf '.'; n=$((n+1)); sleep 3; done; printf '\n'
wait "$pid" 2>/dev/null
if [ "$(cat "$LOG.status" 2>/dev/null)" != "0" ]; then
  tail -n 15 "$LOG"; rm -f "$LOG.status"
  die "installing the libraries failed (the full log is $LOG). If it mentions a compiler, run: xcode-select --install"
fi
rm -f "$LOG.status"
VERSION="$("$VENV/bin/python" -m binder_review --version 2>/dev/null | awk '{print $NF}')"
[ -n "$VERSION" ] || die "Binderscope was installed but does not start (see $LOG)."
ok "Binderscope $VERSION is installed"

# ---------------------------------------------------------------- 4. app and command
step "4 of 5  Creating the app"
mkdir -p "$PREFIX/bin"
cat > "$PREFIX/bin/binderscope" <<SH
#!/bin/bash
exec "$VENV/bin/python" -m binder_review "\$@"
SH
chmod +x "$PREFIX/bin/binderscope"
if [ "$LINK" = 1 ] && case ":$PATH:" in *":${HOME}/.local/bin:"*) true;; *) false;; esac; then
  mkdir -p "${HOME}/.local/bin" && ln -sf "$PREFIX/bin/binderscope" "$BIN_LINK" && ok "command: binderscope (for example: binderscope app, binderscope --help)"
else
  ok "command: $PREFIX/bin/binderscope"
fi

if [ "$MAKE_APP" = 1 ]; then
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" || die "cannot create $APP"
  cat > "$APP/Contents/MacOS/Binderscope" <<SH
#!/bin/bash
# Starts the Binderscope app (a local server and its window). Projects are kept in ~/Binderscope.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
mkdir -p "$HOME/Library/Logs"
exec "$VENV/bin/python" -m binder_review app --window auto --quit-when-closed "\$@" >>"$HOME/Library/Logs/Binderscope.log" 2>&1
SH
  chmod +x "$APP/Contents/MacOS/Binderscope"
  cat > "$APP/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Binderscope</string>
  <key>CFBundleDisplayName</key><string>Binderscope</string>
  <key>CFBundleIdentifier</key><string>app.binderscope.launcher</string>
  <key>CFBundleExecutable</key><string>Binderscope</string>
  <key>CFBundleIconFile</key><string>Binderscope</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSUIElement</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
</dict></plist>
PL
  ICON="$APP_SRC/src/binder_review/app/static/binderscope-icon.png"
  if [ -f "$ICON" ] && command -v iconutil >/dev/null 2>&1 && command -v sips >/dev/null 2>&1; then
    IS="$(mktemp -d)/Binderscope.iconset"; mkdir -p "$IS"
    for s in 16 32 128 256 512; do
      sips -z $s $s "$ICON" --out "$IS/icon_${s}x${s}.png" >/dev/null 2>&1
      sips -z $((s*2)) $((s*2)) "$ICON" --out "$IS/icon_${s}x${s}@2x.png" >/dev/null 2>&1
    done
    iconutil -c icns "$IS" -o "$APP/Contents/Resources/Binderscope.icns" >/dev/null 2>&1 || warn "could not build the icon (the app still works)"
    rm -rf "$(dirname "$IS")"
  fi
  touch "$APP"
  ok "app: $APP"
fi

# ---------------------------------------------------------------- 5. done
step "5 of 5  Done"
if [ "$MAKE_APP" = 1 ]; then
  say "  Open Binderscope from Launchpad or Spotlight (press Cmd+Space, type Binderscope), or from $APP_DIR."
else
  # No app was created, so there is nothing in Launchpad to open and no
  # ~/Applications to look in. Say what there actually is: the one command,
  # which is the same program as binder-review.
  CMD="$PREFIX/bin/binderscope"; [ -L "$BIN_LINK" ] && CMD="binderscope"
  say "  Start it with:  $CMD app        (a local server and a browser window)"
  say "  Or work from the command line:  $CMD --help"
fi
say "  Your projects are kept in ${HOME}/Binderscope."
say "  To update, run ./install.sh again. To remove it, run ./install.sh --uninstall."
if [ "$MAKE_APP" = 1 ] && [ "$OPEN" = 1 ]; then
  say; say "  Starting Binderscope..."
  open "$APP" >/dev/null 2>&1 || warn "could not start the app: open $APP yourself"
fi
exit 0
