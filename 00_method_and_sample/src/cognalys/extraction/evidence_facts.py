#!/usr/bin/env python3
"""Answer the evidence the adjudicator keeps asking for, from the checkout.

The failure ledger's largest stage is adjudication: 29 occurrences over 23
distinct signatures, nearly all of the same shape --

    adjudication:unresolved ownership and version of the reported class
    adjudication:target implementation unavailable
    adjudication:obtain the implementation being reported
    adjudication:reported-rule applicability is unconfirmed
    adjudication:incomplete_reachable_caller_contract

-- which is not a model declining to have an opinion. It is a model asking
for a fact and being told nothing, because the evidence responder could only
answer with a repository file, and none of these questions is answered by
one. Each was then recorded as an unconfirmed cause and the finding ended.

Every function here answers one of those questions deterministically, or
refuses with a reason that names what is missing. None of them consults a
model, and none of them returns a value it had to guess: an unresolved
answer is returned as unresolved, because the whole point of the exercise is
that the adjudicator can tell a fact from a hypothesis.

The specification answer follows the rule the rest of this pipeline uses for
detector families: the predicate lives in code and the rationale is quoted
from the specification's own data at match time, so it holds for every
specification that documents its governed APIs and for none that does not.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

_EXTRACTION = Path(__file__).resolve().parent
_SOURCE_ROOT = _EXTRACTION.parent

from cognalys.extraction.dependency_context import group_owns_class  # noqa: E402
from cognalys.extraction.symbol_binding import resolve_call_bindings  # noqa: E402
from cognalys.extraction.test_classpath import declared_coordinates  # noqa: E402


_SOURCE_SUFFIXES = (".java", ".kt")
_SKIPPED_PARTS = frozenset({".git", "build", ".gradle", "out", "node_modules"})
MAX_CALLER_FILES = 40


def _source_files(repo_root: Path, limit: int = 6000) -> List[Path]:
    found: List[Path] = []
    for path in repo_root.rglob("*"):
        if len(found) >= limit:
            break
        if path.suffix not in _SOURCE_SUFFIXES or not path.is_file():
            continue
        if _SKIPPED_PARTS & set(path.parts):
            continue
        found.append(path)
    return found


def _declared_version(repo_root: Path, coordinate: str) -> str:
    """The version declared for `coordinate`, from the build files it is in.

    Read from the catalog or the literal coordinate, never inferred from a
    lock file the build may not use.
    """
    group_artifact = coordinate.split(":")
    if len(group_artifact) < 2:
        return ""
    literal = re.compile(
        rf"""["']{re.escape(group_artifact[0])}:{re.escape(group_artifact[1])}:([^"']+)["']"""
    )
    catalog_entry = re.compile(
        rf"""module\s*=\s*["']{re.escape(group_artifact[0])}:"""
        rf"""{re.escape(group_artifact[1])}["'][^\n]*?version(?:\.ref)?\s*=\s*["']([^"']+)["']"""
    )
    for name in (
        "gradle/libs.versions.toml", "build.gradle", "build.gradle.kts",
        "app/build.gradle", "app/build.gradle.kts",
    ):
        path = repo_root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in (literal, catalog_entry):
            match = pattern.search(text)
            if match:
                return match.group(1)
    return ""


def artifact_ownership(
    repo_root: Path, class_name: str, relative_path: str = "",
) -> Dict:
    """Which artifact ships the reported class, and at which declared version.

    Statuses:
      repository_class      the checkout declares it; no artifact owns it
      declared_dependency   a declared coordinate's group ships it
      undeclared_external   nothing in the checkout declares or ships it
      unresolved            the build files could not be read at all
    """
    repo_root = Path(repo_root)
    record: Dict = {
        "class_name": str(class_name),
        "artifact": "",
        "declared_version": "",
        "build_file_scope": relative_path or "",
    }
    if not repo_root.is_dir():
        return {**record, "status": "unresolved",
                "detail": "the checkout is not available"}
    outer = str(class_name or "").split("$")[0]
    simple = outer.rsplit(".", 1)[-1]
    declaring = [
        path.relative_to(repo_root).as_posix()
        for path in _source_files(repo_root)
        if path.stem == simple
    ]
    if declaring:
        return {
            **record, "status": "repository_class",
            "declaring_paths": sorted(declaring)[:8],
            "detail": "the checkout declares this class, so no external "
                      "artifact owns it and no version applies",
        }
    coordinates = declared_coordinates(
        repo_root, relative_path or "build.gradle"
    )
    if not coordinates:
        return {**record, "status": "unresolved",
                "detail": "no dependency declaration could be read for this "
                          "module, so ownership is unestablished"}
    owners = [
        coordinate for coordinate in coordinates
        if group_owns_class(coordinate.split(":", 1)[0], outer)
    ]
    if len(owners) == 1:
        return {
            **record, "status": "declared_dependency",
            "artifact": owners[0],
            "declared_version": _declared_version(repo_root, owners[0]),
            "detail": "one declared coordinate's group ships this class",
        }
    if owners:
        return {
            **record, "status": "unresolved",
            "candidates": sorted(owners),
            "detail": "several declared coordinates could ship this class",
        }
    return {
        **record, "status": "undeclared_external",
        "declared_dependencies": len(coordinates),
        "detail": "the checkout neither declares this class nor declares a "
                  "dependency whose group ships it",
    }


def _api_tokens(text: str) -> List[str]:
    """Method-ish tokens in an api_pattern, for matching against the source."""
    return [
        name for name in re.findall(r"([A-Za-z_][\w$]*)\s*\(", str(text or ""))
    ]


def specification_applicability(
    specification_context: Dict, method_body: str, error_message: str = "",
) -> Dict:
    """Whether the specification's own governed APIs appear in this code.

    The specification states which calls it governs, in `events[].api_patterns`
    and `applies_to_types`. Matching those against the reviewed source is a
    fact about this finding; asserting that the rule applies because the
    detector fired is not. The matched pattern and its own condition text are
    quoted back, so the adjudicator weighs the specification's wording rather
    than this function's paraphrase -- and an unverified specification is
    reported as unverified.
    """
    specification = specification_context if isinstance(
        specification_context, dict
    ) else {}
    if not specification:
        return {"status": "specification_unavailable",
                "detail": "no specification context accompanied this finding"}
    source = str(method_body or "") + "\n" + str(error_message or "")
    matched = []
    for event in specification.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        for pattern in event.get("api_patterns", []) or []:
            for token in _api_tokens(pattern):
                if re.search(rf"\b{re.escape(token)}\s*\(", source):
                    matched.append({
                        "event": str(event.get("name", "")),
                        "api_pattern": str(pattern),
                        "matched_call": token,
                        "condition": str(event.get("condition", ""))[:600],
                    })
                    break
    authority = {
        "spec_id": str(specification.get("spec_id", "")),
        "source_verified": bool(specification.get("source_verified")),
        "source_title": str(specification.get("source_title", ""))[:200],
        "source_url": str(specification.get("source_url", "")),
        "applies_to_types": [
            str(item) for item in specification.get("applies_to_types", []) or []
        ][:8],
    }
    if not matched:
        return {
            "status": "no_governed_api_matched", "authority": authority,
            "detail": "none of the specification's own api_patterns matches a "
                      "call in the reviewed source, so the reported rule is "
                      "not shown to govern this code",
        }
    return {
        "status": "governed_api_matched",
        "authority": authority,
        "matched": matched[:8],
        "detail": "the reviewed source contains a call the specification "
                  "itself names as governed; the event's own condition text "
                  "is quoted so its limits are read with it",
    }


