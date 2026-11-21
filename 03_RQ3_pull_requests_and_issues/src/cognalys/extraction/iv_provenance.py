"""Whether the IV a parameter spec carries is the one the cipher provider chose.

Cryptomator Lite run_v6 (2026-10-30): RVSec reported "the IV material was
not observed to come from a randomized source" at CipherGCM.getIvParameterSpec
(`return new GCMParameterSpec(128, iv)`). Its only callers are BaseCipher's
decrypt paths, which take `iv` from the first bytes of the ciphertext, and
BaseCipher encrypts with `cipher.init(ENCRYPT_MODE, key)` -- no IV -- and
stores `cipher.getIV()` in front of the result. The IV is the provider's own
random one; the monitor tracks a randomized producer by object identity and
never sees the provider's internal source. The adjudicator lacked that chain,
blocked on "the upstream IV producer is not supplied", and the evidence-gap
promotion generated a patch on the "runtime observation" alone: a SecureRandom
IV passed to init for an AndroidKeyStore key, which refuses a caller IV, so
every encryption would have thrown. Amethyst#4642 had already been closed by
its maintainers for the same reason (a provider-generated GCM IV is random).

provider_generated_iv answers the question from the checkout: for each IV
spec the reported method builds, whether every init that receives it is a
decryption, whether every encryption in those files lets the provider choose
the IV (init with no parameter spec), and whether that encryption keeps the
IV it chose (getIV()/iv). Anything it cannot follow -- a spec returned
further, stored in a field, passed to an unknown method, an init whose mode
is a variable, an encryption that supplies its own IV, a constant IV -- is
not_established, never guessed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cognalys.extraction.evidence_facts import (
    MAX_CALLER_FILES, _blank_comments, _classify_material, _declares,
    _matching_close, _parameter_names, _source_files, source_level_name,
)
from cognalys.utils.detector_labels import report_kind
from cognalys.utils.finding_source import is_runtime_monitor_tool
from cognalys.utils.test_source_sets import is_test_source_path

# The IV or nonce argument of each spec a cipher takes one from.
_IV_SPEC_ARGUMENT = {"IvParameterSpec": 0, "GCMParameterSpec": 1, "ChaCha20ParameterSpec": 0}
_IV_SPEC_CALL = re.compile(r"\b(?P<api>IvParameterSpec|GCMParameterSpec|ChaCha20ParameterSpec)\s*\(")
_INIT_CALL = re.compile(r"(?:(?P<receiver>[A-Za-z_][\w$]*)\s*[?!]*\.\s*)?(?<![\w$])init\s*\(")
_CONSTANT_NAME = re.compile(r"(?:[\w$]+\.)*[A-Z][A-Z0-9_]+")
_SOURCE_FILE_LIMIT = 6000
_NOT_A_TYPE = frozenset({"return", "new", "throw", "else", "yield", "case", "in", "is", "await"})


def _arguments(code: str, text: str, open_index: int, close: int) -> List[str]:
    """The call's arguments, split where `code` (literals blanked) has top-level commas."""
    parts, depth, start = [], 0, open_index + 1
    for index in range(open_index + 1, close):
        char = code[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    if text[start:close].strip():
        parts.append(text[start:close].strip())
    return parts


def _mode(argument: str) -> str:
    """encrypt, decrypt or unknown for a cipher mode argument; "" for anything else."""
    if re.search(r"\b(?:ENCRYPT|WRAP)_MODE\b", argument):
        return "encrypt"
    if re.search(r"\b(?:DECRYPT|UNWRAP)_MODE\b", argument):
        return "decrypt"
    if argument.strip() in {"1", "3"}:
        return "encrypt"
    if argument.strip() in {"2", "4"}:
        return "decrypt"
    if re.search(r"(?i)mode", argument):
        return "unknown"
    return ""


def _random_source(argument: str, text: str) -> bool:
    """A SecureRandom passed as init's third argument: no IV, the provider still chooses it."""
    argument = argument.strip()
    if re.search(r"\bSecureRandom\b", argument):
        return True
    if not re.fullmatch(r"[A-Za-z_][\w$.]*", argument):
        return False
    name = re.escape(argument.split(".")[-1])
    return bool(re.search(
        rf"\bSecureRandom\s+{name}\b|\b{name}\s*(?::\s*SecureRandom\b|=\s*(?:new\s+)?SecureRandom\b)",
        text))


def cipher_inits(text: str) -> List[Dict]:
    """Every Cipher.init-shaped call in `text`: receiver, mode, arguments, span.

    provider_iv: an encryption (or an init whose mode is a variable) given
    no parameter spec -- two arguments, or a SecureRandom third -- so the
    provider generates the IV itself.
    """
    code = _blank_comments(text, literals=True)
    plain = _blank_comments(text)
    found = []
    for match in _INIT_CALL.finditer(code):
        open_index = match.end() - 1
        close = _matching_close(code, open_index, "(", ")")
        if close < 0:
            continue
        arguments = _arguments(code, plain, open_index, close)
        mode = _mode(arguments[0]) if len(arguments) >= 2 else ""
        if not mode:
            continue
        found.append({
            "receiver": match.group("receiver") or "", "mode": mode,
            "arguments": arguments, "start": match.start(), "open": open_index,
            "close": close, "line": code.count("\n", 0, match.start()) + 1,
            "text": re.sub(r"\s+", " ", plain[match.start():close + 1])[:160],
            "provider_iv": mode != "decrypt" and (
                len(arguments) == 2
                or (len(arguments) == 3 and _random_source(arguments[2], plain))),
        })
    return found


def encryption_keeps_provider_iv(text: str) -> Optional[Dict]:
    """How the file's encryption lets the provider choose the IV and keeps it, or None.

    Every encryption init (and every init whose mode is a variable) must
    pass no parameter spec, at least one must be a stated ENCRYPT_MODE, and
    the IV of one of those ciphers must be read back (getIV(), iv,
    getParameters()): an IV the code throws away cannot be the one a
    decryption later reads.
    """
    inits = cipher_inits(text)
    encryptions = [item for item in inits if item["mode"] != "decrypt"]
    if (not any(item["mode"] == "encrypt" for item in encryptions)
            or any(not item["provider_iv"] for item in encryptions)):
        return None
    code = _blank_comments(text, literals=True)
    plain = _blank_comments(text)
    for receiver in dict.fromkeys(item["receiver"] for item in encryptions if item["receiver"]):
        read = re.search(
            rf"(?<![\w$]){re.escape(receiver)}\s*[?!]*\.\s*"
            r"(?:getIV\s*\(\s*\)|iv\b(?!\s*[(=])|getParameters\s*\(|parameters\b(?!\s*[(=]))", code)
        if read:
            line_start = code.rfind("\n", 0, read.start()) + 1
            line_end = code.find("\n", read.end())
            return {
                "encryptions": [{"line": item["line"], "init": item["text"]}
                                for item in encryptions],
                "iv_read_back": {
                    "line": code.count("\n", 0, read.start()) + 1,
                    "statement": plain[line_start:line_end if line_end >= 0 else len(plain)]
                    .strip()[:160],
                },
            }
    return None


def _statement_prefix(code: str, position: int) -> str:
    """The text of the statement before `position`, from its last `;`, brace or line start."""
    start = max(code.rfind(char, 0, position) for char in ";{}\n") + 1
    return code[start:position]


def _local_bound(prefix: str) -> str:
    """The local a statement's prefix (`Type name =`, `val name: T =`) assigns, or ""."""
    match = re.search(
        r"(?:(?<![\w$.])(?P<type>[\w$.<>\[\]?]+)\s+|\b(?:val|var)\s+)(?P<name>[A-Za-z_]\w*)"
        r"\s*(?::\s*[\w$.<>?\[\]]+\s*)?=\s*(?:new\s+)?(?:[\w$]+\s*\.\s*)*$", prefix)
    if not match or (match.group("type") or "") in _NOT_A_TYPE:
        return ""
    return match.group("name")


def _block_end(code: str, position: int) -> int:
    """The offset of the brace closing the block that encloses `position`."""
    depth = 0
    for index in range(position, len(code)):
        if code[index] == "{":
            depth += 1
        elif code[index] == "}":
            depth -= 1
            if depth < 0:
                return index
    return len(code)


def _argument_index(code: str, item: Dict, position: int) -> int:
    """Which top-level argument of the init `position` lies in; -1 inside a nested call."""
    depth, index = 0, 0
    for offset in range(item["open"] + 1, position):
        char = code[offset]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            index += 1
    return index if depth == 0 else -1


def _destination(code: str, inits: List[Dict], start: int, end: int) -> Tuple[str, List[Dict]]:
    """Where the value of the expression code[start:end] goes.

    ("inits", [...]) when every use reaches an init's arguments after the
    first two, ("returned", []) when it is returned, ("", []) otherwise.
    """
    for item in inits:
        if item["open"] < start and end <= item["close"]:
            # Only as the parameter argument: a key built from the value is
            # not an IV use.
            return ("inits", [item]) if _argument_index(code, item, start) >= 2 else ("", [])
    prefix = _statement_prefix(code, start)
    if re.search(r"\breturn\s+(?:new\s+)?(?:[\w$]+\s*\.\s*)*$", prefix) or re.search(
            r"\bfun\b[^=]*=\s*(?:[\w$]+\s*\.\s*)*$", prefix):
        return "returned", []
    local = _local_bound(prefix)
    if not local:
        return "", []
    block_end = _block_end(code, end)
    uses = [match.start() for match in re.finditer(
        rf"(?<![\w$.]){re.escape(local)}\b(?!\s*=[^=])", code[end:block_end])]
    if not uses:
        return "", []
    reached = []
    for offset in uses:
        absolute = end + offset
        if re.search(r"\breturn\s+$", _statement_prefix(code, absolute)):
            return "returned", []
        owner = next((item for item in inits if item["open"] < absolute < item["close"]), None)
        if owner is None or _argument_index(code, owner, absolute) < 2:
            return "", []
        reached.append(owner)
    # A local both passed to an init and returned is followed no further.
    return "inits", list({id(item): item for item in reached}.values())


_FIXED_OR_CALLER_CHOSEN = frozenset({
    "fresh_zero_filled", "constant_literal", "filled_by_nextBytes", "written_after_creation"})


def _material_refusal(api: str, argument: str, scopes: List[str]) -> str:
    """Why bytes passed as the IV are not a provider's IV, or "" when nothing says so."""
    material = _classify_material(argument, scopes)
    if material in {"fresh_zero_filled", "constant_literal"} or _CONSTANT_NAME.fullmatch(argument):
        return f"{api}({argument}): the IV is constant ({material})"
    if material in _FIXED_OR_CALLER_CHOSEN:
        return f"{api}({argument}): the code supplies its own IV ({material})"
    return ""


class _Reader:
    """The reported method, its file and (once asked) its call sites."""

    def __init__(self, repo_root: Path, body: str, method_name: str,
                 declaring_path: str, declaring_type: str):
        self.repo_root, self.method_name = repo_root, method_name
        self.declaring_path, self.declaring_type = declaring_path, declaring_type
        self.code = _blank_comments(body, literals=True)
        self.plain = _blank_comments(body)
        try:
            self.file_source = ((repo_root / declaring_path).read_text(
                encoding="utf-8", errors="replace") if declaring_path else "")
        except OSError:
            self.file_source = ""
        self.parameters = _parameter_names(body)
        self.inits = cipher_inits(body)
        self._callers = None

    def callers(self):
        if self._callers is None:
            self._callers = _caller_destinations(
                self.repo_root, self.method_name, self.declaring_type, self.declaring_path)
        return self._callers

    def reached_inits(self, api: str, argument: str, start: int, end: int):
        """[(path, file source, init)] the spec reaches, or the reason it is not followed."""
        where, reached = _destination(self.code, self.inits, start, end)
        if where == "inits":
            return [(self.declaring_path, self.file_source, item) for item in reached]
        if where != "returned":
            return f"{api}({argument}): where the spec goes is not followed"
        callers = self.callers()
        if isinstance(callers, str):
            return callers
        found = []
        for caller in callers:
            if argument in self.parameters:
                index = self.parameters.index(argument)
                passed = caller["arguments"][index] if index < len(caller["arguments"]) else ""
                refusal = _material_refusal(api, passed, [caller["file_code"]])
                if refusal:
                    return f"{caller['source_file_path']}:{caller['line']}: {refusal}"
            found.extend((caller["source_file_path"], caller["source"], item)
                         for item in caller["inits"])
        return found

    def use(self, construction: "re.Match"):
        """The record of one IV spec the method builds, or the reason it is not established."""
        api = construction.group("api")
        open_index = construction.end() - 1
        close = _matching_close(self.code, open_index, "(", ")")
        arguments = _arguments(self.code, self.plain, open_index, close) if close >= 0 else []
        position = _IV_SPEC_ARGUMENT[api]
        if len(arguments) <= position:
            return f"{api}: no IV argument"
        argument = arguments[position]
        refusal = _material_refusal(api, argument, [self.plain, _blank_comments(self.file_source)])
        if refusal:
            return refusal
        reached = self.reached_inits(api, argument, construction.start(), close + 1)
        if isinstance(reached, str):
            return reached
        if not reached:
            return f"{api}({argument}): no init receives the spec"
        not_decrypting = [f"{path}:{item['line']} {item['text']}" for path, _, item in reached
                          if item["mode"] != "decrypt"]
        if not_decrypting:
            return (f"{api}({argument}) reaches an init that is not a decryption: "
                    + "; ".join(not_decrypting[:2]))
        encryption = {}
        for path, source, _ in reached:
            kept = encryption.get(path) or (encryption_keeps_provider_iv(source) if source else None)
            if not kept:
                return (f"{path or 'the reported file'} does not encrypt with a "
                        "provider-chosen IV that it keeps")
            encryption[path] = kept
        return {
            "api": api, "argument": argument[:120],
            "line": self.code.count("\n", 0, construction.start()) + 1,
            "decryptions": [f"{path}:{item['line']} {item['text']}"
                            for path, _, item in reached][:6],
            "encryptions": [
                {"source_file_path": path,
                 "inits": [f"{path}:{entry['line']} {entry['init']}"
                           for entry in kept["encryptions"]][:4],
                 "iv_read_back": (f"{path}:{kept['iv_read_back']['line']} "
                                  f"{kept['iv_read_back']['statement']}")}
                for path, kept in encryption.items()],
        }


def provider_generated_iv(
    repo_root: Path, method_body: str, method_name: str,
    declaring_path: str = "", declaring_type: str = "",
    governed: Optional[List[str]] = None,
) -> Dict:
    """For the IV specs the reported method builds, whether the provider chose that IV.

    governed: the spec classes the finding's rule is about (all IV specs
    when None); constructions of others in the method are not judged.

    provider_generated: every spec it builds goes only to decryptions (in
    the method or, when it returns the spec, at every call site in the
    checkout), each with a non-constant IV, and every file holding those
    decryptions encrypts with init(ENCRYPT_MODE, key) and no parameter spec
    and keeps the IV the provider chose. That IV is random by the platform's
    contract (Cipher.init: a cipher that needs parameters it was not given
    generates them from its own random source).
    """
    body = str(method_body or "")
    if not body.strip():
        return {"status": "no_method_body"}
    reader = _Reader(Path(repo_root) if repo_root else Path(), body, method_name,
                     declaring_path, declaring_type)
    constructions = [match for match in _IV_SPEC_CALL.finditer(reader.code)
                     if governed is None or match.group("api") in governed]
    if not constructions:
        return {"status": "no_iv_constructor"}
    uses = []
    for construction in constructions:
        use = reader.use(construction)
        if isinstance(use, str):
            return {"status": "not_established", "reason": use}
        uses.append(use)
    files = sorted({entry["source_file_path"] for use in uses for entry in use["encryptions"]})
    return {
        "status": "provider_generated",
        "uses": uses,
        "statement": (
            "Every IV spec this method builds reaches only decryptions, and "
            f"{', '.join(files) or 'the file'} encrypts with Cipher.init(ENCRYPT_MODE, key) "
            "and no parameter spec, then keeps the IV the cipher returns: the provider "
            "generated that IV from its own random source, and the IV reaching the "
            "spec is that one, read back for decryption."
        ),
        "bound": (
            "Lexical: specs followed through locals, returns and the call sites "
            f"of the method (at most {MAX_CALLER_FILES} files outside tests); "
            "anything not followed is not_established."
        ),
    }


def governed_iv_specs(specification_context) -> List[str]:
    """The IV specs (simple names) the finding's specification governs.

    Read from the specification's own applies_to_types, or the older
    expected_api_simple_name, as source_facts.governed_api_types does.
    """
    context = specification_context if isinstance(specification_context, dict) else {}
    values = context.get("applies_to_types") or []
    if isinstance(values, str):
        values = [values]
    names = [str(value).strip().rsplit(".", 1)[-1].rsplit("$", 1)[-1] for value in values]
    if not any(names):
        names = [str((context.get("generation_evidence") or {}).get(
            "expected_api_simple_name", "")).strip()]
    return [name for name in dict.fromkeys(names) if name in _IV_SPEC_ARGUMENT]


def iv_provenance_fact(error: Dict) -> Dict:
    """provider_generated_iv for a finding record; never raises.

    Read from the record's own fields, so the evidence packet, the
    adjudication and the patch check all derive the same fact.
    """
    record = error if isinstance(error, dict) else {}
    repository = str(record.get("repository_path", "") or "")
    if not repository:
        return {"status": "no_checkout"}
    # Only for a rule about the IV spec itself: tokn's KeystoreManager.decrypt
    # builds a GCMParameterSpec from the provider's IV, and its CipherSpec
    # finding there ("no generator of the key given to Cipher.init was
    # observed") is about the key, which this fact says nothing about.
    governed = governed_iv_specs(record.get("specification_context"))
    if not governed:
        return {"status": "rule_does_not_govern_an_iv_spec"}
    # Only for a runtime monitor's "no randomized producer observed": the
    # provider's own random source is what such a monitor cannot see. A value
    # check (a constant or contradicted IV, a tag length outside the rule), an
    # echo of a refused producer, an origin the monitor saw and the check does
    # not admit, or a static analyzer's data flow reports something the
    # provider's IV does not answer. The label's report kind is read from
    # data/detector_labels.json.
    if not is_runtime_monitor_tool(str(record.get("tool", "") or "")):
        return {"status": "not_a_runtime_report"}
    detector_context = record.get("detector_context") or {}
    label = str(detector_context.get("label", "") or "").strip()
    if label and report_kind(detector_context) != "unobserved_origin":
        return {"status": "report_is_not_an_unobserved_origin", "label": label}
    try:
        return provider_generated_iv(
            Path(repository), str(record.get("method_body", "") or ""),
            str(record.get("method_name", "") or ""),
            str(record.get("source_file_path", "") or ""),
            str(record.get("csv_class", "") or ""), governed,
        )
    except Exception as exc:  # noqa: BLE001 -- a fact is recorded, never fatal
        return {"status": "unavailable", "detail": str(exc)[:200]}


def _caller_destinations(repo_root: Path, method_name: str, declaring_type: str,
                         declaring_path: str):
    """Each call site of the method with the inits its result reaches; a reason string otherwise."""
    name, resolution = source_level_name(method_name, declaring_type, declaring_path)
    if not name or resolution == "constructor" or not repo_root.is_dir():
        return f"the call sites of {method_name or 'the method'} are not searchable ({resolution})"
    pattern = re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
    sites, files = [], 0
    sources = _source_files(repo_root, _SOURCE_FILE_LIMIT + 1)
    if len(sources) > _SOURCE_FILE_LIMIT:
        # A call site past the listing's limit could pass the spec to an
        # encryption; the fact never rests on a partial listing.
        return f"more than {_SOURCE_FILE_LIMIT} source files; the call sites of {name} are not all read"
    for path in sources:
        relative = path.relative_to(repo_root).as_posix()
        # Test source sets only: a production package named attestation/ or
        # latest/ holds call sites that decide the fact, and one skipped
        # there could hide a constant IV given to an encryption.
        if is_test_source_path(relative):
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not pattern.search(source):
            continue
        files += 1
        if files > MAX_CALLER_FILES:
            return f"more than {MAX_CALLER_FILES} files call {name}"
        found = _call_sites_in(source, relative, name, pattern)
        if isinstance(found, str):
            return found
        sites.extend(found)
    return sites or f"no call site of {name} in the checkout"


def _call_sites_in(source: str, relative: str, name: str, pattern: "re.Pattern"):
    """The call sites of `name` in one file and the inits each result reaches."""
    code = _blank_comments(source, literals=True)
    plain = _blank_comments(source)
    inits = cipher_inits(source)
    sites = []
    for match in pattern.finditer(code):
        line_start = code.rfind("\n", 0, match.start()) + 1
        line_end = code.find("\n", match.end())
        line = code[line_start:line_end if line_end >= 0 else len(code)]
        if _declares(line, name, False) or re.search(rf"\b(?:fun|void)\s+{re.escape(name)}\b", line):
            continue
        close = _matching_close(code, match.end() - 1, "(", ")")
        if close < 0:
            continue
        where, reached = _destination(code, inits, match.start(), close + 1)
        number = code.count("\n", 0, match.start()) + 1
        if where != "inits":
            return (f"{relative}:{number}: what {name}(...) returns is "
                    + ("returned again" if where == "returned" else "not followed"))
        sites.append({
            "source_file_path": relative, "line": number, "source": source,
            "file_code": plain, "inits": reached,
            "arguments": _arguments(code, plain, match.end() - 1, close),
        })
    return sites


