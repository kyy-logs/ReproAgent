"""Scripted approvals for protocol fixtures, not evidence of model judgment."""


def approved_assertions(payload):
    sources = [{key: source[key] for key in ('path', 'content_hash', 'start_line', 'end_line')}
               for source in payload['expectation_sources']]
    return [{'assertion_id': site['id'], 'supported': True, 'basis': 'current_requirement',
             'reason': 'Scripted fixture: this expectation is supported.', 'evidence_refs': sources}
            for site in payload['assertion_sites']]
