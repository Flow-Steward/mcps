"""Exercise PR shell control flow with fake git/gh, without network or a GitHub token."""

import os
import subprocess
from pathlib import Path

import yaml
import pytest


def steps():
    path = Path(__file__).resolve().parents[1] / ".github/workflows/registry-sync.yml"
    return yaml.safe_load(path.read_text())["jobs"]["sync"]["steps"]


def test_workflow_shell_blocks_parse():
    for step in steps():
        if "run" in step:
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)


@pytest.mark.parametrize("validator_exit", [0, 1])
def test_untracked_candidates_and_rejected_updates_still_open_a_review_pr(tmp_path, validator_exit):
    script = next(s["run"] for s in steps() if s.get("name") == "Open a pull request when something changed")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "servers").mkdir()
    (tmp_path / "discovery").mkdir()
    (tmp_path / "report.md").write_text("Review candidates\n")
    for tool in ("git", "gh", "python"):
        path = bindir / tool
        path.write_text(
            '#!/bin/sh\nprintf "%s\\n" "' + tool + ' $*" >> "$CALL_LOG"\n'
            'case "$*" in\n'
            '  "status --porcelain -- servers discovery") printf "?? discovery/candidates/newco/\\n";;\n'
            '  "ls-remote "*) exit 2;;\n'
            "esac\n"
            + (
                f'echo "deleted upstream record requires review"; exit {validator_exit}\n'
                if tool == "python"
                else ""
            )
        )
        path.chmod(0o755)
    log = tmp_path / "calls"
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "RUNNER_TEMP": str(tmp_path),
        "REGISTRY_REVIEW_BRANCH": "registry-sync/review-123-1",
    }
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        check=True,
    )
    calls = log.read_text()
    assert "git add -- discovery" in calls
    assert "git commit" in calls
    assert "gh pr create --draft --base main --head registry-sync/review" in calls
    assert "--force" not in calls
    if validator_exit:
        assert "deleted upstream record requires review" in (tmp_path / "body.md").read_text()


def test_closed_pr_gets_a_fresh_branch_even_when_its_old_branch_remains(tmp_path):
    script = next(s["run"] for s in steps() if s.get("name") == "Resume the existing review branch")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls"
    for tool in ("git", "gh"):
        path = bindir / tool
        path.write_text('#!/bin/sh\nprintf "%s\\n" "' + tool + ' $*" >> "$CALL_LOG"\n')
        path.chmod(0o755)
    envfile = tmp_path / "environment"
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "GITHUB_RUN_ID": "456",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_ENV": str(envfile),
    }
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=tmp_path, env=env, check=True)
    assert "git switch -c registry-sync/review-456-2 origin/main" in log.read_text()
    assert "REGISTRY_REVIEW_BRANCH=registry-sync/review-456-2" in envfile.read_text()


def test_workflow_resumes_the_pending_review_branch_before_scanning():
    workflow_steps = steps()
    names = [s.get("name", "") for s in workflow_steps]
    resume = names.index("Resume the existing review branch")
    scan = names.index("Refresh listed servers and discover new candidates")
    assert resume < scan
    assert "git merge --no-edit origin/main" in workflow_steps[resume]["run"]
