"""Small inventories of actual HTTP inputs; never prompt content or agent evidence.

Assembly records provenance separately, by exact text digest. The HTTP hook joins
it to the final request before ContentStore can truncate its diagnostic copy.
Unknown messages stay unknown. This module never changes messages or counts tokens.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

from .core.serialization import parse_json

MAX_MANIFEST_BYTES = 12000
MAX_SOURCES = 256
_active_sources = ContextVar('reproagent_input_sources', default=None)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(b, dict) and b.get('type') == 'text'
                                       and isinstance(b.get('text'), str) for b in value):
        return '\n'.join(b['text'] for b in value)
    return None


class InputSources:
    """Task-local metadata, bounded independently of the SDK conversation."""
    def __init__(self):
        self.entries = OrderedDict()
        self.current_phase = None
        self.expected = {}

    def remember(self, text, kind):
        key = _digest(text)
        self._put(key, {'kind': kind, 'role': 'system'})
        self.expected[kind] = (key, 'system')

    def phase(self, text, data, *, omitted_history=0):
        sections = []
        for key, value in data.items():
            descriptor = {'kind': {'history': 'phase_history', 'experience_catalog': 'experience_notice',
                                    'experience_browse': 'experience_browse'}.get(key, key),
                          'section': key, 'bytes': len(_encoded(value))}
            if key == 'contract' and isinstance(value, dict):
                descriptor.update(contract_id=value.get('contract_id'), version=value.get('version'))
            elif key == 'issue' and isinstance(value, dict):
                descriptor['source_hash'] = value.get('content_hash')
            elif key == 'feedback' and isinstance(value, str):
                try:
                    feedback = parse_json(value)
                except (ValueError, TypeError, RecursionError):
                    feedback = None
                if isinstance(feedback, dict):
                    for field in ('result_code', 'candidate_id', 'contract_id', 'contract_version'):
                        if field in feedback:
                            descriptor[field] = feedback[field]
            if key == 'history' and isinstance(value, list):
                descriptor['entry_count'] = len(value)
            sections.append(descriptor)
        key = _digest(text)
        self.current_phase = key
        self.expected['phase_input'] = (key, 'user')
        self._put(key, {'kind': 'phase_input', 'role': 'user', 'sections': sections,
                        'omitted_history': omitted_history})

    def _put(self, key, value):
        self.entries[key] = value
        self.entries.move_to_end(key)
        while len(self.entries) > MAX_SOURCES:
            self.entries.popitem(last=False)


@contextmanager
def input_sources(sources):
    token = _active_sources.set(sources)
    try:
        yield
    finally:
        _active_sources.reset(token)


def remember_phase(sources, text, data, *, omitted_history=0):
    """Optional observation must never stop an otherwise valid phase."""
    try:
        sources.phase(text, data, omitted_history=omitted_history)
    except Exception:
        sources.current_phase = None
        sources.expected.pop('phase_input', None)


def remember_prompt(sources, text):
    try:
        sources.remember(text, 'system_prompt')
    except Exception:
        pass


def remember_notices(sources, messages):
    try:
        for message in messages:
            kind = {'experience-browse': 'experience_browse',
                    'exploration-progress': 'budget_progress'}.get(getattr(message, 'name', ''))
            if kind:
                blocks = getattr(message, 'content', ())
                text = '\n'.join(b.text for b in blocks if getattr(b, 'type', '') == 'text')
                sources.remember(text, kind)
    except Exception:
        pass


def _tool_item(message, text, calls):
    call_id = message.get('tool_call_id')
    tool = calls.get(call_id, message.get('name', 'unknown'))
    item = {'kind': 'tool_result', 'tool': tool, 'tool_call_id': call_id}
    if tool in ('Read', 'Grep'):
        item['kind'] = 'source_result'
        # These are the tool's displayed references, not a fresh integrity verdict.
        if text and '\n<evidence>' in text:
            tail = text.rsplit('\n<evidence>', 1)[1].split('</evidence>', 1)[0]
            try:
                refs = parse_json(tail).get('refs')
            except (ValueError, AttributeError, RecursionError):
                refs = None
            if isinstance(refs, list):
                item.update(source_refs=refs[:8], source_ref_count=len(refs),
                            refs_origin='tool_result_sidecar')
    elif tool in ('list_experiences', 'read_experience'):
        item['kind'] = 'experience_result'
        try:
            result = parse_json(text) if text else None
        except (ValueError, TypeError, RecursionError):
            result = None
        if isinstance(result, dict):
            if tool == 'list_experiences' and isinstance(result.get('items'), list):
                item.update(kind='experience_catalog',
                            experience_ids=[v.get('id') for v in result['items'][:16] if isinstance(v, dict)],
                            entry_count=len(result['items']))
            elif tool == 'read_experience' and all(k in result for k in ('id', 'summary', 'detail')):
                item.update(kind='experience_body', experience_id=result['id'])
            elif 'code' in result:
                item['result_code'] = result['code']
    return item


def build_manifest(body, *, purpose, request_bytes, request_hash):
    """Describe this body, not the retained/truncated trace copy of it."""
    sources = _active_sources.get()
    known = sources.entries if sources is not None else {}
    messages = body.get('messages', [])
    if not isinstance(messages, list):
        raise ValueError('unsupported request messages')
    texts = [_text(m.get('content')) if isinstance(m, dict) else None for m in messages]
    keys = [_digest(text) if text is not None else None for text in texts]
    current = sources.current_phase if sources is not None else None
    latest = max((i for i, key in enumerate(keys) if current is not None and key == current
                  and isinstance(messages[i], dict) and messages[i].get('role') == 'user'), default=-1)
    items, omissions, calls = [], [], {}
    unknown = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            unknown += 1
            items.append({'kind': 'unknown', 'message_index': index})
            continue
        text, key = texts[index], keys[index]
        base = {'message_index': index, 'role': message.get('role'),
                'bytes': len(text.encode('utf-8')) if text is not None else len(_encoded(message)),
                'content_hash': key}
        source = known.get(key)
        if source is not None and source['role'] != message.get('role'):
            source = None  # An assistant/tool echo is not program-authored state.
        if source is not None and source['kind'] == 'phase_input':
            scope = 'current' if index == latest else 'historical'
            items.extend({**base, **section, 'scope': scope} for section in source['sections'])
            if source['omitted_history']:
                omissions.append({'kind': 'phase_history', 'message_index': index, 'scope': scope,
                                  'reason': 'phase_input_capacity', 'entry_count': source['omitted_history']})
        elif source is not None:
            items.append({**base, **source})
        elif message.get('role') == 'tool':
            items.append({**base, **_tool_item(message, text, calls)})
        elif message.get('role') == 'assistant':
            items.append({**base, 'kind': 'assistant_message'})
        else:
            items.append({**base, 'kind': 'unknown'})
            unknown += 1
        for call in message.get('tool_calls', []) or []:
            if isinstance(call, dict) and isinstance(call.get('function'), dict):
                calls[call.get('id')] = call['function'].get('name', 'unknown')
    tools = body.get('tools') or []
    if tools:
        items.append({'kind': 'tool_definitions', 'location': '/tools',
                      'bytes': len(_encoded(tools)), 'entry_count': len(tools)})
    delivered = {(key, message.get('role')) for key, message in zip(keys, messages)
                 if isinstance(message, dict) and isinstance(message.get('role'), str)}
    missing = [{'kind': kind, 'reason': 'not_in_http_request'}
               for kind, identity in (sources.expected.items() if sources is not None else ())
               if identity not in delivered]
    result = {'schema_version': 1, 'basis': 'http_request_body', 'purpose': purpose,
              'request_bytes': request_bytes, 'request_hash': request_hash,
              'message_count': len(messages), 'tool_definition_count': len(tools),
              'unclassified_messages': unknown, 'items': items, 'omissions': omissions,
              'missing': missing, 'inventory_complete': True, 'omitted_items': 0}
    # JSON's default array separator is two bytes (", "). Encode each row once;
    # repeatedly encoding the shrinking whole object would be quadratic in history.
    groups = (items, omissions, missing)
    sizes = [[len(_encoded(item)) for item in group] for group in groups]
    payload_bytes = sum(sum(group) + 2 * max(0, len(group) - 1) for group in sizes)
    while True:
        envelope = {**result, 'items': [], 'omissions': [], 'missing': []}
        if len(_encoded(envelope)) + payload_bytes <= MAX_MANIFEST_BYTES:
            break
        for group, row_sizes in zip(groups, sizes):
            if group:
                payload_bytes -= row_sizes.pop() + (2 if len(group) > 1 else 0)
                group.pop()
                break
        else:
            break
        result['omitted_items'] += 1
        result['inventory_complete'] = False
    return result
