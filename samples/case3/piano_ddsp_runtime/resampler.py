"""Piano-DDSP-specific stateful sinc resampling."""

from __future__ import annotations

import numpy as np

from realtime_ddsp import WindowedSincResampler


class PianoSincResampler(WindowedSincResampler):
    """A lower-latency Hann-sinc kernel for 16 kHz Piano-DDSP audio."""

    NUM_CROSSINGS = 32
    TABLE_POINTS_PER_CROSSING = 100
    HISTORY_SIZE = NUM_CROSSINGS * 2
    _lookup_table = None

    def __init__(self, source_rate: int, target_rate: int) -> None:
        super().__init__(source_rate, target_rate)
        self._workspaces: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def prepare(self, input_size: int) -> None:
        super().prepare(input_size)
        self._workspace(int(input_size))

    def _workspace(self, input_size: int) -> tuple[np.ndarray, np.ndarray]:
        cached = self._workspaces.get(input_size)
        if cached is not None:
            return cached
        indices, _ = self._plan(input_size)
        workspace = (
            np.empty(self.HISTORY_SIZE + input_size, dtype=np.float32),
            np.empty(indices.shape, dtype=np.float32),
        )
        self._workspaces[input_size] = workspace
        return workspace

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return np.zeros(0, dtype=np.float32)
        indices, weights = self._plan(block.size)
        source, gathered = self._workspace(block.size)
        source[: self.HISTORY_SIZE] = self.history
        source[self.HISTORY_SIZE :] = block
        np.take(source, indices, out=gathered)
        output = np.einsum(
            "ij,ij->i",
            gathered,
            weights,
            optimize=False,
            dtype=np.float32,
        )
        self.history[:] = source[-self.HISTORY_SIZE :]
        return output.astype(np.float32, copy=False)
