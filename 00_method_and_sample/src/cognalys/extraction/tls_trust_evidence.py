"""What a trust-all TLS client actually connects to, and the root that would replace it.

zSMTH (2026-10-17) builds its only HTTP client with a TrustManager that
accepts any certificate and a HostnameVerifier that accepts any host. Its
servers' certificates are valid -- but they chain to ISRG Root X2 through
Let's Encrypt's newest hierarchy, which Android only trusts in recent
releases, while the app supports API 21. Replacing the trust-all with the
platform's default validation (the pipeline's first patch) would have broken
every older device; leaving it leaves every connection open to interception.
The substantive fix trusts the platform store *plus* the servers' own root,
bundled with the app, and keeps hostname verification.

This module gathers the facts that fix needs, from the code and the network:
whether the code disables certificate or hostname checks, which HTTPS hosts
it hard-codes, and, for each, the self-signed root its chain leads to
(following Authority Information Access links past what the server sends).
A host whose root cannot be resolved makes the evidence incomplete, and no
trust change is proposed. Network reads are bounded by timeouts and cached
per host.
"""
from __future__ import annotations

import re
import subprocess
import urllib.request
from functools import lru_cache
from pathlib import Path

TIMEOUT = 20
MAX_CHAIN_STEPS = 4

_EMPTY_SERVER_CHECK = re.compile(
    r"checkServerTrusted\s*\([^)]*\)\s*(?:throws\s+[\w.,\s]+)?"
    r"(?:\{\s*(?://[^\n]*\s*)*\}|=\s*Unit\b|:\s*Unit\s*\{\s*\})")
_ANY_HOSTNAME = re.compile(
    r"\{\s*_\s*,\s*_\s*->\s*true\s*\}"
    r"|verify\s*\([^)]*\)\s*(?::\s*Boolean\s*)?\{\s*return\s+true\s*;?\s*\}"
    r"|verify\s*\([^)]*\)\s*(?::\s*Boolean\s*)?=\s*true\b"
    r"|ALLOW_ALL_HOSTNAME_VERIFIER|NoopHostnameVerifier")
_HTTPS_HOST = re.compile(r"\"https://([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?::\d+)?[/\"]")
_PEM = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.DOTALL)
_CA_ISSUERS = re.compile(r"CA Issuers - URI:(\S+)")


def trust_overrides(source: str) -> dict:
    return {"trust_all": bool(_EMPTY_SERVER_CHECK.search(source or "")),
            "any_hostname": bool(_ANY_HOSTNAME.search(source or ""))}


def hardcoded_hosts(source: str) -> list[str]:
    return sorted({match.group(1).lower() for match in _HTTPS_HOST.finditer(source or "")})


def _openssl(args: list[str], data: bytes | None = None) -> str:
    result = subprocess.run(["openssl", *args], input=data, capture_output=True,
                            timeout=TIMEOUT)
    return result.stdout.decode("utf-8", errors="replace") if result.returncode == 0 else ""


def _field(pem: str, name: str) -> str:
    return _openssl(["x509", "-noout", f"-{name}", "-nameopt", "RFC2253"], pem.encode()).strip()


def _download_certificate(url: str) -> str:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        body = response.read(200_000)
    if b"-----BEGIN CERTIFICATE-----" in body:
        found = _PEM.search(body.decode("ascii", errors="replace"))
        return found.group(0) if found else ""
    return _openssl(["x509", "-inform", "DER", "-outform", "PEM"], body).strip()


_UNRESOLVABLE = re.compile(r"Name or service not known|nodename nor servname|"
                           r"No address associated|getaddrinfo|Temporary failure in name resolution")


