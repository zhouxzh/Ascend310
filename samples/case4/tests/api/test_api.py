from __future__ import annotations

"""Contract tests for the FastAPI boundary.

The local authoring environment intentionally does not contain OpenCV/CANN
runtime assets.  The tests are therefore skipped when the board runtime
dependencies are absent; on the board they exercise the real route table and
the model/backend guards without opening a camera or running an OM model.
"""

import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch


_RUNTIME_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ("cv2", "fastapi", "numpy")
)


@unittest.skipUnless(_RUNTIME_AVAILABLE, "board Python runtime dependencies are not installed")
class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from palmprint_workbench.api import server as api_server
        from palmprint_workbench.config import MANUAL_TEST_PROFILE, RELEASE_PROFILE

        cls.api_server = api_server
        cls.manual_test_profile = RELEASE_PROFILE == MANUAL_TEST_PROFILE
        cls.client = TestClient(api_server.app)

    def test_health_and_bootstrap(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertIn(health.json()["status"], {"ok", "warning"})
        self.assertIn("npu_runtime", health.json())
        self.assertIn("acl_lifecycle", health.json())
        for field in (
            "transport_ready",
            "runtime_importable",
            "model_ready",
            "template_store_ready",
            "template_store",
            "inference_smoke",
            "admitted_model_ids",
            "release_id",
        ):
            self.assertIn(field, health.json())

        bootstrap = self.client.get("/api/bootstrap")
        self.assertEqual(bootstrap.status_code, 200)
        payload = bootstrap.json()
        self.assertGreaterEqual(len(payload["models"]), 1)
        self.assertEqual(payload["defaults"]["backend"], "npu")
        self.assertEqual(payload["defaults"]["precision"], "mixed_fp16")
        self.assertTrue(all("cpu" not in item.get("available_backends", []) for item in payload["models"]))
        self.assertTrue(all(item.get("id") != "edcc" for item in payload["models"]))
        compnet_ids = {
            item.get("id") for item in payload["models"] if str(item.get("id", "")).startswith("compnet_")
        }
        if self.manual_test_profile:
            self.assertEqual(
                compnet_ids,
                {
                    "compnet_tongji_600",
                    "compnet_iitd_460",
                    "compnet_rest_358",
                    "compnet_xjtu_flash_200",
                    "compnet_xjtu_natural_200",
                },
            )
            self.assertTrue(
                all(
                    item.get("manual_test_pending") is True
                    for item in payload["models"]
                    if item.get("id") in compnet_ids
                )
            )
        else:
            self.assertEqual(compnet_ids, set())
        self.assertTrue(all(item.get("usable_for_recognition") is True for item in payload["models"]))
        self.assertTrue(all(item.get("status", {}).get("npu_mixed_fp16") == "ready" for item in payload["models"]))
        self.assertIn("defaults", payload)
        self.assertNotIn("candidates", payload)

    def test_edcc_npu_is_rejected_before_adapter_creation(self):
        response = self.client.post(
            "/api/enrollment-sessions",
            json={
                "model_id": "edcc",
                "backend": "npu",
                "precision": "origin",
                "person_id": "TEST-001",
                "palm_side": "left",
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("离线研究资产", response.json()["detail"])

    def test_cpu_backend_is_rejected_for_enrollment(self):
        response = self.client.post(
            "/api/enrollment-sessions",
            json={
                "model_id": "ccnet",
                "backend": "cpu",
                "precision": "origin",
                "person_id": "TEST-001",
                "palm_side": "left",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("backend=npu", response.json()["detail"])

    def test_origin_precision_and_pending_candidate_are_rejected(self):
        origin = self.client.post(
            "/api/enrollment-sessions",
            json={
                "model_id": "ccnet",
                "backend": "npu",
                "precision": "origin",
                "person_id": "TEST-001",
                "palm_side": "left",
            },
        )
        self.assertEqual(origin.status_code, 422)
        self.assertIn("mixed_fp16", origin.json()["detail"])

        pending = self.client.post(
            "/api/enrollment-sessions",
            json={
                "model_id": "compnet_tongji_600",
                "backend": "npu",
                "precision": "mixed_fp16",
                "person_id": "TEST-001",
                "palm_side": "left",
                "mode": "self_service",
            },
        )
        if self.manual_test_profile:
            self.assertEqual(pending.status_code, 200)
            self.assertEqual(pending.json()["phase"], "ready")
            self.client.delete(f"/api/enrollment-sessions/{pending.json()['id']}")
        else:
            self.assertEqual(pending.status_code, 503)
            self.assertIn("暂不可用", pending.json()["detail"])

    def test_enrollment_assigns_person_id_and_requires_palm_side(self):
        response = self.client.post(
            "/api/enrollment-sessions",
            json={"model_id": "ccnet", "backend": "npu", "precision": "mixed_fp16"},
        )
        self.assertEqual(response.status_code, 422)
        missing = {entry["loc"][-1] for entry in response.json()["detail"]}
        self.assertEqual(missing, {"palm_side"})

    def test_assisted_enrollment_exposes_review_and_commit_actions(self):
        """Administrator sessions must not silently take the self-service path."""

        import numpy as np

        response = self.client.post(
            "/api/enrollment-sessions",
            json={
                "model_id": "ccnet",
                "backend": "npu",
                "precision": "mixed_fp16",
                "person_id": "API-REVIEW-001",
                "palm_side": "left",
                "mode": "assisted",
            },
        )
        self.assertEqual(response.status_code, 200)
        session_id = response.json()["id"]
        state = self.api_server._SESSIONS[session_id]

        def populate_review_samples() -> None:
            state.samples = [
                self.api_server.EnrollmentSample(
                    sample_id=f"sample-{index}",
                    roi=np.zeros((8, 8, 3), dtype=np.uint8),
                    capture_id=f"capture-{index}",
                    preview_url=None,
                    roi_url=None,
                    quality={},
                    geometry={},
                )
                for index in range(5)
            ]
            state.capture_ids = [sample.capture_id for sample in state.samples]
            state.phase = "reviewing"

        try:
            populate_review_samples()
            status = self.client.get(f"/api/enrollment-sessions/{session_id}")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["mode"], "assisted")
            self.assertTrue(status.json()["review_actions_enabled"])

            with patch.object(self.api_server.WORKBENCH.captures, "cleanup", return_value=True):
                discarded = self.client.delete(
                    f"/api/enrollment-sessions/{session_id}/samples/sample-0"
                )
            self.assertEqual(discarded.status_code, 200)
            self.assertEqual(discarded.json()["phase"], "ready")
            self.assertEqual(discarded.json()["sample_count"], 4)
            self.assertFalse(discarded.json()["review_actions_enabled"])

            populate_review_samples()
            replacement = {
                "palm_id": "API-REVIEW-001:left",
                "replaced_samples": 0,
            }
            with patch.object(self.api_server, "_complete_enrollment", return_value=replacement):
                committed = self.client.post(
                    f"/api/enrollment-sessions/{session_id}/commit", json={}
                )
            self.assertEqual(committed.status_code, 200)
            self.assertEqual(committed.json()["phase"], "completed")
            self.assertEqual(committed.json()["palm_id"], "API-REVIEW-001:left")
        finally:
            with self.api_server._SESSION_LOCK:
                self.api_server._SESSIONS.pop(session_id, None)

    def test_cpu_backend_is_rejected_for_evaluation(self):
        response = self.client.post(
            "/api/evaluations",
            json={"model_id": "ccnet", "backend": "cpu", "precision": "origin"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("backend=npu", response.json()["detail"])

    def test_evaluation_timeout_is_bounded(self):
        from pydantic import ValidationError

        options = self.api_server.EvaluationOptions(timeout_seconds=120)
        self.assertEqual(options.timeout_seconds, 120)
        with self.assertRaises(ValidationError):
            self.api_server.EvaluationOptions(timeout_seconds=9)
        with self.assertRaises(ValidationError):
            self.api_server.EvaluationOptions(timeout_seconds=3601)

    def test_cpu_backend_is_rejected_for_template_reads_and_deletes(self):
        read_response = self.client.get(
            "/api/templates",
            params={"model_id": "ccnet", "backend": "cpu", "precision": "origin"},
        )
        self.assertEqual(read_response.status_code, 422)
        self.assertIn("backend=npu", read_response.json()["detail"])

        delete_response = self.client.delete(
            "/api/templates/template-id",
            params={"model_id": "ccnet", "backend": "cpu", "precision": "origin"},
        )
        self.assertEqual(delete_response.status_code, 422)
        self.assertIn("backend=npu", delete_response.json()["detail"])

    def test_cpu_backend_is_rejected_before_upload_decode(self):
        # An unsupported backend must not be able to reach image/model code;
        # this request intentionally omits the image field.
        response = self.client.post(
            "/api/recognitions",
            data={"model_id": "ccnet", "backend": "cpu", "precision": "origin"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("backend=npu", response.json()["detail"])

    def test_cpu_backend_is_rejected_before_camera_open(self):
        # Camera recognition validates the model contract before touching a
        # V4L2 node, so this is safe on hosts without a camera.
        response = self.client.post(
            "/api/cameras/%2Fdev%2Fvideo0/recognitions",
            json={"model_id": "ccnet", "backend": "cpu", "precision": "origin"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("backend=npu", response.json()["detail"])

    def test_camera_recognition_forces_automatic_roi(self):
        """A stale client cannot submit a full V4L2 frame as a ready ROI."""

        import numpy as np

        frame = SimpleNamespace(rgb=np.zeros((720, 1280, 3), dtype=np.uint8))
        observed: dict[str, bool] = {}

        def recognize(_image, options, **_kwargs):
            observed["assume_roi"] = options.assume_roi
            return {"outcome": "no_templates", "matches": []}

        with patch.object(self.api_server.WORKBENCH, "capture_camera_frame", return_value=frame), patch.object(
            self.api_server, "_recognition_payload", side_effect=recognize
        ):
            response = self.client.post(
                "/api/cameras/%2Fdev%2Fvideo0/recognitions",
                json={
                    "model_id": "ccnet",
                    "backend": "npu",
                    "precision": "mixed_fp16",
                    "assume_roi": True,
                    "resolution": "1280x720",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(observed["assume_roi"])

    def test_enrollment_session_forces_automatic_roi(self):
        response = self.client.post(
            "/api/enrollment-sessions",
            json={
                "model_id": "ccnet",
                "backend": "npu",
                "precision": "mixed_fp16",
                "person_id": "ROI-GUARD-001",
                "palm_side": "left",
                "assume_roi": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        session_id = response.json()["id"]
        try:
            self.assertFalse(self.api_server._SESSIONS[session_id].options.assume_roi)
        finally:
            self.client.delete(f"/api/enrollment-sessions/{session_id}")

    def test_npu_template_namespace_is_canonical(self):
        from palmprint_workbench.services.workbench import _template_namespace

        self.assertEqual(
            _template_namespace("ccnet", "npu", "mixed_fp16"),
            "ccnet__npu__mixed_fp16",
        )
        with self.assertRaises(ValueError):
            _template_namespace("ccnet", "npu", "origin")

    def test_frontend_fallback_is_safe(self):
        response = self.client.get("/api/route-that-does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_candidate_inventory_is_separate_from_production_models(self):
        response = self.client.get("/api/candidates")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            {item["id"] for item in payload["items"]},
            {
                "ccnet",
                "compnet_tongji_600",
                "compnet_iitd_460",
                "compnet_rest_358",
                "compnet_xjtu_flash_200",
                "compnet_xjtu_natural_200",
            },
        )
        self.assertEqual(payload["archived_count"], 22)
        self.assertIn("ppnet", payload["archived_candidate_ids"])
        self.assertTrue(all("task_type" in item for item in payload["items"]))
        self.assertTrue(all(item["task_type"] == "embedding" for item in payload["items"]))
        self.assertTrue(all("diagnostic_status" in item for item in payload["items"]))

    def test_candidate_runtime_inventory_is_separate_and_contractual(self):
        response = self.client.get("/api/candidate-runtimes")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["production_registry_modified"])
        self.assertEqual(
            {item["id"] for item in payload["items"]},
            {
                "holzweber_resnet18_tongji",
                "holzweber_mobilenet_v2_tongji",
                "holzweber_roi_lanet",
                "lin_dxin_resnet18_pair",
                "ppnet",
                "kenan_cnn_palmar_veins",
            },
        )
        pair = next(item for item in payload["items"] if item["id"] == "lin_dxin_resnet18_pair")
        self.assertEqual(len(pair["input_shape"]), 2)
        self.assertEqual(pair["output_kind"], "embedding_pair")

    def test_candidate_run_requires_image_and_does_not_touch_production_route(self):
        response = self.client.post("/api/candidate-runs", data={"candidate_id": "ppnet"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("image", response.json()["detail"])

    def test_legacy_static_compnet_cannot_be_promoted_by_marker(self):
        spec = self.api_server.WORKBENCH.registry.get("compnet")
        payload = self.api_server._model_payload(spec)
        self.assertFalse(payload["usable_for_recognition"])
        self.assertIn("候选级", payload["eligibility_reason"])

    def test_comparison_is_offline_only(self):
        response = self.client.post(
            "/api/comparisons",
            json={"candidate_ids": ["ccnet"], "dataset_id": "tongji", "precision": "mixed_fp16"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("tools.offline", response.json()["detail"])

    def test_comparison_rejects_before_candidate_resolution(self):
        response = self.client.post(
            "/api/comparisons",
            json={"candidate_ids": ["does-not-exist"], "dataset_id": "tongji"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("tools.offline", response.json()["detail"])

    def test_report_path_is_not_user_controlled(self):
        # The route must not accept an arbitrary filesystem path as a report.
        response = self.client.get("/api/evaluations/../etc/passwd/report")
        self.assertIn(response.status_code, {404, 400})

    def test_camera_payload_advertises_full_hd_mode(self):
        from palmprint_workbench.config import camera_resolution_options
        from palmprint_workbench.services.workbench import CAMERA_RESOLUTION_CHOICES

        self.assertIn("1920x1080", camera_resolution_options())
        self.assertIn(("1920 x 1080", "1920x1080"), CAMERA_RESOLUTION_CHOICES)

    def test_preview_encoding_downscales_without_changing_capture_contract(self):
        import numpy as np
        from palmprint_workbench.api.server import _preview_jpeg

        frame = SimpleNamespace(rgb=np.zeros((1080, 1920, 3), dtype=np.uint8))
        payload, size = _preview_jpeg(frame, max_width=960, quality=72)
        self.assertTrue(payload.startswith(b"\xff\xd8"))
        self.assertEqual(size, (960, 540))

    def test_auto_enrollment_handles_numpy_preview_frames(self):
        """A NumPy preview is not valid in a boolean ``or`` expression."""

        import numpy as np

        state = self.api_server.EnrollmentState(
            "preview-test",
            self.api_server.EnrollmentOptions(palm_side="left"),
            device="/dev/video0",
            resolution="1280x720",
        )
        frame = SimpleNamespace(rgb=np.zeros((32, 32, 3), dtype=np.uint8))
        roi_result = SimpleNamespace(
            preview=np.full((32, 32, 3), 24, dtype=np.uint8),
            roi=None,
            quality={},
            geometry={},
        )

        def extract_and_stop(*_args):
            state.stop_event.set()
            return roi_result, "ROI 提取失败"

        with patch.object(self.api_server.WORKBENCH, "open_camera_session"), patch.object(
            self.api_server.WORKBENCH, "capture_camera_frame", return_value=frame
        ), patch.object(self.api_server.WORKBENCH, "close_cameras"), patch.object(
            self.api_server.service_layer, "_extract", side_effect=extract_and_stop
        ), patch.object(self.api_server, "AUTO_ENROLL_COUNTDOWN_SECONDS", 0.0), patch.object(
            self.api_server, "AUTO_ENROLL_BACKGROUND_SECONDS", 0.0
        ), patch.object(
            self.api_server, "AUTO_ENROLL_FRAME_INTERVAL_SECONDS", 0.0
        ):
            self.api_server._auto_capture_worker(state)

        self.assertEqual(state.phase, "waiting_hand")
        self.assertIsNone(state.last_roi_url)
        self.assertTrue(str(state.last_preview_url).startswith("data:image/jpeg;base64,"))

    def test_pyacl_error_has_restart_guidance(self):
        from palmprint_workbench.api.server import _npu_error_detail

        detail = _npu_error_detail(RuntimeError("PyACL is unavailable; source the CANN environment"))
        self.assertIn("source /usr/local/Ascend/ascend-toolkit/set_env.sh", detail)


if __name__ == "__main__":
    unittest.main()
