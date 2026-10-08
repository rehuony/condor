"""Research links never open the host filesystem or bypass report ownership."""

from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from condor import paths
from condor.web import auth
from condor.web.models import WebUser
from condor.web.routes import research

ADMIN = WebUser(id=111, role="admin")
TRADER = WebUser(id=222, role="user")
BODY = "# 调研报告\n\n[数据](data.json)\n\n|币种|价格|\n|---|---|\n|BTC|1|\n"


@pytest.fixture
def files(tmp_path, monkeypatch):
    root = paths.research_dir()
    topic = root / "topic" / "2026-10-10"
    topic.mkdir(parents=True)
    (topic / "复盘 report.md").write_text(BODY, encoding="utf-8")
    (topic / "data.json").write_text('{"price": 1}', encoding="utf-8")
    (topic / "100%.txt").write_text("percent", encoding="utf-8")
    (topic / "large.md").write_bytes(b"x" * (research.MAX_TEXT_PREVIEW + 1))
    (topic / "binary.txt").write_bytes(b"\xff\x00\xfe")
    (root / ".env").write_text("PRIVATE")
    (root / "hidden-alias.txt").symlink_to(root / ".env")
    outside = tmp_path / "secret.md"
    outside.write_text("OUTSIDE")
    (root / "escape.md").symlink_to(outside)
    (root / "escape-dir").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(
        auth,
        "get_config_manager",
        lambda: type("Config", (), {"is_admin": lambda _, uid: uid == ADMIN.id})(),
    )
    return root


def client_for(user=None):
    app = FastAPI()
    app.include_router(research.router, prefix="/api/v1")
    if user:
        app.dependency_overrides[auth.get_current_user] = lambda: user
    return TestClient(app)


@pytest.mark.parametrize("download", [False, True])
def test_requires_authentication_and_admin(files, download):
    url = f"/api/v1/research/topic/2026-10-10/data.json?download={int(download)}"
    with client_for() as client:
        assert client.get(url).status_code in (401, 403)
    with client_for(TRADER) as client:
        assert client.get(url).status_code == 403


def test_preview_and_download_preserve_unicode_and_original_bytes(files):
    path = "topic/2026-10-10/复盘 report.md"
    url = "/api/v1/research/" + quote(path)
    with client_for(ADMIN) as client:
        response = client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert data["path"] == path
        assert data["name"] == "复盘 report.md"
        assert data["preview"] == "markdown"
        assert data["content"] == BODY
        assert data["title"] == "调研报告"
        assert data["body"] == BODY.split("\n\n", 1)[1]
        assert data["size"] == len(BODY.encode())
        assert str(files) not in response.text
        assert response.headers["cache-control"] == "private, no-store"
        response = client.get(url + "?download=1")
        assert response.content == BODY.encode()
        assert response.headers["content-disposition"].startswith("attachment;")
        assert "filename*=utf-8''" in response.headers["content-disposition"]
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    [
        "%2e%2e/secret.md",
        "topic/%2e%2e/%2e%2e/secret.md",
        "escape.md",
        "hidden-alias.txt",
        "escape-dir/secret.md",
        ".env",
        "topic",
        "missing.md",
        "/etc/passwd",
        "topic%5c..%5csecret.md",
        "topic/%00.md",
        "%252e%252e/secret.md",
    ],
)
def test_rejects_traversal_hidden_files_directories_and_missing_files(files, path):
    with client_for(ADMIN) as client:
        for suffix in ("", "?download=1"):
            response = client.get("/api/v1/research/" + path + suffix)
            assert response.status_code == 404
            assert "OUTSIDE" not in response.text
            assert "PRIVATE" not in response.text
            assert str(files) not in response.text


@pytest.mark.parametrize("name", ["large.md", "binary.txt"])
def test_unpreviewable_files_still_download_intact(files, name):
    with client_for(ADMIN) as client:
        url = f"/api/v1/research/topic/2026-10-10/{name}"
        data = client.get(url).json()
        assert data["preview"] == "none"
        assert data["content"] is None
        assert (
            client.get(url + "?download=1").content
            == (files / "topic/2026-10-10" / name).read_bytes()
        )


def test_literal_percent_filename_is_not_decoded_twice(files):
    with client_for(ADMIN) as client:
        assert (
            client.get("/api/v1/research/topic/2026-10-10/100%25.txt").json()["content"]
            == "percent"
        )


def test_route_is_registered_in_dashboard():
    from condor.web.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/api/v1/research/topic/file.md").status_code in (401, 403)


def test_standard_report_uses_metadata_without_changing_source_or_download(files):
    content = (
        '---\ntitle: "Spot---market review"\nsummary: "Wait for deeper liquidity."\n'
        'created_at: "2026-10-10T09:30:00+08:00"\n---\n\n'
        "# Spot---market review\n\n## Findings\n\nRead [data](data/prices.json).\n"
    )
    (files / "report.md").write_text(content)
    with client_for(ADMIN) as client:
        data = client.get("/api/v1/research/report.md").json()
        assert data["title"] == "Spot---market review"
        assert data["summary"] == "Wait for deeper liquidity."
        assert data["created_at"] == "2026-10-10T09:30:00+08:00"
        assert data["body"].startswith("## Findings")
        assert data["content"] == content
        assert client.get("/api/v1/research/report.md?download=1").text == content


@pytest.mark.parametrize(
    "frontmatter", ["title: [broken", "- not-a-mapping", "!!python/object:evil {}"]
)
def test_bad_metadata_does_not_destroy_or_execute_report_content(files, frontmatter):
    content = f"---\n{frontmatter}\n---\n\n# Findings\n\nEvidence."
    (files / "report.md").write_text(content)
    with client_for(ADMIN) as client:
        data = client.get("/api/v1/research/report.md").json()
        assert data["content"] == content
        assert data["body"] == content
        assert data["title"] == "report.md"


def test_metadata_accepts_only_text_and_timezone_aware_dates(files):
    content = "---\ntitle: [not, text]\nsummary: {nested: value}\ncreated_at: 2026-10-10\n---\n\n# Findings\n\nEvidence."
    (files / "report.md").write_text(content)
    with client_for(ADMIN) as client:
        data = client.get("/api/v1/research/report.md").json()
        assert data["title"] == "Findings"
        assert data["summary"] == ""
        assert data["created_at"] is None


def test_shipped_report_template_matches_the_reader(files):
    from pathlib import Path

    template = Path(__file__).parents[1] / "agents/_defaults/research-report.md"
    (files / "report.md").write_bytes(template.read_bytes())
    with client_for(ADMIN) as client:
        data = client.get("/api/v1/research/report.md").json()
        assert data["title"] == "研究主题与范围"
        assert data["summary"]
        assert data["created_at"]
        assert data["body"].startswith("## 关键发现")
