"""Whether a run finished, and whether it may be resumed in place.

Written by the pipeline as <run>/run_status.json; read by the queue and the
rerun scripts through ``cognalys status RUN_DIR --resume-check`` (not
"run-status": "cognalys run" in a command line marks a repository user for
the lock and for the scripts' process checks).

Neither the exit code nor "N findings listed" says whether a run is done.
On 2026-10-31 salty run_v4 lost both findings to one OpenAI HTTP 500 and was
marked done because it listed findings, while six runs whose validation_fix
legitimately exited 1 (bluegate run, run_v5, run_v6, lite run_v2, tasks
run_v2, tokn run_v4) had finished normally. The status names what is left:
the findings that ended crashed, the ones the analysis step never concluded
because it stalled, timed out or was killed, and the ones it never reached.
A finding the step concluded in any other way (an issue, an abstention, a
refused or unanalysable input) is done.

A run is resumed in place only under the library it started with: its
commit and a hash of the src/ tree are recorded on every pass, so a resume
after a library change, which would mix two pipelines in one run, is
reported for a new run_vN instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from cognalys.utils import repository_lock

RUN_STATUS_FILE = "run_status.json"
COMPLETE = "complete"
RESUMABLE = "resumable"
RUNNING = "running"
# Failure kinds of solutions_tracking.json that a new attempt can change: a
# crash is a transient provider or network fault as often as a defect (salty
# run_v4's HTTP 500, tokn run_v5's response stuck in "queued"). The others
# (a content refusal, a rejected key, an exhausted budget, an unanalysable
# input) are verdicts a resume would only repeat. provider_unavailable is a
# server fault or capacity failure that outlasted the call's resends
# (agent_baseline_20261108 run_v4: server_is_overloaded, then read as a
# refusal, ended five of nine findings for good).
RESUMABLE_FAILURE_KINDS = frozenset({"crashed", "provider_unavailable"})
# Passes a run may take in place: the first and two resumes. A crash that is
# a pipeline defect repeats under the same code, and the scripts then report
# the run instead of resuming it for ever.
MAX_PASSES = 3

# --resume-check exit codes, one per thing a script does next.
CHECK_COMPLETE = 0
CHECK_RESUME_IN_PLACE = 10
CHECK_NEW_RUN = 11
CHECK_GIVE_UP = 12
CHECK_RUNNING = 13
CHECK_NO_STATUS = 14


def _package_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def src_tree_sha256(package_dir: Optional[Path] = None) -> str:
    """A hash of every source file of the library, independent of git."""
    package_dir = Path(package_dir or _package_dir())
    digest = hashlib.sha256()
    files = []
    # os.walk, pruning __pycache__: rglob over /mnt/c listed every .pyc too
    # and took 5 s instead of about 1.5 s.
    for directory, subdirectories, names in os.walk(package_dir):
        subdirectories[:] = [name for name in subdirectories if name != "__pycache__"]
        files.extend(Path(directory) / name for name in names
                     if not name.endswith((".pyc", ".pyo")))
    for path in sorted(files, key=lambda item: item.relative_to(package_dir).as_posix()):
        digest.update(path.relative_to(package_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _git(package_dir: Path, *arguments: str) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(package_dir), *arguments],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


# The identity to record instead of computing it. The test suite sets it
# (tests/_paths.py): computing it reads every source file, about 1.5 s
# through /mnt/c, twice per pipeline run.
IDENTITY_VARIABLE = "COGNALYS_LIBRARY_IDENTITY"


def library_identity(package_dir: Optional[Path] = None) -> Dict:
    """The library this process runs: git commit, uncommitted changes, src/ hash."""
    if package_dir is None and os.environ.get(IDENTITY_VARIABLE):
        return {"commit": "", "dirty": None,
                "src_tree_sha256": os.environ[IDENTITY_VARIABLE]}
    package_dir = Path(package_dir or _package_dir())
    commit = (_git(package_dir, "rev-parse", "HEAD") or "").strip()
    status = _git(package_dir, "status", "--porcelain", "--", ".")
    return {
        "commit": commit,
        "dirty": bool(status.strip()) if status is not None else None,
        "src_tree_sha256": src_tree_sha256(package_dir),
    }


def recorded_library(run_dir: Path) -> Dict:
    """The library a run started under: run_inputs.json, else run_status.json; {} if neither."""
    for name in ("run_inputs.json", RUN_STATUS_FILE):
        data = _json(Path(run_dir) / name)
        library = data.get("library") if isinstance(data, dict) else None
        if isinstance(library, dict) and library:
            return library
    return {}


def library_version(identity: Dict) -> str:
    """A short name for a library identity: its commit (with "dirty"), or its src/ hash."""
    commit = str(identity.get("commit") or "")
    if commit:
        return commit[:10] + (" dirty" if identity.get("dirty") else "")
    tree = str(identity.get("src_tree_sha256") or "")
    return f"src {tree[:12]}" if tree else "unrecorded"


def run_version(run_dir: Path) -> str:
    """The library version that produced a run's results.

    A run resumed under another src/ tree, or edited while it ran
    (run_status.json passes with more than one tree, or mixed_pipeline),
    mixes two pipelines: its name lists the versions of its passes and ends
    in "(mixed)", so it is never counted with the clean first version.
    """
    first = library_version(recorded_library(run_dir))
    status = _json(Path(run_dir) / RUN_STATUS_FILE)
    if not isinstance(status, dict):
        return first
    passes = [item for item in status.get("passes") or [] if isinstance(item, dict)]
    trees: list = []
    names: list = []
    for item in passes:
        tree = str(item.get("src_tree_sha256") or "")
        if tree in trees:
            continue
        trees.append(tree)
        name = library_version(item)
        if name in names:
            # Same commit and dirty flag on another tree: the hash tells them apart.
            name = f"{name} src {tree[:12]}"
        names.append(name)
    if not status.get("mixed_pipeline") and len(trees) <= 1:
        return first
    return " -> ".join(names or [first]) + " (mixed)"


def read_status(run_dir: Path) -> Optional[Dict]:
    try:
        data = json.loads((Path(run_dir) / RUN_STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(run_dir: Path, data: Dict) -> None:
    path = Path(run_dir) / RUN_STATUS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def record_pass_start(run_dir: Path, identity: Dict) -> Dict:
    """Mark the run as running under ``identity``; returns the status written.

    Written before the first step, so a pass killed outright (an OOM kill,
    a WSL restart) leaves "running" with a pid that is gone, which the check
    reads as resumable.
    """
    previous = read_status(run_dir) or {}
    passes = list(previous.get("passes") or [])
    passes.append({"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **identity})
    data = {
        "status": RUNNING,
        "pid": os.getpid(),
        # The pid alone is not this process after WSL restarts and numbers
        # PIDs from 1 again (2026-10-31 review: a reused pid kept a killed
        # run "running" and the queue left it alone on every pass).
        "boot_id": repository_lock._boot_id(),
        "start_ticks": repository_lock._start_ticks(os.getpid()),
        "passes": passes,
        "library": passes[0],
    }
    _write(run_dir, data)
    return data


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# error_solutions/<folder>/<apk>/<index>_<method>.json, written by
# llm.issue_candidates when a finding ends without a solution or an issue.
CONCLUDED_RECORD_FOLDERS = ("generation_shortfall", "no_security_impact",
                            "confirmed_without_safe_fix", "settled_before_analysis")


def _indexed_files(directory: Path, suffix: str) -> set:
    """(apk, index) of every ``<index>_<method><suffix>`` under directory/<apk>/."""
    found = set()
    if not directory.is_dir():
        return found
    for path in directory.glob(f"*/*{suffix}"):
        index = path.name.split("_", 1)[0]
        found.add((path.parent.name, index))
    return found


def finding_states(run_dir: Path, analysis_completed: bool) -> Dict[str, List[str]]:
    """The findings left for another pass: {"crashed": [...], "provider_unavailable": [...],
    "unfinished": [...]}.

    ``analysis_completed`` says the llm step ran to its end in this pass and
    wrote its tracking file; it then accounts for every finding it was given,
    and only the crashed ones and those an unavailable provider left
    (provider_unavailable, kept apart from crashes, which may be defects
    here) remain. Otherwise only a saved solution, an
    issue candidate, a record of an ending without either
    (CONCLUDED_RECORD_FOLDERS) or a non-crash verdict in the tracking file
    shows that a finding was concluded: an evidence review alone may belong
    to an analysis stopped half way.
    """
    run_dir = Path(run_dir)
    solutions_dir = run_dir / "error_solutions"
    solutions = _indexed_files(solutions_dir, "_solution.json")
    issues = _indexed_files(solutions_dir / "issue_candidates", "_issue.json")
    # The endings issue_candidates records without an issue (no security
    # impact, a correction not generated, ...) conclude a finding as an issue does.
    for folder in CONCLUDED_RECORD_FOLDERS:
        issues |= _indexed_files(solutions_dir / folder, ".json")
    tracking = _json(solutions_dir / "solutions_tracking.json") or {}
    verdicts = {}
    for failure in tracking.get("failures", []) or []:
        if isinstance(failure, dict):
            verdicts[(failure.get("apk_name", ""), failure.get("finding_id", ""))] = str(
                failure.get("kind", ""))
    findings = []
    for path in sorted(run_dir.glob("error_methods/*/*.json")):
        data = _json(path) or {}
        meta, error = data.get("extraction_metadata", {}) or {}, data.get("error", {}) or {}
        apk = meta.get("apk_name", "") or path.parent.name
        findings.append((apk, str(meta.get("csv_index", path.name.split("_", 1)[0])),
                         error.get("unique_error_id", "") or path.stem))
    state = {}
    for apk, index, finding_id in findings:
        verdict = verdicts.get((apk, finding_id), "")
        if (apk, index) in solutions:
            state[(apk, finding_id)] = "done"
        elif verdict == "provider_unavailable":
            state[(apk, finding_id)] = "provider_unavailable"
        elif verdict in RESUMABLE_FAILURE_KINDS:
            state[(apk, finding_id)] = "crashed"
        elif analysis_completed or (apk, index) in issues or verdict:
            state[(apk, finding_id)] = "done"
        else:
            state[(apk, finding_id)] = "unfinished"
    # A finding analysed as part of another's unit shares that unit's fate.
    for record in _json(solutions_dir / "joint_findings.json") or []:
        if not isinstance(record, dict):
            continue
        joined, into = record.get("joined") or {}, record.get("into") or {}
        key = (joined.get("apk_name", ""), joined.get("unique_error_id", ""))
        primary = (into.get("apk_name", ""), into.get("unique_error_id", ""))
        if key in state and primary in state:
            state[key] = state[primary]
    left: Dict[str, List[str]] = {"crashed": [], "provider_unavailable": [], "unfinished": []}
    for (apk, finding_id), value in state.items():
        if value in left:
            left[value].append(f"{apk}:{finding_id}")
    return left


def record_pass_end(run_dir: Path, *, analysis_selected: bool,
                    analysis_outcome: Optional[Dict], failed_steps: Iterable,
                    identity_now: Optional[Dict] = None) -> Dict:
    """Write the pass's final status; returns it.

    ``analysis_outcome`` is the llm step's outcome in this pass (returncode,
    timed_out, idle, started_at as a wall-clock time), or None when it did
    not run.
    """
    run_dir = Path(run_dir)
    data = dict(read_status(run_dir) or {})
    data.pop("pid", None)
    reasons: List[str] = []
    completed = False
    if analysis_selected:
        if analysis_outcome is None:
            reasons.append("the analysis step did not run")
        elif analysis_outcome.get("timed_out") or analysis_outcome.get("idle"):
            reasons.append("the analysis step stalled or reached its deadline")
        else:
            tracking = run_dir / "error_solutions" / "solutions_tracking.json"
            try:
                written = tracking.stat().st_mtime
            except OSError:
                written = None
            completed = (written is not None
                         and written >= float(analysis_outcome.get("started_at", 0)) - 1)
            if not completed:
                reasons.append("the analysis step ended without accounting for its "
                               f"findings (exit {analysis_outcome.get('returncode')})")
        left = finding_states(run_dir, completed)
    else:
        left = {"crashed": [], "provider_unavailable": [], "unfinished": []}
    if left["crashed"]:
        reasons.append(f"{len(left['crashed'])} finding(s) crashed")
    if left["provider_unavailable"]:
        # The 2026-11-09 Flex outage (18:42 to 20:03) is no defect to look
        # for in the log: named apart, it is resumed all the same.
        reasons.append(f"{len(left['provider_unavailable'])} finding(s) left by an "
                       "unavailable provider")
    if left["unfinished"]:
        reasons.append(f"{len(left['unfinished'])} finding(s) not concluded")
    passes = data.get("passes") or []
    # The library changed while the run went on (src/ edited mid-run): its
    # result mixes two pipelines and only a new run settles it.
    started = passes[-1] if passes else {}
    mixed = bool(identity_now and started
                 and identity_now.get("src_tree_sha256") != started.get("src_tree_sha256"))
    mixed = mixed or len({item.get("src_tree_sha256") for item in passes}) > 1
    data.update({
        "status": RESUMABLE if reasons else COMPLETE,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reasons": reasons,
        "crashed_findings": left["crashed"],
        "provider_unavailable_findings": left["provider_unavailable"],
        "unfinished_findings": left["unfinished"],
        # Informational: a step after the analysis that failed (validation_fix
        # exits 1 when a repair failed) does not make the run resumable.
        "failed_steps": [{"step": step, "exit_code": code} for step, code in failed_steps],
        "mixed_pipeline": mixed,
    })
    _write(run_dir, data)
    return data


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _runner_alive(data: Dict) -> bool:
    """Whether the pass that wrote ``data`` still runs: its pid, and, when
    recorded, the same boot and process start time (as the repository lock)."""
    try:
        pid = int(data.get("pid"))
    except (TypeError, ValueError):
        return False
    if not _pid_alive(pid):
        return False
    return repository_lock._owner_alive(data, pid)


def resume_check(run_dir: Path, identity_now: Optional[Dict] = None) -> tuple:
    """(exit code, one line) saying what a script should do with ``run_dir``."""
    data = read_status(run_dir)
    if data is None:
        return CHECK_NO_STATUS, f"no {RUN_STATUS_FILE} (a run from before it existed)"
    status = data.get("status")
    if status == RUNNING and _runner_alive(data):
        return CHECK_RUNNING, f"running (pid {data.get('pid')})"
    if status == COMPLETE:
        failed = ", ".join(f"{item.get('step')} exit {item.get('exit_code')}"
                           for item in data.get("failed_steps") or [])
        note = f"; failed steps: {failed}" if failed else ""
        if data.get("mixed_pipeline"):
            note += "; the library changed during the run, rerun it in a new run_vN"
        return CHECK_COMPLETE, "complete" + note
    reasons = list(data.get("reasons") or [])
    if status == RUNNING:
        reasons = ["interrupted (its process is gone)"]
    detail = "; ".join(reasons) or "not complete"
    passes = data.get("passes") or []
    identity_now = identity_now or library_identity()
    started = data.get("library") or (passes[0] if passes else {})
    if (data.get("mixed_pipeline")
            or started.get("src_tree_sha256") != identity_now.get("src_tree_sha256")):
        recorded = (started.get("commit") or "?")[:10]
        return CHECK_NEW_RUN, (
            f"{detail}; the library changed since the run started "
            f"(recorded {recorded}, now {(identity_now.get('commit') or '?')[:10]}"
            f"{', uncommitted changes' if identity_now.get('dirty') else ''}): "
            "rerun it in a new run_vN")
    if len(passes) >= MAX_PASSES:
        if (status != RUNNING and data.get("provider_unavailable_findings")
                and not data.get("crashed_findings") and not data.get("unfinished_findings")):
            # Only the provider failed: nothing in the log to fix.
            return CHECK_GIVE_UP, (f"{detail}; already ran {len(passes)} times in place: "
                                   "rerun it in a new run_vN once the provider answers")
        return CHECK_GIVE_UP, (f"{detail}; already ran {len(passes)} times in place: "
                               "look at its log, then rerun it in a new run_vN")
    return CHECK_RESUME_IN_PLACE, f"{detail}; resume it in place"
