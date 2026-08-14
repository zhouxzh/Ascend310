#!/usr/bin/env bash
# Board-only 6022BE/libsigrok continuous-throughput sweep.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${project_root}/data/sigrok_throughput"
binary="/tmp/case5_sigrok_streaming_probe"
duration_ms="${CASE5_SIGROK_DURATION_MS:-10000}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_file="${output_dir}/${timestamp}.jsonl"

mkdir -p "${output_dir}"
gcc -O3 -std=c11 -Wall -Wextra -Werror \
  "${project_root}/scripts/measure_sigrok_streaming.c" \
  -o "${binary}" $(pkg-config --cflags --libs libsigrok)

printf '{"record_type":"metadata","sigrok_cli_version":"%s","duration_ms":%s}\n' \
  "$(sigrok-cli --version | head -1)" "${duration_ms}" > "${output_file}"

for channels in 1 2; do
  for rate_hz in 1000000 4000000 8000000 16000000 24000000 30000000 48000000; do
    printf 'channels=%s requested_rate_hz=%s\n' "${channels}" "${rate_hz}" >&2
    if "${binary}" "${rate_hz}" "${channels}" "${duration_ms}" >> "${output_file}"; then
      :
    else
      printf '{"requested_rate_hz":%s,"active_channels":%s,"error":"probe_failed"}\n' \
        "${rate_hz}" "${channels}" >> "${output_file}"
    fi
  done
done

printf '%s\n' "${output_file}"
