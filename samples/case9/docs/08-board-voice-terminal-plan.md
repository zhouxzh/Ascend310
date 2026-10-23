# Voice Modes and Board Terminal Plan

## Status and Scope

This document defines two **experimental, partially validated** voice modes for
Ascend Edge Chat. Neither is a `v0.1.0` release capability, and neither changes
the text gateway, MindSpore worker, model lock, or the existing 20T text
acceptance result.

| Mode | Audio owner | Intended purpose | Current evidence |
| --- | --- | --- | --- |
| `board` | USB/3.5mm devices connected to the 20T board | Independent board voice terminal | Audio hardware enumeration only; VAD, board capture, board playback, board ASR/TTS, and board turns are `untested` |
| `browser` | The remote browser's microphone and speaker | HTTPS-gated browser voice chat | Browser capture/playback self-test and one synthetic ASR-to-text turn are `observed-pass`; TTS and the full W0-W7 gate remain incomplete |

The intended hardware target is `Ascend310B1 / 20T`. An 8T result must be
measured separately and must not inherit any result in this document. Every
remaining ASR, TTS, VAD, USB-device, browser-audio, and end-to-end voice item
stays `untested` until its own raw board/browser evidence and human review exist.

## Runtime Boundaries and Current Core Contract

The voice core has a loopback-only contract implemented under
`ascend_edge_chat/voice/`. It accepts only the modes `disabled`, `browser`, and
`board`; callers cannot select ALSA strings, model paths, gateway hosts, shell
commands, or raw board PCM through HTTP. Its currently defined loopback
endpoints are `/health`, `/api/config`, `/api/status`, and the browser-mode
conversion endpoints `/v1/voice/browser/transcribe` and
`/v1/voice/browser/synthesize`. The 20T board currently runs this service with
reviewed browser ASR, while board ALSA ownership and TTS remain inactive.

The shared LLM boundary remains unchanged:

```text
                 +-> existing loopback gateway :7861 -> MindSpore worker :8090
                 |
board mode:      voice-agent owns board ALSA input/output
browser mode:    HTTPS browser -> UI proxy -> loopback voice-agent
```

### Board Mode

```text
USB microphone
  -> local voice-agent
  -> VAD silence segmentation
  -> ASR
  -> existing loopback gateway :7861
  -> existing MindSpore worker :8090
  -> TTS
  -> USB speaker or USB audio adapter

Browser UI :7865
  -> status, transcript, diagnostics, and text fallback only
```

In `board` mode, the browser must not invoke `getUserMedia`, play the TTS
response, or access board ALSA. The board-local voice agent owns selected
capture/playback devices and calls the existing loopback gateway. The public
OpenAI gateway and NPU worker must not acquire audio devices or absorb ASR/TTS
logic.

### Browser Mode

```text
Browser microphone (HTTPS secure context)
  -> browser-recorded 16 kHz mono WAV
  -> HTTPS UI proxy
  -> loopback voice-agent browser-transcribe
  -> existing gateway :7861 -> worker :8090
  -> loopback voice-agent browser-synthesize
  -> finite WAV response through UI proxy
  -> browser speaker
```

`browser` mode never opens board ALSA and never returns board-captured PCM.
The voice core accepts only a bounded finite 16 kHz, mono, signed-16-bit PCM
WAV upload for browser transcription, and returns only a bounded finite WAV for
browser synthesis. The browser must not address `127.0.0.1:7862` directly:
that address would refer to the browser's own machine, not the board. The UI
server proxies only the reviewed loopback contract; raw board-captured PCM never
enters a browser response.

Remote browser microphone access requires a secure browser context. The current
trusted-LAN text UI at `http://<board-ip>:7865/` is not evidence that
`getUserMedia` will be permitted. Browser mode is blocked until a reviewed HTTPS
termination and certificate path are configured, then separately accepted by
browser permission, capture, upload, synthesis, playback, and privacy gates.

