#!/usr/bin/env python3
"""What each call in the reported method binds to, read from the shipped dex.

symbol_binding resolves a call from the source file's imports and
declarations, and it is right to refuse whatever that text cannot settle: a
call with no receiver (`getIntent()` inside an Activity), a receiver that is
itself an expression (`intent.data!!.buildUpon()`), a property of generated
code (`binding.webview`). Those refusals ended real findings. In
run_20260927_175748 the Home Assistant advisory finding (CVE-2023-41898)
abstained because "binding.webview is not resolved as an
android.webkit.WebView", and the WordPress one (GHSL-2022-046) because the
unqualified getIntent() and setResult() calls were not shown to bind to
android.app.Activity.

The compiler already answered both. Every invoke instruction in the dex
names the type and descriptor it calls -- `Landroid/webkit/WebView;.loadUrl`
-- and the dex's own class definitions give each application class's
superclass, so a call the compiler wrote against the application's class
(`EditPostActivity.getIntent`) can be followed up to the platform or
library type that declares it.

This module is independent of any specification or detector family: it
reports the binding of every call in the method and leaves it to the caller
to compare those bindings with what a rule governs.
"""
from __future__ import annotations

import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional


_INVOKE = re.compile(
    r"\binvoke-(?P<kind>virtual|super|direct|static|interface|polymorphic|custom)"
    r"(?:/range)?\s+\{[^}]*\},\s*"
    r"(?P<owner>L[^;]+;|\[[^.]+)\.(?P<name>[^:\s]+):(?P<descriptor>\([^)]*\)\S+)"
)
_DESCRIPTOR_TYPE = re.compile(r"\[*(?:L[^;]+;|[ZBSCIJFDV])")

NO_INDEX = 0xFFFFFFFF
MAX_HIERARCHY_DEPTH = 32


def dotted(descriptor: str) -> str:
    """`Landroid/webkit/WebView;` -> `android.webkit.WebView`.

    A member class is written the way source and specifications name it,
    `android.net.Uri.Builder`; a synthetic one (`MyActivity$onCreate$1`)
    keeps its binary name, since no source spells it.
    """
    text = str(descriptor or "")
    if text.startswith("L") and text.endswith(";"):
        text = text[1:-1]
    text = text.replace("/", ".")
    outer, *members = text.split("$")
    if members and all(re.fullmatch(r"[A-Z]\w*", part) for part in members):
        return ".".join([outer, *members])
    return text


def parameter_count(descriptor: str) -> int:
    """Number of parameters a method descriptor `(...)R` declares."""
    inside = str(descriptor or "")[1:].split(")", 1)[0]
    return len(_DESCRIPTOR_TYPE.findall(inside))


def invocations(disassembly: str) -> List[Dict]:
    """Every invoke instruction in `dexdump -d` output, in order."""
    found = []
    for match in _INVOKE.finditer(str(disassembly or "")):
        descriptor = match.group("descriptor")
        found.append({
            "kind": match.group("kind"),
            "static_owner": match.group("owner"),
            "name": match.group("name"),
            "descriptor": descriptor,
            "arity": parameter_count(descriptor),
        })
    return found


@dataclass
class ClassInfo:
    superclass: str = ""
    interfaces: List[str] = field(default_factory=list)
    methods: frozenset = frozenset()


ClassLookup = Callable[[str], Optional[ClassInfo]]


def resolve_owner(static_owner: str, name: str, descriptor: str,
                  lookup: ClassLookup) -> Dict:
    """The type that declares the called method, as far as the APK shows it.

    `lookup(descriptor)` returns the class as the APK defines it, or None for
    a class the APK does not define -- the platform, or a library the build
    did not package. Walking up from the static owner:

      declared_in_apk   a class the APK defines declares the method;
      outside_apk       the walk reached a class the APK does not define, so
                        the method is that type's or one of its supertypes'
                        -- a platform or external library API;
      unresolved        the walk ended without either (a cycle, a depth
                        bound, or a method only an interface declares).
    """
    if static_owner.startswith("["):
        return {"resolution": "outside_apk", "qualified_type": static_owner}
    signature = f"{name}:{descriptor}"
    current = static_owner
    for _ in range(MAX_HIERARCHY_DEPTH):
        info = lookup(current)
        if info is None:
            return {"resolution": "outside_apk", "qualified_type": dotted(current)}
        if signature in info.methods:
            return {"resolution": "declared_in_apk", "qualified_type": dotted(current)}
        if not info.superclass:
            break
        current = info.superclass
    return {"resolution": "unresolved", "qualified_type": ""}


