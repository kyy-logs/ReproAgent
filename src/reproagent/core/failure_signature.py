"""Conservative normalization of object addresses in pytest failure displays."""
import re


_ADDRESSED_REPR = re.compile(r'<([^<>\n]+?) (?:@ ([0-9]+)|at (0x[0-9a-fA-F]+))>')
# A generator's repr nests ``<locals>`` inside the outer angle brackets, so the
# ordinary addressed-repr pattern deliberately cannot match it.  Its address is
# still a process identity, not an assertion value; normalize only this exact
# built-in repr and only outside quoted literals.
_GENERATOR_ADDRESS = re.compile(
    r'(<generator object [^\n]*?) at 0x[0-9a-fA-F]+>')
_QUOTED = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', re.VERBOSE)


def _replace_unquoted(pattern, text, replacement):
    quoted = [(match.start(), match.end()) for match in _QUOTED.finditer(text)]

    def substitute(match):
        if any(start <= match.start() < end for start, end in quoted):
            return match.group(0)
        return replacement(match)

    return pattern.sub(substitute, text)


def stable_failure_message(message, object_ids=()):
    # Keep all values and types in the full repr; only the identity suffix varies.
    def generator(match):
        return match.group(1) + ' <address>>'

    message = _replace_unquoted(_GENERATOR_ADDRESS, message, generator)
    identities = {value for value in object_ids if type(value) is int and value > 0}

    def addressed(match):
        displayed = int(match.group(2)) if match.group(2) else int(match.group(3), 16)
        if displayed not in identities:
            return match.group(0)
        return '<' + match.group(1) + ' <address>>'

    # Never guess a truncated repr's missing content. New Probe observations
    # provide unabridged operands and observed object identities separately;
    # without that provenance even a full repr's number might be business data.
    return _replace_unquoted(_ADDRESSED_REPR, message, addressed)
