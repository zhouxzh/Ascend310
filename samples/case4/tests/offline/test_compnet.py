from __future__ import annotations

import importlib.util
import unittest

import numpy as np


TORCH_AVAILABLE = importlib.util.find_spec("torch") is not None
if TORCH_AVAILABLE:
    import torch

    from tools.export.compnet_static import build_static_compnet
else:
    torch = None
    build_static_compnet = None


@unittest.skipUnless(
    TORCH_AVAILABLE,
    "PyTorch is optional at runtime; CompNet export tests require torch.",
)
class CompNetTests(unittest.TestCase):
    def test_static_compnet_contract(self):
        model, official = build_static_compnet(None, 20260814)
        self.assertFalse(official)
        with torch.no_grad():
            output = model(torch.zeros(1, 1, 128, 128)).numpy()
        self.assertEqual(output.shape, (1, 512))
        self.assertTrue(np.all(np.isfinite(output)))
        self.assertTrue(np.allclose(np.linalg.norm(output, axis=1), 1.0, atol=1e-5))


if __name__ == "__main__":
    unittest.main()
