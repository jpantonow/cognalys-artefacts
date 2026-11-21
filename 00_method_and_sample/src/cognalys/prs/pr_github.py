#!/usr/bin/env python3
"""
GitHub access for the pull-request revision flow.

Reading a pull request's live state, its maintainer feedback and its CI
results, and pushing a revision back to it. Extracted from scripts/fix_prs.py,
which had grown into the project's largest module while sitting in scripts/,
where domain logic is invisible to the rest of the pipeline and reachable only
by importing a script.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any

import requests

from cognalys.utils.network_git import run_git_command

_LOG_PATH_PATTERN = re.compile(
    r"\b([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:kt|java|kts|gradle))\b(?::(\d+))?"
)


def github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_all(url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while url:
        response = requests.get(
            url,
            headers=github_headers(),
            params={"per_page": 100} if not rows else None,
            timeout=60,
        )
        response.raise_for_status()
        rows.extend(response.json())
        url = response.links.get("next", {}).get("url", "")
    return rows


def fetch_current_pr_state(pr: dict[str, Any]) -> dict[str, Any]:
    """Re-fetch a PR's live state from the GitHub API.

    ``pr`` is a snapshot written when the PR was first created; nothing
    refreshes it afterwards. A maintainer may have closed or merged the PR
    since then, in which case revising it and, with --publish, pushing a new
    commit to its branch serves no purpose and can be a surprising, visible
    action (a push can reopen a closed-but-unmerged PR on GitHub).
    """
    api_url = str(pr.get("url", "")).strip()
    if not api_url:
        return {"status": "unknown", "reason": "no_api_url_in_snapshot"}
    response = requests.get(api_url, headers=github_headers(), timeout=60)
    if response.status_code == 404:
        return {"status": "not_found"}
    response.raise_for_status()
    live = response.json()
    if live.get("state") != "open":
        return {
            "status": "merged" if live.get("merged") else "closed",
            "state": live.get("state", ""),
        }
    return {"status": "open"}


# Accounts that post as bots without GitHub's "[bot]" suffix (user accounts
# run by a service). GitHub Apps always carry the suffix and type "Bot".
KNOWN_BOT_ACCOUNTS = frozenset({
    "codecov-commenter", "coveralls", "sonarcloud", "sonarqubecloud",
    "deepsource-io", "gemini-code-assist", "coderabbitai", "sourcery-ai",
    "copilot-pull-request-reviewer", "github-actions", "netlify", "vercel",
})


def bot_name(login: str) -> str:
    return str(login or "").removesuffix("[bot]").strip().lower()


# Review bots whose comments a recorded outcome keeps, so pr_outcomes gives
# them to planning and generation as automated review comments, never as what
# the maintainer said. GitHub's Copilot reviewer appears as the login
# "Copilot" on review comments (paroj/DSub2000#160, 2026-11-10). Kept out of
# KNOWN_BOT_ACCOUNTS on purpose: feedback_from_pr drops those, and the user
# decided (2026-11-10) that these review points are kept, reclassified.
RECORDED_REVIEW_BOT_ACCOUNTS = frozenset({"copilot"})


def is_bot_login(login: str) -> bool:
    """The login of a review bot or service account, when only the name is recorded."""
    name = bot_name(login)
    return (str(login or "").lower().endswith("[bot]") or name in KNOWN_BOT_ACCOUNTS
            or name in RECORDED_REVIEW_BOT_ACCOUNTS)


def is_bot(user: dict[str, Any] | None) -> bool:
    """A review bot or service account, by GitHub's own marks first."""
    user = user or {}
    login = str(user.get("login", ""))
    return (str(user.get("type", "")).lower() == "bot"
            or login.lower().endswith("[bot]")
            or bot_name(login) in KNOWN_BOT_ACCOUNTS)


