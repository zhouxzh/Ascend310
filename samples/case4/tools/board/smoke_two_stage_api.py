"""Exercise the manual-test HTTP workflow with one pinned board-side image."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import cv2
import numpy as np
from fastapi.testclient import TestClient

from palmprint_workbench.api.server import app
from palmprint_workbench.config import REPORT_DIR, ROI_PIPELINE_ID


def _summary(response) -> dict:
    body = response.json()
    result = {"http_status": response.status_code}
    if response.status_code >= 400:
        result["detail"] = body.get("detail")
    else:
        result["status"] = body.get("status")
        result["detection_count"] = body.get("detection_count")
        result["hands"] = [
            {"index": hand.get("index"), "outcome": hand.get("outcome"),
             "roi_pipeline_id": hand.get("roi_pipeline_id"),
             "match_count": len(hand.get("matches", []))}
            for hand in body.get("hands", [])
        ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--camera", default="/dev/video0")
    parser.add_argument("--resolution", default="640x480")
    args = parser.parse_args()
    report_path = args.report.resolve()
    report_path.relative_to(REPORT_DIR.resolve())
    image_bytes = args.image.read_bytes()
    bgr = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("test image could not be decoded")
    report: dict = {
        "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "image_shape": list(bgr.shape),
        "roi_pipeline_id": ROI_PIPELINE_ID,
        "registration_note": "Five copies of one ROI exercise HTTP/storage/ACL only; they do not prove enrollment quality.",
    }
    session_id = None
    template_id = None
    try:
        with TestClient(app) as client:
            for path, key in (("/api/health", "health"), ("/api/bootstrap", "bootstrap"),
                              ("/", "static"), ("/api/cameras", "cameras")):
                response = client.get(path)
                report[key] = {"http_status": response.status_code}
                if key == "health" and response.status_code == 200:
                    payload = response.json()
                    report[key].update({name: payload.get(name) for name in
                                        ("release_profile", "runtime_importable", "model_ready", "template_store_ready")})
                if key == "bootstrap" and response.status_code == 200:
                    report[key]["model_ids"] = [item["id"] for item in response.json().get("models", [])]
                if key == "cameras" and response.status_code == 200:
                    report[key]["devices"] = [
                        {name: item.get(name) for name in ("device", "physical_id", "admitted", "spectrum")}
                        for item in response.json().get("items", [])
                    ]

            response = client.post("/api/recognitions", data={"model_id": "ccnet"},
                                   files={"image": (args.image.name, image_bytes, "image/jpeg")})
            report["real_upload"] = _summary(response)
            if response.status_code != 200 or response.json().get("detection_count") != 1:
                raise RuntimeError("real image upload did not return one palm")
            hand = response.json()["hands"][0]
            if hand.get("outcome") == "roi_rejected":
                raise RuntimeError("real image ROI failed quality check")
            xyxy = hand["bbox_xyxy"]
            left, top = max(0, int(np.floor(xyxy[0]))), max(0, int(np.floor(xyxy[1])))
            right, bottom = min(bgr.shape[1], int(np.ceil(xyxy[2]))), min(bgr.shape[0], int(np.ceil(xyxy[3])))
            roi = bgr[top:bottom, left:right]
            ok, encoded_roi = cv2.imencode(".png", roi)
            if not ok:
                raise RuntimeError("could not encode detected ROI")
            roi_bytes = encoded_roi.tobytes()

            response = client.post("/api/recognitions", data={"assume_roi": "true"},
                                   files={"image": ("roi.png", roi_bytes, "image/png")})
            report["roi_upload"] = _summary(response)

            blank = np.full_like(bgr, 127)
            ok, encoded_blank = cv2.imencode(".png", blank)
            if not ok:
                raise RuntimeError("could not encode blank frame")
            response = client.post("/api/recognitions", files={
                "image": ("blank.png", encoded_blank.tobytes(), "image/png")})
            report["blank_upload"] = _summary(response)

            context = 50
            patch = bgr[max(0, top - context):min(bgr.shape[0], bottom + context),
                        max(0, left - context):min(bgr.shape[1], right + context)]
            patch = cv2.resize(patch, (180, 180), interpolation=cv2.INTER_LINEAR)
            composite = np.full((480, 640, 3), 127, dtype=np.uint8)
            composite[150:330, 40:220] = patch
            composite[150:330, 400:580] = patch
            ok, encoded_composite = cv2.imencode(".png", composite)
            if not ok:
                raise RuntimeError("could not encode two-palm composite")
            response = client.post("/api/recognitions", files={
                "image": ("two-palms.png", encoded_composite.tobytes(), "image/png")})
            report["composite_upload"] = _summary(response)

            options = {"model_id": "ccnet", "backend": "npu", "precision": "mixed_fp16",
                       "palm_side": "left", "display_name": "manual-test-http-smoke", "mode": "assisted"}
            response = client.post("/api/enrollment-sessions", json=options)
            report["enrollment_create"] = {"http_status": response.status_code}
            if response.status_code != 200:
                raise RuntimeError(f"enrollment create failed: {response.text}")
            session_id = response.json()["id"]
            response = client.post(f"/api/enrollment-sessions/{session_id}/samples",
                                   data={"assume_roi": "false"},
                                   files={"image": ("two-palms.png", encoded_composite.tobytes(), "image/png")})
            report["multi_palm_enrollment_rejection"] = {
                "http_status": response.status_code,
                "detail": response.json().get("detail"),
            }
            if response.status_code != 422 or "多只手" not in str(response.json().get("detail")):
                raise RuntimeError("multi-palm registration was not rejected")
            samples = []
            for _ in range(5):
                response = client.post(f"/api/enrollment-sessions/{session_id}/samples",
                                       data={"assume_roi": "true"},
                                       files={"image": ("roi.png", roi_bytes, "image/png")})
                samples.append(response.status_code)
                if response.status_code != 200:
                    raise RuntimeError(f"enrollment sample failed: {response.text}")
            report["enrollment_samples"] = samples
            response = client.post(f"/api/enrollment-sessions/{session_id}/commit", json={})
            report["enrollment_commit"] = {"http_status": response.status_code,
                                            "phase": response.json().get("phase")}
            if response.status_code != 200:
                raise RuntimeError(f"enrollment commit failed: {response.text}")
            template_id = response.json()["palm_id"]
            response = client.post("/api/recognitions", data={"assume_roi": "true"},
                                   files={"image": ("roi.png", roi_bytes, "image/png")})
            report["recognition_after_enrollment"] = _summary(response)
            if response.status_code != 200 or not response.json()["hands"][0].get("matches"):
                raise RuntimeError("enrolled ROI did not produce a candidate")

            camera_path = quote(args.camera, safe="")
            response = client.post(f"/api/cameras/{camera_path}/recognitions",
                                   json={"model_id": "ccnet", "backend": "npu",
                                         "precision": "mixed_fp16", "resolution": args.resolution})
            report["camera_recognition"] = _summary(response)
            response = client.post(f"/api/cameras/{camera_path}/close")
            report["camera_close"] = {"http_status": response.status_code}
            report["ok"] = report["camera_recognition"]["http_status"] == 200 and response.status_code == 200
    except BaseException as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if template_id:
            # The test identity is isolated by the fresh release and removed
            # after probing the template search path.
            try:
                with TestClient(app) as client:
                    response = client.delete(f"/api/templates/{template_id}")
                    report["template_cleanup"] = {"http_status": response.status_code}
            except BaseException as exc:
                report["template_cleanup"] = {"error": f"{type(exc).__name__}: {exc}"}
        elif session_id:
            try:
                with TestClient(app) as client:
                    response = client.delete(f"/api/enrollment-sessions/{session_id}")
                    report["session_cleanup"] = {"http_status": response.status_code}
            except BaseException as exc:
                report["session_cleanup"] = {"error": f"{type(exc).__name__}: {exc}"}
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if report.get("template_cleanup", {}).get("http_status", 200) != 200:
            report["ok"] = False
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
