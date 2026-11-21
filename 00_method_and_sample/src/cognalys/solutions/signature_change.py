"""Callers a patch leaves behind when it changes or removes a declaration.

A patch that drops a parameter's default value, removes a parameter, adds a
required one, or deletes a constant is correct where it is written and breaks
every place that still relies on the old form. The generated fix for
hfut-schedule's `getUpdate` removed `= Constant.GITEE_TOKEN` from the
signature; its own review notes said callers would need migrating, and the
caller `gitee.getUpdate()` in another file no longer compiled. Gradle reported
that after a sixteen-minute build.

Everything here is pure text and deliberately conservative. It speaks only
about a call it can read completely (balanced arguments, no trailing lambda,
no spread, no generics in the argument list) and only when the changed
function is the single declaration of that name in the repository, so an
overload or an unusual shape stays silent rather than blocking a good patch.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

_SKIP_DIRECTORIES = frozenset({".git", "build", ".gradle", "node_modules"})
_SOURCE_SUFFIXES = frozenset({".kt", ".java"})
_NOT_A_TYPE = frozenset({
    "return", "new", "throw", "else", "assert", "yield", "await", "in", "is",
    "as", "do", "if", "while", "when", "for", "case", "package", "import",
})
_NOT_A_METHOD = frozenset({
    "if", "for", "while", "switch", "catch", "synchronized", "return", "new",
    "when", "try", "super", "this",
})
_MAX_REPORTED = 5

_KOTLIN_FUNCTION = re.compile(
    r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w$.<>?]+\.)?(?P<name>[A-Za-z_$][\w$]*)\s*\("
)
_JAVA_METHOD = re.compile(
    r"^[ \t]*(?:@[\w.]+(?:\([^)]*\))?\s*)*"
    r"(?:(?:public|protected|private|static|final|synchronized|native|abstract"
    r"|default|strictfp)\s+)*(?:<[^>]+>\s+)?"
    r"(?P<type>[\w$.<>\[\],?]+)\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(",
    re.MULTILINE,
)
_CONSTANT_DECLARATION = re.compile(
    r"\b(?:const\s+val|val|var|static\s+final\s+[\w$.<>\[\],?]+"
    r"|final\s+static\s+[\w$.<>\[\],?]+)\s+(?P<name>[A-Z][A-Z0-9_]{2,})\b"
)


@dataclass
class Parameter:
    name: str
    type: str = ""
    has_default: bool = False
    vararg: bool = False


@dataclass
class Declaration:
    name: str
    language: str
    parameters: List[Parameter]
    name_start: int


@dataclass
class Change:
    kind: str
    function: str
    parameter: str = ""
    index: int = 0
    old_arity: int = 0
    new_arity: int = 0
    new_names: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

def blank_comments_and_strings(code: str) -> str:
    """The same text with comments and string/char literals replaced by spaces.

    Length and line structure are kept, so an offset in the result is an
    offset in the source.
    """
    out = list(code)
    length, index = len(code), 0

    def blank(start: int, end: int) -> None:
        for position in range(start, min(end, length)):
            if out[position] not in "\r\n":
                out[position] = " "

    while index < length:
        pair = code[index:index + 2]
        if pair == "//":
            end = code.find("\n", index)
            end = length if end < 0 else end
            blank(index, end)
            index = end
        elif pair == "/*":
            end = code.find("*/", index + 2)
            end = length if end < 0 else end + 2
            blank(index, end)
            index = end
        elif code.startswith('"""', index):
            end = code.find('"""', index + 3)
            end = length if end < 0 else end + 3
            blank(index + 1, end - 1)
            index = end
        elif code[index] in "\"'":
            quote, position = code[index], index + 1
            while position < length and code[position] != quote:
                position += 2 if code[position] == "\\" else 1
                if position < length and code[position] == "\n":
                    break
            blank(index + 1, position)
            index = position + 1
        else:
            index += 1
    return "".join(out)


