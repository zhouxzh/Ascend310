# AGENTS.md - case3 Instructions

## Scope

These instructions apply to the entire `case3` directory.

This file is the implementation contract for future agents working on case3.
It records the product intent, architectural boundaries, and accepted UX
decisions. Detailed operator instructions and API descriptions remain in
`doc/02-webui.md`; do not duplicate that document here.

## Project Intent

- Case3 is tutorial companion code intended for formal publication. Prefer
  readable, explicit code over clever abstractions, hidden behavior, temporary
  compatibility layers, or unexplained generated files.
- The application is a usable music workstation, not a landing page or a model
  showcase. The first screen must expose an actual performance workflow.
- The primary physical target is an Ascend 310B board with a 10-inch touch
  display. Desktop browsers and smaller tablets/phones remain supported.
- Preserve the distinction between browser UI behavior, board audio routing,
  MIDI state, model inference, and generated artifacts. Do not collapse these
  boundaries merely to reduce file count.
- Remove code only after proving that it has no runtime, test, tutorial, or
  deployment role. Test fixtures and generated render history are not dead
  code.

## Sources of Truth

- `AGENTS.md`: engineering constraints and design contract.
- `doc/02-webui.md`: detailed WebUI behavior, deployment, controls, and API guide.
- `doc/04-piano-ddsp.md`: Piano-DDSP model and runtime contract.
- `webui/src/`: browser implementation.
- `midi_ddsp_webui/`: FastAPI service, jobs, MIDI analysis, playback, and audio
  library index.
- `piano_ddsp_runtime/`: Piano-DDSP runtime and real-time MIDI state.
- `reports/webui/`: local/board test output and screenshots; these are generated
  evidence, not source code.
- Filesystem task metadata, MIDI files, WAV files, reports, model manifests,
  checksums, and conversion logs are authoritative artifacts. SQLite is only a
  rebuildable catalog over those files.

## Runtime Boundary

- The local computer is the development, frontend-build, and published-model download environment.
- TensorFlow checkpoint to ONNX export is historical only; case3 consumes verified published ONNX/OM releases.
- Ascend-only operations such as ATC conversion, OM inference, PyACL runtime
  checks, and `npu-smi` inspection must run on the target Ascend 310B board.

## Remote Board Safety

- Use the board's existing CANN and Conda `base` environment. The authorized
  deployment exception is `python -m pip install -r requirements.txt` and a
  subsequent pytest check in that environment.
- Do not install Node/npm, use `conda install`, run `apt install`, remove or
  upgrade unrelated packages, or initialize software on the remote board.
- If a system or CANN dependency is missing, stop that step, preserve the
  diagnostic output, and report the dependency as a blocker.
- Do not edit remote shell startup files or system configuration.

## Change Discipline

- Do not modify any files in the sibling `case2` directory.
- Do not revert unrelated user changes.
- Keep model checksums and raw conversion logs with each conversion result.
- Do not reintroduce removed legacy routes, duplicate visualizers, the removed
  `AudioWaveform`, or the old monolithic `webui/src/styles.css` without a new,
  documented requirement.
- Do not add a large front-end or audio dependency when the current React,
  Canvas, browser Audio API, and parsed MIDI data can implement the behavior.
- Preserve existing API and WebSocket message formats unless a versioned
  migration and compatibility tests are included.

## Test Assets

- Files under `midi/` and `midi_wav/` are local test assets for MIDI parsing,
  DDSP rendering, WebUI catalog checks, and audio playback validation. Do not
  delete them during cleanup or code reduction.
- These MIDI, MuseScore, and WAV files are intentionally excluded from the
  GitHub package by the repository `.gitignore`. Keep them in the local
  workspace and board staging area, but do not add them with `git add -f` or
  upload them to GitHub.
- Regenerate only the deterministic fixture when needed with
  `python tools/create_test_midi.py --output midi/ddsp-test.mid`; preserve the
  rest of the local test library.

## System Architecture

The production data flow is:

