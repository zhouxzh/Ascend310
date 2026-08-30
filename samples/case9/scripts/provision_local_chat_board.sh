#!/usr/bin/env bash
# Prepare the separate board-only environment and local artifacts for case9.
# This script never modifies conda base, system startup files, or the gateway's
# existing environment. It must be run on the aarch64 board by the board user.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/provision_local_chat_board.sh [--install-runtime] [--download-speech] [--download-models] [--build-llama]

  --install-runtime  Create/update the isolated case9-local-chat conda env.
  --download-speech  Download, verify, and extract only the fixed ASR/TTS
                     archives. Useful when the separate Qwen download is
                     temporarily unavailable.
  --download-models  Download and verify the ASR, TTS, and Qwen artifacts.
  --build-llama      Clone the pinned llama.cpp revision and build llama-server
                     with its CANN backend. This does not start inference.
EOF
}

install_runtime=false
download_speech=false
download_models=false
build_llama=false
for arg in "$@"; do
  case "$arg" in
    --install-runtime) install_runtime=true ;;
    --download-speech) download_speech=true ;;
    --download-models) download_models=true ;;
    --build-llama) build_llama=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This provisioning script is board-only and requires aarch64." >&2
  exit 1
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="$repo_dir/local_model_manifest.json"
home_dir="${CASE9_LOCAL_CHAT_HOME:-$HOME/case9-local-chat}"
artifact_dir="$home_dir/artifacts"
source_dir="$home_dir/src"
lock_file="$artifact_dir/manifest.lock.json"
environment_name="${CASE9_LOCAL_CHAT_ENV:-case9-local-chat}"
mkdir -p "$artifact_dir" "$source_dir"

manifest_field() {
  local artifact="$1" field="$2"
  python3 - "$manifest" "$artifact" "$field" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as source:
    value = json.load(source)["artifacts"][sys.argv[2]][sys.argv[3]]
print("" if value is None else value)
PY
}

record_lock() {
  local artifact="$1" path="$2" source_url="$3" sha256="$4" bytes="$5"
  python3 - "$lock_file" "$artifact" "$path" "$source_url" "$sha256" "$bytes" <<'PY'
import json, os, sys
from datetime import datetime, timezone
lock_path, artifact, path, source_url, sha256, bytes_value = sys.argv[1:]
try:
    with open(lock_path, encoding="utf-8") as source:
        document = json.load(source)
except FileNotFoundError:
    document = {"schema_version": 1, "artifacts": {}}
document["artifacts"][artifact] = {
    "path": path, "source_url": source_url, "sha256": sha256,
    "bytes": int(bytes_value),
    "recorded_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
}
temporary = lock_path + ".tmp"
with open(temporary, "w", encoding="utf-8") as output:
    json.dump(document, output, indent=2, sort_keys=True); output.write("\n")
os.replace(temporary, lock_path)
PY
}

verify_or_download() {
  local artifact="$1" filename="$2"
  local destination="$artifact_dir/$filename"
  local expected_bytes expected_sha source_url actual_bytes actual_sha locked_sha
  expected_bytes="$(manifest_field "$artifact" expected_bytes)"
  expected_sha="$(manifest_field "$artifact" sha256)"
  source_url="$(manifest_field "$artifact" url)"
  # The official Hugging Face endpoint can be unavailable from an isolated
  # board network. An operator may explicitly provide a transport mirror for
  # the fixed Qwen revision, but the immutable path and published SHA-256 are
  # still mandatory and the actual transport URL is recorded in the lock file.
  if [[ "$artifact" == "llm" && -n "${CASE9_LLM_DOWNLOAD_URL:-}" ]]; then
    source_url="$CASE9_LLM_DOWNLOAD_URL"
    revision="$(manifest_field llm revision)"
    if [[ "$source_url" != *"/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/$revision/"* ]]; then
      echo "CASE9_LLM_DOWNLOAD_URL must name the fixed Qwen repository and revision" >&2
      exit 2
    fi
  fi
  if [[ ! -f "$destination" ]]; then
    echo "Downloading $artifact to $destination"
    curl --http1.1 --fail --location --retry 4 --retry-delay 3 --continue-at - \
      --output "$destination" "$source_url"
  fi
  actual_bytes="$(stat -c '%s' "$destination")"
  if [[ -n "$expected_bytes" && "$artifact" != llm && "$actual_bytes" != "$expected_bytes" ]]; then
    echo "$artifact size mismatch: got $actual_bytes, expected $expected_bytes" >&2
    exit 1
  fi
  actual_sha="$(sha256sum "$destination" | awk '{print $1}')"
  if [[ -n "$expected_sha" && "$actual_sha" != "$expected_sha" ]]; then
    echo "$artifact SHA-256 mismatch for $destination" >&2
    exit 1
  fi
  if [[ -z "$expected_sha" && -f "$lock_file" ]]; then
    locked_sha="$(python3 - "$lock_file" "$artifact" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as source:
        print(json.load(source)["artifacts"][sys.argv[2]]["sha256"])
except (FileNotFoundError, KeyError):
    print("")
PY
)"
    if [[ -n "$locked_sha" && "$actual_sha" != "$locked_sha" ]]; then
      echo "$artifact differs from the board-local locked SHA-256" >&2
      exit 1
    fi
  fi
  record_lock "$artifact" "$destination" "$source_url" "$actual_sha" "$actual_bytes"
  echo "Verified $artifact: bytes=$actual_bytes sha256=$actual_sha"
}

