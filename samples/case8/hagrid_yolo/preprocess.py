from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class PreprocessInfo:
    scale: float
    pad_left: int
    pad_top: int


def letterbox(image, new_shape=640, color=(114, 114, 114)):
    height, width = image.shape[:2]
    scale = min(new_shape / height, new_shape / width)
    resized_width = int(round(width * scale))
    resized_height = int(round(height * scale))
    pad_w = new_shape - resized_width
    pad_h = new_shape - resized_height
    pad_left = pad_w // 2
    pad_top = pad_h // 2

    if width == resized_width and height == resized_height:
        resized = image
    else:
        resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)

    padded = cv2.copyMakeBorder(
        resized,
        pad_top,
        pad_h - pad_top,
        pad_left,
        pad_w - pad_left,
        cv2.BORDER_CONSTANT,
        value=color,
    )
    return padded, PreprocessInfo(scale=scale, pad_left=pad_left, pad_top=pad_top)


def preprocess_image(image, imgsz):
    padded, info = letterbox(image, imgsz)
    rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
    tensor = rgb.transpose(2, 0, 1).astype(np.float32) / 255.0
    tensor = np.expand_dims(tensor, axis=0)
    return tensor, info

