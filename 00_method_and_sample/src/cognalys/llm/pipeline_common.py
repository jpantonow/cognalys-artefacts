"""Versions stamped on every v2 artifact, the canonical hash, and the small
helpers every v2 module reads a finding or a specification with.

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


PROMPT_VERSION = "llm-pipeline-v2.1"


SCHEMA_VERSION = "2.1"


POLICY_VERSION = "2.0"


# 2.2 admits FLOW security-sink reachability to the evidence catalog. The
# packet is hashed into the LLM cache key, so this invalidates the evidence
# stage for every finding -- intended: a packet carrying a new conclusion is
# not the packet the cached answer was computed from.
#
# 2.3 adds CONST (the declaration fixing a constrained value), DOC (the
# project's own format and design documents), CONV (the code converting a
# produced value between layers), ROUND (where a written setting is read
# back) and MUT (every writer of shared state the method reaches), and
# settles callers whose receiver is an injected interface so a common method
# name no longer leaves the scope incomplete.
#
# Bumping it is not bookkeeping. photok's run_20260911_171528 built a packet
# whose scope was complete in every dimension and then answered from five
# cache hits and no misses: the adjudication replayed a decision computed
# from a packet that no longer exists, and none of the evidence above was
# ever read.
#
# 2.6 adds BYTE (the reported method disassembled from the owning library's
# cached binary, for a finding whose class is not in the checkout) and the
# cached-metadata carriers to DEP.
#
# 2.7 prefers the analysed APK's own bytecode for BYTE, adds APK (the library
# versions the APK records), a field-level SEQ across the reviewed file, the
# origin of each governed call's String argument, and
# reported_violation_section on the finding.
#
# 2.8 adds DEPF (what a dependency or deprecation message names, checked
# against the checkout) and supplies the build files declaring it as exact
# source, so a dependency plan can edit them.
#
# 2.9 adds FLOW derived_value_consumers (where the reported method's value is
# used, from its call sites) and RENAME (whether the method may be renamed).
#
# 2.10 adds REPO repository_replacement_idioms, CONTRACT
# deprecated_usage_contract with its files as exact source, and DEPF
# usage_inventory, migration_scale and effective_resolution.
#
# 2.12 adds detector_context to DET-001: the findings CSV's detector_*
# columns (report code, label and its documented meaning, value seen, source
# location, observations across runs), and finding.app_caller_attribution
# when a library frame implementing the governed API is analysed at the
# application call that passed it the reported value.
#
# 2.13 extends FLOW derived_value_consumers: a dependency-injection provider's
# value is followed from the injection sites of its type, and any value into
# the parameters of the methods it is passed to.
#
# 2.15 adds a SPEC entry for each joint finding reported under another rule
# (generate_error_fixes.join_findings_of_one_sequence keeps that finding's own
# specification context), and binds a receiverless call named like a type as
# that type's constructor in derived_facts.call_binding (BlueGate run_v6 and
# run_v7, 2026-10-31: the joined KeyPairGeneratorSpec rule "was not supplied"
# and KeyPair(...) stayed unresolved under `import java.security.KeyPair`).
#
# 2.16 adds FLOW persisted_value_readers when every call of the reported
# method is the value of a key-value write under keys no code in the
# repository reads (Your Local Weather run_v3, 2026-11-02: the adjudication
# ended encryptKey as NO_SAFE_FIX for want of a reader of its stored hash
# that the PR step's search had already shown does not exist).
#
# 2.17 adds INTENT deliberate_insecurity: suppressions of a security check
# on the reported code, comments stating why it is insecure, and whether an
# option or build flag gates it (weeWX Weather, 2026-11-04: a trust-all
# manager under a class-level @SuppressLint, for self-signed home servers,
# became a PR that removed it).
#
# 2.18 reads detector labels from data/detector_labels.json: DET-001's
# detector_context carries the catalogue's current meaning of a known label
# and site kind instead of the text stored with the finding, and a record
# whose report kind the catalogue flags outranks_rule_text_alternative goes to
# extraction and adjudication as reported_by_detector (CometOTP
# EncryptionHelper.loadOrGenerateWrappedKey, agent_baseline_20261108: a
# stored meaning called a SecureRandom key "the normal way to make a key"
# while the checker that ran recorded that origin and does not admit it).
# 2.19 adds function_contract to the evidence extraction: who the reported
# code serves and whether they depend on the reported behaviour, with quotes
# checked against the repository entries they name (Input Leaf
# ServerScanner.discoverySslContext, agent_baseline_20261108). The packet is
# unchanged; an established contract goes to the behaviour review only.
CONTEXT_BUILDER_VERSION = "2.19"


SEMANTIC_RESOLVER_VERSION = "tree-sitter-semantic-v2"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def prompt_json(value: Any) -> str:
    """JSON for a model prompt: compact, key order kept.

    Indentation carries nothing a model reads and cost about 11% of every
    evidence packet (exp_final_20261019). Files on disk stay indented.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def without_duplicate_specification(packet: dict) -> dict:
    """The packet as a prompt needs it: the specification sent once.

    The packet carries the specification as specification_context and again
    as a citable evidence_catalog entry with the same data -- identical in all
    14 packets of exp_final_20261019, about 12,000 characters each. The entry
    stays, because claims cite it; the context points at it. The packet
    written to disk is not changed.
    """
    specification = packet.get("specification_context")
    if not specification:
        return packet
    for item in packet.get("evidence_catalog") or []:
        if isinstance(item, dict) and item.get("data") == specification:
            trimmed = dict(packet)
            trimmed["specification_context"] = (
                f"identical to evidence_catalog {item.get('evidence_id', '')} data"
            )
            return trimmed
    return packet


