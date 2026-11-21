# solution_mapper.py

"""
Solution Mapper - discovers and maps error solutions to cloned repositories.
Uses relative_method_file as the authoritative path to locate source files.
"""

import difflib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from cognalys.solutions.scope_utils import brace_depth_at, scope_change_outside_edit
from cognalys.utils.edit_text import read_source, write_source


def stored_contract_from_artifacts(solution_file: Path, metadata: dict, original_error: dict) -> dict:
    """Compatibility with solutions written before contracts were serialized."""
    if metadata.get("stored_value_contract"):
        return metadata["stored_value_contract"]
    method = re.sub(r"[^A-Za-z0-9._-]+", "_", metadata.get("method_name", ""))
    packet_path = (solution_file.parent.parent / "v2_artifacts" / metadata.get("apk_name", "")
                   / f"{metadata.get('error_index', 0)}_{method}_evidence_packet.json")
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not original_error.get("unique_error_id") or packet.get("finding", {}).get("finding_id") != original_error["unique_error_id"]:
        return {}
    return next((item["data"] for item in packet.get("evidence_catalog", [])
                 if item.get("kind") == "stored_value_contract"), {})


class MethodReplacer:
    """Replace entire method body directly in source file."""

    def __init__(self, logger):
        self.logger = logger

    @staticmethod
    def _first_line_indent(text: str) -> str:
        first = next((line for line in text.splitlines() if line.strip()), "")
        return re.match(r"^[ \t]*", first).group(0)

    @staticmethod
    def keeps_generated_layout(original_method_body: str, new_method_body: str) -> bool:
        """Whether a generated method must be spliced with its own indentation.

        Two shapes carry their layout in the text itself: an original_code
        that runs through an enclosing `}` (its braces, comments and
        literals aside, it closes more than it opens), and a replacement
        with a line less indented than its first one. Shifting either moves
        the closing brace and whatever follows it into the wrong column --
        Fread's buildSSLFactory edit (2026-10-15) ended with the object's
        `}` and a new top-level class, and moved both four spaces in.
        """
        if brace_depth_at(original_method_body, len(original_method_body)) < 0:
            return True
        lines = [line for line in new_method_body.strip("\r\n").splitlines()
                 if line.strip()]
        if not lines:
            return False
        first = len(lines[0]) - len(lines[0].lstrip(" \t"))
        return any(len(line) - len(line.lstrip(" \t")) < first
                   for line in lines[1:])

    @staticmethod
    def align_generated_indentation(
        original_method_body: str,
        new_method_body: str,
    ) -> str:
        """Align an LLM method with the original declaration indentation.

        Every line moves by the difference between the two first lines'
        indentation, so the method's own nesting is kept and nothing goes
        below column 0. No difference, or a shape whose layout is part of
        its meaning (`keeps_generated_layout`), leaves the indentation as
        the model wrote it.
        """
        declaration_indent = MethodReplacer._first_line_indent(original_method_body)
        normalized = new_method_body.strip("\r\n")
        generated_indent = MethodReplacer._first_line_indent(normalized)
        if (generated_indent == declaration_indent
                or MethodReplacer.keeps_generated_layout(
                    original_method_body, new_method_body)):
            return "\n".join(
                line if line.strip() else ""
                for line in normalized.splitlines()
            )
        # keeps_generated_layout ruled out a line less indented than the
        # first, so each non-blank line starts with at least that many
        # whitespace characters.
        return "\n".join(
            declaration_indent + line[len(generated_indent):]
            if line.strip() else ""
            for line in normalized.splitlines()
        )

    @staticmethod
    def preserve_unchanged_trailing_whitespace(
        original_method_body: str,
        new_method_body: str,
    ) -> str:
        """Undo trailing-whitespace-only edits to lines that did not change.

        A generated method is rewritten whole, so it arrives with the
        model's whitespace habits applied to every line it left alone --
        almost always trailing spaces stripped from blank lines. Those lines
        then appear in the diff a maintainer reviews. gtalksms's HelpCmd
        constructor (run_20260911_125911) changed by two lines and produced
        a 104-line diff; ten of the rewritten lines were blank ones whose
        trailing spaces had been removed.

        Only trailing whitespace is restored. A line whose *leading*
        whitespace changed is kept as generated, because that is how a block
        moved one level deeper legitimately reads -- 41 of the lines in that
        same diff were the loop body indenting into a new synchronized
        block, and flattening them back would be wrong.

        Trailing whitespace is insignificant in every language this pipeline
        patches, so restoring it can only change the diff, never the
        program.
        """
        original_lines = original_method_body.splitlines()
        new_lines = new_method_body.splitlines()
        matcher = difflib.SequenceMatcher(
            None,
            [line.strip() for line in original_lines],
            [line.strip() for line in new_lines],
            autojunk=False,
        )
        merged: List[str] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                merged.extend(new_lines[j1:j2])
                continue
            for original, generated in zip(
                original_lines[i1:i2], new_lines[j1:j2]
            ):
                keep_original = (
                    original != generated
                    and original.rstrip() == generated.rstrip()
                )
                merged.append(original if keep_original else generated)
        suffix = "\n" if new_method_body.endswith("\n") else ""
        return "\n".join(merged) + suffix

    @staticmethod
    def remove_duplicate_leading_annotations(
        source_content: str,
        original_method_body: str,
        new_method_body: str,
    ) -> str:
        """Remove generated annotations already attached to the source method.

        Method extraction intentionally starts at the declaration, so an LLM
        may repeat an annotation visible in surrounding context. Only exact
        annotation blocks found immediately above the original method are
        removed; genuinely new annotations remain untouched.
        """
        method_index = source_content.find(original_method_body)
        if method_index < 0:
            return new_method_body

        prefix = source_content[:method_index]
        annotation_zone = re.split(r"\n[ \t]*\n", prefix)[-1]

        def normalized(value: str) -> str:
            return re.sub(r"\s+", "", value)

        normalized_zone = normalized(annotation_zone)
        lines = new_method_body.splitlines(keepends=True)
        offset = 0
        while offset < len(lines):
            if not lines[offset].lstrip().startswith("@"):
                break
            start = offset
            balance = 0
            while offset < len(lines):
                line = lines[offset]
                balance += line.count("(") - line.count(")")
                offset += 1
                if balance <= 0:
                    break
            block = "".join(lines[start:offset]).strip()
            if normalized(block) not in normalized_zone:
                offset = start
                break

        if offset == 0:
            return new_method_body
        return "".join(lines[offset:]).lstrip("\r\n")

    def replace_method(
        self,
        source_file: Path,
        original_method_body: str,
        new_method_body: str
    ) -> bool:
        """Whether the method was replaced; replace_method_status says why not."""
        return self.replace_method_status(
            source_file, original_method_body, new_method_body) == APPLIED

    def replace_method_status(
        self,
        source_file: Path,
        original_method_body: str,
        new_method_body: str
    ) -> str:
        """Replace the one occurrence of the reviewed body; APPLIED or why not.

        The coordinated path already required exactly one occurrence of an
        edit's original code (ORIGINAL_CODE_NOT_UNIQUE); this one replaced
        the first of several, so a file holding the same body twice was
        patched wherever the first copy happened to be. And a corrected body
        already present was reported as applied, a success that changed
        nothing; it is ALREADY_APPLIED now (PLANO_CORRECOES_DEFESA A5).
        """
        try:
            # LF text to match the generated body against, written back in
            # the file's own line endings (edit_text.write_source).
            content = read_source(source_file)

            occurrences = content.count(original_method_body) if original_method_body else 0
            if occurrences == 0:
                if new_method_body.strip() and new_method_body.strip() in content:
                    self.logger.info(
                        "Corrected method is already present in %s and the "
                        "reviewed one is not: nothing to apply",
                        source_file,
                    )
                    return ALREADY_APPLIED
                self.logger.warning(
                    f"Original method body not found in {source_file}"
                )
                return ORIGINAL_CODE_NOT_FOUND
            if occurrences > 1:
                self.logger.warning(
                    "Original method body occurs %d times in %s; which one "
                    "the finding is about is ambiguous", occurrences, source_file,
                )
                return ORIGINAL_CODE_NOT_UNIQUE

            # After alignment, not before: align_generated_indentation
            # rewrites every blank line to "", which is the trailing-space
            # churn this removes.
            aligned_method_body = self.preserve_unchanged_trailing_whitespace(
                original_method_body,
                self.align_generated_indentation(
                    original_method_body,
                    self.remove_duplicate_leading_annotations(
                        content,
                        original_method_body,
                        new_method_body,
                    ),
                ),
            )

            scope_change = scope_change_outside_edit(
                content, original_method_body, aligned_method_body)
            if scope_change:
                self.logger.warning(
                    "Generated method would change the scope of the code "
                    "after it in %s: %s", source_file, scope_change,
                )
                return SCOPE_CHANGE

            updated_content = content.replace(
                original_method_body,
                aligned_method_body,
                1
            )

            write_source(source_file, updated_content)

            self.logger.info(
                f"Patched method in {source_file}"
            )

            return APPLIED

        except Exception as e:
            self.logger.error(
                f"Patch failed for {source_file}: {e}"
            )
            return PATCH_FAILED


