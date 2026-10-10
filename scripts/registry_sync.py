#!/usr/bin/env python3
"""Bring servers from the official MCP Registry into this catalog — for review.

Nothing here decides what Flow Steward lists. It writes files a reviewer then
checks in a pull request:

    python scripts/registry_sync.py candidates --search notion
        Registry servers that match, with the signals a reviewer weighs: who owns
        the namespace, whether the website and the endpoint sit on that domain.

    python scripts/registry_sync.py add com.notion/mcp --id notion --category productivity
        A new folder servers/notion/: the Registry record (registry.json), a draft
        catalog.yaml with what probing the endpoint suggested, and icon.png from
        the Registry or the vendor's website. auth.mode stays TODO until a
        reviewer confirms it, so the check refuses an unreviewed entry.

    python scripts/registry_sync.py sync [--report report.md]
        For every listed server: fetch its latest Registry record, store it, and
        raise the entry's revision when it changed (a new version, deprecated,
        deleted). Run on a schedule; the result is a pull request, never a merge.

The Registry asks catalogs to read it "on a regular but infrequent basis" and to
keep their own copy; it does not promise uptime or durable data.
"""

from __future__ import annotations

import argparse
import html.parser
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_index  # noqa: E402

REGISTRY = os.environ.get("MCP_REGISTRY_URL", "https://registry.modelcontextprotocol.io").rstrip("/")
USER_AGENT = "flow-steward-mcp-catalog (+https://github.com/Flow-Steward/mcps)"
TIMEOUT = 20
MAX_FETCH_BYTES = 2 * 1024 * 1024
ICON_PX = 128

# Aggregators republish other people's servers; their namespace says nothing about the vendor.
AGGREGATOR_NAMESPACES = {"ai.smithery", "com.mcparmory", "com.pulsemcp", "ai.waystation", "com.a2awire"}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def fetch(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """(status, headers, body) for an https request; HTTP errors are returned, not raised."""
    if not url.startswith("https://"):
        raise ValueError(f"{url}: only https is fetched")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - https only
            return response.status, dict(response.headers), response.read(MAX_FETCH_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read(MAX_FETCH_BYTES) if exc.fp else b""


def fetch_json(url: str) -> Any:
    status, _headers, data = fetch(url, headers={"Accept": "application/json"})
    if status != 200:
        raise LookupError(f"{url}: HTTP {status}")
    return json.loads(data)


def registry_latest(name: str) -> dict[str, Any] | None:
    """The latest Registry record of *name*, deleted ones included; None when unknown."""
    quoted = urllib.parse.quote(name, safe="")
    try:
        return fetch_json(f"{REGISTRY}/v0.1/servers/{quoted}/versions/latest?include_deleted=true")
    except LookupError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise


def registry_search(term: str, *, limit: int = 100) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"search": term, "version": "latest", "limit": limit})
    return list(fetch_json(f"{REGISTRY}/v0.1/servers?{query}").get("servers") or [])


# ---------------------------------------------------------------------------
# signals for a reviewer
# ---------------------------------------------------------------------------


def namespace(name: str) -> str:
    return name.split("/", 1)[0]


def namespace_domain(name: str) -> str | None:
    """notion.com for com.notion/mcp; None for io.github.* (a GitHub account, not a domain)."""
    ns = namespace(name)
    if ns.startswith("io.github."):
        return None
    return ".".join(reversed(ns.split(".")))


def _under(host: str, domain: str | None) -> bool:
    return bool(host and domain) and (host == domain or host.endswith("." + domain))


def signals(record: dict[str, Any]) -> dict[str, Any]:
    server = record.get("server") or {}
    official = (record.get("_meta") or {}).get(build_index.OFFICIAL_META_KEY) or {}
    name = str(server.get("name") or "")
    domain = namespace_domain(name)
    remotes = [r for r in server.get("remotes") or [] if r.get("type") == "streamable-http"]
    hosts = [urllib.parse.urlsplit(r.get("url", "")).hostname or "" for r in remotes]
    website_host = urllib.parse.urlsplit(str(server.get("websiteUrl") or "")).hostname or ""
    return {
        "name": name,
        "version": server.get("version"),
        "status": official.get("status"),
        "namespace": "github account" if domain is None else f"domain {domain}",
        "aggregator": namespace(name) in AGGREGATOR_NAMESPACES,
        "endpoints_on_namespace_domain": bool(hosts) and all(_under(h, domain) for h in hosts if "{" not in h),
        "website_on_namespace_domain": _under(website_host, domain),
        "streamable_http_endpoints": [r.get("url") for r in remotes],
        "declared_headers": sorted({h.get("name") for r in remotes for h in r.get("headers") or []}),
        "has_icon": bool(server.get("icons")),
    }


