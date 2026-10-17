from pathlib import Path

from reproagent.core.models import EvidenceRef, TestObservation
from reproagent.core.serialization import bytes_hash, parse_json


def read_probe(path: Path, expected_run_id: str, max_bytes: int = 33554432) -> TestObservation:
    if not path.exists():
        return TestObservation(framework_details={'errors': ['probe missing']})
    if path.stat().st_size > max_bytes:
        return TestObservation(framework_details={'errors':['probe output limit exceeded']})
    with path.open('rb') as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        return TestObservation(framework_details={'errors':['probe output limit exceeded']})
    digest = bytes_hash(data)
    events, errors, failures = [], [], []
    for index, line in enumerate(data.splitlines(keepends=True)):
        try:
            if not line.endswith(b'\n'):
                raise ValueError('incomplete probe line')
            item = parse_json(line.decode('utf-8'))
            if item.get('probe_version') != 1 or type(item.get('probe_version')) is not int or item.get('run_id') != expected_run_id:
                raise ValueError('probe identity/version mismatch')
            if type(item.get('seq')) is not int or item['seq'] != index or not isinstance(item.get('payload'), dict):
                raise ValueError('probe sequence/payload mismatch')
            if events and events[-1]['event'] == 'session_finish':
                raise ValueError('events after finish')
            event, payload = item['event'], item['payload']
            if event not in ('session_start', 'collection', 'collected', 'deselected', 'test_phase', 'session_finish'):
                raise ValueError('unknown probe event')
            if event in ('collected', 'deselected') and (not isinstance(payload.get('nodeids'), list) or any(type(n) is not str for n in payload['nodeids'])):
                raise ValueError('invalid collected node list')
            if event == 'test_phase' and (type(payload.get('nodeid')) is not str or payload.get('when') not in ('setup','call','teardown') or payload.get('outcome') not in ('passed','failed','skipped')):
                raise ValueError('invalid test phase')
            if event == 'test_phase' and 'assertion_context_object_ids' in payload:
                identities = payload['assertion_context_object_ids']
                if (not isinstance(identities,list) or len(identities) > 64
                        or any(type(identity) is not int or identity <= 0 for identity in identities)):
                    raise ValueError('invalid assertion context identities')
            if event == 'test_phase' and 'sphinx_inputs' in payload:
                # The observation decides whether a candidate's document was ever read, so
                # its shape is checked here like every other record the probe emits.
                value = payload['sphinx_inputs']
                if (not isinstance(value, dict) or set(value) != {'testroot','source_suffix','created','unread'}
                        or type(value['testroot']) is not str
                        or any(not isinstance(value[key], list) or any(type(name) is not str for name in value[key])
                               for key in ('source_suffix','created','unread'))):
                    raise ValueError('invalid sphinx input observation')
            if event == 'test_phase' and 'target_functions' in payload:
                value = payload['target_functions']
                # The probe bounds this at 256 (a whole package is a target, and each
                # module gets its own share); the record has to accept what it can emit.
                if (not isinstance(value, dict) or len(value) > 256
                        or any(type(key) is not str or type(count) is not int or count < 0
                               for key, count in value.items())):
                    raise ValueError('invalid target function observation')
            if event == 'test_phase' and 'diagnostics' in payload:
                value = payload['diagnostics']
                if (not isinstance(value, list) or len(value) > 64
                        or any(not isinstance(row, dict) or set(row) != {'message','path','lineno','level'}
                               or any(type(row[key]) is not str for key in ('message','path','level'))
                               or type(row['lineno']) is not int or row['lineno'] < 0 for row in value)):
                    raise ValueError('invalid diagnostic observation')
            if event == 'test_phase' and 'monitoring' in payload:
                value = payload['monitoring']
                allowed = {'captured', 'not_configured', 'incomplete'}
                if (not isinstance(value, dict) or set(value) != {'inputs','functions','diagnostics'}
                        or any(not isinstance(value[key], str) or value[key] not in allowed for key in value)):
                    raise ValueError('invalid monitoring status')
            if event == 'session_finish' and type(payload.get('exitstatus')) is not int:
                raise ValueError('invalid session exit status')
            events.append(item)
            if item['payload'].get('outcome') == 'failed':
                failures.append(EvidenceRef(str(path), digest, index + 1, index + 1))
        except (ValueError, UnicodeError) as exc:
            errors.append(str(exc))
            break
    complete = bool(events and not errors and events[0]['event'] == 'session_start' and events[-1]['event'] == 'session_finish')
    phases = tuple({**e['payload'], 'captured_output_ref': {
        'path': str(path), 'content_hash': digest, 'start_line': e['seq'] + 1, 'end_line': e['seq'] + 1}}
        if 'captured_output' in e['payload'] else e['payload']
        for e in events if e['event'] == 'test_phase')
    lists = [e['payload']['nodeids'] for e in events if e['event'] == 'collected']
    nodes = lists[0] if lists else []
    completed = []
    if complete:
        if len(lists) != 1 or len(set(nodes)) != len(nodes):
            errors.append('missing/duplicate collected nodes')
        if any(t['nodeid'] not in nodes for t in phases):
            errors.append('phase refers to uncollected node')
        for node in nodes:
            records = [t for t in phases if t['nodeid'] == node]
            names = [t['when'] for t in records]
            expected = ['setup','call','teardown'] if records and records[0]['when'] == 'setup' and records[0]['outcome'] == 'passed' else ['setup','teardown']
            if names != expected:
                errors.append('incomplete phases for ' + node)
            else:
                completed.append(node)
        complete = not errors
    collected = bool(nodes)
    finish = events[-1]['payload'] if complete else {}
    details = {'errors': errors, 'collection_failures': [e['payload'] for e in events if e['event'] == 'collection' and e['payload'].get('outcome') == 'failed'],
               'target_calls': finish.get('target_calls', {}), 'exitstatus': finish.get('exitstatus'), 'collected_nodeids':nodes,
               'completed_nodeids':completed, 'deselected_nodeids':[node for e in events if e['event'] == 'deselected' for node in e['payload']['nodeids']]}
    return TestObservation(bool(collected), any(t.get('when') == 'call' and t.get('outcome') != 'skipped' for t in phases), complete,
        phases, finish.get('target_origins', {}), tuple(failures), details)
