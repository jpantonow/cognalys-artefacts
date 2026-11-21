#!/usr/bin/env python3
"""
Applying a coordinated edit set to source text.

This is the single place that decides what it means to apply one generated
edit: how original_code is located, when a match is ambiguous, how a create or
delete is expressed, and how required imports are merged.

It exists because two callers had grown separate answers to those questions.
scripts/fix_prs.py validated edit shape, demanded a unique match and tolerated
indentation drift; src/solutions/solution_applicator.py checked a content hash
and rejected duplicate blocks but did neither of the first three. A patch that
applied through one path could fail through the other, and a rule fixed in one
copy stayed broken in the other.

Everything here is pure text: no filesystem, no repository, no policy. The
environment-specific concerns each caller genuinely needs -- path containment
and rollback for the worktree applier, PR checkout state for the revision
flow -- stay with those callers.
"""
from __future__ import annotations

import re
from typing import Any


from cognalys.llm.semantic_validation import (  # noqa: E402
    normalize_coordinated_edit,
    validate_coordinated_edit_shape,
)
from cognalys.utils.edit_text import match_trailing_line_break, remove_block  # noqa: E402

EDIT_KINDS = (
    "method", "declaration", "constant", "test", "create", "delete",
    "delete_file",
)


def _same_file_content(text: str, original: str, tolerate_indentation: bool) -> bool:
    """Whether original_code reproduces the whole of a file's current content.

    delete_file removes a file outright, so the model has to show which file it
    means by copying all of it; a block that is only part of the file must stay
    an ordinary delete. Blank lines and line endings never distinguish two
    files, and leading indentation is ignored only where the caller already
    tolerates indentation drift.
    """
    def lines(value: str) -> list[str]:
        return [
            (line.strip() if tolerate_indentation else line.rstrip())
            for line in value.splitlines() if line.strip()
        ]
    return bool(lines(original)) and lines(text) == lines(original)


def apply_required_imports(source_text: str, imports: Any) -> tuple[str, list[str]]:
    if not isinstance(imports, list):
        return source_text, []
    requested = []
    for value in imports:
        name = str(value).strip().removeprefix("import ").rstrip(";")
        if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", name):
            requested.append(name)
    existing = set(re.findall(
        r"(?m)^import[ \t]+([\w.]+)[ \t]*;?[ \t]*$", source_text,
    ))
    added = sorted(set(requested) - existing)
    if not added:
        return source_text, []
    lines = source_text.splitlines()
    import_indexes = [i for i, line in enumerate(lines) if line.startswith("import ")]
    if not import_indexes:
        return source_text, []
    terminator = ";" if any(
        re.match(r"^import[ \t]+[\w.]+[ \t]*;[ \t]*$", lines[i])
        for i in import_indexes
    ) else ""
    for name in added:
        standard_index = next(
            (
                i for i in import_indexes
                if lines[i].startswith(("import java.", "import javax.", "import kotlin."))
            ),
            import_indexes[-1] + 1,
        )
        if name.startswith(("java.", "javax.", "kotlin.")):
            insert_at = import_indexes[-1] + 1
        else:
            insert_at = standard_index
        lines.insert(insert_at, f"import {name}{terminator}")
        import_indexes = [
            i for i, line in enumerate(lines) if line.startswith("import ")
        ]
    newline = "\r\n" if "\r\n" in source_text else "\n"
    suffix = newline if source_text.endswith(("\n", "\r\n")) else ""
    return newline.join(lines) + suffix, added


def _source_line_spans(text: str) -> list[tuple[int, int, int, str]]:
    """Return start/content-end/full-end/content for each physical line."""
    spans: list[tuple[int, int, int, str]] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        if raw_line.endswith("\r\n"):
            content = raw_line[:-2]
        elif raw_line.endswith(("\n", "\r")):
            content = raw_line[:-1]
        else:
            content = raw_line
        content_end = offset + len(content)
        full_end = offset + len(raw_line)
        spans.append((offset, content_end, full_end, content))
        offset = full_end
    return spans


