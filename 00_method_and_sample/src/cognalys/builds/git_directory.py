"""A real `.git` directory for builds that read or write under it.

Build scripts commonly derive a version or commit hash by reading .git/HEAD
directly (a widely copied gist), or install hooks into .git/hooks (NotallyX's
installLocalGitHooks, a dependency of preBuild). Both work in a clone, where
.git is a directory, and fail in a git worktree, where .git is a regular file
holding "gitdir: ...". Patches are validated in worktrees, so such a project
cannot configure or build there -- the failure is environmental and no source
patch can avoid it.

Every Gradle build the pipeline runs goes through apk_build.run_build, which
puts the shim around the Gradle process alone. It used to wrap only the
validation's production build: NotallyX's patched assembleDebug built with
it, and the unit-test build of the same worktree, and the baseline worktree's,
then failed at installLocalGitHooks (run_v4, 2026-10-29), so a patch that
built was recorded as failed.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

GIT_DIRECTORY_READ_PATTERN = re.compile(r"""\.git['"]?\s*[/+]|['"]\.git/""")
BUILD_SCRIPT_NAMES = (
    "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts",
)
GIT_POINTER_BACKUP = ".git.worktree-pointer.bak"
# Where a shim directory that cannot be deleted is moved, inside the
# worktree's own gitdir: git ignores it there and `git worktree prune`
# removes it with the rest.
SHIM_LEFTOVER_PREFIX = "cognalys-shim-leftover-"


def build_reads_git_directory(project: Path) -> list[str]:
    """Build scripts in this project that read paths under `.git/`."""
    found = []
    for name in BUILD_SCRIPT_NAMES:
        for path in [project / name, *sorted(project.glob(f"*/{name}"))]:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if GIT_DIRECTORY_READ_PATTERN.search(text):
                found.append(str(path.relative_to(project)).replace("\\", "/"))
    return sorted(set(found))


@contextlib.contextmanager
def readable_git_directory(worktree: Path):
    """Present `.git` as a real directory for the duration of a build.

    A git worktree's `.git` is a file pointing at the real gitdir, so a build
    script reading `.git/HEAD` gets "Not a directory" and the project never
    configures. This swaps in a minimal directory carrying `HEAD` and the
    checked-out `refs/heads/<branch>` -- enough for the read-the-file pattern --
    and always restores the pointer afterwards. Whatever the build writes
    there (hooks) goes with the directory.

    Git itself does not recognise the worktree while the shim is in place, so
    the window must contain nothing but the build subprocess. The swap is
    skipped entirely when `.git` is already a directory (a plain clone).
    """
    git_path = worktree / ".git"
    backup = worktree / GIT_POINTER_BACKUP

    # Self-heal: a previous run interrupted inside the window, or a restore
    # that could not finish, left the backup behind and the worktree
    # unusable by git.
    if backup.is_file() and not git_path.is_file():
        restore_worktree_pointer(worktree)

    if not git_path.is_file():
        yield False
        return

    gitdir = _pointer_gitdir(worktree, git_path.read_text(encoding="utf-8", errors="replace"))
    if gitdir is None:
        yield False
        return
    head_source = gitdir / "HEAD"
    if not head_source.is_file():
        yield False
        return

    def _git(*arguments: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(worktree), *arguments],
            capture_output=True, text=True,
        )
        return done.stdout.strip() if done.returncode == 0 else ""

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    commit = _git("rev-parse", "HEAD")

    shutil.copy2(git_path, backup)
    try:
        git_path.unlink()
        git_path.mkdir()
        shutil.copy2(head_source, git_path / "HEAD")
        # A detached HEAD holds a raw sha and needs no ref file.
        if branch and branch != "HEAD" and commit:
            ref_file = git_path / "refs" / "heads" / branch
            ref_file.parent.mkdir(parents=True, exist_ok=True)
            ref_file.write_text(f"{commit}\n", encoding="utf-8")
        yield True
    finally:
        restore_worktree_pointer(worktree)


