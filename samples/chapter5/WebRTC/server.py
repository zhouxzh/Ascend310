import argparse
import asyncio
import logging
import os
import socket
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription

from webrtc_app.ascend_source import AscendVideoTrack, DEFAULT_SOURCE_NAME
from webrtc_app.cann_encoder import CannH264Encoder, _try_import_cann, _CANN_READY


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
LOG_DIR = ROOT / "logs"
pcs: set[RTCPeerConnection] = set()
app_logger = logging.getLogger("server")


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_DIR / "index.html")


async def client_js(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_DIR / "client.js")


async def styles_css(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_DIR / "styles.css")


async def health(request: web.Request) -> web.Response:
    source_mode = request.config_dict.get("source_mode", "demo")
    return web.json_response(
        {
            "status": "ok",
            "runtime_target": "ascend-310b",
            "default_source": source_mode,
        }
    )


def parse_offer_payload(params: dict[str, object]) -> tuple[RTCSessionDescription, int, int, int]:
    try:
        offer = RTCSessionDescription(sdp=str(params["sdp"]), type=str(params["type"]))
        width = int(params.get("width", 1280))
        height = int(params.get("height", 720))
        fps = int(params.get("fps", 30))
    except (KeyError, TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=f"Invalid offer payload: {exc}") from exc

    if width <= 0 or height <= 0 or fps <= 0:
        raise web.HTTPBadRequest(text="width, height, and fps must be positive integers.")

    return offer, width, height, fps


async def offer(request: web.Request) -> web.Response:
    params = await request.json()
    offer, width, height, fps = parse_offer_payload(params)

    # Close stale connections first to release /dev/video0 for new offer
    if pcs:
        logger = logging.getLogger("pc")
        logger.info("Closing %s stale peer connection(s) before new offer", len(pcs))
        await asyncio.gather(
            *[close_peer_connection(pc) for pc in list(pcs)],
            return_exceptions=True,
        )
        pcs.clear()
        await asyncio.sleep(0.3)

    pc = RTCPeerConnection()
    pcs.add(pc)
    logger = logging.getLogger("pc")
    logger.info(
        "Create PeerConnection %s for Ascend source width=%s height=%s fps=%s",
        id(pc),
        width,
        height,
        fps,
    )

    source_track: Optional[AscendVideoTrack] = None

    try:
        source_track = AscendVideoTrack(
            width=width,
            height=height,
            fps=fps,
            source_type=request.config_dict.get("source_mode", "demo"),
            camera_device=request.config_dict.get("camera_device", 0),
        )
        pc.addTrack(source_track)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            logger.info("PeerConnection %s state -> %s", id(pc), pc.connectionState)
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                await close_peer_connection(pc, source_track)

        @pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange() -> None:
            logger.info("PeerConnection %s ICE -> %s", id(pc), pc.iceConnectionState)
            if pc.iceConnectionState in {"failed", "closed", "disconnected"}:
                await close_peer_connection(pc, source_track)

        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        logger.info(
            "PeerConnection %s created answer successfully for source=%s",
            id(pc),
            source_track.source_name,
        )
        return web.json_response(
            {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
                "source_settings": source_track.describe_settings(),
            }
        )
    except web.HTTPException:
        raise
    except Exception:
        logger.exception(
            "Offer handling failed for PeerConnection %s with source=%s width=%s height=%s fps=%s",
            id(pc),
            DEFAULT_SOURCE_NAME,
            width,
            height,
            fps,
        )
        await close_peer_connection(pc, source_track)
        raise web.HTTPInternalServerError(text="Failed to create WebRTC answer. Check logs/server.log.")


async def close_peer_connection(
    pc: RTCPeerConnection,
    source_track: Optional[AscendVideoTrack] = None,
) -> None:
    logger = logging.getLogger("pc")

    if source_track is not None:
        try:
            source_track.stop()
        except Exception:
            logger.exception("Failed to stop source track for PeerConnection %s", id(pc))

    if pc in pcs:
        pcs.discard(pc)
        try:
            await pc.close()
        except Exception:
            logger.exception("Failed to close PeerConnection %s cleanly", id(pc))


@web.middleware
async def error_logging_middleware(
    request: web.Request,
    handler,
) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        app_logger.exception(
            "Unhandled server error while processing %s %s",
            request.method,
            request.path,
        )
        raise web.HTTPInternalServerError(text="Unhandled server error. Check logs/server.log.")


async def on_shutdown(_: web.Application) -> None:
    app_logger.info("Shutting down server, closing %s peer connections", len(pcs))
    await asyncio.gather(
        *[close_peer_connection(pc) for pc in list(pcs)],
        return_exceptions=True,
    )
    pcs.clear()


def build_app(source_mode: str = "demo", camera_device: str | int = 0) -> web.Application:
    app = web.Application(middlewares=[error_logging_middleware])
    app["source_mode"] = source_mode
    app["camera_device"] = camera_device
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", client_js)
    app.router.add_get("/styles.css", styles_css)
    app.router.add_get("/health", health)
    app.router.add_post("/offer", offer)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Python aiortc sender for Ascend 310B WebRTC publishing."
    )
    parser.add_argument("--host", default=os.environ.get("WEBRTC_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("WEBRTC_PORT", "8080")),
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("WEBRTC_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--log-file",
        default=os.environ.get("WEBRTC_LOG_FILE", str(LOG_DIR / "server.log")),
        help="Path to the server log file.",
    )
    parser.add_argument(
        "--source",
        default=os.environ.get("WEBRTC_SOURCE", "demo"),
        choices=["demo", "usb_camera", "dvpp_camera"],
        help="Video source type (default: demo).",
    )
    parser.add_argument(
        "--camera-device",
        default=os.environ.get("WEBCAM_DEVICE", "0"),
        help="Camera device index or path, e.g. 0, /dev/video0 (default: 0).",
    )
    parser.add_argument(
        "--hardware-encode",
        action="store_true",
        default=os.environ.get("WEBCAM_HARDWARE_ENCODE", "").lower() in ("1", "true", "yes"),
        help="Use CANN VENC hardware H264 encoding instead of CPU libx264.",
    )
    return parser.parse_args()


def setup_logging(log_level: str, log_file: str) -> None:
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(getattr(logging, log_level))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    app_logger.info("File logging enabled at %s", log_path)


def get_local_ip() -> str:
    """Discover the LAN IP address by connecting a UDP socket."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _patch_h264_encoder():
    """Replace aiortc H264Encoder with CANN VENC encoder."""
    _try_import_cann()
    if not _CANN_READY:
        app_logger.warning(
            "CANN ACL not available, H264 encoding will fall back to CPU libx264"
        )
        return False
    import aiortc.codecs.h264 as h264_module
    h264_module.H264Encoder = CannH264Encoder
    app_logger.info("H264 encoder switched to CANN VENC hardware")
    return True


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level, args.log_file)
    app_logger.info(
        "Starting server on http://%s:%s with source=%s log level=%s",
        args.host,
        args.port,
        args.source,
        args.log_level,
    )

    if args.hardware_encode or args.source == "dvpp_camera":
        _patch_h264_encoder()

    local_ip = get_local_ip()
    print(f"Browser URL: http://{local_ip}:{args.port}")

    camera_device: str | int = args.camera_device
    if str(camera_device).isdigit():
        camera_device = int(camera_device)

    web.run_app(
        build_app(source_mode=args.source, camera_device=camera_device),
        host=args.host,
        port=args.port,
        access_log=app_logger,
    )


if __name__ == "__main__":
    main()
