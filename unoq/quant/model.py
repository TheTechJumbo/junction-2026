"""Portable weight-only INT8: no CPU ISA/backend-specific quantized kernels."""
import hashlib
import json
from pathlib import Path
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file

REVISION = 'a288dd0f14786c93483e45524328726ab7b1b4ce'


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def quantize(weight):
    scale = weight.abs().amax(dim=1, keepdim=True).clamp_min(1e-12) / 127
    q = (weight / scale).round().clamp(-127, 127).to(torch.int8)
    return q, scale


class Int8Linear(nn.Module):
    def __init__(self, qweight, scale, bias=None):
        super().__init__()
        self.register_buffer('qweight', qweight)
        self.register_buffer('scale', scale)
        self.register_buffer('bias', bias)

    def forward(self, x):
        # Only this layer's float weights exist during this call; never cache them.
        weight = self.qweight.to(dtype=x.dtype) * self.scale
        return F.linear(x, weight, self.bias)


def skeleton():
    from vggt.models.vggt import VGGT
    from unittest.mock import patch
    linspace = torch.linspace
    # Upstream builds scalar drop-path rates with .item(); keep just these scalars on CPU.
    with patch('torch.linspace', lambda *a, **kw: linspace(*a, **{**kw, 'device': 'cpu'})), torch.device('meta'):
        return VGGT(enable_point=False, enable_track=False).eval()


def load(root):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest['revision'] != REVISION or manifest['format'] != 'junction-int8-v1':
        raise ValueError('incompatible manifest')
    model = skeleton()
    for name, info in manifest['tensors'].items():
        path = root / info['file']
        if path.parent != root or digest(path) != info['sha256']:
            raise ValueError('checkpoint integrity failure')
        tensors = load_file(str(path))
        if info['quantized']:
            parent_name, _, attr = name.rpartition('.')
            parent = model.get_submodule(parent_name) if parent_name else model
            old = getattr(parent, attr)
            if not isinstance(old, nn.Linear) or tuple(tensors['qweight'].shape) != tuple(old.weight.shape):
                raise ValueError('unexpected quantized module')
            setattr(parent, attr, Int8Linear(tensors['qweight'], tensors['scale'], tensors.get('bias')))
        else:
            parent_name, _, attr = name.rpartition('.')
            parent = model.get_submodule(parent_name) if parent_name else model
            old = getattr(parent, attr)
            value = tensors['value']
            if value.shape != old.shape:
                raise ValueError('unexpected tensor shape')
            setattr(parent, attr, nn.Parameter(value, requires_grad=False) if isinstance(old, nn.Parameter) else value)
    # Non-persistent constants are not in the upstream checkpoint.
    model.aggregator._resnet_mean = torch.tensor([.485, .456, .406]).view(1, 1, 3, 1, 1)
    model.aggregator._resnet_std = torch.tensor([.229, .224, .225]).view(1, 1, 3, 1, 1)
    if any(t.is_meta for t in list(model.parameters()) + list(model.buffers())):
        raise ValueError('checkpoint incomplete')
    return model.eval()
