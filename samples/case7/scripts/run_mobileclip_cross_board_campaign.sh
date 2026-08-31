#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

# Board-only, serial MobileCLIP conversion and ACL evidence harness.
# It performs no SSH/SCP, CPU fallback, production writes, cache, or parallel work.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
CASE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
MODEL_ID="mobileclip_s0__npu__mixed_fp16"
MODE=native
COMPONENTS=all
SOC_VERSION=Ascend310B4
CAMPAIGN_ROOT=
REFERENCE_DIR=
IMAGE_REFERENCE_DIR=
TEXT_REFERENCE_DIR=
OM_DIR=
IMAGE_OM=
TEXT_OM=
ARTIFACT_LABEL=
RUNTIME_LABEL=
RUNTIME_LABEL_EXPLICIT=0
PYTHON_BIN=python
CONDA_SH=/usr/local/miniconda3/etc/profile.d/conda.sh
CANN_SET_ENV=
SKIP_SYSTEM_STATUS=0
DRY_RUN=0
ALLOW_EXISTING=0
CELL_ID=

usage() {
  cat <<'EOF'
Usage: run_mobileclip_cross_board_campaign.sh [options]
  --mode preflight|native|validate|all
                                  preflight captures board evidence only; native converts+validates
  --campaign-root PATH             new/empty evidence root
  --case-root PATH                 Case7 root (default: script parent)
  --soc-version SOC                ATC target (default: Ascend310B4)
  --component image|text|all       component selection (default: all)
  --reference-dir PATH             fixed NPZ directory, optionally with image/text subdirs
  --image-reference-dir PATH       explicit image fixtures
  --text-reference-dir PATH        explicit text fixtures
  --om-dir PATH                    directory containing mobileclip_s0_image/text.om
  --image-om PATH / --text-om PATH explicit supplied OM
  --artifact-label NAME             compiler/source label
  --runtime-label NAME              runtime board label
  --python PATH                     board Python executable
  --conda-sh PATH                   conda.sh path
  --cann-set-env PATH               CANN set_env.sh path
  --skip-system-status              skip status capture (testing only)
  --allow-existing                 allow a pre-staged, non-production campaign directory
  --cell-id NAME                   unique validation cell name (required for matrix reruns)
  --dry-run                         print plan without sourcing or writing
EOF
}
die() { echo "[campaign:error] $*" >&2; exit 2; }
need_arg() { [[ $# -ge 2 ]] || die "missing value for $1"; printf '%s' "$2"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$(need_arg "$@")"; shift 2 ;;
    --campaign-root) CAMPAIGN_ROOT="$(need_arg "$@")"; shift 2 ;;
    --case-root) CASE_ROOT="$(need_arg "$@")"; shift 2 ;;
    --soc-version) SOC_VERSION="$(need_arg "$@")"; shift 2 ;;
    --component) COMPONENTS="$(need_arg "$@")"; shift 2 ;;
    --reference-dir) REFERENCE_DIR="$(need_arg "$@")"; shift 2 ;;
    --image-reference-dir) IMAGE_REFERENCE_DIR="$(need_arg "$@")"; shift 2 ;;
    --text-reference-dir) TEXT_REFERENCE_DIR="$(need_arg "$@")"; shift 2 ;;
    --om-dir) OM_DIR="$(need_arg "$@")"; shift 2 ;;
    --image-om) IMAGE_OM="$(need_arg "$@")"; shift 2 ;;
    --text-om) TEXT_OM="$(need_arg "$@")"; shift 2 ;;
    --artifact-label) ARTIFACT_LABEL="$(need_arg "$@")"; shift 2 ;;
    --runtime-label) RUNTIME_LABEL="$(need_arg "$@")"; RUNTIME_LABEL_EXPLICIT=1; shift 2 ;;
    --python) PYTHON_BIN="$(need_arg "$@")"; shift 2 ;;
    --conda-sh) CONDA_SH="$(need_arg "$@")"; shift 2 ;;
    --cann-set-env) CANN_SET_ENV="$(need_arg "$@")"; shift 2 ;;
    --skip-system-status) SKIP_SYSTEM_STATUS=1; shift ;;
    --allow-existing) ALLOW_EXISTING=1; shift ;;
    --cell-id) CELL_ID="$(need_arg "$@")"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$MODE" in preflight|native|validate|all) ;; *) die "invalid --mode" ;; esac
case "$COMPONENTS" in image|text|all) ;; *) die "invalid --component" ;; esac
[[ "$SOC_VERSION" =~ ^Ascend[[:alnum:]_.-]+$ ]] || die "invalid --soc-version"
[[ -z "$ARTIFACT_LABEL" || "$ARTIFACT_LABEL" =~ ^[[:alnum:]_.-]+$ ]] || die "invalid --artifact-label"
[[ -z "$RUNTIME_LABEL" || "$RUNTIME_LABEL" =~ ^[[:alnum:]_.-]+$ ]] || die "invalid --runtime-label"
[[ -z "$CELL_ID" || "$CELL_ID" =~ ^[[:alnum:]_.-]+$ ]] || die "invalid --cell-id"
[[ "$CELL_ID" != "." && "$CELL_ID" != ".." ]] || die "invalid --cell-id"
case "$ARTIFACT_LABEL" in ""|8t-310b4|20t-310b1) ;; *) die "unsupported compiler role: $ARTIFACT_LABEL" ;; esac
case "$RUNTIME_LABEL" in ""|8t-310b4|20t-310b1) ;; *) die "unsupported runtime role: $RUNTIME_LABEL" ;; esac
[[ -n "$CAMPAIGN_ROOT" ]] || CAMPAIGN_ROOT="/home/HwHiAiUser/Documents/ai-album-mobileclip-compat-$(date +%Y%m%d-%H%M%S)"

