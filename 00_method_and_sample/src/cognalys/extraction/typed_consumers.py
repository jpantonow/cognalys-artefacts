"""The app's own declarations that receive the reported method's value with its type.

BlueGate run_v9 (2026-10-31): KeyManager.getOrCreateKeyPair(): KeyPair is
called as `keyPair = keyManager.getOrCreateKeyPair()` in MainActivity, where
keyPair is a class property (`private lateinit var keyPair: KeyPair`) that
another method passes on as `Authenticator(this, bleManager, keyPair, ...)`,
and Authenticator declares `private val keyPair: KeyPair`. The plan covered
KeyManager.kt and both activities but not Authenticator.kt, so every draft
could only rebuild KeyPair(entry.certificate.publicKey, entry.privateKey) in the
callers, which the behaviour review rightly found without effect. Changing what
the method returns needs each declaration that receives the value under its
type; value_consumers.derived_value_consumers follows a value inside one
method and classifies persistence and transmission, so it found none of them.

This follows the returned value from each resolved call site through locals
and through class properties into the class's other methods, and records every
app-owned constructor parameter, function parameter or property declared with
the value's type that receives it, with its file and line. Lexical and bounded
like value_consumers: a declaration it finds is evidence, one it misses is not
evidence that none exists. A value of a basic type (String, Int, a collection)
is not followed: its type says nothing about where it goes.
"""

from __future__ import annotations

import re
from pathlib import Path

from cognalys.extraction.repository_index import build_repository_index
from cognalys.extraction.value_consumers import (
    _JAVA_MODIFIERS,
    _JAVA_RETURN_TYPE,
    _KOTLIN_RETURN_TYPE,
    _TEST_SOURCE,
    _enclosing_call,
    _header_name,
    _parameter_list,
    _statement,
)


MAX_TYPED_CONSUMERS = 12
MAX_HOPS = 3
MAX_VISITS = 120
MAX_DECLARATION_LINES = 30
MAX_LOCAL_SCOPE_LINES = 80
# Types whose name says nothing about the value: language basics and
# collections. A parameter of one of these is not evidence of a consumer.
_BASIC_TYPES = frozenset({
    "String", "CharSequence", "Int", "Integer", "Long", "Short", "Byte", "Char",
    "Character", "Boolean", "Float", "Double", "Number", "Unit", "Void", "void",
    "Any", "Object", "Nothing", "ByteArray", "IntArray", "LongArray", "CharArray",
    "Array", "List", "MutableList", "ArrayList", "Map", "MutableMap", "HashMap",
    "Set", "MutableSet", "HashSet", "Collection", "Iterable", "Sequence", "Pair",
    "Triple", "Result", "Optional", "int", "long", "short", "byte", "char",
    "boolean", "float", "double",
})
_PROPERTY = r"\b(?:val|var)\s+{name}\s*(?::\s*(?P<ktype>[\w$.<>?, ]+?))?\s*(?:=|by\b|$)"
_JAVA_FIELD = (r"^\s*(?:(?:private|protected|public|static|final|volatile|transient)\s+)*"
               r"(?P<jtype>[A-Z][\w$.]*)(?:<[^;=]*>)?\s+{name}\s*(?:=|;)")
_CLASS_HEADER = re.compile(
    r"\b(?:class|object)\s+(?P<name>[A-Za-z_$][\w$]*)")
_TYPE_HEADER = re.compile(
    r"\b(?:class|object|interface)\s+(?P<name>[A-Za-z_$][\w$]*)")
_ASSIGNED = re.compile(
    r"^\s*(?:(?:val|var|final)\s+)?(?:[A-Z][\w$.<>?\[\]]*\s+)?(?:this\s*\.\s*)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?::\s*[\w$.<>?, ]+)?\s*=(?!=)\s*(?P<value>.*)$")


def _simple_type(text: str) -> str:
    """`java.security.KeyPair?` -> KeyPair; `List<KeyPair>` -> List."""
    head = re.split(r"[<\[,=)]", str(text or "").strip(), maxsplit=1)[0].strip()
    return head.rstrip("?!").split(".")[-1].strip()


