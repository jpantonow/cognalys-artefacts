"""Collect repository evidence needed for safe algorithm migrations."""

from __future__ import annotations

import logging
import re
import os
import hashlib
import subprocess
from pathlib import Path

_LOG = logging.getLogger(__name__)


SOURCE_SUFFIXES = {".java", ".kt", ".kts"}
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".xml", ".json", ".yml", ".yaml"}
SKIPPED_PARTS = {".git", ".gradle", "build", "dist", "node_modules"}
CATEGORIES = {
    "ephemeral_sensitive": ("sealedobject", "in-memory", "in_memory", "memorykey", "memoryiv"),
    "password": ("password", "passwd", "credential", "login", "pin"),
    "fingerprint": ("fingerprint", "thumbprint", "certificate", "cert"),
    "protocol": (
        "http", "okhttp", "endpoint", "network", "header", "request",
        "response", "socket", "protocol", "api", "json",
    ),
    "persistence": ("database", "sqlite", "sharedpreferences", "preference", "persist", "storage"),
    "cache": ("cache", "cached", "cachekey", "cache_key", "diskcache"),
}

REAL_PERSISTENCE_SINKS = (
    r"\bObjectOutputStream\s*\(\s*new\s+FileOutputStream\b",
    r"\bObjectInputStream\s*\(\s*new\s+FileInputStream\b",
    r"\bFiles\.(?:newOutputStream|write)\s*\(",
    r"\bopenFileOutput\s*\(",
    r"\bSharedPreferences\.Editor\b[^\n]*(?:putString|putStringSet)\s*\(",
    r"\b(?:Gson|Moshi|ObjectMapper)\b[^\n]*(?:toJson|writeValue)\s*\(",
)

ANDROID_MIN_SDK_PATTERNS = (
    r"\bminSdkVersion\s*(?:=\s*)?(\d+)",
    r"\bminSdk\s*(?:=\s*)?(\d+)",
)

ANDROID_APPLICATION_ID_PATTERN = re.compile(
    r"\bapplicationId[ \t]*(?:=[ \t]*|[ \t]+)"
    r"[\"']([^\"'\r\n]+)[\"']"
)
ANDROID_APPLICATION_ID_SUFFIX_PATTERN = re.compile(
    r"\bapplicationIdSuffix[ \t]*(?:=[ \t]*|[ \t]+)"
    r"[\"']([^\"'\r\n]+)[\"']"
)

# These signals describe cache lifecycle behavior, rather than merely counting
# words such as "cache" or "storage".  They deliberately stay conservative: a
# cache is considered automatically migratable only when the bounded context
# shows both a miss path and a population/write path, and no evidence that the
# derived value participates in a sensitive or externally visible contract.
CACHE_BEHAVIOR_PATTERNS = {
    "miss_path": (
        r"\bexists\s*\(\s*\)",
        r"\breturn\s+null\b",
        r"\bif\s*\([^\n)]*(?:cache|file)[^\n)]*==\s*null",
        r"\b(?:cache\s*miss|cachemiss)\b",
    ),
    "population_path": (
        r"\b(?:put|write|save)\s*\(",
        r"\b(?:fileoutputstream|outputstream|writebytes|writetext)\b",
        r"\b(?:encode|serialize|serialise)\s*\(",
    ),
    "derived_location": (
        r"\bnew\s+file\s*\([^\n]*(?:cache|key|digest|hash)",
        r"\b(?:cachedir|cache_dir|getcachedir|cachefile|cache_file)\b",
        r"\b(?:cachekey|cache_key)\b",
        r"\b(?:dbfilename|databasefilename|database_name)\b",
        r"[\"'][^\"']*\.db[\"']",
    ),
    "eviction_path": (
        r"\b(?:clear|evict|invalidate|delete)\s*\(",
        r"\bdeleteonexit\s*\(",
    ),
}

CACHE_CONTRACT_BLOCKERS = {
    "credential_or_secret": (
        r"\b(?:password|passwd|credential|secret|private\s*key|pin)\b",
    ),
    "fingerprint_or_identity": (
        r"\b(?:fingerprint|thumbprint|certificate|stable\s+id|identifier)\b",
    ),
    "external_protocol": (
        r"\b(?:authorization|http\s+header|request\s+header|protocol\s+field)\b",
        r"\b(?:send|upload|transmit)\s*\([^\n]*(?:digest|hash|key)",
    ),
}


def _eligible(path: Path, suffixes: set[str]) -> bool:
    return path.suffix.lower() in suffixes and not any(
        part.lower() in SKIPPED_PARTS for part in path.parts
    )


def _read(path: Path, max_chars: int = 500_000) -> str:
    try:
        if path.stat().st_size > max_chars:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _repository_files(root: Path):
    for directory, names, filenames in os.walk(root):
        names[:] = [name for name in names if name.lower() not in SKIPPED_PARTS]
        base = Path(directory)
        for filename in filenames:
            yield base / filename


def _snippet(lines: list[str], line_index: int, radius: int = 5) -> str:
    start = max(0, line_index - radius)
    end = min(len(lines), line_index + radius + 1)
    return "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))


def _contains_keyword(text: str, keyword: str) -> bool:
    boundary_match = bool(re.search(
        rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])",
        text,
        re.IGNORECASE,
    ))
    # Long terms are safe to recognize inside camelCase identifiers such as
    # cacheKey and passwordHash. Short terms such as pin and api require word
    # boundaries to avoid matching fingerprint or unrelated words.
    return boundary_match or (len(keyword) >= 5 and keyword.lower() in text.lower())


def _matching_patterns(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE)]


# Words a declaration regex can read as a type or a name: `return
# inputStream(` is not a function named inputStream, and NotallyX's flow
# symbols took `inputStream`, which then matched app/build.gradle.kts and
# ordered it ahead of the real call site (2026-10-28).
_NOT_FLOW_SYMBOLS = frozenset({
    "return", "new", "throw", "else", "case", "yield", "when", "null", "true",
    "false", "this", "super", "if", "for", "while", "try", "catch", "val", "var",
    "fun", "it", "is", "in", "as", "do",
})
_KEYWORD_BEFORE_NAME = frozenset({"return", "new", "throw", "else", "case", "yield"})
_KOTLIN_FUNCTION = re.compile(r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w.?<>]+\.)?(\w+)\s*\(")
_JAVA_METHOD = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|abstract|"
    r"synchronized|native)\s+)*([\w$.<>?,\[\]]+)\s+"
    r"([A-Za-z_]\w*)\s*\("
)
# A bare type name (ByteArray, ZipVerificationException): UpperCamelCase.
# ALL_CAPS constants and lower-case wrapper names stay flow symbols.
_BARE_TYPE_NAME = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)*$")


def _flow_symbol(name: str) -> bool:
    return (len(name) >= 3 and name not in _NOT_FLOW_SYMBOLS
            and not _BARE_TYPE_NAME.match(name))


def _flow_symbols(lines: list[str], line_index: int) -> set[str]:
    """Extract bounded identifiers that can lead to the next consumer edge."""
    symbols = set()
    line = lines[line_index]
    # `val digest: ByteArray = target()` names `digest`, not its type.
    assignment = re.search(
        r"\b(?:val|var)\s+([A-Za-z_]\w*)\s*(?::\s*[^=]+?)?(?<![=!<>])=(?!=)", line
    ) or re.search(
        r"(?<![.\w])(?:[A-Za-z_$][\w$<>?]*\s+)?"
        r"([A-Za-z_]\w*)\s*(?<![=!<>])=(?!=)", line
    )
    if assignment:
        lhs_start = assignment.start(1)
        # A member/property assignment such as configuration.size = ... is a
        # semantic sink, not a new repository-wide symbol named `size`.
        if lhs_start == 0 or line[lhs_start - 1] != ".":
            symbols.add(assignment.group(1))
    put_receiver = re.search(
        r"\b([A-Za-z_]\w*)\s*\.\s*put\s*\(\s*[\"']([^\"']+)[\"']",
        line,
    )
    if put_receiver:
        symbols.update(put_receiver.groups())
    symbols.update(re.findall(r"\.put\s*\(\s*[\"']([^\"']+)[\"']", line))
    enclosing_method = ""
    for index in range(line_index, max(-1, line_index - 40), -1):
        kotlin = _KOTLIN_FUNCTION.search(lines[index])
        if kotlin:
            enclosing_method = kotlin.group(1)
            break
        java = _JAVA_METHOD.search(lines[index])
        if java and java.group(1) not in _KEYWORD_BEFORE_NAME:
            enclosing_method = java.group(2)
            break
    # Propagate an enclosing method only when it returns the affected value or
    # container. A method that compares a fingerprint and returns a boolean is
    # a semantic boundary: its callers need review context, but they do not all
    # become mandatory representation-migration edits.
    if enclosing_method and symbols:
        bounded_body = "\n".join(lines[line_index:min(len(lines), line_index + 80)])
        if any(re.search(rf"\breturn\s+{re.escape(symbol)}\b", bounded_body)
               for symbol in symbols):
            symbols.add(enclosing_method)
    # A direct `return Target.hash(...)` has no assignment, but the enclosing
    # method is itself the next value-flow symbol.  Without this edge wrapper
    # methods disappear and fields/comparisons using the wrapper are never
    # reached (for example sha1 -> notePayloadHash -> originalHash).
    if enclosing_method and re.search(r"\breturn\b[^;]*\w+\s*\(", line):
        symbols.add(enclosing_method)
    return {symbol for symbol in symbols if _flow_symbol(symbol)}


