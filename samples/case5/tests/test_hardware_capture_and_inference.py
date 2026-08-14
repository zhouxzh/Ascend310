"""Opt-in Ascend 310B integration test for real USB capture plus OM inference."""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time

import numpy as np
import pytest

from time_frequency_dashboard.acquisition import SigrokCapture
from time_frequency_dashboard.config import Case5Config
from time_frequency_dashboard.npu import AnalysisService, AscendOmRunner
from time_frequency_dashboard.processing import AnalysisWindow


@pytest.mark.hardware
def test_real_scope_capture_then_npu_inference():
    """Capture a real window first, then prove that the same window reaches the NPU."""
    if os.environ.get("CASE5_RUN_HARDWARE_TESTS") != "1":
        pytest.skip("set CASE5_RUN_HARDWARE_TESTS=1 on the Ascend 310B board")

    project_root = Path(__file__).resolve().parents[1]
    config = Case5Config()
    om_path = project_root / "models/generated/npu_dft_1ms_10000_20khz.om"
    if not om_path.is_file():
        pytest.fail(f"OM model not found: {om_path}; run prepare_models.py first")

    frames = []
    errors = []
    received = threading.Event()

    def on_frame(frame) -> None:
        frames.append(frame)
        if len(frames) >= 2:
            received.set()

    capture = SigrokCapture(
        sample_rate_hz=config.sample_rate_hz,
        frame_samples=config.analysis_samples,
        callback_msec=config.sigrok_callback_msec,
        ch1_volts_per_division=config.ch1_volts_per_division,
        ch2_volts_per_division=config.ch2_volts_per_division,
        ch1_probe_ratio=config.ch1_probe_ratio,
        ch2_probe_ratio=config.ch2_probe_ratio,
        bridge_path=project_root / "build/sigrok_capture_bridge",
        frame_callback=on_frame,
        error_callback=errors.append,
    )

    test_started = time.perf_counter()
    try:
        capture_started = time.perf_counter()
        capture.start()
        if not received.wait(timeout=10.0):
            detail = errors[-1] if errors else "no two analysis windows arrived within 10 seconds"
            raise RuntimeError(detail)
        capture_seconds = time.perf_counter() - capture_started
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if "Resource busy" in message or "LIBUSB_ERROR_BUSY" in message:
            pytest.fail(
                "real 6022BE capture failed because the USB interface is busy. "
                "Close the Case 5 dashboard, PulseView, and sigrok-cli, then rerun "
                "this pytest so it can own the scope exclusively."
            )
        pytest.fail(
            "real 6022BE capture failed: "
            f"{message}. "
            "Run python -m time_frequency_dashboard.acquisition.usb_diagnostics "
            "and check the udev writable=True result."
        )
    finally:
        capture.stop()

    assert errors == []
    assert len(frames) >= 2
    assert [frame.sequence for frame in frames[:2]] == [0, 1]
    raw = frames[0].samples
    assert raw.shape == (config.analysis_samples, 2)
    assert np.isfinite(raw).all(), "scope returned NaN or infinite samples"
    waveforms = raw.T.astype(np.float32, copy=False)
    waveforms = waveforms - waveforms.mean(axis=1, keepdims=True, dtype=np.float32)

    service = AnalysisService(
        AscendOmRunner(om_path),
        input_capacity=config.analysis_queue_capacity,
        result_capacity=config.result_queue_capacity,
    )
    npu_initialize_started = time.perf_counter()
    status = service.start()
    npu_initialize_seconds = time.perf_counter() - npu_initialize_started
    assert status.ready, f"OM/NPU worker initialization failed after capture: {status.message}"
    try:
        npu_inference_started = time.perf_counter()
        service.submit(
            AnalysisWindow(
                first_sequence=0,
                last_sequence=frames[0].sequence,
                start_host_ns=frames[0].host_receive_ns
                - int(1_000_000_000 * config.analysis_samples / config.sample_rate_hz),
                end_host_ns=frames[0].host_receive_ns,
                sample_rate_hz=config.sample_rate_hz,
                waveforms=waveforms,
            )
        )
        result = service.results.get(timeout=15.0)
        npu_inference_seconds = time.perf_counter() - npu_inference_started
    finally:
        service.close()

    assert result.status.ready, f"OM/NPU inference failed after capture: {result.status.message}"
    assert result.spectrum_power is not None
    output = result.spectrum_power
    assert output.shape == (1, 2, config.spectrum_bins, 1)
    assert np.isfinite(output).all(), "NPU returned NaN or infinite band energy"
    print(
        "hardware timing: "
        f"two_sigrok_frames={capture_seconds:.3f}s, "
        f"npu_initialize={npu_initialize_seconds:.3f}s, "
        f"npu_inference={npu_inference_seconds:.3f}s, "
        f"total={time.perf_counter() - test_started:.3f}s"
    )
