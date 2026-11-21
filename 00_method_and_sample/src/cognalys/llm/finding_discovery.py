"""Which findings a run analyses, and which it closes before any model call.

Moved verbatim out of generate_error_fixes.py; see FixGenerator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import (
    Dict,
    List,
)

_LLM_MODULE_DIR = Path(__file__).resolve().parent
_SRC_DIR = _LLM_MODULE_DIR.parent

from cognalys.llm.migration_policy import (  # noqa: E402
    generic_migration_policy,
    resolve_case_policy,
)
from cognalys.llm.pipeline_architecture import message_names_dependency  # noqa: E402
from cognalys.utils.detector_labels import current_detector_context  # noqa: E402


class FindingDiscovery:
    """Which findings a run analyses, and which it closes before any model call."""

    def discover_errors(self) -> List[Dict]:
        """Discover all error JSONs in input directory.

        A finding file that cannot be read or parsed is recorded in
        `self.input_invalid` instead of being dropped: dropped, it vanished
        from total_errors as well, so the run's denominator shrank by
        exactly the findings it could not look at.
        """
        errors = []
        self.input_invalid = []

        for json_file in self.input_dir.rglob("*.json"):
            try:
                with open(json_file) as f:
                    raw_data = json.load(f)

                # Handle nested structure from extract_sample_methods_tracked.py
                if "error" in raw_data and isinstance(raw_data["error"], dict):
                    # Extract from nested structure
                    error_obj = raw_data["error"]
                    method_obj = raw_data.get("method_info", {})
                    source_obj = raw_data.get("source_code", {})
                    extraction_obj = raw_data.get("extraction_metadata", {})

                    class_obj = raw_data.get("class_info", {})
                    source_file_path = class_obj.get("source_file_path", "")
                    relative_method_file = class_obj.get("relative_method_file", "")
                    if not relative_method_file and source_file_path:
                        relative_method_file = str(
                            Path(extraction_obj.get("apk_name", "")) / source_file_path
                        )

                    error_data = {
                        "error_message": error_obj.get("error_message", "Unknown error"),
                        "unique_error_id": error_obj.get("unique_error_id", ""),
                        "method_body": source_obj.get("method_body", ""),
                        "method_name": error_obj.get("csv_method") or method_obj.get("name", "unknown"),
                        "source_method_name": method_obj.get(
                            "source_name", error_obj.get("csv_method", "")
                        ),
                        "source_line_hint": method_obj.get("source_line_hint"),
                        "csv_class": error_obj.get("csv_class", ""),
                        "spec": error_obj.get("spec", ""),
                        "tool": error_obj.get("tool", ""),
                        # The record as stored, with the label catalogue's
                        # current texts for a known label: a stored meaning
                        # written before a rewording would otherwise reach
                        # the packet, the adjudication and the IV settlement
                        # in its old words (utils.detector_labels).
                        "detector_context": current_detector_context(
                            error_obj.get("detector_context") or {}),
                        "error_index": extraction_obj.get("csv_index", 0),
                        "repository_url": extraction_obj.get(
                            "repository_url", ""
                        ),
                        "repository_path": extraction_obj.get(
                            "repository_path", ""
                        ),
                        "extraction_status": extraction_obj.get(
                            "extraction_status", ""
                        ),
                        "pinned_revision": extraction_obj.get(
                            "pinned_revision", ""
                        ),
                        "contribution_base": extraction_obj.get(
                            "contribution_base", {}
                        ),
                        "dependency_context": raw_data.get(
                            "dependency_context", {}
                        ),
                        "apk_evidence": raw_data.get("apk_evidence", {}),
                        "apk_bytecode": raw_data.get("apk_bytecode", {}),
                        "dependency_finding": raw_data.get("dependency_finding", {}),
                        "application_namespace": extraction_obj.get(
                            "application_namespace", {}
                        ),
                        "class_resolution": extraction_obj.get(
                            "class_resolution", {}
                        ),

                        # NOVOS CAMPOS
                        "relative_method_file": relative_method_file,
                        "source_file_path": source_file_path,
                        "imports": source_obj.get("imports", ""),
                        "surrounding_context": source_obj.get(
                            "surrounding_context", ""
                        ),
                        "call_sites": source_obj.get("call_sites", ""),
                        "implicit_receiver_calls": source_obj.get(
                            "implicit_receiver_calls", []
                        ),
                        "method_parameter_resolution": source_obj.get(
                            "method_parameter_resolution", []
                        ),
                        "migration_context": source_obj.get(
                            "migration_context", {}
                        ),
                        "specification_context": raw_data.get(
                            "specification_context", {}
                        ),
                        "repository_guidance": raw_data.get(
                            "repository_guidance", []
                        ),
                        "branch_policy": raw_data.get("branch_policy", {}),

                        "_file_path": json_file
                    }
                else:
                    # Handle flat structure (backward compatibility)
                    error_data = raw_data.copy()
                    error_data["_file_path"] = json_file
                    if "_file_path" not in error_data:
                        error_data["_file_path"] = json_file
                    # The same current texts as the nested structure, only
                    # when the record carries one: no key is added.
                    if "detector_context" in error_data:
                        error_data["detector_context"] = current_detector_context(
                            error_data["detector_context"])

                # Validate required fields
                required_fields = ["error_message", "unique_error_id", "method_body", "method_name"]
                if all(field in error_data for field in required_fields):
                    inferred_policy = generic_migration_policy(error_data)
                    # Exact finding id, then family, then the inferred
                    # defaults -- most specific wins.
                    explicit_policy = resolve_case_policy(
                        self.migration_policies, error_data
                    )
                    error_data["case_migration_policy"] = {
                        **inferred_policy, **explicit_policy,
                    }
                    # Read only by the source recheck, so kept out of the
                    # policy every prompt serializes: in the policy it changed
                    # each prompt and so every cache key (subset_newpr2).
                    if error_data["case_migration_policy"].pop(
                            "keeps_legacy_derivation", False):
                        error_data["keeps_legacy_derivation"] = True
                    errors.append(error_data)
            except (OSError, ValueError) as e:
                self.logger.warning(f"Failed to load {json_file}: {e}")
                self.input_invalid.append({
                    "file": str(json_file),
                    "cause": f"{type(e).__name__}: {e}"[:300],
                })
            except Exception as e:  # noqa: BLE001 -- counted as a crash below
                # A defect in this pipeline's loading code, not a bad file.
                self.logger.exception("Loading %s crashed: %s", json_file, e)
                self.input_invalid.append({
                    "file": str(json_file),
                    "cause": f"{type(e).__name__}: {e}"[:300],
                    "kind": "crashed",
                })

        return sorted(errors, key=lambda x: str(x["_file_path"]))

    @staticmethod
    def solution_key(error: Dict) -> tuple:
        """(APK directory, finding id): what a saved solution answers.

        A solution is written under the APK directory of the finding's
        extraction record (output_dir/<apk>/...), and unique_error_id does not
        name the APK: two APKs embedding the same library method report the
        same id, and resuming by the id alone skipped the second APK's finding
        because the first had a solution.
        """
        return (Path(error.get("_file_path", "")).parent.name,
                str(error.get("unique_error_id", "")))

    def check_existing_solutions(self) -> set:
        """(APK directory, finding id) of every finding that already has a solution."""
        existing = set()
        for sol_file in self.output_dir.rglob("*_solution.json"):
            try:
                with open(sol_file) as f:
                    sol = json.load(f)
            except (OSError, ValueError) as exc:
                self.logger.warning("Solution %s not read: %s", sol_file, exc)
                continue
            original = sol.get("original_error") if isinstance(sol, dict) else None
            error_id = original.get("unique_error_id") if isinstance(original, dict) else None
            if error_id:
                existing.add((sol_file.parent.name, str(error_id)))
        return existing

    @staticmethod
    def unanalysable_reason(error: Dict) -> str:
        """Why this finding cannot be analysed at all, or "" if it can.

        run_20260909_100446 spent 2m19s of model time on
        org.mifos.mobilebanking.api.SelfServiceOkHttpClient to conclude that
        every predicate was unknown, because the class does not exist in the
        analysed checkout: the CVE names tag v1.0.0 and the clone is the
        Kotlin Multiplatform rewrite, so extraction wrote an empty
        method_body, an empty source_file_path and no context at all. There
        was nothing to adjudicate before the first token was spent.

        Deliberately narrow. An empty body alone is not enough: a dependency
        finding is fixed by changing a declared version, not by editing a
        method, and DEPENDENCY_FIX_SUPPORTED exists for exactly that. So this
        withholds a finding only when there is neither source to read nor an
        identified artifact to upgrade.
        """
        dependency = error.get("dependency_context") or {}
        bytecode = dependency.get("external_target_bytecode") or {}
        apk = error.get("apk_evidence") or {}
        extraction_status = str(error.get("extraction_status", "")) or "unknown"
        # Rows extraction recorded without any source, so that the run's own
        # reports account for every input row (see _write_unextracted_record
        # in the extractor). Each says what stopped it, and each begins
        # differently, because the retrospective's failure signature is the
        # start of this text.
        if extraction_status == "repository_not_cloned":
            return (
                "the repository could not be cloned "
                "(extraction_status=repository_not_cloned), so there is no "
                "checkout to read and no declared dependency to change"
            )
        if extraction_status == "pinned_revision_unavailable":
            return (
                "the commit the APK was built from could not be checked out "
                "(extraction_status=pinned_revision_unavailable); the default "
                "branch is other code than the one the finding was reported "
                "against, so it is not read in its place"
            )
        if extraction_status == "source_file_unreadable":
            return (
                "the reported class's source file could not be read "
                "(extraction_status=source_file_unreadable), so there is no "
                "code to review"
            )
        if extraction_status == "missing_class_or_method":
            return (
                "the input row names no class or no method "
                "(extraction_status=missing_class_or_method), so there is no "
                "target to locate"
            )
        # The class exists and declares no method by the reported name. A
        # message naming a dependency or deprecation is still analysable from
        # the build files and the class, which is what dependency_finding
        # records; anything else has nothing left to read.
        if (extraction_status == "method_not_found"
                and not str(error.get("method_body", "")).strip()
                and not (error.get("dependency_finding") or {}).get("status")):
            resolution = error.get("class_resolution") or {}
            located = (
                f" (located by its simple name as "
                f"{resolution.get('resolved_class')})"
                if resolution.get("method") == "simple_name_fallback" else ""
            )
            return (
                "the reported class is in the checkout at "
                f"{error.get('source_file_path', '') or 'an unrecorded path'}"
                f"{located} but declares no method named "
                f"{error.get('method_name', '')} "
                "(extraction_status=method_not_found), and the message names "
                "no dependency or deprecation to check, so there is no code "
                "to review"
            )
        has_source = any(
            str(error.get(field, "")).strip() for field in (
                "method_body", "source_file_path", "surrounding_context",
            )
        )
        # Bytecode is code to review even when it is not this project's
        # source: it is what the detector analysed, and the APK's copy is the
        # very build it analysed.
        if has_source or bytecode.get("methods") or apk.get("methods"):
            return ""
        # A dependency the checkout declares is something to change even
        # when the class the tool attached the finding to is gone.
        if str((error.get("dependency_finding") or {}).get("status", "")) in {
            "confirmed", "declared_version_differs",
            "declared_version_unresolved", "used_but_not_declared",
        }:
            return ""
        apk_note = (
            f"; the analysed APK's bytecode was {apk['status']}"
            + (f" ({apk['reason']})" if apk.get("reason") else "")
            if apk.get("status") else ""
        )
        # An identified dependency is analysable whoever's class reported it:
        # a dependency finding reported at an application class is fixed in
        # the build file, not in the class -- provided the finding is about
        # that dependency. run_20260914_122712 sent two application classes
        # with no source to the model because a declared artifact shared a
        # name with the application's own organisation (org.kiwix:libkiwix
        # under org.kiwix.kiwixmobile, SimpleMobileTools:Simple-Commons under
        # com.simplemobiletools.gallery.pro), while their messages -- an
        # OutOfMemoryError and a SecurityException -- named no library. Six
        # model calls and ten minutes concluded that there was no source.
        likely = dependency.get("likely_dependency") or {}
        application_class = extraction_status in {
            "application_class_not_in_checkout",
            # Same absence, established rather than suspected: git says the
            # class was in the release this APK was built from and is not in
            # the checkout now (see extraction/release_ref.py). It is
            # reported with that evidence instead of the guess below.
            "application_class_removed_since_release",
            # Absence not established: files the index could not read.
            "application_class_index_incomplete",
        }
        removed_since_release = (
            extraction_status == "application_class_removed_since_release"
        )
        identified = str(dependency.get("status", "")) == "identified"
        if identified and (
            not application_class
            or message_names_dependency(
                str(error.get("error_message", "")), likely,
            )
        ):
            return ""
        if application_class:
            namespace = error.get("application_namespace") or {}
            package = namespace.get("package") or ""
            if extraction_status == "application_class_index_incomplete":
                unreadable = (
                    (error.get("class_resolution") or {}).get(
                        "index_unreadable_files") or []
                )
                return (
                    "the reported class was not found, but the class index "
                    "is incomplete (extraction_status="
                    "application_class_index_incomplete): "
                    f"{len(unreadable)} source file(s) could not be read "
                    f"({', '.join(unreadable[:3])}), so the class may be in "
                    "one of them. Re-run extraction once the files are "
                    "readable; this is not evidence that the class is absent"
                )
            if removed_since_release:
                history = (
                    (error.get("class_resolution") or {}).get("history") or {}
                )
                reference = history.get("release_ref") or {}
                return (
                    "the reported class was removed or renamed after the "
                    "analysed release (extraction_status="
                    "application_class_removed_since_release): git shows "
                    f"{history.get('historical_path', 'it')} at tag "
                    f"{reference.get('tag', '')}, which this APK was built "
                    "from, and no such file in the checkout now. The finding "
                    "was real for that build and there is nothing left in "
                    "this repository to repair, so it is stale rather than "
                    "unanalysable"
                )
            unnamed_dependency = (
                "; a declared dependency "
                f"({likely.get('group', '')}:{likely.get('artifact', '')}) "
                "matched the class by name, but the message does not name it"
                if identified else ""
            )
            if (not namespace.get("name_looks_obfuscated")
                    and apk.get("reason") == "class_not_in_apk"):
                # Downloaded, not minified, and the class is not in it. The
                # build the finding claims to come from does not contain the
                # reported class, which no choice of checkout revision
                # explains: NewPipe's VideoPlayer and Kodi's NativeLoader in
                # run_20260914_122712 were blamed on the revision.
                return (
                    "the reported class is in neither the analysed checkout "
                    "nor the analysed APK (extraction_status="
                    "application_class_not_in_checkout, apk_evidence="
                    "class_not_in_apk): it is in the application's own "
                    f"package {package}, and the APK, which is not minified, "
                    "does not contain it, so the finding does not correspond "
                    "to the build it was reported against"
                    + unnamed_dependency
                    + "; there is no code to review and no dependency to change"
                )
            return (
                "extraction produced no source for the reported target "
                "(extraction_status=application_class_not_in_checkout): the "
                "reported class is in the application's own package "
                f"{package} but not in the analysed "
                "checkout, "
                + (
                    "and its name looks obfuscated, so the checkout would "
                    "hold it under its original name"
                    if namespace.get("name_looks_obfuscated") else
                    "so the checkout is probably not the revision the APK "
                    "was built from"
                )
                + apk_note
                + unnamed_dependency
                + "; there is no code to review and no dependency to change"
            )
        # A declared artifact that names the owning library is enough to
        # analyse. The outcome may well be a classification rather than an
        # upgrade, but "no dependency" was being recorded as a fact:
        # jellyfin's okhttp3.internal.platform.Platform arrives through
        # coil-network-okhttp, which the project declares by name.
        if dependency.get("lexical_carriers"):
            return ""
        # Published metadata leading from a declared artifact to the owning
        # library. app.maskan.chat's Tink finding was skipped with
        # security-crypto declared and its POM listing tink-android.
        metadata = dependency.get("cached_artifact_metadata") or {}
        if metadata.get("carriers"):
            return ""
        extraction_status = str(error.get("extraction_status", "")) or "unknown"
        # Said as what was and was not checked, never as absence: the name
        # scan and the resolved graph are different evidence, and the second
        # failing is not the dependency being missing.
        graph = dependency.get("dependency_graph") or {}
        graph_status = str(graph.get("status", ""))
        graph_note = (
            f"; the resolved dependency graph was {graph_status}"
            + (f" ({graph.get('reason')})" if graph.get("reason") else "")
            if graph_status and graph_status != "available" else ""
        )
        # A match below the identification threshold is still a match, and
        # was being reported as none: maskan's security-crypto scored and the
        # refusal said no declared dependency matched.
        weak = [
            f"{item.get('group', '')}:{item.get('artifact', '')}"
            for item in dependency.get("dependency_candidates") or []
            if isinstance(item, dict)
        ]
        name_note = (
            f"{len(weak)} declared "
            f"dependenc{'y' if len(weak) == 1 else 'ies'} matched it by name "
            f"only below the identification threshold ({', '.join(weak[:3])})"
            if weak else "no declared dependency matched it by name"
        )
        metadata_note = (
            "; cached dependency metadata led to no carrier "
            f"({metadata['status']})" if metadata.get("status") else ""
        )
        bytecode_note = (
            f"; the owning library's bytecode was {bytecode['status']}"
            + (f" ({bytecode['reason']})" if bytecode.get("reason") else "")
            if bytecode.get("status") else ""
        )
        return (
            "extraction produced no source for the reported target "
            f"(extraction_status={extraction_status}, "
            f"dependency_context.status={dependency.get('status', 'absent')}): "
            "the reported class was not found in the analysed checkout and "
            + name_note + metadata_note + graph_note + bytecode_note + apk_note
            + ", so "
            "there is neither code to review nor a declared version to change"
        )

    @staticmethod
    def not_applicable_reason(error: Dict) -> str:
        """Why a dependency or deprecation finding has nothing to change here.

        Two outcomes of the deterministic check, and only those two: the
        checkout already declares a version meeting the reported target, or it
        neither declares nor imports anything the message names. Everything
        else -- a version below the target, one that could not be resolved, a
        library used without a declaration -- still goes to the analysis.
        """
        finding = error.get("dependency_finding") or {}
        status = str(finding.get("status", ""))
        names = ", ".join(
            [str(item.get("coordinate") or item.get("name", ""))
             for item in finding.get("subjects") or []]
            + list(finding.get("deprecated_types") or [])
        ) or "what the finding names"
        if status == "already_satisfied":
            declared = "; ".join(
                f"{item.get('version_expression', '')} in "
                f"{item.get('source_file_path', '')}:{item.get('line', '')}"
                for item in (finding.get("matched_declarations") or [])[:5]
            )
            return (
                f"the checkout already declares {names} at a version meeting the "
                f"reported target {finding.get('target', {}).get('version', '')} "
                f"({declared}), so nothing is left to change on this branch"
            )
        if status == "not_found":
            return (
                f"the finding names {names}, but this checkout neither declares "
                "it in any build file nor imports it in any source file, so "
                "there is nothing here to change"
            )
        return ""

    def group_by_specification(self, errors: List[Dict]) -> List[Dict]:
        """Order findings so that one specification's run consecutively.

        Grouping is by spec first and repository second, which also keeps a
        repository's expanded-context reads together. Order within a group
        is the discovery order, so a run remains reproducible.
        """
        return sorted(
            errors,
            key=lambda error: (
                str(error.get("spec", "")),
                str(error.get("repository_url", "")),
                str(error.get("unique_error_id", "")),
            ),
        )
