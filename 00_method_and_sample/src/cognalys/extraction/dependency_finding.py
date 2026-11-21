"""What a dependency or deprecation finding names, and whether the checkout has it.

A crypto finding is about the code at the reported method. An outdated or
deprecated dependency finding is not: its subject is in the message --
"Using okhttp3 v3.12.0. Update to v4.10.0 or higher.", "Found
android.support.v4.app.Fragment. Migrate to androidx.fragment.app.Fragment."
-- and the reported class is only where a tool happened to attach it. The
pipeline attributed these by class name, which says nothing about the
library, and never checked whether the checkout still contained what the
message names.

data/processed/bibliotecas_deprecadas_pr.csv shows why that matters. Its
DuckDuckGo row reports okhttp3 3.12.0 against a checkout whose
versions.properties pins 4.12.0, above the stated target; its Fenix row
reports Retrofit 1.9.0 against a checkout that neither declares nor imports
Retrofit. Both would have been sent to the model to discover that.

This module reads the message with patterns that do not depend on any one
library, checks the named library against the declared dependencies and the
named types against the source imports, and records the outcome. It decides
nothing about whether an upgrade is safe; it decides only whether there is
anything in this checkout to upgrade.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from cognalys.extraction.artifact_cache import version_key
from cognalys.extraction.dependency_context import collect_declarations, resolve_dependency_graph
from cognalys.extraction.repository_index import build_repository_index


MAX_RECORDED = 20

_TRIGGER = re.compile(
    r"\b(?:deprecat\w*|outdated|obsolete|end[- ]of[- ]life|EOL|"
    r"update\s+to|upgrade\s+to|updating\s+to|migrat\w*\s+to|replace\w*\s+with)\b"
    r"|\bUsing\s+[A-Za-z][\w.-]*\s+v?\d",
    re.IGNORECASE,
)
# Where the message stops describing what is used and starts describing what
# should replace it. "Using" is not a marker: `\buse\b` does not match it.
_REPLACEMENT_MARKER = re.compile(
    r"\b(?:update|upgrade|updating|upgrading|migrate|migrating|use|"
    r"replace|replacing|consider)\b",
    re.IGNORECASE,
)
_COORDINATE = re.compile(
    r"\b(?P<group>[a-z][\w-]*(?:\.[\w-]+)+):(?P<artifact>[A-Za-z][\w-]*)"
    r"(?::(?P<version>\d[\w.+-]*))?"
)
_QUALIFIED_TYPE = re.compile(r"\b(?:[a-z][a-z0-9_]*\.)+[A-Z][\w$]*\b")
_QUALIFIED_PACKAGE = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\b")
_NAMED_VERSION = re.compile(
    r"(?P<name>[A-Za-z][\w.-]*)\s+v?(?P<version>\d+(?:\.\d+)*(?:\.x)?)(?!\w)(?!\.\d)"
)
_BARE_VERSION = re.compile(r"(?<![\w.])v?(?P<version>\d+(?:\.\d+)+|\d+\.x)(?!\w)(?!\.\d)")
_LIBRARY_WORD = re.compile(
    r"(?P<name>[A-Za-z][\w.-]*[A-Za-z0-9])\s+(?:library|libraries)\b", re.IGNORECASE,
)
# Words that sit before a version number without naming a library.
_NOT_LIBRARY_NAMES = frozenset({
    "to", "v", "version", "than", "from", "or", "and", "at", "of", "the",
    "using", "update", "upgrade", "updating", "api", "level", "sdk", "java",
    "is", "found", "in", "on", "with", "for",
})

ANALYSABLE_STATUSES = frozenset({
    "confirmed", "declared_version_differs", "declared_version_unresolved",
    "used_but_not_declared",
})
NOT_APPLICABLE_STATUSES = frozenset({"already_satisfied", "not_found"})


def _flat(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _library_tokens(name: str) -> set[str]:
    """okhttp3 -> {okhttp3, okhttp}; joda-time -> {jodatime}."""
    tokens = {_flat(name), _flat(re.sub(r"\d+$", "", str(name)))}
    return {token for token in tokens if len(token) >= 4}


def _unique(items: list[dict], key) -> list[dict]:
    seen, result = set(), []
    for item in items:
        marker = key(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def parse_finding_message(message: str) -> dict:
    """The library, version, deprecated types and target a message names.

    Returns {} for a message that is not about a dependency or a deprecation,
    so crypto findings ("expecting one of PKIX,SunX509 but found .") are left
    exactly as they were.
    """
    text = " ".join(str(message or "").split())
    if not text or not _TRIGGER.search(text):
        return {}
    marker = _REPLACEMENT_MARKER.search(text)
    current = text[:marker.start()] if marker else text
    replacement = text[marker.start():] if marker else ""

    subjects: list[dict] = []
    for match in _COORDINATE.finditer(current):
        subjects.append({
            "name": match.group("artifact"),
            "coordinate": f"{match.group('group')}:{match.group('artifact')}",
            "version": (match.group("version") or "").rstrip("."),
        })
    covered = {item["name"].lower() for item in subjects}
    for match in _NAMED_VERSION.finditer(current):
        name = match.group("name").strip(".(")
        if name.lower() in _NOT_LIBRARY_NAMES or name.lower() in covered:
            continue
        if _QUALIFIED_TYPE.fullmatch(name):
            continue
        subjects.append({"name": name, "version": match.group("version")})
        covered.add(name.lower())
    for match in _LIBRARY_WORD.finditer(current):
        name = match.group("name")
        if name.lower() not in covered and name.lower() not in _NOT_LIBRARY_NAMES:
            subjects.append({"name": name, "version": ""})
            covered.add(name.lower())

    deprecated_types = list(dict.fromkeys(_QUALIFIED_TYPE.findall(current)))

    target_version, target_name = "", ""
    for match in _NAMED_VERSION.finditer(replacement):
        name = match.group("name").strip(".(")
        if name.lower() in _NOT_LIBRARY_NAMES:
            continue
        target_name, target_version = name, match.group("version")
        break
    if not target_version:
        bare = _BARE_VERSION.search(replacement)
        target_version = bare.group("version") if bare else ""
    replacement_types = list(dict.fromkeys(_QUALIFIED_TYPE.findall(replacement)))
    replacement_packages = [
        package for package in dict.fromkeys(_QUALIFIED_PACKAGE.findall(replacement))
        if not any(kind.startswith(package + ".") for kind in replacement_types)
    ]

    reported_version = next(
        (item["version"] for item in subjects if item["version"]), ""
    )
    if subjects and reported_version and target_version:
        kind = "dependency_version"
    elif subjects or deprecated_types:
        kind = "api_migration"
    else:
        return {}
    return {
        "kind": kind,
        "subjects": subjects,
        "deprecated_types": deprecated_types,
        "reported_version": reported_version,
        "target": {
            "name": target_name,
            "version": target_version,
            "types": replacement_types,
            "packages": replacement_packages,
        },
        "change_scale": change_scale(reported_version, target_version),
    }


def _major(version: str) -> int:
    match = re.match(r"\d+", str(version or ""))
    return int(match.group(0)) if match else -1


def change_scale(current: str, target: str) -> str:
    """major when the target crosses a major version, which is an API migration."""
    if _major(current) < 0 or _major(target) < 0:
        return "unknown"
    return "major" if _major(target) > _major(current) else "minor"


def _resolvable(version: str) -> bool:
    return bool(re.match(r"^\d", str(version or "")))


def version_satisfies(declared: str, target: str) -> bool:
    if str(target).endswith(".x"):
        return _major(declared) >= _major(target)
    return version_key(declared) >= version_key(target)


def declaration_matches(item: dict, subject: dict) -> bool:
    coordinate = subject.get("coordinate", "")
    if coordinate:
        group, _, artifact = coordinate.partition(":")
        return item.get("group") == group and item.get("artifact") == artifact
    tokens = _library_tokens(subject.get("name", ""))
    artifact = str(item.get("artifact", ""))
    group_tail = str(item.get("group", "")).split(".")[-1]
    names = {
        _flat(artifact), _flat(re.sub(r"\d+$", "", artifact)),
        _flat(group_tail), _flat(re.sub(r"\d+$", "", group_tail)),
    }
    return bool(tokens & names)


def source_usages(source_cache: dict, repo_root: Path, parsed: dict) -> list[dict]:
    """Source lines that import or name what the finding says is used."""
    # Whole names only: android.os.AsyncTask must not match
    # android.os.AsyncTaskLoader, nor android.preference.PreferenceManager
    # match androidx.preference.PreferenceManager.
    type_patterns = [
        (name, re.compile(rf"(?<![\w.]){re.escape(name)}(?![\w$])"))
        for name in parsed.get("deprecated_types") or []
    ]
    tokens = set()
    for subject in parsed.get("subjects") or []:
        tokens |= _library_tokens(subject.get("name", ""))
    usages: list[dict] = []
    for path, content in source_cache.items():
        for number, line in enumerate(str(content).splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            matched = next(
                (name for name, pattern in type_patterns if pattern.search(stripped)), "",
            )
            if not matched and stripped.startswith("import ") and tokens:
                flattened = _flat(stripped)
                matched = next((token for token in tokens if token in flattened), "")
            if not matched:
                continue
            try:
                relative = Path(path).relative_to(repo_root).as_posix()
            except ValueError:
                relative = str(path)
            usages.append({
                "source_file_path": relative, "line": number,
                "matched": matched, "text": stripped[:200],
            })
            if len(usages) >= MAX_RECORDED:
                return usages
    return usages


def module_build_files(repo_root: Path, source_file_path: str) -> list[str]:
    """The reported module's build script, and the project's shared version files.

    Replacing a deprecated API with one from a library means declaring that
    library, and the module the reported source belongs to is where.
    """
    root = Path(repo_root)
    found: list[str] = []
    if source_file_path:
        start = (root / source_file_path).parent
        for directory in [start, *start.parents]:
            if directory != root and root not in directory.parents:
                break
            script = next(
                (directory / name for name in ("build.gradle.kts", "build.gradle")
                 if (directory / name).is_file()),
                None,
            )
            if script is not None:
                found.append(script.relative_to(root).as_posix())
                break
            if directory == root:
                break
    for shared in ("gradle/libs.versions.toml", "versions.properties"):
        if (root / shared).is_file() and shared not in found:
            found.append(shared)
    return found


def ground_finding(parsed: dict, declarations: list[dict], version_entries: list[dict],
                   usages: list[dict]) -> dict:
    """Whether the checkout still contains what the finding names."""
    candidates = [*declarations, *version_entries]
    matched = _unique(
        [item for item in candidates
         if any(declaration_matches(item, subject) for subject in parsed.get("subjects") or [])],
        key=lambda item: (item.get("source_file_path"), item.get("line")),
    )
    resolvable = [item for item in matched if _resolvable(item.get("version_expression", ""))]
    unresolved = [item for item in matched if not _resolvable(item.get("version_expression", ""))]
    target = str((parsed.get("target") or {}).get("version", ""))
    reported = str(parsed.get("reported_version", ""))

    if parsed.get("kind") == "dependency_version":
        # Already satisfied only when every matching declaration pins a
        # version and every one of them meets the target. A declaration whose
        # version could not be read (a BOM-managed coordinate, an unresolved
        # constant) leaves the effective version open, and the finding goes to
        # the analysis instead of being closed on a partial reading.
        if (resolvable and target and not unresolved
                and all(version_satisfies(item["version_expression"], target)
                        for item in resolvable)):
            status = "already_satisfied"
        elif resolvable and target:
            status = "confirmed"
        elif resolvable:
            status = (
                "confirmed" if any(item["version_expression"] == reported for item in resolvable)
                else "declared_version_differs"
            )
        elif matched:
            status = "declared_version_unresolved"
        elif usages:
            status = "used_but_not_declared"
        else:
            status = "not_found"
    else:
        status = "confirmed" if (usages or matched) else "not_found"

    return {
        "kind": parsed.get("kind", ""),
        "status": status,
        "subjects": parsed.get("subjects", []),
        "deprecated_types": parsed.get("deprecated_types", []),
        "reported_version": reported,
        "target": parsed.get("target", {}),
        "change_scale": parsed.get("change_scale", "unknown"),
        "matched_declarations": [
            {key: item.get(key) for key in (
                "source_file_path", "line", "group", "artifact",
                "version_expression", "declaration", "version_resolved_from",
            ) if item.get(key) not in (None, "")}
            for item in matched[:MAX_RECORDED]
        ],
        "declaration_files": sorted({
            str(item["source_file_path"]) for item in matched if item.get("source_file_path")
        }),
        "usages": usages[:MAX_RECORDED],
        "bound": (
            "Read from the detector message and matched lexically against the "
            "build files, versions.properties and source imports of this "
            "checkout. It establishes what this branch declares and uses; it "
            "does not resolve the Gradle dependency graph, so a version forced "
            "by a platform, a constraint or a transitive path is not seen."
        ),
    }


def package_prefixes(parsed: dict, matched: list[dict], declarations: list[dict]) -> list[str]:
    """Package prefixes the named library's imports start with, from its group.

    RxJava 2 is declared as io.reactivex.rxjava2:rxjava and imported as
    io.reactivex.Observable: no import line contains "rxjava", so the token
    match in source_usages found 17 test imports in AmazeFileManager and none
    of its production code, and the analysis could not size the migration.
    A group whose last segment is the library's own name (rxjava2) also
    publishes under its parent (io.reactivex) -- unless another declared
    library shares that parent, as com.squareup does for okhttp3 and
    retrofit2, in which case the parent says nothing about which one.
    """
    tokens = set()
    for subject in parsed.get("subjects") or []:
        tokens |= _library_tokens(subject.get("name", ""))
    matched_groups = {str(item.get("group", "")) for item in matched if item.get("group")}
    prefixes: list[str] = []
    for group in sorted(matched_groups):
        prefixes.append(group)
        segments = group.split(".")
        if len(segments) < 3 or _flat(re.sub(r"\d+$", "", segments[-1])) not in tokens:
            continue
        parent = ".".join(segments[:-1])
        shared = any(
            str(item.get("group", "")).startswith(parent + ".")
            and str(item.get("group", "")) not in matched_groups
            for item in declarations
        )
        if not shared:
            prefixes.append(parent)
    return list(dict.fromkeys(prefixes))


def usage_inventory(source_cache: dict, repo_root: Path, parsed: dict,
                    prefixes: list[str]) -> dict:
    """Every import of what the finding names, counted without a cap.

    source_usages keeps the first twenty lines as examples. Sizing a
    migration needs the whole count -- 331 imports across 182 files in
    commons-app is an issue, three files are a pull request.
    """
    type_patterns = [
        re.compile(rf"^\s*import\s+{re.escape(name)}(?![\w$])")
        for name in parsed.get("deprecated_types") or []
    ]
    prefix_patterns = [re.compile(rf"^\s*import\s+(?:static\s+)?{re.escape(prefix)}\.")
                       for prefix in prefixes]
    tokens = set()
    for subject in parsed.get("subjects") or []:
        tokens |= _library_tokens(subject.get("name", ""))
    files, production, test, lines = set(), set(), set(), 0
    for path, content in source_cache.items():
        try:
            relative = Path(path).relative_to(repo_root).as_posix()
        except ValueError:
            relative = str(path)
        hits = 0
        for line in str(content).splitlines():
            stripped = line.strip()
            if not stripped.startswith("import "):
                continue
            if (any(pattern.match(stripped) for pattern in type_patterns)
                    or any(pattern.match(stripped) for pattern in prefix_patterns)
                    or (tokens and any(token in _flat(stripped) for token in tokens))):
                hits += 1
        if not hits:
            continue
        lines += hits
        files.add(relative)
        is_test = bool(re.search(
            r"(?:^|/)(?:test|androidTest|testShared|sharedTest)\w*/|Tests?\.(?:java|kt)$",
            relative,
        ))
        (test if is_test else production).add(relative)
    return {
        "import_lines": lines,
        "files": len(files),
        "production_files": len(production),
        "test_files": len(test),
        "package_prefixes": prefixes,
        "production_file_sample": sorted(production)[:MAX_RECORDED],
    }


MAX_BOUNDED_MIGRATION_FILES = 10


def migration_scale(change: str, inventory: dict) -> str:
    """bounded or large, for a change that crosses a major version."""
    if change != "major":
        return "not_a_major_change"
    return (
        "bounded" if int(inventory.get("production_files", 0))
        <= MAX_BOUNDED_MIGRATION_FILES else "large"
    )


def effective_resolution(repo_root: Path, matched: list[dict]) -> dict:
    """The version Gradle selects for the matched coordinates, read offline.

    Both RxJava adjudications in run_20260913_004434 blocked on "the declared
    version does not establish the effective resolved version". The resolved
    tree was available offline for these checkouts; nothing asked for it.
    """
    coordinates = {
        (str(item.get("group", "")), str(item.get("artifact", "")))
        for item in matched if item.get("group") and item.get("artifact")
    }
    if not coordinates:
        return {"status": "not_applicable"}
    if os.environ.get("TCC_SKIP_GRADLE_RESOLUTION"):
        return {"status": "skipped", "reason": "TCC_SKIP_GRADLE_RESOLUTION is set"}
    graph = resolve_dependency_graph(Path(repo_root))
    if graph.get("status") != "available":
        return {"status": "unavailable", "reason": graph.get("reason", "")}
    selected = [
        {
            "coordinate": f"{node['group']}:{node['artifact']}",
            "version": node.get("version_expression", ""),
            "direct": node.get("direct", False),
            "dependency_path": node.get("dependency_path", []),
        }
        for node in graph.get("nodes") or []
        if (node.get("group"), node.get("artifact")) in coordinates
    ]
    return {
        "status": "resolved" if selected else "not_in_resolved_graph",
        "selected": selected,
        "bound": (
            "`gradle dependencies --offline` over every configuration of the "
            "project; the shallowest occurrence of each coordinate is kept, so "
            "a configuration that selects a different version is not shown."
        ),
    }


def collect_dependency_finding(repo_root: Path, message: str,
                               source_file_path: str = "") -> dict:
    parsed = parse_finding_message(message)
    if not parsed:
        return {}
    repo_root = Path(repo_root)
    index = build_repository_index(repo_root)
    declared = collect_declarations(repo_root, index)
    usages = source_usages(index["source_cache"], repo_root, parsed)
    grounded = ground_finding(
        parsed, declared["declarations"], declared["version_entries"], usages,
    )
    matched = grounded.get("matched_declarations") or []
    inventory = usage_inventory(
        index["source_cache"], repo_root, parsed,
        package_prefixes(parsed, matched, declared["declarations"]),
    )
    result = {
        **grounded,
        "module_build_files": module_build_files(repo_root, source_file_path),
        "usage_inventory": inventory,
        "migration_scale": migration_scale(grounded.get("change_scale", ""), inventory),
    }
    if grounded.get("kind") == "dependency_version" and grounded.get("status") in ANALYSABLE_STATUSES:
        result["effective_resolution"] = effective_resolution(repo_root, matched)
    return result
