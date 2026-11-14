"""Record failures outside the limited inference cgroup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

args = sys.argv[1:]
if '--out' not in args:
    sys.exit('--out is required')
out = Path(args[args.index('--out')+1])
out.mkdir(parents=True, exist_ok=True, mode=0o700)
report = out / 'report.json'
if report.exists():
    sys.exit('Use a fresh output directory: report.json already exists')
start = time.monotonic()
command = ['systemd-run', '--setenv=PYTHONPATH=/home/arduino/junction-vggt', '--setenv=HF_HUB_OFFLINE=1', '--setenv=OMP_NUM_THREADS=2', '--setenv=OPENBLAS_NUM_THREADS=2', '--user', '--wait', '--pipe', '--collect', '--unit=junction-vggt-probe',
           '-p', 'UMask=0077', '-p', 'NoNewPrivileges=yes', '-p', 'MemoryMax=2800M', '-p', 'MemorySwapMax=0', '-p', 'RuntimeMaxSec=600', '-p', 'TimeoutStopSec=5',
           '/home/arduino/junction-vggt-env/bin/python', '/home/arduino/junction/unoq/quant/run.py', *args]
result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
(out / 'supervisor.log').write_text(result.stdout)
if not report.exists():
    reason = 'memory_limit' if 'oom-kill' in result.stdout else 'timeout' if 'result: timeout' in result.stdout else 'runtime_error'
    report.write_text(json.dumps(dict(inference='failed', reason=reason, exit_code=result.returncode,
                                     elapsed_seconds=time.monotonic()-start), indent=2))
print(report.read_text())
sys.exit(result.returncode)
