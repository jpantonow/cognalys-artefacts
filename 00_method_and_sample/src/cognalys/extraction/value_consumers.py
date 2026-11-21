"""Where the value a reported method returns ends up, read from its real call sites.

QuantumBadger/RedReader closed PR #1373 because its premise was wrong. The
analysis said General.sha1's result named "derived, regenerable cache entries",
so switching SHA-1 to SHA-256 would only cause cache misses. Its one caller is

    private static String getDbFilename(final String type, final RedditAccount user) {
        return General.sha1(user.username.getBytes()) + "_" + type + "_subreddits.db";
    }

-- the name of a persistent database. Changing the digest renames that file,
and every user loses the subreddit data stored under the old name. The usage
classification was the model's word; nothing looked at where the value went.

This follows the returned value from each resolved call site -- through a
`return` into the enclosing method's own callers, and through a local
assignment into the lines that use the variable -- and classifies every
statement it reaches by what the statement does with it. It is lexical and
bounded, and says so: a consumer it recognises is evidence, a consumer it does
not recognise is not evidence that the value stays in memory.

A value can also leave its method without a call site naming it. In
StellarSand/Password-Monitor the reported statement is a Koin provider,

    single { MessageDigest.getInstance("SHA-1") }

and the digest reaches HashUtils through `get<MessageDigest>()`, whose 5-hex
prefix is sent to api.pwnedpasswords.com/range/ -- a service that only answers
SHA-1 prefixes. No caller of the provider lambda exists in the source, so the
value looked unused and a SHA-256 patch was proposed that would have broken the
app's only feature. When the reported line is a dependency-injection provider
(Koin single/factory/scoped, Dagger/Hilt/Spring @Provides/@Bean), the tracing
starts from every injection site of the provided type instead; and a value
passed as an argument is followed into the parameter of the method receiving it.
"""

from __future__ import annotations

import re
from pathlib import Path

from cognalys.extraction.repository_index import build_repository_index
from cognalys.extraction.semantic_index import STATEMENT_KEYWORDS


MAX_CONSUMERS = 30
MAX_STATEMENT_LINES = 6
MAX_FOLLOW_LINES = 40
MAX_DEPTH = 5
MAX_VISITS = 200
# A callee name declared more often than this is too ambiguous to follow.
MAX_DECLARATIONS = 4

