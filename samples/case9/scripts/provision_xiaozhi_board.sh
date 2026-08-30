#!/usr/bin/env bash
# Provision the independent XiaoZhi server environment on the Ascend board.
# It never modifies conda base, shell startup files, or the case9 local-chat
# environment. Source, dependencies, models, and rendered secrets stay under
# $HOME/case9-xiaozhi unless CASE9_XIAOZHI_HOME is set.
set -euo pipefail

readonly XIAOZHI_REPOSITORY="https://github.com/xinnan-tech/xiaozhi-esp32-server.git"
readonly XIAOZHI_REVISION="e1876f1ce19cad6e7bfd7c80e41dc56b2e858dd5"
readonly XIAOZHI_ARCHIVE_ROOT="xiaozhi-esp32-server-${XIAOZHI_REVISION}"

usage() {
  cat <<'EOF'
Usage: bash scripts/provision_xiaozhi_board.sh [options]

Options may be combined. No operation is performed without an option.

  --clone-source          Clone xinnan-tech/xiaozhi-esp32-server and detach at
                          the pinned revision.
  --archive-source PATH   Import the pinned source from a pre-downloaded GitHub
                          tar.gz archive. Requires --archive-sha256 and refuses
                          to replace any existing source tree or provenance file.
  --archive-sha256 SHA256 Expected SHA-256 for --archive-source. Record this on
                          the controller before transferring the archive.
  --create-env            Create the isolated Python 3.10 conda environment.
  --install-dependencies  Disabled for this Ascend 310B4 case. The upstream
                          requirements include torch and torchaudio, which are
                          prohibited by the board deployment policy.
  --render-config         Render data/.config.yaml from the checked-in partial
                          template. Requires CASE9_GATEWAY_API_KEY in the
                          command environment; the value is never printed.
  --check                 Validate the pinned source, environment, and rendered
                          Case9 OpenAI provider configuration.
  -h, --help              Show this help.

Example:
  # This creates only the isolated Python 3.10 environment. Do not install the
  # upstream requirements until a reviewed no-Torch profile is available.
  bash scripts/provision_xiaozhi_board.sh --clone-source --create-env

Archive fallback example (do not combine with --clone-source):
  bash scripts/provision_xiaozhi_board.sh \
    --archive-source "$HOME/xiaozhi-esp32-server-${XIAOZHI_REVISION}.tar.gz" \
    --archive-sha256 '<recorded-controller-sha256>' \
    --create-env --install-dependencies
EOF
}

clone_source=false
archive_source_path=""
archive_sha256=""
create_env=false
install_dependencies=false
render_config=false
check=false

while (( $# > 0 )); do
  case "$1" in
    --clone-source)
      clone_source=true
      shift
      ;;
    --archive-source)
      if (( $# < 2 )); then
        echo "--archive-source requires a path." >&2
        usage >&2
        exit 2
      fi
      archive_source_path="$2"
      shift 2
      ;;
    --archive-sha256)
      if (( $# < 2 )); then
        echo "--archive-sha256 requires a SHA-256 value." >&2
        usage >&2
        exit 2
      fi
      archive_sha256="$(tr '[:upper:]' '[:lower:]' <<<"$2")"
      shift 2
      ;;
    --create-env)
      create_env=true
      shift
      ;;
    --install-dependencies)
      install_dependencies=true
      shift
      ;;
    --render-config)
      render_config=true
      shift
      ;;
    --check)
      check=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! "$clone_source" && [[ -z "$archive_source_path" ]] && ! "$create_env" && ! "$install_dependencies" && ! "$render_config" && ! "$check"; then
  usage
  exit 2
fi

if "$clone_source" && [[ -n "$archive_source_path" ]]; then
  echo "Choose exactly one source path: --clone-source or --archive-source." >&2
  exit 2
fi

if [[ -n "$archive_source_path" && -z "$archive_sha256" ]]; then
  echo "--archive-source requires --archive-sha256 from the transfer controller." >&2
  exit 2
fi

if [[ -z "$archive_source_path" && -n "$archive_sha256" ]]; then
  echo "--archive-sha256 is valid only with --archive-source." >&2
  exit 2
fi

if [[ -n "$archive_sha256" && ! "$archive_sha256" =~ ^[a-f0-9]{64}$ ]]; then
  echo "--archive-sha256 must be exactly 64 hexadecimal characters." >&2
  exit 2
