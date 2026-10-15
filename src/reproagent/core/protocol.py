"""The model boundary's contract vocabulary; local validation remains authoritative.

Everything a provider reply is reduced to before the product looks at it lives here: the
fixed failure classes and the closed set of outcomes an attempt may record.  Neither the
SDK model factory nor any future model boundary may invent a vocabulary of its own, and
no raw provider text ever enters a record built from these helpers.
"""


class ModelProtocolError(Exception):
    """The model failed bounded correction of a structured response."""


class ProviderRequestError(RuntimeError):
    """A failed provider request, carrying no raw provider text or credentials."""
    def __init__(self, *, status_code=None):
        self.status_code = status_code
        self.kind = 'http_error' if status_code is not None else 'network_error'
        super().__init__(f'provider HTTP {status_code}' if status_code is not None
                         else 'provider network failure after 3 attempts')


# Fixed classes for an unusable provider response; only these enter events, and none
# of them carries provider text. Truncation, an explicit filter/abort and an oversized
# body cannot be corrected by repeating the same request; a complete but structurally
# wrong JSON object can, within the correction limit.
MODEL_OUTPUT_CODES = frozenset({'OUTPUT_TRUNCATED', 'OUTPUT_FILTERED', 'EMPTY_OUTPUT',
                                'RESPONSE_TOO_LARGE', 'INVALID_PROTOCOL'})


class ModelOutputError(ValueError):
    """Provider returned invalid output, distinct from invalid caller configuration.

    ``code`` names the failure class and ``retryable`` says whether repeating the
    same logical request can still help. The class stays a ``ValueError`` so that
    existing ``except ValueError`` paths keep catching it.
    """
    def __init__(self, message, code='INVALID_PROTOCOL', retryable=True):
        if code not in MODEL_OUTPUT_CODES:
            raise ValueError(f'unknown model output code: {code}')
        super().__init__(message)
        self.code = code
        self.retryable = retryable


FINISH_REASONS = frozenset({'stop', 'length', 'content_filter', 'tool_calls', 'function_call'})

# Closed set for the outcome an attempt records; anything else is projected to 'unknown'.
ATTEMPT_OUTCOMES = frozenset({'completed', 'unknown', 'network_error', 'http_error'}) | MODEL_OUTPUT_CODES


def classify_finish_reason(value):
    """Reduce a provider's finish_reason to a closed set; raw values never enter records."""
    if not isinstance(value, str) or not value: return 'unknown'
    return value if value in FINISH_REASONS else 'other'


def classify_output(finish_reason, text, tool_calls=False):
    """Map a provider choice onto the fixed failure classes; None means usable text."""
    if finish_reason == 'length':
        return ModelOutputError('provider stopped at the output limit; the response is incomplete',
                                'OUTPUT_TRUNCATED', False)
    if finish_reason != 'stop' or tool_calls:
        return ModelOutputError('provider stopped for a reason other than a complete answer',
                                'OUTPUT_FILTERED', False)
    if not isinstance(text, str) or not text:
        return ModelOutputError('provider returned no text; return exactly one complete JSON object',
                                'EMPTY_OUTPUT')
    return None


def attempt_payload(attempt, usage, cost_kind, cost_value, *, response_kind, effective_output_limit,
                    outcome='unknown', finish_reason='', content_bytes=0, backend=None, http_status_code=None):
    """Bound what a model attempt records: no prompt, raw response, reasoning text or header."""
    record = {'attempt':attempt, 'usage':usage, 'cost_kind':cost_kind, 'cost_value':cost_value,
        'outcome':outcome if outcome in ATTEMPT_OUTCOMES else 'unknown',
        'response_kind':response_kind, 'effective_output_limit':effective_output_limit,
        'finish_reason':classify_finish_reason(finish_reason), 'content_bytes':content_bytes}
    if backend is not None: record['backend'] = backend
    if type(http_status_code) is int and 100 <= http_status_code <= 599:
        record['http_status_code'] = http_status_code
    return record


def object_schema(properties):
    return {'type':'object', 'properties':properties, 'required':list(properties), 'additionalProperties':False}


