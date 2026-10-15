"""Scripted approvals for protocol fixtures, not evidence of model judgment."""


def approved_assertions(payload):
    sources = [{key: source[key] for key in ('path', 'content_hash', 'start_line', 'end_line')}
               for source in payload['expectation_sources']]
    return [{'assertion_id': site['id'], 'supported': True,
             'reason': 'Scripted fixture: this expectation is supported.', 'evidence_refs': sources}
            for site in payload['assertion_sites']]


def approved_oracles(payload):
    """Scripted protocol fixture only, never a real semantic quality judgment."""
    if not payload.get('oracle_checks'):
        return {}
    source = payload['expectation_sources'][0]
    ref = {key:source[key] for key in ('path','content_hash','start_line','end_line')}
    return {'oracle_reviews':[{'check_id':item['id'],'role':'regression','supported':True,
        'basis':'desired_behavior','reason':'Scripted protocol fixture, not semantic evidence.',
        'evidence_refs':[ref],'source_quote':source['text'][:200]}
        for item in payload['oracle_checks']]}
