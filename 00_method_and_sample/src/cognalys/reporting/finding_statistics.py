#!/usr/bin/env python3
"""Descriptive statistics of pipeline runs over an imported findings CSV.

Reads only the artefacts a run already wrote (error_methods, evidence
reviews, solutions, execution and build reports, pull-request metadata) and
the findings CSV the run consumed, and writes Markdown and CSV tables:

  1. funnel      -- how many findings reach each stage, with Wilson 95%
                    intervals, overall and by origin / outcome / label;
  2. agreement   -- the pipeline's detector_violation against the package's
                    machine-readable labels, and against human verdicts when
                    an annotation file is supplied (see --annotations);
  3. stability   -- the same findings across two or more runs: identical
                    decisions and Cohen's kappa on detector_violation;
  6. builds      -- why each validated patch did or did not build.

Nothing here judges a finding. The human verdict (R/W/F, see the package's
group guide, section 8.1) is read from an annotation file the user fills;
--write-annotation-template writes one with every finding and an empty
verdict column.

Usage:
    python3 -m cognalys.reporting.finding_statistics --runs outputs/run_A outputs/run_B \
        --findings-csv data/processed/rv_import_findings.csv \
        [--annotations data/raw/exp_novo/verdicts.csv] \
        [--write-annotation-template data/raw/exp_novo/verdicts_template.csv] \
        --output-dir outputs/statistics
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from cognalys.llm.generation_loops import loop_depth_label
from cognalys.sources import read_rows
from cognalys.utils.pr_text import (
    analysed_identity, joint_finding_without_effect, merged_joint_without_effect)
from cognalys.utils.disposition import (
    BUILD_VALIDATION_PASSING_STATUSES,
    PRODUCTION_ONLY_TEST_BUILD_PREEXISTING,
    external_contract_decision,
    repaired_patch_review_blocks,
)

Key = Tuple[str, str, str, str]  # apk, class, method, spec

STAGES = (
    ("imported", "importado"),
    ("analysed", "analisado pelo LLM"),
    ("violation_confirmed", "violação confirmada"),
    ("fix_proposed", "correção proposta"),
    ("solution", "solução gerada"),
    ("applied", "aplicada na worktree"),
    ("built", "build ok"),
    ("pr_ready", "PR pronto (commit)"),
    # The two parts of "PR pronto", which keeps the total comparable with
    # earlier counts: the drafts pr_generator found publishable (directly, or
    # cleared after validation, READY_FOR_PUBLICATION_WITH_NOTES) and the ones
    # still kept for review. Both need an explicit approval to be published.
    ("pr_ready_publishable", "PR pronto (publicável)"),
    ("pr_ready_review", "PR pronto (revisão)"),
)
FIX_DECISIONS = {
    "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW", "SINGLE_FILE_FIX", "SAFE_FIX",
    "SAFE_INTERNAL_DIGEST_MIGRATION", "DEPENDENCY_FIX_PROPOSED",
}
# The statuses pr_creator accepts, so "PR pronto" counts what can be approved.
PASSING_BUILDS = set(BUILD_VALIDATION_PASSING_STATUSES)
# The pass whose test build fails on the base commit before any test compiles
# (validation.applied_solutions.settle_test_build_failing_on_base).
PRODUCTION_ONLY_BUILD = PRODUCTION_ONLY_TEST_BUILD_PREEXISTING
# The detail of a "PR pronto" draft that did not pass the current behaviour
# review (publication_checks.stale_review). The category stays, so counts
# remain comparable across runs and with the paper's.
STALE_REVIEW_DETAIL = "revisão anterior: reexecutar"
# A draft no behaviour review judged (the review did not run, B4): the same
# rerun is due, said apart from a review that is only older.
NOT_RUN_REVIEW_DETAIL = "revisão não executada: reexecutar"
# A draft from a fix path that has no behaviour review
# (publication_checks.review_absent_by_design): a rerun would not add one.
UNREVIEWED_PATH_DETAIL = "caminho sem revisão de comportamento"
ENVIRONMENT_SIGNATURES = (
    ("ferramenta ausente", r"Required command is not installed|command not found"),
    ("processador de anotações", r"AnnotationProcessor|kapt|hiltJavaCompile"),
    ("dependência não resolvida", r"Could not resolve|Could not find .*:.*:"),
    ("SDK/NDK ausente", r"SDK location not found|NDK .* not (?:found|installed)"),
    ("configuração do projeto", r"A problem occurred (?:evaluating|configuring)"),
)


# ---------------------------------------------------------------- utilities

def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def wilson(successes: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a proportion; (0, 0) when total is 0."""
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def cohen_kappa(pairs: List[Tuple[str, str]]) -> Optional[float]:
    if not pairs:
        return None
    total = len(pairs)
    observed = sum(a == b for a, b in pairs) / total
    first = Counter(a for a, _ in pairs)
    second = Counter(b for _, b in pairs)
    expected = sum(first[c] * second[c] for c in set(first) | set(second)) / total ** 2
    return None if expected == 1 else (observed - expected) / (1 - expected)


def percent(value: float) -> str:
    return f"{100 * value:.1f}%"


