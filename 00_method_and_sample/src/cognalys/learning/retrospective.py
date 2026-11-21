"""What a run teaches the next one: where each finding stopped, and why.

The fixes that moved Gallery's AsyncTask migration from abstention to a
generated patch were all found the same way: reading one run's artifacts by
hand, finding the stage that stopped the finding and the rule that stopped
it, and comparing with the run before. That reading is mechanical, so it is
done here, at the end of every run:

  * every finding gets a terminal stage (adjudication, plan, generation,
    build, repair, validated, published) and normalised failure signatures
    naming the rule that stopped it, with the artifact to open;
  * the run's funnel -- analysed, cause confirmed, generation supported,
    plan valid, generated, build passed, published -- and model-call
    signals, such as calls created on the server only after the whole
    timeout (a hung connection);
  * a ledger across runs says which signatures are new, which keep
    recurring, which reappeared, and which a rerun of the same finding no
    longer shows -- so a pipeline change is judged by whether its signature
    went away;
  * a finding whose verdict flipped between proposing a patch and abstaining
    is reported together with whether the evidence it was given was the same,
    because a flip on identical evidence is the model's variance, not a
    change in what is known about the code;
  * facts a deterministic signal verified (an import a passing build
    contained) and each finding's attempt history go to the knowledge the
    next run's planner, generator and repair stage read.

It is deterministic and never edits code or prompts: improving the pipeline
from what it reports stays a reviewed change.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from cognalys.evaluation.coverage import coverage_report, read_labels
from cognalys.evaluation.experiment_manifest import frozen as frozen_experiment
from cognalys.utils.disposition import BUILD_VALIDATION_PASSING_STATUSES
from cognalys.utils.finding_source import advisory_identifiers
from cognalys.utils.artifact_names import finding_artifact_stem, legacy_finding_artifact_stem
from cognalys.extraction.rename_safety import RENAME_RULES_VERSION
from cognalys.learning.knowledge import (
    DRAFT_CHECK_RULES_VERSION,
    KNOWLEDGE_FILE,
    LEDGER_FILE,
    PATCH_STAGES,
    VALIDATED_STAGES,
    finding_key_for,
    read_json,
    write_json_atomic,
)
from cognalys.workspace import workspace_root


# Reviewer labels, when they exist. Coverage is reported either way; only
# the avoidable-abstention figure depends on them, and it says so when they
# are absent rather than reporting a zero nobody measured.
DEFAULT_LABELS_FILE = (
    workspace_root() / "evals" / "ground_truth.csv"
)


PASSING_BUILD_STATUSES = BUILD_VALIDATION_PASSING_STATUSES
HUNG_FRACTION_OF_TIMEOUT = 0.9
DEFAULT_TIMEOUT_MS = 600_000
MAX_ATTEMPTS_PER_FINDING = 5
MAX_EXAMPLES_PER_SIGNATURE = 5
MAX_RUNS_PER_SIGNATURE = 30
MAX_RUNS_IN_LEDGER = 200
MAX_DETAIL_CHARACTERS = 300
_PLATFORM_IMPORTS = ("java.", "javax.", "android.", "androidx.", "kotlin.", "kotlinx.", "dalvik.")
_IMPORT_LINE = re.compile(
    r"^\s*import\s+(?:static\s+)?(?P<path>[\w$.]+)(?:\s+as\s+(?P<alias>[\w$]+))?\s*;?\s*$"
)
FUNNEL_STAGES = (
    "analysed", "cause_confirmed", "generation_supported", "plan_valid",
    "generated", "build_passed", "published",
)


REVISION_STOPPED_STATUSES = frozenset({"NO_SAFE_FIX", "FAILED"})

# Where a finding stopped tells what the pipeline concluded about it: it
# either went on to a patch, or stopped before one for want of a confirmed
# cause or a valid plan. Other stages (analysis failures, provider refusals)
# say nothing about the verdict and are left out of the comparison. The same
# set decides which earlier attempts planning and generation read
# (knowledge.finding_history).
PROPOSED_STAGES = PATCH_STAGES
ABSTAINED_STAGES = frozenset({"adjudication", "plan"})


def pr_filename_component(value: str, fallback: str = "change") -> str:
    """Mirror PrGenerator.safe_filename_component, which names PR artifacts.

    Looking PR files up by the raw method name never found a constructor's
    ("<init>" is written as "init"), so every such finding counted as
    unpublished even with its pull request open.
    """
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    return value.strip("._-") or fallback


def _pr_revision_record(results: Path, apk: str, method: str) -> dict:
    report = read_json(results / "fix_prs_report.json")
    for record in report.get("results") or []:
        if (isinstance(record, dict) and record.get("project") == apk
                and record.get("method_name") == method):
            return record
    return {}


def normalize_detail(text: str, limit: int = 120) -> str:
    """A stable signature fragment: identifiers, paths, literals and numbers masked."""
    value = str(text or "")
    value = re.sub(r"\b[A-Z]{2,}-\d+\b", "<id>", value)
    value = re.sub(r"\S*/\S*", "<path>", value)
    value = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", "<x>", value)
    value = re.sub(r"\d+", "<n>", value)
    value = re.sub(r"[\[\](){},;]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    # Trailing punctuation varies between otherwise identical messages.
    return value[:limit].strip(" .:;")


def _relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def _signal(signature: str, detail: str, artifact: Path, base: Path) -> dict:
    return {
        "signature": signature,
        "detail": str(detail or "")[:MAX_DETAIL_CHARACTERS],
        "artifact": _relative(artifact, base),
    }


def _failed_draft_issues(drafts_dir: Path, manifest: dict) -> list[tuple[str, Path]]:
    issues = []
    failed = set(manifest.get("failed_files") or [])
    for path in sorted(drafts_dir.glob("*.json")):
        if path.name in {"manifest.json", "semantic_validation.json"}:
            continue
        draft = read_json(path)
        if draft.get("source_file_path") in failed:
            for issue in draft.get("validation_issues") or []:
                issues.append((str(issue), path))
    return issues


def _outcome(run_dir: Path, dirs: dict, key: str, evidence: dict, apk: str,
             prefix: str, execution: dict | None) -> dict:
    solutions, pulls, results = dirs["error_solutions"], dirs["pull_requests"], dirs["application_results"]
    analysis = evidence.get("analysis") or {}
    method = str(evidence.get("method_name", ""))
    v2 = solutions / "v2_artifacts" / apk
    adjudication_path = v2 / f"{prefix}_adjudication.json"
    plan_path = v2 / f"{prefix}_patch_plan.json"
    adjudication = read_json(adjudication_path)
    plan = read_json(plan_path)
    drafts_dir = solutions / "coordinated_drafts" / apk / prefix
    manifest = read_json(drafts_dir / "manifest.json")
    semantic = read_json(drafts_dir / "semantic_validation.json")
    solution_path = solutions / apk / f"{prefix}_solution.json"
    pr_stem = finding_artifact_stem(apk, method, str(evidence.get("spec", "")))
    if not (pulls / f"{pr_stem}_pr.json").is_file():
        # A run made before the spec was part of the name.
        legacy = legacy_finding_artifact_stem(apk, method)
        if (pulls / f"{legacy}_pr.json").is_file():
            pr_stem = legacy
    pr_path = pulls / f"{pr_stem}_pr.json"
    created_path = pulls / f"{pr_stem}_pr_created.json"
    created = read_json(created_path)
    fix_path = results / "validation_fixes" / f"{key}_validation_fix.json"
    validation_fix = read_json(fix_path)
    build = (execution or {}).get("build_validation") or {}
    build_status = str(build.get("status", ""))

    generation_status = str(adjudication.get("generation_status") or analysis.get("generation_status") or "")
    plan_valid = bool(
        plan and str(plan.get("decision", "")) != "NO_SAFE_FIX"
        and plan.get("required_edits") and not plan.get("generation_blockers")
    )
    reached = {
        "analysed": True,
        "cause_confirmed": str(adjudication.get("cause_status") or analysis.get("cause_status") or "") == "confirmed",
        "generation_supported": generation_status in {"supported", "supported_for_review"},
        "plan_valid": plan_valid,
        "generated": solution_path.is_file() or bool(execution),
        "build_passed": build_status in PASSING_BUILD_STATUSES,
        "published": bool(created.get("html_url")),
    }

    signals: list[dict] = []
    evidence_path = solutions / "evidence_reviews" / apk / f"{prefix}_evidence.json"
    revision = _pr_revision_record(results, apk, method) if reached["published"] else {}
    revision_status = str(revision.get("status", ""))
    if revision_status in REVISION_STOPPED_STATUSES:
        # The pull request exists, but the latest round of maintainer feedback
        # was not addressed; "published" alone hid that the run did nothing.
        stage = "revision"
        if revision_status == "NO_SAFE_FIX":
            detail = str((revision.get("llm_revision") or {}).get("explanation", ""))
            signature = "revision:no_safe_fix"
        else:
            detail = str(revision.get("error", ""))
            signature = "revision:" + normalize_detail(
                revision.get("failure_stage") or detail.split(":", 1)[0] or "failed"
            )
        signals.append(_signal(signature, detail, results / "fix_prs_report.json", run_dir))
    elif reached["published"]:
        stage = "published"
    elif execution and reached["build_passed"]:
        stage = "validated"
    elif execution and build_status:
        stage = "build"
        diagnostics = [item for item in build.get("compiler_diagnostics") or []
                       if isinstance(item, dict)]
        first = diagnostics[0].get("message", "") if diagnostics else ""
        located = located_diagnostics(diagnostics, str(build.get("worktree_path", "")))
        # The other places the same build failed (BlueGate run_v5: the same
        # mismatch at AdminDashboardActivity.kt:244 and MainActivity.kt:288)
        # go in the one signal's detail, so the next run knows every call
        # site and the ledger counts one failed build once (review of batch
        # 13, 2026-10-31: one signal per place counted it three times).
        signals.append(_signal(
            "build:" + normalize_detail(first or build.get("reason", build_status)),
            (located[0][:220] + "".join(
                "; also at " + place.split(": ", 1)[0] for place in located[1:3])
             if located else (first or build.get("reason", ""))),
            Path(str(build.get("log_file", results))), run_dir,
        ))
        if validation_fix and validation_fix.get("status") != "success":
            stage = "repair"
            error = str(validation_fix.get("error", ""))
            signals.append(_signal(
                "repair:" + normalize_detail(error.split(":", 1)[0] or "failed"), error, fix_path, run_dir,
            ))
    elif execution and str(execution.get("status", "")) not in {"", "SUCCESS"}:
        stage = "apply"
        signals.append(_signal(
            "apply:" + normalize_detail(execution.get("error") or execution.get("status")),
            str(execution.get("error", "")), results / "execution_report.json", run_dir,
        ))
    elif manifest and (manifest.get("status") != "complete" or semantic.get("status") == "invalid"):
        stage = "generation"
        issues = _failed_draft_issues(drafts_dir, manifest)
        for issue, path in issues[:3]:
            signals.append(_signal("generation:" + normalize_detail(issue), issue, path, run_dir))
        for error in (semantic.get("errors") or [])[:3]:
            code = str(error.get("code", "semantic_validation")) if isinstance(error, dict) else str(error)
            signals.append(_signal(
                "generation:semantic:" + code.lower(), "; ".join(map(str, error.get("details", [])))
                if isinstance(error, dict) else "", drafts_dir / "semantic_validation.json", run_dir,
            ))
        if not signals:
            signals.append(_signal("generation:incomplete", manifest.get("status", ""), drafts_dir / "manifest.json", run_dir))
    elif plan and plan.get("generation_blockers"):
        stage = "plan"
        for blocker in (plan.get("generation_blockers") or [])[:3]:
            signals.append(_signal("plan:" + normalize_detail(blocker), blocker, plan_path, run_dir))
    elif generation_status == "blocked" or str(analysis.get("decision", "")).upper() == "NO_SAFE_FIX":
        stage = "adjudication"
        cause_status = str(adjudication.get("cause_status") or analysis.get("cause_status") or "unknown")
        if cause_status != "confirmed":
            signals.append(_signal(f"adjudication:cause_{cause_status}", adjudication.get("confirmed_cause", ""), adjudication_path, run_dir))
        for blocker in (adjudication.get("blocking_for_generation") or [])[:4]:
            if isinstance(blocker, dict):
                signals.append(_signal(
                    "adjudication:" + normalize_detail(blocker.get("kind") or blocker.get("description")),
                    blocker.get("description", ""), adjudication_path, run_dir,
                ))
        if not signals:
            signals.append(_signal("adjudication:no_safe_fix", analysis.get("confirmed_cause", ""), evidence_path, run_dir))
    elif reached["generated"]:
        # A solution exists and nothing downstream says what became of it.
        # Left without a signature, run_20260927_175748's Signal patch read
        # as an ordinary stopping point when the apply step had simply never
        # been run for its project.
        stage = "generated"
        if not execution:
            signals.append(_signal(
                "apply:not_run",
                "a solution was generated but no execution report entry "
                "records applying it", solution_path, run_dir,
            ))
        elif not build_status:
            signals.append(_signal(
                "build:not_run",
                "the solution was applied but no build validation is "
                "recorded for it", results / "execution_report.json", run_dir,
            ))
    else:
        stage = "analysis"
        signals.append(_signal("analysis:" + normalize_detail(evidence.get("status", "unclassified")), "", evidence_path, run_dir))
    attributed_away = (_attributed_elsewhere(read_json(pr_path), method, str(evidence.get("spec", "")))
                       if stage in {"validated", "published"} else "")
    if attributed_away:
        # Review of batch 13 (2026-10-31): the unit's pull request is
        # attributed to a joint finding (pr_text.attributed_view) because this
        # finding's code was left as the checker saw it. Recorded as validated
        # with the patch's title and files, the next planner for this finding
        # was told a draft of it had built, while the statistics said "sem
        # impacto".
        stage = "generation"
        signals = [_signal("generation:reported_finding_without_effect", attributed_away,
                           pr_path, run_dir)]

    context_path = solutions / "finding_context" / apk / prefix / "manifest.json"
    context = read_json(context_path)
    previews = run_dir / "previews"
    candidates = {
        "evidence_review": evidence_path,
        "evidence_context": context_path,
        "evidence_packet": v2 / f"{prefix}_evidence_packet.json",
        "external_research": v2 / f"{prefix}_external_research.json",
        "adjudication": adjudication_path,
        "patch_plan": plan_path,
        "coordinated_drafts": drafts_dir / "manifest.json",
        "solution": solution_path,
        "validation_feedback": results / "build_validation" / key / "validation_feedback.json",
        "validation_fix": fix_path,
        "pull_request": pr_path,
        "pull_request_created": created_path,
        "analysis_preview": previews / "analyses" / f"{apk}_{method}_analysis.md",
        "pull_request_preview": previews / "pull_requests" / f"{apk}_{method}.md",
    }
    # Only what exists, relative to the run, so the index can be moved with it.
    artifacts = {
        name: _relative(path, run_dir) for name, path in candidates.items() if path.is_file()
    }
    # What the draft that built was: its title and files (BlueGate,
    # 2026-10-31: a bare "validated" entry said nothing and pushed the
    # failures' lessons out of the history; knowledge.finding_history).
    pull = read_json(pr_path) if stage in {"validated", "published"} else {}
    draft = {"title": str(pull.get("title", "")),
             "files": [str(item) for item in pull.get("changed_files") or []][:8]} if pull else {}
    return {
        "finding_key": key,
        "artifacts": artifacts,
        **({"draft": draft} if draft and (draft["title"] or draft["files"]) else {}),
        "unique_error_id": str(evidence.get("error_id", "")),
        "apk": apk, "method": method, "spec": str(evidence.get("spec", "")),
        "decision": str(analysis.get("decision", "")),
        # The hash of the exact context the model was shown; equal hashes in two
        # runs mean any difference in the verdict did not come from the evidence.
        "evidence_fingerprint": str(context.get("context_sha256", "")),
        "terminal_stage": stage,
        "reached": reached,
        "signals": signals,
        "signatures": sorted({item["signature"] for item in signals}),
        "pull_request": str(created.get("html_url", "")) or (str(pr_path) if pr_path.is_file() else ""),
        "known_positive": known_positive(read_json(run_dir / "error_methods" / apk / f"{prefix}.json")),
    }


def _attributed_elsewhere(pull: dict, method: str, spec: str) -> str:
    """Why the finding's pull request names another finding of its unit, or ''.

    The pull-request file keeps the analysed identity as analysed_finding
    when the review found that finding without effect in its own patch.
    """
    analysed = (pull or {}).get("analysed_finding")
    if not isinstance(analysed, dict) or not analysed:
        return ""
    if (str(pull.get("method_name", "")), str(pull.get("spec", ""))) == (
            str(analysed.get("method_name", method)), str(analysed.get("spec", spec))):
        return ""
    reason = next((str(item.get("reason", "")).strip()
                   for item in pull.get("joint_findings_without_effect") or []
                   if isinstance(item, dict) and str(item.get("specification", "")) == spec
                   and pr_filename_component(str(item.get("method", "")))
                   == pr_filename_component(method)), "")
    return (f"the unit's patch resolved only {pull.get('method_name', '')} "
            f"[{pull.get('spec', '')}] and its pull request is that finding's; this "
            "finding's code was left as the checker saw it" + (f": {reason}" if reason else ""))


def located_diagnostics(diagnostics: list, worktree: str = "") -> list[str]:
    """Each compiler diagnostic as `path:line: message (source line)`, path relative
    to the worktree, one per distinct place.

    BlueGate run_v5/v6 (2026-10-31): the history kept only the first message,
    "Argument type mismatch: actual type is 'KeyStore.PrivateKeyEntry', but
    'KeyPair' was expected", without the Authenticator(...) call at
    AdminDashboardActivity.kt:244 and MainActivity.kt:288 that showed which
    constructor still needed a KeyPair.
    """
    root = worktree.rstrip("/") + "/" if worktree else ""
    found: list[str] = []
    for item in diagnostics or []:
        if not isinstance(item, dict) or not str(item.get("message", "")).strip():
            continue
        path = str(item.get("path", "")).replace("\\", "/")
        if root and path.startswith(root):
            path = path[len(root):]
        place = f"{path}:{item.get('line')}" if path and item.get("line") else path
        source = " ".join(str(item.get("source_line", "")).split())
        text = (f"{place}: " if place else "") + str(item["message"]).strip() + (
            f" ({source[:160]})" if source else "")
        if text not in found:
            found.append(text)
    return found


def known_positive(extracted: dict) -> dict:
    """The published advisory that makes this finding a known positive, if any.

    A finding that names a published advisory and was analysed at the
    revision that advisory describes is a vulnerability its own project
    confirmed and repaired. Ending without a patch on one is an abstention
    on a real, locally repairable defect -- measurable without any reviewer
    label. The revision matters: at the current branch the project may
    already carry the fix, and there abstaining is right.
    """
    error = extracted.get("error") or {}
    metadata = extracted.get("extraction_metadata") or {}
    advisories = advisory_identifiers({
        "error_message": error.get("error_message", ""),
        "unique_error_id": error.get("unique_error_id", ""),
    })
    pinned = str(metadata.get("pinned_revision", "") or "").strip()
    if not advisories or not pinned:
        return {}
    return {"advisories": advisories, "pinned_revision": pinned}


def collect_finding_outcomes(run_dir: Path, dirs: dict | None = None) -> list[dict]:
    run_dir = Path(run_dir)
    dirs = {
        "error_solutions": run_dir / "error_solutions",
        "application_results": run_dir / "application_results",
        "pull_requests": run_dir / "pull_requests",
        **{name: Path(path) for name, path in (dirs or {}).items() if path},
    }
    execution = read_json(dirs["application_results"] / "execution_report.json")
    by_finding = {
        str(item.get("finding_id", "")): item
        for item in execution.get("per_solution_details") or [] if isinstance(item, dict)
    }
    records: dict[str, dict] = {}
    for path in sorted((dirs["error_solutions"] / "evidence_reviews").glob("*/*_evidence.json")):
        evidence = read_json(path)
        if not evidence:
            continue
        apk = path.parent.name
        prefix = path.name[:-len("_evidence.json")]
        key = finding_key_for({
            "unique_error_id": evidence.get("error_id", ""), "apk_name": apk,
            "csv_class": evidence.get("csv_class", ""),
            "method_name": evidence.get("method_name", ""), "spec": evidence.get("spec", ""),
        })
        records[key] = _outcome(run_dir, dirs, key, evidence, apk, prefix, by_finding.get(key))
    tracking = read_json(dirs["error_solutions"] / "solutions_tracking.json")
    for failure in tracking.get("failures") or []:
        if not isinstance(failure, dict):
            continue
        key = finding_key_for({"unique_error_id": failure.get("finding_id", "")})
        kind = str(failure.get("kind", "failed"))
        if key in records:
            if kind == "provider_unavailable" and not records[key]["reached"].get("generated"):
                # Adjudicated, then stopped by an unavailable provider: its
                # stage is the failure, as the statistics say
                # (finding_statistics._final_outcome), not the decision its
                # evidence review recorded.
                signal = _signal(
                    f"{kind}:" + normalize_detail(failure.get("cause") or "no cause recorded"),
                    str(failure.get("cause", "")),
                    dirs["error_solutions"] / "solutions_tracking.json", run_dir)
                records[key]["terminal_stage"] = kind
                records[key]["signals"] = [signal]
                records[key]["signatures"] = [signal["signature"]]
            continue
        records[key] = {
            "finding_key": key, "unique_error_id": str(failure.get("finding_id", "")),
            "apk": str(failure.get("apk_name", "")), "method": str(failure.get("method_name", "")),
            "spec": "", "decision": str(failure.get("decision", "")),
            "terminal_stage": kind, "reached": {"analysed": False},
            "signals": [_signal(
                f"{kind}:" + normalize_detail(failure.get("cause") or "no cause recorded"),
                str(failure.get("cause", "")), dirs["error_solutions"] / "solutions_tracking.json", run_dir,
            )],
            "pull_request": "",
        }
        records[key]["signatures"] = [records[key]["signals"][0]["signature"]]
    return list(records.values())


INDEX_SCHEMA_VERSION = 1


def findings_index(run_id: str, outcomes: list[dict]) -> dict:
    """One small file per run: every finding, where it stopped, and its artifacts.

    A run's artifacts are spread over eight directories, and opening the right
    one for a finding meant knowing the layout. This is a derived view over
    that layout -- nothing reads it back and no artifact moves -- with paths
    relative to the run directory.
    """
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "run_id": run_id,
        "findings": [
            {
                "finding_key": outcome.get("finding_key", ""),
                "apk": outcome.get("apk", ""),
                "method": outcome.get("method", ""),
                "spec": outcome.get("spec", ""),
                "decision": outcome.get("decision", ""),
                "terminal_stage": outcome.get("terminal_stage", ""),
                "signatures": outcome.get("signatures", []),
                "evidence_fingerprint": outcome.get("evidence_fingerprint", ""),
                "pull_request": outcome.get("pull_request", ""),
                "artifacts": outcome.get("artifacts", {}),
            }
            for outcome in outcomes
        ],
    }


def funnel(outcomes: list[dict]) -> dict:
    return {
        stage: sum(1 for outcome in outcomes if (outcome.get("reached") or {}).get(stage))
        for stage in FUNNEL_STAGES
    }


def llm_performance(run_dir: Path, dirs: dict | None = None,
                    timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
    results = Path((dirs or {}).get("application_results") or Path(run_dir) / "application_results")
    per_stage: dict[str, dict] = {}
    for path in sorted((results / "openai_prompt_logs").glob("*_metadata.json")):
        metadata = read_json(path)
        if not metadata:
            continue
        stage = str(metadata.get("stage", "unknown"))
        entry = per_stage.setdefault(stage, {
            "calls": 0, "cache_hits": 0, "replays": 0, "latency_ms": 0, "hung_connections": 0,
            "failed": 0,
        })
        entry["calls"] += 1
        status = str(metadata.get("status", ""))
        if status == "cache_hit":
            entry["cache_hits"] += 1
        elif status == "finding_replay":
            # Answered by the finding's own identical earlier call, not sent.
            entry["replays"] += 1
        elif status == "research_corpus":
            # Answered from the research corpus (an earlier run's answer), not sent.
            entry["corpus_hits"] = entry.get("corpus_hits", 0) + 1
        elif status not in {"", "success"}:
            entry["failed"] += 1
        entry["latency_ms"] += int(metadata.get("latency_ms") or 0)
        created_after = metadata.get("server_created_after_ms")
        if isinstance(created_after, (int, float)) and created_after >= HUNG_FRACTION_OF_TIMEOUT * timeout_ms:
            entry["hung_connections"] += 1
    signals = [
        {"signature": f"llm:hung_connection:{stage}", "detail": f"{entry['hung_connections']} call(s)",
         "artifact": _relative(results / "openai_prompt_logs", Path(run_dir))}
        for stage, entry in sorted(per_stage.items()) if entry["hung_connections"]
    ] + [
        {"signature": f"llm:call_failed:{stage}", "detail": f"{entry['failed']} call(s)",
         "artifact": _relative(results / "openai_prompt_logs", Path(run_dir))}
        for stage, entry in sorted(per_stage.items()) if entry["failed"]
    ]
    return {"per_stage": per_stage, "signals": signals}


# A type a patch declares: `enum class Mode` and `fun interface Check` match
# at the second keyword.
_DECLARATION = re.compile(
    r"\b(?:class|interface|object|typealias|enum|record)\s+(?!class\b|interface\b)([A-Za-z_$][\w$]*)"
)


def patch_declared_symbols(edits) -> set[str]:
    """Types the edits declare that their original code did not.

    Tasks.org (run, 2026-10-25): the patch declared CustomCertManagerProvider
    in cert4android and imported it in the app; the build passed, the import
    became verified knowledge, and run_v2 was told to use exactly that import
    of a class only the unmerged patch held (Unresolved reference).
    """
    from cognalys.solutions.signature_change import blank_comments_and_strings

    declared: set[str] = set()
    for edit in edits or []:
        if not isinstance(edit, dict):
            continue
        before = set(_DECLARATION.findall(blank_comments_and_strings(str(edit.get("original_code", "")))))
        declared |= set(_DECLARATION.findall(
            blank_comments_and_strings(str(edit.get("corrected_code", ""))))) - before
    return declared


def _imported_name(line: str) -> str:
    match = _IMPORT_LINE.match(str(line))
    return match.group("path").rsplit(".", 1)[-1] if match else ""


def purge_patch_declared_imports(knowledge: dict, execution_report: dict,
                                 run_id: str = "") -> list[str]:
    """Drop stored imports of a type the same finding's patch declares.

    An entry is removed only when a patch in `execution_report` for the same
    repository and finding (any build status) declares the imported type,
    so knowledge recorded before patch_declared_symbols filtered the harvest
    is cleaned by reading the run that recorded it. With `run_id`, an entry
    another run recorded stays: a later run of the same finding may import
    the type once the earlier patch was merged, and that import is verified.
    Mutates `knowledge`; returns "<repository>: <import>" for each entry
    removed.
    """
    repositories = (knowledge or {}).get("repositories") or {}
    removed: list[str] = []
    for detail in execution_report.get("per_solution_details") or []:
        if not isinstance(detail, dict):
            continue
        repository = str(detail.get("apk_name", ""))
        stored = (repositories.get(repository) or {}).get("verified_imports") or {}
        declared = patch_declared_symbols(detail.get("coordinated_edits")) if stored else set()
        for symbol, entry in list(stored.items()):
            if (isinstance(entry, dict)
                    and entry.get("finding_key") == str(detail.get("finding_id", ""))
                    and (not run_id or entry.get("run", run_id) == run_id)
                    and _imported_name(entry.get("import", "")) in declared):
                del stored[symbol]
                removed.append(f"{repository}: {entry.get('import', '')}")
    return removed


def _added_imports(edit: dict) -> list[tuple[str, str]]:
    before = {line.strip() for line in str(edit.get("original_code", "")).splitlines()}
    added = []
    for line in str(edit.get("corrected_code", "")).splitlines():
        match = _IMPORT_LINE.match(line)
        if not match or line.strip() in before:
            continue
        path = match.group("path")
        if path.startswith(_PLATFORM_IMPORTS) or "." not in path:
            continue
        added.append((match.group("alias") or path.rsplit(".", 1)[-1], line.strip().rstrip(";")))
    return added


def harvest_knowledge(knowledge: dict, run_id: str, outcomes: list[dict],
                      execution_report: dict) -> tuple[dict, list[str]]:
    knowledge = dict(knowledge or {})
    knowledge["format_version"] = 1
    repositories = knowledge.setdefault("repositories", {})
    findings = knowledge.setdefault("findings", {})
    added: list[str] = [
        f"removed {item}: the same finding's patch declares it"
        for item in purge_patch_declared_imports(knowledge, execution_report, run_id)
    ]
    for detail in execution_report.get("per_solution_details") or []:
        if not isinstance(detail, dict):
            continue
        status = str((detail.get("build_validation") or {}).get("status", ""))
        if status not in PASSING_BUILD_STATUSES:
            continue
        repository = str(detail.get("apk_name", ""))
        imports = repositories.setdefault(repository, {}).setdefault("verified_imports", {})
        # A type the patch declares is in no checkout until it is merged.
        declared = patch_declared_symbols(detail.get("coordinated_edits"))
        for edit in detail.get("coordinated_edits") or []:
            if not isinstance(edit, dict):
                continue
            for symbol, line in _added_imports(edit):
                if _imported_name(line) in declared:
                    continue
                if (imports.get(symbol) or {}).get("import") != line:
                    added.append(f"{repository}: {line}")
                imports[symbol] = {
                    "import": line, "run": run_id,
                    "finding_key": str(detail.get("finding_id", "")), "build_status": status,
                }
    for outcome in outcomes:
        entry = findings.setdefault(outcome["finding_key"], {
            "apk": outcome.get("apk", ""), "method": outcome.get("method", ""), "attempts": [],
        })
        attempts = [item for item in entry.get("attempts") or [] if isinstance(item, dict)]
        attempt = {
            "run": run_id,
            # Which rename rules produced this outcome; knowledge.finding_history
            # drops rename failures recorded under older ones.
            "rename_rules_version": RENAME_RULES_VERSION,
            # Which draft checks refused it; knowledge.finding_history drops
            # refusals a later check change (DRAFT_CHECK_CHANGES) can affect.
            "draft_check_rules_version": DRAFT_CHECK_RULES_VERSION,
            "terminal_stage": outcome.get("terminal_stage", ""),
            "signatures": outcome.get("signatures", []),
            "details": [
                {"signature": item["signature"], "detail": item["detail"]}
                for item in outcome.get("signals", [])[:5]
            ],
            # The validated draft's title and files (knowledge.finding_history).
            **({"draft_title": (outcome.get("draft") or {}).get("title", ""),
                "draft_files": (outcome.get("draft") or {}).get("files", [])}
               if outcome.get("draft") else {}),
        }
        entry["attempts"] = kept_attempts(attempts, attempt)
    return knowledge, added


def kept_attempts(attempts: list[dict], attempt: dict) -> list[dict]:
    """A finding's stored attempts with ``attempt`` recorded, in run order.

    Review of batch 13 (2026-10-31): the store kept the last
    MAX_ATTEMPTS_PER_FINDING attempts of any kind, so validated runs still
    pushed the failures out before knowledge.finding_history could keep them
    apart (BlueGate getOrCreateKeyPair held exactly [v5 repair, v6 repair,
    v7, v8, v9]: a validated v10 would have dropped v5's lesson). The latest
    MAX_ATTEMPTS_PER_FINDING failures and validated attempts are kept
    separately. A run harvested again replaces its entry where it stands, so
    an old run re-read later is not taken for the newest attempt.
    """
    run = attempt.get("run")
    position = next((index for index, item in enumerate(attempts) if item.get("run") == run), None)
    if position is None:
        recorded = [*attempts, attempt]
    else:
        recorded = [attempt if index == position else item
                    for index, item in enumerate(attempts) if item.get("run") != run
                    or index == position]
    validated = [index for index, item in enumerate(recorded)
                 if str(item.get("terminal_stage", "")) in VALIDATED_STAGES]
    failed = [index for index in range(len(recorded)) if index not in set(validated)]
    kept = {*validated[-MAX_ATTEMPTS_PER_FINDING:], *failed[-MAX_ATTEMPTS_PER_FINDING:]}
    return [item for index, item in enumerate(recorded) if index in kept]


def update_ledger(ledger: dict, run_record: dict) -> dict:
    """Recompute the aggregate from the stored runs, so recording a run twice is harmless."""
    runs = [
        run for run in (ledger or {}).get("runs") or []
        if isinstance(run, dict) and run.get("run_id") != run_record["run_id"]
    ]
    runs.append(run_record)
    runs.sort(key=lambda run: str(run.get("recorded_at", "")))
    runs = runs[-MAX_RUNS_IN_LEDGER:]
    signatures: dict[str, dict] = {}
    for run in runs:
        run_id = run.get("run_id", "")
        for item in run.get("signals") or []:
            signature = item.get("signature", "")
            entry = signatures.setdefault(signature, {
                "stage": signature.split(":", 1)[0], "occurrences": 0, "runs": [],
                "first_seen": run_id, "last_seen": run_id, "examples": [],
            })
            entry["occurrences"] += 1
            if run_id not in entry["runs"]:
                entry["runs"].append(run_id)
            entry["last_seen"] = run_id
            entry["examples"] = (entry["examples"] + [{
                "run": run_id, "finding_key": item.get("finding_key", ""),
                "apk": item.get("apk", ""), "method": item.get("method", ""),
                "detail": item.get("detail", ""), "artifact": item.get("artifact", ""),
            }])[-MAX_EXAMPLES_PER_SIGNATURE:]
            entry["runs"] = entry["runs"][-MAX_RUNS_PER_SIGNATURE:]
    return {"format_version": 1, "runs": runs, "signatures": signatures}


def _verdict(stage: str) -> str:
    if stage in PROPOSED_STAGES:
        return "proposed"
    if stage in ABSTAINED_STAGES:
        return "abstained"
    return ""


def verdict_changes(current_findings: list[dict], earlier_runs: list[dict]) -> list[dict]:
    """Findings that went from a patch to an abstention, or back, between runs.

    Compared with the most recent earlier run in which the finding reached a
    verdict at all. `same_evidence` is True or False when both runs recorded
    the context hash, and None when either did not (an older ledger entry).
    """
    changes = []
    for finding in current_findings:
        now = _verdict(str(finding.get("terminal_stage", "")))
        if not now:
            continue
        for run in reversed(earlier_runs):
            past = next(
                (item for item in run.get("findings") or []
                 if item.get("finding_key") == finding.get("finding_key")
                 and _verdict(str(item.get("terminal_stage", "")))),
                None,
            )
            if past is None:
                continue
            if _verdict(str(past.get("terminal_stage", ""))) != now:
                before, after = past.get("evidence_fingerprint"), finding.get("evidence_fingerprint")
                changes.append({
                    "finding": f"{finding.get('apk')}/{finding.get('method')}",
                    "previous_run": run.get("run_id"),
                    "previous_stage": past.get("terminal_stage"),
                    "now_stage": finding.get("terminal_stage"),
                    "was": _verdict(str(past.get("terminal_stage", ""))), "now": now,
                    "same_evidence": (before == after) if before and after else None,
                })
            break
    return changes


def compare_with_history(ledger: dict, run_id: str) -> dict:
    runs = ledger.get("runs") or []
    index = next((position for position, run in enumerate(runs) if run.get("run_id") == run_id), None)
    if index is None:
        return {}
    current, earlier = runs[index], runs[:index]
    current_signatures = {item["signature"] for item in current.get("signals") or []}
    earlier_signatures = {item["signature"] for run in earlier for item in run.get("signals") or []}
    previous_signatures = {item["signature"] for item in (earlier[-1].get("signals") or [])} if earlier else set()
    resolved, progress = [], []
    for finding in current.get("findings") or []:
        key = finding.get("finding_key")
        history = [
            (run.get("run_id"), past)
            for run in earlier for past in run.get("findings") or [] if past.get("finding_key") == key
        ]
        if history:
            last_run, last = history[-1]
            for signature in sorted(set(last.get("signatures") or []) - set(finding.get("signatures") or [])):
                resolved.append({
                    "finding": f"{finding.get('apk')}/{finding.get('method')}",
                    "signature": signature, "previous_run": last_run,
                    "now_ends_at": finding.get("terminal_stage"),
                })
        progress.append({
            "finding": f"{finding.get('apk')}/{finding.get('method')}",
            "stages": [past.get("terminal_stage") for _run, past in history[-5:]] + [finding.get("terminal_stage")],
        })
    signatures = ledger.get("signatures") or {}
    return {
        "new": sorted(current_signatures - earlier_signatures),
        "recurring": sorted(
            (signature for signature in current_signatures
             if len((signatures.get(signature) or {}).get("runs") or []) >= 2),
            key=lambda signature: -len(signatures[signature]["runs"]),
        ),
        "reappeared": sorted(
            signature for signature in current_signatures & earlier_signatures
            if earlier and signature not in previous_signatures
        ),
        "resolved_for_rerun_findings": resolved,
        "finding_progress": progress,
        "verdict_changes": verdict_changes(current.get("findings") or [], earlier),
    }


def pipeline_commit(root: Path | None) -> dict:
    if not root:
        return {}
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        dirty = subprocess.run(
            ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", "src", "data/llm_policies"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return {"commit": head.stdout.strip(), "uncommitted_changes": dirty.returncode == 1}


def render_markdown(report: dict) -> str:
    lines = [f"# Retrospective: {report['run_id']}", ""]
    lines += ["## Funnel", "", "| Stage | Findings |", "|---|---|"]
    lines += [f"| {stage} | {count} |" for stage, count in report["funnel"].items()]
    coverage = report.get("coverage") or {}
    if coverage:
        total = coverage.get("findings_attributed", 0)
        lines += ["", "## Coverage", "",
                  f"Denominator: {total} finding(s) attributed to this run.", ""]
        lines += ["| Measure | Count | Rate |", "|---|---|---|"]
        for label, count_key, rate_key in (
            ("proposals (a patch exists)", "proposals", "proposal_coverage"),
            ("abstentions", "abstentions", "abstention_rate"),
            ("published", "published", "publication_rate"),
        ):
            rate = coverage.get(rate_key)
            lines.append(
                f"| {label} | {coverage.get(count_key, 0)} | "
                f"{'N/A' if rate is None else f'{rate:.0%}'} |"
            )
        actions = {
            name: count for name, count in (coverage.get("actions") or {}).items()
            if count
        }
        if actions:
            lines += ["", "Actions taken: " + ", ".join(
                f"`{name}` {count}" for name, count in sorted(actions.items())
            ), ""]
        avoidable = coverage.get("avoidable_abstention") or {}
        if avoidable.get("status") == "measured":
            rate = avoidable.get("rate")
            lines.append(
                f"Avoidable abstention: {avoidable['abstained_anyway']} of "
                f"{avoidable['labelled_repairable_in_this_run']} finding(s) a "
                "reviewer labelled locally repairable ended without a patch"
                + ("" if rate is None else f" ({rate:.0%})") + "."
            )
        else:
            lines.append(
                "Avoidable abstention: **unmeasured** — "
                + str(avoidable.get("detail", "no reviewer labels"))
            )
    known = coverage.get("known_positive_abstention") or {}
    if known.get("status") == "measured":
        lines.append(
            f"Abstention on known positives: {known['abstained']} of "
            f"{known['known_positives']} finding(s) that name a published "
            "advisory and were analysed at its vulnerable revision ended "
            "without a patch"
            + (f": {', '.join(known['advisories'])}" if known.get("advisories") else "")
            + "."
        )
    lines += ["", "## Where each finding stopped", ""]
    for outcome in report["findings"]:
        lines.append(f"- **{outcome['apk']}/{outcome['method']}** ended at `{outcome['terminal_stage']}`")
        for signal in outcome["signals"]:
            lines.append(f"  - `{signal['signature']}` — open `{signal['artifact']}`")
            if signal.get("detail"):
                lines.append(f"    > {signal['detail'][:200]}")
    comparison = report.get("comparison") or {}
    for title, key in (("New failure signatures", "new"), ("Recurring across runs", "recurring"),
                       ("Reappeared after being absent", "reappeared")):
        items = comparison.get(key) or []
        if items:
            lines += ["", f"## {title}", ""]
            signatures = report.get("ledger_signatures") or {}
            lines += [
                f"- `{signature}` — {len((signatures.get(signature) or {}).get('runs') or [])} run(s)"
                for signature in items
            ]
    if comparison.get("resolved_for_rerun_findings"):
        lines += ["", "## No longer seen when the same finding ran again", ""]
        lines += [
            f"- {item['finding']}: `{item['signature']}` (last in {item['previous_run']}; now ends at `{item['now_ends_at']}`)"
            for item in comparison["resolved_for_rerun_findings"]
        ]
    changes = comparison.get("verdict_changes") or []
    if changes:
        lines += ["", "## Verdict changed since an earlier run", ""]
        for item in changes:
            evidence = {
                True: "the model was given identical evidence, so the flip is model variance",
                False: "the evidence it was given also changed",
                None: "whether the evidence was the same was not recorded",
            }[item.get("same_evidence")]
            lines.append(
                f"- {item['finding']}: {item['was']} (`{item['previous_stage']}`, {item['previous_run']}) "
                f"→ {item['now']} (`{item['now_stage']}`); {evidence}."
            )
    progress = [item for item in comparison.get("finding_progress") or [] if len(item["stages"]) > 1]
    if progress:
        lines += ["", "## Stage history of findings run before", ""]
        lines += [f"- {item['finding']}: " + " → ".join(item["stages"]) for item in progress]
    llm = report.get("llm_performance", {}).get("per_stage") or {}
    if llm:
        lines += ["", "## Model calls", "",
                  "| Stage | Calls | Cache hits | Replays | Minutes | Hung | Failed |",
                  "|---|---|---|---|---|---|---|"]
        lines += [
            f"| {stage} | {entry['calls']} | {entry['cache_hits']} | {entry.get('replays', 0)} "
            f"| {entry['latency_ms'] / 60000:.1f} "
            f"| {entry['hung_connections']} | {entry['failed']} |"
            for stage, entry in sorted(llm.items())
        ]
    if report.get("knowledge_added"):
        lines += ["", "## Verified knowledge recorded for later runs", ""]
        lines += [f"- {item}" for item in report["knowledge_added"]]
    lines += [
        "", "---", "",
        "Signatures come from deterministic rules over this run's artifacts. "
        "A pipeline change is working when its signature stops appearing for "
        "the findings it targeted.",
    ]
    return "\n".join(lines) + "\n"


def run_retrospective(run_dir: Path, learning_directory: Path, pipeline_root: Path | None = None,
                      dirs: dict | None = None, run_id: str | None = None,
                      labels_file: Path | None = None) -> dict:
    run_dir = Path(run_dir)
    run_id = run_id or run_dir.name
    outcomes = collect_finding_outcomes(run_dir, dirs)
    performance = llm_performance(run_dir, dirs)
    coverage = coverage_report(outcomes, read_labels(labels_file or DEFAULT_LABELS_FILE))
    results_dir = Path((dirs or {}).get("application_results") or run_dir / "application_results")
    execution = read_json(results_dir / "execution_report.json")
    signals = [
        {**signal, "finding_key": outcome["finding_key"], "apk": outcome["apk"], "method": outcome["method"]}
        for outcome in outcomes for signal in outcome["signals"]
    ] + performance["signals"]
    run_record = {
        "run_id": run_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": pipeline_commit(pipeline_root),
        "funnel": funnel(outcomes),
        "coverage": coverage,
        "findings": [
            {
                key: outcome.get(key, "")
                for key in ("finding_key", "apk", "method", "terminal_stage", "signatures",
                            "evidence_fingerprint")
            }
            for outcome in outcomes
        ],
        "signals": signals,
        "llm_per_stage": performance["per_stage"],
    }
    learning_directory = Path(learning_directory)
    ledger = update_ledger(read_json(learning_directory / LEDGER_FILE), run_record)
    knowledge, added = harvest_knowledge(
        read_json(learning_directory / KNOWLEDGE_FILE), run_id, outcomes, execution,
    )
    # A frozen experiment reports on itself and leaves the store as it
    # found it: a run that updates the ledger changes the conditions of
    # every run after it, and comparing conditions is the whole point.
    if frozen_experiment():
        report_only = True
    else:
        report_only = False
        write_json_atomic(learning_directory / LEDGER_FILE, ledger)
        write_json_atomic(learning_directory / KNOWLEDGE_FILE, knowledge)
    report = {
        "run_id": run_id,
        "pipeline": run_record["pipeline"],
        "funnel": run_record["funnel"],
        "coverage": coverage,
        "findings": outcomes,
        "llm_performance": performance,
        "comparison": compare_with_history(ledger, run_id),
        "ledger_signatures": {
            signature: ledger["signatures"][signature]
            for signature in {item["signature"] for item in signals}
        },
        "knowledge_added": added,
        "learning_store_written": not report_only,
    }
    output_dir = run_dir / "retrospective"
    write_json_atomic(output_dir / "retrospective.json", report)
    write_json_atomic(run_dir / "findings_index.json", findings_index(run_id, outcomes))
    markdown_path = output_dir / "retrospective.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    stopped = {}
    for outcome in outcomes:
        stopped[outcome["terminal_stage"]] = stopped.get(outcome["terminal_stage"], 0) + 1
    report["markdown_path"] = str(markdown_path)
    report["summary_line"] = (
        f"{len(outcomes)} finding(s): "
        + (", ".join(f"{count} at {stage}" for stage, count in sorted(stopped.items())) or "none")
        + f"; {len(report['comparison'].get('new', []))} new and "
        f"{len(report['comparison'].get('recurring', []))} recurring failure signature(s)"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """`python -m cognalys.learning.retrospective purge-patch-declared-imports RUN...`

    Applies purge_patch_declared_imports to the knowledge store with each
    run's execution report: the runs that recorded an import are the ones
    whose patch may have declared it. Nothing else in the store changes.
    """
    import argparse

    from cognalys.learning.knowledge import learning_dir

    parser = argparse.ArgumentParser(prog="python -m cognalys.learning.retrospective")
    commands = parser.add_subparsers(dest="command", required=True)
    purge = commands.add_parser("purge-patch-declared-imports")
    purge.add_argument("runs", nargs="+", type=Path)
    purge.add_argument("--learning-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    path = learning_dir(args.learning_dir) / KNOWLEDGE_FILE
    knowledge = read_json(path)
    removed = []
    for run in args.runs:
        # The run id run_retrospective records: the run directory's name.
        removed += purge_patch_declared_imports(
            knowledge, read_json(Path(run) / "application_results" / "execution_report.json"),
            Path(run).resolve().name)
    for item in removed:
        print(f"removed {item}")
    if removed and not frozen_experiment():
        write_json_atomic(path, knowledge)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
