#!/usr/bin/env bash
# Toolchain installation for the setup wizard. Sourced after lib/common.sh.

# Install uv, tmux, Node.js/npm (via nvm) and TypeScript when missing.
install_dependencies() {
    echo -e "${BOLD}Step 0: Installing Dependencies${RESET}"
    echo ""

    SUDO_CMD=""
    if [ "${EUID:-0}" -ne 0 ] && command_exists sudo; then
        SUDO_CMD="sudo"
    fi

    # Track if we need to restart the script
    NEEDS_RESTART=false

    # ── Install uv ──────────────────────────────────────

    if ! command_exists uv; then
        msg_info "Installing uv (https://docs.astral.sh/uv/)..."
        if curl -LsSf https://astral.sh/uv/install.sh | sh; then
            msg_ok "uv installed successfully"
            NEEDS_RESTART=true
            refresh_path

            # Verify installation
            if ! command_exists uv; then
                msg_warn "uv installed but not immediately available. Will retry after PATH refresh."
            else
                msg_ok "uv is now available"
            fi
        else
            msg_error "Failed to install uv automatically."
            msg_info "Please install manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
            exit 1
        fi
    else
        msg_ok "uv is already installed ($(uv --version 2>/dev/null || echo 'version unknown'))"
    fi

    # ── Install tmux ────────────────────────────────────

    if ! command_exists tmux; then
        msg_info "Installing tmux..."

        if command_exists apt-get; then
            $SUDO_CMD apt-get update -qq && $SUDO_CMD apt-get install -y tmux || {
                msg_error "Failed to install tmux via apt-get."
                exit 1
            }
        elif command_exists yum; then
            $SUDO_CMD yum install -y tmux || {
                msg_error "Failed to install tmux via yum."
                exit 1
            }
        elif command_exists dnf; then
            $SUDO_CMD dnf install -y tmux || {
                msg_error "Failed to install tmux via dnf."
                exit 1
            }
        elif command_exists brew; then
            brew install tmux || {
                msg_error "Failed to install tmux via Homebrew."
                exit 1
            }
        else
            msg_error "No supported package manager found. Please install tmux manually."
            exit 1
        fi
        msg_ok "tmux installed successfully"
    else
        msg_ok "tmux is already installed ($(tmux -V 2>/dev/null || echo 'version unknown'))"
    fi

    # ── Install Node.js and npm via nvm ────────────────

    if ! command_exists node || ! command_exists npm; then
        # If node exists but npm doesn't, we still need to install via nvm
        if command_exists node && ! command_exists npm; then
            msg_warn "Node.js found but npm is missing. Installing via nvm..."
        else
            msg_info "Installing Node.js and npm via nvm..."
        fi

        # Install nvm (Node Version Manager)
        if [ ! -d "$HOME/.nvm" ]; then
            msg_info "Installing nvm..."
            if curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash; then
                msg_ok "nvm installed successfully"
            else
                msg_error "Failed to install nvm."
                msg_info "Install manually: https://github.com/nvm-sh/nvm"
                exit 1
            fi
        else
            msg_ok "nvm is already installed"
        fi

        # Load nvm into current shell (in lieu of restarting)
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
        [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"

        # Verify nvm is loaded
        if ! command_exists nvm; then
            msg_warn "nvm not immediately available, sourcing profile..."
            # Try to source nvm from common locations
            for profile in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.zshrc" "$HOME/.profile"; do
                if [ -f "$profile" ] && grep -q "NVM_DIR" "$profile"; then
                    source "$profile" 2>/dev/null || true
                    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
                    break
                fi
            done
        fi

        # Install Node.js LTS (version 24)
        msg_info "Installing Node.js v24 (LTS)..."
        if nvm install 24; then
            nvm use 24
            nvm alias default 24
            msg_ok "Node.js v24 installed and set as default"
            NEEDS_RESTART=true
        else
            msg_error "Failed to install Node.js via nvm."
            exit 1
        fi

        # Load node into current shell
        export PATH="$NVM_DIR/versions/node/$(nvm version)/bin:$PATH"

    else
        msg_ok "Node.js is already installed ($(node --version 2>/dev/null))"
        msg_ok "npm is available ($(npm --version 2>/dev/null))"
    fi

    # Verify npm is available - final check with better error handling
    if ! command_exists npm; then
        msg_warn "npm not found, attempting to load nvm..."

        # Try to load nvm and node
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

        if command_exists nvm; then
            nvm use default 2>/dev/null || nvm use node 2>/dev/null || nvm use 24 2>/dev/null || true
            export PATH="$NVM_DIR/versions/node/$(nvm version)/bin:$PATH"
        fi

        # Final check
        if ! command_exists npm; then
            msg_error "npm still not available."
            msg_info "Your system has Node.js $(node --version 2>/dev/null) installed without npm."
            msg_info ""
            msg_info "Options:"
            msg_info "  1. Install npm: sudo apt-get install npm (or your package manager)"
            msg_info "  2. Or let this script install via nvm (uninstall system node first)"
            msg_info ""
            msg_info "To use nvm: sudo apt-get remove nodejs && make setup"
            exit 1
        fi
    fi

    # ── Install TypeScript globally ─────────────────────

    if ! command_exists tsc && ! npm list -g typescript >/dev/null 2>&1; then
        msg_info "Installing TypeScript globally..."
        if npm install -g typescript; then
            msg_ok "TypeScript installed successfully"
            NEEDS_RESTART=true
            refresh_path
        else
            msg_error "Failed to install TypeScript globally."
            msg_info "You can install it later with: npm install -g typescript"
            # Don't exit - TypeScript might not be critical for all setups
        fi
    else
        msg_ok "TypeScript is already installed ($(tsc --version 2>/dev/null || echo 'installed'))"
    fi

    # ── Handle script restart if needed ─────────────────

    if [ "$NEEDS_RESTART" = true ]; then
        msg_info "Dependencies were installed. Refreshing environment..."
        refresh_path

        # Verify critical commands are now available
        missing_commands=()
        command_exists uv || missing_commands+=("uv")
        command_exists node || missing_commands+=("node")
        command_exists npm || missing_commands+=("npm")

        if [ ${#missing_commands[@]} -gt 0 ]; then
            msg_warn "Some commands still not available: ${missing_commands[*]}"
            msg_info "Restarting script with refreshed environment..."
            echo ""

            # Re-execute the wizard in a new shell with the refreshed environment.
            exec bash "$REPO_ROOT/scripts/shell/setup.sh"
        else
            msg_ok "All dependencies are now available!"
        fi
    fi
}
