#!/usr/bin/env python3
"""Repair compiler diagnostics in an already-applied finding worktree.

This stage is deliberately narrower than the main LLM stage: it does not
reanalyze the finding or regenerate the security solution. It receives exact
compiler failures, diagnosed files, and referenced declarations, then produces
only exact replacement edits for those diagnosed files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from pathlib import Path



from cognalys.llm.fix_generation_common import COMMENT_PRESERVATION_GUIDANCE  # noqa: E402
from cognalys.llm.generate_error_fixes import OpenAIProvider, ResponseParser  # noqa: E402
from cognalys.llm.llm_providers import ProviderTransientFailure, remote_provider  # noqa: E402
from cognalys.llm.pipeline_common import prompt_json  # noqa: E402


# The removal of patch-created tests and its commit live with the validation,
# which also removes them when a test build fails the same way on base;
# pr_generator still reads REPAIR_COMMIT_PREFIX from here.
from cognalys.validation.applied_solutions import (  # noqa: E402,F401
    REPAIR_COMMIT_PREFIX,
    _PATCH_TEST_PATH,
    commit_repair,
    record_removed_tests,
    remove_patch_created_tests,
)
from cognalys.utils.edit_text import (  # noqa: E402
    match_trailing_line_break, read_exact, read_source, write_exact, write_source,
)


def validate_execution_report(report: dict) -> list[str]:
    """Deterministic shape guard for execution_report.json (see
    solution_applicator.py's identical check for why this exists)."""
    issues = []
    details = report.get("per_solution_details")
    if not isinstance(details, list):
        return ["per_solution_details must be a list"]
    for index, entry in enumerate(details):
        if not isinstance(entry, dict):
            issues.append(f"per_solution_details[{index}] is not an object")
            continue
        if entry.get("status") == "SUCCESS" and not str(
                entry.get("finding_id", "")).strip():
            issues.append(
                f"per_solution_details[{index}] is SUCCESS without finding_id"
            )
    return issues


def write_json_atomic(path: Path, value: dict | list) -> None:
    """Write via write-then-rename so a crash mid-write can't truncate a
    file every later pipeline step depends on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


# Corrections allowed after a repair is rejected while being applied.
MAX_REPAIR_CORRECTIONS = 1


REPAIRABLE_ATTRIBUTIONS = {
    "generated_patch",
    "preexisting_test_or_source_debt",
    "verified_preexisting_test_debt",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair validation compiler failures without rerunning finding analysis."
    )
    parser.add_argument("--execution-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-terra")
    parser.add_argument("--endpoint", default="https://api.openai.com/v1")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-output-tokens", type=int, default=16384)
    parser.add_argument(
        "--prompt-log-dir", type=Path,
        help="Directory for JSON records of prompts sent to OpenAI.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh"),
        default="high",
    )
    parser.add_argument("--project", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def repair_schema() -> dict:
    edit = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "source_file_path": {"type": "string"},
            "symbol": {"type": "string"},
            "original_code": {"type": "string"},
            "occurrences": {"type": "integer", "minimum": 1},
            "corrected_code": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": [
            "source_file_path", "symbol", "original_code", "occurrences",
            "corrected_code", "reason",
        ],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "decision": {"type": "string", "enum": ["REPAIR", "ABSTAIN"]},
            "explanation": {"type": "string"},
            "edits": {"type": "array", "items": edit, "maxItems": 16},
            "remaining_uncertainties": {
                "type": "array", "items": {"type": "string"},
            },
        },
        "required": ["decision", "explanation", "edits", "remaining_uncertainties"],
    }


def relative_diagnostic_path(worktree: Path, raw: str) -> str:
    """Express a compiler diagnostic's path relative to the worktree.

    Every caller compares the result against worktree-relative paths, so an
    absolute one must never be returned. `worktree / absolute` is the
    absolute path itself under pathlib, which made the whole-path candidate
    below match on its first iteration: the diagnosed set was then keyed by
    an absolute path while validate_and_apply normalized the model's echo of
    it back to a relative one, and the two never matched.
    """
    candidate = Path(raw.replace("\\", "/"))
    try:
        return candidate.resolve().relative_to(worktree.resolve()).as_posix()
    except (ValueError, OSError):
        pass
    parts = candidate.parts
    # Fall back to the longest trailing run of components that names a file
    # in this worktree; skip index 0 when it is the filesystem anchor.
    start = 1 if candidate.is_absolute() else 0
    for index in range(start, len(parts)):
        relative = Path(*parts[index:])
        if relative.is_absolute():
            continue
        if (worktree / relative).is_file():
            return relative.as_posix()
    return ""


def diagnosed_context(
    worktree: Path, raw_diagnostics: list,
) -> tuple[list[dict], dict[str, str]]:
    """Normalize the diagnostics and read the sources they name.

    Both returned structures are keyed by the same worktree-relative path
    the model is asked to echo back, and that path is also what
    validate_and_apply checks its edits against. Keeping the normalization
    here rather than inline in main() is what makes that agreement
    testable: the two halves were each covered on their own while the seam
    between them silently disagreed about whether paths were absolute.
    """
    diagnostics: list[dict] = []
    diagnosed_sources: dict[str, str] = {}
    for diagnostic in raw_diagnostics:
        item = dict(diagnostic)
        relative = relative_diagnostic_path(worktree, str(item.get("path", "")))
        if not relative:
            continue
        item["path"] = relative
        item["source_file_path"] = relative
        diagnostics.append(item)
        diagnosed_sources.setdefault(
            relative,
            (worktree / relative).read_text(encoding="utf-8", errors="replace"),
        )
    return diagnostics, diagnosed_sources


def _source_files(worktree: Path):
    """The Java and Kotlin sources of the worktree, outside build output."""
    for path in worktree.rglob("*"):
        if (path.suffix.lower() not in {".java", ".kt"} or not path.is_file()
                or any(part in {".git", ".gradle", "build"} for part in path.parts)):
            continue
        yield path


def _declaring_sources(worktree: Path, names: set, excluded: set[str]) -> list[dict]:
    """Every source outside `excluded` declaring one of `names`, in walk order."""
    results = []
    if not names:
        return results
    for path in _source_files(worktree):
        relative = path.relative_to(worktree).as_posix()
        if relative in excluded:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > 80_000:
            continue
        matched = [name for name in names if re.search(
            rf"\b(?:class|object|interface)\s+{re.escape(name)}\b", content
        )]
        if matched:
            results.append({
                "source_file_path": relative,
                "declared_types": matched,
                "content": content,
            })
    return results


def supporting_declarations(worktree: Path, source_lines: list[str],
                            diagnosed: set[str]) -> list[dict]:
    """Read-only sources declaring the types a diagnosed line depends on.

    The names were previously collected with `([A-Z]\\w*)\\s*\\.`, which only
    sees an identifier followed by a dot -- static or member access. A
    constructor call is written `new Schedule(...)`, so the one type the fix
    actually needed was never collected, the repair stage was handed only the
    broken test, and it correctly abstained rather than invent an API. Every
    capitalised identifier is collected instead; the declaration search below
    already discards the ones that name nothing in the repository.
    """
    names = set()
    for line in source_lines:
        names.update(re.findall(r"\b([A-Z][A-Za-z0-9_$]*)\b", line))
    return _declaring_sources(worktree, names, diagnosed)[:12]


# The value's type and the type a declaration expects for it, for the
# compiler messages that name both (kotlinc K2 and K1, javac). Only these can
# widen the repair's scope. A message naming no types -- a wrong number of
# arguments, an unresolved or invented member, javac's "method X in class Y
# cannot be applied to given types;" whose required/found lines the recorded
# diagnostic does not keep -- cannot tell a declaration the patch's new type
# now reaches from a call the patch wrote wrong (review of 2026-10-31: a
# patch calling CryptoHelper.encrypt(data, key, iv), CryptoHelper.encryptGcm,
# or passing a byte[] to RedReader's General.sha256(String) in javac form
# would each have made the existing class editable), so it keeps every
# declaration read-only. K2's "Initializer/Return/Assignment type mismatch:
# expected 'X', actual 'Y'" is against a declaration of the diagnosed file
# itself and is not matched here either.
_ACTUAL_AND_EXPECTED = (
    re.compile(r"actual type is '(?P<actual>[^']+)', but '(?P<expected>[^']+)' was expected"),
    re.compile(r"actual type is (?P<actual>[\w.$]+\??), but (?P<expected>[\w.$]+\??) was expected"),
    re.compile(r"inferred type is (?P<actual>\S+) but (?P<expected>\S+) was expected"),
    re.compile(r"incompatible types: (?P<actual>\S+) cannot be converted to (?P<expected>\S+)"),
)
# A repository type the diagnosed line calls or reaches through: a
# constructor call, a static or companion member, a generic use, a
# reference, or `new Name`.
_DECLARATION_USE = r"(?:\bnew\s+{name}\b|\b{name}\s*(?:\(|\.|<|::))"
# Directories whose sources the repository does not write by hand.
_NOT_HAND_WRITTEN = {"build", "generated", ".gradle", ".git"}
# Generated code committed next to hand-written sources (protobuf, AIDL,
# greenDAO output under src/main/java) says so in its header or carries
# @Generated; its generator would overwrite any edit (review of 2026-10-31).
_GENERATED_HEADER = re.compile(
    r"(?i)auto-?generated|generated by|do not (?:edit|modify)"
    r"|this (?:file|class|code) (?:is|was) (?:auto-?)?generated|code generated")
_GENERATED_ANNOTATION = re.compile(r"@(?:javax\.annotation\.(?:processing\.)?)?Generated\b")
# Source sets every APK of the module compiles. A product flavour's set is
# added only for the flavour the validation build selects; a build type's
# (debug, release, fdroidRelease) never is: the build either does not
# compile it or the published APK does not contain it. A shared KMP set
# (commonMain) also reaches desktop or iOS callers no Android build checks.
_APK_SOURCE_SETS = {"", "main", "androidMain"}


def _simple_type_name(type_text: str) -> str:
    """`java.security.KeyStore.PrivateKeyEntry` -> `PrivateKeyEntry`."""
    bare = re.sub(r"<.*", "", str(type_text)).strip().rstrip("?!").strip()
    return bare.rsplit(".", 1)[-1].strip("'\" ")


def _actual_and_expected(message: str) -> tuple[str, str] | None:
    """(value's type, expected type) as simple names, or None when the
    message does not name both, or names a generic type: `Map<String,
    KeyPair>` against `Map<String, PrivateKeyEntry>` differs only inside the
    type arguments, which a simple name cannot compare."""
    for pattern in _ACTUAL_AND_EXPECTED:
        match = pattern.search(message)
        if match:
            if "<" in match.group("actual") or "<" in match.group("expected"):
                return None
            actual = _simple_type_name(match.group("actual"))
            expected = _simple_type_name(match.group("expected"))
            return (actual, expected) if actual and expected and actual != expected else None
    return None


# Declarations a line holds, read per language from its code alone (comments
# dropped, literals emptied): (name, "value" or "function", the declared type's
# simple name, or None when the compiler infers it). Review of 2026-10-31: one
# pattern for both languages read `// signer: Uses the entry`, `"signer:
# Ready"`, a `when` branch (`signer -> Unit`) and a Java ternary (`ready ?
# signer : Fallback.INSTANCE`) as declarations of `signer`. A Kotlin parameter
# (`name: Type`) is therefore read only at the start of the line or right
# after `(`, `,` or `{`, an untyped lambda's parameters only after a `{` that
# opens no `when` block, and Java's `Type name` only in Java files.
_KOTLIN_VALUE = re.compile(
    r"\b(?:val|var)\s+(?:<[^>]*>\s*)?(?:[\w$]+\.)*(?P<name>[A-Za-z_$][\w$]*)"
    r"(?:\s*:\s*(?P<type>[A-Za-z_$][\w$.]*))?")
_KOTLIN_FUNCTION = re.compile(
    r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w$]+(?:<[^>]*>)?\??\.)*(?P<name>[A-Za-z_$][\w$]*)\s*\(")
_KOTLIN_PARAMETER = re.compile(
    r"(?:^|(?<=[(,{]))\s*(?:@[\w$.]+(?:\([^()]*\))?\s+)*"
    r"(?:(?:vararg|noinline|crossinline|private|public|protected|internal|override|open|final)\s+)*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*:\s*(?P<type>[A-Za-z_$][\w$.]*)")
_KOTLIN_LAMBDA = re.compile(
    r"\{\s*(?P<names>[A-Za-z_$][\w$]*(?:\s*,\s*[A-Za-z_$][\w$]*)*)\s*->")
_JAVA_DECLARATION = re.compile(
    r"(?<![\w$.])(?P<type>(?:[a-z][\w$]*\.)*[A-Z][\w$]*(?:\.[A-Z][\w$]*)*"
    r"|byte|short|int|long|char|float|double|boolean|var)"
    r"(?:<[^;=(){}]*>)?(?:\[\])*\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?=(?P<end>[=;,):(]))")
_NOT_A_NAME = frozenset({
    "this", "super", "it", "null", "true", "false", "new", "as", "is", "in", "return",
    "object", "class", "interface", "if", "else", "when", "try", "catch", "finally", "throw",
    "for", "while", "do", "break", "continue", "val", "var", "fun", "instanceof", "case",
    "default", "switch", "package", "import"})
_IDENTIFIER = r"[A-Za-z_$][\w$]*"


def _normalized_line(text: str) -> str:
    return " ".join(str(text).split())


def _code_text(line: str) -> str:
    """The line without its comments, every string or character literal
    emptied: a name in a comment or a log message declares nothing."""
    if line.strip().startswith(("*", "/*")):
        return ""
    kept, quote, index = [], "", 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                kept.append(char)
                quote = ""
            index += 1
            continue
        if char in "\"'":
            quote = char
            kept.append(char)
        elif line.startswith("//", index):
            break
        elif line.startswith("/*", index):
            close = line.find("*/", index + 2)
            if close < 0:
                break
            index = close + 2
            continue
        else:
            kept.append(char)
        index += 1
    return "".join(kept)


def _closing_parenthesis(code: str, opened: int) -> int | None:
    depth = 0
    for index in range(opened, len(code)):
        depth += {"(": 1, ")": -1}.get(code[index], 0)
        if depth == 0:
            return index
    return None


def _declarations(line: str, java: bool) -> list[tuple[str, str, str | None]]:
    code = _code_text(line)
    found = []
    if java:
        for match in _JAVA_DECLARATION.finditer(code):
            found.append((match.group("name"), "function" if match.group("end") == "(" else "value",
                          None if match.group("type") == "var"
                          else _simple_type_name(match.group("type"))))
    else:
        for match in _KOTLIN_VALUE.finditer(code):
            found.append((match.group("name"), "value",
                          _simple_type_name(match.group("type")) if match.group("type") else None))
        for match in _KOTLIN_FUNCTION.finditer(code):
            close = _closing_parenthesis(code, match.end() - 1)
            returned = (re.match(r"\s*:\s*([A-Za-z_$][\w$.]*)", code[close + 1:])
                        if close is not None else None)
            found.append((match.group("name"), "function",
                          _simple_type_name(returned.group(1)) if returned else None))
        for match in _KOTLIN_PARAMETER.finditer(code):
            found.append((match.group("name"), "value", _simple_type_name(match.group("type"))))
        for match in _KOTLIN_LAMBDA.finditer(code):
            if not re.search(r"\bwhen\s*(?:\([^()]*\))?\s*$", code[:match.start()]):
                found.extend((name, "value", None)
                             for name in re.split(r"\s*,\s*", match.group("names")))
    return [item for item in found if item[0] not in _NOT_A_NAME]


def _roots_and_calls(expression: str) -> tuple[str, list[tuple[str, int]], set[str]]:
    """An expression's code, the names that start its chains (with where each
    ends), and the functions it calls. `signer.privateKey` starts at `signer`:
    a member named like something the patch declares (`val privateKey` in
    another file) is not that thing (review of 2026-10-31)."""
    code = re.sub(r"\bthis(?:@[\w$]+)?\s*\.\s*", "", _code_text(expression))
    roots = [(match.group(), match.end())
             for match in re.finditer(rf"(?<![\w$.:@]){_IDENTIFIER}", code)
             if match.group() not in _NOT_A_NAME]
    calls = {match.group(1) for match in re.finditer(
        rf"(?<![\w$])({_IDENTIFIER})\s*(?:<[^<>()]*>)?\s*\(", code)} - _NOT_A_NAME
    return code, roots, calls


def _call_arguments(line: str) -> list[tuple[str, int, int, int]]:
    """Every argument of a call the line closes: (callee, the call's open
    parenthesis, argument start, argument end). A call left open at the end
    of the line (continued on the next one) yields nothing: which argument
    holds a value is then not readable from the line."""
    arguments = []
    # (bracket, its index, current argument's start, the call's earlier arguments)
    stack: list[list] = []
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote and line[index - 1] != "\\":
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char in "([{":
            stack.append([char, index, index + 1, []])
        elif char in ")]}" and stack:
            opened, start, argument, earlier = stack.pop()
            if opened == "(":
                callee = re.search(r"((?:\bnew\s+)?[\w$.]+)\s*(?:<[^<>()]*>)?\s*$", line[:start])
                arguments.extend((callee.group(1) if callee else "", start, begin, finish)
                                 for begin, finish in [*earlier, (argument, index)])
        elif char == "," and stack and stack[-1][0] == "(":
            stack[-1][3].append((stack[-1][2], index))
            stack[-1][2] = index + 1
    return sorted(arguments, key=lambda item: item[3] - item[2])


def _replaced_argument(line: str, old: str, name: str) -> tuple | None:
    """The one argument of a call to `name` that `line` changed against the
    line it replaced, `old`: (call's open parenthesis, start, end, old
    argument text), when every other character of the two lines is the same
    and `old` had that argument at the same place in the same call."""
    prefix = 0
    limit = min(len(line), len(old))
    while prefix < limit and line[prefix] == old[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and line[-1 - suffix] == old[-1 - suffix]:
        suffix += 1
    start, end = prefix, len(line) - suffix
    if end <= start:
        return None
    old_arguments = {(callee, begin, finish) for callee, _, begin, finish in _call_arguments(old)}
    for callee, opened, begin, finish in _call_arguments(line):
        if not (begin <= start and end <= finish):
            continue
        # Only the innermost argument holding the change: in
        # `Authenticator(wrap(entry), raw)` the mismatch may be wrap's.
        old_finish = len(old) - (len(line) - finish)
        if (name in re.findall(r"[\w$]+", callee)
                and (callee, begin, old_finish) in old_arguments):
            return opened, begin, finish, old[begin:old_finish]
        return None
    return None


def _value_the_patch_introduced(source_line: str, path: str, file_edits: list[dict],
                                patch_edits: list[dict], name: str, expected: str,
                                base_text: str) -> bool:
    """Whether the value the diagnosed line passes to `name` comes from the
    patch's own replacement of the expected type, when the compiler named the
    value's type but the patch never spelled it.

    BlueGate (run_v6, 2026-10-31): the patch made getOrCreateKeyPair(): KeyPair
    into getOrCreatePrivateKeyEntry(): KeyStore.PrivateKeyEntry, kept the
    result in a new `keyEntry: KeyStore.PrivateKeyEntry` and passed
    `keyEntry.privateKey` to Authenticator(...), still taking a KeyPair;
    kotlinc reported "actual type is 'java.security.PrivateKey!'", a type
    Kotlin inferred from the property and no line of the patch names, so the
    spelled-type test made nothing editable and the repair abstained a fourth
    time. Every condition must hold, and anything the code does not show
    keeps the declaration read-only:
    - the diagnosed line is one the patch wrote (in its file's corrected
      code, not in its original code), and against one line of its original
      code it changed exactly one argument of the call to `name`, the rest of
      both lines being identical, the new argument not in the original code;
    - the patch moved a producer off the expected type: an edit replaced a
      function declared to return it with one declared to return another
      type (getOrCreateKeyPair -> getOrCreatePrivateKeyEntry);
    - the new argument comes from that replacement: every name its chains
      start with calls such a producer, receives its call, or is a name the
      patch newly declares in the diagnosed file (in neither its original
      code nor the file's base, or re-declared off the expected type) with
      the producer's new type, or untyped and initialised from such a value;
    - the old argument was of the expected type: it names a value the
      patch's original code of that file declared with that type, or calls a
      function declared to return it, on a line the patch rewrote;
    - no other argument of the call names anything the patch declares in that
      file or a producer, so which one the compiler meant is decidable.
    Review of 2026-10-31: a name the patch merely wrote again (`context` on a
    rewritten signature), declared in another file, read from a comment, or
    a value the patch only renamed (`val signingKey = context.resources`)
    is not the patch's; RedReader's shape (`user: User` replacing `text:
    String`, then `user.name.toByteArray()` handed to General.sha256(String))
    replaces no producer and stays read-only.
    """
    java = str(path).endswith(".java")
    line = _normalized_line(source_line)

    def ordered(edit, key):
        return [_normalized_line(text) for text in str(edit.get(key, "")).splitlines()
                if text.strip()]

    def lines(edits, key):
        return {text for edit in edits for text in ordered(edit, key)}

    written, replaced = lines(file_edits, "corrected_code"), lines(file_edits, "original_code")
    if not line or line not in written or line in replaced:
        return False
    changes = {change for old in replaced
               if (change := _replaced_argument(line, old, name)) is not None}
    if len({change[:3] for change in changes}) != 1:
        return False
    opened, start, end, _ = next(iter(changes))

    def unlabelled(argument: str) -> str:
        return re.sub(r"^[\w$]+\s*=(?!=)\s*", "", argument.strip())

    value = unlabelled(line[start:end])
    original_code = "\n".join(lines(patch_edits, "original_code"))
    if not value or re.search(rf"(?<![\w$.]){re.escape(value)}(?![\w$])", original_code):
        return False

    def is_java(edit) -> bool:
        return str(edit.get("source_file") or edit.get("source_file_path") or "").endswith(".java")

    # Producers the patch moved off the expected type: function name -> its new type.
    producers: dict[str, str] = {}
    returning_expected: set[str] = set()
    original_types: dict[str, set] = {}
    for edit in patch_edits:
        old_lines, new_lines = ordered(edit, "original_code"), ordered(edit, "corrected_code")
        for text in old_lines:
            for declared, _, type_name in _declarations(text, is_java(edit)):
                original_types.setdefault(declared, set()).add(type_name)

        def functions(texts, other):
            return [(declared, type_name) for text in texts if text not in other
                    for declared, kind, type_name in _declarations(text, is_java(edit))
                    if kind == "function"]

        removed, added = functions(old_lines, set(new_lines)), functions(new_lines, set(old_lines))
        returning_expected |= {declared for declared, type_name in removed if type_name == expected}
        if len(removed) == len(added):
            producers.update((new_name, new_type)
                             for (_, old_type), (new_name, new_type) in zip(removed, added)
                             if old_type == expected and new_type and new_type != expected)
    if not producers:
        return False
    file_written = [text for edit in file_edits for text in ordered(edit, "corrected_code")
                    if text not in replaced]
    file_removed = [text for edit in file_edits for text in ordered(edit, "original_code")
                    if text not in written]
    new_declarations = [(declared, kind, type_name, text) for text in file_written
                        for declared, kind, type_name in _declarations(text, java)]
    old_values = {declared for text in file_removed
                  for declared, kind, type_name in _declarations(text, java)
                  if kind == "value" and type_name == expected}
    base_names = set(re.findall(_IDENTIFIER, str(base_text or "")))
    producer_call = (rf"\s*(?:!!)?\s*\??\.\s*(?:{'|'.join(map(re.escape, producers))})"
                     rf"\s*(?:<[^<>()]*>)?\s*\(")

    def own(declared: str) -> bool:
        # New to the file, or the same name re-declared off the expected type.
        return ((declared not in original_types and declared not in base_names)
                or original_types.get(declared) == {expected})

    def from_replacement(expression: str, depth: int) -> bool:
        code, roots, _ = _roots_and_calls(expression)
        return bool(roots) and all(
            re.match(producer_call, code[finish:])
            or (root in producers and re.match(r"\s*(?:<[^<>()]*>)?\s*\(", code[finish:]))
            or holds_replacement(root, depth)
            for root, finish in roots)

    def holds_replacement(root: str, depth: int) -> bool:
        for declared, kind, type_name, text in new_declarations:
            if declared != root or kind != "value" or not own(declared):
                continue
            if type_name in set(producers.values()):
                return True
            initial = re.search(rf"(?<![\w$.]){re.escape(declared)}\s*(?::[^=]*)?=(?!=)\s*(.+)",
                                _code_text(text))
            if (type_name is None and depth == 0 and initial
                    and from_replacement(initial.group(1).rstrip(";"), depth + 1)):
                return True
        return False

    if not from_replacement(value, 0):
        return False
    for *_, old_value in changes:
        _, roots, calls = _roots_and_calls(unlabelled(old_value))
        if not ({root for root, _ in roots} & old_values or calls & returning_expected):
            return False
    patch_names = {declared for declared, *_ in new_declarations} | set(producers)
    for _, call, begin, finish in _call_arguments(line):
        if call == opened and (begin, finish) != (start, end):
            _, roots, calls = _roots_and_calls(unlabelled(line[begin:finish]))
            if ({root for root, _ in roots} | calls) & patch_names:
                return False
    return True


def _gradle_module(worktree: Path, relative: str) -> str | None:
    """The worktree-relative directory of the Gradle project holding a file."""
    root = worktree.resolve()
    current = (worktree / relative).resolve().parent
    while True:
        if (current / "build.gradle").is_file() or (current / "build.gradle.kts").is_file():
            return current.relative_to(root).as_posix() if current != root else ""
        if current == root or root not in current.parents:
            return None
        current = current.parent


def _module_in_build(worktree: Path, module: str) -> bool:
    """Whether the root settings file includes a Gradle project directory."""
    if module == "":
        return True
    gradle_path = re.escape(module.replace("/", ":"))
    for name in ("settings.gradle", "settings.gradle.kts"):
        settings = worktree / name
        if settings.is_file() and re.search(
                rf"[\"']:?{gradle_path}[\"']",
                settings.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def _module_depends_on(worktree: Path, consumer: str, provider: str) -> bool:
    """Whether the consumer Gradle project declares a dependency on the
    provider project (`project(":core")`, `project(path: ':core')` or the
    type-safe `projects.core`). Only a direct dependency counts: a module
    the settings include but the app does not compile -- a :wear or :desktop
    application next to :app -- is another APK (review of 2026-10-31)."""
    if consumer == provider:
        return True
    gradle_path = re.escape(":" + provider.replace("/", ":"))
    accessor = re.escape(".".join(
        re.sub(r"[-_](\w)", lambda match: match.group(1).upper(), part)
        for part in provider.split("/")))
    for name in ("build.gradle", "build.gradle.kts"):
        build = worktree / consumer / name
        if build.is_file() and re.search(
                rf"project\s*\(\s*(?:path\s*[:=]\s*)?[\"']{gradle_path}[\"']"
                rf"|\bprojects\.{accessor}\b",
                build.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def _compiled_into_the_apk(worktree: Path, relative: str, module: str) -> bool:
    """Whether the APK's validation build compiles a production source."""
    from pathlib import PurePosixPath

    from cognalys.extraction.rename_safety import _source_set
    from cognalys.validation.applied_solutions import _selected_product_flavors

    source_set = _source_set(PurePosixPath(relative))
    if source_set in _APK_SOURCE_SETS:
        return True
    selected = _selected_product_flavors(worktree / module)
    combined = "".join(flavor if index == 0 else flavor[:1].upper() + flavor[1:]
                       for index, flavor in enumerate(selected))
    return source_set in set(selected) | ({combined} if combined else set())


def _owned_production_source(worktree: Path, relative: str, diagnosed_path: str,
                             content: str = "") -> str:
    """Why a source is not the app's own hand-written production code the APK
    builds with the diagnosed file, or "" when it is.

    Owned: under the worktree, outside build output, generated code (by path
    or by its own header) and any nested checkout (a submodule's .git), not
    in a test source set, in a source set the APK's build compiles
    (_compiled_into_the_apk), and in the diagnosed file's Gradle project or
    one that project depends on directly and the root settings include.
    """
    from pathlib import PurePosixPath

    from cognalys.utils.disposition import files_the_build_does_not_compile
    from cognalys.validation.applied_solutions import is_test_source

    parts = PurePosixPath(relative).parts
    if ".." in parts or any(part in _NOT_HAND_WRITTEN for part in parts):
        return "generated_or_build_output"
    if _GENERATED_HEADER.search(content[:2000]) or _GENERATED_ANNOTATION.search(content):
        return "generated_source"
    if is_test_source(relative):
        return "test_source"
    if files_the_build_does_not_compile([relative]):
        return "source_set_not_in_the_apk"
    for depth in range(1, len(parts)):
        if (worktree.joinpath(*parts[:depth]) / ".git").exists():
            return "nested_checkout"
    module = _gradle_module(worktree, relative)
    if module is None:
        return "outside_any_gradle_project"
    if not _compiled_into_the_apk(worktree, relative, module):
        return "source_set_not_in_the_apk"
    if not _module_reachable(worktree, relative, diagnosed_path):
        return "gradle_project_not_a_dependency"
    return ""


def _module_reachable(worktree: Path, relative: str, diagnosed_path: str) -> bool:
    """Whether the diagnosed file's Gradle project compiles against the one
    holding `relative`: the same project, or one it depends on directly that
    the root settings include."""
    module = _gradle_module(worktree, relative)
    consumer = _gradle_module(worktree, diagnosed_path)
    if module is None or consumer is None:
        return False
    return module == consumer or (_module_in_build(worktree, module)
                                  and _module_depends_on(worktree, consumer, module))


def _is_test_or_build_output(relative: str) -> bool:
    from pathlib import PurePosixPath

    from cognalys.validation.applied_solutions import is_test_source

    return (is_test_source(relative)
            or any(part in _NOT_HAND_WRITTEN for part in PurePosixPath(relative).parts))


def _package_of(content: str) -> str:
    match = re.search(r"(?m)^\s*package\s+([\w.]+)", content)
    return match.group(1) if match else ""


def _declares_at_top_level(content: str, name: str) -> bool:
    """A class, object or interface `name` declared at column 0 -- the one an
    unqualified `name` resolves to through the package or an import; a nested
    one is reached as Outer.name."""
    return bool(re.search(
        rf"(?m)^(?:[@\w]+\s+)*(?:class|object|interface)\s+{re.escape(name)}\b", content))


def _resolves_to(caller: str, name: str, package: str) -> bool:
    """Whether `name`, written unqualified in the caller's text, is the type
    declared top-level in `package`: an explicit import of it, the caller's own
    package with no import of another `name`, or a wildcard import of the
    package. A declaration matched by simple name only -- another package's
    Authenticator in the same module -- is not the one the call reaches
    (review of 2026-10-31)."""
    imports = re.findall(
        r"(?m)^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)(?:\s+as\s+(\w+))?\s*;?\s*$", caller)
    named = [(path, alias) for path, alias in imports
             if (alias or path.rsplit(".", 1)[-1]) == name]
    if named:
        return all(path == (f"{package}.{name}" if package else name) and not alias
                   for path, alias in named)
    return _package_of(caller) == package or (bool(package) and any(
        path == f"{package}.*" for path, _ in imports))


def patch_consequence_declarations(worktree: Path, diagnostics: list[dict],
                                   diagnosed: set[str], patch_edits: list[dict],
                                   base_source=None, planned_files=()) -> list[dict]:
    """Owned declarations the patch's new types now reach, which the repair may edit.

    BlueGate (run, run_v4, run_v5; 2026-10-29/30): the KeyPairSpec patch made
    KeyManager.getOrCreateKeyPair return KeyStore.PrivateKeyEntry and updated
    both callers, but both pass the entry on to `Authenticator(...)`, whose
    constructor -- in the same app module, outside the plan -- still takes a
    KeyPair. The compiler reported the mismatch at the two call sites, the
    repair was told supporting declarations are read-only, and it abstained
    three times: building a KeyPair at the call site would compile and undo
    the correction, and the only honest repair was to change Authenticator.

    A declaration qualifies only when every condition holds; a case that
    cannot be decided keeps it read-only, and the repair abstains as before:
    (a) a patch-caused diagnostic in a diagnosed file names the value's type
    and the expected type (_actual_and_expected), the value is the patch's --
    its type is written by the patch or, when the compiler inferred it
    (BlueGate run_v6 passed `keyEntry.privateKey`, a PrivateKey no line
    names), the line hands the call, in place of a value of the expected
    type, one derived from a producer the patch moved off that type
    (_value_the_patch_introduced) --, the expected
    one is a type the patch replaced (named in its original code, gone from
    its corrected code), and the expected type is not written on the
    diagnosed line itself (`val pair: KeyPair = entry` is the line's own
    declaration). RedReader (2026-10-19) is the counterexample:
    its patch passed a byte[] to the existing General.sha256(String), a
    call-site mistake; the patch had not moved away from String;
    (b) the line calls or reaches that declaration, which names the expected
    type, is declared top-level in the package the caller's package or
    imports resolve the name to (_resolves_to), is the app's own hand-written
    production source the APK's build compiles (_owned_production_source),
    and is the only such declaration of that name;
    (c) the declaration is not in a file the patch or its plan edits: the
    patch's own corrected file holds the security correction, and inviting
    the repair to "adapt" it would let it revert that correction where no
    review reads it again (review of 2026-10-31: `Authenticator(KeyManager.
    getOrCreateKeyPair(), raw)` made KeyManager.kt editable);
    (d) the diagnosed file already used that declaration before the patch
    (`base_source(path)`, the file at the analysed revision): what changed is
    what the patch now passes into an existing use, not a call the patch
    invented. einkbro (2026-09-17): its generated test, a file the base did
    not have, called an instance method as static, and adapting BackupUnit to
    that call would have bent production code around a wrong test.
    Libraries, generated code, tests and other projects stay read-only.
    """
    from cognalys.validation.applied_solutions import PATCH_CAUSED_ATTRIBUTIONS

    def names(code: str, type_name: str) -> bool:
        return bool(re.search(rf"(?<![\w$]){re.escape(type_name)}(?![\w$])", code))

    def normalized(path) -> str:
        return str(path or "").replace("\\", "/").removeprefix("./")

    edits = [edit for edit in patch_edits or [] if isinstance(edit, dict)]
    patch_code = "\n".join(str(edit.get("corrected_code", "")) for edit in edits)
    patch_original = "\n".join(str(edit.get("original_code", "")) for edit in edits)
    patched = {normalized(edit.get("source_file") or edit.get("source_file_path"))
               for edit in edits} | {normalized(path) for path in planned_files or []}
    patched.discard("")
    base_source = base_source or (lambda _path: "")
    base_files: dict[str, str] = {}
    caller_files: dict[str, str] = {}
    # Declared type name -> (diagnostic, expected type, value's type).
    candidates: dict[str, list[tuple[dict, str, str]]] = {}
    for item in diagnostics:
        source_line = str(item.get("source_line", ""))
        if item.get("attribution") not in PATCH_CAUSED_ATTRIBUTIONS or not source_line:
            continue
        typed = _actual_and_expected(str(item.get("message", "")))
        if typed is None:
            continue
        actual, expected = typed
        if (not names(patch_original, expected) or names(patch_code, expected)
                or names(source_line, expected)):
            continue
        spelled = names(patch_code, actual)
        path = str(item.get("path", ""))
        if path not in base_files:
            base_files[path] = base_source(path) or ""
            caller_files[path] = read_source(worktree / path) if (worktree / path).is_file() else ""
        file_edits = [edit for edit in edits if normalized(
            edit.get("source_file") or edit.get("source_file_path")) == normalized(path)]
        for name in dict.fromkeys(re.findall(r"\b([A-Z][A-Za-z0-9_$]*)\b", source_line)):
            use = _DECLARATION_USE.format(name=re.escape(name))
            if (re.search(use, source_line) and re.search(use, base_files[path])
                    and (spelled or _value_the_patch_introduced(
                        source_line, path, file_edits, edits, name, expected,
                        base_files[path]))):
                candidates.setdefault(name, []).append((item, expected, actual))
    # Declared name -> {declaration file: the diagnostics reaching it}.
    reaching: dict[str, dict[str, list[tuple[dict, str, str]]]] = {}
    # Declared name -> every hand-written production declaration the call
    # could reach, owned or not, built into the APK or not.
    resolving: dict[str, set[str]] = {}
    declarations = {}
    for declaration in _declaring_sources(worktree, set(candidates), diagnosed | patched):
        relative = declaration["source_file_path"]
        content = declaration["content"]
        for name in declaration["declared_types"]:
            if not _declares_at_top_level(content, name):
                continue
            for entry in candidates[name]:
                caller = str(entry[0].get("path", ""))
                if not _resolves_to(caller_files.get(caller, ""), name, _package_of(content)) \
                        or not _module_reachable(worktree, relative, caller):
                    continue
                if not _is_test_or_build_output(relative):
                    resolving.setdefault(name, set()).add(relative)
                if (names(content, entry[1])
                        and not _owned_production_source(worktree, relative, caller, content)):
                    reaching.setdefault(name, {}).setdefault(relative, []).append(entry)
                    declarations[relative] = declaration
    by_file: dict[str, list[tuple[dict, str, str]]] = {}
    for name, files in reaching.items():
        if len(files) != 1 or len(resolving.get(name, ())) != 1:
            # Another declaration of the name the call could reach: a copy in
            # another flavour (fdroid and gplay), a desktop actual next to the
            # Android one, a class shadowing another. Adapting only the one
            # the validation build compiles leaves the other variant calling
            # the old signature -- a build no check here runs (review of
            # 2026-10-31) -- and which one the compiler meant is not
            # decidable from the message.
            continue
        for relative, entries in files.items():
            by_file.setdefault(relative, []).extend(entries)
    results = []
    for relative, reached in by_file.items():
        declaration = declarations[relative]
        sites = list(dict.fromkeys(
            f"{item.get('path', '')}:{item.get('line', '')}: {item.get('message', '')}"
            for item, _, _ in reached))
        replaced = list(dict.fromkeys(f"{expected} with {actual}" for _, expected, actual in reached))
        declared = [name for name in declaration["declared_types"] if name in reaching
                    and relative in reaching[name]]
        results.append({
            **declaration,
            "declared_types": declared,
            "editable": True,
            "editable_reason": (
                f"{', '.join(declared)} is declared in this app's own production source, "
                f"which the build compiles; the patch replaced {', '.join(replaced)}, and "
                "that change now reaches it and fails to compile at "
                + "; ".join(sites)
                + ". Adapt this declaration minimally to accept what the corrected "
                "code now passes, and its uses inside this file."),
            "diagnostics": sites,
        })
    return results


def _verified_imports(repository: str) -> list[dict]:
    try:
        from cognalys.learning.knowledge import load_knowledge, verified_imports
    except ImportError:
        return []
    from cognalys.llm.fix_generation_common import holdout_repository

    if holdout_repository():
        return []  # B13: the finding's own repository gives nothing
    return verified_imports(load_knowledge(), repository)


_UNRESOLVED_SYMBOL = re.compile(
    r"Unresolved reference[:\s]+'?(?P<kotlin>[A-Za-z_$][\w$]*)'?"
    r"|symbol:\s+(?:class|method|variable|interface)\s+(?P<java>[A-Za-z_$][\w$]*)"
)
MAX_IMPORT_HINTS = 3


def unresolved_symbol_imports(worktree: Path, diagnostics: list[dict],
                              diagnosed: set[str]) -> list[dict]:
    """How the rest of the repository imports each symbol the compiler could
    not resolve.

    Gallery's patch imported ensureBackgroundThread from
    org.fossify.commons.extensions; the compiler said only "Unresolved
    reference 'ensureBackgroundThread'", and the repair, seeing no import
    anywhere, replaced the repository's own helper with a raw Thread. Every
    other file in the repository imported it from org.fossify.commons.helpers.
    """
    names = []
    for item in diagnostics:
        for match in _UNRESOLVED_SYMBOL.finditer(
            f"{item.get('message', '')}\n{item.get('source_line', '')}"
        ):
            name = match.group("kotlin") or match.group("java")
            if name and name not in names:
                names.append(name)
    if not names:
        return []
    patterns = {
        name: re.compile(
            rf"^\s*import\s+(?:static\s+)?[\w$.]+\.{re.escape(name)}(?:\s+as\s+[\w$]+)?\s*;?\s*$",
            re.MULTILINE,
        )
        for name in names
    }
    counts: dict[str, dict[str, int]] = {name: {} for name in names}
    for path in worktree.rglob("*"):
        if (path.suffix.lower() not in {".java", ".kt"} or not path.is_file()
                or any(part in {".git", ".gradle", "build"} for part in path.parts)):
            continue
        if path.relative_to(worktree).as_posix() in diagnosed:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in patterns.items():
            for match in pattern.finditer(content):
                line = match.group(0).strip().rstrip(";")
                counts[name][line] = counts[name].get(line, 0) + 1
    return [
        {
            "symbol": name,
            "imports": [
                {"import": line, "files": count}
                for line, count in sorted(found.items(), key=lambda item: -item[1])[:MAX_IMPORT_HINTS]
            ],
        }
        for name, found in counts.items() if found
    ]


# A test-case annotation of JUnit 4/5 or kotlin.test, bare or qualified.
_TEST_CASE_ANNOTATION = re.compile(
    r"@(?:[\w.]+\.)?(?:Test|ParameterizedTest|RepeatedTest|TestFactory)\b"
)


def semantic_regressions(worktree: Path, solution: dict, repair_edits: list[dict]) -> list[dict]:
    """What the generation-time semantic gates say about a repair's own edits.

    A repair is generated after the patch's checks, by a separate call that no
    gate looked at: hfut-schedule's repair reintroduced reflection in a test,
    the very construct REFLECTION_ONLY_TEST_SEAM had rejected during generation.
    The gates are read against the worktree (its files are what the repair's
    `original_code` came from), with the finding's own context, and only what
    they say about the repair's edits is returned.
    """
    try:
        from cognalys.llm.semantic_validation import validate_coordinated_semantics
        from cognalys.validation.applied_solutions import is_test_source
    except ImportError:
        return []
    evidence = solution.get("evidence_analysis") or {}
    error = {
        "repository_path": str(worktree),
        # The worktree already holds the patch, tests it generated included:
        # its files are not the project's own baseline.
        "repository_includes_patch": True,
        "error_message": solution.get("error_message", ""),
        "csv_class": solution.get("csv_class", ""),
        "spec": solution.get("spec", ""),
        "method_name": solution.get("method_name", ""),
        "source_file_path": solution.get("source_file_path") or solution.get("source_file", ""),
        "policy_source_paths": solution.get("changed_files") or [
            edit.get("source_file_path", "") for edit in solution.get("coordinated_edits") or []],
        "evidence_analysis": {
            "confirmed_cause": evidence.get("confirmed_cause", ""),
            "migration_assessment": solution.get("migration_assessment") or {},
        },
        "case_migration_policy": (solution.get("compatibility_policy") or {}).get(
            "case_migration_policy", {}),
    }
    stored = solution.get("stored_value_contract") or {}
    from cognalys.extraction.repository_index import build_repository_index
    index = build_repository_index(worktree, refresh=True)
    if stored.get("entries"):
        from cognalys.extraction.stored_value_contract import collect_stored_value_contract
        fresh = collect_stored_value_contract(worktree, {"persisted": True},
                                            index=index, seed_entries=stored["entries"])
        if fresh.get("status") != "complete":
            return [{"code": "STORED_VALUE_SCOPE_INCOMPLETE", "details": fresh.get("limitations", [])}]
        error["stored_value_contract"] = fresh
    gate_edits = [
        {
            "source_file_path": item["source_file"], "symbol": item.get("symbol", ""),
            "edit_kind": "test" if is_test_source(item["source_file"]) else "method",
            "original_code": item["original_code"], "corrected_code": item["corrected_code"],
            "reason": item.get("reason", ""),
        }
        for item in repair_edits
        if item.get("source_file") and item.get("corrected_code")
    ]
    # Porting and build repair run on a different checkout from adjudication.
    # Rebuild rename evidence from that checkout with the same assessment the
    # generation used: a stale record must neither reject a rename the
    # checkout allows nor accept one it refuses (bitbanana's port was refused
    # on a reason the patch itself resolved).
    from cognalys.llm.semantic_validation import detected_renames
    renames = {(item["source_file_path"], old) for item in gate_edits
               for old, _ in detected_renames(item["original_code"], item["corrected_code"])}
    if renames:
        from cognalys.extraction.semantic_index import build_semantic_index
        from cognalys.extraction.rename_safety import assess_rename_safety
        assessments = {}
        for path, symbol in sorted(renames):
            semantic = build_semantic_index(
                repo_root=worktree, source_cache=index["source_cache"],
                target_file=worktree / path, target_method=symbol, target_arity=None,
            )
            assessment = assess_rename_safety(worktree, path, symbol, semantic, index)
            if index.get("metadata", {}).get("truncated"):
                # Hard: an index that stopped early cannot have listed every
                # reference. Resolvable reasons (an overload family, calls the
                # resolution missed) only add references the port updates.
                assessment["allowed"] = False
                assessment.setdefault("hard_blockers", []).append("repository_index_truncated")
                assessment.setdefault("blocked_by", []).append("repository_index_truncated")
            assessments[f"{path}#{symbol}"] = assessment
        error["rename_safety_by_symbol"] = assessments
    try:
        errors = list(validate_coordinated_semantics(error, gate_edits).get("errors", []))
        # A rename check the draft itself left to review is not the port's
        # objection (without_rename_checks_left_to_review).
        from cognalys.llm.semantic_validation import without_rename_checks_left_to_review
        recorded = [*((solution.get("semantic_validation") or {}).get("warnings") or []),
                    *(solution.get("review_blockers") or [])]
        return without_rename_checks_left_to_review(error, recorded, errors, gate_edits)
    except Exception as exc:  # noqa: BLE001 -- a heuristic gate must not end the repair
        logging.getLogger("validation_fix").warning(
            "semantic gates failed on the repair; none applied: %s", exc)
        return []


def validate_and_apply(worktree: Path, generated: dict,
                       allowed_paths: set[str], dry_run: bool,
                       semantic_check=None,
                       widened_paths: dict[str, str] | None = None,
                       ) -> tuple[bool, list[dict], str]:
    """Check every edit of a repair and write them all, or none.

    `widened_paths` maps each declaration made editable beyond the diagnosed
    files (patch_consequence_declarations) to the reason; every edit of one
    carries that scope and reason, so the record, the commit and the PR step
    can tell it from an edit of a diagnosed file.
    """
    widened_paths = widened_paths or {}
    if generated.get("decision") != "REPAIR":
        return False, [], "model_abstained"
    edits = generated.get("edits", [])
    if not edits:
        return False, [], "empty_repair"
    snapshots: dict[Path, str] = {}
    updated: dict[Path, str] = {}
    normalized = []
    for edit in edits:
        raw_path = str(edit.get("source_file_path", ""))
        relative = Path(raw_path)
        if relative.is_absolute():
            # The model was given both a relative source_file_path and (until
            # normalized upstream) an absolute path for the same diagnostic;
            # tolerate an absolute path here as long as it resolves inside
            # this worktree and names an actually-diagnosed file, rather than
            # discarding an otherwise-correct repair over path formatting.
            try:
                relative = relative.resolve().relative_to(worktree.resolve())
            except ValueError:
                return False, [], f"repair_outside_diagnosed_files:{raw_path}"
        relative_text = relative.as_posix()
        if ".." in relative.parts or relative_text not in allowed_paths:
            return False, [], f"repair_outside_diagnosed_files:{relative_text}"
        target = (worktree / relative).resolve()
        try:
            target.relative_to(worktree.resolve())
        except ValueError:
            return False, [], f"unsafe_repair_path:{relative_text}"
        if not target.is_file():
            return False, [], f"repair_source_missing:{relative_text}"
        original = str(edit.get("original_code", ""))
        corrected = str(edit.get("corrected_code", ""))
        # An empty replacement deletes the original block, and removing a
        # wrong import line is often the whole repair: Gallery's repair was
        # rejected here for deleting `import ...commons.extensions.
        # ensureBackgroundThread`. What stays forbidden is an empty original,
        # a no-op, and emptying the entire file.
        if not original or original == corrected:
            return False, [], f"invalid_replacement:{relative_text}"
        corrected = match_trailing_line_break(original, corrected)
        content = updated.get(target)
        if content is None:
            content = read_source(target)
            snapshots[target] = read_exact(target)
        if not corrected and original.strip() == content.strip():
            return False, [], f"file_deletion_not_allowed:{relative_text}"
        # How many times the block occurs, as the edit declares it. Identical
        # lines are common in tests -- the same call repeated in several
        # methods -- and an exact-once contract could not express replacing
        # them: in run_20260914_141852 the repair merged five separate calls
        # into one block that occurred nowhere and was rejected as
        # "not unique". The count must match exactly, so a block the model
        # did not look at closely enough is still refused, and says why.
        try:
            expected = int(edit.get("occurrences", 1))
        except (TypeError, ValueError):
            return False, [], f"invalid_occurrences:{relative_text}"
        if expected < 1:
            return False, [], f"invalid_occurrences:{relative_text}"
        found = content.count(original)
        if found == 0:
            return False, [], f"original_not_found:{relative_text}"
        if found != expected:
            label = "original_not_unique" if expected == 1 else "occurrence_count_mismatch"
            return False, [], (
                f"{label}:{relative_text}:expected={expected}:found={found}"
            )
        updated[target] = content.replace(original, corrected)
        if _PATCH_TEST_PATH.search(relative_text):
            before = len(_TEST_CASE_ANNOTATION.findall(read_source(target)))
            after = len(_TEST_CASE_ANNOTATION.findall(updated[target]))
            if after < before:
                # Deleting @Test compiles, and the test task then passes by
                # running nothing. In run_20260929_104044 that is how
                # HomeApp's regression test was "repaired" and the pull
                # request still claimed test coverage. A test that cannot
                # compile is dropped by remove_patch_created_tests instead.
                return False, [], (
                    f"repair_removes_test_cases:{relative_text}:"
                    f"before={before}:after={after}"
                )
        normalized.append({
            "source_file": relative_text,
            "symbol": str(edit.get("symbol", "")),
            "edit_kind": "validation_repair",
            "original_code": original,
            "occurrences": expected,
            "corrected_code": corrected,
            "reason": str(edit.get("reason", "")),
            "original_code_sha256": hashlib.sha256(
                original.encode("utf-8")
            ).hexdigest(),
            **({"repair_scope": "patch_consequence_declaration",
                "repair_scope_reason": widened_paths[relative_text]}
               if relative_text in widened_paths else {}),
        })
    if semantic_check is not None:
        objections = semantic_check(normalized)
        if objections:
            # Nothing has been written. The rejection goes back to the model
            # with the rule that was broken, like any other application error.
            return False, [], "repair_violates_semantic_gates:" + "; ".join(
                f"{item.get('code', '')}: "
                + " | ".join(str(detail) for detail in item.get("details", []))[:300]
                for item in objections[:4]
            )
    if not dry_run:
        try:
            for target, content in updated.items():
                # Its own line endings: a CRLF file written back from the LF
                # text changed every line (2026-10-30).
                write_source(target, content)
        except Exception:
            for target, content in snapshots.items():
                write_exact(target, content)
            raise
    return True, normalized, ""


def patch_edits_of(solution: dict) -> list[dict]:
    """The patch's own edits, without a repair an earlier pass appended to them.

    A single-method row records its patch as source_file with
    original_method_body/corrected_method_body and no coordinated edits; its
    coordinated_edits hold only repair edits. Reading only coordinated_edits
    left such a patch invisible to the widened scope, and a re-review of its
    repaired patch would have judged the repair edits alone, never the
    method change (review of 2026-10-31).
    """
    edits = [edit for edit in solution.get("coordinated_edits") or []
             if isinstance(edit, dict) and edit.get("edit_kind") != "validation_repair"]
    original = str(solution.get("original_method_body", "") or "")
    corrected = str(solution.get("corrected_method_body", "") or "")
    source = str(solution.get("source_file", "") or solution.get("source_file_path", "") or "")
    if not edits and source and corrected.strip() and original != corrected:
        edits = [{"source_file": source, "symbol": str(solution.get("method_name", "")),
                  "original_code": original, "corrected_code": corrected,
                  "reason": "the patch's method change"}]
    return edits


def files_beyond_plan(planned_files, repair_edits: list[dict]) -> list[str]:
    """Files a repair edited through a widened scope that the plan did not include."""
    planned = {str(path).replace("\\", "/") for path in planned_files or []}
    return sorted({
        str(item.get("source_file", "")) for item in repair_edits or []
        if isinstance(item, dict) and item.get("repair_scope")
        and str(item.get("source_file", "")) not in planned
    })


def _file_at(repository: Path, commit: str, relative: str) -> str:
    import subprocess

    if not commit:
        return ""
    try:
        shown = subprocess.run(["git", "-C", str(repository), "show", f"{commit}:{relative}"],
                               capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return shown.stdout.decode("utf-8", errors="replace") if shown.returncode == 0 else ""


def review_repaired_patch(solution: dict, worktree: Path, provider, logger,
                          solutions_dir: Path) -> dict:
    """The behaviour review of the whole patch once a repair reached a new file.

    The review judged the coordinated draft before validation; a repair that
    also edits a declaration the plan did not include (BlueGate's
    Authenticator, 2026-10-30) changes code no review read. The same
    review_patch_behavior runs on every edit the pull request would carry,
    against the files as they were before the patch, with the finding's own
    evidence analysis from its solution file.
    """
    from cognalys.llm.coordinated_generation import CoordinatedGeneration
    from cognalys.llm.fix_artifacts import recorded_solution

    class _Reviewer(CoordinatedGeneration):
        def __init__(self):
            self.provider = provider
            self.logger = logger

    record = recorded_solution(
        solutions_dir, str(solution.get("apk_name", "")), str(solution.get("csv_class", "")),
        str(solution.get("method_name", "")), str(solution.get("spec", "")),
        patch_code=[str(edit.get("corrected_code", "")) for edit in patch_edits_of(solution)])
    base_commit = str(solution.get("base_commit", "") or "")
    base_repository = Path(str(solution.get("base_repository_path", "") or ""))
    # The review's helpers overlay the edits on the repository as it was
    # before the patch; the worktree already holds them.
    repository = base_repository if str(base_repository) not in ("", ".") \
        and base_repository.is_dir() else worktree
    if not record:
        # Without the plan's invariants, forbidden changes and the
        # adjudication's concerns the review would judge a weaker question
        # and still say "reviewed"; not running it leaves the blocker.
        logger.warning("behaviour review of the repaired patch: no solution record of "
                       "this patch under %s", solutions_dir)
        return {"regressions": [], "status": "not_run", "reason": "solution_record_not_found"}
    # The patch first (a single-method row's method change included), then
    # every repair edit, in the order they were applied.
    edits = [
        {"source_file_path": str(item.get("source_file") or item.get("source_file_path") or ""),
         **{key: item.get(key, "") for key in ("symbol", "original_code", "corrected_code", "reason")}}
        for item in [*patch_edits_of(solution),
                     *[edit for edit in solution.get("coordinated_edits") or []
                       if isinstance(edit, dict) and edit.get("edit_kind") == "validation_repair"]]
        if item.get("original_code") or item.get("corrected_code")
    ]
    source_by_path = {}
    for path in dict.fromkeys(edit["source_file_path"] for edit in edits):
        source_by_path[path] = _file_at(worktree, base_commit, path)
    error = {
        **{key: solution.get(key, "") for key in (
            "spec", "error_message", "csv_class", "method_name", "tool", "apk_name",
            "detector_context", "contribution_base", "joint_findings")},
        "source_file_path": solution.get("source_file") or solution.get("source_file_path", ""),
        "evidence_analysis": record.get("evidence_analysis") or {},
        "stored_value_contract": solution.get("stored_value_contract") or {},
        "repository_path": str(repository),
    }
    try:
        return _Reviewer().review_patch_behavior(error, edits, source_by_path)
    except Exception as exc:  # noqa: BLE001 -- recorded as not run, which blocks
        # The review now raises a provider that failed to answer
        # (ProviderTransientFailure) instead of returning not_run; this step
        # runs outside process_finding, so it is recorded here as not run,
        # with that reason, which blocks as any review not run does.
        logger.warning("behaviour review of the repaired patch failed: %s", exc)
        review = {"regressions": [], "status": "not_run"}
        if isinstance(exc, ProviderTransientFailure):
            review["reason"] = "provider_unavailable"
        return review


def rename_branch_for_attribution(solution: dict, worktree: Path) -> str:
    """Name the worktree's branch after the finding the pull request is attributed to.

    Review of batch 13 (2026-10-31): an attribution found only by the review
    of a repaired patch (record_repair_review) came after the worktree branch
    was named for the reported finding, so the branch kept
    fix/<method>-<reported spec>-... while the pull request's spec moved.
    Only the method-and-spec part changes; the finding id and revision stay.
    On any failure the branch keeps its name (only the name is wrong, never
    the patch). Returns the branch the row now names.
    """
    import subprocess

    from cognalys.solutions.worktree_manager import WorktreeManager

    branch = str(solution.get("branch", "")).strip()
    if not branch or not solution.get("pr_attribution"):
        return branch
    old_method, old_spec = WorktreeManager.branch_method_and_spec(
        {key: value for key, value in solution.items() if key != "pr_attribution"})
    method, spec = WorktreeManager.branch_method_and_spec(solution)
    prefix = f"fix/{old_method}-{old_spec}-"
    if (old_method, old_spec) == (method, spec) or not branch.startswith(prefix):
        return branch
    renamed = f"fix/{method}-{spec}-" + branch[len(prefix):]
    try:
        subprocess.run(["git", "-C", str(worktree), "branch", "-m", branch, renamed],
                       capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        logging.getLogger("validation_fix").warning(
            "branch %s kept its name: %s", branch, getattr(exc, "stderr", "") or exc)
        return branch
    solution["branch"] = renamed
    return renamed


def unit_left_without_effect(solution: dict, review: dict) -> bool:
    """Whether the repaired patch's review, with the draft's per-finding verdicts,
    leaves every finding of the unit without effect
    (coordinated_generation.every_finding_without_effect)."""
    from cognalys.llm.coordinated_generation import every_finding_without_effect
    from cognalys.utils.pr_text import merged_joint_without_effect

    reported = review.get("reported_finding_without_effect") or (
        (solution.get("pr_attribution") or {}).get("reported_finding") or {})
    return every_finding_without_effect({
        "status": review.get("status"),
        "reported_finding_without_effect": reported,
        "joint_findings_without_effect": merged_joint_without_effect(
            review.get("joint_findings_without_effect"),
            solution.get("joint_findings_without_effect")),
        "joint_finding_count": max(int(review.get("joint_finding_count") or 0),
                                   int(solution.get("joint_finding_count") or 0)),
    })


def record_repair_review(solution: dict, review: dict, files: list[str]) -> None:
    """Make the repaired patch's review the one publication reads.

    publication_after_validation reads the review from the solution's
    publication_blocker_record: regressions or no effect there block the pull
    request, and a review that did not run leaves a blocker of its own, so a
    file no review read is never published on the strength of the draft's
    earlier review. The summary was written for the planned files, so it is
    marked stale: the PR step rewrites it from the final edits.
    """
    from cognalys.llm.fix_artifacts import REVIEWED_STATUSES

    status = str(review.get("status", "") or "not_run")
    blocker_record = solution.get("publication_blocker_record")
    if not isinstance(blocker_record, dict):
        blocker_record = solution["publication_blocker_record"] = {}
    blocker_record["behavior_review"] = {
        "status": status,
        "review_contract_version": review.get("review_contract_version"),
        "regression_count": len(review.get("regressions") or []),
        "patch_has_no_effect": bool(review.get("patch_has_no_effect")
                                    and not review.get("explicit_default_only")),
    }
    if status in REVIEWED_STATUSES and review.get("review_contract_version") is not None:
        solution["review_contract_version"] = review["review_contract_version"]
    else:
        blocker_record.setdefault("blockers", []).append({
            "category": "repair_scope_unreviewed",
            "kind": "behaviour review of the repaired patch did not run",
            "description": (
                "The build repair also edited " + ", ".join(files) + ", which the plan "
                "did not include, and no behaviour review judged the repaired patch."),
            "discharged_by_pipeline_validation": False,
        })
    solution["summary_stale"] = True
    solution["repair_behavior_review"] = {"files_beyond_plan": list(files), **review}
    if status in REVIEWED_STATUSES and review.get("joint_findings_without_effect"):
        # Review of 2026-10-31: kept only here, the repaired patch's per-finding
        # verdict reached neither the PR text check (pr_generator) nor the
        # statistics, which read the draft's review.
        from cognalys.utils.pr_text import merged_joint_without_effect

        solution["joint_findings_without_effect"] = merged_joint_without_effect(
            solution.get("joint_findings_without_effect"),
            [{key: item.get(key, "") for key in ("method", "specification", "reason")}
             for item in review["joint_findings_without_effect"] if isinstance(item, dict)])
        solution["joint_finding_count"] = max(int(solution.get("joint_finding_count") or 0),
                                              int(review.get("joint_finding_count") or 0))
    if status in REVIEWED_STATUSES and unit_left_without_effect(solution, review):
        # Every finding of the unit without effect, the draft's verdicts with
        # the repaired patch's (review of batch 13, 2026-10-31): the patch has
        # no effect as a whole and is not published for any of them.
        blocker_record["behavior_review"]["patch_has_no_effect"] = True
        review = {**review, "no_effect_reason": str(review.get("no_effect_reason", "")).strip()
                  or "no finding of this unit is resolved by the repaired patch"}
    if (status in REVIEWED_STATUSES and isinstance(review.get("pr_attribution"), dict)
            and review["pr_attribution"] and not solution.get("pr_attribution")
            and not blocker_record["behavior_review"]["patch_has_no_effect"]):
        # The repaired patch leaves the reported finding as it was too
        # (LocalSend run_v4, 2026-10-31): its pull request is attributed to the
        # joint findings it affects. A draft already attributed keeps that;
        # a build repair never makes the reported finding's share effective.
        solution["pr_attribution"] = review["pr_attribution"]
    # The counts above decide publication; the operator reads the texts.
    # Only the counts were kept at first (review of 2026-10-31), so a
    # regression scenario or a maintainer question about the repaired patch
    # never reached the PR notes pr_generator.review_notes assembles.
    scope = "the repaired patch (" + ", ".join(files) + ")"
    blockers = [f"Behaviour review of {scope}: {str(item.get('scenario', '')).strip()}"
                for item in review.get("regressions") or []
                if isinstance(item, dict) and str(item.get("scenario", "")).strip()]
    if blocker_record["behavior_review"]["patch_has_no_effect"]:
        blockers.append(f"Behaviour review of {scope}: no effect -- "
                        + str(review.get("no_effect_reason", "")).strip())
    considerations = ([f"Behaviour review of {scope}: "
                       + str(review.get("maintainer_decision", "")).strip()]
                      if review.get("maintainer_decision_required")
                      and str(review.get("maintainer_decision", "")).strip() else [])
    solution["repair_review_notes"] = {"review_blockers": blockers,
                                       "publication_considerations": considerations}


REPAIR_SYSTEM_PROMPT = (
    "You are a build-repair stage, not a security-analysis stage. Apply "
    "minimal exact replacements in diagnosed files and preserve behavior. "
    "The diagnostics are compiler errors, failing test assertions, or both."
)

_READ_ONLY_CONSTRAINT = (
    "Edit only diagnosed source files; supporting declarations are read-only.")
# Said only when a declaration was made editable, so every other repair
# request stays as it was.
_WIDENED_CONSTRAINTS = [
    "Edit only diagnosed source files and the supporting declarations marked "
    "editable; editable_reason says why each one is. Every other supporting "
    "declaration is read-only.",
    "An editable declaration is changed minimally so that it accepts the type "
    "or arguments the corrected code now passes -- its parameter, property or "
    "return type, and its uses inside that file -- and nothing else in it. "
    "Never satisfy the old declaration by building the old type or value at "
    "the call site or anywhere else (wrapping the new value back into what the "
    "correction removed): that reverses the security correction.",
]


def repair_request(worktree: Path, solution: dict) -> dict:
    """The repair prompt's payload and what the repair may edit.

    Returns {"payload", "diagnostics", "diagnosed_sources", "allowed",
    "widened"}: `allowed` is every path an edit may name and `widened` maps
    each declaration made editable beyond the diagnosed files to its reason.
    """
    finding_id = str(solution.get("finding_id", "unknown"))
    validation = solution.get("build_validation") or {}
    # A failing assertion is diagnosed exactly like a compiler error:
    # same record shape, same diagnosed source, same exact-replacement
    # contract. Keeping them in one list is what lets a compile-clean
    # run whose test fails be repaired at all.
    diagnostics, diagnosed_sources = diagnosed_context(
        worktree,
        list(validation.get("compiler_diagnostics", []))
        + list(validation.get("failing_tests", [])),
    )
    diagnosed = set(diagnosed_sources)
    supporting = supporting_declarations(
        worktree,
        # The message often names the type more plainly than the code
        # does: "constructor Schedule in class Schedule cannot be
        # applied to given types" identifies exactly the declaration
        # the repair needs to read.
        [str(item.get("source_line", "")) for item in diagnostics]
        + [str(item.get("message", "")) for item in diagnostics]
        # A failing assertion names no type at all: the diagnostic is
        # "equalItemsHaveEqualHashCodes failed: AssertionError" over
        # "assertEquals(first, second);". The types the repair must
        # reason about are declared in the diagnosed file itself, so
        # its own text is a name source too. The search below is a
        # single tree walk whatever the number of names.
        + list(diagnosed_sources.values()),
        diagnosed,
    )
    base_commit = str(solution.get("base_commit", "") or "")
    base_repository = Path(str(solution.get("base_repository_path", "") or ""))

    def base_source(relative: str) -> str:
        # The worktree shares the clone's objects; the clone is the fallback.
        return _file_at(worktree, base_commit, relative) or (
            _file_at(base_repository, base_commit, relative)
            if str(base_repository) not in ("", ".") and base_repository.is_dir() else "")

    editable = patch_consequence_declarations(
        worktree, diagnostics, diagnosed, patch_edits_of(solution), base_source,
        planned_files=solution.get("changed_files") or [])
    widened = {item["source_file_path"]: item["editable_reason"] for item in editable}
    # First, so the cap on supporting declarations never drops one the
    # repair may edit.
    supporting = editable + [item for item in supporting
                             if item["source_file_path"] not in widened]
    constraints = [
        "Repair every supplied diagnostic.",
        "When a symbol is unresolved and "
        "repository_imports_for_unresolved_symbols shows how this "
        "repository imports it, correct the import to that line rather "
        "than replacing the API the correction chose.",
        "A diagnostic naming a test that FAILED is a failing assertion, "
        "not a compile error: the code builds and the assertion is "
        "wrong. Asserting equality between two separately constructed "
        "values of a type that declares no equals(Object) compares "
        "references and can never pass; assert on the observable "
        "fields, or on the same instance, instead.",
        "Each edit's original_code is one contiguous block copied "
        "verbatim from diagnosed_sources"
        + (" or from an editable supporting declaration" if widened else "")
        + ", including its indentation "
        "and line breaks. Never join separate locations into one "
        "original_code. When the identical block occurs several times "
        "and every occurrence needs the same change, set occurrences "
        "to that exact count; otherwise include enough surrounding "
        "lines to make the block unique and set occurrences to 1.",
        *(_WIDENED_CONSTRAINTS if widened else [_READ_ONLY_CONSTRAINT]),
        "Preserve test intent and the security correction.",
        "Use current repository APIs only; do not invent compatibility shims.",
        "Do not delete files or tests and do not perform unrelated cleanup.",
        # BlueGate run_v8 (2026-10-31): a rewritten block lost the
        # maintainer's commented-out alternative (utils.removed_comments).
        COMMENT_PRESERVATION_GUIDANCE.strip(),
    ]
    payload = {
        "finding_id": finding_id,
        "original_security_correction": solution.get("explanation", ""),
        "compiler_diagnostics": diagnostics,
        "diagnosed_sources": [
            {"source_file_path": path, "content": content}
            for path, content in diagnosed_sources.items()
        ],
        "supporting_current_api_sources": supporting,
        "repository_imports_for_unresolved_symbols": unresolved_symbol_imports(
            worktree, diagnostics, diagnosed,
        ),
        # Import lines an earlier passing build of this repository
        # contained (data/pipeline_learning).
        "verified_imports_from_previous_runs": _verified_imports(
            str(solution.get("apk_name", ""))
        ),
        "constraints": constraints,
    }
    return {
        "payload": payload, "diagnostics": diagnostics,
        "diagnosed_sources": diagnosed_sources,
        "allowed": diagnosed | set(widened), "widened": widened,
    }


def repair_prompt(payload: dict) -> str:
    return (
        "Repair the supplied validation failures. Return JSON matching the schema.\n\n"
        + prompt_json(payload)
        + "\n\nSCHEMA:\n" + prompt_json(repair_schema())
    )


def main() -> int:
    args = parse_args()
    report = json.loads(args.execution_report.read_text(encoding="utf-8"))
    candidates = []
    for solution in report.get("per_solution_details", []):
        validation = solution.get("build_validation", {}) or {}
        if (solution.get("status") == "SUCCESS"
                and (not args.project or solution.get("apk_name") == args.project)
                and validation.get("status") != "success"
                and validation.get("failure_attribution") in REPAIRABLE_ATTRIBUTIONS
                and (validation.get("compiler_diagnostics")
                     or validation.get("failing_tests"))):
            candidates.append(solution)
    if args.limit > 0:
        candidates = candidates[:args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not candidates:
        print("Validation fix: no repairable compiler diagnostics "
              "or failing tests")
        return 0
    logger = logging.getLogger("validation_fix")
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())
    provider = remote_provider(
        openai_class=OpenAIProvider,
        endpoint=args.endpoint, model=args.model, timeout=args.timeout,
        max_output_tokens=args.max_output_tokens,
        reasoning_effort=args.reasoning_effort,
        cache_dir=args.output_dir / ".llm_cache", logger=logger,
        prompt_log_dir=args.prompt_log_dir,
    )
    repaired = 0
    failed = 0
    for solution in candidates:
        finding_id = str(solution.get("finding_id", "unknown"))
        worktree = Path(str(solution.get("worktree_path", ""))).resolve()
        request_parts = repair_request(worktree, solution)
        diagnosed_sources = request_parts["diagnosed_sources"]
        allowed = request_parts["allowed"]
        widened = request_parts["widened"]
        if widened:
            logger.warning(
                "%s: the patch's change reaches declarations outside the diagnosed "
                "files; the repair may adapt them: %s", finding_id, ", ".join(sorted(widened)))
        schema = repair_schema()
        request = repair_prompt(request_parts["payload"])
        system = REPAIR_SYSTEM_PROMPT + (
            " A supporting declaration marked editable may also be edited, minimally."
            if widened else "")
        trace_context = {
            "finding_id": finding_id,
            # v3: existing comments the repair does not make false are kept
            # (BlueGate run_v8, 2026-10-31). v4: a TODO the repair resolves
            # may go (review of 2026-10-31).
            "prompt_version": "validation-fix-v4",
            "schema_version": "2.0",
            "policy_version": "2.0",
        }
        response = provider.generate(
            request, system, json_mode=True, json_schema=schema,
            schema_name="validation_compiler_repair",
            trace_stage="validation_fix.compiler_repair",
            trace_context=trace_context,
        )
        generated = ResponseParser.parse_json_object(response) or {}

        def semantic_check(repair_edits, _worktree=worktree, _solution=solution):
            return semantic_regressions(_worktree, _solution, repair_edits)

        ok, edits, error = validate_and_apply(
            worktree, generated, allowed, args.dry_run, semantic_check, widened
        )
        # A right repair in the wrong shape used to end the step: nothing is
        # written until every edit applies, so the rejected answer and the
        # exact application error go back for a bounded correction.
        rejected_attempts = []
        for _ in range(MAX_REPAIR_CORRECTIONS):
            if ok or error == "model_abstained":
                break
            rejected_attempts.append({"generated": generated, "error": error})
            response = provider.generate(
                request
                + "\n\nYOUR PREVIOUS REPAIR WAS REJECTED BEFORE ANY FILE CHANGED:\n"
                + json.dumps(
                    {"rejected_repair": generated, "application_error": error},
                    indent=2, ensure_ascii=False,
                )
                + "\n\nReturn a corrected repair that resolves the application "
                "error. Every original_code must be one contiguous block copied "
                "verbatim from diagnosed_sources"
                + (" or an editable supporting declaration" if widened else "")
                + " and occur in its file exactly "
                "`occurrences` times. When the error names a rule "
                "(repair_violates_semantic_gates: CODE), the repair must satisfy "
                "it: do not reintroduce the construct the rule rejects.",
                system, json_mode=True, json_schema=schema,
                schema_name="validation_compiler_repair",
                trace_stage="validation_fix.compiler_repair.correction",
                trace_context=trace_context,
            )
            generated = ResponseParser.parse_json_object(response) or {}
            ok, edits, error = validate_and_apply(
                worktree, generated, allowed, args.dry_run, semantic_check, widened
            )
        removed_tests: list[str] = []
        if not ok:
            # The one repair this stage may make without the model: drop the
            # tests the patch itself created, when they are the only thing
            # failing. Every diagnostic is in a file the base commit does not
            # have, so nothing pre-existing is touched and the security fix
            # -- which compiles -- is not lost with them. einkbro's
            # restoreLegacyBackupData patch was a correct production change
            # whose generated test imported a runner the module never
            # declared; the model rightly abstained, because the only honest
            # code repair was a build-file edit the finding does not
            # authorise, and the whole finding died with it.
            removed_tests = remove_patch_created_tests(
                worktree,
                Path(str(solution.get("base_repository_path", "")) or "."),
                str(solution.get("base_commit", "")),
                sorted(diagnosed_sources),
                dry_run=args.dry_run,
            )
            if removed_tests:
                ok = True
                error = ""
                edits = [
                    {"source_file": path, "change": "removed_generated_test"}
                    for path in removed_tests
                ]
                logger.warning(
                    "%s: removed %d generated test file(s) that nothing on "
                    "this module's test classpath could compile; the "
                    "production fix stands and the tests it required are a "
                    "reviewer checklist: %s",
                    finding_id, len(removed_tests), ", ".join(removed_tests),
                )
        artifact = {
            "finding_id": finding_id, "worktree_path": str(worktree),
            "generated": generated, "applied_edits": edits,
            "rejected_attempts": rejected_attempts,
            "removed_generated_tests": removed_tests,
            "status": "dry_run" if args.dry_run and ok else "success" if ok else "failed",
            "error": error,
            # Each declaration the repair was allowed to edit beyond the
            # diagnosed files, with the reason (patch_consequence_declarations).
            "editable_declarations": [
                {"source_file_path": path, "reason": reason}
                for path, reason in sorted(widened.items())],
        }
        artifact_path = args.output_dir / f"{finding_id}_validation_fix.json"
        if ok and not args.dry_run:
            commit_hash = commit_repair(
                worktree, sorted({item["source_file"] for item in edits}), finding_id
            )
            artifact["commit_hash"] = commit_hash
            if removed_tests:
                # Not appended as edits: a removal entry kept the test's path
                # in coordinated_edits, so the PR text still announced the
                # test and revalidation rebuilt its module's test task.
                record_removed_tests(
                    solution, removed_tests,
                    "The generated test for this fix did not compile against "
                    "this module's declared test dependencies and was removed "
                    "rather than rewritten.",
                )
            else:
                beyond_plan = files_beyond_plan(solution.get("changed_files"), edits)
                solution.setdefault("coordinated_edits", []).extend(edits)
                solution["changed_files"] = list(dict.fromkeys([
                    *solution.get("changed_files", []),
                    *[item["source_file"] for item in edits],
                ]))
                if beyond_plan:
                    # Reviewed before revalidation and the PR step read it.
                    review = review_repaired_patch(
                        solution, worktree, provider, logger,
                        args.execution_report.resolve().parent.parent / "error_solutions")
                    record_repair_review(solution, review, beyond_plan)
                    # An attribution the repaired patch's review found renames
                    # the branch too (review of batch 13, 2026-10-31).
                    rename_branch_for_attribution(solution, worktree)
                    artifact["files_beyond_plan"] = beyond_plan
                    artifact["behavior_review_after_repair"] = review
                    logger.warning(
                        "%s: the repair edited %s beyond the plan; behaviour review of "
                        "the repaired patch: %s, %d regression(s)", finding_id,
                        ", ".join(beyond_plan), review.get("status", "not_run"),
                        len(review.get("regressions") or []))
            solution["commit_hash"] = commit_hash
            solution["validation_fix"] = {
                "status": "success", "artifact": str(artifact_path.resolve()),
                "commit_hash": commit_hash,
            }
            repaired += 1
        elif ok:
            repaired += 1
        else:
            failed += 1
            solution["validation_fix"] = {
                "status": "failed", "error": error,
                "artifact": str(artifact_path.resolve()),
            }
        artifact_path.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    shape_issues = validate_execution_report(report)
    if shape_issues:
        print(
            "execution_report.json shape check found issues: "
            + "; ".join(shape_issues),
            file=sys.stderr,
        )
    write_json_atomic(args.execution_report, report)
    print(f"Validation fix: {repaired} repaired, {failed} failed")
    # A solution that already compiled went through this step as much as a
    # repaired one. Counting only repairs made Bluegate's one failed repair
    # (2026-10-22) end the run before its compiled fix reached the PR step.
    from cognalys.utils.step_outcome import step_exit_code

    compiled = sum(
        1 for entry in report.get("per_solution_details", []) or []
        if isinstance(entry, dict)
        and str((entry.get("build_validation") or {}).get("status", "")).startswith("success")
    )
    return step_exit_code(repaired + compiled, failed)


if __name__ == "__main__":
    raise SystemExit(main())
