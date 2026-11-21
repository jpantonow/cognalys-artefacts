"""Deterministic routes to closing a finding as a false positive.

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
from functools import lru_cache
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
    false_positive_shapes_reviewed,
    reported_violation,
    specification_shapes_are_grounded,
)


# Curated, evidence-backed false-positive shapes, read from each
# specification's own data (data/specifications/<spec_id>.json):
#
#   "false_positive_triggers": [{"shape_contains": "...", "detector_message":
#       "...", "external_class_only": true, "requires_source_pattern": "...",
#       "violation": "...", "unmodelled_methods": [...]}]
#
# A trigger names one of the specification's known_false_positive_shapes by
# a fragment of its text, and that shape's own words are the rationale. If a
# specification stops documenting the shape -- a regeneration rewrites it --
# the trigger no longer resolves and the pipeline stops claiming it. Nothing
# here is keyed by a specification's name in code: these tables used to be,
# with the SSLContextSpec message copied from its JSON and the KeyStoreSpec
# methods listed by hand (2026-10-17 audit).
#
# "external_class_only": the shape may only be claimed when the reported class
# is not in the application's own repository, so there is no application
# source to patch either way. That guard suits a shape about how an API is
# *used*; it does not suit a defect in the detector itself, which holds
# wherever the class lives (TreeSet_Comparable's monitor tests the Collection
# argument of addAll instead of its elements).
#
# "requires_source_pattern" keeps a message-only trigger from swallowing a
# real finding: the reviewed source must also show the shape. Without the
# source the shape is not claimed: an unknown is not a false positive.


@lru_cache(maxsize=None)
def _specification(spec_id: str) -> dict:
    from cognalys.workspace import library_data

    try:
        return json.loads((library_data("specifications") / f"{spec_id}.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def false_positive_triggers(spec_id: str, specification: dict | None = None) -> list[dict]:
    """The specification's triggers whose shape it still documents, each with that shape.

    None until the specification's shapes went through the shape review
    (pipeline_common.false_positive_shapes_reviewed): an unreviewed shape may
    describe code that breaks the rule as written, and since P14 it no longer
    supports a dismissal on the adjudication route, so it does not on this
    deterministic one either. Measured on 2026-10-29 over the 1,008 stored
    evidence reviews: 12 were CONFIRMED_FALSE_POSITIVE, and only one of them
    (paperwork's sslContext, SSLContextSpec's getInstance("TLS") message
    trigger, run_20260902_164231) still resolves through a trigger; it now
    stays UNCONFIRMED_FINDING. The three Object_MonitorOwner rows rest on the
    arity route, which reads no shape.
    """
    specification = _specification(spec_id) if specification is None else specification
    if not false_positive_shapes_reviewed(specification):
        return []
    shapes = [shape for shape in specification.get("known_false_positive_shapes") or []
              if isinstance(shape, str)]
    resolved = []
    for trigger in specification.get("false_positive_triggers") or []:
        fragment = str((trigger or {}).get("shape_contains", ""))
        shape = next((text for text in shapes if fragment and fragment in text), "")
        if shape:
            resolved.append({**trigger, "shape": shape})
    return resolved


def _specification_ids() -> list[str]:
    from cognalys.workspace import library_data

    directory = library_data("specifications")
    return sorted(path.stem for path in directory.glob("*.json")) if directory.is_dir() else []


def _curated_messages() -> dict[str, dict[str, dict]]:
    table: dict[str, dict[str, dict]] = {}
    for spec_id in _specification_ids():
        for trigger in false_positive_triggers(spec_id):
            message = str(trigger.get("detector_message", "")).strip()
            if not message:
                continue
            shapes = _specification(spec_id).get("known_false_positive_shapes") or []
            index = shapes.index(trigger["shape"]) if trigger["shape"] in shapes else 0
            entry = {"external_class_only": bool(trigger.get("external_class_only")),
                     "rationale": (f"{trigger['shape']} (see data/specifications/"
                                   f"{spec_id}.json, known_false_positive_shapes[{index}])")}
            if trigger.get("requires_source_pattern"):
                entry["requires_source_pattern"] = str(trigger["requires_source_pattern"])
            table.setdefault(spec_id, {})[message] = entry
    return table


def _unmodelled_call_shapes() -> dict[str, dict]:
    table: dict[str, dict] = {}
    for spec_id in _specification_ids():
        for trigger in false_positive_triggers(spec_id):
            methods = tuple(str(name) for name in trigger.get("unmodelled_methods") or [])
            if methods and trigger.get("violation"):
                table[spec_id] = {"violation": str(trigger["violation"]), "methods": methods}
    return table


KNOWN_FALSE_POSITIVE_MESSAGES: dict[str, dict[str, dict]] = _curated_messages()


def confirmed_false_positive_reason(
    spec_id: str,
    error_message: str,
    external_class_confirmed: bool,
    app_source_file_path: str,
    source: str = "",
) -> str:
    """Match a finding against a curated, documented false-positive shape.

    The exact detector message must match an entry the registry above
    describes, and that entry's own conditions must hold: an
    "external_class_only" shape is claimed only when the reported class is
    absent from the application's repository, and a shape with a
    "requires_source_pattern" is claimed only when the reviewed source shows
    it. Returns the rationale text, or "" when no curated shape matches.
    """
    entry = KNOWN_FALSE_POSITIVE_MESSAGES.get(spec_id, {}).get(
        error_message.strip()
    )
    if not isinstance(entry, dict):
        return ""
    if entry.get("external_class_only") and (
        not external_class_confirmed or app_source_file_path.strip()
    ):
        return ""
    pattern = str(entry.get("requires_source_pattern", ""))
    if pattern and not re.search(pattern, source or ""):
        return ""
    return str(entry.get("rationale", ""))


# Calls a specification's protocol does not model, by the violation type they
# provoke. A CrySL order grammar reports InvalidSequenceOfMethodCalls when it
# meets a call it has no event for, so valid code using a documented but
# unmodelled API looks like a broken sequence.
#
# Only the *predicate* lives here. The rationale is the specification's own
# known_false_positive_shapes text, quoted at match time: if a spec stops
# documenting a shape, the pipeline stops claiming it. This code may not
# assert a false positive on its own authority.
UNMODELLED_CALL_SHAPES = _unmodelled_call_shapes()


def unmodelled_calls_in_source(spec_id: str, source: str) -> list[str]:
    """Which of the specification's unmodelled calls this source makes.

    The detector tracks the object across the whole class, not just the
    reported method -- opencloud's setCertificateEntry sits in a different
    method of the same file from the reported getKnownServersStore -- so the
    caller supplies the file, not the method body.
    """
    shape = UNMODELLED_CALL_SHAPES.get(spec_id)
    if not shape or not source:
        return []
    return [
        name for name in shape["methods"]
        if re.search(rf"\.\s*{re.escape(name)}\s*\(", source)
    ]


def unmodelled_call_false_positive_reason(
    spec_id: str,
    reported_violation: str,
    observed_calls: list | None,
    specification_context: dict | None,
) -> str:
    """The specification's own words for why this shape is a false positive.

    Returns "" unless all of it holds: the specification has a documented
    shape for the reported violation, the source really makes one of the
    unmodelled calls, and the specification's known_false_positive_shapes
    names that call. The last condition is what keeps the claim anchored to
    the project's documentation rather than to this table.
    """
    shape = UNMODELLED_CALL_SHAPES.get(spec_id)
    if not shape or not observed_calls:
        return ""
    if reported_violation.strip() != shape["violation"]:
        return ""
    if not specification_shapes_are_grounded(specification_context):
        return ""
    documented = (specification_context or {}).get(
        "known_false_positive_shapes"
    ) or []
    for call in observed_calls:
        for text in documented:
            if isinstance(text, str) and call in text:
                return text
    return ""


# An AspectJ-style matcher written over a name and an unconstrained parameter
# list -- `call(* Object+.notify(..))` -- matches every method that happens to
# share the name, on every receiver, whatever its arity. When the API the
# specification actually governs declares that method with no parameters at
# all, a matched call that passes arguments cannot be it: the monitor matched
# a different method that merely shares a name.
#
# Object_MonitorOwner is that shape. Its verified source matches
# `call(* Object+.notify(..)) || call(* Object+.notifyAll(..))` and guards on
# `!Thread.holdsLock(o)`, while java.lang.Object declares notify() and
# notifyAll() with no parameters. Every Android app that dispatches a
# notification calls NotificationManager.notify(int, Notification) without
# synchronizing on the manager, so the monitor reports it: 38 of the 55 rows
# in data/processed/exp01_novo.csv are this specification, and their reported
# methods are named createNotification, showNotification, updateNotification
# and the like.
#
# The predicate lives here; the rationale is quoted from the specification at
# match time, like every other route in this module. Nothing about Object or
# Android is hardcoded -- the governed arity is read from the specification's
# own api_patterns and the unconstrained matcher from its own event
# conditions -- so this closes a finding only for a specification whose
# verified source documents both.
_UNCONSTRAINED_ARITY_MATCHER = re.compile(r"\(\s*\.\.\s*\)")


_PARAMETERLESS_API_PATTERN = re.compile(r"^\.?([A-Za-z_$][\w$]*)\s*\(\s*\)$")


def _specification_events(specification_context: dict | None) -> list[dict]:
    events = (specification_context or {}).get("events") or []
    return [item for item in events if isinstance(item, dict)]


def governed_call_names(specification_context: dict | None) -> list[str]:
    """Every method name the specification's own events name.

    Read from each event's ``api_patterns`` -- ``KeyStore.getInstance(``,
    ``.load(``, ``.store(`` -- so a specification that documents what it
    governs supplies its own matcher and one that documents nothing supplies
    none. This exists because the only thing in the pipeline that correlated
    evidence against source did it with three crypto factory names written
    into a regex, which no KeyStore, MOP, advisory or dependency finding
    could ever satisfy.
    """
    names: list[str] = []
    for event in _specification_events(specification_context):
        for raw in event.get("api_patterns") or []:
            pattern = str(raw).strip()
            if not pattern.endswith("("):
                continue
            name = pattern[:-1].strip().rsplit(".", 1)[-1].strip()
            if name and name.isidentifier():
                names.append(name)
    return list(dict.fromkeys(names))


def evidence_correlates_with_source(
    evidence: str,
    source_corpus: str,
    specification_context: dict | None = None,
) -> bool:
    """Whether a stated piece of evidence is visible in the reviewed source.

    This decides whether a violated predicate is grounded enough to reach a
    maintainer, and it used to ask that question with a fixed alternation of
    MessageDigest, Cipher and SSLContext factory calls. Everything else --
    a hard-coded KeyStore password, a monitor property, an advisory, a
    dependency coordinate -- failed to correlate for the single reason that
    its API was not one of those three, and the issue was dropped without a
    word.

    Three ways to correlate, in decreasing strength: the evidence occurs in
    the source; a call of a method the specification itself governs occurs in
    both; or a string literal long enough to be distinctive occurs quoted in
    both. Evidence commonly appends an explanatory sentence to an exact
    source expression, which is why the whole string matching is not enough
    on its own.
    """
    evidence_text = str(evidence or "").strip()
    if len(evidence_text) < 4:
        return False
    corpus = _normalised(source_corpus)
    if not corpus:
        return False
    compact = _normalised(evidence_text)
    if compact in corpus:
        return True

    for name in governed_call_names(specification_context):
        pattern = re.compile(
            r"[A-Za-z_$][\w$.]*\s*\.\s*" + re.escape(name) + r"\s*\([^)]{0,160}\)"
        )
        for match in pattern.finditer(evidence_text):
            if _normalised(match.group(0)) in corpus:
                return True

    # A literal is what most constraint violations are actually about: the
    # algorithm name, the protocol, the hard-coded password. Three characters
    # is short enough to keep "MD5" and long enough that a quoted fragment is
    # not matching on punctuation.
    for literal in re.findall(r'"([^"\n]{3,120})"', evidence_text):
        if f'"{literal.lower()}"' in corpus:
            return True
    return False


def parameterless_governed_methods(
    specification_context: dict | None,
) -> list[str]:
    """Method names the specification documents as taking no arguments.

    Read from the specification's own ``api_patterns``: ``.notify()`` states
    an empty parameter list, while an open-ended ``.wait(`` states nothing
    about arity. A name written both ways is dropped -- java.lang.Object
    declares wait(), wait(long) and wait(long, int), and Object_MonitorOwner
    records exactly that by listing both spellings, so an argument-carrying
    wait() call proves nothing about the detector's matching.
    """
    declared: list[str] = []
    open_ended: set[str] = set()
    for event in _specification_events(specification_context):
        for raw in event.get("api_patterns") or []:
            pattern = str(raw).strip()
            match = _PARAMETERLESS_API_PATTERN.match(pattern)
            if match:
                declared.append(match.group(1))
                continue
            if pattern.endswith("("):
                name = pattern[:-1].strip().rsplit(".", 1)[-1].strip()
                if name:
                    open_ended.add(name)
    return [
        name for name in dict.fromkeys(declared) if name not in open_ended
    ]


def unconstrained_arity_matcher(specification_context: dict | None) -> str:
    """The specification's own text showing it matches calls of any arity.

    Returns the first event condition that spells out an unconstrained
    parameter list, or "". Without one the detector cannot have matched a
    call of a different arity, and no overmatch may be claimed.
    """
    for event in _specification_events(specification_context):
        condition = str(event.get("condition", ""))
        if _UNCONSTRAINED_ARITY_MATCHER.search(condition):
            return condition.strip()
    return ""


def _calls_by_name(source: str, name: str) -> tuple[list[str], bool]:
    """Calls of ``name`` in ``source``: those with arguments, and whether any
    has none.

    Only the first non-space character after the opening parenthesis decides,
    so a nested call or a parenthesis inside a string literal cannot confuse
    it. A receiver expression is required, which keeps a declaration of the
    same name out of the result.
    """
    with_arguments: list[str] = []
    parameterless = False
    pattern = re.compile(
        r"([A-Za-z_$][\w$.]*)\s*\.\s*" + re.escape(name) + r"\s*\("
    )
    for match in pattern.finditer(source or ""):
        if source[match.end():].lstrip().startswith(")"):
            parameterless = True
        else:
            with_arguments.append(f"{match.group(1)}.{name}(...)")
    return with_arguments, parameterless


def detector_arity_overmatch_reason(
    specification_context: dict | None,
    source: str,
) -> str:
    """Why an arity-unconstrained matcher reported a same-named other method.

    Returns "" unless all of it holds: the specification's source was
    verified, its own event condition matches calls of any arity, it declares
    the governed method with no parameters, the reviewed source calls that
    name with arguments, and it makes no parameterless call of that name
    anywhere -- because a single genuine governed call is enough to make the
    report an unknown rather than an overmatch.
    """
    context = specification_context or {}
    if context.get("source_verified") is not True:
        return ""
    condition = unconstrained_arity_matcher(context)
    if not condition or not source:
        return ""
    for name in parameterless_governed_methods(context):
        with_arguments, parameterless = _calls_by_name(source, name)
        if parameterless or not with_arguments:
            continue
        observed = ", ".join(dict.fromkeys(with_arguments))
        return (
            "The specification's verified source matches this event as \""
            + condition
            + "\", which constrains neither the receiver type nor the "
            "parameter list, while the API it governs declares "
            f"{name}() with no parameters -- the specification's own "
            f"api_patterns record it as \".{name}()\". The reviewed source "
            f"calls {observed} with arguments and makes no parameterless "
            f"{name}() call, so the matched call cannot be the governed "
            f"{name}(): the detector matched a different method that shares "
            "its name."
        )
    return ""


# Retired route (2026-10-28): a rejected value that appeared as a word in one
# of the specification's known_false_positive_shapes closed the finding as a
# false positive, even against a confirmed violation, without checking the
# condition the shape describes (KeyStoreSpec's AndroidKeyStore; fread's
# getInstance("SSL") once matched the shape written for "TLS"). That is
# deterministic triage of a known false-positive pattern, which the project
# does not do.


def false_positive_rationale(
    spec_id: str,
    error_message: str,
    external_class_confirmed: bool,
    app_source_file_path: str,
    reported_violation: str = "",
    observed_calls: list | None = None,
    specification_context: dict | None = None,
    source: str = "",
) -> str:
    """Every documented route to closing a finding as a false positive.

    The curated-message route needs the detector's exact text, and 95 of the
    133 sampled candidates report "unknown" -- every one of them an
    InvalidSequenceOfMethodCalls -- so it was unreachable for the majority of
    the dataset. The unmodelled-call route reads the calls observed in the
    source instead of the detector message. The arity route reads the
    specification's own matcher together with the source, so it needs
    neither a curated message nor a curated shape.
    """
    curated = confirmed_false_positive_reason(
        spec_id, error_message, external_class_confirmed,
        app_source_file_path, source,
    )
    if curated:
        return curated
    unmodelled = unmodelled_call_false_positive_reason(
        spec_id, reported_violation, observed_calls, specification_context
    )
    if unmodelled:
        return unmodelled
    return detector_arity_overmatch_reason(specification_context, source)


def _states_cause_status(analysis: dict, evidence_status: dict) -> bool:
    """Whether this record says anything at all about cause_status.

    A record that states it is judged on what it states. Only one that omits
    the field entirely -- written before it existed -- falls back to reading
    a non-empty confirmed_cause as the confirmation it used to imply.
    """
    return "cause_status" in (analysis or {}) or "cause_status" in (
        evidence_status or {}
    )


def blocked_finding_review_status(
    analysis: dict,
    spec_id: str = "",
    error_message: str = "",
    app_source_file_path: str = "",
    reported_violation: str = "",
    observed_calls: list | None = None,
    specification_context: dict | None = None,
    source: str = "",
) -> str:
    """Classify a blocked finding without conflating cause and violation.

    A confirmed root cause proves repository behavior, but it does not by
    itself prove that the detector predicate was violated. Keep that
    distinction in presentation-only status labels; this helper deliberately
    does not authorize generation or publication.

    ``spec_id``, ``error_message`` and ``app_source_file_path`` are optional:
    when supplied, a finding that would otherwise stay UNCONFIRMED_FINDING
    forever can instead be closed as CONFIRMED_FALSE_POSITIVE when it matches
    a curated shape from ``KNOWN_FALSE_POSITIVE_MESSAGES``. Omitting them
    preserves the previous behavior exactly.
    """
    evidence_status = analysis.get("evidence_status", {}) or {}
    detector_violation = str(
        analysis.get(
            "detector_violation",
            evidence_status.get("detector_violation", "unknown"),
        )
    )
    cause_status = str(
        analysis.get("cause_status", evidence_status.get("cause_status", "unknown"))
    )
    confirmed_cause = str(analysis.get("confirmed_cause", "")).strip()
    if detector_violation in {"confirmed", "source_established"}:
        return "CONFIRMED_FINDING_NO_SAFE_FIX"
    if cause_status == "confirmed":
        return "CONFIRMED_CAUSE_ONLY_NO_SAFE_FIX"
    # A non-empty confirmed_cause used to be read here as a confirmed cause
    # in its own right, which is only true of a record written before
    # cause_status existed. The adjudication fills that field with prose
    # whatever it concludes, and the prose is regularly a *negative*: agit's
    # RepoNotifications.notifyCompletionWith in run_20260910_235224 carried
    # cause_status "unknown" beside "No source-level cause for an Object
    # monitor-ownership violation is confirmed: ...". The string is truthy,
    # so the finding was labelled CONFIRMED_CAUSE_ONLY_NO_SAFE_FIX while the
    # preview rendered "Cause: unknown" two lines below it, and the false
    # positive test further down was unreachable for every finding the
    # adjudication bothered to explain.
    if confirmed_cause and not _states_cause_status(analysis, evidence_status):
        return "CONFIRMED_CAUSE_ONLY_NO_SAFE_FIX"
    dependency_assessment = analysis.get("dependency_fix_assessment") or {}
    # Reached only after a confirmed detector violation and a confirmed cause
    # have both been ruled out, so this rescues findings that would otherwise
    # be UNCONFIRMED_FINDING -- "we could not tell" -- and never overrides a
    # positive conclusion.
    if false_positive_rationale(
        spec_id, error_message,
        bool(dependency_assessment.get("external_class_confirmed")),
        app_source_file_path,
        reported_violation, observed_calls, specification_context, source,
    ):
        return "CONFIRMED_FALSE_POSITIVE"
    return "UNCONFIRMED_FINDING"


# Phrases that hand a requirement about the reviewed code back to the
# detector. Recognised so an extraction written before `unknown_reason`
# existed, or one that routes around it in prose, is still named rather than
# absorbed. This only annotates; it never decides a predicate.
_DETECTOR_DEFERRAL = re.compile(
    r"(?:the\s+)?detector(?:'s)?\s+(?:own\s+)?"
    r"(?:treatment|modelling|modeling|behaviou?r|classification|"
    r"approximation|handling|resolution)"
    r"|does not (?:define|establish|specify) (?:the )?detector"
    r"|(?:how|whether) the detector (?:models|treats|recognizes|recognises)",
    re.IGNORECASE,
)


def predicate_deferred_to_detector(predicate: dict | None) -> bool:
    """An `unknown` whose stated reason is what the detector would do.

    The predicate asks whether a requirement holds in the reviewed code.
    Whether the detector models that requirement decides whether it would be
    reported, which `detector_violation` already answers. An unknown resting
    on the second question is not a gap in the evidence, and treating it as
    one is how a hard-coded keystore password was gathered, written into the
    analysis and then dropped.
    """
    entry = predicate or {}
    if str(entry.get("status", "")).strip() != "unknown":
        return False
    if str(entry.get("unknown_reason", "")).strip() == "detector_modelling":
        return True
    return bool(_DETECTOR_DEFERRAL.search(str(entry.get("explanation", ""))))
