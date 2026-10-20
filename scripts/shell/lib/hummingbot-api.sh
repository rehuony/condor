#!/usr/bin/env bash
# The co-located or remote hummingbot-api, as the setup wizard sees it: checkout
# validation, its own configuration and deployment, the address Condor connects to,
# and the config.yml entry. Sourced after lib/common.sh and lib/tailnet.sh.

HB_API_DIR="${HB_API_DIR:-$REPO_ROOT/../hummingbot-api}"
HB_API_REPO="${HB_API_REPO:-https://github.com/rehuony/hummingbot-api.git}"

# Clone only when absent; never realign an existing checkout implicitly.
prepare_hummingbot_api_checkout() {
    local origin branch upstream shallow
    if [ ! -e "$HB_API_DIR" ]; then
        msg_info "Cloning hummingbot-api main to $HB_API_DIR..."
        if ! git clone --branch main --single-branch "$HB_API_REPO" "$HB_API_DIR"; then
            msg_error "Failed to clone hummingbot-api"
            return 1
        fi
    fi
    if ! git -C "$HB_API_DIR" rev-parse --show-toplevel >/dev/null 2>&1 || \
       [ "$(git -C "$HB_API_DIR" rev-parse --show-prefix 2>/dev/null)" != "" ]; then
        msg_error "$HB_API_DIR must be a hummingbot-api Git checkout root."
        return 1
    fi
    origin=$(git -C "$HB_API_DIR" remote get-url origin 2>/dev/null) || origin=""
    case "$origin" in
        https://github.com/rehuony/hummingbot-api|https://github.com/rehuony/hummingbot-api.git|git@github.com:rehuony/hummingbot-api.git|ssh://git@github.com/rehuony/hummingbot-api.git) ;;
        *) msg_error "hummingbot-api origin must point to rehuony/hummingbot-api; checkout unchanged."; return 1 ;;
    esac
    branch=$(git -C "$HB_API_DIR" branch --show-current)
    upstream=$(git -C "$HB_API_DIR" rev-parse --abbrev-ref '@{upstream}' 2>/dev/null) || upstream=""
    if [ "$branch" != main ] || [ "$upstream" != origin/main ]; then
        msg_error "hummingbot-api must use main tracking origin/main; checkout unchanged."
        return 1
    fi
    shallow=$(git -C "$HB_API_DIR" rev-parse --is-shallow-repository)
    if [ "$shallow" != false ]; then
        msg_error "hummingbot-api requires full Git history for source update checks."
        msg_info "Review the checkout, then deepen it with: git -C $HB_API_DIR fetch --unshallow origin"
        return 1
    fi
    if [ ! -f "$HB_API_DIR/scripts/shell/setup.sh" ] || \
       [ ! -f "$HB_API_DIR/Makefile" ] || [ ! -f "$HB_API_DIR/docker-compose.yml" ]; then
        msg_error "hummingbot-api is missing scripts/shell/setup.sh, Makefile, or docker-compose.yml."
        msg_info "Review and update that checkout before running this installer again."
        return 1
    fi
    msg_ok "hummingbot-api checkout verified; existing files and local changes preserved"
}