def _pointer_gitdir(worktree: Path, pointer: str) -> Optional[Path]:
    pointer = pointer.strip()
    if not pointer.startswith("gitdir:"):
        return None
    gitdir = Path(pointer.split("gitdir:", 1)[1].strip())
    return gitdir if gitdir.is_absolute() else worktree / gitdir


def restore_worktree_pointer(worktree: Path) -> bool:
    """Put the worktree's `.git` pointer file back from its backup.

    The shim directory is first renamed out of the way -- a rename succeeds
    where deleting fails (a read-only directory a build task wrote, a Gradle
    process still writing) -- and only then deleted. The backup is removed
    only once `.git` is again a file holding the pointer, so a restore that
    cannot finish leaves it for the next build's self-heal: copying the
    pointer into a `.git` that was still a directory used to lose it.
    """
    git_path = worktree / ".git"
    backup = worktree / GIT_POINTER_BACKUP
    try:
        pointer = backup.read_bytes()
    except OSError:
        return git_path.is_file()
    if git_path.is_dir() and not git_path.is_symlink():
        doomed = git_path
        gitdir = _pointer_gitdir(worktree, pointer.decode("utf-8", errors="replace"))
        if gitdir is not None and gitdir.is_dir():
            leftover = gitdir / f"{SHIM_LEFTOVER_PREFIX}{os.getpid()}-{time.time_ns()}"
            try:
                os.replace(git_path, leftover)
                doomed = leftover
            except OSError:
                pass
        _remove_tree(doomed)
    if not git_path.exists() and not git_path.is_symlink():
        try:
            shutil.copy2(backup, git_path)
        except OSError:
            return False
    try:
        restored = git_path.is_file() and git_path.read_bytes() == pointer
    except OSError:
        restored = False
    if restored:
        backup.unlink(missing_ok=True)
    return restored


def _remove_tree(path: Path) -> None:
    """shutil.rmtree that makes a read-only entry (or its directory) writable and retries."""
    def retry_writable(function, target, _error):
        try:
            for entry in (os.path.dirname(target), target):
                os.chmod(entry, os.stat(entry).st_mode | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRUSR)
            function(target)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry_writable)
    else:  # pragma: no cover -- the venv runs 3.12
        shutil.rmtree(path, onerror=retry_writable)


def checkout_root(gradle_root: Path, top: Optional[Path] = None) -> Path:
    """The directory whose `.git` a build of `gradle_root` sees.

    find_gradle_root returns a subdirectory when settings.gradle is not at
    the repository's top (`android/`, Olvid's `obv_messenger/`), while the
    worktree's `.git` pointer sits at the top: the nearest ancestor of
    `gradle_root`, up to `top`, holding a `.git` entry (or the backup of
    one). Without `top`, or with nothing found, `gradle_root` itself.
    """
    start = Path(gradle_root)
    if top is None:
        return start
    bound = Path(top).resolve()
    current = start.resolve()
    try:
        current.relative_to(bound)
    except ValueError:
        return start
    while True:
        if (current / ".git").exists() or (current / GIT_POINTER_BACKUP).is_file():
            return current
        if current == bound or current.parent == current:
            return start
        current = current.parent


@contextlib.contextmanager
def git_directory_for_build(gradle_root: Path, top: Optional[Path] = None):
    """The shim around one Gradle build, when its scripts touch `.git/`.

    The scripts are read under `gradle_root`; the shim goes on the checkout
    holding it (checkout_root, bounded by `top`, the project or worktree
    directory). Yields the record a build result carries -- which scripts
    read the directory, relative to the checkout, and whether the shim was
    applied -- or None for a project whose scripts never name it, which
    keeps its untouched layout.
    """
    scripts = build_reads_git_directory(gradle_root)
    if not scripts:
        yield None
        return
    checkout = checkout_root(gradle_root, top)
    try:
        prefix = Path(gradle_root).resolve().relative_to(checkout.resolve())
    except ValueError:
        prefix = Path()
    scripts = [(prefix / script).as_posix() for script in scripts]
    with readable_git_directory(checkout) as applied:
        yield {"applied": bool(applied), "build_scripts_reading_git": scripts}