def without_entry_hashes(items: Any) -> Any:
    """Catalog entries as a prompt needs them: without their ``sha256``.

    The hash of each entry's data is the audit trail of what was sent; no
    instruction or schema of any stage names it, and in the recorded
    extraction prompts the hash fields were 1.6% of the characters. The
    entries on disk and in memory keep it.
    """
    if not isinstance(items, list):
        return items
    return [
        {key: value for key, value in item.items() if key != "sha256"}
        if isinstance(item, dict) else item
        for item in items
    ]


def packet_prompt_view(packet: dict, policy_section: str = "",
                       policy: Any = None) -> dict:
    """The evidence packet as a prompt shows it; always a new dict.

    The specification once (without_duplicate_specification), no
    ``input_hashes`` and no entry ``sha256`` (without_entry_hashes), and,
    when the prompt already carries the selected policy as its own
    ``policy_section`` with the same value -- the extraction's
    stable_policy was equal to the packet's selected_policy in all 2,013
    recorded extraction prompts -- a pointer to that section instead of a
    second copy. The packet written to disk keeps every field.
    """
    view = dict(without_duplicate_specification(packet))
    view.pop("input_hashes", None)
    for key in ("evidence_catalog", "external_evidence_catalog"):
        if key in view:
            view[key] = without_entry_hashes(view[key])
    diagnostic = view.get("diagnostic_evidence")
    if isinstance(diagnostic, dict) and "sha256" in diagnostic:
        view["diagnostic_evidence"] = {
            key: value for key, value in diagnostic.items() if key != "sha256"}
    if policy_section and "selected_policy" in view and view["selected_policy"] == policy:
        view["selected_policy"] = f"identical to {policy_section}"
    return view


EXTERNAL_SOURCES_POINTER = (
    "listed once, in this order, as finding_evidence_packet."
    "external_evidence_catalog[].data, each with its citable WEB evidence_id"
)


def research_prompt_view(research: dict, external_catalog: Any) -> dict:
    """The research record as a prompt beside the packet needs it.

    Every source becomes an external_evidence_catalog entry whose data is
    that very source (its WEB id added), so a prompt that carries both had
    each source twice: 12,321 of 12,321 sources in the 1,944 recorded
    extraction prompts with research were identical to their entry's data,
    2.3% of the characters. When the two lists match, ``sources`` points at
    the catalog; otherwise (a source the catalog skipped) the record is
    returned as it is. The research written to disk is not changed.
    """
    sources = research.get("sources") if isinstance(research, dict) else None
    if not sources or not isinstance(external_catalog, list):
        return research
    data = [item.get("data") if isinstance(item, dict) else None
            for item in external_catalog]
    if canonical_json(sources) != canonical_json(data):
        return research
    view = dict(research)
    view["sources"] = EXTERNAL_SOURCES_POINTER
    return view


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def specification_shapes_are_grounded(specification_context: dict | None) -> bool:
    """Whether this specification's documented shapes may be quoted at all.

    A `known_false_positive_shapes` entry is a statement about when a finding
    may be dismissed, and it carries weight only if the source it came from
    can be produced. Ten of this project's specifications cite a URL that
    answers 404 -- the concerns CrySL does not model, each pointed at a
    plausible-looking path in the CrySL rule repository -- and between them
    they hold 66 such statements.

    That used to be prevented only by `source_verified` happening to gate the
    callers, a coupling nothing wrote down and any later edit could undo.
    scripts/audit_specifications.py records `shapes_grounded` next to the
    shapes themselves, and this is the single place that reads it, so a shape
    whose authority is missing cannot become the reason a real finding was
    dismissed.
    """
    context = specification_context or {}
    if context.get("shapes_grounded") is False:
        return False
    return context.get("source_verified") is True


