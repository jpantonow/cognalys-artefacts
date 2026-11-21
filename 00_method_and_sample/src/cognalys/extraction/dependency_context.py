"""Collect bounded dependency evidence for findings whose class is external."""

from __future__ import annotations

import re
import os
import subprocess
from pathlib import Path

from cognalys.extraction.artifact_cache import (
    locate_method_bytecode,
    locate_repository_binary_method,
    trace_owner_through_metadata,
    upstream_repository_for,
)
from cognalys.extraction.repository_index import build_repository_index


BUILD_NAMES = {
    "build.gradle", "build.gradle.kts", "libs.versions.toml", "pom.xml",
    "gradle.lockfile", "settings.gradle", "settings.gradle.kts",
}
SKIPPED = {".git", ".gradle", "build", "dist", "node_modules"}
COORDINATE = re.compile(
    r"(?P<group>[A-Za-z0-9_.-]+):(?P<artifact>[A-Za-z0-9_.-]+)"
    r"(?::(?P<version>[A-Za-z0-9_.+${}-]+))?"
)

# A dependency named through a constant rather than written out:
# `implementation Libs.okhttp`. The buildSrc object and the version catalog
# are how a modern Android project declares dependencies, so a scanner that
# only reads literal coordinates finds nothing in most of them.
# Eventfahrplan declares `implementation Libs.okhttp` and the coordinate is
# two constant lookups away in one buildSrc file:
#
#     const val okhttp = "com.squareup.okhttp3:okhttp:${Versions.okhttp}"
#     const val okhttp = "5.3.2"
#
# and the run recorded "no dependency artifact was identified for it".
_CONFIGURATION = (
    r"implementation|api|compileOnly|runtimeOnly|annotationProcessor|kapt|ksp"
    r"|classpath|testImplementation|androidTestImplementation|debugImplementation"
)
# Where a coordinate is allowed to be read from. A colon between two words
# is not rare in a build file, and reading one wherever it appeared is how
# at.linuxtage.Eventfahrplan's dependency_context came back listing
# "-Xlint:unchecked" and "-Xlint:deprecation" off a compilerArgs line, and
# "HH:mm" out of the date format "yyyy-MM-dd'T'HH:mm'Z'", as candidate
# artifacts -- while the OkHttp declaration the finding was actually about
# went unfound. Quoting is no filter: both of those sit inside string
# literals. What distinguishes a coordinate is the declaration around it.
DEPENDENCY_LINE = re.compile(
    r"\b(?:" + _CONFIGURATION + r")\b\s*[(\s\"']"
    r"|\b(?:module|group|name)\s*=\s*[\"']"
    r"|<artifactId>|<groupId>"
)
# A lock file has no declaration syntax: every line is a coordinate.
COORDINATE_ONLY_FILES = {"gradle.lockfile"}

# A version catalog entry that keeps the group and the name in separate
# keys. The other catalog form writes `module = "group:artifact"`, which the
# coordinate pattern reads directly; this one has no coordinate anywhere in
# the text, so it was invisible. dev.dettmer.simplenotes declares
#
#     androidx-security-crypto = { group = "androidx.security",
#                                  name = "security-crypto",
#                                  version.ref = "securityCrypto" }
#
# and its dependency_context came back with zero declared dependencies.
CATALOG_LIBRARY = re.compile(
    r"\bgroup\s*=\s*[\"'](?P<group>[A-Za-z0-9_.-]+)[\"']"
    r"[^\n]{0,120}?\bname\s*=\s*[\"'](?P<artifact>[A-Za-z0-9_.-]+)[\"']"
)
CATALOG_VERSION_REF = re.compile(
    r"\bversion\s*\.\s*ref\s*=\s*[\"'](?P<ref>[A-Za-z0-9_.-]+)[\"']"
)
CATALOG_VERSION_LITERAL = re.compile(
    r"\bversion\s*=\s*[\"'](?P<version>[A-Za-z0-9_.+-]+)[\"']"
)
CATALOG_VERSION_ENTRY = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_.-]+)\s*=\s*[\"'](?P<version>[A-Za-z0-9_.+-]+)[\"']"
)


def collect_catalog_versions(content: str) -> dict:
    """The `[versions]` table of a Gradle version catalog, by alias."""
    versions: dict[str, str] = {}
    in_versions = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_versions = stripped.lower().startswith("[versions")
            continue
        if not in_versions:
            continue
        match = CATALOG_VERSION_ENTRY.match(line)
        if match:
            versions[match.group("name")] = match.group("version")
    return versions


CONSTANT_DEPENDENCY = re.compile(
    r"\b(?:" + _CONFIGURATION + r")\s*[(\s]\s*"
    r"(?:(?P<owner>[A-Za-z_][\w$]*)\.)?(?P<name>[A-Za-z_][\w$]*)\s*[)\s]?"
)
CONSTANT_VALUE = re.compile(
    r"\b(?:const\s+)?val\s+(?P<name>[A-Za-z_][\w$]*)\s*(?::[^=]{0,80})?=\s*"
    r"[\"'](?P<value>[^\"']{1,200})[\"']"
)
_INTERPOLATION = re.compile(
    r"\$\{?(?:(?P<owner>[A-Za-z_][\w$]*)\.)?(?P<name>[A-Za-z_][\w$]*)\}?"
)
_CONTAINER = re.compile(
    r"\b(?:object|class|interface)\s+(?P<owner>[A-Za-z_][\w$]*)"
)
MAX_CONSTANT_HOPS = 3

RESOLVED_COORDINATE = re.compile(
    r"(?P<group>[A-Za-z0-9_.-]+):(?P<artifact>[A-Za-z0-9_.-]+):"
    r"(?P<requested>[^\s()]+)(?:\s+->\s+(?P<selected>[^\s()]+))?"
)


# Segments that identify nothing: almost every Java package and Maven group
# begins with one, so matching on them scores every artifact against every
# class. _tokens excluded them from the start; the group-prefix score below
# did not, and org.mifos.mobilebanking.api.SelfServiceOkHttpClient was
# therefore offered org.jetbrains.compose.runtime:runtime and three
# androidx.lifecycle artifacts as dependency candidates, all on the strength
# of sharing "org" (run_20260909_100446). Both paths read it from here now.
UNINFORMATIVE_SEGMENTS = frozenset({
    "com", "org", "net", "android", "www", "github", "io", "dev", "app",
})


# The package of a third-party class is, for almost every JVM library, the
# Maven group that ships it: com.google.crypto.tink.integration.android.*
# comes from the com.google.crypto.tink group. That costs nothing to read
# and needs no Gradle, which matters because resolving the real dependency
# tree needs a warm build cache and a finding that never reaches the build
# stage never has one -- dev.dettmer.simplenotes fails `gradlew dependencies
# --offline` because the Android plugin itself was never downloaded here.
#
# It does not name the artifact or the version. What it does establish is
# the one thing the old record got wrong: the finding is in a named third
# party's code, and whether this project declares that party at all.
_REVERSE_DOMAIN_HEADS = ("com", "org", "net", "io", "dev", "me", "cc")
MAX_PACKAGE_SEGMENTS = 3

# Segments that describe how a library lays out its own code rather than
# which library it is. The prefix used to stop on them, and
# okhttp3.internal.platform.Platform was attributed to "okhttp3.internal" --
# a directory inside OkHttp, not OkHttp.
_LAYOUT_SEGMENTS = frozenset({"internal", "impl", "shaded"})

