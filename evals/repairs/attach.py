"""Attach a confirmed repaired-environment diagnostic to the round it re-judged.

The pinned harness never mounts a repair profile, so a case whose environment cannot run
at all comes back `resolved: false` with every test set empty -- an environment result,
not a candidate verdict.  That much is already recorded: the case stays unresolved and
the official rate keeps counting it as one.  What was missing is the other half, stated
where a reader of the round will see it.

This writes that half as a separate field on the outcome the receipt actually re-judged.
The official verdict is not touched: `official_resolved`, `official_status` and every
official figure are carried through unchanged, and the record says so in `official_effect`
rather than leaving a reader to infer it.

Only a receipt that binds *this* round can be attached.  The binding is the round hash the
diagnostic froze before it ran, so a receipt cannot be moved onto a round it did not judge,
and a second diagnostic cannot overwrite the first.
"""
import argparse
import json
from pathlib import Path

from evals.swt_bench.io import file_hash, read_json, seal, verify_seal, write_json

#: The diagnostic kind this accepts.  A different kind means a different claim.
DIAGNOSTIC_KIND = 'reproagent-repaired-environment-diagnostic-v1'
#: Only a confirmed differential is worth attaching; anything else is not a finding.
CONFIRMED = 'candidate_differential_confirmed'
#: What the record says about the official figures, carried as data so that no reader has
#: to infer that this annotation left them alone.
OFFICIAL_EFFECT = 'none'


def attach(round_dir, receipt_path):
    """Record one confirmed repaired-environment diagnostic on its own round.

    Args:
        round_dir: the round the diagnostic re-judged.  It is read, re-sealed with the
            added field and rewritten; nothing else in it changes.
        receipt_path: the diagnostic's ``receipt.json``.

    Returns:
        dict: the updated round record.

    Raises:
        ValueError: the receipt is not a confirmed diagnostic of this round, the round
            names no such case, or the case already carries a repair record.
    """
    round_dir = Path(round_dir).resolve()
    receipt_path = Path(receipt_path).resolve()
    from .sphinx_receipt import validate_rejudge_receipt
    before_hash = file_hash(receipt_path)
    receipt = validate_rejudge_receipt(round_dir, receipt_path)
    if file_hash(receipt_path) != before_hash:
        raise ValueError('diagnostic receipt changed during validation')
    if receipt.get('kind') != DIAGNOSTIC_KIND:
        raise ValueError('not a repaired-environment diagnostic receipt')
    if receipt.get('result') != CONFIRMED:
        raise ValueError('only a confirmed differential can be attached')
    data = read_json(round_dir / 'round.json')
    verify_seal(data, 'round_hash')
    # The diagnostic froze this round's hash before it ran, so a receipt can only ever be
    # attached to the round it was derived from.
    if receipt.get('source_round_hash') != data['round_hash']:
        raise ValueError('diagnostic receipt does not bind this round')
    case_id = receipt.get('case_id')
    if case_id not in data['outcomes']:
        raise ValueError('diagnostic names a case this round does not record')
    image = receipt.get('image')
    if not isinstance(image, dict) or not isinstance(receipt.get('profile'), dict):
        raise ValueError('diagnostic receipt carries no image or profile identity')
    record = {'kind': receipt['kind'], 'result': receipt['result'], 'official_effect': OFFICIAL_EFFECT,
        'receipt_path': receipt_path.as_posix(), 'receipt_sha256': before_hash,
        'source_round_hash': receipt['source_round_hash'], 'image_id': image.get('id'),
        'harness_commit': receipt.get('harness_commit'), 'base_commit': receipt.get('base_commit'),
        'prediction_patch_sha256': receipt.get('prediction_patch_sha256'),
        'profile_sha256': dict(receipt['profile'])}
    outcomes = {key: dict(value) for key, value in data['outcomes'].items()}
    if outcomes[case_id].get('environment_repair') is not None:
        raise ValueError('this case already carries a repair record')
    outcomes[case_id]['environment_repair'] = record
    # Only the outcomes are rebuilt; the official fields ride along in the copies above.
    updated = seal({**{key: value for key, value in data.items() if key != 'round_hash'}, 'outcomes': outcomes},
        'round_hash')
    write_json(round_dir / 'round.json', updated)
    from evals.swt_bench.results import save_summary
    save_summary(round_dir, updated)
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', required=True)
    parser.add_argument('--receipt', required=True)
    args = parser.parse_args()
    updated = attach(args.round, args.receipt)
    recorded = [key for key, value in updated['outcomes'].items() if value.get('environment_repair')]
    print(json.dumps({'attached': recorded, 'round_hash': updated['round_hash']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
