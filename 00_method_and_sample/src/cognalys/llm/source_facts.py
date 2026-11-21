"""Facts read from the reviewed source, its bytecode and its build: call
sequences, argument origins, sinks, shared state, manifest components.

Moved verbatim out of pipeline_architecture.py, which re-exports every
name so existing imports keep working.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from xml.etree import ElementTree
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Importable with only src/llm on the path, or as llm.<module>: src/ for the
# utils package, and this folder for the sibling modules imported by name.

from cognalys.utils.disposition import GENERATING_DECISIONS  # noqa: E402,F401
from cognalys.utils.finding_source import (  # noqa: E402,F401
    ADVISORY_IDENTIFIERS,
    advisory_identifiers,
)

from cognalys.llm.pipeline_common import (  # noqa: E402
    _normalised,
    canonical_hash,
    reported_violation,
    specification_shapes_are_grounded,
    violation_rule_section,
)


VENDORED_PATH_SEGMENTS = {
    "thirdparty", "third_party", "third-party", "3rdparty", "vendor",
    "vendored",
}


VENDORED_HEADER = re.compile(
    r"(?im)^[^\n]*?\b(copied|vendored|forked|imported|taken|adapted)\s+from\b"
    r"[^\n]*$"
)


VENDORED_UPSTREAM_URL = re.compile(r"https?://[^\s*]+")


def vendored_source_assessment(source_file_path: str, content: str) -> dict:
    """Whether the reported file is a copy of code another project owns.

    markor's JavaPasswordbasedCryption lives under ``app/thirdparty/`` beneath
    a ``COPIED FROM https://gitlab.com/opensource21/jpencconverter/...`` header,
    and its PBKDF2 work factor is a decision that belongs to that upstream
    project. Both facts were inside the evidence packet and neither reached the
    adjudication, which reasoned about the change purely as a compatibility and
    policy question and never noted that a pull request would be opened against
    a repository that only carries the file.

    This is a publication signal, not a correctness one: vendored code can
    still be wrong, and a fork sometimes has to be patched locally. It is
    reported so the adjudication can weigh where the change belongs, never as
    an automatic block.

    Returns an empty dict when nothing indicates a vendored copy, so a packet
    for ordinary application code is unchanged.
    """
    signals = []
    segments = {
        segment.lower() for segment in re.split(r"[\\/]+", source_file_path)
    }
    vendored_segments = sorted(segments & VENDORED_PATH_SEGMENTS)
    if vendored_segments:
        signals.append(
            "path is under a vendored directory: "
            + ", ".join(vendored_segments)
        )
    upstream = ""
    header = VENDORED_HEADER.search("\n".join(content.splitlines()[:120]))
    if header:
        line = header.group(0).strip()
        signals.append("source header states: " + line[:300])
        url = VENDORED_UPSTREAM_URL.search(line)
        if url:
            upstream = url.group(0).rstrip(".,;)")
    if not signals:
        return {}
    return {
        "vendored": True,
        "signals": signals,
        "upstream": upstream,
        "publication_consideration": (
            "The reported file appears to be a copy of code maintained "
            "elsewhere. A change to it may belong upstream rather than in "
            "this repository, and a pull request here may be declined or may "
            "be reverted by the next sync. Weigh this when deciding "
            "publication_status; it is not by itself a reason to abstain."
        ),
    }


def target_source_completeness(catalog: list[dict]) -> str:
    """Whether the packet holds the reported target's code.

    It used to say "complete" whenever the catalog held anything at all, and
    the target's own entry is always in the catalog, empty or not: gallery's
    ThumbnailLoader and kiwix's ZIMFileReader in run_20260914_122712 went to
    the adjudicator as target_source "complete" beside a target_method whose
    content was the empty string.
    """
    targets = [
        item for item in catalog
        if item.get("kind") in {"target_source", "target_method"}
    ]
    if any(str((item.get("data") or {}).get("content", "")).strip()
           for item in targets):
        return "complete"
    return "missing" if targets else "unknown"


# Coordinate tokens too common to say which library a message is about.
_GENERIC_COORDINATE_TOKENS = frozenset({
    "android", "androidx", "core", "common", "commons", "library", "java",
    "kotlin", "google", "github", "gitlab", "jitpack", "app", "sdk", "api",
    "simple", "utils", "util", "base", "support",
})


def message_names_dependency(message: str, dependency: dict | None) -> bool:
    """Whether a finding's message names the given declared dependency.

    A dependency matched to a class by name says where the class might come
    from; only the message says what the finding is about. An outdated OkHttp
    reported at an application class names okhttp, and is fixed in the build
    file. An OutOfMemoryError reported at an application class names no
    library, whatever declared artifact happens to share the application's
    organisation name. Word-bounded, over the artifact and the group's last
    segment and their parts, minus tokens common to every coordinate.
    """
    text = str(message or "").lower()
    dependency = dependency or {}
    tokens: set[str] = set()
    for value in (
        str(dependency.get("artifact", "")),
        str(dependency.get("group", "")).rsplit(".", 1)[-1],
    ):
        value = value.strip().lower()
        if not value:
            continue
        tokens.update({value, value.replace("-", "."), value.replace("-", "")})
        tokens.update(re.split(r"[-_.]", value))
    return any(
        len(token) >= 4 and token not in _GENERIC_COORDINATE_TOKENS
        and re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", text)
        for token in tokens
    )


_EVIDENCE_ID_PREFIX = re.compile(
    r"^\s*Evidence IDs:\s*(?P<ids>[A-Z]+-\d+(?:\s*,\s*[A-Z]+-\d+)*)?\s*\.\s*"
)


_QUOTED_LITERAL = re.compile(r'"([^"\n]{3,120})"')


_FALSE_POSITIVE_WORDS = re.compile(r"false[\s-]positive", re.IGNORECASE)


def analysis_dismissal_texts(analysis: dict | None) -> list[str]:
    """The sentences in which an analysis explains what it dismissed."""
    record = analysis or {}
    texts = [str(record.get("confirmed_cause", ""))]
    texts.extend(str(item) for item in record.get("review_assumptions") or [])
    for key in ("blocking_for_generation", "blocking_for_publication",
                "unresolved_relations"):
        for item in record.get(key) or []:
            if isinstance(item, dict):
                texts.append(str(item.get("description", "")))
    return texts


def _documented_shape_literals(specification_context: dict | None) -> set[str]:
    """Quoted values the specification's grounded false-positive shapes name."""
    if not specification_shapes_are_grounded(specification_context):
        return set()
    return {
        literal.lower()
        for shape in (specification_context or {}).get("known_false_positive_shapes") or []
        if isinstance(shape, str)
        for literal in _QUOTED_LITERAL.findall(shape)
    }


def false_positive_dismissal_literals(texts: Iterable[str],
                                      specification_context: dict | None) -> set[str]:
    """Quoted values this analysis dismissed as a documented false positive.

    Both halves are required. The specification's own grounded shapes must
    quote the value, and a sentence of this analysis that calls something a
    false positive must quote it too. Neither alone is enough: the shape
    without the analysis would dismiss by pattern, and the analysis without
    the shape would dismiss on the model's say-so.
    """
    documented = _documented_shape_literals(specification_context)
    if not documented:
        return set()
    dismissed: set[str] = set()
    for text in texts or []:
        for sentence in re.split(r"(?<=[.;])\s+", str(text or "")):
            if _FALSE_POSITIVE_WORDS.search(sentence):
                dismissed.update(
                    literal.lower() for literal in _QUOTED_LITERAL.findall(sentence)
                )
    return documented & dismissed


_CALL_NAME = re.compile(r"\b([A-Za-z_]\w*)\(")
_NULL_VALUE = re.compile(r"\bnull\b", re.IGNORECASE)


def _calls_and_values(text: str) -> tuple[set[str], set[str]]:
    """The call names and literal values a sentence about code mentions."""
    text = str(text or "")
    values = {literal.lower() for literal in _QUOTED_LITERAL.findall(text)}
    if _NULL_VALUE.search(text):
        values.add("null")
    return set(_CALL_NAME.findall(text)), values


