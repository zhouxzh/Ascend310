# HaGRID YOLO Ascend 310B Sample

This sample shows how to run HaGRID YOLO gesture models on Ascend 310B. The
Ascend-side code focuses on ONNX validation, ATC conversion, OM inference, and
camera testing.

The PyTorch `.pt` to ONNX export helper is kept under `weights/` because it is
part of the model-preparation workflow. It is expected to move with the weights,
for example to a separate Hugging Face model repository.

## Files

- `hagrid_yolo/`: reusable Python package for preprocessing, postprocessing, camera loops, and inference backends.
- `hagrid_yolo/backends/onnx_backend.py`: ONNX Runtime backend used for CPU validation.
- `hagrid_yolo/backends/acl_backend.py`: Ascend ACL backend used for OM inference.
- `scripts/infer_onnx_camera.py`: thin CLI entry for ONNX camera/image testing.
- `scripts/infer_om_camera.py`: thin CLI entry for OM camera/image testing on Ascend 310B.
- `scripts/webrtc_om_app.py`: WebRTC H.264 sender for remote browser viewing of OM camera inference.
- `webrtc_app/cann_encoder.py`: Ascend CANN VENC H.264 encoder adapter for aiortc.
- `webrtc_app/dvpp_jpegd.py`, `webrtc_app/v4l2_raw.py`, `webrtc_app/v4l2_capture.py`: optional DVPP JPEGD camera path.
- `scripts/gradio_om_app.py`: compatibility entry that launches `scripts/webrtc_om_app.py`.
- `scripts/atc_convert.sh`: template command for ONNX to OM conversion on a CANN/ATC machine.
- `web/`: browser UI used by the WebRTC app.
- `requirements.txt`: minimal runtime helpers for ONNX camera testing, WebRTC streaming, and Ascend-side preprocessing/postprocessing. CANN/ACL must be installed separately.
- `weights/export_yolo_to_onnx.py`: GPU/PC-side helper for exporting YOLO `.pt` weights to ONNX and metadata.
- `weights/`: copied YOLO weights. This folder is intended to be separated from the Ascend sample later.
- `models/`: output folder for ONNX, generated labels, and metadata.

## Export ONNX

Run this part on a PC or GPU workstation, not on the Ascend 310B target. The
export helper lives under `weights/` so it can move with the model files later.
Install the export dependencies in that separate export environment:

```bash
pip install numpy==1.26.4 onnx==1.14.1 onnxruntime==1.15.1 opencv-python==4.8.0.76
pip install torch==2.10.0 torchvision==0.25.0 --extra-index-url https://download.pytorch.org/whl/cu128
pip install ultralytics==8.4.60
```

Start with the smaller model first:

```bash
python weights/export_yolo_to_onnx.py \
  --weights weights/YOLOv10n_gestures.pt \
  --output-dir models \
  --imgsz 640 \
  --batch 1 \
  --opset 13 \
  --device cpu
```

Export the larger gesture model:

```bash
python weights/export_yolo_to_onnx.py \
  --weights weights/YOLOv10x_gestures.pt \
  --output-dir models \
  --imgsz 640 \
  --batch 1 \
  --opset 13 \
  --device cpu
```

If ATC rejects an operator with opset 13, retry export with `--opset 17`.

## Model Input And CPU Performance

All current exported ONNX models in `models/` use the same static input shape:

```text
images:1,3,640,640
```

This applies to both small and large models:

| Model | Input name | Input shape | Classes |
| :--- | :--- | :--- | ---: |
| `YOLOv10n_gestures.onnx` | `images` | `1,3,640,640` | 34 |
| `YOLOv10n_hands.onnx` | `images` | `1,3,640,640` | 34 |
| `YOLOv10x_gestures.onnx` | `images` | `1,3,640,640` | 34 |
| `YOLOv10x_hands.onnx` | `images` | `1,3,640,640` | 48 |

So `YOLOv10n` and `YOLOv10x` have the same input resolution. Their speed
difference comes from model size and compute cost, not from different input
dimensions. On Ascend 310B with ONNX Runtime CPU, `YOLOv10n_gestures.onnx`
reaches only a few inferences per second, while `YOLOv10x` is much slower.
For CPU camera testing, this package therefore defaults to
`models/YOLOv10n_gestures.onnx`.

