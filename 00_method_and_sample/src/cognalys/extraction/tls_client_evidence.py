"""Which client carries a TLS context's connections, and which protocols it allows.

An SSLContext finding is about the context, but the protocols a connection
may negotiate are decided by the client that uses it. zSMTH (2026-10-17)
hands its context to OkHttp 4.12.0, whose default connection spec has offered
only TLS 1.2 and 1.3 since OkHttp 3.13 dropped TLS 1.0 and 1.1; filtering the
socket's protocols there would add code that changes nothing. A client
configured with OkHttp's COMPATIBLE_TLS, an OkHttp older than 3.13, or a raw
SSLSocketFactory / HttpsURLConnection does keep the deprecated versions
reachable, and there removing them is a real change.

This module reads those facts from the reviewed class and the repository's
Gradle files. It never pins a version list: the remedy it enables removes
deprecated names and keeps whatever newer versions the provider offers.
"""
from __future__ import annotations

import re
from pathlib import Path

# OkHttp 3.13 (2019-02) removed TLS 1.0 and 1.1 from MODERN_TLS, its default.
OKHTTP_MODERN_DEFAULT = (3, 13)
_GRADLE_SUFFIXES = (".gradle", ".kts", ".toml", ".properties")
_MAX_FILES = 200

_OKHTTP_COORDINATE = re.compile(
    r"com\.squareup\.okhttp3?:okhttp(?:-bom)?:(?P<version>[^\"'\s)]+)")
_VARIABLE = re.compile(r"^\$\{?(?:rootProject\.(?:ext\.)?|project\.)?(?P<name>[\w.]+)\}?$")
_TOML_LIBRARY = re.compile(
    r"com\.squareup\.okhttp3?:okhttp\"[^\n]*?version(?:\.ref)?\s*=\s*\"(?P<value>[^\"]+)\"")
_TOML_MODULE_REF = re.compile(
    r"(?:module\s*=\s*\"com\.squareup\.okhttp3?:okhttp\""
    # Read You: { group = "com.squareup.okhttp3", name = "okhttp", version.ref = "okhttp" }
    r"|group\s*=\s*\"com\.squareup\.okhttp3?\"\s*,\s*name\s*=\s*\"okhttp\")"
    r"[^\n]*?version\.ref\s*=\s*\"(?P<ref>[^\"]+)\"")

# Which configuration keeps deprecated protocols reachable depends on the
# client, so the predicate is split by client kind (2026-10-29 review).
#
# OkHttp: its ConnectionSpec intersects every socket's enabled protocols with
# the spec's TLS versions, so only a spec naming deprecated versions counts --
# COMPATIBLE_TLS, a deprecated TlsVersion, or tlsVersions("TLSv1"). A quoted
# protocol anywhere else (a socket's enabled list) is overridden by the spec.
_OKHTTP_LEGACY_SPEC = re.compile(
    r"\bConnectionSpec\s*\.\s*COMPATIBLE_TLS\b|\bTlsVersion\s*\.\s*(?:TLS_1_0|TLS_1_1|SSL_3_0)\b")
_OKHTTP_TLS_VERSIONS = re.compile(r"\btlsVersions\s*\(")
_LEGACY_PROTOCOL_LITERAL = re.compile(r"[\"'](?:SSLv2|SSLv3|TLSv1|TLSv1\.0|TLSv1\.1)[\"']")
# Platform sockets: a deprecated name counts only where it reaches an
# effective sink in the same statement. Fread (tls_20261015) assigned "SSLv3"
# to sslContext.defaultSSLParameters.protocols -- a copy, so a no-op -- and
# that line alone made a client on OkHttp 4.12.0 read as reachable.
_ENABLED_PROTOCOLS_SINK = re.compile(
    r"\bsetEnabledProtocols\s*\(|(?:\.|^|[;{(])\s*enabledProtocols\s*=(?!=)",
    re.MULTILINE)
_PARAMETERS_PROTOCOLS = re.compile(
    r"(?P<receiver>[\w$.()]*?)\s*\.\s*(?:setProtocols\s*\(|protocols\s*=(?!=))"
    r"|(?:^|[;{])\s*(?:setProtocols\s*\(|protocols\s*=(?!=))", re.MULTILINE)
_DEFAULT_PARAMETERS = re.compile(
    r"(?:defaultSSLParameters|getDefaultSSLParameters\s*\(\s*\)"
    r"|supportedSSLParameters|getSupportedSSLParameters\s*\(\s*\))")
