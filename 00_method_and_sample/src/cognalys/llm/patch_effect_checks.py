"""Deterministic checks of a finished patch that the behaviour review let through.

Review of the App Manager drafts of 2026-11-01: five of six reached "PR
pronto" although three changed nothing a runtime checker observes (a
delegating alias, a key rebuilt from its own bytes, a discarded read added
for the call order), one turned the MD5 log line into a second identical
SHA-256 line, and one replaced the GCMParameterSpec an AndroidKeyStore
AES/GCM key requires with an IvParameterSpec on the path that decrypts data
the old code stored. Each check here judges the patch, never the finding:
each failure goes back to the generator with its reason, and only the
no-effect ones close a finding (coordinated_generation._NO_EFFECT_GATE_CODES)
when they are all that is left on the last attempt.

The platform facts (which method only calls which, which transformation
needs which parameter type) are data in data/patch_effect_data.json, each
with the source it comes from, so they can be extended without code.
"""

from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

from cognalys.llm.false_positive_rules import governed_call_names

_DATA_PATH = Path(__file__).with_name("data") / "patch_effect_data.json"
_COMMENT = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/")
_TOKEN = re.compile(r'"(?:\\.|[^"\\\n])*"|\w+|\S')


@lru_cache(maxsize=1)
def patch_effect_data() -> Dict:
    try:
        return json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


from cognalys.utils.test_source_sets import is_test_source  # noqa: E402


def _is_test_edit(edit: Dict) -> bool:
    return edit.get("edit_kind") == "test" or is_test_source(str(edit.get("source_file_path", "")))


def _production(edits: List[Dict]) -> List[Dict]:
    return [edit for edit in edits if not _is_test_edit(edit)
            and str(edit.get("edit_kind", "")) not in {"delete", "delete_file"}]


def _code(text: str) -> str:
    return _COMMENT.sub(" ", str(text or ""))


def equivalent_modulo_local_renames(original: str, corrected: str) -> bool:
    """Same token stream once lower-case identifiers are consistently renamed.

    `java.security.KeyPair generateKeyPair = g.generateKeyPair();` and
    `java.security.KeyPair keyPair = g.generateKeyPair();` are the same
    code. A renamed method call would also pass, so callers only use this
    after mapping the one call they know is equivalent.
    """
    left, right = _TOKEN.findall(_code(original)), _TOKEN.findall(_code(corrected))
    if len(left) != len(right):
        return False
    forward: Dict[str, str] = {}
    backward: Dict[str, str] = {}
    for index, (a, b) in enumerate(zip(left, right)):
        # A member (`.name`) or a call (`name(`) is not a local: it must match
        # as written, even when a local of the same name was renamed.
        member = (index and left[index - 1] == "."
                  or index + 1 < len(left) and left[index + 1] == "(")
        if a == b and (member or (a not in forward and b not in backward)):
            continue
        if member or not (re.fullmatch(r"[a-z_]\w*", a) and re.fullmatch(r"[a-z_]\w*", b)):
            return False
        if forward.setdefault(a, b) != b or backward.setdefault(b, a) != a:
            return False
    return True


def _own_method_edits(error: Dict, production: List[Dict]) -> List[Dict]:
    """The edits holding the finding's own method, or every edit when none names it."""
    method = str((error or {}).get("method_name", "")).strip()
    if method:
        declaring = re.compile(r"\b" + re.escape(method) + r"\s*\(")
        own = [edit for edit in production
               if declaring.search(str(edit.get("original_code", "")))]
        if own:
            return own
    return production


# ---------------------------------------------------------------------------
# 1a. A call replaced by an alias that only calls it.
# ---------------------------------------------------------------------------

