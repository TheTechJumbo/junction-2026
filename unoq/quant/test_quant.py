import tempfile
import unittest
from pathlib import Path
import torch
from safetensors.torch import save_file, load_file
from model import Int8Linear, quantize, digest


class QuantTests(unittest.TestCase):
    def test_roundtrip_and_layer_error(self):
        torch.manual_seed(0)
        w, x = torch.randn(32, 64), torch.randn(4, 64)
        q, scale = quantize(w)
        self.assertEqual(q.dtype, torch.int8)
        self.assertTrue(torch.all((w-q.float()*scale).abs() <= scale/2 + 1e-6))
        layer = Int8Linear(q, scale)
        relative = torch.linalg.vector_norm(layer(x)-x@w.T)/torch.linalg.vector_norm(x@w.T)
        self.assertLess(relative.item(), .02)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'layer.safetensors'
            save_file({'qweight':q,'scale':scale},str(path))
            before=digest(path)
            tensors=load_file(str(path))
            self.assertTrue(torch.equal(Int8Linear(**tensors)(x),layer(x)))
            with path.open('ab') as f: f.write(b'corrupt')
            self.assertNotEqual(digest(path),before)

    def test_zero_rows_bias_and_no_cache(self):
        q, s = quantize(torch.zeros(3, 4))
        layer=Int8Linear(q,s,torch.ones(3))
        self.assertTrue(torch.equal(layer(torch.ones(2,4)),torch.ones(2,3)))
        self.assertEqual(set(layer.state_dict()), {'qweight','scale','bias'})

    def test_manifest_integrity_and_missing_tensor(self):
        import json
        from unittest.mock import patch
        import model
        class Tiny(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.aggregator = torch.nn.Linear(4, 3, bias=False, device='meta')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            q, scale = quantize(torch.randn(3, 4))
            path = root / '0.safetensors'
            save_file({'qweight':q,'scale':scale},str(path))
            info = dict(file=path.name,sha256=digest(path),quantized=True)
            manifest = dict(format='junction-int8-v1',revision=model.REVISION,tensors={'aggregator':info})
            (root/'manifest.json').write_text(json.dumps(manifest))
            with patch.object(model,'skeleton',Tiny):
                loaded=model.load(root)
                self.assertFalse(any(t.is_meta for t in loaded.buffers()))
                self.assertEqual(loaded.aggregator(torch.ones(1,4)).shape, (1,3))
                empty={**manifest, 'tensors':{}}
                (root/'manifest.json').write_text(json.dumps(empty))
                with self.assertRaisesRegex(ValueError,'incomplete'):
                    model.load(root)
                (root/'manifest.json').write_text(json.dumps(manifest))
                with path.open('ab') as f:f.write(b'corrupt')
                with self.assertRaisesRegex(ValueError,'integrity'):
                    model.load(root)


if __name__=='__main__': unittest.main()
