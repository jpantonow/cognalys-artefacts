#!/usr/bin/env python3
"""Generate one Markdown preview for every prepared pull request and issue."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from cognalys.utils.artifact_names import finding_artifact_stem
from cognalys.issues.issue_creator import IssueCreator
from cognalys.issues.issue_text import DEFAULT_TITLE_WITHOUT_SECURITY_IMPACT, contact_issue_title
# Regenerating the preview of an earlier run shows the same review notes as
# a new one, without rerunning the prs step (shared with pr_creator).
from cognalys.llm.fix_artifacts import with_recorded_review_notes  # noqa: F401
from cognalys.llm.generation_loops import loop_depth_note
from cognalys.prs.publication_checks import (
    published_pull_request,
    review_absent_by_design,
    stale_review,
)
from cognalys.utils.disposition import (
    BUILD_VALIDATION_PASSING_STATUSES,
    FindingDisposition,
)
from cognalys.llm.pipeline_architecture import (
    blocked_finding_review_status,
    detector_arity_overmatch_reason,
    false_positive_rationale,
    confirmed_false_positive_reason,
)


# A bare status word next to a predicate reads as agreement with whatever the
# predicate says, which is why markor's analysis rendered "a forbidden
# one-argument or three-argument PBEKeySpec constructor is used - confirmed"
# over an explanation stating that the four-argument overload is the one used.
# Spelling out what each value asserts keeps the human reader from having to
# infer the convention from the explanation underneath.
PREDICATE_STATUS_MEANING = {
    "confirmed": "the requirement holds",
    "violated": "the requirement does not hold",
    "unknown": "the evidence does not decide",
}


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value or "preview"


def code_language(source_file: str) -> str:
    suffix = Path(source_file).suffix.lower()
    return {".java": "java", ".kt": "kotlin", ".kts": "kotlin"}.get(
        suffix, "text"
    )


def code_change_preview(metadata: dict) -> str:
    """Render local-only source excerpts and the unified diff."""
    coordinated = metadata.get("coordinated_edits", [])
    original = str(metadata.get("original_method_body", "")).strip("\r\n")
    corrected = str(metadata.get("corrected_method_body", "")).strip("\r\n")
    diff = str(metadata.get("diff", "")).strip("\r\n")
    if not any((original, corrected, diff)):
        return (
            "\n\n---\n\n## Code change preview\n\n"
            "Code details are unavailable in this legacy PR metadata file. "
            "Regenerate the `solutions`, `prs`, and `preview` steps.\n"
        )

    language = code_language(str(metadata.get("source_file", "")))
    parts = ["", "---", "", "## Code change preview", ""]
    if isinstance(coordinated, list) and coordinated:
        parts.extend(["### Coordinated edits", ""])
        for edit in coordinated:
            source = str(edit.get("source_file", "source"))
            symbol = str(edit.get("symbol", "change"))
            edit_language = code_language(source)
            corrected = str(edit.get("corrected_code", "")).strip("\r\n")
            if edit.get("edit_kind") == "delete":
                corrected = "// Deleted: " + symbol
            parts.extend([
                f"#### `{source}` — `{symbol}`",
                "",
                f"````{edit_language}",
                corrected,
                "````",
                "",
            ])
    # A coordinated patch has no single canonical "method": the solution
    # metadata may select an import, declaration, or test as its primary edit.
    # The per-file edits and unified diff are authoritative in that case.
    if original and not coordinated:
        parts.extend([
            "<details>",
            "<summary>Original method</summary>",
            "",
            f"````{language}",
            original,
            "````",
            "",
            "</details>",
            "",
        ])
    if corrected and not coordinated:
        parts.extend([
            "### Corrected method",
            "",
            f"````{language}",
            corrected,
            "````",
            "",
        ])
    if diff:
        parts.extend([
            "### Unified diff",
            "",
            "````diff",
            diff,
            "````",
            "",
        ])
    return "\n".join(parts).rstrip() + "\n"


def markdown_list(values: object, empty: str = "None reported.") -> str:
    if not isinstance(values, list) or not values:
        return empty
    return "\n".join(f"- {str(value).strip()}" for value in values)


def analysis_preview(metadata: dict) -> str:
    analysis = metadata.get("analysis", {})
    if not isinstance(analysis, dict):
        analysis = {}
    decision = str(analysis.get("decision", metadata.get("status", "unknown")))
    cause = str(analysis.get("confirmed_cause", "")).strip()
    status = str(metadata.get("status", "unknown"))
    source_file = str(metadata.get("source_file", ""))
    if status in {
        "NO_SAFE_FIX", "CONFIRMED_FINDING_NO_SAFE_FIX",
        "CONFIRMED_CAUSE_ONLY_NO_SAFE_FIX", "UNCONFIRMED_FINDING",
    }:
        status = blocked_finding_review_status(
            analysis,
            spec_id=str(metadata.get("spec", "")),
            error_message=str(metadata.get("error_message", "")),
            app_source_file_path=source_file,
            # Recorded by the generation stage, which had the repository.
            reported_violation=str(metadata.get("reported_violation", "")),
            observed_calls=metadata.get("observed_unmodelled_calls") or [],
            specification_context=metadata.get("specification_context") or {},
            source=str(metadata.get("method_body", "")),
        )
    if status == "CONFIRMED_FALSE_POSITIVE":
        reason = false_positive_rationale(
            str(metadata.get("spec", "")),
            str(metadata.get("error_message", "")),
            bool((analysis.get("dependency_fix_assessment") or {}).get(
                "external_class_confirmed"
            )),
            source_file,
            str(metadata.get("reported_violation", "")),
            metadata.get("observed_unmodelled_calls") or [],
            metadata.get("specification_context") or {},
            str(metadata.get("method_body", "")),
        )
        # Which route closed it decides what may be said about why. The
        # curated route's justification rests on the reported class being
        # absent from the application repository; the arity route closes
        # findings whose class is present and whose source is correct as
        # written, so claiming the class was absent would be false.
        if reason and reason == detector_arity_overmatch_reason(
            metadata.get("specification_context") or {},
            str(metadata.get("method_body", "")),
        ):
            interpretation = (
                "This finding is classified as a confirmed false positive, "
                "not an unresolved unknown, because the rule that reported "
                "it matches calls by name without constraining the "
                "parameter list, and the reviewed source's matched call "
                "cannot be the method the rule governs. There is nothing to "
                "repair in the reviewed source: " + reason
            )
        else:
            interpretation = (
                "This finding is classified as a confirmed false positive, not "
                "an unresolved unknown, because the reported class was not "
                "found in the analyzed application's own repository (there is "
                "no application source to patch) and the detector message "
                "matches a shape the specification itself documents as a known "
                "false positive: " + (reason or "See the specification's "
                "known_false_positive_shapes entry for this message.")
            )
    elif status == "UNCONFIRMED_FINDING":
        interpretation = (
            "No root cause was confirmed. This may be a false positive, but "
            "it can also mean that required runtime or source evidence is missing."
        )
    elif status == "CONFIRMED_FINDING_NO_SAFE_FIX":
        interpretation = (
            "The detector violation was confirmed, but the available evidence "
            "does not support a safe automatic code change. This is not "
            "classified as a false positive."
        )
    elif status == "CONFIRMED_CAUSE_ONLY_NO_SAFE_FIX":
        interpretation = (
            "Repository behavior and a root cause were confirmed, but the "
            "detector violation itself remains unconfirmed or unknown. No safe "
            "automatic code change is authorized."
        )
    elif status in {
        "FIX_SUPPORTED", "MULTI_FILE_FIX_SUPPORTED",
        "DEPENDENCY_FIX_SUPPORTED", "DEPENDENCY_REPLACEMENT_SUPPORTED",
    }:
        interpretation = "The evidence supports generating a code correction."
    elif status == "CONFIRMED_FINDING_REQUIRES_POLICY":
        interpretation = (
            "The affected edit set was identified, but automatic correction "
            "requires an explicit behavioral or migration policy."
        )
    elif status == "PROPOSED_FIX_REQUIRES_REVIEW":
        interpretation = (
            "A complete candidate patch was generated from confirmed evidence, "
            "but residual assumptions require human review. It is explicitly "
            "blocked from automatic application and publication."
        )
    elif status == "INCOMPLETE_MULTI_FILE_SCOPE":
        interpretation = (
            "The analysis JSON is valid and the cause may be confirmed, but "
            "one or more generation-blocking local code relations were not "
            "resolved. External backend or deployed-client uncertainty alone "
            "should be recorded as a publication blocker for review-only work."
        )
    elif status == "EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX":
        interpretation = (
            "The digest participates in an external contract. A client-only "
            "algorithm replacement is not compatible with the supplied evidence."
        )
    elif status == "CONFIRMED_NON_SECURITY_HASH_USAGE":
        interpretation = (
            "The hash-rule violation is confirmed, but its repository use is "
            "non-security behavior and no automatic migration is proposed."
        )
    elif status == "MULTI_REPOSITORY_MIGRATION_REQUIRES_COORDINATION":
        interpretation = (
            "The correction requires coordinated client/backend rollout and "
            "remains blocked from automatic publication."
        )
    elif status == "CONFIRMED_NON_SECURITY_SPEC_VIOLATION":
        interpretation = (
            "The literal specification violation is confirmed, but repository "
            "and upstream evidence classify this use as non-security behavior. "
            "No application code change is proposed."
        )
    else:
        interpretation = "The LLM analysis was not available as valid JSON."

    lines = [
        "<!-- LOCAL LLM ANALYSIS PREVIEW: not intended for publication. -->",
        "",
        f"# LLM analysis: `{metadata.get('method_name', 'unknown')}`",
        "",
        f"**Project:** `{metadata.get('apk_name', 'unknown')}`  ",
        f"**Class:** `{metadata.get('csv_class', 'unknown')}`  ",
        f"**Specification:** `{metadata.get('spec', 'unknown')}`  ",
        f"**Decision:** `{decision}`  ",
        f"**Review status:** `{status}`",
        "",
        "## Interpretation",
        "",
        interpretation,
        "",
        "## Evidence status",
        "",
    ]
    evidence_status = analysis.get("evidence_status", {}) or {}
    status_fields = (
        ("Cause", analysis.get("cause_status", evidence_status.get("cause_status", "unknown"))),
        ("Detector violation", analysis.get("detector_violation", evidence_status.get("detector_violation", "unknown"))),
        ("Runtime security impact", analysis.get("runtime_security_impact", evidence_status.get("runtime_security_impact", "unknown"))),
        ("Scope", evidence_status.get("scope_status", analysis.get("scope_assessment", {}).get("status", "unknown"))),
        ("Generation", analysis.get("generation_status", evidence_status.get("generation_status", "unknown"))),
        ("Publication", analysis.get("publication_status", evidence_status.get("publication_status", "unknown"))),
    )
    lines.extend(
        f"- **{label}:** `{value}`" for label, value in status_fields
    )
    diagnostic = analysis.get("diagnostic_evidence", {}) or {}
    if diagnostic:
        lines.extend([
            f"- **Diagnostic evidence:** `{diagnostic.get('evidence_id', 'unknown')}`",
            f"- **Diagnostic authority:** `{'informational_only' if diagnostic.get('informational_only') else 'claim_eligible'}`",
        ])
    lines.extend([
        "",
        "## Reported finding",
        "",
        str(metadata.get("error_message", "Not available.")),
        "",
        "## Confirmed cause",
        "",
        cause or "No root cause was confirmed.",
        "",
        "## Confirmed facts",
        "",
        markdown_list(analysis.get("confirmed_facts")),
        "",
        "## Unsupported hypotheses",
        "",
        markdown_list(analysis.get("unsupported_hypotheses")),
        "",
        "## Missing evidence",
        "",
        markdown_list(analysis.get("missing_evidence")),
        "",
        "## Predicate assessment",
        "",
    ])
    predicates = analysis.get("predicate_assessment", [])
    if isinstance(predicates, list) and predicates:
        for item in predicates:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "unknown"))
            lines.extend([
                f"### `{item.get('event', 'unknown')}` — {status}"
                f" ({PREDICATE_STATUS_MEANING.get(status, 'meaning not recorded')})",
                "",
                str(item.get("evidence", "No evidence recorded.")),
                "",
            ])
    else:
        lines.extend(["No predicate assessment was produced.", ""])

    # Given its own heading because it is the one part of the analysis the
    # headline decision does not speak for: the reported finding can be
    # dismissed while these stand.
    collateral = analysis.get("collateral_violations")
    if isinstance(collateral, list) and collateral:
        lines.extend([
            "## Other requirements this source appears to break",
            "",
            "These are requirements of the same specification that the "
            "reported finding is not about. The decision above answers for "
            "the reported condition only.",
            "",
        ])
        for item in collateral:
            if not isinstance(item, dict):
                continue
            lines.extend([
                f"- **{item.get('predicate', 'unknown')}**",
                f"  {item.get('explanation', 'No explanation recorded.')}",
                "",
            ])

    compatibility = analysis.get("forward_compatibility_assessment", {})
    if not isinstance(compatibility, dict):
        compatibility = {}
    migration = analysis.get("migration_assessment", {})
    if not isinstance(migration, dict):
        migration = {}
    lines.extend([
        "## Migration assessment",
        "",
        f"**Usage kind:** `{migration.get('usage_kind', 'unknown')}`  ",
        f"**Legacy compatibility required:** `{migration.get('legacy_compatibility_required', 'unknown')}`  ",
        f"**Policy authorized:** `{migration.get('policy_authorized', False)}`",
        "",
        f"**Safe strategy:** {migration.get('safe_strategy', 'Not established.')}",
        "",
        markdown_list(migration.get("evidence")),
        "",
        "## Forward compatibility",
        "",
        f"**Status:** `{compatibility.get('status', 'unknown')}`",
        "",
        str(compatibility.get("evidence", "No evidence recorded.")),
        "",
        f"**Strategy:** {compatibility.get('strategy', 'Not specified.')}",
        "",
        "## External sources",
        "",
    ])
    sources = analysis.get("external_sources", [])
    if isinstance(sources, list) and sources:
        for source in sources:
            if not isinstance(source, dict):
                continue
            title = str(source.get("title", "Source"))
            url = str(source.get("url", "")).strip()
            claim = str(source.get("supported_claim", "")).strip()
            lines.append(f"- [{title}]({url}) — {claim}" if url else f"- {title} — {claim}")
    else:
        lines.append("No external sources were recorded.")
    return "\n".join(lines).rstrip() + "\n"


LOCAL_REPOSITORY = "local checkout (no hosted repository)"


def preview_repository(metadata: dict) -> str:
    """owner/name of the hosted repository; a local checkout has none.

    A preview is read locally, so a finding in a checkout that is on no
    forge (a local app given to `cognalys run --repo`) still gets one; only
    publication needs the hosted repository.
    """
    try:
        _, owner, repo = IssueCreator.repository_from_metadata(metadata)
    except ValueError:
        if any(metadata.get(key) for key in ("repo_host", "repo_owner", "repo_name")):
            raise
        return LOCAL_REPOSITORY
    return f"{owner}/{repo}"


def markdown_preview(kind: str, repository: str, title: str, body: str) -> str:
    return (
        f"<!-- PREVIEW ONLY: this {kind} has not been published to GitHub. -->\n\n"
        f"# {title}\n\n"
        f"**Repository:** `{repository}`  \n"
        f"**Type:** {kind}\n\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )


# The prefix coordinated_generation gives a behaviour review's maintainer
# decision (test_github_previews checks that it still writes it).
MAINTAINER_DECISION_PREFIX = "For the maintainer to decide:"


def operator_notes(metadata: dict) -> list[str]:
    """What the operator should weigh before approving, never shown to the maintainer.

    The behaviour review's "for the maintainer to decide" notes, its warnings
    a person decides, the adjudication's human-decided publication points and
    the patch plan's publication blockers. Four "PR pronto" drafts of
    2026-10-27/28 carried such a note and no preview showed it; Trigger's said
    that a user picking the ED25519 option now gets an EC identity.
    """
    notes = []
    policy = metadata.get("ai_policy")
    if isinstance(policy, dict) and policy.get("kind") == "affirmation_required":
        # Shown first (App Manager, 2026-11-01): never a blocker, the user decides.
        notes.append(f"**WARNING: submitting affirms that no AI was used.** "
                     f"{policy.get('path', '')}: \"{policy.get('statement', '')}\" "
                     "Decide yourself whether to submit, and write the text yourself.")
    unreviewed = review_absent_by_design(metadata.get("fix_scope"),
                                         metadata.get("review_contract_version"))
    if unreviewed:
        notes.append(f"No behaviour review: {unreviewed}. Read the whole diff.")
    for value in metadata.get("review_assumptions") or []:
        text = str(value).strip()
        if text.startswith(MAINTAINER_DECISION_PREFIX):
            notes.append("Maintainer decision: "
                         + text[len(MAINTAINER_DECISION_PREFIX):].strip())
    for blocker in metadata.get("review_blockers") or []:
        if isinstance(blocker, dict) and blocker.get("maintainer_decides"):
            details = "; ".join(str(item) for item in blocker.get("details") or [])
            code = str(blocker.get("code", "review warning"))
            notes.append(f"`{code}`: {details}" if details else f"`{code}`")
        # A gate that no longer refuses the patch but leaves a point to weigh
        # (TLS_LEGACY_PROTOCOL_FILTER_MISSING: no socket filter was added).
        elif isinstance(blocker, dict) and str(blocker.get("publication_consideration", "")).strip():
            notes.append(f"`{blocker.get('code', 'review warning')}`: "
                         + str(blocker["publication_consideration"]).strip())
    for item in metadata.get("publication_considerations") or []:
        if isinstance(item, dict) and str(item.get("description", "")).strip():
            category = str(item.get("category", "")).strip()
            notes.append((f"{category}: " if category else "")
                         + str(item["description"]).strip())
    notes.extend(str(item).strip() for item in metadata.get("publication_blockers") or []
                 if str(item).strip())
    uncommitted = [str(path) for path in metadata.get("uncommitted_patch_files") or []]
    if uncommitted:
        notes.append("The worktree still has uncommitted edits to "
                     + ", ".join(f"`{path}`" for path in uncommitted)
                     + "; the diff below shows only the commit.")
    # What the repository's pull-request form asks that the pipeline cannot
    # answer truthfully (prs.pr_template; NotallyX#1167, 2026-10-30).
    template = metadata.get("pull_request_template") or {}
    if isinstance(template, dict):
        notes.extend(f"Pull-request template{' ' + template['path'] if template.get('path') else ''}: "
                     f"{note}" for note in template.get("notes") or [] if str(note).strip())
    return list(dict.fromkeys(notes))


def finding_review_preview(metadata: dict) -> str:
    """Render local publication gates and the exact finding approval flag."""
    finding_id = str(metadata.get("finding_id", "")).strip()
    solution_revision = str(metadata.get("solution_revision", "")).strip()
    preview_status = str(metadata.get("preview_status", "UNKNOWN")).strip()
    commit_hash = str(metadata.get("commit_hash", "")).strip()
    validation = metadata.get("build_validation", {})
    if not isinstance(validation, dict):
        validation = {}
    build_status = str(validation.get("status", "not_run")).strip() or "not_run"
    review_only = not FindingDisposition.from_metadata(metadata).publishable

    def yes_no(value: object) -> str:
        return "yes" if value is True else "no"

    lines = [
        "## Finding review",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Finding ID | `{finding_id or 'missing'}` |",
        f"| Solution revision | `{solution_revision or 'missing'}` |",
        f"| Preview status | `{preview_status or 'UNKNOWN'}` |",
        f"| Manual review required | `{yes_no(metadata.get('manual_review_required'))}` |",
        f"| Auto-apply allowed | `{yes_no(metadata.get('auto_apply'))}` |",
        f"| Publication allowed by analysis | `{yes_no(metadata.get('publication_allowed'))}` |",
        f"| Build validation | `{build_status}` |",
        f"| Commit | `{commit_hash or 'missing'}` |",
        "",
    ]
    # Drafted again after a refusal, or planned again (llm.generation_loops):
    # the drafts to read first. Said for a published draft too, whose
    # maintainer's answer is what tells whether those drafts fare worse.
    rounds = loop_depth_note(metadata.get("generation_loop"))
    if rounds:
        lines.insert(-1, f"| Draft rounds | `{rounds}` |")
    existing_pull_request = str(metadata.get("existing_open_pull_request", "")).strip()
    if existing_pull_request:
        lines.extend([
            "**An earlier run already published a pull request for this "
            f"finding: {existing_pull_request}.** Publishing this revision "
            "would open a second one, so it is refused unless "
            "`--allow-duplicate-pr` is passed. Update the existing pull "
            "request instead.",
            "",
        ])
    # Opened from this very file (publication_checks.published_pull_request,
    # which main records here): there is nothing left to approve, and a
    # stale-review banner would ask for a rerun of a published finding.
    published = str(metadata.get("published_pull_request", "")).strip()
    if published:
        lines.extend([
            f"**This draft is already published: {published}.** pr_creator "
            "will not publish it again (unless `--reprocess-existing` is "
            "passed); review the pull request there instead.",
            "",
        ])
        return "\n".join(lines)
    if rounds:
        lines.extend([
            f"**Read this draft first:** it stood only after {rounds}; the checks "
            "that refused this unit before passed it now, so read the diff "
            "closely before approving.",
            "",
        ])
    notes = operator_notes(metadata)
    if notes:
        lines.extend(["**Notes before approving** (for you; not part of the pull request):",
                      "", *[f"- {note}" for note in notes], ""])
    stale = stale_review(metadata.get("review_contract_version"),
                         metadata.get("behavior_review_recorded"),
                         fix_scope=metadata.get("fix_scope"))
    if stale and not existing_pull_request:
        lines.extend([
            f"**This draft did not pass the current behaviour review** ({stale}). "
            "pr_creator refuses to publish it and prints the command that runs "
            "the finding again; `--allow-stale-review` publishes it as it is.",
            "",
        ])
    if review_only and metadata.get("publication_promoted_after_validation") is True and finding_id:
        # utils.disposition.publication_after_validation cleared it once the
        # patch was built and reviewed; publishing is still the operator's call.
        lines.extend([
            "This draft was generated for review and passed every check after "
            "validation; it is recommended for publication with the notes "
            "below. Read the diff, then authorize this exact finding with:",
            "",
            "```bash",
            f"--approve-reviewed-finding {finding_id}",
            "```",
            "",
            *(["Notes from the verdict after validation:", "",
               *[f"- {note}" for note in metadata.get("publication_notes") or []], ""]
              if metadata.get("publication_notes") else []),
        ])
    elif review_only:
        build_passed = build_status.lower() in BUILD_VALIDATION_PASSING_STATUSES
        if finding_id and build_passed and commit_hash:
            lines.extend([
                "This artifact is review-only. After reviewing the evidence and "
                "diff, authorize this exact finding with:",
                "",
                "```bash",
                f"--approve-reviewed-finding {finding_id}",
                "```",
                "",
                "This flag is a publication authorization; build success alone "
                "does not approve the finding.",
                "",
            ])
            kept = [str(item) for item in metadata.get("publication_verdict_blocks") or []]
            if kept:
                lines.extend(["What keeps it review-only after validation:", "",
                              *[f"- {item}" for item in kept], ""])
        else:
            blockers = []
            if not finding_id:
                blockers.append("finding ID is missing")
            if not build_passed:
                blockers.append(f"build status is {build_status}")
            if not commit_hash:
                blockers.append("commit hash is missing")
            lines.extend([
                "This artifact is review-only and cannot be approved for "
                "publication yet: " + "; ".join(blockers) + ".",
                "",
            ])
    elif metadata.get("publishable") is True:
        lines.extend(["This artifact is marked ready for publication.", ""])
    else:
        lines.extend([
            "This artifact is not currently publishable. Check the status and "
            "validation fields above before proceeding.",
            "",
        ])
    return "\n".join(lines)


def pull_request_preview(
    repository: str,
    fallback_title: str,
    body: str,
    metadata: dict | None = None,
) -> str:
    """Place preview metadata below the PR body's heading without repeating it."""
    lines = body.strip().splitlines()
    if lines and lines[0].startswith("# "):
        heading = lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
        remaining_body = "\n".join(lines).rstrip()
    else:
        heading = f"# {fallback_title}"
        remaining_body = body.strip()

    review = finding_review_preview(metadata or {})
    marker = ("this Pull request was published from this draft"
              if str((metadata or {}).get("published_pull_request", "")).strip()
              else "this Pull request has not been published to GitHub")
    return (
        f"<!-- PREVIEW ONLY: {marker}. -->\n\n"
        f"{heading}\n\n"
        f"**Repository:** `{repository}`  \n"
        "**Type:** Pull request\n\n"
        f"{review}\n"
        "---\n\n"
        f"{remaining_body}\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate local Markdown previews of GitHub PRs and issues."
    )
    parser.add_argument("--pull-requests-dir", type=Path, required=True)
    parser.add_argument("--issue-candidates-dir", type=Path)
    parser.add_argument("--evidence-reviews-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--issue-title", required=True)
    # The contact-first issue of a draft without a confirmed security impact
    # (issues.issue_text.contact_issue_title).
    parser.add_argument("--issue-title-without-security-impact",
                        default=DEFAULT_TITLE_WITHOUT_SECURITY_IMPACT)
    parser.add_argument("--issue-body", required=True)
    # The style issue_creator publishes with, so the preview is the issue.
    parser.add_argument("--pr-issue-style", choices=("v1", "v2", "v3"), default="v1")
    parser.add_argument("--project", default="")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = sorted(args.pull_requests_dir.glob("*_pr.json"))
    if args.project:
        files = [
            file for file in files
            if file.name.startswith(f"{args.project}_")
        ]
    if args.limit > 0:
        files = files[: args.limit]

    prs_dir = args.output_dir / "pull_requests"
    issues_dir = args.output_dir / "issues"
    analyses_dir = args.output_dir / "analyses"
    prs_dir.mkdir(parents=True, exist_ok=True)
    issues_dir.mkdir(parents=True, exist_ok=True)
    analyses_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    issue_only_generated = 0
    analyses_generated = 0
    failed = 0
    for metadata_file in files:
        try:
            metadata = with_recorded_review_notes(
                json.loads(metadata_file.read_text(encoding="utf-8")), metadata_file)
            metadata["published_pull_request"] = published_pull_request(metadata_file)
            repository = preview_repository(metadata)
            source_file = IssueCreator.source_file_from_metadata(metadata)
            stem = safe_filename(metadata_file.stem.removesuffix("_pr"))

            pr_preview = pull_request_preview(
                repository,
                metadata["title"],
                metadata["body"],
                metadata,
            )
            pr_preview = pr_preview.rstrip() + code_change_preview(metadata)
            issue_body = IssueCreator.build_issue_body(
                args.issue_body,
                source_file,
                str(metadata.get("spec", "")).strip(),
                str(metadata.get("method_name", "")).strip(),
                str(metadata.get("csv_class", "")).strip(),
                str(metadata.get("error_message", "")).strip(),
                str(metadata.get("tool", "")).strip(),
                style=args.pr_issue_style,
            )
            issue_preview = markdown_preview(
                "Issue",
                repository,
                contact_issue_title(metadata, args.issue_title,
                                    args.issue_title_without_security_impact),
                issue_body,
            )

            (prs_dir / f"{stem}.md").write_text(pr_preview, encoding="utf-8")
            (issues_dir / f"{stem}.md").write_text(
                issue_preview,
                encoding="utf-8",
            )
            generated += 1
            print(f"PREVIEW: {repository} -> {stem}.md")
        except Exception as exc:
            failed += 1
            print(f"FAILED: {metadata_file.name}: {exc}")

    candidate_files = []
    if args.issue_candidates_dir and args.issue_candidates_dir.exists():
        candidate_files = sorted(
            args.issue_candidates_dir.rglob("*_issue.json")
        )
    if args.project:
        candidate_files = [
            file for file in candidate_files
            if file.parent.name == args.project
        ]
    for metadata_file in candidate_files:
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            repository = preview_repository(metadata)
            content = metadata.get("issue_content", {})
            title = str(content.get("title", args.issue_title)).strip()
            body = IssueCreator.build_no_safe_fix_body(
                args.issue_body, metadata, style=args.pr_issue_style
            )
            # issue_creator titles a constructor finding by its class.
            simple_class = str(metadata.get("csv_class", "")).split("$", 1)[0].rsplit(".", 1)[-1]
            if simple_class:
                title = re.sub(r"<(?:cl)?init>", simple_class, title)
            stem = finding_artifact_stem(
                metadata.get("apk_name", metadata_file.parent.name),
                metadata.get("method_name", metadata_file.stem),
                metadata.get("spec", ""),
            ) + "_no_safe_fix"
            preview = markdown_preview("Issue", repository, title, body)
            (issues_dir / f"{stem}.md").write_text(
                preview, encoding="utf-8"
            )
            issue_only_generated += 1
            print(f"ISSUE PREVIEW: {repository} -> {stem}.md")
        except Exception as exc:
            failed += 1
            print(f"FAILED: {metadata_file.name}: {exc}")

    evidence_files = []
    if args.evidence_reviews_dir and args.evidence_reviews_dir.exists():
        evidence_files = sorted(args.evidence_reviews_dir.rglob("*_evidence.json"))
    if args.project:
        evidence_files = [
            file for file in evidence_files if file.parent.name == args.project
        ]
    if args.limit > 0:
        evidence_files = evidence_files[: args.limit]
    for metadata_file in evidence_files:
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            metadata.setdefault("apk_name", metadata_file.parent.name)
            legacy_parts = str(metadata.get("error_id", "")).split(":::")
            if len(legacy_parts) >= 5:
                metadata.setdefault("csv_class", legacy_parts[0])
                metadata.setdefault("method_name", legacy_parts[1])
                metadata.setdefault("spec", legacy_parts[2])
                metadata.setdefault("error_message", legacy_parts[4])
            stem = finding_artifact_stem(
                metadata.get("apk_name", metadata_file.parent.name),
                metadata.get("method_name", metadata_file.stem),
                metadata.get("spec", ""),
            ) + "_analysis"
            (analyses_dir / f"{stem}.md").write_text(
                analysis_preview(metadata), encoding="utf-8"
            )
            analyses_generated += 1
            print(f"ANALYSIS PREVIEW: {stem}.md")
        except Exception as exc:
            failed += 1
            print(f"FAILED: {metadata_file.name}: {exc}")

    print(
        f"Preview summary: {generated} PR previews, "
        f"{generated + issue_only_generated} issue previews, "
        f"{analyses_generated} analysis previews, {failed} failed"
    )
    print(f"Output: {args.output_dir.resolve()}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