CLASS_ONLY_PRIVATE_RELEVANCE = (
    "target class only (target method is private, so no test "
    "can reference it by name)"
)


def _target_method_is_private(method_body: str, method_name: str) -> bool:
    """Whether a test can only reach the target method through its class.

    Test relevance below scores a "target class only" match at 20, well under
    the 60 that marks a file as a usable edit target, on the reasoning that
    merely naming the class is not evidence a test exercises the change.

    That reasoning holds for a public method, where a genuinely related test
    could have named it. It inverts for a private or package-private one:
    markor's ``createKeyFromPassword`` is ``private``, so no test can ever
    reference it, and ``JavaPasswordbasedCryptionTest`` -- which round-trips
    encrypt/decrypt over every declared Version, exactly the regression test
    the adjudication went on to require -- was scored 20, filtered out as a
    weak test path, and replaced in the evidence packet by an unrelated
    ``TextViewUtilsTest``. When the target cannot be named, exercising its
    class is the strongest coupling any test can have.

    Only an explicit ``private`` counts. Java package-private members are
    reachable from a test in the same package, and a Kotlin ``fun`` with no
    modifier is public by default, so inferring "not public" from a missing
    keyword would promote unrelated tests in exactly the repositories this is
    meant to leave alone. Returns False when no declaration is found in the
    supplied body, so an unparsed declaration keeps the previous scoring.
    """
    if not method_name or method_name.startswith("<"):
        return False
    declaration = re.compile(
        r"^\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*"
        r"((?:(?:public|private|protected|internal|static|final|abstract|"
        r"synchronized|native|default|strictfp|suspend|open|override)\s+)*)"
        r"[\w$.<>?,\[\]]+\s+" + re.escape(method_name) + r"\s*\("
    )
    for line in method_body.splitlines():
        match = declaration.match(line)
        if match:
            return bool(re.search(r"\bprivate\b", match.group(1)))
    return False


def _collect_android_string_resources(repo_root: Path, repository_paths,
                                      evidence_text: str) -> list[dict]:
    """Resolve exact R.string references to their XML declarations."""
    names = sorted(set(re.findall(r"\bR\.string\.([A-Za-z_]\w*)", evidence_text)))
    if not names:
        return []
    wanted = set(names)
    results = []
    for path in repository_paths:
        if path.suffix.lower() != ".xml" or "res" not in {
                part.lower() for part in path.parts}:
            continue
        content = _read(path, max_chars=500_000)
        if not content:
            continue
        for match in re.finditer(
            r"<string\b[^>]*\bname\s*=\s*[\"']([A-Za-z_]\w*)[\"'][^>]*>"
            r"([\s\S]*?)</string>", content, re.IGNORECASE,
        ):
            name = match.group(1)
            if name not in wanted:
                continue
            results.append({
                "resource": f"R.string.{name}",
                "source_file_path": path.relative_to(repo_root).as_posix(),
                "line": content.count("\n", 0, match.start()) + 1,
                "declaration": match.group(0),
            })
    grouped = []
    for name in names:
        candidates = [
            item for item in results if item["resource"] == f"R.string.{name}"
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda item: (
            0 if "/values/" in item["source_file_path"] else 1,
            item["source_file_path"],
        ))
        primary = dict(candidates[0])
        primary["localized_variant_count"] = max(0, len(candidates) - 1)
        primary["localized_variant_paths"] = [
            item["source_file_path"] for item in candidates[1:11]
        ]
        grouped.append(primary)
    return grouped


def _pattern_evidence(path: Path, repo_root: Path, patterns: tuple[str, ...],
                      limit: int = 8, content: str | None = None) -> list[dict]:
    """Return bounded, line-addressable evidence for semantic source patterns."""
    content = content if content is not None else _read(path)
    if not content:
        return []
    lines = content.splitlines()
    evidence = []
    for index, line in enumerate(lines):
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns):
            evidence.append({
                "path": path.relative_to(repo_root).as_posix(),
                "line": index + 1,
                "snippet": _snippet(lines, index, 4),
            })
            if len(evidence) >= limit:
                break
    return evidence


def _collect_platform_context(repo_root: Path, repository_paths) -> dict:
    declarations = []
    values = []
    for path in repository_paths:
        if path.suffix.lower() not in {".gradle", ".kts"}:
            continue
        content = _read(path, max_chars=300_000)
        for pattern in ANDROID_MIN_SDK_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                value = int(match.group(1))
                values.append(value)
                declarations.append({
                    "path": path.relative_to(repo_root).as_posix(),
                    "min_sdk": value,
                    "evidence": match.group(0),
                })
    return {
        "android_min_sdk": min(values) if values else None,
        "min_sdk_declarations": declarations[:20],
        "status": "available" if values else "not_found",
    }


def _collect_android_build_variants(repo_root: Path, repository_paths) -> dict:
    """Resolve literal Android application IDs that can affect runtime behavior."""
    declarations = []
    base_ids = []
    suffixes = []
    base_ids_by_file = {}
    suffixes_by_file = {}
    for path in repository_paths:
        if path.suffix.lower() not in {".gradle", ".kts"}:
            continue
        content = _read(path, max_chars=300_000)
        if not content:
            continue
        relative = path.relative_to(repo_root).as_posix()
        for match in ANDROID_APPLICATION_ID_PATTERN.finditer(content):
            value = match.group(1)
            base_ids.append(value)
            base_ids_by_file.setdefault(relative, []).append(value)
            declarations.append({
                "kind": "application_id", "value": value,
                "source_file_path": relative,
                "line": content.count("\n", 0, match.start()) + 1,
                "evidence": match.group(0),
            })
        for match in ANDROID_APPLICATION_ID_SUFFIX_PATTERN.finditer(content):
            value = match.group(1)
            suffixes.append(value)
            suffixes_by_file.setdefault(relative, []).append(value)
            declarations.append({
                "kind": "application_id_suffix", "value": value,
                "source_file_path": relative,
                "line": content.count("\n", 0, match.start()) + 1,
                "evidence": match.group(0),
            })
    variants = list(dict.fromkeys(base_ids))
    resolved_pairs = []
    unresolved_suffixes = []
    for relative, file_suffixes in suffixes_by_file.items():
        file_bases = base_ids_by_file.get(relative, [])
        if not file_bases and len(set(base_ids)) == 1:
            file_bases = list(dict.fromkeys(base_ids))
        if not file_bases:
            unresolved_suffixes.extend(file_suffixes)
            continue
        for base in file_bases:
            for suffix in file_suffixes:
                variant = f"{base}{suffix}"
                if variant not in variants:
                    variants.append(variant)
                resolved_pairs.append({
                    "base": base, "suffix": suffix, "variant": variant,
                    "source_file_path": relative,
                })
    return {
        "status": "available" if declarations else "not_found",
        "known_application_ids": variants,
        "declarations": declarations[:30],
        "resolved_variant_pairs": resolved_pairs,
        "unresolved_suffixes": list(dict.fromkeys(unresolved_suffixes)),
        "literal_resolution_complete": bool(base_ids) and not unresolved_suffixes,
    }


