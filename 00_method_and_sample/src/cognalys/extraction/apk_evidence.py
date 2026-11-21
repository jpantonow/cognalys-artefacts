"""The analysed APK as evidence for a finding whose class is not in the checkout.

A detector finding names a class and a method inside an APK. When that class
is not in the repository checkout, the pipeline had only indirect sources
left: the build files, which say what the project asks for, and the local
Gradle cache, which holds some version of what it might get. Findings
analysed that way stopped on the same question -- is this the code the APK
actually ships? -- and app.maskan.chat's Tink finding and
ch.rmy.android.http_shortcuts' Conscrypt finding both closed UNCONFIRMED on
it, with the cached bytecode in the packet.

The APK answers that question. F-Droid publishes the exact build the detector
analysed (the sample CSV carries its apk_url), its dex holds the reported
method, and AndroidX and Google libraries write the version the build
resolved into META-INF/<group>_<artifact>.version. dexdump, from the Android
build tools the build step already needs, reads the dex without Gradle.

It also covers a case the cache never can: a class in the application's own
package that the checkout does not contain, because the name is obfuscated
(com.mde.potdroid.helpers.f) or the checkout is a different revision
(org.mifos.mobilebanking's SelfServiceOkHttpClient). Those were recorded as
dependency candidates, which they are not.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Iterator

from cognalys.extraction.artifact_cache import version_key
from cognalys.extraction.repository_index import build_repository_index


APK_DOWNLOAD_TIMEOUT_SECONDS = 300
DEXDUMP_TIMEOUT_SECONDS = 300
MAX_METHOD_CHARACTERS = 12_000
MAX_METHOD_OVERLOADS = 4
MAX_LIBRARY_VERSIONS = 300

# R8 and ProGuard rename classes to one or two lower-case letters. A real
# class name is almost never spelled that way, so it is a usable hint -- and
# only a hint: it changes the explanation, never what is analysed.
_OBFUSCATED_CLASS = re.compile(r"^[a-z]{1,2}\d?$")

_CLASS_HEADER = re.compile(r"^Class #\d+")
_CLASS_DESCRIPTOR = re.compile(r"^\s+Class descriptor\s+:\s+'(?P<descriptor>[^']+)'")
_MEMBER_START = re.compile(r"^\s+#\d+\s+:\s+\(in L")
_MEMBER_FIELD = re.compile(r"^\s+(?P<key>name|type|access)\s+:\s+(?P<value>.*)$")
_INSTRUCTION = re.compile(r"^[0-9a-f]{6}:[^|]*\|(?P<text>.*)$")
_DEX_ENTRY = re.compile(r"classes\d*\.dex")


def apk_evidence_enabled(environ: dict | None = None) -> bool:
    """Whether to fetch and read the analysed APK (on unless switched off)."""
    environ = os.environ if environ is None else environ
    return str(environ.get("TCC_SKIP_APK_EVIDENCE", "")).strip().lower() not in {
        "1", "true", "yes",
    }


def application_package(apk_name: str, declared: str = "") -> str:
    """The application id the APK was published under.

    The sample CSV carries it as `pacote`, which wins. Otherwise F-Droid's
    file name is <applicationId>_<versionCode>.apk.
    """
    declared = str(declared or "").strip()
    if declared:
        return declared
    # The suffix is a versionCode on F-Droid (_90) and a versionName in other
    # samples (_5.148.0); the package ends at the first "_<digit>".
    name = str(apk_name or "").strip()
    if not name.endswith(".apk"):
        return ""
    version = re.search(r"_\d", name)
    if not version:
        return ""
    package = name[:version.start()]
    return package if re.fullmatch(r"[A-Za-z][\w.]*", package) else ""


def class_in_application_namespace(class_name: str, package: str) -> bool:
    outer = str(class_name or "").split("$", 1)[0]
    package = str(package or "").strip()
    return bool(package) and (outer == package or outer.startswith(package + "."))


_NAMESPACE_DECLARATION = re.compile(
    r"""\b(?:namespace|applicationId)\s*(?:=\s*)?["'](?P<name>[A-Za-z][\w.]*)["']"""
)
_MANIFEST_TAG = re.compile(r"<manifest\b[^>]*>")
_MANIFEST_PACKAGE = re.compile(r"""\bpackage\s*=\s*["'](?P<name>[A-Za-z][\w.]*)["']""")
_NAMESPACE_FILES = {"build.gradle", "build.gradle.kts", "AndroidManifest.xml"}


