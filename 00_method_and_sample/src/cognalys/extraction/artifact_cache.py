"""Read what the local Gradle cache already holds about a dependency.

A class that ships in a transitive dependency has no declaration of its own in
the build files, and resolving the real dependency graph needs Gradle to start
offline -- which fails whenever a plugin the build needs was never downloaded.
jellyfin-android's graph failed on a missing kotlin-dsl plugin, and
app.maskan.chat's on the Android Gradle plugin, so a finding in
com.google.crypto.tink.integration.android.AndroidKeystoreAesGcm was skipped as
having "neither code to review nor a declared version to change".

Both facts were on disk. The cache holds the published metadata of the
artifacts the project declares -- security-crypto's POM lists
com.google.crypto.tink:tink-android -- and the jar of the library itself, whose
bytecode is the code the detector reported. Reading them needs no Gradle and no
network, only the files a previous build of any project left behind.

Nothing here is presented as more than it is. A carrier read from a cached
version other than the declared one says so, and bytecode is labelled with the
artifact version it came from, which need not be the version the APK ships.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import deque
from pathlib import Path
from typing import Callable, Iterable
from xml.etree import ElementTree


MAX_TRAVERSAL_DEPTH = 3
MAX_VISITED_MODULES = 300
MAX_BYTECODE_ARTIFACTS = 12
# Dependencies of a declared owner, in the owner's own group, examined for
# a class it does not ship itself.
MAX_TRANSITIVE_ARTIFACTS = 16
MAX_METHOD_CHARACTERS = 12_000
MAX_METHOD_OVERLOADS = 4
JAVAP_TIMEOUT_SECONDS = 60

_BINARY_SUFFIXES = {".jar", ".aar"}
# The cache keeps sources and documentation beside the binary; neither holds
# the bytecode the detector analysed.
_NON_CODE_CLASSIFIERS = ("-sources", "-javadoc", "-samples")
_SKIPPED_POM_SCOPES = {"test", "provided", "system"}
# Gradle module variants that do not describe code the application runs:
# documentation variants redirect to the same modules as the library ones, and
# platform variants constrain versions without shipping anything.
_CODE_CATEGORIES = {"library"}

_QUALIFIER_RANK = {
    "snapshot": 0, "dev": 1, "alpha": 2, "a": 2, "beta": 3, "b": 3,
    "milestone": 4, "m": 4, "rc": 5, "cr": 5,
}
_UNKNOWN_QUALIFIER_RANK = 6
_RELEASE_RANK = 9
_VERSION_WIDTH = 6


ONLINE_RESOLUTION_ENV = "TCC_ONLINE_DEPENDENCY_RESOLUTION"
LIBRARY_DOWNLOAD_DIR_ENV = "TCC_LIBRARY_ARTIFACT_DIR"
LIBRARY_REPOSITORIES = (
    "https://repo1.maven.org/maven2",
    "https://dl.google.com/dl/android/maven2",
    # Libraries published straight from GitHub (com.github.<owner>:...):
    # libchecker's LibChecker-Rules-Bundle, fossify's PatternLockView.
    "https://jitpack.io",
)
LIBRARY_DOWNLOAD_TIMEOUT_SECONDS = 120


def library_download_root(environ: dict | None = None) -> Path:
    """Where binaries fetched by coordinate are kept, in the Gradle layout."""
    environ = os.environ if environ is None else environ
    configured = environ.get(LIBRARY_DOWNLOAD_DIR_ENV, "")
    return Path(configured) if configured else Path.home() / ".cache" / "tcc_library_artifacts"


def gradle_cache_roots(environ: dict | None = None) -> list[Path]:
    """The module caches a Gradle run on this machine would have filled.

    Plus the pipeline's own download directory, laid out the same way, which
    holds library binaries fetched by coordinate (see fetch_library_binary).
    """
    environ = os.environ if environ is None else environ
    homes = []
    if environ.get("GRADLE_USER_HOME"):
        homes.append(Path(environ["GRADLE_USER_HOME"]))
    homes.append(Path.home() / ".gradle")
    roots: list[Path] = []
    seen: set[str] = set()
    for root in [home / "caches" / "modules-2" / "files-2.1" for home in homes] + [
            library_download_root(environ)]:
        if str(root) in seen:
            continue
        seen.add(str(root))
        if root.is_dir():
            roots.append(root)
    return roots


def fetch_library_binary(group: str, artifact: str, version: str,
                         environ: dict | None = None) -> Path | None:
    """Download a library's jar or aar by coordinate; None when not allowed.

    `gradle dependencies` resolves the graph from POMs and module files and
    never downloads a jar, so a dependency tree resolved online names the
    owning library and its exact version while the class stays out of reach:
    nextcloudpasswords' tink-android 1.19.0 in run_20261011_091101. Only with
    online resolution enabled; the binary is fetched from the public Maven
    repositories into library_download_root(), never into Gradle's own cache.
    """
    import urllib.request

    environ = os.environ if environ is None else environ
    if environ.get(ONLINE_RESOLUTION_ENV, "") != "1" or not (group and artifact and version):
        return None
    target_dir = library_download_root(environ) / group / artifact / version / "download"
    for suffix in (".jar", ".aar"):
        existing = target_dir / f"{artifact}-{version}{suffix}"
        if existing.is_file() and zipfile.is_zipfile(existing):
            return existing
    path = f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}"
    for repository in LIBRARY_REPOSITORIES:
        for suffix in (".jar", ".aar"):
            target = target_dir / f"{artifact}-{version}{suffix}"
            temporary = None
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                handle, temporary = tempfile.mkstemp(dir=target_dir, suffix=".part")
                os.close(handle)
                request = urllib.request.Request(
                    f"{repository}/{path}{suffix}",
                    headers={"User-Agent": "tcc-library-artifact/1.0"},
                )
                with urllib.request.urlopen(
                        request, timeout=LIBRARY_DOWNLOAD_TIMEOUT_SECONDS) as response, \
                        open(temporary, "wb") as output:
                    shutil.copyfileobj(response, output, 1 << 20)
                if zipfile.is_zipfile(temporary):
                    os.replace(temporary, target)
                    return target
            except (OSError, ValueError):
                continue
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
    return None


def fetch_library_dependencies(group: str, artifact: str, version: str,
                               environ: dict | None = None) -> list[dict]:
    """What a library's published .module (or .pom) declares, fetched by coordinate.

    For a library whose metadata no Gradle run on this machine cached, so its
    dependencies can still be followed. Same conditions and directory as
    fetch_library_binary; [] when not allowed or not found.
    """
    import urllib.request

    environ = os.environ if environ is None else environ
    if environ.get(ONLINE_RESOLUTION_ENV, "") != "1" or not (group and artifact and version):
        return []
    target_dir = library_download_root(environ) / group / artifact / version / "download"
    path = f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}"
    for suffix, reader in ((".module", read_module_dependencies), (".pom", read_pom_dependencies)):
        target = target_dir / f"{artifact}-{version}{suffix}"
        if not target.is_file():
            for repository in LIBRARY_REPOSITORIES:
                try:
                    request = urllib.request.Request(
                        f"{repository}/{path}{suffix}",
                        headers={"User-Agent": "tcc-library-artifact/1.0"},
                    )
                    with urllib.request.urlopen(
                            request, timeout=LIBRARY_DOWNLOAD_TIMEOUT_SECONDS) as response:
                        text = response.read(5_000_000).decode("utf-8", errors="replace")
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8")
                    break
                except (OSError, ValueError):
                    continue
        try:
            return reader(target.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError, ElementTree.ParseError):
            continue
    return []


def _same_owner(dependency_group: str, owner_group: str, class_name: str) -> bool:
    """A dependency the owner's own publisher made: same group, or one naming the package.

    com.google.protobuf under tink is someone else's library even though both
    start with com.google; io.ktor under io.ktor is the same project.
    """
    return dependency_group == owner_group or class_name.startswith(dependency_group + ".")


def _named_like_package(group: str, artifact: str, class_name: str) -> bool:
    """ktor-utils-jvm for io.ktor.util.CryptoKt: a name token matches a package segment."""
    ignored = set(group.lower().split(".")) | {"jvm", "android", "core", "common", "api"}
    segments = {segment.lower() for segment in class_name.split(".")[:-1]} - ignored
    tokens = set(re.split(r"[-_.]", artifact.lower())) - ignored
    return any(len(token) >= 3 and len(segment) >= 3
               and (token.startswith(segment) or segment.startswith(token))
               for token in tokens for segment in segments)


def version_key(version: str) -> tuple:
    """Order Maven versions well enough to pick the nearest cached one.

    Not a full Maven comparator. It gets the cases that decide a nearest
    version right: numeric components compare as numbers, and a pre-release
    (1.1.0-alpha06) sorts below its release (1.1.0).
    """
    text = str(version or "").strip().lower()
    base, _, qualifier = text.partition("-")
    numbers = []
    for piece in re.split(r"[.+_]", base):
        match = re.match(r"\d+", piece)
        numbers.append(int(match.group(0)) if match else 0)
    numbers = (numbers + [0] * _VERSION_WIDTH)[:_VERSION_WIDTH]
    if not qualifier:
        rank = (_RELEASE_RANK, 0)
    else:
        name = re.match(r"[a-z]*", qualifier).group(0)
        digits = re.search(r"\d+", qualifier)
        rank = (
            _QUALIFIER_RANK.get(name, _UNKNOWN_QUALIFIER_RANK),
            int(digits.group(0)) if digits else 0,
        )
    return tuple(numbers), rank


def select_cached_version(requested: str, available: Iterable[str]) -> tuple[str, bool]:
    """The cached version to read for a requested one, and whether it is exact.

    The same major.minor line is preferred, because that is where published
    dependencies stay stable: maskan requests security-crypto 1.1.0-alpha06,
    the cache holds 1.0.0 and 1.1.0, and 1.1.0 is the nearer description of
    what the project gets. An unparseable or empty request (an unresolved
    catalog reference) reads the newest cached version.
    """
    versions = sorted(set(available), key=version_key)
    if not versions:
        return "", False
    requested = str(requested or "").strip()
    if requested in versions:
        return requested, True
    if not re.match(r"^\d", requested):
        return versions[-1], False
    target = version_key(requested)
    line = target[0][:2]
    same_line = [version for version in versions if version_key(version)[0][:2] == line]
    if same_line:
        at_or_above = [v for v in same_line if version_key(v) >= target]
        return (at_or_above[0] if at_or_above else same_line[-1]), False
    below = [version for version in versions if version_key(version) <= target]
    return (below[-1] if below else versions[0]), False


def read_module_dependencies(text: str) -> list[dict]:
    """Dependencies and redirects a Gradle module metadata file declares."""
    document = json.loads(text)
    found: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for variant in document.get("variants", []) or []:
        if not isinstance(variant, dict):
            continue
        attributes = variant.get("attributes") or {}
        if attributes.get("org.gradle.category", "library") not in _CODE_CATEGORIES:
            continue
        entries: list[tuple[dict, str, str]] = []
        redirect = variant.get("available-at")
        # A multiplatform module publishes no code of its own: okhttp 5.3.2
        # redirects to okhttp-android and okhttp-jvm, which ship the classes.
        if isinstance(redirect, dict):
            entries.append((redirect, "available_at", str(redirect.get("version", ""))))
        for dependency in variant.get("dependencies", []) or []:
            if not isinstance(dependency, dict):
                continue
            version = dependency.get("version") or {}
            if isinstance(version, dict):
                requested = (
                    version.get("strictly") or version.get("requires")
                    or version.get("prefers") or ""
                )
            else:
                requested = str(version)
            entries.append((dependency, "dependency", str(requested)))
        for entry, relation, requested in entries:
            group = str(entry.get("group", ""))
            module = str(entry.get("module", ""))
            if not group or not module or (group, module, relation) in seen:
                continue
            seen.add((group, module, relation))
            found.append({
                "group": group, "artifact": module,
                "version": requested, "relation": relation,
            })
    return found


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_pom_dependencies(text: str) -> list[dict]:
    """Runtime dependencies a Maven POM declares (test and provided excluded)."""
    root = ElementTree.fromstring(text)
    properties: dict[str, str] = {}
    for child in root:
        name = _local_name(child.tag)
        if name == "properties":
            for prop in child:
                properties[_local_name(prop.tag)] = (prop.text or "").strip()
        elif name == "version":
            properties.setdefault("project.version", (child.text or "").strip())
    found: list[dict] = []
    for child in root:
        # dependencyManagement only constrains versions; it adds nothing.
        if _local_name(child.tag) != "dependencies":
            continue
        for dependency in child:
            fields = {
                _local_name(element.tag): (element.text or "").strip()
                for element in dependency
            }
            if (fields.get("scope") or "compile") in _SKIPPED_POM_SCOPES:
                continue
            if fields.get("optional") == "true":
                continue
            if not fields.get("groupId") or not fields.get("artifactId"):
                continue
            version = re.sub(
                r"\$\{([^}]+)\}",
                lambda match: properties.get(match.group(1), match.group(0)),
                fields.get("version", ""),
            )
            found.append({
                "group": fields["groupId"], "artifact": fields["artifactId"],
                "version": version, "relation": "dependency",
            })
    return found


class ArtifactCache:
    """Lookups over one or more Gradle module caches, memoised per process."""

    def __init__(self, roots: Iterable[Path] | None = None):
        self.roots = list(gradle_cache_roots() if roots is None else roots)
        self._dependencies: dict[tuple[str, str, str], tuple[list[dict], str]] = {}

    def versions(self, group: str, artifact: str) -> dict[str, list[Path]]:
        found: dict[str, list[Path]] = {}
        for root in self.roots:
            base = root / group / artifact
            if not base.is_dir():
                continue
            for version_dir in base.iterdir():
                if version_dir.is_dir():
                    found.setdefault(version_dir.name, []).append(version_dir)
        return found

    def files(self, group: str, artifact: str, version: str) -> list[Path]:
        # Layout: <root>/<group>/<artifact>/<version>/<sha1>/<file>
        paths: list[Path] = []
        for version_dir in self.versions(group, artifact).get(version, []):
            paths.extend(path for path in version_dir.glob("*/*") if path.is_file())
        return sorted(paths)

    def dependencies(self, group: str, artifact: str, version: str) -> tuple[list[dict], str]:
        """What the cached metadata declares, and which file said so.

        The file name is empty when no readable metadata is cached, which is
        different from metadata that declares no dependencies.
        """
        key = (group, artifact, version)
        if key not in self._dependencies:
            files = self.files(group, artifact, version)
            readers = (
                (".module", read_module_dependencies),
                (".pom", read_pom_dependencies),
            )
            result: tuple[list[dict], str] = ([], "")
            for suffix, reader in readers:
                for path in (p for p in files if p.suffix == suffix):
                    try:
                        result = (reader(path.read_text(encoding="utf-8", errors="replace")), path.name)
                    except (OSError, ValueError, ElementTree.ParseError):
                        continue
                    break
                if result[1]:
                    break
            self._dependencies[key] = result
        return self._dependencies[key]

    def binaries(self, group: str, artifact: str, version: str) -> list[Path]:
        # By suffix, never by expected name: okhttp-android 5.3.2 is cached as
        # okhttp-release.aar.
        return [
            path for path in self.files(group, artifact, version)
            if path.suffix in _BINARY_SUFFIXES
            and not any(tag in path.stem for tag in _NON_CODE_CLASSIFIERS)
        ]


_SHARED_CACHE: ArtifactCache | None = None


def shared_cache() -> ArtifactCache:
    global _SHARED_CACHE
    if _SHARED_CACHE is None:
        _SHARED_CACHE = ArtifactCache()
    return _SHARED_CACHE


def _hop(group: str, artifact: str, requested: str, version: str,
         exact: bool, metadata_file: str) -> dict:
    return {
        "coordinate": f"{group}:{artifact}",
        "requested_version": requested,
        "cached_version": version,
        "version_exact": exact,
        "metadata_file": metadata_file,
    }


def _owners_reached_from(cache: ArtifactCache, group: str, artifact: str,
                         requested: str, owns: Callable[[str], bool]) -> list[dict] | None:
    """Owning modules reachable from one declared artifact, with their paths.

    None when the declared artifact has no cached metadata at all, so the
    caller can report it as unexamined rather than as carrying nothing.
    """
    version, exact = select_cached_version(requested, cache.versions(group, artifact))
    if not version:
        return None
    _, metadata_file = cache.dependencies(group, artifact, version)
    if not metadata_file:
        return None
    start = [_hop(group, artifact, requested, version, exact, metadata_file)]
    queue = deque([(group, artifact, version, start)])
    visited = {(group, artifact)}
    reached: list[dict] = []
    reached_owners: set[tuple[str, str]] = set()
    while queue and len(visited) <= MAX_VISITED_MODULES:
        current_group, current_artifact, current_version, path = queue.popleft()
        dependencies, _ = cache.dependencies(current_group, current_artifact, current_version)
        for dependency in dependencies:
            coordinate = (dependency["group"], dependency["artifact"])
            if owns(dependency["group"]):
                if coordinate not in reached_owners:
                    reached_owners.add(coordinate)
                    reached.append({"owner": dict(dependency), "path": path})
                continue
            if coordinate in visited or len(path) >= MAX_TRAVERSAL_DEPTH:
                continue
            visited.add(coordinate)
            next_version, next_exact = select_cached_version(
                dependency["version"], cache.versions(*coordinate)
            )
            if not next_version:
                continue
            _, next_file = cache.dependencies(*coordinate, next_version)
            if not next_file:
                continue
            queue.append((
                *coordinate, next_version,
                path + [_hop(*coordinate, dependency["version"], next_version,
                             next_exact, next_file)],
            ))
    return reached


def trace_owner_through_metadata(declarations: Iterable[dict],
                                 owns: Callable[[str], bool],
                                 cache: ArtifactCache | None = None) -> dict:
    """Declared artifacts whose cached metadata leads to the owning library.

    This is metadata evidence, not a name match: security-crypto shares no
    token with Tink, but its published POM lists tink-android. A carrier is
    `confirmed` only when every hop was read from the exact version
    requested; otherwise the record says which cached version was read.
    """
    cache = shared_cache() if cache is None else cache
    if not cache.roots:
        return {"status": "cache_unavailable", "carriers": [],
                "reason": "no Gradle module cache found on this machine"}
    carriers: list[dict] = []
    unexamined: list[str] = []
    examined = 0
    seen: set[tuple[str, str]] = set()
    for item in declarations:
        group = str(item.get("group", ""))
        artifact = str(item.get("artifact", ""))
        if not group or not artifact or (group, artifact) in seen:
            continue
        seen.add((group, artifact))
        if owns(group):
            # The owner itself, declared directly: not a carrier.
            continue
        requested = str(item.get("version_expression", ""))
        reached = _owners_reached_from(cache, group, artifact, requested, owns)
        if reached is None:
            unexamined.append(f"{group}:{artifact}")
            continue
        examined += 1
        for found in reached:
            owner = found["owner"]
            path = found["path"]
            exact = all(hop["version_exact"] for hop in path)
            owner_coordinate = f"{owner['group']}:{owner['artifact']}"
            first = path[0]
            basis = (
                f"{path[-1]['metadata_file']} in the local Gradle cache lists "
                f"{owner_coordinate} as a {owner['relation'].replace('_', '-')}"
            )
            if not exact:
                basis += (
                    f"; read from cached {first['coordinate']}:"
                    f"{first['cached_version']}, not the declared "
                    f"{first['requested_version'] or 'unresolved version'}"
                )
            carriers.append({
                "group": group,
                "artifact": artifact,
                "version_expression": requested,
                "source_file_path": item.get("source_file_path", ""),
                "line": item.get("line"),
                "owner": owner,
                "dependency_path": [hop["coordinate"] for hop in path] + [owner_coordinate],
                "hops": path,
                "basis": basis,
                "confirmed": exact,
            })
    return {
        "status": "found" if carriers else "not_found",
        "carriers": carriers[:20],
        "examined_declarations": examined,
        "declarations_without_cached_metadata": unexamined[:20],
    }


def github_repository_url(text: str) -> str:
    """A canonical https://github.com/<owner>/<repo> from any SCM spelling."""
    match = re.search(
        r"github\.com[:/]+(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?(?:[/#?].*)?\s*$",
        str(text or "").strip(), re.IGNORECASE,
    )
    if not match:
        return ""
    return f"https://github.com/{match.group('owner')}/{match.group('repo')}"


