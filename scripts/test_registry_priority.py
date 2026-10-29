"""Market scope and vendor provenance must beat Registry alphabetical order."""

import json

import pytest

from test_build_index import catalog, root, write_entry  # noqa: F401
from test_registry_discovery import vendor


def market(project_root, services, policy=None):
    coverage = project_root / "coverage"
    coverage.mkdir(exist_ok=True)
    (coverage / "services.json").write_text(json.dumps({"services": services}))
    if policy is not None:
        (coverage / "discovery-policy.json").write_text(json.dumps({"services": policy}))


def service(item_id, **kw):
    return {"id": item_id, "name": item_id.title(), "priority": "P2", **kw}


def official(url):
    return {"url": url, "evidence_url": "https://docs.vendor.test/mcp"}


def test_verified_vendor_endpoint_beats_alphabetical_and_same_name_wrapper(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    url = "https://mcp.sumup.com/mcp"
    market(root, [service("sumup")], {"sumup": {"official_endpoints": [official(url)]}})
    wrapper = vendor("com.aaa/sumup", "https://aaa.com/mcp", "SumUp MCP")
    genuine = vendor("io.github.sumup/mcp", url, "SumUp MCP Server")
    result = discover([vendor(), wrapper, genuine], limit=1)
    assert result["proposed"] == ["io.github.sumup/mcp"]
    priority = result["priorities"]["io.github.sumup/mcp"]
    assert priority["tier"] == "target_vendor_verified"
    assert priority["coverage"] == "uncovered"
    assert priority["evidence_urls"] == ["https://docs.vendor.test/mcp"]


def test_uncovered_service_precedes_indirect_and_published_extension(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    market(
        root,
        [
            service("alpha", extension_catalog_ids=["flowsteward.alpha"]),
            service("beta", coverage_bucket="existing_indirect"),
            service("zulu"),
        ],
    )
    records = [
        vendor("com.alpha/mcp", "https://alpha.com/mcp", "Alpha"),
        vendor("com.beta/mcp", "https://beta.com/mcp", "Beta"),
        vendor("com.zulu/mcp", "https://zulu.com/mcp", "Zulu"),
    ]
    result = discover(records, limit=3)
    assert result["proposed"] == ["com.zulu/mcp", "com.beta/mcp", "com.alpha/mcp"]
    assert result["priorities"]["com.beta/mcp"]["coverage"] == "indirect_reported"


def test_direct_catalog_coverage_uses_current_catalog_not_old_research(root):  # noqa: F811
    from registry_discovery import catalog_identities
    from registry_priority import MarketPriorities

    write_entry(root)
    market(root, [service("example", catalog_after=[]), service("missing", catalog_after=["old-id"])])
    priorities = MarketPriorities(catalog_identities())
    assert priorities.describe(vendor("com.example/extra", title="Example"))["coverage"] == "direct_catalog"
    assert priorities.describe(vendor("com.missing/mcp", title="Missing"))["coverage"] == "uncovered"


def test_reviewed_alias_is_exact_and_does_not_merge_product_editions(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    market(
        root,
        [service("lexware-office", name="Lexware Office"), service("sage-50-canada", name="Sage 50 Canada")],
        {"lexware-office": {"aliases": ["lexoffice"]}},
    )
    rows = [
        vendor("io.github.someone/lexoffice", "https://other.test/mcp", "lexoffice MCP"),
        vendor("com.sage/mcp", "https://sage.com/mcp", "Sage"),
        vendor("com.other/mcp", "https://other.test/different", "Lexoffice Migration Helper"),
    ]
    result = discover(rows, limit=3)
    assert result["priorities"][rows[0]["server"]["name"]]["target_services"][0]["id"] == "lexware-office"
    assert result["priorities"]["com.sage/mcp"]["tier"] == "other"
    assert result["priorities"]["com.other/mcp"]["tier"] == "other"


def test_spoofed_endpoint_and_mixed_vendor_remotes_are_not_verified(root):  # noqa: F811
    from registry_priority import MarketPriorities

    url = "https://mcp.sumup.com/mcp"
    market(root, [service("sumup")], {"sumup": {"official_endpoints": [official(url)]}})
    priorities = MarketPriorities([])
    spoof = vendor("com.fake/sumup", "https://mcp.sumup.com.evil.test/mcp", "SumUp")
    assert priorities.describe(spoof)["tier"] == "target_unverified"
    mixed = vendor("com.fake/sumup", url, "SumUp")
    mixed["server"]["remotes"].append({"type": "streamable-http", "url": "https://evil.test/mcp"})
    assert priorities.describe(mixed)["tier"] != "target_vendor_verified"


def test_new_target_displaces_general_proposal_without_erasing_its_draft(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    discover([vendor()], limit=1)
    draft = root / "discovery/candidates/newco/catalog.yaml"
    draft.write_text(draft.read_text() + "# pending human review\n")
    market(root, [service("sumup")])
    result = discover([vendor(), vendor("com.sumup/mcp", "https://sumup.com/mcp", "SumUp")], limit=1)
    assert result["proposed"] == ["com.sumup/mcp"]
    assert "pending human review" in draft.read_text()
    assert json.loads((draft.parent / "review.json").read_text())["queue_state"] == "deferred"


def test_missing_source_is_retained_but_does_not_block_available_target(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    discover([vendor()], limit=1)
    market(root, [service("sumup")])
    result = discover([vendor("com.sumup/mcp", "https://sumup.com/mcp", "SumUp")], limit=1)
    assert result["proposed"] == ["com.sumup/mcp"]
    assert result["unavailable"] == ["com.newco/mcp"]
    assert (root / "discovery/candidates/newco/catalog.yaml").exists()


def test_inconsistent_policy_fails_before_queue_mutation(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    discover([vendor()], limit=1)
    draft = root / "discovery/candidates/newco/catalog.yaml"
    before = draft.read_bytes()
    market(root, [service("sumup")], {"typo-service": {"aliases": ["sumup"]}})
    with pytest.raises(ValueError, match="unknown service"):
        discover([vendor()], limit=1)
    assert draft.read_bytes() == before


def test_priority_is_reported_and_vendor_endpoints_absent_from_registry_are_visible(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover, report

    market(
        root,
        [service("pipedrive"), service("sumup")],
        {"pipedrive": {"official_endpoints": [official("https://mcp.pipedrive.ai/mcp")]}},
    )
    result = discover([vendor("com.sumup/mcp", "https://sumup.com/mcp", "SumUp")], limit=1)
    text = "\n".join(report(result))
    assert "Sumup" in text and "uncovered" in text
    assert "pipedrive" in text.lower() and "absent from Registry" in text


def test_overlapping_aliases_do_not_choose_an_arbitrary_product(root):  # noqa: F811
    from registry_priority import MarketPriorities

    market(
        root,
        [service("sage-uk"), service("sage-canada")],
        {"sage-uk": {"aliases": ["Sage"]}, "sage-canada": {"aliases": ["Sage"]}},
    )
    result = MarketPriorities([]).describe(vendor("com.sage/mcp", title="Sage"))
    assert result["tier"] == "other"
    assert result["ambiguous_services"] == ["sage-canada", "sage-uk"]


def test_a_different_product_with_same_publisher_word_does_not_match(root):  # noqa: F811
    from registry_priority import MarketPriorities

    market(root, [service("wave", name="Wave")])
    record = vendor("co.wave/mcp", "https://mcp.wave.co", "Wave AI")
    record["server"]["description"] = "AI note taker for calls and meetings, not accounting"
    assert MarketPriorities([]).describe(record)["tier"] == "other"


def test_brand_in_nested_namespace_is_not_vendor_domain_evidence(root):  # noqa: F811
    from registry_priority import MarketPriorities

    market(root, [service("sap", name="SAP")])
    record = vendor(
        "ai.oobeprotocol.sap.mcp/sap-mcp", "https://mcp.sap.oobeprotocol.ai/mcp", "SAP MCP Server"
    )
    assert MarketPriorities([]).describe(record)["tier"] == "target_unverified"


def test_reviewed_product_mismatch_is_not_a_market_match(root):  # noqa: F811
    from registry_priority import MarketPriorities

    name = "ai.oobeprotocol.sap.mcp/sap-mcp"
    market(
        root,
        [service("sap", name="SAP")],
        {"sap": {"excluded_registry_names": {name: "Solana gateway; not SAP enterprise software"}}},
    )
    record = vendor(name, "https://mcp.sap.oobeprotocol.ai/mcp", "SAP MCP Server")
    assert MarketPriorities([]).describe(record)["tier"] == "other"


def test_missing_title_can_match_vendor_namespace_but_not_an_arbitrary_github_account(root):  # noqa: F811
    from registry_priority import MarketPriorities

    market(root, [service("sumup")])
    genuine = vendor("com.sumup/mcp", "https://sumup.com/mcp")
    genuine["server"].pop("title")
    fake = vendor("io.github.sumup/mcp", "https://elsewhere.test/mcp")
    fake["server"].pop("title")
    priority = MarketPriorities([])
    assert priority.describe(genuine)["tier"] == "target_vendor_candidate"
    assert priority.describe(fake)["tier"] == "other"


def test_higher_ranked_vendor_does_not_erase_same_title_pending_wrapper_review(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    wrapper = vendor("com.aaa/sumup", "https://aaa.com/mcp", "SumUp MCP")
    discover([wrapper], limit=1)
    draft = root / "discovery/candidates/sumup-mcp/catalog.yaml"
    draft.write_text(draft.read_text() + "# reviewer checked this alternative\n")
    url = "https://mcp.sumup.com/mcp"
    market(root, [service("sumup")], {"sumup": {"official_endpoints": [official(url)]}})
    result = discover([wrapper, vendor("io.github.sumup/mcp", url, "SumUp MCP Server")], limit=1)
    assert result["proposed"] == ["io.github.sumup/mcp"]
    assert "reviewer checked this alternative" in draft.read_text()
    assert result["duplicates"][0]["name"] == "com.aaa/sumup"


def test_occupied_pending_slug_cannot_block_new_verified_vendor(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    wrapper = vendor("com.aaa/sumup", "https://aaa.com/mcp", "SumUp")
    discover([wrapper], limit=1)
    draft = root / "discovery/candidates/sumup/catalog.yaml"
    before = draft.read_bytes()
    url = "https://mcp.sumup.com/mcp"
    market(root, [service("sumup")], {"sumup": {"official_endpoints": [official(url)]}})
    records = [wrapper, vendor("io.github.sumup/mcp", url, "SumUp")]
    result = discover(records, limit=1)
    assert result["proposed"] == ["io.github.sumup/mcp"]
    assert draft.read_bytes() == before
    queued = list((root / "discovery/candidates").iterdir())
    assert len(queued) == 2
    contents = {p: p.read_bytes() for p in (root / "discovery").rglob("*") if p.is_file()}
    discover(records, limit=1)
    assert contents == {p: p.read_bytes() for p in contents}