def feedback_from_pr(pr: dict[str, Any]) -> list[dict[str, Any]]:
    """Maintainers' comments, reviews and inline comments on a pull request.

    Review bots are not maintainers: on NotallyX#1167 (2026-10-30) this read
    coderabbitai[bot]'s review, with its "Prompt for AI Agents" block, as
    maintainer feedback for the revision prompt and for the recorded outcome
    that adjudication reads. Bot comments are read as untrusted data by
    pr_followup (`cognalys pr-followup`) instead; the failing checks a bot
    reports still reach the revision through the CI evidence.
    """
    feedback: list[dict[str, Any]] = []
    sources = (
        ("conversation", pr.get("comments_url", "")),
        ("inline_review", pr.get("review_comments_url", "")),
        ("review", str(pr.get("url", "")).rstrip("/") + "/reviews"),
    )
    for kind, url in sources:
        if not url:
            continue
        for comment in fetch_all(url):
            if is_bot(comment.get("user")):
                continue
            body = str(comment.get("body", "")).strip()
            state = str(comment.get("state", "")).strip() if kind == "review" else ""
            # A review can formally request changes, or approve, with no
            # summary body at all -- the actual concerns then live only in
            # its separately-fetched inline comments (kind="inline_review").
            # Keep the review record anyway when its state is decisive, so
            # "CHANGES_REQUESTED"/"APPROVED" is never silently dropped just
            # because the reviewer left no top-level text.
            decisive_review = kind == "review" and state not in ("", "COMMENTED")
            if not body and not decisive_review:
                continue
            entry: dict[str, Any] = {
                "kind": kind,
                "author": comment.get("user", {}).get("login", ""),
                "body": body,
                "path": comment.get("path", ""),
                "line": comment.get("line") or comment.get("original_line"),
                "diff_hunk": comment.get("diff_hunk", ""),
                "url": comment.get("html_url", ""),
            }
            if kind == "review":
                entry["review_state"] = state
            if kind == "inline_review":
                # GitHub clears `position` (but keeps `original_position`)
                # once a comment's diff hunk is no longer part of the PR's
                # current diff -- typically because a later commit already
                # changed that code. Treating it the same as live feedback
                # risks "fixing" something already addressed.
                entry["outdated"] = comment.get("position") is None
            feedback.append(entry)
    return feedback


def path_from_log_excerpt(log_excerpt: str) -> str:
    """Best-effort primary candidate path named in a CI failure's own log.

    A linter or compiler failure routinely names the exact file it concerns
    (e.g. "app/src/.../Foo.kt:42: ..."); a dependency-resolution failure
    names no source file at all. Either way this only supplies one candidate
    `path` on the synthesized feedback entry -- the same field inline review
    comments already carry -- so gather_candidate_files' existing (and
    already tolerant of nonexistent paths) discovery decides what is
    actually readable, instead of a hardcoded file-type lock.
    """
    match = _LOG_PATH_PATTERN.search(log_excerpt)
    return match.group(1) if match else ""


def fetch_check_runs(owner_repo: str, ref: str) -> list[dict[str, Any]]:
    """All check runs GitHub recorded for a commit (not a bare-array
    endpoint, unlike fetch_all's callers, so it is not reused here)."""
    runs: list[dict[str, Any]] = []
    url = f"https://api.github.com/repos/{owner_repo}/commits/{ref}/check-runs"
    params: dict[str, Any] | None = {"per_page": 100}
    while url:
        response = requests.get(
            url, headers=github_headers(), params=params, timeout=60
        )
        response.raise_for_status()
        runs.extend(response.json().get("check_runs", []))
        url = response.links.get("next", {}).get("url", "")
        params = None
    return runs


def fetch_job_log_excerpt(owner_repo: str, job_id: Any, max_chars: int = 4000) -> str:
    """The tail of a GitHub Actions job's raw log (errors are near the end);
    bounded the same way MAX_CANDIDATE_FILE_CHARS bounds file content below.
    """
    url = f"https://api.github.com/repos/{owner_repo}/actions/jobs/{job_id}/logs"
    response = requests.get(url, headers=github_headers(), timeout=60)
    response.raise_for_status()
    return response.text[-max_chars:]


def job_reproduces_on_base(
    owner_repo: str, job_name: str, base_ref: str
) -> bool | None:
    """Whether a same-named job also failed in the base branch's most recent
    completed workflow run -- the deterministic signal for "pre-existing and
    unrelated to this PR" rather than trusting the model's own guess. None
    when no completed base-branch run (or no job of that name in it) could
    be found, which callers must treat as unknown, not as "not reproduced".
    """
    runs_url = f"https://api.github.com/repos/{owner_repo}/actions/runs"
    response = requests.get(
        runs_url, headers=github_headers(),
        params={"branch": base_ref, "status": "completed", "per_page": 5},
        timeout=60,
    )
    response.raise_for_status()
    for run in response.json().get("workflow_runs", []):
        jobs_url = str(run.get("jobs_url", ""))
        if not jobs_url:
            continue
        jobs_response = requests.get(jobs_url, headers=github_headers(), timeout=60)
        jobs_response.raise_for_status()
        for job in jobs_response.json().get("jobs", []):
            if str(job.get("name", "")) == job_name:
                return job.get("conclusion") == "failure"
    return None