def upstream_repository(group: str, artifact: str, version: str,
                        cache: ArtifactCache | None = None) -> dict:
    """Where a library is developed, as its own published POM says.

    The <scm> block names the repository, and a POM that inherits it names
    its parent instead, so one parent is followed. Only a GitHub repository
    is returned, because that is the only host an issue can be filed on here.
    """
    cache = shared_cache() if cache is None else cache
    for _ in range(3):
        pom = next(
            (path for path in cache.files(group, artifact, version)
             if path.suffix == ".pom"),
            None,
        )
        if pom is None:
            return {}
        try:
            root = ElementTree.fromstring(pom.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ElementTree.ParseError):
            return {}
        candidates = []
        parent = None
        for child in root:
            name = _local_name(child.tag)
            if name == "scm":
                candidates.extend(
                    element.text or "" for element in child
                    if _local_name(element.tag) in {"url", "connection", "developerConnection"}
                )
            elif name == "url":
                candidates.append(child.text or "")
            elif name == "parent":
                parent = {
                    _local_name(element.tag): (element.text or "").strip()
                    for element in child
                }
        for text in candidates:
            url = github_repository_url(text)
            if url:
                return {
                    "url": url,
                    "coordinate": f"{group}:{artifact}:{version}",
                    "basis": f"{pom.name} in the local Gradle cache names it",
                }
        if not parent:
            return {}
        group = parent.get("groupId", "")
        artifact = parent.get("artifactId", "")
        version = parent.get("version", "")
        if not (group and artifact and version):
            return {}
    return {}


