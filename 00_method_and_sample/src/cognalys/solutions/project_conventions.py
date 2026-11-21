"""Conventions a repository keeps that a generated change has to follow.

A model shown one method writes what that method suggests. On 2026-10-15 a
revision of Auto-Shine's Save listener answered the maintainer's "it should
show some message toast" with Toast.makeText(this, "Wrong numeric value",
...), while every other Toast in the app reads its text from strings.xml
through getResources().getString(R.string....). The convention was in the
repository, not in the method, so nothing told the model.

Each function here reads one convention from the checkout and says so in
words a prompt can carry, with the repository's own lines as examples, or
says it could not tell. Nothing here edits anything.
"""
from __future__ import annotations

import re
from pathlib import Path

_SKIPPED_PARTS = frozenset({".git", ".gradle", "build", "generated", "node_modules"})
_TEST_PARTS = re.compile(r"/(?:test|androidTest|sharedTest)\w*/")
_MAX_FILES = 3000
# A string resource used in code: R.string.x, getString(R.string.x),
# stringResource(R.string.x), context.getText(R.string.x).
_RESOURCE_TEXT = re.compile(r"\bR\.string\.\w+")
# A literal handed to an API that shows text to the user.
_LITERAL_TEXT = re.compile(
    r"(?:Toast\.makeText\s*\([^,]+,\s*|\.set(?:Text|Title|Message|Hint|ContentDescription)\s*\(\s*|"
    r"\bText\s*\(\s*(?:text\s*=\s*)?)\"[^\"\n]{2,}\"")


def _module_root(project_root: Path, source_file: Path) -> Path:
    """The Gradle module holding ``source_file``: the parent of its src/."""
    for parent in Path(source_file).resolve().parents:
        if parent.name == "src":
            return parent.parent
        if parent == Path(project_root).resolve():
            break
    return Path(project_root)


def _sources(root: Path) -> list[Path]:
    found = []
    for path in root.rglob("*"):
        if len(found) >= _MAX_FILES:
            break
        if path.suffix not in (".java", ".kt") or not path.is_file():
            continue
        relative = "/" + path.relative_to(root).as_posix()
        if _SKIPPED_PARTS.intersection(path.relative_to(root).parts) or _TEST_PARTS.search(relative):
            continue
        found.append(path)
    return found


def user_visible_text_convention(project_root: Path, source_file: Path) -> dict:
    """Whether the module of ``source_file`` keeps user-visible text in strings.xml.

    ``string_resources`` when the module has a res/values/strings.xml and its
    code refers to string resources at least three times and at least twice
    as often as it passes literals to text-showing APIs; ``literals`` when
    literals dominate; ``undetermined`` otherwise.
    """
    project_root = Path(project_root).resolve()
    source_file = Path(source_file).resolve()
    module = _module_root(project_root, source_file)
    strings = module / "src" / "main" / "res" / "values" / "strings.xml"
    resource_lines, literal_count = [], 0
    for path in _sources(module):
        text = path.read_text(encoding="utf-8", errors="replace")
        literal_count += len(_LITERAL_TEXT.findall(text))
        for line in text.splitlines():
            if _RESOURCE_TEXT.search(line):
                resource_lines.append((path, line.strip()))
    record = {
        "resource_references": len(resource_lines),
        "literal_texts": literal_count,
    }
    if strings.is_file() and len(resource_lines) >= 3 and len(resource_lines) >= 2 * literal_count:
        own = [line for path, line in resource_lines if path.resolve() == source_file]
        others = [line for path, line in resource_lines if path.resolve() != source_file]
        translations = sorted(
            str(path.relative_to(project_root))
            for path in (module / "src" / "main" / "res").glob("values-*/strings.xml"))
        return {**record, "convention": "string_resources",
                "strings_file": str(strings.relative_to(project_root)),
                "translations": translations,
                "examples": [line[:160] for line in (own + others)[:4]]}
    if literal_count > len(resource_lines):
        return {**record, "convention": "literals"}
    return {**record, "convention": "undetermined"}


def describe_text_convention(convention: dict) -> str:
    """The convention in words for a prompt; "" when there is nothing to follow."""
    if convention.get("convention") != "string_resources":
        return ""
    lines = [
        "User-visible text: this module keeps the text it shows the user in "
        f"{convention['strings_file']} and reads it in code through R.string "
        f"({convention['resource_references']} references, "
        f"{convention['literal_texts']} literal texts). New text shown to the "
        "user goes there as a new <string> entry, read the way the examples "
        f"below read theirs; {convention['strings_file']} is in current_files "
        "for that edit.",
    ]
    if convention.get("translations"):
        lines.append("Translations exist (" + ", ".join(convention["translations"]) + "); a new "
                     "entry left untranslated falls back to the default text.")
    lines.append("Examples from this repository:\n" + "\n".join(
        f"  {example}" for example in convention.get("examples", [])))
    return "\n".join(lines)