def _collect_historical_android_variants(
    repo_root: Path, max_tags: int = 24,
) -> dict:
    """Collect bounded application-ID evidence from repository release tags."""
    try:
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"], cwd=repo_root,
            capture_output=True, text=True, timeout=5,
        )
        history_scope = (
            "shallow" if shallow.returncode == 0
            and shallow.stdout.strip() == "true" else "full"
        )
        tags_result = subprocess.run(
            ["git", "tag", "--sort=creatordate"], cwd=repo_root,
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "status": "unavailable", "history_scope": "unknown",
            "sampled_tags": [], "known_application_ids": [],
            "declarations": [],
        }
    tags = tags_result.stdout.splitlines() if tags_result.returncode == 0 else []
    if len(tags) > max_tags:
        half = max_tags // 2
        tags = tags[:half] + tags[-half:]
    ids = []
    declarations = []
    if not tags:
        return {
            "status": "no_tags", "history_scope": history_scope,
            "sampled_tags": [], "known_application_ids": [],
            "declarations": [], "bounded": True, "max_tags": max_tags,
        }
    try:
        result = subprocess.run(
            [
                "git", "grep", "-E",
                "applicationId|applicationIdSuffix|<manifest",
                *tags, "--", "*.gradle", "*.gradle.kts",
                "*AndroidManifest.xml",
            ],
            cwd=repo_root, capture_output=True, text=True, timeout=8,
        )
        combined = result.stdout if result.returncode in {0, 1} else ""
        scan_status = "available"
    except subprocess.TimeoutExpired:
        combined = ""
        scan_status = "history_scan_timeout"
    except OSError:
        combined = ""
        scan_status = "unavailable"
    by_tag = {tag: [] for tag in tags}
    for line in combined.splitlines():
        tag = line.split(":", 1)[0]
        if tag in by_tag:
            by_tag[tag].append(line)
    for tag, lines in by_tag.items():
        text = "\n".join(lines)
        bases = ANDROID_APPLICATION_ID_PATTERN.findall(text)
        suffixes = ANDROID_APPLICATION_ID_SUFFIX_PATTERN.findall(text)
        # Before applicationId existed, the manifest package was the runtime ID.
        if not bases:
            bases = re.findall(
                r"<manifest[^>]*\bpackage\s*=\s*[\"']([^\"']+)[\"']",
                text,
            )
        tag_ids = list(dict.fromkeys(bases))
        for base in bases:
            tag_ids.extend(f"{base}{suffix}" for suffix in suffixes)
        for value in dict.fromkeys(tag_ids):
            if value not in ids:
                ids.append(value)
            declarations.append({"tag": tag, "value": value})
    return {
        "status": scan_status,
        "history_scope": history_scope,
        "sampled_tags": tags,
        "known_application_ids": ids,
        "declarations": declarations[:80],
        "bounded": True,
        "max_tags": max_tags,
    }


def _java_byte_array_content_hash(data: bytes) -> int:
    """Match java.util.Arrays.hashCode(byte[]) / Kotlin contentHashCode()."""
    result = 1
    for raw in data:
        signed = raw if raw < 128 else raw - 256
        result = (31 * result + signed) & 0xFFFFFFFF
    return result if result < 0x80000000 else result - 0x100000000


def _evaluate_digest_discriminator_inputs(
    evidence_text: str, build_variants: dict, constants: list[dict],
) -> list[dict]:
    algorithm_match = re.search(
        r"getInstance\s*\(\s*[\"'](MD5|SHA-?1)[\"']", evidence_text,
        re.IGNORECASE,
    )
    if not algorithm_match or "contentHashCode" not in evidence_text:
        return []
    normalized = algorithm_match.group(1).replace("-", "").lower()
    digest_name = {"md5": "md5", "sha1": "sha1"}.get(normalized)
    if not digest_name:
        return []
    numeric_constants = {}
    for item in constants:
        expression = str(item.get("value_expression", "")).strip()
        if re.fullmatch(r"-?\d+", expression):
            numeric_constants.setdefault(int(expression), []).append(item["symbol"])
    mappings = []
    current_ids = build_variants.get("known_application_ids", [])
    historical_ids = build_variants.get("historical_application_ids", [])
    for value in dict.fromkeys([*current_ids, *historical_ids]):
        digest = hashlib.new(digest_name, value.encode("utf-8")).digest()
        projected = _java_byte_array_content_hash(digest)
        mappings.append({
            "input": value,
            "input_scope": "current" if value in current_ids else "historical",
            "algorithm": algorithm_match.group(1).upper(),
            "projection": "java_byte_array_content_hash",
            "projected_value": projected,
            "matching_constants": numeric_constants.get(projected, []),
            "resolution": (
                "matched_declared_constant"
                if numeric_constants.get(projected) else "no_declared_constant"
            ),
        })
    return mappings


def _collect_behavioral_discriminator_context(
    repo_root: Path, repository_paths, source_cache: dict[Path, str],
    evidence_text: str, source_file: Path, build_variants: dict,
) -> dict:
    """Collect constants and duplicate code used by local hash discriminators."""
    discriminator_signals = {
        "package_identity": bool(re.search(
            r"\b(?:packageName|applicationId|BuildConfig\.[A-Z_]*)\b",
            evidence_text,
        )),
        "integer_projection": bool(re.search(
            r"\b(?:contentHashCode|hashCode)\s*\(", evidence_text
        )),
        "branch_or_configuration": bool(re.search(
            r"\b(?:when|switch|if)\b|\bTYPE\w*\b|\bSIZE\w*\b",
            evidence_text,
        )),
        "message_digest": bool(re.search(
            r"\bMessageDigest\b|\b(?:MD5|SHA-?1)\b", evidence_text,
            re.IGNORECASE,
        )),
    }
    supported = all(discriminator_signals.values())
    historical_variants = (
        _collect_historical_android_variants(repo_root) if supported else {
            "status": "not_requested", "history_scope": "unknown",
            "sampled_tags": [], "known_application_ids": [],
            "declarations": [],
        }
    )
    build_variants["historical_evidence"] = historical_variants
    build_variants["historical_application_ids"] = [
        value for value in historical_variants.get("known_application_ids", [])
        if value not in build_variants.get("known_application_ids", [])
    ]
    identifiers = sorted(set(re.findall(
        r"\b(?:TYPE|SIZE|MODE|VARIANT|FLAVOR)[A-Z0-9_]*\b", evidence_text
    )))[:80]
    constants = []
    equivalent = []
    tests = []
    production_contract_text = []
    test_contract_text = []
    target_relative = source_file.relative_to(repo_root).as_posix()
    target_class_name = source_file.stem
    for path in repository_paths:
        if not _eligible(path, SOURCE_SUFFIXES):
            continue
        content = source_cache.get(path, "") or _read(path)
        if not content:
            continue
        relative = path.relative_to(repo_root).as_posix()
        for identifier in identifiers:
            match = re.search(
                rf"\b(?:const\s+val|static\s+final\s+\w+|val|int)\s+"
                rf"{re.escape(identifier)}\s*(?::\s*\w+)?\s*=\s*([^\n;,}}]+)",
                content,
            )
            if match:
                constants.append({
                    "symbol": identifier, "value_expression": match.group(1).strip(),
                    "source_file_path": relative,
                    "line": content.count("\n", 0, match.start()) + 1,
                })
        if any(
            item["source_file_path"] == relative for item in constants
        ):
            for match in re.finditer(
                r"\b(?:const\s+val|static\s+final\s+\w+|val|int)\s+"
                r"((?:TYPE|SIZE|MODE|VARIANT|FLAVOR)[A-Z0-9_]*)"
                r"\s*(?::\s*\w+)?\s*=\s*([^\n;,}]+)",
                content,
            ):
                constants.append({
                    "symbol": match.group(1),
                    "value_expression": match.group(2).strip(),
                    "source_file_path": relative,
                    "line": content.count("\n", 0, match.start()) + 1,
                })
        structurally_equivalent = (
            relative != target_relative
            and re.search(r"\bMessageDigest\b", content)
            and re.search(r"\b(?:contentHashCode|hashCode)\s*\(", content)
            and re.search(r"\bpackageName\b", content)
        )
        if structurally_equivalent:
            equivalent.append({
                "source_file_path": relative,
                "reason": "same package identity -> MessageDigest -> integer discriminator shape",
            })
        if relative == target_relative or structurally_equivalent:
            production_contract_text.append(content)
        if "test" in relative.lower() and (
            any(identifier in content for identifier in identifiers)
            or re.search(r"calculate(?:Child|Graph)Type", content)
            or target_class_name in content
        ):
            tests.append({
                "source_file_path": relative,
                "reason": (
                    "direct target-class test" if target_class_name in content
                    else "covers discriminator constants or behavior"
                ),
            })
            test_contract_text.append(content)
    unique_constants = list({
        (item["symbol"], item["source_file_path"], item["line"]): item
        for item in constants
    }.values())
    input_mappings = _evaluate_digest_discriminator_inputs(
        evidence_text, build_variants, unique_constants
    )
    production_corpus = "\n".join(production_contract_text)
    test_corpus = "\n".join(test_contract_text)
    constant_roles = []
    for item in unique_constants:
        symbol = item["symbol"]
        accepted = bool(re.search(
            rf"(?:==\s*{re.escape(symbol)}\b|\b{re.escape(symbol)}\s*==|"
            rf"\b{re.escape(symbol)}\s*(?:,\s*\w+\s*)?->)",
            production_corpus,
        ))
        negative_test = any(
            re.search(rf"\b{re.escape(symbol)}\b", line)
            and re.search(r"\bSIZE_MIN\b", line)
            for line in test_corpus.splitlines()
        )
        mapped_inputs = [
            mapping["input"] for mapping in input_mappings
            if symbol in mapping["matching_constants"]
        ]
        constant_roles.append({
            "symbol": symbol,
            "used_as_accepted_branch": accepted,
            "covered_as_fallback_or_negative": negative_test,
            "mapped_known_inputs": mapped_inputs,
            "mapping_status": (
                "mapped_known_input" if mapped_inputs
                else "unmapped_accepted_branch" if accepted
                else "non_accepted_or_output_constant"
            ),
        })
    current_mappings = [
        item for item in input_mappings if item.get("input_scope") == "current"
    ]
    historical_mappings = [
        item for item in input_mappings
        if item.get("input_scope") == "historical"
    ]
    current_coverage_complete = bool(current_mappings) and all(
        item["resolution"] == "matched_declared_constant"
        for item in current_mappings
    )
    historical_coverage_complete = (
        historical_variants.get("status") == "available"
        and
        historical_variants.get("history_scope") == "full"
        and historical_variants.get("bounded") is False
        and all(item["resolution"] == "matched_declared_constant"
                for item in historical_mappings)
    )
    return {
        "classification": "runtime_discriminator" if supported else "not_established",
        "supported": supported,
        "signals": discriminator_signals,
        "constants": unique_constants[:80],
        "equivalent_implementations": equivalent[:30],
        "relevant_tests": tests[:30],
        "evaluated_input_mappings": input_mappings,
        "constant_roles": constant_roles,
        "mapping_coverage": {
            "current_variants": (
                "complete" if current_coverage_complete else "incomplete"
            ),
            "historical_variants": (
                "complete" if historical_coverage_complete else "unresolved"
            ),
            "history_scope": historical_variants.get(
                "history_scope", "unknown"
            ),
            "sampled_tag_count": len(
                historical_variants.get("sampled_tags", [])
            ),
        },
        "unmapped_accepted_constants": [
            item["symbol"] for item in constant_roles
            if item["mapping_status"] == "unmapped_accepted_branch"
        ],
        "unresolved_constants": [
            identifier for identifier in identifiers
            if not any(item["symbol"] == identifier for item in unique_constants)
        ],
        "explanation": (
            "The digest is projected to a local scalar and used to choose runtime "
            "configuration from application/build identity; it is not thereby a cache key."
            if supported else
            "The bounded source does not establish all runtime-discriminator signals."
        ),
    }


