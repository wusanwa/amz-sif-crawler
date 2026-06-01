#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
export AMAZON_DAEMON_URL="${AMAZON_DAEMON_URL:-http://127.0.0.1:8001}"
export SIF_DAEMON_URL="${SIF_DAEMON_URL:-http://127.0.0.1:8002}"

if [[ $# -lt 2 ]]; then
  echo "Usage: bash scripts/daily_report.sh --bindkey <bindKey> [--date YYYY-MM-DD] [--mode both|amazon|sif]" >&2
  exit 1
fi

export PYTHONPATH="$ROOT_DIR/src"
"$PYTHON_BIN" "$ROOT_DIR/daily_report.py" "$@"
