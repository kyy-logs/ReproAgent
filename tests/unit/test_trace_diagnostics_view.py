import copy
from tests.unit.test_trace_view_model import fixture,node
from reproagent.trace_view_model import build_trace_view
from reproagent.trace_rendering import render_trace


def test_diagnostic_chain_distinguishes_revision_and_refusal():
    d=fixture(2);r=d["root_span_id"]
    names=[("contract.established",{}),("publication.rejected",{"decision_code":"CONTRACT_UNGROUNDED"}),
           ("citation.rejected",{"decision_code":"CITATION_NOT_DISPLAYED"}),
           ("task.stopped",{"stop_reason":"EXHAUSTED","budget_dimension":"steps"})]
    for i,(name,attrs) in enumerate(names):
        d["spans"].append(node(f"point:{r}:{i+3}",name,r,kind="point",contract_version=1,**attrs))
    d["diagnostic_summary"]={"status":"EXHAUSTED","stop_reason":"EXHAUSTED","budget":{"steps_remaining":0},"budget_dimension":"steps"}
    before=copy.deepcopy(d);view=build_trace_view(d)
    kinds=[x["kind"] for x in view["diagnostics"]]
    assert "publication.rejected" in kinds and "citation.rejected" in kinds
    assert "contract.revised" not in kinds and "verify" not in kinds
    assert view["diagnostics"][-1]["stop_reason"]=="EXHAUSTED"
    assert d==before
    html=render_trace(d)
    assert 'id="diagnostics"' in html and 'data-diagnostic-node=' in html
    assert "CONTRACT_UNGROUNDED" in html and "业务诊断链" in html


def test_legacy_unknowns_are_not_fabricated():
    d=fixture(1)
    for entry in d["spans"]: entry["attributes"].pop("tool_call_key",None)
    d["partial"]=True;d["contents"][0].update(availability="omitted_limit",text=None)
    view=build_trace_view(d)
    assert view["header"]["capture_health"]["legacy"]
    assert view["header"]["capture_health"]["issues"][0]["code"]=="LEGACY_UNSPECIFIED"
    assert all(x["relation"]=="sequence_only" for x in view["diagnostics"])
    assert all(x["budget"] is None for x in view["diagnostics"])
    assert "历史记录未提供" in render_trace(d)
