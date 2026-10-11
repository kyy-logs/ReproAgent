import asyncio
from types import SimpleNamespace

from agentscope.message import AssistantMsg, ToolCallBlock, ToolResultBlock, TextBlock
from reproagent.core.models import EvidenceRef
from reproagent.core.serialization import canonical_bytes
from reproagent import learning_material


def capture(messages, refs=(), **kwargs):
    function = getattr(learning_material, 'capture_exploration', None)
    assert callable(function), 'a bounded SDK error capture is required'
    return function(messages, refs, **kwargs)


def pair(name, call_id, args, output, state='error'):
    return [AssistantMsg(name='explorer', content=[ToolCallBlock(id=call_id, name=name, input=args)]),
        AssistantMsg(name='explorer', content=[ToolResultBlock(id=call_id, name=name, output=output, state=state)])]


def test_capture_retains_current_errors_without_memory_or_assistant_claims():
    messages = pair('read_experience', 'memory', '{}', 'HISTORICAL_HINT')
    messages += [AssistantMsg(name='explorer', content='UNVERIFIED_CAUSE')]
    messages += pair('Read', 'read', '{"file_path":"absent.py"}', [TextBlock(text='file absent secret')])
    messages += pair('Read', 'read', '{"file_path":"parser.py"}', 'ok', state='success')
    messages += pair('Grep', 'grep', '{"pattern":"x"}', 'reserved', state='denied')
    ref = EvidenceRef('snapshots/s/code/parser.py', 'a' * 64, 1, 2)
    result = capture(messages, (ref,), secrets=('secret',))
    assert result['source_refs'] == (ref,)
    errors = result['tool_errors']
    assert [e['tool'] for e in errors] == ['Read', 'Grep']
    assert errors[0]['tool_call_id'] == 'read'
    assert errors[0]['arguments'] == {'file_path':'absent.py'}
    assert errors[0]['result'] == 'file absent [REDACTED]'
    text = canonical_bytes(result).decode()
    assert 'HISTORICAL_HINT' not in text and 'UNVERIFIED_CAUSE' not in text


def test_capture_keeps_small_errors_when_other_atoms_are_too_large_or_unlinked():
    messages = pair('Read', 'huge', '{}', 'big' * 3000)
    messages += [AssistantMsg(name='explorer', content=[ToolResultBlock(
        id='unlinked', name='Read', output='no previous call', state='interrupted')])]
    for i in range(12):
        messages += pair('Read', f'r{i}', '{}', 'absent')
    result = capture(messages)
    assert len(result['tool_errors']) == 8
    assert all(len(canonical_bytes(error)) <= 2048 for error in result['tool_errors'])
    assert 'TOOL_ERROR_TOO_LARGE' in result['capture_issues']
    assert 'MISSING_TOOL_CALL' in result['capture_issues']
    assert 'TOOL_ERROR_LIMIT' in result['capture_issues']
    assert 'bigbig' not in canonical_bytes(result).decode()


def test_sdk_retirement_failure_still_closes_factory():
    from reproagent.adapters.agentscope.explorer import AgentScopeExplorer
    closed = []
    class Runtime:
        async def aclose(self):
            raise RuntimeError('retirement failed')
    class Factory:
        async def aclose(self):
            closed.append(True)
    explorer = object.__new__(AgentScopeExplorer)
    explorer.runtime, explorer.model_factory = Runtime(), Factory()
    explorer.ledger = SimpleNamespace(issued_refs=())
    explorer.gateway = SimpleNamespace(secrets=())
    try:
        asyncio.run(explorer.aclose())
    except RuntimeError:
        pass
    assert closed == [True]
