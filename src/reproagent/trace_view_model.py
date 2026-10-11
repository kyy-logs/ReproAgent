"""Read-only projection of retained observations; never an agent input."""
from __future__ import annotations

import json
import math


def number(value):
    """A measured non-negative finite number, or unknown."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _safe(value):
    # Iterative copy also tolerates deeply nested, hand-edited attributes.
    holder = [None]
    pending = [(holder, 0, value)]
    while pending:
        parent, key, item = pending.pop()
        if isinstance(item, dict):
            parent[key] = {}
            pending.extend((parent[key], str(k), v) for k, v in item.items())
        elif isinstance(item, list):
            parent[key] = [None] * len(item)
            pending.extend((parent[key], i, v) for i, v in enumerate(item))
        elif isinstance(item, float) and not math.isfinite(item):
            parent[key] = None
        else:
            parent[key] = item
    return holder[0]


def _tag(entry):
    name = str(entry.get("name", ""))
    if name == "task": return "ENTRY"
    if name.startswith("sdk.agent") or name in ("explore", "exploration"): return "AGENT"
    if name == "model.http_attempt": return "HTTP"
    if name.startswith("model.") or name.startswith("sdk.model"): return "LLM"
    if name.startswith("tool.") or name.startswith("sdk.tool") or name == "permission": return "TOOL"
    if name.startswith("verify") or name == "verdict" or "check" in entry.get("attributes", {}): return "VERIFY"
    if name.startswith("process"): return "PROCESS"
    return "OTHER"


DIAGNOSTIC_LABELS={'contract.established':'契约建立','contract.revised':'契约变更',
    'phase.started':'探索阶段开始','phase.completed':'探索阶段完成','permission':'工具权限',
    'publication.rejected':'候选发布被拒','citation.rejected':'引用校验被拒',
    'action.completed':'业务步骤完成','action.rejected':'业务步骤被拒',
    'budget.step_charged':'模型探索消耗一步','task.stopped':'任务停止','task.final':'最终任务结果'}

DIAGNOSTIC_EVENTS=frozenset(('contract.established','contract.revised','phase.started','phase.completed',
    'permission','publication.rejected','citation.rejected','action.completed','action.rejected',
    'budget.step_charged','task.stopped'))


def capture_health_view(document):
    health=document.get('capture_health')
    if isinstance(health,dict): return {**_safe(health),'legacy':False}
    issues=[]
    for r in document.get('contents',[]):
        if r.get('truncated') or r.get('availability') not in (None,'captured'):
            issues.append(dict(code='LEGACY_UNSPECIFIED',source=r.get('source'),span_id=r.get('owner_span_id'),
                               content_id=r.get('content_id'),count=1,severity='info'))
    if document.get('partial') and not issues:
        issues.append(dict(code='LEGACY_UNSPECIFIED',source=None,count=1,severity='warning'))
    return dict(legacy=True,structure_complete=None,counts_complete=document.get('metrics_complete'),
        content_complete=document.get('content_complete'),issues=issues[:128],issues_dropped=max(0,len(issues)-128))


def build_diagnostic_chain(document):
    spans=document.get('spans',[])
    indices={s['span_id']:i for i,s in enumerate(spans)}
    content_ids={}
    for record in document.get('contents',[]):
        content_ids.setdefault(record.get('content_id'),len(content_ids))
    rows=[]
    for key,s in enumerate(spans):
        attrs=s.get('attributes',{});name=s.get('name','')
        if name not in DIAGNOSTIC_EVENTS and not (name.startswith('tool.') and attrs.get('result_code')=='ERROR'):
            continue
        ancestry=[];walker=s;seen=set()
        while walker and walker['span_id'] not in seen:
            seen.add(walker['span_id']);ancestry.append(walker)
            walker=spans[indices[walker['parent_span_id']]] if walker.get('parent_span_id') in indices else None
        phase=attrs.get('phase_id') or next((a['span_id'] for a in ancestry if a['name']=='explore'),None)
        call=next((a.get('attributes',{}).get('tool_call_key') for a in ancestry
                   if a.get('attributes',{}).get('tool_call_key')),None)
        relation=('contract_transition' if name=='contract.revised' and attrs.get('previous_contract_version') is not None
                  else 'same_tool_call' if call else 'same_phase' if phase else 'sequence_only')
        rows.append(dict(kind=name,label=DIAGNOSTIC_LABELS.get(name,"工具执行返回 ERROR"),node_key=key,phase_id=phase,contract_version=attrs.get('contract_version'),
            previous_contract_version=attrs.get('previous_contract_version'),decision_code=attrs.get('decision_code'),
            result_code=attrs.get('result_code'),tool=attrs.get('tool'),tool_call_key=call,
            budget=_safe(attrs.get('budget')),budget_dimension=attrs.get('budget_dimension'),
            stop_reason=attrs.get('stop_reason'),content_keys=list(dict.fromkeys(content_ids[x] for x in
                s.get('content_refs',[]) if x in content_ids)),relation=relation,
            offset_seconds=number(s.get('offset_seconds'))))
    rows.sort(key=lambda r:(r['offset_seconds'] is None,r['offset_seconds'] or 0,r['node_key']))
    final=document.get('diagnostic_summary')
    if isinstance(final,dict):
        rows.append(dict(kind='task.final',label=DIAGNOSTIC_LABELS['task.final'],node_key=None,phase_id=None,contract_version=None,
            decision_code=None,result_code=final.get('status'),budget=_safe(final.get('budget')),
            budget_dimension=final.get('budget_dimension'),stop_reason=final.get('stop_reason'),
            content_keys=[],relation='sequence_only',offset_seconds=None))
    for row in rows:
        if not row['kind'].startswith('tool.') or not row.get('tool_call_key'): continue
        permissions=[p for p in rows if p['kind']=='permission' and
            p.get('tool_call_key')==row['tool_call_key'] and p.get('phase_id')==row.get('phase_id')]
        if len(permissions)==1:
            p=permissions[0]
            if row['contract_version'] is None: row['contract_version']=p['contract_version']
            if row['budget'] is None and p['budget'] is not None:
                row['budget']=p['budget'];row['budget_origin']='same_call_permission'
        reasons=[p for p in rows if p.get('decision_code') and p.get('tool_call_key')==row['tool_call_key']]
        row['label']+='（同调用有详细拒绝）' if reasons else '（精确程序原因未记录）'
    return _safe(rows)


def build_trace_view(document: dict) -> dict:
    from .trace_rendering import validate_trace
    validate_trace(document)
    spans = document.get("spans", [])
    records = document.get("contents", [])
    contents, content_map = [], {}
    for record in records:
        cid = record.get("content_id")
        if cid in content_map: continue
        key = len(contents); content_map[cid] = key
        descriptor = {field: _safe(record.get(field)) for field in
                      ("content_id", "source", "availability", "original_bytes", "captured_bytes",
                       "truncated", "redacted", "excerpt_mode", "reason_code", "limit_scope", "limit_value")}
        descriptor.update(key=key, container_id=f"trace-content-{key}")
        contents.append(descriptor)
    id_map = {s["span_id"]: i for i, s in enumerate(spans)}
    nodes = []
    for key, entry in enumerate(spans):
        attrs = _safe(entry.get("attributes", {}))
        parent = entry.get("parent_span_id")
        offset, duration = number(entry.get("offset_seconds")), number(entry.get("duration_seconds"))
        nodes.append(dict(key=key, span_id=entry["span_id"], actual_parent_span_id=parent,
                          parent_key=id_map.get(parent), child_keys=[], depth=0,
                          label=str(entry.get("name", "unknown")), kind_tag=_tag(entry),
                          display_status=str(entry.get("status", "unknown")),
                          otel_status=entry.get("otel_status"), attributes=attrs,
                          source=entry.get("instrumentation_scope", "unknown"),
                          content_keys=list(dict.fromkeys(content_map[r] for r in entry.get("content_refs") or [])),
                          offset_seconds=offset, duration_seconds=duration,
                          start_percent=None, width_percent=None,
                          missing_parent=parent is not None and parent not in id_map,
                          is_point=entry.get("kind") in ("point", "event"),
                          permission_result=attrs.get("result_code") if entry.get("name")=="permission" else None,
                          model=None))
    roots = []
    for n in nodes:
        if n["parent_key"] is None: roots.append(n["key"])
        else: nodes[n["parent_key"]]["child_keys"].append(n["key"])
    sort_key = lambda k: (nodes[k]["offset_seconds"] is None, nodes[k]["offset_seconds"] or 0, k)
    roots.sort(key=sort_key)
    for n in nodes: n["child_keys"].sort(key=sort_key)
    stack = [(k, 0) for k in roots]
    while stack:
        k, depth = stack.pop(); nodes[k]["depth"] = depth
        stack.extend((child, depth+1) for child in nodes[k]["child_keys"])
    # Share captures by the actual tool call identity, without rewriting topology.
    groups = {}
    for n in nodes:
        call = n["attributes"].get("tool_call_key")
        if isinstance(call, str) and call and n["kind_tag"] == "TOOL":
            groups.setdefault(call, []).append(n)
    for group in groups.values():
        refs = list(dict.fromkeys(k for n in group for k in n["content_keys"]))
        for n in group: n["content_keys"] = list(refs)
    http_by_content = {}
    for n in nodes:
        if n["kind_tag"] == "HTTP":
            for key in n["content_keys"]:
                http_by_content.setdefault(key, set()).add(n["key"])
    for record in records:
        if record.get("source") != "wire_request" or record.get("truncated") or record.get("excerpt_mode"):
            continue
        if record.get("availability") != "captured" or not isinstance(record.get("text"), str): continue
        try: model = json.loads(record["text"]).get("model")
        except (ValueError, AttributeError, RecursionError): continue
        if not isinstance(model, str): continue
        owners = set(http_by_content.get(content_map.get(record.get("content_id")), ()))
        owner = id_map.get(record.get("owner_span_id"))
        if owner is not None and nodes[owner]["kind_tag"] == "HTTP": owners.add(owner)
        # The store deduplicates identical requests: every actual citing branch counts.
        for k in owners:
            while k is not None:
                if nodes[k]["kind_tag"] in ("LLM", "HTTP"): nodes[k]["model"] = model
                k = nodes[k]["parent_key"]
    total = number(document.get("total_duration"))
    ends = [n["offset_seconds"] + (n["duration_seconds"] or 0) for n in nodes
            if n["offset_seconds"] is not None]
    ends = [v for v in ends if math.isfinite(v)]
    extent = max(([total] if total is not None else []) + ends, default=None)
    for n in nodes:
        if extent is not None and n["offset_seconds"] is not None:
            n["start_percent"] = min(100, n["offset_seconds"] / extent * 100) if extent else 0
            if n["duration_seconds"] is not None:
                n["width_percent"] = min(100-n["start_percent"], n["duration_seconds"] / extent * 100) if extent else 0
    summary = document.get("summary", {})
    usage, cost = summary.get("usage") or {}, summary.get("cost") or {}
    tin, tout = number(usage.get("input_tokens")), number(usage.get("output_tokens"))
    header = dict(status=document.get("status", "unknown"), trace_id=document.get("trace_id"),
                  task_id=document.get("task_id"), started_at=(document.get("started_at") if isinstance(document.get("started_at"), str)
                              else number(document.get("started_at"))),
                  main_duration=number(document.get("main_duration")), total_duration=total,
                  partial=bool(document.get("partial")), metrics_complete=document.get("metrics_complete", True),
                  content_complete=document.get("content_complete", True),
                  warnings=_safe(document.get("warnings", [])),
                  capture_health=capture_health_view(document),diagnostic_summary=_safe(document.get("diagnostic_summary")),
                  agent_replies=sum(n["label"]=="sdk.agent_reply" for n in nodes) if document["schema_version"]==2 else None,
                  logical_calls=number(summary.get("logical_calls")), http_attempts=number(summary.get("http_attempts")),
                  tool_executions=number(summary.get("tool_executions")), input_tokens=tin, output_tokens=tout,
                  total_tokens=tin+tout if usage.get("complete") is True and tin is not None and tout is not None else None,
                  token_label="总额" if usage.get("complete") is True else "已知小计",
                  cost=number(cost.get("amount")), cost_label="总额" if cost.get("complete") is True else "已知小计",
                  ttft=None, cache_hit_rate=None)
    hints = []
    slow = sorted((n for n in nodes if n["kind_tag"] in ("HTTP","TOOL","VERIFY","PROCESS") and
                   not n["is_point"] and n["duration_seconds"] is not None),
                  key=lambda n: (-n["duration_seconds"], n["key"]))[:3]
    for n in slow:
        hints.append(dict(kind="slow",node_key=n["key"],text=f"耗时 {n['label']}: {n['duration_seconds']:g}s"))
    attempts = {}
    for n in nodes:
        if n["kind_tag"] != "HTTP": continue
        k = n["parent_key"]
        while k is not None and nodes[k]["label"] != "model.logical": k=nodes[k]["parent_key"]
        if k is not None: attempts.setdefault(k, []).append(n["key"])
    for k, children in attempts.items():
        if len(children)>1: hints.append(dict(kind="retry",node_key=k,text=f"同一逻辑调用保留 {len(children)} 次 HTTP 尝试"))
    reserved = sum(n["permission_result"]=="RESERVED_FOR_PUBLISHING" for n in nodes)
    if reserved: hints.append(dict(kind="reserved",node_key=None,text=f"RESERVED_FOR_PUBLISHING: {reserved} 次发布预留"))
    for n in nodes:
        if n["display_status"].lower() in ("error","failed") or str(n["attributes"].get("result_code", "")).upper() in ("ERROR","FAIL","FAILED") or n["attributes"].get("health") in ("failed","blocked"):
            hints.append(dict(kind="failure",node_key=n["key"],text=f"失败位置: {n['label']}"))
    from .trace_business_steps import build_business_steps
    return dict(view_version=1,business_steps=build_business_steps(document,nodes),header=header,diagnostics=build_diagnostic_chain(document),nodes=nodes,roots=roots,contents=contents,hints=hints,
                time_axis=dict(extent_seconds=extent,label="总耗时" if total is not None else "已记录时间范围" if extent is not None else "无时间数据"))
