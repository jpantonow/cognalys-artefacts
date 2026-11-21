"""Git commands that talk to a remote, bounded in time.

A transfer over a dead TCP connection never ends on its own: git waits for
bytes that will not come. After the laptop woke from standby, AntennaPod's
extraction sat in `git submodule update --init --recursive`, on the
`git fetch origin <sha>` of its StoreMetadata submodule, for well over an
hour (rvsec_misuse5_20261017 run_v3), until it was killed by hand.

Every network git command the pipeline runs goes through run_network_git:

- git's own low-speed limit (GIT_HTTP_LOW_SPEED_LIMIT/TIME, inherited by the
  fetches a submodule update starts) ends an HTTP transfer that moves less
  than 1000 bytes/s for 60 s, so a dead connection fails within a minute
  of the machine being awake (ssh remotes have only the timeout: an ssh
  command of our own would override the user's core.sshCommand);
- a timeout proportionate to the operation bounds whatever the limit cannot
  see (a server that trickles, a hung credential helper);
- a timeout or a stalled transfer is retried once, since the usual cause --
  a connection the standby killed -- is gone on the second try.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from typing import Optional, Sequence

LOW_SPEED_LIMIT_BYTES = 1000
LOW_SPEED_TIME_SECONDS = 60

# Seconds per operation. A clone writes a whole working tree (minutes across
# WSL's 9p bridge); ls-remote moves a few refs.
NETWORK_GIT_TIMEOUTS = {
    "ls-remote": 120,
    "fetch": 900,
    "pull": 900,
    "push": 600,
    "clone": 1800,
    "submodule": 1800,
}
NETWORK_SUBCOMMANDS = frozenset(NETWORK_GIT_TIMEOUTS)

# What git prints when a transfer stalled or a connection died, as opposed to
# a refusal (no such repository, authentication) that a retry cannot change.
STALL_MARKERS = (
    "Operation too slow",
    "Operation timed out",
    "Connection timed out",
    "curl 28",
    "early EOF",
    "Connection reset by peer",
    "unexpected disconnect",
    "Timeout, server",
)

# Git options that take a separate value: skipped to find the subcommand.
_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})

_logger = logging.getLogger(__name__)


def network_subcommand(command: Sequence[str]) -> Optional[str]:
    """The git subcommand of `command` when it talks to a remote, else None.

    `git submodule sync` only rewrites local configuration; `submodule
    update` (and `foreach`, which may fetch) count as network commands.
    """
    arguments = [str(item) for item in command]
    if not arguments or os.path.basename(arguments[0]) not in ("git", "git.exe"):
        return None
    index = 1
    while index < len(arguments) and arguments[index].startswith("-"):
        index += 2 if arguments[index] in _OPTIONS_WITH_VALUE else 1
    if index >= len(arguments):
        return None
    subcommand = arguments[index]
    if subcommand == "submodule":
        rest = arguments[index + 1:index + 2]
        return subcommand if rest and rest[0] in ("update", "foreach") else None
    return subcommand if subcommand in NETWORK_SUBCOMMANDS else None


def network_git_timeout(command: Sequence[str]) -> int:
    """The default time limit for a network git command."""
    return NETWORK_GIT_TIMEOUTS.get(network_subcommand(command) or "", 900)


def network_git_env(env: Optional[dict] = None) -> dict:
    """`env` (or this process's environment) with the transfer bounds set.

    Values already present are kept, so an operator can loosen them.
    """
    bounded = dict(os.environ if env is None else env)
    bounded.setdefault("GIT_TERMINAL_PROMPT", "0")
    bounded.setdefault("GIT_HTTP_LOW_SPEED_LIMIT", str(LOW_SPEED_LIMIT_BYTES))
    bounded.setdefault("GIT_HTTP_LOW_SPEED_TIME", str(LOW_SPEED_TIME_SECONDS))
    return bounded


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def transfer_stalled(output) -> bool:
    """Whether git's output says the transfer stalled or the connection died."""
    text = _text(output)
    return any(marker in text for marker in STALL_MARKERS)


def run_network_git(
    command: Sequence[str],
    timeout: Optional[float] = None,
    *,
    retries: int = 1,
    env: Optional[dict] = None,
    raise_on_timeout: bool = True,
    log=None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """subprocess.run for a git command that talks to a remote, bounded.

    `timeout` defaults to the operation's (network_git_timeout). A timeout,
    or a failure whose output shows a stalled transfer, is retried `retries`
    times. The last timeout raises subprocess.TimeoutExpired, or with
    `raise_on_timeout=False` returns a result with return code 124 and the
    reason in stderr, for callers that only read a return code.

    `log`, a writable text stream, receives one line per retry; the output
    of a command run with stdout=log goes there as usual.
    """
    command = [str(item) for item in command]
    limit = float(timeout) if timeout else float(network_git_timeout(command))
    if "input" not in kwargs and "stdin" not in kwargs:
        # A credential prompt waiting on stdin is a hang of its own.
        kwargs["stdin"] = subprocess.DEVNULL
    bounded_env = network_git_env(env)
    # The subcommand alone: a clone or push URL can carry a token.
    label = f"git {network_subcommand(command) or 'command'}"
    attempts = max(0, int(retries)) + 1
    # A clone killed on its timeout cannot clean up: the partial destination
    # stays, and a retry fails at once on "already exists". The destination
    # is removed after a timeout when this call created it; when it cannot
    # be told, the timeout is not retried.
    destination = clone_destination(command, kwargs.get("cwd"))
    clone_owns_destination = destination is not None and not os.path.lexists(destination)
    if network_subcommand(command) == "clone" and not clone_owns_destination:
        attempts_after_timeout = 1
    else:
        attempts_after_timeout = attempts
    for attempt in range(1, attempts + 1):
        try:
            completed = subprocess.run(
                command, timeout=limit, env=bounded_env, **kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            if clone_owns_destination:
                shutil.rmtree(destination, ignore_errors=True)
            retrying = attempt < attempts_after_timeout
            note = (f"{label} timed out after {int(limit)}s "
                    f"(attempt {attempt}/{attempts})")
            _note(log, note + ("; retrying." if retrying else "."))
            if retrying:
                continue
            if raise_on_timeout:
                raise
            textual = any(kwargs.get(key) for key in ("text", "encoding", "universal_newlines"))
            stdout, stderr = _text(exc.stdout), f"{note}: git timed out"
            return subprocess.CompletedProcess(
                command, 124,
                stdout if textual else stdout.encode(),
                stderr if textual else stderr.encode(),
            )
        if (completed is not None and completed.returncode != 0
                and attempt < attempts
                and transfer_stalled(
                    _text(getattr(completed, "stderr", None))
                    + _text(getattr(completed, "stdout", None)))):
            _note(log, f"{label} stalled (attempt {attempt}/"
                       f"{attempts}); retrying.")
            continue
        return completed
    return completed  # pragma: no cover -- the loop always returns


# `git clone` options that take a separate value, skipped to find the
# repository and directory arguments.
_CLONE_OPTIONS_WITH_VALUE = frozenset({
    "-b", "--branch", "-o", "--origin", "-c", "--config", "--depth",
    "--reference", "--reference-if-able", "--separate-git-dir", "-j", "--jobs",
    "--template", "-u", "--upload-pack", "--filter", "--shallow-since",
    "--shallow-exclude", "--server-option", "--bundle-uri", "--revision",
})


def clone_destination(command: Sequence[str], cwd=None) -> Optional[str]:
    """The directory a `git clone` command writes, or None for any other command.

    The explicit directory argument, or git's own default: the repository
    URL's last component without ".git". None as well when it cannot be told.
    """
    arguments = [str(item) for item in command]
    if network_subcommand(arguments) != "clone":
        return None
    index = arguments.index("clone") + 1
    positional = []
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            positional.extend(arguments[index + 1:])
            break
        if argument.startswith("-"):
            index += 2 if argument in _CLONE_OPTIONS_WITH_VALUE else 1
            continue
        positional.append(argument)
        index += 1
    if len(positional) >= 2:
        target = positional[1]
    elif len(positional) == 1:
        target = re.split(r"[/:\\]", positional[0].rstrip("/\\"))[-1]
        target = target[:-4] if target.endswith(".git") else target
    else:
        return None
    if not target or target in (".", ".."):
        return None
    return target if os.path.isabs(target) else os.path.join(str(cwd or os.getcwd()), target)


def run_git_command(command: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run for any git command; network ones are bounded.

    For helpers that run every git command through one function: a local
    command runs exactly as before, a network command through
    run_network_git with its default limit, returning code 124 on a final
    timeout rather than raising.
    """
    if network_subcommand(command) is None:
        return subprocess.run(list(command), **kwargs)
    timeout = kwargs.pop("timeout", None)
    env = kwargs.pop("env", None)
    return run_network_git(command, timeout, env=env, raise_on_timeout=False, **kwargs)


def _note(log, message: str) -> None:
    _logger.warning(message)
    if log is None:
        return
    try:
        log.write(message + "\n")
        log.flush()
    except (OSError, ValueError, AttributeError):
        pass
