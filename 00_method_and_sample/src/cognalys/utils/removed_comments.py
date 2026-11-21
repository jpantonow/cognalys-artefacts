"""Comments and commented-out code a patch deletes that its change does not make false.

BlueGate run_v8 (2026-10-31): the KeyPairSpec patch for getOrCreateKeyPair
rewrote generateNewKeyPair for its new return type and, on the way, deleted
the maintainer's commented-out alternative (an `if (Build.VERSION.SDK_INT >=
Build.VERSION_CODES.S)` branch building an `ECGenParameterSpec("ed25519")`)
-- nothing in the fix touched what that alternative names. It also deleted
"The following line is for validation and might not be strictly
necessary...", which described the KeyPair line the fix removed: that one may
go. An earlier BlueGate draft (run_research_medium) had deleted commented-out
code too. A maintainer reads every deleted line as part of the change.

The judgement is about the patch's text, never about the finding. A comment
line kept anywhere in the file's corrected code, or reworded in place, is not
deleted. A deleted comment block is kept out of the verdict when

- it names an identifier or value the patch's code now uses less (a value
  such as `SHA-1` or `TLSv1.2` read whole, not as `SHA` and `1`);
- it sits in a block of code the patch removed or rewrote entirely;
- it is commented-out code (most of its lines read as statements) the patch
  turned into live code;
- it is prose whose next statement the patch removed, changed or moved (for
  a doc comment, the declaration's signature), or whose words name what the
  patch changed in the code right after it (up to the next blank or comment
  line) or what it removed from or brought into the file (`GCM` once the
  cipher uses it, "all-trusting" once trustAllCerts is gone).

Everything else -- an alternative, a TODO the change does not resolve, an
explanation of code the patch leaves as it was -- is reported, so the draft
is regenerated with the reason; the regeneration may still update a comment
the change makes false. Measured on 2026-10-31 over the 966 distinct
coordinated and PR drafts under outputs/: 172 delete comment lines, and 9 are
reported -- BlueGate's commented-out ed25519 alternative (6 drafts of 4 runs,
among them run_research_medium) and the demo CryptoApp's "Generate a new
secret key" and "Encrypt" above calls the patch kept (3 drafts); none is a
comment on code the patch removed or changed. C-family comments in the app's own sources
only (Java, Kotlin, Go, Dart, Swift, JavaScript; test sources go with the
test the patch rewrites); a file in another syntax is never reported.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Optional, Set, Tuple

from cognalys.utils.identifier_words import COMMENT_OR_LITERAL, identifier_words

_C_FAMILY = frozenset({".java", ".kt", ".kts", ".go", ".dart", ".swift", ".js", ".ts",
                       ".jsx", ".tsx", ".scala", ".groovy", ".gradle", ".c", ".cc",
                       ".cpp", ".h", ".hpp", ".m", ".mm", ".cs", ".rs"})
_TOKEN = re.compile(r"[A-Za-z_$][\w$]*|\d[\w.]*")
# A value written with separators, read whole: SHA-1, TLSv1.2,
# AES/ECB/PKCS5Padding. Review of 2026-10-31: read as tokens, the `1` of
# SHA-1 and the `2` of TLSv1.2 were too short to count, so "SHA-1
# fingerprints match the admin panel" was reported after SHA-1 -> SHA-256.
_VALUE = re.compile(r"[A-Za-z0-9_$]+(?:[-/.][A-Za-z0-9_$]+)+")
# Words that say nothing about which code a comment describes.
_NOT_EVIDENCE = frozenset({
    "if", "else", "for", "while", "do", "return", "val", "var", "fun", "new", "this",
    "null", "true", "false", "try", "catch", "finally", "throw", "throws", "class",
    "object", "public", "private", "protected", "internal", "static", "final", "void",
    "int", "long", "boolean", "byte", "char", "short", "float", "double", "import",
    "package", "when", "is", "in", "as", "or", "and", "not", "it", "to", "of", "the",
    "a", "an", "be", "by", "on", "at", "with", "from", "that", "this", "we", "i",
    "override", "super", "let", "const", "func", "self", "nil", "String",
    "TODO", "FIXME", "XXX", "NOTE", "HACK",
})
# Endings and starts no sentence has.
_CODE_SHAPE = re.compile(r"[;{}(\[]\s*$|^[})\]]|^\??\.[A-Za-z_$]|->\s*$|=\s*$")
_CODE_START = re.compile(
    r"^(?:if|else|for|while|return|val|var|fun|private|public|protected|internal|import|"
    r"try|catch|final|static|new|override|throw|when|let|const|func)\b")
_CALL_OR_ASSIGNMENT = re.compile(r"^[\w$.<>\[\]?]+(?:\(|\s*=[^=])")
# What only code has: a call with no space before its parenthesis, a member
# access, a statement end, an assignment. Review of 2026-10-31: "Build the
# context (see issue #42)" and "Older devices, sadly," ended in `)` and `,`
# and were read as commented-out code, so the code they describe was never
# looked at.
_CODE_MARK = re.compile(r"[\w$\])>]\(|[\w$)\]]\.[A-Za-z_$][\w$]+|[;{}]|(?<![=!<>])=(?!=)|->|::")
_PROSE_LEAD = re.compile(r"^(?:TODO|FIXME|XXX|NOTE|HACK)\b", re.IGNORECASE)


def _comment_text(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^(?://+|/\*+|\*+(?!/))", "", text)
    text = re.sub(r"\*+/\s*$", "", text)
    return " ".join(text.split())


def _classify(code: str) -> List[Tuple[str, str]]:
    """(kind, text) per line: kind is comment, code or blank.

    A line is a comment line when it holds nothing but comment: a // line, or
    a line of a /* */ block with no code outside it.
    """
    lines, in_block = [], False
    for line in str(code or "").split("\n"):
        stripped = line.strip()
        if in_block:
            end = stripped.find("*/")
            if end < 0:
                lines.append(("comment", _comment_text(line)))
                continue
            in_block = False
            rest = stripped[end + 2:].strip()
            lines.append(("code", line) if rest else ("comment", _comment_text(line)))
            continue
        if not stripped:
            lines.append(("blank", ""))
        elif stripped.startswith("//"):
            lines.append(("comment", _comment_text(line)))
        elif stripped.startswith("/*"):
            end = stripped.find("*/", 2)
            if end < 0:
                in_block = True
                lines.append(("comment", _comment_text(line)))
            elif stripped[end + 2:].strip():
                lines.append(("code", line))
            else:
                lines.append(("comment", _comment_text(line)))
        else:
            lines.append(("code", line))
    return lines


def _live(code: str) -> str:
    """The code with its comments blanked, string contents kept."""
    return COMMENT_OR_LITERAL.sub(
        lambda match: " " if match.group(0).startswith(("//", "/*")) else match.group(0),
        str(code or ""))


def _code_tokens(code: str) -> Counter:
    """Identifier and literal tokens of the code, comments left out, string contents kept."""
    return Counter(_TOKEN.findall(_live(code)))


def _code_values(code: str) -> Counter:
    """Values written with separators (SHA-1, TLSv1.2), comments left out."""
    return Counter(_VALUE.findall(_live(code)))


def _squeezed(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _reads_as_code(text: str) -> bool:
    text = text.strip()
    if not text or _PROSE_LEAD.match(text):
        return False
    if _CODE_SHAPE.search(text):
        return True
    marked = bool(_CODE_MARK.search(COMMENT_OR_LITERAL.sub('""', text)))
    if re.search(r"[),]\s*$", text) and (marked or " " not in text):
        return True
    if _CODE_START.match(text) and marked:
        return True
    return bool(_CALL_OR_ASSIGNMENT.match(text) and not text.endswith("."))


def _blocks(lines: List[Tuple[str, str]], removed: set) -> List[List[int]]:
    """Runs of consecutive removed comment lines (blank lines do not split a run)."""
    blocks, current = [], []
    for index, (kind, _text) in enumerate(lines):
        if index in removed:
            current.append(index)
        elif kind == "blank" and current:
            continue
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _depth_change(text: str) -> int:
    blanked = COMMENT_OR_LITERAL.sub(" ", text)
    return blanked.count("{") - blanked.count("}")


def _braces_only(text: str) -> bool:
    return bool(re.fullmatch(r"\s*[})\]]+[;,)]*\s*", text))


def _attached_code(lines: List[Tuple[str, str]], block: List[int]) -> List[int]:
    """The original lines a prose comment sits on.

    The code lines after it up to the next blank or comment line, through the
    end of a block the first of them opens (a doc comment describes the whole
    declaration, "// Initialize empty keystore" the try block below it); when
    nothing but closing braces follows, the code lines before it.
    """
    after: List[int] = []
    depth = 0
    for index in range(block[-1] + 1, len(lines)):
        kind, text = lines[index]
        if kind != "code" and depth <= 0:
            break
        if kind == "code":
            after.append(index)
            depth += _depth_change(text)
            if depth <= 0 and (len(after) >= 12 or index != after[0] and depth < 0):
                break
        if len(after) >= 80:
            break
    if after and not all(_braces_only(lines[index][1]) for index in after):
        return after
    before: List[int] = []
    for index in range(block[0] - 1, -1, -1):
        if lines[index][0] != "code":
            break
        before.append(index)
        if len(before) >= 12:
            break
    return before + after


def _next_statement(lines: List[Tuple[str, str]], attached: List[int],
                    signature_only: bool) -> List[int]:
    """The lines of the first statement of ``attached``, through the block it opens.

    For a doc comment (``signature_only``), the declaration's signature: up to
    the line that opens its body. Review of 2026-10-31: the whole blank-free
    run of code after a comment, or a doc comment's whole declaration, was
    taken as what it describes, so "Callers hold the lock while this runs"
    or a method's unrelated KDoc could go whenever one line under it changed.
    """
    statement: List[int] = []
    depth = 0
    for position, index in enumerate(attached):
        text = lines[index][1]
        statement.append(index)
        change = _depth_change(text)
        if signature_only and (change > 0 or re.search(r"=\s*\S|=\s*$", text)):
            break
        depth += change
        if len(statement) >= 40:
            break
        following = lines[attached[position + 1]][1].strip() if position + 1 < len(attached) else ""
        continues = (depth > 0 or re.search(r"[(\[,=+\-*/.&|?:]\s*$|->\s*$", text.strip())
                     or re.match(r"^(?:\??\.|[)\]]|&&|\|\||\?:|:)", following))
        if not continues:
            break
    return statement


def _enclosing_block_removed(lines: List[Tuple[str, str]], block: List[int],
                             unchanged: set) -> bool:
    """Whether the comment sits inside a block of code every line of which the patch changed.

    Review of 2026-10-31: a commented-out `cipher.init(...)` inside an
    `if (legacy) { ... }` branch the patch deleted whole was reported as an
    unrelated deletion. A block whose opener and statements are all removed or
    rewritten takes its comments with it; one with a line kept in place (the
    method around BlueGate's ed25519 alternative) does not.
    """
    depth, opener = 0, None
    for index in range(block[0] - 1, -1, -1):
        kind, text = lines[index]
        if kind != "code":
            continue
        depth -= _depth_change(text)
        if depth < 0:
            opener = index
            break
    if opener is None:
        return False
    members, depth = [], 0
    for index in range(opener, len(lines)):
        kind, text = lines[index]
        if kind != "code":
            continue
        members.append(index)
        depth += _depth_change(text)
        if index != opener and depth <= 0:
            break
    body = [index for index in members if not _braces_only(lines[index][1])]
    return bool(body) and all(index not in unchanged for index in body)


def _unchanged_code_lines(lines: List[Tuple[str, str]],
                          corrected_lines: List[Tuple[str, str]]) -> set:
    """Indices of the original code lines the edit keeps in place.

    Aligned line by line, indentation included, so a line the patch moved
    into another block (Grocy's keyStore.load(null, null) moved under the
    first-run branch), rewrote or deleted is changed even when an identical
    line survives elsewhere.
    """
    positions = [index for index, (kind, _text) in enumerate(lines) if kind == "code"]
    matcher = difflib.SequenceMatcher(
        None, [lines[index][1].rstrip() for index in positions],
        [text.rstrip() for kind, text in corrected_lines if kind == "code"], autojunk=False)
    kept = set()
    for tag, first, last, _start, _end in matcher.get_opcodes():
        if tag == "equal":
            kept.update(positions[first:last])
    return kept


def _updated_comment_lines(lines: List[Tuple[str, str]],
                           corrected_lines: List[Tuple[str, str]]) -> set:
    """Indices of original comment lines the edit replaced by other comment lines.

    A comment reworded in place goes with the change it describes (TOTP's
    "@param crypto the crypto algorithm (HmacSHA1, ...)" without HmacSHA1,
    run_20261009_012517): an update, not a deletion.
    """
    def key(line: Tuple[str, str]) -> Tuple[str, str]:
        return (line[0], _squeezed(line[1]) if line[0] == "comment" else line[1].rstrip())

    matcher = difflib.SequenceMatcher(None, [key(line) for line in lines],
                                      [key(line) for line in corrected_lines], autojunk=False)
    updated = set()
    for tag, first, last, start, end in matcher.get_opcodes():
        if tag == "replace" and any(kind == "comment" for kind, _ in corrected_lines[start:end]):
            updated.update(index for index in range(first, last) if lines[index][0] == "comment")
    return updated


def _stems(words: Iterable[str]) -> set:
    return {word[:5] for word in words if len(word) >= 4 and word.isalpha()}


def _code_like_name(token: str) -> bool:
    """A word of a comment that can only be a name or a value: GCM, MD5, keyStore, SSL_v3."""
    return (len(token) >= 2 and token not in _NOT_EVIDENCE
            and bool(re.fullmatch(r"[A-Z][A-Z0-9]+", token) or re.search(r"\d", token)
                     or re.search(r"[a-z][A-Z]|\w_\w", token)))


def _names(texts: List[str], names: Set[str], values: Set[str],
           kept: Optional[Set[str]] = None) -> bool:
    """Whether prose names, in words, one of ``names`` or ``values``.

    A value read whole (SHA-1), a name written as code (GCM, keyStore), a
    word that spells a whole name ("keystore" for keyStore), or words of
    four or more letters compared by their first five ("trusting" names
    trustAllCerts, "hmac" names PBKDF2WithHmacSHA1). With ``kept``, a word
    that spells a whole name the code still uses is about that name and no
    evidence (review of 2026-10-31: "The password comes from the user's
    settings" is still about `password` when PasswordProtection appears).
    """
    text = " ".join(texts)
    if values and set(_VALUE.findall(text)) & values:
        return True
    said = set(re.findall(r"[A-Za-z_$][\w$]*", text))
    if kept:
        spelled = {name.lower() for name in kept}
        said = {word for word in said if word.lower() not in spelled}
    if {token for token in said if _code_like_name(token)} & names:
        return True
    lowered = {name.lower() for name in names if len(name) >= 4}
    if {word.lower() for word in said if len(word) >= 4} & lowered:
        return True
    # Values are compared whole above: the words of KeyStore.getDefaultType
    # are not evidence that "key store" prose is about the change.
    removed = _stems(word for name in names if not _VALUE.fullmatch(name)
                     for word in identifier_words(name))
    spoken = _stems(word.lower() for token in said for word in identifier_words(token))
    return bool(removed & spoken - _COMMON_STEMS)


# Words too common in comments and names alike to tie the two together.
_COMMON_STEMS = frozenset({"this", "that", "with", "from", "have", "will", "when", "then",
                           "else", "return", "value", "values", "data", "make", "used", "using",
                           "only", "here", "there", "some", "each", "into", "over", "also"})


_TEST_PATH = re.compile(r"(?:^|/)src/(?:test|androidTest|sharedTest)\w*/|(?:^|/)tests?/"
                        r"|Tests?\.(?:java|kt)$")


def unrelated_removed_comments(edits: Iterable[Dict]) -> List[Dict]:
    """The comment blocks each file's edits delete that the change does not make false.

    One entry per file: {"source_file_path", "symbols", "lines"}; [] when
    every deleted comment described code the patch removed or changed.
    """
    edits = [edit for edit in edits if isinstance(edit, dict)]
    by_path: Dict[str, List[Dict]] = {}
    for edit in edits:
        path = str(edit.get("source_file_path") or edit.get("source_file") or "")
        # A test's comments go with the test the patch rewrites; only the
        # app's own code is judged.
        if PurePosixPath(path).suffix.lower() in _C_FAMILY and not _TEST_PATH.search(path):
            by_path.setdefault(path, []).append(edit)
    findings = []
    for path, file_edits in by_path.items():
        corrected_all = "\n".join(str(edit.get("corrected_code") or "") for edit in file_edits)
        corrected_lines = _classify(corrected_all)
        # A comment moved, reindented or kept in another edit of the file is
        # not deleted.
        kept_comments = Counter(text for kind, text in corrected_lines
                                if kind == "comment" and text)
        # The comment text the corrected code still carries anywhere, trailing
        # comments included.
        corrected_comments = "\n".join(
            _squeezed(_comment_text(match.group(0))) for match in COMMENT_OR_LITERAL.finditer(
                corrected_all) if match.group(0).startswith(("//", "/*")))
        live_corrected = _squeezed(_live(corrected_all))
        corrected_code_lines = {_squeezed(text) for kind, text in corrected_lines
                                if kind == "code"}
        file_after = _code_tokens(corrected_all) + _code_values(corrected_all)
        file_before = Counter()
        for edit in file_edits:
            original_text = str(edit.get("original_code") or "")
            file_before.update(_code_tokens(original_text) + _code_values(original_text))
        # What the patch removed from the file or brought into it: prose
        # naming one of those describes the change. A name the file uses
        # both before and after is no such evidence by itself (review of
        # 2026-10-31: KeyStore used once more did not make "Opens the app's
        # key store" about the change).
        appeared_or_gone = {token for token in set(file_before) | set(file_after)
                            if (file_before[token] > 0) != (file_after[token] > 0)}
        kept_names = {token for token in set(file_before) & set(file_after)
                      if file_before[token] > 0 and file_after[token] > 0}
        reported_lines, symbols = [], []
        for edit in file_edits:
            original = str(edit.get("original_code") or "")
            corrected = str(edit.get("corrected_code") or "")
            # A deleted declaration takes its comments with it; a new file has none.
            if not original.strip() or not _code_tokens(corrected):
                continue
            lines = _classify(original)
            raw = original.split("\n")
            corrected_edit_lines = _classify(corrected)
            unchanged = _unchanged_code_lines(lines, corrected_edit_lines)
            updated = _updated_comment_lines(lines, corrected_edit_lines)
            before = _code_tokens(original) + _code_values(original)
            after = _code_tokens(corrected) + _code_values(corrected)
            used_less = {token for token, count in before.items() if after[token] < count}
            new_in_edit = {token for token in after if before[token] == 0}
            live_original = _squeezed(_live(original))
            available = Counter(kept_comments)
            removed = set()
            for index, (kind, text) in enumerate(lines):
                if kind != "comment" or not text:
                    continue
                if available[text] > 0:
                    available[text] -= 1
                elif _squeezed(text) in corrected_comments or index in updated:
                    # Kept as a trailing comment or inside a longer one, or
                    # reworded in place.
                    continue
                else:
                    removed.add(index)

            def changed_names(indices: List[int]) -> Tuple[Set[str], Set[str]]:
                """What the changed lines among ``indices`` changed: every name of a
                line moved elsewhere, else the names it uses less and those the
                edit introduces."""
                names: Set[str] = set()
                values: Set[str] = set()
                for index in indices:
                    if index in unchanged:
                        continue
                    text = lines[index][1]
                    tokens = set(_TOKEN.findall(_live(text)))
                    found = set(_VALUE.findall(_live(text)))
                    if _squeezed(text) in corrected_code_lines:
                        names |= tokens
                        values |= found
                    else:
                        names |= (tokens & used_less) | {token for token in new_in_edit
                                                         if not _VALUE.fullmatch(token)}
                        values |= (found & used_less) | {token for token in new_in_edit
                                                         if _VALUE.fullmatch(token)}
                return names - _NOT_EVIDENCE, values

            for block in _blocks(lines, removed):
                texts = [lines[index][1] for index in block if lines[index][1]]
                tokens = {token for text in texts for token in _TOKEN.findall(text)
                          if token not in _NOT_EVIDENCE and len(token) > 1}
                tokens |= {value for text in texts for value in _VALUE.findall(text)}
                if not tokens:
                    continue  # a lone brace or rule line says nothing either way
                if tokens & used_less:
                    continue  # names code the patch removed or changed
                if _enclosing_block_removed(lines, block, unchanged):
                    continue  # its block went, or was rewritten, whole
                code_like = sum(_reads_as_code(text) for text in texts) * 2 >= len(texts)
                if code_like:
                    # Commented-out code the patch turned into live code.
                    if any(_squeezed(text) in live_corrected and _squeezed(text) not in live_original
                           for text in texts if len(_squeezed(text)) > 3):
                        continue
                else:
                    attached = _attached_code(lines, block)
                    doc = raw[block[0]].strip().startswith("/**") if block[0] < len(raw) else False
                    following = [index for index in attached if index > block[-1]]
                    statement = (_next_statement(lines, following, doc) if following
                                 else attached)
                    if any(index not in unchanged for index in statement):
                        continue  # described the statement the patch removed or changed
                    names, values = changed_names(attached)
                    if (names or values) and _names(texts, names, values):
                        # About a changed line further down the same run of
                        # code: Grocy run_v6's "Initialize empty keystore"
                        # once keyStore.load(null, null) moved under the
                        # first-run branch.
                        continue
                    if _names(texts, appeared_or_gone - _NOT_EVIDENCE,
                              {token for token in appeared_or_gone if _VALUE.fullmatch(token)},
                              kept=kept_names):
                        # "Create an ssl socket factory with our all-trusting
                        # manager" once trustAllCerts is gone (zSMTH run7),
                        # "HmacSHA1 with PBKDF2" once another edit of the file
                        # moves the hash to PBKDF2WithHmacSHA256 (Bitbanana
                        # subset_policy), "TODO: switch to GCM" once the
                        # cipher uses GCM.
                        continue
                reported_lines.extend(texts)
                symbols.append(str(edit.get("symbol", "")).strip())
        if reported_lines:
            findings.append({"source_file_path": path,
                             "symbols": list(dict.fromkeys(item for item in symbols if item)),
                             "lines": reported_lines})
    return findings


UNRELATED_COMMENT_REMOVED = "UNRELATED_COMMENT_REMOVED"


def unrelated_comment_issues(edits: Iterable[Dict]) -> List[Dict]:
    """unrelated_removed_comments as semantic-validation errors that regenerate the file."""
    issues = []
    for item in unrelated_removed_comments(edits):
        shown = "; ".join(f"`{line}`" for line in item["lines"][:6])
        more = len(item["lines"]) - 6
        issues.append({
            "code": UNRELATED_COMMENT_REMOVED,
            "path": item["source_file_path"],
            "affected_paths": [item["source_file_path"]],
            "details": [
                "the patch deletes comments or commented-out code that the change "
                "does not seem to make false"
                + (f" (in {', '.join(item['symbols'])})" if item["symbols"] else "")
                + f": {shown}" + (f" and {more} more line(s)" if more > 0 else "")
                # Review of 2026-10-31: "copy them exactly" would put back a
                # comment the change did make false, which the check cannot
                # always tell.
                + ". Keep them in corrected_code where they were, verbatim; only a "
                "comment that describes code this patch removes or changes may be "
                "removed, or updated to describe the new code."
            ],
        })
    return issues