def settled_value_texts(adjudication: dict | None) -> list[str]:
    """The sentences in which an adjudication judged the reported code correct use.

    Only when it did (current_code_is_correct_use): the reason and the
    confirmed cause then both describe the value that was judged, and
    nothing else.
    """
    record = adjudication if isinstance(adjudication, dict) else {}
    if record.get("current_code_is_correct_use") is not True:
        return []
    return [
        text for text in (str(record.get("correct_use_reason", "")).strip(),
                          str(record.get("confirmed_cause", "")).strip())
        if text
    ]


_TYPE_NAME = re.compile(r"(?<![\w$])([A-Z][A-Za-z0-9_]*[a-z][A-Za-z0-9_]*)(?:\[\])?(?!\.\w|\s*\(|[\w$])")
_SENTENCE_START = re.compile(r"(?:^|[.!?;:]\s+|\n\s*)$")
# Capitalised prose words that name no argument.
_PROSE_WORDS = {"null", "evidence", "none", "true", "false", "android", "java", "kotlin"}


def _argument_types(text: str) -> set[str]:
    """The type names a sentence about a call uses for its arguments.

    `KeyManager[]` in "a null KeyManager[] selects the default key managers",
    `SecureRandom` in "the SecureRandom passed to init". A receiver
    (`SSLContext.init`), a call (`TrustAllManager()`) and a sentence's first
    word are not argument types.
    """
    found = set()
    for match in _TYPE_NAME.finditer(str(text or "")):
        if _SENTENCE_START.search(text[:match.start()]):
            continue
        name = match.group(1).lower()
        if name in _PROSE_WORDS:
            continue
        found.add(name[:-1] if name.endswith("s") and len(name) > 3 else name)
    return found


def _settled_call_value(text: str, settled_texts: list[str]) -> tuple[str, str]:
    """A (call, value) pair the text shares with one sentence that settled it.

    Only about the same argument: the two must also name the same argument
    type. SSLContext.init(null, trustManagers, null) judged correct use for its
    null KeyManager[] says nothing about its TrustManager[] or its null
    SecureRandom, though the same call and the same `null` appear in both. An
    entry or a sentence that names no argument type is not tied to the
    judgement, and the entry stands.
    """
    calls, values = _calls_and_values(text)
    types = _argument_types(text)
    for settled in settled_texts:
        settled_calls, settled_values = _calls_and_values(settled)
        shared_calls = calls & settled_calls
        shared_values = values & settled_values
        if shared_calls and shared_values and types & _argument_types(settled):
            return sorted(shared_calls)[0], sorted(shared_values)[0]
    return "", ""


def collateral_constraint_violations(
    extraction: dict | None,
    error: dict | None = None,
    dismissal_texts: Iterable[str] | None = None,
    excluded: list | None = None,
    settled_texts: Iterable[str] | None = None,
) -> list[dict]:
    """Requirements the source breaks that are not the reported one.

    A specification states several requirements and a detector reports one of
    them at a time. The evidence pass already assesses all of them -- six for
    opencloud's KeyStore finding -- but only the reported predicate reached a
    decision, so when that predicate turned out to be a documented false
    positive the case closed over evidence that the keystore password is the
    literal "password", which the same specification forbids in as many
    words.

    A documented false-positive shape alone still does not suppress anything
    here: it says the detector may report something wrongly, not that a
    requirement the source settles holds. What does is the analysis itself
    having dismissed that very value as the documented false positive.
    org.fedorahosted.freeotp's adjudication blocked the constructor because
    KeyStore.getInstance("AndroidKeyStore") is the specification's first
    documented shape, and the same record then raised "AndroidKeyStore is not
    in the permitted type set" as a collateral violation, and an issue asking
    the maintainers about it was prepared. A value both the specification and
    this analysis call a false positive is not a separate finding; it is the
    dismissal, restated. Those are skipped and, when `excluded` is given,
    recorded there with the reason, never dropped silently.

    The same holds for a value the adjudication judged correct use.
    AntennaPod's BackportTrustManager.getSystemTrustManager passes a null
    KeyStore to TrustManagerFactory.init, the documented request for the
    platform trust store; the adjudication said so (correct_use_reason) and
    the finding ended as a violation without security impact, yet the
    required-predicate entry "every KeyStore passed to init has the
    generatedKeyStore evidence", about that same null at that same call,
    became an issue asking the maintainer about it. An entry naming a call
    and a literal value (null, a quoted literal) that a settling sentence
    (`settled_texts`, or the analysis's own correct-use reason and
    confirmed cause) also names, about the same argument type, is that
    judgement restated. An entry about another call, another value or
    another argument of the same call still stands (Grocy's
    getInstance("X509") beside its documented init(null); a trust-all
    TrustManager beside a documented null KeyManager[] at the same
    SSLContext.init).

    Returns the violated predicates that are not the reported one, each with
    the evidence backing it, for the caller to decide about explicitly.
    """
    entry = extraction or {}
    reported = _normalised(reported_violation(error or {}))
    reported_section = violation_rule_section(reported_violation(error or {}))
    dismissed = false_positive_dismissal_literals(
        [*(dismissal_texts or []), *analysis_dismissal_texts(entry)],
        (error or {}).get("specification_context"),
    )
    settled = [
        *(str(text) for text in settled_texts or [] if str(text).strip()),
        *settled_value_texts(entry),
        *settled_value_texts((entry.get("v2") or {}).get("adjudication")
                             if isinstance(entry.get("v2"), dict) else None),
    ]
    collateral = []
    for predicate in entry.get("predicate_assessment", []) or []:
        if not isinstance(predicate, dict):
            continue
        if str(predicate.get("status", "")).strip() != "violated":
            continue
        if not (predicate.get("evidence_ids") or predicate.get("evidence")):
            continue
        statement = str(
            predicate.get("predicate") or predicate.get("event") or ""
        ).strip()
        if not statement:
            continue
        # The reported violation is a compact type name
        # (InvalidSequenceOfMethodCalls, UnsafeAlgorithm); a predicate is a
        # sentence. Treating the type name occurring in the sentence as "this
        # is the reported one" keeps the reported predicate out without
        # needing the two vocabularies to match exactly.
        if reported and reported in _normalised(statement):
            continue
        # A requirement from the same part of the rule as the reported
        # violation is the reported finding in other words: freeotp's
        # "Store follows SetEntry" is what its InvalidSequenceOfMethodCalls
        # was about, not a second finding beside it.
        if reported_section and str(predicate.get("rule_section", "")).strip() == reported_section:
            continue
        explanation = str(predicate.get("explanation")
                          or predicate.get("evidence") or "").strip()
        evidence_ids = list(predicate.get("evidence_ids") or [])
        # The analysis record writes "Evidence IDs: SRC-001, SPEC-001." at the
        # head of the text; that reached a maintainer's issue verbatim while
        # evidence_ids stayed empty.
        prefix = _EVIDENCE_ID_PREFIX.match(explanation)
        if prefix:
            for identifier in re.findall(r"[A-Z]+-\d+", prefix.group("ids") or ""):
                if identifier not in evidence_ids:
                    evidence_ids.append(identifier)
            explanation = explanation[prefix.end():].strip()
        quoted = {
            literal.lower()
            for literal in _QUOTED_LITERAL.findall(f"{statement} {explanation}")
        }
        restated = dismissed & quoted
        # The entry's own explanation can be the dismissal. networksurvey's
        # KeyStore record (exp_final_20261019) raised "AndroidKeyStore is
        # outside the whitelist" with the explanation "The specification
        # lists this as a known false-positive shape", and an issue went to
        # the maintainer; the sentence-level match above missed it because
        # the value and the words sit in adjacent sentences. tokn and
        # traffic-light, the same code, raised nothing.
        if not restated and _FALSE_POSITIVE_WORDS.search(explanation):
            restated = quoted & _documented_shape_literals(
                (error or {}).get("specification_context"))
        if restated:
            if excluded is not None:
                excluded.append({
                    "predicate": statement,
                    "reason": (
                        "the analysis dismissed "
                        + ", ".join(f'"{value}"' for value in sorted(restated))
                        + " as a false positive the specification documents"
                    ),
                })
            continue
        call, value = _settled_call_value(f"{statement} {explanation}", settled)
        if call:
            if excluded is not None:
                excluded.append({
                    "predicate": statement,
                    "reason": (
                        f"the adjudication judged {value} at {call}(...) "
                        "correct use; this is that value restated"
                    ),
                })
            continue
        collateral.append({
            "predicate": statement,
            "evidence_ids": evidence_ids,
            "explanation": explanation,
        })
    return collateral


