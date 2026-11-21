"""JSON schemas and decision vocabularies of the v2 model calls.

Moved verbatim out of pipeline_architecture.py, which re-exports every
name so existing imports keep working.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from xml.etree import ElementTree
from dataclasses import dataclass
from typing import Any, Iterable

# Importable with only src/llm on the path, or as llm.<module>: src/ for the
# utils package, and this folder for the sibling modules imported by name.

from cognalys.utils.disposition import GENERATING_DECISIONS  # noqa: E402,F401
from cognalys.utils.finding_source import (  # noqa: E402,F401
    ADVISORY_IDENTIFIERS,
    advisory_identifiers,
)

from cognalys.llm.pipeline_common import (  # noqa: E402
    RULE_SECTIONS,
    canonical_hash,
)


# The OpenAI structured-output format name: letters, digits, "_" and "-",
# at most 64 characters.
_SCHEMA_NAME_STEM = re.compile(r"[A-Za-z0-9_-]{1,40}")
SCHEMA_NAME_DIGEST_CHARS = 16
# The stems of the schemas named by content (schema_name_by_content).
EXTRACTION_SCHEMA_STEM = "evidence_extraction"
ADJUDICATION_SCHEMA_STEM = "adjudication"


def schema_name_by_content(stem: str, schema: dict) -> str:
    """The structured-output name of ``schema``: ``stem`` and a digest of
    its canonical JSON.

    The name is part of the cached prompt prefix, so two calls whose schema
    and instructions are byte-identical but whose names differ share no
    cache entry. Families were named one by one (<family>_adjudication_v2)
    although most of them send the same schema: in the C arm of
    comparison_20261113, 15 adjudication calls whose earlier match differed
    only in that name got 0 cached tokens, against 27 of 28 full matches
    cached. Named by content, identical schemas share a name whatever the
    family, and different ones never do.
    """
    if not _SCHEMA_NAME_STEM.fullmatch(stem):
        raise ValueError(f"schema name stem {stem!r} is not 1-40 letters, digits, _ or -")
    return f"{stem}_{canonical_hash(schema)[:SCHEMA_NAME_DIGEST_CHARS]}"


def _string(enum: list[str] | None = None, description: str = "") -> dict:
    value: dict[str, Any] = {"type": "string"}
    if enum:
        value["enum"] = enum
    if description:
        value["description"] = description
    return value


def _array(items: dict) -> dict:
    return {"type": "array", "items": items}


def _object(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def external_research_schema() -> dict:
    source = _object({
        "url": _string(),
        "authority": _string(["official", "primary", "secondary"]),
        "claim": _string(),
        "relevance": _string(),
        "limitations": _string(),
    })
    return _object({
        "queries_performed": _array(_string()),
        "sources": _array(source),
        "externally_confirmed_facts": _array(_string()),
        "externally_unresolved_questions": _array(_string()),
    })


# Who the reported code serves (function_contract), read by the evidence
# extraction and carried to the behaviour review only when established
# (adjudication_rules.established_function_contract).
FUNCTION_CONTRACT_COUNTERPARTS = ("third_party_service", "user_operated_peer", "same_app_or_device", "unknown")
FUNCTION_CONTRACT_ACTIVATIONS = ("always", "user_enabled_option", "build_flag", "unknown")
FUNCTION_CONTRACT_DEPENDENCE = ("established", "not_established")


def evidence_extraction_schema(family: str) -> dict:
    claim = _object({
        "claim_id": _string(),
        "statement": _string(),
        "epistemic_status": _string([
            "confirmed", "inferred", "hypothesis", "unknown"
        ]),
        "evidence_ids": _array(_string()),
    })
    # The enum had no stated meaning, and the model supplied one of its own.
    # On markor's createKeyFromPassword it wrote the predicate "a forbidden
    # one-argument or three-argument PBEKeySpec constructor is used" with
    # status "confirmed" while explaining that the four-argument overload is
    # the one used, and wrote "the source-defined iteration value violates the
    # verified CrySL iteration floor" with status "violated" while explaining
    # that this violation predicate is false. Read literally the first asserts
    # the opposite of the evidence, and "violated" was used to mean "the
    # predicate does not hold" -- the reverse of how persist_blocked_issue_
    # candidate reads it when grounding an issue. Both the phrasing rule and
    # the meaning of each value are stated here so the contract is part of the
    # request rather than left to the model.
    predicate = _object({
        "predicate": _string(description=(
            "A single requirement stated positively, as a condition that is "
            "supposed to hold (\"iterationCount is at least the specification "
            "floor\"). Never phrase it as a violation or as the thing going "
            "wrong (\"the iteration count violates the floor\", \"a forbidden "
            "overload is used\"): the status field below carries whether it "
            "holds, and a violation-phrased predicate inverts that reading."
        )),
        "status": _string(
            ["confirmed", "violated", "unknown"],
            description=(
                "confirmed: the supplied evidence establishes that the "
                "predicate holds. violated: the supplied evidence establishes "
                "that the predicate does NOT hold, which is a real defect in "
                "the reviewed code. unknown: the supplied evidence is "
                "insufficient to decide either way. Never use violated to say "
                "that a violation-shaped predicate is false -- restate the "
                "predicate positively and mark it confirmed."
            ),
        ),
        # opencloud's getKnownServersStore established, as a confirmed fact,
        # that the char[] handed to KeyStore.load comes from the literal
        # "password", and then marked the specification's own notHardCoded
        # requirement "unknown" because "the supplied specification does not
        # define the detector's treatment of this derived char[] flow". The
        # requirement is about the reviewed code; how the detector models it
        # decides whether the detector would report it, which is a different
        # question and is answered by detector_violation. Giving the reason
        # a field with no detector-shaped value in it removes the move.
        "unknown_reason": _string(
            ["not_applicable", "source_incomplete", "scope_incomplete",
             "runtime_dependent"],
            description=(
                "Why the status is unknown. not_applicable for confirmed or "
                "violated. source_incomplete: the supplied source does not "
                "contain what would decide it. scope_incomplete: it is "
                "decided elsewhere in the repository, outside the supplied "
                "scope. runtime_dependent: no source can decide it because "
                "the value is only determined at run time. Whether the "
                "detector models, resolves or reports the requirement is "
                "never a reason: the predicate asks whether the requirement "
                "holds in the reviewed code, not whether the detector would "
                "notice. If the supplied source settles the requirement, the "
                "status is confirmed or violated even when the detector's "
                "own treatment of it is unclear."
            ),
        ),
        # Which part of the rule the requirement belongs to, so a violation
        # of the part the detector reported is recognised as the reported
        # finding however it is phrased, rather than raised again as a
        # separate requirement.
        "rule_section": _string(
            list(RULE_SECTIONS),
            description=(
                "The part of the rule this requirement belongs to: order for "
                "a required call sequence, constraint for an argument value, "
                "required_predicate for a value another rule must produce, "
                "forbidden_call for a call the rule forbids, other otherwise."
            ),
        ),
        "evidence_ids": _array(_string()),
        "explanation": _string(),
    })
    relation = _object({
        "kind": _string(),
        "status": _string([
            "confirmed", "not_found_in_examined_scope",
            "confirmed_absent", "unknown_due_to_incomplete_scope"
        ]),
        "evidence_ids": _array(_string()),
        "description": _string(),
    })
    family_properties: dict[str, dict]
    if family == "tls":
        family_properties = {
            "generic_context_observed": {"type": "boolean"},
            "effective_enabled_protocols": _string(["confirmed", "unknown"]),
            "negotiated_protocol": _string(["confirmed", "unknown"]),
            "detector_runtime_equivalent": {"type": "boolean"},
            "evidence_ids": _array(_string()),
        }
    elif family == "message_digest":
        family_properties = {
            "usage_kind": _string([
                "security", "cache", "identifier", "runtime_discriminator",
                "external_contract", "unknown"
            ]),
            "expected_value_source": _string([
                "internal", "external_backend", "multi_repository", "unknown"
            ]),
            "security_relevance": _string([
                "confirmed", "plausible", "not_established", "false"
            ]),
            "client_only_change_safe": _string(["yes", "no", "unknown"]),
            "evidence_ids": _array(_string()),
        }
    elif family == "cipher":
        family_properties = {
            "transformation": _string(),
            "initialization_status": _string(["confirmed", "violated", "unknown"]),
            "nonce_lifecycle": _string(["fresh", "reused", "unknown"]),
            "authentication_property": _string(["confirmed", "absent", "unknown"]),
            "evidence_ids": _array(_string()),
        }
    elif family == "dependency":
        family_properties = {
            "artifact": _string(),
            "current_version": _string(),
            "fixed_version_or_replacement": _string(),
            "official_migration_source": _string(),
            "compatibility_status": _string(["compatible", "incompatible", "unknown"]),
            "evidence_ids": _array(_string()),
        }
    elif family == "key_generation":
        family_properties = {
            "algorithm": _string(),
            "provider": _string(),
            "initialization_status": _string(["confirmed", "violated", "unknown"]),
            "key_size_or_parameters": _string(),
            "consumer_contract": _string(),
            "evidence_ids": _array(_string()),
        }
    else:
        family_properties = {
            "summary": _string(),
            "evidence_ids": _array(_string()),
        }
    function_contract = _object({
        "purpose": _string(),
        "counterpart": _string(list(FUNCTION_CONTRACT_COUNTERPARTS)),
        "activation": _string(list(FUNCTION_CONTRACT_ACTIVATIONS)),
        "depends_on_reported_behavior": _string(list(FUNCTION_CONTRACT_DEPENDENCE)),
        "dependence": _string(),
        "evidence_ids": _array(_string()),
        "quotes": _array(_object({"evidence_id": _string(), "quote": _string()})),
    })
    return _object({
        "claims": _array(claim),
        "observed_events": _array(_string()),
        "predicate_assessment": _array(predicate),
        "confirmed_dataflows": _array(_string()),
        "relations": _array(relation),
        "unresolved_questions": _array(_string()),
        "needs_more_evidence": {"type": "boolean"},
        "requested_evidence": _array(_object({
            "kind": _string(), "symbol": _string(), "reason": _string(),
        })),
        "family_assessment": _object(family_properties),
        "function_contract": function_contract,
    })


DECISIONS = [
    "FIX_SUPPORTED", "MULTI_FILE_FIX_SUPPORTED",
    "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW", "NO_SAFE_FIX",
    "SAFE_INTERNAL_DIGEST_MIGRATION", "NON_SECURITY_HASH_USAGE",
    "CONFIRMED_SPEC_VIOLATION_NON_SECURITY_USAGE",
    "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY",
    "MULTI_REPOSITORY_MIGRATION_SUPPORTED",
    "EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX",
    "DEPENDENCY_FIX_SUPPORTED", "DEPENDENCY_REPLACEMENT_SUPPORTED",
    "DEPENDENCY_FIX_REQUIRES_POLICY", "UPSTREAM_FIX_REQUIRES_POLICY",
    "MULTI_FILE_FIX_REQUIRES_POLICY",
]


# Decisions whose downstream gate accepts only a MessageDigestSpec finding.
# Offered to any other family, they are a trap: Gallery's AsyncTask
# migration was adjudicated BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY with a
# complete, invariant-preserving plan, and the digest gate turned it into
# NO_SAFE_FIX because the specification was not MessageDigestSpec.
DIGEST_ONLY_DECISIONS = frozenset({
    "SAFE_INTERNAL_DIGEST_MIGRATION", "NON_SECURITY_HASH_USAGE",
    "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY",
    "MULTI_REPOSITORY_MIGRATION_SUPPORTED",
    "EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX",
})


# What a blocker is about. `kind` stayed free text and the stored corpus holds
# dozens of spellings of the same few concerns, so no code could tell a
# missing caller body from a maintainer's authorisation.
BLOCKER_CATEGORIES = [
    "cause_unconfirmed", "code_evidence_missing", "target_unresolved",
    "maintainer_decision", "runtime_behavior", "compatibility_contract",
    "rename_authorization",
    # Whether the reporting analyzer accepts the patched code. Filed as
    # "other" until 2026-10-29 (Fread's "the verified primary rule's exact
    # selector whitelist still rejects TLS"), so no code could settle it from
    # the reporter's own message (adjudication_rules.discharge_analyzer_acceptance).
    "analyzer_reconfirmation",
    "other",
]


# Never blocks: adjudication_rules.discharge_rename_authorization_blockers
# moves it to publication_considerations, whatever rename_safety says.
RENAME_AUTHORIZATION_CATEGORY = "rename_authorization"

# Questions that are notes for the reviewer, never a reason to withhold a
# patch: whether the analyzer will accept the change once it reruns (Fread's
# "TLS" selector, which the reporting detector itself lists as accepted),
# and whether a rename the repository check allows may go ahead (Orgzly's
# sha1). They had no category of their own and fell into `other`, which
# blocks (utils.disposition.publication_after_validation).
NOTE_BLOCKER_CATEGORIES = frozenset({"analyzer_reconfirmation", "rename_authorization"})


# Categories a human settles by reading a patch, as opposed to evidence the
# patch would have to be written from.
HUMAN_DECIDED_BLOCKER_CATEGORIES = frozenset({
    "maintainer_decision", "runtime_behavior", "compatibility_contract",
}) | NOTE_BLOCKER_CATEGORIES


def adjudication_schema(family: str) -> dict:
    decisions = list(DECISIONS)
    if family != "message_digest":
        decisions = [item for item in decisions if item not in DIGEST_ONLY_DECISIONS]
    if family == "tls":
        decisions = [
            "FIX_SUPPORTED", "MULTI_FILE_FIX_SUPPORTED",
            "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW",
            "MULTI_FILE_FIX_REQUIRES_POLICY", "NO_SAFE_FIX",
        ]
    elif family == "dependency":
        decisions = [
            "DEPENDENCY_FIX_SUPPORTED", "DEPENDENCY_REPLACEMENT_SUPPORTED",
            "DEPENDENCY_FIX_REQUIRES_POLICY", "UPSTREAM_FIX_REQUIRES_POLICY",
            "NO_SAFE_FIX",
        ]
    # discharged_by_pipeline_validation lets a publication blocker say whether
    # this pipeline's own downstream validation settles it. Without it,
    # publication_status was a dead enum value: "eligible_after_validation" was
    # rejected whenever any publication blocker remained, but the blockers are
    # the pending validations, so the state it names was unreachable for every
    # finding that needed validating -- which is all of them. Across 54 stored
    # adjudications not one reached it, and the easydiary Zip Slip run shows
    # the model answering eligible_after_validation, being rejected, and
    # relabelling to blocked with its two blockers unchanged. The kind field
    # cannot carry this: it is free text and the corpus holds ~50 variants.
    blocker = _object({
        "kind": _string(),
        "category": _string(
            BLOCKER_CATEGORIES,
            description=(
                "cause_unconfirmed: the violation or its cause is not "
                "established in the source; code_evidence_missing: source "
                "the change must be written from is not in the packet; "
                "target_unresolved: which file, artifact or version to "
                "change is undetermined; maintainer_decision: a choice only "
                "the project can make; runtime_behavior: what only a running "
                "device, platform or endpoint shows; compatibility_contract: "
                "whether stored data, a wire format or an external consumer "
                "keeps working; rename_authorization: whether a function may "
                "be renamed or removed (rename_safety answers it; it is a "
                "note, never a blocker); analyzer_reconfirmation: whether the "
                "analyzer that reported the finding accepts the patched "
                "code when it runs again; other: anything else."
            ),
        ),
        "description": _string(),
        "supporting_claim_ids": _array(_string()),
        "discharged_by_pipeline_validation": {"type": "boolean"},
    })
    return _object({
        "cause_status": _string(["confirmed", "inferred", "unknown"]),
        "confirmed_cause": _string(),
        "cause_rule_section": _string(
            list(RULE_SECTIONS),
            description=(
                "The part of the rule confirmed_cause is about: order for a "
                "call sequence, constraint for an argument value, "
                "required_predicate for a value another rule must produce, "
                "forbidden_call for a call the rule forbids, other otherwise."
            ),
        ),
        "detector_violation": _string([
            "confirmed", "source_established", "not_confirmed", "unknown",
        ]),
        "runtime_security_impact": _string([
            "confirmed", "not_established", "non_security", "unknown"
        ]),
        "compatibility_status": _string(["preserved", "regression_risk", "unknown"]),
        "scope_status": _string(["complete", "incomplete", "not_applicable"]),
        "decision": _string(decisions),
        "decision_claim_ids": _array(_string()),
        "blocking_for_generation": _array(blocker),
        "blocking_for_publication": _array(blocker),
        "generation_status": _string([
            "blocked", "supported", "supported_for_review"
        ]),
        "publication_status": _string(["blocked", "eligible_after_validation"]),
        "required_edit_categories": _array(_string()),
        "required_tests": _array(_string()),
        "forward_compatibility_strategy": _string(),
        "authorized_behavior_change": _object({
            "authorized": {"type": "boolean"},
            "description": _string(),
            "policy_reference": _string(),
        }),
        "review_assumptions": _array(_string()),
        # The question the behaviour review answers for a finished patch,
        # asked here for a finding that ends without one. Grocy's
        # TrustManagerFactory.init(null), the documented request for the
        # platform trust store, became a maintainer issue when adjudication
        # stopped it, and a violation without security impact when the
        # behaviour review did (finding_processing,
        # documented_use_without_security_impact).
        "current_code_is_correct_use": {
            "type": "boolean",
            "description": (
                "True only when decision is NO_SAFE_FIX, the reported code "
                "already uses the API as its documentation prescribes, and "
                "every change that would satisfy the rule alters what the "
                "code trusts, accepts or protects. A change that passes a "
                "null or omitted platform-default argument to another "
                "governed API only moves the finding and does not count as "
                "satisfying the rule. False otherwise, including when the "
                "evidence cannot settle it."
            ),
        },
        "correct_use_reason": _string(description=(
            "Why the reported code already uses the API as documented, "
            "citing that documentation, or an empty string."
        )),
    })


def patch_plan_schema() -> dict:
    return _object({
        "decision": _string(DECISIONS),
        "required_edits": _array(_object({
            "source_file_path": _string(),
            "symbol": _string(),
            "reason": _string(),
            "create_new_file": {"type": "boolean"},
            "edit_category": _string(),
            "supporting_claim_ids": _array(_string()),
        })),
        "required_tests": _array(_string()),
        "behavioral_invariants": _array(_string()),
        "allowed_apis": _array(_string()),
        "forbidden_changes": _array(_string()),
        "generation_blockers": _array(_string()),
        "publication_blockers": _array(_string()),
    })
