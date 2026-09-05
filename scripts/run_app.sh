#!/usr/bin/env bash
# Run the service on the host, supervised.
#
# Docker Compose is the documented path (`docker compose up -d`), but running
# the app on the host against the containerised database is the fallback when
# the image cannot be built, and it is what WORKFLOWS.md section 0 describes.
#
# The restart loop is not a convenience: it reproduces `restart: unless-stopped`
# from docker-compose.yml, which Experiment 4's process-kill mode depends on.
# Without it the service stays dead after os._exit and the invariants can never
# be re-checked.
#
#   scripts/run_app.sh start | stop | status | logs

set -euo pipefail
cd "$(dirname "$0")/.."

PIDFILE="${PIDFILE:-.run/app.pid}"
LOGFILE="${LOGFILE:-.run/app.log}"
PORT="${PORT:-8000}"
DB_PORT="${DB_PORT:-5433}"

export DATABASE_URL="${DATABASE_URL:-postgresql://postgres:postgres@localhost:${DB_PORT}/ledger}"
export ENFORCE_DB_CONSTRAINT="${ENFORCE_DB_CONSTRAINT:-true}"
export POOL_MAX_SIZE="${POOL_MAX_SIZE:-120}"
export NAIVE_RACE_DELAY_MS="${NAIVE_RACE_DELAY_MS:-0}"
export ALLOW_ADMIN_ENDPOINTS="${ALLOW_ADMIN_ENDPOINTS:-true}"

mkdir -p .run

supervise() {
    # Exit code 3 is the injected crash (see app/main.py). Anything else that is
    # not a clean shutdown is also restarted, matching the container policy.
    while true; do
        .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 \
            >>"$LOGFILE" 2>&1 || true
        if [ -f .run/stop ]; then break; fi
        echo "--- service exited, restarting $(date -Is) ---" >>"$LOGFILE"
        sleep 0.5
    done
}

case "${1:-start}" in
start)
    rm -f .run/stop
    : >"$LOGFILE"
    supervise &
    echo $! >"$PIDFILE"
    for _ in $(seq 1 40); do
        if curl -sf "localhost:${PORT}/health" >/dev/null 2>&1; then
            echo "app up on :${PORT} (supervisor pid $(cat "$PIDFILE"))"
            exit 0
        fi
        sleep 0.5
    done
    echo "app failed to start; last log lines:" >&2
    tail -20 "$LOGFILE" >&2
    exit 1
    ;;
stop)
    touch .run/stop
    [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null || true
    pkill -f "[u]vicorn app.main:app" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "stopped"
    ;;
status)
    curl -s "localhost:${PORT}/config" || echo "down"
    ;;
logs)
    tail -f "$LOGFILE"
    ;;
*)
    echo "usage: $0 {start|stop|status|logs}" >&2
    exit 2
    ;;
esac