def observed_call_sequence(error: dict) -> list[dict]:
    """The spec events seen in the reviewed method, in source order.

    enrich_spec_context.py already records every modelled event it finds in
    the method body together with its line, but nothing consumed that. Sorting
    the occurrences by line reconstructs the call sequence the specification's
    `protocol` grammar is written against.

    This is lexical: the matcher looks only inside the reviewed method and
    does not resolve receivers, so an event satisfied in a caller is invisible
    here and an unrelated receiver can match. The result is evidence for the
    adjudication to weigh, never grounds on its own to declare a violation.
    """
    specification = error.get("specification_context", {}) or {}
    by_line: dict[int, dict] = {}
    for entry in specification.get("observed_source_events", []) or []:
        if not isinstance(entry, dict) or not entry.get("observed"):
            continue
        name = str(entry.get("event", ""))
        for occurrence in entry.get("occurrences", []) or []:
            if not isinstance(occurrence, dict):
                continue
            raw_line = occurrence.get("method_line")
            line = int(raw_line) if isinstance(raw_line, int) else 0
            # Several modelled events routinely share one api_pattern because
            # they differ only by overload -- a single `.update(` call matches
            # UpdateByte, UpdateByteArray, UpdateByteArrayRange and
            # UpdateByteBuffer at once. Emitting each as its own step would
            # describe four calls where the source has one. The matcher
            # records no column, so a line is the finest position it can
            # honestly claim; collapse to that and keep the alternatives.
            position = by_line.setdefault(line, {
                "method_line": line,
                "candidate_events": [],
                "patterns": [],
                "detection": str(
                    occurrence.get("detection", "lexical_method_body")
                ),
            })
            if name and name not in position["candidate_events"]:
                position["candidate_events"].append(name)
            pattern = str(occurrence.get("pattern", ""))
            if pattern and pattern not in position["patterns"]:
                position["patterns"].append(pattern)

    sequence = []
    for line in sorted(by_line):
        position = by_line[line]
        position["candidate_events"].sort()
        position["patterns"].sort()
        position["unambiguous"] = len(position["candidate_events"]) == 1
        position["event"] = (
            position["candidate_events"][0] if position["unambiguous"]
            else " | ".join(position["candidate_events"])
        )
        sequence.append(position)
    return sequence


_EVENT_CALL_NAME = re.compile(r"(?:^|\.)(?P<name>[A-Za-z_$][\w$]*)\($")


_METHOD_HEADER = re.compile(
    r"^\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*"
    r"(?:(?:public|protected|private|static|final|synchronized|abstract|"
    r"native|override|open|internal|suspend|inline|operator|default)\s+)*"
    r"(?:fun\s+(?:<[^>]+>\s*)?(?:[\w$]+\.)?(?P<kotlin>[\w$]+)"
    r"|(?:[\w$.<>\[\],?]+\s+)?(?P<java>[\w$]+))\s*\([^;=]*$"
)


_NOT_METHOD_NAMES = frozenset({
    "if", "for", "while", "switch", "catch", "synchronized", "return", "new",
    "else", "try", "when", "throw", "super", "this",
})


def _enclosing_method_names(lines: list[str]) -> list[str]:
    """For each source line, the method it most plausibly sits in (lexical)."""
    names, current = [], ""
    for line in lines:
        header = _METHOD_HEADER.match(line)
        if header:
            name = header.group("kotlin") or header.group("java") or ""
            prefix = line[:header.start("java")] if header.group("java") else ""
            # A Java method has a return type or a modifier before its name; a
            # bare "foo(" is a call continued on the next line.
            declared = bool(header.group("kotlin")) or bool(
                re.search(r"[\w$>\]]\s+$", prefix)
            )
            if (name and name not in _NOT_METHOD_NAMES and declared
                    and not re.search(r"\b(?:return|new|throw|else)\s+$", prefix)):
                current = name
        names.append(current)
    return names


def field_call_sequences(class_source: str, method_body: str,
                         specification_context: dict | None) -> list[dict]:
    """Governed calls on a field the reviewed method assigns, across the file.

    A typestate detector tracks an object, not a method, and commonly reports
    the violation where the object was created. freeotp's TokenPersistence
    assigns its KeyStore field in the constructor -- the reported method --
    and calls setEntry in provision() and setEntry then getKey in restore().
    The SEQ evidence read the constructor only, saw Get then Load, and the
    analysis went looking for the violation somewhere else in the rule.

    Lexical, and said to be: receivers are matched by name, enclosing methods
    by their header lines, and the order in which those methods run is not
    something a file can show.
    """
    spec = specification_context or {}
    events = []
    for event in spec.get("events") or []:
        if not isinstance(event, dict):
            continue
        for pattern in event.get("api_patterns") or []:
            text = str(pattern).strip()
            call = _EVENT_CALL_NAME.search(text)
            if call:
                events.append((str(event.get("name", "")), text, call.group("name")))
    if not class_source or not str(method_body or "").strip() or not events:
        return []
    remainder = class_source.replace(method_body, "", 1)
    fields: list[str] = []
    for match in re.finditer(
        r"(?:\bthis\s*\.\s*)?\b(?P<field>[A-Za-z_$][\w$]*)\s*=(?!=)\s*(?P<rhs>[^;\n]+)",
        method_body,
    ):
        name = match.group("field")
        if name in fields:
            continue
        if not any(pattern in match.group("rhs") for _, pattern, _ in events):
            continue
        line_start = method_body.rfind("\n", 0, match.start("field")) + 1
        before = method_body[line_start:match.start("field")]
        # `KeyStore ks = ...` and `val ks = ...` declare a local.
        if re.search(r"(?:\b(?:val|var)|[\w$>\]])\s+$", before):
            continue
        if not re.search(rf"\b{re.escape(name)}\b\s*[;:=]", remainder):
            continue
        fields.append(name)

    lines = class_source.splitlines()
    enclosing = _enclosing_method_names(lines)
    sequences = []
    for name in fields:
        receiver = rf"(?:\bthis\s*\.\s*)?\b{re.escape(name)}\s*"
        occurrences = []
        for number, line in enumerate(lines, 1):
            candidates = []
            for event_name, pattern, call in events:
                if pattern.startswith("."):
                    hit = re.search(
                        receiver + r"\.\s*" + re.escape(call) + r"\s*\(", line
                    )
                else:
                    hit = re.search(receiver + r"=(?!=)[^;]*" + re.escape(pattern), line)
                if hit and event_name and event_name not in candidates:
                    candidates.append(event_name)
            if candidates:
                candidates.sort()
                occurrences.append({
                    "line": number,
                    "enclosing_method": enclosing[number - 1],
                    "candidate_events": candidates,
                    "event": " | ".join(candidates),
                    "text": line.strip()[:200],
                })
        if occurrences:
            sequences.append({
                "field": name,
                "occurrences": occurrences[:60],
                "methods": list(dict.fromkeys(
                    item["enclosing_method"] for item in occurrences
                )),
            })
    return sequences


_JAVAP_INVOKE = re.compile(
    r"\binvoke\w+\s+#\d+\s+//\s*(?:Interface)?Method\s+"
    r"(?P<owner>[\w/$]+)\.(?P<name>[\w$<>\"]+):(?P<desc>\S+)"
)


_DEX_INVOKE = re.compile(
    r"\binvoke-[\w/-]+\s+\{[^}]*\},\s*L(?P<owner>[\w/$]+);\."
    r"(?P<name>[\w$<>]+):(?P<desc>\S+)"
)


