"""Tests for the MCP server catalog builder. Run: python -m pytest -q scripts"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from PIL import Image

import build_index as bi

REPO = "Flow-Steward/mcps"


def _png(width: int = 64, height: int = 64) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (width, height), (10, 20, 30, 255)).save(out, format="PNG")
    return out.getvalue()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(bi, "ROOT", tmp_path)
    (tmp_path / bi.CATEGORIES_FILE).write_text(
        json.dumps({"productivity": "Productivity", "email": "Email"}), encoding="utf-8"
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "test")
    return tmp_path


def registry_record(name="com.example/mcp", url="https://mcp.example.com/mcp", status="active", **remote):
    return {
        "server": {
            "$schema": bi.SERVER_SCHEMA,
            "name": name,
            "description": "Example server",
            "version": "1.2.0",
            "remotes": [{"type": "streamable-http", "url": url, **remote}],
        },
        "_meta": {bi.OFFICIAL_META_KEY: {"status": status, "isLatest": True}},
    }


def catalog(**overrides):
    body = {
        "revision": 1,
        "source": {"registry": "com.example/mcp"},
        "title": "Example",
        "description": "Search and update example records in the workspaces you authorize.",
        "category": "productivity",
        "official": {"vendor": "Example", "evidence_url": "https://docs.example.com/mcp"},
        "remotes": [
            {
                "id": "default",
                "label": "Example",
                "url": "https://mcp.example.com/mcp",
                "allowed_hosts": ["mcp.example.com"],
                "auth": {"mode": "oauth2", "client_registration": "dcr"},
            }
        ],
    }
    body.update(overrides)
    return body


def write_entry(root: Path, item_id="example", *, record=None, server=None, body=None, icon=None):
    folder = root / bi.SERVERS_DIR / item_id
    folder.mkdir(parents=True, exist_ok=True)
    if server is not None:
        (folder / bi.SERVER_FILE).write_text(json.dumps(server), encoding="utf-8")
    else:
        (folder / bi.REGISTRY_FILE).write_text(json.dumps(record or registry_record()), encoding="utf-8")
    (folder / bi.CATALOG_FILE).write_text(yaml.safe_dump(body or catalog()), encoding="utf-8")
    (folder / bi.ICON_FILE).write_bytes(_png() if icon is None else icon)
    return folder


def problems(root: Path) -> str:
    with pytest.raises(bi.CatalogError) as caught:
        bi.build_index(repository=REPO)
    return str(caught.value)


def test_index_has_the_registry_list_shape_with_the_reviewed_overlay(root):
    write_entry(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "add example")

    index = bi.build_index(repository=REPO)

    assert index["schema_version"] == bi.SCHEMA_VERSION
    assert index["metadata"] == {"count": 1}
    record = index["servers"][0]
    assert record["_meta"] == {bi.OFFICIAL_META_KEY: {"status": "active", "isLatest": True}}
    server = record["server"]
    assert server["name"] == "com.example/mcp"
    overlay = server["_meta"][bi.CATALOG_META_KEY]
    assert overlay["id"] == "example"
    assert overlay["revision"] == 1
    assert overlay["status"] == "active"
    assert overlay["source"] == {"registry": "com.example/mcp", "version": "1.2.0"}
    assert overlay["summary"] == "Search and update example records in the workspaces you authorize."
    assert overlay["official"] == {"vendor": "Example", "evidence_url": "https://docs.example.com/mcp"}
    assert overlay["remotes"] == [
        {
            "remote_id": "default",
            "label": "Example",
            "url": "https://mcp.example.com/mcp",
            "allowed_hosts": ["mcp.example.com"],
            "user_defined_host": False,
            "auth": {
                "mode": "oauth2",
                "client_registration": "dcr",
                "requested_scopes": [],
                "authorization_parameters": {},
                "refresh_token_required": False,
            },
        }
    ]
    icon = overlay["icon"]
    sha = subprocess.run(
        ["git", "log", "-1", "--format=%H"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert icon["url"] == f"https://raw.githubusercontent.com/{REPO}/{sha}/servers/example/icon.png"
    assert icon["size"] == "64x64" and icon["mime_type"] == "image/png" and len(icon["sha256"]) == 64
    # Deterministic: the same tree gives the same bytes.
    assert bi.render(index) == bi.render(bi.build_index(repository=REPO))


def test_a_server_written_here_has_no_registry_bookkeeping(root):
    server = registry_record(name="io.github.flow-steward/example")["server"]
    write_entry(root, server=server, body=catalog(source=None))
    body = catalog()
    body.pop("source")
    (root / "servers/example/catalog.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    record = bi.build_index(repository=REPO)["servers"][0]

    assert "_meta" not in record
    assert record["server"]["_meta"][bi.CATALOG_META_KEY]["source"] == {
        "catalog": "io.github.flow-steward/example",
        "version": "1.2.0",
    }


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"title": "example_mcp"}, "title must be the product's name"),
        ({"title": "mcp"}, "title must be the product's name"),
        ({"description": "Too short."}, "description needs at least"),
        ({"category": "nope"}, "category 'nope' is not in"),
        ({"revision": 0}, "revision must be a whole number"),
        ({"surprise": 1}, "unknown keys ['surprise']"),
        ({"official": {"vendor": "Example"}}, "official needs the vendor and an https evidence_url"),
        ({"source": {"registry": "com.other/mcp"}}, "source.registry says 'com.other/mcp'"),
        ({"remotes": []}, "remotes must list at least one endpoint"),
    ],
)
def test_entry_fields_are_checked(root, change, expected):
    write_entry(root, body=catalog(**change))
    assert expected in problems(root)


def _remote(**changes):
    remote = catalog()["remotes"][0]
    remote.update(changes)
    return remote


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        (_remote(url="https://elsewhere.example.com/mcp"), "is not a streamable-http remote"),
        (_remote(allowed_hosts=["other.example.com"]), "host mcp.example.com is outside allowed_hosts"),
        (_remote(allowed_hosts=[]), "allowed_hosts is required"),
        (_remote(auth={"mode": "TODO"}), "auth.mode must be one of"),
        (_remote(auth={"mode": "oauth2", "client_registration": "maybe"}), "auth.client_registration"),
        (
            _remote(auth={"mode": "oauth2", "client_registration": "dcr", "authorization_parameters": {"scope": "x"}}),
            "must not set ['scope']",
        ),
        (_remote(auth={"mode": "none", "requested_scopes": ["x"]}), "only allowed with auth.mode oauth2"),
        (_remote(auth={"mode": "headers"}), "declares no headers"),
        (_remote(id="Bad Id"), "id must be lowercase"),
    ],
)
def test_remotes_are_checked(root, remote, expected):
    write_entry(root, body=catalog(remotes=[remote]))
    assert expected in problems(root)


def test_wildcard_hosts_match_subdomains_only():
    assert bi.host_allowed("mcp.example.com", ["*.example.com"])
    assert not bi.host_allowed("example.com", ["*.example.com"])
    assert not bi.host_allowed("evilexample.com", ["*.example.com"])
    assert bi.host_allowed("MCP.Example.com.", ["mcp.example.com"])


def test_host_variables_need_choices_inside_allowed_hosts_or_a_user_defined_host(root):
    url = "https://{api_host}/mcp"
    choices = {"api_host": {"choices": ["api.example.com", "api.eu.example.com"]}}
    write_entry(
        root,
        record=registry_record(url=url, variables=choices),
        body=catalog(remotes=[_remote(url=url, allowed_hosts=["api.example.com"])]),
    )
    assert "host api.eu.example.com is outside allowed_hosts" in problems(root)

    write_entry(
        root,
        record=registry_record(url=url, variables=choices),
        body=catalog(remotes=[_remote(url=url, allowed_hosts=["*.example.com"])]),
    )
    assert bi.build_index(repository=REPO)["metadata"]["count"] == 1

    free = {"api_host": {"description": "your instance"}}
    write_entry(
        root,
        record=registry_record(url=url, variables=free),
        body=catalog(remotes=[_remote(url=url, allowed_hosts=["*.example.com"])]),
    )
    assert "has no choices; set user_defined_host: true" in problems(root)

    write_entry(
        root,
        record=registry_record(url=url, variables=free),
        body=catalog(remotes=[_remote(url=url, allowed_hosts=[], user_defined_host=True)]),
    )
    overlay = bi.build_index(repository=REPO)["servers"][0]["server"]["_meta"][bi.CATALOG_META_KEY]
    assert overlay["remotes"][0]["user_defined_host"] is True


def test_oauth_refuses_required_credential_headers_but_allows_optional_ones(root):
    optional = [{"name": "Authorization", "isSecret": True}]
    write_entry(root, record=registry_record(headers=optional))
    assert bi.build_index(repository=REPO)["metadata"]["count"] == 1

    required = [{"name": "Authorization", "isSecret": True, "isRequired": True}]
    write_entry(root, record=registry_record(headers=required))
    assert "must not require credential headers" in problems(root)


def test_the_same_endpoint_may_be_offered_with_two_sign_in_methods(root):
    headers = [{"name": "Authorization", "isSecret": True}]
    write_entry(
        root,
        record=registry_record(headers=headers),
        body=catalog(remotes=[_remote(), _remote(id="api-token", auth={"mode": "headers"})]),
    )
    remotes = bi.build_index(repository=REPO)["servers"][0]["server"]["_meta"][bi.CATALOG_META_KEY]["remotes"]
    assert [(r["remote_id"], r["auth"]["mode"]) for r in remotes] == [
        ("default", "oauth2"),
        ("api-token", "headers"),
    ]


def test_a_deleted_registry_server_must_be_removed(root):
    write_entry(root, record=registry_record(status="deleted"))
    assert "the Registry deleted com.example/mcp" in problems(root)


def test_a_deprecated_registry_server_stays_listed_as_deprecated(root):
    write_entry(root, record=registry_record(status="deprecated"))
    overlay = bi.build_index(repository=REPO)["servers"][0]["server"]["_meta"][bi.CATALOG_META_KEY]
    assert overlay["status"] == "deprecated"


@pytest.mark.parametrize(
    ("icon", "expected"),
    [
        (b"GIF89a", "is not a PNG"),
        (_png(64, 48), "must be square"),
        (_png(16, 16), "must be square, 32"),
    ],
)
def test_icons_are_checked(root, icon, expected):
    write_entry(root, icon=icon)
    assert expected in problems(root)


def test_an_entry_needs_exactly_one_server_record(root):
    folder = write_entry(root)
    (folder / bi.SERVER_FILE).write_text(json.dumps(registry_record()["server"]), encoding="utf-8")
    assert "needs exactly one of registry.json or server.json" in problems(root)


def test_a_server_written_here_must_not_carry_a_secret(root):
    server = registry_record(
        name="io.github.flow-steward/example",
        headers=[{"name": "X-Api-Key", "isSecret": True, "value": "sk-live-123"}],
    )["server"]
    body = catalog()
    body.pop("source")
    write_entry(root, server=server, body=body)
    assert "carries a secret value" in problems(root)


def test_a_changed_entry_must_raise_its_revision(root):
    write_entry(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "add")
    _git(root, "branch", "base")
    (root / "servers/example/icon.png").write_bytes(_png(96, 96))
    _git(root, "commit", "-qam", "new icon")
    assert bi.revision_problems("base") == ["example: changed, so raise revision above 1"]

    body = yaml.safe_load((root / "servers/example/catalog.yaml").read_text())
    body["revision"] = 2
    (root / "servers/example/catalog.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    _git(root, "commit", "-qam", "raise revision")
    assert bi.revision_problems("base") == []


def test_the_published_catalog_passes_its_own_check():
    """The real entries in servers/ — the state a pull request is merged into."""
    assert bi.main(["--check", "--repository", REPO]) == 0
