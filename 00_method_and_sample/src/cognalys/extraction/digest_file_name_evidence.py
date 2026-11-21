"""Whether a reported digest's value names a file the application stores.

RedReader's General.sha1 (exp_final_20261019) was refused every time because
its SHA-1 forms the name of the subreddit database,
`General.sha1(user.username.getBytes()) + "_" + type + "_subreddits.db"`: a
digest computed the new way names a different file and the user's data is
lost. That is a migration problem with a known shape -- rename the old file
before opening it -- and not a reason to leave the weak digest in place, but
the policy could only say so once the pipeline recorded that the value is a
file name. This records it.

Flagged: a call of the reported method, outside its own declaration, whose
statement builds a file name with it -- concatenated with a literal that ends
in a data-file extension, or passed to an API that opens or names a file.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

_SOURCE_SUFFIXES = (".java", ".kt")
_SKIPPED = {".git", ".gradle", "build", "node_modules"}
_FILE_EXTENSION_LITERAL = re.compile(
    r"\"[^\"\n]*\.(?:db|sqlite|sqlite3|realm|json|dat|bin|cache|txt|xml|log|ser|png|jpe?g|webp|gif)\"",
    re.IGNORECASE,
)
_FILE_API = re.compile(
    r"\b(?:File|openOrCreateDatabase|getDatabasePath|openFileOutput|openFileInput"
    r"|deleteDatabase|getFileStreamPath)\s*\(")


# A line ending in one of these goes on to the next; "*/" closes a comment.
_CONTINUES = re.compile(r"(?<!\*)[(\[,+\-*/=.:&|?]\s*$")


def _statement(content: str, position: int) -> str:
    # A "${" opens a Kotlin string template, not a block: starting there cut
    # off the File(...) call the template sits in.
    block = max((m.end() for m in re.finditer(r"(?<!\$)\{", content[:position])),
                default=0)
    start = max(content.rfind(";", 0, position) + 1, block,
                content.rfind("\n\n", 0, position), 0)
    # Kotlin: an earlier line that does not visibly continue ends the
    # previous statement.
    line_start = content.rfind("\n", 0, position)
    while line_start > start:
        previous_start = content.rfind("\n", 0, line_start)
        if not _CONTINUES.search(content[max(previous_start, start):line_start]):
            start = line_start + 1
            break
        line_start = previous_start
    end = content.find(";", position)
    end = len(content) if end < 0 else end
    # Kotlin has no semicolons: a line ends the statement unless it visibly
    # continues (open call, operator, comma) -- otherwise the next ";" lies
    # past unrelated declarations and comments.
    for newline in re.finditer(r"\n", content[position:end]):
        line_end = position + newline.start()
        if not _CONTINUES.search(content[position:line_end]):
            end = line_end
            break
    return content[start:end if end > 0 else len(content)].strip(" ;\n\t")


def digest_file_name_evidence(error: Dict) -> Dict:
    """``{"names_stored_file": True, ...}`` when the digest names a stored file."""
    method = str(error.get("method_name", "")).strip()
    # An empty path is Path("."), the working directory: scanning it walked
    # every cloned project under outputs/ whenever a finding had no checkout.
    raw_repo = str(error.get("repository_path") or "").strip()
    repo = Path(raw_repo)
    if (not raw_repo or not method or not re.fullmatch(r"[A-Za-z_]\w*", method)
            or not repo.is_dir()):
        return {}
    call = re.compile(rf"(?<![\w$]){re.escape(method)}\s*\(")
    declaration = re.compile(
        rf"\b(?:fun|static|public|private|protected|internal)\b[^;{{=]*?(?<![\w$.]){re.escape(method)}\s*\(")
    sites: List[Dict] = []
    other_uses = 0
    for path in repo.rglob("*"):
        if (path.suffix not in _SOURCE_SUFFIXES or not path.is_file()
                or _SKIPPED & set(path.relative_to(repo).parts)
                or "test" in str(path.relative_to(repo)).lower()):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in call.finditer(content):
            line_start = content.rfind("\n", 0, match.start()) + 1
            line_end = content.find("\n", match.start())
            line = content[line_start:line_end if line_end >= 0 else len(content)]
            if declaration.search(line):
                continue
            statement = _statement(content, match.start())
            if _FILE_EXTENSION_LITERAL.search(statement) or _FILE_API.search(statement):
                sites.append({
                    "path": str(path.relative_to(repo)).replace("\\", "/"),
                    "line": content.count("\n", 0, match.start()) + 1,
                    "statement": re.sub(r"\s+", " ", statement)[:200],
                })
            else:
                other_uses += 1
    # Every use must be a file name. DSub's md5Hex names cached cover images
    # and also computes the Subsonic authentication token, which the server
    # checks with MD5: a rename migration would be the wrong policy there.
    if not sites or other_uses:
        return {}
    first = sites[0]
    return {
        "names_stored_file": True,
        "file_name_sites": sites[:8],
        "maintainer_facts": {
            "stored_file_name": (
                # A reader-facing sentence: the file and line, never the raw
                # statement (InterTune, 2026-10-27: the statement ran into the
                # next KDoc and pasted half a comment into the PR text).
                f"The digest is part of the name of a file the app stores "
                f"({first['path'].rsplit('/', 1)[-1]}, line {first['line']}), so the "
                "change renames existing files to the new name before they are "
                "opened instead of leaving them behind."
            ),
        },
    }
