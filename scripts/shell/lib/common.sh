#!/usr/bin/env bash
# Shared helpers for Condor's operational scripts. Sourcing this file resolves the
# checkout and changes into it; it never installs, starts, or edits anything.
REPO_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT" || return 1

# uv and cargo installs land here; a non-login shell (make, tmux) may not have them.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

ENV_FILE="${ENV_FILE:-.env}"
CONFIG_FILE="${CONFIG_FILE:-config.yml}"

# ── Colors & Output Helpers ──────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

msg_info()  { echo -e "  ${CYAN}→${RESET} $1"; }
msg_ok()    { echo -e "  ${GREEN}✓${RESET} $1"; }
msg_warn()  { echo -e "  ${YELLOW}!${RESET} $1"; }
msg_error() { echo -e "  ${RED}✗${RESET} $1"; }

# Prompt with default value display
prompt_visible() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"
    if [ -n "$default" ]; then
        echo -ne "  ${prompt} ${DIM}[${default}]${RESET}: " >&2
    else
        echo -ne "  ${prompt}: " >&2
    fi
    read -r value < /dev/tty || value=""
    value=$(echo "$value" | tr -d '[:space:]')
    if [ -z "$value" ] && [ -n "$default" ]; then
        value="$default"
    fi
    eval "$var_name=\"$value\""
}

# Prompt for passwords (no echo). Never echoes ``default`` in cleartext --
# unlike prompt_visible, a bracketed hint here would defeat the whole point
# of a masked prompt the moment a caller passed one.
#
# Only the *edges* are trimmed. prompt_visible strips all whitespace, which is
# right for a token or a hostname but wrong for a password: the user would
# type "correct horse battery", get "correcthorsebattery" written to two
# different .env files, and then be unable to log in anywhere with the
# passphrase they believe they set. Inner whitespace is rejected instead --
# see prompt_required_secret -- because .env is read by three different
# parsers (python-dotenv, docker compose, and `source`) that do not agree on
# how to quote it.
prompt_secret() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"
    if [ -n "$default" ]; then
        echo -ne "  ${prompt} ${DIM}[hidden]${RESET}: " >&2
    else
        echo -ne "  ${prompt}: " >&2
    fi
    read -rs value < /dev/tty || value=""
    echo "" >&2
    value="$(printf '%s' "$value" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    if [ -z "$value" ] && [ -n "$default" ]; then
        value="$default"
    fi
    eval "$var_name=\"$value\""
}

# Prompt visibly, looping until non-empty. No default -- for values (like
# credentials) that must never silently fall back to something guessable.
prompt_required_visible() {
    local prompt="$1" var_name="$2" warn_msg="${3:-This cannot be empty}"
    while true; do
        prompt_visible "$prompt" "" "$var_name"
        if [ -z "${!var_name}" ]; then
            msg_warn "$warn_msg"
            continue
        fi
        break
    done
}

# Same as prompt_required_visible, but masked (see prompt_secret).
prompt_required_secret() {
    local prompt="$1" var_name="$2" warn_msg="${3:-This cannot be empty}"
    while true; do
        prompt_secret "$prompt" "" "$var_name"
        if [ -z "${!var_name}" ]; then
            msg_warn "$warn_msg"
            continue
        fi
        if [[ "${!var_name}" =~ [[:space:]] ]]; then
            msg_warn "Spaces are not supported in this value -- please retype it without any"
            continue
        fi
        break
    done
}

# Escape special characters for .env file
escape_env_value() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//\$/\\\$}"
    echo "$value"
}