1. The development computer builds `webui/dist/` and edits Python/TypeScript
   source.
2. FastAPI serves the production bundle, REST endpoints, artifacts, and
   WebSocket events on the board.
3. Existing CANN/PyACL and OM bundles perform board inference. Audio is routed
   explicitly to a selected board device.
4. Rendered WAV, metadata, reports, checksums, and logs stay on the filesystem.
   `reports/webui/library.sqlite3` indexes successful MIDI-DDSP versions.

Do not run a Vite or Node production server on the board. Do not make the
browser responsible for model inference or board device selection.

## Product Workspaces

The top-level application has exactly four user workspaces. Real-time touch and
physical MIDI performance share one workspace with explicit input modes; keep
the other workspace jobs separate even though some share services.

### Real-Time Performance (`实时演奏`)

- Provide `触摸屏` and `MIDI 键盘` as a segmented input-mode control inside one
  mounted real-time workspace. Preserve the running session, shared settings,
  and catalog while navigating between the two stopped modes.
- Lock input-mode switching while the session is running or recording so the
  selected physical MIDI port and capture sources cannot disagree with the UI.

#### Touch Input (`触摸屏`)

- Intended for direct finger performance on the 10-inch display.
- Default to 25 keys and allow 13 or 25 keys only. These are screen instruments,
  so their key height is optimized for touch rather than copied from physical
  piano dimensions.
- Allow octave movement in 12-semitone steps within the A0-C8 boundary.
- Put start/stop, sound selection, live roll, playable keys, gain, velocity,
  transpose, reverb, pitch bend, and sustain in the performance hierarchy.
- A touch `pointerdown` sends `note_on` immediately. Never add a front-end hold
  delay to make a note audible.

#### MIDI Keyboard Input (`MIDI 键盘`)

- Intended for a physical MIDI controller and must remain a distinct mode from
  touch input inside the shared workspace.
- Support 32, 49, 61, and 88-key views. Allow octave movement for every range
  below 88 keys.
- Make the dynamic roll the visual focus. Keep MIDI port, modulation, pitch
  bend, output gain, and range controls compact; place infrequent settings in a
  lower details area.
- Do not put an 88-key touch keyboard in touch input mode.

### MIDI-DDSP

- Separate `音频库` from `新建渲染` with a segmented view control.
- `音频库` groups one MIDI source with multiple render versions. Selecting a
  version updates its configuration summary, piano-roll colors, artifact, and
  playback target together.
- `新建渲染` exposes MIDI selection/upload, model bundle, voice analysis,
  instrument assignment, gain/tail/seed, render stages, and final artifact.
- MIDI-DDSP has one visible piano-roll visualizer. The removed waveform must not
  return as a second animated panel on a 10-inch screen.
- Default playback target is `开发板喇叭`, not browser audio. Browser playback is
  an explicit alternative and must not alter the local/system mixer.
- Browser transport follows the mature controller contract used by
  `html-midi-player`: explicit loading/error state, play/pause, stop, loop,
  draggable seek, current/total time, active-note highlighting, and follow mode.
  Do not copy its multi-player/multi-visualizer advanced-demo layout.

### DDSP-VST Effect (`DDSP-VST`)

- This page is a microphone effect, not another MIDI synthesizer. Its fixed
  route is physical PulseAudio capture, Feature OM, Control OM, CPU DDSP
  synthesis, and an explicitly selected PulseAudio output.
- Production inference is OM-only. Both Feature and Control backends must
  report `acl/om`; do not add ONNX Runtime, TFLite, browser inference, or CPU
  model fallbacks.
- Accept catalog model/device IDs and bounded parameters only. The server owns
  model paths and PulseAudio source/sink resolution, and a monitor source must
  never be accepted as a microphone.
- Share the exclusive audio/NPU resource lock with real-time performance,
  MIDI-DDSP playback, audio input tests, and speaker tests. Device loss or an
  invalid model/hash/tensor contract must stop the effect and release resources.
