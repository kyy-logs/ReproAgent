"""Program-maintained current view; existing records remain the evidence authority."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from .core.serialization import canonical_bytes, parse_json, bytes_hash
from .paths import workspace_path
from .store import atomic_write, safe_child


def empty_state():
    return dict(schema_version=1, revision=0, task_id='unknown', goal='Reproduce the frozen issue',
                status='UNKNOWN', stage='unknown', contract=None, facts=[], hypotheses=[],
                latest_candidate=None, blockers=[], todos=[], materials=[], budgets={},
                completion=dict(accepted_candidate_id=None, evidence_level='NONE', verdict_ids=[]))


class TaskProgress:
    def __init__(self, store):
        self.store, self.last_error = store, ''
        self.data = empty_state()
        self._roles = {}
        # A projection is not a resume checkpoint. Rehydrate authoritative records only;
        # arbitrary STATE edits cannot create successful evidence or verified facts.
        for kind in ('task',):
            try:
                obj = store.load_record(kind, kind)
                self._record(kind, obj, None)
            except (OSError, ValueError, TypeError, KeyError):
                pass

    def _clean(self, value):
        if isinstance(value, str): return self.store.materials.redact(value)
        if isinstance(value, dict): return {k:self._clean(v) for k,v in value.items()}
        if isinstance(value, (tuple,list)): return [self._clean(v) for v in value]
        return value

    def _persist(self):
        self.data['revision'] += 1
        self.data['materials'] = self.store.materials.catalog()[-32:]
        self.data = self._clean(self.data)
        try:
            encoded = canonical_bytes(self.data)
            if len(encoded) > 65536: raise ValueError('state capacity')
            atomic_write(workspace_path(self.store.root/'STATE.json'), encoded)
            self.last_error = ''
        except (OSError, ValueError):
            self.last_error = 'state_store_error'

    def record(self, kind, obj, ref):
        if kind not in ('task','contracts','candidates','runs','verdicts'): return
        with self.store._lock:
            try:
                self._record(kind,obj,ref)
                self._persist()
            except (OSError,ValueError,TypeError,KeyError):
                self.last_error = 'state_projection_error'

    def _record(self, kind, obj, ref):
        state = self.data
        if kind == 'task':
            state.update(task_id=obj.task_id,status=obj.status.value,stage=obj.status.value.lower())
            state['completion'].update(accepted_candidate_id=obj.accepted_candidate_id or None,
                                      evidence_level=obj.evidence_level.value)
        elif kind == 'contracts':
            current = state['contract']
            changed = current is not None and (current['id'], current['version']) != (obj.contract_id,obj.version)
            if changed:
                state.update(facts=[],latest_candidate=None,blockers=[],todos=[])
                state['completion'] = empty_state()['completion']
            state['contract'] = dict(id=obj.contract_id,version=obj.version,
                path=ref.path if ref is not None else '',expected=obj.expected[:512],
                missing_information=list(obj.missing_information)[:8])
            state['goal'] = obj.trigger[:512] or state['goal']
        elif kind == 'candidates' and self._current(obj):
            state['latest_candidate'] = dict(id=obj.candidate_id,contract_version=obj.contract_version,
                                              run_ids=[],verdict_id=None,result='PUBLISHED')
        elif kind == 'runs':
            self._roles[obj.run_id] = obj.execution_role
            if obj.execution_role != 'original' or not self._current(obj): return
            raw, obs = obj.raw, obj.observation
            meta = self.store.materials.capture(canonical_bytes(asdict(obj)).decode('utf-8'),
                kind='execution_report',description='Original execution '+obj.run_id,upstream_complete=True)
            for label, source in (('stdout',raw.stdout_ref),('stderr',raw.stderr_ref)):
                self._register_log(source, label+' '+obj.run_id, not raw.log_truncated)
            fact = dict(run_id=obj.run_id,candidate_id=obj.candidate_id,contract_version=obj.contract_version,
                stop_reason=raw.stop_reason,exit_code=raw.exit_code,collected=obs.collected,executed=obs.executed,
                probe_complete=obs.probe_complete,evidence_ids=[meta['id']] if 'id' in meta else [],
                interpretation='Execution is incomplete; reproduction is undetermined' if raw.stop_reason!='EXITED'
                    or not obs.probe_complete else 'Execution completed; consult the independent verdict')
            state['facts'] = (state['facts'] + [fact])[-12:]
            state['latest_candidate'] = dict(id=obj.candidate_id,contract_version=obj.contract_version,
                run_ids=[obj.run_id],verdict_id=None,result=raw.stop_reason)
            if raw.stop_reason != 'EXITED' or not obs.probe_complete:
                state['blockers'] = [dict(code=raw.stop_reason if raw.stop_reason!='EXITED' else 'INCOMPLETE_PROBE',
                    description=fact['interpretation'],evidence_ids=fact['evidence_ids'])]
                state['todos'] = [dict(id='resolve-execution',title='Investigate the recorded execution obstacle',status='pending')]
        elif kind == 'verdicts' and self._current(obj):
            if any(self._roles.get(identifier) != 'original' for identifier in obj.run_ids): return
            meta=self.store.materials.capture(canonical_bytes(asdict(obj)).decode('utf-8'),
                kind='verdict',description='Independent verdict for '+obj.candidate_id,upstream_complete=True)
            code = str(obj.classification or 'UNDETERMINED')
            state['latest_candidate'] = dict(id=obj.candidate_id,contract_version=obj.contract_version,
                run_ids=list(obj.run_ids),verdict_id=meta.get('id'),result=code)
            state['completion']['verdict_ids'] = (state['completion']['verdict_ids']+[ref.path])[-4:]
            state['blockers'] = [] if code=='REPRODUCED' else [dict(code=code,description=obj.reason[:512],
                evidence_ids=[meta['id']] if 'id' in meta else [])]
            state['todos'] = [] if code=='REPRODUCED' else [dict(id='resolve-verdict',
                title='Address the current independent verification feedback',status='pending')]

    def _current(self, obj):
        current = self.data['contract']
        return current is not None and (current['id'],current['version'])==(obj.contract_id,obj.contract_version)

    def _register_log(self, ref, description, complete):
        try:
            path = safe_child(self.store.root, ref.path)
            if path.stat().st_size > 33554432: return
            raw = path.read_bytes()
            if bytes_hash(raw) != ref.content_hash: return
            self.store.materials.capture(raw.decode('utf-8',errors='replace'),kind='execution_log',
                                         description=description,upstream_complete=complete)
        except (OSError,ValueError):
            pass

    def event(self, kind, payload):
        if kind not in ('exploration.action','exploration.tool_error','exploration.protocol_failure','exploration.tool_result'): return
        code = payload.get('result_code')
        if code == 'TOOL_SUCCEEDED':
            if any(b.get('tool') == payload.get('action') for b in self.data['blockers']):
                self.data['blockers'] = []; self.data['todos'] = []; self._persist()
            return
        if not isinstance(code,str) or code in ('ALLOWED','BEGIN','ready'): return
        with self.store._lock:
            self.data['blockers'] = [dict(code=code[:80],tool=str(payload.get('action',''))[:80],
                description='Program-reported refusal or protocol error; consult the tool result')]
            self.data['todos'] = [dict(id='resolve-refusal',title='Address the current refusal before retrying',status='pending')]
            self._persist()

    def phase(self, phase_context):
        with self.store._lock:
            current = self.data['contract']
            contract = phase_context.contract
            if current is None or (current['id'],current['version'])!=(contract.contract_id,contract.version):
                self._record('contracts',contract,None)
            self.data['stage'] = 'exploring'
            self._persist()

    def snapshot(self, budget=None):
        with self.store._lock:
            if budget is not None:
                self.data['budgets']=dict(remaining_steps=max(0,budget.limits.agent_steps-budget.steps_used),
                    remaining_seconds=round(max(0,budget.deadline-budget.clock()),3),
                    cost_spent=str(budget.cost_spent),cost_limit=budget.limits.model_cost_limit,
                    unknown_cost_calls=budget.unknown_cost_calls)
                self._persist()
            return deepcopy(self.data)

    def view(self, max_bytes=4096):
        with self.store._lock:
            data=deepcopy(self.data)
            data['materials']=[{k:m[k] for k in ('id','kind','description','storage_complete','upstream_complete')}
                               for m in self.store.materials.catalog()[-3:]]
            data['material_count']=len(self.store.materials.catalog())
            data['omitted'] = []
            for key in ('hypotheses','facts','materials','todos'):
                while data[key] and len(canonical_bytes(data))>max_bytes:
                    data[key].pop(0)
                    if key not in data['omitted']: data['omitted'].append(key)
            if len(canonical_bytes(data))>max_bytes: raise ValueError('current task view exceeds context capacity')
            return data
