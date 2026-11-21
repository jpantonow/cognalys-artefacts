"""Repository context gathered for a finding before its evidence packet is built.

Moved verbatim out of generate_error_fixes.py; see FixGenerator.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

_LLM_MODULE_DIR = Path(__file__).resolve().parent
_SRC_DIR = _LLM_MODULE_DIR.parent

from cognalys.llm.evidence_requests import (  # noqa: E402
    RepositoryIndex,
    catalog_supplement,
    resolve_evidence_requests,
)
from cognalys.llm.fix_artifacts import _write_json_atomic  # noqa: E402
from cognalys.llm.pipeline_common import without_entry_hashes  # noqa: E402

# The trusted line that precedes an extraction appendix of fetched entries.
SUPPLEMENT_NOTE = (
    "The repository answered the evidence requests of the extraction above. "
    "The entries in supplementary_evidence_catalog below extend evidence_catalog: "
    "cite each by its own evidence_id, as any evidence_catalog entry; "
    "previously_requested_evidence records what was requested, supplied and "
    "declined. Extract again from all the data above and below."
)
ADJUDICATION_SUPPLEMENT_NOTE = (
    "The evidence the adjudication's blockers named was fetched from the "
    "repository. The entries in supplementary_evidence_catalog below extend "
    "evidence_catalog: cite each by its own evidence_id, as any "
    "evidence_catalog entry. Extract again from all the data above and below."
)

# Adjudication blockers that name evidence the checkout may hold.
EVIDENCE_BLOCKER_CATEGORIES = frozenset({
    "cause_unconfirmed", "code_evidence_missing", "target_unresolved",
})


def reported_line(error: Dict, source_file_path: str) -> int:
    """The line the finding was reported at, when it is in `source_file_path`.

    The extraction's own hint first, then the detector's FILE:LINE location
    when FILE names the same source file.
    """
    try:
        hint = int(error.get("source_line_hint") or 0)
    except (TypeError, ValueError):
        hint = 0
    if hint > 0:
        return hint
    location = str((error.get("detector_context") or {}).get("source_location", ""))
    name, _, number = location.rpartition(":")
    if name and number.isdigit() and Path(source_file_path).name == Path(name).name:
        return int(number)
    return 0


_BLOCKER_IDENTIFIER = re.compile(
    r"\b[A-Z][\w$]*(?:\.[a-z_][\w$]*)?\b(?:\s*\()?|\b[a-z_][\w$]*\.[a-z_][\w$]*\b")


def blocker_identifiers(text: str, declared=None) -> str:
    """The code identifiers a blocker's prose names, as a request symbol.

    A capitalised word is kept only when it reads as code: it has a capital,
    digit, underscore or $ after its first letter (WidgetCodec, SHA256,
    MAX_SIZE), it is called or qualified (Cipher(, Cipher.getInstance), or
    ``declared(name)`` says the repository declares it. Every capitalised
    word was kept, so k3v10's blocker "The packet does not include the hash
    implementation..." asked the repository for the symbol "The". A dotted
    lower-case name (cipher.init) is kept as before.
    """
    kept = []
    for found in _BLOCKER_IDENTIFIER.findall(text):
        item = found.strip()
        word = item.rstrip("(").strip()
        if word[:1].isupper() and not (
                "." in word or item.endswith("(")
                or re.search(r"[A-Z0-9_$]", word[1:])
                or (declared is not None and declared(word))):
            continue
        kept.append(item)
    return " ".join(dict.fromkeys(kept))


class FindingContextCollection:
    """Repository context gathered for a finding before its evidence packet is built."""

    def collect_expanded_repository_context(self, error: Dict) -> Dict:
        """Collect a bounded second-pass context for any under-evidenced spec."""
        try:
            from cognalys.extraction.migration_context import collect_migration_context

            repo_path = Path(str(error.get("repository_path", "")))
            relative_source = str(error.get("source_file_path", ""))
            source_file = repo_path / relative_source
            dependency_context = error.get("dependency_context", {})
            if repo_path.is_dir() and dependency_context:
                return {
                    "status": "available",
                    "analysis_kind": "external_dependency_context",
                    "dependency_context": dependency_context,
                    "repository_path": str(repo_path),
                }
            if not repo_path.is_dir() or not source_file.is_file():
                return {
                    "status": "unavailable",
                    "reason": "repository_or_source_file_not_found",
                    "repository_path": str(repo_path),
                    "source_file": relative_source,
                }
            context = collect_migration_context(
                repo_root=repo_path,
                source_file=source_file,
                method_name=str(
                    error.get("source_method_name")
                    or error.get("method_name", "")
                ),
                class_name=str(error.get("csv_class", "")),
                method_body=str(error.get("method_body", "")),
                multi_file_exact_chars=(
                    min(100_000, max(16_000, int(self.provider.context_length) * 2))
                    if self.provider.context_length else 100_000
                ),
                case_migration_policy=error.get("case_migration_policy", {}),
            )
            context["trigger"] = "pre_decision_repository_scope_analysis"
            context["applies_to_spec"] = str(error.get("spec", ""))
            return context
        except Exception as exc:
            self.logger.warning("Expanded repository context failed: %s", exc)
            return {"status": "unavailable", "reason": str(exc)}

    def collect_patch_safety_context(self, error: Dict) -> None:
        """Where the reported method's value goes, and whether it may be renamed.

        Both are deterministic readings of the repository, taken before the
        analysis so the model sees them and the gates can enforce them.
        """
        try:
            from cognalys.extraction.rename_safety import assess_rename_safety
            from cognalys.extraction.value_consumers import derived_value_consumers

            repo_path = Path(str(error.get("repository_path", "")))
            source = str(error.get("source_file_path", ""))
            if not repo_path.is_dir() or not source:
                return
            semantic = (
                (error.get("expanded_repository_context") or {})
                .get("multi_file_impact", {}) or {}
            ).get("semantic_resolution", {}) or {}
            method = str(
                error.get("source_method_name") or error.get("method_name", "")
            )
            error["derived_value_consumers"] = derived_value_consumers(
                repo_path, semantic.get("resolved_callers") or [], method,
                source_path=source, source_line=reported_line(error, source),
            )
            error["rename_safety"] = assess_rename_safety(
                repo_path, source, method, semantic,
            )
            # Whether anything reads the keys the method's value is written
            # under (Your Local Weather run_v3, 2026-11-02: NO_SAFE_FIX over
            # the compatibility of a hash no code reads). Only "no_reader"
            # reaches the model (persisted_value_readers.readers_evidence).
            from cognalys.utils.persisted_value_readers import unread_written_keys

            error["persisted_value_readers"] = unread_written_keys(
                repo_path, method, str(error.get("method_body", "")),
            )
            # Every file a rename (or a caller switch) must edit reaches the
            # plan as exact source, whatever the budget left out.
            from cognalys.extraction.multi_file_context import require_caller_sources
            from cognalys.extraction.rename_safety import MAX_REFERENCES

            references = error["rename_safety"].get("references") or []
            impact = (error.get("expanded_repository_context") or {}).get("multi_file_impact")
            if isinstance(impact, dict) and impact.get("status") == "available" \
                    and len(references) <= MAX_REFERENCES:
                require_caller_sources(impact, repo_path, sorted({
                    str(item.get("path", "")) for item in references
                    if isinstance(item, dict) and item.get("path") != source
                }))
        except Exception as exc:  # noqa: BLE001 -- recorded, never fatal
            self.logger.warning("Patch safety context failed: %s", exc)
        self.collect_typed_value_consumers(error)
        self.collect_deliberate_insecurity(error)

    def collect_deliberate_insecurity(self, error: Dict) -> None:
        """Signs that the reported behaviour is insecure on purpose, as evidence.

        weeWX Weather (2026-11-04): a trust-all manager under a class-level
        @SuppressLint("TrustAllX509TrustManager"), for the user's own
        self-signed server, with no setting; the PR replaced it with platform
        trust. The reading only informs the model (deliberate_insecurity).
        """
        try:
            from cognalys.utils.deliberate_insecurity import deliberate_insecurity

            repo_path = Path(str(error.get("repository_path", "")))
            source = str(error.get("source_file_path", ""))
            if not repo_path.is_dir() or not source:
                return
            error["deliberate_insecurity"] = deliberate_insecurity(
                repo_path, source,
                str(error.get("source_method_name") or error.get("method_name", "")),
                str(error.get("method_body", "")), str(error.get("csv_class", "")),
                reported_line(error, source),
            )
        except Exception as exc:  # noqa: BLE001 -- recorded, never fatal
            self.logger.warning("Deliberate-insecurity context failed: %s", exc)

    def collect_typed_value_consumers(self, error: Dict) -> None:
        """The app declarations that receive the reported method's value with its type.

        BlueGate run_v9 (2026-10-31): Authenticator's constructor takes the
        KeyPair getOrCreateKeyPair returns, through a class property of
        MainActivity; no plan included Authenticator.kt, so every draft could
        only rebuild the KeyPair in the callers, which has no effect. The
        declarations reach the plan (typed_value_consumers) and their files
        are exact sources, as rename_safety's references are.
        """
        try:
            from cognalys.extraction.multi_file_context import require_caller_sources
            from cognalys.extraction.typed_consumers import typed_value_consumers

            repo_path = Path(str(error.get("repository_path", "")))
            source = str(error.get("source_file_path", ""))
            if not repo_path.is_dir() or not source:
                return
            impact = (error.get("expanded_repository_context") or {}).get("multi_file_impact")
            semantic = ((impact or {}).get("semantic_resolution") or {}) if isinstance(
                impact, dict) else {}
            method = str(error.get("source_method_name") or error.get("method_name", ""))
            # The lexical call sites too: BlueGate run_v9's tree-sitter parse
            # failed (signal 11) and resolved no caller, while direct_callers
            # held both activities' calls.
            callers = [*(semantic.get("resolved_callers") or []),
                       *((impact.get("direct_callers") or []) if isinstance(impact, dict) else [])]
            error["typed_value_consumers"] = typed_value_consumers(
                repo_path, callers, method, source_path=source)
            paths = sorted({str(item.get("path", "")) for item in
                            error["typed_value_consumers"].get("consumers") or []
                            if item.get("path") and item.get("path") != source})
            if paths and isinstance(impact, dict) and impact.get("status") == "available":
                require_caller_sources(impact, repo_path, paths)
        except Exception as exc:  # noqa: BLE001 -- recorded, never fatal
            self.logger.warning("Typed value consumers failed: %s", exc)

    def collect_migration_grounding(self, error: Dict) -> None:
        """The repository's replacement idioms and its uses of a deprecated API."""
        finding = error.get("dependency_finding") or {}
        repo_path = Path(str(error.get("repository_path", "")))
        if finding.get("kind") != "api_migration" or not repo_path.is_dir():
            return
        try:
            from cognalys.extraction.migration_grounding import (
                deprecated_usage_contract,
                recommend_mechanisms,
                repository_replacement_idioms,
            )

            error["repository_replacement_idioms"] = repository_replacement_idioms(
                repo_path, finding,
            )
            error["deprecated_usage_contract"] = deprecated_usage_contract(
                repo_path, finding,
                source_file_path=str(error.get("source_file_path", "")),
            )
            if error.get("repository_replacement_idioms"):
                recommend_mechanisms(
                    error["repository_replacement_idioms"],
                    error["deprecated_usage_contract"],
                )
        except Exception as exc:  # noqa: BLE001 -- recorded, never fatal
            self.logger.warning("Migration grounding failed: %s", exc)

    def collect_stored_value_context(self, error: Dict) -> None:
        from cognalys.extraction.stored_value_contract import (
            collect_stored_value_contract, promote_stored_value_sources,
        )
        consumers = error.get("derived_value_consumers") or {}
        if not consumers.get("persisted"):
            return
        try:
            error["stored_value_contract"] = collect_stored_value_contract(
                Path(error["repository_path"]), consumers)
            promote_stored_value_sources(error)
        except Exception as exc:
            self.logger.warning("Stored value contract collection failed: %s", exc)
            error["stored_value_contract"] = {
                "status": "partial", "entries": [], "required_paths": [],
                "limitations": [str(exc)],
            }

    def replan_stored_value_migration(self, error: Dict, paths: set,
                                     apk_name: str, method_name: str) -> bool:
        """Recollect and revalidate once; gate text never authorizes a new file."""
        self.collect_stored_value_context(error)
        contract = error.get("stored_value_contract") or {}
        if contract.get("status") != "complete" or not paths <= set(contract.get("required_paths", [])):
            return False
        previous = error.get("evidence_analysis") or {}
        error["previous_validation_feedback"] = {
            **(error.get("previous_validation_feedback") or {}),
            "stored_value_scope": {"missing_paths": sorted(paths), "action": "replan all affected readers and writers"},
        }
        analysis, artifacts = self.adjudicate_finding(error, apk_name, method_name)
        if (analysis.get("decision") != previous.get("decision")
                or not analysis.get("coordinated_fix_plan", {}).get("required_edits")):
            return False
        previous.clear()
        previous.update(analysis)
        error["evidence_analysis"] = previous
        error["v2_artifacts"] = artifacts
        return True

    def _evidence_for_blocked_adjudication(
        self, error: Dict, packet: Dict, adjudication: Dict, artifact_prefix,
    ) -> List[Dict]:
        """Fetch what an evidence-blocked adjudication said was missing.

        _supplement_evidence answers the extraction's own requests, before
        adjudication. In exp_final_20261019, 21 adjudications blocked
        generation for an unconfirmed cause, missing code evidence or an
        unresolved target, and only 6 of them had had that round: the
        extraction had not asked, or the scope read as complete, and the
        adjudicator then named what it lacked in its blockers. Those blockers
        become requests here. Returns the catalogued entries, [] when none
        could be fetched (the caller then keeps the adjudication as it is).
        """
        blockers = [
            item for item in adjudication.get("blocking_for_generation") or []
            if isinstance(item, dict)
            and str(item.get("category", "")) in EVIDENCE_BLOCKER_CATEGORIES
        ]
        if not blockers:
            return []
        # A blocker is prose. Read as a request, words such as "derivation"
        # or "generator" classed YAPM's (rerun_correcoes_20261022) as needing
        # a running program, and it asked for SecretKeyHolder.secretKey's
        # construction -- source the checkout holds. The request keeps only
        # the code identifiers the blocker names, as a source request.
        repo_root = Path(str(error.get("repository_path", "")))
        index = RepositoryIndex(repo_root) if repo_root.is_dir() else None
        requests = [{
            "kind": "repository_source",
            "symbol": blocker_identifiers(
                f"{item.get('kind', '')} {item.get('description', '')}",
                declared=(lambda name: index.declaring_path(name) is not None)
                if index else None),
            "reason": str(item.get("description", "")),
        } for item in blockers]
        requests = [request for request in requests if request["symbol"]]
        if not requests:
            return []
        already = {
            str(item.get("data", {}).get("source_file_path", ""))
            for item in packet.get("evidence_catalog", [])
        }
        resolution = resolve_evidence_requests(
            repo_root, requests, already, index=index,
            target={
                "source_file_path": str(error.get("source_file_path", "")),
                "method_name": str(error.get("method_name", "")),
                "error_message": str(error.get("error_message", "")),
                "csv_class": str(error.get("csv_class", "")),
                "method_body": str(error.get("method_body", "")),
                "specification_context": error.get("specification_context", {}),
                "detector_context": error.get("detector_context") or {},
                "apk_name": str(error.get("apk_name", "")),
            },
        )
        supplement = catalog_supplement(resolution["entries"], prefix="ADJ")
        _write_json_atomic(
            artifact_prefix.with_name(artifact_prefix.name + "_adjudication_supplement.json"),
            {"blockers": len(blockers), "entries_supplied": len(supplement),
             "declined": resolution["declined"],
             "evidence_ids": [item["evidence_id"] for item in supplement]},
        )
        return supplement

    def _supplement_evidence(
        self, error: Dict, packet: Dict, extraction: Dict,
        sections: Dict, run_extraction, artifact_prefix, method_name: str,
    ) -> Dict:
        """Answer the extractor's own evidence requests, then extract again.

        The extraction schema has always ended with `needs_more_evidence` and
        a list of `requested_evidence` items naming what is missing. Across
        the 40 adjudicated findings in the recorded runs the flag was true 40
        times and nothing read either field. Scope completeness is close to
        decisive for the outcome -- 9 of 10 complete-scope findings reached an
        actionable decision, while 18 of 30 incomplete-scope ones abstained --
        so the requests the repository can answer are worth one more pass.

        Exactly one extra round runs, and only when something was actually
        fetched, so a finding whose requests are all runtime observations
        costs nothing. What was declined is recorded alongside what was
        added: the second extraction can still conclude "unknown", but no
        longer because nobody looked.
        """
        requests = [item for item in extraction.get("requested_evidence") or []
                    if isinstance(item, dict)]
        completeness = packet.get("scope_completeness", {}) or {}
        scope_settled = all(
            value == "complete"
            for key, value in completeness.items()
            if key not in {"limitations", "overall", "external_contract"}
        )
        if not requests or not extraction.get("needs_more_evidence") or scope_settled:
            return extraction

        repo_root = Path(str(error.get("repository_path", "")))
        already = {
            str(item.get("data", {}).get("source_file_path", ""))
            for item in packet.get("evidence_catalog", [])
        }
        resolution = resolve_evidence_requests(
            repo_root, requests, already,
            # The reported source and message, so a request to resolve what a
            # call binds to can be answered from that file's imports instead
            # of being declined for naming a platform type no repository file
            # declares (see evidence_requests._binding_entry).
            target={
                "source_file_path": str(error.get("source_file_path", "")),
                "method_name": str(error.get("method_name", "")),
                "error_message": str(error.get("error_message", "")),
                "csv_class": str(error.get("csv_class", "")),
                "method_body": str(error.get("method_body", "")),
                "specification_context": error.get("specification_context", {}),
                "detector_context": error.get("detector_context") or {},
                "apk_name": str(error.get("apk_name", "")),
            },
        )
        supplement = catalog_supplement(resolution["entries"])
        # Requests and entries are counted apart. One dependency request
        # answered with three build files was logged as "3 of 4 requests
        # answered" for jellyfin, while three of its four were declined.
        answered = len(requests) - len(resolution["declined"])
        record = {
            "requested": len(requests),
            "requests_answered_from_repository": answered,
            "entries_supplied": len(supplement),
            "declined": resolution["declined"],
            "evidence_ids": [item["evidence_id"] for item in supplement],
        }
        _write_json_atomic(
            artifact_prefix.with_name(
                artifact_prefix.name + "_evidence_supplement.json"),
            record,
        )
        if not supplement:
            packet["supplementary_retrieval"] = record
            return extraction

        _write_json_atomic(
            artifact_prefix.with_name(
                artifact_prefix.name + "_evidence_extraction_round1.json"),
            extraction,
        )
        packet["evidence_catalog"] = [
            *packet.get("evidence_catalog", []), *supplement,
        ]
        packet["supplementary_retrieval"] = record
        self.logger.info(
            "Supplemented evidence for %s (%s): %d of %d requests answered "
            "from the repository with %d entries (%s)",
            method_name, error.get("unique_error_id", ""),
            answered, len(requests), len(supplement),
            ", ".join(record["evidence_ids"]),
        )
        # Round 2 is round 1's request with the fetched entries appended,
        # not the packet sent again with them inside its catalog: OpenAI
        # finds a cached prefix only at a part boundary, so round 1 is read
        # back from the cache whole however long the appendix is. The
        # in-memory packet holds them in evidence_catalog (above), so
        # validation, adjudication and the plan read them as before.
        return run_extraction(sections, "round_2", appendix=(
            SUPPLEMENT_NOTE,
            {"supplementary_evidence_catalog": without_entry_hashes(supplement),
             "previously_requested_evidence": record},
        ))
