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
# For --sge on a host older than glibc 2.28, where the review package cannot be installed:
#   the launcher is installed on its own, and reviews run in a Python container on a compute node.
#   --review-sif PATH  a Python 3.12+ Apptainer image to run reviews in (default: pull python:3.12-slim)
#   --review-queue N   the SGE queue for review jobs, which need no GPU (default: the site's default)
#   --review-submit C  the whole command that sends a review to a node, for a site that needs more
#                      than a queue (e.g. a resource saying "no GPU"); it must stream output like qrsh
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
RSIF="" RQUEUE="" RQUEUE_SET=0 RSUBMIT="" OLD_GLIBC=0
REST=()

usage() { sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
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
    --review-sif) RSIF="${2:?--review-sif needs a path}"; shift ;;
    --review-queue) RQUEUE="${2?--review-queue needs a name}"; RQUEUE_SET=1; shift ;;
    --review-submit) RSUBMIT="${2:?--review-submit needs a command}"; shift ;;
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
  # The review package needs numpy 2.5 and scipy 1.18, whose prebuilt packages need glibc
  # 2.28; here pip could only try to compile them, and fail. The launcher itself is happy
  # with older releases, so on a cluster it is installed alone and the reviews go to a
  # container on a compute node, which brings its own glibc.
  [[ $MODE == sge ]] || die "this machine's C library is glibc $GLIBC, too old for the review package (numpy 2.5 and
scipy 1.18 need glibc 2.28 or newer) and for BindCraft 2 itself. Run the review-only install
on a newer machine. On a cluster, ./install.sh --sge installs the launcher on its own."
  OLD_GLIBC=1
  say "   glibc $GLIBC is older than 2.28, too old for the review package. The launcher is installed"
  say "   on its own, and reviews will run in a Python container on a compute node."
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
  if (( OLD_GLIBC )); then
    for c in qrsh apptainer; do command -v "$c" >/dev/null || die "there is no $c here, and launcher-only mode needs it on this host: reviews are sent
to compute nodes with qrsh, and the review runtime is set up and run with apptainer."; done
    [[ -n $RSUBMIT ]] || (( RQUEUE_SET )) || ask RQUEUE "SGE queue for review jobs (CPU only)? Leave empty for the site's default." ""
    RSIF="${RSIF/#\~/$HOME}"
    [[ -z $RSIF || -f $RSIF ]] || die "no review image at '$RSIF'. Give a Python 3.12+ Apptainer image, or leave --review-sif out to pull one."
    [[ -z $RJOBS ]] && RJOBS=4        # a review runs on a compute node here, with the slots it asks for
  fi
  [[ -z $RJOBS ]] && RJOBS=2
fi
for v in "$SIF" "$PARAMS" "$QUEUE" "$RSIF" "$RQUEUE" "$RSUBMIT"; do [[ $v != *[\"\$\`\\]* ]] || die "a value may not contain \" \$ \` or \\: $v"; done
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
if (( OLD_GLIBC )); then
  run "$VENV/bin/python" -m pip install --quiet --prefer-binary -r "$BC/bc2serve/requirements-launcher.txt"
  run "$VENV/bin/python" -c "import fastapi, uvicorn, numpy, biotite, scipy" \
    || die "the packages did not import; see the pip output above"
  (( DRY )) || say "   ok: $("$VENV/bin/python" -c 'import sys, numpy; print("Python", sys.version.split()[0], "· numpy", numpy.__version__, "· the launcher alone")')"
else
run "$VENV/bin/python" -m pip install --quiet --prefer-binary -r "$BC/bc2serve/requirements.txt"
# Editable, and it must be: the review finds its pages (viewer/), schemas (docs/schema) and
# validator (scripts/) beside its source. A plain install copies the package alone, and the
# first review then fails with "viewer sources not found".
run "$VENV/bin/python" -m pip install --quiet --prefer-binary -e "$BC/binderscope"
run "$VENV/bin/python" -c "import fastapi, uvicorn, numpy, biotite, binder_review" \
  || die "the packages did not import; see the pip output above"
(( DRY )) || say "   ok: $("$VENV/bin/python" -c 'import sys, numpy, binder_review; print("Python", sys.version.split()[0], "· numpy", numpy.__version__, "· binder_review", binder_review.__version__)')"
fi

# ---- 2b. launcher-only: the review package in a container, for compute nodes
REVIEW_RUNTIME="" REVIEW_SUBMIT=""
if (( OLD_GLIBC )); then
  step "Setting up the review runtime: the review package in a Python container"
  RDIR="$BC/.binderscope" RVENV="$BC/.binderscope/review-venv"
  if [[ -z $RSIF ]]; then
    RSIF="$RDIR/python312.sif"
    if [[ ! -f $RSIF ]]; then run mkdir -p "$RDIR"; run apptainer pull "$RSIF" docker://python:3.12-slim; fi
  fi
  # The checkout is bound in by name, so the venv, the source and the run folders are where
  # the host says they are even when the checkout is not under the home directory.
  APX=(apptainer exec -e --bind "$BC" "$RSIF")
  # -L, not -e: the venv's python links to the container's own interpreter, a path that
  # does not exist on the host, so from out here the link always looks broken.
  [[ -L $RVENV/bin/python ]] || run "${APX[@]}" python3 -m venv "$RVENV"
  run "${APX[@]}" "$RVENV/bin/python" -m pip install --quiet --upgrade pip
  run "${APX[@]}" "$RVENV/bin/python" -m pip install --quiet --prefer-binary -e "$BC/binderscope"
  run "${APX[@]}" "$RVENV/bin/python" -m binder_review --version \
    || die "the review package does not run in $RSIF; see the pip output above"
  # Quoted for shlex, which is how the service splits it: a path with a space stays one word.
  REVIEW_RUNTIME="apptainer exec -e --bind '$BC' '$RSIF' '$RVENV/bin/python' -m binder_review"
  # qrsh, not qsub: the build's output has to stream back as it runs, because that is
  # what drives the progress stages. -noshell keeps a login shell such as tcsh from
  # re-reading the arguments; -now no lets it wait for a slot instead of failing at once.
  REVIEW_SUBMIT="${RSUBMIT:-qrsh -noshell -now no${RQUEUE:+ -q $RQUEUE} -pe smp $RJOBS}"
  say "   review jobs: $REVIEW_SUBMIT"
fi

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
if (( OLD_GLIBC )); then
  set_env BC2SERVE_REVIEW_RUNTIME "$REVIEW_RUNTIME"
  set_env BC2SERVE_REVIEW_SUBMIT "$REVIEW_SUBMIT"
fi

# ---- 4. start it
if (( NOSERVICE )); then
  step "Done. Start it with: $BC/bc2-serve.sh install   (or $BC/bc2-serve.sh to start it by hand)"
  exit 0
fi
UNIT="$HOME/.config/systemd/user/bindcraft-web.service"
if [[ -f $UNIT ]] && ! grep -qxF "WorkingDirectory=$BC" "$UNIT"; then
  # One instance per person: the unit belongs to another checkout, and installing
  # this one would take it over.
  step "Installed, not started: this account already runs Binderscope from $(sed -n 's/^WorkingDirectory=//p' "$UNIT")"
  say "   It is one instance per person. To run this one as well, set BC2SERVE_PORT in"
  say "   $BC/bc2serve.env to a free port and start it by hand: $BC/bc2-serve.sh"
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
