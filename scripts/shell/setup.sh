#!/usr/bin/env bash
# Condor's interactive setup wizard: toolchain, run mode, AI model, Hummingbot API
# and config.yml. Re-running it preserves existing configuration.
# shellcheck source=scripts/shell/lib/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
# shellcheck source=scripts/shell/lib/dependencies.sh
. "$REPO_ROOT/scripts/shell/lib/dependencies.sh"
# shellcheck source=scripts/shell/lib/tailnet.sh
. "$REPO_ROOT/scripts/shell/lib/tailnet.sh"
# shellcheck source=scripts/shell/lib/hummingbot-api.sh
. "$REPO_ROOT/scripts/shell/lib/hummingbot-api.sh"

DATA_DIR="data"
TEMPLATE_CONFIG="$REPO_ROOT/templates/config.yml"

# The settings this wizard reads back from .env. Read with read_env_var, never by
# sourcing: .env is data, and one value with shell metacharacters must neither run
# nor hide every variable after it. Only keys present in the file are assigned.
load_condor_env() {
    local key value
    [ -f "$ENV_FILE" ] || return 0
    for key in CONDOR_MODE TELEGRAM_TOKEN ADMIN_USER_ID USE_TAILSCALE DEPLOY_HUMMINGBOT_API; do
        value="$(read_env_var "$key")"
        [ -n "$value" ] && printf -v "$key" '%s' "$value"
    done
    return 0
}

# What local mode costs, said out loud. It is a dashboard with no login at all,
# so the only thing standing between it and the network is the loopback bind.
local_mode_warning() {
    echo ""
    msg_warn "Local mode has NO login — whoever reaches the dashboard controls your funds"
    echo -e "    • It binds ${BOLD}127.0.0.1${RESET} only: reachable from this machine, nowhere else."
    echo -e "    • Exposing it takes an explicit ${BOLD}WEB_HOST=0.0.0.0${RESET} in .env, which puts full"
    echo -e "      trading control in reach of anyone who can hit the port. Only do that behind"
    echo -e "      something that authenticates (Tailscale, an SSH tunnel, a reverse proxy)."
    echo -e "    • Dashboard: ${BOLD}http://localhost:8088${RESET}"
    echo ""
}

# A configured install is *offered the other mode*, never just told it is
# already configured. Switching is the one thing a re-run has to be able to do:
# the boot errors for a broken mode all say "run make setup", and that has to be
# advice that leads somewhere.
choose_mode() {
    echo -e "    ${BOLD}1) Telegram${RESET}  — control Condor from a Telegram bot (recommended)"
    echo -e "    ${BOLD}2) Local${RESET}     — no Telegram; the dashboard runs on this machine, no login"
    echo ""
    while true; do
        prompt_visible "Choose [1/2]" "$1" "condor_mode_choice"
        case "${condor_mode_choice:-}" in
            1|2) break ;;
            *) msg_warn "Enter 1 (Telegram) or 2 (Local)" ;;
        esac
    done
    echo ""
}

# Point config.yml's `local` server at the API this run configured. A local deploy
# uses the credentials its .env actually holds (setup keeps an existing .env, so
# prompted values may differ) and its API_BIND, whether or not hummingbot-api also
# has its own tailnet node for other clients.
sync_api_server_config() {
    local host port credentials_env=""
    if [ "${hb_api_deployed:-}" = true ]; then
        host="$(local_api_host)"; port=8000; credentials_env="$HB_API_DIR/.env"
    elif [ "${hb_api_configured:-false}" = true ]; then
        host="$HB_API_HOST"; port="$HB_API_PORT"; credentials_env="${hb_api_credentials_env:-}"
    else
        return 0
    fi
    if update_config_api_server "$host" "$port" "$credentials_env"; then
        msg_ok "Configured $CONFIG_FILE: ${HB_API_PROTOCOL:-http}://$host:$port"
        config_updated=true
    else
        msg_error "Could not update $CONFIG_FILE with the API connection; it was left unchanged."
        msg_info "Add the server via /servers in Telegram or the dashboard."
    fi
}

# ── Banner ───────────────────────────────────────────

echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║            Condor Setup                   ║${RESET}"
echo -e "${BOLD}╚═══════════════════════════════════════════╝${RESET}"
echo ""

install_dependencies

echo ""

# ── Step 1: Run mode (Telegram or local) ────────────

echo -e "${BOLD}Step 1: How will you use Condor?${RESET}"
echo ""

