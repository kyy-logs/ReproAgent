"""Independent external results and local evidence retain separate meanings."""
import math
import re
from pathlib import Path

from .io import read_json,write_json,verify_seal,seal,file_hash
from reproagent.core.models import IDENTITY_FIELDS,NOT_RECORDED,component_identity

#: Run statuses that mean the runner started the case. A cancellation recorded before the
#: case started carries no duration and is not an execution.
EXECUTED_STATUSES=('DONE','EXHAUSTED','FAILED')


def percentile(values,fraction):
    if not values: return None
    values=sorted(values); location=(len(values)-1)*fraction
    low=int(location); high=min(low+1,len(values)-1)
    return values[low]+(values[high]-values[low])*(location-low)


def round_identity(round_data):
    """The round's component identity: what it recorded, over the product's own identity.

    The round record may carry these fields at its top level or inside its configuration;
    whatever it recorded wins, so a round that ran another backend keeps saying so.  A round
    that recorded no backend is marked as such rather than inheriting a product constant in
    the slot that reports what the round itself recorded.
    """
    recorded={}
    for source in (round_data.get('configuration'),round_data):
        if isinstance(source,dict):
            recorded.update({key:value for key,value in source.items() if key in IDENTITY_FIELDS and value})
    # The version the round ran on is what it recorded, never the one installed now: a round
    # summarised after an SDK upgrade must not be re-attributed to the newer build.
    for key in ('model_backend','agent_backend','agentscope_version'):
        recorded.setdefault(key,NOT_RECORDED)
    return component_identity(**recorded)


def recorded_label(value):
    """A recorded backend name, or a plain statement that the round recorded none."""
    return '未记录' if value == NOT_RECORDED else value


def summarize_round(round_data):
    outcomes=list(round_data['outcomes'].values()); total=len(outcomes)
    ready=[value for value in outcomes if value.get('preparation',{}).get('status')=='ready']
    verified=[value for value in outcomes if value.get('official_status')=='verified']
    official=sum(value.get('official_resolved') is True for value in verified)
    reviewed=[value for value in outcomes if type(value.get('human_judgement')) is bool]
    claims=[value for value in reviewed if value.get('status')=='DONE']
    durations=[value['duration'] for value in outcomes if value.get('status') not in ('EXHAUSTED','CANCELLED') and type(value.get('duration')) in (int,float) and math.isfinite(value['duration'])]
    published=[value for value in outcomes if value.get('status')=='DONE']
    ratio=lambda numerator,denominator:numerator/denominator if denominator else None
    # The fixed-version outcome is its own vocabulary: a repeated original failure is not a
    # differential success, and an outcome recorded before the field existed defaults to
    # "not_provided" rather than inheriting either claim.
    fix_status=lambda value:value.get('fix_validation_status','not_provided')
    # A case the runner actually started either reached a terminal run status or recorded a
    # duration; one cancelled before it started, never prepared or never run is not counted
    # as an execution. Independent delivery is the fresh-copy replay of a delivered package:
    # its own confirmed/failed/not-yet-run split stays separate from the local measurement.
    def executed(value): return value.get('status') in EXECUTED_STATUSES or (
        type(value.get('duration')) in (int,float) and math.isfinite(value['duration']))
    fully_graded=bool(total) and len(verified)==total
    summary={'all_tasks':total,'ready_tasks':len(ready),'preparation_rate':ratio(len(ready),total),
        'executed_tasks':sum(executed(value) for value in outcomes),
        # Rounds recorded before the source was pinned cannot claim a comparable version.
        'source_comparison_status':round_data.get('source_comparison_status','not_recorded'),
        'local_repeated':sum(value.get('evidence_level') in ('REPEATED_OBSERVATION','DIFFERENTIAL_VALIDATED') for value in outcomes),
        'local_differential':sum(value.get('evidence_level')=='DIFFERENTIAL_VALIDATED' for value in outcomes),
        'fix_validation_passed':sum(fix_status(value)=='passed' for value in outcomes),
        'fix_validation_failed':sum(fix_status(value)=='failed' for value in outcomes),
        'fix_validation_blocked':sum(fix_status(value)=='blocked' for value in outcomes),
        'fix_validation_not_provided':sum(fix_status(value)=='not_provided' for value in outcomes),
        'independent_delivered':sum(value.get('export_replayed') is True for value in outcomes),
        'independent_delivery_failed':sum(value.get('export_replayed') is False for value in outcomes),
        'independent_delivery_pending':sum(value.get('status')=='DONE' and value.get('export_replayed') is None for value in outcomes),
        'official_graded':len(verified),'official_successes':official,'official_not_verified':total-len(verified),
        # A verified official report is the only source of an official figure. While any
        # case is still unverified the state is "pending" and the official rate stays None:
        # it is never reported as a zero score, and the measured local numbers above remain
        # the visible measurement. `official_total_rate` keeps the documented provisional
        # lower bound over every case for readers who want it.
        'official_grade_status':'final' if fully_graded else 'pending',
        'official_rate':ratio(official,total) if fully_graded else None,
        'official_total_rate':ratio(official,total),'official_rate_final':fully_graded,
        'runnable_official_rate':ratio(sum(value.get('official_status')=='verified' and value.get('official_resolved') is True for value in ready),len(ready)),
        'human_reviewed':len(reviewed),'pending_human_review':total-len(reviewed),
        'human_confirmation_rate':ratio(sum(value['human_judgement'] is True for value in reviewed),len(reviewed)),
        'false_positive_rate':ratio(sum(value['human_judgement'] is False for value in claims),len(claims)),
        'export_replay_rate':ratio(sum(value.get('export_replayed') is True for value in published),len(published)),
        'http_attempts':sum(value.get('http_attempts',0) for value in outcomes),
        'total_tokens':sum(value.get('usage',{}).get('total_tokens',0) for value in outcomes),
        'costs_unknown_count':sum(value.get('cost_kind','unknown')=='unknown' for value in outcomes),
        'known_cost_subtotal':sum(value.get('known_cost_subtotal',0) for value in outcomes),
        'preparation_seconds':sum(value.get('preparation',{}).get('duration',0) for value in outcomes),
        'duration_p50':percentile(durations,.5),'duration_p90':percentile(durations,.9),
        'interrupted_tasks':sum(value.get('status') in ('EXHAUSTED','CANCELLED') for value in outcomes)}
    # What this round ran on: the product identity, with whatever the round recorded kept.
    summary.update(round_identity(round_data))
    return summary


