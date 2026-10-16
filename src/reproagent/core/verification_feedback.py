"""A bounded model-facing projection of saved verification facts; no model calls."""
import json
from copy import deepcopy
from dataclasses import asdict
from .budget import BudgetStopped
from .serialization import canonical_bytes
from .execution_feedback import valid_execution_observation

INSTRUCTIONS = {
    'revise_candidate': '根据失败检查修改候选，再提交执行；不要只更换假设措辞。',
    'revise_contract': '查找原版依据后修订契约；缺少必要事实时请求信息。',
    'narrow_candidate': '缩小候选或报告执行障碍；只改假设不能解决执行超时或证据超限。',
    'request_information': '当前条件阻塞；报告缺失信息，不把未完成执行当作已复现。',
    'inspect_evidence': '检查列出的证据与缺口，再决定修订候选、契约或请求信息；未确认不等于已证明错误。',
}
# An observed passing run is a fact; metadata contamination is only a possibility to inspect.
ORIGINAL_PASS_GUIDANCE = ('原版通过，可能是正常对照。复核失败分支与触发条件；'
                          '契约有误凭原版证据 revise_contract。排除名称、说明、回显输入污染；'
                          '勿靠无关断言造失败或只改假设。')
_EXTRA_KEYS = frozenset({'consecutive_command_timeouts','configured_command_timeout_seconds',
    'previous_candidate_id','previous_run_ids','prior_result_code','replay_unmet_checks','execution_observation'})


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
        elif key == 'execution_observation': ok = valid_execution_observation(value)
        elif key == 'replay_unmet_checks':
            ok = isinstance(value,(tuple,list)) and 1 <= len(value) <= 6 and all(
                type(v) is str and v in {'verdicts_reproduce_bound_candidate', 'independent_bound_integral_runs',
                    'preconditions_repeatable', 'test_inventory_matches', 'target_failure_present',
                    'failure_signature_matches', 'confirmation_unavailable'} for v in value)
        elif key == 'previous_run_ids': ok = isinstance(value,(tuple,list)) and all(type(v) is str for v in value)
        else: ok = type(value) is str
        if not ok: raise ValueError('invalid verification feedback extra')
    data = asdict(verdict)
    recovery = verdict.issues[0].recovery if verdict.issues else 'inspect_evidence'
    default_instruction = (ORIGINAL_PASS_GUIDANCE
        if verdict.issues and verdict.issues[0].code == 'CANDIDATE_NOT_REPRODUCED'
        else INSTRUCTIONS[recovery])
    minimum_instruction = default_instruction
    if any(item.measurements.get('setup_failures') for item in verdict.issues):
        default_instruction += (' setup_failures 是实际准备失败；先 Read 原版 fixture/API 定义与已有用法，'
            '修正缺失名称或调用，不要只改布局、假设或添加断言；准备失败不能证明目标 Bug。')
    if verdict.issues and verdict.issues[0].code == 'REVIEW_CONTEXT_TOO_LARGE':
        default_instruction += (' 查看 material_sizes 与 failure_sizes，通过 test_index 对照执行异常位置。'
            '避免把整页 HTML 或完整大对象作为断言消息；采用有依据的小范围行为检查，完整产物保留在文件中。')
    call_guidance = (' execution_observation 是程序观察，不是语义判决；message为最多512字符摘录。核对异常位置与目标触发路径；'
        '若涉及 API 参数或 fixture 用法，先 Read 原版签名/示例再修正，不能把任意异常当作目标复现。')
    payload = {'result_code':result_code, 'decision':decision, 'verdict':data,
        'instruction':(instruction or default_instruction) + (call_guidance if 'execution_observation' in extras else ''),
        'projection':{'omitted_issues':0,'omitted_refs':0,'omitted_details':0}, **extras}
    def redact(value):
        if isinstance(value,str):
            for secret in secrets:
                if secret: value=value.replace(secret,'[REDACTED]')
            return value
        if isinstance(value,dict):
            result, dropped = {}, 0
            for key,item in value.items():
                if key == 'passing_outputs' and isinstance(item,(list,tuple)):
                    rows = []
                    for encoded in item:
                        try:
                            row = json.loads(encoded)
                            if not isinstance(row,dict):
                                raise ValueError('invalid passing observation')
                        except (ValueError,TypeError):
                            dropped += 1
                            continue
                        clean = redact(row)
                        if clean != row:
                            clean['redacted'] = True
                        rows.append(json.dumps(clean,ensure_ascii=False))
                    result[key] = rows
                else:
                    result[key] = redact(item)
            if dropped:
                result['omitted_outputs'] = result.get('omitted_outputs',0) + dropped
            return result
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
    if 'execution_observation' in payload:
        observed = payload['execution_observation']
        while len(observed['failed_calls'])>1:
            observed['failed_calls'].pop()
            observed['omitted_failed_calls']+=1
            counts['omitted_details']+=1
            text=output()
            if text is not None:return text
        del payload['execution_observation']
        counts['omitted_details']+=1
        payload['instruction']=redact(instruction or default_instruction)
        text=output()
        if text is not None:return text
    for item in reversed(data['issues']):
        measurements=item['measurements']
        for key in list(measurements):
            if isinstance(measurements[key],(str,list,dict)):
                if key == 'passing_outputs':
                    measurements['omitted_outputs'] = measurements.get('omitted_outputs', 0) + len(measurements[key])
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
    fallback_instruction = redact(minimum_instruction)
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
