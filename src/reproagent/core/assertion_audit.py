"""Locate common Python/pytest expectations without executing candidate code.

This inventory makes omissions checkable; it is not a proof of test semantics.
Custom assertion helpers still require review of the complete candidate.
"""
import ast


EXPECTATION_CALLS = frozenset({'raises', 'warns', 'deprecated_call', 'fail',
    'fnmatch_lines', 'fnmatch_lines_random', 're_match_lines', 're_match_lines_random'})


def assertion_sites(candidate_files):
    located = []
    for entry in sorted(candidate_files, key=lambda item: item['path']):
        # Data may intentionally contain invalid Python for a parser/collection
        # regression. Keep it in the full review; do not parse it as a test.
        if entry['role'] != 'test' or not entry['path'].endswith('.py'):
            continue
        try:
            tree = ast.parse(entry['content'])
        except (SyntaxError, ValueError, RecursionError) as error:
            raise ValueError('candidate assertion inventory unavailable: ' + entry['path']) from error
        for node in ast.walk(tree):
            kind = ''
            if isinstance(node, ast.Assert):
                kind = 'assert'
            elif isinstance(node, ast.Call):
                name = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else node.func.id if isinstance(node.func, ast.Name) else '')
                if name in EXPECTATION_CALLS or name.startswith('assert'):
                    kind = name
            if kind:
                located.append((entry['path'], node.lineno, node.col_offset, node.end_lineno, kind))
    return [{'id': f'A{index}', 'path': path, 'start_line': line, 'end_line': end, 'kind': kind}
            for index, (path, line, _, end, kind) in enumerate(sorted(located), 1)]
