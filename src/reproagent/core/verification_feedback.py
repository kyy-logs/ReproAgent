"""A bounded model-facing projection of saved verification facts; no model calls."""
from copy import deepcopy
from dataclasses import asdict
from .budget import BudgetStopped
from .serialization import canonical_bytes
from .execution_log_evidence import log_payloads, omit_log_signal

INSTRUCTIONS = {
    'revise_candidate': '根据失败检查修改候选，再提交执行；不要只更换假设措辞。',
    'revise_contract': '查找原版依据后修订契约；缺少必要事实时请求信息。',
    'narrow_candidate': '缩小候选或报告执行障碍；只改假设不能解决执行超时或证据超限。',
    'request_information': '当前条件阻塞；报告缺失信息，不把未完成执行当作已复现。',
    'inspect_evidence': '检查列出的证据与缺口，再决定修订候选、契约或请求信息；未确认不等于已证明错误。',
}
# An observed passing run is a fact; metadata contamination is only a possibility to inspect.
ORIGINAL_PASS_GUIDANCE = ('候选在原版通过，尚未复现。检查断言是否被测试名称、说明或回显输入满足；'
                          '验证目标行为后再提交，不能只改假设。')
_EXTRA_KEYS = frozenset({'consecutive_command_timeouts','configured_command_timeout_seconds',
    'previous_candidate_id','previous_run_ids','prior_result_code'})
#: The largest one cited segment may be, in bytes.  A cited range is a few lines of a
#: failure report; a range that needs more than this is not the excerpt its reference
#: promised, and half of it would read as the whole.
EVIDENCE_SEGMENT_BYTES = 4096
_EVIDENCE_KEYS = frozenset({'path','content_hash','start_line','end_line','text'})


def evidence_excerpts(refs, *, read_ref, max_segment_bytes=EVIDENCE_SEGMENT_BYTES):
    """The cited lines of *refs*, read through the hash-checked *read_ref*, in order.

    A verdict names its evidence by path and line range, and the exploration phase reads
    the frozen snapshot only -- an execution path such as ``runs/<id>/probe.jsonl`` is a
    reference the model can never follow.  These excerpts are what makes the reference
    more than a promise: the same lines, carried in the feedback itself.

    A reference the reader refuses -- because the file changed, or is gone -- yields no
    excerpt rather than a wrong one, and neither does a segment over the ceiling.  The
    caller chooses the order, so the evidence a reader most needs to act on comes first
    and survives the shedding in :func:`render_verification_feedback`.
    """
    seen, excerpts = set(), []
    for ref in refs:
        key = (ref.path, ref.content_hash, ref.start_line, ref.end_line)
        if key in seen: continue
        seen.add(key)
        try:
            text = read_ref(ref)
        except (OSError, ValueError, UnicodeError):
            continue
        if not isinstance(text, str) or ref.start_line < 1 or ref.end_line < ref.start_line:
            continue
        segment = '\n'.join(text.splitlines()[ref.start_line - 1:ref.end_line])
        if len(segment.encode('utf-8')) > max_segment_bytes: continue
        excerpts.append({'path': ref.path, 'content_hash': ref.content_hash,
            'start_line': ref.start_line, 'end_line': ref.end_line, 'text': segment})
    return tuple(excerpts)


def _validated_evidence(evidence):
    """*evidence* as the closed shape the renderer accepts, or a refusal."""
    checked = []
    for item in evidence:
        if not isinstance(item, dict) or set(item) != _EVIDENCE_KEYS:
            raise ValueError('invalid verification evidence excerpt')
        if (not all(isinstance(item[key], str) for key in ('path','content_hash','text'))
                or type(item['start_line']) is not int or type(item['end_line']) is not int
                or item['start_line'] < 1 or item['end_line'] < item['start_line']):
            raise ValueError('invalid verification evidence excerpt')
        checked.append({key: item[key] for key in ('path','content_hash','start_line','end_line','text')})
    return tuple(checked)