# The bracket a literal sits in, named by the call that opens it: these
# build the list of protocols to enable. A literal in filter { it != "TLSv1" }
# or minus("TLSv1") is what the code removes.
_ENABLING_CALLS = frozenset({
    "arrayOf", "listOf", "setOf", "mutableListOf", "mutableSetOf", "arrayListOf",
    "hashSetOf", "asList", "of", "plus", "add", "addAll", "union",
})
_TEST_SOURCE = re.compile(r"(?:^|/)src/\w*[Tt]est\w*/|(?:^|/)tests?/")
_STRING_OR_COMMENT = re.compile(
    r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*[\s\S]*?\*/')
_CONNECTION_SPEC = re.compile(r"\bconnectionSpecs\s*\(|\bConnectionSpec\b")
_RAW_CLIENT = re.compile(r"\bHttpsURLConnection\b|\bSSLSocket\b|\bcreateSocket\s*\(")
_OKHTTP_CLIENT = re.compile(r"\bOkHttpClient\b")
# OkHttp's builder takes the factory with its trust manager; the platform's
# HttpsURLConnection.setSSLSocketFactory takes the factory alone.
_OKHTTP_SOCKET_FACTORY = re.compile(r"\bsslSocketFactory\s*\([^,()]+,\s*[^,()]+\)")


def _version_tuple(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(text or ""))[:3]
    return tuple(int(value) for value in numbers)


def _gradle_files(repository: Path) -> list[Path]:
    found = []
    for path in repository.rglob("*"):
        if len(found) >= _MAX_FILES:
            break
        if (path.suffix in _GRADLE_SUFFIXES and path.is_file()
                and not any(part in {"build", ".gradle", ".git", "node_modules"}
                            for part in path.relative_to(repository).parts)):
            found.append(path)
    return found


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:300_000]
    except OSError:
        return ""


def okhttp_version(repository: Path) -> str:
    """The lowest OkHttp version the build declares, or "" when none is literal."""
    texts = [_read(path) for path in _gradle_files(repository)]
    joined = "\n".join(texts)
    candidates = []
    for match in _OKHTTP_COORDINATE.finditer(joined):
        candidates.append(match.group("version"))
    for match in _TOML_LIBRARY.finditer(joined):
        candidates.append(match.group("value"))
    for match in _TOML_MODULE_REF.finditer(joined):
        candidates.append("$" + match.group("ref"))
    versions = []
    for candidate in candidates:
        variable = _VARIABLE.match(candidate)
        if variable:
            name = re.escape(variable.group("name").rsplit(".", 1)[-1])
            value = re.search(rf"\b{name}\s*[=:]?\s*[\"']?(\d+(?:\.\d+)+(?:-[\w.]+)?)", joined)
            candidate = value.group(1) if value else ""
        if _version_tuple(candidate):
            versions.append(candidate)
    return min(versions, key=_version_tuple) if versions else ""


def _sdk_value(text: str, gradle_text: str, key: str) -> int | None:
    found = re.search(rf"\b{key}(?:Version)?\s*(?:=\s*)?([\w.()]+)", text)
    if not found:
        return None
    value = found.group(1)
    if value.isdigit():
        return int(value)
    # "minSdkVersion rootProject.minSdkVersion" / "libs.versions.minSdk.get()":
    # the number is declared elsewhere in the build under the last name part.
    name = re.escape(re.sub(r"\.get\(\)$", "", value).rsplit(".", 1)[-1])
    declared = re.search(rf"\b{name}\s*[=:]\s*[\"']?(\d+)", gradle_text)
    return int(declared.group(1)) if declared else None


def module_sdk(repository: Path, source_file_path: str, key: str = "minSdk") -> int | None:
    """minSdk (or targetSdk) of the module holding the source file. A library
    module bundled in the repository may declare one that does not apply, so
    another module's value is used only for minSdk, and then the lowest."""
    path = str(source_file_path or "").replace("\\", "/")
    module = path.split("/src/", 1)[0] if "/src/" in path else ""
    files = _gradle_files(repository)
    gradle_text = "\n".join(_read(item) for item in files)
    for name in ("build.gradle", "build.gradle.kts"):
        if module:
            value = _sdk_value(_read(repository / module / name), gradle_text, key)
            if value:
                return value
    if key != "minSdk":
        return None
    values = [value for value in (_sdk_value(_read(item), gradle_text, key) for item in files)
              if value]
    return min(values) if values else None


