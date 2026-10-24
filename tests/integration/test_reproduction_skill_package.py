import json
import sys
from pathlib import Path
from zipfile import ZipFile

from tests.integration.test_installed_package import command


def test_wheel_loads_skill_without_checkout_or_runtime_dependencies(tmp_path):
    checkout = Path(__file__).resolve().parents[2]
    command([sys.executable, '-m', 'pip', 'wheel', checkout, '--no-index', '--no-deps',
        '--no-build-isolation', '--wheel-dir', tmp_path / 'wheels'])
    wheel = next((tmp_path / 'wheels').glob('*.whl'))
    resource = 'reproagent/resources/skills/reproduce-bug/SKILL.md'
    with ZipFile(wheel) as archive:
        source = archive.read(resource).decode('utf-8').replace('\r\n', '\n')
    body = source.partition('\n---\n')[2].strip()
    start = source.index('<!-- decision-card -->') + len('<!-- decision-card -->')
    end = source.index('<!-- /decision-card -->')
    expected_card = source[start:end].strip()
    script = """import json, sys
sys.path.insert(0, sys.argv[1])
from reproagent.core.agent import reproduction_skill, reproduction_skill_checklist
import reproagent
print(json.dumps({'body': reproduction_skill(), 'card': reproduction_skill_checklist(),
                  'module': reproagent.__file__}))
"""
    data = json.loads(command([sys.executable, '-I', '-c', script, wheel], cwd=tmp_path).stdout)
    assert data['body'] == body and body
    assert data['card'] == expected_card and expected_card
    assert str(wheel) in data['module']
