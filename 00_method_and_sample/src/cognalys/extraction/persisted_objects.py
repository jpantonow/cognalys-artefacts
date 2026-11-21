"""Classes whose instances the app serializes, and where.

FreeOTP (2026-10-27, v6): a patch re-encrypted MasterKey's wrapped key under a
new AndroidKeyStore key, a key that never leaves the device. MasterKey is
written with `new Gson().toJson(mk)` into the backup preferences and read back
with `fromJson(s, MasterKey.class)` to restore with only the password; after
the patch a restore on another device cannot decrypt it. The plan said "no
stored value contract" -- the pipeline's contract followed strings put into
preferences, not objects serialized whole -- and the behaviour review passed it.

This lists, for the classes declared in the given files, every place the
checkout serializes or deserializes one of them (Gson/Moshi/Jackson toJson,
fromJson, readValue, writeValueAsString; ObjectOutputStream.writeObject), and
the classes marked serializable by declaration (Serializable, Parcelable,
@Serializable, @Parcelize). A class held in a field of a serialized class is
serialized with it, so it is listed too, through that field. Lexical and
bounded: what it finds is established, what it misses says nothing.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from pathlib import Path
from typing import Dict, Iterable, List

from cognalys.extraction.evidence_facts import _source_files

MAX_SITES = 8
_CLASS = re.compile(r"\b(?:class|object|record)\s+(?P<name>[A-Z]\w*)(?P<header>[^{;]*)")
_MARKED = re.compile(r"\b(?:Serializable|Parcelable|Externalizable)\b|@(?:kotlinx\.serialization\.)?Serializable\b|@Parcelize\b")
_FIELD = re.compile(
    r"^\s*(?:(?:private|protected|public|internal|final|transient|static|val|var)\s+)*"
    r"(?P<type>[A-Z]\w*)\s+(?P<name>m?\w+)\s*[;=]"
    r"|^\s*(?:(?:private|protected|public|internal)\s+)?(?:val|var)\s+(?P<kname>\w+)\s*:\s*(?P<ktype>[A-Z]\w*)",
    re.M)


def _declared_classes(text: str) -> Dict[str, str]:
    """name -> header text (extends/implements/annotations just before it)."""
    found = {}
    for match in _CLASS.finditer(text):
        before = text[max(0, match.start() - 160):match.start()]
        found[match.group("name")] = before.rsplit("\n\n", 1)[-1] + match.group(0)
    return found


# The three lexical shapes of a serialization site. A reader or writer call
# opens the window that runs to the end of its statement (the next ';' or
# line break); a class literal ahead of a reader call closes one.
_READER_CALL = re.compile(r"\b(?:fromJson|readValue|adapter|decodeFromString)\s*\(")
_WRITER_CALL = re.compile(r"\b(?:toJson|writeValueAsString|writeObject|encodeToString)\s*\(")
_WRITER_ARGUMENT = re.compile(r"\b(?:toJson|writeValueAsString|writeObject|encodeToString)\s*\(\s*(?P<arg>\w+)\s*[,)]")
_CLASS_LITERAL = re.compile(r"\b(?P<name>\w+)\s*(?:\.class|::class)")
_READER_NAME = re.compile(r"\b(?:fromJson|readValue)\b")
_WORD = re.compile(r"\w+")
_STATEMENT_END = re.compile(r"[;\n]")


def _statement_end(text: str, start: int) -> int:
    end = _STATEMENT_END.search(text, start)
    return end.start() if end else len(text)


def _serialization_sites(text: str, variable_types: Dict[str, str],
                         names: set) -> Dict[str, set]:
    """name -> lines of `text` that serialize or deserialize a class of `names`.

    One pass over the file for every class at once. This asked, for each
    class declared anywhere in the checkout, a regex of its own over every
    source file: Amethyst (rvsec_amethyst_20261024/run_v2, 2026-10-29) has
    6000 source files read here and thousands of declared classes, and the
    llm step sat in this loop after adjudication until the stall watch ended
    it, twice, with the finding left unanalysed.
    """
    starts = [0] + [match.end() for match in re.finditer("\n", text)]

    def line_of(offset: int) -> int:
        return bisect_right(starts, offset)

    sites: Dict[str, set] = {}

    def add(name: str, offset: int) -> None:
        if name in names:
            sites.setdefault(name, set()).add(line_of(offset))

    for call in _READER_CALL.finditer(text):
        end = _statement_end(text, call.end())
        # The class name starts inside the statement; ".class" may follow
        # after a line break, as the per-class pattern allowed.
        for literal in _CLASS_LITERAL.finditer(text, call.end(), min(len(text), end + 256)):
            if literal.start() >= end:
                break
            add(literal.group("name"), call.start())
    for call in _WRITER_CALL.finditer(text):
        end = _statement_end(text, call.end())
        for word in _WORD.finditer(text, call.end(), end):
            add(word.group(0), call.start())
    for literal in _CLASS_LITERAL.finditer(text):
        if _READER_NAME.search(text, literal.end(), _statement_end(text, literal.end())):
            add(literal.group("name"), literal.start())
    for call in _WRITER_ARGUMENT.finditer(text):
        add(variable_types.get(call.group("arg"), ""), call.start())
    return sites


def _variable_types(text: str) -> Dict[str, str]:
    types = {}
    for match in re.finditer(r"\b(?P<type>[A-Z]\w*)\s+(?P<name>[a-z]\w*)\s*=", text):
        types[match.group("name")] = match.group("type")
    for match in re.finditer(r"\b(?:val|var)\s+(?P<name>\w+)\s*(?::\s*(?P<type>[A-Z]\w*))?\s*=\s*(?P<init>[A-Z]\w*)?", text):
        types[match.group("name")] = match.group("type") or match.group("init") or ""
    return types


def persisted_object_formats(repo_root: Path, paths: Iterable[str]) -> Dict:
    """{"status", "classes": [{"class", "declared_in", "serialized_at", ...}]}."""
    root = Path(repo_root)
    wanted = [str(path) for path in paths if str(path).endswith((".java", ".kt"))]
    if not root.is_dir() or not wanted:
        return {"status": "not_applicable", "classes": []}
    sources = {}
    for path in _source_files(root):
        relative = path.relative_to(root).as_posix()
        if re.search(r"(?:^|/)src/(?:test|androidTest)\w*/", relative):
            continue
        try:
            sources[relative] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    # The files asked about are read even past the checkout's file cap:
    # Amethyst (rvsec_amethyst_20261024/run_v2, 2026-10-29) has 9542
    # Kotlin/Java files and the 6000 read first leave out 3498 production
    # ones; a finding in one of those would have its classes undeclared and
    # come back "none_in_these_files", which reads as no stored format.
    for relative in wanted:
        if relative not in sources and (root / relative).is_file():
            try:
                sources[relative] = (root / relative).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    declared = {}
    # A name declared in a file asked about stands for that declaration:
    # Amethyst's SecretEncryption (run_v2, 2026-10-29) is declared in
    # jvmAndroid, jvmMain and androidMain, and whichever the walk met first
    # stood for it, leaving the androidMain actual asked about off the list.
    asked_first = sorted(sources.items(), key=lambda item: item[0] not in wanted)
    for relative, text in asked_first:
        for name, header in _declared_classes(text).items():
            declared.setdefault(name, (relative, header, text))
    names = set(declared)
    # name -> {file: lines}, files in the order they were read.
    index: Dict[str, Dict[str, set]] = {}
    for relative, text in sources.items():
        for name, lines in _serialization_sites(text, _variable_types(text), names).items():
            index.setdefault(name, {})[relative] = lines

    def sites_of(name: str) -> List[Dict]:
        found = []
        for relative, lines in index.get(name, {}).items():
            text_lines = sources[relative].split("\n")
            for line in sorted(lines):
                found.append({"source_file_path": relative, "line": line,
                              "code": text_lines[line - 1].strip()[:160]})
                if len(found) >= MAX_SITES:
                    return found
        return found

    serialized: Dict[str, Dict] = {}
    for name, (relative, header, text) in declared.items():
        sites = sites_of(name)
        marked = bool(_MARKED.search(header))
        if sites or marked:
            serialized[name] = {"class": name, "declared_in": relative,
                                "serialized_at": sites, "declared_serializable": marked}
    # A field of a serialized class is serialized with it.
    # Read once per file: a file declaring many serialized classes was
    # scanned again for each.
    fields_by_file: Dict[str, List[tuple]] = {}
    queue = list(serialized)
    while queue:
        owner = queue.pop()
        owner_file = declared[owner][0]
        if owner_file not in fields_by_file:
            fields_by_file[owner_file] = [
                (match.group("type") or match.group("ktype"),
                 match.group("name") or match.group("kname"))
                for match in _FIELD.finditer(declared[owner][2])
            ]
        for field_type, field_name in fields_by_file[owner_file]:
            if field_type in declared and field_type not in serialized and field_type != owner:
                serialized[field_type] = {"class": field_type, "declared_in": declared[field_type][0],
                                          "serialized_at": [], "declared_serializable": False,
                                          "serialized_as_field_of": f"{owner}.{field_name}"}
                queue.append(field_type)
    in_files = [item for item in serialized.values() if item["declared_in"] in wanted]
    return {
        "status": "collected" if in_files else "none_in_these_files",
        "classes": in_files,
        "meaning": ("an instance of each class listed is written out whole and read back "
                    "(from preferences, a file, a backup or an export): its fields and how "
                    "they are encrypted form a stored format. Values already stored must still "
                    "read after the patch, and a value read back elsewhere (a restore, an "
                    "import, another device) must not come to depend on anything that side "
                    "may lack, such as a key that never leaves the device."),
    }