_JAVAP_STRING = re.compile(r"\bldc(?:_w)?\s+#\d+\s+//\s*String\s+(?P<value>.*)$")


_DEX_STRING = re.compile(r'\bconst-string(?:/jumbo)?\s+v\d+,\s*"(?P<value>(?:[^"\\]|\\.)*)"')


_STACK_NEUTRAL = re.compile(r"\bcheckcast\b|\bnop\b|\bdup\b")


_JAVAP_LOAD = re.compile(r"\baload(?:_(?P<short>\d)\b|\s+(?P<slot>\d+)\b)")


_JAVAP_FIELD = re.compile(r"\bget(?:static|field)\s+#\d+\s+//\s*Field\s+(?P<field>\S+)")


_DEX_INVOKE_REGISTERS = re.compile(r"\binvoke-[\w/-]+\s+\{(?P<registers>[^}]*)\}")


_DEX_WRITE = re.compile(r"^\s*(?:[0-9a-f]{4}:\s*)?(?P<op>[\w/-]+)\s+(?P<register>v\d+)\b")


_DEX_FIELD = re.compile(r"\b[si]get-object\s+v\d+,\s*(?:v\d+,\s*)?(?P<field>\S+)")


_DEX_NOT_A_WRITE = (
    "iput", "sput", "aput", "if-", "return", "throw", "monitor", "check-cast",
    "fill-array", "packed-switch", "sparse-switch",
)


_PRODUCER_LOOKBACK = 40


def _javap_argument_producer(instructions: list[str], index: int) -> str | None:
    """The instruction that pushed the last argument of the call at `index`.

    Follows a local variable back to its store: javac writes
    `getDefaultAlgorithm; astore_0; aload_0; getInstance` as readily as the
    direct form, and Conscrypt's createDefaultX509TrustManager does.
    """
    position = index - 1
    for _ in range(_PRODUCER_LOOKBACK):
        if position < 0:
            return None
        line = instructions[position]
        if _STACK_NEUTRAL.search(line):
            position -= 1
            continue
        load = _JAVAP_LOAD.search(line)
        if load:
            slot = load.group("short") or load.group("slot")
            store = re.compile(rf"\bastore(?:_{slot}\b|\s+{slot}\b)")
            position = next(
                (earlier for earlier in range(position - 1, -1, -1)
                 if store.search(instructions[earlier])),
                -1,
            ) - 1
            continue
        return line
    return None


def _dex_argument_producer(instructions: list[str], index: int, register: str) -> str | None:
    """The instruction that last wrote `register` before the call at `index`."""
    for position in range(index - 1, max(-1, index - _PRODUCER_LOOKBACK), -1):
        write = _DEX_WRITE.search(instructions[position])
        if not write or write.group("register") != register:
            continue
        operation = write.group("op")
        if operation.startswith(_DEX_NOT_A_WRITE):
            continue
        if operation.startswith("move-result"):
            return instructions[position - 1] if position > 0 else None
        return instructions[position]
    return None


def _argument_origin(producer: str | None) -> dict:
    if producer is None:
        return {"argument_origin": "not_determined",
                "reason": "no producing instruction within the traced window"}
    literal = _JAVAP_STRING.search(producer) or _DEX_STRING.search(producer)
    if literal:
        return {"argument_origin": "string_literal", "value": literal.group("value").strip()}
    call = _JAVAP_INVOKE.search(producer) or _DEX_INVOKE.search(producer)
    if call and call.group("desc").endswith(")Ljava/lang/String;"):
        return {
            "argument_origin": "method_return",
            "producer": (
                call.group("owner").replace("/", ".") + "."
                + call.group("name") + call.group("desc")
            ),
        }
    field = _JAVAP_FIELD.search(producer) or _DEX_FIELD.search(producer)
    if field:
        return {"argument_origin": "field", "field": field.group("field")}
    return {"argument_origin": "not_determined",
            "reason": "the producing instruction is neither a constant, a call nor a field read"}


MAX_BUILD_SOURCE_CHARACTERS = 60_000


MAX_BUILD_SOURCES = 6


def dependency_declaration_sources(repository_path: str, dependency_finding: dict | None,
                                   covered_paths: set[str]) -> list[dict]:
    """Build files a dependency or API-migration plan has to be able to edit.

    A plan may only edit a file supplied as exact source, and no build file was
    ever supplied: the declarations reached the packet only as the DEP
    summary. So a version bump -- one line in a version catalog, a
    build.gradle or versions.properties -- was rejected as "planned path
    lacks exact source evidence" whatever the analysis concluded, and no
    dependency decision could become a patch. The files that declare the
    named library are supplied; for an API migration, the reported module's
    build script and the shared version files are too, because the
    replacement usually has to be declared.
    """
    finding = dependency_finding or {}
    if not finding.get("kind") or not repository_path:
        return []
    paths = list(finding.get("declaration_files") or [])
    if finding.get("kind") == "api_migration" or not paths:
        paths.extend(finding.get("module_build_files") or [])
    sources = []
    root = Path(repository_path)
    for relative in dict.fromkeys(str(path) for path in paths if path):
        if relative in covered_paths:
            continue
        candidate = root / relative
        try:
            candidate.resolve().relative_to(root.resolve())
            content = candidate.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        if not content or len(content) > MAX_BUILD_SOURCE_CHARACTERS:
            continue
        sources.append({
            "source_file_path": relative,
            "relevance": [
                "declares the dependency the finding names"
                if relative in (finding.get("declaration_files") or [])
                else "build file of the reported module"
            ],
            "content": content,
        })
        if len(sources) >= MAX_BUILD_SOURCES:
            break
    return sources


# Counted over every contract file, already-present candidates included, since
# those are now promoted rather than skipped.
MAX_CONTRACT_SOURCES = 10


# The build-file cap (60,000 characters) dropped exactly the files a migration
# most needs: TowerCollector's MainActivity.java (94K) starts the task,
# NetworkSurvey's CellularController.java (66K) registers the receiver, and
# Gallery's ViewPagerActivity.kt (63K) runs it. Each plan was then rejected
# for editing a path without exact source.
MAX_CONTRACT_SOURCE_CHARACTERS = 160_000


def migration_contract_sources(repository_path: str, contract: dict | None,
                               covered_paths: set[str]) -> list[dict]:
    """Production files that use the deprecated API, supplied as exact source.

    Migrating AsyncTask means editing the subclass and the activity that
    starts and cancels it; migrating LocalBroadcastManager means editing the
    sender and every registered receiver. A plan may only edit exact sources,
    and the reported method's callers do not include those files -- nothing
    calls doInBackground directly -- so without this the plan could not reach
    the other half of the change.
    """
    if not repository_path:
        return []
    paths = sorted({
        str(path) for item in (contract or {}).get("contracts") or []
        for path in item.get("production_files") or []
    })
    root = Path(repository_path)
    sources = []
    for relative in paths:
        if relative in covered_paths:
            continue
        candidate = root / relative
        try:
            candidate.resolve().relative_to(root.resolve())
            content = candidate.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        if not content or len(content) > MAX_CONTRACT_SOURCE_CHARACTERS:
            continue
        sources.append({
            "source_file_path": relative,
            "relevance": ["uses the deprecated API the finding names"],
            "content": content,
        })
        if len(sources) >= MAX_CONTRACT_SOURCES:
            break
    return sources


def governed_factory_calls(specification_context: dict | None) -> set[tuple[str, str]]:
    """(Type, method) pairs the specification's events name by type."""
    calls = set()
    for event in (specification_context or {}).get("events") or []:
        if not isinstance(event, dict):
            continue
        for pattern in event.get("api_patterns") or []:
            match = re.fullmatch(
                r"\s*(?:[\w$]+\.)*(?P<type>[A-Z][\w$]*)\.(?P<method>[\w$]+)\(\s*",
                str(pattern),
            )
            if match:
                calls.add((match.group("type"), match.group("method")))
    return calls


