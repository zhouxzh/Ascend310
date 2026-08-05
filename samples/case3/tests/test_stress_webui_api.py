from tools.stress_webui_api import percentile, summarize_samples


def test_percentile_interpolates_sorted_values() -> None:
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.5) == 2.5
    assert percentile([], 0.95) == 0.0


def test_summary_reports_errors_and_endpoint_latency() -> None:
    samples = [
        {"endpoint": "/api/v1/status", "elapsed_ms": 10.0, "ok": True},
        {"endpoint": "/api/v1/status", "elapsed_ms": 30.0, "ok": True},
        {"endpoint": "/api/v1/catalog", "elapsed_ms": 20.0, "ok": False},
    ]
    summary = summarize_samples(samples)
    assert summary["requests"] == 3
    assert summary["errors"] == 1
    assert summary["error_rate"] == 1 / 3
    assert summary["p50_ms"] == 20.0
    assert summary["endpoints"]["/api/v1/status"]["p50_ms"] == 20.0
