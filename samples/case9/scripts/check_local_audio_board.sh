#!/usr/bin/env bash
# Board-only microphone/speaker preflight. Raw audio stays in pipes and is
# discarded after computing a 1-second RMS; this script never creates a WAV.
set -euo pipefail

source_name="${LOCAL_CHAT_PULSE_SOURCE:-alsa_input.usb-046d_C922_Pro_Stream_Webcam_B7E0139F-02.analog-stereo}"
sink_name="${LOCAL_CHAT_PULSE_SINK:-alsa_output.usb-Jieli_Technology_UACDemoV1.0_4150344637353804-00.analog-stereo}"
[[ "$(uname -m)" == aarch64 ]] || { echo "Run on the aarch64 board." >&2; exit 1; }
command -v pactl >/dev/null; command -v parec >/dev/null; command -v paplay >/dev/null
pactl list short sources | awk -v name="$source_name" '$2 == name {found=1} END {exit !found}'
pactl list short sinks | awk -v name="$sink_name" '$2 == name {found=1} END {exit !found}'
echo "Sampling one second from configured C922 source; raw bytes are discarded."
# The reader intentionally stops after one second, so parec may report SIGPIPE
# when it notices that the downstream consumer closed.  Validate the reader's
# result explicitly instead of treating that expected producer status as a
# failed microphone check.
set +e
timeout 4 parec --raw --format=s16le --rate=16000 --channels=1 --device="$source_name" \
  | head -c 32000 \
  | python3 -c 'import array, math, sys; raw=sys.stdin.buffer.read(); values=array.array("h"); values.frombytes(raw); assert len(raw)==32000, f"expected 32000 bytes, got {len(raw)}"; print(f"microphone samples={len(values)} rms={math.sqrt(sum(v*v for v in values)/len(values)):.1f}")'
reader_status=${PIPESTATUS[2]}
set -e
test "$reader_status" -eq 0
echo "Testing USB speaker stream with one second of silent in-memory PCM."
head -c 44100 /dev/zero | paplay --raw --format=s16le --rate=22050 --channels=1 --device="$sink_name"
echo "PulseAudio microphone and speaker path checks passed."
