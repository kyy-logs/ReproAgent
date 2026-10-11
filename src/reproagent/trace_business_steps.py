"""Business navigation over real span boundaries; never a lifecycle reconstruction."""
from __future__ import annotations

LABELS=('准备环境','建立契约','探索与生成候选','执行候选','验收','重跑确认','输出交付')
ANCHORS={'prepare':1,'analyze':2,'revise_contract':2,'explore':3,
         'run_candidate':4,'verify':5,'replay':6,'export':7}
FINAL_STATUSES=frozenset(('DONE','FAILED','BLOCKED','EXHAUSTED','CANCELLED','NEEDS_INFORMATION'))


def _code(value):
    return value if isinstance(value,str) else None


def build_business_steps(document, nodes):
    by_key={n['key']:n for n in nodes}
    ancestry={}
    for n in nodes:
        chain=[];seen=set();k=n.get('parent_key')
        while k in by_key and k not in seen:
            seen.add(k);chain.append(k);k=by_key[k].get('parent_key')
        ancestry[n['key']]=chain
    anchors=[]
    for n in nodes:
        if n['is_point']: continue
        name=n['label'];parents=[by_key[k]['label'] for k in ancestry[n['key']]]
        # The second verification and fixed-version preparation are parts of replay.
        if 'replay' in parents: continue
        step=ANCHORS.get(name);variant='契约修订' if name=='revise_contract' else ''
        if step is None and name=='runner.execute' and 'run_candidate' not in parents:
            step=6 if n['attributes'].get('execution_role')=='fixed' else 4
            variant='旧记录：修复版执行' if step==6 else '旧记录：执行轮次未知'
        if step is None and name=='runner.prepare' and 'prepare' not in parents:
            step=1;variant='旧记录：环境准备'
        if step is not None: anchors.append((n,step,variant))
    anchors.sort(key=lambda item:(item[0]['offset_seconds'] is None,item[0]['offset_seconds'] or 0,item[0]['key']))
    anchor_keys={n['key'] for n,_,_ in anchors}
    owned={key:[] for key in anchor_keys}
    for child in nodes:
        owner=next((k for k in ancestry[child['key']] if k in anchor_keys),None)
        if owner is not None: owned[owner].append(child)
    counters={};occurrences=[]
    for n,step,variant in anchors:
        related=owned[n['key']]
        a=n['attributes'];code=_code(a.get('result_code')) or _code(a.get('stop_reason'))
        version=a.get('contract_version');previous_version=None
        contract_events=[x for x in related if x['label'] in ('contract.established','contract.revised')]
        if step==2 and contract_events:
            fact=contract_events[-1]['attributes'];version=fact.get('contract_version')
            previous_version=fact.get('previous_contract_version')
        state=n['display_status'].lower()
        status='已取消' if state=='cancelled' else '执行中断' if state in ('error','failed') else '未完成' if state in ('incomplete','unknown') or n['duration_seconds'] is None else '已执行'
        if status=='已执行':
            if step==5:
                verdicts=[x['attributes'].get('result_code') for x in related if x['label']=='verdict']
                code=_code(verdicts[-1]) if verdicts else code
                status={'REPRODUCED':'通过','NOT_REPRODUCED':'未通过','INVALID_CANDIDATE':'未通过',
                        'ENVIRONMENT_BLOCKED':'环境阻塞','UNCLASSIFIED':'未确认'}.get(code,'未确认')
            elif n['label'] in ('replay','revise_contract'):
                purpose='submit_candidate' if step==6 else 'revise_contract'
                actions=[x for x in related if x['label'] in ('action.completed','action.rejected') and x['attributes'].get('purpose')==purpose]
                if actions:
                    last=actions[-1];code=_code(last['attributes'].get('result_code'))
                    status=('已确认' if step==6 else '已修订') if last['label']=='action.completed' and code=='OK' else '被拒绝' if last['label']=='action.rejected' else '未确认'
            elif step==2 and contract_events:
                status='已建立'
            elif step==3:
                points=[x for x in related if x['label']=='phase.completed']
                if points: code=_code(points[-1]['attributes'].get('result_code'))
        counters[step]=counters.get(step,0)+1
        occurrences.append(dict(step=step,label=LABELS[step-1],ordinal=counters[step],node_key=n['key'],
            status=status,result=code,variant=variant,offset_seconds=n['offset_seconds'],duration_seconds=n['duration_seconds'],
            candidate_id=a.get('candidate_id'),contract_version=version,previous_contract_version=previous_version))
    health=document.get('capture_health')
    coverage=(isinstance(health,dict) and health.get('structure_complete') is True
              and any(n['label']=='task' and n['attributes'].get('purpose')=='business_steps_v1' for n in nodes)
              and not any(n['missing_parent'] for n in nodes)
              and isinstance(document.get('status'),str) and document['status'] in FINAL_STATUSES)
    steps=[]
    for number,label in enumerate(LABELS,1):
        observed=[i for i,o in enumerate(occurrences) if o['step']==number]
        status=occurrences[observed[-1]]['status'] if observed else '未执行' if coverage else '未记录'
        steps.append(dict(number=number,label=label,status=status,count=len(observed),occurrence_keys=observed))
    return dict(steps=steps,occurrences=occurrences,coverage_complete=bool(coverage))