def _leading_indentation_matches(
    text: str, original: str,
) -> list[tuple[int, int, list[tuple[int, int, int, str]]]]:
    """Find whole-line blocks differing only in leading spaces or tabs.

    This deliberately does not normalize internal/trailing whitespace or
    punctuation. It is a narrow fallback for an LLM copying the right lines
    with the wrong absolute indentation, not a fuzzy source-code matcher.
    """
    if not original or original.endswith(("\n", "\r")):
        return []
    original_lines = original.splitlines()
    if not original_lines:
        return []
    normalized = [line.lstrip(" \t") for line in original_lines]
    source_lines = _source_line_spans(text)
    matches: list[tuple[int, int, list[tuple[int, int, int, str]]]] = []
    window_size = len(original_lines)
    for index in range(len(source_lines) - window_size + 1):
        window = source_lines[index:index + window_size]
        if all(
            source_line[3].lstrip(" \t") == expected
            for source_line, expected in zip(window, normalized)
        ):
            matches.append((window[0][0], window[-1][1], window))
    return matches


def _constant_indentation_shift(
    original_lines: list[str],
    matched_lines: list[tuple[int, int, int, str]],
) -> tuple[str, bool] | None:
    """Find one indentation shift that explains every line of a matched block.

    The per-line rebasing below only works when corrected_code keeps the
    same line count as original_code, because it walks the two in lockstep.
    An LLM that both reindents *and* restructures a block (e.g. extracting a
    helper method) produces a corrected_code with a different line count, so
    that per-line walk cannot apply -- but the reindenting itself is often
    still just one constant amount of whitespace the model dropped or added
    uniformly on every original_code line (its literal tab/space rendering
    drifting from this file's own mixed-indentation convention). When that
    one shift explains every line of the matched block, the same shift can
    be applied to every line of corrected_code independently of how many
    lines it has.

    Returns (prefix, add) where add=True means every actual line's leading
    whitespace equals `prefix` followed by that line's original_code
    indentation, and add=False means `prefix` must instead be stripped from
    the original indentation to reach the actual one. None when no single
    shift accounts for every non-blank line.
    """
    prefix: str | None = None
    add: bool | None = None
    for original_line, matched in zip(original_lines, matched_lines):
        if not original_line.strip(" \t"):
            continue
        actual_line = matched[3]
        actual_indent = actual_line[:len(actual_line) - len(actual_line.lstrip(" \t"))]
        original_indent = original_line[
            :len(original_line) - len(original_line.lstrip(" \t"))
        ]
        if actual_indent.endswith(original_indent):
            candidate = actual_indent[:len(actual_indent) - len(original_indent)]
            candidate_add = True
        elif original_indent.endswith(actual_indent):
            candidate = original_indent[:len(original_indent) - len(actual_indent)]
            candidate_add = False
        else:
            return None
        if prefix is None:
            prefix, add = candidate, candidate_add
        elif (prefix, add) != (candidate, candidate_add):
            return None
    if not prefix:
        return None
    return prefix, bool(add)


def _rebase_with_unambiguous_indentation_levels(
    original_lines: list[str],
    corrected_lines: list[str],
    matched_lines: list[tuple[int, int, int, str]],
) -> list[str] | None:
    """Rebase a restructured block when only isolated indentation drifted.

    A model can copy one line of ``original_code`` with one tab missing while
    copying every other line exactly.  When ``corrected_code`` also adds or
    removes lines, a single constant shift cannot describe that drift even
    though the source match is otherwise exact and unique.

    Build a mapping from each indentation level used by ``original_code`` to
    the corresponding level in the matched source.  Applying that mapping is
    safe only when every level has exactly one source representation, every
    level used by ``corrected_code`` was observed in ``original_code``, and at
    least one non-blank line already had exact indentation.  The final guard
    prevents this fallback from guessing how to reindent a block whose entire
    whitespace convention differs from the source.
    """
    indentation_levels: dict[str, set[str]] = {}
    has_exact_indentation = False
    for original_line, matched in zip(original_lines, matched_lines):
        if not original_line.strip(" \t"):
            continue
        actual_line = matched[3]
        original_indent = original_line[
            :len(original_line) - len(original_line.lstrip(" \t"))
        ]
        actual_indent = actual_line[
            :len(actual_line) - len(actual_line.lstrip(" \t"))
        ]
        indentation_levels.setdefault(original_indent, set()).add(actual_indent)
        has_exact_indentation = has_exact_indentation or original_indent == actual_indent

    if not has_exact_indentation or any(
        len(actual_levels) != 1
        for actual_levels in indentation_levels.values()
    ):
        return None

    rebased: list[str] = []
    for line in corrected_lines:
        if not line.strip(" \t"):
            rebased.append("")
            continue
        corrected_indent = line[:len(line) - len(line.lstrip(" \t"))]
        actual_levels = indentation_levels.get(corrected_indent)
        if actual_levels is None:
            return None
        actual_indent = next(iter(actual_levels))
        rebased.append(actual_indent + line[len(corrected_indent):])
    return rebased


