"""What a passing run can say about why it passed -- and when it can say nothing.

A candidate that passes on the original snapshot is not evidence of anything, and the
bare phrase says the same thing whether the input was never read, the build never reached
the branch, or the assertion was satisfied by something else.  The probe already records
what the run read and which diagnostics it emitted; this turns that into one conclusion a
next phase can act on.

The conclusion is drawn only from call phases that were monitored completely.  A run that
was not watching and a run that reached nothing are indistinguishable from the outside,
so anything less than a complete capture stays ``unknown`` and never reads as an absence.
"""
from __future__ import annotations

CAPTURED = 'captured'
INCOMPLETE = 'incomplete'
NOT_CONFIGURED = 'not_configured'
NO_DIAGNOSTIC_EMITTED = 'no_diagnostic_emitted'
DIAGNOSTICS_EMITTED = 'diagnostics_emitted'
UNKNOWN = 'unknown'


def _combine(values):
    """One status for several phases: captured only when every one of them was."""
    present = [value for value in values if value]
    if not present:
        return NOT_CONFIGURED
    if all(value == CAPTURED for value in present):
        return CAPTURED
    return INCOMPLETE if INCOMPLETE in present else NOT_CONFIGURED


def reproduction_observation(observation):
    """The measured reason a passing run did or did not exercise its input.

    Args:
        observation: the `TestObservation` of the run whose candidate passed.

    Returns:
        dict: ``inputs`` and ``diagnostics`` monitoring statuses, how many diagnostics the
            call phases emitted, and one of ``no_diagnostic_emitted``,
            ``diagnostics_emitted`` or ``unknown``.  ``unknown`` is not a quiet absence:
            it means the run cannot answer, and no reader may treat it as "nothing was
            emitted".
    """
    phases = [phase for phase in observation.tests if phase.get('when') == 'call']
    monitoring = [phase.get('monitoring') or {} for phase in phases]
    inputs = _combine([item.get('inputs') for item in monitoring])
    diagnostics = _combine([item.get('diagnostics') for item in monitoring])
    emitted = sum(len(phase.get('diagnostics') or ()) for phase in phases)
    if inputs == CAPTURED and diagnostics == CAPTURED:
        conclusion = NO_DIAGNOSTIC_EMITTED if emitted == 0 else DIAGNOSTICS_EMITTED
    else:
        conclusion = UNKNOWN
    return {'inputs': inputs, 'diagnostics': diagnostics, 'emitted': emitted, 'conclusion': conclusion}


__all__ = ['CAPTURED', 'DIAGNOSTICS_EMITTED', 'INCOMPLETE', 'NOT_CONFIGURED',
           'NO_DIAGNOSTIC_EMITTED', 'UNKNOWN', 'reproduction_observation']
