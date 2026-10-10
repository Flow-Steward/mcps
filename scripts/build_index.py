#!/usr/bin/env python3
"""Build the Flow Steward MCP server catalog from the reviewed entries in servers/.

Every listed server is one folder, ``servers/<id>/``, holding:

* ``catalog.yaml`` — what a reviewer checked: title, description, category, the
  vendor's own page that names this endpoint (``official.evidence_url``), and for
  every endpoint the hosts it may point at and how a user signs in;
* ``registry.json`` — the server's record from the official MCP Registry, written
  by ``scripts/registry_sync.py`` and never edited by hand; or ``server.json`` — a
  record in the Registry's ``server.json`` format written here, for a server the
  Registry does not carry (Google Workspace, for example);
* ``icon.png`` — the icon Flow Steward shows, square, 32–512 px.

``index.json`` has the shape of the Registry's ``GET /v0.1/servers`` response, so
Flow Steward reads this catalog and a private registry the same way. The
reviewed data sits under ``server._meta["io.github.flow-steward/catalog"]``, the
place the Registry reserves for a downstream catalog's own metadata.

    python scripts/build_index.py                          # write index.json
    python scripts/build_index.py --check --base origin/main
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

# FS_CATALOG_ROOT lets a pull request be checked by the base branch's copy of this
# script, so a submission cannot rewrite its own check.
ROOT = Path(os.environ.get("FS_CATALOG_ROOT") or Path(__file__).resolve().parent.parent).resolve()
SERVERS_DIR = "servers"
CATEGORIES_FILE = "categories.json"
INDEX_FILE = "index.json"
CATALOG_FILE = "catalog.yaml"
REGISTRY_FILE = "registry.json"
SERVER_FILE = "server.json"
ICON_FILE = "icon.png"

SCHEMA_VERSION = "mcp_server_catalog/v1"
CATALOG_META_KEY = "io.github.flow-steward/catalog"
OFFICIAL_META_KEY = "io.modelcontextprotocol.registry/official"
SERVER_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"

# Flow Steward's static catalog ceilings (core/infrastructure/static_catalog/settings.py).
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 5000
MAX_ICON_BYTES = 100 * 1024
MIN_ICON_PX, MAX_ICON_PX = 32, 512
MAX_SUMMARY_CHARS = 160
MIN_DESCRIPTION_CHARS = 40

AUTH_MODES = {"none", "headers", "oauth2"}
CLIENT_REGISTRATIONS = {"dcr", "client_metadata", "user_supplied"}
# Flow Steward refuses these on an OAuth connector (mcp_connector_services._validate_oauth_headers).
CREDENTIAL_HEADERS = {"authorization", "proxy-authorization", "x-api-key", "api-key", "apikey"}
# Never pre-filled in an authorization request; they belong to the client or the flow.
RESERVED_AUTHORIZATION_PARAMETERS = {
    "client_id",
    "client_secret",
    "code_challenge",
    "code_challenge_method",
    "redirect_uri",
    "response_type",
    "scope",
    "state",
}

_ITEM_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_REMOTE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
_SERVER_NAME = re.compile(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$")
_HOST_PATTERN = re.compile(r"^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+$")
_URL_VARIABLE = re.compile(r"\{([A-Za-z0-9_]+)\}")
# "mcp", "notion_mcp", "com.notion/mcp" are identifiers; "monday.com" and "Notion" are names.
_IDENTIFIER_LIKE = re.compile(r"^[a-z0-9-]+$|_|/")

_CATALOG_KEYS = {
    "revision",
    "source",
    "title",
    "summary",
    "description",
    "category",
    "website_url",
    "help_url",
    "preview_note",
    "official",
    "remotes",
}
_REMOTE_KEYS = {"id", "label", "url", "allowed_hosts", "user_defined_host", "auth"}
_AUTH_KEYS = {
    "mode",
    "client_registration",
    "requested_scopes",
    "authorization_parameters",
    "refresh_token_required",
    "setup_url",
}


class CatalogError(Exception):
    """Something cannot be published; the message lists every problem found."""


@dataclass(frozen=True)
class Item:
    item_id: str
    revision: int
    record: dict[str, Any]  # one entry of index.json "servers"


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commits(path: str) -> tuple[str, str, str]:
    """(last sha, first commit time, last commit time) for *path*; HEAD/now when uncommitted."""
    try:
        out = _git("log", "--format=%H %cI", "--", path)
    except subprocess.CalledProcessError:  # a repository without commits yet
        out = ""
    if not out:
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        try:
            head = _git("rev-parse", "HEAD")
        except subprocess.CalledProcessError:
            head = "main"
        return head, now, now
    lines = out.splitlines()
    last_sha, last_when = lines[0].split(" ", 1)
    _first_sha, first_when = lines[-1].split(" ", 1)
    return last_sha, first_when, last_when


def _repository() -> str:
    """owner/name for raw URLs: GITHUB_REPOSITORY in Actions, else the origin remote."""
    env = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if env:
        return env
    url = _git("remote", "get-url", "origin")
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not match:
        raise CatalogError(f"cannot derive owner/name from remote '{url}'; set GITHUB_REPOSITORY")
    return match.group(1)


def _iso_utc(when: str) -> str:
    return (
        datetime.fromisoformat(when).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    return str(value or "").strip()


def _summary(description: str) -> str:
    first = re.split(r"(?<=[.!?])\s+|\n\s*\n", description.strip(), maxsplit=1)[0].strip()
    if len(first) <= MAX_SUMMARY_CHARS:
        return first
    return first[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"


def _https_url(value: Any) -> bool:
    parts = urlsplit(_text(value))
    return parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password


def host_allowed(host: str, patterns: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in patterns:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and host != pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height of a PNG, read from its IHDR chunk."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG file")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def load_category_labels() -> dict[str, str]:
    """categories.json: category id -> the name to show. The extension catalog's file."""
    raw = json.loads((ROOT / CATEGORIES_FILE).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value.strip() for key, value in raw.items()
    ):
        raise CatalogError("categories.json must map each category id to the name to show")
    return {key: value.strip() for key, value in raw.items()}


