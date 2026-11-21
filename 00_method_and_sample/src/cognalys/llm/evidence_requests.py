#!/usr/bin/env python3
"""Satisfy the evidence the extractor asks for, when the repository holds it.

The v2 extractor ends every finding by declaring `needs_more_evidence` and
listing four to six `requested_evidence` items naming exactly what it is
missing. Across the 40 adjudicated findings in the recorded runs that flag was
true 40 times out of 40, and nothing ever read either field: they appeared
only in the response schema and in one default value. The model stated its
own blocking gap, in writing, once per finding, and the pipeline discarded it.

Most of those requests genuinely cannot be answered -- 102 of 193 ask to
observe a running program (`SSLSession.getProtocol()`, handshake telemetry, a
runtime trace), and no amount of source reading substitutes for that. But 46
name something the checkout already contains: the source of a named type, the
build and dependency files, the tests for a symbol. Ten of the nineteen
NO_SAFE_FIX findings asked for at least one of those.

This module classifies the requests and answers the answerable ones. It never
invents evidence: a request is either satisfied with real file content or
declined with a recorded reason, so the extraction that follows can still
conclude that something is unknown -- but not because nobody looked.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional


# A request is answerable from a checkout when it asks for something the
# checkout contains. These are matched against the request's declared kind,
# which the extractor writes as free text, so the patterns are deliberately
# broad and the runtime family is tested first: "runtime_configuration_
# evidence" asks to observe a process, not to read a config file.
_RUNTIME_REQUEST = re.compile(
    r"runtime|handshake|telemetry|session|negotiat|analyzer|detector"
    r"|instrumentation",
    re.IGNORECASE,
)
# Words that also name a source question. Tested first, they declined
# `callers_or_byte_provenance` and `call_site_or_provenance_trace` (185 of 480
# requests declined as runtime in the 2026-10 runs; 12 asked for callers,
# producers or a definition) and the ownership question
# `dependency_provenance`. They classify as runtime only when no other
# family matches.
_WEAK_RUNTIME_REQUEST = re.compile(r"provenance|trace|observ", re.IGNORECASE)
# "Where does this value come from?" -- the producers of what the method is
# given, answered with the callers and what each one passes.
_PRODUCER_REQUEST = re.compile(r"produc(?:er|ers|ed[_\s-]*by|ing)", re.IGNORECASE)
# An Android resource: `R.string.default_rsa_len`, `@array/key_sizes`, or a
# request of kind `resource`. Droid_SCEP's key size lived in res/, and both
# requests for it were declined as "no matching file in the repository".
_RESOURCE_TYPES = (
    "string", "integer", "bool", "array", "string-array", "integer-array",
    "dimen", "color", "plurals", "fraction", "style", "id", "xml", "raw",
    "layout", "menu", "drawable", "mipmap", "navigation", "anim", "font",
)
_RESOURCE_TYPE = "|".join(
    re.escape(name) for name in sorted(_RESOURCE_TYPES, key=len, reverse=True)
)
_RESOURCE_REFERENCE = re.compile(
    rf"\bR\s*\.\s*(?P<type>{_RESOURCE_TYPE})\s*\.\s*(?P<name>[A-Za-z_]\w*)"
    rf"|@\+?(?P<at_type>{_RESOURCE_TYPE})/(?P<at_name>[A-Za-z_][\w.]*)"
)
_RESOURCE_REQUEST = re.compile(r"(?<![A-Za-z])resources?(?![A-Za-z])", re.IGNORECASE)
_SOURCE_REQUEST = re.compile(
    r"source|bytecode|implementation|call_?graph|usage_graph|semantic",
    re.IGNORECASE,
)
_TEST_REQUEST = re.compile(r"test", re.IGNORECASE)
_BUILD_REQUEST = re.compile(
    r"depend|lockfile|build|version|manifest|gradle", re.IGNORECASE,
)
# "Which API does this call actually bind to?" No repository file answers
# it when the API is a platform or library one, which is why these requests
# were declined as "no matching file in the repository" in four runs. They
# are answered from the file's imports and the receiver's declaration
# instead -- see extraction.symbol_binding.
# Deliberately narrow: "resolved_dependency_graph_or_lockfile" is a build
# request, not a question about what a call binds to.
# Matched on the shape of the question, not on one spelling of it: the
# extractor has asked for `symbol_resolution` and, in run_20260920_175850,
# for `semantic_callee_resolution` -- which matched none of the earlier
# alternatives, fell to the source family on the word "semantic", and was
# declined as "no matching file in the repository" again.
_BINDING_REQUEST = re.compile(
    r"(?:symbol|callee|call|invocation|method|target|api|signature|type"
    r"|receiver|binding)[_\s-]*(?:resolution|binding|confirmation)"
    r"|overload|binding|receiver|resolves?[_\s-]*to"
    r"|resolv\w*[_\s-]*(?:callee|call|invocation|target)"
    r"|which\s+api",
    re.IGNORECASE,
)
# "Which artifact ships this class, at which version?" -- asked as
# `unresolved ownership and version of the reported class` and as `target
# implementation unavailable`, and answered from the module's own declared
# coordinates rather than by fetching a library.
_OWNERSHIP_REQUEST = re.compile(
    r"ownership|owning[_\s-]*artifact|which[_\s-]*artifact|coordinate"
    r"|provenance[_\s-]*of[_\s-]*the[_\s-]*class|declared[_\s-]*version",
    re.IGNORECASE,
)
# "Does the reported rule actually govern this code?" -- answered from the
# specification's own api_patterns, quoted back with its condition text.
_APPLICABILITY_REQUEST = re.compile(
    r"applicab|governs?|rule[_\s-]*scope|specification[_\s-]*(?:scope|applies)"
    r"|reported[_\s-]*rule",
    re.IGNORECASE,
)
# "What do the callers require of this method?" -- answered by enumerating
# them with their receivers resolved.
_NAMED_SOURCE_PATH = re.compile(
    r"[\w./-]+\.(?:java|kt|kts|gradle|toml|xml|properties|lockfile)\b",
    re.IGNORECASE,
)
_CALLER_REQUEST = re.compile(
    r"caller|call[_\s-]*sites?|reachab|consumer[_\s-]*contract"
    r"|usage[_\s-]*of[_\s-]*the[_\s-]*method|call[_\s-]*inventory",
    re.IGNORECASE,
)

RUNTIME_ONLY = "runtime_only"
REPOSITORY_SOURCE = "repository_source"
REPOSITORY_TESTS = "repository_tests"
BUILD_CONFIGURATION = "build_configuration"
SYMBOL_BINDING = "symbol_binding"
ARTIFACT_OWNERSHIP = "artifact_ownership"
RULE_APPLICABILITY = "rule_applicability"
CALLER_CONTRACT = "caller_contract"
ANDROID_RESOURCE = "android_resource"
UNCLASSIFIED = "unclassified"

# Families answered with a derived record rather than with a file. Each is a
# question no repository file answers on its own, and each was previously
# declined -- which the adjudicator then recorded as an unconfirmed cause.
DERIVED_FAMILIES = frozenset({
    SYMBOL_BINDING, ARTIFACT_OWNERSHIP, RULE_APPLICABILITY, CALLER_CONTRACT,
})

# Why a derived family could not be answered at all. These say what was
# missing from the finding, not "no matching file in the repository", which
# was true of every one of them and useful for none.
_DERIVED_DECLINE = {
    SYMBOL_BINDING: "no call of the requested name was found in the reported "
                    "source file",
    ARTIFACT_OWNERSHIP: "the finding names no class whose ownership could be "
                        "looked up",
    RULE_APPLICABILITY: "the finding carries no specification context, so "
                        "what the rule governs is not stated anywhere",
    CALLER_CONTRACT: "the finding names no method whose callers could be "
                     "enumerated",
}

_BUILD_FILE_NAMES = (
    "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts",
    "gradle.properties", "gradle/libs.versions.toml",
    "AndroidManifest.xml",
)
_SKIPPED_PARTS = {".git", "build", ".gradle", "out", "node_modules"}

# A supplementary entry must not crowd out the packet it supplements. These
# caps are per-entry and overall; anything longer is truncated and the entry
# says so, rather than being silently shortened.
MAX_ENTRY_CHARACTERS = 24_000
MAX_TOTAL_CHARACTERS = 90_000
MAX_ENTRIES = 8
# Without a per-request cap the first greedy request wins everything: a
# single dependency-graph request matches five build files and leaves no
# room for the source and test requests behind it.
MAX_ENTRIES_PER_REQUEST = 3


def _names_declared_code(request: Dict, index: Optional["RepositoryIndex"]) -> bool:
    """Whether a request names a member or type the checkout declares."""
    if index is None:
        return False
    if re.search(r"\b[A-Z][\w$]*\s*[.#]\s*<init>", str(request.get("symbol", ""))):
        return True
    return (any(declares_member(index, member) for member in requested_member_names(request)[:4])
            or any(index.declaring_path(name) is not None
                   for name in requested_type_names(request)[:4]))


def classify_evidence_request(request: Dict, index: Optional["RepositoryIndex"] = None) -> str:
    """Say which family of evidence a request belongs to.

    Runtime is tested first on purpose: several requests name a
    configuration or a source but ask for the value a running process
    actually used, which reading the file cannot establish. A question about
    producers is a caller question only when it names code the checkout
    declares, which takes the checkout's `index`.
    """
    text = " ".join(
        str(request.get(field, "")) for field in ("kind", "type", "symbol")
    )
    if _RUNTIME_REQUEST.search(text):
        return RUNTIME_ONLY
    # A resource reference is a value declared under res/; the file it is
    # named after (fragment_scep.xml) is fetched with it.
    if _RESOURCE_REFERENCE.search(text):
        return ANDROID_RESOURCE
    # A request that spells out a file is answered with that file, whatever
    # else its wording resembles. `callsite_lifecycle_context` naming
    # extensions/Context.kt reads as a caller-contract question and is not
    # one: it asks for a source body the checkout has.
    if _NAMED_SOURCE_PATH.search(text):
        return REPOSITORY_SOURCE
    # After the runtime family, so "runtime resolution of the negotiated
    # protocol" stays a runtime observation, and before the source family,
    # so "which API does this call bind to" is answered with a binding
    # record rather than with the file that was already supplied.
    if _BINDING_REQUEST.search(text):
        return SYMBOL_BINDING
    if _OWNERSHIP_REQUEST.search(text):
        return ARTIFACT_OWNERSHIP
    if _APPLICABILITY_REQUEST.search(text):
        return RULE_APPLICABILITY
    if _CALLER_REQUEST.search(text):
        return CALLER_CONTRACT
    # Producers of code the checkout has: "installed_crysl_rules_and_
    # predicate_producers" asks what a specification defines, and a caller
    # list labelled as satisfying it read as the answer.
    if _PRODUCER_REQUEST.search(text) and _names_declared_code(request, index):
        return CALLER_CONTRACT
    # The bare word only after the caller family: in "callers of Enc.close
    # releasing resources" the resources are streams.
    if _RESOURCE_REQUEST.search(text):
        return ANDROID_RESOURCE
    if _TEST_REQUEST.search(text):
        return REPOSITORY_TESTS
    if _SOURCE_REQUEST.search(text):
        return REPOSITORY_SOURCE
    if _BUILD_REQUEST.search(text):
        return BUILD_CONFIGURATION
    if _WEAK_RUNTIME_REQUEST.search(text):
        return RUNTIME_ONLY
    return UNCLASSIFIED


def asks_for_value_origin(request: Dict) -> bool:
    """Whether a request asks for a running program or for where a value comes from.

    These words declined a request before the runtime family was split; a
    caller list or a binding record does not settle them, so the blocker
    discharge reads this rather than the family.
    """
    text = " ".join(
        str(request.get(field, "")) for field in ("kind", "type", "symbol")
    )
    return bool(_RUNTIME_REQUEST.search(text) or _WEAK_RUNTIME_REQUEST.search(text)
                or _PRODUCER_REQUEST.search(text))


def requested_type_names(request: Dict) -> List[str]:
    """Type names a request's `symbol` field points at.

    A symbol is written for a human -- "at.bitfire.cert4android.
    CustomCertManager and CustomCertManager.hostnameVerifier(...)" -- so the
    qualified tail and every capitalised identifier are both candidates. The
    declaration lookup discards whatever names nothing.
    """
    symbol = str(request.get("symbol", ""))
    names = []
    for qualified in re.findall(r"\b(?:[a-z][\w$]*\.)+([A-Z][\w$]*)", symbol):
        names.append(qualified)
    names.extend(re.findall(r"\b([A-Z][A-Za-z0-9_$]*)\b", symbol))
    seen, ordered = set(), []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


# Java and Kotlin modifiers that may precede a type declaration.
_DECLARATION_MODIFIERS = (
    r"(?:public|protected|private|internal|abstract|final|static|strictfp|sealed|non-sealed"
    r"|open|data|enum|annotation|inner|value|inline|expect|actual|fun|companion)")


def _looks_like_test_source(path: Path) -> bool:
    """The rule test_sources uses: a test directory, or "test" in the file name."""
    parts = {part.lower() for part in path.parts}
    return bool({"test", "androidtest"} & parts) or "test" in path.stem.lower()


class RepositoryIndex:
    """One walk of the checkout, reused for every request.

    Each lookup used to walk the tree again: a type resolution per candidate
    name, a build-file glob per filename, and a full scan for tests. With
    four to six requests naming several types each that is dozens of walks
    of a large Android checkout, which on a mounted filesystem dominates
    everything else the stage does. The walk happens once here instead.
    """

    _SOURCE_SUFFIXES = {".java", ".kt"}

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.sources: List[Path] = []
        self.by_name: Dict[str, List[Path]] = {}
        self.by_stem: Dict[str, List[Path]] = {}
        for path in repo_root.rglob("*"):
            if any(part in _SKIPPED_PARTS for part in path.parts):
                continue
            if not path.is_file():
                continue
            self.by_name.setdefault(path.name, []).append(path)
            if path.suffix.lower() in self._SOURCE_SUFFIXES:
                self.sources.append(path)
                self.by_stem.setdefault(path.stem, []).append(path)

    def text(self, path: Path) -> str:
        """A source file's text, read once: a reference search reads every
        source for each member it looks up, which on a mounted checkout is
        most of the stage's time."""
        cache = self.__dict__.setdefault("_texts", {})
        if path not in cache:
            try:
                cache[path] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                cache[path] = ""
        return cache[path]

    def resource_files(self) -> List[Path]:
        """Every file under an Android `res/` directory, in path order."""
        cache = self.__dict__.get("_resources")
        if cache is None:
            cache = sorted(
                path for paths in self.by_name.values() for path in paths
                if path.parent.parent.name == "res"
            )
            self.__dict__["_resources"] = cache
        return cache

    def build_files(self) -> List[Path]:
        found = []
        for name in _BUILD_FILE_NAMES:
            found.extend(self.by_name.get(Path(name).name, []))
        return found

    def declaring_path(self, type_name: str) -> Optional[Path]:
        """File declaring `type_name`, including a nested type in its outer file.

        The file named after the type (or its outer type) first. Kotlin
        declares top-level types in files of any name, and so may Java a
        non-public one: InterTune's `object WidgetCodec` is in
        WidgetSnapshot.kt, and the request for WidgetCodec was declined "no
        matching file in the repository" (run_k3v9, run_k3v10). When no file
        of that name declares it, every source is searched, main sources
        before test sources and each in path order, so the answer does not
        depend on the walk's order; the answer is kept, since the index is
        reused for every request.
        """
        parts = type_name.split(".")
        simple, outer = parts[-1], parts[0]
        pattern = re.compile(
            rf"\b(?:class|interface|enum|record|object)\s+{re.escape(simple)}\b"
        )
        for stem in (outer, simple):
            for path in self.by_stem.get(stem, []):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if pattern.search(text):
                    return path
        return self._declaring_path_by_content(simple)

    def _declaring_path_by_content(self, simple: str) -> Optional[Path]:
        # A top-level declaration only, at the start of a line after its
        # modifiers. The file-name lookup above can afford "object X" anywhere
        # in the type's own file; across every source, a comment or a string
        # ("an object The ...") would answer a word of prose, and a nested
        # namesake in an unrelated file would answer another type (Olvid's
        # PeliasReverseResponse.Engine for io.olvid.engine.engine.Engine).
        cache = self.__dict__.setdefault("_declarations", {})
        if simple not in cache:
            declaration = re.compile(
                rf"^(?:@\w+(?:\([^)\n]*\))?[ \t]+)*(?:{_DECLARATION_MODIFIERS}[ \t]+)*"
                rf"(?:class|interface|enum|record|object|@interface)[ \t]+"
                rf"{re.escape(simple)}\b", re.MULTILINE)
            ordered = sorted(self.sources, key=lambda path: (
                _looks_like_test_source(path), path.as_posix()))
            cache[simple] = next(
                (path for path in ordered
                 if simple in self.text(path) and declaration.search(self.text(path))),
                None)
        return cache[simple]

    def test_sources(self, names: List[str], limit: int) -> List[Path]:
        if not names:
            return []
        pattern = re.compile(
            "|".join(rf"\b{re.escape(name)}\b" for name in names)
        )
        found = []
        for path in self.sources:
            parts = {part.lower() for part in path.parts}
            if not ({"test", "androidtest"} & parts) and "test" not in path.stem.lower():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if pattern.search(text):
                found.append(path)
            if len(found) >= limit:
                break
        return found


