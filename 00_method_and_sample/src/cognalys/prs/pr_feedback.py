#!/usr/bin/env python3
"""Revise methods in open PRs using maintainer feedback and OpenAI."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from cognalys.workspace import workspace_root
from cognalys.utils.edit_text import read_source, write_source
from cognalys.utils.pr_text import source_names
from cognalys.llm.pipeline_common import prompt_json

ROOT = workspace_root()

from cognalys.extraction.extract_methods_single_csv import MethodExtractor
from cognalys.solutions.coordinated_edits import (
    EDIT_KINDS,
    apply_edits_to_sources,

)
from cognalys.prs.pr_github import (
    ci_failure_comment_body,
    ci_failures_from_pr,
    configured_author,
    fetch_current_pr_state,
    feedback_from_pr,
    force_push_branch,

    post_pr_comment,
    publish_revision,
    run_git,
    sign_off_branch_commits,
    signoff_check_failure,
    update_pr_metadata,
)
from cognalys.prs.impact_class import published_impact_class
from cognalys.prs.pr_outcomes import record_pr_outcome
from cognalys.solutions.project_conventions import (
    describe_text_convention, user_visible_text_convention,
)
from cognalys.prs.pr_validation import (

    repair_style_violations,
    run_compile_validation,
    run_style_validation,
)
from cognalys.llm.llm_providers import remote_provider
from cognalys.llm.generate_error_fixes import (
    OpenAIProvider,
    ResponseParser,
    unrelated_test_placements,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use OpenAI to address feedback on already published PRs."
    )
    parser.add_argument("--pull-requests-dir", required=True)
    parser.add_argument("--projects-dir", required=True)
    parser.add_argument(
        "--worktrees-dir",
        default="outputs/finding_worktrees",
        help=(
            "Root for isolated per-PR checkouts (see prepare_pr_checkout): "
            "reuses the finding worktree the PR was created from when still "
            "present, or creates one here next to it. Never mutates "
            "--projects-dir's shared checkout, which other pipeline steps "
            "expect to stay on the repository's default branch."
        ),
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--project", default="")
    parser.add_argument("--model", default="gpt-6.1-terra")
    parser.add_argument("--endpoint", default="https://api.openai.com/v1")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-output-tokens", type=int, default=32768)
    parser.add_argument(
        "--prompt-log-dir", type=Path,
        help="Directory for JSON records of prompts sent to OpenAI.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh"),
        default="high",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--author-guidance", default="",
        help="Your decision on a choice the feedback leaves open, passed to the revision as is.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Commit the revised method and push the existing PR branch, for "
             "the findings named by --approve-revision only.",
    )
    parser.add_argument(
        "--approve-revision",
        action="append",
        default=[],
        metavar="FINDING_ID",
        help="With --publish: push this finding's revalidated revision, update "
             "its pull request's text and post its reply (repeatable). Every "
             "other revision is reported with what it would send.",
    )
    parser.add_argument(
        "--commit-message",
        default="Address pull request feedback for {method_name}",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip repository-provided ktlintCheck validation.",
    )
    parser.add_argument(
        "--build-script",
        type=Path,
        default=None,
        help="Program used to compile the revised project; the package's own "
             "build stage by default.",
    )
    parser.add_argument(
        "--build-variant", choices=("debug", "release"), default="debug",
    )
    parser.add_argument(
        "--build-timeout", type=int, default=1800,
        help="Gradle build timeout in seconds (separate from --timeout, "
             "which is for the OpenAI request).",
    )
    parser.add_argument(
        "--skip-compile-validation",
        action="store_true",
        help=(
            "Skip actually compiling the revised project. ktlintCheck alone "
            "(see --skip-validation) only checks formatting and would pass "
            "on a revision that does not compile at all; do not disable "
            "this for a --publish run without a specific reason."
        ),
    )
    parser.add_argument(
        "--skip-pr-state-check",
        action="store_true",
        help=(
            "Skip re-fetching the pull request's current state before "
            "revising it. Without this check, a PR closed or merged after "
            "its local snapshot was written would still be revised and, "
            "with --publish, pushed to."
        ),
    )
    parser.add_argument(
        "--self-audit",
        action="store_true",
        help=(
            "When a PR has no maintainer feedback, re-check the published "
            "patch against the pipeline's current deterministic correctness "
            "gates and revise anything they reject. Lets a gate added after "
            "publication repair the pull requests that predate it, without "
            "waiting for someone to notice and comment."
        ),
    )
    parser.add_argument(
        "--fix-ci-failures",
        action="store_true",
        help=(
            "When a PR has no maintainer feedback, also check its own CI "
            "checks and revise to address any that failed. Only relevant "
            "for the revision step (see --revalidate for compiling and "
            "publishing); off by default because it lets the model touch "
            "files -- build scripts, dependency versions -- outside the "
            "original finding's file, gated by the same compile/style "
            "validation and --publish approval as any other revision."
        ),
    )
    parser.add_argument(
        "--revalidate",
        action="store_true",
        help=(
            "Run as the explicit revalidation_fix_prs step instead of the "
            "fix_prs revision step: read --output as an existing report, "
            "compile and style-check (and, with --publish, commit and push) "
            "every APPLIED entry, then overwrite --output with the result. "
            "Does not call OpenAI. Mirrors validation being its own step "
            "after solutions in the main pipeline, rather than compiling "
            "and publishing being buried inside the same run that revises."
        ),
    )
    return parser.parse_args()


def project_name_from_metadata(metadata: dict[str, Any]) -> str:
    """The apk/project identifier a PR metadata record belongs to.

    metadata["repository"] is a finding worktree path built by
    WorktreeManager as <worktrees_dir>/<project>/<finding_id>/<revision>
    (see src/solutions/worktree_manager.py's path_prefix construction), so
    its last path component is the solution revision, not the project.
    Naively taking Path(...).name there silently extracts the wrong value,
    making --project match nothing and every record get filtered out.
    Anchoring on finding_id (never given a retry suffix, unlike revision)
    finds the project component regardless of the worktrees directory name.
    """
    repository = str(metadata.get("repository", ""))
    parts = Path(repository).parts
    finding_id = str(metadata.get("finding_id", ""))
    if finding_id:
        for index, part in enumerate(parts):
            if part == finding_id and index > 0:
                return parts[index - 1]
    return Path(repository).name


def resolve_source_method_name(
    method_name: str, csv_class: str, source: Path,
) -> str:
    """Map a bytecode-level method name to what actually appears in source.

    "<init>" is the JVM's name for every constructor; source code is never
    spelled that way, so a literal search for "<init>(" can never match.
    This mirrors extract_methods_single_csv.py's own extract_methods() loop,
    which resolves "<init>" to the declaring class's simple name (or, for a
    Kotlin top-level-function wrapper class whose simple name ends in "Kt",
    the source file's own name) before searching for a declaration.
    """
    if method_name != "<init>":
        return method_name
    simple_name = csv_class.split("$")[-1].split(".")[-1]
    if simple_name.endswith("Kt"):
        return source.stem
    return simple_name



# A dependency-resolution or plugin-configuration failure (like a Maven
# Central 403 on a pinned ktlint version) names no source file at all -- only
# offered as *additional*, existence-checked candidates in build_file_
# candidates below, never as a hard "these are the only files" restriction.
_BUILD_FILE_FALLBACK_CANDIDATES = (
    "app/build.gradle.kts", "build.gradle.kts",
    "app/build.gradle", "build.gradle",
    "settings.gradle.kts", "settings.gradle",
    "gradle/libs.versions.toml", "gradle.properties",
)


def self_audit_findings(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-check a published patch against the pipeline's current gates.

    Maintainers do not report every defect, and some are invisible unless you
    read the diff: PR #1210 on spacecowboy/Feeder shipped TLS protocol
    assertions appended to DateFormattingTest.kt and nobody commented. When a
    deterministic gate is strengthened afterwards, replaying it over the
    already-published edit set turns the pipeline's own judgement into the
    same feedback shape a reviewer would have produced, so the existing
    revision flow can repair the PR without waiting for a human.
    """
    edits = metadata.get("coordinated_edits") or []
    if not isinstance(edits, list) or not edits:
        return []
    error = {
        "source_file_path": str(metadata.get("source_file", "")),
        "method_name": str(metadata.get("method_name", "")),
        "case_migration_policy": {},
        "repository_path": "",
    }
    issues = unrelated_test_placements(error, edits)

    entries: list[dict[str, Any]] = []
    for issue in issues:
        details = issue.get("details", [])
        path = str(details[0]).split(" does not cover ")[0] if details else ""
        entries.append({
            "kind": "self_audit",
            "author": "cognalys-self-audit",
            "body": (
                "An automated re-check of this pull request against the "
                f"pipeline's current correctness gates reported {issue['code']}."
                "\n\n" + "\n".join(f"- {item}" for item in details)
            ),
            "path": path,
            "line": None,
            "diff_hunk": "",
            "url": "",
            "audit_code": issue["code"],
        })
    return entries


def repair_missing_signoff(
    project_root: Path, branch: str, head_sha: str, commits: int, logger,
) -> dict:
    """Add the Signed-off-by trailer this branch's own commits are missing.

    Deterministic, with no model involved: the remedy a DCO check prescribes
    is exactly `git rebase --signoff` over the branch's own commits followed
    by a force push, and the pipeline can run it verbatim.

    Rewriting published history is only safe under every one of these, so
    each is checked rather than assumed:

    * the local checkout is on the pull request's branch and its HEAD is the
      exact commit the pull request points at -- otherwise this would be
      rewriting something other than what is published;
    * only the commits the pull request itself adds are rewritten, counted
      from GitHub's own commit total, so the base branch is never touched;
    * every one of those commits was authored by this checkout's identity,
      because a sign-off is an attestation by the commit's author and making
      it for somebody else would assert what they did not (enforced inside
      sign_off_branch_commits, which refuses);
    * the push uses --force-with-lease, so a branch someone else advanced in
      the meantime is not clobbered.
    """
    if not branch or not head_sha:
        return {"status": "SKIPPED", "detail": "pull request has no branch"}
    if not isinstance(commits, int) or commits < 1:
        return {
            "status": "SKIPPED",
            "detail": f"unusable commit count from GitHub: {commits!r}",
        }
    local_head = run_git(project_root, "rev-parse", "HEAD")
    if local_head != head_sha:
        return {
            "status": "SKIPPED",
            "detail": (
                f"local HEAD {local_head[:12]} is not the published head "
                f"{head_sha[:12]}; refusing to rewrite history this checkout "
                "does not match"
            ),
        }
    signed = sign_off_branch_commits(
        project_root, f"HEAD~{commits}", configured_author(project_root),
    )
    if not signed:
        return {
            "status": "NOT_NEEDED",
            "detail": "every commit on the branch already carries a sign-off",
        }
    pushed = force_push_branch(project_root, branch)
    logger.info(
        "Signed off %d commit(s) on %s and force-pushed %s",
        len(signed), branch, pushed[:12],
    )
    return {
        "status": "PUBLISHED",
        "commits_signed": len(signed),
        "previous_head": head_sha,
        "commit": pushed,
    }


def revision_finding_id(record: dict[str, Any], pull_requests_dir) -> str:
    """The finding id of a fix_prs record, for --approve-revision.

    Recorded by run_revision since 2026-10-30; an earlier record is matched
    to its pull request's *_pr_created.json by URL and read from the
    *_pr.json beside it. "" when neither says.
    """
    finding = str(record.get("finding_id", "") or "").strip()
    if finding or not pull_requests_dir:
        return finding
    url = str(record.get("pull_request", "") or "").strip()
    if not url:
        return ""
    for created in sorted(Path(pull_requests_dir).glob("*_pr_created.json")):
        try:
            published = json.loads(created.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(published, dict) or str(published.get("html_url", "")).strip() != url:
            continue
        generated = created.with_name(created.name.removesuffix("_pr_created.json") + "_pr.json")
        try:
            return str(json.loads(generated.read_text(encoding="utf-8"))
                       .get("finding_id", "") or "").strip()
        except (OSError, ValueError, AttributeError):
            return ""
    return ""


def revision_approval(record: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str]:
    """Whether this record's revision may reach GitHub, and why not.

    --publish alone pushed every revalidated revision of the report, rewrote
    its pull request's text and posted its reply (batch6 audit, 2026-10-30);
    a write to a published pull request needs that finding named.
    """
    approved = {str(item).strip() for item in getattr(args, "approve_revision", []) or []
                if str(item).strip()}
    finding = revision_finding_id(record, getattr(args, "pull_requests_dir", ""))
    if finding and finding in approved:
        return True, ""
    return False, (f"no --approve-revision {finding or '<finding id>'} for "
                   f"{record.get('pull_request') or 'this pull request'}")


def build_file_candidates(
    project_root: Path, ci_failures: list[dict[str, Any]], already_covered: set[str],
) -> list[str]:
    """Extra build-file candidates for a CI failure whose log named no file.

    Only offered -- never forced -- and only when at least one ci_failure
    entry has no path of its own; existence is checked against the real
    checkout so a repo without, say, a version catalog never sees it listed.
    """
    if not any(not str(item.get("path", "")).strip() for item in ci_failures):
        return []
    return [
        candidate for candidate in _BUILD_FILE_FALLBACK_CANDIDATES
        if candidate not in already_covered
        and (project_root / candidate).is_file()
    ]


MAX_CANDIDATE_FILES = 6
MAX_CANDIDATE_FILE_CHARS = 60_000


def gather_candidate_files(
    project_root: Path,
    primary_source: Path,
    feedback: list[dict[str, Any]],
    extra_paths: list[str] | None = None,
) -> dict[str, str]:
    """Read every file the review feedback plausibly needs edited.

    Maintainer feedback on a single-method PR routinely names concerns in
    OTHER files -- a helper class, a misplaced test -- that the originally
    reported finding's own method cannot address alone. Scoping revision to
    only that one file silently drops such feedback every time. An inline
    review comment (kind "inline_review" from feedback_from_pr) carries the
    exact repository-relative path it was left on; a concern mentioned only
    in a review's free-text summary has no such structured path, so callers
    should pass discover_referenced_class_files' hits as extra_paths.

    The primary_source is always included, even past MAX_CANDIDATE_FILE_CHARS
    -- it is the one file this call is guaranteed to need, and dropping it
    for being large would silently make the whole revision impossible rather
    than just token-expensive.
    """
    primary = str(primary_source.relative_to(project_root))
    paths: list[str] = [primary]
    for item in feedback:
        candidate = str(item.get("path", "")).strip()
        if candidate and candidate not in paths:
            paths.append(candidate)
    for candidate in extra_paths or []:
        if candidate and candidate not in paths:
            paths.append(candidate)
    files: dict[str, str] = {}
    for relative in paths[:MAX_CANDIDATE_FILES]:
        full = project_root / relative
        if not full.is_file():
            continue
        text = read_source(full)
        if relative != primary and len(text) > MAX_CANDIDATE_FILE_CHARS:
            continue
        files[relative] = text
    return files


def repository_style_context(project_root: Path) -> str:
    snippets: list[str] = []
    for name in (
        ".editorconfig", "build.gradle", "build.gradle.kts",
        "detekt.yml", "ktlint.yml",
    ):
        for path in project_root.rglob(name):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            relevant = [
                line for line in text.splitlines()
                if any(word in line.lower() for word in (
                    "ktlint", "indent", "continuation_indent", "max_line_length"
                ))
            ]
            if relevant:
                snippets.append(f"{path.relative_to(project_root)}:\n" + "\n".join(relevant[:40]))
    return "\n\n".join(snippets)


_WALK_SKIPPED_PARTS = {".git", ".gradle", "build", "dist", "generated", "node_modules"}


_SETTINGS_REQUEST = re.compile(
    r"\b(settings?|toggle|preferences?|option|switch)\b", re.IGNORECASE)
_PREFERENCE_READ = re.compile(r"getDefaultSharedPreferences\s*\(")


def settings_files(project_root: Path, feedback: list[dict[str, Any]],
                   already_covered: set[str]) -> list[str]:
    """Where the app declares its settings and reads one, when feedback asks for one.

    HomeApp #114: "you can put it behind a toggle in the settings". The
    revision saw the WebView client and strings.xml only, and answered that
    no safe toggle could be written without the settings screen. A setting
    needs three places -- the screen (a PreferenceScreen XML), the text, and
    one existing read to copy -- so the first and the last are added here.
    """
    if not any(_SETTINGS_REQUEST.search(str(item.get("body", ""))) for item in feedback):
        return []
    screens, readers = [], []
    for path in sorted(project_root.rglob("*")):
        if not path.is_file() or _WALK_SKIPPED_PARTS & set(path.parts):
            continue
        relative = str(path.relative_to(project_root))
        if relative in already_covered:
            continue
        if path.suffix == ".xml" and "/res/xml/" in f"/{relative}":
            text = path.read_text(encoding="utf-8", errors="replace")
            if "PreferenceScreen" in text:
                screens.append((0 if "SwitchPreference" in text else 1, relative))
        elif path.suffix in {".kt", ".java"} and not readers:
            if _PREFERENCE_READ.search(path.read_text(encoding="utf-8", errors="replace")):
                readers.append(relative)
    # A screen that already holds switches is where a new one belongs; taking
    # the first screens by name gave HomeApp's About page, not pref_general.
    return [relative for _, relative in sorted(screens)[:3]] + readers


def discover_referenced_class_files(
    project_root: Path,
    feedback: list[dict[str, Any]],
    already_covered: set[str],
    max_files: int = 3,
) -> list[str]:
    """Find files declaring a class named in feedback as ClassName.member(...).

    A concern raised only in a review's free-text summary -- e.g. "X.y(...)
    should throw Z" naming a class with no inline comment on it -- has no
    structured path the way gather_candidate_files' feedback-item paths do.
    Requiring the more specific "ClassName.member(" shape rules out most
    incidental capitalized words; matching it against the filename (rather
    than reading every candidate file's content, which is the repository
    walk repository_symbol_evidence already does, and is too slow to repeat
    here) covers the common case directly, since Java requires a public
    top-level class's filename to match its name exactly and Kotlin
    conventionally follows the same rule. A class referenced only as a
    private nested type (not matching this convention) is expected to
    already be covered some other way -- most often because it lives in the
    primary_source file gather_candidate_files always includes.

    Walks the tree with directory-level pruning (os.walk, deleting skipped
    directory names before os.walk descends into them) rather than
    Path.glob("**/..."), which enumerates every entry under .git's object
    database before any filter can exclude it -- on this project that alone
    made a single glob call take minutes.
    """
    bodies = "\n".join(str(item.get("body", "")) for item in feedback)
    class_names = set(re.findall(
        r"\b([A-Z][A-Za-z0-9_]{3,})\.[a-zA-Z_]\w*\s*\(", bodies
    ))
    if not class_names:
        return []
    wanted = {f"{name}.java" for name in class_names} | {
        f"{name}.kt" for name in class_names
    }
    by_filename: dict[str, Path] = {}
    for directory, names, filenames in os.walk(project_root):
        names[:] = [name for name in names if name not in _WALK_SKIPPED_PARTS]
        for filename in filenames:
            if filename in wanted and filename not in by_filename:
                by_filename[filename] = Path(directory) / filename
        if len(by_filename) >= len(wanted):
            break
    found: list[str] = []
    for name in sorted(class_names):
        for filename in (f"{name}.java", f"{name}.kt"):
            path = by_filename.get(filename)
            if path is None:
                continue
            relative = str(path.relative_to(project_root))
            if relative not in already_covered and relative not in found:
                found.append(relative)
            break
        if len(found) >= max_files:
            break
    return found


_TYPE_DECLARATION = re.compile(
    r"\b(?:class|interface|enum|record|object)\s+([A-Z][A-Za-z0-9_]*)\b"
)
_USAGE_SOURCE_SUFFIXES = {".java", ".kt"}


def discover_type_usage_files(
    project_root: Path,
    feedback: list[dict[str, Any]],
    covered_files: dict[str, str],
    max_files: int = 4,
) -> list[str]:
    """Find files that use a type the feedback names and a covered file declares.

    A review asking to remove, rename or replace a type -- "remove
    TLSSocketFactory and its usage" -- cannot be carried out on the declaring
    file alone: every file that still names the type stops compiling. Before
    this, those call sites reached the model only as grep lines in
    repository_symbol_evidence, not as editable files, so the only honest
    answer was NO_SAFE_FIX.

    Scoped twice so that an incidental mention never widens the edit set: the
    type must be declared in a file already being revised (covered_files), and
    its simple name must appear in the feedback text in any form -- a bare
    word, backticks, or ClassName.member(). A type declared elsewhere is
    discover_referenced_class_files' concern.
    """
    bodies = "\n".join(str(item.get("body", "")) for item in feedback)
    declared = {
        name
        for text in covered_files.values()
        for name in _TYPE_DECLARATION.findall(text)
    }
    named = sorted(
        name for name in declared
        if re.search(rf"\b{re.escape(name)}\b", bodies)
    )
    if not named:
        return []
    pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, named)) + r")\b")
    found: list[str] = []
    for directory, names, filenames in os.walk(project_root):
        names[:] = sorted(
            name for name in names if name not in _WALK_SKIPPED_PARTS
        )
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.suffix not in _USAGE_SOURCE_SUFFIXES:
                continue
            relative = path.relative_to(project_root).as_posix()
            if relative in covered_files or relative in found:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pattern.search(text):
                found.append(relative)
                if len(found) >= max_files:
                    return found
    return found


