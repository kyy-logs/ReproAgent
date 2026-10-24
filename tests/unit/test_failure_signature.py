import pytest

from reproagent.core.failure_signature import stable_failure_message, stable_replay_message


@pytest.mark.parametrize('message', [
    'AssertionError: assert "<Tag @ 123>" == "expected"',
    "AssertionError: assert '<Tag at 0x111>' == 'expected'",
    '<Tag...dead> != <Tag at 0x111>',
])
def test_literal_values_and_unproven_abbreviations_are_preserved(message):
    assert stable_failure_message(message) == message


def test_only_an_observed_object_identity_can_be_normalized():
    assert stable_failure_message('<Tag @ 123>', (123,)) == '<Tag <address>>'
    assert stable_failure_message('<Quote @ 456>', (123,)) == '<Quote @ 456>'
    assert stable_failure_message('"<Tag @ 123>"', (123,)) == '"<Tag @ 123>"'


def test_replay_normalizes_builtin_addresses_and_pytest_summary_elapsed():
    message = (
        "AssertionError: <built-in method startswith of str object at 0xABC123>('E ')\n"
        "  1 failed in 0.41s"
    )
    assert stable_replay_message(message) == (
        "AssertionError: <built-in method startswith of str object at <address>>('E ')\n"
        "  1 failed in <elapsed>"
    )


@pytest.mark.parametrize('message', [
    'assert value == 0xABC123',
    'waited 0.41s',
    '<Tag at 0xABC123> != <Tag at 0xDEF456>',
])
def test_replay_keeps_unrelated_hex_and_durations(message):
    assert stable_replay_message(message) == message
