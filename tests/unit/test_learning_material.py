import asyncio
import json
from dataclasses import replace

from reproagent.core.serialization import canonical_bytes, encode_record
from reproagent.store import atomic_write
from tests.unit.test_controller import setup, publish, PASSING
from tests.unit.test_experience_store import exp
from tests.integration.test_exporter import export_setup
from reproagent.core.models import TaskState
import pytest


def test_repeated_same_code_does_not_hide_previous_distinct_attempt(tmp_path, projects, facts):
    from tests.unit.test_controller import nothing
    request, model, controller, context = setup(tmp_path, projects, facts,
        plan=[publish(content=PASSING), publish(hypothesis='try 1'),
              publish(hypothesis='try 2'), publish(hypothesis='try 3'), nothing()])
    model.classification = 'NOT_REPRODUCED'
    result = asyncio.run(controller.run(request, context))
    material = exp().build_learning_input(controller.store, result, facts.context())
    assert material is not None
    text = canonical_bytes(material.payload).decode()
    assert 'parse([1])' in text
    assert 'assert parse([])' in text
    assert len(material.payload['attempts']) == 2


def test_observed_code_has_priority_over_unverified_hypotheses(tmp_path, projects, facts):
    request, _, controller, context = setup(tmp_path, projects, facts,
        plan=[publish(content=PASSING, hypothesis='guess ' * 180),
              publish(hypothesis='other guess ' * 90)])
    request.issue_file.write_text('Empty input must return [].\n' * 65, encoding='utf-8')
    result = asyncio.run(controller.run(request, context))
    material = exp().build_learning_input(controller.store, result, facts.context())
    assert material is not None
    kinds = [entry['kind'] for entry in material.payload['evidence']]
    assert kinds.count('candidate') == 2
    assert len(canonical_bytes(material.payload)) <= 8192


def test_repeated_failure_details_do_not_displace_candidate_fragments(tmp_path, projects, facts):
    from tests.unit.test_controller import nothing
    body = ('from example.parser import parse\n'
        'def test_empty_one(): assert parse([]) == []\n'
        'def test_empty_two(): assert parse([]) == []\n')
    request, model, controller, context = setup(tmp_path, projects, facts,
        plan=[publish(content=body), publish(content=body+'# adjustment\n'), nothing()])
    request.issue_file.write_text('Empty input must return [].\n' * 65, encoding='utf-8')
    model.classification = 'NOT_REPRODUCED'
    result = asyncio.run(controller.run(request, context))
    events, _ = controller.store.read_events()
    for event in events:
        if event.kind != 'run.completed':
            continue
        run_id = event.refs[0].split('/')[1]
        run = controller.store.load_record('runs', run_id)
        tests = tuple(dict(phase, crash=dict(phase.get('crash') or {}, context='duplicate ' * 100))
            if phase.get('outcome') == 'failed' else phase for phase in run.observation.tests)
        changed = replace(run, observation=replace(run.observation, tests=tests))
        atomic_write(controller.store.record_path('runs', run_id), canonical_bytes(encode_record(changed)))
    material = exp().build_learning_input(controller.store, result, facts.context())
    assert material is not None
    assert sum(entry['kind'] == 'candidate' for entry in material.payload['evidence']) == 2
    assert all(attempt['evidence_ids'] for attempt in material.payload['attempts'])
    assert len(canonical_bytes(material.payload)) <= 8192


def test_registered_fixed_snapshot_cannot_supply_learning_source(tmp_path, projects, facts):
    from reproagent.workspace import Workspace
    from reproagent.core.models import EvidenceRef
    from reproagent.core.serialization import bytes_hash
    from reproagent.learning_material import prepare_learning_input
    _, result, store, _, _, original, request = export_setup(tmp_path, projects, facts)
    atomic_write(store.root/'input/issue.md', (original.root/'issue.md').read_bytes())
    fixed_repo = projects.fixed(tmp_path/'fixed')
    fixed = Workspace(store.root, store).freeze(replace(request, repo=fixed_repo), facts.context())
    source = fixed.root/'example/parser.py'
    ref = EvidenceRef(source.relative_to(store.root).as_posix(), bytes_hash(source.read_bytes()), 1, 2)
    built = prepare_learning_input(store, result, facts.context(), exploration={'source_refs':(ref,)})
    assert built.code == 'skipped_invalid_evidence'
    assert not (store.root/'learning/evidence.jsonl').exists()


def test_small_candidate_uses_same_line_boundary_validation(tmp_path, projects, facts):
    from reproagent.learning_material import prepare_learning_input
    body = 'from example.parser import parse\nNOTE = "a\u2028b"\ndef test_empty(): assert parse([]) == []\n'
    request, _, controller, context = setup(tmp_path, projects, facts, plan=[publish(content=body)])
    result = asyncio.run(controller.run(request, context))
    built = prepare_learning_input(controller.store, result, facts.context())
    assert built.code == 'skipped_invalid_evidence'
    assert not (controller.store.root/'learning/evidence.jsonl').exists()