def bytecode_argument_origins(methods: list | None,
                              specification_context: dict | None) -> list[dict]:
    """Where the String argument of each governed factory call comes from.

    The detector renders a value it could not resolve as nothing -- "expecting
    one of PKIX,SunX509 but found ." -- and both OkHttp's
    Platform.platformTrustManager and Conscrypt's
    SSLParametersImpl.createDefaultX509TrustManager pass
    TrustManagerFactory.getDefaultAlgorithm(), a value that exists only at run
    time. The bytecode states that plainly; this reads it deterministically
    from javap or dexdump output instead of leaving the model to find it.

    Only a call whose single parameter is a String is traced: the producing
    instruction is followed through local variables (javap) or registers
    (dex) within a bounded window, and anything else is reported as
    not_determined rather than guessed.
    """
    calls = governed_factory_calls(specification_context)
    if not calls:
        return []
    origins = []
    for method in methods or []:
        if not isinstance(method, dict):
            continue
        instructions = [
            line for line in str(method.get("disassembly", "")).splitlines()
            if line.strip()
        ]
        for index, line in enumerate(instructions):
            invoke = _JAVAP_INVOKE.search(line) or _DEX_INVOKE.search(line)
            if not invoke:
                continue
            owner = invoke.group("owner").rsplit("/", 1)[-1]
            if (owner, invoke.group("name")) not in calls:
                continue
            record = {
                "method": str(method.get("signature", "")),
                "call": f"{owner}.{invoke.group('name')}",
                "descriptor": invoke.group("desc"),
            }
            if not invoke.group("desc").startswith("(Ljava/lang/String;)"):
                origins.append({
                    **record, "argument_origin": "not_determined",
                    "reason": "only a call whose single parameter is a String is traced",
                })
                continue
            registers = _DEX_INVOKE_REGISTERS.search(line)
            if registers:
                names = [name.strip() for name in registers.group("registers").split(",")
                         if name.strip()]
                producer = (
                    _dex_argument_producer(instructions, index, names[-1])
                    if names else None
                )
            else:
                producer = _javap_argument_producer(instructions, index)
            origins.append({**record, **_argument_origin(producer)})
    return origins


def classify_spec_family(specification: str, dependency_context: dict | None = None,
                         dependency_finding: dict | None = None) -> str:
    """The analysis family, decided by what the finding is about.

    A dependency used to mean only "the reported class is not in the
    checkout", so an outdated-library finding attached to a class that does
    exist -- K-9's MessagingController, NewPipe's NewPipeSettings -- was
    analysed as generic code. The detector message says what the finding is
    about, and dependency_finding carries that reading.
    """
    kind = str((dependency_finding or {}).get("kind", ""))
    if kind == "dependency_version":
        return "dependency"
    if kind == "api_migration":
        return "api_migration"
    if dependency_context:
        return "dependency"
    # The specification's own analysis_family, not substrings of its name
    # (2026-10-17 audit). A specification that exists but declares none is
    # analysed as generic: its name is never consulted.
    from cognalys.utils.specification_data import analysis_family, specification as spec_data

    if spec_data(specification):
        return analysis_family(specification) or "generic"
    # A rule with no specification at all -- an external detector's rule id
    # such as Semgrep's weak-cipher-algorithm -- has nothing but its
    # descriptive name to go by.
    text = specification.lower()
    if "sslcontext" in text or "trustmanager" in text:
        return "tls"
    if "messagedigest" in text or "digest" in text:
        return "message_digest"
    if "cipher" in text or "gcm" in text:
        return "cipher"
    if "keygenerator" in text or "keypairgenerator" in text:
        return "key_generation"
    return "generic"


_GET_INSTANCE_CALL_PATTERN = re.compile(
    r"\b(?P<receiver>[A-Za-z_][\w.]*)\.getInstance\s*\("
)


_STRING_LITERAL_ONLY_PATTERN = re.compile(
    r'^\s*"(?:[^"\\]|\\.)*"(?:\s*\+\s*"(?:[^"\\]|\\.)*")*\s*$'
)


def _balanced_call_argument(text: str, open_paren_index: int) -> str | None:
    """Return the text between a call's opening '(' and its matching ')'."""
    depth = 0
    for index in range(open_paren_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren_index + 1:index]
    return None


def _first_top_level_argument(argument_list: str) -> str:
    """Split a call's argument list on its first top-level comma."""
    depth = 0
    for index, char in enumerate(argument_list):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            return argument_list[:index]
    return argument_list


# Language and platform namespaces. A qualified name under one of them in a
# specification describes the governed API, not an application.
_PLATFORM_NAMESPACES = (
    "java.", "javax.", "android.", "androidx.", "kotlin.", "kotlinx.",
    "dalvik.", "jdk.", "sun.",
)


_QUALIFIED_CLASS = re.compile(r"\b((?:[a-z][\w]*\.){2,}[A-Z][\w$]*)")


def with_scope_provenance(specification: dict | None, target_class: str) -> dict:
    """The specification, marked when its scope was written for another app.

    A generated specification's `semantic_scope` is written from the one
    representative finding it was generated for, and 25 of the 54 in
    data/specifications name that finding's application class ("The
    reported class is me.blog.korn123.easydiary.helper.ZipHelper ..."). The
    same text then reached every later finding of that specification as a
    statement about its scope: com.hmdm.launcher's XapkUtils.extract was
    adjudicated against a scope describing easydiary's ZipHelper, and
    oxproxion's createLanHttpClient against mifos's SelfServiceOkHttpClient;
    both ended as incomplete_multi_file_scope.

    Application classes are recognised as qualified names outside the
    language and platform namespaces that the specification does not govern
    (applies_to_types, event api_patterns). When one of them is not this
    finding's class, the copy returned carries `semantic_scope_provenance`
    saying so. The specification file is never changed: experiment
    manifests hash it as it is.
    """
    context = dict(specification or {})
    scope = str(context.get("semantic_scope", "") or "")
    if not scope:
        return context
    governed = set(_QUALIFIED_CLASS.findall(" ".join([
        json.dumps(context.get("applies_to_types", []), ensure_ascii=False),
        json.dumps([
            event.get("api_patterns", [])
            for event in context.get("events", []) or []
            if isinstance(event, dict)
        ], ensure_ascii=False),
    ])))
    target = str(target_class or "").strip()
    target_outer = target.split("$")[0]
    named = [
        name for name in dict.fromkeys(_QUALIFIED_CLASS.findall(scope))
        if not name.startswith(_PLATFORM_NAMESPACES) and name not in governed
    ]
    foreign = [
        name for name in named
        if name.split("$")[0] not in {target, target_outer}
    ]
    if not foreign:
        return context
    context["semantic_scope_provenance"] = {
        "status": "written_for_another_finding",
        "named_application_classes": foreign[:5],
        "this_finding_class": target,
        "note": (
            "semantic_scope was written from the representative finding this "
            "specification was generated for, in "
            + ", ".join(foreign[:3])
            + ". It describes the detector rule; its statements about that "
            "class's code, callers or files do not describe this finding, "
            "whose scope is established only by this packet's own evidence."
        ),
    }
    return context


def governed_api_types(specification_context: dict | None) -> list[str]:
    """Return the API types a specification explicitly governs.

    ``applies_to_types`` is authoritative when present.  Older generated
    specifications may expose only ``expected_api_simple_name``; retain that
    as a compatibility fallback instead of disabling targeted research.
    """
    context = specification_context or {}
    values = context.get("applies_to_types", [])
    if isinstance(values, str):
        values = [values]
    result = [str(value).strip() for value in values if str(value).strip()]
    expected = str(
        (context.get("generation_evidence", {}) or {}).get(
            "expected_api_simple_name", ""
        )
    ).strip()
    if not result and expected:
        result.append(expected)
    return list(dict.fromkeys(result))


