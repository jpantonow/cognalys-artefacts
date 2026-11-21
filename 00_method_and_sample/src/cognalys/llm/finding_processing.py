"""One finding from analysis to its outcome, and the worker pool over many.

Moved verbatim out of generate_error_fixes.py; see FixGenerator.
"""

from __future__ import annotations

import json
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
)

_LLM_MODULE_DIR = Path(__file__).resolve().parent
_SRC_DIR = _LLM_MODULE_DIR.parent

from cognalys.llm.llm_providers import (  # noqa: E402
    CallBudgetExhausted,
    ProviderAuthenticationError,
    ProviderContentRefusal,
    ProviderRequestRejected,
    ProviderTransientFailure,
    budget_limits_from_environment,
    cache_reads_bypassed,
    finding_bypasses_cache,
    finding_budget,
    finding_replay_scope,
)
from cognalys.utils.disposition import (  # noqa: E402
    FindingDisposition,
    GENERATING_DECISIONS,
    REPAIR_DECISIONS,
    external_contract_decision,
    minimum_coordinated_edits,
)
from cognalys.utils.contribution_base import code_gone_from_base  # noqa: E402
from cognalys.llm.source_facts import classify_spec_family  # noqa: E402
from cognalys.llm.migration_policy import (  # noqa: E402
    ground_usage_in_value_consumers,
    promote_validation_repair_sources,
    with_provided_files,
    authorize_provided_files,
)
from cognalys.llm.code_quality import (  # noqa: E402
    coordinated_quality_metrics,
)
from cognalys.llm.fix_artifacts import (  # noqa: E402
    _write_json_atomic,
    persist_context_snapshot,
    summarize_patch_changes,
)
from cognalys.llm.adjudication import _trace_context  # noqa: E402
from cognalys.llm.response_parsing import ResponseParser  # noqa: E402
from cognalys.llm.semantic_validation import (  # noqa: E402
    plan_edits_beyond_method,
    validate_multi_file_scope,
    MULTI_FILE_DECISIONS,
    REVIEW_ONLY_DECISIONS,
    minimum_planned_files,
    normalize_coordinated_edit,
    repository_has_test_infrastructure,
    validate_coordinated_edit_shape,
)
from cognalys.llm.coordinated_generation import (  # noqa: E402
    caller_source_gap, is_test_path, joint_findings_without_effect)
from cognalys.llm.pipeline_architecture import (  # noqa: E402
    PROMPT_VERSION,
    SCHEMA_VERSION,
    blocked_finding_review_status,
    reported_violation,
    demote_incomplete_scope_to_review,
    human_decided_publication_considerations,
    publication_blocker_record,
    unmodelled_calls_in_source,
    section_telemetry,
)

from cognalys.llm.fix_generation_common import (  # noqa: E402
    FindingOutcome,
    generation_shortfall,
)


def one_method_edit(error: Dict, method_name: str) -> Dict:
    """The plan edit of a supported change confined to the reported method.

    What the single-method generator assumed without a plan entry (B3): the
    per-file path generates the reported file, and this names it and the
    method, so the edit-coverage check and the floor of one edit read it.
    """
    return {
        "source_file_path": str(error.get("source_file_path", "")),
        "symbol": str(error.get("source_method_name") or method_name),
        "reason": "the reported method",
        "create_new_file": False,
        "edit_category": "method",
        "supporting_claim_ids": [],
    }


def adjudicated_review_assumptions(evidence_analysis) -> List[str]:
    """The review assumptions the adjudication recorded for this finding."""
    if not isinstance(evidence_analysis, dict):
        return []
    adjudication = (evidence_analysis.get("v2") or {}).get("adjudication") or {}
    values = (adjudication.get("review_assumptions")
              or evidence_analysis.get("review_assumptions") or [])
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if str(item).strip()]


def documented_use_without_security_impact(evidence_analysis) -> str:
    """The adjudication's reason the reported code already uses the API as documented.

    Grocy's MemorizingTrustManager.getTrustManager and AntennaPod's
    BackportTrustManager.getSystemTrustManager pass a null KeyStore to
    TrustManagerFactory.init, the documented request for the platform trust
    store. Adjudication answered NO_SAFE_FIX, so each became a maintainer
    issue, while the same shape stopped by the behaviour review ended as a
    violation without security impact (Cryptomator, Olvid, Talk).

    The judgement is the model's (current_code_is_correct_use with its
    reason); this only checks that it applies: an adjudicated NO_SAFE_FIX
    with a confirmed cause, a confirmed violation and no planned edit. A
    decision the pipeline turned into NO_SAFE_FIX (a recorded suppression, a
    rejected external or policy decision) keeps its issue, and a violation
    adjudication did not confirm keeps its own outcome (a false positive, or
    insufficient evidence): both motivating cases were adjudicated confirmed.
    Only an impact of non_security (or a record without the field) is read
    as without security impact: 7 recorded closures answered correct use
    with not_established and one with confirmed (2026-11-10).
    """
    if not isinstance(evidence_analysis, dict):
        return ""
    adjudication = (evidence_analysis.get("v2") or {}).get("adjudication") or {}
    if not isinstance(adjudication, dict):
        return ""
    evidence_status = evidence_analysis.get("evidence_status") or {}
    if not isinstance(evidence_status, dict):
        evidence_status = {}

    def field(name: str) -> str:
        return str(evidence_analysis.get(name, evidence_status.get(name, ""))).strip()

    if field("detector_violation") not in {"confirmed", "source_established"}:
        return ""
    impact = field("runtime_security_impact")
    if impact and impact != "non_security":
        # The adjudication prescribes non_security for correct use. A
        # confirmed impact reaches the maintainer as an issue (IbisWallet
        # run, 2026-11-08), and an impact left open is a confirmed violation
        # without a safe correction (blocked_without_confirmed_impact). A
        # record without the field reads as before.
        return ""
    if (str(evidence_analysis.get("decision", "")).upper() != "NO_SAFE_FIX"
            or str(adjudication.get("decision", "NO_SAFE_FIX")).upper() != "NO_SAFE_FIX"):
        return ""
    if (str(evidence_analysis.get("cause_status", "")) != "confirmed"
            or not str(evidence_analysis.get("confirmed_cause", "")).strip()):
        return ""
    plan = evidence_analysis.get("coordinated_fix_plan") or {}
    if isinstance(plan, dict) and plan.get("required_edits"):
        return ""
    if adjudication.get("current_code_is_correct_use") is not True:
        return ""
    return str(adjudication.get("correct_use_reason", "")).strip()


