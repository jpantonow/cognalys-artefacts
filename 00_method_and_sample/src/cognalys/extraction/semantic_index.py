"""Tree-sitter-backed Java/Kotlin symbol and call resolution."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


DECLARATION_TYPES = {
    "method_declaration", "constructor_declaration", "function_declaration",
    "secondary_constructor",
}
CLASS_TYPES = {
    "class_declaration", "interface_declaration", "enum_declaration",
    "object_declaration", "annotation_type_declaration",
}
CALL_TYPES = {"method_invocation", "call_expression"}
PARAMETER_TYPES = {
    "formal_parameter", "spread_parameter", "receiver_parameter",
    "function_value_parameter", "parameter", "class_parameter",
}
GENERIC_METHOD_NAMES = {
    "get", "set", "put", "add", "read", "write", "load", "save",
    "create", "build", "update", "run", "execute", "value", "result",
}
_PARSE_CACHE: dict[tuple, dict] = {}
# Words that open a statement and are never a type or a modifier. A line
# `return name(alias)` calls name; read as `<type> name(` it looked like a
# Java declaration, and Kotlin's missing semicolon let it pass (BlueGate's
# rename check counted no reference to generateNewKeyMaterial, 2026-10-29).
# Shared by the lexical readers that tell a declaration from a statement.
STATEMENT_KEYWORDS = frozenset({
    "return", "throw", "new", "else", "case", "yield", "await", "assert", "do",
    "in", "is", "as",
})


def kotlin_function_declaration(name: str) -> str:
    """The pattern of a Kotlin `fun` declaring ``name``, extension receivers
    included (`fun Context.md5Hash(`, `fun <T> List<T>?.name(`)."""
    return rf"\bfun\s+(?:<[^>]+>\s*)?(?:[\w$.<>?, ]+\.)?{re.escape(name)}\s*\("


# A Kotlin function declared at the top of its file, outside any class and
# not private: the compiler puts it in the file's `<Stem>Kt` facade and other
# files reach it through its package.
_TOP_LEVEL_KOTLIN_FUNCTION = re.compile(
    r"^(?:@[\w.]+(?:\([^)]*\))?\s+)*"
    r"(?:(?:public|internal|inline|suspend|infix|operator|tailrec|external)\s+)*fun\b"
)
_PACKAGE = re.compile(r"(?m)^\s*package\s+([\w.]+)")


def _lexical_qualified_fallback(
    repo_root: Path,
    source_cache: dict[Path, str],
    target_file: Path,
    target_method: str,
    target_arity: int | None,
    parser_error: str,
) -> dict:
    """Resolve conservative, explicitly qualified calls without native parsers.

    This fallback intentionally does not treat every same-name call as the
    target.  It confirms calls qualified by the target source class, calls in
    the declaring file, and unqualified calls backed by an explicit static or
    member import.  Remaining same-name calls are retained as ambiguities.

    A top-level Kotlin function is imported by its package, not its file
    (`import com.philkes.notallyx.utils.md5Hash`), and called unqualified in
    its own package. Those calls resolve only while no other declaration of
    the name exists in the repository: NotallyX (2026-10-28) left both real
    callers of its md5Hash extensions ambiguous, and counted the three
    `fun Context.md5Hash(` declarations as their own callers.
    """
    target_relative = target_file.relative_to(repo_root).as_posix()
    target_owner = target_file.stem
    owners = [target_owner]
    if target_file.suffix.lower() == ".kt":
        owners.append(f"{target_owner}Kt")
    owner_alternatives = "|".join(re.escape(owner) for owner in owners)
    method_token = re.compile(rf"\b{re.escape(target_method)}\s*\(")
    qualified_token = re.compile(
        rf"\b(?:{owner_alternatives})\s*\.\s*"
        rf"{re.escape(target_method)}\s*\("
    )
    # A Kotlin call line has no semicolon: `return md5Hash(uri)` is not a
    # declaration of a method returning `return`.
    declaration_token = re.compile(
        rf"(?:{kotlin_function_declaration(target_method)}|"
        rf"\b(?!(?:return|throw|else|new|in|is|as|do|try|case|yield|await)\b)"
        rf"(?:public|private|protected|internal|static|final|abstract|"
        rf"synchronized|native|open|override|suspend|inline|\w+[<>,.?\[\]]*)"
        rf"(?:\s+\w+[<>,.?\[\]]*)*\s+{re.escape(target_method)}\s*\()"
    )
    import_token = re.compile(
        rf"(?m)^\s*import\s+(?:static\s+)?[\w.]*"
        rf"\.(?:{owner_alternatives})\.{re.escape(target_method)}\s*;?\s*$"
    )
    unqualified_token = re.compile(rf"(?<![\w$.:]){re.escape(target_method)}\s*\(")
    resolved: list[dict] = []
    ambiguous: list[dict] = []
    declarations: list[dict] = []

    top_level: set[tuple[str, int]] = set()
    scanned: list[tuple[str, str, list[str]]] = []
    for path, source in sorted(source_cache.items(), key=lambda item: item[0].as_posix()):
        if path.suffix.lower() not in {".java", ".kt", ".kts"}:
            continue
        relative = path.relative_to(repo_root).as_posix()
        lines = source.splitlines()
        scanned.append((relative, source, lines))
        for index, line in enumerate(lines):
            if method_token.search(line) and declaration_token.search(line):
                declarations.append({
                    "symbol_id": f"{target_owner}#{target_method}",
                    "name": target_method,
                    "arity": target_arity,
                    "owner": target_owner if relative == target_relative else "<unknown>",
                    "path": relative,
                    "line": index + 1,
                    "override": bool(re.search(r"\b(?:override|@Override)\b", line)),
                    "resolution": "lexical_declaration",
                })
                if path.suffix.lower() == ".kt" and _TOP_LEVEL_KOTLIN_FUNCTION.match(line):
                    top_level.add((relative, index + 1))
    declared_lines = {(item["path"], item["line"]) for item in declarations}

    target_source = next(
        (source for relative, source, _ in scanned if relative == target_relative), "",
    )
    package = _PACKAGE.search(target_source)
    in_target = [item for item in declarations if item["path"] == target_relative]
    package_scope = bool(
        package and in_target
        and all((item["path"], item["line"]) in top_level for item in in_target)
        and len(in_target) == len(declarations)
    )
    if package_scope:
        qualified_name = rf"{re.escape(package.group(1))}\.{re.escape(target_method)}"
        package_import = re.compile(rf"(?m)^\s*import\s+{qualified_name}\s*;?\s*$")
        wildcard_import = re.compile(
            rf"(?m)^\s*import\s+{re.escape(package.group(1))}\.\*\s*;?\s*$"
        )
        # The same name imported from somewhere else wins over the package.
        other_import = re.compile(
            rf"(?m)^\s*import\s+(?!{qualified_name}\b)[\w.]+\.{re.escape(target_method)}"
            rf"(?:\s+as\s+\w+)?\s*;?\s*$"
        )

    for relative, source, lines in scanned:
        explicit_import = bool(import_token.search(source))
        package_import_backed = same_package_scope = False
        # Java reaches a top-level function only through the `<Stem>Kt`
        # facade; its unqualified same-name call is its own class's method.
        kotlin_caller = relative.lower().endswith((".kt", ".kts"))
        if package_scope and kotlin_caller and not other_import.search(source):
            package_import_backed = bool(package_import.search(source))
            source_package = _PACKAGE.search(source)
            same_package_scope = bool(
                wildcard_import.search(source)
                or (source_package and source_package.group(1) == package.group(1))
            )
        for index, line in enumerate(lines):
            if not method_token.search(line) or (relative, index + 1) in declared_lines:
                continue
            record = {
                "name": target_method,
                "arity": None,
                "receiver": target_owner if qualified_token.search(line) else "",
                "path": relative,
                "line": index + 1,
                "enclosing_symbol": "<lexical>",
                "snippet": "\n".join(
                    f"{line_number + 1}: {lines[line_number]}"
                    for line_number in range(max(0, index - 3), min(len(lines), index + 4))
                ),
            }
            if (qualified_token.search(line)
                    or relative == target_relative
                    or explicit_import):
                record["resolution"] = "lexically_qualified_target"
                record["resolved_symbol_id"] = f"{target_owner}#{target_method}"
                resolved.append(record)
            elif package_import_backed or (
                    same_package_scope and unqualified_token.search(line)):
                record["resolution"] = "kotlin_package_scope_target"
                record["resolved_symbol_id"] = f"{target_owner}#{target_method}"
                resolved.append(record)
            else:
                record["resolution"] = "same_name_call_without_type_proof"
                record["candidate_symbol_ids"] = [f"{target_owner}#{target_method}"]
                ambiguous.append(record)

    target_declarations = [
        item for item in declarations if item["path"] == target_relative
    ]
    return {
        "status": "available" if target_declarations else "unresolved",
        "backend": "lexically_qualified_repository_fallback",
        "type_resolved": False,
        "resolution_strength": (
            "lexically_complete" if not ambiguous else "lexically_partial"
        ),
        "target_symbol": target_declarations[0] if target_declarations else {},
        "resolved_callers": resolved,
        "ambiguous_calls": ambiguous,
        "related_declarations": [
            item for item in declarations if item["path"] != target_relative
        ],
        "parse_error_files": [],
        "parser_fallback_reason": parser_error,
        "coverage": {
            "parsed_files": 0,
            "lexically_scanned_files": len(source_cache),
            "target_calls_resolved": len(resolved),
            "target_calls_ambiguous": len(ambiguous),
            "candidate_files_truncated": False,
        },
        "warning": (
            "Native parsing was unavailable. Calls are complete for the supplied "
            "repository index but only explicitly qualified/import-backed calls "
            "are considered resolved."
        ),
    }


def _parse_batch_worker(request_path: Path, response_path: Path) -> int:
    """Parse a bounded batch in a disposable process."""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    parsers, error = _load_parsers()
    if not parsers:
        response_path.write_text(json.dumps({
            "status": "unavailable", "reason": error,
        }), encoding="utf-8")
        return 2
    files = []
    for item in request.get("files", []):
        suffix = str(item.get("suffix", "")).lower()
        parser = parsers.get(suffix)
        if parser is None:
            continue
        files.append(_parse_file(
            Path(str(item.get("path", ""))),
            str(item.get("relative", "")),
            str(item.get("source", "")), parser,
        ))
    response_path.write_text(json.dumps({
        "status": "complete", "files": files,
    }, ensure_ascii=False), encoding="utf-8")
    return 0


def _parse_batch_once(items: list[tuple[Path, str]], repo_root: Path,
                      timeout: int = 180) -> tuple[list[dict] | None, str]:
    """Run one native-parser batch in a disposable process."""
    with tempfile.TemporaryDirectory(prefix="tcc-semantic-") as directory:
        temporary = Path(directory)
        request_path = temporary / "request.json"
        response_path = temporary / "response.json"
        request_path.write_text(json.dumps({"files": [{
            "path": str(path),
            "relative": path.relative_to(repo_root).as_posix(),
            "suffix": path.suffix.lower(),
            "source": source,
        } for path, source in items]}, ensure_ascii=False), encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()),
                 "--parse-batch", str(request_path), str(response_path)],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None, f"isolated tree-sitter parsing timed out after {timeout}s"
        if result.returncode != 0 or not response_path.is_file():
            signal = -result.returncode if result.returncode < 0 else None
            detail = f"signal {signal}" if signal else f"exit {result.returncode}"
            return None, f"isolated tree-sitter parser failed with {detail}"
        try:
            response = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"isolated parser returned invalid output: {exc}"
        if response.get("status") != "complete":
            return None, str(response.get("reason", "isolated parser unavailable"))
        return response.get("files", []), ""


def _parse_batch_isolated(items: list[tuple[Path, str]], repo_root: Path,
                          timeout: int = 180) -> tuple[list[dict] | None, str]:
    """Contain native crashes and recover every independently parseable file.

    A native grammar crash is frequently input-specific. Bisecting a failed
    batch prevents one malformed/generated source from discarding semantic
    resolution for the target and all healthy callers.
    """
    parsed, error = _parse_batch_once(items, repo_root, timeout)
    if parsed is not None or len(items) <= 1:
        return parsed, error
    midpoint = len(items) // 2
    left, left_error = _parse_batch_isolated(
        items[:midpoint], repo_root, timeout
    )
    right, right_error = _parse_batch_isolated(
        items[midpoint:], repo_root, timeout
    )
    recovered = (left or []) + (right or [])
    failures = [value for value in (left_error, right_error) if value]
    detail = "; ".join(dict.fromkeys(failures))
    if recovered:
        skipped = len(items) - len(recovered)
        warning = f"recovered partial semantic corpus; skipped {skipped} file(s)"
        if detail:
            warning += f": {detail}"
        return recovered, warning
    return None, detail or error


def resolve_policy_consumers(repo_root: Path, source_cache: dict[Path, str],
                             consumers: list[str]) -> dict:
    """Resolve policy class/member references to repository source files."""
    requested = [str(value).strip() for value in consumers if str(value).strip()]
    if not requested:
        return {"status": "not_requested", "backend": "none",
                "resolved": [], "unresolved": []}

    classes = []
    backend = "textual_package_class_fallback"
    parser_error = ""
    wanted_names = {part for value in requested for part in value.split(".")}
    parser_items = [
        (path, source) for path, source in source_cache.items()
        if path.suffix.lower() in {".java", ".kt", ".kts"}
        and len(source) <= 300_000
        and any(re.search(rf"\b{re.escape(name)}\b", source)
                for name in wanted_names)
    ]
    parser_items.sort(key=lambda item: item[0].as_posix())
    if parser_items:
        parsed, parser_error = _parse_batch_isolated(
            parser_items[:120], repo_root
        )
        if parsed is not None:
            backend = "tree_sitter_java_kotlin_isolated"
            classes.extend(
                declaration for file_data in parsed
                for declaration in file_data["classes"]
            )

    # Always build a lexical class index as a fallback and for field/property
    # confirmation, which the call-oriented semantic index does not enumerate.
    lexical_classes = []
    for path, source in source_cache.items():
        if path.suffix.lower() not in {".java", ".kt", ".kts"}:
            continue
        package_match = re.search(r"(?m)^\s*package\s+([\w.]+)", source)
        package = package_match.group(1) if package_match else ""
        for match in re.finditer(
                r"\b(?:class|interface|enum|object)\s+([A-Za-z_]\w*)", source):
            name = match.group(1)
            lexical_classes.append({
                "name": name,
                "qualified_name": f"{package}.{name}" if package else name,
                "path": path.relative_to(repo_root).as_posix(),
                "line": source.count("\n", 0, match.start()) + 1,
            })
    index = {item["qualified_name"]: item for item in lexical_classes}
    index.update({item["qualified_name"]: item for item in classes})

    resolved, unresolved = [], []
    for consumer in requested:
        class_ref = ""
        member = ""
        for candidate in sorted(index, key=len, reverse=True):
            if consumer == candidate or consumer.startswith(candidate + "."):
                class_ref = candidate
                member = consumer[len(candidate):].lstrip(".")
                break
        if not class_ref:
            unresolved.append({"consumer": consumer, "reason": "class_not_found"})
            continue
        declaration = index[class_ref]
        source = source_cache.get(repo_root / declaration["path"], "")
        if member and not re.search(rf"\b{re.escape(member)}\b", source):
            unresolved.append({
                "consumer": consumer, "reason": "member_not_found",
                "class_source_file_path": declaration["path"],
            })
            continue
        resolved.append({
            "consumer": consumer,
            "class": class_ref,
            "member": member or None,
            "source_file_path": declaration["path"],
            "resolution": backend,
        })
    return {
        "status": "complete" if not unresolved else "incomplete",
        "backend": backend,
        "parser_fallback_reason": parser_error,
        "resolved": resolved,
        "unresolved": unresolved,
    }


def _load_parsers() -> tuple[dict[str, object], str]:
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_java
        import tree_sitter_kotlin

        return {
            ".java": Parser(Language(tree_sitter_java.language())),
            ".kt": Parser(Language(tree_sitter_kotlin.language())),
            ".kts": Parser(Language(tree_sitter_kotlin.language())),
        }, ""
    except (ImportError, OSError, RuntimeError, TypeError) as exc:
        return {}, str(exc)


def _walk(node):
    """Pre-order traversal of an AST, iteratively.

    This was a recursive generator, and the `yield from` chain it built was
    the pipeline's single largest source of lost semantic resolution. Every
    nested node adds a generator frame that each `next()` resumes through in
    C, so with the helper calls in _parse_file's loop body on top of the
    chain the C stack overflowed and the interpreter took SIGSEGV -- not a
    RecursionError, which would have been caught and reported. The isolated
    parser process died, _parse_batch_isolated bisected the batch and dropped
    the file it blamed, and because batches are sorted target-first the file
    dropped was usually the target itself. Its declaration then went missing
    from the AST, build_semantic_index answered "unresolved", and everything
    downstream degraded: scope_completeness.resolved_callers became
    "unknown" (20 of 54 stored packets), callers fell back to lexical
    discovery, and adjudications blocked on caller and contract scope that
    the resolver had in fact been asked to establish. Iteration removes the
    C-stack bound entirely; the yielded order is unchanged.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _name(node, content: bytes) -> str:
    named = node.child_by_field_name("name")
    if named is not None:
        return _text(named, content)
    for child in node.children:
        if child.type in {"identifier", "simple_identifier", "type_identifier"}:
            return _text(child, content)
    return ""


