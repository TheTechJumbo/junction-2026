"""Bounded CPU VGGT benchmark; uses local weights only, never uploads images.

Prepare a venv with torch, pillow and VGGT upstream, then pass local safetensors
and a directory of public/synthetic JPEGs. Default mode is a dependency preflight.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time


def preflight():
    memory = {}
    if Path('/proc/meminfo').exists():
        memory = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:', 'MemAvailable:'))}
    return {'architecture': platform.machine(), 'python': platform.python_version(), **memory,
            'dependencies': {x: importlib.util.find_spec(x) is not None for x in ('torch', 'vggt', 'PIL', 'safetensors')},
            'runtime': 'CPU; existing reference uses NVIDIA CUDA, not the Adreno GPU',
            'inference': 'not tested'}


def worker(args):
    import torch
    from PIL import Image
    import numpy as np
    from safetensors.torch import load_file
    from vggt.models.vggt import VGGT
    torch.set_num_threads(2)
    start = time.monotonic()
    # Construct on meta to avoid an extra full-sized allocation during weight loading.
    with torch.device('meta'):
        model = VGGT()
    weights = load_file(args.weights, device='cpu')
    size = sum(t.numel() * t.element_size() for t in weights.values())
    available = preflight().get('MemAvailable', 0)
    if size + 768 * 1024**2 > available:
        print(json.dumps({'inference': 'blocked', 'reason': 'weights plus 768 MiB minimum headroom exceed available RAM', 'weight_bytes': size}))
        return
    model.load_state_dict(weights, assign=True)
    model.eval()
    del weights
    frames = sorted(Path(args.frames).glob('*.jpg'))[:args.count]
    if len(frames) < args.count:
        raise ValueError('not enough JPEG frames')
    # 224 is divisible by VGGT patch size 14. This is a feasibility probe, not quality validation.
    images = torch.stack([torch.from_numpy(np.array(Image.open(p).convert('RGB').resize((224, 224)))).permute(2, 0, 1).float() / 255 for p in frames])
    with torch.inference_mode():
        prediction = model(images)
    valid = all(k in prediction and torch.isfinite(prediction[k]).all().item() for k in ('depth', 'depth_conf', 'pose_enc'))
    print(json.dumps({'inference': 'completed', 'valid_outputs': valid, 'frames': len(frames), 'elapsed_seconds': time.monotonic() - start,
                      'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--quantized', action='store_true', help='print the bounded INT8 runner instructions')
    p.add_argument('--weights')
    p.add_argument('--frames')
    p.add_argument('--count', type=int, choices=(2, 3, 9), default=2)
    p.add_argument('--timeout', type=int, default=120)
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.quantized:
        print('Use quant/bounded.sh --checkpoint PATH --frames PATH --count 2 --size 224 --out PATH; see quant/README.md')
        return
    if args.worker:
        worker(args)
        return
    report = preflight()
    print(json.dumps(report, indent=2), flush=True)
    if not args.weights or not args.frames:
        return
    if not all(report['dependencies'].values()):
        sys.exit('Blocked: install missing ARM-compatible dependencies in an isolated environment first.')
    # Linux address-space cap, in addition to time limit. Never consume all board memory.
    def cap():
        maximum = min(2800 * 1024**2, report.get('MemAvailable', 2800 * 1024**2))
        resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))
    try:
        result = subprocess.run([sys.executable, __file__, '--worker', '--weights', args.weights,
                                 '--frames', args.frames, '--count', str(args.count)],
                                timeout=args.timeout, preexec_fn=cap if sys.platform == 'linux' else None)
        sys.exit(result.returncode)
    except subprocess.TimeoutExpired:
        sys.exit('Blocked: benchmark exceeded time budget; no remote fallback attempted.')


if __name__ == '__main__':
    main()
