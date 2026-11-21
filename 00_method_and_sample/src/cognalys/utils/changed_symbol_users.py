"""Whether every user of the symbols a patch changed sits where the patch looked.

primftpd run_v2 (2026-11-03): the adjudication raised "code_evidence_missing:
The packet does not establish the registration or negotiation sites that
would need to change to retire RC4, and caller resolution is partial", and
it stayed a publication block although a search of the worktree settles it:
`setUpDefaultCiphers` is called once, in the SshBuilder the patch edits,
and the RC4 classes (ARCFOUR128/256, BaseRC4Cipher) are named nowhere else
once the patch removed them from that list.

The search is textual and conservative. The changed symbols are the methods
the patch edits. A symbol is accounted for when every file naming it
(after the patch, in the worktree) is a file the patch changes, the
symbol's own declaring file, or the file of a subclass that is itself
accounted for (one nothing else references). A method whose
name occurs elsewhere falls back to its class: an instance method is
reached through a reference to its class or a subclass. Anything else --
a file outside the patch naming the symbol, a string literal naming it
(reflection), a name too short to search for, a symbol not found at all --
leaves the evidence gap as it was. Reflection through names built at run
time and code outside the repository remain a limitation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

_SOURCE_SUFFIXES = (".java", ".kt", ".kts", ".xml", ".pro", ".gradle")
_SKIPPED_DIRS = frozenset({".git", "build", ".gradle", "node_modules", ".idea", "out"})
_MAX_FILES = 20000
# The gap is about where a symbol is called, registered or used.
_SITES_GAP = re.compile(
    r"(?i)\b(?:call(?:er|ing)?s?\b|call\s+sites?|registration|register\w*|negotiation"
    r"|usages?|users?\s+of|references?|reach\w*|dispatch\w*)")
_METHOD_NAME = re.compile(r"(?:\bfun\s+|[\w$<>\[\],?]\s+)(?P<name>[a-z_$][\w$]*)\s*\(")
_STRING = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"")
# Names too common to say anything when searched as text.
_MIN_NAME = 4


def sites_gap(description: str) -> bool:
    """The evidence gap asks for call, registration or use sites."""
    return bool(_SITES_GAP.search(str(description or "")))


def _source_files(root: Path) -> list[Path]:
    found = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in _SKIPPED_DIRS]
        for name in files:
            if name.endswith(_SOURCE_SUFFIXES):
                found.append(Path(directory) / name)
                if len(found) > _MAX_FILES:
                    return []
    return found


def _edited_method(edit: dict) -> str:
    symbol = str(edit.get("symbol", "") or "").rsplit(".", 1)[-1]
    if re.fullmatch(r"[a-z_$][\w$]*", symbol):
        return symbol
    match = _METHOD_NAME.search(str(edit.get("original_code", "")).split("{", 1)[0])
    return match.group("name") if match else ""


_IMPORT_LINES = re.compile(r"(?:\s*import\s+[\w.*$]+(?:\s+as\s+\w+)?\s*;?\s*\n?)+\s*")


def changed_symbols(edits: Iterable[dict]) -> list[tuple[str, str]] | None:
    """(method, declaring class) for each method the patch edits.

    A deleted import is not a changed symbol: the file stopped naming that
    class, and the class's other users are not sites of this change
    (BaseRC4Cipher's removed SecretKeySpec import). Any other kind of edit
    (a declaration, a constant, a deleted block, a new file) changes
    something this search does not follow: None.
    """
    symbols = []
    for edit in edits or []:
        if not isinstance(edit, dict):
            continue
        kind = str(edit.get("edit_kind", ""))
        path = str(edit.get("source_file_path") or edit.get("source_file") or "")
        if kind == "delete" and _IMPORT_LINES.fullmatch(str(edit.get("original_code", ""))):
            continue
        if kind == "test":
            continue
        name = _edited_method(edit) if kind == "method" else ""
        if not name or not path:
            return None
        symbols.append((name, Path(path).stem))
    return list(dict.fromkeys(symbols))


def changed_symbol_users(repository: Path | str, edits: Iterable[dict],
                         changed_files: Iterable[str]) -> dict:
    """Search the worktree for the users of the symbols the patch changed.

    {"status": "accounted" | "unexplained" | "unresolved", "sites": [...],
    "unexplained": [...], "detail": str}. "accounted" only when every
    changed symbol was found and every file naming it is explained.
    """
    root = Path(repository)
    symbols = changed_symbols(edits)
    if not root.is_dir() or not symbols:
        return {"status": "unresolved", "sites": [], "unexplained": [],
                "detail": "no changed symbol or no worktree to search"}
    files = _source_files(root)
    if not files:
        return {"status": "unresolved", "sites": [], "unexplained": [],
                "detail": "the worktree has no source files to search, or too many"}
    texts = {}
    for path in files:
        try:
            texts[path.relative_to(root).as_posix()] = path.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
    changed = {str(item).replace("\\", "/") for item in changed_files or []}

    def users(name: str) -> dict:
        pattern = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
        found = {}
        for path, text in texts.items():
            lines = [number for number, line in enumerate(text.splitlines(), 1)
                     if pattern.search(line)]
            if lines:
                found[path] = lines
        return found

    def named_in_string(name: str) -> bool:
        pattern = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
        return any(pattern.search(literal) for text in texts.values()
                   for literal in _STRING.findall(text))

    def subclasses(text: str, name: str) -> bool:
        # Only a subclass is reached through the class it extends; any
        # other file naming it is a use of it, however unused that file is.
        return bool(re.search(
            rf"\b(?:extends|implements)\s+(?:[\w.]+\.)?{re.escape(name)}\b"
            rf"|:\s*(?:[\w.]+\.)?{re.escape(name)}\s*\(", text))

    sites, unexplained = [], []
    verdicts: dict[str, bool] = {}

    def class_accounted(name: str, depth: int = 0) -> bool:
        if name in verdicts:
            return verdicts[name]
        verdicts[name] = False  # a cycle explains nothing
        if len(name) < _MIN_NAME or depth > 3 or named_in_string(name):
            unexplained.append(f"{name}: cannot be searched as text")
            return False
        found = users(name)
        if not found:
            unexplained.append(f"{name}: not found in the repository")
            return False
        ok = True
        for path, lines in sorted(found.items()):
            stem = Path(path).stem
            where = f"{path}:{','.join(str(line) for line in lines[:5])}"
            if path in changed:
                sites.append(f"{name}: {where} (changed by the patch)")
            elif stem == name:
                sites.append(f"{name}: {where} (its declaration)")
            elif (stem[:1].isupper() and subclasses(texts[path], name)
                  and class_accounted(stem, depth + 1)):
                sites.append(f"{name}: {where} (in {stem}, which nothing else uses)")
            else:
                unexplained.append(f"{name}: {where}")
                ok = False
        verdicts[name] = ok
        return ok

    resolved = True
    for method, owner in symbols:
        found = users(method) if len(method) >= _MIN_NAME else {}
        if found and all(path in changed for path in found) and not named_in_string(method):
            sites.extend(f"{method}: {path}:{','.join(str(line) for line in lines[:5])} "
                         "(changed by the patch)" for path, lines in sorted(found.items()))
            continue
        # Reached through its class (or a subclass) wherever else the name
        # occurs: an overridden init is called through the interface.
        if not class_accounted(owner):
            resolved = False
    status = "accounted" if resolved and not unexplained else (
        "unexplained" if unexplained else "unresolved")
    return {"status": status, "sites": list(dict.fromkeys(sites)),
            "unexplained": list(dict.fromkeys(unexplained)),
            "detail": ("every user of " + ", ".join(method for method, _owner in symbols)
                       + " is in a file the patch changes or in a class nothing else uses")
            if status == "accounted" else "; ".join(unexplained[:5])}


def discharge_sites_gaps(notes: dict, repository: Path | str, solution: dict) -> dict:
    """The review notes with code_evidence_missing gaps about sites settled by the search.

    Only a blocker of that category whose description asks for call,
    registration or use sites (sites_gap), and only when
    changed_symbol_users finds every user accounted for: the blocker moves
    to the blocker record's discharged_blockers with the sites as evidence,
    and the search is kept as `changed_symbol_users`. Returns a new notes
    dict; the input is not changed.
    """
    notes = dict(notes or {})
    record = dict(notes.get("publication_blocker_record") or {})
    gaps = [item for item in record.get("blockers") or []
            if isinstance(item, dict) and item.get("category") == "code_evidence_missing"
            and item.get("discharged_by_pipeline_validation") is not True
            and sites_gap(str(item.get("description") or item.get("kind") or ""))]
    if not gaps:
        return notes
    edits = [item for item in solution.get("coordinated_edits") or [] if isinstance(item, dict)]
    changed = list(solution.get("changed_files") or []) or [
        str(item.get("source_file_path") or item.get("source_file") or "") for item in edits]
    evidence = changed_symbol_users(repository, edits, changed)
    notes["changed_symbol_users"] = evidence
    if evidence.get("status") != "accounted":
        return notes
    settled = {"discharged_by": "repository search of the changed symbols' users",
               "evidence": evidence["detail"], "sites": evidence["sites"]}
    record["blockers"] = [item for item in record.get("blockers") or [] if item not in gaps]
    record["discharged_blockers"] = [*(record.get("discharged_blockers") or []),
                                     *({**item, **settled} for item in gaps)]
    notes["publication_blocker_record"] = record
    return notes