# Package roots that are the platform or the language, not a third party.
# No declared artifact carries java.* or kotlin.*, so looking for one that
# does would only ever turn up noise.
_PLATFORM_ROOTS = frozenset({
    "java", "javax", "jdk", "sun", "kotlin", "kotlinx",
    "android", "androidx", "dalvik", "libcore",
})


def owning_package_prefix(class_name: str) -> str:
    """The package prefix that identifies the library a class comes from.

    Deliberately not called a Maven group, and not claimed to be one: no
    rule derives a group from a package. okhttp3.internal.platform.Platform
    ships in com.squareup.okhttp3:okhttp, and org.bouncycastle.jce.provider
    in org.bouncycastle:bcprov-jdk18on. What the prefix does identify, and
    identify correctly, is which library the reported code belongs to.
    """
    segments = [
        segment for segment in class_name.replace("$", ".").split(".")
        if segment and segment.islower()
    ]
    if len(segments) < 2:
        return ""
    depth = MAX_PACKAGE_SEGMENTS if segments[0] in _REVERSE_DOMAIN_HEADS else 2
    prefix = segments[:depth]
    while len(prefix) > 1 and prefix[-1] in _LAYOUT_SEGMENTS:
        prefix.pop()
    return ".".join(prefix)


def _is_owner_declaration(item: dict, group: str) -> bool:
    """Whether a declaration is the library that owns `group`'s classes."""
    if not group:
        return False
    head = group.split(".")[0]
    return (
        item["group"] == group
        or item["group"].startswith(group + ".")
        or group.startswith(item["group"] + ".")
        # okhttp3.* is shipped by com.squareup.okhttp3, whose last segment
        # is the package head rather than a prefix of it.
        or item["group"].split(".")[-1] == head
    )


def group_owns_class(group: str, class_name: str) -> bool:
    """Whether a Maven group is the one that ships `class_name`.

    Stricter than _is_owner_declaration, because it is asked of every node of
    a cached dependency tree rather than of the few artifacts a project
    declares. A group owns a class when the class's package lies inside it
    (com.google.crypto.tink ships com.google.crypto.tink.integration.*), or
    when the group's last segment is the package root (com.squareup.okhttp3
    ships okhttp3.*). That second rule never matches the platform or an
    organisation root: com.google.android.material does not ship android.*.
    """
    group = str(group or "").lower()
    outer = class_name.split("$", 1)[0]
    if "." not in group or "." not in outer:
        return False
    package = outer.rsplit(".", 1)[0].lower()
    if package == group or package.startswith(group + "."):
        return True
    root = package.split(".")[0]
    return (
        root not in _PLATFORM_ROOTS
        and root not in _REVERSE_DOMAIN_HEADS
        and group.split(".")[-1] == root
    )


def library_head(class_name: str) -> str:
    """The library's own name as its package root spells it.

    Trailing version digits are dropped, because that is how an artifact
    that integrates the library names it: the package is okhttp3 and the
    Coil integration is coil-network-okhttp, the package is retrofit2 and
    the adapters are adapter-rxjava3 or converter-gson. Comparing the two
    spellings as they stand is what made jellyfin's OkHttp finding look like
    a class with no dependency at all.

    Empty when the root names an organisation (com, org, io...) rather than
    a library, or the platform itself. Guessing a library from
    com.google.crypto would match every artifact with "google" or "crypto"
    in its name, so those are left to the resolved dependency graph.
    """
    segments = [
        segment for segment in class_name.replace("$", ".").split(".")
        if segment and segment.islower()
    ]
    if not segments:
        return ""
    root = segments[0]
    if root in _REVERSE_DOMAIN_HEADS or root in _PLATFORM_ROOTS:
        return ""
    head = re.sub(r"\d+$", "", root)
    return head if len(head) >= 4 else ""


def lexical_carriers(class_name: str, declarations: list) -> list[dict]:
    """Declared artifacts that probably bring the owning library in.

    A class in a transitive dependency has no declaration of its own to
    find. jellyfin-android never declares OkHttp, yet it declares
    io.coil-kt.coil3:coil-network-okhttp and
    androidx.media3:media3-datasource-okhttp, and Coil's own Gradle module
    metadata lists com.squareup.okhttp3:okhttp as a dependency. With the
    resolved graph unavailable -- the offline build could not even load the
    kotlin-dsl plugin buildSrc needs -- nothing looked at those names, and
    the finding was recorded as having no dependency artifact at all.

    This is a name match and is labelled as one. It says the library
    probably arrives through these artifacts; it does not say they ship the
    class, and it does not stand in for the graph. The owning library's own
    declarations are excluded: those are the owner, not a carrier.
    """
    head = library_head(class_name)
    if not head:
        return []
    group = owning_package_prefix(class_name)
    carriers: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in declarations:
        if _is_owner_declaration(item, group):
            continue
        tokens = {
            re.sub(r"\d+$", "", token)
            for token in re.split(r"[^a-z0-9]+", str(item["artifact"]).lower())
            if token
        }
        if head not in tokens:
            continue
        key = (item["group"], item["artifact"])
        if key in seen:
            continue
        seen.add(key)
        carriers.append({
            "group": item["group"],
            "artifact": item["artifact"],
            "version_expression": item.get("version_expression", ""),
            "source_file_path": item.get("source_file_path", ""),
            "line": item.get("line"),
            "basis": (
                f"the artifact name contains '{head}', the owning "
                "library's package root"
            ),
            "confirmed": False,
        })
    return carriers


def third_party_attribution(class_name: str, declarations: list) -> dict:
    """Which library owns this class, and whether the project names it."""
    group = owning_package_prefix(class_name)
    if not group:
        return {}
    declared = [
        item for item in declarations if _is_owner_declaration(item, group)
    ]
    return {
        "owning_package": group,
        "declared_directly": bool(declared),
        "matching_declarations": [
            {
                "group": item["group"],
                "artifact": item["artifact"],
                "version_expression": item.get("version_expression", ""),
                "source_file_path": item.get("source_file_path", ""),
            }
            for item in declared[:5]
        ],
        "bound": (
            "Derived from the reported class package, which is the Maven "
            "group for almost every JVM library. It names the third party, "
            "not the exact artifact or version; those need the resolved "
            "dependency tree."
        ),
    }


def _score_candidate(item: dict, class_lower: str, class_segments: set,
                     class_tokens: set) -> dict:
    """How well one declared or resolved artifact explains this class."""
    group = item["group"].lower()
    artifact = item["artifact"].lower()
    group_parts = [
        part for part in group.split(".")
        if len(part) >= 3 and part not in UNINFORMATIVE_SEGMENTS
    ]
    prefix_parts = sum(1 for part in group_parts if part in class_segments)
    overlap = len(class_tokens & (_tokens(group) | _tokens(artifact)))
    # The artifact that ships a class usually names its root package.
    # okhttp3.internal.platform.Platform comes from
    # com.squareup.okhttp3:okhttp, not from the okhttp-zstd or
    # logging-interceptor artifacts beside it in the same build file -- and
    # without this they all score identically and the finding is recorded as
    # ambiguous with the right answer sitting in the list.
    #
    # Only a library's own root counts, never an organisation's or the
    # platform's: "com" is a prefix of compose-bom, common and commons-io,
    # and app.maskan.chat's com.google.crypto.tink finding listed compose-bom
    # as a candidate on exactly that.
    root_segment = class_lower.split(".")[0]
    if (root_segment in _REVERSE_DOMAIN_HEADS
            or root_segment in UNINFORMATIVE_SEGMENTS
            or root_segment in _PLATFORM_ROOTS):
        root_segment = ""
    flattened = artifact.replace("-", "").replace("_", "")
    artifact_prefix = len(root_segment) >= 4 and len(flattened) >= 4 and (
        root_segment.startswith(flattened) or flattened.startswith(root_segment)
    )
    candidate = dict(item)
    candidate["match_score"] = (
        prefix_parts * 3 + overlap * 2 + (2 if artifact_prefix else 0)
    )
    candidate["match_reasons"] = [
        reason for condition, reason in (
            (prefix_parts > 0, "group segments occur in class package"),
            (overlap > 0, "artifact/group tokens overlap class package"),
            (artifact_prefix, "artifact name matches the class root package"),
        ) if condition
    ]
    candidate.setdefault("source_file_path", "")
    return candidate


