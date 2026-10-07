"""Model-visible contracts; local validation remains authoritative."""


class ModelProtocolError(Exception):
    """The model failed bounded correction of a structured response."""


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


def verdict_schema():
    from .models import CandidateClass
    properties = {'classification':{'type':['string','null'],'enum':[c.value for c in CandidateClass] + [None]},
                  'reason':{'type':'string'}, 'evidence_refs':{'type':'array','minItems':1,'items':object_schema({
                      'path':{'type':'string'}, 'content_hash':{'type':'string'},
                      'start_line':{'type':'integer','minimum':1},'end_line':{'type':'integer','minimum':1}})}}
    properties.update({name:{'type':'boolean'} for name in ('expected_assertion','target_triggered','failure_matches_issue')})
    return object_schema(properties)


def validate_verdict(result):
    validate_keys(result, verdict_schema()['required'])
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
    return result