# The API owns its configuration schema and generated broker credentials.
configure_hummingbot_api() {
    if [ -f "$HB_API_DIR/.env" ]; then
        msg_ok "hummingbot-api .env already exists — preserving its configuration"
    fi
    msg_info "Running hummingbot-api's configuration setup (non-interactive)..."
    if ! (cd "$HB_API_DIR" && \
        HBAPI_NONINTERACTIVE=1 \
        HBAPI_USERNAME="$hb_username" \
        HBAPI_PASSWORD="$hb_password" \
        HBAPI_CONFIG_PASSWORD="$hb_config_password" \
        TAILSCALE_ENABLED="$([ "$HB_TAILSCALE_MODE" = none ] && echo false || echo true)" \
        TAILSCALE_MODE="$HB_TAILSCALE_MODE" \
        TAILSCALE_AUTH_KEY="$ts_auth_key" \
        TAILSCALE_HOSTNAME="$ts_hb_hostname" \
        HBAPI_BACKTESTING_MAX_CONCURRENT=4 \
        HBAPI_BACKTESTING_TIMEOUT_SECONDS=1800 \
        bash scripts/shell/setup.sh); then
        msg_error "hummingbot-api scripts/shell/setup.sh failed — see the output above"
        msg_info "Run it yourself: cd $HB_API_DIR && bash scripts/shell/setup.sh, then: make deploy"
        return 1
    fi
    if [ ! -f "$HB_API_DIR/.env" ]; then
        msg_error "hummingbot-api setup did not create .env; deployment skipped."
        return 1
    fi
    # Setup preserves existing .env. Sync Condor with the credentials actually
    # on disk, rather than newly prompted values that setup deliberately ignored.
    hb_username=$(ENV_FILE="$HB_API_DIR/.env" read_env_var USERNAME)
    hb_password=$(ENV_FILE="$HB_API_DIR/.env" read_env_var PASSWORD)
    if [ -z "$hb_username" ] || [ -z "$hb_password" ]; then
        msg_error "hummingbot-api .env needs USERNAME and PASSWORD; deployment skipped."
        return 1
    fi
    msg_ok "Hummingbot API configuration is ready"
}

# Where Condor on this machine reaches the co-located API: its API_BIND, made
# connectable. A wildcard bind is reached over loopback; a specific address only on
# that address. IPv6 literals are bracketed for use in a URL and in config.yml.
local_api_host() {
    local bind
    bind="$(ENV_FILE="$HB_API_DIR/.env" read_env_var API_BIND)"
    case "$bind" in
        ""|0.0.0.0) echo "127.0.0.1" ;;
        ::|"[::]") echo "[::1]" ;;
        \[*\]) echo "$bind" ;;
        *:*) echo "[$bind]" ;;
        *) echo "$bind" ;;
    esac
}

# Quick API probe — avoid hanging Step 3 when port 8000 is slow/unreachable
api_health_check() {
    curl -sf --connect-timeout 3 --max-time 5 "http://$(local_api_host):8000/docs" >/dev/null 2>&1
}

# Can we actually reach and log into the API we were just told to use?
#
# Checked before setup reports success, because every way this goes wrong
# produces a symptom that points somewhere else:
#
#   no connection   hummingbot-api publishes port 8000 on 127.0.0.1 by
#                   default, so a correct install on another machine is
#                   unreachable until API_BIND is widened there -- and nothing
#                   in either installer mentions that at the point you choose
#                   a remote API.
#   401             the host answered, but not with these credentials. On a
#                   tailnet that usually means a name collision: some OTHER
#                   machine is running hummingbot-api under the name we asked
#                   for.
#
# Echoes one of: ok | unauthorized | unreachable
api_probe() {
    local proto="$1" host="$2" port="$3" user="$4" pass="$5" code
    command_exists curl || { echo ok; return; }
    code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 \
            -u "$user:$pass" "$proto://$host:$port/accounts/" 2>/dev/null)" || code=000
    case "$code" in
        2*)      echo ok ;;
        401|403) echo unauthorized ;;
        000)     echo unreachable ;;
        *)       echo ok ;;   # 404/500 etc: it is there and it let us in
    esac
}

