"""Constants, the outcome record and the module-level helpers FixGenerator's
stages share.

Moved verbatim out of generate_error_fixes.py; see FixGenerator.
"""

from __future__ import annotations

import os

import re
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Dict,
    List,
    Optional,
    Tuple,
)

from cognalys.utils.finding_source import is_runtime_monitor_tool
from cognalys.utils.pr_text import strip_evidence_citations

_LLM_MODULE_DIR = Path(__file__).resolve().parent
_SRC_DIR = _LLM_MODULE_DIR.parent


MAX_SEMANTIC_VALIDATION_RETRIES = 2


MAX_ADJUDICATION_RETRIES = 2


ISSUE_PRESENTATION_FIELDS = (
    "title", "finding_summary", "why_no_pr",
    "maintainer_question", "proposed_next_step",
)


@dataclass(frozen=True)
class FindingOutcome:
    """Everything one finding's analysis concluded.

    `outcome` and `decision` used to be reported by assigning to attributes
    on the generator from wherever the analysis gave up, which made them a
    race the moment findings were analysed concurrently and left the caller
    reading state rather than a result.

    `outcome` is "abstained" when the analysis deliberately declined to
    produce a patch, and "failed" otherwise, including on a successful
    analysis -- run_analysis reads `success` on that path, not this field.

    "crashed" and "provider_refused" split two things out of "failed" that
    are not the same event and do not deserve the same response. A defect in
    this pipeline and a refusal by the provider were both reported as "Failed
    to analyze" with no cause recorded, which is why runs before
    run_20260907_180505 name no reason for any of their failures at all: the
    exception text was logged, if at all, by a different line further up. A
    crash is this project's bug and should fail the step; a content refusal
    is a verdict on the prompt that repeats identically on every retry, so it
    is recorded and the run carries on. "provider_unavailable" is the
    provider failing to answer (a server fault, a capacity failure) after a
    call's own resends: no verdict on the text, so the finding is left for a
    resume pass, as a crash is.

    `failure_cause` is the exception text, kept so the tracking JSON can name
    what happened instead of only counting it.
    """

    solution: Optional[Dict]
    success: bool
    outcome: str = "failed"
    decision: str = ""
    failure_cause: str = ""
    # What the finding spent on model calls (llm_providers.CallBudget).
    llm_budget: Optional[Dict] = None


# Evidence a packet derives from the repository itself carries a trust_level
# beginning with "repository_" (pipeline_architecture's _EvidenceItem and
# evidence_requests.catalog_supplement); these are the ID prefixes it hands
# those items. SPEC is the specification, DET the detector diagnostic -- which
# the packet itself marks decision_authority "none" -- and WEB an external
# page. None of the three ground a claim in this project's own code.
REPOSITORY_EVIDENCE_PREFIXES = frozenset({
    "SRC", "SEQ", "CALL", "TEST", "EDGE", "BUILD", "DEP", "SUP", "FLOW",
    # Both mint "repository_exact_source" items in pipeline_architecture and
    # were missing here, so a cause resting only on the application's own
    # AndroidManifest declaration -- an exported component with no
    # permission, which is the whole finding for FtpReceiver -- or only on
    # where the reported class is constructed counted as ungrounded, and its
    # issue was withheld for want of repository evidence it had.
    "MANIFEST", "CTOR", "CALLEE", "SIZE",
    # Read from this checkout's build files and sources: the dependency or
    # deprecated API a finding names (DEPF), the mechanisms the repository
    # already uses in its place (REPO), and its own uses of it (CONTRACT).
    "DEPF", "REPO", "CONTRACT",
    # Measured while this application ran (evidence_packet.runtime_measurement):
    # the value its own code passed to the governed call.
    "RUN",
    # Read from the reviewed method (evidence_packet.constant_material_evidence).
    "CONST",
})


# Set by `cognalys run --holdout-repository` (B13, 2026-11-12): each finding is
# analysed without what its own repository taught earlier runs, so the
# findings of published pull requests can be run again without the
# maintainers' verdict reaching the prompts.
HOLDOUT_REPOSITORY_VARIABLE = "COGNALYS_HOLDOUT_REPOSITORY"


def holdout_repository() -> bool:
    """Whether this process withholds each finding's own repository's history."""
    return str(os.environ.get(HOLDOUT_REPOSITORY_VARIABLE, "")).strip().lower() in {
        "1", "true", "yes", "on"}


def learning_context(error: Dict, repository: str) -> Dict:
    """What earlier runs verified for this repository and finding.

    Read from data/pipeline_learning (see learning/knowledge.py): import lines
    a passing build of this repository contained, and how the most recent
    runs of this same finding ended. Empty when nothing was recorded.

    Also what this repository's maintainers did with our earlier pull
    requests for the same specification, what other repositories' maintainers
    did with them, and this repository's maintainers with ours for any other
    (prs.pr_outcomes): evidence of the shape of change they accept, not a
    verdict on this finding.

    With holdout_repository() the finding's own repository gives nothing:
    no verified imports and no maintainer answers from it; other
    repositories' answers stay.
    """
    held_out = holdout_repository()
    try:
        from cognalys.learning.knowledge import (
            finding_history, finding_key_for, load_knowledge, verified_imports,
        )
        from cognalys.prs.pr_outcomes import (
            responses_in_same_repository, responses_to_same_rule,
            responses_to_this_rule_in_same_repository,
        )
    except ImportError:
        return {}
    knowledge = load_knowledge()
    context = {
        "verified_repository_imports": verified_imports(knowledge, repository)
        if knowledge and not held_out else [],
        "previous_attempts_of_this_finding": finding_history(
            knowledge, finding_key_for(error),
        ) if knowledge else [],
        # InterTune run_v2 (2026-11-08): the closure of InterTune#52 for this
        # very method reached adjudication among other projects' answers.
        "maintainer_responses_to_this_rule_in_this_repository": [] if held_out else
            responses_to_this_rule_in_same_repository(
                str(error.get("repository_url", "")), str(error.get("spec", ""))),
        "maintainer_responses_to_this_rule_elsewhere": responses_to_same_rule(
            str(error.get("spec", "")),
            excluding_repository_url=str(error.get("repository_url", ""))),
        # Amethyst v3 (2026-10-31): the CipherSpec draft never saw #4642's
        # closure, recorded under GCMParameterSpecSpec in the same repository.
        "maintainer_responses_in_this_repository": [] if held_out else
            responses_in_same_repository(
                str(error.get("repository_url", "")), str(error.get("spec", ""))),
    }
    return {key: value for key, value in context.items() if value}


