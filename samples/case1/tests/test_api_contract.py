"""HTTP contract tests using a fake runtime (no ACL, camera, or OM files)."""

import base64
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

CASE_ROOT = Path(__file__).resolve().parents[1]
if str(CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(CASE_ROOT))

from fastapi.testclient import TestClient

from face_attendance import database
from face_attendance.api import create_app


class FakeRuntime:
    def __init__(self, ready=True, camera_ready=True):
        self.ready = ready
        self.camera_ready = camera_ready
        self.readiness_error = None if ready else "模型缺失"
        self.started = 0
        self.stopped = 0
        self.registered = []
        self.clockins = []

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def capture_snapshot(self):
        if not self.camera_ready:
            from face_attendance.runtime import CameraUnavailableError

            raise CameraUnavailableError("摄像头不可用")
        return b"jpeg-bytes"

    def register_user(self, name, image):
        self.registered.append((name, image))
        return 17

    def clockin(self, image, image_path):
        self.clockins.append((image, image_path))
        return {"success": True, "match": True, "user": "张三", "similarity": 0.91}

    def iter_mjpeg(self):
        return iter([b"--frame\r\nContent-Type: image/jpeg\r\n\r\njpeg\r\n"])


def _make_app(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(database, "init_db", lambda: None)
    decoder = lambda payload: {"decoded": payload}
    frontend = tmp_path / "frontend"
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>case1</div></body></html>",
        encoding="utf-8",
    )
    return create_app(
        runtime=runtime,
        image_decoder=decoder,
        upload_dir=tmp_path / "uploads",
        frontend_dist=frontend,
    )


def test_lifespan_pages_health_and_static_safety(tmp_path, monkeypatch):
    runtime = FakeRuntime()
    app = _make_app(runtime, tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert runtime.started == 1
        assert client.get("/").status_code == 200
        assert client.get("/users_page").status_code == 200
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ready"] is True
        assert client.get("/uploads/../requirements.txt").status_code == 404
        assert client.get("/docs").status_code == 404
        assert client.get("/api/unknown").status_code == 404
    assert runtime.stopped == 1


def test_users_and_clockin_request_contract(tmp_path, monkeypatch):
    runtime = FakeRuntime()
    app = _make_app(runtime, tmp_path, monkeypatch)
    rows = [{"id": 1, "name": "张三", "embedding": b"private", "avatar": "a.jpg"}]
    monkeypatch.setattr(database, "get_users", lambda: rows)
    monkeypatch.setattr(database, "delete_user", lambda user_id: None)
    monkeypatch.setattr(database, "update_user_name", lambda user_id, name: None)
    monkeypatch.setattr(database, "get_attendance", lambda: [{"id": 2, "name": "张三"}])

    with TestClient(app) as client:
        users = client.get("/api/users")
        assert users.status_code == 200
        assert "embedding" not in users.json()[0]

        response = client.post(
            "/api/users",
            data={"name": "张三"},
            files={"image": ("face.jpg", b"image", "image/jpeg")},
        )
        assert response.json() == {"success": True, "user_id": 17}
        assert runtime.registered[0][0] == "张三"

        encoded = base64.b64encode(b"image").decode("ascii")
        response = client.post(
            "/api/clockin",
            data={"image_base64": "data:image/jpeg;base64," + encoded},
        )
        assert response.status_code == 200
        assert response.json()["match"] is True
        assert client.get("/api/attendance").json() == [{"id": 2, "name": "张三"}]
        assert client.put("/api/users/1", json={}).status_code == 400
        assert client.put("/api/users/1", json={"name": "李四"}).status_code == 200
        assert client.delete("/api/users/1").status_code == 200


def test_capture_and_mjpeg_camera_unavailable(tmp_path, monkeypatch):
    runtime = FakeRuntime(camera_ready=True)
    app = _make_app(runtime, tmp_path, monkeypatch)
    with TestClient(app) as client:
        capture = client.post("/api/camera/capture")
        assert capture.status_code == 200
        assert (tmp_path / "uploads" / capture.json()["temp_path"]).read_bytes() == b"jpeg-bytes"
        stream = client.get("/video_feed")
        assert stream.status_code == 200
        assert "multipart/x-mixed-replace" in stream.headers["content-type"]

    unavailable = FakeRuntime(camera_ready=False)
    app = _make_app(unavailable, tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post("/api/camera/capture").status_code == 503
        assert client.get("/video_feed").status_code == 503

    no_frame = FakeRuntime(camera_ready=True)
    no_frame.capture_snapshot = lambda: None
    app = _make_app(no_frame, tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post("/api/camera/capture").status_code == 503


def test_model_not_ready_returns_503(tmp_path, monkeypatch):
    runtime = FakeRuntime(ready=False, camera_ready=False)
    app = _make_app(runtime, tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/users",
            data={"name": "张三"},
            files={"image": ("face.jpg", b"image", "image/jpeg")},
        )
        assert response.status_code == 503
        assert response.json()["error"] == "模型缺失"


def test_runtime_error_does_not_expose_model_path(tmp_path, monkeypatch):
    runtime = FakeRuntime(ready=False, camera_ready=False)
    runtime.readiness_error = "缺少模型: /opt/case1/models/face_detection.om"
    app = _make_app(runtime, tmp_path, monkeypatch)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["error"] == "运行时暂不可用"
        response = client.post(
            "/api/users",
            data={"name": "张三"},
            files={"image": ("face.jpg", b"image", "image/jpeg")},
        )
        assert response.status_code == 503
        assert response.json()["error"] == "人脸系统暂不可用"


def test_database_failure_is_not_reported_as_empty_users(tmp_path, monkeypatch):
    runtime = FakeRuntime()
    app = _make_app(runtime, tmp_path, monkeypatch)
    monkeypatch.setattr(database, "get_users", lambda: (_ for _ in ()).throw(RuntimeError("locked")))
    with TestClient(app) as client:
        response = client.get("/api/users")
        assert response.status_code == 503
        assert response.json() == {"error": "用户数据暂不可用"}


def test_upload_type_size_and_name_boundaries(tmp_path, monkeypatch):
    runtime = FakeRuntime()
    app = _make_app(runtime, tmp_path, monkeypatch)
    with TestClient(app) as client:
        invalid_type = client.post(
            "/api/users",
            data={"name": "张三"},
            files={"image": ("face.txt", b"not-image", "text/plain")},
        )
        assert invalid_type.status_code == 400

        oversized = client.post(
            "/api/users",
            data={"name": "张三"},
            files={"image": ("face.jpg", b"x" * (8 * 1024 * 1024 + 1), "image/jpeg")},
        )
        assert oversized.status_code == 400

        assert client.post(
            "/api/users",
            data={"name": "   "},
            files={"image": ("face.jpg", b"image", "image/jpeg")},
        ).status_code == 400
