#!/usr/bin/env python3
"""
Adjudication support: tracing, issue shape, and the analysis record.

Extracted from generate_error_fixes.py (Passo 6 of the modularization plan).
None of these read orchestrator state.

_analysis_record assembles the single analysis record that the scope
validators, the PR and issue writers and the persisted evidence review all
read. It was written to project the staged pipeline back onto a v1 baseline
shape; the v1 pipeline is gone, so the record is now simply this pipeline's
own output format, kept stable because the stored runs under outputs/ are
research data that later analysis reads.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from cognalys.llm.llm_schemas import _strict_object, _string
from cognalys.llm.adjudication_rules import established_function_contract
from cognalys.utils.patch_scope import PatchScope
from cognalys.llm.pipeline_architecture import (
    CONTEXT_BUILDER_VERSION,
    analysis_dismissal_texts,
    collateral_constraint_violations,
    POLICY_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SEMANTIC_RESOLVER_VERSION,
    settled_value_texts,
)


def _trace_context(error: Dict, stage: str,
                      telemetry: Optional[Dict] = None) -> Dict:
    return {
        "finding_id": error.get("unique_error_id", ""),
        "stage": stage,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "context_builder_version": CONTEXT_BUILDER_VERSION,
        "semantic_resolver_version": SEMANTIC_RESOLVER_VERSION,
        "section_telemetry": telemetry or {},
    }


def _issue_schema() -> Dict:
    return _strict_object({
        "eligible": {"type": "boolean"},
        "title": _string(),
        "finding_summary": _string(),
        "why_no_pr": _string(),
        "maintainer_question": _string(),
        "proposed_next_step": _string(),
    })


def _analysis_record(error: Dict, packet: Dict, research: Dict,
    extraction: Dict, adjudication: Dict, plan: Dict, issue: Dict,
) -> Dict:
    claims = extraction.get("claims", [])
    family = packet.get("finding", {}).get("spec_family", "generic")
    family_assessment = extraction.get("family_assessment", {}) or {}
    predicates = []
    for item in extraction.get("predicate_assessment", []):
        ids = item.get("evidence_ids", [])
        evidence = (
            f"Evidence IDs: {', '.join(ids)}. "
            + str(item.get("explanation", ""))
        ).strip()
        # No source expression is spliced in front of this text. It used to
        # be, so that the legacy lexical issue correlation had something
        # deterministic to match, but the regex took the first crypto factory
        # call anywhere in the cited file rather than one this predicate is
        # about: zemzeme's verifyChannelPassword bypass was labelled
        # 'Cipher.getInstance("AES/GCM/NoPadding")', picked up from an
        # unrelated method further down ChannelManager.kt. That gate now
        # reads the claim graph (claim_grounded_cause), so the prefix bought
        # nothing and misdescribed the evidence a maintainer reads.
        predicates.append({
            "event": str(item.get("predicate", "")),
            "status": str(item.get("status", "unknown")),
            "evidence": evidence,
            # Read by collateral_constraint_violations: a requirement from the
            # reported part of the rule is the reported finding, not another.
            "rule_section": str(item.get("rule_section", "")),
        })

    required_edits = [
        {
            "source_file_path": str(item.get("source_file_path", "")),
            "symbol": str(item.get("symbol", "")),
            "reason": str(item.get("reason", "")),
            "create_new_file": item.get("create_new_file") is True,
            "edit_category": str(item.get("edit_category", "")),
            "supporting_claim_ids": list(
                item.get("supporting_claim_ids", [])
            ),
        }
        for item in plan.get("required_edits", []) if isinstance(item, dict)
    ]
    scope = PatchScope.from_required_edits(required_edits)
    reviewed_files = [
        {
            "source_file_path": item["source_file_path"],
            "action": scope.action_for(item["source_file_path"]),
            "symbols": [item["symbol"]] if item["symbol"] else [],
            "evidence": "Planned from adjudicated claims: " + ", ".join(
                next((raw.get("supporting_claim_ids", [])
                      for raw in plan.get("required_edits", [])
                      if raw.get("source_file_path") == item["source_file_path"]), [])
            ),
        }
        for item in required_edits
    ]
    reviewed_paths = {
        item["source_file_path"] for item in reviewed_files
    }
    # The extraction has already classified semantic caller and ambiguity relations.
    # Account for those files without pretending that an ambiguous
    # same-name call is an exact target dispatch or an edit target. This
    # prevents the legacy compatibility validator from demanding edits to
    # unrelated Builder.build declarations while keeping the ambiguity
    # explicit and publication-blocking when appropriate.
    #
    # Mandatory related_source items are credited for the same reason. The
    # packet marks a source mandatory exactly when its path is one of the
    # coverage_requirements files validate_multi_file_scope demands the
    # analysis review (see ArchitectureV2's required_paths), so the whole
    # file was put in front of the adjudicator. Leaving it uncredited made
    # the validator fail a finding for "not reviewing" evidence it had been
    # shown in full: observed on me.blog.korn123.easydiary's ZipHelper Zip
    # Slip finding, where ZipHelperWithVisitor.kt and FullRecoveryWorker.kt
    # were both mandatory SRC items and the abstention was spurious.
    # lexical_caller is credited too -- it is the fallback the packet emits
    # when the semantic index resolves no caller at all, so excluding it
    # reproduced the same false negative on every finding whose callers did
    # not resolve. Supplementary (non-mandatory) sources stay uncredited:
    # they are background context, not a coverage requirement.
    accounted_actions = {
        "resolved_caller": "semantic_resolved_caller",
        "ambiguous_relation": "semantic_ambiguity_accounted",
        "lexical_caller": "lexical_caller_accounted",
        "related_source": "mandatory_context_reviewed",
    }
    for evidence_item in packet.get("evidence_catalog", []):
        kind = str(evidence_item.get("kind", ""))
        action = accounted_actions.get(kind)
        if action is None:
            continue
        if kind == "related_source" and evidence_item.get("mandatory") is not True:
            continue
        data = evidence_item.get("data", {}) or {}
        path = str(data.get("path", data.get("source_file_path", "")))
        if not path or path in reviewed_paths:
            continue
        symbol = str(
            data.get("resolved_symbol_id")
            or data.get("name")
            or data.get("enclosing_symbol")
            or ""
        )
        reviewed_files.append({
            "source_file_path": path,
            "action": action,
            "symbols": [symbol] if symbol else [],
            "evidence": (
                f"{evidence_item.get('evidence_id')} records a "
                f"{kind} relation; it is accounted "
                    "for but is not automatically an edit target."
            ),
        })
        reviewed_paths.add(path)
    blockers = [
        {
            "kind": "other",
            "blocking_for_generation": True,
            "blocking_for_publication": True,
            "description": str(item.get("description", "")),
        }
        for item in adjudication.get("blocking_for_generation", [])
        if isinstance(item, dict)
    ] + [
        {
            "kind": "external_protocol" if family == "tls" else "other",
            "blocking_for_generation": False,
            "blocking_for_publication": True,
            "description": str(item.get("description", "")),
        }
        for item in adjudication.get("blocking_for_publication", [])
        if isinstance(item, dict)
    ]
    # generation_status "supported_for_review" means the adjudicator
    # authorized generating the patch but not applying it unattended (see the
    # adjudication validator: reviewable generation is explicitly weaker than
    # automatic generation). Downstream, though, nothing reads that field:
    # process_error derives review_only -- and with it auto_apply and
    # publication_allowed -- purely from the decision label, and
    # validate_multi_file_scope keys its review-only scope waiver on the same
    # label. So a MULTI_FILE_FIX_SUPPORTED carrying supported_for_review was
    # read as auto-apply by every consumer while the adjudicator had asked for
    # a human, and it simultaneously missed the waiver written for exactly its
    # situation. Project it onto the review-only label so the two agree.
    #
    # FIX_SUPPORTED is normalized the same way and for the same reason: an
    # adjudication that confirms the cause, authorises generation for review
    # and blocks only publication was still read as auto-apply, so the
    # scope waiver never fired and the finding abstained before any code was
    # generated -- twice in run_20260917_230558 (hmdm extract,
    # oxproxion createLanHttpClient), both with an incomplete scope whose
    # only unresolved relations were publication-blocking. The pipeline
    # already relabels FIX_SUPPORTED this way in two other places
    # (plan_edits_beyond_method routing and
    # route_unsettled_compatibility_to_review), so the landing label is the
    # established one for "generate it, a human decides".
    #
    # Dependency and digest decisions are deliberately left alone: rewriting
    # one into this label would discard the family semantics that their own
    # downstream gates depend on.
    decision = str(adjudication.get("decision", "NO_SAFE_FIX"))
    if (decision in {"MULTI_FILE_FIX_SUPPORTED", "FIX_SUPPORTED"}
            and adjudication.get("generation_status") == "supported_for_review"):
        decision = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
    case_policy = error.get("case_migration_policy", {}) or {}
    usage_kind = {
        "tls": "protocol", "message_digest": str(
            family_assessment.get("usage_kind", "unknown")
        ),
    }.get(family, "unknown")
    # Must stay a superset of the extraction enum
    # (security | cache | identifier | runtime_discriminator |
    # external_contract | unknown). It previously omitted three of them, so a
    # precise answer degraded to "unknown" here and the downstream
    # migration assessment lost it. Widening the set does not loosen any gate:
    # promote_bounded_digest_discriminator_review still accepts only cache and
    # runtime_discriminator.
    allowed_usage = {
        "cache", "runtime_discriminator", "ephemeral_sensitive",
        "persistence", "protocol", "public_api", "fingerprint",
        "password", "identifier", "security", "external_contract", "unknown",
    }
    if usage_kind not in allowed_usage:
        usage_kind = "unknown"
    digest = {
        "security_relevance": str(
            family_assessment.get("security_relevance", "not_established")
        ) if family == "message_digest" else "false",
        "contract_kind": str(
            family_assessment.get("expected_value_source", "unknown")
        ) if family == "message_digest" else "unknown",
        "expected_digest_source": "Evidence IDs: " + ", ".join(
            family_assessment.get("evidence_ids", [])
        ) if family == "message_digest" else "",
        "backend_repository_available": False,
        "backend_change_required": (
            family_assessment.get("expected_value_source") == "external_backend"
        ) if family == "message_digest" else False,
        "client_only_change_safe": (
            family_assessment.get("client_only_change_safe") == "yes"
        ) if family == "message_digest" else False,
        "rollout_strategy": "",
        "pull_request_note": "",
    }
    dependency = {
        "external_class_confirmed": family == "dependency",
        "declared_artifact": str(family_assessment.get("artifact", "")),
        "current_version": str(family_assessment.get("current_version", "")),
        "target_version": str(
            family_assessment.get("fixed_version_or_replacement", "")
        ),
        "target_artifact": "",
        "official_migration_source": str(
            family_assessment.get("official_migration_source", "")
        ),
        "upgrade_kind": "unknown",
        "build_files": [],
        "call_site_migration_required": False,
        "compatibility_constraints_satisfied": (
            family_assessment.get("compatibility_status") == "compatible"
        ),
        "replacement_preserves_required_capabilities": False,
        "fork_or_vcs_dependency": False,
    }
    return {
        "architecture_version": "v2",
        "versions": packet.get("versions", {}),
        "input_hashes": packet.get("input_hashes", {}),
        "diagnostic_evidence": packet.get("diagnostic_evidence", {}),
        "cause_status": str(adjudication.get("cause_status", "unknown")),
        "detector_violation": str(
            adjudication.get("detector_violation", "unknown")
        ),
        "runtime_security_impact": str(
            adjudication.get("runtime_security_impact", "unknown")
        ),
        "generation_status": str(
            adjudication.get("generation_status", "blocked")
        ),
        "publication_status": str(
            adjudication.get("publication_status", "blocked")
        ),
        "evidence_status": {
            "cause_status": str(adjudication.get("cause_status", "unknown")),
            "detector_violation": str(
                adjudication.get("detector_violation", "unknown")
            ),
            "runtime_security_impact": str(
                adjudication.get("runtime_security_impact", "unknown")
            ),
            "scope_status": str(adjudication.get("scope_status", "incomplete")),
            "generation_status": str(
                adjudication.get("generation_status", "blocked")
            ),
            "publication_status": str(
                adjudication.get("publication_status", "blocked")
            ),
        },
        "external_sources": [
            {
                "title": str(item.get("authority", "")),
                "url": str(item.get("url", "")),
                "supported_claim": str(item.get("claim", "")),
            }
            for item in research.get("sources", []) if isinstance(item, dict)
        ],
        "observed_events": extraction.get("observed_events", []),
        "predicate_assessment": predicates,
        # Carried on the record rather than recomputed by each reader, so the
        # preview, the issue gate and anyone auditing the run are looking at
        # the same list. Empty on the great majority of findings; when it is
        # not, it is the part of the analysis the reported verdict does not
        # speak for.
        "collateral_violations": collateral_constraint_violations(
            extraction, error,
            dismissal_texts=analysis_dismissal_texts(adjudication),
            settled_texts=settled_value_texts(adjudication),
        ),
        "confirmed_facts": [
            str(item.get("statement", "")) for item in claims
            if item.get("epistemic_status") == "confirmed"
        ],
        "unsupported_hypotheses": [
            str(item.get("statement", "")) for item in claims
            if item.get("epistemic_status") in {"inferred", "hypothesis"}
        ],
        "missing_evidence": extraction.get("unresolved_questions", []),
        "confirmed_cause": str(adjudication.get("confirmed_cause", "")),
        "forward_compatibility_assessment": {
            "status": str(adjudication.get("compatibility_status", "unknown")),
            "dimensions_checked": adjudication.get(
                "required_edit_categories", []
            ),
            "evidence": "Claim IDs: " + ", ".join(
                adjudication.get("decision_claim_ids", [])
            ),
            "strategy": str(adjudication.get(
                "forward_compatibility_strategy", ""
            )),
        },
        "migration_assessment": {
            "usage_kind": usage_kind,
            "evidence": [
                str(item.get("statement", "")) for item in claims
                if item.get("claim_id") in adjudication.get(
                    "decision_claim_ids", []
                )
            ],
            "legacy_compatibility_required": bool(
                adjudication.get("blocking_for_publication")
            ),
            "safe_strategy": str(adjudication.get(
                "forward_compatibility_strategy", ""
            )),
            "policy_authorized": bool(case_policy.get("authorized")),
        },
        "digest_contract_assessment": digest,
        "decision": decision,
        "dependency_fix_assessment": dependency,
        "coordinated_fix_plan": {
            "affected_symbols": sorted({
                item["symbol"] for item in required_edits if item["symbol"]
            }),
            "required_edits": required_edits,
            "required_tests": plan.get("required_tests", []),
            "compatibility_policy_required": (
                not adjudication.get("authorized_behavior_change", {}).get(
                    "authorized", False
                ) and bool(adjudication.get("blocking_for_publication"))
            ),
            "scope_evidence": [
                "Claim IDs: " + ", ".join(
                    adjudication.get("decision_claim_ids", [])
                )
            ],
        },
        "scope_assessment": {
            "status": str(adjudication.get("scope_status", "incomplete")),
            "local_code_scope": str(adjudication.get(
                "scope_status", "incomplete"
            )),
            "external_contract_scope": (
                "unresolved" if adjudication.get("blocking_for_publication")
                else "not_applicable"
            ),
            "multi_file_change_required": scope.is_multi_file,
            "reviewed_files": reviewed_files,
            "unresolved_relations": blockers,
        },
        "issue_candidate": issue or {
            "eligible": False, "title": "", "finding_summary": "",
            "why_no_pr": "", "maintainer_question": "",
            "proposed_next_step": "",
        },
        "v2": {
            "evidence_packet_sha256": packet.get(
                "input_hashes", {}
            ).get("packet_sha256", ""),
            "claims": claims,
            "adjudication": adjudication,
            "patch_plan": plan,
            # Who the reported code serves, only when the extraction
            # established that they depend on the reported behaviour; read by
            # the behaviour review alone (ESPEC_Q2Q3).
            **({"function_contract": contract}
               if (contract := established_function_contract(extraction, packet)) else {}),
        },
    }
