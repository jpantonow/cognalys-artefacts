#!/usr/bin/env python3
"""The conditions a comparison runs, and what each one is allowed to use.

"The pipeline abstained on 57 findings" is not a result until something else
has been asked the same question under the same limits. Without a baseline
the reported numbers describe one system's behaviour and support no claim
that its structure -- separated evidence, adjudication and generation --
produced them.

Four conditions, defined once here so the runner, the report and the paper
cannot drift apart:

  B0  abstain on everything, with no model call at all. It sets the floor:
      whatever a condition scores, B0 scores it for free, and a pipeline
      that cannot beat B0 is doing nothing useful. It also produces no
      incorrect patch, which is exactly why "never proposed a bad patch"
      cannot be reported as a quality on its own.
  B1  one model call carrying the method, the finding and the rule, free to
      abstain. The comparison that matters: what does the structure add
      over asking the same model the same question directly?
  G   the full pipeline.
  G-  an ablation of G with one component removed, named by the flag that
      removes it.

Every condition gets the same cohort, the same model, the same budgets and
the same final checks. Baselines are allowed to abstain: forcing them to
answer would compare a system that may say "I don't know" with one that may
not, and the difference would be read as quality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


ABSTAIN_NOTHING_RUNS = "B0"
SINGLE_CALL = "B1"
FULL_PIPELINE = "G"


@dataclass(frozen=True)
class Condition:
    """One arm of the comparison."""

    name: str
    description: str
    # How the runner executes it: "internal" is answered by this repository
    # without the pipeline; "pipeline" shells out to run_pipeline.py.
    executor: str
    pipeline_flags: Tuple[str, ...] = field(default_factory=tuple)
    uses_model: bool = True

    def as_record(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "executor": self.executor,
            "pipeline_flags": list(self.pipeline_flags),
            "uses_model": self.uses_model,
        }


CONDITIONS: Dict[str, Condition] = {
    ABSTAIN_NOTHING_RUNS: Condition(
        name=ABSTAIN_NOTHING_RUNS,
        description=(
            "Abstain on every finding. No model call, no patch. The floor "
            "every other condition has to clear."
        ),
        executor="internal",
        uses_model=False,
    ),
    SINGLE_CALL: Condition(
        name=SINGLE_CALL,
        description=(
            "One model call with the reported method, the finding and the "
            "rule, free to abstain. What the structured pipeline is "
            "compared against."
        ),
        executor="internal",
    ),
    FULL_PIPELINE: Condition(
        name=FULL_PIPELINE,
        description="The full pipeline under its frozen manifest.",
        executor="pipeline",
    ),
    "G-no-web": Condition(
        name="G-no-web",
        description=(
            "The full pipeline with external research disabled: what the "
            "external sources contribute."
        ),
        executor="pipeline",
        pipeline_flags=("--disable-llm-web-search",),
    ),
    "G-no-multifile": Condition(
        name="G-no-multifile",
        description=(
            "The full pipeline with the structured multi-file relations "
            "reduced to local context: what the structured context "
            "contributes."
        ),
        executor="pipeline",
        pipeline_flags=("--repository-context-budget", "0"),
    ),
}


def resolve(names: List[str]) -> List[Condition]:
    """Named conditions, in the order given, or a named error."""
    unknown = [name for name in names if name not in CONDITIONS]
    if unknown:
        raise KeyError(
            f"unknown condition(s): {', '.join(sorted(unknown))}; "
            f"available: {', '.join(sorted(CONDITIONS))}"
        )
    return [CONDITIONS[name] for name in names]


def interleaved_plan(conditions: List[Condition], findings: List[str]) -> List[Dict]:
    """Finding-major order, so no condition owns a period of the day.

    Running one condition to completion and then the next makes every
    difference between them also a difference in when they ran: a provider
    that changed behaviour, a rate limit, a machine under different load.
    Interleaving does not remove that, but it stops it from lining up with
    the thing being measured.
    """
    plan = []
    for index, finding in enumerate(findings):
        # The condition order rotates per finding, so no arm is
        # systematically first.
        rotated = conditions[index % len(conditions):] + conditions[:index % len(conditions)]
        for condition in rotated:
            plan.append({"finding_id": finding, "condition": condition.name})
    return plan


def abstention_record(finding_id: str, condition: str) -> Dict:
    """The artifact B0 writes: an explicit abstention, not an empty file.

    It is written in the same shape the pipeline's adjudication artifacts
    use, so every downstream reader -- the evaluator, the lineage, the
    coverage report -- treats B0 as a condition rather than as missing data.
    """
    return {
        "finding_id": finding_id,
        "condition": condition,
        "decision": "NO_SAFE_FIX",
        "cause_status": "unknown",
        "detector_violation": "unknown",
        "runtime_security_impact": "unknown",
        "generation_status": "blocked",
        "publication_status": "blocked",
        "confirmed_cause": "",
        "blocking_for_generation": [{
            "kind": "condition_abstains_by_construction",
            "category": "not_attempted",
            "description": (
                "Condition B0 abstains on every finding without examining "
                "it. This is the floor of the comparison, not a judgement "
                "about this finding."
            ),
            "blocking_for_generation": True,
            "blocking_for_publication": True,
        }],
    }