if "$install_runtime"; then
  if [[ "${CASE9_ALLOW_LOCAL_CHAT_INSTALL:-}" != "1" ]]; then
    echo "Refusing local-chat dependency installation. Set CASE9_ALLOW_LOCAL_CHAT_INSTALL=1 only after reviewing the exact environment change." >&2
    exit 2
  fi
  test -f /usr/local/miniconda3/etc/profile.d/conda.sh
  # shellcheck disable=SC1091
  source /usr/local/miniconda3/etc/profile.d/conda.sh
  # Do not satisfy board-local requirements from ~/.local or the base conda
  # site-packages directory. This environment is intentionally self-contained.
  export PYTHONNOUSERSITE=1
  if ! conda env list | awk '{print $1}' | grep -Fxq "$environment_name"; then
    conda create --yes --name "$environment_name" python=3.9
  fi
  conda run --no-capture-output --name "$environment_name" python -m pip install \
    --disable-pip-version-check --no-input \
    -r "$repo_dir/requirements.txt" -r "$repo_dir/requirements-local-chat.txt"
  conda run --no-capture-output --name "$environment_name" python - <<'PY'
import importlib.util

blocked = {
    "torch", "torch_npu", "torchaudio", "torchvision", "torchtext",
    "mindtorch", "mindspore", "transformers", "vllm", "mindie",
    "qwen_ascend_llm", "onnxruntime", "xformers",
}
present = sorted(name for name in blocked if importlib.util.find_spec(name) is not None)
if present:
    raise SystemExit("forbidden inference packages are present: " + ", ".join(present))
print("forbidden inference packages: none")
PY
  conda run --no-capture-output --name "$environment_name" python -c \
    'import sherpa_onnx; print("sherpa_onnx import OK")'
fi

if "$download_speech" || "$download_models"; then
  verify_or_download asr sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23.tar.bz2
  verify_or_download tts vits-piper-zh_CN-huayan-medium.tar.bz2
fi
if "$download_models"; then
  verify_or_download llm qwen2.5-0.5b-instruct-q4_0.gguf
fi
if "$download_speech" || "$download_models"; then
  for artifact in asr tts; do
    if [[ "$artifact" == asr ]]; then
      archive="$artifact_dir/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23.tar.bz2"
    else
      archive="$artifact_dir/vits-piper-zh_CN-huayan-medium.tar.bz2"
    fi
    target="$artifact_dir/$artifact"
    if [[ ! -d "$target" ]]; then
      staging="$(mktemp -d "$artifact_dir/.${artifact}.extract.XXXXXX")"
      tar -xjf "$archive" -C "$staging"
      extracted="$(find "$staging" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
      test -n "$extracted"
      mv "$extracted" "$target"
      rmdir "$staging" || true
    fi
    echo "$artifact model directory: $target"
  done
fi

if "$build_llama"; then
  test -f /usr/local/Ascend/ascend-toolkit/set_env.sh
  llama_repo="$source_dir/llama.cpp"
  llama_revision="$(manifest_field llama_cpp revision)"
  llama_url="$(manifest_field llama_cpp repository)"
  if [[ -d "$llama_repo/.git" ]]; then
    git -C "$llama_repo" fetch --depth 1 origin "$llama_revision"
    git -C "$llama_repo" checkout --detach "$llama_revision"
  elif [[ -f "$llama_repo/.case9-pinned-revision" ]]; then
    actual_revision="$(tr -d '[:space:]' < "$llama_repo/.case9-pinned-revision")"
    if [[ "$actual_revision" != "$llama_revision" ]]; then
      echo "Pre-seeded llama.cpp source revision mismatch: $actual_revision" >&2
      exit 1
    fi
    echo "Using pre-seeded pinned llama.cpp source: $llama_repo @ $actual_revision"
  elif [[ -e "$llama_repo" ]]; then
    echo "Refusing to replace unverified existing llama.cpp path: $llama_repo" >&2
    exit 1
  else
    git clone --filter=blob:none --no-checkout "$llama_url" "$llama_repo"
    git -C "$llama_repo" fetch --depth 1 origin "$llama_revision"
    git -C "$llama_repo" checkout --detach "$llama_revision"
  fi
  # shellcheck disable=SC1091
  # CANN's generated set_env.sh reads some optional variables before assigning
  # them, so it is not nounset-safe. Keep the relaxation scoped to sourcing it.
  set +u
  source /usr/local/Ascend/ascend-toolkit/set_env.sh
  set -u
  cmake -S "$llama_repo" -B "$llama_repo/build-cann" \
    -DGGML_CANN=ON -DCMAKE_BUILD_TYPE=Release
  cmake --build "$llama_repo/build-cann" --target llama-server --parallel "$(nproc)"
  test -x "$llama_repo/build-cann/bin/llama-server"
  echo "llama.cpp CANN build OK at $llama_repo/build-cann/bin/llama-server"
fi

if ! "$install_runtime" && ! "$download_speech" && ! "$download_models" && ! "$build_llama"; then usage; fi
