#!/usr/bin/env python3
"""
Error Fix Generator

Processes error method JSONs with an LLM to generate precise Java/Kotlin fixes.
Uses Ollama local deployment to avoid API costs while ensuring code quality.

Features:
- Ollama provider with retry logic and streaming support
- Code quality validation to detect hallucinations
- Comprehensive logging and progress tracking
- Resume capability for interrupted runs
- Master tracking JSON with quality metrics
"""

from __future__ import annotations

import collections
import json
import hashlib
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import argparse

_LLM_MODULE_DIR = Path(__file__).resolve().parent
_SRC_DIR = _LLM_MODULE_DIR.parent

from cognalys.llm.llm_providers import (
    CallBudgetExhausted,
    ProviderAuthenticationError,
    ProviderContentRefusal,
    budget_limits_from_environment,
    finding_budget,
    OllamaProvider,
    LLMResponseCache,
    OpenAIProvider,
    REMOTE_PROVIDERS,
    remote_provider,
    setup_logging,
)
from cognalys.llm.run_statistics import (
    record_failure,
    record_input_invalid,
    record_llm_budget,
    record_solution,
    step_failures,
)
from cognalys.llm.evidence_requests import (
    catalog_supplement,
    resolve_evidence_requests,
)
from cognalys.utils.disposition import (
    FindingDisposition,
    REPAIR_DECISIONS,
    minimum_coordinated_edits,
)
from cognalys.utils.step_outcome import step_exit_code
from cognalys.llm.prompt_builders import (
    declared_api_surface,
)
from cognalys.llm.migration_policy import (
    generic_migration_policy,
    resolve_case_policy,
    nondigest_fallback_evidence,
    ground_usage_in_value_consumers,
    promote_grounded_api_migration_review,
    promote_bounded_digest_discriminator_review,
    promote_policy_authorized_tls_review,
    promote_validation_repair_sources,
)
from cognalys.llm.code_quality import (
    CodeQualityValidator,
    coordinated_quality_metrics,
)
from cognalys.llm.fix_artifacts import (
    _write_json_atomic,
    compact_evidence_context,
    coordinated_fix_explanation,
    format_elapsed_time,
    persist_context_snapshot,
    summarize_patch_changes,
)
from cognalys.llm.adjudication import (
    _analysis_record,
    _issue_schema,
    _trace_context,
)
from cognalys.llm.response_parsing import ResponseParser
from cognalys.llm.semantic_validation import (
    _validate_patch_plan,
    plan_edits_beyond_method,
    repair_patch_plan,
    repository_test_source_roots,
    validate_multi_file_scope,
    MULTI_FILE_DECISIONS,
    REVIEW_ONLY_DECISIONS,
    _LEGACY_TLS_EXCLUSION_PATTERN,
    _SEMANTIC_VALIDATION_PRODUCTION_CODES,
    _SEMANTIC_VALIDATION_TEST_CODES,
    _TEST_DIR_NAMES,
    _TEST_FILE_SUFFIXES,
    minimum_planned_files,
    normalize_coordinated_edit,
    repository_has_test_infrastructure,
    semantic_validation_retry_targets,
    unrelated_test_placements,
    validate_coordinated_edit_shape,
    validate_coordinated_semantics,
    name_behavior_contradictions,
)
from cognalys.extraction.test_classpath import (
    module_test_classpath,
    test_classpath_summary,
)
from cognalys.llm.llm_schemas import (
    _string,
    _strict_object,
    coordinated_file_draft_schema,
    coordinated_fix_schema,
    maintainer_summary_schema,
)
from cognalys.llm.pipeline_architecture import (
    CONTEXT_BUILDER_VERSION,
    POLICY_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SEMANTIC_RESOLVER_VERSION,
    FindingEvidencePacketBuilder,
    PolicyRegistry,
    adjudication_schema,
    blocked_finding_review_status,
    canonical_hash,
    case_scoped_specification_context,
    detector_matching_research_question,
    verified_detector_semantics,
    advisory_identifiers,
    lifecycle_research_questions,
    specification_is_externally_anchored,
    classify_spec_family,
    collateral_constraint_violations,
    evidence_correlates_with_source,
    evidence_extraction_schema,
    external_research_schema,
    reported_violation,
    demote_incomplete_scope_to_review,
    discharge_blockers_answered_by_derived_facts,
    human_decided_publication_considerations,
    promote_human_decided_blockers_to_review,
    promote_authority_confirmed_cause_to_review,
    promote_evidence_gap_to_review,
    independent_finding_authority,
    route_non_security_repair_to_review,
    route_unsettled_compatibility_to_review,
    unmodelled_calls_in_source,
    governed_api_types,
    message_names_dependency,
    non_literal_getinstance_arguments,
    patch_plan_schema,
    sanitize_evidence_extraction,
    section_telemetry,
    validate_adjudication_of,
    validate_evidence_extraction,
)
from cognalys.llm.fix_generation_common import (  # noqa: E402,F401
    FindingOutcome,
    ISSUE_PRESENTATION_FIELDS,
    MAX_ADJUDICATION_RETRIES,
    MAX_SEMANTIC_VALIDATION_RETRIES,
    REPOSITORY_EVIDENCE_PREFIXES,
    claim_grounded_cause,
    collateral_issue_candidate,
    learning_context,
    post_validation_issue_candidate,
)
from cognalys.llm.finding_discovery import FindingDiscovery  # noqa: E402
from cognalys.llm.finding_context import FindingContextCollection  # noqa: E402
from cognalys.llm.finding_adjudication import FindingAdjudication  # noqa: E402
from cognalys.llm.coordinated_generation import (  # noqa: E402
    CONCLUDED_CO_REPORTED_STATUS,
    CoordinatedGeneration,
)
from cognalys.llm.issue_candidates import (  # noqa: E402
    NOT_APPLICABLE_TO_BASE_SUFFIX, IssueCandidates,
)
from cognalys.llm.finding_processing import FindingProcessing  # noqa: E402
from cognalys.workspace import library_data


def annotate_co_reported_findings(errors: list) -> None:
    """Tell each finding about the others reported in the same class.

    Each finding is analysed alone, so two in one file got two patches
    that rewrote the same method in incompatible ways (Grocy, 2026-10-23:
    KeyStoreSpec in X509KeyStoreFile.load and .save, each fix leaving the
    other violation in place). The plan sees the others and can propose one
    change that resolves them together; identical patches are then one
    pull request (prs.pr_grouping).
    """
    def key(error: dict) -> tuple:
        path = error.get("_file_path")
        apk = path.parent.name if hasattr(path, "parent") else ""
        return apk, str(error.get("csv_class", "")).split("$", 1)[0]

    groups: dict = {}
    for error in errors:
        groups.setdefault(key(error), []).append(error)
    for group in groups.values():
        for error in group:
            others = [
                {"specification": other.get("spec", ""),
                 "class": other.get("csv_class", ""),
                 "method": other.get("method_name", ""),
                 "error_message": other.get("error_message", "")}
                for other in group
                if other is not error and other.get("unique_error_id") != error.get("unique_error_id")
            ]
            if others:
                error["co_reported_findings"] = others