# ---------------------------------------------------------------------------
# probing an endpoint for a sign-in hint (a hint, never a decision)
# ---------------------------------------------------------------------------

_INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "flow-steward-catalog-probe", "version": "1"},
        },
    }
).encode()


def _resource_metadata_url(www_authenticate: str) -> str:
    match = re.search(r'resource_metadata="([^"]+)"', www_authenticate or "")
    return match.group(1) if match else ""


def probe_auth(url: str) -> dict[str, Any]:
    """What an unauthenticated initialize and OAuth discovery suggest about sign-in."""
    hint: dict[str, Any] = {"endpoint": url}
    try:
        status, headers, _body = fetch(
            url,
            method="POST",
            body=_INITIALIZE,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not fail
        hint["error"] = str(exc)
        return hint
    hint["initialize_status"] = status
    lowered = {k.lower(): v for k, v in headers.items()}
    challenge = lowered.get("www-authenticate", "")
    if challenge:
        hint["www_authenticate"] = challenge[:300]
    parts = urllib.parse.urlsplit(url)
    candidates = [_resource_metadata_url(challenge)] if _resource_metadata_url(challenge) else []
    candidates += [
        f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource{parts.path.rstrip('/')}",
        f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource",
    ]
    resource: dict[str, Any] = {}
    for candidate in candidates:
        try:
            resource = fetch_json(candidate)
            hint["protected_resource_metadata"] = candidate
            break
        except Exception:  # noqa: BLE001
            continue
    issuers = [str(i) for i in resource.get("authorization_servers") or []]
    if not issuers and status == 401:
        issuers = [f"{parts.scheme}://{parts.netloc}"]
    for issuer in issuers[:1]:
        issuer_parts = urllib.parse.urlsplit(issuer)
        base = f"{issuer_parts.scheme}://{issuer_parts.netloc}"
        path = issuer_parts.path.rstrip("/")
        for well_known in (
            f"{base}/.well-known/oauth-authorization-server{path}",
            f"{base}/.well-known/openid-configuration{path}",
            f"{issuer.rstrip('/')}/.well-known/openid-configuration",
        ):
            try:
                metadata = fetch_json(well_known)
            except Exception:  # noqa: BLE001
                continue
            hint["authorization_server"] = issuer
            hint["dynamic_client_registration"] = bool(metadata.get("registration_endpoint"))
            hint["client_id_metadata_document"] = bool(
                metadata.get("client_id_metadata_document_supported")
            )
            hint["scopes_supported"] = list(metadata.get("scopes_supported") or [])[:20]
            break
    if "authorization_server" in hint:
        if hint.get("dynamic_client_registration"):
            hint["suggested"] = {"mode": "oauth2", "client_registration": "dcr"}
        elif hint.get("client_id_metadata_document"):
            hint["suggested"] = {"mode": "oauth2", "client_registration": "client_metadata"}
        else:
            hint["suggested"] = {"mode": "oauth2", "client_registration": "user_supplied"}
    elif status in (401, 403):
        hint["suggested"] = {"mode": "headers"}
    elif 200 <= status < 300:
        hint["suggested"] = {"mode": "none"}
    return hint


# ---------------------------------------------------------------------------
# icons
# ---------------------------------------------------------------------------


class _IconLinks(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        values = {k.lower(): (v or "") for k, v in attrs}
        rel = values.get("rel", "").lower().split()
        href = values.get("href", "")
        if not href:
            return
        if "apple-touch-icon" in rel:
            self.links.append((0, href))
        elif "icon" in rel and not href.lower().endswith(".svg"):
            size = re.findall(r"(\d+)x\1", values.get("sizes", ""))
            self.links.append((1 if size and int(size[0]) >= 64 else 2, href))


def icon_websites(server: dict[str, Any], website: str = "") -> list[str]:
    """Where to look for a site icon: the given site, websiteUrl, then the namespace's domain."""
    sites = [website, str(server.get("websiteUrl") or "")]
    domain = namespace_domain(str(server.get("name") or ""))
    if domain:
        sites += [f"https://{domain}", f"https://www.{domain}"]
    return [s for s in dict.fromkeys(sites) if s.startswith("https://")]


def icon_candidates(server: dict[str, Any], website: str = "") -> list[str]:
    """Registry icons first (raster only), then each website's touch icon, link icons, favicon."""
    urls: list[str] = []
    for icon in server.get("icons") or []:
        src = str((icon or {}).get("src") or "")
        mime = str((icon or {}).get("mimeType") or "")
        if src.startswith("https://") and "svg" not in mime and not src.lower().endswith(".svg"):
            urls.append(src)
    for site in icon_websites(server, website):
        parts = urllib.parse.urlsplit(site)
        root = f"{parts.scheme}://{parts.netloc}"
        try:
            status, _headers, body = fetch(root + "/")
            if status == 200:
                parser = _IconLinks()
                parser.feed(body.decode("utf-8", "replace"))
                urls += [urllib.parse.urljoin(root + "/", href) for _rank, href in sorted(parser.links)]
        except Exception:  # noqa: BLE001
            pass
        urls += [f"{root}/apple-touch-icon.png", f"{root}/favicon.ico"]
    # Last resort: the avatar of the GitHub organization that publishes the server.
    repository = str((server.get("repository") or {}).get("url") or "")
    match = re.match(r"^https://github\.com/([A-Za-z0-9-]+)/", repository)
    if match:
        urls.append(f"https://github.com/{match.group(1)}.png?size={ICON_PX}")
    return [u for u in dict.fromkeys(urls) if u.startswith("https://")]


def normalize_icon(data: bytes) -> bytes:
    """A square RGBA PNG, ICON_PX wide (smaller sources keep their size, from 32 px)."""
    from PIL import Image  # Pillow, only needed when fetching icons

    with Image.open(io.BytesIO(data)) as image:
        if getattr(image, "format", "") == "ICO":
            image.size = max(image.info.get("sizes") or [image.size])
        frame = image.convert("RGBA")
    side = max(frame.size)
    if side < build_index.MIN_ICON_PX:
        raise ValueError(f"icon is {frame.size[0]}x{frame.size[1]}; at least {build_index.MIN_ICON_PX} px needed")
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(frame, ((side - frame.size[0]) // 2, (side - frame.size[1]) // 2))
    target = min(side, ICON_PX)
    if target != side:
        square = square.resize((target, target), Image.LANCZOS)
    out = io.BytesIO()
    square.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _source_side(data: bytes) -> int:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        sizes = image.info.get("sizes") if getattr(image, "format", "") == "ICO" else None
        return max(max(size) for size in sizes) if sizes else min(image.size)


def fetch_icon(server: dict[str, Any], website: str = "") -> tuple[bytes, str] | None:
    """The largest usable icon among the candidates; a Registry icon wins when big enough."""
    best: tuple[int, bytes, str] | None = None
    for url in icon_candidates(server, website)[:8]:
        try:
            status, _headers, data = fetch(url)
            if status != 200 or not data:
                continue
            side = _source_side(data)
            icon = normalize_icon(data)
        except Exception:  # noqa: BLE001 - try the next source
            continue
        if best is None or side > best[0]:
            best = (side, icon, url)
        if side >= ICON_PX:
            break
    return (best[1], best[2]) if best else None


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def _servers_dir() -> Path:
    return build_index.ROOT / build_index.SERVERS_DIR


def _dump_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def cmd_candidates(args: argparse.Namespace) -> int:
    for record in registry_search(args.search):
        print(json.dumps(signals(record), ensure_ascii=False))
    return 0


def draft_catalog(
    record: dict[str, Any],
    *,
    category: str,
    hints: dict[str, dict[str, Any]],
    website: str = "",
) -> str:
    server = record["server"]
    remotes = []
    for remote in server.get("remotes") or []:
        if remote.get("type") != "streamable-http":
            continue
        url = remote["url"]
        host = urllib.parse.urlsplit(re.sub(r"\{[^}]+\}", "x", url)).hostname or ""
        has_host_variable = bool(re.search(r"\{[^}]+\}", urllib.parse.urlsplit(url).netloc))
        remotes.append(
            {
                "id": "default" if not remotes else f"endpoint-{len(remotes) + 1}",
                "label": server.get("title") or server["name"],
                "url": url,
                "allowed_hosts": [] if has_host_variable else [host],
                "auth": {"mode": "TODO"},
            }
        )
    body = {
        "revision": 1,
        "source": {"registry": server["name"]},
        "title": server.get("title") or server["name"].split("/")[-1],
        "description": server.get("description") or "",
        "category": category,
        "website_url": website or server.get("websiteUrl") or "",
        "remotes": remotes,
    }
    lines = [
        "# Drafted by scripts/registry_sync.py add. A reviewer must:",
        "#   - rewrite title and description for people choosing a service (40+ characters);",
        "#   - set every remote's auth from the vendor's documentation (the probe below is a hint);",
        "#   - add official: {vendor, evidence_url} only with the vendor's own page naming the URL;",
        "#   - check icon.png.",
    ]
    for url, hint in hints.items():
        lines.append(f"# probe {url}: {json.dumps(hint.get('suggested') or hint, ensure_ascii=False)[:400]}")
    lines.append(f"# signals: {json.dumps(signals(record), ensure_ascii=False)}")
    return "\n".join(lines) + "\n" + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100)


def cmd_add(args: argparse.Namespace) -> int:
    item_id = args.id
    if not build_index._ITEM_ID.match(item_id):
        print(f"--id {item_id!r}: lowercase letters, digits and dashes", file=sys.stderr)
        return 2
    folder = _servers_dir() / item_id
    if folder.exists():
        print(f"{folder} already exists", file=sys.stderr)
        return 2
    record = registry_latest(args.name)
    if record is None:
        print(f"{args.name} is not in the Registry", file=sys.stderr)
        return 1
    status = ((record.get("_meta") or {}).get(build_index.OFFICIAL_META_KEY) or {}).get("status")
    if status != "active":
        print(f"{args.name} is {status} in the Registry; only active servers are added", file=sys.stderr)
        return 1
    hints = {
        r["url"]: probe_auth(r["url"])
        for r in record["server"].get("remotes") or []
        if r.get("type") == "streamable-http" and "{" not in r["url"]
    }
    folder.mkdir(parents=True)
    _dump_json(folder / build_index.REGISTRY_FILE, record)
    (folder / build_index.CATALOG_FILE).write_text(
        draft_catalog(record, category=args.category, hints=hints, website=args.website or ""),
        encoding="utf-8",
    )
    icon = fetch_icon(record["server"], args.website or "")
    if icon:
        (folder / build_index.ICON_FILE).write_bytes(icon[0])
        print(f"icon from {icon[1]}")
    else:
        print("no icon found: add icon.png by hand (square PNG, 32-512 px)")
    print(f"wrote {folder.relative_to(build_index.ROOT)}; review catalog.yaml before the pull request")
    return 0


def _bump_revision(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    current = (yaml.safe_load(text) or {}).get("revision", 0)
    new_text, count = re.subn(r"(?m)^revision:\s*\d+\s*$", f"revision: {int(current) + 1}", text, count=1)
    if not count:
        raise ValueError(f"{path}: no top-level revision line to raise")
    path.write_text(new_text, encoding="utf-8")


def sync_entry(folder: Path) -> str | None:
    """Refresh one registry-backed entry; return a report line when it changed."""
    registry_path = folder / build_index.REGISTRY_FILE
    if not registry_path.is_file():
        return None
    stored = json.loads(registry_path.read_text(encoding="utf-8"))
    name = stored["server"]["name"]
    latest = registry_latest(name)
    if latest is None:
        return f"- `{folder.name}`: {name} is no longer in the Registry; decide whether to remove the entry"
    before = stored.get("_meta", {}).get(build_index.OFFICIAL_META_KEY, {})
    after = latest.get("_meta", {}).get(build_index.OFFICIAL_META_KEY, {})
    if stored == latest:
        return None
    _dump_json(registry_path, latest)
    _bump_revision(folder / build_index.CATALOG_FILE)
    changes = []
    if stored["server"].get("version") != latest["server"].get("version"):
        changes.append(f"version {stored['server'].get('version')} → {latest['server'].get('version')}")
    if before.get("status") != after.get("status"):
        changes.append(f"status {before.get('status')} → {after.get('status')}")
    if after.get("status") == "deleted":
        changes.append("**the Registry deleted it: remove the entry**")
    return f"- `{folder.name}` ({name}): " + (", ".join(changes) or "record changed")


def cmd_sync(args: argparse.Namespace) -> int:
    lines = []
    failures = []
    for folder in sorted(p for p in _servers_dir().iterdir() if p.is_dir()) if _servers_dir().is_dir() else []:
        try:
            line = sync_entry(folder)
        except Exception as exc:  # noqa: BLE001 - one unreachable record must not stop the rest
            failures.append(f"- `{folder.name}`: {exc}")
            continue
        if line:
            lines.append(line)
    report = []
    if lines:
        report += ["Registry changes for listed servers:", "", *lines]
    if failures:
        report += ["", "Could not refresh:", "", *failures]
    text = "\n".join(report) + ("\n" if report else "")
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    print(text or "No Registry changes.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    candidates = sub.add_parser("candidates", help="Registry servers matching a name, with review signals")
    candidates.add_argument("--search", required=True)
    candidates.set_defaults(func=cmd_candidates)
    add = sub.add_parser("add", help="draft a new entry from a Registry server")
    add.add_argument("name", help="Registry server name, e.g. com.notion/mcp")
    add.add_argument("--id", required=True, help="catalog id and folder name, e.g. notion")
    add.add_argument("--category", required=True, help="an id from categories.json")
    add.add_argument("--website", help="the vendor's site, when the Registry record has none")
    add.set_defaults(func=cmd_add)
    sync = sub.add_parser("sync", help="refresh every listed server from the Registry")
    sync.add_argument("--report", help="write the pull request body here")
    sync.set_defaults(func=cmd_sync)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
