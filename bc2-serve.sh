#!/usr/bin/env bash
# Run the BindCraft2 web service, bound to the loopback interface only.
#
# Nothing outside this machine can reach it: people tunnel in over SSH. Give them
# bc2-connect.command and they double-click it.
#
#   ./bc2-serve.sh install     run it at boot and keep it running (recommended)
#   ./bc2-serve.sh             start it now
#   ./bc2-serve.sh stop        stop it
#   ./bc2-serve.sh status      is it up?
#   ./bc2-serve.sh logs        follow the log
#   ./bc2-serve.sh uninstall   stop starting it at boot
#
# Settings live in bc2serve.env beside this script: the port, and any GPUs to keep
# out of the queue. Edit that, then ./bc2-serve.sh restart.
#
# Once installed, start/stop/status go through systemd so there is one answer to
# "is it running", however it was started.
set -euo pipefail

BINDCRAFT_HOME="${BINDCRAFT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$BINDCRAFT_HOME"

UNIT_NAME="bindcraft-web"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/$UNIT_NAME.service"
ENV_FILE="$BINDCRAFT_HOME/bc2serve.env"
LOG="$BINDCRAFT_HOME/bc2serve.log"
PIDFILE="$BINDCRAFT_HOME/bc2serve_state/server.pid"

[[ -f $ENV_FILE ]] && source "$ENV_FILE"
# One instance per person, so two people on this machine must not both land on
# 8080. The default is derived from the user id: stable for a given account,
# different between accounts, and needing no coordination.
#
# Stable is the part that matters. The review pages keep your keep/maybe/reject
# decisions in the browser against the address they were opened from, so a port
# that moved between restarts would look like a fresh set of reviews with every
# decision lost. That rules out "just grab a free port".
PORT="${BC2SERVE_PORT:-$((8080 + $(id -u) % 900))}"

installed() { [[ -f $UNIT_FILE ]]; }

write_env_file() {
  [[ -f $ENV_FILE ]] && return 0
  # Not a quoted heredoc: the port below is this account's derived one, written
  # down so it is a fact of the install rather than something recomputed each
  # start. An existing file is never rewritten (the guard above), so upgrading
  # an install keeps whatever port its reviews were saved against.
  cat > "$ENV_FILE" <<SETTINGS
# Settings for the BindCraft web service. Change these, then: ./bc2-serve.sh restart

# Port it listens on, on this machine's loopback interface only. Binderscope runs
# one instance per person, so this is derived from your user id to keep two
# accounts on this machine off the same port. Change it if you like, but do not
# change it casually: the review pages store your keep/maybe/reject decisions
# against this address, and moving the port hides them. Tell people to use the
# matching line in bc2-connect.command.
BC2SERVE_PORT=$PORT

# GPUs the queue must never touch, comma separated, for cards that should always be
# free for cryoSPARC or for interactive work. Leave empty to let it use all of them.
BC2SERVE_RESERVED_GPUS=

# Where campaign notifications are sent. The public ntfy.sh needs no account; point
# this at your own ntfy server if you would rather not use it.
BC2SERVE_NTFY_SERVER=https://ntfy.sh
SETTINGS
  echo "bc2serve: wrote $ENV_FILE"
}

show_how_to_reach() {
  cat <<REACH

Reserved GPUs: ${BC2SERVE_RESERVED_GPUS:-none}

From your own machine:
  ssh -N -L $PORT:localhost:$PORT $USER@$(hostname -f)
then open http://localhost:$PORT

Or hand people bc2-connect.command, which does both.
REACH
}

# ------------------------------------------------------------------ subcommands

case "${1:-start}" in

  run-foreground)
    # What systemd runs. Stays in the foreground so systemd can supervise it.
    # The log is redirected here rather than with StandardOutput=append:, which
    # needs systemd 240 and this machine has 239 -- where it silently logs nothing.
    exec >> "$LOG" 2>&1
    echo "=== starting on 127.0.0.1:$PORT at $(date '+%Y-%m-%d %H:%M:%S') ==="
    exec .venv/bin/python -m uvicorn bc2serve.app:app \
      --host 127.0.0.1 --port "$PORT"
    ;;

  install)
    write_env_file
    mkdir -p "$UNIT_DIR"

    # Lingering is what lets a user service run with nobody logged in, which is
    # the whole point of starting at boot.
    if [[ $(loginctl show-user "$USER" -p Linger --value 2>/dev/null) != yes ]]; then
      if ! loginctl enable-linger "$USER" 2> /dev/null; then
        echo "bc2serve: could not enable lingering for $USER." >&2
        echo "bc2serve: without it the service stops when you log out. Ask an admin for:" >&2
        echo "    sudo loginctl enable-linger $USER" >&2
      fi
    fi

    cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=BindCraft2 web service