def concluded_outcome(output_dir, error: dict, result) -> dict:
    """How a finding ended with its code left as it is, or {} when it did not.

    No effect and correct use both write the no-security-impact marker
    (IssueCandidates.record_no_security_impact); a false positive and a
    confirmed violation without a safe fix are NO_SAFE_FIX, told apart by the
    evidence review. A supported fix that the gates stopped (a generating
    decision finding_processing turned into NO_SAFE_FIX, as the digest gate
    does), a violation the review left undecided, a crash or a refusal did
    not conclude anything about the code. A recorded suppression did: the
    maintainer chose to keep that code.
    """
    if result.success or result.outcome != "abstained":
        return {}
    path = error.get("_file_path")
    apk = path.parent.name if hasattr(path, "parent") else ""
    safe_method = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(error.get("method_name", "unknown")))
    stem = f"{error.get('error_index', 0)}_{safe_method}"
    marker = Path(output_dir) / "no_security_impact" / apk / f"{stem}.json"
    if marker.is_file():
        try:
            reason = str(json.loads(marker.read_text(encoding="utf-8")).get("reason", ""))
        except (OSError, ValueError):
            reason = ""
        return {"outcome": "no security impact", "reason": reason}
    decision = str(result.decision or "").upper()
    if decision in {"CONFIRMED_SPEC_VIOLATION_NON_SECURITY_USAGE", "NON_SECURITY_HASH_USAGE"}:
        return {"outcome": "no security impact", "reason": ""}
    if decision != "NO_SAFE_FIX":
        return {}
    analysis: dict = {}
    try:
        analysis = json.loads((Path(output_dir) / "evidence_reviews" / apk
                               / f"{stem}_evidence.json").read_text(encoding="utf-8")).get("analysis") or {}
    except (OSError, ValueError, AttributeError):
        analysis = {}
    if not isinstance(analysis, dict):
        return {}
    adjudication = (analysis.get("v2") or {}).get("adjudication") or {}
    if (isinstance(adjudication, dict)
            and str(adjudication.get("decision", "NO_SAFE_FIX")).upper() != "NO_SAFE_FIX"):
        return {}
    status = analysis.get("evidence_status") or {}
    violation = str(analysis.get("detector_violation", (
        status.get("detector_violation", "") if isinstance(status, dict) else ""))).strip()
    reason = str(analysis.get("confirmed_cause", "")).strip()
    if violation in {"confirmed", "source_established"}:
        return {"outcome": "no safe fix", "reason": reason}
    if violation == "not_confirmed" and reason:
        return {"outcome": "false positive", "reason": reason}
    return {}


def pass_on_concluded_outcome(errors: list, error: dict, concluded: dict) -> None:
    """Mark ``error`` concluded in the co_reported_findings of the findings after it.

    Clash Fest (2026-10-28): generateAndroidKeyStoreKey ended with no effect,
    and 13 minutes later the serverSocketFactory plan rewrote that method "so
    the co-reported KeyPairGeneratorSpec violation is resolved". Each list is
    replaced, never changed in place, because a worker may be reading it.
    Findings are analysed in order, so only the ones after it see this.
    """
    if not concluded:
        return
    identity = (error.get("spec", ""), error.get("csv_class", ""),
                error.get("method_name", ""), error.get("error_message", ""))

    def same(entry: dict) -> bool:
        return (entry.get("specification", ""), entry.get("class", ""),
                entry.get("method", ""), entry.get("error_message", "")) == identity

    for other in errors:
        entries = other.get("co_reported_findings")
        if other is error or not entries or not any(same(entry) for entry in entries):
            continue
        other["co_reported_findings"] = [
            {**entry, "status": CONCLUDED_CO_REPORTED_STATUS,
             "concluded_outcome": concluded.get("outcome", ""),
             "concluded_reason": str(concluded.get("reason", ""))[:400]}
            if same(entry) else entry
            for entry in entries]


def _detector_label(error: dict) -> str:
    return str((error.get("detector_context") or {}).get("label", "")).strip()


def returned_value_reaches_a_call(caller_body: str, callee: str) -> dict:
    """How what ``callee(...)`` returns reaches another call in ``caller_body``, or {}.

    Lexical and inside one method body: the result bound to a local
    (`val key = deriveKey(salt)`, `SecretKey key = derive(...)`, `key =
    derive(...)`) whose name is an argument of a later call
    (`cipher.init(mode, key, spec)`), or the call written directly as an
    argument of another one. What it finds is established; what it misses
    says nothing.
    """
    if not callee or callee.startswith("<"):
        return {}
    lines = str(caller_body or "").splitlines()
    call = re.compile(rf"(?<![\w.])(?:this\.)?{re.escape(callee)}\s*\(")
    for index, line in enumerate(lines):
        match = call.search(line)
        if not match:
            continue
        prefix = line[:match.start()]
        if re.search(r"\breturn\s*$", prefix):
            continue
        if prefix.count("(") > prefix.count(")") and re.search(r"[\w>\]]\s*\(", prefix) \
                and not re.match(r"\s*(?:if|while|for|when|switch)\s*\(", prefix):
            return {"binding": "", "reaches": line.strip()[:200]}
        bound = re.search(r"(?P<name>[A-Za-z_]\w*)\s*(?::\s*[^=]+?)?\s*=\s*$", prefix)
        if not bound or re.search(r"[=!<>]=?\s*$", prefix[:bound.end("name")]):
            continue
        name = bound.group("name")
        argument = re.compile(
            rf"[\w>\]]\s*\([^()]*(?:\([^()]*\)[^()]*)*(?<![\w.]){re.escape(name)}\b(?!\s*=[^=])")
        for later in lines[index + 1:]:
            if argument.search(later):
                return {"binding": name, "reaches": later.strip()[:200]}
    return {}


# A storage key: a string literal, a resource id (optionally through
# getString), or a named constant.
_STORAGE_KEY = (r'(?P<key>"[^"\n]{1,80}"|(?:\w+\.)?getString\(\s*R\.string\.\w+\s*\)'
                r'|R\.string\.\w+|(?:[A-Za-z_]\w*\.)*[A-Z][A-Z0-9_]{2,})')
_STORED_WRITE = re.compile(r"\.put\w*\(\s*" + _STORAGE_KEY + r"\s*,\s*(?P<value>[A-Za-z_]\w*)\s*\)")
_STORED_READ = re.compile(r"(?P<value>[A-Za-z_]\w*)\s*(?::\s*[\w.<>?]+\s*)?=\s*[\w.()]*?\.get\w*\(\s*"
                          + _STORAGE_KEY + r"\s*[,)]")