# Report an api_probe result, with the diagnosis each failure actually needs.
# Never fatal: a VPS that is not up yet is a normal reason to finish setup and
# fix the connection afterwards.
api_probe_report() {
    local proto="$1" host="$2" port="$3" user="$4" pass="$5" tailnet="${6:-false}"
    msg_info "Checking the API at $proto://$host:$port ..."
    case "$(api_probe "$proto" "$host" "$port" "$user" "$pass")" in
        ok)
            msg_ok "API reachable and credentials accepted"
            ;;
        unauthorized)
            msg_warn "$host:$port answered, but rejected these credentials (401)."
            if [ "$tailnet" = true ]; then
                msg_info "On a tailnet this usually means the name belongs to a DIFFERENT machine:"
                msg_info "Tailscale appends -1, -2 ... when a hostname is taken, so the node you"
                msg_info "deployed may be '$host-1' while '$host' is somebody else's."
                msg_info "Check with: tailscale status"
            fi
            msg_info "Fix the host or the credentials in config.yml, or via /servers in Telegram."
            ;;
        unreachable)
            msg_warn "Could not connect to $host:$port."
            case "$host" in
                localhost|127.0.0.1|::1|"[::1]")
                    # Nothing to do with bind addresses: the stack is right
                    # here and simply is not up.
                    msg_info "That is this machine, so the API is just not running."
                    msg_info "Start it: cd $HB_API_DIR && make deploy"
                    ;;
                *)
                    msg_info "hummingbot-api publishes port 8000 on 127.0.0.1 by default, so a healthy"
                    msg_info "install is still unreachable from another machine until you widen it."
                    msg_info "On the machine running hummingbot-api:"
                    msg_info "  add API_BIND=0.0.0.0 to its .env, then 'make deploy'"
                    msg_info "  (or put both machines on a tailnet, which needs no open port)"
                    ;;
            esac
            msg_info "Then re-check here with: make doctor"
            ;;
    esac
}

# True when config.yml already has a server host entry under servers:
config_has_api_server() {
    [ -f "$CONFIG_FILE" ] && grep -A8 '^servers:' "$CONFIG_FILE" 2>/dev/null | grep -q 'host:'
}

# Point config.yml's `local` server at the API through Condor's own ConfigManager:
# an atomic, structured edit that keeps every other server, permission and chat
# default, and refuses to touch a config.yml it cannot parse. Credentials come from
# the API's .env when one is named, otherwise from hb_username/hb_password, and are
# passed through the environment rather than argv. Returns non-zero, having changed
# nothing, when the edit could not be made.
update_config_api_server() {
    local host="$1" port="$2" credentials_env="${3:-}"
    local args=(--config "$CONFIG_FILE" --host "$host" --port "$port" --owner "${ADMIN_USER_ID:-}")
    [ -n "$credentials_env" ] && args+=(--credentials-env "$credentials_env")
    CONDOR_API_USERNAME="${hb_username:-}" CONDOR_API_PASSWORD="${hb_password:-}" \
        uv run python -m condor.setup_api_server "${args[@]}"
}

# Split an API URL into HB_API_PROTOCOL / HB_API_HOST / HB_API_PORT. The URL is
# data: it reaches Python through the environment, never spliced into code. IPv6
# hosts are bracketed for use in a URL and in config.yml.
parse_api_url() {
    local parsed
    parsed="$(URL="$1" python3 -c '
import os
from urllib.parse import urlparse
url = urlparse(os.environ["URL"])
scheme = url.scheme or "http"
host = url.hostname or "localhost"
port = url.port or (443 if scheme == "https" else 8000)
print(scheme, f"[{host}]" if ":" in host else host, port, sep="\n")
' 2>/dev/null)" || parsed=$'http\nlocalhost\n8000'
    { read -r HB_API_PROTOCOL; read -r HB_API_HOST; read -r HB_API_PORT; } <<< "$parsed"
}