telegram_configured=false

load_condor_env

# An install from before local mode existed has a token but no CONDOR_MODE.
# That is telegram — recorded now, so the mode is always explicit on disk and
# is never inferred later from whether a token happens to be there.
if [ -z "${CONDOR_MODE:-}" ] && [ -n "${TELEGRAM_TOKEN:-}" ] && [ -n "${ADMIN_USER_ID:-}" ]; then
    CONDOR_MODE="telegram"
    set_env_var CONDOR_MODE "telegram"
fi


if [ "${CONDOR_MODE:-}" = "local" ]; then
    msg_ok "Currently: Local mode — dashboard only, no Telegram"
    echo ""
    prompt_visible "Keep local mode? [Y/n]" "Y" "keep_mode"
    echo ""
    if [[ "${keep_mode:-Y}" =~ ^[Nn]$ ]]; then
        choose_mode "1"
    else
        condor_mode_choice=2
    fi
elif [ -n "${TELEGRAM_TOKEN:-}" ] && [ -n "${ADMIN_USER_ID:-}" ]; then
    msg_ok "Currently: Telegram (bot configured, admin ${ADMIN_USER_ID})"
    echo ""
    prompt_visible "Keep Telegram mode? [Y/n]" "Y" "keep_mode"
    echo ""
    if [[ "${keep_mode:-Y}" =~ ^[Nn]$ ]]; then
        choose_mode "2"
    else
        telegram_configured=true
        load_tailscale_choice
    fi
else
    choose_mode "1"
fi

if [ "${condor_mode_choice:-}" = "2" ]; then
    # Local mode: nothing to ask about a bot. No Telegram, no token — the
    # dashboard logs in as ADMIN_USER_ID, the same id config.yml, chat
    # defaults, preferences, memory and session keys already key on.
    #
    # An existing numeric id is KEPT, never overwritten with 1: a Telegram
    # install switching to local keeps its own admin, so the dashboard logs in
    # as you with every server, preference and default intact. 1 is only the
    # answer for an install that never had a Telegram id.
    CONDOR_MODE="local"
    if [[ "${ADMIN_USER_ID:-}" =~ ^[1-9][0-9]*$ ]]; then
        msg_info "Local mode will log in as user ${ADMIN_USER_ID} (ADMIN_USER_ID in .env)"
    else
        ADMIN_USER_ID="1"
        set_env_var ADMIN_USER_ID "1"
    fi
    set_env_var CONDOR_MODE "local"
    set_env_var WEB_URL "http://localhost:8088"
    msg_ok ".env configured for local mode"
    local_mode_warning

    # Tailscale is not just an option here — it is the answer to the warning
    # above: an unauthenticated dashboard is only as safe as what can actually
    # reach it. Asked the same way Telegram mode asks it (below), so local
    # mode gets the identical Tailscale install/connect treatment in Step 3
    # for hummingbot-api — and for the dashboard itself, condor/web/server.py
    # binds loopback and proxies it via `tailscale serve` whenever
    # USE_TAILSCALE is set, local mode or not.
    prompt_visible "Use Tailscale to secure the dashboard and the hummingbot-api connection? [y/N]" "N" "use_tailscale_early"
    if [[ "${use_tailscale_early:-}" =~ ^[Yy]$ ]]; then
        USE_TAILSCALE=true
    else
        USE_TAILSCALE=false
    fi
    set_env_var USE_TAILSCALE "$USE_TAILSCALE"