- Keep one lightweight pitch/loudness Canvas and the controls in the physical
  `1920x969` viewport without page-level vertical scrolling. Do not add browser
  monitoring, recording, Dry/Wet passthrough, or a duplicate MIDI synth.
- Default to UGREEN camera capture, EDIFIER M16 Pro output, `-18 dB` output
  gain, and transformed audio only. Sustained overload must cause explicit
  safety mute rather than silent device rerouting.

### Devices (`设备`)

- Organize the page into `设备概览`, `音频设备`, and `运行环境` tabs.
- Show NPU/CANN/PyACL, audio inputs/outputs, MIDI ports, Bluetooth status,
  diagnostics, and speaker test without implying that a detected device has
  been audibly verified.
- `npu-smi` `Health: Alarm` is a warning on the known `ascend8t` board, not an
  automatic inference failure when the device is visible and real inference
  succeeds.
- Bluetooth controls use board facilities already present. If
  `bluetoothctl` is unavailable, report it; do not substitute local-browser
  Bluetooth or install software.

## Shared Real-Time Contract

- Touch and physical MIDI are two input modes over one exclusive real-time
  session. They share the selected patch, output device, recording, monitoring,
  parameters, resource lock, and diagnostics while retaining independent key
  ranges and mode-specific controls.
- Starting, switching, stopping, panic, disconnection, pointer cancellation,
  and source release must not leave hanging notes or a held pedal.
- New Piano-DDSP notes have a minimum audible gate of four 4 ms synthesis
  frames (`16 ms`) in `piano_ddsp_runtime/midi_state.py`. `note_on` remains
  immediate and an early `note_off` is delayed only inside synthesis state.
  Do not add network or UI latency to implement this rule.
- Repeat notes, sustain, voice stealing, `panic`, and `release_source` must keep
  their current semantics. Add regression tests whenever MIDI state changes.
- Output gain is a physical quantity shown in dB. The real-time range is
  `-60..+6 dB`, default `0 dB`; negative values attenuate and positive values
  boost. Changes apply live to synthesis output and never modify browser,
  PulseAudio, ALSA, or hardware mixer volume.
- Keep WebSocket note edges as the source for real-time visual history. Status
  snapshots are for active-key synchronization and recovery, not for
  reconstructing short notes.

## Piano-Roll Contracts

There are two deliberately separate components. Do not merge their state or
their time models.

### `LivePianoRoll`

- Displays past real-time note edges aligned to the current visible keyboard.
- Cache static key geometry and grid; draw note trails on a separate layer.
- Use one `requestAnimationFrame` loop, cap active drawing at 30 FPS, cap canvas
  pixel ratio at 1.25, and stop the loop while idle or hidden.
- Keep a bounded event history (currently 192 trails). Event callbacks enqueue
  state and schedule a frame; they do not synchronously redraw the full canvas.
- Render completed notes with a minimum visible height so a valid short note is
  never lost between animation frames.
- Notes outside the visible range or history window are intentionally clipped.

### `MidiFilePianoRoll`

- Displays a complete, read-only MIDI file timeline. It is not the real-time
  history renderer and is not an editor.
- Show voice/instrument colors, piano pitch labels, beat/bar grid, zoom, drag
  pan, progress cursor, active-note highlighting, and voice-to-assignment
  navigation.
- Use three Canvas layers: cached static grid, note layer, and dynamic cursor.
  Do not create one React/SVG node per note; 10,000-note inputs are part of the
  performance target.
- Keep a single animation loop only during playback/render progress and stop it
  when idle. Avoid full-sequence redraw on every note event.
- On a 10-inch screen the roll is the sole animated visualization. On phones it
  may start collapsed, but its expand target must remain touch accessible.
- Draw from `GET /api/v1/midi-files/{midi_id}/piano-roll`; do not reparse MIDI in
  the browser.

## Visual Design Contract

- Design for quiet, repeated workstation use. Use graphite, white/light gray,
  teal actions, amber warnings/active playback, and red errors. Do not add
  gradients, decorative blobs, oversized hero text, or marketing composition.