# A check whose remedy is a property of the commits rather than of the code.
# The LLM revision pipeline cannot fix one of these -- there is no file to
# edit -- so routing it there burns a call and still leaves the pull request
# blocked. nextcloud/news-android#1716 was blocked this way by "DCO".
_COMMIT_FORM_CHECK_MARKERS = ("dco", "sign-off", "signed-off-by", "signoff")

# A check that blocks the pull request. "failure" is the obvious one and was
# the only one this module looked for, which made it blind to the other:
# GitHub's DCO app reports "action_required", meaning a human has to do
# something before the check can pass. nextcloud/news-android#1716 sat in
# exactly that state -- blocking the merge -- while every CI code path here
# saw no failing check at all.
_BLOCKING_CONCLUSIONS = ("failure", "action_required")


def _check_run_haystack(run: dict[str, Any]) -> str:
    output = run.get("output") or {}
    return " ".join([
        str(run.get("name", "")),
        str(output.get("title", "")),
    ]).lower()


def is_commit_form_check(run: dict[str, Any]) -> bool:
    """Whether this check is about the commits rather than about the code."""
    haystack = _check_run_haystack(run)
    return any(marker in haystack for marker in _COMMIT_FORM_CHECK_MARKERS)


def signoff_check_failure(
    pr: dict[str, Any], errors: list[str] | None = None,
) -> dict[str, Any] | None:
    """The failing check that wants a Signed-off-by trailer, if any.

    Network failures degrade to None rather than failing the whole
    revision; when `errors` is given the failure is appended to it, so a
    caller can tell "no such check" from "the checks could not be read".
    """
    owner_repo = str(
        pr.get("base", {}).get("repo", {}).get("full_name", "")
    ).strip()
    head_sha = str(pr.get("head", {}).get("sha", "")).strip()
    if not owner_repo or not head_sha:
        return None
    try:
        check_runs = fetch_check_runs(owner_repo, head_sha)
    except Exception as exc:  # noqa: BLE001 -- reported through `errors`
        if errors is not None:
            errors.append(f"check runs of {owner_repo}@{head_sha[:12]}: {exc}"[:300])
        return None
    for run in check_runs:
        if run.get("conclusion") not in _BLOCKING_CONCLUSIONS:
            continue
        if is_commit_form_check(run):
            return {
                "check_name": str(run.get("name", "")).strip(),
                "conclusion": str(run.get("conclusion", "")),
                "url": str(run.get("html_url", "")),
                "head_sha": head_sha,
            }
    return None


def configured_author(project_root: Path) -> str:
    """The identity this checkout commits as, as git would write it."""
    name = run_git(project_root, "config", "user.name")
    email = run_git(project_root, "config", "user.email")
    return f"{name} <{email}>"


