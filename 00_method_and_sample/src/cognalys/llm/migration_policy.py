#!/usr/bin/env python3
"""
Case-scoped migration policy and evidence promotion.

Extracted from generate_error_fixes.py (Passo 4 of the
modularization plan). These derive the compatibility policy that governs a
finding and promote specific, well-evidenced situations (validation-repair
sources, policy-authorized TLS review) into a stronger posture than the raw
adjudication alone would justify.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional


# A policy file used to answer only to an exact finding id -- the whole
# `class:::method:::spec:::violation:::message` string -- so one authorisation
# covered one finding in one project, and the file held a single entry. The
# adjudication asks for exactly this ("No case-policy authorization was
# supplied for a potentially compatibility-affecting change"), and writing one
# entry per finding is what made answering it impractical: the 33 application
# findings of the priority sample fall into 11 shapes, and the five largest
# cover 27 of them.
#
# So a policy may now also be written against a family: a specification, a
# reported violation, and optionally a message pattern. An exact finding id
# still wins over a family, and a family still wins over the inferred
# defaults, so nothing already written changes meaning.
def _family_matches(family: Dict, specification: str, violation: str,
                    message: str) -> bool:
    """Whether a family entry describes this finding."""
    wanted_spec = str(family.get("spec", "")).strip()
    if wanted_spec and wanted_spec != specification:
        return False
    wanted_violation = str(family.get("violation", "")).strip()
    if wanted_violation and wanted_violation != violation:
        return False
    pattern = str(family.get("message_pattern", "")).strip()
    if pattern:
        try:
            if not re.search(pattern, message, re.IGNORECASE):
                return False
        except re.error:
            return False
    # A family naming nothing at all would authorise everything.
    return bool(wanted_spec or wanted_violation or pattern)


def resolve_case_policy(policies: Dict, error: Dict) -> Dict:
    """The authorisation that applies to this finding, most specific first.

    Accepts both shapes the file can take: the legacy flat map of finding id
    to policy, and a structured object with "findings" and "families". The
    legacy shape is read exactly as before.
    """
    policies = policies or {}
    if "findings" in policies or "families" in policies:
        by_finding = policies.get("findings") or {}
        families = policies.get("families") or []
    else:
        by_finding = policies
        families = []
    finding_id = str(error.get("unique_error_id", ""))
    exact = by_finding.get(finding_id) or {}
    parts = finding_id.split(":::")
    specification = str(error.get("spec", "")) or (
        parts[2] if len(parts) > 3 else ""
    )
    violation = parts[3] if len(parts) > 4 else ""
    message = str(error.get("error_message", ""))
    matched_family: Dict = {}
    for family in families:
        if not isinstance(family, dict):
            continue
        if _family_matches(family, specification, violation, message):
            matched_family = family.get("policy") or {}
            break
    return {**matched_family, **exact}


def generic_migration_policy(error: Dict) -> Dict:
    """Derive reusable compatibility policy from a concrete finding shape.

    The policy is the finding's specification's own remediation entry
    (remediation_policies in data/specifications/<spec>.json) whose
    condition the finding meets, with the variants its evidence selects. It
    used to be written here per specification name -- the TLS negotiation
    recipe under SSLContextSpec, the in-process digest migration under
    MessageDigestSpec -- and moved to the data verbatim (2026-10-17 audit).
    A specification without an entry gets no inferred policy.
    """
    from cognalys.utils.specification_data import remediation_entry, resolved_policy

    entry = remediation_entry(error)
    if not entry:
        return {}
    # Evidence an entry asks for, computed from the reviewed code (and, for
    # TLS trust, from the hosts it connects to), recorded in the policy under
    # its own key. Files the evidence supplies -- a root certificate to bundle
    # -- become provided_files, which the pipeline writes itself.
    # An entry may ask for several (MessageDigestSpec: the value fallback and
    # the expected digests a download is checked against); each is recorded
    # under its own key, and the variants see them together.
    names = entry.get("evidence") or []
    names = [names] if isinstance(names, str) else list(names)
    collected = []
    for name in names:
        collector, recorded_as = _EVIDENCE.get(str(name), (None, ""))
        found = collector(error) if collector else {}
        if found:
            collected.append((recorded_as, found))
    evidence: Dict = {}
    facts: Dict = {}
    conditions: Dict = {}
    for _, found in collected:
        evidence.update(found)
        facts.update(found.get("maintainer_facts") or {})
        conditions.update(found.get("fact_conditions") or {})
    policy = resolved_policy(entry, evidence)
    for recorded_as, found in collected:
        policy[recorded_as] = {key: value for key, value in found.items()
                               if key not in {"provided_files", "maintainer_facts",
                                              "fact_conditions"}}
    if evidence.get("provided_files") and policy.get("uses_provided_files"):
        policy["provided_files"] = evidence["provided_files"]
    # Values the evidence established for the patch to use verbatim (the
    # SHA-256 of a downloaded file that replaces its expected MD5).
    if evidence.get("replacement_values") and policy.get("uses_replacement_values"):
        policy["replacement_values"] = evidence["replacement_values"]
    # Sentences the evidence wrote for the maintainer (the hosts checked, why
    # a root is bundled), stated in the pull request only when the resolved
    # policy names them: a fact about a change that a variant did not select
    # would describe a patch that was not made.
    stated = [str(facts[name]) for name in policy.get("states_facts") or []
              if facts.get(name)]
    if stated:
        policy["maintainer_facts"] = stated
    # A fact that holds only for a patch that does what it says (protocols
    # removed at the socket) carries the predicate that checks the final diff.
    conditioned = {str(facts[name]): conditions[name]
                   for name in policy.get("states_facts") or []
                   if facts.get(name) and conditions.get(name)}
    if conditioned:
        policy["maintainer_fact_conditions"] = conditioned
    return policy


def _diff_removes_deprecated_protocols(edits) -> bool:
    from cognalys.extraction.tls_client_evidence import diff_removes_deprecated_protocols

    return diff_removes_deprecated_protocols(edits)


# Predicates a maintainer fact may depend on, by the name its evidence
# collector records in fact_conditions; each reads the final edits.
_FACT_CONDITIONS = {
    "diff_removes_deprecated_protocols": _diff_removes_deprecated_protocols,
}


def facts_supported_by_diff(policy: Dict, edits) -> List[str]:
    """The policy's maintainer facts that the final diff bears out.

    Fread (tls_20261015) said "SSL 3.0, TLS 1.0 and TLS 1.1 are removed from
    the protocols each socket enables" about a patch that only changed the
    selector. A fact recorded with a condition is stated only when its
    predicate holds for the edits; every other fact is stated as written. A
    policy stored before conditions were recorded is matched by the
    sentences the evidence modules write.
    """
    from cognalys.extraction.tls_client_evidence import FACT_CONDITIONS

    conditions = {**FACT_CONDITIONS, **((policy or {}).get("maintainer_fact_conditions") or {})}
    stated = []
    for item in (policy or {}).get("maintainer_facts") or []:
        text = str(item).strip()
        if not text:
            continue
        predicate = _FACT_CONDITIONS.get(str(conditions.get(text, "")))
        if predicate is not None and not predicate(edits or []):
            continue
        stated.append(text)
    return stated


def _tls_trust_evidence(error: Dict) -> Dict:
    """The client the TLS context serves, then what its trust overrides accept."""
    from cognalys.extraction.tls_client_evidence import tls_client_evidence
    from cognalys.extraction.tls_trust_evidence import tls_trust_evidence

    client = tls_client_evidence(error)
    trust = tls_trust_evidence(error)
    merged = {**client, **trust}
    facts = {**(client.get("maintainer_facts") or {}), **(trust.get("maintainer_facts") or {})}
    if facts:
        merged["maintainer_facts"] = facts
    conditions = {**(client.get("fact_conditions") or {}), **(trust.get("fact_conditions") or {})}
    if conditions:
        merged["fact_conditions"] = conditions
    return merged


def _digest_reference_evidence(error: Dict) -> Dict:
    from cognalys.extraction.digest_reference_evidence import digest_reference_evidence

    return digest_reference_evidence(error)


def _digest_file_name_evidence(error: Dict) -> Dict:
    from cognalys.extraction.digest_file_name_evidence import digest_file_name_evidence

    return digest_file_name_evidence(error)


# Evidence collectors a specification's remediation entry may name, and the
# policy key each is recorded under.
def _digest_store_key_evidence(error: Dict) -> Dict:
    from cognalys.extraction.digest_store_key_evidence import digest_store_key_evidence

    return digest_store_key_evidence(error)


_EVIDENCE = {
    "digest_store_key": (_digest_store_key_evidence, "established_digest_store_key_evidence"),
    "value_fallback": (lambda error: nondigest_fallback_evidence(error),
                       "established_value_contract_evidence"),
    "tls_trust": (_tls_trust_evidence, "established_tls_trust_evidence"),
    "digest_reference": (_digest_reference_evidence, "established_digest_reference_evidence"),
    "digest_file_name": (_digest_file_name_evidence, "established_digest_file_name_evidence"),
}


def promote_validation_repair_sources(error: Dict) -> None:
    """Add compiler-diagnosed legacy sources and referenced APIs to exact context."""
    feedback = error.get("previous_validation_feedback", {}) or {}
    if feedback.get("failure_attribution") not in {
        "preexisting_test_or_source_debt", "verified_preexisting_test_debt",
    }:
        return
    repo_root = Path(str(error.get("repository_path", ""))).resolve()
    context = error.get("expanded_repository_context", {})
    impact = context.get("multi_file_impact", {}) if isinstance(context, dict) else {}
    if not repo_root.is_dir() or not isinstance(impact, dict):
        return
    exact = impact.setdefault("exact_candidate_sources", [])
    existing = {
        str(item.get("source_file_path", "")) for item in exact
        if isinstance(item, dict)
    }
    repair_paths = []
    reference_types = set()
    for diagnostic in feedback.get("compiler_diagnostics", []):
        raw_path = Path(str(diagnostic.get("path", "")).replace("\\", "/"))
        parts = raw_path.parts
        relative = ""
        for index in range(len(parts)):
            candidate_relative = Path(*parts[index:]).as_posix()
            if (repo_root / candidate_relative).is_file():
                relative = candidate_relative
                break
        if not relative:
            continue
        repair_paths.append(relative)
        reference_types.update(re.findall(
            r"\b([A-Z][A-Za-z0-9_$]*)\s*\.",
            str(diagnostic.get("source_line", "")),
        ))
    supporting = []
    if reference_types:
        for candidate in repo_root.rglob("*"):
            if candidate.suffix.lower() not in {".java", ".kt"} or not candidate.is_file():
                continue
            if any(part in {".git", ".gradle", "build"} for part in candidate.parts):
                continue
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(re.search(
                rf"\b(?:class|object|interface)\s+{re.escape(name)}\b", content
            ) for name in reference_types):
                supporting.append(candidate.relative_to(repo_root).as_posix())
    promoted = []
    for relative in dict.fromkeys([*repair_paths, *supporting]):
        if relative in existing:
            promoted.append(relative)
            continue
        candidate = repo_root / relative
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(content) > 80_000:
            continue
        exact.append({
            "source_file_path": relative,
            "relevance": [
                "compiler-diagnosed validation repair source"
                if relative in repair_paths else
                "declaration referenced by compiler-diagnosed source"
            ],
            "content": content,
        })
        existing.add(relative)
        promoted.append(relative)
    impact["validation_repair_sources"] = list(dict.fromkeys(repair_paths))
    impact["validation_repair_supporting_sources"] = list(dict.fromkeys(
        path for path in promoted if path not in repair_paths
    ))


_HASHCODE_OVERRIDE_PATTERN = re.compile(
    r"\bpublic\s+int\s+hashCode\s*\(\s*\)", re.IGNORECASE
)


def nondigest_fallback_evidence(error: Dict) -> Dict:
    """Detect a fallback path that already returns a non-digest-derived value.

    The adjudicator's standing objection to a digest migration is that the
    consumer inventory is unresolved, so some caller might depend on the exact
    value. A method that already falls back to a value computed a completely
    different way answers that from its own source: the callers must
    tolerate a different value, because this code can already produce one.

    net.gaast.giggity's Schedule.Item.hashCode is the motivating case -- its
    catch returns super.hashCode(), the identity hash, which differs on every
    process run. That is deterministic evidence sitting inside the method
    under repair, and the adjudication blocked on "value_contract_unresolved"
    without it.
    """
    body = str(error.get("method_body", ""))
    if not body:
        return {}
    if "catch" not in body:
        return {}

    fallbacks = []
    # Returns inside a catch block, i.e. after the first catch keyword.
    tail = body[body.index("catch"):]
    for match in re.finditer(r"\breturn\s+([^;]+);", tail):
        expression = match.group(1).strip()
        if re.search(r"\b(?:digest|md5|sha)\w*\b", expression, re.IGNORECASE):
            continue  # still derived from the digest; proves nothing
        fallbacks.append(expression)
    if not fallbacks:
        return {}

    is_hashcode_override = bool(_HASHCODE_OVERRIDE_PATTERN.search(body))
    identity_hash = any(
        re.search(r"\bsuper\s*\.\s*hashCode\s*\(\s*\)", item) for item in fallbacks
    )
    return {
        "observed": True,
        "fallback_expressions": fallbacks,
        "returns_identity_hash": identity_hash,
        "is_hashcode_override": is_hashcode_override,
        "established_fact": (
            "The reviewed method already has a path that returns a value not "
            "derived from the digest ("
            + "; ".join(fallbacks)
            + "), so its callers must already tolerate a different value. An "
            "unresolved consumer inventory is therefore not evidence that an "
            "exact digest-derived value contract exists."
            + (
                " That fallback is Object.hashCode(), an identity hash that "
                "differs on every process run, so no caller can be relying on "
                "a value that is stable across runs."
                if identity_hash else ""
            )
        ),
    }


def _test_relevant_to_target(
    exact: Dict, candidate_tests: List[str], target: str, error: Dict,
) -> str:
    """The existing test file that actually covers the target, or "".

    Relevance has to be established, not assumed: an arbitrary test file is a
    valid Kotlin/Java file and will happily compile new assertions, so nothing
    downstream catches a nonsensical placement. A test qualifies when its name
    derives from the target's file name, or when its own source already
    references the target type or the reported method.
    """
    target_stem = Path(target).stem if target else ""
    method = str(error.get("method_name", "")).strip()
    if not target_stem:
        return ""

    for path in candidate_tests:
        stem = Path(path).stem
        # FooTest.kt / FooKtTest.kt / FooTlsProtocolTest.kt for Foo.kt
        if stem.lower().startswith(target_stem.lower()):
            return path

    for path in candidate_tests:
        item = exact.get(path)
        content = str(item.get("content", "")) if isinstance(item, dict) else ""
        if not content:
            continue
        if re.search(rf"\b{re.escape(target_stem)}\b", content):
            return path
        if method and re.search(rf"\b{re.escape(method)}\s*\(", content):
            return path
    return ""


def promote_policy_authorized_tls_review(error: Dict, analysis: Dict) -> Dict:
    """Promote a complete generic-TLS case to reviewable generation.

    This does not claim that a literal-only analyzer will stop reporting
    SSLContext("TLS"). It authorizes generation of the separately stated
    effective-transport remediation when implementation source is exact and a
    test target is either exact or can be created in an established test set.
    """
    policy = error.get("case_migration_policy", {})
    if (policy.get("consumer_scope")
            != "signature_preserving_internal_transport_change"):
        return analysis
    if str(analysis.get("decision", "")).upper() not in {
        "NO_SAFE_FIX", "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW",
    }:
        return analysis
    impact = error.get("expanded_repository_context", {}).get(
        "multi_file_impact", {}
    )
    if impact.get("scope_completeness", {}).get("status") != "complete":
        return analysis
    exact = {
        str(item.get("source_file_path", "")): item
        for item in impact.get("exact_candidate_sources", [])
        if isinstance(item, dict)
    }
    target = str(impact.get("target", {}).get("source_file_path", ""))
    candidate_tests = [
        path for path in sorted(exact)
        if re.search(r"(?:^|/)(?:androidTest|test)/|Test\.(?:java|kt)$", path)
    ]
    # Only an existing test that demonstrably covers the target may host the
    # TLS tests. Taking the first test file in the exact-candidate set put TLS
    # protocol assertions inside DateFormattingTest.kt on spacecowboy/Feeder
    # (PR #1210): a date-formatting test is not a place to prove socket
    # protocol filtering, and appending there also hides the new coverage from
    # anyone looking for it.
    relevant_test = _test_relevant_to_target(exact, candidate_tests, target, error)
    test_paths = [relevant_test] if relevant_test else []
    create_test = False
    if not test_paths:
        known_tests = error.get("expanded_repository_context", {}).get("tests", [])
        established = next((
            str(item.get("path", "")) for item in known_tests
            if isinstance(item, dict) and re.search(
                r"(?:^|/)src/(?:test|androidTest)/(?:java|kotlin)/",
                str(item.get("path", "")), re.IGNORECASE,
            )
        ), "")
        if not established:
            # An unrelated existing test still proves where this repository
            # keeps its tests, even though it must not host these assertions.
            established = next((
                path for path in candidate_tests
                if re.search(r"(?:^|/)src/(?:test|androidTest)/(?:java|kotlin)/",
                             path, re.IGNORECASE)
            ), "")
        target_match = re.match(
            r"^(.*?/src/main/)(?:java|kotlin)/(.*?)([^/]+)\.(java|kt)$", target
        )
        test_match = re.match(
            r"^(.*?/src/(?:test|androidTest)/(?:java|kotlin)/)", established,
            re.IGNORECASE,
        )
        if target_match and test_match:
            package_dir = target_match.group(2)
            class_name = target_match.group(3)
            extension = Path(established).suffix or f".{target_match.group(4)}"
            test_paths = [
                f"{test_match.group(1)}{package_dir}{class_name}TlsProtocolTest{extension}"
            ]
            create_test = True
    if not target or target not in exact or not test_paths:
        return analysis
    test_path = test_paths[0]
    promoted = dict(analysis)
    promoted["confirmed_cause"] = (
        "The application passes the generic literal \"TLS\" to "
        "SSLContext.getInstance, while the reported SSLContextSpec predicate "
        "accepts only TLSv1.2 or TLSv1.3. The literal mismatch is confirmed; "
        "the effective negotiated protocol remains unproven."
    )
    predicates = [dict(item) for item in analysis.get(
        "predicate_assessment", []) if isinstance(item, dict)
    ]
    literal_found = False
    for item in predicates:
        if item.get("event") == "detector_literal_allowlist":
            item["status"] = "violated"
            literal_found = True
    if not literal_found:
        predicates.append({
            "event": "detector_literal_allowlist", "status": "violated",
            "evidence": (
                "The exact source contains SSLContext.getInstance(\"TLS\") "
                "and the concrete finding accepts only TLSv1.2 or TLSv1.3."
            ),
        })
    promoted["predicate_assessment"] = predicates
    promoted["forward_compatibility_assessment"] = {
        "status": "preserved",
        "dimensions_checked": [
            "provider defaults", "deprecated protocols", "future TLS versions",
            "API 21", "endpoint interoperability",
        ],
        "evidence": (
            "The authorized strategy starts with the provider-enabled set, "
            "removes only named legacy protocols, preserves unknown future TLS "
            "names, and does not enable provider-disabled protocols."
        ),
        "strategy": str(policy.get("strategy", "")),
    }
    migration = dict(promoted.get("migration_assessment", {}))
    migration.update({
        "usage_kind": "protocol", "legacy_compatibility_required": False,
        "safe_strategy": str(policy.get("strategy", "")),
        "policy_authorized": True,
    })
    promoted["migration_assessment"] = migration
    promoted["decision"] = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
    promoted["coordinated_fix_plan"] = {
        "affected_symbols": [
            str(error.get("csv_class", "")) + "."
            + str(error.get("method_name", "")),
            "effective TLS protocol filter",
        ],
        "required_edits": [
            {
                "source_file_path": target,
                "symbol": str(error.get("method_name", "")),
                "reason": (
                    "Enforce legacy-protocol removal at the effective transport "
                    "boundary without pinning SSLContext to a version."
                ),
                "create_new_file": False,
            },
            {
                "source_file_path": test_path,
                "symbol": "TLS protocol filtering tests",
                "reason": "Prove legacy removal, future retention, and fail-closed behavior.",
                "create_new_file": create_test,
            },
        ],
        "required_tests": list(policy.get("required_tests", [])),
        "compatibility_policy_required": False,
        "scope_evidence": [
            "The target implementation is exact and the test uses an established repository source set.",
            "The internal transport change preserves the Builder.build signature and return type.",
        ],
    }
    repair_sources = [
        str(path) for path in impact.get("validation_repair_sources", [])
        if str(path) and str(path) not in {target, test_path}
    ]
    for path in repair_sources:
        promoted["coordinated_fix_plan"]["required_edits"].append({
            "source_file_path": path,
            "symbol": "compiler-diagnosed legacy test API usages",
            "reason": (
                "Migrate baseline-confirmed stale test calls to current repository "
                "APIs so the complete changed test source set compiles."
            ),
            "create_new_file": False,
        })
    unresolved = [
        dict(item) for item in analysis.get("scope_assessment", {}).get(
            "unresolved_relations", []) if isinstance(item, dict)
        and item.get("kind") in {
            "external_protocol", "deployed_client_compatibility",
        }
    ]
    for item in unresolved:
        item["blocking_for_generation"] = False
        item["blocking_for_publication"] = True
    promoted["scope_assessment"] = {
        "status": "complete", "local_code_scope": "complete",
        "external_contract_scope": "unresolved",
        "multi_file_change_required": True,
        "reviewed_files": [
            {
                "source_file_path": target, "action": "edit",
                "symbols": [str(error.get("method_name", ""))],
                "evidence": "Exact implementation source is supplied.",
            },
            {
                "source_file_path": test_path, "action": "required_test",
                "symbols": ["TLS protocol filtering tests"],
                "evidence": (
                    "A dedicated test is created in an established repository source set."
                    if create_test else "Exact existing test source is supplied."
                ),
            },
        ],
        "unresolved_relations": unresolved,
    }
    for path in repair_sources:
        promoted["scope_assessment"]["reviewed_files"].append({
            "source_file_path": path, "action": "edit",
            "symbols": ["compiler-diagnosed stale test calls"],
            "evidence": (
                "The validation feedback reproduces this compiler failure in the "
                "base commit; exact source and referenced declarations are supplied."
            ),
        })
    promoted["issue_candidate"] = {
        "eligible": False, "title": "", "finding_summary": "",
        "why_no_pr": "", "maintainer_question": "",
        "proposed_next_step": "",
    }
    return promoted


_NON_PROMOTABLE_BLOCKER_KINDS = frozenset({
    "deterministic_validation", "invalid_patch_plan",
})


def promote_grounded_api_migration_review(
    error: Dict, adjudication: Dict, research: Optional[Dict] = None,
    family: str = "",
) -> Optional[Dict]:
    """Turn a confirmed, grounded API migration from abstention into a review PR.

    In run_20260913_004434 all three deprecated-API findings were confirmed
    with complete scope and abstained on the same blockers: no replacement
    selected, no behavioural contract, and "the pipeline cannot run lifecycle
    behaviour on a device". The first two are answered once the repository's
    own replacement idiom (REPO) or an official source is present and the
    uses of the deprecated API are enumerated (CONTRACT). The third is true of
    every migration and is exactly what a human reviewer is for; as a
    generation blocker it made every such finding unfixable.

    So, when the finding is a confirmed api_migration with complete scope, a
    replacement is grounded, and the uses span at least two production files,
    the generation blockers move to publication, a runtime-review blocker is
    added, and the decision becomes MULTI_FILE_FIX_PROPOSED_FOR_REVIEW: the
    patch is generated and built, and never published without review. A
    deterministic validation failure is never promoted, and a single-file use
    is left as it was, because the review-only decision requires two paths.

    Mutates `adjudication` and returns what was done, or None.
    """
    finding = error.get("dependency_finding") or {}
    if family != "api_migration" and finding.get("kind") != "api_migration":
        return None
    if (str(adjudication.get("decision", "")).upper() != "NO_SAFE_FIX"
            or adjudication.get("generation_status") != "blocked"):
        return None
    if (adjudication.get("cause_status") != "confirmed"
            or adjudication.get("detector_violation") != "confirmed"
            or adjudication.get("scope_status") != "complete"
            or not adjudication.get("decision_claim_ids")):
        return None
    blockers = [
        item for item in adjudication.get("blocking_for_generation") or []
        if isinstance(item, dict)
    ]
    if any(str(item.get("kind", "")) in _NON_PROMOTABLE_BLOCKER_KINDS for item in blockers):
        return None
    idioms = error.get("repository_replacement_idioms") or {}
    mechanisms = [
        mechanism
        for item in idioms.get("by_deprecated_type") or []
        for mechanism in item.get("mechanisms") or []
        if mechanism.get("production_files")
    ]
    # A recommendation that covers every requirement of the invariants is a
    # grounded replacement even when its mechanisms are not used in the
    # repository yet (a platform executor needs no dependency).
    recommended = [
        entry
        for item in idioms.get("by_deprecated_type") or []
        for recommendation in [item.get("recommendation") or {}]
        if recommendation.get("recommended")
        and not recommendation.get("uncovered_requirements")
        for entry in recommendation["recommended"]
    ]
    mechanisms = [*mechanisms, *(
        {"mechanism": entry.get("mechanism", ""), "production_files": entry.get("production_files", 0)}
        for entry in recommended
        if entry.get("mechanism") not in {item.get("mechanism") for item in mechanisms}
    )]
    external_facts = [
        fact for fact in (research or {}).get("externally_confirmed_facts") or [] if fact
    ]
    if not mechanisms and not external_facts:
        return None
    contract = error.get("deprecated_usage_contract") or {}
    files = sorted({
        str(path) for item in contract.get("contracts") or []
        for path in item.get("production_files") or []
    })
    if contract.get("status") != "found" or len(files) < 2:
        return None

    mechanism_names = [str(item.get("mechanism", "")) for item in mechanisms[:3]]
    claims = list(adjudication.get("decision_claim_ids") or [])
    adjudication["blocking_for_publication"] = [
        *(item for item in adjudication.get("blocking_for_publication") or []
          if isinstance(item, dict)),
        *(dict(item, discharged_by_pipeline_validation=False) for item in blockers),
        {
            "kind": "runtime_behaviour_requires_human_review",
            "description": (
                "A reviewer must confirm that the replacement keeps the callback "
                "threads, order, cancellation and delivery recorded in the "
                "deprecated_usage_contract evidence; a build, unit tests and the "
                "source recheck cannot show runtime behaviour."
            ),
            "supporting_claim_ids": claims,
            "discharged_by_pipeline_validation": False,
        },
    ]
    adjudication["blocking_for_generation"] = []
    adjudication["generation_status"] = "supported_for_review"
    adjudication["publication_status"] = "blocked"
    adjudication["decision"] = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
    adjudication["authorized_behavior_change"] = {
        "authorized": True,
        "description": (
            "Replace the deprecated API across its production uses, following "
            "the repository's existing idiom where one is recorded; review-only."
        ),
        "policy_reference": "api_migration: promote_grounded_api_migration_review",
    }
    record = {
        "reason": "confirmed api_migration with a grounded replacement and enumerated uses",
        "repository_mechanisms": mechanism_names,
        "external_facts": len(external_facts),
        "files": files,
        "moved_generation_blockers": [str(item.get("kind", "")) for item in blockers],
    }
    adjudication["review_assumptions"] = [
        *(adjudication.get("review_assumptions") or []),
        "Promoted to reviewable generation: "
        + (
            "the replacement follows " + ", ".join(mechanism_names)
            + " already used in this repository"
            if mechanism_names else "the replacement follows the cited official sources"
        )
        + f"; every production use in {', '.join(files)} must be migrated; "
        "runtime behaviour is left to human review.",
    ]
    adjudication["promotion"] = record
    return record


IN_MEMORY_USAGE_KINDS = frozenset({
    "", "unknown", "cache", "runtime_discriminator", "ephemeral_sensitive",
})


def ground_usage_in_value_consumers(error: Dict, analysis: Dict) -> Optional[Dict]:
    """Correct a usage classification the method's call sites contradict.

    QuantumBadger/RedReader closed PR #1373: the analysis called General.sha1's
    result a regenerable cache key, and its only caller builds a database file
    name from it. When derived_value_consumers shows the value naming stored
    data or leaving the process, an in-memory label is replaced by persistence
    or protocol, legacy compatibility becomes required, and a policy that only
    covers in-process recomputed values stops authorising the change (the
    decision becomes NO_SAFE_FIX). Returns the correction applied, or None.
    """
    consumers = error.get("derived_value_consumers") or {}
    migration = analysis.get("migration_assessment")
    if not (consumers.get("persisted") or consumers.get("transmitted")
            or consumers.get("fixed")):
        return None
    if not isinstance(migration, dict):
        return None
    claimed = str(migration.get("usage_kind", "")).strip().lower()
    if claimed not in IN_MEMORY_USAGE_KINDS:
        return None
    # A value compared with a constant fixed in the source (WiFiAnalyzer's
    # TYPE1..3) must keep matching it, as a protocol value must.
    grounded = "persistence" if consumers.get("persisted") else "protocol"
    evidence = next(
        (item for item in consumers.get("consumers", [])
         if item.get("kind") in {"persistence", "network", "fixed_value"}),
        {},
    )
    migration["usage_kind"] = grounded
    migration["legacy_compatibility_required"] = True
    migration["policy_authorized"] = False
    migration["usage_grounded_by"] = evidence
    policy = error.get("case_migration_policy") or {}
    voided = (
        isinstance(policy, dict)
        and policy.get("consumer_scope") == "internal_recomputed_digest_value"
        and str(analysis.get("decision", "")).upper() != "NO_SAFE_FIX"
    )
    if voided:
        # That policy authorises only a value recomputed and compared inside
        # the process. Changing a value that names stored data orphans the
        # data, so the patch it authorised would be wrong, not merely risky.
        analysis["decision"] = "NO_SAFE_FIX"
    return {
        "claimed": claimed or "unset",
        "grounded": grounded,
        "evidence": evidence,
        "policy_authorization_voided": voided,
    }


def promote_bounded_digest_discriminator_review(
    error: Dict, analysis: Dict,
) -> Dict:
    """Promote an internal, recomputed-in-process digest to reviewable generation.

    A bounded scope analysis cannot prove that no unexamined, reflective or
    external consumer of a digest value exists, so an adjudication that demands
    that proof abstains on every digest finding forever. This promotes the
    narrow case the evidence *can* settle: the value is classified as a cache or
    runtime discriminator, its expected value source is internal, and the
    comparison is between two values produced by the same code path. Because the
    stored and recomputed values both move with the algorithm, no value
    migration is required.

    The result is review-only. It never auto-applies, and it refuses to promote
    when the usage is persisted, external, or otherwise outside the policy's
    stated scope.
    """
    policy = error.get("case_migration_policy", {}) or {}
    if policy.get("consumer_scope") != "internal_recomputed_digest_value":
        return analysis
    if str(analysis.get("decision", "")).upper() != "NO_SAFE_FIX":
        return analysis
    consumers = error.get("derived_value_consumers") or {}
    if consumers.get("persisted") or consumers.get("transmitted"):
        # The call sites show the value names stored data or leaves the
        # process: it is not an internal recomputed value, whatever the label.
        return analysis
    if not str(analysis.get("confirmed_cause", "")).strip():
        # Without a confirmed cause there is nothing established to repair.
        return analysis

    migration = analysis.get("migration_assessment", {})
    digest = analysis.get("digest_contract_assessment", {})
    if not isinstance(migration, dict) or not isinstance(digest, dict):
        return analysis

    # A hashCode() override whose own catch returns Object.hashCode() settles
    # the usage and value-contract questions structurally, without needing the
    # consumer inventory the adjudicator keeps asking for: the method already
    # has a path that yields an identity hash, which differs on every process
    # run, so nothing can be depending on a stable digest-derived value. Both
    # halves are required. The Java contract alone ("not required to be the
    # same value across executions") is normative, and real code violates it
    # -- net.gaast.giggity's own comment says the value was used for
    # notification and alarm IDs -- so the fallback is what turns the argument
    # from "callers must not rely on this" into "this code already breaks it".
    fallback = policy.get("established_value_contract_evidence", {}) or {}
    structurally_settled = bool(
        fallback.get("is_hashcode_override")
        and fallback.get("returns_identity_hash")
    )

    if not structurally_settled:
        if str(migration.get("usage_kind", "")) not in {
            "cache", "runtime_discriminator",
        }:
            return analysis
        if str(digest.get("contract_kind", "")) != "internal":
            return analysis
    if digest.get("backend_change_required") is True:
        return analysis

    target = str(error.get("source_file_path", "")).strip()
    if not target:
        return analysis

    promoted = dict(analysis)
    promoted["decision"] = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"

    migration = dict(migration)
    migration.update({
        "legacy_compatibility_required": False,
        "safe_strategy": str(policy.get("strategy", "")),
        "policy_authorized": True,
    })
    if structurally_settled:
        # Record what the structure establishes rather than leaving the
        # model's weaker "identifier"/"unknown" in the published artifacts.
        migration["usage_kind"] = "runtime_discriminator"
    promoted["migration_assessment"] = migration

    digest = dict(digest)
    digest["client_only_change_safe"] = True
    if structurally_settled:
        digest["contract_kind"] = "internal"
    promoted["digest_contract_assessment"] = digest

    promoted["forward_compatibility_assessment"] = {
        "status": "preserved",
        "dimensions_checked": [
            "stored comparison value", "recomputed value", "method signature",
            "persisted representations", "external value consumers",
        ],
        "evidence": (
            "The compared values are both produced by the reviewed method in "
            "the same process, so replacing the algorithm moves them together. "
            "The examined scope identified no persisted, transmitted, or "
            "external consumer of the derived value."
        ),
        "strategy": str(policy.get("strategy", "")),
    }

    edits = [{
        "source_file_path": target,
        "symbol": str(error.get("method_name", "")),
        "reason": (
            "Replace the weak digest algorithm with a governed algorithm while "
            "preserving the method signature and value-derivation shape."
        ),
        "create_new_file": False,
    }]
    promoted["coordinated_fix_plan"] = {
        "affected_symbols": [
            ".".join(part for part in (
                str(error.get("csv_class", "")), str(error.get("method_name", "")),
            ) if part),
        ],
        "required_edits": edits,
        "required_tests": list(policy.get("required_tests", [])),
        "compatibility_policy_required": False,
        "scope_evidence": [
            "The digest usage is classified as an internal runtime "
            "discriminator or cache with an internal expected-value source.",
            "The stored and recomputed values are produced by the same "
            "reviewed method, so no value migration is required.",
        ],
    }

    assumptions = [
        item for item in analysis.get("review_assumptions", [])
        if isinstance(item, str)
    ]
    if structurally_settled:
        assumptions.append(
            "This is an override of Object.hashCode() whose own fallback path "
            "returns Object.hashCode(), an identity hash that differs on every "
            "process run, so the value is already not stable across runs and "
            "no conforming caller can depend on it."
        )
        # State the concrete residual risk rather than implying the structural
        # argument disposes of it: the Java contract forbids relying on the
        # value, but code that derives a persisted notification or alarm ID
        # from hashCode does rely on it, and such an ID would change once.
        assumptions.append(
            "If any identifier derived from this hashCode was persisted -- a "
            "notification or alarm ID, for example -- it changes once after "
            "this patch, so a reviewer should confirm that no such stored "
            "identifier needs to keep matching."
        )
    else:
        assumptions.append(
            "The examined scope found no persisted, transmitted, or external "
            "consumer of the digest-derived value. A reviewer should confirm "
            "that no value of this digest survives an application upgrade, "
            "because a stored pre-upgrade value would compare unequal exactly "
            "once after the algorithm changes."
        )
    promoted["review_assumptions"] = assumptions
    promoted["issue_candidate"] = {
        "eligible": False, "title": "", "finding_summary": "",
        "why_no_pr": "", "maintainer_question": "",
        "proposed_next_step": "",
    }
    return promoted


def with_provided_files(edits: list, policy: Dict) -> list:
    """The generated edits with every policy-provided file created verbatim.

    A provided file -- a root certificate the TLS trust evidence fetched -- is
    written by the pipeline, not copied by the model: a PEM block retyped by a
    model is one transposed character away from an unreadable anchor. Any edit
    the model made to the same path is replaced. An entry with "original" and
    "replacement" edits an existing file instead (the manifest attribute that
    enables a provided network security config). Only plain relative paths
    under a module's src/main/ directory are accepted.
    """
    provided = [item for item in (policy or {}).get("provided_files") or []
                if isinstance(item, dict)]
    if not provided:
        return edits
    kept = list(edits or [])
    for item in provided:
        path = str(item.get("path", "")).replace("\\", "/")
        relative = Path(path)
        if (not path or relative.is_absolute() or ".." in relative.parts
                or "/src/main/" not in f"/{path}"):
            continue
        kept = [edit for edit in kept
                if str((edit or {}).get("source_file_path", "")).replace("\\", "/") != path]
        if item.get("original"):
            kept.append({
                "source_file_path": path,
                "symbol": relative.name,
                "edit_kind": "declaration",
                "original_code": str(item["original"]),
                "corrected_code": str(item.get("replacement", "")),
                "reason": str(item.get("purpose", "")) or "edit provided by the remediation policy",
            })
            continue
        kept.append({
            "source_file_path": path,
            "symbol": relative.name,
            "edit_kind": "create",
            "original_code": "",
            "corrected_code": str(item.get("content", "")),
            "reason": str(item.get("purpose", "")) or "file provided by the remediation policy",
        })
    return kept


def authorize_provided_files(plan: Dict, policy: Dict) -> Dict:
    """Record each policy-provided file in the authorized patch plan.

    The applicator refuses any edit outside the adjudicated plan
    (EDIT_OUTSIDE_AUTHORIZED_PLAN), and the model is told not to plan these
    files: the pipeline writes them. Recording them here, as creations whose
    authority is the policy, keeps that gate exact instead of exempting a path.
    """
    if not isinstance(plan, dict):
        return plan
    edits = plan.setdefault("required_edits", [])
    planned = {str(item.get("source_file_path", "")) for item in edits if isinstance(item, dict)}
    for item in (policy or {}).get("provided_files") or []:
        path = str((item or {}).get("path", "")).replace("\\", "/")
        if not path or path in planned:
            continue
        edits.append({
            "source_file_path": path,
            "symbol": Path(path).name,
            "reason": str(item.get("purpose", "")) or "file provided by the remediation policy",
            "create_new_file": not item.get("original"),
            "edit_category": "policy_provided_file",
            "supporting_claim_ids": [],
        })
    return plan
