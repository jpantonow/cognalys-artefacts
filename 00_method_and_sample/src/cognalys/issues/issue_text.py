"""An issue's own sentences, rewritten when they fail the text checks.

The issue a maintainer reads is fixed text the pipeline adds (the greeting,
the sentence naming RVSec, "Thanks!") around four fields of the finding's
presentation: what the code does, why no pull request was opened, the
question and the next step. When the adjudication left them unwritten they
come from post_validation_issue_candidate, whose summary is the
adjudication's own cause sentence. DNS Toggle (2026-11-04) read "Cipher.init
received a key for which the monitor observed no admitted producer ...
[C1, C2, C5; RUN-001, SRC-001, SPEC-001, SPEC-002]" and was held at
publication for evidence ids and pipeline vocabulary, with no step that
could write it again. A pull request's text is rewritten when its check
fails (pr_generator.repair_text); this does the same for an issue, keeping
the facts and changing only the words. The fixed sentences are not the
model's to write and stay as they are.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from cognalys.utils.pr_text import strip_evidence_citations, symbol_phrase, text_problems

# The fields IssueCreator.build_no_safe_fix_body prints between its fixed
# sentences; the title is the template's and is kept.
REWRITTEN_FIELDS = ("finding_summary", "why_no_pr", "maintainer_question", "proposed_next_step")
# The kinds whose body prints those fields (IssueCreator.process); a
# collateral issue's body prints none of them.
PRESENTED_KINDS = frozenset({"no_safe_fix", "generation_blocked"})
PROMPT_VERSION = "issue-text-rewrite-v1"


def issue_title(metadata: Dict, default: str = "") -> str:
    """The title IssueCreator publishes for a candidate."""
    title = str((metadata.get("issue_content") or {}).get("title", default)).strip()
    # A maintainer knows the class, not the JVM's constructor name
    # ("... use in <init>", DSub2000, exp_final_20261019).
    simple_class = str(metadata.get("csv_class", "")).split("$", 1)[0].rsplit(".", 1)[-1]
    if simple_class:
        title = re.sub(r"<(?:cl)?init>", simple_class, title)
    return title


# --issue-title-without-security-impact's default (run_pipeline, the preview
# step and issue_creator).
DEFAULT_TITLE_WITHOUT_SECURITY_IMPACT = (
    "Question about the {api} use in {location} — would you be open to a pull request?")
_SYNTHETIC_METHOD = re.compile(r"[$<>-]|^(?:invoke|invokeSuspend)$")


def contact_issue_title(metadata: Dict, with_security_impact: str,
                        without_security_impact: str) -> str:
    """The title of the contact-first issue of a pull-request draft.

    ``with_security_impact`` (--issue-title) is used only for a draft whose
    record carries impact_class security_impact (prs.impact_class). Any other
    draft, one recorded before the class existed included, gets
    ``without_security_impact`` with {api} (the specification without its
    "Spec" suffix) and {location} filled: the method when it is a plain
    source name, otherwise its outer class (a constructor, a lambda, a
    coroutine or a mangled Kotlin name would put a bytecode name before a
    maintainer).
    """
    if str(metadata.get("impact_class", "")) == "security_impact" or not without_security_impact:
        return with_security_impact
    spec = str(metadata.get("spec", "")).strip()
    api = spec[:-len("Spec")] if spec.endswith("Spec") and len(spec) > len("Spec") else spec
    method = str(metadata.get("method_name", "")).strip()
    location = method
    if not method or _SYNTHETIC_METHOD.search(method):
        location = str(metadata.get("csv_class", "")).split("$", 1)[0].rsplit(".", 1)[-1]
        location = re.sub(r"Kt$", "", location) or location
    return (without_security_impact.replace("{api}", api or "API")
            .replace("{location}", location or "this code"))


def issue_text_problems(metadata: Dict, intro: str = "") -> List[str]:
    """What keeps a candidate's v3 issue text from being published as written.

    The pull request's own check, wording checks included (bytecode names,
    the analyzer's rule ids and vocabulary): unlike the publication hold,
    which leaves them out, these can now be acted on. ``intro`` is the
    greeting the issue step prints first; without it the whole body counts
    toward the summary length, which an issue that short has to meet anyway.
    [] for a kind whose body prints no presentation fields, or a record that
    cannot be built into a body.
    """
    from cognalys.issues.issue_creator import IssueCreator

    if metadata.get("issue_kind") not in PRESENTED_KINDS:
        return []
    try:
        body = IssueCreator.build_no_safe_fix_body(intro, metadata, style="v3")
    except ValueError:
        return []
    spec = str(metadata.get("spec", "")).strip()
    return text_problems(issue_title(metadata), body, str(metadata.get("error_message", "")),
                         specs=[spec] if spec else [], wording_checks=True)


def recorded_evidence(metadata: Dict) -> Dict:
    """The adjudication a candidate's evidence_review file holds ({} when unreadable)."""
    try:
        record = json.loads(Path(str(metadata.get("evidence_review", ""))).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    analysis = record.get("analysis") if isinstance(record, dict) else None
    return analysis if isinstance(analysis, dict) else {}


def _facts(metadata: Dict, evidence_analysis: Dict) -> Dict:
    """What the rewrite may say: the finding as recorded, in its own words."""
    from cognalys.issues.issue_creator import IssueCreator
    from cognalys.llm.fix_generation_common import plain_blocker_reasons
    from cognalys.utils.pr_text import value_problem_reasons

    message = str(metadata.get("error_message", "")).strip()
    try:
        source_file = IssueCreator.source_file_from_metadata(metadata)
    except ValueError:
        source_file = ""
    blockers = [str(item) for item in metadata.get("generation_blockers") or []]
    return {
        "rvsec_message": message,
        "location": symbol_phrase(str(metadata.get("csv_class", "")),
                                  str(metadata.get("method_name", ""))),
        # The analyzer's internal rule id, for context only (the pull
        # request summary's analyzer_rule_id_never_written).
        "analyzer_rule_id_never_written": str(metadata.get("spec", "")).strip(),
        # The adjudication's cause in its own vocabulary: the facts, not
        # the words (DNS Toggle's "no admitted producer").
        "analysis_notes_facts_only": strip_evidence_citations(str(
            metadata.get("confirmed_cause") or evidence_analysis.get("confirmed_cause") or ""
        )).strip(),
        "why_the_value_is_a_problem": value_problem_reasons(evidence_analysis, message),
        # The gates' reasons as a maintainer reads them; only those that
        # stopped this finding (generation_blocked) are known.
        "why_no_pull_request": plain_blocker_reasons(
            blockers, regressions=metadata.get("review_regressions")) if blockers else [],
        "printed_before_your_text": [
            "(a greeting introducing the author)",
            IssueCreator.flagged_sentence(
                str(metadata.get("tool", "")), str(metadata.get("csv_class", "")),
                str(metadata.get("method_name", "")), source_file),
        ],
        "printed_after_your_text": ["Thanks!"],
    }


_INSTRUCTIONS = (
    "Rewrite the four fields of text_to_rewrite, the text of a GitHub issue "
    "about this code, so that it has none of the problems listed in "
    "problems_with_the_text. Keep every fact it states and add none: what "
    "the code does today, why that matters for the app's users (for example "
    "a fixed or all-zero key, or a check that accepts anything), why no pull "
    "request was opened, and the question for the maintainer. "
    "analysis_notes_facts_only and why_no_pull_request are the same facts in "
    "other words; take facts from them, never their wording. When "
    "why_the_value_is_a_problem is not empty, say in plain words why the "
    "reported value is a problem, as those statements give it. "
    "printed_before_your_text and printed_after_your_text are printed around "
    "your text as they are: do not repeat them. When previous_attempt is "
    "present it was rejected for problems_with_the_text; write one that has "
    "none of them.\n\n"
)

_SYSTEM = (
    "You write the text of a GitHub issue as the student who read this code, "
    "in the first person and in the words a developer of this app would use: "
    "short plain sentences, no headings, no lists, the four fields together "
    "under 150 words. finding_summary: two or three sentences on what the "
    "code does and why that is a problem for the app's users; you may quote "
    "the one line at fault in backticks. why_no_pr: one or two sentences, "
    "starting 'I didn't open a pull request because'. maintainer_question: "
    "one direct question a maintainer can answer in a sentence; you may say "
    "the issue can simply be closed if the current behaviour is intentional. "
    "proposed_next_step: one sentence on what you would do with the answer, "
    "or an empty string. RVSec is the analyzer that reported the call; never "
    "describe the work as automated or produced by a tool or pipeline. Refer "
    "to code by its name in the source: a constructor as 'the X constructor', "
    "never <init>, a Kotlin file-level function by its own name, never an "
    "...Kt class, and never a name with '$'. Never write "
    "analyzer_rule_id_never_written or any other rule id, evidence or claim "
    "ids such as C1, RUN-001 or (C-001), the words usage rule, CrySL, "
    "allow-list, compliant, predicate, governed, adjudication, coordinated, "
    "finalized, deterministic, fail-closed or evidence packet, or the "
    "analyzer's own terms such as monitor, admitted producer, required event "
    "or joint finding."
)


def rewrite_issue_text(provider, logger: Optional[logging.Logger], metadata: Dict,
                       evidence_analysis: Optional[Dict] = None, intro: str = "",
                       attempts: int = 2) -> List[str]:
    """Rewrite ``metadata["issue_content"]`` in place when its text fails the checks.

    Returns what is still wrong ([] when the text passes). A rewrite is kept
    only when it passes; otherwise the text stays as it was and the
    publication hold applies as before. Either way metadata records the
    attempt under "issue_text_rewrite": the problems and the text before, or
    the last text tried and what was still wrong with it. ``attempts`` is the
    first rewrite and one retry told the problems of the first.
    """
    logger = logger or logging.getLogger(__name__)
    problems = issue_text_problems(metadata, intro)
    if not problems or provider is None:
        return problems
    from cognalys.llm.llm_schemas import issue_text_schema
    from cognalys.llm.pipeline_common import prompt_json
    from cognalys.llm.response_parsing import ResponseParser

    content = dict(metadata.get("issue_content") or {})
    original = {field: str(content.get(field, "")).strip() for field in REWRITTEN_FIELDS}
    facts = _facts(metadata, evidence_analysis if evidence_analysis is not None
                   else recorded_evidence(metadata))
    before, attempt, remaining = list(problems), None, list(problems)
    for _ in range(max(attempts, 1)):
        payload = {"text_to_rewrite": original, "problems_with_the_text": remaining,
                   **({"previous_attempt": attempt} if attempt else {}), **facts}
        try:
            response = provider.generate(
                _INSTRUCTIONS + prompt_json(payload), _SYSTEM,
                json_mode=True, json_schema=issue_text_schema(),
                schema_name="issue_text_rewrite", trace_stage="issue_text_rewrite",
                trace_context={
                    "finding_id": str(metadata.get("finding_id", "")),
                    # v1: the four presentation fields, from the facts, when
                    # the text fails text_problems (DNS Toggle, 2026-11-04).
                    "prompt_version": PROMPT_VERSION,
                },
            )
        except Exception:  # noqa: BLE001 -- the text is kept and the hold applies
            logger.warning("Issue text rewrite failed for %s", metadata.get("method_name", ""),
                           exc_info=True)
            break
        parsed = ResponseParser.parse_json_object(response) or {}
        attempt = {field: strip_evidence_citations(str(parsed.get(field, ""))).strip()
                   for field in REWRITTEN_FIELDS}
        empty = [field for field in REWRITTEN_FIELDS[:3] if not attempt[field]]
        trial = {**metadata, "issue_content": {**content, **attempt}}
        remaining = ([f"empty {', '.join(empty)}"] if empty else []) + issue_text_problems(
            trial, intro)
        if not remaining:
            metadata["issue_content"] = trial["issue_content"]
            metadata["issue_text_rewrite"] = {
                "prompt_version": PROMPT_VERSION, "problems_before": before,
                "text_before": original,
            }
            logger.info("Rewrote the issue text of %s", metadata.get("method_name", ""))
            return []
    metadata["issue_text_rewrite"] = {
        "prompt_version": PROMPT_VERSION, "problems_before": before,
        "problems_after": remaining, **({"attempted_text": attempt} if attempt else {}),
    }
    logger.info("Issue text of %s still fails its checks: %s",
                metadata.get("method_name", ""), "; ".join(remaining))
    return remaining