def reporting_detector_site(error: Dict) -> Dict:
    """The checker's own code where it raised this finding, when it can be read.

    {"reporting_detector_site": {...}} or {}: see extraction.detector_emitter.
    Computed once per finding.
    """
    if "_reporting_detector_site" not in error:
        try:
            from cognalys.extraction.detector_emitter import detector_emitter
            error["_reporting_detector_site"] = detector_emitter(error)
        except Exception:  # noqa: BLE001 -- a missing excerpt is not a failure
            error["_reporting_detector_site"] = {"status": "unavailable"}
    site = error["_reporting_detector_site"]
    return {"reporting_detector_site": site} if site.get("status") == "found" else {}


def reported_by_detector(error: Dict) -> Dict:
    """The analyzer's own record of each finding of the unit whose check
    recorded a fact and does not admit it.

    {"reported_by_detector": [...]} or {}: the finding's own detector_context
    (with its spec and method_name) and each joint finding's (its
    specification, method and joint_detector_contexts entry), read through
    utils.detector_labels.reported_by_detector. Only a report kind the label
    catalogue flags outranks_rule_text_alternative gives a record, so every
    other finding's extraction and adjudication are unchanged. A missing
    catalogue raises: it is library data, not an optional input.
    """
    from cognalys.utils.detector_labels import reported_by_detector as records
    return records(error)


def finding_detector(error: Dict) -> Dict:
    """{"detector": {"tool", "kind": "runtime_monitor"}} when a runtime monitor reported it, or {}.

    Beta run_v2 (2026-10-30): nothing in the generation payload said who
    reported RVSec's KeyPairSpec and KeyFactorySpec findings, and the
    coordinated generator wrote that they "are emitted by a source/static
    analyzer" while it inlined a helper and split a statement -- the same
    calls on the same objects to the monitor. The behaviour review, the plan
    and every generation payload read the fact from here; a static or manual
    finding states no detector.
    """
    tool = str(error.get("tool", "") or "").strip()
    if not is_runtime_monitor_tool(tool):
        return {}
    return {"detector": {"tool": tool, "kind": "runtime_monitor"}}


# How the plan and the generators read finding_detector's fact. The behaviour
# review states the same rule in its own terms (patch_has_no_effect).
RUNTIME_MONITOR_GUIDANCE = (
    "detector, when present, says a runtime monitor reported the finding while "
    "the app ran: it observes each call, the object it is made on and the "
    "values passed, never the source text. A restructuring it would observe as "
    "the same calls on the same objects with the same values -- a helper "
    "inlined or extracted, an object's parts passed one by one instead of the "
    "object, statements split or merged, locals renamed or retyped -- cannot "
    "satisfy its rule; change the call, the object or the value the rule "
    "governs. "
)


# How the adjudication, the plan, the generators and the behaviour review read
# learning_context's maintainer_responses_to_this_rule_in_this_repository.
# InterTune run_v2 (2026-11-08): InterTune#52, closed for this very method
# ("this hash only names the widget's cached cover files"), reached the
# adjudication under maintainer_responses_to_this_rule_elsewhere, described
# as what "maintainers of other projects" answered, and the finding became a
# ready pull request for the same change.
SAME_REPOSITORY_SAME_RULE_GUIDANCE = (
    "maintainer_responses_to_this_rule_in_this_repository, when present, are "
    "what this repository's own maintainers answered to an earlier proposal "
    "of ours for this same rule; changed_code names the method that proposal "
    "changed. When it is this finding's code, it is the maintainers' answer "
    "to the change this finding calls for: weigh their technical reason as "
    "evidence about this code before any other project's answer, and never "
    "propose again a change their stated objection applies to as if it had "
    "not been answered. It is evidence, not a verdict: it does not by itself "
    "make the finding false, and a reason that does not hold for this code "
    "is said so. "
)


# How the plan and the generators read the carrier rule the behaviour review
# applies (REVIEW_CONTRACT_VERSION 12). BlueGate run_v7 (2026-10-31): the plan
# named a KeyMaterial "carrier" for KeyPair's two keys and every file draft
# built it, so the finding's patch only stopped calling the KeyPair
# constructor.
SAME_PARTS_CARRIER_GUIDANCE = (
    "Never replace the object or value the rule governs with a type of the "
    "app's own -- a new data class, record or class, a Pair, Triple, Map, list "
    "or array -- that carries the same parts from the same sources only so "
    "the governed API is no longer called: the app holds, trusts and does "
    "what it did, and the change has no effect. Using an object the platform "
    "already returns instead of rebuilding one (a key store entry through its "
    "own getters), or changing what the parts are or where they come from, "
    "is a change. "
)


# The behaviour review's rule that a change only the checker notices has no
# effect ("although the checker then no longer observes the governed call ...
# the rule was evaded, not satisfied"), said upstream to the adjudication and
# the plan. Frozen run (2026-11-13): TOTP's adjudication asked for "a
# compatible SecretKey representation that does not pass a disallowed
# algorithm name to SecretKeySpec", and Herald's plan for "a locally scoped
# KeyManagerFactory provider and SPI whose getKeyManagers implementation
# constructs the existing MtlsKeyManager"; their drafts were what was asked.
CHECKER_VIEW_GUIDANCE = (
    "A fix never takes the governed call out of the checker's view while the "
    "code keeps what it had: a factory, provider or service provider "
    "interface the app writes around the object the code already passes, a "
    "key class the app implements over the material the governed constructor "
    "took, a type of the app's own carrying the same parts, or the same value "
    "produced through an API the checker does not observe leaves the code "
    "holding, trusting and doing exactly what it did -- the rule is evaded, "
    "not satisfied. Never prescribe such a change: a fix changes the object, "
    "value or material the governed call receives, obtains it from the "
    "platform, or adds, removes or reorders the calls the rule governs. "
)


# How the plan and the generators treat the maintainer's comments.
# BlueGate run_v8 (2026-10-31): the getOrCreateKeyPair patch rewrote
# generateNewKeyPair for its new return type and deleted the commented-out
# `ECGenParameterSpec("ed25519")` alternative beside it, which nothing in the
# fix touched (utils.removed_comments checks the finished draft). Review of
# 2026-10-31: "a TODO" among what stays read as keeping a TODO the fix
# resolves; it is a TODO the change does not resolve.
COMMENT_PRESERVATION_GUIDANCE = (
    "Keep every existing comment and commented-out line the change does not "
    "make false, verbatim and where it was, also inside a block you rewrite: "
    "a commented-out alternative, a TODO the change does not resolve, a "
    "license line or an explanation of code the change leaves as it was is "
    "the maintainer's and stays. Only a comment that describes code this "
    "change removes or changes, or a TODO it resolves, may be removed or "
    "updated with that code. "
)


