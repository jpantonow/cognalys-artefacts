#!/usr/bin/env python3
"""What may be done with a finding: apply it, publish it, or neither.

Three questions used to be answered independently by four modules, each
writing its own boolean expression over the same three metadata flags
(manual_review_required, auto_apply, publication_allowed). The expressions
disagreed, and one of the disagreements was load-bearing:

    # pr_generator.py
    review_only = (manual_review_required is True
                   or publication_allowed is False
                   or auto_apply is False)

    # solution_applicator.py
    review_only = (manual_review_required is True
                   or auto_apply is False)
                   # publication_allowed deliberately absent

That omission encoded the most important rule in the pipeline -- a blocked
publication must still be applied and built, because the block is on handing
the patch to a maintainer, not on compiling it -- and it encoded it as a
*missing clause*, written down nowhere. Setting manual_review_required for a
publication-blocked finding therefore looked obviously correct and silently
stopped the applicator: run_20260908_154553 generated a 90%-confidence Zip
Slip fix and discarded it before any build.

The flags were also built in two places 200 lines apart in
generate_error_fixes.py, so a fix to the publication gate landed in
one of them and not the other -- caught by a test, not by review.

This module answers the three questions once, by name, from the two facts
that actually decide them: the adjudicated decision and the adjudicated
publication status.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Decisions whose patch is never applied without a human reading it first.
# Kept here rather than in the LLM layer because the applicator, the PR
# writers and the preview generator all need it and none of them is an LLM
# concern. llm.semantic_validation re-exports it for its existing callers.
REVIEW_ONLY_DECISIONS = frozenset({
    "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW",
    "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY",
    "MULTI_REPOSITORY_MIGRATION_SUPPORTED",
})

# The rest of the decision vocabulary, named once. The adjudication validator
# and the orchestrator each spelled these sets out as literals, so a decision
# one of them did not list fell into whichever branch its author had not
# considered. Together with NO_SAFE_FIX they partition the schema's decisions.
#
# Decisions that authorise a patch applied without a human reading it first.
REPAIR_DECISIONS = frozenset({
    "FIX_SUPPORTED", "MULTI_FILE_FIX_SUPPORTED",
    "SAFE_INTERNAL_DIGEST_MIGRATION",
    "DEPENDENCY_FIX_SUPPORTED", "DEPENDENCY_REPLACEMENT_SUPPORTED",
})

# Decisions under which a patch is generated at all.
GENERATING_DECISIONS = REPAIR_DECISIONS | REVIEW_ONLY_DECISIONS

# Decisions whose change can legitimately be a single edit: a dependency
# bump is one declaration, a backend migration is reviewed whole.
_SINGLE_EDIT_DECISIONS = frozenset({
    "DEPENDENCY_FIX_SUPPORTED", "DEPENDENCY_REPLACEMENT_SUPPORTED",
    "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY",
})


def minimum_coordinated_edits(
    decision: str, planned_edit_count: int, fix_scope: str = "",
) -> int:
    """How many edits a coordinated patch must carry to be accepted.

    One rule, for the generator and the applicator alike. They each used to
    spell it out, and when the generator's copy was changed to follow the
    authorised plan the applicator's kept a fixed floor of two: einkbro's
    restoreLegacyBackupData (run_20260920_175850) was generated as a valid
    one-edit review-only patch and then refused with
    INVALID_COORDINATED_EDIT_COUNT before it could be built.

    The floor follows the plan, capped at two, so a decision that
    auto-applies still has to show two coordinated edits when its plan asks
    for two or more, and a plan requiring one edit is satisfied by one. A
    review-only decision needs only one, because a human reads it before
    anything is applied. An empty plan authorises nothing and keeps the
    strictest floor.
    """
    if decision in _SINGLE_EDIT_DECISIONS or fix_scope == "dependency":
        return 1
    if planned_edit_count <= 0:
        return 2
    ceiling = 1 if decision in REVIEW_ONLY_DECISIONS else 2
    return min(ceiling, planned_edit_count)

# Decisions that confirm a change is needed and name the authorisation it
# waits for, without generating it.
POLICY_DECISIONS = frozenset({
    "MULTI_FILE_FIX_REQUIRES_POLICY", "DEPENDENCY_FIX_REQUIRES_POLICY",
    "UPSTREAM_FIX_REQUIRES_POLICY",
})


def external_contract_decision(decision) -> bool:
    """Whether ``decision`` says a counterpart outside the repository fixes
    what the finding is about (EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX
    today): the decisions reporting.finding_statistics has always labelled
    "contrato externo impede a correção"."""
    text = str(decision or "").upper()
    return text.startswith("EXTERNAL_") or "CONTRACT" in text

# Decisions that classify a confirmed finding instead of changing it.
CLASSIFICATION_DECISIONS = frozenset({
    "EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX",
    "NON_SECURITY_HASH_USAGE",
    "CONFIRMED_SPEC_VIOLATION_NON_SECURITY_USAGE",
})

# The adjudication's verdict that something it could not settle must be
# settled before a maintainer sees this. Anything else -- including an absent
# value, which is what a record written before the field existed carries --
# leaves publication on the decision alone.
PUBLICATION_BLOCKED = "blocked"

# Build-validation results that let a patch proceed. The same divergence this
# module was written to end had happened again, over this set:
#
#   validate_applied_solutions.py  {"success", "success_app_module_only"}
#   pr_creator.py                  {"success", "success_app_module_only"}
#   pr_generator.py                build_status != "success"
#
# "success_app_module_only" is the narrower pass recorded by
# rescue_unrelated_module_failure: the full build failed on a task in a module
# the patch never touched, or in a product flavor validation does not build,
# and the module(s) actually containing the patch were then recompiled in
# isolation and succeeded. pr_creator documents at length why it accepts that
# status and then requires a commit hash -- which pr_generator, holding the
# stricter rule, never produced for it. The acceptance branch was unreachable,
# and hanjoongcho/aaf-easydiary's Zip Slip fix in run_20260908_212305 sat in
# its worktree uncommitted because an unselected gmsDev flavor is missing two
# drawable resources.
#
# It is still never conflated with a real "success": callers that report what
# was verified must say which of the two they got.
# "success_on_analyzed_revision": the base branch fails to build with or
# without the patch, and the patch compiled on the revision the finding was
# analysed at (validation.applied_solutions.run_analyzed_revision_build).
# "success_production_only_test_build_preexisting": the production build
# passed; the module's test build fails at the same tasks on the base commit,
# before any test compiles, and the tests the patch created there were removed
# (validation.applied_solutions.settle_test_build_failing_on_base). Only
# production code was verified, and the PR text says no more than that.
# Named once here, since the reports and the PR text compare against it.
PRODUCTION_ONLY_TEST_BUILD_PREEXISTING = "success_production_only_test_build_preexisting"
# "success_compile_only": the same compile-only recheck as
# "success_app_module_only", in a project whose only Gradle module is the
# patched one. Everything compiled; no APK was assembled.
COMPILE_ONLY_PASS = "success_compile_only"
BUILD_VALIDATION_PASSING_STATUSES = frozenset({
    "success", "success_app_module_only", "success_on_analyzed_revision",
    PRODUCTION_ONLY_TEST_BUILD_PREEXISTING, COMPILE_ONLY_PASS,
})

# Build results in which the patch was never compiled at all: something about
# the machine or the repository stopped the build before it could be. These
# are the only results an explicit publish-without-validation approval may
# cover, and the distinction is the whole safety of that approval.
#
# "failed" is deliberately absent, and must stay absent. It means the patch
# itself did not compile, and a patch that does not compile must never reach
# a maintainer however loudly its publication was approved. "timeout" is
# absent too: it does not say which of the two happened.
#
# gtalksms is the case this exists for. Its .gitmodules declares
# git://github.com/JakeWharton/ActionBarSherlock.git/actionbarsherlock, whose
# path segment after .git is not a repository on any protocol, so the build
# cannot run on any machine and no amount of fixing the patch changes that.
BUILD_NEVER_RAN_STATUSES = frozenset({
    "infrastructure_failure", "not_run",
})

# A failed build that says as little about the patch as one that never ran:
# the unmodified base commit fails the same way (MetadataRemover's
# :app:hiltJavaCompileDebug on any JDK), so the repository cannot be built
# here at all. Still never a pass; only an explicit human approval of that
# finding may publish it, as for BUILD_NEVER_RAN_STATUSES.
BUILD_FAILURES_PREDATING_THE_PATCH = frozenset({
    "verified_preexisting_build_failure", "preexisting_build_configuration",
})


def build_says_nothing_about_patch(build_validation: dict | None) -> bool:
    """Whether the build never ran, or failed exactly as the base commit does.

    A failure counts only when it is attributed to the base commit and the
    patched build reported no compiler diagnostic the base build did not:
    a patch that adds a compile error is never covered.
    """
    validation = build_validation or {}
    status = str(validation.get("status", "")).lower()
    if status in BUILD_NEVER_RAN_STATUSES:
        return True
    return (
        status == "failed"
        and validation.get("failure_attribution") in BUILD_FAILURES_PREDATING_THE_PATCH
        and not validation.get("new_compiler_diagnostics")
    )


def files_the_build_does_not_compile(changed_files) -> list[str]:
    """Changed production files in source sets an Android build never compiles."""
    from pathlib import PurePosixPath

    from cognalys.extraction.extract_methods_single_csv import android_source_rank
    return [str(path) for path in changed_files or []
            if android_source_rank(PurePosixPath(str(path))) == 2
            and not re.search(r"(?i)(^|/)(test|androidTest|\w+Test)/", str(path))]


# What publication_after_validation does with each adjudication blocker
# category (llm_contracts.BLOCKER_CATEGORIES). A category it does not know,
# and uncategorised `other`, blocks: the hfut-schedule case
# (adjudication_rules.human_decided_publication_considerations).
_EVIDENCE_GAP_CATEGORIES = frozenset({"cause_unconfirmed", "code_evidence_missing", "target_unresolved"})
_NOTE_CATEGORIES = frozenset({
    "maintainer_decision", "runtime_behavior", "analyzer_reconfirmation", "rename_authorization",
})
# Conservative: the review cannot see data written by older versions.
_BLOCKING_CATEGORIES = frozenset({"compatibility_contract"})
# Decisions whose own gates (dependency coordinates, a backend migration)
# this verdict does not replace.
_DECISIONS_KEPT_FOR_THEIR_GATES = frozenset({
    "DEPENDENCY_FIX_SUPPORTED", "DEPENDENCY_REPLACEMENT_SUPPORTED",
    "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY", "MULTI_REPOSITORY_MIGRATION_SUPPORTED",
})
# A generated test in the wrong place blocks; a missing test is a note.
TEST_PLACEMENT_CODES = frozenset({"TEST_PLACED_IN_UNRELATED_FILE", "TEST_IN_OTHER_MODULE"})
# A checklist line asking for something only a person or a device settles is
# never discharged by the build.
_NOT_SETTLED_BY_A_BUILD = re.compile(
    r"(?i)device|emulator|instrument|endpoint|server|handshake|maintainer|manual|human"
    r"|analy[sz]er|rvsec|dynamic|on a phone|real network")
_VALUE_LITERAL = re.compile(r"\"((?:[^\"\\\n]|\\.)*)\"")


def _value_literals(code: str) -> set:
    """String literals that look like an algorithm, transformation or provider name."""
    found = set()
    for text in _VALUE_LITERAL.findall(code or ""):
        if (2 <= len(text) <= 40 and " " not in text and re.fullmatch(r"[A-Za-z][\w/.:+-]*", text)
                and (re.search(r"[\d/:-]", text) or text.isupper()
                     or re.search(r"[a-z][A-Z]", text))):
            found.add(text)
    return found


def _settled_by_validation(line: str, build_validation: dict) -> bool:
    """Whether a plan checklist line asks only for what the passing validation already ran."""
    if _NOT_SETTLED_BY_A_BUILD.search(line):
        return False
    phases = build_validation.get("phases") or {}
    wanted = []
    if re.search(r"(?i)\b(?:build|compil\w*|assembl\w*|gradle)\b", line):
        wanted.append(str(build_validation.get("status", "")).lower()
                      in BUILD_VALIDATION_PASSING_STATUSES)
    if re.search(r"(?i)\bunit[- ]?tests?\b|\btest (?:task|suite)s?\b|\bandroidtest\b", line):
        wanted.append(str((phases.get("patched_tests") or {}).get("status", "")) == "success")
    if re.search(r"(?i)\bre-?check|\bsource check", line):
        wanted.append(str((build_validation.get("detector_replay") or {}).get("outcome", ""))
                      == "repaired")
    return bool(wanted) and all(wanted)


def repaired_patch_review_blocks(record: dict) -> list[str]:
    """What the behaviour review of a repaired patch holds back, whatever the
    draft's decision.

    validation.repair re-reviews a patch whose build repair edited a file the
    plan did not include (BlueGate's Authenticator, 2026-10-30) and records
    that review in `repair_behavior_review`. The PR step applied the verdict
    after validation only to review-only drafts, and pr_creator and the
    statistics never read it, so a publishable draft whose repaired patch
    regressed, had no effect or was never reviewed still came out
    READY_FOR_PUBLICATION (review of 2026-10-31). The edits beyond the plan
    were never judged by the draft-time review, so no approval or decision
    stands in for this one: pr_generator, pr_creator and the statistics all
    hold the draft on it.
    """
    from cognalys.llm.fix_artifacts import REVIEWED_STATUSES

    review = (record or {}).get("repair_behavior_review")
    # Only record_repair_review writes this review, and only for a repair
    # that edited files beyond the plan; with none there is nothing the
    # draft-time review did not judge. BlueGate run_v7 (2026-10-31) built on
    # the first try, no repair ran, and the PR record's empty
    # repair_behavior_review ({}) read as a review that never ran: the
    # statistics held the draft on "no behaviour review judged the repaired
    # patch's edits of " with no file named.
    if not isinstance(review, dict) or not review.get("files_beyond_plan"):
        return []
    files = ", ".join(str(path) for path in review.get("files_beyond_plan") or [])
    blocks = []
    if (str(review.get("status", "") or "not_run") not in REVIEWED_STATUSES
            or review.get("review_contract_version") is None):
        blocks.append(f"no behaviour review judged the repaired patch's edits of {files}")
    regressions = [item for item in review.get("regressions") or [] if item]
    if regressions:
        blocks.append(f"the behaviour review of the repaired patch found "
                      f"{len(regressions)} regression(s)")
    if review.get("patch_has_no_effect") and not review.get("explicit_default_only"):
        blocks.append("the behaviour review of the repaired patch found it has no effect")
    return blocks


def publication_after_validation(record: dict) -> dict:
    """The publication verdict once the patch is committed, built and reviewed.

    Publishability was fixed at adjudication time, before any patch existed:
    every draft generated for review stayed REVIEW_ONLY_NOT_PUBLISHABLE,
    though Orgzly's, Fread's, Salty's and SMSecure's carried a current
    behaviour review with no regression, and stale checklist lines ("run
    the patched module build") stayed blockers after the build passed.

    Conservative: anything the passing validation and the current review
    cannot speak for blocks -- a build that did not pass or no commit, a
    change the build never compiled, text problems or uncommitted files, a
    behaviour review that is absent, stale, found a regression or no effect,
    production semantic errors and misplaced tests, an evidence gap or an
    uncategorised blocker, a compatibility contract, and a decision that
    generates nothing or keeps its own gates. A maintainer decision with no
    regression, runtime behaviour, analyzer reconfirmation, a rename
    question, blockers the pipeline already discharged and test-coverage
    warnings are notes. Runtime-behaviour notes are dropped outright when
    the patched code introduces no algorithm, transformation or provider
    literal the original lacked. Publishing still needs the explicit
    approval of the finding (prs.pr_creator).

    Returns {"publishable", "blocks", "notes", "dropped_plan_blockers"}.
    """
    record = record or {}
    blocks: list[str] = []
    notes: list[str] = []
    validation = record.get("build_validation") or {}
    status = str(validation.get("status", "") or "not_run").lower()
    if status not in BUILD_VALIDATION_PASSING_STATUSES:
        blocks.append(f"build validation is {status}")
    commit = str(record.get("commit_hash", "") or "").strip()
    if not commit or commit == "DRY_RUN":
        blocks.append("no commit")
    uncompiled = files_the_build_does_not_compile(record.get("changed_files") or [])
    if uncompiled:
        blocks.append("the build compiles none of " + ", ".join(uncompiled))
    if record.get("text_problems"):
        blocks.append("text problems: " + "; ".join(str(item) for item in record["text_problems"]))
    if record.get("uncommitted_patch_files"):
        blocks.append("uncommitted patch files: "
                      + ", ".join(str(item) for item in record["uncommitted_patch_files"]))

    blocker_record = record.get("publication_blocker_record") or {}
    review = blocker_record.get("behavior_review") or {}
    version = record.get("review_contract_version")
    if version in (None, ""):
        version = review.get("review_contract_version")
    if version in (None, ""):
        blocks.append("no behaviour review judged this draft")
    else:
        from cognalys.prs.publication_checks import stale_review

        stale = stale_review(version, record.get("behavior_review_recorded"),
                             fix_scope=record.get("fix_scope"))
        if stale:
            blocks.append(f"behaviour review is stale: {stale}")
    if int(review.get("regression_count") or 0):
        blocks.append(f"the behaviour review found {review['regression_count']} regression(s)")
    if review.get("patch_has_no_effect"):
        blocks.append("the behaviour review found the patch has no effect")
    blocks.extend(block for block in repaired_patch_review_blocks(record) if block not in blocks)

    from cognalys.llm.semantic_validation import _SEMANTIC_VALIDATION_TEST_CODES

    semantic = record.get("semantic_validation") or {}
    for item in semantic.get("errors") or []:
        code = str((item or {}).get("code", "") if isinstance(item, dict) else item)
        blocks.append(f"semantic validation error {code}")
    for item in semantic.get("warnings") or []:
        code = str((item or {}).get("code", "") if isinstance(item, dict) else item)
        if code in TEST_PLACEMENT_CODES:
            blocks.append(f"misplaced test: {code}")
        elif code in _SEMANTIC_VALIDATION_TEST_CODES:
            notes.append(f"test coverage: {code}")
        elif isinstance(item, dict) and item.get("rename_check_left_to_review"):
            # A rename check the build and the review stood in for: the
            # reader is told which name or reference it could not confirm.
            notes.append(f"rename check left to review: {code}: "
                         + "; ".join(str(detail) for detail in item.get("details") or []))
        elif isinstance(item, dict) and item.get("maintainer_decides"):
            # SMSecure run_v4 (2026-10-31): an algorithm above minSdk kept on
            # the last attempt; raising minSdk is the maintainer's call.
            notes.append(f"for the maintainer: {code}: "
                         + "; ".join(str(detail) for detail in item.get("details") or []))
        else:
            notes.append(f"review warning: {code}")

    decision = str(record.get("decision", "") or blocker_record.get("decision", ""))
    if decision not in GENERATING_DECISIONS:
        blocks.append(f"decision {decision or 'missing'} generates no patch")
    elif decision in _DECISIONS_KEPT_FOR_THEIR_GATES:
        blocks.append(f"decision {decision} keeps its own publication gates")

    edits = [item for item in record.get("coordinated_edits") or [] if isinstance(item, dict)]
    original = "\n".join(str(item.get("original_code", "")) for item in edits) or str(
        record.get("original_method_body", ""))
    corrected = "\n".join(str(item.get("corrected_code", "")) for item in edits) or str(
        record.get("corrected_method_body", ""))
    introduces_values = bool(_value_literals(corrected) - _value_literals(original))
    described = set()
    for blocker in blocker_record.get("blockers") or []:
        category = str(blocker.get("category", "")).strip()
        text = str(blocker.get("description") or blocker.get("kind") or "").strip()
        described.add(text)
        if blocker.get("discharged_by_pipeline_validation") is True:
            notes.append(f"settled by validation: {text}")
        elif category in _EVIDENCE_GAP_CATEGORIES:
            blocks.append(f"{category}: {text}")
        elif category in _BLOCKING_CATEGORIES:
            blocks.append(f"{category}: {text}")
        elif category == "runtime_behavior" and not introduces_values:
            continue
        elif category in _NOTE_CATEGORIES:
            notes.append(f"{category}: {text}")
        else:
            blocks.append(f"{category or 'uncategorised'}: {text}")
    for blocker in blocker_record.get("discharged_blockers") or []:
        notes.append(f"discharged ({blocker.get('discharged_by', 'rule')}): "
                     f"{blocker.get('description') or blocker.get('kind', '')}")
    dropped = []
    for line in blocker_record.get("plan_publication_blockers") or []:
        if line in described:
            continue
        if _settled_by_validation(line, validation):
            dropped.append(line)
            notes.append(f"settled by validation: {line}")
        else:
            notes.append(line)
    return {
        "publishable": not blocks,
        "blocks": list(dict.fromkeys(blocks)),
        "notes": list(dict.fromkeys(notes)),
        "dropped_plan_blockers": dropped,
    }


def _names_only(text: str, gone: set, kept: set) -> bool:
    """Whether ``text`` names a spec in ``gone`` and none in ``kept``."""
    def names(spec: str) -> bool:
        return bool(re.search(rf"(?<![\w$]){re.escape(spec)}(?![\w$])", text))

    return any(names(spec) for spec in gone) and not any(names(spec) for spec in kept)


def without_blockers_of_findings_without_effect(notes: dict, without_effect_specs,
                                                pull_request_specs) -> tuple[dict, list[str]]:
    """The review notes less the blockers that are only about findings without effect.

    LocalSend run_v5 (2026-10-31): the pull request was attributed to the joint
    KeyStoreSpec finding, and the reported KeyPairSpec finding ended without
    effect; its plan's "cause_unconfirmed" ("the reported KeyPairSpec order
    condition cannot be attributed ..."), "obtain the resolved ... trace for
    the KeyPairSpec report" and analyzer reconfirmation still held the draft
    as REVIEW_ONLY_NOT_PUBLISHABLE. A publication blocker, consideration or
    plan line that names a spec of a finding without effect and none of the
    pull request's findings is dropped; one naming no spec, or one shared by
    both (two findings of one spec), stays. Returns (notes, dropped texts).
    """
    kept = {str(spec).strip() for spec in pull_request_specs or [] if str(spec or "").strip()}
    gone = {str(spec).strip() for spec in without_effect_specs or []
            if str(spec or "").strip()} - kept
    notes = dict(notes or {})
    if not gone:
        return notes, []
    dropped: list[str] = []

    def keep(item) -> bool:
        text = (" ".join(str(item.get(key, "")) for key in ("kind", "description"))
                if isinstance(item, dict) else str(item))
        if _names_only(text, gone, kept):
            dropped.append(text.strip())
            return False
        return True

    for key in ("publication_blockers", "publication_considerations"):
        notes[key] = [item for item in notes.get(key) or [] if keep(item)]
    record = dict(notes.get("publication_blocker_record") or {})
    if record:
        for key in ("blockers", "plan_publication_blockers"):
            if key in record:
                record[key] = [item for item in record.get(key) or [] if keep(item)]
        notes["publication_blocker_record"] = record
    return notes, list(dict.fromkeys(dropped))


def decision_label(decision: str, changed_files) -> str:
    """The decision as the final diff shows it, for display only.

    A MULTI_FILE_* decision whose final diff changes one production file is
    shown as SINGLE_FILE_*, and FIX_SUPPORTED changing several as
    MULTI_FILE_FIX_SUPPORTED; the recorded decision never changes.
    """
    production = {str(path) for path in changed_files or []
                  if not re.search(r"(?:^|/)src/(?:test|androidTest|sharedTest)\w*/|(?:^|/)tests?/",
                                   str(path))}
    decision = str(decision or "")
    if decision.startswith("MULTI_FILE_") and len(production) <= 1:
        return "SINGLE_FILE_" + decision[len("MULTI_FILE_"):]
    if decision == "FIX_SUPPORTED" and len(production) > 1:
        return "MULTI_FILE_FIX_SUPPORTED"
    return decision


@dataclass(frozen=True)
class FindingDisposition:
    """Whether a finding may be applied, published, or only reviewed."""

    decision: str = ""
    publication_status: str = ""

    @classmethod
    def from_metadata(cls, metadata: dict) -> "FindingDisposition":
        """Rebuild a disposition from a persisted solution record.

        Downstream stages read the execution report, not the analysis, so the
        disposition has to be recovered from what was written. `decision` has
        always been persisted. `publication_status` is persisted from now on;
        for a record written before it existed, publication_allowed=False
        carries the same fact and is read as the block it recorded.
        """
        metadata = metadata or {}
        publication_status = str(metadata.get("publication_status", ""))
        if not publication_status and metadata.get(
                "publication_allowed") is False:
            publication_status = PUBLICATION_BLOCKED
        return cls(
            decision=str(metadata.get("decision", "")),
            publication_status=publication_status,
        )

    @property
    def needs_human_review(self) -> bool:
        """A human must read the patch before it goes anywhere."""
        return self.decision in REVIEW_ONLY_DECISIONS

    @property
    def appliable(self) -> bool:
        """May be written into a worktree and built.

        A blocked publication does NOT make a finding unappliable. The block
        says the patch must not reach a maintainer; validation still has to
        run against it, and it cannot run against a patch that was never
        applied. This asymmetry is the whole reason this module exists: it
        used to live only in solution_applicator omitting one clause.
        """
        return not self.needs_human_review

    @property
    def publishable(self) -> bool:
        """Needs no review of its own before it may become a pull request.

        pr_creator still publishes it only for a finding the user approves by
        name (--approve-reviewed-finding); without one it is a dry run
        (batch6 audit, 2026-10-30).
        """
        return (
            not self.needs_human_review
            and self.publication_status != PUBLICATION_BLOCKED
        )

    def as_metadata(self) -> dict:
        """The persisted flags, so every producer writes the same ones."""
        return {
            "decision": self.decision,
            "publication_status": self.publication_status,
            "manual_review_required": self.needs_human_review,
            "auto_apply": self.appliable,
            "publication_allowed": self.publishable,
        }
