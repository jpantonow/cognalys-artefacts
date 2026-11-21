"""Fast, bounded repository source index with a portable fallback."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path


SOURCE_SUFFIXES = {".java", ".kt", ".kts"}
SKIPPED_PARTS = {".git", ".gradle", "build", "dist", "node_modules"}
_INDEX_CACHE: dict[tuple[str, int, int], dict] = {}


def _portable_relative_path(value: str) -> Path:
    """Interpret ripgrep output from either the native or Windows executable."""
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return Path(normalized)


def _eligible(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_SUFFIXES and not any(
        part.lower() in SKIPPED_PARTS for part in path.parts
    )


def _fallback_index(repo_root: Path, max_file_chars: int,
                    max_total_chars: int) -> dict:
    paths = []
    sources = {}
    total = 0
    truncated = False
    for directory, names, filenames in os.walk(repo_root):
        # Sorted, so the walk -- and which files fit under the cap -- does
        # not depend on the order the filesystem happens to list entries in.
        names[:] = sorted(name for name in names if name.lower() not in SKIPPED_PARTS)
        base = Path(directory)
        for filename in sorted(filenames):
            path = base / filename
            paths.append(path)
            if not _eligible(path):
                continue
            try:
                if path.stat().st_size > max_file_chars:
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if total + len(content) > max_total_chars:
                truncated = True
                continue
            sources[path] = content
            total += len(content)
    return {
        "paths": paths,
        "source_cache": sources,
        "metadata": {
            "backend": "python_walk",
            "source_files": len(sources),
            "total_source_chars": total,
            "truncated": truncated,
        },
    }


def _stream_sources(command: list[str], repo_root: Path,
                    max_total_chars: int) -> tuple[dict[Path, list[str]], int, bool]:
    """Each file's source lines from ripgrep's JSON stream, up to the cap."""
    process = subprocess.Popen(
        command,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    chunks: dict[Path, list[str]] = {}
    total = 0
    truncated = False
    assert process.stdout is not None
    for output_line in process.stdout:
        try:
            event = json.loads(output_line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data", {})
        relative = str(data.get("path", {}).get("text", ""))
        text = str(data.get("lines", {}).get("text", ""))
        if not relative:
            continue
        if total + len(text) > max_total_chars:
            truncated = True
            process.terminate()
            break
        path = repo_root / _portable_relative_path(relative)
        chunks.setdefault(path, []).append(text)
        total += len(text)
    try:
        _, stderr = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        _, stderr = process.communicate()
    if process.returncode not in (0, 1, -15) and not truncated:
        raise RuntimeError(stderr.strip() or "rg source index failed")
    return chunks, total, truncated


def build_repository_index(repo_root: Path, max_file_chars: int = 500_000,
                           max_total_chars: int = 60_000_000, *, refresh: bool = False) -> dict:
    """Index source text once using ripgrep, falling back to Python walking."""
    cache_key = (str(repo_root.resolve()), max_file_chars, max_total_chars)
    if not refresh and cache_key in _INDEX_CACHE:
        cached = _INDEX_CACHE[cache_key]
        metadata = dict(cached["metadata"])
        metadata["cache_hit"] = True
        return {
            "paths": cached["paths"],
            "source_cache": cached["source_cache"],
            "metadata": metadata,
        }
    started = time.monotonic()
    # WSL commonly exposes a Windows rg.exe through PATH even when a native
    # Linux `rg` package is not installed.
    rg = shutil.which("rg") or shutil.which("rg.exe")
    if not rg:
        result = _fallback_index(repo_root, max_file_chars, max_total_chars)
        result["metadata"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["metadata"]["fallback_reason"] = "rg_not_found"
        _INDEX_CACHE[cache_key] = result
        return result

    try:
        files_result = subprocess.run(
            [rg, "--files", "--hidden", "--glob", "!.git/**",
             "--glob", "!.gradle/**", "--glob", "!**/build/**",
             "--glob", "!**/dist/**", "--glob", "!**/generated/**",
             "--glob", "!**/node_modules/**"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if files_result.returncode not in (0, 1):
            raise RuntimeError(files_result.stderr.strip() or "rg --files failed")
        # Sorted: ripgrep lists files in the order its threads finish, so the
        # same checkout gave a different order -- and a different evidence
        # packet, prompt and LLM cache key -- on every run.
        paths = sorted(
            repo_root / _portable_relative_path(line)
            for line in files_result.stdout.splitlines() if line
        )

        command = [
            rg, "--json", "--line-number", "--no-messages",
            "--max-filesize", str(max_file_chars),
            "--glob", "*.java", "--glob", "*.kt", "--glob", "*.kts",
            "--glob", "!.git/**", "--glob", "!.gradle/**",
            "--glob", "!**/build/**", "--glob", "!**/dist/**",
            "--glob", "!**/generated/**", "--glob", "!**/node_modules/**",
            "^", ".",
        ]
        chunks, total, truncated = _stream_sources(command, repo_root, max_total_chars)
        if truncated:
            # Which files fit under the cap depended on thread timing too.
            # Sorting inside ripgrep makes the cut deterministic; it runs
            # single-threaded (four times slower on the largest checkouts),
            # so only here, where the cap was actually reached.
            chunks, total, truncated = _stream_sources(
                [command[0], "--sort", "path", *command[1:]], repo_root, max_total_chars,
            )
        # Each file's lines arrive together and in order, so only the order
        # of the files needs fixing.
        sources = {path: "".join(chunks[path]) for path in sorted(chunks)}
        result = {
            "paths": paths,
            "source_cache": sources,
            "metadata": {
                "backend": "ripgrep_stream",
                "source_files": len(sources),
                "repository_files": len(paths),
                "total_source_chars": total,
                "truncated": truncated,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            },
        }
        _INDEX_CACHE[cache_key] = result
        return result
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        result = _fallback_index(repo_root, max_file_chars, max_total_chars)
        result["metadata"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["metadata"]["fallback_reason"] = str(exc)
        _INDEX_CACHE[cache_key] = result
        return result
