import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from compare_threads import compare

ROOT = Path(__file__).resolve().parent


class ThreadComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ref, self.cand = (Path(self.temp.name) / x for x in ('ref', 'cand'))
        for directory in (self.ref, self.cand):
            directory.mkdir()
            (directory / 'report.json').write_text(json.dumps({'inference': 'completed', 'finite': True}))
            np.savez(directory / 'reconstruction.npz', depth=[1.], depth_conf=[2.], pose_enc=[3.])

    def test_close_and_mismatch(self):
        self.assertTrue(compare(self.ref, self.cand)['passed'])
        np.savez(self.cand / 'reconstruction.npz', depth=[1.1], depth_conf=[2.], pose_enc=[3.])
        self.assertEqual(compare(self.ref, self.cand)['arrays']['depth']['failure_count'], 1)

    def test_invalid_reports_and_arrays(self):
        for report in ({'inference': 'failed', 'finite': True},
                       {'inference': 'completed', 'finite': False},
                       {'inference': 'completed', 'finite': True, 'exit_code': 1}):
            (self.cand / 'report.json').write_text(json.dumps(report))
            with self.assertRaises(ValueError): compare(self.ref, self.cand)
        (self.cand / 'report.json').write_text(json.dumps({'inference': 'completed', 'finite': True}))
        for arrays in ({'depth': [float('nan')], 'depth_conf': [2.], 'pose_enc': [3.]},
                       {'depth': [1., 2.], 'depth_conf': [2.], 'pose_enc': [3.]},
                       {'depth': [1.], 'pose_enc': [3.]}):
            np.savez(self.cand / 'reconstruction.npz', **arrays)
            with self.assertRaises(ValueError): compare(self.ref, self.cand)

    def test_provenance_mismatch_and_legacy(self):
        legacy = compare(self.ref, self.cand)
        self.assertIn('frame_sha256', legacy['provenance_missing'])
        common = {'inference': 'completed', 'finite': True, 'frames': 2, 'size': 224,
                  'frame_sha256': ['a', 'b'], 'checkpoint_manifest_sha256': 'checkpoint'}
        (self.ref / 'report.json').write_text(json.dumps(common))
        for changed in ({'frame_sha256': ['b', 'a']}, {'checkpoint_manifest_sha256': 'other'}):
            (self.cand / 'report.json').write_text(json.dumps({**common, **changed}))
            with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
                compare(self.ref, self.cand)
        (self.cand / 'report.json').write_text(json.dumps(common))
        self.assertEqual(compare(self.ref, self.cand)['provenance_missing'], [])

    def test_supervisor_invalidates_completed_report_on_failure(self):
        out = Path(self.temp.name) / 'failed'
        def fake_run(command, **kwargs):
            (out / 'report.json').write_text(json.dumps({'inference': 'completed', 'finite': True}))
            return SimpleNamespace(stdout='failed', returncode=1)
        with patch.object(sys, 'argv', ['supervise.py', '--out', str(out)]), \
                patch('subprocess.run', side_effect=fake_run), self.assertRaises(SystemExit):
            runpy.run_path(str(ROOT / 'supervise.py'), run_name='__main__')
        self.assertEqual(json.loads((out / 'report.json').read_text())['inference'], 'failed')
        with self.assertRaises(ValueError): compare(self.ref, out)

    def test_supervisor_propagates_threads_and_limits(self):
        for threads in (1, 2, 4):
            out = Path(self.temp.name) / str(threads)
            captured = []
            def fake_run(command, **kwargs):
                captured.extend(command)
                return SimpleNamespace(stdout='ok', returncode=1)
            with patch.object(sys, 'argv', ['supervise.py', '--out', str(out), '--threads', str(threads)]), \
                    patch('subprocess.run', side_effect=fake_run), self.assertRaises(SystemExit):
                runpy.run_path(str(ROOT / 'supervise.py'), run_name='__main__')
            self.assertIn(f'--setenv=OMP_NUM_THREADS={threads}', captured)
            self.assertIn(f'--setenv=OPENBLAS_NUM_THREADS={threads}', captured)
            self.assertEqual(captured[-2:], ['--threads', str(threads)])
            for limit in ('MemoryMax=2800M', 'MemorySwapMax=0', 'RuntimeMaxSec=600'):
                self.assertIn(limit, captured)

    def test_supervisor_rejects_invalid_thread_count(self):
        result = subprocess.run([sys.executable, str(ROOT / 'supervise.py'), '--threads', '3'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid choice', result.stderr)


if __name__ == '__main__': unittest.main()