def ci_failures_from_pr(
    pr: dict[str, Any], errors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Synthesize feedback_from_pr-shaped entries from this PR's failing CI
    checks, so a revision addresses them through the exact same evidence-
    driven file-discovery and edit pipeline as human review feedback --
    no separate schema, no fixed file allowlist. Network failures degrade to
    an empty list rather than failing the whole revision; when `errors` is
    given the failure is appended to it, because an empty list alone reads
    exactly like a pull request whose checks all passed.
    """
    owner_repo = str(pr.get("base", {}).get("repo", {}).get("full_name", "")).strip()
    base_ref = str(pr.get("base", {}).get("ref", "")).strip()
    head_sha = str(pr.get("head", {}).get("sha", "")).strip()
    if not owner_repo or not head_sha:
        return []
    try:
        check_runs = fetch_check_runs(owner_repo, head_sha)
    except Exception as exc:  # noqa: BLE001 -- reported through `errors`
        if errors is not None:
            errors.append(f"check runs of {owner_repo}@{head_sha[:12]}: {exc}"[:300])
        return []
    entries: list[dict[str, Any]] = []
    for run in check_runs:
        if run.get("conclusion") not in _BLOCKING_CONCLUSIONS:
            continue
        # Owned by the deterministic sign-off repair, which fixes it without
        # a model call. Handing it to the revision pipeline would spend one
        # looking for a file to edit that does not exist.
        if is_commit_form_check(run):
            continue
        name = str(run.get("name", "")).strip()
        job_id = run.get("id")
        if not name or not job_id:
            continue
        try:
            log_excerpt = fetch_job_log_excerpt(owner_repo, job_id)
        except Exception:
            log_excerpt = ""
        reproduces: bool | None = None
        if base_ref:
            try:
                reproduces = job_reproduces_on_base(owner_repo, name, base_ref)
            except Exception:
                reproduces = None
        if reproduces is True:
            attribution = (
                f"This same check also fails on the base branch "
                f"{base_ref!r}, so it is very likely pre-existing and "
                "unrelated to this pull request's own changes."
            )
        elif reproduces is False:
            attribution = (
                f"This check passes on the base branch {base_ref!r}, so it "
                "may be caused by this pull request's own changes -- check "
                "those first."
            )
        else:
            attribution = (
                "Whether this check also fails on the base branch could not "
                "be determined."
            )
        body = (
            f'Automated CI check "{name}" failed on this pull request '
            f"({run.get('html_url', '')}). {attribution}\n\nLog excerpt:\n"
            + (log_excerpt or "(log unavailable)")
        )
        entries.append({
            "kind": "ci_failure",
            "author": "github-actions",
            "body": body,
            "path": path_from_log_excerpt(log_excerpt),
            "line": None,
            "diff_hunk": "",
            "url": str(run.get("html_url", "")),
            "check_name": name,
            "reproduces_on_base": reproduces,
        })
    return entries


def post_pr_comment(comments_url: str, body: str) -> None:
    if not comments_url:
        return
    response = requests.post(
        comments_url, headers=github_headers(), json={"body": body}, timeout=30,
    )
    response.raise_for_status()


def update_pr_metadata(pr_api_url: str, update: dict[str, str]) -> None:
    """PATCH a pull request's title and/or body."""
    fields = {key: value for key, value in update.items() if key in {"title", "body"}}
    if not pr_api_url or not fields:
        return
    response = requests.patch(
        pr_api_url, headers=github_headers(), json=fields, timeout=30,
    )
    response.raise_for_status()


def ci_failure_comment_body(record: dict[str, Any]) -> str:
    lines = [
        "This revision also addresses one or more failing CI checks "
        "unrelated to this pull request's original fix:",
        "",
    ]
    for item in record.get("ci_failures", []):
        reproduces = item.get("reproduces_on_base")
        if reproduces is True:
            attribution = "pre-existing -- it also fails on the base branch"
        elif reproduces is False:
            attribution = "not reproduced on the base branch"
        else:
            attribution = "reproduction on the base branch could not be confirmed"
        lines.append(f"- **{item.get('check_name', 'CI check')}**: {attribution}.")
    explanation = str(record.get("llm_revision", {}).get("explanation", "")).strip()
    if explanation:
        lines.extend(["", explanation])
    return "\n".join(lines)


def run_git(project_root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    # push, fetch and clone are bounded and retried once when they stall
    # (utils.network_git); a final timeout fails with return code 124.
    completed = run_git_command(
        ["git", *args],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _branch_commits(project_root: Path, base_sha: str) -> list[str]:
    """The commits this branch adds on top of ``base_sha``, oldest first."""
    if not base_sha:
        return []
    listing = run_git(
        project_root, "rev-list", "--reverse", f"{base_sha}..HEAD"
    )
    return [line.strip() for line in listing.splitlines() if line.strip()]


def unsigned_branch_commits(project_root: Path, base_sha: str) -> list[dict]:
    """Commits on this branch with no Signed-off-by trailer for their author.

    A Developer Certificate of Origin check reads exactly this, and it fails
    the whole branch when any single commit is missing the trailer.
    """
    unsigned = []
    for sha in _branch_commits(project_root, base_sha):
        author = run_git(
            project_root, "log", "-1", "--format=%an <%ae>", sha
        )
        trailers = run_git(
            project_root, "log", "-1",
            "--format=%(trailers:key=Signed-off-by)", sha,
        )
        signed = any(
            line.strip().endswith(author)
            for line in trailers.splitlines() if line.strip()
        )
        if not signed:
            unsigned.append({"sha": sha, "author": author})
    return unsigned


def sign_off_branch_commits(
    project_root: Path, base_sha: str, expected_author: str,
) -> list[str]:
    """Rewrite this branch's own commits to carry their sign-off.

    A sign-off is an attestation made by the commit's author, so this
    refuses outright when any commit on the branch was written by somebody
    else: adding the trailer on their behalf would be asserting something
    they did not. Only the commits above ``base_sha`` are rewritten, so the
    base branch's history is never touched.

    Returns the commits that were missing it, or [] when there was nothing
    to do.
    """
    unsigned = unsigned_branch_commits(project_root, base_sha)
    if not unsigned:
        return []
    authors = {
        run_git(project_root, "log", "-1", "--format=%an <%ae>", sha)
        for sha in _branch_commits(project_root, base_sha)
    }
    foreign = sorted(author for author in authors if author != expected_author)
    if foreign:
        raise RuntimeError(
            "Refusing to sign off commits written by "
            + ", ".join(foreign)
            + f": a Signed-off-by is an attestation by the commit's own "
            f"author, and this pipeline may only make it for "
            f"{expected_author}"
        )
    run_git(project_root, "rebase", "--signoff", base_sha)
    still_unsigned = unsigned_branch_commits(project_root, base_sha)
    if still_unsigned:
        raise RuntimeError(
            "Sign-off rebase left "
            f"{len(still_unsigned)} commit(s) without the trailer"
        )
    return [item["sha"] for item in unsigned]


def token_git_env(token: str) -> dict[str, str]:
    """Environment for a git push authenticated by ``token``, never stored on disk.

    The token travels as an extra HTTP header for github.com only, so it is
    neither written into the remote URL nor into the checkout's config.
    """
    credential = base64.b64encode(
        f"x-access-token:{token}".encode("utf-8")
    ).decode("ascii")
    git_env = os.environ.copy()
    git_env["GIT_TERMINAL_PROMPT"] = "0"
    git_env["GIT_CONFIG_COUNT"] = "1"
    git_env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
    git_env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {credential}"
    return git_env


def push_branch(project_root: Path, branch: str, remote: str = "origin") -> str:
    """Push the checkout's ``branch`` (no force) and return the pushed commit.

    A plain push: when the remote branch moved since this checkout last saw
    it, git refuses, and nothing anyone else pushed is overwritten. ``remote``
    may be the pull request's head repository URL, so a checkout whose
    "origin" is another repository does not receive the push.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required to push")
    current_branch = run_git(project_root, "branch", "--show-current")
    if current_branch != branch:
        raise RuntimeError(
            f"Clone is on branch {current_branch!r}, expected PR branch {branch!r}"
        )
    run_git(project_root, "push", remote or "origin", f"{branch}:refs/heads/{branch}",
            env=token_git_env(token))
    return run_git(project_root, "rev-parse", "HEAD")


def force_push_branch(project_root: Path, branch: str) -> str:
    """Publish a rewritten branch, refusing to clobber an unseen update.

    --force-with-lease is the whole safety here: rewriting history is only
    acceptable while the remote still holds what this checkout last saw.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required with --publish")
    git_env = token_git_env(token)
    run_git(
        project_root, "push", "--force-with-lease", "origin", branch,
        env=git_env,
    )
    return run_git(project_root, "rev-parse", "HEAD")


def publish_revision(
    project_root: Path,
    sources: list[Path],
    branch: str,
    commit_message: str,
) -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required with --publish")
    current_branch = run_git(project_root, "branch", "--show-current")
    if current_branch != branch:
        raise RuntimeError(
            f"Clone is on branch {current_branch!r}, expected PR branch {branch!r}"
        )
    relative_sources = [str(source.relative_to(project_root)) for source in sources]
    run_git(project_root, "add", "--", *relative_sources)
    staged = set(
        run_git(project_root, "diff", "--cached", "--name-only")
        .replace("\\", "/").splitlines()
    )
    publishable = [
        relative for relative in relative_sources
        if relative.replace("\\", "/") in staged
    ]
    # A listed file can legitimately end with no net change:
    # repair_style_violations runs the project's own ktlintFormat, which can
    # undo a reformatting the model proposed and restore the file
    # byte-for-byte. Requiring *every* listed file to be staged turned that
    # into a publish failure even though the substantive change -- here,
    # moving misplaced tests into their own file -- was staged and correct.
    # Guard the real hazard instead: refuse only when nothing landed at all.
    if not publishable:
        raise RuntimeError(
            "No revised change is staged for any of: "
            + ", ".join(relative_sources)
        )
    unchanged = [
        relative for relative in relative_sources if relative not in publishable
    ]
    if unchanged:
        logging.getLogger("fix_prs").info(
            "Publishing %d of %d revised files; these ended with no net change "
            "and are excluded from the commit: %s",
            len(publishable), len(relative_sources), ", ".join(unchanged),
        )
    # --signoff: a revision pushed onto an existing pull request is a new
    # commit on that branch, and a DCO check fails the branch when any one
    # of its commits lacks the trailer.
    run_git(
        project_root, "commit", "--signoff", "-m", commit_message,
        "--", *publishable,
    )
    commit_hash = run_git(project_root, "rev-parse", "HEAD")
    git_env = token_git_env(token)
    run_git(project_root, "push", "origin", branch, env=git_env)
    return commit_hash
