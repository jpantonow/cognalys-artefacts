"""What external research is asked for a finding, and the specification
context and policy it is judged under.

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

from cognalys.llm.false_positive_rules import (  # noqa: E402
    _specification_events,
)
from cognalys.llm.pipeline_common import (  # noqa: E402
    POLICY_VERSION,
    canonical_hash,
    false_positive_shapes_reviewed,
)
from cognalys.llm.source_facts import (  # noqa: E402
    governed_api_types,
)


_RESEARCHABLE_DEPENDENCY_STATUSES = frozenset({
    "confirmed", "declared_version_differs", "declared_version_unresolved",
    "used_but_not_declared",
})


def lifecycle_research_questions(dependency_finding: dict | None) -> list[str]:
    """Official-source questions a deprecation or outdated-dependency finding
    makes answerable by naming its own subject.

    Research questions came from a verified specification, a getInstance()
    argument, or a published advisory. Deprecation and outdated-dependency
    findings have none of the three, so all five in run_20260913_004434 --
    AsyncTask twice, LocalBroadcastManager, RxJava 2.2.9 and 2.2.3 -- ran with
    zero queries, and every adjudication then blocked on the absence of an
    official deprecation notice, support status or migration guide. The
    message names the API or the library and its version, and its vendor
    documents exactly those things.
    """
    finding = dependency_finding or {}
    if finding.get("status") not in _RESEARCHABLE_DEPENDENCY_STATUSES:
        return []
    target = finding.get("target") or {}
    target_text = " ".join(
        part for part in (target.get("name", ""), target.get("version", "")) if part
    ) or ", ".join([*(target.get("types") or []), *(target.get("packages") or [])])
    replacement = f" and names {target_text} as the replacement" if target_text else ""
    questions: list[str] = []
    if finding.get("kind") == "dependency_version":
        # Asked about the release line and the Maven group, not the exact
        # version and artifact list: the answer is the same for every patch
        # release of the line, and the same question is then a research cache
        # hit across projects. AmazeFileManager (RxJava 2.2.9) and
        # commons-app (2.2.3) each spent eleven minutes researching the
        # RxJava 2.x end of life in run_20260913_140913. The exact version is
        # already in DEPF for the adjudication.
        groups = sorted({
            str(item.get("group"))
            for item in finding.get("matched_declarations") or []
            if item.get("group")
        })
        for subject in finding.get("subjects") or []:
            name, version = subject.get("name", ""), subject.get("version", "")
            if not name or not version:
                continue
            major = re.match(r"\d+", str(version))
            line = f"{major.group(0)}.x" if major else str(version)
            questions.append(
                f"This finding concerns the {name} {line} release line"
                + (f" (Maven group {', '.join(groups)})" if groups else "")
                + replacement
                + ". Using the library project's own README, release notes, "
                "changelog or official announcements, report: (a) the "
                f"maintenance or support status of the {line} line, with the "
                "date and the announcement that stated it; (b) the latest "
                f"release published in the {line} line; (c) the official "
                f"migration guide from the {line} line"
                + (f" to {target_text}" if target_text else " to its successor")
                + ", with the breaking API and behaviour changes it lists; and "
                "(d) whether the project documents using both lines side by "
                "side during a migration. Report only what official sources "
                "document, and cite each one."
            )
    elif finding.get("kind") == "api_migration":
        for type_name in finding.get("deprecated_types") or []:
            questions.append(
                f"This finding reports {type_name} as deprecated{replacement}. "
                "Using the API vendor's official reference documentation and "
                "release notes, report: (a) the release or API level in which "
                "it was deprecated, quoting the deprecation text; (b) the "
                "replacement that official text recommends; (c) the documented "
                "behaviour of the deprecated API a replacement has to preserve "
                "-- the thread each callback runs on, callback order, "
                "cancellation, and delivery or ordering guarantees; and (d) the "
                "documented equivalents of those guarantees in the recommended "
                "replacement. Cite each source."
            )
        if not finding.get("deprecated_types"):
            for subject in finding.get("subjects") or []:
                if subject.get("name"):
                    questions.append(
                        f"This finding reports the {subject['name']} library as "
                        f"deprecated{replacement}. Using the library's official "
                        "documentation, repository README or announcements, "
                        "report: (a) where and when the deprecation was "
                        "announced, quoting it; (b) the replacement it "
                        "recommends; and (c) the migration guide and the "
                        "behaviour changes it lists. Cite each source."
                    )
    return questions[:3]


def specification_is_externally_anchored(context: dict | None) -> bool:
    """Whether a specification has anything a web search can research.

    True only when the specification's own source was verified and it names
    a governed API type. SQLInjectionSpec satisfied neither: its source_url
    was a CrySL path that answered 404, and its single applies_to_types
    entry, "com.owncloud.android.providers.SQLInjection", is a class that
    does not exist -- the specification's own unknowns call it "an
    unverified package-qualified placeholder derived from the reported class
    namespace". Research built from that asks the open web to confirm a
    fictional type and to assess a named product's behaviour, which is
    unanswerable and, on 2026-09-07, refused by the provider after ten
    minutes of searching.
    """
    data = context or {}
    return bool(data.get("source_verified") is True and governed_api_types(data))


def verified_detector_semantics(context: dict | None) -> dict:
    """What the detector matches, when its own source was verified.

    The research and adjudication requests are deliberately small, so they
    carried the finding and the evidence but not the specification. That cost
    a verdict. agit's RepoNotifications.notifyCompletionWith
    (run_20260910_235224) was adjudicated against Object_MonitorOwner, whose
    specification is source_verified and quotes the monitor's own pointcut,
    and every stage that could have used it was looking elsewhere: the
    research request held no part of the specification and concluded "No
    authoritative specification for the detector identifier
    mop.Object_MonitorOwner was supplied", then spent its web budget on the
    java.lang.Object API docs; the adjudication request held none of it
    either and recorded "whether the local detector ... applies a static
    approximation or different matching rule" as missing evidence, requesting
    the detector's "implementation and call-matching logic" -- which was in
    data/specifications, verified, all along.

    Returns "" -- an empty dict -- for an unverified specification, whose
    matching rule is a generated guess and must not be presented as the
    detector's documented behaviour. What it returns is bounded to the parts
    that describe matching: where the rule came from, what each event matches
    and the call shapes it names, plus the recorded detector semantics.
    """
    data = context or {}
    if data.get("source_verified") is not True:
        return {}
    events = [
        {
            "name": str(event.get("name", "")),
            "matches": str(event.get("condition", "")),
            "api_patterns": [
                str(pattern) for pattern in event.get("api_patterns") or []
            ],
        }
        for event in _specification_events(data)
    ]
    semantics = {
        "specification": str(data.get("spec_id", "")),
        "source_title": str(data.get("source_title", "")),
        "source_url": str(data.get("source_url", "")),
        "source_verified": True,
        "governed_api_types": governed_api_types(data),
        "protocol": str(data.get("protocol", "")),
        "events": events,
    }
    detector_semantics = data.get("detector_semantics")
    if isinstance(detector_semantics, dict):
        semantics["detector_semantics"] = deepcopy(detector_semantics)
    # What the rule gets wrong is part of what the rule is, and this is the
    # half that was still missing. KeyStoreSpec documents Android's
    # AndroidKeyStore as its first known false positive; the adjudication
    # request for leekleak/traffic-light contained no occurrence of the
    # field, confirmed the literal violation, and an issue went on to ask
    # the maintainer whether they would accept a key reset.
    shapes = [
        str(shape) for shape in data.get("known_false_positive_shapes") or []
        if str(shape).strip()
    ]
    # Only shapes the shape review kept are the rule's verified record of
    # what it gets wrong. Before that review a shape may describe code that
    # does break the rule in a harmless context (SSLContextSpec's
    # init(null, ...), which dismissed Aegis's buildSocket), so an
    # unreviewed one goes out under its own name and supports no dismissal.
    if shapes:
        semantics[
            "known_false_positive_shapes" if false_positive_shapes_reviewed(data)
            else "unreviewed_false_positive_shapes"
        ] = shapes
    # Real violations without a security stake are recorded apart from the
    # false positives. MessageDigestSpec filed "MD5 used only as a cache-file
    # key" among them while saying, in the same sentence, that such code
    # still violates the rule, and untis and giggity were dismissed on it.
    non_security = [
        str(shape) for shape in data.get("non_security_violation_shapes") or []
        if str(shape).strip()
    ]
    if non_security:
        semantics["non_security_violation_shapes"] = non_security
    return {key: value for key, value in semantics.items() if value}


def detector_matching_research_question(context: dict | None) -> str:
    """Ask the researcher to settle what the detector matches, not guess it.

    Only for a specification whose source was verified: that source is a
    named, fetchable document, so the question is answerable from a primary
    source. For an unverified one there is nothing to point the researcher
    at, and the question would invite an invention.
    """
    data = context or {}
    if data.get("source_verified") is not True:
        return ""
    url = str(data.get("source_url", "")).strip()
    if not url:
        return ""
    title = str(data.get("source_title", "")).strip() or "this rule's source"
    return (
        f"This finding was reported by the rule recorded in {title}, "
        f"published at {url}. Using that source itself, report the exact "
        "matching rule it applies: which method names and receiver types it "
        "matches, whether it constrains the parameter list of a matched "
        "call, and what condition it then evaluates. State explicitly "
        "whether the rule can match a method that merely shares a name with "
        "the API it governs. Quote the matching clause verbatim and cite the "
        "source. Report only what that source states."
    )


# Asked with the finding, in the research request that is specific to it. The
# shared request answers what the governed API documents; this asks for the
# one thing only the case can raise: an outside standard that fixes the value.
CASE_CONTEXT_QUESTION = (
    "The finding names the reported class, method and detector message. If "
    "those names or the message identify a published standard, protocol, "
    "file format or external service that this use of the governed API must "
    "interoperate with (for example an RFC that fixes an algorithm, or a "
    "server API that expects a particular digest), name it and report what "
    "its official specification requires of the reported value or "
    "parameter, citing it. If they identify none, say so in "
    "externally_unresolved_questions without searching further."
)

# The parts of a specification that define its rule. The rest of the
# specification context is enriched per finding (observed events, parameter
# resolution, detector examples) and would make every shared request unique.
_RULE_DEFINING_FIELDS = (
    "spec_id", "source_url", "protocol", "events", "detector_semantics",
    "api_semantics", "security_semantics", "known_false_positive_shapes",
    "non_security_violation_shapes", "applies_to_types",
)


def shared_research_scope(packet: dict, error: dict,
                          lifecycle_only: bool = False) -> dict:
    """What a finding-independent research request is about.

    Two findings of one specification with one detector message ask the same
    questions and get the same scope, so the second request is a cache hit.
    The rule's version is part of it: a specification that changes is a
    different scope and is researched again.
    """
    if lifecycle_only:
        return {"kind": "dependency_lifecycle"}
    finding = packet.get("finding", {}) or {}
    specification = packet.get("specification_context", {}) or {}
    return {
        "kind": "governed_api_and_rule",
        "specification": str(finding.get("specification", "")),
        "governed_api_types": governed_api_types(specification),
        "reported_violation": str(finding.get("reported_violation", "")),
        "detector_message": str(finding.get("error_message", "")),
        "rule_sha256": canonical_hash({
            field: specification.get(field) for field in _RULE_DEFINING_FIELDS
        }),
    }


_RESEARCH_LISTS = ("queries_performed", "externally_confirmed_facts",
                   "externally_unresolved_questions")


def merge_research_parts(base: dict, results: list) -> dict:
    """One research record from the shared and the case requests.

    Lists are concatenated in request order and sources de-duplicated by
    URL, so WEB-ids still number one list. A part that failed is recorded in
    `parts`; the record carries a failure status only when every part failed,
    which is what the single request recorded before.
    """
    merged = deepcopy(base)
    seen: set[str] = set()
    for result in results:
        for field in _RESEARCH_LISTS:
            merged.setdefault(field, [])
            merged[field].extend(
                item for item in result.get(field) or []
                if item not in merged[field]
            )
        merged.setdefault("sources", [])
        for source in result.get("sources") or []:
            url = str(source.get("url", "")) if isinstance(source, dict) else ""
            if url and url in seen:
                continue
            if url:
                seen.add(url)
            merged["sources"].append(source)
        for key, value in result.items():
            if key not in (*_RESEARCH_LISTS, "sources", "part", "status",
                           "error", "refusal") and key not in merged:
                merged[key] = value
    merged["parts"] = [
        {key: result[key] for key in ("part", "status", "error", "refusal")
         if key in result}
        for result in results
    ]
    failed = [result for result in results if result.get("status")]
    if results and len(failed) == len(results):
        merged["status"] = failed[0]["status"]
        for key in ("error", "refusal"):
            if key in failed[0]:
                merged[key] = failed[0][key]
    return merged


def case_scoped_specification_context(context: dict | None,
                                      finding: dict) -> dict:
    """Drop cross-finding examples and ask what this finding needs answered.

    The questions are about the governed API rather than about the reported
    application; case_scope still records which finding they were built for,
    and the finding itself travels alongside them in the request.
    """
    scoped = deepcopy(context or {})
    generation = scoped.get("generation_evidence")
    if isinstance(generation, dict):
        generation = deepcopy(generation)
        generation.pop("detector_examples", None)
        scoped["generation_evidence"] = generation

    target = finding.get("target", {}) or {}
    types = governed_api_types(scoped)
    governed_label = ", ".join(types) or "the API governed by this specification"
    message = str(finding.get("error_message", "")).strip()
    # These ask about the governed API, not about the reported application.
    #
    # They used to ask whether the path through a named class and method in
    # a named, published app was "feasible at runtime and has
    # security-relevant impact". No web source can answer that -- the same
    # prompt forbids the researcher from inspecting repository behaviour, so
    # the only evidence that could settle it is excluded by construction --
    # and phrased that way it reads as an exploitability assessment of a
    # specific product, which is what a provider refused on 2026-09-07.
    #
    # What the adjudication downstream actually consumes is the API's
    # documented contract: what the specification governs, how the API is
    # meant to be used safely, and what its documentation says about
    # changing that usage. Those are answerable from primary sources, and
    # they are the questions this pipeline needed all along. The finding's
    # own message is still supplied for context, in the finding section of
    # the request, so nothing is hidden from the researcher.
    scoped["unknowns"] = [
        (
            f"What does {governed_label} govern, and which documented API "
            f"event or value does a finding of this kind refer to: {message}"
        ).rstrip(": "),
        (
            f"What do the official sources for {governed_label} document as "
            "its correct and safe usage, and what do they give as the "
            "recommended remediation when it is used otherwise?"
        ),
        (
            f"What do the official sources for {governed_label} document "
            "about compatibility when that usage is changed -- persisted "
            "formats, wire formats, platform version support, and any "
            "documented migration path?"
        ),
    ]
    scoped["case_scope"] = {
        "finding_id": str(finding.get("finding_id", "")),
        "target": target,
        "governed_api_types": types,
    }
    return scoped


class PolicyRegistry:
    """Load only common and applicable spec-family policy documents."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _load(self, name: str) -> dict:
        path = self.root / f"{name}.json"
        if not path.is_file():
            return {"policy_id": name, "version": POLICY_VERSION, "rules": []}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Policy {path} must contain an object")
        return data

    def select(self, family: str, specification: str) -> dict:
        return {
            "registry_version": POLICY_VERSION,
            "specification": specification,
            "family": family,
            "common": self._load("common"),
            "family_policy": self._load(family),
        }
