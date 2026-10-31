"""Exercise rolling publication with real Git repositories and a GitHub boundary fake."""

import json
import subprocess
from pathlib import Path

import pytest

import build_index as bi
import registry_sync as rs
from test_build_index import root as catalog_root  # noqa: F401
from test_build_index import write_entry, registry_record


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def publication(catalog_root, tmp_path, monkeypatch):  # noqa: F811 - imported fixture
    import registry_sync_pr as pub

    root = catalog_root
    write_entry(root)
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-u", "origin", "main")
    report = root / "report.md"
    report.write_text("Registry changes\n")
    state = {
        "prs": [],
        "reviews": [],
        "closed": [],
        "edits": 0,
        "fail_create": False,
        "fail_review": False,
    }

    def gh(*args):
        if args[:2] == ("pr", "list"):
            prs = state["prs"]
            if "--head" in args:
                prs = [
                    p for p in prs if p["headRefName"] == args[args.index("--head") + 1]
                ]
            return json.dumps(prs)
        if args[:2] == ("pr", "create"):
            if state["fail_create"]:
                raise RuntimeError("GitHub unavailable")
            state["prs"].append(
                {
                    "number": 42,
                    "headRefName": pub.BRANCH,
                    "url": "https://github.com/x/y/pull/42",
                    "body": "",
                    "isCrossRepository": False,
                }
            )
            state["reviews"].append(args[args.index("--reviewer") + 1])
            return "https://github.com/x/y/pull/42"
        if args[:2] == ("pr", "edit"):
            state["edits"] += 1
            if "--add-reviewer" in args:
                if state["fail_review"]:
                    raise RuntimeError("review request failed")
                state["reviews"].append(args[args.index("--add-reviewer") + 1])
            if "--body-file" in args:
                state["prs"][-1]["body"] = Path(
                    args[args.index("--body-file") + 1]
                ).read_text()
            return ""
        if args[:2] == ("pr", "close"):
            state["closed"].append(int(args[2]))
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(pub, "gh", gh)
    return pub, root, report, state


def refresh(root, monkeypatch, version):
    newer = registry_record()
    newer["server"]["version"] = version
    monkeypatch.setattr(rs, "registry_latest", lambda name: newer)
    rs.sync_entry(root / "servers/example")


def test_reuses_one_pr_and_does_not_notify_for_identical_updates(
    publication, monkeypatch
):
    pub, root, report, state = publication
    refresh(root, monkeypatch, "2.0.0")
    pub.publish(report, "reviewer")
    first = git(root, "rev-parse", "HEAD")
    edits = state["edits"]
    git(root, "switch", "main")
    refresh(root, monkeypatch, "2.0.0")
    pub.publish(report, "reviewer")
    assert git(root, "rev-parse", "origin/" + pub.BRANCH) == first
    assert len(state["prs"]) == 1
    assert state["reviews"] == ["reviewer"]
    assert state["edits"] == edits
    git(root, "restore", "servers")
    refresh(root, monkeypatch, "3.0.0")
    pub.publish(report, "reviewer")
    assert len(state["prs"]) == 1
    assert state["edits"] > edits
    assert state["reviews"] == ["reviewer", "reviewer"]


def test_rebuilds_from_main_with_a_revision_above_the_merged_update(
    publication, monkeypatch
):
    pub, root, report, state = publication
    refresh(root, monkeypatch, "2.0.0")
    pub.publish(report, "reviewer")
    git(root, "switch", "main")
    # Another catalog update is merged while the rolling PR is waiting.
    refresh(root, monkeypatch, "1.5.0")
    git(root, "add", "servers")
    git(root, "commit", "-qm", "independent update")
    git(root, "push", "origin", "main")
    refresh(root, monkeypatch, "3.0.0")
    pub.publish(report, "reviewer")
    assert bi.revision_problems("origin/main") == []
    assert "revision: 3" in git(root, "show", "HEAD:servers/example/catalog.yaml")
    assert git(root, "rev-parse", "HEAD^") == git(root, "rev-parse", "main")
    assert len(state["prs"]) == 1


def test_no_change_does_not_open_or_notify(publication):
    pub, root, report, state = publication
    pub.publish(report, "reviewer")
    assert state["prs"] == state["reviews"] == []


