"""What replacing a deprecated API has to look like, read from the repository.

In run_20260913_004434 three deprecated-API findings -- AsyncTask in
FossifyOrg/Gallery and TowerCollector, LocalBroadcastManager in
NetworkSurvey -- were confirmed and then abstained on the same two blockers:
"no replacement is selected" and "the behaviour a replacement must preserve is
not established". Both were answerable from the checkouts. Gallery already
calls a background-thread helper in 25 files; TowerCollector already posts to
Handler(Looper.getMainLooper()) and schedules WorkManager; NetworkSurvey
already delivers events through LiveData and listener interfaces in 25 files.
The maintainers' own idiom is the replacement a reviewer expects. And the
behaviour a replacement must keep is written in the code that uses the
deprecated API: which callbacks a subclass overrides, where it is started,
cancelled, registered and unregistered.

This module collects both. It is lexical and bounded, and says so: it locates
code, it does not prove equivalence. The mechanisms per role come from
data/api_replacement_roles.json, and the replacement the finding's own message
names is always searched as well, so a deprecated API with no catalogued role
still gets that much.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from collections import Counter
from pathlib import Path

from cognalys.extraction.repository_index import build_repository_index
from cognalys.workspace import library_data

_LOG = logging.getLogger(__name__)


ROLES_FILE = library_data("api_replacement_roles.json")
MAX_EXAMPLES = 5
MAX_CALL_SITES = 40
MAX_SUBCLASSES = 10
_TEST_PATH = re.compile(
    r"(?:^|/)(?:test|androidTest|testShared|sharedTest|testDebug|testRelease)\w*/"
    r"|Tests?\.(?:java|kt)$"
)
_IMPORT_OR_PACKAGE = re.compile(r"^\s*(?:import|package)\s")
_OVERRIDE = re.compile(
    r"\boverride\s+(?:suspend\s+)?fun\s+(?P<kotlin>[A-Za-z_$][\w$]*)\s*\("
    r"|@Override\s+(?:(?:public|protected|private|final|synchronized)\s+)*"
    r"[\w$.<>\[\],? ]+?\s+(?P<java>[A-Za-z_$][\w$]*)\s*\("
)
_CALLED_MEMBER = re.compile(r"[?!]*\.\s*(?P<member>[A-Za-z_$][\w$]*)\s*\(")


def load_replacement_roles(path: Path = ROLES_FILE) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [role for role in data.get("roles", []) if isinstance(role, dict)]


def _relative_sources(repo_root: Path, source_cache: dict | None) -> dict[str, str]:
    root = Path(repo_root)
    if source_cache is None:
        source_cache = build_repository_index(root)["source_cache"]
    sources = {}
    for path, content in source_cache.items():
        try:
            relative = Path(path).relative_to(root).as_posix()
        except ValueError:
            relative = str(path)
        sources[relative] = str(content)
    return sources


_IMPORT_LINE = re.compile(
    r"^\s*import\s+(?:static\s+)?(?P<path>[\w$.]+)\*?(?:\s+as\s+(?P<alias>[\w$]+))?\s*;?\s*$"
)


def _imported_names(lines: list[str]) -> dict[str, str]:
    """Simple name (or alias) -> the import line that brings it into scope."""
    names: dict[str, str] = {}
    for line in lines:
        match = _IMPORT_LINE.match(line)
        if not match or line.rstrip().endswith("*"):
            continue
        name = match.group("alias") or match.group("path").rsplit(".", 1)[-1]
        names.setdefault(name, line.strip().rstrip(";"))
    return names


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(str(path)))


def _mechanisms_for(type_name: str, finding: dict, roles: list[dict]) -> tuple[list, list]:
    """(roles, [(name, patterns, catalogue entry)]) for one deprecated type."""
    matching = [role for role in roles if type_name in (role.get("applies_to") or [])]
    mechanisms = []
    for role in matching:
        for mechanism in role.get("mechanisms") or []:
            try:
                patterns = [re.compile(pattern) for pattern in mechanism.get("patterns") or []]
            except re.error:
                continue
            if patterns:
                mechanisms.append((str(mechanism.get("name", "")), patterns, mechanism))
    target = finding.get("target") or {}
    for package in target.get("packages") or []:
        mechanisms.append((
            f"{package} (named by the finding)",
            [re.compile(rf"^\s*import\s+{re.escape(package)}\.")],
            {},
        ))
    for replacement in target.get("types") or []:
        mechanisms.append((
            f"{replacement} (named by the finding)",
            [re.compile(rf"^\s*import\s+{re.escape(replacement)}\b")],
            {},
        ))
    return matching, mechanisms


# Packages whose code is the platform or the language runtime: their
# behaviour is documented, not read from a binary.
_PLATFORM_PACKAGES = ("java.", "javax.", "android.", "androidx.", "kotlin.", "kotlinx.", "dalvik.")
MAX_BEHAVIOUR_CLASSES = 80
MAX_BEHAVIOUR_DECLARATIONS = 40


def summarize_bytecode_behaviour(disassembly: str) -> dict:
    """Facts about how a helper runs its callback, read from `javap -c` output.

    A Kotlin lambda compiles to a separate synthetic method, so a callback
    invocation that appears in the helper's own bytecode is one it makes on
    the calling thread. That is how org.fossify.commons.helpers.
    ensureBackgroundThread reads: a new Thread only when isOnMainThread(),
    otherwise Function0.invoke inline -- which is what made Gallery's
    hand-built worker queue block and interrupt its callers' threads.
    """
    text = disassembly or ""
    return {
        "checks_main_thread": bool(re.search(r"isOnMainThread|getMainLooper|myLooper", text)),
        "starts_new_thread": bool(re.search(r"java/lang/Thread\.start\b", text)),
        "runs_callback_inline": bool(re.search(
            r"kotlin/jvm/functions/Function\d+\.invoke|java/lang/Runnable\.run\b", text,
        )),
        "uses_executor": bool(re.search(
            r"java/util/concurrent/(?:Executor|ExecutorService)\.(?:execute|submit)", text,
        )),
        "posts_to_handler": bool(re.search(r"android/os/Handler\.post", text)),
        "uses_coroutines": "kotlinx/coroutines" in text,
    }


def summarize_source_behaviour(parameters: str, body: str) -> dict:
    """The same facts, read lexically from a helper declared in the repository."""
    callbacks = set(re.findall(
        r"([A-Za-z_$][\w$]*)\s*:\s*(?:\([^)]*\)\s*->|[\w.]*Runnable\b)", parameters or "",
    )) | set(re.findall(
        r"(?:Runnable|Function\d*<[^>]*>|Callable<[^>]*>)\s+([A-Za-z_$][\w$]*)", parameters or "",
    ))
    invocations = sum(
        len(re.findall(rf"(?<![\w$.]){re.escape(name)}\s*(?:\.\s*(?:invoke|run|call))?\s*\(", body))
        for name in callbacks
    )
    starts_thread = bool(re.search(r"\bThread\s*[({]|(?<![\w.])thread\s*[({]", body))
    return {
        "checks_main_thread": bool(re.search(
            r"isOnMainThread|Looper\s*\.\s*getMainLooper|Looper\s*\.\s*myLooper", body,
        )),
        "starts_new_thread": starts_thread,
        # Invoked both inside the thread and outside it, or with no thread at
        # all: the callback runs on the caller's thread on some path.
        "runs_callback_inline": invocations >= 2 if starts_thread else invocations >= 1,
        "uses_executor": bool(re.search(r"\bExecutors?\b|\bExecutorService\b", body)),
        "posts_to_handler": bool(re.search(r"\.\s*post(?:Delayed)?\s*[({]", body)),
        "uses_coroutines": bool(re.search(r"\blaunch\s*[({]|\bwithContext\s*\(", body)),
    }


def behaviour_consequences(facts: dict) -> tuple[list[str], list[str]]:
    """(guarantees, caveats) that implementation facts establish."""
    guarantees: set[str] = set()
    caveats: list[str] = []
    if (facts.get("checks_main_thread") and facts.get("starts_new_thread")
            and facts.get("runs_callback_inline")):
        caveats.append(
            "runs the work synchronously on the caller's thread when called off "
            "the main thread, and starts a new thread only when called from the "
            "main thread"
        )
    elif facts.get("starts_new_thread"):
        caveats.append("starts a new thread for every call")
        if not facts.get("runs_callback_inline"):
            guarantees.add("off_caller_thread")
    elif facts.get("runs_callback_inline") and not facts.get("uses_executor"):
        caveats.append("runs the work on the caller's thread")
    if facts.get("uses_executor"):
        guarantees.add("off_caller_thread")
    if ((facts.get("starts_new_thread") or facts.get("runs_callback_inline"))
            and not facts.get("uses_executor")):
        caveats.append(
            "gives no ordering between calls and returns no handle to cancel the "
            "work or keep an interrupt from reaching the next caller"
        )
    return sorted(guarantees), caveats


def _package_class_entries(binary: Path, package_path: str) -> list[str]:
    prefix = package_path.rstrip("/") + "/"

    def direct(names):
        return sorted(
            name for name in names
            if name.startswith(prefix) and name.endswith(".class")
            and "/" not in name[len(prefix):]
        )

    entries: list[str] = []
    try:
        with zipfile.ZipFile(binary) as archive:
            names = archive.namelist()
            entries.extend(direct(names))
            if binary.suffix == ".aar":
                for inner in sorted(name for name in names if name.endswith(".jar")):
                    with zipfile.ZipFile(io.BytesIO(archive.read(inner))) as nested:
                        entries.extend(direct(nested.namelist()))
    except (OSError, zipfile.BadZipFile, KeyError):
        return []
    return list(dict.fromkeys(entries))


def implementation_behaviour(repo_root: Path, import_line: str, sources: dict,
                             declarations: list | None = None, cache=None,
                             javap: str | None = None) -> dict:
    """How an imported helper function actually runs its work.

    Read from its declaration when the repository declares it, otherwise from
    its bytecode in the cached library that ships it. Only functions are
    read: a class import names no single method whose behaviour would answer
    the question. Platform packages are skipped; their behaviour is
    documented in the catalogue.
    """
    match = _IMPORT_LINE.match(import_line or "")
    if not match:
        return {}
    symbol = match.group("path").rstrip(".")
    if symbol.startswith(_PLATFORM_PACKAGES) or "." not in symbol:
        return {}
    package, name = symbol.rsplit(".", 1)
    if not name or name[:1].isupper():
        return {}
    kotlin_function = re.compile(
        rf"\bfun\s+(?:<[^>]*>\s*)?(?:[\w$.]+\.)?{re.escape(name)}\s*"
        r"\((?P<params>(?:[^()]|\([^()]*\))*)\)"
    )
    java_method = re.compile(
        rf"\bstatic\s+[\w$<>\[\],.? ]+\s+{re.escape(name)}\s*\((?P<params>[^)]*)\)"
    )
    package_line = re.compile(rf"^\s*package\s+{re.escape(package)}\s*;?\s*$", re.MULTILINE)
    for relative, text in sources.items():
        if not package_line.search(text):
            continue
        found = kotlin_function.search(text) or java_method.search(text)
        if not found:
            continue
        rest = text[found.end():]
        if re.match(r"\s*(?::\s*[\w$.<>?, ]+)?\s*=(?!=)", rest):
            body = rest.split("\n", 1)[0]
        else:
            body = _class_body(text, found.end())
        facts = summarize_source_behaviour(found.group("params"), body)
        guarantees, caveats = behaviour_consequences(facts)
        return {
            "symbol": symbol, "basis": "repository_source", "path": relative,
            "facts": facts, "derived_guarantees": guarantees, "caveats": caveats,
            "bound": "Read lexically from the declaration's own body.",
        }
    try:
        from cognalys.extraction.artifact_cache import (
            disassemble_method, find_javap, read_class_bytes, select_cached_version,
            shared_cache,
        )
        from cognalys.extraction.dependency_context import collect_declarations
    except ImportError:
        return {"symbol": symbol, "basis": "unresolved", "reason": "artifact_tooling_unavailable"}
    javap = find_javap() if javap is None else javap
    if not javap:
        return {"symbol": symbol, "basis": "unresolved", "reason": "javap_not_found"}
    cache = shared_cache() if cache is None else cache
    if declarations is None:
        declarations = collect_declarations(Path(repo_root))["declarations"]
    owner_prefix = ".".join(package.split(".")[:2])
    ordered = sorted(
        (item for item in declarations if item.get("group") and item.get("artifact")),
        key=lambda item: not str(item.get("group", "")).startswith(owner_prefix),
    )
    package_path = package.replace(".", "/")
    needle = name.encode("utf-8")
    seen: set[tuple[str, str]] = set()
    for declaration in ordered[:MAX_BEHAVIOUR_DECLARATIONS]:
        key = (str(declaration["group"]), str(declaration["artifact"]))
        if key in seen:
            continue
        seen.add(key)
        version, _exact = select_cached_version(
            str(declaration.get("version_expression") or ""), cache.versions(*key),
        )
        if not version:
            continue
        for binary in cache.binaries(key[0], key[1], version):
            for entry in _package_class_entries(binary, package_path)[:MAX_BEHAVIOUR_CLASSES]:
                data = read_class_bytes(binary, entry)
                if not data or needle not in data:
                    continue
                class_name = entry[:-len(".class")].replace("/", ".")
                result = disassemble_method(data, class_name, name, javap)
                if result.get("status") != "available":
                    continue
                facts = summarize_bytecode_behaviour(result["methods"][0]["disassembly"])
                guarantees, caveats = behaviour_consequences(facts)
                return {
                    "symbol": symbol, "basis": "library_bytecode",
                    "artifact": f"{key[0]}:{key[1]}:{version}", "class": class_name,
                    "facts": facts, "derived_guarantees": guarantees, "caveats": caveats,
                    "bound": (
                        "Read from the method's own bytecode in the local Gradle "
                        "cache (javap -c); methods it calls are not followed."
                    ),
                }
    return {
        "symbol": symbol, "basis": "unresolved",
        "reason": "declaration_not_found_in_sources_or_cached_artifacts",
    }


def recommend_mechanisms(idioms: dict, contract: dict) -> dict:
    """Choose, per deprecated type, the mechanisms that carry its invariants.

    The planner was told to use the mechanism the repository uses most.
    Gallery uses ensureBackgroundThread in 22 files; it gives no ordering, no
    cancellation handle and runs inline off the main thread, so the plan built
    a queue by hand on top of it and inherited all three defects. Here each
    invariant's `requires` must be covered by some mechanism's guarantees
    (catalogued, or derived from its implementation). The smallest cover is
    picked greedily, preferring on ties what the repository already uses, and
    a platform mechanism is eligible even when unused, since it adds no
    dependency. Mutates and returns `idioms`.
    """
    invariants_by_type = {
        str(item.get("deprecated_type", "")): item.get("invariants") or []
        for item in (contract or {}).get("contracts") or []
    }
    for item in (idioms or {}).get("by_deprecated_type") or []:
        invariants = invariants_by_type.get(str(item.get("deprecated_type", "")), [])
        requirements = sorted({
            requirement for invariant in invariants
            for requirement in invariant.get("requires") or []
        })
        candidates = []
        for mechanism in item.get("mechanisms") or []:
            effective = set(mechanism.get("guarantees") or []) | {
                guarantee
                for behaviour in mechanism.get("implementation_behaviour") or []
                for guarantee in behaviour.get("derived_guarantees") or []
            }
            candidates.append((mechanism, effective))
        uncovered = set(requirements)
        chosen: list[dict] = []
        remaining = list(candidates)
        while uncovered and remaining:
            mechanism, effective = max(remaining, key=lambda pair: (
                len(pair[1] & uncovered),
                int(pair[0].get("production_files", 0)),
                bool(pair[0].get("platform")),
            ))
            covered = effective & uncovered
            if not covered:
                break
            chosen.append({
                "mechanism": mechanism.get("mechanism", ""),
                "covers": sorted(covered),
                "platform": bool(mechanism.get("platform")),
                "production_files": int(mechanism.get("production_files", 0)),
                "imports": list(mechanism.get("imports") or []),
                "note": str(mechanism.get("guarantees_note", "")),
            })
            uncovered -= covered
            remaining = [pair for pair in remaining if pair[0] is not mechanism]
        chosen_names = {entry["mechanism"] for entry in chosen}
        item["recommendation"] = {
            "requirements": requirements,
            "recommended": chosen,
            "uncovered_requirements": sorted(uncovered),
            "not_recommended": [
                {
                    "mechanism": mechanism.get("mechanism", ""),
                    "production_files": int(mechanism.get("production_files", 0)),
                    "missing": sorted(set(requirements) - effective),
                    "caveats": [
                        caveat
                        for behaviour in mechanism.get("implementation_behaviour") or []
                        for caveat in behaviour.get("caveats") or []
                    ],
                }
                for mechanism, effective in candidates
                if mechanism.get("mechanism") not in chosen_names
                and mechanism.get("production_files")
                and set(requirements) - effective
            ],
            "basis": (
                "Each invariant's requirements matched against catalogued "
                "guarantees and guarantees derived from implementations; the "
                "smallest greedy cover, ties broken by repository usage."
            ),
        }
    return idioms


def repository_replacement_idioms(repo_root: Path, dependency_finding: dict,
                                  source_cache: dict | None = None,
                                  roles: list[dict] | None = None,
                                  resolve_implementations: bool = True) -> dict:
    """Mechanisms the repository already uses for each deprecated type's role."""
    finding = dependency_finding or {}
    types = [str(item) for item in finding.get("deprecated_types") or [] if item]
    if finding.get("kind") != "api_migration" or not types:
        return {}
    sources = _relative_sources(repo_root, source_cache)
    roles = load_replacement_roles() if roles is None else roles
    results = []
    for type_name in types:
        matching, mechanisms = _mechanisms_for(type_name, finding, roles)
        found = []
        for name, patterns, entry in mechanisms:
            files, production, occurrences, examples = set(), set(), 0, []
            imports: Counter = Counter()
            for relative, text in sources.items():
                # Whole-file prefilter first: most files match nothing, and
                # splitting every file into lines for every mechanism is the
                # expensive part.
                if not any(re.search(pattern.pattern, text, re.MULTILINE)
                           for pattern in patterns):
                    continue
                lines = text.splitlines()
                imported = _imported_names(lines)
                for number, line in enumerate(lines, 1):
                    match = next(
                        (found_match for pattern in patterns
                         for found_match in [pattern.search(line)] if found_match),
                        None,
                    )
                    if not match:
                        continue
                    occurrences += 1
                    files.add(relative)
                    if not is_test_path(relative):
                        production.add(relative)
                    for identifier in re.findall(r"[A-Za-z_$][\w$]*", match.group(0)):
                        if identifier in imported:
                            imports[imported[identifier]] += 1
                    if len(examples) < MAX_EXAMPLES:
                        examples.append({
                            "path": relative, "line": number, "text": line.strip()[:200],
                        })
            if occurrences:
                found.append({
                    "mechanism": name, "occurrences": occurrences,
                    "files": len(files), "production_files": len(production),
                    "examples": examples,
                    # The import that makes the mechanism resolvable, as
                    # this repository writes it. Gallery's patch called
                    # ensureBackgroundThread from the examples above and
                    # guessed org.fossify.commons.extensions for it; every
                    # file in the repository imports it from
                    # org.fossify.commons.helpers, and the build failed on
                    # that one line.
                    "imports": [line for line, _count in imports.most_common(MAX_EXAMPLES)],
                    "platform": bool(entry.get("platform")),
                    "guarantees": list(entry.get("guarantees") or []),
                    "guarantees_note": str(entry.get("guarantees_note", "")),
                })
        found.sort(key=lambda item: (-item["production_files"], -item["occurrences"]))
        present = {item["mechanism"] for item in found}
        for name, _patterns, entry in mechanisms:
            # A platform mechanism needs no new dependency, so it is a
            # candidate even where the repository does not use it yet.
            if entry.get("platform") and name not in present:
                found.append({
                    "mechanism": name, "occurrences": 0, "files": 0,
                    "production_files": 0, "examples": [], "imports": [],
                    "platform": True,
                    "guarantees": list(entry.get("guarantees") or []),
                    "guarantees_note": str(entry.get("guarantees_note", "")),
                })
        if resolve_implementations:
            declarations = None
            for mechanism in found:
                if mechanism.get("platform"):
                    continue
                behaviours = []
                for import_line in (mechanism.get("imports") or [])[:2]:
                    if declarations is None and not import_line.strip().startswith(
                            tuple(f"import {prefix}" for prefix in _PLATFORM_PACKAGES)):
                        try:
                            from cognalys.extraction.dependency_context import collect_declarations
                            declarations = collect_declarations(Path(repo_root))["declarations"]
                        except Exception as exc:  # noqa: BLE001 -- behaviour is optional evidence
                            _LOG.warning("dependency declarations not read: %s", exc)
                            declarations = []
                    behaviour = implementation_behaviour(
                        repo_root, import_line, sources, declarations,
                    )
                    if behaviour:
                        behaviours.append(behaviour)
                if behaviours:
                    mechanism["implementation_behaviour"] = behaviours
        results.append({
            "deprecated_type": type_name,
            "roles": [
                {"role": role.get("role", ""), "summary": role.get("summary", "")}
                for role in matching
            ],
            "mechanisms": found,
        })
    return {
        "status": "found" if any(item["mechanisms"] for item in results) else "none_found",
        "by_deprecated_type": results,
        "bound": (
            "Lexical counts of mechanisms catalogued for the deprecated API's "
            "role, plus the replacement the finding's message names. They show "
            "which idioms this repository already uses and where; they do not "
            "show that any of them is equivalent to the deprecated API."
        ),
    }


