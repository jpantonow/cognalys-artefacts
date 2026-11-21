"""Repository-wide structural evidence for coordinated source fixes.

This module deliberately avoids pretending to be a type resolver.  It builds
an explainable, bounded symbol-impact graph and reports every ambiguity or
truncation so the LLM cannot mistake a partial lexical scan for complete scope.
"""

from __future__ import annotations

import math
import re
from pathlib import Path


SOURCE_SUFFIXES = {".java", ".kt", ".kts"}
CONFIG_NAMES = {
    "build.gradle", "build.gradle.kts", "settings.gradle",
    "settings.gradle.kts", "libs.versions.toml", "AndroidManifest.xml",
    "gradle.properties", "proguard-rules.pro",
}
GENERIC_SYMBOLS = {
    "get", "set", "put", "add", "read", "write", "load", "save",
    "create", "build", "update", "run", "execute", "value", "result",
}
# A symbol that occurs across a large share of the repository selects files by
# ubiquity, not by relation to the finding. markor's createKeyFromPassword
# calls Arrays.fill on the password buffer, and `Arrays` alone -- present in 35
# of that repository's 160 source files -- scored TodoTxtTask, TodoTxtFilter
# and TextCasingUtils into the evidence packet as "references shared symbol
# used by target", spending the exact-source budget on files with no relation
# to PBEKeySpec. The cut-off is derived from the repository being scanned
# rather than a hard-coded name list, so a symbol that is genuinely
# distinctive there still counts however common it is elsewhere, and the
# absolute floor keeps small fixtures and small repositories unaffected.
UBIQUITY_FILE_FRACTION = 0.05
UBIQUITY_MIN_FILES = 5
TEST_SOURCE_ROOT_PATTERN = re.compile(
    r"^(.*?/src/(?:test|androidTest)/(?:java|kotlin))(?:/|$)", re.IGNORECASE,
)
# A file holding a resolved call site of the reported method is the one a
# caller migration must edit. It is exempt from the per-file cap (40,000) and
# from the shared exact-source budget, up to this bound of its own: NotallyX's
# ExportExtensions.kt (44,298 chars, the md5Hash call site) never reached the
# plan, which could then only add an unused helper (2026-10-28).
MAX_REQUIRED_CALLER_CHARS = 120_000
# The call-site files are bounded as a group too. Each one escapes the shared
# budget, so a helper called from forty files put forty files of mandatory
# source into every adjudication, planning and generation prompt (3.6M chars
# in a synthetic repository with 40 callers of 90k chars). Past these bounds a
# call-site file is listed in omitted_required_caller_sources, which the
# rebuild and the caller_source_gap route report, instead of overflowing.
MAX_REQUIRED_CALLER_FILES = 12
MAX_REQUIRED_CALLER_TOTAL_CHARS = 180_000
MAX_CANDIDATE_FILE_CHARS = 40_000
REQUIRED_CALLER_RELEVANCE = "direct call site of the reported method (required exact source)"


def _gradle_module(relative: str) -> str:
    return relative.split("/src/", 1)[0] if "/src/" in relative else ""


def admit_required_callers(contents: dict[str, str], target_relative: str,
                           call_counts: dict[str, int] | None = None,
                           used_chars: int = 0, used_files: int = 0,
                           ) -> tuple[list[str], list[str]]:
    """Split call-site files into the admitted and the omitted ones.

    Ranked by the target's own Gradle module first (the APK compiles it, and
    a caller migration starts there), then by how many call sites a file
    holds, then smaller first so one large file does not crowd out several
    small ones. Admitted while the file fits MAX_REQUIRED_CALLER_CHARS and
    the group still fits MAX_REQUIRED_CALLER_FILES and
    MAX_REQUIRED_CALLER_TOTAL_CHARS (after `used_*`, what is already
    required). An empty content (unreadable) is omitted.
    """
    counts = call_counts or {}
    module = _gradle_module(target_relative)
    ordered = sorted(contents, key=lambda path: (
        _gradle_module(path) != module, -counts.get(path, 0),
        len(contents[path] or ""), path))
    admitted, omitted = [], []
    total, files = used_chars, used_files
    for path in ordered:
        size = len(contents[path] or "")
        if (not size or size > MAX_REQUIRED_CALLER_CHARS
                or files >= MAX_REQUIRED_CALLER_FILES
                or total + size > MAX_REQUIRED_CALLER_TOTAL_CHARS):
            omitted.append(path)
            continue
        admitted.append(path)
        total += size
        files += 1
    return admitted, omitted


def _call_argument_count(text: str) -> int | None:
    """Arguments of a call whose text starts right after its `(`.

    None when the call does not close within `text` (a call spread over
    several lines), so the caller can treat the count as unknown.
    """
    depth, count, filled, quote, index = 0, 1, False, "", 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote, filled = char, True
        elif char in "([{":
            depth, filled = depth + 1, True
        elif char in ")]}":
            if depth == 0:
                return count if filled else 0
            depth -= 1
        elif char == "," and depth == 0:
            count += 1
        elif not char.isspace():
            filled = True
        index += 1
    return None


def _arity_range(method_body: str, method_name: str) -> tuple[int, int | None] | None:
    """(fewest, most) arguments a call of the declaration can pass.

    Kotlin parameters with a default value may be left out and a trailing
    function parameter may move out of the parentheses; a vararg has no
    upper bound. None when the declaration is not found.
    """
    match = re.search(rf"\b{re.escape(method_name)}\s*\(([^)]*)\)", method_body)
    if not match:
        return None
    parameters, depth, current = [], 0, ""
    for char in match.group(1):
        if char in "<([{":
            depth += 1
        elif char in ">)]}":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parameters.append(current)
            current = ""
        else:
            current += char
    parameters.append(current)
    parameters = [item.strip() for item in parameters if item.strip()]
    if any("..." in item or re.search(r"\bvararg\b", item) for item in parameters):
        return 0, None
    fewest = sum(1 for item in parameters if not re.search(r"(?<![=!<>])=(?!=)", item))
    if parameters and "->" in parameters[-1]:
        fewest = max(0, fewest - 1)
    return min(fewest, len(parameters)), len(parameters)


def _lexical_call_targets(line: str, match: re.Match, method_name: str,
                          simple_class: str, arity: tuple[int, int | None] | None,
                          file_content: str, top_level: bool, target_package: str,
                          ) -> bool:
    """Whether a lexical `name(` match can be a call of the reported method.

    Used only without the semantic index, to decide which lexical callers
    are required exact sources: a common name (`init`, `encode`) also
    matches every `cipher.init(` in the repository. The call's argument count
    must fit the declaration, and its receiver must be able to be the
    target: unqualified (and the file does not declare the name itself),
    the class itself, or a receiver in a file that names the class -- for a
    top-level or extension function, a file that imports the name or shares
    the target's package.
    """
    if arity is not None:
        passed = _call_argument_count(line[match.end():])
        fewest, most = arity
        if passed is not None and (passed < fewest or (most is not None and passed > most)):
            return False
    prefix = line[:match.start()].rstrip()
    if not prefix.endswith("."):
        return not any(_declaration_like(item, method_name)
                       for item in file_content.splitlines())
    receiver = re.search(r"([\w$]+)\s*\??\.$", prefix)
    if receiver and receiver.group(1) in {simple_class, "this"}:
        return True
    if top_level:
        package = re.search(r"^\s*package\s+([\w.]+)", file_content, re.MULTILINE)
        return bool(
            re.search(rf"^\s*import\s+[\w.]+\.{re.escape(method_name)}\b",
                      file_content, re.MULTILINE)
            or (package.group(1) if package else "") == target_package)
    return bool(simple_class) and re.search(
        rf"\b{re.escape(simple_class)}\b", file_content) is not None