# Tokens are matched with one flat character class and filtered afterwards:
# a nested "(dir/)+file.ext" pattern backtracks super-linearly on long text.
_PATH_TOKEN = re.compile(r"[\w./-]+")
_PATH_SUFFIXES = (".kt", ".java", ".kts", ".gradle", ".xml", ".toml", ".properties")


def mentioned_repository_paths(
    request: Dict, repo_root: Path, index: Optional["RepositoryIndex"] = None,
) -> List[Path]:
    """Repository files a request names by path, or by a file name only one file has.

    Gallery's extraction asked for "app/src/main/kotlin/org/fossify/gallery/
    extensions/Context.kt GetMediaAsynctask call site". The kind matched no
    family and the only type name in it was GetMediaAsynctask, whose file was
    already supplied, so the request was declined as "no matching file in the
    repository" -- for a file it spelled out. Droid_SCEP's asked for
    "layout fragment_scep.xml" and "app_restrictions.xml" without a directory
    and was declined the same way; a bare name is resolved when exactly one
    file in the checkout has it.
    """
    text = " ".join(
        str(request.get(field, "")) for field in ("symbol", "reason", "kind", "type")
    )
    found: List[Path] = []
    tokens = (token.strip("./") for token in _PATH_TOKEN.findall(text))
    for raw in dict.fromkeys(
        token for token in tokens if token.endswith(_PATH_SUFFIXES)
    ):
        if "/" not in raw:
            same_name = index.by_name.get(raw, []) if index is not None else []
            if len(same_name) == 1:
                found.append(same_name[0])
            continue
        candidate = repo_root / raw
        try:
            candidate.resolve().relative_to(repo_root.resolve())
        except (ValueError, OSError):
            continue
        if candidate.is_file():
            found.append(candidate)
    return list(dict.fromkeys(found))


