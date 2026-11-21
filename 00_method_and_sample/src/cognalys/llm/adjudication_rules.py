"""Deterministic checks and routings applied to what the model answers.

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

from cognalys.utils.disposition import GENERATING_DECISIONS  # noqa: E402,F401
from cognalys.utils.finding_source import (  # noqa: E402,F401
    ADVISORY_IDENTIFIERS,
    advisory_identifiers,
)

from cognalys.llm.false_positive_rules import (  # noqa: E402
    predicate_deferred_to_detector,
)
from cognalys.llm.llm_contracts import (  # noqa: E402
    FUNCTION_CONTRACT_ACTIVATIONS,
    FUNCTION_CONTRACT_COUNTERPARTS,
    FUNCTION_CONTRACT_DEPENDENCE,
    HUMAN_DECIDED_BLOCKER_CATEGORIES,
    RENAME_AUTHORIZATION_CATEGORY,
)
from cognalys.llm.pipeline_common import (  # noqa: E402
    canonical_json,
    violation_rule_section,
)


def cause_rule_section_issues(adjudication: dict, finding: dict | None) -> list[str]:
    """A confirmed cause must be about the part of the rule that was reported.

    Deterministic, and only for violation types whose section is known. An
    adjudication written before cause_rule_section existed states nothing and
    is not held to it.
    """
    violation = str((finding or {}).get("reported_violation", ""))
    expected = violation_rule_section(violation)
    if not expected or adjudication.get("cause_status") != "confirmed":
        return []
    if "cause_rule_section" not in adjudication:
        return []
    stated = str(adjudication.get("cause_rule_section", "")).strip()
    if stated == expected:
        return []
    return [
        f"the detector reported {violation}, which is a violation of the "
        f"rule's {expected} section, but the confirmed cause is stated for "
        f"its {stated or 'unstated'} section; a cause in another section "
        "explains a different requirement, so record that requirement as a "
        f"predicate and base cause_status on the {expected} requirement the "
        "detector reported, or set it to unknown if the evidence does not "
        "decide that one"
    ]


def human_decided_publication_considerations(evidence_analysis: dict) -> list[dict]:
    """The publication blockers only a human can settle, deduplicated.

    _analysis_record already writes every blocking_for_publication item, with
    its category and description untouched, to evidence_analysis["v2"]
    ["adjudication"] -- this just reads it back and keeps only the categories
    HUMAN_DECIDED_BLOCKER_CATEGORIES names (the same generic vocabulary the
    review-routing functions above use, not a per-specification list).

    A category outside that set means the adjudicator believes the pipeline's
    own build and recheck can settle the point without a human; the pull
    request generated for hfut-schedule's getUpdate is the case this leaves
    out on purpose -- its "other"-category item claimed exactly that, but no
    HardcodedSecretSpec adapter exists to run the recheck, so nothing ever
    confirmed it. That gap is a job for the revalidation adapters, not for
    this function to paper over by guessing.
    """
    if not isinstance(evidence_analysis, dict):
        return []
    adjudication = (evidence_analysis.get("v2") or {}).get("adjudication") or {}
    # Rename questions arrive already moved out of the blockers
    # (discharge_rename_authorization_blockers): they are notes.
    items = [*(adjudication.get("blocking_for_publication") or []),
             *(adjudication.get("publication_considerations") or [])]
    seen: set[str] = set()
    considerations = []
    for item in items:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", ""))
        if category not in HUMAN_DECIDED_BLOCKER_CATEGORIES | {RENAME_AUTHORIZATION_CATEGORY}:
            continue
        description = str(item.get("description", "")).strip()
        if not description or description in seen:
            continue
        seen.add(description)
        # kind and the cited claim ids let the pull request text drop the
        # adjudicator's internal reasoning while keeping its question.
        considerations.append({
            "category": category, "description": description,
            "kind": str(item.get("kind", "")).strip(),
            "supporting_claim_ids": [
                str(claim) for claim in item.get("supporting_claim_ids") or []
                if str(claim).strip()
            ],
        })
    return considerations


def publication_blocker_record(evidence_analysis: dict,
                               behavior_review_summary: dict | None = None) -> dict:
    """Every publication blocker the adjudication wrote, for the verdict after validation.

    human_decided_publication_considerations keeps the descriptions a human
    reads; this keeps all of them, each with its category, kind and whether
    the pipeline's validation discharges it, plus the blockers rules already
    discharged, the plan's publication checklist and what the behaviour
    review found. utils.disposition.publication_after_validation reads it
    once the patch is built: publishability used to be fixed at adjudication
    time, before any patch existed, and nothing judged it again.
    """
    analysis = evidence_analysis if isinstance(evidence_analysis, dict) else {}
    v2 = analysis.get("v2") or {}
    adjudication = v2.get("adjudication") or {}

    def entry(item: dict) -> dict:
        return {
            "category": str(item.get("category", "")).strip(),
            "kind": str(item.get("kind", "")).strip(),
            "description": str(item.get("description", "")).strip(),
            "discharged_by_pipeline_validation":
                item.get("discharged_by_pipeline_validation") is True,
        }

    seen: set = set()
    blockers = []
    for item in adjudication.get("blocking_for_publication") or []:
        if not isinstance(item, dict):
            continue
        record = entry(item)
        key = (record["category"], record["description"] or record["kind"])
        if key in seen:
            continue
        seen.add(key)
        blockers.append(record)
    discharged = [
        {**entry(item), "discharged_by": next(
            (name for name in ("discharged_by_derived_evidence", "discharged_by_producer_finding")
             if item.get(name)), "rule")}
        for item in adjudication.get("discharged_blockers") or [] if isinstance(item, dict)
    ]
    review = behavior_review_summary if isinstance(behavior_review_summary, dict) else {}
    return {
        "decision": str(adjudication.get("decision", analysis.get("decision", "")) or ""),
        "blockers": blockers,
        "discharged_blockers": discharged,
        "evidence_gap_promoted": list(adjudication.get("evidence_gap_promoted") or []),
        "plan_publication_blockers": [
            str(item).strip() for item in (v2.get("patch_plan") or {}).get(
                "publication_blockers") or [] if str(item).strip()],
        "behavior_review": {
            "status": str(review.get("status", "") or "not_reviewed"),
            "review_contract_version": review.get("review_contract_version"),
            "regression_count": int(review.get("regression_count") or 0),
            "patch_has_no_effect": bool(review.get("patch_has_no_effect")),
        },
    }


def validate_evidence_extraction(extraction: dict, packet: dict) -> list[str]:
    valid_evidence = {
        item.get("evidence_id") for item in [
            *packet.get("evidence_catalog", []),
            *packet.get("external_evidence_catalog", []),
        ]
    }
    issues = []
    claim_ids = set()
    for claim in extraction.get("claims", []):
        if not isinstance(claim, dict):
            issues.append("every claim must be an object")
            continue
        claim_id = str(claim.get("claim_id", ""))
        if not claim_id or claim_id in claim_ids:
            issues.append("claim IDs must be non-empty and unique")
        claim_ids.add(claim_id)
        evidence_ids = claim.get("evidence_ids", [])
        unknown = sorted(set(evidence_ids) - valid_evidence)
        if unknown:
            issues.append(f"claim {claim_id} references unknown evidence: {unknown}")
        if claim.get("epistemic_status") == "confirmed" and not evidence_ids:
            issues.append(f"confirmed claim {claim_id} has no evidence IDs")
    for predicate in extraction.get("predicate_assessment", []):
        if not isinstance(predicate, dict):
            issues.append("every predicate assessment must be an object")
            continue
        evidence_ids = predicate.get("evidence_ids", [])
        unknown = sorted(set(evidence_ids) - valid_evidence)
        if unknown:
            issues.append(f"predicate references unknown evidence: {unknown}")
        if predicate.get("status") in {"confirmed", "violated"} and not evidence_ids:
            issues.append("confirmed/violated predicate has no evidence IDs")
    return issues


# The entries that cannot show who the reported code serves: the reported code
# itself and the rule. A dependence must be shown by something else.
_CONTRACT_EXCLUDED_KINDS = frozenset({"target_source", "verified_specification"})
_CONTRACT_QUOTES = 3
_CONTRACT_QUOTE_MIN, _CONTRACT_QUOTE_MAX = 12, 400
_CONTRACT_SERVED = ("third_party_service", "user_operated_peer")
NEUTRAL_FUNCTION_CONTRACT = {
    "purpose": "", "counterpart": "unknown", "activation": "unknown",
    "depends_on_reported_behavior": "not_established", "dependence": "",
    "evidence_ids": [], "quotes": [],
}
FUNCTION_CONTRACT_MEANING = (
    "the evidence extraction's reading of who the reported code serves and why they depend on the "
    "reported behaviour, with the repository text it quotes; each quote was checked to appear in the "
    "entry it names. It is a reading of the repository, not a maintainer statement."
)


def _entry_text(entry: dict) -> str:
    """An evidence entry's text: its content, or its data as JSON for a structured entry."""
    data = entry.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        return data["content"]
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _collapsed(text: str) -> str:
    return " ".join(str(text).split())


