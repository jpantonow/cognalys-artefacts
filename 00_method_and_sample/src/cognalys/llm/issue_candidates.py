"""Whether and where a finding without a patch becomes an issue candidate.

Moved verbatim out of generate_error_fixes.py; see FixGenerator.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import (
    Dict,
    List,
    Optional,
    Tuple,
)

_LLM_MODULE_DIR = Path(__file__).resolve().parent
_SRC_DIR = _LLM_MODULE_DIR.parent

from cognalys.llm.fix_artifacts import _write_json_atomic  # noqa: E402
from cognalys.llm.pipeline_architecture import (  # noqa: E402
    collateral_constraint_violations,
    evidence_correlates_with_source,
)

from cognalys.llm.fix_generation_common import (  # noqa: E402
    ISSUE_PRESENTATION_FIELDS,
    claim_grounded_cause,
    collateral_issue_candidate,
    post_validation_issue_candidate,
    recorded_review_regressions,
)
from cognalys.utils.contribution_base import code_gone_from_base  # noqa: E402

# The candidate an issue would have been, for a statement no longer on the
# contribution base: read by the statistics, never by a preview or publisher.
NOT_APPLICABLE_TO_BASE_SUFFIX = "_not_applicable_to_base.json"


class IssueCandidates:
    """Whether and where a finding without a patch becomes an issue candidate."""

    @staticmethod
    def finding_is_reportable(evidence_analysis: Dict) -> bool:
        """Whether the reported finding survived adjudication.

        A confirmed cause is not a confirmed finding. cause_status answers
        "did we establish what this code does"; detector_violation and
        runtime_security_impact answer "and is it wrong".

        The distinction that matters is between not knowing and having
        checked. Arcanum's verifyPin was checked: its adjudication recorded
        detector_violation "not_confirmed" because the monotonic term holds
        the PIN lockout while it is positive, so the reported clock-forward
        bypass does not happen. An issue was prepared for the maintainer
        anyway, headed "I identified a potential issue" over a body
        explaining that the reported issue was not confirmed.

        "unknown" is not that. opencloud's getKnownServersStore confirmed
        that both KeyStore.load password arguments derive from the literal
        "password" and only left unknown whether that caused the category
        the detector happened to report; hoyservicios' TrustManagerFactory
        and the SHA-1 digest are the same shape. Those are real source facts
        a maintainer should hear, and withholding them would repeat the loss
        this gate's predecessor caused.

        Only v2 emits these fields; their absence stays permissive so the v1
        baseline behaves exactly as before.
        """
        evidence_status = evidence_analysis.get("evidence_status", {}) or {}

        def field(name: str) -> str:
            return str(evidence_analysis.get(
                name, evidence_status.get(name, "")
            )).strip()

        detector_violation = field("detector_violation")
        runtime_impact = field("runtime_security_impact")
        # A documented false-positive shape no longer withholds the issue
        # here: matching the rejected value as a word in the shape's text was
        # a deterministic dismissal that never checked the shape's condition
        # (removed 2026-10-28; see false_positive_rules).
        if (detector_violation in {"confirmed", "source_established"}
                or runtime_impact == "confirmed"):
            return True
        # Only an explicit negative withholds the issue. Confirmation is
        # checked first so that a confirmed violation in a use the analysis
        # judged non-security still reaches the maintainer.
        return not (
            detector_violation == "not_confirmed"
            or runtime_impact == "non_security"
        )

    DEPENDENCY_CHANGE_DECISIONS = frozenset({
        "DEPENDENCY_FIX_SUPPORTED", "DEPENDENCY_REPLACEMENT_SUPPORTED",
        "DEPENDENCY_FIX_REQUIRES_POLICY",
    })

    @classmethod
    def issue_repository(
        cls, error: Dict, decision: str,
    ) -> Tuple[Optional["re.Match"], str, str]:
        """Which repository an issue about this finding belongs in.

        Returns (repository match, target kind, reason withheld). A finding in
        the application's code, or one whose remedy is changing a dependency
        the application declares, belongs with the application. A finding
        inside library code does not: the application's maintainers cannot
        change OkHttp's Platform, and asking them about it spends their time
        on a question only the library can answer. It goes to the library's
        own repository when its published POM names one, and nowhere when it
        does not.
        """
        pattern = r"github\.com[/:]([^/]+)/([^/#]+?)(?:\.git)?$"
        application = re.search(
            pattern, str(error.get("repository_url", "")).strip(), re.IGNORECASE,
        )
        # Code in a git submodule is the submodule's repository's, whoever
        # vendored it: EteSync's cert4android is a separate project, and a
        # change has to be made there.
        submodule = ((error.get("contribution_base") or {})
                     .get("submodule") or {})
        if submodule:
            match = re.search(pattern, str(submodule.get("url", "")).strip(),
                              re.IGNORECASE)
            if match:
                return match, "upstream_library", ""
            return None, "", (
                f"the reported code is in the git submodule "
                f"{submodule.get('path', '')} ({submodule.get('url', '')}), "
                "whose repository is not on GitHub"
            )
        if (str(error.get("extraction_status", "")) != "external_dependency_candidate"
                or decision in cls.DEPENDENCY_CHANGE_DECISIONS):
            return application, "application_repository", ""
        upstream = str(
            ((error.get("dependency_context") or {})
             .get("owning_library_repository") or {}).get("url", "")
        )
        match = re.search(pattern, upstream, re.IGNORECASE)
        if match:
            return match, "upstream_library", ""
        return None, "", (
            f"the reported class {error.get('csv_class', '')} is library code "
            "and no repository for that library is recorded, so there is no "
            "repository an issue about it belongs in"
        )

    def record_no_security_impact(self, error: Dict, apk_name: str,
                                  method_name: str, reason: str,
                                  joint_without_effect=None) -> Path:
        """error_solutions/no_security_impact/<apk>/<index>_<method>.json.

        Read by reporting.finding_statistics, which reports the finding as a
        violation without security impact. No issue is written for it.
        ``joint_without_effect`` are the unit's joint findings the behaviour
        review found without effect (all of them when the patch as a whole
        had none): the statistics give each "analisado junto com X, sem
        efeito no patch" (2026-10-31: joined findings of units ended this way
        read "resolvido junto com").
        """
        safe_method = re.sub(r"[^A-Za-z0-9_.-]+", "_", method_name)
        path = (Path(self.output_dir) / "no_security_impact" / apk_name
                / f"{error.get('error_index', 0)}_{safe_method}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "apk_name": apk_name, "csv_class": error.get("csv_class", ""),
            "method_name": method_name, "spec": error.get("spec", ""),
            "unique_error_id": error.get("unique_error_id", ""),
            "reason": reason,
            **({"joint_findings_without_effect": [
                {key: item.get(key, "") for key in ("method", "specification", "reason")}
                for item in joint_without_effect if isinstance(item, dict)]}
               if joint_without_effect else {}),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        self.logger.info("%s: violation without security impact: %s", method_name, reason)
        return path

    def record_settled_before_analysis(self, error: Dict, apk_name: str,
                                       method_name: str, settled: Dict) -> Path:
        """error_solutions/settled_before_analysis/<apk>/<index>_<method>.json.

        A finding no paid analysis was spent on (finding_processing.
        settled_before_analysis): one of our pull requests for it is open, or
        merged after the analysed commit, or its file is not on the
        contribution base. Read by reporting.finding_statistics ("PR já
        aberto", or a fix not applicable to the base).
        """
        safe_method = re.sub(r"[^A-Za-z0-9_.-]+", "_", method_name)
        stem = f"{error.get('error_index', 0)}_{safe_method}"
        path = Path(self.output_dir) / "settled_before_analysis" / apk_name / f"{stem}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(path, {
            "apk_name": apk_name, "csv_class": error.get("csv_class", ""),
            "method_name": method_name, "spec": error.get("spec", ""),
            "unique_error_id": error.get("unique_error_id", ""),
            **{key: str(settled.get(key, "")) for key in ("kind", "reason", "pull_request")},
        })
        return path

    def record_confirmed_without_safe_fix(self, error: Dict, apk_name: str,
                                          method_name: str, reason: str,
                                          kind: str = "") -> Path:
        """error_solutions/confirmed_without_safe_fix/<apk>/<index>_<method>.json.

        A violation the adjudication confirmed that ends before any draft with
        nothing to ask the maintainer: a planner abstention after a review
        promotion (finding_processing.planner_abstained_after_promotion) or a
        blocked violation whose impact the adjudication did not confirm
        (finding_processing.blocked_without_confirmed_impact). Read by
        reporting.finding_statistics as "violação confirmada sem correção
        segura", never as a false positive or a violation without impact.
        kind names why: "external_contract" (read as "contrato externo impede
        a correção") or "no_safe_fix"; absent for a planner abstention after a
        promotion.
        """
        safe_method = re.sub(r"[^A-Za-z0-9_.-]+", "_", method_name)
        stem = f"{error.get('error_index', 0)}_{safe_method}"
        (Path(self.output_dir) / "issue_candidates" / apk_name
         / f"{stem}_issue.json").unlink(missing_ok=True)
        path = Path(self.output_dir) / "confirmed_without_safe_fix" / apk_name / f"{stem}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(path, {
            "apk_name": apk_name, "csv_class": error.get("csv_class", ""),
            "method_name": method_name, "spec": error.get("spec", ""),
            "unique_error_id": error.get("unique_error_id", ""),
            "reason": reason,
            **({"kind": kind} if kind else {}),
        })
        if kind == "external_contract":
            self.logger.info("%s: a counterpart outside the repository fixes the value: %s",
                             method_name, reason[:300])
        else:
            self.logger.info("%s: confirmed violation without a safe correction: %s",
                             method_name, reason[:300])
        return path

    def record_generation_shortfall(self, error: Dict, apk_name: str,
                                    method_name: str, blockers: List[str],
                                    kind: str = "draft_quality") -> Path:
        """error_solutions/generation_shortfall/<apk>/<index>_<method>.json.

        A fix whose every final blocker is the pipeline's own
        (fix_generation_common.generation_shortfall, which names ``kind``): a
        check on the drafts themselves, or a scope the analysis left
        incomplete that the pipeline could have closed. No correct patch was
        written, and no maintainer can answer that. Read by
        reporting.finding_statistics as "correção não gerada"; no issue is
        written, and one an earlier attempt of this finding wrote is removed
        (audit of 2026-10-31: Ursa's sessionPinnedClient and Trigger's
        createKeyPair asked their maintainers about drafts the pipeline had
        refused).
        """
        safe_method = re.sub(r"[^A-Za-z0-9_.-]+", "_", method_name)
        stem = f"{error.get('error_index', 0)}_{safe_method}"
        (Path(self.output_dir) / "issue_candidates" / apk_name
         / f"{stem}_issue.json").unlink(missing_ok=True)
        path = Path(self.output_dir) / "generation_shortfall" / apk_name / f"{stem}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(path, {
            "apk_name": apk_name, "csv_class": error.get("csv_class", ""),
            "method_name": method_name, "spec": error.get("spec", ""),
            "unique_error_id": error.get("unique_error_id", ""),
            "generation_blockers": [str(item) for item in blockers],
            "shortfall_kind": kind,
        })
        self.logger.warning(
            "%s: every blocker is the pipeline's own (%s: %s); recorded "
            "as a correction not generated, no issue", method_name, kind,
            "; ".join(sorted({str(item).split(":", 1)[0] for item in blockers})))
        return path

    def review_regressions(self, error: Dict, apk_name: str, method_name: str,
                           blockers: List) -> List[Dict]:
        """The last behaviour review's regressions, when it refused the drafts.

        Read from coordinated_drafts/<apk>/<index>_<method>/behavior_review.json
        (coordinated_generation's draft directory) only when its refusal is
        among ``blockers``: a review a later check overrode is not the reason.
        """
        if not any("UNINTENDED_BEHAVIOR_CHANGE" in str(item) for item in blockers or []):
            return []
        safe_method = re.sub(r"[^A-Za-z0-9._-]+", "_", method_name)
        return recorded_review_regressions(
            Path(self.output_dir) / "coordinated_drafts" / apk_name
            / f"{error.get('error_index', 0)}_{safe_method}")

    def persist_blocked_issue_candidate(
        self, error: Dict, evidence_analysis: Dict, evidence_file: Path,
        apk_name: str, method_name: str, blockers: List[str],
        collateral_only: bool = False,
        settled_texts: Optional[List[str]] = None,
    ) -> Optional[Path]:
        """Persist a maintainer issue whenever a confirmed fix is blocked.

        collateral_only: the reported finding is already settled elsewhere
        (recorded as a violation without security impact), so only the
        source-settled violations of its other requirements may be written.
        settled_texts: the sentences that settled it (the correct-use
        reason); a collateral entry about the same value at the same call is
        that judgement restated and is not raised.
        """
        confirmed_cause = str(
            evidence_analysis.get("confirmed_cause", "")
        ).strip()
        predicates = evidence_analysis.get("predicate_assessment", [])
        corpus = "\n".join(str(error.get(field, "")) for field in (
            "error_message", "unique_error_id", "method_body", "imports",
            "surrounding_context", "call_sites",
        ))
        specification = error.get("specification_context", {}) or {}

        def correlated(item: Dict) -> bool:
            # The matcher comes from the finding's own specification rather
            # than from a fixed list of crypto factories, so a KeyStore
            # password, a monitor property, an advisory or a dependency
            # coordinate can correlate on the same terms a Cipher call does.
            if item.get("status") != "violated":
                return False
            return evidence_correlates_with_source(
                str(item.get("evidence", "")) or str(item.get("explanation", "")),
                corpus, specification,
            )

        issue = evidence_analysis.get("issue_candidate", {})
        required = ISSUE_PRESENTATION_FIELDS
        regressions = self.review_regressions(error, apk_name, method_name, blockers)
        if (
            confirmed_cause and not collateral_only
            and not (
                isinstance(issue, dict) and issue.get("eligible") is True
                and all(str(issue.get(field, "")).strip() for field in required)
            )
        ):
            # A maintainer knows the class, not the JVM's name for its constructor.
            named = (str(error.get("csv_class", "")).split("$", 1)[0].rsplit(".", 1)[-1]
                     if method_name in {"<init>", "<clinit>"} else "") or method_name
            issue = post_validation_issue_candidate(
                issue, blockers, named,
                str(error.get("spec", "reported")), confirmed_cause,
                getattr(self, "pr_issue_style", "v1"),
                regressions=regressions,
            )
            evidence_analysis["issue_candidate"] = issue
            if evidence_file.is_file():
                try:
                    review_record = json.loads(
                        evidence_file.read_text(encoding="utf-8")
                    )
                    if isinstance(review_record.get("analysis"), dict):
                        review_record["analysis"]["issue_candidate"] = issue
                        _write_json_atomic(evidence_file, review_record)
                except (OSError, json.JSONDecodeError) as exc:
                    self.logger.warning(
                        "Could not enrich evidence review with fallback issue: %s",
                        exc,
                    )
        repository_match, issue_target, withheld_reason = self.issue_repository(
            error, str(evidence_analysis.get("decision", "")),
        )
        if withheld_reason:
            self.logger.info(
                "Issue candidate withheld for %s: %s", method_name, withheld_reason,
            )
        # v2 answers "is this cause grounded in the repository?" from the
        # claim graph the adjudication already cites; v1, which has no claims,
        # keeps the lexical correlation as its only available answer.
        grounded = claim_grounded_cause(evidence_analysis) or any(
            correlated(item) for item in predicates if isinstance(item, dict)
        )
        # A non-empty confirmed_cause is not by itself a confirmed cause: when
        # v2 confirms nothing it still writes the sentence saying so ("No
        # concrete CipherSpec violation or underlying cause is confirmed"),
        # and okio's HashingSink and mtgfam's second decrypt both closed that
        # way. cause_status is the field that actually decides, and only v2
        # emits it, so its absence stays permissive for v1.
        cause_confirmed = bool(confirmed_cause) and str(
            evidence_analysis.get("cause_status", "confirmed")
        ).strip() == "confirmed"
        # Requirements of the same specification that the source itself
        # breaks, other than the one the detector reported. They are computed
        # before the reportability gate because that gate answers for the
        # reported finding, and these are a different claim: opencloud's
        # reported InvalidSequenceOfMethodCalls really was a documented rule
        # false positive, and the keystore password really was the literal
        # "password", and the first being false is no reason to drop the
        # second.
        dismissed_collateral: List[Dict] = []
        collateral = [
            item for item in collateral_constraint_violations(
                evidence_analysis, error, excluded=dismissed_collateral,
                settled_texts=settled_texts,
            )
            if evidence_correlates_with_source(
                item.get("explanation", ""), corpus, specification,
            )
        ]
        for item in dismissed_collateral:
            self.logger.info(
                "Collateral violation for %s not raised: %s -- %s",
                method_name, item["predicate"], item["reason"],
            )
        reportable = (not collateral_only
                      and self.finding_is_reportable(evidence_analysis))
        if collateral and not reportable:
            # The presentation on hand explains the reported finding, which
            # here is the finding that is not real. Replace it rather than
            # publish a summary that contradicts the reason for writing.
            api_types = specification.get("applies_to_types") or []
            issue = collateral_issue_candidate(
                collateral, method_name,
                str(error.get("spec", "reported")),
                getattr(self, "pr_issue_style", "v1"),
                class_name=str(error.get("csv_class", "")),
                api_name=str(api_types[0]).rsplit(".", 1)[-1] if api_types else "",
                # The adjudication's own verdict on the reported condition:
                # AntennaPod's issue said "the analysis did not confirm the
                # reported condition" over detector_violation=confirmed.
                reported_confirmed=str(evidence_analysis.get(
                    "detector_violation",
                    (evidence_analysis.get("evidence_status") or {}).get(
                        "detector_violation", ""),
                )).strip() in {"confirmed", "source_established"},
            )
            evidence_analysis["issue_candidate"] = issue
        if cause_confirmed and grounded and not reportable and not collateral_only:
            # Said out loud rather than dropped: an issue that vanishes with
            # no reason recorded is how two confirmed findings were lost
            # before, and this one is being withheld on purpose.
            self.logger.info(
                "Issue candidate withheld for %s: the cause is confirmed but "
                "the reported finding is not (detector_violation=%s, "
                "runtime_security_impact=%s); the evidence review still "
                "records the analysis",
                method_name,
                evidence_analysis.get("detector_violation", "unset"),
                evidence_analysis.get("runtime_security_impact", "unset"),
            )
        if collateral and not reportable:
            self.logger.info(
                "Reported finding for %s is not reportable, but %d "
                "source-settled violation(s) of other %s requirements are: %s",
                method_name, len(collateral), error.get("spec", "specification"),
                "; ".join(item["predicate"] for item in collateral),
            )
        # Two independent routes. The reported finding needs its cause
        # confirmed, grounded and reportable; a collateral violation carries
        # its own grounding -- every entry correlated against the source
        # above -- and does not inherit the reported finding's confirmation,
        # because it is not a claim about the reported finding.
        if not (
            ((cause_confirmed and grounded and reportable) or collateral)
            and isinstance(issue, dict) and issue.get("eligible") is True
            and all(str(issue.get(field, "")).strip() for field in required)
            and repository_match
        ):
            return None
        safe_method = re.sub(r"[^A-Za-z0-9_.-]+", "_", method_name)
        base = error.get("contribution_base") or {}
        # An issue about a statement the project has already removed asks the
        # maintainer about code they no longer have (code_gone_from_base).
        # The candidate is kept under another name, outside every
        # *_issue.json reader, so the user can still check a moved violation.
        gone = (code_gone_from_base(base)
                if issue_target != "upstream_library" else "")
        stem = self.output_dir / "issue_candidates" / apk_name / (
            f"{error.get('error_index', 0)}_{safe_method}")
        path, other = (Path(f"{stem}{NOT_APPLICABLE_TO_BASE_SUFFIX}"), Path(f"{stem}_issue.json"))
        if not gone:
            path, other = other, path
        # What an earlier attempt of this finding wrote under the other name.
        other.unlink(missing_ok=True)
        extra = ({"status": "not_applicable_to_base", "not_applicable_reason": gone,
                  "base_file_absent": base.get("target_present") is False}
                 if gone else
                 # The reviewed body changed upstream while the reported
                 # statement is still there (or could not be looked for).
                 {"method_changed_upstream": True}
                 if base.get("analyzed_code_present") is False else {})
        record = {
            **extra,
            "issue_kind": (
                "collateral_constraint_violation"
                if collateral and not reportable
                else "generation_blocked" if blockers else "no_safe_fix"
            ),
            "collateral_violations": collateral,
            "analysis_decision": str(evidence_analysis.get("decision", "")),
            "generation_blockers": blockers,
            "repo_host": "github.com", "repo_owner": repository_match.group(1),
            "repo_name": repository_match.group(2), "apk_name": apk_name,
            # What `issue_creator --approve-reviewed-finding` names to publish it.
            "finding_id": str(error.get("unique_error_id", "") or ""),
            # upstream_library when the reported code is a library's and the
            # issue is addressed to that library, not to the application.
            "issue_target": issue_target,
            "application_repository": error.get("repository_url", ""),
            "method_name": method_name, "csv_class": error.get("csv_class", ""),
            "source_file": (
                error.get("source_file_path", "") or str(error.get("csv_class", ""))
            ),
            "spec": error.get("spec", ""), "tool": error.get("tool", ""),
            "error_message": error.get("error_message", ""),
            "confirmed_cause": confirmed_cause,
            "compatibility_policy": error.get("compatibility_policy", {}),
            "migration_assessment": evidence_analysis.get(
                "migration_assessment", {}
            ),
            "digest_contract_assessment": evidence_analysis.get(
                "digest_contract_assessment", {}
            ),
            "issue_content": issue, "evidence_review": str(evidence_file),
            # What the behaviour review that refused the drafts recorded, for
            # the issue text's rewrite (issues.issue_text._facts).
            **({"review_regressions": regressions} if regressions else {}),
            # The detector's line as analysed: issue_creator looks for it on
            # the default branch before opening the issue (A7).
            "reported_statement": _reported_statement(error),
        }
        if not gone and getattr(self, "pr_issue_style", "v1") == "v3":
            # Rewritten here, where the run's model is at hand, so the preview
            # and the issue step read text that passes the issue's checks
            # (issues.issue_text; DNS Toggle, 2026-11-04).
            from cognalys.issues.issue_text import rewrite_issue_text

            if rewrite_issue_text(getattr(self, "provider", None), self.logger, record,
                                  evidence_analysis) == [] and "issue_text_rewrite" in record:
                evidence_analysis["issue_candidate"] = record["issue_content"]
        _write_json_atomic(path, record)
        if gone:
            self.logger.info("Issue candidate for %s not raised: %s; kept as %s",
                             method_name, gone, path)
            return None
        self.logger.info("Issue candidate prepared for manual review: %s", path)
        return path


def _reported_statement(error: Dict) -> str:
    """The reported line of the analysed checkout, when it holds a call; else ""."""
    from cognalys.llm.finding_context import reported_line
    from cognalys.utils.contribution_base import reported_statement

    source = str(error.get("source_file_path", "") or "")
    repository = str(error.get("repository_path", "") or "")
    line = reported_line(error, source) if source else 0
    if not (source and repository and line > 0):
        return ""
    try:
        lines = (Path(repository) / source).read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return reported_statement(lines[line - 1]) if line <= len(lines) else ""
