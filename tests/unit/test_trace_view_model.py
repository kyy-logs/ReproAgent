import copy
import json
import math

import pytest
from reproagent.trace_view_model import build_trace_view


def fixture(version=1):
    root = "0" * 16
    permission = "1" * 16 if version == 1 else f"point:{root}:1"
    spans = [node(root, "task", None), node(permission, "permission", root,
             kind="event" if version == 1 else "point", tool_call_key="call", result_code="ALLOWED"),
             node("2"*16, "tool.Read", root, tool_call_key="call")]
    spans[1]["content_refs"] = ["args"]
    contents = [{"content_id":"args", "owner_span_id":permission, "source":"sdk_tool_input",
                "availability":"captured", "text":"unique body", "truncated":False}]
    doc = {"schema_version":version,"trace_id":"a"*32,"root_span_id":root,
           "total_duration":10,"status":"DONE","spans":spans,"contents":contents,
           "summary":{"usage":{"input_tokens":10,"output_tokens":2,"complete":True},
                      "cost":{"amount":0.1,"complete":True},"logical_calls":1}}
    return doc


def node(ident, name, parent, kind="span", **attrs):
    return {"span_id":ident,"otel_span_id":ident if kind != "point" else None,
            "otel_status":"UNSET","name":name,"parent_span_id":parent,"kind":kind,
            "status":"ok","attributes":attrs,"offset_seconds":0,"duration_seconds":1}


@pytest.mark.parametrize("version",[1,2])
def test_schema1_and_schema2_permissions_join_tools(version):
    doc=fixture(version); before=copy.deepcopy(doc)
    view=build_trace_view(doc)
    assert view["nodes"][1]["content_keys"]==view["nodes"][2]["content_keys"]==[0]
    assert doc==before


def test_denied_and_reserved_calls_keep_their_own_arguments():
    doc=fixture(); doc["spans"].pop()
    for code in ("DENIED","RESERVED_FOR_PUBLISHING"):
        doc["spans"][1]["attributes"]["result_code"]=code
        view=build_trace_view(doc)
        assert view["nodes"][1]["content_keys"]==[0]
        assert view["nodes"][1]["permission_result"]==code
    assert any("RESERVED_FOR_PUBLISHING" in h["text"] for h in view["hints"])


def test_shared_content_has_one_descriptor_and_no_embedded_text():
    doc=fixture(); doc["spans"][0]["content_refs"]=["args"]
    view=build_trace_view(doc)
    assert len(view["contents"])==1
    assert "unique body" not in json.dumps(view)
    assert "text" not in view["contents"][0]


def test_time_geometry_handles_unknown_zero_and_nonfinite_values():
    doc=fixture(); doc["spans"][2].update(offset_seconds=2,duration_seconds=3)
    view=build_trace_view(doc)
    assert view["nodes"][2]["start_percent"]==20
    assert view["nodes"][2]["width_percent"]==30
    for value in (None,-1,True,float("nan"),float("inf")):
        doc["spans"][2].update(offset_seconds=value,duration_seconds=value)
        n=build_trace_view(doc)["nodes"][2]
        assert n["offset_seconds"] is None and n["duration_seconds"] is None
    doc["spans"][2].update(offset_seconds=0,duration_seconds=0)
    n=build_trace_view(doc)["nodes"][2]
    assert n["width_percent"]==0 and n["duration_seconds"]==0
    json.dumps(build_trace_view(doc),allow_nan=False)


def test_partial_metrics_are_labelled_known_subtotals():
    doc=fixture(); doc["partial"]=True; doc["summary"]["usage"]["complete"]=False
    doc["summary"]["cost"]["complete"]=False
    h=build_trace_view(doc)["header"]
    assert h["token_label"]=="已知小计" and h["total_tokens"] is None
    assert h["cost_label"]=="已知小计" and h["partial"] is True
    assert h["agent_replies"] is None and h["ttft"] is None


def test_deep_tree_and_missing_parent_do_not_invent_edges():
    doc=fixture(2); doc["contents"]=[]
    doc["spans"]=[node(f"{i:016x}","task" if i==0 else "step",None if i==0 else f"{i-1:016x}") for i in range(1024)]
    view=build_trace_view(doc)
    assert view["nodes"][-1]["depth"]==1023
    assert view["time_axis"]["extent_seconds"]==10
    doc["partial"]=True; doc["spans"][-1]["parent_span_id"]="f"*16
    n=build_trace_view(doc)["nodes"][-1]
    assert n["missing_parent"] and n["parent_key"] is None
    assert n["actual_parent_span_id"]=="f"*16


def test_model_name_requires_complete_wire_json_and_actual_ancestor():
    doc=fixture(); doc["spans"][2]["name"]="model.http_attempt"
    doc["spans"][1].update(name="model.logical",kind="span",content_refs=[])
    doc["spans"][2]["parent_span_id"]=doc["spans"][1]["span_id"]
    doc["contents"][0].update(owner_span_id="2"*16,source="wire_request",text='{"model":"test-model"}')
    view=build_trace_view(doc)
    assert view["nodes"][2]["model"]==view["nodes"][1]["model"]=="test-model"
    assert view["nodes"][0]["model"] is None
    doc["contents"][0]["truncated"]=True
    assert build_trace_view(doc)["nodes"][2]["model"] is None


def test_hints_use_retained_attempts_and_exclude_nested_wrappers():
    doc=fixture(); doc["spans"][1].update(name="model.logical",kind="span",content_refs=[])
    doc["spans"][2].update(name="model.http_attempt",parent_span_id="1"*16)
    doc["spans"].append(node("3"*16,"model.http_attempt","1"*16))
    view=build_trace_view(doc)
    assert any(h["kind"]=="retry" for h in view["hints"])
    assert all(h["node_key"]!=0 for h in view["hints"] if h["kind"]=="slow")
