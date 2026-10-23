"""Bind ports can differ from public login URLs behind a reverse proxy."""

import pytest

from utils.config import resolve_web_host, resolve_web_url_and_port


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, ("http://localhost:8088", 8088)),
        ({"WEB_PORT": "8080"}, ("http://localhost:8080", 8080)),
        (
            {"WEB_URL": "https://dashboard.example.com"},
            ("https://dashboard.example.com", 443),
        ),
        (
            {"WEB_URL": "http://dashboard.example.com"},
            ("http://dashboard.example.com", 80),
        ),
        (
            {"WEB_URL": "http://dashboard.example.com:8088"},
            ("http://dashboard.example.com:8088", 8088),
        ),
        ({"WEB_PORT": " "}, ("http://localhost:8088", 8088)),
    ],
)
def test_existing_web_config_defaults(env, expected):
    assert resolve_web_url_and_port(env) == expected


def test_https_proxy_uses_private_listener_and_public_login_url():
    env = {
        "WEB_HOST": "127.0.0.1",
        "WEB_PORT": " 8080 ",
        "WEB_URL": " https://dashboard.example.com/ ",
    }
    assert resolve_web_host(env) == "127.0.0.1"
    assert resolve_web_url_and_port(env) == ("https://dashboard.example.com", 8080)


def test_explicit_bind_port_overrides_public_explicit_port():
    assert resolve_web_url_and_port(
        {"WEB_PORT": "8080", "WEB_URL": "https://dashboard.example.com:8443"}
    ) == ("https://dashboard.example.com:8443", 8080)