def _receiver_matches_governed_type(receiver: str,
                                      governed_types: Iterable[str]) -> bool:
    qualified = {str(value).strip() for value in governed_types if str(value).strip()}
    if not qualified:
        return True
    simple = {
        value.rsplit(".", 1)[-1].rsplit("$", 1)[-1] for value in qualified
    }
    return receiver in qualified or receiver.rsplit(".", 1)[-1] in simple


def non_literal_getinstance_arguments(
    method_body: str,
    limit: int = 5,
    governed_types: Iterable[str] | None = None,
) -> list[str]:
    """Find getInstance(...) call arguments that are not plain string literals.

    Every CrySL-governed factory call this pipeline reasons about (SSLContext,
    KeyStore, Cipher, Mac, MessageDigest, TrustManagerFactory, ...) follows
    the same ``Type.getInstance(constrainedArg[, provider])`` shape. When the
    constrained argument is a literal (or concatenated literals), evidence
    extraction can read its value directly from the app's own source. When it
    is instead an expression -- a constant, enum accessor, or method call --
    the value usually lives in a third-party library the app depends on
    (e.g. ``okhttp3.TlsVersion.TLS_1_3.javaName()``) and is invisible to a
    plain source read. Surfacing these expressions lets the research stage
    ask a targeted, source-specific question instead of only the spec's
    generic unknowns, which by construction cannot mention this finding's
    actual code. When governed types are supplied, unrelated APIs that happen
    to use the common ``getInstance`` factory name are ignored. If type
    metadata is unavailable the broad legacy behavior is retained, so an old
    specification does not silently lose potentially useful research.
    """
    results: list[str] = []
    seen: set[str] = set()
    for match in _GET_INSTANCE_CALL_PATTERN.finditer(method_body):
        if not _receiver_matches_governed_type(
            match.group("receiver"), governed_types or []
        ):
            continue
        argument_list = _balanced_call_argument(method_body, match.end() - 1)
        if argument_list is None:
            continue
        candidate = _first_top_level_argument(argument_list).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if not _STRING_LITERAL_ONLY_PATTERN.match(candidate):
            results.append(candidate)
        if len(results) >= limit:
            break
    return results


def security_sink_evidence(catalog: list, method_name: str) -> dict | None:
    """Whether the finding's value reaches a security-relevant use.

    eu.opencloud.android's getKnownServersStore was closed NO_SAFE_FIX partly
    on "the evidence does not establish that stored certificates affect
    production TLS validation", while the source that establishes it was
    already in the packet as SRC-002: the KeyStore flows into an
    AdvancedX509TrustManager and from there into SSLContext.init. The
    adjudicator had to derive that itself across a local rename, and did not.

    This runs only over sources the packet already carries, so it adds a
    conclusion rather than new content, and it inherits their vetting. It is
    a lead for a reviewed decision, never grounds for automatic application:
    the record states the bound it was computed under.
    """
    if not method_name:
        return None
    try:
        from cognalys.extraction.sink_reachability import sink_reachability_in_source
    except ImportError:
        return None
    for item in catalog:
        data = item.get("data") or {}
        content = str(data.get("content") or "")
        path = str(data.get("source_file_path") or "")
        if not content or not path:
            continue
        found = sink_reachability_in_source(content, method_name, path)
        if not found:
            continue
        payload = dict(found, corroborates_evidence_id=item.get("evidence_id"))
        return {
            "evidence_id": "FLOW-001",
            "kind": "security_sink_reachability",
            "trust_level": "derived_from_supplied_source",
            "mandatory": False,
            "sha256": canonical_hash(payload),
            "data": payload,
        }
    return None


# Documented block sizes, in bytes, of the block ciphers the JCA names. A
# block size is a property of the algorithm, not a judgement about any use of
# it; it is carried so a reader can relate an enforced input size to the
# primitive actually in play.
CIPHER_BLOCK_SIZE_BYTES = {
    "AES": 16, "ARIA": 16, "CAMELLIA": 16, "SM4": 16, "SEED": 16,
    "DES": 8, "DESEDE": 8, "TRIPLEDES": 8, "BLOWFISH": 8, "RC2": 8,
}


# require(x.size == N) / if (x.length != N) and the Java and Kotlin spellings
# in between. Only equality and inequality against a single operand, because
# only those establish a size rather than bound one.
INPUT_SIZE_GUARD = re.compile(
    r"\b([A-Za-z_]\w*)\s*(?:\.\s*(?:size|length)\s*(?:\(\s*\))?)\s*"
    r"(==|!=)\s*([A-Za-z_]\w*|\d+)\b"
)


CONSTANT_DEFINITION = re.compile(
    r"\b(?:const\s+val|val|var|static\s+final\s+\w+|final\s+\w+|\w+)\s+"
    r"{name}\s*(?:=|:\s*\w+\s*=)\s*(\d+)\b"
)


def enforced_input_size_evidence(catalog: list, method_name: str,
                                 method_body: str) -> dict | None:
    """Sizes the reviewed method requires of its inputs, and what they mean.

    A weakness that depends on input length or on repetition across blocks
    reads very differently when the method refuses anything but one fixed
    size. com.vayunmathur's CableEid.encrypt requires its plaintext to be
    exactly EID_SIZE bytes, EID_SIZE is 16, and 16 is the AES block size, so
    the ECB it was flagged for can only ever process a single block -- the
    repetition the mode is criticised for cannot arise. None of that reached
    the adjudication, which recorded runtime_security_impact "confirmed".

    This states facts and no verdict. It reports which operands the method
    constrains, to what size, and the documented block size of any cipher
    algorithm named in the same body. Whether that changes the finding is the
    adjudication's call, and for CableEid it should not change the decision:
    the advertised 20-byte wire format still blocks a safe replacement.

    Runs only over the sources the packet already carries, so it adds a
    conclusion rather than new content, and inherits their vetting.
    """
    if not method_name or not method_body.strip():
        return None
    source = ""
    corroborates = ""
    for item in catalog:
        data = item.get("data") or {}
        content = str(data.get("content") or "")
        if content and method_name in content:
            source = content
            corroborates = str(item.get("evidence_id") or "")
            break

    constraints = []
    for operand, operator, size_expression in INPUT_SIZE_GUARD.findall(method_body):
        if operator != "==" and operator != "!=":
            continue
        resolved: int | None = None
        if size_expression.isdigit():
            resolved = int(size_expression)
        elif source:
            match = re.search(
                CONSTANT_DEFINITION.pattern.format(
                    name=re.escape(size_expression)
                ),
                source,
            )
            if match:
                resolved = int(match.group(1))
        if resolved is None:
            continue
        record = {
            "operand": operand,
            "comparison": operator,
            "size_expression": size_expression,
            "resolved_bytes": resolved,
        }
        if record not in constraints:
            constraints.append(record)
    if not constraints:
        return None

    algorithms = {
        name: size for name, size in CIPHER_BLOCK_SIZE_BYTES.items()
        if re.search(rf'"{name}(?:/|"){{1}}', method_body, re.IGNORECASE)
        or re.search(rf'"{name}/', method_body, re.IGNORECASE)
    }
    payload = {
        "method": method_name,
        "enforced_input_sizes": constraints,
        "named_cipher_block_sizes_bytes": algorithms,
        "bound": (
            "Read from the reviewed method body and the constants of its own "
            "file. It states which inputs the method refuses unless they have "
            "an exact size, and the documented block size of any block cipher "
            "named there. It draws no conclusion about the finding."
        ),
    }
    if corroborates:
        payload["corroborates_evidence_id"] = corroborates
    return {
        "evidence_id": "SIZE-001",
        "kind": "enforced_input_size",
        "trust_level": "derived_from_supplied_source",
        "mandatory": False,
        "sha256": canonical_hash(payload),
        "data": payload,
    }


# The JDK's mutating collection and map methods, by name. Assignment is the
# other way shared state changes and is matched separately; between them they
# cover how a field reachable through an accessor is written.
_MUTATOR_NAMES = (
    "add", "addAll", "addFirst", "addLast", "clear", "compute",
    "computeIfAbsent", "computeIfPresent", "merge", "offer", "offerFirst",
    "offerLast", "poll", "pollFirst", "pollLast", "pop", "push", "put",
    "putAll", "putIfAbsent", "remove", "removeAll", "removeFirst",
    "removeIf", "removeLast", "replaceAll", "retainAll", "set", "sort",
)