# How the plan and the generators treat a joint finding no observable change
# resolves (the behaviour review judges each one's share:
# joint_findings_without_effect). BlueGate run_v8 (2026-10-31): the plan told
# the drafts to "explicitly prepare the EC AlgorithmParameterSpec" for
# generateNewKeyPair [KeyPairGeneratorSpec], and the draft moved
# ECGenParameterSpec("secp256r1") into a local -- the same call and value.
JOINT_FINDING_EFFECT_GUIDANCE = (
    "Resolve each joint finding (joint_findings, when present: violations "
    "reported in the same code that this change resolves too) with a change "
    "its checker observes at its own "
    "reported call (another call, object, value or order). A joint finding no "
    "such change resolves is left exactly as its code was: never restructure "
    "its code for it (a value moved into a local, statements split or merged, "
    "a helper extracted or inlined), which only adds lines the maintainer "
    "reads for nothing. "
)


# The behaviour review's rule on removing something deprecated or insecure
# that the code offers to or accepts from a peer (coordinated_generation,
# "What the fix removes"), given to the adjudication and the plan too. prim-ftpd
# #453 (frozen run, 2026-11-13): the adjudication filed "dropping arcfour
# breaks arcfour-only clients" as a maintainer decision with no evidence that
# any client needs it, and the plan, obeying it, found no edit three times;
# the original run had retired arcfour and its PR stands open.
PEER_HARDENING_PLAN_GUIDANCE = (
    "No longer offering to or accepting from a peer or server something "
    "deprecated or insecure (an old protocol version, a weak algorithm or "
    "cipher, a certificate nothing verifies) is hardening the maintainer judges "
    "in review: it is never by itself a reason for NO_SAFE_FIX, a refused "
    "authorized_behavior_change, or an empty plan. It is a compatibility "
    "concern only when the payload shows that someone depends on it: "
    "function_contract with depends_on_reported_behavior established, "
    "deliberate_insecurity, an option or setting in the code that turns the "
    "behaviour on, other code handling the same peer in a way the stricter "
    "behaviour refuses, or this repository's maintainers saying such peers are "
    "supported. Without one of those, plan the removal and leave the question "
    "to the reviewer as a note; a peer you would have to assume is not "
    "evidence. "
)


# How the adjudication, the plan and the behaviour review read value_context.
VALUE_EXPORTS_GUIDANCE = (
    "value_exports, when present, lists the lines, found by following the "
    "reported method's callers, where the value it returns reaches an export "
    "sink, and input_labels names the UI fields bound to its arguments; "
    "app_purpose is what the app is for, in its README's words. A value that "
    "leaves through an external sink (clipboard, share intent, file, network) "
    "is read by other installations, possibly older versions of this app, so a "
    "new format for it is a compatibility fact the pull request must state: a "
    "note, not a block, unless the change makes the value unreadable where the "
    "same app version read it before. A sink value_exports does not list is "
    "not established either way. "
)


# How a test written by generation or by a follow-up states a known value.
# The follow-up for CodeRabbit's "assert a known SHA-256 digest" on
# NotallyX#1167 (2026-10-30) wrote the digest as 32 byteArrayOf entries
# (0xba.toByte(), 0x78, ...), correct and unreadable; a maintainer checks a
# test vector against its published hex form.
KNOWN_VALUE_ASSERTIONS = (
    "Write a known expected value in the most readable form the test file or "
    "the project already uses for such values: a digest, key or ciphertext as "
    "the hex (or Base64) string its specification publishes, compared with "
    "the result encoded the same way through a helper the project or the "
    "test file already has, or a short local one; never as a long list of "
    "byte literals. "
)


# The planning invariant for inputs a change could drop (Salty's replan
# stopped deriving new keys from BuildConfig.APP_SALT, with nothing against
# it); the behaviour review checks the same thing on the finished patch
# (extraction.dropped_inputs).
DROPPED_INPUT_INVARIANT = (
    "Every value that contributed to the original result (a parameter, a "
    "build-time field, an environment value) still contributes to the new "
    "path, moved to another argument when the rule forbids it in its slot, "
    "unless removing it is the fix itself; never move a constant into a slot "
    "whose rule forbids constants (such as a password or key argument), and "
    "list this among behavioral_invariants for each such value. "
)


def value_context(error: Dict) -> Dict:
    """Where the reported method's value leaves the app, and what the app is for.

    {"value_exports": ..., "app_purpose": ...}, each only when there is
    something to say: see extraction.value_exports and
    repository_guidance.app_purpose. Computed once per finding, from the
    reported method and the methods of its joint findings.
    """
    if "_value_context" not in error:
        context: Dict = {}
        repository = str(error.get("repository_path", "") or "")
        try:
            from cognalys.extraction.repository_guidance import app_purpose
            from cognalys.extraction.value_exports import value_exports
            if repository and Path(repository).is_dir():
                exports = value_exports(
                    Path(repository), str(error.get("source_file_path", "")),
                    str(error.get("csv_class", "")),
                    [error.get("source_method_name") or error.get("method_name", ""),
                     *[item.get("method", "") for item in error.get("joint_findings") or []
                       if isinstance(item, dict)]])
                if exports.get("sinks") or exports.get("input_labels"):
                    context["value_exports"] = exports
                purpose = app_purpose(Path(repository))
                if purpose:
                    context["app_purpose"] = purpose
        except Exception:  # noqa: BLE001 -- missing context is not a failure
            context = {}
        error["_value_context"] = context
    return dict(error["_value_context"])


def claim_grounded_cause(evidence_analysis: Dict) -> bool:
    """Whether the adjudicated cause rests on this repository's own source.

    The issue gate has to answer that question before it shows a maintainer a
    finding, and v2 already carries the answer: sanitize_evidence_extraction
    downgrades any "confirmed" claim whose evidence_ids are absent or not in
    the packet, so a surviving confirmed claim that cites repository evidence
    is grounded by construction.

    The lexical alternative this replaces asked whether a predicate's prose
    occurred verbatim in the finding's own method body, with a fallback that
    recognised only MessageDigest, Cipher and SSLContext factory calls. Any
    finding outside those three -- zemzeme's verifyChannelPassword returning
    an unconditional true, opencloud's KeyStore.load reading a hard-coded
    "password" -- could not pass it however well evidenced, and both were
    dropped with a complete, eligible presentation already written.
    """
    v2 = evidence_analysis.get("v2")
    if not isinstance(v2, dict):
        return False
    claims = {
        str(claim.get("claim_id", "")).strip(): claim
        for claim in v2.get("claims", []) if isinstance(claim, dict)
    }
    adjudication = v2.get("adjudication")
    cited = [
        str(claim_id).strip()
        for claim_id in (
            adjudication.get("decision_claim_ids", [])
            if isinstance(adjudication, dict) else []
        )
    ]
    # An adjudication that cites no claim is judged on every claim it made:
    # the requirement is that the cause is evidenced, not that the model
    # remembered to enumerate which claims carried it.
    considered = [
        claims[claim_id] for claim_id in cited if claim_id in claims
    ] or list(claims.values())
    return any(
        str(claim.get("epistemic_status", "")).strip() == "confirmed"
        and any(
            str(evidence_id).strip().split("-")[0].upper()
            in REPOSITORY_EVIDENCE_PREFIXES
            for evidence_id in claim.get("evidence_ids", [])
        )
        for claim in considered
    )