- Keep cards for repeated tracks/versions or genuine tools. Do not nest cards
  or turn every page section into a floating card.
- Use Lucide icons for familiar actions, icon tooltips, segmented controls for
  modes, switches/checkboxes for binary state, and range inputs for numeric
  state.
- Preserve stable dimensions so labels, playback state, note counts, and icons
  do not shift the keyboard or Canvas during interaction.
- Use visible text together with color for loading, selected, warning, error,
  running, paused, and unavailable states.
- All interactive controls require keyboard focus styles and accessible labels.
  Tabs use `tablist`, `tab`, and `tabpanel` semantics.

### Touch Sizing

- The physical board viewport after desktop/browser chrome is approximately
  `1920x969`; it must be tested explicitly with touch enabled.
- For the 10-inch layout, aim for 16 px body/control text, at least 14 px
  secondary text, and 20-22 px primary navigation. Primary actions are at least
  56 px high and ordinary touch targets at least 52 px high.
- Do not rely only on `(any-pointer: coarse)`: the dock, browser, or display can
  report a fine pointer. Keep the board-size fallback and explicit page classes
  such as `.realtime-stage--touch`.
- Full top navigation remains visible on the board display. Narrow phones use
  the bottom navigation and safe-area spacing; hide the full status footer when
  it competes with content.
- No supported viewport may have incoherent overlap or document-level
  horizontal scrolling.

## CSS Ownership

- `webui/src/styles/index.css` is the only style entry point.
- `tokens.css`: palette, radius, and stable control dimensions.
- `foundation.css` and `common.css`: reset and shared primitives.
- `shell.css` and `workspace.css`: app shell, navigation, panels, and workspace
  structure.
- `realtime-base.css` plus `realtime.css`: real-time stage, keyboards, controls,
  and touch overrides.
- `midi-ddsp.css`: MIDI library, render view, file roll, and audio transport.
- `devices-base.css` plus `devices.css`: device tabs, diagnostics, Bluetooth, and
  speaker test.
- `responsive-base.css` plus `responsive.css`: viewport behavior and final
  readability overrides.
- Before adding a selector, search all style modules for the same component.
  Extend the owning file and remove genuinely superseded duplicates; do not add
  another global override at the end of the cascade.

## MIDI-DDSP Audio Library

- Use Python standard-library `sqlite3`; do not add a database dependency.
- Database path: `reports/webui/library.sqlite3`.
- Enable foreign keys, WAL, `busy_timeout`, and migration through
  `PRAGMA user_version`.
- `midi_sources` identifies a composition by MIDI content SHA256.
- `render_versions` uses the render job ID and records model, voice mapping,
  instrument IDs, seed, output gain, tail, sample rate, configuration hash,
  state, and relative WAV/report references.
- `library_preferences` records an optional preferred render for each source.
- Every explicit render creates history even when its configuration hash matches
  an older version. Do not overwrite or silently deduplicate user renders.
- Playback resolution is: available preferred version, otherwise latest
  successful available version. If the user explicitly selects an unavailable
  version, return an error instead of silently changing versions.
- Missing files mark an indexed version unavailable. Do not silently delete its
  row. Startup synchronization must remain idempotent.
- If the database is corrupt, preserve a quarantined copy and rebuild the index
  from filesystem task metadata. Do not treat SQLite as the only copy of data.
- The first version does not provide version deletion, bulk cleanup, or MIDI
  note editing. Add those only with explicit product requirements and recovery
  tests.

## API and File Safety

- Keep paths server-owned. Browser requests submit catalog IDs, render IDs,
  device IDs, and bounded parameters, never arbitrary filesystem paths or shell
  commands.
- Preserve the current `/api/v1/realtime/*`, MIDI-DDSP library, piano-roll,
  artifact, job, device, Bluetooth, and speaker-test contracts described in
  `doc/02-webui.md`.
- The legacy `POST /api/v1/midi-ddsp/recordings/{job_id}/play` endpoint delegates
  to the same playback implementation as version playback; keep it compatible.
