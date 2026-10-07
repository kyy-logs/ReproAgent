import pytest


def test_summary_keeps_denominator_unknown_cost_and_pending_judgement():
    from evals.swt_bench.results import summarize_round
    round_data={'outcomes':{
        'a':{'status':'DONE','preparation':{'status':'ready'},'evidence_level':'DIFFERENTIAL_VALIDATED','official_status':'verified',
            'official_resolved':True,'human_judgement':None,'export_replayed':True,'cost_kind':'unknown','duration':5,'http_attempts':3,'usage':{'total_tokens':50}},
        'b':{'status':'BLOCKED','preparation':{'status':'blocked'},'official_status':'not_run','official_resolved':None,'duration':None}}}
    summary=summarize_round(round_data)
    assert summary['all_tasks']==2 and summary['ready_tasks']==1 and summary['official_successes']==1
    assert summary['official_total_rate']==.5 and summary['official_rate_final'] is False
    assert summary['human_confirmation_rate'] is None and summary['pending_human_review']==2
    assert summary['costs_unknown_count']==2 and summary['http_attempts']==3 and summary['total_tokens']==50
    assert summary['duration_p50']==5


def test_round_summary_keeps_repeated_observation_and_failed_fix_separate():
    from evals.swt_bench.results import summarize_round
    # A failed fixed-version check must not be summarised like a passing one, and a round
    # recorded before the field existed keeps its outcome as "no fixed version provided".
    round_data={'outcomes':{
        'pallets__flask-4992':{'status':'DONE','evidence_level':'REPEATED_OBSERVATION','fix_validation_status':'failed',
            'human_judgement':None,'export_replayed':None},
        'sphinx-doc__sphinx-8801':{'status':'DONE','evidence_level':'DIFFERENTIAL_VALIDATED','fix_validation_status':'passed'},
        'old-round':{'status':'DONE','evidence_level':'REPEATED_OBSERVATION'},
        'no-fix':{'status':'DONE','evidence_level':'REPEATED_OBSERVATION','fix_validation_status':'not_provided'}}}
    summary=summarize_round(round_data)
    assert summary['local_repeated']==4 and summary['local_differential']==1
    assert summary['fix_validation_passed']==1 and summary['fix_validation_failed']==1
    assert summary['fix_validation_not_provided']==2 and summary['fix_validation_blocked']==0


def test_round_summary_states_the_component_identity_without_relabelling_a_round(tmp_path):
    from evals.swt_bench.results import save_summary,summarize_round
    # What a round actually ran on is kept; the product identity is stated next to it and
    # never replaces a recorded backend.
    round_data={'configuration':{'model_backend':'native','agent_backend':'native'},
        'outcomes':{'a':{'status':'DONE','evidence_level':'REPEATED_OBSERVATION','fix_validation_status':'failed'}}}
    summary=summarize_round(round_data)
    assert summary['infrastructure']=='agentscope' and summary['strategy']=='reproagent'
    assert summary['strategy_version']=='1' and isinstance(summary['agentscope_version'],str)
    assert summary['model_backend']=='native' and summary['agent_backend']=='native'
    # A round recorded before any of this existed still summarises, and its outcome keeps
    # the truthful "no fixed version provided" default.
    legacy_data={'outcomes':{'old':{'status':'DONE','evidence_level':'REPEATED_OBSERVATION'}}}
    legacy=summarize_round(legacy_data)
    assert legacy['fix_validation_not_provided']==1 and legacy['local_differential']==0
    assert legacy['infrastructure']=='agentscope' and legacy['strategy_version']=='1'
    # A round that recorded no backend may not inherit a product constant in the slot that
    # reports what the round itself recorded.
    assert legacy['model_backend']=='not_recorded' and legacy['agent_backend']=='not_recorded'
    save_summary(tmp_path,round_data)
    text=(tmp_path/'report.md').read_text(encoding='utf-8')
    assert '基础设施：agentscope' in text and '复现策略：reproagent' in text
    assert '本轮记录的后端：模型 native；Agent native' in text
    save_summary(tmp_path/'legacy',legacy_data)
    text=(tmp_path/'legacy'/'report.md').read_text(encoding='utf-8')
    assert '本轮记录的后端：模型 未记录；Agent 未记录' in text
    assert 'agentscope' in text and '本轮记录的后端：模型 agentscope' not in text


def test_unverified_external_report_is_not_counted_as_independent_success():
    from evals.swt_bench.results import summarize_round
    summary=summarize_round({'outcomes':{'a':{'status':'DONE','official_status':'imported_unverified','official_resolved':True}}})
    assert summary['official_successes']==0 and summary['official_not_verified']==1