def _read_bounded(path: Path, limit: int) -> str:
    try:
        if not path.is_file() or path.stat().st_size > limit * 4:
            return ""
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return content if len(content) <= limit else ""


def require_caller_sources(impact: dict, repo_root: Path, paths) -> list[str]:
    """Make each file of `paths` a mandatory exact source of `impact`.

    For files that hold a call site the patch must change -- a resolved
    caller, a rename_safety reference -- and that a budget left out. Each is
    added to exact_candidate_sources and to coverage_requirements
    .caller_files, which the evidence packet treats as mandatory and
    validate_multi_file_scope expects reviewed. Returns the paths now
    supplied; one larger than MAX_REQUIRED_CALLER_CHARS, unreadable, or past
    the group bounds of admit_required_callers (counting the call-site files
    already required), is listed in omitted_required_caller_sources instead.
    """
    if not isinstance(impact, dict):
        return []
    exact = impact.setdefault("exact_candidate_sources", [])
    present = {
        str(item.get("source_file_path", "")): item for item in exact if isinstance(item, dict)
    }
    supplied, omitted = [], []
    pending: dict[str, str] = {}
    for relative in dict.fromkeys(str(path) for path in paths or [] if path):
        if relative in present:
            relevance = present[relative].setdefault("relevance", [])
            if REQUIRED_CALLER_RELEVANCE not in relevance:
                relevance.append(REQUIRED_CALLER_RELEVANCE)
            supplied.append(relative)
            continue
        pending[relative] = _read_bounded(Path(repo_root) / relative, MAX_REQUIRED_CALLER_CHARS)
    required_now = [
        str(item.get("content", "")) for item in exact
        if isinstance(item, dict) and REQUIRED_CALLER_RELEVANCE in (item.get("relevance") or [])
    ]
    semantic = impact.get("semantic_resolution") or {}
    counts: dict[str, int] = {}
    for item in semantic.get("resolved_callers") or []:
        if isinstance(item, dict):
            counts[str(item.get("path", ""))] = counts.get(str(item.get("path", "")), 0) + 1
    admitted, omitted = admit_required_callers(
        pending, str((impact.get("target") or {}).get("source_file_path", "")), counts,
        used_chars=sum(len(item) for item in required_now), used_files=len(required_now))
    for relative in admitted:
        content = pending[relative]
        exact.append({
            "source_file_path": relative,
            "relevance": [REQUIRED_CALLER_RELEVANCE],
            "content": content,
        })
        present[relative] = exact[-1]
        supplied.append(relative)
    coverage = impact.setdefault("coverage_requirements", {})
    coverage["caller_files"] = sorted(set(coverage.get("caller_files") or []) | set(supplied))
    impact["required_caller_sources"] = sorted(
        set(impact.get("required_caller_sources") or []) | set(supplied))
    impact["omitted_required_caller_sources"] = sorted(
        (set(impact.get("omitted_required_caller_sources") or []) | set(omitted)) - set(supplied))
    return supplied


def caller_sources_missing_from_plan(plan: dict, source_manifest: list, impact: dict) -> list[str]:
    """Call-site files a plan's generation blocker says it could not see.

    NotallyX's plan blocked, correctly, on "ExportExtensions.kt is present in
    the manifest only with exact_source_available=false"; the correction
    then dropped the blocker and planned an unused helper. Such a blocker is
    answered by supplying the file, not by correcting the plan. Matched on
    paths only: a manifest path without exact source, or an omitted required
    source that holds a resolved call site, that a blocker names by its path
    (or by its file name when no other candidate shares it).
    """
    blockers = [str(item) for item in (plan or {}).get("generation_blockers") or [] if item]
    if not blockers or not isinstance(impact, dict):
        return []
    available = {
        str(item.get("source_file_path", "")) for item in source_manifest or []
        if item.get("exact_source_available")
    }
    candidates = {
        str(item.get("source_file_path", "")) for item in source_manifest or []
        if not item.get("exact_source_available") and item.get("source_file_path")
    }
    semantic = impact.get("semantic_resolution") or {}
    call_site_files = {
        str(item.get("path", "")) for item in semantic.get("resolved_callers") or []
        if isinstance(item, dict)
    } | set(impact.get("required_caller_sources") or [])
    candidates |= {
        path for path in [*(impact.get("omitted_required_flow_sources") or []),
                          *(impact.get("omitted_required_caller_sources") or [])]
        if path in call_site_files
    }
    candidates -= available
    candidates.discard("")
    names = [Path(path).name for path in candidates | available]
    missing = []
    for path in sorted(candidates):
        name = Path(path).name
        unique = names.count(name) == 1
        by_name = re.compile(rf"(?<![\w.-]){re.escape(name)}(?![\w-])")
        if any(path in blocker or (unique and by_name.search(blocker)) for blocker in blockers):
            missing.append(path)
    return missing


def _snippet(lines: list[str], index: int, radius: int = 5) -> str:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return "\n".join(f"{i + 1}: {lines[i]}" for i in range(start, end))


def _method_arity(method_body: str, method_name: str) -> int | None:
    match = re.search(rf"\b{re.escape(method_name)}\s*\(([^)]*)\)", method_body)
    if not match:
        return None
    parameters = match.group(1).strip()
    if not parameters:
        return 0
    depth = 0
    count = 1
    for char in parameters:
        if char in "<([{":
            depth += 1
        elif char in ">)]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            count += 1
    return count


def _declaration_like(line: str, name: str) -> bool:
    """Whether this line declares ``name`` rather than calling it.

    The Kotlin form is checked first and on its own. A declaration like

        inline fun <reified T> Intent.getParcelableExtraCompat(key: String)

    puts a type parameter list and an extension receiver between `fun` and
    the name, so the older pattern -- `fun` immediately followed by the name
    -- missed it, and the receiver's dot then tripped the member-access guard
    below, which classified the declaration as a call site. Element Android's
    getParcelableExtraCompat is exactly that shape, and the adjudication of
    CVE-2024-26131 blocked on not knowing what it does.
    """
    if re.search(
        rf"\bfun\b\s*(?:<[^>]{{0,120}}>)?\s*(?:[\w$?]+(?:<[^>]{{0,120}}>)?\.)?"
        rf"{re.escape(name)}\s*\(",
        line,
    ):
        return True
    prefix = line.split(name, 1)[0]
    if re.search(r"(?:[.=]|\b(?:return|throw|new)\s+)\s*$", prefix):
        return False
    return bool(re.search(
        rf"^\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*"
        rf"(?:(?:public|private|protected|internal|static|final|abstract|"
        rf"override|suspend|native|synchronized|open|inline)\s+)*"
        rf"[\w$.<>?,\[\]]+\s+{re.escape(name)}\s*\(",
        line,
    ))


def _enclosing_symbol(lines: list[str], index: int) -> str:
    pattern = re.compile(
        r"\b(?:fun\s+|(?:public|private|protected|internal|static|final|"
        r"override|suspend|abstract|synchronized|native)\s+)*"
        r"([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?::[^={]+)?(?:\{|=|$)"
    )
    for cursor in range(index, max(-1, index - 120), -1):
        match = pattern.search(lines[cursor])
        if match and match.group(1) not in {"if", "for", "while", "when", "switch"}:
            return match.group(1)
    return "<unknown>"


TEST_SOURCE_ROOT_ANCHOR_CHARS = 3_000