def _governed_lines(body: str, error: dict) -> List[tuple]:
    """(line, governed names it uses) of each line of ``body`` that calls or constructs
    what the finding's specification governs."""
    from cognalys.llm.false_positive_rules import governed_call_names
    from cognalys.llm.source_facts import governed_api_types

    context = error.get("specification_context")
    names = [value.rsplit(".", 1)[-1] for value in governed_api_types(context)]
    calls = governed_call_names(context)
    patterns = {**{name: rf"\b{re.escape(name)}\b" for name in names if name},
                **{name: rf"\.{re.escape(name)}\s*\(" for name in calls if name}}
    found = []
    for line in str(body or "").splitlines():
        used = {name for name, pattern in patterns.items() if re.search(pattern, line)}
        if used:
            found.append((line, used))
    return found


def _module_of(path: str) -> str:
    """The Gradle module of a repository path: what precedes its src/<set>/ folder."""
    text = str(path or "").replace("\\", "/")
    match = re.search(r"(?:^|/)src/[\w-]+/", text)
    return text[:match.start()].strip("/") if match else ""


def _key(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"^(?:\w+\.)?getString\((.*)\)$", r"\1", text.strip()))


def stored_value_link(writer: dict, reader: dict) -> str:
    """The storage key one finding's method writes and the other's reads, for one object.

    BookWyrm (2026-10-31): MainActivity.LogIn encrypts with
    `new GCMParameterSpec(128, magicCode.getBytes())` and stores magicCode
    with `putString(getString(R.string.q), magicCode)`; StartActivity.onCreate
    reads `codeMagic = ....getString(getString(R.string.q), ...)` and
    decrypts with `new GCMParameterSpec(128, codeMagic.getBytes())`. Two
    units, two drafts, two incompatible formats for one stored IV. Linked
    only when both findings carry the same specification, the written value
    is used on a line that calls what that specification governs in the
    writer, and the read value likewise in the reader. Returns the key, or "".
    """
    if not writer.get("spec") or writer.get("spec") != reader.get("spec"):
        return ""
    # One Gradle module: a key of the same name in another module is
    # another store, or code this link cannot follow (2026-11-01 review).
    if _module_of(writer.get("source_file_path", "")) != _module_of(reader.get("source_file_path", "")):
        return ""
    writer_lines = _governed_lines(writer.get("method_body", ""), writer)
    reader_lines = _governed_lines(reader.get("method_body", ""), reader)
    if not writer_lines or not reader_lines:
        return ""
    def used(value: str, lines: List[tuple]) -> set:
        """The governed names the lines using ``value`` reach."""
        return {name for line, names in lines
                if re.search(rf"(?<![\w.]){re.escape(value)}\b", line) for name in names}
    written: dict = {}
    for match in _STORED_WRITE.finditer(str(writer.get("method_body", ""))):
        reached = used(match.group("value"), writer_lines)
        if reached:
            written.setdefault(_key(match.group("key")), set()).update(reached)
    for match in _STORED_READ.finditer(str(reader.get("method_body", ""))):
        key = _key(match.group("key"))
        # The written value and the read value reach the same governed API
        # (both a GCMParameterSpec): a shared key name ("password", "token")
        # used for unrelated things on each side is no link.
        if key in written and written[key] & used(match.group("value"), reader_lines):
            return key
    return ""


