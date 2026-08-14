import tempfile
import threading
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from time_frequency_dashboard.acquisition.frame_protocol import BridgeFrame, FrameStreamDecoder
from time_frequency_dashboard.acquisition.synthetic import SyntheticCapture
from time_frequency_dashboard.config import Case5Config
from time_frequency_dashboard.npu import AnalysisService, NpuStatus
from time_frequency_dashboard.model.soak_om_inference import timing_summary
from time_frequency_dashboard.processing import AnalysisWindow, CaptureProcessor, LatestQueue, WindowAssembler
from time_frequency_dashboard.session import SessionWriter


def frame(sequence, values, timestamp=1_000_000_000):
    return BridgeFrame(
        sequence=sequence,
        host_receive_ns=timestamp,
        sample_rate_hz=1_000.0,
        flags=0,
        samples=np.asarray(values, dtype=np.float32),
    )


def test_decoder_accepts_fragmented_packets():
    original = frame(3, [[1.0, -0.2], [0.5, 0.1]])
    decoder = FrameStreamDecoder()
    packet = original.to_bytes()
    assert decoder.feed(packet[:7]) == []
    decoded = decoder.feed(packet[7:])
    assert len(decoded) == 1
    assert decoded[0].sequence == 3
    np.testing.assert_allclose(decoded[0].samples, original.samples)


def test_bridge_frame_rejects_nonfinite_values_and_protocol_rejects_oversized_frame():
    with pytest.raises(ValueError, match="finite"):
        frame(1, [[float("nan"), 0.0]])

    decoder = FrameStreamDecoder(max_frame_samples=4)
    packet = BridgeFrame(
        sequence=0,
        host_receive_ns=1,
        sample_rate_hz=1_000.0,
        flags=0,
        samples=np.zeros((5, 2), dtype=np.float32),
    ).to_bytes()
    with pytest.raises(ValueError, match="safety limit"):
        decoder.feed(packet[:44])


def test_window_assembler_preserves_two_channels():
    assembler = WindowAssembler(4)
    first = frame(1, [[0, 10], [1, 11], [2, 12]])
    second = frame(2, [[3, 13], [4, 14], [5, 15]])
    assert assembler.push(first) == []
    windows = assembler.push(second)
    assert len(windows) == 1
    np.testing.assert_array_equal(windows[0].waveforms[0], [0, 1, 2, 3])
    np.testing.assert_array_equal(windows[0].waveforms[1], [10, 11, 12, 13])


def test_window_assembler_uses_latest_real_frame_timestamp_for_latency():
    assembler = WindowAssembler(4)
    assembler.push(frame(1, [[0, 0], [1, 1], [2, 2]], timestamp=1_000_000_000))
    window = assembler.push(frame(2, [[3, 3], [4, 4]], timestamp=2_500_000_000))[0]

    assert window.end_host_ns == 2_500_000_000
    assert window.start_host_ns == 2_496_000_000


def test_window_assembler_tracks_sequence_after_partial_continuous_frame():
    assembler = WindowAssembler(4)
    assert assembler.push(frame(1, [[0, 10], [1, 11], [2, 12]])) == []
    first = assembler.push(frame(2, [[3, 13], [4, 14], [5, 15]]))[0]
    second = assembler.push(frame(3, [[6, 16], [7, 17]]))[0]

    assert (first.first_sequence, first.last_sequence) == (1, 2)
    assert (second.first_sequence, second.last_sequence) == (2, 3)


def test_window_assembler_does_not_combine_a_sequence_gap_or_backward_time():
    assembler = WindowAssembler(4)
    assert assembler.push(frame(1, [[0, 0], [1, 1], [2, 2]], timestamp=2_000_000_000)) == []
    assert assembler.push(frame(3, [[3, 3], [4, 4]], timestamp=2_100_000_000)) == []
    assembled = assembler.push(frame(4, [[5, 5], [6, 6]], timestamp=2_200_000_000))
    assert len(assembled) == 1
    assert (assembled[0].first_sequence, assembled[0].last_sequence) == (3, 4)

    assert assembler.push(frame(5, [[7, 7]], timestamp=2_300_000_000)) == []
    assert assembler.push(frame(6, [[8, 8], [9, 9], [10, 10]], timestamp=2_250_000_000)) == []


def test_current_conversion_uses_declared_probe_sensitivity_without_software_zeroing():
    config = replace(
        Case5Config(), sample_rate_hz=1_000.0, analysis_samples=4, spectrum_max_frequency_hz=500.0
    )
    processor = CaptureProcessor(config)
    windows = processor.process(frame(1, [[1, 2], [2, 2], [3, 2], [4, 2]]))
    assert len(windows) == 1
    assert float(processor.latest_waveforms[1].mean()) == pytest.approx(2.0)
    np.testing.assert_allclose(windows[0].waveforms[1], 0.0)


