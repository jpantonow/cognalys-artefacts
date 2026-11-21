# scope_utils.py

"""Lightweight lexical-scope heuristics for coordinated Kotlin/Java edits.

These are text-based heuristics, not a parser or compiler front-end. Their only
job is to catch the common way an LLM-authored `declaration` edit breaks: it
introduces a new function/property meant to be reachable unqualified (from a
sibling non-inner nested type, from another file, ...) but anchors the edit at
a location that is actually nested inside a class/object body, so once spliced
the new symbol becomes an instance member instead of a free-standing
declaration. A finding here means "reject and force regeneration or manual
review" -- this module never attempts to relocate code itself.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional


def indentation_of(text: str) -> int:
    """Leading whitespace width of the first non-blank line of `text`.

    Used as a cheap proxy for "is this anchor nested inside some enclosing
    block" when the surrounding file is not available (e.g. at LLM-response
    validation time, before any file is touched).
    """
    for line in text.splitlines():
        if line.strip():
            return len(line) - len(line.lstrip(" \t"))
    return 0


def brace_depth_at(content: str, offset: int) -> int:
    """Return the `{`/`}` nesting depth of `content` at `offset`.

    Characters inside "..."/'...' string literals, //... line comments and
    /*...*/ block comments are skipped so braces appearing in those contexts
    are not counted.
    """
    depth = 0
    i = 0
    n = min(offset, len(content))
    in_string: Optional[str] = None
    in_line_comment = False
    in_block_comment = False
    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch in "\"'":
            in_string = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return depth


def line_scope_states(content: str) -> list[tuple[int, str]]:
    """`(brace depth, open token)` at the start of every line of `content`.

    The open token is "" in code, or the kind of literal/comment a line
    starts inside (a Kotlin raw string, a block comment). Same lexical
    rules as `brace_depth_at`,
    plus Kotlin raw strings and backtick names: a test named
    `` `it doesn't crash` `` would otherwise open a char literal that
    swallows the rest of the file.
    """
    states = [(0, "")]
    depth = 0
    i = 0
    n = len(content)
    token = ""
    while i < n:
        ch = content[i]
        if ch == "\n":
            if token in {"//", "'", '"', "`"}:
                # Line comments end here; a single-line literal left open
                # is a lexing miss, not a reason to misread every later line.
                token = ""
            states.append((depth, token))
            i += 1
            continue
        if token == "//":
            i += 1
            continue
        if token == "/*":
            if content.startswith("*/", i):
                token = ""
                i += 2
                continue
            i += 1
            continue
        if token == '"""':
            if content.startswith('"""', i):
                token = ""
                i += 3
                continue
            i += 1
            continue
        if token:
            if ch == "\\" and token != "`":
                i += 2 if i + 1 < n and content[i + 1] != "\n" else 1
                continue
            if ch == token:
                token = ""
            i += 1
            continue
        if content.startswith("//", i) or content.startswith("/*", i):
            token = content[i:i + 2]
            i += 2
            continue
        if content.startswith('"""', i):
            token = '"""'
            i += 3
            continue
        if ch in "\"'`":
            token = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    states.append((depth, token))
    return states


def scope_change_outside_edit(content: str, original: str, corrected: str) -> str:
    """Describe the first line after an edit whose brace scope it changes.

    Code before the edited span cannot change scope; every line after it
    must stay at the brace depth it had. An edit whose original_code ran
    through an enclosing `}` and whose replacement does not close it the
    same way silently moves the rest of the file into, or out of, a class
    body. Returns "" when nothing after the span moves, or when `original`
    does not occur in `content`.
    """
    offset = content.find(original) if original else -1
    if offset < 0:
        return ""
    end = offset + len(original)
    updated = content[:offset] + corrected + content[end:]
    before = line_scope_states(content)
    after = line_scope_states(updated)
    # The suffix after the span is the same text in both, so the states of
    # the lines it starts (and the end of the text) pair up from the end.
    # The line holding the span's end is skipped: whether the replacement
    # kept its closing line break is not a scope question, and any depth it
    # changes reaches the next line anyway.
    later = content.count("\n", end) + 1
    lines = content.splitlines()
    for index in range(later):
        position = len(before) - later + index
        old_state = before[position]
        new_state = after[len(after) - later + index]
        if old_state != new_state:
            line_number = position + 1
            text = (lines[line_number - 1].strip()
                    if index < later - 1 and line_number - 1 < len(lines)
                    else "end of file")
            return (
                f"line {line_number} ({text[:60]!r}) would move from brace "
                f"depth {old_state[0]} to {new_state[0]}"
                + ("" if old_state[1] == new_state[1]
                   else f" and start inside {new_state[1] or 'code'!r}")
            )
    return ""


