"""Strict, task-local equivalence of already validated semantic review material."""
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

from .models import EvidenceRef, Verdict
from .review_context import _root_spellings
from .serialization import canonical_hash, parse_json


@dataclass(frozen=True, slots=True)
class SemanticApproval:
    key: str
    run_id: str
    verdict: Verdict
    available_refs: tuple[EvidenceRef, ...]


def _gateway_policy(gateway):
    layers = []
    seen = set()
    while gateway is not None:
        if id(gateway) in seen or len(layers) >= 8:
            return None
        seen.add(id(gateway))
        factory = getattr(gateway, 'factory', None)
        config = getattr(factory, 'config', None)
        layers.append({'identity': id(gateway), 'type': type(gateway).__qualname__,
            'factory': id(factory) if factory is not None else None,
            'config': asdict(config) if is_dataclass(config) else None,
            'redaction': tuple(getattr(gateway, 'secrets', ()))})
        gateway = getattr(gateway, 'gateway', None)
    return layers


def review_key(contract, candidate, run, review, prompt, gateway, *, byte_limit, secrets):
    """Ignore only execution identity/paths, never business values or failure text.

    Both review contexts must have been built by build_review_context, including
    candidate and citation hash checks. Unknown evidence stays non-reusable.
    """
    if run.execution_role != 'original' or candidate.preconditions:
        return None
    payload = deepcopy(review.payload)
    ref_keys = ('path', 'content_hash', 'start_line', 'end_line')
    replacements = {}
    for index, ref in enumerate(run.observation.failure_refs):
        values = asdict(ref)
        replacements[tuple(values[key] for key in ref_keys)] = {
            **values, 'path': f'<failure:{index}>', 'content_hash': f'<checked-content:{index}>'}

    def refs(value):
        if isinstance(value, dict):
            identity = tuple(value.get(key) for key in ref_keys)
            if all(isinstance(v, (str, int)) for v in identity) and identity in replacements:
                value.update(replacements[identity])
            return {key: refs(item) for key, item in value.items()}
        if isinstance(value, list):
            return [refs(item) for item in value]
        return value

    roots = []
    details = run.observation.framework_details
    code_root = details.get('run_root', '')
    if code_root:
        roots.extend((s, '<run>') for s in _root_spellings(code_root))
        workspace = str(Path(code_root).parent)
        if workspace not in ('', '.', code_root):
            roots.extend((s, '<workspace>') for s in _root_spellings(workspace))
    if details.get('temp_root'):
        roots.extend((s, '<temp>') for s in _root_spellings(details['temp_root']))
    roots.sort(key=lambda pair: len(pair[0]), reverse=True)

    def paths(value):
        if isinstance(value, str):
            for spelling, replacement in roots:
                value = value.replace(spelling, replacement)
            return value
        if isinstance(value, dict):
            return {key: paths(item) for key, item in value.items()}
        if isinstance(value, list):
            return [paths(item) for item in value]
        return value

    try:
        for evidence in payload['failure_evidence']:
            record = parse_json(evidence['text'])
            if (record.get('event') != 'test_phase' or record.get('run_id') != run.run_id
                    or not isinstance(record.get('payload'), dict)):
                return None
            record['run_id'] = '<run-id>'
            evidence['text'] = paths(record)
        payload['observation'] = paths(payload['observation'])
        payload = refs(payload)
        policy = _gateway_policy(gateway)
        if policy is None:
            return None
        return canonical_hash({'policy': 1, 'prompt': prompt, 'gateway_policy': policy,
            'byte_limit': byte_limit, 'redaction': tuple(secrets),
            'contract': asdict(contract), 'candidate': asdict(candidate),
            'manifest_hash': candidate.manifest_hash, 'snapshot_id': run.snapshot_id,
            'environment_id': run.environment_id, 'exit_code': run.raw.exit_code,
            'stop_reason': run.raw.stop_reason, 'material': payload})
    except (ValueError, TypeError, KeyError):
        return None


def rebound_refs(approval, review):
    if len(approval.available_refs) != len(review.available_refs):
        raise ValueError('review citation count changed')
    mapping = dict(zip(approval.available_refs, review.available_refs))
    return tuple(mapping[ref] for ref in approval.verdict.evidence_refs)
