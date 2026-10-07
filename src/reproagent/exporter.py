import os
import json
import time
import uuid
from dataclasses import asdict
from importlib.resources import files

from .core.models import ArtifactManifest, EvidenceLevel, FileEntry, TaskState
from .core.budget import BudgetStopped
from .core.serialization import bytes_hash, canonical_bytes, canonical_hash, encode_record
from .paths import shared_path_form, workspace_path
from .store import atomic_write, safe_child
from .workspace import candidate_hash
from .reporting import render_report


class Exporter:
    def __init__(self, secrets=()):
        self.secrets = tuple(secret for secret in secrets if secret)

    def export(self, result, store, context=None):
        success = result.status in (TaskState.EXPORTING, TaskState.DONE) and result.evidence_level in (EvidenceLevel.REPEATED_OBSERVATION, EvidenceLevel.DIFFERENTIAL_VALIDATED) and bool(result.accepted_candidate_id)
        clock = context.budget.clock if context else time.monotonic
        deadline = clock() + (context.budget.limits.finalize_timeout_seconds if context else 5)
        if success and context:
            deadline = min(deadline, context.budget.deadline)
        def check():
            if clock() >= deadline:
                raise BudgetStopped('EXHAUSTED', 'local finalization deadline exceeded')
            if success and context:
                context.budget.check()
                if context.cancel_event.is_set(): raise BudgetStopped('CANCELLED')
        # One shared set of task-root spellings for bytes and for decoded records, longest
        # first: the native form, its forward-slash form and the JSON-escaped form.
        root_spellings = tuple(sorted({str(store.root), str(store.root).replace('\\', '/'),
            json.dumps(str(store.root))[1:-1]}, key=len, reverse=True))
        def read(path):
            chunks = []
            with path.open('rb') as stream:
                while True:
                    check()
                    chunk = stream.read(65536)
                    if not chunk: break
                    chunks.append(chunk)
            return b''.join(chunks)
        def redact(data):
            for secret in self.secrets:
                for value in sorted({secret, json.dumps(secret)[1:-1], json.dumps(secret, ensure_ascii=False)[1:-1]}, key=lambda value: (len(value), value), reverse=True):
                    data = data.replace(value.encode(), b'[REDACTED]')
            for prefix in root_spellings:
                data = data.replace(prefix.encode(), b'<task>')
            return data
        def redact_record(value):
            if isinstance(value, str):
                for secret in self.secrets:
                    value = value.replace(secret, '[REDACTED]')
                for prefix in root_spellings:
                    value = value.replace(prefix, '<task>')
                return value
            if isinstance(value, dict):
                return {key: redact_record(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact_record(item) for item in value]
            return value
        check()
        kind = 'reproduction' if success else 'diagnostic'
        # The build directory is renamed onto the final one, so both names keep
        # one representation even when only the longer build name needs it.
        root, temporary = shared_path_form(store.root / 'artifacts' / kind,
                                           store.root / 'artifacts' / ('.building-' + uuid.uuid4().hex))
        if root.exists():
            raise ValueError('export package already exists')
        temporary.mkdir(parents=True)
        events = store.read_events()[0]
        report = {'package_kind':kind, 'verified':bool(success), 'task_id':result.task_id, 'status':result.status.value,
            'stop_reason':result.stop_reason, 'evidence_level':result.evidence_level.value, 'uncertainties':list(result.uncertainties),
            # Carried through unchanged so the report states the fixed-version outcome next to
            # the evidence level instead of letting the repeated observation imply one.
            'fix_validation_status':result.fix_validation_status,
            'event_cutoff':len(events) - 1, 'candidate_files':[], 'log_mapping':[], 'runs':[], 'source_mapping':[], 'verdict_mapping':[],
            'accepted_run_ids':[], 'environment_probes':[]}
        previews = {}
        report['backends'] = next((dict(event.payload) for event in reversed(events) if event.kind == 'backend.selected'), {})
        issue_path = safe_child(store.root, 'input/issue.md')
        if issue_path.is_file():
            original = read(issue_path)
            exported = redact(original)
            path = 'sources/source-0000.txt'
            atomic_write(temporary / path, exported)
            report['source_mapping'].append({'source_path':'input/issue.md', 'source_hash':bytes_hash(original),
                'export_path':path, 'export_hash':bytes_hash(exported), 'start_line':1, 'end_line':max(1, len(original.splitlines()))})
            previews[path] = exported[:8000].decode('utf-8', errors='replace')[:2000]
        if success:
            candidate = store.load_record('candidates', result.accepted_candidate_id)
            if candidate_hash(candidate) != candidate.manifest_hash:
                raise ValueError('candidate manifest changed')
            if any(secret in value for value in (*[entry.path for entry in candidate.files], *candidate.selectors) for secret in self.secrets):
                raise ValueError('installation metadata contains a known secret; cannot export accepted paths')
            for entry in candidate.files:
                content = read(safe_child(candidate.storage_root, entry.path))
                if bytes_hash(content) != entry.content_hash:
                    raise ValueError('candidate file changed')
                if any(secret.encode() in content for secret in self.secrets):
                    raise ValueError('candidate contains a known secret; cannot export accepted bytes')
                if str(store.root).encode() in content or str(store.root).replace('\\', '/').encode() in content:
                    raise ValueError('candidate depends on task-local absolute path')
                atomic_write(safe_child(temporary, 'candidate/' + entry.path), content)
            report.update(candidate_id=candidate.candidate_id, candidate_manifest_hash=candidate.manifest_hash,
                candidate_files=[asdict(f) for f in candidate.files], selectors=list(candidate.selectors), preconditions=list(candidate.preconditions))
            snapshot = store.load_record('snapshots', candidate.snapshot_id)
            report['protected_snapshot'] = {'snapshot_id':snapshot.snapshot_id, 'manifest_hash':snapshot.manifest_hash, 'files':[asdict(entry) for entry in snapshot.files], 'excluded':list(snapshot.excluded)}
            report['fixture_hashes'] = [asdict(entry) for entry in snapshot.files if entry.path.endswith('conftest.py')]
            contract = store.load_contract(candidate.contract_id, candidate.contract_version)
            report['contract'] = encode_record(contract)
            if report['source_mapping'] and contract.description_hash and report['source_mapping'][0]['source_hash'] != contract.description_hash:
                raise ValueError('original issue evidence changed')
            for source in contract.sources:
                original = read(safe_child(store.root, source.path))
                if bytes_hash(original) != source.content_hash:
                    raise ValueError('contract source evidence changed')
                if any(item['source_path'] == source.path and item['source_hash'] == source.content_hash for item in report['source_mapping']):
                    continue
                exported = redact(original)
                export_path = f"sources/source-{len(report['source_mapping']):04d}.txt"
                atomic_write(safe_child(temporary, export_path), exported)
                if len(report['source_mapping']) < 4:
                    previews[export_path] = '\n'.join(exported.decode('utf-8', errors='replace').splitlines()[source.start_line - 1:source.end_line])[:2000]
                report['source_mapping'].append({'source_path':source.path, 'source_hash':source.content_hash, 'export_path':export_path, 'export_hash':bytes_hash(exported), 'start_line':source.start_line, 'end_line':source.end_line})
            verdicts = workspace_path(store.root / 'verdicts')
            for path in sorted(verdicts.glob('*.json')):
                check()
                verdict = store.load_record('verdicts', path.stem)
                if verdict.candidate_id != candidate.candidate_id or verdict.manifest_hash != candidate.manifest_hash or verdict.contract_version != candidate.contract_version:
                    continue
                original = read(path); exported = canonical_bytes(redact_record(json.loads(original)))
                export_path = f"verdicts/verdict-{len(report['verdict_mapping']):04d}.json"
                atomic_write(temporary / export_path, exported)
                if len(report['verdict_mapping']) < 2:
                    summary = f'{verdict.classification.value}: {verdict.reason}' if verdict.classification else verdict.reason
                    previews[export_path] = redact(summary.encode()).decode('utf-8', errors='replace')[:2000]
                report['verdict_mapping'].append({'source_path':f'verdicts/{path.relative_to(verdicts).as_posix()}', 'source_hash':bytes_hash(original), 'export_path':export_path, 'export_hash':bytes_hash(exported)})
            atomic_write(temporary / 'probe/reproagent_pytest_probe.py', files('reproagent').joinpath('adapters/languages/python_pytest/probe/reproagent_pytest_probe.py').read_bytes())
            atomic_write(temporary / 'replay.py', files('reproagent').joinpath('resources/replay.py').read_bytes())
        runs = workspace_path(store.root / 'runs')
        for path in sorted(runs.glob('*/execution.json')):
            check()
            run = store.load_record('runs', path.parent.name)
            if success and run.candidate_id == candidate.candidate_id and run.manifest_hash == candidate.manifest_hash and run.contract_version == candidate.contract_version and run.execution_role == 'original':
                report['accepted_run_ids'].append(run.run_id)
            report['runs'].append({'run_id':run.run_id, 'role':run.execution_role, 'exit_code':run.raw.exit_code, 'cleanup_ok':run.raw.cleanup_ok,
                'candidate_id':run.candidate_id, 'snapshot_id':run.snapshot_id, 'failure_refs':[asdict(r) for r in run.observation.failure_refs]})
            for ref in (run.raw.stdout_ref, run.raw.stderr_ref, *run.observation.failure_refs):
                if any(m['source_path'] == ref.path for m in report['log_mapping']):
                    continue
                original = read(safe_child(store.root, ref.path))
                if bytes_hash(original) != ref.content_hash:
                    raise ValueError('original evidence changed')
                exported = redact(original)
                export_path = f"evidence/log-{len(report['log_mapping']):04d}.txt"
                atomic_write(safe_child(temporary, export_path), exported)
                if ref.path.endswith('/stdout.log') and (not success or run.candidate_id == result.accepted_candidate_id) and len([key for key in previews if key.startswith('evidence/')]) < 2:
                    previews[export_path] = exported[:8000].decode('utf-8', errors='replace')[:2000]
                report['log_mapping'].append({'source_path':ref.path, 'source_hash':ref.content_hash, 'export_path':export_path, 'export_hash':bytes_hash(exported)})
            if success and run.candidate_id == result.accepted_candidate_id and run.execution_role == 'original':
                env = store.load_record('environments', run.environment_id)
                report.update(python=env.python, pytest_version=env.tool_version, source_roots=list(env.source_roots), target_modules=list(env.target_modules),
                    pytest_args=list(env.pytest_args), limitations=list(env.limitations),
                    argv=['<python>', '-m', 'pytest', *env.pytest_args, '-p', 'reproagent_pytest_probe', *candidate.selectors], env_names=['PYTHONPATH', 'REPROAGENT_RUN_ID', 'REPROAGENT_PROBE_PATH', 'REPROAGENT_TARGET_MODULES'])
        probes = workspace_path(store.root / 'probes')
        for path in sorted(probes.glob('*/*.log')):
            original = read(path); exported = redact(original)
            relative = f'probes/{path.relative_to(probes).as_posix()}'
            export_path = f"evidence/log-{len(report['log_mapping']):04d}.txt"
            atomic_write(safe_child(temporary, export_path), exported)
            report['environment_probes'].append(export_path)
            report['log_mapping'].append({'source_path':relative, 'source_hash':bytes_hash(original), 'export_path':export_path, 'export_hash':bytes_hash(exported)})
        if success and 'source_roots' not in report:
            raise ValueError('accepted candidate has no recorded environment/run')
        if success and any(secret in value for value in (*report['source_roots'], *report['target_modules'], *report['pytest_args']) for secret in self.secrets):
            raise ValueError('installation metadata contains a known secret; cannot export accepted environment')
        report_safe = redact_record(report)
        report_text = canonical_bytes(report_safe)
        atomic_write(temporary / 'report.json', report_text)
        markdown = render_report(report_safe, previews, check=check)
        atomic_write(temporary / 'report.md', redact(markdown.encode('utf-8')))
        check()
        entries = tuple(FileEntry(p.relative_to(temporary).as_posix(), bytes_hash(read(p)), p.stat().st_size, 'artifact') for p in sorted(temporary.rglob('*')) if p.is_file())
        digest = canonical_hash({'package_kind':kind, 'files':entries, 'candidate_id':result.accepted_candidate_id, 'event_cutoff':report['event_cutoff']})
        manifest = ArtifactManifest(kind, root, digest, entries, result.accepted_candidate_id, report['event_cutoff'])
        payload = encode_record(manifest)
        payload['root'] = '.'
        atomic_write(temporary / 'manifest.json', canonical_bytes(payload))
        for entry in entries:
            if bytes_hash(read(safe_child(temporary, entry.path))) != entry.content_hash:
                raise ValueError('export integrity failed')
        check()
        os.replace(temporary, root)
        return manifest