def _matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_top_level(text: str, generic_types: bool) -> List[str]:
    """Split on commas outside brackets.

    In a Kotlin parameter list `<`/`>` also nest, but only in the type part of
    a parameter (before its default value); inside a default they are
    comparison operators.
    """
    parts: List[str] = []
    depth, start, in_default = 0, 0, False
    for index, character in enumerate(text):
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif generic_types and depth >= 0 and not in_default:
            if character == "<":
                depth += 1
            elif character == ">" and text[index - 1:index] != "-":
                depth -= 1
        if character == "=" and depth == 0 and not in_default:
            before, after = text[index - 1:index], text[index + 1:index + 2]
            if before not in "!<>=" and after != "=":
                in_default = True
        if character == "," and depth == 0:
            parts.append(text[start:index])
            start, in_default = index + 1, False
    tail = text[start:]
    if tail.strip():
        parts.append(tail)
    return parts


def _strip_annotations(text: str) -> str:
    text = text.strip()
    while text.startswith("@"):
        match = re.match(r"@[\w.]+(?::[\w.]+)?", text)
        if not match:
            break
        rest = text[match.end():].lstrip()
        if rest.startswith("("):
            close = _matching_paren(rest, 0)
            if close < 0:
                break
            rest = rest[close + 1:].lstrip()
        text = rest
    return text


def _kotlin_parameter(raw: str) -> Optional[Parameter]:
    text = _strip_annotations(raw)
    vararg = False
    while True:
        match = re.match(r"(vararg|noinline|crossinline|val|var|final)\s+", text)
        if not match:
            break
        vararg = vararg or match.group(1) == "vararg"
        text = text[match.end():]
    match = re.match(r"(?P<name>[A-Za-z_$][\w$]*)\s*:\s*(?P<rest>.*)\Z", text, re.DOTALL)
    if not match:
        return None
    rest = match.group("rest")
    has_default, type_text = False, rest
    depth = 0
    for index, character in enumerate(rest):
        if character in "([{<":
            depth += 1
        elif character in ")]}" or (character == ">" and rest[index - 1:index] != "-"):
            depth -= 1
        elif character == "=" and depth == 0 and rest[index + 1:index + 2] != "=":
            has_default, type_text = True, rest[:index]
            break
    return Parameter(match.group("name"), " ".join(type_text.split()), has_default, vararg)


def _java_parameter(raw: str) -> Optional[Parameter]:
    text = re.sub(r"\bfinal\s+", "", _strip_annotations(raw)).strip()
    match = re.match(r"(?P<type>.+?)\s+(?P<name>[A-Za-z_$][\w$]*)\s*\Z", text, re.DOTALL)
    if not match:
        return None
    return Parameter(match.group("name"), " ".join(match.group("type").split()), False,
                     match.group("type").rstrip().endswith("..."))


# --------------------------------------------------------------------------
# Declarations
# --------------------------------------------------------------------------

def declarations(code: str, language: str) -> List[Declaration]:
    """Function/method declarations in `code` whose parameters could be read."""
    blanked = blank_comments_and_strings(code)
    found: List[Declaration] = []
    if language in {"kotlin", ""}:
        for match in _KOTLIN_FUNCTION.finditer(blanked):
            open_index = match.end() - 1
            close = _matching_paren(blanked, open_index)
            if close < 0:
                continue
            parameters = [
                _kotlin_parameter(part)
                for part in _split_top_level(blanked[open_index + 1:close], True)
            ]
            if any(item is None for item in parameters):
                continue
            found.append(Declaration(match.group("name"), "kotlin", parameters, match.start("name")))
    if language in {"java", ""}:
        for match in _JAVA_METHOD.finditer(blanked):
            name, type_word = match.group("name"), match.group("type")
            if name in _NOT_A_METHOD or type_word in _NOT_A_TYPE:
                continue
            open_index = match.end() - 1
            close = _matching_paren(blanked, open_index)
            if close < 0:
                continue
            tail = re.match(r"\s*(?:throws\s+[\w$.,\s]+)?\s*[{;]", blanked[close + 1:])
            if not tail:
                continue
            parameters = [
                _java_parameter(part)
                for part in _split_top_level(blanked[open_index + 1:close], True)
            ]
            if any(item is None for item in parameters):
                continue
            found.append(Declaration(name, "java", parameters, match.start("name")))
    return found


