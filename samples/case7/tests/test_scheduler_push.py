"""Deterministic scheduler tests for explicit PhotoFrame active push.

These tests exercise the real ``ApplicationState.push_due`` and
``push_device`` methods while replacing only the renderer and HTTP transport.
They intentionally use an in-memory JPEG byte string and a nonexistent source
path, so no network request, NPU inference, or derived image cache is used.
"""

import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import app
from device_registry import DeviceRegistry
from server_config import ConfigStore


class _FakeSelector:
    revision = 3
    weather = {"status": "ok", "weather_code": 0}

    def __init__(self):
        self.now = datetime(2026, 8, 23, 12, 0)

    def _now(self):
        return self.now

    def current_for_device(self, device_id, policy, force=False):
        return {
            "id": 1,
            "filepath": "/nonexistent/managed-photo.jpg",
            "filename": "managed-photo.jpg",
            "sha256": "fixed-photo-digest",
            "selection_revision": self.revision,
            "weather": "晴天",
        }


class _FakeRenderer:
    def __init__(self):
        self.calls = 0
        self.policies = []

    def render(self, path, policy, timezone, weather_label):
        self.calls += 1
        self.policies.append(
            {
                "width": int(policy["width"]),
                "height": int(policy["height"]),
                "rotation": int(policy.get("rotation", 0)),
            }
        )
        return b"\xff\xd8fake-jpeg\xff\xd9", int(policy["width"]), int(policy["height"])


class _FakePushClient:
    calls = []
    fail = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def push_jpeg(self, base_url, body, *, photo_id=None, etag=None, headers=None):
        type(self).calls.append({"base_url": base_url, "photo_id": photo_id, "etag": etag, "body": body})
        if type(self).fail:
            raise OSError("synthetic E1002 transport failure")
        return SimpleNamespace(status_code=200, attempts=1)


class SchedulerPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.registry = DeviceRegistry(root / "devices.json")
        created = self.registry.handshake({
            "name": "e1002-test",
            "profile_id": "seeedstudio_reterminal_e1002",
            "display": {"kind": "photoframe", "width": 800, "height": 480, "codecs": ["jpeg"]},
        })
        self.device_id = created["device_id"]
        self.registry.update(self.device_id, {"policy": {"rotation_cron": ["*/5 * *"]}})
        self.registry.update(self.device_id, {
            "push": {
                "enabled": True,
                "base_url": "http://e1002.invalid",
                "protocol": "photoframe_api",
                "timeout_seconds": 1,
                "attempts": 1,
            }
        })
        self.state = SimpleNamespace(
            push_lock=threading.RLock(),
            devices=self.registry,
            selector=_FakeSelector(),
            renderer=_FakeRenderer(),
            config=ConfigStore(root / "config.json"),
        )
        # Bind the production method to the lightweight fake state. This keeps
        # scheduler and retry bookkeeping real while avoiding ApplicationState
        # construction (which would initialize ACL/NPU resources).
        self.state.push_device = lambda device_id, **kwargs: app.ApplicationState.push_device(
            self.state, device_id, **kwargs
        )
        _FakePushClient.calls = []
        _FakePushClient.fail = False

    def tearDown(self):
        self.temp.cleanup()

    def test_same_cron_slot_deduplicates_network_send(self):
        slot = "2026-08-23-12-00:*/5 * *"
        with mock.patch.object(app, "PhotoFramePushClient", _FakePushClient), \
             mock.patch.object(app, "effective_cron_slot", return_value=slot):
            app.ApplicationState.push_due(self.state)
            # Simulate a later render revision (for example a changed
            # date/time overlay) while remaining in the same cron slot.
            self.state.selector.revision = 99
            app.ApplicationState.push_due(self.state)

        self.assertEqual(len(_FakePushClient.calls), 1)
        self.assertEqual(self.state.renderer.calls, 1)
        # A successful physical refresh remains authoritative for the slot;
        # the second scheduler tick is skipped before rendering or HTTP.
        self.assertEqual(self.registry.get(self.device_id)["push"]["last_status"], "ok")
        self.assertEqual(self.registry.get(self.device_id)["push"]["last_slot"], slot)
        self.assertEqual(self.registry.get(self.device_id)["push"]["last_success_slot"], slot)
        self.assertEqual(list(Path(self.temp.name).rglob("*.jpg")), [])
        self.assertEqual(list(Path(self.temp.name).rglob("*.jpeg")), [])

    def test_force_send_can_repeat_a_successful_slot_explicitly(self):
        slot = "2026-08-23-12-00:*/5 * *"
        with mock.patch.object(app, "PhotoFramePushClient", _FakePushClient), \
             mock.patch.object(app, "effective_cron_slot", return_value=slot):
            app.ApplicationState.push_due(self.state)
            repeated = app.ApplicationState.push_device(self.state, self.device_id)
            self.assertEqual(repeated["status"], "not_modified")
            self.assertEqual(len(_FakePushClient.calls), 1)
            result = app.ApplicationState.push_device(
                self.state,
                self.device_id,
                scheduled_slot=slot,
                force_send=True,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(_FakePushClient.calls), 2)
        self.assertEqual(self.registry.get(self.device_id)["push"]["last_success_slot"], slot)

    def test_failed_slot_waits_for_retry_at_but_next_slot_retries(self):
        first_slot = "2026-08-23-12-00:*/5 * *"
        next_slot = "2026-08-23-12-05:*/5 * *"
        _FakePushClient.fail = True
        with mock.patch.object(app, "PhotoFramePushClient", _FakePushClient), \
             mock.patch.object(app, "effective_cron_slot", return_value=first_slot):
            # push_due intentionally absorbs one device failure after the
            # registry records retry_at; this models the five-second loop.
            app.ApplicationState.push_due(self.state)
            failed = self.registry.get(self.device_id)["push"]
            self.assertEqual(failed["last_status"], "error")
            self.assertEqual(failed["last_slot"], first_slot)
            self.assertIsNotNone(failed["retry_at"])
            app.ApplicationState.push_due(self.state)

        self.assertEqual(len(_FakePushClient.calls), 1)

        # A new cron slot is eligible even while the previous slot's retry
        # window is active. The next request must be attempted once.
        _FakePushClient.fail = False
        with mock.patch.object(app, "PhotoFramePushClient", _FakePushClient), \
             mock.patch.object(app, "effective_cron_slot", return_value=next_slot):
            app.ApplicationState.push_due(self.state)

        self.assertEqual(len(_FakePushClient.calls), 2)
        pushed = self.registry.get(self.device_id)["push"]
        self.assertEqual(pushed["last_status"], "ok")
        self.assertEqual(pushed["last_slot"], next_slot)
        self.assertIsNone(pushed["retry_at"])
        self.assertEqual(list(Path(self.temp.name).rglob("*.jpg")), [])
        self.assertEqual(list(Path(self.temp.name).rglob("*.jpeg")), [])

    def test_profiled_waveshare_push_uses_fixed_portrait_contract_without_degree_rotation(self):
        """The active-push path must enforce the registered product profile."""

        created = self.registry.handshake(
            {
                "name": "waveshare-portrait",
                "profile_id": "waveshare_photopainter_73",
                "display": {
                    "kind": "photoframe",
                    "width": 480,
                    "height": 800,
                    "orientation": "portrait",
                    "codecs": ["jpeg"],
                },
            }
        )
        profile_device_id = created["device_id"]
        self.registry.update(
            profile_device_id,
            {
                "push": {
                    "enabled": True,
                    "base_url": "http://waveshare.invalid",
                    "protocol": "photoframe_api",
                }
            },
        )

        with mock.patch.object(app, "PhotoFramePushClient", _FakePushClient):
            result = app.ApplicationState.push_device(self.state, profile_device_id, force=True)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["width"], 480)
        self.assertEqual(result["height"], 800)
        self.assertEqual(self.state.renderer.policies[-1], {"width": 480, "height": 800, "rotation": 0})

    def test_profiled_push_rejects_stale_degree_rotation_policy(self):
        """A corrupted legacy policy cannot re-enable degree rotation."""

        created = self.registry.handshake(
            {
                "name": "waveshare-stale-policy",
                "profile_id": "waveshare_photopainter_73",
                "display": {
                    "kind": "photoframe",
                    "width": 800,
                    "height": 480,
                    "orientation": "landscape",
                    "codecs": ["jpeg"],
                },
            }
        )
        profile_device_id = created["device_id"]
        self.registry.update(
            profile_device_id,
            {
                "push": {
                    "enabled": True,
                    "base_url": "http://waveshare.invalid",
                    "protocol": "photoframe_api",
                }
            },
        )
        # Simulate a pre-profile registry edit; normal API updates reject this
        # value, but push_device must remain defensive at its own boundary.
        self.registry._data["devices"][profile_device_id]["policy"]["rotation"] = 90

        with self.assertRaises(app.PushError):
            app.ApplicationState.push_device(self.state, profile_device_id, force=True)

    def test_weather_tick_does_not_rotate_restored_display(self):
        """Weather refresh and local display rotation use independent clocks."""

        class StopAfterOneWait:
            def __init__(self):
                self.calls = 0

            def wait(self, _seconds):
                self.calls += 1
                return self.calls > 1

        weather_calls = []
        display_calls = []
        state = SimpleNamespace(
            _initial_display_deadline=10**18,
            _initial_epaper_deadline=10**18,
            scheduler_stop=StopAfterOneWait(),
            config=SimpleNamespace(
                get=lambda: {
                    "weather": {"refresh_seconds": 1800},
                    "display": {"enabled": True, "interval_seconds": 3600},
                }
            ),
            selector=SimpleNamespace(refresh_weather=lambda: weather_calls.append(True)),
            refresh_display=lambda **kwargs: display_calls.append(kwargs),
            refresh_epaper=lambda **kwargs: self.fail("e-paper must not refresh before its deadline"),
            push_due=lambda: None,
        )

        app.ApplicationState._schedule_loop(state)

        self.assertEqual(len(weather_calls), 1)
        self.assertEqual(display_calls, [])

    def test_resume_restarts_epaper_interval_without_an_immediate_refresh(self):
        """Resume selects locally but never consumes an e-paper refresh slot."""

        class StopAfterThreeWaits:
            def __init__(self):
                self.calls = 0

            def wait(self, _seconds):
                self.calls += 1
                return self.calls > 3

        values = iter([
            {
                "weather": {"refresh_seconds": 1800},
                "display": {"enabled": False, "touchscreen_interval_seconds": 60},
                "epaper": {"rotation_interval_seconds": 1800},
            },
            {
                "weather": {"refresh_seconds": 1800},
                "display": {"enabled": True, "touchscreen_interval_seconds": 60},
                "epaper": {"rotation_interval_seconds": 1800},
            },
            {
                "weather": {"refresh_seconds": 1800},
                "display": {"enabled": True, "touchscreen_interval_seconds": 60},
                "epaper": {"rotation_interval_seconds": 1800},
            },
        ])
        display_calls = []
        epaper_calls = []
        state = SimpleNamespace(
            _initial_display_deadline=0.0,
            _initial_epaper_deadline=0.0,
            scheduler_stop=StopAfterThreeWaits(),
            config=SimpleNamespace(get=lambda: next(values)),
            selector=SimpleNamespace(refresh_weather=lambda: None),
            refresh_display=lambda **kwargs: display_calls.append(kwargs),
            refresh_epaper=lambda **kwargs: epaper_calls.append(kwargs),
            push_due=lambda: None,
        )

        app.ApplicationState._schedule_loop(state)

        self.assertEqual(len(display_calls), 1)
        self.assertEqual(display_calls[0]["render_epaper"], False)
        self.assertEqual(epaper_calls, [])


if __name__ == "__main__":
    unittest.main()