The camera script uses letterbox preprocessing: each camera frame is resized and
padded to the model input size, then converted to NCHW float32 in RGB order.
Changing camera capture size may reduce camera I/O slightly, but it does not
change the ONNX model input size; the model still runs at `640x640`.

You can verify the real ONNX input shape from the generated metadata:

```bash
python - <<'PY'
import json
from pathlib import Path

for path in sorted(Path("models").glob("*_metadata.json")):
    metadata = json.loads(path.read_text())
    print(path.name, metadata["input_shapes"])
PY
```

## Convert ONNX To OM

Run this on the machine where CANN/ATC is installed. In this repository that
usually means the Ascend 310B board, not the local documentation workspace.

First load the CANN environment:

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd samples/case8
```

Then convert all ONNX files under `models/`:

```bash
SOC_VERSION=Ascend310B4 bash scripts/atc_convert.sh
```

If your 310B board uses a different SoC version, replace `Ascend310B4` with the value required by your CANN installation.

The export script writes `models/<model>_metadata.json`.
`scripts/atc_convert.sh` reads that file and uses the actual input name and shape, for example
`images:1,3,640,640`. If the metadata file is missing, the script falls back to
`INPUT_NAME=images` and `IMG_SIZE=640`.

To convert just one ONNX file, pass the ONNX path and optional output prefix:

```bash
SOC_VERSION=Ascend310B4 \
  bash scripts/atc_convert.sh models/YOLOv10n_gestures.onnx models/YOLOv10n_gestures
```

### ATC Output Notes

If ATC prints the following line, conversion has succeeded and the `.om` file
should have been generated:

```text
ATC run success, welcome to the next use.
```

You may also see warnings like:

```text
Operator_Missing_High-Priority_Performance(W11001): Op [/model.23/Div_1] does not hit the high-priority operator information library, which might result in compromised performance.
Operator_Missing_High-Priority_Performance(W11001): Op [/model.23/Mod] does not hit the high-priority operator information library, which might result in compromised performance.
```

These are performance warnings, not conversion failures. They mean those
operators did not match CANN's high-priority optimized operator information
library, so ATC may use a less optimized implementation for them. The generated
OM model is still usable; test inference speed and accuracy before deciding
whether further model simplification or re-export is needed.

### Details Of `/model.23/Div_1` And `/model.23/Mod`

`model.23` is the exported YOLOv10 detection head/postprocessing part of the
ONNX graph. It is not a separate source file. In this model, the detection head
selects the best predictions with `TopK`, then decodes the returned flattened
class-score index back into a candidate box index and a class id.

For the current gesture model, the number of classes is `34`. A flattened index
from `TopK` can be interpreted as:

```text
flat_index = box_index * 34 + class_id
```

So the graph needs the following two operations:

```text
box_index = flat_index // 34
class_id  = flat_index % 34
```

The two ATC warning nodes correspond to those operations:

| ONNX node | Operator | Main input | Constant | Meaning |
| :--- | :--- | :--- | ---: | :--- |
| `/model.23/Div_1` | `Div` | `/model.23/TopK_1_output_1` | `34` | Computes the candidate box index from the flattened TopK index. The result is cast and used by `Gather` to select the final boxes. |
| `/model.23/Mod` | `Mod` | `/model.23/TopK_1_output_1` | `34` | Computes the class id from the flattened TopK index. The result is concatenated into the final output. |

Example:

```text
flat_index = 1425
box_index  = 1425 // 34 = 41
class_id   = 1425 % 34  = 31
```

The final exported output is still the expected YOLO-style tensor:

```text
[x1, y1, x2, y2, score, class_id]
```

These two operators are small index-decoding operations near the output of the
model. They are not convolution/backbone layers. If ATC only reports these
warnings and still prints `ATC run success`, the first step should be to
benchmark the generated OM model before changing the model graph.

### Optimization Ideas

Use the following order when optimizing this case on Ascend 310B:

1. Start with `YOLOv10n`.
   `YOLOv10n` and `YOLOv10x` both use `640x640` input, but `YOLOv10x` has much
   higher compute cost. For camera testing and first OM validation, use
   `YOLOv10n_gestures`.

2. Do not optimize the warning blindly.
   `/model.23/Div_1` and `/model.23/Mod` are output-side index operations. They
   may be much cheaper than the convolution layers. Confirm real latency with an
   OM benchmark before spending time rewriting the ONNX graph.

3. If these postprocessing operators are proven to be expensive, export a model
   without embedded TopK/index decoding and move the final TopK/class-id decode
   into the application code. This can remove `Div`/`Mod` from the OM graph, but
   it also means the application must handle more raw model outputs and the CPU
   postprocess code becomes more complex.

4. If lower latency is more important than maximum accuracy, re-export at a
   smaller static input size such as `512` or `416`, then convert that ONNX model
   to OM. Changing only the camera capture size does not reduce model compute;
   the ONNX/OM input shape must also change.

5. For CPU-only ONNX testing, keep using asynchronous inference, frame skipping,
   and the small model. Useful script options are `--infer-every-n`,
   `--no-window`, `--max-frames`, `--intra-op-threads`, and `--sync-infer` for
   comparison.

6. Advanced options such as quantization, CANN auto-tuning, or moving
   preprocessing into AIPP/DVPP may help later, but they should be tried only
   after the baseline OM model has been measured on the real 310B device.

## Test ONNX With USB Camera

Use `scripts/infer_onnx_camera.py` to test the ONNX model with ONNX Runtime before converting to Ascend OM. This test uses the single package requirements file and does not need Torch, TorchVision, or Ultralytics:

```bash
pip install -r requirements.txt
```

```bash
python scripts/infer_onnx_camera.py
```

If your camera appears as another device, replace `/dev/video0` with `/dev/video1` or the correct path.

On the Ascend 310B CPU path, use the `YOLOv10n` model for interactive testing.
`YOLOv10x` is too slow under ONNX Runtime CPU and should be reserved for OM/NPU
testing after conversion. For SSH benchmarking without an OpenCV window:

```bash
python scripts/infer_onnx_camera.py \
  --no-window \
  --max-frames 30
