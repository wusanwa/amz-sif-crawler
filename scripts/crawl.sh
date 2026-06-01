#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
CRAWLER_API_URL="${CRAWLER_API_URL:-http://127.0.0.1:8000/crawl}"

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/crawl.sh [--amazon-only|--sif-only] <amazon-url-or-asin> [more...]" >&2
  exit 1
fi

export PYTHONPATH="$ROOT_DIR/src"
MODE="both"
if [[ "${1:-}" == "--amazon-only" ]]; then
  MODE="amazon"
  shift
elif [[ "${1:-}" == "--sif-only" ]]; then
  MODE="sif"
  shift
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/crawl.sh [--amazon-only|--sif-only] <amazon-url-or-asin> [more...]" >&2
  exit 1
fi

URLS_JSON="$("$PYTHON_BIN" -c 'import json,sys; print(json.dumps(sys.argv[1:], ensure_ascii=False))' "$@")"
PAYLOAD="$("$PYTHON_BIN" -c 'import json,sys; print(json.dumps({"urls": json.loads(sys.argv[1]), "mode": sys.argv[2]}, ensure_ascii=False))' "$URLS_JSON" "$MODE")"

curl --fail --silent --show-error \
  -X POST "$CRAWLER_API_URL" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD"
printf '\n'
