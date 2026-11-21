"""What a findings row's `tool` column says about where the finding came from.

Public PR and issue text used to decide this in three separate copies of a
value set, and all three missed ``static:manual`` -- the label of a candidate a
person found by reading the code. Its patch was described as "the
dynamic-analysis run by `static:manual`" directly above a quote that begins
"Static candidate (not executed)".
"""
from __future__ import annotations

import re

# Values a findings CSV puts in its tool column that name how the row was
# gathered, not an analyzer that reported it.
NON_DETECTOR_TOOL_VALUES = frozenset({"web_research", "manual", "manual_review"})

_UNKNOWN_TOOL_VALUES = frozenset({"", "unknown", "none", "n/a"})


def _tokens(tool: str) -> list[str]:
    return [
        token for token in re.split(r"[:/\s]+", str(tool or "").strip().lower())
        if token
    ]


def is_analyzer_tool(tool: str) -> bool:
    """Whether `tool` names an analyzer that reported the finding.

    False for an unknown value and for any label that says a person or a web
    search produced the row, whatever prefix it carries (``static:manual``).
    """
    tokens = _tokens(tool)
    if not tokens or str(tool).strip().lower() in _UNKNOWN_TOOL_VALUES:
        return False
    return not any(token in NON_DETECTOR_TOOL_VALUES for token in tokens)


def is_dynamic_analysis_tool(tool: str) -> bool:
    """Whether the finding was reported by running the app under an analyzer."""
    return is_analyzer_tool(tool) and _tokens(tool)[0] != "static"


ADVISORY_IDENTIFIERS = (
    re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE),
    re.compile(r"\bGHSA(?:-[0-9a-z]{4}){3}\b", re.IGNORECASE),
    re.compile(r"\bOSV-\d{4}-\d{1,6}\b", re.IGNORECASE),
    # GitHub Security Lab's own advisories. Without this pattern the
    # WordPress and Signal findings of run_20260927_175748 (GHSL-2022-046,
    # GHSL-2026-082) were not recognised as naming an advisory, so no
    # research question was asked about them.
    re.compile(r"\bGHSL-\d{4}-\d{2,4}\b", re.IGNORECASE),
)


def advisory_identifiers(error: dict | None) -> list[str]:
    """Published advisories this finding names, in the order they appear.

    A finding does not have to be anchored on a CrySL rule to have an
    authority behind it. When it reports a published advisory, that advisory
    is a primary source: it exists, it is citable, and it states the affected
    versions and the remediation its project shipped.

    The pipeline was not reading them. External research derives its
    questions either from a verified specification or from a getInstance()
    argument heuristic, and a finding that has neither -- a broadcast
    receiver, a path traversal, a Zip Slip -- produced no questions at all
    and skipped research entirely. com.amaze.filemanager's FtpReceiver in
    run_20260909_105409 is the case: its own message reports CVE-2020-35173
    and says the fix introduced a signature-level permission, the
    adjudication then blocked generation on
    "required_authorization_semantics_unknown", and
    external_research.json recorded zero queries.

    Read from the finding's own text rather than from a dedicated column, so
    a finding from any source is covered as long as it names its advisory.
    """
    data = error or {}
    seen: list[str] = []
    for field in ("error_message", "unique_error_id", "reported_violation"):
        text = str(data.get(field, "") or "")
        if not text:
            continue
        for pattern in ADVISORY_IDENTIFIERS:
            for match in pattern.findall(text):
                identifier = match.upper()
                if identifier not in seen:
                    seen.append(identifier)
    return seen


# UI explorers RVSec drives the app with. Findings imported before RVSec was
# named as their tool carry the explorer in the tool column ("ape:sata",
# "monkey:fast"); the checking was RVSec's all the same. The PR text used to
# know only a bare "ape", so those findings reached maintainers with the rule's
# name and nothing about how they were found (session-android#2241,
# rssreader#4, ShortMessageSecure#83).
UI_EXPLORERS = frozenset({
    # ARES: FreeOTP#524 (2026-10-28) went out without naming RVSec because
    # its finding came from an ARES-driven run.
    "ape", "ares", "monkey", "droidbot", "humanoid", "fastbot", "stoat", "sapienz",
    "timemachine", "qtesting", "dqt", "droidmate", "wctester",
})


def is_rvsec_finding(tool: str) -> bool:
    """Whether RVSec reported the finding, under its own name or an explorer's."""
    tokens = _tokens(tool)
    return bool(tokens) and (tokens[0] == "rvsec" or tokens[0] in UI_EXPLORERS)


# Analyzers that observe the running app's calls (RVSec is a JavaMOP monitor).
RUNTIME_MONITORS = frozenset({"rvsec", "javamop"})


def is_runtime_monitor_tool(tool: str) -> bool:
    """Whether a runtime monitor reported the finding, named or through the explorer driving it.

    Narrower than is_dynamic_analysis_tool, which accepts any label not
    starting with "static": `manual_static_review`, `external_advisory`,
    `lint`, `codeql` or `crash_analyzer` (all in findings under outputs/,
    2026-10-30) would pass it. A judgement of what a monitor observes -- the
    detector fact the prompts read, the provider-IV settlement -- needs the
    finding to have come from one.
    """
    tokens = _tokens(tool)
    return (is_analyzer_tool(tool) and bool(tokens)
            and (tokens[0] in RUNTIME_MONITORS or tokens[0] in UI_EXPLORERS))


def analyzer_name(tool: str) -> str:
    """The analyzer to name in public text: RVSec rather than its UI explorer."""
    return "RVSec" if is_rvsec_finding(tool) else str(tool or "").strip()


def tool_and_spec_context(tool: str, spec: str) -> str:
    """One or two plain sentences on how the finding was found and its rule.

    Says what the analysis did, never that the contribution was automated.
    """
    spec = str(spec or "").strip()
    if is_rvsec_finding(tool):
        # No rule id and no "usage rules written in CrySL": network-survey
        # #154 was closed with "It only satisfies the CrySL rule", and the
        # spec id this sentence named reached 48 of 52 drafts of 2026-10-24..31
        # (pr_text.rule_vocabulary_problems).
        return (
            "I found this while running the app under RVSec, a runtime-verification "
            "tool that watches the app's calls to the Java cryptography and TLS APIs "
            "and checks how each sequence of calls uses them."
        )
    return ""