def post_validation_issue_candidate(
    issue, blockers, method_name: str, spec: str, confirmed_cause: str,
    style: str, build_failed: bool = False,
    regressions: Optional[List[Dict]] = None,
) -> Dict:
    """The issue a maintainer receives when generation was blocked late.

    build_failed: a build of the change ran and failed (plain_blocker_reasons).
    regressions: the last behaviour review's (recorded_review_regressions),
    so a refusal by that review names the case it recorded.

    Eligibility belongs to this layer, which holds the deterministic
    blockers; the explanation does not. The model routinely declines
    eligibility -- correctly, no PR is ready -- while writing exactly the
    reasoning a maintainer needs, and discarding all of it for a template
    threw away the analysis that had already been paid for. On mtgfam's
    decrypt the model asked which component produces the three hard-coded
    ciphertexts, the question that actually settles whether the finding can
    be fixed at all; the template asked the maintainer to "clarify the
    unresolved scope and compatibility assumptions".

    A complete presentation is therefore kept and only its eligibility
    overridden. The template remains for a presentation that is missing or
    half-written, where there is nothing to keep.
    """
    blocker_text = "; ".join(
        str(item) for item in blockers if str(item).strip()
    ) or "the finalized evidence does not authorize a safe patch"
    complete = isinstance(issue, dict) and all(
        str(issue.get(field, "")).strip() for field in ISSUE_PRESENTATION_FIELDS
    )
    if complete:
        return {
            **{field: str(issue.get(field, "")).strip()
               for field in ISSUE_PRESENTATION_FIELDS},
            "eligible": True,
            "presentation_source": "model_presentation_retained",
            "post_validation_blockers": blocker_text,
        }
    if style in ("v2", "v3"):
        # Same facts as the v1 template below, worded in first person: this
        # edge case otherwise bypasses all the cautious-tone work elsewhere.
        # The blockers are the pipeline's own gate codes; a maintainer read
        # "because STORED_DERIVED_VALUE_CHANGED, so I could not validate a
        # generated patch automatically" (exp_final_20261019), which is
        # neither plain nor true to how the contribution is presented.
        question, next_step = plain_blocker_question(blockers)
        summary = plain_cause(confirmed_cause)
        why_no_pr = plain_why_no_pr(blockers, build_failed, regressions,
                                    (summary, question, next_step))
        api = re.sub(r"Spec$", "", spec) if spec.endswith("Spec") and spec != "Spec" else spec
        return {
            "eligible": True,
            "title": f"Question about the {api} use in {method_name}",
            "finding_summary": summary,
            "why_no_pr": why_no_pr,
            # It used to ask "which of the current behaviours above must
            # stay" in every case, above a text that listed none
            # (mtgfam fetch, exp_final_20261019).
            "maintainer_question": question,
            "proposed_next_step": next_step,
            "presentation_source": "deterministic_post_validation_fallback",
        }
    return {
        "eligible": True,
        "title": f"Review requested for {spec} finding in {method_name}",
        "finding_summary": confirmed_cause,
        "why_no_pr": (
            "No pull request was generated because deterministic "
            f"post-adjudication validation blocked generation: {blocker_text}."
        ),
        "maintainer_question": (
            "Could the maintainers clarify or validate the unresolved "
            "scope and compatibility assumptions before a patch is prepared?"
        ),
        "proposed_next_step": (
            "Resolve the recorded blockers, rerun the same finding, and "
            "retain human review and validation before publication."
        ),
        "presentation_source": "deterministic_post_validation_fallback",
    }