def _collect_representation_lifetime(repo_root: Path, repository_paths,
                                     source_cache: dict[Path, str],
                                     classification_records: list[dict]) -> dict:
    corpus = "\n".join(record["text"] for record in classification_records)
    memory_patterns = (
        r"\bSealedObject\b", r"\bmemory(?:Key|Iv)\b",
        r"\bin[-_ ]memory\b",
    )
    memory_evidence = [
        pattern for pattern in memory_patterns
        if re.search(pattern, corpus, re.IGNORECASE)
    ]
    relevant_paths = {
        str(record.get("path", "")).replace("\\", "/")
        for record in classification_records
        if record.get("path")
    }
    sinks = []
    for path in repository_paths:
        if not _eligible(path, SOURCE_SUFFIXES):
            continue
        relative = path.relative_to(repo_root).as_posix()
        if relative not in relevant_paths:
            continue
        content = source_cache.get(path, "") or _read(path)
        if not content:
            continue
        for pattern in REAL_PERSISTENCE_SINKS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                line = content.count("\n", 0, match.start()) + 1
                sinks.append({
                    "path": relative,
                    "line": line,
                    "sink": match.group(0),
                })
                if len(sinks) >= 20:
                    break
            if len(sinks) >= 20:
                break
        if len(sinks) >= 20:
            break
    supported = bool(memory_evidence) and not sinks
    return {
        "classification": "ephemeral_sensitive" if supported else "unresolved",
        "supported": supported,
        "memory_evidence_patterns": memory_evidence,
        "real_persistence_sinks": sinks,
        "reviewed_flow_files": sorted(relevant_paths),
        "legacy_compatibility_required": False if supported else None,
        "explanation": (
            "Sensitive representation is bounded to in-memory wrappers and no "
            "concrete serialization sink was found. Serializable declarations "
            "or surrounding file I/O alone are not persistence evidence."
            if supported else
            "The bounded scan could not prove that the protected representation "
            "is both in-memory and free of concrete serialization sinks."
        ),
    }


def _assess_ephemeral_runtime_discriminator(
    value_usage_graph: dict,
    call_sites: list[dict],
    repository_index_metadata: dict,
    max_call_sites: int,
) -> dict:
    """Recognize internal, comparison-only digest values with no durable sink."""
    sink_assessment = value_usage_graph.get("sink_assessment", {}) or {}
    observed = set(sink_assessment.get("observed", []))
    blocking_sinks = sorted(observed.intersection({
        "persistence", "network_or_protocol", "identity_or_security", "cache",
    }))
    comparisons = value_usage_graph.get("comparison_evidence", []) or []
    repository_complete = not bool(repository_index_metadata.get("truncated"))
    caller_scan_complete = len(call_sites) < max_call_sites
    supported = bool(
        call_sites and comparisons and repository_complete
        and caller_scan_complete and not blocking_sinks
    )
    return {
        "classification": (
            "ephemeral_runtime_discriminator" if supported else "not_established"
        ),
        "supported": supported,
        "call_site_count": len(call_sites),
        "caller_scan_complete": caller_scan_complete,
        "repository_index_complete": repository_complete,
        "comparison_evidence": comparisons,
        "blocking_sinks": blocking_sinks,
        "non_blocking_sinks": sorted(observed - set(blocking_sinks)),
        "explanation": (
            "The derived value is compared during runtime and the complete bounded "
            "repository scan found no persistence, cache, identity/security, or "
            "network/protocol sink."
            if supported else
            "The bounded graph did not establish comparison-only ephemeral use."
        ),
    }


# The types a method holds values of: what it constructs, the declared type of
# its locals and parameters, and a Kotlin property's annotated type.
_METHOD_TYPE_USES = (
    re.compile(r"\bnew\s+([A-Z][A-Za-z0-9_]*)\s*(?:<[^;{}()]*>)?\s*[({]"),
    re.compile(
        r"\b([A-Z][A-Za-z0-9_]*)(?:<[^;{}()=]*>)?(?:\[\])*\s+"
        r"[a-z_][A-Za-z0-9_]*\s*(?:=|;|:|,|\))"
    ),
    re.compile(r"\b(?:val|var)\s+[a-z_][A-Za-z0-9_]*\s*:\s*([A-Z][A-Za-z0-9_]*)"),
)
MAX_METHOD_TYPE_SOURCES = 8


def _method_type_declarations(repo_root: Path, source_file: Path,
                              class_name: str, method_body: str,
                              repository_paths) -> list[dict]:
    """Repository files declaring the types the reported method holds values of.

    ru.valle.btc's Transaction(byte[], boolean) closes a BitcoinInputStream,
    a class of that repository extending ByteArrayInputStream without
    overriding close(). The adjudication (run_20260915_175048) abstained
    because nothing established the receiver's runtime type: the packet held
    Transaction.java and its test, while BitcoinInputStream.java -- the one
    line that settled it -- was only an edge in a constructor graph. Whether
    a receiver is a subtype of a type the rule names, and whether it overrides
    the call, is written in the declaration of the receiver's type, so those
    declarations join the context. Test sources and the reported class itself
    are left out.
    """
    names: list[str] = []
    for pattern in _METHOD_TYPE_USES:
        for match in pattern.finditer(method_body or ""):
            if match.group(1) not in names:
                names.append(match.group(1))
    own = str(class_name or "").rsplit(".", 1)[-1].split("$")[0]
    names = [name for name in names if name != own]
    if not names:
        return []
    index: dict[str, list[Path]] = {}
    for raw in repository_paths or ():
        path = Path(raw)
        if _eligible(path, SOURCE_SUFFIXES):
            index.setdefault(path.stem, []).append(path)
    target = source_file.resolve()
    declarations = []
    for name in names:
        for path in sorted(index.get(name, [])):
            try:
                if path.resolve() == target:
                    continue
                relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
            except (OSError, ValueError):
                continue
            if re.search(r"(?:^|/)src/(?:test|androidTest)/", relative):
                continue
            declarations.append({"path": relative, "type": name})
            if len(declarations) >= MAX_METHOD_TYPE_SOURCES:
                return declarations
    return declarations