```

By default the camera loop and ONNX Runtime inference are decoupled: the latest
frame is sent to a background inference thread and the display reuses the most
recent detections. Add `--sync-infer` only when you want the older blocking
behavior for debugging.

Smoke test with an image and save the annotated result:

```bash
python scripts/infer_onnx_camera.py \
  --model models/YOLOv10n_gestures.onnx \
  --source ../images/example.jpeg \
  --once \
  --save models/onnx_test.jpg
```

## Test OM With USB Camera

After ATC conversion, use `scripts/infer_om_camera.py` on the Ascend 310B
device. The default model is `models/YOLOv10n_gestures.om`, and the default
source is `/dev/video0`, so the normal camera command is:

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/Documents/Ascend310/samples/case8
/home/HwHiAiUser/.conda/envs/npu/bin/python scripts/infer_om_camera.py
```

For SSH benchmarking without an OpenCV window:

```bash
/home/HwHiAiUser/.conda/envs/npu/bin/python scripts/infer_om_camera.py \
  --no-window \
  --max-frames 60
```

To check only OM loading and NPU inference latency with synthetic input:

```bash
/home/HwHiAiUser/.conda/envs/npu/bin/python scripts/infer_om_camera.py \
  --benchmark-runs 20 \
  --print-model-info
```

To run another converted model:

```bash
/home/HwHiAiUser/.conda/envs/npu/bin/python scripts/infer_om_camera.py \
  --model models/YOLOv10x_gestures.om
```

The OM script reuses the same preprocessing and postprocessing as
`scripts/infer_onnx_camera.py` through the shared `hagrid_yolo/` package: letterbox
resize to the static model input, NCHW RGB float input, and YOLOv10 output rows
shaped as `[x1, y1, x2, y2, score, class_id]`.

## WebRTC H.264 Web App

Use the WebRTC app when you want to view the annotated OM camera stream from
another computer. This path uses browser WebRTC video with H.264 negotiation
instead of Gradio image streaming, so it avoids the repeated JPEG/base64-style
frame transfer overhead that limited the remote Gradio preview FPS.