def contract_schema(source_count):
    properties = {name:{'type':'string'} for name in ('trigger','expected','reported_actual')}
    properties.update({name:{'type':'array','items':{'type':'string'}}
                       for name in ('observable_checks','assumptions','missing_information')})
    indices = {'type':'array','items':{'type':'integer','minimum':0}}
    if source_count:
        indices['items']['maximum'] = source_count - 1
    else:
        indices['maxItems'] = 0
    properties['source_indices'] = indices
    return object_schema(properties)


def validate_contract(result, source_count):
    validate_keys(result, contract_schema(source_count)['required'])
    for name in ('trigger','expected','reported_actual'):
        if type(result.get(name, '')) is not str:
            raise ValueError(f'{name} must be a string, not an object or array')
    for name in ('observable_checks','assumptions','missing_information'):
        value = result.get(name, [])
        if not isinstance(value,list) or any(type(item) is not str for item in value):
            raise ValueError(f'{name} must be an array of strings')
    indices = result.get('source_indices', [])
    if not isinstance(indices,list) or any(type(i) is not int or not 0 <= i < source_count for i in indices):
        raise ValueError(f'source_indices must contain integer indices into the {source_count} supplied sources')
    return result


def validate_keys(result, required):
    missing = set(required) - set(result)
    extra = set(result) - set(required)
    if missing or extra:
        raise ValueError(f'response fields mismatch: missing {sorted(missing)}, unexpected {sorted(extra)}')


def verdict_schema(assertion_ids=(), oracle_ids=()):
    from .models import CandidateClass
    properties = {'classification':{'type':['string','null'],'enum':[c.value for c in CandidateClass] + [None]},
                  'reason':{'type':'string'}, 'evidence_refs':{'type':'array','minItems':1,'items':object_schema({
                      'path':{'type':'string'}, 'content_hash':{'type':'string'},
                      'start_line':{'type':'integer','minimum':1},'end_line':{'type':'integer','minimum':1}})}}
    properties.update({name:{'type':'boolean'} for name in ('expected_assertion','target_triggered','failure_matches_issue')})
    properties['assertion_reviews'] = {'type': 'array', 'minItems': len(assertion_ids), 'maxItems': len(assertion_ids),
        'items': object_schema({'assertion_id': {'type': 'string', **({'enum': list(assertion_ids)} if assertion_ids else {})},
            'supported': {'type': 'boolean'}, 'reason': {'type': 'string'},
            'evidence_refs': {'type': 'array', 'items': properties['evidence_refs']['items']}})}
    if oracle_ids:
        properties['oracle_reviews'] = {'type':'array','minItems':len(oracle_ids),'maxItems':len(oracle_ids),
            'items':object_schema({'check_id':{'type':'string','enum':list(oracle_ids)},
                'role':{'type':'string','enum':['regression','execution_control','setup_precondition']},
                'supported':{'type':'boolean'},
                'basis':{'type':'string','enum':['desired_behavior','existing_semantics','reported_actual','unknown']},
                'reason':{'type':'string'}, 'evidence_refs':{'type':'array','items':properties['evidence_refs']['items']},
                'source_quote':{'type':'string','maxLength':1024}})}
    return object_schema(properties)


