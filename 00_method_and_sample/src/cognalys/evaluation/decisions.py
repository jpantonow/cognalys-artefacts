#!/usr/bin/env python3
"""What each adjudicated decision means, for evaluation rather than execution.

The evaluator used to derive this from substrings of the decision name:

    repairability = (
        "no_safe_fix" if decision == "NO_SAFE_FIX" else
        "policy_or_review" if "POLICY" in decision else
        "review_only" if "REVIEW" in decision else "supported"
    )

Three things follow from that `else`, and all three were wrong. An empty
decision -- a crashed run, a truncated artifact, a case the pipeline never
reached -- scored as a supported repair. So did
EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX, which is the opposite: a
confirmed finding whose repair cannot be made on the client side at all. And
any decision added to the schema later would silently join them.

So the mapping is exhaustive and it is derived from the vocabulary the
pipeline already owns in utils.disposition, instead of being restated here.
A decision this module has never heard of is INVALID, never a repair.

Two separate questions are also separated here, because conflating them is
what made the old precision and recall meaningless:

  * what the pipeline DID -- the action, read off the decision;
  * whether that was the right thing to do -- which only the oracle knows,
    and which lives in the labelled worksheet, not in an artifact.

A confirmed finding that is genuinely not repairable in this repository, and
that the pipeline correctly declined to patch, is a correct outcome. Under
the old binary label it was a false negative.
"""
from __future__ import annotations



from cognalys.utils.disposition import (  # noqa: E402
    CLASSIFICATION_DECISIONS,
    POLICY_DECISIONS,
    REPAIR_DECISIONS,
    REVIEW_ONLY_DECISIONS,
)


# What the pipeline did with a finding. Exhaustive over the decision enum in
# llm_schemas, plus INVALID for everything else.
REPAIR_UNATTENDED = "repair_unattended"
REPAIR_FOR_REVIEW = "repair_for_review"
AWAIT_POLICY = "await_policy"
CLASSIFY_NO_CHANGE = "classify_no_change"
ABSTAIN = "abstain"
INVALID = "invalid"

ACTIONS = (
    REPAIR_UNATTENDED, REPAIR_FOR_REVIEW, AWAIT_POLICY,
    CLASSIFY_NO_CHANGE, ABSTAIN, INVALID,
)

# Actions under which a patch exists at the end. Used for coverage, never for
# correctness: a proposal is not a repair until something verifies it.
PROPOSING_ACTIONS = frozenset({REPAIR_UNATTENDED, REPAIR_FOR_REVIEW})

_BY_DECISION: dict[str, str] = {
    **{decision: REPAIR_UNATTENDED for decision in REPAIR_DECISIONS},
    **{decision: REPAIR_FOR_REVIEW for decision in REVIEW_ONLY_DECISIONS},
    **{decision: AWAIT_POLICY for decision in POLICY_DECISIONS},
    **{decision: CLASSIFY_NO_CHANGE for decision in CLASSIFICATION_DECISIONS},
    "NO_SAFE_FIX": ABSTAIN,
}

# Decisions the schema allows that utils.disposition does not name, checked by
# a test rather than assumed. Kept explicit so adding one to the schema
# without deciding what it means here fails loudly instead of defaulting.
KNOWN_DECISIONS = frozenset(_BY_DECISION)


def action_for(decision: object) -> str:
    """The action a decision represents, or INVALID.

    Empty, missing, unknown and non-string decisions are all INVALID. This
    is the whole point: absence of a decision is not evidence of a repair.
    """
    if not isinstance(decision, str) or not decision.strip():
        return INVALID
    return _BY_DECISION.get(decision.strip(), INVALID)


def proposes_change(decision: object) -> bool:
    """Whether this decision ends with a patch to look at."""
    return action_for(decision) in PROPOSING_ACTIONS


def unknown_decisions(decisions: object) -> list[str]:
    """Decisions in `decisions` that this module cannot classify."""
    if not isinstance(decisions, (list, tuple, set, frozenset)):
        return []
    return sorted(
        str(decision) for decision in decisions
        if action_for(decision) == INVALID
    )