# A string literal naming a file: the value is part of a stored file's name.
_FILE_LIKE_LITERAL = re.compile(
    r'"[^"\n]*\.(?:db|sqlite3?|realm|json|xml|txt|csv|bin|dat|properties|prefs'
    r'|log|png|jpe?g|webp|gif|zip|tmp|cache)"',
    re.IGNORECASE,
)
_PERSISTENCE_CALL = re.compile(
    r"\b(?:new\s+)?(?:File|FileOutputStream|FileInputStream|FileWriter|FileReader"
    r"|RandomAccessFile)\s*\("
    r"|\b(?:Paths\s*\.\s*get|Path\s*\.\s*of|openFileOutput|openFileInput"
    r"|openOrCreateDatabase|getDatabasePath|deleteDatabase|databaseBuilder"
    r"|getSharedPreferences|preferencesDataStore|getFileStreamPath|getDir)\s*\("
    r"|\.\s*(?:putString|putInt|putLong|putBoolean|putFloat|putStringSet)\s*\("
    r"|\bSQLiteOpenHelper\b"
)
_NETWORK_CALL = re.compile(
    r"\b(?:new\s+)?URL\s*\(|\bUri\s*\.\s*parse\s*\(|\bHttpUrl\b|\bRequest\s*\.\s*Builder\b"
    r"|\.\s*(?:addHeader|header|setRequestProperty|url|addQueryParameter"
    r"|appendQueryParameter)\s*\("
)
# A call on a store-like receiver: EinkBro's ad filter keeps each filter's
# processed data under its id, `binaryDataStore.loadData(id)` (2026-10-22),
# and the id was SHA-1 of the URL; a patch moving it to SHA-256 left every
# downloaded filter under a key nothing reads any more.
_STORE_KEYED_CALL = re.compile(
    r"\b\w*(?:Store|Storage|Prefs|Preferences|Repository|Dao)\s*\.\s*"
    r"(?:load|save|store|has|clear|remove|delete|read|write|get|put|contains)\w*\s*\(",
    re.IGNORECASE,
)
# Written to a stream: javalc6/rssreader's FileManager.writeFile puts an MD5
# checksum into every backup through `ostream.write(digest.getBytes(...))`,
# and readFile checks it on restore. The receiver's name is the only lexical
# sign of a stream, so it must say so (out, stream, writer, sink).
_STREAM_WRITE = re.compile(
    r"\b\w*(?:[Ss]tream|[Ww]riter|[Ss]ink|[Oo]ut|OUT)\s*\.\s*"
    r"(?:write|writeBytes|writeUTF|writeString|writeUtf8|print|println|append)\s*\("
)
# The app's cache directory. AmaRadio names its cache files
# File(ctx.cacheDir, "cache_" + key.md5()); classed as persistence by File(,
# the model's own (correct) "cache" reading was overridden and the finding
# abstained (2026-10-16). A file there is regenerable by definition: Android
# deletes it when storage runs low.
_CACHE_LOCATION = re.compile(
    r"\b(?:cacheDir|externalCacheDir|codeCacheDir)\b"
    r"|\b(?:getCacheDir|getExternalCacheDir|getCodeCacheDir)\s*\("
)
# A lower-case receiver of a call, `digester` in `digester.update(bytes)`:
# a local object (a field or variable), not a type.
_LOCAL_RECEIVER = re.compile(r"(?<![\w$.])([a-z_][\w$]*)\s*\.\s*[A-Za-z_$][\w$]*\s*\(")
# A request built from a URL literal: client.get("https://host/path/$value").
# Checked before file names, which a URL path can end with.
_URL_LITERAL = re.compile(r'"(?:https?|wss?)://')
_COMPARISON = re.compile(
    r"\.\s*(?:equals|contentEquals|equalsIgnoreCase)\s*\(|[!=]==?"
    r"|MessageDigest\s*\.\s*isEqual\s*\("
)
# Compared with a constant: an UPPER_CASE name, a long number or a hex string.
# WiFiAnalyzer hashes its package name with MD5 and checks the result against
# TYPE1/TYPE2/TYPE3, the precomputed values of its official builds; a patch
# that changed or dropped the MD5 turned that check off (2026-10-16). Such a
# value is fixed as surely as one stored on disk.
_CONSTANT_OPERAND = r"(?:[A-Z][A-Z0-9_]{2,}\b|-?\d{6,}|0x[0-9a-fA-F]{6,}|\"[0-9a-fA-F]{16,}\")"
_CONSTANT_COMPARISON = re.compile(
    rf"(?:==|!=|\.\s*(?:equals|contentEquals)\s*\()\s*{_CONSTANT_OPERAND}"
    rf"|{_CONSTANT_OPERAND}\s*(?:==|!=|\.\s*(?:equals|contentEquals)\s*\()"
)
_RETURN = re.compile(r"^\s*return\b")
_EXPRESSION_BODY = re.compile(
    r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w$.]+\.)?(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)"
    r"\s*(?::\s*[^=]+)?=(?!=)"
)
_ASSIGNMENT = re.compile(
    r"(?:\b(?:val|var)\s+|^\s*(?:final\s+)?[\w$.<>\[\],? ]+?\s+)"
    r"(?P<name>[a-zA-Z_$][\w$]*)\s*(?::[^=]+)?=(?!=)"
)
_METHOD_HEADER = re.compile(
    r"^\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*"
    r"(?:(?:public|protected|private|internal|static|final|synchronized|override"
    r"|open|suspend|inline)\s+)*"
    r"(?:fun\s+(?:<[^>]+>\s*)?(?:[\w$.]+\.)?(?P<kotlin>[\w$]+)"
    r"|[\w$.<>\[\],?]+\s+(?P<java>[\w$]+))\s*\([^;]*$"
)
_NOT_METHOD_NAMES = frozenset({
    "if", "for", "while", "switch", "catch", "synchronized", "return", "new",
    "else", "try", "when", "throw", "super", "this",
})


def _statement(lines: list[str], index: int) -> str:
    """The statement starting at `index`, joined while its brackets stay open."""
    text = lines[index]
    depth = text.count("(") - text.count(")")
    end = index
    while depth > 0 and end + 1 < len(lines) and end - index < MAX_STATEMENT_LINES:
        end += 1
        text += " " + lines[end].strip()
        depth += lines[end].count("(") - lines[end].count(")")
    return " ".join(text.split())