_MUTATING_CALL = re.compile(
    r"\b(?P<field>[A-Za-z_$][\w$]*)\s*\.\s*(?P<call>"
    + "|".join(sorted(_MUTATOR_NAMES, key=len, reverse=True))
    + r")\s*\("
)


_ZERO_ARG_CALL = re.compile(r"\.\s*([A-Za-z_$][\w$]*)\s*\(\s*\)")


_NON_FIELD_RETURNS = frozenset({"null", "true", "false", "this", "super"})


# A reviewed method can call dozens of zero-argument methods, and following
# every one of them through three hops over every source in the packet is
# work out of proportion to what it finds. The accessor that reaches shared
# state is near the top of a method, not its hundredth call.
_MAX_ACCESSORS = 40


_RETURN_EXPRESSION = re.compile(r"\breturn\s+([^;]+);")


# A class-level field, which always carries a modifier in practice; requiring
# one is what keeps a local variable of the same shape out of the result. The
# type runs to the end of its own line only: letting it cross newlines made
# the lazy quantifier backtrack over whole files, and a declaration whose
# name sits on a later line than its modifiers is not a real shape anyway.
_FIELD_DECLARATION = (
    r"^[ \t]*(?:(?:public|protected|private|static|final|transient|volatile)"
    r"[ \t]+)+[\w$.<>,\[\]? \t]{{0,200}}?\b{name}[ \t]*(?P<tail>[=;])"
)


