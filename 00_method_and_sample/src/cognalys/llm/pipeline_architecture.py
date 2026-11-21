"""Deterministic contracts and evidence selection for LLM pipeline v2.

The code lives in modules split by responsibility; every name they define
is re-exported here so `from pipeline_architecture import X` and
`from llm.pipeline_architecture import X` keep working:

  pipeline_common        Versions stamped on every v2 artifact, the canonical hash, and the small
  false_positive_rules   Deterministic routes to closing a finding as a false positive.
  source_facts           Facts read from the reviewed source, its bytecode and its build: call
  research_questions     What external research is asked for a finding, and the specification
  evidence_packet        The evidence packet each v2 model call is given.
  llm_contracts          JSON schemas and decision vocabularies of the v2 model calls.
  adjudication_rules     Deterministic checks and routings applied to what the model answers.
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

from cognalys.llm.pipeline_common import (  # noqa: E402,F401
    CONTEXT_BUILDER_VERSION,
    POLICY_VERSION,
    PROMPT_VERSION,
    RULE_SECTIONS,
    SCHEMA_VERSION,
    SEMANTIC_RESOLVER_VERSION,
    VIOLATION_RULE_SECTIONS,
    _normalised,
    canonical_hash,
    canonical_json,
    reported_violation,
    specification_shapes_are_grounded,
    violation_rule_section,
)
from cognalys.llm.false_positive_rules import (  # noqa: E402,F401
    KNOWN_FALSE_POSITIVE_MESSAGES,
    UNMODELLED_CALL_SHAPES,
    _DETECTOR_DEFERRAL,
    _PARAMETERLESS_API_PATTERN,
    _UNCONSTRAINED_ARITY_MATCHER,
    _calls_by_name,
    _specification_events,
    _states_cause_status,
    blocked_finding_review_status,
    confirmed_false_positive_reason,
    detector_arity_overmatch_reason,
    evidence_correlates_with_source,
    false_positive_rationale,
    governed_call_names,
    parameterless_governed_methods,
    predicate_deferred_to_detector,
    unconstrained_arity_matcher,
    unmodelled_call_false_positive_reason,
    unmodelled_calls_in_source,
)
from cognalys.llm.source_facts import (  # noqa: E402,F401
    ANDROID_NS,
    CIPHER_BLOCK_SIZE_BYTES,
    CONSTANT_DEFINITION,
    INPUT_SIZE_GUARD,
    MANIFEST_COMPONENT_TAGS,
    MAX_BUILD_SOURCES,
    MAX_BUILD_SOURCE_CHARACTERS,
    MAX_CONTRACT_SOURCES,
    MAX_CONTRACT_SOURCE_CHARACTERS,
    VENDORED_HEADER,
    VENDORED_PATH_SEGMENTS,
    VENDORED_UPSTREAM_URL,
    _DEX_FIELD,
    _DEX_INVOKE,
    _DEX_INVOKE_REGISTERS,
    _DEX_NOT_A_WRITE,
    _DEX_STRING,
    _DEX_WRITE,
    _EVENT_CALL_NAME,
    _EVIDENCE_ID_PREFIX,
    _FALSE_POSITIVE_WORDS,
    _FIELD_DECLARATION,
    _GENERIC_COORDINATE_TOKENS,
    _GET_INSTANCE_CALL_PATTERN,
    _JAVAP_FIELD,
    _JAVAP_INVOKE,
    _JAVAP_LOAD,
    _JAVAP_STRING,
    _MAX_ACCESSORS,
    _METHOD_HEADER,
    _METHOD_SIGNATURE,
    _MUTATING_CALL,
    _MUTATOR_NAMES,
    _NON_FIELD_RETURNS,
    _NOT_METHOD_NAMES,
    _PLATFORM_NAMESPACES,
    _PRODUCER_LOOKBACK,
    _QUALIFIED_CLASS,
    _QUOTED_LITERAL,
    _RETURN_EXPRESSION,
    _STACK_NEUTRAL,
    _STRING_LITERAL_ONLY_PATTERN,
    _ZERO_ARG_CALL,
    _argument_origin,
    _balanced_call_argument,
    _call_sites,
    _dex_argument_producer,
    _enclosing_method,
    _enclosing_method_names,
    _first_top_level_argument,
    _javap_argument_producer,
    _manifest_name_matches,
    _method_text,
    _receiver_matches_governed_type,
    analysis_dismissal_texts,
    bytecode_argument_origins,
    classify_spec_family,
    collateral_constraint_violations,
    dependency_declaration_sources,
    enforced_input_size_evidence,
    false_positive_dismissal_literals,
    field_call_sequences,
    fields_reached_through_accessors,
    governed_api_types,
    governed_factory_calls,
    manifest_component_evidence,
    message_names_dependency,
    migration_contract_sources,
    non_literal_getinstance_arguments,
    observed_call_sequence,
    security_sink_evidence,
    settled_value_texts,
    shared_state_mutation_evidence,
    target_source_completeness,
    vendored_source_assessment,
    with_scope_provenance,
)
from cognalys.llm.research_questions import (  # noqa: E402,F401
    PolicyRegistry,
    _RESEARCHABLE_DEPENDENCY_STATUSES,
    case_scoped_specification_context,
    detector_matching_research_question,
    lifecycle_research_questions,
    specification_is_externally_anchored,
    verified_detector_semantics,
)
from cognalys.llm.evidence_packet import (  # noqa: E402,F401
    FindingEvidencePacketBuilder,
    _EvidenceItem,
    _promote_or_add_exact_sources,
)
from cognalys.llm.llm_contracts import (  # noqa: E402,F401
    BLOCKER_CATEGORIES,
    DECISIONS,
    DIGEST_ONLY_DECISIONS,
    HUMAN_DECIDED_BLOCKER_CATEGORIES,
    _array,
    _object,
    _string,
    adjudication_schema,
    evidence_extraction_schema,
    external_research_schema,
    patch_plan_schema,
)
from cognalys.llm.adjudication_rules import (  # noqa: E402,F401
    AUTHORITY_SETTLED_BLOCKER_CATEGORIES,
    NON_SECURITY_CLASSIFICATIONS,
    REVIEW_SETTLED_ADJUDICATION_ISSUES,
    _DEMOTABLE_FOR_SCOPE,
    _HUMAN_DECIDED_PROMOTABLE_DECISIONS,
    cause_rule_section_issues,
    demote_incomplete_scope_to_review,
    detector_message_is_absent,
    discharge_analyzer_acceptance,
    discharge_blockers_answered_by_derived_facts,
    human_decided_publication_considerations,
    independent_finding_authority,
    planned_replacement_values,
    promote_authority_confirmed_cause_to_review,
    promote_evidence_gap_to_review,
    promote_human_decided_blockers_to_review,
    publication_blocker_record,
    route_non_security_repair_to_review,
    route_unsettled_compatibility_to_review,
    sanitize_evidence_extraction,
    dismissal_justification_issues,
    section_telemetry,
    validate_adjudication,
    validate_adjudication_of,
    validate_evidence_extraction,
)
