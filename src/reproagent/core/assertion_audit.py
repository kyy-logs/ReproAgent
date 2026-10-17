"""Locate common Python/pytest expectations without executing candidate code.

This inventory makes omissions checkable; it is not a proof of test semantics.
Custom assertion helpers still require review of the complete candidate.
"""
import ast


def with_execution_observations(sites, phases):
    """Locate unique failure-frame overlaps; never infer assertion entry.

    Input phases already have checked run paths normalized by review_context.
    Absent frames (including passing tests) never prove an assertion was skipped
    or passed; this is traceback evidence, not line/branch coverage.
    """
    failed_locations = []
    for index, phase in enumerate(phases):
        if phase.get('outcome') != 'failed':
            continue
        for frame in [*phase.get('exception_frames', ()), phase.get('crash') or {}]:
            path, line = frame.get('path', ''), frame.get('lineno')
            if type(path) is not str or type(line) is not int or line < 1:
                continue
            failed_locations.append((path.removeprefix('<run>/'), line, index))
    observed = {}
    for path, line, index in set(failed_locations):
        matches = [site['id'] for site in sites
                   if path == site['path'] and site['start_line'] <= line <= site['end_line']]
        if len(matches) == 1:
            observed.setdefault(matches[0], set()).add(index)
    result = []
    for site in sites:
        indexes = sorted(observed.get(site['id'], ()))
        result.append({**site, 'execution_observation': {
            'state':'failure_location_overlap' if indexes else 'not_established',
            'phase_indexes':indexes}})
    return result


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
            # Python accepts a UTF-8 BOM when compiling file bytes. Strip only
            # this encoding marker for parsing, preserving source/hash/lines.
            tree = ast.parse(entry['content'].removeprefix('\ufeff'))
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
