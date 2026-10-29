"""Rank Registry review candidates against the curated market scope, offline."""

from __future__ import annotations

import json

import build_index as bi

from registry_discovery import endpoint_key, title_key

TIERS = {"target_vendor_verified": 0, "target_vendor_candidate": 1, "target_unverified": 2, "other": 3}
COVERAGE = {
    "uncovered": 0,
    "indirect_reported": 1,
    "extension_reported": 2,
    "direct_catalog": 2,
    "unknown": 3,
}


class MarketPriorities:
    def __init__(self, known: list[dict]):
        source = bi.ROOT / "coverage/services.json"
        rows = json.loads(source.read_text(encoding="utf-8"))["services"] if source.exists() else []
        self.services = {}
        for row in rows:
            if not isinstance(row, dict) or not row.get("id") or not row.get("name"):
                raise ValueError("coverage services require id and name")
            if row["id"] in self.services:
                raise ValueError(f"duplicate coverage service: {row['id']}")
            self.services[row["id"]] = row
        path = bi.ROOT / "coverage/discovery-policy.json"
        config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        policy = config.get("services", {})
        self.excluded_publishers = config.get("excluded_publishers", {})
        if not isinstance(self.excluded_publishers, dict) or any(
            not isinstance(reason, str) or not reason.strip() for reason in self.excluded_publishers.values()
        ):
            raise ValueError("excluded publishers require nonempty review reasons")
        unknown = set(policy) - set(self.services)
        if unknown:
            raise ValueError("discovery policy references unknown service: " + ", ".join(sorted(unknown)))
        self.aliases = {}
        self.keys = {}
        self.endpoints = {}
        self.coverage = {}
        self.excluded = {}
        known_ids = {item["id"] for item in known}
        for item_id, row in self.services.items():
            settings = policy.get(item_id, {})
            excluded = settings.get("excluded_registry_names", {})
            if not isinstance(excluded, dict) or any(
                not isinstance(v, str) or not v.strip() for v in excluded.values()
            ):
                raise ValueError(f"{item_id}: excluded Registry names require review reasons")
            self.excluded[item_id] = excluded
            aliases = settings.get("aliases", [])
            if not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() for a in aliases):
                raise ValueError(f"{item_id}: aliases must be nonempty strings")
            keys = {title_key(s) for s in [item_id, row["name"], *aliases]}
            keys.discard("")
            self.keys[item_id] = keys
            for key in keys:
                self.aliases.setdefault(key, set()).add(item_id)
            for evidence in settings.get("official_endpoints", []):
                key = endpoint_key(evidence["url"])
                if not key or not endpoint_key(evidence["evidence_url"]):
                    raise ValueError(f"{item_id}: reviewed endpoints need literal HTTPS URL and evidence_url")
                existing = self.endpoints.get(key)
                if existing and existing["service_id"] != item_id:
                    raise ValueError(f"reviewed endpoint shared by different services: {key}")
                self.endpoints[key] = {**evidence, "service_id": item_id}
            direct = bool(set(row.get("catalog_after", [])) & known_ids) or any(
                item["id"] == item_id or item["titles"] & keys for item in known
            )
            self.coverage[item_id] = (
                "direct_catalog"
                if direct
                else "extension_reported"
                if row.get("extension_catalog_ids")
                else "indirect_reported"
                if row.get("coverage_bucket") in {"existing_indirect", "pending_indirect"}
                else "uncovered"
            )
        for evidence in config.get("reviewed_vendor_endpoints", []):
            key = endpoint_key(evidence["url"])
            if (
                not key
                or not endpoint_key(evidence["evidence_url"])
                or not isinstance(evidence.get("vendor"), str)
                or not evidence["vendor"].strip()
            ):
                raise ValueError("reviewed non-market endpoints need vendor, HTTPS URL and evidence_url")
            if key in self.endpoints:
                raise ValueError(f"duplicate reviewed endpoint policy: {key}")
            self.endpoints[key] = {**evidence, "service_id": None}

    def describe(self, record: dict) -> dict:
        from registry_sync import namespace_domain, signals

        server = record["server"]
        name = server["name"]
        remotes = [r.get("url", "") for r in server.get("remotes", []) if r.get("type") == "streamable-http"]
        endpoint_evidence = [self.endpoints.get(endpoint_key(url)) for url in remotes]
        ids = set()
        for value in (server.get("title") or "", name.split("/")[-1]):
            ids.update(self.aliases.get(title_key(value), set()))
        domain = namespace_domain(name)
        # Conservative namespace fallback only when the publisher supplies no
        # product title. An arbitrary GitHub account or nested subdomain isn't a brand.
        root_label = (domain or "").split(".")[-2:-1]
        if not ids and not server.get("title") and root_label:
            ids.update(self.aliases.get(title_key(root_label[0]), set()))
        ids = {item_id for item_id in ids if name not in self.excluded[item_id]}
        # A reviewed URL is stronger than a Registry author's display name.
        owners = {title_key(e["service_id"] or e["vendor"]) for e in endpoint_evidence if e}
        vendor_verified = bool(endpoint_evidence) and all(endpoint_evidence) and len(owners) == 1
        documented = {e["service_id"] for e in endpoint_evidence if e and e["service_id"]}
        if len(documented) == 1 and vendor_verified:
            ids = documented
        elif vendor_verified and not documented:
            # Match a general evidence entry by the reviewed vendor label, never
            # by an arbitrary Registry display title.
            ids = set().union(*(self.aliases.get(title_key(e["vendor"]), set()) for e in endpoint_evidence))
        description = {
            "tier": "other",
            "coverage": "unknown",
            "target_services": [],
            "vendor_verified": vendor_verified,
            "evidence_urls": sorted({e["evidence_url"] for e in endpoint_evidence if e})
            if vendor_verified
            else [],
            "access_notes": sorted(
                {e["access_note"] for e in endpoint_evidence if e and e.get("access_note")}
            )
            if vendor_verified
            else [],
            "ambiguous_services": [],
        }
        if len(ids) != 1:
            description["ambiguous_services"] = sorted(ids)
            return description
        item_id = next(iter(ids))
        row = self.services[item_id]
        verified = vendor_verified and (not documented or documented == {item_id})
        namespace_keys = {title_key(part) for part in root_label}
        aligned = (
            bool(namespace_keys & self.keys[item_id]) and signals(record)["endpoints_on_namespace_domain"]
        )
        description.update(
            {
                "tier": "target_vendor_verified"
                if verified
                else "target_vendor_candidate"
                if aligned
                else "target_unverified",
                "coverage": self.coverage[item_id],
                "target_services": [
                    {
                        "id": item_id,
                        "name": row["name"],
                        "regions": row.get("regions", []),
                        "priority": row.get("priority", "P3"),
                        "research_mcp_status": row.get("mcp_status", "not_reviewed"),
                        "research_notes": row.get("notes", ""),
                    }
                ],
                "evidence_urls": sorted({e["evidence_url"] for e in endpoint_evidence if e})
                if verified
                else [],
                "access_notes": sorted(
                    {e["access_note"] for e in endpoint_evidence if e and e.get("access_note")}
                )
                if verified
                else [],
            }
        )
        return description

    def rejection_reason(self, record: dict) -> str | None:
        from registry_sync import namespace

        if namespace(record["server"]["name"]) in self.excluded_publishers:
            return "excluded_publisher"
        if not any(r.get("type") == "streamable-http" for r in record["server"].get("remotes", []) or []):
            return "out_of_scope_transport"
        description = self.describe(record)
        if description["coverage"] == "extension_reported":
            return "existing_extension"
        if description["coverage"] == "direct_catalog":
            return "existing_catalog"
        if not description["vendor_verified"]:
            return "vendor_endpoint_not_reviewed"
        return None

    def sort_key(self, description: dict) -> tuple:
        priority = min((s["priority"] for s in description["target_services"]), default="P3")
        return TIERS[description["tier"]], COVERAGE[description["coverage"]], priority

    def unlisted_endpoints(self, records: list[dict], known: list[dict]) -> list[dict]:
        represented = {key for item in known for key in item["endpoints"]}
        represented.update(
            endpoint_key(r.get("url", ""))
            for record in records
            for r in record["server"].get("remotes", [])
            if r.get("type") == "streamable-http"
        )
        return [e for key, e in sorted(self.endpoints.items()) if key not in represented]