def _replacement_with_source_indentation(
    original: str,
    corrected: str,
    matched_lines: list[tuple[int, int, int, str]],
    source_text: str,
) -> tuple[str | None, str | None]:
    """Rebase corrected leading indentation onto the uniquely matched block."""
    if corrected == "":
        return "", None
    if corrected.endswith(("\n", "\r")):
        return None, "indentation fallback cannot safely add a trailing newline"
    original_lines = original.splitlines()
    corrected_lines = corrected.splitlines()
    if len(corrected_lines) != len(original_lines):
        shift = _constant_indentation_shift(original_lines, matched_lines)
        if shift is None:
            shifted = _rebase_with_unambiguous_indentation_levels(
                original_lines, corrected_lines, matched_lines,
            )
            if shifted is None:
                return None, (
                    "indentation fallback requires original_code and "
                    "corrected_code to have the same number of lines, a "
                    "single indentation shift, or an unambiguous mapping "
                    "for every indentation level in corrected_code"
                )
        else:
            prefix, add = shift
            shifted = []
            for line in corrected_lines:
                if not line.strip(" \t"):
                    shifted.append("")
                    continue
                if add:
                    shifted.append(prefix + line)
                    continue
                indent = line[:len(line) - len(line.lstrip(" \t"))]
                if not indent.endswith(prefix):
                    return None, (
                        "indentation fallback could not safely rebase "
                        "corrected_code"
                    )
                shifted.append(indent[:-len(prefix)] + line[len(indent):])
        terminator = source_text[matched_lines[0][1]:matched_lines[0][2]] or "\n"
        return terminator.join(shifted), None

    rebased: list[str] = []
    for original_line, corrected_line, matched_line in zip(
        original_lines, corrected_lines, matched_lines,
    ):
        actual_line = matched_line[3]
        actual_indent = actual_line[:len(actual_line) - len(actual_line.lstrip(" \t"))]
        original_indent = original_line[:
            len(original_line) - len(original_line.lstrip(" \t"))
        ]
        corrected_indent = corrected_line[:
            len(corrected_line) - len(corrected_line.lstrip(" \t"))
        ]
        if not corrected_line.strip(" \t"):
            rebased.append("")
            continue
        if corrected_indent == original_indent:
            new_indent = actual_indent
        elif corrected_indent.startswith(original_indent):
            new_indent = actual_indent + corrected_indent[len(original_indent):]
        elif original_indent.startswith(corrected_indent):
            removed_indent = original_indent[len(corrected_indent):]
            if not removed_indent or not actual_indent.endswith(removed_indent):
                return None, (
                    "indentation fallback could not safely rebase corrected_code"
                )
            new_indent = actual_indent[:-len(removed_indent)]
        else:
            return None, "indentation fallback found an incompatible indentation change"
        rebased.append(new_indent + corrected_line[len(corrected_indent):])

    replacement_parts: list[str] = []
    for index, line in enumerate(rebased):
        replacement_parts.append(line)
        if index < len(rebased) - 1:
            content_end = matched_lines[index][1]
            full_end = matched_lines[index][2]
            replacement_parts.append(source_text[content_end:full_end])
    return "".join(replacement_parts), None