# Connect to an API this wizard does not deploy: a co-located install left
# untouched, or a remote one (optionally over the tailnet). Sets HB_API_* and
# hb_api_configured.
connect_hummingbot_api() {
    if [ "${hb_api_preexisting:-false}" = true ]; then
        # Co-located install we chose not to touch: its credentials are
        # already on disk, so asking the user to retype them invites a
        # typo and a 401. Its API_BIND, not the tailnet name -- Condor is
        # on this machine, so MagicDNS would be a longer route to the same
        # port and one more thing that can be down.
        hb_username=$(ENV_FILE="$HB_API_DIR/.env" read_env_var USERNAME)
        hb_password=$(ENV_FILE="$HB_API_DIR/.env" read_env_var PASSWORD)
        HB_API_PROTOCOL="http"
        HB_API_HOST="$(local_api_host)"
        HB_API_PORT="8000"
        if [ -n "${hb_username:-}" ] && [ -n "${hb_password:-}" ]; then
            hb_api_credentials_env="$HB_API_DIR/.env"
            msg_ok "Read API credentials from $HB_API_DIR/.env"
        else
            msg_warn "Could not read USERNAME/PASSWORD from $HB_API_DIR/.env"
            prompt_required_visible "API admin username" "hb_username" "Username cannot be empty"
            prompt_required_secret "API admin password" "hb_password" "Password cannot be empty"
        fi
        hb_api_configured=true
    else
        msg_info "Enter the Hummingbot API connection details."
        if [[ "${use_tailscale_early:-}" =~ ^[Yy]$ ]]; then
            # Skipping local deploy + using Tailscale means hummingbot-api is
            # on another machine (a VPS, most likely). "hummingbot-api" is the
            # name its own setup ASKS for -- a starting point, not an answer.
            # The tailnet is searched for the real node once we are on it,
            # below, because Tailscale suffixes a name that is already taken
            # and the unsuffixed one then belongs to someone else's machine.
            HB_API_PROTOCOL="http"
            HB_API_HOST="hummingbot-api"
            HB_API_PORT="8000"
            msg_info "Looking for hummingbot-api on your tailnet after connecting..."
        else
            prompt_visible "API URL + port (e.g. http://your-server:8000)" "http://localhost:8000" "hb_api_url_raw"
            parse_api_url "${hb_api_url_raw:-http://localhost:8000}"
        fi
        prompt_required_visible "API admin username" "hb_username" "Username cannot be empty"
        prompt_required_secret "API admin password" "hb_password" "Password cannot be empty"
    fi

    # ── Tailscale option for external API ──────────────
    # Skipped for a co-located pre-existing install: HB_API_HOST is already
    # localhost, and this block would rewrite it to a MagicDNS name that
    # points back at this same machine by a longer path.
    use_tailscale_remote="${use_tailscale_early:-N}"
    if [ "${hb_api_preexisting:-false}" = true ]; then
        use_tailscale_remote="N"
    fi
    if [[ "${use_tailscale_remote:-}" =~ ^[Yy]$ ]]; then
        if command_exists tailscale && tailscale status >/dev/null 2>&1; then
            msg_ok "Tailscale already connected on this machine"
        else
            show_tailscale_key_help
            prompt_tailscale_auth_key "Tailscale auth key (tskey-auth-...)"
            msg_info "Installing Tailscale on this machine..."
            curl -fsSL https://tailscale.com/install.sh | sh
            msg_info "Connecting to Tailscale network..."
            tailscale_up --authkey="$ts_auth_key" --hostname="condor" --accept-dns=true
        fi
        ts_condor_ip=$(tailscale ip -4 2>/dev/null | head -1)

        # Which node on this tailnet IS the hummingbot-api? Asked, not
        # assumed. Tailscale suffixes a hostname that is already taken, so
        # on a tailnet that already had a "hummingbot-api" -- a
        # colleague's box, an older install -- the machine we want is
        # "hummingbot-api-1" and the bare name is somebody else's. Their
        # box is likely running the same software, so it ANSWERS, and the
        # install ends up pointed at it: the failure arrives as "401
        # Incorrect username or password", which sends people to check a
        # password that was never wrong.
        ts_hostname=""
        _ts_candidates="$(tailnet_api_peers hummingbot-api)"
        _ts_count="$(printf '%s' "$_ts_candidates" | grep -c . || true)"
        if [ "${_ts_count:-0}" -eq 1 ]; then
            ts_hostname="$_ts_candidates"
            msg_ok "Found it on your tailnet: $ts_hostname"
        elif [ "${_ts_count:-0}" -gt 1 ]; then
            msg_warn "More than one hummingbot-api node on this tailnet:"
            printf '%s\n' "$_ts_candidates" | while IFS= read -r _c; do
                [ -n "$_c" ] && echo "      • $_c"
            done
            msg_info "Tailscale adds -1, -2 ... when a name is taken, so these are"
            msg_info "different machines. Pick the one you just deployed."
            prompt_required_visible "Which node is your hummingbot-api?" "ts_hostname" "Name cannot be empty"
        else
            msg_warn "No hummingbot-api node is visible on this tailnet yet."
            msg_info "Deploy it on that machine first (its setup joins the tailnet), or"
            msg_info "enter the name/address it is reachable at."
            prompt_visible "hummingbot-api host" "hummingbot-api" "ts_hostname"
            ts_hostname="${ts_hostname:-hummingbot-api}"
        fi

        # Use the Tailscale MagicDNS hostname to reach hummingbot-api (plain HTTP — WireGuard encrypts in transit)
        HB_API_HOST="$ts_hostname"
        HB_API_PORT="8000"
        HB_API_PROTOCOL="http"
        msg_ok "Tailscale connected — server URL: http://$ts_hostname:8000"
        set_tailnet_web_url
    fi

    # Say now whether the address we just wrote actually works. Everything
    # above is a claim about another machine; this is the only step that
    # checks one.
    echo ""
    api_probe_report "$HB_API_PROTOCOL" "$HB_API_HOST" "$HB_API_PORT" \
                     "$hb_username" "$hb_password" \
                     "$([[ "${use_tailscale_remote:-N}" =~ ^[Yy]$ ]] && echo true || echo false)"

    hb_api_configured=true
}