def test_current_conversion_and_latency_continue_across_frames():
    config = replace(
        Case5Config(), sample_rate_hz=1_000.0, analysis_samples=4, spectrum_max_frequency_hz=500.0
    )
    processor = CaptureProcessor(config)
    processor.process(frame(1, [[1, 2], [2, 2], [3, 2], [4, 2]]))
    windows = processor.process(frame(2, [[5, 3], [6, 3], [7, 3], [8, 3]], timestamp=2_500_000_000))
    assert len(windows) == 1
    assert float(processor.latest_waveforms[1].mean()) == pytest.approx(3.0)
    assert processor.capture_interval_ms == pytest.approx(1_500.0)


def test_capture_processor_counts_two_analysis_windows_from_one_usb_block_once():
    config = replace(
        Case5Config(), sample_rate_hz=1_000.0, analysis_samples=4, spectrum_max_frequency_hz=500.0
    )
    processor = CaptureProcessor(config)
    processor.process(frame(1, np.zeros((4, 2)), timestamp=1_000_000_000))
    processor.process(frame(2, np.zeros((4, 2)), timestamp=1_000_000_000))
    processor.process(frame(3, np.zeros((4, 2)), timestamp=1_050_000_000))

    assert processor.frames_received == 3
    assert processor.usb_blocks_received == 2
    assert processor.capture_interval_ms == pytest.approx(50.0)


def test_capture_processor_rejects_duplicate_or_regressing_sequence_numbers():
    config = replace(
        Case5Config(), sample_rate_hz=1_000.0, analysis_samples=4, spectrum_max_frequency_hz=500.0
    )
    processor = CaptureProcessor(config)
    processor.process(frame(2, np.zeros((4, 2))))
    with pytest.raises(ValueError, match="must increase"):
        processor.process(frame(2, np.zeros((4, 2))))
    with pytest.raises(ValueError, match="must increase"):
        processor.process(frame(1, np.zeros((4, 2))))


def test_capture_processor_reset_stream_allows_sequence_restart():
    config = replace(
        Case5Config(), sample_rate_hz=1_000.0, analysis_samples=4, spectrum_max_frequency_hz=500.0
    )
    processor = CaptureProcessor(config)
    processor.process(frame(5, np.zeros((4, 2))))
    processor.reset_stream()
    processor.process(frame(0, np.zeros((4, 2))))
    assert processor.frames_received == 1


def test_config_rejects_invalid_storage_budget_and_nonfinite_scale():
    with pytest.raises(ValueError, match="at least raw_chunk_bytes"):
        replace(Case5Config(), raw_chunk_bytes=128, max_session_bytes=64).validate()
    with pytest.raises(ValueError, match="CH1 volts/div"):
        replace(Case5Config(), ch1_volts_per_division=float("nan")).validate()


def test_analysis_service_initializes_runs_and_closes_on_one_thread():
    class RecordingRunner:
        def __init__(self):
            self._status = NpuStatus("NPU unavailable", False, "not initialized")
            self.thread_ids = []

        @property
        def status(self):
            return self._status

        def initialize(self):
            self.thread_ids.append(("initialize", threading.get_ident()))
            self._status = NpuStatus("NPU (test)", True, "ready")
            return self._status

        def run(self, waveforms):
            self.thread_ids.append(("run", threading.get_ident()))
            return np.zeros((1, 2, 5, 1), dtype=np.float32)

        def mark_unavailable(self, message):
            self._status = NpuStatus("NPU unavailable", False, message)
            return self._status

        def close(self):
            self.thread_ids.append(("close", threading.get_ident()))

    runner = RecordingRunner()
    service = AnalysisService(runner, input_capacity=2, result_capacity=2)
    assert service.start().ready
    service.submit(
        AnalysisWindow(
            first_sequence=1,
            last_sequence=1,
            start_host_ns=0,
            end_host_ns=4_000_000,
            sample_rate_hz=1_000.0,
            waveforms=np.zeros((2, 4), dtype=np.float32),
        )
    )
    result = service.results.get(timeout=1.0)
    service.close()

    assert result.spectrum_power.shape == (1, 2, 5, 1)
    assert [name for name, _thread_id in runner.thread_ids] == ["initialize", "run", "close"]
    assert len({thread_id for _name, thread_id in runner.thread_ids}) == 1