def _ancestor(node, accepted: set[str]):
    current = node.parent
    while current is not None:
        if current.type in accepted:
            return current
        current = current.parent
    return None


def _parameter_data(node, content: bytes) -> tuple[int, list[str]]:
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        parameters = next(
            (child for child in node.children if "parameter" in child.type), None
        )
    if parameters is None:
        return 0, []
    items = [
        item for item in _walk(parameters)
        if item.type in PARAMETER_TYPES
        and not any(parent.type in PARAMETER_TYPES for parent in [item.parent] if parent)
    ]
    types = []
    for item in items:
        type_node = item.child_by_field_name("type")
        if type_node is not None:
            types.append(re.sub(r"\s+", "", _text(type_node, content)))
        else:
            raw = _text(item, content)
            kotlin = re.search(r":\s*([^=,]+)", raw)
            java = re.match(r"\s*([\w$.<>?\[\]]+)\s+\w+", raw)
            types.append(
                re.sub(r"\s+", "", kotlin.group(1)) if kotlin
                else java.group(1) if java else "?"
            )
    return len(items), types


def _argument_count(node) -> int:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        arguments = next(
            (child for child in node.children if child.type in {
                "argument_list", "call_suffix", "value_arguments"
            }), None
        )
    if arguments is None:
        return 0
    named = [child for child in arguments.named_children]
    if len(named) == 1 and named[0].type in {"value_arguments", "argument_list"}:
        named = list(named[0].named_children)
    return len([child for child in named if child.type not in {"type_arguments"}])


