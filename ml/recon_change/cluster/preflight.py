#!/usr/bin/env python3
"""Offline, read-only worker preflight. Run from the persistent worker checkout."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--expect-rust', choices=('on', 'off'), default='on')
args = parser.parse_args()
errors = []
for name in ('recon_change.py', 'recon_rust.py', 'locate.py', 'items.py', 'objects.py',
             'watch.py', 'splat.py', 'live/worker.py', 'live/gateway.py', 'cluster/worker_env.sh'):
    if not (ROOT / name).is_file():
        errors.append(f'missing source: {name}')
for name in ('numpy', 'cv2', 'torch', 'vggt', 'huggingface_hub', 'recon_rust') + (('recon_fast',) if args.expect_rust == 'on' else ()):
    try:
        importlib.import_module(name)
    except Exception as exc:
        errors.append(f'{name}: {type(exc).__name__}: {exc}')
try:
    import recon_rust
    if args.expect_rust == 'on' and (not recon_rust.enabled() or recon_rust.OFF):
        errors.append('Rust is not fully enabled')
    if args.expect_rust == 'off' and recon_rust.enabled():
        errors.append('Rust is enabled; set RECON_FAST=0 for NumPy rollback')
except Exception:
    pass
for key in ('RECON_ITEMS_ENV', 'RECON_ITEMS_LIMITS', 'RECON_ITEMS_LOOK_FROM', 'RECON_ITEMS_JUDGE',
            'RECON_ITEMS_JUDGE_LIMITS', 'RECON_ITEMS_CANDIDATES'):
    if not os.environ.get(key):
        errors.append(f'missing setting: {key}')
try:
    from huggingface_hub import snapshot_download
    import items
    if not items.wanted():
        errors.append('RECON_ITEMS is empty; item detector would be disabled')
    importlib.import_module('transformers')
    for model, cache in [('facebook/VGGT-1B', None), (items.MODEL_ID, items.CACHE), (items.JUDGE, items.CACHE)]:
        if model:
            path = Path(snapshot_download(model, cache_dir=cache, local_files_only=True))
            if not (path / 'config.json').is_file() or not any(path.glob('*.safetensors')):
                errors.append(f'incomplete cached model: {model}')
            for index in path.glob('*.safetensors.index.json'):
                shards = set(json.loads(index.read_text()).get('weight_map', {}).values())
                if not shards or any(not (path / shard).is_file() for shard in shards):
                    errors.append(f'incomplete model shards: {model}')
            if model != 'facebook/VGGT-1B':
                from transformers import AutoTokenizer
                AutoTokenizer.from_pretrained(model, cache_dir=cache, local_files_only=True)
except Exception as exc:
    errors.append(f'item dependencies or model cache: {type(exc).__name__}: {exc}')
if errors:
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(1)
print(f'Offline preflight passed: sources, settings, imports, Rust {args.expect_rust}, and model cache inventory present')
