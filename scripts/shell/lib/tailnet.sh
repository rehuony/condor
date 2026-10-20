#!/usr/bin/env bash
# Tailscale helpers for the setup wizard. Sourced after lib/common.sh.

# Restore Tailscale wizard choice from .env (survives re-runs after Step 1 is skipped)
load_tailscale_choice() {
    case "${USE_TAILSCALE:-}" in
        [Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Yy]|1) use_tailscale_early="y" ;;
    esac
}

# Does the tailnet device exist on this host?
#
# /sys/class/net is present on every Linux kernel and reading it needs no
# iproute2, so a host without `ip` still gets a real answer rather than the
# permissive one -- previously a missing `ip` read as "device free" and sent
# a caller straight into the collision this is meant to detect.
#
# No /sys and no tools means this is not Linux, and a network_mode: host
# container cannot hold the host's device there in the first place, so
# "absent" is the correct answer rather than a guess.
tailnet_device_present() {
    [ -e /sys/class/net/tailscale0 ] && return 0
    command_exists ip && ip link show tailscale0 >/dev/null 2>&1 && return 0
    command_exists ifconfig && ifconfig tailscale0 >/dev/null 2>&1 && return 0
    return 1
}

# The name a node is ACTUALLY registered under on the tailnet.
#
# Tailscale suffixes a hostname that is already taken: ask for
# "hummingbot-api" on a tailnet that already has one -- a colleague's box, an
# earlier install, a machine someone rebuilt -- and this node comes up as
# "hummingbot-api-1". The requested name then belongs to somebody ELSE, and
# because their machine is very likely running the same software it answers,
# so the mistake surfaces as "401 Incorrect username or password" and sends
# people to check a password that was never wrong.
#
# `--self=true --peers=false` prints exactly one line whose second field is the
# assigned DNS label. No JSON parser needed, which matters inside the sidecar
# image (no python, no jq).
#
# Pass the command that reaches the right daemon:
#   tailnet_node_name                                              # this host
#   tailnet_node_name docker exec hummingbot-tailscale tailscale   # sidecar
tailnet_node_name() {
    local line
    if [ "$#" -eq 0 ]; then
        set -- tailscale
    fi
    line="$("$@" status --self=true --peers=false 2>/dev/null | grep -v '^[[:space:]]*$' | head -1)" || return 1
    [ -n "$line" ] || return 1
    printf '%s' "$line" | awk '{print $2}'
}

# Peers on this tailnet whose name looks like a hummingbot-api node, newline
# separated. Matches the requested name and the -1/-2 suffixes Tailscale adds,
# so the machine we are looking for is found under whichever one it got.
tailnet_api_peers() {
    local want="${1:-hummingbot-api}"
    tailscale status --peers 2>/dev/null \
        | awk '{print $2}' \
        | grep -E "^${want}(-[0-9]+)?$" || true
}

# Make sure a tailscaled is running, then `tailscale up`.
#
# Ask the service manager first, ALWAYS -- including on WSL2, which has shipped
# with systemd on by default since 0.67 and which the packaged tailscale
# installs a unit for. This used to branch straight to a hand-started daemon
# whenever /proc/version mentioned microsoft, and that daemon is a background
# child of this script with no nohup and no setsid: it died with the
# installer's shell, taking tailscale0 and the serve config with it, while
# setup had already printed "Tailscale connected" and written a tailnet
# dashboard URL. It also left tailscaled.service sitting there inactive and
# never enabled, so nothing brought it back on reboot either.
#
# That matters more than it used to: the tailnet work now resolves most
# machines to mode=host, which means `make deploy` -- today's and every future
# one -- depends on this daemon still being here.
tailscale_up() {
    if ! pgrep -x tailscaled >/dev/null 2>&1; then
        if command_exists systemctl && systemctl list-unit-files tailscaled.service >/dev/null 2>&1; then
            msg_info "Starting the tailscaled service..."
            # --now starts it; enable is what survives a reboot.
            sudo systemctl enable --now tailscaled >/dev/null 2>&1 || \
                sudo systemctl start tailscaled >/dev/null 2>&1 || true
            # The socket appears a moment after the unit reports active.
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                pgrep -x tailscaled >/dev/null 2>&1 && break
                sleep 1
            done
        fi
    fi

    if ! pgrep -x tailscaled >/dev/null 2>&1; then
        # No service manager, or no unit for it. Start it ourselves -- but
        # detached, so it outlives this script's terminal instead of dying
        # with it.
        msg_info "Starting the Tailscale daemon..."
        sudo mkdir -p /var/run/tailscale /var/lib/tailscale
        # The redirection has to happen INSIDE sudo. Written as
        # `sudo tailscaled ... >/var/log/tailscaled.log` the calling shell
        # opens that path as the invoking user, which fails on any normal
        # box before sudo runs at all -- and the daemon then never starts,
        # silently, which is the failure this whole function exists to avoid.
        # setsid detaches it from this terminal so it outlives the installer.
        sudo sh -c 'setsid tailscaled --state=/var/lib/tailscale/tailscaled.state \
                                      --socket=/var/run/tailscale/tailscaled.sock \
                                      >>/var/log/tailscaled.log 2>&1 </dev/null &'
        sleep 2
        # Say it out loud rather than leaving someone to discover it after a
        # reboot: nothing here arranges for this daemon to come back.
        msg_warn "Started without a service manager — this daemon will not restart on boot."
        msg_info "Re-run 'sudo tailscaled ... &' and 'sudo tailscale up' after a restart,"
        msg_info "or install a service for it."
    fi

    sudo tailscale up "$@"
}

# Where an auth key comes from, for a user who has never made one.
show_tailscale_key_help() {
    echo ""
    echo -e "  ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "  ${CYAN}  How to get a Tailscale auth key:${RESET}"
    echo -e "  ${CYAN}    1. Create a free account at https://tailscale.com${RESET}"
    echo -e "  ${CYAN}    2. Go to: https://tailscale.com/admin/settings/keys${RESET}"
    echo -e "  ${CYAN}    3. Click 'Generate auth key'${RESET}"
    echo -e "  ${CYAN}    4. Check 'Reusable' for multiple deployments${RESET}"
    echo -e "  ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
}

# Ask until the answer looks like an auth key; the result is ts_auth_key.
prompt_tailscale_auth_key() {
    while true; do
        prompt_visible "$1" "" "ts_auth_key"
        if [ -z "${ts_auth_key:-}" ]; then
            msg_warn "Auth key cannot be empty"
            continue
        fi
        if [[ ! "$ts_auth_key" =~ ^tskey-auth- ]]; then
            msg_warn "Auth key must start with 'tskey-auth-'"
            continue
        fi
        break
    done
}

# With Tailscale on, the dashboard binds this node's tailnet address, so that is
# the URL /web must hand out. Local mode is excluded: it keeps its loopback bind
# whether or not Tailscale is on (an unauthenticated dashboard does not go on the
# tailnet), so localhost:8088 stays the correct URL there.
set_tailnet_web_url() {
    if [ -n "${ts_condor_ip:-}" ] && [ "${CONDOR_MODE:-telegram}" != "local" ]; then
        set_env_var WEB_URL "http://$ts_condor_ip:8088"
        msg_ok "Web dashboard: http://$ts_condor_ip:8088 (Tailscale access)"
    fi
}