def _call_name(node, content: bytes) -> str:
    named = node.child_by_field_name("name")
    if named is not None:
        return _text(named, content)
    raw = _text(node, content)
    match = re.search(r"([A-Za-z_]\w*)\s*(?:<[^()]*>)?\s*\(", raw)
    return match.group(1) if match else ""


def _receiver(node, content: bytes, call_name: str) -> str:
    obj = node.child_by_field_name("object")
    if obj is not None:
        return _text(obj, content).strip()
    raw = _text(node, content)
    match = re.search(rf"([A-Za-z_]\w*)\s*\.\s*{re.escape(call_name)}\s*\(", raw)
    return match.group(1) if match else ""


def _receiver_type(receiver: str, enclosing, content: bytes) -> str:
    if not receiver or enclosing is None:
        return ""
    scope = _text(enclosing, content)
    kotlin = re.search(
        rf"\b{re.escape(receiver)}\s*:\s*([A-Za-z_][\w$.<>?]*)", scope
    )
    if kotlin:
        return kotlin.group(1).split("<", 1)[0]
    java = re.search(
        rf"\b([A-Za-z_][\w$.<>?]*)\s+{re.escape(receiver)}\b", scope
    )
    if java and java.group(1) not in {"val", "var"}:
        return java.group(1).split("<", 1)[0]
    return ""


