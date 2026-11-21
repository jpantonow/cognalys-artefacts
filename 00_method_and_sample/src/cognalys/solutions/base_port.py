#!/usr/bin/env python3
"""Carry a reviewed patch onto a base branch whose code has moved on.

The analysis reads the revision the analysed APK was built from; a pull
request targets the tip of the base branch. When code near or inside an edit
changed in between, the edit's original code is not on the base and the
three-way merge in solution_applicator conflicts. Until now that ended the
finding: run_20261010_200449 recorded nextcloudcookbook's trust-all
TrustManager and DSub2000's as ORIGINAL_CODE_ABSENT_FROM_BASE_BRANCH although
both were still on the base, only refactored and reformatted, and BitBanana's
and WifiAnalyzer's patches failed on a single block in a caller and a test
that upstream had rewritten.

This is a porting step, not a second analysis. The model receives the patch
as written, what upstream changed in each file it touches, and those files
as they are on the base, and either re-anchors the same correction there or
says the violation is gone or that it cannot port. Nothing it returns is
trusted: the edits are applied by the applicator's own checks and the
semantic gates, then built and replayed through the detector like any other
patch.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from cognalys.llm.pipeline_common import prompt_json

PORT_DECISIONS = ("PORT", "ALREADY_FIXED_UPSTREAM", "ABSTAIN")
# One whole base file is enough to anchor an edit; the diff says what moved.
MAX_FILE_CHARACTERS = 120_000
MAX_DIFF_CHARACTERS = 40_000
PORT_EDIT_KINDS = ("method", "declaration", "constant", "delete", "test", "create")


def port_schema() -> dict:
    edit = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "source_file_path": {"type": "string"},
            "symbol": {"type": "string"},
            "edit_kind": {"type": "string", "enum": list(PORT_EDIT_KINDS)},
            "original_code": {"type": "string"},
            "corrected_code": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["source_file_path", "symbol", "edit_kind",
                     "original_code", "corrected_code", "reason"],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "decision": {"type": "string", "enum": list(PORT_DECISIONS)},
            "explanation": {"type": "string"},
            "edits": {"type": "array", "items": edit, "maxItems": 64},
        },
        "required": ["decision", "explanation", "edits"],
    }


def _git_text(repo: Path, *arguments: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments], capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace")


def port_payload(solution: dict, base_repo: Path, worktree_root: Path,
                 base_ref: str, analysed_ref: str = "HEAD") -> dict:
    """What the model needs to port the patch, and nothing it could act on.

    Paths are the solution's own, after any relocation; each file's upstream
    diff is read under the path it had at the analysed revision when git
    knows it was renamed.
    """
    edits = [edit for edit in solution.get("coordinated_edits") or []
             if isinstance(edit, dict)]
    if not edits:
        edits = [{
            "source_file_path": solution.get("source_file_path", ""),
            "symbol": solution.get("method_name", ""),
            "edit_kind": "method",
            "original_code": solution.get("original_method_body", ""),
            "corrected_code": solution.get("corrected_method_body", ""),
            "reason": solution.get("corrected_explanation", ""),
        }]
    files = []
    paths = list(dict.fromkeys(str(edit.get("source_file_path", ""))
                              for edit in edits if edit.get("edit_kind") != "create"))
    paths.extend(path for path in _stored_port_paths(solution) if path not in paths)
    for path in paths:
        target = worktree_root / path
        if not path or not target.is_file():
            continue
        content = target.read_text(encoding="utf-8", errors="replace")
        diff = _git_text(base_repo, "diff", "--find-renames", analysed_ref,
                         base_ref, "--", path) or ""
        files.append({
            "source_file_path": path,
            "upstream_diff_since_analysed_revision": diff[:MAX_DIFF_CHARACTERS],
            "content_on_base": content[:MAX_FILE_CHARACTERS],
            "content_truncated": len(content) > MAX_FILE_CHARACTERS,
        })
    return {
        "finding": {
            "spec": solution.get("spec", ""),
            "detector_message": solution.get("error_message", ""),
            "class": solution.get("csv_class", ""),
            "method": solution.get("method_name", ""),
            "correction": solution.get("corrected_explanation", ""),
        },
        "patch_as_reviewed": [
            {key: edit.get(key, "") for key in
             ("source_file_path", "symbol", "edit_kind", "original_code",
              "corrected_code", "reason")}
            for edit in edits
        ],
        "files_on_base": files,
        "affected_stored_value_paths_on_base": _stored_port_paths(solution),
        "stored_value_contract": solution.get("stored_value_contract", {}),
        "compatibility_policy": solution.get("compatibility_policy", {}),
        "constraints": [
            "Port the same security correction; do not redesign it, widen it, "
            "or fix anything else upstream changed.",
            "Edit only files in patch_as_reviewed or affected_stored_value_paths_on_base. "
            "The latter are verified consumers of the same stored entries on this base; "
            "a previously opaque reader may now verify credentials and need migration. A file the patch "
            "creates is returned as a create edit with empty original_code.",
            "Every original_code is one contiguous block copied verbatim from "
            "content_on_base, including indentation and line breaks, and "
            "occurs in that file exactly once.",
            "Return the whole ported patch, every edit, not only the ones "
            "that stopped applying.",
            "Preserve every affected stored-value reader and writer on the base. "
            "Do not drop a verifier migration from a caller because upstream "
            "changed its surrounding lockout/delay logic; keep both behaviours.",
            "Where upstream changed code the patch's tests exercise (a "
            "constructor, a helper), adapt the tests to the base's API.",
            "Answer ALREADY_FIXED_UPSTREAM only when content_on_base no "
            "longer contains the violation the detector reported, and say "
            "where the base removed it. Answer ABSTAIN when the correction "
            "cannot be carried over without a new design decision.",
        ],
    }


def _stored_port_paths(solution: dict) -> list[str]:
    contract = solution.get("stored_value_contract") or {}
    return sorted(contract.get("required_paths") or []) if contract.get("status") == "complete" else []


def validate_port(generated: dict, solution: dict) -> str:
    """Why a PORT answer cannot be applied, or "" when its shape is sound.

    The applicator still checks every original_code against the files; this
    refuses only what no file check would catch.
    """
    if generated.get("decision") != "PORT":
        return ""
    edits = generated.get("edits") or []
    if not edits:
        return "PORT_WITHOUT_EDITS"
    reviewed = {str(edit.get("source_file_path", ""))
                for edit in solution.get("coordinated_edits") or []
                if isinstance(edit, dict)} or {
        str(solution.get("source_file_path", ""))}
    reviewed.update(_stored_port_paths(solution))
    for edit in edits:
        path = str(edit.get("source_file_path", ""))
        if path not in reviewed:
            return f"PORT_EDIT_OUTSIDE_REVIEWED_FILES:{path}"
        if edit.get("edit_kind") != "create" and not str(
                edit.get("original_code", "")).strip():
            return f"PORT_EMPTY_ORIGINAL:{path}"
    return ""


def ported_solution(solution: dict, generated: dict) -> dict:
    """The solution with its edits replaced by the ported ones.

    Fields describing the edits' provenance are recomputed by the applicator;
    the hashes of the reviewed blocks no longer describe these blocks.
    """
    ported = dict(solution)
    edits = []
    for edit in generated.get("edits") or []:
        edits.append({
            "source_file_path": edit["source_file_path"],
            "symbol": edit.get("symbol", ""),
            "edit_kind": edit.get("edit_kind", "method"),
            "original_code": edit.get("original_code", ""),
            "corrected_code": edit.get("corrected_code", ""),
            "reason": edit.get("reason", ""),
        })
    if solution.get("coordinated_edits"):
        ported["coordinated_edits"] = edits
    else:
        ported["original_method_body"] = edits[0]["original_code"]
        ported["corrected_method_body"] = edits[0]["corrected_code"]
    return ported


def port_to_base(provider, solution: dict, base_repo: Path,
                 worktree_root: Path, base_ref: str,
                 apply: Callable[[dict], tuple[bool, list, str]],
                 semantic_check: Callable[[dict], list[dict]] | None = None,
                 max_corrections: int = 1) -> dict:
    """Ask for the patch on the base and apply it; the outcome, as a record.

    ``apply`` takes a ported solution and returns the applicator's
    (success, changes, error); ``semantic_check`` returns the generation-time
    gates' objections to a ported solution. A rejected port goes back once
    with the exact reason, as validation_fix does with a rejected repair.
    """
    stored = solution.get("stored_value_contract") or {}
    if stored.get("entries"):
        from cognalys.extraction.repository_index import build_repository_index
        from cognalys.extraction.stored_value_contract import collect_stored_value_contract
        index = build_repository_index(worktree_root, refresh=True)
        fresh = collect_stored_value_contract(worktree_root, {"persisted": True},
                                            index=index, seed_entries=stored["entries"])
        if fresh.get("status") != "complete":
            return {"decision": "ABSTAIN", "error": "STORED_VALUE_SCOPE_INCOMPLETE",
                    "explanation": str(fresh.get("limitations", [])), "attempts": []}
        solution = {**solution, "stored_value_contract": fresh}
    payload = port_payload(solution, base_repo, worktree_root, base_ref)
    schema = port_schema()
    request = (
        "Port this reviewed security patch onto the base branch. Return JSON "
        "matching the schema.\n\n"
        + prompt_json(payload)
        + "\n\nSCHEMA:\n" + prompt_json(schema)
    )
    system = (
        "You are a patch-porting stage. The finding was already analysed and "
        "the correction already reviewed; carry it onto the newer code with "
        "exact, minimal replacements."
    )
    trace_context = {
        "finding_id": str(solution.get("finding_id", "")),
        "prompt_version": "base-port-v3",
        "schema_version": "1.0", "policy_version": "1.0",
    }
    attempts = []
    prompt = request
    for attempt in range(max_corrections + 1):
        response = provider.generate(
            prompt, system, json_mode=True, json_schema=schema,
            schema_name="base_branch_port",
            trace_stage="solutions.base_port" + (".correction" if attempt else ""),
            trace_context=trace_context,
        )
        generated = _parse_json(response)
        decision = generated.get("decision", "")
        if decision not in PORT_DECISIONS:
            error = "PORT_UNPARSEABLE_RESPONSE"
        elif decision != "PORT":
            return {"decision": decision,
                    "explanation": str(generated.get("explanation", "")),
                    "attempts": attempts}
        else:
            error = validate_port(generated, solution)
        ported = ported_solution(solution, generated) if not error else None
        if ported is not None and semantic_check is not None:
            objections = semantic_check(ported)
            if objections:
                error = "PORT_VIOLATES_SEMANTIC_GATES:" + "; ".join(
                    f"{item.get('code', '')}: "
                    + " | ".join(str(d) for d in item.get("details", []))[:300]
                    for item in objections[:4]
                )
        if not error:
            success, changes, error = apply(ported)
            if success:
                return {"decision": "PORT", "applied": True,
                        "explanation": str(generated.get("explanation", "")),
                        "solution": ported, "changes": changes,
                        "attempts": attempts}
        attempts.append({"generated": generated, "error": error})
        prompt = (
            request
            + "\n\nYOUR PREVIOUS PORT WAS REJECTED BEFORE ANY FILE CHANGED:\n"
            + json.dumps({"rejected_port": generated, "error": error},
                         indent=2, ensure_ascii=False)
            + "\n\nReturn a corrected port that resolves the error. Every "
            "original_code must be copied verbatim from content_on_base and "
            "occur exactly once in its file."
        )
    return {"decision": "PORT", "applied": False,
            "error": attempts[-1]["error"] if attempts else "",
            "attempts": attempts}


def _parse_json(response: str) -> dict:
    from cognalys.llm.generate_error_fixes import ResponseParser  # noqa: E402
    return ResponseParser.parse_json_object(response) or {}
