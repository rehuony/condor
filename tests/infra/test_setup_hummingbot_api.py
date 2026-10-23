"""The local API installer: checkout ownership, configuration order, and config.yml.

Credentials used here are fictional. A preserved API ``.env`` may hold hand-written,
quoted values; they must reach Condor's ``config.yml`` as the same YAML strings,
without executing ``.env``, without passing through argv, and without disturbing the
rest of the configuration.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

from condor import setup_api_server

SHELL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "shell"
SCRIPT = "\n".join(
    (SHELL_DIR / name).read_text()
    for name in ("setup.sh", "lib/common.sh", "lib/hummingbot-api.sh")
)
REPOSITORY = "https://github.com/rehuony/hummingbot-api.git"


def function(name):
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}", SCRIPT, re.M | re.S)
    assert match, name
    return match.group()


HELPERS = "\n".join(
    function(name)
    for name in (
        "read_env_var",
        "local_api_host",
        "update_config_api_server",
        "prepare_hummingbot_api_checkout",
        "configure_hummingbot_api",
        "install_local_hummingbot_api",
        "sync_api_server_config",
        "parse_api_url",
    )
)
INSTALL = "install_local_hummingbot_api"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    repo = tmp_path / "api"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Installer Test")
    git(repo, "config", "user.email", "installer@example.test")
    (repo / "scripts/shell").mkdir(parents=True)
    (repo / "scripts/shell/setup.sh").write_text(
        'printf "setup\\n" >> "$CALLS"\n'
        '[ -f .env ] || printf "USERNAME=generated\\nPASSWORD=generated-password\\n" > .env\n'
        'printf "%s\\n" "$HBAPI_NONINTERACTIVE" "$HBAPI_USERNAME" '
        '"$HBAPI_PASSWORD" "$HBAPI_CONFIG_PASSWORD" "$TAILSCALE_ENABLED" '
        '"$TAILSCALE_MODE" "$TAILSCALE_AUTH_KEY" "$TAILSCALE_HOSTNAME" '
        '"$HBAPI_BACKTESTING_MAX_CONCURRENT" "$HBAPI_BACKTESTING_TIMEOUT_SECONDS" '
        '"${HBAPI_SKIP_DEPS-unset}" > "$PARAMETERS"\n'
    )
    (repo / "Makefile").write_text("deploy:\n\t@true\n")
    (repo / "docker-compose.yml").write_text("services: {}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    git(repo, "remote", "add", "origin", REPOSITORY)
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "branch", "--set-upstream-to=origin/main", "main")
    return repo


def run_installer(tmp_path, repo, *, code=INSTALL, prefix="", **overrides):
    env = (
        os.environ
        | {
            "HB_API_DIR": str(repo),
            "HB_API_REPO": REPOSITORY,
            "CALLS": str(tmp_path / "calls"),
            "PARAMETERS": str(tmp_path / "parameters"),
            "hb_username": "test-admin",
            "hb_password": "test-password",
            "hb_config_password": "test-config-password",
            "HB_TAILSCALE_MODE": "sidecar",
            "ts_auth_key": "test-auth-key",
            "ts_hb_hostname": "test-api",
            "docker_available": "true",
        }
        | overrides
    )
    harness = """
