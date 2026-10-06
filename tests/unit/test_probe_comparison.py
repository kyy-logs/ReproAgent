import importlib

import pytest


@pytest.mark.parametrize('mode', ['too_large', 'exception', 'base_exception'])
def test_failed_operand_display_drops_previous_comparison(mode):
    probe = importlib.import_module('reproagent.adapters.languages.python_pytest.probe.reproagent_pytest_probe')
    probe.pytest_assertrepr_compare(None, '==', [1], [])
    assert probe._comparison is not None
    class DisplayFailure(BaseException):
        pass
    class Value:
        def __repr__(self):
            if mode == 'too_large': return 'x' * 9000
            if mode == 'base_exception': raise DisplayFailure('unavailable representation')
            raise ValueError('unavailable representation')
    probe.pytest_assertrepr_compare(None, '==', Value(), [])
    assert probe._comparison is None
