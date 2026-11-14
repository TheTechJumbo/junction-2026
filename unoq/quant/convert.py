"""Stream a public FP32 checkpoint into independently verified INT8 shards."""
import argparse
import json
from pathlib import Path
import torch
from safetensors import safe_open
from safetensors.torch import save_file
from model import REVISION, digest, quantize, skeleton


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--weights', required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args()
    torch.set_num_threads(2)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    model = skeleton()
    linear = {name: module for name, module in model.named_modules()
              if isinstance(module, torch.nn.Linear) and name.startswith('aggregator.')}
    state = model.state_dict()
    consumed = set()
    manifest = dict(format='junction-int8-v1', revision=REVISION,
                    source_sha256=digest(args.weights), tensors={})
    with safe_open(args.weights, framework='pt', device='cpu') as source:
        for name, module in linear.items():
            q, scale = quantize(source.get_tensor(name + '.weight'))
            tensors = {'qweight': q, 'scale': scale}
            consumed.add(name + '.weight')
            if module.bias is not None:
                tensors['bias'] = source.get_tensor(name + '.bias')
                consumed.add(name + '.bias')
            filename = str(len(manifest['tensors'])) + '.safetensors'
            save_file(tensors, str(out / filename))
            manifest['tensors'][name] = dict(file=filename, sha256=digest(out / filename), quantized=True)
        for name in state:
            if name in consumed:
                continue
            filename = str(len(manifest['tensors'])) + '.safetensors'
            save_file({'value': source.get_tensor(name)}, str(out / filename))
            manifest['tensors'][name] = dict(file=filename, sha256=digest(out / filename), quantized=False)
    manifest['artifact_bytes'] = sum(p.stat().st_size for p in out.glob('*.safetensors'))
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k: v for k, v in manifest.items() if k != 'tensors'}, indent=2))


if __name__ == '__main__':
    main()
