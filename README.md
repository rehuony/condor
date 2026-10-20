# Condor

A personal Telegram bot and web dashboard for monitoring and trading with Hummingbot via the **Hummingbot API**.

## Repository and provenance

This project is maintained for the owner's personal use at [rehuony/condor](https://github.com/rehuony/condor). Development focuses on daily workflows, reliability, privacy, and personal experience. External issues, feature requests, pull requests, and community support are not accepted.

The `main` branch is the development and deployment branch. Changes are published as ordinary commits; updates follow this repository's `origin/main`. Each subsequent change adds its own commit to the maintained history. See [Maintenance and deployment](docs/maintenance.md) for development, validation, updates, and recovery.

The original project is [hummingbot/condor](https://github.com/hummingbot/condor). This repository's development is based on upstream commit [`564f35dd25f1e0028bf43d048bdfb45dfeecde7a`](https://github.com/hummingbot/condor/commit/564f35dd25f1e0028bf43d048bdfb45dfeecde7a) (2026-10-16, “Merge pull request #243 from hummingbot/fix/update-flow-hardening”). Independent development continues on `main`. Later upstream commits are not part of that baseline. Original copyright and license notices remain in [LICENSE](LICENSE).

Usage telemetry and conversation publishing have been removed. See [Privacy and data handling](PRIVACY.md) for local storage and the external services used by normal features.

> **Why we recommend Tailscale for production**
>
> Condor controls real trading through Hummingbot API: orders, balances, bots, and stored exchange keys. That has always required strong passwords and careful configuration—but **the risk surface has grown**. Trading agents, MCP tools, and other AI assistants make powerful API actions easier to trigger, while cloud VPSes are constantly scanned for open ports like **8000**.
>
> **Tailscale is one safeguard you can add**: it puts the API on a private encrypted network so only your devices can reach it, without publishing port 8000 to the internet. It does **not** replace proper security—use strong API and config passwords, and avoid exposing sensitive services publicly. Tailscale also works when Condor and the API run on the **same machine**.
>
> Setup: [Secure Connection via Tailscale](#secure-connection-via-tailscale) · [Hummingbot API Tailscale guide](https://hummingbot.org/hummingbot-api/tailscale/)

## Features

- **Portfolio Dashboard** - Comprehensive portfolio view with PNL tracking, 24h changes, and graphical analysis
- **Bot Monitoring** - Track active Hummingbot trading bots with real-time status and metrics
- **CLOB Trading** - Place orders on centralized exchanges (Binance, Bybit, etc.) with interactive menus
- **DEX Trading** - Swap tokens and manage CLMM liquidity positions via Gateway
- **Web Dashboard** - Browser UI (via **`/web`**) with a live trade panel that covers both CEX and DEX venues: order book trading, DEX swaps, and CLMM liquidity positions created and drawn directly on the chart
- **Configuration** - Manage API servers, exchange credentials, and Gateway through Telegram (`/servers`, `/keys`, `/gateway`)
- **AI Assistant** - Natural language trading help via **`/agent`** (optional OpenAI or OpenRouter keys, or any custom OpenAI-compatible endpoint like Venice AI; MCP tools when configured)
- **Telegram Attachments** - Send images, albums, audio, and text documents to the assistant. See [Telegram conversations](docs/telegram-conversations.md) for formats, limits, and controls.
- **Chart Delivery** - Authorized agents can send chart images to Telegram and attach them to dashboard conversations. See [image export and delivery](agents/_shared/skills/charting/SKILL.md#image-export-and-delivery).
- **AI Agents** - Domain agents that can be asked, delegated to, or run on a loop — each can author its own tick strategy (analysis order, decision rules, risk limits) and run it autonomously with dry-run support

## What you need

- A **Mac** or **Linux** computer (Windows users: install **WSL2** with Ubuntu, then use Terminal inside Ubuntu).
- The **Terminal** app open.
- **Git**, **Make**, and **curl** installed. The setup wizard installs missing runtime tools, including uv, Node.js, and tmux, using your system's package manager where needed.
- A **stable internet** connection.
- For **Hummingbot API** (the API-only install below, or if you choose to add the API during Condor setup): **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** (Mac/Windows) or Docker on Linux, **installed and running** on that machine before you run the command.
- **[Tailscale](https://tailscale.com) account** (free tier is enough) — **recommended for production**, especially when Condor and the API run on different machines. Create a [reusable auth key](https://login.tailscale.com/admin/settings/keys) and enable [MagicDNS](https://login.tailscale.com/admin/dns) before you install.

---

## Install Condor (start here)

### Before you install (production)

1. Create a free account at [tailscale.com](https://tailscale.com)
2. Generate a **reusable** auth key at [Settings → Keys](https://login.tailscale.com/admin/settings/keys) (starts with `tskey-auth-`)
3. Enable **[MagicDNS](https://login.tailscale.com/admin/dns)** in the Tailscale admin console

Open Terminal in the parent folder where you want to keep the checkout:

```bash
git clone --branch main https://github.com/rehuony/condor.git
cd condor
make install
make run
```

`make install` runs this checkout's setup wizard and installs the Python and frontend dependencies. The wizard asks **how you will use Condor**: **Telegram** (a bot you control from your phone) or **Local** (no Telegram, see [Local mode](#local-mode-no-telegram) below). Telegram mode needs your bot token and Telegram user id. The wizard can connect to an existing **Hummingbot API**, or deploy it on the **same machine** when Docker is available.

Configure Tailscale when prompted if you need private remote access. `make run` builds the dashboard and starts Condor in tmux. Run `make doctor` to check the result, then continue to **After installation** below. You can rerun `make setup` to change the configuration without cloning the repository again.

---

## Install only Hummingbot API

**Hummingbot API** remains a separate runtime dependency. To deploy it on another machine, follow the installation instructions in the [Hummingbot API repository](https://github.com/hummingbot/hummingbot-api), with Docker installed and running on that host. Configure private access using its [Tailscale guide](https://hummingbot.org/hummingbot-api/tailscale/) when needed.

Then run `make setup` in your Condor checkout and choose the existing API connection, or add its URL and credentials through `/servers`. Use the actual hostname shown by the API deployment; `hummingbot-api` is only an example. Updating Condor does not require updating Hummingbot API or Gateway at the same time.

---

## After installation

The following applies after **Install Condor**. If you used **Install only Hummingbot API**, use your API host's health checks and client docs instead; point Condor (or other clients) at that API when you connect them.

| Where you connect from | API URL |
|------------------------|---------|
| Same machine as the API | `http://localhost:8000` |
| Another device on your tailnet (Condor, browser) | `http://<api-tailnet-hostname>:8000` |

- Open the **Telegram** chat with your Condor bot. When startup succeeds, admins receive a message such as **"Condor is online and ready."**
- **Logs:** Condor runs in a **tmux** session named `condor`. Attach with `make logs` (or `tmux attach -t condor`). Detach without stopping the bot: **Ctrl+B**, then **D**.
- **Control:** `make run` starts Condor in that session, `make stop` stops it, `make restart` does both, `make status` shows whether it is up. Use `make run-fg` to run in the foreground when a startup error needs debugging.
- In Telegram, use **`/servers`** for Hummingbot API URLs and auth, **`/keys`** for exchange credentials, and **`/gateway`** for DEX setup (or **`/start`** for the setup shortcuts) so commands like `/portfolio` and `/trade` can reach your stack.
- If Condor and the API are on **different machines**, install [Tailscale](https://tailscale.com/download) on the Condor host and add the API in **`/servers`** with its actual tailnet hostname. See [Secure Connection via Tailscale](#secure-connection-via-tailscale) below.
- **Check the install:** `make doctor` re-verifies dependencies, `.env`/`config.yml`, the AI model, the dashboard's network binding, Tailscale, and whether every configured Hummingbot API server is actually reachable and authenticating. Run it any time something stops working — it is read-only.
- **Refresh Codex:** If a newer model fails because the Codex ACP bridge is outdated, run `make refresh-codex` to update the bridge and its bundled Codex CLI, then start a new Codex session in Condor. Existing sessions keep the bridge they already started.
- If something fails, see **Troubleshooting** below.

## Updates

Deployments use `main` from [rehuony/condor](https://github.com/rehuony/condor), with that repository configured as `origin`. The dashboard update flow and Telegram `/update` fetch the current branch from `origin` and apply fast-forward updates. Keep deployment checkouts on `main` and publish code changes through this repository before updating them.

Back up `.env`, `config.yml`, `.condor/`, `data/`, `reports/`, and any configured external runtime directories before deployment changes. Review and preserve any local edits before updating. Follow [Maintenance and deployment](docs/maintenance.md) for manual updates, validation, migration of existing checkouts, and recovery.

## Local mode (no Telegram)

Answer **2) Local** at the first setup question and Condor runs with **no Telegram bot at all**: no token, no `/web` login link, no polling. `make run` boots the process and the dashboard is at **http://localhost:8088**, already logged in. Everything else is the same product — portfolio, bots, agents, routines, trading — and notifications that would have gone to Telegram land in the dashboard's notification bell instead.

Use it for development, or to try Condor before deciding whether you want a bot.

> **Local mode has no login.** Anyone who can reach the port has full trading
> control. That is why it binds **`127.0.0.1` only** — the dashboard is reachable
> from the machine it runs on and nowhere else, not from your LAN and not from
> the internet.
>
> `WEB_HOST=0.0.0.0` in `.env` is the one, deliberate way to expose it. Only set
> it behind something that authenticates for you (Tailscale, an SSH tunnel, an
> authenticating reverse proxy). If you want Condor reachable from your phone,
> the answer is usually Telegram mode, not this.

The mode is **explicit** and is never guessed from whether a token is present: an
install configured for Telegram whose `TELEGRAM_TOKEN` goes missing **exits at
boot** telling you to run `make setup`. It never falls back to a login-less
dashboard.

Local mode logs in as `ADMIN_USER_ID` — the same id `config.yml`, your servers,
preferences and defaults already key on. `make setup` writes `ADMIN_USER_ID=1`
for an install that never had a Telegram id; an install that has one **keeps
it**, so switching modes keeps everything you had.

Switching later is a `make setup` re-run — it shows the current mode and offers
the other one — or editing `CONDOR_MODE` in `.env`. Your `TELEGRAM_TOKEN` is left
in place either way, so switching back is one line.

A mode that cannot work stops at boot, in `make run`, naming the `.env` line to
fix: telegram mode with no token, an `ADMIN_USER_ID` that is not a positive
integer, or a local user who is not in `config.yml`. None of it is deferred to
the browser.

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome, access requests, and shortcuts to servers, keys, and Gateway |
| `/portfolio` | Portfolio dashboard with PNL indicators, holdings, and graphs |
| `/bots` | Active bots with status and metrics |
| `/new_bot` | Create bot configurations |
| `/executors` | Deploy and manage trading executors |
| `/trade` | CEX and DEX trading menu (spot & perpetual orders, positions, swaps) |
| `/swap` | Same trading flow as `/trade` (convenient alias) |
| `/lp` | DEX liquidity pool management (positions, pools) |
| `/routines` | Auto-discoverable Python scripts with scheduling |
| `/agent` | AI trading assistant (optional LLM keys in `.env`) |
| `/servers` | Hummingbot API servers (add, edit, default, status) |
| `/keys` | Exchange API credentials per account |
| `/gateway` | Gateway configuration for DEX |
| `/web` | Time-limited link to the web dashboard |
| `/admin` | Admin panel: users and access (admin role only) |
| `/update` | Check for updates and restart (admin role only) |

## Architecture

```
Telegram → Condor Bot → Hummingbot API → Trading Bots
                     ↘ Gateway → DEX Protocols
```

All commands use direct API calls via `hummingbot_api_client` with interactive button menus.

## Project Structure

```
condor/
├── handlers/                    # Telegram command handlers
│   ├── portfolio.py            # /portfolio command with dashboard
│   ├── bots/                   # Bot monitoring module
│   │   ├── __init__.py         # /bots command
│   │   ├── menu.py             # Bot status display
│   │   └── controllers/        # Bot controller configs
│   ├── cex/                    # CEX trading module (/trade)
│   │   ├── __init__.py         # Main command, callback router
│   │   ├── trade.py            # Order placement
│   │   ├── orders.py           # Order management
│   │   └── positions.py        # Position tracking
│   ├── dex/                    # DEX trading module (/swap, /lp)
│   │   ├── __init__.py         # Main commands, callback router
│   │   ├── swap.py             # Quote, execute, history
│   │   ├── liquidity.py        # LP positions management
│   │   └── pools.py            # Pool info and discovery
│   ├── config/                 # Configuration module (/servers, /keys, /gateway)
│   │   ├── __init__.py         # Main command
│   │   ├── servers.py          # API server management
│   │   ├── api_keys.py         # Exchange credentials
│   │   └── gateway/            # Gateway configuration
│   ├── routines/               # Routines module (/routines)
│   │   └── __init__.py         # Script discovery and execution
│   └── admin/                  # Admin panel (/admin)
├── routines/                   # User-defined automation scripts
├── utils/                      # Utilities
│   ├── auth.py                 # @restricted, @admin_required decorators
│   └── telegram_formatters.py  # Message formatting
├── config_manager.py           # Unified config (servers, users, permissions)
├── hummingbot_api_client/      # API client library
└── main.py                     # Entry point
```

## Handler Features

### Portfolio (`/portfolio`)
- **PNL Indicators** - 24h, 7d, 30d with deposit/withdrawal detection
- **Token Holdings** - Balances with 24h price changes
- **Positions** - Perpetual positions with unrealized PnL
- **LP Positions** - CLMM positions with in-range status
- **Active Orders** - Open order summary
- **Dashboard** - Combined chart with value history, token distribution, account breakdown
- **Settings** - Configure time period (1d, 3d, 7d, 14d, 30d)

### CEX Trading (`/trade`)
- **Overview** - Account balances, positions, orders at a glance
- **Place Orders** - Interactive menu with dual input (buttons + direct text)
  - Toggle: side, order type, position mode
  - Input: connector, pair, amount, price
  - USD notation: `$100` auto-converts to token units
- **Set Leverage** - Configure leverage and position mode per connector
- **Search Orders** - View/filter/cancel orders
- **Manage Positions** - View, trade, close positions with confirmation

### DEX Swaps (`/swap`)
- **Gateway Balances** - Token balances across DEX wallets
- **Swap Quote** - Get quotes before executing
- **Execute Swap** - Perform swaps with slippage control
- **Quick Swap** - Repeat last swap with minimal input

### Liquidity Pools (`/lp`)
- **Pool Discovery** - Search pools by connector and token
- **Pool Info** - Detailed pool stats with liquidity charts
- **LP Positions** - Manage CLMM positions (add, close, collect fees)

### Routines (`/routines`)
- **Auto-Discovery** - Python scripts auto-discovered from `routines/` folder
- **Pydantic Config** - Type-safe configuration with descriptions
- **One-shot Scripts** - Run once, optionally schedule (interval or daily)
- **Continuous Scripts** - Long-running tasks with start/stop control
- **Multi-instance** - Run multiple instances with different configs

### Configuration (`/servers`, `/keys`, `/gateway`)
- **API Servers** (`/servers`) - Add, modify, delete Hummingbot API servers
  - Real-time status checking (online/offline/auth error)
  - Set default server
  - Progressive form for adding servers
- **API Keys** (`/keys`) - Manage exchange credentials per account
  - View connected exchanges
  - Add new credentials (field-by-field input)
  - Delete credentials with confirmation
- **Gateway** (`/gateway`) - DEX connector settings and Gateway status

## User Preferences

Preferences are automatically saved and persist across sessions:

- **Portfolio** - Graph time period (days, interval)
- **CEX** - Active account, last order parameters
- **DEX** - Default network/connector, last swap parameters
- **General** - Active server

## Security

- **Admin Whitelist** - Only `ADMIN_USER_ID` has initial access
- **Role-Based Access** - Admin, User, Pending, Blocked roles
- **@restricted Decorator** - Applied to all command handlers
- **Secret Masking** - Passwords hidden in UI

## Configuration Files

### `.env`
```bash
CONDOR_MODE=telegram                     # telegram (default) or local — see Local mode
TELEGRAM_TOKEN=your_bot_token            # Required unless CONDOR_MODE=local
ADMIN_USER_ID=123456789                  # Your Telegram user id. Local mode logs in
                                         # as this same id (1 if you never had one)
OPENAI_API_KEY=sk-...                    # Optional, for AI features
OPENROUTER_API_KEY=sk-or-...             # Optional, unlocks the OpenRouter LLM picker
WEB_HOST=0.0.0.0                         # Optional. Overrides the dashboard bind
                                         # address. Local mode binds 127.0.0.1;
                                         # read the Local mode warning first.
```

Configure the listener separately from the public dashboard URL, for example behind a reverse proxy:

```bash
WEB_HOST=127.0.0.1
WEB_PORT=8080
WEB_URL=https://dashboard.example.com
```

`WEB_HOST` and `WEB_PORT` control the listener; `WEB_URL` sets public links.
Without `WEB_PORT`, the listener uses the URL's port (443 for HTTPS, 80 for HTTP),
or 8088 if no URL is set. Restart after changing these values. Reverse proxies
must forward WebSocket upgrades. Telegram authentication requires `CONDOR_MODE=telegram`.

> **OpenRouter:** Add `OPENROUTER_API_KEY` to `.env`, then in `/agent → Change LLM`
> select **OpenRouter — Pick Model**. The picker fetches the live catalog and shows
> only models that support tool-calling. Get a key at
> [openrouter.ai/keys](https://openrouter.ai/keys).

> **Custom OpenAI-compatible endpoints:** Connect Venice AI, Together, Fireworks,
> or your own vLLM / LM Studio server. Add one from either surface — they share
> the same saved endpoints:
> - **Telegram:** `/agent → Change LLM → Custom — OpenAI-compatible API → + Add endpoint`
> - **Web:** `Settings → LLM Endpoints → Add endpoint`
>
> You give a base URL (e.g. `https://api.venice.ai/api/v1`) and an optional API
> key; Condor validates them by fetching `{base_url}/models` and lists the chat
> models it finds. Endpoints are saved per user under a short nickname, and the
> selected model is stored as `custom@<nickname>:<model-id>`.
>
> Optional environment variables:
> ```bash
> CUSTOM_LLM_BASE_URL=https://api.venice.ai/api/v1   # Headless fallback, no UI setup
> CUSTOM_LLM_API_KEY=sk-...
> CUSTOM_LLM_BLOCK_PRIVATE_URLS=true                 # Refuse loopback/RFC1918 targets
> ```
> Leave `CUSTOM_LLM_BLOCK_PRIVATE_URLS` unset for personal deployments — it is
> what allows pointing at `http://localhost:8000/v1`. Turn it on for shared
> multi-user instances, where a user could otherwise aim the fetcher at hosts
> on your internal network. Cloud metadata addresses are always refused.

### `config.yml` (auto-created on first run)
```yaml
servers:
  main:
    host: localhost
    port: 8000
    username: admin
    password: admin
default_server: main
admin_id: 123456789
users: {}
server_access: {}
chat_defaults: {}
audit_log: []
```

## Secure Connection via Tailscale

[Tailscale](https://tailscale.com) creates a private WireGuard network (tailnet) so Condor can reach Hummingbot API securely without exposing port 8000 publicly.

Use this when:
- Hummingbot API is running on a remote server or VPS
- You want an encrypted private connection without opening firewall ports
- Condor runs on your laptop and the API runs in the cloud (most common production layout)

Tailscale also works when Condor and the API run on the **same machine**—you still get a stable hostname and avoid publishing port 8000 publicly.

### Prerequisites: Get a Tailscale auth key

1. Create a free account at [tailscale.com](https://tailscale.com)
2. Go to **Settings → Keys**: [tailscale.com/admin/settings/keys](https://tailscale.com/admin/settings/keys)
3. Click **Generate auth key** — check **Reusable** for multiple deployments
4. Copy the key (starts with `tskey-auth-`)
5. Enable **[MagicDNS](https://login.tailscale.com/admin/dns)** in the Tailscale admin console

### On the API server

Follow [Install only Hummingbot API](#install-only-hummingbot-api) and its Tailscale setup instructions. Confirm the API host is reachable on your tailnet, and note its actual hostname. A separate API Tailscale node is optional when Condor and the API share a machine.

### On the Condor machine

1. Install [Tailscale](https://tailscale.com/download) and sign in to the **same account**
2. In Telegram, open **`/servers`** and add the API with:
   - **Host**: the API's MagicDNS hostname (for example, `hummingbot-api`)
   - **Port**: `8000`
   - **Username / Password**: same as the API `.env`

Test from the Condor host:

```bash
curl -u YOUR_USERNAME:YOUR_PASSWORD http://hummingbot-api:8000/
```

### Configure through `make setup`

Run `make setup` — the wizard supports two Tailscale scenarios:

**Scenario A — Deploy Hummingbot API locally with Tailscale**

Choose `Y` to deploy Hummingbot API locally, then `y` when asked about Tailscale. The wizard will:
- Configure the local `hummingbot-api` deployment
- Connect this machine to Tailscale, reusing an existing connection when available
- Keep Condor's API connection on `localhost`
- Offer a separate API Tailscale node if other devices need to reach the API directly

**Scenario B — Connect to a remote Hummingbot API via Tailscale**

Choose `N` to skip local deployment, enter the remote API URL, then `y` when asked about Tailscale. The wizard will:
- Connect this machine to Tailscale
- Update `config.yml` to use the actual Tailscale MagicDNS hostname of the remote API

The remote machine must be running [hummingbot-api](https://github.com/hummingbot/hummingbot-api) with Tailscale enabled on the same tailnet.

### Network layout

```
[Condor]  tailnet: condor  ──WireGuard──►  [API]  tailnet: hummingbot-api
                           http://hummingbot-api:8000
```

Both machines must be on the same Tailscale account.

### Verify the connection

```bash
tailscale status
```

For separate machines, confirm the Condor host and the API host appear as connected peers. Run `make doctor` in the Condor checkout to verify API authentication and connectivity as well.

**Do not open port 8000 on your public firewall** when Tailscale is enabled. Allow SSH (port 22) for server administration only.

## Troubleshooting

Start with `make doctor` — it checks dependencies, config, the AI model, port
exposure, Tailscale, and API connectivity in one pass, and names the fix for
whatever it finds. The table below covers the rest.

| Issue | Solution |
|-------|----------|
| Bot not responding | Check `TELEGRAM_TOKEN` and `ADMIN_USER_ID` in `.env` |
| Exits at boot: `TELEGRAM_TOKEN is not set` | Telegram mode needs a token. Run `make setup` — or choose Local mode there if you do not want a bot |
| Local mode: dashboard not reachable from another device | Intended — local mode binds `127.0.0.1`. See [Local mode](#local-mode-no-telegram) before changing `WEB_HOST` |
| Access pending | Admin must approve user via /config > Admin Panel |
| Commands failing | Verify Hummingbot API is running |
| Connection refused | Check server host:port in `/servers`; use `hummingbot-api` (not `localhost`) when API is on another machine via Tailscale |
| Auth error | Verify server credentials match the API `.env` |
| DEX features unavailable | Ensure Gateway is configured and running |
| Tailscale: name `hummingbot-api` does not work | Enable **MagicDNS** in [Tailscale DNS settings](https://login.tailscale.com/admin/dns) |
| Tailscale: can't reach API | Run `tailscale status` — confirm both peers are connected; on API server run `make tailscale-status` |
| Tailscale: auth key rejected | Key must start with `tskey-auth-`, check expiry in Tailscale admin |
| API still reachable on public IP | Remove port **8000** from your cloud provider's firewall / security group |

## Development

### Run from source

Use the [installation steps](#install-condor-start-here) to prepare a checkout. Start a feature branch from this repository's `main`, add ordinary commits, and validate the change before merging and publishing. The full workflow and required checks are in [Maintenance and deployment](docs/maintenance.md).

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/your-change
```

Use `make run-fg` for foreground debugging, `make test` for backend tests, and the frontend checks documented in the maintenance guide. Hummingbot API and Gateway are managed as separate dependencies.

### Flow Documentation
See `flows/` directory for detailed command flow documentation:
- Each handler has a corresponding `*_flow.txt` file
- `common_patterns.txt` documents shared patterns across handlers

### Adding New Features
1. Create handler in `handlers/` (or subdirectory for complex features)
2. Register in `main.py`
3. Follow patterns in `flows/common_patterns.txt`
4. Document flow in `flows/`

## Personal maintenance references

- **Installation**: [Install Condor](#install-condor-start-here)
- **Operations and development**: [Maintenance and deployment](docs/maintenance.md)
- **Project documentation**: [docs/](docs/)
- **API networking**: [Hummingbot API Tailscale guide](https://hummingbot.org/hummingbot-api/tailscale/)

---

**Built with [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) and [Hummingbot](https://hummingbot.org/)**
