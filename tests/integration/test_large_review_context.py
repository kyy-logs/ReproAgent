"""A real pytest round trip whose execution loads Sphinx-scale target origins."""

import asyncio
import json
import sys
from dataclasses import replace

from reproagent.app import create_controller
from reproagent.core.models import (CandidateClass, EvidenceLevel, FixValidationRequest, ModelConfig, ModelResponse,
    PythonPytestConfig, TaskRequest, TaskState)
from reproagent.core.verifier import Verifier

MODULE_COUNT = 132
BUGGY = 'def parse(values):\n    return [values[0]]\n'
FIXED = 'def parse(values):\n    return [values[0]] if values else []\n'
ISSUE = 'Empty input must return an empty list; parse([]) raises IndexError.\n'
MODULE_BODY = 'from example.parser import parse\n\n\ndef value():\n    return parse([1])\n'
# Loading many target submodules is what made the recorded Sphinx observation overflow.
CANDIDATE = ("import importlib\n\n\ndef test_empty():\n"
    f"    for index in range({MODULE_COUNT}):\n"
    "        importlib.import_module('example.ext.autodoc.mock_%03d' % index).value()\n"
    "    from example.parser import parse\n"
    "    assert parse([]) == []\n")


def large_project(root, parser=BUGGY):
    root.mkdir(parents=True)
    (root / 'example' / 'ext' / 'autodoc').mkdir(parents=True)
    for package in ('example', 'example/ext', 'example/ext/autodoc'):
        (root / package / '__init__.py').write_text('', encoding='utf-8')
    (root / 'example' / 'parser.py').write_text(parser, encoding='utf-8')
    for index in range(MODULE_COUNT):
        (root / 'example' / 'ext' / 'autodoc' / f'mock_{index:03d}.py').write_text(MODULE_BODY, encoding='utf-8')
    (root / 'tests').mkdir()
    (root / 'tests' / 'test_existing.py').write_text(
        'from example.parser import parse\n\n\ndef test_nonempty():\n    assert parse([1]) == [1]\n', encoding='utf-8')
    (root / 'issue.md').write_text(ISSUE, encoding='utf-8')
    return root


class EvidenceCitingModel:
    """Scripted reviewer: it proves the pipeline and the bounded context, not model skill."""

    def __init__(self):
        self.step, self.reviews = 0, []

    async def complete(self, request, context):
        data = json.loads(request.messages[-1]['content'])
        if request.response_kind == 'contract':
            result = {'trigger':'empty list', 'expected':'parse([]) returns []', 'reported_actual':'IndexError',
                'source_indices':[0], 'missing_information':[], 'observable_checks':[], 'assumptions':[]}
        elif request.response_kind == 'verdict':
            self.reviews.append((data, request.messages[-1]['content']))
            refs = data['available_refs']
            expect = next(ref for ref in refs if ref['path'].endswith('issue.md'))
            failure = next(ref for ref in refs if ref['path'].endswith('probe.jsonl'))
            result = {'classification':'REPRODUCED', 'reason':'empty input raises the reported IndexError in the target parser',
                'evidence_refs':[expect, failure], 'expected_assertion':True, 'target_triggered':True, 'failure_matches_issue':True}
        else:
            name = ('write_candidate', 'run_candidate', 'submit_candidate')[min(self.step, 2)]
            self.step += 1
            parameters = {'files':[{'path':'tests/test_repro.py', 'content':CANDIDATE, 'role':'test'}], 'hypothesis':'empty input'}
            result = {'name':name, 'parameters':parameters if name == 'write_candidate' else {'candidate_id':(data['candidate_ids'] or [''])[0]}}
        return ModelResponse(json.dumps(result))


def test_large_origin_run_reviews_with_bounded_citable_evidence(tmp_path, projects, facts):
    repo = large_project(tmp_path / 'large-review-repository')
    request = TaskRequest(repo, tmp_path / 'out', repo / 'issue.md',
        language=PythonPytestConfig(python=sys.executable, target_modules=('example',), candidate_parent='tests'))
    model = EvidenceCitingModel()
    controller = create_controller(request, ModelConfig(), gateway=model)
    fixed = large_project(tmp_path / 'large-review-fixed', FIXED)

    result = asyncio.run(controller.run(request, facts.context(), FixValidationRequest(fixed, sys.executable)))

    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.DIFFERENTIAL_VALIDATED
    # Every review request, including the replay, stayed inside the context budget.
    assert model.reviews and all(len(text.encode()) <= 32768 for _, text in model.reviews)
    payload, text = model.reviews[0]
    assert payload['observation']['target_origins']['count'] >= MODULE_COUNT
    assert payload['observation']['target_calls']['count'] >= MODULE_COUNT
    assert [segment['content'] for segment in payload['candidate']] == [CANDIDATE]
    # The full execution record keeps every mapping and its absolute paths, and the
    # summary describes exactly that mapping.
    runs = [controller.store.load_record('runs', path.parent.name)
            for path in (request.output_dir / 'runs').glob('*/execution.json')]
    original = next(run for run in runs if run.execution_role == 'original')
    assert len(original.observation.target_origins) >= MODULE_COUNT
    assert all(str(path).startswith(original.observation.framework_details['run_root']) for path in original.observation.target_origins.values())
    assert payload['observation']['target_origins']['count'] == len(original.observation.target_origins)
    assert payload['observation']['target_calls']['total'] == sum(original.observation.framework_details['target_calls'].values())
    assert original.observation.framework_details['run_root'] not in text

    # A cited source whose line range is outside the real file is rejected before the
    # reviewer is called, whatever the compact projection would have shown.
    candidate = controller.store.load_record('candidates', original.candidate_id)
    contract = controller.store.load_contract(original.contract_id, original.contract_version)
    class NeverCalled:
        def __init__(self): self.calls = 0
        async def complete(self, request, context):
            self.calls += 1
            raise AssertionError('a review must not be requested for out-of-range evidence')
    guard = NeverCalled()
    verifier = Verifier(controller.store, controller.runner.adapter, guard)
    out_of_range = replace(contract, sources=(replace(contract.sources[0], end_line=99999),))
    verdict = asyncio.run(verifier.evaluate(out_of_range, candidate, (original,), facts.context()))
    assert verdict.classification != CandidateClass.REPRODUCED and guard.calls == 0
