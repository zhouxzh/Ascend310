"""
Case 5: FPGA vibration data interface + mel-spectrogram generation.

Produces 128×128 mel-spectrograms from vibration waveforms for NPU
fault classification. Supports both real FPGA data and simulation.
"""

import random
import time

import numpy as np

from config import (
    FFT_WINDOW,
    HOP_LENGTH,
    N_MELS,
    NUM_MOTORS,
    SAMPLE_RATE,
    SPEC_SIZE,
    SPEC_TIME_STEPS,
)


class VibrationProcessor:
    """Receive vibration waveform, compute mel-spectrogram for NPU."""

    def __init__(self):
        self.use_fpga = False
        # Pre-compute mel filterbank (once)
        self._mel_basis = self._build_mel_filterbank(
            N_MELS, FFT_WINDOW // 2 + 1, SAMPLE_RATE
        )
        self._window = np.hanning(FFT_WINDOW).astype(np.float32)
        print("[VibrationProcessor] Mel filterbank ready "
              f"({N_MELS} bands × {FFT_WINDOW//2+1} bins)")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, motor_id=0):
        """Read one chunk of vibration data.

        With real FPGA: reads SPI buffer.
        Without FPGA: generates synthetic vibration with fault signature.

        Returns:
            waveform: np.ndarray shape (FFT_WINDOW * SPEC_TIME_STEPS,), float32
        """
        if self.use_fpga:
            return self._read_fpga(motor_id)
        else:
            return self._simulate(motor_id)

    def waveform_to_spectrogram(self, waveform):
        """Convert a vibration waveform to a mel-spectrogram image.

        Args:
            waveform: (samples,) float32 array

        Returns:
            spec_3ch: (SPEC_SIZE, SPEC_SIZE, 3) float32, normalized to [0,1]
        """
        # 1. STFT: frame → window → FFT → magnitude
        n_frames = (len(waveform) - FFT_WINDOW) // HOP_LENGTH + 1
        spec = np.zeros((FFT_WINDOW // 2 + 1, n_frames), dtype=np.float32)

        for i in range(n_frames):
            start = i * HOP_LENGTH
            frame = waveform[start:start + FFT_WINDOW] * self._window
            mag = np.abs(np.fft.rfft(frame))
            spec[:, i] = mag

        # 2. Apply mel filterbank
        mel_spec = np.dot(self._mel_basis, spec)  # (n_mels, n_frames)

        # 3. Log scale (avoid log(0))
        mel_spec = np.log(mel_spec + 1e-6)

        # 4. Normalize to [0, 1]
        mel_min = mel_spec.min()
        mel_max = mel_spec.max()
        if mel_max - mel_min > 1e-8:
            mel_spec = (mel_spec - mel_min) / (mel_max - mel_min)

        # 5. Resize to SPEC_SIZE × SPEC_SIZE via cropping / padding
        spec_img = self._resize_2d(mel_spec, SPEC_SIZE, SPEC_SIZE)

        # 6. Stack to 3 channels (grayscale → RGB)
        spec_3ch = np.stack([spec_img, spec_img, spec_img], axis=-1)

        return spec_3ch.astype(np.float32)

    def get_spectrogram_for_npu(self, motor_id=0):
        """Read vibration and return NPU-ready spectrogram.

        Returns:
            spec_bgr: (SPEC_SIZE, SPEC_SIZE, 3) uint8 image (BGR for cv2)
            spec_3ch: (SPEC_SIZE, SPEC_SIZE, 3) float32 [0,1]
        """
        waveform = self.read(motor_id)
        spec_float = self.waveform_to_spectrogram(waveform)

        # Also produce a color-mapped BGR image for display
        spec_bgr = self._colormap_for_display(spec_float[:, :, 0])

        return spec_bgr, spec_float

    # ------------------------------------------------------------------
    # Simulation — synthetic vibration with configurable fault signature
    # ------------------------------------------------------------------

    def _simulate(self, motor_id=0):
        """Generate synthetic vibration data for a motor.

        Different motor_id → slightly different base frequency.
        Occasional fault injection for testing.
        """
        num_samples = FFT_WINDOW * SPEC_TIME_STEPS  # 32768 samples

        # Base motor speed (different per motor)
        base_freq = 50 + motor_id * 3  # ~50 Hz fundamental (3000 RPM)

        t = np.arange(num_samples, dtype=np.float32) / SAMPLE_RATE

        # Fundamental + harmonics
        signal = (np.sin(2 * np.pi * base_freq * t) * 1.0
                  + np.sin(2 * np.pi * base_freq * 2 * t) * 0.3
                  + np.sin(2 * np.pi * base_freq * 3 * t) * 0.15)

        # Random noise floor
        signal += np.random.randn(num_samples).astype(np.float32) * 0.05

        # Occasionally inject a fault signature (2% chance)
        r = random.random()
        if r < 0.005:
            # Bearing wear: high-frequency noise
            signal += np.random.randn(num_samples).astype(np.float32) * 0.4
            # high-pass: amplify high frequencies
            signal += (np.sin(2 * np.pi * base_freq * 8 * t)
                       * np.random.uniform(0.3, 0.6))
        elif r < 0.01:
            # Unbalance: strong 1× peak
            signal += np.sin(2 * np.pi * base_freq * t) * 1.5
        elif r < 0.015:
            # Misalignment: strong 2× peak
            signal += np.sin(2 * np.pi * base_freq * 2 * t) * 1.2
        elif r < 0.02:
            # Looseness: multiple harmonics
            for h in range(1, 6):
                amp = 0.5 / h
                signal += np.sin(2 * np.pi * base_freq * h * t) * amp

        return signal.astype(np.float32)

    # ------------------------------------------------------------------
    # FPGA interface (placeholder — real implementation needs SPI driver)
    # ------------------------------------------------------------------

    def _read_fpga(self, motor_id=0):
        """Read vibration data from FPGA over SPI.

        This is a placeholder. Real implementation depends on the specific
        SPI interface and FPGA data format. Documented in fpga_interface.md.
        """
        raise NotImplementedError(
            "FPGA SPI read not implemented — use simulation mode. "
            "See stm32_protocol.md for hardware integration guide."
        )

    # ------------------------------------------------------------------
    # Mel filterbank (pure numpy)
    # ------------------------------------------------------------------

    @staticmethod
    def _hz_to_mel(hz):
        return 2595.0 * np.log10(1.0 + hz / 700.0)

    @staticmethod
    def _mel_to_hz(mel):
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    @classmethod
    def _build_mel_filterbank(cls, n_mels, n_fft_bins, sample_rate):
        """Build a mel-scale filterbank matrix.

        Returns:
            (n_mels, n_fft_bins) float32 matrix
        """
        f_min = 0.0
        f_max = sample_rate / 2.0

        mel_min = cls._hz_to_mel(f_min)
        mel_max = cls._hz_to_mel(f_max)
        mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
        hz_points = cls._mel_to_hz(mel_points)

        # Convert Hz to FFT bin indices
        bin_points = np.floor((n_fft_bins - 1) * hz_points / f_max).astype(int)

        filters = np.zeros((n_mels, n_fft_bins), dtype=np.float32)
        for m in range(n_mels):
            start = bin_points[m]
            center = bin_points[m + 1]
            end = bin_points[m + 2]

            # Rising edge
            if center > start:
                filters[m, start:center] = (
                    np.arange(start, center) - start
                ) / (center - start)
            # Falling edge
            if end > center:
                filters[m, center:end] = (
                    end - np.arange(center, end)
                ) / (end - center)

        return filters

    # ------------------------------------------------------------------
    # Image utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _resize_2d(array, target_h, target_w):
        """Simple 2D resize via cropping/padding (no scipy needed)."""
        h, w = array.shape

        # Crop or pad height
        if h > target_h:
            start = (h - target_h) // 2
            array = array[start:start + target_h, :]
        elif h < target_h:
            pad_top = (target_h - h) // 2
            pad_bot = target_h - h - pad_top
            array = np.pad(array, ((pad_top, pad_bot), (0, 0)),
                           mode="constant")

        # Crop or pad width
        if w > target_w:
            start = (w - target_w) // 2
            array = array[:, start:start + target_w]
        elif w < target_w:
            pad_left = (target_w - w) // 2
            pad_right = target_w - w - pad_left
            array = np.pad(array, ((0, 0), (pad_left, pad_right)),
                           mode="constant")

        return array

    @staticmethod
    def _colormap_for_display(gray_spec):
        """Apply JET colormap for visualization, return BGR uint8."""
        # Normalize to [0, 255]
        gray_uint8 = (gray_spec * 255).astype(np.uint8)
        # Apply OpenCV colormap if available, else return grayscale
        try:
            import cv2
            colored = cv2.applyColorMap(gray_uint8, cv2.COLORMAP_JET)
            return colored
        except ImportError:
            return cv2.cvtColor(gray_uint8, cv2.COLOR_GRAY2BGR)