fi

if "$install_dependencies"; then
  cat >&2 <<'EOF'
Refusing to install xinnan's full upstream requirements on this Ascend 310B4.
That requirements file pins torch and torchaudio for FunASR/Silero paths.
This case currently has no reviewed no-Torch XiaoZhi dependency profile.
EOF
  exit 2
fi

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This board provisioning script requires aarch64." >&2
  exit 1
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
template_path="$repo_dir/configs/xiaozhi-case9-override.template.yaml"
home_dir="${CASE9_XIAOZHI_HOME:-$HOME/case9-xiaozhi}"
case "$home_dir" in
  "$HOME"/*) ;;
  *)
    echo "CASE9_XIAOZHI_HOME must remain below $HOME: $home_dir" >&2
    exit 2
    ;;
esac
server_dir="$home_dir/xiaozhi-esp32-server"
# xinnan loads its board-local override relative to main/xiaozhi-server, not
# from the repository root.
config_path="$server_dir/main/xiaozhi-server/data/.config.yaml"
archive_provenance_path="$home_dir/xiaozhi-source-provenance.env"
environment_name="${CASE9_XIAOZHI_ENV:-case9-xiaozhi}"

load_conda() {
  if [[ -f /usr/local/miniconda3/etc/profile.d/conda.sh ]]; then
    # shellcheck disable=SC1091
    source /usr/local/miniconda3/etc/profile.d/conda.sh
  elif [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
  elif [[ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
  else
    echo "A conda profile script was not found; no system Python fallback is used." >&2
    exit 1
  fi
}

ensure_clean_source() {
  if [[ -d "$server_dir/.git" ]] && [[ -n "$(git -C "$server_dir" status --porcelain)" ]]; then
    echo "Refusing to change a source tree with local modifications: $server_dir" >&2
    exit 1
  fi
}

ensure_source_layout() {
  test -d "$server_dir" || { echo "Missing XiaoZhi source directory: $server_dir" >&2; exit 1; }
  test -f "$server_dir/main/xiaozhi-server/requirements.txt" || {
    echo "Pinned XiaoZhi source is missing main/xiaozhi-server/requirements.txt" >&2
    exit 1
  }
}

ensure_archive_provenance() {
  test -f "$archive_provenance_path" || {
    echo "Missing archive provenance record: $archive_provenance_path" >&2
    exit 1
  }
  if [[ "$(grep -Fxc 'source_method=github_archive' "$archive_provenance_path" || true)" -ne 1 ]]; then
    echo "Archive provenance has an unexpected source method." >&2
    exit 1
  fi
  if [[ "$(grep -Fxc "repository=$XIAOZHI_REPOSITORY" "$archive_provenance_path" || true)" -ne 1 ]]; then
    echo "Archive provenance repository mismatch." >&2
    exit 1
  fi
  if [[ "$(grep -Fxc "revision=$XIAOZHI_REVISION" "$archive_provenance_path" || true)" -ne 1 ]]; then
    echo "Archive provenance revision mismatch." >&2
    exit 1
  fi
  if [[ "$(grep -Fxc "archive_root=$XIAOZHI_ARCHIVE_ROOT" "$archive_provenance_path" || true)" -ne 1 ]]; then
    echo "Archive provenance root mismatch." >&2
    exit 1
  fi
  archive_hash_count="$(grep -Ec '^archive_sha256=[a-f0-9]{64}$' "$archive_provenance_path" || true)"
  archive_hash_key_count="$(grep -Ec '^archive_sha256=' "$archive_provenance_path" || true)"
  if [[ "$archive_hash_count" -ne 1 || "$archive_hash_key_count" -ne 1 ]]; then
    echo "Archive provenance checksum is missing or invalid." >&2
    exit 1
  fi
}

ensure_pinned_source() {
  ensure_source_layout
  if [[ -d "$server_dir/.git" ]]; then
    actual_revision="$(git -C "$server_dir" rev-parse HEAD)"
    if [[ "$actual_revision" != "$XIAOZHI_REVISION" ]]; then
      echo "Source revision mismatch: $actual_revision" >&2
      exit 1
    fi
    return
  fi

  ensure_archive_provenance
}

validate_archive_members() {
  local member member_type
  local -a roots=()

  tar -tzf "$archive_source_path" >/dev/null
  mapfile -t roots < <(tar -tzf "$archive_source_path" | awk -F/ 'NF { print $1 }' | sort -u)
  if [[ "${#roots[@]}" -ne 1 || "${roots[0]}" != "$XIAOZHI_ARCHIVE_ROOT" ]]; then
    echo "Archive must contain exactly the GitHub root $XIAOZHI_ARCHIVE_ROOT/." >&2
    exit 1
  fi
  while IFS= read -r member; do
    [[ -n "$member" ]] || continue
    if [[ "$member" == /* || "$member" == ../* || "$member" == *"/../"* || "$member" == . || "$member" == ./* ]]; then
      echo "Archive contains an unsafe member path: $member" >&2
      exit 1
    fi
    if [[ "$member" == "$XIAOZHI_ARCHIVE_ROOT/.git" || "$member" == "$XIAOZHI_ARCHIVE_ROOT/.git/"* ]]; then
      echo "Archive must not contain a Git working directory: $member" >&2
      exit 1
    fi
  done < <(tar -tzf "$archive_source_path")
  while IFS= read -r member; do
    member_type="${member:0:1}"
    case "$member_type" in
      -|d) ;;
      *)
        echo "Archive contains a non-regular member type: $member" >&2
        exit 1
        ;;
    esac
  done < <(tar -tvzf "$archive_source_path")
}

import_archive_source() {
  local actual_hash stage_dir

  if [[ -e "$server_dir" || -L "$server_dir" ]]; then
    echo "Refusing to replace an existing source path: $server_dir" >&2
    exit 1
  fi
  if [[ -e "$archive_provenance_path" || -L "$archive_provenance_path" ]]; then
    echo "Refusing to replace an existing archive provenance record: $archive_provenance_path" >&2
    exit 1
  fi
  if [[ ! -f "$archive_source_path" ]]; then
    echo "Archive is not a regular file: $archive_source_path" >&2
    exit 1
  fi
  archive_source_path="$(realpath -e -- "$archive_source_path")"
  actual_hash="$(sha256sum -- "$archive_source_path" | awk '{ print $1 }')"
  if [[ "$actual_hash" != "$archive_sha256" ]]; then
    echo "Archive SHA-256 mismatch: $actual_hash" >&2
    exit 1
  fi
  validate_archive_members

  stage_dir="$(mktemp -d "$home_dir/.xiaozhi-source.XXXXXX")"
  tar -xzf "$archive_source_path" -C "$stage_dir" --no-same-owner --no-same-permissions
  test -d "$stage_dir/$XIAOZHI_ARCHIVE_ROOT" || {
    echo "Archive extraction did not produce the expected source root." >&2
    exit 1
  }
  mv -- "$stage_dir/$XIAOZHI_ARCHIVE_ROOT" "$server_dir"
  rmdir -- "$stage_dir"
  ensure_source_layout

  umask 077
  {
    printf 'source_method=github_archive\n'
    printf 'repository=%s\n' "$XIAOZHI_REPOSITORY"
    printf 'revision=%s\n' "$XIAOZHI_REVISION"
    printf 'archive_root=%s\n' "$XIAOZHI_ARCHIVE_ROOT"
    printf 'archive_sha256=%s\n' "$actual_hash"
  } > "$archive_provenance_path"
  chmod 600 "$archive_provenance_path"
  echo "Pinned XiaoZhi archive source ready: $server_dir @ $XIAOZHI_REVISION"
}

if "$clone_source"; then
  mkdir -p "$home_dir"
  if [[ -e "$archive_provenance_path" || -L "$archive_provenance_path" ]]; then
    echo "Refusing to combine Git source with an archive provenance record: $archive_provenance_path" >&2
    exit 1
  fi
  if [[ ! -d "$server_dir/.git" ]]; then
    if [[ -e "$server_dir" || -L "$server_dir" ]]; then
      echo "Refusing to replace a non-Git source path: $server_dir" >&2
      exit 1
    fi
    git clone --filter=blob:none "$XIAOZHI_REPOSITORY" "$server_dir"
  fi
  ensure_clean_source
  git -C "$server_dir" fetch --depth 1 origin "$XIAOZHI_REVISION"
  git -C "$server_dir" checkout --detach "$XIAOZHI_REVISION"
  actual_revision="$(git -C "$server_dir" rev-parse HEAD)"
  if [[ "$actual_revision" != "$XIAOZHI_REVISION" ]]; then
    echo "Pinned XiaoZhi revision mismatch: $actual_revision" >&2
    exit 1
  fi
  echo "Pinned XiaoZhi source ready: $server_dir @ $actual_revision"
fi

if [[ -n "$archive_source_path" ]]; then
  mkdir -p "$home_dir"
  import_archive_source
fi

if "$create_env" || "$install_dependencies" || "$render_config" || "$check"; then
  load_conda
  # Keep the isolated XiaoZhi environment from importing packages installed
  # in the board user's ~/.local site directory.
  export PYTHONNOUSERSITE=1
fi

if "$create_env"; then
  if ! conda env list | awk '{print $1}' | grep -Fxq "$environment_name"; then
    conda create --yes --name "$environment_name" python=3.10
  fi
  conda run --no-capture-output --name "$environment_name" python -c \
    'import sys; assert sys.version_info[:2] == (3, 10); print(sys.executable, sys.version)'
fi

if "$render_config"; then
  test -f "$template_path" || { echo "Missing template: $template_path" >&2; exit 1; }
  ensure_pinned_source
  if ! conda env list | awk '{print $1}' | grep -Fxq "$environment_name"; then
    echo "Missing conda environment $environment_name; run --create-env first." >&2
    exit 1
  fi
  if [[ -z "${CASE9_GATEWAY_API_KEY:-}" ]]; then
    echo "CASE9_GATEWAY_API_KEY is required to render the board-local secret." >&2
    exit 2
  fi
  if [[ -e "$config_path" && "${CASE9_ALLOW_CONFIG_OVERWRITE:-0}" != "1" ]]; then
    echo "Refusing to overwrite existing XiaoZhi config: $config_path" >&2
    echo "Set CASE9_ALLOW_CONFIG_OVERWRITE=1 only after reviewing it." >&2
    exit 1
  fi
  umask 077
  mkdir -p "$(dirname "$config_path")"
  CASE9_TEMPLATE_PATH="$template_path" CASE9_CONFIG_PATH="$config_path" \
    conda run --no-capture-output --name "$environment_name" python - <<'PY'
import json
import os
from pathlib import Path

template = Path(os.environ["CASE9_TEMPLATE_PATH"])
destination = Path(os.environ["CASE9_CONFIG_PATH"])
token = os.environ["CASE9_GATEWAY_API_KEY"]
if not token.strip() or "\n" in token or "\r" in token:
    raise SystemExit("CASE9_GATEWAY_API_KEY must be a non-empty single-line value")
text = template.read_text(encoding="utf-8")
marker = '"__CASE9_GATEWAY_API_KEY__"'
if text.count(marker) != 1:
    raise SystemExit("configuration template does not contain exactly one key marker")
destination.write_text(text.replace(marker, json.dumps(token)), encoding="utf-8")
PY
  chmod 600 "$config_path"
  echo "Rendered board-local configuration: $config_path"
fi

if "$check"; then
  ensure_pinned_source
  if ! conda env list | awk '{print $1}' | grep -Fxq "$environment_name"; then
    echo "Missing conda environment $environment_name" >&2
    exit 1
  fi
  test -f "$config_path" || { echo "Missing rendered config: $config_path" >&2; exit 1; }
  conda run --no-capture-output --name "$environment_name" python - "$config_path" <<'PY'
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is unavailable; run --install-dependencies first") from exc

config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert config["server"] == {"ip": "0.0.0.0", "port": 8000, "http_port": 8003}
assert config["selected_module"]["LLM"] == "Case9RagLLM"
assert config["selected_module"]["Intent"] == "nointent"
provider = config["LLM"]["Case9RagLLM"]
assert provider["type"] == "openai"
assert provider["base_url"] == "http://127.0.0.1:7861/v1"
assert provider["model_name"] == "case9-rag"
assert provider["api_key"] and provider["api_key"] != "__CASE9_GATEWAY_API_KEY__"
print("Case9 XiaoZhi override validated")
PY
  echo "Pinned revision and rendered configuration validated."
fi