def save_summary(root,round_data):
    root=Path(root); summary=summarize_round(round_data); write_json(root/'summary.json',summary)
    official_grade='待判分' if summary['official_grade_status']=='pending' else '已判分（最终）'
    lines=['# SWT-Bench 开发子集评测','',f"选定样本：{summary['all_tasks']}；准备可用：{summary['ready_tasks']}；实际执行：{summary['executed_tasks']}。",
        f"来源状态：{summary['source_comparison_status']}。",
        f"基础设施：{summary['infrastructure']}；复现策略：{summary['strategy']}（版本 {summary['strategy_version']}）。",
        f"本轮记录的后端：模型 {recorded_label(summary['model_backend'])}；Agent {recorded_label(summary['agent_backend'])}；AgentScope 版本：{recorded_label(summary['agentscope_version'])}。",
        f"本地重复确认：{summary['local_repeated']}；本地差分确认：{summary['local_differential']}。",
        f"独立交付确认：{summary['independent_delivered']}；交付重跑未通过：{summary['independent_delivery_failed']}；待独立重跑：{summary['independent_delivery_pending']}。",
        f"修复版对照：通过 {summary['fix_validation_passed']}；未通过 {summary['fix_validation_failed']}；受阻 {summary['fix_validation_blocked']}；未提供 {summary['fix_validation_not_provided']}。",
        '原版重复确认只说明报告的失败再次出现，修复版未通过不计作差分成功。',
        f"官方判分：{official_grade}；有来源的官方判分：{summary['official_graded']}；官方成功：{summary['official_successes']}；尚未核验：{summary['official_not_verified']}。",
        '官方报告缺失时保留待判分，本地测量数字仍照原样显示；本地 DONE 不能代替 SWT 判分。',
        f"人工已审查：{summary['human_reviewed']}；待审查：{summary['pending_human_review']}。",
        f"HTTP 尝试：{summary['http_attempts']}；记录 token：{summary['total_tokens']}；费用未知样本：{summary['costs_unknown_count']}。",
        '', '| 样本 | 准备 | 本地状态 | 证据 | 修复版 | 官方状态 |','| --- | --- | --- | --- | --- | --- |']
    def escaped(value): return str(value).replace('|','\\|').replace('\n',' ').replace('<','&lt;').replace('>','&gt;')
    for identity,outcome in round_data['outcomes'].items():
        lines.append('| '+' | '.join(escaped(value) for value in (identity,outcome.get('preparation',{}).get('status','pending'),
            outcome.get('status','NOT_RUN'),outcome.get('evidence_level','NONE'),
            outcome.get('fix_validation_status','not_provided'),outcome.get('official_status','not_run')))+' |')
    from reproagent.store import atomic_write
    atomic_write(root/'report.md',('\n'.join(lines)+'\n').encode())
    return summary