| Process | Board mode responsibility | Browser mode responsibility | Exposure |
| --- | --- | --- | --- |
| `voice-agent` | Capture, VAD, ASR, request correlation, TTS, local playback | Bounded WAV conversion only; no board ALSA | Loopback only |
| Gateway | Existing Bearer validation and JSON/SSE LLM contract | Same | `127.0.0.1:7861` |
| MindSpore worker | Existing serial LLM generation | Same | `127.0.0.1:8090` |
| Browser UI | Status, transcript, recovery controls, text fallback | HTTPS proxy, browser permission/status, transcript, finite audio playback | Trusted LAN only; HTTPS required for microphone access |

This is a teaching example, not a multi-user production voice service. It adds
no extra voice-operation token beyond the existing gateway Bearer contract. The
voice agent is loopback-only, device selection is restricted to its enumerated
allowlist, browser audio is proxied through the UI, and raw board-captured PCM
is never returned to the browser. A production deployment should add its own
operator authentication and HTTPS policy before exposing device controls beyond
a trusted teaching LAN.

## Observed 20T Audio Facts

Audio-device identity is dynamic and must be checked immediately before a board
voice run. Two read-only snapshots of the current `Ascend310B1 / 20T` board show
why no card/device number may be hard-coded:

| Snapshot | Observation | Evidence meaning |
| --- | --- | --- |
| `2026-10-20T10:52:23+08:00` | Board ALSA card `0 [ascend310b]`, playback `/dev/snd/pcmC0D0p`, capture `/dev/snd/pcmC0D1c` | The kernel exposes board audio nodes; this does not establish 3.5mm routing or audible output |
| `2026-10-20T10:52:23+08:00` | Jieli USB Composite Device appeared as card 1, with capture `01-00` / `/dev/snd/pcmC1D0c` | Historical USB capture enumeration only; no USB playback PCM entry was observed |
| `2026-10-21T08:27:52+08:00` | Only card 0 and its playback/capture entries remained in `/proc/asound/pcm` | The earlier USB device was not currently enumerated; board mode must not start against stale device IDs |
| `2026-10-21T08:48:35+08:00` | UGREEN Camera 1080P appeared as card 1, `hw:1,0`, capture-only, S16_LE, 2 channels, 16–48 kHz | The camera microphone is currently enumerated but requires stereo capture/downmix; no USB playback entry exists |
| Both snapshots | Node owner/mode `root:audio`, `0660` | A service user needs membership in `audio` or another reviewed access policy |
| Both snapshots | `HwHiAiUser`, `HwDmUser`, `HwBaseUser` | `HwHiAiUser` was not observed in `audio` during these inspections |

Neither snapshot proves useful microphone samples, audible speaker output,
VAD, ASR, TTS, or physical 3.5mm routing. The missing USB playback entry does
not prove that no speaker can ever work, but it blocks a USB-speaker acceptance
claim until the selected output device is currently enumerated and tested.
`npu-smi`, LLM JSON, and browser HTTP results are unrelated to audio acceptance.

A `2026-10-21T09:09+08:00` run of the synchronized
`voice-preflight.sh` stopped before any audio operation because
`ASCEND_EDGE_CHAT_VOICE_CONDA_ENV` is not configured on this board. This is an
expected fail-closed deployment result, not evidence for or against the USB
microphone, playback, ASR, or TTS.

## Hardware and Permission Prerequisites

Use a USB microphone plus USB speaker or USB audio adapter for the first board
audio iteration. Its ALSA identity must be enumerated on the actual board;
never hard-code `card 0`, `/dev/snd/pcmC0D1c`, or a historical USB name.

An operator must manually collect the following before starting any voice
process:

```bash
id -nG
cat /proc/asound/cards
cat /proc/asound/pcm
arecord -l
aplay -l
ls -l /dev/snd
```

The service account must have reviewed access to the selected capture and
playback nodes. Group membership and ACL/direct node permissions are both
valid; access must be proven on the current device nodes rather than inferred
from group names. If the selected access policy requires the `audio` group and
`HwHiAiUser` is not a member, an administrator may choose to add the user:

```bash
sudo usermod -aG audio HwHiAiUser
```

This is a manual host-permission change, not a voice-service deployment command. The
operator must log out and log in again, then re-run `id -nG` to prove the new
group is active. Do not run a voice service as `root` merely to bypass device
permissions.

For each selected USB device, retain only non-sensitive identity evidence:
manufacturer/product name, ALSA card/device number, capture or playback role,
and the command output. Do not store raw microphone audio as hardware evidence.

### Known Camera Microphone Incompatibility

The `SN0002 1080P USB Camera` (`USB ID 2bdf:0289`, serial prefix
`46435000_P030C00_SN0002`) is **not usable for microphone capture** on the tested
Orange Pi AI Pro system images. Its UVC video interface can still enumerate and
stream video; the incompatibility is specifically in the camera's UAC audio
microphone path.

| Board evidence set | Video | Microphone capture | Observation |
| --- | --- | --- | --- |
| `Ascend310B1 / 20T` | UVC video device enumerated and concurrent video stream started | Failed | `arecord` received 22 samples and then reported `pcm_read:2221: read error: Input/output error` |
| `Ascend310B4 / 8T` | UVC video device enumerated; video mode not separately re-streamed during this microphone test | Failed | The same camera reproduced the same 22-sample failure; this is a separate 8T result, not inherited from 20T |

The failure reproduced with direct USB connection, through the board USB path,
with `hw:1,0` and `plughw:1,0`, with adjusted ALSA periods/buffers, at 100%
microphone gain, with USB autosuspend disabled, and through PulseAudio/PipeWire.
Kernel evidence included `usb_set_interface failed (-110)`, endpoint
`cannot set freq 16000 to ep 0x82`, `device descriptor read/64, error -71`, and
USB disconnect/reset cycles. The standard Linux `snd-usb-audio` driver was
already loaded; replacing or installing a generic sound driver is not a
supported fix.

As a control on the 8T evidence set, a `UGREEN Camera 1080P`
(`USB ID 0c45:2283`) exposed stereo 16 kHz `S16_LE` capture and produced 48,011
frames in 3.001 seconds with non-zero RMS and no I/O error. This establishes
that the 8T capture path can work with a compatible UVC/UAC camera, while the
`SN0002` microphone path remains incompatible.

Operational rule: the `SN0002` camera may be used for video, but it must not be
selected as the board voice input. Use a reviewed external USB microphone or
3.5 mm input for microphone capture. Do not infer that the whole camera is
unusable, and do not treat a driver reinstall as a remedy without new evidence
from the vendor or kernel.

## Half-Duplex VAD State Machine

### Board Mode

The requested independent-terminal interaction is automatic VAD silence
segmentation, not browser push-to-talk and not a wake-word claim. The first
board-local design is strictly half duplex:

```text
IDLE
  -> LISTENING
  -> SPEECH
  -> SILENCE_HOLD
  -> ASR
  -> LLM
  -> TTS
  -> PLAYING
  -> COOLDOWN
  -> IDLE
```

- `LISTENING` accepts capture only after the selected device is available.
- `SPEECH` starts only after a configured VAD threshold is met.
- `SILENCE_HOLD` ends an utterance only after a configured continuous-silence
  duration; the threshold must be measured with the actual microphone.
- `ASR`, `LLM`, and `TTS` process one utterance at a time.
- `PLAYING` disables capture or applies a separately validated echo-control
  policy. The first version must not listen while it plays TTS.
- `COOLDOWN` prevents a speaker tail from immediately creating another turn.
- Cancellation, device removal, ASR failure, gateway failure, TTS failure, or
  playback failure must return to a safe `IDLE` state after releasing the
  capture/playback handle.

The silence duration, speech threshold, maximum utterance duration, cooldown,
and any echo policy are `untested` tuning parameters. Do not publish default
values until a recorded board test shows their behavior in the intended room.

### Browser Mode

Browser mode may use its own reviewed recording-stop or browser-side VAD policy,
but that policy is not the board ALSA VAD and must be accepted separately. The
current loopback voice-agent contract receives one already-finished bounded WAV
upload; it does not claim continuous browser listening or a wake-word service.
Browser playback must not start a board-side VAD turn, and board playback must
not be returned as raw board PCM to a browser.

The UI also exposes a localhost-only “browser recording playback self-test”. It
records and plays back in the current browser memory without calling ASR/TTS or
uploading the WAV. Passing this self-test proves browser permission, capture,
WAV conversion, and browser playback only; it does not pass W0-W7 or any board
voice gate.

## ASR and TTS Candidate Boundary

Historical repository material mentioned the same sherpa-onnx Zipformer family
and a Chinese VITS/Piper-style TTS candidate. On `2026-10-21`, browser ASR was
newly reviewed and admitted for technical testing in an isolated Python 3.9
environment:

| Item | Observed value |
| --- | --- |
| ASR runtime | `sherpa-onnx 1.13.6`, `sherpa-onnx-core 1.13.6`, CPU, Python 3.9.25 |
| ASR model | `sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23`, int8 encoder/decoder/joiner |
| License evidence | Model archive `README.md` declares `apache-2.0` |
| Archive SHA-256 | `2cbd71b640d9c37d3784f29367333a4577b0398b62e9deeed418170b081cba8b` |
| Encoder SHA-256 | `1c556ea57cec304e55ec4b72e52c1cc098bb01476ed7d90f3de939fe126487b1` |
| Decoder SHA-256 | `22f123bb8cba9b38974b3df18a3f45e7081f4985ebb2e075d9f21f618c468bbf` |
| Joiner SHA-256 | `a7cf9d82757bdcf786059454495a9ca95e4bd7347f72473fc08d794475c36169` |
| Tokens SHA-256 | `8b294db9045d6e5f94647f4c1eec1af4da143a75053c399611444b378ff966ac` |
| Offline sample | Bundled 16 kHz Chinese WAV produced a non-empty transcript |
| Browser bridge | Chromium synthetic microphone returned HTTP 200 and reached the text gateway |

This evidence admits one ASR technical path only. It does not establish ASR
accuracy, physical-microphone quality, board-mode behavior, hostile-client
privacy, or performance percentiles. TTS remains unconfigured and fail-closed.

Additional speech artifacts still require a runtime manifest that records:

- upstream repository and immutable revision;
- exact archive/model files, byte counts, and SHA-256 values;
- license and redistribution status for ASR, TTS, and voice data;
- Python/runtime package versions and whether they import Torch, Torch-NPU, or
  Torchaudio;
- selected CPU/NPU execution boundary;
- input PCM contract and output PCM contract;
- target SoC, CANN/driver fingerprint, and board date.

The current text worker remains the only NPU path with current 20T acceptance
evidence. ASR/TTS must not silently reuse that claim. A CPU ASR/TTS result is
an audio-service result, not an Ascend inference result.

## Privacy and Operator Controls

The default planned privacy policy is in-memory audio only:

- do not persist PCM, WAV, or synthesized speech by default;
- do not place raw audio in logs, reports, browser responses, or Git;
- retain only timestamps, state transitions, byte/sample counts, durations,
  non-secret error categories, and explicitly approved aggregate metrics;
- require an explicit, time-bounded, operator-controlled debug capture mode
  before retaining any consented audio sample;
- expose a local stop/cancel control that interrupts capture, LLM request, TTS,
  and playback, then releases device handles;
- serialize voice turns until concurrency and audio-device behavior are
  independently validated.

Browser transcript/status controls must not imply that browser microphone
permission is used in board mode. In browser mode, the browser's own microphone
permission, recorded WAV, finite synthesis response, and browser speaker are a
separate privacy surface: obtain user permission through HTTPS, enforce the
bounded WAV contract, avoid persistent recordings, and show mode/status clearly.
The teaching UI must still prevent browser code from directly addressing board
ALSA or the loopback voice-agent endpoint.

## Manual Acceptance Gates

Run the mode-specific gates in order and retain raw command output or ignored
board/browser reports. A failed gate stops the next one; a board-mode result
never promotes a browser-mode gate, and the reverse is also true.

### Board Mode Gates

| Gate | Required evidence | Current state |
| --- | --- | --- |
| B0 hardware identity | Enumerated USB/3.5mm capture and playback devices; account permissions; unplug/replug identity check | `observed-pass`: UGREEN capture `hw:0,0` and Jieli playback `hw:1,0` enumerated after replug on 20T |
| B1 capture | Selected microphone produces the declared PCM format, duration, sample count, and non-silent level without persisting raw audio | `observed-pass`: UGREEN stereo capture was non-silent without I/O errors |
| B2 playback | Selected speaker plays a declared PCM format; an operator confirms audible output | `observed-pass`: Jieli `hw:1,0` played the synthesized Chinese reply and the operator confirmed audibility |
| B3 VAD | Quiet-room, speech-start, silence-end, cancellation, and playback-suppression trials; false trigger/miss counts | `untested` |
| B4 ASR | Fixed consented Chinese prompt set, transcript output, reference text, error-rate protocol, and failure samples | `partial`: one bundled Chinese sample returned a non-empty transcript |
| B5 LLM bridge | Correlated local ASR text reaches the existing loopback gateway and one serial LLM result returns | `observed-pass`: deterministic ASR text reached the gateway and returned a Chinese reply |
| B6 TTS | Fixed text set, output PCM contract, first playable audio time, playback completion, and human listening review | `observed-pass`: eSpeak returned 22,050 Hz mono PCM, Jieli playback completed, and the operator confirmed hearing the Chinese reply; quality scoring remains separate |
| B7 end-to-end | At least ten sequential board voice turns with capture-end, ASR-end, first-token, first-audio, playback-end timestamps, resource snapshots, and human review | `partial`: one deterministic ASR→LLM→eSpeak→Jieli chain completed; ten-turn timing is absent |
| B8 recovery/privacy | Device unplug, cancel, malformed audio, ASR/TTS/gateway failure, allowlist rejection, and no-raw-audio-persistence checks | `partial`: unplug/replug inventory recovery, unit contracts and allowlist rejection are covered; full failure matrix remains manual |

### Browser Mode Gates

| Gate | Required evidence | Current state |
| --- | --- | --- |
| W0 secure context | Reviewed HTTPS termination, certificate chain, trusted origin, and browser `getUserMedia` permission prompt | `partial`: localhost secure context and synthetic permission passed; production HTTPS chain untested |
| W1 browser capture | User-authorized browser microphone produces a bounded 16 kHz mono WAV; board ALSA remains unopened | `observed-pass`: synthetic and operator-confirmed real browser microphone capture produced bounded WAV |
| W2 browser recording-stop/VAD | Recording-stop policy, silence behavior, cancellation, and no unsolicited capture behavior | `partial`: manual stop and two-second self-test stop passed; silence/cancel untested |
| W3 browser ASR | Valid/invalid WAV handling, transcript output, reference prompt set, and error protocol through the UI proxy | `observed-pass`: bundled and operator-confirmed real-microphone turns returned non-empty transcripts through HTTP 200 |
| W4 LLM bridge | Browser transcript reaches the existing server-side gateway without exposing the gateway token | `observed-pass`: recognized turns reached the gateway and returned text |
| W5 browser TTS/playback | Finite backend WAV through the UI proxy or browser-native `speechSynthesis` completion, followed by human listening review | `partial`: browser-native `zh-CN` synthesis call completed in automation and board eSpeak is now available; human audibility/quality remains pending |
| W6 privacy/recovery | Permission denial, HTTPS failure, oversized/malformed WAV, mode switch, cancel, and no board-ALSA access | `partial`: invalid/mode/error contracts are unit-tested; browser denial/cancel remain manual |
| W7 end-to-end | At least ten sequential browser voice turns with browser capture-end, ASR-end, first-token, first-audio, playback-end timestamps and human review | `partial`: synthetic ASR-to-text-to-browser-synthesis and real-microphone ASR-to-text turns passed; human listening and turn timing remain absent |

No HTTP 200, text-chat response, ALSA node, secure URL, imported package, or one
audible sound is sufficient to promote any later gate. Do not calculate p50/p95
until the timing boundary, warmup, turn count, and raw report path are fixed.

## Promotion Rule

Board mode may be described as an experimental feature only after B0 through B8
have current-board evidence, the ASR/TTS artifacts and licenses are reviewed,
and an operator accepts the privacy and listening-quality review. Browser mode
may be described as an experimental feature only after W0 through W7 have
separate secure-context, browser, proxy, and privacy evidence. Either mode may
enter a release scope only after a separate human approval. Until then, this
deployment remains a text-chat release with documented dual-mode voice design.