def _test_source_root_anchor(
    repo_root: Path,
    repository_paths: list[Path],
    source_cache: dict[Path, str],
    covered_paths: set[str],
    exclude_paths: set[str] | None = None,
) -> dict | None:
    """Prove an existing test source root even when no test covers this fix.

    The relevance scoring above only pulls in a test file when an existing
    test happens to reference the changed symbol. A repository can still
    have a perfectly usable ``src/test`` or ``src/androidTest`` source set
    that simply does not yet cover the affected method; without an anchor
    like this, downstream patch planners are told no test source root
    exists at all and can never add a regression test for the fix.

    ``exclude_paths`` lets a caller keep out test files it already decided
    are too weakly related to present as exact evidence (for example, a
    test that only mentions the target class in passing); this anchor must
    not resurrect those under a different justification, so it looks for
    another file under the same root and gives up rather than including one.

    Deliberately ignores the exact-source character budget and truncates to
    a small, fixed size: this is a bounded proof that a test convention
    exists, not a full editable candidate, so it must survive even when the
    target file alone (an oversized "god class", say) exhausts the budget.
    """
    if any(TEST_SOURCE_ROOT_PATTERN.match(path) for path in covered_paths):
        return None
    exclude_paths = exclude_paths or set()
    candidates = sorted(
        path.relative_to(repo_root).as_posix()
        for path in repository_paths
        if path.suffix.lower() in SOURCE_SUFFIXES
        and TEST_SOURCE_ROOT_PATTERN.match(path.relative_to(repo_root).as_posix())
        and path.relative_to(repo_root).as_posix() not in exclude_paths
    )
    if not candidates:
        return None
    relative = candidates[0]
    path = repo_root / relative
    content = source_cache.get(path, "")
    if not content and path.is_file():
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
    if not content:
        return None
    truncated = len(content) > TEST_SOURCE_ROOT_ANCHOR_CHARS
    # The old wording asserted "no existing test currently references this
    # method". That is a claim about the repository, and the anchor is in no
    # position to make it: it also fires when related tests were found and
    # then held back through ``exclude_paths``. markor has a round-trip test
    # for the reported class, and the anchor still presented an unrelated
    # TextViewUtilsTest under a sentence saying no such test existed. State
    # only what this file is -- a bounded proof that a test source root
    # exists -- and say which case produced it.
    reason = (
        "existing test-source-root convention (anchor only, not selected for "
        + ("its relation to this finding; more closely related test files "
           "were found but were not recommended as edit targets"
           if exclude_paths else
           "its relation to this finding; no test file was scored as related "
           "to this finding")
        + (", content truncated to the file's opening" if truncated else "")
        + ")"
    )
    return {
        "source_file_path": relative,
        "relevance": [reason],
        "content": content[:TEST_SOURCE_ROOT_ANCHOR_CHARS],
    }


def _important_symbols(method_body: str, method_name: str) -> list[str]:
    symbols = set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", method_body))
    symbols.update(re.findall(
        r"\b(?:this\.)?([A-Za-z_]\w*)\s*(?:=|\.get\s*\(|\.set\s*\()",
        method_body,
    ))
    symbols.update(re.findall(r"\b([A-Z][A-Za-z0-9_]*)\s*\.", method_body))
    symbols.discard(method_name)
    return sorted(
        symbol for symbol in symbols
        if len(symbol) >= 4 and symbol.lower() not in GENERIC_SYMBOLS
    )[:24]


# Names that appear followed by "(" but are language or platform constructs,
# not repository functions worth resolving.
NON_CALLEE_NAMES = {
    "if", "for", "while", "when", "switch", "catch", "return", "throw", "new",
    "super", "this", "synchronized", "try", "do", "else", "assert", "print",
    "println", "require", "check", "let", "also", "apply", "run", "with",
    "takeif", "takeunless", "listof", "setof", "mapof", "arrayof", "lazy",
    "string", "int", "long", "boolean", "float", "double", "byte", "char",
}
MAX_CALLEE_DEFINITIONS = 6
CALLEE_DEFINITION_CHARS = 4_000


def _called_names(method_body: str, method_name: str) -> list[str]:
    """Function names the reviewed body calls, in first-appearance order."""
    seen: list[str] = []
    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*(?:<[^<>()]{0,80}>)?\s*\(",
                             method_body):
        name = match.group(1)
        if name == method_name or name.lower() in NON_CALLEE_NAMES:
            continue
        if len(name) < 3 or name[0].isupper():
            # A capitalised name is a constructor or a type, whose class is
            # already reached through the ordinary class-reference scoring.
            continue
        if name not in seen:
            seen.append(name)
    return seen


# A constraint finding turns on the *value* of an argument, and that value is
# usually a constant declared somewhere else. `_called_names` skips
# capitalised names on the grounds that the class-reference scoring already
# reaches them, and for photok that was simply untrue: the reviewed method
# selects `Algorithm.AesCbcPkcs7Padding`, whose declaration fixes the
# transformation string `AES/CBC/PKCS7Padding` the whole CipherSpec finding is
# about, and Algorithm.kt never entered the packet. The external research pass
# compensated by fetching it from raw.githubusercontent -- as a web claim, in
# the one evidence category the adjudication is told never establishes what
# this repository's code does.
#
# Matched narrowly: `Type.MEMBER`, which is a constant or enum access, rather
# than every capitalised token. A constructor call (`Type(`) and a bare type
# annotation are excluded because neither carries a value.
_MEMBER_ACCESS = re.compile(r"\b([A-Z][\w$]*)\s*\.\s*([A-Z_][\w$]*)\b")
MAX_CONSTANT_DEFINITIONS = 4
CONSTANT_DEFINITION_CHARS = 3_000


# A project that writes down its own stored format or crypto design puts the
# contract *and the reason for it* in a document, and nothing in the packet
# ever looked at one. photok's ENCRYPTION.md states that versions 2 and 3
# deliberately moved from AES/GCM to AES/CBC because "AES-GCM also doesn't
# support random access seeking -- large videos required reading and
# discarding all preceding bytes", and that the stored format is versioned
# with a documented migration. The adjudication, lacking it, asked the
# maintainer to authorise a format change and define a migration policy --
# both already written down in their repository -- and the external research
# pass ended up fetching that same file over the network as a web claim.
#
# Selected by term overlap with the reviewed method, so a boilerplate
# SECURITY.md that names none of this code's types is not pulled in.
PROJECT_DOC_SUFFIXES = {".md", ".adoc", ".rst", ".txt"}
PROJECT_DOC_STEMS = {
    "encryption", "security", "architecture", "design", "format", "protocol",
    "crypto", "cryptography", "threat_model", "threatmodel", "spec",
    "specification", "storage", "migration",
}
MAX_DOCUMENTATION_FILES = 2
DOCUMENTATION_CHARS = 8_000


def _is_project_document(relative: str, suffix: str) -> bool:
    """Whether this path is one of the project's own design documents."""
    if suffix.lower() not in PROJECT_DOC_SUFFIXES:
        return False
    parts = relative.split("/")
    if len(parts) > 1 and parts[0].lower() not in {"docs", "doc", "documentation"}:
        return False
    stem = parts[-1].rsplit(".", 1)[0].lower().replace("-", "_")
    return stem in PROJECT_DOC_STEMS


def _documentation_evidence(method_body: str, class_name: str,
                            repository_paths: list[Path], repo_root: Path,
                            source_cache: dict[Path, str]) -> list[dict]:
    """The project's own design documents that speak about this finding."""
    terms = {
        token for token in re.findall(r"\b[A-Z][\w$]{4,}\b", method_body or "")
    }
    simple_class = class_name.rsplit(".", 1)[-1].split("$")[0]
    if simple_class:
        terms.add(simple_class)
    if not terms:
        return []
    documents: list[dict] = []
    for path in repository_paths:
        if len(documents) >= MAX_DOCUMENTATION_FILES:
            break
        relative = path.relative_to(repo_root).as_posix()
        if not _is_project_document(relative, path.suffix):
            continue
        content = source_cache.get(path, "")
        if not content:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        matched = sorted(term for term in terms if term in content)
        if not matched:
            continue
        documents.append({
            "source_file_path": relative,
            "matched_terms": matched[:8],
            "content": content[:DOCUMENTATION_CHARS],
            "truncated": len(content) > DOCUMENTATION_CHARS,
        })
    return documents


