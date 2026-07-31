from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from midi_ddsp_webui.core import ResourceCoordinator
from midi_ddsp_webui.piano import (
    PianoDdspController,
    piano_catalog,
    resolve_piano_bundle,
)
from piano_ddsp_runtime.bundle import load_bundle
from midi_ddsp_webui.speaker import merge_piano_audio_outputs


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = ROOT / "models" / "piano_ddsp" / "model-suite-v1.0.0"


def make_bundle(
    root: Path,
    bundle_id: str = "fixture",
    *,
    validation_frames: int = 10_000,
    validation_passed: bool = True,
) -> None:
    bundle = root / "bundles" / bundle_id
    models = bundle / "models"
    models.mkdir(parents=True)
    om = models / "paper.om"
    metadata_path = models / "paper.json"
    om.write_bytes(b"fixture-om")
    om_sha256 = hashlib.sha256(om.read_bytes()).hexdigest()
    metadata_path.write_bytes((RELEASE_ROOT / "ddsp_piano_paper_ir.json").read_bytes())
    validation_dir = bundle / "validation"
    validation_dir.mkdir()
    validation_path = validation_dir / "paper_ir.json"
    validation_path.write_text(
        json.dumps(
            {
                "schema": "piano-ddsp-om-validation/v1",
                "bundle_id": bundle_id,
                "model_id": "paper_ir",
                "om_sha256": om_sha256,
                "frames": validation_frames,
                "passed": validation_passed,
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema": "piano-ddsp-om-bundle/v1",
        "id": bundle_id,
        "release": "model-suite-v1.0.0",
        "precision": "FP32",
        "precision_mode_v2": "origin",
        "soc_version": "Ascend310B4",
        "complete": False,
        "models": {
            "paper_ir": {
                "display_name": "Paper IR",
                "om": "models/paper.om",
                "om_sha256": om_sha256,
                "metadata": "models/paper.json",
                "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
                "validation": {
                    "path": "validation/paper_ir.json",
                    "sha256": hashlib.sha256(validation_path.read_bytes()).hexdigest(),
                    "frames": validation_frames,
                    "passed": validation_passed,
                    "om_sha256": om_sha256,
                },
            }
        },
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class PianoWebUiTest(unittest.TestCase):
    def test_spawn_failure_releases_resource_owner(self) -> None:
        coordinator = ResourceCoordinator()
        controller = PianoDdspController(coordinator)
        with mock.patch.object(controller, "_spawn", side_effect=OSError("spawn failed")):
            with self.assertRaisesRegex(OSError, "spawn failed"):
                controller.start({})
        self.assertIsNone(coordinator.owner)

    def test_bundle_resolution_requires_requested_model(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            make_bundle(root)
            with mock.patch("midi_ddsp_webui.piano.BUNDLE_ROOT", root / "bundles"):
                self.assertTrue(resolve_piano_bundle("fixture", "paper_ir").is_file())
                with self.assertRaisesRegex(KeyError, "film_fdn"):
                    resolve_piano_bundle("fixture", "film_fdn")

    def test_validation_report_must_match_the_current_om_hash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            make_bundle(root)
            bundle = root / "bundles" / "fixture"
            report_path = bundle / "validation" / "paper_ir.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["om_sha256"] = "0" * 64
            report_path.write_text(json.dumps(report), encoding="utf-8")
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["models"]["paper_ir"]["validation"]["sha256"] = hashlib.sha256(
                report_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Validation report mismatch"):
                load_bundle(manifest_path)
            relaxed = load_bundle(manifest_path, validate_qualification=False)
            self.assertFalse(relaxed.models["paper_ir"].validation_passed)

    def test_piano_audio_outputs_add_direct_edifier_without_changing_pulse_id(self) -> None:
        outputs = merge_piano_audio_outputs(
            [
                {
                    "id": "pulse:edifier",
                    "name": "EDIFIER M16 Pro",
                    "backend": "pulse",
                    "is_default": True,
                }
            ],
            [
                {"id": "0", "index": 0, "name": "pulse"},
                {"id": "1", "index": 1, "name": "dmix"},
                {"id": "2", "index": 2, "name": "EDIFIER M16 Pro USB Audio"},
            ],
        )
        self.assertEqual([item["id"] for item in outputs], ["pulse:edifier", "portaudio:2"])
        self.assertFalse(outputs[0]["is_default"])
        self.assertTrue(outputs[1]["is_default"])

    def test_piano_audio_outputs_use_direct_onboard_instead_of_pulse(self) -> None:
        outputs = merge_piano_audio_outputs(
            [
                {
                    "id": "pulse:alsa_output.platform-sound.stereo-fallback",
                    "name": "Built-in Audio Stereo",
                    "sink_name": "alsa_output.platform-sound.stereo-fallback",
                    "backend": "pulse",
                    "is_default": True,
                },
                {
                    "id": "pulse:bluez_output.11_22_33_44_55_66.1",
                    "name": "Bluetooth speaker",
                    "sink_name": "bluez_output.11_22_33_44_55_66.1",
                    "backend": "pulse",
                    "is_default": False,
                    "is_bluetooth": True,
                },
            ],
            [
                {"id": "0", "index": 0, "name": "ascend310b: - (hw:0,0)"},
                {"id": "1", "index": 1, "name": "EDIFIER M16 Pro USB Audio"},
            ],
        )
        self.assertEqual(
            [item["id"] for item in outputs],
            [
                "pulse:bluez_output.11_22_33_44_55_66.1",
                "alsa:onboard-headset",
                "portaudio:1",
            ],
        )
        self.assertEqual(outputs[1]["name"], "板载 3.5 mm")
        self.assertEqual(outputs[1]["backend"], "alsa_mono")
        self.assertEqual(outputs[1]["max_output_channels"], 1)
        self.assertEqual(outputs[1]["default_sample_rate"], 48_000)
        self.assertEqual(outputs[1]["alsa_route_device_id"], 2)
        self.assertTrue(outputs[1]["is_onboard"])
        self.assertTrue(outputs[2]["is_default"])

    def test_piano_audio_outputs_keep_onboard_and_reject_generic_aliases(self) -> None:
        outputs = merge_piano_audio_outputs(
            [],
            [
                {"id": "0", "index": 0, "name": "ascend310b: - (hw:0,0)"},
                {"id": "1", "index": 1, "name": "sysdefault"},
                {"id": "2", "index": 2, "name": "pulse"},
                {"id": "3", "index": 3, "name": "dmix"},
                {"id": "4", "index": 4, "name": "default"},
            ],
        )
        self.assertEqual([item["id"] for item in outputs], ["alsa:onboard-headset"])
        self.assertTrue(outputs[0]["is_default"])

    def test_catalog_exposes_active_bundle_without_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            make_bundle(root)
            active = root / "active-bundle.json"
            active.write_text(
                json.dumps(
                    {
                        "schema": "piano-ddsp-active-bundle/v1",
                        "bundle_id": "fixture",
                        "manifest": "bundles/fixture/manifest.json",
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch("midi_ddsp_webui.piano.BUNDLE_ROOT", root / "bundles"),
                mock.patch("midi_ddsp_webui.piano.RELEASE_ROOT", RELEASE_ROOT),
                mock.patch("midi_ddsp_webui.piano.ACTIVE_BUNDLE_PATH", active),
            ):
                catalog = piano_catalog()
            self.assertEqual(catalog["active_bundle_id"], "fixture")
            self.assertTrue(catalog["models"][0]["available"])
            self.assertNotIn("recommended", json.dumps(catalog))

    def test_stop_is_idempotent_and_releases_resource(self) -> None:
        coordinator = ResourceCoordinator()
        controller = PianoDdspController(coordinator)
        self.assertEqual(controller.stop()["state"], "stopped")
        self.assertEqual(controller.stop()["state"], "stopped")

    def test_catalog_does_not_offer_short_smoke_validation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            make_bundle(root, validation_frames=100, validation_passed=False)
            with (
                mock.patch("midi_ddsp_webui.piano.BUNDLE_ROOT", root / "bundles"),
                mock.patch("midi_ddsp_webui.piano.RELEASE_ROOT", RELEASE_ROOT),
                mock.patch("midi_ddsp_webui.piano.ACTIVE_BUNDLE_PATH", root / "missing.json"),
            ):
                catalog = piano_catalog()
            self.assertFalse(catalog["models"][0]["available"])
            self.assertEqual(catalog["bundles"][0]["models"], [])

    def test_failed_worker_gets_shutdown_before_forced_termination(self) -> None:
        class Process:
            def __init__(self) -> None:
                self.finished = False
                self.terminated = False

            def poll(self) -> int | None:
                return 0 if self.finished else None

            def wait(self, timeout: float) -> int:
                self.finished = True
                return 0

            def terminate(self) -> None:
                self.terminated = True

            def kill(self) -> None:
                self.terminated = True

        coordinator = ResourceCoordinator()
        coordinator.acquire(PianoDdspController.OWNER)
        controller = PianoDdspController(coordinator)
        process = Process()
        controller._process = process  # type: ignore[assignment]
        with mock.patch.object(controller, "send", return_value={}) as send:
            controller._terminate_failed("render failed")
        send.assert_called_once_with("shutdown", timeout=3.0)
        self.assertFalse(process.terminated)
        self.assertEqual(controller.status()["state"], "failed")


if __name__ == "__main__":
    unittest.main()
