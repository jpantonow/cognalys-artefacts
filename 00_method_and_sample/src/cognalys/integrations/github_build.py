"""Build an Android app straight from its GitHub repository, with no F-Droid recipe.

The F-Droid pool ran dry on 2026-11-03 (every app with a GitHub repository
had been scanned). Android apps on GitHub that F-Droid does not list have no
recipe naming the commit, module and flavour, so they are read from the
checkout instead: the default branch's head, the module that applies the
Android application plugin, its applicationId and versionCode, and one
product flavour per dimension -- the one whose name says it is the free build
(foss, fdroid, oss, libre...) when there is one, since it leaves out the
proprietary services an emulator lacks, else the first declared.

probe(repo_url, work) returns an FdroidBuild for fdroid.build_from_source,
which then builds it exactly as fdroid-build does (apk_build, debug variant,
no minification) and writes apks/<package>.debug_<versionCode>.apk and
sources.csv.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable

from cognalys.integrations.fdroid import FdroidBuild

APPLICATION_PLUGIN = re.compile(
    r"""com\.android\.application|plugins\.android\.application\b|id\s*\(?\s*["']android-application""")
APPLIED_ELSEWHERE = re.compile(r"\bapply\s*\(?\s*false\b|\bclasspath\b")
APPLICATION_ID = re.compile(r"""\bapplicationId\s*(?:=|\s)\s*["']([\w.]+)["']""")
NAMESPACE = re.compile(r"""\bnamespace\s*(?:=|\s)\s*["']([\w.]+)["']""")
VERSION_CODE = re.compile(r"\bversionCode\s*(?:=|\s)\s*(\d+)")
MANIFEST_PACKAGE = re.compile(r"""<manifest\b[^>]*\bpackage\s*=\s*["']([\w.]+)["']""")
# A flavour name saying the build leaves out proprietary services.
FREE_FLAVOUR = re.compile(r"foss|fdroid|f_droid|oss|libre|floss|free|opensource|github", re.IGNORECASE)
# Modules that are not the phone app, when a repository has several.
SECONDARY_MODULE = re.compile(r"wear|tv|auto(motive)?|sample|demo|example|benchmark|test",
                              re.IGNORECASE)
SKIP_DIRS = {"build", ".git", ".gradle", "node_modules", "test", "androidTest"}


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _without_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$|(?<=[\s;{}])//.*$", "", text)


def application_modules(checkout: Path) -> list:
    """Directories (relative to the checkout) whose build file applies the application plugin."""
    modules = []
    for build_file in sorted(checkout.rglob("build.gradle*")):
        relative = build_file.parent.relative_to(checkout)
        if SKIP_DIRS & set(relative.parts) or len(relative.parts) > 3:
            continue
        # The root build file declares the plugin for its modules (`apply
        # false`, a buildscript classpath) without applying it: notedelight's
        # and hawk's root was taken for the app (2026-11-03).
        if any(APPLICATION_PLUGIN.search(line) and not APPLIED_ELSEWHERE.search(line)
               for line in _without_comments(_text(build_file)).splitlines()):
            modules.append(relative)
    return modules


def pick_module(modules: list) -> Path | None:
    """The phone app's module: `app` if present, else the shallowest non-secondary one."""
    if not modules:
        return None
    return min(modules, key=lambda path: (path.name != "app",
                                          bool(SECONDARY_MODULE.search(path.as_posix())),
                                          len(path.parts), path.as_posix()))


def _block(text: str, keyword: str) -> str:
    """The body of the first `keyword { ... }` block, braces balanced; '' when absent."""
    match = re.search(rf"\b{keyword}\s*\{{", text)
    return _balanced(text, match.end()) if match else ""


def _balanced(text: str, start: int) -> str:
    """text[start:] up to the brace closing the one just before ``start``."""
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index]
    return ""


FLAVOUR_ENTRY = re.compile(
    r"""(?:\b(?:create|register|maybeCreate|getByName)\s*\(\s*["'](\w+)["']\s*\)|(?<![\w.])(\w+))\s*\{""")
DIMENSION = re.compile(r"""\bdimension\s*(?:=|\s)\s*["'](\w+)["']""")
DIMENSIONS = re.compile(r"""\bflavorDimensions\s*(?:\+?=|\.addAll\(|\(|\s)\s*(?:listOf\(|\[)?([^\n)\]]*)""")
NOT_FLAVOURS = {"all", "configureEach", "each", "forEach", "if", "else", "when", "buildConfigField",
                "manifestPlaceholders", "resValue", "ndk", "externalNativeBuild", "signingConfig"}


