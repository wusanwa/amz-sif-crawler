#!/usr/bin/env bash
set -euo pipefail

# Sync current standalone repo into gkb:master under amz-sif-crawler/
# Usage:
#   scripts/sync_to_gkb_subtree.sh
#   GKB_REMOTE_URL=http://... scripts/sync_to_gkb_subtree.sh

PREFIX_DIR="amz-sif-crawler"
TARGET_BRANCH="master"
SOURCE_BRANCH="${SOURCE_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
GKB_REMOTE_URL="${GKB_REMOTE_URL:-http://gitlab.geekbuy.cn:8081/AceLink/AI-MCP.git}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "[1/5] Validating local repository..."
git -C "$REPO_ROOT" rev-parse --verify "$SOURCE_BRANCH" >/dev/null

echo "[2/5] Cloning remote repository..."
git clone --branch "$TARGET_BRANCH" --single-branch "$GKB_REMOTE_URL" "$WORK_DIR/remote"

echo "[3/5] Linking local repo as source remote..."
git -C "$WORK_DIR/remote" remote add local-src "$REPO_ROOT"
git -C "$WORK_DIR/remote" fetch local-src "$SOURCE_BRANCH"

echo "[4/5] Running subtree pull into $PREFIX_DIR/..."
if [ ! -d "$WORK_DIR/remote/$PREFIX_DIR" ]; then
  mkdir -p "$WORK_DIR/remote/$PREFIX_DIR"
  git -C "$WORK_DIR/remote" add "$PREFIX_DIR"
  git -C "$WORK_DIR/remote" commit -m "chore: create $PREFIX_DIR directory" || true
fi

git -C "$WORK_DIR/remote" subtree pull --prefix "$PREFIX_DIR" local-src "$SOURCE_BRANCH" --squash -m "sync($PREFIX_DIR): from local $SOURCE_BRANCH"

echo "[5/5] Pushing to gkb $TARGET_BRANCH..."
git -C "$WORK_DIR/remote" push origin "$TARGET_BRANCH"

echo "Done. Local repo remains standalone; remote updated under $PREFIX_DIR/."