elif [ "${condor_mode_choice:-}" = "1" ]; then
    msg_info "Create a bot: $(make_link 'https://t.me/BotFather')"
    msg_info "Get your ID: $(make_link 'https://t.me/userinfobot')"
    echo ""

    # Prompt for Telegram Bot Token
    while true; do
        prompt_visible "Telegram Bot Token" "" "telegram_token"
        if [ -z "$telegram_token" ]; then
            msg_warn "Token cannot be empty"
            continue
        fi
        if ! [[ "$telegram_token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
            msg_warn "Invalid format. Expected: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
            continue
        fi
        break
    done
    TELEGRAM_TOKEN="$telegram_token"

    # Prompt for Admin User ID
    while true; do
        prompt_visible "Admin User ID" "" "admin_id"
        if [ -z "$admin_id" ]; then
            msg_warn "User ID cannot be empty"
            continue
        fi
        if ! [[ "$admin_id" =~ ^[0-9]+$ ]]; then
            msg_warn "User ID must be numeric (e.g., 123456789)"
            continue
        fi
        break
    done
    ADMIN_USER_ID="$admin_id"

    # Determine web dashboard access
    echo ""
    prompt_visible "Will you use Tailscale to secure the connection to hummingbot-api? [y/N]" "N" "use_tailscale_early"
    SERVER_IP=""
    if ! [[ "${use_tailscale_early:-}" =~ ^[Yy]$ ]]; then
        echo ""
        echo -e "  The /web Telegram command sends you a login link for the web dashboard."
        echo -e "  So we need to know where Condor is running:"
        echo -e "    ${BOLD}Local machine${RESET} (Mac / Linux / WSL2) — press Enter"
        echo -e "    ${BOLD}Remote VPS${RESET}                         — enter the server's public IP"
        prompt_visible "Public IP or hostname (press Enter for localhost)" "" "server_ip"
        SERVER_IP="${server_ip:-}"
    fi

    if [[ "${use_tailscale_early:-}" =~ ^[Yy]$ ]]; then
        USE_TAILSCALE=true
    else
        USE_TAILSCALE=false
    fi

    # Write .env, keeping every other setting already in it.
    set_env_var TELEGRAM_TOKEN "$TELEGRAM_TOKEN"
    set_env_var ADMIN_USER_ID "$ADMIN_USER_ID"
    set_env_var USE_TAILSCALE "$USE_TAILSCALE"
    if [ -n "$SERVER_IP" ]; then
        set_env_var WEB_URL "http://$SERVER_IP:8088"
    fi

    # The mode is written explicitly even for the default, so nothing downstream
    # has to guess it from the presence of a token.
    CONDOR_MODE="telegram"
    set_env_var CONDOR_MODE "telegram"

    msg_ok ".env created"
    if [ -n "$SERVER_IP" ]; then
        msg_ok "Web server configured: http://$SERVER_IP:8088"
    fi
    telegram_configured=true
fi

echo ""

# ── Step 2: AI Model (LLM) ──────────────────────────

echo -e "${BOLD}Step 2: AI Model (LLM)${RESET}"
echo ""

# The wizard (condor.setup_llm) renders the same readiness probes the bot
# uses, so it has to run in the project's Python env. Sync it FIRST, before
# asking anything below -- otherwise `uv run`'s own venv-creation/install
# output shows up sandwiched between "Pick an AI model now?" and the actual
# model menu, which reads as the prompt getting interrupted mid-conversation
# rather than as one continuous step. Unconditional (not gated behind the
# Y/n) since both branches below need it: picking a model runs the wizard
# directly, and skipping still needs it synced for `--status`/no-tty.
msg_info "Setting up Condor's Python environment (~250MB first run, 1-3 min)..."
uv run python -c "pass"
echo ""

if (: </dev/tty) 2>/dev/null; then
    prompt_visible "Pick an AI model now? [Y/n]" "Y" "pick_model_now"
    if [[ "${pick_model_now:-Y}" =~ ^[Nn]$ ]]; then
        msg_ok "Skipped -- run 'make pick-model' any time"
    else
        # The venv is already warm above, so this goes straight to the model
        # menu -- and if the model picked (or kept) needs a CLI bridge that
        # isn't installed yet (e.g. `npm install -g @google/gemini-cli`),
        # installs and confirms it right here, no separate pass afterward.
        uv run python -m condor.setup_llm < /dev/tty || \
            msg_warn "Model selection did not complete -- run 'make pick-model' later"
    fi
else
    msg_info "No terminal available -- skipping model selection"
    uv run python -m condor.setup_llm --status || true
fi

echo ""

# ── Step 3: Hummingbot API ──────────────────────────

echo -e "${BOLD}Step 3: Hummingbot API${RESET}"
echo ""

hb_api_deployed=false
# Configured is not the same as answering; the summary must not conflate them.
hb_api_healthy=false

load_condor_env
load_tailscale_choice

finish_remote_api=false
if [ -n "${DEPLOY_HUMMINGBOT_API:-}" ]; then
    if [ "${DEPLOY_HUMMINGBOT_API:-}" = "true" ]; then
        msg_ok "Hummingbot API already configured (enabled)"
        hb_api_deployed=true
    elif config_has_api_server; then
        msg_ok "Hummingbot API already configured (skipped)"
    else
        msg_info "Finishing remote API configuration..."
        finish_remote_api=true
    fi
fi

if [ -z "${DEPLOY_HUMMINGBOT_API:-}" ] || [ "$finish_remote_api" = true ]; then
    # Check if a Hummingbot API is already running on port 8000
    existing_api=false
    msg_info "Checking for an existing API on $(local_api_host):8000..."
    if api_health_check; then
        existing_api=true
        msg_warn "Hummingbot API already running on $(local_api_host):8000"
        echo ""
        prompt_visible "An API instance is already running. Override it? [y/N]" "N" "override_api"
        if [[ "${override_api:-}" =~ ^[Yy]$ ]]; then
            msg_info "Will reconfigure and restart the API."
        else
            set_env_var DEPLOY_HUMMINGBOT_API "false"
            msg_ok "Keeping existing API instance"
            hb_api_deployed=false

            # Keeping someone else's API still means Condor has to log in to
            # it. Skipping this used to leave config.yml on the template's
            # placeholder credentials, so the very next thing the user saw was
            # every command failing with a 401 and no hint as to why.
            echo ""
            msg_info "Condor still needs this API's credentials to talk to it."
            msg_info "They are USERNAME / PASSWORD in that instance's .env."
            prompt_required_visible "API admin username" "hb_username" "Username cannot be empty"
            prompt_required_secret "API admin password" "hb_password" "Password cannot be empty"
            HB_API_PROTOCOL="http"
            HB_API_HOST="$(local_api_host)"
            HB_API_PORT="8000"
            hb_api_configured=true

            echo ""
            api_probe_report "$HB_API_PROTOCOL" "$HB_API_HOST" "$HB_API_PORT" \
                             "$hb_username" "$hb_password"

            # Skip the rest of the API setup block
            existing_api=skip
        fi
    fi

    if [ "$existing_api" != "skip" ]; then
        msg_info "Condor connects to Hummingbot Backend API for trading."
        echo ""
        hb_api_preexisting=false
        if [ "$finish_remote_api" = true ]; then
            deploy_hb="n"
            msg_ok "Using remote Hummingbot API (local Docker deploy skipped)"
        elif [ -f "$HB_API_DIR/.env" ]; then
            # An install on disk is protected even when it is not running: the
            # health check above only sees a live API, and redeploying from here
            # used to overwrite its .env -- leaving broker credentials the
            # broker's bootstrap file and volume no longer matched. So the
            # default is "n", and the existing .env is read-only input.
            hb_api_preexisting=true
            msg_ok "Existing hummingbot-api install detected at $HB_API_DIR"
            msg_info "Its .env (API and broker credentials) will be left untouched."
            prompt_visible "Build the current API source and deploy its Docker stack now? [y/N]" "N" "deploy_hb"
        else
            prompt_visible "Configure and launch local Hummingbot API with Docker? [Y/n]" "Y" "deploy_hb"
        fi

        if [[ "${deploy_hb:-}" =~ ^[Nn]$ ]]; then
            if [ "$finish_remote_api" != true ]; then
                set_env_var DEPLOY_HUMMINGBOT_API "false"
            fi
            msg_ok "Skipped Hummingbot API deployment"
            echo ""
            connect_hummingbot_api
        else
            deploy_hummingbot_api
        fi
    fi
fi

echo ""

# ── Step 4: Create/Update config.yml ─────────────────

echo -e "${BOLD}Step 4: Configuration Files${RESET}"
echo ""

load_condor_env
config_updated=false

if [ ! -s "$CONFIG_FILE" ]; then
    msg_info "Creating $CONFIG_FILE from templates/config.yml..."
    # Owner-only from the first byte: it carries the API password once filled in.
    # The admin id is validated numeric in Step 1; awk substitutes it as data.
    (umask 077; awk -v id="${ADMIN_USER_ID:-}" \
        'id != "" { gsub(/ADMIN_USER_ID_PLACEHOLDER/, id) } { print }' \
        "$TEMPLATE_CONFIG" > "$CONFIG_FILE")
    msg_ok "Created $CONFIG_FILE"
    config_updated=true
fi

sync_api_server_config

if [ "$config_updated" = false ] && [ -f "$CONFIG_FILE" ]; then
    msg_ok "$CONFIG_FILE exists and is configured"
fi

echo ""

# ── Step 5: Data directory ──────────────────────────

if [ ! -d "$DATA_DIR" ]; then
    mkdir -p "$DATA_DIR"
fi

# ── Step 6: Summary ────────────────────────────────

echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo -e "  ${GREEN}Setup complete!${RESET}"
echo ""
echo -e "  ${BOLD}Next:${RESET}"
echo -e "    make run     ${DIM}- start Condor (tmux session 'condor')${RESET}"
echo -e "    make doctor  ${DIM}- re-check dependencies, config and API access${RESET}"
echo -e "    make logs    ${DIM}- attach to the running session (detach: Ctrl+B then D)${RESET}"
if [ "${CONDOR_MODE:-telegram}" = "local" ]; then
echo ""
echo -e "  Then open ${BOLD}http://localhost:8088${RESET} — you are logged in already."
echo -e "  ${DIM}No login, loopback only. See 'Local mode' in docs/user-guide.md before exposing it.${RESET}"
else
echo ""
echo -e "  Then send ${BOLD}/start${RESET} to your Telegram bot."
fi
if [ "${hb_api_healthy:-false}" = true ]; then
echo ""
echo -e "  Hummingbot API is running — config at ${BOLD}$HB_API_DIR/.env${RESET}"
elif [ "${hb_api_deployed:-}" = true ]; then
echo ""
echo -e "  Hummingbot API is configured but ${BOLD}not answering yet${RESET} — config at ${BOLD}$HB_API_DIR/.env${RESET}"
echo -e "  ${DIM}Start or check it: cd $HB_API_DIR && make deploy && make doctor${RESET}"
fi
if [ "${TS_DEPLOY:-false}" = true ]; then
echo ""
echo -e "  ${BOLD}Tailscale:${RESET}"
if [ "${HB_OWN_TAILNET_NODE:-false}" = true ]; then
# Ask the sidecar what name the control plane gave it, rather than repeating
# the one we requested: Tailscale suffixes a name that is already taken, and a
# URL built from the request then points at whoever holds the original.
_hb_node="$(tailnet_node_name docker exec hummingbot-tailscale tailscale 2>/dev/null || true)"
echo -e "    hummingbot-api URL:  http://${_hb_node:-$ts_hb_hostname}:8000  ${CYAN}(own tailnet node)${RESET}"
if [ -n "$_hb_node" ] && [ "$_hb_node" != "$ts_hb_hostname" ]; then
echo -e "    ${DIM}('$ts_hb_hostname' was already taken on your tailnet — this node is '$_hb_node')${RESET}"
fi
else
echo -e "    hummingbot-api:      http://$(local_api_host):8000  ${CYAN}(local — no tailnet node needed)${RESET}"
fi
if [ -n "${ts_condor_ip:-}" ] && [ "${CONDOR_MODE:-telegram}" != "local" ]; then
echo -e "    Web dashboard URL:   http://${ts_condor_ip}:8088  ${CYAN}(Tailscale only)${RESET}"
else
echo -e "    Web dashboard URL:   http://localhost:8088"
fi
echo -e "    Tailscale status:    tailscale status"
elif [[ "${use_tailscale_remote:-}" =~ ^[Yy]$ ]]; then
echo ""
echo -e "  ${BOLD}Tailscale:${RESET}"
echo -e "    hummingbot-api URL:  http://${ts_hostname:-hummingbot-api}:8000"
if [ -n "${ts_condor_ip:-}" ] && [ "${CONDOR_MODE:-telegram}" != "local" ]; then
echo -e "    Web dashboard URL:   http://${ts_condor_ip}:8088  ${CYAN}(Tailscale only)${RESET}"
else
echo -e "    Web dashboard URL:   http://localhost:8088"
fi
echo -e "    Tailscale status:    tailscale status"
fi
if [ "${TS_DEPLOY:-false}" = true ] || [[ "${use_tailscale_remote:-}" =~ ^[Yy]$ ]]; then
echo ""
echo -e "  ${BOLD}Accessing the web dashboard from another device:${RESET}"
echo -e "  ${CYAN}  Install Tailscale on that device, then sign in to the same account:${RESET}"
echo -e "    Linux / WSL:   curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"
echo -e "    macOS / Win:   https://tailscale.com/download — then sign in to the same account"
echo -e "  ${DIM}A reusable auth key works too (sudo tailscale up --authkey=tskey-auth-...).${RESET}"
echo -e "  ${DIM}Yours is not reprinted here — it is a credential, and this output ends up in${RESET}"
echo -e "  ${DIM}scrollback and install logs. Find or reissue it at${RESET}"
echo -e "  ${DIM}https://login.tailscale.com/admin/settings/keys${RESET}"
fi
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo ""