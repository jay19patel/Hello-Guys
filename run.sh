#!/usr/bin/env bash
# Start everything for local dev: Redis check, migrations, Django server, AI worker, trigger scheduler.
# Usage: ./run.sh            (PORT=8085 ./run.sh for another port)
# Ctrl+C stops all of them.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/.venv/bin/python"
PORT="${PORT:-8000}"
cd "$ROOT"

# 1. Dependencies
if [ ! -x "$PY" ]; then
  echo "▶ Creating .venv (uv sync)…"
  uv sync
fi

# 2. Redis (queue + live chat stream)
if ! redis-cli ping >/dev/null 2>&1; then
  echo "▶ Starting Redis…"
  if command -v brew >/dev/null; then brew services start redis >/dev/null; else redis-server --daemonize yes; fi
  for _ in {1..20}; do redis-cli ping >/dev/null 2>&1 && break; sleep 0.5; done
  redis-cli ping >/dev/null 2>&1 || { echo "✗ Redis did not start"; exit 1; }
fi
echo "✓ Redis running"

cd "$ROOT/backend"

# 3. Database
"$PY" manage.py migrate --noinput

# 4. Processes. When this script exits (Ctrl+C or one crashes), stop all of them.
# Written for macOS bash 3.2 (no `wait -n`).
cleanup() {
  trap - EXIT INT TERM
  echo; echo "▶ Stopping…"
  # Only this script's processes: children, then their children (runserver's reloader, dramatiq workers).
  for p in $(pgrep -P $$); do pkill -TERM -P "$p" 2>/dev/null || true; done
  pkill -TERM -P $$ 2>/dev/null || true
  wait 2>/dev/null || true
  exit 0
}
trap cleanup EXIT INT TERM

# Worker reloads on code changes when watchdog is installed (dramatiq[watch]); otherwise restart this script.
WATCH=()
if "$PY" -c "import watchdog" 2>/dev/null; then WATCH=(--watch .); else echo "! worker auto-reload off: run 'uv sync' to enable"; fi
"$ROOT/.venv/bin/dramatiq" hello_guy_ai.worker --processes 1 --threads 4 ${WATCH[@]+"${WATCH[@]}"} 2>&1 | sed -l 's/^/[worker] /' &
W=$!
"$PY" manage.py hello_guy_scheduler 2>&1 | sed -l 's/^/[scheduler] /' &
S=$!
"$PY" manage.py runserver "$PORT" 2>&1 | sed -l 's/^/[web] /' &
R=$!

echo "✓ Open http://127.0.0.1:$PORT/admin/  (Ctrl+C to stop)"
while kill -0 $W 2>/dev/null && kill -0 $S 2>/dev/null && kill -0 $R 2>/dev/null; do sleep 2; done
echo "✗ A process exited, stopping the rest."
