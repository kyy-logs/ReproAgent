from pathlib import Path

from reproagent.core.models import EvidenceRef, TestObservation
from reproagent.core.serialization import bytes_hash, parse_json


def _valid_assertion_display(payload):
    """Return the optional display proof only in its exact bounded shape.

    A probe is an untrusted file on disk like every other artifact.  An invalid
    optional field is ignored, not allowed to make the whole observation unusable.
    """
    display = payload.get('assertion_display')
    if display is None:
        return None
    if not isinstance(display, dict) or set(display) != {'provenance', 'object_ids'}:
        return None
    if display.get('provenance') != 'pytest_rewrite':
        return None
    ids = display.get('object_ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 64:
        return None
    if any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != len(ids):
        return None
    return {'provenance': 'pytest_rewrite', 'object_ids': list(ids)}


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
            if event == 'test_phase':
                if (type(payload.get('nodeid')) is not str or payload.get('when') not in ('setup','call','teardown')
                        or payload.get('outcome') not in ('passed','failed','skipped')):
                    raise ValueError('invalid test phase')
                display = _valid_assertion_display(payload)
                if display is None:
                    payload = {key: value for key, value in payload.items()
                               if key != 'assertion_display'}
                else:
                    payload = {**payload, 'assertion_display': display}
                item = {**item, 'payload': payload}
            if event == 'session_finish' and type(payload.get('exitstatus')) is not int:
                raise ValueError('invalid session exit status')
            events.append(item)
            if item['payload'].get('outcome') == 'failed':
                failures.append(EvidenceRef(str(path), digest, index + 1, index + 1))
        except (ValueError, UnicodeError) as exc:
            errors.append(str(exc))
            break
    complete = bool(events and not errors and events[0]['event'] == 'session_start' and events[-1]['event'] == 'session_finish')
    phases = tuple(e['payload'] for e in events if e['event'] == 'test_phase')
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