def validate_verdict(result, assertion_ids=(), oracle_ids=()):
    validate_keys(result, verdict_schema(assertion_ids, oracle_ids)['required'])
    if result['classification'] not in verdict_schema()['properties']['classification']['enum']:
        raise ValueError('classification must be an allowed verdict or null')
    if type(result['reason']) is not str:
        raise ValueError('reason must be a string')
    for name in ('expected_assertion','target_triggered','failure_matches_issue'):
        if type(result[name]) is not bool:
            raise ValueError(f'{name} must be a boolean, not assertion text')
    if not isinstance(result['evidence_refs'],list) or not result['evidence_refs']:
        raise ValueError('evidence_refs must be a nonempty array of available references')
    for ref in result['evidence_refs']:
        if not isinstance(ref,dict):
            raise ValueError('invalid evidence reference')
        validate_keys(ref, ('path','content_hash','start_line','end_line'))
        if any(type(ref[name]) is not str for name in ('path','content_hash')) or any(type(ref[name]) is not int for name in ('start_line','end_line')):
            raise ValueError('invalid evidence reference types')
    rows = result['assertion_reviews']
    if not isinstance(rows, list):
        raise ValueError('assertion_reviews must be an array covering every assertion_id')
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid assertion_reviews entry')
        validate_keys(row, ('assertion_id', 'supported', 'reason', 'evidence_refs'))
        identifier = row['assertion_id']
        if type(identifier) is not str or identifier not in assertion_ids or identifier in seen:
            raise ValueError('assertion_reviews contains unknown or duplicate assertion_id')
        seen.add(identifier)
        if type(row['supported']) is not bool or type(row['reason']) is not str or not row['reason'].strip():
            raise ValueError('assertion_reviews requires supported boolean and nonempty reason')
        refs = row['evidence_refs']
        if not isinstance(refs, list) or (row['supported'] and not refs):
            raise ValueError('supported assertion_reviews require expectation source citations')
        for ref in refs:
            if not isinstance(ref, dict):
                raise ValueError('invalid assertion_reviews evidence reference')
            validate_keys(ref, ('path', 'content_hash', 'start_line', 'end_line'))
            if any(type(ref[name]) is not str for name in ('path', 'content_hash')) or any(type(ref[name]) is not int for name in ('start_line', 'end_line')):
                raise ValueError('invalid assertion_reviews evidence reference types')
    if seen != set(assertion_ids):
        raise ValueError('assertion_reviews must cover every assertion_id exactly once')
    if oracle_ids:
        rows = result['oracle_reviews']
        if not isinstance(rows,list):
            raise ValueError('oracle_reviews must cover generated checks')
        covered = set()
        for row in rows:
            if not isinstance(row,dict): raise ValueError('invalid oracle review')
            validate_keys(row, ('check_id','role','supported','basis','reason','evidence_refs','source_quote'))
            key = row['check_id']
            if type(key) is not str or key not in oracle_ids or key in covered:
                raise ValueError('unknown or duplicate oracle check')
            covered.add(key)
            if (row['role'] not in ('regression','execution_control','setup_precondition')
                    or row['basis'] not in ('desired_behavior','existing_semantics','reported_actual','unknown')
                    or type(row['supported']) is not bool or type(row['reason']) is not str or not row['reason'].strip()
                    or type(row['source_quote']) is not str or len(row['source_quote']) > 1024):
                raise ValueError('invalid oracle support fields')
            refs = row['evidence_refs']
            if not isinstance(refs,list): raise ValueError('invalid oracle references')
            for ref in refs:
                if not isinstance(ref,dict): raise ValueError('invalid oracle reference')
                validate_keys(ref, ('path','content_hash','start_line','end_line'))
                if (any(type(ref[name]) is not str for name in ('path','content_hash'))
                        or any(type(ref[name]) is not int for name in ('start_line','end_line'))):
                    raise ValueError('invalid oracle reference types')
            if row['supported'] and (row['basis'] not in ('desired_behavior','existing_semantics')
                    or not refs or not row['source_quote'].strip()):
                raise ValueError('approved oracle needs normative or existing-semantic source and exact quote')
        if covered != set(oracle_ids): raise ValueError('oracle_reviews must cover every generated check exactly once')
    return result

def learning_schema():
    properties = {"category": {"type": "string", "enum": ["framework", "model", "workflow"]},
        "tags": {"type": "array", "maxItems": 5, "items": {"type": "string", "minLength": 1, "maxLength": 64}},
        "name": {"type": "string", "minLength": 1, "maxLength": 64},
        "description": {"type": "string", "minLength": 1, "maxLength": 160},
        "summary": {"type": "string", "minLength": 1}, "detail": {"type": "string", "minLength": 1},
        "evidence_ids": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
            "items": {"type": "string"}}}
    return object_schema({"experience": {"anyOf": [object_schema(properties), {"type": "null"}]}})
