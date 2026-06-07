import json
import queue
import threading
import time

import cv2

from .metadata import available_video_devices, get_source
from .visualization import draw_detections


def infer_image(source, detector, labels, save=None, no_window=False, window_title="YOLO"):
    image = cv2.imread(str(get_source(source)))
    if image is None:
        raise RuntimeError(f"Cannot read image: {source}")

    detections, latency_ms = detector.infer_frame(image)
    draw_detections(image, detections, labels)
    print(json.dumps(detections, indent=2))
    print(f"[{window_title}] inference latency: {latency_ms:.2f} ms")

    if save:
        cv2.imwrite(str(save), image)
    elif not no_window:
        cv2.imshow(window_title, image)
        cv2.waitKey(0)


def infer_camera(
    source,
    detector,
    labels,
    camera_width=640,
    camera_height=480,
    camera_fps=30,
    infer_every_n=1,
    sync_infer=False,
    max_frames=0,
    no_window=False,
    window_title="YOLO",
    latency_label="Infer",
):
    cap = cv2.VideoCapture(get_source(source))
    if not cap.isOpened():
        devices = available_video_devices()
        device_text = ", ".join(devices) if devices else "none"
        raise RuntimeError(f"Cannot open video source: {source}. Available video devices: {device_text}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, camera_width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, camera_height)
    cap.set(cv2.CAP_PROP_FPS, camera_fps)

    prev_t = time.time()
    frame_id = 0
    last_detections = []
    last_latency_ms = 0.0
    infer_every_n = max(1, infer_every_n)
    processed_frames = 0
    infer_count = 0
    total_latency_ms = 0.0
    start_t = time.time()
    result_lock = threading.Lock()
    frame_queue = queue.Queue(maxsize=1)
    stop_event = threading.Event()
    worker = None

    if not sync_infer:
        shared = {"detections": [], "infer_count": 0, "latency_ms": 0.0, "total_latency_ms": 0.0, "error": None}

        def infer_worker():
            while not stop_event.is_set():
                try:
                    worker_frame = frame_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    detections, latency_ms = detector.infer_frame(worker_frame)
                    with result_lock:
                        shared["detections"] = detections
                        shared["infer_count"] += 1
                        shared["latency_ms"] = latency_ms
                        shared["total_latency_ms"] += latency_ms
                except Exception as exc:
                    with result_lock:
                        shared["error"] = exc
                    stop_event.set()
                finally:
                    frame_queue.task_done()

        worker = threading.Thread(target=infer_worker, daemon=True)
        worker.start()

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if sync_infer and frame_id % infer_every_n == 0:
                last_detections, last_latency_ms = detector.infer_frame(frame)
                infer_count += 1
                total_latency_ms += last_latency_ms
            elif not sync_infer:
                with result_lock:
                    worker_error = shared["error"]
                if worker_error is not None:
                    raise RuntimeError(f"Background inference failed: {worker_error}") from worker_error

                if frame_id % infer_every_n == 0 and frame_queue.empty():
                    try:
                        frame_queue.put_nowait(frame.copy())
                    except queue.Full:
                        pass
                with result_lock:
                    last_detections = list(shared["detections"])
                    infer_count = shared["infer_count"]
                    last_latency_ms = shared["latency_ms"]
                    total_latency_ms = shared["total_latency_ms"]

            if not no_window:
                draw_detections(frame, last_detections, labels)

            now = time.time()
            fps = 1.0 / max(now - prev_t, 1e-6)
            prev_t = now
            if not no_window:
                cv2.putText(
                    frame,
                    f"FPS: {fps:.1f}, Infer: 1/{infer_every_n}, {latency_label}: {last_latency_ms:.1f} ms",
                    (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (0, 255, 0),
                    2,
                )
            frame_id += 1
            processed_frames += 1

            if no_window:
                if max_frames and processed_frames >= max_frames:
                    break
                continue

            cv2.imshow(window_title, frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            if max_frames and processed_frames >= max_frames:
                break
    finally:
        cap.release()
        stop_event.set()
        if worker:
            worker.join(timeout=1.0)
        if not no_window:
            cv2.destroyAllWindows()

    elapsed = time.time() - start_t
    if elapsed > 0 and (no_window or max_frames):
        avg_latency_ms = total_latency_ms / infer_count if infer_count else 0.0
        print(
            f"Processed {processed_frames} frames, {infer_count} inferences, "
            f"camera FPS {processed_frames / elapsed:.2f}, "
            f"inference FPS {infer_count / elapsed:.2f}, "
            f"avg {latency_label} latency {avg_latency_ms:.2f} ms"
        )