# Member names a request is about: `Helper.getFingerprint(Context, String)`,
# `Helper#getFingerprint`, `MainViewModel.random`, `getDbFilename()`.
_MEMBER_AFTER_TYPE = re.compile(r"\b[A-Z][\w$]*\s*[.#]\s*([a-z_][\w$]{2,})\b")
_CALLED_MEMBER = re.compile(r"\b([a-z_][\w$]{2,})\s*\(")
_NOT_MEMBERS = {
    "and", "the", "for", "any", "all", "its", "from", "with", "into", "that",
    "this", "return", "value", "values", "outputs", "output", "new", "call",
    "calls", "used", "uses", "use", "data", "file", "files", "path", "paths",
}
MAX_REFERENCE_SITES = 40


def requested_member_names(request: Dict) -> List[str]:
    """Methods or fields a request's `symbol` names, in order, without repeats."""
    symbol = str(request.get("symbol", ""))
    names = _MEMBER_AFTER_TYPE.findall(symbol) + _CALLED_MEMBER.findall(symbol)
    return [name for name in dict.fromkeys(names) if name.lower() not in _NOT_MEMBERS]


def _member_declaration(member: str) -> "re.Pattern":
    return re.compile(
        rf"(?:\bfun\s+(?:[\w<>?.]+\.)?|\b(?:val|var)\s+(?:[\w<>?.]+\.)?|[\w<>\[\]?]\s+)"
        rf"{re.escape(member)}\s*[(=:;{{]")