def _sanitized_function_contract(value, packet: dict, valid_evidence: set) -> tuple[dict, list[str]]:
    """function_contract checked against the packet: known values only, valid ids, and
    quotes kept only when found verbatim (whitespace collapsed) in a repository entry
    other than the reported source and the specification. A dependence stays
    established only for a third-party service or a user-operated peer, with a
    dependence stated and a quote kept."""
    notes: list[str] = []
    if not isinstance(value, dict):
        return deepcopy(NEUTRAL_FUNCTION_CONTRACT), [
            "function_contract: not an object; replaced by the neutral contract"]
    contract = deepcopy(value)
    for field, allowed, default in (
            ("counterpart", FUNCTION_CONTRACT_COUNTERPARTS, "unknown"),
            ("activation", FUNCTION_CONTRACT_ACTIVATIONS, "unknown"),
            ("depends_on_reported_behavior", FUNCTION_CONTRACT_DEPENDENCE, "not_established")):
        if contract.get(field) not in allowed:
            notes.append(f"function_contract: {field} {contract.get(field)!r} is not a known "
                         f"value; set to {default}")
            contract[field] = default
    for field in ("purpose", "dependence"):
        contract[field] = "" if contract.get(field) is None else str(contract.get(field))
    ids = contract.get("evidence_ids") if isinstance(contract.get("evidence_ids"), list) else []
    kept_ids = list(dict.fromkeys(item for item in ids if item in valid_evidence))
    invalid = [item for item in ids if item not in valid_evidence]
    if invalid:
        notes.append(f"function_contract: removed invalid evidence reference(s) {invalid}")
    contract["evidence_ids"] = kept_ids
    catalog = {entry.get("evidence_id"): entry for entry in packet.get("evidence_catalog", []) or []
               if isinstance(entry, dict)}
    quotes = contract.get("quotes") if isinstance(contract.get("quotes"), list) else []
    kept = []
    for item in quotes:
        if not isinstance(item, dict):
            continue
        entry = catalog.get(item.get("evidence_id"))
        quote = item.get("quote") if isinstance(item.get("quote"), str) else ""
        if (item.get("evidence_id") in kept_ids and entry is not None
                and entry.get("kind") not in _CONTRACT_EXCLUDED_KINDS
                and _CONTRACT_QUOTE_MIN <= len(quote.strip()) <= _CONTRACT_QUOTE_MAX
                and _collapsed(quote) in _collapsed(_entry_text(entry))):
            kept.append({"evidence_id": item["evidence_id"], "quote": quote})
    kept = kept[:_CONTRACT_QUOTES]
    if len(kept) < len(quotes):
        notes.append(f"function_contract: dropped {len(quotes) - len(kept)} quote(s) not found in a "
                     "repository entry other than the reported source and the specification")
    contract["quotes"] = kept
    if contract["depends_on_reported_behavior"] == "established":
        reason = (f"counterpart {contract['counterpart']}" if contract["counterpart"] not in _CONTRACT_SERVED
                  else "empty dependence" if not contract["dependence"].strip()
                  else "no verified quote" if not kept else "")
        if reason:
            contract["depends_on_reported_behavior"] = "not_established"
            notes.append("function_contract: depends_on_reported_behavior downgraded to "
                         f"not_established ({reason})")
    return contract, notes


def extraction_for_adjudication(extraction: dict) -> dict:
    """The extraction as the adjudication reads it: without function_contract, which
    only the behaviour review receives. The argument is not changed."""
    if not isinstance(extraction, dict) or "function_contract" not in extraction:
        return extraction
    return {key: value for key, value in extraction.items() if key != "function_contract"}


def established_function_contract(extraction: dict, packet: dict) -> dict:
    """The extraction's function_contract as the behaviour review receives it, sanitized
    again against the packet; {} unless the dependence is established."""
    if not isinstance(extraction, dict) or "function_contract" not in extraction:
        return {}
    valid_evidence = {
        item.get("evidence_id") for item in [
            *packet.get("evidence_catalog", []),
            *packet.get("external_evidence_catalog", []),
        ]
    }
    contract, _ = _sanitized_function_contract(
        extraction.get("function_contract"), packet, valid_evidence)
    if contract["depends_on_reported_behavior"] != "established":
        return {}
    catalog = {entry.get("evidence_id"): entry for entry in packet.get("evidence_catalog", []) or []
               if isinstance(entry, dict)}
    evidence = []
    for quote in contract["quotes"]:
        entry = catalog.get(quote["evidence_id"]) or {}
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        evidence.append({"evidence_id": quote["evidence_id"], "kind": str(entry.get("kind", "")),
                         "source_file_path": str(data.get("source_file_path") or data.get("path") or ""),
                         "quote": quote["quote"]})
    return {"purpose": contract["purpose"], "counterpart": contract["counterpart"],
            "activation": contract["activation"], "depends_on_reported_behavior": "established",
            "dependence": contract["dependence"], "evidence": evidence,
            "meaning": FUNCTION_CONTRACT_MEANING}


def sanitize_evidence_extraction(extraction: dict, packet: dict) -> tuple[dict, list[str]]:
    """Strip invalid evidence references instead of discarding the extraction.

    A model can cite an evidence_id that was only ever shown for provenance
    -- DET-001 is deliberately kept out of ``evidence_catalog`` so it cannot
    ground a confirmed claim, but it is still visible in the prompt -- or
    otherwise reference an ID the packet never issued. Treating any such
    reference as grounds to discard every claim and predicate the same
    response got right destroys real signal over one bad citation. Instead,
    drop only the invalid references (demoting any claim or predicate whose
    "confirmed"/"violated" status depended entirely on them to "unknown",
    the schema's neutral value) and keep everything else the model
    correctly extracted. Malformed entries (non-object, or a claim with an
    empty/duplicate ID) are dropped individually for the same reason.
    """
    valid_evidence = {
        item.get("evidence_id") for item in [
            *packet.get("evidence_catalog", []),
            *packet.get("external_evidence_catalog", []),
        ]
    }
    notes: list[str] = []
    sanitized = deepcopy(extraction)

    sanitized_claims = []
    seen_claim_ids: set[str] = set()
    for claim in sanitized.get("claims", []) or []:
        if not isinstance(claim, dict):
            notes.append("dropped a non-object claim entry")
            continue
        claim_id = str(claim.get("claim_id", "")).strip()
        if not claim_id:
            notes.append("dropped a claim with an empty claim_id")
            continue
        if claim_id in seen_claim_ids:
            notes.append(f"dropped duplicate claim_id {claim_id}")
            continue
        seen_claim_ids.add(claim_id)
        evidence_ids = claim.get("evidence_ids", []) or []
        invalid = sorted(set(evidence_ids) - valid_evidence)
        if invalid:
            claim["evidence_ids"] = [e for e in evidence_ids if e in valid_evidence]
            notes.append(f"claim {claim_id}: removed invalid evidence reference(s) {invalid}")
        if claim.get("epistemic_status") == "confirmed" and not claim.get("evidence_ids"):
            claim["epistemic_status"] = "unknown"
            notes.append(
                f"claim {claim_id}: downgraded epistemic_status to unknown "
                "(no valid evidence remained after sanitization)"
            )
        sanitized_claims.append(claim)
    sanitized["claims"] = sanitized_claims

    sanitized_predicates = []
    for predicate in sanitized.get("predicate_assessment", []) or []:
        if not isinstance(predicate, dict):
            notes.append("dropped a non-object predicate_assessment entry")
            continue
        evidence_ids = predicate.get("evidence_ids", []) or []
        invalid = sorted(set(evidence_ids) - valid_evidence)
        if invalid:
            predicate["evidence_ids"] = [e for e in evidence_ids if e in valid_evidence]
            notes.append(
                f"predicate {predicate.get('predicate', '')!r}: removed invalid "
                f"evidence reference(s) {invalid}"
            )
        if predicate.get("status") in {"confirmed", "violated"} and not predicate.get("evidence_ids"):
            notes.append(
                f"predicate {predicate.get('predicate', '')!r}: downgraded status "
                f"from {predicate.get('status')} to unknown (no valid evidence "
                "remained after sanitization)"
            )
            predicate["status"] = "unknown"
        # Named, not repaired. Nothing here can decide a requirement the
        # evidence pass declined to decide, and guessing would be worse than
        # the silence it replaces. What it can do is stop the deferral from
        # reading as an ordinary evidence gap: the note travels into the
        # artifact and the log, where a run that lost a source-settled
        # violation this way can be recognised as having lost one.
        if predicate_deferred_to_detector(predicate):
            predicate["unknown_reason"] = "detector_modelling"
            notes.append(
                f"predicate {predicate.get('predicate', '')!r}: left unknown "
                "because of what the detector would do, which does not decide "
                "whether the requirement holds in the reviewed code"
            )
        sanitized_predicates.append(predicate)
    sanitized["predicate_assessment"] = sanitized_predicates

    if "function_contract" in sanitized:
        sanitized["function_contract"], contract_notes = _sanitized_function_contract(
            sanitized["function_contract"], packet, valid_evidence)
        notes.extend(contract_notes)

    return sanitized, notes


