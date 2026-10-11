"""Business facts copied for diagnostics, never instructions or evidence."""
from dataclasses import asdict
from .observability import mark, tracing_enabled

CONTRACT_FIELDS=("trigger","expected","reported_actual","observable_checks","sources",
                 "assumptions","missing_information")


def contract_snapshot(contract):
    return asdict(contract)


def contract_diff(before,after):
    old,new=contract_snapshot(before),contract_snapshot(after)
    changed=[k for k in CONTRACT_FIELDS if old[k]!=new[k]]
    return {"changed_fields":changed,"changes":{k:{"before":old[k],"after":new[k]} for k in changed}}


def budget_snapshot(budget):
    return dict(steps_used=budget.steps_used,steps_limit=budget.limits.agent_steps,
        steps_remaining=max(0,budget.limits.agent_steps-budget.steps_used),
        seconds_remaining=max(0.,budget.deadline-budget.clock()),cost_spent=str(budget.cost_spent),
        cost_limit=str(budget.limits.model_cost_limit) if budget.limits.model_cost_limit is not None else None,
        unknown_cost_calls=budget.unknown_cost_calls)


def decision_metadata(context=None,contract=None):
    if not tracing_enabled(): return {}
    try:
        from .otel_backend import current_sink
        sink=current_sink();phase=sink._explore_scope()
        attrs={'phase_id':phase if phase!=sink.root_span_id else None}
        if phase!=sink.root_span_id:
            attrs.update({k:sink._entries[phase]['attributes'].get(k) for k in ('contract_id','contract_version')})
        if context is not None:
            attrs.update(budget=budget_snapshot(context.budget),step_index=context.budget.steps_used)
        if contract is not None:
            attrs.update(contract_id=contract.contract_id,contract_version=contract.version)
        return attrs
    except Exception:
        from .otel_backend import current_sink
        sink=current_sink()
        if sink is not None: sink._fault()
        return {}


def observe_decision(name, *, attributes, content=None, context=None, contract=None):
    if not tracing_enabled():
        return
    try:
        from .otel_backend import current_sink
        sink=current_sink()
        phase=sink._explore_scope()
        attrs={**decision_metadata(context,contract),**attributes,
               'phase_id':phase if phase!=sink.root_span_id else None}
        if phase!=sink.root_span_id:
            for key in ('contract_id','contract_version'):
                if key not in attrs: attrs[key]=sink._entries[phase]['attributes'].get(key)
        handle=mark(name,attributes=attrs)
        if content is not None:
            handle.capture(name,content() if callable(content) else content,source="program_artifact")
    except Exception:
        sink=current_sink()
        if sink is not None: sink._fault()