def delegating_alias_calls(error: Dict, edits: List[Dict]) -> List[Dict]:
    """The finding's method changed only by calling an alias of the same method.

    App Manager's generateRSAKeyPair (2026-11-01): generateKeyPair() became
    genKeyPair(), whose body in the JDK and in Android is `return
    generateKeyPair();`. The checker observes the same call; the draft also
    renamed a local, which changes nothing either. Flagged when every edit
    of the finding's own method is its original code once each known alias
    is read as the method it calls and locals are renamed back.
    """
    aliases = [item for item in patch_effect_data().get("aliases") or []
               if item.get("alias") and item.get("delegates_to")]
    production = _production(edits)
    if not aliases or not production:
        return []
    own = _own_method_edits(error, production)
    used = []
    for edit in own:
        original = _code(edit.get("original_code", ""))
        corrected = _code(edit.get("corrected_code", ""))
        mapped = corrected
        for item in aliases:
            alias_call = re.compile(r"\b" + re.escape(item["alias"]) + r"\b(?=\s*\()")
            if alias_call.search(corrected) and not alias_call.search(original):
                mapped = alias_call.sub(item["delegates_to"], mapped)
                used.append(item)
        if mapped == corrected or not equivalent_modulo_local_renames(original, mapped):
            return []
    if not used:
        return []
    item = used[0]
    return [{
        "code": "DELEGATING_ALIAS_CALL",
        "path": str(own[0].get("source_file_path", "")),
        "details": [
            f"{item['alias']}() only calls {item['delegates_to']}() ({item.get('source', '')}), "
            "so the patch leaves every call the checker observes as it was. Change "
            "what the reported call receives or the call itself, or decide that no "
            "change is needed."
        ],
    }]


# ---------------------------------------------------------------------------
# 1b. An object rebuilt from its own encoded form.
# ---------------------------------------------------------------------------

_REBUILT = re.compile(
    r"(?:\bnew\s+)?\b(?P<type>[A-Z]\w*)\s*\(\s*(?P<source>[a-z_]\w*)\s*\.\s*"
    r"(?:getEncoded\s*\(\s*\)|encoded\b)\s*,\s*(?P<alg>\"[^\"\n]*\"|[\w.]+)\s*\)")
_JAVA_PARAMS = re.compile(r"\b(?P<name>\w+)\s*\((?P<params>[^()]*)\)\s*(?:throws[^{]*)?\{")


def _split_args(text: str) -> List[str]:
    parts, depth, current = [], 0, ""
    for char in text:
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _builders(code: str, type_name: str, alg: str) -> set:
    """Names of functions in `code` that return `new type(..., alg)`."""
    names = set()
    construction = (r"(?:new\s+)?" + re.escape(type_name) + r"\s*\([^;]*,\s*"
                    + re.escape(alg) + r"\s*\)")
    for match in re.finditer(r"\b(\w+)\s*\([^()]*\)\s*(?::\s*[\w.<>?]+\s*)?(?:throws[^{=]*)?"
                             r"(?:\{\s*return\s+|=\s*)" + construction, code):
        names.add(match.group(1))
    return names