def collect_declared_constants(
    repository_paths: list, repo_root: Path, source_cache: dict,
) -> dict:
    """Every `val NAME = "literal"` a build definition declares, by name.

    Read from the Kotlin/Groovy files a project uses to name its
    dependencies -- buildSrc and the version catalog -- rather than from the
    whole repository, so an unrelated application constant of the same name
    cannot be mistaken for a dependency coordinate.
    """
    constants: dict[str, dict] = {}
    for path in repository_paths:
        relative = path.relative_to(repo_root).as_posix()
        in_build_definition = (
            relative.startswith("buildSrc/")
            or path.name in BUILD_NAMES
            or "gradle" in relative.split("/")[0].lower()
        )
        if not in_build_definition:
            continue
        if path.suffix.lower() not in {".kt", ".kts", ".gradle", ".java"}:
            continue
        try:
            if path.stat().st_size > 500_000:
                continue
            content = source_cache.get(path) or path.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
        # These files name a constant twice on purpose: Versions.okhttp is
        # the version and Libs.okhttp is the coordinate that interpolates
        # it. Keyed by bare name alone, the first one wins and
        # `implementation Libs.okhttp` resolves to "5.3.2" -- not a
        # coordinate, so the real dependency is dropped while its siblings
        # okhttpZstd and okhttpLoggingInterceptor, which collide with
        # nothing, resolve fine. Record the enclosing object too, so a
        # qualified reference can ask for the one it meant.
        # A stack rather than "the last object seen": these files nest
        # `object Versions` inside `object Libs`, and without popping on the
        # closing brace every constant after the nested block is still
        # attributed to Versions -- so Libs.okhttp is never registered and
        # the qualified lookup that exists to separate the two falls back to
        # the bare name it was meant to disambiguate.
        containers: list[tuple[int, str]] = []
        depth = 0
        for number, line in enumerate(content.splitlines(), 1):
            container = _CONTAINER.search(line)
            if container and "{" in line:
                containers.append((depth, container.group("owner")))
            depth += line.count("{") - line.count("}")
            while containers and depth <= containers[-1][0]:
                containers.pop()
            owner = containers[-1][1] if containers else ""
            match = CONSTANT_VALUE.search(line)
            if not match:
                continue
            record = {
                "value": match.group("value"),
                "source_file_path": relative,
                "line": number,
            }
            name = match.group("name")
            if owner:
                constants.setdefault(f"{owner}.{name}", record)
            existing = constants.get(name)
            # On a bare-name collision keep the coordinate-shaped value: it
            # is the one a dependency declaration can use.
            if existing is None or (
                ":" in record["value"] and ":" not in existing["value"]
            ):
                constants[name] = record
    return constants


def resolve_constant_value(name: str, constants: dict) -> str:
    """Follow a constant to a literal, expanding any interpolation in it.

    Bounded to a few hops: `Libs.okhttp` names a coordinate string that
    interpolates `Versions.okhttp`, and that is as deep as this pattern
    goes in practice. An unresolvable interpolation is left in place rather
    than guessed at, so the caller can see what was not settled.
    """
    seen: set[str] = set()
    current = name
    for _ in range(MAX_CONSTANT_HOPS):
        if current in seen or current not in constants:
            break
        seen.add(current)
        value = constants[current]["value"]
        if "$" not in value:
            return value
        resolved = value
        for match in _INTERPOLATION.finditer(value):
            owner = match.group("owner")
            keys = ([f"{owner}.{match.group('name')}"] if owner else [])
            keys.append(match.group("name"))
            for key in keys:
                if key in constants and key not in seen:
                    resolved = resolved.replace(
                        match.group(0), constants[key]["value"]
                    )
                    break
        if resolved == value:
            return resolved
        return resolved
    return ""


def _tokens(value: str) -> set[str]:
    return {
        token.lower() for token in re.split(r"[^A-Za-z0-9]+", value)
        if len(token) >= 3 and token.lower() not in UNINFORMATIVE_SEGMENTS
    }


def parse_gradle_resolution(output: str, group: str, artifact: str) -> list[dict]:
    evidence = []
    for line in output.splitlines():
        for match in RESOLVED_COORDINATE.finditer(line):
            if match.group("group") != group or match.group("artifact") != artifact:
                continue
            requested = match.group("requested")
            selected = match.group("selected") or requested
            record = {
                "group": group,
                "artifact": artifact,
                "requested_version": requested,
                "selected_version": selected,
                "evidence": line.strip()[:500],
                "source": "gradle_offline_dependencies",
            }
            if record not in evidence:
                evidence.append(record)
    return evidence[:20]


# A class shipped by a transitive dependency is invisible to any scan of the
# build files, because the artifact that ships it is named nowhere in them.
# dev.dettmer.simplenotes declares androidx.security:security-crypto and the
# finding is in com.google.crypto.tink.integration.android.AndroidKeystoreAesGcm:
# Tink arrives through security-crypto, shares not one token with it, and the
# scan can only ever come back "no dependency artifact was identified".
#
# The dependency tree names every node, direct and transitive, and this
# project already runs it -- but only after an artifact has been identified,
# to resolve its version. Run it to do the identifying and the transitive
# case resolves like any other: com.google.crypto.tink:tink-android matches
# com.google.crypto.tink.* unambiguously.
_TREE_NODE = re.compile(
    r"(?P<group>[A-Za-z0-9_.-]+):(?P<artifact>[A-Za-z0-9_.-]+)"
    r"(?::(?P<version>[^\s(]+))?(?:\s+->\s+(?P<resolved>[^\s(]+))?"
)
TREE_INDENT = 5
_RICH_VERSION = re.compile(r"\{\s*(?:strictly|require|prefer)\s+([^\s}]+)[^}]*\}")