def detector_message_is_absent(finding: dict) -> bool:
    """Whether the detector rendered no usable message for this finding.

    "unknown" is what the analyzer writes when it resolved a violation but
    rendered no predicate text for it; the violated predicate itself is still
    recoverable from the finding id and is carried as `reported_violation`.
    An empty message is treated the same way.
    """
    message = str(finding.get("error_message", "")).strip().lower()
    return message in {"", "unknown"}


# Evidence that cannot carry a dismissal on its own: the detector's own report
# (DET, kept out of the catalogue so it never grounds a claim) and published
# sources found by the web research (WEB). Everything else in the packet is
# the repository, the analysed APK or the verified specification.
_DISMISSAL_UNGROUNDED_EVIDENCE_PREFIXES = ("DET-", "WEB-")

# How an answer says it rests on one of the rule's documented false-positive
# shapes: the adjudication prompt asks it to name the matched shape in
# confirmed_cause. Measured on 2026-11-12 over the 183 recorded not_confirmed
# adjudications: 136 say so in these words, none negated.
_NAMES_FALSE_POSITIVE_SHAPE = re.compile(
    r"false[- ]positive shape|known false[- ]positive|documented false[- ]positive",
    re.IGNORECASE,
)

# A cause quoting this many consecutive words of a shape rests on that shape.
_SHAPE_QUOTE_WORDS = 6


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_.$()]+", str(text or "").lower())


def _longest_common_word_run(left: list[str], right: list[str]) -> int:
    best = 0
    previous = [0] * (len(right) + 1)
    for left_word in left:
        current = [0] * (len(right) + 1)
        for index, right_word in enumerate(right, 1):
            if left_word == right_word:
                current[index] = previous[index - 1] + 1
                best = max(best, current[index])
        previous = current
    return best


def dismissal_justification_issues(adjudication: dict, extraction: dict,
                                   reported_by_rule: dict | None = None) -> list[str]:
    """What a not_confirmed answer must show, as a patch must show its cause.

    A generating decision needs a confirmed cause and cited claims; a
    dismissal (detector_violation not_confirmed) needed nothing and became a
    recognised false positive. It now needs:
    1. cited claims (decision_claim_ids);
    2. every cited claim confirmed and resting on evidence other than the
       detector's report or web sources alone (repository, APK or
       specification evidence);
    3. no reliance on a false-positive shape the shape review did not keep:
       a cause quoting an unreviewed shape, or naming a false-positive shape
       when the rule has no reviewed one, is rejected.
    It decides nothing about the finding: a rejected dismissal goes through
    the adjudication's correction, and one still rejected after it falls to
    detector_violation unknown, never to a false positive.
    reported_by_rule is the rule as the adjudication read it
    (verified_detector_semantics); without it no shape counts as reviewed.
    """
    if adjudication.get("detector_violation") != "not_confirmed":
        return []
    issues = []
    cited = [str(item) for item in adjudication.get("decision_claim_ids", []) or []]
    if not cited:
        issues.append(
            "a not_confirmed detector violation requires supporting claim IDs, "
            "as a repair requires them"
        )
    claims = {
        str(item.get("claim_id", "")): item for item in extraction.get("claims", []) or []
        if isinstance(item, dict)
    }
    ungrounded = []
    for claim_id in cited:
        claim = claims.get(claim_id)
        if claim is None:
            continue  # reported as an unknown claim reference
        evidence = [str(item) for item in claim.get("evidence_ids", []) or []]
        grounded = any(not item.startswith(_DISMISSAL_UNGROUNDED_EVIDENCE_PREFIXES)
                       for item in evidence)
        if claim.get("epistemic_status") != "confirmed" or not grounded:
            ungrounded.append(claim_id)
    if ungrounded:
        issues.append(
            "a not_confirmed detector violation may cite only confirmed claims "
            "resting on repository or specification evidence (not the "
            "detector's report or web sources alone); not so: "
            + ", ".join(ungrounded)
        )
    rule = reported_by_rule if isinstance(reported_by_rule, dict) else {}
    reviewed = [str(item) for item in rule.get("known_false_positive_shapes") or []]
    unreviewed = [str(item) for item in rule.get("unreviewed_false_positive_shapes") or []]
    cause = str(adjudication.get("confirmed_cause", ""))
    cause_words = _words(cause)
    quoted = [shape for shape in unreviewed
              if _longest_common_word_run(cause_words, _words(shape)) >= _SHAPE_QUOTE_WORDS]
    if quoted or (not reviewed and _NAMES_FALSE_POSITIVE_SHAPE.search(cause)):
        issues.append(
            "a not_confirmed detector violation cannot rest on a false-positive "
            "shape the shape review did not keep (unreviewed_false_positive_shapes, "
            "or a shape the rule does not list as reviewed); judge the "
            "violation from the reviewed code instead"
        )
    return issues


def validate_adjudication(adjudication: dict, extraction: dict,
                          finding: dict | None = None,
                          reported_by_rule: dict | None = None) -> list[str]:
    claim_ids = {
        str(item.get("claim_id", "")) for item in extraction.get("claims", [])
        if isinstance(item, dict)
    }
    issues = []
    unknown = sorted(set(adjudication.get("decision_claim_ids", [])) - claim_ids)
    if unknown:
        issues.append(f"decision references unknown claims: {unknown}")
    decision = str(adjudication.get("decision", ""))
    supported = decision in GENERATING_DECISIONS
    if supported:
        if adjudication.get("cause_status") != "confirmed":
            issues.append("supported decision requires confirmed cause")
        if not str(adjudication.get("confirmed_cause", "")).strip():
            issues.append("supported decision requires non-empty confirmed cause")
        detector = adjudication.get("detector_violation")
        if detector not in {"confirmed", "source_established"}:
            issues.append(
                "supported decision requires a detector violation that is "
                "confirmed, or source_established when the detector rendered "
                "no message"
            )
        elif detector == "source_established":
            # The detector reports "unknown" for 99,611 of the 142,580
            # recorded findings, and then there is nothing to corroborate:
            # requiring corroboration made every such finding abstain even
            # when the source proved the violation on its own. Four of four
            # confirmed-cause findings with an unknown message were abstained
            # this way. The claim is allowed to rest on source evidence, but
            # only on source evidence that exists, and never silently -- the
            # missing corroboration becomes a stated review assumption that
            # reaches the published artifact.
            # Without the finding there is nothing to read the message from.
            # Treating that as "no message" waived this check for every
            # production adjudication, whose call sites did not pass it.
            if finding is None:
                issues.append(
                    "source_established cannot be checked without the "
                    "finding it adjudicates"
                )
            elif not detector_message_is_absent(finding):
                issues.append(
                    "source_established is only for a finding whose detector "
                    "rendered no usable message"
                )
            if adjudication.get("cause_status") != "confirmed":
                issues.append(
                    "source_established requires a confirmed cause"
                )
            if not any(
                "detector" in str(item).lower()
                for item in adjudication.get("review_assumptions", []) or []
            ):
                issues.append(
                    "source_established requires a review assumption stating "
                    "that the detector's own report could not be corroborated"
                )
            if adjudication.get("generation_status") == "supported":
                # Auto-apply rests on two independent signals agreeing. Only
                # one of them exists here, so a human reads this one.
                issues.append(
                    "source_established cannot authorize automatic "
                    "generation; it is reviewable generation at most"
                )
        if not adjudication.get("decision_claim_ids"):
            issues.append("supported decision requires supporting claim IDs")
        if adjudication.get("blocking_for_generation"):
            issues.append("supported decision has generation blockers")
        if (adjudication.get("generation_status") == "supported"
                and adjudication.get("compatibility_status") != "preserved"):
            issues.append("automatic generation requires preserved compatibility")
    elif adjudication.get("generation_status") in {
            "supported", "supported_for_review"}:
        issues.append("non-repair decision cannot authorize generation")
    behavior = adjudication.get("authorized_behavior_change", {}) or {}
    if behavior.get("authorized") is True and not str(
            behavior.get("policy_reference", "")).strip():
        issues.append("authorized behavior change requires a policy reference")
    # "eligible_after_validation" means eligible once validation runs, so a
    # blocker the pipeline's own validation discharges is exactly what the
    # state contemplates and must not veto it. Anything else -- a live
    # endpoint, an unknown external contract, a maintainer decision -- no
    # automated step can settle, and it still forces "blocked".
    if adjudication.get("publication_status") == "eligible_after_validation":
        undischarged = [
            str(item.get("kind", "unknown"))
            for item in adjudication.get("blocking_for_publication", [])
            if isinstance(item, dict)
            and item.get("discharged_by_pipeline_validation") is not True
        ]
        if undischarged:
            issues.append(
                "publication cannot be eligible while blockers this "
                "pipeline's validation does not discharge remain: "
                + ", ".join(sorted(set(undischarged))[:8])
            )
    issues.extend(dismissal_justification_issues(adjudication, extraction, reported_by_rule))
    return issues