def declares_member(index: "RepositoryIndex", member: str, path: Optional[Path] = None) -> bool:
    """Whether the checkout (or the one file `path`) declares a method or field `member`."""
    declaration = _member_declaration(member)
    return any(member in index.text(candidate) and declaration.search(index.text(candidate))
               for candidate in ([path] if path is not None else index.sources))


def reference_sites(index: "RepositoryIndex", member: str, limit: int = MAX_REFERENCE_SITES) -> List[str]:
    """`path:line: code` for every source line that mentions `member`, with one line around it.

    What most declined requests asked for (exp_final_20261019: 217 of 584
    declined as "no matching file in the repository") was not a file but
    the places a member is used -- who reads getFingerprint's result, every
    use of MainViewModel.random, the consumers of getDbFilename -- while the
    declaring file was already in the packet.
    """
    pattern = re.compile(rf"(?<![\w$]){re.escape(member)}(?![\w$])")
    # Only a member the checkout declares: a word of the request's prose
    # ("string", "entries") matched 40 unrelated lines in the replay of
    # exp_final_20261019.
    if not declares_member(index, member):
        return []
    sites: List[str] = []
    for path in index.sources:
        text = index.text(path)
        if member not in text:
            continue
        lines = text.splitlines()
        relative = path.relative_to(index.repo_root).as_posix()
        for number, line in enumerate(lines, 1):
            if not pattern.search(line):
                continue
            around = lines[max(0, number - 2):number + 1]
            sites.append(f"{relative}:{number}:\n" + "\n".join(
                f"    {text.rstrip()}" for text in around))
            if len(sites) >= limit:
                return sites
    return sites


def _entry(kind: str, relative: str, content: str, request: Dict) -> Dict:
    truncated = len(content) > MAX_ENTRY_CHARACTERS
    return {
        "kind": kind,
        "trust_level": "repository_source",
        "mandatory": False,
        "data": {
            "source_file_path": relative,
            "content": content[:MAX_ENTRY_CHARACTERS],
            "truncated": truncated,
            "satisfies_request": {
                "kind": str(request.get("kind", "")),
                "symbol": str(request.get("symbol", "")),
            },
        },
    }


def _binding_entry(repo_root: Path, target: Dict, request: Dict,
                   index: RepositoryIndex) -> Optional[Dict]:
    """A binding record for the call the request asks about, or None.

    `target` describes the reported finding: the file it was raised in and
    the message that names the API. Nothing here reads a model's opinion --
    the record is computed from that file's own imports and declarations.
    """

    from cognalys.extraction.symbol_binding import (  # local: keeps this module's import cheap
        DERIVATION_NOTE, called_names_in, qualified_type_names,
        resolve_call_bindings,
    )

    relative = str(target.get("source_file_path", ""))
    if not relative:
        return None
    path = repo_root / relative
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    asked = " ".join(
        str(request.get(field, "")) for field in ("symbol", "reason", "kind")
    )
    # The names to resolve: those the request spells out, falling back to the
    # ones the detector's own message names. Only names this file actually
    # calls are looked up, so an unrelated mention costs nothing.
    names = [
        name for name in
        called_names_in(asked) or called_names_in(str(target.get("error_message", "")))
        if re.search(rf"\b{re.escape(name)}\s*\(", source)
    ]
    if not names:
        return None
    bindings = resolve_call_bindings(
        source, names,
        declares=lambda simple: index.declaring_path(simple) is not None,
        expected_types=qualified_type_names(
            asked + " " + str(target.get("error_message", ""))
        ),
    )
    if not bindings:
        return None
    return {
        "kind": "resolved_symbol_binding",
        "trust_level": "repository_source",
        "mandatory": False,
        "data": {
            "source_file_path": relative,
            "bindings": bindings,
            "derivation": DERIVATION_NOTE,
            "satisfies_request": {
                "kind": str(request.get("kind", "")),
                "symbol": str(request.get("symbol", "")),
            },
        },
    }


