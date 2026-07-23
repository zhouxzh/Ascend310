from __future__ import annotations

from pathlib import Path
import asyncio
import importlib
import sys
import tempfile
import time
import unittest

import midi_ddsp_webui.core as core
from midi_ddsp_webui.core import JobManager, ResourceBusyError, ResourceCoordinator
from midi_ddsp_webui.live import InputRouter
from fastapi import HTTPException
from pydantic import ValidationError
from realtime_ddsp import PolyphonicMidiState
from starlette.requests import Request


web_app = importlib.import_module("midi_ddsp_webui.app")


def request_with_body(body: bytes) -> Request:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/api/v1/midi-files", "headers": []}, receive)


class CatalogTest(unittest.TestCase):
    def test_public_catalog_does_not_expose_server_paths(self) -> None:
        data = core.public_catalog()
        self.assertGreaterEqual(len(data["midi_files"]), 1)
        for group in ("midi_files", "live_models", "midi_ddsp_models"):
            for item in data[group]:
                self.assertNotIn("path", item)

    def test_live_catalog_contains_only_om_models(self) -> None:
        models = core.public_catalog()["live_models"]
        self.assertGreaterEqual(len(models), 1)
        self.assertTrue(all(model["backend"] == "om" for model in models))

    def test_catalog_ids_resolve_only_known_items(self) -> None:
        item = core.catalog()["midi_files"][0]
        resolved = core.resolve_catalog_item("midi_files", item["id"])
        self.assertEqual(resolved["path"], item["path"])
        with self.assertRaises(KeyError):
            core.resolve_catalog_item("midi_files", "midi-not-found")

    def test_benchmark_summary_parses_json_without_exposing_path(self) -> None:
        original_root = core.ROOT
        with tempfile.TemporaryDirectory() as folder:
            core.ROOT = Path(folder)
            summary_path = Path(folder) / "reports" / "run" / "midi_ddsp" / "result" / "summary.json"
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text('{"rows": [{"component": "expression"}]}', encoding="utf-8")
            try:
                summary = core.load_benchmark_summary()
                self.assertIsNotNone(summary)
                assert summary is not None
                self.assertEqual(summary["name"], "summary.json")
                self.assertNotIn("path", summary)
            finally:
                core.ROOT = original_root


class ApiValidationTest(unittest.TestCase):
    def test_live_parameters_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            web_app.LiveStartRequest(model_id="known-model", max_voices=0)

    def test_upload_limit_removes_partial_file(self) -> None:
        original_app_root = web_app.UPLOAD_ROOT
        original_core_root = core.UPLOAD_ROOT
        with tempfile.TemporaryDirectory() as folder:
            upload_root = Path(folder)
            web_app.UPLOAD_ROOT = upload_root
            core.UPLOAD_ROOT = upload_root
            try:
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        web_app.upload_midi(
                            request_with_body(b"x" * (web_app.MAX_MIDI_BYTES + 1)),
                            filename="oversized.mid",
                        )
                    )
                self.assertEqual(raised.exception.status_code, 413)
                self.assertEqual(list(upload_root.iterdir()), [])
            finally:
                web_app.UPLOAD_ROOT = original_app_root
                core.UPLOAD_ROOT = original_core_root

    def test_upload_response_does_not_expose_path(self) -> None:
        original_app_root = web_app.UPLOAD_ROOT
        original_core_root = core.UPLOAD_ROOT
        original_root = core.ROOT
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            upload_root = root / "reports" / "webui" / "uploads"
            core.ROOT = root
            web_app.UPLOAD_ROOT = upload_root
            core.UPLOAD_ROOT = upload_root
            try:
                result = asyncio.run(
                    web_app.upload_midi(request_with_body(b"MThd"), filename="example.mid")
                )
                self.assertEqual(result["original_name"], "example.mid")
                self.assertNotIn("path", result)
            finally:
                web_app.UPLOAD_ROOT = original_app_root
                core.UPLOAD_ROOT = original_core_root
                core.ROOT = original_root


class ResourceCoordinatorTest(unittest.TestCase):
    def test_single_owner_is_enforced_and_release_is_owner_scoped(self) -> None:
        coordinator = ResourceCoordinator()
        coordinator.acquire("live")
        with self.assertRaises(ResourceBusyError):
            coordinator.acquire("benchmark")
        coordinator.release("another-owner")
        self.assertEqual(coordinator.owner, "live")
        coordinator.release("live")
        self.assertIsNone(coordinator.owner)


class JobManagerTest(unittest.TestCase):
    def test_job_captures_progress_and_releases_resource(self) -> None:
        original_job_root = core.JOB_ROOT
        with tempfile.TemporaryDirectory() as folder:
            core.JOB_ROOT = Path(folder)
            try:
                coordinator = ResourceCoordinator()
                manager = JobManager(coordinator)
                code = (
                    "print('WEBUI_EVENT {\"event\":\"progress\","
                    "\"rendered\":2,\"played\":2,\"total\":4}')"
                )
                job = manager.start("test", [sys.executable, "-c", code])
                deadline = time.monotonic() + 5
                while manager.get(job.id).state not in core.TERMINAL_STATES:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                finished = manager.get(job.id)
                self.assertEqual(finished.state, "succeeded")
                self.assertEqual(finished.progress, 1.0)
                self.assertIsNone(coordinator.owner)
                self.assertTrue((Path(folder) / job.id / "job.log").is_file())
            finally:
                core.JOB_ROOT = original_job_root


class FakeMidiState:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def note_on(self, note: int, velocity: int) -> None:
        self.events.append(("on", note, velocity))

    def note_off(self, note: int) -> None:
        self.events.append(("off", note))

    def set_sustain(self, enabled: bool) -> None:
        self.events.append(("sustain", enabled))

    def all_notes_off(self) -> None:
        self.events.append(("all-off",))


class InputRouterTest(unittest.TestCase):
    def test_duplicate_sources_do_not_cut_each_other_off(self) -> None:
        midi = FakeMidiState()
        router = InputRouter(midi)
        router.note_on("touch", 60, 100)
        router.note_on("hardware", 60, 80)
        router.note_off("touch", 60)
        router.note_off("hardware", 60)
        self.assertEqual(midi.events, [("on", 60, 100), ("off", 60)])

    def test_disconnect_releases_notes_and_sustain(self) -> None:
        midi = FakeMidiState()
        router = InputRouter(midi)
        router.note_on("browser", 64, 90)
        router.sustain("browser", True)
        router.release_source("browser")
        self.assertEqual(
            midi.events,
            [("on", 64, 90), ("sustain", True), ("off", 64), ("sustain", False)],
        )


class StructuredMidiControlTest(unittest.TestCase):
    def test_sustain_delays_release_and_all_notes_off_is_safe(self) -> None:
        midi = PolyphonicMidiState(max_voices=2)
        midi.note_on(60, 100)
        midi.set_sustain(True)
        midi.note_off(60)
        self.assertEqual(midi.active_notes, [60])
        midi.set_sustain(False)
        midi.all_notes_off()
        for _ in range(100):
            midi.next_snapshots()
        self.assertEqual(midi.active_notes, [])

    def test_invalid_browser_values_are_rejected(self) -> None:
        midi = PolyphonicMidiState()
        with self.assertRaises(ValueError):
            midi.note_on(128, 100)
        with self.assertRaises(ValueError):
            midi.note_on(60, 200)
        with self.assertRaises(ValueError):
            midi.set_pitch_bend(9000)


if __name__ == "__main__":
    unittest.main()
