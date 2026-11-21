"""Where the value a reported method returns leaves the app.

Salty (2026-10-29, run_v2): HashUtils.encode's ciphertext goes through
SaltyViewModel.encodeMessage into a Compose state variable and from there to
`clipboardManager.setText(AnnotatedString(encodedResult))`; the UI says to
share it with the recipient. Messages in the patch's new `v1:` format cannot
be read by Salty 1.1 or older, and none of the adjudication, plan or review
requests said a word about the clipboard: the pipeline's stored-format facts
follow objects serialized whole (persisted_objects) and strings put into
preferences, not a value handed to another installation.

This follows the value from each method whose output the plan may change:
its call sites (qualified by the declaring class or an instance in a file
that names that class, or any call in the declaring file), the variable the
result lands in -- a local within its function, a state variable within the
function that declares it (Compose state written in one lambda and read in
another), a field within its file -- the variables and objects it is copied
into, and the functions that return it, up to three hops. It lists the lines
where the value reaches an export sink, and the UI labels of the input
fields bound to the arguments at those call sites. Lexical and bounded: what
it finds is established, what it misses says nothing.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from cognalys.extraction.evidence_facts import _source_files

MAX_HOPS = 3
MAX_SINKS = 12
MAX_LABELS = 8
MAX_NAMES_PER_SITE = 8

# First match wins: a content-provider insert is an export, a DAO insert is
# local storage.
_SINKS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("clipboard", re.compile(
        r"\bsetPrimaryClip\s*\(|\bClipData\.new\w*\s*\(|clip\w*\s*\.\s*setText\s*\(",
        re.IGNORECASE)),
    ("share_intent", re.compile(
        r"\bEXTRA_TEXT\b|\bACTION_SEND\w*\b|\bcreateChooser\s*\(|\bputExtra\s*\(")),
    ("content_provider", re.compile(
        r"\bcontentResolver\s*\.\s*(?:insert|update)\s*\("
        r"|\bgetContentResolver\(\)\s*\.\s*(?:insert|update)\s*\(")),
    ("network", re.compile(
        r"\btoRequestBody\s*\(|\bRequestBody\s*\.\s*(?:create|Companion)\b|\bFormBody\b"
        r"|\.(?:post|put|patch)\s*\(\s*\w*[Bb]ody\b|\bgetOutputStream\s*\(")),
    ("file_or_stream", re.compile(
        r"\bFileWriter\b|\bFileOutputStream\b|\bopenOutputStream\s*\("
        r"|\.\s*write(?:Text|Bytes|Utf|UTF|String)?\s*\(|\bbufferedWriter\s*\(")),
    ("shared_preferences", re.compile(
        r"\.\s*put(?:String|Int|Long|Boolean|Float|StringSet)\s*\(")),
    ("database", re.compile(
        r"\.\s*(?:insert|update|upsert)\w*\s*\(|\bContentValues\b")),
)
# Sinks through which the value reaches another installation, a peer or a
# file the user takes elsewhere -- possibly an older version of this app.
EXTERNAL_SINK_KINDS = frozenset({
    "clipboard", "share_intent", "content_provider", "network", "file_or_stream",
})

_KEYWORD_PREFIX = re.compile(r"\b(?:else|return|throw|yield|await|case|in|is|as|new)\s*$")
_DECLARATION_PREFIX = re.compile(
    r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*"
    r"(?:(?:public|private|protected|static|final|synchronized|abstract|override|internal"
    r"|open|suspend|inline|operator|native)\s+)*"
    r"(?:fun\s+(?:<[^>]+>\s*)?(?:[\w.?]+\.)?|[\w<>\[\],.?]+\s+)$")
_CLASS = re.compile(r"\b(?:class|object|interface)\s+(?P<name>[A-Z]\w*)")
_FUNCTION = re.compile(
    r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w.?]+\.)?(?P<kname>\w+)\s*\("
    r"|^\s*(?:@\w+\s+)*(?:(?:public|private|protected|static|final|synchronized|abstract)\s+)*"
    r"(?!(?:return|new|else|throw|case)\b)[\w<>\[\],.?]+\s+"
    r"(?!(?:if|for|while|switch|catch|synchronized)\b)(?P<jname>[a-z]\w*)\s*\([^;]*$")
_LABEL = re.compile(
    r"\b(?:label|placeholder)\s*=\s*\{\s*Text\s*\(\s*(?:text\s*=\s*)?\"(?P<compose>[^\"]+)\""
    r"|\b(?:hint|label)\s*=\s*\"(?P<plain>[^\"]+)\""
    r"|\bsetHint\s*\(\s*\"(?P<java>[^\"]+)\"")
_ASSIGNED = re.compile(
    r"^\s*(?:(?:val|var|final)\s+)?(?:[\w<>\[\],.?]+\s+)?(?P<name>[A-Za-z_][\w.]*)\s*"
    r"(?::\s*[^=]+?)?\s*=(?!=)(?P<value>.*)$")


def _is_test(relative: str) -> bool:
    return bool(re.search(r"(?:^|/)src/(?:test|androidTest|sharedTest)\w*/|(?:^|/)tests?/", relative))


def _comment(line: str) -> bool:
    return line.lstrip().startswith(("//", "*", "/*"))


def _sink_kind(line: str) -> str:
    return next((kind for kind, pattern in _SINKS if pattern.search(line)), "")


def _block_end(lines: List[str], start: int) -> int:
    depth, opened = 0, False
    for index in range(start, len(lines)):
        for char in lines[index]:
            if char == "{":
                depth += 1
                opened = True
            elif char == "}":
                depth -= 1
        if opened and depth <= 0:
            return index
    return len(lines) - 1


def _function_scope(lines: List[str], index: int) -> Tuple[int, int]:
    """(first, last) line of the innermost function around `index`; the file otherwise."""
    for back in range(index, -1, -1):
        if _FUNCTION.search(lines[back]) and not _comment(lines[back]):
            if _block_end(lines, back) >= index:
                return back, _block_end(lines, back)
    return 0, len(lines) - 1


def _enclosing(lines: List[str], index: int) -> Tuple[str, str]:
    """(class, function) around line `index`."""
    start, _ = _function_scope(lines, index)
    match = _FUNCTION.search(lines[start]) if lines else None
    function = (match.group("kname") or match.group("jname") or "") if match else ""
    owner = ""
    for line in reversed(lines[:index + 1]):
        found = _CLASS.search(line)
        if found and not _comment(line):
            owner = found.group("name")
            break
    return owner, function


def _scope_of(lines: List[str], name: str, index: int, declared_here: bool) -> Tuple[int, int]:
    """Where a variable written at line `index` can be read."""
    if declared_here:
        return index, _function_scope(lines, index)[1]
    declaration = re.compile(rf"\b(?:val|var)\s+{re.escape(name)}\b|[\w>\]]\s+{re.escape(name)}\s*[;=]")
    whole = (0, len(lines) - 1)
    for back in range(index, -1, -1):
        if declaration.search(lines[back]):
            scope = _function_scope(lines, back)
            return whole if scope == whole else (back, scope[1])
    return whole


def _opening_line(lines: List[str], index: int) -> Optional[int]:
    """The line whose parenthesis is still open at line `index`, or None.

    None as well when a brace opens first: the line is then in a block or a
    lambda body, not in an argument list.
    """
    parens = braces = 0
    for back in range(index, max(-1, index - 40), -1):
        for char in reversed(lines[back]):
            if char == ")":
                parens += 1
            elif char == "(":
                if parens == 0:
                    return back
                parens -= 1
            elif char == "}":
                braces += 1
            elif char == "{":
                if braces == 0:
                    return None
                braces -= 1
    return None


def _writes(line: str, name: str) -> bool:
    escaped = re.escape(name)
    return bool(re.search(rf"\b(?:val|var)\s+{escaped}\b", line)
                or re.search(rf"(?<![\w.]){escaped}\s*=(?!=)", line))


def _declares(line: str, name: str) -> bool:
    escaped = re.escape(name)
    return bool(re.search(rf"\b(?:val|var|final)\s+{escaped}\b", line)
                or re.search(rf"[\w>\]]\s+{escaped}\s*(?::[^=]+)?=", line))


def _sink(kind: str, relative: str, index: int, lines: List[str], via: List[str],
          holder: str) -> Dict:
    return {"kind": kind, "source_file_path": relative, "line": index + 1,
            "code": lines[index].strip()[:200], "value_held_by": holder,
            "external": kind in EXTERNAL_SINK_KINDS, "via": list(via)}


def _follow(relative: str, lines: List[str], name: str, scope: Tuple[int, int],
            via: List[str]) -> Tuple[List[Dict], bool]:
    """Sinks the value held by `name` reaches within `scope`, and whether it is returned."""
    sinks: List[Dict] = []
    returned = False
    tracked = {name}
    queue = [(name, scope)]
    while queue and len(tracked) <= MAX_NAMES_PER_SITE:
        current, (low, high) = queue.pop(0)
        read = re.compile(rf"(?<![\w.]){re.escape(current)}\b")
        for index in range(low, min(high, len(lines) - 1) + 1):
            line = lines[index]
            if _comment(line) or not read.search(line):
                continue
            assigned = _ASSIGNED.match(line)
            if assigned and read.search(assigned.group("value")):
                opening = _opening_line(lines, index)
                if not re.match(r"\s*(?:val|var)\b", line) and (
                        opening is not None or line.rstrip().endswith(",")):
                    # A named argument (`encodedMessage = encoded,`): the
                    # object that call builds carries the value.
                    if opening is None:
                        continue
                    kind = _sink_kind(lines[opening])
                    holder = re.match(r"\s*(?:(?:val|var|final)\s+)?(?:[\w<>\[\],.?]+\s+)?"
                                      r"(?P<name>[A-Za-z_]\w*)\s*(?::[^=]+)?=(?!=)", lines[opening])
                    if kind:
                        sinks.append(_sink(kind, relative, opening, lines, via, current))
                    elif holder and holder.group("name") not in tracked:
                        tracked.add(holder.group("name"))
                        queue.append((holder.group("name"), _scope_of(
                            lines, holder.group("name"), opening,
                            _declares(lines[opening], holder.group("name")))))
                    continue
                target = assigned.group("name").rsplit(".", 1)[-1]
                if target not in tracked:
                    tracked.add(target)
                    queue.append((target, _scope_of(lines, target, index, _declares(line, target))))
            elif _writes(line, current):
                continue
            kind = _sink_kind(line)
            if kind:
                sinks.append(_sink(kind, relative, index, lines, via, current))
            escaped = re.escape(current)
            if (re.search(rf"\breturn\b.*(?<![\w.]){escaped}\b", line)
                    or re.match(rf"^\s*[A-Z][\w.]*\s*\(.*(?<![\w.]){escaped}\b.*\)\s*$", line)
                    or line.strip() == current):
                returned = True
    return sinks, returned


def _call_arguments(lines: List[str], index: int, start: int) -> List[str]:
    """Identifiers passed at the call whose parenthesis opens at (index, start)."""
    text = "\n".join([lines[index][start:], *lines[index + 1:index + 12]])
    depth, arguments, current = 0, [], ""
    for char in text:
        if char == "(":
            depth += 1
            if depth == 1:
                continue
        elif char == ")":
            depth -= 1
            if depth == 0:
                arguments.append(current)
                break
        if depth == 1 and char == ",":
            arguments.append(current)
            current = ""
        elif depth >= 1:
            current += char
    names = []
    for argument in arguments:
        value = argument.split("=", 1)[-1] if re.match(r"\s*\w+\s*=[^=]", argument) else argument
        match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*", value)
        if match:
            names.append(match.group(1))
    return names


def _input_labels(relative: str, lines: List[str], arguments: Iterable[str],
                  scope: Tuple[int, int]) -> List[Dict]:
    """The labels of the input fields whose value is each argument, within `scope`."""
    labels = []
    low, high = scope
    for argument in arguments:
        bound = re.compile(rf"\bvalue\s*=\s*{re.escape(argument)}\b")
        for index in range(low, min(high, len(lines) - 1) + 1):
            if not bound.search(lines[index]):
                continue
            # The field's own label: after its value up to the next field,
            # else just before it.
            ahead = [index]
            for offset in range(index + 1, min(len(lines), index + 16)):
                if re.search(r"\bvalue\s*=", lines[offset]):
                    break
                ahead.append(offset)
            behind = []
            for offset in range(index - 1, max(-1, index - 7), -1):
                if re.search(r"\bvalue\s*=", lines[offset]):
                    break
                behind.append(offset)
            for near in [*ahead, *behind]:
                match = _LABEL.search(lines[near])
                if match:
                    labels.append({"argument": argument,
                                   "label": match.group("compose") or match.group("plain")
                                   or match.group("java"),
                                   "source_file_path": relative, "line": index + 1})
                    break
            break
    return labels


def value_exports(repo_root: Path, target_path: str, class_name: str,
                  methods: Iterable[str]) -> Dict:
    """{"status", "sinks", "leaves_the_app", "input_labels", "meaning"}."""
    root = Path(str(repo_root or ""))
    methods = [str(method) for method in methods if str(method or "").strip()
               and not str(method).startswith("<")]
    if not str(repo_root or "").strip() or not root.is_dir() or not methods:
        return {"status": "not_applicable", "sinks": [], "input_labels": []}
    sources: Dict[str, List[str]] = {}
    for path in _source_files(root):
        if path.suffix not in (".java", ".kt"):
            continue
        relative = path.relative_to(root).as_posix()
        if _is_test(relative):
            continue
        try:
            sources[relative] = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
    owner = str(class_name or "").rsplit(".", 1)[-1].split("$", 1)[0]
    frontier = [(owner, method, str(target_path or ""), []) for method in methods]
    seen: set = set()
    sinks: List[Dict] = []
    labels: List[Dict] = []
    for _ in range(MAX_HOPS):
        following = []
        for declaring_class, method, declaring_path, via in frontier:
            if (declaring_class, method) in seen:
                continue
            seen.add((declaring_class, method))
            call = re.compile(rf"(?:(?P<receiver>[\w)\]?!]+)\s*\.\s*)?(?<![\w$]){re.escape(method)}\s*\(")
            # A file that names the declaring class, imports the method (a
            # Kotlin top-level function's FileKt class is never named) or
            # shares its package directory.
            mentions = re.compile(
                (rf"\b{re.escape(declaring_class)}\b|" if declaring_class else "")
                + rf"^\s*import\s+[\w.]*\.{re.escape(method)}\b")
            package_dir = str(Path(declaring_path).parent.as_posix()) if declaring_path else None
            imported = re.compile(rf"^\s*import\s+[\w.]*\.{re.escape(method)}\b")
            for relative, lines in sources.items():
                local = relative == declaring_path
                if not local and Path(relative).parent.as_posix() != package_dir and not any(
                        mentions.search(line) for line in lines):
                    continue
                imports_method = not local and any(imported.search(line) for line in lines)
                for index, line in enumerate(lines):
                    if _comment(line):
                        continue
                    match = call.search(line)
                    if not match or (not local and not match.group("receiver") and not imports_method):
                        continue
                    prefix = line[:match.start()]
                    if (_DECLARATION_PREFIX.match(prefix) and "=" not in prefix
                            and not _KEYWORD_PREFIX.search(prefix)):
                        continue
                    step = f"{relative}:{index + 1}"
                    scope = _function_scope(lines, index)
                    labels.extend(_input_labels(relative, lines, _call_arguments(
                        lines, index, match.end() - 1), scope))
                    kind = _sink_kind(line)
                    if kind:
                        sinks.append(_sink(kind, relative, index, lines, [*via, step], method))
                    returned = bool(re.search(r"\breturn\b", prefix)) or bool(
                        re.match(r"^\s*(?:(?:private|public|internal|protected|override|suspend)\s+)*"
                                 r"fun\b[^=]*=\s*$", prefix))
                    assigned = re.search(r"(?P<name>[A-Za-z_][\w.]*)\s*(?::\s*[^=]+?)?\s*=\s*$", prefix)
                    if assigned and not re.search(r"[=!<>]$", prefix[:assigned.end("name")].rstrip()):
                        holder = assigned.group("name").rsplit(".", 1)[-1]
                        found, holder_returned = _follow(
                            relative, lines, holder,
                            _scope_of(lines, holder, index, _declares(prefix, holder)),
                            [*via, step])
                        sinks.extend(found)
                        returned = returned or holder_returned
                    if returned:
                        enclosing_class, enclosing = _enclosing(lines, index)
                        if enclosing and enclosing != method:
                            following.append((enclosing_class, enclosing, relative, [*via, step]))
        frontier = following
        if not frontier:
            break
    unique: Dict[Tuple, Dict] = {}
    for sink in sinks:
        unique.setdefault((sink["source_file_path"], sink["line"]), sink)
    ordered = sorted(unique.values(), key=lambda item: (not item["external"], item["source_file_path"],
                                                        item["line"]))[:MAX_SINKS]
    seen_labels: Dict[Tuple, Dict] = {}
    for label in labels:
        seen_labels.setdefault((label["argument"], label["label"]), label)
    return {
        "status": "collected" if ordered else "no_export_sink_found",
        "sinks": ordered,
        "leaves_the_app": any(item["external"] for item in ordered),
        "input_labels": list(seen_labels.values())[:MAX_LABELS],
        "meaning": (
            "lines where the value the reported method returns reaches an export "
            "sink, through its callers. An external sink (clipboard, share intent, "
            "file, network, content provider) hands the value to other "
            "installations, possibly older versions of this app: a new format for "
            "it is a compatibility fact the pull request must state, and a "
            "regression only if the patch makes the value unreadable where the same "
            "app version read it before. Lexical and bounded: what is listed is "
            "established, what is missing says nothing."),
    }
