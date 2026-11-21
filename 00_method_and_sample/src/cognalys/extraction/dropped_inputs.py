"""Inputs an edited function read before the patch and its new version no longer reads.

Salty (2026-10-29, run_v2): the replan dropped `BuildConfig.APP_SALT` from the
key derivation of new messages, with no invariant against it. The behaviour
review could only call that a maintainer decision: the definition
(`buildConfigField "String", "APP_SALT", ...` in app/build.gradle, from
`System.getenv("APP_SALT") ?: "Salty@AES-256"`) was not in its packet.

For each production function an edit rewrites, this compares the inputs its
original body reads -- its parameters, `BuildConfig` fields and
`System.getenv` values -- with what the function of the same name reads in
the corrected code, together with the functions the patch declares that it
calls. A function the patch keeps only for a legacy path (a name saying
legacy, old, deprecated or compat) does not count: a copy kept there does
not make the new path use the value. Constants are not inputs, so a constant
IV or salt the patch replaces is never listed. Each dropped input carries
where it is defined. Lexical: what it lists is established, what it misses
says nothing.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

_KOTLIN_FUNCTION = re.compile(
    r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w.?]+\.)?(?P<name>\w+)\s*\((?P<params>[^)]*)\)")
_JAVA_FUNCTION = re.compile(
    r"(?m)^[ \t]*(?:@\w+(?:\([^)]*\))?\s+)*"
    r"(?:(?:public|private|protected|static|final|synchronized|abstract|native)\s+)*"
    r"(?!(?:return|new|else|throw|case|fun)\b)[\w<>\[\],.?]+\s+"
    r"(?!(?:if|for|while|switch|catch|synchronized)\b)(?P<name>[A-Za-z_]\w*)\s*"
    r"\((?P<params>[^)]*)\)\s*(?:throws\s+[\w.,\s]+)?\{")
_LEGACY_NAME = re.compile(r"(?i)legacy|(?<![a-z])old|deprecated|compat")
_BUILD_CONFIG = re.compile(r"\bBuildConfig\.(?P<field>[A-Z_][A-Z0-9_]*)\b")
_ENVIRONMENT = re.compile(r"\bSystem\.getenv\s*\(\s*\"(?P<name>[^\"]+)\"\s*\)")


def _is_test(path: str) -> bool:
    return bool(re.search(r"(?:^|/)src/(?:test|androidTest|sharedTest)\w*/|(?:^|/)tests?/", path))


def _body_from(code: str, start: int) -> str:
    """The function body starting at offset `start` (just after the signature)."""
    rest = code[start:]
    expression = re.match(r"\s*(?::\s*[\w<>?,. \[\]]+)?\s*=", rest)
    brace = rest.find("{")
    if expression and (brace < 0 or expression.end() <= brace):
        # Kotlin expression body: up to the next declaration at the same depth.
        following = re.search(r"\n\s*(?:(?:private|public|internal|protected|override)\s+)*"
                              r"(?:fun|val|var|class|object)\b", rest[expression.end():])
        return rest[expression.end():expression.end() + following.start()] if following \
            else rest[expression.end():]
    if brace < 0:
        return ""
    depth = 0
    for offset in range(brace, len(rest)):
        if rest[offset] == "{":
            depth += 1
        elif rest[offset] == "}":
            depth -= 1
            if depth == 0:
                return rest[brace + 1:offset]
    return rest[brace + 1:]


def _parameters(text: str) -> List[str]:
    names = []
    for parameter in re.split(r",(?![^<]*>)", text or ""):
        parameter = re.sub(r"@\w+(?:\([^)]*\))?|\bfinal\b|\bvararg\b|=.*$", "", parameter).strip()
        if not parameter:
            continue
        if ":" in parameter:  # Kotlin `name: Type`
            name = parameter.split(":", 1)[0].strip().split()[-1:]
        else:  # Java `Type name`
            name = parameter.split()[-1:]
        if name and re.fullmatch(r"[A-Za-z_]\w*", name[0]):
            names.append(name[0])
    return names


def functions(code: str) -> List[Dict]:
    """[{"name", "parameters", "body"}] for every function `code` declares."""
    found = []
    for pattern in (_KOTLIN_FUNCTION, _JAVA_FUNCTION):
        for match in pattern.finditer(str(code or "")):
            end = match.end() - (1 if pattern is _JAVA_FUNCTION else 0)
            found.append({"name": match.group("name"),
                          "parameters": _parameters(match.group("params")),
                          "body": _body_from(code, end), "start": match.start()})
    found.sort(key=lambda item: item["start"])
    return found


def _reads(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w.]){re.escape(name)}\b(?!\s*=(?!=))", text))


def _inputs_read(function: Dict) -> List[Tuple[str, str]]:
    """[(input, kind)] the function's body reads, in order of first use."""
    body = function["body"]
    found: Dict[str, str] = {}
    for name in function["parameters"]:
        if _reads(body, name):
            found.setdefault(name, "parameter")
    for match in _BUILD_CONFIG.finditer(body):
        found.setdefault(f"BuildConfig.{match.group('field')}", "build_config_field")
    for match in _ENVIRONMENT.finditer(body):
        found.setdefault(f'System.getenv("{match.group("name")}")', "environment")
    return list(found.items())