def entry_dirs() -> list[str]:
    base = ROOT / SERVERS_DIR
    if not base.is_dir():
        return []
    return sorted(path.name for path in base.iterdir() if path.is_dir())


# ---------------------------------------------------------------------------
# one entry
# ---------------------------------------------------------------------------


def _load_upstream(folder: Path, item_id: str, catalog: dict[str, Any], problems: list[str]):
    """Return (server, official_meta, source) from registry.json or server.json."""
    has_registry = (folder / REGISTRY_FILE).is_file()
    has_server = (folder / SERVER_FILE).is_file()
    if has_registry == has_server:
        problems.append(f"{item_id}: needs exactly one of {REGISTRY_FILE} or {SERVER_FILE}")
        return None, None, None
    source = catalog.get("source") if isinstance(catalog.get("source"), dict) else {}
    registry_name = _text(source.get("registry"))
    if has_registry:
        try:
            record = json.loads((folder / REGISTRY_FILE).read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{item_id}: {REGISTRY_FILE} is not JSON ({exc})")
            return None, None, None
        server = record.get("server") if isinstance(record, dict) else None
        meta = record.get("_meta") if isinstance(record, dict) else None
        official = (meta or {}).get(OFFICIAL_META_KEY) if isinstance(meta, dict) else None
        if not isinstance(server, dict) or not isinstance(official, dict):
            problems.append(f"{item_id}: {REGISTRY_FILE} must be a Registry response with server and _meta")
            return None, None, None
        if not registry_name:
            problems.append(f"{item_id}: source.registry must name the Registry server of {REGISTRY_FILE}")
        elif _text(server.get("name")) != registry_name:
            problems.append(
                f"{item_id}: {REGISTRY_FILE} is {server.get('name')!r}, source.registry says {registry_name!r}"
            )
        return server, official, {"registry": _text(server.get("name")), "version": _text(server.get("version"))}
    if registry_name:
        problems.append(f"{item_id}: source.registry is set, so the record must be {REGISTRY_FILE}")
    try:
        server = json.loads((folder / SERVER_FILE).read_text(encoding="utf-8"))
    except ValueError as exc:
        problems.append(f"{item_id}: {SERVER_FILE} is not JSON ({exc})")
        return None, None, None
    if not isinstance(server, dict):
        problems.append(f"{item_id}: {SERVER_FILE} must be an object")
        return None, None, None
    if _text(server.get("$schema")) != SERVER_SCHEMA:
        problems.append(f"{item_id}: {SERVER_FILE} must declare $schema {SERVER_SCHEMA}")
    if not _SERVER_NAME.match(_text(server.get("name"))):
        problems.append(f"{item_id}: {SERVER_FILE} name must look like io.github.owner/server")
    if not _text(server.get("version")):
        problems.append(f"{item_id}: {SERVER_FILE} needs a version")
    for remote in server.get("remotes") or []:
        for header in (remote or {}).get("headers") or []:
            value = _text((header or {}).get("value"))
            if (header or {}).get("isSecret") and value and "{" not in value:
                problems.append(f"{item_id}: {SERVER_FILE} header {header.get('name')!r} carries a secret value")
    return server, None, {"catalog": _text(server.get("name")), "version": _text(server.get("version"))}


def _check_remote(
    item_id: str,
    remote: Any,
    upstream: dict[str, dict[str, Any]],
    problems: list[str],
) -> dict[str, Any] | None:
    where = f"{item_id}: remote {remote.get('id') if isinstance(remote, dict) else remote!r}"
    if not isinstance(remote, dict):
        problems.append(f"{where}: must be a mapping")
        return None
    unknown = set(remote) - _REMOTE_KEYS
    if unknown:
        problems.append(f"{where}: unknown keys {sorted(unknown)}")
    remote_id = _text(remote.get("id"))
    if not _REMOTE_ID.match(remote_id):
        problems.append(f"{where}: id must be lowercase letters, digits and dashes")
    url = _text(remote.get("url"))
    upstream_remote = upstream.get(url)
    if upstream_remote is None:
        problems.append(f"{where}: {url!r} is not a streamable-http remote of the server record")
        return None
    concrete = _URL_VARIABLE.sub("x", url)
    if not _https_url(concrete):
        problems.append(f"{where}: url must be https without credentials")
    if urlsplit(concrete).query:
        problems.append(f"{where}: url must not carry a query string")

    variables = upstream_remote.get("variables") if isinstance(upstream_remote.get("variables"), dict) else {}
    used = _URL_VARIABLE.findall(url)
    for name in used:
        if name not in variables:
            problems.append(f"{where}: url variable {{{name}}} is not declared by the server record")
    host_template = urlsplit(_URL_VARIABLE.sub(lambda m: f"__{m.group(1)}__", url)).hostname or ""
    host_variables = [name for name in used if f"__{name.lower()}__" in host_template]
    user_defined_host = remote.get("user_defined_host", False)
    if not isinstance(user_defined_host, bool):
        problems.append(f"{where}: user_defined_host must be true or false")
        user_defined_host = False
    allowed_hosts = remote.get("allowed_hosts")
    if allowed_hosts is None:
        allowed_hosts = []
    if not isinstance(allowed_hosts, list) or not all(
        isinstance(h, str) and _HOST_PATTERN.match(h) for h in allowed_hosts
    ):
        problems.append(f"{where}: allowed_hosts must list host names, optionally starting with '*.'")
        allowed_hosts = []
    if user_defined_host:
        if not host_variables:
            problems.append(f"{where}: user_defined_host needs a host the user fills in ({{variable}})")
    else:
        if not allowed_hosts:
            problems.append(f"{where}: allowed_hosts is required unless the user defines the host")
        hosts = [host_template]
        for name in host_variables:
            choices = (variables.get(name) or {}).get("choices")
            if not choices:
                problems.append(
                    f"{where}: host variable {{{name}}} has no choices; set user_defined_host: true"
                )
                hosts = []
                break
            hosts = [h.replace(f"__{name.lower()}__", str(c).lower()) for h in hosts for c in choices]
        for host in hosts:
            if not host_allowed(host, allowed_hosts):
                problems.append(f"{where}: host {host} is outside allowed_hosts")

    auth = remote.get("auth")
    if not isinstance(auth, dict):
        problems.append(f"{where}: auth is required")
        return None
    unknown = set(auth) - _AUTH_KEYS
    if unknown:
        problems.append(f"{where}: unknown auth keys {sorted(unknown)}")
    mode = _text(auth.get("mode"))
    headers = [h for h in upstream_remote.get("headers") or [] if isinstance(h, dict)]
    required_secret = [h for h in headers if h.get("isRequired") and h.get("isSecret")]
    if mode not in AUTH_MODES:
        problems.append(f"{where}: auth.mode must be one of {sorted(AUTH_MODES)} (a reviewer sets it)")
    elif mode == "headers" and not headers:
        problems.append(f"{where}: auth.mode headers, but the server record declares no headers")
    elif mode == "none" and required_secret:
        problems.append(f"{where}: auth.mode none, but the server record requires secret headers")
    out_auth: dict[str, Any] = {"mode": mode}
    if mode == "oauth2":
        required_credentials = {
            _text(h.get("name")).lower() for h in headers if h.get("isRequired")
        } & CREDENTIAL_HEADERS
        if required_credentials:
            problems.append(f"{where}: an OAuth endpoint must not require credential headers")
        registration = _text(auth.get("client_registration"))
        if registration not in CLIENT_REGISTRATIONS:
            problems.append(f"{where}: auth.client_registration must be one of {sorted(CLIENT_REGISTRATIONS)}")
        scopes = auth.get("requested_scopes", [])
        if not isinstance(scopes, list) or not all(isinstance(s, str) and s.strip() for s in scopes):
            problems.append(f"{where}: auth.requested_scopes must be a list of scopes")
            scopes = []
        parameters = auth.get("authorization_parameters", {})
        if not isinstance(parameters, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parameters.items()
        ):
            problems.append(f"{where}: auth.authorization_parameters must map names to strings")
            parameters = {}
        reserved = sorted(set(map(str.lower, parameters)) & RESERVED_AUTHORIZATION_PARAMETERS)
        if reserved:
            problems.append(f"{where}: auth.authorization_parameters must not set {reserved}")
        refresh = auth.get("refresh_token_required", False)
        if not isinstance(refresh, bool):
            problems.append(f"{where}: auth.refresh_token_required must be true or false")
        out_auth.update(
            {
                "client_registration": registration,
                "requested_scopes": [s.strip() for s in scopes],
                "authorization_parameters": dict(parameters),
                "refresh_token_required": bool(refresh),
            }
        )
    elif any(key in auth for key in _AUTH_KEYS - {"mode", "setup_url"}):
        problems.append(f"{where}: OAuth settings are only allowed with auth.mode oauth2")
    setup_url = _text(auth.get("setup_url"))
    if setup_url:
        if not _https_url(setup_url):
            problems.append(f"{where}: auth.setup_url must be https")
        out_auth["setup_url"] = setup_url
    return {
        "remote_id": remote_id,
        "label": _text(remote.get("label")) or remote_id,
        "url": url,
        "allowed_hosts": list(allowed_hosts),
        "user_defined_host": user_defined_host,
        "auth": out_auth,
    }


def derive_item(
    item_id: str,
    *,
    categories: dict[str, str],
    repository: str,
) -> Item:
    problems: list[str] = []
    folder = ROOT / SERVERS_DIR / item_id
    if not _ITEM_ID.match(item_id):
        problems.append(f"{item_id}: the folder name must be lowercase letters, digits and dashes")
    try:
        catalog = yaml.safe_load((folder / CATALOG_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CatalogError(f"{item_id}: {CATALOG_FILE} is missing") from None
    except yaml.YAMLError as exc:
        raise CatalogError(f"{item_id}: {CATALOG_FILE} is not YAML ({exc})") from None
    if not isinstance(catalog, dict):
        raise CatalogError(f"{item_id}: {CATALOG_FILE} must be a mapping")
    unknown = set(catalog) - _CATALOG_KEYS
    if unknown:
        problems.append(f"{item_id}: unknown keys {sorted(unknown)}")

    revision = catalog.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        problems.append(f"{item_id}: revision must be a whole number from 1")
        revision = 0
    title = _text(catalog.get("title"))
    if not title or _IDENTIFIER_LIKE.search(title):
        problems.append(f"{item_id}: title must be the product's name, not an identifier")
    description = _text(catalog.get("description"))
    if len(description) < MIN_DESCRIPTION_CHARS:
        problems.append(f"{item_id}: description needs at least {MIN_DESCRIPTION_CHARS} characters")
    summary = _text(catalog.get("summary")) or _summary(description)
    if len(summary) > MAX_SUMMARY_CHARS:
        problems.append(f"{item_id}: summary is longer than {MAX_SUMMARY_CHARS} characters")
    category = _text(catalog.get("category"))
    if category not in categories:
        problems.append(f"{item_id}: category {category!r} is not in {CATEGORIES_FILE}")
    for key in ("website_url", "help_url"):
        if catalog.get(key) and not _https_url(catalog.get(key)):
            problems.append(f"{item_id}: {key} must be an https URL")

    official_out: dict[str, Any] | None = None
    official = catalog.get("official")
    if official is not None:
        if not isinstance(official, dict) or set(official) - {"vendor", "evidence_url"}:
            problems.append(f"{item_id}: official takes vendor and evidence_url")
        elif not _text(official.get("vendor")) or not _https_url(official.get("evidence_url")):
            problems.append(
                f"{item_id}: official needs the vendor and an https evidence_url — the vendor's own "
                "page naming this endpoint"
            )
        else:
            official_out = {"vendor": _text(official["vendor"]), "evidence_url": _text(official["evidence_url"])}

    server, official_meta, source = _load_upstream(folder, item_id, catalog, problems)
    status = "active"
    if official_meta is not None:
        status = _text(official_meta.get("status")) or "active"
        if status == "deleted":
            problems.append(
                f"{item_id}: the Registry deleted {source['registry']} (spam, malware or illegal "
                "content); remove the entry"
            )

    remotes_out: list[dict[str, Any]] = []
    if server is not None:
        upstream = {
            _text(r.get("url")): r
            for r in server.get("remotes") or []
            if isinstance(r, dict) and r.get("type") == "streamable-http"
        }
        remotes = catalog.get("remotes")
        if not isinstance(remotes, list) or not remotes:
            problems.append(f"{item_id}: remotes must list at least one endpoint")
            remotes = []
        seen: set[str] = set()
        for remote in remotes:
            checked = _check_remote(item_id, remote, upstream, problems)
            if checked is None:
                continue
            if checked["remote_id"] in seen:
                problems.append(f"{item_id}: remote id {checked['remote_id']!r} is used twice")
            seen.add(checked["remote_id"])
            remotes_out.append(checked)

    icon_path = folder / ICON_FILE
    icon_out: dict[str, Any] | None = None
    if not icon_path.is_file():
        problems.append(f"{item_id}: {ICON_FILE} is missing")
    else:
        data = icon_path.read_bytes()
        try:
            width, height = png_size(data)
        except ValueError:
            problems.append(f"{item_id}: {ICON_FILE} is not a PNG")
        else:
            if len(data) > MAX_ICON_BYTES:
                problems.append(f"{item_id}: {ICON_FILE} is larger than {MAX_ICON_BYTES} bytes")
            if width != height or not MIN_ICON_PX <= width <= MAX_ICON_PX:
                problems.append(
                    f"{item_id}: {ICON_FILE} must be square, {MIN_ICON_PX}–{MAX_ICON_PX} px (is {width}x{height})"
                )
            icon_sha, _first, _last = _commits(f"{SERVERS_DIR}/{item_id}/{ICON_FILE}")
            icon_out = {
                "url": f"https://raw.githubusercontent.com/{repository}/{icon_sha}/{SERVERS_DIR}/{item_id}/{ICON_FILE}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "mime_type": "image/png",
                "size": f"{width}x{height}",
            }

    if problems:
        raise CatalogError("\n".join(problems))
    assert server is not None and source is not None and icon_out is not None

    _sha, added, updated = _commits(f"{SERVERS_DIR}/{item_id}")
    overlay = {
        "id": item_id,
        "revision": revision,
        "status": status,
        "source": source,
        "title": title,
        "summary": summary,
        "description": description,
        "category": category,
        "website_url": _text(catalog.get("website_url")) or _text(server.get("websiteUrl")),
        "help_url": _text(catalog.get("help_url")),
        "preview_note": _text(catalog.get("preview_note")),
        "official": official_out,
        "icon": icon_out,
        "remotes": remotes_out,
        "entry_url": f"https://github.com/{repository}/tree/main/{SERVERS_DIR}/{item_id}",
        "added_at": _iso_utc(added),
        "updated_at": _iso_utc(updated),
    }
    published = copy.deepcopy(server)
    meta = published.get("_meta") if isinstance(published.get("_meta"), dict) else {}
    meta = {key: value for key, value in meta.items() if key != CATALOG_META_KEY}
    meta[CATALOG_META_KEY] = overlay
    published["_meta"] = meta
    record: dict[str, Any] = {"server": published}
    if official_meta is not None:
        record["_meta"] = {OFFICIAL_META_KEY: official_meta}
    return Item(item_id=item_id, revision=revision, record=record)


# ---------------------------------------------------------------------------
# the index
# ---------------------------------------------------------------------------


def build_index(*, repository: str | None = None) -> dict[str, Any]:
    categories = load_category_labels()
    repo = repository or _repository()
    problems: list[str] = []
    items: list[Item] = []
    for item_id in entry_dirs():
        try:
            items.append(derive_item(item_id, categories=categories, repository=repo))
        except CatalogError as exc:
            problems.append(str(exc))
    if problems:
        raise CatalogError("\n".join(problems))
    if len(items) > MAX_ITEMS:
        raise CatalogError(f"the catalog would carry {len(items)} servers; the ceiling is {MAX_ITEMS}")
    items.sort(key=lambda item: (item.record["server"]["_meta"][CATALOG_META_KEY]["title"].lower(), item.item_id))
    servers = [item.record for item in items]
    digest = hashlib.sha256(json.dumps(servers, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
    newest = max(
        (item.record["server"]["_meta"][CATALOG_META_KEY]["updated_at"] for item in items),
        default=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_revision": f"{newest[:10]}.{digest}",
        "published_at": newest,
        "category_labels": categories,
        "servers": servers,
        "metadata": {"count": len(servers)},
    }


def render(index: dict[str, Any]) -> str:
    text = json.dumps(index, indent=2, ensure_ascii=False) + "\n"
    if len(text.encode("utf-8")) > MAX_INDEX_BYTES:
        raise CatalogError(f"index.json would exceed {MAX_INDEX_BYTES} bytes")
    return text


def _revision_at(ref: str, item_id: str) -> int | None:
    try:
        raw = _git("show", f"{ref}:{SERVERS_DIR}/{item_id}/{CATALOG_FILE}")
    except subprocess.CalledProcessError:
        return None
    try:
        value = (yaml.safe_load(raw) or {}).get("revision")
    except yaml.YAMLError:
        return None
    return value if isinstance(value, int) else None


def revision_problems(base: str) -> list[str]:
    """Every changed entry must raise its revision, so Flow Steward sees the change."""
    out = _git("diff", "--name-only", f"{base}...HEAD", "--", SERVERS_DIR)
    changed = sorted({Path(line).parts[1] for line in out.splitlines() if len(Path(line).parts) > 2})
    problems = []
    for item_id in changed:
        before = _revision_at(base, item_id)
        after = _revision_at("HEAD", item_id)
        if before is None or after is None:
            continue  # added or removed
        if after <= before:
            problems.append(f"{item_id}: changed, so raise revision above {before}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="validate without writing")
    parser.add_argument("--base", help="git ref a pull request targets; changed entries must raise revision")
    parser.add_argument("--repository", help="owner/name used in raw URLs")
    args = parser.parse_args(argv)
    try:
        if args.base:
            problems = revision_problems(args.base)
            if problems:
                raise CatalogError("\n".join(problems))
        text = render(build_index(repository=args.repository))
    except CatalogError as exc:
        print(f"Catalog check failed:\n{exc}", file=sys.stderr)
        return 1
    count = json.loads(text)["metadata"]["count"]
    if args.check:
        print(f"Catalog OK: {count} servers.")
        return 0
    (ROOT / INDEX_FILE).write_text(text, encoding="utf-8")
    print(f"Wrote {INDEX_FILE} with {count} servers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
