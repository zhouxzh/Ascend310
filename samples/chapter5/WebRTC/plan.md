# H.265 WebRTC Migration Plan

## Goal

Add an explicit H.265/HEVC mode for the Ascend 310B WebRTC sender.

Target command:

```bash
python server.py --source dvpp_camera --video-codec h265
```

When H.265 is selected:

- The server negotiates only `video/H265`.
- Browser offers without `video/H265` are rejected with a clear error.
- CANN/VENC failures are fatal for the H.265 path.
- No H.264 fallback and no CPU encoder fallback are used.

## Runtime Assumptions

- Reference runtime is the Ascend 310B host reachable as `310`.
- Remote project path:
  `/home/HwHiAiUser/Documents/Ascend310/samples/chapter5/WebRTC`
- Remote Python:
  `/home/HwHiAiUser/.conda/envs/npu/bin/python`
- CANN environment:
  `source /usr/local/Ascend/ascend-toolkit/set_env.sh`
- Browser must support WebRTC HEVC and expose `video/H265` in
  `RTCRtpReceiver.getCapabilities("video")`.

## Phase 1: Remote Environment Confirmation

Run on `310`:

```bash
cd ~/Documents/Ascend310/samples/chapter5/WebRTC
source /usr/local/Ascend/ascend-toolkit/set_env.sh
/home/HwHiAiUser/.conda/envs/npu/bin/python -V
/home/HwHiAiUser/.conda/envs/npu/bin/python -c "import aiortc, av, aiohttp, numpy; print(aiortc.__version__, av.__version__)"
/home/HwHiAiUser/.conda/envs/npu/bin/python -c "from aiortc import RTCRtpSender; print(RTCRtpSender.getCapabilities('video').codecs)"
```

Expected outcome:

- Python imports required dependencies.
- aiortc codec capabilities are known before patching.
- If aiortc does not expose `video/H265`, the project will patch aiortc at
  startup for the H.265 mode.

## Phase 2: Validate Ascend VENC H.265 Raw Output

Before touching WebRTC signaling, validate that CANN VENC can produce a valid
HEVC elementary stream.

Implementation tasks:

- Add `ENTYPE_H265_MAIN = 0`.
- Allow `CannVenc` to receive `entype=ENTYPE_H265_MAIN`.
- Add a small validation script or test that:
  - creates NV12 test frames,
  - encodes them through VENC H.265,
  - writes `output/test.h265`,
  - parses Annex-B NAL units.

Validation criteria:

- VENC channel logs show `entype=0`.
- Output stream is non-empty.
- Annex-B stream contains HEVC parameter or IDR NALs:
  - VPS: NAL type 32
  - SPS: NAL type 33
  - PPS: NAL type 34
  - IDR: NAL type 19 or 20

Optional command if `ffprobe` is available:

```bash
ffprobe output/test.h265
```

## Phase 3: Implement HEVC RTP Packetization

Current `CannH264Encoder` inherits aiortc's `H264Encoder` and reuses H.264 RTP
packetization. H.265 must not reuse H.264 packetization.

Implementation tasks:

- Add HEVC Annex-B NAL splitting.
- Add HEVC RTP payload generation using RFC 7798:
  - single NAL packets for small NAL units,
  - fragmentation units for large NAL units.
- Implement `CannH265Encoder`.
- Keep the first version narrow: single NAL + FU packetization is enough for
  end-to-end WebRTC debugging. Aggregation packets can be added later if needed.

Validation criteria:

- Unit tests cover:
  - Annex-B splitting,
  - HEVC NAL type extraction,
  - small NAL single packet behavior,
  - large NAL fragmentation and start/end flags.

## Phase 4: Register/Patch aiortc H.265 Codec

Implementation depends on the installed aiortc version.

If aiortc already exposes H.265:

- Patch the H.265 encoder factory to use `CannH265Encoder`.

If aiortc does not expose H.265:

- Add startup patching for H.265 mode:
  - expose `video/H265` in video capabilities,
  - return `CannH265Encoder` from encoder creation,
  - keep clock rate at `90000`,
  - use dynamic payload type negotiation through aiortc SDP.

Validation criteria:

- `RTCRtpSender.getCapabilities("video")` includes `video/H265` after patching.
- Generated answer SDP includes `a=rtpmap:<pt> H265/90000`.
- H.265 mode fails fast if patching cannot be applied.

## Phase 5: Server-Side Codec Selection and Offer Validation

Implementation tasks:

- Add `--video-codec` with supported values:
  - `h264`
  - `h265`
- Keep existing behavior for `h264`.
- In `h265` mode:
  - initialize CANN encoder patching before creating answers,
  - require local `video/H265` capability,
  - set transceiver codec preferences to H.265 only,
  - reject remote offers that do not contain `H265/90000`.

Expected HTTP error for unsupported browser offers:

```text
Browser offer does not contain video/H265. Use a WebRTC HEVC-capable browser.
```

## Phase 6: Browser-Side H.265 Offer

Implementation tasks:

- Add a client-side codec preference for H.265 when the server/page is in H.265
  mode.
- Before `createOffer()`, check:

```javascript
RTCRtpReceiver.getCapabilities("video").codecs
```

- If `video/H265` is unavailable, show a clear page error and do not send the
  offer.
- If available, call `transceiver.setCodecPreferences(h265Codecs)` so the offer
  contains H.265 only.

Validation criteria:

- Unsupported browsers fail before `/offer`.
- Supported browsers send an offer containing `H265/90000`.

## Phase 7: End-to-End Ascend 310B Validation

Run on `310`:

```bash
cd ~/Documents/Ascend310/samples/chapter5/WebRTC
source /usr/local/Ascend/ascend-toolkit/set_env.sh
/home/HwHiAiUser/.conda/envs/npu/bin/python server.py \
  --host 0.0.0.0 \
  --port 8080 \
  --source dvpp_camera \
  --video-codec h265 \
  --log-level DEBUG
```

Browser validation:

- Open `http://<310-ip>:8080` from a WebRTC HEVC-capable browser.
- Confirm video starts.
- Confirm `webrtc-internals` shows inbound H.265/HEVC codec.
- Confirm server logs show:
  - H.265 codec preference applied,
  - VENC `entype=0`,
  - ICE reaches connected/completed,
  - no H.264 fallback.

## Phase 8: Failure and Reconnect Tests

Required tests:

- Unsupported browser: page shows H.265 unsupported and does not call `/offer`.
- Malformed or incompatible offer: server returns HTTP 400.
- CANN initialization failure: server H.265 mode fails, with no H.264 fallback.
- Reconnect: old peer connection and VENC channel are released.
- `dvpp_camera`: NV12 remains direct path; no RGB conversion is introduced.

## References

- HEVC RTP payload format: RFC 7798
- Current H.264 packetizer reference: `aiortc.codecs.h264.H264Encoder`
- Browser compatibility must be verified on the actual receiver browser at
  runtime with `RTCRtpReceiver.getCapabilities("video")`.