def runtime_entry(target: Dict, request: Dict) -> Optional[Dict]:
    """What the dynamic analysis observed, for a request only a running app answers.

    Of 584 requests in exp_final_20261019, 302 were declined as "requires
    observing a running program" -- for findings a running program had
    produced. RVSec records the value it saw and what the rule expected, the
    explorers and runs that reported it, and (attach_coverage) every method
    the exploration executed. That answers part of those requests: whether
    a named class or method ran, and what value reached the governed call.
    None when the finding carries no runtime record.
    """
    context = target.get("detector_context") or {}
    observed = [(label, str(context.get(key, "")).strip()) for label, key in (
        ("label", "label"), ("value seen", "value"), ("values the rule admits", "expected"),
        ("explorers that reported it", "tools"), ("source location", "source_location"),
        ("what the runs observed", "runtime_observation"),
    )]
    observed = [(label, value) for label, value in observed if value]
    coverage_file = str(context.get("runtime_coverage_file", "")).strip()
    executed = {}
    if coverage_file:
        from cognalys.integrations.rvsec import runtime_coverage

        executed = runtime_coverage(coverage_file, str(target.get("apk_name", "")))
    # The value, expectation and run observation already reach the packet as
    # the detector-context entry (evidence_packet); what is new here is the
    # coverage, so without it there is nothing to add.
    if not executed:
        return None
    lines = ["What the dynamic analysis (RVSec) observed while exploring the app:"]
    lines += [f"- {label}: {value}" for label, value in observed]
    if executed:
        reported_class = str(target.get("csv_class", "")).strip()
        names = [*requested_type_names(request), *requested_member_names(request)]
        lines.append("")
        lines.append("Methods executed during exploration (absence means not reached by "
                     "the explorer in this run, not that the code never runs):")
        for tool, methods in sorted(executed.items()):
            in_class = sorted(m for c, m in methods if reported_class and c == reported_class)
            lines.append(f"- {tool}: {len(methods)} method(s) executed"
                         + (f"; in {reported_class}: {', '.join(in_class[:15]) or 'none'}"
                            if reported_class else ""))
            for name in names[:6]:
                hits = sorted(f"{c}.{m}" for c, m in methods
                              if m == name or c == name or c.endswith("." + name)
                              or c.endswith("$" + name))
                lines.append(f"  - {name}: " + (", ".join(hits[:8]) + (" ..." if len(hits) > 8 else "")
                                                 if hits else "not executed"))
    entry = _entry("runtime_observation", "runtime:rvsec", "\n".join(lines), request)
    entry["trust_level"] = "runtime_trace"
    return entry


# The XML elements a reference of each value type is declared with.
_VALUE_ELEMENTS = {
    "string": ("string",), "integer": ("integer",), "bool": ("bool",),
    "array": ("array", "string-array", "integer-array"),
    "string-array": ("string-array",), "integer-array": ("integer-array",),
    "dimen": ("dimen",), "color": ("color",), "plurals": ("plurals",),
    "fraction": ("fraction",), "style": ("style",),
}
MAX_RESOURCE_DECLARATIONS = 24
MAX_RESOURCE_CHARACTERS = 1_500
RESOURCE_DERIVATION = (
    "Values declared under res/ in the checkout, read statically. They are "
    "what the build packages, not a value observed at run time: a qualified "
    "directory (values-xx, layout-land) applies only on matching devices, and "
    "code or a stored setting can supply something else."
)


def resource_references(text: str) -> List[tuple]:
    """(type, name) of every Android resource reference in `text`, in order."""
    found = [
        (match.group("type") or match.group("at_type"),
         match.group("name") or match.group("at_name"))
        for match in _RESOURCE_REFERENCE.finditer(text or "")
    ]
    return list(dict.fromkeys(found))


def _resource_declarations(index: RepositoryIndex, kind: str, name: str) -> List[Dict]:
    """Where res/ declares `kind`/`name`: value elements, the file, or the view with that id."""
    found: List[Dict] = []

    def record(path: Path, text: str) -> None:
        found.append({
            "reference": f"@{kind}/{name}",
            "source_file_path": path.relative_to(index.repo_root).as_posix(),
            "qualifier": path.parent.name,
            "declaration": text[:MAX_RESOURCE_CHARACTERS],
            "truncated": len(text) > MAX_RESOURCE_CHARACTERS,
        })

    escaped = re.escape(name)
    for path in index.resource_files():
        if len(found) >= MAX_RESOURCE_DECLARATIONS:
            break
        folder = path.parent.name
        if kind in _VALUE_ELEMENTS:
            if not folder.startswith("values") or path.suffix != ".xml":
                continue
            text = index.text(path)
            if name not in text:
                continue
            tags = "|".join(re.escape(tag) for tag in _VALUE_ELEMENTS[kind])
            for match in re.finditer(
                # Not `\b` after the tag: it falls inside `string-array`,
                # and a string sharing an array's name got the array.
                rf"<(?P<tag>{tags}|item)(?![\w-])[^>]*\bname\s*=\s*\"{escaped}\"[^>]*?(?:/>|>.*?</(?P=tag)\s*>)",
                text, re.S,
            ):
                if match.group("tag") == "item" and f'type="{kind}"' not in match.group():
                    continue
                record(path, match.group())
        elif kind == "id":
            text = index.text(path)
            for match in re.finditer(rf"@\+?id/{escaped}(?![\w.])", text):
                start = text.rfind("<", 0, match.start())
                end = text.find(">", match.end())
                element = text[max(0, start):end + 1 if end >= 0 else len(text)]
                record(path, element)
                # A spinner's or list's choices: android:entries="@array/x".
                for listed, entry in resource_references(element):
                    if listed in _VALUE_ELEMENTS and entry != name:
                        found.extend(_resource_declarations(index, listed, entry))
        elif folder.split("-", 1)[0] == kind and path.stem == name:
            record(path, index.text(path))
    return found[:MAX_RESOURCE_DECLARATIONS]