By default the app patches aiortc's H.264 encoder to use Ascend CANN VENC
hardware encoding. If VENC cannot be initialized or fails while encoding, the
encoder falls back to aiortc/libx264 CPU encoding. `/health` reports
`cann-venc-h264` or `cpu-libx264-fallback`; when fallback happens at runtime,
`encoder_last_error` contains the CANN error text.

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/Documents/Ascend310/samples/case8
/home/HwHiAiUser/.conda/envs/npu/bin/python scripts/webrtc_om_app.py
```

The app listens on `0.0.0.0:8080` by default. Open one of these URLs from a
browser on the same network:

```text
http://313:8080
http://<board-ip>:8080
```

The terminal prints the usable hostname, LAN IP, and loopback URLs at startup.
If `8080` is already in use, the script automatically tries the next ports
(`8081`, `8082`, and so on). Use `--strict-port` only when you want the script
to fail instead of switching ports.

The web UI lists OM models by file name, for example `YOLOv10n_gestures.om`;
you do not need to type the `models/` prefix. The default model is
`YOLOv10n_gestures.om`. The page also shows the actual model input, which is
`640x640` for the current converted models.

The `采集分辨率` control changes the camera capture resolution, such as
`640x480`, `1280x720`, or `1920x1080`. This is separate from the OM model input:
each camera frame is still letterboxed to the model's static `640x640` input
before inference. A larger camera resolution can make the browser preview
clearer, but it does not reduce or increase the model compute unless you export
and convert a model with a different input size.

### WebRTC FPS And Latency

The WebRTC sender uses an asynchronous pipeline:

```text
capture latest camera frame -> infer latest frame in a worker -> draw latest detections -> VENC/WebRTC
```

This keeps camera capture, OM inference, and WebRTC sending from blocking each
other. The stream draws boxes on the original camera frame, but the box result
may come from the most recent completed inference rather than the exact same
frame. This is usually the right tradeoff for a live preview because display
FPS is no longer capped directly by one synchronous `capture + preprocess + NPU
+ postprocess + draw + encode` chain.

The page shows several different FPS and timing values:

| UI field | Meaning |
| :--- | :--- |
| `FPS` overlay | Browser-side decoded/received FPS. This is what you actually see remotely. |
| `服务端 FPS` | Server-side WebRTC output FPS after drawing boxes. |
| `采集/编码` `cap ... fps` | Actual camera capture FPS measured in the Python process. |
| `采集/编码` `cap ... ms` | Time spent waiting for and decoding one camera frame. |
| `NPU` | `acl.mdl.execute` time only. It does not include preprocessing, output copy, postprocess, drawing, or encoding. |
| `推理状态` `total ... ms` | End-to-end model worker time for preprocess + OM execute + output decode/postprocess. |
| `推理状态` `... fps` | Completed inference worker FPS. |

So `NPU = 20 ms` does not guarantee `50 fps` video. If capture, BGR/RGB/NV12
conversion, postprocess, drawing, VENC input copy, browser decode, or network
delivery consume additional time, the browser FPS will be lower. If the page
shows `cap 15 fps`, the camera path itself is only delivering about 15 fresh
frames per second, regardless of NPU speed.

The WebRTC app defaults to `infer_every_n=1`, which runs OM inference for every
captured frame and gives the freshest detection boxes. On the tested 310B setup
at `1280x720@30` with `YOLOv10n_gestures.om`, this may reduce the browser FPS
to the high-20s because NPU inference, postprocess, and encoding still compete
for CPU/runtime time. If smooth 30 FPS remote preview is more important than
per-frame box freshness, set `infer_every_n=2` in the page controls:

```text
infer_every_n=1  -> default, best box freshness, lower video FPS on this board
infer_every_n=2  -> smoother remote preview, about 30 FPS video
```

Check the camera's real USB/V4L2 modes on the 310B board:

```bash
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

Many USB cameras support high FPS at `1280x720` only in `MJPG` mode. `YUYV` at
the same resolution can be limited to 10 or 15 FPS because it uses much more USB
bandwidth. Keep the page's `摄像头 FourCC` set to `MJPG`, and confirm the page
shows the actual FourCC as `MJPG` in the `采集/编码` line. If OpenCV still cannot
hold the target FPS, try the `DVPP JPEGD` backend so JPEG decode is moved away
from the CPU path. On the current test camera, OpenCV + MJPG is the stable
default; the DVPP JPEGD path is still optional because some MJPEG frames may fail
DVPP JPEG parsing with errors such as `jpeg_get_image_info failed: 100000`.

Useful options:

```bash
/home/HwHiAiUser/.conda/envs/npu/bin/python scripts/webrtc_om_app.py \
  --model models/YOLOv10n_gestures.om \
  --source /dev/video0 \
  --camera-width 1280 \
  --camera-height 720 \
  --camera-fps 30 \
  --infer-every-n 2 \
  --bitrate-kbps 4000 \
  --port 8080
```

Hardware encoding is enabled by default. Use `--no-hardware-encode` only when
you intentionally want to compare against aiortc/libx264 CPU encoding:

```bash
/home/HwHiAiUser/.conda/envs/npu/bin/python scripts/webrtc_om_app.py \
  --no-hardware-encode
```

The script forces WebRTC codec preference to `video/H264` on both the browser
offer and the aiortc sender answer. The default camera backend is still OpenCV:

```text
OpenCV camera capture -> BGR original frame -> OM inference -> draw boxes -> CANN VENC H.264
```

For cameras that support MJPEG, you can try the DVPP JPEGD camera backend:

```bash
/home/HwHiAiUser/.conda/envs/npu/bin/python scripts/webrtc_om_app.py \
  --camera-backend dvpp \
  --camera-width 1280 \
  --camera-height 720 \
  --camera-fps 30
```

The DVPP path is:

```text
V4L2 MJPEG capture -> DVPP JPEGD -> BGR original frame -> OM inference -> draw boxes -> CANN VENC H.264
```

If `--camera-backend dvpp` cannot open the camera as MJPEG or cannot initialize
DVPP JPEGD, the `/offer` request fails with an explicit error instead of
silently falling back to OpenCV. The browser log shows the returned error text.
Use the page's `采集后端` control to switch between `OpenCV` and `DVPP JPEGD`.
The server also validates that the selected backend can produce the first
annotated frame before returning the WebRTC answer, so a DVPP/JPEGD startup
failure is reported during connection setup rather than being hidden in the
background capture thread.

The OM backend, VENC encoder, and optional JPEGD decoder run in the same Python
process. The WebRTC path keeps the ACL runtime initialized while a stream is
active and avoids global `acl.finalize()` in the per-track cleanup path, so do
not add per-frame or per-track global `acl.finalize()` calls here.

The default H.264 target bitrate is `4000 kbps`, which gives a clearer 720p
preview than the earlier automatic low-bitrate estimate. For a sharper image,
select `6000`, `8000`, or `10000 kbps` in the page, or start the script with a
higher `--bitrate-kbps` value. If you select `自动估算` in the page, the server
estimates the target bitrate from the selected capture resolution and FPS.

Useful log keywords:

```text
H264 encoder switched to CANN VENC hardware
VENC encode frame
Using direct V4L2 MJPEG capture backend for DVPP
DVPP JPEGD decode frame
cpu-libx264-fallback
```

For WebRTC, the browser must be able to reach the board's ICE/UDP media
candidates. An SSH `-L` tunnel only forwards the HTTP signaling page; it is not
a complete WebRTC media tunnel. For normal remote viewing, put the browser and
the 310B board on the same LAN and open the printed board URL directly.

You can still use SSH forwarding to check that the HTTP page and `/health`
route are alive:

```bash
ssh -L 8080:127.0.0.1:8080 313
```

Then open:

```text
http://127.0.0.1:8080
```

If the page loads through the tunnel but the video stays disconnected, use the
LAN URL printed by the script or add a proper TURN/relay setup.

`scripts/gradio_om_app.py` is kept only as a compatibility entry. Running it
prints a notice and launches `scripts/webrtc_om_app.py`.

## Notes

- Static input shape is recommended for Ascend 310B, so this package defaults to `batch=1`, `imgsz=640`, and no dynamic axes.
- ONNX export should be done outside the Ascend 310B device. A GPU workstation is preferred for this model-preparation step.
- The Ascend 310B target does not need Torch, TorchVision, or Ultralytics.
- ONNX Runtime is included in `requirements.txt` only for ONNX camera testing. OM inference with Ascend uses CANN/ACL instead.
- The ONNX demo expects Ultralytics YOLOv10 exported output shaped like `[1, 300, 6]`, where each row is `[x1, y1, x2, y2, score, class_id]`.
- ONNX and OM inference share the same preprocessing, postprocessing, drawing, and camera-loop code from the `hagrid_yolo/` package.
