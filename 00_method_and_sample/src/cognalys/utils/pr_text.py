"""Plain-language pieces of the text a maintainer reads in a pull request.

The deterministic PR summary used to be a stack of headings (Observed failure,
Root cause, Correction strategy, Coordinated changes, Verification included)
that repeated the same test lines twice and carried the pipeline's internal
evidence ids ("[C1, C2, C3]"). A maintainer wants what was wrong, what changed
and where; everything else is kept in the solution and execution JSON, which
is where the operator reviews it before publishing.
"""
from __future__ import annotations

import re
from typing import Iterable

from cognalys.utils.finding_source import is_analyzer_tool, is_dynamic_analysis_tool

_UNSPECIFIC_MESSAGES = frozenset({
    "unknown", "unknown error", "none", "n/a", "not available",
})

# The pipeline cites its evidence packet as "[C1, C2]" or "[CLM-002, SRC-001]".
# Those ids mean nothing outside a run's artifacts. A leading space is
# required, so an index expression such as `values[I1]` is never touched.
_EVIDENCE_CITATION = re.compile(
    r"[ \t]+\[[A-Z][A-Z0-9]{0,7}-?\d{1,4}(?:\s*[,;]\s*[A-Z][A-Z0-9]{0,7}-?\d{1,4})*\]"
    # The same ids in parentheses ("(CLM-001, CLM-002)"), which the last run's
    # comfychair summary carried into a pull request body. Only the hyphenated
    # form is stripped there, so a parenthesised "(SHA1)" is never touched,
    # and only zero-padded, so "(AES-256)" or "(SHA-512)" is not either.
    r"|[ \t]+\([A-Z]{2,8}-0\d{2,3}(?:\s*[,;]\s*[A-Z]{2,8}-0\d{2,3})*\)"
    # Single-letter ids, "(C-001, C-002)", reached AI Hub's draft. Hyphen and
    # three digits are required, so "(API-21)" or "(SHA1)" is never matched.
    r"|[ \t]+\([A-Z]-\d{3,4}(?:\s*[,;]\s*[A-Z]-\d{3,4})*\)"
    # A list that mixes the two forms, "(C1; SRC-001, WEB-005)" (Tempus's
    # issue, 2026-11-04). Two ids at least, one of them zero-padded like the
    # packet's ("SRC-001"), so "(AES-256, SHA1)" or "(API 21)" is never matched.
    r"|[ \t]+\((?=[^)]*\b[A-Z]{2,8}-0\d{2}\b)[A-Z][A-Z0-9]{0,7}-?\d{1,4}"
    r"(?:\s*[,;]\s*[A-Z][A-Z0-9]{0,7}-?\d{1,4})+\)"
)

# The title the maintainer summary proposes, carried inside the explanation
# from generation to the PR step as an HTML comment (invisible on GitHub).
_TITLE_MARKER = re.compile(r"^\s*<!--\s*pr-title:\s*(?P<title>.*?)\s*-->\s*\n?", re.IGNORECASE)


def with_title_marker(title: str, text: str) -> str:
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    return f"<!-- pr-title: {title} -->\n{text}" if title else text


def split_title_marker(text: str) -> tuple[str, str]:
    """(proposed title or "", text without the marker)."""
    match = _TITLE_MARKER.match(str(text or ""))
    if not match:
        return "", str(text or "")
    return match.group("title").strip(), str(text)[match.end():]


# Words of the pipeline's own vocabulary that mean nothing to a maintainer.
# Each was in a pull request body or title the review of 2026-10-12 rewrote
# (Metadata Remover, AI Hub) or a maintainer closed (HomeApp #114).
PIPELINE_JARGON = (
    "governed", "predicate", "adjudicat", "evidence packet", "claim id",
    "confirmed cause", "fail-closed", "finalized", "deterministic",
    "coordinated", "rule-supported", "required prohibitive", "post-adjudication",
    "source-level regression", "dependency-free",
    # RedReader's issue text (exp_final_20261019): "The available material
    # does not establish the encryption producer".
    "available material",
    # The analyzer's own terms in DNS Toggle's issue (2026-11-04): "the
    # monitor observed no admitted producer; the actual runtime branch is
    # unknown ... the getKey event required by its rule". None of the 25
    # published pull requests or other issue texts uses them.
    "admitted producer", "monitor observed", "required by its rule", "runtime branch",
    "non-security usage",
    # Z-Sync's issue (2026-11-06): "outside the detector's accepted SHA-2
    # algorithm list; MD5 was also observed in two runs". Of 326 texts these
    # appear in 17 issues and no published pull request ("algorithm list"
    # does, SMSecure's, so it stays out).
    "the detector", "detector's", "in two runs", "accepted sha-2",
)
_GENERIC_TITLE = re.compile(
    r"^(?:Fix|Address \w+ finding in|Review requested for)\s+[\w$<>.]+$", re.IGNORECASE)
MAX_SUMMARY_WORDS = 250


_ALLOWLIST = re.compile(
    r"expecting\s+one\s+of\s*(?:\{(?P<braced>[^}]*)\}|(?P<bare>.*?))\s+but\s+found\b",
    re.IGNORECASE | re.DOTALL)


_RULE_WORD = re.compile(r"\b(?:rule|requir\w*|allow\w*|accept\w*|expect\w*|permit\w*)\b",
                        re.IGNORECASE)


def reported_allowlist(message: str) -> list[str]:
    """The values a rule's "expecting one of X but found Y" message accepts.

    [] when the message has no such list or elides part of it ("..."): a
    partial list cannot show that a sentence about it is wrong.
    """
    match = _ALLOWLIST.search(str(message or ""))
    if not match:
        return []
    listed = match.group("braced") if match.group("braced") is not None else match.group("bare")
    values = [value.strip().strip("'\"").strip() for value in str(listed or "").split(",")]
    if any(value and set(value) <= {"."} for value in values):
        return []
    return [value for value in values if value]


_FOUND_VALUE = re.compile(r"\bbut\s+found\s+['\"]?(?P<value>[\w.\-/]+?)['\"]?(?:[\s,;)]|$)",
                          re.IGNORECASE)
_STRING_LITERAL = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"")
_IDENTIFIER = re.compile(r"\b[A-Za-z_$][\w$]*\b")


def reported_found_value(message: str) -> str:
    """The value a rule's "... but found Y" message rejected, or ""."""
    match = _FOUND_VALUE.search(str(message or ""))
    return match.group("value").rstrip(".") if match else ""


def _name_token(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", str(value or "").lower())


_TEST_SOURCE = re.compile(r"(?:^|/)src/\w*[Tt]est\w*/|(?:^|/)tests?/")
_LITERAL_OR_TOKEN = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|[A-Za-z_$][\w$]*|\S")
_IMPORT_LINE = re.compile(r"^\s*(?:import|package)\s[^\n]*$", re.MULTILINE)


def production_edits(edits: Iterable[dict]) -> list[dict]:
    """Edits of existing production code: no test file, no file the patch created.

    A test asserts every protocol name (Fread's test held "TLSv1.2" while
    production now said "TLS"), and a created file holds only names the patch
    chose; neither says what the app's own code kept.
    """
    kept = []
    for item in edits or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("source_file_path") or item.get("source_file") or "")
        if str(item.get("edit_kind", "")) in {"test", "create"} or _TEST_SOURCE.search(path):
            continue
        kept.append(item)
    return kept


def _literal_value(token: str) -> str:
    return token[1:-1] if len(token) >= 2 and token.startswith('"') else ""


def replacement_literal(message: str, edits: Iterable[dict]) -> str:
    """The literal that took the rejected value's place, or "" when that is not one value.

    Read token by token from each production edit: where the reported
    literal ("SSL") was replaced, the literal written in its place ("TLS").
    Not the first allowed value found anywhere in the patch: Fread's test
    spelled "TLSv1.2" while its production code moved "SSL" to "TLS".
    """
    token = _name_token(reported_found_value(message))
    if len(token) < 2:
        return ""
    import difflib

    values: dict[str, str] = {}
    for item in production_edits(edits):
        before = _LITERAL_OR_TOKEN.findall(str(item.get("original_code", "")))
        after = _LITERAL_OR_TOKEN.findall(str(item.get("corrected_code", "")))
        matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
        for tag, start, end, new_start, new_end in matcher.get_opcodes():
            if tag != "replace":
                continue
            removed = {_name_token(_literal_value(text)) for text in before[start:end]
                       if _literal_value(text)}
            if token not in removed:
                continue
            for text in after[new_start:new_end]:
                value = _literal_value(text)
                if value and _name_token(value) != token:
                    values.setdefault(value.casefold(), value)
    return next(iter(values.values())) if len(values) == 1 else ""


def _code_identifiers(code: str) -> list[str]:
    """Identifiers of `code` outside literals, comments, annotations and imports."""
    from cognalys.utils.identifier_words import blank_comments_and_literals

    code = _IMPORT_LINE.sub("", blank_comments_and_literals(code or ""))
    code = re.sub(r"@[\w.]+", " ", code)
    return _IDENTIFIER.findall(code)


def _app_declared(name: str, original: str, declared: set) -> bool:
    """Whether `name` is the app's own: declared in the code, or used bare in it.

    A platform type (`SSLSocket`), or a platform member only ever reached
    through a receiver (`.sslSocketFactory(`), is not the app's name to keep.
    """
    from cognalys.utils.identifier_words import TYPE_IDENTIFIER, blank_comments_and_literals

    if name in declared:
        return True
    if TYPE_IDENTIFIER.fullmatch(name):
        return False
    code = blank_comments_and_literals(original or "")
    return bool(re.search(rf"(?<![\w$.]){re.escape(name)}\b(?!\s*\.\s*class\b)",
                          re.sub(r"\.\s+", ".", code)))