# What a gate's refusal means to the project's maintainer, in their terms.
_PLAIN_BLOCKERS = (
    ({"STORED_DERIVED_VALUE_CHANGED"},
     "the fix would change values the app has already stored, so existing "
     "data (for example saved hashes or encrypted values) would stop matching"),
    ({"CONSTANT_KEY_MATERIAL_KEPT", "SHIPPED_CIPHERTEXT_UNREADABLE",
      "SHIPPED_CIPHERTEXT_REMOVED"},
     "the key is built into the app and the data it opens ships with the app "
     "too, so no change inside the app removes the secret without breaking "
     "that data"),
    ({"CALLER_SUPPLIED_VALUE_OVERRIDDEN", "REJECTS_CALLER_SUPPLIED_VALUE"},
     "satisfying the rule would override or refuse values that users or "
     "callers choose today"),
    ({"USER_OPTION_NEUTRALIZED", "USER_SETTING_REMOVED"},
     "it would take away what a setting users turn on does"),
    ({"PROTOCOL_MANDATED_ALGORITHM", "PINNED_NEGOTIATED_PROTOCOL",
      "VALUE_STILL_CONFIGURES_OTHER_API"},
     "the value is fixed by a protocol or another component the code has to "
     "work with"),
    ({"REPORTED_VALUE_UNTOUCHED", "COMMENT_ONLY_CHANGE",
      "GOVERNED_CALL_RESULT_DISCARDED", "STATEMENT_WITHOUT_EFFECT",
      "DISCARDED_CONSTRUCTION", "WRAPPED_SAME_OBJECT", "FALLBACK_TO_ORIGINAL",
      "SAME_VALUE_UNOBSERVED_API", "SAME_PARTS_APP_CARRIER", "SAME_PARTS_APP_KEY",
      "DELEGATING_ALIAS_CALL", "SAME_OBJECT_REBUILT", "EMPTY_STORE_MANAGERS"},
     "the changes I could make left the reported call as it is"),
    ({"REQUIRED_PARAMETER_SPEC_REMOVED"},
     "the cipher mode needs a parameter type that every version I wrote "
     "removed, which would stop the app decrypting the data it already stored"),
    ({"CALLER_IV_REFUSED_BY_KEYSTORE_KEY"},
     "the key lives in the Android Keystore and is set up so that the "
     "Keystore picks the IV itself, so encrypting with an IV chosen by the "
     "app would fail on the device every time"),
    ({"UNINTENDED_BEHAVIOR_CHANGE"},
     "every version I wrote changed what the app does in a case it handles "
     "today"),
    # semantic_validation._RENAME_CODES and the name that still states the
    # removed value.
    ({"UNAUTHORIZED_RENAME", "RENAME_TARGET_ALREADY_DECLARED",
      "RENAME_REFERENCES_NOT_UPDATED", "RENAME_PARTIAL_FAMILY",
      "RENAME_CALL_REBINDS", "NAME_CONTRADICTS_BEHAVIOR"},
     "I could not consistently rename every use of a name the change makes "
     "inaccurate"),
    # coordinated_generation.DROPPABLE_TEST_CODES and the visibility a test
    # widened.
    ({"TEST_READS_PRODUCTION_SOURCE", "TEST_ASSERTS_ONLY_CONSTANTS",
      "TEST_IMPORT_NOT_ON_TEST_CLASSPATH", "TEST_CALLS_PRIVATE_MEMBER",
      "TEST_PLACED_IN_UNRELATED_FILE", "REFLECTION_ONLY_TEST_SEAM",
      "TEST_IN_OTHER_MODULE", "TEST_SEAM_VISIBILITY_WIDENED"},
     "I could not write a test for the change that fits the project's own "
     "tests"),
)
# The checks a draft fails because of how it was written, never because of
# what the app needs: a rename left half done, an API widened or a symbol
# invented, a statement that changes nothing, the reported value left as it
# was, a test that does not fit the project. Each one is answered by another
# draft or another plan, never by the maintainer. Audit of 2026-10-31: 12 of
# the 21 latest maintainer issues came from failed generations, and Ursa's
# sessionPinnedClient (UNNECESSARY_PUBLIC_API_EXPANSION) and Trigger's
# createKeyPair (REPORTED_VALUE_UNTOUCHED) asked the maintainer about drafts
# the pipeline itself had refused. An allowlist: a code not named here
# (a stored value that would change, a user option, a protocol, a regression
# the review named, a free-text blocker) keeps the issue.
DRAFT_QUALITY_CODES = frozenset({
    # semantic_validation._RENAME_ROUTE_CODES and a caller renamed for nothing.
    "UNAUTHORIZED_RENAME", "RENAME_TARGET_ALREADY_DECLARED",
    "RENAME_REFERENCES_NOT_UPDATED", "RENAME_PARTIAL_FAMILY", "RENAME_CALL_REBINDS",
    "NAME_CONTRADICTS_BEHAVIOR", "REDUNDANT_CALLER_RENAME",
    # The shape of the code the draft wrote.
    "UNNECESSARY_PUBLIC_API_EXPANSION", "SYMBOL_NOT_DECLARED_IN_REPOSITORY",
    "CONSTRUCTOR_NOT_DECLARED_IN_REPOSITORY", "DECLARATION_SCOPE_MISMATCH",
    "OBSOLETE_CHECKED_EXCEPTION_CATCH", "CHECKED_EXCEPTION_NOT_PROPAGATED",
    "SIGNATURE_CHANGE_BREAKS_CALLERS", "UNRELATED_PARAMETER_CHANGED",
    "JOINT_FINDING_EDIT_WITHOUT_EFFECT", "NO_PRODUCTION_CHANGE",
    # A draft that left the reported call as it was (the no-effect gate codes
    # close the finding on their own: _close_discarded_construction).
    "REPORTED_VALUE_UNTOUCHED", "COMMENT_ONLY_CHANGE",
    "GOVERNED_CALL_RESULT_DISCARDED", "STATEMENT_WITHOUT_EFFECT",
    "DELEGATING_ALIAS_CALL", "SAME_OBJECT_REBUILT", "EMPTY_STORE_MANAGERS",
    # A statement duplicated over its neighbour (App Manager's getDigest,
    # 2026-11-01): another draft answers it.
    "DUPLICATED_ADJACENT_STATEMENT",
    # A value below the policy's own minimum (Your Local Weather run_v2,
    # 2026-11-02: the drafts kept 65536 iterations and the maintainer was
    # asked about a value the pipeline had refused). Another plan meets it.
    "CURRENT_PARAMETER_BELOW_POLICY",
    # coordinated_generation.DROPPABLE_TEST_CODES and the visibility a test
    # widened. A rewritten existing test expectation is not here: the
    # project's own test pins the behaviour the draft changed.
    "TEST_READS_PRODUCTION_SOURCE", "TEST_ASSERTS_ONLY_CONSTANTS",
    "TEST_IMPORT_NOT_ON_TEST_CLASSPATH", "TEST_CALLS_PRIVATE_MEMBER",
    "TEST_PLACED_IN_UNRELATED_FILE", "REFLECTION_ONLY_TEST_SEAM",
    "TEST_IN_OTHER_MODULE", "TEST_SEAM_VISIBILITY_WIDENED",
})
_LEADING_CODE = re.compile(r"\s*([A-Z][A-Z0-9_]{4,})\b")


def draft_quality_failure(blockers) -> bool:
    """Whether every blocker of a failed generation is a DRAFT_QUALITY_CODES check.

    ``blockers`` are the result's issues (a code, or "CODE: detail") or
    semantic-validation errors ({"code": ...}). Empty, or any blocker that
    does not start with such a code, is False.
    """
    codes = []
    for item in blockers or []:
        text = str(item.get("code", "")) if isinstance(item, dict) else str(item)
        match = _LEADING_CODE.match(text)
        if not match:
            return False
        codes.append(match.group(1))
    return bool(codes) and all(code in DRAFT_QUALITY_CODES for code in codes)


# The deterministic shape checks a draft or the assembled drafts fail, which
# carry a message and no code: coordinated_generation._coordinated_draft_issues
# ("every issue here is mechanical and fixable by the model when it is told"),
# semantic_validation.validate_coordinated_edit_shape and the edit checks
# finding_processing runs on the assembled result. Like DRAFT_QUALITY_CODES
# they judge how the drafts were written: rvsec_com_inputleaf_android_20261105
# asked its maintainer about "draft path differs from requested path; file
# draft contains no edits", rvsec_org_cryptomator_lite_20261104 about an edit
# that changed the brace scope after it. "coordinated generation abstained"
# (the generator chose another decision) is not here: it keeps the issue.
MECHANICAL_DRAFT_MESSAGES = (
    r"draft path differs from requested path",
    r"file draft contains no edits",
    r"a draft with edits cannot declare no_change_required",
    r"edit targets another file",
    r"new test file requires create edit and empty original_code",
    r"original_code must occur exactly once",
    r"edit changes the brace scope of the code after it",
    r"symbol and reason are required",
    r"create edit must have exactly empty original_code",
    r"create edit requires complete non-empty file content",
    r"original_code is required for non-create edits",
    r"\w+ edit must have exactly empty corrected_code",
    r"replacement edit requires non-empty corrected_code",
    r"unsupported edit_kind",
    r"coordinated edit count must be between",
    r"every coordinated edit must be an object",
    r"unsafe repository path",
    r"unsupported source path",
    r"incomplete edit for",
    r"no-op edit for",
    r"generated edits do not cover the required plan",
    r"multi-file fix must edit at least two distinct files",
    r"review-only generation must require manual review",
    r"review-only generation must state review assumptions",
)
# The scope checks semantic_validation.validate_multi_file_scope runs on the
# analysis before any draft: the adjudicator's scope_assessment did not
# review files the pipeline itself computed as callers, declarations or
# consumers of the change, or called the scope incomplete with no relation
# that blocks generation. The pipeline can close that gap (the files are in
# the repository it cloned); a maintainer cannot answer it. Winterhold's
# derive, Zen Companion's diskKey and Xtra's WebSocket digest (2026-10-29 to
# 2026-11-08) were filed as maintainer issues with only these blockers.
# "scope_assessment has generation-blocking relations" is not here: the
# adjudicator named something that blocks the change.
SCOPE_GAP_MESSAGES = (
    r"scope_assessment\.status must be complete",
    r"scope_assessment\.local_code_scope must be complete",
    r"scope assessment did not review required files",
    r"structural repository scope is incomplete",
)
_MECHANICAL_DRAFT = re.compile(r"\s*(?:%s)\b" % "|".join(MECHANICAL_DRAFT_MESSAGES))
_SCOPE_GAP = re.compile(r"\s*(?:%s)\b" % "|".join(SCOPE_GAP_MESSAGES))
# Publication blockers that ask a person to decide: the same categories
# adjudication_rules.human_decided_publication_considerations reads, without
# the notes (rename authorization, analyzer reconfirmation).
_MAINTAINER_QUESTION_CATEGORIES = frozenset({
    "maintainer_decision", "compatibility_contract", "runtime_behavior"})


