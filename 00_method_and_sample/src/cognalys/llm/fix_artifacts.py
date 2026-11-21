#!/usr/bin/env python3
"""
Durable artifacts and telemetry for generated fixes.

Extracted from generate_error_fixes.py (Passo 5 of the
modularization plan). These build the human-facing explanation of a patch,
the deterministic audit telemetry that accompanies it, and the persisted
context snapshots that make a run reproducible after the fact.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

# Importable on its own, as the modules beside it are: the source root is what
# holds `utils`, and a caller that loads only src/llm does not put it on the path.

from cognalys.utils.pr_text import (  # noqa: E402
    changes_list,
    finding_paragraph,
    plain_explanation,
    short_symbol,
)


def format_elapsed_time(seconds: float) -> str:
    """Format a duration like the top-level pipeline, without 24h wrapping."""
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"


def _write_json_atomic(path: Path, value: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


def summarize_patch_changes(edits: List[Dict]) -> Dict:
    """Produce deterministic audit telemetry for a generated edit set."""
    changed_files = sorted({
        str(item.get("source_file_path", "")) for item in edits
        if isinstance(item, dict) and str(item.get("source_file_path", "")).strip()
    })
    changed_symbols = sorted({
        str(item.get("symbol", "")) for item in edits
        if isinstance(item, dict) and str(item.get("symbol", "")).strip()
    })
    changed_apis = sorted({
        str(item.get("symbol", "")) for item in edits
        if isinstance(item, dict)
        and item.get("edit_kind") in {"method", "declaration"}
        and re.search(
            r"\b(?:public|protected)\b",
            str(item.get("original_code", ""))
            + "\n" + str(item.get("corrected_code", "")),
        )
    })
    changed_constants = sorted({
        str(item.get("symbol", "")) for item in edits
        if isinstance(item, dict) and item.get("edit_kind") == "constant"
    })
    changed_dependencies = sorted({
        str(item.get("source_file_path", "")) for item in edits
        if isinstance(item, dict) and (
            Path(str(item.get("source_file_path", ""))).suffix.lower()
            in {".gradle", ".kts", ".toml", ".lockfile"}
            or "dependenc" in str(item.get("reason", "")).lower()
        )
    })
    changed_tests = sorted({
        str(item.get("source_file_path", "")) for item in edits
        if isinstance(item, dict) and (
            item.get("edit_kind") == "test"
            or re.search(
                r"(?:^|/)src/(?:test|androidTest)/",
                str(item.get("source_file_path", "")), re.IGNORECASE,
            )
        )
    })
    return {
        "changed_files": changed_files,
        "changed_symbols": changed_symbols,
        "changed_apis": changed_apis,
        "changed_constants": changed_constants,
        "changed_dependencies": changed_dependencies,
        "changed_tests": changed_tests,
    }


def coordinated_fix_explanation(
    error: Dict, edits: List[Dict], required_tests: List[str],
    review_assumptions: List[str], style: str = "v1",
) -> str:
    """Build a short, plain summary of a fix for persisted file drafts.

    ``required_tests`` and ``review_assumptions`` are accepted and deliberately
    not rendered. They used to become "Verification included" and "Residual
    review requirements" lists, which the PR body then repeated; a maintainer
    does not need the pipeline's checklist. Both are still recorded in the
    solution and execution JSON, where the operator reviews them before
    publishing.

    ``style`` no longer changes this output and is kept so existing callers
    keep working.
    """
    evidence = error.get("evidence_analysis", {}) or {}
    message = str(error.get("error_message", "")).strip()
    tool = str(error.get("tool", "")).strip()
    spec = str(error.get("spec", "")).strip()
    method = str(error.get("method_name", "")).strip()
    class_name = str(error.get("csv_class", "")).strip()
    cause = str(evidence.get("confirmed_cause", "")).strip()
    migration = evidence.get("migration_assessment", {}) or {}
    strategy = str(migration.get("safe_strategy", "")).strip()
    if not strategy:
        strategy = str(
            (error.get("case_migration_policy", {}) or {}).get("strategy", "")
        ).strip()

    lines = finding_paragraph(
        tool, spec, short_symbol(class_name, method), message, cause,
    )

    lines.append(
        cause or (
            "The repository evidence confirmed the reported specification "
            "violation, but did not provide a more specific runtime exception."
        )
    )

    if strategy:
        lines.extend(["", strategy])

    file_reasons = {}
    for edit in edits:
        path = str(edit.get("source_file_path", "")).strip()
        reason = str(edit.get("reason", "")).strip()
        if path:
            file_reasons.setdefault(path, [])
            if reason and reason not in file_reasons[path]:
                file_reasons[path].append(reason)
    changes = changes_list(file_reasons)
    if changes:
        lines.extend(["", *changes])

    return plain_explanation("\n".join(lines))


def _read_json_object(path: Path) -> Dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# Review statuses under which the behaviour review judged the draft. The same
# set as coordinated_generation's, which imports this module and so cannot be
# imported from here (test_publication_checks pins the two equal).
REVIEWED_STATUSES = frozenset({"reviewed", "all_edits_on_base"})


def review_judged_draft(metadata: Dict, review: Dict) -> bool:
    """Whether the recorded behaviour review judged this draft.

    A review file is written for an outage too (status not_run), and a draft
    completed through one records its contract version as None: neither
    passed the review, yet both were reported as a review that "predates the
    review contract versions". A draft from before versions were recorded has
    no such key; its review file then decides.
    """
    if "review_contract_version" in metadata and metadata["review_contract_version"] is None:
        return False
    version = metadata.get("review_contract_version", review.get("review_contract_version"))
    if "status" not in review and "review_contract_version" not in metadata:
        return bool(review)
    return bool(review) and (review.get("status") in REVIEWED_STATUSES
                             or version is not None)


def _blocker_record_of(evidence_analysis: Dict, review: Dict) -> Dict:
    """adjudication_rules.publication_blocker_record, from a behaviour review file."""
    from cognalys.llm.adjudication_rules import publication_blocker_record

    if not evidence_analysis:
        return {}
    summary = {
        "status": review.get("status", "") if review else "",
        "review_contract_version": review.get("review_contract_version") if review else None,
        "regression_count": len(review.get("regressions") or []) if review else 0,
        "patch_has_no_effect": bool(review.get("patch_has_no_effect")
                                    and not review.get("explicit_default_only")) if review else False,
    }
    return publication_blocker_record(evidence_analysis, summary if review else None)


def _solution_files(solutions_dir: Path, apk_name: str, csv_class: str,
                    method_name: str, spec: str):
    """(path, content) of each solution file the llm step wrote for a finding."""
    identity = (str(csv_class), str(method_name), str(spec))
    for path in sorted((Path(solutions_dir) / str(apk_name)).glob("*_solution.json")):
        data = _read_json_object(path)
        metadata = data.get("solution_metadata") or {}
        if (str(metadata.get("csv_class", "")), str(metadata.get("method_name", "")),
                str(metadata.get("spec", ""))) == identity:
            yield path, data


def _recorded_patch_code(data: Dict) -> List[str]:
    """The corrected code a solution file records: its coordinated edits'
    corrected_code, or its method body for a single-method fix."""
    implementation = data.get("corrected_implementation") or {}
    if not isinstance(implementation, dict):
        return []
    code = [str(edit.get("corrected_code", "")) for edit in implementation.get("coordinated_edits") or []
            if isinstance(edit, dict) and str(edit.get("corrected_code", "")).strip()]
    body = str(implementation.get("method_body", "") or "")
    return code or ([body] if body.strip() else [])


def recorded_solution(solutions_dir: Path, apk_name: str, csv_class: str,
                      method_name: str, spec: str, patch_code=None) -> Dict:
    """The solution file the llm step wrote for a finding, or {}.

    The execution report carries the applied edits but not the evidence
    analysis (plan invariants, forbidden changes, adjudication concerns) a
    later behaviour review of the same patch needs; validation.repair reads
    it from here. With several files for one finding, the first in name order
    need not be the revision that produced the patch (review of 2026-10-31):
    given the patch's corrected code, the one file recording exactly that code
    is returned, and {} when none or several do.
    """
    files = [data for _path, data in _solution_files(
        solutions_dir, apk_name, csv_class, method_name, spec)]
    if len(files) <= 1 or patch_code is None:
        return files[0] if len(files) == 1 or (files and patch_code is None) else {}
    wanted = sorted(str(code).strip() for code in patch_code if str(code).strip())
    matching = [data for data in files
                if sorted(code.strip() for code in _recorded_patch_code(data)) == wanted]
    return matching[0] if len(matching) == 1 else {}


def recorded_evidence_analysis(solutions_dir: Path, apk_name: str, csv_class: str,
                               method_name: str, spec: str) -> Dict:
    """The evidence analysis of the solution file naming this finding, or {}.

    The pull-request step's rows carry no path to it; the text checks read
    why the reported value is a problem from it (pr_text.value_problem_reasons).
    """
    for _path, data in _solution_files(solutions_dir, apk_name, csv_class, method_name, spec):
        evidence = data.get("evidence_analysis")
        return evidence if isinstance(evidence, dict) else {}
    return {}


def recorded_review_notes(solutions_dir: Path, apk_name: str, csv_class: str,
                          method_name: str, spec: str) -> Dict:
    """What the operator should read before approving a finding's draft.

    Read back from the solution file the llm step wrote under
    ``solutions_dir/<apk>/`` and from its behaviour review in
    ``coordinated_drafts``: the review's assumptions and warnings, the
    publication points only a human settles, the patch plan's publication
    blockers, and the review contract the draft passed. Trigger's createKeyPair
    (2026-10-28) carried seven plan blockers and a "for the maintainer to
    decide" note on its ED25519 option, and its preview showed none of them.
    An empty dict when no solution file names this finding.
    """
    for path, data in _solution_files(solutions_dir, apk_name, csv_class, method_name, spec):
        metadata = data.get("solution_metadata") or {}
        index = path.name.split("_", 1)[0]
        safe_method = re.sub(r"[^A-Za-z0-9._-]+", "_", str(method_name))
        review = _read_json_object(Path(solutions_dir) / "coordinated_drafts"
                                   / str(apk_name) / f"{index}_{safe_method}"
                                   / "behavior_review.json")
        plan = (((data.get("evidence_analysis") or {}).get("v2") or {})
                .get("patch_plan") or {})
        version = metadata.get("review_contract_version",
                               review.get("review_contract_version"))
        return {
            "review_assumptions": list(metadata.get("review_assumptions") or []),
            "review_blockers": list(metadata.get("review_blockers") or []),
            "publication_considerations": list(
                metadata.get("publication_considerations") or []),
            "publication_blockers": [str(item) for item in plan.get("publication_blockers") or []
                                     if str(item).strip()],
            "review_contract_version": version,
            "behavior_review_recorded": review_judged_draft(metadata, review),
            # The review file of an outage (B4): the review was asked and did
            # not run, which a draft from before the review cannot say.
            "behavior_review_not_run": review.get("status") == "not_run",
            "fix_scope": str(metadata.get("fix_scope", "") or ""),
            # A solution written before the record existed gets it from its
            # own evidence analysis and behaviour review file.
            "publication_blocker_record": metadata.get("publication_blocker_record")
            or _blocker_record_of(data.get("evidence_analysis") or {}, review),
            # Joint findings whose share of the patch has no effect
            # (coordinated_generation.review_facts_for_text): the PR text must
            # not claim them (pr_text.joint_finding_claims).
            **joint_without_effect_of(data),
            # Joint findings whose own statement the base still holds when the
            # unit's production edits were already there
            # (coordinated_generation.review_patch_behavior, tokn run_v6).
            **({"joint_findings_still_on_base": list(review["joint_findings_still_on_base"])}
               if review.get("joint_findings_still_on_base") else {}),
            # The rounds the unit's drafts went through, from the run's call
            # traces (generation_loops): read first when it is not empty.
            **_generation_loop_of(solutions_dir, csv_class, method_name, spec),
        }
    return {}


def _generation_loop_of(solutions_dir: Path, csv_class: str, method_name: str,
                        spec: str) -> Dict:
    """{"generation_loop": {...}} when the unit was drafted or planned again, else {}."""
    from cognalys.llm.generation_loops import finding_loop

    loop = finding_loop(Path(solutions_dir).parent / "application_results"
                        / "openai_prompt_logs", csv_class, method_name, spec)
    return {"generation_loop": loop} if loop else {}


def joint_without_effect_of(data: Dict) -> Dict:
    """The behaviour review's joint findings without effect, as a solution file records them.

    {} when there are none, so a draft without joint findings reads as before.
    """
    facts = (data.get("evidence_analysis") or {}).get("behavior_review") or {}
    entries = [item for item in facts.get("joint_findings_without_effect") or []
               if isinstance(item, dict)]
    reported = facts.get("reported_finding_without_effect")
    if not entries and not isinstance(reported, dict):
        return {}
    return {**({"joint_findings_without_effect": entries} if entries else {}),
            "joint_finding_count": int(facts.get("joint_finding_count") or 0),
            # The reported finding itself without effect, and the findings its
            # pull request is attributed to (LocalSend run_v4, 2026-10-31).
            **({"reported_finding_without_effect": reported}
               if isinstance(reported, dict) and reported else {}),
            **({"pr_attribution": facts["pr_attribution"]}
               if isinstance(facts.get("pr_attribution"), dict) else {})}


def with_recorded_review_notes(metadata: Dict, metadata_file: Path) -> Dict:
    """PR metadata with the review notes, read back for a file written before it carried them.

    ``metadata_file`` is the run's ``pull_requests/<apk>_<method>_<spec>_pr.json``;
    the notes come from the run's ``error_solutions``. What the file holds
    wins, so a hand-written title or body and the execution report's version
    stay. Whether a review judged the draft is read again from the review
    file, so an older PR file gets today's answer. The preview, pr_creator
    and the statistics read a draft through this one function: KeyPass's
    getKey PR file (2026-10-25) had no fix_scope, so pr_creator refused its
    single-method draft as stale while the preview called it unreviewed by
    design.
    """
    name = Path(metadata_file).name
    if ".apk_" not in name:
        return metadata
    apk = name.split(".apk_", 1)[0] + ".apk"
    # A pull request attributed to a joint finding names that finding; its
    # draft is stored under the analysed one (LocalSend run_v4, 2026-10-31).
    from cognalys.utils.pr_text import analysed_identity

    analysed = analysed_identity(metadata)
    recorded = recorded_review_notes(
        Path(metadata_file).parent.parent / "error_solutions", apk,
        analysed["csv_class"], analysed["method_name"], analysed["spec"],
    )
    if not recorded:
        return metadata
    # pr_generator.review_notes wrote this field from the same files.
    merged = {**recorded, **metadata,
              "behavior_review_recorded": recorded["behavior_review_recorded"]}
    # An empty value in the PR file is not an answer: the recorded one fills
    # it, as pr_generator.review_notes and the statistics' read_run do.
    for name in _RECORDED_WHEN_EMPTY:
        if merged.get(name) in (None, ""):
            merged[name] = recorded.get(name)
    return merged


# Review fields where a PR file's None or "" gives way to the solution file's value.
_RECORDED_WHEN_EMPTY = ("review_contract_version", "fix_scope")


_TIMING_KEYS = frozenset({"elapsed_seconds", "cache_hit"})


def _without_timings(value):
    """`value` without the measurements of how it was collected."""
    if isinstance(value, dict):
        return {key: _without_timings(item) for key, item in value.items()
                if key not in _TIMING_KEYS}
    if isinstance(value, list):
        return [_without_timings(item) for item in value]
    return value


def persist_context_snapshot(output_dir: Path, error: Dict,
                             apk_name: str, method_name: str) -> Path:
    """Persist the exact expanded context supplied to evidence analysis."""
    context = error.get("expanded_repository_context", {})
    directory = Path(output_dir) / "evidence_reviews" / apk_name
    directory.mkdir(parents=True, exist_ok=True)
    safe_method = re.sub(r"[^A-Za-z0-9._-]+", "_", method_name)
    path = directory / (
        f"{error.get('error_index', 0)}_{safe_method}_context.json"
    )
    # The fingerprint identifies the evidence, so it leaves out how long it
    # took to gather: repository_index.elapsed_seconds made two identical
    # contexts hash differently on every run, and the retrospective's
    # "same evidence, different verdict" comparison could never match.
    canonical = json.dumps(
        _without_timings(context), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    exact_sources = (
        context.get("multi_file_impact", {}).get("exact_candidate_sources", [])
        if isinstance(context, dict) else []
    )
    manifest = [
        {
            "source_file_path": item.get("source_file_path", ""),
            "characters": len(str(item.get("content", ""))),
            "sha256": hashlib.sha256(
                str(item.get("content", "")).encode("utf-8")
            ).hexdigest(),
        }
        for item in exact_sources if isinstance(item, dict)
    ]
    workspace = (
        Path(output_dir) / "finding_context" / apk_name
        / f"{error.get('error_index', 0)}_{safe_method}"
    )
    source_dir = workspace / "sources"
    source_records = []
    for index, item in enumerate(exact_sources, 1):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", ""))
        relative = str(item.get("source_file_path", ""))
        source_record = {
            "format_version": 1,
            "source_file_path": relative,
            "characters": len(content),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "relevance": item.get("relevance", []),
            "content": content,
        }
        safe_name = re.sub(
            r"[^A-Za-z0-9._-]+", "_", Path(relative).name or "source"
        )
        source_path = source_dir / f"{index:02d}_{safe_name}.json"
        _write_json_atomic(source_path, source_record)
        source_records.append({
            key: value for key, value in source_record.items() if key != "content"
        } | {"artifact": str(source_path)})

    # Persist relationships separately from full source text. This allows a
    # resumed or corrective pass to reuse the same immutable corpus without
    # relying on process memory or a previous conversational turn.
    relationship_context = json.loads(json.dumps(context, ensure_ascii=False))
    relationship_context.get("multi_file_impact", {}).pop(
        "exact_candidate_sources", None
    )
    relationships_path = workspace / "relationships.json"
    _write_json_atomic(relationships_path, {
        "format_version": 1,
        "expanded_repository_relationships": relationship_context,
    })
    workspace_manifest_path = workspace / "manifest.json"
    _write_json_atomic(workspace_manifest_path, {
        "format_version": 1,
        "error_id": error.get("unique_error_id", ""),
        "apk_name": apk_name,
        "method_name": method_name,
        "context_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "relationships_artifact": str(relationships_path),
        "sources": source_records,
    })
    error["finding_workspace_manifest"] = str(workspace_manifest_path)
    error["finding_workspace_identity"] = {
        "format_version": 1,
        "context_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "sources": [{
            "source_file_path": item["source_file_path"],
            "characters": item["characters"],
            "sha256": item["sha256"],
            "relevance": item["relevance"],
        } for item in source_records],
    }
    path.write_text(json.dumps({
        "snapshot_version": 1,
        "local_analysis_artifact": True,
        "error_id": error.get("unique_error_id", ""),
        "apk_name": apk_name,
        "method_name": method_name,
        "context_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "exact_source_manifest": manifest,
        "finding_workspace_manifest": str(workspace_manifest_path),
        "expanded_repository_context": context,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def compact_evidence_context(context: Dict) -> Tuple[Dict, List[Dict]]:
    """Separate exact sources and remove snippets duplicated by full files."""
    compact = json.loads(json.dumps(context, ensure_ascii=False))
    impact = compact.get("multi_file_impact", {})
    exact_sources = impact.pop("exact_candidate_sources", [])
    exact_paths = {
        str(item.get("source_file_path", ""))
        for item in exact_sources if isinstance(item, dict)
    }

    def strip_duplicate_snippets(value):
        if isinstance(value, list):
            return [strip_duplicate_snippets(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {
            key: strip_duplicate_snippets(child)
            for key, child in value.items()
            if key != "snippet"
        }
        if "snippet" in value and str(value.get("path", "")) not in exact_paths:
            result["snippet"] = value["snippet"]
        return result

    return strip_duplicate_snippets(compact), exact_sources
