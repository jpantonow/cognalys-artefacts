"""The words of an identifier, and the words of the types a piece of code uses.

Shared by the semantic gates (semantic_validation: a name that still says the
replaced value) and the pull request text (pr_text: the paragraph that lists
such names), so both judge "this name says SSL" the same way. Fread's body
(tls_20261015) listed `SuppressLint` and `SSLSocket` as names that "still say
SSL"; the gate had already learned that "suppreSSLint" is not the word SSL
and that buildSSLFactory names the SSLSocketFactory it returns.
"""
from __future__ import annotations

import re

TYPE_IDENTIFIER = re.compile(r"(?<![\w$@])[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*\b")
IDENTIFIER_WORD = re.compile(r"[A-Z]+[0-9]*(?![a-z])|[A-Z]?[a-z]+[0-9]*|[0-9]+")

COMMENT_OR_LITERAL = re.compile(
    r'"""[\s\S]*?"""'
    r'|//[^\n]*'
    r'|/\*[\s\S]*?\*/'
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'",
)


def blank_comments_and_literals(code: str) -> str:
    """`code` with comments and string literals blanked, offsets preserved.

    Brace counting is only trustworthy once a brace inside a comment or a
    string cannot be mistaken for a block delimiter. Blanking in place
    rather than deleting keeps every offset usable against the original.
    """
    characters = list(code)
    for match in COMMENT_OR_LITERAL.finditer(code):
        for index in range(match.start(), match.end()):
            if characters[index] != "\n":
                characters[index] = " "
    return "".join(characters)


def identifier_words(name: str) -> list[str]:
    """`buildSSLFactory` -> ["build", "ssl", "factory"]; `SHA_256` -> ["sha", "256"]."""
    return [word.lower() for word in IDENTIFIER_WORD.findall(str(name or ""))]


def names_token_as_word(name: str, token: str) -> bool:
    """Whether `token` is a whole word of `name`, or several consecutive ones.

    "md5" is a word of `model16MD5` and "sha256" of `SHA_256Digest`, but
    "ssl" is not a word of `suppressLint` nor "des" one of `description`.
    """
    words = identifier_words(name)
    token = str(token or "").lower()
    for start in range(len(words)):
        joined = ""
        for word in words[start:]:
            joined += word
            if joined == token:
                return True
            if len(joined) >= len(token):
                break
    return False


_DECLARED_NAME_PATTERNS = (
    # Kotlin functions, receivers and generics included (`fun File.md5Hash(`).
    re.compile(r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w.?<>]+\.)?(\w+)\s*\("),
    # Kotlin properties and constants.
    re.compile(r"\b(?:val|var)\s+(\w+)"),
    # Java fields and locals with a type: `static final String model16MD5 =`,
    # `private byte[] key;`, `String digest =`.
    re.compile(r"\b(?:(?:private|public|protected|static|final|transient|volatile)\s+)*"
               r"(?:String|byte|int|long|char|boolean|short|float|double|[A-Z][\w.]*)"
               r"(?:<[^<>;=(){}]*>)?(?:\s*\[\s*\])*\s+(\w+)\s*[=;]"),
    # Java methods: a name, a parameter list and a body.
    re.compile(r"\b[\w<>\[\],.?]+\s+(\w+)\s*\([^;{}()]*\)\s*(?:throws\s[^{;]*)?\{"),
    re.compile(r"\b(?:class|interface|object|enum|record|typealias)\s+(\w+)"),
)
_NOT_NAMES = frozenset({
    "return", "new", "throw", "else", "case", "yield", "if", "for", "while",
    "switch", "catch", "synchronized", "this", "super", "null", "true", "false",
})


def declared_names(code: str) -> set:
    """Functions, fields, locals and types `code` itself declares."""
    code = blank_comments_and_literals(code or "")
    names = set()
    for pattern in _DECLARED_NAME_PATTERNS:
        names |= {name for name in pattern.findall(code) if name not in _NOT_NAMES}
    return names


def type_words(code: str) -> set:
    """Whole camel-case words of the types `code` uses but does not declare.

    The "SSL" of Fread's buildSSLFactory names the SSLSocketFactory it
    returns, not the protocol, so "SSL" -> "TLS" leaves the name true
    (tls_20261015). Comments, literals and annotations are blanked first, and
    a word must equal the token: "des" is not a word of FileDescriptor, and
    an annotation (@SuppressLint, @RequiresSSL) exempts nothing. A type the code declares is its own name
    and exempts nothing either.
    """
    code = re.sub(r"@[\w.]+(?:\([^)]*\))?", " ", blank_comments_and_literals(code or ""))
    declared = set(re.findall(
        r"\b(?:class|interface|object|enum|record|typealias)\s+([A-Z]\w*)", code))
    words = set()
    for identifier in TYPE_IDENTIFIER.findall(code):
        if identifier not in declared:
            words |= set(identifier_words(identifier))
    return words
