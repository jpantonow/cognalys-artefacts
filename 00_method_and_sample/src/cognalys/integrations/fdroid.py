"""APKs from F-Droid with the exact source they were built from.

A runtime report (RVSec) is about an APK; the repair is made in source. For
the two to meet, each APK needs the repository and the commit it was built
from. F-Droid builds every APK from a recorded commit: its build metadata
(fdroiddata, metadata/<package>.yml) names the repository (`Repo:`) and, per
build, the `versionCode` and the `commit`. The APK F-Droid currently
suggests is downloaded and matched with its build entry; an APK whose commit
cannot be established is reported, not guessed, since reports against the
wrong revision would send the repair to the wrong code.

The result is what `cognalys rvsec-import --sources` and `cognalys
rvsec-run --sources` read: apk, repo_url, commit, manifest_package.

The APK F-Droid publishes went through R8, which renames classes (A.c,
R1.b) no checkout can resolve. build_from_source builds the same commit
instead, with the build entry's own subdir, Gradle flavour, submodules and
recipe steps (source libraries included), as a debug build with
minification off, so RVSec reports name the classes the repository has.
"""
from __future__ import annotations

import csv
import dataclasses
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

FDROID_REPO = "https://f-droid.org/repo"
FDROID_API = "https://f-droid.org/api/v1/packages"
FDROIDDATA = "https://gitlab.com/fdroid/fdroiddata/-/raw/master/metadata"
# packages_logic: which app_packages derived manifest_package (packages_logic());
# empty when nothing derived it (prepare takes F-Droid's package name).
# build_note: what the build changed in the checkout that the repository does
# not hold (github-build's placeholder google-services.json, 2026-11-03), so
# the build is never taken for the project's own configuration.
SOURCE_COLUMNS = ("apk", "repo_url", "commit", "manifest_package", "packages_logic", "build_note")


def _fetch(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "cognalys"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


@dataclass(frozen=True)
class FdroidBuild:
    package: str
    version_code: int
    repo_url: str
    commit: str
    # The build entry's subdir, Gradle flavours and whether it needs submodules.
    subdir: str = ""
    gradle: tuple = ()
    submodules: bool = False
    # The recipe's own preparation: paths it removes, shell steps it runs
    # before building, and Gradle properties it passes (never its sudo steps).
    rm: tuple = ()
    prebuild: tuple = ()
    gradleprops: tuple = ()
    # The build's versionName, for the recipe's $$VERSION$$ placeholder.
    version_name: str = ""
    # The recipe's `build:` commands, which F-Droid runs before its Gradle
    # task, the source libraries they name ($$go$$ for `go@go1.24.2`) and the
    # NDK version $$NDK$$ stands for.
    build_commands: tuple = ()
    srclibs: tuple = ()
    ndk: str = ""
    # The module's Gradle path when it is not the subdir itself: a GitHub
    # app whose settings.gradle sits in Android/ builds Android/app as
    # `app` (github_build); F-Droid recipes leave it empty.
    gradle_path: str = ""

    @property
    def apk(self) -> str:
        return f"{self.package}_{self.version_code}.apk"


def parse_metadata(text: str, version_code: int) -> tuple[str, str]:
    """(repository URL, commit of the build of ``version_code``) from fdroiddata YAML."""
    repo = re.search(r"^Repo:\s*(\S+)", text, re.MULTILINE)
    commit = next((found for code, found in reversed(_build_commits(text))
                   if code == version_code and found), "")
    return (repo.group(1).removesuffix(".git") if repo else ""), commit


def build_entry(text: str, version_code: int) -> dict:
    """subdir, gradle flavours and submodules of the build of ``version_code``.

    PCAPdroid's 2.0.2 builds `gradle: [standard]` from subdir app with
    submodules; building without the flavour produced two APKs, and the
    wrong one was taken. `gradle: [yes]` means the default variant.
    """
    for build in re.split(r"\n  - versionName:", text)[1:]:
        code = re.search(r"\n\s+versionCode:\s*(\d+)", build)
        if not code or int(code.group(1)) != version_code:
            continue
        subdir = re.search(r"\n\s+subdir:\s*(\S+)", build)
        gradle = re.search(r"\n\s+gradle:\s*\n((?:\s+-\s*\S+\n?)+)", build)
        flavours = tuple(name for name in re.findall(r"-\s*(\S+)", gradle.group(1))
                         if name.lower() != "yes") if gradle else ()
        if not flavours:
            flavours = _output_flavours(build)
        submodules = re.search(r"\n\s+submodules:\s*true", build)
        return {"version_name": build.split("\n", 1)[0].strip().strip("'\""),
                "subdir": subdir.group(1) if subdir else "", "gradle": flavours,
                "submodules": bool(submodules), "rm": _recipe_items(build, "rm"),
                "prebuild": _recipe_items(build, "prebuild"),
                "gradleprops": _recipe_items(build, "gradleprops"),
                "build_commands": _recipe_items(build, "build"),
                "srclibs": _recipe_items(build, "srclibs"),
                "ndk": next(iter(_recipe_items(build, "ndk")), "")}
    return {"version_name": "", "subdir": "", "gradle": (), "submodules": False,
            "rm": (), "prebuild": (), "gradleprops": (), "build_commands": (),
            "srclibs": (), "ndk": ""}


def _camel_parts(name: str) -> list:
    return [part.lower() for part in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name)]