def _is_consumer_source(path: str) -> bool:
    """A Java or Kotlin source; a Gradle script (*.gradle.kts) is build
    configuration, never a consumer of a value the target produces."""
    name = Path(path).name.lower()
    return name.endswith((".java", ".kt")) or (
        name.endswith(".kts") and not name.endswith(".gradle.kts"))


def _promote_flow_sources(repo_root: Path, source_file: Path,
                          source_cache: dict[Path, str], context: dict,
                          max_exact_chars: int) -> None:
    """Put transitive consumers/tests into the exact LLM source corpus."""
    impact = context.get("multi_file_impact", {})
    if not isinstance(impact, dict) or impact.get("status") != "available":
        return
    target = source_file.relative_to(repo_root).as_posix()
    # Only files reached from symbols discovered inside the target are hard
    # scope requirements. Repository-wide textual matches, lifetime review
    # files, and tests remain useful context but can refer to unrelated APIs.
    root_symbols = set(context.get("target_flow_symbols", []))
    policy = context.get("case_migration_policy", {})
    signature_preserving_consumers = (
        policy.get("consumer_scope")
        == "signature_preserving_internal_transport_change"
    )
    policy_resolution = context.get("policy_consumer_resolution", {})
    policy_sources = list(dict.fromkeys(
        str(item.get("source_file_path", ""))
        for item in policy_resolution.get("resolved", [])
        if item.get("source_file_path")
    ))
    discriminator = context.get("runtime_discriminator_assessment", {})
    behavioral_sources = []
    if discriminator.get("supported") is True:
        behavioral_sources.extend(
            item.get("source_file_path", "")
            for item in discriminator.get("constants", [])
        )
        behavioral_sources.extend(
            item.get("source_file_path", "")
            for item in discriminator.get("equivalent_implementations", [])
        )
        behavioral_sources.extend(
            item.get("source_file_path", "")
            for item in discriminator.get("relevant_tests", [])
        )
        behavioral_sources.extend(
            item.get("source_file_path", "")
            for item in context.get("android_build_variants", {}).get(
                "declarations", []
            )
        )
        # Resource declarations are supporting evidence, not mandatory edit
        # scope merely because a transitive consumer mentions R.string. A
        # resource becomes required through the normal caller/declaration graph
        # only when the planned change actually edits that resource contract.
    behavioral_sources = list(dict.fromkeys(
        path for path in behavioral_sources if path
    ))
    # The files that call the target: the semantically resolved callers, or
    # the lexical call sites when the index did not run (the target-shaped
    # ones multi_file_context listed in call_site_files). A caller migration
    # edits exactly these, so they rank right after the policy sources and
    # never compete for the budget, within the group bounds of
    # admit_required_callers (MAX_REQUIRED_CALLER_CHARS bounds each).
    from cognalys.extraction.multi_file_context import GENERIC_SYMBOLS

    semantic = impact.get("semantic_resolution", {}) or {}
    method = str((impact.get("target") or {}).get("method", ""))
    if "call_site_files" in impact:
        caller_sources = [str(path) for path in impact.get("call_site_files") or []]
    elif semantic.get("status") == "available":
        caller_sources = [str(item.get("path", ""))
                          for item in semantic.get("resolved_callers", [])]
    elif method and method.lower() not in GENERIC_SYMBOLS:
        caller_sources = [str(item.get("path", ""))
                          for item in context.get("repository_call_sites", [])]
    else:
        caller_sources = []
    caller_sources = list(dict.fromkeys(
        path for path in [*caller_sources, *(impact.get("required_caller_sources") or [])]
        if path and path != target
    ))
    required = [target, *policy_sources, *caller_sources, *behavioral_sources]
    if not signature_preserving_consumers:
        # Depth-1 consumers of a symbol the target produces, source files
        # only: a Gradle script that happens to contain the word is never a
        # consumer. Ranked by how many root symbols they use, so a budget
        # cuts the weakest first.
        consumers: dict[str, int] = {}
        for item in context.get("transitive_consumer_evidence", []):
            path = str(item.get("path", ""))
            if (not path or int(item.get("depth", 99)) != 1
                    or not _is_consumer_source(path)):
                continue
            matched = root_symbols.intersection(item.get("matched_symbols", []))
            if matched:
                consumers[path] = consumers.get(path, 0) + len(matched)
        required.extend(sorted(consumers, key=lambda path: -consumers[path]))
    supplementary = []
    # The declarations of the types the reported method holds values of come
    # first: they are short, and they alone say whether a receiver is a
    # subtype of a type the rule names (see _method_type_declarations).
    supplementary.extend(
        str(item.get("path", ""))
        for item in context.get("method_type_declarations", [])
        if item.get("path")
    )
    # Tests are the highest-value supplementary source when policy requires a
    # signature-preserving internal change: callers do not need full-file edit
    # context, while a concrete test seam does.
    supplementary.extend(
        str(item.get("path", "")) for item in context.get("tests", [])
        if item.get("path") and int(item.get("relevance_score", 0)) >= 60
    )
    supplementary.extend(
        str(item.get("path", "")) for item in context.get("repository_call_sites", [])
        if item.get("path")
    )
    supplementary.extend(
        str(item.get("path", ""))
        for item in context.get("class_construction_sites", [])
        if item.get("path")
    )
    supplementary.extend(
        str(path) for path in context.get(
            "representation_lifetime_assessment", {}
        ).get("reviewed_flow_files", [])
    )
    existing = impact.get("exact_candidate_sources", [])
    weak_test_paths = {
        str(item.get("path", "")) for item in context.get("tests", [])
        if item.get("path") and item.get("edit_target_recommended") is False
    }
    supplementary.extend(
        str(item.get("source_file_path", "")) for item in existing
        if (isinstance(item, dict) and item.get("source_file_path")
            and str(item.get("source_file_path", "")) not in weak_test_paths)
    )
    required = list(dict.fromkeys(path for path in required if path))
    ordered = required + [path for path in dict.fromkeys(supplementary)
                          if path and path not in required]
    # Every promoted file used to be stamped with the same sentence, "target
    # or transitive protected-value flow", so a packet could not distinguish
    # the reported class from a file that merely shares a symbol name with it:
    # markor's TodoTxtFilter and TextCasingUtils arrived under the same label
    # as JavaPasswordbasedCryption itself. The collectors above already record
    # why each path was selected, so carry that through rather than
    # overwriting it, and keep the old sentence only as the fallback for a
    # path no collector explained.
    reasons_by_path: dict[str, list[str]] = {}

    def note(path: str, *reasons: str) -> None:
        if not path:
            return
        bucket = reasons_by_path.setdefault(path, [])
        for reason in reasons:
            reason = str(reason).strip()
            if reason and reason not in bucket:
                bucket.append(reason)

    note(target, "reported finding location")
    for item in context.get("method_type_declarations", []):
        note(str(item.get("path", "")),
             f"declares {item.get('type', '')}, a type the reported method "
             "holds a value of")
    for path in policy_sources:
        note(path, "resolved policy consumer")
    for path in behavioral_sources:
        note(path, "behavioral discriminator evidence")
    for item in context.get("transitive_consumer_evidence", []):
        note(str(item.get("path", "")),
             "transitive consumer of a symbol produced at the target")
    for item in context.get("repository_call_sites", []):
        note(str(item.get("path", "")), "call site of the target method")
    for item in context.get("class_construction_sites", []):
        note(str(item.get("path", "")),
             "constructs the reported class, deciding the state it reads")
    for item in context.get("tests", []):
        note(str(item.get("path", "")),
             "existing test: " + str(item.get("relevance", "")))
    for path in context.get(
        "representation_lifetime_assessment", {}
    ).get("reviewed_flow_files", []):
        note(str(path), "reviewed protected-value lifetime flow")
    for item in existing:
        if isinstance(item, dict):
            note(str(item.get("source_file_path", "")),
                 *(item.get("relevance") or []))
    from cognalys.extraction.multi_file_context import (
        MAX_REQUIRED_CALLER_CHARS, REQUIRED_CALLER_RELEVANCE, admit_required_callers,
    )

    caller_contents = {}
    for relative in caller_sources:
        path = repo_root / relative
        content = source_cache.get(path, "")
        if not content and path.is_file():
            content = _read(path, max_chars=MAX_REQUIRED_CALLER_CHARS)
        caller_contents[relative] = content
    # caller_sources is already ranked (call_site_files); a descending weight
    # keeps that order inside each module.
    counts = {path: index for index, path in enumerate(reversed(caller_sources))}
    admitted_callers = set(admit_required_callers(caller_contents, target, counts)[0])
    exact_sources = []
    total_chars = 0
    omitted = []
    for relative in ordered:
        path = repo_root / relative
        content = source_cache.get(path, "")
        if relative in caller_sources:
            if relative not in admitted_callers:
                omitted.append(relative)
                continue
            content = caller_contents[relative]
            exact_sources.append({
                "source_file_path": relative,
                "relevance": list(dict.fromkeys([
                    *(reasons_by_path.get(relative) or []), REQUIRED_CALLER_RELEVANCE])),
                "content": content,
            })
            continue
        if not content and path.is_file():
            content = _read(path, max_chars=80_000)
        if not content or total_chars + len(content) > max_exact_chars:
            omitted.append(relative)
            continue
        exact_sources.append({
            "source_file_path": relative,
            "relevance": reasons_by_path.get(relative)
            or ["target or transitive protected-value flow"],
            "content": content,
        })
        total_chars += len(content)
    # The flow-scored files above compete for max_exact_chars in priority
    # order, so a large target file (or several required files) can starve
    # everything after it. A test-source-root anchor is cheap and bounded by
    # design (multi_file_context.TEST_SOURCE_ROOT_ANCHOR_CHARS), and losing
    # it here means a downstream patch plan can never be told a test file is
    # allowed at all, so it is re-added even when the ordinary competition
    # above dropped it.
    try:
        from cognalys.extraction.multi_file_context import _test_source_root_anchor

        test_anchor = _test_source_root_anchor(
            repo_root, list(source_cache), source_cache,
            covered_paths={item["source_file_path"] for item in exact_sources},
            exclude_paths=weak_test_paths,
        )
    except Exception as exc:  # noqa: BLE001 -- the anchor is optional evidence
        _LOG.warning("test source root anchor not resolved: %s", exc)
        test_anchor = None
    if test_anchor:
        exact_sources.append(test_anchor)
        if test_anchor["source_file_path"] in omitted:
            omitted.remove(test_anchor["source_file_path"])

    impact["exact_candidate_sources"] = exact_sources
    impact["promoted_flow_sources"] = ordered
    impact["required_flow_sources"] = required
    impact["required_policy_sources"] = policy_sources
    impact["required_behavioral_sources"] = behavioral_sources
    impact["unresolved_policy_consumers"] = policy_resolution.get("unresolved", [])
    impact["supplementary_context_sources"] = [
        path for path in ordered if path not in required
    ]
    impact["omitted_promoted_sources"] = omitted
    impact["coverage_requirements"]["flow_consumer_files"] = sorted(
        set(required) - {target}
    )
    impact["coverage_requirements"]["behavioral_contract_files"] = sorted(
        set(behavioral_sources)
    )
    supplied_callers = [item["source_file_path"] for item in exact_sources
                        if item["source_file_path"] in caller_sources]
    impact["required_caller_sources"] = supplied_callers
    impact["omitted_required_caller_sources"] = [
        path for path in caller_sources if path not in supplied_callers]
    impact["coverage_requirements"]["caller_files"] = sorted(
        set(impact["coverage_requirements"].get("caller_files") or []) | set(supplied_callers))
    omitted_required = [path for path in omitted if path in required]
    impact["omitted_required_flow_sources"] = omitted_required
    if omitted_required:
        completeness = impact.setdefault("scope_completeness", {})
        completeness["status"] = "incomplete"
        reasons = completeness.setdefault("reasons", [])
        reason = "required flow exact sources exceeded the context budget"
        if reason not in reasons:
            reasons.append(reason)
    unresolved_policy = policy_resolution.get("unresolved", [])
    if unresolved_policy:
        completeness = impact.setdefault("scope_completeness", {})
        completeness["status"] = "incomplete"
        reasons = completeness.setdefault("reasons", [])
        reason = "migration policy has unresolved required consumers"
        if reason not in reasons:
            reasons.append(reason)


