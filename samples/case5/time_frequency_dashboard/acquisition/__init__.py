"""The sigrok and synthetic inputs used by the current teaching case."""

from __future__ import annotations

from typing import Callable

from .frame_protocol import BridgeFrame


FrameCallback = Callable[[BridgeFrame], None]
ErrorCallback = Callable[[str], None]

from .sigrok import SigrokCapture
from .synthetic import SyntheticCapture

__all__ = [
    "ErrorCallback",
    "FrameCallback",
    "BridgeFrame",
    "SigrokCapture",
    "SyntheticCapture",
]