# A native run has an unambiguous compiler/runtime role from its requested SoC.
# Validation runs intentionally leave the compiler role unset unless the caller
# supplies --artifact-label, so a cross-board cell can never be guessed.
role_for_soc() {
  case "$1" in
    Ascend310B4) printf '%s' '8t-310b4' ;;
    Ascend310B1) printf '%s' '20t-310b1' ;;
    *) printf '%s' '' ;;
  esac
}
soc_for_role() {
  case "$1" in
    8t-310b4) printf '%s' 'Ascend310B4' ;;
    20t-310b1) printf '%s' 'Ascend310B1' ;;
    *) printf '%s' '' ;;
  esac
}
if [[ -z "$RUNTIME_LABEL" ]]; then RUNTIME_LABEL="$(role_for_soc "$SOC_VERSION")"; fi
if [[ "$MODE" == native && -z "$ARTIFACT_LABEL" ]]; then ARTIFACT_LABEL="$RUNTIME_LABEL"; fi

resolve_path() {
  if [[ "$1" = /* ]]; then realpath -m -- "$1"; else realpath -m -- "$CASE_ROOT/$1"; fi
}
CASE_ROOT="$(resolve_path "$CASE_ROOT")"
CAMPAIGN_ROOT="$(resolve_path "$CAMPAIGN_ROOT")"
[[ -d "$CASE_ROOT" ]] || die "case root does not exist: $CASE_ROOT"
inside() { [[ "$1" == "$2" || "$1" == "$2"/* ]]; }
reject_production() {
  local path="$(realpath -m -- "$1")" root
  for root in "$CASE_ROOT/models/om" "$CASE_ROOT/models/registry.json" "$CASE_ROOT/data" "$CASE_ROOT/photos" "$CASE_ROOT/reports/model_pipeline" "$CASE_ROOT/reports/precision_sweep" "/home/HwHiAiUser/Documents/ai-album"; do
    root="$(realpath -m -- "$root")"
    inside "$path" "$root" && die "refusing production/active path: $path"
  done
  return 0
}
reject_production "$CAMPAIGN_ROOT"
CAMPAIGN_STARTED_AT="$(date --iso-8601=seconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
if [[ "$DRY_RUN" != 1 ]]; then
  [[ ! -e "$CAMPAIGN_ROOT" || -d "$CAMPAIGN_ROOT" ]] || die "campaign root is not a directory"
  if [[ "$ALLOW_EXISTING" != 1 && -d "$CAMPAIGN_ROOT" && -n "$(find -P "$CAMPAIGN_ROOT" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
    die "campaign root must be new or empty (or pass --allow-existing for a verified pre-staged root)"
  fi
fi
[[ "$MODE" != validate || -n "$OM_DIR$IMAGE_OM$TEXT_OM" ]] || die "validate mode requires --om-dir or explicit OM"
if [[ "$MODE" == all ]]; then
  if [[ -n "$OM_DIR$IMAGE_OM$TEXT_OM" ]]; then MODE=validate; else MODE=native; fi
fi
if [[ "$MODE" == native && -z "$ARTIFACT_LABEL" ]]; then ARTIFACT_LABEL="$RUNTIME_LABEL"; fi
if [[ "$MODE" == native && -z "$RUNTIME_LABEL" ]]; then
  die "native mode requires a supported Ascend310B1 or Ascend310B4 SoC"
fi
if [[ "$MODE" == validate && -z "$ARTIFACT_LABEL" ]]; then
  die "validate mode requires --artifact-label so cross-board compiler role is explicit"
fi
if [[ "$MODE" == validate && "$RUNTIME_LABEL_EXPLICIT" != 1 ]]; then
  die "validate mode requires --runtime-label so runtime board role is explicit"
fi

reference_for() {
  local component="$1" candidate
  if [[ "$component" == image && -n "$IMAGE_REFERENCE_DIR" ]]; then candidate="$IMAGE_REFERENCE_DIR"
  elif [[ "$component" == text && -n "$TEXT_REFERENCE_DIR" ]]; then candidate="$TEXT_REFERENCE_DIR"
  elif [[ -n "$REFERENCE_DIR" ]]; then
    if [[ -d "$(resolve_path "$REFERENCE_DIR/$component")" ]]; then candidate="$REFERENCE_DIR/$component"
    elif [[ -d "$(resolve_path "$REFERENCE_DIR/$component-references")" ]]; then candidate="$REFERENCE_DIR/$component-references"
    else candidate="$REFERENCE_DIR"; fi
  else candidate="$CASE_ROOT/reports/model_pipeline/references"; fi
  if [[ -d "$(resolve_path "$candidate/$component")" ]]; then
    candidate="$candidate/$component"
  elif [[ -d "$(resolve_path "$candidate/$component-references")" ]]; then
    candidate="$candidate/$component-references"
  fi
  resolve_path "$candidate"
}
om_for() {
  local component="$1" candidate
  if [[ "$component" == image && -n "$IMAGE_OM" ]]; then candidate="$IMAGE_OM"
  elif [[ "$component" == text && -n "$TEXT_OM" ]]; then candidate="$TEXT_OM"
  elif [[ -n "$OM_DIR" ]]; then candidate="$OM_DIR/mobileclip_s0_$component.om"
  else candidate="$CAMPAIGN_ROOT/native/om/mobileclip_s0_$component.om"; fi
  [[ "$candidate" = /* ]] && realpath -m -- "$candidate" || realpath -m -- "$CASE_ROOT/$candidate"
}

if [[ "$DRY_RUN" == 1 ]]; then
  echo "campaign_root=$CAMPAIGN_ROOT"
  echo "case_root=$CASE_ROOT"
  echo "mode=$MODE soc_version=$SOC_VERSION components=$COMPONENTS"
  echo "serial_atc=1 compiler_cache=disabled swap_required=0 cpu_fallback=0"
  for component in image text; do
    if [[ "$COMPONENTS" == all || "$COMPONENTS" == "$component" ]]; then
      echo "component=$component reference=$(reference_for "$component") om=$(om_for "$component")"
    fi
  done
  exit 0
fi

mkdir -p "$CAMPAIGN_ROOT/stages" "$CAMPAIGN_ROOT/native/om" "$CAMPAIGN_ROOT/native/reports" "$CAMPAIGN_ROOT/validation" "$CAMPAIGN_ROOT/environment"
if [[ "$CONDA_SH" != /* ]]; then CONDA_SH="$(resolve_path "$CONDA_SH")"; fi
if [[ ! -f "$CONDA_SH" ]]; then
  for candidate in \
    /usr/local/miniconda3/etc/profile.d/conda.sh \
    /home/HwHiAiUser/miniconda3/etc/profile.d/conda.sh \
    /opt/conda/etc/profile.d/conda.sh; do
    if [[ -f "$candidate" ]]; then CONDA_SH="$candidate"; break; fi
  done
fi
[[ -f "$CONDA_SH" ]] || die "conda.sh not found; pass --conda-sh explicitly"
set +u
# shellcheck disable=SC1090
source "$CONDA_SH" || die "failed to source conda.sh: $CONDA_SH"
command -v conda >/dev/null 2>&1 || die "conda command unavailable after sourcing $CONDA_SH"
conda activate base || die "failed to activate conda environment base"
set -u
if [[ -z "$CANN_SET_ENV" ]]; then
  toolkit_hint="${ASCEND_TOOLKIT_HOME:-${ASCEND_HOME_PATH:-}}"
  for candidate in \
    "${toolkit_hint:+$toolkit_hint/set_env.sh}" \
    /usr/local/Ascend/ascend-toolkit/set_env.sh \
    /usr/local/Ascend/ascend-toolkit/latest/set_env.sh \
    /home/HwHiAiUser/Ascend/ascend-toolkit/set_env.sh \
    /home/HwHiAiUser/Ascend/ascend-toolkit/latest/set_env.sh; do
    [[ -n "$candidate" ]] || continue
    [[ -f "$candidate" ]] && CANN_SET_ENV="$candidate" && break
  done
fi
[[ -n "$CANN_SET_ENV" && -f "$CANN_SET_ENV" ]] || die "CANN set_env.sh not found"
set +u
# shellcheck disable=SC1090
source "$CANN_SET_ENV" || die "failed to source CANN environment: $CANN_SET_ENV"
set -u
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python unavailable: $PYTHON_BIN"
if [[ "$MODE" == native ]]; then
  command -v atc >/dev/null 2>&1 || die "atc unavailable after CANN setup"
fi
command -v npu-smi >/dev/null 2>&1 || die "npu-smi unavailable"

# prepare_models.py emits the complete ATC contract for each component:
# --framework=5, --input_shape=image:1,3,256,256 or text:1,77,
# --precision_mode=allow_fp32_to_fp16, --op_select_implmode=high_precision_for_all,
# --enable_graph_parallel=0, and --op_compiler_cache_mode=disable.  Keep the
# policy marker here so board evidence remains readable without re-running ATC.
export MAX_COMPILE_CORE_NUMBER=1 MULTI_THREAD_COMPILE=0 TBE_PARALLEL_COMPILER=0
export TE_PARALLEL_COMPILER=1 ASCENDC_PAR_COMPILE_JOB=0 TILINGKEY_PAR_COMPILE=0
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export CMAKE_BUILD_PARALLEL_LEVEL=1 MAKEFLAGS=-j1 VECLIB_MAXIMUM_THREADS=1 BLIS_NUM_THREADS=1 GOMP_NUM_THREADS=1
export PYTHONHASHSEED=0
unset TE_COMPILE_CACHE_PATH TBE_IMPL_CACHE_PATH ASCEND_OPP_CACHE_PATH ASCEND_CACHE_PATH 2>/dev/null || true
SWAP_BYTES="$(awk 'NR > 1 { total += $3 } END { print total + 0 }' /proc/swaps 2>/dev/null || echo 0)"
[[ "$SWAP_BYTES" == 0 ]] || die "swap is enabled ($SWAP_BYTES bytes); no swap is allowed"

cat >"$CAMPAIGN_ROOT/environment/compile-policy.txt" <<EOF
policy=serial-no-cache-no-swap
atc_framework=5
atc_soc_version=$SOC_VERSION
atc_precision_mode=allow_fp32_to_fp16
atc_op_select_implmode=high_precision_for_all
atc_enable_graph_parallel=0
atc_op_compiler_cache_mode=disable
input_contract_image=image:1,3,256,256
input_contract_text=text:1,77
MAX_COMPILE_CORE_NUMBER=$MAX_COMPILE_CORE_NUMBER
MULTI_THREAD_COMPILE=$MULTI_THREAD_COMPILE
TBE_PARALLEL_COMPILER=$TBE_PARALLEL_COMPILER
TE_PARALLEL_COMPILER=$TE_PARALLEL_COMPILER
ASCENDC_PAR_COMPILE_JOB=$ASCENDC_PAR_COMPILE_JOB
TILINGKEY_PAR_COMPILE=$TILINGKEY_PAR_COMPILE
OMP_NUM_THREADS=$OMP_NUM_THREADS
OPENBLAS_NUM_THREADS=$OPENBLAS_NUM_THREADS
MKL_NUM_THREADS=$MKL_NUM_THREADS
NUMEXPR_NUM_THREADS=$NUMEXPR_NUM_THREADS
CMAKE_BUILD_PARALLEL_LEVEL=$CMAKE_BUILD_PARALLEL_LEVEL
MAKEFLAGS=$MAKEFLAGS
swap_bytes=$SWAP_BYTES
cpu_fallback=false
EOF

STATUS_TXT="$CAMPAIGN_ROOT/environment/system-status.txt"
VERSION_LIST="$CAMPAIGN_ROOT/environment/version-files.list"
if [[ "$SKIP_SYSTEM_STATUS" != 1 ]]; then
  {
    echo "captured_at=$(date --iso-8601=seconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "hostname=$(hostname 2>/dev/null || true)"; echo "whoami=$(whoami 2>/dev/null || true)"; echo "uname=$(uname -a 2>/dev/null || true)"
    echo "swap_bytes=$SWAP_BYTES"; echo "--- free -b ---"; free -b 2>&1 || true; echo "--- df -h /home ---"; df -h /home 2>&1 || true
    echo "--- npu-smi info ---"; npu-smi info 2>&1 || true; echo "--- npu-smi board ---"; npu-smi info -t board -i 0 2>&1 || true
    echo "--- atc --version ---"; atc --version 2>&1 || true; echo "--- python --version ---"; "$PYTHON_BIN" --version 2>&1 || true
    echo "--- acl import ---"; "$PYTHON_BIN" -c 'import acl; print(acl.__file__)' 2>&1 || true
    echo "--- driver version.info ---"; [[ -f /var/davinci/driver/version.info ]] && cat /var/davinci/driver/version.info || echo MISSING
    echo "--- toolkit version files ---"
    TOOLKIT_ROOT="$(printenv ASCEND_TOOLKIT_HOME 2>/dev/null || true)"
    [[ -d "$TOOLKIT_ROOT" ]] || TOOLKIT_ROOT=/usr/local/Ascend/ascend-toolkit/latest
    find -P "$TOOLKIT_ROOT" -maxdepth 5 -type f \( -name version.cfg -o -name version.info \) -print 2>/dev/null | sort -u >"$VERSION_LIST" || true
    while IFS= read -r version_file; do echo "### $version_file"; cat "$version_file" 2>&1 || true; done <"$VERSION_LIST"
  } >"$STATUS_TXT" 2>&1
else
  : >"$STATUS_TXT"; : >"$VERSION_LIST"
fi

"$PYTHON_BIN" - "$CAMPAIGN_ROOT/environment/environment.json" "$STATUS_TXT" "$VERSION_LIST" "$SOC_VERSION" "$RUNTIME_LABEL" "$ARTIFACT_LABEL" "$CAMPAIGN_ROOT" <<'PY'
import hashlib,json,re,sys
from pathlib import Path
target,status_file,list_file,requested,label,compiler_label,campaign_root=sys.argv[1:]
status=Path(status_file).read_text(encoding="utf-8",errors="replace")
versions={}
for raw in Path(list_file).read_text(encoding="utf-8",errors="replace").splitlines():
    if raw.strip():
        try: versions[raw]=Path(raw).read_text(encoding="utf-8",errors="replace")
        except OSError as exc: versions[raw]="ERROR: "+str(exc)
def find(pattern,default="unknown"):
    m=re.search(pattern,status,re.I|re.M); return m.group(1).strip() if m else default
firmware=find(r"Firmware\s+Version\s*[:=]\s*([^\s|]+)")
if firmware.lower() in {"na","n/a","none"}: firmware="NA"
soc=find(r"Ascend\s*([0-9]+B[0-9]+)")
if soc=="unknown":
    table=re.search(r"\|\s*\d+\s+(310B\d+)\s+\|",status,re.I)
    if table: soc="Ascend"+table.group(1).upper()
if soc!="unknown" and not soc.lower().startswith("ascend"): soc="Ascend"+soc
cann_software=find(r"Software\s+Version\s*[:=]\s*([^\s|]+)")
compatibility=find(r"Compatibility\s*[:=]\s*([^\s|]+)")
if compatibility=="unknown": compatibility="not_reported_by_npu-smi"
driver_marker="--- driver version.info ---"
driver_text=status.split(driver_marker,1)[-1].split("--- toolkit version files ---",1)[0].strip() if driver_marker in status else ""
cann=[]
component_versions={}
for value in versions.values():
    cann += re.findall(r"(?:toolkit|compiler)_running_version\s*=\s*\[([^\]]+)",value,re.I)
    for key, version in re.findall(r"^([A-Za-z0-9_]+)_running_version\s*=\s*\[([^\]]+)\]", value, re.I|re.M):
        component_versions[key]=version
health=find(r"\|\s*0\s+310B\d+\s+\|\s*([^|\s]+)", "unknown")
atc_version=find(r"(?:ATC|atc)[^\n]*?(?:Version|version)\s*[:=]\s*([^\s|]+)", "unknown")
python_version=find(r"Python\s+([0-9][^\s]+)", "unknown")
driver_components={}
for key in ("ascendhal_version", "tsfw_version", "Innerversion"):
    driver_components[key]=find(r"^\s*"+re.escape(key)+r"\s*[:=]\s*([^\s]+)", "unknown")
payload={"schema_version":1,"campaign_id":Path(campaign_root).name,"role":label or None,
         "compiler_role":compiler_label or None,"runtime_role":label or None,
         "artifact_label":compiler_label or None,
         "soc_version_requested":requested,"soc_detected":soc,"npu_model":soc,
         "runtime_label":label or None,
         "npu_smi":{"software_version":cann_software,"compatibility":compatibility,
                    "firmware_version_reported":firmware,"firmware_version_raw":firmware,
                    "health":health,
                    "raw_status_sha256":hashlib.sha256(status.encode()).hexdigest()},
         "cann":{"versions_detected":sorted(set(cann)),"component_versions":component_versions,
                  "atc_version":atc_version,"python_version":python_version,"version_files":versions},
         "driver_version_file":"/var/davinci/driver/version.info","driver_version_info":driver_text,
         "driver_version_sha256":hashlib.sha256(driver_text.encode()).hexdigest() if driver_text else None,
         "driver_components":driver_components,"swap_policy":{"required_total_bytes":0},
         "raw_status":str(Path(status_file)),"firmware_version_raw":firmware,
         "firmware_note":"NA is preserved; no firmware value is inferred.",
         "production_mutation":False}
Path(target).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY

if [[ "$MODE" != preflight ]]; then
  # npu-smi versions differ: some print `Ascend310B4`, others only `310B4`
  # in the table. Parse both forms before allowing ATC or ACL validation.
  detected_soc="$(grep -Eo 'Ascend[[:space:]]*[0-9]+B[0-9]+|[|[:space:]][0-9]+B[0-9]+[[:space:]]*[|]' "$STATUS_TXT" 2>/dev/null | head -n 1 | tr -d '[:space:]|' | sed 's/^\([0-9].*\)$/Ascend\1/' || true)"
  if [[ -z "$detected_soc" ]]; then
    die "npu-smi did not report a supported Ascend310B1/Ascend310B4 SoC; refusing ATC/ACL validation"
  fi
  expected_runtime_soc="$SOC_VERSION"
  if [[ "$MODE" == validate ]]; then
    expected_runtime_soc="$(soc_for_role "$RUNTIME_LABEL")"
    [[ -n "$expected_runtime_soc" ]] || die "unsupported runtime role: $RUNTIME_LABEL"
  fi
  if [[ "$detected_soc" != "$expected_runtime_soc" ]]; then
    if [[ "$MODE" == native ]]; then
      die "requested $SOC_VERSION but npu-smi reports $detected_soc; refusing wrong-SoC ATC"
    fi
    die "runtime role $RUNTIME_LABEL requires $expected_runtime_soc but npu-smi reports $detected_soc; refusing cross-board ACL validation"
  fi
fi

"$PYTHON_BIN" -c 'import acl' >"$CAMPAIGN_ROOT/environment/acl-import.log" 2>&1 || { echo "[campaign:error] PyACL import failed; CPU fallback is forbidden" >&2; exit 1; }

if [[ "$MODE" != preflight ]]; then
REFERENCE_DIR_VALUE="$REFERENCE_DIR" IMAGE_REFERENCE_DIR_VALUE="$IMAGE_REFERENCE_DIR" TEXT_REFERENCE_DIR_VALUE="$TEXT_REFERENCE_DIR" \
"$PYTHON_BIN" - "$CASE_ROOT" "$CAMPAIGN_ROOT/environment/inputs.json" "$COMPONENTS" <<'PY'
import hashlib,json,os,sys
from pathlib import Path
case_root,target,selected=Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve(),sys.argv[3]
components=["image","text"] if selected=="all" else [selected]
manifest=json.loads((case_root/"candidate_manifest.json").read_text(encoding="utf-8"))
record=next((x for x in manifest.get("models",[]) if x.get("model_id")=="mobileclip_s0__npu__mixed_fp16"),None)
if not isinstance(record,dict): raise SystemExit("MobileCLIP candidate is absent")
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()
def resolve(value):
    p=Path(str(value)); return p if p.is_absolute() else (case_root/p).resolve()
def refs(component):
    explicit=os.environ.get(component.upper()+"_REFERENCE_DIR_VALUE"); common=os.environ.get("REFERENCE_DIR_VALUE")
    options=[resolve(explicit)] if explicit else []
    if common:
        p=resolve(common); options += [p/component,p/(component+"-references"),p]
    else: options += [case_root/"reports"/"model_pipeline"/"references"]
    expected=36 if component=="image" else 20
    # A reference directory can also contain the one-sample canonical NPZ.
    # Select the immutable fixed fixture set explicitly so validate-candidate
    # cannot accidentally execute 37 image or 21 text samples.
    prefix="mobileclip_s0__npu__mixed_fp16__"+component
    patterns=(
        [prefix+"__sample-*.npz", prefix+"__seed-*.npz"]
        if component=="image" else [prefix+"__query-*.npz"]
    )
    for directory in options:
        if not directory.is_dir(): continue
        files=[]
        for pattern in patterns: files.extend(directory.glob(pattern))
        files=sorted(set(files))
        if len(files)==expected: return directory,files
        # Permit an already-pruned directory using the broad contract name.
        broad=sorted(directory.glob(prefix+"*.npz"))
        if len(files)==0 and len(broad)==expected: return directory,broad
    directory=options[0]
    count=len(list(directory.glob(prefix+"*.npz"))) if directory.is_dir() else 0
    raise SystemExit(f"{component} reference count {count}, expected {expected}")
out={"schema_version":1,"model_id":record["model_id"],"components":{},"staging_root":str(target.parent.parent/"references")}
staging_root=target.parent.parent/"references"
for component in components:
    info=record.get("components",{}).get(component)
    if not isinstance(info,dict): raise SystemExit("missing "+component+" contract")
    onnx=resolve(info["onnx"])
    if not onnx.is_file(): raise SystemExit("missing ONNX: "+str(onnx))
    actual=digest(onnx); declared=str(info.get("onnx_sha256") or "")
    if declared and actual.lower()!=declared.lower(): raise SystemExit(component+" ONNX SHA-256 mismatch: "+actual)
    directory,files=refs(component)
    staged=staging_root/component
    staged.mkdir(parents=True,exist_ok=True)
    staged_files=[]
    for source in files:
        destination=staged/source.name
        source_hash=digest(source)
        if destination.exists() or destination.is_symlink():
            if not destination.is_symlink() or not destination.exists() or digest(destination)!=source_hash:
                raise SystemExit("reference staging collision: "+str(destination))
        else:
            try:
                destination.symlink_to(os.path.relpath(source,destination.parent))
            except OSError:
                # Some mounted filesystems disallow symlinks.  A byte-for-byte
                # fixture copy is still isolated evidence, not a photo cache.
                import shutil
                shutil.copyfile(source,destination)
            if digest(destination)!=source_hash:
                raise SystemExit("reference staging hash mismatch: "+str(destination))
        staged_files.append(destination)
    out["components"][component]={"onnx":str(onnx),"onnx_size":onnx.stat().st_size,
      "onnx_sha256":actual,"onnx_declared_sha256":declared or None,
      "input_name":info.get("input_name"),"input_shape":info.get("input_shape"),"input_dtype":info.get("input_dtype"),
      "output_dtype":info.get("output_dtype"),"embedding_dim":record.get("embedding_dim"),"source_reference_dir":str(directory.resolve()),
      "reference_dir":str(staged.resolve()),"reference_count":len(staged_files),
      "references":[{"path":str(p.resolve()),"source_path":str(files[i].resolve()),"size":p.stat().st_size,"sha256":digest(p)} for i,p in enumerate(staged_files)]}
target.parent.mkdir(parents=True,exist_ok=True); target.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY
else
  "$PYTHON_BIN" - "$CAMPAIGN_ROOT/environment/inputs.json" "$MODEL_ID" "$COMPONENTS" <<'PY'
import json, sys
from pathlib import Path
target, model_id, selected = sys.argv[1:]
Path(target).write_text(json.dumps({
    "schema_version": 1,
    "model_id": model_id,
    "components": {},
    "reference_count": {},
    "note": "preflight mode does not inspect model or reference assets",
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
fi

write_stage() {
  local out="$1" kind="$2" component="$3" status="$4" classification="$5" rc="$6" log="$7" om="$8" report="$9"
  "$PYTHON_BIN" - "$out" "$kind" "$component" "$status" "$classification" "$rc" "$log" "$om" "$report" "$ARTIFACT_LABEL" "$RUNTIME_LABEL" "$MODE" <<'PY'
import hashlib,json,sys
from pathlib import Path
out,kind,component,status,classification,rc,log,om,report,artifact_label,runtime_label,mode=sys.argv[1:]
def digest(value):
    p=Path(value)
    if not p.is_file(): return None
    h=hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()
value={"schema_version":1,"stage_kind":kind,"component":component,"status":status,"classification":classification,
       "exit_code":int(rc),"command_log":log,"artifact_label":artifact_label or None,"runtime_label":runtime_label or None,
       "compiler_role":artifact_label or None,"runtime_role":runtime_label or None,
       "compiler_board":artifact_label or None,"runtime_board":runtime_label or None,
       "mode":mode,"model_id":"mobileclip_s0__npu__mixed_fp16",
       "production_mutation":False}
if kind == "conversion":
    value["atc_log"]=log
    value["atc_command_file"]=str(Path(report).parent / "atc_command.txt") if report else None
if om: value["om"]={"path":om,"exists":Path(om).is_file(),"size":Path(om).stat().st_size if Path(om).is_file() else None,"sha256":digest(om)}
if report: value["report"]={"path":report,"exists":Path(report).is_file(),"sha256":digest(report)}
if report and Path(report).is_file():
    try:
        raw=json.loads(Path(report).read_text(encoding="utf-8"))
    except Exception:
        raw={}
    refs=raw.get("references",[]) if isinstance(raw,dict) else []
    if kind == "validation":
        cosines=[float(item["cosine_similarity"]) for item in refs if isinstance(item,dict) and item.get("cosine_similarity") is not None]
        value["acl_status"]="passed" if raw.get("passed") is True else "failed"
        value["sample_count"]=len(refs)
        value["passed_count"]=sum(1 for item in refs if isinstance(item,dict) and item.get("passed") is True)
        value["min_cosine"]=min(cosines) if cosines else None
        value["max_cosine"]=max(cosines) if cosines else None
        value["fixture_expected"]=36 if component == "image" else 20
        value["threshold"]=raw.get("threshold", 0.995)
        value["failure_class"]=None if raw.get("passed") is True else classification
    elif kind == "conversion":
        components=raw.get("models",{}).get("mobileclip_s0__npu__mixed_fp16",{}).get("components",{})
        detail=components.get(component,{}) if isinstance(components,dict) else {}
        value["atc_command"]=detail.get("command")
        value["atc_log_reported"]=detail.get("log")
        value["cann_version"]=raw.get("cann_version")
        value["atc_status"]=detail.get("status")
        for field in ("onnx", "onnx_size", "onnx_sha256", "om", "om_sha256", "om_size",
                      "precision_mode", "parallel_option", "parallel_policy",
                      "keep_dtype", "keep_dtype_sha256"):
            if field in detail:
                value[field]=detail[field]
Path(out).write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY
}
classify_validation() {
  "$PYTHON_BIN" - "$1" <<'PY'
import json,sys
from pathlib import Path
try: value=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception: print("execute_failed"); raise SystemExit
if value.get("passed") is True: print("passed"); raise SystemExit
error=str(value.get("error","")).lower()
if any(x in error for x in ("dtype","bytes","output is","returned ","contract")): print("output_contract_mismatch"); raise SystemExit
refs=value.get("references",[])
if any(x.get("finite") is False for x in refs if isinstance(x,dict)): print("non_finite"); raise SystemExit
if any(x.get("cosine_similarity") is not None and float(x["cosine_similarity"]) < .995 for x in refs if isinstance(x,dict)): print("numerical_mismatch"); raise SystemExit
if any(x in error for x in ("acl","model","device","init","load","om")): print("load_rejected")
else: print("execute_failed")
PY
}
write_atc_evidence() {
  local report="$1" log="$2" out_dir="$3" stage="${4:-}"
  # Keep the compiler's own command and log beside the structured report.  The
  # report is authoritative; the wrapper log is retained even when ATC fails.
  cp -f -- "$log" "$out_dir/atc.log" 2>/dev/null || true
  if [[ -f "$report" ]]; then
    "$PYTHON_BIN" - "$report" "$out_dir/atc_command.txt" <<'PY' || true
import json, shlex, sys
from pathlib import Path
report, target = map(Path, sys.argv[1:])
command = None
try:
    payload = json.loads(report.read_text(encoding="utf-8"))
    components = payload.get("models", {}).get(
        "mobileclip_s0__npu__mixed_fp16", {}
    ).get("components", {})
    for detail in components.values():
        if isinstance(detail, dict) and detail.get("command"):
            command = detail["command"]
            break
except (OSError, ValueError, TypeError):
    pass
if isinstance(command, list):
    # shlex.join is unavailable on a few board Python 3.7 images.
    def quote(value):
        return shlex.quote(str(value))
    text = " ".join(quote(value) for value in command)
else:
    text = "ATC command unavailable; inspect atc_conversion.json and atc.log"
target.write_text(text + "\n", encoding="utf-8")
PY
  else
    printf '%s\n' "ATC report was not generated; inspect wrapper log" >"$out_dir/atc_command.txt"
  fi
  if [[ -n "$stage" && -f "$stage" ]]; then
    "$PYTHON_BIN" - "$stage" "$out_dir/atc.log" "$out_dir/atc_command.txt" <<'PY' || true
import hashlib, json, sys
from pathlib import Path
stage, log, command = map(Path, sys.argv[1:])
def digest(path):
    if not path.is_file():
        return None
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()
try:
    payload = json.loads(stage.read_text(encoding="utf-8"))
    payload["atc_log"] = str(log)
    payload["atc_log_sha256"] = digest(log)
    payload["atc_command_file"] = str(command)
    payload["atc_command_sha256"] = digest(command)
    stage.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
except (OSError, ValueError):
    pass
PY
  fi
}
write_preflight_stage() {
  local out="$CAMPAIGN_ROOT/stages/preflight.json"
  "$PYTHON_BIN" - "$out" "$STATUS_TXT" "$CAMPAIGN_ROOT/environment/inputs.json" "$CAMPAIGN_ROOT/environment/acl-import.log" "$SOC_VERSION" "$RUNTIME_LABEL" <<'PY'
import json, sys
from pathlib import Path
target, status, inputs, acl_log, soc, role = sys.argv[1:]
payload = {
    "schema_version": 1,
    "stage_kind": "preflight",
    "component": "all",
    "mode": "preflight",
    "status": "passed",
    "classification": "passed",
    "exit_code": 0,
    "soc_version_requested": soc,
    "runtime_role": role or None,
    "environment_status": status,
    "inputs_manifest": inputs,
    "acl_import_log": acl_log,
    "serial": True,
    "compiler_cache": "disabled",
    "swap_required_total_bytes": 0,
    "cpu_fallback": False,
    "production_mutation": False,
}
target=Path(target)
target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}
annotate_conversion_report() {
  local report="$1" component="$2" status="$3" classification="$4" rc="$5" log="$6" om="$7"
  [[ -f "$report" ]] || return 0
  "$PYTHON_BIN" - "$report" "$component" "$status" "$classification" "$rc" "$log" "$om" "$ARTIFACT_LABEL" "$RUNTIME_LABEL" <<'PY' || true
import hashlib, json, sys
from pathlib import Path
report, component, status, classification, rc, log, om, compiler, runtime = sys.argv[1:]
path = Path(report)
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    payload = {}
payload.update({
    "schema_version": 1,
    "model_id": "mobileclip_s0__npu__mixed_fp16",
    "component": component,
    "compiler_role": compiler or None,
    "runtime_role": runtime or None,
    "status": status,
    "classification": classification,
    "exit_code": int(rc),
    "command_log": log,
    "atc_log": log,
    "production_mutation": False,
})
if om:
    candidate = Path(om)
    payload["om_path"] = str(candidate)
    payload["om_exists"] = candidate.is_file()
    if candidate.is_file():
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        payload["om_size"] = candidate.stat().st_size
        payload["om_sha256"] = digest.hexdigest()
detail = payload.get("models", {}).get(
    "mobileclip_s0__npu__mixed_fp16", {}
).get("components", {}).get(component, {})
if isinstance(detail, dict) and detail.get("onnx"):
    onnx_path = Path(str(detail["onnx"]))
    if onnx_path.is_file():
        detail["onnx_size"] = onnx_path.stat().st_size
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}
run_conversion() {
  local component="$1" out="$CAMPAIGN_ROOT/native/om" report_dir="$CAMPAIGN_ROOT/native/reports/$1"
  local log="$CAMPAIGN_ROOT/stages/native-convert-$component.log" stage="$CAMPAIGN_ROOT/stages/native-convert-$component.json"
  local om="$out/mobileclip_s0_$component.om" report="$report_dir/atc_conversion.json"
  mkdir -p "$report_dir"
  if [[ "$ALLOW_EXISTING" == 1 && ( -e "$om" || -e "$report" ) ]]; then
    die "native conversion refuses pre-existing OM/report; use a fresh campaign root"
  fi
  set +e
  {
    echo "command: $PYTHON_BIN $CASE_ROOT/prepare_models.py convert --model $MODEL_ID --component $component --soc-version $SOC_VERSION --precision-mode allow_fp32_to_fp16 --op-select-implmode high_precision_for_all --enable-graph-parallel 0 --without-keep-dtype --allow-low-memory-single-thread --output-om-dir $out --report-dir $report_dir"
    "$PYTHON_BIN" "$CASE_ROOT/prepare_models.py" convert --model "$MODEL_ID" --component "$component" --soc-version "$SOC_VERSION" --precision-mode allow_fp32_to_fp16 --op-select-implmode high_precision_for_all --enable-graph-parallel 0 --without-keep-dtype --allow-low-memory-single-thread --output-om-dir "$out" --report-dir "$report_dir"
  } >"$log" 2>&1
  local rc=$?
  set -e
  local status=failed classification=conversion_failed
  local report_ok=0
  if [[ -f "$report" && -f "$om" ]]; then
    report_ok="$("$PYTHON_BIN" - "$report" "$om" <<'PY'
import hashlib, json, sys
from pathlib import Path
report, om = map(Path, sys.argv[1:])
try:
    payload = json.loads(report.read_text(encoding="utf-8"))
    detail = payload.get("models", {}).get(
        "mobileclip_s0__npu__mixed_fp16", {}
    ).get("components", {})
    detail = next((item for item in detail.values() if isinstance(item, dict)), {})
    digest = hashlib.sha256()
    with om.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    print("1" if detail.get("status") == "passed" and
          (not detail.get("om_sha256") or detail.get("om_sha256").lower() == digest.hexdigest()) else "0")
except (OSError, ValueError, TypeError):
    print("0")
PY
    )" || report_ok=0
  fi
  if [[ "$rc" == 0 && "$report_ok" == 1 ]]; then status=passed; classification=passed
  elif [[ "$rc" == 0 ]]; then classification=execute_failed; fi
  annotate_conversion_report "$report" "$component" "$status" "$classification" "$rc" "$log" "$om"
  write_stage "$stage" conversion "$component" "$status" "$classification" "$rc" "$log" "$om" "$report"
  write_atc_evidence "$report" "$log" "$report_dir" "$stage"
  [[ "$status" == passed ]]
}
run_validation() {
  local component="$1" om="$2" refs="$3"
  local suffix="${CELL_ID:+-$CELL_ID}"
  local report="$CAMPAIGN_ROOT/validation/$component-acl${suffix}.json" log="$CAMPAIGN_ROOT/stages/validate-$component${suffix}.log" stage="$CAMPAIGN_ROOT/stages/validate-$component${suffix}.json"
  reject_production "$om"
  if [[ ! -f "$om" ]]; then write_stage "$stage" validation "$component" failed load_rejected 2 "$log" "$om" "$report"; return 1; fi
  set +e
  {
    echo "command: $PYTHON_BIN $CASE_ROOT/prepare_models.py validate-candidate --model $MODEL_ID --component $component --om $om --report $report --reference-dir $refs"
    "$PYTHON_BIN" "$CASE_ROOT/prepare_models.py" validate-candidate --model "$MODEL_ID" --component "$component" --om "$om" --report "$report" --reference-dir "$refs"
  } >"$log" 2>&1
  local rc=$?
  set -e
  local classification=execute_failed status=failed
  [[ -f "$report" ]] && classification="$(classify_validation "$report" 2>/dev/null || echo execute_failed)"
  [[ "$rc" == 0 && "$classification" == passed ]] && status=passed
  write_stage "$stage" validation "$component" "$status" "$classification" "$rc" "$log" "$om" "$report"
  # Keep a stable matrix-cell record separate from the human-readable ACL
  # report. Re-running a cell therefore never overwrites another cell's data.
  mkdir -p "$CAMPAIGN_ROOT/cells/$component"
  "$PYTHON_BIN" - "$report" "$CAMPAIGN_ROOT/cells/$component/${CELL_ID:-${ARTIFACT_LABEL}-om-on-${RUNTIME_LABEL}}/result.json" "$component" "$status" "$classification" "$ARTIFACT_LABEL" "$RUNTIME_LABEL" "$om" "$log" <<'PY'
import json,sys
from pathlib import Path
report,target,component,status,classification,compiler,runtime,om,log=sys.argv[1:]
payload={"schema_version":1,"mode":"validate","component":component,
         "status":"passed" if status=="passed" else classification,
         "classification":classification,"compiler_role":compiler or None,
         "runtime_role":runtime or None,"compiler_board":compiler or None,
         "runtime_board":runtime or None,"om_path":om,"validator_log":log,
         "production_mutation":False}
try:
    value=json.loads(Path(report).read_text(encoding="utf-8"))
except Exception as exc:
    value={"error":str(exc)}
payload.update({"model_id":value.get("model_id"),"threshold":value.get("threshold"),
                "passed_count":sum(1 for item in value.get("references",[]) if isinstance(item,dict) and item.get("passed")),
                "sample_count":len(value.get("references",[])),"min_cosine":None,
                "max_cosine":None,"acl_status":"passed" if status=="passed" else "failed",
                "fixture_expected":36 if component=="image" else 20,
                "failure_class":None if status=="passed" else classification,
                "validator_report":report})
cosines=[float(item["cosine_similarity"]) for item in value.get("references",[])
         if isinstance(item,dict) and item.get("cosine_similarity") is not None]
if cosines:
    payload["min_cosine"]=min(cosines); payload["max_cosine"]=max(cosines)
if value.get("error"): payload["error"]=value["error"]
target=Path(target); target.parent.mkdir(parents=True,exist_ok=True)
target.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY
  [[ "$status" == passed ]]
}

overall=0
if [[ "$MODE" == preflight ]]; then
  write_preflight_stage
else
  for component in image text; do
    [[ "$COMPONENTS" == all || "$COMPONENTS" == "$component" ]] || continue
    om_path="$(om_for "$component")"
    if [[ "$MODE" == native ]]; then
      conversion_ok=0
      if run_conversion "$component"; then conversion_ok=1; else overall=1; fi
      # Never validate a stale/partial OM after a failed conversion.
      [[ "$conversion_ok" == 1 && -f "$om_path" ]] || continue
    fi
    # The preparation step creates an exact-count immutable fixture view under
    # the campaign root; use it rather than a directory that may also contain
    # the canonical one-sample reference.
    run_validation "$component" "$om_path" "$CAMPAIGN_ROOT/references/$component" || overall=1
  done
fi

  CAMPAIGN_FINISHED_AT="$(date --iso-8601=seconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$PYTHON_BIN" - "$CAMPAIGN_ROOT/summary.json" "$CAMPAIGN_ROOT" "$CAMPAIGN_ROOT/environment/inputs.json" "$CAMPAIGN_ROOT/environment/environment.json" "$MODE" "$SOC_VERSION" "$overall" "$CAMPAIGN_STARTED_AT" "$CAMPAIGN_FINISHED_AT" "$ARTIFACT_LABEL" "$RUNTIME_LABEL" <<'PY'
import json,sys
from pathlib import Path
target,root,inputs,environment,mode,soc,overall,started,finished,compiler_role,runtime_role=sys.argv[1:]
root=Path(root); stages=[]
for path in sorted((root/"stages").glob("*.json")):
    try: value=json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc: value={"status":"failed","classification":"execute_failed","error":str(exc)}
    value["evidence_path"]=str(path); stages.append(value)
passed=bool(int(overall)==0 and stages and all(x.get("status")=="passed" for x in stages))
payload={"schema_version":1,"campaign_id":root.name,"campaign_kind":"mobileclip_cross_board",
         "mode":mode,"soc_version":soc,"compiler_role":compiler_role or None,
         "runtime_role":runtime_role or None,"started_at":started,"finished_at":finished,
         "serial":True,"compiler_cache":"disabled","cpu_fallback":False,
         "swap_required_total_bytes":0,"production_mutation":False,
         "inputs":inputs,"environment":environment,"stages":stages,"passed":passed}
Path(target).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
lines=["campaign_id="+root.name,"mode="+mode,"soc_version="+soc,
       "compiler_role="+(compiler_role or ""),"runtime_role="+(runtime_role or ""),
       "started_at="+started,"finished_at="+finished,"passed="+str(passed).lower()]
lines += [str(x.get("stage_kind"))+"/"+str(x.get("component"))+"="+str(x.get("classification"))+" exit="+str(x.get("exit_code")) for x in stages]
(root/"summary.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
PY
if [[ "$overall" != 0 ]]; then echo "[campaign] failed; inspect $CAMPAIGN_ROOT/summary.json" >&2; exit 1; fi
"$PYTHON_BIN" - "$CAMPAIGN_ROOT/artifact_manifest.json" "$CAMPAIGN_ROOT" <<'PY'
import hashlib, json, sys, time
from pathlib import Path

target, root = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
entries = []
for path in sorted(root.rglob("*")):
    # Reference fixtures can be symlinks to canonical files outside the
    # campaign. They remain described by inputs.json, but are not artifacts.
    if path == target or path.is_symlink() or not path.is_file():
        continue
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        continue
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    entries.append({
        "path": str(relative).replace("\\", "/"),
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    })
payload = {
    "schema_version": 1,
    "generated_at": time.time(),
    "production_mutation": False,
    "artifacts": entries,
}
target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
echo "[campaign] passed: $CAMPAIGN_ROOT/summary.json"
