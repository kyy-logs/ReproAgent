import json
import pytest
from agentscope.message import ToolResultState
from reproagent.core.models import BudgetLimits
from test_agentscope_domain_tools import environment, call, text_of, sidecar_refs


def test_large_tool_result_is_saved_and_readable_without_new_citation_authority(tmp_path, projects, facts):
    env=environment(tmp_path, projects, facts, limits=BudgetLimits(tool_response_bytes=2048))
    text=text_of(call(env,'Read',{'file_path':str(env.root/'example/big.py')}))
    assert len(text.encode('utf-8')) <= 2048
    assert 'read_artifact' in text
    meta=env.store.materials.catalog()[0]
    response=call(env,'read_artifact',{'artifact_id':meta['id'],'offset':1000,'limit':2})
    data=json.loads(text_of(response))
    assert response.state is ToolResultState.SUCCESS
    assert 'padding_line_1000' in data['text']
    assert data['upstream_complete'] is None
    assert sidecar_refs(text_of(response)) == []
    assert sidecar_refs(text), 'original displayed lines retain their evidence sidecar'


def test_unknown_material_does_not_allow_arbitrary_file_reads(tmp_path,projects,facts):
    env=environment(tmp_path,projects,facts)
    data=json.loads(text_of(call(env,'read_artifact',{'artifact_id':'../input/issue.md'})))
    assert data['code']=='unknown_artifact'


def test_offload_failure_preserves_truncated_tool_result(tmp_path,projects,facts,monkeypatch):
    env=environment(tmp_path,projects,facts,limits=BudgetLimits(tool_response_bytes=2048))
    monkeypatch.setattr(env.store.materials,'capture',lambda *a,**k:{'code':'material_store_error'})
    text=text_of(call(env,'Read',{'file_path':str(env.root/'example/big.py')}))
    assert 'padding_line_' in text and 'material_store_error' in text
    assert len(text.encode()) <= 2048
    assert '"id"' not in text


def test_tiny_tool_capacity_never_returns_oversized_notice(tmp_path,projects,facts):
    env=environment(tmp_path,projects,facts,limits=BudgetLimits(tool_response_bytes=64))
    text=text_of(call(env,'Read',{'file_path':str(env.root/'example/big.py')}))
    assert len(text.encode()) <= 64
