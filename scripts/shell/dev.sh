#!/usr/bin/env bash
# Installation and environment tools: install, setup-chrome, doctor, pick-model,
# refresh-codex. None of them starts or restarts Condor.
set -uo pipefail
# shellcheck source=scripts/shell/lib/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

# Bounded, and says which way it failed (condor/setup_chrome.py). It always exits 0:
# charts are optional, and an optional renderer must not fail an install.
setup_chrome() {
    echo "Setting up Chrome for chart rendering..."
    uv run python -m condor.setup_chrome || true
}

# Node is loaded first so the doctor sees the same npm and CLI bridges Condor will.
doctor() {
    load_node
    uv run python -m condor.doctor
}

case "${1:-}" in
    install)
        bash "$REPO_ROOT/scripts/shell/setup.sh" || exit 1
        uv sync --locked --dev || exit 1
        load_node
        (cd "$REPO_ROOT/frontend" && npm ci) || exit 1
        setup_chrome
        doctor || true
        ;;
    setup-chrome) setup_chrome ;;
    doctor) doctor ;;
    pick-model) exec uv run python -m condor.setup_llm ;;
    refresh-codex)
        load_node
        has_npm() { command -v npm >/dev/null 2>&1; }
        has_npm || { echo "npm not found. Install Node.js or configure nvm."; exit 1; }
        npm install -g @agentclientprotocol/codex-acp@latest \
            && npm list -g @agentclientprotocol/codex-acp @openai/codex --depth=1 \
            && echo "Start a new Codex session in Condor to use the updated bridge."
        ;;
    *)
        echo "Usage: bash scripts/shell/dev.sh {install|setup-chrome|doctor|pick-model|refresh-codex}" >&2
        exit 1
        ;;
esac