# A code identifier in a request's symbol: camelCase or snake_case, not a
# word of prose ("spinner", "values").
_CODE_IDENTIFIER = re.compile(r"(?<![\w$.@/])([a-z_]\w*?(?:[A-Z]|_[A-Za-z0-9])\w*)(?![\w$])")
MAX_WIDGET_IDENTIFIERS = 4
MAX_WIDGET_VIEW_IDS = 4


def _view_binding_pattern(identifier: str) -> "re.Pattern":
    """``identifier`` assigned a view: findViewById(R.id.X) or a binding's X.

    Java `spinner = v.findViewById(R.id.x)` or `(Spinner) findViewById(...)`,
    Kotlin `val spinner: Spinner = findViewById<Spinner>(R.id.x)` or
    `by lazy { ... }`, and view binding `spinner = binding.spinnerX`.
    """
    name = re.escape(identifier)
    return re.compile(
        rf"(?<![\w$.]){name}(?:\s*:\s*[\w.<>?]+)?\s*(?:=|by\s+lazy\s*\{{)[^;\n]{{0,160}}?"
        rf"(?:findViewById\s*(?:<[^>]*>)?\s*\(\s*(?:[\w.]+\.)?R\s*\.\s*id\s*\.\s*(?P<id>\w+)"
        rf"|\b\w*[bB]inding\s*!*\??\s*\.\s*(?P<binding>[A-Za-z_]\w*))")


def _binding_id_names(property_name: str) -> List[str]:
    """The layout ids a view-binding property can come from: as written and
    in snake_case (spinnerKeySize from @+id/spinner_key_size)."""
    snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", property_name).lower()
    return list(dict.fromkeys([property_name, snake]))


def widget_view_ids(index: RepositoryIndex, target: Dict, request: Dict) -> List[str]:
    """Layout ids of the views the code identifiers in a request's symbol hold.

    A request for "the spinner's available values" names the field, not
    the resource: Droid_SCEP's "spinnerKeyLen / R.string.default_rsa_len"
    (frozen run rerun_correcoes_20261022 and every comparison run) got the
    string and never the choices, because spinnerKeyLen is bound in
    ScepFragment (`v.findViewById(R.id.spinnerKeySize)`) and the choices are
    that view's android:entries="@array/rsa_lens"
    (_resource_declarations follows them from the id). The reported file
    is searched first, then every source; an identifier that is itself a
    layout id (view binding or synthetic access) counts too. Only ids some
    layout declares are returned.
    """
    identifiers = [name for name in dict.fromkeys(
        _CODE_IDENTIFIER.findall(str(request.get("symbol", ""))))][:MAX_WIDGET_IDENTIFIERS]
    if not identifiers:
        return []
    layouts = [path for path in index.resource_files()
               if path.suffix == ".xml" and path.parent.name.split("-", 1)[0] == "layout"]

    def declared(name: str) -> bool:
        pattern = re.compile(rf"@\+?id/{re.escape(name)}(?![\w.])")
        return any(pattern.search(index.text(path)) for path in layouts)

    reported = index.repo_root / str(target.get("source_file_path", "") or "")
    ordered = ([reported] if reported.is_file() else []) + [
        path for path in index.sources if path != reported]
    found: List[str] = []
    for identifier in identifiers:
        pattern = _view_binding_pattern(identifier)
        candidates: List[str] = []
        for path in ordered:
            text = index.text(path)
            if identifier not in text:
                continue
            for match in pattern.finditer(text):
                candidates.extend([match.group("id")] if match.group("id")
                                  else _binding_id_names(match.group("binding")))
        candidates.extend(_binding_id_names(identifier))
        found.extend(name for name in dict.fromkeys(candidates)
                     if name not in found and declared(name))
    return found[:MAX_WIDGET_VIEW_IDS]


def _resource_entry(index: RepositoryIndex, target: Dict, request: Dict,
                    budget: int = MAX_ENTRY_CHARACTERS) -> Optional[Dict]:
    """The res/ declarations of the resources a request names, or None.

    Referenced by the request; when it names neither a resource nor a type
    (a request of kind `resource` and nothing else), by the reported
    method's body. One naming a type is about that type: "resources" also
    means streams to close. Like every entry, the declarations stop at
    MAX_ENTRY_CHARACTERS and at the `budget` left, and say so.
    """
    asked = " ".join(
        str(request.get(field, "")) for field in ("symbol", "reason", "kind", "type")
    )
    references = resource_references(asked)
    # Whether the method body names the resources is decided by what the
    # request itself names, not by the views its fields resolve to: a
    # Spinner without android:entries, filled in the method by
    # createFromResource(ctx, R.array.x, ...), would otherwise lose the very
    # array a "keySpinner choices" request asks for. The views come first,
    # the method body's resources after them.
    fallback = (resource_references(str(target.get("method_body", "")))
                if not references and not requested_type_names(request) else [])
    references += [("id", name) for name in widget_view_ids(index, target, request)
                   if ("id", name) not in references]
    references += [reference for reference in fallback if reference not in references]
    found: List[Dict] = []
    for kind, name in references[:8]:
        found.extend(_resource_declarations(index, kind, name))
    room = min(MAX_ENTRY_CHARACTERS, budget)
    declarations: List[Dict] = []
    for declaration in found[:MAX_RESOURCE_DECLARATIONS]:
        room -= len(declaration["declaration"])
        if room < 0:
            break
        declarations.append(declaration)
    if not declarations:
        return None
    truncated = len(declarations) < len(found)
    return {
        "kind": "android_resource_values",
        "trust_level": "repository_source",
        "mandatory": False,
        "data": {
            "references": [f"@{kind}/{name}" for kind, name in references[:8]],
            "declarations": declarations,
            "truncated": truncated,
            "derivation": RESOURCE_DERIVATION,
            "satisfies_request": {
                "kind": str(request.get("kind", "")),
                "symbol": str(request.get("symbol", "")),
            },
        },
    }


