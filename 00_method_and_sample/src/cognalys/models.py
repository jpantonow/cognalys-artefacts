"""The typed values the public API takes and returns.

A run is still configured by the pipeline's own command-line vocabulary
(RunConfig holds those arguments, validated), because that is what every
launcher, profile and test already speaks; grouping it into typed sections
belongs with the configuration file of the CLI. What a run produced is read
back from its artifacts into FindingResult and RunResult.
"""
from __future__ import annotations

import argparse
import contextlib
import io
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Finding:
    """One reported misuse, as a caller hands it to the pipeline.

    The fields are the corpus columns every step reads (`class` is a Python
    keyword, hence class_name). `detector` holds what the detector itself
    recorded and becomes the detector_* columns; `extra` carries any other
    column through unchanged.
    """

    apk: str
    class_name: str
    method: str
    spec: str
    message: str = ""
    repo_url: str = ""
    commit: str = ""
    tool: str = ""
    detector: Mapping[str, str] = field(default_factory=dict)
    extra: Mapping[str, str] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.apk, self.class_name, self.method, self.spec)

    def to_row(self) -> dict:
        row = {
            "apk": self.apk, "class": self.class_name, "method": self.method,
            "spec": self.spec, "message": self.message, "repo_url": self.repo_url,
            "commit": self.commit, "tool": self.tool,
        }
        row.update({f"detector_{name}": value for name, value in self.detector.items()})
        row.update(self.extra)
        return row


@dataclass(frozen=True)
class RunConfig:
    """A run's arguments, in the vocabulary of `scripts/run_pipeline.py`."""

    arguments: tuple[str, ...]

    @classmethod
    def from_legacy_args(cls, argv: Sequence[str]) -> "RunConfig":
        """Validated arguments; a ValueError, not an exit, when they are not."""
        config = cls(tuple(str(item) for item in argv))
        config.namespace()
        return config

    def namespace(self) -> argparse.Namespace:
        from cognalys.orchestration import run_pipeline

        errors = io.StringIO()
        try:
            with contextlib.redirect_stderr(errors):
                return run_pipeline.parse_args(list(self.arguments))
        except SystemExit as exc:
            message = errors.getvalue().strip().splitlines()
            raise ValueError(message[-1] if message else f"invalid arguments ({exc.code})") from None


@dataclass(frozen=True)
class Step:
    """One pipeline step and the command that runs it."""

    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class FindingResult:
    """Where one finding ended, as its run's artifacts record it."""

    apk: str
    csv_class: str
    method: str
    spec: str
    outcome: str
    detail: str = ""
    run: str = ""
    analysed: bool = False
    decision: str = ""
    detector_violation: str = ""
    evidence: str = ""
    solution: bool = False
    apply_status: str = ""
    build_status: str = ""
    pr_ready: bool = False
    pr_title: str = ""
    issue_repository: str = ""

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.apk, self.csv_class, self.method, self.spec)

    @classmethod
    def from_record(cls, key: Sequence[str], record: Mapping) -> "FindingResult":
        from cognalys.reporting.finding_statistics import final_outcome

        outcome, detail = final_outcome(dict(record))
        apk, csv_class, method, spec = key
        return cls(
            apk=apk, csv_class=csv_class, method=method, spec=spec,
            outcome=outcome, detail=detail,
            run=str(record.get("run", "")),
            analysed=bool(record.get("analysed")),
            decision=str(record.get("decision", "") or ""),
            detector_violation=str(record.get("detector_violation", "") or ""),
            evidence=str(record.get("evidence", "") or ""),
            solution=bool(record.get("solution")),
            apply_status=str(record.get("apply_status", "") or ""),
            build_status=str(record.get("build_status", "") or ""),
            pr_ready=bool(record.get("pr_ready")),
            pr_title=str(record.get("pr_title", "") or ""),
            issue_repository=str(record.get("issue_repository", "") or ""),
        )


@dataclass(frozen=True)
class RunResult:
    """Every finding of one run, or of several combined."""

    runs: tuple[Path, ...]
    findings: tuple[FindingResult, ...] = field(default_factory=tuple)
    exit_code: int | None = None

    def outcomes(self) -> Counter:
        return Counter(finding.outcome for finding in self.findings)

    def ready_pull_requests(self) -> tuple[FindingResult, ...]:
        return tuple(finding for finding in self.findings if finding.pr_ready)

    def failing(self, level: str) -> tuple[FindingResult, ...]:
        """The findings that make a run fail at ``level`` (see FAIL_ON_LEVELS)."""
        if level == "none":
            return ()
        if level == "any":
            return tuple(finding for finding in self.findings
                         if finding.outcome not in NOT_A_PROBLEM_OUTCOMES)
        wanted = UNFIXED_OUTCOMES | ({"PR pronto", "PR já aberto"} if level == "confirmed" else set())
        return tuple(finding for finding in self.findings if finding.outcome in wanted)


# --fail-on levels, from the final outcomes of reporting.finding_statistics.
# unfixed: a confirmed violation still in the code without a ready fix;
# confirmed: also the ones with a pull request ready;
# any: everything but a recognised false positive or code fixed upstream.
FAIL_ON_LEVELS = ("none", "unfixed", "confirmed", "any")
UNFIXED_OUTCOMES = frozenset({
    "correção gerada, sem PR",
    "correção não aplicável: arquivo fora do branch de destino",
    "correção proposta, reprovada antes de gerar",
    "correção não gerada",
    "issue para o mantenedor do app",
    "issue para a biblioteca",
    "violação confirmada sem correção segura",
    "contrato externo impede a correção",
    "depende de decisão do mantenedor",
})
NOT_A_PROBLEM_OUTCOMES = frozenset({
    "falso positivo reconhecido",
    "correção não aplicável: código mudou upstream",
})