def rebuilt_from_own_encoding(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A key the patch rebuilds from its own bytes with the same algorithm.

    App Manager's calculateHmac (2026-11-01): the patch moved
    `new SecretKeySpec(token bytes, "HmacSHA256")` into prepareHmacKey and
    made calculateHmac take that Key, then build
    `new SecretKeySpec(hmacKey.getEncoded(), "HmacSHA256")` from it: the
    same bytes under the same algorithm, so Mac.init receives the key it
    always did. Flagged only when the source is traced to that same
    construction: a local assigned it, or a parameter that every call in
    the patch fills with a function returning it. A key from anywhere else
    (a KeyStore, a generator) passes.
    """
    production = _production(edits)
    corrected_all = "\n".join(_code(edit.get("corrected_code", "")) for edit in production)
    original_all = "\n".join(_code(edit.get("original_code", "")) for edit in production)
    for match in _REBUILT.finditer(corrected_all):
        if re.sub(r"\s+", "", match.group(0)) in re.sub(r"\s+", "", original_all):
            continue
        type_name, source, alg = match.group("type"), match.group("source"), match.group("alg")
        same = (r"(?:new\s+)?" + re.escape(type_name) + r"\s*\([^;]*,\s*" + re.escape(alg) + r"\s*\)")
        traced = bool(re.search(r"\b" + re.escape(source) + r"\s*=\s*" + same, corrected_all))
        if not traced:
            builders = _builders(corrected_all, type_name, alg)
            enclosing = [item for item in _JAVA_PARAMS.finditer(corrected_all[:match.start()])
                         if item.group("name") not in {"if", "for", "while", "switch", "catch",
                                                       "synchronized"}]
            if builders and enclosing:
                method = enclosing[-1]
                params = _split_args(method.group("params"))
                index = next((position for position, param in enumerate(params)
                              if re.search(r"\b" + re.escape(source) + r"\s*$", param)), None)
                calls = [_split_args(call.group(1)) for call in re.finditer(
                    r"(?<![\w.])(?:\w+\.)?" + re.escape(method.group("name")) + r"\s*\(([^;{}]*)\)\s*[;,)]",
                    corrected_all) if call.start() != method.start()]
                calls = [args for args in calls if len(args) == len(params)]
                traced = bool(index is not None and calls and all(
                    re.match(r"(?:[\w.]+\.)?(?:" + "|".join(map(re.escape, builders)) + r")\s*\(",
                             args[index]) for args in calls))
        if traced:
            path = next((str(edit.get("source_file_path", "")) for edit in production
                         if match.group(0) in _code(edit.get("corrected_code", ""))), "")
            shown = re.sub(r"\s+", " ", match.group(0))
            return [{
                "code": "SAME_OBJECT_REBUILT",
                "path": path,
                "details": [
                    f"{shown} rebuilds the {type_name} "
                    f"the patch already made with {alg} from its own encoded bytes, so the "
                    "reported call receives the same key as before. Change where the key "
                    "comes from, or decide that no change is needed."
                ],
            }]
    return []


# ---------------------------------------------------------------------------
# 1c. A governed read added as a statement of its own.
# ---------------------------------------------------------------------------

# A read by its name: it returns what it finds and changes nothing.
_QUERY_NAME = re.compile(r"^(?:get|is|has|contains)(?:[A-Z]\w*)?$")
_BARE_CALL = re.compile(
    r"^(?P<call>[\w$.]+(?:\([^;{}]*\))?\s*\.\s*(?P<name>[\w$]+)\s*\([^;{}]*\))\s*;?$")


# Kotlin blocks whose last statement is not a value: a function body, a loop,
# a class or init block. Any other block (`run {`, `x.let { a ->`, `if (c) {`,
# `when`, `try {`, `synchronized(lock) {`) may yield its last expression.
_KOTLIN_STATEMENT_BLOCK = re.compile(
    r"\b(?:fun|for|while|do|init|class|object|interface|constructor)\b|\b(?:get|set)\s*\(")


def _kotlin_block_value(lines: List[str], index: int) -> bool:
    """Whether lines[index], the last statement before a `}`, may be its block's value.

    `val key = run {\\n    ks.getEntry(alias, null)\\n}` (2026-11-01 review):
    the bare call is what run returns. When the block's opening line cannot
    be found, it may be a value too: the check does not flag what it cannot
    decide.
    """
    depth = 0
    for line in reversed(lines[:index]):
        text = line.strip()
        depth += text.count("}") - text.count("{")
        if depth < 0:
            return not _KOTLIN_STATEMENT_BLOCK.search(text)
    return True


def discarded_governed_reads(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A read of the governed API the patch adds as a statement, its result unused.

    App Manager's addKeyPair (2026-11-01): `mAmKeyStore.getEntry(alias, new
    KeyStore.PasswordProtection(password));` was added before the existing
    getKey call in both getKey methods; the entry went nowhere, and the
    line existed so the checker sees the call order it expects.
    semantic_validation.discarded_governed_calls only knew the
    `try { read } catch {}` form. Flagged: a production statement the patch
    adds that is one call to a method the specification's events govern,
    named as a read (get/is/has/contains), with nothing taking its value.
    """
    governed = {name for name in governed_call_names((error or {}).get("specification_context"))
                if _QUERY_NAME.match(name)}
    if not governed:
        return []
    issues = []
    for edit in _production(edits):
        original = {re.sub(r"\s+", "", line) for line in
                    _code(edit.get("original_code", "")).splitlines()}
        lines = _code(edit.get("corrected_code", "")).splitlines()
        kotlin = str(edit.get("source_file_path", "")).endswith((".kt", ".kts"))
        found = []
        for index, line in enumerate(lines):
            text = line.strip()
            match = _BARE_CALL.match(text)
            if not match or match.group("name") not in governed:
                continue
            if re.sub(r"\s+", "", text) in original:
                continue
            previous = next((item.strip() for item in reversed(lines[:index]) if item.strip()), "")
            following = next((item.strip() for item in lines[index + 1:] if item.strip()), "")
            # The value may be taken by the line above (an assignment or an
            # argument list left open), or the line may be a branch's body.
            if previous.endswith(("=", "(", ",", "->", ".", "+", "&&", "||", "?:", "return")):
                continue
            if re.match(r"^(?:\}\s*)?(?:else\b|(?:if|while|for)\s*\()", previous) \
                    and not previous.endswith("{"):
                continue
            if following.startswith((".", "?.", "?:", "as ", "as?")):
                continue
            if kotlin and following.startswith("}") and _kotlin_block_value(lines, index):
                continue
            found.append(re.sub(r"\s+", " ", match.group("call")))
        if found:
            issues.append({
                "code": "GOVERNED_CALL_RESULT_DISCARDED",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    "the patch adds a read of the governed API whose result nothing uses, "
                    "so it changes nothing the application does and only makes the "
                    "detector observe the call: " + " | ".join(found[:3])
                    + ". Use the compliant call for what the code needs (its result "
                    "replaces the non-compliant one), or decide that no fix changes behaviour."
                ],
            })
    return issues


