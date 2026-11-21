"""Build a bounded, repository-local value usage graph.

The graph is deliberately lightweight.  It does not claim type resolution;
instead it records line-addressable evidence for assignments, wrapper calls,
field/property propagation, constant comparisons, sinks, tests, build inputs,
and structurally similar implementations.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


SOURCE_SUFFIXES = {".java", ".kt", ".kts"}
BUILD_SUFFIXES = {".gradle", ".kts", ".properties", ".toml"}
SKIPPED_PARTS = {".git", ".gradle", "build", "dist", "node_modules"}

SINK_PATTERNS = {
    "persistence": r"\b(?:database|sqlite|sharedpreferences|preference|persist|save|write|file)\b",
    "network_or_protocol": r"\b(?:http|request|response|header|socket|endpoint|upload|send|api)\b",
    "identity_or_security": r"\b(?:password|credential|secret|fingerprint|certificate|identifier|token)\b",
    "ui_or_runtime_configuration": r"\b(?:view|adapter|graph|configuration|sizeavailable|visibility|layout|screen)\b",
    "cache": r"\b(?:cache|cached|evict|invalidate)\b",
}


def _eligible(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_SUFFIXES and not any(
        part.lower() in SKIPPED_PARTS for part in path.parts
    )


def _read(path: Path, max_chars: int = 500_000) -> str:
    try:
        if path.stat().st_size > max_chars:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _snippet(lines: list[str], index: int, radius: int = 3) -> str:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return "\n".join(f"{line + 1}: {lines[line]}" for line in range(start, end))


def _symbol_pattern(symbol: str, kind: str) -> re.Pattern:
    if kind == "method":
        return re.compile(rf"\b{re.escape(symbol)}\s*\(")
    if "." in symbol:
        return re.compile(re.escape(symbol))
    return re.compile(rf"\b{re.escape(symbol)}\b")


def _lhs(line: str) -> str:
    kotlin = re.search(
        r"\b(?:val|var)\s+([A-Za-z_]\w*)"
        r"(?:\s*:\s*[^=]+)?\s*=\s*(?!=)",
        line,
    )
    if kotlin:
        return kotlin.group(1)
    qualified = re.search(
        r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*=\s*(?!=)", line
    )
    if qualified:
        return qualified.group(1)
    simple = re.search(r"\b([A-Za-z_]\w*)\s*=\s*(?!=)", line)
    return simple.group(1) if simple else ""


def _symbol_variants(symbol: str) -> list[str]:
    parts = symbol.split(".")
    variants = [symbol]
    if len(parts) >= 2:
        variants.append(".".join(parts[-2:]))
    if len(parts) == 1:
        variants.append(parts[-1])
    return list(dict.fromkeys(value for value in variants if len(value) >= 3))


def collect_value_usage_graph(
    repo_root: Path,
    source_file: Path,
    method_name: str,
    method_body: str,
    call_sites: list[dict],
    max_depth: int = 4,
    max_nodes: int = 60,
    max_evidence: int = 80,
    source_cache: dict[Path, str] | None = None,
    repository_paths: list[Path] | None = None,
) -> dict:
    """Collect a bounded forward slice from a method result and its inputs."""
    source_cache = source_cache or {}
    files = [
        (path, content, content.splitlines())
        for path, content in source_cache.items() if content
    ]
    build_files = []
    paths = repository_paths
    if paths is None:
        paths = []
        for directory, names, filenames in os.walk(repo_root):
            names[:] = [name for name in names if name.lower() not in SKIPPED_PARTS]
            base = Path(directory)
            paths.extend(base / filename for filename in filenames)
    for path in paths:
        if _eligible(path) and path not in source_cache:
            content = _read(path)
            if content:
                files.append((path, content, content.splitlines()))
        elif path.suffix.lower() in BUILD_SUFFIXES or path.name.lower().endswith(
                (".gradle", ".gradle.kts")):
            content = _read(path, 250_000)
            if content:
                build_files.append((path, content, content.splitlines()))

    # Build one inverted symbol index for this repository. Graph expansion can
    # then inspect only candidate lines instead of rescanning every source file
    # once for every newly discovered symbol.
    symbol_occurrences: dict[str, list[tuple[Path, list[str], int, str]]] = {}
    files_by_stem: dict[str, list[tuple[Path, list[str]]]] = {}
    for path, _, lines in files:
        files_by_stem.setdefault(path.stem.lower(), []).append((path, lines))
        for index, line in enumerate(lines):
            for token in set(re.findall(r"[A-Za-z_]\w*", line)):
                symbol_occurrences.setdefault(token, []).append(
                    (path, lines, index, line)
                )

    nodes = {}
    edges = []
    evidence = []
    sinks = {name: [] for name in SINK_PATTERNS}
    comparisons = []
    queue = []

    def add_symbol(symbol: str, kind: str, depth: int, origin: str) -> None:
        key = f"{kind}:{symbol}"
        if not symbol or key in nodes or len(nodes) >= max_nodes:
            return
        nodes[key] = {"id": key, "symbol": symbol, "kind": kind,
                      "depth": depth, "origin": origin}
        queue.append((symbol, kind, depth, key))

    result_node = f"return:{method_name}"
    nodes[result_node] = {
        "id": result_node, "symbol": method_name, "kind": "method_return",
        "depth": 0, "origin": source_file.relative_to(repo_root).as_posix(),
    }

    for site in call_sites:
        text = str(site.get("snippet", ""))
        site_path = str(site.get("path", ""))
        invocation_lines = [line for line in text.splitlines() if re.search(
            rf"\b{re.escape(method_name)}\s*\(", line
        )]
        for line in invocation_lines or [text]:
            clean_line = re.sub(r"^\d+:\s*", "", line)
            assignment = _lhs(clean_line)
            if assignment:
                for variant in _symbol_variants(assignment):
                    add_symbol(variant, "value", 1, site_path)
                    edges.append({"from": result_node, "to": f"value:{variant}",
                                  "kind": "ASSIGNED_TO", "evidence": clean_line.strip()})
            invocation_at = clean_line.find(method_name)
            outer_calls = re.findall(r"\b([A-Za-z_]\w*)\s*\(", clean_line[:invocation_at])
            if outer_calls:
                wrapper = outer_calls[-1]
                add_symbol(wrapper, "method", 1, site_path)
                edges.append({"from": result_node, "to": f"method:{wrapper}",
                              "kind": "PASSED_TO", "evidence": clean_line.strip()})
        for symbol in site.get("flow_symbols", []) or []:
            kind = "method" if re.search(
                rf"\breturn\b[^\n;]*\b{re.escape(str(symbol))}\s*\(", text
            ) else "value"
            add_symbol(str(symbol), kind, 1, site_path)
            edges.append({
                "from": result_node, "to": f"{kind}:{symbol}",
                "kind": "RETURNED_BY" if kind == "method" else "ASSIGNED_TO",
                "evidence": text,
            })

    input_chains = re.findall(
        r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", method_body
    )
    for chain in input_chains:
        for variant in _symbol_variants(chain):
            add_symbol(variant, "input", 0, "target_method")
            edges.append({"from": f"input:{variant}", "to": result_node,
                          "kind": "DERIVED_FROM", "evidence": chain})

    scanned = set()
    while queue and len(evidence) < max_evidence:
        symbol, kind, depth, node_id = queue.pop(0)
        if depth > max_depth or node_id in scanned:
            continue
        scanned.add(node_id)
        pattern = _symbol_pattern(symbol, kind)
        owner = ""
        member = ""
        if kind == "value" and "." in symbol:
            symbol_parts = symbol.split(".")
            owner = symbol_parts[-2]
            member = symbol_parts[-1]
        matches = 0
        lookup_symbol = symbol.rsplit(".", 1)[-1]
        candidates = list(symbol_occurrences.get(lookup_symbol, ()))
        if owner:
            for path, lines in files_by_stem.get(owner.lower(), ()):
                candidates.extend(
                    (path, lines, index, line)
                    for index, line in enumerate(lines)
                    if member in line
                )
        seen_candidates = set()
        for path, lines, index, line in candidates:
            candidate_key = (path, index)
            if candidate_key in seen_candidates:
                continue
            seen_candidates.add(candidate_key)
            relative = path.relative_to(repo_root).as_posix()
            direct_match = pattern.search(line)
            owner_declaration_match = (
                bool(owner)
                and path.stem.lower() == owner.lower()
                and bool(re.search(rf"\b{re.escape(member)}\b", line))
            )
            if not direct_match and not owner_declaration_match:
                continue
            if kind == "method":
                method_declaration = re.search(
                    rf"\b(?:fun|void|int|long|boolean|String|File|[A-Z]\w*)\s+"
                    rf"{re.escape(symbol)}\s*\(", line
                )
                method_call = bool(direct_match) and not method_declaration
                if not method_declaration and not method_call:
                    continue
            matches += 1
            record = {"node": node_id, "path": relative, "line": index + 1,
                      "snippet": _snippet(lines, index)}
            evidence.append(record)
            lowered = f"{relative}\n{line}".lower()
            for sink, expression in SINK_PATTERNS.items():
                if re.search(expression, lowered, re.IGNORECASE) and len(sinks[sink]) < 12:
                    sinks[sink].append(record)

            assignment = _lhs(line)
            match_start = direct_match.start() if direct_match else len(line)
            if assignment and line.find("=") < match_start:
                assignment_variants = _symbol_variants(assignment)
                if owner_declaration_match and "." not in assignment:
                    assignment_variants = []
                for variant in assignment_variants:
                    add_symbol(variant, "value", depth + 1, relative)
                    edges.append({"from": node_id, "to": f"value:{variant}",
                                  "kind": "ASSIGNED_TO", "evidence": line.strip()})

            if re.search(r"==|!=", line):
                comparison = {
                    "node": node_id, "path": relative, "line": index + 1,
                    "snippet": _snippet(lines, index),
                }
                if comparison not in comparisons:
                    comparisons.append(comparison)
                for compared in re.findall(r"\b[A-Za-z_]\w*\b", line):
                    if compared in {symbol, "true", "false", "null"}:
                        continue
                    if compared.lower() in {"fun", "return", "if", "else"}:
                        continue
                    add_symbol(compared, "value", depth + 1, relative)
                    edges.append({
                        "from": node_id, "to": f"value:{compared}",
                        "kind": "COMPARED_WITH", "evidence": line.strip(),
                    })

            if owner_declaration_match:
                derived_property = re.search(
                    r"\b(?:val|var)\s+([A-Za-z_]\w*)[^\n=]*"
                    r"(?:\bget\s*\(\s*\)|=)[^\n]*",
                    line,
                )
                if derived_property and derived_property.group(1) != member:
                    qualified_property = f"{owner}.{derived_property.group(1)}"
                    add_symbol(qualified_property, "value", depth + 1, relative)
                    edges.append({
                        "from": node_id,
                        "to": f"value:{qualified_property}",
                        "kind": "DERIVES_PROPERTY",
                        "evidence": line.strip(),
                    })

            declaration = re.search(
                r"\b(?:fun|void|int|long|boolean|String|File|[A-Z]\w*)\s+"
                r"([A-Za-z_]\w*)\s*\(", line
            )
            if declaration and declaration.group(1) == symbol:
                constants = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", _snippet(lines, index, 5))
                for constant in constants:
                    add_symbol(constant, "constant", depth + 1, relative)
                    edges.append({"from": node_id, "to": f"constant:{constant}",
                                  "kind": "COMPARED_WITH_OR_USES", "evidence": line.strip()})

            constants = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", line)
            if re.search(r"==|!=|\bwhen\b|\bswitch\b", line):
                for constant in constants:
                    add_symbol(constant, "constant", depth + 1, relative)
                    edges.append({"from": node_id, "to": f"constant:{constant}",
                                  "kind": "COMPARED_WITH", "evidence": line.strip()})
            if matches >= 30 or len(evidence) >= max_evidence:
                break

    build_evidence = []
    if re.search(r"\b(?:packageName|applicationId)\b", method_body):
        for path, _, lines in build_files:
            relative = path.relative_to(repo_root).as_posix()
            for index, line in enumerate(lines):
                if re.search(r"\b(?:applicationId|applicationIdSuffix|namespace)\b", line):
                    build_evidence.append({"path": relative, "line": index + 1,
                                           "snippet": _snippet(lines, index, 2)})
                    if len(build_evidence) >= 20:
                        break

    operation_markers = [
        marker for marker in (
            "MessageDigest.getInstance", ".update(", "digest()",
            ".contentHashCode()", ".toHexString()", "Cipher.getInstance",
            "Mac.getInstance", "KeyStore.getInstance",
        ) if marker in method_body
    ]
    similar = []
    if len(operation_markers) >= 2:
        for path, content, lines in files:
            if path.resolve() == source_file.resolve():
                continue
            matched = [marker for marker in operation_markers if marker in content]
            if len(matched) < max(2, len(operation_markers) - 1):
                continue
            first_index = min(
                index for index, line in enumerate(lines)
                if any(marker in line for marker in matched)
            )
            similar.append({
                "path": path.relative_to(repo_root).as_posix(),
                "matched_operations": matched,
                "snippet": _snippet(lines, first_index, 8),
            })
            if len(similar) >= 12:
                break

    tests = []
    generic_test_symbols = {"size", "type", "text", "value", "result", "digest", "boolean", "int"}
    graph_symbols = set()
    for node in nodes.values():
        symbol = node["symbol"]
        candidates = [symbol]
        if "." in symbol:
            candidates.append(symbol.rsplit(".", 1)[-1])
        for candidate in candidates:
            if (len(candidate) >= 4
                    and candidate.lower() not in generic_test_symbols):
                graph_symbols.add(candidate)
    for path, content, _ in files:
        relative = path.relative_to(repo_root).as_posix()
        if "test" not in relative.lower():
            continue
        matched = sorted(symbol for symbol in graph_symbols if symbol in content)
        if matched:
            tests.append({"path": relative, "matched_symbols": matched[:12]})
            if len(tests) >= 20:
                break

    return {
        "status": "available",
        "nodes": list(nodes.values()),
        "edges": edges[: max_evidence * 2],
        "evidence": evidence,
        "sink_assessment": {
            "observed": [name for name, records in sinks.items() if records],
            "evidence": sinks,
            "warning": "Lexical sinks are leads for review, not proof of runtime flow.",
        },
        "comparison_evidence": comparisons[:20],
        "build_configuration": build_evidence,
        "related_tests": tests,
        "structurally_similar_implementations": similar,
        "limits": {
            "analysis_kind": "bounded_lexical_forward_slice",
            "max_depth": max_depth,
            "max_nodes": max_nodes,
            "max_evidence": max_evidence,
            "type_resolved": False,
        },
    }
