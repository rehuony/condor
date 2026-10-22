# Repository scripts

Use `make help` from the repository root for routine commands. Every Make target is one
line that calls a script here; entry points resolve the checkout from their own location,
so they also work as `bash /path/to/condor/scripts/shell/<script>.sh`.

```text
scripts/
└── shell/
    ├── setup.sh             # Interactive setup wizard (make setup)
    ├── dev.sh               # install, setup-chrome, doctor, pick-model, refresh-codex
    ├── service.sh           # run, run-fg, stop, restart, status, logs (tmux session)
    ├── build-frontend.sh    # Build the dashboard and swap it into frontend/dist (make build)
    ├── source-update.sh     # check / update against origin/main; Git only
    └── lib/
        ├── common.sh        # Repository root, output, prompts, .env reading and writing
        ├── dependencies.sh  # uv, tmux, Node.js/npm and TypeScript installation
        ├── tailnet.sh       # Tailscale detection, enrollment and auth-key prompts
        └── hummingbot-api.sh  # API checkout, setup, deployment and the config.yml entry
```

| File | Responsibility |
| --- | --- |
| `shell/*.sh` | Entry points behind the Makefile; each takes the action names listed in its usage line |
| `shell/lib/*.sh` | Functions sourced by the entry points; sourcing never installs or starts anything |

Shell filenames use lowercase words separated by hyphens. Python helpers the scripts call
live in the `condor` package and run with `uv run python -m`: `condor.setup_llm`,
`condor.setup_chrome`, `condor.setup_api_server` and `condor.doctor`. They import Condor
itself, and some also run inside the bot.

Scripts read `.env` as data and never source or execute it. They do not push, deploy
another service, or restart Condor unless that is their stated action: `source-update.sh`
changes only Git state, and `make setup` deploys Hummingbot API only when asked to,
through that repository's own `make deploy`.

New installations get `config.yml` from [`templates/config.yml`](/templates/config.yml).

When moving an entry point, update its callers, the Makefile, CI, tests, documentation,
and messages that name it together. Keep usage in the [user guide](/docs/user-guide.md)
and operational procedure in the [maintenance guide](/docs/maintenance.md).