# Deploy hummingbot-api on this machine: Docker and Tailscale choices, the
# credentials it is configured with, then install_local_hummingbot_api.
deploy_hummingbot_api() {
    # Check Docker (only for hummingbot-api launch)
    if ! command_exists docker; then
        msg_warn "Docker not found. API config will be saved but launch skipped."
        msg_info "Install Docker: https://docs.docker.com/get-docker/"
        docker_available=false
    elif ! docker info >/dev/null 2>&1; then
        msg_warn "Docker is not running. API config will be saved but launch skipped."
        docker_available=false
    else
        docker_available=true
    fi

    # ── Tailscale option for Docker deploy ─────────────
    # Condor is deploying hummingbot-api right here, on this same machine
    # -- so Condor itself always reaches it over localhost, tailnet or not.
    # Answering yes below connects THIS HOST to the tailnet (so the
    # dashboard can be reached remotely via `tailscale serve`). Giving
    # hummingbot-api its OWN separate tailnet node is a second, distinct
    # question asked further down, only if this one is yes -- most
    # installs don't need a second node just to reach something already
    # local, and spinning one up unconditionally used to mean two tailnet
    # devices (and two `tailscale up` connections) for one machine.
    TS_DEPLOY=false
    HB_OWN_TAILNET_NODE=false
    ts_auth_key=""
    ts_hb_hostname="hummingbot-api"
    HB_TAILSCALE_MODE=none
    use_tailscale="${use_tailscale_early:-N}"
    # Only a NATIVE daemon is a node Condor can reuse. A hummingbot-api
    # sidecar is a separate tailnet identity with its socket inside its
    # container: `tailscale ip` on the host returns nothing for it, so
    # treating it as reusable produced an install that reported "Tailscale
    # connected" with no address and a dashboard reachable from nowhere.
    #
    # A kernel-mode sidecar also OWNS tailscale0, so Condor cannot enroll
    # natively alongside it -- that is the collision this whole change
    # exists to prevent, and it must not be walked into here either. A
    # userspace sidecar holds no device, so enrolling is fine.
    # Two facts decide this, and neither needs to attribute a process to a
    # host or a container:
    #   can we USE a host daemon?   status answers AND yields an address
    #   is the device taken?        tailscale0 already exists
    #
    # Reuse requires the first. Enrolling requires the absence of the
    # second: `tailscale up` against a device something else owns fails,
    # and that something else is usually hummingbot-api's kernel-mode
    # sidecar.
    #
    # Deliberately NOT derived from tailnet_state(). That has to decide
    # whether a tailscaled belongs to this host or to a container, and it
    # cannot always -- an unreadable /proc, a cgroup with no recognised
    # marker, a docker daemon this user may not query. A sidecar
    # misattributed as native there has no host address, which used to
    # fall through to enrollment and contend for the very device the
    # sidecar was holding. Asking what we can use, and what is already
    # taken, has no such failure mode.
    _ts_host_usable=false
    if command_exists tailscale && tailscale status >/dev/null 2>&1 \
       && [ -n "$(tailscale ip -4 2>/dev/null | head -1)" ]; then
        _ts_host_usable=true
    fi
    _ts_device_busy=false
    tailnet_device_present && _ts_device_busy=true

    if [[ "${use_tailscale:-}" =~ ^[Yy]$ ]] && [ "$_ts_host_usable" != true ] \
       && [ "$_ts_device_busy" = true ]; then
        msg_warn "Something already owns this machine's tailnet device (tailscale0),"
        msg_warn "and it is not a daemon Condor can use — most likely hummingbot-api's"
        msg_warn "Tailscale sidecar running in kernel mode."
        msg_info "Joining now would contend for the same device and fail."
        msg_info "Fix it in $HB_API_DIR, then re-run this setup:"
        msg_info "  set TAILSCALE_MODE=host in its .env  (one shared node), or"
        msg_info "  set TAILSCALE_MODE=sidecar           (its own node, userspace)"
        msg_info "then run 'make deploy' there. Continuing without Tailscale for Condor."
        use_tailscale="N"
        use_tailscale_early="N"
        # Step 1 already recorded USE_TAILSCALE=true. Leaving it would put
        # the rest of the system at odds with what we just told the user:
        # main.py would try a tailnet bind, fail closed to loopback, and
        # doctor would report the dashboard as unreachable -- all correct
        # individually, and all confusing after "continuing without".
        USE_TAILSCALE=false
        set_env_var USE_TAILSCALE "false"
    fi
    if [[ "${use_tailscale:-}" =~ ^[Yy]$ ]]; then
        # Join the tailnet only if nothing here has already, and reuse only a
        # daemon we can actually use: one machine, one node. Reaching the else
        # means the device is free -- the busy case bailed out above -- so
        # enrolling cannot contend with anything. No auth key is asked for a
        # join that is skipped.
        if [ "$_ts_host_usable" = true ]; then
            msg_ok "Tailscale already active on this machine — reusing its node"
            msg_info "Not joining the tailnet a second time: one machine, one node."
        else
            show_tailscale_key_help
            prompt_tailscale_auth_key "Tailscale auth key (tskey-auth-...)"
            msg_info "Installing Tailscale on this machine..."
            curl -fsSL https://tailscale.com/install.sh | sh
            msg_info "Connecting to Tailscale network..."
            tailscale_up --authkey="$ts_auth_key" --hostname="condor" --accept-dns=true
        fi
        ts_condor_ip=$(tailscale ip -4 2>/dev/null | head -1)
        # Report what actually happened. Without an address there is no
        # tailnet bind and no working /web link, so announcing success
        # here just moves the failure somewhere harder to diagnose.
        if [ -n "${ts_condor_ip:-}" ]; then
            TS_DEPLOY=true
            msg_ok "Tailscale connected — this machine (and its dashboard) is reachable on your tailnet"
        else
            msg_warn "Tailscale did not report an address for this machine."
            msg_info "The dashboard will stay on localhost until \`tailscale status\` is healthy."
        fi
        set_tailnet_web_url

        echo ""
        msg_info "hummingbot-api runs on this same machine, so Condor reaches it over"
        msg_info "localhost — it doesn't need a tailnet node of its own for that."
        prompt_visible "Also give hummingbot-api its own Tailscale node, so other devices (e.g. an MCP client on another machine) can reach it directly? [y/N]" "N" "hb_tailscale_choice"
        if [[ "${hb_tailscale_choice:-}" =~ ^[Yy]$ ]]; then
            # A second node is safe now: this machine already runs a
            # kernel-mode daemon, so hummingbot-api's `make deploy` starts
            # its sidecar in userspace mode. Netstack claims no TUN device
            # and no host routes, so both coexist -- verified on a live
            # tailnet, serving a loopback-bound port to another node with a
            # single tailscale0 between them. Before that, answering yes
            # here produced a sidecar that died on startup and a container
            # that still reported "running".
            HB_OWN_TAILNET_NODE=true
            HB_TAILSCALE_MODE=sidecar
            if [ -z "${ts_auth_key:-}" ]; then
                msg_info "A separate node needs its own auth key (this machine's node is already registered)."
                prompt_tailscale_auth_key "Tailscale auth key for hummingbot-api (tskey-auth-...)"
            fi
            msg_ok "hummingbot-api will join the tailnet as '$ts_hb_hostname'"
            msg_info "If that name is already taken on your tailnet it registers as"
            msg_info "$ts_hb_hostname-1, -2 ... — the real one is confirmed after deploy."
        else
            # One machine, one node: port 8000 is served on the node this
            # host already has, so nothing new registers and nothing
            # contends for tailscale0.
            HB_TAILSCALE_MODE=host
            msg_ok "hummingbot-api will be served on this machine's existing tailnet node"
        fi
    fi

    echo ""
    prompt_required_visible "API admin username" "hb_username" "Username cannot be empty"
    prompt_required_secret "API admin password" "hb_password" "Password cannot be empty"
    prompt_required_secret "Config password" "hb_config_password" "Config password cannot be empty"

    # Save to condor's .env
    set_env_var DEPLOY_HUMMINGBOT_API "true"

    install_local_hummingbot_api
}