def parse_dependency_tree(output: str) -> list[dict]:
    """Every node of a `gradle dependencies` tree, with how it was reached.

    The path matters as much as the coordinate: a direct dependency is
    changed by editing its declared version, and a transitive one is not.
    """
    nodes: list[dict] = []
    stack: list[str] = []
    for line in output.splitlines():
        marker = line.find("--- ")
        if marker < 0:
            continue
        depth = marker // TREE_INDENT
        # Rich version constraints print as `{strictly 3.4.1} -> 3.4.1`; the
        # version is what the braces hold. Read raw, linkora's Ktor nodes
        # had the version "{strictly" and no binary could be found for them.
        text = _RICH_VERSION.sub(r"\1", line[marker + 4:].strip())
        match = _TREE_NODE.match(text)
        if not match:
            continue
        coordinate = f"{match.group('group')}:{match.group('artifact')}"
        del stack[depth:]
        parent = stack[-1] if stack else ""
        stack.append(coordinate)
        nodes.append({
            "group": match.group("group"),
            "artifact": match.group("artifact"),
            "version_expression": (
                match.group("resolved") or match.group("version") or ""
            ),
            "depth": depth,
            "direct": depth == 0,
            "reached_through": parent,
            "dependency_path": list(stack),
            "origin": "resolved_dependency_graph",
            "source_file_path": "",
            "declaration": " -> ".join(stack),
        })
    return nodes


# Resolved at most once per repository per process. The attempt costs a
# Gradle start either way -- dev.dettmer.simplenotes takes 84 seconds to
# fail, because the Android plugin is not in the offline cache -- and a run
# analysing many findings from one project would otherwise pay it for each.
_GRAPH_CACHE: dict[str, dict] = {}

_GRADLE_WHAT_WENT_WRONG = re.compile(
    r"\*\s*What went wrong:\s*(?P<cause>.+?)(?=\n\s*\*\s*(?:Try|Exception is):|\Z)",
    re.DOTALL,
)


def gradle_failure_cause(output: str) -> str:
    """The sentence Gradle wrote about why it failed, without its footer.

    The end of a failed Gradle run is always the same boilerplate --
    "Run with --stacktrace", "Run with --scan", "Get more help" -- so keeping
    the last characters of the output keeps exactly the part that says
    nothing. jellyfin's offline resolution failed because buildSrc needs a
    kotlin-dsl plugin that is not in the offline cache, and that line sat
    above a footer long enough to push it out of the recorded tail.
    """
    match = _GRADLE_WHAT_WENT_WRONG.search(output or "")
    if not match:
        return ""
    return " ".join(match.group("cause").split())[:600]


ONLINE_RESOLUTION_ENV = "TCC_ONLINE_DEPENDENCY_RESOLUTION"
ONLINE_RESOLUTION_TIMEOUT = 900
_SKIPPED_MODULE_DIRECTORIES = {"build", ".gradle", ".git", "node_modules"}
# The plugin id, or applicationId, which only an application module declares:
# catalog aliases name the plugin freely (libs.plugins.androidApplication,
# libs.plugins.agp.application, libs.plugins.android) and a libGDX root build
# applies it to a subproject from outside, so the id alone missed five of the
# projects of run_20261011_091101.
_APPLICATION_PLUGIN = re.compile(
    r"com\.android\.application|plugins\.android\.application\b"
    r"|\bapplicationId\s*[=( ]"
)


# Words a coordinate and a package share without saying anything about which
# library it is.
_GENERIC_NAME_TOKENS = {
    "com", "org", "io", "net", "de", "at", "me", "app", "github", "gitlab",
    "android", "androidx", "java", "jvm", "kotlin", "kotlinx", "lib", "library",
    "core", "common", "ktx", "api", "sdk", "internal", "util", "utils",
    "release", "debug", "impl",
}
NAME_SIMILAR_OWNERS = 8


def _name_tokens(text: str) -> set[str]:
    return {
        token for token in re.split(r"[^a-z0-9]+", str(text).lower())
        if len(token) >= 3 and token not in _GENERIC_NAME_TOKENS
    }


def owner_name_similarity(class_name: str, group: str, artifact: str) -> int:
    """How strongly a coordinate's name points at the reported class's package.

    The owner of a class is not always in the group named like its package:
    JitPack publishes at.bitfire.cert4android as com.github.bitfireAT:
    cert4android, Ktor ships io.ktor.network.tls in ktor-network-tls-jvm,
    and Sentry's io.sentry.util is in io.sentry:sentry among a dozen io.sentry
    artifacts. Tokens the package shares with the coordinate count; tokens
    only the artifact has (android, compose, fragment) count against it, so
    the plainest artifact of a family ranks first. Zero means unrelated.
    """
    package = class_name.rsplit(".", 1)[0] if "." in class_name else class_name
    package_tokens = _name_tokens(package)
    joined_package = re.sub(r"[^a-z0-9]", "", package.lower())
    artifact_tokens = _name_tokens(artifact)
    shared = package_tokens & (_name_tokens(group) | artifact_tokens)
    if not shared:
        # Names joined differently: libchecker's com.absinthe.rulesbundle is
        # published as LibChecker-Rules-Bundle.
        compact = re.sub(r"[^a-z0-9]", "", artifact.lower())
        if len(compact) >= 6 and compact in joined_package:
            shared = {compact}
        else:
            shared = {token for token in package_tokens
                      if len(token) >= 6 and token in compact}
    if not shared:
        return 0
    # Words of the artifact that the matched name already spells
    # (rules and bundle inside rulesbundle) are not extra.
    extra = len({
        token for token in artifact_tokens - package_tokens
        if not any(token in matched for matched in shared)
    })
    return 3 * len(shared) - extra


def name_similar_owners(class_name: str, candidates: list, tried: set,
                        limit: int = NAME_SIMILAR_OWNERS) -> list[dict]:
    """The candidates most likely, by name, to ship the class; best first.

    Each group present also proposes its plain artifact, named like the
    group's last segment, at the version its other artifacts resolved to: the
    tree of untis lists sentry-android and four siblings, while
    io.sentry.util lives in io.sentry:sentry, which the tree did not print.
    """
    candidates = list(candidates)
    families: dict[str, str] = {}
    for item in candidates:
        group = str(item.get("group", ""))
        version = str(item.get("version_expression") or item.get("version", ""))
        if group and re.fullmatch(r"[0-9][\w.\-]*", version):
            families.setdefault(group, version)
    for group, version in families.items():
        plain = group.rsplit(".", 1)[-1]
        candidates.append({"group": group, "artifact": plain,
                           "version_expression": version, "depth": 99})
    scored = []
    for item in candidates:
        group, artifact = str(item.get("group", "")), str(item.get("artifact", ""))
        if not group or not artifact or (group, artifact) in tried:
            continue
        score = owner_name_similarity(class_name, group, artifact)
        if score > 0:
            # Ties go to the shorter artifact name: sentry before
            # sentry-android, the family's plain artifact first.
            scored.append((score, -len(artifact), group, artifact, item))
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    chosen, seen = [], set()
    for _, _, group, artifact, item in scored:
        if (group, artifact) in seen:
            continue
        seen.add((group, artifact))
        chosen.append({
            "group": group, "artifact": artifact,
            "version": item.get("version_expression") or item.get("version", ""),
        })
        if len(chosen) >= limit:
            break
    return chosen


def online_resolution_enabled(environ: dict | None = None) -> bool:
    return (environ if environ is not None else os.environ).get(
        ONLINE_RESOLUTION_ENV, "") == "1"


