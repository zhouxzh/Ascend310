#!/usr/bin/env bash
# Install the one pinned tokenizer wheel into an isolated Case9 20T campaign
# overlay.  This script never changes the board's conda environment, CANN,
# driver, OPP, or system Python, and it never reaches a package index.
set -Eeuo pipefail

ROOT="${CASE9_OM20T_ROOT:-/home/HwHiAiUser/case9-om-20t-20260914}"
CONDA_ENV="${CASE9_OM20T_CONDA_ENV:-base}"
CONDA_SH="${CASE9_OM20T_CONDA_SH:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
MODE="check"

WHEEL_NAME="tokenizers-0.19.1-cp39-cp39-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
WHEEL_BYTES=3570575
WHEEL_SHA256="c85cf76561fbd01e0d9ea2d1cbe711a65400092bc52b5242b16cfd22e51f0c58"

usage() {
  cat <<'EOF'
Usage: bash scripts/provision_case9_om20t_overlay.sh (--check|--install) [options]

Options:
  --root DIR         Isolated 20T campaign root, default
                     /home/HwHiAiUser/case9-om-20t-20260914
  --conda-env NAME   Existing conda environment to use, default base
  --conda-sh PATH    Existing conda activation script
  --check            Verify the wheel and existing campaign overlay only
  --install          Install the pinned wheel into <root>/overlay only
  --help             Show this help

The wheel must already have been transferred by sync_case9_om20t.sh.  The
install path is a new isolated overlay, created atomically.  This command uses
pip --no-index --no-deps --only-binary=:all: and cannot download, upgrade, or
uninstall any package in base.
EOF
}

die() {
  echo "Case9 20T overlay provisioning refused: $*" >&2
  exit 2
}

is_word() {
  [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]]
}

