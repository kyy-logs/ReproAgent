"""A single-decision AgentScope strategy sharing the native protocol rules."""
from .dependency import require_agentscope
from ..models.provider import bounded
from ...core.agent import ReproAgent
from ...core.budget import BudgetStopped
from ...core.models import ModelRequest
from ...core.protocol import ModelOutputError


class DecisionGateway:
    def __init__(self, gateway):
        require_agentscope()
        self.gateway = gateway

    async def complete(self, request, context):
        from agentscope.agent import Agent, ReActConfig, InjectionConfig, ModelConfig, ContextConfig
        from agentscope.model import ChatModelBase, ChatResponse
        from agentscope.message import Msg, TextBlock
        from agentscope.tool import Toolkit
        from agentscope.formatter import OpenAIChatFormatter

        shared = self.gateway
        class ModelBridge(ChatModelBase):
            def __init__(self):
                super().__init__(None, 'reproagent-shared-model', self.Parameters(), stream=False, max_retries=0, context_size=1048576)
                self.calls = 0
                self.response = None
                self.formatter = OpenAIChatFormatter()

            async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
                context.budget.check()
                if context.cancel_event.is_set():
                    raise BudgetStopped('CANCELLED')
                if tools or self.calls:
                    raise ModelOutputError('AgentScope decision must use no tools and at most one model call')
                self.calls += 1
                mapped = []
                for message in messages:
                    blocks = message.get_content_blocks()
                    if any(not isinstance(block, TextBlock) for block in blocks):
                        raise ModelOutputError('unexpected non-text AgentScope decision context')
                    mapped.append({'role':message.role, 'content':message.get_text_content() or ''})
                response = await shared.complete(ModelRequest(tuple(mapped), request.response_kind, request.max_output_tokens), context)
                if response.finish_reason != 'stop' or not response.text:
                    raise ModelOutputError('incomplete decision response')
                self.response = response
                return ChatResponse(content=[TextBlock(text=response.text)], is_last=True, id=response.request_id or 'decision')

        bridge = ModelBridge()
        system = '\n'.join(message['content'] for message in request.messages if message['role']=='system')
        inputs = [Msg(name=message['role'], role=message['role'], content=[TextBlock(text=message['content'])])
                  for message in request.messages if message['role']!='system']
        agent = Agent(name='ReproAgent', system_prompt=system, model=bridge, toolkit=Toolkit(),
            model_config=ModelConfig(max_retries=0),
            react_config=ReActConfig(max_iters=1, interruption_raise_cancelled_error=True),
            injection_config=InjectionConfig(inject_runtime_state=False),
            context_config=ContextConfig(compression_tool_enabled=False))
        result = await bounded(agent.reply(inputs), context)
        if context.cancel_event.is_set():
            raise BudgetStopped('CANCELLED')
        if str(result.finished_reason) != 'completed' or bridge.response is None or bridge.calls != 1:
            raise ModelOutputError('AgentScope decision did not complete exactly one model call')
        if result.get_text_content() != bridge.response.text:
            raise ModelOutputError('AgentScope changed the decision response')
        return bridge.response


class AgentScopeExplorer(ReproAgent):
    def __init__(self, gateway, context, candidate_parent='tests'):
        super().__init__(DecisionGateway(gateway), context, candidate_parent)
