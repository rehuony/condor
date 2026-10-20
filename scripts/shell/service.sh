#!/usr/bin/env bash
# Condor's process lifecycle in a tmux session: run, run-fg, stop, restart, status, logs.
set -uo pipefail
# shellcheck source=scripts/shell/lib/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

SESSION="${CONDOR_TMUX_SESSION:-condor}"

running() { tmux has-session -t "$SESSION" 2>/dev/null; }

set_remain_on_exit() {
    tmux set-option -t "$SESSION" remain-on-exit "$1" >/dev/null 2>&1 \
        || tmux set-window-option -t "$SESSION" remain-on-exit "$1" >/dev/null 2>&1 || true
}

# Fails early (before the frontend build) if Condor is already up.
require_stopped() {
    if running; then
        echo "Condor is already running in tmux session '$SESSION'."
        echo "  make logs     - attach to it"
        echo "  make restart  - stop and start again"
        exit 1
    fi
}

# `remain-on-exit` keeps the pane alive after the process dies, so a startup failure
# can be read back with capture-pane instead of scrolling away with the session.
# Config errors (a bad ADMIN_USER_ID, a mode with no token, a local user config.yml
# has never heard of) exit in the first second. It is turned back off once the boot
# is confirmed: a crash hours later must still take the session with it, or
# `make status` would report a dead Condor as running. main.py does the same after
# its own startup (utils.updater.set_tmux_remain_on_exit).
start() {
    require_stopped
    bash "$REPO_ROOT/scripts/shell/build-frontend.sh" || exit 1
    tmux new-session -d -s "$SESSION" -c "$REPO_ROOT" 'uv run python main.py'
    set_remain_on_exit on
    sleep 3
    if ! running; then
        echo "Condor exited on startup. Run 'make run-fg' to see the error."
        exit 1
    fi
    if [ "$(tmux list-panes -t "$SESSION" -F '#{pane_dead}' 2>/dev/null | head -1)" = "1" ]; then
        echo "Condor exited on startup:"
        echo ""
        tmux capture-pane -p -t "$SESSION" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -20 | sed 's/^/    /'
        echo ""
        echo "  make run-fg - run in the foreground for the full output"
        tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
        exit 1
    fi
    set_remain_on_exit off
    echo "Condor started in tmux session '$SESSION'."
    echo "  make logs - attach (detach: Ctrl+B then D)"
    echo "  make stop - stop Condor"
}

stop() {
    if ! running; then
        echo "Condor is not running."
        return
    fi
    tmux send-keys -t "$SESSION" C-c
    for _ in $(seq 1 20); do
        running || break
        sleep 0.5
    done
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    echo "Condor stopped."
}

case "${1:-}" in
    run) start ;;
    run-fg)
        bash "$REPO_ROOT/scripts/shell/build-frontend.sh" || exit 1
        exec uv run python main.py
        ;;
    stop) stop ;;
    restart) stop; start ;;
    status)
        if running; then
            echo "Condor is running (tmux session '$SESSION')."
        else
            echo "Condor is not running."
        fi
        ;;
    logs)
        tmux attach -t "$SESSION" 2>/dev/null \
            || echo "No '$SESSION' session running. Start it with 'make run'."
        ;;
    *)
        echo "Usage: bash scripts/shell/service.sh {run|run-fg|stop|restart|status|logs}" >&2
        exit 1
        ;;
esac