while (($#)); do
  case "$1" in
    --root) [[ $# -ge 2 ]] || die "--root requires a value"; ROOT="$2"; shift 2 ;;
    --conda-env) [[ $# -ge 2 ]] || die "--conda-env requires a value"; CONDA_ENV="$2"; shift 2 ;;
    --conda-sh) [[ $# -ge 2 ]] || die "--conda-sh requires a value"; CONDA_SH="$2"; shift 2 ;;
    --check) MODE="check"; shift ;;
    --install) MODE="install"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

is_word "$CONDA_ENV" || die "unsafe conda environment name"
[[ -d "$ROOT" ]] || die "campaign root does not exist: $ROOT"
ROOT="$(cd "$ROOT" && pwd)"
case "$ROOT" in
  /home/HwHiAiUser/case9-om-20t-*) ;;
  *) die "root must be an isolated /home/HwHiAiUser/case9-om-20t-* directory" ;;
esac
[[ -r "$CONDA_SH" ]] || die "conda profile is unavailable: $CONDA_SH"

WHEEL="$ROOT/wheels/$WHEEL_NAME"
OVERLAY="$ROOT/overlay"
MARKER="$OVERLAY/.case9-tokenizers-overlay.json"

verify_wheel() {
  [[ -f "$WHEEL" ]] || die "pinned wheel is missing: $WHEEL"
  local actual_bytes actual_sha
  actual_bytes="$(stat -c '%s' "$WHEEL")"
  actual_sha="$(sha256sum "$WHEEL" | awk '{print $1}')"
  [[ "$actual_bytes" == "$WHEEL_BYTES" ]] || die "wheel byte count mismatch: $actual_bytes != $WHEEL_BYTES"
  [[ "$actual_sha" == "$WHEEL_SHA256" ]] || die "wheel SHA-256 mismatch: $actual_sha != $WHEEL_SHA256"
}

activate_python() {
  set +u
  # shellcheck disable=SC1090
  source "$CONDA_SH"
  set -u
  conda activate "$CONDA_ENV" || return 1
  export PATH="${CONDA_PREFIX:-}/bin:$PATH"
  export PYTHONNOUSERSITE=1
  hash -r 2>/dev/null || true
  python - <<'PY'
import os
import sys
from pathlib import Path

prefix = os.environ.get("CONDA_PREFIX", "")
if not prefix:
    raise SystemExit("CONDA_PREFIX is empty after activation")
if sys.version_info[:2] != (3, 9):
    raise SystemExit("Case9 20T overlay requires Python 3.9")
if Path(sys.prefix).resolve() != Path(prefix).resolve():
    raise SystemExit("active Python is outside CONDA_PREFIX")
print("python=%s version=%s" % (sys.executable, sys.version.split()[0]))
PY
}

check_overlay() {
  local candidate="$1"
  local candidate_marker="$candidate/.case9-tokenizers-overlay.json"
  [[ -d "$candidate" ]] || return 1
  [[ -f "$candidate_marker" ]] || return 1
  PYTHONPATH="$candidate" PYTHONNOUSERSITE=1 python - "$candidate" "$candidate_marker" "$WHEEL_NAME" "$WHEEL_BYTES" "$WHEEL_SHA256" <<'PY'
import json
import sys
from pathlib import Path

overlay = Path(sys.argv[1]).resolve()
marker = Path(sys.argv[2])
expected_name, expected_bytes, expected_sha = sys.argv[3:]
try:
    value = json.loads(marker.read_text(encoding="utf-8"))
except (OSError, ValueError) as exc:
    raise SystemExit("invalid overlay marker: %s" % exc)
for key, expected in {
    "wheel_name": expected_name,
    "wheel_bytes": int(expected_bytes),
    "wheel_sha256": expected_sha,
    "tokenizers_version": "0.19.1",
}.items():
    if value.get(key) != expected:
        raise SystemExit("overlay marker mismatch for %s" % key)
import tokenizers
origin = Path(tokenizers.__file__).resolve()
try:
    origin.relative_to(overlay)
except ValueError as exc:
    raise SystemExit("tokenizers import did not come from campaign overlay: %s" % origin) from exc
if tokenizers.__version__ != "0.19.1":
    raise SystemExit("unexpected tokenizers version: %s" % tokenizers.__version__)
print("overlay_tokenizers=%s" % origin)
PY
}

verify_wheel
if ! activate_python; then
  die "could not activate conda environment $CONDA_ENV"
fi

if [[ "$MODE" == "check" ]]; then
  check_overlay "$OVERLAY" || die "campaign overlay is absent or does not match the pinned wheel: $OVERLAY"
  echo "Case9 20T tokenizer overlay verified: $OVERLAY"
  exit 0
fi

if [[ -e "$OVERLAY" ]]; then
  if check_overlay "$OVERLAY"; then
    echo "Case9 20T tokenizer overlay already verified: $OVERLAY"
    exit 0
  fi
  die "existing overlay is not the admitted tokenizer overlay; refusing to replace it: $OVERLAY"
fi

STAGING="$(mktemp -d "$ROOT/.case9-tokenizers-overlay.XXXXXX")"
[[ "$STAGING" == "$ROOT"/.case9-tokenizers-overlay.* ]] || die "unsafe temporary overlay path"
echo "Installing the pinned tokenizer wheel into isolated staging: $STAGING"
if ! python -m pip install --disable-pip-version-check --no-index --no-deps --only-binary=:all: \
  --target "$STAGING/overlay" "$WHEEL"; then
  die "wheel installation failed; staging was retained for inspection: $STAGING"
fi

python - "$STAGING/overlay/.case9-tokenizers-overlay.json" "$WHEEL_NAME" "$WHEEL_BYTES" "$WHEEL_SHA256" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "wheel_name": sys.argv[2],
    "wheel_bytes": int(sys.argv[3]),
    "wheel_sha256": sys.argv[4],
    "tokenizers_version": "0.19.1",
}
with path.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY

if ! check_overlay "$STAGING/overlay"; then
  die "staged tokenizer overlay failed its import-origin check: $STAGING"
fi
[[ ! -e "$OVERLAY" ]] || die "overlay appeared during installation; staging retained: $STAGING"
mv -- "$STAGING/overlay" "$OVERLAY"
rmdir -- "$STAGING"
echo "Case9 20T tokenizer overlay installed and verified: $OVERLAY"
