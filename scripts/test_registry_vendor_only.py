"""Automatic proposals require vendor evidence and an uncovered service."""

import json

import registry_sync as rs
from test_build_index import root, write_entry  # noqa: F401
from test_registry_discovery import vendor
from test_registry_priority import market, official, service


def test_unverified_domain_aligned_and_third_party_servers_do_not_fill_queue(root):  # noqa: F811
    from registry_discovery import discover

    market(root, [service("fortnox")])
    records = [
        vendor(),
        vendor("io.usefulapi/fortnox", "https://fortnox.usefulapi.io/mcp", "Fortnox"),
        vendor("com.fortnox/mcp", "https://fortnox.com/mcp", "Fortnox"),
    ]
    result = discover(records)
    assert result["proposed"] == []
    assert not list((root / "discovery/candidates").iterdir())
    assert result["excluded_counts"]["vendor_endpoint_not_reviewed"] == 3


def test_only_verified_vendor_is_proposed_without_backfilling_to_limit(root):  # noqa: F811
    from registry_discovery import discover, report

    url = "https://mcp.sumup.com/mcp"
    market(root, [service("sumup")], {"sumup": {"official_endpoints": [official(url)]}})
    records = [
        vendor(),
        vendor("io.usefulapi/sumup", "https://sumup.usefulapi.io/mcp", "SumUp"),
        vendor("io.github.sumup/mcp", url, "SumUp"),
    ]
    result = discover(records, limit=25)
    assert result["proposed"] == ["io.github.sumup/mcp"]
    assert "Only reviewed vendor endpoints" in "\n".join(report(result))
    assert result["excluded_counts"]["vendor_endpoint_not_reviewed"] == 2


def test_known_aggregator_cannot_inherit_status_from_vendor_url(root):  # noqa: F811
    from registry_discovery import discover

    url = "https://mcp.sumup.com/mcp"
    market(root, [service("sumup")], {"sumup": {"official_endpoints": [official(url)]}})
    policy = root / "coverage/discovery-policy.json"
    body = json.loads(policy.read_text())
    body["excluded_publishers"] = {"io.usefulapi": "Third-party aggregator rejected by catalog owner"}
    policy.write_text(json.dumps(body))
    result = discover([vendor("io.usefulapi/sumup", url, "SumUp")])
    assert not result["proposed"]
    assert result["excluded_counts"]["excluded_publisher"] == 1


def test_extension_covered_shopify_is_excluded_even_with_vendor_evidence(root):  # noqa: F811
    from registry_discovery import discover

    url = "https://shopify.test/mcp"
    market(
        root,
        [service("shopify", extension_catalog_ids=["flowsteward.shopify"])],
        {"shopify": {"official_endpoints": [official(url)]}},
    )
    result = discover([vendor("com.shopify/new", url, "Shopify")])
    assert not result["proposed"]
    assert result["excluded_counts"]["existing_extension"] == 1


def test_existing_catalog_product_does_not_get_an_alternative_proposal(root):  # noqa: F811
    from registry_discovery import discover

    write_entry(root)
    url = "https://example.com/new-mcp"
    market(root, [service("example")], {"example": {"official_endpoints": [official(url)]}})
    result = discover([vendor("com.example/new", url, "Example")])
    assert not result["proposed"]
    assert result["excluded_counts"]["existing_catalog"] == 1


def test_vendor_outside_market_list_can_be_proposed_with_reviewed_evidence(root):  # noqa: F811
    from registry_discovery import discover

    market(root, [])
    policy = root / "coverage/discovery-policy.json"
    policy.write_text(
        json.dumps(
            {
                "services": {},
                "reviewed_vendor_endpoints": [{**official("https://mcp.newco.com/mcp"), "vendor": "NewCo"}],
            }
        )
    )
    result = discover([vendor()])
    assert result["proposed"] == ["com.newco/mcp"]
    assert result["priorities"]["com.newco/mcp"]["vendor_verified"] is True


def test_old_unverified_proposal_is_removed_and_does_not_return_as_missing_source(root):  # noqa: F811
    from registry_discovery import discover, stage_candidates

    stage_candidates([vendor()])
    result = discover([])
    assert result["proposed"] == []
    assert not list((root / "discovery/candidates").iterdir())
    assert result["excluded_counts"]["vendor_endpoint_not_reviewed"] == 1
    assert discover([])["proposed"] == []


def test_normal_sync_uses_vendor_only_selection(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rs, "registry_all", lambda: [vendor()])
    report_path = root / "report.md"
    assert rs.main(["sync", "--discover", "--report", str(report_path)]) == 0
    assert not list((root / "discovery/candidates").iterdir())
    assert "Only reviewed vendor endpoints" in report_path.read_text()


def test_mixed_reviewed_endpoints_for_different_vendors_are_not_one_official_server(root):  # noqa: F811
    from registry_discovery import discover

    one, two = "https://one.test/mcp", "https://two.test/mcp"
    market(
        root,
        [service("one"), service("two")],
        {"one": {"official_endpoints": [official(one)]}, "two": {"official_endpoints": [official(two)]}},
    )
    record = vendor("com.wrapper/mcp", one, "One")
    record["server"]["remotes"].append({"type": "streamable-http", "url": two})
    assert not discover([record])["proposed"]


def test_general_vendor_evidence_does_not_bypass_existing_extension(root):  # noqa: F811
    from registry_discovery import discover

    url = "https://shopify.test/mcp"
    market(root, [service("shopify", extension_catalog_ids=["flowsteward.shopify"])])
    (root / "coverage/discovery-policy.json").write_text(
        json.dumps({"services": {}, "reviewed_vendor_endpoints": [{**official(url), "vendor": "Shopify"}]})
    )
    assert not discover([vendor("com.shopify/mcp", url, "Shopify")])["proposed"]


def test_general_evidence_matching_a_market_vendor_keeps_verified_priority(root):  # noqa: F811
    from registry_discovery import discover

    url = "https://mcp.sumup.com/mcp"
    market(root, [service("sumup")])
    (root / "coverage/discovery-policy.json").write_text(
        json.dumps({"services": {}, "reviewed_vendor_endpoints": [{**official(url), "vendor": "SumUp"}]})
    )
    result = discover([vendor("io.github.sumup/mcp", url, "SumUp")])
    assert result["priorities"]["io.github.sumup/mcp"]["tier"] == "target_vendor_verified"


def test_latest_verified_record_replaces_stale_unverified_draft(root):  # noqa: F811
    from registry_discovery import discover, stage_candidates

    name = "io.github.sumup/mcp"
    stage_candidates([vendor(name, "https://old.test/mcp", "SumUp")])
    url = "https://mcp.sumup.com/mcp"
    market(root, [service("sumup")], {"sumup": {"official_endpoints": [official(url)]}})
    result = discover([vendor(name, url, "SumUp")])
    assert result["proposed"] == [name]
    assert result["priorities"][name]["vendor_verified"] is True