def validate_adjudication_of(adjudication: dict, extraction: dict,
                             finding: dict,
                             reported_by_rule: dict | None = None) -> list[str]:
    """Every deterministic objection to an adjudication of `finding`.

    The one entry point the pipeline validates an adjudication through, with
    the finding required, so the checks that read it cannot be skipped by a
    call site that forgets to pass it. reported_by_rule is the rule the
    adjudication read; without it no false-positive shape counts as
    reviewed, which only makes a dismissal harder.
    """
    return (
        validate_adjudication(adjudication, extraction, finding, reported_by_rule)
        + cause_rule_section_issues(adjudication, finding)
    )


# Decisions that name a confirmed violation's impact rather than its remedy.
# Decisions that authorise a patch applied without review, and so the ones a
# scope that cannot be completed demotes rather than discards.
_DEMOTABLE_FOR_SCOPE = frozenset({"FIX_SUPPORTED", "MULTI_FILE_FIX_SUPPORTED"})


def demote_incomplete_scope_to_review(analysis: dict, decision: str,
                                      scope_issues: list) -> bool:
    """Generate for review a confirmed repair whose scope is incomplete.

    An incomplete scope means files that consume the change were not all
    examined. That rules out applying the patch unattended; it does not rule
    out writing it for a human who reads it first, which is exactly what the
    review-only decision is. The pipeline already demotes this way when the
    adjudicator itself writes `supported_for_review` (see
    adjudication._analysis_record); when it writes `supported` instead, the
    same finding abstained -- hmdm's Zip Slip fix in run_20260920_175850,
    cause confirmed, zero generation blockers, stopped only because five
    consumers were not individually reviewed.

    The authority only ever goes down: an auto-applied decision becomes a
    reviewed one and its publication is blocked until a human signs off. It
    never fires when anything blocks generation itself, when the cause is
    unconfirmed, or for dependency and digest decisions whose own gates
    depend on their family.

    A review-only decision whose publication is not blocked is completed the
    same way: adjudication._analysis_record relabels FIX_SUPPORTED with
    `supported_for_review` to this label without blocking publication, so the
    scope waiver (which needs publication blocked) never applied and this
    function refused the label -- winterhold's derive stopped as a scope_gap
    in eight runs (c_arm_k3 r3 among them) while the replicas whose
    adjudicator wrote `supported` were demoted and validated.

    Mutates `analysis`; returns whether it demoted.
    """
    if not scope_issues or not isinstance(analysis, dict):
        return False
    adjudication = (analysis.get("v2") or {}).get("adjudication") or {}
    if not isinstance(adjudication, dict):
        return False
    review_only_unblocked = (
        decision == "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
        and adjudication.get("publication_status") != "blocked"
    )
    if decision not in _DEMOTABLE_FOR_SCOPE and not review_only_unblocked:
        return False
    if adjudication.get("generation_status") not in {
            "supported", "supported_for_review"}:
        return False
    if adjudication.get("blocking_for_generation"):
        return False
    if str(analysis.get("cause_status", adjudication.get("cause_status", ""))) != "confirmed":
        return False
    scope = analysis.get("scope_assessment") or {}
    relations = scope.get("unresolved_relations") or [] if isinstance(scope, dict) else []
    if any(
        not isinstance(relation, dict)
        or relation.get("blocking_for_generation") is True
        for relation in relations
    ):
        return False
    demoted = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
    analysis["decision"] = demoted
    adjudication["decision"] = demoted
    adjudication["generation_status"] = "supported_for_review"
    adjudication["publication_status"] = "blocked"
    analysis["generation_status"] = "supported_for_review"
    analysis["publication_status"] = "blocked"
    assumptions = [
        item for item in analysis.get("review_assumptions", []) or []
        if isinstance(item, str)
    ]
    assumptions.append(
        "The change was generated for review because its scope could not be "
        "completed automatically (" + "; ".join(str(item) for item in scope_issues[:3])
        + "). A reviewer must confirm the unexamined consumers before this "
        "is applied."
    )
    analysis["review_assumptions"] = assumptions
    analysis["demoted_from"] = decision
    return True


def _settles(fact: str, record: dict) -> bool:
    """Whether a record with a settling status really answered its question.

    "No callers" settles a caller-contract blocker only when the search ran
    where the callers would be: Amethyst's MacInstance.<init> had two
    blockers discharged on a no_callers_found that searched for the literal
    `<init>(` and skipped the declaring file, next to 23 caller entries in
    the same packet. A record that does not say what it searched (written
    before it did) settles nothing.
    """
    if fact != "caller_contract" or record.get("status") != "no_callers_found":
        return True
    return (record.get("searched_declaring_file") is True
            and bool(record.get("name_resolution"))
            and not record.get("catalog_callers_of_target"))


def discharge_blockers_answered_by_derived_facts(
    adjudication: dict, derived_facts: dict,
) -> list[dict]:
    """Drop generation blockers the checkout already answers.

    The adjudicator states a blocker as a question it could not settle --
    "unresolved ownership and version of the reported class",
    "reported-rule applicability is unconfirmed",
    "incomplete_reachable_caller_contract". Those three questions are
    answered deterministically in the packet's derived_facts, and a blocker
    that survives despite its answer ends the finding for want of something
    it was given.

    The blocker is matched to a question by the same classifier the evidence
    responder uses on requests, so the two stay in step, and it is
    discharged only when the corresponding record reached a status that
    settles it. A record that came back unresolved, undeclared_external or
    no_governed_api_matched settles nothing and the blocker stands.

    Mutates `adjudication`; returns the blockers it discharged, each
    carrying the fact that discharged it so the record stays auditable.
    """
    from cognalys.llm.evidence_requests import (
        ARTIFACT_OWNERSHIP, CALLER_CONTRACT, RULE_APPLICABILITY, SYMBOL_BINDING,
        asks_for_value_origin, classify_evidence_request,
    )

    if not isinstance(derived_facts, dict) or derived_facts.get("status") != "collected":
        return []
    # Each question can be settled by any of the facts listed for it; the
    # first one that reached a settling status is recorded.
    settled = {
        # "The source establishes only a lexical call; it does not
        # establish that this call resolves to <governed API>" -- settled
        # only when every governed call binds to a type the specification
        # itself names.
        SYMBOL_BINDING: (("call_binding", {"resolved_to_governed_type"}),),
        ARTIFACT_OWNERSHIP: ((
            "artifact_ownership",
            {"repository_class", "declared_dependency"},
        ),),
        # Two independent authorities: the specification's governed API is
        # in the reviewed source, or the reporting detector itself raises
        # the rule in this method on this revision.
        RULE_APPLICABILITY: (
            ("specification_applicability", {"governed_api_matched"}),
            ("detector_reproduction", {"alert_present"}),
        ),
        CALLER_CONTRACT: ((
            "caller_contract", {"callers_enumerated", "no_callers_found"},
        ),),
    }
    blockers = adjudication.get("blocking_for_generation") or []
    if not isinstance(blockers, list):
        return []
    kept, discharged = [], []
    for blocker in blockers:
        if not isinstance(blocker, dict):
            kept.append(blocker)
            continue
        question = {
            "kind": str(blocker.get("kind", "")),
            "symbol": str(blocker.get("category", "")),
            "reason": str(blocker.get("description", "")),
        }
        # A blocker that names a provenance, a trace, an observation or a
        # producer asks where a value comes from, which no caller list or
        # binding settles. The responder now answers such requests from
        # source when they also ask for callers; the discharge does not.
        if asks_for_value_origin(question):
            kept.append(blocker)
            continue
        family = classify_evidence_request(question)
        alternatives = settled.get(family)
        if alternatives is None:
            kept.append(blocker)
            continue
        answer = next((
            (fact, (derived_facts.get(fact) or {}).get("status"))
            for fact, statuses in alternatives
            if str((derived_facts.get(fact) or {}).get("status", "")) in statuses
            and _settles(fact, derived_facts.get(fact) or {})
        ), None)
        if answer is None:
            kept.append(blocker)
            continue
        discharged.append({
            **blocker,
            "discharged_by_derived_evidence": True,
            "discharging_fact": answer[0],
            "discharging_status": answer[1],
        })
    if discharged:
        adjudication["blocking_for_generation"] = kept
        adjudication["discharged_blockers"] = [
            *(adjudication.get("discharged_blockers") or []), *discharged,
        ]
    return discharged


