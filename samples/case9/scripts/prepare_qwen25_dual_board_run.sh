#!/usr/bin/env bash
# Prepare an isolated board-side acceptance root without installing packages.
# Existing model files are referenced explicitly; this script only creates
# small provenance/lock files and refuses to overwrite an existing lock.
set -Eeuo pipefail

ROOT=""
OM=""
SOURCE_OM=""
CONTROLLER_CONTRACT=""
TOKENIZER=""
TOKENIZER_CONFIG=""
SOC_VERSION=""
OM_CONTRACT=""
OM_LOCK=""
TOKENIZER_LOCK=""
REPORTS=""

usage() {
  cat <<'EOF'
Usage: prepare_qwen25_dual_board_run.sh --root DIR --om FILE --source-om FILE \
  --controller-contract FILE --tokenizer FILE --tokenizer-config FILE \
  --soc-version Ascend310B1|Ascend310B4 --om-contract FILE --om-lock FILE \
  --tokenizer-lock FILE --reports DIR

The command creates an isolated directory and provenance locks. It does not
copy or modify model files, install packages, or touch system CANN/OPP.
EOF
}

die() { echo "prepare-qwen25-run: $*" >&2; exit 2; }
while (($#)); do
  case "$1" in
    --root) ROOT="${2:?missing value}"; shift 2 ;;
    --om) OM="${2:?missing value}"; shift 2 ;;
    --source-om) SOURCE_OM="${2:?missing value}"; shift 2 ;;
    --controller-contract) CONTROLLER_CONTRACT="${2:?missing value}"; shift 2 ;;
    --tokenizer) TOKENIZER="${2:?missing value}"; shift 2 ;;
    --tokenizer-config) TOKENIZER_CONFIG="${2:?missing value}"; shift 2 ;;
    --soc-version) SOC_VERSION="${2:?missing value}"; shift 2 ;;
    --om-contract) OM_CONTRACT="${2:?missing value}"; shift 2 ;;
    --om-lock) OM_LOCK="${2:?missing value}"; shift 2 ;;
    --tokenizer-lock) TOKENIZER_LOCK="${2:?missing value}"; shift 2 ;;
    --reports) REPORTS="${2:?missing value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ "$SOC_VERSION" == Ascend310B1 || "$SOC_VERSION" == Ascend310B4 ]] ||
  die "--soc-version must be Ascend310B1 or Ascend310B4"