def import_reports(round_dir,reports_dir,receipt=None):
    round_dir=Path(round_dir).resolve(); data=read_json(round_dir/'round.json'); verify_seal(data,'round_hash')
    outcomes={key:dict(value) for key,value in data['outcomes'].items()}
    expected={key:data[key] for key in ('run_id','model_name','predictions_hash','harness_commit')}
    if receipt is not None:
        if any(receipt.get(key)!=value for key,value in expected.items()): raise ValueError('official receipt identity mismatch')
        if not (round_dir/'predictions.jsonl').is_file(): raise ValueError('prediction file missing')
        if file_hash(round_dir/'predictions.jsonl')!=data['predictions_hash']: raise ValueError('prediction bytes changed')
        if receipt.get('manifest_hash')!=data['manifest']['manifest_hash']: raise ValueError('official manifest identity mismatch')
        if receipt.get('dataset_snapshot_hash')!=data['manifest']['source']['snapshot_sha256'] or not re.fullmatch(r'[0-9a-f]{64}',receipt.get('harness_source_hash','')):
            raise ValueError('dataset or executed harness source identity missing')
        if receipt.get('executor')!='reproagent-swt-official-run-v1' or type(receipt.get('exit_code')) is not int or receipt['exit_code']!=0:
            raise ValueError('missing successful official execution receipt')
        if not isinstance(receipt.get('report_hashes'),dict): raise ValueError('missing report hashes')
    from .predictions import plain_child
    seen=set(); root=Path(reports_dir).resolve()
    current=plain_child(root,data['run_id']+'/'+data['model_name'])
    for report_path in current.rglob('report.json'):
        relative=report_path.relative_to(root).parts
        if len(relative)!=4 or relative[:2]!=(data['run_id'],data['model_name']): raise ValueError('report run/model path mismatch')
        plain_child(root,report_path.relative_to(root).as_posix())
        identity=relative[2]
        if identity not in outcomes or identity in seen: raise ValueError('unknown or duplicate official report')
        seen.add(identity); report=read_json(report_path)
        if not isinstance(report,dict) or set(report)!={identity} or type(report[identity].get('resolved')) is not bool:
            raise ValueError('invalid official report structure')
        digest=file_hash(report_path)
        if receipt is not None and receipt['report_hashes'].get(identity)!=digest: raise ValueError('official report hash mismatch')
        write_json(round_dir/'official'/identity/(digest+'.json'),report)
        outcomes[identity].update(official_status='verified' if receipt is not None else 'imported_unverified',
            official_resolved=report[identity]['resolved'],official_report_hash=digest)
    if receipt is not None and set(receipt['report_hashes'])!=seen: raise ValueError('receipt report set mismatch')
    if receipt is not None:
        for identity in set(outcomes)-seen:
            outcomes[identity].update(official_status='missing',official_resolved=None)
    updated=seal({**{key:value for key,value in data.items() if key!='round_hash'},'outcomes':outcomes},'round_hash')
    write_json(round_dir/'round.json',updated); save_summary(round_dir,updated)
    if receipt is not None: write_json(round_dir/'official-execution.receipt.json',receipt)
    return updated
