"""Condor's operational scripts under scripts/shell, driven through the Makefile.

The Makefile only dispatches; each target must name a script action that exists.
Source updates touch Git only, verify the personal repository, and refuse anything
but a clean main. `.env` edits keep values byte for byte.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts" / "shell"
ORIGIN = "https://github.com/rehuony/condor.git"


def run(*args, cwd, env=None, check=True):
    return subprocess.run(
        list(args),
        cwd=cwd,
        env=(os.environ | (env or {})),
        capture_output=True,
        text=True,
        check=check,
    )


# ── Makefile dispatch ──


def test_every_make_target_dispatches_to_an_existing_script_action():
    makefile = (ROOT / "Makefile").read_text()
    targets = re.findall(r"^([a-z-]+):\n\t@(.+)$", makefile, re.M)
    assert {name for name, _ in targets} >= {
        "setup",
        "install",
        "doctor",
        "check-update",
        "update",
        "build",
        "run",
        "run-fg",
        "stop",
        "restart",
        "status",
        "logs",
    }
    for name, recipe in targets:
        if name == "help":
            continue
        match = re.fullmatch(r"bash (scripts/shell/[a-z-]+\.sh)(?: ([a-z-]+))?", recipe)
        if match is None:
            assert recipe.startswith("uv run "), (name, recipe)
            continue
        script = ROOT / match.group(1)
        assert script.is_file(), (name, script)
        if match.group(2):
            assert re.search(
                rf"^\s+(?:[a-z-]+\|)*{match.group(2)}(?:\|[a-z-]+)*\)",
                script.read_text(),
                re.M,
            ), (
                name,
                match.group(2),
            )


def test_help_is_the_default_target():
    result = run("make", "--no-print-directory", cwd=ROOT)
    assert result.stdout.startswith("Usage: make <command> (default: help)")


def test_no_operational_script_is_left_at_the_repository_root():
    assert not list(ROOT.glob("*.sh"))


# ── .env editing ──


@pytest.mark.parametrize("value", ["a&b", "x|y", "http://h:8088/?a=1&b=2", "plain"])
def test_set_env_var_keeps_values_and_other_lines(tmp_path, value):
    env = tmp_path / ".env"
    env.write_text("KEEP=1\nKEY=old\n# comment\n")
    script = f'. "{SHELL}/lib/common.sh"; ENV_FILE="{env}"; set_env_var KEY "$VALUE"; set_env_var NEW added'
    run("bash", "-c", script, cwd=tmp_path, env={"VALUE": value})

    assert env.read_text() == f"KEEP=1\nKEY={value}\n# comment\nNEW=added\n"
    assert env.stat().st_mode & 0o777 == 0o600
    read = run(
        "bash",
        "-c",
        f'. "{SHELL}/lib/common.sh"; ENV_FILE="{env}"; read_env_var KEY',
        cwd=tmp_path,
    )
    assert read.stdout == value


# ── source updates ──


@pytest.fixture
def repos(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    remote = tmp_path / "remote.git"
    run("git", "init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    work = tmp_path / "condor"
    run("git", "clone", "-q", str(remote), str(work), cwd=tmp_path)
    for key, value in (("user.name", "Ops Test"), ("user.email", "ops@example.test")):
        run("git", "config", key, value, cwd=work)
    # The script sees the personal URL; fetches go to the local bare repository.
    run("git", "remote", "set-url", "origin", ORIGIN, cwd=work)
    run("git", "config", f"url.{remote}.insteadOf", ORIGIN, cwd=work)
    # A copy, not a symlink: lib/common.sh resolves the checkout through symlinks.
    shutil.copytree(ROOT / "scripts", work / "scripts")
    (work / ".gitignore").write_text("scripts\n.env\n")
    commit(work, "base")
    run("git", "push", "-q", "-u", "origin", "main", cwd=work)
    return work, remote


def commit(repo, name):
    (repo / f"{name}.txt").write_text(name)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-q", "-m", name, cwd=repo)


def advance_remote(work, remote, tmp_path):
    other = tmp_path / "other"
    run("git", "clone", "-q", str(remote), str(other), cwd=tmp_path)
    for key, value in (("user.name", "Ops Test"), ("user.email", "ops@example.test")):
        run("git", "config", key, value, cwd=other)
    commit(other, "remote-change")
    run("git", "push", "-q", "origin", "main", cwd=other)


def source_update(work, action):
    return run("bash", "scripts/shell/source-update.sh", action, cwd=work, check=False)


def state(result):
    return re.search(r"Source status: (\S+)", result.stdout).group(1)


def test_check_reports_each_relation_to_main(repos, tmp_path):
    work, remote = repos
    assert state(source_update(work, "check")) == "up_to_date"

    advance_remote(work, remote, tmp_path)
    assert state(source_update(work, "check")) == "update_available"

    commit(work, "local")
    assert state(source_update(work, "check")) == "diverged"

    run("git", "reset", "-q", "--hard", "FETCH_HEAD", cwd=work)
    commit(work, "ahead")
    assert state(source_update(work, "check")) == "ahead"


def test_update_fast_forwards_a_clean_main_and_keeps_ignored_state(repos, tmp_path):
    work, remote = repos
    (work / ".env").write_text("SECRET=kept\n")
    advance_remote(work, remote, tmp_path)

    result = source_update(work, "update")

    assert result.returncode == 0, result.stderr
    assert (work / "remote-change.txt").exists()
    assert (work / ".env").read_text() == "SECRET=kept\n"
    assert "make restart" in result.stdout


@pytest.mark.parametrize("problem", ["dirty", "branch", "origin"])
def test_update_refuses_and_changes_nothing(repos, tmp_path, problem):
    work, remote = repos
    advance_remote(work, remote, tmp_path)
    if problem == "dirty":
        (work / "base.txt").write_text("edited")
    elif problem == "branch":
        run("git", "switch", "-q", "-c", "feature", cwd=work)
    else:
        run(
            "git",
            "remote",
            "set-url",
            "origin",
            "https://github.com/other/condor.git",
            cwd=work,
        )
    head = run("git", "rev-parse", "HEAD", cwd=work).stdout

    result = source_update(work, "update")

    assert result.returncode != 0
    assert run("git", "rev-parse", "HEAD", cwd=work).stdout == head
    assert not (work / "remote-change.txt").exists()


# ── templates ──


def test_the_config_template_is_committed_and_renders_for_an_admin():
    ignored = run(
        "git", "check-ignore", "-q", "templates/config.yml", cwd=ROOT, check=False
    )
    assert ignored.returncode == 1, "templates/config.yml is ignored by .gitignore"

    text = (ROOT / "templates" / "config.yml").read_text()
    data = yaml.safe_load(text.replace("ADMIN_USER_ID_PLACEHOLDER", "4242"))
    assert data["admin_id"] == 4242
    assert data["server_access"]["local"]["owner_id"] == 4242
    assert data["chat_defaults"] == {4242: "local"}
    assert data["servers"]["local"]["username"] == ""
    assert "ADMIN_USER_ID_PLACEHOLDER" in (ROOT / "scripts/shell/setup.sh").read_text()
