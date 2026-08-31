import unittest
from dataclasses import replace
from unittest import mock

import app
from model_registry import load_candidates


class FakeIndex:
    def stats(self):
        return {
            "total_photos": 0,
            "available_photos": 0,
            "unavailable_photos": 0,
            "photos_with_faces": 0,
            "embeddings_by_model": {},
        }


class FakeRegistry:
    def ids(self):
        return ()


class FakeEpaper:
    config = type("Config", (), {"backend": "dry-run"})()


class AppContractTests(unittest.TestCase):
    def test_portrait_capability_keeps_its_encoded_aspect(self):
        options = app._render_options(
            {"width": "480", "height": "800", "orientation": "portrait"},
            {"width": 800, "height": 480, "max_bytes": 200000, "quality": 82},
        )
        self.assertEqual((options["width"], options["height"]), (480, 800))
        self.assertEqual(options["target_orientation"], "portrait")
        self.assertIn("480x800", options["variant"])

    def test_epaper_variant_uses_epaper_policy_only(self):
        base = {
            "display": {"orientation_mode": "auto", "rotation": 0},
            "epaper": {"orientation_mode": "match_display", "rotation": 270, "e6_dither": False},
        }
        changed_touchscreen = {
            **base,
            "display": {"orientation_mode": "match_display", "rotation": 90},
        }
        self.assertEqual(app._epaper_options(base), app._epaper_options(changed_touchscreen))
        changed_epaper = {**base, "epaper": {**base["epaper"], "rotation": 90}}
        self.assertNotEqual(app._epaper_options(base)["variant"], app._epaper_options(changed_epaper)["variant"])
        # A migrated E6 capability may contain a default rotation field; it
        # must not mask the server-wide epaper policy.
        self.assertEqual(
            app._epaper_options(base, {"rotation": 0, "orientation_mode": "auto"}),
            app._epaper_options(base),
        )

    def test_display_header_negotiation_requires_a_consistent_pair(self):
        capability = {"width": 800, "height": 480, "max_bytes": 200000}
        portrait = app._display_capability_with_headers(capability, orientation="portrait")
        self.assertEqual((portrait["width"], portrait["height"]), (480, 800))
        stale = {"width": 800, "height": 480, "orientation": "portrait", "max_bytes": 200000}
        normalized = app._display_capability_with_headers(stale, orientation="auto")
        self.assertEqual(normalized["orientation"], "landscape")
        with self.assertRaises(app.DisplayPolicyError):
            app._display_capability_with_headers(capability, width="480")
        with self.assertRaises(app.DisplayPolicyError):
            app._display_capability_with_headers(capability, width="800", height="480", orientation="portrait")
        with self.assertRaises(app.DisplayPolicyError):
            app._render_options(
                {"width": "800", "height": "480", "orientation": "portrait"},
                {"width": 800, "height": 480, "max_bytes": 200000, "quality": 82},
            )

    def test_models_payload_exposes_component_precision_contract(self):
        candidate = next(
            value
            for value in load_candidates()
            if value.model_id == "mobileclip_s0__npu__mixed_fp16"
        )

        class Registry:
            def ids(self):
                return (candidate.model_id,)

            def get(self, model_id):
                self.asserted = model_id
                return candidate

        state = type("State", (), {"registry": Registry()})()
        with mock.patch.object(app, "_state", state), mock.patch.object(
            app, "load_candidates", return_value=(candidate,)
        ):
            payload = app.models_payload()
        self.assertEqual(payload[0]["precision"], "allow_fp32_to_fp16")
        self.assertEqual(
            payload[0]["components"]["image"]["precision_mode"],
            "allow_fp32_to_fp16",
        )
        self.assertEqual(
            payload[0]["components"]["text"]["precision_mode"],
            "allow_fp32_to_fp16",
        )
        self.assertEqual(
            payload[0]["precision_strategy"]["candidate_id"],
            "C0",
        )

    def test_models_payload_exposes_admitted_precision_strategy(self):
        candidate = next(
            value
            for value in load_candidates()
            if value.model_id == "mobileclip_s0__npu__mixed_fp16"
        )
        admitted_record = replace(
            candidate,
            precision_strategy={
                "kind": "selective_mixed_precision",
                "candidate_id": "C0",
            },
        )
        admitted = type(
            "Admitted",
            (),
            {
                "ids": lambda self: (candidate.model_id,),
                "get": lambda self, _model_id: admitted_record,
            },
        )()
        state = type("State", (), {"registry": admitted})()
        with mock.patch.object(app, "_state", state), mock.patch.object(
            app, "load_candidates", return_value=(candidate,)
        ):
            payload = app.models_payload()
        self.assertEqual(payload[0]["precision_strategy"]["candidate_id"], "C0")

    def test_auto_upload_requires_admitted_npu_models(self):
        with self.assertRaises(app.AlbumIndexError):
            app._upload_model_ids(FakeRegistry(), {"auto_index_uploads": True, "models": []})
        self.assertEqual(
            app._upload_model_ids(
                type("Registry", (), {"ids": lambda self: ("m1", "m2")})(),
                {"auto_index_uploads": True, "models": ["m2"]},
            ),
            ("m2",),
        )
        self.assertEqual(
            app._upload_model_ids(FakeRegistry(), {"auto_index_uploads": False, "models": []}),
            (),
        )

    def test_health_is_degraded_without_admitted_models(self):
        state = type(
            "State",
            (),
            {"registry": FakeRegistry(), "index": FakeIndex(), "epaper": FakeEpaper()},
        )()
        with mock.patch.object(app, "_state", state), mock.patch.object(
            app,
            "_npu_snapshot",
            return_value={
                "pyacl_available": True,
                "npu_smi_available": True,
                "device": "Ascend 310B4",
                "health": "Alarm",
            },
        ):
            payload = app.health_payload()
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(len(payload["missing_required_models"]), 3)
        self.assertEqual(payload["npu"]["health"], "Alarm")

    def test_system_status_uses_explicit_line_breaks(self):
        state = type(
            "State",
            (),
            {"registry": FakeRegistry(), "index": FakeIndex(), "epaper": FakeEpaper()},
        )()
        with mock.patch.object(app, "_state", state), mock.patch.object(
            app,
            "_npu_snapshot",
            return_value={
                "pyacl_available": True,
                "npu_smi_available": True,
                "device": "Ascend 310B4",
                "health": "Alarm",
            },
        ):
            output = app.system_markdown()
        self.assertIn("**服务状态**　degraded<br>\n**NPU**", output)


if __name__ == "__main__":
    unittest.main()