def language_of(path: str) -> str:
    suffix = Path(str(path)).suffix.lower()
    return {".kt": "kotlin", ".java": "java"}.get(suffix, "")


def signature_changes(original: str, corrected: str, language: str = "") -> List[Change]:
    """How each same-named function's parameters differ between two texts."""
    before, after = declarations(original, language), declarations(corrected, language)
    changes: List[Change] = []
    for name in {item.name for item in before}:
        old = [item for item in before if item.name == name]
        new = [item for item in after if item.name == name]
        if len(old) != 1 or len(new) != 1:
            continue
        old, new = old[0], new[0]
        if old.language == "kotlin":
            old_by_name = {item.name: (index, item) for index, item in enumerate(old.parameters)}
            new_by_name = {item.name: (index, item) for index, item in enumerate(new.parameters)}
            for pname, (index, parameter) in old_by_name.items():
                other = new_by_name.get(pname)
                if other is None:
                    changes.append(Change(
                        "removed_parameter", name, pname, index, len(old.parameters),
                        len(new.parameters), [p.name for p in new.parameters],
                    ))
                elif parameter.has_default and not other[1].has_default:
                    changes.append(Change(
                        "removed_default", name, pname, index, len(old.parameters),
                        len(new.parameters), [p.name for p in new.parameters],
                    ))
            for pname, (index, parameter) in new_by_name.items():
                if pname not in old_by_name and not parameter.has_default and not parameter.vararg:
                    changes.append(Change(
                        "added_required_parameter", name, pname, index, len(old.parameters),
                        len(new.parameters), [p.name for p in new.parameters],
                    ))
        elif len(old.parameters) != len(new.parameters):
            changes.append(Change(
                "arity_changed", name, "", 0, len(old.parameters), len(new.parameters),
            ))
    return changes


def removed_constants(original: str, corrected: str) -> Set[str]:
    """ALL_CAPS constants the original declares and the corrected text does not."""
    before = {m.group("name") for m in _CONSTANT_DECLARATION.finditer(blank_comments_and_strings(original))}
    after = {m.group("name") for m in _CONSTANT_DECLARATION.finditer(blank_comments_and_strings(corrected))}
    return before - after


# --------------------------------------------------------------------------
# Repository scan
# --------------------------------------------------------------------------

def _candidate_files(repo_root: Path, needles: Optional[Iterable[str]]) -> Optional[List[Path]]:
    """Files containing any needle, found by `grep` when it is available.

    Reading every source file of a large checkout over a slow filesystem took
    half a minute; only the files that mention a changed name matter. None
    means "could not narrow", and the caller walks the tree instead.
    """
    words = sorted({str(item) for item in needles or () if str(item)})
    if not words:
        return None
    command = ["grep", "-rlF", "--include=*.kt", "--include=*.java"]
    for directory in _SKIP_DIRECTORIES:
        command.append(f"--exclude-dir={directory}")
    for word in words:
        command.extend(["-e", word])
    command.append(str(repo_root))
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode not in (0, 1):
        return None
    return [Path(line) for line in completed.stdout.splitlines() if line.strip()]