def stale_value_names(message: str, edits: Iterable[dict]) -> tuple[list[str], str]:
    """Names the patch keeps that still name the value it replaced.

    whoBIRD's patch (2026-10-18) moved its digests from MD5 to SHA-256 and
    kept `model16MD5` and `calcModelMD5`, which renaming rules leave alone.
    A maintainer reading the diff sees SHA-256 values under MD5 names; the
    pull request says why. Returns (names, replacement value or "") and
    ([], "") unless the rejected literal left the edited code.

    Only names of the app's existing production code count, matched as a
    whole word: Fread's body (tls_20261015) listed `SuppressLint` ("suppreSSLint"),
    the platform's `SSLSocket` and a class the patch had just created. A
    word that a type the code uses also carries (buildSSLFactory returns an
    SSLSocketFactory) still names that type, as semantic_validation judges it.
    """
    from cognalys.utils.identifier_words import declared_names, names_token_as_word, type_words

    found = reported_found_value(message)
    token = _name_token(found)
    if len(token) < 2:
        return [], ""
    edits = production_edits(edits)
    original = "\n".join(str(item.get("original_code", "")) for item in edits)
    corrected = "\n".join(str(item.get("corrected_code", "")) for item in edits)
    literals = lambda code: {_name_token(text.strip('"'))
                             for text in _STRING_LITERAL.findall(code)}
    if token not in literals(original) or token in literals(corrected):
        return [], ""
    if token in type_words(corrected):
        return [], ""
    replacement = replacement_literal(message, edits)
    declared = declared_names(original)
    before = set(_code_identifiers(original))
    after = _code_identifiers(corrected)
    names = []
    for name in after:
        if (name in names or name not in before or _name_token(name) == token
                or not names_token_as_word(name, token)
                or not _app_declared(name, original, declared)):
            continue
        names.append(name)
    return names, replacement


def stale_value_names_note(message: str, edits: Iterable[dict]) -> str:
    names, replacement = stale_value_names(message, edits)
    if not names:
        return ""
    found = reported_found_value(message)
    listed = ", ".join(f"`{name}`" for name in names)
    holds = (f"they now hold {replacement} values" if replacement
             else f"they no longer hold {found} values")
    return (f"The names {listed} still say {found} but {holds}; I kept them to keep "
            "the diff small.")


_STALE_NAMES_PARAGRAPH = re.compile(r"The names?\s+(?P<names>(?:`[^`]+`(?:,\s*|\s+and\s+)?)+)"
                                    r"\s+still says?\s")


def stale_names_paragraph_problems(body: str, edits: Iterable[dict]) -> list[str]:
    """Names a "still say X" sentence lists that are not the app's kept names.

    A name the patch created, or a platform type, does not "still say" the
    old value; listing one (Fread's `ProtocolFilteringSSLSocketFactory`,
    `SSLSocket`) is a regression of stale_value_names for the rewrite path
    to fix.
    """
    from cognalys.utils.identifier_words import TYPE_IDENTIFIER, declared_names

    edits = [item for item in edits or [] if isinstance(item, dict)]
    created = declared_names("\n".join(str(item.get("corrected_code", "")) for item in edits))
    production = production_edits(edits)
    original = "\n".join(str(item.get("original_code", "")) for item in production)
    declared = declared_names(original)
    present = set(_code_identifiers(original))
    problems = []
    for match in _STALE_NAMES_PARAGRAPH.finditer(str(body or "")):
        for name in re.findall(r"`([^`]+)`", match.group("names")):
            if name in declared:
                continue
            if name in created or (name not in present and not TYPE_IDENTIFIER.fullmatch(name)):
                problems.append(f"lists `{name}` as a name that still says the old value, "
                                "but the patch created it")
            elif TYPE_IDENTIFIER.fullmatch(name):
                problems.append(f"lists `{name}` as a name that still says the old value, "
                                "but it is a platform type, not a name the app chose")
    return problems


_CONSTANT_DECLARATION = re.compile(
    r"\b(?:const\s+val|val|static\s+final\s+\w+(?:\[\])?|final\s+static\s+\w+(?:\[\])?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?::\s*[\w.]+\s*)?=\s*(?P<value>\"(?:[^\"\\\n]|\\.)*\"|0x[0-9A-Fa-f]+|\d+)")


