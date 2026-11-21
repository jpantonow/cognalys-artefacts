#!/usr/bin/env python3
"""Collect one run's artifacts, deterministically and without mixing runs.

The previous loader walked whatever directory it was given with
`rglob("*.json")`, ranked each file by a substring of its name, and kept it
when `rank >= ranks.get(finding_id, -1)`. Three consequences:

  * `>=` means the last file at the winning rank wins, and "last" is
    filesystem order. Two artifacts of the same kind for one finding -- a
    first attempt and a retry, which this pipeline produces routinely --
    were resolved by whichever sorted later.
  * pointed at `outputs/`, it silently merged every run in the directory,
    so a finding's result could come from one run and its sibling's from
    another.
  * the earlier attempts it passed over were not recorded anywhere, so a
    report could not distinguish "right the first time" from "right on the
    third try", which is exactly the difference a resource budget is
    supposed to expose.

Here a run is named explicitly, precedence between artifact kinds is a
table, ties within a kind are resolved by an ordering that is stated rather
than inherited from the filesystem, and every artifact seen for a finding is
kept in `attempts` so the first attempt and the total cost remain
recoverable.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# Which artifact answers "what did the pipeline decide about this finding?",
# best first. A solution record supersedes an adjudication because it is
# what the run acted on; an adjudication supersedes the evidence review for
# the same reason.
_KIND_PRECEDENCE = (
    ("solution", re.compile(r"_solution\.json$")),
    ("adjudication", re.compile(r"_adjudication\.json$")),
    ("evidence_review", re.compile(r"_evidence\.json$")),
    ("patch_plan", re.compile(r"_patch_plan\.json$")),
    ("evidence_extraction", re.compile(r"_evidence_extraction\.json$")),
)
# Artifacts that record an earlier attempt at something later superseded.
# They are collected, never chosen.
_ATTEMPT_MARKERS = re.compile(
    r"_round\d+\.json$|_attempt\d+\.json$|_shape_retry\.json$"
)
_RUN_DIRECTORY = re.compile(r"^run_\d{8}_\d{6}$")


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def finding_id_of(value: dict) -> str:
    """The finding this artifact is about, from the first id field it carries."""
    for node in _walk(value):
        if isinstance(node, dict):
            for key in ("unique_error_id", "error_id", "finding_id"):
                candidate = node.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
    return ""


# Suffixes that turn an artifact path into its finding's file prefix, so a
# sibling can be consulted for the id.
_ARTIFACT_SUFFIX = re.compile(
    r"_(?:adjudication|patch_plan|evidence_extraction|evidence_packet"
    r"|evidence_supplement|external_research|evidence|solution)"
    r"(?:_round\d+)?\.json$"
)


def sibling_finding_id(path: Path) -> str:
    """The finding id of an artifact that does not carry one.

    The v2 adjudication and patch-plan records hold the verdict but no
    identifier: the id lives in the evidence packet written beside them
    under the same prefix. Without this they are invisible to the loader,
    and the run falls back to a lower-precedence artifact -- which is how a
    finding with a real adjudicated decision was scored from a record that
    had none.
    """
    match = _ARTIFACT_SUFFIX.search(path.name)
    if not match:
        return ""
    prefix = path.name[: match.start()]
    for sibling_name in (
        f"{prefix}_evidence_packet.json", f"{prefix}_evidence.json",
    ):
        sibling = path.with_name(sibling_name)
        if not sibling.is_file():
            continue
        try:
            value = json.loads(sibling.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            found = finding_id_of(value)
            if found:
                return found
    return ""


def _kind(name: str) -> tuple:
    """(rank, kind) for a file name; rank 0 means "not a decision artifact"."""
    if _ATTEMPT_MARKERS.search(name):
        return 0, "attempt"
    for index, (kind, pattern) in enumerate(_KIND_PRECEDENCE):
        if pattern.search(name):
            return len(_KIND_PRECEDENCE) - index, kind
    return 0, "other"


def resolve_run_root(path: Path, run_id: str = "") -> Path:
    """The single run directory to read, or a refusal explaining the choice.

    Pointing this at a directory holding several runs is the mistake that
    merged them, so it is an error rather than a default.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such directory: {path}")
    if run_id:
        candidate = path if path.name == run_id else path / run_id
        if not candidate.is_dir():
            raise FileNotFoundError(f"run {run_id} not found under {path}")
        return candidate
    if _RUN_DIRECTORY.match(path.name):
        return path
    runs = sorted(
        child.name for child in path.iterdir()
        if child.is_dir() and _RUN_DIRECTORY.match(child.name)
    )
    if len(runs) == 1:
        return path / runs[0]
    if runs:
        raise ValueError(
            f"{path} holds {len(runs)} runs ({runs[0]} .. {runs[-1]}); "
            "name one with --run-id rather than merging them"
        )
    return path


def load_run(
    root: Path, run_id: str = "", extra_roots: Iterable[Path] = (),
) -> Dict[str, dict]:
    """Every finding's chosen artifact in one run, with its attempts.

    The chosen artifact carries `_eval_artifact` (its path), `_eval_kind`
    and `_eval_attempts` (every artifact seen for that finding, by kind and
    path, in a stable order).
    """
    run_root = resolve_run_root(Path(root), run_id)
    searched = [run_root, *[Path(item) for item in extra_roots]]
    best: Dict[str, tuple] = {}
    attempts: Dict[str, List[dict]] = {}
    for base in searched:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.json"), key=lambda item: str(item)):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if not isinstance(value, dict):
                continue
            finding_id = finding_id_of(value) or sibling_finding_id(path)
            if not finding_id:
                continue
            rank, kind = _kind(path.name)
            attempts.setdefault(finding_id, []).append(
                {"kind": kind, "path": str(path)}
            )
            if rank == 0:
                continue
            current = best.get(finding_id)
            # Strictly greater: the first artifact of the winning kind, in
            # the stated path order, is the one chosen. A later file of the
            # same kind is an attempt, not a replacement.
            if current is None or rank > current[0]:
                best[finding_id] = (rank, kind, path, value)
    results: Dict[str, dict] = {}
    for finding_id, (_rank, kind, path, value) in best.items():
        value["_eval_artifact"] = str(path)
        value["_eval_kind"] = kind
        value["_eval_attempts"] = attempts.get(finding_id, [])
        results[finding_id] = value
    return results


def run_provenance(root: Path, run_id: str = "") -> dict:
    """What run these results came from, for the report to carry."""
    run_root = resolve_run_root(Path(root), run_id)
    retrospective = run_root / "retrospective" / "retrospective.json"
    pipeline: Optional[dict] = None
    if retrospective.is_file():
        try:
            pipeline = json.loads(
                retrospective.read_text(encoding="utf-8")
            ).get("pipeline")
        except (OSError, json.JSONDecodeError, ValueError):
            pipeline = None
    return {
        "run_id": run_root.name,
        "run_root": str(run_root),
        "pipeline": pipeline or {},
    }