# Whether a value the reviewed method produces survives a round trip through
# storage is decided by the code that converts it, and that code is usually a
# few lines in a file nothing else points at. photok's adjudication blocked on
# "the mapper/schema, all rehydration paths, and all import/unlock consumers
# are not established", with the DAO, the repository and the backup flows all
# in the packet and VaultProtectionMapper.kt -- twenty-two lines that pass
# `params` through untouched in both directions -- absent. Those twenty-two
# lines are what establish that the stored record carries its own algorithm
# and is read back with it, which is the whole compatibility question.
_CONVERSION_DECLARATION = re.compile(
    r"\bfun\s+(?:[\w$.<>?]+\.)?(?:to|from|as|map)[A-Z]\w*\s*\("
)
_CONSTRUCTED_TYPE = re.compile(r"\b([A-Z][\w$]{4,})\s*\(")
MAX_CONVERSION_DEFINITIONS = 2
CONVERSION_DEFINITION_CHARS = 2_500


def _produced_type_names(method_body: str) -> list[str]:
    """Types the reviewed method builds, the returned one first."""
    returned = [
        match.group(1) for match in re.finditer(
            r"\breturn\s+([A-Z][\w$]{4,})\s*\(", method_body or "")
    ]
    built = [
        match.group(1)
        for match in _CONSTRUCTED_TYPE.finditer(method_body or "")
    ]
    return list(dict.fromkeys(returned + built))


def _conversion_definitions(method_body: str, target_relative: str,
                            repository_paths: list[Path], repo_root: Path,
                            source_cache: dict[Path, str]) -> list[dict]:
    """Where the types this method produces are converted between layers.

    A conversion function names the type it converts in its own signature,
    which is what ties the file to this finding rather than to the
    repository at large. Bounded like the other resolvers beside it.
    """
    produced = _produced_type_names(method_body)
    if not produced:
        return []
    definitions: list[dict] = []
    for path in repository_paths:
        if len(definitions) >= MAX_CONVERSION_DEFINITIONS:
            break
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        content = source_cache.get(path, "")
        if not content:
            continue
        relative = path.relative_to(repo_root).as_posix()
        if relative == target_relative:
            continue
        if not any(name in content for name in produced):
            continue
        converted: list[str] = []
        for line in content.splitlines():
            if not _CONVERSION_DECLARATION.search(line):
                continue
            for name in produced:
                if name in line and name not in converted:
                    converted.append(name)
        if not converted:
            continue
        definitions.append({
            "converted_types": converted[:4],
            "source_file_path": relative,
            "definition": content[:CONVERSION_DEFINITION_CHARS],
            "truncated": len(content) > CONVERSION_DEFINITION_CHARS,
        })
    return definitions


# Persisting a setting and reading it back is what makes a stored format
# self-describing, and neither half says it alone. photok's packet carried
# the mapper that persists VaultProtectionParams (CONV) and the file whose
# unlock() reads `params.algorithm.value` back out of it (the target source),
# and the adjudication still blocked on "whether all readers select an
# algorithm from the stored parameters" -- the two halves were both present
# and the relation between them was stated nowhere.
#
# Narrow on purpose: only a field whose written value is chosen from one of
# this repository's own constants, which is what a format selector looks
# like. `kdf = kdf` copies a local and says nothing about a stored setting;
# `algorithm = Algorithm.AesCbcPkcs7Padding` picks one.
_SETTING_ASSIGNMENT = re.compile(
    r"\b(?P<field>[a-z][\w$]*)\s*=\s*(?P<owner>[A-Z][\w$]*)\s*\.\s*[A-Za-z_]"
)
MAX_ROUND_TRIP_FIELDS = 3
MAX_ROUND_TRIP_READERS = 6


def _written_settings(method_body: str, owners: list[str]) -> list[tuple[str, str]]:
    """(field, constant owner) pairs the reviewed method writes."""
    known = set(owners)
    pairs: list[tuple[str, str]] = []
    for match in _SETTING_ASSIGNMENT.finditer(method_body or ""):
        pair = (match.group("field"), match.group("owner"))
        if pair[1] in known and pair not in pairs:
            pairs.append(pair)
    return pairs[:MAX_ROUND_TRIP_FIELDS]


_KOTLIN_FUNCTION = re.compile(r"\bfun\s+(?P<name>[A-Za-z_][\w$]*)\s*[(<]")


def _enclosing_function(lines: list[str], index: int) -> str:
    """The function a line sits in, by declaration and indentation.

    _enclosing_symbol beside this matches anything signature-shaped, which
    on a read site latches onto the call on the nearest line:
    `requireNotNull(params.algorithm)` inside unlock() was reported as being
    read by "requireNotNull". For evidence a model reads, that is worse than
    saying nothing. A declaration indented less than the read is the
    function containing it.
    """
    own_indent = len(lines[index]) - len(lines[index].lstrip())
    for cursor in range(index - 1, max(-1, index - 200), -1):
        line = lines[cursor]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent >= own_indent:
            continue
        match = _KOTLIN_FUNCTION.search(line)
        if match:
            return match.group("name")
        java = _MEMBER_DECLARATION.match(line)
        if java:
            return java.group("name")
    return "<unknown>"


_MEMBER_DECLARATION = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized"
    r"|override|suspend|native|default)\s+)+"
    r"[\w$.<>?,\[\]]+\s+(?P<name>[A-Za-z_][\w$]*)\s*\("
)


# A Kotlin call site carries no receiver type, so every call to a common
# method name comes back ambiguous against every declaration of that name.
# photok's `create` resolved zero callers out of twenty-one call sites, which
# left resolved_callers "partial" and the scope incomplete -- and an
# incomplete scope is what the adjudication is told to abstain on, so no
# amount of other evidence could move the finding. VaultService calls
# `passwordProtectionHandler.create(request)`, and the receiver is declared
# as an interface that dependency injection binds to the concrete class:
#
#     private val passwordProtectionHandler:
#         VaultProtectionHandler<UnlockRequest.Password, CreateRequest.Password>
#
#     @Binds fun bindPasswordUnlocker(impl: PasswordVaultProtectionHandler):
#         VaultProtectionHandler<UnlockRequest.Password, CreateRequest.Password>
#
# Three bindings return the same interface and only the type arguments tell
# them apart, so the whole parameterised type is what gets matched.
_DECLARED_PROPERTY = (
    r"\b{name}\s*:\s*(?P<type>[A-Za-z_][\w$.]*(?:\s*<[^>]{{0,200}}>)?)"
)
_BINDING_FUNCTION = re.compile(
    r"\bfun\s+[A-Za-z_][\w$]*\s*\(\s*[A-Za-z_][\w$]*\s*:\s*"
    r"(?P<impl>[A-Za-z_][\w$.]*)\s*\)\s*:\s*"
    r"(?P<bound>[A-Za-z_][\w$.]*(?:\s*<[^>]{0,200}>)?)"
)
MAX_DI_BINDINGS = 200