msg_info() { printf '%s\\n' "$1"; }
msg_ok() { printf '%s\\n' "$1"; }
msg_warn() { printf '%s\\n' "$1"; }
msg_error() { printf '%s\\n' "$1"; }
set_env_var() { printf '%s=%s\\n' "$1" "$2" >> "$CALLS"; }
command_exists() { return 0; }
api_health_check() { return 0; }
make() { printf 'make %s\\n' "$*" >> "$CALLS"; return "${MAKE_RESULT:-0}"; }
"""
    return subprocess.run(
        ["bash", "-c", harness + HELPERS + "\n" + prefix + "\n" + code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def calls(tmp_path):
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


def test_configuration_precedes_single_deploy_and_preserves_parameters(
    tmp_path, checkout
):
    result = run_installer(tmp_path, checkout)
    assert result.returncode == 0, result.stderr
    assert calls(tmp_path) == ["setup", "make deploy"]
    assert (tmp_path / "parameters").read_text().splitlines() == [
        "1",
        "test-admin",
        "test-password",
        "test-config-password",
        "true",
        "sidecar",
        "test-auth-key",
        "test-api",
        "4",
        "1800",
        "unset",
    ]
    assert "source built and stack deployed" in result.stdout
    assert "test-password" not in result.stdout + result.stderr


def test_existing_env_and_local_changes_are_preserved(tmp_path, checkout):
    env = checkout / ".env"
    env.write_text("USERNAME=existing\nPASSWORD=keep-this\n")
    local = checkout / "Makefile"
    local.write_text(local.read_text() + "# local change\n")
    before = git(checkout, "status", "--porcelain")
    expected = env.read_bytes(), local.read_bytes()
    result = run_installer(
        tmp_path,
        checkout,
        HB_TAILSCALE_MODE="none",
        code=INSTALL + '\nprintf "%s|%s" "$hb_username" "$hb_password" > synced',
    )
    assert result.returncode == 0
    assert calls(tmp_path) == ["setup", "make deploy"]
    assert (env.read_bytes(), local.read_bytes()) == expected
    assert (tmp_path / "synced").read_text() == "existing|keep-this"
    assert git(checkout, "status", "--porcelain") == before
    assert (tmp_path / "parameters").read_text().splitlines()[4:6] == ["false", "none"]


@pytest.mark.parametrize(
    "invalid", ["origin", "branch", "detached", "upstream", "shallow", "script"]
)
def test_invalid_checkout_never_runs_setup_or_deploy(tmp_path, checkout, invalid):
    (checkout / ".env").write_text("PASSWORD=existing-secret\n")
    if invalid == "origin":
        git(checkout, "remote", "set-url", "origin", "https://github.com/other/api.git")
    elif invalid == "branch":
        git(checkout, "switch", "-c", "other")
    elif invalid == "detached":
        git(checkout, "checkout", "--detach")
    elif invalid == "upstream":
        git(checkout, "branch", "--unset-upstream")
    elif invalid == "shallow":
        (checkout / ".git/shallow").write_text(
            git(checkout, "rev-parse", "HEAD") + "\n"
        )
    else:
        (checkout / "scripts/shell/setup.sh").unlink()
    before = git(checkout, "status", "--porcelain"), git(checkout, "rev-parse", "HEAD")
    result = run_installer(tmp_path, checkout)
    assert calls(tmp_path) == ["DEPLOY_HUMMINGBOT_API=false"]
    assert "checkout validation failed" in result.stdout
    assert (
        git(checkout, "status", "--porcelain"),
        git(checkout, "rev-parse", "HEAD"),
    ) == before
    assert (checkout / ".env").read_text() == "PASSWORD=existing-secret\n"
    assert "existing-secret" not in result.stdout + result.stderr


def test_non_repo_directory_is_preserved(tmp_path, checkout):
    directory = checkout / "nested"
    directory.mkdir()
    result = run_installer(tmp_path, directory)
    assert calls(tmp_path) == ["DEPLOY_HUMMINGBOT_API=false"]
    assert "Git checkout root" in result.stdout
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("setup", ["exit 9\n", "true\n"])
def test_setup_failure_or_missing_env_prevents_deploy(tmp_path, checkout, setup):
    (checkout / "scripts/shell/setup.sh").write_text(setup)
    result = run_installer(tmp_path, checkout)
    assert calls(tmp_path) == ["DEPLOY_HUMMINGBOT_API=false"]
    assert "Skipping the API deploy" in result.stdout


def test_deploy_failure_has_no_fallback_and_is_not_reported_running(tmp_path, checkout):
    result = run_installer(tmp_path, checkout, MAKE_RESULT="8")
    assert calls(tmp_path) == ["setup", "make deploy"]
    assert "build or deployment failed" in result.stdout
    assert "stack deployed" not in result.stdout
    assert "is healthy" not in result.stdout


def test_clone_uses_personal_main_and_full_history(tmp_path, checkout):
    target = tmp_path / "new-api"
    prefix = """
git() {
    if [ "$1" = clone ]; then
        printf 'git %s\\n' "$*" >> "$CALLS"
        command git clone --branch main --single-branch "$FIXTURE_REPO" "$HB_API_DIR" >/dev/null 2>&1 || return
        command git -C "$HB_API_DIR" remote set-url origin "$HB_API_REPO"
    else
        command git "$@"
    fi
}
"""
    result = run_installer(tmp_path, target, prefix=prefix, FIXTURE_REPO=str(checkout))
    assert result.returncode == 0, result.stderr
    assert calls(tmp_path) == [
        f"git clone --branch main --single-branch {REPOSITORY} {target}",
        "setup",
        "make deploy",
    ]
    assert git(target, "rev-parse", "--is-shallow-repository") == "false"
    assert git(target, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/main"


# ── config.yml: from the API .env to the final YAML ──

ROOT = Path(__file__).resolve().parents[2]
DEPLOYED_SYNC = "sync_api_server_config"

# (line as written in .env, value every reader must agree on)
TRICKY = [
    ("PASSWORD='!test-secret'", "!test-secret"),
    ("PASSWORD='kept: password'", "kept: password"),
    ("PASSWORD=true", "true"),
    ("PASSWORD=0123", "0123"),
    ("PASSWORD=null", "null"),
    ("PASSWORD=a=b=c", "a=b=c"),
    ("PASSWORD=pa#ss", "pa#ss"),
    ("PASSWORD=plain # trailing comment", "plain"),
    ("PASSWORD='has # hash'", "has # hash"),
    ('PASSWORD="dq \\"quoted\\" & more"', 'dq "quoted" & more'),
    ("export PASSWORD='x&y|z'", "x&y|z"),
]


def bash(tmp_path, code, **env):
    harness = """