def _refuses_behavior_change(adjudication: dict) -> bool:
    behavior = adjudication.get("authorized_behavior_change")
    return (isinstance(behavior, dict) and behavior.get("authorized") is False
            and bool(str(behavior.get("description", "")).strip()))


def discharge_rename_authorization_blockers(
    adjudication: dict, extraction: dict | None = None, finding: dict | None = None,
) -> list[dict]:
    """A rename question is a note, never a blocker.

    Orgzly's sha1 and NotallyX's md5Hash (2026-10-25/28) were held as
    review-only because the adjudicator asked for a "rename_safety
    authorization", although rename_safety is a repository check this
    pipeline runs and Orgzly's had allowed the rename. A rename the check
    allows needs nobody's approval; one it refuses changes the route of the
    fix (a correctly named function beside the old one), and neither stops
    a patch. So every blocking_for_generation or blocking_for_publication
    item of category rename_authorization moves to publication_considerations
    and discharged_blockers, matched on the structured category only.

    An authorized_behavior_change refusal becomes a note as well only when
    those were the only blockers and a structured field ties it to the
    rename: its policy_reference names rename_safety or rename_authorization.
    Never because nothing else was filed: a refusal about stored digest keys
    beside a rename blocker is a compatibility refusal, and it stays, with
    publication_status, as it was. When they
    were the only generation blockers of a confirmed violation, the decision
    becomes the review-only one, as promote_human_decided_blockers_to_review
    does, provided the result validates. Mutates `adjudication`; returns the
    moved items.
    """
    if not isinstance(adjudication, dict):
        return []
    moved: list[dict] = []
    remaining_any = False
    moved_from_generation = False
    for key in ("blocking_for_generation", "blocking_for_publication"):
        items = adjudication.get(key)
        if not isinstance(items, list):
            continue
        kept = []
        for item in items:
            if isinstance(item, dict) and item.get("category") == RENAME_AUTHORIZATION_CATEGORY:
                moved.append({**item, "discharged_as": "rename_question_is_a_note",
                              "moved_from": key})
                moved_from_generation = moved_from_generation or key == "blocking_for_generation"
            else:
                kept.append(item)
                remaining_any = True
        adjudication[key] = kept
    if not moved:
        return []
    notes = adjudication.setdefault("publication_considerations", [])
    for item in moved:
        notes.append({
            "category": RENAME_AUTHORIZATION_CATEGORY,
            "kind": str(item.get("kind", "")),
            "description": str(item.get("description", "")),
            "supporting_claim_ids": list(item.get("supporting_claim_ids") or []),
        })
    behavior = adjudication.get("authorized_behavior_change")
    about_rename = _refuses_behavior_change(adjudication) and bool(re.search(
        r"\brename_(?:safety|authorization)\b", str(behavior.get("policy_reference", "")),
        re.IGNORECASE))
    if not remaining_any and about_rename:
        notes.append({
            "category": RENAME_AUTHORIZATION_CATEGORY,
            "kind": "authorized_behavior_change",
            "description": str(behavior["description"]).strip(),
            "supporting_claim_ids": [],
        })
        adjudication["authorized_behavior_change"] = {
            **behavior, "authorized": True,
            "refusal_discharged_as_rename_note": True,
        }
    adjudication["discharged_blockers"] = [
        *(adjudication.get("discharged_blockers") or []), *moved,
    ]
    if (adjudication.get("publication_status") == "blocked"
            and not adjudication.get("blocking_for_publication")
            and not _refuses_behavior_change(adjudication)
            and adjudication.get("generation_status") in {"supported", "supported_for_review"}):
        adjudication["publication_status"] = "eligible_after_validation"
    if (moved_from_generation and not adjudication.get("blocking_for_generation")
            and str(adjudication.get("decision", "")) in _HUMAN_DECIDED_PROMOTABLE_DECISIONS
            and adjudication.get("cause_status") == "confirmed"
            and adjudication.get("detector_violation") in {"confirmed", "source_established"}
            and adjudication.get("scope_status") != "incomplete"
            and adjudication.get("decision_claim_ids")):
        candidate = deepcopy(adjudication)
        candidate["decision"] = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
        candidate["generation_status"] = "supported_for_review"
        candidate["publication_status"] = (
            "blocked" if candidate.get("blocking_for_publication") else "eligible_after_validation")
        if extraction is not None and not validate_adjudication_of(
                candidate, extraction, finding or {}):
            adjudication.clear()
            adjudication.update(candidate)
    return moved


ANALYZER_RECONFIRMATION = "analyzer_reconfirmation"
_QUOTED_VALUE = re.compile(r"[\"']([A-Za-z][\w.\-/]{0,40})[\"']")
# A literal named in one of these clauses is what the strategy rules out
# ("do not pin \"TLSv1.2\""), not the value it writes.
_NEGATED_CLAUSE = re.compile(
    r"\b(?:not|never|no|instead\s+of|rather\s+than|avoid\w*|without|forbid\w*|except)\b",
    re.IGNORECASE)


def reporter_allowlist(finding: dict | None) -> list[str]:
    """The values the reporting detector says it accepts, or [].

    Its message's "expecting one of X but found Y" list, else a
    detector_context `expected` column. Each finding has its own: in Fread's
    joined unit the SSLContext selector's list (TLSV1.2, TLSV1.3, TLS) is
    not the SSLParameters list (TLSv1.2, TLSv1.3) of the joined finding.
    """
    from cognalys.utils.pr_text import reported_allowlist

    finding = finding or {}
    listed = reported_allowlist(str(finding.get("error_message") or finding.get("message") or ""))
    if listed:
        return listed
    expected = str((finding.get("detector_context") or {}).get("expected", ""))
    values = [value.strip().strip("{}'\" ") for value in expected.split(",")]
    return [value for value in values if value]


def is_analyzer_reconfirmation(blocker: dict) -> bool:
    """A blocker about whether the reporting analyzer accepts the patch, by
    its structured category (or a kind that is exactly that label)."""
    if not isinstance(blocker, dict):
        return False
    kind = re.sub(r"[\s\-]+", "_", str(blocker.get("kind", "")).strip().lower())
    return (str(blocker.get("category", "")) == ANALYZER_RECONFIRMATION
            or kind == ANALYZER_RECONFIRMATION)


def planned_replacement_values(finding: dict | None, *strategies: str) -> list[str]:
    """The literals a strategy says the patch writes in place of the reported one.

    The quoted values of each strategy text, without the reported value
    itself and without those in a clause that rules a value out.
    """
    from cognalys.utils.pr_text import reported_found_value

    found = reported_found_value(str((finding or {}).get("error_message")
                                     or (finding or {}).get("message") or "")).casefold()
    values: list[str] = []
    for strategy in strategies:
        for clause in re.split(r"[.;:,]\s+|\n", str(strategy or "")):
            for match in _QUOTED_VALUE.finditer(clause):
                if _NEGATED_CLAUSE.search(clause[:match.start()]):
                    continue
                value = match.group(1)
                if value.casefold() != found and value not in values:
                    values.append(value)
    return values


