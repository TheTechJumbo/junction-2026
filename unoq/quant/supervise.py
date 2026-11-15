"""Record failures outside the limited inference cgroup."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

args = sys.argv[1:]
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--threads', type=int, choices=(1, 2, 4), default=2)
options, _ = parser.parse_known_args(args)
available_cpus = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count()
if available_cpus is not None and options.threads > available_cpus:
    sys.exit(f'--threads exceeds available CPU count ({available_cpus})')
if '--out' not in args:
    sys.exit('--out is required')
out = Path(args[args.index('--out')+1])
out.mkdir(parents=True, exist_ok=True, mode=0o700)
report = out / 'report.json'
if report.exists():
    sys.exit('Use a fresh output directory: report.json already exists')
start = time.monotonic()
command = ['systemd-run', '--setenv=PYTHONPATH=/home/arduino/junction-vggt', '--setenv=HF_HUB_OFFLINE=1', f'--setenv=OMP_NUM_THREADS={options.threads}', f'--setenv=OPENBLAS_NUM_THREADS={options.threads}', '--user', '--wait', '--pipe', '--collect', f'--unit=junction-vggt-probe-{os.getpid()}',
           '-p', 'UMask=0077', '-p', 'NoNewPrivileges=yes', '-p', 'MemoryMax=2800M', '-p', 'MemorySwapMax=0', '-p', 'RuntimeMaxSec=600', '-p', 'TimeoutStopSec=5',
           '/home/arduino/junction-vggt-env/bin/python', str(Path(__file__).resolve().parent / 'run.py'), *args]
result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
(out / 'supervisor.log').write_text(result.stdout)
elapsed = time.monotonic()-start
if not report.exists():
    reason = 'memory_limit' if 'oom-kill' in result.stdout else 'timeout' if 'result: timeout' in result.stdout else 'runtime_error'
    report.write_text(json.dumps(dict(inference='failed', reason=reason, exit_code=result.returncode,
                                     elapsed_seconds=elapsed), indent=2))
else:
    data = json.loads(report.read_text())
    data['supervisor_wall_seconds'] = elapsed
    if result.returncode != 0:
        data.update(inference='failed', reason='runtime_error', exit_code=result.returncode)
    report.write_text(json.dumps(data, indent=2))
print(report.read_text())
sys.exit(result.returncode)
