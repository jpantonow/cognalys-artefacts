#!/usr/bin/env python3
"""Close GitHub issues previously created by the pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ModuleNotFoundError:
    requests = None


GITHUB_API = "https://api.github.com"


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def resolve_issue(record: dict) -> tuple[str, str, int] | None:
    if record.get("dry_run") is True:
        return None

    number = record.get("number")
    repository_url = record.get("repository_url", "")
    if not number or not repository_url:
        raise ValueError("record does not contain a real GitHub issue")

    parts = urlparse(repository_url).path.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "repos":
        raise ValueError(f"invalid repository_url: {repository_url}")
    return parts[1], parts[2], int(number)


def cleanup_approval_names(record: dict, owner: str, repo: str, number: int) -> set:
    """The names --approve-issue-cleanup accepts for this issue.

    Its finding id (issue_creator records it since 2026-10-30) and, for a
    record written before, the issue itself as owner/repo#number. Closing an
    issue is a write a maintainer sees; --confirm-issue-cleanup alone closed
    every issue the directory recorded (batch6 audit, 2026-10-30).
    """
    names = {f"{owner}/{repo}#{number}"}
    finding = str(record.get("finding_id", "") or "").strip()
    if finding:
        names.add(finding)
    return names


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Close GitHub issues created by the pipeline."
    )
    parser.add_argument("--issues-dir", type=Path, required=True)
    parser.add_argument("--confirm-issue-cleanup", action="store_true",
                        help="kept for older command lines: nothing is closed "
                             "unless the issue is named by --approve-issue-cleanup")
    parser.add_argument("--approve-issue-cleanup", action="append", default=[],
                        metavar="FINDING_ID|OWNER/REPO#N",
                        help="close the issue of this finding (or this issue); "
                             "repeatable. Every other issue is only previewed.")
    parser.add_argument("--github-token")
    parser.add_argument("--github-api", default=GITHUB_API)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--project", default="")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    approved = {str(item).strip() for item in args.approve_issue_cleanup if str(item).strip()}
    if args.confirm_issue_cleanup and not approved:
        print("--confirm-issue-cleanup names no issue: pass --approve-issue-cleanup "
              "<finding id or owner/repo#N> for each one to close.")
    args.confirm_issue_cleanup = bool(approved)
    files = sorted(args.issues_dir.glob("*_issue_created.json"))
    if args.project:
        files = [
            file for file in files
            if file.name.startswith(f"{args.project}_")
        ]
    if args.limit > 0:
        files = files[: args.limit]

    token = args.github_token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if args.confirm_issue_cleanup and requests is None:
        print("Missing dependency: requests", file=sys.stderr)
        return 2
    if args.confirm_issue_cleanup and not token:
        print(
            "GITHUB_TOKEN or GH_TOKEN is required for confirmed issue cleanup.",
            file=sys.stderr,
        )
        return 2

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api = args.github_api.rstrip("/")

    if args.confirm_issue_cleanup:
        auth = requests.get(f"{api}/user", headers=headers, timeout=15)
        if auth.status_code != 200:
            print(
                f"GitHub authentication failed: HTTP {auth.status_code}",
                file=sys.stderr,
            )
            return 2
        print(f"Authenticated as: {auth.json().get('login', 'unknown')}")
    else:
        print("PREVIEW ONLY: no GitHub issue will be changed.")

    summary = {"closed": 0, "previewed": 0, "skipped": 0, "failed": 0}
    print(f"Found {len(files)} issue records")

    for record_file in files:
        try:
            record = load_json(record_file)
            if record.get("pipeline_cleanup", {}).get("closed") is True:
                print(f"SKIPPED: {record_file.name} was already cleaned up")
                summary["skipped"] += 1
                continue
            resolved = resolve_issue(record)
            if resolved is None:
                print(f"SKIPPED: {record_file.name} is a dry-run record")
                summary["skipped"] += 1
                continue

            owner, repo, number = resolved
            names = cleanup_approval_names(record, owner, repo, number)
            close = bool(names & approved)
            action = "close" if close else "would close"
            print(f"GitHub: {action} issue {owner}/{repo}#{number}"
                  + ("" if close else
                     f" (pass --approve-issue-cleanup {sorted(names)[0]} to close it)"))
            if close:
                response = requests.patch(
                    f"{api}/repos/{owner}/{repo}/issues/{number}",
                    headers=headers,
                    json={"state": "closed", "state_reason": "not_planned"},
                    timeout=30,
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"HTTP {response.status_code}: {response.text}"
                    )
                record["pipeline_cleanup"] = {
                    "closed": True,
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                }
                record_file.write_text(
                    json.dumps(record, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            summary["closed" if close else "previewed"] += 1
        except Exception as exc:
            summary["failed"] += 1
            print(f"FAILED: {record_file.name}: {exc}")

    print(
        "Issue cleanup summary: "
        f"{summary['closed']} closed, {summary['previewed']} would be closed, "
        f"{summary['skipped']} skipped, {summary['failed']} failed"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
