#!/usr/bin/env bash
# Binderscope installer: one entry point, three installs.
#
#   ./install.sh            asks which one
#   ./install.sh --review   the review on its own: reads finished BindCraft runs.
#                           No GPU, no BindCraft. macOS or Linux. Runs binderscope/install.sh.
#   ./install.sh --local    the whole thing on this machine: runs BindCraft 2 campaigns
#                           on its own NVIDIA GPUs, queues them, and reviews them.
#   ./install.sh --sge      the whole thing on an SGE cluster: run on a host that can
#                           submit jobs; campaigns go to the scheduler as Apptainer jobs.
#
# For --local and --sge:
#   --bindcraft DIR    the BindCraft 2 checkout to install into (default: this folder, when it is one)
#   --review-jobs N    worker processes a review may use (--sge: 2; --local: every core but one)
#   --no-service       install, but do not start the service
# For --sge also:
#   --sif PATH         the BindCraft 2 Apptainer image that cluster jobs run    (asked for if not given)
#   --params DIR       the AlphaFold 2 parameters folder those jobs bind in    (asked for if not given)
#   --queue NAME       the SGE queue to submit to (default: the site's own default queue)
#   --python PATH      a Python 3.12+ for the service, if the checkout has no .venv yet
# Everywhere:
#   --dry-run          say what would be done, and do nothing
#   -y, --yes          ask nothing: use what was given and the defaults
#
# With --review, options this script does not know go to binderscope/install.sh (see its header).
# Nothing here needs administrator rights, and nothing outside the BindCraft 2
# checkout (or, for --review, the folders binderscope/install.sh names) is written.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="" BC="" PY="" SIF="" PARAMS="" QUEUE="" QUEUE_SET=0 RJOBS="" NOSERVICE=0 DRY=0 YES=0
REST=()

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
die() { printf '\ninstall: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
run() { if (( DRY )); then printf '   would run: %s\n' "$*"; else "$@"; fi; }
interactive() { (( ! YES )) && [[ -t 0 ]]; }

while (( $# )); do
  case "$1" in
    --review) MODE=review ;;
    --local) MODE=local ;;
    --sge) MODE=sge ;;
    --bindcraft) BC="${2:?--bindcraft needs a folder}"; shift ;;
    --python) PY="${2:?--python needs a path}"; shift ;;
    --sif) SIF="${2:?--sif needs a path}"; shift ;;
    --params) PARAMS="${2:?--params needs a folder}"; shift ;;
    --queue) QUEUE="${2?--queue needs a name}"; QUEUE_SET=1; shift ;;   # empty is allowed: the site default
    --review-jobs) RJOBS="${2:?--review-jobs needs a number}"; shift ;;
    --no-service) NOSERVICE=1 ;;
    --dry-run) DRY=1 ;;
    -y|--yes) YES=1 ;;
    -h|--help) usage ;;
    *) REST+=("$1") ;;          # not ours: binderscope/install.sh may know it (--review only)
  esac
  shift
done

# ------------------------------------------------------------------ which install

if [[ -z $MODE ]]; then
  interactive || die "say which install: --review, --local or --sge (see --help)"
  cat <<'MENU'
Binderscope installs in one of three ways.

  1) Review only
     Read finished BindCraft 1 or 2 run folders and review them in the browser.
     No GPU, no BindCraft, no web service. macOS or Linux.

  2) Full, on this machine
     Run BindCraft 2 campaigns on this machine's NVIDIA GPUs, queue them and
     review them, from a browser over an SSH tunnel. Needs Linux and a working
     BindCraft 2 checkout.

  3) Full, on an SGE cluster
     The same, but campaigns are sent to an SGE scheduler as Apptainer jobs.
     Run this on a host that can submit jobs (qsub). Needs a BindCraft 2
     Apptainer image and the AlphaFold parameters on the cluster.

MENU
  read -r -p "Which one? [1/2/3] " pick || pick=""      # end of input is "no answer", not a silent exit under set -e
  case "$pick" in 1) MODE=review ;; 2) MODE=local ;; 3) MODE=sge ;; *) die "no install chosen" ;; esac
fi

if [[ $MODE == review ]]; then
  # Our --python and -y mean the same to binderscope/install.sh, so they go through too.
  [[ -n $PY ]] && REST+=(--python "$PY")
  (( YES )) && REST+=(-y)
  [[ -x $HERE/binderscope/install.sh ]] || die "binderscope/install.sh is missing: copy the whole repository, not single files"
  if (( DRY )); then say "would run: $HERE/binderscope/install.sh ${REST[*]:-}"; exit 0; fi
  # ${a[@]+"${a[@]}"}: macOS ships bash 3.2, where an empty array under set -u is an error
  exec "$HERE/binderscope/install.sh" ${REST[@]+"${REST[@]}"}
