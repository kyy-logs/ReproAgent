"""The six-tool phase surface: three snapshot-bound file tools and three domain tools.

The toolkit is built over a real frozen snapshot, so a candidate is published through the
existing Workspace, a contract revision cites only lines a tool really displayed at the
width of the tool that showed them, and a phase result is the controller's, never the
model's narration.
"""
import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("agentscope")

from agentscope.message import Base64Source, DataBlock, TextBlock, ToolCallBlock, ToolResultState
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from agentscope.state import AgentState
from agentscope.tool import Read as SDKRead
from agentscope.tool import ToolBase, ToolChunk

from reproagent.adapters.agentscope.evidence import EvidenceLedger
from reproagent.adapters.agentscope.snapshot_backend import SnapshotBackend
from reproagent.adapters.agentscope.tools import (
    DOMAIN_TOOL_NAMES,
    FILE_TOOL_NAMES,
    RESPONSE_TRUNCATION_MARKER,
    TOOL_NAMES,
    SnapshotResponseMiddleware,
    build_toolkit,
)
from reproagent.core.candidate_service import CandidateService
from reproagent.core.models import BudgetLimits, IssueContract, ProjectView, PythonPytestConfig, SourceRef, TaskRequest
from reproagent.core.phase import PhaseGate, PhaseResult
from reproagent.core.serialization import bytes_hash
from reproagent.paths import relative_name
from reproagent.store import TaskStore
from reproagent.workspace import Workspace

MARKER = "boundary_marker_77"
MODULE_TEXT = (
    '"""Empty input must return an empty list."""\n'
    "\n"
    "def parse(values):\n"
    "    return [values[0]]\n"
    "\n"
    f"def {MARKER}(values):\n"
    "    return values or []\n"
)
MODULE_LINES = MODULE_TEXT.splitlines()
MARKER_LINE = next(index for index, line in enumerate(MODULE_LINES, 1) if MARKER in line)
COMMENT_LINE = next(index for index, line in enumerate(MODULE_LINES, 1) if line.startswith('"""'))
#: Wider than the 500 bytes the SDK's Grep shows whole, narrower than Read's 2000.
WIDE_LINE = "# " + "x" * 598
#: Wider than either tool displays whole.
VERY_WIDE_LINE = "# " + "x" * 2098
BIG_LINES = "".join(f"padding_line_{index} = {index:04d}\n" for index in range(1, 1501))


def text_of(response) -> str:
    """The text the model sees, whatever chunk or completed response it arrived in."""
    return "".join(block.text for block in response.content if isinstance(block, TextBlock))


def sidecar_refs(text) -> list[dict]:
    """The evidence sidecar a file tool appended, or nothing when it cited no lines."""
    match = re.search(r"<evidence>(.*?)</evidence>", text, re.S)
    return [] if match is None else json.loads(match.group(1))["refs"]


def shown_lines(text) -> set[int]:
    return {int(match.group(1)) for match in re.finditer(r"^ *(\d+)\t", text, re.M)}


def call(env, name: str, payload: dict, call_id: str = "call-1"):
    """One tool call through the real Toolkit, which is what the agent does."""
    return asyncio.run(dispatch(env, name, payload, call_id))


async def dispatch(env, name, payload, call_id="call-1"):
    response = None
    async for chunk in env.toolkit.call_tool(ToolCallBlock(id=call_id, name=name, input=json.dumps(payload)), AgentState()):
        response = chunk
    return response


async def permission(tool, tool_input, mode):
    """The engine's own decision, exactly as ``Agent._acting`` obtains it."""
    return await PermissionEngine(PermissionContext(mode=mode)).check_permission(tool, tool_input)


def find_ripgrep():
    candidates = (os.environ.get("REPROAGENT_RG_PATH"), shutil.which("rg"), shutil.which("rg.exe"))
    return next((path for path in candidates if path and Path(path).is_file()), None)


def contract(**overrides):
    values = dict(contract_id="contract-1", version=1, description_hash="description-hash",
                  trigger="parse([]) raises IndexError", expected="parse([]) returns []", reported_actual="IndexError",
                  observable_checks=("parse([]) == []",), sources=(SourceRef("input/issue.md", "0" * 64, 1, 1, "issue"),),
                  assumptions=(), missing_information=())
    return IssueContract(**{**values, **overrides})


