#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    echo "Python interpreter not executable: $ROOT_DIR/.venv/bin/python" >&2
    exit 1
  fi
fi

export PYTHONPATH="$ROOT_DIR/src"

echo "[1/3] compile"
"$PYTHON_BIN" -m compileall "$ROOT_DIR/src" "$ROOT_DIR/mcp_server.py" "$ROOT_DIR/mcp_gateway.py" "$ROOT_DIR/crawler_worker.py" "$ROOT_DIR/sif_login.py" "$ROOT_DIR/crawl_once.py"

echo "[2/3] import-check"
"$PYTHON_BIN" -c "from amz_sif_crawler.api.app import build_app; from amz_sif_crawler.service import crawl_and_wrap; print('import ok')"

echo "[3/3] pytest"
"$PYTHON_BIN" -m pytest "$ROOT_DIR/tests" -q