After=network-online.target

[Service]
Type=exec
WorkingDirectory=$BINDCRAFT_HOME
Environment=BINDCRAFT_HOME=$BINDCRAFT_HOME
EnvironmentFile=-$ENV_FILE
ExecStart=$BINDCRAFT_HOME/bc2-serve.sh run-foreground
Restart=on-failure
RestartSec=10
# The campaigns it starts are detached into their own process groups on purpose,
# so stopping the service leaves running designs alone.
KillMode=process

[Install]
WantedBy=default.target
UNIT

    # A manually started server would hold the port the service wants.
    if [[ -f $PIDFILE ]] && kill -0 "$(cat "$PIDFILE")" 2> /dev/null; then
      echo "bc2serve: stopping the manually started server first"
      kill "$(cat "$PIDFILE")" || true
      rm -f "$PIDFILE"
      sleep 2
    fi

    systemctl --user daemon-reload
    systemctl --user enable "$UNIT_NAME"
    # restart, not start: "enable --now" leaves an already-running service alone,
    # which would keep serving the code it started with.
    systemctl --user restart "$UNIT_NAME"
    sleep 3
    systemctl --user --no-pager --lines=0 status "$UNIT_NAME" || true
    echo
    echo "bc2serve: installed. It starts at boot and restarts if it crashes."
    echo "bc2serve: settings in $ENV_FILE"
    show_how_to_reach
    ;;

  uninstall)
    if installed; then
      systemctl --user disable --now "$UNIT_NAME" 2> /dev/null || true
      rm -f "$UNIT_FILE"
      systemctl --user daemon-reload
      echo "bc2serve: no longer starts at boot"
    else
      echo "bc2serve: was not installed"
    fi
    ;;

  stop)
    if installed; then
      systemctl --user stop "$UNIT_NAME"
      echo "bc2serve: stopped"
    elif [[ -f $PIDFILE ]] && kill -0 "$(cat "$PIDFILE")" 2> /dev/null; then
      kill "$(cat "$PIDFILE")"
      rm -f "$PIDFILE"
      echo "bc2serve: stopped"
    else
      echo "bc2serve: not running" >&2
    fi
    ;;

  restart)
    if installed; then
      systemctl --user restart "$UNIT_NAME"
      echo "bc2serve: restarted"
    else
      "$0" stop 2> /dev/null || true
      sleep 1
      "$0" start
    fi
    ;;

  status)
    if installed; then
      systemctl --user --no-pager --lines=0 status "$UNIT_NAME" || true
    elif [[ -f $PIDFILE ]] && kill -0 "$(cat "$PIDFILE")" 2> /dev/null; then
      echo "bc2serve: running as pid $(cat "$PIDFILE") (started by hand, not at boot)"
    else
      echo "bc2serve: not running"
    fi
    ;;

  logs)
    if installed; then
      journalctl --user -u "$UNIT_NAME" -f
    else
      tail -f "$LOG"
    fi
    ;;

  start)
    if installed; then
      systemctl --user start "$UNIT_NAME"
      sleep 2
      systemctl --user --no-pager --lines=0 status "$UNIT_NAME" || true
      show_how_to_reach
      exit 0
    fi

    write_env_file
    mkdir -p "$(dirname "$PIDFILE")"

    if [[ -f $PIDFILE ]] && kill -0 "$(cat "$PIDFILE")" 2> /dev/null; then
      echo "bc2serve: already running as pid $(cat "$PIDFILE")" >&2
      exit 1
    fi

    # --host 127.0.0.1 is the part that matters on a shared machine: the service is
    # never offered to the network, so an SSH tunnel is the only route in.
    setsid nohup "$0" run-foreground < /dev/null > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 2

    if ! kill -0 "$(cat "$PIDFILE")" 2> /dev/null; then
      echo "bc2serve: failed to start. Last lines of $LOG:" >&2
      tail -20 "$LOG" >&2
      rm -f "$PIDFILE"
      exit 1
    fi
    echo "bc2serve: running on 127.0.0.1:$PORT as pid $(cat "$PIDFILE")"
    echo "bc2serve: started by hand; ./bc2-serve.sh install makes it start at boot"
    show_how_to_reach
    ;;

  *)
    echo "usage: $0 [install|start|stop|restart|status|logs|uninstall]" >&2
    exit 2
    ;;
esac