def environment(tmp_path, projects, facts, rg=None, *, bound=True, limits=None, parent="tests", **contract_overrides):
    repo = projects.plain(tmp_path / "中文 repo")
    (repo / "example" / "中文模块.py").write_text(MODULE_TEXT, encoding="utf-8", newline="")
    (repo / "example" / "long_lines.py").write_text(f"# short\n{WIDE_LINE}\n{VERY_WIDE_LINE}\n", encoding="utf-8", newline="")
    (repo / "example" / "big.py").write_text(BIG_LINES, encoding="utf-8", newline="")
    task = tmp_path / "task"
    store = TaskStore(task)
    workspace = Workspace(task, store)
    snapshot = workspace.freeze(TaskRequest(repo, task, repo / "issue.md",
                                            language=PythonPytestConfig(candidate_parent=parent)), facts.context())
    project = ProjectView(snapshot)
    context = facts.context(limits=limits) if limits is not None else facts.context()
    gate, service = PhaseGate(), CandidateService(project, workspace, context)
    if bound:
        service.bind_contract(contract(**contract_overrides))
    backend = SnapshotBackend(project, store, context, rg_path=rg)
    ledger = EvidenceLedger(project, store)
    toolkit = build_toolkit(backend, ledger, service, gate, context)
    return SimpleNamespace(repo=repo, task=task, store=store, workspace=workspace, snapshot=snapshot,
                           project=project, context=context, gate=gate, service=service, backend=backend,
                           ledger=ledger, toolkit=toolkit, root=snapshot.root, module=str(snapshot.root / "example" / "中文模块.py"))


def write_payload(path="tests/test_repro.py", content="def test_repro():\n    assert False\n", role="test", hypothesis="empty input"):
    return {"files": [{"path": path, "content": content, "role": role}], "hypothesis": hypothesis}


def candidate_ids(env):
    directory = env.task / "candidates"
    return sorted(item.name for item in directory.iterdir()) if directory.exists() else []