def module_min_sdk(repository: Path, source_file_path: str) -> int | None:
    return module_sdk(repository, source_file_path, "minSdk")


def _client_kind(text: str) -> str:
    if _OKHTTP_CLIENT.search(text) or _OKHTTP_SOCKET_FACTORY.search(text):
        return "okhttp"
    return "platform" if _RAW_CLIENT.search(text) else ""


def _without_comments(text: str) -> str:
    """Comments blanked (offsets kept), string literals left as they are."""
    return _STRING_OR_COMMENT.sub(
        lambda match: match.group(0) if match.group(0)[0] in "\"'"
        else re.sub(r"[^\n]", " ", match.group(0)), str(text or ""))


def _string_end(text: str, start: int) -> int:
    quote = text[start]
    index = start + 1
    while index < len(text) and text[index] != quote and text[index] != "\n":
        index += 2 if text[index] == "\\" else 1
    return index


def _statement(text: str, start: int) -> str:
    """From `start` to the end of its statement: a `;`, the bracket that
    closes an enclosing block, or a line break that does not continue it."""
    depth, index = 0, start
    while index < len(text):
        character = text[index]
        if character in "\"'":
            index = _string_end(text, index) + 1
            continue
        if character in "([{":
            depth += 1
        elif character in ")]}":
            if depth == 0:
                return text[start:index]
            depth -= 1
        elif character == ";" and depth == 0:
            return text[start:index]
        elif character == "\n" and depth == 0:
            before = text[start:index].rstrip()
            after = text[index:].lstrip()
            if not (before.endswith(("=", "(", ",", "+", ".", "->", "{"))
                    or after.startswith((".", "+", "?:", "?."))):
                return text[start:index]
        index += 1
    return text[start:]


def _enabling_literal(statement: str, position: int) -> bool:
    """Whether the literal at `position` is an element of what is enabled.

    Read from the innermost bracket around it: arrayOf("TLSv1"),
    new String[]{"TLSv1"} and `+ "TLSv1"` enable it; filter { it != "TLSv1" }
    and minus("TLSv1") take it out.
    """
    stack, index = [], 0
    while index < position:
        character = statement[index]
        if character in "\"'":
            index = _string_end(statement, index) + 1
            continue
        if character in "([{":
            stack.append(index)
        elif character in ")]}" and stack:
            stack.pop()
        index += 1
    if not stack:
        return bool(re.search(r"(?:\+|\+=|=)\s*$", statement[:position]))
    opening = stack[-1]
    before = statement[:opening].rstrip()
    if statement[opening] == "(":
        name = re.search(r"([A-Za-z_$][\w$]*)\s*(?:<[^<>()]*>)?$", before)
        return bool(name) and (name.group(1) in _ENABLING_CALLS
                               or name.group(1) in {"setEnabledProtocols", "setProtocols"})
    if statement[opening] == "{":
        # A Java array initializer: new String[] { ... } or String[] p = { ... }.
        return before.endswith("]") or bool(re.search(r"\[\s*\]\s*\w*\s*=\s*$", before))
    return False


def _legacy_literal_enabled(statement: str) -> bool:
    return any(_enabling_literal(statement, match.start())
               for match in _LEGACY_PROTOCOL_LITERAL.finditer(statement))


def _applies_parameters(code: str, position: int) -> bool:
    """Whether a receiverless `protocols =` sits in an SSLParameters(...)
    block whose object is given to a socket or engine."""
    depth = 0
    for index in range(position - 1, -1, -1):
        character = code[index]
        if character == "}":
            depth += 1
        elif character == "{":
            if depth:
                depth -= 1
                continue
            head = code[max(0, index - 300):index]
            block = re.search(r"SSLParameters\s*\([^()]*\)\s*\.\s*(?:apply|also|run)\s*$", head)
            if not block:
                return False
            lead = head[:block.start()]
            if re.search(r"(?:sslParameters\s*=|setSSLParameters\s*\()\s*$", lead):
                return True
            variable = re.search(r"\b(?:val|var)\s+(\w+)\s*(?::\s*SSLParameters\s*)?=\s*$", lead)
            return bool(variable) and _parameters_given(code, variable.group(1))
    return False


def _parameters_given(code: str, variable: str) -> bool:
    name = re.escape(variable)
    return bool(re.search(rf"\bsetSSLParameters\s*\(\s*{name}\s*\)|\bsslParameters\s*=\s*{name}\b",
                          code))


