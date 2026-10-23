"""Conservative normalization of object addresses in pytest failure displays."""
import re


_ADDRESSED_REPR = re.compile(r'<([^<>\n]+?) (?:@ ([0-9]+)|at (0x[0-9a-fA-F]+))>')
_QUOTED = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', re.VERBOSE)


def stable_failure_message(message, object_ids=()):
    # Keep all values and types in the full repr; only the identity suffix varies.
    quoted = [(match.start(), match.end()) for match in _QUOTED.finditer(message)]
    identities = {value for value in object_ids if type(value) is int and value > 0}
    def addressed(match):
        if any(start <= match.start() < end for start, end in quoted):
            return match.group(0)
        displayed = int(match.group(2)) if match.group(2) else int(match.group(3), 16)
        if displayed not in identities:
            return match.group(0)
        return '<' + match.group(1) + ' <address>>'
    # Never guess a truncated repr's missing content. New Probe observations
    # provide unabridged operands and observed object identities separately;
    # without that provenance even a full repr's number might be business data.
    return _ADDRESSED_REPR.sub(addressed, message)



def _glob_requires_non_summary_character(pattern):
    # A required literal outside this summary's alphabet proves the summary
    # cannot be the expected target. Classes/wildcards provide no such proof.
    allowed = set('=. passed in seconds')
    index = 0
    while index < len(pattern):
        char = pattern[index]
        index += 1
        if char in '*?':
            continue
        if char == '[':
            end = index
            if end < len(pattern) and pattern[end] == '!': end += 1
            if end < len(pattern) and pattern[end] == ']': end += 1
            while end < len(pattern) and pattern[end] != ']': end += 1
            if end < len(pattern):
                index = end + 1
                continue
        if not char.isdecimal() and any(c not in allowed for c in char.casefold()):
            return True
    return False


def stable_pytester_message(message, phase, target_origins):
    """Normalize a narrow, attributed pytest LineMatcher display, not arbitrary text."""
    import ast
    if len(message) > 32768:
        return message
    origin = target_origins.get('_pytest.pytester')
    if (not isinstance(origin, str) or not origin
            or phase.get('exception_type') != 'builtins.Failed'
            or not any(frame.get('module') == '_pytest.pytester' and frame.get('function') == 'fnmatch_lines'
                and str(frame.get('path', '')).replace('\\', '/') == origin.replace('\\', '/')
                for frame in phase.get('exception_frames', ()))):
        return message
    lines = message.splitlines()
    if (len(lines) < 3 or not lines[0].startswith('Failed: nomatch: ')
            or not lines[-1].startswith('remains unmatched: ')):
        return message
    expected = lines[0][len('Failed: nomatch: '):]
    if message != '\n'.join(lines) or expected != lines[-1][len('remains unmatched: '):]:
        return message
    try:
        pattern = ast.literal_eval(expected)
        actual = [ast.literal_eval(line[len('    and: '):]) for line in lines[1:-1]
                  if line.startswith('    and: ')]
    except (ValueError, SyntaxError, TypeError, RecursionError):
        return message
    if (not isinstance(pattern, str) or len(actual) != len(lines) - 2
            or any(not isinstance(line, str) for line in actual)
            or re.search(r'\b(?:seconds?|duration|passed|failed|skipped|errors?)\b', pattern, re.I)
            or not _glob_requires_non_summary_character(pattern)):
        return message
    summary = re.compile(r'(?P<head>={3,} \d+ passed in )\d+(?:\.\d+)?(?P<tail> seconds ={3,})')
    result = list(lines)
    for index, line in enumerate(actual, 1):
        if summary.fullmatch(line):
            result[index] = '    and: ' + repr(summary.sub(r'\g<head><elapsed>\g<tail>', line))
    return '\n'.join(result) if result != lines else message