# ---------------------------------------------------------------------------
# 2. A statement duplicated next to its twin.
# ---------------------------------------------------------------------------

_DUPLICATE_WINDOW = 3
_OPEN_ENDING = ("=", "(", ",", "->", ".", "+", "&&", "||", "?:")


def duplicated_adjacent_statements(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A statement the patch makes identical to one beside it in the same block.

    App Manager's getDigest (2026-11-01): logEncoded logged SHA-256, SHA-1
    and MD5; the patch turned the MD5 line into a second copy of the SHA-256
    line, so the log shows SHA-256 twice and MD5 no more. The review passed
    it. Flagged: a production statement (a call or assignment of some
    length) whose count in the edit grows and which, after the patch, has
    an identical twin at the same indentation within a few lines and no
    block boundary between them. Duplicates the original already had pass.
    """
    issues = []
    for edit in _production(edits):
        original = [re.sub(r"\s+", " ", line).strip()
                    for line in _code(edit.get("original_code", "")).splitlines()]
        lines = _code(edit.get("corrected_code", "")).splitlines()
        normal = [re.sub(r"\s+", " ", line).strip() for line in lines]
        # Only a line that replaces a removed one can have turned it into a
        # copy of its neighbour (getDigest's MD5 line): an added separator or
        # `md.update(salt)` on both sides of the input repeats on purpose.
        replacements = set()
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                a=original, b=normal, autojunk=False).get_opcodes():
            if tag == "replace":
                removed = {item for item in original[i1:i2] if item}
                replacements.update(j for j in range(j1, j2) if normal[j] not in removed)
        found = []
        for index, text in enumerate(normal):
            if (len(text) < 15 or "(" not in text or text in found
                    or normal.count(text) <= original.count(text)):
                continue
            previous = next((item for item in reversed(normal[:index]) if item), "")
            if previous.endswith(_OPEN_ENDING):
                # The tail of an expression (fread's Kotlin overloads,
                # `fun f(): T =\n    delegate(...)`), not a statement.
                continue
            indent = len(lines[index]) - len(lines[index].lstrip())
            for other in range(index + 1, min(len(normal), index + 1 + _DUPLICATE_WINDOW * 2)):
                between = normal[index + 1:other]
                if any("{" in item or "}" in item or item.endswith(_OPEN_ENDING)
                       or item.startswith("@") or re.search(r"\bfun\b|\bdef\b", item)
                       for item in between):
                    break
                if len([item for item in between if item]) >= _DUPLICATE_WINDOW:
                    break
                if (normal[other] == text and (index in replacements or other in replacements)
                        and len(lines[other]) - len(lines[other].lstrip()) == indent):
                    found.append(text)
                    break
        if found:
            issues.append({
                "code": "DUPLICATED_ADJACENT_STATEMENT",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    "the patch leaves two identical statements next to each other, so "
                    "whatever the replaced line did (a log entry, a value) is lost and "
                    "the other is repeated: " + " | ".join(found[:2])
                    + ". Change the reported statement itself, or remove it, without "
                    "duplicating its neighbour."
                ],
            })
    return issues


# ---------------------------------------------------------------------------
# 3. The parameter type a transformation requires, removed.
# ---------------------------------------------------------------------------

_OTHER_SPEC = re.compile(r"(?:\bnew\s+|(?<![\w.]))(?P<type>[A-Z]\w*ParameterSpec)\s*\(")
_INIT_CALL = re.compile(r"\.\s*init\s*\((?P<args>[^;]*)\)")


def _init_builds_other_spec(code: str, spec: str) -> bool:
    """Whether an init call in ``code`` receives a parameter spec of a type other than ``spec``.

    Built inline in the call, assigned in ``code`` to a variable the call
    passes, or a variable or parameter ``code`` declares with that type (App
    Manager's `decryptData(context, IvParameterSpec ivSpec, ...)`).
    """
    for call in _INIT_CALL.finditer(code):
        args = call.group("args")
        if any(match.group("type") != spec for match in _OTHER_SPEC.finditer(args)):
            return True
        for name in re.findall(r"\b([a-z_]\w*)\b", args):
            declared = re.search(r"\b(?P<type>[A-Z]\w*ParameterSpec)\s+" + re.escape(name) + r"\b"
                                 r"|\b" + re.escape(name) + r"\s*:\s*(?P<kt>[A-Z]\w*ParameterSpec)\b",
                                 code)
            if declared and (declared.group("type") or declared.group("kt")) != spec:
                return True
            assigned = re.search(r"\b" + re.escape(name) + r"\s*=\s*(?P<value>[^;\n]+)", code)
            if assigned and any(match.group("type") != spec
                                for match in _OTHER_SPEC.finditer(assigned.group("value"))):
                return True
    return False


def required_parameter_spec_removed(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A cipher init that loses the parameter type its transformation requires.

    App Manager's decryptData (2026-11-01): the patch replaced
    `new GCMParameterSpec(128, iv)` with `new IvParameterSpec(iv)` on the
    path that decrypts what the old code stored with an AndroidKeyStore
    AES/GCM key, and that provider refuses anything but GCMParameterSpec:
    every stored password would stop decrypting. Flagged: an edit whose
    original builds the required parameter type and whose corrected code
    no longer does, gives an init call a spec of another type
    (_init_builds_other_spec), and still names the transformation (in a
    literal or an identifier). A patch that moves off the transformation,
    an init without parameters (a provider-generated IV) and a spec built
    in a helper pass (2026-11-01 review).
    """
    issues = []
    for rule in patch_effect_data().get("required_parameter_specs") or []:
        word, spec = str(rule.get("transformation_word", "")), str(rule.get("parameter_spec", ""))
        if not word or not spec:
            continue
        builds = re.compile(r"\b" + re.escape(spec) + r"\s*\(")
        names = re.compile(r"(?:/|\b|_)" + re.escape(word) + r"(?:/|\b|_)")
        for edit in _production(edits):
            original = _code(edit.get("original_code", ""))
            corrected = _code(edit.get("corrected_code", ""))
            if not builds.search(original) or builds.search(corrected):
                continue
            # Only the corrected init that now builds another spec type
            # (decryptData's IvParameterSpec). An init without parameters
            # (a provider-generated IV) or a spec built in a helper or in
            # another edit cannot be judged here, so it passes.
            if not _init_builds_other_spec(corrected, spec):
                continue
            if not names.search(re.sub(re.escape(spec), " ", corrected)):
                continue
            issues.append({
                "code": "REQUIRED_PARAMETER_SPEC_REMOVED",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    f"the {word} cipher here needs a {spec} ({rule.get('source', '')}); the "
                    f"patch removes it from {edit.get('symbol', 'this code')}, so the init "
                    "fails, and data the old code stored can no longer be decrypted. Keep "
                    f"{spec} for this transformation."
                ],
            })
    return issues


# ---------------------------------------------------------------------------
# 4. Managers built over an empty store, passed where null was.
# ---------------------------------------------------------------------------

# `store = X.getInstance(...)` (Java or Kotlin, optionally `.apply { load(null...) }`).
_STORE_DECLARED = re.compile(
    r"\b(?P<name>[a-z_]\w*)\s*(?::\s*[\w.<>?]+\s*)?=\s*[\w.]*\bgetInstance\s*\(")
_NULL_ARGUMENT = re.compile(r"\binit\s*\(\s*null\b")
_STORE_FILLED = re.compile(r"\b(?:set\w*Entry|setEntry)\s*\(|\bload\s*\(\s*(?!null\b)[^)\s]")


def _file_format_store(arguments: str) -> bool:
    """Whether a getInstance call (its text from the first argument on) names a
    store type that is empty after load(null) (data: empty_store_types)."""
    types = patch_effect_data().get("empty_store_types") or {}
    first = arguments.split(")", 1)[0]
    call = str(types.get("default_type_call", "") or "")
    if call and re.search(r"\b" + re.escape(call) + r"\b", first):
        return True
    literal = re.match(r'\s*"([^"]+)"', arguments)
    return bool(literal) and literal.group(1).upper() in {
        str(item).upper() for item in types.get("literals") or []}


def _empty_stores(code: str) -> List[str]:
    """Names of objects ``code`` creates with getInstance and loads only from null, never filled."""
    names = []
    for match in _STORE_DECLARED.finditer(code):
        name = match.group("name")
        tail = code[match.end():]
        if not _file_format_store(tail):
            continue
        statement = tail.split("\n\n", 1)[0]
        loaded_inline = re.match(r"[^;\n]*\)\s*\.\s*apply\s*\{\s*load\s*\(\s*null\b", tail)
        loaded_later = re.search(r"\b" + re.escape(name) + r"\s*\.\s*load\s*\(\s*null\b", tail)
        if not (loaded_inline or loaded_later):
            continue
        filled = re.search(r"\b" + re.escape(name) + r"\s*\.\s*(?:set\w*Entry|load\s*\(\s*(?!null\b))",
                           tail)
        if filled or (loaded_inline and _STORE_FILLED.search(
                re.sub(r"\bload\s*\(\s*null\b", " ", statement))):
            continue
        names.append(name)
    return names


def empty_store_managers(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A null argument replaced by managers a factory built over an empty store.

    Hermes Control's CertificateSocketFactory (2026-11-02): `init(null,
    trust, null)` became `init(keyManagers, trust, null)`, the key managers
    coming from a KeyManagerFactory initialised over a KeyStore created and
    loaded from null in the patch, with nothing ever stored in it. An empty
    store holds no key and no certificate, so the context presents no client
    certificate either way: the patch only wraps what null already meant.
    Flagged when an edit's corrected code has fewer `init(null` calls than
    its original, creates such an empty store (getInstance, load from null,
    no set...Entry and no load from a stream), and passes it to an init.
    """
    issues = []
    for edit in _production(edits):
        original = _code(edit.get("original_code", ""))
        corrected = _code(edit.get("corrected_code", ""))
        if len(_NULL_ARGUMENT.findall(corrected)) >= len(_NULL_ARGUMENT.findall(original)):
            continue
        for name in _empty_stores(corrected):
            if name in _empty_stores(original):
                continue
            if not re.search(r"\binit\s*\(\s*" + re.escape(name) + r"\b", corrected):
                continue
            issues.append({
                "code": "EMPTY_STORE_MANAGERS",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    f"`{name}` is created and loaded from null in the patch and nothing is "
                    "ever stored in it, so the managers built over it hold no key or "
                    "certificate: passing them where null was passed changes nothing the "
                    "connection does (and an empty trust store would reject every server). "
                    "Load the material the code really needs, or decide that no change is "
                    "needed."
                ],
            })
            break
    return issues


def patch_effect_issues(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Every check of this module, in the semantic-validation error shape."""
    return [
        *delegating_alias_calls(error, edits),
        *rebuilt_from_own_encoding(error, edits),
        *discarded_governed_reads(error, edits),
        *duplicated_adjacent_statements(error, edits),
        *required_parameter_spec_removed(error, edits),
        *empty_store_managers(error, edits),
    ]
