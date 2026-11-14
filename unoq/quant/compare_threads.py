"""Strictly compare completed thread-experiment outputs and reports."""
import argparse
import json
from pathlib import Path

import numpy as np

KEYS = {'depth', 'depth_conf', 'pose_enc'}


def compare(reference, candidate):
    result = {'passed': False, 'arrays': {}}
    reports = []
    for directory in (reference, candidate):
        report = json.loads((directory / 'report.json').read_text())
        if (report.get('inference') != 'completed' or report.get('finite') is not True
                or report.get('exit_code', 0) != 0):
            raise ValueError(f'incomplete or invalid run: {directory}')
        reports.append(report)
    provenance = ('checkpoint_manifest_sha256', 'frame_sha256', 'frames', 'size')
    result['provenance_missing'] = [key for key in provenance if any(key not in report for report in reports)]
    for key in provenance:
        if key not in result['provenance_missing'] and reports[0][key] != reports[1][key]:
            raise ValueError(f'provenance mismatch: {key}')
    with np.load(reference / 'reconstruction.npz', allow_pickle=False) as ref, \
            np.load(candidate / 'reconstruction.npz', allow_pickle=False) as cand:
        if set(ref.files) != KEYS or set(cand.files) != KEYS:
            raise ValueError('output keys differ from required keys')
        for key in sorted(KEYS):
            a, b = ref[key], cand[key]
            if a.shape != b.shape:
                raise ValueError(f'{key}: output shapes differ')
            if not (np.isfinite(a).all() and np.isfinite(b).all()):
                raise ValueError(f'{key}: nonfinite output')
            difference = np.abs(a.astype(np.float64) - b.astype(np.float64))
            close = np.isclose(b, a, rtol=1e-4, atol=1e-5)
            result['arrays'][key] = {'max_abs_diff': float(difference.max(initial=0)),
                                     'failure_count': int(np.count_nonzero(~close))}
    result['passed'] = all(item['failure_count'] == 0 for item in result['arrays'].values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.reference, args.candidate)
    except (ValueError, OSError, KeyError) as exc:
        result = {'passed': False, 'error': str(exc)}
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
