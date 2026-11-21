#!/usr/bin/env python3
"""Whether a finding's value reaches a security-relevant use, within a method.

The adjudicator blocked eu.opencloud.android's getKnownServersStore finding on
"the KeyStore reaches a trust-manager constructor, but the evidence does not
establish that stored certificates affect production TLS validation" -- while
the source proving it was already in the evidence packet, as SRC-002. The
whole chain lives inside one method body:

    final X509TrustManager trustManager =
        new AdvancedX509TrustManager(NetworkUtils.getKnownServersStore(ctx));
    sslContext.init(keyManagers, new TrustManager[]{trustManager}, null);
    final SSLSocketFactory sslSocketFactory = sslContext.getSocketFactory();
    mOkHttpClient = buildNewOkHttpClient(sslSocketFactory, trustManager, ...);

value_usage_graph does not follow it: that is a lexical forward slice over the
whole repository, keyed on symbol names, and the chain here passes through a
local rename (`trustManager`) it has no way to track. Probed against this
finding it reported persistence, network and credential sinks -- all of them
matched in unrelated files -- and nothing on the TLS installation at all.

So this is deliberately the smaller problem: one method body, local
assignments only, no type resolution and no cross-file inference. That bound
is what makes the answer trustworthy enough to cite. It reports a *reached*
verdict only when it can name every step, and says nothing otherwise.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# A sink is a call that puts the value somewhere security-relevant. Each entry
# pairs the call shape with an optional argument requirement, because `init(`
# alone is far too common to mean anything on its own.
SECURITY_SINKS: Dict[str, List[Dict[str, str]]] = {
    "tls_trust_configuration": [
        {"call": r"\.init\s*\(", "requires": r"TrustManager"},
        {"call": r"\bsslSocketFactory\s*\("},
        {"call": r"\bsetSSLSocketFactory\s*\("},
        {"call": r"\bsetDefaultSSLSocketFactory\s*\("},
        {"call": r"\bhostnameVerifier\s*\("},
        {"call": r"\bsetHostnameVerifier\s*\("},
    ],
}

_ASSIGNMENT = re.compile(
    r"^\s*(?:final\s+)?(?:[A-Za-z_$][\w$<>\[\].,?\s]*\s+)?"
    r"(?P<name>[a-z_$][\w$]*)\s*=\s*(?P<value>.+)$"
)
_IDENTIFIER = re.compile(r"\b([A-Za-z_$][\w$]*)\b")

MAX_STEPS = 12


def _arguments_of(line: str, call_end: int) -> str:
    """Text of the argument list whose opening parenthesis precedes call_end."""
    depth, buffer = 1, ""
    for character in line[call_end:]:
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                return buffer
        buffer += character
    return buffer


_METHOD_HEADER = re.compile(
    r"^[ \t]*(?:(?:public|protected|private|static|final|abstract|synchronized"
    r"|native|default|suspend|override|open)\s+)+[\w$<>\[\], .?]*"
    r"[A-Za-z_$][\w$]*\s*\([^;]*$"
)


def enclosing_method_bodies(content: str, seed: str):
    """Yield (first_line, body) for each method in `content` mentioning `seed`.

    The reachability check is only trustworthy because it stays inside one
    method: run over a whole file, a local named `trustManager` in an
    unrelated method would join the chain. This carves the file into method
    bodies by brace depth so that guarantee survives whole-file input.
    """
    lines = content.splitlines()
    index = 0
    while index < len(lines):
        if not _METHOD_HEADER.match(lines[index]):
            index += 1
            continue
        depth, end, opened = 0, index, False
        for position in range(index, len(lines)):
            depth += lines[position].count("{") - lines[position].count("}")
            if "{" in lines[position]:
                opened = True
            if opened and depth <= 0:
                end = position
                break
        else:
            break
        body = "\n".join(lines[index:end + 1])
        if re.search(rf"\b{re.escape(seed)}\b", body):
            yield index + 1, body
        index = max(end + 1, index + 1)


_MEMBER_DECLARATION = re.compile(
    r"\b(?:public|private|protected|static|final|abstract|override|fun)\b"
)


def _passed_identifiers(arguments: str) -> set:
    """Identifiers actually passed, ignoring inline class and lambda bodies.

    A hostname verifier is routinely written inline:

        setHostnameVerifier(new HostnameVerifier() {
            @Override public boolean verify(String host, SSLSession s) {...}
        });

    Reading every identifier in the argument text makes `verify` -- the name
    of a method *declared* there -- look like a value passed in. That was
    four of the five hits in the first repository sweep. A brace block whose
    body declares members is dropped; an array initializer like
    `new TrustManager[]{trustManager}` declares none and is kept, which
    matters because that is the shape this check exists to follow.
    """
    kept, depth, block = [], 0, ""
    for character in arguments:
        if character == "{":
            depth += 1
            if depth == 1:
                block = ""
                continue
        if character == "}":
            depth -= 1
            if depth == 0:
                if not _MEMBER_DECLARATION.search(block):
                    kept.append(block)
                continue
        if depth >= 1:
            block += character
        else:
            kept.append(character)
    return set(_IDENTIFIER.findall("".join(kept)))


def _logical_lines(method_body: str) -> List[str]:
    """One entry per source line, with a wrapped statement joined onto its first.

    The chain this exists to follow is written across two lines in the real
    case:

        final X509TrustManager trustManager = new AdvancedX509TrustManager(
                NetworkUtils.getKnownServersStore(mContext));

    Read a line at a time, the assignment's right-hand side never contains the
    seed and the whole trace stops before it starts. Joining while brackets
    stay open fixes that; entries stay aligned to the original line numbers so
    the evidence still points at real lines, and continuation lines become
    empty rather than being dropped.
    """
    raw = method_body.splitlines()
    joined: List[str] = [""] * len(raw)
    index = 0
    while index < len(raw):
        statement = raw[index]
        depth = statement.count("(") - statement.count(")")
        span = index
        while depth > 0 and span + 1 < len(raw) and span - index < 8:
            span += 1
            statement = f"{statement.rstrip()} {raw[span].strip()}"
            depth += raw[span].count("(") - raw[span].count(")")
        joined[index] = statement
        index = span + 1
    return joined


def reaches_security_sink(
    method_body: str, seed: str, *, source_file_path: str = "",
    first_line: int = 1,
) -> Optional[Dict]:
    """Trace `seed` through local assignments to a security sink.

    `seed` is the symbol the finding is about -- a method name whose result is
    the value in question, or a local variable. Returns a record naming every
    step from the seed to the sink, or None when no sink is reached. Reporting
    nothing is the normal outcome and carries no meaning: this reads one
    method body and follows only what a local assignment makes obvious.
    """
    if not method_body.strip() or not seed:
        return None

    lines = _logical_lines(method_body)
    tainted = {seed}
    steps: List[Dict] = []
    seen_lines = set()

    # A single pass forward is enough for straight-line code, but a value can
    # be assigned before the line that consumes it is itself assigned from, so
    # the set is grown until it stops changing.
    for _ in range(MAX_STEPS):
        grew = False
        for offset, raw in enumerate(lines):
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            match = _ASSIGNMENT.match(line)
            if not match:
                continue
            name = match.group("name")
            if name in tainted:
                continue
            referenced = set(_IDENTIFIER.findall(match.group("value")))
            if not (referenced & tainted):
                continue
            tainted.add(name)
            grew = True
            if offset not in seen_lines:
                seen_lines.add(offset)
                steps.append({
                    "line": first_line + offset,
                    "source_line": line[:200],
                    "binds": name,
                })
        if not grew:
            break

    for family, patterns in SECURITY_SINKS.items():
        for offset, raw in enumerate(lines):
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            for pattern in patterns:
                call = re.search(pattern["call"], line)
                if not call:
                    continue
                arguments = _passed_identifiers(_arguments_of(line, call.end()))
                names = arguments
                if not (names & tainted):
                    continue
                requirement = pattern.get("requires")
                if requirement and not re.search(requirement, line):
                    continue
                # Only the bindings that happen before the sink are part of
                # the chain to it. A later assignment of the same value is
                # true but irrelevant, and listing it as a step overstates
                # what the evidence shows.
                sink_line = first_line + offset
                chain = [item for item in steps if item["line"] <= sink_line]
                return {
                    "status": "reached",
                    "sink_family": family,
                    "source_file_path": source_file_path,
                    "sink_line": sink_line,
                    "sink_source_line": line[:200],
                    "seed": seed,
                    "reached_through": sorted(item["binds"] for item in chain),
                    "steps": chain[:MAX_STEPS],
                    "analysis_kind": "intra_method_local_assignment_chain",
                    "limits": (
                        "Follows local assignments inside one method body. It "
                        "does not resolve types, fields, or calls into other "
                        "methods, so silence is not evidence of no flow."
                    ),
                }
    return None


def sink_reachability_in_source(content: str, seed: str,
                                source_file_path: str = "") -> Optional[Dict]:
    """First security sink `seed` reaches in any single method of `content`."""
    for first_line, body in enclosing_method_bodies(content, seed):
        found = reaches_security_sink(
            body, seed, source_file_path=source_file_path,
            first_line=first_line,
        )
        if found:
            return found
    return None
