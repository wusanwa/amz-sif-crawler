#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
export AMAZON_DAEMON_URL="${AMAZON_DAEMON_URL:-http://127.0.0.1:8001}"
export SIF_DAEMON_URL="${SIF_DAEMON_URL:-http://127.0.0.1:8002}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    echo "Python interpreter not executable: $ROOT_DIR/.venv/bin/python" >&2
    exit 1
  fi
fi

if [[ -z "${HERMES_BINDING_KEY:-}" ]]; then
  echo "Usage: HERMES_BINDING_KEY=<bindKey> bash scripts/daily_report.sh [--date YYYY-MM-DD] [--mode both|amazon|sif]" >&2
  echo "bindKey must be provided via HERMES_BINDING_KEY environment variable." >&2
  exit 1
fi

export PYTHONPATH="$ROOT_DIR/src"
"$PYTHON_BIN" "$ROOT_DIR/daily_report.py" "$@"