def product_flavours(build_text: str) -> list:
    """[(flavour, dimension or '')] declared in productFlavors, in order."""
    body = _block(_without_comments(build_text), "productFlavors")
    flavours, depth, index = [], 0, 0
    while index < len(body):
        if depth == 0:
            match = FLAVOUR_ENTRY.match(body, index)
            if match and (match.group(1) or match.group(2)) not in NOT_FLAVOURS:
                name = match.group(1) or match.group(2)
                inner = _balanced(body, match.end())
                dimension = DIMENSION.search(inner)
                flavours.append((name, dimension.group(1) if dimension else ""))
        if body[index] == "{":
            depth += 1
        elif body[index] == "}":
            depth -= 1
        index += 1
    return flavours


def flavour_dimensions(build_text: str) -> list:
    match = DIMENSIONS.search(_without_comments(build_text))
    return re.findall(r"""["'](\w+)["']""", match.group(1)) if match else []


def chosen_flavours(build_text: str) -> tuple:
    """One flavour per dimension, in dimension order: the free build's name first, else the first."""
    flavours = product_flavours(build_text)
    if not flavours:
        return ()
    order = flavour_dimensions(build_text)
    by_dimension: dict = {}
    for name, dimension in flavours:
        by_dimension.setdefault(dimension, []).append(name)
    dimensions = [d for d in order if d in by_dimension] + \
        [d for d in by_dimension if d not in order]
    chosen = []
    for dimension in dimensions:
        names = by_dimension[dimension]
        chosen.append(next((name for name in names if FREE_FLAVOUR.search(name)), names[0]))
    return tuple(chosen)


def application_id(checkout: Path, module: Path) -> str:
    text = _without_comments("".join(_text(path) for path in sorted((checkout / module).glob("build.gradle*"))))
    for pattern in (APPLICATION_ID, NAMESPACE):
        match = pattern.search(text)
        if match:
            return match.group(1)
    match = MANIFEST_PACKAGE.search(_text(checkout / module / "src" / "main" / "AndroidManifest.xml"))
    return match.group(1) if match else ""


def version_code(checkout: Path, module: Path) -> int:
    text = _without_comments("".join(_text(path) for path in sorted((checkout / module).glob("build.gradle*"))))
    match = VERSION_CODE.search(text)
    if not match:  # often in gradle.properties or a version catalog
        for path in [checkout / "gradle.properties", *sorted(checkout.glob("gradle/*.toml"))]:
            match = re.search(r"(?i)version_?code\s*[=:]\s*\"?(\d+)", _text(path))
            if match:
                break
    return int(match.group(1)) if match else 1


def gradle_path(checkout: Path, module: Path) -> str:
    """The module's Gradle path from the settings file that includes it (":" for the root itself).

    crisis-connect (2026-11-03) keeps its project in Android/: the module
    Android/app is `:app` there, not `:Android/app`.
    """
    for root in [module, *module.parents]:
        if any((checkout / root / name).is_file() for name in ("settings.gradle", "settings.gradle.kts")):
            parts = module.relative_to(root).parts
            return ":".join(parts) if parts else ":"
    return ":".join(module.parts) if module.parts else ":"


def build_for_checkout(checkout: Path, repo_url: str, commit: str) -> FdroidBuild:
    """The FdroidBuild a recipe would give, read from the checkout."""
    module = pick_module(application_modules(checkout))
    if module is None:
        raise ValueError("no module applies the Android application plugin")
    package = application_id(checkout, module)
    if not package:
        raise ValueError(f"no applicationId or namespace in {module.as_posix() or 'the root'}")
    build_text = "".join(_text(path) for path in sorted((checkout / module).glob("build.gradle*")))
    subdir = "" if module == Path(".") else module.as_posix()
    return FdroidBuild(package=package, version_code=version_code(checkout, module),
                       repo_url=repo_url, commit=commit, subdir=subdir,
                       gradle_path=gradle_path(checkout, module),
                       gradle=chosen_flavours(build_text),
                       submodules=(checkout / ".gitmodules").is_file())


def _default_run(command):
    from cognalys.utils.network_git import run_git_command

    return run_git_command(command, capture_output=True, text=True)