def _analysed_commit_date(repository_path: str) -> Optional[datetime]:
    """When the checkout's HEAD (the analysed revision) was committed, or None."""
    import subprocess

    if not repository_path:
        return None
    try:
        shown = subprocess.run(["git", "-C", repository_path, "log", "-1", "--format=%cI", "HEAD"],
                               capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return _instant(shown.stdout.strip()) if shown.returncode == 0 else None


def _instant(text: str) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def settled_before_analysis(error: Dict, publications: List, outcomes_dir=None) -> Dict:
    """Why this finding needs no paid analysis at all, or {}.

    2026-10-31: reruns regenerated Grocy's load/save while our #1013 was
    open, Fread's buildSSLFactory while #168 was, and Salty's encode beside
    #6; pr_creator then refused each as a duplicate. Three cases, read from
    local records only (prs.outcome_report.earlier_pull_request_for): one of
    our pull requests for the finding is open; one was merged and opened
    after the analysed commit, so the analysed code predates it (a merged one
    older than the analysed commit leaves the analysis to say whether the
    finding came back); or the reported file is not on the contribution base
    at all (a file kept in a submodule is another repository's, not absent).
    A method whose body merely changed upstream is still analysed (agrahn
    run_v3's invokeSuspend reached a draft that way).
    """
    from cognalys.prs.outcome_report import earlier_pull_request_for
    from cognalys.prs.pr_outcomes import OUTCOMES_DIR

    # Every finding of the unit must be covered, each by its own id or
    # file+method+spec: a joined unit whose new finding (a later run's `save`
    # beside `load`'s open PR) no pull request covers is still analysed, or
    # that finding would be dropped as "PR já aberto" for a PR that does not
    # change it (review of batch 14, 2026-10-31). Publication records do not
    # list joint findings, so a partly covered unit cannot be settled safely.
    members = [{key: value for key, value in error.items() if key != "joint_findings"}] + [
        {"unique_error_id": item.get("unique_error_id", ""),
         "source_file_path": item.get("source_file_path") or error.get("source_file_path", ""),
         "method_name": item.get("method") or item.get("method_name", ""),
         "spec": item.get("specification") or item.get("spec", ""),
         "repository_url": error.get("repository_url", "")}
        for item in error.get("joint_findings") or [] if isinstance(item, dict)]
    earlier_each = [earlier_pull_request_for(member, publications, outcomes_dir or OUTCOMES_DIR)
                    for member in members]
    if earlier_each and all(item.get("state") == "open" for item in earlier_each):
        earlier = earlier_each[0]
        return {"kind": "open_pull_request", "pull_request": earlier["pull_request"],
                "reason": f"our pull request {earlier['pull_request']} for this finding is open"}
    analysed = None
    merged_after = []
    for item in earlier_each:
        if item.get("state") == "merged":
            opened = _instant(item.get("created_at", ""))
            if analysed is None:
                analysed = _analysed_commit_date(str(error.get("repository_path", ""))) or False
            if opened and analysed and opened > analysed:
                merged_after.append(item)
    if earlier_each and merged_after and all(
            item.get("state") == "open" or item in merged_after for item in earlier_each):
        earlier = merged_after[0]
        return {"kind": "merged_pull_request", "pull_request": earlier["pull_request"],
                "reason": f"our pull request {earlier['pull_request']} for this finding "
                          "was merged, and the analysed commit predates it"}
    base = error.get("contribution_base") or {}
    if base.get("target_present") is False and not base.get("submodule"):
        return {"kind": "file_absent_from_base",
                "reason": "%s is not on the contribution base branch %s" % (
                    base.get("source_file_path", "") or error.get("source_file_path", ""),
                    base.get("base_branch", "") or "unknown")}
    return {}


def non_security_hash_settled(evidence_analysis) -> str:
    """Why a confirmed digest finding adjudicated NON_SECURITY_HASH_USAGE needs no change, or "".

    Xtra (audit of 2026-10-31): the SHA-1 of the WebSocket handshake's
    Sec-WebSocket-Accept is fixed by RFC 6455, and the finding became an issue
    asking the maintainer to coordinate an endpoint change. The adjudication
    now chooses NON_SECURITY_HASH_USAGE for a digest a public protocol
    standard fixes; that decision generates nothing, and it reached the same
    issue. The judgement is the model's; this only checks it applies: that
    decision on a confirmed violation and cause, no planned edit. A
    non-security violation with a correct change still becomes a patch (adjudication_rules.route_non_security_repair_to_review).
    """
    if not isinstance(evidence_analysis, dict):
        return ""
    if str(evidence_analysis.get("decision", "")).upper() != "NON_SECURITY_HASH_USAGE":
        return ""
    status = evidence_analysis.get("evidence_status") or {}
    violation = str(evidence_analysis.get(
        "detector_violation", (status if isinstance(status, dict) else {}).get(
            "detector_violation", ""))).strip()
    cause = str(evidence_analysis.get("confirmed_cause", "")).strip()
    if (violation not in {"confirmed", "source_established"} or not cause
            or str(evidence_analysis.get("cause_status", "")) != "confirmed"):
        return ""
    plan = evidence_analysis.get("coordinated_fix_plan") or {}
    if isinstance(plan, dict) and plan.get("required_edits"):
        return ""
    adjudication = (evidence_analysis.get("v2") or {}).get("adjudication") or {}
    return str((adjudication if isinstance(adjudication, dict) else {}).get(
        "correct_use_reason", "")).strip() or cause


def planner_abstained_after_promotion(evidence_analysis) -> str:
    """The planner's reason for finding no edit after a review promotion, or "".

    Audit of 2026-10-31: 11 run-units (17 issue rows; in the latest runs
    Trigger's decodePublic x2, agrahn's generateKeystoreNativeKey x2 and
    Where) were promoted by the adjudication to a reviewable fix, the planner
    then planned no edit, and each became a maintainer issue asking "Is there
    a constraint I may be missing?". The violation stays confirmed, and no
    maintainer decision was found: recorded as a confirmed violation without
    a safe correction. Only a confirmed violation qualifies; the
    correct-use judgement, when the adjudication gave it, is applied first
    (documented_use_without_security_impact).
    """
    if not isinstance(evidence_analysis, dict):
        return ""
    adjudication = (evidence_analysis.get("v2") or {}).get("adjudication") or {}
    abstention = (adjudication.get("planner_abstained_after_review_promotion")
                  if isinstance(adjudication, dict) else None)
    if not isinstance(abstention, dict):
        return ""
    if str(evidence_analysis.get("decision", "")).upper() != "NO_SAFE_FIX":
        return ""
    status = evidence_analysis.get("evidence_status") or {}
    violation = str(evidence_analysis.get(
        "detector_violation", (status if isinstance(status, dict) else {}).get(
            "detector_violation", ""))).strip()
    if (violation not in {"confirmed", "source_established"}
            or str(evidence_analysis.get("cause_status", "")) != "confirmed"):
        return ""
    impact = str(evidence_analysis.get(
        "runtime_security_impact", (status if isinstance(status, dict) else {}).get(
            "runtime_security_impact", ""))).strip()
    if impact == "confirmed":
        # A confirmed impact reaches the maintainer, with or without a
        # promotion (MeloX run_v5, 2026-11-09).
        return ""
    plan = evidence_analysis.get("coordinated_fix_plan") or {}
    if isinstance(plan, dict) and plan.get("required_edits"):
        return ""
    return str(abstention.get("reason", "")).strip() or "the plan found no edit"


# The values with which the adjudication answered runtime_security_impact
# without confirming an impact. "unknown" is its answer for a hazard the
# evidence does not address; the pipeline's own "unknown" (written when the
# extraction or the adjudication failed its checks) also sets the violation
# and the cause to unknown and never passes the tests below. A record
# without the field keeps its issue.
IMPACT_NOT_CONFIRMED = frozenset({"not_established", "non_security", "unknown"})


def blocked_without_confirmed_impact(evidence_analysis) -> Dict:
    """{"kind", "reason"} of a confirmed violation that ends before any draft
    with an impact the adjudication did not confirm, or {}.

    Measured 2026-11-10 over the 297 production findings: 17 of 47 issues were
    an adjudicated NO_SAFE_FIX or external contract whose impact the
    adjudication did not confirm, and in 13 the maintainer had nothing to act
    on (a third-party API fixing MD5, a rule gap). Herald and IbisWallet moved
    between an issue and no issue on the correct-use answer alone. The
    judgement is the adjudication's; this only checks it applies: its own
    decision (one a pipeline gate turned into NO_SAFE_FIX keeps its issue), a
    confirmed violation and cause, no planned edit, and an impact in
    IMPACT_NOT_CONFIRMED. kind is "external_contract" for a decision
    utils.disposition.external_contract_decision names, else "no_safe_fix";
    reason is what blocked the change, in the planner's words when the plan
    was the blocker.
    """
    if not isinstance(evidence_analysis, dict):
        return {}
    v2 = evidence_analysis.get("v2") or {}
    adjudication = v2.get("adjudication") if isinstance(v2, dict) else {}
    if not isinstance(adjudication, dict):
        adjudication = {}
    evidence_status = evidence_analysis.get("evidence_status") or {}
    if not isinstance(evidence_status, dict):
        evidence_status = {}

    def field(name: str) -> str:
        return str(evidence_analysis.get(name, evidence_status.get(name, ""))).strip()

    decision = str(evidence_analysis.get("decision", "")).upper()
    external = external_contract_decision(decision)
    if not (decision == "NO_SAFE_FIX" or external):
        return {}
    if str(adjudication.get("decision") or decision).upper() != decision:
        return {}
    if field("detector_violation") not in {"confirmed", "source_established"}:
        return {}
    if field("runtime_security_impact") not in IMPACT_NOT_CONFIRMED:
        return {}
    cause = str(evidence_analysis.get("confirmed_cause", "")).strip()
    if str(evidence_analysis.get("cause_status", "")) != "confirmed" or not cause:
        return {}
    plan = evidence_analysis.get("coordinated_fix_plan") or {}
    if isinstance(plan, dict) and plan.get("required_edits"):
        return {}
    blockers = [item for item in adjudication.get("blocking_for_generation") or []
                if isinstance(item, dict)]
    invalid_plan = next((item for item in blockers
                         if item.get("kind") == "invalid_patch_plan"), None)
    if invalid_plan:
        validator = {part.strip() for part in
                     str(invalid_plan.get("description", "")).split(";")}
        planned = v2.get("patch_plan") if isinstance(v2, dict) else {}
        listed = (planned.get("generation_blockers") or []) if isinstance(planned, dict) else []
        texts = [str(item.get("description", "") if isinstance(item, dict) else item).strip()
                 for item in listed]
        texts = [text for text in texts if text not in validator]
    else:
        texts = [str(item.get("description", "")).strip() for item in blockers]
    reason = "; ".join(dict.fromkeys(text for text in texts if text)) or cause
    return {"kind": "external_contract" if external else "no_safe_fix", "reason": reason}


def provider_iv_settled(evidence_analysis) -> str:
    """The reason adjudication_rules.provider_iv_settlement recorded, or ""."""
    if not isinstance(evidence_analysis, dict):
        return ""
    adjudication = (evidence_analysis.get("v2") or {}).get("adjudication") or {}
    if not isinstance(adjudication, dict):
        return ""
    return str(adjudication.get("iv_provenance_settlement", "") or "").strip()


def behavior_review_judged(generated: Dict) -> bool:
    """Whether the current behaviour review judged this generated draft.

    The contract version is recorded only then (review_facts_for_text): a
    draft salvaged without its generated test (_without_rejected_new_tests)
    or completed through a reviewer outage carries none.
    """
    return bool(generated.get("review_contract_version")
                or (generated.get("behavior_review") or {}).get("review_contract_version"))


def behavior_review_dropped_tests(generated: Dict) -> List[str]:
    """Test files whose edits the behaviour review dropped as unrelated.

    Read from the review persisted beside the draft manifest, and only when
    that review judged this draft: drafts share one directory across
    retries, so a salvaged draft's manifest may sit beside a review of an
    earlier attempt. Only test paths count, by the same predicate the review
    drops them under. The draft's own record (dropped_unrelated_tests) is
    read first: it is written by the path that dropped them.
    """
    recorded = generated.get("dropped_unrelated_tests")
    if recorded:
        return [str(path) for path in recorded
                if isinstance(path, str) and is_test_path(path)]
    manifest = str(generated.get("manifest", "") or "")
    if not manifest or not behavior_review_judged(generated):
        return []
    try:
        review = json.loads(
            (Path(manifest).parent / "behavior_review.json")
            .read_text(encoding="utf-8"))
        named = review.get("unrelated_test_edits") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [str(path) for path in named
            if isinstance(path, str) and is_test_path(path)]


def unsupplied_caller_sources(error: Dict, evidence_analysis: Dict) -> List[str]:
    """Call-site files a blocked plan needed that no rebuild supplied.

    From the gap the adjudication recorded on the finding
    (caller_source_gap) or, when only the analysis survived, from the
    structured caller_sources_unavailable of its generation blockers.
    """
    gap = caller_source_gap(error if isinstance(error, dict) else {})
    if gap:
        return gap
    analysis = evidence_analysis if isinstance(evidence_analysis, dict) else {}
    adjudication = (analysis.get("v2") or {}).get("adjudication")         if isinstance(analysis.get("v2"), dict) else None
    paths = set()
    for record in (analysis, adjudication or {}):
        for item in record.get("blocking_for_generation") or []:
            if isinstance(item, dict):
                paths.update(str(path) for path in item.get("caller_sources_unavailable") or []
                             if path)
    return sorted(paths)


class FindingProcessing:
    """One finding from analysis to its outcome, and the worker pool over many."""

    def earlier_publications(self) -> List:
        """Every real pull-request publication under outputs/, read once per run.

        The outputs/ directory this run writes under (prs.outcome_report.
        published); none for a run kept elsewhere.
        """
        cached = getattr(self, "_earlier_publications", None)
        if cached is not None:
            return cached
        from cognalys.prs.outcome_report import published

        output = Path(str(getattr(self, "output_dir", "") or ".")).resolve()
        outputs = next((parent for parent in output.parents if parent.name == "outputs"), None)
        try:
            found = published(outputs) if outputs is not None and outputs.is_dir() else []
        except OSError:
            found = []
        self._earlier_publications = found
        return found

    def refresh_policy_with_value_consumers(self, error: Dict) -> None:
        """Re-resolve the inferred policy once derived_value_consumers exists.

        The policy is resolved at discovery, before the consumers are traced,
        so a variant whose evidence reads them (digest_store_key) could never
        apply: EinkBro's rerun (2026-10-22) still planned with the
        internal-digest policy although its value keys stored filter data.
        The refreshed policy replaces the earlier one only when it reaches a
        different consumer scope; explicit policies still win.
        """
        if not error.get("derived_value_consumers"):
            return
        from cognalys.llm.migration_policy import generic_migration_policy, resolve_case_policy

        refreshed = generic_migration_policy(error)
        current = error.get("case_migration_policy") or {}
        scope = refreshed.get("consumer_scope")
        if not scope or scope == current.get("consumer_scope"):
            return
        policy = {**refreshed, **resolve_case_policy(getattr(self, "migration_policies", {}), error)}
        keeps = policy.pop("keeps_legacy_derivation", False)
        error["case_migration_policy"] = policy
        compatibility = dict(error.get("compatibility_policy") or {})
        compatibility["case_migration_policy"] = policy
        if keeps:
            error["keeps_legacy_derivation"] = True
            compatibility["keeps_legacy_derivation"] = True
        error["compatibility_policy"] = compatibility
        self.logger.info("%s: policy refined by the value's consumers: %s -> %s",
                         error.get("method_name", ""), current.get("consumer_scope", "none"), scope)

    def process_finding(self, error: Dict) -> FindingOutcome:
        """Analyse a single finding and report what it concluded.

        `outcome` starts at "failed" and only the exits that deliberately
        decline a patch raise it to "abstained". `decision_taken` is written
        at exactly two points and nowhere else, which is where the reported
        decision was always captured: the local `decision` is promoted again
        further down (the MULTI_FILE_FIX_REQUIRES_POLICY normalisation), and
        that later value has never been the one reported.
        """
        outcome = "failed"
        decision_taken = ""
        try:
            error_msg = error.get("error_message", "Unknown error")
            method_body = error.get("method_body", "")
            method_name = error.get("method_name", "unknown")
            apk_name = error.get("_file_path", "").parent.name

            self.logger.info(f"Processing: {apk_name}/{method_name}")

            # Said before the first token is spent, not after. The mifos
            # finding in run_20260909_125353 cost 1h50m of model time and
            # produced a 90%-confidence fix that the applicator then had
            # nowhere to write, because the reported file does not exist on
            # the branch a pull request would target. The analysis is still
            # worth having -- it is the research result -- but the operator
            # should learn at the start that no pull request can come of it.
            contribution_base = error.get("contribution_base") or {}
            reason = ""
            if contribution_base.get("target_present") is False:
                reason = "%s does not exist on the contribution base branch %s"
            elif contribution_base.get("analyzed_code_present") is False:
                # The file is there and the reviewed body is not, which for an
                # advisory finding usually means the project already shipped
                # the fix. Element Android cost 34 minutes of analysis and an
                # 18-minute worktree before the applicator said so.
                reason = (
                    "the reviewed code is no longer in %s on the contribution "
                    "base branch %s, so the project has most likely already "
                    "changed it"
                )
            if reason:
                # Whether an issue may still come of it: only when the
                # reported statement is still there (code_gone_from_base).
                self.logger.warning(
                    "%s/%s will not produce a pull request: " + reason
                    + ". The analysis still runs; the patch will have nowhere "
                    "to land%s.",
                    apk_name, method_name,
                    contribution_base.get("source_file_path", "the target"),
                    contribution_base.get("base_branch", "unknown"),
                    "; the reported statement is gone too, so no issue will be"
                    " raised" if code_gone_from_base(contribution_base) else
                    "; the reported statement is still there, so an issue may"
                    " still be raised"
                    if contribution_base.get("reported_statement_present") else "",
                )

            settled = settled_before_analysis(error, self.earlier_publications())
            if settled:
                # Nothing a paid analysis could add (2026-10-31: Grocy, Fread
                # and Salty reruns beside their open pull requests).
                self.logger.warning(
                    "Skipped before any model call: %s/%s: %s",
                    apk_name, method_name, settled["reason"])
                self.record_settled_before_analysis(error, apk_name, method_name, settled)
                return FindingOutcome(None, False, "not_applicable", "", settled["reason"])

            not_applicable = self.not_applicable_reason(error)
            if not_applicable:
                self.logger.warning(
                    "Not applicable, skipped before any model call: %s/%s: %s",
                    apk_name, method_name, not_applicable,
                )
                return FindingOutcome(
                    None, False, "not_applicable", "", not_applicable,
                )

            unanalysable = self.unanalysable_reason(error)
            if unanalysable:
                self.logger.warning(
                    "Not analysable, skipped before any model call: %s/%s: %s",
                    apk_name, method_name, unanalysable,
                )
                return FindingOutcome(
                    None, False, "not_analyzable", "", unanalysable,
                )

            error = dict(error)
            feedback_identity = str(error.get("unique_error_id", "")) or "|".join(
                str(error.get(key, ""))
                for key in ("apk_name", "csv_class", "method_name", "spec")
            )
            feedback_id = hashlib.sha256(
                feedback_identity.encode("utf-8")
            ).hexdigest()[:12]
            if self.validation_feedback_dir:
                feedback_path = (
                    self.validation_feedback_dir / feedback_id
                    / "validation_feedback.json"
                )
                if feedback_path.is_file():
                    try:
                        error["previous_validation_feedback"] = json.loads(
                            feedback_path.read_text(encoding="utf-8")
                        )
                        self.logger.info(
                            "Using persisted validation feedback for %s: %s",
                            method_name, feedback_path,
                        )
                    except (OSError, json.JSONDecodeError) as exc:
                        self.logger.warning(
                            "Could not read validation feedback %s: %s",
                            feedback_path, exc,
                        )
            compatibility_policy = dict(error.get("compatibility_policy", {}))
            if self.allow_cache_invalidation:
                compatibility_policy.update({
                    "allow_cache_invalidation": True,
                    "scope": (
                        "Confirmed derived, disposable cache entries may become "
                        "cache misses and be regenerated; user-owned, offline-only, "
                        "credential, preference, protocol, fingerprint, and ID data "
                        "are excluded."
                    ),
                })
            if error.get("case_migration_policy"):
                compatibility_policy["case_migration_policy"] = error[
                    "case_migration_policy"
                ]
            if error.get("keeps_legacy_derivation"):
                compatibility_policy["keeps_legacy_derivation"] = True
            if compatibility_policy:
                error["compatibility_policy"] = compatibility_policy

            # Phase 1: collect repository-wide evidence before deciding that a
            # local method edit is sufficient.  Delaying this until after a
            # preliminary FIX_SUPPORTED result allowed false single-file fixes
            # to bypass caller, override, test, and shared-contract analysis.
            if "expanded_repository_context" not in error:
                error["expanded_repository_context"] = (
                    self.collect_expanded_repository_context(error)
                )
            promote_validation_repair_sources(error)
            if "derived_value_consumers" not in error:
                self.collect_patch_safety_context(error)
            elif "typed_value_consumers" not in error:
                # A context collected before the typed consumers existed
                # (BlueGate run_v9, 2026-10-31).
                self.collect_typed_value_consumers(error)
            if "deliberate_insecurity" not in error:
                # A context collected before the reading existed (2026-11-04).
                self.collect_deliberate_insecurity(error)
            self.refresh_policy_with_value_consumers(error)
            self.collect_stored_value_context(error)
            if "deprecated_usage_contract" not in error:
                self.collect_migration_grounding(error)
            context_snapshot_path = persist_context_snapshot(
                self.output_dir, error, apk_name, method_name
            )
            if (
                error.get("expanded_repository_context", {})
                .get("multi_file_impact", {})
                .get("multi_file_candidate") is True
            ):
                self.bump_stat("multi_file_candidates")

            start_time = time.time()
            tokens_before = self.provider.get_token_usage()
            self.logger.debug(
                "Running the isolated evidence pipeline for %s...", method_name
            )
            evidence_analysis, v2_artifacts = self.adjudicate_finding(
                error, apk_name, method_name
            )
            error["v2_artifacts"] = v2_artifacts
            evidence_response = json.dumps(
                evidence_analysis, ensure_ascii=False
            )
            preliminary_needs_expansion = False
            evidence_dir = self.output_dir / "evidence_reviews" / apk_name
            evidence_dir.mkdir(parents=True, exist_ok=True)
            safe_method = re.sub(r"[^A-Za-z0-9._-]+", "_", method_name)
            evidence_file = evidence_dir / (
                f"{error.get('error_index', 0)}_{safe_method}_evidence.json"
            )
            if not evidence_analysis:
                evidence_file.write_text(
                    json.dumps(
                        {
                            "status": "ANALYSIS_INVALID",
                            "error_id": error.get("unique_error_id", ""),
                            "apk_name": apk_name,
                            "method_name": method_name,
                            "csv_class": error.get("csv_class", ""),
                            "source_file": error.get("source_file_path", ""),
                            "spec": error.get("spec", ""),
                            "error_message": error.get("error_message", ""),
                            "raw_response": evidence_response,
                        },
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                self.api_logger.error(
                    "Invalid evidence JSON for %s (length=%d):\n%s",
                    method_name,
                    len(evidence_response),
                    evidence_response,
                )
                self.logger.warning(
                    "Evidence analysis for %s was not valid JSON; raw response "
                    "saved to api_calls.log",
                    method_name,
                )
                return FindingOutcome(None, False, outcome, decision_taken)

            self.api_logger.info(
                "Evidence analysis for %s:\n%s",
                method_name,
                json.dumps(evidence_analysis, indent=2, ensure_ascii=False),
            )

            lifetime_assessment = (
                error.get("expanded_repository_context", {})
                .get("representation_lifetime_assessment", {})
            )
            case_policy = error.get("case_migration_policy", {})
            migration_assessment = evidence_analysis.get(
                "migration_assessment", {}
            )
            ephemeral_proven = (
                isinstance(lifetime_assessment, dict)
                and lifetime_assessment.get("supported") is True
            )
            policy_authorized = (
                isinstance(case_policy, dict)
                and case_policy.get("authorized") is True
            )
            if ephemeral_proven and isinstance(migration_assessment, dict):
                migration_assessment["usage_kind"] = "ephemeral_sensitive"
                migration_assessment["legacy_compatibility_required"] = False
                if policy_authorized:
                    migration_assessment["policy_authorized"] = True

            # A usage the call sites contradict is corrected before any gate
            # reads it. RedReader's General.sha1 was classified as naming
            # regenerable cache entries; its caller builds a database file name
            # from it. A value that names stored data or leaves the process is
            # persistence or protocol whatever the analysis called it.
            correction = ground_usage_in_value_consumers(error, evidence_analysis)
            if correction:
                evidence = correction["evidence"]
                self.logger.warning(
                    "Usage of %s's value corrected from %s to %s by its call "
                    "site %s:%s: %s%s",
                    method_name, correction["claimed"], correction["grounded"],
                    evidence.get("path"), evidence.get("line"),
                    str(evidence.get("statement", ""))[:160],
                    "; the in-process digest policy no longer authorises a patch"
                    if correction["policy_authorization_voided"] else "",
                )
                evidence_analysis["usage_grounding"] = correction

            decision = str(evidence_analysis.get("decision", "")).upper()
            discriminator = (
                error.get("expanded_repository_context", {})
                .get("runtime_discriminator_assessment", {})
            )
            discriminator_plan = evidence_analysis.get(
                "coordinated_fix_plan", {}
            )
            discriminator_edits = (
                discriminator_plan.get("required_edits", [])
                if isinstance(discriminator_plan, dict) else []
            )
            discriminator_paths = {
                str(item.get("source_file_path", ""))
                for item in discriminator_edits if isinstance(item, dict)
                and item.get("source_file_path")
            }
            discriminator_has_test = any(
                isinstance(item, dict) and (
                    item.get("edit_kind") == "test"
                    or "test" in str(item.get("source_file_path", "")).lower()
                ) for item in discriminator_edits
            )
            if (
                discriminator.get("supported") is True
                and decision in {
                    "CONFIRMED_SPEC_VIOLATION_NON_SECURITY_USAGE",
                    "NON_SECURITY_HASH_USAGE",
                }
                and len(discriminator_paths) >= 2
                and discriminator_has_test
            ):
                decision = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
                evidence_analysis["decision"] = decision
                if isinstance(migration_assessment, dict):
                    migration_assessment["usage_kind"] = "runtime_discriminator"
                self.logger.info(
                    "Complete runtime-discriminator refactor plan for %s promoted "
                    "to a review-only coordinated fix", method_name,
                )
            planned_edits = (
                evidence_analysis.get("coordinated_fix_plan", {}) or {}
            ).get("required_edits") or []
            if (decision in {
                    "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY",
                    "MULTI_REPOSITORY_MIGRATION_SUPPORTED",
            } and classify_spec_family(str(error.get("spec", ""))) != "message_digest"
                    and planned_edits):
                # A digest-only review label on a finding that is not a digest
                # (a provider without strict schema enforcement can still
                # produce one). Both labels are review-only, so the plan keeps
                # its review-only standing under the generic label instead of
                # being discarded by the digest gate below.
                self.logger.info(
                    "%s: digest-only decision %s on a %s finding relabelled "
                    "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW",
                    method_name, decision, error.get("spec", "") or "non-digest",
                )
                decision = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
                evidence_analysis["decision"] = decision
            decision_taken = decision
            digest_contract = evidence_analysis.get(
                "digest_contract_assessment", {}
            )
            digest_decisions = {
                "SAFE_INTERNAL_DIGEST_MIGRATION",
                "MULTI_REPOSITORY_MIGRATION_SUPPORTED",
                "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY",
                "EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX",
                "NON_SECURITY_HASH_USAGE",
            }
            # A client-only change is structurally safe when every expected
            # value the patch keeps was recomputed from the very file it
            # describes: whoBIRD (2026-10-18) downloads its models from the
            # URLs in its own code, and the pipeline proved each MD5 constant
            # against them. The adjudicator decided the migration, the
            # extraction still answered "unknown", and the gate dropped it.
            case_policy_digest = error.get("case_migration_policy") or {}
            if (decision == "SAFE_INTERNAL_DIGEST_MIGRATION"
                    and isinstance(digest_contract, dict)
                    and case_policy_digest.get("replacement_values")
                    and (case_policy_digest.get("established_digest_reference_evidence")
                         or {}).get("expected_digests_verified")):
                digest_contract.update({
                    "contract_kind": "internal",
                    "backend_change_required": False,
                    "client_only_change_safe": True,
                    "expected_digest_source": (
                        "Recomputed by the pipeline from the files at the URLs in "
                        "the reviewed code (established_digest_reference_evidence)"),
                })
                evidence_analysis["digest_contract_assessment"] = digest_contract
            if decision in digest_decisions:
                digest_issues = []
                if classify_spec_family(str(error.get("spec", ""))) != "message_digest":
                    digest_issues.append(
                        "digest migration decisions require a message-digest finding"
                    )
                if not isinstance(digest_contract, dict):
                    digest_issues.append("digest contract assessment is missing")
                    digest_contract = {}
                if decision == "SAFE_INTERNAL_DIGEST_MIGRATION" and (
                    digest_contract.get("contract_kind") != "internal"
                    or digest_contract.get("backend_change_required") is not False
                    or digest_contract.get("client_only_change_safe") is not True
                ):
                    digest_issues.append(
                        "internal migration requires an application-owned contract"
                    )
                if decision == "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY" and (
                    digest_contract.get("contract_kind") != "external_backend"
                    or digest_contract.get("backend_change_required") is not True
                    or digest_contract.get("backend_repository_available") is not False
                    or not str(digest_contract.get("rollout_strategy", "")).strip()
                    or not str(digest_contract.get("pull_request_note", "")).strip()
                ):
                    digest_issues.append(
                        "backend review patch requires an unavailable backend, rollout strategy, and PR note"
                    )
                if decision == "MULTI_REPOSITORY_MIGRATION_SUPPORTED" and (
                    digest_contract.get("contract_kind") != "multi_repository"
                    or digest_contract.get("backend_repository_available") is not True
                    or digest_contract.get("backend_change_required") is not True
                    or not str(digest_contract.get("rollout_strategy", "")).strip()
                    or not str(digest_contract.get("pull_request_note", "")).strip()
                ):
                    digest_issues.append(
                        "multi-repository migration requires exact backend source"
                    )
                if digest_issues:
                    self.logger.warning(
                        "Digest contract decision rejected for %s: %s",
                        method_name, "; ".join(digest_issues),
                    )
                    evidence_analysis["decision"] = "NO_SAFE_FIX"
                    decision = "NO_SAFE_FIX"
            decision_taken = decision
            confirmed_cause = str(
                evidence_analysis.get("confirmed_cause", "")
            ).strip()
            planned_review_edits = evidence_analysis.get(
                "coordinated_fix_plan", {}
            ).get("required_edits", [])
            planned_review_paths = {
                str(item.get("source_file_path", ""))
                for item in planned_review_edits
                if isinstance(item, dict)
                and str(item.get("source_file_path", "")).strip()
            } if isinstance(planned_review_edits, list) else set()
            scope_for_review = evidence_analysis.get("scope_assessment", {})
            if (
                decision == "MULTI_FILE_FIX_REQUIRES_POLICY"
                and confirmed_cause
                and len(planned_review_paths) >= 2
                and isinstance(scope_for_review, dict)
                and scope_for_review.get("status") == "complete"
                and scope_for_review.get("multi_file_change_required") is True
            ):
                decision = (
                    "MULTI_FILE_FIX_SUPPORTED"
                    if ephemeral_proven and policy_authorized
                    else "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
                )
                evidence_analysis["decision"] = decision
                self.logger.info(
                    "Complete policy-dependent plan for %s normalized to %s "
                    "from deterministic lifetime and policy evidence",
                    method_name, decision,
                )
            supported_decisions = REPAIR_DECISIONS
            final_scope_issues = validate_multi_file_scope(
                error, evidence_analysis
            )
            # An incomplete scope forbids applying unattended, not writing
            # the patch for a reviewer (see demote_incomplete_scope_to_review).
            # Revalidated afterwards so the review-only waiver, and the
            # unreviewed-consumer assumptions below, apply to the demoted
            # decision exactly as they would to one the adjudicator chose.
            if demote_incomplete_scope_to_review(
                    evidence_analysis, decision, final_scope_issues):
                self.logger.info(
                    "%s: %s with incomplete scope generated for review "
                    "instead of abandoned (%s)", method_name, decision,
                    "; ".join(final_scope_issues[:3]),
                )
                decision = evidence_analysis["decision"]
                decision_taken = decision
                final_scope_issues = validate_multi_file_scope(
                    error, evidence_analysis
                )
            # validate_multi_file_scope records the consumers a review-only
            # analysis did not individually review instead of failing it (see
            # unreviewed_coverage_files there). Carry that into the review
            # assumptions so whoever approves the patch sees which callers of
            # the changed value were never examined.
            unreviewed = (
                evidence_analysis.get("scope_assessment", {})
                .get("unreviewed_coverage_files", [])
                if isinstance(evidence_analysis, dict) else []
            )
            if unreviewed:
                assumptions = [
                    item for item in evidence_analysis.get(
                        "review_assumptions", []
                    ) if isinstance(item, str)
                ]
                assumptions.append(
                    "These files consume the changed value and were not "
                    "individually reviewed: " + ", ".join(unreviewed[:12])
                    + ". A reviewer should confirm none of them depends on "
                    "the previous value."
                )
                evidence_analysis["review_assumptions"] = assumptions
            # A value persisted outside preferences is not an unknown scope
            # any more (stored_value_contract "not_modelled", Syncthing-lite's
            # key.pem, 2026-10-29), so whoever approves the patch is told
            # where it is written instead.
            outside = [
                f"{item.get('path', '')}:{item.get('line', 0)}"
                for item in (error.get("stored_value_contract") or {}).get(
                    "unmodelled_sinks") or [] if isinstance(item, dict)
            ]
            if outside and isinstance(evidence_analysis, dict):
                note = (
                    "The changed value is also written to storage at "
                    + ", ".join(outside[:6]) + ". A reviewer should confirm "
                    "data written there before the patch still loads."
                )
                assumptions = [
                    item for item in evidence_analysis.get(
                        "review_assumptions", []
                    ) if isinstance(item, str)
                ]
                if note not in assumptions:
                    evidence_analysis["review_assumptions"] = [*assumptions, note]
            decision_is_supported = (
                decision in supported_decisions and not final_scope_issues
            )
            review_only = (
                decision in REVIEW_ONLY_DECISIONS
                and not final_scope_issues
            )
            generation_allowed = decision_is_supported or review_only

            # These detector-context fields are persisted for every outcome,
            # not only for blocked findings.  Compute them before selecting the
            # review status so supported and review-only decisions cannot skip
            # their initialization and fail while writing the evidence record.
            target_source = ""
            try:
                target_source = (
                    Path(str(error.get("repository_path", "")))
                    / str(error.get("source_file_path", ""))
                ).read_text(encoding="utf-8", errors="replace")
            except OSError:
                target_source = ""
            violation = reported_violation(error)
            observed_unmodelled = unmodelled_calls_in_source(
                str(error.get("spec", "")), target_source
            )

            if decision_is_supported:
                review_status = decision
            elif review_only:
                review_status = "PROPOSED_FIX_REQUIRES_REVIEW"
            elif final_scope_issues:
                review_status = "INCOMPLETE_MULTI_FILE_SCOPE"
            elif decision in {
                "MULTI_FILE_FIX_REQUIRES_POLICY",
                "DEPENDENCY_FIX_REQUIRES_POLICY",
                "UPSTREAM_FIX_REQUIRES_POLICY",
            }:
                review_status = "CONFIRMED_FINDING_REQUIRES_POLICY"
            elif decision == "CONFIRMED_SPEC_VIOLATION_NON_SECURITY_USAGE":
                review_status = "CONFIRMED_NON_SECURITY_SPEC_VIOLATION"
            elif decision == "NON_SECURITY_HASH_USAGE":
                review_status = "CONFIRMED_NON_SECURITY_HASH_USAGE"
            elif decision == "EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX":
                review_status = "EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX"
            elif decision == "MULTI_REPOSITORY_MIGRATION_SUPPORTED":
                review_status = "MULTI_REPOSITORY_MIGRATION_REQUIRES_COORDINATION"
            else:
                review_status = blocked_finding_review_status(
                    evidence_analysis,
                    spec_id=str(error.get("spec", "")),
                    error_message=str(error.get("error_message", "")),
                    app_source_file_path=str(error.get("source_file_path", "")),
                    reported_violation=violation,
                    observed_calls=observed_unmodelled,
                    specification_context=error.get("specification_context", {}),
                    # The reviewed source, which the source-reading false
                    # positive routes need. Without it this call site could
                    # only reach the curated-message route, so the status it
                    # recorded disagreed with the one the preview stage --
                    # which does pass it -- derived from the same record.
                    source=method_body,
                )
            evidence_file.write_text(
                json.dumps(
                    {
                        "status": review_status,
                        "error_id": error.get("unique_error_id", ""),
                        "apk_name": apk_name,
                        "method_name": method_name,
                        "csv_class": error.get("csv_class", ""),
                        "source_file": error.get("source_file_path", ""),
                        "repository_url": error.get("repository_url", ""),
                        "spec": error.get("spec", ""),
                        "error_message": error.get("error_message", ""),
                        "reported_violation": violation,
                        "observed_unmodelled_calls": observed_unmodelled,
                        "specification_context": error.get(
                            "specification_context", {}
                        ),
                        # The reviewed source itself, so a later stage can
                        # test a documented false-positive shape against the
                        # code rather than against the model's prose about it.
                        "method_body": method_body,
                        "expanded_context_snapshot": str(context_snapshot_path),
                        "analysis": evidence_analysis,
                        "architecture": {
                            "version": self.architecture_version,
                            "prompt_version": PROMPT_VERSION,
                            "schema_version": SCHEMA_VERSION,
                        },
                        "v2_artifacts": error.get("v2_artifacts", {}),
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            if (
                not generation_allowed
                or not confirmed_cause
            ):
                if final_scope_issues:
                    self.bump_stat("incomplete_multi_file_scope")
                documented_use = documented_use_without_security_impact(
                    evidence_analysis)
                # A call-site file the plan needed and the rebuild could not
                # supply is a gap in the pipeline's own context: never a
                # judgement on the code ("sem impacto") and never a question
                # for the maintainer, as on the generated-result path below.
                unavailable_callers = unsupplied_caller_sources(error, evidence_analysis)
                if unavailable_callers:
                    _write_json_atomic(
                        evidence_dir / f"{error.get('error_index', 0)}_{safe_method}_pipeline_failure.json",
                        {
                            "status": "pipeline_failure",
                            "kind": "caller_source_gap",
                            "caller_sources_unavailable": unavailable_callers,
                            "issues": final_scope_issues,
                            "evidence_review": str(evidence_file),
                        },
                    )
                    self.logger.warning(
                        "%s: call-site source(s) %s never reached the plan; recorded "
                        "as a pipeline failure", method_name, ", ".join(unavailable_callers))
                elif provider_iv_settled(evidence_analysis):
                    # The IV is the one the provider generated at an
                    # encryption given none (Cryptomator Lite run_v6,
                    # 2026-10-30): nothing in the code to repair, and no
                    # question for the maintainer.
                    self.record_no_security_impact(
                        error, apk_name, method_name, provider_iv_settled(evidence_analysis))
                elif documented_use:
                    # Every change would alter what the code trusts or
                    # protects, and it already uses the API as documented: a
                    # violation without security impact, as when the
                    # behaviour review reaches the same conclusion below.
                    self.record_no_security_impact(
                        error, apk_name, method_name, documented_use)
                    # Another requirement the source itself breaks still
                    # reaches the maintainer (Grocy's getInstance("X509")
                    # beside its documented init(null)).
                    self.persist_blocked_issue_candidate(
                        error, evidence_analysis, evidence_file, apk_name,
                        method_name, final_scope_issues, collateral_only=True,
                        settled_texts=[documented_use],
                    )
                elif non_security_hash_settled(evidence_analysis):
                    # A digest whose value a public standard the app
                    # implements fixes (Xtra's RFC 6455 SHA-1, audit of
                    # 2026-10-31): no change, no question for the maintainer.
                    settled = non_security_hash_settled(evidence_analysis)
                    self.record_no_security_impact(error, apk_name, method_name, settled)
                    self.persist_blocked_issue_candidate(
                        error, evidence_analysis, evidence_file, apk_name,
                        method_name, final_scope_issues, collateral_only=True,
                        settled_texts=[settled],
                    )
                elif (planner_abstained_after_promotion(evidence_analysis)
                      and not code_gone_from_base(error.get("contribution_base") or {})):
                    # Promoted to a reviewable fix, then no edit planned: a
                    # confirmed violation without a safe correction, not a
                    # maintainer question (audit of 2026-10-31). Another
                    # requirement the source breaks still reaches them.
                    abstention = planner_abstained_after_promotion(evidence_analysis)
                    self.record_confirmed_without_safe_fix(
                        error, apk_name, method_name, abstention)
                    self.persist_blocked_issue_candidate(
                        error, evidence_analysis, evidence_file, apk_name,
                        method_name, final_scope_issues, collateral_only=True,
                        settled_texts=[abstention],
                    )
                elif (final_scope_issues and confirmed_cause
                      and decision in supported_decisions | REVIEW_ONLY_DECISIONS
                      and str(evidence_analysis.get("cause_status", "confirmed"))
                      == "confirmed"
                      and self.finding_is_reportable(evidence_analysis)
                      and (shortfall := generation_shortfall(
                          final_scope_issues, evidence_analysis))
                      and not code_gone_from_base(error.get("contribution_base") or {})):
                    # A fix the adjudication supported, stopped only because
                    # the analysis left scope the pipeline computed unreviewed
                    # (Winterhold's derive, Zen Companion's diskKey, Xtra's
                    # WebSocket digest): the pipeline's gap, closed by giving
                    # the analysis those files, not a maintainer question.
                    # Another requirement the source breaks still reaches them.
                    self.record_generation_shortfall(
                        error, apk_name, method_name, final_scope_issues, shortfall)
                    self.persist_blocked_issue_candidate(
                        error, evidence_analysis, evidence_file, apk_name,
                        method_name, final_scope_issues, collateral_only=True,
                    )
                elif ((settled := blocked_without_confirmed_impact(evidence_analysis))
                      and not code_gone_from_base(error.get("contribution_base") or {})):
                    # A confirmed violation with no safe fix, or one a
                    # counterpart outside the repository fixes, whose impact
                    # the adjudication did not confirm: nothing the
                    # maintainer is asked. Another requirement the source
                    # breaks still reaches them.
                    self.record_confirmed_without_safe_fix(
                        error, apk_name, method_name, settled["reason"],
                        kind=settled["kind"])
                    self.persist_blocked_issue_candidate(
                        error, evidence_analysis, evidence_file, apk_name,
                        method_name, final_scope_issues, collateral_only=True,
                    )
                else:
                    self.persist_blocked_issue_candidate(
                        error, evidence_analysis, evidence_file, apk_name,
                        method_name, final_scope_issues,
                    )
                outcome = "abstained"
                self.logger.warning(
                    "Evidence phase abstained for %s: decision=%s, "
                    "cause_status=%s, detector_violation=%s, cause=%s, "
                    "scope_issues=%s; "
                    "review saved to %s",
                    method_name,
                    decision or "missing",
                    evidence_analysis.get("cause_status", "unknown"),
                    evidence_analysis.get("detector_violation", "unknown"),
                    confirmed_cause or "not confirmed",
                    "; ".join(final_scope_issues) or "none",
                    evidence_file,
                )
                return FindingOutcome(None, False, outcome, decision_taken)

            # Phase 2: generate code only from the supported analysis.
            error_with_analysis = dict(error)
            error_with_analysis["evidence_analysis"] = evidence_analysis
            dependency_assessment = evidence_analysis.get(
                "dependency_fix_assessment", {}
            )
            if decision in {
                "DEPENDENCY_FIX_SUPPORTED", "DEPENDENCY_REPLACEMENT_SUPPORTED"
            } and (
                not isinstance(dependency_assessment, dict)
                or dependency_assessment.get("compatibility_constraints_satisfied")
                is not True
                or dependency_assessment.get("fork_or_vcs_dependency") is not False
                or not str(dependency_assessment.get("official_migration_source", "")).strip()
                or (
                    decision == "DEPENDENCY_REPLACEMENT_SUPPORTED"
                    and dependency_assessment.get(
                        "replacement_preserves_required_capabilities"
                    ) is not True
                )
            ):
                self.logger.warning(
                    "Dependency fix rejected for %s: official migration, compatible "
                    "constraints, and non-fork artifact evidence are required",
                    method_name,
                )
                outcome = "abstained"
                return FindingOutcome(None, False, outcome, decision_taken)
            beyond_method = plan_edits_beyond_method(
                evidence_analysis.get("coordinated_fix_plan", {}),
                str(error.get("source_file_path", "")),
                (method_name, str(error.get("source_method_name", ""))),
            )
            routed_from_single_method = False
            routed_review_note = ""
            # B3 (2026-11-12): one generation path. A supported plan that
            # edits only the reported method went to a single-method
            # generator with fewer checks, no behaviour review and no redraft
            # (27 of the 399 recorded solutions); it is now generated by the
            # per-file path under its own decision, as one `method` edit of
            # the reported file when the plan names none.
            one_method_plan = False
            # whoBIRD (2026-10-18): a digest migration planned over the
            # class's expected-value constants and every check reached the
            # single-method generator, which rewrote one call of four.
            if decision in {"FIX_SUPPORTED", "SAFE_INTERNAL_DIGEST_MIGRATION"} and beyond_method:
                routed_from_single_method = True
                # The single-method generator can only replace the reported
                # method; this plan edits more than that. Generate it through
                # the per-file path as a reviewable change instead of sending
                # it where it cannot succeed (see plan_edits_beyond_method).
                self.logger.info(
                    "%s: the supported plan edits %s, not only the reported "
                    "method; generating it as a reviewable declaration change",
                    method_name, ", ".join(beyond_method),
                )
                # A replan that comes back with this same decision is routed
                # the same way (coordinated_generation._replan_refusal).
                evidence_analysis["decision_before_review_routing"] = decision
                decision = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
                decision_taken = decision
                evidence_analysis["decision"] = decision
                error_with_analysis["evidence_analysis"] = evidence_analysis
                # Every check below reads the decision in force, review-only
                # now, not the one review_only was first computed from.
                # Orgzly's MiscUtils.sha1 (frozen run, 2026-11-13): routed
                # here from SAFE_INTERNAL_DIGEST_MIGRATION, its clean-reviewed
                # draft was refused by the test gate as an auto-applied
                # decision ("case policy requires at least one concrete
                # test-file edit") instead of carrying the missing test as a
                # note for the reviewer.
                review_only = decision in REVIEW_ONLY_DECISIONS and not final_scope_issues
                routed_review_note = (
                    "The plan edits " + ", ".join(beyond_method)
                    + " beyond the reported method; a reviewer should confirm "
                    "those changes.")
            elif decision in {"FIX_SUPPORTED", "SAFE_INTERNAL_DIGEST_MIGRATION"}:
                one_method_plan = True
                plan = evidence_analysis.setdefault("coordinated_fix_plan", {})
                if not [item for item in plan.get("required_edits") or []
                        if isinstance(item, dict)]:
                    plan["required_edits"] = [one_method_edit(error, method_name)]
                error_with_analysis["evidence_analysis"] = evidence_analysis
            if decision in GENERATING_DECISIONS:
                self.logger.debug(
                    "Calling persisted per-file coordinated generation for %s...",
                    method_name,
                )
                generated = self.generate_coordinated_file_drafts(
                    error_with_analysis, decision, apk_name, method_name
                )
                tokens_after = self.provider.get_token_usage()
                if generated.get("status") != "complete":
                    generation_blockers = list(generated.get("issues", [])) or [
                        "persisted coordinated generation failed"
                    ]
                    # A pipeline gap (a call-site file the plan never saw)
                    # is never recorded as a judgement on the code.
                    if generated.get("correct_use") and not generated.get("pipeline_failure"):
                        # Every fix changed what the code trusts or protects,
                        # and the code already uses the API as documented: a
                        # violation without security impact, not a question
                        # for the maintainer (Grocy's TrustManagerFactory.init
                        # (null), which selects the platform trust policy).
                        self.record_no_security_impact(
                            error, apk_name, method_name, str(generated["correct_use"]),
                            generated.get("joint_findings_without_effect"))
                    elif (not generated.get("pipeline_failure")
                          and (shortfall := generation_shortfall(
                              generation_blockers, evidence_analysis))
                          and not code_gone_from_base(error.get("contribution_base") or {})):
                        # Only the drafts were wrong (a rename left half done,
                        # an API widened, the reported value untouched, a
                        # draft with no edits): the pipeline's shortfall, not
                        # a question for the maintainer (audit of 2026-10-31:
                        # Ursa, Trigger). A statement gone from the base keeps
                        # the record below, which says so. Another requirement
                        # the source breaks still reaches the maintainer.
                        self.record_generation_shortfall(
                            error, apk_name, method_name, generation_blockers, shortfall)
                        self.persist_blocked_issue_candidate(
                            error, evidence_analysis, evidence_file, apk_name,
                            method_name, generation_blockers, collateral_only=True,
                        )
                    elif not generated.get("pipeline_failure"):
                        self.persist_blocked_issue_candidate(
                            error, evidence_analysis, evidence_file, apk_name,
                            method_name, generation_blockers,
                        )
                    else:
                        _write_json_atomic(evidence_dir / f"{error.get('error_index', 0)}_{method_name}_pipeline_failure.json", generated)
                    self.logger.warning(
                        "Persisted coordinated generation failed for %s: %s; "
                        "manifest=%s", method_name,
                        "; ".join(generated.get("issues", [])) or "invalid draft",
                        generated.get("manifest", ""),
                    )
                    outcome = "abstained"
                    return FindingOutcome(None, False, outcome, decision_taken)
                case_policy_files = error.get("case_migration_policy") or {}
                edits = with_provided_files(generated.get("edits", []), case_policy_files)
                generated["edits"] = edits
                authorize_provided_files(
                    evidence_analysis.get("coordinated_fix_plan", {}), case_policy_files)
                plan = evidence_analysis.get("coordinated_fix_plan", {})
                required = {
                    (str(item.get("source_file_path", "")), str(item.get("symbol", "")))
                    for item in plan.get("required_edits", [])
                    if isinstance(item, dict)
                }
                planned_edits = [
                    item for item in plan.get("required_edits", [])
                    if isinstance(item, dict)
                ]
                supplied_paths = set()
                edit_issues = []
                # A plan routed here from the single-method path was supported
                # as a one-file change, and one file may be one edit -- a class
                # declaration replaced whole.
                #
                # For every other decision the floor is a property of the
                # authorised plan, not of the decision label. A confirmed
                # single-file repair rejected only for claiming automatic
                # generation is relabelled MULTI_FILE_FIX_PROPOSED_FOR_REVIEW
                # by route_unsettled_compatibility_to_review while its plan
                # still requires exactly one edit, and a fixed floor of two
                # discarded that complete, semantically valid draft
                # (run_20260917_230558). Taking the smaller of the plan size
                # and minimum_planned_files -- the same function the
                # distinct-file check below uses -- keeps the two-file
                # evidence requirement for decisions that auto-apply without
                # letting the label outvote the plan.
                #
                # A plan with no required edits authorises nothing, so it
                # cannot lower the floor either: there the strictest floor
                # stays, which is what keeps a lone edit from passing as a
                # coordinated change when no plan states otherwise.
                #
                # The rule lives in utils.disposition so the applicator
                # applies the same one: a separate copy there kept a fixed
                # floor of two and refused a patch this check had accepted.
                minimum_edits = 1 if routed_from_single_method or one_method_plan else (
                    minimum_coordinated_edits(decision, len(planned_edits))
                )
                if (not isinstance(edits, list)
                        or not (minimum_edits <= len(edits) <= 64)):
                    edit_issues.append(
                        f"coordinated edit count must be between {minimum_edits} and 64"
                    )
                    edits = []
                for item in edits:
                    if not isinstance(item, dict):
                        edit_issues.append("every coordinated edit must be an object")
                        continue
                    normalized = normalize_coordinated_edit(item)
                    item.clear()
                    item.update(normalized)
                    relative = Path(str(item.get("source_file_path", "")))
                    original = str(item.get("original_code", ""))
                    corrected = str(item.get("corrected_code", ""))
                    symbol = str(item.get("symbol", ""))
                    if relative.is_absolute() or ".." in relative.parts:
                        edit_issues.append(f"unsafe repository path: {relative}")
                    # A file the remediation policy provides (a bundled root
                    # certificate) is written verbatim by the pipeline, not
                    # by the model; only those exact paths extend the list.
                    provided_paths = {
                        str(entry.get("path", "")).replace("\\", "/")
                        for entry in (error.get("case_migration_policy") or {})
                        .get("provided_files") or [] if isinstance(entry, dict)
                    }
                    if relative.suffix.lower() not in {
                        ".java", ".kt", ".kts", ".gradle", ".toml", ".xml",
                        ".properties", ".lockfile",
                    } and relative.as_posix() not in provided_paths:
                        edit_issues.append(f"unsupported source path: {relative}")
                    shape_issues = validate_coordinated_edit_shape(item)
                    if shape_issues:
                        edit_issues.append(
                            f"incomplete edit for {relative}: "
                            + ", ".join(shape_issues)
                        )
                    if original == corrected:
                        edit_issues.append(f"no-op edit for {relative}:{symbol}")
                    item["original_code_sha256"] = hashlib.sha256(
                        original.encode("utf-8")
                    ).hexdigest()
                    supplied_paths.add(relative.as_posix())
                # A planned file whose draft declared that it needs no change
                # is accounted for, not missing. So is an existing test file
                # whose edit the behaviour review dropped as testing nothing
                # the change touches (Cryptomator's DoLicenseCheckTest,
                # 2026-10-24): the plan listed it, the review removed it on
                # purpose, and requiring it back would reject the patch.
                required_paths = {path for path, _symbol in required
                                  if (Path(str(error.get("repository_path", ""))) / path).is_file()
                                  } - set(generated.get("unchanged_files") or [])
                required_paths -= set(behavior_review_dropped_tests(generated)) - supplied_paths
                if required_paths and not required_paths.issubset(supplied_paths):
                    edit_issues.append("generated edits do not cover the required plan")
                policy_tests = (
                    case_policy.get("required_tests", [])
                    if isinstance(case_policy, dict) else []
                )
                has_test_edit = any(
                    isinstance(item, dict) and (
                        item.get("edit_kind") == "test"
                        or (item.get("edit_kind") == "create" and re.search(
                            r"(?:^|/)src/(?:test|androidTest)/",
                            str(item.get("source_file_path", "")), re.IGNORECASE,
                        ))
                    )
                    for item in edits
                )
                # A test left out on purpose: none could check this change
                # (coordinated_generation, untestable_reason), every one
                # generated tested nothing (_without_rejected_new_tests) or
                # held every regression the review found (LocalSend's
                # loadOrCreateKeyStore, 2026-10-29: the draft was then
                # reviewed again without it), the behaviour review dropped
                # every one as asserting
                # behaviour the change does not alter (NotallyX's md5Hash,
                # 2026-10-29: a reviewed SHA-256 patch with no regression
                # became an issue once its only test edit was dropped), or
                # the accepted plan planned none, as its prompt allows for a
                # change that only replaces a value (NotallyX's md5Hash and
                # Orgzly's sha1 after the replan, 2026-10-29: plan
                # required_tests [] and no test path, rejected by the
                # policy's required_tests).
                accepted_plan = (evidence_analysis.get("v2") or {}).get("patch_plan")
                plan_planned_no_test = (
                    isinstance(accepted_plan, dict)
                    and "required_tests" in accepted_plan
                    and not [item for item in accepted_plan.get("required_tests") or []
                             if str(item).strip()]
                    and not any(
                        isinstance(item, dict)
                        and is_test_path(str(item.get("source_file_path", "")))
                        for item in accepted_plan.get("required_edits") or []
                    )
                )
                untestable = [
                    str(item.get("detail") or item.get("reason"))
                    for item in generated.get("skipped_planned_files") or []
                    if isinstance(item, dict) and item.get("reason") == "test_cannot_exercise"
                ] + ([str(generated.get("dropped_generated_tests_reason")
                          or "every generated test failed the test-quality checks")]
                     if generated.get("dropped_generated_tests") else []) + (
                    ["the behaviour review found the generated tests assert "
                     "behaviour the change does not alter"]
                    if behavior_review_dropped_tests(generated) else []) + (
                    ["the plan found no unit test that observes this change"]
                    if plan_planned_no_test else [])
                # A supported plan held to its test after the pipeline itself
                # left the test out goes to review instead of being rejected:
                # Orgzly's MiscUtils.sha1 (2026-10-25) lost a complete,
                # reviewed SHA-256 patch because its only planned test was an
                # androidTest, which validation compiles but never runs. Every
                # reason above counts -- the test could not exercise the
                # change, every generated one tested nothing, or the review
                # dropped it as testing nothing the change alters. Authority
                # only goes down, and dependency and digest decisions keep
                # their own labels (demote_incomplete_scope_to_review). Only
                # a draft the behaviour review judged: a draft salvaged
                # without its generated test was never reviewed, and a
                # supported decision does not ship it unreviewed.
                if (policy_tests and not has_test_edit and untestable
                        and decision in {"MULTI_FILE_FIX_SUPPORTED", "FIX_SUPPORTED"}
                        and behavior_review_judged(generated)):
                    self.logger.info(
                        "%s: %s without a test the pipeline could run (%s); "
                        "generated for review instead of rejected",
                        method_name, decision, "; ".join(dict.fromkeys(untestable)),
                    )
                    evidence_analysis["demoted_from"] = decision
                    decision = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
                    decision_taken = decision
                    evidence_analysis["decision"] = decision
                    generated["decision"] = decision
                    review_only = True
                    # The evidence review was saved with the supported
                    # decision; statistics and previews read it, so it says
                    # what the solution says.
                    try:
                        review_record = json.loads(
                            evidence_file.read_text(encoding="utf-8"))
                        if isinstance(review_record.get("analysis"), dict):
                            review_record["analysis"]["decision"] = decision
                            review_record["analysis"]["demoted_from"] = (
                                evidence_analysis["demoted_from"])
                        review_record["status"] = "PROPOSED_FIX_REQUIRES_REVIEW"
                        _write_json_atomic(evidence_file, review_record)
                    except (OSError, ValueError) as exc:
                        self.logger.warning(
                            "Could not record the review demotion in %s: %s",
                            evidence_file, exc,
                        )
                    # The plan's own publication blockers are what the
                    # reviewer has to settle without a test, so they join the
                    # manual checks.
                    plan_blockers = [
                        str(item) for item in
                        ((evidence_analysis.get("v2") or {}).get("patch_plan") or {})
                        .get("publication_blockers") or []
                        if str(item).strip()
                    ]
                    policy_tests = list(dict.fromkeys(
                        [*policy_tests, *plan_blockers]))
                if policy_tests and not has_test_edit and untestable and review_only:
                    assumptions = generated.get("review_assumptions")
                    if not isinstance(assumptions, list):
                        assumptions = []
                    assumptions.append(
                        "No automated test accompanies this patch ("
                        + "; ".join(dict.fromkeys(untestable)) + "). Before merging, "
                        "manually confirm: " + "; ".join(str(item) for item in policy_tests)
                    )
                    generated["review_assumptions"] = assumptions
                    generated["manual_review_required"] = True
                elif policy_tests and not has_test_edit:
                    repo_root = Path(str(error.get("repository_path", "")))
                    if not review_only:
                        edit_issues.append(
                            "case policy requires at least one concrete "
                            "test-file edit"
                        )
                    elif repository_has_test_infrastructure(repo_root):
                        # Tests are optional for a reviewed patch, as the
                        # plan validation already decided (2026-10-15): a
                        # missing one is a note for the maintainer, not a
                        # rejection. Rejecting here still dropped AmaRadio's
                        # confirmed MD5 cache-key fix (2026-10-16) after its
                        # plan had passed.
                        assumptions = generated.get("review_assumptions")
                        if not isinstance(assumptions, list):
                            assumptions = []
                        assumptions.append(
                            "No automated test accompanies this patch. Before "
                            "merging, confirm: "
                            + "; ".join(str(item) for item in policy_tests)
                        )
                        generated["review_assumptions"] = assumptions
                        generated["manual_review_required"] = True
                    else:
                        # This repository has no test source root or test
                        # file at all (no test/androidTest directory, no
                        # *Test.{java,kt} anywhere), so authoring a new test
                        # file would also require guessing at build wiring
                        # (a test source set, a JUnit dependency) with no
                        # evidence any of it is correct -- a materially
                        # different, riskier kind of change than the finding
                        # itself authorizes. Only review-only decisions
                        # (never an auto-applied one) may fall back to
                        # publishing without an automated test, and only by
                        # documenting the exact manual verification a
                        # maintainer must still do.
                        assumptions = generated.get("review_assumptions")
                        if not isinstance(assumptions, list):
                            assumptions = []
                        assumptions.append(
                            "This repository has no existing test "
                            "infrastructure (no test/androidTest source "
                            "root or *Test file), so this patch adds none "
                            "and the fix is not verified automatically. "
                            "Before merging, manually confirm: "
                            + "; ".join(str(item) for item in policy_tests)
                        )
                        generated["review_assumptions"] = assumptions
                        generated["manual_review_required"] = True
                if (decision in MULTI_FILE_DECISIONS
                        and len(supplied_paths) < minimum_planned_files(decision)):
                    edit_issues.append("multi-file fix must edit at least two distinct files")
                if str(generated.get("decision", "")).upper() != decision:
                    edit_issues.append("coordinated generation abstained")
                generated_review = generated.get("manual_review_required")
                review_assumptions = generated.get("review_assumptions", [])
                # The adjudication already wrote down why this decision needs
                # review. MetadataRemover's SecureRandom fix (subset_newpr2,
                # 2026-10-20) was dropped only because the generator returned
                # an empty list that time, although the adjudication carried
                # four assumptions. They are operator-facing (the PR body does
                # not render them), so inheriting them hides nothing.
                if review_only and not any(
                        str(item).strip() for item in review_assumptions or []):
                    # A decision routed to review here, not by the
                    # adjudication, may have no assumption of its own: the
                    # routing's reason is the one the reviewer needs.
                    inherited = adjudicated_review_assumptions(evidence_analysis) or (
                        [routed_review_note] if routed_review_note else [])
                    if inherited:
                        review_assumptions = inherited
                        generated["review_assumptions"] = inherited
                if review_only and generated_review is not True:
                    edit_issues.append(
                        "review-only generation must require manual review"
                    )
                if review_only and (
                    not isinstance(review_assumptions, list)
                    or not any(str(item).strip() for item in review_assumptions)
                ):
                    edit_issues.append(
                        "review-only generation must state review assumptions"
                    )
                if edit_issues:
                    shortfall = generation_shortfall(edit_issues, evidence_analysis)
                    if shortfall and not code_gone_from_base(
                            error.get("contribution_base") or {}):
                        # Checks on the assembled drafts (an edit count, a
                        # no-op edit, a plan file left uncovered): the
                        # pipeline's shortfall, as on the path above.
                        self.record_generation_shortfall(
                            error, apk_name, method_name, edit_issues, shortfall)
                        self.persist_blocked_issue_candidate(
                            error, evidence_analysis, evidence_file, apk_name,
                            method_name, edit_issues, collateral_only=True,
                        )
                    else:
                        self.persist_blocked_issue_candidate(
                            error, evidence_analysis, evidence_file, apk_name,
                            method_name, edit_issues,
                        )
                    self.logger.warning(
                        "Coordinated generation rejected for %s: %s",
                        method_name, "; ".join(edit_issues),
                    )
                    outcome = "abstained"
                    return FindingOutcome(None, False, outcome, decision_taken)
                explanation = str(generated.get("explanation", "")).strip()
                if not explanation:
                    self.logger.warning(
                        "Coordinated generation rejected for %s: empty explanation",
                        method_name,
                    )
                    outcome = "abstained"
                    return FindingOutcome(None, False, outcome, decision_taken)
                # The published method_body is meant to show the corrected
                # method. Taking the first edit that merely matches the target
                # path picked a `delete` on net.gaast.giggity -- whose
                # corrected_code is empty by definition -- so the solution went
                # out with an empty body and SolutionMapper dropped it. Prefer
                # an edit that actually carries replacement code.
                target_path = str(error.get("source_file_path", ""))
                substantive = {"method", "declaration", "constant", "create"}

                def _primary_rank(item: Dict) -> tuple:
                    same_file = str(item.get("source_file_path", "")) == target_path
                    kind = str(item.get("edit_kind", ""))
                    has_code = bool(str(item.get("corrected_code", "")).strip())
                    # Carrying code outranks being in the target file: an
                    # empty body is never a useful representative of the
                    # correction, and preferring the target file first is
                    # exactly how a `delete` on it won here before.
                    return (
                        not has_code,
                        not same_file,
                        kind not in substantive,
                        kind != "method",
                    )

                primary = min(edits, key=_primary_rank)
                primary_path = str(primary.get("source_file_path", ""))
                confidence = str(generated.get("confidence", "medium")).lower()
                confidence_percent = {"high": 90, "medium": 70, "low": 45}.get(
                    confidence, 60
                )
                metrics = coordinated_quality_metrics(
                    edits, error.get("spec", ""), method_name,
                )
                code_is_syntactically_valid = metrics["is_syntactically_valid"]
                quality_score = metrics["quality_score"]
                quality_issues = metrics["issues"]
                return FindingOutcome({
                    "solution_metadata": {
                        "apk_name": apk_name,
                        "error_index": error.get("error_index", 0),
                        "method_name": method_name,
                        "csv_class": error.get("csv_class", ""),
                        "relative_method_file": str(Path(apk_name) / primary_path),
                        "source_file_path": primary_path,
                        "spec": error.get("spec", ""),
                        "tool": error.get("tool", ""),
                        "repository_guidance": error.get("repository_guidance", []),
                        "branch_policy": error.get("branch_policy", {}),
                        "compatibility_policy": error.get("compatibility_policy", {}),
                        "digest_contract_assessment": digest_contract,
                        "migration_assessment": migration_assessment,
                        "review_assumptions": generated.get(
                            "review_assumptions", []
                        ),
                        "review_blockers": generated.get(
                            "semantic_validation", {}
                        ).get("warnings", []),
                        # The behaviour-review contract this draft passed;
                        # None when the review did not judge it, so the
                        # statistics and the publication gate can tell a
                        # stale or unreviewed draft apart.
                        "review_contract_version": generated.get(
                            "review_contract_version"
                        ),
                        "publication_considerations": (
                            human_decided_publication_considerations(
                                evidence_analysis
                            )
                        ),
                        # Every publication blocker with its category and
                        # what the behaviour review found, for the verdict
                        # pr_generator gives after the build.
                        "publication_blocker_record": publication_blocker_record(
                            evidence_analysis, generated.get("behavior_review_summary"),
                        ),
                        "required_tests": generated.get("required_tests", []),
                        "change_telemetry": summarize_patch_changes(edits),
                        "decision": decision,
                        # One construction site for all three flags, so a
                        # change to the publication gate cannot land in one
                        # code path and miss the other -- which is exactly
                        # what happened when this and the single-method path
                        # below were edited separately.
                        **FindingDisposition(
                            decision=decision,
                            publication_status=str(
                                evidence_analysis.get('publication_status', '')
                            ),
                        ).as_metadata(),
                        "llm_architecture": {
                            "version": self.architecture_version,
                            **evidence_analysis.get("versions", {}),
                            "input_hashes": evidence_analysis.get(
                                "input_hashes", {}
                            ),
                        },
                        "authorized_patch_plan": evidence_analysis.get(
                            "coordinated_fix_plan", {}
                        ),
                        "stored_value_contract": error.get("stored_value_contract", {}),
                        "semantic_validation": generated.get(
                            "semantic_validation", {"status": "not_run"}
                        ),
                        "fix_scope": (
                            "dependency" if decision in {
                                "DEPENDENCY_FIX_SUPPORTED",
                                "DEPENDENCY_REPLACEMENT_SUPPORTED",
                            }
                            else ("review_only" if review_only else "coordinated")
                        ),
                        "code_quality_metrics": {
                            "is_syntactically_valid": code_is_syntactically_valid,
                            "quality_score": quality_score,
                            "issues_detected": quality_issues,
                        },
                        "tokens_used": {
                            key: tokens_after.get(key, 0) - tokens_before.get(key, 0)
                            for key in ("input", "output")
                        },
                    },
                    "original_error": {
                        "error_message": error_msg,
                        "unique_error_id": error.get("unique_error_id", ""),
                        "spec": error.get("spec", ""),
                        "tool": error.get("tool", ""),
                        "detector_context": error.get("detector_context") or {},
                        "method_body": primary.get("original_code") or method_body,
                    },
                    "llm_analysis": {
                        "root_cause": generated.get("root_cause", confirmed_cause),
                        "error_explanation": explanation,
                        "confidence_percent": confidence_percent,
                        "required_tests": generated.get("required_tests", []),
                        "review_assumptions": generated.get(
                            "review_assumptions", []
                        ),
                    },
                    "evidence_analysis": evidence_analysis,
                    "corrected_implementation": {
                        "method_body": primary.get("corrected_code") or method_body,
                        "explanation": explanation,
                        "coordinated_edits": edits,
                        "alternative_solution": None,
                    },
                }, True, outcome, decision_taken)
            # Every generating decision returned above; nothing else generates.
            self.logger.warning("%s: no generation path for decision %s", method_name, decision)
            outcome = "abstained"
            return FindingOutcome(None, False, outcome, decision_taken)

        except (CallBudgetExhausted, ProviderAuthenticationError):
            # Decided per finding by analyse_one, not reported as a crash.
            raise

        except ProviderContentRefusal as exc:
            self.logger.error(
                "Provider refused this finding's content (%s): %s",
                exc.code or "no code", exc,
            )
            return FindingOutcome(
                None, False, "provider_refused", decision_taken, str(exc),
            )

        except ProviderTransientFailure as exc:
            # The provider failed to answer after the call's resends: not a
            # verdict on this finding and not a defect here. The finding is
            # left for a resume pass (run_status.RESUMABLE_FAILURE_KINDS).
            self.logger.error(
                "Provider unavailable for this finding (%s): %s",
                getattr(exc, "code", "") or type(exc).__name__, exc,
            )
            return FindingOutcome(
                None, False, "provider_unavailable", decision_taken,
                f"{type(exc).__name__}: {exc}",
            )

        except ProviderRequestRejected as exc:
            # The provider rejected the request itself (a coded 4xx such as
            # context_length_exceeded, an unknown code twice): no verdict on
            # the text, and a resume would send the same request for the same
            # answer. A failed analysis: concluded, and it fails the step.
            self.logger.error(
                "Provider rejected the request for this finding (%s): %s",
                exc.code or "no code", exc,
            )
            return FindingOutcome(
                None, False, "failed_analysis", decision_taken,
                f"{type(exc).__name__}: {exc}",
            )

        except Exception as e:
            # exception(), not error(): a crash here is a defect in this
            # pipeline, and the message alone never said which line raised.
            self.logger.exception("Failed to process error: %s", e)
            return FindingOutcome(
                None, False, "crashed", decision_taken, f"{type(e).__name__}: {e}",
            )

    def analyse_one(self, error: Dict) -> Tuple[Dict, FindingOutcome]:
        """Analyse a single finding, pairing it with what the analysis found.

        The result comes back as a value, so concurrent workers share nothing
        but the generator's collaborators.
        """
        bypass = finding_bypasses_cache(error)
        if bypass:
            self.logger.info(
                "LLM cache reads bypassed for %s", error.get("unique_error_id")
                or error.get("method_name", "unknown"),
            )
        # The replay scope holds this unit's identical calls (a replan's
        # research, extraction and adjudication), under the bypass too: the
        # bypass refreshes the persistent cache, not what this analysis
        # already asked once.
        with finding_budget(**budget_limits_from_environment()) as budget, \
                cache_reads_bypassed(bypass), finding_replay_scope():
            error, result = self._analyse_one_within_budget(error)
        return error, replace(result, llm_budget=budget.summary())

    def _analyse_one_within_budget(self, error: Dict) -> Tuple[Dict, FindingOutcome]:
        try:
            return error, self.process_finding(error)
        except CallBudgetExhausted as exc:
            self.logger.error(
                "Model-call budget exhausted for %s: %s",
                error.get("method_name", "unknown"), exc,
            )
            return error, FindingOutcome(
                None, False, "llm_budget_exhausted", "", str(exc),
            )
        except ProviderAuthenticationError as exc:
            self.logger.error("Provider authentication failed: %s", exc)
            return error, FindingOutcome(
                None, False, "provider_auth_failed", "", str(exc),
            )
        except ProviderContentRefusal as exc:  # noqa: BLE001 - see process_finding
            self.logger.error(
                "Provider refused the content of %s (%s): %s",
                error.get("method_name", "unknown"), exc.code or "no code", exc,
            )
            return error, FindingOutcome(
                None, False, "provider_refused", "", str(exc),
            )
        except ProviderTransientFailure as exc:  # see process_finding
            self.logger.error(
                "Provider unavailable for %s (%s): %s",
                error.get("method_name", "unknown"),
                getattr(exc, "code", "") or type(exc).__name__, exc,
            )
            return error, FindingOutcome(
                None, False, "provider_unavailable", "", f"{type(exc).__name__}: {exc}",
            )
        except ProviderRequestRejected as exc:  # see process_finding
            self.logger.error(
                "Provider rejected the request for %s (%s): %s",
                error.get("method_name", "unknown"), exc.code or "no code", exc,
            )
            return error, FindingOutcome(
                None, False, "failed_analysis", "", f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - one finding must not end the run
            self.logger.exception(
                "Unhandled failure analysing %s: %s",
                error.get("method_name", "unknown"), exc,
            )
            return error, FindingOutcome(
                None, False, "crashed", "", f"{type(exc).__name__}: {exc}",
            )

    def analyse_findings(self, errors: List[Dict], workers: int):
        """Yield analysed findings, sequentially or from a worker pool.

        A single worker keeps the original sequential path exactly, including
        the order results arrive in, so the default run is unchanged.
        """
        if workers <= 1 or len(errors) <= 1:
            for error in errors:
                yield self.analyse_one(error)
            return
        self.logger.info(
            "Analysing %d findings with %d workers", len(errors), workers
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # map, not as_completed: results are consumed in submission order,
            # so a run's logs and counters do not depend on which provider
            # call happened to answer first.
            yield from pool.map(self.analyse_one, errors)
