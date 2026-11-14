"""Offline regression checks for optional Rust and deployment helpers."""
import os
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import unittest

ROOT = Path(__file__).resolve().parent

class RolloutTests(unittest.TestCase):
    def python(self, source, env=None):
        return subprocess.run([sys.executable, '-c', source], cwd=ROOT, env={**os.environ, **(env or {})},
                              text=True, capture_output=True)

    def test_missing_helper_and_unrelated_failure(self):
        prefix = '''import builtins
real = builtins.__import__
def fake(name, *a, **kw):
    if name == "recon_rust":
        raise ModuleNotFoundError("missing", name=NAME)
    return real(name, *a, **kw)
builtins.__import__ = fake
'''
        missing = self.python('NAME="recon_rust"\n' + prefix + 'import recon_change; assert not recon_change.recon_rust.enabled()')
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertIn('using NumPy', missing.stderr)
        unrelated = self.python('NAME="unrelated_dependency"\n' + prefix + 'import recon_change')
        self.assertNotEqual(unrelated.returncode, 0)
        self.assertIn('ModuleNotFoundError', unrelated.stderr)

    def test_extension_missing_and_disabled(self):
        code = '''import builtins
real=builtins.__import__
def fake(name,*a,**kw):
    if name == "recon_fast": raise ModuleNotFoundError("missing", name="recon_fast")
    return real(name,*a,**kw)
builtins.__import__=fake
import recon_rust
assert not recon_rust.enabled()
'''
        for env in ({}, {'RECON_FAST': '0'}):
            result = self.python(code, env)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_extension_abi_import_error_falls_back(self):
        code = """import builtins
real=builtins.__import__
def fake(name,*a,**kw):
    if name == 'recon_fast': raise ImportError('incompatible binary')
    return real(name,*a,**kw)
builtins.__import__=fake
import recon_rust
assert not recon_rust.enabled()
"""
        result = self.python(code, {'RECON_FAST': '1'})
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(importlib.util.find_spec("recon_fast"), "optional Rust extension not installed")
    def test_rust_on_and_off(self):
        on = self.python('import recon_rust; assert recon_rust.enabled()', {'RECON_FAST': '1'})
        self.assertEqual(on.returncode, 0, on.stderr)
        off = self.python('import recon_rust; assert not recon_rust.enabled()', {'RECON_FAST': '0'})
        self.assertEqual(off.returncode, 0, off.stderr)

    def test_preflight_fails_before_any_restart(self):
        result = subprocess.run([sys.executable, str(ROOT/'iridis/preflight.py')], cwd=ROOT,
                                env={'PATH': os.environ['PATH'], 'HF_HOME': '/nonexistent'}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing setting:', result.stderr)

    def test_preflight_missing_source_and_rust(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'iridis'
            directory.mkdir()
            shutil.copy(ROOT / 'iridis/preflight.py', directory)
            result = subprocess.run([sys.executable, str(directory / 'preflight.py')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('missing source: recon_change.py', result.stderr)
        code = """import builtins, runpy
real = builtins.__import__
def fake(name, *a, **kw):
    if name == 'recon_fast': raise ModuleNotFoundError('missing', name='recon_fast')
    return real(name, *a, **kw)
builtins.__import__ = fake
runpy.run_path('iridis/preflight.py', run_name='__main__')
"""
        result = self.python(code)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('recon_fast:', result.stderr)

    def test_remote_root_quoted(self):
        sys.path.insert(0, str(ROOT/'live'))
        import relay
        self.assertIn("'", relay.remote_command('/tmp/worker; touch /tmp/no'))
        with self.assertRaises(ValueError): relay.remote_command('relative')

if __name__ == '__main__': unittest.main()
