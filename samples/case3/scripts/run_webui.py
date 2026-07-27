from pathlib import Path
import sys

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