def discharge_analyzer_acceptance(
    adjudication: dict, finding: dict | None, patched_values: Iterable[str],
) -> list[dict]:
    """Settle analyzer acceptance from the reporting detector's own message.

    Fread (tls_20261015) was held for review because external research
    surfaced a CrySL rule admitting only TLSv1.2/TLSv1.3, while RVSec's own
    message for the same finding accepted "TLSV1.2,TLSV1.3,TLS" -- and the
    patch wrote "TLS". The detector that reported the finding is the
    authority on what it accepts, above external rule text: when a value the
    patch writes is in the finding's own allowlist, every
    analyzer_reconfirmation blocker (generation or publication) moves to
    discharged_blockers. Only the finding's own list counts; a joined
    finding's list neither discharges nor blocks it. A value outside the
    list, or a message without one, settles nothing.

    Mutates `adjudication`; returns the blockers it discharged.
    """
    if not isinstance(adjudication, dict):
        return []
    allowed = {value.casefold() for value in reporter_allowlist(finding)}
    accepted = [str(value) for value in patched_values or []
                if str(value).casefold() in allowed]
    if not accepted:
        return []
    discharged = []
    for field in ("blocking_for_generation", "blocking_for_publication"):
        items = adjudication.get(field)
        if not isinstance(items, list):
            continue
        kept = []
        for item in items:
            if is_analyzer_reconfirmation(item):
                discharged.append({
                    **item,
                    "discharged_by_reporter_allowlist": True,
                    "discharging_values": accepted,
                    "reporter_allowlist": reporter_allowlist(finding),
                    "discharged_from": field,
                })
            else:
                kept.append(item)
        adjudication[field] = kept
    if discharged:
        adjudication["discharged_blockers"] = [
            *(adjudication.get("discharged_blockers") or []), *discharged,
        ]
    return discharged


NON_SECURITY_CLASSIFICATIONS = frozenset({
    "CONFIRMED_SPEC_VIOLATION_NON_SECURITY_USAGE", "NON_SECURITY_HASH_USAGE",
})


def route_non_security_repair_to_review(adjudication: dict) -> str:
    """Keep a confirmed non-security violation the adjudicator would repair.

    Neither non-security label generates a patch, so an adjudication that chose
    one while authorising generation was rejected by validate_adjudication,
    and its correction could only satisfy the validator by blocking
    generation: the violation left the pipeline as an issue. A confirmed
    violation without security impact is still repaired here, so the answer
    is moved onto the review-only decision, which generates a patch and never
    applies or publishes it unread. The impact stays recorded in
    runtime_security_impact, and every other requirement of a generating
    decision is still validated afterwards.

    Mutates `adjudication`; returns the decision it replaced, or "".
    """
    decision = str(adjudication.get("decision", ""))
    if (decision not in NON_SECURITY_CLASSIFICATIONS
            or adjudication.get("generation_status") not in {
                "supported", "supported_for_review"}
            or adjudication.get("detector_violation") not in {
                "confirmed", "source_established"}):
        return ""
    adjudication["decision"] = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
    adjudication["generation_status"] = "supported_for_review"
    return decision


# Decisions a set of human-decided blockers is promoted from. The dependency
# and upstream policy decisions are left alone: rewriting them into the
# generic review decision would discard the family semantics their gates use.
_HUMAN_DECIDED_PROMOTABLE_DECISIONS = frozenset({
    "NO_SAFE_FIX", "MULTI_FILE_FIX_REQUIRES_POLICY",
})


def mark_single_round_when_correct_use(candidate: dict) -> None:
    """Give a promotion of a correct-use adjudication one draft round.

    AntennaPod's AntennaPodSslSocketFactory.<init> (misuse5 run_v2): the
    adjudicator answered NO_SAFE_FIX with current_code_is_correct_use and a
    reason (the documented null KeyManager[] of SSLContext.init), and the
    promotion to review still ran the full loop twice -- six drafts and a
    replan, 22 calls, US$ 0.92 -- to reach the same "sem impacto" the
    sibling finding reached in 6 calls. The promotion stays, so a wrong
    correct-use answer still gets a draft and the behaviour review; it is
    marked `single_round`, and the generator then makes one draft with no
    semantic retries and no replan after regressions.
    """
    if (candidate.get("current_code_is_correct_use") is True
            and str(candidate.get("correct_use_reason", "")).strip()):
        candidate["single_round"] = True


def promote_human_decided_blockers_to_review(
    adjudication: dict, extraction: dict, finding: dict, family: str = "",
) -> list[str]:
    """Generate a confirmed violation for review when only a human blocks it.

    A confirmed violation was closed without a patch whenever the adjudicator
    listed, as generation blockers, questions no evidence in the packet could
    ever answer: a maintainer's authorisation, behaviour on a running device,
    whether persisted data or an external consumer keeps working. Those are
    what a reviewer reading a patch decides, and blocking generation on them
    left nothing to review. omninotes' DES encryption and a dozen other
    confirmed findings in the stored runs ended that way. promote_grounded_
    api_migration_review had already made the same move for API migrations.

    So when the cause and the violation are confirmed, the scope is not known
    to be incomplete, and every generation blocker carries one of
    HUMAN_DECIDED_BLOCKER_CATEGORIES, those blockers move to publication and
    the decision becomes the review-only one: the patch is generated and never
    applied or published unread. One blocker in any other category -- an
    unconfirmed cause, missing source, an unresolved target, or a record
    without categories -- leaves the adjudication as it was. So does a
    violation the adjudicator found not_confirmed: a finding matching a false
    positive shape its specification documents, whose "authorisation" blocker
    is the adjudicator describing a change that would make the software
    worse, is answered not_confirmed, and dismissal_justification_issues
    checks what that answer rests on.

    The promoted answer must pass validate_adjudication_of before it
    replaces the original. Mutates `adjudication`; returns the categories
    moved, or [] when nothing changed.
    """
    if family == "dependency":
        return []
    if str(adjudication.get("decision", "")) not in \
            _HUMAN_DECIDED_PROMOTABLE_DECISIONS:
        return []
    if (adjudication.get("cause_status") != "confirmed"
            or adjudication.get("detector_violation") not in {
                "confirmed", "source_established"}
            or adjudication.get("scope_status") == "incomplete"
            or not adjudication.get("decision_claim_ids")):
        return []
    blockers = [
        item for item in adjudication.get("blocking_for_generation") or []
        if isinstance(item, dict)
    ]
    if not blockers or any(
        str(item.get("category", "")) not in HUMAN_DECIDED_BLOCKER_CATEGORIES
        for item in blockers
    ):
        return []
    candidate = deepcopy(adjudication)
    candidate["blocking_for_publication"] = [
        *(item for item in candidate.get("blocking_for_publication") or []
          if isinstance(item, dict)),
        *(dict(item, discharged_by_pipeline_validation=False)
          for item in blockers),
    ]
    candidate["blocking_for_generation"] = []
    candidate["decision"] = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
    candidate["generation_status"] = "supported_for_review"
    candidate["publication_status"] = "blocked"
    mark_single_round_when_correct_use(candidate)
    if validate_adjudication_of(candidate, extraction, finding):
        return []
    adjudication.clear()
    adjudication.update(candidate)
    return sorted({str(item["category"]) for item in blockers})


def independent_finding_authority(error: dict | None) -> dict:
    """Who, other than the adjudicator's reading of the source, reports the defect.

    A published advisory, when the finding was analysed at the revision the
    advisory describes: the project confirmed the defect there and shipped a
    repair. Read at the current branch the same advisory may already be
    fixed, so an unpinned finding has no such authority. Returns {} when
    there is none.
    """
    data = error or {}
    identifiers = advisory_identifiers(data)
    revision = str(data.get("pinned_revision", "") or "").strip()
    if not identifiers or not revision:
        return {}
    return {
        "kind": "published_advisory",
        "identifiers": identifiers,
        "revision": revision,
    }


# Generation blockers an independent report of the defect settles
# (cause_unconfirmed), or that a reviewer settles by reading the patch.
AUTHORITY_SETTLED_BLOCKER_CATEGORIES = (
    frozenset({"cause_unconfirmed"}) | HUMAN_DECIDED_BLOCKER_CATEGORIES
)