def probe(repo_url: str, work: Path, run: Callable[..., object] | None = None) -> FdroidBuild:
    """Clone the default branch's head and read its build; the clone becomes the build's checkout.

    The clone is moved to where build_from_source checks the commit out
    (work/<package>/projects/<package>), so the repository is fetched once.
    """
    run = run or _default_run
    repo_url = repo_url.rstrip("/").removesuffix(".git")
    slug = re.sub(r"[^\w.-]+", "__", repo_url.removeprefix("https://").removeprefix("github.com/"))
    probe_dir = Path(work) / "_github_probe" / slug
    shutil.rmtree(probe_dir, ignore_errors=True)
    probe_dir.parent.mkdir(parents=True, exist_ok=True)
    result = run(["git", "clone", "-q", repo_url, str(probe_dir)])
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError(f"git clone failed: {(getattr(result, 'stderr', '') or '').strip()[-200:]}")
    head = run(["git", "-C", str(probe_dir), "rev-parse", "HEAD"])
    commit = (getattr(head, "stdout", "") or "").strip()
    if getattr(head, "returncode", 1) != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("the clone has no HEAD commit")
    build = build_for_checkout(probe_dir, repo_url, commit)
    checkout = Path(work) / build.package / "projects" / build.package
    shutil.rmtree(checkout, ignore_errors=True)
    checkout.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(probe_dir), str(checkout))
    return build


# --- google-services.json placeholder -------------------------------------
#
# Crisis-Connect and android-sms-gateway (2026-11-03) failed at
# processDebugGoogleServices: "File google-services.json is missing". A
# repository applying the com.google.gms.google-services plugin keeps that
# file out of git (it names the maintainers' Firebase project), so no clone
# builds as committed. The plugin only needs a client whose package_name is
# the variant's applicationId; with placeholder values Firebase fails at
# runtime on the emulator, which RVSec's exploration survives (the app's
# crypto/TLS code does not depend on it). Only github-build writes it: an
# F-Droid recipe builds as F-Droid defines it. The checkout under work/ is a
# build-only clone; patches and pull requests clone repo_url again.
GOOGLE_SERVICES_ID = "com.google.gms.google-services"
GOOGLE_SERVICES_APPLIED = re.compile(
    r"""(?:\bid\s*\(?\s*|apply\s*\(?\s*plugin\s*(?::|=)\s*)["']com\.google\.gms\.google-services["']""")
PLUGIN_ALIAS = re.compile(r"""\balias\s*\(\s*libs\.plugins\.([\w.]+)\s*\)""")
CATALOG_PLUGIN = re.compile(r"""(?m)^\s*([\w.-]+)\s*=\s*(?:\{[^}\n]*\bid\s*=\s*)?["']([\w.-]+)""")
APPLICATION_ID_SUFFIX = re.compile(r"""\bapplicationIdSuffix\s*(?:=|\s)\s*["']([\w.]*)["']""")
# Marks the file as ours, so a reused checkout (git clean keeps ignored
# files) is not taken for one holding the project's own file.
PLACEHOLDER_PROJECT = "cognalys-build-placeholder"
MISSING_GOOGLE_SERVICES = re.compile(r"google-services\.json is missing|No matching client found for package name")


def _module_text(checkout: Path, module: Path) -> str:
    return _without_comments("".join(_text(path) for path in sorted((checkout / module).glob("build.gradle*"))))


def _catalog_plugins(checkout: Path, module: Path) -> dict:
    """{accessor (dots for - and _): plugin id} from [plugins] of the version catalogs above the module."""
    plugins = {}
    for root in [module, *module.parents]:
        for catalog in sorted((checkout / root / "gradle").glob("*.toml")):
            section = re.search(r"(?ms)^\[plugins\]\s*$(.*?)(?=^\[|\Z)", _text(catalog))
            for alias, plugin_id in CATALOG_PLUGIN.findall(section.group(1) if section else ""):
                plugins.setdefault(re.sub(r"[-_]", ".", alias), plugin_id)
    return plugins


def applies_google_services(checkout: Path, module: Path) -> bool:
    """Whether the module applies the google-services plugin (id, apply plugin, catalog alias)."""
    text = _module_text(checkout, module)
    lines = [line for line in text.splitlines() if not APPLIED_ELSEWHERE.search(line)]
    if any(GOOGLE_SERVICES_APPLIED.search(line) for line in lines):
        return True
    aliases = [alias for line in lines for alias in PLUGIN_ALIAS.findall(line)]
    if not aliases:
        return False
    catalog = _catalog_plugins(checkout, module)
    return any(catalog.get(alias) == GOOGLE_SERVICES_ID for alias in aliases)


