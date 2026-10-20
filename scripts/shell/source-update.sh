#!/usr/bin/env bash
# Source maintenance only: never sync dependencies, build, change runtime config, or
# restart Condor. The running process keeps its code until `make restart`.
set -euo pipefail
# shellcheck source=scripts/shell/lib/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

action=${1:-}
case "$action" in
    check|update) ;;
    *) echo "Usage: bash scripts/shell/source-update.sh {check|update}" >&2; exit 1 ;;
esac

# The configured URL, not `remote get-url`, which would apply insteadOf rewrites.
case "$(git config --get remote.origin.url)" in
    https://github.com/rehuony/condor|https://github.com/rehuony/condor.git|git@github.com:rehuony/condor.git|ssh://git@github.com/rehuony/condor.git) ;;
    *) echo "Error: origin must point to rehuony/condor." >&2; exit 1 ;;
esac

branch=$(git branch --show-current)
# Runtime state (.env, config.yml, data/, .condor/) is ignored, so it never counts.
changes=$(git status --porcelain --untracked-files=normal)

if [ "$action" = update ]; then
    [ "$branch" = main ] || { echo "Error: update requires the main branch." >&2; exit 1; }
    [ -z "$changes" ] || { echo "Error: commit or stash local changes before updating." >&2; exit 1; }
    git pull --ff-only origin main
    echo "Source updated. Condor keeps running the old code until 'make restart',"
    echo "which syncs dependencies and rebuilds the dashboard on the way up."
    exit 0
fi

if [ "$(git rev-parse --is-shallow-repository)" = true ]; then
    echo "Error: source comparison requires full Git history; deepen the checkout first." >&2
    exit 1
fi
current=$(git rev-parse --verify HEAD)
# Read this fetch's commit rather than a potentially stale origin/main ref.
git fetch --no-tags --quiet origin main
latest=$(git rev-parse --verify FETCH_HEAD)
if [ "$current" = "$latest" ]; then
    state=up_to_date
elif git merge-base --is-ancestor "$current" "$latest"; then
    state=update_available
elif git merge-base --is-ancestor "$latest" "$current"; then
    state=ahead
else
    state=diverged
fi
printf 'Source status: %s\nBranch: %s\nCurrent revision: %s\nRemote main: %s\n' \
    "$state" "${branch:-detached}" "$current" "$latest"
if [ -n "$changes" ]; then
    echo "Working tree: modified; this comparison describes commits only."
else
    echo "Working tree: clean"
fi
echo "This checks the checkout, not the running process. Use make update, then make restart."