def promote_authority_confirmed_cause_to_review(
    adjudication: dict, extraction: dict, finding: dict, authority: dict,
    family: str = "",
) -> list[str]:
    """Generate for review a defect a published advisory already confirms.

    The adjudicator decides cause_status from the source alone, and it is
    right to: a lexical reading proves only the text. But for a finding that
    reports a published advisory and was analysed at the revision the
    advisory describes, whether the defect exists is not open -- its own
    project confirmed it and shipped a repair. Three of the four such
    findings in run_20260927_175748 (CVE-2023-41898, CVE-2023-33188,
    GHSL-2022-046) still abstained, each blocked only on "the cause is not
    established" plus questions a reviewer settles.

    So when every generation blocker is either cause_unconfirmed or one of
    HUMAN_DECIDED_BLOCKER_CATEGORIES, the cause is recorded as confirmed on
    the advisory's authority -- saying so in confirmed_cause and in a review
    assumption, so nobody reads it as a source-level confirmation -- and the
    decision becomes the review-only one: the patch is generated, never
    applied or published unread, and every former blocker stays a
    publication blocker. A blocker about missing source or an unresolved
    target is not something an advisory answers, and leaves the adjudication
    as it was; so does a dependency finding, whose repair is a coordinate the
    advisory does not choose. An answer of not_confirmed is not refused here:
    the advisory is the project's own confirmation of the defect, which
    outweighs a reading of the source.

    The promoted answer must pass validate_adjudication_of before it
    replaces the original. Mutates `adjudication`; returns the categories
    settled, or [] when nothing changed.
    """
    if not authority or family == "dependency":
        return []
    if str(adjudication.get("decision", "")) not in \
            _HUMAN_DECIDED_PROMOTABLE_DECISIONS:
        return []
    if (adjudication.get("cause_status") not in {"inferred", "unknown"}
            or not adjudication.get("decision_claim_ids")):
        return []
    blockers = [
        item for item in adjudication.get("blocking_for_generation") or []
        if isinstance(item, dict)
    ]
    categories = {str(item.get("category", "")) for item in blockers}
    if ("cause_unconfirmed" not in categories
            or not categories <= AUTHORITY_SETTLED_BLOCKER_CATEGORIES):
        return []
    identifiers = ", ".join(authority.get("identifiers") or [])
    target = finding.get("target") or {}
    location = ".".join(
        part for part in (str(target.get("class", "")).rsplit(".", 1)[-1],
                          str(target.get("method", ""))) if part
    )
    source_reading = str(adjudication.get("confirmed_cause", "")).strip()
    candidate = deepcopy(adjudication)
    candidate["cause_status"] = "confirmed"
    candidate["detector_violation"] = "confirmed"
    candidate["cause_authority"] = dict(authority)
    candidate["confirmed_cause"] = (
        f"Published advisory {identifiers} reports this defect in "
        f"{location or 'the reported method'} at the analysed revision "
        f"{str(authority.get('revision', ''))[:12]}: "
        f"{str(finding.get('error_message', '')).strip()}"
    )
    candidate["blocking_for_publication"] = [
        *(item for item in candidate.get("blocking_for_publication") or []
          if isinstance(item, dict)),
        *(dict(item, discharged_by_pipeline_validation=False)
          for item in blockers),
    ]
    candidate["blocking_for_generation"] = []
    candidate["decision"] = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
    candidate["generation_status"] = "supported_for_review"
    candidate["publication_status"] = "blocked"
    candidate["review_assumptions"] = [
        *(item for item in candidate.get("review_assumptions") or []
          if isinstance(item, str)),
        f"The cause rests on the published advisory {identifiers}, not on "
        "the source reading alone, which did not establish it"
        + (f" ({source_reading[:300]})" if source_reading else "")
        + ". A reviewer must confirm the change addresses what the advisory "
        "describes.",
    ]
    if validate_adjudication_of(candidate, extraction, finding):
        return []
    adjudication.clear()
    adjudication.update(candidate)
    return sorted(categories)


def provider_iv_settlement(derived_facts: dict | None, adjudication: dict,
                           detector_context: dict | None = None,
                           joint_findings: list | None = None) -> str:
    """Why a blocked IV finding is a violation without security impact, or "".

    Cryptomator Lite run_v6 (2026-10-30): "the IV material was not observed
    to come from a randomized source" at CipherGCM.getIvParameterSpec, whose
    spec reaches only BaseCipher's decryptions while BaseCipher encrypts with
    init(ENCRYPT_MODE, key) and keeps cipher.getIV(). The adjudicator blocked
    on "the upstream IV producer is not supplied", and the evidence-gap
    promotion then generated, on the runtime observation alone, a SecureRandom
    IV for an AndroidKeyStore key that refuses it. The report was not an
    observation of a defect: the monitor saw no producer it tracks, and the
    provider's own random source is one it cannot see (Amethyst#4642 was
    closed for the same reason).

    The predicate is iv_provenance (extraction.iv_provenance): every IV spec
    of the method reaches only decryptions, and the encryption in those files
    lets the provider choose the IV and keeps it. It applies only to an
    adjudication that does not generate and whose cause the source did not
    confirm: an adjudicator that established a defect from the code keeps its
    own outcome, and a generating one meets the patch check
    (semantic_validation.provider_iv_replaced). The rationale is the fact's
    own statement and, when the detector recorded one, its account of what
    the report means.

    Never for a finding analysed together with others (joint_findings): the
    fact covers this method's IV spec only, and the unit's one ending would
    settle the others too. Beta run_v2 (2026-10-25) joined beginUnwrap
    [CipherSpec] ("no generator of the key given to Cipher.init was
    observed") into beginUnwrap [GCMParameterSpecSpec]; a key report is not
    answered by where the IV comes from.
    """
    fact = (derived_facts or {}).get("iv_provenance") if isinstance(derived_facts, dict) else None
    if not isinstance(fact, dict) or fact.get("status") != "provider_generated":
        return ""
    if joint_findings:
        return ""
    if str(adjudication.get("generation_status", "")) in {"supported", "supported_for_review"}:
        return ""
    if (adjudication.get("cause_status") == "confirmed"
            and adjudication.get("detector_violation") in {"confirmed", "source_established"}
            and (adjudication.get("cause_authority") or {}).get("kind") != "runtime_observation"):
        return ""
    reason = str(fact.get("statement", "")).strip()
    meaning = str((detector_context or {}).get("label_meaning", "")).strip()
    if meaning:
        reason += f" What the detector's report means: {meaning}"
    return reason


# Generation blockers about evidence the packet lacked: what a reviewer reading
# the patch next to the code settles, once the violation itself is reported.
EVIDENCE_GAP_BLOCKER_CATEGORIES = frozenset({
    "code_evidence_missing", "target_unresolved", "cause_unconfirmed",
})

# The gaps an echo's producer settles: what caused the echo's predicate to
# fail is that the producer was reported, and the producer is in the unit.
# Which file to change (target_unresolved) is a different question.
PRODUCER_SETTLED_GAP_CATEGORIES = frozenset({"code_evidence_missing", "cause_unconfirmed"})