_LOGGING = re.compile(
    r"^\s*(?:Log\s*\.\s*[vdiwe]|Timber\s*\.\s*\w+|System\s*\.\s*(?:out|err)\s*\.\s*print\w*"
    r"|println|print|\w*[Ll]ogger\s*\.\s*\w+)\s*\("
)


def classify_statement(statement: str) -> tuple[str, str]:
    # A log line naming a file does not store anything under that name.
    if _LOGGING.match(statement):
        return "", ""
    if _URL_LITERAL.search(statement):
        return "network", "the value is sent in a URL, header or request"
    if _CACHE_LOCATION.search(statement):
        return "cache", (
            "the value names a file in the app's cache directory, which Android "
            "may clear at any time"
        )
    if _FILE_LIKE_LITERAL.search(statement) or _PERSISTENCE_CALL.search(statement):
        return "persistence", (
            "the value becomes part of a stored file, database or preferences "
            "name, or is written to storage"
        )
    if _STORE_KEYED_CALL.search(statement):
        return "persistence", (
            "the value is a key into a store, preferences, repository or "
            "DAO: data saved under the old key is no longer found"
        )
    if _STREAM_WRITE.search(statement):
        return "persistence", (
            "the value is written to an output stream or writer, such as a "
            "file, a backup or a socket"
        )
    if _NETWORK_CALL.search(statement):
        return "network", "the value is sent in a URL, header or request"
    if _CONSTANT_COMPARISON.search(statement):
        return "fixed_value", (
            "the value is compared with a value fixed in the source, such as a "
            "precomputed digest, which a different derivation never matches"
        )
    if _COMPARISON.search(statement):
        return "comparison", "the value is compared with another value"
    return "", ""


# A line opening with one of these is a statement, even when it looks like a
# method header: `return withContext(Dispatchers.Default) {`.
_STATEMENT_KEYWORDS = STATEMENT_KEYWORDS


def _header_name(line: str) -> str:
    """The name of the method `line` declares, or ''."""
    header = _METHOD_HEADER.match(line)
    if not header:
        return ""
    words = line.split()
    if words and words[0] in _STATEMENT_KEYWORDS:
        return ""
    name = header.group("kotlin") or header.group("java") or ""
    return "" if name in _NOT_METHOD_NAMES else name


def _enclosing_method(lines: list[str], index: int) -> str:
    for position in range(index, -1, -1):
        name = _header_name(lines[position])
        if name:
            return name
    return ""


def enclosing_method_name(lines: list[str], index: int) -> str:
    """The method whose body holds line ``index`` (0-based), or ''."""
    return _enclosing_method(lines, index)


def _is_declaration(line: str, name: str) -> bool:
    return _header_name(line) == name


def _net_opens(text: str) -> int:
    return sum(text.count(c) for c in "([{") - sum(text.count(c) for c in ")]}")


def _returned_through(lines: list[str], index: int) -> str:
    """The method whose returned expression spans line `index`, if any.

    `return withContext(Dispatchers.Default) {` opens a block whose last
    expression is the method's result; a value used inside it is returned.
    """
    depth = 0
    for position in range(index - 1, max(-1, index - 1 - MAX_FOLLOW_LINES), -1):
        text = lines[position]
        depth += _net_opens(text)
        name = _header_name(text)
        if name:
            return name if depth > 0 and _EXPRESSION_BODY.search(text) else ""
        if _RETURN.match(text):
            return _enclosing_method(lines, position) if depth > 0 else ""
    return ""


_DESTRUCTURING = re.compile(
    r"\b(?:val|var)\s*\((?P<names>[^)]*)\)\s*(?::[^=]+)?=(?!=)"
)


def _assigned_names(line: str) -> tuple[list[str], int]:
    """Variables a declaration or assignment on `line` binds, and where its value starts."""
    destructuring = _DESTRUCTURING.search(line)
    if destructuring:
        names = [
            part.split(":")[0].strip()
            for part in destructuring.group("names").split(",")
        ]
        return [name for name in names if re.fullmatch(r"[A-Za-z_$][\w$]*", name)
                and name != "_"], destructuring.end()
    assignment = _ASSIGNMENT.search(line)
    if assignment:
        return [assignment.group("name")], assignment.end()
    return [], 0


