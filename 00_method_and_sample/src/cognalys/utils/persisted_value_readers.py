"""Whether anything in the repository reads the value a changed method writes.

Your Local Weather (2026-11-02): `encryptKey` returns a hex PBKDF2 hash of
the licence key with a random salt it then discards, and its one caller
writes the result with
`preferences.edit().putString(Constants.KEY_PREF_WEATHER_INITIAL_TOKEN,
encryptKey(...))`. Nothing reads that key. The adjudication still raised two
"compatibility_contract" blockers ("establish every reader of
KEY_PREF_WEATHER_INITIAL_TOKEN") that no one had looked for, and they held
the draft and leaked into its text. A compatibility contract on a stored
value needs a reader; with none, there is no earlier data anyone reads back.

The search is textual and conservative: it answers "no reader" only when
every call of the changed method is the value of a key-value write, the
method itself writes nothing, and no file of the repository names any
written key (its constant, its literal or its string resource) outside the
declaration and the writes. Anything else (no call found, another use, a
dynamic key, one reference elsewhere) leaves the blocker as it was.

Refuted 2026-11-02: a header map `.put("Authorization", hash(t))`, an
`intent.putExtra("token", ...)`, an export reading `prefs.getAll()` and a
reader whose key is `"t" + "ok"` all came out "no reader". Only writes to a
persistent key-value store count now (an Editor's putX reached through
edit() or an Editor variable, a DataStore `edit { it[KEY] = ... }`); any
other put is another use. Any getAll(), a BackupAgent or a backup /
data-extraction rules file, a reader-shaped call with a non-constant key,
or a native/script source naming the key leaves the answer unknown.
Readers the search cannot see (reflection, code outside the repository)
remain a limitation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

_SOURCE_SUFFIXES = (".java", ".kt", ".xml", ".kts")
_SKIPPED_DIRS = frozenset({".git", "build", ".gradle", "node_modules", ".idea", "out"})
# Any put-shaped write whose value is an expression (maps, Bundle,
# ContentValues, Intent extras, SharedPreferences.Editor, ...).
_WRITE = re.compile(
    r"(?:\.|\b)(?P<op>put(?:String|Int|Long|Boolean|Float|Double|StringSet|Extra)?)\s*\(\s*"
    r"(?P<key>[^,()]+(?:\([^()]*\))?)\s*,\s*(?P<value>.*)")
_EDITOR_PUT = frozenset({"putString", "putInt", "putLong", "putBoolean", "putFloat",
                         "putStringSet"})
# DataStore: `prefs[KEY] = value` inside `edit { ... }`.
_DATASTORE_WRITE = re.compile(r"\b[\w$]+\s*\[\s*(?P<key>[^\]]+?)\s*\]\s*=\s*(?P<value>.*)")
_GET_ALL = re.compile(r"\.getAll\s*\(\s*\)")
_BACKUP = re.compile(r"\bBackupAgent(?:Helper)?\b|fullBackupContent|dataExtractionRules"
                     r"|<full-backup-content\b|<data-extraction-rules\b")
_READER_CALL = re.compile(
    r"(?P<receiver>[\w$]*)\s*\.\s*(?P<op>getString|getInt|getLong|getBoolean|getFloat"
    r"|getStringSet|contains)\s*\(\s*(?P<key>(?:\"(?:[^\"\\\n]|\\.)*\"|\([^()]*\)|[^,()\"])*)(?P<end>[,)])")
_CONSTANT_KEY = re.compile(
    r'"(?:[^"\\]|\\.)*"|(?:[\w$]+\.)*R\.string\.\w+|(?:[\w$]+\.)*[A-Z][A-Z0-9_]*')
_RESOURCE_RECEIVER = re.compile(r"(?:m?[Cc]ontext|ctx|[Rr]esources|res|activity|application"
                                r"|requireContext|requireActivity)")
_PREFS_RECEIVER =re.compile(r"pref|setting|shared|^sp$|store", re.I)
_OTHER_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".js", ".mjs", ".ts",
                          ".rs", ".go", ".py", ".dart", ".swift", ".m", ".mm")
# What the method's own body may not do for its result alone to be the value.
_WRITES_INSIDE = re.compile(
    r"\bput(?:String|Int|Long|Boolean|Float|Double|StringSet|Extra)?\s*\(|openFileOutput\s*\("
    r"|FileOutputStream\s*\(|FileWriter\s*\(|\.write(?:Text|Bytes)?\s*\(|\.commit\s*\(|\.apply\s*\(")
_DECLARATION_BEFORE = re.compile(
    r"(?:\bfun\s+(?:[\w.<>]+\.)?|(?<!\breturn)(?<!\bnew)(?<!\bthrow)\b[\w$<>\[\],?]+\s+)$")


def _source_files(root: Path, suffixes: tuple = _SOURCE_SUFFIXES) -> Iterable[Path]:
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in _SKIPPED_DIRS]
        for name in files:
            if name.endswith(suffixes):
                yield Path(directory) / name


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _key_names(key: str) -> dict[str, str]:
    """The ways a key expression can be named elsewhere: constant, literal, resource."""
    key = key.strip()
    literal = re.fullmatch(r'"((?:[^"\\]|\\.)*)"', key)
    if literal:
        return {"literal": literal.group(1)}
    resource = re.search(r"\bR\.string\.(\w+)", key)
    if resource:
        return {"resource": resource.group(1)}
    constant = re.fullmatch(r"(?:[\w$]+\.)*([A-Za-z_$][\w$]*)", key)
    if constant:
        return {"constant": constant.group(1)}
    return {}


def _persistent_write(lines: list[str], index: int, text: str, write) -> bool:
    """Whether a put on lines[index] writes to a persistent key-value store."""
    if write.group("op") not in _EDITOR_PUT:
        return False
    before = lines[index][:write.start("op")]
    if re.search(r"\bedit\s*\(\s*\)", before):
        return True
    receiver = re.search(r"([\w$]+)\s*\.\s*$", before)
    if receiver:
        name = re.escape(receiver.group(1))
        return bool(re.search(
            rf"\bEditor\s*\??\s*{name}\b|\b{name}\s*:\s*(?:SharedPreferences\.)?Editor\b"
            rf"|\b{name}\s*=\s*[^;\n]*\bedit\s*\(\s*\)", text))
    # A bare putX inside a Kotlin `edit { ... }` block (core-ktx).
    window = "\n".join(lines[max(0, index - 5):index + 1])
    return bool(re.search(r"\bedit\s*(?:\([^()]*\))?\s*\{", window))


def _datastore_write(lines: list[str], index: int, match_start: int):
    """The DataStore `it[KEY] = value` holding the call, inside an edit { } block."""
    found = None
    for candidate in _DATASTORE_WRITE.finditer(lines[index]):
        if candidate.start("value") <= match_start:
            found = candidate
    if found is None:
        return None
    window = "\n".join(lines[max(0, index - 5):index + 1])
    return found if re.search(r"\.edit\s*\{", window) else None


def unread_written_keys(repository: Path | str, method_name: str,
                        method_body: str = "") -> dict[str, Any]:
    """Whether the value `method_name` returns is only written under keys no code reads.

    Returns {"status", "keys", "writes", "readers", "detail"}; status
    "no_reader" is the only one that settles a compatibility question.
    """
    root = Path(repository)
    result: dict[str, Any] = {"status": "unknown", "keys": [], "writes": [], "readers": [],
                              "detail": ""}
    name = str(method_name or "").strip()
    if not name or not re.fullmatch(r"[A-Za-z_$][\w$]*", name) or not root.is_dir():
        result["detail"] = "no method name or repository to search"
        return result
    if method_body and _WRITES_INSIDE.search(method_body):
        result.update(status="writes_inside", detail="the method itself writes or stores data")
        return result
    texts = {path: _read(path) for path in _source_files(root)}
    call = re.compile(rf"(?<![\w$.]){re.escape(name)}\s*\(|(?<=\.){re.escape(name)}\s*\(")
    writes: list[dict[str, Any]] = []
    keys: dict[str, dict[str, str]] = {}
    for path, text in texts.items():
        if not path.name.endswith((".java", ".kt")):
            continue
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            for match in call.finditer(line):
                before = line[:match.start()]
                if _DECLARATION_BEFORE.search(before.rstrip() + " ") and not re.search(
                        r"[=(,.:!]\s*$|\breturn\s*$", before):
                    continue  # the declaration, not a call
                write = None
                for candidate in _WRITE.finditer(line):
                    if candidate.start("value") <= match.start():
                        write = candidate
                if write is not None and not _persistent_write(lines, number - 1, text, write):
                    result.update(status="other_use",
                                  detail=f"{path.relative_to(root).as_posix()}:{number} passes "
                                         f"{name} to {write.group('op')}, not a persistent "
                                         "key-value store")
                    return result
                if write is None:
                    write = _datastore_write(lines, number - 1, match.start())
                if write is None:
                    result.update(status="other_use",
                                  detail=f"{path.relative_to(root).as_posix()}:{number} uses "
                                         f"{name} other than as a stored value")
                    return result
                key = write.group("key").strip()
                names = _key_names(key)
                if not names:
                    result.update(status="dynamic_key",
                                  detail=f"the key {key!r} is not a constant, literal or resource")
                    return result
                keys[key] = names
                writes.append({"file": path.relative_to(root).as_posix(), "line": number,
                               "key": key})
    if not writes:
        result.update(status="no_call_found", detail=f"no call of {name} was found")
        return result
    for path, text in texts.items():
        relative = path.relative_to(root).as_posix()
        if _GET_ALL.search(text):
            result.update(status="unknown", detail=f"{relative} reads every preference (getAll)")
            return result
        if _BACKUP.search(text):
            result.update(status="unknown", detail=f"{relative} backs up or exports app data")
            return result
        if path.name.endswith((".java", ".kt")):
            for reader in _READER_CALL.finditer(text):
                key = reader.group("key").strip()
                if reader.group("op") == "contains" and not _PREFS_RECEIVER.search(
                        reader.group("receiver")):
                    continue
                if reader.group("op") != "contains" and reader.group("end") != ",":
                    continue  # one argument: Context/Bundle getString, not a preference read
                if _RESOURCE_RECEIVER.fullmatch(reader.group("receiver")):
                    continue  # Context/Resources.getString(resId, formatArgs)
                if key and not _CONSTANT_KEY.fullmatch(key):
                    result.update(status="unknown",
                                  detail=f"{relative} reads {reader.group('op')}({key}), "
                                         "a key the search cannot resolve")
                    return result
    write_lines = {(item["file"], item["line"]) for item in writes}
    readers = []
    for key, names in keys.items():
        patterns = []
        if "constant" in names:
            constant = re.escape(names["constant"])
            patterns.append((re.compile(rf"(?<![\w$]){constant}(?![\w$])"),
                             re.compile(rf"(?<![\w$]){constant}\s*(?::\s*[\w.<>?]+\s*)?=")))
            declared = re.compile(
                rf"(?<![\w$]){constant}\s*(?::\s*[\w.<>?]+\s*)?=\s*\"((?:[^\"\\]|\\.)*)\"")
            for text in texts.values():
                found = declared.search(text)
                if found:
                    names["literal"] = found.group(1)
                    break
        if "literal" in names:
            patterns.append((re.compile(re.escape(f'"{names["literal"]}"')), None))
        if "resource" in names:
            resource = re.escape(names["resource"])
            patterns.append((re.compile(rf"\bR\.string\.{resource}\b|@string/{resource}\b"), None))
            patterns.append((re.compile(rf'name="{resource}"'),
                             re.compile(rf'<string\s+name="{resource}"')))
        for path, text in texts.items():
            relative = path.relative_to(root).as_posix()
            for number, line in enumerate(text.splitlines(), 1):
                if (relative, number) in write_lines:
                    continue
                # The key's own declaration names its literal too.
                if any(declaration and declaration.search(line) for _p, declaration in patterns):
                    continue
                for pattern, _declaration in patterns:
                    if pattern.search(line):
                        readers.append({"file": relative, "line": number, "key": key,
                                        "text": line.strip()[:160]})
                        break
    spellings = {value for names in keys.values() for kind, value in names.items()
                 if kind in ("constant", "literal", "resource")}
    for path in _source_files(root, _OTHER_SOURCE_SUFFIXES):
        text = _read(path)
        named = sorted(item for item in spellings if item and item in text)
        if named:
            result.update(status="unknown", keys=sorted(keys), writes=writes,
                          detail=f"{path.relative_to(root).as_posix()} (outside Java/Kotlin/XML) "
                                 f"names {named[0]}")
            return result
    result.update(keys=sorted(keys), writes=writes, readers=readers)
    if readers:
        result.update(status="read", detail=f"{len(readers)} other reference(s) to the written key")
    else:
        result.update(status="no_reader",
                      detail="every call of " + name + " is the value of a write under "
                             + ", ".join(sorted(keys)) + ", and no other code names that key")
    return result


def discharge_compatibility_without_readers(notes: dict, repository: Path | str,
                                            solution: dict) -> dict:
    """The review notes with compatibility_contract blockers settled by the search.

    Only when the patch changes the one method (plus constants it uses) and
    unread_written_keys finds no reader: each blocker moves to the blocker
    record's discharged_blockers with the evidence, its consideration is
    dropped, and the search is kept as `persisted_value_readers`. Returns
    a new notes dict; the input is not changed.
    """
    notes = dict(notes or {})
    record = dict(notes.get("publication_blocker_record") or {})
    category = "compatibility_contract"
    blockers = [item for item in record.get("blockers") or []
                if isinstance(item, dict) and item.get("category") == category]
    considerations = [item for item in notes.get("publication_considerations") or []
                      if isinstance(item, dict) and item.get("category") == category]
    if not blockers and not considerations:
        return notes
    method = str(solution.get("method_name", "") or "")
    edits = [item for item in solution.get("coordinated_edits") or [] if isinstance(item, dict)]
    if any(str(item.get("edit_kind", "")) not in ("method", "constant", "test")
           or (item.get("edit_kind") == "method"
               and str(item.get("symbol", "")).rsplit(".", 1)[-1] != method)
           for item in edits):
        return notes
    evidence = unread_written_keys(repository, method, str(solution.get("original_method_body", "")))
    notes["persisted_value_readers"] = evidence
    if evidence.get("status") != "no_reader":
        return notes
    settled = {"discharged_by": "no reader of the written key", "evidence": evidence["detail"],
               "writes": evidence["writes"]}
    record["blockers"] = [item for item in record.get("blockers") or [] if item not in blockers]
    record["discharged_blockers"] = [*(record.get("discharged_blockers") or []),
                                     *({**item, **settled} for item in blockers or considerations)]
    notes["publication_considerations"] = [
        item for item in notes.get("publication_considerations") or []
        if item not in considerations]
    # The plan's checklist lines asking for the readers of that same key are
    # answered by the same search.
    names = [re.escape(name.rsplit(".", 1)[-1]) for name in evidence["keys"]]
    asks = re.compile(r"(?<![\w$])(?:" + "|".join(names) + r")(?![\w$])") if names else None
    if asks:
        for holder, key in ((notes, "publication_blockers"), (record, "plan_publication_blockers")):
            if key in holder:
                holder[key] = [line for line in holder.get(key) or []
                               if not asks.search(str(line))]
    notes["publication_blocker_record"] = record
    return notes


# How the search reaches evidence extraction, adjudication, planning and the
# behaviour review (Your Local Weather run_v3, 2026-11-02: with only the PR
# step reading it, gpt-6.1-terra ended encryptKey as NO_SAFE_FIX because "no
# verification or decryption path ... is established" for the PBKDF2 hash
# written to KEY_PREF_WEATHER_INITIAL_TOKEN, which nothing reads).
NO_READER_GUIDANCE = (
    "persisted_value_readers, when present, is a repository search this "
    "pipeline ran: every call of the reported method is the value of a "
    "key-value write under the listed keys, and no code in the repository "
    "names those keys anywhere else (no reader, no getAll, no backup rules). "
    "A stored value nothing reads has no consumer to keep compatible: a "
    "changed value, length or format there breaks no earlier data, so "
    "stored-value compatibility is not a reason for NO_SAFE_FIX, an issue, a "
    "blocker or a regression for that value. It says nothing about whether "
    "the finding is real. "
)


def readers_evidence(result: dict | None, method_name: str) -> dict:
    """The search as evidence, only when it found no reader; {} otherwise.

    unknown, read, other_use and the rest add nothing: only the conservative
    answer is passed on.
    """
    if not isinstance(result, dict) or result.get("status") != "no_reader":
        return {}
    writes = [item for item in result.get("writes") or [] if isinstance(item, dict)]
    sites = ", ".join(f"{item.get('key')} at {item.get('file')}:{item.get('line')}"
                      for item in writes)
    return {
        "status": "no_reader",
        "method": method_name,
        "keys": list(result.get("keys") or []),
        "writes": writes,
        "statement": (f"The value returned by {method_name} is written only under "
                      f"preference key(s) {sites}, and no code in the repository reads "
                      "those keys."),
        "limits": "Textual search of the repository; reflection and code outside "
                  "the repository are not visible.",
    }