def _normalise_type(value: str) -> str:
    """A type with its arguments, whitespace collapsed, package dropped."""
    collapsed = re.sub(r"\s+", "", value or "")
    head, _, arguments = collapsed.partition("<")
    simple = head.rsplit(".", 1)[-1]
    if not arguments:
        return simple
    parts = [
        part.rsplit(".", 1)[-1]
        for part in arguments.rstrip(">").split(",") if part
    ]
    return f"{simple}<{','.join(parts)}>"


_LOCAL_CONSTRUCTION = (
    r"\b(?:val|var)\s+{name}\s*=\s*(?P<type>[A-Z][\w$.]*)\s*\("
)


def declared_receiver_type(content: str, receiver: str) -> str:
    """The type of a receiver, from its declaration or its construction.

    Three shapes, all deterministic and all common in the call sites that
    came back unresolved: an annotated property or parameter
    (`x: SomeType`), a local whose type Kotlin infers from the constructor
    it calls (`val x = SomeType(...)`), and a receiver that is itself a type
    name, which is how a static or companion call reads.
    """
    if not receiver:
        return ""
    # A type name needs no declaration to look up: it is the type.
    if receiver[:1].isupper():
        return _normalise_type(receiver)
    if not content:
        return ""
    match = re.search(
        _DECLARED_PROPERTY.format(name=re.escape(receiver)), content
    )
    if match:
        return _normalise_type(match.group("type"))
    constructed = re.search(
        _LOCAL_CONSTRUCTION.format(name=re.escape(receiver)), content
    )
    return _normalise_type(constructed.group("type")) if constructed else ""


def collect_injection_bindings(repository_paths: list[Path], repo_root: Path,
                               source_cache: dict[Path, str]) -> dict:
    """Interface-to-implementation bindings declared by dependency injection.

    Read only from files that declare a module, so an ordinary factory
    function returning an interface is not mistaken for a binding.
    """
    bindings: dict[str, str] = {}
    for path in repository_paths:
        if len(bindings) >= MAX_DI_BINDINGS:
            break
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        content = source_cache.get(path, "")
        if not content or "@Module" not in content:
            continue
        for match in _BINDING_FUNCTION.finditer(content):
            bound = _normalise_type(match.group("bound"))
            implementation = _normalise_type(match.group("impl"))
            if bound and implementation and bound != implementation:
                bindings.setdefault(bound, implementation)
    return bindings


def resolve_injected_receivers(semantic_resolution: dict, target_owner: str,
                               repo_root: Path,
                               source_cache: dict[Path, str],
                               bindings: dict) -> dict:
    """Settle ambiguous calls whose receiver is an injected interface.

    A call whose receiver resolves to the reviewed class becomes a resolved
    caller; one that resolves to a different implementation is not about
    this finding at all and stops being counted as unresolved. Anything that
    does not resolve is left exactly as it was.
    """
    ambiguous = semantic_resolution.get("ambiguous_calls") or []
    if not ambiguous or not bindings or not target_owner:
        return semantic_resolution
    simple_target = target_owner.rsplit(".", 1)[-1]
    still_ambiguous, newly_resolved = [], []
    for call in ambiguous:
        relative = str(call.get("path", ""))
        # A build script is not a caller of application code. Gradle's own
        # `create("play")` product-flavor calls were counted as unresolved
        # calls to the reviewed method purely because they share its name.
        if relative.endswith((".gradle", ".gradle.kts")):
            continue
        receiver = str(call.get("receiver", "")).strip()
        path = repo_root / relative
        content = source_cache.get(path, "")
        declared = declared_receiver_type(content, receiver) if receiver else ""
        # A declared type that injection binds resolves to its
        # implementation; a concrete declared type already is one. Either
        # way the receiver's type is settled, and a call on a different type
        # was never about this finding.
        implementation = bindings.get(declared, "")
        if not implementation and declared and "<" not in declared:
            implementation = declared
        if not implementation:
            still_ambiguous.append(call)
            continue
        if implementation == simple_target:
            resolved = dict(call)
            resolved["resolved_symbol_id"] = f"{target_owner}#{call.get('name', '')}"
            resolved["resolution"] = "injected_receiver_binding"
            resolved["receiver_type"] = declared
            resolved["bound_implementation"] = implementation
            newly_resolved.append(resolved)
        # Bound to another implementation: this call site was never about
        # the reviewed class, and counting it as unresolved scope was what
        # made every caller of a common method name look unsettled.
    if not newly_resolved and len(still_ambiguous) == len(ambiguous):
        return semantic_resolution
    updated = dict(semantic_resolution)
    updated["resolved_callers"] = (
        list(semantic_resolution.get("resolved_callers") or []) + newly_resolved
    )
    updated["ambiguous_calls"] = still_ambiguous
    updated["injection_bindings_used"] = len(bindings)
    return updated


def _round_trip_evidence(method_body: str, method_name: str,
                         repository_paths: list[Path], repo_root: Path,
                         source_cache: dict[Path, str],
                         declared_owners: list[str]) -> list[dict]:
    """Where a setting this method writes is read back, with file and line.

    Reads inside the reviewed method itself are excluded: a method reading
    what it just assigned establishes nothing about a round trip through
    storage. What counts is another method, elsewhere in the examined scope,
    selecting behaviour from the same field.

    ``declared_owners`` are the constant owners this repository actually
    declares, not merely the ones the method names. Without that
    restriction `val cipher = Cipher.getInstance(...)` reads as a setting
    chosen from a constant, and javax.crypto.Cipher is a platform type whose
    "round trip" means nothing.
    """
    settings = _written_settings(method_body, declared_owners)
    if not settings:
        return []
    records = []
    for field, owner in settings:
        pattern = re.compile(r"\.\s*" + re.escape(field) + r"\b")
        readers: list[dict] = []
        for path in repository_paths:
            if len(readers) >= MAX_ROUND_TRIP_READERS:
                break
            if path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            content = source_cache.get(path, "")
            if not content or field not in content:
                continue
            relative = path.relative_to(repo_root).as_posix()
            lines = content.splitlines()
            for index, line in enumerate(lines):
                if not pattern.search(line):
                    continue
                enclosing = _enclosing_function(lines, index)
                if enclosing == method_name:
                    continue
                readers.append({
                    "source_file_path": relative,
                    "line": index + 1,
                    "enclosing_symbol": enclosing,
                    "statement": line.strip()[:200],
                })
                if len(readers) >= MAX_ROUND_TRIP_READERS:
                    break
        if readers:
            records.append({
                "field": field,
                "chosen_from": owner,
                "written_by": method_name,
                "read_back_by": readers,
                "bound": (
                    "Reads of this field found in the examined scope, "
                    "outside the reviewed method. It states where the "
                    "setting is consumed; it does not prove that no other "
                    "consumer exists outside that scope."
                ),
            })
    return records


def _constant_owner_names(method_body: str) -> list[str]:
    """Types whose members the reviewed body reads, in first-appearance order."""
    seen: list[str] = []
    for match in _MEMBER_ACCESS.finditer(method_body or ""):
        owner = match.group(1)
        if owner in seen or len(owner) < 3:
            continue
        # `Type(` is construction, not a constant read.
        if re.search(rf"\b{re.escape(owner)}\s*\(", method_body or ""):
            continue
        seen.append(owner)
    return seen


_TYPE_KEYWORD = re.compile(r"\b(?:class|interface|enum|object|record)\b")
_IDENTIFIER = re.compile(r"\s*([A-Za-z_$][\w$]*)")
# Words that may sit between the keyword and the declared name.
_TYPE_MODIFIERS = frozenset({
    "class", "interface", "enum", "object", "record", "data", "sealed",
    "open", "abstract", "final", "static", "public", "private", "protected",
    "internal", "inner", "annotation", "value", "companion",
})


