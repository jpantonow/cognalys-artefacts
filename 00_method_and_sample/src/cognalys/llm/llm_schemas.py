#!/usr/bin/env python3
"""
Strict JSON-schema contracts for LLM-structured pipeline responses.

Extracted from generate_error_fixes.py (Phase 2 of the
modularization plan).
"""
from __future__ import annotations

from typing import Dict, List, Optional


def _strict_object(properties: Dict, description: str = "") -> Dict:
    schema = {"type": "object", "properties": properties,
              "required": list(properties), "additionalProperties": False}
    if description:
        schema["description"] = description
    return schema


def _string(description: str = "", enum: Optional[List[str]] = None) -> Dict:
    schema = {"type": "string"}
    if description:
        schema["description"] = description
    if enum:
        schema["enum"] = enum
    return schema


def _array(items: Dict, description: str = "") -> Dict:
    schema = {"type": "array", "items": items}
    if description:
        schema["description"] = description
    return schema


def maintainer_summary_schema() -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"title": {"type": "string"}, "summary": {"type": "string"}},
        "required": ["title", "summary"],
    }


def issue_text_schema() -> Dict:
    """The four presentation fields of an issue, rewritten (issues.issue_text)."""
    return _strict_object({field: _string() for field in (
        "finding_summary", "why_no_pr", "maintainer_question", "proposed_next_step")})


def evidence_analysis_schema() -> Dict:
    """Strict contract for repository evidence and scope analysis."""
    source = _strict_object({
        "title": _string("Primary or official source title."),
        "url": _string("Direct source URL."),
        "supported_claim": _string("Claim directly supported by the source."),
    })
    # Same contract as the v2 predicate in pipeline_architecture: the status
    # answers whether the stated requirement holds, so the requirement itself
    # must be phrased positively or the two invert each other.
    predicate = _strict_object({
        "event": _string(
            "A single requirement stated positively, as a condition that is "
            "supposed to hold. Never phrase it as a violation or as the thing "
            "going wrong; the status field carries whether it holds."
        ),
        "status": _string(
            "confirmed: the evidence establishes that the requirement holds. "
            "violated: the evidence establishes that it does NOT hold, which "
            "is a real defect in the reviewed code. unknown: the evidence is "
            "insufficient to decide. Never use violated to say a "
            "violation-shaped requirement is false.",
            enum=["confirmed", "violated", "unknown"],
        ),
        "evidence": _string("Exact supplied or directly cited evidence."),
    })
    required_edit = _strict_object({
        "source_file_path": _string("Repository-relative path."),
        "symbol": _string(), "reason": _string(),
        "create_new_file": {
            "type": "boolean",
            "description": "True only for a planned dedicated test file that does not exist.",
        },
    })
    reviewed_file = _strict_object({
        "source_file_path": _string("Repository-relative path."),
        "action": _string(enum=["edit", "required_test", "unchanged"]),
        "symbols": _array(_string()), "evidence": _string(),
    })
    unresolved_relation = _strict_object({
        "kind": _string(enum=[
            "local_caller", "ambiguous_dispatch", "missing_source_file",
            "local_data_flow", "build_configuration", "classpath_limitation",
            "external_backend_contract", "deployed_client_compatibility",
            "external_protocol", "other",
        ]),
        "blocking_for_generation": {"type": "boolean"},
        "blocking_for_publication": {"type": "boolean"},
        "description": _string(),
    })
    return _strict_object({
        "external_sources": _array(source),
        "observed_events": _array(_string()),
        "predicate_assessment": _array(predicate),
        "confirmed_facts": _array(_string()),
        "unsupported_hypotheses": _array(_string()),
        "missing_evidence": _array(_string()),
        "confirmed_cause": _string("Confirmed cause, or an empty string."),
        "forward_compatibility_assessment": _strict_object({
            "status": _string(enum=["preserved", "regression_risk", "unknown"]),
            "dimensions_checked": _array(_string()), "evidence": _string(),
            "strategy": _string(),
        }),
        "migration_assessment": _strict_object({
            # The last three come from the V2 extraction vocabulary. Without
            # them a V2 answer of "identifier"/"security"/"external_contract"
            # had no legal legacy value and was silently rewritten to
            # "unknown", discarding a classification the model had actually
            # established (observed on net.gaast.giggity's hashCode finding).
            "usage_kind": _string(enum=["cache", "runtime_discriminator", "ephemeral_sensitive", "persistence", "protocol", "public_api", "fingerprint", "password", "identifier", "security", "external_contract", "unknown"]),
            "evidence": _array(_string()),
            "legacy_compatibility_required": {"type": "boolean"},
            "safe_strategy": _string(), "policy_authorized": {"type": "boolean"},
        }),
        "digest_contract_assessment": _strict_object({
            "security_relevance": _string(enum=[
                "confirmed", "plausible", "not_established", "false",
            ]),
            "contract_kind": _string(enum=[
                "internal", "external_backend", "multi_repository",
                "non_security", "unknown",
            ]),
            "expected_digest_source": _string(
                "Exact repository evidence identifying where the expected digest comes from."
            ),
            "backend_repository_available": {"type": "boolean"},
            "backend_change_required": {"type": "boolean"},
            "client_only_change_safe": {"type": "boolean"},
            "rollout_strategy": _string(
                "Evidence-backed direct migration, dual-read rollout, or empty string."
            ),
            "pull_request_note": _string(
                "Concrete backend/rollout prerequisite to expose in review artifacts."
            ),
        }),
        "decision": _string(enum=[
            "FIX_SUPPORTED", "MULTI_FILE_FIX_SUPPORTED", "DEPENDENCY_FIX_SUPPORTED",
            "DEPENDENCY_REPLACEMENT_SUPPORTED", "MULTI_FILE_FIX_REQUIRES_POLICY",
            "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW",
            "DEPENDENCY_FIX_REQUIRES_POLICY", "UPSTREAM_FIX_REQUIRES_POLICY",
            "SAFE_INTERNAL_DIGEST_MIGRATION",
            "MULTI_REPOSITORY_MIGRATION_SUPPORTED",
            "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY",
            "EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX",
            "NON_SECURITY_HASH_USAGE",
            "CONFIRMED_SPEC_VIOLATION_NON_SECURITY_USAGE", "NO_SAFE_FIX",
        ]),
        "dependency_fix_assessment": _strict_object({
            "external_class_confirmed": {"type": "boolean"},
            "declared_artifact": _string(), "current_version": _string(),
            "target_version": _string(), "target_artifact": _string(),
            "official_migration_source": _string(),
            "upgrade_kind": _string(enum=["patch", "minor", "major", "replacement", "unknown"]),
            "build_files": _array(_string()),
            "call_site_migration_required": {"type": "boolean"},
            "compatibility_constraints_satisfied": {"type": "boolean"},
            "replacement_preserves_required_capabilities": {"type": "boolean"},
            "fork_or_vcs_dependency": {"type": "boolean"},
        }),
        "coordinated_fix_plan": _strict_object({
            "affected_symbols": _array(_string()),
            "required_edits": _array(required_edit),
            "required_tests": _array(_string()),
            "compatibility_policy_required": {"type": "boolean"},
            "scope_evidence": _array(_string()),
        }),
        "scope_assessment": _strict_object({
            "status": _string(enum=["complete", "incomplete", "not_applicable"]),
            "local_code_scope": _string(enum=["complete", "incomplete", "not_applicable"]),
            "external_contract_scope": _string(enum=["complete", "unresolved", "not_applicable"]),
            "multi_file_change_required": {"type": "boolean"},
            "reviewed_files": _array(reviewed_file),
            "unresolved_relations": _array(unresolved_relation),
        }),
        "issue_candidate": _strict_object({
            "eligible": {"type": "boolean"}, "title": _string(),
            "finding_summary": _string(), "why_no_pr": _string(),
            "maintainer_question": _string(), "proposed_next_step": _string(),
        }),
    })