fi

# ------------------------------------------------------------------ the full installs

(( ${#REST[@]} == 0 )) || die "unknown option ${REST[0]} (try --help)"

ask() {   # ask VAR "question" [default]: answer, else default, else what VAR already holds
  local var=$1 q=$2 def=${3:-} cur="${!1}" ans
  [[ -n $cur ]] && return 0
  if interactive; then read -r -p "$q${def:+ [$def]} " ans || ans=""; printf -v "$var" '%s' "${ans:-$def}"
  else printf -v "$var" '%s' "$def"; fi
}
version_ge() { printf '%s\n%s\n' "$2" "$1" | sort -V -C; }     # is $1 >= $2

is_checkout() { [[ -d $1/bindcraft && -d $1/settings/target ]]; }
if [[ -z $BC ]] && is_checkout "$HERE"; then BC=$HERE; fi
ask BC "Where is your BindCraft 2 checkout (the folder holding bindcraft/ and settings/)?"
[[ -n $BC ]] || die "a full install goes into a BindCraft 2 checkout; give it with --bindcraft DIR"
BC="$(cd "${BC/#\~/$HOME}" 2>/dev/null && pwd)" || die "no such folder: $BC"
is_checkout "$BC" || die "$BC does not look like a BindCraft 2 checkout: it has no bindcraft/ and settings/target/"
VENV="$BC/.venv"

step "Checking this machine for a ${MODE} install"
case "$(uname -s)" in Linux) ;; *) die "the full install runs on Linux. On a Mac, choose the review-only install." ;; esac
GLIBC="$(ldd --version 2>/dev/null | head -1 | grep -o '[0-9]\+\.[0-9]\+$' || true)"
if [[ -n $GLIBC ]] && ! version_ge "$GLIBC" 2.28; then
  die "this machine's C library is glibc $GLIBC. The service needs numpy 2.5 and scipy 1.18, whose
prebuilt packages need glibc 2.28 or newer, so they cannot be installed here at all (pip
would try to build them and fail). On a cluster this old, run the review-only install on
your own computer, or ask your site about a newer submit host. See 'On an SGE cluster' in
the README."
fi
if [[ $MODE == local ]]; then
  command -v nvidia-smi >/dev/null || say "   note: no nvidia-smi here; campaigns need this machine's NVIDIA GPUs"
  [[ -x $VENV/bin/python && -x $VENV/bin/bindcraft ]] || die "$BC has no working BindCraft 2 environment ($VENV/bin/bindcraft).
Install BindCraft 2 itself first, with its own instructions, and check that it designs a
binder. Binderscope runs it; it does not install it."
else
  for c in qsub qstat qdel; do command -v "$c" >/dev/null || die "there is no $c here. Run this on a host that can submit SGE jobs (usually a login node)."; done
  command -v apptainer >/dev/null || say "   note: no apptainer on this host. That is fine if the compute nodes have it: the jobs run it there."
  if [[ ! -x $VENV/bin/python ]]; then
    if [[ -z $PY ]]; then
      for c in python3.13 python3.12 python3; do
        p="$(command -v "$c" || true)"
        [[ -n $p ]] && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null && { PY=$p; break; }
      done
    fi
    [[ -n $PY ]] || die "no Python 3.12 or newer here. Without root: pip install --user uv && uv python install 3.12,
then run this again with --python \"\$(uv python find 3.12)\"."
  fi
  ask SIF "Path to the BindCraft 2 Apptainer image (.sif) that cluster jobs should run?"
  ask PARAMS "Path to the AlphaFold 2 parameters folder (the one holding params_model_*.npz)?"
  (( QUEUE_SET )) || ask QUEUE "SGE queue to submit to? Leave empty for the site's default." ""
  SIF="${SIF/#\~/$HOME}" PARAMS="${PARAMS/#\~/$HOME}"
  [[ -n $SIF && -f $SIF ]] || die "no BindCraft 2 image at '${SIF}'. Give one with --sif. Build or pull a CUDA 12 image: many clusters' GPU drivers are too old for CUDA 13."
  [[ -n $PARAMS && -d $PARAMS ]] || die "no AlphaFold parameters folder at '${PARAMS}'. Give it with --params."
  ls "$PARAMS"/params_model_*.npz >/dev/null 2>&1 || say "   note: no params_model_*.npz in $PARAMS; BindCraft will not find its weights there"
  [[ -z $RJOBS ]] && RJOBS=2
fi
for v in "$SIF" "$PARAMS" "$QUEUE"; do [[ $v != *[\"\$\`\\]* ]] || die "a value may not contain \" \$ \` or \\: $v"; done
[[ -z $RJOBS || $RJOBS =~ ^[1-9][0-9]*$ ]] || die "--review-jobs takes a whole number, not '$RJOBS'"
say "   BindCraft 2 checkout: $BC"

# ---- 1. lay Binderscope over the checkout
if [[ $BC != "$HERE" ]]; then
  step "Copying Binderscope into $BC"
  say "   bc2serve/, binderscope/ and bc2-serve.sh are added or updated. Nothing of BindCraft's"
  say "   is changed or removed, and an existing bc2serve.env is kept."
  if interactive && (( ! DRY )); then read -r -p "   Go ahead? [Y/n] " ok || ok=n; [[ ${ok:-y} =~ ^[Yy] ]] || die "stopped; nothing copied"; fi
  for item in bc2serve binderscope bc2-serve.sh; do run cp -R "$HERE/$item" "$BC/"; done
fi

# ---- 2. the service's Python environment
step "Installing the service's Python packages into $VENV"
if [[ ! -x $VENV/bin/python ]]; then run "$PY" -m venv "$VENV"; fi
# --prefer-binary: take a prebuilt package whenever there is one, rather than a newer
# release that would have to be compiled here.
run "$VENV/bin/python" -m pip install --quiet --upgrade pip
run "$VENV/bin/python" -m pip install --quiet --prefer-binary -r "$BC/bc2serve/requirements.txt"
# Editable, and it must be: the review finds its pages (viewer/), schemas (docs/schema) and
# validator (scripts/) beside its source. A plain install copies the package alone, and the
# first review then fails with "viewer sources not found".
run "$VENV/bin/python" -m pip install --quiet --prefer-binary -e "$BC/binderscope"
run "$VENV/bin/python" -c "import fastapi, uvicorn, numpy, biotite, binder_review" \
  || die "the packages did not import; see the pip output above"
(( DRY )) || say "   ok: $("$VENV/bin/python" -c 'import sys, numpy, binder_review; print("Python", sys.version.split()[0], "· numpy", numpy.__version__, "· binder_review", binder_review.__version__)')"

# ---- 3. settings
step "Writing settings to $BC/bc2serve.env"
ENV_FILE="$BC/bc2serve.env"
run "$BC/bc2-serve.sh" init
set_env() {   # set_env KEY VALUE: change the line if it is there, add it if not
  local key=$1 val=$2
  if (( DRY )); then say "   would set $key=\"$val\""; return; fi
  if grep -q "^$key=" "$ENV_FILE"; then
    local tmp; tmp="$(mktemp)"
    awk -v k="$key" -v v="$val" 'index($0, k "=") == 1 { print k "=\"" v "\""; next } { print }' "$ENV_FILE" > "$tmp" && cat "$tmp" > "$ENV_FILE" && rm -f "$tmp"
  else
    printf '%s="%s"\n' "$key" "$val" >> "$ENV_FILE"
  fi
  say "   $key=\"$val\""
}
if [[ $MODE == sge ]] && ! grep -q '^BC2SERVE_SGE_SIF=' "$ENV_FILE" 2>/dev/null && (( ! DRY )); then
  printf '\n# Sending campaigns to an SGE cluster (./install.sh --sge). See "On an SGE cluster" in the README.\n' >> "$ENV_FILE"
fi
set_env BC2SERVE_BACKEND "$MODE"
if [[ $MODE == sge ]]; then
  set_env BC2SERVE_SGE_SIF "$SIF"
  set_env BC2SERVE_SGE_PARAMS "$PARAMS"
  set_env BC2SERVE_SGE_QUEUE "$QUEUE"
fi
[[ -n $RJOBS ]] && set_env BC2SERVE_REVIEW_JOBS "$RJOBS"

# ---- 4. start it
if (( NOSERVICE )); then
  step "Done. Start it with: $BC/bc2-serve.sh install   (or $BC/bc2-serve.sh to start it by hand)"
  exit 0
fi
if systemctl --user show-environment >/dev/null 2>&1; then
  step "Installing the service (systemd user unit: starts at boot, restarts if it crashes)"
  run "$BC/bc2-serve.sh" install
else
  step "Starting the service by hand (this machine has no systemd user session)"
  say "   It runs until you stop it, log out for good, or the machine restarts."
  [[ $MODE == sge ]] && say "   On a login node, check your site's rules for long-running processes first."
  run "$BC/bc2-serve.sh" start
fi
