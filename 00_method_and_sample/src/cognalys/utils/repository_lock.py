"""Conservative, cross-platform exclusion between pipeline runs and pruning.

Directory creation is atomic on both Windows and WSL. A lock left by an owner
that died is removed only when that can be established: the owner was on this
host, its PID no longer exists, and no process that could still be using the
repositories (a pipeline step, Gradle, Semgrep) is running here. A PID check
alone cannot say a crashed owner's children stopped, so it is never enough
by itself; a lock from the other side of Windows/WSL, or one whose owner
cannot be read, is never touched. On 2026-10-12 such locks were removed by
hand four times after a stopped run, each time after exactly these checks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from datetime import datetime, timezone

# Command-line fragments of processes that may still be using the clones,
# worktrees or outputs after their parent run died.
_REPOSITORY_USERS = ("-m cognalys.", "cognalys run", "cognalys propose-prs",
                     "run_pipeline", "GradleWrapperMain", "GradleDaemon", "semgrep")


class RepositoryBusyError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _start_ticks(pid: int) -> str:
    """When ``pid`` started, in clock ticks since boot (/proc/<pid>/stat field 22)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def _owner_alive(owner: dict, pid: int) -> bool:
    """Whether the process that wrote ``owner`` still runs.

    A PID alone is not that process once the machine or WSL restarted: WSL
    is shut down after every queue (after_queue_sparse.ps1) and restarts
    numbering PIDs from 1, so a lock left by a killed run could name a PID
    some unrelated process holds now, and was then never cleared. A lock
    written since records the boot and the process's start time; an older
    one is judged by its PID, as before.
    """
    if not _pid_alive(pid):
        return False
    boot = owner.get("boot_id")
    if boot and _boot_id() and boot != _boot_id():
        return False
    started = owner.get("start_ticks")
    if started and _start_ticks(pid) and str(started) != _start_ticks(pid):
        return False
    return True


def _ancestors(pid: int) -> set[int]:
    """``pid`` and every process above it, read from /proc/<pid>/stat."""
    chain = set()
    while pid > 1 and pid not in chain:
        chain.add(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            # The command name is in parentheses and may hold spaces.
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return chain


def _repository_users(exclude: int) -> list[str]:
    """Running processes that may be using the repositories ("pid command").

    The processes that started this one are not users: on 2026-10-14 a run
    started from tmux saw the tmux server, whose command line carries
    "cognalys run", and refused to remove a lock whose owner had died.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        # Not Linux: nothing to read, so nothing can be established.
        return ["(process list unavailable on this platform)"]
    starters = _ancestors(exclude) | {exclude, os.getppid()}
    users = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) in starters:
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if any(fragment in command for fragment in _REPOSITORY_USERS):
            users.append(f"{entry.name} {command.strip()[:100]}")
    return users


def stale_lock_reason(path: Path) -> str:
    """Why the lock at ``path`` may be removed, or "" when it may not."""
    try:
        owner = json.loads((path / "owner.json").read_text(encoding="utf-8"))
        pid = int(owner["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    if owner.get("host") != socket.gethostname() or _owner_alive(owner, pid):
        return ""
    if _repository_users(exclude=os.getpid()):
        return ""
    return f"its owner (pid {pid}, started {owner.get('started_at', '?')}) no longer runs"


class RepositoryUsageLock:
    def __init__(self, workspace: Path):
        self.path = Path(workspace).resolve() / "outputs" / ".repository_usage.lock"

    def _busy(self) -> RepositoryBusyError:
        try:
            owner = (self.path / "owner.json").read_text(encoding="utf-8")
        except OSError:
            owner = "unreadable"
        running = _repository_users(exclude=os.getpid())
        return RepositoryBusyError(
            f"Repository use is locked: {self.path} (owner: {owner}). "
            + (f"Still running: {'; '.join(running[:5])}. " if running else "")
            + "If a previous run crashed, verify that it and its children "
            "stopped before removing this lock directory manually."
        )

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.mkdir()
        except FileExistsError:
            reason = stale_lock_reason(self.path)
            if not reason:
                raise self._busy() from None
            print(f"Removing a stale repository lock: {reason}, and nothing "
                  "else is using the repositories.", flush=True)
            (self.path / "owner.json").unlink(missing_ok=True)
            try:
                self.path.rmdir()
                self.path.mkdir()
            except OSError:
                # Another run took it between the check and now.
                raise self._busy() from None
        try:
            (self.path / "owner.json").write_text(json.dumps({
                "pid": os.getpid(), "host": socket.gethostname(),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "boot_id": _boot_id(), "start_ticks": _start_ticks(os.getpid()),
            }), encoding="utf-8")
        except BaseException:
            (self.path / "owner.json").unlink(missing_ok=True)
            self.path.rmdir()
            raise
        return self

    def __exit__(self, *exc):
        (self.path / "owner.json").unlink()
        self.path.rmdir()
