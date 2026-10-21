# Repository maintenance

The canonical repository is [rehuony/condor](https://github.com/rehuony/condor),
maintained for the owner's personal use. Prioritize personal workflows and
reliability; external issues, feature requests, and pull requests are not accepted.
`main` is the development and release baseline. `origin` points to this repository;
development and deployment do not synchronize another Condor repository. Project
provenance is recorded in the [README](../README.md); usage is in the
[user guide](user-guide.md).

## Repository layout

Product code lives in `main.py`, `config_manager.py` and the `condor/`,
`handlers/`, `routines/`, `agents/`, `mcp_servers/`, `utils/` and `frontend/` trees.
Operational scripts live under [`scripts/shell/`](../scripts/README.md): the Makefile only
dispatches to them, so a target's behavior is read in one script, and the default
target is `make help`. Files copied into a new installation live in `templates/`.
Runtime state (`.env`, `config.yml`, `data/`, `reports/`, `.condor/`) is ignored by Git.

Keep the root free of operational scripts. When moving one, update the Makefile,
CI, tests, documentation and every message that names it in the same change, as
[scripts/README.md](../scripts/README.md) describes.

## Development and publication

One agent owns Git writes while collaborators work on separate files. Inspect
the checkout, remotes, worktrees, and pending Git operations before changing
branches. Preserve unknown local changes; do not automatically stash, reset, or
clean them.

```sh
git status --short --branch
git worktree list
git remote -v
git fetch --prune origin
git switch main
git merge --ff-only origin/main
git switch -c feature/short-description
```

Make focused changes and add ordinary commits using the configured Git identity.
Each subsequent change adds a new commit; ordinary publication preserves the
commits already on `main`. Rewrite published history only when the user explicitly
requests that specific operation. A one-time consolidation does not establish a
recurring publication rule. If a fast-forward fails, inspect and reconcile the
divergence; do not bypass the check. After validation, fast-forward `main` to the
feature branch and publish with `git push origin main` when authorized. Small
changes can be developed directly on `main` under the same validation rules.
Confirm that the published remote SHA matches the intended commit and that CI
passed for that SHA. A successful push does not establish a successful deployment.

Commit, push, deployment, and service restart actions must stay within the user's
request. Preserve existing authorization; analysis alone does not authorize a
publication or a restart.

## Validation

Use the existing environment when it matches the lockfiles. Install dependencies
with `uv sync --locked --dev` and `npm --prefix frontend ci` when required. Update
manifests and lockfiles together for intentional dependency changes.

The CI workflow runs on pushes to `main`. It can also be run
manually from GitHub Actions by selecting CI, Run workflow, and `main`.
Its checks are:

```sh
uv run --locked black --check .
uv run --locked isort --check --skip-gitignore .
uv run --locked python -c 'import config_manager; import handlers; import utils'
uv run --locked pytest --tb=short -q
npm --prefix frontend test
npm --prefix frontend run lint:ci
for script in scripts/shell/*.sh scripts/shell/lib/*.sh; do bash -n "$script"; done
shellcheck -S error -x -P scripts/shell scripts/shell/*.sh scripts/shell/lib/*.sh
make --dry-run setup install check-update update build run stop doctor
```

Also run the frontend production build. On a checkout that serves a dashboard,
use a separate output directory so validation does not replace the live bundle:

```sh
validation_build=$(mktemp -d /tmp/condor-validation-build.XXXXXX)
npm --prefix frontend run build -- --outDir "$validation_build"
git diff --check
git status --short --branch
```

Run targeted regression tests while developing, then the applicable release
checks before publication. Use controlled data for browser checks; do not place
real trades as a test. Documentation-only changes need link and contract checks,
not unrelated behavior tests. Report failed or unavailable checks explicitly.
Keep generated evidence in `.condor/research/` under the
[artifact standard](../agents/_defaults/research-artifacts.md).

## Deployment and updates

Deploy a clean `main` checkout of `rehuony/condor`, tracking `origin/main`. Both
`make update` and the in-app update interface fetch `origin` and fast-forward the
checked-out branch, so each deployment must use `main`. `make check-update` compares
the checkout with `origin/main` and changes only Git metadata. The in-app interface
also installs dependencies, rebuilds the dashboard when needed, and reports when a
process relaunch is required; `make update` does none of that. Code on disk and a
running process are separate states.

Before updating, record the current and target full SHAs and back up runtime
data separately from Git. Preserve `.env`, `config.yml`, `.condor/`, `data/`,
and `reports/`. Include directories selected by `CONDOR_DATA_DIR`,
`CONDOR_REPORTS_DIR`, `CONDOR_RUNTIME_ROOT`, `CONDOR_AGENTS_ROOT`, or other
configured path overrides. Use a consistent snapshot or an authorized
maintenance stop for data that is actively being written. Git branches, tags,
and bundles do not back up ignored configuration or runtime data.

For a manual update, inspect the incoming commits and any data format changes
before applying them:

```sh
make check-update
git log --oneline HEAD..FETCH_HEAD
```

After confirming a clean `main` checkout, passing CI for the target SHA, backups,
and deployment authorization, apply the update in a maintenance window. Handler
hot reload can activate code edits before a full process restart. For a deployment
managed by this repository's tmux commands:

```sh
make stop
make update
uv sync --locked
make doctor
make run
git rev-parse HEAD
make status
```

Stop on any command failure and inspect the cause. `make run` builds the frontend,
installing its locked dependencies when needed, then starts Condor. `make status`
only checks the tmux session: also inspect logs, the actual configured dashboard
port, and required API connectivity. Record the deployed SHA and verification
results. For a different process supervisor, use that deployment's stop/start
commands instead of starting a second instance with tmux.

Condor's frontend and backend are deployed from the same current revision.
Retired Condor protocols and storage migrations are not supported. Keep the
existing runtime data in its current format; inspect any data from another
installation before importing it. External-service compatibility is independent
of this policy.

Hummingbot API, Gateway, and exchange services are separate dependencies. Check
their compatibility and update them deliberately; a Condor source update is not
authorization to replace another service or its data. The in-app updater also
checks and upgrades an outdated Claude ACP bridge, which is installed separately
from the Python lockfile. `make refresh-codex` updates the Codex ACP bridge
explicitly. Review bridge compatibility before an update.

## Deployment checkout alignment

Before using the update interface, verify that the deployment is on `main`,
tracking `origin/main`, and that `origin` is `git@github.com:rehuony/condor.git` or
`https://github.com/rehuony/condor.git`. Fetch with `--prune` and inspect local
changes and branch history.

A clean checkout whose commit is an ancestor of `origin/main` can fast-forward.
For divergent history or conflicting local work, preserve the checkout and use
a fresh clone of this repository's `main` in a separate directory. Migrate
backed-up configuration and runtime data, then verify the new checkout before
retiring the old one. Do not reset or delete unknown work to make an update
succeed.

Coordinate any service transition explicitly and run only one instance against
the same runtime data.

## Recovery

Prefer a reviewed `git revert` on `main`, followed by normal validation and a
normal push. Deploy that new commit through the same fast-forward process.
Do not move published `main` backwards or force deployments to reset their work.

Before reverting a data migration, check whether the previous code can read the
current data. Restore a compatible runtime snapshot only during an authorized
maintenance operation; a source-code revert does not undo data changes. If a
failed rollout requires a temporary deployment of a prior commit, record that
state explicitly and return to a clean tracking `main` before resuming updates.