for path in "$ROOT" "$OM" "$SOURCE_OM" "$CONTROLLER_CONTRACT" "$TOKENIZER" "$TOKENIZER_CONFIG" "$OM_CONTRACT" "$OM_LOCK" "$TOKENIZER_LOCK" "$REPORTS"; do
  [[ "$path" == /* && "$path" != *".."* ]] || die "paths must be absolute and must not contain ..: $path"
done
[[ -d "$ROOT" ]] || mkdir -p "$ROOT"
[[ -f "$OM" ]] || die "OM is missing: $OM"
[[ -f "$CONTROLLER_CONTRACT" ]] || die "controller contract is missing: $CONTROLLER_CONTRACT"
[[ -f "$TOKENIZER" ]] || die "tokenizer is missing: $TOKENIZER"
[[ -f "$TOKENIZER_CONFIG" ]] || die "tokenizer config is missing: $TOKENIZER_CONFIG"
[[ "$OM" != "$SOURCE_OM" || "$SOC_VERSION" == Ascend310B4 ]] ||
  die "a B1 run must use an explicitly selected board OM distinct from source OM"
[[ ! -e "$OM_LOCK" ]] || die "refusing to overwrite existing OM lock: $OM_LOCK"
[[ ! -e "$TOKENIZER_LOCK" ]] || die "refusing to overwrite existing tokenizer lock: $TOKENIZER_LOCK"

mkdir -p "$(dirname "$OM_CONTRACT")" "$(dirname "$OM_LOCK")" "$(dirname "$TOKENIZER_LOCK")" "$REPORTS"
# Candidate roots intentionally reference the verified historical artifacts
# with symlinks.  Lock metadata must describe the target binary, not the
# short link text, otherwise strict startup rejects a valid SHA/size pair.
OM_BYTES="$(stat -Lc '%s' "$OM")"
OM_SHA="$(sha256sum "$OM" | awk '{print $1}')"
TOKENIZER_BYTES="$(stat -Lc '%s' "$TOKENIZER")"
TOKENIZER_SHA="$(sha256sum "$TOKENIZER" | awk '{print $1}')"
CONTROLLER_BYTES="$(stat -Lc '%s' "$CONTROLLER_CONTRACT")"
CONTROLLER_SHA="$(sha256sum "$CONTROLLER_CONTRACT" | awk '{print $1}')"

python - "$TOKENIZER_LOCK" "$TOKENIZER" "$TOKENIZER_BYTES" "$TOKENIZER_SHA" <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

target = Path(sys.argv[1])
artifact = Path(sys.argv[2])
size = int(sys.argv[3])
sha = sys.argv[4]
document = {
    "schema_version": 1,
    "artifact": str(artifact),
    "bytes": size,
    "sha256": sha,
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "source": "prepare_qwen25_dual_board_run.sh",
}
temporary = target.with_name(target.name + ".part")
temporary.write_text(json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, target)
PY

python - "$OM_LOCK" "$OM" "$SOURCE_OM" "$SOC_VERSION" "$OM_BYTES" "$OM_SHA" "$CONTROLLER_CONTRACT" "$CONTROLLER_BYTES" "$CONTROLLER_SHA" <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

target, om, source_om, soc, om_bytes, om_sha, controller, controller_bytes, controller_sha = sys.argv[1:]
document = {
    "schema_version": 2,
    "status": "awaiting_acl_descriptor",
    "model_id": "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om",
    "om_path": om,
    "source_om": source_om,
    "bytes": int(om_bytes),
    "sha256": om_sha,
    "soc_version": soc,
    "chip_tier": "20T" if soc == "Ascend310B1" else "8T",
    "contract_path": controller,
    "contract_sha256": controller_sha,
    "controller_contract_path": controller,
    "controller_contract_bytes": int(controller_bytes),
    "controller_contract_sha256": controller_sha,
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "provenance": "prepare_qwen25_dual_board_run.sh; runtime descriptor is added only after ACL smoke",
}
target_path = Path(target)
temporary = target_path.with_name(target_path.name + ".part")
temporary.write_text(json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, target_path)
PY

python - "$REPORTS/preflight.json" "$ROOT" "$SOC_VERSION" "$OM" "$OM_BYTES" "$OM_SHA" "$TOKENIZER" "$TOKENIZER_BYTES" "$TOKENIZER_SHA" "$CONTROLLER_CONTRACT" "$CONTROLLER_SHA" <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

target = Path(sys.argv[1])
root, soc, om, om_bytes, om_sha, tokenizer, tokenizer_bytes, tokenizer_sha, controller, controller_sha = sys.argv[2:]
document = {
    "schema_version": 1,
    "status": "prepared_for_acl_smoke",
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "root": root,
    "soc_version": soc,
    "artifacts": {
        "om": {"path": om, "bytes": int(om_bytes), "sha256": om_sha},
        "tokenizer": {"path": tokenizer, "bytes": int(tokenizer_bytes), "sha256": tokenizer_sha},
        "controller_contract": {"path": controller, "sha256": controller_sha},
    },
    "next_step": "run provision_qwen25_kv102_board.sh smoke after sourcing the intended conda and CANN environments",
}
temporary = target.with_name(target.name + ".part")
temporary.write_text(json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, target)
PY

echo "prepared root=$ROOT soc=$SOC_VERSION om_bytes=$OM_BYTES om_sha256=$OM_SHA tokenizer_sha256=$TOKENIZER_SHA"