# A Kotlin property whose value is computed: an extension getter
# (`val String.sha1: String` followed by `get() = sha(...)`) or a lazy
# delegate (`val id by lazy { url.sha1 }`). The value leaves through
# `receiver.name`, with no parentheses, which call sites never match.
_PROPERTY_HEADER = re.compile(
    r"\b(?:val|var)\s+(?:[\w$.<>?]+\.)?(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]*)?$")
_GETTER = re.compile(r"^\s*get\s*\(\s*\)\s*(?:=|\{)")
_LAZY_PROPERTY = re.compile(
    r"\b(?:val|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]+)?\s+by\s+lazy\b")


def _property_through(lines: list[str], index: int) -> str:
    """The Kotlin property whose computed value line `index` is, or ''."""
    lazy = _LAZY_PROPERTY.search(lines[index])
    if lazy:
        return lazy.group("name")
    if _GETTER.search(lines[index]) and index > 0:
        header = _PROPERTY_HEADER.search(lines[index - 1].strip())
        if header:
            return header.group("name")
    return ""


def _symbol_pattern(symbol: str, is_call: bool) -> re.Pattern:
    if symbol.startswith("."):
        # A property read, `filter.id`, as _property_through follows it.
        return re.compile(rf"\.\s*{re.escape(symbol[1:])}(?![\w$])(?!\s*\()")
    if is_call:
        return re.compile(rf"(?<![\w$]){re.escape(symbol)}\s*\(")
    return re.compile(rf"(?<![\w.]){re.escape(symbol)}(?![\w$])(?!\s*\()")


def _enclosing_call(statement: str, position: int) -> tuple[str, int] | None:
    """The call whose argument list holds `position`, and the argument's index."""
    depth = 0
    commas = 0
    for cursor in range(position - 1, -1, -1):
        char = statement[cursor]
        if char in ")]}":
            depth += 1
        elif char in "([{":
            if depth:
                depth -= 1
                continue
            if char != "(":
                return None
            callee = re.search(r"([A-Za-z_$][\w$]*)\s*(?:<[^<>()]*>)?\s*$",
                               statement[:cursor])
            if not callee or callee.group(1) in _NOT_METHOD_NAMES:
                return None
            return callee.group(1), commas
        elif char == "," and not depth:
            commas += 1
    return None


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, []
    for position, char in enumerate(text):
        if char in "([{<":
            depth += 1
        elif char in ")]}" or (char == ">" and text[position - 1:position] != "-"):
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


_PARAMETER_NOISE = re.compile(
    r"@[\w.]+(?:\([^)]*\))?|\b(?:val|var|vararg|final|private|protected|public"
    r"|internal|override|crossinline|noinline)\b"
)


def _parameter_list(statement: str, name: str) -> list[str] | None:
    opening = re.search(rf"(?<![\w$]){re.escape(name)}\s*\(", statement)
    if not opening:
        return None
    depth = 0
    start = opening.end()
    for cursor in range(start - 1, len(statement)):
        if statement[cursor] == "(":
            depth += 1
        elif statement[cursor] == ")":
            depth -= 1
            if depth == 0:
                return _split_top_level(statement[start:cursor])
    return None


def _parameter_names(statement: str, name: str) -> list[str]:
    """Parameter names of the method `name` declared in `statement`, in order."""
    names = []
    for parameter in _parameter_list(statement, name) or []:
        text = _PARAMETER_NOISE.sub(" ", parameter.split("=")[0]).strip()
        if ":" in text:  # Kotlin: name: Type
            found = re.findall(r"[A-Za-z_$][\w$]*", text.split(":")[0])
        else:  # Java: Type name
            found = re.findall(r"[A-Za-z_$][\w$]*", text.replace("...", " "))
        names.append(found[-1] if found else "")
    return names


