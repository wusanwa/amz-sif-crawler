#!/usr/bin/env bash
set -euo pipefail

# Pull amz-sif-crawler/ from gkb:master into the current standalone repo.
# Usage:
#   scripts/pull_from_gkb_subtree.sh
#   GKB_REMOTE_URL=http://... scripts/pull_from_gkb_subtree.sh
#   TARGET_BRANCH=master LOCAL_BRANCH=master scripts/pull_from_gkb_subtree.sh

PREFIX_DIR="amz-sif-crawler"
TARGET_BRANCH="${TARGET_BRANCH:-master}"
LOCAL_BRANCH="${LOCAL_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
GKB_REMOTE_URL="${GKB_REMOTE_URL:-http://gitlab.geekbuy.cn:8081/AceLink/AI-MCP.git}"

CALL_DIR="$(pwd -P)"
REPO_ROOT="$(git -C "$CALL_DIR" rev-parse --show-toplevel)"
WORK_DIR="$(mktemp -d)"
LOCAL_TMP_REMOTE="codex-gkb-pull-$$"
REMOTE_SPLIT_BRANCH="codex/subtree-pull-${PREFIX_DIR}-$$"

cleanup() {
  git -C "$REPO_ROOT" remote remove "$LOCAL_TMP_REMOTE" >/dev/null 2>&1 || true
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "[1/6] Validating local repository..."
git -C "$REPO_ROOT" rev-parse --verify "$LOCAL_BRANCH" >/dev/null
if [ -n "$(git -C "$REPO_ROOT" status --short)" ]; then
  echo "ERROR: Local worktree is not clean."
  echo "Commit or stash your changes before pulling subtree updates."
  exit 1
fi

echo "      local repo    : $REPO_ROOT"
echo "      local branch  : $LOCAL_BRANCH"
echo "      remote branch : $TARGET_BRANCH"
echo "      subtree dir   : $PREFIX_DIR"

echo "[2/6] Cloning remote repository..."
git clone --branch "$TARGET_BRANCH" --single-branch "$GKB_REMOTE_URL" "$WORK_DIR/remote"

echo "[3/6] Splitting remote subtree $PREFIX_DIR/..."
git -C "$WORK_DIR/remote" subtree split --ignore-joins --prefix "$PREFIX_DIR" -b "$REMOTE_SPLIT_BRANCH" "$TARGET_BRANCH" >/dev/null
REMOTE_SPLIT_HASH="$(git -C "$WORK_DIR/remote" rev-parse "$REMOTE_SPLIT_BRANCH")"
echo "      remote split  : $REMOTE_SPLIT_HASH"

echo "[4/6] Fetching split history into local repo..."
git -C "$REPO_ROOT" remote add "$LOCAL_TMP_REMOTE" "$WORK_DIR/remote"
git -C "$REPO_ROOT" fetch "$LOCAL_TMP_REMOTE" "$REMOTE_SPLIT_BRANCH"

echo "[5/6] Merging subtree updates into $LOCAL_BRANCH..."
git -C "$REPO_ROOT" checkout "$LOCAL_BRANCH" >/dev/null
if git -C "$REPO_ROOT" merge-base --is-ancestor FETCH_HEAD HEAD; then
  echo "      already up to date"
elif git -C "$REPO_ROOT" merge-base --is-ancestor HEAD FETCH_HEAD; then
  git -C "$REPO_ROOT" merge --ff-only FETCH_HEAD
else
  git -C "$REPO_ROOT" merge --no-edit FETCH_HEAD
fi

echo "[6/6] Done."
echo "Local repo updated from $GKB_REMOTE_URL:$TARGET_BRANCH -> $PREFIX_DIR/"