- The service is LAN-only and has no authentication. Keep mutating HTTP and
  WebSocket requests same-origin unless origins are explicitly configured.
- Uploads remain limited to `.mid`/`.midi` and the configured size boundary.
  Never expose a generic upload/download path.

## Performance and State Rules

- Do not make Canvas animation update the whole React page per frame.
- Do not poll faster to solve an event-delivery bug. Fix the event source,
  queue, or state boundary.
- Cache static geometry and parsing results. Invalidate caches only when size,
  range, zoom, source, or relevant configuration changes.
- Stop timers, animation frames, media playback, MIDI notes, and WebSockets on
  unmount or workflow shutdown.
- Provide stable loading placeholders and explicit empty/error/unavailable
  states. Never enable play before audio metadata can be loaded.
- Preserve cancellation, resource locks, progress heartbeats, stage progress,
  elapsed time, and ETA for long renders.

## Testing and Acceptance

Run local checks proportional to the change. The standard suite is:

```powershell
python -m pytest -q
cd webui
npm run test
npm run build
npm run test:e2e
```

- Local tests may syntax-check and unit-test Python but must not run PyACL, ATC,
  OM inference, `npu-smi`, or other board-only checks.
- Unit tests must cover MIDI state edges, library migrations/rebuild, version
  resolution, missing artifacts, API compatibility, roll geometry, short notes,
  cache invalidation, and idle animation behavior when those areas change.
- Playwright must cover at least `1920x969` board touch, `1366x768` desktop,
  `1024x768` tablet, and `390x844` mobile behavior as relevant.
- UI acceptance includes no horizontal overflow, no overlap, readable text,
  touch target size, semantic tabs, stable layout, and explicit status text.
- Canvas acceptance includes screenshot inspection and nonblank pixel checks;
  DOM presence alone is insufficient.
- Preserve the fast-touch regression: a press/release in one browser frame still
  sends both events, produces audio for the minimum gate, and leaves a visible
  roll mark.
- Preserve the MIDI-DDSP regression: exactly one visible piano-roll visualizer,
  no waveform panel, working browser seek/loop controls, and version changes
  update the roll and artifact together.

## Board Deployment and Validation

- Build the React production bundle locally. Synchronize source and
  `webui/dist/` to `/home/HwHiAiUser/Documents/case3` on `ascend8t`.
- Stage a new `dist` separately, verify hashes, then stop the current service and
  atomically switch the directory. Keep a rollback copy until HTTP and
  Playwright checks pass; remove only that verified temporary backup afterward.
- Activate the board's existing CANN and conda `base` environment. Do not fall
  back to system Python; install only this repository's `requirements.txt` via
  pip, then verify pytest.
- Start with the existing `scripts/run_webui.py`; do not edit shell startup or
  create a new system service as part of routine deployment.
- Verify `/`, `/api/v1/status`, WebSocket connection, process PID, served asset
  hashes, and the relevant real-device workflow after restart.
- For audio or inference changes, record actual board evidence such as
  `midi_to_pcm_ms`, `queue_latency_ms`, render block time, PCM nonzero data,
  clipping, and underruns. A successful HTTP response alone does not validate
  sound.
- Recount `midi/` and `midi_wav/` before and after deployment. Never use a
  mirroring command that deletes board-only test assets, task history,
  conversion logs, checksums, or user files.

## Future Optimization Order

When continuing work, optimize in this order:

1. Correct sound, complete MIDI edges, safe device routing, and recoverable
   files.
2. Touch readability and control reachability on the physical 10-inch display.
3. Stable frame time, bounded Canvas work, and no idle rendering.
4. Clear task/version state and useful error recovery.
5. Desktop/mobile polish and code reduction proven by tests.

Measure before replacing a working subsystem. A visually richer piano roll is
acceptable only if it keeps one visualizer, the 10-inch layout, short-note
visibility, bounded CPU/GPU work, and current playback/inference semantics.