def _collect_cache_lifecycle(repo_root: Path, seed_paths,
                             max_depth: int = 4, max_files: int = 24,
                             source_cache: dict[Path, str] | None = None) -> dict:
    """Follow local wrapper types and prove a cache miss/fallback/write cycle.

    This is intentionally a bounded source graph, not a project-specific list
    of classes. Edges come from constructor expressions in each reachable file.
    """
    source_cache = source_cache or {}
    source_paths = list(source_cache) or [
        path for path in _repository_files(repo_root) if _eligible(path, SOURCE_SUFFIXES)
    ]
    class_index = {}
    for path in source_paths:
        class_index.setdefault(path.stem, []).append(path)

    frontier = []
    # Preserve caller order. Direct call sites must be explored before broader
    # textual consumers; sorting or using an unordered set can exhaust the
    # bounded file budget in unrelated parts of a large repository.
    ordered_seeds = list(dict.fromkeys(seed_paths))
    for relative in ordered_seeds:
        candidate = repo_root / relative
        if candidate.is_file() and _eligible(candidate, SOURCE_SUFFIXES):
            frontier.append((candidate, 0))

    visited = set()
    edges = []
    reachable = []
    while frontier and len(reachable) < max_files:
        path, depth = frontier.pop(0)
        resolved = path.resolve()
        if resolved in visited:
            continue
        visited.add(resolved)
        reachable.append(path)
        if depth >= max_depth:
            continue
        content = source_cache.get(path, "") or _read(path)
        for type_name in sorted(set(re.findall(
                r"\bnew\s+([A-Z][A-Za-z0-9_]*)\s*(?:<[^;{}()]*>)?\s*\(",
                content))):
            for target in class_index.get(type_name, []):
                edges.append({
                    "from": path.relative_to(repo_root).as_posix(),
                    "to": target.relative_to(repo_root).as_posix(),
                    "type": type_name,
                    "depth": depth + 1,
                })
                if target.resolve() not in visited:
                    frontier.append((target, depth + 1))

    patterns = {
        "local_read": (
            r"\b(?:getById|getAll|getReadableDatabase|read|load|exists)\s*\(",
        ),
        "miss_or_stale": (
            r"\b(?:result|value|entry|file|cached\w*|db\w*)\s*==\s*null\b",
            r"\b(?:result|value|entry|file|cached\w*|db\w*)\s*!=\s*null\b",
            r"\bisEmpty\s*\(\s*\)",
            r"!\s*[^;\n]*\.exists\s*\(\s*\)",
        ),
        "fallback_source": (
            r"\b(?:alternate|fallback|remote|network|api)\w*\.\s*"
            r"(?:performRequest|fetch|request|load|get)\s*\(",
        ),
        "repopulation": (
            r"\b(?:performWrite|putAll|save|write)\s*\(\s*(?:result|results|value|values)",
        ),
        "persistent_storage": (
            r"\bextends\s+SQLiteOpenHelper\b",
            r"\b(?:SQLiteDatabase|database|dbFilename|cacheDir)\b",
        ),
    }
    evidence = {
        name: [item for path in reachable
               for item in _pattern_evidence(
                   path, repo_root, expressions, content=source_cache.get(path)
               )][:12]
        for name, expressions in patterns.items()
    }
    required = {
        "local_read": bool(evidence["local_read"]),
        "miss_or_stale": bool(evidence["miss_or_stale"]),
        "fallback_source": bool(evidence["fallback_source"]),
        "repopulation": bool(evidence["repopulation"]),
        "persistent_storage": bool(evidence["persistent_storage"]),
    }
    flow_signal_names = (
        "local_read", "miss_or_stale", "fallback_source", "repopulation"
    )
    flow_files = [
        {item["path"] for item in evidence[name]}
        for name in flow_signal_names
    ]
    coherent_flow_files = sorted(set.intersection(*flow_files)) if flow_files else []
    return {
        "supported": all(required.values()) and bool(coherent_flow_files),
        "required_signals": required,
        "coherent_flow_files": coherent_flow_files,
        "evidence": evidence,
        "reachable_files": [
            path.relative_to(repo_root).as_posix() for path in reachable
        ],
        "constructor_edges": edges[: max_files * 3],
        "limits": {
            "max_depth": max_depth,
            "max_files": max_files,
            "analysis_kind": "bounded_local_constructor_graph",
        },
    }
