import importlib.util
import os
from pathlib import Path
import shlex
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANN_ENV_SCRIPT = Path("/usr/local/Ascend/ascend-toolkit/set_env.sh")
CONDA_ENV_SCRIPT = Path("/usr/local/miniconda3/etc/profile.d/conda.sh")
CANN_BOOTSTRAP_FLAG = "MIDI_DDSP_CANN_BOOTSTRAPPED"


def ensure_board_runtime_environment() -> None:
    """Re-exec this entrypoint with the board's existing CANN/base environment."""
    if importlib.util.find_spec("acl") is not None or not CANN_ENV_SCRIPT.is_file():
        return
    if os.environ.get(CANN_BOOTSTRAP_FLAG) == "1":
        raise RuntimeError("PyACL is unavailable after loading the existing CANN environment")
    if not CONDA_ENV_SCRIPT.is_file():
        raise RuntimeError(f"Conda environment script is missing: {CONDA_ENV_SCRIPT}")

    command = " && ".join(
        (
            f"export {CANN_BOOTSTRAP_FLAG}=1",
            f"source {shlex.quote(str(CANN_ENV_SCRIPT))}",
            f"source {shlex.quote(str(CONDA_ENV_SCRIPT))}",
            "conda activate base",
            "exec "
            + shlex.join(
                [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
            ),
        )
    )
    os.execv("/bin/bash", ["bash", "-lc", command])


ensure_board_runtime_environment()

import uvicorn  # noqa: E402


if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from midi_ddsp_webui.app import app  # noqa: E402
from midi_ddsp_webui.core import local_ipv4_addresses  # noqa: E402


def main() -> None:
    addresses = local_ipv4_addresses()
    print("\nMIDI-DDSP Studio")
    print("  Local:   http://127.0.0.1:8765")
    for address in addresses:
        if address != "127.0.0.1":
            print(f"  Network: http://{address}:8765")
    print(flush=True)
    uvicorn.run(app, host="0.0.0.0", port=8765)


if __name__ == "__main__":
    main()
