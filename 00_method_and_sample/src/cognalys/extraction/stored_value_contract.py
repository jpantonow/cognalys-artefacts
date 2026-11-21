"""Bounded, provenance-carrying readers of values written to preferences.

An unresolved receiver is not equated to a receiver in another file. Accessor
aliases are followed only through a unique declaration and a single return.
This is a lexical contract collector, not a proof of whole-program data flow.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from cognalys.extraction.repository_index import build_repository_index
from cognalys.extraction.value_consumers import MAX_STATEMENT_LINES
from cognalys.solutions.signature_change import declarations, language_of, blank_comments_and_strings

VERSION = 1
_TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/', re.S)
_OPERATION = re.compile(r'\.\s*(?P<operation>putString|getString)\s*\(')


def _code(source):
    return _TOKENS.sub(lambda m: m[0] if m[0][0] in '\"\'' else
                       ''.join('\n' if c == '\n' else ' ' for c in m[0]), source)


def _closing(code, opening):
    masked = blank_comments_and_strings(code)
    depth = 0
    for end in range(opening, len(masked)):
        if masked[end] == '(':
            depth += 1
        elif masked[end] == ')':
            depth -= 1
            if not depth:
                return end
    return -1


def _receiver(code, end):
    masked = blank_comments_and_strings(code)
    cursor = end - 1
    while cursor >= 0 and masked[cursor].isspace():
        cursor -= 1
    last = cursor + 1
    while cursor >= 0:
        if masked[cursor] == ')':
            depth = 1
            cursor -= 1
            while cursor >= 0 and depth:
                depth += (masked[cursor] == ')') - (masked[cursor] == '(')
                cursor -= 1
            while cursor >= 0 and masked[cursor].isspace():
                cursor -= 1
        stop = cursor
        while cursor >= 0 and (masked[cursor].isalnum() or masked[cursor] in '_$'):
            cursor -= 1
        if stop == cursor:
            return ''
        start = cursor + 1
        while cursor >= 0 and masked[cursor].isspace():
            cursor -= 1
        if cursor < 0 or masked[cursor] != '.':
            return code[start:last].strip()
        cursor -= 1
        while cursor >= 0 and masked[cursor].isspace():
            cursor -= 1
    return ''


def collect_stored_value_contract(repo_root: Path, consumers: dict, *, index=None, seed_entries=()) -> dict:
    """Join storage reads/writes to persistence sinks reached by the finding."""
    if not consumers.get('persisted'):
        return {'version': VERSION, 'status': 'not_applicable', 'entries': [], 'required_paths': []}
    root = Path(repo_root).resolve()
    index = index or build_repository_index(root)
    sources = {Path(p).resolve().relative_to(root).as_posix(): _code(text)
               for p, text in index['source_cache'].items()
               if not re.search(r'(?:^|/)src/(?:test|androidTest)/', str(p).replace('\\', '/'))}
    classes = {}
    for path, code in sources.items():
        for owner in re.findall(r'\b(?:class|object|interface)\s+(\w+)', blank_comments_and_strings(code)):
            classes.setdefault(owner, set()).add(path)
    methods = {}
    for path, code in sources.items():
        for declaration in declarations(code, language_of(path)):
            header = re.match(r'[\w$]+\s*\([^{};]*\)\s*(?:throws\s+[\w.,\s]+|:\s*[\w.<>,?\s]+)?\s*\{', code[declaration.name_start:])
            if not header:
                continue
            opening = declaration.name_start + header.end() - 1
            depth, end = 1, opening + 1
            masked = blank_comments_and_strings(code)
            while end < len(masked) and depth:
                depth += (masked[end] == '{') - (masked[end] == '}')
                end += 1
            values = re.findall(r'\breturn\s+([^;\n]+)', code[opening + 1:end - 1])
            if len(values) == 1:
                methods.setdefault((path, declaration.name), []).append(values[0].strip())

    def owner_path(path, owner):
        candidates = classes.get(owner, set())
        imported = re.search(r'\bimport\s+([\w.]+\.' + re.escape(owner) + r')\s*;', sources[path])
        if imported:
            suffix = imported[1].replace('.', '/') + Path(path).suffix
            candidates = {p for p in candidates if p.endswith(suffix)}
        if path in candidates:
            return path
        return next(iter(candidates)) if len(candidates) == 1 else ''

    def resolve(path, expression, depth=0):
        expression = re.sub(r'\s+', '', expression)
        if depth > 6:
            return ''
        expression = re.split(r'\.putString\(', expression, maxsplit=1)[0]
        if expression.endswith('.edit()'):
            storage = resolve(path, expression[:-7], depth + 1)
            if storage:
                return storage
        call = re.fullmatch(r'(?:(\w+)\.)?(\w+)\(\)', expression)
        if call:
            target = owner_path(path, call[1]) if call[1] else path
            values = methods.get((target, call[2]), [])
            if len(values) == 1:
                return resolve(target, values[0], depth + 1)
            return ''
        field = re.fullmatch(r'(?:(\w+)\.)?(\w+)', expression)
        if field:
            target = owner_path(path, field[1]) if field[1] else path
            if not target:
                return ''
            # Resolve constants to their value, and preference fields to a
            # declaration identity; local names never unify across files.
            value = re.findall(r'\b' + re.escape(field[2]) + r'\s*=\s*("(?:\\.|[^"\\])*"|[\w.]+\(\)(?:\.edit\(\))?)\s*;', sources[target])
            if len(set(value)) > 1:
                return ''
            if len(set(value)) == 1:
                if value[0].startswith('"'):
                    return 'literal:' + value[0]
                return resolve(target, value[0], depth + 1)
            if re.search(r'\b(?:private|public|protected)\s+(?:(?:static|final)\s+)*SharedPreferences\s+' + re.escape(field[2]) + r'\b', sources[target]):
                return target + '#' + field[2]
            return ''
        if re.fullmatch(r'"(?:\\.|[^"\\])*"', expression):
            return 'literal:' + expression
        return ''

    def derivation(path, expression, depth=0):
        if depth > 3:
            return ''
        call = re.match(r'\s*(?:(\w+)\.)?(\w+)\s*\(', expression)
        if call:
            end = _closing(expression, call.end() - 1)
            if end < 0 or expression[end + 1:].lstrip().startswith('.'):
                return ''
            target = owner_path(path, call[1]) if call[1] else path
            if target and len(methods.get((target, call[2]), [])) == 1:
                return target + '#' + call[2]
        if re.fullmatch(r'\w+', expression):
            assignments = re.findall(r'\b' + re.escape(expression) + r'\s*=\s*([^;\n]+)', sources[path])
            values = {derivation(path, value, depth + 1) for value in assignments}
            if len(values) == 1:
                return values.pop()
        return ''

    operations, unresolved = [], []
    for path, code in sorted(sources.items()):
        for match in _OPERATION.finditer(blank_comments_and_strings(code)):
            opening = match.end() - 1
            closing = _closing(code, opening)
            if closing < 0:
                continue
            arguments = code[opening + 1:closing]
            key = re.match(r'\s*("(?:\\.|[^"\\])*"|[\w.]+)\s*,', arguments)
            if not key:
                continue
            receiver = _receiver(code, match.start())
            store = resolve(path, receiver)
            key_identity = resolve(path, key[1])
            record = {'path': path, 'line': code.count('\n', 0, match.start()) + 1,
                      'operation': match['operation'], 'storage_expression': receiver,
                      'key_expression': key[1], 'storage': store, 'key': key_identity,
                      'value_expression': arguments[key.end():].strip() if match['operation'] == 'putString' else '',
                      'statement': code[match.start() - len(receiver):closing + 1].strip()[:800]}
            record['derivation'] = derivation(path, record['value_expression'])
            (operations if store and key_identity else unresolved).append(record)
    sinks = [item for item in consumers.get('consumers', []) if item.get('kind') == 'persistence']
    def near(item, record):
        return (item.get('path') == record['path'] and
                abs(int(item.get('line', 0)) - record['line']) <= 5 and
                (not item.get('statement') or record['key_expression'] in item['statement']))
    def reached(record):
        return any(near(item, record) for item in sinks)
    # A sink this collector models is a preference accessor (_OPERATION) or
    # reaches one; a sink without a statement may be one. Syncthing-lite
    # (2026-10-29) writes its key to File(configFolder, FILENAME_KEY_PEM):
    # finding no preference entry there was reported as an unknown scope,
    # which kept a confirmed finding whose only blockers a reviewer settles
    # out of review-only generation.
    other_sinks = [item for item in sinks if item.get('statement')
                   and not _OPERATION.search(item['statement'])
                   and not any(near(item, record) for record in operations + unresolved)]
    seeds = {(item['storage'], item['key']) for item in operations
             if item['operation'] == 'putString' and reached(item)}
    present = {(item['storage'], item['key']) for item in operations}
    seeds.update((item['storage'], item['key']) for item in seed_entries
                 if (item['storage'], item['key']) in present)
    producer_functions = {item['derivation'] for item in operations
                          if item['derivation'] and (item['storage'], item['key']) in seeds}
    seeds.update((item['storage'], item['key']) for item in operations
                 if item['operation'] == 'putString' and item['derivation'] in producer_functions)
    entries = []
    for storage, key in sorted(seeds):
        participants = [item for item in operations if (item['storage'], item['key']) == (storage, key)]
        entries.append({'storage': storage, 'key': key,
                        'writers': [p for p in participants if p['operation'] == 'putString'],
                        'readers': [p for p in participants if p['operation'] == 'getString']})
    missing = [item for item in unresolved if reached(item)]
    limitations = []
    if index.get('metadata', {}).get('truncated'):
        limitations.append('repository source index truncated')
    if missing:
        limitations.append('persistence sink identity could not be resolved')
    ambiguous_readers = [item for item in unresolved if item['operation'] == 'getString'
                         and item['key'] in {key for _, key in seeds}]
    if ambiguous_readers:
        limitations.append('reader of an affected key has an unresolved storage identity')
    # A recollection (seed_entries) or a call without sinks expects entries.
    if not entries and (len(other_sinks) < len(sinks) or not sinks or seed_entries):
        limitations.append('no preference entry resolved from the observed persistence sinks')
    paths = sorted({p['path'] for e in entries for role in ('readers', 'writers') for p in e[role]})
    unmodelled = [{'path': item.get('path', ''), 'line': int(item.get('line', 0) or 0),
                   'statement': str(item['statement'])[:800],
                   'readers': _location_readers(sources, item)} for item in other_sinks]
    return {'version': VERSION,
            'status': 'partial' if limitations else 'complete' if entries else 'not_modelled',
            'entries': entries, 'required_paths': paths, 'limitations': limitations,
            'unresolved_sinks': missing,
            'unresolved_readers': ambiguous_readers,
            'unmodelled_sinks': unmodelled,
            'source_hashes': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths},
            'bound': 'Lexical preference accessors with unique declarations; unresolved identities and index limits are explicit. '
                     'Other persistence sinks are listed with the statements naming the same constant or literal, unmodelled.'}


def _location_readers(sources, sink, limit=8):
    """Other statements naming the location a non-preference sink writes.

    The location is a string literal or a constant the repository declares
    (FILENAME_KEY_PEM), never a local name, which differs per file. A name
    found in more than `limit` statements identifies nothing and is dropped.
    """
    statement = str(sink.get('statement', ''))
    masked = blank_comments_and_strings(statement)
    names = {name for name in re.findall(r'\b[A-Z][A-Z0-9_]*[A-Z0-9]\b', masked)
             if any(re.search(r'\b' + name + r'\s*=', code) for code in sources.values())}
    literals = set(re.findall(r'"(?:\\.|[^"\\])+"', statement))
    first = int(sink.get('line', 0) or 0)
    # The sink's own statement spans the lines its brackets stay open over:
    # value_consumers joins `File(dir,\n FILENAME_KEY_PEM).writeText(pem)`
    # into one line, and the continuation is the write, not a reader.
    last = first + statement.count('\n')
    depth = 0
    at_sink = blank_comments_and_strings(sources.get(sink.get('path'), '')).splitlines()
    for number in range(first, min(first + MAX_STATEMENT_LINES + 1, len(at_sink) + 1)):
        if number < 1:
            break
        depth += at_sink[number - 1].count('(') - at_sink[number - 1].count(')')
        last = max(last, number)
        if depth <= 0:
            break
    own = range(first, last + 1)
    readers, seen = [], set()
    for token in sorted(names | literals):
        pattern = re.compile(re.escape(token) if token in literals else r'\b' + token + r'\b')
        hits = []
        for path, code in sorted(sources.items()):
            lines = code.splitlines()
            searched = lines if token in literals else blank_comments_and_strings(code).splitlines()
            for number, line in enumerate(searched, 1):
                if not pattern.search(line) or (path == sink.get('path') and number in own):
                    continue
                if token in names and re.search(r'\b' + token + r'\s*=', line):
                    continue
                hits.append({'path': path, 'line': number, 'names': token,
                             'statement': lines[number - 1].strip()[:300]})
        if len(hits) > limit:
            continue
        for hit in hits:
            if (hit['path'], hit['line']) not in seen:
                seen.add((hit['path'], hit['line']))
                readers.append(hit)
    return readers


def promote_stored_value_sources(error: dict) -> None:
    """Make resolved consumers available as full sources to planning."""
    contract = error.get('stored_value_contract') or {}
    impact = (error.get('expanded_repository_context') or {}).get('multi_file_impact') or {}
    if not contract.get('entries') or impact.get('status') != 'available':
        return
    root = Path(error['repository_path']).resolve()
    exact = {item['source_file_path']: item for item in impact.get('exact_candidate_sources', [])}
    missing = []
    for relative in contract['required_paths']:
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            missing.append(relative)
            continue
        try:
            content = candidate.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            missing.append(relative)
            continue
        exact[relative] = {'source_file_path': relative, 'content': content,
                           'sha256': hashlib.sha256(content.encode()).hexdigest(),
                           'relevance': ['reader or writer of the same persisted value']}
    impact['exact_candidate_sources'] = list(exact.values())
    required = impact.setdefault('coverage_requirements', {})
    required['stored_value_files'] = contract['required_paths']
    scope = impact.setdefault('scope_completeness', {})
    if missing or contract['status'] != 'complete':
        scope['status'] = 'incomplete'
        scope.setdefault('reasons', []).extend([*contract['limitations'], *('missing full source: ' + p for p in missing)])
