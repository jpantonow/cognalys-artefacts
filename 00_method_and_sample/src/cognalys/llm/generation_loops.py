"""How many rounds a finding's drafts went through before they stood.

A draft the semantic checks or the behaviour review refuse is drafted again
with the refusal as feedback (coordinated_generation: the file payload's
semantic_validation_retry, numbered by attempt), and a unit whose drafts keep
failing is planned again (a patch_planning call after drafting began). A
pull request reached that way passed checks that had already refused this
unit once, and is the one to read first: the statistics' "PR pronto" detail
and the pull request preview say so. Read only from the run's call traces
(application_results/openai_prompt_logs), so every recorded run has it; it
changes no outcome and no publication.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

DRAFT_STAGE = "code_generation.coordinated_file"
PLAN_STAGE = "patch_planning"
# The retry feedback inside a file draft's payload, which the trace stores
# as an escaped JSON string: semantic_validation_retry\":{\"attempt\":2
_RETRY_ATTEMPT = re.compile(
    r'semantic_validation_retry\\*"\s*:\s*\{\s*\\*"attempt\\*"\s*:\s*(\d+)')

# A call id (and its trace's file name) is <date>T<time>_<microseconds>_<pid>_<n>_<stage>:
# the pid tells the processes of one run folder apart.
_CALL_PROCESS = re.compile(r"^\d{8}T\d{6}_\d+_(\d+)_\d+_")

_LOOPS: Dict[str, Dict[str, Dict[str, int]]] = {}


def _process(metadata: Dict, path: Path) -> str:
    """The process that wrote a call: the pid in its call id or file name; "" when neither has one."""
    for name in (str(metadata.get("call_id", "")), path.name):
        match = _CALL_PROCESS.match(name)
        if match:
            return match.group(1)
    return ""


def _traces(paths) -> Iterator[Tuple[str, str, str, Path]]:
    """(stage, finding_id, process, metadata path) of each draft or plan call, in trace order."""
    for path in paths:
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(metadata, dict):
            continue
        stage, finding = str(metadata.get("stage", "")), str(metadata.get("finding_id", ""))
        if stage in (DRAFT_STAGE, PLAN_STAGE) and finding:
            yield stage, finding, _process(metadata, path), path


def _retry_attempt(metadata_path: Path) -> str:
    """The retry attempt a draft call's payload carries, or "" for a first draft."""
    request = metadata_path.with_name(
        metadata_path.name[:-len("_metadata.json")] + "_request.json")
    try:
        match = _RETRY_ATTEMPT.search(request.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    return match.group(1) if match else ""


def generation_loops(log_dir: Path) -> Dict[str, Dict[str, int]]:
    """{finding_id: {"redrafts": n, "replans": n}} for the units the traces drafted.

    redrafts: the distinct retry attempts drafted on each plan, summed over
    the plans; replans: the plans made after drafting had begun (a plan not
    taken counts too: the unit was planned again). Calls in trace order (the
    file names start with the time and a counter). Read again only when the
    directory holds another number of traces.

    Counted within one process, the last that drafted the finding. The llm
    step started again in the same run folder (the stall watch's resume, a
    queue resumed after a reboot, a manual rerun) plans every unit from the
    start, from the cache or not, and replays the drafts it has: across
    processes, that first plan read as a replan and the replayed retries
    were added twice (einkbro hash, four processes: 6 redrafts and 3
    replans where no process replanned). A planning call a process replays
    from the cache after its own drafts is still the replan it replays.
    """
    directory = Path(log_dir)
    paths = sorted(directory.glob("*_metadata.json")) if directory.is_dir() else []
    key = f"{directory}|{len(paths)}"
    if key in _LOOPS:
        return _LOOPS[key]
    states: Dict[Tuple[str, str], Dict] = {}
    last_drafting: Dict[str, str] = {}
    for stage, finding, process, path in _traces(paths):
        state = states.setdefault((finding, process), {
            "drafting": False, "attempts": set(), "redrafts": 0, "replans": 0})
        if stage == DRAFT_STAGE:
            state["drafting"] = True
            state["attempts"].add(_retry_attempt(path))
            last_drafting[finding] = process
        elif state["drafting"]:
            state["replans"] += 1
            state["redrafts"] += len(state["attempts"] - {""})
            state["attempts"], state["drafting"] = set(), False
    loops = {}
    for finding, process in last_drafting.items():
        state = states[(finding, process)]
        loops[finding] = {"redrafts": state["redrafts"] + len(state["attempts"] - {""}),
                          "replans": state["replans"]}
    for finding, _process_id in states:
        loops.setdefault(finding, {"redrafts": 0, "replans": 0})
    _LOOPS[key] = loops
    return loops


def finding_loop(log_dir: Path, csv_class: str, method_name: str, spec: str) -> Dict[str, int]:
    """The rounds of the unit whose finding is (class, method, spec); {} when none."""
    identity = (str(csv_class), str(method_name), str(spec))
    for finding, loop in generation_loops(log_dir).items():
        if tuple(finding.split(":::")[:3]) == identity and (loop["redrafts"] or loop["replans"]):
            return dict(loop)
    return {}


def _rounds(loop: Optional[Dict]) -> Tuple[int, int]:
    loop = loop if isinstance(loop, dict) else {}
    return int(loop.get("redrafts") or 0), int(loop.get("replans") or 0)


def loop_depth_label(loop: Optional[Dict]) -> str:
    """'após 2 redrafts e replan' for the statistics; '' for a draft that stood at once."""
    redrafts, replans = _rounds(loop)
    parts = []
    if redrafts:
        parts.append(f"{redrafts} redraft" + ("s" if redrafts > 1 else ""))
    if replans:
        parts.append("replan")
    return "após " + " e ".join(parts) if parts else ""


def loop_depth_note(loop: Optional[Dict]) -> str:
    """The same for the operator's preview, in English; '' for none."""
    redrafts, replans = _rounds(loop)
    parts = []
    if redrafts:
        parts.append(f"{redrafts} redraft" + ("s" if redrafts > 1 else "") + " after a refusal")
    if replans:
        parts.append("a new plan")
    return " and ".join(parts)
