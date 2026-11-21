"""Whether the reported method may be renamed, decided before any model sees it.

A patch that changes what a function computes while keeping a name that states
the old behaviour misleads every reader: QuantumBadger/RedReader closed PR
#1373 in part because `General.sha1` was made to return a SHA-256 digest.
Sometimes the correct patch is a rename. A rename is also the easiest way to
break a build, a reflective lookup, a ProGuard rule, an XML `onClick`, or a
subclass that overrides the method -- and some of those only fail at run time.

This is a repository fact the pipeline computes, never a maintainer approval.
Each condition is either hard or resolvable.

Hard -- a rename here can break something no build or edit of this patch
would show, so the patch takes the non-rename route (a correctly named
function, every caller switched):

  * a declaration of the family that overrides or is overridable (override,
    open, abstract, expect/actual, native, a Java override without
    @Override), sits inside an interface body, or carries an annotation that
    pins its name (@Keep, @JvmName, @JavascriptInterface);
  * a platform or language convention name (equals, invoke, onCreate...);
  * the name inside a string literal (reflection), or read by a machine
    outside the sources: an XML attribute value (android:onClick), a
    ProGuard/R8 rule, AIDL, a JSON key, properties or build configuration;
  * the declaring module publishes an artifact, or is a library no
    application module of this repository builds;
  * a reference in a source set or variant the build does not compile
    (jvmMain, desktop, an unselected flavor, release, a test set the build
    does not run), or in a second application module;
  * calls that cannot be attributed: the same name is declared elsewhere and
    the semantic resolution cannot tell which declaration a call binds to.

Resolvable -- recorded, and turned into references the patch must update in
the same change (every call, `import pkg.name` and `::name`): an overload
family declared in the reported file, the same name declared in other classes
(when every call is attributed), calls the semantic resolution missed or left
ambiguous, an unrelated interface elsewhere in the file, an in-repository
library module an application module depends on. Prose in string resources or
changelogs is not a reference at all.

`allowed` is true when no hard blocker holds. The validator then checks that
the patch updated every reference, and the build compiles them.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from cognalys.extraction.repository_index import build_repository_index
from cognalys.extraction.semantic_index import STATEMENT_KEYWORDS, kotlin_function_declaration


# Bumped whenever what counts as hard or resolvable changes. Learned failure
# signatures about renames recorded under an older version no longer describe
# what the pipeline does (learning.knowledge.finding_history drops them).
RENAME_RULES_VERSION = 2
MAX_REFERENCES = 30
MAX_TEXT_FILE_BYTES = 1_000_000
_SOURCE_SUFFIXES = {".java", ".kt", ".kts"}
# Files a machine reads by name. Any occurrence of the symbol is a hard
# reference: a ProGuard keep rule, an AIDL method, build configuration.
_MACHINE_READ_SUFFIXES = {".pro", ".cfg", ".aidl", ".properties", ".gradle", ".toml",
                          ".yml", ".yaml"}
_PINNING_ANNOTATIONS = re.compile(r"@(?:Keep|JvmName|JavascriptInterface|UsedByNative|CalledByNative)\b")
_OVERRIDE_OR_OPEN = re.compile(r"(?<![\w$])(?:override|open|abstract|native|external|expect|actual)\b|@Override\b")
_APPLICATION_PLUGIN = re.compile(r"com\.android\.application|android\.application\b|plugins\.android\.app\b")
_PUBLISHING = re.compile(
    r"maven-publish|`maven-publish`|\bpublishing\s*\{|\bmavenPublishing\b|\bsigning\s*\{"
    r"|id\s*\(?\s*[\"']signing[\"']|com\.vanniktech\.maven\.publish"
)
# Names a platform, the JDK or a language convention calls by name: a rename
# compiles and silently stops the call (Kotlin operators, Object methods,
# Android lifecycle callbacks).
_PLATFORM_NAMES = frozenset({
    "equals", "hashCode", "toString", "finalize", "clone", "compareTo", "close",
    "run", "call", "invoke", "iterator", "hasNext", "next", "get", "set", "contains",
    "getValue", "setValue", "provideDelegate", "plus", "minus", "times", "div", "rem",
    "unaryMinus", "unaryPlus", "not", "inc", "dec", "rangeTo", "main",
})
_PLATFORM_CALLBACK = re.compile(r"^on[A-Z]\w*$")
_PACKAGE = re.compile(r"(?m)^\s*package\s+([\w.]+)")
# Source sets an Android debug build compiles, besides the selected flavors
# and the shared sets android_source_rank ranks 1.
_BUILT_SOURCE_SETS = {"", "main", "debug", "androidMain"}
# Test sets the build validation compiles once the patch edits them
# (applied_solutions.changed_test_modules runs their test tasks).
_BUILT_TEST_SOURCE_SETS = {"test", "androidTest"}


def _declaration_pattern(name: str) -> re.Pattern:
    """A line declaring `name`. A statement keyword is never the Java return
    type: the validator's reference count skips every line this matches, so
    `return name(alias)` must stay a reference (STATEMENT_KEYWORDS)."""
    escaped = re.escape(name)
    keywords = "|".join(sorted(STATEMENT_KEYWORDS))
    return re.compile(
        rf"{kotlin_function_declaration(name)}"
        rf"|^\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*"
        rf"(?:(?:public|protected|private|static|final|synchronized|native|abstract"
        rf"|default|strictfp)\s+)*(?!(?:{keywords})\s)"
        rf"(?P<java_type>[\w$.<>\[\],?]+)\s+{escaped}\s*\("
        rf"(?:[^;]*$|[^()]*\)\s*(?:throws\s+[\w.,\s]+)?\{{)",
        re.MULTILINE,
    )


def reference_pattern(name: str) -> re.Pattern:
    """Every way a source file names a function: a call, `::name`, an import.

    Shared with the validator (semantic_validation.rename_issues), so the
    references the assessment lists and the ones a patch must update are
    counted alike.
    """
    escaped = re.escape(name)
    return re.compile(
        rf"(?<![\w$]){escaped}\s*\("
        rf"|::\s*{escaped}\b"
        rf"|^\s*import\s+(?:static\s+)?[\w.]+\.{escaped}\s*;?\s*$",
        re.MULTILINE,
    )


def _module_build_file(root: Path, source: Path) -> Path | None:
    for directory in [source.parent, *source.parent.parents]:
        if directory != root and root not in directory.parents:
            return None
        for name in ("build.gradle.kts", "build.gradle"):
            candidate = directory / name
            if candidate.is_file():
                return candidate
        if directory == root:
            return None
    return None


# Declarations a new function name can clash with: functions (generic and
# extension ones too), Kotlin properties and constants, Java fields.
_DECLARED_NAME = re.compile(
    r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w$.]+(?:<[^>]*>)?\??\.)?(?P<fun>[A-Za-z_$][\w$]*)\s*\("
    r"|\b(?:val|var)\s+(?:<[^>]+>\s*)?(?:[\w$.]+\.)?(?P<prop>[A-Za-z_$][\w$]*)\s*[:=]"
    r"|^[ \t]*(?:@[\w.]+(?:\([^)]*\))?\s*)*"
    r"(?:(?:public|protected|private|static|final|synchronized|native|abstract"
    r"|default|strictfp|volatile|transient)\s+)*(?!(?:return|new|throw|else|case)\s)"
    r"[\w$.<>\[\],?]+\s+(?P<java>[A-Za-z_$][\w$]*)\s*(?:\(|=|;)",
    re.MULTILINE,
)
_NOT_NAMES = frozenset({"if", "for", "while", "switch", "catch", "synchronized",
                        "return", "new", "when", "else", "throw", "super", "this"})
MAX_NAMES_IN_SCOPE = 200
# Suffixes a free name is built from when the planned one is taken: each
# says what the function returns, never which version of it this is.
_FREE_NAME_SUFFIXES = ("Hex", "Digest", "Bytes", "String", "Value", "Result")


def declared_names(text: str) -> set[str]:
    """Names `text` declares that a new function name could clash with."""
    names = set()
    for match in _DECLARED_NAME.finditer(text or ""):
        name = match.group("fun") or match.group("prop") or match.group("java") or ""
        if name and name not in _NOT_NAMES and name not in STATEMENT_KEYWORDS:
            names.add(name)
    return names


def names_in_scope(root: Path, source_file_path: str,
                   sources: dict[str, list[str]] | None = None) -> list[str]:
    """Names declared in the reported file and in the other files of its
    package in the same module: a Kotlin top-level or extension function, an
    object member or a sibling's declaration a new name would clash with.

    RedReader's General.sha1 (2026-11-03): three of four drafts renamed it
    to a name already declared, and each retry was told only that the name
    was taken, never which names were declared or free.
    """
    root = Path(root)
    target = root / str(source_file_path or "")
    target_relative = PurePosixPath(str(source_file_path or "")).as_posix()
    if sources is None:
        sources = {}
        if target.parent.is_dir():
            for candidate in target.parent.iterdir():
                if candidate.suffix in {".java", ".kt"} and candidate.is_file():
                    sources[candidate.relative_to(root).as_posix()] = (
                        _read(candidate).splitlines())
    target_text = "\n".join(sources.get(target_relative) or _read(target).splitlines())
    names = declared_names(target_text)
    package_match = _PACKAGE.search(target_text)
    package = package_match.group(1) if package_match else None
    module = _module_build_file(root, target)
    for relative, lines in sources.items():
        if relative == target_relative or PurePosixPath(relative).suffix not in {".java", ".kt"}:
            continue
        text = "\n".join(lines)
        match = _PACKAGE.search(text)
        if (match.group(1) if match else None) != package:
            continue
        if _module_build_file(root, root / relative) != module:
            continue
        names |= declared_names(text)
    return sorted(names)[:MAX_NAMES_IN_SCOPE]


def suggest_free_name(planned: str, taken) -> str:
    """The planned name when it is free, else the first free name built from
    it (sha256 -> sha256Hex -> sha256Digest ...), else planned + a number."""
    taken = set(taken or ())
    planned = str(planned or "")
    if not planned or planned not in taken:
        return planned
    for suffix in _FREE_NAME_SUFFIXES:
        if planned + suffix not in taken:
            return planned + suffix
    number = 2
    while f"{planned}{number}" in taken:
        number += 1
    return f"{planned}{number}"


def free_name_rule_text() -> str:
    """How a draft or a plan picks a new name against names_in_scope."""
    return (
        "names_in_scope lists the functions, properties and fields already "
        "declared in the reported file and in its package's other files of the "
        "same module. A new name -- a rename target or a correctly named "
        "function added beside the old one -- must not be one of them; when "
        "the name you want is taken, add a suffix that says what it returns "
        "(sha256 taken -> sha256Hex) and check that one too."
    )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _gradle_path(root: Path, build_file: Path) -> str:
    relative = build_file.parent.relative_to(root).as_posix()
    return ":" + relative.replace("/", ":") if relative not in {"", "."} else ":"


def _depends_on(build_text: str, gradle_path: str) -> bool:
    """Whether a build script depends on the in-repository project `gradle_path`."""
    if gradle_path == ":":
        return False
    quoted = re.escape(gradle_path)
    accessor = "projects." + ".".join(
        re.sub(r"[-_](\w)", lambda match: match.group(1).upper(), part)
        for part in gradle_path.strip(":").split(":")
    )
    return bool(
        re.search(rf"project\s*\(\s*(?:path\s*[:=]\s*)?[\"']{quoted}[\"']", build_text)
        or re.search(rf"(?<![\w.]){re.escape(accessor)}\b", build_text)
    )


def _publishes(root: Path, build_text: str) -> bool:
    """The module, or a script applied to every module, publishes an artifact."""
    if _PUBLISHING.search(build_text):
        return True
    for name in ("build.gradle.kts", "build.gradle"):
        text = _read(root / name)
        if re.search(r"\b(?:allprojects|subprojects)\s*\{", text) and _PUBLISHING.search(text):
            return True
    return (root / "jitpack.yml").is_file()


def _blank(code: str) -> str:
    from cognalys.llm.semantic_validation import blank_comments_and_literals

    return blank_comments_and_literals(code)


def _enclosing_types(lines: list[str], line_number: int) -> list[tuple[str, str]]:
    """(kind, header) of every type body enclosing `line_number`, innermost last."""
    text = _blank("\n".join(lines))
    offset = sum(len(line) + 1 for line in lines[:line_number - 1])
    stack: list[tuple[str, str]] = []
    header_start = 0
    for index, character in enumerate(text[:offset]):
        if character == "{":
            header = text[header_start:index]
            match = None
            for match in re.finditer(
                    r"(?<![.\w])(interface|class|object|enum|record)\b(?!\s*\.)([^;{}()]*(?:\([^)]*\))?[^;{}]*)$",
                    header):
                pass
            if match and not re.search(r"\bnew\s+[\w.<>]+\s*\([^)]*\)\s*$", header):
                kind = "interface" if match.group(1) == "interface" or re.search(
                    r"@interface\s*$", header[:match.start()]) else match.group(1)
                stack.append((kind, match.group(0)))
            else:
                stack.append(("block", ""))
            header_start = index + 1
        elif character == "}":
            if stack:
                stack.pop()
            header_start = index + 1
        elif character == ";":
            header_start = index + 1
    return [item for item in stack if item[0] != "block"]


def _repository_types(sources: dict[str, list[str]]) -> dict[str, list[str]]:
    """Simple type name -> the lines of the file declaring it."""
    types: dict[str, list[str]] = {}
    for lines in sources.values():
        for match in re.finditer(r"\b(?:class|interface|enum)\s+([A-Z]\w*)", "\n".join(lines)):
            types.setdefault(match.group(1), lines)
    return types


def _java_override_without_annotation(relative: str, lines: list[str], line_number: int,
                                      name: str, types: dict[str, list[str]]) -> bool:
    """A Java method that overrides a supertype method no @Override marks.

    Only a supertype this repository declares can be checked, plus the
    methods of java.lang.Object; a platform supertype's methods are not
    known here, so those are left to the callback-name rule.
    """
    if not relative.endswith(".java"):
        return False
    if name in {"equals", "hashCode", "toString", "finalize", "clone"}:
        return True
    enclosing = _enclosing_types(lines, line_number)
    if not enclosing:
        return False
    header = enclosing[-1][1]
    own = re.match(r"\w+\s+([A-Z]\w*)", header)
    # A type argument naming the class itself (Adapter<Self.ViewHolder>) is
    # not a supertype.
    supertypes = set(re.findall(r"\b([A-Z]\w*)\b", " ".join(re.findall(
        r"\b(?:extends|implements)\s+([^{]+)", header)))) - {own.group(1) if own else ""}
    declaration = re.compile(rf"(?<![\w$.]){re.escape(name)}\s*\(")
    return any(declaration.search("\n".join(types.get(supertype, [])))
               for supertype in supertypes)


def _source_set(path: PurePosixPath) -> str:
    """The Gradle source set of `path` (src/<set>/java|kotlin/...), or "" for
    a layout without one (an Eclipse-style src/ the build maps to main)."""
    parts = path.parts
    for index, part in enumerate(parts[:-2]):
        if part == "src" and parts[index + 2] in {"java", "kotlin", "aidl", "resources"}:
            return parts[index + 1]
    return ""


def _built_by_debug_build(root: Path, relative: str) -> tuple[bool, str]:
    """Whether the validation build compiles `relative` once the patch edits it."""
    from cognalys.extraction.extract_methods_single_csv import android_source_rank

    source_set = _source_set(PurePosixPath(relative))
    if source_set in _BUILT_TEST_SOURCE_SETS:
        return True, ""
    rank = android_source_rank(PurePosixPath(f"src/{source_set}/x")) if source_set else 0
    if rank == 1:
        return True, ""
    if rank == 2:
        return False, f"source set {source_set} is not part of an Android build"
    if source_set in _BUILT_SOURCE_SETS:
        return True, ""
    build_file = _module_build_file(root, root / relative)
    if build_file is None:
        return False, f"source set {source_set} has no module build file"
    from cognalys.validation.applied_solutions import _selected_product_flavors

    selected = _selected_product_flavors(build_file.parent)
    combined = "".join(
        flavor if index == 0 else flavor[:1].upper() + flavor[1:]
        for index, flavor in enumerate(selected)
    )
    built = set(selected) | ({combined} if combined else set())
    built |= {name + "Debug" for name in built}
    if source_set in built:
        return True, ""
    return False, f"source set {source_set} is not compiled by the debug build of the selected variant"


def assess_rename_safety(repo_root: Path, source_file_path: str, method_name: str,
                         semantic_resolution: dict | None,
                         repository_index: dict | None = None) -> dict:
    root = Path(repo_root)
    target = root / str(source_file_path or "")
    record = {
        "allowed": False,
        "symbol": method_name,
        "declaration": {},
        "declarations": [],
        "references": [],
        "hard_blockers": [],
        "resolvable": [],
        "blocked_by": [],
        "rules_version": RENAME_RULES_VERSION,
        "bound": (
            "Lexical. A hard blocker means the rename could break something "
            "no build of this patch shows; a resolvable reason only adds "
            "references the patch must update. Build validation compiles "
            "every reference after the patch is applied."
        ),
    }
    hard: list[str] = []
    resolvable: list[str] = []
    if not method_name or len(method_name) < 3 or not target.is_file():
        record["hard_blockers"] = record["blocked_by"] = ["target_method_or_source_not_found"]
        return record
    index = repository_index or build_repository_index(root)
    sources: dict[str, list[str]] = {}
    for path, content in index["source_cache"].items():
        try:
            relative = Path(path).relative_to(root).as_posix()
        except ValueError:
            continue
        sources[relative] = str(content).splitlines()
    target_relative = target.relative_to(root).as_posix()
    if target_relative not in sources:
        sources[target_relative] = _read(target).splitlines()
    target_lines = sources[target_relative]

    declaration = _declaration_pattern(method_name)
    declarations = []
    for relative, lines in sources.items():
        for number, line in enumerate(lines, 1):
            if declaration.search(line):
                declarations.append((relative, number))
    declaration_lines = set(declarations)
    in_target = [item for item in declarations if item[0] == target_relative]
    elsewhere = sorted({path for path, _ in declarations if path != target_relative})
    if not in_target:
        hard.append("no_declaration_in_the_reported_file")
    elif len(in_target) > 1:
        resolvable.append(f"overload_family_in_the_reported_file:{len(in_target)}")
    record["declarations"] = [{"path": path, "line": line} for path, line in in_target]
    if in_target:
        record["declaration"] = {"path": target_relative, "line": in_target[0][1]}
    if elsewhere:
        resolvable.append("same_name_declared_elsewhere_in_the_repository:" + ",".join(elsewhere[:5]))

    types = _repository_types(sources)
    for _, line_number in in_target:
        line = target_lines[line_number - 1]
        context = "\n".join(target_lines[max(0, line_number - 4):line_number])
        if _OVERRIDE_OR_OPEN.search(line):
            hard.append(f"override_or_overridable_declaration:{line_number}")
        if _PINNING_ANNOTATIONS.search(context) or re.search(r"@Override\b", context):
            hard.append(f"annotation_pins_or_overrides_the_name:{line_number}")
        enclosing = _enclosing_types(target_lines, line_number)
        if enclosing and enclosing[-1][0] == "interface":
            hard.append(f"declared_inside_an_interface:{line_number}")
        if _java_override_without_annotation(target_relative, target_lines, line_number,
                                             method_name, types):
            hard.append(f"java_override_without_annotation:{line_number}")
    if re.search(r"(?m)^\s*(?:[\w@]+\s+)*(?:fun\s+)?interface\s+\w", "\n".join(target_lines)) \
            and not any(item.startswith("declared_inside_an_interface") for item in hard):
        resolvable.append("unrelated_interface_in_the_declaring_file")
    # A constructor carries its class's name: renaming it renames the type.
    if method_name == "<init>" or re.search(
            rf"\b(?:class|object|interface|enum|record)\s+{re.escape(method_name)}\b",
            "\n".join(target_lines)):
        hard.append("constructor_or_type_name")
    if method_name in _PLATFORM_NAMES or (
            _PLATFORM_CALLBACK.match(method_name) and target_relative.endswith(".java")):
        hard.append("platform_or_convention_name")

    build_file = _module_build_file(root, target)
    application_modules = []
    for candidate in index["paths"]:
        candidate = Path(candidate)
        if candidate.name in {"build.gradle", "build.gradle.kts"} and _APPLICATION_PLUGIN.search(
                _read(candidate)):
            application_modules.append(candidate)
    if build_file is None:
        hard.append("module_build_file_not_found")
    else:
        record["module_build_file"] = build_file.relative_to(root).as_posix()
        build_text = _read(build_file)
        if _publishes(root, build_text):
            hard.append("module_publishes_an_artifact")
        elif not _APPLICATION_PLUGIN.search(build_text):
            module = _gradle_path(root, build_file)
            users = sorted(
                _gradle_path(root, application) for application in application_modules
                if application != build_file and _depends_on(_read(application), module)
            )
            if users:
                resolvable.append(f"in_repository_library_module_used_by:{','.join(users)}")
            else:
                hard.append("module_is_not_an_application")

    semantic = semantic_resolution or {}
    available = semantic.get("status") == "available"
    ambiguous = bool(semantic.get("ambiguous_calls"))
    if not available:
        resolvable.append("semantic_resolution_unavailable")
    if ambiguous:
        resolvable.append("ambiguous_same_name_calls")
    resolved = {
        (str(item.get("path", "")), int(item.get("line") or 0))
        for item in semantic.get("resolved_callers") or [] if isinstance(item, dict)
    }
    package = (_PACKAGE.search("\n".join(target_lines)) or [None, ""])[1]
    call = re.compile(rf"(?<![\w$]){re.escape(method_name)}\s*\(")
    method_reference = re.compile(rf"::\s*{re.escape(method_name)}\b")
    target_reference = re.compile(rf"\b{re.escape(target.stem)}\s*::\s*{re.escape(method_name)}\b")
    import_line = re.compile(
        rf"^\s*import\s+(?:static\s+)?([\w.]+)\.{re.escape(method_name)}\s*;?\s*$")
    string_literal = re.compile(rf"\"{re.escape(method_name)}\"")
    calls, imports, method_references = set(), set(), set()
    for relative, lines in sources.items():
        for number, line in enumerate(lines, 1):
            if (relative, number) in declaration_lines:
                continue
            imported = import_line.match(line)
            if imported:
                if not package or imported.group(1).startswith(package):
                    imports.add((relative, number))
                continue
            if call.search(line):
                calls.add((relative, number))
            if method_reference.search(line):
                method_references.add((relative, number))
            if string_literal.search(line):
                hard.append(f"name_in_string_literal:{relative}:{number}")
    unresolved = sorted(calls - resolved)
    if elsewhere:
        # The same name is declared in another class: a call is a reference
        # only once the semantic resolution binds it to this declaration.
        # When it cannot tell, no list of references is trustworthy.
        if unresolved and (not available or ambiguous or len(in_target) > 1):
            hard.append(
                "unattributable_calls_with_same_name_declared_elsewhere:"
                + ",".join(f"{path}:{line}" for path, line in unresolved[:5])
            )
        calls &= resolved
        method_references = {
            (path, line) for path, line in method_references
            if target_reference.search(sources[path][line - 1])
        }
    elif unresolved:
        resolvable.append(
            "calls_not_in_the_semantic_resolution:"
            + ",".join(f"{path}:{line}" for path, line in unresolved[:5])
        )
    if method_references:
        resolvable.append("method_references:" + ",".join(
            f"{path}:{line}" for path, line in sorted(method_references)[:5]))
    references = (
        [{"path": path, "line": line, "kind": "call"} for path, line in calls]
        + [{"path": path, "line": line, "kind": "import"} for path, line in imports]
        + [{"path": path, "line": line, "kind": "method_reference"}
           for path, line in method_references]
    )
    references.sort(key=lambda item: (item["path"], item["line"], item["kind"]))
    record["references"] = references
    if len(references) > MAX_REFERENCES:
        hard.append("too_many_references_to_review")

    reference_files = sorted({item["path"] for item in references} | {target_relative})
    for relative in reference_files:
        built, reason = _built_by_debug_build(root, relative)
        if not built:
            hard.append(f"reference_outside_the_built_sources:{relative} ({reason})")
    # One application module is built and checked. References in a second
    # one (beside the declaring application, or a second user of a library)
    # are compiled by nothing this patch's validation runs.
    reference_applications = {
        module for module in (
            _module_build_file(root, root / relative) for relative in reference_files)
        if module is not None and module in application_modules
    }
    if build_file is not None and (
            reference_applications - {build_file} if build_file in application_modules
            else set(list(sorted(reference_applications))[1:])):
        hard.append("references_in_a_second_application_module:" + ",".join(sorted(
            _gradle_path(root, module) for module in reference_applications)))

    word = re.compile(rf"(?<![\w$]){re.escape(method_name)}(?![\w$])")
    json_key = re.compile(rf"\"{re.escape(method_name)}\"")
    for path in index["paths"]:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in _SOURCE_SUFFIXES:
            continue
        machine_read = suffix in _MACHINE_READ_SUFFIXES or re.match(
            r"proguard.*\.txt$", path.name, re.IGNORECASE)
        if not machine_read and suffix not in {".xml", ".json"}:
            continue
        try:
            if path.stat().st_size > MAX_TEXT_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        # XML element text (a string resource) and JSON values in prose are
        # words for people; an attribute value or a key is read by a machine.
        if machine_read:
            found = bool(word.search(text))
        elif suffix == ".xml":
            found = _xml_names_method(text, method_name)
        else:
            found = bool(json_key.search(text))
        if found:
            hard.append(f"referenced_outside_sources:{path.relative_to(root).as_posix()}")

    record["hard_blockers"] = list(dict.fromkeys(hard))
    record["blocked_by"] = list(record["hard_blockers"])
    record["resolvable"] = list(dict.fromkeys(resolvable))
    record["allowed"] = not record["hard_blockers"]
    record["names_in_scope"] = names_in_scope(root, target_relative, sources)
    return record


_XML_ATTRIBUTE = re.compile(r"([\w:.-]+)\s*=\s*\"([^\"]*)\"")


def _xml_names_method(text: str, method_name: str) -> bool:
    """Whether an XML file names the method where a framework calls it by name.

    Only an event-handler attribute whose value is the bare name
    (`android:onClick="encrypt"`, resolved by reflection) and a data-binding
    expression that calls or references it (`@{() -> vm.encrypt()}`,
    `@{vm::encrypt}`). A resource name (`<string name="encrypt">`) and a
    resource reference (`@+id/decrypt`, `@string/encrypt`) are identifiers
    read through R, never the method: crypto apps name their resources after
    the operation, and counting them refused KeyPass's rename route.
    """
    name = re.escape(method_name)
    expression = re.compile(rf"(?:(?<![\w$]){name}\s*\(|::\s*{name}(?![\w$]))")
    for attribute, value in _XML_ATTRIBUTE.findall(text):
        local = attribute.rsplit(":", 1)[-1]
        if re.fullmatch(r"on[A-Z]\w*", local) and value.strip() == method_name:
            return True
        if re.match(r"\s*@=?\{", value) and expression.search(value):
            return True
    return False


def compact_rename_safety(record: dict | None) -> dict:
    """The assessment as the adjudication, the plan and the reviews read it.

    They received only an evidence id (RENAME-001), never its data, and an
    adjudicator wrote "that authorization is not supplied" over an allowed
    rename (Orgzly's sha1, 2026-10-25). Empty when no symbol was assessed.
    """
    record = record or {}
    if not record.get("symbol"):
        return {}
    return {
        "symbol": record.get("symbol", ""),
        "allowed": bool(record.get("allowed")),
        "declarations": list(record.get("declarations") or (
            [record["declaration"]] if record.get("declaration") else [])),
        "hard_blockers": list(record.get("hard_blockers", record.get("blocked_by")) or []),
        "resolvable": list(record.get("resolvable") or []),
        "references": [
            {key: item.get(key) for key in ("path", "line", "kind") if key in item}
            for item in record.get("references") or [] if isinstance(item, dict)
        ],
        **({"names_in_scope": list(record["names_in_scope"]),
            "free_name_rule": free_name_rule_text()}
           if record.get("names_in_scope") else {}),
        "meaning": (
            "a repository check this pipeline ran, not a maintainer approval: "
            "with no hard_blockers the declaration may be renamed, updating every "
            "reference in the same patch"
        ),
    }


def rename_rule_text() -> str:
    """The rename rule as the generator, the planner and the reviewers read it."""
    return (
        "A rename is a repository check this pipeline runs (rename_safety), "
        "never a maintainer approval. When rename_safety.hard_blockers is "
        "empty, the declaration (or its whole overload family) may be renamed, "
        "and every entry of rename_safety.references -- each call, each "
        "`import ...name` line and each `::name` -- is updated to the new name "
        "in the same patch. When hard_blockers is not empty, do not rename: "
        "add a correctly named function, switch every caller to it, and keep "
        "the weak-named one only while callers outside the patch remain. A "
        "maintainer preference about a name is at most a note in the pull "
        "request, never a blocker."
    )