def maintainer_question_in_adjudication(evidence_analysis) -> bool:
    """Whether the adjudication left a question only the maintainer can answer.

    A publication blocker or consideration under a maintainer-decision,
    compatibility-contract or runtime-behaviour category that the pipeline's
    own validation does not discharge, or a publication blocker written
    without any category (older runs), which is read as such a question.
    """
    if not isinstance(evidence_analysis, dict):
        return False
    adjudication = (evidence_analysis.get("v2") or {}).get("adjudication") or {}
    for key in ("blocking_for_publication", "publication_considerations"):
        for item in adjudication.get(key) or []:
            if not isinstance(item, dict) or item.get("discharged_by_pipeline_validation") is True:
                continue
            category = str(item.get("category", "")).strip()
            if category in _MAINTAINER_QUESTION_CATEGORIES or (
                    not category and key == "blocking_for_publication"):
                return True
    return False


def generation_shortfall(blockers, evidence_analysis=None) -> str:
    """Why a fix that was not generated is the pipeline's shortfall, or "".

    "draft_quality": every blocker is a DRAFT_QUALITY_CODES check
    (draft_quality_failure, unchanged). "draft_shape": every blocker is that
    or a MECHANICAL_DRAFT_MESSAGES check. "scope_gap": at least one is a
    SCOPE_GAP_MESSAGES check and the rest are draft checks. The last two
    hold only while the adjudication left the maintainer no question
    (maintainer_question_in_adjudication): such a finding keeps its issue.
    Any other blocker -- a stored value, a user option, a protocol, a
    regression the review named, free text -- also keeps the issue. Read by
    finding_processing, which records the finding as a correction not
    generated (issue_candidates.record_generation_shortfall) instead of
    asking the maintainer about the pipeline's own drafts or scope.
    """
    if draft_quality_failure(blockers):
        return "draft_quality"
    kinds = set()
    for item in blockers or []:
        text = str(item.get("code", "") or item.get("message", "")) if isinstance(
            item, dict) else str(item)
        code = _LEADING_CODE.match(text)
        if code and code.group(1) in DRAFT_QUALITY_CODES:
            kinds.add("draft_quality")
        elif _MECHANICAL_DRAFT.match(text):
            kinds.add("draft_shape")
        elif _SCOPE_GAP.match(text):
            kinds.add("scope_gap")
        else:
            return ""
    if not kinds or maintainer_question_in_adjudication(evidence_analysis):
        return ""
    return "scope_gap" if "scope_gap" in kinds else "draft_shape"


# Said of a blocker no sentence above covers. It claimed "and still builds
# cleanly with the rest of the code" in 36 of 109 issues of the rvsec runs
# (2026-10-29: Bluegate, NotallyX, Ursa), whose blockers were rename checks
# or review regressions and where no build ever ran; the build clause is
# added only for a build that ran and failed.
_DEFAULT_BLOCKER = (
    "I could not find a change that keeps the current behaviour intact"
)
_BUILD_FAILED_CLAUSE = " and builds cleanly with the rest of the code"


# The question that follows from each reason, in the order of _PLAIN_BLOCKERS,
# and what I can do with the answer.
_PLAIN_QUESTIONS = (
    ({"STORED_DERIVED_VALUE_CHANGED"},
     "Would you accept a change that keeps reading values stored the old way "
     "and writes new ones the new way?",
     "If so, I can prepare that migration and send it for review."),
    ({"CONSTANT_KEY_MATERIAL_KEPT", "SHIPPED_CIPHERTEXT_UNREADABLE",
      "SHIPPED_CIPHERTEXT_REMOVED"},
     "Is keeping this value inside the app intended, or would moving it out "
     "of the app (for example behind a server you control) be an option?",
     "If the value can move, I can help with the app side of that change."),
    ({"CALLER_SUPPLIED_VALUE_OVERRIDDEN", "REJECTS_CALLER_SUPPLIED_VALUE"},
     "Should existing choices keep working as they are, with the rule's "
     "value only as the default for new ones?",
     "With your answer I can prepare a change that respects it."),
    ({"USER_OPTION_NEUTRALIZED", "USER_SETTING_REMOVED"},
     "Should the setting keep its current effect, or would you accept a "
     "safer way to provide it?",
     "With your answer I can prepare a change that respects it."),
    ({"PROTOCOL_MANDATED_ALGORITHM", "PINNED_NEGOTIATED_PROTOCOL",
      "VALUE_STILL_CONFIGURES_OTHER_API"},
     "Is the value fixed by that protocol or component, or is there room "
     "to change it?",
     "If there is room, I can prepare the change and send it for review."),
    ({"CALLER_IV_REFUSED_BY_KEYSTORE_KEY"},
     "Should the Keystore keep choosing the IV for this key, with the app "
     "storing the IV it returns next to the encrypted data?",
     "If so, I can prepare a change that keeps that and send it for review."),
)
_DEFAULT_QUESTION = (
    "Is there a constraint on this code I may be missing, and would you be "
    "open to a change here?",
    "With your answer I can prepare a change and send it for review.",
)


def plain_blocker_question(blockers: List) -> tuple:
    """The question and next step for the first known reason behind ``blockers``."""
    codes = set(re.findall(r"\b[A-Z][A-Z_]{5,}\b", " ".join(str(item) for item in blockers)))
    for group, question, next_step in _PLAIN_QUESTIONS:
        if codes & group:
            return question, next_step
    return _DEFAULT_QUESTION


_CLAIM_IDS = re.compile(r"\s*\((?:[A-Z]{1,6}-\d+(?:,\s*)?)+\)")
# A clause quoting the rule's predicate, e.g. "violating the rule's required
# preparedKeyMaterial[keyMaterial] || randomized[keyMaterial] predicate".
_PREDICATE_CLAUSE = re.compile(r"[,;]?\s*[^,;.]*\b\w+\[\w+\][^,;.]*")


