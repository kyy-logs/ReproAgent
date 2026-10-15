"""A bounded model-facing projection of saved verification facts; no model calls."""
from copy import deepcopy
from dataclasses import asdict
from .budget import BudgetStopped
from .serialization import canonical_bytes

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


def render_verification_feedback(verdict, *, result_code, byte_limit, decision='continue',
                                 instruction='', secrets=(), extras=None):
    if type(byte_limit) is not int or byte_limit < 0 or decision not in ('continue','stop'):
        raise ValueError('invalid verification feedback capacity or decision')
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
    recovery = verdict.issues[0].recovery if verdict.issues else 'inspect_evidence'
    default_instruction = (ORIGINAL_PASS_GUIDANCE
        if verdict.issues and verdict.issues[0].code == 'CANDIDATE_NOT_REPRODUCED'
        else INSTRUCTIONS[recovery])
    payload = {'result_code':result_code, 'decision':decision, 'verdict':data,
        'instruction':instruction or default_instruction,
        'projection':{'omitted_issues':0,'omitted_refs':0,'omitted_details':0}, **extras}
    def redact(value):
        if isinstance(value,str):
            for secret in secrets:
                if secret: value=value.replace(secret,'[REDACTED]')
            return value
        if isinstance(value,dict): return {key:redact(item) for key,item in value.items()}
        if isinstance(value,(tuple,list)): return [redact(item) for item in value]
        return value
    payload=redact(payload)
    data=payload['verdict']; counts=payload['projection']
    def output():
        encoded=canonical_bytes(payload)
        return encoded.decode('utf-8') if len(encoded)<=byte_limit else None
    text=output()
    if text is not None: return text
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
