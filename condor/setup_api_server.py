"""Point Condor's ``local`` server at a hummingbot-api — the installer's config writer.

``make setup`` (``scripts/shell/lib/hummingbot-api.sh``) runs ``uv run python -m
condor.setup_api_server`` once the API's address and credentials are known. The edit
goes through :class:`config_manager.ConfigManager`, the same structured writer the
bot uses: an atomic write with a ``.bak``, other servers, permissions and chat
defaults kept, and no write at all over a ``config.yml`` that cannot be parsed.

Credentials never travel on the command line, where any local user can read them in
the process list. They come from the API's own ``.env`` (``--credentials-env``,
parsed with python-dotenv and never executed) or from ``CONDOR_API_USERNAME`` /
``CONDOR_API_PASSWORD`` in the environment, and they are stored as YAML strings, so
values such as ``true``, ``0123`` or ``kept: password`` round-trip unchanged. Errors
name what was wrong, never the values.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

SERVER_NAME = "local"


class SetupError(Exception):
    """A reason this run changed nothing; safe to print."""


def read_credentials(env_file: Path | None) -> tuple[str, str]:
    """``(username, password)`` from the API ``.env`` or the environment."""
    if env_file is not None:
        if not env_file.is_file():
            raise SetupError(f"{env_file} does not exist")
        values = dotenv_values(env_file, interpolate=False)
        username, password = values.get("USERNAME"), values.get("PASSWORD")
        source = str(env_file)
    else:
        username = os.environ.get("CONDOR_API_USERNAME")
        password = os.environ.get("CONDOR_API_PASSWORD")
        source = "CONDOR_API_USERNAME/CONDOR_API_PASSWORD"
    if not username or not password:
        raise SetupError(f"{source} does not provide both USERNAME and PASSWORD")
    return username, password


def write_server(
    config_path: Path,
    host: str,
    port: int,
    username: str,
    password: str,
    owner: int | None,
) -> None:
    """Create or update the ``local`` server and make it usable by ``owner``."""
    from config_manager import ConfigManager

    cm = ConfigManager(str(config_path))
    if cm._load_failed:
        raise SetupError(f"{config_path} cannot be parsed; it was left unchanged")

    if cm.get_server(SERVER_NAME) is None:
        cm.add_server(SERVER_NAME, host, port, username, password, owner_id=owner)
    else:
        cm.modify_server(
            SERVER_NAME, host=host, port=port, username=username, password=password
        )
    cm.ensure_server_registered(SERVER_NAME, owner)
    if cm.get_default_server() not in cm.list_servers():
        cm.set_default_server(SERVER_NAME)
    chats = {str(chat) for chat in cm._data.get("chat_defaults") or {}}
    if owner is not None and str(owner) not in chats:
        cm.set_chat_default_server(owner, SERVER_NAME)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("config.yml"))
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--credentials-env",
        type=Path,
        help="hummingbot-api .env to read USERNAME and PASSWORD from",
    )
    parser.add_argument("--owner", help="Telegram user id that owns the server")
    args = parser.parse_args(argv)

    owner = int(args.owner) if args.owner and args.owner.isdigit() else None
    # ConfigManager logs YAML parse errors, which quote the offending line; in a
    # config.yml that line can be a password. The outcome is reported below instead.
    logging.disable(logging.CRITICAL)
    try:
        if not 0 < args.port < 65536:
            raise SetupError(f"port {args.port} is outside 1-65535")
        username, password = read_credentials(args.credentials_env)
        write_server(args.config, args.host, args.port, username, password, owner)
    except SetupError as exc:
        print(f"config.yml not updated: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # the message of an unexpected error may quote values
        print(
            f"config.yml not updated: unexpected {type(exc).__name__}", file=sys.stderr
        )
        return 1
    finally:
        logging.disable(logging.NOTSET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