def promote_evidence_gap_to_review(
    adjudication: dict, extraction: dict, finding: dict,
    runtime_observed: bool = False, family: str = "",
    echo_producer: dict | None = None,
) -> list[str]:
    """Generate for review a reported violation blocked only on missing evidence.

    In exp_final_20261019 eight of the fifteen findings left without a patch
    were closed by the adjudicator for evidence it did not have: where
    FairEmail's `hash` parameter is chosen (code_evidence_missing,
    target_unresolved), which file carries RedReader's IV, whether FreeOTP's
    cipher use breaks the rule (cause_unconfirmed). None of them was judged
    not to be a violation, and the post-adjudication search found nothing
    more in the checkout. Closing them left the maintainer nothing to read;
    a patch for review, publication blocked, puts the question in front of
    the person who can answer it.

    Two cases are promoted, both review-only and never applied unread:
    - the cause and the violation are confirmed and every generation blocker
      is an evidence gap or a question a human decides (an incomplete scope
      is allowed: review-only generation waives it);
    - the cause is not confirmed, but a dynamic analyzer observed the
      violating call while the app ran, and the adjudicator did not find the
      report false. The cause is then recorded on that observation, said so
      in confirmed_cause and in a review assumption.
    A violation the adjudicator found not_confirmed (which is how a finding
    matching a false-positive shape its specification documents is answered)
    and a dependency finding are left as they were. Every former blocker becomes a
    publication blocker.

    An echo (`echo_producer`, generate_error_fixes.join_findings_of_one_
    sequence) takes its cause from the producer finding joined with it, whose
    result reaches the reported call: Salty's encode (CipherSpec,
    upstream-refused) was adjudicated "the formal cause of the missing
    key-provenance predicate is not settled" while deriveKey, the
    PBEKeySpecSpec finding that builds that key, was in the same unit. When
    the adjudicator left the cause open, the cause is recorded as the
    producer's, and its cause gaps (PRODUCER_SETTLED_GAP_CATEGORIES) are
    discharged by that finding instead of becoming publication blockers.
    This attributes a cause; it moves nothing towards a dismissal.

    The promoted answer must pass validate_adjudication_of. Mutates
    `adjudication`; returns the categories moved, or [] when nothing changed.
    """
    if family == "dependency":
        return []
    if str(adjudication.get("decision", "")) not in \
            _HUMAN_DECIDED_PROMOTABLE_DECISIONS:
        return []
    if (adjudication.get("detector_violation") == "not_confirmed"
            or not adjudication.get("decision_claim_ids")):
        return []
    blockers = [
        item for item in adjudication.get("blocking_for_generation") or []
        if isinstance(item, dict)
    ]
    categories = {str(item.get("category", "")) for item in blockers}
    if (not categories & EVIDENCE_GAP_BLOCKER_CATEGORIES
            or not categories <= (EVIDENCE_GAP_BLOCKER_CATEGORIES
                                  | HUMAN_DECIDED_BLOCKER_CATEGORIES)):
        return []
    confirmed = (adjudication.get("cause_status") == "confirmed"
                 and adjudication.get("detector_violation") in {
                     "confirmed", "source_established"})
    if not confirmed and not runtime_observed:
        return []
    producer = (echo_producer if isinstance(echo_producer, dict)
                and str(echo_producer.get("method", "")).strip()
                and adjudication.get("cause_status") != "confirmed" else None)
    settled = [item for item in blockers
               if producer and item.get("category") in PRODUCER_SETTLED_GAP_CATEGORIES]
    blockers = [item for item in blockers if all(item is not done for done in settled)]
    candidate = deepcopy(adjudication)
    gaps = "; ".join(str(item.get("description", ""))[:200] for item in blockers
                     if item.get("category") in EVIDENCE_GAP_BLOCKER_CATEGORIES)
    assumptions = [item for item in candidate.get("review_assumptions") or []
                   if isinstance(item, str)]
    if producer:
        source_reading = str(adjudication.get("confirmed_cause", "")).strip()
        binding = str(producer.get("binding", "")).strip()
        reaches = str(producer.get("reaches", "")).strip()
        candidate["cause_status"] = "confirmed"
        candidate["detector_violation"] = "confirmed"
        candidate["cause_authority"] = {
            "kind": "producer_finding",
            **{key: str(producer.get(key, "")) for key in
               ("specification", "method", "error_message", "binding", "reaches")},
        }
        candidate["confirmed_cause"] = (
            f"{str(finding.get('error_message', '')).strip()}: the value reaching "
            f"this call is produced by {producer.get('method', '')}, where "
            f"{producer.get('specification', '') or 'the detector'} reported: "
            f"{str(producer.get('error_message', '')).strip()}"
            + (f" ({binding} holds its result and reaches `{reaches}`)" if binding and reaches
               else f" (its result reaches `{reaches}`)" if reaches else "")
        )
        assumptions.append(
            "This finding repeats the report of its producer, "
            f"{producer.get('method', '')}, analysed in the same change; the "
            "cause is that finding's"
            + (f", not the source reading of this one ({source_reading[:300]})"
               if source_reading else "")
            + ". A reviewer must confirm the change repairs the producer."
        )
        candidate["discharged_blockers"] = [
            *(candidate.get("discharged_blockers") or []),
            *(dict(item, discharged_by_producer_finding=True,
                   discharging_finding=str(producer.get("method", "")))
              for item in settled),
        ]
    elif not confirmed:
        source_reading = str(adjudication.get("confirmed_cause", "")).strip()
        candidate["cause_status"] = "confirmed"
        candidate["detector_violation"] = "confirmed"
        candidate["cause_authority"] = {"kind": "runtime_observation"}
        candidate["confirmed_cause"] = (
            "The dynamic analysis observed this violation while the app ran: "
            f"{str(finding.get('error_message', '')).strip()}"
        )
        assumptions.append(
            "The cause rests on the runtime observation, not on the source "
            "reading, which did not establish it"
            + (f" ({source_reading[:300]})" if source_reading else "")
            + ". A reviewer must confirm the reported call is the one changed."
        )
    if gaps:
        assumptions.append(
            "Generated for review although the analysis lacked evidence: " + gaps
            + ". A reviewer must confirm the change is in the right place."
        )
    candidate["review_assumptions"] = assumptions
    candidate["blocking_for_publication"] = [
        *(item for item in candidate.get("blocking_for_publication") or []
          if isinstance(item, dict)),
        *(dict(item, discharged_by_pipeline_validation=False)
          for item in blockers),
    ]
    candidate["blocking_for_generation"] = []
    candidate["decision"] = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
    candidate["generation_status"] = "supported_for_review"
    candidate["publication_status"] = "blocked"
    candidate["evidence_gap_promoted"] = sorted(
        {str(item.get("category", "")) for item in blockers}
        & EVIDENCE_GAP_BLOCKER_CATEGORIES) if producer else sorted(categories)
    mark_single_round_when_correct_use(candidate)
    if validate_adjudication_of(candidate, extraction, finding):
        return []
    adjudication.clear()
    adjudication.update(candidate)
    return sorted(categories)


# The objections validate_adjudication raises against a confirmed repair only
# because generation was claimed without the compatibility or blocker state
# automatic generation needs. A reviewer reading the patch settles each one,
# which is exactly what review-only generation provides.
REVIEW_SETTLED_ADJUDICATION_ISSUES = frozenset({
    "automatic generation requires preserved compatibility",
    "supported decision has generation blockers",
})


def route_unsettled_compatibility_to_review(
    adjudication: dict, issues: list[str], extraction: dict, finding: dict,
) -> bool:
    """Keep a confirmed repair whose compatibility only a reviewer can settle.

    org.bombusmod's Strconv.baosFromBase64 closes a local
    ByteArrayOutputStream before returning it (run_20260915_175048). All three
    adjudication answers confirmed the cause and the detector violation and
    asked for the removal. Each was rejected only for its generation claim --
    compatibility "unknown" or "regression_risk" under automatic generation,
    then review generation that listed its compatibility question as a
    generation blocker -- and when the corrections ran out every field was
    overwritten with NO_SAFE_FIX and an unknown cause, although the cause was
    never in dispute.

    So when every remaining objection is one of
    REVIEW_SETTLED_ADJUDICATION_ISSUES, cause and violation are confirmed with
    supporting claims, the scope is not known to be incomplete, and every
    generation blocker carries a HUMAN_DECIDED_BLOCKER_CATEGORIES category,
    the adjudication becomes review-only generation: the blockers and the
    unsettled compatibility move to publication, where nothing is published
    unread. Any other objection, or a violation not confirmed (a finding
    matching a false-positive shape its specification documents is answered
    not_confirmed), leaves the adjudication as it was.

    The routed answer must pass validate_adjudication_of. Mutates
    `adjudication`; returns True when it was routed.
    """
    if not issues:
        return False
    if any(issue not in REVIEW_SETTLED_ADJUDICATION_ISSUES for issue in issues):
        return False
    if str(adjudication.get("decision", "")) not in GENERATING_DECISIONS:
        return False
    if (adjudication.get("cause_status") != "confirmed"
            or not str(adjudication.get("confirmed_cause", "")).strip()
            or adjudication.get("detector_violation") not in {
                "confirmed", "source_established"}
            or adjudication.get("scope_status") == "incomplete"
            or not adjudication.get("decision_claim_ids")):
        return False
    blockers = [
        item for item in adjudication.get("blocking_for_generation") or []
        if isinstance(item, dict)
    ]
    if any(
        str(item.get("category", "")) not in HUMAN_DECIDED_BLOCKER_CATEGORIES
        for item in blockers
    ):
        return False
    candidate = deepcopy(adjudication)
    moved = [dict(item, discharged_by_pipeline_validation=False) for item in blockers]
    compatibility = str(adjudication.get("compatibility_status", "")) or "unknown"
    if compatibility != "preserved":
        moved.append({
            "kind": "compatibility_unsettled",
            "category": "compatibility_contract",
            "description": (
                f"compatibility_status is {compatibility}: a reviewer confirms "
                "the change preserves existing behaviour before publication"
            ),
            "supporting_claim_ids": list(adjudication.get("decision_claim_ids") or []),
            "discharged_by_pipeline_validation": False,
        })
    candidate["blocking_for_publication"] = [
        *(item for item in candidate.get("blocking_for_publication") or []
          if isinstance(item, dict)),
        *moved,
    ]
    candidate["blocking_for_generation"] = []
    candidate["decision"] = "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW"
    candidate["generation_status"] = "supported_for_review"
    candidate["publication_status"] = "blocked"
    if validate_adjudication_of(candidate, extraction, finding):
        return False
    adjudication.clear()
    adjudication.update(candidate)
    return True


def section_telemetry(sections: dict[str, Any]) -> dict:
    return {
        name: {
            "characters": len(value) if isinstance(value, str) else len(canonical_json(value)),
            "approx_tokens": max(
                1, round((len(value) if isinstance(value, str)
                          else len(canonical_json(value))) / 4)
            ),
            "sha256": hashlib.sha256(
                (value if isinstance(value, str) else canonical_json(value)).encode("utf-8")
            ).hexdigest(),
        }
        for name, value in sections.items()
    }