def upstream_repository_for(owners: Iterable[dict],
                            cache: ArtifactCache | None = None) -> dict:
    """The first owning library whose repository its cached POM names."""
    cache = shared_cache() if cache is None else cache
    seen: set[tuple[str, str]] = set()
    for owner in owners:
        group = str(owner.get("group", ""))
        artifact = str(owner.get("artifact", ""))
        if not group or not artifact or (group, artifact) in seen:
            continue
        seen.add((group, artifact))
        version, _ = select_cached_version(
            str(owner.get("version") or owner.get("version_expression") or ""),
            cache.versions(group, artifact),
        )
        if not version:
            continue
        found = upstream_repository(group, artifact, version, cache)
        if found:
            return found
    return {}


def find_javap(environ: dict | None = None) -> str:
    environ = os.environ if environ is None else environ
    found = shutil.which("javap")
    if found:
        return found
    java_home = environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / ("javap.exe" if os.name == "nt" else "javap")
        if candidate.is_file():
            return str(candidate)
    return ""


def class_entry_name(class_name: str) -> str:
    return class_name.replace(".", "/") + ".class"


def read_class_bytes(binary: Path, entry: str) -> bytes | None:
    """The class file from a jar, or from the jars packed inside an aar."""
    try:
        with zipfile.ZipFile(binary) as archive:
            names = archive.namelist()
            if entry in names:
                return archive.read(entry)
            if binary.suffix == ".aar":
                for inner in sorted(name for name in names if name.endswith(".jar")):
                    with zipfile.ZipFile(io.BytesIO(archive.read(inner))) as nested:
                        if entry in nested.namelist():
                            return nested.read(entry)
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    return None


