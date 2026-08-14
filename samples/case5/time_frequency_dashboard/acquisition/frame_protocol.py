"""Binary frame protocol shared by the sigrok bridge and Python pipeline.

The C bridge writes ``BridgeFrameV1`` records to stdout. This module is the
single Python definition of that small transport boundary; processing, storage,
and the synthetic source only depend on the resulting :class:`BridgeFrame`.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
import struct
from typing import List

import numpy as np


MAGIC = b"C5BF"
VERSION = 1
HEADER = struct.Struct("<4sHHQQdIHHI")
MAX_FRAME_SAMPLES = 1_000_000

# Bit zero is common to every acquisition implementation. Higher bits remain
# available for future source-specific diagnostics.
FRAME_FLAG_CLIPPED = 1 << 0


@dataclass(frozen=True)
class BridgeFrame:
    """A real two-channel block with host-side transport metadata."""

    sequence: int
    host_receive_ns: int
    sample_rate_hz: float
    flags: int
    samples: np.ndarray

    def __post_init__(self) -> None:
        values = np.asarray(self.samples, dtype=np.float32)
        if values.ndim != 2 or values.shape[1] != 2:
            raise ValueError("BridgeFrame samples must have shape [samples, 2]")
        if values.shape[0] == 0:
            raise ValueError("BridgeFrame must contain at least one sample")
        if not np.isfinite(values).all():
            raise ValueError("BridgeFrame samples must be finite")
        if not np.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("BridgeFrame sample rate must be finite and positive")
        if (
            not isinstance(self.sequence, Integral)
            or isinstance(self.sequence, bool)
            or not 0 <= self.sequence <= (2**64 - 1)
        ):
            raise ValueError("BridgeFrame sequence is outside the uint64 range")
        if (
            not isinstance(self.host_receive_ns, Integral)
            or isinstance(self.host_receive_ns, bool)
            or not 0 <= self.host_receive_ns <= (2**64 - 1)
        ):
            raise ValueError("BridgeFrame host_receive_ns is outside the uint64 range")
        if (
            not isinstance(self.flags, Integral)
            or isinstance(self.flags, bool)
            or not 0 <= self.flags <= (2**16 - 1)
        ):
            raise ValueError("BridgeFrame flags are outside the uint16 range")
        object.__setattr__(self, "samples", np.ascontiguousarray(values))

    @property
    def sample_count(self) -> int:
        return int(self.samples.shape[0])

    def to_bytes(self) -> bytes:
        """Serialize the stable little-endian BridgeFrameV1 representation."""

        payload = self.samples.astype("<f4", copy=False).tobytes(order="C")
        header = HEADER.pack(
            MAGIC,
            VERSION,
            HEADER.size,
            int(self.sequence),
            int(self.host_receive_ns),
            float(self.sample_rate_hz),
            self.sample_count,
            2,
            int(self.flags),
            len(payload),
        )
        return header + payload


class FrameStreamDecoder:
    """Incrementally decode arbitrary stdout chunks from the sigrok bridge."""

    def __init__(self, *, max_frame_samples: int = MAX_FRAME_SAMPLES) -> None:
        if (
            not isinstance(max_frame_samples, int)
            or isinstance(max_frame_samples, bool)
            or max_frame_samples <= 0
        ):
            raise ValueError("max_frame_samples must be a positive integer")
        self._max_frame_samples = max_frame_samples
        self._buffer = bytearray()

    @property
    def pending_bytes(self) -> int:
        """Number of incomplete bytes retained after the last feed call."""
        return len(self._buffer)

    def feed(self, data: bytes) -> List[BridgeFrame]:
        self._buffer.extend(data)
        frames: List[BridgeFrame] = []
        while True:
            if len(self._buffer) < HEADER.size:
                return frames
            (
                magic,
                version,
                header_bytes,
                sequence,
                timestamp,
                rate,
                count,
                channels,
                flags,
                payload_bytes,
            ) = HEADER.unpack_from(self._buffer)
            if magic != MAGIC:
                raise ValueError("invalid bridge frame magic")
            if version != VERSION or header_bytes != HEADER.size:
                raise ValueError("unsupported bridge frame version")
            if channels != 2 or count == 0 or not np.isfinite(rate) or rate <= 0:
                raise ValueError("invalid bridge frame metadata")
            if count > self._max_frame_samples:
                raise ValueError(
                    f"bridge frame sample count {count} exceeds safety limit "
                    f"{self._max_frame_samples}"
                )
            expected_bytes = count * channels * np.dtype("<f4").itemsize
            if payload_bytes != expected_bytes:
                raise ValueError("bridge frame payload length mismatch")
            packet_bytes = HEADER.size + payload_bytes
            if len(self._buffer) < packet_bytes:
                return frames
            payload = bytes(self._buffer[HEADER.size:packet_bytes])
            samples = np.frombuffer(payload, dtype="<f4").reshape(count, channels).copy()
            del self._buffer[:packet_bytes]
            frames.append(
                BridgeFrame(
                    sequence=sequence,
                    host_receive_ns=timestamp,
                    sample_rate_hz=rate,
                    flags=flags,
                    samples=samples,
                )
            )
