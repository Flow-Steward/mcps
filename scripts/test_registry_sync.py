"""Tests for scripts/registry_sync.py, without network access."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml
from PIL import Image

import build_index as bi
import registry_sync as rs
from test_build_index import catalog, registry_record, root, write_entry  # noqa: F401 - fixture


def test_signals_separate_domain_namespaces_from_github_accounts_and_aggregators():
    vendor = rs.signals(registry_record(name="com.example/mcp", url="https://mcp.example.com/mcp"))
    assert vendor["namespace"] == "domain example.com"
    assert vendor["endpoints_on_namespace_domain"] is True
    assert vendor["aggregator"] is False

    elsewhere = rs.signals(registry_record(name="com.example/mcp", url="https://mcp.other.dev/mcp"))
    assert elsewhere["endpoints_on_namespace_domain"] is False

    account = rs.signals(registry_record(name="io.github.someone/tool", url="https://tool.dev/mcp"))
    assert account["namespace"] == "github account"

    aggregator = rs.signals(registry_record(name="ai.smithery/someone-tool", url="https://server.smithery.ai/x"))
    assert aggregator["aggregator"] is True


def test_draft_leaves_sign_in_for_a_reviewer_and_fails_the_check(root, monkeypatch):  # noqa: F811
    record = registry_record()
    text = rs.draft_catalog(record, category="productivity", hints={}, website="https://example.com")
    draft = yaml.safe_load(text)
    assert draft["remotes"][0]["auth"] == {"mode": "TODO"}
    assert draft["remotes"][0]["allowed_hosts"] == ["mcp.example.com"]
    assert draft["website_url"] == "https://example.com"
    assert "official" not in draft

    folder = write_entry(root)
    (folder / bi.CATALOG_FILE).write_text(text, encoding="utf-8")
    with pytest.raises(bi.CatalogError, match="auth.mode must be one of"):
        bi.build_index(repository="Flow-Steward/mcps")


def test_sync_stores_the_new_record_and_raises_the_revision(root, monkeypatch):  # noqa: F811
    folder = write_entry(root)
    newer = registry_record()
    newer["server"]["version"] = "1.3.0"
    monkeypatch.setattr(rs, "registry_latest", lambda name: newer)

    line = rs.sync_entry(folder)

    assert line == "- `example` (com.example/mcp): version 1.2.0 → 1.3.0"
    assert json.loads((folder / bi.REGISTRY_FILE).read_text())["server"]["version"] == "1.3.0"
    assert yaml.safe_load((folder / bi.CATALOG_FILE).read_text())["revision"] == 2
    assert rs.sync_entry(folder) is None  # unchanged now


def test_sync_reports_a_deletion_for_a_reviewer_to_act_on(root, monkeypatch):  # noqa: F811
    folder = write_entry(root)
    monkeypatch.setattr(rs, "registry_latest", lambda name: registry_record(status="deleted"))
    line = rs.sync_entry(folder)
    assert "status active → deleted" in line and "remove the entry" in line
    # The entry is not removed automatically, and the check now refuses it.
    assert folder.is_dir()
    with pytest.raises(bi.CatalogError, match="the Registry deleted"):
        bi.build_index(repository="Flow-Steward/mcps")


def test_sync_leaves_a_server_the_registry_no_longer_knows(root, monkeypatch):  # noqa: F811
    folder = write_entry(root)
    monkeypatch.setattr(rs, "registry_latest", lambda name: None)
    assert "no longer in the Registry" in rs.sync_entry(folder)
    assert yaml.safe_load((folder / bi.CATALOG_FILE).read_text())["revision"] == 1


def test_sync_skips_servers_written_here(root, monkeypatch):  # noqa: F811
    body = catalog()
    body.pop("source")
    folder = write_entry(root, server=registry_record(name="io.github.flow-steward/x")["server"], body=body)
    monkeypatch.setattr(rs, "registry_latest", lambda name: pytest.fail("must not ask the Registry"))
    assert rs.sync_entry(folder) is None


def _image(size: tuple[int, int], fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, (200, 0, 0, 255)).save(out, format=fmt)
    return out.getvalue()


def test_icons_become_square_pngs_no_larger_than_the_target():
    wide = Image.open(io.BytesIO(rs.normalize_icon(_image((300, 150)))))
    assert wide.format == "PNG" and wide.size == (rs.ICON_PX, rs.ICON_PX)
    small = Image.open(io.BytesIO(rs.normalize_icon(_image((48, 48)))))
    assert small.size == (48, 48)
    ico = Image.open(io.BytesIO(rs.normalize_icon(_image((64, 64), "ICO"))))
    assert ico.size == (64, 64)
    with pytest.raises(ValueError, match="at least 32"):
        rs.normalize_icon(_image((16, 16)))


def test_icon_sources_prefer_registry_rasters_then_sites_then_the_github_org(monkeypatch):
    monkeypatch.setattr(rs, "fetch", lambda url, **_kw: (404, {}, b""))
    server = {
        "name": "com.example/mcp",
        "icons": [
            {"src": "https://cdn.example.com/logo.svg", "mimeType": "image/svg+xml"},
            {"src": "https://cdn.example.com/logo.png", "mimeType": "image/png"},
        ],
        "repository": {"url": "https://github.com/example-org/mcp"},
    }
    urls = rs.icon_candidates(server)
    assert urls[0] == "https://cdn.example.com/logo.png"
    assert "https://cdn.example.com/logo.svg" not in urls
    assert "https://example.com/apple-touch-icon.png" in urls
    assert urls[-1] == f"https://github.com/example-org.png?size={rs.ICON_PX}"


def test_probe_suggests_oauth_with_dcr_from_discovery(monkeypatch):
    def fake_fetch(url, **_kw):
        if url == "https://mcp.example.com/mcp":
            return 401, {"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/prm"'}, b""
        raise AssertionError(url)

    documents = {
        "https://mcp.example.com/prm": {"authorization_servers": ["https://auth.example.com"]},
        "https://auth.example.com/.well-known/oauth-authorization-server": {
            "registration_endpoint": "https://auth.example.com/register"
        },
    }

    def fake_json(url):
        if url in documents:
            return documents[url]
        raise LookupError(url)

    monkeypatch.setattr(rs, "fetch", fake_fetch)
    monkeypatch.setattr(rs, "fetch_json", fake_json)
    hint = rs.probe_auth("https://mcp.example.com/mcp")
    assert hint["suggested"] == {"mode": "oauth2", "client_registration": "dcr"}


def test_bump_revision_keeps_comments(tmp_path: Path):
    path = tmp_path / "catalog.yaml"
    path.write_text("# reviewed by a person\nrevision: 4\ntitle: X\n", encoding="utf-8")
    rs._bump_revision(path)
    assert path.read_text(encoding="utf-8") == "# reviewed by a person\nrevision: 5\ntitle: X\n"