# Set (or replace) KEY=VALUE in .env, creating the file if it does not exist.
#
# Rewritten line by line rather than with `sed s|...|value|`: in a replacement `&`
# means "the matched text" and `|` ends the expression, so a value carrying either
# was silently corrupted. The file is published by rename and kept owner-only,
# because it holds the bot token and the API password.
set_env_var() {
    local key="$1" value line tmp found=false
    value="$(escape_env_value "$2")"
    [ -f "$ENV_FILE" ] || : > "$ENV_FILE"
    tmp="$(mktemp "$ENV_FILE.XXXXXX")" || return 1
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" == "$key="* ]]; then
            [ "$found" = true ] || printf '%s=%s\n' "$key" "$value"
            found=true
        else
            printf '%s\n' "$line"
        fi
    done < "$ENV_FILE" > "$tmp"
    [ "$found" = true ] || printf '%s=%s\n' "$key" "$value" >> "$tmp"
    chmod 600 "$tmp" 2>/dev/null || true
    mv "$tmp" "$ENV_FILE"
}

# Read one KEY out of .env *without executing the file*.
#
# `source` runs .env as a shell script, so one value containing shell
# metacharacters (an API key with parentheses is enough) is a syntax error that
# aborts the read and silently drops every variable below that line. That is how
# a hand-added CONDOR_MODE=local could become invisible here — and then be
# rewritten to `telegram` by the legacy-install migration below. The variables
# that decide the mode are read with this instead.
read_env_var() {
    local key="$1" line value="" char quote i
    [ -f "$ENV_FILE" ] || return 0
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$ENV_FILE" 2>/dev/null | tail -1)" || return 0
    [ -n "$line" ] || return 0
    line="${line#*=}"
    line="${line#"${line%%[![:space:]]*}"}"
    # Quoting follows python-dotenv and Compose: single quotes are literal, double
    # quotes take \" and \\ escapes, and an unquoted value ends at " #".
    case "$line" in
        \'*)
            line="${line#\'}"
            value="${line%%\'*}"
            ;;
        \"*)
            line="${line#\"}"
            quote=false
            for ((i = 0; i < ${#line}; i++)); do
                char="${line:i:1}"
                if [ "$quote" = true ]; then
                    case "$char" in
                        n) value+=$'\n' ;;
                        \"|\\) value+="$char" ;;
                        *) value+="\\$char" ;;
                    esac
                    quote=false
                elif [ "$char" = "\\" ]; then
                    quote=true
                elif [ "$char" = '"' ]; then
                    break
                else
                    value+="$char"
                fi
            done
            ;;
        *)
            value="$(printf '%s' "$line" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')"
            ;;
    esac
    printf '%s' "$value"
}

# OSC 8 clickable hyperlink (falls back to plain URL)
make_link() {
    local url="$1"
    local text="${2:-$url}"
    # Check if terminal supports hyperlinks (most modern terminals do)
    if [ -n "${TERM:-}" ] && [ "${TERM:-}" != "dumb" ]; then
        echo -e "\033]8;;${url}\033\\${text}\033]8;;\033\\"
    else
        echo "$text ($url)"
    fi
}

# Refresh PATH to include common installation locations
refresh_path() {
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
    
    # Load nvm if available
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
    
    # Add nvm node to PATH if nvm is available
    if command_exists nvm; then
        export PATH="$NVM_DIR/versions/node/$(nvm version 2>/dev/null)/bin:$PATH"
    fi
    
    # Also source profile files if they exist
    [ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc" 2>/dev/null || true
    [ -f "$HOME/.profile" ] && source "$HOME/.profile" 2>/dev/null || true
    [ -f "$HOME/.bash_profile" ] && source "$HOME/.bash_profile" 2>/dev/null || true
}

# Check if a command exists and is executable
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Put nvm's default Node first on PATH when nvm is installed. Make recipes and tmux
# panes start non-interactive shells that never read the profile that loads it.
load_node() {
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ -s "$NVM_DIR/nvm.sh" ]; then
        # shellcheck source=/dev/null
        . "$NVM_DIR/nvm.sh" >/dev/null 2>&1
        nvm use default >/dev/null 2>&1 || nvm use node >/dev/null 2>&1 || true
    fi
}