def test_analysis_service_drops_submit_after_a_stopped_worker():
    class UnavailableRunner:
        def __init__(self):
            self._status = NpuStatus("NPU unavailable", False, "not initialized")

        @property
        def status(self):
            return self._status

        def initialize(self):
            self._status = NpuStatus("NPU unavailable", False, "missing OM")
            return self._status

        def run(self, _waveforms):
            raise AssertionError("unavailable runner must not execute")

        def mark_unavailable(self, message):
            self._status = NpuStatus("NPU unavailable", False, message)
            return self._status

        def close(self):
            return None

    service = AnalysisService(UnavailableRunner(), input_capacity=1, result_capacity=1)
    assert not service.start().ready
    service.submit(
        AnalysisWindow(1, 1, 0, 1, 1_000.0, np.zeros((2, 1), dtype=np.float32))
    )
    assert service.input.clear() == 0
    assert service.close()


def test_om_runner_latency_includes_host_output_materialization(monkeypatch, tmp_path):
    import time_frequency_dashboard.npu as npu_module

    class FakeTensor:
        def __init__(self, values):
            self.values = values

    class FakeOutput:
        def __init__(self):
            self.hosted = False

        def to_host(self):
            self.hosted = True
            npu_module.time.perf_counter_ns()

        def __array__(self, dtype=None):
            assert self.hosted
            return np.asarray([7.0], dtype=dtype or np.float32)

    class FakeSession:
        def run(self, names, inputs):
            assert names == ["output"]
            assert len(inputs) == 1
            return [FakeOutput()]

    runner = npu_module.AscendOmRunner(tmp_path / "unused.om")
    runner._runtime = type("FakeRuntime", (), {"Tensor": FakeTensor})
    runner._session = FakeSession()
    runner._output_names = ["output"]
    runner._status = npu_module.NpuStatus("NPU (test)", True, "ready")

    timestamps = iter((0, 1_000_000, 3_000_000))
    monkeypatch.setattr(npu_module.time, "perf_counter_ns", lambda: next(timestamps))

    output = runner.run(np.zeros((1, 2), dtype=np.float32))

    np.testing.assert_array_equal(output, [7.0])
    assert runner.status.last_latency_ms == pytest.approx(3.0)


def test_soak_timing_summary_requires_a_measured_inference():
    with pytest.raises(ValueError, match="without a measured inference"):
        timing_summary([])


def test_latest_queue_discards_stale_entry():
    queue = LatestQueue(2)
    queue.put_latest("old")
    queue.put_latest("middle")
    queue.put_latest("new")
    assert queue.dropped == 1
    assert queue.get_nowait() == "middle"
    assert queue.get_nowait() == "new"


def test_latest_queue_clear_discards_pending_generation_work():
    queue = LatestQueue(3)
    queue.put_latest("old-1")
    queue.put_latest("old-2")

    assert queue.clear() == 2
    with pytest.raises(Exception):
        queue.get_nowait()


def test_session_writes_frame_index_and_analysis_reference():
    with tempfile.TemporaryDirectory() as directory:
        config = replace(Case5Config(), session_root=Path(directory), raw_chunk_bytes=64, max_session_bytes=1024)
        writer = SessionWriter(config, {"model": "test"})
        assert writer.record_frame(frame(9, [[1, 2], [3, 4]]))
        writer.record_analysis({"first_sequence": 9, "backend": "NPU (Ascend 310B)"})
        writer.close()
        assert (writer.path / "manifest.json").is_file()
        assert '"sequence": 9' in (writer.path / "raw_index.jsonl").read_text(encoding="utf-8")
        assert '"first_sequence": 9' in (writer.path / "analysis.jsonl").read_text(encoding="utf-8")


def test_session_close_does_not_block_when_raw_queue_is_full_and_rejects_nan_analysis(tmp_path):
    config = replace(Case5Config(), session_root=tmp_path, raw_chunk_bytes=64, max_session_bytes=1024)
    writer = SessionWriter(config, {"model": "test"})
    for sequence in range(20):
        writer.record_frame(frame(sequence, [[1, 2], [3, 4]]))
    assert not writer.record_analysis({"value": float("nan")})
    assert writer.writer_error is not None
    assert writer.close(timeout=2.0)
    summary = (writer.path / "summary.json").read_text(encoding="utf-8")
    assert "analysis serialization failed" in summary