def _parse_file(path: Path, relative: str, source: str, parser) -> dict:
    encoded = source.encode("utf-8")
    tree = parser.parse(encoded)
    root = tree.root_node
    package_match = re.search(r"(?m)^\s*package\s+([\w.]+)", source)
    package = package_match.group(1) if package_match else ""
    declarations = []
    calls = []
    classes = []
    for node in _walk(root):
        if node.type in CLASS_TYPES:
            class_name = _name(node, encoded)
            if class_name:
                classes.append({
                    "name": class_name,
                    "qualified_name": f"{package}.{class_name}" if package else class_name,
                    "path": relative,
                    "line": node.start_point.row + 1,
                    "kind": node.type,
                })
        elif node.type in DECLARATION_TYPES:
            name = _name(node, encoded)
            if not name:
                continue
            owner_node = _ancestor(node, CLASS_TYPES)
            owner = _name(owner_node, encoded) if owner_node is not None else "<top-level>"
            arity, parameter_types = _parameter_data(node, encoded)
            symbol_id = (
                f"{package + '.' if package else ''}{owner}#"
                f"{name}({','.join(parameter_types)})"
            )
            raw_header = _text(node, encoded).split("{", 1)[0].split("=", 1)[0]
            declarations.append({
                "symbol_id": symbol_id,
                "name": name,
                "arity": arity,
                "parameter_types": parameter_types,
                "owner": owner,
                "package": package,
                "path": relative,
                "line": node.start_point.row + 1,
                "end_line": node.end_point.row + 1,
                "override": bool(re.search(r"\b(?:override|@Override)\b", raw_header)),
                "kind": node.type,
            })
        elif node.type in CALL_TYPES:
            name = _call_name(node, encoded)
            if not name:
                continue
            enclosing = _ancestor(node, DECLARATION_TYPES)
            receiver = _receiver(node, encoded, name)
            calls.append({
                "name": name,
                "arity": _argument_count(node),
                "receiver": receiver,
                "receiver_type": _receiver_type(receiver, enclosing, encoded),
                "path": relative,
                "line": node.start_point.row + 1,
                "enclosing_symbol": _name(enclosing, encoded) if enclosing else "<initializer>",
                "snippet": "\n".join(
                    f"{i + 1}: {line}"
                    for i, line in enumerate(source.splitlines())
                    if abs(i - node.start_point.row) <= 3
                ),
            })
    return {
        "path": relative,
        "has_error": root.has_error,
        "classes": classes,
        "declarations": declarations,
        "calls": calls,
    }


