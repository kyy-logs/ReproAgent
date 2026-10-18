"""Build the current task view for one model request, without mutating SDK history."""
from agentscope.message import UserMsg

from ...core.budget import BudgetStopped
from ...core.serialization import canonical_bytes, bytes_hash
from ...observability import mark


class ExplorationContextBuilder:
    def __init__(self, store, context, secrets=()):
        self.store, self.context = store, context
        self.store.materials.secrets = tuple(s for s in secrets if s)

    def build(self, messages, phase_context):
        progress = self.store.progress
        progress.snapshot(self.context.budget)
        if progress.last_error:
            raise BudgetStopped('NEEDS_INFORMATION','current task state could not be persisted: '+progress.last_error)
        try:
            # Include the envelope in the byte budget, not just its inner state.
            data = {'task_state':progress.view(max_bytes=4070)}
            text = canonical_bytes(data).decode('utf-8')
        except ValueError as exc:
            raise BudgetStopped('NEEDS_INFORMATION','current task state exceeds input capacity') from exc
        payload=dict(revision=data['task_state']['revision'],bytes=len(text.encode('utf-8')),
                     content_hash=bytes_hash(text.encode('utf-8')))
        # Diagnostics cannot cause an already executed action to be replayed.
        try:
            self.store.append_event('context.prepared',(),payload)
            mark('context.prepared',attributes=payload)
        except (OSError,ValueError,RuntimeError):
            pass
        return [m for m in messages if getattr(m,'name','')!='task-state'] + [UserMsg(name='task-state',content=text)]
