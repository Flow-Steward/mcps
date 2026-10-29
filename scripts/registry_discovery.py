"""Deterministic, bounded review proposals; nothing here publishes a server."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import build_index as bi
import yaml


def endpoint_key(url: str) -> str | None:
    """Compare literal endpoints, preserving path case and query semantics."""
    if not url or "{" in url or "}" in url:
        return None
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
        ):
            return None
        host = parts.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        if parts.port not in (None, 443):
            host += f":{parts.port}"
        return urlunsplit(("https", host, parts.path.rstrip("/"), parts.query, ""))
    except ValueError:
        return None


def title_key(title: str) -> str:
    title = re.sub(
        r"(?:[\s_-]+mcp(?:[\s_-]+server)?|[\s_-]+server)$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    return "".join(c for c in title.casefold() if c.isalnum())


def identity(item_id: str, server: dict, catalog: dict | None = None) -> dict:
    catalog = catalog or {}
    urls = [r.get("url", "") for r in server.get("remotes", []) if r.get("type") == "streamable-http"]
    urls += [r.get("url", "") for r in catalog.get("remotes", [])]
    return {
        "id": item_id,
        "names": {n for n in (server.get("name"), (catalog.get("source") or {}).get("registry")) if n},
        "titles": {
            t
            for t in (
                title_key(server.get("title", "")),
                title_key(catalog.get("title", "")),
            )
            if t
        },
        "endpoints": {key for url in urls if (key := endpoint_key(url))},
    }


def catalog_identities() -> list[dict]:
    result = []
    for folder in sorted((bi.ROOT / bi.SERVERS_DIR).glob("*")):
        if not folder.is_dir():
            continue
        catalog = yaml.safe_load((folder / bi.CATALOG_FILE).read_text(encoding="utf-8"))
        registry = folder / bi.REGISTRY_FILE
        server = (
            json.loads(registry.read_text(encoding="utf-8"))["server"]
            if registry.exists()
            else json.loads((folder / bi.SERVER_FILE).read_text(encoding="utf-8"))
        )
        result.append(identity(folder.name, server, catalog))
    return result


def duplicate_matches(candidate: dict, known: list[dict]) -> list[dict]:
    matches = []
    for item in known:
        reasons = []
        if candidate["id"] == item["id"]:
            reasons.append("catalog_id")
        for field in ("names", "titles", "endpoints"):
            overlap = candidate[field] & item[field]
            if overlap:
                reasons.append(field + ": " + ", ".join(sorted(overlap)))
        if reasons:
            matches.append({"id": item["id"], "reasons": reasons})
    return matches


def proposed_id(server: dict) -> str:
    name = server["name"]
    label = server.get("title") or name.split("/", 1)[-1]
    if label.casefold() in {"mcp", "server", "mcp-server"}:
        label = name.split("/", 1)[0].split(".")[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")
    return slug[:80] or "candidate-" + hashlib.sha256(name.encode()).hexdigest()[:12]


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def discover(records: list[dict], *, limit: int = 25) -> dict:
    """Stage ranked review drafts after a complete scan; retain deferred human work."""
    from registry_priority import MarketPriorities
    from registry_sync import AGGREGATOR_NAMESPACES, draft_catalog, namespace, signals

    if limit < 1:
        raise ValueError("candidate limit must be positive")
    base = bi.ROOT / "discovery"
    queue = base / "candidates"
    ignored_path = base / "ignored.json"
    ignored = json.loads(ignored_path.read_text(encoding="utf-8")) if ignored_path.exists() else {}
    if not isinstance(ignored, dict) or any(
        not isinstance(v, str) or not v.strip() for v in ignored.values()
    ):
        raise ValueError("discovery/ignored.json must map Registry names to rejection reasons")
    known = catalog_identities()
    published_names = {name for item in known for name in item["names"]}
    market = MarketPriorities(known)
    unlisted = market.unlisted_endpoints(records, known)
    pending = {}
    # Read the complete queue before mutating it. A broken draft must not disappear silently.
    for folder in sorted(queue.glob("*")):
        if folder.is_dir():
            record = json.loads((folder / bi.REGISTRY_FILE).read_text(encoding="utf-8"))
            pending[record["server"]["name"]] = (folder, record)
    eligible = {}
    snapshot_names = {record["server"]["name"] for record in records}
    for record in records:
        server = record.get("server") or {}
        name = server.get("name", "")
        status = ((record.get("_meta") or {}).get(bi.OFFICIAL_META_KEY) or {}).get("status")
        remotes = server.get("remotes") or []
        if (
            not name
            or name in ignored
            or status != "active"
            or namespace(name) in AGGREGATOR_NAMESPACES
            or not any(
                r.get("type") == "streamable-http"
                and (
                    endpoint_key(r.get("url", ""))
                    or r.get("url", "").startswith("https://")
                    and "{" in r.get("url", "")
                )
                for r in remotes
            )
        ):
            continue
        if name in eligible and eligible[name] != record:
            raise ValueError(f"multiple latest records for {name}")
        eligible[name] = record

    unavailable = []
    for name, (_, record) in pending.items():
        if name not in snapshot_names and name not in ignored:
            # Absence is not a rejection: keep the reviewer's work and flag it.
            eligible[name] = record
            unavailable.append(name)

    priorities = {name: market.describe(record) for name, record in eligible.items()}
    result = {
        "proposed": [],
        "duplicates": [],
        "deferred": 0,
        "unavailable": sorted(unavailable),
        "priorities": {},
        "scope_count": len(market.services),
        "unlisted_vendor_endpoints": unlisted,
    }
    selected = []
    retained = []
    retired = set()
    held_duplicates = {}
    occupied = {folder.name: name for name, (folder, _) in pending.items()}
    # Market relevance beats a previously queued general service or alphabetical order.
    ordered = sorted(
        eligible,
        key=lambda name: (
            name in unavailable,
            *market.sort_key(priorities[name]),
            name not in pending,
            not signals(eligible[name])["endpoints_on_namespace_domain"],
            name,
        ),
    )
    for name in ordered:
        record = eligible[name]
        item_id = pending[name][0].name if name in pending else proposed_id(record["server"])
        if item_id in occupied and occupied[item_id] != name:
            # Candidate folders are staging identities, not publication decisions.
            # A previous wrapper must not reserve a vendor's name indefinitely.
            item_id += "-" + hashlib.sha256(name.encode()).hexdigest()[:12]
            if item_id in occupied and occupied[item_id] != name:
                raise ValueError(f"candidate directory collision: {item_id}")
        candidate = identity(item_id, record["server"])
        matches = duplicate_matches(candidate, known)
        if matches:
            result["duplicates"].append({"name": name, "matches": matches, "priority": priorities[name]})
            if name in pending and name not in published_names:
                retained.append((item_id, record))
                held_duplicates[name] = matches
            else:
                retired.add(name)
            continue
        if len(selected) >= limit:
            result["deferred"] += 1
            if name in pending:
                retained.append((item_id, record))
            continue
        known.append(candidate)
        occupied[item_id] = name
        selected.append((item_id, record))
        result["proposed"].append(name)
        result["priorities"][name] = priorities[name]

    queue.mkdir(parents=True, exist_ok=True)
    selected_names = set(result["proposed"])
    for name, (folder, _) in pending.items():
        if name in ignored or name in retired or name not in eligible:
            shutil.rmtree(folder)
    for item_id, record in selected + retained:
        name = record["server"]["name"]
        folder = pending[name][0] if name in pending else queue / item_id
        folder.mkdir(parents=True, exist_ok=True)
        _write_json(folder / bi.REGISTRY_FILE, record)
        # Preserve human edits to a pending draft across Registry version refreshes.
        draft = folder / bi.CATALOG_FILE
        if not draft.exists():
            draft.write_text(draft_catalog(record, category="TODO", hints={}), encoding="utf-8")
        _write_json(
            folder / "review.json",
            {
                "catalog_id": item_id,
                "signals": signals(record),
                "ownership": "unverified",
                "priority": priorities[name],
                "queue_state": "proposed" if name in selected_names else "deferred",
                "duplicate_matches": held_duplicates.get(name, []),
                "registry_presence": "missing_from_snapshot" if name in unavailable else "present",
                "required_review": [
                    "Vendor-owned evidence page naming the exact endpoint",
                    "Category and description",
                    "Authentication and access restrictions",
                    "Square PNG icon",
                    "Duplicate matches",
                ],
            },
        )
    _write_json(base / "duplicates.json", result["duplicates"])
    return result


def report(result: dict) -> list[str]:
    lines = [
        "",
        "New MCP candidates (ownership and authentication require review):",
        "",
    ]
    lines += [
        f"Market scope: {result['scope_count']} services; vendor evidence first, then coverage gaps.",
        "",
    ]
    for name in result["proposed"]:
        priority = result["priorities"][name]
        services = ", ".join(s["name"] for s in priority["target_services"]) or "outside market scope"
        lines.append(f"- `{name}` — {services}; {priority['tier']}; {priority['coverage']}")
        lines += [f"  - Vendor evidence: {url}" for url in priority["evidence_urls"]]
        lines += [f"  - Access review: {note}" for note in priority["access_notes"]]
    if not result["proposed"]:
        lines.append("- No pending candidates.")
    lines += [
        "",
        f"Deferred by queue limit: {result['deferred']}.",
        "",
        f"Duplicate matches: {len(result['duplicates'])}; full details in `discovery/duplicates.json`.",
    ]
    if result["unavailable"]:
        lines += ["", "Pending sources missing from this Registry snapshot (drafts retained):"]
        lines += [f"- `{name}`" for name in result["unavailable"]]
    if result["unlisted_vendor_endpoints"]:
        lines += [
            "",
            "Reviewed vendor endpoints absent from Registry (manual onboarding, not invented records):",
        ]
        lines += [
            f"- `{e['service_id']}`: {e['url']} — {e['evidence_url']}"
            for e in result["unlisted_vendor_endpoints"]
        ]
    if result["duplicates"]:
        lines += ["", "Possible duplicate records (not proposed):"]
    for duplicate in result["duplicates"][:30]:
        details = "; ".join(m["id"] + " (" + "; ".join(m["reasons"]) + ")" for m in duplicate["matches"])
        lines.append(f"- `{duplicate['name']}` → {details}")
    lines += [
        "",
        "Review drafts under `discovery/candidates/`. They are excluded from index.json.",
        "Promote reviewed entries to servers/; record rejected Registry names in discovery/ignored.json.",
    ]
    return lines
