#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import requests

from cognalys.utils.network_git import run_git_command


GITHUB_API = "https://api.github.com"


def run(command, cwd=None):
    result = run_git_command(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def request(session, method, url, expected):
    response = session.request(method, url)
    if response.status_code not in expected:
        raise RuntimeError(
            f"GitHub API {method} {url} returned "
            f"{response.status_code}: {response.text}"
        )
    return response


def load_json(path):
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def reset_clone(repo_path, branch, upstream_url, default_branch, confirmed):
    print(f"  local: reset {repo_path} to upstream/{default_branch}")
    print(f"  local: delete branch {branch}")
    if not confirmed:
        return

    repo_path = Path(repo_path)
    if not (repo_path / ".git").exists():
        raise RuntimeError(f"not a Git repository: {repo_path}")

    remotes = run(["git", "remote"], cwd=repo_path).splitlines()
    if "upstream" in remotes:
        run(["git", "remote", "set-url", "upstream", upstream_url], cwd=repo_path)
    else:
        run(["git", "remote", "add", "upstream", upstream_url], cwd=repo_path)

    run(["git", "fetch", "--prune", "upstream", default_branch], cwd=repo_path)
    run(
        ["git", "checkout", "-B", default_branch, f"upstream/{default_branch}"],
        cwd=repo_path,
    )
    if branch != default_branch:
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=repo_path,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    run(["git", "reset", "--hard", f"upstream/{default_branch}"], cwd=repo_path)
    run(["git", "clean", "-fd"], cwd=repo_path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Close pipeline-created GitHub PRs, delete fork branches, "
            "and hard-reset their local clones."
        )
    )
    parser.add_argument("--pull-requests-dir", type=Path, required=True)
    parser.add_argument(
        "--confirm-cleanup",
        action="store_true",
        help="Kept for older command lines: nothing is changed unless the "
             "finding is named by --approve-cleanup.",
    )
    parser.add_argument(
        "--approve-cleanup",
        action="append",
        default=[],
        metavar="FINDING_ID",
        help="Close this finding's pull request, delete its fork branch and "
             "reset its clone (repeatable). Every other finding is only previewed.",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--project", default="")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    # Closing a pull request and deleting its branch are writes a maintainer
    # sees; --confirm-cleanup alone did both for every PR record in the
    # directory (batch6 audit, 2026-10-30). Only a finding named by
    # --approve-cleanup is changed.
    approved = {str(item).strip() for item in args.approve_cleanup if str(item).strip()}
    if args.confirm_cleanup and not approved:
        print("--confirm-cleanup names no finding: pass --approve-cleanup "
              "<finding id> for each one to clean up.")
    args.confirm_cleanup = bool(approved)
    files = sorted(args.pull_requests_dir.glob("*_pr.json"))
    if args.project:
        files = [
            file for file in files
            if file.name.startswith(f"{args.project}_")
        ]
    if args.limit > 0:
        files = files[:args.limit]

    token = os.getenv("GITHUB_TOKEN")
    if args.confirm_cleanup and not token:
        print("GITHUB_TOKEN is required for confirmed cleanup.", file=sys.stderr)
        return 2

    session = requests.Session()
    if token:
        session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            }
        )

    if args.confirm_cleanup:
        user = request(session, "GET", f"{GITHUB_API}/user", {200}).json()["login"]
        print(f"Authenticated as: {user}")
    else:
        user = "<authenticated-user>"
        print("PREVIEW ONLY: no PR, branch, or clone will be changed.")

    succeeded = 0
    failed = 0
    seen_local = set()

    for metadata_file in files:
        print(f"\nPROCESSING: {metadata_file.name}")
        try:
            metadata = load_json(metadata_file)
            finding_id = str(metadata.get("finding_id", "") or "").strip()
            confirmed = bool(finding_id) and finding_id in approved
            if not confirmed:
                print(f"  preview only: pass --approve-cleanup {finding_id or '<finding id>'} "
                      "to change it")
            created_file = metadata_file.with_name(
                f"{metadata_file.stem}_created.json"
            )
            created = load_json(created_file) if created_file.exists() else {}

            owner = metadata["repo_owner"]
            repo = metadata["repo_name"]
            branch = metadata["branch"]
            repo_path = metadata["repository"]
            upstream_url = f"https://github.com/{owner}/{repo}.git"

            repo_info = None
            if confirmed:
                repo_info = request(
                    session, "GET", f"{GITHUB_API}/repos/{owner}/{repo}", {200}
                ).json()
            default_branch = (
                (created.get("base") or {}).get("ref")
                or (repo_info or {}).get("default_branch")
                or "main"
            )

            pr_number = created.get("number")
            if pr_number and not created.get("dry_run"):
                print(f"  GitHub: close PR {owner}/{repo}#{pr_number}")
                if confirmed:
                    response = session.patch(
                        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}",
                        json={"state": "closed"},
                    )
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"failed closing PR: {response.status_code} {response.text}"
                        )
            else:
                print("  GitHub: no real PR record to close")

            encoded_branch = quote(branch, safe="/")
            print(f"  GitHub: delete {user}/{repo}:{branch}")
            if confirmed:
                request(
                    session,
                    "DELETE",
                    f"{GITHUB_API}/repos/{user}/{repo}/git/refs/heads/{encoded_branch}",
                    {204, 404},
                )

            local_key = (str(Path(repo_path).resolve()), branch)
            if local_key not in seen_local:
                reset_clone(
                    repo_path,
                    branch,
                    upstream_url,
                    default_branch,
                    confirmed,
                )
                seen_local.add(local_key)

            succeeded += 1
        except Exception as error:
            failed += 1
            print(f"  FAILED: {error}")

    print(f"\nCleanup summary: {succeeded} succeeded, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
