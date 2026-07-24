from pathlib import Path
import socket
import sys

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from midi_ddsp_webui.app import app  # noqa: E402


def local_ipv4() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("1.1.1.1", 80))
            return str(connection.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def main() -> None:
    address = local_ipv4()
    print("\nMIDI-DDSP Studio")
    print("  Local:   http://127.0.0.1:8765")
    if address != "127.0.0.1":
        print(f"  Network: http://{address}:8765")
    print(flush=True)
    uvicorn.run(app, host="0.0.0.0", port=8765)


if __name__ == "__main__":
    main()