_DECL_KEYWORD = re.compile(r"\b(companion\s+object|inner\s+class|class|object|interface)\b")


def enclosing_class_stack(content: str, offset: Optional[int] = None) -> list[dict]:
    """Chain of class/object/interface frames enclosing `offset` in `content`.

    Each frame is `{"name": str, "is_companion": bool, "is_inner": bool}`,
    outermost first. Braces opened by function bodies, lambdas, `if`/`for`/
    `when` blocks, etc. are tracked to keep nesting correct but are not
    returned as frames.
    """
    n = len(content) if offset is None else min(offset, len(content))
    stack: list[Optional[dict]] = []
    i = 0
    in_string: Optional[str] = None
    in_line_comment = False
    in_block_comment = False
    pending: Optional[dict] = None
    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch in "\"'":
            in_string = ch
            i += 1
            continue
        if ch == "{":
            stack.append(pending)
            pending = None
            i += 1
            continue
        if ch == "}":
            if stack:
                stack.pop()
            i += 1
            continue
        match = _DECL_KEYWORD.match(content, i)
        if match:
            text = match.group(0)
            rest = content[match.end():]
            name_match = re.match(r"\s*([A-Za-z_]\w*)", rest)
            pending = {
                "name": name_match.group(1) if name_match else "",
                "is_companion": text.startswith("companion"),
                "is_inner": text.startswith("inner"),
                "is_object": text.startswith(("object", "companion")),
            }
            i = match.end()
            continue
        i += 1
    return [frame for frame in stack if frame is not None]


_CALLABLE_DECL = re.compile(
    r"(?m)^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*"
    r"(?:(?:public|private|protected|internal|open|final|override|"
    r"suspend|inline|abstract|lateinit)\s+)*"
    r"(?:fun|val|var)\s+([A-Za-z_]\w*)"
)


def new_callable_symbols(original_code: str, corrected_code: str) -> list[tuple[str, int]]:
    """`(name, offset)` for fun/val/var symbols declared in `corrected_code`
    that are not already declared in `original_code` (i.e. genuinely new)."""
    existing = {m.group(1) for m in _CALLABLE_DECL.finditer(original_code)}
    found = []
    for match in _CALLABLE_DECL.finditer(corrected_code):
        name = match.group(1)
        if name not in existing:
            found.append((name, match.start()))
    return found


def _unqualified_occurrences(code: str, symbol: str) -> list[int]:
    pattern = re.compile(
        r"(?<![.\w])(?<!fun )(?<!val )(?<!var )" + re.escape(symbol) + r"\s*\("
    )
    return [match.start() for match in pattern.finditer(code)]


def unqualified_call_sites(
    edits: Iterable[dict], symbol: str, declaring_file: str = ""
) -> list[str]:
    """`source_file_path`s (other than `declaring_file`) whose corrected_code
    calls `symbol` unqualified, ignoring files that import it explicitly."""
    import_pattern = re.compile(
        r"(?m)^\s*import\s+[\w.]*\b" + re.escape(symbol) + r"\b"
    )
    sites = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        source_file = str(edit.get("source_file_path", ""))
        if source_file == declaring_file:
            continue
        code = str(edit.get("corrected_code", ""))
        if import_pattern.search(code):
            continue
        if _unqualified_occurrences(code, symbol):
            sites.append(source_file)
    return sites


_DOC_AND_ANNOTATIONS_AT_END = re.compile(
    r"(?:^[ \t]*/\*\*(?:(?!\*/).)*\*/[ \t]*\n)(?:^[ \t]*@[^\n]*\n)*\Z",
    re.S | re.M,
)