def test_supersedes_only_dated_bot_prs_after_success(publication, monkeypatch):
    pub, root, report, state = publication
    state["prs"] = [
        {"number": 4, "headRefName": "registry-sync/2026-10-16", "isCrossRepository": False},
        {"number": 8, "headRefName": "registry-sync/manual", "isCrossRepository": False},
        {"number": 9, "headRefName": "registry-sync/2026-10-17", "isCrossRepository": True},
    ]
    state["fail_create"] = True
    refresh(root, monkeypatch, "2.0.0")
    with pytest.raises(RuntimeError, match="GitHub unavailable"):
        pub.publish(report, "reviewer")
    assert state["closed"] == []
    state["fail_create"] = False
    git(root, "switch", "main")
    refresh(root, monkeypatch, "2.0.0")
    pub.publish(report, "reviewer")
    assert state["closed"] == [4]


def test_push_failure_does_not_create_pr_or_close_existing(publication, monkeypatch):
    pub, root, report, state = publication
    refresh(root, monkeypatch, "2.0.0")
    remote = git(root, "remote", "get-url", "origin")
    hook = Path(remote) / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(subprocess.CalledProcessError):
        pub.publish(report, "reviewer")
    assert state["prs"] == state["reviews"] == state["closed"] == []


def test_retries_review_request_after_push_succeeded(publication, monkeypatch):
    pub, root, report, state = publication
    refresh(root, monkeypatch, "2.0.0")
    pub.publish(report, "reviewer")
    git(root, "switch", "main")
    refresh(root, monkeypatch, "3.0.0")
    state["fail_review"] = True
    with pytest.raises(RuntimeError, match="review request failed"):
        pub.publish(report, "reviewer")
    git(root, "switch", "main")
    refresh(root, monkeypatch, "3.0.0")
    state["fail_review"] = False
    pub.publish(report, "reviewer")
    assert state["reviews"] == ["reviewer", "reviewer"]


def test_refuses_to_publish_if_main_moves_during_sync(publication, monkeypatch):
    pub, root, report, state = publication
    refresh(root, monkeypatch, "2.0.0")
    actual_git = pub.git

    def advanced_main(*args):
        if args == ("ls-remote", "--heads", "origin", "refs/heads/main"):
            return "0" * 40 + "\trefs/heads/main"
        return actual_git(*args)

    monkeypatch.setattr(pub, "git", advanced_main)
    with pytest.raises(ValueError, match="main advanced"):
        pub.publish(report, "reviewer")
    assert state["prs"] == state["reviews"] == state["closed"] == []


def test_untracked_discovery_alone_is_published_and_identical_runs_do_not_notify(
    publication,
):
    pub, root, report, state = publication
    draft = root / "discovery/candidates/newco/registry.json"
    draft.parent.mkdir(parents=True)
    draft.write_text(json.dumps(registry_record(name="com.newco/mcp")))
    pub.publish(report, "reviewer")
    first = git(root, "rev-parse", "HEAD")
    assert git(root, "show", "HEAD:discovery/candidates/newco/registry.json")
    assert len(state["prs"]) == 1
    git(root, "switch", "main")
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(json.dumps(registry_record(name="com.newco/mcp")))
    pub.publish(report, "reviewer")
    assert git(root, "rev-parse", "origin/" + pub.BRANCH) == first
    assert state["reviews"] == ["reviewer"]


def test_closed_pr_with_retained_branch_does_not_block_new_discovery(
    publication, monkeypatch
):
    pub, root, report, state = publication
    refresh(root, monkeypatch, "2.0.0")
    pub.publish(report, "reviewer")
    state["prs"] = []  # review closed without deleting its remote branch
    git(root, "switch", "main")
    draft = root / "discovery/duplicates.json"
    draft.parent.mkdir()
    draft.write_text("[]\n")
    pub.publish(report, "reviewer")
    assert len(state["prs"]) == 1
    assert git(root, "show", "HEAD:discovery/duplicates.json") == "[]"


def test_deleted_registry_updates_can_still_reach_review(publication, monkeypatch):
    pub, root, report, state = publication
    monkeypatch.setattr(
        rs, "registry_latest", lambda name: registry_record(status="deleted")
    )
    rs.sync_entry(root / "servers/example")
    pub.publish(report, "reviewer")
    assert len(state["prs"]) == 1


def test_a_fork_pr_on_the_rolling_branch_name_is_not_reused(publication, monkeypatch):
    pub, root, report, state = publication
    state["prs"] = [
        {
            "number": 66,
            "headRefName": pub.BRANCH,
            "url": "https://github.com/attacker/y/pull/66",
            "body": "",
            "isCrossRepository": True,
        }
    ]
    refresh(root, monkeypatch, "2.0.0")
    pub.publish(report, "reviewer")
    created = [pr for pr in state["prs"] if pr["number"] == 42]
    assert len(created) == 1
    assert state["prs"][0]["body"] == ""
    assert state["reviews"] == ["reviewer"]