def test_malformed_or_mixed_external_reports_are_rejected(tmp_path):
    from evals.swt_bench.results import import_reports
    from evals.swt_bench.io import write_json,seal
    root=tmp_path/'round'; root.mkdir()
    data=seal({'run_id':'r','model_name':'model','harness_commit':'a'*40,'predictions_hash':'h',
        'outcomes':{'known':{'status':'DONE','official_status':'not_run'}}},'round_hash')
    write_json(root/'round.json',data)
    reports=tmp_path/'reports'/'r'/'model'/'unknown'; reports.mkdir(parents=True)
    write_json(reports/'report.json',{'unknown':{'resolved':True}})
    with pytest.raises(ValueError): import_reports(root,tmp_path/'reports')
    reports.rename(reports.parent/'known')
    write_json(reports.parent/'known'/'report.json',{'known':{'resolved':'yes'}})
    with pytest.raises(ValueError): import_reports(root,tmp_path/'reports')


def test_external_results_require_matching_run_and_prediction_identity(tmp_path):
    from evals.swt_bench.results import import_reports
    from evals.swt_bench.io import write_json,seal,read_json
    root=tmp_path/'round'; root.mkdir()
    data=seal({'run_id':'r','model_name':'model','harness_commit':'a'*40,'predictions_hash':'h',
        'outcomes':{'known':{'status':'DONE','official_status':'not_run'}}},'round_hash')
    write_json(root/'round.json',data)
    reports=tmp_path/'reports'/'r'/'model'/'known'; reports.mkdir(parents=True)
    write_json(reports/'report.json',{'known':{'resolved':True,'patch_successfully_applied':True}})
    result=import_reports(root,tmp_path/'reports')
    assert result['outcomes']['known']['official_status']=='imported_unverified'
    receipt={'run_id':'other','predictions_hash':'h','harness_commit':'a'*40,'model_name':'model'}
    with pytest.raises(ValueError): import_reports(root,tmp_path/'reports',receipt=receipt)


def test_import_ignores_reports_from_previous_rounds(tmp_path):
    from evals.swt_bench.results import import_reports
    from evals.swt_bench.io import write_json,seal
    root=tmp_path/'round'; root.mkdir()
    write_json(root/'round.json',seal({'run_id':'r','model_name':'model','harness_commit':'a'*40,'predictions_hash':'h',
        'outcomes':{'known':{'status':'DONE','official_status':'not_run'}}},'round_hash'))
    reports=tmp_path/'reports'
    write_json(reports/'previous'/'other-model'/'unrelated'/'report.json',{'unrelated':{'resolved':False}})
    write_json(reports/'r'/'model'/'known'/'report.json',{'known':{'resolved':True}})
    assert import_reports(root,reports)['outcomes']['known']['official_resolved'] is True


def test_verified_import_binds_actual_predictions_and_manifest(tmp_path):
    from evals.swt_bench.results import import_reports
    from evals.swt_bench.io import write_json,seal,file_hash
    root=tmp_path/'round'; root.mkdir(); (root/'predictions.jsonl').write_text('original predictions\n')
    report=tmp_path/'reports/r/model/known/report.json'; write_json(report,{'known':{'resolved':True}})
    data=seal({'run_id':'r','model_name':'model','harness_commit':'a'*40,'predictions_hash':file_hash(root/'predictions.jsonl'),
        'manifest':{'manifest_hash':'selected','source':{'snapshot_sha256':'d'*64}},'outcomes':{'known':{'status':'DONE','official_status':'not_run'}}},'round_hash')
    write_json(root/'round.json',data)
    receipt={'run_id':'r','model_name':'model','harness_commit':'a'*40,'predictions_hash':data['predictions_hash'],
        'executor':'reproagent-swt-official-run-v1','exit_code':0,'manifest_hash':'selected','report_hashes':{'known':file_hash(report)},
        'harness_source_hash':'a'*64,'dataset_snapshot_hash':'d'*64}
    result=import_reports(root,tmp_path/'reports',receipt=receipt)
    assert result['outcomes']['known']['official_status']=='verified'
    with pytest.raises(ValueError): import_reports(root,tmp_path/'reports',receipt={**receipt,'manifest_hash':'different'})
    (root/'predictions.jsonl').write_text('tampered predictions\n')
    with pytest.raises(ValueError): import_reports(root,tmp_path/'reports',receipt=receipt)


