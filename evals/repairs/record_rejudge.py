"""Run an explicit environment diagnostic and validate it before attachment.

Official results are preserved; this command only records a separate diagnostic.
"""
import argparse
import json
import math
from pathlib import Path

from .attach import attach
from .sphinx_rejudge import run_rejudge


def run_and_record(round_dir, logs, image, output, *, timeout=240):
    if (type(timeout) is not int
            or not 0 < timeout <= 3600):
        raise ValueError('timeout must be an integer between 1 and 3600 seconds')
    receipt = run_rejudge(round_dir, logs, image, output, timeout=timeout)
    result = {'result': receipt.get('result', 'inconclusive'),
              'recorded': False, 'official_effect': 'none'}
    if result['result'] == 'candidate_differential_confirmed':
        updated = attach(round_dir, Path(output) / 'receipt.json')
        result.update(recorded=True, round_hash=updated['round_hash'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('round', 'logs', 'image', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--timeout', type=int, default=240)
    args = parser.parse_args(argv)
    result = run_and_record(args.round, args.logs, args.image, args.output, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['recorded'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
