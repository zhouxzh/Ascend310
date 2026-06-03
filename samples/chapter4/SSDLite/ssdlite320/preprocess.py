from __future__ import annotations

import numpy as np

from .config import DEFAULT_IMAGE_SIZE

IMAGENET_MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
IMAGENET_STD = np.asarray([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)


def preprocess_image(image, img_size: int = DEFAULT_IMAGE_SIZE) -> tuple[np.ndarray, np.ndarray]:
    if image.mode != "RGB":
        image = image.convert("RGB")

    resized_image = image.resize((img_size, img_size))
    image_chw = np.asarray(resized_image, dtype=np.float32).transpose(2, 0, 1) / 255.0
    image_chw = (image_chw - IMAGENET_MEAN) / IMAGENET_STD
    image_batch = np.expand_dims(image_chw, axis=0).astype(np.float32, copy=False)
    return np.ascontiguousarray(image_batch), image_chw.astype(np.float32, copy=False)
