#!/usr/bin/env bash
set -euo pipefail

# Sync the current project directory into gkb:master under amz-sif-crawler/
# Usage:
#   scripts/sync_to_gkb_subtree.sh
#   GKB_REMOTE_URL=http://... scripts/sync_to_gkb_subtree.sh

PREFIX_DIR="amz-sif-crawler"
TARGET_BRANCH="master"
SOURCE_BRANCH="${SOURCE_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
GKB_REMOTE_URL="${GKB_REMOTE_URL:-http://gitlab.geekbuy.cn:8081/AceLink/AI-MCP.git}"

CALL_DIR="$(pwd -P)"
REPO_ROOT="$(git -C "$CALL_DIR" rev-parse --show-toplevel)"
SOURCE_PREFIX_RAW="${SOURCE_PREFIX:-$(git -C "$CALL_DIR" rev-parse --show-prefix)}"
SOURCE_PREFIX="${SOURCE_PREFIX_RAW%/}"
WORK_DIR="$(mktemp -d)"
SPLIT_BRANCH="codex/subtree-sync-${PREFIX_DIR}-$$"
cleanup() {
  if git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$SPLIT_BRANCH"; then
    git -C "$REPO_ROOT" branch -D "$SPLIT_BRANCH" >/dev/null
  fi
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "[1/5] Validating local repository..."
git -C "$REPO_ROOT" rev-parse --verify "$SOURCE_BRANCH" >/dev/null
if [ -n "$(git -C "$REPO_ROOT" status --short)" ]; then
  echo "ERROR: Local worktree is not clean."
  echo "Commit or stash your changes before subtree sync."
  exit 1
fi

SYNC_MODE="subtree-split"
DISPLAY_SOURCE_PREFIX="$SOURCE_PREFIX"
if [ -z "$SOURCE_PREFIX" ] || [ "$SOURCE_PREFIX" = "." ]; then
  SYNC_MODE="standalone-archive"
  DISPLAY_SOURCE_PREFIX="."
fi

echo "      repo root     : $REPO_ROOT"
echo "      source branch : $SOURCE_BRANCH"
echo "      source prefix : $DISPLAY_SOURCE_PREFIX"
echo "      target prefix : $PREFIX_DIR"
echo "      sync mode     : $SYNC_MODE"

if [ "$SYNC_MODE" = "subtree-split" ]; then
  echo "[2/6] Creating split branch from $SOURCE_PREFIX/..."
  # Ignore historical subtree join metadata from the monorepo so split can be
  # recomputed from the current tree shape even when older split hashes are gone.
  git -C "$REPO_ROOT" subtree split --ignore-joins --prefix "$SOURCE_PREFIX" -b "$SPLIT_BRANCH" "$SOURCE_BRANCH" >/dev/null
else
  echo "[2/6] Standalone repo detected; subtree split not required."
fi

echo "[3/6] Cloning remote repository..."
git clone --branch "$TARGET_BRANCH" --single-branch "$GKB_REMOTE_URL" "$WORK_DIR/remote"

echo "[5/6] Syncing subtree into $PREFIX_DIR/..."
if [ "$SYNC_MODE" = "subtree-split" ]; then
  echo "[4/6] Linking local repo as source remote..."
  git -C "$WORK_DIR/remote" remote add local-src "$REPO_ROOT"
  git -C "$WORK_DIR/remote" fetch local-src "$SPLIT_BRANCH"

  HAS_DIR=0
  HAS_SUBTREE_HISTORY=0
  if git -C "$WORK_DIR/remote" ls-tree -d --name-only HEAD "$PREFIX_DIR" | grep -qx "$PREFIX_DIR"; then
    HAS_DIR=1
  fi
  if git -C "$WORK_DIR/remote" log --grep="git-subtree-dir: $PREFIX_DIR" --oneline -n 50 | grep -q .; then
    HAS_SUBTREE_HISTORY=1
  fi

  if [ "$HAS_SUBTREE_HISTORY" -eq 1 ]; then
    git -C "$WORK_DIR/remote" subtree pull --prefix "$PREFIX_DIR" local-src "$SPLIT_BRANCH" --squash -m "sync($PREFIX_DIR): from ${SOURCE_PREFIX}@${SOURCE_BRANCH}"
  else
    if [ "$HAS_DIR" -eq 1 ]; then
      # Existing folder is not managed by git subtree yet; convert it once.
      git -C "$WORK_DIR/remote" rm -r "$PREFIX_DIR"
      git -C "$WORK_DIR/remote" commit -m "chore($PREFIX_DIR): prepare subtree migration"
    fi
    git -C "$WORK_DIR/remote" subtree add --prefix "$PREFIX_DIR" local-src "$SPLIT_BRANCH" --squash -m "init($PREFIX_DIR): import from ${SOURCE_PREFIX}@${SOURCE_BRANCH}"
  fi
else
  rm -rf "$WORK_DIR/remote/$PREFIX_DIR"
  mkdir -p "$WORK_DIR/remote/$PREFIX_DIR"
  git -C "$REPO_ROOT" archive --format=tar "$SOURCE_BRANCH" | tar -x -C "$WORK_DIR/remote/$PREFIX_DIR"

  if [ -n "$(git -C "$WORK_DIR/remote" status --short -- "$PREFIX_DIR")" ]; then
    git -C "$WORK_DIR/remote" add "$PREFIX_DIR"
    git -C "$WORK_DIR/remote" commit -m "sync($PREFIX_DIR): from standalone ${SOURCE_BRANCH}@$(git -C "$REPO_ROOT" rev-parse --short "$SOURCE_BRANCH")"
  else
    echo "      no remote changes detected under $PREFIX_DIR/"
  fi
fi

echo "[6/6] Pushing to gkb $TARGET_BRANCH..."
git -C "$WORK_DIR/remote" push origin "$TARGET_BRANCH"

echo "Done. Local repo remains in-place under $DISPLAY_SOURCE_PREFIX/; remote updated under $PREFIX_DIR/."