def _class_body(text: str, start: int) -> str:
    """From the first brace at or after `start` to its matching brace."""
    opening = text.find("{", start)
    if opening < 0:
        return ""
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[opening:index + 1]
    return text[opening:]


def _overridden(body: str) -> list[str]:
    return sorted({
        item.group("kotlin") or item.group("java") for item in _OVERRIDE.finditer(body)
    })


def deprecated_usage_contract(repo_root: Path, dependency_finding: dict,
                              source_cache: dict | None = None,
                              source_file_path: str = "",
                              roles: list[dict] | None = None) -> dict:
    """How the repository uses each deprecated type: subclasses, overridden
    callbacks, and every call site that constructs, starts, cancels, registers
    or sends through it.

    When the reported file declares a subclass, the contract is that
    subclass and its uses. TowerCollector has two AsyncTask subclasses; the
    finding is on UpdateCheckAsyncTask, and MainMapFragment's loader task is a
    separate finding that a migration of this one must not swallow. The other
    subclasses are still listed, outside the scope.
    """
    finding = dependency_finding or {}
    types = [str(item) for item in finding.get("deprecated_types") or [] if item]
    if finding.get("kind") != "api_migration" or not types:
        return {}
    sources = _relative_sources(repo_root, source_cache)
    contracts = []
    for type_name in types:
        simple = type_name.rsplit(".", 1)[-1]
        if not simple:
            continue
        subclass_pattern = re.compile(
            rf"\bclass\s+(?P<name>[A-Za-z_$][\w$]*)[^{{;]*?"
            rf"(?:\bextends\s+|:\s*)(?:[\w.]+\.)?{re.escape(simple)}\b"
        )
        anonymous_pattern = re.compile(
            rf"\b(?:new\s+|object\s*:\s*)(?:[\w.]+\.)?{re.escape(simple)}\s*(?:<[^;{{]*?>)?\s*\([^;{{]*?\)\s*\{{"
        )
        subclasses = []
        body_calls: dict[tuple, set] = {}
        for relative, text in sources.items():
            if simple not in text:
                continue
            for match, body in [
                *((item, _class_body(text, item.end()))
                  for item in subclass_pattern.finditer(text)),
                *((item, _class_body(text, item.end() - 1))
                  for item in anonymous_pattern.finditer(text)),
            ]:
                start_line = text.count("\n", 0, match.start()) + 1
                name = match.groupdict().get("name") or "(anonymous)"
                subclasses.append({
                    "name": name, "path": relative, "line": start_line,
                    "overridden_members": _overridden(body),
                })
                # Inherited members the subclass calls unqualified --
                # Gallery's stopFetching() calls cancel(true) -- are uses of
                # the deprecated API too, invisible to the call-site scan.
                body_calls[(relative, start_line)] = set(
                    re.findall(r"(?<![\w$.])([a-z][\w$]*)\s*\(", body)
                )
        declaration_lines = {(item["path"], item["line"]) for item in subclasses}
        reported = [
            item for item in subclasses
            if source_file_path and item["path"] == source_file_path
        ]
        restrict_to = ""
        if reported:
            in_scope = reported
            named = [item["name"] for item in reported if item["name"] != "(anonymous)"]
            names = named or [simple]
            if not named:
                # Only anonymous subclasses in the reported file: their uses
                # are in that file, not wherever else the type is used.
                restrict_to = source_file_path
        else:
            in_scope = subclasses
            names = [simple, *dict.fromkeys(
                item["name"] for item in subclasses if item["name"] != "(anonymous)"
            )]
        other_subclasses = [
            {key: item[key] for key in ("name", "path", "line")}
            for item in subclasses if item not in in_scope
        ]
        name_pattern = re.compile(
            r"(?<![\w$])(?P<name>" + "|".join(re.escape(name) for name in names) + r")(?![\w$])"
        )
        call_sites, members = [], Counter()
        alternatives = "|".join(re.escape(name) for name in names)
        variable_patterns = [
            # x = Name(...), val x = new Name<...>(...)
            re.compile(
                r"(?P<var>[A-Za-z_$][\w$]*)\s*(?::\s*[\w$.<>?, ]+)?\s*=\s*"
                rf"(?:new\s+)?(?:[\w.]+\.)?(?:{alternatives})\b"
            ),
            # Kotlin: var x: Name? = null
            re.compile(rf"(?P<var>[A-Za-z_$][\w$]*)\s*:\s*(?:[\w.]+\.)?(?:{alternatives})\b"),
            # Java: Name<...> x = ... / Name x;
            re.compile(
                rf"(?<![\w$.])(?:{alternatives})\s*(?:<[^;=]*?>)?\s+"
                r"(?P<var>[A-Za-z_$][\w$]*)\s*[=;,)]"
            ),
        ]
        for relative, text in sources.items():
            if restrict_to and relative != restrict_to:
                continue
            if not name_pattern.search(text):
                continue
            lines = text.splitlines()
            # Variables first, over the whole file: a field is often used
            # (mTask?.cancel()) on a line above the one that assigns it.
            variables: set[str] = {
                match.group("var")
                for line in lines if not _IMPORT_OR_PACKAGE.match(line)
                for pattern in variable_patterns
                for match in pattern.finditer(line)
                if match.group("var") not in names
            }
            for number, line in enumerate(lines, 1):
                if _IMPORT_OR_PACKAGE.match(line) or (relative, number) in declaration_lines:
                    continue
                site_members: list[str] = []
                symbol = ""
                match = name_pattern.search(line)
                if match:
                    symbol = match.group("name")
                    # Name.class.getSimpleName() reads the class literal; it
                    # is a reference, not a use of the API.
                    if re.match(r"\s*(?:\.\s*class\b|::\s*class\b)", line[match.end():]):
                        site_members = []
                    else:
                        site_members = [
                            item.group("member")
                            for item in _CALLED_MEMBER.finditer(line, match.end())
                        ]
                else:
                    for variable in variables:
                        usage = re.search(
                            rf"(?<![\w$.]){re.escape(variable)}[?!]*\.\s*(?P<member>[A-Za-z_$][\w$]*)\s*\(",
                            line,
                        )
                        if usage:
                            symbol = variable
                            site_members = [usage.group("member")]
                            break
                if not symbol:
                    continue
                members.update(site_members)
                if len(call_sites) < MAX_CALL_SITES:
                    call_sites.append({
                        "path": relative, "line": number, "symbol": symbol,
                        "members": site_members, "text": line.strip()[:200],
                    })
        files = sorted({item["path"] for item in [*in_scope, *call_sites]})
        # The documented behaviour of exactly what this code uses: a member
        # it calls, or a callback its subclass overrides. Gallery's
        # replacement moved AsyncTask's serial execution onto a cached thread
        # pool and let doInBackground's exceptions vanish into an unread
        # Future -- two behaviours AsyncTask documents, neither of which the
        # generation had in front of it.
        used = set(members) | {
            member for item in in_scope for member in item.get("overridden_members") or []
        } | {
            call for item in in_scope
            for call in body_calls.get((item["path"], item["line"]), set())
        }
        invariants = [
            {
                "when": entry.get("when", ""),
                "requires": list(entry.get("requires") or []),
                "invariant": entry.get("invariant", ""),
                "source": entry.get("source", ""),
            }
            for role in (load_replacement_roles() if roles is None else roles)
            for entry in role.get("documented_semantics") or []
            if entry.get("applies_to") == type_name
            and (entry.get("when") == "*" or entry.get("when") in used)
        ]
        contracts.append({
            "deprecated_type": type_name,
            "invariants": invariants,
            "subclasses": in_scope[:MAX_SUBCLASSES],
            "other_subclasses_not_in_scope": other_subclasses[:MAX_SUBCLASSES],
            "call_sites": call_sites,
            "members_called": dict(members.most_common()),
            "files": files,
            "production_files": [path for path in files if not is_test_path(path)],
        })
    return {
        "status": "found" if any(item["files"] for item in contracts) else "none_found",
        "contracts": contracts,
        "bound": (
            "Lexical: subclasses and anonymous subclasses of the deprecated "
            "type, the members they override, and the lines that name the type, "
            "a subclass, or a variable assigned from one, with the members "
            "called there. Overridden members are read from the subclass's "
            "own body, nested classes included. When the reported file "
            "declares a subclass, only that subclass and its uses are in "
            "scope. Reflection and aliases through other types are not "
            "followed."
        ),
    }


def contract_files(contract: dict) -> list[str]:
    """Production files a migration of the deprecated usages has to edit."""
    return sorted({
        path for item in (contract or {}).get("contracts") or []
        for path in item.get("production_files") or []
    })