def _type_declaration_like(line: str, name: str) -> bool:
    """Whether this line declares the type ``name``.

    The declared name is the first identifier after the keyword that is not
    itself a modifier, so a name appearing later on the line -- as a type
    parameter bound, a supertype, a constructor parameter -- does not count.
    `interface VaultProtectionHandler<URT : UnlockRequest, CRT: CreateRequest>`
    declares VaultProtectionHandler and nothing else, and matching CreateRequest
    there spent one of the few evidence slots on the wrong file.

    Nothing lazy bridges the keyword and the name: a pattern of that shape
    backtracks super-linearly, which is how an earlier evidence helper in
    this pipeline turned a 28-second test suite into a 200-second one.
    """
    keyword = _TYPE_KEYWORD.search(line)
    if not keyword:
        return False
    rest = line[keyword.start():]
    while True:
        token = _IDENTIFIER.match(rest)
        if not token:
            return False
        if token.group(1) == name:
            return True
        if token.group(1) not in _TYPE_MODIFIERS:
            return False
        rest = rest[token.end():]


def _constant_definitions(method_body: str, target_relative: str,
                          repository_paths: list[Path], repo_root: Path,
                          source_cache: dict[Path, str]) -> list[dict]:
    """Where the constants this method reads are declared, with their values.

    Bounded the same way as the callee definitions beside it: a handful of
    declarations, each truncated, and only those this repository declares. A
    member read from a platform type (Cipher.ENCRYPT_MODE) resolves to
    nothing here and is left to external research, which is the evidence path
    that can answer for code this project does not contain.
    """
    wanted = _constant_owner_names(method_body)
    if not wanted:
        return []
    definitions: list[dict] = []
    remaining = list(wanted)
    for path in repository_paths:
        if not remaining or len(definitions) >= MAX_CONSTANT_DEFINITIONS:
            break
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        content = source_cache.get(path, "")
        if not content:
            continue
        relative = path.relative_to(repo_root).as_posix()
        lines = content.splitlines()
        for name in list(remaining):
            if name not in content:
                continue
            for index, line in enumerate(lines):
                if not _type_declaration_like(line, name):
                    continue
                if relative == target_relative:
                    remaining.remove(name)
                    break
                snippet = "\n".join(
                    lines[index:index + 40]
                )[:CONSTANT_DEFINITION_CHARS]
                definitions.append({
                    "constant_owner": name,
                    "source_file_path": relative,
                    "line": index + 1,
                    "declaration": snippet,
                })
                remaining.remove(name)
                break
            if len(definitions) >= MAX_CONSTANT_DEFINITIONS:
                break
    return definitions


def _callee_definitions(method_body: str, method_name: str,
                        target_relative: str,
                        repository_paths: list[Path], repo_root: Path,
                        source_cache: dict[Path, str]) -> list[dict]:
    """Where the functions this method calls are defined, with their source.

    The packet supplied the reviewed method, its callers and its tests, but
    never the body of the helpers the method itself calls. Element Android's
    handleAppStarted reads its nested Intent through
    `intent.getParcelableExtraCompat<Intent>(EXTRA_NEXT_INTENT)`, and the
    adjudication of CVE-2024-26131 blocked on
    "unconfirmed_pre_launch_validation_state": it could not establish whether
    that helper validates, filters or sanitises what it returns. The helper is
    fifteen lines in the same repository -- an API-level wrapper around
    getParcelableExtra that validates nothing -- and it was never offered.

    Bounded on purpose: a handful of definitions, each truncated, and only
    those the repository itself declares. A call into a platform or library
    API resolves to nothing here and is left to external research, which is
    the evidence path that can actually answer for code this project does not
    contain.
    """
    wanted = _called_names(method_body, method_name)
    if not wanted:
        return []
    definitions: list[dict] = []
    remaining = list(wanted)
    for path in repository_paths:
        if not remaining or len(definitions) >= MAX_CALLEE_DEFINITIONS:
            break
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        content = source_cache.get(path, "")
        if not content:
            continue
        relative = path.relative_to(repo_root).as_posix()
        lines = content.splitlines()
        for name in list(remaining):
            if f"{name}(" not in content:
                continue
            for index, line in enumerate(lines):
                if name not in line or not _declaration_like(line, name):
                    continue
                # The declaration inside the reviewed file is the method
                # itself or a sibling already supplied with it.
                if relative == target_relative:
                    remaining.remove(name)
                    break
                start = max(0, index - 2)
                snippet = "\n".join(
                    lines[start:index + 24]
                )[:CALLEE_DEFINITION_CHARS]
                definitions.append({
                    "callee": name,
                    "source_file_path": relative,
                    "line": index + 1,
                    "definition": snippet,
                })
                remaining.remove(name)
                break
            if len(definitions) >= MAX_CALLEE_DEFINITIONS:
                break
    return definitions