# Resolutions that name one type for the receiver. Anything else leaves the
# binding open.
_SETTLED_BINDINGS = frozenset({
    "unique_external", "consistent_with_reported_api",
    "single_wildcard_import", "repository_type", "bytecode_invocation",
})


def governed_call_bindings(
    repo_root: Path, relative_path: str, specification_context: Dict,
    bytecode: Dict | None = None,
) -> Dict:
    """What each call the specification governs actually binds to.

    The specification states the calls it governs in `events[].api_patterns`
    and names their owning type, qualified, in its own condition text. Each
    such call found in the reported file is resolved from that file's
    imports and declarations (see symbol_binding), and compared with the
    type the specification names.

    This answers the question the adjudicator keeps recording as a blocker
    -- "the source establishes only a lexical call; it does not establish
    that this call resolves to android.webkit.WebView.setWebContentsDebugging
    Enabled(boolean)" -- without waiting for the extractor to ask it in a
    wording the responder recognises.

    Statuses:
      resolved_to_governed_type  every governed call found binds to a type
                                 the specification names
      resolved_elsewhere         at least one binds to some other type
      unresolved                 at least one could not be bound at all
      no_governed_call           none of the governed calls is in the file

    `bytecode` is the reported method's resolved invocations from the
    analysed APK (see bytecode_binding). A call the file's text cannot bind
    -- no receiver, an expression receiver, generated code -- is bound from
    the invoke instruction the compiler wrote for it, when name and
    parameter count single out one type. Calls only the bytecode shows,
    such as a Kotlin property assignment compiled to a setter, are listed
    beside the status and do not change it: the APK may have been built
    from another revision than the checkout.
    """
    from cognalys.extraction.symbol_binding import qualified_type_names  # local: same package
    from cognalys.extraction.bytecode_binding import bindings_by_name, settle_from_bytecode

    specification = specification_context if isinstance(
        specification_context, dict
    ) else {}
    repo_root = Path(repo_root)
    try:
        source = (repo_root / relative_path).read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return {"status": "unresolved",
                "detail": "the reported file could not be read"}
    names: List[str] = []
    wording: List[str] = []
    for event in specification.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        wording.append(str(event.get("condition", "")))
        for pattern in event.get("api_patterns", []) or []:
            wording.append(str(pattern))
            for token in _api_tokens(pattern):
                if token not in names:
                    names.append(token)
    grouped = bindings_by_name(bytecode)
    governed = list(names)
    names = [
        name for name in names
        if re.search(rf"\b{re.escape(name)}\s*\(", source)
    ]
    # The types the specification governs are named by it too. A receiverless
    # constructor bound from its import (BlueGate's KeyPair, run_v7,
    # 2026-10-31) was otherwise compared only with the types its condition
    # text spells: SecretKeySpecSpec's names java.lang.String alone, and
    # SecretKeySpec(key, "AES") read as "binds to a type the specification
    # does not name" (review of 2026-10-31).
    from cognalys.llm.source_facts import governed_api_types  # local: llm imports extraction

    expected = list(dict.fromkeys([*qualified_type_names(" ".join(wording)),
                                   *governed_api_types(specification)]))
    bytecode_only = [
        {key: item.get(key) for key in (
            "name", "arity", "static_owner", "qualified_type", "resolution")}
        for name in governed if name not in names
        for item in grouped.get(name, [])
    ]
    extra = {"bytecode_only_calls": bytecode_only} if bytecode_only else {}
    if not names:
        detail = ("none of the calls the specification governs appears in "
                  "the reported file")
        if bytecode_only:
            detail += (
                "; the analysed APK's bytecode of the reported method does "
                "call them, so the source may reach them through syntax that "
                "is not a call (a property access compiled to a getter or "
                "setter)"
            )
        return {"status": "no_governed_call", "expected_types": expected,
                **extra, "detail": detail}
    declared_here = {path.stem for path in _source_files(repo_root)}
    bindings = resolve_call_bindings(
        source, names, declares=lambda name: name in declared_here,
        expected_types=expected,
    )
    if not bindings:
        return {"status": "no_governed_call", "expected_types": expected}
    if grouped:
        bindings = [
            item if item.get("resolution") in _SETTLED_BINDINGS
            else settle_from_bytecode(item, grouped)
            for item in bindings
        ]
    if any(item.get("resolution") not in _SETTLED_BINDINGS for item in bindings):
        status = "unresolved"
    elif expected and all(
        item.get("qualified_type") in expected for item in bindings
    ):
        status = "resolved_to_governed_type"
    else:
        status = "resolved_elsewhere"
    return {
        "status": status,
        "expected_types": expected,
        "bindings": bindings,
        **extra,
        "detail": {
            "resolved_to_governed_type": "every governed call in the file "
                                         "binds to a type the specification "
                                         "names",
            "resolved_elsewhere": "a governed call binds to a type the "
                                  "specification does not name; the rule may "
                                  "not apply to it",
            "unresolved": "at least one governed call could not be bound "
                          "from the file's imports and declarations",
        }[status],
    }


# Bytecode names the checkout never spells. A constructor is `<init>` and is
# called as `Name(` (Kotlin) or `new Name(` (Java); Kotlin compiles a function
# taking an inline value class to name-<hash> (Password Store's
# calculateTotp-Zyo9ksc), the open body of a suspend function to
# name$suspendImpl (Tasks' newClient$suspendImpl), an internal member to
# name$<module> (okio's digest$okio) and default arguments to name$default;
# javac names a lambda lambda$name$N. A search for the bytecode name matched
# none of them, and caller_contract then said "no file calls this method":
# Amethyst's MacInstance.<init> had 23 callers in the same packet.
_SYNTHETIC_OWNER = re.compile(r"\$\d+(?:\$|$)|\$lambda|\$\$ExternalSynthetic")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def source_level_name(
    method_name: str, declaring_type: str = "", declaring_path: str = "",
) -> tuple:
    """(the name source code spells, how it was derived); "" when there is none.

    A static initializer, the body of a lambda or anonymous class and an
    accessor the compiler generated have no call site in source, so they are
    reported as not searchable rather than searched for and not found.
    """
    name = str(method_name or "").strip()
    if not name:
        return "", "no_method_name"
    if name == "<clinit>":
        return "", "static_initializer"
    if name == "<init>":
        if not (declaring_type or declaring_path):
            return "", "constructor_of_unknown_class"
        # Imported here: pr_feedback reaches the generation modules, which
        # import this one.
        from cognalys.prs.pr_feedback import resolve_source_method_name

        simple = resolve_source_method_name(
            name, declaring_type or Path(declaring_path).stem, Path(declaring_path),
        )
        if _IDENTIFIER.fullmatch(simple or ""):
            return simple, "constructor"
        return "", "constructor_of_unknown_class"
    if _SYNTHETIC_OWNER.search(str(declaring_type or "")):
        return "", "lambda_or_anonymous_class_body"
    if name.startswith("access$") or name == "invokeSuspend":
        return "", "compiler_generated"
    rule = "direct"
    lambda_body = re.fullmatch(r"lambda\$([A-Za-z_]\w*)\$\d+", name)
    if lambda_body:
        name, rule = lambda_body.group(1), "lambda_outer_function"
    if "-" in name:
        name, rule = name.split("-", 1)[0], "kotlin_value_class_mangling"
    if "$" in name:
        name, _, suffix = name.partition("$")
        rule = (
            "kotlin_suspend_impl" if suffix == "suspendImpl"
            else "lambda_outer_function" if suffix.startswith("lambda")
            else "synthetic_suffix"
        )
    if not _IDENTIFIER.fullmatch(name):
        return "", "compiler_generated"
    return name, rule


