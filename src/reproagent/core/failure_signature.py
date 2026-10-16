"""Conservative normalization of object addresses in pytest failure displays."""
import re


_ADDRESSED_REPR = re.compile(r'<((?:[^<>\n]|<locals>|<genexpr>)+?) (?:@ ([0-9]+)|at (0x[0-9a-fA-F]+))>')
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


# Match the framework's actual output line, not a requested fnmatch/regex pattern.
_PYTEST_SUMMARY = re.compile(
    r"(?m)^(?P<before> +and: ['\"]={3,} "
    r"[0-9]+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)"
    r"(?:, [0-9]+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?))* in )"
    r"[0-9]+(?:\.[0-9]+)?s(?P<after> ={3,}['\"])$"
)


def stable_pytester_transcript(message, phase):
    """Ignore only pytest's elapsed-summary field in a proven LineMatcher failure.

    A candidate's expected pattern and business output remain literal. If it tests
    timing, keep the complete transcript instead of weakening that assertion.
    """
    modules = {frame.get('module') for frame in phase.get('exception_frames', ())}
    if (phase.get('exception_type') not in ('builtins.Failed', '_pytest.outcomes.Failed')
            or not {'_pytest.pytester', '_pytest.outcomes'} <= modules
            or not message.startswith('Failed: nomatch: ')
            or '\nremains unmatched: ' not in message):
        return message
    expected = [line for line in message.splitlines()
                if line.startswith(('Failed: nomatch: ', 'nomatch: ', 'remains unmatched: '))]
    if any(re.search(r'\bin\s|[0-9]', line) for line in expected):
        return message
    lines = message.splitlines(keepends=True)
    # Output which happens to resemble a summary is still business data. Require
    # the pytest session and short-summary delimiters, and touch ONLY the final
    # actual-output line immediately before the unmatched-pattern diagnostic.
    if len(lines) < 5 or not lines[-1].startswith('remains unmatched: '):
        return message
    if not any(re.fullmatch(r" +and: ['\"]={3,} test session starts ={3,}['\"]", line.rstrip('\r\n'))
               for line in lines[:-2]):
        return message
    if not any(re.fullmatch(r" +and: ['\"]={3,} short test summary info ={3,}['\"]", line.rstrip('\r\n'))
               for line in lines[:-2]):
        return message
    lines[-2] = _PYTEST_SUMMARY.sub(lambda m: m['before'] + '<elapsed>s' + m['after'], lines[-2])
    return ''.join(lines)
