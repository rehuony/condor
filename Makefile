.DEFAULT_GOAL := help
.PHONY: help setup install pick-model setup-chrome refresh-codex doctor check-update update build-frontend run run-fg stop restart status logs test lint

# Command-line Make values are exported, never interpolated into shell code.
ifneq ($(origin CONDOR_TMUX_SESSION),undefined)
export CONDOR_TMUX_SESSION
endif

help:
	@printf '%s\n' \
	  'Usage: make <command> (default: help)' \
	  'Configuration' \
	  '  setup           Interactive setup wizard; preserves existing configuration' \
	  '  install         Setup, then Python and dashboard dependencies, Chrome and doctor' \
	  '  pick-model      Choose the AI model Condor thinks with' \
	  '  setup-chrome    Install the headless Chrome used for chart rendering' \
	  '  refresh-codex   Update the Codex ACP bridge and its bundled CLI' \
	  'Source' \
	  '  check-update    Fetch Git metadata and compare this checkout with origin/main' \
	  '  update          Fast-forward a clean main; no dependency sync, build or restart' \
	  'Service' \
	  '  build-frontend  Build the dashboard and swap it in place' \
	  '  run             Build the dashboard, then start Condor in tmux session "condor"' \
	  '  run-fg          Build the dashboard, then run Condor in the foreground' \
	  '  stop            Stop Condor' \
	  '  restart         Stop, then run' \
	  '  status          Report whether Condor is running' \
	  '  logs            Attach to the session (detach: Ctrl+B then D)' \
	  'Maintenance' \
	  '  doctor          Diagnose dependencies, configuration and Hummingbot API access' \
	  '  test            Run the backend tests' \
	  '  lint            Format with black and isort'

setup:
	@bash scripts/shell/setup.sh
install:
	@bash scripts/shell/dev.sh install
pick-model:
	@bash scripts/shell/dev.sh pick-model
setup-chrome:
	@bash scripts/shell/dev.sh setup-chrome
refresh-codex:
	@bash scripts/shell/dev.sh refresh-codex
doctor:
	@bash scripts/shell/dev.sh doctor
check-update:
	@bash scripts/shell/source-update.sh check
update:
	@bash scripts/shell/source-update.sh update
build-frontend:
	@bash scripts/shell/build-frontend.sh
run:
	@bash scripts/shell/service.sh run
run-fg:
	@bash scripts/shell/service.sh run-fg
stop:
	@bash scripts/shell/service.sh stop
restart:
	@bash scripts/shell/service.sh restart
status:
	@bash scripts/shell/service.sh status
logs:
	@bash scripts/shell/service.sh logs
test:
	@uv run pytest
lint:
	@uv run black . && uv run isort .
