---
name: gkb-subtree-sync
description: Sync current project directory into GKB monorepo subtree safely. Use when pushing local changes under amz-sif-crawler/ to gkb master via scripts/sync_to_gkb_subtree.sh, especially after multi-file local changes, non-fast-forward confusion, or HTTP 413 oversized push failures.
---

# GKB Subtree Sync

Follow this runbook to publish current project directory changes into `gkb/master` as subtree path `amz-sif-crawler/`.

## Enforce Safety Rules

- Commit local changes before subtree sync.
- Never use direct `git push gkb main:master` for this workflow.
- Use `scripts/sync_to_gkb_subtree.sh` as the source of truth.
- Run the script from the project directory you want to publish so it can split the correct subtree prefix.

## Execute Standard Flow

1. Check status: `git status --short`.
2. Review remotes: `git remote -v`.
3. Commit all intended local changes:
   - `git add -A`
   - `git commit -m "<message>"`
4. Confirm clean worktree: `git status --short` must be empty.
5. Run subtree sync:
   - `env GIT_TERMINAL_PROMPT=0 scripts/sync_to_gkb_subtree.sh`
6. Verify success from output:
   - Expect `To ... master -> master`
   - Expect final line: `Done. Local repo remains in-place under amz-sif-crawler/; remote updated under amz-sif-crawler/.`

## Handle Known Failures

### Non-fast-forward from direct push

Symptom:
- `! [rejected] main -> master (non-fast-forward)`

Action:
- Stop using direct push.
- Re-run `scripts/sync_to_gkb_subtree.sh`.

### HTTP 413 (payload too large)

Symptom:
- `error: RPC failed; HTTP 413`

Action:
1. Keep large archives local (do not track in Git):
   - Ensure `.gitignore` includes `profile_bundles/*.tar.gz`.
2. Untrack existing archives while keeping local files:
   - `git rm --cached profile_bundles/amazon.tar.gz profile_bundles/sif.tar.gz`
3. Commit fix:
   - `git add .gitignore README.md`
   - `git commit -m "chore: keep profile archives local to avoid oversized pushes"`
4. Re-run subtree sync.

## Post-sync Quick Checks

- Local latest commits: `git log --oneline -n 3`
- Optional remote head check: `git ls-remote --heads gkb master`