# A comment, or a string literal that can hold what looks like one: an
# Android MIME type "image/*" read as the start of a block comment blanked
# everything up to the next Javadoc, callers included, and a URL's "//" the
# rest of its line. The same alternatives as semantic_validation's.
_COMMENT_OR_LITERAL = re.compile(
    r'"""[\s\S]*?"""'
    r'|//[^\n]*'
    r'|/\*[\s\S]*?\*/'
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'",
)


def _blank_comments(source: str, literals: bool = False) -> str:
    """`source` with comments (and, if asked, string literals) replaced by
    spaces; offsets and line numbers are kept."""
    characters = list(source)
    for match in _COMMENT_OR_LITERAL.finditer(source):
        if not literals and not match.group().startswith("/"):
            continue
        for index in range(match.start(), match.end()):
            if characters[index] != "\n":
                characters[index] = " "
    return "".join(characters)


def constructor_call_pattern(name: str, declaring_type: str = "", java: bool = False) -> "re.Pattern":
    """Calls of the constructor of class `name`, as Java or Kotlin writes them.

    Java writes `new (pkg.)(Outer.)Name<..>(`. Kotlin writes `Name(` and, for
    a nested class built from outside its outer class, `Outer.Name(` (a
    sealed type's `Result.Success(`): a qualifier is accepted only when it
    ends in one of the class's outer names or its package, so an unrelated
    `receiver.Name(` call is not taken for it.
    """
    escaped = re.escape(name)
    type_arguments = r"(?:<[^<>()]{0,120}>)?"
    if java:
        return re.compile(rf"\bnew\s+(?:[\w$]+\s*\.\s*)*{escaped}\s*{type_arguments}\s*\(")
    parts = str(declaring_type or "").split("$")
    package, _, outer = parts[0].rpartition(".")
    qualifiers = [item for item in (outer, *parts[1:-1], package.rpartition(".")[2])
                  if _IDENTIFIER.fullmatch(item) and item != name]
    qualifier = (
        rf"(?:(?:\w+\s*\.\s*)*(?:{'|'.join(re.escape(item) for item in qualifiers)})\s*\.\s*)?"
        if qualifiers else ""
    )
    return re.compile(rf"(?<![\w.$@]){qualifier}{escaped}\s*{type_arguments}\s*\(")


def _call_pattern(name: str, constructor: bool, path: Path, declaring_type: str = "") -> "re.Pattern":
    """Calls of `name` as a file in `path`'s language writes them."""
    if not constructor:
        return re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
    return constructor_call_pattern(name, declaring_type, java=path.suffix == ".java")


def _declares(line: str, name: str, constructor: bool) -> bool:
    """Whether `line` declares `name` rather than calling it."""
    if constructor:
        return bool(re.search(
            rf"\b(?:class|object|interface|fun)\s+(?:[\w$?]+\.)?{re.escape(name)}\b", line))
    from cognalys.extraction.multi_file_context import _declaration_like

    return (_declaration_like(line, name)
            and not re.match(r"\s*(?:else|return|throw|do|try)\b", line))


# Where a declaration's header ends: its body's `{`, an expression body's `=`
# or Java's `;`. A Kotlin abstract or interface member has none of them, and
# the look-ahead must stop at the next member instead of taking that
# member's body (or a following class's) for its own.
_HEADER_END = re.compile(
    r"(?:(?!\n\s*(?:@|\}|(?:(?:public|private|protected|internal|override|abstract|open"
    r"|final|suspend|inline|operator|infix|static|synchronized|native|default|external"
    r"|tailrec|data|sealed|enum|companion|lateinit|const|annotation|inner)\s+)*"
    r"(?:fun|class|object|interface|val|var|init|constructor|typealias)\b))[^{=;])*([{=;])")


