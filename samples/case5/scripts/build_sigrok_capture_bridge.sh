#!/usr/bin/env bash
# Build the small libsigrok-to-BridgeFrameV1 adapter in the project directory.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_file="${project_root}/time_frequency_dashboard/acquisition/native/sigrok_capture_bridge.c"
output_dir="${project_root}/build"
output_file="${output_dir}/sigrok_capture_bridge"

for command in gcc pkg-config; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    printf 'missing required build command: %s\n' "${command}" >&2
    exit 1
  fi
done
if ! pkg-config --exists libsigrok; then
  printf 'libsigrok development files are missing (pkg-config libsigrok failed)\n' >&2
  exit 1
fi

mkdir -p "${output_dir}"
gcc -O3 -std=c11 -Wall -Wextra -Wpedantic -Werror \
  "${source_file}" -o "${output_file}" \
  $(pkg-config --cflags --libs libsigrok)
printf '%s\n' "${output_file}"
