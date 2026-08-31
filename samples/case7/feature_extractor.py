"""Backward-compatible ResNet50 feature extractor.

Production code uses :mod:`embedding_backend`. This adapter preserves the
original case7 import surface for offline examples without permitting an
implicit CPU fallback.
"""

from __future__ import annotations

import numpy as np

from embedding_backend import ModelManager, RESNET50_ID, l2_normalize


class FeatureExtractor:
    def __init__(self, backend="npu", manager=None):
        if backend not in ("npu", "cpu"):
            raise ValueError("backend must be 'npu' or 'cpu'")
        self.backend = backend
        self.use_npu = backend == "npu"
        self._manager = manager
        self._torch_model = None
        if self.use_npu:
            self._manager = manager or ModelManager()
            self._backend = self._manager.get(RESNET50_ID)
        else:
            self._init_cpu()

    def _init_cpu(self):
        import torch
        import torchvision.models as models

        self._torch = torch
        self._torch_model = models.resnet50(weights=models.ResNet50_Weights.IMAGENET1K_V1)
        self._torch_model.fc = torch.nn.Identity()
        self._torch_model.eval()

    @staticmethod
    def _preprocess(image_bgr):
        import cv2

        image = cv2.resize(image_bgr, (224, 224))
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        image = (image - np.asarray([0.485, 0.456, 0.406], np.float32)) / np.asarray(
            [0.229, 0.224, 0.225], np.float32
        )
        return np.ascontiguousarray(image.transpose(2, 0, 1)[None, ...])

    def extract(self, image_bgr):
        if self.use_npu:
            return self._backend.encode_image(image_bgr)
        with self._torch.no_grad():
            output = self._torch_model(self._torch.from_numpy(self._preprocess(image_bgr)))
        return l2_normalize(output.squeeze(0).numpy())

    def extract_batch(self, images_bgr_list):
        if not images_bgr_list:
            return np.empty((0, 2048), dtype=np.float32)
        return np.stack([self.extract(image) for image in images_bgr_list], axis=0)

    def release(self):
        if self.use_npu and self._manager is not None:
            self._manager.release()
