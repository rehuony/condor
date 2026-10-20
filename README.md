# Condor

A personal Telegram bot and web dashboard for monitoring and trading with Hummingbot through
the [Hummingbot API](https://github.com/rehuony/hummingbot-api).

- Track portfolio balances, PnL, positions and active bots.
- Trade on centralized exchanges, swap on DEXs and manage CLMM liquidity through Gateway.
- Ask, delegate to, or run AI trading agents, with MCP tools and dry-run support.
- Use it from Telegram, from the web dashboard, or locally with no Telegram at all.

Usage telemetry and conversation publishing have been removed. See
[Privacy and data handling](PRIVACY.md) for local storage and the external services used by
normal features.

## Installation

You need macOS or Linux (on Windows, WSL2 with Ubuntu), **Git**, **Make** and **curl**.
The setup wizard installs missing runtime tools — uv, Node.js and tmux — with your
package manager where needed. To deploy Hummingbot API on the same machine, install
Docker with the Compose v2 plugin and start it first.

```bash
git clone --branch main https://github.com/rehuony/condor.git
cd condor
make install
make run
```

`make install` runs the setup wizard, then installs the Python and dashboard dependencies,
the headless Chrome used for charts, and runs `make doctor`. The wizard asks how you will use
Condor — **Telegram** (needs a bot token and your Telegram user id) or **Local** (no bot, see
[Local mode](docs/user-guide.md#local-mode-no-telegram)) — and the AI model, then connects to
an existing Hummingbot API or builds and deploys one next to Condor (`../hummingbot-api`).
Re-run `make setup` at any time; it preserves existing configuration.

> **Use Tailscale in production.** Condor controls real trading: orders, balances, bots and
> stored exchange keys, increasingly through agents and MCP tools. Tailscale keeps the API on
> a private network instead of a public port 8000. It does not replace strong API and config
> passwords. See [Secure connection via Tailscale](docs/user-guide.md#secure-connection-via-tailscale).

## Access

| Surface | Where |
| --- | --- |
| Telegram | Send `/start` to your bot; `/web` sends a time-limited dashboard link |
| Web dashboard | `http://localhost:8088`, or the `WEB_URL` / tailnet address setup configured |
| Local mode | `http://localhost:8088`, already logged in, loopback only |

## Common commands

| Command | Purpose |
| --- | --- |
| `make help` | List commands and their effects; also the default target |
| `make setup` | Run the setup wizard; preserves existing configuration |
| `make install` | Setup, then all dependencies, Chrome and `make doctor` |
| `make run` / `make stop` / `make restart` | Start (building the dashboard first), stop, or both, in tmux session `condor` |
| `make status` / `make logs` | Whether Condor is running; attach to its session |
| `make run-fg` | Run in the foreground to debug a startup failure |
| `make doctor` | Check dependencies, configuration, the AI model, Tailscale and API access |
| `make check-update` | Compare this checkout with `origin/main` |
| `make update` | Fast-forward a clean `main` checkout |
| `make pick-model` | Choose the AI model Condor thinks with |

## Updates

```bash
make check-update
make update
# Review the incoming commits and back up runtime data first.
make restart
make doctor
```

`check-update` reports whether the checkout is current, behind, ahead or diverged, and any
local modifications. `update` requires a clean `main` tracking this repository and only
fast-forwards. Neither touches the running process: `make restart` applies the new code,
syncing dependencies and rebuilding the dashboard on the way up. Admins can also update from
Telegram `/update` or **Settings → Updates** in the dashboard. Hummingbot API is updated on its
own host — see [Hummingbot API](docs/user-guide.md#hummingbot-api).

Back up `.env`, `config.yml`, `.condor/`, `data/`, `reports/` and any configured external
runtime directories before deployment changes; see [maintenance](docs/maintenance.md#deployment-and-updates).

## Documentation

- [User guide](docs/user-guide.md): features, commands, local mode, configuration,
  Hummingbot API, Tailscale, troubleshooting and development.
- [Maintenance](docs/maintenance.md): repository layout, validation, publication,
  deployment and recovery.
- [Scripts](scripts/README.md): what each operational script does.
- [Telegram conversations](docs/telegram-conversations.md) and
  [reply delivery](docs/telegram-reply-delivery.md): attachments, streaming and controls.
- [Agent instructions](AGENTS.md): repository engineering and operational conventions.

## Source and license

Maintained for the owner's personal use at [rehuony/condor](https://github.com/rehuony/condor)
on `main`. External issues, feature requests, pull requests and community support are not
accepted. Changes are published as ordinary commits to `origin/main`.

Original project: [hummingbot/condor](https://github.com/hummingbot/condor). Development is
based on upstream commit
[`564f35dd25f1e0028bf43d048bdfb45dfeecde7a`](https://github.com/hummingbot/condor/commit/564f35dd25f1e0028bf43d048bdfb45dfeecde7a)
(2026-10-16, “Merge pull request #243 from hummingbot/fix/update-flow-hardening”); later
upstream commits are not part of that baseline. Original copyright and license notices remain
in [LICENSE](LICENSE).

Built with [aiogram](https://github.com/aiogram/aiogram) and [Hummingbot](https://hummingbot.org/).
