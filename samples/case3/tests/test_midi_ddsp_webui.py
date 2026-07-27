from __future__ import annotations

from pathlib import Path
import asyncio
import importlib
import json
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import mido
import midi_ddsp_webui.core as core
from midi_ddsp_webui.core import JobManager, ResourceBusyError, ResourceCoordinator
from midi_ddsp_webui.live import InputRouter, resolve_latency_profile
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


def midi_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "fixture.mid"
        midi = mido.MidiFile(type=0, ticks_per_beat=480)
        track = mido.MidiTrack()
        track.append(mido.Message("program_change", program=40, time=0))
        track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
        midi.tracks.append(track)
        midi.save(path)
        return path.read_bytes()


class CatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_root = core.ROOT
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        midi_root = root / "midi"
        om_root = root / "models" / "om"
        midi_root.mkdir(parents=True)
        om_root.mkdir(parents=True)
        metadata_root = root / "models" / "ddsp_vst"
        metadata_root.mkdir(parents=True)
        (metadata_root / "metadata.json").write_text(
            '{"Violin": {"pitch_min_note": 56.63, "pitch_max_note": 74.44, '
            '"pitch_min_hz": 215.39, "pitch_max_hz": 602.44}}',
            encoding="utf-8",
        )
        (midi_root / "fixture.mid").write_bytes(midi_bytes())
        (om_root / "Violin_mixed_float16.om").write_bytes(b"fixture")
        core.ROOT = root
        core.clear_catalog_cache()

    def tearDown(self) -> None:
        core.ROOT = self.original_root
        core.clear_catalog_cache()
        self.temp_dir.cleanup()

    def test_public_catalog_does_not_expose_server_paths(self) -> None:
        data = core.public_catalog()
        self.assertGreaterEqual(len(data["midi_files"]), 1)
        self.assertNotIn("midi_ddsp_models", data)
        self.assertEqual(data["midi_ddsp_bundles"], [])
        for group in ("midi_files", "ddsp_vst_models", "midi_ddsp_bundles"):
            for item in data[group]:
                self.assertNotIn("path", item)
                self.assertNotIn("manifest", item)
                for component in item.get("components", {}).values():
                    self.assertNotIn("path", component)

    def test_ddsp_vst_catalog_contains_only_om_models(self) -> None:
        models = core.public_catalog()["ddsp_vst_models"]
        self.assertGreaterEqual(len(models), 1)
        self.assertTrue(all(model["backend"] == "om" for model in models))
        violin = next(model for model in models if model["instrument"] == "Violin")
        self.assertAlmostEqual(violin["pitch_min_note"], 56.63)
        self.assertAlmostEqual(violin["pitch_max_hz"], 602.44)

    def test_catalog_reuses_cache_until_cleared(self) -> None:
        with patch.object(core, "scan_ddsp_vst_models", wraps=core.scan_ddsp_vst_models) as scan:
            core.catalog(refresh=True)
            core.catalog()
            self.assertEqual(scan.call_count, 1)
            core.clear_catalog_cache()
            core.catalog()
            self.assertEqual(scan.call_count, 2)

    def test_catalog_ids_resolve_only_known_items(self) -> None:
        item = core.catalog()["midi_files"][0]
        resolved = core.resolve_catalog_item("midi_files", item["id"])
        self.assertEqual(resolved["path"], item["path"])
        with self.assertRaises(KeyError):
            core.resolve_catalog_item("midi_files", "midi-not-found")

    def test_catalog_accepts_schema_two_voice_batches(self) -> None:
        bundle_root = core.ROOT / "models" / "midi_ddsp" / "bundles" / "batched"
        bundle_root.mkdir(parents=True)
        component = bundle_root / "component.om"
        component.write_bytes(b"batch fixture")
        import hashlib
        import json

        (bundle_root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "id": "batched",
                    "name": "Batched fixture",
                    "architecture": "stateful-v2",
                    "source_commit": core.MIDI_DDSP_SOURCE_COMMIT,
                    "voice_batch_sizes": [1, 2, 4, 8],
                    "components": {
                        "fixture": {
                            "file": component.name,
                            "sha256": hashlib.sha256(component.read_bytes()).hexdigest(),
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        core.clear_catalog_cache()
        bundle = next(
            item
            for item in core.public_catalog()["midi_ddsp_bundles"]
            if item["id"] == "batched"
        )
        self.assertEqual(bundle["voice_batch_sizes"], [1, 2, 4, 8])

    def test_catalog_rejects_non_origin_bundle(self) -> None:
        bundle_root = core.ROOT / "models" / "midi_ddsp" / "bundles" / "unsupported"
        bundle_root.mkdir(parents=True)
        component = bundle_root / "component.om"
        component.write_bytes(b"unsupported fixture")
        import hashlib
        import json

        (bundle_root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "id": "unsupported",
                    "name": "Unsupported fixture",
                    "architecture": "stateful-v2",
                    "source_commit": core.MIDI_DDSP_SOURCE_COMMIT,
                    "onnx_dtype": "float32",
                    "precision": "unsupported",
                    "voice_batch_sizes": [1],
                    "components": {
                        "fixture": {
                            "file": component.name,
                            "sha256": hashlib.sha256(component.read_bytes()).hexdigest(),
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        core.clear_catalog_cache()
        self.assertEqual(core.public_catalog()["midi_ddsp_bundles"], [])

class ApiValidationTest(unittest.TestCase):
    @staticmethod
    def _voice_analysis() -> dict[str, object]:
        return {
            "analysis_id": "a" * 64,
            "algorithm": {
                "id": "partitura-chew-wu-contig-v1",
                "commit": "427ff875bd5a49a0eec894fdd7c6631ed7f597ea",
            },
            "groups": [
                {
                    "id": "track-0-channel-1-program-40",
                    "voices": [
                        {"id": "track-0-channel-1-program-40-voice-1"},
                        {"id": "track-0-channel-1-program-40-voice-2"},
                    ],
                }
            ],
        }

    def test_public_routes_use_ddsp_vst_name_only(self) -> None:
        paths = {route.path for route in web_app.app.routes}
        self.assertIn("/api/v1/ddsp-vst/start", paths)
        self.assertIn("/api/v1/ddsp-vst/events", paths)
        self.assertNotIn("/api/v1/live/start", paths)
        self.assertNotIn("/api/v1/live/events", paths)

    def test_speaker_level_is_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            web_app.SpeakerTestRequest(
                audio_device_id="1",
                level_db=0,
            )

    def test_speaker_test_rejects_unknown_audio_device(self) -> None:
        with patch.object(web_app, "query_speaker_outputs", return_value=[]):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    web_app.start_speaker_test(
                        web_app.SpeakerTestRequest(audio_device_id="missing")
                    )
                )
        self.assertEqual(raised.exception.status_code, 404)

    def test_midi_backend_failure_returns_unavailable_response(self) -> None:
        with patch.object(
            web_app,
            "query_midi_devices",
            side_effect=SystemError("ALSA sequencer unavailable"),
        ):
            result = web_app.get_midi_ports()
        self.assertFalse(result["available"])
        self.assertEqual(result["ports"], [])
        self.assertIn("ALSA sequencer unavailable", str(result["error"]))

    def test_ddsp_vst_parameters_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            web_app.DdspVstStartRequest(model_id="known-model", max_voices=0)
        with self.assertRaises(ValidationError):
            web_app.DdspVstStartRequest(model_id="known-model", reverb_wet=1.1)
        with self.assertRaises(ValidationError):
            web_app.DdspVstStartRequest(model_id="known-model", latency_profile="fast")
        with self.assertRaises(ValidationError):
            web_app.DdspVstStartRequest(model_id="known-model", velocity_curve=0.2)

    def test_latency_profiles_are_device_aware_and_keep_legacy_values(self) -> None:
        balanced = resolve_latency_profile({"latency_profile": "balanced"})
        self.assertEqual(balanced["prebuffer"], 2)
        self.assertEqual(balanced["audio_latency_ms"], 20.0)

        bluetooth = resolve_latency_profile(
            {
                "latency_profile": "balanced",
                "is_bluetooth": True,
                "audio_device_sample_rate": 44_100,
            }
        )
        self.assertEqual(bluetooth["sample_rate"], 44_100)
        self.assertEqual(bluetooth["audio_latency_ms"], 220.0)
        with self.assertRaisesRegex(ValueError, "Bluetooth"):
            resolve_latency_profile({"latency_profile": "low", "is_bluetooth": True})

        legacy = resolve_latency_profile({"prebuffer": 6, "audio_latency_ms": 80.0})
        self.assertEqual(legacy["prebuffer"], 6)
        self.assertEqual(legacy["audio_latency_ms"], 80.0)

    def test_midi_ddsp_rejects_positive_output_gain(self) -> None:
        with self.assertRaises(ValidationError):
            web_app.MidiDdspJobRequest(
                midi_id="known-midi",
                model_bundle_id="known-bundle",
                output_gain_db=0.1,
            )

    def test_voice_analysis_endpoint_returns_algorithm_and_voices(self) -> None:
        expected = self._voice_analysis()
        with patch.object(
            web_app,
            "resolve_catalog_item",
            return_value={"path": "fixture.mid"},
        ), patch.object(web_app, "analyze_midi_voices", return_value=expected):
            actual = web_app.get_midi_voices("midi-fixture")
        self.assertEqual(actual, expected)

    def test_midi_ddsp_rejects_stale_or_incomplete_voice_assignment(self) -> None:
        analysis = self._voice_analysis()
        payload = web_app.MidiDdspJobRequest(
            midi_id="midi-fixture",
            model_bundle_id="bundle-fixture",
            voice_analysis_id="0" * 64,
            voice_instruments={
                "track-0-channel-1-program-40-voice-1": 0,
                "track-0-channel-1-program-40-voice-2": 1,
            },
        )
        midi = {
            "path": "fixture.mid",
            "name": "fixture.mid",
            "midi_ddsp_supported": True,
        }
        bundle = {
            "id": "bundle-fixture",
            "manifest": "manifest.json",
            "name": "Origin",
            "architecture": "stateful-v2",
            "quality_status": "validated",
        }

        def resolve(group: str, _item_id: str) -> dict[str, object]:
            return midi if group == "midi_files" else bundle

        with patch.object(web_app, "_require_board"), patch.object(
            web_app, "resolve_catalog_item", side_effect=resolve
        ), patch.object(web_app, "analyze_midi_voices", return_value=analysis):
            with self.assertRaises(HTTPException) as raised:
                web_app.start_midi_ddsp_job(payload)
        self.assertEqual(raised.exception.detail["code"], "voice_analysis_stale")

        payload.voice_analysis_id = str(analysis["analysis_id"])
        payload.voice_instruments.pop(
            "track-0-channel-1-program-40-voice-2"
        )
        with patch.object(web_app, "_require_board"), patch.object(
            web_app, "resolve_catalog_item", side_effect=resolve
        ), patch.object(web_app, "analyze_midi_voices", return_value=analysis):
            with self.assertRaises(HTTPException) as raised:
                web_app.start_midi_ddsp_job(payload)
        self.assertEqual(
            raised.exception.detail["code"], "voice_assignment_mismatch"
        )

    def test_midi_ddsp_forwards_compact_complete_voice_assignment(self) -> None:
        analysis = self._voice_analysis()
        mapping = {
            "track-0-channel-1-program-40-voice-1": 0,
            "track-0-channel-1-program-40-voice-2": 2,
        }
        payload = web_app.MidiDdspJobRequest(
            midi_id="midi-fixture",
            model_bundle_id="bundle-fixture",
            force_render=True,
            voice_analysis_id=str(analysis["analysis_id"]),
            voice_instruments=mapping,
        )
        midi = {
            "path": "fixture.mid",
            "name": "fixture.mid",
            "midi_ddsp_supported": True,
            "midi_ddsp_mode": "polyphonic",
            "voice_count": 2,
        }
        bundle = {
            "id": "bundle-fixture",
            "manifest": "manifest.json",
            "name": "Origin",
            "architecture": "stateful-v2",
            "quality_status": "validated",
        }

        def resolve(group: str, _item_id: str) -> dict[str, object]:
            return midi if group == "midi_files" else bundle

        fake_job = SimpleNamespace(public=lambda: {"id": "job-fixture"})
        with patch.object(web_app, "_require_board"), patch.object(
            web_app, "resolve_catalog_item", side_effect=resolve
        ), patch.object(
            web_app, "analyze_midi_voices", return_value=analysis
        ), patch.object(
            web_app, "validate_midi_ddsp_reverb_asset", return_value="reverb-sha"
        ), patch.object(web_app.jobs, "start", return_value=fake_job) as start:
            actual = web_app.start_midi_ddsp_job(payload)

        self.assertEqual(actual, {"id": "job-fixture"})
        command = start.call_args.args[1]
        self.assertIn("--force-render", command)
        mapping_index = command.index("--voice-instruments-json") + 1
        self.assertEqual(
            command[mapping_index],
            json.dumps(mapping, sort_keys=True, separators=(",", ":")),
        )
        metadata = start.call_args.kwargs["metadata"]
        self.assertTrue(metadata["force_render"])
        self.assertEqual(metadata["instrument_ids"], [0, 2])
        self.assertEqual(metadata["voice_instruments"], mapping)
        self.assertEqual(metadata["instrument_mode"], "per_voice")

    def test_ddsp_vst_uses_safe_default_output_gain(self) -> None:
        request = web_app.DdspVstStartRequest(model_id="known-model")
        self.assertEqual(request.output_gain_db, -18.0)
        self.assertEqual(request.attack, 0.02)
        self.assertEqual(request.velocity_curve, 0.55)

    def test_corrupt_reverb_asset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "midi_ddsp_reverb_ir.npz"
            path.write_bytes(b"not-an-npz")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                core.validate_midi_ddsp_reverb_asset(path)

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
                    web_app.upload_midi(request_with_body(midi_bytes()), filename="example.mid")
                )
                self.assertEqual(result["original_name"], "example.mid")
                self.assertNotIn("path", result)
            finally:
                web_app.UPLOAD_ROOT = original_app_root
                core.UPLOAD_ROOT = original_core_root
                core.ROOT = original_root

    def test_invalid_midi_upload_is_rejected_and_removed(self) -> None:
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
                            request_with_body(b"MThd"), filename="broken.mid"
                        )
                    )
                self.assertEqual(raised.exception.status_code, 422)
                self.assertEqual(list(upload_root.iterdir()), [])
            finally:
                web_app.UPLOAD_ROOT = original_app_root
                core.UPLOAD_ROOT = original_core_root


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
    def test_job_stores_structured_heartbeat_and_monotonic_progress(self) -> None:
        job = core.Job(id="progress", kind="midi-ddsp-play", progress=0.4)
        JobManager._apply_web_event(
            job,
            '{"event":"heartbeat","stage":"pitch_context",'
            '"stage_progress":0.5,"overall_progress":0.45,'
            '"completed":4,"total":8,"voice_batch_index":1,'
            '"voice_batch_count":1,"elapsed_seconds":12.0,'
            '"eta_seconds":15.0,"heartbeat_at":123.0}',
        )
        self.assertEqual(job.progress, 0.45)
        self.assertEqual(job.progress_detail["stage"], "pitch_context")
        self.assertEqual(job.progress_detail["voice_batch_count"], 1)
        JobManager._apply_web_event(
            job,
            '{"event":"progress","overall_progress":0.2,"total":1}',
        )
        self.assertEqual(job.progress, 0.45)

    def test_pause_is_rejected_before_playback_stage(self) -> None:
        coordinator = ResourceCoordinator()
        manager = JobManager(coordinator)
        job = core.Job(
            id="rendering",
            kind="midi-ddsp-play",
            state="running",
            progress_detail={"stage": "dsp_reverb"},
        )
        manager._jobs[job.id] = job
        with self.assertRaisesRegex(RuntimeError, "only be paused during playback"):
            manager.pause(job.id)

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

    def test_hardware_velocity_is_available_for_live_diagnostics(self) -> None:
        midi = FakeMidiState()
        router = InputRouter(midi)
        router.hardware_message(SimpleNamespace(type="note_on", note=60, velocity=18))
        router.hardware_message(SimpleNamespace(type="note_on", note=64, velocity=92))
        self.assertEqual(router.hardware_velocity_snapshot(), [18, 92])


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