def _qualified_name(index: RepositoryIndex, path: Path, type_name: str) -> str:
    package = re.search(r"(?m)^\s*package\s+([\w.]+)", index.text(path))
    return f"{package.group(1)}.{type_name}" if package else type_name


def _requested_callee(
    repo_root: Path, target: Dict, request: Dict,
    index: Optional[RepositoryIndex],
) -> tuple:
    """(method, declaring type, declaring path) whose callers a request asks for.

    The member the request names, when the checkout declares it; otherwise
    the reported method. Cryptomator's request for "BaseCipher.decrypt(byte[])
    callers" was answered with the callers of the reported
    getIvParameterSpec, labelled as satisfying it.
    """
    reported = (
        str(target.get("method_name", "")), str(target.get("csv_class", "")),
        str(target.get("source_file_path", "")),
    )
    if index is None:
        return reported
    text = " ".join(str(request.get(field, "")) for field in ("symbol", "kind"))
    constructor = re.search(r"\b([A-Z][\w$]*)\s*[.#]\s*<init>", text)
    if constructor:
        path = index.declaring_path(constructor.group(1))
        if path is not None:
            return ("<init>", _qualified_name(index, path, constructor.group(1)),
                    path.relative_to(repo_root).as_posix())
    reported_names = {reported[0], re.split(r"[$-]", reported[0])[0]}
    for member in requested_member_names(request):
        if member in reported_names:
            return reported
        if not declares_member(index, member):
            continue
        for type_name in requested_type_names(request):
            path = index.declaring_path(type_name)
            if path is not None and declares_member(index, member, path):
                return (member, _qualified_name(index, path, type_name),
                        path.relative_to(repo_root).as_posix())
        return member, "", ""
    return reported


def _derived_entry(
    repo_root: Path, target: Dict, request: Dict, family: str,
    index: Optional[RepositoryIndex] = None,
) -> Optional[Dict]:
    """A computed answer for one of the derived families, or None.

    Every record states its own status, including the statuses that mean
    "this could not be established". Returning None means the question did
    not apply to this finding at all, and the request is then declined with
    a reason rather than answered with an empty record.
    """

    from cognalys.extraction.evidence_facts import (
        artifact_ownership, caller_contract, parameter_producers,
        specification_applicability,
    )

    relative = str(target.get("source_file_path", ""))
    if family == ARTIFACT_OWNERSHIP:
        class_name = str(target.get("csv_class", "")) or str(
            request.get("symbol", "")
        ).split("#")[0]
        if not class_name:
            return None
        record = artifact_ownership(repo_root, class_name, relative)
        kind = "resolved_artifact_ownership"
    elif family == RULE_APPLICABILITY:
        record = specification_applicability(
            target.get("specification_context", {}),
            str(target.get("method_body", "")),
            str(target.get("error_message", "")),
        )
        if record.get("status") == "specification_unavailable":
            return None
        kind = "specification_applicability"
    elif family == CALLER_CONTRACT:
        method_name, declaring_type, declaring = _requested_callee(
            repo_root, target, request, index)
        if not method_name:
            return None
        record = caller_contract(
            repo_root, method_name, declaring, declaring_type,
            str(target.get("method_body", "")) if method_name == target.get("method_name") else "",
        )
        relative = declaring or relative
        kind = "resolved_caller_contract"
        # A question about where the values come from is answered with what
        # each caller passes, for the reported method whose body is known.
        if (asks_for_value_origin(request) and method_name == target.get("method_name")
                and target.get("method_body")):
            record = {**record, "parameter_producers": parameter_producers(
                repo_root, str(target.get("method_body", "")), method_name,
                str(target.get("csv_class", "")), relative,
            )}
    else:
        return None
    return {
        "kind": kind,
        "trust_level": "repository_source",
        "mandatory": False,
        "data": {
            "source_file_path": relative,
            "record": record,
            "satisfies_request": {
                "kind": str(request.get("kind", "")),
                "symbol": str(request.get("symbol", "")),
            },
        },
    }


