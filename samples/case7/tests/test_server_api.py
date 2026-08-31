import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

import app
from device_registry import DeviceRegistry
from admin_auth import AdminTokenStore
from photo_index import ImportSummary
from server_config import ConfigStore


class FakeRegistry:
    def ids(self):
        return ()


class FakeIndex:
    def __init__(self, row):
        self.row = row
        self.saved = {}
        self.history = []

    def stats(self):
        return {"total_photos": 1, "available_photos": 1, "unavailable_photos": 0, "photos_with_faces": 0, "embeddings_by_model": {}}

    def get_photo(self, photo_id):
        return self.row if int(photo_id) == 1 else None

    def list_photos(self, face_filter="all", limit=None):
        return [self.row][: int(limit) if limit is not None else None]

    def get_display_state(self, device_id):
        return self.saved.get(str(device_id))

    def save_display_state(self, device_id, photo_id, slot_key, policy_revision, selection_revision):
        self.saved[str(device_id)] = {"device_id": str(device_id), "photo_id": photo_id, "slot_key": slot_key, "policy_revision": policy_revision, "selection_revision": selection_revision}

    def display_history_ids(self, device_id, limit=12):
        return list(self.history)[-limit:][::-1]

    def record_display_history(self, device_id, photo_id, keep=12):
        self.history.append(int(photo_id))

    def rewind_display_history(self, device_id, photo_id):
        history = self.display_history_ids(device_id, len(self.history))
        try:
            position = history.index(int(photo_id))
        except ValueError:
            return False
        if position:
            del self.history[len(self.history) - position:]
        return True

    def pop_display_history(self, device_id, expected_photo_id=None):
        if not self.history or (expected_photo_id is not None and self.history[-1] != int(expected_photo_id)):
            return None
        return self.history.pop()


class FakeSelector:
    revision = 7

    def __init__(self, row):
        self.row = row

    def current_photo(self, profile):
        return dict(self.row, weather="晴天", selection_revision=self.revision)

    def status(self):
        return {
            "selection_revision": self.revision,
            "current": dict(self.row, weather="晴天", selection_revision=self.revision),
            "weather": {"status": "ok"},
        }


class FakeEpaper:
    config = type("Config", (), {"backend": "dry-run"})()


class _PeerOverrideApp:
    """Inject a deterministic TCP peer without relying on TestClient kwargs.

    Starlette added the ``client=`` TestClient argument at different times
    across supported board environments.  Wrapping the ASGI app keeps the
    PhotoFrame source-IP proof test portable across those versions.
    """

    def __init__(self, app, client):
        self.app = app
        self.client = client

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            scope = dict(scope)
            scope["client"] = self.client
        await self.app(scope, receive, send)


class ServerApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_patch = mock.patch.object(app, "DATA_DIR", str(root / "data"))
        self.data_patch.start()
        image = root / "photo.jpg"
        Image.new("RGB", (80, 60), (10, 20, 30)).save(image)
        self.row = {"id": 1, "filepath": str(image), "filename": "photo.jpg", "sha256": "digest", "mime_type": "image/jpeg", "upload_time": 1.0, "capture_time": None, "tags": "晴天"}
        self.state = type("State", (), {})()
        self.state.registry = FakeRegistry()
        self.state.index = FakeIndex(self.row)
        self.state.config = ConfigStore(root / "config.json")
        self.state.devices = DeviceRegistry(root / "devices.json")
        self.state.selector = FakeSelector(self.row)
        self.state.epaper = FakeEpaper()
        self.state.admin = AdminTokenStore(root / "secrets" / "admin.token")
        self.state.renderer = app.PhotoRenderer()
        self.upload_submissions = []

        def submit_upload(paths, capture_time=None):
            self.upload_submissions.append((list(paths), capture_time))
            # The production worker owns these staged files. The fake removes
            # them immediately so the test does not leave upload artifacts.
            for path in paths:
                Path(path).unlink(missing_ok=True)
            return {"job_id": "test-upload-job", "status": "queued", "progress": 0}

        self.state.submit_upload = submit_upload
        self.refresh_weather_arguments = []

        def refresh_display(refresh_weather=True):
            self.refresh_weather_arguments.append(refresh_weather)
            return self.row

        self.state.refresh_display = refresh_display
        # Verified registration writes the server's URL into an ESP32.  Use a
        # routable LAN base URL rather than TestClient's default ``testserver``
        # so registration tests exercise the same strict origin validation as
        # the board service.
        self.client = TestClient(app.create_app(), base_url="http://192.168.1.135:7860")
        # The PhotoFrame data-plane proof additionally requires the TCP peer
        # to match the address that was verified during registration.  Keep a
        # separate client for that one role; ``self.client`` remains the
        # browser/control-plane client and must not accidentally prove a pull.
        self.photoframe_client = TestClient(
            _PeerOverrideApp(app.create_app(), ("192.168.1.137", 50000)),
            base_url="http://192.168.1.135:7860",
        )
        self.patch = mock.patch.object(app, "_state", self.state)
        self.patch.start()

    def tearDown(self):
        self.photoframe_client.close()
        self.client.close()
        self.patch.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def _create_test_photoframe(self, name="test-frame", profile_id="waveshare_photopainter_73", display=None, policy=None):
        """Create a pre-verified fixture without exercising registration APIs.

        Content, rendering, and policy tests need an existing device record;
        registration behavior itself is covered by the atomic endpoint tests.
        Creating this fixture directly keeps those concerns separate now that
        both public registration routes reject unverified PhotoFrames.
        """

        profile = app.photo_frame_profile(profile_id)
        requested = dict(display or {})
        orientation = str(requested.get("orientation") or "landscape").lower()
        width, height = int(profile["width"]), int(profile["height"])
        if orientation == "portrait":
            width, height = height, width
        requested.setdefault("width", width)
        requested.setdefault("height", height)
        requested.setdefault("orientation", orientation)
        requested.setdefault("max_bytes", 20000)
        requested.update(kind="photoframe", profile_id=profile["profile_id"], codecs=["jpeg"])
        value = self.state.devices.handshake(
            {
                "name": name,
                "profile_id": profile["profile_id"],
                "protocol_version": 1,
                "display": requested,
            },
            require_token=False,
        )
        value.pop("token", None)
        if policy is not None:
            value = self.state.devices.update(value["device_id"], {"policy": policy})
        return value

    def test_upload_accepts_more_than_legacy_file_count_limit(self):
        files = [
            ("files", (f"photo-{index}.jpg", b"fixture", "image/jpeg"))
            for index in range(51)
        ]
        response = self.client.post("/api/photos/upload", files=files)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(self.upload_submissions), 1)
        self.assertEqual(len(self.upload_submissions[0][0]), 51)

    def test_upload_ignores_legacy_manual_tags_form_field(self):
        response = self.client.post(
            "/api/photos/upload",
            data={"tags": "手工 标签"},
            files={"files": ("photo.jpg", b"fixture", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(self.upload_submissions), 1)
        submitted_paths, capture_time = self.upload_submissions[0]
        self.assertEqual(len(submitted_paths), 1)
        self.assertIsNone(capture_time)

    def test_upload_rejects_empty_multipart_request(self):
        response = self.client.post("/api/photos/upload", files=[])
        self.assertEqual(response.status_code, 400)

    def test_gallery_preview_is_inline_jpeg_for_mpo_source(self):
        # The fixture bytes are a normal JPEG, but metadata intentionally
        # carries the MIME found on camera MPO imports.  Preview consumers
        # must not depend on the source MIME or FileResponse download headers.
        self.row["mime_type"] = "image/mpo"
        response = self.client.get("/api/photos/1/preview?width=96&height=72")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(response.headers["content-disposition"], "inline")
        self.assertEqual(response.headers["x-album-preview"], "1")
        self.assertLessEqual(len(response.content), 2 * 1024 * 1024)
        with Image.open(io.BytesIO(response.content)) as rendered:
            self.assertEqual(rendered.format, "JPEG")
            self.assertLessEqual(rendered.width, 96)
            self.assertLessEqual(rendered.height, 72)
        unchanged = self.client.get(
            "/api/photos/1/preview?width=96&height=72",
            headers={"If-None-Match": response.headers["etag"]},
        )
        self.assertEqual(unchanged.status_code, 304)
        self.assertEqual(unchanged.headers["etag"], response.headers["etag"])

    def test_original_mpo_file_remains_download_attachment(self):
        """Keep the source endpoint distinct from the browser preview URL."""
        self.row["mime_type"] = "image/mpo"
        response = self.client.get("/api/photos/1/file")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/mpo")
        self.assertIn("attachment", response.headers["content-disposition"].lower())
        self.assertNotIn("inline", response.headers["content-disposition"].lower())

    def test_gallery_and_search_contract_exposes_preview_and_original_urls(self):
        gallery = self.client.get("/api/photos?limit=1")
        self.assertEqual(gallery.status_code, 200)
        photo = gallery.json()["photos"][0]
        self.assertIn("/preview?width=480&height=360", photo["url"])
        self.assertEqual(photo["preview_url"], photo["url"])
        self.assertEqual(photo["file_url"], "/api/photos/1/file")

        class SearchIndex(FakeIndex):
            def search_text(self, query, model_id, k):
                from photo_index import SearchResult

                return [SearchResult(1, self.row["filepath"], self.row["filename"], 0, 0.5, model_id)]

        self.state.index = SearchIndex(self.row)
        self.state.selector = FakeSelector(self.row)
        # The search route only needs the index result here; avoid loading a
        # real NPU model by replacing the routing helper with a fixed ID.
        with mock.patch.object(app, "resolve_text_model", return_value="model_a__npu__mixed_fp16"):
            result = self.client.post("/api/search/text", json={"query": "房子", "model": "auto", "top_k": 1})
        self.assertEqual(result.status_code, 200)
        item = result.json()["results"][0]
        self.assertIn("/preview?width=480&height=360", item["url"])
        self.assertEqual(item["preview_url"], item["url"])
        self.assertEqual(item["file_url"], "/api/photos/1/file")

    def test_upload_job_exposes_real_indexing_progress_fields(self):
        class UploadRegistry:
            def ids(self):
                return ("model_a__npu__mixed_fp16",)

        class UploadConfig:
            def get(self):
                return {"index": {"auto_index_uploads": True, "models": ["model_a__npu__mixed_fp16"]}}

        class UploadIndex:
            def __init__(self):
                self.events = []

            def import_uploads(self, paths, model_ids, progress_reporter=None):
                self.events.append((list(paths), tuple(model_ids)))
                progress_reporter({"phase": "importing", "files_completed": 1, "files_total": 1, "accepted": 1, "duplicates": 0})
                progress_reporter({"phase": "validating", "files_completed": 1, "files_total": 1, "embedding_completed": 0, "embedding_total": 0})
                progress_reporter({"phase": "embedding", "files_completed": 1, "files_total": 1, "embedding_completed": 1, "embedding_total": 1, "current_model": "model_a__npu__mixed_fp16"})
                progress_reporter({"phase": "finalizing", "files_completed": 1, "files_total": 1, "embedding_completed": 1, "embedding_total": 1})
                return ImportSummary(discovered=1, indexed=1)

            def find_by_sha256(self, digest):
                return {"id": 9}

            def update_photo_metadata(self, photo_id, metadata):
                self.metadata = (photo_id, dict(metadata))

        root = Path(self.temp.name)
        staged = root / "upload.jpg"
        staged.write_bytes(b"progress-fixture")
        upload_state = app.ApplicationState.__new__(app.ApplicationState)
        upload_state.jobs = {}
        upload_state.jobs_lock = threading.RLock()
        upload_state.executor = ThreadPoolExecutor(max_workers=1)
        upload_state.registry = UploadRegistry()
        upload_state.config = UploadConfig()
        upload_state.index = UploadIndex()
        upload_state.selector = type("Selector", (), {"state_lock": threading.RLock(), "revision": 1})()
        job = None
        try:
            job = upload_state.submit_upload([str(staged)])
        finally:
            upload_state.executor.shutdown(wait=True, cancel_futures=False)
        completed = upload_state.jobs[job["job_id"]]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["phase"], "completed")
        self.assertEqual(completed["progress"], 1.0)
        self.assertEqual(completed["files_total"], 1)
        self.assertEqual(completed["index_files_completed"], 1)
        self.assertEqual(completed["embedding_completed"], 1)
        self.assertEqual(completed["photo_ids"], [9])
        self.assertFalse(staged.exists())

    def test_device_handshake_manifest_content_and_etag(self):
        handshake = self.client.post("/api/devices/handshake", json={"name": "lcd", "display": {"kind": "lcd", "width": 64, "height": 48, "codecs": ["jpeg"], "max_bytes": 20000}})
        self.assertEqual(handshake.status_code, 200)
        value = handshake.json()
        manifest = self.client.get(f"/api/devices/{value['device_id']}/manifest")
        self.assertEqual(manifest.status_code, 200)
        etag = manifest.json()["etag"]
        content = self.client.get(f"/api/devices/{value['device_id']}/content")
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.headers["content-type"], "image/jpeg")
        unchanged = self.client.get(f"/api/devices/{value['device_id']}/content", headers={"If-None-Match": etag})
        self.assertEqual(unchanged.status_code, 304)

    def test_photoframe_url_rotation_uses_etag_and_standard_auth(self):
        value = self._create_test_photoframe(name="waveshare")
        url = f"/api/devices/{value['device_id']}/photoframe"
        headers = {"X-Display-Width": "480", "X-Display-Height": "800", "X-Display-Orientation": "portrait"}
        content = self.client.get(url, headers=headers)
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.headers["content-type"], "image/jpeg")
        self.assertLessEqual(int(content.headers["x-album-width"]), 480)
        self.assertLessEqual(int(content.headers["x-album-height"]), 800)
        unchanged = self.client.get(url, headers={**headers, "If-None-Match": content.headers["etag"]})
        self.assertEqual(unchanged.status_code, 304)
        state = self.client.get(f"/api/admin/devices/{value['device_id']}/state")
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()["etag"], content.headers["etag"])
        resized = self.client.get(url, headers={"X-Display-Width": "800", "X-Display-Height": "480", "X-Display-Orientation": "landscape", "If-None-Match": content.headers["etag"]})
        self.assertEqual(resized.status_code, 200)

    def test_dimension_override_recomputes_registered_orientation(self):
        value = self._create_test_photoframe(
            name="switchable-frame",
            display={"orientation": "landscape"},
        )
        device_id = value["device_id"]
        # The device was registered as landscape. Supplying a complete
        # portrait capability must replace that stale label even when the
        # firmware omits X-Display-Orientation.
        response = self.client.get(
            f"/api/devices/{device_id}/photoframe",
            headers={"X-Display-Width": "480", "X-Display-Height": "800"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-album-target-orientation"], "portrait")
        self.assertEqual(response.headers["x-album-orientation-mode"], "auto")

    def test_photoframe_rejects_invalid_orientation(self):
        value = self._create_test_photoframe(name="invalid-orientation")
        response = self.client.get(f"/api/devices/{value['device_id']}/photoframe", headers={"X-Display-Orientation": "diagonal"})
        self.assertEqual(response.status_code, 400)

    def test_mismatched_orientation_handshake_does_not_leave_a_device(self):
        response = self.client.post(
            "/api/devices/handshake",
            json={"name": "bad-frame", "display": {"kind": "lcd", "width": 80, "height": 48, "orientation": "portrait", "codecs": ["jpeg"], "max_bytes": 20000}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/devices").json()["devices"], [])

    def test_legacy_mismatched_capability_returns_a_client_error(self):
        value = self.client.post(
            "/api/devices/handshake",
            json={"name": "legacy-frame", "display": {"kind": "lcd", "width": 64, "height": 48, "codecs": ["jpeg"], "max_bytes": 20000}},
        ).json()
        device_id = value["device_id"]
        raw = self.state.devices._data["devices"][device_id]
        raw["display"]["orientation"] = "portrait"
        response = self.client.get(f"/api/devices/{device_id}/manifest")
        self.assertEqual(response.status_code, 400)

    def test_local_content_negotiates_portrait_viewport_and_orientation_mode(self):
        preserved = self.client.get(
            "/api/display/content?profile=jpeg&width=48&height=64&orientation=portrait&orientation_mode=auto"
        )
        self.assertEqual(preserved.status_code, 200)
        self.assertEqual((preserved.headers["x-album-width"], preserved.headers["x-album-height"]), ("48", "36"))
        self.assertEqual(preserved.headers["x-album-orientation"], "landscape")
        self.assertEqual(preserved.headers["x-album-target-orientation"], "portrait")
        matched = self.client.get(
            "/api/display/content?profile=jpeg&width=48&height=64&orientation=portrait&orientation_mode=match_display"
        )
        self.assertEqual(matched.status_code, 200)
        self.assertEqual((matched.headers["x-album-width"], matched.headers["x-album-height"]), ("48", "64"))
        self.assertNotEqual(preserved.headers["etag"], matched.headers["etag"])
        self.assertEqual(matched.headers["x-album-orientation"], "portrait")
        self.assertEqual(matched.headers["x-album-target-orientation"], "portrait")

    def test_display_current_url_and_etag_match_negotiated_content(self):
        current = self.client.get(
            "/api/display/current?width=48&height=64&orientation=portrait"
        )
        self.assertEqual(current.status_code, 200)
        value = current.json()["current"]
        content = self.client.get(value["url"])
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.headers["etag"], value["etag"])

    def test_display_current_square_viewport_has_a_follow_up_content_url(self):
        current = self.client.get("/api/display/current?width=60&height=60")
        self.assertEqual(current.status_code, 200)
        value = current.json()["current"]
        self.assertNotIn("orientation=square", value["url"])
        content = self.client.get(value["url"])
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.headers["etag"], value["etag"])

    def test_photoframe_orientation_policy_is_in_etag_and_response(self):
        value = self._create_test_photoframe(
            name="portrait-frame",
            display={"orientation": "portrait"},
        )
        url = f"/api/devices/{value['device_id']}/photoframe"
        first = self.client.get(url, headers={"X-Display-Orientation": "portrait"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["x-album-orientation"], "portrait")
        changed = self.client.patch(
            f"/api/admin/devices/{value['device_id']}",
            json={"policy": {"orientation_mode": "match_display"}},
        )
        self.assertEqual(changed.status_code, 200)
        second = self.client.get(
            url,
            headers={"X-Display-Orientation": "portrait", "If-None-Match": first.headers["etag"]},
        )
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.headers["etag"], second.headers["etag"])

    def test_photoframe_etag_ignores_touchscreen_only_configuration(self):
        value = self._create_test_photoframe(
            name="isolated-frame",
            profile_id="seeedstudio_reterminal_e1002",
        )
        url = f"/api/devices/{value['device_id']}/photoframe"
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        config = self.client.get("/api/config").json()
        changed = self.client.patch(
            "/api/config",
            json={"revision": config["revision"], "display": {"rotation": 90}},
        )
        self.assertEqual(changed.status_code, 200)
        unchanged = self.client.get(url, headers={"If-None-Match": first.headers["etag"]})
        self.assertEqual(unchanged.status_code, 304)

    def test_configuration_revision_conflict_is_reported(self):
        current = self.client.get("/api/config").json()
        updated = self.client.patch("/api/config", json={"revision": current["revision"], "device": {"jpeg_quality": 70}})
        self.assertEqual(updated.status_code, 200)
        stale = self.client.patch("/api/config", json={"revision": current["revision"], "timezone": "UTC"})
        self.assertEqual(stale.status_code, 409)

    def test_public_status_hides_paths_hashes_and_device_token_hashes(self):
        health = self.client.get("/api/health").json()
        self.assertNotIn("filepath", health["selection"]["current"])
        self.assertNotIn("sha256", health["selection"]["current"])
        self.client.post("/api/devices/handshake", json={"display": {"kind": "lcd", "width": 64, "height": 48, "codecs": ["jpeg"], "max_bytes": 20000}})
        device = self.client.get("/api/devices").json()["devices"][0]
        self.assertNotIn("token_hash", device)

    def test_admin_pairing_requires_verified_registration(self):
        """The legacy pending route cannot create an unverified PhotoFrame."""

        response = self.client.post(
            "/api/admin/devices",
            json={
                "name": "den",
                "profile_id": "waveshare_photopainter_73",
                "policy": {"crop_mode": "fit"},
            },
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertEqual(detail["registration_status"], "not_registered")
        self.assertEqual(detail["next"], "/api/admin/devices/register")
        self.assertEqual(self.state.devices.list(), [])

    @mock.patch.object(app, "PhotoFrameProvisioner")
    def test_atomic_registration_does_not_leave_unreachable_device(self, provisioner_class):
        """A failed connection must not be presented as a registered device."""

        provisioner_class.return_value.provision.side_effect = app.ProvisionError(
            "PhotoFrame /api/system-info is unreachable: timed out",
            kind="transport",
        )
        response = self.client.post(
            "/api/admin/devices/register",
            json={
                "name": "offline-frame",
                "profile_id": "waveshare_photopainter_73",
                "device_url": "http://192.168.1.137",
            },
        )
        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["detail"]["registration_status"], "not_registered")
        self.assertEqual(self.state.devices.list(), [])
        provisioner_class.return_value.provision.assert_called_once()

    @mock.patch.object(app, "PhotoFrameProvisioner")
    def test_atomic_registration_requires_device_url_and_reports_rotate_failure(self, provisioner_class):
        missing = self.client.post(
            "/api/admin/devices/register",
            json={"name": "missing-url", "profile_id": "waveshare_photopainter_73"},
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(self.state.devices.list(), [])

        provisioner_class.return_value.provision.return_value = app.ProvisionResult(
            device_url="http://192.168.1.137",
            device_hardware_id="a4cb8fdaa1dc",
            firmware_version="v2.18.0",
            board_name="Waveshare 7.3 7-Color",
            configured_image_url="http://testserver/api/devices/pending/photoframe",
            rotation_cron=("*/30 * *",),
            display_orientation="landscape",
            rotate_requested=True,
            rotate_status="failed",
            rotate_error="PhotoFrame /api/rotate returned HTTP 500",
            rotate_http_status=500,
        )
        response = self.client.post(
            "/api/admin/devices/register",
            json={
                "name": "configured-frame",
                "profile_id": "waveshare_photopainter_73",
                "device_url": "http://192.168.1.137",
            },
        )
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["registration_status"], "awaiting_pull")
        self.assertEqual(body["connection_evidence"], "device_http_verified")
        self.assertEqual(body["pull_status"], "awaiting_device_pull")
        self.assertEqual(body["immediate_refresh"], "failed")
        # A failed optional /api/rotate request does not invalidate the
        # server-side configuration proof.  The physical pull remains
        # asynchronous and is reported as awaiting_pull until official
        # PhotoFrame headers arrive.
        self.assertIn("等待设备", body["message"])
        self.assertEqual(body["pull_provision"]["status"], "awaiting_pull")
        self.assertEqual(len(self.state.devices.list()), 1)

    @mock.patch.object(app, "PhotoFrameProvisioner")
    def test_atomic_registration_rejects_a_duplicate_normalized_device_url(self, provisioner_class):
        """One PhotoFrame root can own only one managed pull registration."""

        provisioner_class.return_value.provision.return_value = app.ProvisionResult(
            device_url="http://192.168.1.137",
            device_hardware_id="a4cb8fdaa1dc",
            firmware_version="v2.18.0",
            board_name="Waveshare PhotoPainter 7.3",
            configured_image_url="http://192.168.1.135:7860/api/devices/pending/photoframe",
            rotation_cron=("*/30 * *",),
            display_orientation="landscape",
            rotate_requested=True,
            rotate_status="requested",
        )
        first = self.client.post(
            "/api/admin/devices/register",
            json={
                "name": "first-frame",
                "profile_id": "waveshare_photopainter_73",
                "device_url": "http://192.168.1.137/",
            },
        )
        self.assertEqual(first.status_code, 202)
        first_id = first.json()["device_id"]

        duplicate = self.client.post(
            "/api/admin/devices/register",
            json={
                "name": "same-frame-again",
                "profile_id": "waveshare_photopainter_73",
                "device_url": "http://192.168.1.137",
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        detail = duplicate.json()["detail"]
        self.assertEqual(detail["registration_status"], "already_registered")
        self.assertEqual(detail["device_id"], first_id)
        self.assertEqual(len(self.state.devices.list()), 1)
        # The duplicate check runs inside the provisioning lock before any
        # second probe/configuration can touch the ESP32.
        self.assertEqual(provisioner_class.return_value.provision.call_count, 1)

    @mock.patch.object(app, "PhotoFrameProvisioner")
    def test_atomic_registration_rejects_malformed_policy_without_server_error(self, provisioner_class):
        """Malformed policy input is a client error and leaves no record."""

        for malformed in ("not-an-object", ["not", "an", "object"], 17):
            with self.subTest(policy=malformed):
                response = self.client.post(
                    "/api/admin/devices/register",
                    json={
                        "name": "bad-policy",
                        "profile_id": "waveshare_photopainter_73",
                        "device_url": "http://192.168.1.137",
                        "policy": malformed,
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertNotEqual(response.headers.get("content-type"), "text/plain; charset=utf-8")
                self.assertEqual(self.state.devices.list(), [])
        provisioner_class.return_value.provision.assert_not_called()

    def test_admin_pairing_accepts_only_device_pull_transport(self):
        accepted = self.client.post(
            "/api/admin/devices",
            json={"name": "pull-only", "profile_id": "waveshare_photopainter_73", "delivery_mode": "device_pull"},
        )
        self.assertEqual(accepted.status_code, 400)
        self.assertEqual(self.state.devices.list(), [])
        for payload in (
            {"name": "push-mode", "profile_id": "waveshare_photopainter_73", "delivery_mode": "server_push"},
            {"name": "push-config", "profile_id": "waveshare_photopainter_73", "push": {"enabled": False}},
        ):
            rejected = self.client.post("/api/admin/devices", json=payload)
            self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.state.devices.list(), [])

    def test_device_profiles_and_e1002_orientation_contract(self):
        profiles = self.client.get("/api/device-profiles")
        self.assertEqual(profiles.status_code, 200)
        values = {item["profile_id"]: item for item in profiles.json()["profiles"]}
        self.assertEqual(values["waveshare_photopainter_73"]["orientations"], ["landscape", "portrait"])
        self.assertEqual(values["waveshare_photopainter_73"]["panel"], "E6")
        self.assertEqual(values["waveshare_photopainter_73"]["color_count"], 6)
        self.assertEqual(values["waveshare_photopainter_73"]["rotation_degrees"], [])
        self.assertEqual(values["seeedstudio_reterminal_e1002"]["orientations"], ["landscape"])
        self.assertEqual(values["seeedstudio_reterminal_e1002"]["panel"], "E Ink Spectra 6")
        self.assertEqual(values["seeedstudio_reterminal_e1002"]["color_count"], 6)
        accepted = self._create_test_photoframe(
            profile_id="seeedstudio_reterminal_e1002",
            name="e1002",
        )
        self.assertEqual(accepted["profile_id"], "seeedstudio_reterminal_e1002")
        rejected = self.client.post("/api/admin/devices", json={
            "profile_id": "seeedstudio_reterminal_e1002",
            "name": "e1002-portrait",
            "display": {"orientation": "portrait", "width": 480, "height": 800},
        })
        self.assertEqual(rejected.status_code, 400)
        conflict = self.client.post("/api/admin/devices", json={
            "profile_id": "waveshare_photopainter_73",
            "display": {"profile_id": "seeedstudio_reterminal_e1002"},
        })
        self.assertEqual(conflict.status_code, 400)
        self.assertIn("conflicts", conflict.json()["detail"])

    def test_managed_photoframe_registration_requires_explicit_profile(self):
        response = self.client.post("/api/admin/devices", json={"name": "unidentified-frame"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("profile_id is required", response.json().get("detail", ""))
        invalid_display = self.client.post(
            "/api/admin/devices",
            json={"profile_id": "waveshare_photopainter_73", "display": "not-an-object"},
        )
        self.assertEqual(invalid_display.status_code, 400)
        self.assertIn("display must be", invalid_display.json()["detail"])
        valid = self.client.post(
            "/api/admin/devices",
            json={"name": "verified-only", "profile_id": "waveshare_photopainter_73"},
        )
        self.assertEqual(valid.status_code, 400)
        self.assertEqual(valid.json()["detail"]["registration_status"], "not_registered")
        self.assertEqual(self.state.devices.list(), [])

    def test_low_level_photoframe_handshake_requires_profile_and_exact_jpeg(self):
        missing = self.client.post(
            "/api/devices/handshake",
            json={"display": {"kind": "photoframe", "width": 800, "height": 480, "codecs": ["jpeg"]}},
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn("profile_id is required", missing.json()["detail"])
        bad_codec = self.client.post(
            "/api/devices/handshake",
            json={
                "profile_id": "waveshare_photopainter_73",
                "display": {"kind": "photoframe", "width": 800, "height": 480, "codecs": ["e6"]},
            },
        )
        self.assertEqual(bad_codec.status_code, 400)
        self.assertEqual(bad_codec.json()["detail"]["registration_status"], "not_registered")
        valid = self.client.post(
            "/api/devices/handshake",
            json={
                "profile_id": "waveshare_photopainter_73",
                "display": {"kind": "photoframe", "width": 800, "height": 480, "codecs": ["jpeg"]},
            },
        )
        self.assertEqual(valid.status_code, 400)
        self.assertEqual(valid.json()["detail"]["registration_status"], "not_registered")
        self.assertEqual(self.state.devices.list(), [])
        invalid_payload = self.client.post("/api/devices/handshake", json=["not-an-object"])
        self.assertEqual(invalid_payload.status_code, 400)
        invalid_display = self.client.post("/api/devices/handshake", json={"display": "not-an-object"})
        self.assertEqual(invalid_display.status_code, 400)

    def test_unidentified_legacy_photoframe_is_blocked_until_reidentified(self):
        path = Path(self.temp.name) / "devices.json"
        path.write_text(
            '{"schema_version":1,"devices":{"legacy":{"display":{"kind":"photoframe",'
            '"width":1024,"height":600,"codecs":["jpeg"]}}}}',
            encoding="utf-8",
        )
        self.state.devices = DeviceRegistry(path)
        listing = self.client.get("/api/admin/devices").json()["devices"]
        legacy = next(item for item in listing if item["device_id"] == "legacy")
        self.assertTrue(legacy["profile_required"])
        content = self.client.get("/api/devices/legacy/photoframe")
        self.assertEqual(content.status_code, 400)
        self.assertIn("model is not identified", content.json()["detail"])
        # A malformed old record must remain blocked even if an earlier tool
        # accidentally omitted the migration marker.
        self.state.devices._data["devices"]["legacy"].pop("profile_required", None)
        advance = self.client.post("/api/admin/devices/legacy/advance")
        self.assertEqual(advance.status_code, 400)
        status = self.client.get("/api/admin/devices/legacy/state")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["profile_required"])
        heartbeat = self.client.post("/api/devices/legacy/heartbeat")
        self.assertEqual(heartbeat.status_code, 400)
        confirmed = self.client.patch(
            "/api/admin/devices/legacy",
            json={"profile_id": "seeedstudio_reterminal_e1002"},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["display"]["orientation"], "landscape")
        self.assertEqual(
            (confirmed.json()["display"]["width"], confirmed.json()["display"]["height"]),
            (800, 480),
        )

    def test_unknown_profile_cannot_advance_or_report_ready(self):
        created = self._create_test_photoframe(name="corrupted")
        device_id = created["device_id"]
        raw = self.state.devices._data["devices"][device_id]
        raw["profile_id"] = "unknown_profile"
        raw["display"]["profile_id"] = "unknown_profile"
        advance = self.client.post(f"/api/admin/devices/{device_id}/advance")
        self.assertEqual(advance.status_code, 400)
        state = self.client.get(f"/api/admin/devices/{device_id}/state")
        self.assertEqual(state.status_code, 400)
        heartbeat = self.client.post(f"/api/devices/{device_id}/heartbeat")
        self.assertEqual(heartbeat.status_code, 400)

    def test_photoframe_profile_is_reported_and_content_negotiation_is_fixed(self):
        wave_value = self._create_test_photoframe(
            profile_id="waveshare_photopainter_73",
            display={"orientation": "portrait"},
        )
        self.assertEqual(wave_value["profile_id"], "waveshare_photopainter_73")
        self.assertEqual(wave_value["display"]["orientation"], "portrait")
        manifest = self.client.get(f"/api/devices/{wave_value['device_id']}/manifest")
        self.assertIn(manifest.status_code, {200, 404})
        if manifest.status_code == 200:
            self.assertEqual(manifest.json()["device_profile"], "waveshare_photopainter_73")
            self.assertEqual(manifest.json()["orientation"], "portrait")
        wave_content = self.client.get(
            f"/api/devices/{wave_value['device_id']}/photoframe",
            headers={"X-Display-Width": "480", "X-Display-Height": "800", "X-Display-Orientation": "portrait"},
        )
        self.assertIn(wave_content.status_code, {200, 404})

        seeed_value = self._create_test_photoframe(
            profile_id="seeedstudio_reterminal_e1002",
        )
        self.assertEqual(seeed_value["display"]["orientation"], "landscape")
        bad = self.client.get(
            f"/api/devices/{seeed_value['device_id']}/photoframe",
            headers={"X-Display-Width": "480", "X-Display-Height": "800", "X-Display-Orientation": "portrait"},
        )
        self.assertEqual(bad.status_code, 400)
        bad_rotation = self.client.post("/api/admin/devices", json={
            "profile_id": "waveshare_photopainter_73",
            "display": {"rotation": 90},
        })
        self.assertEqual(bad_rotation.status_code, 400)

    def test_local_touchscreen_is_exposed_as_a_separate_admin_device(self):
        remote = self.client.get("/api/devices")
        self.assertEqual(remote.status_code, 200)
        self.assertEqual(remote.json()["devices"], [])

        response = self.client.get("/api/admin/touchscreen")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["device"]["device_id"], "local-touchscreen")
        self.assertEqual(body["device"]["device_type"], "touchscreen")
        self.assertTrue(body["device"]["is_local"])
        self.assertEqual(body["device"]["display"]["kind"], "touchscreen")
        self.assertEqual(body["config"]["display"]["interval_seconds"], 60)
        self.assertTrue(body["config"]["display"]["touchscreen_enabled"])
        self.assertEqual(body["config"]["display"]["touchscreen_interval_seconds"], 60)

        devices = self.client.get("/api/admin/devices").json()["devices"]
        self.assertEqual(devices[0]["device_id"], "local-touchscreen")
        self.assertTrue(devices[0]["is_local"])

    def test_touchscreen_patch_isolated_from_remote_device_configuration(self):
        initial = self.client.get("/api/admin/touchscreen").json()
        response = self.client.patch(
            "/api/admin/touchscreen",
            json={
                "revision": initial["config"]["revision"],
                "name": "QDtech MPI1001",
                "enabled": False,
                "interval_seconds": 45,
                "show_filename": False,
                "repeat_window": 7,
                "orientation_mode": "match_display",
                "rotation": 90,
                "display": {"width": 1920, "height": 1080},
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["device"]["enabled"])
        self.assertEqual(body["device"]["name"], "QDtech MPI1001")
        self.assertEqual(body["config"]["display"]["interval_seconds"], 45)
        self.assertFalse(body["config"]["display"]["show_filename"])
        self.assertEqual(body["config"]["display"]["rotation"], 90)
        self.assertEqual(body["device"]["display"]["width"], 1920)

        config = self.client.get("/api/config").json()
        self.assertFalse(config["display"]["touchscreen_enabled"])
        self.assertEqual(config["display"]["touchscreen_interval_seconds"], 45)
        self.assertEqual(config["device"]["poll_seconds"], 1800)
        self.assertEqual(self.client.get("/api/devices").json()["devices"], [])

    def test_touchscreen_advance_uses_local_display_state_without_epaper_push(self):
        first = self.client.post("/api/admin/touchscreen/advance", json={"action": "next"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["device"]["device_id"], "local-touchscreen")
        self.assertEqual(first.json()["state"]["current"]["photo_id"], 1)
        self.assertEqual(self.refresh_weather_arguments[-1], False)

        paused = self.client.post("/api/admin/touchscreen/advance", json={"action": "pause"})
        self.assertEqual(paused.status_code, 200)
        self.assertFalse(paused.json()["device"]["enabled"])
        self.assertTrue(self.client.get("/api/config").json()["display"]["enabled"])
        resumed = self.client.post("/api/admin/touchscreen/advance", json={"action": "resume"})
        self.assertEqual(resumed.status_code, 200)
        self.assertTrue(resumed.json()["device"]["enabled"])

    def test_admin_device_advance_and_disable_are_isolated(self):
        value = self._create_test_photoframe(name="den")
        advanced = self.client.post(f"/api/admin/devices/{value['device_id']}/advance")
        self.assertEqual(advanced.status_code, 200)
        # Choosing the server-side next image is not a request made by the
        # ESP32. Only the device-facing content routes may update this audit
        # field, otherwise the management page would falsely report a pull.
        state = self.client.get(f"/api/admin/devices/{value['device_id']}/state").json()
        self.assertIsNone(state["last_request"])
        self.assertNotEqual(state["last_status"], "advanced")
        # PATCH is the reversible soft-disable operation.  DELETE is reserved
        # for an explicit, destructive registration removal confirmation.
        disabled = self.client.patch(f"/api/admin/devices/{value['device_id']}", json={"enabled": False})
        self.assertEqual(disabled.status_code, 200)
        blocked = self.client.get(f"/api/devices/{value['device_id']}/photoframe")
        self.assertEqual(blocked.status_code, 404)

    @mock.patch.object(app, "PhotoFrameProvisioner")
    def test_photoframe_provision_pull_writes_url_rotation_without_faking_a_fetch(self, provisioner_class):
        value = self._create_test_photoframe(name="provisioned-frame")
        device_id = value["device_id"]
        provisioner = provisioner_class.return_value
        provisioner.provision.return_value = app.ProvisionResult(
            device_url="http://192.168.1.137",
            device_hardware_id="a4cb8fdaa1dc",
            firmware_version="v2.18.0",
            board_name="Waveshare 7.3 7-Color",
            configured_image_url=f"http://testserver/api/devices/{device_id}/photoframe",
            rotation_cron=("*/30 * *",),
            display_orientation="landscape",
            rotate_requested=True,
            rotate_status="requested",
        )

        response = self.client.post(
            f"/api/admin/devices/{device_id}/provision-pull",
            json={"device_url": "http://192.168.1.137", "trigger_now": True},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["delivery_mode"], "device_pull")
        self.assertEqual(body["pull_provision"]["status"], "awaiting_pull")
        self.assertTrue(body["pull_url"].endswith(f"/api/devices/{device_id}/photoframe"))
        provisioner.provision.assert_called_once()
        kwargs = provisioner.provision.call_args.kwargs
        self.assertTrue(kwargs["image_url"].endswith(f"/api/devices/{device_id}/photoframe"))
        self.assertEqual(kwargs["rotation_cron"], ["*/30 * *"])

        # Configuration success only proves that the ESP32 accepted settings.
        # It must not be displayed as an image fetch before the device itself
        # performs a URL Rotation GET with its native headers.
        state = self.client.get(f"/api/admin/devices/{device_id}/state").json()
        self.assertIsNone(state["last_request"])
        self.assertEqual(state["pull_provision"]["device_hardware_id"], "a4cb8fdaa1dc")

    @mock.patch.object(app, "PhotoFrameProvisioner")
    def test_provision_pull_rejects_unsafe_device_url_before_network_access(self, provisioner_class):
        value = self._create_test_photoframe(name="unsafe-frame")
        response = self.client.post(
            f"/api/admin/devices/{value['device_id']}/provision-pull",
            json={"device_url": "http://127.0.0.1"},
        )
        self.assertEqual(response.status_code, 400)
        provisioner_class.return_value.provision.assert_not_called()

    def test_photoframe_headers_from_another_source_do_not_verify_a_pull(self):
        """Diagnostic JPEG access is allowed, but only the verified ESP32 proves a pull."""

        value = self._create_test_photoframe(name="source-checked-frame")
        device_id = value["device_id"]
        self.state.devices.mark_pull_provision(
            device_id,
            "awaiting_pull",
            device_url="http://192.168.1.137",
            configured_image_url=f"http://192.168.1.135:7860/api/devices/{device_id}/photoframe",
            successful=True,
        )
        headers = {
            "X-Display-Width": "800",
            "X-Display-Height": "480",
            "X-Display-Orientation": "landscape",
            "X-Firmware-Version": "v2.18.0",
        }
        with TestClient(
            _PeerOverrideApp(app.create_app(), ("192.168.1.200", 50000)),
            base_url="http://192.168.1.135:7860",
        ) as wrong_source:
            response = wrong_source.get(f"/api/devices/{device_id}/photoframe", headers=headers)

        # Content remains available for LAN diagnostics, but source-IP
        # mismatch prevents the request from becoming connection evidence.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        state = self.client.get(f"/api/admin/devices/{device_id}/state").json()
        self.assertEqual(state["pull_provision"]["status"], "awaiting_pull")
        self.assertIsNone(state["pull_provision"]["first_pull_at"])
        self.assertIsNone(state["last_request"])
        self.assertIsNone(state["last_request_firmware"])

    def test_fast_valid_pull_does_not_regress_when_provisioning_finishes(self):
        """A completed ESP32 GET wins over a delayed control-plane write."""

        value = self._create_test_photoframe(name="race-frame")
        device_id = value["device_id"]
        pull_url = f"http://192.168.1.135:7860/api/devices/{device_id}/photoframe"
        self.state.devices.mark_pull_provision(
            device_id,
            "configuring",
            device_url="http://192.168.1.137",
            configured_image_url=pull_url,
            rotate_status="not_requested",
        )

        # Model the device handling /api/rotate quickly enough to complete
        # its GET before PhotoFrameProvisioner.provision() returns.
        self.state.devices.mark_request(
            device_id,
            "ok",
            etag='"fixture-etag"',
            client="192.168.1.137",
            firmware_version="v2.18.0",
            display={"width": 800, "height": 480, "orientation": "landscape"},
        )
        pulled_before_finish = self.state.devices.get(device_id)["pull_provision"]
        self.assertEqual(pulled_before_finish["status"], "pulled")

        finished = self.state.devices.mark_pull_provision(
            device_id,
            "awaiting_pull",
            device_url="http://192.168.1.137",
            configured_image_url=pull_url,
            firmware_version="v2.18.0",
            rotate_status="requested",
            successful=True,
        )["pull_provision"]
        self.assertEqual(finished["status"], "pulled")
        self.assertEqual(finished["first_pull_at"], pulled_before_finish["first_pull_at"])
        self.assertEqual(finished["last_pull_at"], pulled_before_finish["last_pull_at"])

    def test_photoframe_fetch_audit_requires_official_url_rotation_headers(self):
        value = self._create_test_photoframe(name="audited-frame")
        device_id = value["device_id"]
        self.state.devices.mark_pull_provision(
            device_id,
            "awaiting_pull",
            device_url="http://192.168.1.137",
            configured_image_url=f"http://192.168.1.135:7860/api/devices/{device_id}/photoframe",
            successful=True,
        )
        url = f"/api/devices/{device_id}/photoframe"

        browser_preview = self.client.get(url)
        self.assertEqual(browser_preview.status_code, 200)
        before = self.client.get(f"/api/admin/devices/{device_id}/state").json()
        self.assertIsNone(before["last_request"])

        device_response = self.photoframe_client.get(
            url,
            headers={
                "X-Display-Width": "800",
                "X-Display-Height": "480",
                "X-Display-Orientation": "landscape",
                "X-Firmware-Version": "v2.18.0",
            },
        )
        self.assertEqual(device_response.status_code, 200)
        payload = json.loads(device_response.headers["x-config-payload"])
        self.assertEqual(payload["config"]["display_orientation"], "landscape")
        self.assertEqual(payload["config"]["display_rotation_deg"], 0)
        self.assertNotIn("orientation_mode", payload["config"])
        after = self.client.get(f"/api/admin/devices/{device_id}/state").json()
        self.assertEqual(after["last_request_firmware"], "v2.18.0")
        self.assertEqual(after["last_request_display"], {"width": 800, "height": 480, "orientation": "landscape"})
        self.assertEqual(after["pull_provision"]["status"], "pulled")
        self.assertIsNotNone(after["pull_provision"]["first_pull_at"])

        unchanged = self.photoframe_client.get(
            url,
            headers={
                "X-Display-Width": "800",
                "X-Display-Height": "480",
                "X-Display-Orientation": "landscape",
                "X-Firmware-Version": "v2.18.0",
                "If-None-Match": device_response.headers["etag"],
            },
        )
        self.assertEqual(unchanged.status_code, 304)
        final_state = self.client.get(f"/api/admin/devices/{device_id}/state").json()
        self.assertEqual(final_state["last_status"], "not_modified")

    def test_admin_device_delete_requires_confirmation_and_removes_record(self):
        value = self._create_test_photoframe(name="remove-me")
        device_id = value["device_id"]

        missing_confirmation = self.client.delete(f"/api/admin/devices/{device_id}")
        self.assertEqual(missing_confirmation.status_code, 400)
        self.assertIn("confirm", missing_confirmation.text.lower())
        self.assertTrue(any(item["device_id"] == device_id for item in self.client.get("/api/admin/devices").json()["devices"]))

        removed = self.client.delete(f"/api/admin/devices/{device_id}?confirm=true")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.json()["device_id"], device_id)
        self.assertTrue(removed.json().get("deleted", True))
        self.assertFalse(any(item["device_id"] == device_id for item in self.client.get("/api/admin/devices").json()["devices"]))

        # All device-facing views disappear with the registration.  Original
        # photo metadata/file handling remains independent and available.
        self.assertEqual(self.client.get(f"/api/devices/{device_id}/manifest").status_code, 404)
        self.assertEqual(self.client.get(f"/api/devices/{device_id}/photoframe").status_code, 404)
        self.assertEqual(self.client.get(f"/api/admin/devices/{device_id}/state").status_code, 404)
        self.assertEqual(self.client.get("/api/photos/1/file").status_code, 200)

    def test_admin_device_delete_cannot_remove_local_touchscreen(self):
        response = self.client.delete("/api/admin/devices/local-touchscreen?confirm=true")
        self.assertIn(response.status_code, {400, 403, 404})
        local = self.client.get("/api/admin/touchscreen")
        self.assertEqual(local.status_code, 200)
        self.assertEqual(local.json()["device"]["device_id"], "local-touchscreen")

    def test_active_push_endpoint_uses_explicit_device_url(self):
        value = self._create_test_photoframe(name="push-frame")
        updated = self.client.patch(
            f"/api/admin/devices/{value['device_id']}",
            json={"push": {"enabled": True, "base_url": "http://192.168.1.76", "protocol": "photoframe_api"}},
        )
        self.assertEqual(updated.status_code, 200)
        calls = []

        def push_device(device_id, force=False, scheduled_slot=None, force_send=False):
            calls.append((device_id, force, scheduled_slot, force_send))
            return {"device_id": device_id, "status": "ok", "photo_id": 1, "attempts": 1}

        self.state.push_device = push_device
        response = self.client.post(f"/api/admin/devices/{value['device_id']}/push", json={"force": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [(value["device_id"], True, None, False)])
        state = self.client.get(f"/api/admin/devices/{value['device_id']}/state").json()
        self.assertEqual(state["push"]["base_url"], "http://192.168.1.76")
        response = self.client.post(
            f"/api/admin/devices/{value['device_id']}/push",
            json={"force": False, "force_send": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[-1], (value["device_id"], False, None, True))

    def test_active_push_enable_rejects_implicit_protocol(self):
        value = self._create_test_photoframe(name="implicit-protocol")
        response = self.client.patch(
            f"/api/admin/devices/{value['device_id']}",
            json={"push": {"enabled": True, "base_url": "http://192.168.1.76"}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("push.protocol", response.text)

    def test_active_push_accepts_case7_protocol_only_when_named(self):
        value = self._create_test_photoframe(name="case7-frame")
        response = self.client.patch(
            f"/api/admin/devices/{value['device_id']}",
            json={"push": {"enabled": True, "base_url": "http://192.168.1.78", "protocol": "case7_push"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["push"]["protocol"], "case7_push")

    def test_playlist_endpoint_selects_immediately_and_exposes_safe_state(self):
        value = self._create_test_photoframe(name="e1002", profile_id="seeedstudio_reterminal_e1002")
        def choose_playlist(device_id, policy, force=False):
            self.state.index.save_display_state(device_id, 1, "2026-08-23-12-05:*/5 * *", policy["policy_revision"], 8)
            return dict(self.row, selection_revision=8, weather="晴天", slot_key="2026-08-23-12-05:*/5 * *")
        self.state.selector.current_for_device = choose_playlist
        response = self.client.post(
            f"/api/admin/devices/{value['device_id']}/playlist",
            json={"photo_ids": [1], "rotation_cron": ["*/5 * *"], "start_immediately": True},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["playlist_photo_ids"], [1])
        self.assertEqual(body["current"]["id"], 1)
        state = self.client.get(f"/api/admin/devices/{value['device_id']}/state")
        self.assertEqual(state.status_code, 200)
        state_body = state.json()
        self.assertEqual(state_body["selection_mode"], "playlist")
        self.assertEqual(state_body["current"]["photo_id"], 1)
        self.assertNotIn("filepath", state.text)
        self.assertNotIn("token_hash", state.text)

    def test_playlist_rejects_missing_photo_without_changing_policy(self):
        value = self._create_test_photoframe(name="e1002", profile_id="seeedstudio_reterminal_e1002")
        response = self.client.post(
            f"/api/admin/devices/{value['device_id']}/playlist",
            json={"photo_ids": [404], "start_immediately": True},
        )
        self.assertEqual(response.status_code, 400)
        device = self.client.get(f"/api/admin/devices/{value['device_id']}/state").json()
        self.assertEqual(device["selection_mode"], "smart")

    def test_playlist_rejects_non_integer_ids_without_coercion(self):
        value = self._create_test_photoframe(name="typed-playlist")
        for photo_ids in (["1"], [1.5], [True]):
            response = self.client.post(
                f"/api/admin/devices/{value['device_id']}/playlist",
                json={"photo_ids": photo_ids},
            )
            self.assertEqual(response.status_code, 400)

    def test_photoframe_policy_change_invalidates_etag(self):
        # This test verifies that an allowed policy change still invalidates
        # the full-size E1002 rendering variant.  The fixture is created
        # directly because network registration is tested separately.
        value = self._create_test_photoframe(
            name="etag-frame",
            profile_id="seeedstudio_reterminal_e1002",
        )
        self.state.selector.current_for_device = lambda _device_id, _policy, force=False: dict(self.row, selection_revision=7, weather="晴天")
        # Managed PhotoFrame registration is URL-only on the trusted LAN;
        # content requests do not need a compatibility device token.
        headers = {"X-Display-Width": "800", "X-Display-Height": "480", "X-Display-Orientation": "landscape"}
        first = self.client.get(f"/api/devices/{value['device_id']}/photoframe", headers=headers)
        self.assertEqual(first.status_code, 200)
        updated = self.client.patch(f"/api/admin/devices/{value['device_id']}", json={"policy": {"overlay_date": False}})
        self.assertEqual(updated.status_code, 200)
        second = self.client.get(f"/api/devices/{value['device_id']}/photoframe", headers={**headers, "If-None-Match": first.headers["etag"]})
        self.assertEqual(second.status_code, 200)

    def test_admin_pairing_preserves_portrait_capability(self):
        value = self._create_test_photoframe(
            name="portrait-frame",
            display={"width": 480, "height": 800, "orientation": "portrait"},
        )
        self.assertEqual(
            (value["display"]["width"], value["display"]["height"]),
            (480, 800),
        )

    def test_e6_etag_tracks_epaper_not_touchscreen_orientation(self):
        value = self.client.post(
            "/api/devices/handshake",
            json={"name": "e6", "display": {"kind": "epaper", "width": 800, "height": 480, "codecs": ["e6"]}},
        )
        self.assertEqual(value.status_code, 200)
        device_id = value.json()["device_id"]
        url = f"/api/devices/{device_id}/content"
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.content), 192000)
        # Touchscreen direction must not change an E6 representation.
        config = self.client.get("/api/config").json()
        changed_touch = self.client.patch(
            "/api/config",
            json={"revision": config["revision"], "display": {"rotation": 90}},
        )
        self.assertEqual(changed_touch.status_code, 200)
        unchanged = self.client.get(url, headers={"If-None-Match": first.headers["etag"]})
        self.assertEqual(unchanged.status_code, 304)
        config = self.client.get("/api/config").json()
        changed_epaper = self.client.patch(
            "/api/config",
            json={"revision": config["revision"], "epaper": {"rotation": 90}},
        )
        self.assertEqual(changed_epaper.status_code, 200)
        rotated = self.client.get(url, headers={"If-None-Match": first.headers["etag"]})
        self.assertEqual(rotated.status_code, 200)
        self.assertNotEqual(rotated.headers["etag"], first.headers["etag"])

    def test_e6_weather_refresh_does_not_invalidate_unchanged_frame(self):
        value = self.client.post(
            "/api/devices/handshake",
            json={
                "name": "weather-stable-e6",
                "display": {"kind": "epaper", "width": 800, "height": 480, "codecs": ["e6"]},
            },
        )
        self.assertEqual(value.status_code, 200)
        url = f"/api/devices/{value.json()['device_id']}/content"
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        # A weather refresh advances the selector's logical revision for the
        # touchscreen, but E6 bytes have no weather overlay.
        selector = self.state.selector
        selector.revision += 1
        unchanged = self.client.get(url, headers={"If-None-Match": first.headers["etag"]})
        self.assertEqual(unchanged.status_code, 304)

    def test_display_select_and_controls_are_public(self):
        selected = self.client.post("/api/display/select", json={"photo_id": 1})
        self.assertEqual(selected.status_code, 200)
        current = self.client.get("/api/display/current").json()
        self.assertEqual(current["current"]["id"], 1)
        paused = self.client.post("/api/display/control", json={"action": "pause"})
        self.assertEqual(paused.status_code, 200)
        self.assertFalse(paused.json()["display"]["enabled"])
        resumed = self.client.post("/api/display/control", json={"action": "resume"})
        self.assertEqual(resumed.status_code, 200)
        self.assertTrue(resumed.json()["display"]["enabled"])
        next_photo = self.client.post("/api/display/control", json={"action": "next"})
        self.assertEqual(next_photo.status_code, 200)
        self.assertEqual(self.refresh_weather_arguments[-1], False)

    def test_display_previous_uses_persisted_touchscreen_history(self):
        second = dict(self.row, id=2, filename="older.jpg")
        self.state.index.get_photo = lambda photo_id: self.row if int(photo_id) == 1 else second if int(photo_id) == 2 else None
        self.state.index.history = [2]
        selected = self.client.post("/api/display/select", json={"photo_id": 1})
        self.assertEqual(selected.status_code, 200)
        previous = self.client.post("/api/display/control", json={"action": "previous"})
        self.assertEqual(previous.status_code, 200)
        self.assertEqual(previous.json()["current"]["id"], 2)
        self.assertEqual(self.state.index.get_display_state("local")["photo_id"], 2)
        self.assertEqual(self.state.index.history, [2])


if __name__ == "__main__":
    unittest.main()