# Dependency injection. A provider hands its value to whoever asks for the type.
_KOIN_PROVIDER = re.compile(
    r"\b(?:single|factory|scoped|viewModel|worker)\s*"
    r"(?:<\s*(?P<type>[\w$.]+)[^>]*>)?\s*(?:\([^)]*\)\s*)?\{\s*(?P<expression>[^}]*)"
)
_PROVIDER_ANNOTATION = re.compile(r"@(?:Provides|Binds|Bean|Produces)\b")
_PROVIDED_EXPRESSION_TYPE = re.compile(
    r"^\s*(?:new\s+)?(?:[a-z_][\w$]*\.)*(?P<type>[A-Z][\w$]*)\s*(?:\.\s*[\w$]+\s*)?\("
)
_KOTLIN_RETURN_TYPE = re.compile(r"\)\s*:\s*(?:[\w$]+\.)*(?P<type>[A-Za-z_$][\w$]*)")
_JAVA_RETURN_TYPE = re.compile(
    r"^\s*(?:[\w$]+\.)*(?P<type>[A-Za-z_$][\w$]*)(?:<[^>]*>)?(?:\[\])*\s+[\w$]+\s*\("
)
_JAVA_MODIFIERS = re.compile(
    r"@[\w.]+(?:\([^)]*\))?|\b(?:public|protected|private|static|final|abstract"
    r"|synchronized|open|internal|override|suspend|inline)\b"
)
_NOT_PROVIDED_TYPES = frozenset({"Unit", "void", "Void", "Any", "Object", "fun"})


def _provider_return_type(lines: list[str], header_index: int, name: str) -> str:
    annotated = any(
        _PROVIDER_ANNOTATION.search(lines[position])
        for position in range(max(0, header_index - 3), header_index + 1)
    )
    if not annotated:
        return ""
    statement = _statement(lines, header_index)
    if re.search(r"\bfun\b", statement):
        found = _KOTLIN_RETURN_TYPE.search(statement)
    else:
        found = _JAVA_RETURN_TYPE.match(_JAVA_MODIFIERS.sub(" ", statement))
    return found.group("type") if found else ""


def provided_types(lines: list[str], line: int = 0, method_name: str = "") -> list[str]:
    """Types the dependency-injection provider at `line` (or `method_name`) provides."""
    types: list[str] = []
    if 1 <= line <= len(lines):
        index = line - 1
        window = " ".join(lines[index:index + 3])
        koin = _KOIN_PROVIDER.search(lines[index]) or (
            _KOIN_PROVIDER.search(window) if lines[index].rstrip().endswith("{") else None
        )
        if koin:
            explicit = (koin.group("type") or "").split(".")[-1]
            constructed = _PROVIDED_EXPRESSION_TYPE.match(koin.group("expression"))
            types.append(explicit or (constructed.group("type") if constructed else ""))
        else:
            for position in range(index, max(-1, index - MAX_FOLLOW_LINES), -1):
                name = _header_name(lines[position])
                if name:
                    types.append(_provider_return_type(lines, position, name))
                    break
    elif method_name:
        for position, text in enumerate(lines):
            if _header_name(text) == method_name:
                types.append(_provider_return_type(lines, position, method_name))
    return [item for item in dict.fromkeys(types) if item and item not in _NOT_PROVIDED_TYPES]


def _injection_patterns(type_name: str) -> dict:
    qualified = rf"(?:[\w$]+\.)*{re.escape(type_name)}"
    return {
        # get<T>(), inject<T>(): the expression is the injected value.
        "lookup": re.compile(
            rf"(?<![\w$])(?:get|inject|getOrNull|injectOrNull)\s*<\s*{qualified}\s*>\s*\("
        ),
        # val x: T by inject() / val x by inject<T>() / val x: T = get()
        "property": re.compile(
            rf"\b(?:val|var)\s+(?P<name>[\w$]+)\s*"
            rf"(?::\s*{qualified}\??\s*(?:by\s+inject|=\s*get\s*\()"
            rf"|(?::[^=]+?)?\s+by\s+inject\s*<\s*{qualified}\s*>)"
        ),
        # @Inject lateinit var x: T / @Inject T x; / constructor parameters.
        "kotlin_typed": re.compile(
            rf"(?<![\w$])(?P<name>[A-Za-z_$][\w$]*)\s*:\s*{qualified}\b(?!\s*[<.])"
        ),
        "java_typed": re.compile(
            rf"(?<![\w$.]){qualified}\s+(?P<name>[A-Za-z_$][\w$]*)\s*[;,)=]"
        ),
    }


_INJECT_ANNOTATION = re.compile(r"@(?:Inject|Autowired)\b")