# Validate the checkout, run the API's own setup, then build and deploy it.
# Sets hb_api_deployed (configured) and hb_api_healthy (answering) separately.
install_local_hummingbot_api() {
    hb_api_checkout_ready=false
    if prepare_hummingbot_api_checkout; then
        hb_api_checkout_ready=true
    fi

    if [ "$hb_api_checkout_ready" = true ]; then
        hb_api_ready=false
        # Setup preserves existing .env and fills only missing profile files.
        # Condor's backtesting tuning is passed to the API's own writer.
        if configure_hummingbot_api; then
            hb_api_ready=true
        fi

        # Deploy if Docker is available
        if [ "$hb_api_ready" != true ]; then
            msg_warn "Skipping the API deploy: it has no configuration to start with."
            msg_info "Fix the error above, then: cd $HB_API_DIR && make deploy"
        elif [ "$docker_available" = true ] && [ -f "$HB_API_DIR/docker-compose.yml" ]; then
            msg_info "Building the current Hummingbot API source and deploying its stack..."
            # The API owns build, broker bootstrap, and Tailscale selection.
            # A failed build must not fall back to starting an old image.
            if ! command_exists make; then
                msg_warn "make is not installed, and the API deploy needs it:"
                msg_info "it builds the current source and prepares broker authentication before deployment."
                msg_info "Install make, then: cd $HB_API_DIR && make deploy"
            elif (cd "$HB_API_DIR" && make deploy); then
                msg_ok "Hummingbot API source built and stack deployed"

                # Wait for API to be healthy
                msg_info "Waiting for API to be ready..."
                for _ in $(seq 1 30); do
                    if api_health_check; then
                        msg_ok "Hummingbot API is healthy"
                        hb_api_healthy=true
                        break
                    fi
                    sleep 2
                done
                if ! api_health_check; then
                    msg_warn "API not responding yet (may still be starting)"
                    msg_info "Check status: cd $HB_API_DIR && docker compose ps"
                fi
            else
                msg_error "Hummingbot API build or deployment failed"
                msg_info "Try manually: cd $HB_API_DIR && make deploy"
            fi
        else
            msg_info "Start API later: cd $HB_API_DIR && make deploy"
        fi

        # hb_api_deployed means CONFIGURED, and drives the config.yml
        # credential sync -- which is right even when the stack did not
        # come up, since the credentials are valid and the API can be
        # started later. Whether it is actually RUNNING is a separate
        # fact, tracked by hb_api_healthy, because a deploy that failed
        # (no Docker, a compose error, a container that never answers)
        # must not be summarised as running.
        if [ "$hb_api_ready" = true ]; then
            msg_ok "Hummingbot API credentials saved"
            hb_api_deployed=true
        else
            set_env_var DEPLOY_HUMMINGBOT_API "false"
            msg_warn "Hummingbot API is not configured — Condor will start without it."
            msg_info "Once it is set up, add it to Condor via /servers or config.yml."
        fi
    else
        set_env_var DEPLOY_HUMMINGBOT_API "false"
        msg_warn "Skipping API configuration and deployment: checkout validation failed."
        msg_info "Existing source, configuration, and running services were left untouched."
    fi
}