def join_findings_of_one_sequence(errors: list) -> dict:
    """Merge findings that are one call sequence into the caller's finding.

    Grocy (2026-10-23): KeyStoreSpec was reported in X509KeyStoreFile.load
    (two Loads) and in save (a Store before any SetEntry), and load calls
    save. Analysed apart, each fix broke the first-run path the other one
    needed; the correct change rewrites both together. The primary finding
    carries the others as ``joint_findings`` and each of those is marked
    ``joined_into``.

    Two findings of one class and source file are one sequence when:
    - one method calls the other, whatever the specifications (the caller
      is primary);
    - they are in the same method (KeyPass's decrypt was reported under
      IvParameterSpecSpec and CipherSpec, and the two drafts rewrote the
      method in incompatible ways);
    - they carry the same specification and the same message in different
      methods (Amethyst's MacInstance: "initialised again after it
      completed" at update, doFinal and init is one object lifecycle).
    In the last two the finding discovered first is primary.

    Findings in different classes of one APK join when one method stores a
    value the other reads back for the same governed object (BookWyrm's
    encrypt and decrypt of one stored IV, 2026-10-31; stored_value_link);
    the writer is primary. The relations
    are transitive: a finding related to one already joined joins the group.

    A primary the detector labelled an echo (its producer was already
    reported) that calls a joined finding whose result reaches one of its
    calls gets ``echo_of``: that producer finding and the binding. Salty's
    encode (CipherSpec, upstream-refused) has `val key = deriveKey(salt)` and
    `cipher.init(..., key, ...)`; deriveKey is the PBEKeySpecSpec finding,
    and the echo's cause is that one's.

    Returns {joined unique_error_id: primary unique_error_id}.
    """
    from cognalys.integrations.rvsec import ECHO_LABELS

    def key(error: dict) -> tuple:
        path = error.get("_file_path")
        apk = path.parent.name if hasattr(path, "parent") else ""
        return (apk, str(error.get("csv_class", "")).split("$", 1)[0],
                str(error.get("source_file_path", "")))

    def calls(caller: dict, callee: dict) -> bool:
        name = str(callee.get("method_name", ""))
        if not name or name.startswith("<") or name == caller.get("method_name"):
            return False
        return re.search(rf"(?<![\w.]){re.escape(name)}\s*\(|\bthis\.{re.escape(name)}\s*\(",
                         str(caller.get("method_body", ""))) is not None

    def same_code(first: dict, second: dict) -> bool:
        if first.get("method_name") and first.get("method_name") == second.get("method_name"):
            return True
        return bool(first.get("spec")) and first.get("spec") == second.get("spec") and (
            " ".join(str(first.get("error_message", "")).split())
            == " ".join(str(second.get("error_message", "")).split()) != "")

    def root(identifier: str) -> str:
        while identifier in joined:
            identifier = joined[identifier]
        return identifier

    def join(primary: dict, other: dict) -> None:
        joined[other["unique_error_id"]] = primary["unique_error_id"]
        other["joined_into"] = primary["unique_error_id"]
        primary.setdefault("joint_findings", []).append({
            "unique_error_id": other["unique_error_id"],
            "specification": other.get("spec", ""),
            "class": other.get("csv_class", ""),
            "method": other.get("method_name", ""),
            "error_message": other.get("error_message", ""),
        })
        # What the other had already gathered follows it: a caller joined
        # into its own caller brings its callee along.
        for item in other.pop("joint_findings", []):
            joined[item["unique_error_id"]] = primary["unique_error_id"]
            primary["joint_findings"].append(item)
        # Where each joined finding's own code stands on the contribution base
        # (its extraction's contribution_base_status): when the unit's edits
        # are already there, a joint finding whose statement is still on the
        # base is not settled by them (tokn run_v6, 2026-10-31). Kept apart
        # from joint_findings, which every prompt reads.
        bases = primary.setdefault("joint_contribution_bases", {})
        if isinstance(other.get("contribution_base"), dict):
            bases[other["unique_error_id"]] = other["contribution_base"]
        bases.update(other.pop("joint_contribution_bases", None) or {})
        # Each joined finding's own specification context, by specification,
        # as enrich_spec_context gave it to that finding. BlueGate run_v6 and
        # run_v7 (2026-10-31): generateNewKeyPair [KeyPairGeneratorSpec],
        # joined into getOrCreateKeyPair [KeyPairSpec], reached the
        # extraction and the adjudication as a name and a message, and both
        # blocked on "the active KeyPairGenerator rule ... was not supplied".
        contexts = primary.setdefault("joint_specification_contexts", {})
        for spec, context in [(other.get("spec", ""), other.get("specification_context")),
                              *(other.pop("joint_specification_contexts", None) or {}).items()]:
            if spec and isinstance(context, dict) and context:
                contexts.setdefault(spec, context)
        # Each joined finding's own detector record, by finding id, so the
        # analyzer's record of a joined finding reaches extraction and
        # adjudication as the primary's does (reported_by_detector). Kept
        # apart from joint_findings, which every prompt reads.
        detector_contexts = primary.setdefault("joint_detector_contexts", {})
        detector_contexts[other["unique_error_id"]] = other.get("detector_context") or {}
        detector_contexts.update(other.pop("joint_detector_contexts", None) or {})
        if (_detector_label(primary) in ECHO_LABELS and not primary.get("echo_of")
                and _detector_label(other) not in ECHO_LABELS):
            link = returned_value_reaches_a_call(
                str(primary.get("method_body", "")), str(other.get("method_name", "")))
            if link:
                primary["echo_of"] = {
                    "specification": other.get("spec", ""),
                    "class": other.get("csv_class", ""),
                    "method": other.get("method_name", ""),
                    "error_message": other.get("error_message", ""),
                    **link,
                }

    groups: dict = {}
    for error in errors:
        if error.get("unique_error_id"):
            groups.setdefault(key(error), []).append(error)
    joined: dict = {}
    for group in groups.values():
        for caller in group:
            if caller["unique_error_id"] in joined:
                continue
            for callee in group:
                if (callee is caller or callee["unique_error_id"] in joined
                        or caller.get("joint_findings") and callee.get("joint_findings")):
                    continue
                if calls(caller, callee) and not calls(callee, caller):
                    join(caller, callee)
        # Same code is followed through findings already joined, so the
        # groups merge (a union-find over this class and file); the primary
        # discovered first stays primary. Tokn (2026-10-28): decrypt
        # [CipherSpec] joined encrypt (same rule and message), and decrypt
        # [GCMParameterSpecSpec], in the same method, was analysed alone.
        position = {error["unique_error_id"]: index for index, error in enumerate(group)}
        by_id = {error["unique_error_id"]: error for error in group}
        for first in group:
            for second in group:
                if second is first or not same_code(first, second):
                    continue
                roots = {root(first["unique_error_id"]), root(second["unique_error_id"])}
                if len(roots) == 2:
                    primary, other = sorted(roots, key=position.__getitem__)
                    join(by_id[primary], by_id[other])
    # Across classes of one APK: a stored value one finding's method writes
    # and another's reads for the same governed object (stored_value_link).
    # The writer is primary: its format is the one the reader must accept.
    by_apk: dict = {}
    for error in errors:
        if error.get("unique_error_id"):
            by_apk.setdefault(key(error)[0], []).append(error)
    every = {error["unique_error_id"]: error for error in errors if error.get("unique_error_id")}
    links = []
    for group in by_apk.values():
        for writer in group:
            for reader in group:
                if reader is writer or key(reader)[1:] == key(writer)[1:]:
                    continue
                link = stored_value_link(writer, reader)
                if link:
                    links.append((writer, reader, link))
    # Only an unambiguous pair: a finding in two links (one writer, several
    # readers, or both ends of a chain or cycle) is joined by none of them,
    # since a shared key name may tie unrelated code together.
    taking = collections.Counter(error["unique_error_id"] for writer, reader, _ in links
                                 for error in (writer, reader))
    pairs = collections.Counter(frozenset((w["unique_error_id"], r["unique_error_id"]))
                                for w, r, _ in links)
    for writer, reader, link in links:
        if (taking[writer["unique_error_id"]] > 1 or taking[reader["unique_error_id"]] > 1
                or pairs[frozenset((writer["unique_error_id"], reader["unique_error_id"]))] > 1):
            continue
        roots = (root(writer["unique_error_id"]), root(reader["unique_error_id"]))
        if roots[0] != roots[1]:
            join(every[roots[0]], every[roots[1]])
            for item in every[roots[0]]["joint_findings"]:
                if item["unique_error_id"] == roots[1]:
                    item["stored_value_key"] = link
    for identifier in joined:  # re-pointed to the final primary
        joined[identifier] = root(identifier)
    for error in errors:
        if error.get("unique_error_id") in joined:
            error["joined_into"] = joined[error["unique_error_id"]]
    return joined