_MEMBER_HEADER = re.compile(r"^  \S.*\)(?:\s+throws\s+[\w.$,\s]+)?;$")


def extract_method_blocks(javap_output: str, method_name: str,
                          class_name: str = "") -> list[dict]:
    """Every overload of `method_name` in `javap -c -p` output.

    Kotlin mangles some names (`encrypt-abc123`), so a hyphenated suffix
    still matches; a constructor is printed under the class's simple name.
    """
    name = method_name
    if method_name == "<init>" and class_name:
        name = re.split(r"[.$]", class_name)[-1]
    pattern = re.compile(rf"(?:^|[\s.]){re.escape(name)}(?:-[\w$]+)?\(")
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in javap_output.splitlines():
        if _MEMBER_HEADER.match(line):
            if current:
                blocks.append(current)
            current = [line] if pattern.search(line.split("(", 1)[0] + "(") else None
            continue
        if current is None:
            continue
        if not line.strip() or line.startswith("}"):
            blocks.append(current)
            current = None
        else:
            current.append(line)
    if current:
        blocks.append(current)
    methods = []
    for block in blocks[:MAX_METHOD_OVERLOADS]:
        text = "\n".join(block)
        methods.append({
            "signature": block[0].strip(),
            "disassembly": text[:MAX_METHOD_CHARACTERS],
            "truncated": len(text) > MAX_METHOD_CHARACTERS,
        })
    return methods


