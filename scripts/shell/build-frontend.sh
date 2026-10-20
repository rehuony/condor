#!/usr/bin/env bash
# Build the dashboard into frontend/dist.new and swap it in, as the in-process
# updater does (utils.updater.build_frontend). vite's emptyOutDir would otherwise
# empty dist in place, leaving a Condor started any other way (run-fg, a supervisor,
# a second checkout) serving nothing for the length of the build, or for good if the
# build fails.
#
# Dependencies are reinstalled when the lockfile moved, not only when node_modules is
# absent: npm rewrites node_modules/.package-lock.json on every install, which makes
# it the record of what is actually installed.
set -euo pipefail
# shellcheck source=scripts/shell/lib/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
load_node

cd "$REPO_ROOT/frontend"
if [ ! -f node_modules/.package-lock.json ] \
   || [ package-lock.json -nt node_modules/.package-lock.json ]; then
    npm ci
fi
rm -rf dist.new
npm run build -- --outDir dist.new --emptyOutDir
rm -rf dist.old
if [ -d dist ]; then
    mv dist dist.old
fi
mv dist.new dist
