"""A static skip finding must never be mistaken for target execution evidence."""
import pytest


def assess(source, selectors=('tests/test_repro.py',), platform='linux', files=None):
    from reproagent.core.delivery import assess_delivery
    return assess_delivery(files or {'tests/test_repro.py': source}, selectors, platform, collection_context_known=True)


@pytest.mark.parametrize('guard', [
    "not sys.platform.startswith('win')", "sys.platform != 'win32'",
    "sys.platform == 'linux'", "os.name != 'nt'",
    "sys.platform == 'linux' and os.name == 'posix'",
    "sys.platform == 'linux' or unavailable()",
])
def test_windows_only_test_is_proven_incompatible_on_linux(guard):
    result = assess(f"import sys, os, pytest\n@pytest.mark.skipif({guard}, reason='Windows')\ndef test_path():\n    assert False\n")
    assert result['status'] == 'incompatible'
    assert result['skipped_tests'] == ['tests/test_repro.py::test_path']
    assert result['unknown_tests'] == []
    assert result['target_platform'] == 'linux'


def test_windows_guard_remains_unverified_on_windows_and_never_becomes_validated():
    result = assess("import sys, pytest\n@pytest.mark.skipif(not sys.platform.startswith('win'), reason='Windows')\ndef test_path(): pass", platform='win32')
    assert result['status'] == 'target_unverified'
    assert result['skipped_tests'] == []
    assert result['unknown_tests'] == ['tests/test_repro.py::test_path']


def test_one_viable_control_prevents_all_skip_rejection():
    source = "import pytest, sys\n@pytest.mark.skipif(sys.platform == 'linux', reason='platform')\ndef test_guarded(): pass\ndef test_control(): pass"
    result = assess(source)
    assert result['status'] == 'target_unverified'
    assert result['skipped_tests'] == ['tests/test_repro.py::test_guarded']
    assert result['unknown_tests'] == ['tests/test_repro.py::test_control']
    assert assess(source, ('tests/test_repro.py::test_guarded',))['status'] == 'incompatible'


def test_aliases_module_and_class_marks_apply_to_selected_nodes():
    source = """import pytest as pt
from sys import platform as host
from os import name as system_name
pytestmark = pt.mark.skipif(host != 'win32' and system_name == 'posix', reason='Windows')
class TestPaths:
    def test_one(self): pass
    def test_two(self): pass
"""
    result = assess(source, ('tests/test_repro.py::TestPaths',))
    assert result['status'] == 'incompatible'
    assert len(result['skipped_tests']) == 2


@pytest.mark.parametrize('source', [
    "import pytest\n@pytest.mark.skip(reason='unsupported')\nclass TestPaths:\n    def test_one(self): pass",
    "import pytest, sys\nclass TestPaths:\n    pytestmark = [pytest.mark.skipif(sys.platform == 'linux', reason='platform')]\n    def test_one(self): pass",
])
def test_class_skip_marks_are_understood(source):
    assert assess(source)['status'] == 'incompatible'


@pytest.mark.parametrize('source', [
    "import pytest, sys\nsys = fake\n@pytest.mark.skipif(sys.platform == 'linux', reason='x')\ndef test_one(): pass",
    "import pytest, sys\n@pytest.mark.skipif(sys.platform == 'linux', reason='x')\ndef test_one(): pass\nsys.platform = 'linux'",
    "import pytest, sys\nclass TestPaths:\n    sys = fake\n    @pytest.mark.skipif(sys.platform == 'linux', reason='x')\n    def test_one(self): pass",
    "import pytest, sys\n@pytest.mark.skipif(dynamic(), reason='x')\ndef test_one(): pass",
    "import pytest, sys\n@pytest.mark.skipif(unknown and sys.platform == 'linux', reason='x')\ndef test_one(): pass",
    "import pytest, sys\n@pytest.mark.skipif(\"sys.platform == 'linux'\", reason='x')\ndef test_one(): pass",
    "import pytest, sys\n@pytest.mark.parametrize('value', [1,2])\n@pytest.mark.skipif(sys.platform == 'linux', reason='x')\ndef test_one(value): pass",
    "import pytest, sys\n@pytest.mark.skipif(sys.platform == 'linux', reason='x')\nclass TestPaths(Base):\n    def test_one(self): pass",
    "import pytest, sys\nclass TestPaths:\n    @pytest.mark.skipif(sys.platform == 'linux', reason='x')\n    def test_one(self): pass\n    def __init__(self): pass",
    "import pytest, sys\n@pytest.mark.skipif(sys.platform == 'linux', reason='x')\ndef test_one(): pass\npytestmark = unknown_marks()",
    "import pytest, sys\n@pytest.mark.skipif(sys.platform == 'linux', reason='x')\ndef test_one(): pass\ndef pytest_collection_modifyitems(items): pass",
    "from elsewhere import *\nimport pytest, sys\n@pytest.mark.skipif(sys.platform == 'linux', reason='x')\ndef test_one(): pass",
])
def test_dynamic_or_shadowed_collection_cannot_prove_all_skip(source):
    result = assess(source)
    assert result['status'] == 'target_unverified'
    assert result['unknown_tests']