def _texts(repo_root: Path, overlay: Dict[str, str],
           needles: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """Java/Kotlin files, as the patch will leave them.

    With `needles`, only the files that mention one of them (plus the patch's
    own files) are read; the results of the checks are the same, because a
    file that never names a changed symbol can neither call nor reference it.
    """
    texts: Dict[str, str] = {}
    narrowed = _candidate_files(repo_root, needles)
    for path in (narrowed if narrowed is not None else repo_root.rglob("*")):
        if path.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        try:
            relative = path.relative_to(repo_root)
        except ValueError:
            continue
        if any(part in _SKIP_DIRECTORIES for part in relative.parts):
            continue
        key = relative.as_posix()
        if key in overlay:
            texts[key] = overlay[key]
            continue
        try:
            texts[key] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    for key, text in overlay.items():
        texts.setdefault(key, text)
    return texts


@dataclass
class CallSite:
    line: int
    snippet: str
    positional: int
    named: Set[str]
    definite: bool
    receiver: str = ""


def enclosing_type(text: str, offset: int) -> str:
    """Name of the class/interface/object declared last before `offset`."""
    names = re.findall(
        r"\b(?:class|interface|object)\s+([A-Za-z_$][\w$]*)",
        blank_comments_and_strings(text)[:offset],
    )
    return names[-1] if names else ""


def receiver_type(text: str, receiver: str) -> str:
    """The type a simple receiver name was declared with in this file, or "".

    Read from `val x: T`, a parameter `x: T`, `val x = Create(T::class.java)`
    or `val x = T(...)`. Anything else is unresolved, and the caller then
    treats the call as ambiguous instead of guessing.
    """
    if not receiver:
        return ""
    blanked = blank_comments_and_strings(text)
    name = re.escape(receiver)
    typed = re.search(rf"(?<![\w$.]){name}\s*:\s*([A-Za-z_$][\w$.]*)", blanked)
    if typed:
        return typed.group(1).split(".")[-1]
    literal = re.search(
        rf"\b(?:val|var)\s+{name}\s*=[^\n]*?\b([A-Z][\w$]*)::class\b", blanked)
    if literal:
        return literal.group(1)
    constructed = re.search(
        rf"\b(?:val|var)\s+{name}\s*=\s*(?:[\w$]+\.)*([A-Z][\w$]*)\s*\(", blanked)
    return constructed.group(1) if constructed else ""


def call_sites(text: str, name: str, language: str) -> List[CallSite]:
    blanked = blank_comments_and_strings(text)
    declared_at = {item.name_start for item in declarations(text, language) if item.name == name}
    sites: List[CallSite] = []
    for match in re.finditer(rf"(?<![\w$]){re.escape(name)}\s*\(", blanked):
        if match.start() in declared_at:
            continue
        before = blanked[:match.start()].rstrip()
        if before.endswith("::") or re.search(r"\bfun\s+(?:[\w$.<>?]+\.)?\Z", before + " "):
            continue
        open_index = match.end() - 1
        close = _matching_paren(blanked, open_index)
        line = blanked.count("\n", 0, match.start()) + 1
        snippet = text.splitlines()[line - 1].strip() if text else ""
        dotted = re.search(r"([A-Za-z_$][\w$]*)\s*\??\.\s*\Z", blanked[:match.start()])
        receiver = dotted.group(1) if dotted else ""
        if close < 0:
            sites.append(CallSite(line, snippet, 0, set(), False, receiver))
            continue
        inner = blanked[open_index + 1:close]
        arguments = _split_top_level(inner, False) if inner.strip() else []
        named = set()
        for argument in arguments:
            named_match = re.match(r"\s*([A-Za-z_]\w*)\s*=(?!=)", argument)
            if named_match:
                named.add(named_match.group(1))
        definite = (
            "<" not in inner
            and not any(argument.lstrip().startswith("*") for argument in arguments)
            and not re.match(r"\s*\{", blanked[close + 1:])
        )
        sites.append(CallSite(line, snippet, len(arguments), named, definite, receiver))
    return sites


def _is_broken(change: Change, site: CallSite) -> bool:
    if not site.definite:
        return False
    if change.kind == "removed_default":
        return change.parameter not in site.named and site.positional <= change.index
    if change.kind == "added_required_parameter":
        return change.parameter not in site.named and site.positional <= change.index
    if change.kind == "removed_parameter":
        return change.parameter in site.named or site.positional > change.new_arity
    if change.kind == "arity_changed":
        return site.positional == change.old_arity and site.positional != change.new_arity
    return False


def _describe(change: Change) -> str:
    where = f"`{change.parameter}`" if change.parameter else "its parameters"
    return {
        "removed_default": f"parameter {where} lost its default value",
        "removed_parameter": f"parameter {where} was removed",
        "added_required_parameter": f"parameter {where} was added without a default",
        "arity_changed": f"it now takes {change.new_arity} parameter(s), not {change.old_arity}",
    }[change.kind]


def _edit_path(edit: dict) -> str:
    return str(edit.get("source_file_path") or edit.get("source_file") or "")


def repository_texts(repo_root: Path, overlay: Dict[str, str],
                     needles: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """The files that matter, as the patch leaves them, read once for both checks."""
    return _texts(repo_root, overlay, needles)


def changed_signatures(edits: Iterable[dict]) -> List[tuple]:
    """(edit, change) for every signature change the edits make."""
    found = []
    for edit in edits:
        if not isinstance(edit, dict) or edit.get("edit_kind") in {"create", "delete_file"}:
            continue
        original, corrected = str(edit.get("original_code", "")), str(edit.get("corrected_code", ""))
        if not original.strip() or not corrected.strip():
            continue
        for change in signature_changes(original, corrected, language_of(_edit_path(edit))):
            found.append((edit, change))
    return found


def changed_symbol_names(edits: Iterable[dict]) -> Set[str]:
    """Names whose callers or readers the edits may break.

    Used to tell a compiler error in code the patch did not write, but caused
    by it, from one that was already in the repository.
    """
    edits = [edit for edit in edits if isinstance(edit, dict)]
    names = {change.function for _edit, change in changed_signatures(edits)}
    for edit in edits:
        if edit.get("edit_kind") in {"create", "delete_file"}:
            continue
        names |= removed_constants(str(edit.get("original_code", "")), str(edit.get("corrected_code", "")))
    return names


def breaking_callers(repo_root: Path, edits: List[dict], overlay: Dict[str, str],
                     texts: Optional[Dict[str, str]] = None) -> List[dict]:
    """Definite callers, left as they were, of a signature the edits changed."""
    changes = changed_signatures(edits)
    if not changes:
        return []
    if texts is None:
        texts = _texts(repo_root, overlay)
    issues: List[dict] = []
    for edit, change in changes:
        language = language_of(_edit_path(edit))
        declaring = 0
        owner = ""
        for key, text in texts.items():
            if change.function in text:
                for item in declarations(text, language_of(key)):
                    if item.name == change.function:
                        declaring += 1
                        if key == _edit_path(edit):
                            owner = enclosing_type(text, item.name_start)
        broken = []
        for key, text in texts.items():
            if change.function not in text:
                continue
            for site in call_sites(text, change.function, language_of(key) or language):
                if not _is_broken(change, site):
                    continue
                resolved = receiver_type(text, site.receiver)
                # A call is ours when its receiver's declared type is the
                # declaring type; with no resolvable receiver it is ours only
                # if nothing else in the repository has that name.
                if resolved and owner:
                    if resolved != owner:
                        continue
                elif declaring != 1:
                    continue
                broken.append(f"{key}:{site.line}: {site.snippet}")
        if broken:
            issues.append({
                "code": "SIGNATURE_CHANGE_BREAKS_CALLERS",
                "source_file_path": _edit_path(edit),
                "details": [
                    f"`{change.function}` changed: {_describe(change)}",
                    "these calls still use the old form and would no longer "
                    "compile: " + "; ".join(broken[:_MAX_REPORTED])
                    + (f" (+{len(broken) - _MAX_REPORTED} more)" if len(broken) > _MAX_REPORTED else ""),
                    "Keep the signature (change only what the parameter's "
                    "default is set to), or update every one of those callers "
                    "in this same patch; do not leave callers that no longer compile.",
                ],
            })
    return issues


def dangling_constant_references(repo_root: Path, edits: List[dict],
                                 overlay: Dict[str, str],
                                 texts: Optional[Dict[str, str]] = None) -> List[dict]:
    """References left to a constant the edits delete."""
    removed: Dict[str, str] = {}
    for edit in edits:
        if not isinstance(edit, dict) or edit.get("edit_kind") in {"create", "delete_file"}:
            continue
        for name in removed_constants(str(edit.get("original_code", "")), str(edit.get("corrected_code", ""))):
            removed[name] = _edit_path(edit)
    if not removed:
        return []
    if texts is None:
        texts = _texts(repo_root, overlay)
    issues: List[dict] = []
    for name, declaring_path in sorted(removed.items()):
        if any(
            name in text and any(
                m.group("name") == name
                for m in _CONSTANT_DECLARATION.finditer(blank_comments_and_strings(text))
            )
            for text in texts.values()
        ):
            continue  # still declared somewhere: the references may resolve to it
        references = []
        for key, text in texts.items():
            if name not in text:
                continue
            blanked = blank_comments_and_strings(text)
            for match in re.finditer(rf"(?<![\w$]){re.escape(name)}(?![\w$])", blanked):
                line = blanked.count("\n", 0, match.start()) + 1
                references.append(f"{key}:{line}: {text.splitlines()[line - 1].strip()}")
        if references:
            issues.append({
                "code": "REMOVED_CONSTANT_STILL_REFERENCED",
                "source_file_path": declaring_path,
                "details": [
                    f"`{name}` is deleted but is still referenced: "
                    + "; ".join(references[:_MAX_REPORTED]),
                    "Remove or replace every reference in this same patch, or "
                    "keep the declaration.",
                ],
            })
    return issues


# --------------------------------------------------------------------------
# Parameters the finding does not concern
# --------------------------------------------------------------------------

def _words(text: str) -> Set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or ""))
    return {word for word in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if len(word) >= 2}