def markdown_table(header: List[str], rows: Iterable[List]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def write_csv(path: Path, header: List[str], rows: Iterable[List]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


# ------------------------------------------------------------- run reading

def read_findings(path: Path) -> Dict[Key, Dict[str, str]]:
    return {
        (row["apk"], row["class"], row["method"], row["spec"]): row
        for row in read_rows(path)
    }


EVIDENCE_KINDS = {
    "gradle_artifact_cache": "bytecode (cache do Gradle)",
    "library_repository_download": "bytecode (baixado do repositório Maven)",
    "repository_binary": "bytecode (binário no repositório)",
    "analysed_apk": "bytecode (APK analisado)",
}


def _key_of_finding_id(apk: str, finding_id: str, spec: str = "") -> Optional[Key]:
    parts = str(finding_id or "").split(":::")
    if len(parts) < 3 or not apk:
        return None
    return (apk, parts[0], parts[1], spec or parts[2])


def _not_analysable_category(kind: str, cause: str) -> str:
    cause = (cause or "").lower()
    if kind == "crashed":
        return "falha na chamada ao LLM" + (" (rede)" if "connection" in cause else "")
    if kind == "provider_unavailable":
        return "falha na chamada ao LLM (provedor indisponível)"
    if "providerrequestrejected" in cause:
        return "pedido recusado pelo provedor"
    if "declares no method" in cause or "method_not_found" in cause:
        return "método não encontrado no código"
    if "external_dependency_candidate" in cause or "no source for the reported target" in cause:
        return "biblioteca sem código nem bytecode"
    if "not in the analysed checkout" in cause or "application_class" in cause:
        return "classe do app ausente do checkout"
    return kind or "outro"


def _diff_size(diff: str) -> Tuple[int, int]:
    added = removed = 0
    for line in str(diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def read_run_costs(run: Path) -> Dict:
    """LLM budget and step durations a run recorded."""
    tracking = read_json(run / "error_solutions" / "solutions_tracking.json") or {}

    def find(value, name):
        if isinstance(value, dict):
            for key, inner in value.items():
                if key == name:
                    return inner
                found = find(inner, name)
                if found is not None:
                    return found
        return None

    budget = find(tracking, "llm_budget") or {}
    timings = read_json(run / "step_timings.json") or {}
    steps = {step.get("step"): float(step.get("seconds", 0) or 0)
             for step in timings.get("steps", []) or []}
    return {"llm": budget, "steps": steps}


def read_run(run: Path) -> Dict[Key, Dict]:
    """Every finding of a run with the furthest stage it reached."""
    records: Dict[Key, Dict] = {}
    index_to_key: Dict[Tuple[str, str], Key] = {}
    for path in sorted(run.glob("error_methods/*/*.json")):
        data = read_json(path) or {}
        meta, error = data.get("extraction_metadata", {}), data.get("error", {})
        key = (meta.get("apk_name", ""), error.get("csv_class", ""),
               error.get("csv_method", ""), error.get("spec", ""))
        index_to_key[(key[0], str(meta.get("csv_index", "")))] = key
        bytecode = ((data.get("dependency_context") or {})
                    .get("external_target_bytecode") or {})
        if ((data.get("source_code") or {}).get("method_body")):
            evidence = "código-fonte"
        elif bytecode.get("status") == "available":
            evidence = EVIDENCE_KINDS.get(bytecode.get("source", ""), "bytecode")
        else:
            evidence = "sem código"
        records[key] = {"extraction_status": meta.get("extraction_status", ""),
                        "evidence": evidence, "run": run.name}
    tracking = read_json(run / "error_solutions" / "solutions_tracking.json") or {}
    failures = []

    def collect(value):
        if isinstance(value, dict):
            for name, inner in value.items():
                if name == "failures" and isinstance(inner, list):
                    failures.extend(item for item in inner if isinstance(item, dict))
                else:
                    collect(inner)

    collect(tracking)
    for failure in failures:
        key = _key_of_finding_id(failure.get("apk_name", ""), failure.get("finding_id", ""))
        if key and key in records:
            records[key]["not_analysed_reason"] = _not_analysable_category(
                str(failure.get("kind", "")), str(failure.get("cause", "")))
            # Kept for an analysed finding too: one adjudicated and then
            # stopped by an unavailable provider is not concluded
            # (_final_outcome), whatever its decision said.
            records[key]["failure_kind"] = str(failure.get("kind", ""))
    for path in sorted(run.glob("error_solutions/evidence_reviews/*/*_evidence.json")):
        data = read_json(path) or {}
        analysis = data.get("analysis", {}) or {}
        decision = analysis.get("decision")
        if isinstance(decision, dict):
            decision = decision.get("decision")
        key = (data.get("apk_name", ""), data.get("csv_class", ""),
               data.get("method_name", ""), data.get("spec", ""))
        records.setdefault(key, {}).update(
            analysed=True, decision=decision or "",
            detector_violation=analysis.get("detector_violation", "") or "",
            cause_status=analysis.get("cause_status", "") or "",
            security_impact=analysis.get("runtime_security_impact", "") or "",
        )
    for path in sorted(run.glob("error_solutions/*.apk/*_solution.json")):
        index = path.name.split("_", 1)[0]
        key = index_to_key.get((path.parent.name, index))
        if key:
            notes = recorded_review_notes(run / "error_solutions", *key)
            records.setdefault(key, {}).update(
                solution=True,
                review_contract_version=notes.get("review_contract_version"),
                behavior_review_recorded=notes.get("behavior_review_recorded"),
                fix_scope=notes.get("fix_scope", ""),
            )
            if notes.get("behavior_review_not_run"):
                records[key]["behavior_review_not_run"] = True
            if notes.get("joint_findings_without_effect"):
                records[key]["joint_findings_without_effect"] = notes[
                    "joint_findings_without_effect"]
            if notes.get("pr_attribution"):
                records[key]["pr_attribution"] = notes["pr_attribution"]
            if notes.get("joint_findings_still_on_base"):
                records[key]["joint_findings_still_on_base"] = notes[
                    "joint_findings_still_on_base"]
            if notes.get("generation_loop"):
                # Drafted again after a refusal, or planned again
                # (llm.generation_loops): the "PR pronto" to read first.
                records[key]["generation_loop"] = notes["generation_loop"]
    report = read_json(run / "application_results" / "execution_report.json") or {}
    for item in report.get("per_solution_details", []) or []:
        key = (item.get("apk_name", ""), item.get("csv_class", ""),
               item.get("method_name", ""), item.get("spec", ""))
        validation = item.get("build_validation") or {}
        records.setdefault(key, {}).update(
            apply_status=item.get("status", ""), apply_error=item.get("error") or "",
            base_port=str((item.get("base_port") or {}).get("decision", "") or ""),
            submodule=str(((item.get("contribution_base") or {}).get("submodule") or {})
                          .get("url", "") or ""),
            build_status=validation.get("status", ""),
            build_attribution=validation.get("failure_attribution", ""),
            finding_id=item.get("finding_id", ""),
        )
        if item.get("pr_attribution") and not records[key].get("pr_attribution"):
            records[key]["pr_attribution"] = item["pr_attribution"]
        if item.get("joint_findings_without_effect"):
            # What the review of a repaired patch found
            # (validation.repair.record_repair_review), with the draft's own.
            records[key]["joint_findings_without_effect"] = merged_joint_without_effect(
                records[key].get("joint_findings_without_effect"),
                item["joint_findings_without_effect"])
    for path in sorted(run.glob("error_solutions/issue_candidates/*/*_issue.json")):
        data = read_json(path) or {}
        key = (data.get("apk_name", "") or path.parent.name, data.get("csv_class", ""),
               data.get("method_name", ""), data.get("spec", ""))
        if key not in records:
            key = index_to_key.get((path.parent.name, path.name.split("_", 1)[0]), key)
        owner, name = data.get("repo_owner", ""), data.get("repo_name", "")
        records.setdefault(key, {}).update(
            issue=True, issue_target=data.get("issue_target", ""),
            issue_kind=data.get("issue_kind", ""),
            issue_repository=f"{owner}/{name}" if owner and name else "",
            method_changed_upstream=bool(data.get("method_changed_upstream")),
        )
    # The issue a finding would have raised about a statement no longer on the
    # contribution base (issue_candidates.NOT_APPLICABLE_TO_BASE_SUFFIX).
    for path in sorted(run.glob("error_solutions/issue_candidates/*/*_not_applicable_to_base.json")):
        data = read_json(path) or {}
        key = (data.get("apk_name", "") or path.parent.name, data.get("csv_class", ""),
               data.get("method_name", ""), data.get("spec", ""))
        if key not in records:
            key = index_to_key.get((path.parent.name, path.name.split("_", 1)[0]), key)
        records.setdefault(key, {}).update(
            not_applicable_to_base=data.get("not_applicable_reason", "") or "sem detalhe",
            base_file_absent=bool(data.get("base_file_absent")))
    for path in run.glob("pull_requests/*_pr.json"):
        # The review fields as pr_creator and the preview read them.
        data = with_recorded_review_notes(read_json(path) or {}, path)
        apk = path.name.split(".apk_", 1)[0] + ".apk" if ".apk_" in path.name else ""
        # The draft's own finding, also when the pull request is attributed
        # to a joint finding (LocalSend run_v4, 2026-10-31).
        analysed = analysed_identity(data)
        key = (apk, analysed["csv_class"], analysed["method_name"], analysed["spec"])
        record = records.get(key)
        if record is None:
            continue
        added, removed = _diff_size(data.get("diff", ""))
        changed = data.get("changed_files") or []
        published = _published_pull_request(path, data)
        if published:
            record["pr_published"] = published
        for name in ("review_contract_version", "fix_scope", "behavior_review_recorded"):
            if data.get(name) not in (None, "") or name not in record:
                record[name] = data.get(name)
        record.update(
            pr=True, pr_title=data.get("title", ""),
            # The draft's record, which the findings joined into it inherit
            # below: one draft however many findings it resolves (report only).
            pr_draft=path.name,
            pr_committed=bool(str(data.get("commit_hash", "")).strip()),
            pr_files=len(changed) if isinstance(changed, list) else 0,
            pr_added=added, pr_removed=removed,
            pr_repository=f"{data.get('repo_owner', '')}/{data.get('repo_name', '')}".strip("/"),
            pr_publishable=data.get("publishable") is True,
        )
        # A pull request whose text promises a change the diff does not make,
        # or that another one already contains, is not ready (pr_creator
        # refuses both).
        held = list(data.get("text_problems") or [])
        if data.get("superseded_by"):
            held.append(f"superseded by {data['superseded_by']}")
        if data.get("resolved_with"):
            # Its own change is in the other draft's diff (pr_grouping, App
            # Manager 2026-11-01): one pull request, counted once.
            held.append(f"resolved with {data['resolved_with']}")
            record["joined_into"] = data.get("resolved_with_method") or data["resolved_with"]
        # pr_creator refuses a draft whose repaired patch's review holds it.
        held.extend(data.get("repaired_patch_review_blocks") or repaired_patch_review_blocks(data))
        if held:
            record["pr_held"] = "; ".join(held)
        elif record["pr_committed"] and record.get("build_status") in PASSING_BUILDS:
            record["pr_ready"] = True
    # Violations whose every fix changed security-relevant behaviour while the
    # code already used the API as documented (issue_candidates.
    # record_no_security_impact).
    for path in sorted(run.glob("error_solutions/no_security_impact/*/*.json")):
        data = read_json(path) or {}
        key = (data.get("apk_name", "") or path.parent.name, data.get("csv_class", ""),
               data.get("method_name", ""), data.get("spec", ""))
        records.setdefault(key, {})["no_security_impact"] = data.get("reason", "") or "sem detalhe"
        if data.get("joint_findings_without_effect"):
            # The unit's joint findings the review found without effect
            # (issue_candidates.record_no_security_impact): each ends on its
            # own, "sem efeito no patch".
            records[key]["joint_findings_without_effect"] = merged_joint_without_effect(
                records[key].get("joint_findings_without_effect"),
                data["joint_findings_without_effect"])
    # Fixes not generated because of the pipeline's own shortfall
    # (issue_candidates.record_generation_shortfall), no issue written; its
    # shortfall_kind says which: draft_quality (every draft failed checks on
    # its code), draft_shape (a draft's shape: no edits, an original_code not
    # found once, a moved brace) or scope_gap (no draft was written: the
    # analysis left scope the pipeline computed unreviewed). The detail names
    # the last two, so a scope gap does not read as a bad draft.
    for path in sorted(run.glob("error_solutions/generation_shortfall/*/*.json")):
        data = read_json(path) or {}
        key = (data.get("apk_name", "") or path.parent.name, data.get("csv_class", ""),
               data.get("method_name", ""), data.get("spec", ""))
        codes = "; ".join(sorted({
            str(item).split(":", 1)[0] for item in data.get("generation_blockers") or []
        })) or "sem detalhe"
        kind = str(data.get("shortfall_kind", "") or "")
        records.setdefault(key, {})["generation_shortfall"] = (
            f"{kind}: {codes}" if kind and kind != "draft_quality" else codes)
    # Confirmed violations that ended before any draft with nothing to ask the
    # maintainer (issue_candidates.record_confirmed_without_safe_fix).
    for path in sorted(run.glob("error_solutions/confirmed_without_safe_fix/*/*.json")):
        data = read_json(path) or {}
        key = (data.get("apk_name", "") or path.parent.name, data.get("csv_class", ""),
               data.get("method_name", ""), data.get("spec", ""))
        reason = str(data.get("reason", "")).strip() or "sem detalhe"
        # kind external_contract: finding_processing.blocked_without_confirmed_impact.
        if str(data.get("kind", "")) == "external_contract":
            records.setdefault(key, {})["external_contract"] = reason
        else:
            records.setdefault(key, {})["confirmed_without_safe_fix"] = reason
    # Findings no paid analysis was spent on
    # (issue_candidates.record_settled_before_analysis).
    for path in sorted(run.glob("error_solutions/settled_before_analysis/*/*.json")):
        data = read_json(path) or {}
        key = (data.get("apk_name", "") or path.parent.name, data.get("csv_class", ""),
               data.get("method_name", ""), data.get("spec", ""))
        records.setdefault(key, {})["settled_before_analysis"] = {
            name: str(data.get(name, "")) for name in ("kind", "reason", "pull_request")}
    mark_interrupted_analyses(run, records)
    # A finding analysed together with the one whose method calls it
    # (generate_error_fixes.join_findings_of_one_sequence) ends where that one ends.
    for item in read_json(run / "error_solutions" / "joint_findings.json") or []:
        joined, into = item.get("joined") or {}, item.get("into") or {}
        identity = ("apk_name", "csv_class", "method_name", "spec")
        joined_key = tuple(joined.get(name, "") for name in identity)
        into_key = tuple(into.get(name, "") for name in identity)
        primary = records.get(into_key, {})
        # The unit's production edits were already on the base, and this
        # joint finding's own statement still is: the base change did not
        # settle it, so it does not end as "código mudou upstream" (tokn
        # run_v6, 2026-10-31: decrypt's Cipher and GCM findings beside the
        # getEntry -> getKey edit our merged tokn#138 had made).
        if any(isinstance(entry, dict)
               and (entry.get("method"), entry.get("specification"))
               == (joined.get("method_name", ""), joined.get("spec", ""))
               for entry in primary.get("joint_findings_still_on_base") or []) \
                and _final_outcome(primary)[0].startswith("correção não aplicável"):
            records[joined_key] = {
                **{name: primary[name] for name in ("run", "analysed", "decision",
                                                    "detector_violation") if name in primary},
                "analysed": True,
                "generation_shortfall": "o patch da unidade já está no branch de destino, "
                                        "a instrução deste achado continua lá",
                "joined_into": into.get("method_name", "")}
            continue
        # A joint finding whose share of the unit's one patch has no effect
        # is not resolved with it: it ends as a violation without security
        # impact, while the unit keeps its patch (BlueGate run_v8,
        # 2026-10-31: generateNewKeyPair was "PR pronto, resolvido junto com
        # getOrCreateKeyPair" for a local holding the same
        # ECGenParameterSpec).
        unresolved = joint_finding_without_effect(
            {"method": joined.get("method_name", ""), "specification": joined.get("spec", "")},
            primary.get("joint_findings_without_effect"))
        if unresolved:
            records[joined_key] = {
                **{name: primary[name] for name in ("run", "analysed", "decision",
                                                    "detector_violation") if name in primary},
                "analysed": True,
                "no_security_impact": str(unresolved.get("reason", "")).strip() or "sem detalhe",
                "joined_into": into.get("method_name", ""), "joint_without_effect": True}
            continue
        attribution = primary.get("pr_attribution") or {}
        attributed_here = any(
            isinstance(item, dict) and (item.get("csv_class", ""), item.get("method_name", ""),
                                        item.get("spec", "")) == tuple(joined_key[1:])
            for item in attribution.get("attributed_to") or []) and bool(
                attribution.get("reported_finding"))
        records[joined_key] = {
            **primary, "joined_into": into.get("method_name", ""),
            # The specification of the finding it was joined into, which ended
            # without effect (a same-method join names the method twice).
            **({"attributed_here": str((attribution.get("reported_finding") or {})
                                       .get("spec", "")) or True}
               if attributed_here else {})}
    close_reported_findings_without_effect(records)
    return records


def close_reported_findings_without_effect(records: Dict[Key, Dict]) -> None:
    """The analysed finding of a unit whose pull request is attributed to its joint findings.

    LocalSend run_v4 (2026-10-31): the patch changed only the joint KeyStoreSpec
    call and outcomes.csv still counted loadOrCreateKeyStore [KeyPairSpec] as
    "PR pronto". With the behaviour review's pr_attribution the joint findings
    the diff affects keep the unit's draft (read_run gave them the analysed
    finding's record), and the analysed finding ends as a violation without
    security impact, analysed with them.
    """
    for key, record in list(records.items()):
        attribution = record.get("pr_attribution") or {}
        reported = attribution.get("reported_finding") or {}
        if record.get("joined_into") or not reported or tuple(key[1:]) != (
                reported.get("csv_class", ""), reported.get("method_name", ""),
                reported.get("spec", "")):
            continue
        targets = [item for item in attribution.get("attributed_to") or []
                   if isinstance(item, dict)
                   and (key[0], item.get("csv_class", ""), item.get("method_name", ""),
                        item.get("spec", "")) in records]
        if not targets:
            continue
        records[key] = {
            **{name: record[name] for name in ("run", "analysed", "decision",
                                               "detector_violation") if name in record},
            "analysed": True,
            "no_security_impact": str(reported.get("reason", "")).strip() or "sem detalhe",
            "analysed_with": targets[0].get("method_name", ""),
            "reported_without_effect": True,
        }


INTERRUPTED_ANALYSIS = "análise interrompida"
# What a finding ends with once its analysis concluded, whichever way.
_CONCLUDED = ("analysed", "solution", "issue", "no_security_impact", "not_applicable_to_base",
              "generation_shortfall", "confirmed_without_safe_fix", "external_contract",
              "settled_before_analysis")


def mark_interrupted_analyses(run: Path, records: Dict[Key, Dict]) -> None:
    """Give INTERRUPTED_ANALYSIS to each finding the model was paid for that concluded nothing.

    SMSecure's run_v2 (2026-10-29) made 6 calls (US$ 0.38) for
    getKeyFromPassphrase [PBEKeySpecSpec] before the llm step died, and the
    finding was listed as "não analisado" with no reason, like one never
    started. The prompt records name the finding (llm.cost.usage_by_finding);
    a reason already recorded (a crashed call) is kept.
    """
    pending = [key for key, record in records.items()
               if not record.get("not_analysed_reason")
               and not any(record.get(name) for name in _CONCLUDED)]
    if not pending:
        return
    from cognalys.llm.cost import OPENAI_DELIVERIES, run_log_dir, usage_by_finding

    # The OpenAI traces, as before the cost reports summed Anthropic calls:
    # this marking is an outcome, which a reporting change does not move.
    called = usage_by_finding(run_log_dir(run), prices={}, deliveries=OPENAI_DELIVERIES,
                              read_responses=False)
    for key in pending:
        if key[1:] in called:
            records[key]["not_analysed_reason"] = INTERRUPTED_ANALYSIS


def recorded_review_notes(solutions_dir: Path, *key: str) -> Dict:
    from cognalys.llm.fix_artifacts import recorded_review_notes as read_notes

    return read_notes(solutions_dir, *key)


def with_recorded_review_notes(metadata: Dict, pr_path: Path) -> Dict:
    from cognalys.llm.fix_artifacts import with_recorded_review_notes as with_notes

    return with_notes(metadata, pr_path)


_RECORDED_OUTCOMES: Dict[str, List[Dict]] = {}


def _recorded_pull_requests(metadata: Dict) -> List[Dict]:
    """The pull requests `cognalys pr-outcomes` recorded for this draft's repository.

    One file per repository (prs.pr_outcomes); a draft that names no
    repository has none, rather than every repository's.
    """
    from cognalys.prs.pr_outcomes import OUTCOMES_DIR, _repository_file

    if not (str(metadata.get("repo_owner", "")).strip()
            and str(metadata.get("repo_name", "")).strip()):
        return []
    path = _repository_file(metadata, OUTCOMES_DIR)
    cache_key = str(path)
    if cache_key not in _RECORDED_OUTCOMES:
        _RECORDED_OUTCOMES[cache_key] = [item for item in read_json(path) or []
                                         if isinstance(item, dict)]
    return _RECORDED_OUTCOMES[cache_key]


def _published_pull_request(pr_path: Path, data: Dict) -> str:
    """The pull request already opened for this draft's finding, or "".

    Its own publication record beside the PR file, else the record of the
    same PR file (same apk, method and specification) in another run of the
    experiment, else a recorded outcome of the same finding in the same
    repository: Grocy, KeyPass, Amethyst, ReadYou and InterTune were published
    from older drafts, and marking them for a rerun is only noise. Another
    repository's pull request for the same class, method and specification (a
    fork, a vendored class) is not this draft's. A repository renamed or
    transferred after publication records its outcome under the new name;
    ReadYou's and Tokn's drafts are still found through the sibling run that
    published them.
    """
    import glob

    from cognalys.prs.publication_checks import published_pull_request

    pr_path = Path(pr_path)
    created = published_pull_request(pr_path)
    if created:
        return created
    experiment = pr_path.parent.parent.parent
    for record in sorted(experiment.glob(
            f"*/pull_requests/{glob.escape(pr_path.stem)}_created.json")):
        sibling = record.with_name(pr_path.name)
        created = "" if sibling == pr_path else published_pull_request(sibling)
        if created:
            return created
    finding_id = str(data.get("finding_id", "")).strip()
    identity = tuple(str(data.get(name, "")) for name in ("spec", "csv_class", "method_name"))
    for item in _recorded_pull_requests(data):
        if ((finding_id and str(item.get("finding_id", "")) == finding_id)
                or (all(identity) and tuple(str(item.get(name, "")) for name in
                                            ("spec", "csv_class", "method_name")) == identity)):
            return str(item.get("pull_request", "")) or "publicado"
    return ""


def stale_review_reason(record: Dict) -> str:
    """Why a draft needs a rerun before approval: its behaviour review is not the current one."""
    if record.get("pr_published"):
        return ""
    from cognalys.prs.publication_checks import stale_review

    return stale_review(record.get("review_contract_version"),
                        record.get("behavior_review_recorded"),
                        fix_scope=record.get("fix_scope"))


def ready_draft_detail(record: Dict) -> str:
    """The detail of a "PR pronto" draft: a rerun is due, or no review exists on its path."""
    if stale_review_reason(record):
        # Said apart only when the draft's review file records the outage: a
        # draft from before the review existed has no file, and stays
        # "revisão anterior".
        return NOT_RUN_REVIEW_DETAIL if record.get("behavior_review_not_run") \
            else STALE_REVIEW_DETAIL
    from cognalys.prs.publication_checks import review_absent_by_design

    if not record.get("pr_published") and review_absent_by_design(
            record.get("fix_scope"), record.get("review_contract_version")):
        return UNREVIEWED_PATH_DETAIL
    return ""


def combine_runs(runs: Dict[str, Dict[Key, Dict]]) -> Dict[Key, Dict]:
    """One record per finding, from the latest run that analysed it.

    Runs are given oldest first. A later run that analysed a finding replaces
    what an earlier one recorded for it; a later run that only extracted it
    does not, because extraction is not a result: run_20261011_161752
    extracted all 109 RV-import findings, analysed 14 before it stopped, and
    letting it win erased the other 95 analyses. A finding no run analysed is
    represented by the latest run that says why it was not analysed, or failing
    that by the latest that extracted it; the same interrupted run had not
    reached analysis, so it carried no reason. The record says which run it
    came from.
    """
    combined: Dict[Key, Dict] = {}
    for records in runs.values():
        for key, record in records.items():
            current = combined.get(key, {})
            if record.get("analysed"):
                combined[key] = record
            elif (record.get("extraction_status") and not current.get("analysed")
                  and (record.get("not_analysed_reason")
                       or not current.get("not_analysed_reason"))):
                combined[key] = record
    return combined


OUTCOME_ORDER = (
    "PR pronto",
    "PR já aberto",
    "correção gerada, sem PR",
    "correção não aplicável: código mudou upstream",
    "correção não aplicável: arquivo fora do branch de destino",
    "correção proposta, reprovada antes de gerar",
    "correção não gerada",
    "issue para o mantenedor do app",
    "issue para a biblioteca",
    "violação confirmada sem correção segura",
    "violação sem impacto de segurança",
    "contrato externo impede a correção",
    "depende de decisão do mantenedor",
    "falso positivo reconhecido",
    "evidência insuficiente",
    "não analisado",
)


# The outcomes in which the unit's patch exists: only there was a joined
# finding resolved with the finding it was joined into.
FIX_OUTCOMES = frozenset({"PR pronto", "correção gerada, sem PR"})


def unit_patch_resolves(record: Dict, outcome: str) -> bool:
    """Whether the unit's patch exists and applied and built, so its joint
    findings were resolved with it.

    Review of batch 13 (2026-10-31): six rows read "correção gerada, sem PR,
    resolvido junto com X" beside "build: failed (generated_patch)"; a patch
    that did not apply or build resolved nothing.
    """
    if outcome not in FIX_OUTCOMES:
        return False
    if outcome == "PR pronto":
        return True
    return not ((record.get("apply_status") and record["apply_status"] != "SUCCESS")
                or (record.get("build_status") and record["build_status"] not in PASSING_BUILDS))


def final_outcome(record: Dict) -> Tuple[str, str]:
    """Where a finding ended, as one category and an optional detail."""
    outcome, detail = _final_outcome(record)
    if record.get("reported_without_effect"):
        # Its unit's pull request is attributed to the joint findings its diff
        # affects (close_reported_findings_without_effect).
        joint = f"analisado junto com {record['analysed_with']}, sem efeito no patch"
        return outcome, f"{joint}; {detail}" if detail else joint
    if record.get("joined_into"):
        # Analysed with that finding, but the unit's patch leaves this one's
        # code as the checker saw it (read_run).
        # Resolved only when the unit ended with its patch; a unit that ended
        # as an issue, without security impact or not analysed resolved
        # nothing (2026-10-31: 48 rows under outputs/ said "resolvido junto
        # com" beside 20 "sem impacto", 19 issues, 4 "evidência insuficiente",
        # 4 "não analisado" and 1 "falso positivo").
        joint = (f"analisado junto com {record['joined_into']}, sem efeito no patch"
                 if record.get("joint_without_effect")
                 # The unit's pull request is this finding's: the one it was
                 # joined into ended without effect (review of batch 13,
                 # 2026-10-31: it read "PR pronto, resolvido junto com X"
                 # beside X's "sem impacto").
                 else f"PR atribuído a este achado; {record['joined_into']}"
                      + (f" [{record['attributed_here']}]"
                         if isinstance(record["attributed_here"], str) else "")
                      + " ficou sem efeito no patch" if record.get("attributed_here")
                 else f"resolvido junto com {record['joined_into']}"
                 if unit_patch_resolves(record, outcome)
                 else f"analisado junto com {record['joined_into']}")
        detail = f"{joint}; {detail}" if detail else joint
    return outcome, detail


def _final_outcome(record: Dict) -> Tuple[str, str]:
    if record.get("pr_ready"):
        # The rounds first: the stale-review details are matched at the end
        # (outcome_section).
        return "PR pronto", "; ".join(part for part in (
            loop_depth_label(record.get("generation_loop")), ready_draft_detail(record)) if part)
    settled = record.get("settled_before_analysis") or {}
    if settled and not record.get("solution"):
        # Not analysed on purpose (2026-10-31: reruns beside our open #1013,
        # #168 and #6): the pull request it would have produced exists.
        if settled.get("kind") == "open_pull_request":
            return "PR já aberto", settled.get("pull_request", "")
        if settled.get("kind") == "merged_pull_request":
            return ("correção não aplicável: código mudou upstream",
                    f"PR aceito depois do commit analisado: {settled.get('pull_request', '')}")
        if settled.get("kind") == "file_absent_from_base":
            return ("correção não aplicável: arquivo fora do branch de destino",
                    settled.get("reason", ""))
    if record.get("solution") and record.get("apply_status") == "NOT_APPLICABLE_TO_BASE":
        # Not a failure of the fix: the branch a pull request would target
        # no longer has the reviewed code, or never had the file. Counting
        # these as "generated, no PR" mixed nextcloudcookbook and Giggity,
        # whose maintainers had already fixed the finding, with patches that
        # failed to build.
        error = record.get("apply_error", "")
        if error in ("SOURCE_FILE_ABSENT_FROM_BASE_BRANCH", "SOURCE_IN_SUBMODULE"):
            return ("correção não aplicável: arquivo fora do branch de destino",
                    f"submódulo {record['submodule']}" if record.get("submodule")
                    else error)
        if record.get("base_port") == "ALREADY_FIXED_UPSTREAM":
            return ("correção não aplicável: código mudou upstream",
                    "o branch de destino já não tem a violação")
        return "correção não aplicável: código mudou upstream", error
    if record.get("solution"):
        if record.get("apply_status") and record.get("apply_status") != "SUCCESS":
            return "correção gerada, sem PR", f"aplicação: {record.get('apply_error') or record['apply_status']}"
        if record.get("build_status") and record["build_status"] not in PASSING_BUILDS:
            # Whose failure it was: the patch's, the project's own (it fails
            # on the base commit too) or the environment's.
            attribution = record.get("build_attribution") or "sem atribuição"
            return "correção gerada, sem PR", f"build: {record['build_status']} ({attribution})"
        if record.get("pr_held"):
            return "correção gerada, sem PR", f"PR retido: {record['pr_held']}"
        return "correção gerada, sem PR", ""
    if record.get("no_security_impact"):
        return "violação sem impacto de segurança", record["no_security_impact"]
    if record.get("not_applicable_to_base"):
        # No patch and the reported statement gone from the branch an issue
        # would be about: AmaRadio's md5 (misuse3 run7) still ended as a
        # maintainer issue about a getInstance("MD5") master no longer has.
        return ("correção não aplicável: arquivo fora do branch de destino"
                if record.get("base_file_absent")
                else "correção não aplicável: código mudou upstream"), record["not_applicable_to_base"]
    if record.get("external_contract"):
        # A counterpart outside the repository fixes the value and the
        # adjudication did not confirm an impact
        # (finding_processing.blocked_without_confirmed_impact).
        return "contrato externo impede a correção", record["external_contract"]
    if record.get("confirmed_without_safe_fix"):
        # Promoted to a reviewable fix, then no edit planned: the violation
        # stays confirmed and no maintainer question was found (audit of
        # 2026-10-31: Trigger's decodePublic).
        return "violação confirmada sem correção segura", record["confirmed_without_safe_fix"]
    if record.get("generation_shortfall"):
        # The pipeline's shortfall (drafts that failed checks on their code
        # or shape, or a scope gap that stopped generation before any
        # draft), neither a question for the maintainer nor a judgement on
        # the code (audit of 2026-10-31).
        return "correção não gerada", record["generation_shortfall"]
    if record.get("issue"):
        target = record.get("issue_target", "")
        repository = record.get("issue_repository", "")
        if record.get("method_changed_upstream"):
            repository = (f"{repository}; " if repository else "") + "o método mudou upstream"
        return ("issue para a biblioteca" if target == "upstream_library"
                else "issue para o mantenedor do app"), repository
    if not record.get("analysed"):
        return "não analisado", record.get("not_analysed_reason", "")
    if record.get("failure_kind") == "provider_unavailable":
        # Adjudicated, then stopped by an unavailable provider (InterTune
        # v4's draft, 2026-11-09): run_status leaves it for a resume, and its
        # decision alone ("correção proposta, reprovada antes de gerar")
        # would read as a judgement on a fix never drafted. Not a crash: of
        # the 12 analysed findings a crash followed under outputs, 10 are
        # exp_final_20261019/run's, whose verdicts were recorded before a
        # datetime serialization crashed them, and the paper's sample counts
        # them by those verdicts.
        return "não analisado", ("análise interrompida: "
                                 + record.get("not_analysed_reason", ""))
    decision = record.get("decision", "")
    violation = record.get("detector_violation") or "unknown"
    if decision in FIX_DECISIONS:
        return "correção proposta, reprovada antes de gerar", ""
    if external_contract_decision(decision):
        return "contrato externo impede a correção", ""
    if decision.endswith("_REQUIRES_POLICY"):
        return "depende de decisão do mantenedor", ""
    if violation == "confirmed":
        return "violação confirmada sem correção segura", ""
    if violation == "not_confirmed":
        return "falso positivo reconhecido", ""
    return "evidência insuficiente", ""


def stage_flags(record: Dict) -> Dict[str, bool]:
    return {
        "imported": True,
        "analysed": bool(record.get("analysed")),
        "violation_confirmed": record.get("detector_violation") == "confirmed",
        "fix_proposed": record.get("decision") in FIX_DECISIONS or bool(record.get("solution")),
        "solution": bool(record.get("solution")),
        "applied": record.get("apply_status") == "SUCCESS",
        "built": record.get("build_status") in PASSING_BUILDS,
        "pr_ready": bool(record.get("pr_ready")),
        "pr_ready_publishable": bool(record.get("pr_ready") and record.get("pr_publishable")),
        "pr_ready_review": bool(record.get("pr_ready") and not record.get("pr_publishable")),
    }


# ------------------------------------------------------------------ tables

# Columns worth grouping by when the findings CSV has them. A CSV from another
# source simply lacks some, and those groupings are left out.
GROUPING_CANDIDATES = (
    ("origin", "origem"), ("outcome", "outcome do pacote"),
    ("detector_label", "label do monitor"), ("spec", "especificação"),
    # Reports read from the logcat of a task errors.csv holds nothing of
    # (rvsec.unreported_task_logcats; DNSToggle and Beta, 2026-10-29) are
    # counted apart from errors.csv's.
    ("report_source", "fonte dos relatos"),
)
MAX_GROUPS = 12


def grouping_columns(findings) -> List[Tuple[str, str]]:
    """The candidate columns present with 2 to MAX_GROUPS distinct values."""
    chosen = []
    for column, title in GROUPING_CANDIDATES:
        values = {row.get(column) for row in findings.values() if row.get(column)}
        if 2 <= len(values) <= MAX_GROUPS:
            chosen.append((column, title))
    return chosen

def funnel_section(findings, records, groups) -> Tuple[str, List[List]]:
    rows, csv_rows = [], []
    total = len(findings)
    for stage, label in STAGES:
        count = sum(stage_flags(records.get(key, {}))[stage] for key in findings)
        low, high = wilson(count, total)
        rows.append([label, count, percent(count / total if total else 0),
                     f"{percent(low)}–{percent(high)}"])
        csv_rows.append(["all", "", stage, count, total])
    text = "### Funil geral\n\n" + markdown_table(
        ["etapa", "n", "% dos importados", "IC 95% (Wilson)"], rows)
    availability = Counter(
        records.get(key, {}).get("extraction_status") or "sem registro" for key in findings
    )
    text += ("\n\n### Disponibilidade de código (extração)\n\n"
             "Não é etapa do funil: um finding de biblioteca pode ser analisado "
             "a partir de bytecode e dependências sem código no checkout.\n\n"
             + markdown_table(["extraction_status", "n"],
                              sorted(availability.items(), key=lambda x: -x[1])))
    for column, title in groups:
        values = sorted({row.get(column, "") or "(vazio)" for row in findings.values()})
        header = ["etapa"] + [f"{value} (n={sum((row.get(column, '') or '(vazio)') == value for row in findings.values())})" for value in values]
        body = []
        for stage, label in STAGES:
            line = [label]
            for value in values:
                keys = [key for key, row in findings.items()
                        if (row.get(column, "") or "(vazio)") == value]
                count = sum(stage_flags(records.get(key, {}))[stage] for key in keys)
                line.append(f"{count} ({percent(count / len(keys)) if keys else '–'})")
                csv_rows.append([column, value, stage, count, len(keys)])
            body.append(line)
        text += f"\n\n### Funil por {title}\n\n" + markdown_table(header, body)
    return text, csv_rows


def agreement_section(findings, records, annotations, severe_sites,
                      groups) -> Tuple[str, List[List]]:
    analysed = {key: records[key] for key in findings if records.get(key, {}).get("analysed")}
    csv_rows = []
    parts = []
    for column, title in groups:
        values = sorted({findings[key].get(column, "") or "(vazio)" for key in analysed})
        verdicts = ["confirmed", "not_confirmed", "unknown"]
        body = []
        for value in values:
            keys = [key for key in analysed
                    if (findings[key].get(column, "") or "(vazio)") == value]
            counts = Counter(analysed[key].get("detector_violation") or "unknown" for key in keys)
            body.append([value, len(keys)] + [counts.get(v, 0) for v in verdicts])
            csv_rows += [[column, value, v, counts.get(v, 0)] for v in verdicts]
        parts.append(f"### detector_violation × {title} (só findings analisados)\n\n"
                     + markdown_table([title, "n"] + verdicts, body))
    if severe_sites:
        body = Counter()
        for key in analysed:
            for site in severe_sites.get(key, []):
                body[(site.get("criterion", ""), site.get("verdict", ""),
                      analysed[key].get("detector_violation") or "unknown")] += 1
        parts.append("### detector_violation × veredito mecânico de severe-sites\n\n"
                     + markdown_table(["critério", "veredito mecânico", "pipeline", "n"],
                                      [[*k, n] for k, n in sorted(body.items())]))
    if annotations:
        pairs, body = [], Counter()
        for key, verdict in annotations.items():
            record = records.get(key, {})
            if not verdict or not record.get("analysed"):
                continue
            pipeline = record.get("detector_violation") or "unknown"
            body[(verdict, pipeline)] += 1
            pairs.append((verdict, pipeline))
        human = sorted({v for v, _ in body})
        machine = ["confirmed", "not_confirmed", "unknown"]
        table = [[v] + [body.get((v, m), 0) for m in machine] for v in human]
        confirmed = [h for h, m in pairs if m == "confirmed"]
        rejected = [h for h, m in pairs if m == "not_confirmed"]
        real = sum(h in ("R", "W") for h in confirmed)
        false_alarm = sum(h == "F" for h in rejected)
        text = ("### Pipeline × veredito humano (anotação)\n\n"
                + markdown_table(["humano \\ pipeline"] + machine, table))
        if confirmed:
            low, high = wilson(real, len(confirmed))
            text += (f"\n\nPrecisão de *confirmed* (R ou W): {real}/{len(confirmed)} = "
                     f"{percent(real / len(confirmed))} (IC 95% {percent(low)}–{percent(high)})")
        if rejected:
            text += (f"\n\nFração de *not_confirmed* que o humano marcou F: "
                     f"{false_alarm}/{len(rejected)} = {percent(false_alarm / len(rejected))}")
        parts.append(text)
        csv_rows += [["annotation", h, m, n] for (h, m), n in body.items()]
    else:
        parts.append("*Concordância com o veredito humano: forneça --annotations "
                     "(colunas apk, class, method, spec, verdict com R/W/F/E).*")
    return "\n\n".join(parts), csv_rows


def stability_section(runs: Dict[str, Dict[Key, Dict]], findings) -> Tuple[str, List[List]]:
    names = list(runs)
    rows, csv_rows = [], []
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            keys = [key for key in findings
                    if runs[first].get(key, {}).get("analysed")
                    and runs[second].get(key, {}).get("analysed")]
            decisions = [(runs[first][k].get("decision", ""), runs[second][k].get("decision", "")) for k in keys]
            violations = [(runs[first][k].get("detector_violation") or "unknown",
                           runs[second][k].get("detector_violation") or "unknown") for k in keys]
            same_decision = sum(a == b for a, b in decisions)
            same_violation = sum(a == b for a, b in violations)
            kappa = cohen_kappa(violations)
            rows.append([f"{Path(first).name} × {Path(second).name}", len(keys),
                         f"{same_decision} ({percent(same_decision / len(keys)) if keys else '–'})",
                         f"{same_violation} ({percent(same_violation / len(keys)) if keys else '–'})",
                         "–" if kappa is None else f"{kappa:.2f}"])
            transitions = Counter((a, b) for a, b in violations if a != b)
            csv_rows += [[first, second, a, b, n] for (a, b), n in transitions.items()]
    text = ("### Estabilidade entre execuções\n\n"
            + markdown_table(["par de execuções", "analisados em ambas",
                              "mesma decisão", "mesmo detector_violation",
                              "κ (detector_violation)"], rows)
            + "\n\nAtenção: se o código do pipeline mudou entre as execuções, a "
              "diferença mistura o efeito das mudanças com a variação do LLM. "
              "Para medir só a variação, compare execuções com o código congelado.")
    return text, csv_rows


def build_section(run: Path, records: Dict[Key, Dict]) -> Tuple[str, List[List]]:
    report = read_json(run / "application_results" / "build_validation" / "build_report.json") or []
    rows, causes = [], Counter()
    for entry in report:
        status = entry.get("status", "")
        attribution = entry.get("failure_attribution") or ""
        cause = "sucesso" if status in PASSING_BUILDS else ""
        if status == PRODUCTION_ONLY_BUILD:
            # A pass, but of production code only: said apart from a full one.
            cause = "sucesso (só produção; testes falham na base)"
        log_text = ""
        if not cause:
            # The top-level result is the phase that decided the status
            # (the patched tests, after a passing production build).
            log_path = Path(str(entry.get("log_file", "")
                                or (entry.get("phases", {}).get("production") or {}).get("log_file", "")))
            try:
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                log_text = ""
        if not cause and status == "timeout":
            cause = "timeout"
        if not cause:
            for name, pattern in ENVIRONMENT_SIGNATURES:
                if re.search(pattern, log_text):
                    cause = f"ambiente: {name}"
                    break
        if not cause and attribution.startswith(("verified_preexisting", "preexisting")):
            cause = "falha pré-existente na base"
        if not cause and attribution == "generated_patch":
            cause = "causada pelo patch"
        if not cause and (entry.get("failing_tests") or entry.get("new_failing_tests")
                          or "There were failing tests" in log_text):
            cause = "testes falhando (sem atribuição)"
        cause = cause or "não classificada"
        causes[cause] += 1
        rows.append([entry.get("apk_name", ""), entry.get("task", ""), status,
                     attribution or "–", cause])
    text = ("### Builds de validação\n\n"
            + markdown_table(["causa", "n"], sorted(causes.items(), key=lambda x: -x[1]))
            + "\n\n" + markdown_table(["app", "tarefa", "status", "atribuição", "causa"], rows))
    return text, [[run.name, *row] for row in rows]


def _crosstab(rows: List[Tuple[str, str]], row_title: str,
              column_order: Optional[List[str]] = None) -> str:
    """A count table of (row, column) pairs with totals."""
    counts = Counter(rows)
    row_values = sorted({r for r, _ in rows}, key=lambda value: -sum(
        n for (r, _), n in counts.items() if r == value))
    columns = column_order or sorted({c for _, c in rows})
    body = [[value] + [counts.get((value, column), 0) for column in columns]
            + [sum(counts.get((value, column), 0) for column in columns)]
            for value in row_values]
    body.append(["**total**"] + [sum(counts.get((value, column), 0) for value in row_values)
                                 for column in columns] + [len(rows)])
    return markdown_table([row_title] + columns + ["total"], body)


def overview_section(findings, records, corpus) -> str:
    apps = Counter(key[0] for key in findings)
    per_app = sorted(apps.values())
    median = per_app[len(per_app) // 2] if per_app else 0
    lines = [
        f"- **Findings:** {len(findings)} em **{len(apps)} apps** "
        f"({len({key[3] for key in findings})} especificações).",
        f"- **Findings por app:** mínimo {per_app[0] if per_app else 0}, "
        f"mediana {median}, máximo {per_app[-1] if per_app else 0}.",
    ]
    for column, title in (("origin", "Origem do código"), ("outcome", "Outcome do pacote"),
                          ("detector_label", "Label do monitor")):
        values = Counter(row.get(column) or "(vazio)" for row in findings.values())
        if len(values) > 1:
            lines.append(f"- **{title}:** " + ", ".join(
                f"{value} {count} ({percent(count / len(findings))})"
                for value, count in values.most_common()))
    runs = Counter(records.get(key, {}).get("run", "sem execução") for key in findings)
    if len(runs) > 1:
        lines.append("- **Execução de onde vem cada finding:** " + ", ".join(
            f"{name} {count}" for name, count in runs.most_common()))
    if corpus:
        sizes = [float(corpus[apk].get("apk_size_mb") or 0) for apk in apps
                 if apk in corpus and corpus[apk].get("apk_size_mb")]
        if sizes:
            sizes.sort()
            lines.append(f"- **Tamanho dos APKs:** mediana {sizes[len(sizes) // 2]:.1f} MB, "
                         f"de {sizes[0]:.1f} a {sizes[-1]:.1f} MB.")
    specs = Counter(key[3] for key in findings)
    return ("\n".join(lines) + "\n\n### Findings por especificação\n\n"
            + markdown_table(["especificação", "n", "%"],
                             [[spec, n, percent(n / len(findings))]
                              for spec, n in specs.most_common()]))


def outcome_section(findings, records, groups) -> Tuple[str, List[List]]:
    outcomes = {key: final_outcome(records.get(key, {})) for key in findings}
    counts = Counter(outcome for outcome, _ in outcomes.values())
    total = len(findings)
    body = []
    for outcome in OUTCOME_ORDER:
        count = counts.get(outcome, 0)
        if not count:
            continue
        low, high = wilson(count, total)
        body.append([outcome, count, percent(count / total), f"{percent(low)}–{percent(high)}"])
    text = ("Cada finding aparece em exatamente uma linha: a etapa mais avançada "
            "que alcançou ou o motivo de ter parado.\n\n"
            + markdown_table(["desfecho", "n", "%", "IC 95% (Wilson)"], body))
    ready = [key for key, (outcome, _) in outcomes.items() if outcome == "PR pronto"]
    if ready:
        publishable = sum(bool(records.get(key, {}).get("pr_publishable")) for key in ready)
        text += (f"\n\nDos {len(ready)} PRs prontos, {publishable} são publicáveis "
                 f"(PR pronto (publicável)) e {len(ready) - publishable} ficam para revisão "
                 "(PR pronto (revisão)); todos exigem a aprovação explícita do finding.")
    methods = {key[:3] for key in ready}
    if len(methods) < len(ready):
        # Two specifications reported on one method give two findings and
        # two patches to the same code, which a maintainer reviews as one.
        text += (f"\n\nOs {len(ready)} PRs prontos tocam {len(methods)} métodos distintos: "
                 "findings de specs diferentes no mesmo método geram patches que se "
                 "sobrepõem e devem ser revisados juntos.")
    looped = [key for key in ready if records.get(key, {}).get("generation_loop")]
    if looped:
        text += (f"\n\n{len(looped)} dos {len(ready)} PRs prontos só ficaram de pé depois "
                 "de um redraft após recusa (checagens semânticas ou revisão de "
                 "comportamento) ou de um replan (detalhe \"após ...\"): leia-os primeiro.")
    stale = [key for key in ready
             if outcomes[key][1].endswith((STALE_REVIEW_DETAIL, NOT_RUN_REVIEW_DETAIL))]
    if stale:
        text += (f"\n\n{len(stale)} dos {len(ready)} PRs prontos passaram por uma revisão de "
                 "comportamento anterior à atual (ou por nenhuma): reexecute-os antes de "
                 "aprovar a publicação.")
    details = Counter((outcome, detail) for outcome, detail in outcomes.values() if detail)
    if details:
        text += ("\n\n### Detalhe dos desfechos\n\n"
                 + markdown_table(["desfecho", "detalhe", "n"],
                                  [[o, d, n] for (o, d), n in details.most_common()]))
    for column, title in groups:
        pairs = [(outcomes[key][0], findings[key].get(column) or "(vazio)") for key in findings]
        text += f"\n\n### Desfecho × {title}\n\n" + _crosstab(pairs, "desfecho")
    csv_rows = [[*key, outcome, detail] for key, (outcome, detail) in sorted(outcomes.items())]
    return text, csv_rows


def decisions_section(findings, records) -> str:
    analysed = [key for key in findings if records.get(key, {}).get("analysed")]
    parts = []
    pairs = [(records[key].get("decision") or "(vazio)",
              records[key].get("detector_violation") or "unknown") for key in analysed]
    parts.append("### Decisão do LLM × violação do detector\n\n"
                 "`detector_violation`: *confirmed* = o relato se confirma no código; "
                 "*not_confirmed* = falso positivo; *unknown* = a evidência não decide.\n\n"
                 + _crosstab(pairs, "decisão", ["confirmed", "not_confirmed", "unknown"]))
    impact = Counter(records[key].get("security_impact") or "(vazio)" for key in analysed)
    parts.append("### Impacto de segurança em tempo de execução (só analisados)\n\n"
                 + markdown_table(["runtime_security_impact", "n"], impact.most_common()))
    evidence = [(records.get(key, {}).get("evidence") or "sem registro",
                 "analisado" if records.get(key, {}).get("analysed") else "não analisado")
                for key in findings]
    parts.append("### O que o LLM tinha para ler × análise\n\n"
                 + _crosstab(evidence, "evidência", ["analisado", "não analisado"]))
    evidence_verdict = [(records[key].get("evidence") or "sem registro",
                         records[key].get("detector_violation") or "unknown") for key in analysed]
    parts.append("### Evidência × violação (só analisados)\n\n"
                 + _crosstab(evidence_verdict, "evidência",
                             ["confirmed", "not_confirmed", "unknown"]))
    spec_pairs = [(key[3], records[key].get("detector_violation") or "unknown") for key in analysed]
    parts.append("### Especificação × violação (só analisados)\n\n"
                 + _crosstab(spec_pairs, "especificação", ["confirmed", "not_confirmed", "unknown"]))
    reasons = Counter(records.get(key, {}).get("not_analysed_reason") or "sem motivo registrado"
                      for key in findings if not records.get(key, {}).get("analysed"))
    if reasons:
        parts.append("### Motivos de não análise\n\n"
                     + markdown_table(["motivo", "n"], reasons.most_common()))
    return "\n\n".join(parts)


def fixes_section(findings, records, pr_annotations) -> str:
    solutions = [key for key in findings if records.get(key, {}).get("solution")
                 or records.get(key, {}).get("pr")]
    parts = []
    if solutions:
        body = []
        for key in solutions:
            record = records[key]
            body.append([
                key[0].removesuffix(".apk"), key[2], key[3],
                record.get("decision", ""),
                record.get("apply_status", "–") or "–",
                record.get("build_status", "–") or "–",
                "sim" if record.get("pr_ready") else "não",
                record.get("pr_files", "–"),
                f"+{record.get('pr_added', 0)}/-{record.get('pr_removed', 0)}" if record.get("pr") else "–",
                (record.get("pr_title", "") or "")[:70],
                pr_annotations.get(key, ""),
            ])
        parts.append("### Correções geradas\n\n" + markdown_table(
            ["app", "método", "spec", "decisão", "aplicação", "build", "PR pronto",
             "arquivos", "linhas", "título", "avaliação manual"], body))
        if not pr_annotations:
            parts.append("*Avaliação manual dos PRs: acrescente a coluna `pr_quality` "
                         "(correto / cosmético / prejudicial) ao arquivo de --annotations.*")
    else:
        parts.append("Nenhuma correção gerada.")
    issues = [key for key in findings if records.get(key, {}).get("issue")]
    if issues:
        body = [[key[0].removesuffix(".apk"), key[2], key[3],
                 "biblioteca" if records[key].get("issue_target") == "upstream_library" else "app",
                 records[key].get("issue_repository", ""), records[key].get("issue_kind", "")]
                for key in issues]
        parts.append("### Candidatos a issue\n\n" + markdown_table(
            ["app", "método", "spec", "destino", "repositório", "tipo"], body))
    return "\n\n".join(parts)


def apps_section(findings, records, corpus) -> Tuple[str, List[List]]:
    by_app: Dict[str, List[Key]] = defaultdict(list)
    for key in findings:
        by_app[key[0]].append(key)
    rows = []
    for apk, keys in sorted(by_app.items(), key=lambda item: -len(item[1])):
        flags = [stage_flags(records.get(key, {})) for key in keys]
        info = corpus.get(apk, {})
        rows.append([
            apk.removesuffix(".apk"), len(keys),
            sum(1 for key in keys if findings[key].get("origin") == "app"),
            sum(flag["analysed"] for flag in flags),
            sum(flag["violation_confirmed"] for flag in flags),
            sum(flag["fix_proposed"] for flag in flags),
            sum(flag["pr_ready"] for flag in flags),
            sum(1 for key in keys if records.get(key, {}).get("issue")),
            (info.get("categories") or "").strip("[]").replace("'", "")[:30],
            info.get("apk_size_mb", ""),
            info.get("play_installs") or info.get("play_min_installs") or "",
        ])
    header = ["app", "findings", "do app", "analisados", "confirmados",
              "correção proposta", "PR pronto", "issues", "categoria", "MB", "instalações"]
    with_any = Counter()
    for row in rows:
        for index, name in ((4, "≥1 confirmado"), (5, "≥1 correção proposta"),
                            (6, "≥1 PR pronto"), (7, "≥1 issue")):
            if row[index]:
                with_any[name] += 1
    summary = ", ".join(f"{name}: {count} de {len(rows)} apps" for name, count in with_any.items())
    return (summary + "\n\n" + markdown_table(header, rows)), rows


def costs_section(run_costs: Dict[str, Dict]) -> str:
    body = []
    for name, costs in run_costs.items():
        llm, steps = costs.get("llm") or {}, costs.get("steps") or {}
        total = sum(steps.values())
        body.append([
            Path(name).name, llm.get("findings", "–"), llm.get("calls", "–"),
            llm.get("failed_calls", "–"),
            # Identical calls a finding's earlier call answered, not sent
            # (llm_providers.finding_replay_scope); "–" before it was counted.
            llm.get("replayed_calls", "–"),
            f"{float(llm.get('model_seconds', 0) or 0) / 3600:.1f}",
            f"{steps.get('llm', 0) / 3600:.1f}",
            f"{(steps.get('validation', 0) + steps.get('revalidation', 0)) / 3600:.1f}",
            f"{total / 3600:.1f}",
        ])
    return markdown_table(["execução", "findings", "chamadas LLM", "falhas",
                           "reaproveitadas", "h de modelo", "h etapa llm", "h validação",
                           "h total"], body)


# -------------------------------------------------------------------- main

def read_annotations(path: Optional[Path]) -> Dict[Key, str]:
    if not path or not path.is_file():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return {
            (row["apk"], row["class"], row["method"], row["spec"]):
                (row.get("verdict") or "").strip().upper()
            for row in csv.DictReader(handle)
        }


def read_pr_annotations(path: Optional[Path]) -> Dict[Key, str]:
    """The manual quality of each generated PR, from the annotations file."""
    if not path or not path.is_file():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return {
            (row["apk"], row["class"], row["method"], row["spec"]):
                (row.get("pr_quality") or "").strip()
            for row in csv.DictReader(handle) if (row.get("pr_quality") or "").strip()
        }


def read_corpus(path: Optional[Path]) -> Dict[str, Dict[str, str]]:
    """Per-APK metadata from a corpus sheet with an `apk` column."""
    if not path or not path.is_file():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return {row["apk"]: row for row in csv.DictReader(handle) if row.get("apk")}


def read_severe_sites(path: Optional[Path]) -> Dict[Key, List[Dict]]:
    sites: Dict[Key, List[Dict]] = defaultdict(list)
    if path and path.is_file():
        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                sites[(row["apk"], row["class"], row["method"], row["spec"])].append(row)
    return sites


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", nargs="+", type=Path, required=True,
                        help="Run directories; the last one is described in full.")
    parser.add_argument("--findings-csv", type=Path, required=True)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--severe-sites", type=Path,
                        help="The package's severe-sites.csv, when there is one.")
    parser.add_argument("--write-annotation-template", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/statistics"),
                        help="Reports go to <output-dir>/<run name>.")
    parser.add_argument("--report-dir", type=Path,
                        help="Write the reports to exactly this directory.")
    parser.add_argument("--combine", action="store_true",
                        help=("Describe every finding from the latest of --runs that "
                              "processed it (runs oldest first), instead of the last run only."))
    parser.add_argument("--corpus", type=Path,
                        help="Corpus sheet with per-APK metadata (e.g. the package's dataset.csv).")
    args = parser.parse_args(argv)

    findings = read_findings(args.findings_csv)
    if args.write_annotation_template:
        write_csv(args.write_annotation_template,
                  ["apk", "class", "method", "spec", "origin", "outcome",
                   "detector_label", "verdict", "pr_quality", "notes"],
                  [[*key, row.get("origin", ""), row.get("outcome", ""),
                    row.get("detector_label", ""), "", "", ""]
                   for key, row in sorted(findings.items())])
        print(f"annotation template -> {args.write_annotation_template}")

    runs = {str(run): read_run(run) for run in args.runs}
    latest = args.runs[-1]
    if args.combine:
        records = combine_runs(runs)
        title = f"consolidado de {len(args.runs)} execuções (até {latest.name})"
        used = [run for run in args.runs
                if any(record.get("run") == run.name for record in records.values())]
        default_name = f"combined_{latest.name}"
    else:
        records = runs[str(latest)]
        title = latest.name
        used = [latest]
        default_name = latest.name
    output = args.report_dir or args.output_dir / default_name
    corpus = read_corpus(args.corpus)
    groups = grouping_columns(findings)
    funnel_text, funnel_csv = funnel_section(findings, records, groups)
    outcome_text, outcome_csv = outcome_section(findings, records, groups)
    agreement_text, agreement_csv = agreement_section(
        findings, records, read_annotations(args.annotations),
        read_severe_sites(args.severe_sites), groups,
    )
    apps_text, apps_csv = apps_section(findings, records, corpus)
    header = (f"# Estatísticas — {title}\n\n"
              f"Findings: {len(findings)}. Execuções lidas: "
              + ", ".join(run.name for run in args.runs) + ".")
    if args.combine and len(used) > 1:
        header += ("\n\n> Cada finding vem da execução mais recente que o processou. "
                   "Execuções diferentes podem ter usado versões diferentes do "
                   "pipeline; para uma medição homogênea, rode todos os findings "
                   "numa mesma execução.")
    sections = [header,
                "## 1. Visão geral\n\n" + overview_section(findings, records, corpus),
                "## 2. Desfecho de cada finding\n\n" + outcome_text,
                "## 3. Funil\n\n" + funnel_text,
                "## 4. Decisões do LLM\n\n" + decisions_section(findings, records),
                "## 5. Correções, PRs e issues\n\n"
                + fixes_section(findings, records, read_pr_annotations(args.annotations)),
                "## 6. Apps\n\n" + apps_text,
                "## 7. Decisão do pipeline × rótulos\n\n" + agreement_text]
    if len(runs) > 1:
        stability_text, stability_csv = stability_section(runs, findings)
        sections.append("## 8. Estabilidade\n\n" + stability_text)
        write_csv(output / "stability_transitions.csv",
                  ["run_a", "run_b", "violation_a", "violation_b", "n"], stability_csv)
    build_texts, build_csv = [], []
    for run in used:
        run_records = {key: record for key, record in records.items()
                       if record.get("run") == run.name}
        text, rows = build_section(run, run_records)
        build_texts.append((f"### {run.name}\n\n" if len(used) > 1 else "") + text)
        build_csv += rows
    sections.append("## 9. Builds\n\n" + "\n\n".join(build_texts))
    sections.append("## 10. Custo\n\n" + costs_section(
        {str(run): read_run_costs(run) for run in used}))
    output.mkdir(parents=True, exist_ok=True)
    (output / "statistics.md").write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    write_csv(output / "funnel.csv", ["group", "value", "stage", "count", "total"], funnel_csv)
    write_csv(output / "outcomes.csv", ["apk", "class", "method", "spec", "outcome", "detail"],
              outcome_csv)
    write_csv(output / "agreement.csv", ["dimension", "value", "verdict", "count"], agreement_csv)
    write_csv(output / "apps.csv",
              ["app", "findings", "app_origin", "analysed", "confirmed", "fix_proposed",
               "pr_ready", "issues", "category", "apk_size_mb", "installs"], apps_csv)
    write_csv(output / "builds.csv", ["run", "apk", "task", "status", "attribution", "cause"], build_csv)
    write_csv(output / "findings.csv",
              ["apk", "class", "method", "spec", "origin", "outcome", "label",
               *[stage for stage, _ in STAGES], "decision", "detector_violation",
               "security_impact", "evidence", "final_outcome", "run",
               "apply_status", "build_status", "pr_title"],
              [[*key, row.get("origin", ""), row.get("outcome", ""), row.get("detector_label", ""),
                *[int(stage_flags(records.get(key, {}))[stage]) for stage, _ in STAGES],
                records.get(key, {}).get("decision", ""),
                records.get(key, {}).get("detector_violation", ""),
                records.get(key, {}).get("security_impact", ""),
                records.get(key, {}).get("evidence", ""),
                final_outcome(records.get(key, {}))[0],
                records.get(key, {}).get("run", ""),
                records.get(key, {}).get("apply_status", ""),
                records.get(key, {}).get("build_status", ""),
                records.get(key, {}).get("pr_title", "")]
               for key, row in sorted(findings.items())])
    print(f"statistics -> {output / 'statistics.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