def application_dependency_tasks(working_directory: Path) -> list[str]:
    """`:<module>:dependencies` for each Android application module.

    The root project of an Android build declares almost nothing; the tree
    that says which library ships a class is the application module's. A
    root `dependencies` task printed no tree for the projects of
    run_20261010_200449 even where Gradle ran.
    """
    tasks = []
    for current, directories, files in os.walk(working_directory):
        depth = len(Path(current).relative_to(working_directory).parts)
        directories[:] = [
            name for name in directories
            if name not in _SKIPPED_MODULE_DIRECTORIES
            and not name.startswith(".") and depth < 3
        ]
        if depth == 0:
            continue
        for name in ("build.gradle", "build.gradle.kts"):
            if name not in files:
                continue
            try:
                text = (Path(current) / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(_APPLICATION_PLUGIN.search(line) and "apply false" not in line
                   for line in text.splitlines()):
                module = ":".join(Path(current).relative_to(working_directory).parts)
                tasks.append(f":{module}:dependencies")
    return sorted(set(tasks)) or ["dependencies"]


def resolve_dependency_graph(repo_root: Path, timeout: int = 120) -> dict:
    """The project's resolved dependency tree, or why it is unavailable.

    Offline first: this reads what the project already resolved, without
    reaching the network. A corpus whose projects were never built on this
    machine has nothing in the cache, so every finding of
    run_20261010_200449 met "no_dependency_tree_in_output". With
    TCC_ONLINE_DEPENDENCY_RESOLUTION=1 an offline failure is followed by one
    online resolution, which also downloads the libraries' jars that the
    bytecode lookup then reads. When neither yields a tree the caller falls
    back to scanning the build files, which is what it did before.
    """
    cache_key = str(repo_root.resolve())
    if cache_key in _GRAPH_CACHE:
        return _GRAPH_CACHE[cache_key]
    wrapper = repo_root / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not wrapper.is_file():
        # A Gradle project can sit in a subdirectory of the repository.
        candidates = [
            path for path in repo_root.glob("*/gradlew")
            if path.is_file()
        ]
        if not candidates:
            result = {"status": "unavailable",
                      "reason": "gradle_wrapper_not_found", "nodes": []}
            _GRAPH_CACHE[cache_key] = result
            return result
        wrapper = candidates[0]
    # Absolute: the command runs with cwd set to the wrapper's own
    # directory, and a relative path stops resolving the moment it does.
    wrapper = wrapper.resolve()
    working_directory = wrapper.parent
    tasks = application_dependency_tasks(working_directory)

    def run(offline: bool, limit: int):
        # --no-daemon: a daemon outlives this call by hours and holds
        # gigabytes; four were still running three hours after the
        # extraction that started them finished.
        command = [str(wrapper), "-q", *tasks, "--console=plain", "--no-daemon"]
        if offline:
            command.append("--offline")
        from cognalys.builds.git_directory import git_directory_for_build

        try:
            # The build step's .git shim, on the checkout holding the
            # wrapper's directory, like every other Gradle run.
            with git_directory_for_build(working_directory, repo_root):
                completed = subprocess.run(
                    command, cwd=working_directory, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=limit,
                )
        except subprocess.TimeoutExpired:
            return [], "", "gradle_offline_timeout" if offline else "gradle_online_timeout"
        except OSError as exc:
            return [], "", f"gradle_start_failed: {exc}"
        combined = (completed.stdout or "") + "\n" + (completed.stderr or "")
        return parse_dependency_tree(combined), combined, ""

    nodes, combined, failure = run(True, timeout)
    resolution = "offline"
    if not nodes and online_resolution_enabled() and not failure.startswith("gradle_start"):
        online_nodes, online_output, online_failure = run(False, ONLINE_RESOLUTION_TIMEOUT)
        if online_nodes or online_output:
            nodes, combined, failure = online_nodes, online_output, online_failure
            resolution = "online"
    if not nodes:
        result = {
            "status": "unavailable",
            "reason": failure or "no_dependency_tree_in_output",
            "nodes": [], "resolution": resolution, "tasks": tasks,
            "diagnostic_tail": combined[-2000:],
        }
        _GRAPH_CACHE[cache_key] = result
        return result
    unique = {}
    for node in nodes:
        key = (node["group"], node["artifact"])
        # Keep the shallowest occurrence: that is how the project reaches it.
        if key not in unique or node["depth"] < unique[key]["depth"]:
            unique[key] = node
    result = {
        "status": "available",
        "working_directory": working_directory.name,
        "resolution": resolution,
        "tasks": tasks,
        "nodes": list(unique.values()),
    }
    _GRAPH_CACHE[cache_key] = result
    return result


def resolve_gradle_dependency(repo_root: Path, group: str, artifact: str,
                              build_file: str = "", timeout: int = 90) -> dict:
    """Resolve only ambiguous external dependencies, without network access."""
    wrapper = repo_root / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not wrapper.is_file():
        return {"status": "unavailable", "reason": "gradle_wrapper_not_found"}
    module_path = Path(build_file).parent.as_posix().strip("./")
    task = (
        ":" + module_path.replace("/", ":") + ":dependencies"
        if module_path else "dependencies"
    )
    command = [str(wrapper), "-q", task, "--offline", "--console=plain", "--no-daemon"]
    from cognalys.builds.git_directory import git_directory_for_build

    try:
        # Configuring a project in a worktree whose scripts read .git/ needs
        # the build step's shim like any other Gradle run.
        with git_directory_for_build(repo_root, repo_root):
            completed = subprocess.run(
                command, cwd=repo_root, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        return {"status": "unavailable", "reason": "gradle_offline_timeout"}
    except OSError as exc:
        return {"status": "unavailable", "reason": f"gradle_start_failed: {exc}"}
    combined = (completed.stdout or "") + "\n" + (completed.stderr or "")
    evidence = parse_gradle_resolution(combined, group, artifact)
    return {
        "status": "resolved" if evidence else "unavailable",
        "reason": "" if evidence else f"gradle_exit_{completed.returncode}_no_match",
        "command": f"gradlew -q {task} --offline --console=plain",
        "evidence": evidence,
        "diagnostic_tail": "\n".join(combined.splitlines()[-20:])[-4000:],
    }


def collect_repository_version_alignment(repo_root: Path, apk_name: str = "") -> dict:
    apk_match = re.search(r"_(\d+)\.apk$", apk_name)
    apk_version = apk_match.group(1) if apk_match else ""
    repository_versions = []
    for path in repo_root.rglob("build.gradle*"):
        if any(part.lower() in SKIPPED for part in path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r"\bversionCode\s*(?:=\s*)?(\d+)", content):
            repository_versions.append({
                "source_file_path": path.relative_to(repo_root).as_posix(),
                "version_code": match.group(1),
            })
    codes = {item["version_code"] for item in repository_versions}
    status = "unknown"
    if apk_version and codes:
        status = "aligned" if apk_version in codes else "mismatch"
    return {
        "status": status,
        "apk_version_code": apk_version,
        "repository_version_codes": repository_versions[:20],
        "warning": (
            "Repository source may not correspond to the analyzed APK."
            if status == "mismatch" else ""
        ),
    }


REFRESH_VERSIONS_FILE = "versions.properties"
_REFRESH_VERSION_ENTRY = re.compile(
    r"^\s*version\.(?P<key>[^=\s]+)\s*=\s*(?P<version>[^\s#]+)"
)


def collect_refresh_versions(content: str, relative: str) -> list[dict]:
    """The versions a refreshVersions project keeps out of its build files.

    DuckDuckGo declares `implementation Square.okHttp3.okHttp` in thirty
    modules and the only version anywhere is `version.okhttp3=4.12.0` in
    versions.properties. A key is either `group..artifact` or an alias the
    plugin shares across a group (okhttp3, retrofit2).
    """
    entries = []
    for number, line in enumerate(content.splitlines(), 1):
        match = _REFRESH_VERSION_ENTRY.match(line)
        if not match:
            continue
        key = match.group("key")
        group, separator, artifact = key.partition("..")
        entries.append({
            "source_file_path": relative,
            "line": number,
            "group": group if separator else "",
            "artifact": artifact if separator else key,
            "version_expression": match.group("version"),
            "declaration": line.strip(),
            "declaration_kind": "refresh_versions",
        })
    return entries


def resolve_version_placeholders(declarations: list[dict],
                                 version_entries: list[dict]) -> None:
    """Replace refreshVersions' `_` with the version versions.properties holds."""
    by_coordinate = {
        (entry["group"], entry["artifact"]): entry
        for entry in version_entries if entry["group"]
    }
    by_alias = {
        entry["artifact"]: entry for entry in version_entries if not entry["group"]
    }
    for item in declarations:
        if item.get("version_expression") != "_":
            continue
        entry = (
            by_coordinate.get((item["group"], item["artifact"]))
            or by_alias.get(item["group"].split(".")[-1])
            or by_alias.get(item["artifact"])
        )
        if entry:
            item["version_expression"] = entry["version_expression"]
            item["version_resolved_from"] = (
                f"{entry['source_file_path']}:{entry['line']}"
            )


# Groovy and Kotlin build scripts name versions as properties:
#   ext { ktor_version = '1.6.0' }      ext.ktor_version = '1.6.0'
#   def ktorVersion = "1.6.0"           extra["ktorVersion"] = "1.6.0"
#   set("ktorVersion", "1.6.0")         gradle.properties: ktor_version=1.6.0
# and dependencies interpolate them: "io.ktor:ktor-client-core:$ktor_version".
_SCRIPT_PROPERTY = re.compile(
    r"^\s*(?:(?:rootProject\.|project\.)?ext\.|def\s+|val\s+|var\s+)?"
    r"(?P<name>[A-Za-z_]\w*)\s*=\s*[\"'](?P<value>[^\"'$\s]{1,80})[\"']\s*;?\s*$"
)
_EXTRA_PROPERTY = re.compile(
    r"\b(?:extra|ext)\s*\[\s*[\"'](?P<name>\w+)[\"']\s*\]\s*=\s*[\"'](?P<value>[^\"'$\s]{1,80})[\"']"
    r"|\bset\s*\(\s*[\"'](?P<set_name>\w+)[\"']\s*,\s*[\"'](?P<set_value>[^\"'$\s]{1,80})[\"']\s*\)"
)
_PROPERTIES_ENTRY = re.compile(r"^\s*(?P<name>[\w.-]+)\s*[=:]\s*(?P<value>[^\s#]+)\s*$")
_VERSION_REFERENCE = re.compile(
    r"\$\{?(?:(?:rootProject|project)\.)?(?:(?:ext|extra|properties)\.)?"
    r"(?:(?P<owner>[A-Z]\w*)\.)?(?P<name>[A-Za-z_]\w*)\}?"
)


def collect_build_properties(repository_paths: list, repo_root: Path,
                             source_cache: dict | None = None) -> dict:
    """Name -> {value, source_file_path, line} of every literal build property.

    Build scripts first, gradle.properties after: a script's ext value is
    what the build sees when both define one (the script runs later).
    """
    source_cache = source_cache or {}
    scripts, properties = {}, {}
    for path in repository_paths:
        name = path.name
        if name not in {"build.gradle", "build.gradle.kts", "settings.gradle",
                        "settings.gradle.kts", "gradle.properties"} \
                and not (name.endswith((".gradle", ".gradle.kts")) and "gradle" in path.parts[:-1]):
            continue
        try:
            content = source_cache.get(path) or path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = path.relative_to(repo_root).as_posix()
        target = properties if name == "gradle.properties" else scripts
        for number, line in enumerate(content.splitlines(), 1):
            if name == "gradle.properties":
                matches = [_PROPERTIES_ENTRY.match(line)]
            else:
                matches = [_SCRIPT_PROPERTY.match(line), *_EXTRA_PROPERTY.finditer(line)]
            for match in matches:
                if not match:
                    continue
                key = match.groupdict().get("name") or match.groupdict().get("set_name")
                value = match.groupdict().get("value") or match.groupdict().get("set_value")
                if key and value:
                    target.setdefault(key, {"value": value, "source_file_path": relative,
                                            "line": number})
    return {**properties, **scripts}


def resolve_property_versions(declarations: list[dict], properties: dict,
                              constants: dict | None = None) -> None:
    """Replace `$name`, `${name}`, `${rootProject.ext.name}`, `${Versions.name}` in versions.

    retrowars declares "io.ktor:ktor-client-core:$ktor_version" and sets
    ktor_version = '1.6.0' in the root build.gradle's ext block. Unresolved,
    the bytecode search read whichever ktor the Gradle cache held (3.6.0),
    which did not ship the reported class. A reference nothing defines is
    left as written, so the record still says what was not settled.
    """
    constants = constants or {}
    for item in declarations:
        expression = str(item.get("version_expression") or "")
        if "$" not in expression:
            continue
        sources = []

        def value_of(match: re.Match) -> str:
            owner, name = match.group("owner"), match.group("name")
            for found in ((constants.get(f"{owner}.{name}") if owner else None),
                          properties.get(name), constants.get(name)):
                if found and "$" not in str(found.get("value", "")):
                    sources.append(f"{found['source_file_path']}:{found['line']}")
                    return str(found["value"])
            return match.group(0)

        resolved = _VERSION_REFERENCE.sub(value_of, expression)
        if resolved != expression and "$" not in resolved:
            item["version_expression"] = resolved
            item["version_resolved_from"] = ", ".join(sources)


def collect_declarations(repo_root: Path, repository_index: dict | None = None) -> dict:
    """Every dependency the build files declare, with the versions they name.

    Split out of collect_dependency_context so a finding that names its
    library in the detector message -- "Using okhttp3 v3.12.0" -- can be
    checked against the same declarations without attributing a class.
    """
    repository_index = repository_index or build_repository_index(repo_root)
    repository_paths = repository_index["paths"]
    source_cache = repository_index["source_cache"]
    declarations = []
    build_constraints = []
    constants = collect_declared_constants(
        repository_paths, repo_root, source_cache,
    )
    files = []
    for path in repository_paths:
        if path.name not in BUILD_NAMES:
            continue
        try:
            if path.stat().st_size > 500_000:
                continue
            content = source_cache.get(path) or path.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
        relative = path.relative_to(repo_root).as_posix()
        files.append(relative)
        coordinate_only = path.name in COORDINATE_ONLY_FILES
        catalog_versions = (
            collect_catalog_versions(content)
            if path.suffix.lower() == ".toml" else {}
        )
        for line_number, line in enumerate(content.splitlines(), 1):
            # The split group/name catalog form, which carries no coordinate
            # for the pattern below to find.
            catalog = CATALOG_LIBRARY.search(line)
            if catalog:
                reference = CATALOG_VERSION_REF.search(line)
                literal = CATALOG_VERSION_LITERAL.search(line)
                version = (
                    catalog_versions.get(reference.group("ref"), "")
                    if reference else
                    literal.group("version") if literal else ""
                )
                declarations.append({
                    "source_file_path": relative,
                    "line": line_number,
                    "group": catalog.group("group"),
                    "artifact": catalog.group("artifact"),
                    "version_expression": version,
                    "declaration": line.strip(),
                })
                continue
            reads_coordinates = coordinate_only or bool(
                DEPENDENCY_LINE.search(line)
            )
            for match in (
                COORDINATE.finditer(line) if reads_coordinates else ()
            ):
                version = match.group("version") or ""
                # The catalog's `module = "group:artifact", version = "3.12.0"`
                # form keeps the version in its own key on the same line, so
                # the coordinate alone recorded a library with no version.
                if not version and path.suffix.lower() == ".toml":
                    reference = CATALOG_VERSION_REF.search(line)
                    literal = CATALOG_VERSION_LITERAL.search(line)
                    version = (
                        catalog_versions.get(reference.group("ref"), "")
                        if reference else
                        literal.group("version") if literal else ""
                    )
                declarations.append({
                    "source_file_path": relative,
                    "line": line_number,
                    "group": match.group("group"),
                    "artifact": match.group("artifact"),
                    "version_expression": version,
                    "declaration": line.strip(),
                })
            # `implementation Libs.okhttp` names its coordinate instead of
            # writing it. Resolve the constant so the declaration is recorded
            # with the artifact and version it actually requests.
            for match in CONSTANT_DEPENDENCY.finditer(line):
                # The qualified name first: `Libs.okhttp` and
                # `Versions.okhttp` are different constants, and only one of
                # them is a coordinate.
                owner = match.group("owner")
                literal = ""
                if owner:
                    literal = resolve_constant_value(
                        f"{owner}.{match.group('name')}", constants
                    )
                if not literal:
                    literal = resolve_constant_value(
                        match.group("name"), constants
                    )
                coordinate = COORDINATE.search(f'"{literal}"') if literal else None
                if not coordinate:
                    continue
                declarations.append({
                    "source_file_path": relative,
                    "line": line_number,
                    "group": coordinate.group("group"),
                    "artifact": coordinate.group("artifact"),
                    "version_expression": coordinate.group("version") or "",
                    "declaration": line.strip(),
                    "resolved_through_constant": match.group("name"),
                    "constant_declared_in": constants.get(
                        match.group("name"), {}
                    ).get("source_file_path", ""),
                })
            if re.search(
                r"\b(?:minSdk|targetSdk|compileSdk|sourceCompatibility|"
                r"jvmTarget|kotlin_version|kotlinVersion)\b", line
            ):
                build_constraints.append({
                    "source_file_path": relative,
                    "line": line_number,
                    "declaration": line.strip(),
                })

    version_entries: list[dict] = []
    for path in repository_paths:
        if path.name != REFRESH_VERSIONS_FILE:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        version_entries.extend(collect_refresh_versions(
            content, path.relative_to(repo_root).as_posix(),
        ))
    resolve_version_placeholders(declarations, version_entries)
    resolve_property_versions(
        declarations, collect_build_properties(repository_paths, repo_root, source_cache),
        constants,
    )
    return {
        "declarations": declarations,
        "version_entries": version_entries,
        "build_constraints": build_constraints,
        "files": files,
        "repository_index": repository_index,
    }


def collect_dependency_context(repo_root: Path, class_name: str,
                               apk_name: str = "", method_name: str = "",
                               artifact_cache=None) -> dict:
    """Parse declared dependencies and rank likely owners of an external class."""
    collected = collect_declarations(repo_root)
    repository_index = collected["repository_index"]
    repository_paths = repository_index["paths"]
    source_cache = repository_index["source_cache"]
    declarations = collected["declarations"]
    build_constraints = collected["build_constraints"]
    files = collected["files"]

    class_lower = class_name.lower().replace("$", ".")
    # Whole package segments, not a substring of the joined name: "ui" used to
    # match inside "gui" and "compose" inside "composer", so a group scored
    # against a class it only happened to spell part of.
    class_segments = {
        segment for segment in class_lower.split(".") if segment
    }
    class_tokens = _tokens(class_name)

    def score_all(items: list[dict]) -> tuple[list[dict], int, int]:
        scored = [_score_candidate(
            item, class_lower, class_segments, class_tokens,
        ) for item in items]
        scored.sort(
            key=lambda entry: (-entry["match_score"], entry["source_file_path"])
        )
        ranked = [entry for entry in scored if entry["match_score"] > 0]
        best = ranked[0]["match_score"] if ranked else 0
        # Counted per coordinate, not per declaration. One dependency
        # declared by two modules is still one dependency, and counting the
        # lines made the identification ambiguous whenever a project used
        # the artifact twice -- Eventfahrplan declares okhttp in both app
        # and network.
        tied = len({
            (entry["group"], entry["artifact"]) for entry in ranked
            if entry["match_score"] == best
        })
        return scored, best, tied

    candidates, top_score, equally_ranked = score_all(declarations)
    dependency_graph = {"status": "not_needed", "nodes": []}
    # Only when the build files could not settle it: resolving the tree
    # spawns Gradle, and a direct dependency never needs it.
    if not (top_score >= 3 and equally_ranked == 1):
        dependency_graph = resolve_dependency_graph(repo_root)
        if dependency_graph.get("nodes"):
            candidates, top_score, equally_ranked = score_all(
                declarations + dependency_graph["nodes"]
            )

    positive = [item for item in candidates if item["match_score"] > 0]
    status = "identified" if top_score >= 3 and equally_ranked == 1 else "ambiguous"
    likely = positive[0] if status == "identified" else {}

    def owns(group: str) -> bool:
        return group_owns_class(group, class_name)

    # The build files and the resolved graph are not the only record of how
    # a library arrives: the published metadata of every declared artifact
    # is in the local Gradle cache, and reading it needs no Gradle start.
    # app.maskan.chat's graph failed on a missing Android plugin, so its
    # com.google.crypto.tink finding went unattributed while security-crypto
    # -- declared, and listing tink-android in its own POM -- sat in the cache.
    cached_metadata = (
        {"status": "not_needed", "carriers": []} if likely
        else trace_owner_through_metadata(declarations, owns, artifact_cache)
    )
    bytecode_owners = [
        {"group": item["group"], "artifact": item["artifact"],
         "version": item.get("version_expression", "")}
        for item in ([likely] if likely else []) + [
            item for item in declarations + dependency_graph.get("nodes", [])
            if owns(item["group"])
        ]
    ] + [
        # The owner's version is only as settled as the carrier it was read
        # through: jellyfin's okhttp 4.12.0 came from cached coil 3.4.0, not
        # the declared 3.6.2, and the bytecode record must not say "exact".
        {**carrier["owner"], "version_confirmed": carrier["confirmed"]}
        for carrier in cached_metadata.get("carriers", [])
    ]
    # The reported method as the library ships it. Without it a finding in a
    # dependency is adjudicated on an empty method record, and jellyfin's
    # OkHttp finding abstained on "the supplied target-method record is
    # empty" while the jar holding that method was in the cache.
    external_target_bytecode = (
        locate_method_bytecode(
            bytecode_owners, class_name, method_name, artifact_cache,
        )
        if method_name and bytecode_owners else {}
    )
    if method_name and external_target_bytecode.get("status") != "available":
        # No owner named like the package shipped the class: try the
        # resolved dependencies whose names point at it. Finding the class
        # entry inside a binary is what makes it the owner, not the name.
        # Only what the first search examined: it stops after
        # MAX_BYTECODE_ARTIFACTS, and linkora's ktor-network-tls-jvm was
        # among the io.ktor owners it never reached.
        tried = {
            tuple(str(entry.get("coordinate", "")).split(":", 1))
            for entry in external_target_bytecode.get("searched", []) or []
            if ":" in str(entry.get("coordinate", ""))
            and entry.get("cached_version")
        }
        similar = name_similar_owners(
            class_name, declarations + dependency_graph.get("nodes", []), tried,
        )
        if similar:
            by_name = locate_method_bytecode(
                similar, class_name, method_name, artifact_cache,
            )
            if by_name.get("status") == "available" or not external_target_bytecode:
                external_target_bytecode = {**by_name, "owner_selection": "name_similarity"}
    if method_name and external_target_bytecode.get("status") != "available":
        local = locate_repository_binary_method(repo_root, class_name, method_name)
        if local.get("status") == "available" or not external_target_bytecode:
            external_target_bytecode = local
    # Where the owning library is developed. A finding inside library code is
    # not the application's to fix, and an issue about it belongs with the
    # library; the cached POM's <scm> is the library's own statement of where
    # that is.
    repository_owners = []
    if (external_target_bytecode.get("artifact")
            and external_target_bytecode.get("source") != "repository_binary"):
        owner_group, _, owner_artifact = external_target_bytecode["artifact"].partition(":")
        repository_owners.append({
            "group": owner_group, "artifact": owner_artifact,
            "version": external_target_bytecode.get("cached_version", ""),
        })
    repository_owners.extend(bytecode_owners)
    owning_library_repository = (
        upstream_repository_for(repository_owners, artifact_cache)
        if repository_owners else {}
    )
    usage_evidence = []
    related_tests = []
    terms: set[str] = set()
    if likely:
        group = likely["group"]
        artifact = likely["artifact"]
        class_prefix = ".".join(class_name.replace("$", ".").split(".")[:3])
        terms = {
            term.lower() for term in (
                group, artifact, artifact.replace("-", "."), class_prefix
            ) if len(term) >= 4
        }
    else:
        # How the application uses the library, even when the artifact is
        # not identified. A finding in library code is often reached through
        # the application's own configuration of it -- the TrustManager it
        # installs, the parameters it passes -- and that is the part the
        # application can change. This scan used to run only for an
        # identified artifact, so for the transitive findings it matters
        # most for it never ran.
        owning = owning_package_prefix(class_name)
        if owning and ("." in owning or library_head(class_name)):
            terms = {owning.lower() + "."}
    if terms:
        eligible_suffixes = {
            ".java", ".kt", ".kts", ".xml", ".properties", ".pro"
        }
        for path in repository_paths:
            if path.suffix.lower() not in eligible_suffixes:
                continue
            try:
                if path.stat().st_size > 500_000:
                    continue
                content = source_cache.get(path) or path.read_text(
                    encoding="utf-8", errors="replace"
                )
                lines = content.splitlines()
            except OSError:
                continue
            relative = path.relative_to(repo_root).as_posix()
            for line_number, line in enumerate(lines, 1):
                matched = sorted(term for term in terms if term in line.lower())
                if not matched:
                    continue
                record = {
                    "source_file_path": relative,
                    "line": line_number,
                    "matched_terms": matched,
                    "declaration_or_usage": line.strip(),
                }
                usage_evidence.append(record)
                if "test" in relative.lower():
                    related_tests.append(record)
                if len(usage_evidence) >= 80:
                    break
            if len(usage_evidence) >= 80:
                break
    resolved_versions = [
        item for item in declarations
        if likely
        and item["group"] == likely["group"]
        and item["artifact"] == likely["artifact"]
        and item["source_file_path"].endswith("gradle.lockfile")
    ]
    gradle_resolution = {"status": "not_needed", "evidence": []}
    if likely and not likely.get("version_expression") and not resolved_versions:
        gradle_resolution = resolve_gradle_dependency(
            repo_root, likely["group"], likely["artifact"],
            likely.get("source_file_path", ""),
        )
        resolved_versions.extend(gradle_resolution.get("evidence", []))
    return {
        "status": status,
        "finding_origin": "external_dependency_class",
        "external_class": class_name,
        "likely_dependency": likely,
        # Only what actually scored. When nothing did, this used to fall back
        # to the first twenty declarations in file order, and jellyfin's
        # OkHttp finding went out listing kotlinx-coroutines, koin and
        # androidx.lifecycle as its "candidates" -- a list with no relation
        # to the class, presented as if it had one. declared_dependencies
        # still carries every declaration for anyone who needs them.
        "dependency_candidates": positive[:20],
        "declared_dependencies": declarations[:100],
        "build_constraints": build_constraints[:60],
        "resolved_version_evidence": resolved_versions[:20],
        "gradle_resolution": gradle_resolution,
        # How the project reaches the identified artifact. A direct
        # dependency is changed by editing its declared version; a
        # transitive one is not, and saying which is which is the difference
        # between a proposable change and a reckless one.
        "dependency_graph": {
            "status": dependency_graph.get("status", "not_needed"),
            "reason": dependency_graph.get("reason", ""),
            "node_count": len(dependency_graph.get("nodes", [])),
            # Kept so "unavailable" says why. It was dropped here, and the
            # only way to learn that jellyfin's offline build failed on a
            # missing kotlin-dsl plugin was to run Gradle again by hand. The
            # cause is extracted rather than trusting the tail: the last
            # characters of a failed Gradle run are its fixed footer, and the
            # one line that matters sits above it.
            **(
                {
                    "diagnostic_cause": gradle_failure_cause(
                        dependency_graph["diagnostic_tail"]
                    ),
                    "diagnostic_tail": dependency_graph["diagnostic_tail"][-1200:],
                }
                if dependency_graph.get("diagnostic_tail") else {}
            ),
        },
        "reached_directly": bool(likely.get("direct", True)) if likely else None,
        "reached_through": likely.get("reached_through", "") if likely else "",
        "dependency_path": likely.get("dependency_path", []) if likely else [],
        # Always present, and independent of Gradle: it names the third
        # party even when the tree cannot be resolved, which is the
        # difference between "no dependency artifact was identified" and a
        # finding attributed to a library by name.
        "third_party_attribution": third_party_attribution(
            class_name, declarations
        ),
        # Only when the owner could not be identified: a direct dependency
        # needs no guess about how the library arrives.
        "lexical_carriers": (
            [] if likely else lexical_carriers(class_name, declarations)
        ),
        # Metadata evidence rather than a name match, so it covers what
        # lexical_carriers deliberately does not: an organisation package
        # such as com.google.crypto.tink, carried by an artifact whose name
        # says nothing about it.
        "cached_artifact_metadata": cached_metadata,
        "external_target_bytecode": external_target_bytecode,
        "owning_library_repository": owning_library_repository,
        "artifact_repository_alignment": collect_repository_version_alignment(
            repo_root, apk_name
        ),
        "repository_usage_evidence": usage_evidence,
        "related_tests": related_tests[:30],
        "build_files": files[:40],
        "limits": {
            "analysis_kind": "declared_dependency_lexical_mapping",
            "resolved_dependency_graph": gradle_resolution.get("status") == "resolved",
            "cached_artifact_metadata": cached_metadata.get("status", ""),
            "repository_index": repository_index["metadata"],
            "warning": (
                "A lexical package/artifact match is a candidate, not proof. "
                "A safe upgrade requires resolved-version and official migration evidence."
            ),
        },
    }