def platform_legacy_sinks(text: str) -> list[str]:
    """Statements that enable a deprecated protocol on a platform socket.

    A deprecated name counts only at an effective sink in the same statement:
    setEnabledProtocols / `enabledProtocols =`, or setProtocols /
    `protocols =` on an SSLParameters the code then gives to
    setSSLParameters / `sslParameters =`. Never an assignment through
    defaultSSLParameters / getDefaultSSLParameters(): they return a copy,
    and the assignment configures nothing.
    """
    code = _without_comments(text)
    sinks = []
    for match in _ENABLED_PROTOCOLS_SINK.finditer(code):
        start = match.start() + re.search(r"\w", match.group(0)).start()
        statement = _statement(code, start)
        if _legacy_literal_enabled(statement):
            sinks.append(statement.strip())
    for match in _PARAMETERS_PROTOCOLS.finditer(code):
        receiver = (match.group("receiver") or "").strip()
        if _DEFAULT_PARAMETERS.search(receiver):
            continue
        start = match.start() + re.search(r"(?:setProtocols|protocols)\s*[(=]$",
                                          match.group(0)).start()
        if receiver:
            variable = re.findall(r"[A-Za-z_$][\w$]*", receiver)
            if not variable or not _parameters_given(code, variable[-1]):
                continue
        elif not _applies_parameters(code, start):
            continue
        statement = _statement(code, start)
        if _legacy_literal_enabled(statement):
            sinks.append(statement.strip())
    return list(dict.fromkeys(sinks))


def okhttp_legacy_sinks(text: str) -> list[str]:
    """OkHttp connection specs that keep deprecated protocols: COMPATIBLE_TLS,
    a deprecated TlsVersion, or tlsVersions(...) naming one. A quoted protocol
    elsewhere does not count: the spec intersects every socket's protocols."""
    code = _without_comments(text)
    sinks = [match.group(0) for match in _OKHTTP_LEGACY_SPEC.finditer(code)]
    for match in _OKHTTP_TLS_VERSIONS.finditer(code):
        statement = _statement(code, match.start())
        if _LEGACY_PROTOCOL_LITERAL.search(statement):
            sinks.append(statement.strip())
    return list(dict.fromkeys(sinks))


def legacy_protocol_sinks(text: str, client: str = "") -> list[str]:
    """The effective deprecated-protocol configuration in `text` for a client
    kind ("okhttp", "platform"); both predicates when the kind is unknown."""
    client = str(client or "").lower()
    if client.startswith("okhttp"):
        return okhttp_legacy_sinks(text)
    if client.startswith("platform"):
        return platform_legacy_sinks(text)
    return okhttp_legacy_sinks(text) + platform_legacy_sinks(text)


def protocol_configuration_sites(repository: Path, client: str = "okhttp") -> list[str]:
    """Sources anywhere in the repository that turn deprecated protocols back
    on for this kind of client (COMPATIBLE_TLS, TLS_1_0/TLS_1_1 for OkHttp).
    openHAB's maintainer (2026-10-18): "We don't override connectionSpecs
    anywhere, so the default applies" -- the claim is about the repository,
    not the reviewed class."""
    sites = []
    for count, path in enumerate(repository.rglob("*")):
        if count > 50_000 or len(sites) >= 10:
            break
        if path.suffix not in {".kt", ".java"} or not path.is_file():
            continue
        parts = path.relative_to(repository).parts
        if any(part in {"build", ".git", "test", "androidTest"} for part in parts):
            continue
        # Only a configuration that turns deprecated versions back on counts:
        # connectionSpecs(listOf(MODERN_TLS)) restricts as much as the default.
        if legacy_protocol_sinks(_read(path), client):
            sites.append(path.relative_to(repository).as_posix())
    return sites