def _output_flavours(build: str) -> tuple:
    """The flavours a build entry's `output:` path names, else ().

    Clash Fest (2026-10-28) names its flavour only in its build script
    (assembleAlphaRelease) and in output: build/outputs/apk/alpha/release/...;
    AGP puts the variant's flavours before its build type, whatever the type
    is called (Thunderbird beta's foss/beta). A bundle's universal APK sits in
    one directory named after the whole variant (MyExpenses'
    apk_from_bundle/externRelease): the flavours are its camel-case words
    before the last. A build type named like a variant (moshinda's
    fdroidRelease, without flavours) reads as flavour fdroid; apk_build's
    task fallbacks then reach assembleFdroidRelease.
    """
    output = re.search(r"\n\s+output:\s*\S*?outputs/(?:apk|apk_from_bundle)/(\w+)/(?:(\w+)/)?",
                       build)
    if not output or output.group(1).lower() in ("release", "debug"):
        return ()
    parts = _camel_parts(output.group(1))
    return tuple(parts if output.group(2) else parts[:-1])


def _recipe_items(build: str, key: str) -> tuple:
    """A build key's value as items: an inline value or a list, folded lines joined."""
    match = re.search(rf"\n    {key}:[ \t]*(?P<inline>[^\n]*)(?P<block>(?:\n {{6,}}\S[^\n]*)*)", build)
    if not match:
        return ()
    if match.group("inline").strip():
        lines = [match.group("inline").strip()] + [
            line.strip() for line in match.group("block").splitlines() if line.strip()]
        return (" ".join(lines),)
    items: list = []
    for line in match.group("block").splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and len(line) - len(line.lstrip()) == 6:
            items.append(line.strip()[2:].strip())
        elif items:
            items[-1] += " " + line.strip()
    return tuple(item.strip("'\"") if item[:1] in "'\"" and item[-1:] == item[:1] else item
                 for item in items)


def suggested_build(package: str, fetch: Callable[[str], bytes] = _fetch,
                    newest_recorded: bool = False) -> FdroidBuild:
    """The build F-Droid suggests, with its recipe.

    ``newest_recorded`` (build_from_source only): when the suggested
    versionCode has no build entry with a commit, the newest entry with one
    and a versionCode not above it. shoppinglist (2026-10-26) suggested
    versionCode 10 while fdroiddata listed builds 4, 8, 9 and 11. An APK
    built from source only needs a commit F-Droid recorded; prepare()
    downloads F-Droid's APK, which may be gone for an older versionCode.
    """
    info = json.loads(fetch(f"{FDROID_API}/{package}"))
    version_code = int(info["suggestedVersionCode"])
    text = fetch(f"{FDROIDDATA}/{package}.yml").decode()
    repo_url, commit = parse_metadata(text, version_code)
    if not commit and newest_recorded:
        recorded = [code for code, found in _build_commits(text) if found and code <= version_code]
        if recorded:
            print(f"{package}: F-Droid suggests versionCode {version_code}, which has no build "
                  f"entry with a commit; building versionCode {max(recorded)}", flush=True)
            version_code = max(recorded)
            repo_url, commit = parse_metadata(text, version_code)
    return FdroidBuild(package, version_code, repo_url, commit, **build_entry(text, version_code))


def _build_commits(text: str) -> list:
    """(versionCode, commit) of every build entry, in file order."""
    entries = []
    for build in re.split(r"\n  - versionName:", text)[1:]:
        code = re.search(r"\n\s+versionCode:\s*(\d+)", build)
        found = re.search(r"\n\s+commit:\s*['\"]?([^\s'\"]+)", build)
        if code:
            entries.append((int(code.group(1)), found.group(1) if found else ""))
    return entries