# What MethodReplacer.replace_method_status answers.
APPLIED = "APPLIED"
ALREADY_APPLIED = "ALREADY_APPLIED"
ORIGINAL_CODE_NOT_FOUND = "ORIGINAL_CODE_NOT_FOUND"
ORIGINAL_CODE_NOT_UNIQUE = "ORIGINAL_CODE_NOT_UNIQUE"
SCOPE_CHANGE = "GENERATED_METHOD_CHANGES_SCOPE"
PATCH_FAILED = "PATCH_FAILED"


class SolutionMapper:
    """Maps error solutions to their source repositories."""

    def __init__(self, error_solutions_dir: str, cloned_projects_dir: str):
        self.error_solutions_dir = Path(error_solutions_dir)
        self.cloned_projects_dir = Path(cloned_projects_dir)

        self.logger = logging.getLogger(self.__class__.__name__)

        self.solutions: List[Dict] = []
        self.mapping: Dict = {}

        self.replacer = MethodReplacer(self.logger)

    def discover_solutions(self) -> List[Dict]:
        """Discover all solution JSON files."""

        self.logger.info(
            f"Discovering solutions in {self.error_solutions_dir}"
        )

        self.solutions = []

        for solution_file in self.error_solutions_dir.glob("**/*_solution.json"):

            try:
                with open(solution_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                metadata = data.get('solution_metadata', {})
                corrected = data.get('corrected_implementation', {})
                original_error = data.get('original_error', {})

                corrected_method = corrected.get('method_body')
                coordinated_edits = corrected.get("coordinated_edits", [])
                corrected_explanation = corrected.get('explanation')
                original_method = original_error.get('method_body')

                # A coordinated fix carries its whole patch in
                # coordinated_edits and may legitimately have no single
                # corrected method body -- a patch that only creates a test
                # and deletes an import has no "the method" at all.
                # _apply_single_solution already branches on coordinated_edits
                # and never reads the body in that case, but discovery still
                # demanded one and dropped the solution before the applicator
                # ever saw it. net.gaast.giggity generated a valid three-edit
                # patch that vanished here: mapping_report reported 0 apks and
                # the run produced no pull request at all.
                has_coordinated_edits = bool(
                    coordinated_edits
                    if isinstance(coordinated_edits, list) else []
                )
                if not corrected_method and not has_coordinated_edits:
                    self.logger.warning(
                        f"Empty corrected method_body in {solution_file.name}"
                    )
                    continue

                if not corrected_explanation:
                    self.logger.warning(
                        f"Empty corrected explanation in {solution_file.name}"
                    )
                    continue

                relative_method_file = metadata.get("relative_method_file")
                if not relative_method_file and metadata.get("source_file_path"):
                    relative_method_file = str(
                        Path(metadata.get("apk_name", ""))
                        / metadata["source_file_path"]
                    )

                if not relative_method_file:
                    self.logger.warning(
                        f"No relative_method_file in {solution_file.name}"
                    )
                    continue

                solution = {
                    "apk_name": metadata.get('apk_name'),
                    "method_name": metadata.get('method_name'),
                    "csv_class": metadata.get('csv_class'),
                    "spec": metadata.get("spec")
                    or original_error.get("spec", ""),
                    "error_message": original_error.get("error_message", ""),
                    "tool": metadata.get("tool")
                    or original_error.get("tool", ""),
                    # What the detector recorded (its code and site kind):
                    # the replay checks the constraint the finding reported.
                    "detector_context": original_error.get("detector_context") or {},

                    "relative_method_file": relative_method_file,
                    "source_file_path": metadata.get("source_file_path")
                    or str(Path(*Path(relative_method_file).parts[1:])),

                    "original_method_body": original_method,
                    "corrected_method_body": corrected_method,
                    "corrected_explanation": corrected_explanation,
                    "coordinated_edits": coordinated_edits
                    if isinstance(coordinated_edits, list) else [],
                    "fix_scope": metadata.get("fix_scope", "single_method"),
                    "review_contract_version": metadata.get("review_contract_version"),
                    "decision": metadata.get("decision", ""),
                    "manual_review_required": metadata.get(
                        "manual_review_required", False
                    ),
                    "auto_apply": metadata.get("auto_apply", True),
                    "publication_allowed": metadata.get(
                        "publication_allowed", True
                    ),
                    "publication_status": metadata.get("publication_status", ""),
                    "semantic_validation": metadata.get(
                        "semantic_validation", {"status": "not_run"}
                    ),
                    "llm_architecture": metadata.get("llm_architecture", {}),
                    "authorized_patch_plan": metadata.get(
                        "authorized_patch_plan", {}
                    ),
                    "stored_value_contract": stored_contract_from_artifacts(solution_file, metadata, original_error),
                    "change_telemetry": metadata.get("change_telemetry", {}),

                    "quality_score": metadata.get(
                        'code_quality_metrics',
                        {}
                    ).get('quality_score', 0),

                    "analysis_timestamp": metadata.get(
                        'analysis_timestamp'
                    ),

                    "llm_model": metadata.get('llm_model'),
                    "llm_provider": metadata.get('llm_provider'),
                    "repository_guidance": metadata.get(
                        "repository_guidance", []
                    ),
                    "branch_policy": metadata.get("branch_policy", {}),
                    "compatibility_policy": metadata.get(
                        "compatibility_policy", {}
                    ),
                    "digest_contract_assessment": metadata.get(
                        "digest_contract_assessment", {}
                    ),
                    "migration_assessment": metadata.get(
                        "migration_assessment", {}
                    ),
                    "publication_considerations": metadata.get(
                        "publication_considerations", []
                    ),
                    "publication_blocker_record": metadata.get(
                        "publication_blocker_record", {}
                    ),
                    "review_assumptions": metadata.get(
                        "review_assumptions", []
                    ),
                    "review_blockers": metadata.get("review_blockers", []),
                    "required_tests": metadata.get("required_tests", []),
                    "unique_error_id": original_error.get(
                        "unique_error_id", ""
                    ),
                    # The findings the pull request is attributed to when the
                    # behaviour review found the reported one without effect
                    # (coordinated_generation.pr_attribution; LocalSend run_v4,
                    # 2026-10-31): the worktree branch and the PR step name them.
                    "pr_attribution": (
                        (data.get("evidence_analysis") or {}).get("behavior_review") or {}
                    ).get("pr_attribution") or {},

                    "solution_file": str(solution_file)
                }

                self.solutions.append(solution)

            except json.JSONDecodeError as e:
                self.logger.error(
                    f"Invalid JSON in {solution_file.name}: {e}"
                )

            except Exception as e:
                self.logger.error(
                    f"Error processing {solution_file.name}: {e}"
                )

        self.logger.info(
            f"Discovered {len(self.solutions)} solutions"
        )

        return self.solutions

    def locate_source_file(
        self,
        relative_method_file: str
    ) -> Optional[Path]:
        """
        Locate source file directly from relative_method_file.
        """

        try:
            relative_path = Path(relative_method_file)

            cloned_path = self.cloned_projects_dir / relative_path
            if cloned_path.exists():
                return cloned_path.resolve()

            if relative_path.exists():
                return relative_path.resolve()

            # fallback
            full_path = (
                Path.cwd() / relative_path
            )

            if full_path.exists():
                return full_path.resolve()

            parts = relative_path.parts

            if len(parts) > 1:

                remapped_path = (
                    self.cloned_projects_dir
                    / Path(*parts[1:])
                )

                if remapped_path.exists():
                    return remapped_path.resolve()

            self.logger.warning(
                f"Source file not found: {relative_method_file}"
            )

            return None

        except Exception as e:
            self.logger.error(
                f"Error locating source file: {e}"
            )
            return None

    def build_mapping(self) -> Dict:
        """Build complete mapping of solutions to repositories."""

        self.logger.info(
            "Building solution to repository mapping..."
        )

        if not self.solutions:
            self.discover_solutions()

        self.mapping = {}

        for solution in self.solutions:

            apk_name = solution['apk_name']

            if apk_name not in self.mapping:
                self.mapping[apk_name] = {
                    "apk_name": apk_name,
                    "solutions": []
                }

            source_file = self.locate_source_file(
                solution['relative_method_file']
            )

            solution_entry = {
                "method_name": solution['method_name'],
                "csv_class": solution['csv_class'],
                "spec": solution.get("spec", ""),
                "error_message": solution.get("error_message", ""),
                "tool": solution.get("tool", ""),
                "detector_context": solution.get("detector_context") or {},

                "relative_method_file": solution[
                    'relative_method_file'
                ],

                "source_file": solution['source_file_path']
                if source_file else None,

                "quality_score": solution['quality_score'],
                "decision": solution.get("decision", ""),
                "manual_review_required": solution.get(
                    "manual_review_required", False
                ),
                "auto_apply": solution.get("auto_apply", True),
                "publication_allowed": solution.get(
                    "publication_allowed", True
                ),
                "publication_status": solution.get("publication_status", ""),
            }

            self.mapping[apk_name]["solutions"].append(
                solution_entry
            )

        self.logger.info(
            "Mapping complete"
        )

        return self.mapping

    def generate_mapping_report(self) -> Dict:
        """Generate mapping report."""

        if not self.mapping:
            self.build_mapping()

        report = {
            "total_apks": len(self.mapping),

            "total_solutions": sum(
                len(m["solutions"])
                for m in self.mapping.values()
            ),

            "mappings": self.mapping
        }

        return report

    def save_mapping_report(
        self,
        output_path: str
    ) -> None:
        """Save mapping report to file."""

        report = self.generate_mapping_report()

        with open(
            output_path,
            'w',
            encoding='utf-8'
        ) as f:

            json.dump(report, f, indent=2)

        self.logger.info(
            f"Mapping report saved to {output_path}"
        )