def test_dashboard_snapshot_exposes_exact_npu_dft_frequency_axis():
    config = replace(
        Case5Config(), sample_rate_hz=20_000.0, analysis_samples=20, spectrum_max_frequency_hz=4_000.0
    )
    controller = __import__("time_frequency_dashboard.controller", fromlist=["Case5Controller"]).Case5Controller(
        config, Path("missing.om")
    )
    snapshot = controller.snapshot()
    np.testing.assert_allclose(snapshot.spectrum_axis_hz, [0.0, 1_000.0, 2_000.0, 3_000.0, 4_000.0])
    assert snapshot.spectrum_values.size == 0
    controller.close()


def test_controller_hardware_start_failure_closes_session_and_resets_state(monkeypatch, tmp_path):
    from time_frequency_dashboard.controller import Case5Controller

    created = {"stops": 0, "waits": 0}

    class _BrokenCapture:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("bridge launch failed")

        def stop(self) -> bool:
            created["stops"] += 1
            return True

        def wait_stopped(self, timeout=None) -> bool:
            del timeout
            created["waits"] += 1
            return True

    monkeypatch.setattr("time_frequency_dashboard.controller.SigrokCapture", _BrokenCapture)
    controller = Case5Controller(replace(Case5Config(), session_root=tmp_path), Path("missing.om"))

    with pytest.raises(RuntimeError, match="bridge launch failed"):
        controller.start_hardware(Path("build/sigrok_capture_bridge"))

    snapshot = controller.snapshot()
    assert snapshot.source == "DISCONNECTED"
    assert snapshot.acquisition_state == "STOPPED"
    assert snapshot.session_path is None
    assert "Failed to start SIGROK" in snapshot.message
    assert created == {"stops": 1, "waits": 1}
    assert len(list(tmp_path.glob("*/summary.json"))) == 1
    controller.close()


def test_controller_does_not_resurrect_running_state_after_stop_races_source_start(monkeypatch, tmp_path):
    from time_frequency_dashboard.controller import Case5Controller

    started = threading.Event()
    release = threading.Event()

    class _BlockingCapture:
        def __init__(self, **_kwargs) -> None:
            self.stopped = False

        def start(self) -> None:
            started.set()
            assert release.wait(timeout=1.0)

        def stop(self) -> bool:
            self.stopped = True
            return True

        def wait_stopped(self, timeout=None) -> bool:
            del timeout
            return self.stopped

    monkeypatch.setattr("time_frequency_dashboard.controller.SigrokCapture", _BlockingCapture)
    controller = Case5Controller(replace(Case5Config(), session_root=tmp_path), Path("missing.om"))
    worker = threading.Thread(
        target=lambda: controller.start_hardware(Path("build/sigrok_capture_bridge")), daemon=True
    )
    worker.start()
    assert started.wait(timeout=1.0)
    assert controller.stop()
    release.set()
    worker.join(timeout=1.0)
    assert not worker.is_alive()
    snapshot = controller.snapshot()
    assert snapshot.acquisition_state == "STOPPED"
    assert snapshot.source == "DISCONNECTED"
    controller.close()


def test_synthetic_capture_reports_callback_exception_through_error_callback():
    errors = []

    def _raise_from_callback(_frame) -> None:
        raise ValueError("bad test callback")

    capture = SyntheticCapture(
        1_000.0,
        8,
        _raise_from_callback,
        error_callback=errors.append,
    )
    capture._run()

    assert capture._stop
    assert errors == ["Synthetic capture callback failed: ValueError: bad test callback"]


def test_capture_error_retains_source_until_its_worker_is_confirmed_stopped():
    with tempfile.TemporaryDirectory() as directory:
        config = replace(Case5Config(), session_root=Path(directory))
        from time_frequency_dashboard.controller import Case5Controller

        controller = Case5Controller(config, Path("missing.om"))
        controller._start_session("test")

        class _DelayedStopSource:
            def __init__(self) -> None:
                self.finished = False

            def stop(self) -> bool:
                return self.finished

            def wait_stopped(self, timeout=None) -> bool:
                del timeout
                return self.finished

        source = _DelayedStopSource()
        controller._source = source
        controller._source_name = "test"
        controller.on_error("USB disconnected")
        snapshot = controller.snapshot()
        assert snapshot.acquisition_state == "FAILED"
        assert snapshot.source == "test"
        assert snapshot.message == "USB disconnected"
        assert snapshot.session_path is None
        assert not controller.wait_stopped(timeout=0.0)
        assert controller.snapshot().source == "test"
        source.finished = True
        assert controller.wait_stopped(timeout=0.0)
        assert controller.snapshot().source == "DISCONNECTED"
        assert controller.snapshot().acquisition_state == "STOPPED"
        controller.close()
