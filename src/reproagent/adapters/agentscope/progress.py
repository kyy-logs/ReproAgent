"""Bounded per-call instructions containing program facts, never user prose."""
import re
from dataclasses import asdict

from ...core.exploration_progress import ExplorationStepView
from ...core.models import AgentContext
from ...core.serialization import canonical_bytes

MAX_PROGRESS_BYTES = 1024
CONVERGENCE_GUIDANCE = (
    'Budget convergence: focus on the current reproduction path. '
    'Use only targeted reads needed to resolve a concrete blocker while readers are allowed. '
    'Publish only when the existing contract and evidence justify it; otherwise revise from '
    'displayed evidence or request_information. Avoid restarting broad searches or retrying '
    'an unchanged refusal. The Controller owns execution and independent confirmation. '
    'Do not relax verification requirements.')


def _identifier(value) -> str:
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) else 'unknown'


def render_progress(view: ExplorationStepView, phase: AgentContext | None) -> str:
    facts = dict(contract_version='unknown', missing_information_count='unknown',
                 contract_grounded='unknown', latest_candidate_id='unknown', latest_candidate_result='unknown')
    if phase is not None:
        version = phase.contract.version
        facts.update(contract_version=version if type(version) is int and version > 0 else 'unknown',
                     missing_information_count=len(phase.contract.missing_information),
                     contract_grounded=bool(phase.contract.expected) and not phase.contract.missing_information)
        if phase.project.candidates:
            identifier = _identifier(phase.project.candidates[-1].candidate_id)
            facts['latest_candidate_id'] = identifier
            if identifier != 'unknown':
                entry = next((item for item in reversed(phase.history)
                              if item.get('phase') == 'candidate' and item.get('candidate_id') == identifier), {})
                facts['latest_candidate_result'] = _identifier(entry.get('result_code'))
    payload = {'budget': asdict(view), 'facts': facts,
               'guidance': CONVERGENCE_GUIDANCE if view.mode == 'converging' else ''}
    # Drop optional groups atomically; neither JSON nor instruction text is sliced.
    for optional in ((), ('latest_candidate_id', 'latest_candidate_result'),
                     ('contract_version', 'missing_information_count', 'contract_grounded')):
        for key in optional:
            facts.pop(key, None)
        encoded = canonical_bytes(payload)
        if len(encoded) <= MAX_PROGRESS_BYTES:
            return encoded.decode('utf-8')
    raise ValueError('exploration budget progress cannot fit its bounded message')