msg_info() { printf '%s\\n' "$1"; }
msg_ok() { printf '%s\\n' "$1"; }
msg_warn() { printf '%s\\n' "$1"; }
msg_error() { printf '%s\\n' "$1"; }
# `uv run python -m ...` without uv: the test interpreter, with the repo importable.
uv() { shift 2; "$PYTHON" "$@"; }
"""
    return subprocess.run(
        ["bash", "-c", harness + HELPERS + "\n" + code],
        cwd=tmp_path,
        env=os.environ
        | {
            "PYTHON": sys.executable,
            "PYTHONPATH": str(ROOT),
            "CONFIG_FILE": str(tmp_path / "config.yml"),
            "HB_API_DIR": str(tmp_path / "api"),
            "ADMIN_USER_ID": "4242",
        }
        | env,
        capture_output=True,
        text=True,
        check=False,
    )


def api_env(tmp_path, *lines):
    api = tmp_path / "api"
    api.mkdir(exist_ok=True)
    (api / ".env").write_text("\n".join(("USERNAME=test-admin", *lines)) + "\n")
    return api / ".env"


@pytest.mark.parametrize(("line", "expected"), TRICKY, ids=[v for _, v in TRICKY])
def test_env_values_reach_config_yml_as_the_same_string(tmp_path, line, expected):
    env_file = api_env(tmp_path, line)
    assert dotenv_values(env_file)["PASSWORD"] == expected

    read = bash(tmp_path, 'ENV_FILE="$HB_API_DIR/.env" read_env_var PASSWORD')
    assert read.stdout == expected

    result = bash(tmp_path, DEPLOYED_SYNC, hb_api_deployed="true")
    assert result.returncode == 0, result.stderr
    server = yaml.safe_load((tmp_path / "config.yml").read_text())["servers"]["local"]
    assert server == {
        "host": "127.0.0.1",
        "port": 8000,
        "username": "test-admin",
        "password": expected,
    }
    assert expected not in result.stdout + result.stderr


def test_credentials_never_appear_in_the_helpers_arguments(tmp_path):
    api_env(tmp_path, "PASSWORD=argv-secret")
    result = bash(
        tmp_path,
        'uv() { printf "%s\\n" "$@" > args; env > environment; }\n'
        'update_config_api_server 127.0.0.1 8000 "$HB_API_DIR/.env"\n'
        "update_config_api_server example.test 8000",
        hb_username="typed-admin",
        hb_password="typed-secret",
    )
    assert result.returncode == 0, result.stderr
    args = (tmp_path / "args").read_text()
    assert "argv-secret" not in args and "typed-secret" not in args
    assert "CONDOR_API_PASSWORD=typed-secret" in (tmp_path / "environment").read_text()


def test_typed_credentials_are_written_from_the_environment(tmp_path):
    result = bash(
        tmp_path,
        "update_config_api_server api.example.test 9000",
        hb_username="typed-admin",
        hb_password="yes",
    )
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load((tmp_path / "config.yml").read_text())
    assert data["servers"]["local"] == {
        "host": "api.example.test",
        "port": 9000,
        "username": "typed-admin",
        "password": "yes",
    }
    assert data["default_server"] == "local"
    assert data["server_access"]["local"]["owner_id"] == 4242
    assert data["chat_defaults"] == {4242: "local"}


def test_an_existing_configuration_is_updated_not_replaced(tmp_path):
    api_env(tmp_path, "PASSWORD='new: secret'")
    original = {
        "servers": {
            "local": {
                "host": "localhost",
                "port": 8000,
                "username": "",
                "password": "",
            },
            "remote": {"host": "vps", "port": 8000, "username": "r", "password": "r"},
        },
        "default_server": "remote",
        "admin_id": 4242,
        "users": {4242: {"user_id": 4242, "role": "admin"}},
        "server_access": {
            "local": {"owner_id": 7, "created_at": 1, "shared_with": {8: "trader"}},
            "remote": {"owner_id": 4242, "created_at": 1, "shared_with": {}},
        },
        "chat_defaults": {4242: "remote"},
        "user_preferences": {4242: {"theme": "dark"}},
        "version": 1,
    }
    (tmp_path / "config.yml").write_text(yaml.safe_dump(original))

    result = bash(tmp_path, DEPLOYED_SYNC, hb_api_deployed="true")

    assert result.returncode == 0, result.stderr
    data = yaml.safe_load((tmp_path / "config.yml").read_text())
    assert data["servers"]["local"]["password"] == "new: secret"
    assert data["servers"]["remote"] == original["servers"]["remote"]
    assert data["server_access"] == original["server_access"]
    assert data["default_server"] == "remote"
    assert data["chat_defaults"] == {4242: "remote"}
    assert data["user_preferences"] == original["user_preferences"]


def test_an_unparseable_config_is_left_alone_and_nothing_leaks(tmp_path):
    api_env(tmp_path, "PASSWORD=leak-check-secret")
    broken = "servers:\n  local: {host: [unclosed\n  password: leak-check-secret\n"
    (tmp_path / "config.yml").write_text(broken)

    result = bash(
        tmp_path,
        DEPLOYED_SYNC + '\n[ "${config_updated:-}" = true ] && echo UPDATED',
        hb_api_deployed="true",
    )

    assert (tmp_path / "config.yml").read_text() == broken
    assert "Could not update" in result.stdout
    assert "UPDATED" not in result.stdout
    assert "leak-check-secret" not in result.stdout + result.stderr


def test_missing_credentials_change_nothing(tmp_path):
    api = tmp_path / "api"
    api.mkdir()
    (api / ".env").write_text("USERNAME=test-admin\n")
    result = bash(tmp_path, DEPLOYED_SYNC, hb_api_deployed="true")

    assert "Could not update" in result.stdout
    assert not (tmp_path / "config.yml").exists()


def test_the_helper_rejects_an_out_of_range_port(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CONDOR_API_USERNAME", "u")
    monkeypatch.setenv("CONDOR_API_PASSWORD", "p")
    config = tmp_path / "config.yml"

    assert (
        setup_api_server.main(["--config", str(config), "--host", "h", "--port", "0"])
        == 1
    )
    assert not config.exists()
    assert "outside 1-65535" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("bind", "host"),
    [
        (None, "127.0.0.1"),
        ("0.0.0.0", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("192.168.1.20", "192.168.1.20"),
        ("::", "[::1]"),
        ("::1", "[::1]"),
        ("fd00::5", "[fd00::5]"),
        ("[fd00::5]", "[fd00::5]"),
    ],
)
def test_condor_connects_where_api_bind_listens(tmp_path, bind, host):
    api_env(tmp_path, "PASSWORD=test-password", *([f"API_BIND={bind}"] if bind else []))
    assert bash(tmp_path, "local_api_host").stdout.strip() == host

    result = bash(tmp_path, DEPLOYED_SYNC, hb_api_deployed="true")
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load((tmp_path / "config.yml").read_text())
    assert data["servers"]["local"]["host"] == host


def test_no_credential_is_read_or_written_with_text_tools():
    assert not re.search(r"(USERNAME|PASSWORD)=.*\|\s*cut", SCRIPT)
    assert not re.search(r"sed [^\n]*(username|password):", SCRIPT)
    assert (
        "python3 -c \"from urllib.parse import urlparse; p=urlparse('${" not in SCRIPT
    )


@pytest.mark.parametrize(
    ("url", "parsed"),
    [
        ("http://localhost:8000", "http|localhost|8000"),
        ("https://api.example.test", "https|api.example.test|443"),
        ("http://vps.example.test", "http|vps.example.test|8000"),
        ("http://[fd00::5]:9000", "http|[fd00::5]|9000"),
        (
            "http://x'); import os; os.system('touch pwned",
            "http|x'); import os; os.system('touch pwned|8000",
        ),
    ],
)
def test_the_remote_url_is_parsed_as_data(tmp_path, url, parsed):
    result = bash(
        tmp_path,
        'parse_api_url "$URL_IN"; printf "%s|%s|%s" "$HB_API_PROTOCOL" "$HB_API_HOST" "$HB_API_PORT"',
        URL_IN=url,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == parsed
    assert not (tmp_path / "pwned").exists()
