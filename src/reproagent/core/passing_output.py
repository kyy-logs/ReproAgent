"""Bound original passing observations for the next exploration phase."""
import json
from dataclasses import asdict
from .models import EvidenceRef
from ..store import safe_child


def _excerpt(stream, limit=1024):
    if not isinstance(stream, dict) or type(stream.get('text')) is not str:
        raise ValueError('invalid captured output text')
    text = stream['text']
    data = text.encode('utf-8')
    size, captured, truncated = stream.get('original_bytes'), stream.get('captured_bytes'), stream.get('truncated')
    if (type(size) is not int or size < 0 or type(captured) is not int or captured != len(data)
            or len(data) > 4096 or type(truncated) is not bool
            or stream.get('excerpt_mode') != ('head_tail' if truncated else 'complete')
            or (size <= captured if truncated else size != captured)):
        raise ValueError('invalid captured output metadata')
    cut = len(data) > limit
    if cut:
        gap = '\n[...feedback output omitted...]\n'
        half = (limit - len(gap.encode())) // 2
        text = data[:half].decode('utf-8', errors='ignore') + gap + data[-half:].decode('utf-8', errors='ignore')
    return {'text': text, 'original_bytes': stream['original_bytes'],
            'captured_bytes': len(text.encode('utf-8')),
            'truncated': bool(stream['truncated'] or cut),
            'excerpt_mode': 'head_tail' if stream['truncated'] or cut else 'complete'}


def passing_outputs(run, read_ref, store_root):
    """Return at most two hash-checked records; old/missing data stays unavailable.

    Captured text remains untrusted observation data, never expectation evidence.
    A fixed-version execution is deliberately excluded from exploration feedback.
    """
    if run.execution_role != 'original':
        return (), (), 0
    rows, refs, omitted = [], [], 0
    for phase in run.observation.tests:
        output = phase.get('captured_output')
        if phase.get('when') != 'call' or phase.get('outcome') != 'passed' or not isinstance(output, dict):
            continue
        try:
            if not any(output[key]['text'] for key in ('stdout', 'stderr')):
                continue
            if len(rows) >= 2:
                omitted += 1
                continue
            ref = EvidenceRef(**phase['captured_output_ref'])
            if (run.raw.probe_artifacts is None or safe_child(store_root, ref.path).resolve() != run.raw.probe_artifacts.path.resolve()
                    or ref.start_line != ref.end_line):
                raise ValueError('passing output is not from this run probe')
            record = json.loads(read_ref(ref).split('\n')[ref.start_line - 1])
            payload = record['payload']
            if (record.get('run_id') != run.run_id or record.get('event') != 'test_phase'
                    or str(payload.get('nodeid', '')).replace('\\', '/').split(run.run_id + '/code/', 1)[-1] != phase['nodeid'] or payload.get('when') != 'call'
                    or payload.get('outcome') != 'passed' or payload.get('captured_output') != output):
                raise ValueError('passing output binding differs')
            row = {'run_id': run.run_id, 'nodeid': phase['nodeid'], 'evidence_ref': asdict(ref),
                   'stdout': _excerpt(output['stdout']), 'stderr': _excerpt(output['stderr'])}
            rows.append(json.dumps(row, ensure_ascii=False))
            refs.append(ref)
        except (KeyError, TypeError, ValueError, IndexError, OSError, UnicodeError):
            omitted += 1
    return tuple(rows), tuple(refs), omitted