def keep_doc_comment_on_anchor(
    content: str, original: str, corrected: str
) -> tuple[str, str]:
    """Widen a declaration edit so new code goes above the anchor's doc comment.

    A declaration edit often keeps its anchor and prepends new declarations
    (`corrected` = new code + `original`). Spliced as is, the new code lands
    between the anchor and the KDoc/Javadoc (and annotations) right above it,
    which then documents the wrong declaration (InterTune, 2026-10-27:
    `shouldFetchArt`'s KDoc ended up above `resolveArtworkFile`). When that is
    the shape, the doc block joins both sides of the edit. Anything else is
    returned unchanged.
    """
    anchor = original.strip()
    if not anchor or corrected == original:
        return original, corrected
    tail = corrected.rstrip()
    if not tail.endswith(anchor) or not tail[: -len(anchor)].strip():
        return original, corrected
    offset = content.find(original)
    if offset < 0:
        return original, corrected
    line_start = content.rfind("\n", 0, offset) + 1
    if content[line_start:offset].strip():
        return original, corrected
    match = _DOC_AND_ANNOTATIONS_AT_END.search(content[:line_start])
    if not match:
        return original, corrected
    doc = content[match.start():offset]
    if content.count(doc + original) != 1:
        return original, corrected
    prefix = corrected[: corrected.rstrip().rfind(anchor)]
    # The prefix ends with the anchor's own indentation; that indentation
    # belongs to the doc comment's first line now, which `doc` already has.
    prefix_body = prefix.rstrip(" \t")
    lead = original[: len(original) - len(original.lstrip())]
    return doc + original, prefix_body + doc + lead + corrected[len(prefix):]


def find_declaration_scope_mismatches(
    original_code: str,
    corrected_code: str,
    anchor_is_nested: bool,
    other_edits: Optional[Iterable[dict]] = None,
    declaring_file: str = "",
    anchor_owner_is_object: bool = False,
) -> list[str]:
    """Human-readable descriptions of likely scope mismatches, or `[]`.

    Only meaningful when `anchor_is_nested` is true: an anchor already at file
    top-level means anything spliced there is itself top-level, so a new
    function/property is unconditionally reachable unqualified and no mismatch
    is possible.

    `anchor_owner_is_object`: the type the declaration lands in is a Kotlin
    `object` (or companion). Its members need no instance, so a nested type
    calls them unqualified (InterTune's `object WidgetStore`, 2026-10-27).
    Other files still need an import or qualifier.
    """
    if not anchor_is_nested:
        return []
    findings: list[str] = []
    other_edits = list(other_edits) if other_edits is not None else []
    for name, decl_offset in new_callable_symbols(original_code, corrected_code):
        decl_stack = enclosing_class_stack(corrected_code, decl_offset)
        if decl_stack:
            # The symbol is already wrapped in a class/object opened within
            # this same snippet: a companion object genuinely exposes it
            # without a receiver from sibling nested types, and any other
            # wrapper is a container the heuristic does not model. Either
            # way, its own container -- not the anchor's nesting -- scopes
            # it, so skip it.
            continue
        for call_offset in ([] if anchor_owner_is_object
                            else _unqualified_occurrences(corrected_code, name)):
            if call_offset == decl_offset:
                continue
            call_stack = enclosing_class_stack(corrected_code, call_offset)
            if (call_stack and not call_stack[-1]["is_inner"]
                    and not call_stack[-1]["is_companion"]):
                findings.append(
                    f"'{name}' will become an instance member of the anchor's "
                    f"enclosing type once spliced, but is called unqualified "
                    f"from nested type '{call_stack[-1]['name']}', which has "
                    f"no implicit access to the outer instance"
                )
        for site in unqualified_call_sites(other_edits, name, declaring_file):
            findings.append(
                f"'{name}' will become an instance member of the anchor's "
                f"enclosing type once spliced, but is referenced unqualified "
                f"from '{site}' with no import or qualifier"
            )
    seen = set()
    unique = []
    for finding in findings:
        if finding not in seen:
            seen.add(finding)
            unique.append(finding)
    return unique
