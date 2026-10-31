#!/usr/bin/env python3
"""Publish a fresh-main Registry snapshot to a single, reviewable pull request.

Run after registry_sync.py sync in a disposable checkout of origin/main.
The rolling branch is automation-owned; make manual catalog changes separately.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import build_index

BRANCH = "registry-sync/latest"
TITLE = "Registry updates and new MCP candidates"
SYNC_PATHS = ("servers", "discovery")


def run(*args: str) -> str:
    return subprocess.run(
        args, cwd=build_index.ROOT, check=True, stdout=subprocess.PIPE, text=True
    ).stdout.strip()


def git(*args: str) -> str:
    return run("git", *args)


def gh(*args: str) -> str:
    return run("gh", *args)


def different(ref: str) -> bool:
    result = subprocess.run(
        ["git", "diff", "--quiet", ref, "--", *SYNC_PATHS], cwd=build_index.ROOT
    )
    if result.returncode not in (0, 1):
        result.check_returncode()
    return result.returncode == 1


def publish(report: Path, reviewer: str) -> None:
    if not reviewer.strip():
        raise ValueError("a GitHub reviewer login is required")
    base = git("rev-parse", "origin/main")
    if git("rev-parse", "HEAD") != base:
        raise ValueError("sync must start from origin/main, not the previous PR branch")
    # Include newly discovered, untracked drafts in comparisons with main and
    # with the pending PR. git diff alone cannot see these new files.
    paths = [
        p
        for p in SYNC_PATHS
        if (build_index.ROOT / p).exists() or git("ls-files", "--", p)
    ]
    if paths:
        git("add", "--", *paths)
    if not different(base):
        print("No Registry changes; no review notification sent.")
        return

    prs = json.loads(
        gh(
            "pr",
            "list",
            "--state",
            "open",
            "--base",
            "main",
            "--head",
            BRANCH,
            "--json",
            "number,url,body,isCrossRepository",
        )
    )
    # `--head` matches a fork's branch of the same name too. Only a branch in this
    # repository, which only automation and maintainers can push, is ours.
    prs = [pr for pr in prs if pr.get("isCrossRepository") is False]
    old = git("ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}")
    old_sha = old.split()[0] if old else ""
    if old_sha:
        git("fetch", "origin", f"refs/heads/{BRANCH}")
    same = bool(prs and old_sha and not different(old_sha))
    marker = f"<!-- registry-sync-reviewed-head: {old_sha} -->"
    if same and marker in prs[0].get("body", ""):
        print(
            "The pending Registry updates are unchanged; no review notification sent."
        )
        retire_legacy(prs[0]["url"])
        return

    if not same:
        git("switch", "-C", BRANCH)
        git("commit", "-m", "Registry updates and new MCP candidates")
        problems = build_index.revision_problems("origin/main")
        if problems:
            raise ValueError("\n".join(problems))
        # Refuse a snapshot if main moved while the Registry was being read.
        current = git("ls-remote", "--heads", "origin", "refs/heads/main").split()[0]
        if current != base:
            raise ValueError(
                "main advanced during sync; rerun to rebuild revisions from fresh main"
            )
        git(
            "push",
            f"--force-with-lease=refs/heads/{BRANCH}:{old_sha}",
            "origin",
            f"HEAD:refs/heads/{BRANCH}",
        )
        old_sha = git("rev-parse", "HEAD")

    body = report.with_name("registry-pr-body.md")
    body.write_text(
        report.read_text(encoding="utf-8")
        + "\nCheck each change against the vendor's documentation before merging.\n\n"
        + "This PR is refreshed from main. Its branch is owned by automation; "
        + "make manual catalog edits in a separate PR. "
        + "Approve any pending Validate submission workflow before merging.\n",
        encoding="utf-8",
    )
    if prs:
        url = prs[0]["url"]
        gh("pr", "edit", str(prs[0]["number"]), "--add-reviewer", reviewer)
    else:
        url = gh(
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            BRANCH,
            "--title",
            TITLE,
            "--body-file",
            str(body),
            "--reviewer",
            reviewer,
        )

    # Save the success marker only after the review request succeeded. If GitHub
    # fails after the push, the next run retries notification without another push.
    with body.open("a", encoding="utf-8") as stream:
        stream.write(f"\n<!-- registry-sync-reviewed-head: {old_sha} -->\n")
    gh("pr", "edit", url, "--title", TITLE, "--body-file", str(body))
    retire_legacy(url)
    print(url)


def retire_legacy(url: str) -> None:
    # Only retire dated automation PRs after their replacement and review request exist.
    legacy = json.loads(
        gh(
            "pr",
            "list",
            "--state",
            "open",
            "--base",
            "main",
            "--author",
            "github-actions[bot]",
            "--limit",
            "1000",
            "--json",
            "number,headRefName,isCrossRepository",
        )
    )
    for pr in legacy:
        if pr.get("isCrossRepository") is not False:
            continue
        if re.fullmatch(r"registry-sync/\d{4}-\d{2}-\d{2}", pr["headRefName"]):
            gh(
                "pr",
                "close",
                str(pr["number"]),
                "--comment",
                f"Superseded by {url}, which consolidates Registry updates from current main.",
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    publish(args.report, args.reviewer)
