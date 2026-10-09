import uuid
from dataclasses import replace
from pathlib import Path

from .adapters.languages.python_pytest.adapter import PythonPytestAdapter
from .adapters.runtimes.local import LocalBackend
from .core.models import ExecutionResult, ProbeResults, ProjectView, ProtectionCheck
from .observability import span
from .paths import directory_path, is_within, relative_name, workspace_path
from .store import atomic_write
from .core.serialization import bytes_hash, canonical_bytes


class Runner:
    def __init__(self, workspace, store, adapter=None, backend=None):
        self.workspace, self.store = workspace, store
        self.adapter = adapter or PythonPytestAdapter()
        self.backend = backend or LocalBackend()

    def relative_ref(self, ref):
        path = workspace_path(Path(ref.path).resolve())
        return replace(ref, path=relative_name(path, self.store.root))

    async def prepare(self, request, snapshot, context):
        with span('runner.prepare'):
            return await self._prepare(request, snapshot, context)

    async def _prepare(self, request, snapshot, context):
        context.budget.check()
        inspection = self.adapter.inspect(ProjectView(snapshot), request.language)
        root = workspace_path(self.store.root / 'probes' / uuid.uuid4().hex)
        directory_path(root).mkdir(parents=True)
        raw = await self.backend.execute(replace(inspection.probes[0], cwd=root), context)
        self.store.append_event('environment.probed', (self.relative_ref(raw.stdout_ref).path, self.relative_ref(raw.stderr_ref).path), {'exit_code':raw.exit_code, 'stop_reason':raw.stop_reason, 'cleanup_ok':raw.cleanup_ok})
        environment = self.adapter.describe_environment(inspection, ProbeResults((raw,)))
        if not request.language.source_roots and (snapshot.root / 'src').is_dir():
            environment = replace(environment, source_roots=('src', '.'))
        ref = self.store.save_record('environments', environment.environment_id, environment)
        self.store.append_event('environment.prepared', (ref.path,), {})
        if request.language.baseline_tests:
            run = self.workspace.fresh_run(snapshot, context=context)
            spec = replace(inspection.probes[0], spec_id='baseline-' + uuid.uuid4().hex, cwd=run.root,
                argv=(environment.python, '-m', 'pytest', *environment.pytest_args, *request.language.baseline_tests),
                env_overrides={'PYTHONPATH': __import__('os').pathsep.join(str(run.root if r == '.' else run.root / r) for r in environment.source_roots), 'PYTEST_ADDOPTS': ''}, probe_path=run.root.parent / 'baseline-unused.jsonl')
            baseline = await self.backend.execute(spec, context)
            self.store.append_event('baseline.observed', (self.relative_ref(baseline.stdout_ref).path,), {'exit_code': baseline.exit_code, 'cleanup_ok': baseline.cleanup_ok})
            if not baseline.cleanup_ok:
                raise RuntimeError('baseline process cleanup failed')
        return environment

    async def execute(self, candidate, snapshot, environment, context, execution_role='original'):
        with span('runner.execute', attributes={'execution_role': execution_role}) as handle:
            result = await self._execute(candidate, snapshot, environment, context, execution_role)
            # Which candidate ran, and under which role: an original run and a fixed-version
            # run are different claims and must not look alike in the trace.
            handle.annotate(run_id=result.run_id, candidate_id=result.candidate_id)
            return result

    async def _execute(self, candidate, snapshot, environment, context, execution_role='original'):
        if execution_role not in ('original', 'fixed'):
            raise ValueError('unknown execution role')
        if execution_role == 'original' and snapshot.snapshot_id != candidate.snapshot_id:
            raise ValueError('original snapshot/candidate mismatch')
        context.budget.check()
        run = self.workspace.fresh_run(snapshot, candidate, context)
        spec = replace(self.adapter.build_execution(candidate, run, environment), execution_role=execution_role)
        atomic_write(run.root.parent / 'spec.json', canonical_bytes(spec))
        raw = await self.backend.execute(spec, context)
        observation = self.adapter.normalize(raw, raw.probe_artifacts)
        protected = {entry.path:entry.content_hash for entry in snapshot.files}
        def origin_ok(path):
            path = Path(path).resolve()
            if not is_within(path, run.root): return False
            relative = relative_name(path, run.root)
            return relative in protected and bytes_hash(path.read_bytes()) == protected[relative]
        origins_ok = bool(observation.target_origins) and all(origin_ok(path) for path in observation.target_origins.values())
        details = dict(observation.framework_details, source_binding_ok=origins_ok, run_root=str(run.root), preconditions=list(environment.preconditions))
        normalize_node = lambda node: node.replace('\\', '/').split(run.run_id + '/code/', 1)[-1]
        details['collected_nodeids'] = [normalize_node(node) for node in details.get('collected_nodeids', [])]
        details['completed_nodeids'] = [normalize_node(node) for node in details.get('completed_nodeids', [])]
        details['exitstatus_matches_process'] = details.get('exitstatus') == raw.exit_code
        observation = replace(observation, tests=tuple(dict(t, nodeid=normalize_node(t['nodeid'])) for t in observation.tests), framework_details=details, failure_refs=tuple(self.relative_ref(r) for r in observation.failure_refs))
        raw = replace(raw, stdout_ref=self.relative_ref(raw.stdout_ref), stderr_ref=self.relative_ref(raw.stderr_ref))
        protection = self.workspace.check(run, snapshot, candidate, context) if raw.stop_reason == 'EXITED' and raw.cleanup_ok else ProtectionCheck(candidate_ok=False)
        result = ExecutionResult(run.run_id, spec.spec_id, snapshot.snapshot_id, environment.environment_id, candidate.candidate_id,
            candidate.manifest_hash, candidate.contract_id, candidate.contract_version, raw, observation, protection, execution_role)
        ref = self.store.save_record('runs', run.run_id, result)
        self.store.append_event('run.completed', (ref.path,), {'execution_role': execution_role})
        return result