def injection_sites(lines_by_path: dict[str, list[str]], type_name: str) -> list[dict]:
    """Where an injected `type_name` enters the application's code.

    Each item is either an expression that is the value ({"kind": "lookup"})
    or a name the value is bound to ({"kind": "binding", "name"}), whose uses
    in the same file carry it.
    """
    patterns = _injection_patterns(type_name)
    koin_constructed = {
        constructed.group("type")
        for lines in lines_by_path.values() for text in lines
        for provider in [_KOIN_PROVIDER.search(text)] if provider
        for constructed in [_PROVIDED_EXPRESSION_TYPE.match(provider.group("expression"))]
        if constructed
    }
    sites: list[dict] = []
    for path, lines in lines_by_path.items():
        for index, text in enumerate(lines):
            if patterns["lookup"].search(text):
                sites.append({"path": path, "line": index + 1, "kind": "lookup",
                              "symbol": patterns["lookup"].search(text).group(0)
                              .rstrip("(").strip()})
            delegated = patterns["property"].search(text)
            if delegated:
                sites.append({"path": path, "line": index + 1, "kind": "binding",
                              "name": delegated.group("name")})
                continue
            annotated = bool(_INJECT_ANNOTATION.search(text)) or (
                index > 0 and bool(_INJECT_ANNOTATION.search(lines[index - 1]))
            )
            # A class a Koin provider constructs receives the value through
            # its primary constructor, without any annotation.
            constructor = re.search(r"\bclass\s+(?P<cls>[\w$]+)[^({]*\(", text)
            koin_class = bool(constructor and constructor.group("cls") in koin_constructed)
            if not (annotated or koin_class):
                continue
            statement = _statement(lines, index)
            for pattern in (patterns["kotlin_typed"], patterns["java_typed"]):
                for match in pattern.finditer(statement):
                    sites.append({"path": path, "line": index + 1, "kind": "binding",
                                  "name": match.group("name")})
    unique = {}
    for site in sites:
        unique.setdefault((site["path"], site["line"], site.get("symbol") or site.get("name")), site)
    return list(unique.values())


_TEST_SOURCE = re.compile(r"(?:^|/)src/(?:test|androidTest|sharedTest|testFixtures)\w*/")
_TYPE_DECLARATION = re.compile(r"\b(?:class|object|interface)\s+(?P<name>[A-Za-z_$][\w$]*)")


def _declaring_type(lines: list[str], index: int) -> str:
    """The nearest class, object or interface declared above line `index`."""
    for position in range(index, -1, -1):
        found = _TYPE_DECLARATION.search(lines[position])
        if found:
            return found.group("name")
    return ""


def _call_receiver(line: str, position: int) -> str:
    """The type a call at `position` is qualified with (`Uri` in Uri.parse), or ''.

    Lower-case receivers are instances, whose type a lexical reading cannot
    know, so they count as unqualified.
    """
    receiver = re.search(r"([A-Za-z_$][\w$]*)\s*\.\s*$", line[:position])
    if not receiver or not receiver.group(1)[0].isupper():
        return ""
    return receiver.group(1)


