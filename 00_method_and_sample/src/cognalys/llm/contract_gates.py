"""Patches that satisfy the rule by breaking something outside the method.

The review of the RV import's "ready" pull requests (2026-10-12) found
patches that compiled, silenced the detector and were wrong, each because the
value or behaviour the patch changed was a contract with something the patch
did not see:

- org.cry.otp's TOTP: HmacSHA1 became HmacSHA256 and the repository's RFC 6238
  known-answer vectors were deleted from its test to make it pass. Every
  account a user had enrolled would stop producing valid codes.
- BitBanana's encodePbkdf2: the iteration count became a literal, so the PIN
  hash stored by an earlier release no longer matches and users are locked out.
- Password Monitor: SHA-1 became SHA-256 in a value whose prefix is sent to
  api.pwnedpasswords.com, which only answers SHA-1 prefixes.
- HomeApp's onReceivedSslError: proceed() became cancel() in a method whose
  `@SuppressLint("WebViewClientOnReceivedSslError")` records that the
  maintainer accepted the warning; their changelog says self-signed
  certificates are deliberately allowed, and they closed the pull request.
  A suppression no longer decides the finding here: it reaches the model as
  evidence (utils/deliberate_insecurity), which weighs it with the rest.

The prompt already told the model not to do these things. These checks make
the same rules deterministic: a test's pinned expectations and a derived
value that is stored or sent are facts read from the repository, not
judgements. None of them calls the finding a false positive:
the violation stays confirmed and goes to the maintainer as an issue.

The same review found two more kinds, checked in sections 4 and 5: MTG
Familiar's patch moved a constant AES key into a helper and kept it, and
org.cry.otp's mOTP and FairEmail's fingerprint replaced an algorithm the
other side of a protocol still computes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List


# ---------------------------------------------------------------------------
# 1. A patch that rewrites what an existing test expects.
# ---------------------------------------------------------------------------

_ASSERTION = re.compile(r"\bassert\w*\s*\(|\bexpect\w*\b|\bshould\w*\b|\bcheck\s*\(", re.IGNORECASE)
# A line that is only literals: one row of a known-answer table.
_LITERAL_ROW = re.compile(
    r'^\s*(?:(?:"[^"\n]*"|\'[^\'\n]*\'|-?0x[0-9a-f]+L?|-?\d[\d_]*(?:\.\d+)?[fFdDlL]?)'
    r'\s*,?\s*)+(?://.*)?$',
    re.IGNORECASE,
)


def _is_test_path(path: str) -> bool:
    return bool(re.search(r"(?:^|/)src/(?:test|androidTest|sharedTest)\w*/|(?:^|/)tests?/", path)) \
        or "test" in Path(path).name.lower()


def rewritten_test_expectations(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Assertions and known-answer values a patch removes from existing tests.

    A test the repository already had pins behaviour someone relied on; when a
    patch has to change what it expects, the patch changes that behaviour.
    Only lines the repository's own file has count: a test this pipeline
    generated (and a compile repair later rewrites) is not the project's
    contract, nor is a new test added to an existing file. Without the
    repository to compare with, nothing is flagged.
    """
    repository = str(error.get("repository_path", "")).strip()
    if not repository or error.get("repository_includes_patch"):
        # A repair reads a worktree the patch is already applied to; the
        # tests there may be the ones this pipeline wrote.
        return []
    issues = []
    for edit in edits:
        path = str(edit.get("source_file_path", ""))
        original = str(edit.get("original_code", ""))
        if not (edit.get("edit_kind") == "test" or _is_test_path(path)) or not original.strip():
            continue
        try:
            existing = {
                line.strip() for line in
                (Path(repository) / path).read_text(encoding="utf-8", errors="replace").splitlines()
            }
        except OSError:
            continue
        corrected_lines = {line.strip() for line in str(edit.get("corrected_code", "")).splitlines()}
        removed = [
            line.strip() for line in original.splitlines()
            if line.strip() and line.strip() not in corrected_lines
            and line.strip() in existing
            and (_ASSERTION.search(line) or _LITERAL_ROW.match(line))
        ]
        if removed:
            issues.append({
                "code": "EXISTING_TEST_EXPECTATIONS_REWRITTEN",
                "path": path,
                "details": [
                    "the patch removes or changes what an existing test expects "
                    f"({len(removed)} line(s), e.g. {removed[0][:120]!r}); those "
                    "values are a contract the project already pins, so the "
                    "change alters behaviour others depend on. Keep the existing "
                    "expectations, or report the finding to the maintainer "
                    "instead of patching it.",
                ],
            })
    return issues


# ---------------------------------------------------------------------------
# 1b. A test that reads the production source instead of running it.
# ---------------------------------------------------------------------------

# A literal naming a Kotlin or Java source file, and a call that reads a file.
_SOURCE_FILE_LITERAL = re.compile(r'"[^"\n]*\.(?:kt|java)"')
# A literal naming a production source path is reason enough: a test has no
# business opening it, whatever it reads it with (AI Hub's second attempt
# switched to FileInputStream).
_PRODUCTION_SOURCE_PATH = re.compile(r'"[^"\n]*src/main/[^"\n]*\.(?:kt|java)"')
_FILE_READ = re.compile(
    r"\.\s*(?:readText|readLines|readBytes|bufferedReader|read)\s*\("
    r"|\bFiles\s*\.\s*(?:readString|readAllLines|readAllBytes|lines|newBufferedReader)\s*\("
    r"|\b(?:new\s+)?(?:FileReader|FileInputStream|InputStreamReader|Scanner)\s*\("
)
# An assertion that compares two constants: it holds or fails whatever the
# code under test does (AI Hub: NEVER_ALLOW == ALWAYS_ALLOW).
_CONSTANT = r"(?:[A-Z]\w*\.)+[A-Z][A-Z0-9_]+"
_CONSTANT_ONLY_ASSERTION = re.compile(
    rf"\bassert\w*\s*\((?:\s*\"[^\"]*\"\s*,)?\s*{_CONSTANT}\s*(?:[=!]=|,)\s*{_CONSTANT}\s*\)"
)


def source_text_tests(edits: List[Dict]) -> List[Dict]:
    """Generated tests that open a .kt/.java file and search its text.

    HomeApp's PR #114 and AI Hub's first patch both shipped a "regression
    test" that read the edited source file and asserted a constant was or
    was not in it. It passes whatever the app does at run time, breaks on a
    rename, and a maintainer reads it as noise: a test must exercise the
    code, or the patch carries none.
    """
    issues = []
    for edit in edits:
        path = str(edit.get("source_file_path", ""))
        if not (edit.get("edit_kind") == "test" or _is_test_path(path)):
            continue
        code = str(edit.get("corrected_code", ""))
        if _CONSTANT_ONLY_ASSERTION.search(code):
            issues.append({
                "code": "TEST_ASSERTS_ONLY_CONSTANTS",
                "path": path,
                "details": [
                    "an assertion compares two constants with each other, so it "
                    "holds whatever the code under test does. Assert on what the "
                    "changed code produces, or add no test.",
                ],
            })
            continue
        reads_source = _PRODUCTION_SOURCE_PATH.search(code) or (
            _SOURCE_FILE_LITERAL.search(code) and _FILE_READ.search(code))
        if reads_source:
            issues.append({
                "code": "TEST_READS_PRODUCTION_SOURCE",
                "path": path,
                "details": [
                    "the test opens a .kt/.java source file and checks its text "
                    "instead of running the code; such a test passes whatever "
                    "the app does and breaks on a rename. Exercise the changed "
                    "behaviour through the code's API, or add no test.",
                ],
            })
    return issues