@lru_cache(maxsize=None)
def resolve_root(host: str) -> dict:
    """The anchor that closes `host`'s chain on any device, verified.

    The anchor is the first certificate the server does not send: the issuer
    of the last one it does (or that one, when it is self-signed). Bundled as
    a trust anchor it completes the path whatever the device's store holds.
    zSMTH's chain ends in "Root YE" signed by ISRG Root X2; following issuer
    links further reaches ISRG Root X1, but the X2-by-X1 link is never sent,
    so a device holding only X1 still could not build the path.
    """
    try:
        result = subprocess.run(
            ["openssl", "s_client", "-connect", f"{host}:443", "-servername", host,
             "-showcerts"], input=b"", capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"host": host, "error": f"connection failed: {exc.__class__.__name__}"}
    shown = result.stdout.decode("utf-8", errors="replace")
    chain = _PEM.findall(shown)
    if not chain:
        if _UNRESOLVABLE.search(result.stderr.decode("utf-8", errors="replace")):
            # The name does not resolve from here (a regional CDN, a retired
            # host): unverified, not a certificate problem.
            return {"host": host, "unreachable": True,
                    "error": "host name does not resolve from the analysis machine"}
        return {"host": host, "error": "no certificate chain received"}
    last = chain[-1]
    try:
        subject = _field(last, "subject").replace("subject=", "", 1)
        issuer = _field(last, "issuer").replace("issuer=", "", 1)
        if subject and subject == issuer:
            anchor = last
        else:
            urls = _CA_ISSUERS.findall(_openssl(["x509", "-noout", "-text"], last.encode()))
            if not urls:
                return {"host": host, "error": "chain ends without a root or an issuer link"}
            anchor = _download_certificate(urls[0])
            if not anchor:
                return {"host": host, "error": f"issuer certificate unreadable: {urls[0]}"}
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return {"host": host, "error": f"anchor lookup failed: {exc.__class__.__name__}"}
    if not _verify(chain[0], anchor, chain[1:]):
        return {"host": host, "error": "the server certificate does not verify against the anchor"}
    return {"host": host, "root_pem": anchor.strip() + "\n",
            "root_subject": _field(anchor, "subject").replace("subject=", "", 1),
            "root_not_after": _field(anchor, "enddate").replace("notAfter=", "", 1)}


def _verify(leaf: str, anchor: str, intermediates: list[str]) -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        (base / "anchor.pem").write_text(anchor)
        (base / "chain.pem").write_text("\n".join(intermediates) or anchor)
        (base / "leaf.pem").write_text(leaf)
        result = subprocess.run(
            ["openssl", "verify", "-partial_chain", "-CAfile", str(base / "anchor.pem"),
             "-untrusted", str(base / "chain.pem"), str(base / "leaf.pem")],
            capture_output=True, timeout=TIMEOUT)
    return result.returncode == 0


def _resource_name(subject: str) -> str:
    common = re.search(r"CN=([^,]+)", subject or "")
    words = re.sub(r"[^a-z0-9]+", "_", (common.group(1) if common else subject).lower()).strip("_")
    return f"tls_root_{words or 'server'}"


def _raw_directory(source_file_path: str) -> str:
    path = str(source_file_path or "").replace("\\", "/")
    module = path.split("/src/main/", 1)[0] if "/src/main/" in path else ""
    return f"{module}/src/main/res/raw" if module else ""


def tls_trust_evidence(error: dict, resolve=resolve_root) -> dict:
    """Trust overrides in the reviewed code, its hosts, and their resolved roots."""
    source = str(error.get("method_body", ""))
    try:
        source = (Path(str(error.get("repository_path", ""))) / str(
            error.get("source_file_path", ""))).read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    overrides = trust_overrides(source)
    if not (overrides["trust_all"] or overrides["any_hostname"]):
        return {}
    hosts = hardcoded_hosts(source)
    resolved = [resolve(host) for host in hosts]
    evidence = {**overrides, "hosts": hosts,
                "host_roots": [{key: value for key, value in item.items() if key != "root_pem"}
                               for item in resolved]}
    directory = _raw_directory(str(error.get("source_file_path", "")))
    verified = [item for item in resolved if "root_pem" in item]
    unverified = [item["host"] for item in resolved if item.get("unreachable")]
    failed = [item for item in resolved if "root_pem" not in item and not item.get("unreachable")]
    if unverified:
        evidence["unverified_hosts"] = unverified
    # A certificate problem anywhere blocks the change; a host that does not
    # resolve from here does not, but is named so the patch says it was not
    # checked (the platform store still validates it, as it would anyway).
    complete = bool(verified) and not failed and bool(directory)
    evidence["trust_all_hosts_resolved"] = bool(overrides["trust_all"] and complete)
    if evidence["trust_all_hosts_resolved"]:
        files = {}
        for item in verified:
            name = _resource_name(item["root_subject"])
            files.setdefault(name, {
                "path": f"{directory}/{name}.pem",
                "resource_reference": f"R.raw.{name}",
                "content": item["root_pem"],
                "purpose": (f"root certificate of {item['root_subject']}, the root "
                            f"the servers' chains lead to; valid until {item['root_not_after']}"),
                "hosts": [],
            })["hosts"].append(item["host"])
        evidence["provided_files"] = list(files.values())
        from cognalys.extraction.tls_client_evidence import module_min_sdk

        min_sdk = module_min_sdk(Path(str(error.get("repository_path", ""))),
                                 str(error.get("source_file_path", "")))
        if min_sdk:
            evidence["min_sdk"] = min_sdk
        evidence["maintainer_facts"] = trust_facts(evidence, min_sdk)
    elif (overrides["trust_all"] and not hosts
          and _VALIDATING_ALTERNATIVE.search(source) and accept_all_default(source)):
        evidence.update(default_validation_evidence(
            Path(str(error.get("repository_path", ""))),
            str(error.get("source_file_path", ""))))
    repository = Path(str(error.get("repository_path", "") or ""))
    if overrides["trust_all"] and str(error.get("repository_path", "")) and repository.is_dir():
        # Read by the plan, the behaviour review and the text, never by triage.
        unreachable = unreachable_trust_all(repository, source,
                                            str(error.get("source_file_path", "")))
        if unreachable:
            evidence["trust_all_unreachable"] = unreachable
    return evidence


# The class already builds a validating manager on another branch (Read You:
# `if (trustAllCerts) <accept all> else Platform.get().platformTrustManager()`).
_VALIDATING_ALTERNATIVE = re.compile(
    r"platformTrustManager\s*\(|TrustManagerFactory\s*\.\s*getInstance\s*\(")
NETWORK_SECURITY_CONFIG = "network_security_config"
_GUARD = re.compile(r"\bif\s*\(\s*(?P<name>[A-Za-z_]\w*)\s*\)")


def accept_all_default(source: str) -> str:
    """The selector that picks the accept-all manager when it defaults to true.

    Read You: `if (trustAllCerts) { <accept all> }` with `trustAllCerts:
    Boolean = true`. A selector read from settings (KPassNotes' WebDAV
    "ignore SSL errors", DSub's per-server option) is the user's choice, not
    a default, and a patch must not override it; "" then.
    """
    for guard in _GUARD.finditer(source or ""):
        name = guard.group("name")
        if not _EMPTY_SERVER_CHECK.search(source[guard.end():guard.end() + 800]):
            continue
        if re.search(rf"\b{re.escape(name)}\s*(?::\s*Boolean\s*)?=\s*true\b"
                     rf"|\bboolean\s+{re.escape(name)}\s*=\s*true\b", source):
            return name
    return ""


def default_validation_evidence(repository: Path, source_file_path: str) -> dict:
    """What making validation the default needs when no server is hard-coded.

    Read You (2026-10-18) defaults its only HTTP client to trust-all, and the
    servers are the feeds and self-hosted sync services users type in. No
    root can be bundled for them; the platform's validation is the default
    to restore. A server with a self-signed certificate or a private CA,
    accepted silently until now, then needs its CA installed on the device --
    which an app targeting API 24 or later ignores unless its network
    security config trusts user CAs. When the module has no such config, one
    trusting the system and user stores (keeping the manifest's cleartext
    choice, which a config would otherwise override) is provided with the
    manifest attribute that enables it.
    """
    from cognalys.extraction.tls_client_evidence import module_sdk

    path = str(source_file_path or "").replace("\\", "/")
    if "/src/main/" not in path:
        return {}
    main = path.split("/src/main/", 1)[0] + "/src/main"
    manifest_path = f"{main}/AndroidManifest.xml"
    try:
        manifest = (repository / manifest_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    application = re.search(r"<application\b(?P<attributes>[^>]*)>", manifest)
    if not application:
        return {}
    evidence: dict = {"trust_all_default_with_validating_branch": True,
                      "target_sdk": module_sdk(repository, source_file_path, "targetSdk")}
    config_path = f"{main}/res/xml/{NETWORK_SECURITY_CONFIG}.xml"
    existing = ("networkSecurityConfig" in application.group("attributes")
                or (repository / config_path).exists())
    evidence["network_security_config_exists"] = existing
    facts = ["Servers whose certificate the system does not trust (self-signed or "
             "from a private CA), accepted silently until now, are rejected."]
    if not existing:
        cleartext = re.search(r"android:usesCleartextTraffic\s*=\s*\"(true|false)\"",
                              application.group("attributes"))
        target = evidence["target_sdk"] or 0
        permitted = cleartext.group(1) if cleartext else ("true" if target and target < 28
                                                          else "false")
        opening = re.match(r"<application(\s+)", manifest[application.start():])
        if opening:
            whitespace = opening.group(1)
            evidence["provided_files"] = [
                {"path": config_path,
                 "content": _network_security_config(permitted),
                 "purpose": ("lets the system and user-installed CAs validate "
                             "connections, so a self-hosted server's CA can be "
                             "installed on the device; keeps cleartext "
                             f"{'allowed' if permitted == 'true' else 'disallowed'} "
                             "as the manifest had it")},
                {"path": manifest_path,
                 "original": "<application" + whitespace,
                 "replacement": ("<application" + whitespace + "android:networkSecurityConfig="
                                 f"\"@xml/{NETWORK_SECURITY_CONFIG}\"" + whitespace),
                 "purpose": "enables the network security config"}]
            evidence["user_ca_config_provided"] = True
            facts.append("The added network security config lets their users install "
                         "that CA on the device; without it Android ignores "
                         "user-installed CAs for apps targeting API 24 or later.")
    evidence["maintainer_facts"] = {"user_configured_servers": " ".join(facts)}
    return evidence


def _network_security_config(cleartext_permitted: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            "<network-security-config>\n"
            f'    <base-config cleartextTrafficPermitted="{cleartext_permitted}">\n'
            "        <trust-anchors>\n"
            '            <certificates src="system" />\n'
            '            <certificates src="user" />\n'
            "        </trust-anchors>\n"
            "    </base-config>\n"
            "</network-security-config>\n")


def _common_name(subject: str) -> str:
    found = re.search(r"CN=([^,]+)", subject or "")
    return found.group(1).strip() if found else str(subject or "").strip()


def _hosts_text(hosts: list[str]) -> str:
    named = [f"`{host}`" for host in hosts]
    return named[0] if len(named) == 1 else ", ".join(named[:-1]) + " and " + named[-1]


def _platform_store() -> dict:
    import json

    from cognalys.workspace import library_data

    try:
        data = json.loads(library_data("platform_trust_stores.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("android") or {} if isinstance(data, dict) else {}


def trust_facts(evidence: dict, min_sdk: int | None, store: dict | None = None) -> dict:
    """What a maintainer needs to accept the bundled anchor, as sentences.

    The hosts that were checked and the root each leads to, why that root
    is bundled (the first Android release that trusts it against the app's
    minSdk, when platform_trust_stores.json knows the root), and the hosts
    that could not be checked. Only what the evidence established is said.
    """
    store = _platform_store() if store is None else store
    by_root: dict[str, list[dict]] = {}
    for item in evidence.get("host_roots") or []:
        if item.get("root_subject"):
            by_root.setdefault(_common_name(item["root_subject"]), []).append(item)
    sentences = []
    for root, items in by_root.items():
        hosts = sorted(item["host"] for item in items)
        year = re.search(r"\b(\d{4})\b", str(items[0].get("root_not_after", "")))
        until = f" (valid until {year.group(1)})" if year else ""
        verb = "it chains" if len(hosts) == 1 else "both chain" if len(hosts) == 2 else "all chain"
        lead = f"I checked {_hosts_text(hosts)}: {verb} to {root}"
        known = store.get(root) or {}
        if known.get("api_level") and min_sdk and min_sdk < int(known["api_level"]):
            why = (f", which Android trusts only from {known['release']} (the app supports "
                   f"API {min_sdk}), so the root is bundled{until}.")
        elif known.get("api_level") and min_sdk:
            why = (f", which every Android version the app supports already trusts; the "
                   f"bundled copy{until} is only a fallback.")
        else:
            why = f", which is bundled{until} for devices whose system store lacks it."
        sentences.append(lead + why)
    unverified = list(evidence.get("unverified_hosts") or [])
    if unverified:
        verb = "does" if len(unverified) == 1 else "do"
        sentences.append(
            f"{_hosts_text(unverified)} {verb} not resolve from my network, so "
            f"{'its chain is' if len(unverified) == 1 else 'their chains are'} unchecked.")
    return {"trust_hosts": " ".join(sentences)} if sentences else {}


# A branch condition of one name, maybe negated: `if (flag)`, `if (!flag)`.
_BRANCH = re.compile(r"\bif\s*\(\s*(?P<negated>!\s*)?(?P<name>[A-Za-z_][\w.]*)\s*\)\s*\{")
_SOURCE_SUFFIXES = (".java", ".kt")


def _block_end(source: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def trust_all_guard(source: str) -> dict:
    """The one-name condition the accept-all manager sits under, and the value that reaches it.

    `if (flag) { <accept all> }` needs flag true; `if (flag) {...} else {
    <accept all> }` needs it false; `!flag` flips either. {} when the
    accept-all is not directly under such a branch.
    """
    from cognalys.utils.identifier_words import blank_comments_and_literals

    source = blank_comments_and_literals(source or "")
    check = _EMPTY_SERVER_CHECK.search(source)
    if not check:
        return {}
    found: dict = {}
    for branch in _BRANCH.finditer(source[:check.start()]):
        opening = branch.end() - 1
        close = _block_end(source, opening)
        if close == -1:
            continue
        needed = branch.group("negated") is None
        if opening < check.start() < close:
            found = {"name": branch.group("name"), "needed": needed}
            continue
        following = re.match(r"\s*else\s*\{", source[close + 1:])
        if following:
            else_open = close + 1 + following.end() - 1
            else_close = _block_end(source, else_open)
            if else_open < check.start() < else_close:
                found = {"name": branch.group("name"), "needed": not needed}
    return found


def _module_and_set(relative: str) -> tuple:
    """(Gradle module path, source set) of a repository path; set "" when it has no src/<set>/."""
    match = re.search(r"(?:^|/)src/(?P<set>[\w-]+)/", relative)
    if not match:
        return (relative.rsplit("/", 1)[0] if "/" in relative else "", "")
    return (relative[:match.start()].strip("/"), match.group("set"))


def unreachable_trust_all(repository: Path, source: str, source_path: str = "") -> dict:
    """The accept-all branch's guard, when no code of the repository ever gives it the value.

    ToFShare (2026-10-31 review): trust evidence recorded trust_all for a
    manager under a static boolean, and the review had to establish by hand
    whether anything could reach it. Recorded when the guard is a field
    declared once in the repository with a literal (Java `boolean name = v`
    or `boolean name;`, Kotlin `val name = v`), its value never assigned
    anywhere else, and that value is not the one the branch needs. A note
    for the review and the text: the finding itself is not dismissed.

    Not recorded whenever something else may set it (2026-11-01 review): a
    Kotlin `var` (a property setter from Java), a `setName(` call, the name
    as a string anywhere (reflection, a preferences key) or in an XML file
    (data binding), a compound assignment, or a declaration in another
    module or in a source set other than main.
    """
    guard = trust_all_guard(source)
    if not guard:
        return {}
    name = guard["name"].rsplit(".", 1)[-1]
    any_declaration = re.compile(rf"\b(?:boolean|Boolean|var|val)\s+{re.escape(name)}\b")
    declaration = re.compile(
        rf"\b(?:(?P<java>boolean|Boolean)|val)\s+{re.escape(name)}\b\s*(?::\s*Boolean\s*)?"
        rf"(?:=\s*(?P<value>true|false)\b)?\s*[;\n]")
    assignment = re.compile(rf"(?<![\w$]){re.escape(name)}\s*(?:[|&^]?=(?!=)|\+\+|--)")
    setter = re.compile(rf"\bset{re.escape(name[:1].upper() + name[1:])}\s*\(")
    quoted = re.compile(rf"[\"']{re.escape(name)}[\"']")
    declared, assigned = [], []
    try:
        files = [path for path in Path(repository).rglob("*")
                 if path.suffix in _SOURCE_SUFFIXES + (".xml",) and path.is_file()
                 and "/build/" not in path.as_posix()]
    except OSError:
        return {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name not in text:
            continue
        if path.suffix == ".xml" or quoted.search(text) or setter.search(text):
            return {}
        for line_number, line in enumerate(text.splitlines(), 1):
            code = line.split("//", 1)[0]
            if not any_declaration.search(code):
                if assignment.search(code):
                    assigned.append((path, line_number))
                continue
            match = declaration.search(code + "\n")
            if not match or not (match.group("java") or match.group("value")):
                return {}
            declared.append((path, line_number, match.group("value") or "false"))
    if len(declared) != 1 or assigned:
        return {}
    path, line_number, value = declared[0]
    where = path.relative_to(repository).as_posix() if path.is_relative_to(repository) else str(path)
    module, source_set = _module_and_set(where)
    if source_set not in ("", "main"):
        return {}
    own = str(source_path or "").replace("\\", "/")
    root = Path(repository).as_posix().rstrip("/") + "/"
    if own.startswith(root):
        own = own[len(root):]
    if own and ("/src/" in "/" + own or "/src/" in "/" + where):
        # A field of another Gradle module may be a different one (or set
        # there by code this scan cannot read): not recorded.
        own_module, _ = _module_and_set(own)
        if not (module == own_module or (own.startswith("/") and own_module.endswith("/" + module))):
            return {}
    if (value == "true") == guard["needed"]:
        return {}
    relative = path.relative_to(repository).as_posix() if path.is_relative_to(repository) else str(path)
    return {
        "guard": name,
        "declared_at": f"{relative}:{line_number}",
        "value": value,
        "needed": "true" if guard["needed"] else "false",
        "note": (f"the accept-all trust manager runs only when {name} is "
                 f"{'true' if guard['needed'] else 'false'}; {name} is declared "
                 f"{value} at {relative}:{line_number} and no code in the repository assigns "
                 "it, so that path cannot run in this build. The finding stands; say so in "
                 "the text rather than describe the path as reachable."),
    }
