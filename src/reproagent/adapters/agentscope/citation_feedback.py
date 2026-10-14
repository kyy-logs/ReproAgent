"""Bounded citation diagnostics; rendering never changes evidence or permissions."""
import json


def render_citation_feedback(*, code: str, errors: list[dict], byte_limit: int,
                             available_refs_truncated: bool = False) -> str:
    if type(byte_limit) is not int or byte_limit < 0:
        raise ValueError('citation feedback byte limit must be a nonnegative integer')
    # Local copies: shrinking this response must not mutate the collected facts.
    entries = [{**error, 'submitted': dict(error['submitted']),
                'available_refs': [dict(ref) for ref in error['available_refs']]}
               for error in errors]
    payload = {
        'code': code,
        'message': 'Citation request rejected. Copy exact verified displayed references; no revision was accepted.',
        'error_count': len(errors), 'errors': entries, 'omitted_errors': 0,
        'available_refs_truncated': available_refs_truncated,
    }
    while True:
        text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        if len(text.encode('utf-8')) <= byte_limit:
            return text
        # Remove low-priority suggestions first, then trailing errors. Never cut JSON.
        with_refs = next((entry for entry in reversed(entries) if entry['available_refs']), None)
        if with_refs is not None:
            with_refs['available_refs'].pop()
            payload['available_refs_truncated'] = True
        elif entries:
            entries.pop()
            payload['omitted_errors'] += 1
        else:
            return 'Citation refused.'[:byte_limit]
