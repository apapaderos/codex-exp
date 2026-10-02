#!/usr/bin/env bash
# Loom, locally, in one command:   ./start.sh            (live: Claude does the work)
#                                  LOOM_RUNNER=stub ./start.sh   (demo: placeholders, no Claude calls)
# Needs Python 3.11+, and either a Claude Code login (run `claude` once) or ANTHROPIC_API_KEY.
set -euo pipefail
cd "$(dirname "$0")"

# Find a Python 3.11+: $PYTHON, then python3.13/3.12/3.11 on PATH, Homebrew and python.org
# install locations, then one managed by uv. macOS ships 3.9, which is too old.
ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; }
PY=""
for c in "${PYTHON:-}" python3.13 python3.12 python3.11 \
         /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
         /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
         /Library/Frameworks/Python.framework/Versions/3.1[123]/bin/python3 python3; do
  [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 && ok "$c" && { PY="$c"; break; }
done
if [ -z "$PY" ]; then
  UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
  if [ -x "$UV" ]; then
    "$UV" python install 3.12 >/dev/null 2>&1 || true
    PY="$("$UV" python find 3.12 2>/dev/null || true)"
  fi
fi
if [ -z "$PY" ]; then
  cat <<'MSG'
Loom needs Python 3.11 or newer, and this Mac only has an older one.
Install one of these, then run ./start.sh again:

  Easiest (no admin rights, about a minute):
    curl -LsSf https://astral.sh/uv/install.sh | sh
    (then close and reopen Terminal; ./start.sh will fetch Python 3.12 by itself)

  Or with Homebrew:     brew install python@3.12
  Or the installer:     https://www.python.org/downloads/macos/
MSG
  exit 1
fi
echo "Using $("$PY" --version) at $(command -v "$PY" || echo "$PY")"
# A venv made by an older Python can't be reused.
if [ -x .venv/bin/python ] && ! ok .venv/bin/python; then rm -rf .venv; fi

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
  if [ "$LOOM_RUNNER" = "sdk" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -t 0 ]; then
    echo
    read -r -p "Sign in to Claude now? It opens your browser. [Y/n] " yn
    if [ "${yn:-Y}" != "n" ] && [ "${yn:-Y}" != "N" ]; then
      .venv/bin/loom login && .venv/bin/loom doctor && SIGNED_IN=1
    fi
  fi
  if [ -z "${SIGNED_IN:-}" ]; then
    echo
    echo "Loom can't reach Claude yet. Sign in with  .venv/bin/loom login  or set ANTHROPIC_API_KEY,"
    echo "or look around in demo mode (no Claude calls):  LOOM_RUNNER=stub ./start.sh"
    exit 1
  fi
fi

echo
echo "Loom is running at http://localhost:$PORT  (Ctrl+C to stop)"
( sleep 2; command -v open >/dev/null && open "http://localhost:$PORT" || command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT" ) >/dev/null 2>&1 &
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning
