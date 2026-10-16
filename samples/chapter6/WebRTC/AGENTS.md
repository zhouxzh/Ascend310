# Project focus

This repository targets a WebRTC video publishing pipeline that runs on Ascend 310B.

Windows may be used for editing, syncing, or reviewing code, but runtime behavior, media generation, deployment, and validation must target Ascend 310B.

Current milestone order:

1. Keep the aiortc signaling and browser receive path working reliably on Ascend 310B.
2. Connect real Ascend-produced frames through the source adapter without changing signaling or transport behavior.
3. Validate ICE, codec negotiation, reconnect, and deployment topology on the device.

# Working priorities

- Treat Ascend 310B runtime behavior as the source of truth.
- Do not reintroduce Windows camera capture paths or Windows-only OpenCV backends.
- Keep source, preprocess, encode, signaling, and transport concerns separated.
- Keep signaling and session control platform-agnostic.
- Prefer narrow platform adapters over scattered device-specific conditionals.
- Isolate CANN, ACL, DVPP, MindX, or hardware-specific assumptions behind interfaces, adapters, or explicit config.

# Ascend-first expectations

- Check device-side source availability, offer and answer flow, ICE state, codec negotiation, and reconnect behavior.
- Be explicit about browser location, runtime, firewall, network topology, and port assumptions.
- Gather evidence from Ascend logs, process state, and deployment configuration before editing when the failure may be environmental.
- Windows is acceptable as a development workstation, but it is not the reference runtime.

# Platform boundaries

- Flag device-only APIs, packaging issues, native dependency coupling, permissions, and path assumptions.
- Prefer a single source adapter boundary instead of spreading hardware logic through signaling or browser code.
- Avoid baking Ascend-specific behavior directly into generic WebRTC session control unless there is no cleaner seam.

# Delegation guidance

- Use `webrtc_mapper` first when the responsible code path is not yet clear.
- Use `windows_camera_debugger` only for remote deployment or browser-connectivity diagnostics when the developer machine happens to be Windows. Do not use it for local camera capture logic.
- Use `ascend_310b_reviewer` before landing changes that affect device portability or runtime readiness.
- Use `docs_researcher` when browser, WebRTC, codec, or deployment behavior needs external verification.
- Use `stream_pipeline_worker` only after the target files and expected behavior are already scoped.

# Change discipline

- Prefer the smallest defensible change.
- Avoid unrelated refactors during bug fixing.
- Add diagnostics at platform boundaries when media path failures are hard to localize.
- If automated verification is not available, report exact manual verification steps and remaining risk.
