#!/usr/bin/env python3
"""Read one finding's artifacts into typed axes, or say it could not.

The previous normalizer produced a flat record in which every field had a
value, including the fields it had guessed. `runtime_security_impact` came
from searching the whole artifact, lowercased, for English phrases like
"not established"; `detector_violation` fell back to a predicate scan; the
repairability came from substrings of the decision name. Nothing recorded
which values were read and which were inferred, so a report could not
distinguish "the pipeline concluded X" from "the evaluator guessed X".

Here every axis is read from the field that carries it, and its absence is
UNKNOWN -- a value in its own right, counted separately, never silently
mapped onto a real answer. Inference is not performed at all: an evaluator
that infers is an evaluator that can agree with itself about a run that
never happened.
"""
from __future__ import annotations

from typing import Any, Dict, List


from cognalys.evaluation.decisions import INVALID, action_for  # noqa: E402


UNKNOWN = "unknown"

# Scope of the change the plan describes, from the plan itself.
SINGLE_FILE = "single_file"
MULTI_FILE = "multi_file"
EXTERNAL_CONTRACT = "external_contract"


def _analysis(value: Dict) -> Dict:
    """The analysis record, wherever this artifact keeps it.

    Three shapes are in the corpus and all three are read here. A solution
    record nests it under `evidence_analysis`; an evidence review writes it
    under `analysis`; an adjudication artifact *is* it. Reading only the
    first -- which is what the previous evaluator did -- made every
    evidence review look like a finding with no decision, and a finding with
    no decision used to normalize to a supported repair.
    """
    for key in ("evidence_analysis", "analysis"):
        candidate = value.get(key)
        if isinstance(candidate, dict) and candidate:
            return candidate
    return value


def _adjudication(value: Dict) -> Dict:
    """The adjudication record, wherever this artifact keeps it."""
    analysis = _analysis(value)
    v2 = analysis.get("v2", {})
    if not isinstance(v2, dict):
        v2 = {}
    for candidate in (v2.get("adjudication"), value.get("adjudication")):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return analysis


def _claims(value: Dict) -> List[Dict]:
    analysis = _analysis(value)
    v2 = analysis.get("v2", {})
    for candidate in (
        v2.get("claims") if isinstance(v2, dict) else None,
        value.get("claims"),
        analysis.get("claims"),
    ):
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _plan(value: Dict) -> Dict:
    analysis = _analysis(value)
    v2 = analysis.get("v2", {})
    for candidate in (
        v2.get("patch_plan") if isinstance(v2, dict) else None,
        value.get("patch_plan"),
        analysis.get("coordinated_fix_plan"),
        value.get("authorized_patch_plan"),
    ):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _evidence_ids(value: Dict) -> set:
    """Evidence IDs the packet backing this artifact actually offers.

    Empty when the artifact does not carry its packet, which is a different
    statement from "the claims cite nothing valid" and is reported as such.
    """
    found = set()
    for container in (
        value.get("v2_artifacts", {}),
        value.get("evidence_packet", {}),
        value.get("packet", {}),
        value,
    ):
        if not isinstance(container, dict):
            continue
        for key in ("evidence_catalog", "external_evidence_catalog"):
            catalog = container.get(key)
            if isinstance(catalog, list):
                found |= {
                    str(item.get("evidence_id", ""))
                    for item in catalog if isinstance(item, dict)
                }
    return {item for item in found if item}


def _text_value(source: Dict, key: str) -> str:
    value = source.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return UNKNOWN


def claim_provenance(value: Dict) -> Dict:
    """Three separate facts about how the confirmed claims are grounded.

    `unsupported` are confirmed claims citing nothing at all -- the only
    thing the old check looked at. `dangling` are claims citing an evidence
    ID the packet does not contain, which the pipeline's own validator
    rejects (pipeline_architecture.validate_evidence_extraction) but the
    evaluator accepted. `catalog_available` says whether the question could
    be asked at all: without the packet, "no dangling references" means
    "nothing was checked", and reporting those as the same number is how an
    evaluator congratulates itself.
    """
    claims = _claims(value)
    catalog = _evidence_ids(value)
    confirmed = [
        claim for claim in claims
        if claim.get("epistemic_status") == "confirmed"
    ]
    unsupported = sorted(
        str(claim.get("claim_id", "")) for claim in confirmed
        if not claim.get("evidence_ids")
    )
    dangling = []
    if catalog:
        for claim in confirmed:
            cited = {
                str(item) for item in claim.get("evidence_ids", [])
                if isinstance(item, (str, int))
            }
            missing = sorted(cited - catalog)
            if missing:
                dangling.append(
                    {"claim_id": str(claim.get("claim_id", "")),
                     "unknown_evidence_ids": missing}
                )
    return {
        "confirmed_claims": len(confirmed),
        "catalog_available": bool(catalog),
        "unsupported_claim_ids": unsupported,
        "dangling_references": dangling,
    }


def normalize(value: Dict) -> Dict:
    """Typed axes for one finding's artifact.

    Every axis is UNKNOWN unless the artifact states it. `action` is the one
    derived field, and it is derived exhaustively (see evaluation.decisions).
    """
    if not isinstance(value, dict):
        value = {}
    adjudication = _adjudication(value)
    plan = _plan(value)
    decision = adjudication.get("decision", value.get("decision", ""))
    decision = decision if isinstance(decision, str) else ""
    paths = sorted({
        str(item.get("source_file_path", ""))
        for item in plan.get("required_edits", [])
        if isinstance(item, dict) and str(item.get("source_file_path", ""))
    })
    scope_assessment = adjudication.get("scope_assessment", {})
    if not isinstance(scope_assessment, dict):
        scope_assessment = {}
    if action_for(decision) == INVALID and not decision:
        scope = UNKNOWN
    elif decision in {"EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX"}:
        scope = EXTERNAL_CONTRACT
    elif len(paths) > 1 or scope_assessment.get("multi_file_change_required"):
        scope = MULTI_FILE
    elif paths:
        scope = SINGLE_FILE
    else:
        scope = UNKNOWN
    return {
        "decision": decision,
        "action": action_for(decision),
        "detector_violation": _text_value(adjudication, "detector_violation"),
        "runtime_security_impact": _text_value(
            adjudication, "runtime_security_impact"
        ),
        "cause_status": _text_value(adjudication, "cause_status"),
        "generation_status": _text_value(adjudication, "generation_status"),
        "publication_status": _text_value(adjudication, "publication_status"),
        "scope": scope,
        "edit_paths": paths,
        "confirmed_claim_ids": sorted(
            str(claim.get("claim_id", "")) for claim in _claims(value)
            if claim.get("epistemic_status") == "confirmed"
        ),
        "claim_statements": sorted(
            str(claim.get("statement", "")).strip().lower()
            for claim in _claims(value)
            if str(claim.get("statement", "")).strip()
        ),
        "provenance": claim_provenance(value),
    }


def axis_names() -> tuple:
    """Axes a manifest case may state an expectation for."""
    return (
        "action", "detector_violation", "runtime_security_impact",
        "cause_status", "generation_status", "publication_status", "scope",
    )


def describe(value: Any) -> str:
    return "" if value is None else str(value)
