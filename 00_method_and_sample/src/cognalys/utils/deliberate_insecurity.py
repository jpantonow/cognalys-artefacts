"""Signs in the repository that the flagged behaviour was written on purpose.

weeWX Weather (2026-11-04): NetworkClient's static initialiser builds an
SSLContext over a trust manager that accepts every certificate, and
generate() installs it with a hostname verifier returning true, under
`@SuppressLint({"CustomX509TrustManager", "TrustAllX509TrustManager"})` on
the class. The app talks to the user's own weather server, often
self-signed or reached by IP, and no setting turns the check back on. The
pipeline replaced it with platform trust ("Users can no longer connect to
servers with self-signed certificates..."), a regression for every user of
the app. Maintainers closed the same kind of change as intentional before
(androidtv-news #14, TagMo #765, Feeder #1210 "some sites would stop
working").

This module only reads the repository and reports what it saw, with
file:line quotes; it never decides the finding. The model receives it as
evidence (DELIBERATE_INSECURITY_GUIDANCE) in evidence extraction,
adjudication, planning and the behaviour review. What it records:

* suppressions naming a security check (`@SuppressLint`, `@SuppressWarnings`,
  `@Suppress`, `@file:Suppress`, `//noinspection`, `tools:ignore`) on the
  reported method, its enclosing classes, its file or a statement inside
  it, plus those in the module's manifest and XML resources. The ids are
  data (data/deliberate_insecurity_ids.json), each with its documentation;
* the module's network security configuration and manifest entries that
  relax transport security (cleartext, user certificates, pin overrides);
* comments in and just above the method that say why (self-signed, trust
  all, on purpose, debug only, a local or home server, ...);
* whether a condition gates the behaviour: the reported line, and every
  use elsewhere (same file, or `Class.name` in the module) of a name the
  method assigns, inside an `if` whose condition names an option, a
  preference read or a build flag ("gated"), or none of them inside one
  ("unconditional").

The reading is lexical: a gate in a caller of a caller, reflection, or a
preference the condition reads through another object is not seen.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List

from cognalys.utils.identifier_words import blank_comments_and_literals

_SKIPPED_DIRS = frozenset({".git", "build", ".gradle", "node_modules", ".idea", "out"})
_CODE_SUFFIXES = (".java", ".kt")
MAX_QUOTE = 160
MAX_ITEMS = 6
MAX_USES = 12
# Lines above the method header read for its annotations and comments.
MAX_HEADER_LINES = 8

_SUPPRESSION = re.compile(
    r"@(?:file\s*:\s*)?(?:SuppressLint|SuppressWarnings|Suppress)\s*\((?P<ids>[^)]*)\)"
    r"|//\s*noinspection\s+(?P<inspection>[\w, ]+)"
    r"|tools:ignore\s*=\s*\"(?P<xml>[^\"]*)\"")

# A comment that says why the code is insecure, or that it is meant to be.
_INTENT_WORDS = re.compile(
    r"self[\s-]?signed|untrusted|unverified|\btrust(?:s|ing)?\s+(?:all|any|every)"
    r"|\baccept(?:s|ing)?\s+(?:all|any|every)\b|\ball\s+(?:certs|certificates|hosts)"
    r"|\binsecure\b|\bunsafe\b|no[\s_-]?verify|without\s+verif"
    r"|on\s+purpose|intentional|deliberate|by\s+design"
    r"|debug(?:ging)?\s+(?:only|builds?)|only\s+(?:for|in)\s+debug"
    r"|(?:bypass|ignor|skip|disabl)\w*\s+(?:the\s+)?(?:ssl|tls|cert|hostname|verification|check)"
    r"|(?:local|home|own|private|internal)\s+(?:server|network|instance)|\blan\b|\bip\s+address",
    re.IGNORECASE)

# A condition that reads an option: a setting the user turns on, a
# preference, or a switch named for what it relaxes. A null or emptiness
# check is not one.
_OPTION_WORDS = re.compile(
    r"[Aa]llow|ALLOW|[Ii]nsecure|INSECURE|[Uu]nsafe|UNSAFE|[Ii]gnore|IGNORE|[Ss]kip|SKIP"
    r"|[Tt]rust(?:All|Any|Every|Self|Insecure|Unsafe|User|Untrusted)|TRUST_ALL"
    r"|[Dd]isable|DISABLE|[Bb]ypass|[Ss]elf_?[Ss]igned|[Nn]o_?[Vv]erif|NO_VERIF"
    r"|[Vv]erify_?(?:Ssl|SSL|Cert|Host|Tls|TLS)|[Aa]ccept|[Pp]ref(?:s|erence)|PREF"
    r"|[Ss]etting|[Oo]ption|get(?:Boolean|String|Int)\s*\(")
_NULL_CHECK = re.compile(
    r"[\w.()]+\s*[!=]==?\s*null|null\s*[!=]==?\s*[\w.()]+|[\w.()]+\s+instanceof\s+[\w.]+"
    r"|[\w.()]+\s+is\s+[\w.]+|[\w.]+\.isN?u?l?l?O?r?Empty\(\)")
_BUILD_FLAG = re.compile(r"\bBuildConfig\.\w+|\bisDebug\w*|\bDEBUG\b|\bdebuggable\b", re.IGNORECASE)
_PREFERENCE_READ = re.compile(
    r"""(?:get(?:Boolean|String|Int|Long|StringSet)\s*\(|"""
    r"""(?:boolean|string|int)PreferencesKey\s*\(|\[)\s*"(?P<key>[\w.\-]+)\"""")

_ELSE_HEADER = re.compile(r"^\s*\}?\s*else\s*$")
_BRACELESS_IF = re.compile(r"^\s*(?:\}\s*)?(?:else\s+)?if\s*\((?P<guard>.*)\)\s*$")
_TYPE_DECLARATION = re.compile(r"\b(?:class|interface|object|enum)\s+(?P<name>[A-Za-z_]\w*)")

_NSC_RELAXING = re.compile(
    r"cleartextTrafficPermitted\s*=\s*\"true\"|<certificates\s+[^>]*src\s*=\s*\"user\""
    r"|overridePins\s*=\s*\"true\"|<debug-overrides\b|usesCleartextTraffic\s*=\s*\"true\"")


@lru_cache(maxsize=1)
def _known_ids() -> Dict[str, Dict[str, str]]:
    """Every warning id of data/deliberate_insecurity_ids.json, with its source."""
    from cognalys.workspace import library_data

    try:
        data = json.loads(library_data("deliberate_insecurity_ids.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    known: Dict[str, Dict[str, str]] = {}
    for analyzer, section in data.items():
        if analyzer.startswith("_") or not isinstance(section, dict):
            continue
        for identifier, entry in (section.get("ids") or {}).items():
            known[identifier] = {"analyzer": analyzer, **(entry if isinstance(entry, dict) else {})}
    return known


def _matching_ids(text: str) -> List[str]:
    """The known ids a suppression's argument names: "TrustAllX509TrustManager",
    "java:S4830", "squid:S4830" and "AllowAllHostnameVerifier" each count once."""
    known = _known_ids()
    found = []
    for token in re.findall(r"[A-Za-z_][\w]*|S\d{3,5}", text):
        name = token.rsplit(":", 1)[-1]
        if name in known and name not in found:
            found.append(name)
    return found


def _about_the_reported_code(suppressions: List[Dict], context: str) -> List[Dict]:
    """The suppressions keeping only the ids whose `apis` the reported code names.

    Bookwyrm's StartActivity.onCreate carries @SuppressLint("SetJavaScriptEnabled")
    and was reported for a GCMParameterSpec: the maintainer accepted JavaScript,
    not the IV. An id without `apis` is kept.
    """
    lowered = context.lower()
    known = _known_ids()
    kept = []
    for item in suppressions:
        ids = [name for name in item["ids"]
               if not known.get(name, {}).get("apis")
               or any(api.lower() in lowered for api in known[name]["apis"])]
        if ids:
            kept.append({**item, "ids": ids})
    return kept


def _quote(line: str) -> str:
    return " ".join(line.strip().split())[:MAX_QUOTE]


def _code_files(root: Path) -> Iterable[Path]:
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in _SKIPPED_DIRS]
        for name in files:
            if name.endswith(_CODE_SUFFIXES):
                yield Path(directory) / name


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _module_root(repository: Path, relative: str) -> Path:
    """The Gradle module holding `relative`: the part before `/src/`."""
    parts = Path(relative).parts
    if "src" in parts:
        return repository.joinpath(*parts[:parts.index("src")])
    return repository


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _matching_brace(code: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(code)):
        if code[index] == "{":
            depth += 1
        elif code[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(code)


def _method_span(text: str, code: str, method_name: str, method_body: str,
                 reported: int) -> tuple:
    """(start offset, end offset) of the method in the file, or of the reported line."""
    body = (method_body or "").strip("\n")
    if body:
        start = text.find(body)
        if start >= 0:
            return start, start + len(body)
        first = next((line.strip() for line in body.splitlines() if line.strip()), "")
        if first:
            for match in re.finditer(re.escape(first), text):
                opening = code.find("{", match.start())
                if opening >= 0:
                    return match.start(), _matching_brace(code, opening) + 1
    name = method_name if method_name not in ("<clinit>", "<init>") else ""
    if name:
        pattern = re.compile(rf"\b(?:fun\s+(?:[\w.<>]+\.)?|[\w<>\[\]?]+\s+){re.escape(name)}\s*\(")
        for match in pattern.finditer(code):
            opening = code.find("{", match.end())
            if opening >= 0 and ";" not in code[match.end():opening]:
                return match.start(), _matching_brace(code, opening) + 1
    if reported > 0:
        lines = text.splitlines(keepends=True)
        if reported <= len(lines):
            start = sum(len(line) for line in lines[:reported - 1])
            return start, start + len(lines[reported - 1])
    return -1, -1


def _header_start(lines: List[str], first_line: int) -> int:
    """The first line (1-based) of the annotations and comments above `first_line`."""
    start = first_line
    while start > 1 and first_line - start < MAX_HEADER_LINES:
        previous = lines[start - 2].strip()
        if previous.startswith(("@", "//", "/*", "*")) or (previous.endswith(")") and "@" in previous):
            start -= 1
        else:
            break
    return start


def _suppressions_in(text: str, low: int, high: int, scope: str, relative: str) -> List[Dict]:
    found = []
    for match in _SUPPRESSION.finditer(text, low, high):
        ids = _matching_ids(match.group("ids") or match.group("inspection") or match.group("xml") or "")
        if ids:
            line = _line_of(text, match.start())
            found.append({"file": relative, "line": line, "scope": scope, "ids": ids,
                          "quote": _quote(text.splitlines()[line - 1])})
    return found


def _enclosing_types(code: str, offset: int) -> List[tuple]:
    """(declaration offset, name) of each class/object declaration whose body holds `offset`."""
    found = []
    for match in _TYPE_DECLARATION.finditer(code, 0, max(offset, 0)):
        opening = code.find("{", match.end())
        if opening < 0 or opening > offset:
            continue
        if re.search(r"[;=]", code[match.end():opening]) and "(" not in code[match.end():opening]:
            continue
        if _matching_brace(code, opening) >= offset:
            found.append((match.start(), match.group("name")))
    return found


_GUARD_KEYWORD = re.compile(r"\b(?:if|while)\s*\(")


def _guard_start(header: str) -> int:
    """Offset in `header` of the `if (...)`/`while (...)` that ends it, or -1.

    The last such keyword whose parenthesis closes at the end of the
    header: Kotlin statements end at a line break, not a `;`, so the head
    of a block may follow other statements.
    """
    stripped = header.rstrip()
    if not stripped.endswith(")"):
        return -1
    for match in reversed(list(_GUARD_KEYWORD.finditer(stripped))):
        depth = 0
        for index in range(match.end() - 1, len(stripped)):
            depth += {"(": 1, ")": -1}.get(stripped[index], 0)
            if depth == 0:
                break
        if depth == 0 and index == len(stripped) - 1:
            return match.start()
    return -1


def _header_before(code: str, index: int) -> tuple:
    """(begin offset, text) of the statement head that ends at the `{` at `index`."""
    begin = max(code.rfind(";", 0, index), code.rfind("{", 0, index),
                code.rfind("}", 0, index)) + 1
    return begin, code[begin:index]


def _block_guards(code: str, text: str, offset: int, floor: int) -> tuple:
    """(guards, outermost opener) of the blocks enclosing `offset` above `floor`.

    The guards are the `if`/`while` headers of those blocks; an `else` block
    contributes its `if`'s condition. The outermost opener is the `{` of the
    member (method, initialiser) holding `offset`.
    """
    guards = []
    outermost = -1
    depth = 0
    index = offset - 1
    lines = text.splitlines()
    while index > floor:
        character = code[index]
        if character == "}":
            depth += 1
        elif character == "{":
            if depth:
                depth -= 1
            else:
                outermost = index
                begin, header = _header_before(code, index)
                negated = False
                if _ELSE_HEADER.match(header) or re.match(r"^\s*\}\s*else\s*$", code[max(begin - 1, 0):index]):
                    # `} else {`: the condition is the matching if's.
                    cursor, depth_back = code.rfind("}", 0, index), 0
                    while cursor > floor:
                        if code[cursor] == "}":
                            depth_back += 1
                        elif code[cursor] == "{":
                            depth_back -= 1
                            if depth_back == 0:
                                break
                        cursor -= 1
                    begin, header = _header_before(code, cursor)
                    negated = True
                start = _guard_start(header)
                if start >= 0:
                    line = _line_of(text, begin + start)
                    guards.append({"line": line, "quote": _quote(lines[line - 1]), "negated": negated})
        index -= 1
    return guards, outermost


def _line_guards(lines: List[str], code_lines: List[str], line: int, low_line: int,
                 column: int = -1) -> List[Dict]:
    """A condition on the site's own line that the site follows, or one on
    the line just before it (no braces). A site inside the condition itself
    is not gated by it (BirdoVPN's `if (storedHmac == computeHmac(...))`).
    An early return is not read: `if (done) return` guards a once-only setup
    as often as an option (memories' mTrustAllDefault)."""
    guards = []
    own = code_lines[line - 1]
    for match in _GUARD_KEYWORD.finditer(own):
        depth, close = 0, -1
        for index in range(match.end() - 1, len(own)):
            depth += {"(": 1, ")": -1}.get(own[index], 0)
            if depth == 0:
                close = index
                break
        if 0 <= close < column:
            guards.append({"line": line, "quote": _quote(lines[line - 1]), "negated": False})
            break
    previous = line - 1
    while previous >= low_line and not code_lines[previous - 1].strip():
        previous -= 1
    if previous >= low_line and _BRACELESS_IF.match(code_lines[previous - 1]) \
            and not own.lstrip().startswith("{"):
        guards.append({"line": previous, "quote": _quote(lines[previous - 1]), "negated": False})
    return guards


def _option_guard(guard: Dict, text: str) -> Dict:
    """`guard` classified: an option, a build flag, or neither (a null check)."""
    quote = guard["quote"]
    condition = _NULL_CHECK.sub(" ", re.sub(r"^\s*(?:\}\s*)?(?:else\s+)?(?:if|while)\s*", "", quote))
    if _OPTION_WORDS.search(condition):
        kind = "option"
    elif _BUILD_FLAG.search(condition):
        kind = "build_flag"
    else:
        return {}
    keys = [match.group("key") for match in _PREFERENCE_READ.finditer(condition)]
    # A name the condition reads that the file assigns from a preference.
    for name in re.findall(r"[A-Za-z_]\w*", condition):
        assignment = re.search(rf"(?<![\w.]){re.escape(name)}\s*=[^=\n][^\n]*", text)
        if assignment:
            keys += [match.group("key") for match in _PREFERENCE_READ.finditer(assignment.group(0))]
    return {**guard, "kind": kind, **({"preference_keys": sorted(set(keys))} if keys else {})}


def _assigned_names(code: str, low: int, high: int) -> List[str]:
    """Names the method assigns that it does not declare: fields it fills."""
    region = code[low:high]
    assigned = set(re.findall(r"(?<![\w.])([A-Za-z_]\w*)\s*=(?![=>])", region))
    declared = set(re.findall(r"\b(?:val|var)\s+([A-Za-z_]\w*)", region))
    declared |= set(re.findall(r"[\w>\]?]\s+([A-Za-z_]\w*)\s*=(?![=>])", region))
    return sorted(name for name in assigned - declared
                  if name not in ("this", "it", "field", "value"))


def _sites(repository: Path, relative: str, text: str, code: str, low: int, high: int,
           reported: int, method_name: str, class_name: str, module: Path) -> List[Dict]:
    """The reported line, and the uses of what the method builds."""
    lines = text.splitlines()
    sites = []
    if reported and _line_of(text, low) <= reported <= _line_of(text, high):
        sites.append({"file": relative, "line": reported, "role": "reported line",
                      "offset": sum(len(line) + 1 for line in lines[:reported - 1])})
    else:
        sites.append({"file": relative, "line": _line_of(text, low), "role": "reported method",
                      "offset": low})
    names = _assigned_names(code, low, high)
    for name in names:
        for match in re.finditer(rf"(?<![\w.$]){re.escape(name)}\b", code):
            if low <= match.start() < high or len(sites) > MAX_USES:
                continue
            line = _line_of(text, match.start())
            if re.search(rf"\b(?:private|protected|public|static|final|val|var|lateinit|internal)\b"
                         rf"[^=\n]*(?<![\w.]){re.escape(name)}\s*(?:[:;]|=\s*null\b|$)",
                         code.splitlines()[line - 1]):
                continue  # its declaration
            sites.append({"file": relative, "line": line, "role": f"use of {name}",
                          "offset": match.start()})
    simple = class_name.rsplit(".", 1)[-1].split("$")[0]
    callable_name = method_name if method_name not in ("<clinit>", "<init>") else ""
    # Calls of the method in its own file (memories' getInsecureTLSContext,
    # called inside `if (trustAll)`).
    if callable_name:
        for match in re.finditer(rf"(?<![\w$]){re.escape(callable_name)}\s*\(", code):
            if low <= match.start() < high or len(sites) > MAX_USES:
                continue
            sites.append({"file": relative, "line": _line_of(text, match.start()),
                          "role": f"call of {callable_name}", "offset": match.start()})
    wanted = [rf"\b{re.escape(simple)}\.{re.escape(name)}\b" for name in names]
    if callable_name:
        wanted.append(rf"(?<![\w$]){re.escape(callable_name)}\s*\(")
    if simple and wanted and len(sites) <= MAX_USES:
        pattern = re.compile("|".join(wanted))
        for path in _code_files(module):
            other = path.relative_to(repository).as_posix()
            if other == relative:
                continue
            other_text = _read(path)
            if simple not in other_text:
                continue
            other_code = blank_comments_and_literals(other_text)
            for match in pattern.finditer(other_code):
                if len(sites) > MAX_USES:
                    break
                sites.append({"file": other, "line": _line_of(other_text, match.start()),
                              "role": "use in another file", "offset": match.start(),
                              "text": other_text, "code": other_code})
    return sites


def _gate(sites: List[Dict], text: str, code: str) -> Dict:
    gated, unguarded = [], []
    merged: Dict[tuple, Dict] = {}
    for site in sites:
        key = (site["file"], site["line"])
        if key in merged:
            merged[key]["role"] += ", " + site["role"]
        else:
            merged[key] = dict(site)
    for site in merged.values():
        site_text = site.get("text", text)
        site_code = site.get("code", code)
        lines = site_text.splitlines()
        code_lines = site_code.splitlines()
        offset = site["offset"]
        enclosing = _enclosing_types(site_code, offset)
        # The innermost type's own `{`: the walk stops at the member level.
        floor = site_code.find("{", enclosing[-1][0]) if enclosing else -1
        guards, opener = _block_guards(site_code, site_text, offset, floor)
        line = site["line"]
        method_start = _line_of(site_text, opener) if opener >= 0 else line
        # A use's column is known; the reported line's site is its start.
        column = offset - (site_code.rfind("\n", 0, offset) + 1) \
            if site["role"] not in ("reported line", "reported method") else -1
        guards += _line_guards(lines, code_lines, line, method_start, column)
        options = [found for guard in guards if (found := _option_guard(guard, site_text))]
        entry = {"file": site["file"], "line": line, "role": site["role"],
                 "quote": _quote(lines[line - 1]) if 0 < line <= len(lines) else ""}
        if options:
            gated.append({**entry, "guards": options[:3]})
        else:
            unguarded.append(entry)
    status = ("gated" if gated and not unguarded else
              "partially_gated" if gated else "unconditional")
    return {"status": status, "gated_sites": gated[:MAX_ITEMS],
            "unguarded_sites": unguarded[:MAX_ITEMS]}


def _module_configuration(repository: Path, module: Path) -> tuple:
    """(relaxing network-security/manifest entries, XML suppressions) of the module."""
    entries, suppressions = [], []
    main = module / "src"
    if not main.is_dir():
        return entries, suppressions
    for directory, dirs, files in os.walk(main):
        dirs[:] = [name for name in dirs if name not in _SKIPPED_DIRS]
        for name in files:
            if not name.endswith(".xml"):
                continue
            path = Path(directory) / name
            if name != "AndroidManifest.xml" and Path(directory).name != "xml":
                continue
            text = _read(path)
            relative = path.relative_to(repository).as_posix()
            lines = text.splitlines()
            if name == "AndroidManifest.xml" or "<network-security-config" in text:
                for match in _NSC_RELAXING.finditer(text):
                    line = _line_of(text, match.start())
                    entries.append({"file": relative, "line": line, "quote": _quote(lines[line - 1])})
            suppressions += _suppressions_in(text, 0, len(text), "module resource", relative)
    return entries[:MAX_ITEMS], suppressions[:MAX_ITEMS]


def deliberate_insecurity(repository: Path | str, source_file_path: str, method_name: str,
                          method_body: str = "", class_name: str = "",
                          reported_line: int = 0) -> Dict[str, Any]:
    """What the repository says about whether the reported code is insecure on purpose.

    status is "signs_found" when a suppression of a known security check
    covers the method (method, enclosing class, file or a statement in it),
    a comment in or above it states the intent, or an option or build flag
    gates it; "none" when there is none; "unavailable" when the file or the
    method cannot be read. The module's network security configuration and
    XML suppressions are recorded alongside, but alone they are not a sign
    about this code.
    """
    repository = Path(str(repository))
    relative = str(source_file_path or "")
    path = repository / relative
    text = _read(path) if relative and path.is_file() else ""
    result: Dict[str, Any] = {"status": "unavailable", "file": relative, "method": method_name}
    if not text:
        result["detail"] = "source file not found"
        return result
    code = blank_comments_and_literals(text)
    low, high = _method_span(text, code, method_name, method_body, int(reported_line or 0))
    if low < 0:
        result["detail"] = "method not found in the file"
        return result
    lines = text.splitlines()
    first_line = _line_of(text, low)
    header_line = _header_start(lines, first_line)
    header_offset = sum(len(line) + 1 for line in lines[:header_line - 1])
    suppressions = _suppressions_in(text, header_offset, low, "method", relative)
    suppressions += _suppressions_in(text, low, high, "statement in the method", relative)
    for declaration, name in _enclosing_types(code, low):
        declaration_line = _line_of(text, declaration)
        start_line = _header_start(lines, declaration_line)
        start = sum(len(line) + 1 for line in lines[:start_line - 1])
        end = code.find("{", declaration)
        suppressions += _suppressions_in(text, start, end, f"class {name}", relative)
    for match in re.finditer(r"@file\s*:\s*Suppress\w*\s*\([^)]*\)", text):
        suppressions += _suppressions_in(text, match.start(), match.end(), "file", relative)
    reported = int(reported_line or 0)
    if 0 < reported <= len(lines):
        # The reported statement: its line, and the next while a parenthesis is open.
        end = reported
        while end < min(len(lines), reported + 4) and \
                "\n".join(lines[reported - 1:end]).count("(") > "\n".join(lines[reported - 1:end]).count(")"):
            end += 1
        context = "\n".join(lines[reported - 1:end])
    else:
        context = text[low:high]
    # A suppression on the method or a statement is judged by the reported
    # statement (Bookwyrm's onCreate holds both the WebView and the cipher);
    # one on the class or file by the whole method (Grocy's class-level
    # CustomX509TrustManager over getTrustManager's `tmf.init(ks)`).
    local = [item for item in suppressions if item["scope"] in ("method", "statement in the method")]
    suppressions = (_about_the_reported_code(local, context)
                    + _about_the_reported_code([item for item in suppressions if item not in local],
                                               text[low:high]))
    comments = []
    for match in re.finditer(r"//[^\n]*|/\*[\s\S]*?\*/|<!--[\s\S]*?-->", text[header_offset:high]):
        intent = _INTENT_WORDS.search(match.group(0))
        if intent and not _SUPPRESSION.search(match.group(0)):
            # The line saying it, not the `/**` that opens the comment.
            line = _line_of(text, header_offset + match.start() + intent.start())
            comments.append({"file": relative, "line": line, "quote": _quote(lines[line - 1])})
    module = _module_root(repository, relative)
    sites = _sites(repository, relative, text, code, low, high, int(reported_line or 0),
                   method_name, class_name, module)
    gate = _gate(sites, text, code)
    configuration, resource_suppressions = _module_configuration(repository, module)
    signs = bool(suppressions or comments or gate["status"] != "unconditional")
    result.update(
        status="signs_found" if signs else "none",
        suppressions=suppressions[:MAX_ITEMS],
        comments=comments[:MAX_ITEMS],
        gate=gate,
        network_security_configuration=configuration,
        module_resource_suppressions=resource_suppressions,
    )
    return result


# How the evidence reaches evidence extraction, adjudication, planning and
# the behaviour review. It describes the maintainer's intent; whether the
# finding is real is decided as before.
DELIBERATE_INSECURITY_GUIDANCE = (
    "deliberate_insecurity, when present, is a repository reading this "
    "pipeline ran: suppressions of a security check on the reported code, "
    "comments stating why it is insecure, and whether an option, preference "
    "or build flag gates it (gate.status gated, partially_gated or "
    "unconditional), each with file:line quotes. It records that the "
    "maintainer knows about the behaviour; it never makes the finding a "
    "false positive and is no reason to dismiss it. When the behaviour is "
    "deliberate and unconditional, users may rely on it (a home or "
    "self-hosted server with a self-signed certificate or an IP address): a "
    "fix that removes it with no alternative breaks them. Consider instead "
    "NO_SAFE_FIX, so the maintainer receives an issue proposing an opt-in "
    "setting or certificate pinning, or a change that keeps the old "
    "behaviour behind an explicit option, only when such an option already "
    "exists in the code; do not invent a new setting. When an option "
    "or preference already gates it, the path the user turns on is the "
    "maintainer's design: keep it working and change only what runs when it "
    "is off. A gate that is a build flag (debug builds) leaves release "
    "builds unaffected; say so. "
)


def deliberate_insecurity_evidence(result: Dict | None) -> Dict:
    """The reading as compact evidence, only when it found a sign; {} otherwise."""
    if not isinstance(result, dict) or result.get("status") != "signs_found":
        return {}
    gate = result.get("gate") or {}
    def quotes(items: List[Dict], *extra: str) -> List[str]:
        return [f"{item.get('file')}:{item.get('line')}: {item.get('quote')}"
                + "".join(f" [{key}: {', '.join(map(str, item[key]))}]" if isinstance(item.get(key), list)
                          else f" [{key}: {item[key]}]" for key in extra if item.get(key))
                for item in items]
    gated = []
    for site in gate.get("gated_sites") or []:
        guards = "; ".join(
            f"{guard.get('kind')} at {site.get('file')}:{guard.get('line')}: {guard.get('quote')}"
            + (f" (preference {', '.join(guard['preference_keys'])})" if guard.get("preference_keys") else "")
            for guard in site.get("guards") or [])
        gated.append(f"{site.get('file')}:{site.get('line')} ({site.get('role')}) under {guards}")
    statement = {
        "gated": "Every site of the behaviour found runs only under an option or build flag.",
        "partially_gated": "Some sites of the behaviour run under an option or build flag; others run unconditionally.",
        "unconditional": "No option, preference or build flag gates the behaviour: it runs for every user.",
    }.get(str(gate.get("status")), "")
    return {
        "status": "signs_found",
        "method": result.get("method", ""),
        "suppressions": quotes(result.get("suppressions") or [], "scope", "ids"),
        "comments": quotes(result.get("comments") or []),
        "gate": {"status": gate.get("status"), "statement": statement,
                 "gated_sites": gated,
                 "unguarded_sites": quotes(gate.get("unguarded_sites") or [], "role")},
        "network_security_configuration": quotes(result.get("network_security_configuration") or []),
        "module_resource_suppressions": quotes(result.get("module_resource_suppressions") or [], "ids"),
        "limits": "Lexical reading of the repository: a gate in a caller of a caller, "
                  "reflection or a preference read through another object is not seen. "
                  "It records intent; it says nothing about whether the finding is real.",
    }