def render_verification_feedback(verdict, *, result_code, byte_limit, decision='continue',
                                 instruction='', secrets=(), extras=None, evidence=(), log_evidence=()):
    if type(byte_limit) is not int or byte_limit < 0 or decision not in ('continue','stop'):
        raise ValueError('invalid verification feedback capacity or decision')
    evidence = _validated_evidence(evidence)
    extras = deepcopy(extras or {})
    if not set(extras) <= _EXTRA_KEYS:
        raise ValueError('unknown verification feedback extras')
    for key,value in extras.items():
        if key == 'consecutive_command_timeouts': ok = type(value) is int and value >= 0
        elif key == 'configured_command_timeout_seconds': ok = type(value) in (int,float) and value >= 0
        elif key == 'previous_run_ids': ok = isinstance(value,(tuple,list)) and all(type(v) is str for v in value)
        else: ok = type(value) is str
        if not ok: raise ValueError('invalid verification feedback extra')
    data = asdict(verdict)
    # Beside the references, not instead of them: the reference stays the provenance the
    # reader can check, and the text is what it says.  Absent when there is none, so a
    # feedback without cited evidence keeps exactly the shape it had before.
    if evidence:
        data['evidence_text'] = list(evidence)
    recovery = verdict.issues[0].recovery if verdict.issues else 'inspect_evidence'
    default_instruction = (ORIGINAL_PASS_GUIDANCE
        if verdict.issues and verdict.issues[0].code == 'CANDIDATE_NOT_REPRODUCED'
        else INSTRUCTIONS[recovery])
    payload = {'result_code':result_code, 'decision':decision, 'verdict':data,
        'instruction':instruction or default_instruction,
        'projection':{'omitted_issues':0,'omitted_refs':0,'omitted_details':0,'omitted_evidence':0}, **extras}
    def redact(value):
        if isinstance(value,str):
            for secret in secrets:
                if secret: value=value.replace(secret,'[REDACTED]')
            return value
        if isinstance(value,dict): return {key:redact(item) for key,item in value.items()}
        if isinstance(value,(tuple,list)): return [redact(item) for item in value]
        return value
    base_instruction = payload['instruction']
    if log_evidence:
        payload['execution_logs'] = log_payloads(log_evidence)
        payload['projection']['omitted_log_signals'] = 0
        payload['instruction'] = ('Inspect original execution log signals for input/import/build obstacles '
            'before tuning assertions; snippets alone do not prove the target was skipped. ' + payload['instruction'])
    payload=redact(payload)
    data=payload['verdict']; counts=payload['projection']
    def output():
        encoded=canonical_bytes(payload)
        return encoded.decode('utf-8') if len(encoded)<=byte_limit else None
    text=output()
    if text is not None: return text
    # Cited evidence is the largest optional payload, and the only one a reference can
    # stand in for, so it is shed first.  Whole segments only: half a failure report
    # reads as the whole one and nothing in the response would say otherwise.
    while data.get('evidence_text'):
        data['evidence_text'].pop(); counts['omitted_evidence']+=1
        text=output()
        if text is not None: return text
    data.pop('evidence_text', None)
    # Excerpts are program-made and explicitly marked; saved Verdict is untouched.
    for container,key in [(data,'reason'), *[(item,'message') for item in reversed(data['issues'])]]:
        if len(container[key])>96:
            container[key]=container[key][:96]+'…[省略]'
            counts['omitted_details']+=1
    text=output()
    if text is not None: return text
    for item in reversed(data['issues']):
        measurements=item['measurements']
        for key in list(measurements):
            if isinstance(measurements[key],(str,list,dict)):
                del measurements[key]; counts['omitted_details']+=1
                text=output()
                if text is not None: return text
    while omit_log_signal(payload.get('execution_logs', [])):
        counts['omitted_log_signals'] += 1
        text = output()
        if text is not None: return text
    if payload.get('execution_logs'):
        logs = payload.pop('execution_logs')
        payload['instruction'] = redact(base_instruction)
        counts.pop('omitted_log_signals', None)
        counts['omitted_details'] += max(1, sum(len(item['signals']) + item['omitted_signals'] for item in logs))
        text = output()
        if text is not None: return text
    for refs in [data['evidence_refs'], *[item['evidence_refs'] for item in reversed(data['issues'])]]:
        while refs:
            refs.pop(); counts['omitted_refs']+=1
            text=output()
            if text is not None: return text
    if data['uncertainties']:
        counts['omitted_details']+=len(data['uncertainties']); data['uncertainties']=[]
    while len(data['issues'])>1:
        data['issues'].pop(); counts['omitted_issues']+=1
        text=output()
        if text is not None: return text
    # Equivalent identity aliases do not need to be repeated in the minimum form.
    for key, equivalent in (('previous_candidate_id', data['candidate_id']),
                            ('previous_run_ids', data['run_ids'])):
        if key in payload and payload[key] == equivalent:
            del payload[key]; counts['omitted_details'] += 1
    # Replace long custom prose with a complete controlled action, never cut the verb.
    fallback_instruction = redact(default_instruction)
    if len(fallback_instruction.encode('utf-8')) < len(payload['instruction'].encode('utf-8')):
        payload['instruction'] = fallback_instruction; counts['omitted_details'] += 1
    for key in ('uncertainties', 'evidence_refs', 'execution_diagnostic'):
        if not data[key]:
            del data[key]; counts['omitted_details'] += 1
    # Keep the main code/check/origin/recovery, every failed check and exact binding.
    # These cannot silently disappear to make an unusable phase fit.
    if len(data['reason'])>32:
        data['reason']=data['reason'][:32]+'…[省略]'; counts['omitted_details']+=1
    if data['issues'] and len(data['issues'][0]['message'])>32:
        data['issues'][0]['message']=data['issues'][0]['message'][:32]+'…[省略]'; counts['omitted_details']+=1
    text=output()
    if text is not None: return text
    raise BudgetStopped('NEEDS_INFORMATION',
        f'verification feedback minimum envelope needs {len(canonical_bytes(payload))} bytes; budget is {byte_limit}; no main diagnostic or binding is silently dropped')