def collect_migration_context(
    repo_root: Path,
    source_file: Path,
    method_name: str,
    class_name: str,
    method_body: str,
    max_call_sites: int = 40,
    max_documents: int = 20,
    multi_file_exact_chars: int = 60_000,
    case_migration_policy: dict | None = None,
) -> dict:
    """Collect bounded call-site, consumer, test, and documentation evidence."""
    if not method_name or method_name.startswith("<"):
        call_pattern = None
    else:
        call_pattern = re.compile(rf"\b{re.escape(method_name)}\s*\(")
    class_simple = class_name.rsplit(".", 1)[-1].split("$")[0]
    target_is_private = _target_method_is_private(method_body, method_name)
    algorithm_terms = sorted(set(re.findall(
        r'(?i)["\'](MD5|SHA-?1|SHA-?256|SHA-?384|SHA-?512)["\']',
        method_body,
    )))
    # The reviewed method reads state the constructor was handed, so the
    # arguments at construction are part of what it does. markor's
    # createKeyFromPassword is built from `new JavaPasswordbasedCryption(
    # Build.VERSION.SDK_INT, new SecureRandom())` in Document.java, and
    # because nothing surfaced that line the analysis closed by asking, as
    # missing evidence, whether the supplied Random was a suitable source --
    # a question the repository had already answered in a file the packet was
    # carrying whole.
    construction_pattern = (
        re.compile(r"\bnew\s+" + re.escape(class_simple) + r"\s*\(")
        if class_simple else None
    )
    call_sites = []
    construction_sites = []
    consumer_evidence = []
    tests = []
    documents = []
    category_evidence = {category: [] for category in CATEGORIES}
    flow_symbols = set()
    target_flow_symbols = set()
    try:
        from cognalys.extraction.repository_index import build_repository_index

        repository_index = build_repository_index(repo_root)
        repository_paths = repository_index["paths"]
        source_cache = repository_index["source_cache"]
        repository_index_metadata = repository_index["metadata"]
    except Exception as exc:
        repository_paths = list(_repository_files(repo_root))
        source_cache = {}
        repository_index_metadata = {
            "backend": "legacy_walk",
            "fallback_reason": str(exc),
        }

    for path in repository_paths:
        relative = path.relative_to(repo_root).as_posix()
        if _eligible(path, SOURCE_SUFFIXES):
            content = source_cache.get(path, "") or _read(path)
            if not content:
                continue
            source_cache[path] = content
            lines = content.splitlines()
            is_target = path.resolve() == source_file.resolve()
            for index, line in enumerate(lines):
                lowered = line.lower()
                if (construction_pattern is not None
                        and len(construction_sites) < max_call_sites
                        and construction_pattern.search(line)):
                    construction_sites.append({
                        "path": relative,
                        "line": index + 1,
                        "snippet": _snippet(lines, index, 5),
                    })
                if call_pattern and call_pattern.search(line):
                    # Exclude the declaration in the target method itself.
                    declaration_like = bool(re.search(
                        rf"\b(?:public|private|protected|internal|static|final|suspend|fun)\b.*\b{re.escape(method_name)}\s*\(",
                        line,
                    ))
                    if not (is_target and declaration_like):
                        record = {
                            "path": relative,
                            "line": index + 1,
                            "snippet": _snippet(lines, index, 7),
                        }
                        if len(call_sites) < max_call_sites:
                            call_sites.append(record)
                        consumer_evidence.append(record)
                        discovered = _flow_symbols(lines, index)
                        record["flow_symbols"] = sorted(discovered)
                        flow_symbols.update(discovered)
                        # Symbols produced at resolved/direct target call sites
                        # are the roots of the affected value flow. Previously
                        # this was restricted to calls physically inside the
                        # target file, leaving ordinary cross-file consumers as
                        # supplementary context instead of mandatory scope.
                        target_flow_symbols.update(discovered)
                if any(term.lower() in lowered for term in algorithm_terms):
                    if any(token in lowered for token in (
                        "return", "=", "put", "save", "write", "compare",
                        "equals", "header", "request", "response", "file",
                    )) and len(consumer_evidence) < max_call_sites * 2:
                        consumer_evidence.append({
                            "path": relative,
                            "line": index + 1,
                            "snippet": _snippet(lines, index, 5),
                        })
            if ("test" in relative.lower() or path.name.lower().endswith("test.java")
                    or path.name.lower().endswith("test.kt")):
                has_method = bool(call_pattern and call_pattern.search(content))
                has_class = bool(class_simple and class_simple in content)
                has_algorithm = any(term in content for term in algorithm_terms)
                # Generic names cannot identify a relevant test by themselves,
                # while a class-only match is useful context but not evidence
                # that the file is a safe insertion point for new tests.
                generic_method = method_name.lower() in {
                    "build", "create", "get", "set", "read", "write",
                    "open", "close", "update", "run", "execute",
                }
                evidence = []
                score = 0
                if has_method and has_class:
                    evidence.append("target class and method")
                    score += 80
                elif has_method and not generic_method:
                    evidence.append("distinctive target method")
                    score += 60
                elif has_class and target_is_private:
                    # The target cannot be named from a test, so exercising
                    # its class is the strongest available coupling; see
                    # _target_method_is_private. Whether that is worth
                    # promoting is decided after the scan -- a class every
                    # test mentions distinguishes none of them.
                    evidence.append(CLASS_ONLY_PRIVATE_RELEVANCE)
                    score += 60
                elif has_class:
                    evidence.append("target class only")
                    score += 20
                if has_algorithm:
                    evidence.append("finding algorithm literal")
                    score += 45
                if score:
                    tests.append({
                        "path": relative,
                        "relevance": ", ".join(evidence),
                        "relevance_score": score,
                        "edit_target_recommended": score >= 60,
                    })
        elif _eligible(path, TEXT_SUFFIXES) and len(documents) < max_documents:
            content = _read(path, max_chars=250_000)
            if not content:
                continue
            lowered = content.lower()
            terms = [method_name.lower(), class_simple.lower(), *[term.lower() for term in algorithm_terms]]
            matched = [term for term in terms if term and term in lowered]
            if matched:
                documents.append({
                    "path": relative,
                    "matched_terms": matched[:8],
                })

    # A private target cannot be named by any test, so exercising its class is
    # the strongest coupling a test can have -- when one test has it. markor
    # had exactly one, JavaPasswordbasedCryptionTest, and promoting it was
    # right. Element Android's target is a private method of MainActivity, a
    # name seven androidTest files mention merely because they launch the app;
    # all seven were promoted, became edit targets, and spent 49k of the
    # packet's 140k character budget while the helper the adjudication
    # actually turned on never reached it. A signal shared by many candidates
    # identifies none of them -- the same rule multi_file_context applies to
    # ubiquitous symbols.
    class_only = [
        item for item in tests
        if CLASS_ONLY_PRIVATE_RELEVANCE in item.get("relevance", "")
    ]
    if len(class_only) > 1:
        for item in class_only:
            item["relevance_score"] -= 60
            item["relevance"] = item["relevance"].replace(
                CLASS_ONLY_PRIVATE_RELEVANCE,
                "target class only (shared with "
                f"{len(class_only) - 1} other test(s), so it singles out none)",
            )
            item["edit_target_recommended"] = item["relevance_score"] >= 60

    # Follow one additional repository edge through assigned variables, JSON
    # keys, and enclosing method names discovered at direct call sites.
    transitive_consumers = []
    if flow_symbols:
        direct_locations = {(item["path"], item["line"]) for item in call_sites}
        seen_locations = set(direct_locations)
        seen_symbols = set(flow_symbols)
        frontier = set(flow_symbols)
        for depth in range(1, 3):
            if not frontier or len(transitive_consumers) >= max_call_sites * 2:
                break
            symbol_pattern = re.compile(
                r"\b(?:" + "|".join(
                    re.escape(value) for value in sorted(frontier)
                ) + r")\b"
            )
            next_symbols = set()
            for path in repository_paths:
                if not _eligible(path, SOURCE_SUFFIXES):
                    continue
                content = source_cache.get(path, "") or _read(path)
                if not content:
                    continue
                relative = path.relative_to(repo_root).as_posix()
                lines = content.splitlines()
                for index, line in enumerate(lines):
                    if not symbol_pattern.search(line):
                        continue
                    location = (relative, index + 1)
                    if location in seen_locations:
                        continue
                    seen_locations.add(location)
                    transitive_consumers.append({
                        "path": relative,
                        "line": index + 1,
                        "depth": depth,
                        "matched_symbols": sorted(set(symbol_pattern.findall(line))),
                        "snippet": _snippet(lines, index, 8),
                    })
                    next_symbols.update(_flow_symbols(lines, index))
                    next_symbols.update(re.findall(
                        r"\bnew\s+([A-Za-z_]\w*)\s*\(", line
                    ))
                    if len(transitive_consumers) >= max_call_sites * 2:
                        break
                if len(transitive_consumers) >= max_call_sites * 2:
                    break
            frontier = next_symbols - seen_symbols
            seen_symbols.update(next_symbols)
        flow_symbols = seen_symbols

    # Classify only from the target method and bounded consumer snippets. Do
    # not use unrelated keywords elsewhere in a large source file.
    classification_records = [{
        "path": source_file.relative_to(repo_root).as_posix(),
        "line": 0,
        "text": method_body,
    }]
    classification_records.extend({
        "path": item["path"],
        "line": item["line"],
        "text": item["snippet"],
    } for item in call_sites)
    classification_records.extend({
        "path": item["path"],
        "line": item["line"],
        "text": item["snippet"],
    } for item in transitive_consumers)
    for record in classification_records:
        haystack = f"{record['path']}\n{record['text']}".lower()
        for category, keywords in CATEGORIES.items():
            if any(_contains_keyword(haystack, keyword) for keyword in keywords):
                if len(category_evidence[category]) < 8:
                    category_evidence[category].append(record)

    signature = method_body.split("{", 1)[0]
    public_api = bool(re.search(r"\b(public|protected)\b", signature))
    scores = {category: len(evidence) for category, evidence in category_evidence.items()}
    ranked = sorted(scores, key=lambda category: scores[category], reverse=True)
    classification = ranked[0] if ranked and scores[ranked[0]] else "unknown"
    conflicting = [category for category, score in scores.items() if score and category != classification]
    behavioral_corpus = "\n".join(record["text"] for record in classification_records)
    build_variants = _collect_android_build_variants(repo_root, repository_paths)
    discriminator_context = _collect_behavioral_discriminator_context(
        repo_root, repository_paths, source_cache, behavioral_corpus,
        source_file, build_variants,
    )
    if discriminator_context["supported"]:
        classification = "runtime_discriminator"
        conflicting = [
            category for category, score in scores.items()
            if score and category != classification
        ]
    android_resources = _collect_android_string_resources(
        repo_root, repository_paths, behavioral_corpus
    )
    # Constructor graphs start at direct consumers of the affected method.
    # Transitive textual matches are deliberately excluded as roots because
    # common names (for example `sha1`, `get`, or `read`) can fan out into
    # unrelated subsystems before the bounded traversal reaches the real
    # wrapper chain.
    lifecycle_seed_paths = [item["path"] for item in call_sites]
    if not lifecycle_seed_paths:
        lifecycle_seed_paths = [
            item["path"] for item in consumer_evidence if item.get("path")
        ][:4]
    if not lifecycle_seed_paths:
        lifecycle_seed_paths = [source_file.relative_to(repo_root).as_posix()]
    cache_lifecycle = _collect_cache_lifecycle(
        repo_root, lifecycle_seed_paths, source_cache=source_cache
    )
    cache_signals = {
        name: _matching_patterns(behavioral_corpus, patterns)
        for name, patterns in CACHE_BEHAVIOR_PATTERNS.items()
    }
    cache_blockers = {
        name: _matching_patterns(behavioral_corpus, patterns)
        for name, patterns in CACHE_CONTRACT_BLOCKERS.items()
    }
    blocking_contracts = [name for name, matches in cache_blockers.items() if matches]
    required_cache_signals = (
        bool(cache_signals["miss_path"])
        and bool(cache_signals["population_path"])
        and bool(cache_signals["derived_location"])
    )
    lifecycle_cache_signals = (
        cache_lifecycle["supported"]
        and bool(cache_signals["derived_location"])
    )
    cache_semantics = classification == "cache" or bool(category_evidence["cache"])
    regenerable_cache_supported = (
        cache_semantics
        and (required_cache_signals or lifecycle_cache_signals)
        and not blocking_contracts
    )
    cache_assessment = {
        "supported": regenerable_cache_supported,
        "confidence": "high" if regenerable_cache_supported else "insufficient",
        "required_signals": {
            "miss_path": bool(cache_signals["miss_path"]),
            "population_path": bool(cache_signals["population_path"]),
            "derived_location": bool(cache_signals["derived_location"]),
        },
        "cache_lifecycle": cache_lifecycle,
        "optional_signals": {
            "eviction_path": bool(cache_signals["eviction_path"]),
        },
        "matched_patterns": cache_signals,
        "blocking_contracts": blocking_contracts,
        "blocker_patterns": cache_blockers,
        "explanation": (
            "A disk-backed cache is not disqualified merely because persistence "
            "or serialization terms occur. Support requires observable miss, "
            "population, and derived-location paths with no sensitive or "
            "externally visible contract in the bounded evidence."
        ),
    }
    platform_context = _collect_platform_context(repo_root, repository_paths)
    lifetime_assessment = _collect_representation_lifetime(
        repo_root, repository_paths, source_cache, classification_records
    )
    if lifetime_assessment["supported"] and classification in {
        "password", "persistence", "unknown", "ephemeral_sensitive"
    }:
        classification = "ephemeral_sensitive"
    try:
        from cognalys.extraction.value_usage_graph import collect_value_usage_graph

        value_usage_graph = collect_value_usage_graph(
            repo_root=repo_root,
            source_file=source_file,
            method_name=method_name,
            method_body=method_body,
            call_sites=call_sites,
            source_cache=source_cache,
            repository_paths=repository_paths,
        )
    except Exception as exc:
        value_usage_graph = {"status": "unavailable", "reason": str(exc)}
    ephemeral_discriminator = _assess_ephemeral_runtime_discriminator(
        value_usage_graph, call_sites, repository_index_metadata, max_call_sites,
    )
    if ephemeral_discriminator["supported"]:
        classification = "ephemeral_runtime_discriminator"
    try:
        from cognalys.extraction.semantic_index import resolve_policy_consumers

        policy_consumer_resolution = resolve_policy_consumers(
            repo_root, source_cache,
            list((case_migration_policy or {}).get("required_consumers", [])),
        )
    except Exception as exc:
        policy_consumer_resolution = {
            "status": "unavailable", "backend": "none",
            "resolved": [], "unresolved": [{
                "consumer": value, "reason": str(exc)
            } for value in (case_migration_policy or {}).get(
                "required_consumers", [])],
        }
    try:
        from cognalys.extraction.multi_file_context import collect_multi_file_context

        multi_file_impact = collect_multi_file_context(
            repo_root=repo_root,
            source_file=source_file,
            method_name=method_name,
            class_name=class_name,
            method_body=method_body,
            source_cache=source_cache,
            repository_paths=repository_paths,
            repository_index_metadata=repository_index_metadata,
            max_exact_chars=multi_file_exact_chars,
        )
    except Exception as exc:
        multi_file_impact = {"status": "unavailable", "reason": str(exc)}
    context = {
        "status": "available",
        "repository_call_sites": call_sites,
        "class_construction_sites": construction_sites,
        "method_type_declarations": _method_type_declarations(
            repo_root, source_file, class_name, method_body, repository_paths,
        ),
        "consumer_evidence": consumer_evidence[: max_call_sites * 2],
        "flow_symbols": sorted(flow_symbols),
        "target_flow_symbols": sorted(target_flow_symbols),
        "transitive_consumer_evidence": transitive_consumers,
        "tests": tests[:20],
        "documentation": documents,
        "usage_classification": {
            "primary": classification,
            "scores": scores,
            "conflicting_categories": conflicting,
            "evidence": category_evidence,
            "public_or_protected_api": public_api,
            "regenerable_cache_supported": regenerable_cache_supported,
            "cache_regeneration_assessment": cache_assessment,
        },
        "automatic_migration_candidate": (
            regenerable_cache_supported or lifetime_assessment["supported"]
            or ephemeral_discriminator["supported"]
        ),
        "representation_lifetime_assessment": lifetime_assessment,
        "platform_context": platform_context,
        "android_build_variants": build_variants,
        "runtime_discriminator_assessment": discriminator_context,
        "constant_resolution": discriminator_context["constants"],
        "equivalent_implementations": discriminator_context[
            "equivalent_implementations"
        ],
        "android_resource_resolution": android_resources,
        "value_usage_graph": value_usage_graph,
        "ephemeral_runtime_discriminator_assessment": ephemeral_discriminator,
        "multi_file_impact": multi_file_impact,
        "case_migration_policy": case_migration_policy or {},
        "policy_consumer_resolution": policy_consumer_resolution,
        "required_policy_sources": list(dict.fromkeys(
            item["source_file_path"]
            for item in policy_consumer_resolution.get("resolved", [])
        )),
        "unresolved_policy_consumers": policy_consumer_resolution.get(
            "unresolved", []
        ),
        "repository_index": repository_index_metadata,
        "limits": {
            "analysis_kind": "bounded_textual_repository_scan",
            "call_sites_complete": len(call_sites) < max_call_sites,
            "warning": "Textual evidence does not replace type-resolved data-flow analysis.",
        },
    }
    _promote_flow_sources(
        repo_root, source_file, source_cache, context, multi_file_exact_chars
    )
    return context
