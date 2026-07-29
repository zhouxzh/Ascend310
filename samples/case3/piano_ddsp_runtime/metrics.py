"""Bounded realtime latency and transport metrics."""

from __future__ import annotations

from collections import deque
import threading
import time
from typing import Iterable

import numpy as np


def percentile(values: Iterable[float], quantile: float) -> float:
    samples = tuple(values)
    return float(np.quantile(samples, quantile)) if samples else 0.0


class RuntimeMetrics:
    def __init__(self, capacity: int = 10_000) -> None:
        self.npu_ms: deque[float] = deque(maxlen=capacity)
        self.dsp_ms: deque[float] = deque(maxlen=capacity)
        self.block_ms: deque[float] = deque(maxlen=capacity)
        self.write_ms: deque[float] = deque(maxlen=capacity)
        self.midi_to_pcm_ms: deque[float] = deque(maxlen=capacity)
        self.rendered_blocks = 0
        self.played_blocks = 0
        self.underruns = 0
        self.overruns = 0
        self.clipped_samples = 0
        self.monitor_drops = 0
        self.started_at = time.monotonic()
        self._lock = threading.Lock()

    def add(self, name: str, value: float) -> None:
        with self._lock:
            target = getattr(self, name)
            target.append(float(value))

    def add_many(self, name: str, values: Iterable[float]) -> None:
        samples = tuple(float(value) for value in values)
        if not samples:
            return
        with self._lock:
            target = getattr(self, name)
            target.extend(samples)

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            setattr(self, name, int(getattr(self, name)) + int(value))

    def snapshot(
        self,
        *,
        buffered_blocks: int = 0,
        block_duration_ms: float = 0.0,
        device_latency_ms: float = 0.0,
        sink_latency_ms: float = 0.0,
        resampler_latency_ms: float = 0.0,
    ) -> dict[str, object]:
        with self._lock:
            npu = tuple(self.npu_ms)
            dsp = tuple(self.dsp_ms)
            blocks = tuple(self.block_ms)
            writes = tuple(self.write_ms)
            midi = tuple(self.midi_to_pcm_ms)
            counters = {
                name: int(getattr(self, name))
                for name in (
                    "rendered_blocks",
                    "played_blocks",
                    "underruns",
                    "overruns",
                    "clipped_samples",
                    "monitor_drops",
                )
            }
        queue_latency = buffered_blocks * block_duration_ms
        return {
            **counters,
            "npu_samples": len(npu),
            "npu_p50_ms": percentile(npu, 0.50),
            "npu_p95_ms": percentile(npu, 0.95),
            "npu_p99_ms": percentile(npu, 0.99),
            "dsp_p50_ms": percentile(dsp, 0.50),
            "dsp_p95_ms": percentile(dsp, 0.95),
            "dsp_p99_ms": percentile(dsp, 0.99),
            "block_p50_ms": percentile(blocks, 0.50),
            "block_p95_ms": percentile(blocks, 0.95),
            "block_p99_ms": percentile(blocks, 0.99),
            "write_p95_ms": percentile(writes, 0.95),
            "midi_to_pcm_p95_ms": percentile(midi, 0.95),
            "buffered_blocks": buffered_blocks,
            "queue_latency_ms": queue_latency,
            "device_latency_ms": device_latency_ms,
            "sink_latency_ms": sink_latency_ms,
            "resampler_latency_ms": resampler_latency_ms,
            "estimated_total_latency_ms": (
                percentile(midi, 0.95)
                + queue_latency
                + device_latency_ms
                + sink_latency_ms
                + resampler_latency_ms
            ),
            "uptime_seconds": max(0.0, time.monotonic() - self.started_at),
        }
