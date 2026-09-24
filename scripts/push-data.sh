#!/usr/bin/env bash
# Publish the contents of ./data (pull log, HALT flag if any, raw + published snapshots)
# as a single commit on the repo's `data` branch, replacing what was there.
#
# One commit, force-pushed, so snapshots never pile up in git history and the repo stays
# small. Nothing is lost: the pull log and the retained snapshots are all in ./data.
#
# Remote: $RR_PUSH_URL if set (the workflow sets it with its token), otherwise this repo's origin.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${RR_DATA_DIR:-$REPO_ROOT/data}"
REMOTE_URL="${RR_PUSH_URL:-$(git -C "$REPO_ROOT" remote get-url origin)}"
MESSAGE="${1:-Data as of $(date -u +%Y-%m-%dT%H:%MZ)}"

cd "$DATA_DIR"
rm -rf .git
git init -q -b data
git config user.name  "${GIT_AUTHOR_NAME:-rr-refresh}"
git config user.email "${GIT_AUTHOR_EMAIL:-rr-refresh@users.noreply.github.com}"
git add -A
git commit -q -m "$MESSAGE"
git push -q --force "$REMOTE_URL" data
rm -rf .git
echo "Pushed data branch: $MESSAGE"