def resolved_invocations(disassembly: str, lookup: ClassLookup) -> List[Dict]:
    records = []
    for item in invocations(disassembly):
        owner = resolve_owner(
            item["static_owner"], item["name"], item["descriptor"], lookup,
        )
        records.append({
            **item,
            "static_owner": dotted(item["static_owner"]),
            **owner,
        })
    return records


# ---------------------------------------------------------------------------
# The dex's class definitions: enough of the format to walk a hierarchy.
# ---------------------------------------------------------------------------

def _uleb128(data: bytes, offset: int) -> tuple[int, int]:
    result, shift = 0, 0
    while True:
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, offset
        shift += 7


class _Dex:
    def __init__(self, data: bytes):
        self.data = data
        (self.string_ids_size, self.string_ids_off,
         self.type_ids_size, self.type_ids_off,
         self.proto_ids_size, self.proto_ids_off) = struct.unpack_from("<6I", data, 0x38)
        (self.method_ids_size, self.method_ids_off,
         self.class_defs_size, self.class_defs_off) = struct.unpack_from("<4I", data, 0x58)
        self._strings: Dict[int, str] = {}
        self._protos: Dict[int, str] = {}

    def string(self, index: int) -> str:
        if index not in self._strings:
            offset = struct.unpack_from("<I", self.data, self.string_ids_off + 4 * index)[0]
            _length, start = _uleb128(self.data, offset)
            end = self.data.index(b"\x00", start)
            self._strings[index] = self.data[start:end].decode("utf-8", errors="replace")
        return self._strings[index]

    def type_name(self, index: int) -> str:
        descriptor_index = struct.unpack_from("<I", self.data, self.type_ids_off + 4 * index)[0]
        return self.string(descriptor_index)

    def type_list(self, offset: int) -> List[str]:
        if not offset:
            return []
        size = struct.unpack_from("<I", self.data, offset)[0]
        return [
            self.type_name(struct.unpack_from("<H", self.data, offset + 4 + 2 * i)[0])
            for i in range(size)
        ]

    def proto(self, index: int) -> str:
        if index not in self._protos:
            _shorty, return_index, parameters_off = struct.unpack_from(
                "<3I", self.data, self.proto_ids_off + 12 * index)
            parameters = "".join(self.type_list(parameters_off))
            self._protos[index] = f"({parameters}){self.type_name(return_index)}"
        return self._protos[index]

    def method_signature(self, index: int) -> str:
        _class_index, proto_index, name_index = struct.unpack_from(
            "<HHI", self.data, self.method_ids_off + 8 * index)
        return f"{self.string(name_index)}:{self.proto(proto_index)}"

    def class_offsets(self) -> Iterable[tuple[str, int]]:
        for i in range(self.class_defs_size):
            offset = self.class_defs_off + 32 * i
            class_index = struct.unpack_from("<I", self.data, offset)[0]
            yield self.type_name(class_index), offset

    def class_info(self, offset: int) -> ClassInfo:
        (_class_index, _access, superclass_index, interfaces_off, _source,
         _annotations, class_data_off, _static) = struct.unpack_from("<8I", self.data, offset)
        methods = set()
        if class_data_off:
            position = class_data_off
            sizes = []
            for _ in range(4):
                value, position = _uleb128(self.data, position)
                sizes.append(value)
            for _ in range(sizes[0] + sizes[1]):
                _diff, position = _uleb128(self.data, position)
                _flags, position = _uleb128(self.data, position)
            for count in sizes[2:]:
                method_index = 0
                for _ in range(count):
                    diff, position = _uleb128(self.data, position)
                    _flags, position = _uleb128(self.data, position)
                    _code, position = _uleb128(self.data, position)
                    method_index += diff
                    methods.add(self.method_signature(method_index))
        return ClassInfo(
            superclass="" if superclass_index == NO_INDEX else self.type_name(superclass_index),
            interfaces=self.type_list(interfaces_off),
            methods=frozenset(methods),
        )