def test_official_harness_rejects_dirty_or_ignored_shadowing(tmp_path,projects):
    from evals.swt_bench.official import check_harness
    from tests.unit.test_swt_prepare import git
    repo=projects.plain(tmp_path/'harness'); git(repo,'init'); git(repo,'add','.')
    git(repo,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-m','base')
    commit=git(repo,'rev-parse','HEAD')
    assert check_harness(repo,commit)
    (repo/'.git/info/exclude').write_text('json.py\n')
    (repo/'json.py').write_text('shadowing official imports')
    with pytest.raises(ValueError): check_harness(repo,commit)
    (repo/'json.py').unlink(); (repo/'example/parser.py').write_text('modified grader')
    with pytest.raises(ValueError): check_harness(repo,commit)


@pytest.mark.parametrize('failure',['timeout','nonzero'])
def test_failed_official_execution_is_retained_as_infrastructure_failure(tmp_path,monkeypatch,failure):
    import subprocess
    from evals.swt_bench import official
    from evals.swt_bench.io import read_json,write_json,seal
    root=tmp_path/'round'; root.mkdir(); harness=tmp_path/'harness'; harness.mkdir()
    data={'run_id':'r','model_name':'model','harness_commit':'a'*40,'predictions_hash':'h',
        'manifest':{'manifest_hash':'selected','cases':[{'instance_id':'known'}]},
        'outcomes':{'known':{'status':'DONE','official_status':'not_run'}}}
    write_json(root/'round.json',seal(data,'round_hash'))
    def run(*args,**kwargs):
        if failure=='timeout': raise subprocess.TimeoutExpired('harness',1)
        return subprocess.CompletedProcess(args,1)
    monkeypatch.setattr(official.subprocess,'run',run)
    monkeypatch.setattr(official,'check_harness',lambda *args:'source-hash')
    with pytest.raises(ValueError): official.execute_official(root,harness,data,['python','-m','src.main'],'source-hash')
    assert read_json(root/'round.json')['outcomes']['known']['official_status']=='infra_error'
    receipt=read_json(root/'official-execution.receipt.json')
    assert receipt['stop_reason'] and receipt['exit_code']!=0


def test_generated_official_command_uses_pinned_snapshot_not_floating_dataset():
    from evals.swt_bench.official import official_command
    command=official_command('round',{'run_id':'r','harness_commit':'a'*40,'manifest':{'cases':[{'instance_id':'known'}]}})
    assert 'REPROAGENT_SWT_SNAPSHOT' in command and 'princeton-nlp/SWE-bench_Lite' not in command


def test_successful_execution_without_report_is_missing_not_never_run(tmp_path):
    from evals.swt_bench.results import import_reports
    from evals.swt_bench.io import write_json,seal,file_hash
    root=tmp_path/'round'; root.mkdir(); (root/'predictions.jsonl').write_text('predictions\n')
    data={'run_id':'r','model_name':'model','harness_commit':'a'*40,'predictions_hash':file_hash(root/'predictions.jsonl'),
        'manifest':{'manifest_hash':'selected','source':{'snapshot_sha256':'d'*64}},
        'outcomes':{'known':{'status':'DONE','official_status':'not_run'}}}
    write_json(root/'round.json',seal(data,'round_hash'))
    receipt={'run_id':'r','model_name':'model','harness_commit':'a'*40,'predictions_hash':data['predictions_hash'],
        'executor':'reproagent-swt-official-run-v1','exit_code':0,'manifest_hash':'selected','report_hashes':{},
        'harness_source_hash':'a'*64,'dataset_snapshot_hash':'d'*64}
    result=import_reports(root,tmp_path/'reports',receipt=receipt)
    assert result['outcomes']['known']['official_status']=='missing'


def test_partial_manual_import_preserves_previously_verified_results(tmp_path):
    from evals.swt_bench.results import import_reports
    from evals.swt_bench.io import write_json,seal
    root=tmp_path/'round'; root.mkdir()
    write_json(root/'round.json',seal({'run_id':'r','model_name':'model','harness_commit':'a'*40,'predictions_hash':'h',
        'outcomes':{'a':{'official_status':'verified','official_resolved':True},'b':{'official_status':'not_run'}}},'round_hash'))
    reports=tmp_path/'reports'; write_json(reports/'r/model/b/report.json',{'b':{'resolved':False}})
    result=import_reports(root,reports)
    assert result['outcomes']['a']['official_status']=='verified' and result['outcomes']['a']['official_resolved'] is True
    empty=tmp_path/'empty'; empty.mkdir()
    assert import_reports(root,empty)['outcomes']==result['outcomes']