def resolve_evidence_requests(
    repo_root: Path, requests: List[Dict], already_supplied: set,
    index: Optional[RepositoryIndex] = None,
    target: Optional[Dict] = None,
) -> Dict:
    """Fetch what the repository can answer; record why the rest is declined.

    Returns {"entries": [...], "declined": [...]} where every entry carries
    real file content and every declined request carries the reason it could
    not be answered, so the extraction that follows can tell "not found in
    the repository" from "nobody looked".
    """
    if not repo_root.is_dir():
        return {"entries": [], "declined": [
            {"request": request, "reason": "repository_not_available"}
            for request in requests
        ]}
    # Building the index walks the whole checkout, which on a mounted
    # filesystem costs seconds. When every request asks to observe a running
    # program there is nothing to look up, so the walk is skipped entirely.
    answerable = [
        request for request in requests
        if isinstance(request, dict)
        and (classify_evidence_request(request) != RUNTIME_ONLY
             or bool(_PRODUCER_REQUEST.search(" ".join(
                 str(request.get(field, "")) for field in ("kind", "type", "symbol")))))
    ]
    if not answerable:
        entries, declined = [], []
        for request in requests:
            if not isinstance(request, dict):
                continue
            entry = runtime_entry(target or {}, request)
            if entry is not None and len(entries) < MAX_ENTRIES:
                entries.append(entry)
            else:
                declined.append({"request": request,
                                 "reason": "requires observing a running program; "
                                           "not obtainable from the repository"})
        return {"entries": entries, "declined": declined}
    if index is None:
        index = RepositoryIndex(repo_root)

    entries: List[Dict] = []
    declined: List[Dict] = []
    supplied = set(already_supplied)
    budget = MAX_TOTAL_CHARACTERS
    taken_for_request = 0

    def add(kind: str, path: Path, request: Dict, focus: List[str] = ()) -> bool:
        nonlocal budget, taken_for_request
        try:
            relative = path.relative_to(repo_root).as_posix()
        except ValueError:
            return False
        if (relative in supplied or len(entries) >= MAX_ENTRIES
                or taken_for_request >= MAX_ENTRIES_PER_REQUEST
                or budget <= 0):
            return False
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        start_line = 1
        if len(content) > MAX_ENTRY_CHARACTERS and focus:
            # A long file is cut at MAX_ENTRY_CHARACTERS; start the excerpt
            # just before the symbol the request is about, so the cut does
            # not drop exactly what was asked for.
            positions = [content.find(name) for name in focus if name and name in content]
            if positions:
                start = max(0, min(positions) - MAX_ENTRY_CHARACTERS // 4)
                start = content.rfind("\n", 0, start) + 1
                start_line = content.count("\n", 0, start) + 1
                content = content[start:]
        entry = _entry(kind, relative, content[:budget], request)
        if start_line > 1:
            entry["data"]["excerpt_starts_at_line"] = start_line
        entries.append(entry)
        supplied.add(relative)
        taken_for_request += 1
        budget -= len(entry["data"]["content"])
        return True

    def add_references(request: Dict) -> bool:
        """The request names members; their uses across the checkout answer
        it even when the declaring file is already supplied."""
        nonlocal budget
        found = False
        for member in requested_member_names(request)[:3]:
            sites = reference_sites(index, member)
            if not sites or len(entries) >= MAX_ENTRIES or budget <= 0:
                continue
            content = (f"References to `{member}` in the repository sources "
                       f"({len(sites)}{'+' if len(sites) >= MAX_REFERENCE_SITES else ''} sites):\n\n"
                       + "\n".join(sites))
            entry = _entry("requested_reference_sites", f"references:{member}",
                           content[:budget], request)
            entry["data"]["member"] = member
            entry["data"]["site_count"] = len(sites)
            entries.append(entry)
            budget -= len(entry["data"]["content"])
            found = True
        return found

    for request in requests:
        if not isinstance(request, dict):
            continue
        family = classify_evidence_request(request, index)
        taken_for_request = 0
        if family == RUNTIME_ONLY:
            entry = runtime_entry(target or {}, request)
            if entry is not None and len(entries) < MAX_ENTRIES:
                entries.append(entry)
                continue
            declined.append({
                "request": request,
                "reason": "requires observing a running program; "
                          "not obtainable from the repository",
            })
            continue
        names = requested_type_names(request)
        satisfied = False
        if family in DERIVED_FAMILIES:
            entry = (
                _binding_entry(repo_root, target or {}, request, index)
                if family == SYMBOL_BINDING
                else _derived_entry(repo_root, target or {}, request, family, index)
            )
            if entry is not None and len(entries) < MAX_ENTRIES:
                entries.append(entry)
                satisfied = True
            if not satisfied:
                # A consumer inventory is a caller question the derived
                # record could not settle; the member's uses can.
                satisfied = add_references(request)
            if not satisfied:
                declined.append({
                    "request": request,
                    "reason": _DERIVED_DECLINE.get(
                        family, "the question could not be answered from the "
                                "checkout"
                    ),
                })
                continue
        elif family == ANDROID_RESOURCE:
            entry = (_resource_entry(index, target or {}, request, budget)
                     if budget > 0 and len(entries) < MAX_ENTRIES else None)
            if entry is not None:
                entries.append(entry)
                taken_for_request += 1
                budget -= sum(len(item["declaration"]) for item in entry["data"]["declarations"])
                satisfied = True
            # The files and types it names as well: Droid_SCEP's key-size
            # request named the restrictions schema next to the string
            # resource, autoshine's the sources of nine EditText fields.
            for path in mentioned_repository_paths(request, repo_root, index):
                satisfied |= add("requested_source", path, request, names)
            for name in names:
                path = index.declaring_path(name)
                if path is not None:
                    satisfied |= add("requested_source", path, request)
        elif family == REPOSITORY_TESTS:
            for path in index.test_sources(names, MAX_ENTRIES_PER_REQUEST):
                satisfied |= add("requested_test_source", path, request)
        elif family == BUILD_CONFIGURATION:
            for path in index.build_files():
                satisfied |= add("requested_build_configuration", path, request)
        else:
            for name in names:
                path = index.declaring_path(name)
                if path is not None:
                    satisfied |= add("requested_source", path, request)
        mentioned = mentioned_repository_paths(request, repo_root, index)
        if not satisfied:
            for path in mentioned:
                satisfied |= add("requested_source", path, request, names)
        if not satisfied:
            satisfied = add_references(request)
        if not satisfied:
            already = [
                path.relative_to(repo_root).as_posix() for path in mentioned
                if path.relative_to(repo_root).as_posix() in supplied
            ]
            declined.append({
                "request": request,
                "reason": (
                    "already in the evidence packet: " + ", ".join(already)
                    if already else "no matching file in the repository"
                ),
            })
    return {"entries": entries, "declined": declined}


def catalog_supplement(entries: List[Dict], prefix: str = "SUP") -> List[Dict]:
    """Number the fetched entries so a claim can cite them.

    They carry their own prefix rather than extending an existing one: an
    extraction that cites SUP-002 is visibly grounded in evidence fetched
    because the previous extraction asked for it.
    """
    from cognalys.llm.pipeline_architecture import canonical_hash

    supplement = []
    for index, entry in enumerate(entries, 1):
        supplement.append({
            "evidence_id": f"{prefix}-{index:03d}",
            "kind": entry["kind"],
            "trust_level": entry["trust_level"],
            "mandatory": entry["mandatory"],
            "sha256": canonical_hash(entry["data"]),
            "data": entry["data"],
        })
    return supplement
