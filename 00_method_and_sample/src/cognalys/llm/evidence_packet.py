"""The evidence packet each v2 model call is given.

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
from pathlib import Path
from typing import Any, Iterable

# Importable with only src/llm on the path, or as llm.<module>: src/ for the
# utils package, and this folder for the sibling modules imported by name.

from cognalys.llm.contract_gates import constant_material_flows, parameter_policies  # noqa: E402
from cognalys.utils.disposition import GENERATING_DECISIONS  # noqa: E402,F401
from cognalys.utils.finding_source import (  # noqa: E402,F401
    ADVISORY_IDENTIFIERS,
    advisory_identifiers,
)

from cognalys.llm.pipeline_common import (  # noqa: E402
    CONTEXT_BUILDER_VERSION,
    POLICY_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SEMANTIC_RESOLVER_VERSION,
    canonical_hash,
    canonical_json,
    reported_violation,
    violation_rule_section,
)
from cognalys.llm.research_questions import (  # noqa: E402
    specification_is_externally_anchored,
)
from cognalys.llm.source_facts import (  # noqa: E402
    bytecode_argument_origins,
    dependency_declaration_sources,
    enforced_input_size_evidence,
    field_call_sequences,
    governed_api_types,
    manifest_component_evidence,
    migration_contract_sources,
    observed_call_sequence,
    security_sink_evidence,
    shared_state_mutation_evidence,
    target_source_completeness,
    vendored_source_assessment,
    with_scope_provenance,
)


def joint_specification_contexts(error: dict) -> list:
    """(specification, context) of each joint finding reported under another rule.

    join_findings_of_one_sequence records them; the finding's own
    specification is left out, since its context is the packet's.
    """
    own = str(error.get("spec", "") or "")
    contexts = error.get("joint_specification_contexts") or {}
    return [(spec, context) for spec, context in contexts.items()
            if spec and spec != own and isinstance(context, dict) and context]


@dataclass
class _EvidenceItem:
    prefix: str
    kind: str
    trust_level: str
    payload: dict
    mandatory: bool = False
    priority: int = 0


_RUNTIME_FIELDS = (
    "value", "expected", "source_location", "code", "label",
    "label_meaning", "runtime_observation",
)


def runtime_measurement(error: dict) -> dict:
    """What a runtime detector observed at the reported call, or {}.

    Only a detector that executed the application records
    ``runtime_observation``; its value is the one that reached the governed
    call in those runs, and its fingerprints say whether that value changed
    between runs that each start from a clean device.
    """
    context = error.get("detector_context") or {}
    if not str(context.get("runtime_observation", "")).strip():
        return {}
    measured = {
        field: str(context[field]).strip()
        for field in _RUNTIME_FIELDS if str(context.get(field, "")).strip()
    }
    measured["tool"] = str(error.get("tool", "")).strip()
    measured["meaning"] = (
        "Measured while the application ran, not inferred from source: "
        "'value' is what reached the governed call at source_location (empty "
        "when the detector logs no value), and runtime_observation says in "
        "how many runs it was reported and whether byte material kept one "
        "fingerprint across runs. It settles what happened at that call in "
        "those runs. It does not say which source expression produced the "
        "value, whether other inputs occur, or what the security impact is."
    )
    return measured


def constant_material_evidence(method_body: str) -> dict:
    """Key, IV, salt or seed bytes the reviewed method takes from a constant."""
    flows = constant_material_flows(method_body)
    if not flows:
        return {}
    return {
        "flows": flows,
        "meaning": (
            "Read from the reviewed method's source: each listed argument -- "
            "directly or through the listed local assignments -- is built "
            "from constant_sources, values compiled into the application (a "
            "resource, a build-time field, a named constant or a literal). "
            "Such a value is the same in every installation of the build and "
            "is readable by anyone with the APK; transformations such as "
            "substring, getBytes or getEncoded keep it constant. "
            "line_in_method counts from the method's first line."
        ),
    }


def _promote_or_add_exact_sources(candidates: list, sources: list, kind: str,
                                  mandatory: bool, priority: int) -> None:
    """Supply each source as exact source, and keep it when it must be kept.

    A source already present as a candidate was skipped as "covered". When
    that candidate was a ranked related_source, the context budget could then
    drop it: Gallery's SearchActivity.kt and PickMediumDialog.kt, both uses of
    the deprecated AsyncTask, and its app/build.gradle.kts were excluded as
    rank_below_repository_context_budget, and the plan reported them as having
    no exact source. An existing candidate is now promoted instead.
    """
    by_path: dict[str, list] = {}
    for item in candidates:
        if item.trust_level == "repository_exact_source":
            by_path.setdefault(str(item.payload.get("source_file_path", "")), []).append(item)
    for source in sources:
        # Only a candidate holding the whole file covers it. A callee
        # definition is an excerpt of one method: Gallery's Context.kt
        # reached the packet as 676 characters of getCachedMedia, the
        # GetMediaAsynctask call site was never supplied, and the extraction
        # recorded that body as missing -- incomplete scope, no promotion.
        wanted = len(str(source.get("content", "")))
        existing = [
            item for item in by_path.get(str(source.get("source_file_path", "")), [])
            if len(str(item.payload.get("content", ""))) >= wanted
        ]
        if existing:
            if mandatory:
                for item in existing:
                    item.mandatory = True
                    item.priority = max(item.priority, priority)
            continue
        candidates.append(_EvidenceItem(
            "SRC", kind, "repository_exact_source", source, mandatory, priority,
        ))


class FindingEvidencePacketBuilder:
    """Build a bounded, provenance-rich packet from deterministic analysis."""

    def __init__(self, repository_context_budget: int = 140_000):
        self.repository_context_budget = max(16_000, repository_context_budget)

    @staticmethod
    def _size(value: Any) -> int:
        return len(canonical_json(value))

    @staticmethod
    def _relation_payload(item: dict) -> dict:
        keys = (
            "symbol_id", "resolved_symbol_id", "name", "arity", "receiver",
            "receiver_type", "path", "line", "enclosing_symbol", "resolution",
            "candidate_symbol_ids", "snippet",
        )
        return {key: item.get(key) for key in keys if item.get(key) not in (None, "", [])}

    @staticmethod
    def _target_source(repo_root: Path, target_path: str) -> str:
        """The reported file's text, for facts that read declarations in it."""
        try:
            return (repo_root / target_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    @staticmethod
    def derived_facts(error: dict, specification: dict, target_path: str) -> dict:
        """Facts the adjudicator otherwise has to ask for, supplied up front.

        Ownership of the reported class, whether the specification's own
        governed APIs appear in the reviewed source, and the callers of the
        reported method are all computable from the checkout. They were
        supplied only when the extractor happened to request them, and the
        request was then declined because the responder could answer only
        with a repository file -- which is how `unresolved ownership and
        version of the reported class`, `reported-rule applicability is
        unconfirmed` and `incomplete_reachable_caller_contract` each ended a
        finding. Supplying them unconditionally removes the dependence on
        the model asking the right question.

        Every record carries its own status, including the ones meaning "not
        established". Failure here is recorded, never fatal: a packet
        without these facts is the packet the pipeline built before.
        """
        repository_path = str(error.get("repository_path", ""))
        if not repository_path:
            return {"status": "no_checkout"}
        try:
            from cognalys.extraction.evidence_facts import (
                artifact_ownership, byte_material, caller_contract,
                governed_call_bindings, parameter_producers,
                specification_applicability, user_input_parse_flow,
            )

            from cognalys.extraction.iv_provenance import iv_provenance_fact

            repo_root = Path(repository_path)
            from cognalys.llm.fix_generation_common import reporting_detector_site
            from cognalys.validation.detector_replay import reproduce

            return {
                "status": "collected",
                "call_binding": governed_call_bindings(
                    repo_root, target_path, specification,
                    bytecode=error.get("apk_bytecode") or None,
                ),
                # The reporting detector re-run on this checkout. For a
                # detector-rule finding it is the rule's own authority on
                # whether the rule applies here; a tool without an adapter
                # says so. The corpus records the rule identifier as spec.
                "detector_reproduction": reproduce(
                    str(error.get("tool", "")), repo_root, target_path,
                    str(error.get("spec", "")),
                    str(error.get("method_name", "")),
                    str(error.get("csv_class", "")),
                ),
                "artifact_ownership": artifact_ownership(
                    repo_root, str(error.get("csv_class", "")), target_path,
                ),
                "specification_applicability": specification_applicability(
                    specification, str(error.get("method_body", "")),
                    str(error.get("error_message", "")),
                ),
                "caller_contract": caller_contract(
                    repo_root, str(error.get("method_name", "")), target_path,
                    str(error.get("csv_class", "")),
                    str(error.get("method_body", "")),
                ),
                # Whether text a user can edit reaches a parser with nothing
                # to catch its failure. Any parse family, any specification.
                "input_flow": user_input_parse_flow(
                    str(error.get("method_body", "")),
                    FindingEvidencePacketBuilder._target_source(repo_root, target_path),
                    repo_root,
                ),
                # Where the bytes given to each key, IV or salt constructor
                # come from, when the method and its file settle it.
                "byte_material": byte_material(
                    str(error.get("method_body", "")),
                    FindingEvidencePacketBuilder._target_source(repo_root, target_path),
                ),
                # Whether the IV each spec of the method carries is the one
                # the cipher provider chose at an encryption given no IV
                # (Cryptomator Lite run_v6, 2026-10-30: the adjudicator
                # blocked on "the upstream IV producer is not supplied").
                "iv_provenance": iv_provenance_fact(error),
                # What each caller passes for the parameters the method
                # hands on, and what produced it (FreeOTP: a KeyStore key
                # from one caller, a PBKDF2 key from the other).
                "parameter_producers": parameter_producers(
                    repo_root, str(error.get("method_body", "")),
                    str(error.get("method_name", "")),
                    str(error.get("csv_class", "")), target_path,
                ),
                # The checker's own code where it raised this finding's code.
                "reporting_detector_site": (
                    reporting_detector_site(error).get("reporting_detector_site")
                    or {"status": "not_available"}
                ),
                # What the remediation policy's evidence collectors
                # established (servers' certificate chains, the digests of
                # the files a class downloads). whoBIRD (2026-10-18): passed
                # only inside the policy, the downloaded files' verified MD5
                # matches were still listed as missing evidence.
                "policy_evidence": {
                    key: value for key, value in
                    (error.get("case_migration_policy") or {}).items()
                    if key.startswith("established_")
                },
                "bound": (
                    "Derived from the checkout by deterministic rules, not by "
                    "a model. A status of unresolved, undeclared_external, "
                    "no_governed_api_matched, callers_truncated or "
                    "not_searchable states a limit of what the checkout "
                    "settles; it is not a negative finding. caller_contract "
                    "names what it searched (searched_name, "
                    "searched_declaring_file). detector_reproduction alert_present means the "
                    "detector that reported the finding raises the same rule "
                    "in the same method on this revision; any other status "
                    "there is a limit of what could be run, not evidence "
                    "that the rule does not apply. byte_material "
                    "fresh_zero_filled and constant_literal are facts about "
                    "the bytes themselves: such bytes are fixed, the same on "
                    "every run, and never the output of a random source; "
                    "not_settled means the method and its file do not say. "
                    "iv_provenance provider_generated means every IV spec "
                    "the method builds reaches only decryptions, and the "
                    "files holding them encrypt with Cipher.init(ENCRYPT_MODE, "
                    "key) and no parameter spec and keep the IV the cipher "
                    "returns: that IV is the one the provider generated from "
                    "its own random source; not_established settles nothing. "
                    "parameter_producers lists, for each parameter the method "
                    "passes to a call, what every call site in the checkout "
                    "passes and the assignments that produced it; when the "
                    "method only forwards a value, the producers are where "
                    "the governed value is made, and a fix belongs there. "
                    "key_algorithms is the algorithm each key was made for "
                    "(what its getAlgorithm() returns) and used_with the "
                    "algorithm of the object the method gives it to; a key "
                    "made for another algorithm is a settled fact about its "
                    "origin, not a gap in a trace. reporting_detector_site is "
                    "the reporting checker's own code at the line that raised "
                    "this finding: its conditions are what the runtime "
                    "observed, and its comments, where they name a conforming "
                    "route, are the checker's statement of what it accepts. "
                    "policy_evidence records what the pipeline itself "
                    "observed outside the code -- connecting to a server, "
                    "downloading a file and computing its digests -- and is "
                    "established fact, not a hypothesis to confirm."
                ),
            }
        except Exception as exc:  # noqa: BLE001 -- recorded, never fatal
            return {"status": "unavailable", "detail": str(exc)[:200]}

    @staticmethod
    def _with_catalog_callers(
        facts: dict, catalog: list, error: dict, target_path: str,
    ) -> dict:
        """Count the catalog's callers resolved to the reported class on the caller contract.

        Amethyst's MacInstance.<init> packet said no_callers_found next to 23
        caller entries, and two generation blockers were discharged on it. The
        count lets the discharge refuse a "no callers" its own packet
        contradicts; only callers resolved to the reported class count, so a
        same-named method of another class does not. A Kotlin top-level
        function is indexed under `pkg.<top-level>`, not under the FooKt
        class the bytecode names, so it counts when the package and the
        function's name are the reported ones.
        """
        contract = facts.get("caller_contract") if isinstance(facts, dict) else None
        if not isinstance(contract, dict):
            return facts
        class_name = str(error.get("csv_class", ""))
        owners = {
            name for name in (
                class_name.split("$")[0].rsplit(".", 1)[-1],
                class_name.rsplit(".", 1)[-1].split("$")[-1],
                Path(target_path).stem if target_path else "",
            ) if name
        }
        package = class_name.split("$")[0].rpartition(".")[0]
        top_level = class_name.split("$")[0].endswith("Kt")
        method = re.split(r"[$-]", str(error.get("method_name", "")), 1)[0]

        def of_target(symbol: str) -> bool:
            owner_path, _, member = symbol.partition("#")
            owner_package, _, owner = owner_path.rpartition(".")
            if owner in owners:
                return True
            return (top_level and owner == "<top-level>" and owner_package == package
                    and re.split(r"[(\s]", member, 1)[0] == method)

        contract["catalog_callers_of_target"] = sum(
            1 for item in catalog
            if item.get("kind") == "resolved_caller"
            and of_target(str((item.get("data") or {}).get("resolved_symbol_id", "")))
        )
        return facts

    def build(self, error: dict, selected_policy: dict) -> dict:
        context = error.get("expanded_repository_context", {}) or {}
        impact = context.get("multi_file_impact", {}) or {}
        semantic = impact.get("semantic_resolution", {}) or {}
        target_path = str(error.get("source_file_path", ""))
        exact_sources = [
            item for item in impact.get("exact_candidate_sources", [])
            if isinstance(item, dict) and item.get("source_file_path")
        ]
        exact_by_path = {
            str(item["source_file_path"]): item for item in exact_sources
        }
        required = impact.get("coverage_requirements", {}) or {}
        # Must stay the same set validate_multi_file_scope requires the
        # analysis to have reviewed. caller_files was demanded there but not
        # prioritised here, so a caller could be graded as unreviewed while
        # never having been offered as evidence.
        required_paths = {
            target_path,
            *required.get("declaration_files", []),
            *required.get("behavioral_contract_files", []),
            *required.get("flow_consumer_files", []),
            *required.get("stored_value_files", []),
            *required.get("caller_files", []),
            *required.get("ambiguous_call_files", []),
        }
        required_paths.discard("")

        candidates: list[_EvidenceItem] = []
        if target_path in exact_by_path:
            item = exact_by_path[target_path]
            candidates.append(_EvidenceItem(
                "SRC", "target_source", "repository_exact_source",
                {
                    "source_file_path": target_path,
                    "relevance": item.get("relevance", []),
                    "content": item.get("content", ""),
                }, True, 100,
            ))
        else:
            candidates.append(_EvidenceItem(
                "SRC", "target_method", "repository_exact_source",
                {
                    "source_file_path": target_path,
                    "symbol": error.get("method_name", ""),
                    "content": error.get("method_body", ""),
                    "imports": error.get("imports", ""),
                }, True, 100,
            ))

        specification = with_scope_provenance(
            error.get("specification_context", {}) or {},
            str(error.get("csv_class", "")),
        )
        if specification.get("status") == "available":
            candidates.append(_EvidenceItem(
                "SPEC", "verified_specification", "verified_primary_specification",
                specification, True, 99,
            ))
        # The rules of the joint findings, supplied as the finding's own is:
        # the analysis resolves them too (finding.joint_findings), and with
        # only a name and a message the extraction recorded "the active
        # KeyPairGenerator rule ... was not supplied" for BlueGate's joined
        # generateNewKeyPair (run_v6, run_v7, 2026-10-31).
        for _, joint_context in joint_specification_contexts(error):
            joint = with_scope_provenance(joint_context, str(error.get("csv_class", "")))
            if joint.get("status") == "available":
                candidates.append(_EvidenceItem(
                    "SPEC", "verified_specification", "verified_primary_specification",
                    joint, True, 98,
                ))

        # The observed call sequence against the specification's own order
        # grammar. Mandatory when the detector reported an ordering violation:
        # that is precisely the finding the analysis has to adjudicate, and
        # without it the reported predicate has nothing to be checked against.
        sequence = observed_call_sequence(error)
        if sequence:
            ordering_violation = "sequence" in reported_violation(error).lower()
            candidates.append(_EvidenceItem(
                "SEQ", "observed_call_sequence", "repository_lexical_discovery",
                {
                    "specification_protocol": specification.get("protocol", ""),
                    "observed_sequence": [item["event"] for item in sequence],
                    "positions": sequence,
                    "scope": "reviewed method body only",
                    "limitations": [
                        "Events satisfied in a caller are not visible here.",
                        "Receivers are matched lexically, so an unrelated "
                        "receiver can produce a spurious event.",
                        "Each position is one source line; where the modelled "
                        "events differ only by overload the position lists "
                        "every candidate rather than guessing one.",
                    ],
                }, ordering_violation, 97,
            ))

        # The same, for a field the reviewed method assigns: the detector
        # follows that object through every method of the file.
        ordering_violation = "sequence" in reported_violation(error).lower()
        class_source = ""
        repository_path = str(error.get("repository_path", "") or "")
        if repository_path and target_path:
            try:
                class_source = (Path(repository_path) / target_path).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                class_source = ""
        field_sequences = field_call_sequences(
            class_source, str(error.get("method_body", "")), specification,
        )
        if field_sequences:
            candidates.append(_EvidenceItem(
                "SEQ", "field_call_sequence", "repository_lexical_discovery",
                {
                    "specification_protocol": specification.get("protocol", ""),
                    "fields": field_sequences,
                    "scope": (
                        "every line of the reviewed file that uses a field "
                        "the reviewed method assigns from a governed call"
                    ),
                    "limitations": [
                        "A detector tracks a field-held object across every "
                        "method that uses it and commonly reports the "
                        "violation where the object was created; the order "
                        "in which those methods run is not visible here.",
                        "Receivers and enclosing methods are matched "
                        "lexically.",
                        "Uses outside the reviewed file, and aliases of the "
                        "field, are not visible.",
                    ],
                }, ordering_violation, 96,
            ))

        # Where the reported method's value goes, read from its call sites.
        # Mandatory when it names stored data or leaves the process: that
        # decides whether changing the value is a cache miss or data loss.
        consumers = error.get("derived_value_consumers") or {}
        stored_contract = error.get("stored_value_contract") or {}
        if stored_contract.get("status") not in {None, "not_applicable"}:
            candidates.append(_EvidenceItem(
                "STORE", "stored_value_contract", "repository_static_analysis",
                stored_contract, True, 98,
            ))
        if consumers.get("consumers"):
            candidates.append(_EvidenceItem(
                "FLOW", "derived_value_consumers", "repository_static_analysis",
                consumers,
                bool(consumers.get("persisted") or consumers.get("transmitted")),
                93,
            ))
        # The keys the method's value is written under, when no code reads
        # them: no consumer whose compatibility the change must keep.
        from cognalys.utils.persisted_value_readers import readers_evidence

        unread = readers_evidence(
            error.get("persisted_value_readers"),
            str(error.get("source_method_name") or error.get("method_name", "")),
        )
        if unread:
            candidates.append(_EvidenceItem(
                "FLOW", "persisted_value_readers", "repository_static_analysis",
                unread, True, 93,
            ))
        # Suppressions, comments and gates showing that the reported
        # behaviour is insecure on purpose (weeWX Weather, 2026-11-04). It
        # informs how to fix, never whether the finding is real.
        from cognalys.utils.deliberate_insecurity import deliberate_insecurity_evidence

        intent = deliberate_insecurity_evidence(error.get("deliberate_insecurity"))
        if intent:
            candidates.append(_EvidenceItem(
                "INTENT", "deliberate_insecurity", "repository_static_analysis",
                intent, True, 92,
            ))
        rename = error.get("rename_safety") or {}
        if rename.get("symbol"):
            candidates.append(_EvidenceItem(
                "RENAME", "rename_safety_assessment", "repository_static_analysis",
                rename, False, 60,
            ))

        ephemeral_discriminator = context.get(
            "ephemeral_runtime_discriminator_assessment", {}
        ) or {}
        if ephemeral_discriminator.get("supported") is True:
            candidates.append(_EvidenceItem(
                "FLOW", "ephemeral_runtime_discriminator",
                "repository_static_analysis",
                {
                    "assessment": ephemeral_discriminator,
                    "value_usage_graph": context.get("value_usage_graph", {}),
                    "repository_call_sites": context.get(
                        "repository_call_sites", []
                    ),
                }, True, 92,
            ))

        for path in sorted(exact_by_path):
            if path == target_path:
                continue
            source = exact_by_path[path]
            is_test = bool(re.search(r"(?:^|/)src/(?:test|androidTest)/", path, re.I))
            mandatory = path in required_paths
            priority = 90 if mandatory else 65 if is_test else 55
            candidates.append(_EvidenceItem(
                "TEST" if is_test else "SRC",
                "test_source" if is_test else "related_source",
                "repository_exact_source",
                {
                    "source_file_path": path,
                    "relevance": source.get("relevance", []),
                    "content": source.get("content", ""),
                }, mandatory, priority,
            ))

        resolved = [
            self._relation_payload(item)
            for item in semantic.get("resolved_callers", [])
            if isinstance(item, dict)
        ]
        for item in sorted(resolved, key=lambda value: (
            str(value.get("path", "")), int(value.get("line", 0) or 0)
        )):
            candidates.append(_EvidenceItem(
                "CALL", "resolved_caller", "repository_semantic_relation",
                item, False, 80,
            ))

        # The raw lexical callers are dropped below as "superseded by semantic
        # resolution". That exclusion was unconditional while the substitution
        # was only best-effort: when the index cannot resolve callers it
        # reports "unknown" and contributes nothing, so the packet ended up
        # with no caller evidence at all -- and validate_multi_file_scope then
        # failed the analysis for not reviewing caller files it was never
        # shown. Observed on net.gaast.giggity, where the withheld caller was
        # Reminder.java, the very file that derives alarm identifiers from the
        # value under repair. Substitute only where a substitute exists.
        callers_resolved = bool(resolved)
        lexical_callers: list[dict] = []
        if not callers_resolved:
            for item in impact.get("direct_callers", []) or []:
                payload = (
                    dict(item) if isinstance(item, dict)
                    else {"description": str(item)}
                )
                if not payload:
                    continue
                payload.setdefault("resolution", "lexical_discovery_unresolved")
                lexical_callers.append(payload)
            for item in sorted(lexical_callers, key=lambda value: (
                str(value.get("path", value.get("source_file_path", ""))),
                int(value.get("line", 0) or 0),
            )):
                candidates.append(_EvidenceItem(
                    "CALL", "lexical_caller", "repository_lexical_discovery",
                    item, False, 78,
                ))

        ambiguous = [
            self._relation_payload(item)
            for item in semantic.get("ambiguous_calls", [])
            if isinstance(item, dict)
        ]
        for item in sorted(ambiguous, key=lambda value: (
            str(value.get("path", "")), int(value.get("line", 0) or 0)
        )):
            candidates.append(_EvidenceItem(
                "EDGE", "ambiguous_relation", "repository_semantic_relation",
                item, False, 70,
            ))

        tests = context.get("tests", []) or impact.get("related_tests", []) or []
        for item in tests:
            payload = (
                dict(item) if isinstance(item, dict)
                else {"description": str(item)}
            )
            # Repository analyzers historically used `path` for test
            # relations while exact sources and planners use
            # `source_file_path`. Preserve the original field but expose one
            # canonical path so an established test source set is not lost at
            # the planning boundary.
            if (not payload.get("source_file_path") and payload.get("path")):
                payload["source_file_path"] = payload["path"]
            candidates.append(_EvidenceItem(
                "TEST", "test_context", "repository_semantic_relation",
                payload, False, 60,
            ))

        # Priority 80: above callers and tests, below the construction sites
        # and the manifest. What the reviewed method calls is part of what it
        # does, and the packet never carried it. Element Android's
        # handleAppStarted reads its nested Intent through
        # getParcelableExtraCompat, and the adjudication of CVE-2024-26131
        # blocked on "unconfirmed_pre_launch_validation_state" -- it could not
        # establish whether that helper sanitises what it returns. The helper
        # is fifteen lines in the same repository and validates nothing.
        for item in impact.get("callee_definitions", []) or []:
            if isinstance(item, dict) and item.get("definition"):
                candidates.append(_EvidenceItem(
                    "CALLEE", "callee_definition",
                    "repository_exact_source", item, False, 80,
                ))

        # Priority 88: just above the callee bodies, because a constraint
        # finding is decided by a value rather than by a call. photok's
        # CipherSpec finding is entirely about the string behind
        # Algorithm.AesCbcPkcs7Padding, and the packet carried the reviewed
        # method, its callers, its tests and its callees while the enum that
        # fixes that string was fetched from the web instead.
        for item in impact.get("constant_definitions", []) or []:
            if isinstance(item, dict) and item.get("declaration"):
                candidates.append(_EvidenceItem(
                    "CONST", "constant_definition",
                    "repository_exact_source", item, False, 88,
                ))

        # Priority 86: the project's own account of the format it stores and
        # why. It is repository evidence, not a web claim -- the distinction
        # the adjudication is instructed to draw -- and it is what answers a
        # compatibility question instead of asking the maintainer to. photok's
        # ENCRYPTION.md documents the versioned stored format, the migration
        # between versions, and the reason AES/CBC was chosen over AES/GCM;
        # the run that reported its CipherSpec finding asked the maintainer
        # for all three.
        for item in impact.get("documentation_evidence", []) or []:
            if isinstance(item, dict) and item.get("content"):
                candidates.append(_EvidenceItem(
                    "DOC", "project_documentation",
                    "repository_exact_source", item, False, 86,
                ))

        # Priority 87: between the constants and the documentation, because
        # whether a produced value survives a round trip through storage is
        # what a compatibility blocker actually turns on. photok's
        # adjudication blocked on the mapper not being established while the
        # DAO, the repository and the backup flows were all in the packet;
        # the mapper is twenty-two lines that pass the parameters through
        # untouched in both directions.
        for item in impact.get("conversion_definitions", []) or []:
            if isinstance(item, dict) and item.get("definition"):
                candidates.append(_EvidenceItem(
                    "CONV", "layer_conversion",
                    "repository_exact_source", item, False, 87,
                ))

        # Priority 89: above the conversion it completes. CONV shows a
        # setting being persisted and the target source shows it being read
        # back, and photok's adjudication had both and still blocked on
        # "whether all readers select an algorithm from the stored
        # parameters" -- the halves were there and the relation between them
        # was stated nowhere. This states it.
        for item in impact.get("round_trip_evidence", []) or []:
            if isinstance(item, dict) and item.get("read_back_by"):
                candidates.append(_EvidenceItem(
                    "ROUND", "setting_round_trip",
                    "repository_exact_source", item, False, 89,
                ))

        # Priority 85: above callers and tests, below the target source and
        # the specification. A method that reads constructor-injected state
        # cannot be judged from its own body alone, and the arguments at
        # construction are the only place that state is decided. markor's
        # createKeyFromPassword takes its Random from
        # `new JavaPasswordbasedCryption(Build.VERSION.SDK_INT,
        # new SecureRandom())`; with no evidence entry pointing at that line
        # the analysis recorded the randomness source as missing evidence
        # while the packet already carried the file containing it.
        for item in context.get("class_construction_sites", []) or []:
            if isinstance(item, dict) and item.get("path"):
                candidates.append(_EvidenceItem(
                    "CTOR", "class_construction_site",
                    "repository_exact_source", item, False, 85,
                ))

        # Priority 96: below the target source and the specification, above
        # every caller and test. Whether the component is reachable from
        # outside the application decides what the finding even means, so it
        # must not be the thing a full context budget drops.
        for declaration in manifest_component_evidence(
            str(error.get("repository_path", "")),
            str(error.get("csv_class", "")),
        ):
            candidates.append(_EvidenceItem(
                "MANIFEST", "component_declaration", "repository_exact_source",
                declaration, False, 96,
            ))

        # Priority 95: what a runtime detector measured while executing the
        # application. DET-001 is a report and cannot be cited, which is
        # right for a static analyser's guess but discards a measurement:
        # droid_scep's RSA size was observed as 8192 and adjudicated "not
        # established" because the source passes a parameter, and
        # mtgfam's IV, one fingerprint over every run, was dismissed under a
        # shape about provenance a static analyser cannot see
        # (exp_final_20261019). Only a detector that records observations of
        # runs produces runtime_observation, so a static report never gets
        # this entry.
        constant = constant_material_evidence(str(error.get("method_body", "")))
        if constant:
            candidates.append(_EvidenceItem(
                "CONST", "constant_material_flow",
                "repository_static_analysis", constant, False, 95,
            ))
        runtime = runtime_measurement(error)
        if runtime:
            candidates.append(_EvidenceItem(
                "RUN", "detector_runtime_measurement",
                "detector_runtime_measurement", runtime, False, 95,
            ))

        build_payload = {
            "platform_context": context.get("platform_context", {}),
            "android_build_variants": context.get("android_build_variants", {}),
            "configuration_evidence": impact.get("configuration_evidence", []),
        }
        candidates.append(_EvidenceItem(
            "BUILD", "build_context", "repository_exact_source",
            build_payload, False, 50,
        ))
        dependency = (
            error.get("dependency_context", {})
            or context.get("dependency_context", {})
            or {}
        )
        if dependency:
            candidates.append(_EvidenceItem(
                "DEP", "dependency_context", "repository_static_analysis",
                {
                    key: dependency.get(key)
                    # The adjudication prompt tells the model to read
                    # reached_directly and reached_through, and neither was
                    # in this list, so it was asked about fields it was never
                    # shown. third_party_attribution and lexical_carriers are
                    # how an unresolved transitive finding is attributed at
                    # all, and dependency_graph says why it is unresolved.
                    for key in (
                        "status", "finding_origin", "external_class",
                        "likely_dependency", "dependency_candidates",
                        "declared_dependencies", "build_constraints",
                        "resolved_version_evidence", "gradle_resolution",
                        "reached_directly", "reached_through",
                        "dependency_path", "dependency_graph",
                        "third_party_attribution", "lexical_carriers",
                        "cached_artifact_metadata",
                        "artifact_repository_alignment",
                        "repository_usage_evidence", "related_tests", "build_files",
                    ) if dependency.get(key) not in (None, "", [], {})
                }, selected_policy.get("family") == "dependency", 95,
            ))

        # What a dependency or deprecation message names and whether this
        # checkout still has it, with the build files a plan must be able to
        # edit supplied as exact source.
        dependency_finding = error.get("dependency_finding") or {}
        if dependency_finding.get("kind"):
            candidates.append(_EvidenceItem(
                "DEPF", "dependency_finding_grounding", "repository_static_analysis",
                {
                    key: dependency_finding.get(key)
                    for key in (
                        "kind", "status", "subjects", "deprecated_types",
                        "reported_version", "target", "change_scale",
                        "matched_declarations", "usages", "usage_inventory",
                        "migration_scale", "effective_resolution", "bound",
                    ) if dependency_finding.get(key) not in (None, "", [], {})
                }, True, 98,
            ))
            _promote_or_add_exact_sources(
                candidates,
                dependency_declaration_sources(repository_path, dependency_finding, set()),
                "dependency_declaration_source",
                selected_policy.get("family") in {"dependency", "api_migration"},
                95,
            )

            # For a deprecated API: the mechanisms this repository already
            # uses for the same role, and every use of the deprecated API,
            # with those files as exact source so a plan can migrate them.
            idioms = error.get("repository_replacement_idioms") or {}
            if idioms.get("by_deprecated_type"):
                candidates.append(_EvidenceItem(
                    "REPO", "repository_replacement_idioms",
                    "repository_lexical_discovery", idioms,
                    idioms.get("status") == "found", 94,
                ))
            usage_contract = error.get("deprecated_usage_contract") or {}
            if usage_contract.get("contracts"):
                candidates.append(_EvidenceItem(
                    "CONTRACT", "deprecated_usage_contract",
                    "repository_lexical_discovery", usage_contract,
                    usage_contract.get("status") == "found", 94,
                ))
                _promote_or_add_exact_sources(
                    candidates,
                    migration_contract_sources(repository_path, usage_contract, set()),
                    "deprecated_usage_source", True, 94,
                )

        # The reported method as it was compiled, for a class the checkout does
        # not hold. The analysed APK is the authority -- it is the build the
        # detector read -- and the Gradle cache only stands in for it. Neither
        # carries "repository_" trust: it can ground what the detector saw,
        # never a cause in this project's source or a file a patch may edit.
        apk_evidence = error.get("apk_evidence") or {}
        cached_bytecode = (dependency or {}).get("external_target_bytecode") or {}
        for bytecode, kind, trust in (
            (apk_evidence, "analysed_apk_bytecode", "analysed_apk_bytecode"),
            (cached_bytecode, "external_target_bytecode", "cached_artifact_bytecode"),
        ):
            if not bytecode.get("methods"):
                continue
            payload = {
                key: bytecode.get(key)
                for key in (
                    "source", "apk", "dex", "class_descriptor", "artifact",
                    "requested_version", "requested_version_confirmed",
                    "cached_version", "version_exact", "binary", "class_entry",
                    "tool", "methods", "bound",
                ) if bytecode.get(key) not in (None, "", [], {})
            }
            origins = bytecode_argument_origins(bytecode.get("methods"), specification)
            if origins:
                payload["governed_call_argument_origins"] = origins
            candidates.append(_EvidenceItem("BYTE", kind, trust, payload, True, 99))
            break
        if apk_evidence.get("library_versions"):
            candidates.append(_EvidenceItem(
                "APK", "analysed_apk_library_versions", "analysed_apk_metadata",
                {
                    "apk": apk_evidence.get("apk", ""),
                    "library_versions": apk_evidence["library_versions"],
                    "bound": (
                        "The META-INF/<group>_<artifact>.version files the "
                        "analysed APK ships, which record the version the "
                        "build resolved. A library that writes no such file "
                        "is absent, which says nothing about whether it is "
                        "packaged."
                    ),
                }, False, 94,
            ))

        candidates.sort(key=lambda item: (
            not item.mandatory, -item.priority,
            str(item.payload.get("source_file_path", item.payload.get("path", ""))),
            canonical_hash(item.payload),
        ))
        selected: list[_EvidenceItem] = []
        excluded: list[dict] = []
        used = 0
        for item in candidates:
            size = self._size(item.payload)
            if item.mandatory or used + size <= self.repository_context_budget:
                selected.append(item)
                used += size
            else:
                excluded.append({
                    "kind": item.kind,
                    "source_file_path": item.payload.get(
                        "source_file_path", item.payload.get("path", "")
                    ),
                    "reason": "rank_below_repository_context_budget",
                    "characters": size,
                    "priority": item.priority,
                })

        # Raw lexical structures are never sent when semantic resolution is
        # available. Record their exclusion rather than silently dropping them.
        # direct_callers is only excluded when resolution actually replaced it
        # (see the lexical fallback above).
        superseded = [
            "shared_symbol_references", "type_references",
            "same_name_declarations", "ranked_candidate_files",
        ]
        if callers_resolved:
            superseded.insert(0, "direct_callers")
        for field in superseded:
            value = impact.get(field)
            if value:
                excluded.append({
                    "kind": field,
                    "source_file_path": "",
                    "reason": "superseded_by_semantic_resolution_or_discovery_only",
                    "characters": self._size(value),
                    "priority": 0,
                })
        for field in (
            "repository_call_sites", "consumer_evidence",
            "transitive_consumer_evidence", "value_usage_graph",
        ):
            value = context.get(field)
            if value:
                excluded.append({
                    "kind": field,
                    "source_file_path": "",
                    "reason": "not_selected_for_minimal_packet",
                    "characters": self._size(value),
                    "priority": 0,
                })

        counts: dict[str, int] = {}
        catalog = []
        for item in selected:
            counts[item.prefix] = counts.get(item.prefix, 0) + 1
            evidence_id = f"{item.prefix}-{counts[item.prefix]:03d}"
            catalog.append({
                "evidence_id": evidence_id,
                "kind": item.kind,
                "trust_level": item.trust_level,
                "mandatory": item.mandatory,
                "sha256": canonical_hash(item.payload),
                "data": item.payload,
            })

        sizes = enforced_input_size_evidence(
            catalog, error.get("method_name", ""),
            str(error.get("method_body", "")),
        )
        if sizes:
            catalog.append(sizes)
            used += self._size(sizes["data"])

        mutations = shared_state_mutation_evidence(
            catalog, str(error.get("method_body", "")),
        )
        if mutations:
            catalog.append(mutations)
            used += self._size(mutations["data"])

        flow = security_sink_evidence(catalog, error.get("method_name", ""))
        if flow:
            catalog.append(flow)
            # It is derived from evidence already counted, but it is still
            # characters the model reads, and selection_metrics is what the
            # budget is judged against. Leaving it out would make the packet
            # under-report its own size.
            used += self._size(flow["data"])

        scope = impact.get("scope_completeness", {}) or {}
        parse_errors = semantic.get("parse_error_files", []) or []
        completeness = {
            "target_source": target_source_completeness(catalog),
            # "complete" used to mean only that the index ran, which is not
            # the same as its having resolved anything. photok's packet
            # reported resolved_callers "complete" while the index resolved
            # zero callers and left twenty-one `create` call sites ambiguous:
            # Kotlin receivers carry no type here, so every call site listed
            # all seven declarations of the name as candidates. The
            # adjudication reads this label, and so did the analysis that
            # went looking for why the finding abstained.
            "resolved_callers": (
                "unknown" if semantic.get("status") != "available" else
                "partial" if (
                    parse_errors
                    or (semantic.get("ambiguous_calls")
                        and not semantic.get("resolved_callers"))
                ) else "complete"
            ),
            "ambiguous_calls": (
                "partial" if any(item["kind"] == "ambiguous_relation" for item in excluded)
                else "complete" if semantic.get("status") == "available" else "unknown"
            ),
            "tests": "complete" if tests else "not_found_in_examined_scope",
            "build_config": "complete" if any(build_payload.values()) else "unknown",
            "external_contract": "unknown",
            "overall": scope.get("status", "unknown"),
            "limitations": [*scope.get("reasons", []), *(
                [f"semantic parse errors in {len(parse_errors)} file(s)"]
                if parse_errors else []
            )],
        }
        if stored_contract.get("entries") or stored_contract.get("status") == "partial":
            available = {item["data"].get("source_file_path") for item in catalog
                         if item.get("trust_level") == "repository_exact_source"}
            missing_stored = sorted(set(stored_contract.get("required_paths", [])) - available)
            completeness["stored_value_consumers"] = (
                "complete" if stored_contract.get("status") == "complete" and not missing_stored
                else "partial")
            if completeness["stored_value_consumers"] != "complete":
                completeness["overall"] = "incomplete"
                completeness["limitations"].extend(stored_contract.get("limitations", []))
                completeness["limitations"].extend("missing stored-value source: " + p for p in missing_stored)
        elif stored_contract.get("status") == "not_modelled":
            # A value persisted only outside preferences (Syncthing-lite's
            # key.pem, 2026-10-29) is not an unknown scope: the sinks and the
            # statements naming the same location are in the STORE evidence.
            # Kept out of limitations, which read as an incomplete scope.
            completeness["stored_value_consumers"] = "not_modelled"
            completeness["persisted_outside_preferences"] = [
                f"{item.get('path', '')}:{item.get('line', 0)} {item.get('statement', '')}"[:300]
                for item in stored_contract.get("unmodelled_sinks", [])]
        family = selected_policy.get("family", "generic")
        finding = {
            "finding_id": error.get("unique_error_id", ""),
            "specification": error.get("spec", ""),
            "spec_family": family,
            "error_message": error.get("error_message", ""),
            "target": {
                "class": error.get("csv_class", ""),
                "method": error.get("method_name", ""),
                "source_file_path": target_path,
            },
        }
        # Other findings of this run in the same class (generate_error_fixes.
        # annotate_co_reported_findings): a fix must not leave them behind in
        # the code it rewrites, nor conflict with theirs.
        if error.get("co_reported_findings"):
            finding["co_reported_findings"] = error["co_reported_findings"]
        # Findings of the same call sequence (generate_error_fixes.
        # join_findings_of_one_sequence): this analysis resolves them too.
        if error.get("joint_findings"):
            finding["joint_findings"] = [
                {key: value for key, value in item.items() if key != "unique_error_id"}
                for item in error["joint_findings"]
            ]
        # This finding repeats its producer's report, and that producer is
        # one of the joint findings (join_findings_of_one_sequence).
        if error.get("echo_of"):
            finding["echo_of"] = error["echo_of"]
        vendored =vendored_source_assessment(
            target_path,
            str(exact_by_path.get(target_path, {}).get("content", ""))
            or str(error.get("method_body", "")),
        )
        if vendored:
            finding["vendored_source"] = vendored
        resolution = error.get("class_resolution") or {}
        if resolution.get("method") == "simple_name_fallback":
            finding["class_resolution"] = resolution
        if resolution.get("method") == "app_caller_attribution":
            # The reported frame is library code implementing the governed
            # API; the reviewed method is the application call that passed it
            # the reported value.
            finding["app_caller_attribution"] = resolution.get(
                "app_caller_attribution", {}
            )
        # The detector names the violated predicate even when it renders no
        # message. Surfacing it stops 71% of the sampled findings from
        # reaching the analysis as "unknown" with nothing else to go on.
        violation = reported_violation(error)
        if violation:
            finding["reported_violation"] = violation
            section = violation_rule_section(violation)
            if section:
                finding["reported_violation_section"] = section
        diagnostic_payload = {
            key: value for key, value in {
                "finding_id": error.get("unique_error_id", ""),
                "tool": error.get("tool", ""),
                "specification": error.get("spec", ""),
                "message": error.get("error_message", ""),
                "reported_class": error.get("csv_class", ""),
                "reported_method": error.get("method_name", ""),
                "source_file_path": target_path,
                # What the detector recorded beyond its message (report code,
                # label and the label's documented meaning, value seen,
                # observations across runs). It tells the analysis what the
                # report claims and what would decide it; like the rest of
                # DET-001 it is not evidence and cannot be cited.
                "detector_context": error.get("detector_context") or {},
            }.items() if value not in (None, "", {})
        }
        diagnostic_evidence = {
            "evidence_id": "DET-001",
            "kind": "detector_diagnostic",
            "trust_level": "reported_detector_output",
            "informational_only": True,
            "decision_authority": "none",
            "sha256": canonical_hash(diagnostic_payload),
            "data": diagnostic_payload,
        }
        packet = {
            "packet_version": CONTEXT_BUILDER_VERSION,
            "finding": finding,
            "selected_policy": selected_policy,
            "case_policy": error.get("case_migration_policy", {}),
            # What the parameter gate will require of the draft, stated so the
            # plan does not keep a value the gate refuses (Your Local Weather
            # run_v2, 2026-11-02).
            "parameter_policies": parameter_policies(error),
            "specification_context": specification,
            # Whether the specification this finding is adjudicated against
            # has a verified source and a real governed API type.
            # SQLInjectionSpec had neither -- a source_url that answered 404
            # and an invented type -- and entered the pipeline silently on
            # the generator's "accept the final attempt anyway" path. An
            # adjudication resting on an unanchored specification is a
            # weaker result than one resting on a verified rule, and the
            # record should say which it was.
            "specification_anchoring": {
                "externally_anchored": specification_is_externally_anchored(
                    specification
                ),
                "source_verified": specification.get("source_verified"),
                "source_url": specification.get("source_url", ""),
                "governed_api_types": governed_api_types(specification),
            },
            # Ownership, rule applicability and the caller contract, computed
            # from the checkout so the adjudicator does not have to ask for
            # them and be declined (see derived_facts).
            "derived_facts": self._with_catalog_callers(
                self.derived_facts(error, specification, target_path),
                catalog, error, target_path,
            ),
            # Kept outside evidence_catalog on purpose: DET-001 improves
            # provenance and reporting, but cannot yet support a confirmed
            # model claim or change adjudication/generation decisions.
            "diagnostic_evidence": diagnostic_evidence,
            "evidence_catalog": catalog,
            "external_evidence_catalog": [],
            "scope_completeness": completeness,
            "excluded_evidence": excluded,
            "selection_metrics": {
                "budget_characters": self.repository_context_budget,
                "selected_characters": used,
                "budget_exceeded_by_mandatory_evidence": max(
                    0, used - self.repository_context_budget
                ),
                "selected_items": len(catalog),
                "informational_items": 1,
                "excluded_items": len(excluded),
            },
            "versions": {
                "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "policy_version": selected_policy.get(
                    "registry_version", POLICY_VERSION
                ),
                "spec_version": str(
                    error.get("specification_context", {}).get(
                        "schema_version", "legacy-v1"
                    )
                ),
                "context_builder_version": CONTEXT_BUILDER_VERSION,
                "semantic_resolver_version": SEMANTIC_RESOLVER_VERSION,
            },
        }
        packet["input_hashes"] = {
            "finding_sha256": canonical_hash(finding),
            "diagnostic_evidence_sha256": canonical_hash(diagnostic_evidence),
            "policy_sha256": canonical_hash(selected_policy),
            "evidence_sha256": canonical_hash(catalog),
            "packet_sha256": canonical_hash(packet),
        }
        return packet
