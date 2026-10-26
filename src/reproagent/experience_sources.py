"""Model-visible learning references to bounded, program-observed task facts."""
from __future__ import annotations

from .core.serialization import canonical_bytes

SOURCE_EVENT = 'experience.source'
SOURCE_TOOLS = frozenset(('Read', 'Grep', 'Glob', 'write_candidate', 'revise_contract', 'request_information'))
SOURCE_BYTES = 1536
PROTOCOL_ERRORS = frozenset(('UNKNOWN_TOOL', 'MULTIPLE_TOOL_CALLS', 'INVALID_TOOL_INPUT'))


def _valid(fact):
    if not isinstance(fact, dict) or len(canonical_bytes(fact)) > SOURCE_BYTES:
        return False
    kind = fact.get('kind')
    if kind == 'tool_error':
        required = {'kind', 'tool', 'tool_call_id', 'arguments', 'result'}
        return (required <= fact.keys() <= required | {'error_code'}
            and fact['tool'] in SOURCE_TOOLS and isinstance(fact['tool_call_id'], str)
            and bool(fact['tool_call_id']) and isinstance(fact['arguments'], dict)
            and isinstance(fact['result'], str) and bool(fact['result']))
    if kind == 'protocol_error':
        return set(fact) == {'kind', 'code'} and fact['code'] in PROTOCOL_ERRORS
    return (kind == 'original_run' and set(fact) == {'kind', 'run_id'}
            and isinstance(fact['run_id'], str) and bool(fact['run_id']))


def learning_sources(events, *, before=None):
    """IDs come from immutable event positions, never from model prose."""
    sources = {}
    for event in events:
        if event.kind != SOURCE_EVENT or before is not None and event.seq >= before:
            continue
        if event.payload.get('schema_version') != 1:
            continue
        fact = event.payload.get('fact')
        if _valid(fact):
            sources['L' + str(event.seq)] = fact
    return sources


class LearningSourceRegistry:
    def __init__(self, store, *, secrets=()):
        self.store = store
        self.secrets = tuple(secret for secret in secrets if secret)

    def _redact(self, value):
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, '[REDACTED]')
            return value
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        return value

    def sources(self):
        events, errors = self.store.read_events()
        return {} if errors else learning_sources(events)

    def register(self, fact):
        """Called only by execution boundaries; failure never changes the task."""
        try:
            fact = self._redact(fact)
            if not _valid(fact):
                return None
            # Leave room for the escaped text and entry envelope in the 2 KiB atom.
            if len(canonical_bytes({'id': 'E9999', 'kind': 'tool_error',
                    'text': canonical_bytes({'source_id': 'L' + '9' * 20, **fact}).decode('utf-8')})) > 2048:
                return None
            if fact['kind'] == 'original_run':
                run = self.store.load_record('runs', fact['run_id'])
                if run.execution_role != 'original':
                    return None
                events, errors = self.store.read_events()
                ref = 'runs/' + run.run_id + '/execution.json'
                if errors or not any(e.kind == 'run.completed' and ref in e.refs for e in events):
                    return None
                existing = next((key for key, value in learning_sources(events).items() if value == fact), None)
                if existing:
                    return existing
            event = self.store.append_event(SOURCE_EVENT, (), {'schema_version': 1, 'fact': fact})
            return 'L' + str(event.seq)
        except (OSError, ValueError, TypeError, UnicodeError):
            return None

    def original_run_notices(self):
        try:
            events, errors = self.store.read_events()
            if errors:
                return []
            notices = []
            for event in events:
                if event.kind != 'run.completed' or event.payload.get('execution_role') != 'original':
                    continue
                for ref in event.refs:
                    parts = ref.split('/')
                    if len(parts) != 3 or parts[0] != 'runs' or parts[-1] != 'execution.json':
                        continue
                    identity = self.register({'kind': 'original_run', 'run_id': parts[1]})
                    if identity:
                        notices.append({'learning_source_id': identity, 'kind': 'original_run', 'run_id': parts[1]})
            return notices[-3:]
        except (OSError, ValueError, TypeError):
            return []


def source_notice(identity):
    data = {'learning_source_id': identity, 'usage': 'Copy this ID into lesson_draft.source_ids only if this fact supports the lesson.'}
    return '\n<learning_source>' + canonical_bytes(data).decode('utf-8') + '</learning_source>'
