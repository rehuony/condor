# User guide

Using and configuring Condor after [installation](/README.md#installation). Repository
operations — validation, publication, deployment and recovery — are in the
[maintenance guide](maintenance.md).

## Features

- **Portfolio Dashboard** - Comprehensive portfolio view with PNL tracking, 24h changes, and graphical analysis
- **Bot Monitoring** - Track active Hummingbot trading bots with real-time status and metrics
- **CLOB Trading** - Place orders on centralized exchanges (Binance, Bybit, etc.) with interactive menus
- **DEX Trading** - Swap tokens and manage CLMM liquidity positions via Gateway
- **Web Dashboard** - Browser UI (via **`/web`**) with a live trade panel that covers both CEX and DEX venues: order book trading, DEX swaps, and CLMM liquidity positions created and drawn directly on the chart
- **Configuration** - Manage API servers, exchange credentials, and Gateway through Telegram (`/servers`, `/keys`, `/gateway`)
- **AI Assistant** - Natural language trading help via **`/agent`** (optional OpenAI or OpenRouter keys, or any custom OpenAI-compatible endpoint like Venice AI; MCP tools when configured)
- **Telegram Attachments** - Send images, albums, audio, and text documents to the assistant. See [Telegram conversations](telegram-conversations.md) for formats, limits, and controls.
- **Chart Delivery** - Authorized agents can send chart images to Telegram and attach them to dashboard conversations. See [image export and delivery](/agents/_shared/skills/charting/SKILL.md#image-export-and-delivery).
- **AI Agents** - Domain agents that can be asked, delegated to, or run on a loop — each can author its own tick strategy (analysis order, decision rules, risk limits) and run it autonomously with dry-run support

## Running Condor

Condor runs in a **tmux** session named `condor`:

| Command | Purpose |
| --- | --- |
| `make build` | Build the dashboard and swap the bundle in place |
| `make run` | Build the dashboard, then start Condor in the session |
| `make run-fg` | Same, in the foreground, for a startup error that needs debugging |
| `make stop` / `make restart` | Stop, or stop and start again |
| `make status` | Whether the session is up |
| `make logs` | Attach; detach without stopping with **Ctrl+B**, then **D** |
| `make doctor` | Read-only check of dependencies, `.env`/`config.yml`, the AI model, the dashboard binding, Tailscale, and every configured Hummingbot API server |

- When startup succeeds, admins receive **"Condor is online and ready."** in Telegram,
  or in the dashboard's notification bell in local mode.
- In Telegram, **`/servers`** holds Hummingbot API URLs and credentials, **`/keys`**
  exchange credentials, and **`/gateway`** DEX setup; **`/start`** has shortcuts to all
  three. Commands such as `/portfolio` and `/trade` need them.
- **Refresh Codex:** if a newer model fails because the Codex ACP bridge is outdated,
  `make refresh-codex` updates the bridge and its bundled Codex CLI. Start a new Codex
  session afterwards; existing sessions keep the bridge they started with.

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

## Hummingbot API

Hummingbot API is a separate runtime dependency, built from source at
[rehuony/hummingbot-api](https://github.com/rehuony/hummingbot-api). `make setup` can
deploy it in a `hummingbot-api` directory beside the Condor checkout (`HB_API_DIR`
overrides it) when Docker is available, or connect to
one that is already running here or elsewhere.

To deploy it on another machine, with Docker installed and running there:

```bash
git clone --branch main https://github.com/rehuony/hummingbot-api.git
cd hummingbot-api
make setup
make deploy
```

`make deploy` builds the current source, then starts the stack. The API listens on
`127.0.0.1:8000` unless `API_BIND` in its `.env` says otherwise; Condor's setup connects
to a co-located API at that address. Then run `make setup` in the Condor checkout and
choose the existing API, or add its URL and credentials through `/servers`. Use the
hostname the API deployment actually reports; `hummingbot-api` is only an example.

| Where you connect from | API URL |
|------------------------|---------|
| Same machine as the API | `http://127.0.0.1:8000` (or the address in its `API_BIND`) |
| Another device on your tailnet (Condor, browser) | `http://<api-tailnet-hostname>:8000` |

Updating the API is manual, on its host: `make check-update`, `make update`, a backup as
its [maintenance guide](https://github.com/rehuony/hummingbot-api/blob/main/docs/maintenance.md)
describes, then `make deploy`. **Settings → Hummingbot API** in the dashboard shows the
running build and can compare it with `main` on request. Condor never updates, deploys or
restarts the API, and updating Condor does not require updating the API or Gateway.

## Configuration files

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

### `config.yml`

Created by `make setup` from [`templates/config.yml`](/templates/config.yml), then
filled in with your admin id and the Hummingbot API connection. Servers, access and
chat defaults are edited from `/servers`, `/admin` and the dashboard afterwards.

```yaml
servers:
  local:
    host: 127.0.0.1
    port: 8000
    username: <API USERNAME>
    password: <API PASSWORD>
default_server: local
admin_id: 123456789
users: {}
server_access:
  local:
    owner_id: 123456789
    created_at: null
    shared_with: {}
chat_defaults:
  123456789: local
version: 1
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

Follow [Hummingbot API](#hummingbot-api) and the Tailscale section of the API's [user guide](https://github.com/rehuony/hummingbot-api/blob/main/docs/user-guide.md#tailscale). Confirm the API host is reachable on your tailnet, and note its actual hostname. A separate API Tailscale node is optional when Condor and the API share a machine.

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
- Keep Condor's API connection on this machine, at the API's `API_BIND` address
- Offer a separate API Tailscale node if other devices need to reach the API directly

**Scenario B — Connect to a remote Hummingbot API via Tailscale**

Choose `N` to skip local deployment, enter the remote API URL, then `y` when asked about Tailscale. The wizard will:
- Connect this machine to Tailscale
- Update `config.yml` to use the actual Tailscale MagicDNS hostname of the remote API

The remote machine must be running [hummingbot-api](https://github.com/rehuony/hummingbot-api) with Tailscale enabled on the same tailnet.

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

## Architecture

```
Telegram → main.py → handlers/ → config_manager.py → Hummingbot API → Trading bots
                  ↘ condor/web/ (FastAPI + WebSocket dashboard)
                              ↘ Gateway → DEX protocols
```

The Telegram bot (aiogram) and the dashboard (FastAPI) run in one process.

```
condor/
├── main.py              # Entry point, process wiring and startup/teardown
├── config_manager.py    # Servers, users and permissions in config.yml
├── handlers/            # Telegram commands and callbacks, one package per feature
├── condor/              # Core: agents, runtime, Telegram layer, web API, updates
├── frontend/            # Dashboard (React + Vite)
├── routines/            # Auto-discovered Python routines
├── agents/              # Agent definitions, playbooks and skills
├── mcp_servers/         # MCP servers exposed to agents
├── utils/               # Shared helpers
├── scripts/shell/       # Setup, service and source-update scripts behind make
├── templates/           # Files copied into a new installation
├── docs/                # User, maintenance and design documentation
└── tests/               # Backend tests
```

Handler hot reload lives in `condor/reload.py`; dashboard server configuration lives
in `condor/web/server.py`.

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

## Troubleshooting

Start with `make doctor` — it checks dependencies, config, the AI model, port
exposure, Tailscale, and API connectivity in one pass, and names the fix for
whatever it finds. The table below covers the rest.

| Issue | Solution |
|-------|----------|
| Bot not responding | Check `TELEGRAM_TOKEN` and `ADMIN_USER_ID` in `.env` |
| Exits at boot: `TELEGRAM_TOKEN is not set` | Telegram mode needs a token. Run `make setup` — or choose Local mode there if you do not want a bot |
| Local mode: dashboard not reachable from another device | Intended — local mode binds `127.0.0.1`. See [Local mode](#local-mode-no-telegram) before changing `WEB_HOST` |
| Access pending | An admin approves the user in `/admin` |
| Commands failing | Verify Hummingbot API is running |
| Connection refused | Check server host:port in `/servers`; use `hummingbot-api` (not `localhost`) when API is on another machine via Tailscale |
| Auth error | Verify server credentials match the API `.env` |
| DEX features unavailable | Ensure Gateway is configured and running |
| Tailscale: name `hummingbot-api` does not work | Enable **MagicDNS** in [Tailscale DNS settings](https://login.tailscale.com/admin/dns) |
| Tailscale: can't reach API | Run `tailscale status` — confirm both peers are connected; on the API host run `make tailscale-status` |
| Tailscale: auth key rejected | Key must start with `tskey-auth-`, check expiry in Tailscale admin |
| API still reachable on public IP | Remove port **8000** from your cloud provider's firewall / security group |

## Development

Prepare a checkout with the [installation steps](/README.md#installation), then follow
the [maintenance guide](maintenance.md) for branches, validation and publication. Use
`make run-fg` for foreground debugging and `make test` for backend tests.

A Telegram feature is a package under `handlers/` with its command, callback router and
message handler; register its command and callback prefix in `handlers/routes.py`,
and list the command in the menu in `handlers/commands.py`.
Callback data uses `{module}:{action}:{params}`. Hummingbot API, Gateway and exchange
services are managed as separate dependencies.