def _still_read(new_text: str, name: str, kind: str) -> bool:
    if kind == "parameter":
        return _reads(new_text, name)
    return name in new_text


def _definitions(repo_root: Optional[Path], name: str, kind: str, function: str) -> List[Dict]:
    """Where an input is defined: its parameter, buildConfigField and the values it reads."""
    if kind == "parameter":
        return [{"kind": "parameter", "of": function}]
    if repo_root is None or not Path(repo_root).is_dir():
        return []
    root = Path(repo_root)
    key = name.split(".", 1)[1] if kind == "build_config_field" else re.sub(
        r'^System\.getenv\("|"\)$', "", name)
    definitions: List[Dict] = []
    builds = [path for pattern in ("build.gradle", "build.gradle.kts", "*/build.gradle",
                                   "*/build.gradle.kts", "*/*/build.gradle", "*/*/build.gradle.kts")
              for path in root.glob(pattern) if path.is_file()]
    field = re.compile(rf"\bbuildConfigField\s*\(?\s*[\"'][^\"']*[\"']\s*,\s*[\"']{re.escape(key)}[\"']\s*,"
                       rf"\s*(?P<value>.+?)\)?\s*$")
    environment = re.compile(rf"\bSystem\.getenv\s*\(\s*[\"']{re.escape(key)}[\"']\s*\)")
    for path in builds:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        for index, line in enumerate(lines):
            match = field.search(line) if kind == "build_config_field" else None
            if match or (kind == "environment" and environment.search(line)):
                definitions.append({"source_file_path": relative, "line": index + 1,
                                    "code": line.strip()[:200]})
            if not match:
                continue
            # The value a build-script variable gives it (`"\"${appSalt}\""`),
            # and where that variable comes from.
            for variable in re.findall(r"\$\{?(\w+)\}?|\+\s*(\w+)", match.group("value")):
                variable = next(part for part in variable if part)
                for other, text in enumerate(lines):
                    if re.search(rf"\b(?:def|val|var)\s+{re.escape(variable)}\s*=", text):
                        definitions.append({"source_file_path": relative, "line": other + 1,
                                            "code": text.strip()[:200]})
    workflows = [path for pattern in (".github/workflows/*.yml", ".github/workflows/*.yaml")
                 for path in root.glob(pattern) if path.is_file()]
    for path in workflows:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for index, line in enumerate(lines):
            if re.search(rf"\b{re.escape(key)}\b", line):
                definitions.append({"source_file_path": path.relative_to(root).as_posix(),
                                    "line": index + 1, "code": line.strip()[:200]})
    return definitions[:8]


def dropped_inputs(edits: Iterable[Dict], repo_root: Optional[Path] = None) -> List[Dict]:
    """[{"input", "kind", "function", "source_file_path", "definitions"}]."""
    dropped: List[Dict] = []
    seen = set()
    for edit in edits or []:
        if not isinstance(edit, dict):
            continue
        path = str(edit.get("source_file_path") or edit.get("source_file") or "")
        if _is_test(path) or str(edit.get("edit_kind", "")) == "create":
            continue
        original = str(edit.get("original_code", ""))
        corrected = str(edit.get("corrected_code", ""))
        before = functions(original) or [{"name": "", "parameters": [], "body": original}]
        after = functions(corrected)
        by_name: Dict[str, Dict] = {}
        for item in after:
            by_name.setdefault(item["name"], item)
        for function in before:
            if function["name"]:
                new = by_name.get(function["name"])
                if new is None:
                    continue
                helpers = [item["body"] for item in after
                           if item is not new and item["name"] != function["name"]
                           and not _LEGACY_NAME.search(item["name"])
                           and re.search(rf"(?<![\w.]){re.escape(item['name'])}\s*\(", new["body"])]
                new_text = "\n".join([new["body"], *helpers])
            else:
                new_text = corrected
            for name, kind in _inputs_read(function):
                if _still_read(new_text, name, kind) or (path, function["name"], name) in seen:
                    continue
                seen.add((path, function["name"], name))
                dropped.append({
                    "input": name, "kind": kind, "function": function["name"],
                    "source_file_path": path,
                    "definitions": _definitions(repo_root, name, kind, function["name"]),
                })
    return dropped