def derived_value_consumers(repo_root: Path, callers: list, method_name: str,
                            source_cache: dict | None = None,
                            source_path: str = "", source_line: int = 0) -> dict:
    """Consumers of `method_name`'s return value, reached from `callers`.

    `callers` are resolved call sites ({"path", "line"}), as the semantic
    resolution records them. `source_path`/`source_line` locate the reported
    statement; when it is a dependency-injection provider, the injection sites
    of the provided type are followed too.
    """
    root = Path(repo_root)
    if source_cache is None:
        source_cache = build_repository_index(root)["source_cache"]
    lines_by_path: dict[str, list[str]] = {}
    for path, content in source_cache.items():
        try:
            relative = Path(path).relative_to(root).as_posix()
        except ValueError:
            continue
        # A test's fixture file name is not where the app stores the value.
        if _TEST_SOURCE.search(relative):
            continue
        lines_by_path[relative] = str(content).splitlines()

    def call_sites(name: str) -> list[tuple[str, int]]:
        pattern = re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
        owners = {
            owner for path, index in declarations(name)
            for owner in [_declaring_type(lines_by_path[path], index)] if owner
        }
        found = []
        for relative, lines in lines_by_path.items():
            for number, line in enumerate(lines, 1):
                if _is_declaration(line, name):
                    continue
                # Uri.parse(...) is not a call of the repository's own parse().
                if any(
                    not owners
                    or _call_receiver(line, match.start()) in owners | {"", "Companion"}
                    for match in pattern.finditer(line)
                ):
                    found.append((relative, number))
        return found

    declarations_cache: dict[str, list[tuple[str, int]]] = {}

    def declarations(name: str) -> list[tuple[str, int]]:
        if name not in declarations_cache:
            declarations_cache[name] = [
                (relative, index)
                for relative, lines in lines_by_path.items()
                for index, line in enumerate(lines)
                if name in line and _header_name(line) == name
            ]
        return declarations_cache[name]

    def property_reads(name: str, path: str, index: int) -> list[tuple[str, int]]:
        """Lines reading `.name`. A short or common name (`id`) is read only
        in files that mention the type declaring the property."""
        owner = _declaring_type(lines_by_path.get(path) or [], index)
        pattern = _symbol_pattern(f".{name}", False)
        needs_owner = len(name) <= 4 and owner
        owner_word = re.compile(rf"\b{re.escape(owner)}\b") if owner else None
        found = []
        for relative, lines in lines_by_path.items():
            if needs_owner and not owner_word.search("\n".join(lines)):
                continue
            for number, line in enumerate(lines, 1):
                if (relative, number - 1) != (path, index) and pattern.search(line):
                    found.append((relative, number))
                    if len(found) >= MAX_CONSUMERS:
                        return found
        return found

    def uses_after(path: str, start: int, symbol: str, stop_at_method: bool,
                   limit: int = MAX_FOLLOW_LINES) -> list[int]:
        lines = lines_by_path.get(path) or []
        usage = _symbol_pattern(symbol, False)
        found = []
        for follow in range(start, min(len(lines), start + limit)):
            if stop_at_method and _header_name(lines[follow]):
                break
            if usage.search(lines[follow]):
                found.append(follow + 1)
        return found

    # (path, line, symbol, how it got there, depth, symbol is a call)
    queue = [
        (str(item.get("path", "")), int(item.get("line") or 0), method_name,
         "resolved call site", 0, True)
        for item in callers or [] if isinstance(item, dict)
    ]
    injection: dict = {}
    source_relative = str(source_path or "").replace("\\", "/")
    if source_relative and source_relative not in lines_by_path:
        source_relative = next(
            (path for path in lines_by_path if path.endswith("/" + source_relative)
             or source_relative.endswith("/" + path)), "",
        )
    if source_relative:
        types = provided_types(lines_by_path[source_relative], int(source_line or 0),
                               "" if source_line else method_name)
        sites = [site for type_name in types
                 for site in injection_sites(lines_by_path, type_name)]
        if types:
            injection = {"provided_types": types, "injection_sites": len(sites)}
        for site in sites:
            via = f"injected as {', '.join(types)} by a dependency-injection provider"
            if site["kind"] == "lookup":
                queue.append((site["path"], site["line"], site["symbol"], via, 1, True))
                continue
            whole_file = uses_after(site["path"], 0, site["name"], False,
                                    limit=len(lines_by_path.get(site["path"]) or []))
            for line in whole_file:
                if line != site["line"]:
                    queue.append((site["path"], line, site["name"],
                                  f"{via}, bound to {site['name']}", 1, False))
        # The value can be consumed inside the reported method itself, which
        # then returns nothing: rssreader's writeFile is void and writes the
        # digest it derives straight into the backup. Follow the variable the
        # reported statement assigns, and the local object it calls on (the
        # detector reports rssreader's at `digester.update(...)`, not at its
        # creation), within its method.
        lines = lines_by_path[source_relative]
        if 1 <= int(source_line or 0) <= len(lines):
            index = int(source_line) - 1
            variables, _ = _assigned_names(lines[index])
            # Only locals assigned earlier in the same method: a parameter
            # (`content.getBytes()`) is the input, not the derived value.
            assigned_above = set()
            for position in range(index - 1, max(-1, index - 1 - MAX_FOLLOW_LINES), -1):
                if _header_name(lines[position]):
                    break
                assigned_above.update(_assigned_names(lines[position])[0])
            variables += [name for name in _LOCAL_RECEIVER.findall(lines[index])
                          if name in assigned_above and name not in variables]
            for variable in variables:
                for line in uses_after(source_relative, index + 1, variable, True):
                    queue.append((source_relative, line, variable,
                                  f"assigned to {variable} in the reported statement", 1, False))

    consumers: list[dict] = []
    visited: set[tuple[str, int, str]] = set()
    while queue and len(visited) < MAX_VISITS and len(consumers) < MAX_CONSUMERS:
        path, line, symbol, via, depth, is_call = queue.pop(0)
        key = (path, line, symbol)
        if key in visited:
            continue
        visited.add(key)
        lines = lines_by_path.get(path) or []
        if not 1 <= line <= len(lines):
            continue
        index = line - 1
        statement = _statement(lines, index)
        kind, basis = classify_statement(statement)
        if kind:
            consumers.append({
                "path": path, "line": line, "statement": statement[:240],
                "kind": kind, "basis": basis, "reached_through": via,
            })
        if depth >= MAX_DEPTH:
            continue
        expression_body = _EXPRESSION_BODY.search(lines[index])
        outer = ""
        if expression_body:
            outer = expression_body.group("name")
        elif _RETURN.match(lines[index]):
            outer = _enclosing_method(lines, index)
        else:
            outer = _returned_through(lines, index)
        if outer and outer != symbol:
            for site_path, site_line in call_sites(outer):
                queue.append((site_path, site_line, outer,
                              f"returned through {outer}()", depth + 1, True))
        prop = _property_through(lines, index)
        if prop and f".{prop}" != symbol:
            for site_path, site_line in property_reads(prop, path, index):
                queue.append((site_path, site_line, f".{prop}",
                              f"read through property {prop}", depth + 1, False))
        variables, value_start = _assigned_names(lines[index])
        symbol_here = _symbol_pattern(symbol, is_call)
        if variables and symbol_here.search(lines[index][value_start:]):
            for variable in variables:
                if variable == symbol:
                    continue
                for follow_line in uses_after(path, index + 1, variable, True):
                    queue.append((path, follow_line, variable,
                                  f"assigned to {variable}", depth + 1, False))
        # Passed as an argument: follow the parameter inside the callee.
        for occurrence in symbol_here.finditer(statement):
            call = _enclosing_call(statement, occurrence.start())
            if not call or call[0] == symbol:
                continue
            callee, argument = call
            targets = declarations(callee)
            if len(targets) > MAX_DECLARATIONS:
                # A common name (WiFiAnalyzer's size()) is resolved by the
                # receiver: graphWrapper.size(...) is GraphWrapper's.
                receiver = re.search(
                    rf"([A-Za-z_$][\w$]*)\s*\.\s*{re.escape(callee)}\s*\(", statement)
                owner = receiver.group(1).lower() if receiver else ""
                targets = [
                    (target_path, target_index) for target_path, target_index in targets
                    if owner and _declaring_type(lines_by_path[target_path],
                                                 target_index).lower() == owner
                ]
            if not targets or len(targets) > MAX_DECLARATIONS:
                continue
            for target_path, target_index in targets:
                target_lines = lines_by_path[target_path]
                declaration = _statement(target_lines, target_index)
                parameters = _parameter_names(declaration, callee)
                if argument >= len(parameters) or not parameters[argument]:
                    continue
                parameter = parameters[argument]
                follow = uses_after(target_path, target_index + 1, parameter, True)
                # An expression body on the declaration's own line uses the
                # parameter there: fun size(value: Int) = if (value == TYPE1) ...
                body = _EXPRESSION_BODY.search(declaration)
                if body and _symbol_pattern(parameter, False).search(declaration[body.end():]):
                    follow = [target_index + 1, *follow]
                for follow_line in follow:
                    queue.append((target_path, follow_line, parameter,
                                  f"passed as {parameter} to {callee}()", depth + 1, False))
    result = {
        "status": "classified" if consumers else "no_classified_consumer",
        "method": method_name,
        "consumers": consumers,
        "persisted": any(item["kind"] == "persistence" for item in consumers),
        "transmitted": any(item["kind"] == "network" for item in consumers),
        "fixed": any(item["kind"] == "fixed_value" for item in consumers),
        "bound": (
            "Lexical: follows the returned value from resolved call sites, and "
            "from the injection sites of a dependency-injection provider, "
            "through returns, local assignments and arguments, up to five "
            "hops. A consumer it recognises is evidence; one it does not "
            "recognise is not evidence that the value stays in memory."
        ),
    }
    if injection:
        result["dependency_injection"] = injection
    return result