def is_commit(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{7,40}", value or ""))


def prepare(packages: Iterable[str], out: Path,
            fetch: Callable[[str], bytes] = _fetch) -> tuple[list[FdroidBuild], list[dict]]:
    """Download each package's suggested APK into out/apks; write out/sources.csv.

    Returns (builds taken, problems). A build whose commit F-Droid does not
    record as a hash (a tag, nothing) is a problem, not a source.
    """
    out = Path(out)
    (out / "apks").mkdir(parents=True, exist_ok=True)
    taken, problems = [], []
    for package in packages:
        try:
            build = suggested_build(package, fetch)
        except Exception as exc:  # noqa: BLE001 -- recorded per package
            problems.append({"package": package, "problem": f"F-Droid metadata unavailable: {exc}"})
            continue
        if not build.repo_url:
            problems.append({"package": package, "problem": "no Repo: in F-Droid metadata"})
            continue
        if not is_commit(build.commit):
            problems.append({"package": package,
                             "problem": f"build {build.version_code} has no commit hash "
                                        f"({build.commit or 'none'})"})
            continue
        target = out / "apks" / build.apk
        if not target.exists():
            target.write_bytes(fetch(f"{FDROID_REPO}/{build.apk}"))
        taken.append(build)
    with open(out / "sources.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_COLUMNS)
        writer.writeheader()
        for build in taken:
            writer.writerow({"apk": build.apk, "repo_url": build.repo_url,
                             "commit": build.commit, "manifest_package": build.package})
    return taken, problems


# ---------------------------------------------------------------------------
# Building the same commit without minification.
# ---------------------------------------------------------------------------

_NAMESPACE = re.compile(r"""\b(?:namespace|applicationId)\s*=?\s*["']([\w.]+)["']""")
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)


def app_packages(checkout: Path, subdir: str = "", share: float = 0.05) -> tuple:
    """The Java/Kotlin packages of the app's own code, read from its checkout.

    F-Droid's application id is not where the code lives: xivpn ships as
    io.github.exclude0122.xivpn with its code under cn.gov.xivpn2, Session as
    network.loki.messenger.fdroid under org.thoughtcrime.securesms and
    org.session. With the id alone, RVSec's reports in both were called
    library code. Every module of the repository is the app's own code
    (fread's HTTP client is in its framework module, com.zhangke.framework),
    so the whole checkout is read: the app module's namespace first, then the
    namespace every other module's build file declares, then every
    three-segment package root holding at least ``share`` of the repository's
    source files.

    The other modules' namespaces matter because a small module can hold the
    crypto: Olvid's engine (io.olvid.engine, beside io.olvid.messenger) and
    Cryptomator's util module (org.cryptomator.util) were both called library
    code on 2026-10-24, and their eight findings never reached the analysis.
    """
    import collections

    module = Path(checkout) / subdir if subdir else Path(checkout)
    packages: list = []
    gradle_files = sorted(module.glob("build.gradle*")) + sorted(
        path for path in Path(checkout).rglob("build.gradle*")
        if "/build/" not in path.as_posix() and path.parent != module)
    for gradle in gradle_files:
        for name in _NAMESPACE.findall(gradle.read_text(encoding="utf-8", errors="replace")):
            if name not in packages:
                packages.append(name)
    roots: collections.Counter = collections.Counter()
    files = 0
    for path in Path(checkout).rglob("*"):
        if path.suffix not in (".java", ".kt") or "/build/" in path.as_posix():
            continue
        if re.search(r"/src/(?:test|androidTest)\w*/", path.as_posix()):
            continue
        match = _PACKAGE.search(path.read_text(encoding="utf-8", errors="replace")[:4000])
        if match:
            files += 1
            roots[".".join(match.group(1).split(".")[:3])] += 1
    for root, count in roots.most_common():
        if files and count / files >= share and not any(
                root == known or root.startswith(known + ".") for known in packages):
            packages.append(root)
    return tuple(packages)


def packages_logic() -> str:
    """A fingerprint of app_packages' own code, written beside what it derived.

    It changes whenever app_packages changes, with nobody to remember a
    version number: Tasks' sources.csv of 2026-10-25 predates the reading of
    every module's namespace and lacks at.bitfire.cert4android, whose
    CustomCertStore finding was then taken for library code.
    """
    import hashlib
    import inspect

    code = inspect.getsource(app_packages) + _NAMESPACE.pattern + _PACKAGE.pattern
    return hashlib.sha1(code.encode("utf-8")).hexdigest()[:12]


def declared_packages(checkout: Path) -> set:
    """Every package a Java/Kotlin source file of the checkout declares, outside build output."""
    declared = set()
    for path in Path(checkout).rglob("*"):
        if path.suffix not in (".java", ".kt") or "/build/" in path.as_posix():
            continue
        try:
            match = _PACKAGE.search(path.read_text(encoding="utf-8", errors="replace")[:4000])
        except OSError:
            continue
        if match:
            declared.add(match.group(1))
    return declared


def refresh_app_packages(out: Path, checkout_for: Callable[[dict], Path | None],
                         findings: Iterable[Path] = ()) -> list[dict]:
    """Derive again the app's packages of each sources.csv row an older app_packages wrote.

    The row's checkout (``checkout_for(row)``, at the row's commit) gives
    app_packages' current answer, joined to the packages the row had. A
    finding of ``findings`` labelled library becomes app only when its class
    falls under a new package and its own package is declared by a source
    file of that checkout: code of a library published from another
    repository (Olvid's engine; SimpMusic's pipepipe) is in no file of the
    checkout and stays library. Nothing is relabelled from app to library.
    Returns one record per row derived again.
    """
    from cognalys.integrations.rvsec import code_origin

    sources = Path(out) / "sources.csv"
    with open(sources, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows, columns = list(reader), list(reader.fieldnames or SOURCE_COLUMNS)
    current = packages_logic()
    findings = [Path(path) for path in findings if Path(path).is_file()]
    # Only an APK with a finding labelled library can gain one: the others
    # are not fetched again.
    library_apks = {record.get("apk", "") for path in findings
                    for record in map(json.loads, filter(str.strip, path.read_text(
                        encoding="utf-8").splitlines()))
                    if record.get("origin") == "library"} if findings else None
    changes, widened = [], {}
    for row in rows:
        if row.get("packages_logic") == current or (
                library_apks is not None and row.get("apk", "") not in library_apks):
            continue
        checkout = checkout_for(row)
        if checkout is None:
            changes.append({"apk": row.get("apk", ""), "problem": "no checkout of its commit"})
            continue
        before = tuple(package for package in re.split(r"[;\s]+", row.get("manifest_package") or "")
                       if package)
        derived = app_packages(checkout)
        after = tuple(dict.fromkeys(derived + before))
        row["manifest_package"], row["packages_logic"] = ";".join(after), current
        added = tuple(package for package in after if package not in before)
        widened[row.get("apk", "")] = (added, declared_packages(checkout) if added else set())
        changes.append({"apk": row.get("apk", ""), "before": before, "after": after, "relabelled": 0})
    if not widened:
        return changes
    for column in SOURCE_COLUMNS:
        if column not in columns:
            columns.append(column)
    with open(sources, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    relabelled: dict = {}
    for path in findings:
        lines, changed = [], False
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            added, declared = widened.get(record.get("apk", ""), ((), set()))
            class_name = str(record.get("class") or "")
            if (record.get("origin") == "library" and code_origin(class_name, added) == "app"
                    and class_name.rpartition(".")[0] in declared):
                record["origin"], changed = "app", True
                relabelled[record["apk"]] = relabelled.get(record["apk"], 0) + 1
                line = json.dumps(record, ensure_ascii=False)
            lines.append(line)
        if changed:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for change in changes:
        change["relabelled"] = relabelled.get(change["apk"], 0) if "after" in change else 0
    return changes


def _android_sdk() -> str:
    import os

    return os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or str(
        Path.home() / "Android" / "Sdk")


def ndk_home(sdk: str, version: str) -> str:
    """The SDK's NDK folder for a recipe's `ndk:` (r25c or 25.2.9519653), else "".

    An r-name's letter is the minor release (r25c is 25.2.x); the folder's
    source.properties says which release it holds.
    """
    version = str(version or "").strip()
    named = re.fullmatch(r"r(\d+)([a-z]?)", version)
    if named:
        prefix = f"{named.group(1)}.{ord(named.group(2)) - ord('a') if named.group(2) else 0}."
    elif re.fullmatch(r"\d+(?:\.\d+)*", version):
        prefix = version + "."
    else:
        return ""
    for folder in sorted((Path(sdk) / "ndk").glob("*"), reverse=True):
        try:
            text = (folder / "source.properties").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        revision = re.search(r"Pkg\.Revision\s*=\s*(\S+)", text)
        if revision and (revision.group(1) + ".").startswith(prefix):
            return str(folder)
    return ""


def installed_goroot() -> str:
    """The installed Go's GOROOT, or "" when Go is not installed."""
    import shutil
    import subprocess

    go = shutil.which("go", path=_recipe_path(""))
    if not go:
        return ""
    try:
        completed = subprocess.run([go, "env", "GOROOT"], capture_output=True, text=True,
                                   timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _recipe_path_dirs(sdk: str) -> list:
    """Tool folders a recipe expects on PATH that a non-login shell lacks.

    PCAPdroid's prebuild runs `sdkmanager`, which lives in the SDK's
    cmdline-tools; native recipes call the SDK's cmake and ninja; rustup and
    Go put their bin folders on PATH only for login shells (vayunmathur's
    passwords, 2026-10-25; restoid's `go: command not found`, 2026-10-27).
    """
    import os

    home = Path.home()
    folders = [Path(sdk) / "cmdline-tools" / "latest" / "bin"]
    cmake = sorted((Path(sdk) / "cmake").glob("*/bin"),
                   key=lambda path: [int(part) for part in re.findall(r"\d+", path.parent.name)])
    folders += cmake[-1:]
    folders += [Path(os.environ.get("CARGO_HOME") or home / ".cargo") / "bin",
                Path("/usr/local/go/bin"), Path(os.environ.get("GOPATH") or home / "go") / "bin"]
    return [str(folder) for folder in folders if folder.is_dir()]


def _recipe_path(sdk: str) -> str:
    import os

    return os.pathsep.join(_recipe_path_dirs(sdk or _android_sdk()) + [os.environ.get("PATH", "")])


# Tools a recipe's own commands call that a build here may lack; a package
# installed by the recipe's `sudo:` but never called is not one of them.
_RECIPE_TOOLS = ("flutter", "dart", "protoc", "swig", "autoreconf", "automake", "libtool",
                 "nasm", "yasm", "meson", "ninja", "cmake", "cargo", "rustup", "go", "gomobile",
                 "npm", "npx", "yarn", "node", "mvn", "ant")


def _command_words(command: str) -> list:
    """The first word of every simple command in a shell line."""
    words = []
    for part in re.split(r"&&|\|\||[;|&(\n`]|\$\(", command):
        tokens = part.split()
        while tokens and (re.fullmatch(r"\w+=\S*", tokens[0])
                          or tokens[0] in ("then", "do", "else", "exec", "time", "env", "!", "{",
                                           "sudo", "-E")):
            tokens.pop(0)
        if tokens:
            # The closing of a subshell or group: `(cd x && ninja)`.
            words.append(tokens[0].rstrip(")}"))
    return words


def recipe_missing_tools(build: FdroidBuild, which: Callable[[str], object] | None = None) -> tuple:
    """Tools the recipe's prebuild or build commands call that are not installed.

    Only a bare command counts: one named by a path (.flutter/bin/flutter,
    $$go$$/bin/go) comes with the repository or a source library.
    new_selection.py ranks such an app lower; it is not skipped, since a tool
    that only a release step needs can be missing from a debug build that
    works.
    """
    import shutil

    if which is None:
        path = _recipe_path("")
        which = lambda tool: shutil.which(tool, path=path)  # noqa: E731
    called = {word for command in (*build.prebuild, *build.build_commands)
              for word in _command_words(command)}
    return tuple(tool for tool in _RECIPE_TOOLS if tool in called and not which(tool))


_GO_BOOTSTRAP = re.compile(r"/usr/lib/go(?:-[\d.]+)?(?=[/\s'\";:]|$)")
_ERREXIT = re.compile(r"\bset\s+-(?P<flags>[a-zA-Z]+)")


def _recipe_command(step: str, placeholders: dict, goroot: str) -> str:
    """A recipe command ready for this machine, or "" when it cannot run here.

    Debian's Go is /usr/lib/go[-1.27] (restoid's and resticdroid's
    GOROOT_BOOTSTRAP, megaproxy487's path); here it is wherever `go env
    GOROOT` says. A `set -e` or `set -u` would end the shared shell at the
    first failing or unset step (resticdroid's build starts with `set -e`),
    while every step here is non-fatal. sudo is never run.
    """
    command = step
    for name, value in placeholders.items():
        if value:
            command = command.replace(name, value)
    if "$$" in command:
        return ""  # a placeholder this machine has nothing for
    if goroot:
        command = _GO_BOOTSTRAP.sub(goroot, command)

    def without_exit_flags(match):
        flags = match.group("flags").replace("e", "").replace("u", "")
        return f"set -{flags}" if flags else "true"

    command = _ERREXIT.sub(without_exit_flags, command)
    command = re.sub(r"\bset\s+-o\s+(?:errexit|nounset)\b", "true", command)
    return re.sub(r"(^|&&|\|\||[;|(])\s*sudo\b[^;&|\n]*",
                  r"\1 echo 'sudo step skipped' >&2", command)


def recipe_steps(build: FdroidBuild, checkout: Path, srclibs: dict | None = None) -> list:
    """The recipe's rm, prebuild and build steps as commands, in F-Droid's order.

    fread's build.gradle.kts reads a keystore.properties that only its
    maintainer has; its recipe deletes those lines with sed before building.
    Without the recipe the build fails where F-Droid's succeeds. F-Droid then
    runs the `build:` commands before its Gradle task, and for half of the
    apps that failed to build here (2026-10-28) that is where the library the
    app links is made: DumDum's Go libcore.aar, Sagernet's gomobile binding,
    Megaproxy's megaproxy.aar. ``srclibs`` maps each source library's name to
    its checkout (prepare_srclibs) for $$name$$. A step naming a placeholder
    this machine has nothing for is dropped, and sudo is never run: such a
    recipe is left to fail visibly in the build log.
    """
    import shlex

    sdk = _android_sdk()
    workdir = checkout / build.subdir if build.subdir else checkout
    steps = [["rm", "-rf", "--", str(checkout / path)] for path in build.rm
             if path and ".." not in Path(path).parts]
    ndk = ndk_home(sdk, build.ndk)
    placeholders = {"$$SDK$$": sdk, "$$NDK$$": ndk, "$$VERSION$$": build.version_name,
                    "$$VERCODE$$": str(build.version_code),
                    **{f"$${name}$$": str(path) for name, path in (srclibs or {}).items()}}
    goroot = installed_goroot() if any(
        _GO_BOOTSTRAP.search(step) for step in (*build.prebuild, *build.build_commands)) else ""
    tool_dirs = _recipe_path_dirs(sdk)
    environment = [f"export ANDROID_HOME={shlex.quote(sdk)} ANDROID_SDK_ROOT={shlex.quote(sdk)}"]
    if tool_dirs:
        environment.append(f"export PATH={shlex.quote(':'.join(tool_dirs))}:\"$PATH\"")
    if ndk:
        environment.append(f"export ANDROID_NDK_HOME={shlex.quote(ndk)} "
                           f"ANDROID_NDK={shlex.quote(ndk)}")
    # F-Droid runs the prebuild list as one shell script, so a `cd` carries
    # into the steps after it: zSMTH's recipe does `cd src/main/java/com/zfdang`
    # and then seds the Umeng analytics calls out of files there. Run apart,
    # each sed missed its file and the build failed on the SDK the recipe
    # removes (2026-10-17). Each step stays non-fatal -- a step needing a tool
    # this machine lacks (PCAPdroid's `sdkmanager 'cmake;3.22.1'`) must not
    # fail a build Gradle can finish -- and runs in the same shell ({ ...; },
    # not a subshell) so its directory changes persist.
    def script(kind: str, commands: Iterable[str], preamble: list) -> list:
        lines = []
        for step in commands:
            command = _recipe_command(step, placeholders, goroot)
            if command:
                lines.append(f"{{ {command}; }} || echo "
                             f"{shlex.quote(kind + ' step failed: ' + command)} >&2")
        if not lines:
            return []
        return [["bash", "-c", "\n".join(
            [f"cd {shlex.quote(str(workdir))} || exit 1", *environment, *preamble, *lines])]]

    steps += script("prebuild", build.prebuild, [])
    # A `gradle` in the app's own folders is F-Droid's release assemble, which
    # the debug task after these steps replaces; in a source library it
    # publishes what the app depends on (passwdsafe's
    # `cd $$yubikit-android$$; gradle build; gradle publishToMavenLocal`).
    # This machine has no system gradle, so there the library's wrapper runs.
    own = [workdir, *(parent for parent in workdir.parents
                      if parent == checkout or checkout in parent.parents)]
    gradle = ("gradle() {\n  case \"$PWD\" in\n    "
              + "|".join(shlex.quote(str(folder)) for folder in own)
              + ") echo \"F-Droid's own gradle step skipped: gradle $*\" >&2; return 0 ;;\n"
              "  esac\n  if [ -f ./gradlew ]; then bash ./gradlew \"$@\"; "
              "else command gradle \"$@\"; fi\n}")
    commands = [re.sub(r"(?<![\w/.-])(?:(?:bash|sh)\s+)?(?:\.{1,2}/)*gradlew(?![\w.-])",
                       "gradle", step) for step in build.build_commands]
    steps += script("build", commands, [gradle])
    return steps


FDROIDDATA_SRCLIBS = "https://gitlab.com/fdroid/fdroiddata/-/raw/master/srclibs"


def prepare_srclibs(build: FdroidBuild, cache: Path, fetch: Callable[[str], bytes],
                    run_step: Callable[[list], object]) -> tuple[dict, list]:
    """Check out each source library the recipe names; (name -> folder, problems).

    Each `name@ref` is cloned once under ``cache`` and reused by later apps;
    a reused one is reset to its ref, keeping what earlier builds left in
    ignored folders (Go's bin/ and pkg/). A library that cannot be had is
    reported and its $$name$$ steps are dropped; the build goes on.
    """
    folders, problems = {}, []
    for spec in build.srclibs:
        name, _, ref = str(spec).partition("@")
        try:
            metadata = fetch(f"{FDROIDDATA_SRCLIBS}/{name}.yml").decode()
        except Exception as exc:  # noqa: BLE001 -- recorded per library
            problems.append(f"srclib {name}: metadata unavailable ({exc})")
            continue
        repo = re.search(r"^Repo:\s*(\S+)", metadata, re.MULTILINE)
        subdir = re.search(r"^Subdir:\s*(\S+)", metadata, re.MULTILINE)
        if not repo:
            problems.append(f"srclib {name}: no Repo: in its metadata")
            continue
        target = Path(cache) / re.sub(r"[^\w.@-]", "_", f"{name}@{ref}" if ref else name)
        steps = []
        if not (target / ".git").is_dir():
            # A tag or branch is cloned alone (Go's source at go1.24.2); a
            # commit needs the history.
            shallow = ["--depth", "1", "--branch", ref] if ref and not is_commit(ref) else []
            steps.append(["git", "clone", "-q", *shallow, repo.group(1), str(target)])
        if ref:
            steps.append(["git", "-C", str(target), "checkout", "-q", "-f", ref])
        steps.append(["git", "-C", str(target), "clean", "-fdq"])
        for step in steps:
            error = run_step(step)
            if error is not None:
                problems.append(f"srclib {name}: {' '.join(step[:4])} failed "
                                f"({error[0] or 'git gave no message'})")
                break
        else:
            folders[name] = target / subdir.group(1) if subdir else target
    return folders, problems


def gradle_task(build: FdroidBuild) -> str:
    """The debug assemble task of the build entry's flavour, in its subdir.

    fdroiddata lists one flavour per dimension, in dimension order, so
    geoshare's `gradle: [free, active]` is the variant FreeActive; the
    first alone (assembleFreeDebug) was ambiguous to Gradle (2026-10-21).
    """
    flavour = "".join(name[:1].upper() + name[1:] for name in build.gradle)
    task = f"assemble{flavour}Debug"
    module = build.gradle_path if build.gradle_path or not build.subdir else build.subdir
    if build.gradle_path == ":":
        module = ""
    return f":{module}:{task}" if module else task


# What the x86_64 emulator runs, best first; it translates arm64 from API 30.
_ABI_PREFERENCE = ("universal", "x86_64", "x86", "arm64-v8a", "armeabi-v7a")


def one_per_abi(apks: list[Path]) -> list[Path]:
    """Several ABI splits of one build reduced to the one the emulator runs.

    LavenderPhotos (2026-10-22) splits per ABI: app-arm64-v8a-debug.apk and
    app-armeabi-v7a-debug.apk. Two APKs read as "cannot tell which flavour",
    and the build that had succeeded produced nothing.
    """
    def abi(apk: Path) -> str:
        name = apk.name.lower()
        return next((tag for tag in _ABI_PREFERENCE if f"-{tag}-" in name or f"_{tag}_" in name
                     or name.startswith(f"{tag}-") or f"-{tag}." in name), "")

    if len(apks) < 2 or not all(abi(apk) for apk in apks):
        return apks
    stems = {apk.name.lower().replace(abi(apk), "") for apk in apks}
    if len(stems) != 1:
        return apks
    return [min(apks, key=lambda apk: _ABI_PREFERENCE.index(abi(apk)))]


def built_apk(apks: Iterable[Path], build: FdroidBuild) -> Path | None:
    """The APK of the build entry's flavour among what Gradle produced."""
    apks = sorted(Path(apk) for apk in apks)
    # The task's module can build another module's APK too: Mixify's
    # :app:assembleDevDebug (2026-11-05) also produced MixifyWear-dev-debug.apk.
    # Gradle names a module's APKs after it, so those are the task's own.
    module = re.split(r"[:/]", gradle_task(build).rpartition(":")[0])[-1].lower()
    own = [apk for apk in apks if module and re.split(r"[-_.]", apk.name.lower())[0] == module]
    if own:
        apks = own
    apks = one_per_abi(apks)
    if build.gradle:
        flavours = {name.lower() for name in build.gradle}
        # Each flavour is one of the name's parts, whatever separates them:
        # app-standard-debug.apk, AmaRadio's AMARadio_1.37_foss.apk, or
        # app-free-active-debug.apk for one flavour per dimension.
        matching = [apk for apk in apks
                    if flavours <= set(re.split(r"[-_.]", apk.name.lower()))]
        if len(matching) == 1:
            return matching[0]
        # The task is the flavour's own (assembleFdroidDebug), so a single
        # APK is that flavour's whatever its name: Read You (2026-10-18)
        # names it ReadYou-0.16.2-091c9207.apk. The name only decides
        # between several.
        return apks[0] if not matching and len(apks) == 1 else None
    return apks[0] if len(apks) == 1 else None


def resolve_tag(repo_url: str, tag: str, run: Callable[..., object]) -> str:
    """The commit `tag` names in `repo_url`, or '' when it names none."""
    result = run(["git", "ls-remote", repo_url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"])
    if getattr(result, "returncode", 1) != 0:
        return ""
    refs = {}
    for line in str(getattr(result, "stdout", "") or "").splitlines():
        parts = line.split()
        if len(parts) == 2:
            refs[parts[1]] = parts[0]
    # An annotated tag's own object is not a commit; its peeled ref (^{}) is.
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}", "")


# Gradle's own account of a failure, up to its "Try:" advice.
_WHAT_WENT_WRONG = re.compile(r"^\* What went wrong:\s*\n(.*?)(?=^\* (?:Try|Exception is|Get more help):"
                              r"|^BUILD FAILED|\Z)", re.MULTILINE | re.DOTALL)
BUILD_LOG_TAIL_LINES = 60


def keep_build_failure(built: Path, step: list, target: Path) -> str:
    """Write why a build step failed to ``target``; its first line, or "".

    40 of the 47 failed builds of 2026-10-22..31 recorded only "apk_build
    failed (logs in ~/rvsec_builds/...)", and the disk cleanups then removed
    those logs: whether a build failure could be predicted (new_selection's
    build_risk) could no longer be checked. Kept: apk_build's reason from
    build_report.json, Gradle's "What went wrong" blocks and the last
    BUILD_LOG_TAIL_LINES lines of the newest project log.
    """
    import json

    sections, headline = [f"failed step: {' '.join(map(str, step))}"], ""
    try:
        report = json.loads((Path(built) / "build_report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = []
    for entry in report if isinstance(report, list) else []:
        if isinstance(entry, dict) and entry.get("status") != "success" and entry.get("reason"):
            sections.append(f"apk_build ({entry.get('status')}): {entry['reason']}")
            headline = headline or str(entry["reason"]).splitlines()[0]
    logs = sorted((Path(built) / "logs" / "projects").glob("*.log"),
                  key=lambda path: path.stat().st_mtime)
    if logs:
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
        blocks = [block.strip() for block in _WHAT_WENT_WRONG.findall(text) if block.strip()]
        if blocks:
            headline = " ".join(blocks[0].split())[:300]
            sections.append("Gradle, what went wrong:\n" + "\n\n".join(blocks))
        sections.append(f"last {BUILD_LOG_TAIL_LINES} lines of {logs[-1]}:\n"
                        + "\n".join(text.splitlines()[-BUILD_LOG_TAIL_LINES:]))
    try:
        Path(target).write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    except OSError:
        pass
    return headline


def build_from_source(packages: Iterable[str], out: Path, work: Path,
                      fetch: Callable[[str], bytes] = _fetch,
                      run: Callable[..., object] | None = None,
                      timeout: int = 3600,
                      srclib_cache: Path | None = None,
                      resolve: Callable[[str], FdroidBuild] | None = None,
                      prepare: Callable[[FdroidBuild, Path, str], str] | None = None,
                      ) -> tuple[list[FdroidBuild], list[dict]]:
    """Build each package's suggested version from its commit into out/apks.

    ``resolve`` turns each item of ``packages`` into its build; by default
    F-Droid's recipe (suggested_build). github_build.probe reads a GitHub
    repository with no recipe instead (items are repository URLs then).

    ``prepare(build, checkout, failure)`` may complete the checkout before the
    Gradle build (failure "") and once more after a failed build (failure:
    the build's logs); a non-empty note means it changed something, is kept
    in sources.csv's build_note and, after a failure, the build is retried
    once. github-build passes the google-services.json placeholder.

    Writes out/sources.csv as prepare does, naming each APK
    <package>.debug_<versionCode>.apk. Checkouts and Gradle output go under
    ``work`` (keep it on a Linux filesystem). ``timeout`` is Gradle's, per
    package: fread, a Kotlin Multiplatform app, ran past apk_build's 30
    minutes while an instrumentation shared the CPU. Source libraries go
    under ``srclib_cache`` ($COGNALYS_SRCLIB_CACHE, else work/srclibs), so a
    queue can share them across apps. Returns (built, problems).
    """
    import os
    import shutil
    import subprocess
    import sys

    from cognalys.builds import apk_build
    from cognalys.builds.apk_build import r8_optimized_apk

    def default_run(command):
        # git's stderr is kept for the problem message; Gradle's output
        # streams to the console as before.
        if command[0] == "git":
            # clone and ls-remote are bounded (utils.network_git); a final
            # timeout is a failed step (code 124), not a hung queue.
            from cognalys.utils.network_git import run_git_command

            return run_git_command(command, capture_output=True, text=True)
        # A recipe's `sdkmanager` asks to accept a license it has not seen;
        # with no input it declines at once instead of waiting.
        return subprocess.run(command, stdin=subprocess.DEVNULL)

    run = run or default_run
    out, work = Path(out), Path(work)

    def run_step(step):
        # A clone is retried: GitHub drops one now and then mid-batch
        # (rssreader, 2026-10-16), and a retry costs nothing next to a build.
        attempts = 3 if step[:2] == ["git", "clone"] else 1
        for attempt in range(attempts):
            if attempt and step[:2] == ["git", "clone"]:
                shutil.rmtree(step[-1], ignore_errors=True)
            result = run(step)
            if getattr(result, "returncode", 1) == 0:
                return None
        return (getattr(result, "stderr", "") or "").strip().splitlines()[-1:] or [""]

    def checkout_commit(build, checkout):
        """(failed step, its error lines), or (None, None) once the commit is checked out."""
        if (checkout / ".git").is_dir():
            # A checkout a killed run left behind (androidipcamera, after the
            # two-lane crash of 2026-10-26) had files the plain checkout
            # refused to overwrite ("Aborting"), and an earlier prebuild's
            # edits would be applied twice. It is a scratch copy: reset it by
            # force, keeping ignored files (Gradle caches), and clone again
            # once if even that fails.
            for step in (["git", "-C", str(checkout), "checkout", "-q", "-f", build.commit],
                         ["git", "-C", str(checkout), "clean", "-fdq"]):
                if run_step(step) is not None:
                    break
            else:
                return None, None
            print(f"{build.package}: the reused checkout could not be reset; cloning again",
                  flush=True)
            shutil.rmtree(checkout, ignore_errors=True)
        for step in (["git", "clone", "-q", build.repo_url, str(checkout)],
                     ["git", "-C", str(checkout), "checkout", "-q", build.commit]):
            error = run_step(step)
            if error is not None:
                return step, error
        return None, None

    def update_submodules(checkout, log_path):
        """None, or the error lines of the submodule update.

        Here, before the recipe's prebuild, which can need a submodule's
        files (xrayfa's writes to ../AndroidLibXrayLite/assets), and from
        the repository root, where .gitmodules is. It goes through apk_build's
        own commands: xrayfa's git@github.com: urls (2026-10-27) need the
        https rewrite, and a private submodule must not wait for a password.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            _urls, env = apk_build.rewrite_dead_submodule_protocols(checkout, log)
            try:
                failure = apk_build.run_submodule_commands(checkout, log, timeout, env, force=True)
            except OSError as exc:
                failure = str(exc)
        if failure is None:
            return None
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            lines = []
        return [f"{failure}: {lines[-1]}" if lines else failure]

    srclib_cache = Path(srclib_cache or os.environ.get("COGNALYS_SRCLIB_CACHE") or work / "srclibs")
    (out / "apks").mkdir(parents=True, exist_ok=True)
    built, problems = [], []
    own_packages: dict = {}
    build_notes: dict = {}
    for package in packages:
        try:
            build = (resolve(package) if resolve
                     else suggested_build(package, fetch, newest_recorded=True))
        except Exception as exc:  # noqa: BLE001 -- recorded per package
            source = "build source" if resolve else "F-Droid metadata"
            problems.append({"package": package, "problem": f"{source} unavailable: {exc}"})
            continue
        package = build.package
        commit = build.commit
        if build.repo_url and commit and not is_commit(commit):
            # F-Droid's commit field may name a tag (WiFiAnalyzer's
            # V3.3.1-F-DROID); the tag's commit is the one F-Droid built.
            commit = resolve_tag(build.repo_url, commit, run)
            if commit:
                build = dataclasses.replace(build, commit=commit)
        if not build.repo_url or not is_commit(build.commit):
            problems.append({"package": package, "problem": f"build {build.version_code} has no "
                             f"repository or commit hash ({build.commit or 'none'})"})
            continue
        projects = work / package / "projects"
        checkout = projects / package
        # The APK picked below is the one this build produced: copies an
        # earlier build of the package left behind would be more candidates.
        shutil.rmtree(work / package / "built" / "apks", ignore_errors=True)
        failed, error = checkout_commit(build, checkout)
        if not failed and build.submodules:
            error = update_submodules(checkout, work / package / "submodules.log")
            if error is not None:
                failed = ["git", "-C", str(checkout), "submodule"]
        if failed:
            problems.append({"package": package, "problem": f"{' '.join(failed[:4])} failed "
                             f"({error[0] or 'git gave no message'})"})
            continue
        folders, notes = prepare_srclibs(build, srclib_cache, fetch, run_step)
        for note in notes:
            print(f"{package}: {note}", flush=True)
        steps = recipe_steps(build, checkout, folders)
        steps.append([sys.executable, "-m", "cognalys.builds.apk_build",
                      "--projects-dir", str(projects), "--output-dir", str(work / package / "built"),
                      "--variant", "debug", "--disable-minification", "--task", gradle_task(build),
                      "--timeout", str(timeout),
                      *(f"--gradle-property={item}" for item in build.gradleprops)])
        note = prepare(build, checkout, "") if prepare else ""
        for step in steps:
            error = run_step(step)
            if error is not None:
                failed = step
                break
        if failed and failed[0] != "git" and prepare:
            # Detection before the build can miss (a plugin applied from a
            # convention plugin); the build's own message settles it, once.
            logs = "\n".join(path.read_text(encoding="utf-8", errors="replace")
                             for path in sorted((work / package / "built" / "logs").rglob("*.log")))
            retry_note = prepare(build, checkout, logs or " ")
            if retry_note:
                note = "; ".join(filter(None, (note, retry_note)))
                print(f"{package}: {retry_note}; building again", flush=True)
                failed, error = None, run_step(steps[-1])
                if error is not None:
                    failed = steps[-1]
        if note:
            build_notes[package] = note
        if failed:
            where = (f"logs in {work / package / 'built' / 'logs'}" if failed[0] != "git"
                     else error[0] or "git gave no message")
            if failed[0] != "git":
                # The logs under work/ go with the next disk cleanup; the
                # cause stays beside the app's APKs and source map.
                headline = keep_build_failure(work / package / "built", failed,
                                              out / f"build_failure_{package}.log")
                where = (f"{headline}; " if headline else "") + where + \
                    f"; cause kept in {out / f'build_failure_{package}.log'}"
            problems.append({"package": package,
                             "problem": f"{' '.join(failed[:4])} failed ({where})"})
            continue
        apk = built_apk((work / package / "built" / "apks").rglob("*.apk"), build)
        if apk is None:
            problems.append({"package": package, "problem": "no single APK of the build's "
                             f"flavour {build.gradle or '(default)'} was produced"})
            continue
        if r8_optimized_apk(apk):
            # Kept: an app that needs R8 to fit the dex limit builds no other
            # way. The problem line says its findings may sit on merged frames.
            problems.append({"package": package, "problem": "APK optimized by R8 (no debug "
                             "build was possible); lambda and inlined findings may not map "
                             "to source"})
        shutil.copyfile(apk, out / "apks" / f"{package}.debug_{build.version_code}.apk")
        built.append(build)
        own_packages[package] = app_packages(checkout, build.subdir) or (package,)
    with open(out / "sources.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_COLUMNS)
        writer.writeheader()
        for build in built:
            writer.writerow({"apk": f"{build.package}.debug_{build.version_code}.apk",
                             "repo_url": build.repo_url, "commit": build.commit,
                             "manifest_package": ";".join(own_packages.get(build.package, (build.package,))),
                             "packages_logic": packages_logic(),
                             "build_note": build_notes.get(build.package, "")})
    return built, problems