def test_candidate_tool_returns_actual_immutable_id(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    payload = json.loads(text_of(call(env, "write_candidate", write_payload())))
    assert payload["status"] == "candidate_published"
    candidate_id = payload["candidate_id"]
    # The id the phase reports is a record in the task store, not a string the model invented.
    stored = env.store.load_record("candidates", candidate_id)
    assert stored.candidate_id == candidate_id and stored.manifest_hash == payload["manifest_hash"]
    env.workspace.validate_candidate(stored)
    assert (stored.storage_root / "tests" / "test_repro.py").read_bytes() == b"def test_repro():\n    assert False\n"
    assert env.service.project.candidates == (stored,)
    assert env.gate.result == PhaseResult("candidate", candidate_id=candidate_id)

    # A refused publish is a tool error, the phase stays open, and nothing is written.
    for fields in (write_payload(path="outside/test_repro.py"),                       # outside the test area
                   write_payload(path="tests/conftest.py"),                           # configuration file
                   write_payload(path="tests/test_helper.py", role="helper"),         # unsupported role
                   write_payload(path="tests/test_repro.py.bak"),                     # not test_*.py
                   write_payload(path="../tests/test_repro.py"),                      # path escape
                   {"files": [{"path": "tests/test_repro.py", "content": "a\n", "role": "test"},
                              {"path": "tests/test_repro.py", "content": "b\n", "role": "test"}], "hypothesis": "duplicate"},
                   {"files": [{"path": "tests/test_existing.py", "content": "def test_x(): pass\n", "role": "test"}], "hypothesis": "overwrite"},
                   {"files": [], "hypothesis": "no files"},
                   {"files": [{"path": "tests/test_repro.py", "content": "def test_x(): pass\n"}], "hypothesis": "schema"}):
        before = candidate_ids(env)
        refused = call(env, "write_candidate", fields)
        assert refused.state is ToolResultState.ERROR, fields
        assert candidate_ids(env) == before
        assert env.gate.result == PhaseResult("candidate", candidate_id=candidate_id)

    # A phase whose contract still has missing information, or has none at all, cannot publish.
    for other in (environment(tmp_path / "ungrounded", projects, facts, expected="", **{}),
                  environment(tmp_path / "missing", projects, facts, missing_information=("which parser?",)),
                  environment(tmp_path / "unbound", projects, facts, bound=False)):
        result = call(other, "write_candidate", write_payload())
        assert result.state is ToolResultState.ERROR and other.gate.result is None
        assert candidate_ids(other) == []
        assert other.service.project.candidates == ()


#: Names a candidate may never be installed as, whatever role it claims: publishing one
#: would put a credential file inside the task's own candidate storage.
SENSITIVE_NAMES = (".env", ".env.local", "credentials.json", "id_rsa", "id_ed25519", "service.key", "server.pem")


@pytest.mark.parametrize("name", SENSITIVE_NAMES)
def test_candidate_cannot_be_installed_at_a_sensitive_name(tmp_path, projects, facts, name):
    """The publication rule refuses a credential name before any file is written."""
    env = environment(tmp_path, projects, facts)
    # The data role matters: a test file is already refused for its name, but nothing but the
    # sensitive-name rule in the product workspace refuses a data file at `tests/<secret>`.
    files = [{"path": "tests/test_repro.py", "content": "def test_repro():\n    assert False\n", "role": "test"},
             {"path": f"tests/{name}", "content": "SECRET=1\n", "role": "data"}]
    refused = call(env, "write_candidate", {"files": files, "hypothesis": "credential name"})
    assert refused.state is ToolResultState.ERROR
    assert candidate_ids(env) == [] and env.service.project.candidates == ()
    assert env.gate.result is None


def test_candidate_files_must_stay_in_the_configured_test_area(tmp_path, projects, facts):
    """The configured test area decides where a candidate may be installed, not the model.

    A path in the repository's ordinary ``tests`` directory is refused, and the same file
    under the configured area is published exactly there.
    """
    env = environment(tmp_path, projects, facts, parent="repro_tests")
    refused = call(env, "write_candidate", write_payload(path="tests/test_repro.py"))
    assert refused.state is ToolResultState.ERROR and env.gate.result is None
    assert candidate_ids(env) == []
    published = json.loads(text_of(call(env, "write_candidate", write_payload(path="repro_tests/test_repro.py"))))
    assert published["status"] == "candidate_published"
    stored = env.store.load_record("candidates", published["candidate_id"])
    assert (stored.storage_root / "repro_tests" / "test_repro.py").is_file()
    assert not (stored.storage_root / "tests").exists()


def test_a_published_candidate_cannot_be_cited_as_an_original(tmp_path, projects, facts):
    """A citation names a line of the frozen original; what the phase wrote is not one."""
    env = environment(tmp_path, projects, facts)
    published = json.loads(text_of(call(env, "write_candidate", write_payload())))
    candidate = env.store.load_record("candidates", published["candidate_id"])
    entry = candidate.files[0]
    citation = {"path": relative_name(candidate.storage_root / entry.path, env.store.root),
                "content_hash": entry.content_hash, "start_line": 1, "end_line": 1}
    env.gate.begin()
    for self_citation in (citation, {**citation, "path": "candidates/" + candidate.candidate_id}):
        refused = call(env, "revise_contract", {"source_refs": [self_citation], "reason": "cite my own candidate"})
        assert refused.state is ToolResultState.ERROR and env.gate.result is None


def test_revision_requires_displayed_original_lines(tmp_path, projects, facts):
    rg = find_ripgrep()
    env = environment(tmp_path, projects, facts, rg=rg)
    module_hash = bytes_hash(MODULE_TEXT.encode("utf-8"))
    citation = {"path": "example/中文模块.py", "content_hash": module_hash, "start_line": MARKER_LINE, "end_line": MARKER_LINE}

    # A Glob names files; a filename-only Grep names files. Neither displayed a line, so
    # neither authorises a line citation, however exact the hash the model writes down.
    listing = text_of(call(env, "Glob", {"pattern": "**/*.py", "path": str(env.root)}))
    assert "中文模块.py" in listing and sidecar_refs(listing) == []
    if rg:
        names = text_of(call(env, "Grep", {"pattern": MARKER, "path": str(env.root), "output_mode": "files_with_matches"}))
        assert "中文模块.py" in names and sidecar_refs(names) == []
    for unshown in (citation, {**citation, "path": str(env.root / "example" / "中文模块.py")}):
        refused = call(env, "revise_contract", {"source_refs": [unshown], "reason": "cite what was never shown"})
        assert refused.state is ToolResultState.ERROR and env.gate.result is None

    # Read displays line MARKER_LINE whole, so exactly that range becomes citable.
    read = text_of(call(env, "Read", {"file_path": env.module, "offset": MARKER_LINE, "limit": 1}))
    refs = sidecar_refs(read)
    # The sidecar names the file the way the snapshot does, so the model can copy it back.
    assert [(ref["path"], ref["start_line"], ref["end_line"]) for ref in refs] == \
        [("example/中文模块.py", MARKER_LINE, MARKER_LINE)]
    assert refs[0]["content_hash"] == module_hash
    # A forged hash, a range the tool never displayed, an empty citation list and an
    # argument the tool does not take are all refused, and none of them ends the phase.
    for forged in ({**citation, "content_hash": bytes_hash(b"elsewhere")},
                   {**citation, "start_line": COMMENT_LINE, "end_line": COMMENT_LINE},
                   {**citation, "end_line": MARKER_LINE + 1}):
        refused = call(env, "revise_contract", {"source_refs": [forged], "reason": "forged"})
        assert refused.state is ToolResultState.ERROR and env.gate.result is None
    for malformed in ({"source_refs": [], "reason": "nothing"},
                      {"source_refs": [citation], "reason": "x", "sources": [citation]},
                      {"source_refs": [citation]},
                      {"reason": "no references"}):
        refused = call(env, "revise_contract", malformed)
        assert refused.state is ToolResultState.ERROR and env.gate.result is None
    accepted = json.loads(text_of(call(env, "revise_contract", {"source_refs": [citation], "reason": "cite the parse source"})))
    assert accepted["status"] == "revision_requested"
    assert env.gate.result.kind == "revise_contract" and env.gate.result.reason == "cite the parse source"
    assert [(ref.path, ref.content_hash, ref.start_line, ref.end_line) for ref in env.gate.result.source_refs] == \
        [(relative_name(env.root / "example" / "中文模块.py", env.store.root), module_hash, MARKER_LINE, MARKER_LINE)]

    # A line Read truncates is not a whole original line: it is shown, and not citable.
    env.gate.begin()
    long_lines = str(env.root / "example" / "long_lines.py")
    long_hash = bytes_hash((env.root / "example" / "long_lines.py").read_bytes())
    read = text_of(call(env, "Read", {"file_path": long_lines}))
    assert "[truncated]" in read
    shown = [(ref["start_line"], ref["end_line"]) for ref in sidecar_refs(read)]
    assert shown == [(1, 2)]                    # the truncated line 3 is displayed, not citable
    long_ref = {"path": "example/long_lines.py", "content_hash": long_hash}
    # Only the whole displayed range is evidence: the truncated line, and a slice of the
    # displayed range, are both refused.
    for refused in ({**long_ref, "start_line": 3, "end_line": 3}, {**long_ref, "start_line": 1, "end_line": 1}):
        assert call(env, "revise_contract", {"source_refs": [refused], "reason": "not displayed"}).state is ToolResultState.ERROR
        assert env.gate.result is None
    intact = {**long_ref, "start_line": 1, "end_line": 2}
    assert json.loads(text_of(call(env, "revise_contract", {"source_refs": [intact], "reason": "whole line"})))["status"] == "revision_requested"
    assert [(ref.path, ref.content_hash, ref.start_line, ref.end_line) for ref in env.gate.result.source_refs] == \
        [(relative_name(env.root / "example" / "long_lines.py", env.store.root), long_hash, 1, 2)]


@pytest.mark.skipif(find_ripgrep() is None, reason="no ripgrep on this host, so the SDK search blocks by design")
def test_grep_content_hits_are_citable_at_the_width_grep_shows(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, rg=find_ripgrep())
    module_hash = bytes_hash(MODULE_TEXT.encode("utf-8"))
    hit = text_of(call(env, "Grep", {"pattern": MARKER, "path": str(env.root), "output_mode": "content"}))
    assert env.module in hit
    refs = sidecar_refs(hit)
    assert [(ref["start_line"], ref["end_line"]) for ref in refs] == [(MARKER_LINE, MARKER_LINE)]
    revision = {"source_refs": [{"path": "example/中文模块.py", "content_hash": module_hash,
                                 "start_line": MARKER_LINE, "end_line": MARKER_LINE}], "reason": "grep hit"}
    assert json.loads(text_of(call(env, "revise_contract", revision)))["status"] == "revision_requested"

    # Grep shows at most 500 bytes of a line, so its view of the 600-byte line is not the
    # whole line and the line is not citable from a Grep view.
    env.gate.begin()
    long_lines = str(env.root / "example" / "long_lines.py")
    long_hash = bytes_hash((env.root / "example" / "long_lines.py").read_bytes())
    grep = text_of(call(env, "Grep", {"pattern": "x{5}", "path": long_lines, "output_mode": "content"}))
    assert "[Omitted" in grep and "# short" not in grep
    assert sidecar_refs(grep) == []
    assert call(env, "revise_contract", {"source_refs": [{"path": "example/long_lines.py", "content_hash": long_hash,
                                                         "start_line": 2, "end_line": 2}], "reason": "truncated"}).state is ToolResultState.ERROR
    assert env.gate.result is None


def test_a_non_text_view_is_passed_through_untouched(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)

    class Picture(ToolBase):
        """A tool whose view is not text: nothing in it is a citable line."""

        name, description, input_schema = "Picture", "returns a data block", {"type": "object", "properties": {}}
        is_mcp = is_external_tool = is_state_injected = False
        is_read_only = is_concurrency_safe = True

        async def check_permissions(self, tool_input, context):
            return PermissionDecision(behavior=PermissionBehavior.PASSTHROUGH, message="read only")

        async def call(self):
            return ToolChunk(content=[DataBlock(source=Base64Source(data="aGk=", media_type="image/png"), name="icon.png")], is_last=True)

    tool = Picture(middlewares=[SnapshotResponseMiddleware(env.backend, env.ledger, env.context, view="none")])
    chunks = asyncio.run(_chunks(tool))
    assert len(chunks) == 1 and isinstance(chunks[0].content[0], DataBlock)
    assert chunks[0].content[0].source.data == "aGk="          # the view the model gets is unchanged


async def _chunks(tool):
    return [chunk async for chunk in await tool()]


def test_first_phase_result_wins_and_toolkit_is_exact(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    # Exactly the six tools the phase registers: no shell, no editor, no run/submit choice.
    names = tuple(tool.name for tool in env.toolkit.tool_groups[0].tools)
    assert names == TOOL_NAMES == FILE_TOOL_NAMES + DOMAIN_TOOL_NAMES
    schemas = asyncio.run(env.toolkit.get_tool_schemas())
    assert tuple(schema["function"]["name"] for schema in schemas) == TOOL_NAMES
    for absent in ("Bash", "PowerShell", "Write", "Edit", "run_candidate", "submit_candidate",
                   "search_code", "read_file", "replace", "MultiEdit"):
        assert absent not in names and asyncio.run(env.toolkit.get_tool(absent)) is None
    # The SDK's default Read promises every file on the machine; the snapshot boundary is
    # stated instead, without editing the SDK.
    reader = asyncio.run(env.toolkit.get_tool("Read"))
    assert "all files on the machine" in SDKRead().description
    assert "all files on the machine" not in reader.description
    for description in (reader.description, asyncio.run(env.toolkit.get_tool("Grep")).description,
                        asyncio.run(env.toolkit.get_tool("Glob")).description):
        assert "registered" in description and "snapshot" in description

    # A response and its evidence sidecar stay inside the configured budget, and every
    # citation describes lines the response really shows.
    text = text_of(call(env, "Read", {"file_path": str(env.root / "example" / "big.py")}))
    assert len(text.encode("utf-8")) <= env.context.budget.limits.tool_response_bytes == 32768
    assert RESPONSE_TRUNCATION_MARKER.strip() in text
    refs = sidecar_refs(text)
    assert refs and shown_lines(text)
    for ref in refs:
        assert set(range(ref["start_line"], ref["end_line"] + 1)) <= shown_lines(text)

    # The first domain result of the round is the round's result.
    published = json.loads(text_of(call(env, "write_candidate", write_payload())))
    assert env.gate.result == PhaseResult("candidate", candidate_id=published["candidate_id"])
    assert call(env, "request_information", {"question": "stop"}).state is ToolResultState.ERROR
    assert call(env, "write_candidate", write_payload(path="tests/test_second.py")).state is ToolResultState.ERROR
    assert env.gate.result == PhaseResult("candidate", candidate_id=published["candidate_id"])
    assert candidate_ids(env) == [published["candidate_id"]]

    # A new round starts with an open gate and can end on a different kind of result.
    env.gate.begin()
    assert json.loads(text_of(call(env, "request_information", {"question": "which parser?"})))["status"] == "information_requested"
    assert env.gate.result == PhaseResult("request_information", question="which parser?")


def test_the_permission_engine_itself_allows_every_phase_tool(tmp_path, projects, facts):
    """The engine decides what a real agent may run; ``Toolkit.call_tool`` never asks it.

    A tool that only works through the direct call path would stall every real phase: a
    non-read-only tool that answers ``PASSTHROUGH`` is *asked for* in DEFAULT mode (no user
    is there to answer) and denied in DONT_ASK mode, and the phase could never end.
    """
    env = environment(tmp_path, projects, facts)
    inputs = {"Read": {"file_path": env.module},
              "Grep": {"pattern": MARKER, "path": str(env.root)},
              "Glob": {"pattern": "**/*.py", "path": str(env.root)},
              "write_candidate": write_payload(),
              "revise_contract": {"source_refs": [{"path": "example/中文模块.py", "content_hash": "0" * 64,
                                                   "start_line": 1, "end_line": 1}], "reason": "cite"},
              "request_information": {"question": "which parser?"}}
    assert tuple(inputs) == TOOL_NAMES
    for name, tool_input in inputs.items():
        tool = asyncio.run(env.toolkit.get_tool(name))
        for mode in (PermissionMode.DEFAULT, PermissionMode.DONT_ASK, PermissionMode.BYPASS):
            decision = asyncio.run(permission(tool, tool_input, mode))
            assert decision.behavior is PermissionBehavior.ALLOW, (name, mode, decision.message)
    # A deny rule still wins: the engine evaluates rules before the tool is consulted.
    engine = PermissionEngine(PermissionContext(mode=PermissionMode.DEFAULT))
    engine.add_rule(PermissionRule(tool_name="write_candidate", rule_content=None, behavior=PermissionBehavior.DENY,
                                   source="projectSettings"))
    tool = asyncio.run(env.toolkit.get_tool("write_candidate"))
    assert asyncio.run(engine.check_permission(tool, write_payload())).behavior is PermissionBehavior.DENY

    # EXPLORE is read-only by design: it denies a domain tool without ever asking it, so the
    # exploration phase must not be run in EXPLORE mode.  The read tools are unaffected.
    for name in DOMAIN_TOOL_NAMES:
        assert asyncio.run(permission(asyncio.run(env.toolkit.get_tool(name)), inputs[name], PermissionMode.EXPLORE)).behavior \
            is PermissionBehavior.DENY
    assert asyncio.run(permission(asyncio.run(env.toolkit.get_tool("Read")), inputs["Read"], PermissionMode.EXPLORE)).behavior \
        is PermissionBehavior.ALLOW


def test_a_small_response_budget_still_yields_a_bounded_response(tmp_path, projects, facts):
    # A budget that fits some lines but not the whole file, and the sidecar that would
    # describe them: the response is cut at a line boundary and marked, and what does not fit
    # is not cited.
    env = environment(tmp_path / "small", projects, facts, limits=BudgetLimits(tool_response_bytes=400))
    text = text_of(call(env, "Read", {"file_path": env.module}))
    assert len(text.encode("utf-8")) <= 400
    assert RESPONSE_TRUNCATION_MARKER.strip() in text
    for ref in sidecar_refs(text):
        assert set(range(ref["start_line"], ref["end_line"] + 1)) <= shown_lines(text)
    assert shown_lines(text) and not shown_lines(text) >= set(range(1, len(MODULE_LINES) + 1))

    # A budget smaller than the truncation marker itself: the body is cut hard, nothing is
    # cited, and the response is still inside the budget.
    tiny = environment(tmp_path / "tiny", projects, facts, limits=BudgetLimits(tool_response_bytes=50))
    text = text_of(call(tiny, "Read", {"file_path": tiny.module}))
    assert 0 < len(text.encode("utf-8")) <= 50
    assert sidecar_refs(text) == []