def repository_namespaces(repo_root: Path, paths: Iterable[Path] | None = None) -> list[str]:
    """Every code namespace the repository's modules declare for themselves.

    The application id is not where the code lives. DuckDuckGo publishes as
    com.duckduckgo.mobile.android while its app module's namespace is
    com.duckduckgo.app.browser and the reported class is
    com.duckduckgo.app.global.api.ApiRequest, so the application id alone
    classifies the application's own code as a library.

    Reads the file list of the repository index the extraction already built
    for this checkout. Walking the tree again took 174 seconds on DuckDuckGo's
    checkout over the mounted Windows filesystem.
    """
    if paths is None:
        paths = build_repository_index(Path(repo_root))["paths"]
    found: list[str] = []
    for path in paths:
        name = Path(path).name
        if name not in _NAMESPACE_FILES:
            continue
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")[:200_000]
        except OSError:
            continue
        if name.endswith(".xml"):
            names = [
                package.group("name")
                for tag in _MANIFEST_TAG.finditer(text)
                for package in [_MANIFEST_PACKAGE.search(tag.group(0))]
                if package
            ]
        else:
            names = [match.group("name") for match in _NAMESPACE_DECLARATION.finditer(text)]
        for declared in names:
            if declared not in found:
                found.append(declared)
    return found


def class_in_application_namespaces(class_name: str, namespaces: Iterable[str],
                                    owned_by_dependency: bool = False) -> bool:
    """Whether a class belongs to the application rather than to a library.

    A class inside any declared namespace belongs to it. A class sharing the
    first three package segments with one -- com.duckduckgo.app.global beside
    com.duckduckgo.app.browser -- belongs to it too, unless a declared
    dependency's group owns the class: an application under
    com.google.android.apps must not claim com.google.android.gms.
    """
    outer = str(class_name or "").split("$", 1)[0]
    declared = [str(name).strip() for name in namespaces if str(name or "").strip()]
    if any(outer == name or outer.startswith(name + ".") for name in declared):
        return True
    if owned_by_dependency or "." not in outer:
        return False
    segments = outer.rsplit(".", 1)[0].split(".")
    return len(segments) >= 3 and any(
        len(name.split(".")) >= 3 and name.split(".")[:3] == segments[:3]
        for name in declared
    )


def name_looks_obfuscated(class_name: str, method_name: str = "") -> bool:
    simple = str(class_name or "").split("$", 1)[0].rsplit(".", 1)[-1]
    method = str(method_name or "")
    return bool(_OBFUSCATED_CLASS.match(simple)) or (
        len(method) == 1 and method.islower()
    )


def find_dexdump(environ: dict | None = None) -> str:
    """dexdump from PATH or from the newest installed Android build tools."""
    environ = os.environ if environ is None else environ
    found = shutil.which("dexdump")
    if found:
        return found
    roots = [
        environ.get("ANDROID_HOME"), environ.get("ANDROID_SDK_ROOT"),
        str(Path.home() / "Android" / "Sdk"),
        str(Path.home() / "Library" / "Android" / "sdk"),
        "/opt/android-sdk",
    ]
    binary = "dexdump.exe" if os.name == "nt" else "dexdump"
    for root in roots:
        if not root:
            continue
        build_tools = Path(root) / "build-tools"
        if not build_tools.is_dir():
            continue
        versions = sorted(
            (path for path in build_tools.iterdir() if path.is_dir()),
            key=lambda path: version_key(path.name), reverse=True,
        )
        for version in versions:
            if (version / binary).is_file():
                return str(version / binary)
    return ""


