#!/usr/bin/env python3
"""Prepare cloned repositories on their documented contribution base branch."""

from __future__ import annotations

from cognalys.workspace import workspace_root
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = workspace_root()
EXTRACTION_SRC = ROOT / "src" / "cognalys" / "extraction"

from cognalys.extraction.repository_guidance import (  # noqa: E402
    discover_repository_guidance,
    infer_branch_policy,
)
from cognalys.utils.network_git import run_git_command  # noqa: E402
from cognalys.utils.step_outcome import step_exit_code  # noqa: E402


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    # A fetch is bounded like every network git command (utils.network_git).
    result = run_git_command(
        ["git", *args], cwd=repo, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result


def ref_exists(repo: Path, ref: str) -> bool:
    return git(repo, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0


def has_only_line_ending_changes(repo: Path) -> bool:
    """True if every uncommitted change is a pure CRLF/LF flip.

    A base clone made before this project pinned core.autocrlf=false on
    clone (see src/extraction/extract_methods_single_csv.py) has every
    tracked text file reported as modified by `git status --porcelain`,
    purely from line-ending normalization -- not real edits. `git diff
    --ignore-all-space --quiet` is blind to that same normalization, so it
    exits 0 only when there is no actual content difference. Any untracked
    file is treated as real, unexpected state and left alone.
    """
    porcelain = git(repo, "status", "--porcelain").stdout
    if any(line.startswith("??") for line in porcelain.splitlines()):
        return False
    unstaged_clean = git(
        repo, "diff", "--ignore-all-space", "--quiet", check=False
    ).returncode == 0
    staged_clean = git(
        repo, "diff", "--cached", "--ignore-all-space", "--quiet", check=False
    ).returncode == 0
    return unstaged_clean and staged_clean


def prepare(repo: Path) -> dict:
    guidance = discover_repository_guidance(repo)
    policy = infer_branch_policy(guidance)
    base = str(policy.get("recommended_base_branch", "")).strip()
    current = git(repo, "branch", "--show-current").stdout.strip()
    result = {
        "repository": repo.name,
        "initial_branch": current,
        "selected_branch": current,
        "branch_policy": policy,
        "guidance_files": [item.get("path", "") for item in guidance],
        "status": "unchanged",
    }
    if not base or base == current:
        return result
    if git(repo, "status", "--porcelain").stdout.strip():
        if has_only_line_ending_changes(repo):
            # A repo whose diff cache disagrees with its own working tree
            # (e.g. content removed outside git) can pass the line-ending-
            # only check above yet still reject a plain checkout. Ignore
            # that failure and fall through to the standard diagnostic
            # below instead of surfacing a confusing git plumbing error.
            git(
                repo, "-c", "core.autocrlf=false", "checkout", "--", ".",
                check=False,
            )
        if git(repo, "status", "--porcelain").stdout.strip():
            raise RuntimeError("repository has uncommitted changes")

    remotes = git(repo, "remote").stdout.split()
    remote_ref = ""
    for remote in ("upstream", "origin"):
        if remote in remotes and ref_exists(repo, f"refs/remotes/{remote}/{base}"):
            remote_ref = f"{remote}/{base}"
            break
    if not remote_ref:
        errors = []
        for remote in ("upstream", "origin"):
            if remote not in remotes:
                continue
            fetched = git(
                repo, "fetch", remote,
                f"refs/heads/{base}:refs/remotes/{remote}/{base}",
                check=False,
            )
            if fetched.returncode == 0:
                remote_ref = f"{remote}/{base}"
                break
            errors.append(f"{remote}: {fetched.stderr.strip()}")
        if not remote_ref:
            # Guidance can name a branch the repository no longer has
            # (aShell, 2026-11-02: the documented 'main' does not exist on the
            # remote, and four app findings were never analysed). The clone's
            # own branch is the remote's default, so stay on it and record
            # why instead of failing the whole step.
            result.update({
                "status": "documented_branch_missing",
                "documented_branch": base,
                "documented_branch_errors": errors,
            })
            return result

    if ref_exists(repo, f"refs/heads/{base}"):
        git(repo, "checkout", base)
    else:
        # A shallow/single-branch clone may have origin/develop as a fetched
        # ref while its remote fetch refspec still recognizes only master.
        # Creating from the explicit ref works without requiring Git to treat
        # it as an auto-trackable remote branch.
        git(repo, "checkout", "-b", base, f"refs/remotes/{remote_ref}")
    result.update({"selected_branch": base, "status": "changed"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    # Repeatable. With none, every clone in --projects-dir is prepared, which
    # is what the pipeline used to pass for a whole-CSV run: a five-row CSV
    # checked out, fetched and branched every one of sixty unrelated clones
    # left by earlier runs.
    parser.add_argument("--project", action="append", default=[])
    args = parser.parse_args()

    selected = {name for name in args.project if name}
    repos = sorted(
        path for path in args.projects_dir.iterdir()
        if path.is_dir() and (path / ".git").exists()
        and (not selected or path.name in selected)
    )
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repositories": [],
    }
    failed = 0
    for repo in repos:
        try:
            item = prepare(repo)
            print(
                f"{repo.name}: {item['initial_branch']} -> "
                f"{item['selected_branch']} ({item['status']})"
            )
        except Exception as exc:
            failed += 1
            item = {"repository": repo.name, "status": "failed", "error": str(exc)}
            print(f"{repo.name}: failed: {exc}", file=sys.stderr)
        report["repositories"].append(item)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not repos:
        print("No matching cloned repositories found", file=sys.stderr)
        return 1
    return step_exit_code(len(repos) - failed, failed)


if __name__ == "__main__":
    raise SystemExit(main())