def test_test_body_is_never_executed_or_used_for_skip_proof():
    source = "import pytest, sys\ndef test_one():\n    sys = fake\n    pytest.skip('not portable')\n    raise RuntimeError('must never execute')"
    assert assess(source)['status'] == 'target_unverified'


@pytest.mark.parametrize('source', [
    "import pytest, sys\n@pytest.mark.skipif((sys.platform or 'win32') == True, reason='x')\ndef test_one(): pass",
    "import pytest\n__test__ = False\n@pytest.mark.skip(reason='x')\ndef test_one(): pass",
    "import pytest\n@pytest.mark.skip(reason='x')\nclass TestPaths:\n    __test__ = False\n    def test_one(self): pass",
    "import pytest\n@pytest.mark.skip(reason='x')\ndef test_one(value=mutate_collection()): pass",
    "import pytest\n@pytest.mark.skip(reason='x')\ndef test_one(value: mutate_collection()): pass",
    "import pytest\n@pytest.mark.skip(reason='x')\ndef test_one(): pass\ndef helper(value=mutate_collection()): pass",
    "import pytest\n@pytest.mark.skip(reason='x')\ndef test_one(): pass\nclass Helpers:\n    def method(self, value=mutate_collection()): pass",
])
def test_collection_suppression_and_import_time_side_effects_are_not_skip_proof(source):
    assert assess(source)['status'] == 'target_unverified'


def test_function_local_alias_does_not_change_decorator_namespace():
    source = "import pytest, sys\n@pytest.mark.skipif(sys.platform != 'win32', reason='Windows')\ndef test_one():\n    sys = fake\n    return sys.platform"
    assert assess(source)['status'] == 'incompatible'


@pytest.mark.parametrize('marker', ["pytest.mark.skipif(True)", "pytest.mark.skipif(True, reason='a', reason='b')"])
def test_invalid_marker_arguments_do_not_establish_skipped_tests(marker):
    assert assess(f'import pytest\n@{marker}\ndef test_one(): pass')['status'] == 'target_unverified'


def test_unresolved_selectors_missing_files_and_syntax_errors_stay_unverified():
    source = "import pytest\n@pytest.mark.skip(reason='x')\ndef test_one(): pass"
    for selectors in [(), ('missing.py',), ('tests/test_repro.py::missing',), ('tests/test_repro.py::test_one[dynamic]',)]:
        assert assess(source, selectors)['status'] == 'target_unverified'
    assert assess('this is not valid python = !')['status'] == 'target_unverified'


def test_multiple_files_and_missing_selector_do_not_hide_viable_tests():
    files = {'tests/test_repro.py': b"import pytest\n@pytest.mark.skip(reason='x')\ndef test_one(): pass",
             'tests/test_other.py': 'def test_other(): pass'}
    assert assess('', tuple(files), files=files)['status'] == 'target_unverified'
    assert assess('', ('tests/test_repro.py', 'missing.py'), files=files)['status'] == 'target_unverified'


def test_candidate_conftest_can_change_collection_so_no_incompatibility_is_claimed():
    files = {'tests/test_repro.py': "import pytest\n@pytest.mark.skip(reason='x')\ndef test_one(): pass", 'tests/conftest.py': 'def pytest_collection_modifyitems(items): pass'}
    assert assess('', ('tests/test_repro.py',), files=files)['status'] == 'target_unverified'


@pytest.mark.parametrize('platform', ['darwin', 'linux', 'win32'])
def test_known_supported_platforms_produce_a_bounded_result(platform):
    result = assess('def test_one(): pass', platform=platform)
    assert result['status'] == 'target_unverified'
    assert isinstance(result['reason'], str) and result['reason']


def test_invalid_platform_is_rejected_without_reading_or_running_candidate():
    with pytest.raises(ValueError, match='platform'):
        assess('raise RuntimeError()', platform='freebsd')



def test_external_import_prevents_a_runtime_all_skip_proof():
    from reproagent.core.delivery import assess_delivery
    files={'tests/test_repro.py': 'import sys\nimport pytest\nimport mark_rewriter\n@pytest.mark.skipif(not sys.platform.startswith("win"),reason="Windows")\ndef test_target(): pass\n'}
    assert assess_delivery(files,('tests/test_repro.py',),'linux',collection_context_known=True)['status']=='target_unverified'


def test_unknown_original_collection_context_cannot_prove_runtime_skip():
    files={'tests/test_repro.py': 'import sys\nimport pytest\n@pytest.mark.skipif(not sys.platform.startswith("win"),reason="Windows")\ndef test_target(): pass\n'}
    from reproagent.core.delivery import assess_delivery as actual
    result=actual(files,('tests/test_repro.py',),'linux',collection_context_known=False)
    assert result['status']=='target_unverified'


def test_default_collection_context_is_unknown():
    from reproagent.core.delivery import assess_delivery
    assert assess_delivery({"test_repro.py":"import pytest\n@pytest.mark.skip(reason='x')\ndef test_one(): pass"},("test_repro.py",),"linux")["status"] == "target_unverified"
