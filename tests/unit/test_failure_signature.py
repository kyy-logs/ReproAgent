import pytest

from reproagent.core.failure_signature import stable_failure_message


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