def _method_text(sources: list[tuple[str, str]], name: str) -> str:
    """The body of a zero-argument method called ``name``, or "".

    Brace-matched from the declaration rather than parsed: the accessor
    chains this follows are short, and a parser for two languages is not
    warranted for reading `return someField;`.
    """
    if not name:
        return ""
    # Bounded and newline-free between the empty parameter list and the body
    # brace: an unbounded class that also matched whitespace backtracked over
    # entire files for every accessor that had no declaration here, which is
    # most of them. `throws IOException` is what the span is for.
    declaration = re.compile(
        r"\b" + re.escape(name) + r"[ \t]*\(\s*\)[^{;\n]{0,200}\{"
    )
    for _, text in sources:
        # A substring test before the regex. Most accessor names appear in
        # none of the packet's files, and scanning every one of them with a
        # regex for every name is what took the evidence stage from
        # milliseconds to seconds per finding.
        if name not in text:
            continue
        match = declaration.search(text)
        if not match:
            continue
        depth = 0
        for index in range(match.end() - 1, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return text[match.end() - 1:index + 1]
        return text[match.end() - 1:]
    return ""


def fields_reached_through_accessors(method_body: str,
                                     sources: list[tuple[str, str]],
                                     max_depth: int = 3) -> list[str]:
    """Fields the reviewed method obtains from another object, by name.

    State a method reaches through an accessor is state something else also
    holds, which is what a concurrency finding is about; a field of the
    method's own class is not. So this follows the zero-argument calls the
    method makes into their declarations, and through any further accessor
    they return, until a bare field name is returned.

    gtalksms is two hops: HelpCmd.<init> calls
    MainService.getCommandHandlersSet(), which returns
    mCommandManager.getCommandHandlerSet(), which returns
    mCommandHandlersSet.
    """
    pending = [
        name for name in dict.fromkeys(_ZERO_ARG_CALL.findall(method_body or ""))
    ][:_MAX_ACCESSORS]
    seen: set[str] = set()
    fields: list[str] = []
    for _ in range(max_depth):
        following: list[str] = []
        for accessor in pending:
            if accessor in seen or len(seen) >= _MAX_ACCESSORS:
                continue
            seen.add(accessor)
            for expression in _RETURN_EXPRESSION.findall(
                _method_text(sources, accessor)
            ):
                expression = expression.strip()
                if re.fullmatch(r"[A-Za-z_$][\w$]*", expression):
                    # `return null;` is a bare identifier by shape and names
                    # no field; so are the other value keywords.
                    if expression in _NON_FIELD_RETURNS:
                        continue
                    if expression not in fields:
                        fields.append(expression)
                    continue
                nested = _ZERO_ARG_CALL.findall(expression)
                following.extend(nested)
        if not following:
            break
        pending = following
    return fields


_METHOD_SIGNATURE = re.compile(
    r"^[ \t]*(?:(?:public|protected|private|internal|static|final|abstract"
    r"|synchronized|override|open|suspend|operator|fun|def)[ \t]+)+"
    r"[\w$.<>,\[\]?]*[ \t]*(?P<name>[A-Za-z_$][\w$]*)[ \t]*\([^;]*$"
)


def _enclosing_method(text: str, statement: str) -> dict:
    """The method a statement sits in, by brace depth, with its modifiers.

    A line scanner rather than a parser, matching the rest of this module:
    it tracks depth and remembers the last signature opened one level above
    the statement. Returns {} when it cannot tell.
    """
    depth = 0
    open_methods: list[tuple[int, str, str]] = []
    for line in text.splitlines():
        if statement and line.strip() == statement:
            if open_methods:
                _, name, declaration = open_methods[-1]
                return {"name": name, "declaration": declaration[:200]}
            return {}
        match = _METHOD_SIGNATURE.match(line)
        if match and "{" in line:
            open_methods.append((depth, match.group("name"), line.strip()))
        depth += line.count("{") - line.count("}")
        while open_methods and depth <= open_methods[-1][0]:
            open_methods.pop()
    return {}


def _call_sites(sources: list[tuple[str, str]], name: str,
                declaration: str) -> list[str]:
    """Where ``name`` is invoked across the packet's sources."""
    if not name:
        return []
    pattern = re.compile(r"(?<![\w$.])(?:[\w$.]+\s*\.\s*)?"
                         + re.escape(name) + r"\s*\(")
    sites = []
    for path, text in sources:
        if name not in text:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if name not in stripped or stripped == declaration:
                continue
            if _METHOD_SIGNATURE.match(line):
                continue
            if pattern.search(stripped):
                sites.append(f"{path}: {stripped[:120]}")
    return sites


def shared_state_mutation_evidence(
    catalog: list, method_body: str, max_fields: int = 6,
) -> dict | None:
    """Every site in the examined scope that writes the shared state read here.

    A concurrency diagnostic asserts a hazard that needs a second writer. The
    packet already carried the sources that answer whether one exists, and
    the adjudication had to derive it across two accessor hops -- and did
    not. gtalksms's HelpCmd.<init> traverses a Collections.synchronizedSet
    without holding its monitor, which is a real violation of the documented
    contract; but CommandManager.mCommandHandlersSet is final, has exactly
    one mutation site in the whole repository (`add` inside setupCommands,
    itself called once), and is never removed from. No concurrent
    modification is possible, so the hazard cannot arise. The adjudication
    recorded runtime_security_impact "unknown" instead
    (run_20260911_095238), which understates what the evidence settles.

    This states facts and no verdict: which fields the method reaches through
    an accessor, whether each is final, and every assignment or mutating call
    on it in the sources this packet carries. The bound travels with it --
    reflection, unexamined modules and native writers are not covered, so a
    single site here is not proof of a single site in the program.
    """
    sources: list[tuple[str, str]] = []
    for item in catalog:
        data = item.get("data") or {}
        text = str(data.get("content") or data.get("definition") or "")
        if text:
            sources.append((
                str(data.get("source_file_path") or ""),
                text,
            ))
    if not sources or not (method_body or "").strip():
        return None

    records = []
    for field in fields_reached_through_accessors(method_body, sources):
        declaration = ""
        declared_in = ""
        for path, text in sources:
            if field not in text:
                continue
            match = re.search(
                _FIELD_DECLARATION.format(name=re.escape(field)),
                text, re.MULTILINE,
            )
            if match:
                declaration = match.group(0).strip()
                declared_in = path
                break
        if not declaration:
            continue
        declaration_pattern = re.compile(
            _FIELD_DECLARATION.format(name=re.escape(field))
        )
        sites = []
        initializer = ""
        seen_statements: set[str] = set()
        for path, text in sources:
            if field not in text:
                continue
            for line in text.splitlines():
                # The same substring test as above, per line: running the
                # mutator alternation over every line of every source is the
                # other half of the cost.
                if field not in line:
                    continue
                stripped = line.strip()
                mutating = [
                    match.group("call")
                    for match in _MUTATING_CALL.finditer(stripped)
                    if match.group("field") == field
                ]
                assigned = re.search(
                    r"\b" + re.escape(field) + r"\s*=(?!=)", stripped
                )
                if not mutating and not assigned:
                    continue
                # The declaration's own initializer is where the field's
                # value comes from, not a second writer racing the first.
                # Reporting it as a mutation site would turn every field
                # into at least one, which is exactly the count that
                # decides whether a concurrency hazard can arise.
                if declaration_pattern.match(line):
                    initializer = stripped[:200]
                    continue
                if stripped in seen_statements:
                    continue
                seen_statements.add(stripped)
                # How often the writer runs decides as much as how many
                # writers there are: one mutating call inside a method
                # called from everywhere is not a single write. The
                # adjudication for gtalksms answered runtime_security_impact
                # "not_established" (run_20260911_125911) with this fact
                # missing, and it was right to -- the enclosing method's
                # caller is outside the examined scope, which the record now
                # says instead of leaving the reader to assume.
                enclosing = _enclosing_method(text, stripped)
                call_sites = _call_sites(
                    sources, enclosing.get("name", ""),
                    enclosing.get("declaration", ""),
                )
                site = {
                    "source_file_path": path,
                    "statement": stripped[:200],
                    "kind": "assignment" if assigned else "mutating_call",
                }
                if enclosing:
                    enclosing_declaration = enclosing["declaration"]
                    site["enclosing_method"] = enclosing["name"]
                    site["enclosing_method_declaration"] = (
                        enclosing_declaration
                    )
                    site["call_sites_in_examined_scope"] = call_sites[:10]
                    # Only a private method is guaranteed to have every
                    # caller in its own file, so only then is a count of
                    # zero or one a fact about the program rather than
                    # about how much source this packet happens to carry.
                    site["call_sites_complete"] = bool(
                        re.match(r"^\s*private\b", enclosing_declaration)
                    )
                sites.append(site)
        records.append({
            "field": field,
            "declared_in": declared_in,
            "declaration": declaration[:200],
            "initializer": initializer,
            "final": bool(re.search(r"\bfinal\b", declaration)),
            "mutation_sites": sites,
            "mutation_site_count": len(sites),
        })
        if len(records) >= max_fields:
            break
    if not records:
        return None

    payload = {
        "fields_reached_through_accessors": records,
        "bound": (
            "Read from the sources this packet carries. It reports every "
            "assignment and JDK mutating call on each field found there, and "
            "whether the field is final. It does not cover reflection, "
            "native code, or modules outside the examined scope, so a single "
            "site here is evidence about the examined scope and not proof "
            "that the program has only one. It draws no conclusion about the "
            "finding."
        ),
    }
    return {
        "evidence_id": "MUT-001",
        "kind": "shared_state_mutation",
        "trust_level": "derived_from_supplied_source",
        "mandatory": False,
        "sha256": canonical_hash(payload),
        "data": payload,
    }


ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


# The manifest elements whose declaration decides whether a component can be
# reached from outside the application.
MANIFEST_COMPONENT_TAGS = ("provider", "activity", "activity-alias",
                           "service", "receiver")


def _manifest_name_matches(declared: str, target_class: str) -> bool:
    """Whether a manifest android:name refers to this finding's class.

    A declaration may be fully qualified, relative to the manifest package
    (".providers.FileContentProvider"), or a bare class name. Resolving the
    package would mean resolving applicationId across build variants and
    suffixes; matching on the suffix settles every real form without it.
    """
    declared = declared.strip()
    target_class = target_class.strip()
    if not declared or not target_class:
        return False
    if declared == target_class:
        return True
    if declared.startswith("."):
        return target_class.endswith(declared)
    if "." not in declared:
        return target_class.endswith("." + declared)
    return False


def manifest_component_evidence(repository_path: str,
                                target_class: str) -> list[dict]:
    """The manifest declarations of the finding's own component.

    android:exported and android:permission decide whether another
    application can reach an Android component at all, so for a finding in a
    ContentProvider, Activity, Service or Receiver they are the difference
    between an attacker-controlled input and one the application supplies to
    itself.

    ownCloud's FileContentProvider.delete is the case that motivated this.
    Its adjudication closed with runtime_security_impact "unknown" over the
    words "provider exposure to an untrusted caller [not established]",
    while the manifest three lines from the declaration said
    android:exported="false". The packet had already discovered that file --
    it listed the path under configuration_evidence, and even recorded that
    it matched the term "ContentProvider" -- and kept only the path, never
    the content.
    """
    # Path("") normalizes to Path("."), so an empty repository_path would
    # otherwise rglob this process's own working directory -- the whole
    # repository, cloned Android projects and all. The same trap is
    # documented on repository_has_test_infrastructure.
    location = str(repository_path or "").strip()
    if not location or not target_class:
        return []
    root = Path(location)
    if not root.is_dir():
        return []
    ignored = {".git", ".gradle", "build", "generated", "node_modules"}
    declarations = []
    for path in sorted(root.rglob("AndroidManifest.xml")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if ignored.intersection(relative.parts):
            continue
        try:
            tree = ElementTree.parse(path)
        except (OSError, ElementTree.ParseError):
            continue
        manifest_root = tree.getroot()
        for tag in MANIFEST_COMPONENT_TAGS:
            for element in manifest_root.iter(tag):
                declared = element.get(f"{ANDROID_NS}name", "")
                target_activity = element.get(f"{ANDROID_NS}targetActivity", "")
                # An <activity-alias> exposes another activity under its own
                # android:name, so matching that name alone walks past the
                # declaration that decides reachability. Element Android
                # declares MainActivity with no android:exported in
                # vector/AndroidManifest.xml and then, from a different
                # module's manifest, an exported LAUNCHER alias whose
                # android:targetActivity is that same MainActivity. Only the
                # first reached the packet, and the adjudication of
                # CVE-2024-26131 closed "effective exported state [...] not
                # established" over an answer the repository contained.
                aliased = bool(
                    target_activity
                    and _manifest_name_matches(target_activity, target_class)
                )
                if not aliased and not _manifest_name_matches(
                    declared, target_class
                ):
                    continue
                attributes = {
                    key.replace(ANDROID_NS, "android:"): value
                    for key, value in element.attrib.items()
                }
                declarations.append({
                    "source_file_path": relative.as_posix(),
                    "component_tag": tag,
                    "declared_name": declared,
                    "target_class": target_class,
                    # True when this declaration is not the component itself
                    # but an alias that exposes it, in which case its
                    # exported/permission values govern reachability of the
                    # target rather than of the declared name.
                    "exposes_target_as_alias": aliased,
                    "android:targetActivity": target_activity or None,
                    # An exported component with no intent filter is reachable
                    # only by explicit component name; one with a filter is
                    # reachable by action. Both matter, and neither was said.
                    "declares_intent_filter": (
                        element.find("intent-filter") is not None
                    ),
                    # Repeated out of the attribute map so a reader cannot
                    # miss them, and explicitly null when the manifest is
                    # silent: an undeclared exported value is resolved by
                    # the platform from targetSdk, which this does not
                    # attempt to infer.
                    "android:exported": element.get(f"{ANDROID_NS}exported"),
                    "android:permission": element.get(
                        f"{ANDROID_NS}permission"
                    ),
                    "android:readPermission": element.get(
                        f"{ANDROID_NS}readPermission"
                    ),
                    "android:writePermission": element.get(
                        f"{ANDROID_NS}writePermission"
                    ),
                    "attributes": attributes,
                })
    return declarations