def disassemble_method(class_bytes: bytes, class_name: str, method_name: str,
                       javap: str) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / (re.split(r"[./]", class_name)[-1] + ".class")
        path.write_bytes(class_bytes)
        try:
            completed = subprocess.run(
                [javap, "-c", "-p", str(path)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=JAVAP_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return {"status": "unavailable", "reason": "javap_timeout"}
        except OSError as exc:
            return {"status": "unavailable", "reason": f"javap_start_failed: {exc}"}
    if completed.returncode != 0:
        return {
            "status": "unavailable",
            "reason": f"javap_exit_{completed.returncode}",
            "diagnostic": (completed.stderr or "")[-600:],
        }
    methods = extract_method_blocks(completed.stdout, method_name, class_name)
    return {
        "status": "available" if methods else "method_not_found",
        "methods": methods,
    }


_REPOSITORY_BINARY_SKIP = {"build", ".gradle", ".git", "node_modules", "intermediates"}
MAX_REPOSITORY_BINARIES = 60


def locate_repository_binary_method(repo_root: Path, class_name: str,
                                    method_name: str, javap: str | None = None) -> dict:
    """The reported method from a jar or aar the repository itself carries.

    Some projects ship a library as a file in the checkout instead of a
    coordinate: org.glpi.inventory.agent builds against
    app/libs/inventory-release-v1.6.1.aar, so no dependency tree or Maven
    repository ever names org.flyve.inventory. Build outputs are skipped.
    """
    repo_root = Path(repo_root)
    entry = class_entry_name(class_name)
    examined = 0
    for current, directories, files in os.walk(repo_root):
        directories[:] = [name for name in directories
                          if name not in _REPOSITORY_BINARY_SKIP and not name.startswith(".")]
        for name in sorted(files):
            if not name.endswith((".jar", ".aar")) or name.endswith("-sources.jar"):
                continue
            if name == "gradle-wrapper.jar":
                continue
            examined += 1
            if examined > MAX_REPOSITORY_BINARIES:
                return {"status": "unavailable", "reason": "too_many_repository_binaries"}
            binary = Path(current) / name
            data = read_class_bytes(binary, entry)
            if data is None:
                continue
            relative = binary.relative_to(repo_root).as_posix()
            record = {
                "source": "repository_binary",
                "artifact": relative,
                "binary": name,
                "class_entry": entry,
                "tool": "javap -c -p",
                "version_exact": True,
                "bound": (
                    f"Bytecode from {relative}, a binary the repository itself "
                    "carries and builds against."
                ),
            }
            javap = find_javap() if javap is None else javap
            if not javap:
                return {**record, "status": "unavailable", "reason": "javap_not_found"}
            return {**record, **disassemble_method(data, class_name, method_name, javap)}
    return {"status": "unavailable", "reason": "class_not_in_repository_binaries"}


def locate_method_bytecode(owners: Iterable[dict], class_name: str,
                           method_name: str, cache: ArtifactCache | None = None,
                           javap: str | None = None) -> dict:
    """Disassemble the reported method from the owning library's cached binary.

    `owners` are coordinates believed to ship the class (group, artifact and
    version or version_expression), tried in order. A module that only
    redirects elsewhere (`available-at`) is followed to the one with code.
    """
    if not method_name:
        return {}
    cache = shared_cache() if cache is None else cache
    if not cache.roots:
        return {"status": "unavailable", "reason": "gradle_cache_not_found"}
    javap = find_javap() if javap is None else javap
    entry = class_entry_name(class_name)
    queue = deque(owners)
    seen: set[tuple[str, str]] = set()
    searched: list[dict] = []
    # Declared owners and transitive ones have separate limits, so a long
    # walk down one library's dependencies cannot crowd out a declared owner.
    examined = {"declared": 0, "transitive": 0}
    limits = {"declared": MAX_BYTECODE_ARTIFACTS, "transitive": MAX_TRANSITIVE_ARTIFACTS}
    while queue:
        owner = queue.popleft()
        group = str(owner.get("group", ""))
        artifact = str(owner.get("artifact", ""))
        if not group or not artifact or (group, artifact) in seen:
            continue
        kind = "transitive" if owner.get("transitive_of") else "declared"
        if examined[kind] >= limits[kind]:
            if all(examined[name] >= limits[name] for name in limits):
                break
            continue
        examined[kind] += 1
        seen.add((group, artifact))
        requested = str(owner.get("version") or owner.get("version_expression") or "")
        version, exact = select_cached_version(requested, cache.versions(group, artifact))
        fetched_without_metadata = None
        if not exact and re.fullmatch(r"[0-9][\w.\-]*", requested):
            # The requested version is not cached -- nothing of this
            # coordinate is, or only another version (untis asked for
            # sentry 8.14.0 and the cache held 7.0.0). It is concrete, so
            # its binary can be fetched instead of reading another release.
            fetched_without_metadata = fetch_library_binary(group, artifact, requested)
            if fetched_without_metadata:
                version, exact = requested, True
        searched.append({
            "coordinate": f"{group}:{artifact}",
            "requested_version": requested,
            "cached_version": version,
            **({"transitive_of": owner["transitive_of"]} if owner.get("transitive_of") else {}),
        })
        if not version:
            continue
        binaries = (
            [fetched_without_metadata] if fetched_without_metadata
            else cache.binaries(group, artifact, version)
        )
        if not binaries:
            # Only the metadata is cached (a dependency tree resolved
            # online): fetch the binary of exactly that version.
            fetched = fetch_library_binary(group, artifact, version)
            binaries = [fetched] if fetched else []
        for binary in binaries:
            data = read_class_bytes(binary, entry)
            if data is None:
                continue
            requested_confirmed = owner.get("version_confirmed", True) is not False
            downloaded = library_download_root() in binary.parents
            record = {
                "source": (
                    "library_repository_download" if downloaded
                    else "gradle_artifact_cache"
                ),
                "artifact": f"{group}:{artifact}",
                "requested_version": requested,
                # Whether the requested version is itself settled: one read
                # through an approximate carrier is not what the build asks for.
                "requested_version_confirmed": requested_confirmed,
                "cached_version": version,
                "version_exact": exact and requested_confirmed,
                **({"transitive_of": owner["transitive_of"]} if owner.get("transitive_of") else {}),
                "binary": binary.name,
                "class_entry": entry,
                "tool": "javap -c -p",
                "bound": (
                    f"Bytecode of {group}:{artifact}:{version} from "
                    + ("the public Maven repository" if downloaded
                       else "the local Gradle cache")
                    + ". It is the library's code, not this "
                    "project's, and the analysed APK may ship a different "
                    "version"
                    + ("" if exact else f" (requested {requested or 'unresolved'})")
                    + ("." if requested_confirmed else
                       "; the requested version was itself read from cached "
                       "metadata of a version other than the one the project "
                       "declares.")
                ),
            }
            if not javap:
                return {**record, "status": "unavailable", "reason": "javap_not_found"}
            return {**record, **disassemble_method(data, class_name, method_name, javap)}
        dependencies, metadata_file = cache.dependencies(group, artifact, version)
        if not metadata_file:
            dependencies = fetch_library_dependencies(group, artifact, version)
        # A redirect is the same library, so it keeps the owner's standing.
        queue.extend(
            {**dependency, "version_confirmed": owner.get("version_confirmed", True),
             **({"transitive_of": owner["transitive_of"]} if owner.get("transitive_of") else {})}
            for dependency in dependencies
            if dependency["relation"] == "available_at"
        )
        # The class may ship in a library the declared one depends on:
        # retrowars declares ktor-client-core, and io.ktor.util.CryptoKt is in
        # ktor-utils, two dependencies down. Only the owner's own group is
        # followed, and a module named like the class's package goes first.
        for dependency in dependencies:
            if dependency["relation"] != "dependency" \
                    or not _same_owner(dependency["group"], group, class_name):
                continue
            item = {**dependency, "version_confirmed": owner.get("version_confirmed", True),
                    "transitive_of": f"{group}:{artifact}:{version}"}
            if _named_like_package(dependency["group"], dependency["artifact"], class_name):
                queue.appendleft(item)
            else:
                queue.append(item)
    return {
        "status": "unavailable",
        "reason": "class_not_found_in_cached_artifacts",
        "searched": searched,
    }
