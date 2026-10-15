"""Bounded static questions about risky oracle assumptions, never defect verdicts.

This deliberately does not interpret arbitrary Python, prove provenance, or decide
whether a source requires particular wording. Unknowns remain visible to review.
"""
import ast
import re
import textwrap

from .assertion_audit import EXPECTATION_CALLS, assertion_sites


_LIMIT = 240
_MATCHERS = {'fnmatch_lines', 'fnmatch_lines_random', 're_match_lines', 're_match_lines_random'}
_PURE_NAMES = {'enumerate', 'range', 'len', 'str', 'list', 'tuple', 'next', 'iter', 'zip', 'sorted'}


def _short(text):
    return text if len(text) <= _LIMIT else text[:_LIMIT - 3] + '...'


def _name(node):
    return node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''


def _kind(node):
    if isinstance(node, ast.Assert):
        return 'assert'
    if isinstance(node, ast.Call):
        name = _name(node)
        if name in EXPECTATION_CALLS or name.startswith('assert'):
            return name
    return ''


class _Audit:
    def __init__(self, entry, tree):
        self.entry, self.tree = entry, tree
        self.parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        self.functions = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions.setdefault(node.name, []).append(node)
        self.metadata = []
        embedded_nodes = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or '\n' not in node.value:
                continue
            if len(node.value) > 32768:
                continue
            try:
                embedded = ast.parse(textwrap.dedent(node.value).strip())
            except (SyntaxError, ValueError, RecursionError):
                continue
            for definition in ast.walk(embedded):
                if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    embedded_nodes.add(node)
                    self.metadata.append((node, definition.name, 'embedded_metadata'))
                    doc = ast.get_docstring(definition)
                    if doc:
                        self.metadata.append((node, doc, 'embedded_metadata'))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or _name(call) in _MATCHERS:
                continue
            ancestor = call
            while ancestor in self.parents and not isinstance(ancestor, ast.Assert):
                ancestor = self.parents[ancestor]
            if isinstance(ancestor, ast.Assert):
                continue
            for value in [*call.args, *(keyword.value for keyword in call.keywords)]:
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and value not in embedded_nodes:
                    self.metadata.append((value, value.value, 'input_literal'))

    def scope(self, node):
        while node in self.parents:
            node = self.parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return node
        return self.tree

    def binding(self, name, at):
        scopes = [self.scope(at), self.tree]
        for scope in scopes:
            matches = []
            for node in ast.walk(scope):
                if self.scope(node) is not scope or getattr(node, 'lineno', 0) >= at.lineno:
                    continue
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(target, ast.Name) and target.id == name for target in targets) and node.value is not None:
                        matches.append((node.lineno, node.value))
                if isinstance(node, ast.For) and isinstance(node.target, (ast.Tuple, ast.List)):
                    for index, target in enumerate(node.target.elts):
                        if isinstance(target, ast.Name) and target.id == name and isinstance(node.iter, (ast.Tuple, ast.List)):
                            values = [item.elts[index] for item in node.iter.elts
                                      if isinstance(item, (ast.Tuple, ast.List)) and len(item.elts) > index]
                            matches.extend((node.lineno, value) for value in values)
            if matches:
                latest = max(line for line, _ in matches)
                return [value for line, value in matches if line == latest]
        return []

    def strings(self, node, seen=None):
        seen = set() if seen is None else seen
        if node in seen or len(seen) >= 128:
            return []
        seen.add(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name):
            return [text for value in self.binding(node.id, node) for text in self.strings(value, seen)]
        return [text for child in ast.iter_child_nodes(node) for text in self.strings(child, seen)]

    def questions(self, site, node):
        rows, keys = [], set()

        def add(kind, at, details):
            key = (kind, at.lineno, at.col_offset, repr(details))
            if key in keys:
                return
            keys.add(key)
            expression = ast.get_source_segment(self.entry['content'].removeprefix('\ufeff'), at) or ast.unparse(at)
            rows.append({'assertion_id': site['id'], 'path': self.entry['path'],
                         'start_line': at.lineno, 'end_line': at.end_lineno,
                         'kind': kind, 'expression': _short(expression),
                         'details': {key: _short(value) if isinstance(value, str) else value for key, value in details.items()}})
            if len(expression) > _LIMIT or any(isinstance(value, str) and len(value) > _LIMIT for value in details.values()):
                unknown(at, 'display is abbreviated; inspect the complete candidate lines')

        def unknown(at, reason):
            add('analysis_unknown', at, {'reason': reason})

        expected = []
        if isinstance(node, ast.Call) and _name(node) in _MATCHERS:
            patterns = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == 'lines'), None)
            if isinstance(patterns, ast.Constant) and isinstance(patterns.value, str):
                pattern_nodes = [patterns]
            elif isinstance(patterns, (ast.List, ast.Tuple)):
                pattern_nodes = patterns.elts
            else:
                pattern_nodes = []
                unknown(node, 'dynamic matcher patterns cannot be decomposed statically')
            if len(pattern_nodes) > 1:
                consecutive = next((kw.value for kw in node.keywords if kw.arg == 'consecutive'), None)
                if consecutive is not None and not (isinstance(consecutive, ast.Constant) and type(consecutive.value) is bool):
                    unknown(consecutive, 'dynamic matcher line-composition option')
                add('matcher_sequence', node, {'pattern_count': len(pattern_nodes),
                    'ordered_lines': not _name(node).endswith('_random'),
                    'consecutive': False if consecutive is None else consecutive.value
                        if isinstance(consecutive, ast.Constant) and type(consecutive.value) is bool else None})
            for pattern in pattern_nodes:
                if not isinstance(pattern, ast.Constant) or not isinstance(pattern.value, str):
                    unknown(pattern, 'dynamic matcher pattern cannot be decomposed statically')
                    continue
                if not _name(node).startswith('fnmatch'):
                    unknown(pattern, 'regular expression semantics require complete manual review')
                    expected.extend(self.strings(pattern))
                    continue
                # Character classes affect literal interpretation; do not claim a
                # complete decomposition of that grammar with a wildcard split.
                if '[' in pattern.value:
                    unknown(pattern, 'glob character class is not decomposed')
                    continue
                fragments = [text for text in re.split(r'[*?]+', pattern.value) if text]
                for index, fragment in enumerate(fragments):
                    add('pattern_fragment', pattern, {'index': index, 'text': fragment})
                    expected.append(fragment)
                add('pattern_composition', pattern, {'same_line': True, 'ordered_fragments': True,
                    'fragment_count': len(fragments), 'matcher': _name(node),
                    'anchored_start': not pattern.value.startswith('*'),
                    'anchored_end': not pattern.value.endswith('*')})
        if isinstance(node, ast.Assert):
            for part in ast.walk(node.test):
                if isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute) and part.func.attr in {'search', 'match', 'fullmatch'} and part.args:
                    expected.extend(self.strings(part.args[0]))
                    unknown(part, 'regular expression semantics require complete manual review')
                elif isinstance(part, ast.Compare):
                    for operand in [part.left, *part.comparators]:
                        if isinstance(operand, (ast.Constant, ast.Name, ast.BinOp)):
                            expected.extend(self.strings(operand))

        visited, helper_stack = set(), set()

        def dependencies(part, in_helper=False, depth=0):
            if part in visited:
                return
            visited.add(part)
            if depth > 16 or len(visited) > 512:
                unknown(node, 'oracle dependency traversal limit reached')
                return
            if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Load):
                for value in self.binding(part.id, part):
                    dependencies(value, in_helper, depth + 1)
            if isinstance(part, ast.Call) and isinstance(part.func, ast.Name):
                definitions = self.functions.get(part.func.id, ())
                if len(definitions) == 1:
                    if definitions[0] in helper_stack:
                        unknown(part, 'recursive helper requires complete manual review')
                    else:
                        helper_stack.add(definitions[0])
                        for statement in definitions[0].body:
                            dependencies(statement, True, depth + 1)
                        helper_stack.remove(definitions[0])
                elif definitions or in_helper and part.func.id not in _PURE_NAMES:
                    unknown(part, 'helper call cannot be resolved statically')
            if isinstance(part, ast.Call) and not isinstance(part.func, (ast.Name, ast.Attribute)):
                unknown(part, 'dynamic helper call cannot be resolved statically')
            if in_helper and isinstance(part, ast.Compare):
                add('helper_predicate', part, {'question': 'Does this selection predicate preserve source-supported outputs?'})
            if in_helper and isinstance(part, ast.Subscript):
                add('helper_selection', part, {'question': 'Is this index or window required by the original sources?'})
            for child in ast.iter_child_nodes(part):
                dependencies(child, in_helper, depth + 1)

        dependencies(node.test if isinstance(node, ast.Assert) else node)
        words = sorted({word for text in expected for word in re.findall(r'[A-Za-z_][A-Za-z_0-9]*', text) if len(word) > 1})
        for word in words:
            for input_node, text, source_kind in self.metadata:
                if re.search(r'(?<!\w)' + re.escape(word) + r'(?!\w)', text):
                    add('candidate_text_overlap', node, {'word': word, 'input_line': input_node.lineno,
                        'source_kind': source_kind,
                        'question': 'Can candidate-provided metadata satisfy this expectation without the requested behavior?'})
                    break
        return rows


def oracle_checks(candidate_files):
    """Return deterministic review questions, sharing the assertion inventory IDs.

    Only literal output matchers, locally reachable selectors and recognized
    embedded Python metadata are inspected. No absence of rows proves safety.
    """
    sites = assertion_sites(candidate_files)
    by_path = {}
    for entry in sorted(candidate_files, key=lambda item: item['path']):
        if entry['role'] != 'test' or not entry['path'].endswith('.py'):
            continue
        tree = ast.parse(entry['content'].removeprefix('\ufeff'))
        nodes = sorted((node for node in ast.walk(tree) if _kind(node)),
                       key=lambda node: (node.lineno, node.col_offset, node.end_lineno, _kind(node)))
        by_path[entry['path']] = (_Audit(entry, tree), iter(nodes))
    rows = []
    for site in sites:
        audit, nodes = by_path[site['path']]
        rows.extend(audit.questions(site, next(nodes)))
    return [{'id': f'O{index}', **row} for index, row in enumerate(rows, 1)]