def repository_symbol_evidence(
    project_root: Path,
    feedback: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Find repository evidence for APIs named in review feedback."""
    bodies = "\n".join(str(item.get("body", "")) for item in feedback)
    symbols = sorted(set(re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", bodies)))
    ignored = {
        "AI", "API", "CAUTION", "CURRENT", "FILE", "FIX", "PR", "TLS",
    }
    symbols = [symbol for symbol in symbols if symbol not in ignored][:12]
    evidence: list[dict[str, str]] = []
    candidates = [
        path for path in project_root.rglob("*")
        if path.is_file()
        and path.suffix in {".kt", ".java", ".gradle", ".kts", ".toml"}
        and not any(part in {".git", ".gradle", "build"} for part in path.parts)
    ]
    for symbol in symbols:
        matches = 0
        pattern = re.compile(rf"\b{re.escape(symbol)}\b", re.IGNORECASE)
        for path in candidates:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line_number, line in enumerate(text.splitlines(), 1):
                if not pattern.search(line):
                    continue
                evidence.append(
                    {
                        "symbol": symbol,
                        "path": str(path.relative_to(project_root)),
                        "line": str(line_number),
                        "evidence": line.strip(),
                    }
                )
                matches += 1
                if matches >= 8:
                    break
            if matches >= 8:
                break
    return evidence




# Wording that presents the contribution itself as machine-produced. Naming
# the analysis tool that found the issue is fine and happens elsewhere; these
# phrases describe authorship, which a revised title or description must not.
_AUTOMATION_DISCLOSURE = re.compile(
    r"\b(?:"
    r"automated(?:ly)?\s+(?:generated|created|produced|pipeline|fix|patch|pull request|PR|contribution|tool)"
    r"|auto-?generated|(?:generated|written|produced|created)\s+(?:by|with|using)\s+(?:an?\s+)?"
    r"(?:AI|LLM|GPT|ChatGPT|Claude|language model|pipeline|bot|automation)"
    r"|(?:AI|LLM)[- ](?:generated|assisted|written)|large language model"
    r")\b",
    re.IGNORECASE,
)
MAX_PR_TITLE_CHARS = 120


def pr_metadata_update(
    revision: dict[str, Any], pr: dict[str, Any],
) -> tuple[dict[str, str], list[str]]:
    """Title/body changes a revision asks for, after deterministic checks.

    Returns (update, issues): update holds only the fields that are non-empty
    and differ from the pull request's current values; a field failing a check
    is left out of update and explained in issues instead of being published.
    """
    update: dict[str, str] = {}
    issues: list[str] = []
    current = {"title": str(pr.get("title", "")), "body": str(pr.get("body", "") or "")}
    for field in ("title", "body"):
        value = str(revision.get(f"pr_{field}", "") or "").strip()
        if not value or value == current[field].strip():
            continue
        if field == "title" and ("\n" in value or len(value) > MAX_PR_TITLE_CHARS):
            issues.append(
                f"pr_title rejected: must be one line of at most "
                f"{MAX_PR_TITLE_CHARS} characters"
            )
            continue
        disclosure = _AUTOMATION_DISCLOSURE.search(value)
        if disclosure:
            issues.append(
                f"pr_{field} rejected: describes the contribution as automated "
                f"({disclosure.group(0)!r})"
            )
            continue
        update[field] = value
    return update, issues


MAX_MAINTAINER_REPLY_CHARS = 500
_REPLY_MARKUP = re.compile(r"^\s*(?:#|[-*+]\s|\d+\.\s|```|\|)", re.MULTILINE)


def maintainer_reply(revision: dict[str, Any]) -> tuple[str, str]:
    """The reply to post after a revision is pushed, and why one was refused.

    The revision used to be pushed without a word: Auto-Shine's maintainer
    asked for a toast, and the only answer would have been a new commit. A
    reply is short and plain -- Feeder's maintainer asked for a human
    summary instead of "that wall of ai text" -- so anything longer, any
    Markdown structure, or any self-description as automated is refused and
    nothing is posted.
    """
    text = str(revision.get("maintainer_reply", "") or "").strip()
    if not text:
        return "", ""
    if len(text) > MAX_MAINTAINER_REPLY_CHARS:
        return "", f"maintainer_reply rejected: longer than {MAX_MAINTAINER_REPLY_CHARS} characters"
    if _REPLY_MARKUP.search(text):
        return "", "maintainer_reply rejected: headings, lists, tables and code blocks are not a reply"
    disclosure = _AUTOMATION_DISCLOSURE.search(text)
    if disclosure:
        return "", (f"maintainer_reply rejected: describes the contribution as automated "
                    f"({disclosure.group(0)!r})")
    return text, ""


def log_result_summary(
    logger: logging.Logger, results: list[dict[str, Any]], project: str = "",
) -> None:
    """One line per pull request, so a step that revised nothing says so.

    The report file holds everything, but the terminal used to print nothing
    at all, and a NO_SAFE_FIX looked exactly like a successful run.
    """
    for record in results:
        if project and record.get("project") != project:
            continue
        status = str(record.get("status", "UNKNOWN"))
        detail = record.get("error") or ""
        if status == "NO_SAFE_FIX":
            detail = str(record.get("llm_revision", {}).get("explanation", ""))
        elif record.get("changed_files"):
            detail = "files: " + ", ".join(record["changed_files"])
        logger.info(
            "%s %s: %s%s", record.get("project", ""),
            record.get("pull_request", ""), status,
            f" -- {detail}" if detail else "",
        )
        for item in record.get("llm_revision", {}).get("unresolved_feedback", []) or []:
            logger.info("  unresolved: %s", item)
        for item in record.get("pr_metadata_rejected", []) or []:
            logger.warning("  %s", item)


def governed_types(spec: str, specifications_dir: Path | None = None) -> list[str]:
    """Simple names of the types the finding's specification governs.

    Read from the specification's own applies_to_types, so any detector's
    specification that records them is covered and none is assumed from its
    name.
    """
    from cognalys.workspace import library_data

    path = Path(specifications_dir or library_data("specifications")) / f"{spec}.json"
    try:
        specification = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names = [str(item).rsplit(".", 1)[-1] for item in specification.get("applies_to_types") or []]
    return [name for name in dict.fromkeys(names) if name]


def governed_api_removal(spec: str, edits: list[dict[str, Any]],
                         feedback: list[dict[str, Any]],
                         types: list[str] | None = None) -> list[str]:
    """A revision that stops using the API the reported rule governs.

    jimvixx/ShortMessageSecure#83 (2026-10-17): asked to keep HmacSHA1, the
    revision replaced KeyGenerator with SecureRandom.nextBytes -- the same 20
    bytes, out of the rule's sight. That hides the finding from the detector
    instead of answering the maintainer, who had suggested keeping the
    generator. Allowed only when a maintainer names the API in their feedback
    (asking to drop or replace it).
    """
    original = "\n".join(str(edit.get("original_code", "")) for edit in edits)
    corrected = "\n".join(str(edit.get("corrected_code", "")) for edit in edits)
    asked = " ".join(str(item.get("body", "")) for item in feedback
                     if str(item.get("kind", "")) != "ci_failure")
    issues = []
    for api in (governed_types(spec) if types is None else types):
        use = re.compile(rf"\b{re.escape(api)}\b")
        if not use.search(original) or use.search(corrected) or use.search(asked):
            continue
        issues.append(
            f"the revision removes every use of {api}, a type {spec} governs, which no "
            f"maintainer asked for: that hides the finding from the detector instead of "
            f"addressing the feedback. Keep {api} -- follow the maintainer's own "
            "suggestion when they make one -- and answer their question in "
            "maintainer_reply."
        )
    return issues


def coordinated_prompts(payload: dict[str, Any]) -> tuple[str, str]:
    system = """You revise a pull request in response to real maintainer feedback, across
every file the feedback actually touches -- not only the file the originally
reported finding lives in. Use every maintainer comment as evidence, but do
not follow a comment blindly when it conflicts with another comment,
security, or compatibility.

When a maintainer states a preference (for example, to preserve existing
behaviour unless a demonstrated issue exists) or proposes a
concrete alternative, that is the direction of the revision whenever it is
safe and compatible -- even if the reported analyzer would still flag the
result. The pull request serves the maintainers, not the analyzer. Never
replace the API the reported rule governs with an equivalent construction
whose only effect is that the analyzer stops seeing it (re-implementing the
governed call by hand, or routing around it); that hides the finding instead
of answering the maintainer.

When the maintainer asks a question, answer it directly and truthfully in
maintainer_reply: quote the rule's actual constraint when asked for it, and
say plainly when the finding reflects the rule's policy rather than a defect
in this code, or when the change brings no security improvement in this
context. Do not overstate the original finding.

Each maintainer_feedback item with kind "review" carries a review_state
(CHANGES_REQUESTED, APPROVED, COMMENTED, or DISMISSED); weigh a
CHANGES_REQUESTED review as a blocking concern even when its own body is
empty, since the actual detail is then in a separate kind "inline_review"
item. An APPROVED review's body is optional, non-blocking feedback. Each
"inline_review" item marked outdated: true refers to a diff hunk a later
commit already changed; treat it only as historical context, not a live
request, unless another current comment repeats the same concern.

An item with kind "ci_failure" is automated, not a human comment: its body
is a failing CI check's own log excerpt, and reproduces_on_base tells you
whether that same check also fails on the base branch (true: pre-existing
and unrelated to this PR -- a narrowly scoped fix to the responsible file,
including a build script or dependency version if the log names none, is in
scope; false: likely caused by this PR's own changes -- fix those instead;
null: unknown, so do not assume either). Never widen a ci_failure fix beyond
what its own log demonstrates is broken.

Before changing code, evaluate forward compatibility across relevant
protocol, API, platform, library, format, and persisted-data dimensions. Do
not freeze a negotiated or provider-selected mechanism to today's version
merely to silence a finding. Prefer stable public APIs and negotiation when
supported by the evidence.

current_files supplies the exact, current content of every file feedback
references; only edit a source_file_path present there, with one exception:
edit_kind=create adds a file that does not exist yet, and its
source_file_path is therefore a new path, its original_code exactly empty,
and its corrected_code the complete content of the new file. This tool
still cannot rename or move a file in place, so a required rename belongs in
unresolved_feedback -- but relocating code is expressed as create for the new
file plus delete for the block being removed, and moving misplaced code is
always preferable to deleting it and losing the behaviour it covered. Each
non-create edit's original_code must be an exact, unique substring of that
file's current content in current_files; corrected_code replaces it, except
for edit_kind=delete, whose corrected_code must be exactly empty. Empty
corrected_code and edit_kind=delete always go together: if you are removing a
block, edit_kind must be exactly "delete" no matter what the block contains --
a removed test block is still edit_kind=delete, not "test". Removing a whole
file -- e.g. a reviewer asks to drop a class and its usages -- is
edit_kind=delete_file, whose original_code is that file's entire current
content and whose corrected_code is exactly empty; do not also edit a file you
delete, and edit every other file in current_files that still references what
you remove, since the revision is compiled before it is published.

Do not reword an existing user-visible string the feedback does not ask about:
its translations would no longer match it. Add new strings instead. Files
this pull request itself added are in pull_request_files; a test there that
asserts behaviour the revision changes must be updated or deleted, never
left asserting the old behaviour. author_guidance, when present, is the
pull request author's own decision on a choice the feedback left open
(for example how broad a requested option is); follow it.

A reviewer may also ask for a clearer pull request title or description, and
a revision that changes what the pull request does needs one.
pr_title and pr_body replace them when non-empty; leave both empty when no
feedback asks for it and the change keeps its description true. Write them
for the maintainers: what the problem was, its practical impact, and what the
revision changes, in the language of the existing description, as plain
paragraphs -- no headings, tables, checklists or "Changes:" lists. Never describe the contribution as automated or
generated by a tool, model, or pipeline. Follow the repository conventions
visible in repository_style_context. Do not invent APIs; use
repository_symbol_evidence to verify a review-suggested API exists, and list
any import it needs in that edit's required_imports. If no safe, coherent
revision addresses the live feedback, return NO_SAFE_FIX with an empty edits
array and explain what is missing in unresolved_feedback -- a partial edit
that silently leaves other live feedback unaddressed is worse than
abstaining. maintainer_reply is the comment posted on the pull request
once the revision is pushed, answering the human feedback: at most three plain
sentences in the maintainer's own language saying what changed in response
to what they asked and directly answering any question they asked -- no
headings, lists, code blocks, thanks paragraphs or
restated analysis, and never a description of the contribution as automated.
Leave it empty when there is no human feedback or when returning NO_SAFE_FIX.
Return one JSON object and no Markdown."""
    schema = {
        "decision": "FIX_SUPPORTED|NO_SAFE_FIX",
        "feedback_analysis": [
            {"comment": "summary", "resolution": "how it is addressed"}
        ],
        "forward_compatibility_assessment": {
            "status": "preserved|regression_risk|unknown",
            "dimensions_checked": ["relevant dimensions"],
            "reasoning": "compatibility reasoning",
        },
        "edits": [
            {
                "source_file_path": (
                    "a path from current_files, or a new path when "
                    "edit_kind=create"
                ),
                "symbol": "class/method/constant name this edit targets",
                "edit_kind": "|".join(EDIT_KINDS),
                "original_code": "exact existing block, unique in the file",
                "corrected_code": "complete replacement block, or empty for delete",
                "reason": "which specific feedback this addresses",
                "required_imports": ["fully.qualified.TypeName without the import keyword"],
            }
        ],
        "explanation": "concise overall explanation",
        "unresolved_feedback": ["anything left unaddressed and why, including any rename/move"],
        "pr_title": "new pull request title, or empty to keep the current one",
        "pr_body": "new pull request description in Markdown, or empty to keep it",
        "maintainer_reply": "one or two plain sentences answering the maintainer, or empty",
    }
    user = (
        "Revise every file needed to address all actionable PR feedback. Search "
        "for an alternative that satisfies each reported concern without reducing "
        "future compatibility. A generic request such as 'fix ktlint issues' means "
        "the returned code must match the repository style visible in the input; "
        "do not claim that an external formatter was run. "
        "\n\n"
        "INPUT:\n" + prompt_json(payload)
        + "\n\nREQUIRED JSON:\n" + prompt_json(schema)
    )
    return system, user


def coordinated_pr_revision_schema() -> dict[str, Any]:
    feedback = {
        "type": "object",
        "properties": {
            "comment": {"type": "string"},
            "resolution": {"type": "string"},
        },
        "required": ["comment", "resolution"],
        "additionalProperties": False,
    }
    compatibility_properties = {
        "status": {
            "type": "string",
            "enum": ["preserved", "regression_risk", "unknown"],
        },
        "dimensions_checked": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    }
    compatibility = {
        "type": "object", "properties": compatibility_properties,
        "required": list(compatibility_properties), "additionalProperties": False,
    }
    edit_properties = {
        "source_file_path": {"type": "string"},
        "symbol": {"type": "string"},
        "edit_kind": {"type": "string", "enum": list(EDIT_KINDS)},
        "original_code": {"type": "string"},
        "corrected_code": {"type": "string"},
        "reason": {"type": "string"},
        "required_imports": {"type": "array", "items": {"type": "string"}},
    }
    edit = {
        "type": "object", "properties": edit_properties,
        "required": list(edit_properties), "additionalProperties": False,
    }
    properties = {
        "decision": {"type": "string", "enum": ["FIX_SUPPORTED", "NO_SAFE_FIX"]},
        "feedback_analysis": {"type": "array", "items": feedback},
        "forward_compatibility_assessment": compatibility,
        "edits": {"type": "array", "items": edit},
        "explanation": {"type": "string"},
        "unresolved_feedback": {"type": "array", "items": {"type": "string"}},
        "pr_title": {"type": "string"},
        "pr_body": {"type": "string"},
        "maintainer_reply": {"type": "string"},
    }
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def prepare_pr_checkout(
    metadata: dict[str, Any],
    branch: str,
    shared_project_root: Path,
    worktrees_dir: Path,
    head_clone_url: str = "",
) -> tuple[Path, str | None]:
    """Get an isolated checkout of the PR's branch, synced to its tip.

    shared_project_root (--projects-dir/<project>) is reused by extraction,
    multi_file_context, and other pipeline steps that expect it to stay on
    the repository's default branch; switching its checked-out branch here
    would leave it in an unexpected state for whichever step runs next.

    metadata["repository"] records the finding worktree the PR was
    originally created from -- a `git worktree`, sharing the same object
    database as shared_project_root but with its own independent working
    directory and HEAD. It is reused when still present and already on the
    PR's branch; otherwise a fresh worktree is created next to where it
    would have been. Either way the branch is then hard-reset to its
    current remote tip, so a stale local copy is never read as "current" or
    revised against outdated review comments.

    Returns (project_root, error). error is None on success; project_root
    is only meaningful (a location worth reporting) when error is set.
    """
    recorded = str(metadata.get("repository", "")).strip()
    candidate = Path(recorded) if recorded else None
    if candidate is not None and candidate.is_dir():
        try:
            current_branch = run_git(candidate, "branch", "--show-current")
        except RuntimeError:
            current_branch = None
        if current_branch == branch:
            try:
                run_git(shared_project_root, "fetch", "origin", branch)
                run_git(candidate, "reset", "--hard", f"origin/{branch}")
                run_git(candidate, "clean", "-fd")
            except RuntimeError as exc:
                return candidate, f"could not sync existing PR worktree: {exc}"
            return candidate, None

    if not branch:
        return shared_project_root, "no branch recorded for this PR"
    if not shared_project_root.is_dir():
        # The shared clone is local and cleanup removes it (HomeApp #114,
        # revised three weeks after it was published). The branch lives in
        # the PR's head repository, so that is what is cloned back.
        if not head_clone_url:
            return shared_project_root, "project clone is gone and the PR records no head repository"
        try:
            shared_project_root.parent.mkdir(parents=True, exist_ok=True)
            run_git(shared_project_root.parent, "clone", "--no-checkout",
                    head_clone_url, shared_project_root.name)
        except RuntimeError as exc:
            return shared_project_root, f"could not clone the PR's head repository: {exc}"
    try:
        run_git(shared_project_root, "fetch", "origin", branch)
    except RuntimeError as exc:
        return shared_project_root, f"could not fetch PR branch: {exc}"

    finding_id = str(metadata.get("finding_id", "")) or "fix_prs"
    worktree_path = (
        worktrees_dir / shared_project_root.name / finding_id / "fix_prs"
    )
    if worktree_path.exists():
        try:
            run_git(
                shared_project_root, "worktree", "remove", "--force",
                str(worktree_path),
            )
        except RuntimeError:
            pass  # Best-effort: `worktree add --force` below still wins.
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_git(
            shared_project_root, "worktree", "add", "--force",
            str(worktree_path), "-B", branch, f"origin/{branch}",
        )
    except RuntimeError as exc:
        return shared_project_root, f"could not create PR worktree: {exc}"
    return worktree_path, None


def write_revision_files(project_root: Path, updated_files: dict[str, str],
                         changed_paths: list[str], deleted_paths: list[str]) -> None:
    """Write a revision's files into the pull request's checkout.

    Each file keeps the line endings it has on disk (write_source): the
    revision is applied to the LF text gather_candidate_files read, and a
    CRLF file written back as LF changed every line (2026-10-30).
    """
    for path in deleted_paths:
        (project_root / path).unlink(missing_ok=True)
    for path in changed_paths:
        if path in deleted_paths:
            continue
        destination = project_root / path
        # A created file may land in a package directory that does not
        # exist yet.
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_source(destination, updated_files[path])


def apply_coordinated_edits(
    files: dict[str, str], edits: list[dict[str, Any]],
) -> tuple[dict[str, str] | None, list[str]]:
    """Apply the revision's edit set to the PR checkout's file contents.

    Delegates to the shared applier in src/solutions/coordinated_edits.py so
    this flow and the worktree applier cannot drift apart again. Indentation
    drift is tolerated here because a pull-request branch may have moved since
    the evidence was gathered; that module documents why it is a caller
    decision rather than a fixed rule.
    """
    return apply_edits_to_sources(
        files, edits, tolerate_indentation_drift=True,
    )


# Classes the compiler generates for a lambda body: D8/R8 desugaring
# (Foo$$ExternalSyntheticLambda0), the JDK's (Foo$$Lambda$12) and Kotlin's
# (Foo$lambda$1, Foo$save$1 is a $N class and left to source_names).
_SYNTHETIC_LAMBDA_CLASS = re.compile(r"\$\$ExternalSyntheticLambda\d*$|\$\$Lambda(?:\$\d+)?(?:/.*)?$"
                                     r"|\$lambda[\$\d]*$")


def revision_commit_message(template: str, record: dict[str, Any]) -> str:
    """The follow-up commit message, naming the method as the source does.

    SMSecure #89's revision commit read "Address pull request feedback for
    <init>" (2026-11-02): the record holds the bytecode name. The same
    mapping as the pull request's text (source_names): a constructor or
    static initializer is its class, a Kotlin file-level function its own
    name, a `$` class its outer class.
    """
    method = str(record.get("method_name", "") or "")
    class_name = str(record.get("class_name", "") or "")
    lambda_class = _SYNTHETIC_LAMBDA_CLASS.search(class_name)
    if lambda_class:
        # Foo$$ExternalSyntheticLambda0.run (review of batch 17, 2026-11-02)
        # read "Foo.run": the method is the synthetic class's, not Foo's. The
        # enclosing function when the record resolved it, else the lambda.
        outer = class_name[:lambda_class.start()].rsplit(".", 1)[-1].split("$", 1)[0]
        enclosing = str(record.get("source_method_name", "") or "")
        if enclosing and enclosing != method and re.fullmatch(r"[A-Za-z_][\w]*", enclosing):
            return template.format(method_name=".".join(v for v in (outer, enclosing) if v))
        return template.format(method_name=f"a lambda in {outer}" if outer else "a lambda")
    owner, member, kind = source_names(class_name, method)
    if not owner and kind in ("constructor", "static initializer"):
        # A record written before class_name was kept: the source file is
        # named after its class.
        owner = Path(str(record.get("source_file", "") or "")).stem
    if kind in ("constructor", "static initializer"):
        name = f"the {owner} {kind}" if owner else kind
    else:
        name = ".".join(value for value in (owner, member) if value) or method
    return template.format(method_name=name)


def publish_revalidated(record: dict[str, Any], args: argparse.Namespace,
                        logger: logging.Logger) -> None:
    """Push an approved REVALIDATED revision, update its text and reply.

    Shared by --revalidate --publish and by a plain --publish run whose
    feedback is unchanged since that revision: the latter used to log
    "keeping the existing result" and push nothing although the finding was
    named by --approve-revision (SMSecure #89, 2026-11-02).
    """
    branch = str(record.get("branch", ""))
    changed = list(record.get("changed_files", []) or [])
    project_root = Path(str(record.get("checkout", ""))).resolve()
    changed_sources = [project_root / path for path in changed]
    publish_refusal = (revision_approval(record, args)[1]
                       if args.publish and not args.dry_run else "")
    if publish_refusal:
        # What it would send, recorded and logged; nothing is pushed.
        record["publish"] = {
            "status": "NOT_APPROVED", "reason": publish_refusal,
            "would_push": {"branch": branch, "files": list(changed)},
            **({"would_update": record["pr_metadata_update"]}
               if record.get("pr_metadata_update") else {}),
            **({"would_reply": record["maintainer_reply"]}
               if record.get("maintainer_reply") else {}),
        }
        logger.info("%s: revalidated revision not pushed: %s",
                    record.get("project", ""), publish_refusal)
        return
    if not (args.publish and not args.dry_run):
        return
    record.pop("publish", None)
    if not branch:
        record.update(
            status="FAILED", failure_stage="revalidation_fix_prs",
            error="revalidation: record is missing branch",
        )
        return
    try:
        record["commit"] = publish_revision(
            project_root, changed_sources, branch,
            revision_commit_message(args.commit_message, record),
        )
        record["status"] = "PUBLISHED"
        metadata_update = record.get("pr_metadata_update") or {}
        if metadata_update:
            try:
                update_pr_metadata(
                    str(record.get("pull_request_api_url", "")),
                    metadata_update,
                )
                record["pr_metadata_updated"] = sorted(metadata_update)
            except Exception as exc:
                logger.warning(
                    "%s: publish succeeded but updating the pull "
                    "request title/description failed: %s",
                    record.get("project", ""), exc,
                )
        if record.get("maintainer_reply"):
            try:
                post_pr_comment(
                    str(record.get("comments_url", "")),
                    record["maintainer_reply"],
                )
                record["maintainer_reply_posted"] = True
            except Exception as exc:
                logger.warning(
                    "%s: publish succeeded but posting the reply to "
                    "the maintainer failed: %s",
                    record.get("project", ""), exc,
                )
        if record.get("ci_failures"):
            try:
                post_pr_comment(
                    str(record.get("comments_url", "")),
                    ci_failure_comment_body(record),
                )
            except Exception as exc:
                logger.warning(
                    "%s: publish succeeded but posting the CI-failure "
                    "explanation comment failed: %s",
                    record.get("project", ""), exc,
                )
    except Exception as exc:
        record.update(
            status="FAILED", failure_stage="revalidation_fix_prs",
            error=str(exc),
        )


def run_revalidation(args: argparse.Namespace) -> int:
    """Compile, style-check, and (with --publish) push every APPLIED entry.

    This is the explicit revalidation_fix_prs pipeline step: it never calls
    OpenAI, only re-checks and optionally publishes what run_revision already
    wrote to each entry's checkout. Keeping it a separate invocation (rather
    than the tail end of run_revision) is what lets it be its own named step
    -- its own "=== REVALIDATION_FIX_PRS ===" terminal section, its own
    skip/force toggle in executar_pipeline.sh -- instead of compiling and
    publishing being buried inside whichever run revised the code.
    """
    logger = logging.getLogger("fix_prs")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    output = Path(args.output).resolve()
    if not output.is_file():
        logger.error("No fix_prs report to revalidate: %s", output)
        return 1
    report = json.loads(output.read_text(encoding="utf-8"))
    results = report.get("results", [])
    for record in results:
        status = record.get("status")
        # Before the status filter below, because a missing sign-off is
        # independent of any code revision: it is a property of the commits,
        # so a record with no changed files at all still needs it. The
        # fix_prs step that diagnosed it runs without --publish, so this is
        # the only place the remedy can actually be pushed.
        signoff = record.get("signoff_failure")
        signoff_refusal = (revision_approval(record, args)[1]
                           if signoff and args.publish and not args.dry_run else "")
        if signoff_refusal:
            record["signoff_repair"] = {"status": "NOT_APPROVED", "detail": signoff_refusal}
            logger.info("%s: would sign off and force-push %s; %s",
                        record.get("project", ""), record.get("branch", ""), signoff_refusal)
        elif signoff and args.publish and not args.dry_run:
            checkout = str(record.get("checkout", ""))
            if not checkout:
                record["signoff_repair"] = {
                    "status": "SKIPPED",
                    "detail": "no checkout recorded for this pull request",
                }
            else:
                try:
                    record["signoff_repair"] = repair_missing_signoff(
                        Path(checkout).resolve(),
                        str(record.get("branch", "")),
                        str(signoff.get("head_sha", "")),
                        signoff.get("commits"),
                        logger,
                    )
                except Exception as exc:
                    record["signoff_repair"] = {
                        "status": "FAILED", "detail": str(exc),
                    }
                    logger.warning(
                        "%s: could not repair the missing sign-off: %s",
                        record.get("project", ""), exc,
                    )
            if record["signoff_repair"].get("status") == "PUBLISHED":
                record["status"] = status = "SIGNOFF_REPAIRED"
        publish_ready = status == "REVALIDATED" and args.publish
        retryable_failure = (
            status == "FAILED"
            and bool(record.get("checkout"))
            and bool(record.get("changed_files"))
            and (
                record.get("failure_stage") == "revalidation_fix_prs"
                # Compatibility with reports written before failure_stage
                # existed, including the path-resolution failure that led to
                # this retry support.
                or "compile_validation" in record
                or "style_validation" in record
            )
        )
        if status != "APPLIED" and not retryable_failure and not publish_ready:
            continue
        project = str(record.get("project", ""))
        if args.project and project != args.project:
            continue
        checkout = str(record.get("checkout", ""))
        changed = record.get("changed_files", [])
        branch = str(record.get("branch", ""))
        if not checkout or not changed:
            record.update(
                status="FAILED",
                error="revalidation: record is missing checkout or changed_files",
            )
            continue
        record.pop("error", None)
        record.pop("failure_stage", None)
        project_root = Path(checkout).resolve()
        changed_sources = [project_root / path for path in changed]
        # A REVALIDATED record already carries a successful compile result.
        # When the user later reruns this step with --publish, reuse that
        # evidence and perform only commit/push. Rebuilding first made little
        # sense, while skipping the record entirely made --publish report a
        # misleading success without publishing anything.
        if not publish_ready and not args.skip_compile_validation:
            safe_method = re.sub(
                r"[^A-Za-z0-9._-]+", "_", str(record.get("method_name", project))
            )
            build_output_dir = (
                output.parent / "fix_prs_revalidation" / project / safe_method
            )
            try:
                compile_result = run_compile_validation(
                    project_root, changed_sources, args.build_script,
                    build_output_dir, args.build_variant, args.build_timeout,
                )
            except Exception as exc:
                compile_result = {"status": "FAILED", "error": str(exc)}
            record["compile_validation"] = compile_result
            # NOT_CONFIGURED (no Gradle wrapper) is not a pass: it means
            # this revision was never actually compiled, so it must not be
            # silently treated as safe to publish.
            if compile_result["status"] != "SUCCESS":
                record.update(
                    status="FAILED",
                    failure_stage="revalidation_fix_prs",
                    error=(
                        "revised project did not compile"
                        if compile_result["status"] == "FAILED"
                        else "compile validation was not configured for this project"
                    ),
                )
                continue
        if not publish_ready and not args.skip_validation:
            try:
                style_result = run_style_validation(
                    project_root, changed_sources, args.timeout
                )
            except Exception as exc:
                style_result = {"status": "FAILED", "error": str(exc)}
            if style_result["status"] == "FAILED":
                # Formatting violations are mechanically fixable by the
                # project's own formatter; only give up if they survive it.
                repair = repair_style_violations(
                    project_root, changed_sources, args.timeout
                )
                record["style_repair"] = repair
                if repair.get("status") == "SUCCESS":
                    try:
                        style_result = run_style_validation(
                            project_root, changed_sources, args.timeout
                        )
                    except Exception as exc:
                        style_result = {"status": "FAILED", "error": str(exc)}
            record["style_validation"] = style_result
            if style_result["status"] == "FAILED":
                record.update(
                    status="FAILED", failure_stage="revalidation_fix_prs",
                    error="ktlintCheck failed",
                )
                continue
        record["status"] = "REVALIDATED"
        record.pop("failure_stage", None)
        publish_revalidated(record, args, logger)
    report["results"] = results
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log_result_summary(logger, results, args.project)
    return 1 if any(row.get("status") == "FAILED" for row in results) else 0


def run_revision(args: argparse.Namespace) -> int:
    pr_dir = Path(args.pull_requests_dir).resolve()
    projects_dir = Path(args.projects_dir).resolve()
    worktrees_dir = Path(args.worktrees_dir).resolve()
    output = Path(args.output).resolve()
    logger = logging.getLogger("fix_prs")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    provider = remote_provider(
        openai_class=OpenAIProvider,
        endpoint=args.endpoint,
        model=args.model,
        timeout=args.timeout,
        max_output_tokens=args.max_output_tokens,
        reasoning_effort=args.reasoning_effort,
        prompt_log_dir=args.prompt_log_dir,
        logger=logger,
    )
    extractor = MethodExtractor(logger)
    results: list[dict[str, Any]] = []

    # A plain (non --revalidate) run always re-fetches every PR's live
    # comments and used to rebuild `results` from scratch, so re-running it
    # against feedback that hadn't changed silently threw away whatever
    # revalidation_fix_prs had already recorded (REVALIDATED/PUBLISHED,
    # compile_validation, style_validation, commit) and reset the entry back
    # to a fresh, unvalidated "APPLIED". Loading the previous report lets a
    # rerun recognize "nothing new to revise" and keep that prior result
    # untouched instead of clobbering it. It also preserves the records of
    # any project this run doesn't touch (e.g. a --project-filtered run),
    # which previously vanished from the report entirely.
    previous_results_by_project: dict[str, dict[str, Any]] = {}
    if output.is_file():
        try:
            previous_report = json.loads(output.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous_report = {}
        for previous_record in previous_report.get("results", []):
            name = previous_record.get("project")
            if name:
                previous_results_by_project[str(name)] = previous_record
    processed_projects: set[str] = set()

    created_files = sorted(pr_dir.glob("*_pr_created.json"))
    if not created_files:
        # Published PR artifacts live in the run directory that created them.
        # Pointing a later run's --pull-requests-dir here finds nothing, and
        # reporting plain success made that indistinguishable from "every PR
        # was already up to date".
        logger.warning(
            "No published pull requests found in %s: this step scans "
            "'*_pr_created.json' there and found none, so nothing was "
            "revised. If the pull request you meant to revise was opened by "
            "an earlier run, point --pull-requests-dir at that run's "
            "pull_requests directory.", pr_dir,
        )

    for created_file in created_files:
        pr = json.loads(created_file.read_text(encoding="utf-8"))
        metadata_file = created_file.with_name(
            created_file.name.replace("_pr_created.json", "_pr.json")
        )
        if not metadata_file.is_file():
            continue
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        project_name = project_name_from_metadata(metadata)
        if args.project and project_name != args.project:
            continue
        processed_projects.add(project_name)
        record: dict[str, Any] = {
            "project": project_name,
            # What --approve-revision names to publish this revision.
            "finding_id": str(metadata.get("finding_id", "") or ""),
            "pull_request": pr.get("html_url", ""),
            "pull_request_api_url": pr.get("url", ""),
            "comments_url": pr.get("comments_url", ""),
        }
        if not args.skip_pr_state_check:
            try:
                pr_state = fetch_current_pr_state(pr)
            except Exception as exc:  # noqa: BLE001 -- recorded as its own status
                pr_state = {"status": "unknown", "reason": str(exc)}
            record["pr_state"] = pr_state
            if pr_state.get("status") == "unknown":
                # Fail closed. The check exists so that a pull request closed
                # or merged since its snapshot is not revised and pushed to,
                # and nothing later re-checks before the push; proceeding on
                # "unknown" made a network error indistinguishable from
                # "still open". The next run retries.
                record["status"] = "PR_STATE_UNKNOWN"
                results.append(record)
                continue
            if pr_state.get("status") != "open":
                record["status"] = "PR_NOT_OPEN"
                if pr_state.get("status") in {"closed", "merged"}:
                    # A closed pull request carries the maintainers' reasons.
                    # It used to be skipped without reading them, so nothing
                    # stopped a later run from proposing the same change again.
                    try:
                        closing_feedback = feedback_from_pr(pr)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Could not read feedback of %s: %s",
                            pr.get("html_url", ""), exc,
                        )
                        closing_feedback = []
                    # The class of a PR published before its record carried
                    # one comes from its run's adjudication (report only).
                    record["outcome_record"] = str(record_pr_outcome(
                        {**metadata,
                         "impact_class": published_impact_class(created_file)},
                        pr, pr_state, closing_feedback,
                    ))
                results.append(record)
                continue
        feedback = feedback_from_pr(pr)
        record["feedback_count"] = len(feedback)
        if args.self_audit:
            # Deliberately not gated on "no maintainer feedback", unlike the
            # CI fallback below. A gate finding is an independent defect, and
            # suppressing it whenever someone happened to comment about
            # something else is how PR #1210 kept its TLS assertions inside
            # DateFormattingTest.kt through a revision that touched only the
            # production file.
            audit = self_audit_findings(metadata)
            if audit:
                record["self_audit"] = [item["audit_code"] for item in audit]
                feedback = feedback + audit
        # Before the CI failures that need the model: a missing Signed-off-by
        # is a property of the commits, not of the code, so there is no file
        # for a revision to edit and the deterministic repair is exactly the
        # one the DCO check itself prescribes. Not gated on fix_ci_failures,
        # which governs the model path, nor on "no maintainer feedback": a
        # blocked DCO check blocks the merge whatever else is happening.
        ci_evidence_errors: list[str] = []
        signoff = signoff_check_failure(pr, ci_evidence_errors)
        if signoff:
            # This step diagnoses; revalidation_fix_prs publishes. That is
            # the same division the rest of fix_prs follows, and it is not
            # optional here: the fix_prs invocation carries no --publish, so
            # a repair attempted at this point could detect the problem and
            # never push the remedy -- which is exactly what it did.
            signoff["commits"] = pr.get("commits")
            signoff_branch = str(
                pr.get("head", {}).get("ref") or metadata.get("branch", "")
            )
            signoff_root, signoff_error = prepare_pr_checkout(
                metadata, signoff_branch, projects_dir / project_name,
                worktrees_dir,
                str(((pr.get("head") or {}).get("repo") or {}).get("clone_url", "")),
            )
            if signoff_error:
                signoff["checkout_error"] = signoff_error
            else:
                record.setdefault("checkout", str(signoff_root))
                record.setdefault("branch", signoff_branch)
            record["signoff_failure"] = signoff
        ci_failures: list[dict[str, Any]] = []
        if not feedback and args.fix_ci_failures:
            ci_failures = ci_failures_from_pr(pr, ci_evidence_errors)
            if ci_failures:
                record["ci_failures"] = [
                    {
                        "check_name": item.get("check_name", ""),
                        "reproduces_on_base": item.get("reproduces_on_base"),
                        "url": item.get("url", ""),
                    }
                    for item in ci_failures
                ]
            feedback = feedback + ci_failures
        if ci_evidence_errors:
            record["ci_evidence_errors"] = ci_evidence_errors
        if not feedback:
            # "Nothing to address" only when the checks were actually read:
            # a failed fetch looks exactly like a pull request with none
            # failing, and would be recorded as settled.
            record["status"] = (
                "CI_EVIDENCE_UNAVAILABLE" if ci_evidence_errors else "NO_FEEDBACK"
            )
            results.append(record)
            continue
        feedback_signature = hashlib.sha256(
            json.dumps(feedback, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        record["feedback_signature"] = feedback_signature
        previous_record = previous_results_by_project.get(project_name)
        if (
            previous_record is not None
            and previous_record.get("status") in {"REVALIDATED", "PUBLISHED"}
            and previous_record.get("feedback_signature") == feedback_signature
        ):
            logger.info(
                "%s: maintainer feedback unchanged since the last %s revision; "
                "keeping the existing result instead of re-revising",
                project_name, previous_record.get("status"),
            )
            if (previous_record.get("status") == "REVALIDATED"
                    and getattr(args, "publish", False) and not args.dry_run):
                # The approved revision is already compiled and checked; this
                # run is the one the user asked to publish it.
                publish_revalidated(previous_record, args, logger)
            results.append(previous_record)
            continue

        branch = str(pr.get("head", {}).get("ref") or metadata.get("branch", ""))
        project_root, checkout_error = prepare_pr_checkout(
            metadata, branch, projects_dir / project_name, worktrees_dir,
            str(((pr.get("head") or {}).get("repo") or {}).get("clone_url", "")),
        )
        record["checkout"] = str(project_root)
        record["branch"] = branch
        record["method_name"] = metadata.get("method_name", "")
        record["class_name"] = str(metadata.get("csv_class", "") or metadata.get("class_name", ""))
        record["source_method_name"] = str(metadata.get("source_method_name", "") or "")
        record["source_file"] = str(metadata.get("source_file", ""))
        if checkout_error:
            record.update(status="FAILED", error=checkout_error)
            results.append(record)
            continue
        source = project_root / metadata["source_file"]
        if not source.is_file():
            record.update(status="FAILED", error=f"Source not found: {source}")
            results.append(record)
            continue
        source_text = source.read_text(encoding="utf-8", errors="replace")
        method_name = str(metadata["method_name"])
        if method_name == "<clinit>":
            extracted = (extractor.extract_static_initializer(source_text)
                         or extractor.extract_class_initializer(source_text))
        else:
            lookup_name = resolve_source_method_name(
                method_name, str(metadata.get("csv_class", "")), source
            )
            extracted = extractor.extract_method_flexible(source_text, lookup_name)
            if not extracted and method_name == "<init>":
                extracted = extractor.extract_class_initializer(source_text)
        if not extracted:
            record.update(status="FAILED", error="Current method could not be extracted")
            results.append(record)
            continue
        current_method, start_line, end_line = extracted

        base_paths = {str(source.relative_to(project_root))} | {
            str(item.get("path", "")).strip() for item in feedback
            if str(item.get("path", "")).strip()
        }
        extra_paths = discover_referenced_class_files(
            project_root, feedback, already_covered=base_paths,
        )
        # Where the module keeps the text it shows users; the file goes first
        # so the candidate cap never drops it (see project_conventions).
        text_convention = user_visible_text_convention(project_root, source)
        if text_convention.get("convention") == "string_resources":
            extra_paths.insert(0, text_convention["strings_file"])
        record["text_convention"] = text_convention.get("convention", "")
        extra_paths += settings_files(
            project_root, feedback, already_covered=base_paths | set(extra_paths))
        # What this pull request added or changed, so a test it introduced
        # is revised with the behaviour (HomeApp #114 kept a test asserting
        # that proceed() was never called after the toggle added it back).
        pull_request_files = sorted(
            str(path) for path in metadata.get("changed_files") or []
            if (project_root / str(path)).is_file())
        # First: gather_candidate_files keeps only MAX_CANDIDATE_FILES, and
        # appended last the test was the one cut.
        extra_paths[:0] = [path for path in pull_request_files if path not in extra_paths]
        if ci_failures:
            extra_paths += build_file_candidates(
                project_root, ci_failures,
                already_covered=base_paths | set(extra_paths),
            )
        candidate_files = gather_candidate_files(
            project_root, source, feedback, extra_paths=extra_paths,
        )
        usage_paths = discover_type_usage_files(
            project_root, feedback, candidate_files,
        )
        if usage_paths:
            record["type_usage_files"] = usage_paths
            candidate_files = gather_candidate_files(
                project_root, source, feedback,
                extra_paths=extra_paths + usage_paths,
            )
        record["candidate_files"] = sorted(candidate_files)
        payload = {
            "pull_request": pr.get("html_url", ""),
            "method_name": metadata["method_name"],
            "source_file": metadata["source_file"],
            "original_finding": {
                "spec": metadata.get("spec", ""),
                "error": metadata.get("error_message", ""),
                "previous_explanation": metadata.get("body", ""),
            },
            "current_method": current_method,
            "method_lines": [start_line, end_line],
            "current_pull_request": {
                "title": pr.get("title", ""),
                "body": pr.get("body", "") or metadata.get("body", ""),
            },
            "maintainer_feedback": feedback,
            "pull_request_files": pull_request_files,
            **({"author_guidance": args.author_guidance} if args.author_guidance else {}),
            "current_files": candidate_files,
            "repository_style_context": "\n\n".join(part for part in (
                repository_style_context(project_root),
                describe_text_convention(text_convention),
            ) if part),
            "repository_symbol_evidence": repository_symbol_evidence(
                project_root, feedback
            ),
        }
        system, user = coordinated_prompts(payload)
        response = provider.generate(
            user, system, json_mode=True,
            json_schema=coordinated_pr_revision_schema(), schema_name="pr_revision",
            trace_stage="fix_prs.maintainer_revision",
            trace_context={
                "finding_id": str(metadata.get("unique_error_id", "")),
                "prompt_version": "pr-revision-v3",
                "schema_version": "3.0",
                "policy_version": "2.0",
            },
        )
        revision = ResponseParser.parse_json_object(response) or {}
        record["llm_revision"] = revision
        edits = revision.get("edits", [])
        if revision.get("decision") != "FIX_SUPPORTED" or not edits:
            record["status"] = "NO_SAFE_FIX"
            results.append(record)
            continue
        updated_files, apply_issues = apply_coordinated_edits(candidate_files, edits)
        apply_issues = list(apply_issues) + governed_api_removal(
            str(metadata.get("spec", "")), edits, feedback)
        if apply_issues:
            # One mechanically malformed edit used to discard an otherwise
            # complete revision -- a correct four-edit relocation was thrown
            # away because a single removal was labelled "test" instead of
            # "delete", after a multi-minute call. The issues are precise and
            # the model fixes them when told, so spend one retry before
            # abandoning the work.
            record["apply_issues_first_attempt"] = list(apply_issues)
            retry_payload = dict(payload)
            retry_payload["rejected_edits_feedback"] = {
                "instruction": (
                    "Your previous edits were rejected by deterministic "
                    "checks. Return a revision with these issues fixed, "
                    "changing nothing else they do not require. Remember that an edit "
                    "whose corrected_code is empty must use edit_kind "
                    "\"delete\", whatever kind of code the removed block "
                    "contains."
                ),
                "issues": list(apply_issues),
                "previous_edits": edits,
            }
            retry_system, retry_user = coordinated_prompts(retry_payload)
            retry_response = provider.generate(
                retry_user, retry_system, json_mode=True,
                json_schema=coordinated_pr_revision_schema(),
                schema_name="pr_revision",
                trace_stage="fix_prs.maintainer_revision.shape_retry",
                trace_context={
                    "finding_id": str(metadata.get("unique_error_id", "")),
                    "prompt_version": "pr-revision-v3",
                    "schema_version": "3.0",
                    "policy_version": "2.0",
                },
            )
            retry_revision = ResponseParser.parse_json_object(retry_response) or {}
            retry_edits = retry_revision.get("edits", [])
            if retry_revision.get("decision") == "FIX_SUPPORTED" and retry_edits:
                record["llm_revision"] = retry_revision
                edits = retry_edits
                updated_files, apply_issues = apply_coordinated_edits(
                    candidate_files, edits
                )
                apply_issues = list(apply_issues) + governed_api_removal(
                    str(metadata.get("spec", "")), edits, feedback)
        if apply_issues:
            record.update(status="FAILED", error="; ".join(apply_issues))
            results.append(record)
            continue
        deleted_paths = sorted(set(candidate_files) - set(updated_files))
        changed_paths = sorted(
            [
                path for path, text in updated_files.items()
                if text != candidate_files.get(path, "")
            ] + deleted_paths
        )
        if not changed_paths:
            record.update(status="FAILED", error="Edits made no change")
            results.append(record)
            continue
        if not args.dry_run:
            write_revision_files(project_root, updated_files, changed_paths, deleted_paths)
        # Compiling, style-checking, and publishing are the explicit
        # revalidation_fix_prs step (see run_revalidation): this status is
        # what it looks for, and it re-derives changed_sources from
        # changed_files + checkout above.
        record["status"] = "DRY_RUN" if args.dry_run else "APPLIED"
        record["changed_files"] = changed_paths
        if deleted_paths:
            record["deleted_files"] = deleted_paths
        # Only alongside a code revision: a rewritten description must
        # describe code that is actually pushed, and revalidation_fix_prs
        # applies it only after that push succeeds.
        metadata_update, metadata_issues = pr_metadata_update(
            record["llm_revision"], pr,
        )
        if metadata_update:
            record["pr_metadata_update"] = metadata_update
        if metadata_issues:
            record["pr_metadata_rejected"] = metadata_issues
        # Only human feedback is answered; self-audit and CI items are not.
        if record.get("feedback_count"):
            reply, reply_issue = maintainer_reply(record["llm_revision"])
            if reply:
                record["maintainer_reply"] = reply
            if reply_issue:
                record.setdefault("pr_metadata_rejected", []).append(reply_issue)
        results.append(record)

    for name, previous_record in previous_results_by_project.items():
        if name not in processed_projects:
            results.append(previous_record)

    report = {
        "provider": "openai",
        "model": args.model,
        "dry_run": args.dry_run,
        "token_usage": provider.get_token_usage(),
        # Recorded so an empty "results" is readable after the fact: no
        # published PR to revise reads very differently from every PR being
        # up to date, and the report used to show both as [].
        "pull_requests_dir": str(pr_dir),
        "published_pull_requests_found": len(created_files),
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log_result_summary(logger, results, args.project)
    return 1 if any(row["status"] == "FAILED" for row in results) else 0


def main() -> int:
    args = parse_args()
    if args.revalidate:
        return run_revalidation(args)
    return run_revision(args)


if __name__ == "__main__":
    raise SystemExit(main())