def coordinated_fix_schema(forced_decision: str = "") -> Dict:
    edit = _strict_object({
        "source_file_path": _string("Repository-relative path."),
        "symbol": _string(),
        "edit_kind": _string(enum=["method", "declaration", "constant", "test", "create", "delete"]),
        "original_code": _string("Exact existing source block."),
        "corrected_code": _string("Complete replacement source block; empty only for edit_kind=delete."),
        "reason": _string(),
    })
    decisions = (
        [forced_decision] if forced_decision else
        ["MULTI_FILE_FIX_SUPPORTED", "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW",
         "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY",
         "DEPENDENCY_FIX_SUPPORTED", "DEPENDENCY_REPLACEMENT_SUPPORTED",
         "NO_SAFE_FIX"]
    )
    return _strict_object({
        "decision": _string(enum=decisions),
        "root_cause": _string(), "explanation": _string(),
        "edits": _array(edit), "required_tests": _array(_string()),
        "manual_review_required": {"type": "boolean"},
        "review_assumptions": _array(_string(
            "Explicit compatibility or lifecycle premise requiring review."
        )),
        "confidence": _string(enum=["high", "medium", "low"]),
    })


def coordinated_file_draft_schema(forced_decision: str) -> Dict:
    """Strict contract for one persisted file of a coordinated fix."""
    edit = _strict_object({
        "source_file_path": _string("The requested repository-relative path."),
        "symbol": _string(),
        "edit_kind": _string(enum=["method", "declaration", "constant", "test", "create", "delete"]),
        "original_code": _string("Exact existing source block."),
        "corrected_code": _string("Complete replacement source block; empty only for edit_kind=delete."),
        "reason": _string(),
    })
    return _strict_object({
        "decision": _string(enum=[forced_decision]),
        "source_file_path": _string(),
        "explanation": _string(),
        "edits": _array(edit),
        # True only with no edits: the planned file already fits the contracts
        # the other files provide (a caller whose entry point is kept).
        "no_change_required": {"type": "boolean"},
        "provided_contracts": _array(_string()),
        "required_contracts": _array(_string()),
        "required_tests": _array(_string()),
        "manual_review_required": {"type": "boolean"},
        "review_assumptions": _array(_string()),
        "confidence": _string(enum=["high", "medium", "low"]),
    })


