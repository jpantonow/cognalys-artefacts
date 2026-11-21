"""The library's entry points: plan a run, run it, read what it produced.

    from cognalys import api
    from cognalys.models import RunConfig

    config = RunConfig.from_legacy_args(["--csv-file", "findings.csv", "--validate-build"])
    for step in api.plan(config):          # no clone, no model call, no write
        print(step.name, " ".join(step.command))
    result = api.run(config)               # the pipeline, as run_pipeline.py runs it
    print(result.outcomes(), result.ready_pull_requests())

`run` is the orchestrator (python -m cognalys.orchestration.run_pipeline), called
in process with an argv: steps still run as subprocesses, with their
timeouts, locks and retrospective. Nothing here calls sys.exit.
"""
from __future__ import annotations

import contextlib
import os
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Iterator

from cognalys.models import FindingResult, RunConfig, RunResult, Step
from cognalys.sources import write_rows
from cognalys.workspace import ENVIRONMENT_VARIABLE, workspace_root


@contextlib.contextmanager
def _workspace(path: Path | None) -> Iterator[Path]:
    """The workspace for this call; set for the steps' processes too."""
    if path is None:
        yield workspace_root()
        return
    previous = os.environ.get(ENVIRONMENT_VARIABLE)
    os.environ[ENVIRONMENT_VARIABLE] = str(Path(path).resolve())
    try:
        yield workspace_root()
    finally:
        if previous is None:
            os.environ.pop(ENVIRONMENT_VARIABLE, None)
        else:
            os.environ[ENVIRONMENT_VARIABLE] = previous


def plan(config: RunConfig, workspace: Path | None = None) -> list[Step]:
    """The steps a run would execute and their commands, without running any.

    Reads only what the command builders read: the findings CSV, to scope
    repository setup to its projects, and whether policy files exist.
    """
    from cognalys.orchestration import run_pipeline

    args = config.namespace()
    with _workspace(workspace) as root:
        paths = run_pipeline.resolve_paths(args, root)
        return [Step(step, tuple(run_pipeline.command_for_step(step, args, paths)))
                for step in run_pipeline.selected_steps(args)]


def run_directory(config: RunConfig, workspace: Path | None = None) -> Path:
    """The run's output directory, where load_results reads it back from."""
    from cognalys.orchestration import run_pipeline

    args = config.namespace()
    with _workspace(workspace) as root:
        return run_pipeline.resolve_paths(args, root).extracted_methods_dir.parent


def run(config: RunConfig, workspace: Path | None = None,
        findings: Iterable[object] | None = None) -> RunResult:
    """Run the pipeline and return what its artifacts say about each finding.

    ``findings`` (Finding objects or mappings in the corpus columns) replaces
    the configured findings file: it is written to findings.jsonl in the run
    directory, which every step then reads, so no CSV is involved.
    """
    from cognalys.orchestration import run_pipeline

    with _workspace(workspace):
        if findings is not None:
            directory = run_directory(config, workspace)
            written = write_rows(findings, directory / "findings.jsonl")
            # The last --csv-file wins; an absolute path stays absolute when
            # the orchestrator joins it to the workspace.
            config = RunConfig.from_legacy_args(
                [*config.arguments, "--csv-file", str(written)])
        exit_code = run_pipeline.main(list(config.arguments))
        directory = run_directory(config, workspace)
    return replace(load_results(directory), exit_code=exit_code)


def load_results(*run_directories: Path) -> RunResult:
    """Each finding's outcome, from one run or several (oldest first).

    With several, a finding comes from the latest run that analysed it; see
    reporting.finding_statistics.combine_runs.
    """
    from cognalys.reporting.finding_statistics import combine_runs, read_run

    directories = tuple(Path(directory) for directory in run_directories)
    if not directories:
        raise ValueError("load_results needs at least one run directory")
    runs = {str(directory): read_run(directory) for directory in directories}
    records = combine_runs(runs) if len(directories) > 1 else runs[str(directories[0])]
    findings = tuple(FindingResult.from_record(key, record)
                     for key, record in sorted(records.items()))
    return RunResult(runs=directories, findings=findings)


class RepositoryChanged(RuntimeError):
    """A checkout with uncommitted changes, asked to be repaired."""


def scan(target, specifications: list[str] | None = None):
    """Run the detectors over a local checkout; see cognalys.detectors.

    Only looks: nothing is cloned, analysed or written. The result lists the
    findings and everything that kept the scan from covering the tree.
    """
    from cognalys.detectors import discover_semgrep

    return discover_semgrep(target, specifications)


def repair_repository(target, config: RunConfig, workspace: Path | None = None,
                      specifications: list[str] | None = None):
    """Scan a local checkout and run the pipeline on what the scan finds.

    Returns (scan result, run result). The pipeline works on a clone of the
    checkout's commit, never on the checkout itself, so a checkout with
    uncommitted changes is refused: the patch would be computed against
    code that is not what the user has on disk.
    """
    from cognalys.detectors import snapshot

    state = snapshot(target.local_path)
    if state.dirty:
        raise RepositoryChanged(
            f"{state.path} has uncommitted changes ({', '.join(state.changed_files[:5])}"
            f"{'...' if len(state.changed_files) > 5 else ''}); commit or stash them "
            "so the patch is computed against what is on disk")
    found = scan(target, specifications)
    if not found.findings:
        return found, RunResult(runs=(), findings=())
    return found, run(config, workspace=workspace, findings=found.findings)