def build_semantic_index(
    repo_root: Path,
    source_cache: dict[Path, str],
    target_file: Path,
    target_method: str,
    target_arity: int | None = None,
) -> dict:
    """Parse the repository and resolve calls to the target symbol family."""
    # Native tree-sitter bindings have shown access violations on very large,
    # unrelated generated/malformed files. Always run them in a disposable
    # child process: repository size is not a reliable predictor of malformed
    # syntax or native binding crashes. Semantic resolution only needs the
    # target and files that mention its symbol, so keep that child process on a
    # bounded, relevant corpus. The lexical repository scan still supplies
    # broader discovery and marks truncated semantic coverage explicitly.
    target_token = re.compile(rf"\b{re.escape(target_method)}\s*\(")
    relevant_items = [
        (path, source) for path, source in source_cache.items()
        if path.resolve() == target_file.resolve()
        or (len(source) <= 300_000 and target_token.search(source))
    ]
    relevant_items.sort(key=lambda item: (
        item[0].resolve() != target_file.resolve(),
        item[0].as_posix(),
    ))
    semantic_truncated = len(relevant_items) > 120
    bounded_source_cache = dict(relevant_items[:120])
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root,
        capture_output=True, text=True,
    )
    revision_key = revision.stdout.strip() if revision.returncode == 0 else "unknown"
    cache_key = (
        str(repo_root.resolve()), revision_key, len(bounded_source_cache),
        sum(len(source) for source in bounded_source_cache.values()),
        target_method,
        tuple(path.as_posix() for path in bounded_source_cache),
    )
    parsed_repo = _PARSE_CACHE.get(cache_key)
    if parsed_repo is None:
        files, isolation_warning = _parse_batch_isolated(
            list(bounded_source_cache.items()), repo_root
        )
        if files is None:
            return _lexical_qualified_fallback(
                repo_root, source_cache, target_file, target_method,
                target_arity, isolation_warning,
            )
        parse_errors = []
        for parsed in files:
            if parsed["has_error"]:
                parse_errors.append(parsed["path"])
        parsed_repo = {
            "files": files,
            "parse_errors": parse_errors,
            "declarations": [
                item for file in files for item in file["declarations"]
            ],
            "calls": [item for file in files for item in file["calls"]],
            "isolation_warning": isolation_warning,
        }
        _PARSE_CACHE[cache_key] = parsed_repo
    files = parsed_repo["files"]
    parse_errors = parsed_repo["parse_errors"]
    declarations = parsed_repo["declarations"]
    calls = parsed_repo["calls"]
    target_relative = target_file.relative_to(repo_root).as_posix()
    target_candidates = [
        item for item in declarations
        if item["path"] == target_relative and item["name"] == target_method
        and (target_arity is None or item["arity"] == target_arity)
    ]
    # A native grammar crash on the target file is not the same as the target
    # method being absent from it. _parse_batch_isolated bisects a crashed
    # batch and returns everything it could still parse, so `files` can come
    # back healthy while the one file this finding is about was dropped --
    # exactly what happens on me.blog.korn123.easydiary's ZipHelper.kt, which
    # segfaults tree-sitter-kotlin 1.1.0 under tree-sitter 0.26.0. The lexical
    # fallback below exists for precisely this situation, but it was reachable
    # only when the entire batch failed, so a partial recovery that lost the
    # target fell through to "unresolved" with no callers at all. That is what
    # left resolved_callers "unknown" in 20 of 54 stored packets and made
    # adjudications block on caller scope the resolver was asked to establish.
    # Only the dropped-target case is redirected: when the target file did
    # parse and the method still is not in it, the mismatch is real and must
    # keep reporting unresolved rather than being masked by a lexical guess.
    if target_relative not in {parsed["path"] for parsed in files}:
        return _lexical_qualified_fallback(
            repo_root, source_cache, target_file, target_method, target_arity,
            str(parsed_repo.get("isolation_warning", ""))
            or "target file was dropped by isolated-parser recovery",
        )
    if not target_candidates:
        return {
            "status": "unresolved", "backend": "tree_sitter",
            "reason": "target declaration not found in AST",
            "parse_error_files": parse_errors,
        }
    target = target_candidates[0]
    family = [
        item for item in declarations
        if item["name"] == target["name"] and item["arity"] == target["arity"]
    ]
    class_names = {target["owner"], target["owner"].rsplit(".", 1)[-1]}
    target_outer = target_file.stem
    target_file_record = next(
        (item for item in files if item["path"] == target_relative), {}
    )
    target_class_names = [
        item.get("name", "")
        for item in target_file_record.get("classes", [])
        if item.get("name")
    ]
    nested_owner = target["owner"]
    if (nested_owner == "<top-level>"
            and ("Builder" in target_class_names or target["name"] == "build")):
        nested_owner = "Builder"
    target_qualifier = f"{target_outer}.{nested_owner}"
    generic_target = target["name"].lower() in GENERIC_METHOD_NAMES
    resolved_calls = []
    ambiguous_calls = []
    for call in calls:
        if call["name"] != target["name"] or call["arity"] != target["arity"]:
            continue
        receiver = call.get("receiver", "")
        receiver_type = call.get("receiver_type", "")
        receiver_matches = (
            receiver in class_names
            or receiver_type in class_names
            or receiver_type.endswith("." + target["owner"])
        )
        qualified_generic_match = (
            target_qualifier in receiver
            or target_qualifier in call.get("snippet", "")
            or receiver_type.endswith(target_qualifier)
        )
        if ((not generic_target and len(family) == 1)
                or (generic_target and qualified_generic_match)
                or (not generic_target and receiver_matches)):
            resolved = dict(call)
            resolved["resolved_symbol_id"] = target["symbol_id"]
            resolved["resolution"] = (
                "qualified_receiver" if generic_target
                else "unique_name_and_arity" if len(family) == 1
                else "receiver_static_type"
            )
            resolved_calls.append(resolved)
        else:
            ambiguous = dict(call)
            ambiguous["candidate_symbol_ids"] = [
                item["symbol_id"] for item in family[:20]
            ]
            ambiguous_calls.append(ambiguous)
    related_declarations = [
        item for item in family if item["symbol_id"] != target["symbol_id"]
    ]
    # Some Kotlin grammar versions expose a chained constructor call as the
    # outer call expression and omit the terminal generic build() node. Recover
    # only an explicitly qualified Target.Builder(...).build() shape; this is
    # substantially stronger than a repository-wide same-name match and cannot
    # confuse Uri/Notification/OkHttp builders with the application builder.
    if generic_target and nested_owner != "<top-level>":
        qualified_pattern = re.compile(
            rf"\b{re.escape(target_qualifier)}\s*\([^;\n]*?"
            rf"\.\s*{re.escape(target['name'])}\s*\("
        )
        existing_locations = {
            (item.get("path"), item.get("line")) for item in resolved_calls
        }
        for path, source in bounded_source_cache.items():
            relative = path.relative_to(repo_root).as_posix()
            for match in qualified_pattern.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                if (relative, line) in existing_locations:
                    continue
                lines = source.splitlines()
                resolved_calls.append({
                    "name": target["name"], "arity": target["arity"],
                    "receiver": target_qualifier, "receiver_type": target_qualifier,
                    "path": relative, "line": line,
                    "enclosing_symbol": "<qualified-expression>",
                    "snippet": "\n".join(
                        f"{index + 1}: {value}" for index, value in enumerate(lines)
                        if abs(index - (line - 1)) <= 3
                    ),
                    "resolved_symbol_id": target["symbol_id"],
                    "resolution": "qualified_constructor_chain",
                })
                existing_locations.add((relative, line))
    return {
        "status": "available",
        "backend": "tree_sitter_java_kotlin",
        "type_resolved": False,
        "target_symbol": target,
        "resolved_callers": resolved_calls,
        "ambiguous_calls": ambiguous_calls,
        "related_declarations": related_declarations,
        "parse_error_files": parse_errors,
        "coverage": {
            "parsed_files": len(files),
            "declarations": len(declarations),
            "calls": len(calls),
            "target_calls_resolved": len(resolved_calls),
            "target_calls_ambiguous": len(ambiguous_calls),
            "candidate_files_truncated": semantic_truncated,
        },
        "warning": (
            "AST and signature resolution is semantic, but full JVM type "
            "resolution requires a project classpath and is not claimed here. "
            + str(parsed_repo.get("isolation_warning", ""))
        ),
    }


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--parse-batch":
        raise SystemExit(_parse_batch_worker(Path(sys.argv[2]), Path(sys.argv[3])))
    raise SystemExit("semantic_index.py is an internal module")