class FixGenerator(
    FindingDiscovery,
    FindingContextCollection,
    FindingAdjudication,
    CoordinatedGeneration,
    IssueCandidates,
    FindingProcessing,
):
    """Orchestrates the error fix generation pipeline."""

    def __init__(self,
                 input_dir: Path,
                 output_dir: Path,
                 provider: OllamaProvider,
                 logger: logging.Logger,
                 api_logger: logging.Logger,
                 validation_mode: str = "strict",
                 allow_cache_invalidation: bool = False,
                 web_search: bool = False,
                 migration_policies: Optional[Dict] = None,
                 validation_feedback_dir: Optional[Path] = None,
                 repository_context_budget: int = 140_000,
                 pr_issue_style: str = "v1"):
        """Initialize the generator."""
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self.logger = logger
        self.api_logger = api_logger
        self.validation_mode = validation_mode
        self.allow_cache_invalidation = allow_cache_invalidation
        self.web_search = web_search and provider.provider_name in REMOTE_PROVIDERS
        self.migration_policies = migration_policies or {}
        # The staged pipeline is the only architecture now. Persisted
        # artifacts still carry the version, and solution_applicator still
        # checks it before authorising coordinated edits, so it stays a
        # written field rather than disappearing from the records.
        self.architecture_version = "v2"
        self.pr_issue_style = pr_issue_style
        self.evidence_packet_builder = FindingEvidencePacketBuilder(
            repository_context_budget=repository_context_budget
        )
        self.policy_registry = PolicyRegistry(
            library_data("llm_policies")
        )
        self.validation_feedback_dir = (
            Path(validation_feedback_dir) if validation_feedback_dir else None
        )

        self.stats = {
            "total_errors": 0,
            "analyzed": 0,
            "fixes_generated": 0,
            "confirmed_external_contract_findings": 0,
            "issue_candidates": 0,
            "successfully_analyzed": 0,
            "failed_analysis": 0,
            "crashed": 0,
            "provider_refused": 0,
            "provider_unavailable": 0,
            "provider_auth_failed": 0,
            "llm_budget_exhausted": 0,
            # Model calls summed over findings, so the cost of retries and
            # regenerations can be read per run (see CallBudget.summary).
            "llm_budget": {
                "findings": 0, "calls": 0, "failed_calls": 0,
                "transport_retries": 0, "replayed_calls": 0, "model_seconds": 0.0,
            },
            "not_analyzable": 0,
            # Already fixed on this branch, or not present in it at all.
            "not_applicable": 0,
            # One entry per finding that did not produce a solution, naming
            # what stopped it. Counting failures without recording their
            # cause is why the August runs say only "Failed to analyze".
            "failures": [],
            "abstained_no_safe_fix": 0,
            "high_confidence": 0,
            "medium_confidence": 0,
            "low_confidence": 0,
            "multi_file_candidates": 0,
            "coordinated_solutions": 0,
            "incomplete_multi_file_scope": 0,
            "total_time": 0,
        }
        # The two counters process_finding itself touches. Everything else in
        # self.stats is updated by run_analysis on the calling thread.
        self._stats_lock = threading.Lock()

    def bump_stat(self, key: str, amount: int = 1) -> None:
        """Increment a counter that concurrent findings may share."""
        with self._stats_lock:
            self.stats[key] += amount

    def run_analysis(self, resume: bool = True, dry_run: bool = False,
                     workers: int = 1):
        """Run the full analysis pipeline."""
        start_time = time.time()

        # Discover errors
        errors = self.discover_errors()
        invalid = getattr(self, "input_invalid", [])
        # Counted in the denominator and named, never silently dropped.
        self.stats["total_errors"] = len(errors) + len(invalid)
        record_input_invalid(self.stats, invalid, self.logger)
        self.logger.info(f"Discovered {len(errors)} errors to process")

        # Check for existing solutions
        existing = self.check_existing_solutions() if resume else set()
        remaining = [e for e in errors
                     if self.solution_key(e) not in existing]

        self.logger.info(f"Processing {len(remaining)} new errors "
                        f"({len(existing)} already complete)")

        if dry_run:
            self.logger.info("DRY RUN: Would process the errors above")
            return self.stats

        # Findings of one specification share a policy section of tens of
        # thousands of characters at the front of every extraction and
        # adjudication prompt. Analysed back to back, that prefix stays warm
        # in the provider's prompt cache; interleaved, it never does -- the
        # runs of 2026-09-07 recorded 0 cached input tokens on the two
        # largest stages. Grouping is a pure reordering: no finding is added,
        # dropped, or judged differently.
        remaining = self.group_by_specification(remaining)
        joined = join_findings_of_one_sequence(errors)
        if joined:
            self.record_joined_findings(errors, joined)
            remaining = [error for error in remaining
                         if error.get("unique_error_id") not in joined]
        annotate_co_reported_findings(
            [error for error in errors if error.get("unique_error_id") not in joined])
        total = len(remaining)

        # Findings are independent analyses whose cost is almost entirely
        # time spent waiting on the provider, so a worker pool multiplies
        # throughput without touching any decision. Bookkeeping stays on this
        # thread: workers only analyse and report back.
        for idx, (error, result) in enumerate(
            self.analyse_findings(remaining, workers), 1
        ):
            self.logger.info(f"[{idx}/{total}] {error.get('method_name')}")
            solution, success = result.solution, result.success
            outcome, decision = result.outcome, result.decision

            record_llm_budget(self.stats, result.llm_budget)

            if success or outcome == "abstained":
                self.stats["analyzed"] += 1
            if decision == "EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX":
                self.stats["confirmed_external_contract_findings"] += 1

            if success and solution:
                # Write solution JSON
                apk_name = error["_file_path"].parent.name
                method_name = error.get("method_name", "unknown")
                error_idx = error.get("error_index", 0)

                sol_dir = self.output_dir / apk_name
                sol_dir.mkdir(parents=True, exist_ok=True)

                sol_file = sol_dir / f"{error_idx}_{method_name}_solution.json"
                with open(sol_file, 'w') as f:
                    json.dump(solution, f, indent=2)

                # A previous NO_SAFE_FIX attempt for this finding must not
                # survive beside a now-supported PR candidate.
                safe_method = re.sub(r"[^A-Za-z0-9._-]+", "_", method_name)
                stale_issue = (
                    self.output_dir / "issue_candidates" / apk_name
                    / f"{error_idx}_{safe_method}_issue.json"
                )
                stale_issue.unlink(missing_ok=True)
                stale_issue.with_name(
                    f"{error_idx}_{safe_method}{NOT_APPLICABLE_TO_BASE_SUFFIX}"
                ).unlink(missing_ok=True)

                confidence = record_solution(self.stats, solution)
                self.logger.info(f"✓ Solution saved (confidence: {confidence}%)")
            else:
                # The converse of the stale issue above: a solution an earlier
                # attempt wrote must not outlive an analysis that now produced
                # none. EinkBro's rerun (2026-10-22) had its patch refused by
                # STORED_DERIVED_VALUE_CHANGED three times, and the 10:28
                # solution -- the very patch the gate refuses -- was applied,
                # built and turned into a ready pull request.
                stale_solution = (
                    self.output_dir / error["_file_path"].parent.name
                    / f"{error.get('error_index', 0)}_{error.get('method_name', 'unknown')}_solution.json"
                )
                if stale_solution.is_file():
                    stale_solution.unlink()
                    self.logger.warning(
                        "Removed the solution an earlier attempt wrote for %s: "
                        "this analysis produced none", error.get("method_name"))
                if outcome == "abstained":
                    self.stats["abstained_no_safe_fix"] += 1
                    self.logger.warning("No safe fix generated")
                else:
                    # A crash, a provider refusal and an analysis that simply
                    # produced nothing publishable are different events with
                    # different responses (see run_statistics).
                    record_failure(self.stats, self.logger, error, result)
            safe_method_for_stats = re.sub(
                r"[^A-Za-z0-9_.-]+", "_", error.get("method_name", "unknown")
            )
            issue_path_for_stats = (
                self.output_dir / "issue_candidates" / error["_file_path"].parent.name
                / f"{error.get('error_index', 0)}_{safe_method_for_stats}_issue.json"
            )
            if issue_path_for_stats.is_file():
                self.stats["issue_candidates"] += 1
            # The plans of the findings still to come in this class leave the
            # code of one that ended without a change as it is.
            pass_on_concluded_outcome(
                errors, error, concluded_outcome(self.output_dir, error, result))

        # Generate tracking JSON
        self.stats["total_time"] = time.time() - start_time
        self._write_tracking_json()
        self._write_summary_report()

        self.logger.info("=" * 70)
        self.logger.info("ANALYSIS COMPLETE")
        self.logger.info(
            "Analyzed: %d/%d",
            self.stats["analyzed"], self.stats["total_errors"],
        )
        self.logger.info("Fixes generated: %d", self.stats["fixes_generated"])
        self.logger.info(
            "Confirmed external-contract findings: %d",
            self.stats["confirmed_external_contract_findings"],
        )
        self.logger.info("Issue candidates: %d", self.stats["issue_candidates"])
        self.logger.info(
            "Time: %s",
            format_elapsed_time(self.stats["total_time"]),
        )
        return self.stats

    def record_joined_findings(self, errors: list, joined: dict) -> None:
        """error_solutions/joint_findings.json: which finding each joined one went into.

        The joined finding gets no analysis of its own; reporting gives it
        the outcome of the finding it was resolved with.
        """
        by_id = {error.get("unique_error_id"): error for error in errors}

        def identity(error: dict) -> dict:
            path = error.get("_file_path")
            return {"apk_name": path.parent.name if hasattr(path, "parent") else "",
                    "csv_class": error.get("csv_class", ""),
                    "method_name": error.get("method_name", ""),
                    "spec": error.get("spec", ""),
                    "unique_error_id": error.get("unique_error_id", "")}

        records = [{"joined": identity(by_id[secondary]), "into": identity(by_id[primary])}
                   for secondary, primary in joined.items()
                   if secondary in by_id and primary in by_id]
        path = self.output_dir / "joint_findings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        for record in records:
            self.logger.info("%s is analysed together with %s: one call sequence",
                             record["joined"]["method_name"], record["into"]["method_name"])

    def _write_tracking_json(self):
        """Write master tracking JSON."""
        tracking = {
            "total_errors": self.stats["total_errors"],
            "analyzed": self.stats["analyzed"],
            "fixes_generated": self.stats["fixes_generated"],
            "confirmed_external_contract_findings": self.stats[
                "confirmed_external_contract_findings"
            ],
            "issue_candidates": self.stats["issue_candidates"],
            "successfully_analyzed": self.stats["successfully_analyzed"],
            "failed_analysis": self.stats["failed_analysis"],
            "crashed": self.stats["crashed"],
            "provider_refused": self.stats["provider_refused"],
            "provider_unavailable": self.stats["provider_unavailable"],
            "provider_auth_failed": self.stats["provider_auth_failed"],
            "input_invalid": self.stats.get("input_invalid", 0),
            "llm_budget_exhausted": self.stats["llm_budget_exhausted"],
            "llm_budget": self.stats["llm_budget"],
            "not_analyzable": self.stats["not_analyzable"],
            "not_applicable": self.stats["not_applicable"],
            "failures": self.stats["failures"],
            "abstained_no_safe_fix": self.stats["abstained_no_safe_fix"],
            "multi_file_candidates": self.stats["multi_file_candidates"],
            "coordinated_solutions": self.stats["coordinated_solutions"],
            "incomplete_multi_file_scope": self.stats[
                "incomplete_multi_file_scope"
            ],
            "processing_time_seconds": self.stats["total_time"],
            "model": self.provider.model,
            "provider": self.provider.provider_name,
            "quality_distribution": {
                "high_confidence_valid": self.stats["high_confidence"],
                "medium_confidence": self.stats["medium_confidence"],
                "low_confidence_invalid": self.stats["low_confidence"]
            }
        }

        tracking_file = self.output_dir / "solutions_tracking.json"
        # Atomic: a run killed mid-write used to leave a truncated file that
        # every later reader of this run failed to parse.
        _write_json_atomic(tracking_file, tracking)

        self.logger.info(f"Tracking saved: {tracking_file}")

    def _write_summary_report(self):
        """Write text summary report."""
        report_file = self.output_dir / "analysis_summary.txt"

        with open(report_file, 'w') as f:
            f.write("Error Fix Generation Report\n")
            f.write("=" * 70 + "\n\n")

            f.write("STATISTICS\n")
            f.write("-" * 70 + "\n")
            f.write(f"Total errors: {self.stats['total_errors']}\n")
            f.write(f"Analyzed: {self.stats['analyzed']}/{self.stats['total_errors']}\n")
            f.write(f"Fixes generated: {self.stats['fixes_generated']}\n")
            f.write(
                "Confirmed external-contract findings: "
                f"{self.stats['confirmed_external_contract_findings']}\n"
            )
            f.write(f"Issue candidates: {self.stats['issue_candidates']}\n")
            f.write(f"Failed: {self.stats['failed_analysis']}\n")
            f.write(f"Crashed (pipeline defect): {self.stats['crashed']}\n")
            f.write(
                "Refused by provider: "
                f"{self.stats['provider_refused']}\n"
            )
            f.write(
                "Provider unavailable (left for a resume pass): "
                f"{self.stats['provider_unavailable']}\n"
            )
            f.write(
                "Not analysable (no source, no artifact): "
                f"{self.stats['not_analyzable']}\n"
            )
            f.write(
                "Not applicable (already fixed or not present): "
                f"{self.stats['not_applicable']}\n"
            )
            f.write(
                "Abstained (no safe fix): "
                f"{self.stats['abstained_no_safe_fix']}\n"
            )
            f.write(f"Multi-file candidates: {self.stats['multi_file_candidates']}\n")
            f.write(f"Coordinated solutions: {self.stats['coordinated_solutions']}\n")
            f.write(
                "Incomplete multi-file scope: "
                f"{self.stats['incomplete_multi_file_scope']}\n"
            )
            f.write(
                "Processing time: "
                f"{format_elapsed_time(self.stats['total_time'])}\n"
            )
            f.write(
                "Average per error: "
                f"{format_elapsed_time(self.stats['total_time'] / max(self.stats['total_errors'], 1))}\n\n"
            )

            f.write("QUALITY DISTRIBUTION\n")
            f.write("-" * 70 + "\n")
            f.write(f"High confidence (>75%): {self.stats['high_confidence']}\n")
            f.write(f"Medium confidence (50-75%): {self.stats['medium_confidence']}\n")
            f.write(f"Low confidence (<50%): {self.stats['low_confidence']}\n\n")

            f.write("MODEL CONFIGURATION\n")
            f.write("-" * 70 + "\n")
            f.write(f"Model: {self.provider.model}\n")
            f.write(f"Provider: {self.provider.provider_name}\n")
            f.write(f"Endpoint: {self.provider.endpoint}\n")

            # What this run's call traces recorded: the stages' prompt and
            # schema versions and the snapshots that answered; then the
            # prompt versions only the stages' artifacts carry (adjudication,
            # plan, behaviour review). A report only.
            from cognalys.llm.cost import recorded_artifact_versions, recorded_versions

            log_dir = getattr(self.provider, "prompt_log_dir", None)
            versions = (recorded_versions(Path(log_dir))
                        if isinstance(log_dir, (str, Path)) else {})
            versions.update(recorded_artifact_versions(self.output_dir))
            if versions:
                f.write("\nRECORDED VERSIONS\n")
                f.write("-" * 70 + "\n")
                for name, values in versions.items():
                    f.write(f"{name}: {', '.join(values)}\n")

        self.logger.info(f"Summary report saved: {report_file}")


