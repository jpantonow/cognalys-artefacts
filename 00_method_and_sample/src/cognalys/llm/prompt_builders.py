#!/usr/bin/env python3
"""
System and user prompt construction for the fix-generation pipeline.

Extracted from generate_error_fixes.py (Passo 3 of the
modularization plan). These were methods of FixGenerator but held
no orchestrator state.

The v1 baseline prompts that also lived here (build_prompt and
build_evidence_prompt, 550 lines between them) were removed with the v1
pipeline: they asked one model call to produce the whole legacy analysis
shape, which the staged pipeline replaced with separate research, extraction,
adjudication and planning calls.
"""
from __future__ import annotations

import re
from typing import Dict, Tuple

from cognalys.llm.fix_artifacts import compact_evidence_context
from cognalys.llm.fix_generation_common import (
    COMMENT_PRESERVATION_GUIDANCE, JOINT_FINDING_EFFECT_GUIDANCE, RUNTIME_MONITOR_GUIDANCE,
    SAME_PARTS_CARRIER_GUIDANCE, finding_detector,
)
from cognalys.llm.pipeline_common import prompt_json
from cognalys.llm.pipeline_architecture import (
    POLICY_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    case_scoped_specification_context,
    classify_spec_family,
    governed_api_types,
    non_literal_getinstance_arguments,
)


# A declaration line is what the model needs in order to call another file's
# API without guessing: the type headers and the member signatures. Control
# statements and ordinary calls also carry parentheses, so they are excluded
# by keyword rather than by trying to parse Java properly here.
_CONTROL_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "else", "do",
    "try", "synchronized", "assert", "throw", "new", "super", "this",
    "case", "break", "continue", "yield", "when",
})
_TYPE_HEADER = re.compile(
    r"^[ \t]*(?:(?:public|protected|private|internal|static|final|abstract"
    r"|sealed|non-sealed|open|data|inner|companion)\s+)*"
    r"(?:class|interface|enum|record|object|@interface)\s+[A-Za-z_$]"
)
_MEMBER_SIGNATURE = re.compile(
    r"^[ \t]*(?:@\w+[^\s]*\s+)*"
    r"(?:(?:public|protected|private|internal|static|final|abstract|native"
    r"|synchronized|strictfp|default|open|override|suspend|operator)\s+)*"
    # A generic method writes its type parameters between the modifiers and
    # the return type: `public static <T extends Fragment> TestRule
    # forFragment(...)`. Without this the whole declaration was dropped from
    # the outline, and a caller of it looked like it was inventing an API.
    r"(?:<[^<>]*(?:<[^<>]*>)?[^<>]*>\s*)?"
    # The head is the return type, if the member declares one. It may not
    # contain a bare space, so it cannot compete with the separator below
    # for the same characters -- that ambiguity is what makes this kind of
    # pattern backtrack super-linearly on a long line.
    r"(?:(?P<head>[\w$.?]+(?:<[^<>]*>)?(?:\[\])*)\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^()]*)\)"
    r"\s*(?:throws\s[\w$., ]+)?\s*(?P<tail>[{;]|=|$)"
)


def _is_declaration(match) -> bool:
    """Reject the ordinary calls that also look like a signature.

    `line.addItem(item);` matches the signature shape, so two things rule
    it out: a qualified head (`obj.`) is never a declaration, and a
    signature with no return type or modifier at all is only a constructor
    when a body opens right after it.
    """
    head = (match.group("head") or "").strip()
    if "." in head:
        return False
    if not head and match.group("tail") != "{":
        return False
    return True


def declared_api_surface(source: str, *, max_lines: int = 250) -> list:
    """Outline the callable API a Java/Kotlin source declares.

    Returns the type headers, constructors and member signatures as trimmed
    source lines, in file order, so a sibling file in the same coordinated
    batch can call them instead of inventing them. It is a line scanner
    rather than a parser, but a parameter list wrapped across lines is
    joined before matching: real code wraps long signatures constantly, and
    dropping them understates the API a caller may legitimately use.
    """
    lines = []
    for raw in _joined_declaration_lines(source):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("//", "*", "/*", "@Override")):
            continue
        first_word = re.match(r"[A-Za-z_$]+", stripped)
        if first_word and first_word.group(0) in _CONTROL_KEYWORDS:
            continue
        if _TYPE_HEADER.match(raw):
            lines.append(stripped.rstrip("{").strip())
        else:
            match = _MEMBER_SIGNATURE.match(raw)
            if not match or not _is_declaration(match):
                continue
            lines.append(re.sub(r"\s*[{;=].*$", "", stripped).strip())
        if len(lines) >= max_lines:
            lines.append("... (API outline truncated)")
            break
    return lines


def _joined_declaration_lines(source: str):
    """Yield source lines with a wrapped parameter list joined onto one.

    `public static void closeFile(@NonNull ActivityScenario<X> scenario,`
    continues on the next line, and a per-line matcher sees neither half as
    a signature. Joining while the parentheses stay open makes the whole
    declaration visible without parsing the language.
    """
    pending = ""
    for raw in source.splitlines():
        candidate = f"{pending} {raw.strip()}" if pending else raw
        opened = candidate.count("(") - candidate.count(")")
        if opened > 0 and candidate.count("(") and len(candidate) < 600:
            pending = candidate.rstrip()
            continue
        pending = ""
        yield candidate
