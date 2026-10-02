#!/usr/bin/env bash
# Loom, locally, in one command:   ./start.sh            (live: Claude does the work)
#                                  LOOM_RUNNER=stub ./start.sh   (demo: placeholders, no Claude calls)
# Needs Python 3.11+, and either a Claude Code login (run `claude` once) or ANTHROPIC_API_KEY.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Loom needs Python 3.11 or newer (found $("$PY" --version 2>&1)). Set PYTHON=/path/to/python3.11"; exit 1; }

if [ ! -x .venv/bin/loom ]; then
  echo "First run: setting up a local environment in loom/.venv (a minute or two)…"
  "$PY" -m venv .venv
  .venv/bin/pip install -q --upgrade pip
fi
.venv/bin/pip install -q -e '.[dev]'

export LOOM_RUNNER="${LOOM_RUNNER:-sdk}"
export LOOM_INLINE_WORKER=1
export LOOM_UNDO_SECONDS="${LOOM_UNDO_SECONDS:-10}"
PORT="${PORT:-8080}"
export LOOM_APP_BASE_URL="http://localhost:$PORT"

if ! .venv/bin/loom doctor; then
  echo
  echo "Loom can't reach Claude yet. Start in demo mode instead with:  LOOM_RUNNER=stub ./start.sh"
  exit 1
fi

echo
echo "Loom is running at http://localhost:$PORT  (Ctrl+C to stop)"
( sleep 2; command -v open >/dev/null && open "http://localhost:$PORT" || command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT" ) >/dev/null 2>&1 &
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning
