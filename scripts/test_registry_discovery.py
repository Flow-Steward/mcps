"""Discovery is an offline review queue, never an automatic catalog publisher."""

import json
from urllib.parse import parse_qs, urlsplit

import build_index as bi
import pytest
import registry_sync as rs
import yaml
from test_build_index import catalog, registry_record, root, write_entry  # noqa: F401


def vendor(name="com.newco/mcp", url="https://mcp.newco.com/mcp", title="NewCo"):
    record = registry_record(name=name, url=url)
    record["server"]["title"] = title
    return record


def test_listing_follows_opaque_cursors_and_keeps_latest_filter(monkeypatch):
    queries = []

    def fetch(url):
        query = parse_qs(urlsplit(url).query)
        queries.append(query)
        if "cursor" not in query:
            return {"servers": [vendor()], "metadata": {"nextCursor": "a/+?=:1"}}
        return {"servers": [vendor("com.other/mcp")], "metadata": {}}

    monkeypatch.setattr(rs, "fetch_json", fetch)
    assert len(rs.registry_all()) == 2
    assert queries[1]["cursor"] == ["a/+?=:1"]
    assert all(q["version"] == ["latest"] for q in queries)


def test_listing_rejects_a_repeated_cursor(monkeypatch):
    monkeypatch.setattr(
        rs,
        "fetch_json",
        lambda url: {"servers": [], "metadata": {"nextCursor": "same"}},
    )
    with pytest.raises(ValueError, match="cursor"):
        rs.registry_all()


def test_listing_does_not_return_a_partial_snapshot(monkeypatch):
    def fetch(url):
        if "cursor=" in url:
            raise LookupError("page unavailable")
        return {"servers": [vendor()], "metadata": {"nextCursor": "next"}}

    monkeypatch.setattr(rs, "fetch_json", fetch)
    with pytest.raises(LookupError, match="page unavailable"):
        rs.registry_all()


def test_discovery_stages_drafts_outside_catalog_and_is_idempotent(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    result = discover([vendor()], limit=25)
    assert result["proposed"] == ["com.newco/mcp"]
    folders = list((root / "discovery/candidates").iterdir())
    assert len(folders) == 1
    draft = yaml.safe_load((folders[0] / "catalog.yaml").read_text())
    assert "official" not in draft
    assert draft["remotes"][0]["auth"]["mode"] == "TODO"
    assert not (root / "servers").exists()
    before = {p: p.read_bytes() for p in (root / "discovery").rglob("*") if p.is_file()}
    discover([vendor()], limit=25)
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize(
    "record",
    [
        vendor(name="com.example/mcp"),
        vendor(url="https://MCP.EXAMPLE.COM:443/mcp/"),
        vendor(title="EXAMPLE MCP Server"),
    ],
)
def test_discovery_blocks_existing_service_duplicates(root, record):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    write_entry(root)
    result = discover([record], limit=25)
    assert not result["proposed"]
    assert result["duplicates"][0]["matches"][0]["id"] == "example"


def test_manual_records_and_curated_endpoints_are_also_compared(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    body = catalog(remotes=[{"url": "https://reviewed.example.com/mcp"}])
    body.pop("source")
    write_entry(root, server=registry_record()["server"], body=body)
    result = discover([vendor(url="https://reviewed.example.com/mcp")], limit=25)
    assert result["duplicates"]


def test_templates_and_distinct_case_sensitive_paths_are_not_duplicates(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    write_entry(
        root,
        record=registry_record(url="https://{instance_host}/mcp"),
        body=catalog(remotes=[{"url": "https://{instance_host}/mcp"}]),
    )
    records = [
        vendor(url="https://{instance_host}/mcp"),
        vendor("com.alpha/mcp", "https://mcp.alpha.com/Case", "Alpha"),
        vendor("com.beta/mcp", "https://mcp.alpha.com/case", "Beta"),
    ]
    result = discover(records, limit=25)
    assert len(result["proposed"]) == 3


def test_queue_filters_inactive_aggregators_and_local_servers_and_caps_total(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    inactive = vendor("com.inactive/mcp")
    inactive["_meta"][bi.OFFICIAL_META_KEY]["status"] = "deprecated"
    local = vendor("com.local/mcp")
    local["server"].pop("remotes")
    records = [
        inactive,
        local,
        vendor("ai.smithery/tool"),
        vendor(),
        vendor("com.alpha/mcp", "https://alpha.com/mcp", "Alpha"),
    ]
    assert len(discover(records, limit=1)["proposed"]) == 1
    assert len(list((root / "discovery/candidates").iterdir())) == 1
    discover(records, limit=1)
    assert len(list((root / "discovery/candidates").iterdir())) == 1


def test_incoming_duplicates_and_rejections_are_not_reproposed(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    (root / "discovery").mkdir()
    (root / "discovery/ignored.json").write_text(json.dumps({"com.rejected/mcp": "Not vendor operated"}))
    result = discover([vendor(), vendor("com.alias/mcp"), vendor("com.rejected/mcp")], limit=25)
    assert len(result["proposed"]) == 1
    assert len(result["duplicates"]) == 1


def test_pending_draft_edits_survive_refresh_and_lowered_limit(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    records = [vendor(), vendor("com.alpha/mcp", "https://alpha.com/mcp", "Alpha")]
    discover(records, limit=2)
    draft = root / "discovery/candidates/newco/catalog.yaml"
    draft.write_text(draft.read_text() + "# human review in progress\n")
    records[0]["server"]["version"] = "2.0"
    discover(records, limit=1)
    assert "human review in progress" in draft.read_text()
    assert len(list((root / "discovery/candidates").iterdir())) == 2


def test_promoted_or_rejected_candidates_leave_the_pending_queue(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    discover([vendor()], limit=25)
    write_entry(root, item_id="newco", record=vendor(), body=catalog(title="NewCo"))
    assert not discover([vendor()], limit=25)["proposed"]
    assert not list((root / "discovery/candidates").iterdir())


def test_missing_registry_record_does_not_erase_pending_review(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    discover([vendor()], limit=25)
    result = discover([], limit=25)
    assert (root / "discovery/candidates/newco/catalog.yaml").exists()
    assert result["unavailable"] == ["com.newco/mcp"]


def test_query_strings_are_not_erased_during_endpoint_comparison():
    from registry_discovery import endpoint_key

    assert endpoint_key("https://example.com/mcp?tenant=one") != endpoint_key(
        "https://example.com/mcp?tenant=two"
    )
    assert endpoint_key("https://example.com/mcp/Case") != endpoint_key("https://example.com/mcp/case")


def test_github_publisher_does_not_receive_an_official_badge(root):  # noqa: F811
    from registry_discovery import stage_candidates as discover

    record = vendor("io.github.vendor/tool", "https://vendor.com/mcp", "Vendor")
    discover([record], limit=25)
    draft = yaml.safe_load((root / "discovery/candidates/vendor/catalog.yaml").read_text())
    review = json.loads((root / "discovery/candidates/vendor/review.json").read_text())
    assert "official" not in draft
    assert review["ownership"] == "unverified"


def test_refresh_failure_stops_publication(root, monkeypatch):  # noqa: F811
    write_entry(root)

    def fail(name):
        raise LookupError("Registry unavailable")

    monkeypatch.setattr(rs, "registry_latest", fail)
    assert rs.main(["sync"]) == 1


def test_add_checks_duplicates_before_probe_or_write(root, monkeypatch):  # noqa: F811
    write_entry(root)
    monkeypatch.setattr(rs, "registry_latest", lambda name: vendor(url="https://mcp.example.com/mcp"))
    monkeypatch.setattr(rs, "probe_auth", lambda url: pytest.fail("duplicate must not be probed"))
    assert rs.main(["add", "com.newco/mcp", "--id", "newco", "--category", "productivity"]) == 2
    assert not (root / "servers/newco").exists()


def test_discovery_failure_returns_nonzero_and_preserves_queue(root, monkeypatch):  # noqa: F811
    queue = root / "discovery/candidates/keep"
    queue.mkdir(parents=True)
    (queue / "registry.json").write_text("{}")

    def fail():
        raise LookupError("second page unavailable")

    monkeypatch.setattr(rs, "registry_all", fail)
    assert rs.main(["sync", "--discover"]) == 1
    assert (queue / "registry.json").read_text() == "{}"
