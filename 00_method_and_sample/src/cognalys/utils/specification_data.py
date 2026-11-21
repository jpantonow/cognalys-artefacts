"""What a specification's own data says, for code that must not key on its name.

The 2026-10-17 audit found code branching on specification names --
`if specification == "SSLContextSpec"` choosing a fix strategy, the analysis
family read from substrings of the name, a table of names for the factory
type. Specification-specific knowledge belongs in the specification
(data/specifications/<spec>.json), which is enriched per run; code reads it
generically, so a specification that records the same thing is covered and
one that does not is not.

Curated fields read here (kept across regeneration, see
generate_specifications.CURATED_FIELDS):

- analysis_family: the family whose assessments and decisions apply.
- remediation_policies: [{"when": {"violation_id_contains", "message_pattern"},
  "evidence": name, "policy": {...}, "variants": [...],
  "keeps_reported_literal": bool}]
  A variant's "append" extends lists and adds sentences to prose fields
  (strategy). A policy's "states_facts" names the evidence's
  maintainer_facts the pull request states (migration_policy).
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache


@lru_cache(maxsize=None)
def specification(spec_id: str) -> dict:
    from cognalys.workspace import library_data

    if not spec_id or "/" in spec_id or "\\" in spec_id:
        return {}
    try:
        data = json.loads((library_data("specifications") / f"{spec_id}.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def analysis_family(spec_id: str) -> str:
    return str(specification(spec_id).get("analysis_family", "") or "")


def remediation_entry(error: dict) -> dict:
    """The specification's first remediation entry whose condition the finding meets."""
    spec_id = str(error.get("spec", ""))
    message = str(error.get("error_message", ""))
    error_id = str(error.get("unique_error_id", ""))
    for entry in specification(spec_id).get("remediation_policies") or []:
        when = (entry or {}).get("when") or {}
        needle = str(when.get("violation_id_contains", ""))
        if needle and needle not in error_id:
            continue
        pattern = str(when.get("message_pattern", ""))
        if pattern and not re.search(pattern, message, re.IGNORECASE):
            continue
        return entry
    return {}


def resolved_policy(entry: dict, evidence: dict) -> dict:
    """The entry's policy with every variant whose evidence flag holds applied."""
    policy = copy.deepcopy(entry.get("policy") or {})
    for variant in entry.get("variants") or []:
        if not evidence.get(str(variant.get("when_evidence", ""))):
            continue
        for key, value in (variant.get("set") or {}).items():
            policy[key] = copy.deepcopy(value)
        for key, values in (variant.get("append") or {}).items():
            if isinstance(policy.get(key), str) or isinstance(values, str):
                # A strategy is prose: a variant adds its sentences to it.
                policy[key] = " ".join(part for part in (
                    str(policy.get(key) or ""), str(values)) if part).strip()
            else:
                policy[key] = list(policy.get(key) or []) + list(values)
    return policy


def keeps_reported_literal(spec_id: str) -> bool:
    """Whether a remediation of this specification keeps the reported literal."""
    return any(bool((entry or {}).get("keeps_reported_literal"))
               for entry in specification(spec_id).get("remediation_policies") or [])