def _balanced_end(text: str, start: int) -> int:
    """Index just past the bracket group opening at `text[start]`, or -1."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    closing = pairs.get(text[start:start + 1])
    if not closing:
        return -1
    depth, quote, index = 0, "", start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == text[start]:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return -1


def _statement_end(text: str, start: int) -> int:
    """End of the branch body starting at `start`: a block, or one line."""
    position = start
    while position < len(text) and text[position] in " \t":
        position += 1
    if text[position:position + 1] == "{":
        end = _balanced_end(text, position)
        return end if end > 0 else len(text)
    newline = text.find("\n", position)
    return len(text) if newline < 0 else newline


def _enclosing_block_end(text: str, start: int) -> int:
    """Index of the `}` closing the block that holds `start` (or the text's end)."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            if depth == 0:
                return index
            depth -= 1
    return len(text)


def _top_level_else(code: str, start: int, end: int) -> int:
    """Index of an `else` in `code[start:end]` outside brackets and strings, or -1."""
    depth, quote, index = 0, "", start
    while index < end:
        char = code[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0 and code.startswith("else", index) and not (
                code[index - 1:index].isalnum() or code[index - 1:index] == "_"
                or re.match(r"\w", code[index + 4:index + 5])):
            return index
        index += 1
    return -1


def _unmarked_span(code: str, position: int) -> tuple[int, int] | None:
    """Where the code a value without the new marker runs sits, at the test at `position`.

    `require(x.startsWith(V1))` / `check(...)`: nowhere, it is rejected (an
    empty span). `if (x.startsWith(V1)) A else B`: B; with no else, the rest
    of the enclosing block; A itself when the test is negated. A `when` arm:
    its `else ->` arm. None when no such structure is recognised, so no
    compatibility is claimed on a guess.
    """
    for opener in reversed(list(re.finditer(r"\b(require|check|assert|if)\s*\(", code[:position]))):
        paren = code.index("(", opener.start())
        close = _balanced_end(code, paren)
        if close < position:
            continue
        if opener.group(1) != "if":
            return position, position
        negated = code[paren + 1:position].strip().startswith("!")
        then_end = _statement_end(code, close)
        # `return if (x.startsWith(V1)) a(x) else legacy(x)`: the then-part
        # ends at an else on the same line.
        inline_else = _top_level_else(code, close, then_end)
        then_end = then_end if inline_else < 0 else inline_else
        rest = code[then_end:]
        otherwise = re.match(r"\s*else\b", rest)
        if negated:
            return close, then_end
        if otherwise:
            else_start = then_end + otherwise.end()
            return else_start, _statement_end(code, else_start)
        # Without an else, unmarked data runs on after the test, whether the
        # marked branch exits (return/throw) or falls through.
        return then_end, _enclosing_block_end(code, then_end)
    arm = re.match(r"[^\n]*?->", code[position:])
    if arm:
        block_end = _enclosing_block_end(code, position)
        fallback = re.search(r"\belse\s*->", code[position:block_end])
        return (position + fallback.end(), block_end) if fallback else (position, position)
    return None


_NOT_A_FUNCTION = frozenset({
    "if", "when", "for", "while", "catch", "return", "throw", "switch", "synchronized",
    "require", "check", "assert", "try", "else", "new", "super", "this"})


def _function_declarations(code: str, name: str = r"\w+") -> Iterable[tuple[str, int, int, int]]:
    """Each function `code` declares whose name matches `name`: the name,
    where the declaration starts, and where its body starts and ends.

    Kotlin `fun name(...)` with a block or an expression body, or a Java
    method `Type name(...) {`. A call (`name(...)` followed by anything but
    a body) is not a declaration.
    """
    pattern = (rf"\bfun\s+(?:<[^>\n]*>\s*)?(?:[\w.]+\.)?(?P<kt>{name})\s*\("
               rf"|\b(?!(?:return|new|throw|else)\b)[\w<>\[\]?,.]+\s+(?P<java>{name})\s*\(")
    for match in re.finditer(pattern, code):
        declared = match.group("kt") or match.group("java")
        if declared in _NOT_A_FUNCTION:
            continue
        paren = code.index("(", match.end() - 1)
        close = _balanced_end(code, paren)
        if close < 0:
            continue
        tail = code[close:]
        if match.group("kt"):
            body = re.match(r"\s*(?::\s*[^={\n]+?)?\s*(?P<kind>[{=])", tail)
        else:
            body = re.match(r"\s*(?:throws\s+[\w.,\s]+?)?\s*(?P<kind>\{)", tail)
        if not body:
            continue
        start = close + body.start("kind")
        if body.group("kind") == "{":
            end = _balanced_end(code, start)
        else:
            # An expression body: a block it opens on its first line, or
            # that line (the next one when `=` ends its line).
            first = re.compile(r"\S").search(code, start + 1)
            first = first.start() if first else len(code)
            line_end = code.find("\n", first)
            line_end = len(code) if line_end < 0 else line_end
            brace = code.find("{", first, line_end)
            end = _balanced_end(code, brace) if brace >= 0 else line_end
        if end > start:
            yield declared, match.start(), start + 1, end


def _declared_function_body(code: str, name: str) -> tuple[int, str] | None:
    """Where `name` is first declared in `code` and its body, or None."""
    for _, declared_at, body_start, body_end in _function_declarations(code, re.escape(name)):
        return declared_at, code[body_start:body_end]
    return None


def _enclosing_function(functions: list[tuple[str, int, int, int]], position: int) -> str | None:
    """Name of the innermost declared function whose body holds `position`."""
    holding = [item for item in functions if item[2] <= position < item[3]]
    return max(holding, key=lambda item: item[2])[0] if holding else None


def _delegated_bodies(code: str, unmarked: tuple[int, int],
                      every_unmarked: list[tuple[int, int]]) -> list[str]:
    """Bodies of the functions the unmarked path calls and nothing else does.

    One level deep, into functions the corrected code declares
    (`else decodeLegacyEnvelope(...)`, Salty v3, 2026-10-29). A helper the
    marked path also calls (a shared `decrypt`) is no reader of the old
    layout, as a kept shared line is not.
    """
    def unmarked_at(index: int) -> bool:
        return any(start <= index < end for start, end in every_unmarked)

    bodies = []
    called = {match.group(1) for match in re.finditer(
        r"(?<![\w.])([A-Za-z_]\w*)\s*\(", code[unmarked[0]:unmarked[1]])}
    for name in sorted(called - _NOT_A_FUNCTION):
        declared = _declared_function_body(code, name)
        if declared is None:
            continue
        calls = [match.start() for match in re.finditer(rf"(?<![\w.]){re.escape(name)}\s*\(", code)
                 if match.start() != code.find(name, declared[0])]
        if calls and all(unmarked_at(index) for index in calls):
            bodies.append(declared[1])
    return bodies


# Calls a marker passed in only tests or consumes input: comparing,
# testing a value's start or end, removing, replacing, searching.
_READ_SIDE_CALL = re.compile(
    r"(?:startsWith|endsWith|removePrefix|removeSuffix|removeSurrounding|substringAfter|"
    r"substringBefore|substringAfterLast|substringBeforeLast|equals|contentEquals|"
    r"contains|indexOf|lastIndexOf|split|regionMatches|matches|equalsIgnoreCase|"
    r"replace|replaceFirst|replaceAll|compareTo)")
# Calls whose arguments only report: a marker in a log line or an exception
# message is not data the app writes.
_LOGGER_CALL = re.compile(r"(?:^|\.)(?:Log|Timber|logger|LOGGER|log|LOG|Logger)\.\w+$")
_REPORTING_CALL = re.compile(
    r"(?:println|print|printf|error|require|check|requireNotNull|checkNotNull|assert\w*|fail|"
    r"TODO|\w*(?:Exception|Error|Throwable))")
# Calls that build the value they are given into data: a builder, a stream,
# a buffer, a map, a format, a join.
_WRITING_CALL = re.compile(
    r"(?:append\w*|insert|put\w*|write\w*|format|concat|plus|join|joinToString|"
    r"StringBuilder|StringBuffer)")
_COLLECTION_BUILDER = re.compile(
    r"(?:listOf|mutableListOf|arrayListOf|arrayOf|setOf|linkedSetOf|sequenceOf|"
    r"Arrays\.asList|List\.of|Stream\.of)")
_BYTE_BUILDER = re.compile(r"(?:byteArrayOf|ubyteArrayOf)")
_CALL_CHAIN = r"(?:\s*\.\s*\w+\s*(?:\([^()\n]*\))?)*"
_NUMERIC_END = re.compile(r"\.\s*(?:length|size|count|lastIndex)\s*(?:\(\s*\))?\s*$")
_COMMENT_OR_STRING = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|//[^\n]*|/\*[\s\S]*?\*/")


def _without_comments(code: str) -> str:
    """`code` with its comments blanked, positions kept."""
    return _COMMENT_OR_STRING.sub(
        lambda match: match.group(0) if match.group(0).startswith('"')
        else re.sub(r"[^\n]", " ", match.group(0)), code)


def _call_spans(code: str) -> list[tuple[int, int, str]]:
    """Each call's parentheses in `code` and its callee (`Log.d`, `listOf`;
    `throw` for an exception thrown), strings skipped."""
    spans, stack, quote, index = [], [], "", 0
    while index < len(code):
        char = code[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "(":
            before = code[max(0, index - 200):index]
            callee = re.search(r"([\w$]+(?:\s*\.\s*[\w$]+)*)\s*$", before)
            name = re.sub(r"\s+", "", callee.group(1)) if callee else ""
            if callee and re.search(r"\bthrow\s+(?:new\s+)?$", before[:callee.start()]):
                name = "throw"
            stack.append((index, name))
        elif char == ")" and stack:
            start, name = stack.pop()
            spans.append((start, index, name))
        index += 1
    return spans


def _reference_writes(code: str, start: int, end: int,
                      calls: list[tuple[int, int, str]]) -> bool:
    """Whether the marker reference at `code[start:end]` (with its call
    chain) goes into a value the code builds."""
    before, after = code[max(0, start - 200):start], code[end:end + 200]
    line = code[code.rfind("\n", 0, start) + 1:start]
    # Compared, measured, or a `when` arm.
    if _NUMERIC_END.search(code[start:end]) \
            or re.search(r"(?:==|!=|<=|>=|<|(?<!-)>)\s*$", before) \
            or re.match(r"\s*(?:==|!=)", after) \
            or (not line.strip() and re.match(r"\s*(?:,[^\n]*)?->", after)):
        return False
    # A thrown or logged message, or a lazy `require(...) { "...$MARKER" }`.
    enclosing = sorted((item for item in calls if item[0] < start < item[1]),
                       key=lambda item: -item[0])
    if re.search(r"\bthrow\b|\b(?:require|check)\w*\s*\(.*\)\s*\{", line) or any(
            name == "throw" or _LOGGER_CALL.search(name)
            or _REPORTING_CALL.fullmatch(name.rsplit(".", 1)[-1]) for _, _, name in enclosing):
        return False
    innermost = enclosing[0][2].rsplit(".", 1)[-1] if enclosing else ""
    if innermost and _READ_SIDE_CALL.fullmatch(innermost):
        return False
    if re.match(r"\s*\+(?![+=])", after) or re.search(r"(?<![+])\+=?\s*$", before) \
            or re.search(r"\$\{?\s*$", before) or re.search(r"\]\s*=\s*$", before):
        return True
    if innermost and _WRITING_CALL.fullmatch(innermost):
        return True
    if _BYTE_BUILDER.fullmatch(innermost):
        return True
    if enclosing and _COLLECTION_BUILDER.fullmatch(enclosing[0][2]):
        # listOf(MARKER.dropLast(1), ...).joinToString(":") (Salty v3), or
        # a collection handed to a join.
        outer = enclosing[1][2].rsplit(".", 1)[-1] if len(enclosing) > 1 else ""
        return bool(re.match(r"\s*\.\s*join\w*\b", code[enclosing[0][1] + 1:])
                    or (outer and _WRITING_CALL.fullmatch(outer)))
    return False


def _marker_written(code: str, marker: str) -> bool:
    """Whether production code builds `marker` into a value it produces.

    Concatenation (`MARKER + body`), a string template, a byte or a slot of
    an array, an argument of a builder, stream, buffer, format or join call,
    or an element of a collection that is joined
    (`listOf(MARKER.removeSuffix(":"), ...).joinToString(":")`, Salty v3).
    Testing input for it (`startsWith`, `==`, `when`), measuring it,
    stripping it, or naming it in a thrown or logged message is no write; a
    marker the code only tests on input, or hands to a helper of its own, is
    no new written format.
    """
    calls = _call_spans(code)
    return any(_reference_writes(code, match.start(), match.end(), calls)
               for match in re.finditer(rf"{marker}{_CALL_CHAIN}", code))


# Lines any rewrite copies around new code: error handling is no reader.
_ERROR_HANDLING = re.compile(r"(?:\}|catch\b|finally\b|throw\b|try\b)")
_ASSIGNED_VALUE = re.compile(
    r"(?:(?:val|var|final|const)\s+)*(?:[\w<>\[\]?,.]+\s+)?\w+\s*(?::\s*[\w<>?,.\[\] ]+?)?"
    r"\s*=(?!=)\s*(?P<value>.+?);?$")


def _kept_statement_keys(line: str) -> set[str]:
    """How a statement is recognised as kept: itself, and the value it
    assigns (`SecretKeyFactory.getInstance(...)` under a renamed local)."""
    line = line.strip()
    if len(line) <= 20 or "(" not in line or _ERROR_HANDLING.match(line):
        return set()
    keys = {line.rstrip(";").rstrip()}
    assigned = _ASSIGNED_VALUE.match(line)
    if assigned and len(assigned.group("value")) > 20 and "(" in assigned.group("value"):
        keys.add(assigned.group("value").rstrip())
    return keys


def _written_formats(edits: Iterable[dict]) -> list[bool]:
    """For each new tagged format the patch writes and branches on, whether the
    branch for unmarked data still runs the original code (a legacy reader)."""
    edits = [item for item in edits or [] if isinstance(item, dict)
             and str(item.get("edit_kind", "")) != "test"
             and not _TEST_SOURCE.search(
                 str(item.get("source_file_path") or item.get("source_file") or ""))]
    original = "\n".join(str(item.get("original_code", "")) for item in edits)
    corrected = "\n".join(str(item.get("corrected_code", "")) for item in edits)
    if not corrected.strip():
        return []
    scanned = _without_comments(corrected)
    markers = []
    for match in _CONSTANT_DECLARATION.finditer(corrected):
        if not re.search(rf"\b{re.escape(match.group('name'))}\b", original):
            markers.append(rf"\b{re.escape(match.group('name'))}\b")
    for literal in sorted(set(_STRING_LITERAL.findall(corrected))
                          - set(_STRING_LITERAL.findall(original))):
        if 0 < len(literal) - 2 <= 12:
            markers.append(re.escape(literal))
    original_statements = {key for line in original.splitlines()
                           for key in _kept_statement_keys(line)}
    functions = list(_function_declarations(corrected))
    original_functions = {item[0] for item in _function_declarations(original)}
    formats = []
    # A constant two edits both declare is one format.
    for marker in dict.fromkeys(markers):
        branch_test = (
            rf"\.startsWith\s*\(\s*{marker}|\.removePrefix\s*\(\s*{marker}"
            rf"|(?:\[0\]|\.get\(0\)|\.first\(\))\s*==\s*{marker}"
            rf"|{marker}\s*==\s*\w+(?:\[0\]|\.get\(0\)|\.first\(\))")
        branches = list(re.finditer(branch_test, corrected))
        if not branches or not _marker_written(scanned, marker):
            continue
        # The old layout still has a reader only when the branch unmarked
        # data takes keeps original statements word for word, itself or in
        # a function only it calls. A kept line elsewhere (the shared
        # `Cipher.getInstance(...)`) is no reader. The branch judged is the
        # one in a function the original code declared (Salty v3's decode,
        # not the isValidEncodedMessage it added), any of them when several.
        spans = [span for span in (_unmarked_span(corrected, match.start())
                                   for match in branches) if span is not None]
        judged = [match for match in branches
                  if _enclosing_function(functions, match.start()) in original_functions] \
            or branches
        reads = False
        for match in judged:
            unmarked = _unmarked_span(corrected, match.start())
            if unmarked is None or unmarked[0] == unmarked[1]:
                continue
            reader = [corrected[unmarked[0]:unmarked[1]],
                      *_delegated_bodies(corrected, unmarked, spans)]
            reads = reads or any(_kept_statement_keys(line) & original_statements
                                 for text in reader for line in text.splitlines())
        formats.append(reads)
    return formats


def new_written_format(edits: Iterable[dict]) -> bool:
    """Whether the patch writes a new tagged format earlier versions cannot read.

    Salty's v1 messages (2026-10-29): the write path prepends a constant the
    original code lacked ("v1:") and the read path branches on it
    (`startsWith`, or a leading version byte). Data written by the patched
    version is then unreadable by earlier versions, which the pull request
    must say. The write is any value the code builds from the marker
    (Salty v3 joined it into a `listOf(...)`; _marker_written); a marker
    passed to an API, filtered on, or named in a log or exception message
    writes nothing (a "TLSv1.3" both requested and matched). Whether data
    from earlier versions still reads is the separate fact
    earlier_data_still_reads. Deterministic over the production edits'
    code: a patch that only replaces a value ("SHA-1" -> "SHA-256") writes no
    new format.
    """
    return bool(_written_formats(edits))


def earlier_data_still_reads(edits: Iterable[dict]) -> bool:
    """Whether, beside a new written format, the old layout still has a reader.

    True only when the branch unmarked data takes (the else of the
    `startsWith`/version-byte test, or the code after an early return for
    the new format) still holds the original statements, itself or in a
    function the corrected code declares that only that branch calls; the
    error handling a rewrite copies (`} catch (...)`, `throw ...`) is no
    reader, and a kept value assigned to a renamed local still is. A
    `require(...)` on the marker, or a branch that throws, leaves no reader,
    and the text must not say data from earlier versions still reads.
    """
    formats = _written_formats(edits)
    return any(formats)


# One sentence saying that earlier versions cannot read the new data.
_EARLIER_VERSIONS = re.compile(r"(?i)\b(?:earlier|older|previous|prior)\s+(?:app\s+)?(?:versions?|releases?)\b")
_UNREADABLE = re.compile(r"(?i)\b(?:not|cannot|can't|can not|unable|no longer|won't)\b[^.;]{0,60}?"
                         r"\b(?:read|decrypt|decod|open|understand)")


def states_earlier_versions_cannot_read(text: str) -> bool:
    return any(_EARLIER_VERSIONS.search(sentence) and _UNREADABLE.search(sentence)
               for sentence in re.split(r"(?<=[.!?])\s+", str(text or "")))


_STILL_READS = re.compile(r"(?i)\bstill\s+(?:be\s+)?(?:reads?|decod\w*|decrypt\w*|opens?|works?|"
                          r"readable|loads?|imports?)\b")


def claims_earlier_data_still_reads(text: str) -> bool:
    """Whether a sentence says data from earlier versions still reads."""
    for sentence in re.split(r"(?<=[.!?;])\s+", str(text or "")):
        if _EARLIER_VERSIONS.search(sentence) and _STILL_READS.search(sentence):
            return True
    return False


def allowlist_misstatements(text: str, message: str) -> list[str]:
    """Sentences that say the rule accepts "only" part of what it accepts.

    zSMTH's summary (2026-10-17) said the rule allows "only TLSv1.2 or
    TLSv1.3"; RVSec's message accepted TLSV1.2, TLSV1.3 and TLS -- and the
    patch used "TLS". A sentence naming some allowed values after "only",
    but not all, misstates the rule.
    """
    allowed = reported_allowlist(message)
    if len(allowed) < 2:
        return []
    problems = []
    for sentence in re.split(r"(?<=[.!?])\s+", str(text or "")):
        # A claim about the rule: "only" and a word for what the rule does.
        # "OkHttp negotiates only TLS 1.2 and 1.3" is about a client, and
        # "TLS 1.2" names a version, not the rule's "TLS" value.
        if not (re.search(r"\bonly\b", sentence, re.IGNORECASE)
                and _RULE_WORD.search(sentence)):
            continue
        named = {value.casefold() for value in allowed if re.search(
            rf"(?<![\w.]){re.escape(value)}(?![\w]|\.\d|\s+\d)", sentence, re.IGNORECASE)}
        if named and len(named) < len({value.casefold() for value in allowed}):
            problems.append(
                f"says the rule accepts only some of its values; it accepts {', '.join(allowed)}")
    return problems


def _simple_method(name) -> str:
    head = str(name or "").split("(", 1)[0]
    parts = re.split(r"[.#:\s]", head.strip())
    return parts[-1] if parts else ""


def joint_finding_without_effect(item: dict, entries) -> dict | None:
    """The behaviour review's entry for this joint finding in
    joint_findings_without_effect (coordinated_generation), or None."""
    method = _simple_method((item or {}).get("method") or (item or {}).get("method_name"))
    specification = str((item or {}).get("specification") or (item or {}).get("spec") or "")
    for entry in entries or []:
        if (isinstance(entry, dict) and method
                and _simple_method(entry.get("method")) == method
                and str(entry.get("specification", "")) == specification):
            return entry
    return None


_RESOLUTION_VERB = re.compile(r"(?i)\b(?:resolv|fix|address|satisf|clear)\w*")
_ANOTHER = re.compile(r"(?i)\b(?:also|same|related|too|as\s+well|another|other)\b")
_OTHER_FINDING = re.compile(
    r"(?i)\b(?:also|same\s+change|as\s+well|related|too)\b[^.;]*?"
    r"\b(?:resolv|fix|address|satisf|clear)\w*[^.;]*?"
    r"\b(?:issue|violation|finding|rule|problem|report|misuse|warning)s?\b"
    r"|\b(?:resolv|fix|address|satisf|clear)\w*[^.;]*?\b(?:related|same|other|another)\s+"
    r"(?:\w+\s+){0,3}(?:issue|violation|finding|problem|report|misuse|warning)s?\b"
    # "resolves both violations reported in KeyManager" (review of 2026-10-31).
    r"|\b(?:resolv|fix|address|satisf|clear)\w*[^.;]*?\b(?:both|all|two|three|every)\s+"
    r"(?:\w+\s+){0,3}(?:issues|violations|findings|problems|reports|misuses|warnings)\b")
# A named method claimed with a finding noun and no "also" (review of
# 2026-10-31: "In `generateNewKeyPair`, preparing the spec first resolves the
# KeyPairGenerator rule violation reported there").
_FINDING_NOUN = re.compile(r"(?i)\b(?:violation|finding|misuse|warning)s?\b")


def merged_joint_without_effect(*lists) -> list[dict]:
    """The joint findings without effect several reviews of one patch found, each once.

    Review of 2026-10-31: the review of a repaired patch kept its verdict
    only under repair_behavior_review, so the text check and the statistics
    judged the published patch on the draft's older review. A finding either
    review found without effect stays unresolved: a build repair keeps the
    change, it never makes a joint finding's share effective.
    """
    merged: list[dict] = []
    for entries in lists:
        for entry in entries or []:
            if isinstance(entry, dict) and not joint_finding_without_effect(entry, merged):
                merged.append(entry)
    return merged


# The finding fields a pull request is keyed by: its spec, message and
# branch, the summary's report, and what `cognalys pr-outcomes` records.
ATTRIBUTION_IDENTITY = ("csv_class", "method_name", "spec", "error_message", "unique_error_id")


def analysed_identity(record: dict) -> dict:
    """The identity of the finding a draft was analysed and stored under.

    A pull request attributed to a joint finding (attributed_view) names that
    finding, but the solution file, the behaviour review and the statistics
    still key the draft by the finding that was analysed; ``analysed_finding``
    keeps it.
    """
    record = record if isinstance(record, dict) else {}
    analysed = record.get("analysed_finding")
    analysed = analysed if isinstance(analysed, dict) else {}
    return {key: str(analysed.get(key, record.get(key, "")) or "") for key in ATTRIBUTION_IDENTITY}


def attributed_view(record: dict, attribution) -> dict:
    """``record`` (a finding, a solution or an execution-report row) as its pull
    request presents it.

    LocalSend run_v4 (2026-10-31): loadOrCreateKeyStore [KeyPairSpec] carried
    loadOrCreateKeyStore [KeyStoreSpec] as a joint finding, and the patch only
    replaced setKeyEntry with setEntry -- a KeyStoreSpec change the review said
    left the reported KeyPairSpec observation unresolved. The pull request was
    still KeyPairSpec's: its spec and message, its branch, the summary opening
    with the KeyPairSpec report, and the record pr-outcomes would keep after a
    merge. With ``attribution`` (coordinated_generation.pr_attribution) the
    view names the first finding the diff affects, carries the others as its
    joint findings, and lists the analysed finding among those without effect,
    so the summary does not claim it and the text check flags a claim on it.
    ``analysed_finding`` keeps the analysed identity for every lookup by it.
    The record unchanged when there is no attribution, or it is about another
    finding, or the record is already a view.
    """
    attribution = attribution if isinstance(attribution, dict) else {}
    reported = attribution.get("reported_finding") or {}
    targets = [item for item in attribution.get("attributed_to") or []
               if isinstance(item, dict) and item.get("method_name") and item.get("spec")]
    record = record if isinstance(record, dict) else {}
    if not reported or not targets or record.get("analysed_finding"):
        return record
    if (_simple_method(record.get("method_name")), str(record.get("spec", ""))) != (
            _simple_method(reported.get("method_name")), str(reported.get("spec", ""))):
        return record
    first = targets[0]
    analysed = {key: str(record.get(key, "") or reported.get(key, "") or "")
                for key in ATTRIBUTION_IDENTITY}
    entry = {"method": analysed["method_name"], "specification": analysed["spec"],
             "reason": str(reported.get("reason", "")).strip()}

    def as_joint(item: dict) -> dict:
        return {"unique_error_id": item.get("unique_error_id", ""),
                "specification": item.get("spec", ""), "class": item.get("csv_class", ""),
                "method": item.get("method_name", ""),
                "error_message": item.get("error_message", "")}

    joint = [as_joint(analysed), *[as_joint(item) for item in targets[1:]]]
    for item in record.get("joint_findings") or []:
        if isinstance(item, dict) and not joint_finding_without_effect(item, [
                {"method": other.get("method"), "specification": other.get("specification")}
                for other in [*joint, as_joint(first)]]):
            joint.append(item)
    view = {
        **record,
        **{key: str(first.get(key) or record.get(key, "") or "") for key in ATTRIBUTION_IDENTITY},
        "analysed_finding": analysed,
        "joint_findings": joint,
        "joint_findings_without_effect": merged_joint_without_effect(
            [entry], record.get("joint_findings_without_effect")),
        "joint_finding_count": max(int(record.get("joint_finding_count") or 0),
                                   len(record.get("joint_findings") or []), len(targets)),
    }
    evidence = record.get("evidence_analysis")
    if isinstance(evidence, dict) and isinstance(evidence.get("behavior_review"), dict):
        review = evidence["behavior_review"]
        view["evidence_analysis"] = {**evidence, "behavior_review": {
            **review, "joint_findings_without_effect": merged_joint_without_effect(
                [entry], review.get("joint_findings_without_effect"))}}
    return view


# A sentence saying that a named specification's violation is resolved
# (LocalSend run_v4, 2026-10-31: "This same change also resolves the reported
# KeyStoreSpec violation"), unless the clause naming it says the opposite.
_NEGATED = re.compile(
    r"(?i)\b(?:not|no longer|never|remains?|still|unresolved)\b|n't\b"
    r"|\bwithout\s+(?:\w+\s+){0,2}?(?:resolv|fix|address|satisf|clear)\w*")
# Where one clause of a sentence ends and the next begins. Review of batch 13
# (2026-10-31): the negation was looked for in the whole sentence, so "It
# resolves the KeyPairSpec report, and keys written by earlier versions still
# load" or "This addresses KeyPairSpec, which remains compatible" read as not
# claiming it (4 of 6 probe claims missed; none in the 217 real bodies).
_CLAIM_CLAUSE_BREAK = re.compile(
    r"[,;:()–—]|\s-\s|\b(?:and|but|so|because|which|while|whereas|since|"
    r"although|though|as)\b", re.IGNORECASE)


def _negated_where_named(sentence: str, name: str) -> bool:
    """Whether a clause of ``sentence`` that names ``name`` says it is not resolved."""
    pattern = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    return any(pattern.search(clause) and _NEGATED.search(clause)
               for clause in _CLAIM_CLAUSE_BREAK.split(sentence))


def joint_finding_claims(body: str, without_effect, joint_finding_count: int = 0,
                         primary_method: str = "", primary_specification: str = "") -> list[str]:
    """Sentences claiming the change resolves a joint finding whose share has no effect.

    BlueGate run_v8 (2026-10-31): "This also resolves the related issue in
    `generateNewKeyPair` by preparing the EC parameter specification before
    generator initialization" -- the only change there moved
    ECGenParameterSpec("secp256r1") into a local, which the behaviour review
    found without effect. run_v7 said the same without the name ("also
    resolves the related rule issue in the key-generation helper"): such an
    unnamed claim is a problem only when every joint finding of the unit is
    without effect, since it could mean one that has an effect. The text is
    rewritten (pr_generator.repair_text), never held for it alone.

    A joint finding in the reported method itself (``primary_method``: a
    same-method join, KeyPass's decrypt under two rules) is judged only by
    the unnamed claim: naming that method describes the reported change
    (review of 2026-10-31: "This change fixes `decrypt` so it reads the IV
    stored with each record" was taken for a claim on decrypt's Cipher rule).

    A finding named by its specification (``KeyPairSpec``) with a resolution
    verb is a claim too, unless the sentence says it is not resolved; the
    reported specification (``primary_specification``) describes the
    reported change. A pull request attributed to a joint finding lists the
    analysed finding among these (attributed_view, LocalSend run_v4,
    2026-10-31).
    """
    entries = [entry for entry in without_effect or [] if isinstance(entry, dict)]
    if not entries:
        return []
    summary = re.split(r"I found this while running the app under RVSec"
                       r"|I'm a Computer Engineering student", str(body or ""), maxsplit=1)[0]
    sentences = [sentence for sentence in re.split(r"(?<=[.!?])\s+|\n{2,}", summary)
                 if sentence.strip()]
    problems = []
    for entry in entries:
        method = _simple_method(entry.get("method"))
        specification = str(entry.get("specification", "")).strip()
        if (specification and specification != str(primary_specification or "").strip()
                and any(re.search(rf"(?<![\w$]){re.escape(specification)}(?![\w$])", sentence)
                        and _RESOLUTION_VERB.search(sentence)
                        and not _negated_where_named(sentence, specification)
                        for sentence in sentences)):
            problems.append(
                f"says the change resolves the {specification} violation"
                + (f" in `{method}`" if method else "")
                + " but its part of the change has no effect "
                f"({str(entry.get('reason', '')).strip()}); do not claim it")
            continue
        if method and method == _simple_method(primary_method):
            continue
        # Naming the method is not a claim by itself: a rename or a new
        # return type the reported fix needs is described there too.
        if method and any(re.search(rf"(?<![\w$]){re.escape(method)}(?![\w$])", sentence)
                          and _RESOLUTION_VERB.search(sentence)
                          and (_ANOTHER.search(sentence) or _FINDING_NOUN.search(sentence))
                          for sentence in sentences):
            problems.append(
                f"says the change resolves the violation in `{method}` "
                f"({entry.get('specification', '')}), but its part of the change has no "
                f"effect ({str(entry.get('reason', '')).strip()}); do not claim it, and "
                "do not describe an edit as being for it")
    if not problems and len(entries) >= max(int(joint_finding_count or 0), 1) and any(
            _OTHER_FINDING.search(sentence) for sentence in sentences):
        problems.append(
            "says the change also resolves another issue, but the only other violation "
            "in this code (" + ", ".join(
                f"`{_simple_method(entry.get('method'))}`" for entry in entries)
            + ") is not resolved by it; describe only the reported one")
    return problems


def finding_specs(record: dict) -> list[str]:
    """The spec ids of a finding record, its joint findings' and the analysed one's."""
    record = record if isinstance(record, dict) else {}
    specs = [record.get("spec", "")]
    specs += [item.get("specification") or item.get("spec") or ""
              for item in record.get("joint_findings") or [] if isinstance(item, dict)]
    analysed = record.get("analysed_finding")
    if isinstance(analysed, dict):
        specs.append(analysed.get("spec", ""))
    return list(dict.fromkeys(str(spec).strip() for spec in specs if str(spec or "").strip()))


# Words too common to say a sentence came from a note: what is left are the
# note's own terms ("reader", "external contract", "migration strategy").
_OVERLAP_COMMON = frozenset("""that this with from have will been were they them their there which
would could should into than then when what where while about after before because does each other
only also more most such some these those very just make makes made change changes code value
values used uses using keep keeps kept must need needs every across under over between""".split())
# A sentence of at least this many terms, this share of them from one note.
_COPIED_NOTE_MIN_TERMS = 8
_COPIED_NOTE_SHARE = 0.75


def _note_terms(text: str) -> set:
    return {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text or "")
            if word.lower() not in _OVERLAP_COMMON}


def _word_sequence(text: str) -> list[str]:
    return [word.lower() for word in re.findall(r"[A-Za-z0-9]+", text or "")]


def _copies_note_wording(sentence: str, note: str) -> bool:
    """The sentence reproduces one of the note's sentences in its order.

    Words the two share in runs of at least two, in the same order, cover
    _COPIED_NOTE_SHARE of the sentence. A restatement of the same fact in
    another structure shares the fact's nouns but not their order: Weewx
    Weather's "Users can no longer connect to HTTPS endpoints with
    self-signed certificates, untrusted private roots, or mismatched
    hostnames" shares 11 of its 13 uncommon words with the review's "HTTPS
    endpoints using self-signed certificates, private roots not trusted by
    the platform, or mismatched hostnames will no longer connect", but its
    in-order runs cover 3 of its 18 words. Over the 252 drafts under
    outputs/ on 2026-11-04 no effect sentence reached the share.
    """
    from difflib import SequenceMatcher

    words = _word_sequence(sentence)
    if not words:
        return False
    for part in re.split(r"(?<=[.!?;:])\s+", str(note or "")):
        blocks = SequenceMatcher(None, words, _word_sequence(part), autojunk=False
                                 ).get_matching_blocks()
        shared = sum(block.size for block in blocks if block.size >= 2)
        if shared / len(words) >= _COPIED_NOTE_SHARE:
            return True
    return False


def review_text_problems(body: str, review_texts: Iterable[str],
                         effects: Iterable[str] = ()) -> list[str]:
    """Sentences of the summary that reproduce the review's own notes.

    Your Local Weather (2026-11-02): the body said "This change makes the
    choice to confirm every reader or external contract for
    `KEY_PREF_WEATHER_INITIAL_TOKEN` accepts values ... rather than define a
    reader-supported compatibility/migration strategy", the review's
    question to the maintainer copied word for word, and no check saw it.
    `review_texts` are the draft's review assumptions, publication blockers,
    considerations and notes. A sentence is flagged when at least 75% of its
    (at least eight) uncommon words come from one of them; over the 144
    drafts under outputs/ on 2026-11-02 that flagged four (AppManager getDigest,
    Password Store invokeSuspend, Salty encode, Your Local Weather), each a
    sentence copied from a
    review note in its jargon. The text is rewritten.

    `effects` are the user-visible effects the body must state
    (user_visible_effects, missing_effect_problems). The sentence stating
    one restates a recorded fact, so it shares the fact's terms with every
    note recording it: Weewx Weather (2026-11-04) was held because its only
    sentence able to state the effect was "copied". A sentence that states
    one of them (missing_effect_problems would accept it) is flagged only
    when it reproduces a note's wording in order (_copies_note_wording), and
    then with a request to restate the fact, never with one to drop it, so
    the two checks never ask for contradictory text.
    """
    notes = [(text, _note_terms(text)) for text in review_texts or [] if str(text or "").strip()]
    if not notes:
        return []
    effect_subjects = [_effect_subjects(effect) for effect in effects or []]
    copied, copied_effects = [], []
    for sentence in re.split(r"(?<=[.!?])\s+", _summary_part(body)):
        terms = _note_terms(sentence)
        if len(terms) < _COPIED_NOTE_MIN_TERMS:
            continue
        if _states_effect(sentence, effect_subjects):
            if any(_copies_note_wording(sentence, text) for text, _terms in notes):
                copied_effects.append(sentence.strip())
            continue
        if any(len(terms & note) / len(terms) >= _COPIED_NOTE_SHARE for _text, note in notes):
            copied.append(sentence.strip())
    problems = []
    if copied:
        problems.append(
            "sentences copied from the review's notes to the reviewer ("
            + "; ".join(f'"{item[:120]}"' for item in copied)
            + "): say what the change does in your own plain words; a choice left to the "
              "maintainer is one short sentence naming the choice and its alternative, "
              "never the review's instructions")
    if copied_effects:
        problems.append(
            "states the effect users will notice in the review's own wording ("
            + "; ".join(f'"{item[:120]}"' for item in copied_effects)
            + "): keep the effect, but restate it as a fact in one short sentence of your "
              "own, with the people who notice it as the subject and in your own word order")
    return problems


# Why a reported value is a problem, in the words evidence uses for it: a
# standard that prohibits or deprecates it, a known break. A statement that
# only says the value is outside the rule's list has none of them.
_VALUE_REASON = re.compile(
    r"(?i)\b(?:broken|insecure|weak(?:ness|er|ened)?|deprecat\w*|prohibit\w*|forbid\w*|"
    r"must\s+not|obsolete\w*|vulnerab\w*|collisions?|attacks?|disallow\w*|retired|"
    r"unsafe|no\s+longer\s+(?:considered\s+)?(?:secure|safe|recommended)|"
    r"not\s+(?:considered\s+)?(?:secure|safe)|predictable|transition\w*\s+away|"
    r"mov\w+\s+away)\b")
# Naming a standard is not a reason by itself ("RFC 1321 specifies MD5"),
# but a summary that cites one is giving more than the rule.
_STANDARD_CITED = re.compile(r"(?i)\b(?:RFC\s*\d{3,5}|NIST|SP\s*800-\d+)\b")
# The summary saying what the rule does with the value ("not an accepted
# SecretKeySpec algorithm", "RVSec's report that ... is not accepted").
_RULE_PARAPHRASE = re.compile(
    r"(?i)\b(?:not\s+(?:an?\s+)?(?:accepted|allowed|permitted)|(?:does|do|did)\s*n[o']t\s+accept|"
    r"isn't\s+accepted|not\s+one\s+of|outside\s+(?:the|its)\s+(?:accepted|allowed)|"
    r"not\s+in\s+the\s+(?:accepted|allowed)\s+(?:list|values))\b")


def _summary_part(body: str) -> str:
    """The summary, before the fixed text naming RVSec or introducing the author."""
    return re.split(
        r"## How was this tested\?|I found this while running the app under RVSec"
        r"|I'm a Computer Engineering student", str(body or ""), maxsplit=1)[0]


def _statements(evidence_analysis: dict) -> list[str]:
    evidence = evidence_analysis if isinstance(evidence_analysis, dict) else {}
    texts = [str(item) for item in evidence.get("confirmed_facts") or []]
    texts += [str(item) for item in (evidence.get("migration_assessment") or {}).get(
        "evidence") or []]
    texts += [str(item.get("supported_claim", "")) for item in
              evidence.get("external_sources") or [] if isinstance(item, dict)]
    return list(dict.fromkeys(text.strip() for text in texts if text.strip()))


def value_problem_reasons(evidence_analysis: dict, message: str) -> list[str]:
    """Evidence statements giving a reason the reported value is a problem.

    primftpd (2026-11-03): the body said only that ARCFOUR "is not an
    accepted SecretKeySpec algorithm", while the research had found that
    RFC 8758 prohibits Arcfour and RC4 in SSH. A statement counts when it
    names the value the finding's "but found X" rejected and gives a reason
    in _VALUE_REASON's words; none is invented, and a finding whose message
    names no value has none.
    """
    value = _name_token(reported_found_value(message))
    if not value:
        return []
    return [text for text in _statements(evidence_analysis)
            if value in {_name_token(word) for word in re.findall(r"[\w.\-/]+", text)}
            and _VALUE_REASON.search(text)]


def rule_only_justification_problems(body: str, reasons: Iterable[str]) -> list[str]:
    """The summary justifies the change only by the rule not accepting the value.

    Flagged when the evidence gives a reason (value_problem_reasons), the
    summary paraphrases the rule (_RULE_PARAPHRASE) and gives no reason of
    its own. The text is rewritten with the reason.
    """
    reasons = [str(item) for item in reasons or [] if str(item or "").strip()]
    summary = _summary_part(body)
    if (not reasons or not _RULE_PARAPHRASE.search(summary)
            or _VALUE_REASON.search(summary) or _STANDARD_CITED.search(summary)):
        return []
    return ["justifies the change only by saying RVSec's rule does not accept the value; "
            f'say why the value is a problem, as the evidence states it ("{reasons[0][:160]}"), '
            "and mention the rule only in the sentence saying RVSec found the call"]


# An effect a user, peer or client of the app notices: something it can no
# longer do. "Direct callers will now fail" is about the code, not a user.
_EFFECT_VERB = re.compile(
    r"(?i)\b(?:no\s+longer|cannot|can't|can\s+not|unable|will\s+fail|fails?\s+to|"
    r"stops?\s+working|lose|loses|lost|won't|will\s+not|refuse\w*|reject\w*|break\w*)\b")
_UNVERIFIABLE = re.compile(
    r"(?i)\b(?:cannot|can't|can\s+not|unable\s+to|does\s+not|do\s+not)\s+(?:\w+\s+){0,2}?"
    r"(?:validate|establish|verify|confirm|determine|prove|check|show|guarantee)\b")
_EFFECT_SUBJECT = re.compile(
    r"(?i)\b(?:peers?|clients?|servers?|users?|devices?|connections?|connect\w*|"
    r"negotiat\w*|log\s*in|sign\s*in|backups?|(?:earlier|older|previous)\s+versions?|"
    r"interoperab\w*)\b")
# A sentence asking the reviewer to check something ("Verify on supported
# devices that untrusted certificates are rejected") names what to test, and
# a note handed to the maintainer to decide (coordinated_generation.
# MAINTAINER_DECISION_PREFIX) is about the code: neither says what a user
# loses. Input Leaf run_v5 and RedReader run_v4 (2026-11-09) were held for
# not stating one of each as an effect.
_REVIEWER_REQUEST = re.compile(
    r"(?i)^\s*(?:(?:on|in|with|for|before|after|during)\b[^,;:.]{0,80},\s*)?"
    r"(?:please\s+)?(?:verify|review|confirm|check|test|validate|ensure|run|re-?run|"
    r"establish|determine|decide|make\s+sure)\b")
_MAINTAINER_NOTE = re.compile(r"(?i)^\s*for\s+the\s+maintainer\s+to\s+decide\s*:")


def _effect_subjects(text: str) -> set:
    subjects = set()
    for match in _EFFECT_SUBJECT.finditer(str(text or "")):
        word = re.sub(r"\s+", " ", match.group(0).lower())
        for stem in ("connect", "negotiat", "interoperab", "version"):
            if stem in word:
                word = stem
        subjects.add(word.rstrip("s") if word not in {"version"} else word)
    return subjects


def _states_effect(sentence: str, effect_subjects: list) -> bool:
    """The sentence states one of the effects whose subjects are given, as
    missing_effect_problems reads it."""
    if not effect_subjects or not _EFFECT_VERB.search(sentence):
        return False
    subjects = _effect_subjects(sentence)
    return any(subjects & effect for effect in effect_subjects)


def user_visible_effects(texts: Iterable[str]) -> list[str]:
    """The review's notes that record an effect a user or peer will notice.

    primftpd (2026-11-03): the review recorded that "peers requiring only
    ARCFOUR will no longer be able to negotiate a connection", and the body
    did not say so. A note counts when one sentence of it says something can
    no longer be done (_EFFECT_VERB) by a user, peer, client, connection or
    earlier version (_EFFECT_SUBJECT). A request to the reviewer
    (_REVIEWER_REQUEST) and a note for the maintainer to decide
    (_MAINTAINER_NOTE) record none.
    """
    effects = []
    for text in texts or []:
        if _MAINTAINER_NOTE.match(str(text or "")):
            continue
        for sentence in re.split(r"(?<=[.!?;])\s+", str(text or "")):
            # "The build ... cannot establish device socket behavior" is what
            # a check cannot show, not what a user loses (SMSecure, Fread
            # and Trigger drafts, measured 2026-11-03).
            if _UNVERIFIABLE.search(sentence) or _REVIEWER_REQUEST.match(sentence):
                continue
            if _EFFECT_VERB.search(sentence) and _EFFECT_SUBJECT.search(sentence):
                effects.append(sentence.strip())
    return list(dict.fromkeys(effects))


def missing_effect_problems(body: str, effects: Iterable[str]) -> list[str]:
    """Recorded user-visible effects (user_visible_effects) the body does not state.

    The body states one when a sentence of it says something can no longer
    be done (_EFFECT_VERB) about the same subject (peer, client, connection,
    ...). One problem for all of them; the text is rewritten.
    """
    sentences = re.split(r"(?<=[.!?;])\s+", _summary_part(body))
    missing = [effect for effect in effects or []
               if not any(_states_effect(sentence, [_effect_subjects(effect)])
                          for sentence in sentences)]
    if not missing:
        return []
    return ["leaves out an effect users will notice that the review recorded ("
            + "; ".join(f'"{item[:140]}"' for item in missing[:3])
            + "): state it as a fact in one short plain sentence of your own, with the "
              "people who notice it as the subject; do not copy the note's wording"]


def text_problems(title: str, body: str, finding_message: str = "",
                  new_written_format: bool = False,
                  earlier_data_still_reads: bool | None = None,
                  specs: Iterable[str] = (), code: str = "",
                  wording_checks: bool = True) -> list[str]:
    """What keeps a pull request's text from being the short, plain text a
    maintainer should read; [] when there is nothing.

    `new_written_format` is the fact of the same name about the final diff
    (new_written_format above): the text must then say that earlier versions
    cannot read what this one writes. `earlier_data_still_reads` is that
    diff's earlier_data_still_reads: when False, the text must not say data
    from earlier versions still reads (None: not known, not checked).
    `specs` (finding_specs) and `code` (the diff) are for
    rule_vocabulary_problems. `wording_checks` adds it and
    bytecode_name_problems: off for an issue's publication hold, so they
    never hold one (2026-10-31: those checks come with the pull request's
    rewrite); on when issues.issue_text decides whether to rewrite it."""
    problems = []
    title = str(title or "").strip()
    if not title or _GENERIC_TITLE.match(title):
        problems.append(f"generic title: {title!r} names a method, not the change")
    text = f"{title}\n{body or ''}"
    if strip_evidence_citations(text) != text:
        problems.append("evidence ids such as (C-001) in the text")
    lowered = text.lower()
    jargon = sorted({word for word in PIPELINE_JARGON if word in lowered})
    if jargon:
        problems.append("pipeline vocabulary: " + ", ".join(jargon))
    if wording_checks:
        problems.extend(bytecode_name_problems(title, body or ""))
        problems.extend(rule_vocabulary_problems(title, body or "", specs, code))
    # The summary ends where the fixed text begins: the v1 test section, or in
    # the v2 prose body the sentence naming RVSec or the closing introduction.
    summary = re.split(
        r"## How was this tested\?|I found this while running the app under RVSec"
        r"|I'm a Computer Engineering student", str(body or ""), maxsplit=1)[0]
    words = len(re.findall(r"\w+", summary))
    if words > MAX_SUMMARY_WORDS:
        problems.append(f"summary of {words} words (at most {MAX_SUMMARY_WORDS})")
    problems.extend(allowlist_misstatements(summary, finding_message))
    if new_written_format and not states_earlier_versions_cannot_read(body):
        problems.append(
            "the change writes data in a new format that earlier versions of the app "
            "cannot read; say so in one plain sentence (data written by this version "
            "is not readable by earlier versions"
            + ("; data from earlier versions still reads)" if earlier_data_still_reads
               is not False else ")"))
    if (new_written_format and earlier_data_still_reads is False
            and claims_earlier_data_still_reads(body)):
        problems.append(
            "says data from earlier versions still reads, but the change keeps no "
            "reader for the old format")
    return problems

_DROPPED_SECTIONS = ("Verification included", "Test scenarios covered")
_HEADINGS_REPLACED_BY_PROSE = (
    "Observed dynamic-analysis failure", "Reported finding", "Root cause",
    "Correction strategy",
)
_LEGACY_INTRO = re.compile(
    r"The dynamic-analysis run by `(?P<tool>[^`]+)` reported"
    r"(?P<category> \(`[^`]+`\))?"
    r"(?: while exercising `(?P<symbol>[^`]+)`)?:"
)


def has_specific_message(message: str) -> bool:
    text = str(message or "").strip()
    return bool(text) and text.lower() not in _UNSPECIFIC_MESSAGES


# Kotlin value-class mangling: `getKey-Abc1234` is `getKey` in the source.
_MANGLED = re.compile(r"^(?P<name>[\w$]+?)-[\w]{7}$")
_INITIALIZERS = {"<init>": "constructor", "<clinit>": "static initializer"}
# Kotlin suffixes an internal function with its module (Input Leaf's
# discoverySslContext$app, 2026-11-06) and a default-arguments bridge with
# $default; javac's access$000 keeps its digits.
_KOTLIN_MEMBER_SUFFIX = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\$[A-Za-z_][\w]*$")


def kotlin_member_name(member: str) -> str:
    """A Kotlin method's bytecode name without its module or $default suffix."""
    match = _KOTLIN_MEMBER_SUFFIX.match(str(member or ""))
    if not match or match.group("name") in ("lambda", "access"):
        return str(member or "")
    return match.group("name")


def source_names(class_name: str, method_name: str) -> tuple[str, str, str]:
    """(class, member, kind) as the source names them, from bytecode names.

    2026-10-31 audit: 9-10 of 52 drafts named bytecode -- SMSecure #89
    "Affects `ConversationListAdapter.<init>`", NotallyX #1167
    "`IOExtensionsKt.md5Hash`" -- and Crazyflie's and BombusMod's maintainers
    had already asked what "Fix `<init>`" meant. kind is "method",
    "constructor", "static initializer" or "function" (a Kotlin file-level
    function, whose class `XKt` is the file, not a type in the source). The
    package is dropped; a nested class reads Outer.Inner; anonymous classes
    ($1), `Companion` and value-class mangling are dropped.
    """
    simple = str(class_name or "").strip().rsplit(".", 1)[-1]
    parts = [part for part in simple.split("$")
             if part and not part.isdigit() and part != "Companion"
             and not part.startswith("ExternalSynthetic")]
    member = str(method_name or "").strip()
    # javac names a lambda body lambda$<enclosing method>$<n> (whoBIRD's
    # "lambda$downloadModels$4"); a maintainer knows the enclosing method.
    member = re.sub(r"^lambda\$(\w+?)\$\d+$", r"\1", member)
    mangled = _MANGLED.match(member)
    if mangled:
        member = mangled.group("name")
    member = kotlin_member_name(member)
    # Kotlin names a lambda's or coroutine's class after the function holding
    # it (Outer$onCreate$1.invokeSuspend): a lower-case `$` part is that
    # function, not a nested class, and the lambda's own invoke/invokeSuspend
    # is no name in the source (review of batch 14, 2026-10-31: the text read
    # `MainActivity.onCreate.invokeSuspend`).
    enclosing = next((index for index, part in enumerate(parts)
                      if index and part[:1].islower()), None)
    if enclosing is not None:
        member = re.sub(r"^lambda\$(\w+?)\$\d+$", r"\1", parts[enclosing])
        parts = parts[:enclosing]
    if member in _INITIALIZERS:
        return ".".join(parts), "", _INITIALIZERS[member]
    if parts and re.search(r"[a-z0-9]Kt$", parts[0]) and len(parts) == 1 and member:
        return "", member, "function"
    if parts and re.search(r"[a-z0-9]Kt$", parts[0]):
        parts = parts[1:] or [parts[0][:-2]]
    return ".".join(parts), member, "method"


def short_symbol(class_name: str, method_name: str) -> str:
    """`Class.method` with the package dropped; a maintainer knows their package.

    Source names (source_names): a constructor is its class, a static
    initializer its class, a Kotlin file-level function its own name.
    """
    owner, member, _kind = source_names(class_name, method_name)
    return ".".join(value for value in (owner, member) if value)


def symbol_phrase(class_name: str, method_name: str) -> str:
    """The finding's location as a maintainer reads it, in Markdown.

    "`Class.method`", "the `Class` constructor", "the `Class` static
    initializer" or "`function`".
    """
    owner, member, kind = source_names(class_name, method_name)
    if kind in _INITIALIZERS.values():
        return f"the `{owner}` {kind}" if owner else ""
    symbol = ".".join(value for value in (owner, member) if value)
    return f"`{symbol}`" if symbol else ""


# What source_names maps, as it reaches a text: the check below asks for a
# rewrite when the summary model copied one from the finding.
_BYTECODE_NAME = re.compile(
    r"<(?:cl)?init>"
    r"|\b[A-Z][A-Za-z0-9]*[a-z0-9]Kt\b"
    r"|\b[A-Z][A-Za-z0-9_]*\$[A-Za-z0-9_]"
    r"|\blambda\$\w"
    # A mangled name inside code spans only: `getKey-Abc1234`.
    r"|(?<=`)[a-z][A-Za-z0-9]*-(?P<hash>[A-Za-z0-9_]{7})(?=[`(])")


def bytecode_name_problems(title: str, body: str) -> list[str]:
    """Bytecode names in a pull request's title or body: rewritten with source names."""
    # A quoted analyzer message keeps its own words.
    text = "\n".join(line for line in f"{title}\n{body}".splitlines()
                     if not line.lstrip().startswith(">"))
    found = sorted({match.group(0) for match in _BYTECODE_NAME.finditer(text)
                    if not match.group("hash") or re.search(r"[0-9A-Z]", match.group("hash"))})
    if not found:
        return []
    return ["bytecode names a maintainer does not write (" + ", ".join(f"`{name}`" for name in found)
            + "): say \"the `X` constructor\" for <init>, the function's own name for a "
              "Kotlin file-level function (no `Kt` class), and the outer class's method "
              "for a `$` class"]


# The analyzer's vocabulary for its rules. network-survey #154 was closed with
# "It only satisfies the CrySL rule", and 48 of 52 drafts and 8 of 10
# published pull requests of 2026-10-24..31 justified the change with the
# finding's spec id ("violates the `GCMParameterSpecSpec` usage rule",
# "outside the `MessageDigestSpec` allow-list", "Use compliant PBKDF2").
_RULE_VOCABULARY = re.compile(
    r"\busage[- ]rules?\b|\bCrySL\b|\brule-(?:modell?ed|supported|accepted)\b",
    re.IGNORECASE)
# Words with an ordinary sense too ("RFC 6455-compliant", the app's own
# pinning allowlist) count only in a text that also names a rule or a
# specification, and never when the diff itself uses the word
# (review of batch 14, 2026-10-31: such a PR could never be rewritten clean
# and was held).
_CONTEXTUAL_RULE_VOCABULARY = re.compile(
    r"\ballow[- ]?lists?\b|\b(?:non-?)?compliant\b|\bcompliance\b", re.IGNORECASE)
# Not "RVSec": every body names it in the sentence saying how it was found.
_RULE_CONTEXT = re.compile(r"\brules?\b|\bspecifications?\b", re.IGNORECASE)


def _spec_names_a_type(spec: str) -> bool:
    """Whether a spec id is also the simple name of a type it governs.

    PBEKeySpec is a specification and javax.crypto.spec.PBEKeySpec: naming
    the class names the Java API (review of batch 14, 2026-10-31). Read from
    the specification's own applies_to_types; unreadable -> False.
    """
    import json

    from cognalys.workspace import library_data

    try:
        data = json.loads(library_data("specifications", f"{spec}.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(str(item).rsplit(".", 1)[-1] == spec
               for item in data.get("applies_to_types") or [])


def rule_vocabulary_problems(title: str, body: str, specs: Iterable[str] = (),
                             code: str = "") -> list[str]:
    """The analyzer's rule ids and rule vocabulary in a pull request's text.

    ``specs`` are the spec ids of the finding and its joint findings; one the
    edited ``code`` itself names (a spec called after its class) is the API's
    name, not the rule's. A rule-only change keeps being proposed; only its
    words change: the Java API, what RVSec reported about it, what changed.
    """
    # The analyzer's own message, quoted (an issue's "> ... not one
    # KeyPairSpec accepts"), is its words, not ours.
    text = "\n".join(line for line in f"{title}\n{body}".splitlines()
                     if not line.lstrip().startswith(">"))
    problems = []
    named = sorted({spec for spec in (str(item or "").strip() for item in specs)
                    if len(spec) >= 4 and re.search(rf"(?<![\w$]){re.escape(spec)}(?![\w$])", text)
                    and not re.search(rf"(?<![\w$]){re.escape(spec)}(?![\w$])", str(code or ""))
                    and not _spec_names_a_type(spec)})
    if named:
        problems.append(
            "names the analyzer's internal rule id (" + ", ".join(f"`{spec}`" for spec in named)
            + "): name the Java API the reported call is on and the concrete change; RVSec "
              "is named only in the sentence saying how it was found")
    vocabulary = {match.group(0).lower() for match in _RULE_VOCABULARY.finditer(text)}
    squeezed_code = re.sub(r"[^a-z]", "", str(code or "").lower())
    # The text is about the analyzer's rule when it names one anywhere
    # ("Use compliant PBKDF2" over a body citing the spec); a qualified
    # "RFC 6455-compliant" is a standard's sense whatever surrounds it.
    about_rule = bool(vocabulary or named or _RULE_CONTEXT.search(text))
    for match in _CONTEXTUAL_RULE_VOCABULARY.finditer(text if about_rule else ""):
        qualified = match.start() > 0 and text[match.start() - 1] == "-"
        if not qualified and re.sub(r"[^a-z]", "", match.group(0).lower()) not in squeezed_code:
            vocabulary.add(match.group(0).lower())
    vocabulary = sorted(vocabulary)
    if vocabulary:
        problems.append(
            "rule vocabulary (" + ", ".join(f"'{word}'" for word in vocabulary)
            + "): say what RVSec reported about the API call and what the change does, "
              "not that it satisfies or complies with a rule")
    return problems


def finding_lead_in(tool: str, spec: str, symbol: str) -> str:
    """One sentence introducing the reported message, worded by its origin.

    A candidate somebody found by reading the code is never described as a
    dynamic run, and the analyzer is named only when one reported the row.
    """
    tool = str(tool or "").strip()
    category = f" (`{spec}`)" if spec else ""
    if is_dynamic_analysis_tool(tool):
        location = f" while exercising `{symbol}`" if symbol else ""
        return f"The dynamic-analysis run by `{tool}` reported{category}{location}:"
    subject = f" in `{symbol}`" if symbol else ""
    if is_analyzer_tool(tool):
        return f"The static analysis (`{tool}`) reported{category}{subject}:"
    return f"Reported finding{category}{subject}:"


def strip_evidence_citations(text: str) -> str:
    return _EVIDENCE_CITATION.sub("", str(text or ""))


def finding_paragraph(tool: str, spec: str, symbol: str, message: str,
                      cause: str) -> list[str]:
    """The opening of the summary, as lines.

    A message a tool reported is evidence and is quoted. A candidate somebody
    found by reading the code has nothing to quote: its message only restates
    the cause that follows, so it is kept only when there is no cause to say.
    """
    if not has_specific_message(message):
        return []
    if not is_analyzer_tool(tool) and str(cause or "").strip():
        return []
    return [finding_lead_in(tool, spec, symbol), "", f"> {message}", ""]


def changes_list(file_reasons: dict[str, Iterable[str]]) -> list[str]:
    """One line per changed file, saying what changed there."""
    if not file_reasons:
        return []
    lines = ["Changes:", ""]
    for path, reasons in file_reasons.items():
        clean = [str(item).strip().rstrip(" .;") for item in reasons if str(item).strip()]
        detail = "; ".join(clean)
        lines.append(f"- `{path}`" + (f": {detail}." if detail else ""))
    return lines


def _drop_sections(text: str) -> str:
    kept, skipping = [], False
    for line in text.splitlines():
        heading = re.match(r"#{2,4}\s+(.*?)\s*$", line)
        if heading:
            skipping = heading.group(1) in _DROPPED_SECTIONS
            if skipping:
                continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


_RESTATED_CANDIDATE = re.compile(
    r"\A(?:The static analysis|Reported finding)[^\n]*:\n\n(?:>[^\n]*\n)+\n"
)


def _drop_restated_candidate(text: str) -> str:
    """Remove a leading quote of a candidate found by reading the code.

    Only when a cause follows it: the quote restates that cause. The analyzer
    case (`The dynamic-analysis run ...`) is evidence and is never touched.
    """
    match = _RESTATED_CANDIDATE.match(text)
    if match and text[match.end():].strip() and not text.startswith("The static analysis"):
        return text[match.end():]
    return text


def plain_explanation(text: str) -> str:
    """Bring a summary written by an earlier run to the current plain style.

    A rerun of the PR step reads the explanation persisted in the execution
    report, so the older headings and evidence ids have to be removable here as
    well as never produced in the first place.
    """
    result = _drop_sections(str(text or ""))
    # Only text an earlier run built (it has these headings) had its quote
    # decided by older rules. Text built today already decided whether to quote
    # the message, and the quote must survive when there was no cause to give.
    earlier_run_text = bool(re.search(
        r"^#{2,4}\s+(?:Observed dynamic-analysis failure|Reported finding|Root cause)",
        result, flags=re.MULTILINE,
    ))
    for heading in _HEADINGS_REPLACED_BY_PROSE:
        result = re.sub(
            rf"^#{{2,4}}\s+{re.escape(heading)}[ \t]*\n+", "", result,
            flags=re.MULTILINE,
        )
    result = re.sub(
        r"^#{2,4}\s+Coordinated changes[ \t]*$", "Changes:", result,
        flags=re.MULTILINE,
    )

    def reword(match: re.Match) -> str:
        category = (match.group("category") or "").strip()
        spec = category.strip(" ()`")
        return finding_lead_in(
            match.group("tool"), spec, match.group("symbol") or "",
        )

    result = _LEGACY_INTRO.sub(reword, result)
    if earlier_run_text:
        result = _drop_restated_candidate(result)
    result = strip_evidence_citations(result)
    # Reasons were joined as "first.; second" by an earlier run.
    result = result.replace(".; ", "; ")
    result = speak_to_the_maintainer(result)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


# The strategy is written for the model that generates the patch ("Generate a
# reviewable coordinated source change that removes X ...") and the caveats for
# the operator who reviews it ("The reviewer must approve ..."). Neither is
# addressed to the maintainer who reads a pull request.
_GENERATE_DIRECTIVE = re.compile(
    r"^(?:Generate|Produce|Draft|Prepare|Create|Write)\s+(?:an?\s+)?"
    r"(?:(?:reviewable|reviewed|reviewer-approved|minimal|safe)\s+)*"
    r"(?:(?:coordinated|multi-file|cross-file)\s+)*(?:(?:source|code)\s+)?"
    r"(?:change|patch|fix|edit|modification)(?:\s+set)?\s+(?:that|which)\s+(?P<rest>.+)$",
    re.DOTALL,
)
# A sentence about how the analysis tool models the rule ("without relying on
# undocumented detector behavior") is about the analysis, not the change.
_DETECTOR_MENTION = re.compile(r"\bdetector'?s?\b", re.IGNORECASE)
_PIPELINE_SENTENCE = re.compile(
    r"^(?:the\s+|a\s+|any\s+)?reviewers?\b"
    r"|^do not (?:generate|publish|apply|auto-apply)\b"
    r"|\breview[- ]only\b|\bthis draft\b|\bpublication is blocked\b",
    re.IGNORECASE,
)


def speak_to_the_maintainer(text: str) -> str:
    """Rewrite or drop sentences addressed to the pipeline or its operator.

    "Generate a change that removes X" becomes "This change removes X"; a
    sentence about what the reviewer must approve, or about review-only
    generation, is dropped. Lists, quotes and headings are left alone, and if
    nothing would remain the text is returned unchanged.
    """
    paragraphs = re.split(r"\n{2,}", str(text or ""))
    rewritten = []
    for paragraph in paragraphs:
        if paragraph.lstrip().startswith(("-", ">", "#", "Changes:", "```")):
            rewritten.append(paragraph)
            continue
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", paragraph):
            directive = _GENERATE_DIRECTIVE.match(sentence.strip())
            if directive:
                sentence = "This change " + directive.group("rest")
            elif (_PIPELINE_SENTENCE.search(sentence.strip())
                  or _EVIDENCE_LIMIT.search(sentence)
                  or _DETECTOR_MENTION.search(sentence)):
                continue
            kept.append(sentence)
        if kept:
            rewritten.append(" ".join(kept))
    result = "\n\n".join(rewritten)
    return result if result.strip() else str(text or "")


# What the adjudicator writes about the limits of its own evidence ("C5 and C6
# do not establish ...", "Module build ... cannot establish that contract").
# True, and addressed to whoever audits the analysis, not to a maintainer.
_EVIDENCE_LIMIT = re.compile(
    r"\b(?:cannot|can ?not|does not|do not|did not|could not)\s+"
    r"(?:establish|confirm|determine|settle|show)\b"
    r"|\bthe pipeline\b|\bthis pipeline\b|\bpipeline's\b",
    re.IGNORECASE,
)


MAX_TITLE_CHARACTERS = 80
# Edits whose reason says what the change does, in the order a title prefers
# them. Tests and repairs of a generated test describe coverage, not the fix.
_TITLE_EDIT_KINDS = ("method", "delete", "declaration")
# Opening verbs of a reason that describes what the edit leaves alone or only
# prepares, rather than the fix ("Retain the existing getUpdate name ...",
# "Imports the module BuildConfig needed to ...").
_NON_ACTION_VERBS = frozenset({
    "retain", "keep", "preserve", "import", "store", "expose", "document",
})
# Where a reason's first clause -- the action -- ends and its justification
# begins.
_CLAUSE_BREAK = re.compile(
    r",\s|;\s|\s(?:so|so that|while|instead of|ensuring|which|because|"
    r"before|after|within|without|and then)\s"
)


def _imperative(verb: str) -> str:
    """`Enables` -> `Enable`, `Verifies` -> `Verify`; other words unchanged."""
    lower = verb.lower()
    if lower.endswith("ies") and len(lower) > 4:
        stem = verb[:-3] + "y"
    elif lower.endswith(("sses", "shes", "ches", "xes", "zes")):
        stem = verb[:-2]
    elif lower.endswith("s") and not lower.endswith("ss") and len(lower) > 3:
        stem = verb[:-1]
    else:
        stem = verb
    return stem[:1].upper() + stem[1:]


def title_from_edit_reasons(edits: Iterable[dict]) -> str:
    """A pull request title from what its main edit does, or "".

    Every pull request the pipeline had generated was titled "Fix
    <method>", which names where the detector pointed and nothing about the
    change. The generated edits already say what they do ("Enables WebView
    debugging only for debug builds, within the existing Lollipop-only
    branch"), so the first clause of the first production edit's reason,
    put in the imperative, is the title. A reason that yields nothing short
    enough leaves the caller's title in place.
    """
    ranked = sorted(
        (item for item in edits or [] if isinstance(item, dict)
         and str(item.get("edit_kind", "")) in _TITLE_EDIT_KINDS),
        key=lambda item: _TITLE_EDIT_KINDS.index(str(item.get("edit_kind"))),
    )
    for item in ranked:
        reason = strip_evidence_citations(str(item.get("reason", ""))).strip()
        words = reason.split(maxsplit=1)
        if len(words) < 2 or not words[0][:1].isupper():
            continue
        verb = _imperative(words[0])
        if verb.lower() in _NON_ACTION_VERBS:
            continue
        clause = _CLAUSE_BREAK.split(words[1], maxsplit=1)[0].strip().rstrip(".")
        # A first clause that is still too long usually joins two actions
        # ("Configures TLS only with X and removes Y"); the first is the title.
        for candidate in (clause, clause.split(" and ", 1)[0]):
            title = f"{verb} {candidate.strip()}"
            if len(title) <= MAX_TITLE_CHARACTERS:
                return title
    return ""