def collect_multi_file_context(
    repo_root: Path,
    source_file: Path,
    method_name: str,
    class_name: str,
    method_body: str,
    source_cache: dict[Path, str],
    repository_paths: list[Path],
    repository_index_metadata: dict,
    max_occurrences: int = 160,
    max_candidates: int = 18,
    max_exact_chars: int = 60_000,
) -> dict:
    """Build a bounded change-impact graph around the reported method."""
    target_relative = source_file.relative_to(repo_root).as_posix()
    simple_class = class_name.rsplit(".", 1)[-1].split("$")[0]
    arity = _method_arity(method_body, method_name)
    kotlin_constructor_pattern = constructor_declaration = None
    if method_name == "<init>":
        # A constructor has no textual call-site pattern matching its own
        # name; Java callers write `new SimpleClassName(...)` and Kotlin
        # callers `SimpleClassName(...)` (Amethyst's MacInstance), whose
        # `class SimpleClassName(` line declares it instead.
        # The same call patterns as evidence_facts' caller contract, so the
        # two never disagree on what a constructor call is (`new pkg.Name<>(`,
        # Kotlin `Outer.Inner(`).
        from cognalys.extraction.evidence_facts import constructor_call_pattern
        from cognalys.prs.pr_feedback import resolve_source_method_name

        constructor_name = (
            resolve_source_method_name(method_name, class_name, source_file)
            if class_name else ""
        )
        method_pattern = (
            constructor_call_pattern(constructor_name, class_name, java=True)
            if constructor_name else None
        )
        if constructor_name:
            kotlin_constructor_pattern = constructor_call_pattern(constructor_name, class_name)
            constructor_declaration = re.compile(
                rf"\b(?:class|object|interface|fun)\s+{re.escape(constructor_name)}\b")
    elif method_name and not method_name.startswith("<"):
        method_pattern = re.compile(rf"\b{re.escape(method_name)}\s*\(")
    else:
        # <clinit> (static initializer) has no explicit call site in source.
        method_pattern = None
    important_symbols = _important_symbols(method_body, method_name)
    scanned_sources = [
        path for path in repository_paths
        if path.suffix.lower() in SOURCE_SUFFIXES and source_cache.get(path)
    ]
    ubiquity_threshold = max(
        UBIQUITY_MIN_FILES,
        math.ceil(UBIQUITY_FILE_FRACTION * len(scanned_sources)),
    )
    symbol_patterns = {}
    ubiquitous_symbols = []
    for symbol in important_symbols:
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        file_count = sum(
            1 for path in scanned_sources if pattern.search(source_cache[path])
        )
        if file_count > ubiquity_threshold:
            ubiquitous_symbols.append(
                {"symbol": symbol, "file_count": file_count}
            )
            continue
        symbol_patterns[symbol] = pattern

    callers = []
    # The lexical calls that can be calls of this target (argument count and
    # receiver fit): without the semantic index, only these are required.
    target_shaped_calls: list[str] = []
    target_arity_range = (
        _arity_range(method_body, method_name) if method_pattern is not None
        and method_name != "<init>" else None)
    target_package_match = re.search(
        r"^\s*package\s+([\w.]+)", source_cache.get(source_file, ""), re.MULTILINE)
    target_package = target_package_match.group(1) if target_package_match else ""
    # A top-level or extension function: its callers name no class.
    top_level_target = bool(method_name) and not method_name.startswith("<") and (
        simple_class.endswith("Kt") or bool(re.search(
            rf"\bfun\b\s*(?:<[^>]{{0,120}}>)?\s*[\w$?]+(?:<[^>]{{0,120}}>)?\."
            rf"{re.escape(method_name)}\s*\(", method_body)))
    declarations = []
    shared_references = []
    type_references = []
    tests = []
    candidate_scores: dict[str, dict] = {}
    total_occurrences = 0
    occurrence_truncated = False

    def score(path: str, points: int, reason: str, symbol: str = "") -> None:
        record = candidate_scores.setdefault(path, {
            "source_file_path": path, "score": 0, "reasons": [], "symbols": [],
        })
        record["score"] += points
        if reason not in record["reasons"]:
            record["reasons"].append(reason)
        if symbol and symbol not in record["symbols"]:
            record["symbols"].append(symbol)

    score(target_relative, 100, "reported finding location", method_name)
    for path in repository_paths:
        relative = path.relative_to(repo_root).as_posix()
        content = source_cache.get(path, "")
        if path.suffix.lower() not in SOURCE_SUFFIXES or not content:
            continue
        lines = content.splitlines()
        is_test = "test" in relative.lower()
        call_pattern = (
            kotlin_constructor_pattern
            if kotlin_constructor_pattern and path.suffix.lower() in {".kt", ".kts"}
            else method_pattern
        )
        for index, line in enumerate(lines):
            if call_pattern and call_pattern.search(line):
                total_occurrences += 1
                if total_occurrences > max_occurrences:
                    occurrence_truncated = True
                    continue
                evidence = {
                    "path": relative,
                    "line": index + 1,
                    "enclosing_symbol": _enclosing_symbol(lines, index),
                    "snippet": _snippet(lines, index),
                }
                if _declaration_like(line, method_name) or (
                        constructor_declaration and constructor_declaration.search(line)):
                    evidence["target_arity"] = arity
                    declarations.append(evidence)
                    if relative != target_relative:
                        score(relative, 45, "same-name declaration, override, or implementation", method_name)
                else:
                    callers.append(evidence)
                    score(relative, 60, "direct call site", evidence["enclosing_symbol"])
                    if method_name == "<init>" or _lexical_call_targets(
                            line, call_pattern.search(line), method_name, simple_class,
                            target_arity_range, content, top_level_target, target_package):
                        target_shaped_calls.append(relative)
                    if is_test:
                        tests.append(evidence)
                        score(relative, 20, "test exercises affected method", method_name)
            if simple_class and simple_class in line and relative != target_relative:
                if len(type_references) < max_occurrences:
                    record = {
                        "path": relative, "line": index + 1,
                        "snippet": _snippet(lines, index, 3),
                    }
                    type_references.append(record)
                    score(relative, 18, "references affected class", simple_class)
            for symbol, pattern in symbol_patterns.items():
                if pattern.search(line) and relative != target_relative:
                    if len(shared_references) < max_occurrences:
                        record = {
                            "path": relative, "line": index + 1,
                            "symbol": symbol, "snippet": _snippet(lines, index, 3),
                        }
                        shared_references.append(record)
                        score(relative, 12, "references shared symbol used by target", symbol)

    # Computed once: the round-trip evidence needs the owners this
    # repository actually declares, not the ones the method merely names.
    constant_definitions = _constant_definitions(
        method_body, target_relative, repository_paths, repo_root, source_cache,
    )

    try:
        from cognalys.extraction.semantic_index import build_semantic_index

        semantic_resolution = build_semantic_index(
            repo_root=repo_root,
            source_cache=source_cache,
            target_file=source_file,
            target_method=method_name,
            target_arity=arity,
        )
    except Exception as exc:
        semantic_resolution = {
            "status": "unavailable", "backend": "none", "reason": str(exc),
        }
    if semantic_resolution.get("status") == "available":
        semantic_resolution = resolve_injected_receivers(
            semantic_resolution, class_name, repo_root, source_cache,
            collect_injection_bindings(
                repository_paths, repo_root, source_cache
            ),
        )
    if semantic_resolution.get("status") == "available":
        for item in semantic_resolution.get("resolved_callers", []):
            score(
                str(item.get("path", "")), 90,
                "AST-resolved caller with matching signature",
                str(item.get("enclosing_symbol", "")),
            )
        for item in semantic_resolution.get("related_declarations", []):
            score(
                str(item.get("path", "")),
                75 if item.get("override") else 55,
                "AST declaration with matching name and arity",
                str(item.get("symbol_id", "")),
            )
        for item in semantic_resolution.get("ambiguous_calls", []):
            score(
                str(item.get("path", "")), 35,
                "AST call requires receiver/type disambiguation",
                str(item.get("enclosing_symbol", "")),
            )

    config_evidence = []
    target_source = source_cache.get(source_file, method_body)
    imported_types = set(re.findall(
        r"\bimport\s+(?:static\s+)?([A-Za-z_][\w.]*)", target_source
    ))
    build_terms = {part for value in imported_types for part in value.split(".") if len(part) > 4}
    for path in repository_paths:
        if path.name not in CONFIG_NAMES and path.suffix.lower() not in {".toml", ".properties"}:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matched = sorted(term for term in build_terms if term.lower() in content.lower())
        if matched:
            relative = path.relative_to(repo_root).as_posix()
            config_evidence.append({"path": relative, "matched_terms": matched[:8]})
            score(relative, 10, "build/configuration reference", matched[0])

    candidates = sorted(
        candidate_scores.values(),
        key=lambda item: (-item["score"], item["source_file_path"]),
    )
    candidate_truncated = len(candidates) > max_candidates
    # The files of the target's call sites: the resolved callers when the
    # semantic index ran, the lexical ones whose argument count and receiver
    # fit the target otherwise. Within the group bounds of
    # admit_required_callers they are never cut by the ranking, the per-file
    # cap or the shared budget below; past them they compete as ordinary
    # candidates and, when left out, are listed as omitted.
    # A lexical match of a common name ("create") is not a call site of this
    # target, so without the index only a distinctive name's callers count.
    if semantic_resolution.get("status") == "available":
        call_sites = [str(item.get("path", "")) for item in
                      semantic_resolution.get("resolved_callers", [])]
    elif method_name.lower() not in GENERIC_SYMBOLS:
        call_sites = target_shaped_calls
    else:
        call_sites = []
    call_counts: dict[str, int] = {}
    for path in call_sites:
        if path and path != target_relative:
            call_counts[path] = call_counts.get(path, 0) + 1
    caller_contents = {}
    for relative in call_counts:
        path = repo_root / relative
        content = source_cache.get(path, "")
        if not content:
            content = _read_bounded(path, MAX_REQUIRED_CALLER_CHARS)
        caller_contents[relative] = content
    caller_paths, _ = admit_required_callers(caller_contents, target_relative, call_counts)
    caller_paths = set(caller_paths)
    kept = candidates[:max_candidates]
    kept += [item for item in candidates[max_candidates:]
             if item["source_file_path"] in caller_paths]
    candidates = kept

    exact_sources = []
    total_chars = 0
    budget_reached = False
    for candidate in candidates:
        relative = candidate["source_file_path"]
        required_caller = relative in caller_paths
        if budget_reached and not required_caller:
            continue
        path = repo_root / relative
        content = source_cache.get(path, "")
        if not content and path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
        if required_caller:
            exact_sources.append({
                "source_file_path": relative,
                "relevance": [*candidate["reasons"], REQUIRED_CALLER_RELEVANCE],
                "content": caller_contents[relative],
            })
            continue
        if not content or len(content) > MAX_CANDIDATE_FILE_CHARS:
            continue
        if total_chars + len(content) > max_exact_chars:
            candidate_truncated = True
            budget_reached = True
            continue
        exact_sources.append({
            "source_file_path": relative,
            "relevance": candidate["reasons"],
            "content": content,
        })
        total_chars += len(content)
    supplied_paths = {item["source_file_path"] for item in exact_sources}
    omitted_callers = [path for path in call_counts if path not in supplied_paths]

    test_anchor = _test_source_root_anchor(
        repo_root, repository_paths, source_cache,
        covered_paths={target_relative, *(item["source_file_path"] for item in exact_sources)},
    )
    if test_anchor:
        exact_sources.append(test_anchor)
        total_chars += len(test_anchor["content"])

    if semantic_resolution.get("status") == "available":
        cross_file_callers = {
            item["path"] for item in semantic_resolution.get("resolved_callers", [])
            if item["path"] != target_relative
        }
        cross_file_declarations = {
            item["path"] for item in semantic_resolution.get("related_declarations", [])
            if item["path"] != target_relative
        }
        ambiguous_call_files = {
            item["path"] for item in semantic_resolution.get("ambiguous_calls", [])
            if item["path"] != target_relative
        }
    else:
        cross_file_callers = {
            item["path"] for item in callers if item["path"] != target_relative
        }
        cross_file_declarations = {
            item["path"] for item in declarations if item["path"] != target_relative
        }
        ambiguous_call_files = set()
    cross_file_shared = {
        item["path"] for item in shared_references if item["path"] != target_relative
    }
    signals = {
        "cross_file_callers": bool(cross_file_callers),
        "possible_overrides_or_implementations": bool(cross_file_declarations),
        "shared_symbol_consumers": bool(cross_file_shared),
        "related_tests": bool(tests),
        "configuration_dependencies": bool(config_evidence),
        "semantic_resolution_available": (
            semantic_resolution.get("status") == "available"
        ),
        "ambiguous_semantic_calls": bool(ambiguous_call_files),
    }
    multi_file_candidate = any(signals.values())
    incomplete_reasons = []
    # Stated, but not disqualifying: a bound the reader should know about
    # that does not by itself make the examined scope incomplete.
    resolved_notes = []
    if repository_index_metadata.get("truncated"):
        incomplete_reasons.append("repository source index was truncated")
    if occurrence_truncated:
        incomplete_reasons.append("method occurrence limit was reached")
    if method_name.lower() in GENERIC_SYMBOLS and len(declarations) > 1:
        # A frequency heuristic over the method's *name*, not a statement
        # that anything was left unresolved. When the semantic index ran and
        # left no ambiguous call behind, it already resolved the target by
        # signature, and repeating the name elsewhere in the repository says
        # nothing about this finding's scope.
        #
        # It used to flip the scope to incomplete on its own, and the
        # adjudication abstains on an incomplete scope. photok's finding is
        # in a method called `create`, one of twelve names on this list, in a
        # repository with ten `fun create(` declarations: its packet recorded
        # target_source, resolved_callers, tests and build_config all
        # complete, and still came out incomplete with this as the single
        # stated limitation. On that rule a method named get, set, add, read,
        # write, load, save, create, build, update, run or execute could
        # almost never earn a generated fix, however completely its evidence
        # had been resolved.
        reason = "generic method name has unresolved declaration ambiguity"
        # Settled when the index resolved a caller of *this* target. A
        # same-named call elsewhere that no receiver type could be found for
        # -- a lambda's `it`, say -- is reported in ambiguous_calls and is a
        # different question: nothing says it targets this class, and
        # requiring zero of them meant a common method name could never have
        # a complete scope however well its own callers were resolved.
        if semantic_resolution.get("status") == "available" and (
            semantic_resolution.get("resolved_callers")
            or not ambiguous_call_files
        ):
            resolved_notes.append(
                "the method name is common in this repository; the semantic "
                "index resolved "
                f"{len(semantic_resolution.get('resolved_callers') or [])} "
                "caller(s) of this target and left "
                f"{len(semantic_resolution.get('ambiguous_calls') or [])} "
                "same-named call(s) whose receiver type could not be "
                "determined, listed in ambiguous_calls"
            )
        else:
            incomplete_reasons.append(reason)
    scope_status = "incomplete" if incomplete_reasons else "complete"

    return {
        "status": "available",
        "analysis_kind": "bounded_structural_change_impact",
        "target": {
            "source_file_path": target_relative,
            "class": class_name,
            "method": method_name,
            "arity": arity,
        },
        "multi_file_candidate": multi_file_candidate,
        "signals": signals,
        "direct_callers": callers[:max_occurrences],
        "same_name_declarations": declarations[:max_occurrences],
        "type_references": type_references[:80],
        "shared_symbol_references": shared_references[:max_occurrences],
        "related_tests": tests[:40],
        "configuration_evidence": config_evidence[:30],
        "semantic_resolution": semantic_resolution,
        "ranked_candidate_files": candidates,
        "exact_candidate_sources": exact_sources,
        "required_caller_sources": sorted(
            item["source_file_path"] for item in exact_sources
            if item["source_file_path"] in caller_paths),
        "omitted_required_caller_sources": sorted(omitted_callers),
        # Every call-site file (semantic, or lexical and target-shaped),
        # ranked as admit_required_callers ranks them, before any bound.
        "call_site_files": sorted(call_counts, key=lambda path: (
            _gradle_module(path) != _gradle_module(target_relative),
            -call_counts[path], path)),
        "scope_completeness": {
            "status": scope_status,
            "reasons": incomplete_reasons,
            "resolved_notes": resolved_notes,
            "repository_index_truncated": bool(repository_index_metadata.get("truncated")),
            "occurrence_limit_reached": occurrence_truncated,
            "candidate_budget_reached": candidate_truncated,
        },
        "coverage_requirements": {
            "caller_files": sorted(cross_file_callers),
            "declaration_files": sorted(cross_file_declarations),
            "ambiguous_call_files": sorted(ambiguous_call_files),
            "shared_symbol_files": sorted(cross_file_shared)[:30],
            "test_files": sorted({item["path"] for item in tests}),
        },
        "callee_definitions": _callee_definitions(
            method_body, method_name, target_relative,
            repository_paths, repo_root, source_cache,
        ),
        "constant_definitions": constant_definitions,
        "documentation_evidence": _documentation_evidence(
            method_body, class_name, repository_paths, repo_root, source_cache,
        ),
        "conversion_definitions": _conversion_definitions(
            method_body, target_relative,
            repository_paths, repo_root, source_cache,
        ),
        "round_trip_evidence": _round_trip_evidence(
            method_body, method_name,
            repository_paths, repo_root, source_cache,
            [item["constant_owner"] for item in constant_definitions],
        ),
        "limits": {
            "max_occurrences": max_occurrences,
            "max_candidates": max_candidates,
            "max_exact_chars": max_exact_chars,
            "type_resolved": False,
            "symbol_ubiquity_threshold_files": ubiquity_threshold,
            "symbols_dropped_as_ubiquitous": ubiquitous_symbols,
            "warning": "Structural lexical evidence must not be described as type-resolved.",
        },
    }