def behavior_review_schema() -> Dict:
    """What a reviewer of a finished patch reports before it is built."""
    regression = _strict_object({
        "source_file_path": _string(),
        "scenario": _string("The caller, input or state in which behaviour changes."),
        "before": _string("What the original code does in that scenario."),
        "after": _string("What the patched code does instead."),
        # Whether a redraft on the same plan can avoid it (review contract
        # 19): the pipeline plans again before the plan's last draft only
        # when two drafts of the plan in a row draw a regression whose source
        # is the plan. Over the recorded reviews, 20 units whose first plan
        # regressed reached a clean review by a redraft on that plan, mostly
        # fixing a slip the review had named (Trigger's spinner token, Tokn's
        # caller-supplied GCM IV, KeyPass's test alias).
        "source": _string(
            "plan only when no draft that follows the plan could avoid this "
            "scenario; draft otherwise and whenever you are unsure.",
            enum=["draft", "plan"]),
        "source_reason": _string(
            "For plan, the required edit, invariant or forbidden change it "
            "follows from; otherwise an empty string."),
    })
    # An edit the reported finding does not need, named the way the drafts
    # name their edits (coordinated_file_draft_schema: path and symbol).
    other_edit = _strict_object({
        "source_file_path": _string(),
        "symbol": _string("The edit's symbol, exactly as the edit names it."),
    })
    return _strict_object({
        "regressions": _array(regression),
        # The review's answer to each adjudication concern (contract 20),
        # recorded in behavior_review.json and read by no gate.
        "concern_verdicts": _array(_strict_object({
            "concern": {"type": "integer",
                        "description": "The concern's position in adjudication_concerns, starting at 1."},
            "verdict": _string(
                "does_what_it_warns_against: this patch does the specific thing the concern warns "
                "against; does_not: it does not; asks_for_verification: the concern only asks someone "
                "to verify, test or review something this patch does not decide.",
                enum=["does_what_it_warns_against", "does_not", "asks_for_verification"]),
        }), "One entry for each adjudication_concerns entry, in order; empty when there are none."),
        "maintainer_decision_required": {"type": "boolean"},
        "maintainer_decision": _string(
            "The decision only the maintainer can make, or an empty string."),
        # Only a product or compatibility choice reaches the pull request
        # text; a concern about the analyzer or unverified equivalence stays
        # with the operator (Tokn, Clash Fest: 2026-10-28).
        "maintainer_decision_kind": _string(
            "What maintainer_decision is about.",
            enum=["none", "product_or_compatibility_choice",
                  "verification_or_analyzer_concern"]),
        "edits_for_other_findings": _array(other_edit, (
            "Edits that resolve neither the reported finding at its call nor a "
            "joint finding, and that no other edit of this patch needs.")),
        # Effect judged per finding of a joined unit. BlueGate run_v8
        # (2026-10-31): the only change for generateNewKeyPair
        # [KeyPairGeneratorSpec] moved ECGenParameterSpec("secp256r1") into a
        # local, and the PR said it resolved that finding too. LocalSend
        # run_v4 (2026-10-31): the reported finding is judged the same way;
        # the patch changed only the joint KeyStoreSpec call and the pull
        # request was still keyed to the reported KeyPairSpec.
        "findings_without_effect": _array(_strict_object({
            "method": _string(
                "The finding's method, exactly as finding.method_name or "
                "finding.joint_findings names it."),
            "specification": _string(
                "Its specification, exactly as finding.specification or "
                "finding.joint_findings names it."),
            "changed_for_it": _string(
                "The code this patch changes only for this finding (a local "
                "extracted, statements split, a helper added), or an empty string "
                "when the patch changes nothing for it."),
            "reason": _string(
                "Why its checker would still observe the same calls with the same "
                "values at its own reported call."),
        }), (
            "Each finding of the unit -- the reported one or a finding.joint_findings "
            "entry -- whose share of the patch has no effect: its checker would still "
            "observe the same calls, objects and values at its own reported call. "
            "Empty when every finding's share has an effect, or when there are no "
            "joint findings.")),
        # Narrowed so an explicit form that passes a null default on to
        # another governed API (a factory initialised with a null key store)
        # is judged as any other change.
        "explicit_default_only": {
            "type": "boolean",
            "description": (
                "True only when the change for the reported call replaces a null or "
                "omitted argument with an explicit producer of the identical default "
                "object, by the path the platform documents, and every call the "
                "explicit form adds satisfies its own specification: none passes a "
                "null or omitted platform-default argument to another governed API. "
                "When true, patch_has_no_effect is false: the reported call now "
                "receives a value the code did not pass."),
        },
        # One entry per edit, so a test the plan requires can be told apart.
        "beyond_reported_argument": _array(_strict_object({
            "source_file_path": _string(),
            "symbol": _string("The edit's symbol, exactly as the edit names it."),
            "change": _string("What it changes besides the reported argument."),
        }), (
            "With explicit_default_only: edits that change other code than the "
            "reported argument and what only builds its explicit producer (calls "
            "that each satisfy their own specification), leaving out what a joint "
            "finding needs.")),
        "patch_has_no_effect": {
            "type": "boolean",
            "description": (
                "True when the runtime checker would still observe the same calls "
                "with the same values (an object the patch constructs and "
                "discards, never a receiver, argument or return value, does "
                "not count; a call added on the object or value the reported "
                "call uses, such as init, load or a setter, does; a new call "
                "whose catch or fallback reaches, for the reported input, the "
                "original value or another name of the same algorithm, "
                "protocol or provider does not count, unless the new path "
                "demonstrably succeeds for that input on the platforms the "
                "app targets; the same value -- algorithm, curve, protocol or "
                "key material -- produced through another library's or a "
                "lower-level API the checker does not observe, such as a "
                "provider's lightweight API instead of JCA, a native call or a "
                "hand-rolled implementation, does not count, while a different "
                "value the rule allows reached that way does; a governed "
                "object constructed from the parts of one the code already "
                "holds -- all of its own getters or properties, each in its "
                "place, as in KeyPair(kp.public, kp.private), or the entry "
                "the original producer itself stored, re-read from the same "
                "store and alias and repackaged after the producer returned "
                "it -- does not count, like a fake factory around the same "
                "object, and is never a maintainer decision, while one that "
                "leaves out, moves or recomputes a part or is built from "
                "different material (a fresh generation, a key from another "
                "source or entry, different parameters) does, while a value "
                "passed through a round trip that hands back what went in "
                "(encrypted or wrapped and then decrypted or unwrapped under "
                "the same key, encoded and decoded) does not count even "
                "through a freshly generated key; the governed "
                "object or value replaced by a type of the app's own (a data "
                "class, record or class added for it, a Pair, Triple, Map, "
                "list or array) carrying the same parts from the same "
                "sources, so that the governed API is no longer called, does "
                "not count although the checker stops observing that call, "
                "while an object the platform already returns used instead "
                "of a rebuilt one, or other parts or sources, does; for a finding a "
                "runtime monitor reported, a restructuring it observes as the "
                "same calls on the same objects with the same values -- a "
                "helper inlined, statements split -- does not count, however "
                "'analyzer-facing'; a forbidden call the patch keeps or moves "
                "only to find, read or migrate what earlier versions produced, "
                "while what the app produces from now on comes from an allowed "
                "value, does count as a change); false for a change for which "
                "explicit_default_only holds."),
        },
        "no_effect_reason": _string(
            "Why the runtime checker would still observe the same calls, or an empty string."),
        # Contract 20, the user's answer to ESPEC_Q2Q3 question 1: the pull
        # request says where the kept call stays and that RVSec still reports it.
        "forbidden_call_kept_for_earlier_data": _string(
            "When the patch keeps or moves a forbidden call only to find, read, verify or migrate "
            "what earlier versions of the app produced: where that call remains and when it runs, "
            "in one sentence; otherwise an empty string."),
        "unrelated_test_edits": _array(_string(
            "Path of a test file whose edit calls no changed or renamed function "
            "directly and checks no value the production edits produce.")),
        # Salty (2026-10-29): BuildConfig.APP_SALT stopped contributing to new
        # keys; the review could only call that a maintainer decision.
        "dropped_inputs_removed_by_fix": _array(_string(
            "The `input` of a dropped_inputs entry whose removal is itself the fix "
            "(the reported value or argument); the others must still contribute.")),
        "current_code_is_correct_use": {"type": "boolean"},
        "correct_use_reason": _string(
            "Why the reported code already uses the API as documented, or an empty string."),
        "rationale": _string(),
    })