def apply_edits_to_sources(
    sources: dict[str, str],
    edits: list[dict],
    *,
    tolerate_indentation_drift: bool = False,
) -> tuple[dict[str, str] | None, list[str]]:
    """Apply a coordinated edit set to in-memory source text, all or nothing.

    Every edit must pass validate_coordinated_edit_shape and locate its
    original_code exactly once; a partial, silently-incomplete patch is worse
    than abstaining. edit_kind=create is the one edit allowed to name a path
    absent from `sources`, because the file does not exist yet.

    `tolerate_indentation_drift` is deliberately a caller decision rather than
    a fixed rule. The revision flow applies to a pull-request branch whose code
    may have moved since the evidence was gathered, so a whole-line match
    differing only in leading whitespace is still the intended block. The
    worktree applier writes against the exact base commit the model was shown,
    where the same tolerance would only mask a mismatch worth failing on.

    edit_kind=delete_file removes a whole supplied file, which is how a
    reviewer's "remove this class and its usages" is expressed; its path is
    simply absent from updated_sources, so a caller detects the deletion by
    comparing keys with `sources`.

    Returns (updated_sources, issues); updated_sources is None when issues is
    non-empty.
    """
    issues: list[str] = []
    updated = dict(sources)
    deleted: set[str] = set()
    edited_paths: set[str] = set()
    imports_by_file: dict[str, list[str]] = {}
    seen_blocks: set[tuple[str, str]] = set()

    for raw_edit in edits:
        if not isinstance(raw_edit, dict):
            issues.append("edit is not an object")
            continue
        edit = normalize_coordinated_edit(raw_edit)
        path = str(edit.get("source_file_path", "") or "?")
        kind = str(edit.get("edit_kind", ""))
        if kind not in EDIT_KINDS:
            issues.append(f"{path}: unsupported edit_kind {kind!r}")
            continue
        shape_issues = validate_coordinated_edit_shape(edit)
        if shape_issues:
            issues.extend(f"{path}: {issue}" for issue in shape_issues)
            continue

        original = str(edit.get("original_code", ""))
        corrected = str(edit.get("corrected_code", ""))
        required = [
            str(item) for item in edit.get("required_imports", []) or []
            if str(item).strip()
        ]

        if kind == "create":
            if path in updated:
                issues.append(
                    f"{path}: create targets a file that already exists; "
                    "edit it instead"
                )
                continue
            updated[path] = corrected
            if required:
                imports_by_file.setdefault(path, []).extend(required)
            continue

        if path not in updated:
            issues.append(f"{path}: not one of the supplied sources")
            continue

        if kind == "delete_file":
            if path in deleted:
                issues.append(f"{path}: duplicate delete_file edit")
                continue
            if not _same_file_content(
                updated[path], original, tolerate_indentation_drift,
            ):
                issues.append(
                    f"{path}: delete_file original_code must be the file's "
                    "entire current content; remove part of a file with "
                    "edit_kind=delete instead"
                )
                continue
            deleted.add(path)
            continue

        edited_paths.add(path)

        # Two edits replacing the same block would silently drop one of them.
        block = (path, original)
        if block in seen_blocks:
            issues.append(f"{path}: duplicate edit for the same original_code")
            continue
        seen_blocks.add(block)

        text = updated[path]
        exact = text.count(original)
        if exact == 1:
            # A deletion removes its lines, not just their text: the same
            # helper the solution applicator uses (remove_block).
            updated[path] = (text.replace(
                original, match_trailing_line_break(original, corrected), 1,
            ) if corrected else remove_block(text, original))
        elif exact > 1:
            issues.append(
                f"{path}: original_code matched {exact} times exactly; "
                "expected exactly one match"
            )
            continue
        elif not tolerate_indentation_drift:
            issues.append(f"{path}: original_code was not found")
            continue
        else:
            matches = _leading_indentation_matches(text, original)
            if not matches:
                issues.append(
                    f"{path}: original_code was not found exactly or by "
                    "leading-indentation-only matching"
                )
                continue
            if len(matches) > 1:
                issues.append(
                    f"{path}: original_code had no exact match and matched "
                    f"{len(matches)} blocks after ignoring leading "
                    "indentation; expected exactly one match"
                )
                continue
            start, end, matched_lines = matches[0]
            replacement, problem = _replacement_with_source_indentation(
                original, corrected, matched_lines, text,
            )
            if problem:
                issues.append(f"{path}: {problem}")
                continue
            updated[path] = text[:start] + str(replacement) + text[end:]

        if required:
            imports_by_file.setdefault(path, []).extend(required)

    issues.extend(
        f"{path}: edit targets a file this revision deletes"
        for path in sorted(deleted & edited_paths)
    )
    if issues:
        return None, issues
    for path, imports in imports_by_file.items():
        updated[path], _ = apply_required_imports(updated[path], imports)
    for path in deleted:
        updated.pop(path, None)
    return updated, []