def _parameter_list(text: str, start: int, name: str) -> tuple:
    """(normalized parameter text, arity, index of its `)`) of the first
    `name(` header at or after `start`; ("", -1, -1) without one."""
    found = re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(").search(text, start)
    close = _matching_close(text, found.end() - 1, "(", ")") if found else -1
    if close < 0:
        return "", -1, -1
    inner = text[found.end():close]
    return (re.sub(r"\s+", "", inner),
            len([item for item in _top_level_arguments(inner) if item.strip()]), close)


def _declarations_in(lines: List[str], structure: str, name: str) -> List[Dict]:
    """Each declaration of `name` in a file: first and last line, parameters, arity.

    `structure` is the file with comments and literals blanked, so a brace in
    a string does not end a body early.
    """
    found = []
    offset = 0
    for number, line in enumerate(lines, 1):
        start, offset = offset, offset + len(line) + 1
        if not _declares(line, name, False):
            continue
        parameters, arity, close = _parameter_list(structure, start, name)
        end_line = number
        after = _HEADER_END.match(structure, close + 1) if close > 0 else None
        if after and after.group(1) == "{":
            body_end = _matching_close(structure, after.start(1), "{", "}")
            if body_end > 0:
                end_line = structure.count("\n", 0, body_end) + 1
        found.append({"first": number, "last": end_line,
                      "parameters": parameters, "arity": arity})
    return found


def _own_and_overload_lines(
    lines: List[str], structure: str, name: str, method_body: str = "",
) -> tuple:
    """(lines of the reported declaration, lines of other same-named ones, arity).

    A call inside the method's own body is recursion, not a caller. A call
    inside a same-named overload is often a caller -- `encrypt(String)`
    delegating to `encrypt(byte[])` -- so only the declaration the reported
    body matches (its parameters, else its unique arity) is excluded. When
    none can be told apart, nothing is excluded and the calls inside every
    overload are kept as unresolved same_name_overload sites.
    """
    declarations = _declarations_in(lines, structure, name)
    reported, arity = None, -1
    if len(declarations) == 1:
        reported = declarations[0]
    elif declarations and method_body:
        body = _blank_comments(str(method_body), literals=True)
        parameters, arity, _ = _parameter_list(body, 0, name)
        exact = [item for item in declarations if parameters and item["parameters"] == parameters]
        by_arity = [item for item in declarations if arity >= 0 and item["arity"] == arity]
        reported = (exact[0] if len(exact) == 1
                    else by_arity[0] if len(by_arity) == 1 else None)
    own, overloads = set(), set()
    for item in declarations:
        span = set(range(item["first"], item["last"] + 1))
        if item is reported:
            own |= span
        else:
            overloads |= span
    # A call in another overload resolves to the reported one only when no
    # other declaration shares the reported arity.
    unique_arity = (reported["arity"] if reported is not None and sum(
        1 for item in declarations if item["arity"] == reported["arity"]) == 1 else -1)
    return own, overloads - own, unique_arity


def _constructor_resolution(source: str, declaring_type: str) -> str:
    """Whether a file's `Name(` names the declaring class or only its simple name."""
    outer = str(declaring_type or "").split("$")[0]
    package = outer.rsplit(".", 1)[0] if "." in outer else ""
    if not package:
        return "unresolved"
    own = re.search(r"(?m)^\s*package\s+([\w.]+)", source)
    if own and own.group(1) == package:
        return "repository_type"
    if re.search(rf"(?m)^\s*import\s+(?:{re.escape(outer)}\b|{re.escape(package)}\.\*)", source):
        return "repository_type"
    return "unresolved"


_RESOLVED_CALLS = frozenset({
    "unique_external", "repository_type", "single_wildcard_import",
    "consistent_with_reported_api", "declaring_file",
})
MAX_CALL_SITES_PER_FILE = 12


def caller_contract(
    repo_root: Path, method_name: str, declaring_path: str = "",
    declaring_type: str = "", method_body: str = "",
) -> Dict:
    """Every call of `method_name` in the checkout, with its receiver resolved.

    The declaring file is searched too, without the declaration and the
    method's own body: a private helper's callers are all there. Skipping it,
    the record said "no callers" for YAPM's SecretService, papercript's
    SecretCodec and smsecure's MasterSecretUtil while the same packet listed
    their same-file callers. `method_body`, when given, tells the reported
    declaration from same-named overloads. The count of files is exact for the files
    scanned, and the scan is bounded; when a bound is reached the record says
    so instead of presenting a partial list as the whole contract.
    """
    repo_root = Path(repo_root)
    if not repo_root.is_dir() or not str(method_name or "").strip():
        return {"status": "unresolved",
                "detail": "no checkout or no method name to look for"}
    name, resolution = source_level_name(method_name, declaring_type, declaring_path)
    if not name:
        return {
            "status": "not_searchable", "name_resolution": resolution,
            "detail": "the reported name has no call site in source ("
                      + resolution.replace("_", " ") + "), so its callers "
                      "could not be searched for; this is not evidence that "
                      "it has none",
        }
    constructor = resolution == "constructor"
    callers = []
    scanned = 0
    truncated = False
    sources = _source_files(repo_root)
    # The declaring file first, so the file bound never drops it.
    sources.sort(key=lambda path: path.relative_to(repo_root).as_posix() != declaring_path)
    searched_declaring_file = bool(declaring_path) and bool(sources) and (
        sources[0].relative_to(repo_root).as_posix() == declaring_path
    )
    # Type names this checkout declares, so a caller's receiver resolves to a
    # repository type instead of being reported as unresolved for want of an
    # import it does not need.
    declared_here = {path.stem for path in sources}
    for path in sources:
        relative = path.relative_to(repo_root).as_posix()
        call = _call_pattern(name, constructor, path, declaring_type)
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not call.search(source):
            continue
        source = _blank_comments(source)
        lines = source.splitlines()
        same_file = relative == declaring_path
        excluded, overloads, unique_arity = (
            _own_and_overload_lines(lines, _blank_comments(source, literals=True),
                                    name, method_body)
            if same_file and not constructor else (set(), set(), -1))
        sites = []
        if constructor:
            for match in call.finditer(source):
                number = source.count("\n", 0, match.start()) + 1
                if _declares(lines[number - 1], name, True):
                    continue
                close = _matching_close(source, match.end() - 1, "(", ")")
                arguments = _top_level_arguments(source[match.end():close]) if close > 0 else []
                sites.append({
                    "line": number, "receiver": "",
                    "resolution": "declaring_file" if same_file
                    else _constructor_resolution(source, declaring_type),
                    "qualified_type": str(declaring_type or "").replace("$", "."),
                    "arity": len([item for item in arguments if item.strip()]),
                })
        else:
            for binding in resolve_call_bindings(
                source, [name],
                declares=lambda simple: simple in declared_here,
                max_sites=MAX_CALL_SITES_PER_FILE * 4,
                expected_types=[declaring_type] if declaring_type else (),
                constructors=False,
            ):
                line = binding["line"]
                if line in excluded or _declares(lines[line - 1], name, False):
                    continue
                site_resolution = binding.get("resolution", "unresolved")
                if same_file and binding.get("receiver_kind") == "implicit":
                    # No receiver in the declaring file: the enclosing type.
                    site_resolution = "declaring_file"
                if line in overloads and binding.get("arity") != unique_arity:
                    # Inside another overload of the same name: a caller of
                    # the reported one, or of itself.
                    site_resolution = "same_name_overload"
                sites.append({
                    "line": line,
                    "receiver": binding.get("receiver", ""),
                    "resolution": site_resolution,
                    "qualified_type": binding.get("qualified_type", ""),
                    "arity": binding.get("arity"),
                })
        if not sites:
            continue
        scanned += 1
        if scanned > MAX_CALLER_FILES:
            truncated = True
            break
        caller = {"source_file_path": relative,
                  "call_sites": sites[:MAX_CALL_SITES_PER_FILE]}
        if same_file:
            caller["declaring_file"] = True
        if len(sites) > MAX_CALL_SITES_PER_FILE:
            caller["call_sites_truncated"] = True
        callers.append(caller)
    resolved = sum(
        1 for caller in callers for site in caller["call_sites"]
        if site["resolution"] in _RESOLVED_CALLS
    )
    total = sum(len(caller["call_sites"]) for caller in callers)
    # What was searched, so a reader -- and the blocker discharge -- can tell
    # "searched and found none" from "could not search where they would be".
    searched = {
        "searched_name": name, "name_resolution": resolution,
        "searched_declaring_file": searched_declaring_file,
    }
    if not callers:
        return {
            "status": "no_callers_found", "files_with_calls": 0, **searched,
            "detail": (
                f"no file in the checkout, the declaring file included, calls "
                f"`{name}`" if searched_declaring_file else
                f"no file in the checkout calls `{name}`; the declaring file "
                f"was not identified, so calls inside it were not searched"
            ) + ", so no caller contract constrains the change",
        }
    return {
        "status": "callers_truncated" if truncated else "callers_enumerated",
        "files_with_calls": len(callers),
        "call_sites": total,
        "same_file_call_sites": sum(
            len(caller["call_sites"]) for caller in callers
            if caller.get("declaring_file")),
        "receiver_resolved_call_sites": resolved,
        **searched,
        "callers": callers,
        "detail": (
            "the scan stopped at the file bound, so this is a lower bound on "
            "the callers" if truncated else
            f"every file in the checkout that calls `{name}` is listed"
        ) + (f", at most {MAX_CALL_SITES_PER_FILE} call sites per file"
             if any(caller.get("call_sites_truncated") for caller in callers) else ""),
    }


def summarise(records: Iterable[Dict]) -> Dict:
    """Statuses of a set of records, for a prompt section or a log line."""
    statuses: Dict[str, int] = {}
    for record in records:
        status = str((record or {}).get("status", "unknown"))
        statuses[status] = statuses.get(status, 0) + 1
    return statuses


# --------------------------------------------------------------------------
# Input flow: does text a user can edit reach a parser with nothing to catch it
# --------------------------------------------------------------------------
#
# A finding that says "this parse throws on invalid input" is confirmed by two
# things the adjudicator kept reporting as unestablished: that invalid input is
# feasible, and that nothing handles the exception. Both are readable from the
# reviewed method. An editable text widget can always yield an empty string, and
# no `inputType` prevents that or a value beyond the parser's range. Whether a
# `try`/`catch` encloses the call is lexical; and a call inside a listener the
# framework invokes has no caller in the repository that could catch for it.
#
# The rule names no API family and no specification: it is a parse call whose
# argument is read from a widget type ending in `EditText`. What it cannot
# decide -- a TextWatcher or InputFilter that validates elsewhere -- it says.

_PARSE_CALL = re.compile(
    r"\b(?P<api>(?:Integer|Long|Short|Byte|Double|Float|BigDecimal|BigInteger)"
    r"\s*\.\s*(?:parse\w+|valueOf)|new\s+(?:BigDecimal|BigInteger))\s*\("
)
_KOTLIN_PARSE = re.compile(
    r"(?P<receiver>[\w$.]+?)\s*(?:\.\s*getText\s*\(\s*\)|\.\s*text)\s*"
    r"(?:\.\s*toString\s*\(\s*\))?\s*\.\s*(?P<api>toInt|toLong|toShort|toByte|toDouble|toFloat"
    r"|toBigDecimal|toBigInteger)\s*\(\s*\)"
)
_EDITABLE_READ = re.compile(
    r"(?P<receiver>[A-Za-z_$][\w$]*)\s*(?:\.\s*getText\s*\(\s*\)|\.\s*text\b"
    r"|\.\s*getEditableText\s*\(\s*\))"
)
_HANDLED_EXCEPTIONS = frozenset({
    "NumberFormatException", "IllegalArgumentException", "RuntimeException",
    "Exception", "Throwable",
})
_CALLBACK_OPENER = re.compile(
    r"(?:\bset\w*Listener|\badd\w*Listener|\bset\w*Callback|\brunOnUiThread|\bpost(?:Delayed)?)"
    r"\s*(?:\(\s*(?:new\s+[\w.<>]+\s*\(\s*\)\s*)?[^{;]*)?$"
    r"|\bon[A-Z]\w*\s*\([^)]*\)\s*(?:throws\s+[\w.,\s]+)?\s*$"
    r"|\bfun\s+on[A-Z]\w*\s*\([^)]*\)\s*(?::\s*[\w.<>?]+\s*)?$"
)


def _editable_widget_names(source: str) -> set:
    """Names declared with a widget type ending in EditText, in this source."""
    names = set()
    # `EditText a, b, c;` declares three widgets, not one.
    for match in re.finditer(
            r"\b[\w$.]*EditText\s+(?P<names>[A-Za-z_$][\w$]*(?:\s*,\s*[A-Za-z_$][\w$]*)*)",
            source):
        names.update(re.findall(r"[A-Za-z_$][\w$]*", match.group("names")))
    for match in re.finditer(
            r"\b(?P<name>[A-Za-z_$][\w$]*)\s*(?::\s*[\w$.]*EditText\b"
            r"|=\s*[^;\n]*<[\w$.]*EditText>)", source):
        names.add(match.group("name"))
    return names


def _matching_close(text: str, open_index: int, opener: str, closer: str) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _guarded_spans(blanked: str) -> list:
    """Spans of `try` bodies whose catch clauses handle a parse failure."""
    spans = []
    for match in re.finditer(r"\btry\s*\{", blanked):
        start = match.end() - 1
        end = _matching_close(blanked, start, "{", "}")
        if end < 0:
            continue
        cursor, handled = end + 1, False
        while True:
            catch = re.match(r"\s*catch\s*\(([^)]*)\)\s*\{", blanked[cursor:])
            if not catch:
                break
            caught = re.findall(r"[A-Za-z_$][\w$.]*", catch.group(1))
            if any(name.split(".")[-1] in _HANDLED_EXCEPTIONS for name in caught):
                handled = True
            cursor += catch.end() - 1
            close = _matching_close(blanked, cursor, "{", "}")
            if close < 0:
                break
            cursor = close + 1
        if handled:
            spans.append((start, end))
    return spans


def _in_deferred_callback(blanked: str, position: int) -> bool:
    """Whether `position` lies in a body the framework, not a caller, invokes."""
    depth_stack = []
    for index in range(position):
        if blanked[index] == "{":
            depth_stack.append(index)
        elif blanked[index] == "}" and depth_stack:
            depth_stack.pop()
    for opener in depth_stack:
        if _CALLBACK_OPENER.search(blanked[max(0, opener - 240):opener]):
            return True
    return False


def user_input_parse_flow(method_body: str, file_source: str = "",
                          repo_root: "Path | None" = None) -> Dict:
    """Editable widget text reaching a parser, and whether anything catches it."""
    body = str(method_body or "")
    if not body.strip():
        return {"status": "no_method_body", "parse_calls": []}
    from cognalys.solutions.signature_change import blank_comments_and_strings

    blanked = blank_comments_and_strings(body)
    widgets = _editable_widget_names(
        blank_comments_and_strings(str(file_source or "")) + "\n" + blanked
    )
    guarded = _guarded_spans(blanked)
    calls = []
    for match in _PARSE_CALL.finditer(blanked):
        open_index = match.end() - 1
        close = _matching_close(blanked, open_index, "(", ")")
        if close < 0:
            continue
        argument = blanked[open_index + 1:close]
        read = _EDITABLE_READ.search(argument)
        if not read or read.group("receiver") not in widgets:
            continue
        calls.append((match.start(), match.group("api"), read.group("receiver")))
    for match in _KOTLIN_PARSE.finditer(blanked):
        receiver = match.group("receiver").split(".")[-1]
        if receiver in widgets:
            calls.append((match.start(), match.group("api"), receiver))
    records = []
    for position, api, widget in sorted(calls):
        line = blanked.count("\n", 0, position) + 1
        records.append({
            "api": re.sub(r"\s+", "", api), "widget": widget, "line": line,
            "snippet": body.splitlines()[line - 1].strip()[:160],
            "enclosed_by_try_that_catches": any(s < position < e for s, e in guarded),
            "in_framework_callback": _in_deferred_callback(blanked, position),
        })
    unguarded = [item for item in records if not item["enclosed_by_try_that_catches"]]
    validation = None
    if records and repo_root is not None:
        validation = repository_validation_of(
            Path(repo_root), str(file_source or ""), body,
            list(dict.fromkeys(item["widget"] for item in records)),
        )
    if not records:
        status = "no_editable_text_parse"
    elif unguarded:
        status = "editable_text_reaches_parser_unguarded"
    else:
        status = "editable_text_reaches_parser_guarded"
    return {
        "status": status,
        **({"validation_search": validation} if validation is not None else {}),
        "parse_calls": records[:12],
        "unguarded_in_framework_callback": sum(
            1 for item in unguarded if item["in_framework_callback"]),
        "bound": (
            "Read from the reviewed method's text. An editable text widget can "
            "always yield an empty string, and a numeric inputType prevents "
            "neither that nor a value beyond the parser's range, so invalid "
            "input is feasible for a parse call fed straight from one. "
            "enclosed_by_try_that_catches is lexical: no try in this method "
            "catches a parse failure at that call. in_framework_callback means "
            "the call sits in a listener or on* callback the framework "
            "invokes, so no caller in the repository can handle its "
            "exception. validation_search, when present, is what a search of "
            "the repository's Java/Kotlin sources and layouts found for these "
            "widgets: validation calls that name them (watchers, filters, "
            "setError), an emptiness check in the method, the layout "
            "attributes of their view ids, the helpers they are passed to, and "
            "any global exception handler. no_validation_found_for_any_widget "
            "means none exists in the repository, so an empty field reaches the "
            "parse; widget_passed_to_helpers_that_may_validate and "
            "validation_found_for say what remains open. It does not read code "
            "generated at build time."
        ),
    }


# What the repository does about those widgets. The adjudicator asked for proof
# that no layout attribute, filter, watcher or enclosing handler excludes invalid
# input on every reachable path. A negative cannot be proved, but a search can
# say what exists: the validation calls that name the widget, the layout
# attributes of its view id, the helpers it is handed to, and any global
# exception handler. An empty result is then a stated fact and not a silence.

_VALIDATION_CALL = (
    r"addTextChangedListener|setFilters|setError|doAfterTextChanged|doOnTextChanged"
    r"|doBeforeTextChanged|setKeyListener|setOnEditorActionListener|setInputType"
)
_GLOBAL_HANDLER = (
    r"setDefaultUncaughtExceptionHandler|UncaughtExceptionHandler|ACRA\.init|Crashlytics"
    r"|Thread\.setDefault"
)
_LAYOUT_ATTRIBUTES = ("inputType", "maxLength", "digits", "editable")
_MAX_LOCATIONS = 3


def _scan_sources(repo_root: Path, pattern: str, needles: Iterable[str]) -> List[str]:
    """`path:line: text` for Java/Kotlin lines matching an extended regex.

    `grep` when available; otherwise a walk that reads only files containing a
    needle. Returns [] when neither works: an unsearched repository must not
    read as an empty one, so the caller records `searched: false` separately.
    """
    import subprocess

    command = ["grep", "-rnE", "--include=*.kt", "--include=*.java"]
    command += [f"--exclude-dir={name}" for name in ("build", ".gradle", ".git", "node_modules")]
    command += ["-e", pattern, str(repo_root)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if completed.returncode in (0, 1):
            found = []
            for line in completed.stdout.splitlines():
                try:
                    relative = Path(line.split(":", 1)[0]).relative_to(repo_root).as_posix()
                except ValueError:
                    relative = line.split(":", 1)[0]
                found.append(relative + ":" + line.split(":", 1)[1])
            return found
    except (OSError, subprocess.SubprocessError):
        pass
    compiled = re.compile(pattern)
    found = []
    for path in _source_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needles and not any(needle in text for needle in needles):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if compiled.search(line):
                found.append(f"{path.relative_to(repo_root).as_posix()}:{number}: {line.strip()}")
    return found


def _layout_attributes(repo_root: Path, view_id: str) -> Dict:
    """The attributes of the element declaring `@+id/<view_id>` in any layout."""
    element = re.compile(
        r"<[A-Za-z_.$][\w.$]*[^<>]*?android:id\s*=\s*\"@\+id/" + re.escape(view_id) + r"\"[^<>]*>",
        re.DOTALL,
    )
    for path in sorted(repo_root.glob("**/res/layout*/*.xml"))[:400]:
        if any(part in {"build", ".gradle", ".git"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = element.search(text)
        if not match:
            continue
        attributes = {
            name: found.group(1)
            for name in _LAYOUT_ATTRIBUTES
            for found in [re.search(rf"android:{name}\s*=\s*\"([^\"]*)\"", match.group(0))]
            if found
        }
        return {"layout": path.relative_to(repo_root).as_posix(), "attributes": attributes}
    return {}


def _view_id(source: str, widget: str) -> str:
    match = re.search(
        rf"\b{re.escape(widget)}\s*=\s*(?:\([^)]*\)\s*)?(?:[\w$.]+\.)?findViewById\s*"
        r"(?:<[^>]*>)?\s*\(\s*R\.id\.(\w+)",
        source,
    )
    return match.group(1) if match else ""


def repository_validation_of(repo_root: Path, file_source: str, body: str,
                             widgets: List[str]) -> Dict:
    """Validation, helpers, layout attributes and global handlers for the widgets."""
    from cognalys.solutions.signature_change import blank_comments_and_strings

    names = "|".join(sorted({re.escape(name) for name in widgets}))
    if not names:
        return {"searched": False, "reason": "no_widgets"}
    try:
        validation = _scan_sources(
            repo_root,
            rf"\b({names})[[:space:]]*\.[[:space:]]*({_VALIDATION_CALL})\b"
            rf"|\b({names})[[:space:]]*\.[[:space:]]*filters[[:space:]]*=",
            widgets,
        )
        helpers = _scan_sources(
            repo_root, rf"[(,][[:space:]]*({names})[[:space:]]*[,)]", widgets,
        )
        handlers = _scan_sources(repo_root, _GLOBAL_HANDLER, ["Handler", "ACRA", "Crashlytics", "Thread"])
    except Exception as exc:  # noqa: BLE001 -- recorded, never fatal
        return {"searched": False, "reason": str(exc)[:120]}
    combined = blank_comments_and_strings(file_source)
    blanked_body = blank_comments_and_strings(body)
    per_widget = {}
    for widget in dict.fromkeys(widgets):
        own = re.compile(rf"\b{re.escape(widget)}\b")
        checked_in_method = bool(re.search(
            rf"\b{re.escape(widget)}\s*(?:\.\s*getText\s*\(\s*\)|\.\s*text)[^;\n]*?"
            r"(?:\.\s*(?:isEmpty|isBlank|isNullOrEmpty|isNullOrBlank|isNotEmpty|isNotBlank"
            r"|length\s*\(\s*\)|length)\b)"
            rf"|TextUtils\s*\.\s*isEmpty\s*\([^)]*\b{re.escape(widget)}\b",
            blanked_body,
        ))
        view = _view_id(combined, widget)
        per_widget[widget] = {
            "view_id": view,
            **({"layout": _layout_attributes(repo_root, view)} if view else {}),
            "validation_calls": [item for item in validation if own.search(item.split(":", 2)[-1])][:_MAX_LOCATIONS],
            "passed_to_helpers": [
                item for item in helpers
                if re.search(
                    rf"\w\s*\([^;]*\b{re.escape(widget)}\b\s*[,)]",
                    item.split(":", 2)[-1],
                ) and not re.search(r"EditText|findViewById", item.split(":", 2)[-1])
            ][:_MAX_LOCATIONS],
            "emptiness_check_in_method": checked_in_method,
        }
    with_validation = [
        name for name, item in per_widget.items()
        if item["validation_calls"] or item["emptiness_check_in_method"]
    ]
    with_helpers = [name for name, item in per_widget.items() if item["passed_to_helpers"]]
    if with_validation:
        conclusion = "validation_found_for: " + ", ".join(with_validation)
    elif with_helpers:
        conclusion = "widget_passed_to_helpers_that_may_validate: " + ", ".join(with_helpers)
    else:
        conclusion = "no_validation_found_for_any_widget"
    return {
        "searched": True,
        "widgets": per_widget,
        "application_level_exception_handlers": handlers[:_MAX_LOCATIONS],
        "conclusion": conclusion,
    }


# ---------------------------------------------------------------------------
# Where the bytes given to a key, IV or salt constructor come from.
# ---------------------------------------------------------------------------

# Constructors that take byte material, and the position of that argument.
_BYTE_MATERIAL_ARGUMENT = {"IvParameterSpec": 0, "GCMParameterSpec": 1,
                           "SecretKeySpec": 0, "PBEKeySpec": 1}
_BYTE_MATERIAL_CALL = re.compile(
    r"\b(?:new\s+)?(?P<api>IvParameterSpec|GCMParameterSpec|SecretKeySpec|PBEKeySpec)\s*\(")
_ZERO_ARRAY = re.compile(r"^(?:ByteArray\s*\(\s*[\w.]+\s*\)|new\s+byte\s*\[\s*[\w.]+\s*\])$")
_LITERAL_ARRAY = re.compile(
    r"^(?:byteArrayOf\s*\(|new\s+byte\s*\[\s*\]\s*\{|ByteArray\s*\([^)]*\)\s*\{|"
    r"\"[^\"]*\"\s*\.\s*(?:toByteArray|getBytes|encodeToByteArray)\s*\()")
_DECLARATION = r"(?:\b(?:val|var)\s+{name}\b(?:\s*:\s*ByteArray\??)?|\bbyte\s*\[\s*\]\s+{name}\b)\s*=\s*(?P<init>[^;\n]+)"
_WRITES = (r"nextBytes\s*\(\s*{name}\s*[,)]", r"\b{name}\s*\[[^\]]+\]\s*=[^=]",
           r"arraycopy\s*\([^;]*,\s*{name}\s*,", r"\.copyInto\s*\(\s*{name}\b",
           r"\.(?:read|readFully|get)\s*\(\s*{name}\s*[,)]", r"\b{name}\s*\.\s*fill\s*\(")
BYTE_MATERIAL_MEANINGS = {
    "fresh_zero_filled": "a newly allocated array, all zero bytes, that nothing in the "
                         "method or its file writes before it is passed",
    "constant_literal": "bytes written out in the source (a byte array literal or a "
                        "string literal's bytes)",
    "filled_by_nextBytes": "an array a nextBytes call fills before it is passed",
    "written_after_creation": "a new array the code writes before passing it; what it "
                              "writes is not settled here",
    "not_settled": "the source alone does not settle where these bytes come from",
}


def _top_level_arguments(text: str) -> List[str]:
    """Split call arguments at the commas outside any brackets."""
    parts, depth, current = [], 0, []
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


def _classify_material(argument: str, scopes: List[str]) -> str:
    argument = argument.strip()
    if _ZERO_ARRAY.match(argument):
        return "fresh_zero_filled"
    if _LITERAL_ARRAY.match(argument):
        return "constant_literal"
    if not re.fullmatch(r"[A-Za-z_]\w*", argument):
        return "not_settled"
    for scope in scopes:
        declared = re.search(_DECLARATION.format(name=re.escape(argument)), scope)
        if not declared:
            continue
        initializer = declared.group("init").strip()
        if "nextBytes" in initializer:
            return "filled_by_nextBytes"
        if _LITERAL_ARRAY.match(initializer):
            return "constant_literal"
        if _ZERO_ARRAY.match(initializer):
            written = any(re.search(pattern.format(name=re.escape(argument)), text)
                          for pattern in _WRITES for text in scopes)
            return "written_after_creation" if written else "fresh_zero_filled"
        return "not_settled"
    return "not_settled"


def byte_material(method_body: str, file_source: str = "") -> Dict:
    """For each key/IV/salt constructor in the method, where its bytes come from.

    PaperCript's SecretCodec.aesCtr passes IvParameterSpec(ByteArray(16)) to
    an AES-CTR Cipher. The adjudicator confirmed the cause under one
    specification and, for the same line under IvParameterSpecSpec, called
    it unknown because the specification does not define "randomized". That
    sixteen zero bytes are not random needs no definition; it needs the fact
    stated. Only the method and its own file are read, so a value built
    elsewhere is not_settled, never guessed.
    """
    body = str(method_body or "")
    if not body.strip():
        return {"status": "no_method_body", "uses": []}
    from cognalys.solutions.signature_change import blank_comments_and_strings

    # Comments go; string literals stay, since their bytes can be the material.
    without_comments = re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: " " * len(m.group()), body, flags=re.S)
    blanked = blank_comments_and_strings(body)
    scopes = [without_comments,
              re.sub(r"/\*.*?\*/|//[^\n]*", "", str(file_source or ""), flags=re.S)]
    uses = []
    for match in _BYTE_MATERIAL_CALL.finditer(blanked):
        open_index = match.end() - 1
        close = _matching_close(blanked, open_index, "(", ")")
        if close < 0:
            continue
        arguments = _top_level_arguments(without_comments[open_index + 1:close])
        position = _BYTE_MATERIAL_ARGUMENT[match.group("api")]
        if len(arguments) <= position:
            continue
        line = blanked.count("\n", 0, match.start()) + 1
        material = _classify_material(arguments[position], scopes)
        uses.append({
            "api": match.group("api"), "argument": arguments[position][:120],
            "line": line, "material": material,
            "meaning": BYTE_MATERIAL_MEANINGS[material],
            "snippet": body.splitlines()[line - 1].strip()[:160],
        })
    return {"status": "collected" if uses else "no_byte_material_constructor", "uses": uses}


_PARAMETER_PRODUCER_SITES = 12
_CAST = re.compile(r"^\(\s*[\w.<>?\[\]]+\s*\)\s*|\s+as\??\s+[\w.<>?]+$")


def _parameter_names(method_body: str) -> List[str]:
    """Names of the method's parameters, Java (`Type name`) or Kotlin (`name: Type`)."""
    blanked = re.sub(r"/\*.*?\*/|//[^\n]*", "", method_body, flags=re.S)
    open_index = blanked.find("(")
    close = _matching_close(blanked, open_index, "(", ")") if open_index >= 0 else -1
    if close < 0:
        return []
    names = []
    for parameter in _top_level_arguments(blanked[open_index + 1:close]):
        # Kotlin constructor properties carry a visibility (`private val key`).
        parameter = re.sub(r"@\w+(?:\([^)]*\))?\s*|\b(?:final|vararg|val|var|private|protected"
                           r"|internal|public|override|open)\s+", "", parameter)
        parameter = parameter.split("=", 1)[0].strip()
        kotlin = re.match(r"^([A-Za-z_]\w*)\s*:", parameter)
        java = re.search(r"([A-Za-z_]\w*)\s*$", parameter)
        name = kotlin.group(1) if kotlin else (java.group(1) if java else "")
        if name:
            names.append(name)
    return names


def _assignment_before(source: str, name: str, position: int) -> Optional[re.Match]:
    pattern = re.compile(rf"(?<![\w.]){re.escape(name)}\s*(?::\s*[\w.<>?]+\s*)?=(?!=)\s*(?P<init>[^;\n]+)")
    last = None
    for match in pattern.finditer(source, 0, position):
        last = match
    return last


# The algorithm a key is made for (JCA: what key.getAlgorithm() returns), and
# the algorithm of the object a method uses it with.
_KEY_ALGORITHM = re.compile(
    r"\b(?:KeyGenerator|SecretKeyFactory|KeyPairGenerator)\s*\.\s*getInstance\s*\(\s*\"(?P<algorithm>[^\"]+)\""
    r"|\bSecretKeySpec\s*\([^;,]*,\s*\"(?P<spec_algorithm>[^\"]+)\"\s*\)")
_USED_WITH = re.compile(r"\b(?:Cipher|Mac|Signature)\s*\.\s*getInstance\s*\(\s*\"(?P<transformation>[^\"]+)\"")


def _returns_of(source: str, function: str) -> List[str]:
    """The return expressions of a method declared in `source`, at most three."""
    header = re.search(
        rf"(?:\bfun\s+|[\w<>\[\]?]+\s+){re.escape(function)}\s*\([^)]*\)[^{{;=]*(?P<open>[{{=])",
        source)
    if not header:
        return []
    if header.group("open") == "=":
        end = source.find("\n", header.end())
        return [source[header.end():end if end >= 0 else len(source)].strip()[:160]]
    close = _matching_close(source, header.end() - 1, "{", "}")
    body = source[header.end():close if close > 0 else len(source)]
    return [match.group(1).strip()[:160]
            for match in re.finditer(r"\breturn\s+([^;\n]+)", body)][:3]


def _declaring_source(repo_root: Path, declaring_path: str) -> str:
    if not declaring_path:
        return ""
    try:
        return (Path(repo_root) / declaring_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _only_match(pattern: "re.Pattern", text: str, start: int = 0,
                end: Optional[int] = None) -> Optional[re.Match]:
    """The match of `pattern` in `text[start:end]` when there is exactly one."""
    found = list(pattern.finditer(text, start, len(text) if end is None else end))
    return found[0] if len(found) == 1 else None


def _constructor_declaration(method_body: str, file_source: str, name: str) -> str:
    """The constructor from its parameter list to the end of what it governs.

    The reported body when it starts with the constructor's header (Java
    `Name(...) {`, Kotlin `class Name(...)` or `constructor(...)`), otherwise
    the declaring file's primary constructor, a `constructor(...)` inside
    that class's own braces, or its only Java constructor. A Kotlin primary
    constructor's parameters are used by the class body and the supertype
    call, so both are kept. With several secondary or Java constructors and a
    body that names none, "" rather than one taken at random: the first
    `constructor(` of the file can belong to another class.
    """
    escaped = re.escape(name)
    kotlin_primary = re.compile(
        rf"\bclass\s+{escaped}\b\s*(?:<[^{{(]*?>)?\s*(?:@\w+\s*)*"
        rf"(?:(?:public|private|protected|internal)\s+)?(?:constructor\s*)?(?=\()")
    secondary = re.compile(r"\bconstructor\s*(?=\()")
    java = re.compile(
        rf"(?m)^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:public|protected|private)\s+)?{escaped}\s*(?=\()")
    body = str(method_body or "")
    head = body[:body.find("{")] if "{" in body else body
    match = (kotlin_primary.search(head) or secondary.search(head) or java.search(head)
             or re.match(rf"\s*(?<![\w.$]){escaped}\s*(?=\()", head))
    text = body
    if not match:
        text = _blank_comments(file_source)
        match = kotlin_primary.search(text) or _only_match(java, text)
        declared = None if match else re.search(rf"\b(?:class|object)\s+{escaped}\b", text)
        if declared:
            structure = _blank_comments(text, literals=True)
            brace = re.compile(r"(?:(?!\b(?:class|fun|object|interface)\b)[^{;])*\{").match(
                structure, declared.end())
            end = _matching_close(structure, brace.end() - 1, "{", "}") if brace else -1
            if end > 0:
                match = _only_match(secondary, text, brace.end(), end)
        if not match:
            return ""
    text = text[match.end():]
    close = _matching_close(text, 0, "(", ")")
    if close < 0:
        return ""
    # The body's brace, not one of a declaration after a body-less class.
    brace = re.match(r"(?:(?!\b(?:class|fun|object|interface|val|var)\b)[^{;])*\{",
                     text[close + 1:])
    if brace:
        end = _matching_close(text, close + brace.end(), "{", "}")
        return text[:end + 1] if end > 0 else text
    line_end = text.find("\n", close)
    return text[:line_end if line_end >= 0 else len(text)]


def parameter_producers(
    repo_root: Path, method_body: str, method_name: str,
    declaring_type: str = "", declaring_path: str = "",
) -> Dict:
    """Where each caller's value comes from, for parameters the method passes on.

    FreeOTP's EncryptedKey.encrypt(key, plaintext) only forwards `key` to
    Cipher.init. The adjudicator could not tell which caller supplied the key
    that broke the rule and asked for a trace; the two callers were in the
    checkout: Encryptor passes `mKeyStore.getKey(...)`, MasterKey passes a
    PBKDF2 key straight from `skf.generateSecret(spec)`. For each parameter
    the method passes to a call, this lists what every call site in the
    checkout passes and, up to two assignments back, the expression that
    produced it. A value it cannot follow is not_settled, never guessed.
    """
    body = str(method_body or "")
    repo_root = Path(repo_root)
    name, resolution = source_level_name(method_name, declaring_type, declaring_path)
    if not body.strip() or not name or not repo_root.is_dir():
        return {"status": "not_applicable", "parameters": []}
    constructor = resolution == "constructor"
    if constructor:
        # Where the key or IV bytes enter a wrapper: Amethyst's
        # MacInstance(algorithm, key) is a Kotlin primary constructor.
        body = _constructor_declaration(
            body, _declaring_source(repo_root, declaring_path), name)
        if not body:
            return {"status": "not_applicable", "parameters": []}
        close = _matching_close(body, body.find("("), "(", ")")
        inner = body[close + 1:] if close >= 0 else ""
    else:
        open_brace = body.find("{")
        inner = body[open_brace + 1:] if open_brace >= 0 else ""
    parameters = _parameter_names(body)
    forwarded = {}
    for index, parameter in enumerate(parameters):
        uses = [
            line.strip()[:160] for line in inner.splitlines()
            if re.search(rf"\b(?!(?:if|while|for|when|switch|catch|synchronized)\b)\w+\s*\("
                         rf"[^;]*(?<![\w.]){re.escape(parameter)}\b(?!\s*\.)", line)
        ]
        if uses:
            forwarded[parameter] = (index, uses[:3])
    if not forwarded:
        return {"status": "no_forwarded_parameter", "parameters": []}
    declaration = re.compile(
        rf"\b(?:fun|void|static|public|private|protected|internal|override)\b[^;{{=]*?(?<![\w.]){re.escape(name)}\s*\(")
    records = {parameter: [] for parameter in forwarded}
    files = 0
    for path in _source_files(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        if "test" in relative.lower():
            continue
        call = _call_pattern(name, constructor, path, declaring_type)
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not call.search(source):
            continue
        files += 1
        if files > MAX_CALLER_FILES:
            break
        source = _blank_comments(source)
        for match in call.finditer(source):
            line_start = source.rfind("\n", 0, match.start()) + 1
            if declaration.search(source[line_start:match.end()]):
                continue
            line_end = source.find("\n", match.end())
            if constructor and _declares(
                    source[line_start:line_end if line_end >= 0 else len(source)], name, True):
                continue
            close = _matching_close(source, match.end() - 1, "(", ")")
            if close < 0:
                continue
            arguments = _top_level_arguments(source[match.end():close])
            for parameter, (index, _) in forwarded.items():
                if index >= len(arguments) or len(records[parameter]) >= _PARAMETER_PRODUCER_SITES:
                    continue
                argument = _CAST.sub("", arguments[index].strip()).strip()
                site = {"source_file_path": relative,
                        "line": source.count("\n", 0, match.start()) + 1,
                        "argument": argument[:160]}
                chain, current, position = [], argument, match.start()
                for _ in range(2):
                    if not re.fullmatch(r"[A-Za-z_]\w*", current):
                        break
                    assigned = _assignment_before(source, current, position)
                    if not assigned:
                        break
                    produced = _CAST.sub("", assigned.group("init").strip()).strip()
                    step = {"value": current, "produced_by": produced[:160],
                            "line": source.count("\n", 0, assigned.start()) + 1}
                    local = re.match(r"^([A-Za-z_]\w*)\s*\(", produced)
                    if local:
                        # A helper of the same file: what it returns names
                        # the factory and its algorithm (FreeOTP's
                        # getSecretKeyFactory -> getInstance("PBKDF2...")).
                        returned = _returns_of(source, local.group(1))
                        if returned:
                            step["which_returns"] = returned
                    chain.append(step)
                    receiver = re.match(r"^([A-Za-z_]\w*)\s*\.", produced)
                    current, position = (receiver.group(1), assigned.start()) if receiver else ("", 0)
                site["producers"] = chain
                made_as = list(dict.fromkeys(
                    found.group("algorithm") or found.group("spec_algorithm")
                    for step in chain
                    for text in [step["produced_by"], *step.get("which_returns", [])]
                    for found in _KEY_ALGORITHM.finditer(text)))
                if made_as:
                    site["key_algorithms"] = made_as
                site["status"] = "settled" if chain or not re.fullmatch(r"[A-Za-z_]\w*", argument) else "not_settled"
                records[parameter].append(site)
    used_with = [match.group("transformation") for match in _USED_WITH.finditer(inner)]
    return {
        "status": "collected" if any(records.values()) else "no_call_sites",
        # A key whose key_algorithms do not name the algorithm the method uses
        # it with was made for something else (FreeOTP: a PBKDF2 key into an
        # AES/GCM Cipher).
        **({"used_with": used_with} if used_with else {}),
        "parameters": [
            {"parameter": parameter, "index": index, "passed_to": uses,
             "call_sites": records[parameter]}
            for parameter, (index, uses) in forwarded.items()
        ],
        "bound": ("call sites outside test sources, at most "
                  f"{MAX_CALLER_FILES} files; producers follow plain assignments "
                  "in the caller's file only"),
    }
