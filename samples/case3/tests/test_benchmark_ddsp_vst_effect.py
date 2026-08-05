from __future__ import annotations

from tools.benchmark_ddsp_vst_effect import evaluate_samples, run_benchmark, select_catalog


def fixture_catalog() -> dict[str, object]:
    models = [
        {"id": f"model-{index}", "name": f"Instrument {index}", "backend": "om"}
        for index in range(10)
    ]
    models.append({"id": "violin", "name": "Violin_mixed_float16.om", "backend": "om"})
    return {
        "available": True,
        "backend": "acl/om",
        "models": models,
        "audio_inputs": [
            {
                "id": "ugreen",
                "name": "UGREEN Camera 1080P",
                "backend": "pulse",
                "type": "capture",
                "available": True,
            }
        ],
        "audio_outputs": [
            {"id": "edifier", "name": "EDIFIER M16 Pro", "backend": "pulse"}
        ],
    }


def test_catalog_requires_the_real_om_model_and_physical_devices() -> None:
    selected = select_catalog(fixture_catalog())
    assert selected["model"]["id"] == "violin"
    assert selected["audio_input"]["id"] == "ugreen"
    assert selected["audio_output"]["id"] == "edifier"


def test_long_run_qualification_checks_audio_timing_and_safety() -> None:
    base_metrics = {
        "elapsed_seconds": 600.2,
        "input_peak_dbfs": -24.0,
        "output_peak_dbfs": -18.0,
        "f0_hz": 440.0,
        "feature_p95_ms": 11.0,
        "control_p95_ms": 1.2,
        "total_latency_ms": 123.0,
        "capture_overflows": 0,
        "playback_underruns": 0,
        "clipped_samples": 0,
        "safety_muted": False,
    }
    samples = [
        {"metrics": {**base_metrics, "frames": 10}},
        {"metrics": {**base_metrics, "frames": 30}},
    ]
    result = evaluate_samples(
        samples,
        requested_seconds=600.0,
        stimulus_description="Independent sustained violin notes beside the UGREEN microphone",
        independent_stimulus_confirmed=True,
    )
    assert result["passed"] is True


def test_silent_or_short_run_cannot_qualify() -> None:
    result = evaluate_samples(
        [
            {
                "metrics": {
                    "frames": 1,
                    "elapsed_seconds": 10.0,
                    "input_peak_dbfs": -96.0,
                    "output_peak_dbfs": -96.0,
                    "f0_hz": 0.0,
                    "feature_p95_ms": 1.0,
                    "control_p95_ms": 1.0,
                    "total_latency_ms": 100.0,
                    "capture_overflows": 0,
                    "playback_underruns": 0,
                    "clipped_samples": 0,
                    "safety_muted": False,
                }
            },
            {
                "metrics": {
                    "frames": 2,
                    "elapsed_seconds": 10.1,
                    "input_peak_dbfs": -96.0,
                    "output_peak_dbfs": -96.0,
                    "f0_hz": 0.0,
                    "feature_p95_ms": 1.0,
                    "control_p95_ms": 1.0,
                    "total_latency_ms": 100.0,
                    "capture_overflows": 0,
                    "playback_underruns": 0,
                    "clipped_samples": 0,
                    "safety_muted": False,
                }
            },
        ],
        requested_seconds=10.0,
        stimulus_description="",
        independent_stimulus_confirmed=False,
    )
    assert result["passed"] is False
    assert result["checks"]["duration_at_least_600_seconds"] is False
    assert result["checks"]["non_silent_physical_input"] is False


def test_benchmark_duration_starts_after_effect_enters_running(tmp_path) -> None:
    clock = [0.0]
    status_times: list[float] = []
    catalog = {**fixture_catalog(), "feature_model": {"name": "feature.om"}}

    def fake_api(_base_url, path, *, method="GET", payload=None):
        if path == "/api/v1/ddsp-vst-effect/catalog":
            return catalog
        if path == "/api/v1/ddsp-vst-effect/start":
            clock[0] += 3.0
            return {"state": "running", "running": True}
        if path == "/api/v1/ddsp-vst-effect/status":
            status_times.append(clock[0])
            elapsed = clock[0] - 3.0
            return {
                "state": "running",
                "running": True,
                "metrics": {
                    "frames": round(elapsed * 50),
                    "elapsed_seconds": elapsed,
                    "input_peak_dbfs": -24.0,
                    "output_peak_dbfs": -18.0,
                    "f0_hz": 440.0,
                    "feature_p95_ms": 11.0,
                    "control_p95_ms": 1.2,
                    "total_latency_ms": 123.0,
                    "capture_overflows": 0,
                    "playback_underruns": 0,
                    "clipped_samples": 0,
                    "safety_muted": False,
                },
            }
        if path == "/api/v1/ddsp-vst-effect/stop":
            return {"state": "stopped", "running": False}
        raise AssertionError((method, path, payload))

    run_benchmark(
        base_url="http://board",
        duration_seconds=10.0,
        poll_interval_seconds=10.0,
        stimulus_description="test tone",
        independent_stimulus_confirmed=True,
        output=tmp_path / "benchmark.json",
        api=fake_api,
        monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    assert status_times == [3.0, 13.0]
