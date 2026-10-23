from copy import deepcopy

import pytest

from reproagent.core import failure_signature


ORIGIN = 'C:/snapshot/src/_pytest/pytester.py'
PHASE = {'exception_type': 'builtins.Failed', 'exception_frames': [
    {'module': '_pytest.pytester', 'path': ORIGIN, 'function': 'fnmatch_lines'}]}


def message(seconds='0.06', pattern='DEBUG module:*', count=1):
    return (f'Failed: nomatch: {pattern!r}\n'
            "    and: 'rootdir: /somewhere'\n"
            f"    and: '================ {count} passed in {seconds} seconds ================'\n"
            f'remains unmatched: {pattern!r}')


def normalize(text, phase=PHASE, origins=None):
    return failure_signature.stable_pytester_message(text, phase,
        {'_pytest.pytester': ORIGIN} if origins is None else origins)


def test_only_known_pytester_transcript_elapsed_is_normalized():
    assert normalize(message()) == normalize(message('0.08'))
    assert normalize(message()) != normalize(message('0.08', count=2))
    assert normalize(message()) != normalize(message('0.08', pattern='OTHER module:*'))


@pytest.mark.parametrize('change', ['missing-origin', 'wrong-origin', 'wrong-type', 'no-frame', 'literal'])
def test_unknown_or_non_framework_messages_stay_strict(change):
    phase = deepcopy(PHASE); origins = {'_pytest.pytester': ORIGIN}
    text = message()
    if change == 'missing-origin': origins = {}
    elif change == 'wrong-origin': origins['_pytest.pytester'] = 'C:/other/pytester.py'
    elif change == 'wrong-type': phase['exception_type'] = 'builtins.AssertionError'
    elif change == 'no-frame': phase['exception_frames'] = []
    elif change == 'literal': text = 'AssertionError: ' + message()
    assert normalize(text, phase, origins) == text


def test_time_expectations_and_malformed_transcripts_are_not_normalized():
    for pattern in ('*passed in 0.10 seconds*', '*duration seconds*'):
        assert normalize(message(pattern=pattern)) == message(pattern=pattern)
    text = message() + '\nextra application data'
    assert normalize(text) == text
    text = message().replace("    and: 'rootdir: /somewhere'", '    and: not a Python string literal')
    assert normalize(text) == text


def test_source_text_is_never_mutated():
    before = deepcopy(PHASE)
    normalize(message())
    assert PHASE == before


@pytest.mark.parametrize('pattern', ['*0.05*', '[ap]*0.05*', '*', '[!x]*0.05*'])
def test_numeric_or_ambiguous_patterns_are_not_treated_as_incidental_elapsed(pattern):
    assert normalize(message(pattern=pattern)) != normalize(message('0.08', pattern=pattern))


@pytest.mark.parametrize('function', [None, 're_match_lines'])
def test_missing_or_regex_matcher_provenance_stays_strict(function):
    phase = deepcopy(PHASE)
    phase['exception_frames'][0]['function'] = function
    text = message(pattern=r'.*0\.05.*')
    assert normalize(text, phase) == text