def tls_client_evidence(error: dict) -> dict:
    """The client the reviewed method hands its TLS context to, and whether
    that client allows deprecated TLS versions."""
    repository = Path(str(error.get("repository_path", "")))
    method = str(error.get("method_body", ""))
    source = method
    try:
        source = (repository / str(error.get("source_file_path", ""))).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        pass
    if not source or not repository.is_dir():
        return {}
    # The method decides first: openHAB's ConnectionFactory gives one context
    # to OkHttp (httpClient.newBuilder().sslSocketFactory(factory, manager))
    # and another, elsewhere in the class, to HttpsURLConnection.
    kind = _client_kind(method) or _client_kind(source)
    if not kind:
        return {}
    # The whole class, the reported method included: a COMPATIBLE_TLS kept in
    # the method still counts.
    legacy = bool(legacy_protocol_sinks(source, kind))
    evidence: dict = {"protocol_configuration_in_class": bool(_CONNECTION_SPEC.search(source)),
                      "legacy_protocols_configured": legacy}
    if kind == "okhttp":
        version = okhttp_version(repository)
        evidence["http_client"] = {"library": "OkHttp", "version": version}
        modern = bool(version) and _version_tuple(version) >= OKHTTP_MODERN_DEFAULT
        sites = protocol_configuration_sites(repository, kind) if modern else []
        if sites:
            evidence["protocol_configuration_sites"] = sites
        evidence["client_excludes_deprecated_protocols"] = (
            modern and not legacy and not sites)
    else:
        evidence["http_client"] = {"library": "platform socket", "version": ""}
        evidence["client_excludes_deprecated_protocols"] = False
    evidence["deprecated_protocols_reachable"] = (
        legacy or not evidence["client_excludes_deprecated_protocols"])
    evidence["maintainer_facts"] = protocol_facts(evidence)
    conditions = {name: FACT_CONDITIONS[text]
                  for name, text in evidence["maintainer_facts"].items() if text in FACT_CONDITIONS}
    if conditions:
        evidence["fact_conditions"] = conditions
    return evidence


PROTOCOL_REMOVAL_FACT = (
    "SSL 3.0, TLS 1.0 and TLS 1.1 are removed from the protocols each socket "
    "enables; TLS 1.2, 1.3 and any newer version stay enabled.")
# A fact true only of a patch that does what it says, by the predicate that
# checks it in the final diff (migration_policy.facts_supported_by_diff).
# Keyed by the sentence too, so a policy stored before conditions were
# recorded is read the same way.
DIFF_REMOVES_DEPRECATED_PROTOCOLS = "diff_removes_deprecated_protocols"
FACT_CONDITIONS = {PROTOCOL_REMOVAL_FACT: DIFF_REMOVES_DEPRECATED_PROTOCOLS}


def protocol_facts(evidence: dict) -> dict:
    client = evidence.get("http_client") or {}
    if evidence.get("client_excludes_deprecated_protocols"):
        return {"client_protocols": (
            f"OkHttp {client.get('version')} already negotiates only TLS 1.2 and 1.3 "
            "by default, so no protocol filter was added.")}
    return {"client_protocols": PROTOCOL_REMOVAL_FACT}


def _patch_code(edits, side: str) -> str:
    """Production code of the patch (created files included, tests left out)."""
    return "\n".join(
        str(item.get(side, "")) for item in edits or [] if isinstance(item, dict)
        and str(item.get("edit_kind", "")) != "test"
        and not _TEST_SOURCE.search(str(item.get("source_file_path")
                                        or item.get("source_file") or "")))


def added_protocol_filters(edits) -> int:
    """How many socket protocol sinks (setEnabledProtocols / enabledProtocols =)
    the patch's production code adds."""
    def count(code: str) -> int:
        return len(_ENABLED_PROTOCOLS_SINK.findall(_without_comments(code)))
    return count(_patch_code(edits, "corrected_code")) - count(_patch_code(edits, "original_code"))


_ENABLED_PROTOCOLS_MENTION = re.compile(r"\b(?:get|set)?[Ee]nabledProtocols\b")


def added_protocol_filter_code(edits) -> int:
    """How many reads or writes of a socket's enabled protocols the patch's
    production code adds: a protocol filter reads the list and sets it back,
    a selector-only patch touches neither."""
    def count(code: str) -> int:
        return len(_ENABLED_PROTOCOLS_MENTION.findall(_without_comments(code)))
    return count(_patch_code(edits, "corrected_code")) - count(_patch_code(edits, "original_code"))


def diff_removes_deprecated_protocols(edits) -> bool:
    """Whether the final diff takes deprecated protocols out where connections
    are made: a socket filter it adds that enables none of them, or an OkHttp
    spec naming them that it replaces."""
    original = _patch_code(edits, "original_code")
    corrected = _patch_code(edits, "corrected_code")
    if added_protocol_filters(edits) > 0 and not platform_legacy_sinks(corrected):
        return True
    return bool(okhttp_legacy_sinks(original)) and not okhttp_legacy_sinks(corrected)