def _depths(lines: list[str]) -> list[int]:
    """The brace depth at the start of each line (strings and comments ignored roughly)."""
    depths, depth = [], 0
    for line in lines:
        depths.append(depth)
        code = re.sub(r'"(?:\\.|[^"\\])*"', '""', line.split("//", 1)[0])
        depth = max(0, depth + code.count("{") - code.count("}"))
    return depths


def _declaration(lines: list[str], index: int) -> str:
    """The declaration starting at `index`, joined until its parameter list closes."""
    text, depth, opened = "", 0, False
    for position in range(index, min(len(lines), index + MAX_DECLARATION_LINES)):
        line = lines[position]
        text += " " + line.strip()
        depth += line.count("(") - line.count(")")
        opened = opened or "(" in line
        if opened and depth <= 0:
            break
    return " ".join(text.split())


def _parameter_type(parameter: str) -> tuple[str, str]:
    """(name, simple type) of one declared parameter, Kotlin or Java."""
    text = re.sub(r"@[\w.]+(?:\([^)]*\))?", " ", parameter.split("=", 1)[0])
    text = re.sub(r"\b(?:val|var|vararg|final|private|protected|public|internal"
                  r"|override|crossinline|noinline)\b", " ", text).strip()
    if ":" in text:
        name, declared = text.split(":", 1)
        return name.strip().split()[-1] if name.strip() else "", _simple_type(declared)
    words = text.replace("...", " ").split()
    if len(words) >= 2:
        return words[-1], _simple_type(" ".join(words[:-1]))
    return "", ""


def _call_receiver(statement: str, position: int, callee: str) -> str:
    """The receiver of the call to `callee` whose argument list holds `position`
    (`storage` in `storage.save(kp)`, `this`, a qualified `a.b`), or ''."""
    depth = 0
    for cursor in range(position - 1, -1, -1):
        char = statement[cursor]
        if char in ")]}":
            depth += 1
        elif char in "([{":
            if depth:
                depth -= 1
                continue
            before = re.search(
                rf"(?:(?P<receiver>[A-Za-z_$][\w$]*(?:\s*\??\.\s*[A-Za-z_$][\w$]*)*)\s*\??\.\s*)?"
                rf"{re.escape(callee)}\s*(?:<[^<>()]*>)?\s*$", statement[:cursor])
            if not before or not before.group("receiver"):
                return ""
            return re.sub(r"\s|\?", "", before.group("receiver"))
    return ""


def returned_type(lines: list[str], method_name: str) -> str:
    """The declared return type of `method_name` in `lines`, or ''."""
    for index, line in enumerate(lines):
        if method_name not in line or _header_name(line) != method_name:
            continue
        statement = _declaration(lines, index)
        if re.search(r"\bfun\b", statement):
            found = _KOTLIN_RETURN_TYPE.search(statement)
        else:
            found = _JAVA_RETURN_TYPE.match(_JAVA_MODIFIERS.sub(" ", statement))
        if found:
            return _simple_type(found.group("type"))
    return ""


