"""The caches a run leaves behind, and how to see and clear them.

- llm: the model responses and web research reused across runs
  (outputs/.llm_cache, or --llm-cache-dir), one file per request hash;
- baselines: the builds of unmodified base commits that tell a patch's build
  failure from the project's own (<clones parent>/baseline_build_cache).

Clearing one only costs time: the next run recomputes what it needs. The
class index each clone keeps inside its .git is not listed; it is keyed by
commit and rebuilt when stale.
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

CACHE_KINDS = ("llm", "baselines")


@dataclass(frozen=True)
class CacheEntry:
    path: Path
    size: int
    modified: float


def _entries(kind: str, outputs: Path) -> list[CacheEntry]:
    found = []
    if kind == "llm":
        root = outputs / ".llm_cache"
        files = [path for path in root.rglob("*") if path.is_file()] if root.is_dir() else []
        for path in files:
            stat = path.stat()
            found.append(CacheEntry(path, stat.st_size, stat.st_mtime))
    elif kind == "baselines":
        # One entry per cached baseline (a directory per base commit).
        roots = [outputs / "baseline_build_cache",
                 *outputs.glob("*/baseline_build_cache"),
                 *outputs.glob("*/*/baseline_build_cache")]
        for root in roots:
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                # Not walked: a baseline holds a whole checkout and its build
                # outputs, and summing them on /mnt/c takes minutes. Size is
                # unknown (-1); age is the entry's own.
                found.append(CacheEntry(entry, -1, entry.stat().st_mtime))
    return found


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def info(outputs: Path) -> list[str]:
    lines = []
    for kind in CACHE_KINDS:
        entries = _entries(kind, outputs)
        if not entries:
            lines.append(f"{kind:10} empty")
            continue
        oldest = min(entry.modified for entry in entries)
        age = (time.time() - oldest) / 86400
        sizes = [entry.size for entry in entries]
        size = "size n/a" if any(value < 0 for value in sizes) else _human(sum(sizes))
        lines.append(f"{kind:10} {len(entries):6} entries  {size:>9}  oldest {age:.0f} day(s)")
    return lines


def clear(outputs: Path, kinds: list[str], older_than_days: float = 0,
          dry_run: bool = False) -> tuple[int, int]:
    """Remove cache entries (older than the given age); returns (entries, bytes).

    The bytes count only entries whose size is known (not baselines).
    """
    cutoff = time.time() - older_than_days * 86400
    removed = size = 0
    for kind in kinds:
        for entry in _entries(kind, outputs):
            if older_than_days and entry.modified > cutoff:
                continue
            removed += 1
            size += max(entry.size, 0)
            if dry_run:
                continue
            if entry.path.is_dir():
                shutil.rmtree(entry.path, ignore_errors=True)
            else:
                entry.path.unlink(missing_ok=True)
    return removed, size