def plain_cause(text: str) -> str:
    """The confirmed cause without evidence ids or rule-predicate syntax."""
    # The bracketed and mixed lists, "[C1, C2; RUN-001]" or "(C1; SRC-001)",
    # reached DNS Toggle's and Tempus's issues (2026-11-04).
    plain = _PREDICATE_CLAUSE.sub("", _CLAIM_IDS.sub(
        "", strip_evidence_citations(str(text or ""))))
    # "The reported required-predicate violation is confirmed: <the cause>"
    plain = re.sub(r"^\s*The reported [^:.]{0,80}? is confirmed:\s*", "", plain)
    plain = plain[:1].upper() + plain[1:]
    plain = re.sub(r"\s+([.,;])", r"\1", re.sub(r"\s{2,}", " ", plain)).strip()
    return plain if plain.endswith(".") or not plain else plain + "."


def plain_blocker_reasons(blockers: List, build_failed: bool = False,
                          regressions: Optional[List[Dict]] = None,
                          case_words: Optional[int] = None) -> List[str]:
    """Each distinct reason behind ``blockers``, worded for a maintainer.

    Gate codes and free-text blockers both arrive here; known codes become
    their sentence, anything else becomes one general sentence, and no code
    is ever shown. build_failed: the caller ran the build and it failed;
    only then does the general sentence speak of building. regressions: the
    last behaviour review's (recorded_review_regressions); when the review's
    refusal is among the blockers and one of them reads plainly
    (regression_reason) within ``case_words`` words (MAX_REGRESSION_WORDS
    when not given), its sentence names that case.
    """
    codes = set(re.findall(r"\b[A-Z][A-Z_]{5,}\b", " ".join(str(item) for item in blockers)))
    if case_words is None:
        case_words = MAX_REGRESSION_WORDS
    named = regression_reason(regressions, case_words) if regressions else ""
    reasons = [named if named and _REVIEW_REFUSAL in group else sentence
               for group, sentence in _PLAIN_BLOCKERS if codes & group]
    known = set().union(*(group for group, _ in _PLAIN_BLOCKERS))
    if not reasons or codes - known or build_failed:
        reasons.append(_DEFAULT_BLOCKER + (_BUILD_FAILED_CLAUSE if build_failed else ""))
    return reasons


def plain_why_no_pr(blockers: List, build_failed: bool = False,
                    regressions: Optional[List[Dict]] = None,
                    other_fields: Tuple[str, ...] = ()) -> str:
    """The deterministic issue's why_no_pr paragraph (plain_blocker_reasons).

    The recorded case (regressions) goes in only within the issue's length
    limit, MAX_ISSUE_WORDS for the four text fields: the words left after
    ``other_fields`` (finding_summary, maintainer_question,
    proposed_next_step) and the general paragraph, less the "in the last
    one" the case adds, and never more than MAX_REGRESSION_WORDS. With no
    room the general paragraph stays.
    """
    def paragraph(case_words: int = 0) -> str:
        return ("I have not opened a pull request because " + "; and ".join(
            plain_blocker_reasons(blockers, build_failed,
                                  regressions if case_words > 0 else None,
                                  case_words)) + ".")

    general = paragraph()
    if not regressions:
        return general
    room = MAX_ISSUE_WORDS - issue_words(general, *other_fields) - issue_words("in the last one")
    return paragraph(min(MAX_REGRESSION_WORDS, room))


# The behaviour review's refusal (coordinated_generation), whose reason the
# issue gave only in general: "every version I wrote changed what the app
# does in a case it handles today". 46 issue previews of the recorded runs
# outside the comparison arms (2026-11-14) carried that sentence, and every
# one had the review's scenario, before and after recorded beside the
# draft; a maintainer asked "is there a constraint on this code I may be
# missing?" could not tell which case was meant.
_REVIEW_REFUSAL = "UNINTENDED_BEHAVIOR_CHANGE"
_REVIEW_REFUSAL_SENTENCE = next(
    sentence for group, sentence in _PLAIN_BLOCKERS if _REVIEW_REFUSAL in group)
# Words the review writes for the pipeline, not for a maintainer: the plan
# it judged, its invariants and forbidden changes, a draft that does not
# compile (or "after the build error is corrected", redreader_sha1 run_sob3:
# the case of a draft that did not build). A regression that needs them
# keeps the general sentence.
_REVIEW_VOCABULARY = re.compile(
    r"\b(?:plans?|plan's|invariants?|forbidden|prohibited|drafts?|"
    r"compil\w*|build(?:ing)? the app|build (?:errors?|failures?)|"
    r"the review|finding|specification)\b",
    re.IGNORECASE)
_PATCH_WORDS = ((re.compile(r"\b(the|this) patch\b", re.IGNORECASE), r"\1 change"),
                (re.compile(r"\bpatched\b", re.IGNORECASE), "changed"))
# The case as the issue states it (the scenario, and what the app does after
# the change when that still fits). The v3 issue keeps its four text fields
# under 150 words and why_no_pr to one or two sentences (issue_text._SYSTEM);
# scenario, before and after together, capped at 100 words, made that
# paragraph 101-126 words and the four fields 104-183 (6 of 10 samples over
# 150, 2026-11-14), so the case is held to one clause.
MAX_REGRESSION_WORDS = 40
# issue_text._SYSTEM's limit for finding_summary, why_no_pr,
# maintainer_question and proposed_next_step together; a case that would
# take the deterministic issue past it is shortened or left out.
MAX_ISSUE_WORDS = 150
_WORD = re.compile(r"\w+")


def issue_words(*texts: str) -> int:
    """Words in ``texts`` as the issue's length limit counts them (a hyphen
    or apostrophe splits a word, so this never undercounts)."""
    return sum(len(_WORD.findall(str(text or ""))) for text in texts)


# Words that start a clause and read lower-case after a colon or semicolon;
# any other first word (a class, a product, a platform) keeps its capital.
_CLAUSE_STARTERS = frozenset("""
a an the this that these those its their his her our your my some any no
all both each every either neither one two several many few another other
when whenever while if once after before during until unless as since
with without within on in at by for from to into through under over
there here it they we you he she user users new existing previously
""".split())


def _lowered(word: str, context: str = "") -> str:
    """``word`` in lower case when it starts a clause only by position.

    A clause starter, a gerund, an adverb or participle, or a word
    ``context`` (the regression's own text) also writes in lower case
    ("Platform certificate validation fails" beside "the platform trust
    store"). A name keeps its capital: "Android", "AndroidKeyStore", "SHA".
    """
    if not re.fullmatch(r"[A-Z][a-z]*", word or ""):
        return word
    lower = word.lower()
    if (lower in _CLAUSE_STARTERS or re.search(r"..(?:ing|ly|ed)$", lower)
            or re.search(rf"(?<![\w.]){re.escape(lower)}\b(?![.:]\w)", context)):
        return lower
    return word