class ApkHierarchy:
    """Class definitions of every dex in an APK, decoded on demand."""

    def __init__(self, apk_path: Path):
        self._dexes: List[_Dex] = []
        self._index: Dict[str, tuple[int, int]] = {}
        self._cache: Dict[str, Optional[ClassInfo]] = {}
        with zipfile.ZipFile(apk_path) as archive:
            names = sorted(
                name for name in archive.namelist()
                if re.fullmatch(r"classes\d*\.dex", name)
            )
            for name in names:
                dex = _Dex(archive.read(name))
                position = len(self._dexes)
                self._dexes.append(dex)
                for descriptor, offset in dex.class_offsets():
                    # The first definition wins, as it does for the runtime's
                    # class loader over classes.dex, classes2.dex, ...
                    self._index.setdefault(descriptor, (position, offset))

    def __call__(self, descriptor: str) -> Optional[ClassInfo]:
        if descriptor not in self._cache:
            located = self._index.get(descriptor)
            self._cache[descriptor] = (
                None if located is None
                else self._dexes[located[0]].class_info(located[1])
            )
        return self._cache[descriptor]


def method_bytecode_bindings(apk_path: Path, methods: List[Dict]) -> Dict:
    """Resolved invocations of each disassembled overload of the method.

    `methods` is what apk_evidence.disassemble_apk_method returns under
    "methods". The dex being unreadable is reported, never raised.
    """
    try:
        # Class definitions are decoded lazily, while the invocations are
        # resolved, so both steps sit inside the guard.
        hierarchy = ApkHierarchy(Path(apk_path))
        resolved = [
            {
                "signature": item.get("signature", ""),
                "invocations": resolved_invocations(item.get("disassembly", ""), hierarchy),
            }
            for item in methods if isinstance(item, dict)
        ]
    except (OSError, zipfile.BadZipFile, struct.error, ValueError, IndexError) as exc:
        return {"status": "unavailable", "reason": f"dex_unreadable: {exc}"[:300]}
    return {"status": "available", "methods": resolved}


def bindings_by_name(bytecode: Dict | None) -> Dict[str, List[Dict]]:
    """Resolved invocations of the method grouped by called name."""
    grouped: Dict[str, List[Dict]] = {}
    for method in (bytecode or {}).get("methods", []) or []:
        for item in method.get("invocations", []) or []:
            grouped.setdefault(str(item.get("name", "")), []).append(item)
    return grouped


def settle_from_bytecode(binding: Dict, grouped: Dict[str, List[Dict]]) -> Dict:
    """A source binding completed from the bytecode, when that is unambiguous.

    The call is matched by name and parameter count. When every matching
    invoke resolves to the same type, that type is the binding; when they
    disagree, or none matches, the source binding is returned unchanged
    with the candidates the bytecode offered.
    """
    candidates = [
        item for item in grouped.get(str(binding.get("called_name", "")), [])
        if item.get("arity") == binding.get("arity")
        and item.get("resolution") in {"declared_in_apk", "outside_apk"}
    ]
    types = sorted({item["qualified_type"] for item in candidates})
    if len(types) != 1:
        if types:
            return {**binding, "bytecode_candidates": types}
        return binding
    return {
        **binding,
        "resolution": "bytecode_invocation",
        "qualified_type": types[0],
        "bytecode_owner": {
            "static_owner": candidates[0]["static_owner"],
            "descriptor": candidates[0]["descriptor"],
            "resolution": candidates[0]["resolution"],
        },
        "note": (
            "bound from the invoke instruction the compiler wrote for this "
            "call in the analysed APK"
            + ("" if candidates[0]["resolution"] == "declared_in_apk" else
               "; the APK does not define that type, so the method is a "
               "platform or external library API declared by it or one of "
               "its supertypes")
        ),
    }
