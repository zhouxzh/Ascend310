"""Thin process launcher for the Case 1 FastAPI application."""

import argparse

from face_attendance.api import create_app


# Safe for ASGI tooling: create_app does not open the camera or import ACL.
app = create_app()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Case 1 face-attendance service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args(argv)

    import uvicorn

    # Keep one process: the NPU context and camera have a single owner.
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