# Waits between connection tests before the step gives up: about three
# minutes in all, every one logged, so the run's stall watch sees progress.
CONNECTION_RETRY_SECONDS = (10, 20, 40, 60, 60)


def wait_for_connection(provider, logger, label: str = "the provider",
                        waits=CONNECTION_RETRY_SECONDS, sleep=time.sleep) -> bool:
    """Test the connection, retrying for a few minutes before giving up.

    SMSecure's getKeyFromPassphrase (rvsec_misuse_20261016/run_v2,
    2026-10-29): the laptop woke from standby, the llm step was resumed, and
    its one connection test met the network still reconnecting (TLS
    answered with a self-signed certificate); the step exited and two
    findings that had already cost US$ 0.38 ended "não analisado". A test
    costs no model call, so a failure is tried again before the step is
    abandoned.
    """
    for attempt, wait in enumerate((0, *waits)):
        if wait:
            logger.warning(
                "Connection to %s failed (attempt %d); trying again in %ds",
                label, attempt, wait)
            sleep(wait)
        if provider.test_connection():
            return True
    return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate error fixes using Ollama or the OpenAI API"
    )
    parser.add_argument(
        "--provider", choices=("ollama", "openai", "anthropic"), default="ollama",
        help="LLM provider (default: ollama)",
    )
    parser.add_argument(
        "--model",
        default="codellama:7b",
        help="Ollama model to use (default: codellama:7b)"
    )
    parser.add_argument(
        "--endpoint",
        default="http://localhost:11434",
        help="Ollama endpoint (default: http://localhost:11434)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Ollama request timeout in seconds (default: 120)"
    )
    parser.add_argument(
        "--ollama-context-length", "--context-length",
        dest="context_length",
        type=int,
        default=16384,
        help=(
            "Ollama physical context window in tokens (default: 16384). "
            "--context-length remains as a compatibility alias."
        ),
    )
    parser.add_argument(
        "--repository-context-budget",
        type=int,
        default=140000,
        help="Logical character budget for deterministic repository evidence selection.",
    )
    parser.add_argument(
        "--pr-issue-style",
        choices=("v1", "v2", "v3"),
        default="v1",
        help=(
            "v1 preserves today's PR/issue text unchanged; v2 composes a "
            "human maintainer summary for the per-file-drafts coordinated "
            "path instead of the deterministic placeholder-prone one."
        ),
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=-1,
        help=(
            "Ollama num_predict (default: -1, generate until completion "
            "or the context limit)"
        ),
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh"),
        default="high",
        help="Reasoning effort for OpenAI models.",
    )
    parser.add_argument(
        "--validation-mode",
        choices=("strict", "balanced", "relaxed"),
        default="balanced",
        help="Evidence strictness; hard code checks always remain enabled.",
    )
    parser.add_argument(
        "--allow-cache-invalidation",
        action="store_true",
        help=(
            "Allow confirmed disposable cache entries to become cache misses "
            "during a safe algorithm migration. Never applies to user data."
        ),
    )
    parser.add_argument(
        "--web-search",
        action="store_true",
        help=(
            "Allow the OpenAI model to research additional authoritative "
            "sources when local context is incomplete."
        ),
    )
    parser.add_argument(
        "--migration-policies-file",
        type=Path,
        help=(
            "Optional JSON object keyed by unique_error_id with explicit, "
            "case-specific migration authorization and constraints."
        ),
    )
    parser.add_argument(
        "--validation-feedback-dir",
        type=Path,
        help=(
            "Optional build-validation directory. Persisted compiler diagnostics "
            "for the same finding are added to a corrective regeneration pass."
        ),
    )
    parser.add_argument(
        "--input-dir",
        default="error_methods_tracked",
        help="Input directory with error JSONs"
    )
    parser.add_argument(
        "--output-dir",
        default="error_solutions",
        help="Output directory for solutions"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help=(
            "LLM response cache directory (default: REPO_ROOT/outputs/.llm_cache). "
            "It must not live under OUTPUT_DIR: that directory is per-run, so a "
            "cache placed there is recreated empty every run and never hits."
        ),
    )
    parser.add_argument(
        "--prompt-log-dir",
        type=Path,
        help=(
            "Directory for one JSON record per prepared OpenAI request. "
            "Records include instructions, input and schema, never API keys."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable the persistent per-request LLM cache",
    )
    parser.add_argument(
        "--web-search-cache-ttl-hours",
        type=float,
        default=168.0,
        help=(
            "How long a cached web-research answer stays usable (default: "
            "168, one week). Research is the pipeline's most expensive stage "
            "and repeats verbatim across runs of the same finding; 0 "
            "disables its cache and refetches every time."
        ),
    )
    parser.add_argument(
        "--research-corpus-dir",
        type=Path,
        help=(
            "Directory of research answers kept per scope across runs. A shared "
            "research request found there (same request, not older than "
            "--research-corpus-max-age-days) is served from it; one researched live "
            "with both passes complete is stored there. Independent of --no-cache and "
            "of --web-search-cache-ttl-hours. Default: no corpus."
        ),
    )
    parser.add_argument(
        "--research-corpus-max-age-days",
        type=float,
        default=90.0,
        help="How long a research corpus entry stays usable (default: 90; 0 never expires).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Findings analysed concurrently (default: 1, sequential). Each "
            "finding is an independent analysis that spends nearly all of "
            "its time waiting on the provider, so raising this multiplies "
            "throughput; results are still consumed in submission order."
        ),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help=(
            "HTTP retries for an ordinary call (default: 2). Every call here "
            "re-runs a full generation at the configured reasoning effort, so "
            "this budget is far below what a cheap request would use."
        ),
    )
    parser.add_argument(
        "--web-search-retries",
        type=int,
        default=1,
        help=(
            "HTTP retries for a web-research call (default: 1). Each one "
            "re-runs the whole research pass, so this is deliberately far "
            "below the retry budget used for ordinary calls."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Skip already-processed errors"
    )
    parser.add_argument(
        "--no-resume",
        action="store_false",
        dest="resume",
        help="Reprocess all errors"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without making API calls"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="More detailed logging"
    )

    args = parser.parse_args()
    migration_policies = {}
    if args.migration_policies_file and args.migration_policies_file.exists():
        try:
            migration_policies = json.loads(
                args.migration_policies_file.read_text(encoding="utf-8")
            )
            if not isinstance(migration_policies, dict):
                raise ValueError("root value must be a JSON object")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"Invalid migration policies file: {exc}")

    # Setup logging
    output_dir = Path(args.output_dir)
    # Anchored at the repository root, not under output_dir: output_dir is the
    # per-run error_solutions directory, so the previous OUTPUT_DIR/.llm_cache
    # default created a fresh empty cache on every run and could never hit.
    cache_dir = None if args.no_cache else (
        args.cache_dir or _SRC_DIR.parent / "outputs" / ".llm_cache"
    )
    analysis_logger, api_logger = setup_logging(output_dir)

    if args.verbose:
        logging.getLogger("fix_analysis").setLevel(logging.DEBUG)

    # Print header
    print("\n" + "=" * 70)
    print("LLM Error Fix Generator")
    print("=" * 70)
    print(f"Model: {args.model}")
    print(f"Provider: {args.provider}")
    print(f"Endpoint: {args.endpoint}")
    print(f"Timeout: {args.timeout}s")
    if args.provider == "ollama":
        print(f"Context length: {args.context_length} tokens")
    else:
        print(f"Reasoning effort: {args.reasoning_effort}")
    print(f"Validation mode: {args.validation_mode}")
    print(f"Repository context budget: {args.repository_context_budget} characters")
    print(f"Allow cache invalidation: {args.allow_cache_invalidation}")
    print(f"Web search: {args.web_search and args.provider in REMOTE_PROVIDERS}")
    print(f"Max output tokens: {args.max_output_tokens}")
    print(f"Input: {args.input_dir}")
    print(f"Output: {args.output_dir}")
    print(f"LLM cache: {cache_dir if cache_dir else 'disabled'}")
    print(f"Resume: {args.resume}")
    print("=" * 70 + "\n")

    # Initialize provider
    if args.provider in REMOTE_PROVIDERS:
        provider = remote_provider(
            args.provider,
            endpoint=args.endpoint, model=args.model, timeout=args.timeout,
            max_output_tokens=args.max_output_tokens,
            reasoning_effort=args.reasoning_effort, cache_dir=cache_dir,
            prompt_log_dir=args.prompt_log_dir,
            web_search_cache_ttl_seconds=round(
                args.web_search_cache_ttl_hours * 3600
            ),
            web_search_retries=args.web_search_retries,
            retries=args.retries,
            logger=analysis_logger)
    else:
        provider = OllamaProvider(
            endpoint=args.endpoint, model=args.model, timeout=args.timeout,
            context_length=args.context_length,
            max_output_tokens=args.max_output_tokens, cache_dir=cache_dir,
            logger=analysis_logger)

    research_corpus = None
    if args.research_corpus_dir and args.provider in REMOTE_PROVIDERS:
        from cognalys.llm.research_corpus import ResearchCorpus, corpus_digest

        research_corpus = ResearchCorpus(args.research_corpus_dir,
                                         args.research_corpus_max_age_days, analysis_logger)
        provider.research_corpus = research_corpus
        print(f"Research corpus: {args.research_corpus_dir} "
              f"({corpus_digest(args.research_corpus_dir)['entries']} entries, "
              f"max age {args.research_corpus_max_age_days:g} days)")

    # Test connection
    provider_label = {"openai": "OpenAI", "anthropic": "Anthropic"}.get(args.provider, "Ollama")
    analysis_logger.info("Testing %s connection...", provider_label)
    if not wait_for_connection(provider, analysis_logger, provider_label):
        analysis_logger.error("Cannot connect to %s", provider_label)
        if args.provider == "ollama":
            analysis_logger.error("  Ensure Ollama is running: ollama serve")
        sys.exit(1)

    analysis_logger.info("%s connection successful", provider_label)

    # Check model availability
    available_models = provider.list_available_models()
    if args.model not in available_models:
        analysis_logger.error(f"✗ Model '{args.model}' not found")
        analysis_logger.error(f"  Available: {available_models}")
        if args.provider == "ollama":
            analysis_logger.error(f"  Pull it with: ollama pull {args.model}")
        sys.exit(1)

    analysis_logger.info(f"✓ Model '{args.model}' available")

    # Run generator
    generator = FixGenerator(
        input_dir=args.input_dir,
        output_dir=output_dir,
        provider=provider,
        logger=analysis_logger,
        api_logger=api_logger,
        validation_mode=args.validation_mode,
        allow_cache_invalidation=args.allow_cache_invalidation,
        web_search=args.web_search,
        migration_policies=migration_policies,
        validation_feedback_dir=args.validation_feedback_dir,
        repository_context_budget=args.repository_context_budget,
        pr_issue_style=args.pr_issue_style,
    )

    stats = generator.run_analysis(
        resume=args.resume, dry_run=args.dry_run, workers=args.workers,
    )
    cache_stats = provider.get_cache_stats()
    analysis_logger.info(
        "LLM cache: %d hit(s), %d miss(es), %d replayed within a finding",
        cache_stats["hits"], cache_stats["misses"], cache_stats.get("replays", 0),
    )
    if research_corpus is not None:
        corpus_stats = research_corpus.stats()
        analysis_logger.info(
            "Research corpus: %d served, %d researched and stored, %d kept, %d expired",
            corpus_stats["hits"], corpus_stats["stored"], corpus_stats["kept"],
            corpus_stats["expired"],
        )
    # A crash is a defect in this pipeline and a failed analysis produced
    # nothing; both fail the step. A content refusal is the provider's verdict
    # on the prompt, identical on every retry and already recorded with its
    # cause -- failing the step on it would abort a run over one finding the
    # operator cannot do anything about from here.
    # An authentication failure and an exhausted call budget also produced
    # nothing for their findings, and unlike a content refusal the operator
    # can act on both (the key, the limits), so they fail the step too.
    failing = step_failures(stats)
    if not args.dry_run and failing > 0:
        # Partial when other findings did get through. This is the most
        # expensive step in the pipeline and it is reached after cloning,
        # extraction and building have all been paid for; ending the batch
        # over one crashed finding throws that away for every other project
        # in it, and the analyses already written are still on disk.
        # "analyzed" already counts only the findings that succeeded or
        # abstained, so it is the number that got through as it stands.
        raise SystemExit(step_exit_code(stats.get("analyzed", 0), failing))
    if stats.get("provider_refused", 0) > 0:
        analysis_logger.warning(
            "%d finding(s) were refused by the provider and carry their cause "
            "in solutions_tracking.json; the run was not failed for them.",
            stats["provider_refused"],
        )
    if stats.get("not_analyzable", 0) > 0:
        # An input problem, not a pipeline defect: the finding names a target
        # this checkout does not contain. Failing the step would abort a run
        # over something no later stage can repair.
        analysis_logger.warning(
            "%d finding(s) had nothing to analyse and were skipped before any "
            "model call; their cause is in solutions_tracking.json.",
            stats["not_analyzable"],
        )
    if stats.get("not_applicable", 0) > 0:
        analysis_logger.warning(
            "%d finding(s) name a dependency or API this branch has already "
            "updated or does not contain, and were skipped before any model "
            "call; their cause is in solutions_tracking.json.",
            stats["not_applicable"],
        )


if __name__ == "__main__":
    main()
