"""Text rules shared by every place that splices a generated edit into a file."""
from __future__ import annotations

_LINE_BREAKS = ("\r\n", "\n", "\r")


def _trailing_line_break(text: str) -> str:
    for line_break in _LINE_BREAKS:
        if text.endswith(line_break):
            return line_break
    return ""


def match_trailing_line_break(original: str, corrected: str) -> str:
    """Give `corrected` the same closing line break `original` had.

    A model copies ``original_code`` out of the file, line break included, and
    then often writes ``corrected_code`` without that final newline. Spliced
    verbatim, the replacement swallows the break and the next line of the file
    is glued onto its last line (``}                    }``); the mirror case
    splits a line in two. Neither changes what the code does, so nothing else
    notices, but a maintainer reads it as a careless patch.

    Only the closing line break is adjusted; an empty replacement (a deletion)
    is returned untouched.
    """
    if not corrected:
        return corrected
    wanted = _trailing_line_break(original)
    present = _trailing_line_break(corrected)
    if wanted == present:
        return corrected
    if not present:
        return corrected + wanted
    if not wanted:
        return corrected[: -len(present)]
    return corrected[: -len(present)] + wanted


def keep_untouched_formatting(original: str, corrected: str) -> str:
    """Leave the lines a generated edit did not change exactly as they were.

    A model rewrites a whole block to change two lines, and in passing strips
    trailing spaces from a line it did not mean to touch, or replaces the code
    it removes with blank lines. AmaRadio's MD5 fix (2026-10-16) removed the
    md5() helper and left an empty line plus a whitespace-only change where it
    had been; WiFiAnalyzer's dropped imports left blank lines in the import
    list. A maintainer reads both as a careless patch.

    Lines equal to the original apart from trailing whitespace keep the
    original text. Where original code is replaced by nothing but blank lines
    -- the code was removed -- the blank lines go too. Everything else the
    model wrote is kept.
    """
    if not corrected or not original:
        return corrected
    import difflib

    old = original.splitlines(keepends=True)
    new = corrected.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(
        None, [line.rstrip() for line in old], [line.rstrip() for line in new],
        autojunk=False)
    result: list[str] = []
    removals: list[int] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            for old_line, new_line in zip(old[old_start:old_end], new[new_start:new_end]):
                # A partial-line anchor may end before the newline. If the
                # replacement inserts another line after it, retaining that
                # missing terminator joins two declarations together.
                if not _trailing_line_break(old_line) and _trailing_line_break(new_line):
                    old_line += _trailing_line_break(new_line)
                result.append(old_line)
            continue
        written = new[new_start:new_end]
        removed_code = any(line.strip() for line in old[old_start:old_end])
        if removed_code and not any(line.strip() for line in written):
            removals.append(len(result))
            continue
        result.extend(written)
    # The blank lines that separated removed code from its neighbours meet:
    # one separator stays.
    for position in reversed(removals):
        if (0 < position < len(result) and not result[position - 1].strip()
                and not result[position].strip()):
            del result[position]
    return "".join(result)


def remove_block(content: str, original: str) -> str:
    """`content` without its first `original`, line and all.

    A block that fills whole lines is removed with its line break: an import
    line copied without its newline left a blank line in its place, so
    primftpd's SshBuilder import block got a blank line where two imports had
    been, and BaseRC4Cipher three blank lines after its package line
    (2026-11-03). The blank lines the removal brings together are collapsed
    to the longer of the two runs that were there before: a declaration
    between two blank lines leaves one (AmaRadio's md5() helper, 2026-10-16),
    and blank lines the removal did not bring together are not touched. A
    block that shares its line with other text is cut as written.
    """
    index = content.find(original)
    if index < 0:
        return content
    before, after = content[:index], content[index + len(original):]
    # The indentation in front of a block copied without it belongs to the
    # line being removed.
    line_start = before.rstrip(" \t")
    if line_start and not line_start.endswith("\n"):
        return before + after
    if not original.endswith("\n"):
        rest, separator, remainder = after.partition("\n")
        if rest.strip():
            return before + after
        after = remainder if separator else ""
    before_lines = line_start.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    blank_before = 0
    while (blank_before < len(before_lines)
           and not before_lines[-1 - blank_before].strip()):
        blank_before += 1
    blank_after = 0
    while (blank_after < len(after_lines)
           and not after_lines[blank_after].strip()):
        blank_after += 1
    # Only runs on both sides of the removed block are joined. A block that
    # opened the file takes its blank separator with it: the file does not
    # start with a blank line.
    if blank_before and blank_after:
        after_lines = after_lines[min(blank_before, blank_after):]
    elif not before_lines and blank_after:
        after_lines = after_lines[1:]
    return "".join(before_lines) + "".join(after_lines)


