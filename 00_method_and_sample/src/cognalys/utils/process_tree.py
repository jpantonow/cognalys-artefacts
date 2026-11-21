#!/usr/bin/env python3
"""Run a pipeline step under a deadline, and take its whole tree down with it.

Every component a step calls has a timeout of its own -- Gradle, git, PMD,
each model request -- but the step itself had none: run_pipeline waited on
it with a bare subprocess.run. A step that stopped making progress without
any component timing out (a deadlock between threads, a child waiting on a
pipe nobody reads) held the run open indefinitely, and every later step and
finding waited behind it.

Killing only the step's own process is not enough either. A step starts
Gradle daemons, git and JVMs of its own; if they survive, they keep the
worktree locked and the next step fails for a reason that has nothing to do
with it. So the step runs in its own process group (a new session on POSIX,
a new process group on Windows) and a timeout ends the group.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Dict, Optional, Sequence

# The conventional code for "killed by a timeout" (coreutils timeout(1)),
# outside the range the step scripts use: 0, 1, 2 and 3 (partial success).
STEP_TIMEOUT_EXIT_CODE = 124
# How long a step gets to exit on its own after being asked to stop, before
# it is killed outright.
TERMINATION_GRACE_SECONDS = 20
# How much longer than its wait a stall-watch check may come back before the
# gap is read as the machine having been suspended rather than the step
# having been idle (see run_with_deadline).
SUSPENSION_GAP_SECONDS = 120


def _group_options() -> Dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_tree(process: subprocess.Popen,
                   grace: float = TERMINATION_GRACE_SECONDS) -> None:
    """End `process` and everything it started, politely and then not."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        # taskkill /T is the only reliable way to reach grandchildren here.
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True, stdin=subprocess.DEVNULL,
        )
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()


def last_activity(paths: Sequence) -> float:
    """The newest modification time among ``paths`` and the files directly in them."""
    newest = 0.0
    for path in paths or ():
        try:
            newest = max(newest, os.stat(path).st_mtime)
            if os.path.isdir(path):
                with os.scandir(path) as entries:
                    for entry in entries:
                        try:
                            newest = max(newest, entry.stat().st_mtime)
                        except OSError:
                            continue
        except OSError:
            continue
    return newest


def run_with_deadline(
    command: Sequence[str], cwd=None, env: Optional[Dict[str, str]] = None,
    timeout_seconds: Optional[float] = None,
    grace: float = TERMINATION_GRACE_SECONDS,
    idle_seconds: Optional[float] = None,
    activity_paths: Sequence = (),
    poll_seconds: float = 30.0,
) -> Dict:
    """Run `command`, returning its exit code and whether a limit hit.

    `timeout_seconds` of None or <= 0 waits indefinitely. With
    `idle_seconds`, the step also ends when nothing under `activity_paths`
    (its logs, its prompt records) has changed for that long: Amethyst's
    analysis (2026-10-25) sat in uninterruptible disk I/O for 47 minutes
    with no model call, far past every request's own timeout, and held the
    run open. An interrupt (Ctrl+C) also ends the tree before propagating,
    so an operator stopping a run does not leave its Gradle daemons behind.

    Time the machine spent suspended is not idleness. SMSecure's
    getKeyFromPassphrase (rvsec_misuse_20261016/run_v2, 2026-10-29): the
    laptop went into standby at 08:21, seconds after a patch-planning
    request was sent, and woke at 09:58; the wall clock then showed 97
    minutes without a write, the step was ended at once, and its one resume
    met a network that was still reconnecting. A gap between two checks far
    longer than the wait asked for means nothing ran in between (host
    standby, a paused WSL VM), so it is discounted.
    """
    process = subprocess.Popen(list(command), cwd=cwd, env=env, **_group_options())
    deadline = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
    started = time.monotonic()
    watched = idle_seconds and idle_seconds > 0 and activity_paths
    activity_seen = time.time()
    checked = activity_seen
    try:
        while True:
            remaining = None if deadline is None else deadline - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(command, deadline)
            wait = poll_seconds if watched else remaining
            if watched and remaining is not None:
                wait = min(wait, remaining)
            try:
                returncode = process.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() - started >= deadline:
                    raise
                if not watched:
                    continue
                now = time.time()
                suspended = (now - checked) - wait
                if suspended > max(SUSPENSION_GAP_SECONDS, 2 * wait):
                    print(
                        f"No check ran for {int(now - checked)}s (the machine "
                        "was suspended); that time is not counted as the "
                        "step's idleness.", file=sys.stderr, flush=True,
                    )
                    activity_seen += suspended
                checked = now
                activity_seen = max(activity_seen, last_activity(activity_paths))
                if now - activity_seen > idle_seconds:
                    print(
                        f"Step made no progress for {int(idle_seconds)}s (nothing "
                        "it writes changed); ending its process tree.",
                        file=sys.stderr, flush=True,
                    )
                    terminate_tree(process, grace)
                    return {"returncode": STEP_TIMEOUT_EXIT_CODE, "timed_out": True,
                            "idle": True, "idle_seconds": idle_seconds}
    except subprocess.TimeoutExpired:
        print(
            f"Step exceeded its deadline of {int(deadline)}s; ending its "
            "process tree.", file=sys.stderr, flush=True,
        )
        terminate_tree(process, grace)
        return {"returncode": STEP_TIMEOUT_EXIT_CODE, "timed_out": True}
    except BaseException:
        terminate_tree(process, grace)
        raise
    return {"returncode": returncode, "timed_out": False}