def false_positive_shapes_reviewed(specification_context: dict | None) -> bool:
    """Whether this specification's shapes went through the shape review.

    A known_false_positive_shapes entry dismisses a finding, so it must
    describe code that does not break the rule as written.
    place_false_positive_shapes reviews each one and moves those that do
    (harmless-context violations) to non_security_violation_shapes, and the
    generator stamps generation_evidence.false_positive_shape_review when it
    has. None of the 74 stored specifications carried the stamp on
    2026-10-29: Aegis's SSLConnector.buildSocket was dismissed on
    SSLContextSpec's init(null) shape, which admits it "cannot satisfy" the
    rule's provenance predicates, while AntennaPod's same construct ended
    as a violation without security impact.
    """
    evidence = (specification_context or {}).get("generation_evidence")
    stamp = evidence.get("false_positive_shape_review") if isinstance(evidence, dict) else None
    if not isinstance(stamp, dict):
        return False
    try:
        return int(stamp.get("version") or 0) >= 1
    except (TypeError, ValueError):
        return False


def _normalised(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def reported_violation(error: dict) -> str:
    """The predicate the detector reported, recovered from unique_error_id.

    The detector writes `class:::method:::spec:::VIOLATION:::message`, and the
    pipeline has always carried that string through as unique_error_id without
    reading it. That mattered because 95 of the 133 sampled candidates (71%)
    arrive with `message: unknown` -- every one of them an
    InvalidSequenceOfMethodCalls -- so the analysis was handed an empty
    message while the violation type sat unparsed in a field it already had.

    Nothing here is inferred: the value is the detector's own output.
    """
    parts = str(error.get("unique_error_id", "")).split(":::")
    return parts[3].strip() if len(parts) > 4 else ""


# The part of a rule a reported violation type is about. A typestate error is
# answered by the rule's order, a value error by its constraints, and a cause
# taken from the other part explains some other requirement.
# org.fedorahosted.freeotp's TokenPersistence.<init> was reported as
# InvalidSequenceOfMethodCalls -- its KeyStore field sees setEntry and then
# getKey with no store between them -- and the adjudication confirmed as the
# cause the "AndroidKeyStore" type whitelist, which is a constraint and cannot
# produce that report.
#
# Only names the detectors actually emit are listed: data/raw/errors.csv holds
# InvalidSequenceOfMethodCalls 86,158, UnsafeAlgorithm 19,621, UnsafeProtocol
# 14,530, InvalidKeyStoreType 2,162 and InvalidKeySize 7, and the other names
# are CryptoAnalysis's for the same error kinds. A type not listed maps to no
# section and nothing is enforced for it, so a specification from another
# detector family keeps its own vocabulary.
RULE_SECTIONS = ("order", "constraint", "required_predicate", "forbidden_call", "other")


VIOLATION_RULE_SECTIONS = {
    "order": (
        "InvalidSequenceOfMethodCalls", "TypestateError",
        "IncompleteOperationError",
    ),
    "constraint": (
        "UnsafeAlgorithm", "UnsafeProtocol", "UnsafeTransformation",
        "InvalidKeyStoreType", "InvalidKeySize", "ConstraintError",
        "HardCodedError", "NeverTypeOfError",
    ),
    "required_predicate": ("RequiredPredicateError",),
    "forbidden_call": ("ForbiddenMethodError",),
}


def violation_rule_section(violation: str) -> str:
    name = str(violation or "").strip()
    for section, names in VIOLATION_RULE_SECTIONS.items():
        if name in names:
            return section
    return ""