def _named_block(body: str, name: str) -> str:
    """The body of `name {`, `create("name") {` or `getByName("name") {` inside ``body``."""
    match = re.search(rf"""(?:\b(?:create|register|maybeCreate|getByName|named)\s*\(\s*["']{name}["']\s*\)"""
                      rf"""|(?<![\w.]){name})\s*\{{""", body)
    return _balanced(body, match.end()) if match else ""


def variant_application_id(checkout: Path, module: Path, flavours: tuple, build_type: str = "debug") -> str:
    """The applicationId of the built variant: the flavours' own id or suffixes, then the build type's suffix."""
    text = _module_text(checkout, module)
    package = application_id(checkout, module)
    suffix = ""
    flavour_body = _block(text, "productFlavors")
    for flavour in flavours:
        inner = _named_block(flavour_body, flavour)
        own = APPLICATION_ID.search(inner)
        if own:
            package = own.group(1)
        found = APPLICATION_ID_SUFFIX.search(inner)
        suffix += found.group(1) if found else ""
    found = APPLICATION_ID_SUFFIX.search(_named_block(_block(text, "buildTypes"), build_type))
    suffix += found.group(1) if found else ""
    if package and suffix and not suffix.startswith("."):
        suffix = "." + suffix
    return package + suffix


def google_services_files(checkout: Path, module: Path, flavours: tuple, build_type: str = "debug") -> list:
    """Where the plugin looks for the variant's file, most specific first, as its lookup order."""
    root = checkout / module
    combined = flavours[0] + "".join(name[:1].upper() + name[1:] for name in flavours[1:]) if flavours else ""
    variant = (combined + build_type[:1].upper() + build_type[1:]) if combined else build_type
    folders = []
    if combined:
        folders += [f"src/{combined}/{build_type}", f"src/{build_type}/{combined}", f"src/{variant}",
                    f"src/{combined}"]
        folders += [f"src/{flavour}/{build_type}" for flavour in flavours]
        folders += [f"src/{flavour}" for flavour in flavours]
    folders += [f"src/{build_type}", "."]
    return [root / folder / "google-services.json" for folder in dict.fromkeys(folders)]


def _is_placeholder(path: Path) -> bool:
    return PLACEHOLDER_PROJECT in _text(path)


def placeholder_google_services(package: str) -> dict:
    """A google-services.json with one client for ``package``; every value says it is a placeholder."""
    return {
        "project_info": {"project_number": "000000000000", "project_id": PLACEHOLDER_PROJECT,
                         "storage_bucket": f"{PLACEHOLDER_PROJECT}.appspot.com"},
        "client": [{
            "client_info": {"mobilesdk_app_id": "1:000000000000:android:0000000000000000",
                            "android_client_info": {"package_name": package}},
            "oauth_client": [],
            "api_key": [{"current_key": "AIzaPlaceholderNotARealKeyCognalysBuild0"}],
            "services": {"appinvite_service": {"other_platform_oauth_client": []}},
        }],
        "configuration_version": "1",
    }


def write_google_services_placeholder(build: FdroidBuild, checkout: Path, failure: str = "") -> str:
    """Write the placeholder in the module root when the build needs one; a note saying so, or "".

    Before the build (``failure`` empty) only for a module applying the
    plugin with no file of its own for the variant; after a failed build
    whose log says the file is missing, whatever the detection said, once.
    """
    import json

    module = Path(build.subdir or ".")
    if not (checkout / module).is_dir():
        return ""
    files = google_services_files(checkout, module, tuple(build.gradle))
    own = [path for path in files if path.is_file() and not _is_placeholder(path)]
    if failure:
        if not MISSING_GOOGLE_SERVICES.search(failure) or own:
            return ""
    elif own or not applies_google_services(checkout, module):
        return ""
    package = variant_application_id(checkout, module, tuple(build.gradle)) or build.package
    target = checkout / module / "google-services.json"
    target.write_text(json.dumps(placeholder_google_services(package), indent=2) + "\n", encoding="utf-8")
    return (f"placeholder google-services.json written for {package} (the repository commits none; "
            "Firebase values are fake)")
