#!/usr/bin/env python3
import runpy
import sys
from pathlib import Path


def main() -> None:
    target = Path(__file__).resolve().with_name("webrtc_om_app.py")
    print(
        "scripts/gradio_om_app.py has been replaced by the WebRTC H.264 app. "
        "Launching scripts/webrtc_om_app.py ...",
        file=sys.stderr,
        flush=True,
    )
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
