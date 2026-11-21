"""Discover concise repository-local contribution guidance for the pipeline."""

from __future__ import annotations

import re
from pathlib import Path


GUIDANCE_PATTERNS = (
    "CONTRIBUTING*", "CONTRIBUTION*", ".github/CONTRIBUTING*",
    "contributing*", "contribution*", ".github/contributing*",
    "GUIDELINES*", "guidelines*", "DEVELOPMENT*", "development*",
    ".github/PULL_REQUEST_TEMPLATE*", ".github/PULL_REQUEST_TEMPLATE/*.md",
    ".github/pull_request_template*", ".github/pull_request_template/*.md",
    "docs/CONTRIBUTING*", "docs/contributing*", "README*", "readme*",
)
RELEVANT_HEADING = re.compile(
    r"contribut|pull request|develop|build|test|style|format|lint|commit",
    re.IGNORECASE,
)


def _read(path: Path, limit: int = 10000) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[:limit].strip()


def _readme_sections(text: str) -> str:
    """Keep contribution-related Markdown sections instead of a huge README."""
    lines = text.splitlines()
    selected: list[str] = []
    include = False
    level = 99
    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading:
            current_level = len(heading.group(1))
            if RELEVANT_HEADING.search(heading.group(2)):
                include = True
                level = current_level
            elif include and current_level <= level:
                include = False
        if include:
            selected.append(line)
    return "\n".join(selected).strip()


def discover_repository_guidance(repo_root: Path, total_limit: int = 24000) -> list[dict]:
    """Return prioritized guidance files with repository-relative paths."""
    repo_root = Path(repo_root)
    found: list[Path] = []
    for pattern in GUIDANCE_PATTERNS:
        for path in repo_root.glob(pattern):
            if path.is_file() and path not in found:
                found.append(path)

    def priority(path: Path) -> tuple[int, str]:
        name = path.name.lower()
        if "pull_request_template" in name:
            return (0, path.as_posix())
        if name.startswith(("contributing", "contribution")):
            return (1, path.as_posix())
        return (2, path.as_posix())

    result: list[dict] = []
    remaining = total_limit
    for path in sorted(found, key=priority):
        if remaining <= 0:
            break
        content = _read(path, min(10000, remaining))
        if path.name.lower().startswith("readme"):
            content = _readme_sections(content)
        if not content:
            continue
        content = content[:remaining]
        result.append({
            "path": path.relative_to(repo_root).as_posix(),
            "content": content,
        })
        remaining -= len(content)
    return result


APP_PURPOSE_LIMIT = 600


def app_purpose(repo_root: Path, limit: int = APP_PURPOSE_LIMIT) -> str:
    """What the app is for, in its README's own words, or "".

    The text under the README's first H1 up to the next heading, without
    images, badges and HTML. Salty's says "Exchange sensitive information
    securely by encrypting messages with a shared Salt": its ciphertext is
    sent to someone else, which no contribution section says (2026-10-29).
    """
    repo_root = Path(repo_root)
    readmes = sorted(path for pattern in ("README*", "readme*")
                     for path in repo_root.glob(pattern) if path.is_file())
    if not readmes:
        return ""
    lines = _read(readmes[0], 20000).splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(r"^#\s+\S", line) or (
                index + 1 < len(lines) and line.strip()
                and re.match(r"^=+\s*$", lines[index + 1])):
            start = index + (2 if not line.startswith("#") else 1)
            break
    if start is None:
        return ""
    body = []
    for index in range(start, len(lines)):
        line = lines[index]
        if re.match(r"^#{1,6}\s", line) or (
                index + 1 < len(lines) and line.strip()
                and re.match(r"^(?:=+|-{3,})\s*$", lines[index + 1])):
            break
        body.append(line)
    text = "\n".join(body)
    text = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", " ", text)  # linked badges
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images
    text = re.sub(r"<[^>]+>", " ", text)  # HTML, <br>, <img>
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links keep their text
    text = re.sub(r"\*{1,3}|`+|(?<!\w)__|__(?!\w)", "", text)
    text = " ".join(text.split())
    return text[:limit].rstrip()


def infer_branch_policy(guidance: list[dict]) -> dict:
    """Infer an explicitly documented PR base branch, without guessing."""
    branch_names = r"develop|development|dev|main|master"
    # Prefer explicit code-change rules. Do not associate a distant mention
    # such as "release is merged into master" with a later PR paragraph.
    patterns = (
        rf"code changes?.{{0,100}}?done on.{{0,30}}?\b({branch_names})\b",
        rf"pull requests?.{{0,100}}?(?:based on|target(?:ed)? at|merge(?:d)? "
        rf"(?:into|to)).{{0,30}}?\b({branch_names})\b",
        rf"branch:\s*({branch_names}).{{0,180}}?small changes and fixes",
    )
    for item in guidance:
        content = str(item.get("content", ""))
        normalized = re.sub(r"[*_`]", "", content)
        normalized = re.sub(r"\s+", " ", normalized)
        for pattern in patterns:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match:
                branch = match.group(1).lower()
                evidence_start = max(0, match.start() - 100)
                evidence_end = min(len(normalized), match.end() + 100)
                return {
                    "recommended_base_branch": branch,
                    "source": str(item.get("path", "")),
                    "evidence": normalized[evidence_start:evidence_end].strip(),
                }
    return {}
