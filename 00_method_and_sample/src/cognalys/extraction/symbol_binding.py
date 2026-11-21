#!/usr/bin/env python3
"""What a call in the reviewed source actually binds to.

Every adjudication starts from the rule that a textual occurrence proves
only the occurrence: unless the receiver is established, `X.foo(true)` is
not evidence that `a.b.X.foo(boolean)` was called. That rule is right, and
for a call into a platform or library API the pipeline had no way to
satisfy it. The extractor asked -- `{"kind": "symbol_resolution", "reason":
"Establish whether the reviewed expression resolves specifically to
android.webkit.WebView.setWebContentsDebuggingEnabled(boolean)"}` -- and the
responder, which could only answer with a repository file, declined with
"no matching file in the repository". There is no such file: the type is
declared in the Android SDK. So the predicate stayed unknown, the cause
stayed unconfirmed, and the finding abstained. That happened in four runs.

This module answers that question from the two things a checkout does hold:
the file's import list and the receiver's declaration. It reports what it
resolved and how, and it reports non-resolution just as plainly -- an alias
import, a chained receiver, two wildcard imports that could both supply the
name. It never guesses a package for a type nothing imports.

It is deliberately independent of any specification: it is given the names
a request asks about and reports the binding facts for those call sites,
whatever detector family asked.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Sequence


# `import a.b.C;`, `import a.b.*;`, `import a.b.C as D` (Kotlin). The alias
# is captured rather than ignored: an aliased import means the name in the
# call site is not the type's own name, which is exactly a case this module
# must refuse to resolve rather than resolve wrongly.
_IMPORT = re.compile(
    r"(?m)^[ \t]*import[ \t]+(?:static[ \t]+)?"
    r"(?P<path>[\w.]+)(?P<wildcard>\.\*)?"
    r"(?:[ \t]+as[ \t]+(?P<alias>[A-Za-z_]\w*))?[ \t]*;?[ \t]*$"
)
_PACKAGE = re.compile(r"(?m)^[ \t]*package[ \t]+([\w.]+)")
_METHOD_NAME = re.compile(r"\b([a-z][A-Za-z0-9_$]*)\s*\(")
# A member access ending right where a call's name begins.
_CHAINED_BEFORE = re.compile(r"(?:\?|!!)?\.\s*\Z")


def called_names_in(text: str) -> List[str]:
    """Method-like names a free-text request or message mentions.

    The extractor writes its request for humans ("including the boolean
    overload"), so the names are recovered from the shape a call has rather
    than from a field that would have to be filled in correctly.
    """
    seen, ordered = set(), []
    for name in _METHOD_NAME.findall(text or ""):
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _arguments(source: str, open_index: int) -> tuple:
    """Raw argument texts of the call whose `(` is at `open_index`."""
    depth = 0
    arguments: List[str] = []
    current = []
    index = open_index
    while index < len(source):
        character = source[index]
        if character in "([{":
            depth += 1
            if depth == 1:
                index += 1
                continue
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                break
        if depth == 1 and character == ",":
            arguments.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    tail = "".join(current).strip()
    if tail:
        arguments.append(tail)
    return [argument for argument in arguments if argument], index


def _declared_type(source: str, receiver: str) -> str:
    """The declared type of `receiver`, read from its declaration."""
    for pattern in (
        # Kotlin `val v: Type`, and a Java/Kotlin parameter `v: Type`
        rf"\b{re.escape(receiver)}\s*:\s*([A-Z][\w$.]*)",
        # Kotlin `val v = Type(...)`
        rf"\b(?:val|var)\s+{re.escape(receiver)}\s*=\s*([A-Z][\w$.]*)\s*\(",
        # Java `Type v = ...`, `Type v;`, `(Type v)`
        rf"\b([A-Z][\w$.]*)(?:<[^;(){{}}]*>)?\s+{re.escape(receiver)}\s*[=;,)]",
    ):
        match = re.search(pattern, source)
        if match:
            return match.group(1).split(".")[-1]
    return ""


def _qualify(simple_type: str, source: str) -> Dict:
    """Fully qualified candidates for `simple_type` in this file."""
    explicit: List[str] = []
    wildcards: List[str] = []
    aliased = False
    for match in _IMPORT.finditer(source):
        path = match.group("path")
        if match.group("alias"):
            if match.group("alias") == simple_type:
                aliased = True
            continue
        if match.group("wildcard"):
            wildcards.append(f"{path}.{simple_type}")
        elif path.split(".")[-1] == simple_type:
            explicit.append(path)
    package = _PACKAGE.search(source)
    return {
        "explicit": list(dict.fromkeys(explicit)),
        "wildcard": list(dict.fromkeys(wildcards)),
        "aliased": aliased,
        "package": package.group(1) if package else "",
    }


def _resolution(candidates: Dict, simple_type: str, declared_locally: bool,
                declared_in_repository: Optional[bool],
                expected_types: Sequence[str] = ()) -> tuple:
    """(status, qualified_name) for one receiver type."""
    if candidates["aliased"]:
        return "aliased_import_not_resolved", ""
    if candidates["explicit"]:
        if len(candidates["explicit"]) > 1:
            return "ambiguous", ""
        return ("repository_type" if declared_in_repository
                else "unique_external"), candidates["explicit"][0]
    if declared_locally or declared_in_repository:
        package = candidates["package"]
        return "repository_type", (
            f"{package}.{simple_type}" if package else simple_type
        )
    if len(candidates["wildcard"]) == 1 and declared_in_repository is False:
        # One wildcard import and a type this checkout does not declare: the
        # name can only come from that package. Reported under its own status
        # so a reader can weigh it differently from an explicit import.
        return "single_wildcard_import", candidates["wildcard"][0]
    if candidates["wildcard"]:
        # Several on-demand imports could spell this name. Both languages
        # reject a simple name that two of them actually supply, so in a file
        # that compiles exactly one of these packages declares the type --
        # which one is not readable from the checkout. When the caller says
        # which type the question is about and it is one of these, that is
        # reported as such and stays distinguishable from an import that
        # names the type outright.
        matched = [
            candidate for candidate in candidates["wildcard"]
            if candidate in set(expected_types)
        ]
        if len(matched) == 1:
            return "consistent_with_reported_api", matched[0]
        return "ambiguous", ""
    return "unresolved", ""


def qualified_type_names(text: str) -> List[str]:
    """Fully qualified type names a request or a specification writes out.

    `android.webkit.WebView.setWebContentsDebuggingEnabled(boolean)` yields
    `android.webkit.WebView`: the qualified prefix that ends at the last
    capitalised segment.
    """
    found = []
    for match in re.finditer(
        r"\b((?:[a-z][\w$]*\.){1,8}[A-Z][\w$]*)\b", text or ""
    ):
        found.append(match.group(1))
    return list(dict.fromkeys(found))


def _names_a_function(name: str, source: str) -> bool:
    """Whether `source` declares or statically imports `name` as a function.

    A capitalised method exists: a Java static `SHA1(...)`, a Kotlin
    Composable `BrowserAuthScreen(...)`. Their receiverless calls are calls
    on the enclosing type or the import, not constructors (review of
    2026-10-31: read as constructors, the same-file callers of a reported
    `SHA1` lost their declaring_file binding).
    """
    escaped = re.escape(name)
    return bool(
        re.search(rf"\bfun\s+(?:<[^>]+>\s*)?(?:[\w?.<>]+\.)?{escaped}\s*\(", source)
        or re.search(rf"(?m)^\s*import\s+static\s+[\w.]+\.{escaped}\s*;", source)
        or re.search(rf"(?m)^\s*(?:@\w+\s+)*(?:(?:public|protected|private|static|final|"
                     rf"synchronized|abstract|native)\s+)*(?:<[^>]+>\s*)?"
                     rf"(?!(?:new|return|throw|else)\b)[\w$.<>\[\],?]+\s+{escaped}\s*\([^;{{}}]*\)\s*(?:throws\s+[^{{;]+)?\{{",
                     source))


def resolve_call_bindings(
    source: str,
    names: Sequence[str],
    declares: Optional[Callable[[str], bool]] = None,
    max_sites: int = 12,
    expected_types: Sequence[str] = (),
    constructors: bool = True,
) -> List[Dict]:
    """Binding facts for every call of `names` in `source`.

    `declares(simple_type)` says whether the checkout itself declares a type
    of that name; without it, "is this an external type?" is left open
    rather than assumed. `expected_types` are the fully qualified types the
    asker named, used only to say whether an on-demand import could supply
    one of them -- never to invent a package for a name nothing imports.
    `constructors=False` is for a caller that knows the name is a method
    (caller_contract looking for the callers of a reported one).
    """
    records: List[Dict] = []
    for name in dict.fromkeys(str(item) for item in names if str(item).strip()):
        call = re.compile(
            r"(?:(?P<receiver>[A-Za-z_][\w$]*(?:\s*\.\s*[A-Za-z_][\w$]*)*)"
            r"\s*(?P<dot>\.)\s*)?(?<![\w$])" + re.escape(name) + r"\s*\(",
        )
        for match in call.finditer(source):
            if len(records) >= max_sites:
                return records
            arguments, _end = _arguments(source, match.end() - 1)
            receiver = re.sub(r"\s+", "", match.group("receiver") or "")
            line = source.count("\n", 0, match.start()) + 1
            record = {
                "called_name": name,
                "line": line,
                "call_text": source[match.start():match.end() - 1].strip(),
                "arguments": arguments,
                "arity": len(arguments),
                "receiver": receiver,
            }
            if not match.group("dot") and _CHAINED_BEFORE.search(
                    source, 0, match.start()):
                # A dot the receiver pattern could not reach: the call hangs
                # off a call result (`a.b().name(`), a Kotlin not-null or
                # safe-call operator (`intent.data!!.name(`, `x?.name(`), or
                # an indexing expression. Reported as implicit, it read as a
                # call on the enclosing type, which it is not.
                record.update({
                    "receiver_kind": "chained",
                    "resolution": "unresolved",
                    "qualified_type": "",
                    "note": "the receiver is an expression, not a single "
                            "declared name",
                })
                records.append(record)
                continue
            if (constructors and not match.group("dot") and name[:1].isupper()
                    and not _names_a_function(name, source)):
                # A receiverless call named like a type is that type's
                # constructor: Java's `new T(`, Kotlin's `T(`. Read as a call
                # on the enclosing type, BlueGate's KeyPair(entry.certificate.
                # publicKey, entry.privateKey) stayed unresolved though the file
                # imports java.security.KeyPair, and the adjudication blocked
                # on the binding twice (run_v6, run_v7, 2026-10-31).
                in_repository = None if declares is None else bool(declares(name))
                candidates = _qualify(name, source)
                status, qualified = _resolution(
                    candidates, name,
                    declared_locally=bool(re.search(
                        rf"\b(?:class|interface|object|enum|record)\s+{re.escape(name)}\b",
                        source)),
                    declared_in_repository=in_repository,
                    expected_types=expected_types,
                )
                record.update({
                    "receiver_kind": "constructor",
                    "receiver_type": name,
                    "resolution": status,
                    "qualified_type": qualified,
                    "declared_in_repository": in_repository,
                    "explicit_imports": candidates["explicit"],
                    "wildcard_imports": candidates["wildcard"],
                    "note": "a receiverless call named like a type: the constructor "
                            "of that type, bound from this file's imports and "
                            "declarations",
                })
                records.append(record)
                continue
            if not match.group("dot"):
                # No receiver at all: the call binds to the enclosing type or
                # something it inherits, which this file alone cannot settle.
                record.update({
                    "receiver_kind": "implicit",
                    "resolution": "unresolved",
                    "qualified_type": "",
                    "note": "the call has no receiver, so its owner is the "
                            "enclosing type or a supertype of it",
                })
                records.append(record)
                continue
            if "." in receiver:
                record.update({
                    "receiver_kind": "chained",
                    "resolution": "unresolved",
                    "qualified_type": "",
                    "note": "the receiver is an expression, not a single "
                            "declared name",
                })
                records.append(record)
                continue
            declared = _declared_type(source, receiver)
            if declared:
                simple_type, receiver_kind = declared, "value"
            elif receiver[:1].isupper():
                simple_type, receiver_kind = receiver, "type"
            else:
                record.update({
                    "receiver_kind": "value",
                    "resolution": "unresolved",
                    "qualified_type": "",
                    "note": "no declaration of the receiver was found in "
                            "this file",
                })
                records.append(record)
                continue
            in_repository = None if declares is None else bool(declares(simple_type))
            candidates = _qualify(simple_type, source)
            status, qualified = _resolution(
                candidates, simple_type,
                declared_locally=bool(
                    re.search(rf"\b(?:class|interface|object|enum)\s+{re.escape(simple_type)}\b",
                              source)
                ),
                declared_in_repository=in_repository,
                expected_types=expected_types,
            )
            record.update({
                "receiver_kind": receiver_kind,
                "receiver_type": simple_type,
                "resolution": status,
                "qualified_type": qualified,
                "declared_in_repository": in_repository,
                "explicit_imports": candidates["explicit"],
                "wildcard_imports": candidates["wildcard"],
            })
            records.append(record)
    return records


DERIVATION_NOTE = (
    "Derived deterministically from this file's import list and the "
    "receiver's declaration, not from a model. `unique_external` means one "
    "explicit import supplies the receiver type and the checkout declares no "
    "type of that name, so the call binds to that library or platform API. "
    "`single_wildcard_import` means the only possible source is one "
    "on-demand import. `consistent_with_reported_api` means several "
    "on-demand imports are in scope and one of them is the package of the "
    "API the request named; since neither Java nor Kotlin compiles a simple "
    "name that two on-demand imports both supply, exactly one of the listed "
    "packages declares this type, and no other candidate was named by the "
    "question -- weaker than an explicit import and stated separately for "
    "that reason. Every other status states what could not be settled and "
    "must be read as unresolved, not as a negative answer."
)