# ---------------------------------------------------------------------------
# 2. A patch that changes a stored or transmitted derived value.
# ---------------------------------------------------------------------------

# Calls whose result is a deterministic function of their arguments and
# input: changing an argument changes every value derived from then on, and
# one recomputed later no longer matches one derived earlier. SecureRandom
# and key generation are absent on purpose: nobody re-derives their output.
_DERIVATION_CALL = re.compile(
    r"\b(?P<api>MessageDigest|Mac|SecretKeyFactory|Cipher|Signature|KeyAgreement"
    r"|DigestUtils|Hashing)\s*\.\s*(?P<call>getInstance|\w+)\s*\("
    r"|\b(?P<ctor>PBEKeySpec|PBEParameterSpec|SecretKeySpec)\s*\(",
)


def _arguments(code: str, start: int) -> str:
    depth, index = 0, start
    while index < len(code):
        char = code[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return re.sub(r"\s+", "", code[start + 1:index])
        index += 1
    return ""


# An algorithm name written as a literal anywhere: EinkBro passes it from
# `sha("SHA-1", ...)` through two helpers to MessageDigest.getInstance(algorithm)
# (2026-10-22), so the governed call reads the same before and after a patch
# that changes the digest. The literal is what changes.
_ALGORITHM_LITERAL = re.compile(
    r'"((?:MD[245]|SHA-?(?:1|224|256|384|512)(?:/\d+)?|SHA3-\d+|Hmac\w+|PBKDF2\w*'
    r'|PBEWith\w+|AES(?:/[\w-]+)*|DESede(?:/[\w-]+)*|DES(?:/[\w-]+)*|RSA(?:/[\w-]+)*'
    r'|RC4|Blowfish))"',
    re.IGNORECASE,
)


def derivations(code: str) -> set:
    """(API, normalised arguments) of each value-deriving call in ``code``,
    and ("algorithm", name) for each algorithm literal."""
    found = {("algorithm", match.group(1)) for match in _ALGORITHM_LITERAL.finditer(code)}
    for match in _DERIVATION_CALL.finditer(code):
        api = match.group("api") or match.group("ctor")
        call = match.group("call") or ""
        if match.group("api") and call != "getInstance" and match.group("api") not in ("DigestUtils", "Hashing"):
            continue
        name = f"{api}.{call}" if call else api
        found.add((name, _arguments(code, match.end() - 1)))
    return found


_CODE_COMMENT = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/")


def reported_value_for(error: Dict, api: str) -> str:
    """The value the runtime checker reported for ``api``'s call, or "".

    Read from the finding (detector_context.value, else the "... but found Y"
    of its message), and only when the finding is about that API: its
    specification or message names the class (MessageDigest for
    MessageDigest.getInstance), so a value seen at one call never stands for
    another API's argument.
    """
    owner = str(api or "").split(".", 1)[0]
    about = f"{error.get('spec', '')} {error.get('error_message', '')}"
    if not owner or owner == "algorithm" or owner not in about:
        return ""
    value = str((error.get("detector_context") or {}).get("value", "") or "").strip()
    if not value:
        found = re.search(r"\bbut\s+found\s+['\"]?(?P<value>[\w.\-/]+?)['\"]?(?:[\s,;)]|$)",
                          str(error.get("error_message", "")))
        value = found.group("value").rstrip(".") if found else ""
    return value if re.fullmatch(r"[\w.\-/]+", value) else ""


def _legacy_derivation_kept(derivation: tuple, corrected: str, reported_value: str = "") -> bool:
    """Whether the patch keeps ``derivation`` as a parameterised legacy path.

    bitbanana's verify-then-migrate draft (exp_final_20261019 subset_policy)
    kept PBKDF2WithHmacSHA1 for stored PIN hashes as LEGACY_APP_LOCK_PBKDF2
    and called SecretKeyFactory.getInstance(algorithm) for both forms, so the
    literal call the original made no longer appears verbatim and the patch
    was refused for dropping it. The derivation is kept when its API is still
    called and every literal it was given is still in the code.

    A call given a variable (EinkBro's MessageDigest.getInstance(algorithm),
    2026-10-22) has no literal of its own: ``reported_value``, the value the
    runtime checker saw at that call ("SHA-1"), stands for it. No literal and
    no reported value is never "kept": "every literal is still there" would
    hold for any patch that still calls the API.
    """
    api, arguments = derivation
    literals = re.findall(r'"[^"\n]*"|(?<![\w.])\d{2,}(?![\w.])', arguments or "")
    code = _CODE_COMMENT.sub("", corrected)
    if not literals and reported_value and api != "algorithm":
        # A number stands as itself (an iteration count), anything else as a
        # string literal; neither inside a longer token.
        written = (reported_value if reported_value.isdigit()
                   else f'"{reported_value}"')
        return (bool(re.search(rf"(?<![\w$]){re.escape(str(api))}\b", code))
                and bool(re.search(rf"(?<![\w.]){re.escape(written)}(?![\w.])", code)))
    if not literals:
        return False
    return (bool(re.search(rf"(?<![\w$]){re.escape(str(api))}\b", code))
            and all(literal in code for literal in literals))


# What makes a value new each time the code runs. A constructor given such a
# value, an algorithm name and nothing else wraps generated material: nothing
# stored was ever computed from the same inputs, so building it another way
# breaks no stored data. CometOTP loadOrGenerateWrappedKey (run_v6,
# 2026-11-10): three drafts replaced SecretKeySpec(raw, "AES"), raw filled by
# SecureRandom on the branch that runs when no key file exists, with a
# KeyGenerator key, and all three were refused as changing a stored value.
# A constructor that also takes a password, a constant or an expression is
# never exempt: its value can be derived again from what is stored.
_FRESH_RANDOM_SOURCE = re.compile(
    r"\bSecureRandom\b|\.\s*nextBytes\s*\(|\bKeyGenerator\b|\.\s*generateKey\s*\("
    r"|\.\s*generateSeed\s*\(")
_PLAIN_NAME = re.compile(r"[A-Za-z_$][\w$]*")
_CHOOSES_A_VALUE = re.compile(r"\b(?:if|when|else)\b|\?:|\?\s*[^\s.:?]")
_NEW_ZERO_ARRAY = re.compile(r"^(?:ByteArray\s*\([^(){}]*\)|new\s+byte\s*\[[^\]]*\])$")


def _top_level_arguments(text: str) -> List[str]:
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


def _declared_initializer(name: str, scope: str) -> str:
    """The initializer of ``val|var name = ...`` or ``Type name = ...`` in ``scope``."""
    found = re.search(
        rf"(?:\b(?:val|var)\s+{re.escape(name)}\b(?:\s*:\s*[\w.<>?\[\]]+)?"
        rf"|\b[\w.<>\[\]]+\s+{re.escape(name)}\b)\s*=(?!=)\s*(?P<init>[^;\n]+)",
        scope)
    return found.group("init").strip() if found else ""


def _function_body(name: str, source: str) -> str:
    """The body of the function ``name`` declared in ``source`` (Kotlin block or
    expression body, or a Java method), or ""."""
    found = re.search(
        rf"\bfun\s+(?:<[^>]+>\s*)?{re.escape(name)}\s*\([^)]*\)[^{{=\n]*(?P<open>[{{=])"
        rf"|\b[\w.<>\[\]]+\s+{re.escape(name)}\s*\([^)]*\)\s*(?:throws[^{{]+)?(?P<jopen>\{{)",
        source)
    if not found:
        return ""
    start = found.end()
    if found.group("open") == "=":
        end = source.find("\n", start)
        return source[start:end if end >= 0 else len(source)]
    depth, index = 1, start
    while index < len(source) and depth:
        depth += source[index] == "{"
        depth -= source[index] == "}"
        index += 1
    return source[start:index - 1]


def _freshly_random(name: str, scopes: List[str], file_source: str, depth: int = 0) -> bool:
    """Whether ``name`` holds material generated anew each time the code runs.

    True when its initializer names a random source, when it is a new zeroed
    array a scope fills with nextBytes, or (one level only) when it is the
    result of a function declared in ``file_source`` that returns such a value
    or whose expression body names a random source. Any other shape is False,
    and so is an initializer that chooses between values (if/when/else, an
    elvis or ternary operator) or a name assigned again in the same scope:
    either can hand the constructor bytes read back from storage (review of
    2026-11-10: `if (f.exists()) f.readBytes() else ... nextBytes(it)`).
    """
    for scope in scopes:
        initializer = _declared_initializer(name, scope)
        if not initializer:
            continue
        if _CHOOSES_A_VALUE.search(initializer) or len(re.findall(
                rf"(?<![\w$.]){re.escape(name)}\s*=(?!=)", scope)) > 1:
            return False
        if _FRESH_RANDOM_SOURCE.search(initializer):
            return True
        if _NEW_ZERO_ARRAY.match(initializer):
            return any(re.search(rf"nextBytes\s*\(\s*{re.escape(name)}\s*[,)]", item)
                       for item in scopes)
        call = re.match(r"(?:this\s*\.\s*)?([A-Za-z_$][\w$]*)\s*\(", initializer)
        if call and depth == 0 and file_source:
            body = _function_body(call.group(1), file_source)
            if not body:
                return False
            returned = re.findall(r"\breturn\s+([A-Za-z_$][\w$]*)\s*;?\s*$", body, re.M)
            if returned:
                return all(_freshly_random(item, [body], "", 1) for item in set(returned))
            return bool(_FRESH_RANDOM_SOURCE.search(body)) and "\n" not in body.strip()
        return False
    return False


def _only_fresh_random_inputs(derivation: tuple, scopes: List[str], file_source: str) -> bool:
    """Whether ``derivation`` is a constructor fed only an algorithm name (or an
    integer) and at least one freshly generated random value."""
    api, arguments = derivation
    if api == "algorithm" or "." in str(api):
        return False
    names = []
    for part in _top_level_arguments(arguments or ""):
        if _ALGORITHM_LITERAL.fullmatch(part) or re.fullmatch(r"\d+", part):
            continue
        if not _PLAIN_NAME.fullmatch(part):
            return False
        names.append(part)
    return bool(names) and all(_freshly_random(name, scopes, file_source) for name in names)


# A factory of material generated anew each time (a random key, key pair or
# byte stream): the algorithm name it is given says what to generate, not
# how to compute a value from an input. ShortMessageSecure's
# generateMacSecret (frozen run, 2026-11-13) stores
# KeyGenerator.getInstance("HmacSHA1").generateKey().getEncoded(); a draft
# asking for "HmacSHA256" was refused as changing a stored derived value,
# although no stored value was ever derived from that name.
_RANDOM_GENERATOR_FACTORY = re.compile(
    r"\b(?:KeyGenerator|KeyPairGenerator|SecureRandom)\s*\.\s*getInstance\s*\(\s*$")


def _names_only_a_random_generator(derivation: tuple, original: str) -> bool:
    """Whether ``derivation`` is an algorithm literal that ``original`` passes
    only as the algorithm of a random generator factory (KeyGenerator,
    KeyPairGenerator, SecureRandom). A literal also given anywhere else (a
    Mac, a digest, a SecretKeySpec over stored bytes) is not."""
    api, name = derivation
    if api != "algorithm" or not name:
        return False
    code = _CODE_COMMENT.sub("", original)
    places = [match.start() for match in re.finditer(rf'"{re.escape(name)}"', code)]
    return bool(places) and all(
        _RANDOM_GENERATOR_FACTORY.search(code[:place]) for place in places)


def _repository_source(error: Dict, path: str) -> str:
    """The file as the repository holds it, before any patch; "" otherwise."""
    repository = str(error.get("repository_path", "")).strip()
    if not repository or not path or error.get("repository_includes_patch"):
        return ""
    try:
        return (Path(repository) / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def changed_stored_derivations(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Value derivations a patch changes when the derived value is stored or sent.

    derived_value_consumers (extraction.value_consumers) records where the
    reported method's value goes. When it names stored data or a network
    request, a value derived the new way stops matching the one stored or
    expected remotely. A patch that keeps the original derivation somewhere
    (a legacy verification or migration path) is not flagged. A constructor
    fed only an algorithm name and freshly generated random material is not a
    derivation of anything stored and is not flagged, nor is an algorithm name
    given only to a random generator factory (KeyGenerator, KeyPairGenerator,
    SecureRandom).
    """
    consumers = error.get("derived_value_consumers") or {}
    if not (consumers.get("persisted") or consumers.get("transmitted")
            or consumers.get("fixed")):
        return []
    sink = next((item for item in consumers.get("consumers", [])
                 if item.get("kind") in {"persistence", "network", "fixed_value"}), {})
    production = [edit for edit in edits
                  if edit.get("edit_kind") != "test"
                  and not _is_test_path(str(edit.get("source_file_path", "")))]
    corrected_all = "\n".join(str(edit.get("corrected_code", "")) for edit in production)
    # A value fixed in the source moves with the derivation when the patch
    # replaces it by the value the new derivation gives for the same input:
    # whoBIRD (2026-10-18) swaps each MD5 constant for the SHA-256 the
    # pipeline computed from the very file it describes. Every replacement
    # must be in place and every old value gone; stored or transmitted values
    # are never covered.
    replacements = [item for item in ((error.get("case_migration_policy") or {})
                                      .get("replacement_values") or [])
                    if isinstance(item, dict)]
    if (replacements and not consumers.get("persisted") and not consumers.get("transmitted")
            and all(str(item.get("new_value", "")).lower() in corrected_all.lower()
                    and str(item.get("old_value", "")).lower() not in corrected_all.lower()
                    for item in replacements)):
        return []
    kept = derivations(corrected_all)
    issues = []
    for edit in production:
        # Gone from every production edit: neither changed in place nor kept
        # on a legacy path in another file of the same patch.
        scopes = [str(edit.get("original_code", "")), str(error.get("method_body", ""))]
        file_source = _repository_source(error, str(edit.get("source_file_path", "")))
        dropped = sorted(item for item in derivations(str(edit.get("original_code", ""))) - kept
                         if not _legacy_derivation_kept(
                             item, corrected_all, reported_value_for(error, item[0]))
                         and not _only_fresh_random_inputs(item, scopes, file_source)
                         and not _names_only_a_random_generator(
                             item, str(edit.get("original_code", ""))))
        if not dropped:
            continue
        api, arguments = dropped[0]
        changed = (f'the algorithm literal "{arguments}"' if api == "algorithm"
                   else f"{api}({arguments})")
        where = ("stored" if consumers.get("persisted")
                 else "sent out of the app" if consumers.get("transmitted")
                 else "compared with a value fixed in the source")
        issues.append({
            "code": "STORED_DERIVED_VALUE_CHANGED",
            "path": str(edit.get("source_file_path", "")),
            "details": [
                f"the patch changes {changed}, and the value it derives "
                f"is {where} ({sink.get('path', '')}:{sink.get('line', '')}: "
                f"{str(sink.get('statement', ''))[:120]}); a value derived the new "
                "way no longer matches one derived before, so existing data or "
                "the remote side breaks. Keep the original derivation for "
                "existing values (verify-then-migrate), or report the finding to "
                "the maintainer instead of patching it.",
            ],
        })
    return issues


# ---------------------------------------------------------------------------
# 4. A patch that moves the constant key material without replacing it.
# ---------------------------------------------------------------------------

# Constructors and calls that take key material, an IV, a salt or a seed.
_MATERIAL_CALL = re.compile(
    r"\b(?:SecretKeySpec|IvParameterSpec|GCMParameterSpec|PBEKeySpec|PBEParameterSpec)\s*\("
    r"|\.setSeed\s*\(|\bSecureRandom\s*\(\s*(?=[^)\s])")
# What names a constant source inside those arguments: a resource, a
# build-time field, a named constant or a long literal.
_SOURCE_ATOM = re.compile(
    r"\bR\.string\.\w+|\bBuildConfig\.\w+|\b[A-Z][A-Z0-9_]{2,}\b|\"[^\"\n]{6,}\"")
_NOT_A_SOURCE = frozenset({
    "AES", "DES", "RSA", "GCM", "CBC", "CTR", "UTF_8", "US_ASCII", "ISO_8859_1", "UTF_16",
    "ENCRYPT_MODE", "DECRYPT_MODE", "WRAP_MODE", "UNWRAP_MODE", "DEFAULT", "NO_WRAP",
    "URL_SAFE", "NO_PADDING", "TAG_LENGTH", "KEY_SIZE", "\"UTF-8\"", "\"PBKDF2WithHmacSHA1\"",
    "\"PBKDF2WithHmacSHA256\"", "\"PBKDF2WithHmacSHA512\"", "\"HmacSHA256\"", "\"DESede\"",
})
# A source of key material that is not a constant of the application.
_NEW_KEY_SOURCE = re.compile(
    r"\b(?:SecureRandom|KeyGenerator|KeyPairGenerator|KeyStore|AndroidKeyStore|SecretKeyFactory"
    r"|MasterKey|EncryptedSharedPreferences|KeyGenParameterSpec|generateKey|nextBytes"
    r"|BiometricPrompt|Argon2|scrypt|bcrypt)\b")


# A Base64 blob long enough to be ciphertext, written into the source.
_EMBEDDED_CIPHERTEXT = re.compile(r'"[A-Za-z0-9+/]{24,}={0,2}"')


def parameter_policies(error: Dict) -> List[str]:
    """The parameter values required_numeric_constants will demand, as sentences
    for the plan and generation prompts.

    Your Local Weather run_v2 (2026-11-02): the prompts said to leave every
    other argument of the reported call as it was, the gate then refused each
    draft that kept 65536 iterations under a policy asking for 100000, and the
    finding became a maintainer issue. Telling the model what the gate checks
    makes the two agree for any policy that declares a numeric minimum.
    """
    constraints = (error.get("case_migration_policy") or {}).get("required_numeric_constants") or []
    sentences = []
    for constraint in constraints:
        try:
            minimum = int(constraint["minimum"])
            pattern = str(constraint["symbol_pattern"])
        except (KeyError, TypeError, ValueError):
            continue
        sentence = (f"The parameter policy requires a named constant whose name matches "
                    f"{pattern}, declared with a value of at least {minimum}, for the value "
                    f"the change writes.")
        purpose = str(constraint.get("purpose") or "").strip()
        sentences.append(f"{sentence} {purpose}".strip())
    return sentences


# Words that say a plan sentence keeps a value as it is.
_KEEPS_VALUE = re.compile(
    r"(?i)\b(?:keep|keeps|kept|retain|retains|preserv\w*|unchanged|same|as it is"
    r"|stays?|remains?)\b")
# A clause about reading what was written before, which a policy for the
# value written from now on leaves alone.
_EARLIER_VALUES = re.compile(
    r"(?i)\b(?:legacy|old|older|historical|former|previous\w*|persisted|stored"
    r"|compatib\w*|unmarked|verifiable|readable)\b")
# A forbidden change that forbids changing the parameter, not lowering it.
_FORBIDS_CHANGE = re.compile(
    r"(?i)^\W*(?:do not|don't|never)\s+(?:change|alter|modify|touch|increase|raise"
    r"|introduce|add)\b")
_FORBIDS_LOWERING = re.compile(r"(?i)\b(?:below|lower\w*|reduc\w*|weak\w*|caller)\b")
_NUMBER = re.compile(r"(?<![\w.-])(\d{1,3}(?:[,_ ]\d{3})+|\d+)(?![\w.])")


def _policy_keywords(pattern: str) -> List[str]:
    """The literal words a policy's name pattern requires, outside lookarounds
    (``(?i)^(?!.*(?:legacy|old)).*iterations.*$`` -> ["iteration"])."""
    body = re.sub(r"\(\?[<]?[=!][^()]*(?:\([^()]*\)[^()]*)*\)", " ", pattern)
    body = re.sub(r"\(\?[a-zA-Z]+\)", " ", body)
    words = re.findall(r"[A-Za-z]{4,}", body)
    return [word.lower().rstrip("s") for word in words]


def _number(match) -> int:
    return int(re.sub(r"[,_ ]", "", match.group(1)))


def reconcile_plan_with_parameter_policies(plan: Dict, error: Dict) -> List[Dict]:
    """Rewrite plan invariants and forbidden changes that pin a parameter a
    parameter policy requires to change, and return what was rewritten.

    Your Local Weather run_v4 (2026-11-02): the plan kept "65,536 iterations"
    among its invariants and forbade changing the iteration count while
    required_numeric_constants asked for at least 100000; the review refused
    the drafts that raised it, the gate refused those that did not, and the
    finding became a maintainer issue. A clause names the parameter by the
    policy's name pattern (its literal words, and its exclusions such as
    "legacy"); clauses about values written earlier are left alone. An
    invariant pins it when it keeps a number below the minimum (rewritten to
    "at least" the minimum) or says the parameter stays as it is, in a
    sentence that keeps something and is not about data written earlier; a forbidden
    change pins it when it forbids changing it (not lowering it).
    """
    constraints = (error.get("case_migration_policy") or {}).get("required_numeric_constants") or []
    policies = []
    for constraint in constraints:
        try:
            policies.append((str(constraint["symbol_pattern"]), int(constraint["minimum"])))
        except (KeyError, TypeError, ValueError):
            continue
    rewritten = []
    if not policies:
        return rewritten
    for field in ("behavioral_invariants", "forbidden_changes"):
        items = plan.get(field)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, str):
                continue
            text = item
            for pattern, minimum in policies:
                keywords = _policy_keywords(pattern)
                if not keywords:
                    continue
                forbids_change = (field == "forbidden_changes"
                                  and _FORBIDS_CHANGE.search(text)
                                  and not _FORBIDS_LOWERING.search(text))
                keeps = _KEEPS_VALUE.search(text)
                clauses = re.split(r"(?<=[,;.:])\s+|\s+(?:and|or)\s+", text)
                conflict = False
                new_text = text
                for clause in clauses:
                    lowered = clause.lower()
                    keyword = next((word for word in keywords if word in lowered), "")
                    if not keyword or _EARLIER_VALUES.search(clause):
                        continue
                    name = re.sub(r"\W", "", clause) + keyword + "s"
                    try:
                        if not re.fullmatch(pattern, name):
                            continue  # a value the policy's pattern excludes
                    except re.error:
                        continue
                    numbers = [_number(m) for m in _NUMBER.finditer(clause)]
                    if field == "forbidden_changes":
                        conflict = conflict or bool(forbids_change and not numbers)
                        continue
                    if not keeps or _EARLIER_VALUES.search(text):
                        continue  # an invariant that keeps nothing, or keeps old data readable
                    low = [m for m in _NUMBER.finditer(clause) if _number(m) < minimum]
                    if low:
                        fixed = clause
                        for match in reversed(low):
                            fixed = fixed[:match.start()] + f"at least {minimum:,}" + fixed[match.end():]
                        new_text = new_text.replace(clause, fixed, 1)
                        conflict = True
                    elif not numbers:
                        conflict = True
                if conflict:
                    note = (f" Exception: parameter_policies requires the {keywords[0]} value "
                            f"(a named constant matching {pattern}) to be at least {minimum:,}; "
                            f"raising it to that is required, not forbidden.")
                    if note.strip() not in new_text:
                        new_text = new_text.rstrip() + note
                    text = new_text
            if text != item:
                items[index] = text
                rewritten.append({"field": field, "original": item, "rewritten": text})
    if rewritten:
        plan["parameter_policy_reconciliations"] = [
            *(plan.get("parameter_policy_reconciliations") or []), *rewritten]
    return rewritten


def required_numeric_constants(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Enforce explicit curated parameter targets, separately from detector minima."""
    constraints = (error.get("case_migration_policy") or {}).get("required_numeric_constants", [])
    if not constraints:
        return []
    production = _production(edits)
    code = "\n".join(str(edit.get("corrected_code", "")) for edit in production)
    if error.get("repository_includes_patch"):
        # A compiler repair may touch only a caller. Check the projected
        # source, including the existing parameter declaration, rather than
        # requiring every repair to repeat the migration's constants.
        root = Path(str(error.get("repository_path", ""))).resolve()
        paths = set(error.get("policy_source_paths") or [])
        paths.update(str(edit.get("source_file_path", "")) for edit in production)
        paths.add(str(error.get("source_file_path", "")))
        projected = []
        for path in sorted(paths - {""}):
            target = (root / path).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                continue
            content = target.read_text(encoding="utf-8", errors="replace")
            for edit in production:
                if edit.get("source_file_path") != path:
                    continue
                original = str(edit.get("original_code", ""))
                corrected = str(edit.get("corrected_code", ""))
                if original and content.count(original) == 1:
                    content = content.replace(original, corrected, 1)
                else:
                    content += "\n" + corrected
            projected.append(content)
        if projected:
            code = "\n".join(projected)
    from cognalys.solutions.signature_change import blank_comments_and_strings
    code = blank_comments_and_strings(code)
    declarations = re.findall(r"\b(?:int|long|const\s+val|val)\s+([A-Z][\w]*)\s*(?::\s*\w+\s*)?=\s*([0-9][0-9_]*)(?:[lL])?\b", code)
    issues = []
    for constraint in constraints:
        pattern, minimum = constraint["symbol_pattern"], int(constraint["minimum"])
        matches = [(name, int(value.replace("_", ""))) for name, value in declarations if re.fullmatch(pattern, name)]
        if not matches or any(value < minimum for _, value in matches):
            issues.append({"code": "CURRENT_PARAMETER_BELOW_POLICY",
                           "path": production[0].get("source_file_path", "") if production else "",
                           "details": [f"The migration policy requires a named current parameter matching {pattern} with value >= {minimum}; found {matches}. " + constraint.get("purpose", "")]})
    return issues


def _production(edits: List[Dict]) -> List[Dict]:
    return [edit for edit in edits
            if edit.get("edit_kind") != "test"
            and not _is_test_path(str(edit.get("source_file_path", "")))]


def _material_arguments(code: str, match) -> str:
    """The arguments of a material call that carry the bytes, not the tag
    length or algorithm beside them (Tokn: GCMParameterSpec(GCM_TAG_LENGTH,
    iv) was refused as keeping the constant key GCM_TAG_LENGTH)."""
    parts = _split_top_level(_arguments(code, match.end() - 1))
    positions = _MATERIAL_POSITIONS.get(re.sub(r"[\s(.]", "", match.group(0)), (0,))
    return ",".join(parts[index] for index in positions if index < len(parts))


def _material_sources(code: str) -> set:
    atoms = set()
    for match in _MATERIAL_CALL.finditer(code):
        atoms.update(atom for atom in _SOURCE_ATOM.findall(_material_arguments(code, match))
                     if atom not in _NOT_A_SOURCE)
    return atoms


_LOCAL_NAME = re.compile(r"(?<![\w.])([a-z]\w*)\b(?!\s*\()")
# Which arguments carry the bytes: not the algorithm name, the tag length or
# the iteration count beside them.
_MATERIAL_POSITIONS = {
    "SecretKeySpec": (0,), "IvParameterSpec": (0,), "GCMParameterSpec": (1,),
    "PBEKeySpec": (0, 1), "PBEParameterSpec": (0,), "setSeed": (0,),
    "SecureRandom": (0,),
}


def _split_top_level(arguments: str) -> List[str]:
    """``arguments`` split on the commas outside brackets and literals."""
    parts, depth, current, quote = [], 0, "", ""
    for char in arguments:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    return parts


def _initializer(code: str, name: str) -> str:
    """The right-hand side of the last plain assignment to ``name``, or ""."""
    found = re.findall(
        rf"(?<![\w.]){re.escape(name)}\s*(?::\s*[\w.<>?\[\], ]+)?=(?!=)\s*([^;\n]+)", code)
    return found[-1] if found else ""


def constant_material_flows(code: str) -> List[Dict]:
    """Key, IV, salt and seed arguments that come from a constant of the app.

    The argument itself, or a local it names (two assignments deep), holds a
    resource, a build-time field, a named constant or a long literal -- a
    value compiled into the application, the same in every installation of
    a build. MTG Familiar's fetch builds its AES key from
    getString(R.string.key_lastLegalityUpdate) and passes sk.getEncoded() as
    the IV; both adjudications (exp_final_20261019) had the source and a
    fingerprint repeated over every run and still asked whether the resource
    "was originally generated randomly". This states the flow as a fact; the
    adjudication still decides whether the rule is broken.
    """
    flows = []
    for match in _MATERIAL_CALL.finditer(code):
        arguments = _material_arguments(code, match)
        sources = {atom for atom in _SOURCE_ATOM.findall(arguments)
                   if atom not in _NOT_A_SOURCE}
        through = []
        frontier = set(_LOCAL_NAME.findall(arguments))
        for _ in range(2):
            following = set()
            for name in sorted(frontier):
                value = shown = _initializer(code, name)
                inner = _MATERIAL_CALL.search(value)
                if inner:
                    # `sk = new SecretKeySpec(bytes, "AES")`: only the bytes.
                    inner_parts = _split_top_level(_arguments(value, inner.end() - 1))
                    inner_positions = _MATERIAL_POSITIONS.get(
                        re.sub(r"[\s(.]", "", inner.group(0)), (0,))
                    value = ",".join(inner_parts[index] for index in inner_positions
                                     if index < len(inner_parts))
                atoms = {atom for atom in _SOURCE_ATOM.findall(value)
                         if atom not in _NOT_A_SOURCE}
                if atoms:
                    sources |= atoms
                    through.append(f"{name} = {shown.strip()[:160]}")
                following |= set(_LOCAL_NAME.findall(value))
            frontier = following - frontier
        if sources:
            flows.append({
                "call": match.group(0).strip().rstrip("(").strip() + f"({arguments[:160]})",
                "line_in_method": code.count("\n", 0, match.start()) + 1,
                "constant_sources": sorted(sources),
                "through_locals": through,
            })
    return flows


def kept_constant_key_material(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A patch that still builds the key from the constant the finding reports.

    MTG Familiar's fetch (2026-10-12 review): the AES key came from
    getString(R.string.key_lastLegalityUpdate); the patch moved that into a
    provisionTcgpAccessTokenKey() helper, zeroed a copy of the bytes, and the
    key still came from the same string. It compiled and the detector's
    pattern no longer matched the reported line, but nothing was fixed.

    Flagged: a constant source (resource, BuildConfig field, named constant,
    long literal) passed as key, IV, salt or seed material in the original
    code is still present in the patched production code, and the patch
    brings in no other source of key material. A patch that keeps the
    constant only to decrypt existing data while new data uses a generated
    or stored key brings in such a source and is not flagged.
    """
    production = _production(edits)
    original_all = "\n".join(str(edit.get("original_code", "")) for edit in production)
    corrected_all = "\n".join(str(edit.get("corrected_code", "")) for edit in production)
    sources = _material_sources(original_all)
    kept = sorted(atom for atom in sources if atom in corrected_all)
    shipped_all = {literal for literal in _EMBEDDED_CIPHERTEXT.findall(original_all)
                   if literal in corrected_all}
    if sources and shipped_all and not kept:
        # The other half of the same trap: MTG Familiar's patch dropped the
        # constant key and decrypts the shipped ciphertexts with a key the
        # Android Keystore generates on the device, which cannot open data
        # encrypted with the old key -- the credentials stop decrypting and
        # the feature they unlock stops working.
        path = next((str(edit.get("source_file_path", "")) for edit in production
                     if any(literal in str(edit.get("corrected_code", ""))
                            for literal in shipped_all)), "")
        return [{
            "code": "SHIPPED_CIPHERTEXT_UNREADABLE",
            "path": path,
            "details": [
                "the code decrypts ciphertext written into the application with a "
                "constant key; the patch removes that key but keeps the ciphertext, and "
                "a key generated on the device cannot decrypt data encrypted with the "
                "old one, so the feature that reads it breaks. No change inside the app "
                "fixes this: keeping the key ships the secret, replacing it breaks the "
                "data. Report the finding to the maintainer instead of patching it.",
            ],
        }]
    shipped_original = set(_EMBEDDED_CIPHERTEXT.findall(original_all))
    if sources and shipped_original and not kept and not shipped_all:
        # The third way out, from MTG Familiar's next draft (exp_final_20261019
        # subset_policy): drop the constant key and the shipped ciphertexts
        # together and read the credentials from a provider that, in its own
        # words, leaves them "unavailable until" someone provisions them --
        # so on every device the feature they unlock stops working.
        path = next((str(edit.get("source_file_path", "")) for edit in production
                     if any(literal in str(edit.get("original_code", ""))
                            for literal in shipped_original)), "")
        return [{
            "code": "SHIPPED_CIPHERTEXT_REMOVED",
            "path": path,
            "details": [
                "the code decrypts data written into the application with a constant "
                "key; the patch removes both the key and that data, so the app no "
                "longer has what the feature needs and it stops working. Removing the "
                "embedded secret needs a change outside the app (a server that issues "
                "the credential, or the maintainer rotating it). Report the finding to "
                "the maintainer instead of patching it.",
            ],
        }]
    if not kept:
        return []
    # A new key source answers a constant that protects data the app
    # produces. It does not answer one that protects data shipped inside the
    # app: MTG Familiar's patch (exp_final_20261019) moved the TCGplayer
    # credentials into the Android Keystore, but the three ciphertexts are
    # string literals in the source and the constant key that opens them
    # stayed, so the APK still carries both halves of the secret.
    shipped = {literal for literal in _EMBEDDED_CIPHERTEXT.findall(original_all)
               if literal in corrected_all}
    if not shipped and (set(_NEW_KEY_SOURCE.findall(corrected_all))
                        - set(_NEW_KEY_SOURCE.findall(original_all))):
        return []
    path = next((str(edit.get("source_file_path", "")) for edit in production
                 if kept[0] in str(edit.get("corrected_code", ""))), "")
    return [{
        "code": "CONSTANT_KEY_MATERIAL_KEPT",
        "path": path,
        "details": [
            f"the key material still comes from {kept[0][:80]}, the constant this finding "
            "reports; the patch moves or wraps it but adds no other source of key "
            "material, so the key is as fixed and as public as before. Derive or store "
            "a key that is not in the application (Android Keystore, a generated key, a "
            "user secret), or report the finding to the maintainer instead of patching it.",
        ],
    }]


# ---------------------------------------------------------------------------
# 5. An algorithm the protocol or the other side prescribes.
# ---------------------------------------------------------------------------

# (what, how the code names it, algorithms the protocol fixes, why).
# Matched case-sensitively on identifiers, so "hotpot" is not HOTP.
_PROTOCOLS = (
    ("mOTP", re.compile(r"(?<![A-Za-z])(?:m_?OTP|Motp|MOTP|motp)(?![a-z])"),
     {"md5"}, "every mOTP token and server derives the code with MD5"),
    ("HOTP/TOTP (RFC 4226, RFC 6238)",
     re.compile(r"(?<![A-Za-z])(?:[HT]OTP|[Hh]otp|[Tt]otp)(?![a-z])|OneTimePassword|otpauth"
                r"|RFC ?(?:4226|6238)"),
     {"sha1", "hmacsha1"}, "codes must match the authenticator the account was enrolled "
     "with, which uses the algorithm recorded at enrolment (SHA-1 by default)"),
    ("Have I Been Pwned's range API",
     re.compile(r"pwnedpasswords|[Hh]ave[Ii]?[Bb]een[Pp]wned|\bHIBP\b"),
     {"sha1"}, "the service only answers SHA-1 prefixes"),
    ("the WebSocket handshake (RFC 6455)",
     re.compile(r"Sec-WebSocket-(?:Accept|Key)|258EAFA5-E914-47DA-95CA-C5AB0DC85B11"),
     {"sha1"}, "the server checks a Sec-WebSocket-Accept computed with SHA-1"),
    ("HTTP Digest authentication", re.compile(r"WWW-Authenticate|[Dd]igest[Aa]uth|\bHA1\b"),
     {"md5"}, "the server computes the response with the algorithm it announced, MD5 by default"),
    ("Gravatar", re.compile(r"[Gg]ravatar"), {"md5"},
     "Gravatar addresses images by the MD5 of the e-mail address"),
    ("a certificate or key fingerprint",
     re.compile(r"[Ff]ingerprint(?!Manager|Compat|Auth|Sensor)"),
     None, "a fingerprint is compared with values computed elsewhere (a published "
     "fingerprint, a pinned value, one shown to the user before)"),
)
_WEAK_ALGORITHM = re.compile(r"(?<![A-Za-z0-9])(?:Hmac)?(?:MD5|SHA-?1)(?![0-9A-Za-z])",
                             re.IGNORECASE)
_DISPLAY = {"md5": "MD5", "sha1": "SHA-1", "hmacsha1": "HmacSHA1", "hmacmd5": "HmacMD5"}


def _algorithms(code: str) -> set:
    return {name.lower().replace("-", "") for name in _WEAK_ALGORITHM.findall(code)}


def protocol_algorithm_changes(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A patch that replaces the algorithm a protocol prescribes.

    org.cry.otp's mOTP (2026-10-12 review): MD5 became SHA-256 in the mOTP
    generator, so its codes no longer matched the server's. FairEmail's
    getFingerprint: SHA1 became SHA-256 in a fingerprint users compare with
    one published elsewhere. The finding is right that the algorithm is
    weak; the patch is still wrong, because the other side keeps computing
    the old one.

    Flagged: the reported code names one of the protocols above, and a weak
    algorithm it prescribes disappears from the patched production code. A
    patch that keeps it (a legacy path next to a new one) is not flagged.
    """
    production = _production(edits)
    original_all = "\n".join(str(edit.get("original_code", "")) for edit in production)
    corrected_all = "\n".join(str(edit.get("corrected_code", "")) for edit in production)
    # Judged per edit as well: org.cry.otp's second draft (exp_final_20261019)
    # left the MD5 helper class untouched and switched mOTP.java and
    # Profiles.java to a new SHA256 class, so MD5 was still "in the patch"
    # while the code that implements mOTP no longer used it.
    for edit in production:
        before = str(edit.get("original_code", ""))
        after = str(edit.get("corrected_code", ""))
        lost = _algorithms(before) - _algorithms(after)
        if not lost:
            continue
        # The whole file, not only the edited lines: org.cry.otp's Profiles
        # shows an mOTP profile's secret as MD5(seed) under `if (otpType ==
        # Home.OTP_TYPE_MOTP)`, two lines above the one the patch switched
        # to SHA-256, so the secret shown no longer matched the one mOTP.java
        # computes codes with (exp_final_20261019 subset_policy).
        relative = str(edit.get("source_file_path", ""))
        try:
            whole = (Path(str(error.get("repository_path", ""))) / relative).read_text(
                encoding="utf-8", errors="replace") if error.get("repository_path") else ""
        except OSError:
            whole = ""
        own = f"{relative}\n{before}\n{whole}"
        for name, pattern, prescribed, why in _PROTOCOLS:
            dropped = lost if prescribed is None else lost & prescribed
            if dropped and pattern.search(own):
                algorithm = sorted(dropped)[0]
                return [{
                    "code": "PROTOCOL_MANDATED_ALGORITHM",
                    "path": str(edit.get("source_file_path", "")),
                    "details": [
                        f"the code implements {name}, and the patch stops using "
                        f"{_DISPLAY.get(algorithm, algorithm)} there: {why}. Values computed "
                        "the new way stop matching the other side, whatever helper still "
                        "exists elsewhere. Keep the algorithm, or report the finding to the "
                        "maintainer instead of patching it.",
                    ],
                }]
    removed = _algorithms(original_all) - _algorithms(corrected_all)
    if not removed:
        return []
    text = "\n".join([original_all] + [str(error.get(key, "")) for key in
                                       ("class_name", "csv_class", "method_name",
                                        "source_file_path", "method_body")])
    for name, pattern, prescribed, why in _PROTOCOLS:
        if not pattern.search(text):
            continue
        dropped = removed if prescribed is None else removed & prescribed
        if not dropped:
            continue
        algorithm = sorted(dropped)[0]
        return [{
            "code": "PROTOCOL_MANDATED_ALGORITHM",
            "path": next((str(edit.get("source_file_path", "")) for edit in production
                          if algorithm in _algorithms(str(edit.get("original_code", "")))), ""),
            "details": [
                f"the code implements {name}, and the patch replaces "
                f"{_DISPLAY.get(algorithm, algorithm)}: {why}. Values computed the new way "
                "stop matching the other side. Keep the algorithm, or report the finding "
                "to the maintainer instead of patching it.",
            ],
        }]
    return []


# ---------------------------------------------------------------------------
# 6. A patch that pins a protocol version where the code negotiated one.
# ---------------------------------------------------------------------------

# Selectors that ask the provider for its best available protocol, and the
# pinned forms that freeze one version. On Android (Conscrypt) "SSL" and
# "TLS" name the same context, which enables TLS 1.3; a "TLSv1.2" context
# leaves TLS 1.3 off by default.
_SELECTOR_CALL = re.compile(
    r"\b(?P<api>SSLContext)\s*\.\s*getInstance\s*\(\s*\"(?P<name>[^\"]+)\"")
_NEGOTIATING = {"tls", "ssl", "default"}
_PINNED = re.compile(r"^(?:TLSv1(?:\.\d)?|SSLv[23])$", re.IGNORECASE)


_PROTOCOL_LIST = re.compile(
    r"(?:protocols\s*=|setProtocols\s*\(|setEnabledProtocols\s*\()\s*"
    r"(?:arrayOf|new\s+String\s*\[\s*\]\s*)?\s*[({]\s*(?P<names>\"[^)}]*)[)}]")


def _protocol_lists(code: str) -> set:
    """Each explicit protocol list the code assigns, as a tuple of names."""
    return {tuple(re.findall(r'"([^"]+)"', match.group("names")))
            for match in _PROTOCOL_LIST.finditer(code)}


def _selectors(code: str) -> List[str]:
    return [match.group("name") for match in _SELECTOR_CALL.finditer(code)]


def pinned_negotiated_protocol(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A patch that replaces a negotiating protocol selector with one version.

    xivpn's Utils and Session's createRegularNodeOkHttpClient (2026-10-15):
    SSLContext.getInstance("SSL") became getInstance("TLSv1.2") because the
    CrySL rule admits only "TLSv1.2" and "TLSv1.3". The detector that
    reported both, RVSec, admits "TLS" too. "TLS" removes the finding
    without changing a byte on the wire; "TLSv1.2" turns TLS 1.3 off. A
    patch that froze a negotiated version is also what Feeder's maintainer
    refused (PR #1210): "some sites would stop working".

    Flagged: a negotiating selector disappears from the production code and
    a pinned one appears in its place.
    """
    production = _production(edits)
    for edit in production:
        before = [name for name in _selectors(str(edit.get("original_code", "")))
                  if name.lower() in _NEGOTIATING]
        after = [name for name in _selectors(str(edit.get("corrected_code", "")))
                 if _PINNED.match(name)]
        kept = [name for name in _selectors(str(edit.get("corrected_code", "")))
                if name.lower() in _NEGOTIATING]
        # Only a regression is refused: a list that drops TLS 1.3 the code had
        # before. Narrowing {"TLSv1.1", "TLSv1.2"} to {"TLSv1.2"} improves the
        # code without making it ideal and passes.
        list_before = _protocol_lists(str(edit.get("original_code", "")))
        list_after = _protocol_lists(str(edit.get("corrected_code", "")))
        had_tls13 = any("tlsv1.3" in {name.lower() for name in names} for names in list_before)
        if list_after and had_tls13 and all(
                "tlsv1.3" not in {name.lower() for name in names} for names in list_after):
            names = sorted(list_after, key=len)[0]
            return [{
                "code": "PINNED_NEGOTIATED_PROTOCOL",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    f"the patch writes the protocol list {list(names)}, which drops the "
                    "TLS 1.3 the code enabled before. Keep TLSv1.3 in the list (for "
                    "example TLSv1.2 and TLSv1.3).",
                ],
            }]
        if before and after and len(kept) < len(before):
            return [{
                "code": "PINNED_NEGOTIATED_PROTOCOL",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    f'the patch replaces SSLContext.getInstance("{before[0]}"), which '
                    f'negotiates the best protocol both sides support, with "{after[0]}", '
                    "which fixes one version: on Android a TLSv1.2 context leaves TLS 1.3 "
                    'off. Use "TLS" (the same context as "SSL" on the wire), or restrict '
                    "versions on the socket's enabled protocols; if the specification "
                    'admits only exact versions, keep "TLS" and say so in '
                    "review_assumptions.",
                ],
            }]
    return []


# ---------------------------------------------------------------------------
# 7. A patch that removes a setting the user turns on.
# ---------------------------------------------------------------------------

# A preference read: getBoolean("key", ...), getString("key", ...), a
# DataStore booleanPreferencesKey("key"), prefs["key"].
_PREFERENCE_READ = re.compile(
    r"""(?:get(?:Boolean|String|Int|Long|StringSet)\s*\(|"""
    r"""(?:boolean|string|int)PreferencesKey\s*\()\s*"(?P<key>[\w.\-]+)\"""")


def removed_user_setting(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A patch that deletes a behaviour the user switches on in the settings.

    xivpn's Utils (2026-10-15): the patch replaced the trust-all context with
    the platform trust manager and deleted, in SubscriptionWork, the branch
    that used it when the user had turned on "subscription_no_verify" -- a
    setting for subscription servers with self-signed certificates. HomeApp
    (#114) and Feeder (#1210) refused the same kind of change: a weaker
    behaviour the user opted into is the maintainer's design, not a defect
    to remove. Flagged: a preference key the original code read is read
    nowhere in the patched production code.
    """
    production = _production(edits)
    before = {match.group("key") for edit in production
              for match in _PREFERENCE_READ.finditer(str(edit.get("original_code", "")))}
    after = {match.group("key") for edit in production
             for match in _PREFERENCE_READ.finditer(str(edit.get("corrected_code", "")))}
    removed = sorted(before - after)
    if not removed:
        return []
    path = next((str(edit.get("source_file_path", "")) for edit in production
                 if removed[0] in str(edit.get("original_code", ""))), "")
    return [{
        "code": "USER_SETTING_REMOVED",
        "path": path,
        "details": [
            f'the patch removes the behaviour behind the user setting "{removed[0]}": a '
            "choice the user makes in the app's settings is the maintainer's design. Keep "
            "the setting and its branch working, and change only what runs when it is "
            "off; if the finding is the opted-in behaviour itself, report it to the "
            "maintainer instead of patching it.",
        ],
    }]


# ---------------------------------------------------------------------------
# A patch that changes a value on one side while the other side keeps it.
# ---------------------------------------------------------------------------

# A call and the string literal it is given: KeyGenerator.getInstance("HmacSHA1"),
# new SecretKeySpec(key, "AES"), Mac.getInstance("HmacSHA1").
_LITERAL_CALL = re.compile(
    r"(?P<callee>(?:new\s+)?[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)?)\s*\("
    r"(?:[^()\"\n]*,\s*)?\"(?P<value>[^\"\n]{2,60})\"")
_SOURCE_SUFFIXES = (".java", ".kt")
_SKIPPED_DIRECTORIES = {".git", "build", ".gradle", "node_modules"}


def _literal_calls(code: str) -> set:
    return {(re.sub(r"\s+", "", match.group("callee")).removeprefix("new"), match.group("value"))
            for match in _LITERAL_CALL.finditer(code)}


def _repository_calls(root: Path, value: str, edited: set) -> List[tuple]:
    """(path, callee) of application calls given `value`, outside `edited` files."""
    found: List[tuple] = []
    if not root.parts or not root.is_dir():
        return found
    needle = f'"{value}"'
    for path in sorted(root.rglob("*")):
        if path.suffix not in _SOURCE_SUFFIXES or _SKIPPED_DIRECTORIES & set(path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if relative in edited or _is_test_path(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle in text:
            found += [(relative, callee) for callee, literal in _literal_calls(text)
                      if literal == value]
    return found


def value_still_configures_other_api(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A patch that swaps the value one call is given while another API keeps it.

    jimvixx/ShortMessageSecure#83 (2026-10-17): KeyGenerator.getInstance went
    from "HmacSHA1" to "HmacSHA256", but Mac.getInstance("HmacSHA1") still
    consumed the generated key throughout MasterSecret. The generator only
    returns random bytes; renaming it changed nothing the MAC computes, and
    the maintainer said so. The same holds for any pair of APIs configured by
    one shared value -- a key pair generator and the cipher using its keys,
    a key spec and the cipher it is handed to: changing the value on one side
    renames, it does not migrate.

    Flagged: a call kept in the patch receives a different literal, and the
    old literal still configures a call to a different API in the patched
    code or elsewhere in the application.
    """
    production = _production(edits)
    original_all = "\n".join(str(edit.get("original_code", "")) for edit in production)
    corrected_all = "\n".join(str(edit.get("corrected_code", "")) for edit in production)
    before, after = _literal_calls(original_all), _literal_calls(corrected_all)
    kept_callees = {callee for callee, _value in after}
    edited = {str(edit.get("source_file_path", "")) for edit in production}
    root = Path(str(error.get("repository_path", "")))
    for callee, value in sorted(before - after):
        if callee not in kept_callees:
            continue
        others = [("the patched code", other) for other, literal in after
                  if literal == value and other != callee]
        others = others or [(path, other) for path, other
                            in _repository_calls(root, value, edited) if other != callee]
        if not others:
            continue
        where, other = others[0]
        replacement = sorted(literal for name, literal in after if name == callee)[0]
        return [{
            "code": "VALUE_STILL_CONFIGURES_OTHER_API",
            "path": next((str(edit.get("source_file_path", "")) for edit in production
                          if f'"{value}"' in str(edit.get("original_code", ""))), ""),
            "details": [
                f'the patch gives {callee} "{replacement}" instead of "{value}", but '
                f'{other} is still given "{value}" ({where}). Both sides are configured '
                "by the same value, so changing one only renames it; what the code does "
                "with the result is unchanged. Migrate every side together (with its "
                "stored-data compatibility), or report the finding to the maintainer "
                "instead of patching it.",
            ],
        }]
    return []