# Reading and writing the files a generated edit lands in.
#
# A model and the edit appliers work on LF lines: original_code is copied
# from text read with universal newlines. Read with read_text and written
# back, a CRLF file came out all LF, a diff touching every line of the file
# (the batch4 format review of pr_followup, 2026-10-30). read_source gives
# the LF view an edit is matched against; write_source writes the result
# with the line endings the file has on disk, so only the edited lines
# change. A file mixing CRLF and LF keeps the ending of every line the edit
# left alone: written all LF, its CRLF lines were in the diff too (six of 82
# local clones hold CRLF or mixed sources, 2026-10-30 review).


def read_exact(path) -> str:
    """A file's text with its line endings (and any undecodable bytes) as on disk."""
    from pathlib import Path

    return Path(path).read_bytes().decode("utf-8", errors="surrogateescape")


def write_exact(path, text: str) -> None:
    """Write text byte for byte: no newline translation (read_exact's inverse)."""
    from pathlib import Path

    Path(path).write_bytes(text.encode("utf-8", errors="surrogateescape"))


def file_line_ending(text: str) -> str:
    """"\r\n" when every line break of the text is CRLF, else "\n"."""
    crlf = text.count("\r\n")
    return "\r\n" if crlf and crlf == text.count("\n") else "\n"


def with_line_ending(text: str, ending: str) -> str:
    """The text with every line break written as ``ending``."""
    lf = text.replace("\r\n", "\n")
    return lf.replace("\n", ending) if ending != "\n" else lf


def read_source(path, errors: str = "replace") -> str:
    """The LF view of a source file that edits are matched and spliced into."""
    from pathlib import Path

    return Path(path).read_bytes().decode("utf-8", errors=errors).replace("\r\n", "\n")


def _lines(text: str) -> list[str]:
    """The text's lines, each with its own line break; only a newline ends a line."""
    lines = text.split("\n")
    return [line + "\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])


def with_endings_of(original: str, text: str) -> str:
    """``text`` (an LF view) written with the line endings ``original`` has.

    An all-LF original takes the text as given and an all-CRLF one makes it
    all CRLF. In a file mixing both, a line the edit left alone keeps its
    own ending, and a line the edit wrote takes the ending of the line it
    replaced, or of the line before it when inserted.
    """
    import difflib

    ending = file_line_ending(original)
    if ending == "\r\n":
        return with_line_ending(text, ending)
    if "\r\n" not in original:
        return text
    old = _lines(original)
    old_lf = [line[:-2] + "\n" if line.endswith("\r\n") else line for line in old]
    new = _lines(text.replace("\r\n", "\n"))

    def ending_of(index: int) -> str:
        return "\r\n" if 0 <= index < len(old) and old[index].endswith("\r\n") else "\n"

    written = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, old_lf, new, autojunk=False).get_opcodes():
        if tag == "equal":
            written.extend(old[i1:i2])
            continue
        own = ending_of(i1 if tag == "replace" else max(i1 - 1, 0))
        written.extend(line[:-1] + own if line.endswith("\n") else line
                       for line in new[j1:j2])
    return "".join(written)


def write_source(path, text: str) -> None:
    """Write an edited source with the line endings its file has on disk.

    A file that does not exist yet (created by the patch) is written LF.
    """
    from pathlib import Path

    path = Path(path)
    write_exact(path, with_endings_of(read_exact(path), text) if path.is_file() else text)