def typed_value_consumers(repo_root: Path, callers: list, method_name: str,
                          source_path: str = "", source_cache: dict | None = None) -> dict:
    """App declarations declared with `method_name`'s return type that receive its value.

    `callers` are resolved call sites ({"path", "line"}) as the semantic
    resolution records them; `source_path` holds the reported method.
    """
    from cognalys.extraction.extract_methods_single_csv import android_source_rank

    root = Path(repo_root)
    if source_cache is None:
        source_cache = build_repository_index(root)["source_cache"]
    lines_by_path: dict[str, list[str]] = {}
    for path, content in source_cache.items():
        try:
            relative = Path(path).relative_to(root).as_posix()
        except ValueError:
            continue
        # Review of batch 13 (2026-10-31): an Authenticator in jvmMain,
        # desktopApp/jvmMain and iosMain was recorded beside the androidMain
        # one, and a commonTest caller recorded a test's FakeSigner. Code the
        # APK never compiles (another Kotlin Multiplatform target, any test
        # set: android_source_rank 2) is no consumer of its value
        # (Amethyst#4642 was closed for changing the desktop actual).
        if (_TEST_SOURCE.search(relative) or android_source_rank(Path(relative)) == 2
                or not relative.endswith((".kt", ".java"))):
            continue
        lines_by_path[relative] = str(content).splitlines()
    source = str(source_path or "").replace("\\", "/")
    if source and source not in lines_by_path:
        source = next((path for path in lines_by_path if path.endswith("/" + source)
                       or source.endswith("/" + path)), "")
    value_type = returned_type(lines_by_path.get(source) or [], method_name) if source else ""
    result = {"method": method_name, "value_type": value_type, "consumers": [],
              "bound": ("Lexical: follows the returned value from resolved call sites through "
                        "locals and class properties into the class's other methods, up to "
                        f"{MAX_HOPS} hops, and records app declarations declared with its type "
                        "that receive it. One it misses is not evidence that none exists.")}
    if not value_type or value_type in _BASIC_TYPES or not value_type[0].isupper():
        return {**result, "status": "no_typed_value"}
    depths_cache: dict[str, list[int]] = {}

    def depths(path: str) -> list[int]:
        if path not in depths_cache:
            depths_cache[path] = _depths(lines_by_path[path])
        return depths_cache[path]

    declarations: dict[str, list[tuple[str, int, str]]] = {}
    # The class, object and interface names the app declares (receiver_type).
    type_names: set[str] = set()

    def enclosing_type(path: str, index: int) -> str:
        """The class, object or interface whose body holds line `index` of `path`, or ''."""
        lines, levels = lines_by_path[path], depths(path)
        for position in range(index - 1, -1, -1):
            if levels[position] < levels[index]:
                match = _TYPE_HEADER.search(lines[position])
                if match:
                    return match.group("name")
        return ""

    def receiver_type(path: str, index: int, receiver: str) -> str:
        """The declared type of `receiver` as the code before line `index` declares it.

        A class name (a static or companion call) is its own type. A
        property, field, parameter or local is looked up by its declaration
        (`name: Type`, `Type name`, `val name = Type(...)`), the nearest one
        above the call. '' when nothing says.
        """
        receiver = receiver.split(".")[-1].strip()
        if not receiver:
            return ""
        if not type_names:
            type_names.update(match.group("name") for lines in lines_by_path.values()
                              for text in lines for match in _TYPE_HEADER.finditer(text))
            type_names.add("")
        if receiver[0].isupper() and receiver in type_names:
            return receiver
        lines = lines_by_path[path]
        declared = re.compile(
            rf"(?<![\w$]){re.escape(receiver)}\s*:\s*(?P<k>[A-Z][\w$.]*)"
            rf"|(?P<j>[A-Z][\w$.]*)(?:<[^;=()]*>)?\s+{re.escape(receiver)}\s*[=;,)]"
            rf"|\b(?:val|var)\s+{re.escape(receiver)}\s*=\s*(?P<c>[A-Z][\w$.]*)\s*\(")
        for position in range(min(index, len(lines) - 1), -1, -1):
            match = declared.search(lines[position])
            if match:
                return _simple_type(match.group("k") or match.group("j") or match.group("c"))
        return ""

    def callee_declarations(name: str) -> list[tuple[str, int, str]]:
        """(path, line index, kind) of each app declaration of a constructor or function `name`."""
        if name not in declarations:
            found = []
            constructor = re.compile(
                rf"\b(?:class|object)\s+{re.escape(name)}\b(?:\s*<[^>]*>)?\s*"
                rf"(?:(?:private|internal|public|protected)?\s*(?:@\w+\s*)*constructor\s*)?\(")
            for path, lines in lines_by_path.items():
                for index, line in enumerate(lines):
                    if name not in line:
                        continue
                    if constructor.search(line):
                        found.append((path, index, "constructor parameter"))
                    elif _header_name(line) == name:
                        # A Java constructor: a header named after a class of
                        # its file, with no return type.
                        owners = {match.group("name") for text in lines
                                  for match in [_CLASS_HEADER.search(text)] if match}
                        found.append((path, index, "constructor parameter" if name in owners
                                      else "function parameter"))
            declarations[name] = found
        return declarations[name]

    def property_type(path: str, name: str) -> tuple[int, str] | None:
        """(line index, simple type) of a class-level property or field `name` of `path`."""
        lines, levels = lines_by_path[path], depths(path)
        kotlin = re.compile(_PROPERTY.format(name=re.escape(name)))
        java = re.compile(_JAVA_FIELD.format(name=re.escape(name)))
        for index, line in enumerate(lines):
            if name not in line or levels[index] > 1:
                continue
            match = kotlin.search(line) or java.search(line)
            if match:
                declared = match.groupdict().get("ktype") or match.groupdict().get("jtype") or ""
                return index, _simple_type(declared)
        return None

    def scope(path: str, index: int, name: str, kind: str) -> tuple[int, int]:
        """The lines in which `name`, bound at line `index` as `kind`, is read.

        A class property, a field or a constructor parameter: the whole file.
        A function parameter: that function's body. A local: the rest of its
        block.
        """
        lines = lines_by_path[path]
        if kind == "constructor parameter" or (kind != "local" and property_type(path, name)):
            return 0, len(lines)
        level, entered = depths(path)[index], False
        end = index + 1
        while end < len(lines) and end - index < MAX_LOCAL_SCOPE_LINES:
            depth = depths(path)[end]
            if depth < level or (entered and depth <= level):
                break
            if depth > level:
                entered = True
            elif kind == "function parameter" and _header_name(lines[end]):
                break
            end += 1
        return index + 1, end

    consumers: list[dict] = []
    seen: set[tuple[str, int]] = set()

    def record(path: str, index: int, kind: str, declaration: str, via: str) -> bool:
        if (path, index) in seen or len(consumers) >= MAX_TYPED_CONSUMERS:
            return False
        seen.add((path, index))
        consumers.append({"path": path, "line": index + 1, "kind": kind,
                          "declaration": declaration.strip()[:200], "receives_from": via})
        return True

    # (path, line index, bound name, hop, how it was bound)
    queue: list[tuple[str, int, str, int, str]] = []

    def bind_statement(path: str, index: int, hop: int, symbol: str, is_call: bool) -> None:
        """What the statement at `index` does with `symbol`: bind it or pass it on."""
        lines = lines_by_path[path]
        statement = _statement(lines, index)
        usage = (re.compile(rf"(?<![\w$]){re.escape(symbol)}\s*\(") if is_call
                 else re.compile(rf"(?<![\w$.]){re.escape(symbol)}(?![\w$])(?!\s*[(.])"))
        assigned = _ASSIGNED.match(lines[index])
        # `this.keyPair = keyPair` binds the field, though the names agree.
        to_field = bool(re.match(r"^\s*this\s*\.", lines[index]))
        # `val key = keys.getSecretKey()` inside a method declares a local,
        # even when a class property is also named key (review of batch 13,
        # 2026-10-31: the property, initialised from other bytes, and a
        # Sender(key) in another method were recorded).
        local = bool(assigned) and not to_field and depths(path)[index] > 1 and bool(
            re.match(r"^\s*(?:(?:val|var|final)\b|[A-Z][\w$.<>?\[\]]*\s+[A-Za-z_$][\w$]*\s*=)",
                     lines[index]))
        if assigned and (assigned.group("name") != symbol or to_field) \
                and usage.search(assigned.group("value")):
            value = assigned.group("value").strip()
            # The value itself, not something computed from it.
            whole = (re.fullmatch(rf"(?:[\w$]+\s*\.\s*)*{re.escape(symbol)}\s*\(.*\)\s*;?", value)
                     if is_call else re.fullmatch(rf"{re.escape(symbol)}\s*;?", value))
            if whole:
                name = assigned.group("name")
                queue.append((path, index, name, hop, "local" if local else "assigned"))
                found = None if local else property_type(path, name)
                if found and found[1] == value_type:
                    record(path, found[0], "property", lines[found[0]], f"{path}:{index + 1}")
                return
        for occurrence in usage.finditer(statement):
            call = _enclosing_call(statement, occurrence.start())
            if not call or call[0] == symbol:
                continue
            callee, argument = call
            named = re.search(rf"(?<![\w$])([A-Za-z_$][\w$]*)\s*=\s*$",
                              statement[:occurrence.start()])
            receiver = _call_receiver(statement, occurrence.start(), callee)
            matched = 0
            for target_path, target_index, kind in callee_declarations(callee):
                if matched >= 4:
                    break
                # Review of batch 13 (2026-10-31): `cipher.init(mode, key)`
                # recorded an app `Unrelated.init(mode: Int, key: SecretKey)`,
                # and `storage.save(kp)` recorded Backup.save and another
                # module's Exporter.save. A function call is matched to the
                # declaration its receiver's type owns: with a receiver, a
                # member of that type; with none (or `this`), a function of
                # the calling file or a top-level one. A constructor is named
                # by its class. A receiver whose type nothing declares
                # matches nothing.
                if kind == "function parameter":
                    if receiver in ("", "this"):
                        if target_path != path and depths(target_path)[target_index] > 0:
                            continue
                    else:
                        owner = receiver_type(path, index, receiver)
                        if not owner or enclosing_type(target_path, target_index) != owner:
                            continue
                matched += 1
                target_lines = lines_by_path[target_path]
                declaration = _declaration(target_lines, target_index)
                parameters = _parameter_list(declaration, callee) or []
                parsed = [_parameter_type(item) for item in parameters]
                position = next((number for number, (parameter, _type) in enumerate(parsed)
                                 if named and parameter == named.group(1)), argument)
                if position >= len(parsed) or parsed[position][1] != value_type:
                    continue
                parameter = parsed[position][0]
                line = next((target_index + offset for offset in range(MAX_DECLARATION_LINES)
                             if target_index + offset < len(target_lines)
                             and re.search(rf"(?<![\w$]){re.escape(parameter)}\s*:"
                                           rf"|\b{re.escape(value_type)}\b[^,()]*\b"
                                           rf"{re.escape(parameter)}\b",
                                           target_lines[target_index + offset])), target_index)
                if record(target_path, line, kind, target_lines[line], f"{path}:{index + 1}"):
                    queue.append((target_path, line, parameter, hop + 1, kind))

    for item in callers or []:
        if not isinstance(item, dict):
            continue
        path, line = str(item.get("path", "")), int(item.get("line") or 0)
        if path in lines_by_path and 1 <= line <= len(lines_by_path[path]):
            bind_statement(path, line - 1, 1, method_name, True)
    visits = 0
    while queue and visits < MAX_VISITS and len(consumers) < MAX_TYPED_CONSUMERS:
        path, index, name, hop, kind = queue.pop(0)
        visits += 1
        if hop > MAX_HOPS:
            continue
        lines = lines_by_path[path]
        start, end = scope(path, index, name, kind)
        reading = re.compile(rf"(?<![\w$.]){re.escape(name)}(?![\w$])(?!\s*=[^=])")
        for follow in range(start, end):
            if follow != index and reading.search(lines[follow]) and (path, follow) not in seen:
                bind_statement(path, follow, hop + 1, name, False)
    return {**result, "status": "found" if consumers else "none", "consumers": consumers}


def compact_typed_consumers(record: dict | None) -> dict:
    """The planner's and the review's form: the type and each declaration, or {}."""
    if not isinstance(record, dict) or not record.get("consumers"):
        return {}
    return {"value_type": record.get("value_type", ""),
            "consumers": [{key: item.get(key, "") for key in
                           ("path", "line", "kind", "declaration", "receives_from")}
                          for item in record["consumers"]],
            "bound": record.get("bound", "")}


TYPED_CONSUMERS_GUIDANCE = (
    "typed_value_consumers, when present, lists the app's own declarations "
    "(constructor or function parameters, properties) declared with the type "
    "the reported method returns that receive its value, found by following "
    "the value from its call sites through locals and class properties: a "
    "change to what the method returns has to update each of them, so a plan "
    "that changes the returned type includes their files (their exact source is "
    "in the manifest). "
)