@pytest.mark.parametrize('mutation', ['altered', 'fixed', 'unicode_lines'])
def test_untrustworthy_exploration_source_is_rejected(tmp_path, projects, facts, mutation):
    from reproagent.core.models import EvidenceRef
    from reproagent.core.serialization import bytes_hash
    from reproagent.learning_material import prepare_learning_input
    _, result, store, _, _, snapshot, _ = export_setup(tmp_path, projects, facts)
    atomic_write(store.root / 'input/issue.md', (snapshot.root / 'issue.md').read_bytes())
    source = snapshot.root / 'example/parser.py'
    digest = bytes_hash(source.read_bytes())
    if mutation == 'altered':
        atomic_write(source, b'changed\n')
    elif mutation == 'fixed':
        source = store.root / 'runs/fixed/code/parser.py'
        atomic_write(source, b'FIXED_ONLY\n')
        digest = bytes_hash(source.read_bytes())
    else:
        atomic_write(source, 'a\u2028b\n'.encode())
        digest = bytes_hash(source.read_bytes())
        snapshot = replace(snapshot, files=tuple(replace(file, content_hash=digest, size=source.stat().st_size)
            if file.path == 'example/parser.py' else file for file in snapshot.files))
        atomic_write(store.record_path('snapshots', snapshot.snapshot_id), canonical_bytes(encode_record(snapshot)))
    ref = EvidenceRef(source.relative_to(store.root).as_posix(), digest, 1, 1)
    built = prepare_learning_input(store, result, facts.context(), exploration={'source_refs': (ref,)})
    assert built.code == 'skipped_invalid_evidence'
    assert not (store.root / 'learning/evidence.jsonl').exists()


def test_learning_retains_previous_attempt_and_final_confirmation(tmp_path, projects, facts):
    request, _, controller, context = setup(tmp_path, projects, facts,
        plan=[publish(content=PASSING), publish()])
    result = asyncio.run(controller.run(request, context))
    material = exp().build_learning_input(controller.store, result, facts.context())
    assert material is not None
    attempts = material.payload['attempts']
    assert len(attempts) == 2
    assert attempts[0]['candidate_id'] != attempts[1]['candidate_id']
    assert attempts[0]['classification'] == 'NOT_REPRODUCED'
    assert attempts[1]['classification'] == 'REPRODUCED'
    assert attempts[1]['confirmed'] is True
    assert len(attempts[1]['run_ids']) == 2
    text = canonical_bytes(material.payload).decode()
    assert 'parse([1])' in text and 'assert parse([])' in text
    assert len(canonical_bytes(material.payload)) <= 8192


def test_large_failure_body_does_not_discard_workflow_or_attempt_facts(tmp_path, projects, facts):
    _, result, store, candidate, run, snapshot, request = export_setup(tmp_path, projects, facts)
    atomic_write(store.root / 'input/issue.md', (snapshot.root / 'issue.md').read_bytes())
    changed = replace(run, observation=replace(run.observation, tests=tuple(
        dict(phase, longrepr='UNNEEDED_BODY' * 6000) if phase.get('outcome') == 'failed' else phase
        for phase in run.observation.tests)))
    atomic_write(store.record_path('runs', run.run_id), canonical_bytes(encode_record(changed)))
    store.append_event('exploration.step', (), {'result_code': 'STEP'})
    material = exp().build_learning_input(store, replace(result, status=TaskState.EXHAUSTED), facts.context())
    assert material is not None
    assert material.payload['category_materials']['workflow']
    assert material.payload['attempts'][0]['candidate_id'] == candidate.candidate_id
    assert material.payload['attempts'][0]['confirmed'] is False
    assert 'UNNEEDED_BODY' not in canonical_bytes(material.payload).decode()
    assert material.payload['omissions']
    assert len(canonical_bytes(material.payload)) <= 8192


def test_oversized_candidate_does_not_hide_protocol_pitfalls(tmp_path, projects, facts):
    # Publication remains real: a large valid candidate is deliberately not a
    # prerequisite for learning an independently recorded tool/protocol error.
    body = 'from example.parser import parse\n' + '# padding\n' * 700
    body += 'def test_empty():\n    assert parse([]) == []\n'
    request, _, controller, context = setup(tmp_path, projects, facts, plan=[publish(content=body)])
    result = asyncio.run(controller.run(request, context))
    controller.store.append_event('exploration.protocol_error', (), {'result_code':'INVALID_TOOL_INPUT'})
    material = exp().build_learning_input(controller.store, result, facts.context())
    assert material is not None
    assert 'INVALID_TOOL_INPUT' in canonical_bytes(material.payload).decode()
    assert material.payload['category_materials']['model']
    assert len(canonical_bytes(material.payload)) <= 8192


def test_source_fragments_have_verified_hashes_and_real_line_ranges(tmp_path, projects, facts):
    from reproagent.core.models import EvidenceRef
    from reproagent.core.serialization import bytes_hash
    from reproagent.learning_material import prepare_learning_input
    _, result, store, candidate, run, snapshot, _ = export_setup(tmp_path, projects, facts)
    atomic_write(store.root / 'input/issue.md', (snapshot.root / 'issue.md').read_bytes())
    source = snapshot.root / 'example/parser.py'
    ref = EvidenceRef(source.relative_to(store.root).as_posix(), bytes_hash(source.read_bytes()), 1, 2)
    built = prepare_learning_input(store, replace(result, status=TaskState.DONE), facts.context(),
        exploration={'source_refs': (ref,), 'tool_errors': (), 'capture_issues': ()})
    assert built.material is not None
    frozen = (store.root / 'learning/evidence.jsonl').read_text(encoding='utf-8')
    records = [json.loads(line) for line in frozen.splitlines()]
    assert any(ref.path in [origin['path'] for origin in entry['origin_refs']] for entry in records)
    assert 'return [values[0]]' in canonical_bytes(built.material.payload).decode()