def _related(left: str, right: str) -> bool:
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 3 and longer.startswith(shorter)


def unrelated_parameter_changes(edits: List[dict], finding_text: str) -> List[dict]:
    """Parameters a patch removes or strips of a default that the finding never names.

    hfut-schedule's finding concerned one `access_token` parameter; the patch
    also removed `page` and `per_page`, so the request silently fell back to the
    server's default pagination. Nothing compiles differently and nothing
    reports it. A parameter is related to the finding when any word of its name
    (split on case and underscores) matches a word of the finding's own
    description, the confirmed cause or the strategy -- never of the patch's own
    reasons, which would justify anything.

    Silent when there is no finding text to compare with.
    """
    finding_words = _words(finding_text)
    if not finding_words:
        return []
    grouped: Dict[tuple, List[str]] = {}
    for edit, change in changed_signatures(edits):
        if change.kind not in {"removed_parameter", "removed_default"} or not change.parameter:
            continue
        if any(_related(word, other) for word in _words(change.parameter) for other in finding_words):
            continue
        grouped.setdefault((_edit_path(edit), change.function), []).append(change.parameter)
    issues = []
    for (path, function), parameters in grouped.items():
        names = ", ".join(f"`{name}`" for name in dict.fromkeys(parameters))
        issues.append({
            "code": "UNRELATED_PARAMETER_CHANGED",
            "source_file_path": path,
            "details": [
                f"`{function}`: parameter(s) {names} were removed or lost their default "
                "value, but the reported finding, its confirmed cause and its strategy "
                "do not concern them",
                "Keep every parameter the finding does not name, with its default; "
                "change only what the finding is about.",
            ],
        })
    return issues