# A sentence end inside a field, not an abbreviation ("e.g. the").
_INNER_SENTENCE_END = re.compile(r"(?<!\.\w)[.!?]\s+(\w+)")


def _clause(text: str, context: str = "") -> str:
    """One field of a regression as a clause: no closing period, its own
    sentences joined, "patch" said as "change", a plain first word in lower
    case (_lowered)."""
    text = re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".").strip()
    for pattern, replacement in _PATCH_WORDS:
        text = pattern.sub(replacement, text)
    text = _INNER_SENTENCE_END.sub(
        lambda match: ", and " + _lowered(match.group(1), context), text)
    first, _, rest = text.partition(" ")
    return _lowered(first, context) + (" " + rest if rest else "")


def regression_reason(regressions: Optional[List[Dict]],
                      max_words: int = MAX_REGRESSION_WORDS) -> str:
    """The review's refusal as one plain sentence naming a case it recorded.

    From the regressions the last behaviour review of the finding wrote
    (scenario, before, after), the first one in production code that reads
    plainly: no evidence ids, none of the pipeline's words
    (pr_text.PIPELINE_JARGON and _REVIEW_VOCABULARY), no bytecode names.
    The case is the scenario and what the app does after the change, or the
    scenario alone when both pass ``max_words`` words; a regression whose
    scenario alone passes it is skipped. "" when none fits, and the caller
    keeps the general sentence. A regression in a test file is about the
    draft's test, not about what the app does.
    """
    from cognalys.utils.pr_text import PIPELINE_JARGON, bytecode_name_problems
    from cognalys.utils.test_source_sets import is_test_source

    def plain(text: str) -> bool:
        lowered = text.lower()
        return not (strip_evidence_citations(text) != text
                    or any(word in lowered for word in PIPELINE_JARGON)
                    or _REVIEW_VOCABULARY.search(text)
                    or bytecode_name_problems("", text))

    for item in regressions or []:
        if not isinstance(item, dict) or is_test_source(str(item.get("source_file_path", ""))):
            continue
        fields = [str(item.get(key, "") or "").strip() for key in ("scenario", "before", "after")]
        if not fields[0]:
            continue
        context = " ".join(fields)
        scenario, after = _clause(fields[0], context), _clause(fields[2], context)
        cases = ([f"{scenario}: after the change, {after}"] if after else []) + [scenario]
        for case in cases:
            if issue_words(case) <= max_words and plain(case):
                return f"{_REVIEW_REFUSAL_SENTENCE}; in the last one, {case}"
    return ""


def recorded_review_regressions(draft_dir) -> List[Dict]:
    """The regressions of the last behaviour review beside a finding's drafts.

    ``draft_dir`` is coordinated_drafts/<apk>/<index>_<method>, where each
    review of the finding writes behavior_review.json over the one before.
    [] when there is none or it cannot be read.
    """
    import json

    try:
        review = json.loads((Path(draft_dir) / "behavior_review.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    regressions = review.get("regressions") if isinstance(review, dict) else None
    return [item for item in regressions or [] if isinstance(item, dict)]


def collateral_issue_candidate(
    collateral: List[Dict], method_name: str, spec: str, style: str,
    class_name: str = "", api_name: str = "", reported_confirmed: bool = False,
) -> Dict:
    """The issue for a requirement the source breaks that was not reported.

    It has to be written from the collateral violation and never from
    ``confirmed_cause``, because in exactly the case this exists for the
    confirmed cause is the explanation of why the *reported* finding is not
    real. Sending a maintainer an issue whose summary says their code
    matches a documented rule false positive would cost them their time to
    reach the conclusion that nothing was being reported.

    The reported finding is named anyway, with the adjudication's own verdict
    on it. A maintainer who receives this is entitled to know which detector
    output started it and whether the pipeline stands behind that part:
    "not confirmed" when it was not, and, when the reported condition was
    confirmed but needs no change (reported_confirmed: a documented use
    recorded as a violation without security impact), never "not
    confirmed" -- AntennaPod's issue said exactly that over
    detector_violation=confirmed.
    """
    # Written for a maintainer, who knows their class and the API it uses,
    # not the pipeline's specification ids or JVM method names:
    # "Possible KeyStoreSpec issue in <init>" named neither.
    statements = [
        str(item.get("predicate", "")).strip().rstrip(".")
        for item in collateral if str(item.get("predicate", "")).strip()
    ]
    if not statements:
        return {"eligible": False}
    subject = api_name or spec
    simple_class = str(class_name or "").split("$", 1)[0].rsplit(".", 1)[-1]
    if simple_class and method_name in {"<init>", "<clinit>"}:
        location = (
            f"the {simple_class} constructor" if method_name == "<init>"
            else f"the {simple_class} static initializer"
        )
    elif simple_class:
        location = f"{simple_class}.{method_name}"
    else:
        location = method_name
    verdict = (
        "analysis confirmed the reported condition but found that it needs "
        "no change, and separately the source"
        if reported_confirmed else
        "analysis did not confirm the reported condition, but the source"
    )
    summary = (
        f"While reviewing a reported {subject} finding in {location}, the "
        f"{verdict} "
        f"does appear to break {'another' if len(statements) == 1 else 'other'} "
        f"requirement{'' if len(statements) == 1 else 's'} the same "
        f"specification states: "
        + "; ".join(statements) + "."
    )
    explanations = [
        str(item.get("explanation", "")).strip()
        for item in collateral if str(item.get("explanation", "")).strip()
    ]
    if explanations:
        summary += " " + " ".join(
            text if text.endswith((".", "!", "?")) else f"{text}."
            for text in explanations
        )
    title = f"Possible {subject} issue in {location}"
    if style in ("v2", "v3"):
        return {
            "eligible": True,
            "title": title,
            "finding_summary": summary,
            "why_no_pr": (
                "I have not opened a pull request because this is not the "
                "condition that was originally reported, and I would rather "
                "check with you before changing anything."
            ),
            "maintainer_question": (
                "Does this reflect the intended behaviour, or would you like "
                "me to prepare a patch for it?"
            ),
            "proposed_next_step": (
                "If you would like a change here, I can prepare one and "
                "validate that the project still builds."
            ),
            "presentation_source": "collateral_constraint_violation",
        }
    return {
        "eligible": True,
        "title": title,
        "finding_summary": summary,
        "why_no_pr": (
            "No pull request was prepared: the originally reported condition "
            + ("needs no change" if reported_confirmed else "was not confirmed")
            + ", and this separate observation has not been reviewed by a "
            "maintainer."
        ),
        "maintainer_question": (
            "Is this the intended behaviour, or would a patch be welcome?"
        ),
        "proposed_next_step": (
            "Confirm whether a change is wanted, then a validated patch can "
            "be prepared for review."
        ),
        "presentation_source": "collateral_constraint_violation",
    }