def fetch_apk(apk_url: str, cache_dir: Path, apk_name: str,
              timeout: int = APK_DOWNLOAD_TIMEOUT_SECONDS) -> Path:
    """The APK at apk_url, downloaded once into cache_dir and reused after.

    A local path (or file:// URL) is used where it is: a runtime-verification
    package can ship the very APKs its campaign instrumented, and those, not
    a store release, are the build the findings were observed on.
    """
    local = str(apk_url or "").strip()
    if local.startswith("file://"):
        local = local[len("file://"):]
    if local and not local.startswith(("http://", "https://")):
        path = Path(local)
        if path.is_file() and zipfile.is_zipfile(path):
            return path
        raise ValueError(f"local APK not found or not an APK archive: {apk_url!r}")
    cache_dir = Path(cache_dir)
    target = cache_dir / Path(apk_name).name
    if target.is_file() and zipfile.is_zipfile(target):
        return target
    if not str(apk_url or "").startswith("https://"):
        raise ValueError(f"not an https APK URL: {apk_url!r}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=cache_dir, suffix=".part")
    os.close(handle)
    try:
        request = urllib.request.Request(
            apk_url, headers={"User-Agent": "apk-evidence/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response, \
                open(temporary, "wb") as output:
            shutil.copyfileobj(response, output, 1 << 20)
        if not zipfile.is_zipfile(temporary):
            raise ValueError(f"{apk_url} did not return an APK archive")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def library_versions(apk_path: Path) -> dict[str, str]:
    """The library versions the APK records about itself.

    AndroidX and several Google libraries write the version Gradle resolved
    into META-INF/<group>_<artifact>.version, so this is the build's answer,
    not the declaration's. Libraries that write no such file are simply
    absent.
    """
    versions: dict[str, str] = {}
    with zipfile.ZipFile(apk_path) as archive:
        for name in sorted(archive.namelist()):
            if not (name.startswith("META-INF/") and name.endswith(".version")):
                continue
            if name.count("/") != 1:
                continue
            value = archive.read(name)[:200].decode("utf-8", "replace").strip()
            if value:
                versions[name[len("META-INF/"):-len(".version")]] = value
            if len(versions) >= MAX_LIBRARY_VERSIONS:
                break
    return versions


def class_descriptor(class_name: str) -> str:
    return "L" + str(class_name).replace(".", "/") + ";"


def dex_entries_containing(apk_path: Path, descriptor: str) -> list[str]:
    """Dex files whose string table names the class, cheapest first.

    A dex that only references the class names it too, so this narrows the
    search without deciding it; the class block itself decides.
    """
    needle = descriptor.encode("utf-8")
    found = []
    with zipfile.ZipFile(apk_path) as archive:
        for name in archive.namelist():
            if _DEX_ENTRY.fullmatch(name) and needle in archive.read(name):
                found.append(name)
    return sorted(found, key=lambda name: int(re.sub(r"\D", "", name) or 1))


def parse_dexdump_class(lines: Iterable[str], descriptor: str,
                        method_name: str) -> tuple[bool, list[dict]]:
    """Whether `dexdump -d` output defines the class, and the method's overloads.

    Stops reading at the end of the class block, so the caller can stop the
    process instead of disassembling the rest of a multi-megabyte dex.
    """
    in_class = False
    found_class = False
    current: dict | None = None
    members: list[dict] = []
    for raw in lines:
        line = raw.rstrip("\n")
        if _CLASS_HEADER.match(line):
            if in_class:
                break
            continue
        header = _CLASS_DESCRIPTOR.match(line)
        if header:
            in_class = header.group("descriptor") == descriptor
            found_class = found_class or in_class
            current = None
            continue
        if not in_class:
            continue
        if _MEMBER_START.match(line):
            current = {"name": "", "type": "", "access": "", "instructions": []}
            members.append(current)
            continue
        if current is None:
            continue
        field = _MEMBER_FIELD.match(line)
        if field and not current[field.group("key")]:
            current[field.group("key")] = field.group("value").strip().strip("'")
            continue
        instruction = _INSTRUCTION.match(line)
        if instruction:
            text = instruction.group("text").strip()
            # The first line of each method repeats its own signature.
            if not text.startswith("["):
                current["instructions"].append(text)
    wanted = re.compile(rf"^{re.escape(method_name)}(?:-[\w$]+)?$")
    methods = []
    for member in members:
        # Fields are listed with the same name/type/access lines; only a
        # method has a parameter list in its type.
        if not member["type"].startswith("(") or not wanted.match(member["name"]):
            continue
        text = "\n".join(member["instructions"])
        methods.append({
            "signature": f"{member['access']} {member['name']}{member['type']}".strip(),
            "disassembly": text[:MAX_METHOD_CHARACTERS],
            "truncated": len(text) > MAX_METHOD_CHARACTERS,
        })
        if len(methods) >= MAX_METHOD_OVERLOADS:
            break
    return found_class, methods


def _lines_until(stream: Iterable[str], deadline: float) -> Iterator[str]:
    for line in stream:
        if time.monotonic() > deadline:
            return
        yield line


_DESCRIPTOR = re.compile(rb"L[\w/$]{1,200};")
_MINIFIED_DESCRIPTOR = re.compile(rb"L(?:[A-Za-z]{1,2}/)+[A-Za-z]{1,2};")
_PLATFORM_DESCRIPTOR = (b"Ljava/", b"Ljavax/", b"Landroid/", b"Ldalvik/", b"Lkotlin/")
MINIFIED_DESCRIPTOR_THRESHOLD = 50


def minification_evidence(apk_path: Path) -> dict:
    """How many class names in the APK look like R8/ProGuard output.

    The published F-Droid APK is not always the binary a detector analysed.
    app.maskan.chat's release build is minified -- its Tink classes are
    com/google/crypto/tink/shaded/protobuf/A, B, C -- so the detector's
    AndroidKeystoreAesGcm occurs nowhere in it, and neither does
    org.jellyfin.mobile's okhttp3.internal.platform.Platform. Saying "not in
    the APK" without this would read as the class being absent from the app.
    """
    names: set[bytes] = set()
    with zipfile.ZipFile(apk_path) as archive:
        for entry in archive.namelist():
            if _DEX_ENTRY.fullmatch(entry):
                names.update(
                    descriptor for descriptor in _DESCRIPTOR.findall(archive.read(entry))
                    if not descriptor.startswith(_PLATFORM_DESCRIPTOR)
                )
    minified = sum(1 for descriptor in names if _MINIFIED_DESCRIPTOR.fullmatch(descriptor))
    return {
        "application_class_descriptors": len(names),
        "minified_looking_descriptors": minified,
        "minified": minified >= MINIFIED_DESCRIPTOR_THRESHOLD,
    }


def disassemble_apk_method(apk_path: Path, class_name: str, method_name: str,
                           dexdump: str) -> dict:
    descriptor = class_descriptor(class_name)
    entries = dex_entries_containing(apk_path, descriptor)
    if not entries:
        minification = minification_evidence(apk_path)
        return {
            "status": "unavailable",
            "reason": (
                "class_not_in_minified_apk" if minification["minified"]
                else "class_not_in_apk"
            ),
            "class_descriptor": descriptor,
            "minification": minification,
            **({"note": (
                "The APK is minified and the reported class name does not "
                "occur in it, so this APK is not the binary the detector "
                "analysed; its bytecode cannot stand in for the reported "
                "method, though its META-INF version files still describe "
                "the build."
            )} if minification["minified"] else {}),
        }
    if not dexdump:
        return {"status": "unavailable", "reason": "dexdump_not_found",
                "class_descriptor": descriptor, "dex_candidates": entries}
    deadline = time.monotonic() + DEXDUMP_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory() as directory, \
            zipfile.ZipFile(apk_path) as archive:
        for entry in entries:
            dex_path = Path(directory) / entry
            dex_path.write_bytes(archive.read(entry))
            process = subprocess.Popen(
                [dexdump, "-d", str(dex_path)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
            )
            try:
                found, methods = parse_dexdump_class(
                    _lines_until(process.stdout, deadline), descriptor, method_name,
                )
            finally:
                process.kill()
                process.wait()
            if found:
                return {
                    "status": "available" if methods else "method_not_found",
                    "dex": entry, "class_descriptor": descriptor,
                    "methods": methods,
                }
            if time.monotonic() > deadline:
                return {"status": "unavailable", "reason": "dexdump_timeout",
                        "class_descriptor": descriptor}
    return {"status": "unavailable", "reason": "class_definition_not_found_in_dex",
            "class_descriptor": descriptor, "dex_candidates": entries}


# Where F-Droid keeps a package's releases: the current ones in repo/, older
# ones in archive/. F-Droid names each <package>_<versionCode>.apk.
FDROID_APK_URLS = ("https://f-droid.org/repo/{apk}", "https://f-droid.org/archive/{apk}")
_FDROID_APK_NAME = re.compile(r"^[A-Za-z][\w]*(?:\.[\w]+)+_\d+\.apk$")
ONLINE_RESOLUTION_ENV = "TCC_ONLINE_DEPENDENCY_RESOLUTION"


def store_apk_urls(apk_name: str, environ: dict | None = None) -> list[str]:
    """F-Droid's release of an APK named <package>_<versionCode>.apk, with online resolution on.

    The consolidated RV import (2026-10-11) carried no APK paths, so every
    finding in a bundled library -- retrowars' ktor, OkHttp in several apps --
    went without the dex that holds the reported method.
    """
    environ = os.environ if environ is None else environ
    name = Path(str(apk_name or "")).name
    if environ.get(ONLINE_RESOLUTION_ENV, "") != "1" or not _FDROID_APK_NAME.match(name):
        return []
    return [template.format(apk=name) for template in FDROID_APK_URLS]


def obtain_apk(apk_name: str, apk_url: str, cache_dir: Path,
               fetch: Callable[[str, Path, str], Path] = fetch_apk,
               ) -> tuple[Path | None, str, str]:
    """(path, URL it came from, failure reason): the given APK, or F-Droid's by name."""
    given = str(apk_url or "").strip()
    urls = [given] if given else store_apk_urls(apk_name)
    if not urls:
        return None, "", "no_apk_url"
    failure = ""
    for url in urls:
        try:
            return fetch(url, Path(cache_dir), apk_name), url, ""
        except (OSError, ValueError) as exc:
            failure = f"apk_download_failed: {exc}"[:300]
    return None, "", failure


def collect_apk_evidence(apk_name: str, apk_url: str, class_name: str,
                         method_name: str, cache_dir: Path,
                         dexdump: str | None = None,
                         fetch: Callable[[str, Path, str], Path] = fetch_apk) -> dict:
    """The reported method and the resolved library versions, from the APK."""
    record = {"source": "analysed_apk", "apk": apk_name, "apk_url": apk_url}
    apk_path, url, failure = obtain_apk(apk_name, apk_url, cache_dir, fetch)
    if apk_path is None:
        return {**record, "status": "unavailable", "reason": failure}
    record["apk_url"] = url
    by_name = url != apk_url
    if by_name:
        record["apk_origin"] = "fdroid_release_by_name"
    try:
        versions = library_versions(apk_path)
        dexdump = find_dexdump() if dexdump is None else dexdump
        result = disassemble_apk_method(apk_path, class_name, method_name, dexdump)
    except (OSError, zipfile.BadZipFile) as exc:
        return {**record, "status": "unavailable",
                "reason": f"apk_unreadable: {exc}"[:300]}
    return {
        **record, **result,
        "library_versions": versions,
        "tool": "dexdump -d",
        "bound": (
            "Dalvik bytecode of the reported method, read from the APK the "
            "finding was reported against. It is the code that shipped, "
            "including whichever library version the build resolved; names "
            "are only as meaningful as the dex keeps them."
            if not by_name else
            "Dalvik bytecode of the reported method, read from F-Droid's release "
            "of the same package and version code. The finding named no APK, so "
            "this is the published build of that version, which may differ from "
            "the one the detector ran on (an instrumented or debug build); names "
            "are only as meaningful as the dex keeps them."
        ),
    }


def collect_method_bytecode_bindings(apk_name: str, apk_url: str,
                                     class_name: str, method_name: str,
                                     cache_dir: Path,
                                     dexdump: str | None = None,
                                     fetch: Callable[[str, Path, str], Path] = fetch_apk,
                                     ) -> dict:
    """What each call in the reported method binds to in the shipped APK.

    Collected for a method the checkout does contain, too: the source says
    what was written, the dex says what it compiled to, and only the dex
    settles a call with no receiver or with generated code as its receiver
    (see bytecode_binding). Every failure is a status, never an exception.
    """
    from cognalys.extraction.bytecode_binding import method_bytecode_bindings

    record = {"source": "analysed_apk", "apk": apk_name}
    # Only the APK the finding names: the checkout holds this method, so a
    # store release fetched by name would add a download per application for
    # what the source already shows.
    if not str(apk_url or "").strip():
        return {**record, "status": "unavailable", "reason": "no_apk_url"}
    apk_path, _, failure = obtain_apk(apk_name, apk_url, cache_dir, fetch)
    if apk_path is None:
        return {**record, "status": "unavailable", "reason": failure}
    try:
        dexdump = find_dexdump() if dexdump is None else dexdump
        disassembled = disassemble_apk_method(apk_path, class_name, method_name, dexdump)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return {**record, "status": "unavailable", "reason": f"apk_unreadable: {exc}"[:300]}
    if disassembled.get("status") != "available":
        return {**record, "status": "unavailable",
                "reason": disassembled.get("reason") or disassembled.get("status", "")}
    return {**record, **method_bytecode_bindings(apk_path, disassembled["methods"])}
