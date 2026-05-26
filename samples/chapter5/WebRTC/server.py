import argparse
import asyncio
import logging
import os
import socket
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from aiohttp import web
from aiortc import RTCPeerConnection, RTCRtpSender, RTCSessionDescription

from webrtc_app.ascend_source import AscendVideoTrack, DEFAULT_SOURCE_NAME
from webrtc_app import cann_encoder
from webrtc_app.cann_encoder import (
    CannH264Encoder,
    CannH265Encoder,
    estimate_venc_bitrate_kbps,
    get_session_bitrate_override_kbps,
    set_session_bitrate_override_kbps,
)


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
LOG_DIR = ROOT / "logs"
pcs: set[RTCPeerConnection] = set()
app_logger = logging.getLogger("server")
VIDEO_CODEC_H264 = "h264"
VIDEO_CODEC_H265 = "h265"


def no_store_file_response(path: Path) -> web.FileResponse:
    response = web.FileResponse(path)
    response.headers["Cache-Control"] = "no-store"
    return response


async def index(_: web.Request) -> web.FileResponse:
    return no_store_file_response(WEB_DIR / "index.html")


async def client_js(_: web.Request) -> web.FileResponse:
    return no_store_file_response(WEB_DIR / "client.js")


async def styles_css(_: web.Request) -> web.FileResponse:
    return no_store_file_response(WEB_DIR / "styles.css")


async def health(request: web.Request) -> web.Response:
    source_mode = request.config_dict.get("source_mode", "demo")
    return web.json_response(
        {
            "status": "ok",
            "runtime_target": "ascend-310b",
            "default_source": source_mode,
            "video_codec": request.config_dict.get("video_codec", VIDEO_CODEC_H264),
        }
    )


def parse_offer_payload(
    params: dict[str, object],
) -> tuple[RTCSessionDescription, int, int, int, Optional[int]]:
    try:
        offer = RTCSessionDescription(sdp=str(params["sdp"]), type=str(params["type"]))
        width = int(params.get("width", 1280))
        height = int(params.get("height", 720))
        fps = int(params.get("fps", 30))
        bitrate_kbps = params.get("bitrate_kbps")
        if bitrate_kbps in (None, "", 0, "0"):
            bitrate_kbps = None
        else:
            bitrate_kbps = int(bitrate_kbps)
    except (KeyError, TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=f"Invalid offer payload: {exc}") from exc

    if width <= 0 or height <= 0 or fps <= 0:
        raise web.HTTPBadRequest(text="width, height, and fps must be positive integers.")
    if bitrate_kbps is not None and bitrate_kbps <= 0:
        raise web.HTTPBadRequest(text="bitrate_kbps must be a positive integer.")

    return offer, width, height, fps, bitrate_kbps


def _offer_has_codec(sdp: str, codec_name: str) -> bool:
    wanted = codec_name.lower()
    return any(
        line.startswith("a=rtpmap:")
        and line.strip().split(None, 1)[-1].split("/", 1)[0].lower() == wanted
        for line in sdp.splitlines()
    )


def resolve_offer_bitrate_kbps(
    width: int,
    height: int,
    fps: int,
    video_codec: str,
    bitrate_kbps: Optional[int] = None,
) -> int:
    if bitrate_kbps is not None:
        return max(500, min(int(bitrate_kbps), 6000))
    return estimate_venc_bitrate_kbps(width, height, fps, codec=video_codec)


def _local_video_codecs(mime_type: str):
    return [
        codec
        for codec in RTCRtpSender.getCapabilities("video").codecs
        if codec.mimeType.lower() == mime_type.lower()
    ]


def _sync_h265_parameters_from_offer(sdp: str) -> dict[str, object]:
    """Echo browser-offered H265 payload type / fmtp in the answer."""
    from aiortc.sdp import SessionDescription
    import aiortc.codecs as codecs_module

    session = SessionDescription.parse(sdp)
    for media in session.media:
        if media.kind != "video":
            continue
        for codec in media.rtp.codecs:
            if codec.mimeType.lower() == "video/h265":
                parameters = dict(codec.parameters)
                for local_codec in codecs_module.CODECS["video"]:
                    if local_codec.mimeType.lower() == "video/h265":
                        local_codec.payloadType = codec.payloadType
                        local_codec.parameters = parameters
                return parameters
    return {}


def _prefer_video_codec_for_sender(
    pc: RTCPeerConnection,
    sender: RTCRtpSender,
    mime_type: str,
) -> None:
    codecs = _local_video_codecs(mime_type)
    if not codecs:
        raise web.HTTPBadRequest(text=f"No local {mime_type} codec capability found.")

    for transceiver in pc.getTransceivers():
        if transceiver.sender == sender:
            transceiver.setCodecPreferences(codecs)
            app_logger.info(
                "Video transceiver codec preference set to %s",
                mime_type,
            )
            return

    raise web.HTTPInternalServerError(text="Could not find sender transceiver.")


async def offer(request: web.Request) -> web.Response:
    params = await request.json()
    offer, width, height, fps, bitrate_kbps = parse_offer_payload(params)
    video_codec = request.config_dict.get("video_codec", VIDEO_CODEC_H264)

    if video_codec == VIDEO_CODEC_H265 and not _offer_has_codec(offer.sdp, "H265"):
        raise web.HTTPBadRequest(
            text=(
                "Browser offer does not contain video/H265. "
                "Use a WebRTC HEVC-capable browser."
            )
        )
    if video_codec == VIDEO_CODEC_H265:
        h265_parameters = _sync_h265_parameters_from_offer(offer.sdp)
        app_logger.info("H265 offer parameters: %s", h265_parameters)

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

    requested_bitrate_kbps = resolve_offer_bitrate_kbps(
        width,
        height,
        fps,
        video_codec,
        bitrate_kbps=bitrate_kbps,
    )

    pc = RTCPeerConnection()
    pcs.add(pc)
    logger = logging.getLogger("pc")
    logger.info(
        "Create PeerConnection %s for Ascend source width=%s height=%s fps=%s "
        "auto_bitrate=%s kbps",
        id(pc),
        width,
        height,
        fps,
        requested_bitrate_kbps,
    )

    source_track: Optional[AscendVideoTrack] = None

    try:
        set_session_bitrate_override_kbps(bitrate_kbps)
        source_track = AscendVideoTrack(
            width=width,
            height=height,
            fps=fps,
            source_type=request.config_dict.get("source_mode", "demo"),
            camera_device=request.config_dict.get("camera_device", 0),
        )
        sender = pc.addTrack(source_track)
        source_mode = request.config_dict.get("source_mode", "demo")
        hardware_encode = bool(request.config_dict.get("hardware_encode", False))
        if video_codec == VIDEO_CODEC_H265:
            _prefer_video_codec_for_sender(pc, sender, "video/H265")
        elif hardware_encode or source_mode == "dvpp_camera":
            _prefer_video_codec_for_sender(pc, sender, "video/H264")

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
        applied_bitrate_kbps = resolve_offer_bitrate_kbps(
            source_track.width,
            source_track.height,
            source_track.fps,
            video_codec,
            bitrate_kbps=get_session_bitrate_override_kbps(),
        )

        logger.info(
            "PeerConnection %s created answer successfully for source=%s",
            id(pc),
            source_track.source_name,
        )
        return web.json_response(
            {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
                "source_settings": source_track.describe_settings(
                    bitrate_kbps=applied_bitrate_kbps,
                    bitrate_mode="manual" if bitrate_kbps is not None else "auto",
                ),
            }
        )
    except web.HTTPException:
        raise
    except Exception:
        source_label = source_track.source_name if source_track is not None else DEFAULT_SOURCE_NAME
        logger.exception(
            "Offer handling failed for PeerConnection %s with source=%s width=%s "
            "height=%s fps=%s bitrate=%s",
            id(pc),
            source_label,
            width,
            height,
            fps,
            requested_bitrate_kbps,
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
        finally:
            set_session_bitrate_override_kbps(None)


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


def build_app(
    source_mode: str = "demo",
    camera_device: str | int = 0,
    hardware_encode: bool = False,
    video_codec: str = VIDEO_CODEC_H264,
) -> web.Application:
    app = web.Application(middlewares=[error_logging_middleware])
    app["source_mode"] = source_mode
    app["camera_device"] = camera_device
    app["hardware_encode"] = hardware_encode
    app["video_codec"] = video_codec
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
    parser.add_argument(
        "--video-codec",
        default=os.environ.get("WEBRTC_VIDEO_CODEC", VIDEO_CODEC_H264),
        choices=[VIDEO_CODEC_H264, VIDEO_CODEC_H265],
        help="Video codec to negotiate. H265 requires WebRTC HEVC browser support.",
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
    if not cann_encoder._try_import_cann():
        app_logger.warning(
            "CANN ACL not available, H264 encoding will fall back to CPU libx264"
        )
        return False
    import aiortc.codecs as codecs_module
    import aiortc.codecs.h264 as h264_module
    h264_module.H264Encoder = CannH264Encoder
    codecs_module.H264Encoder = CannH264Encoder
    app_logger.info("H264 encoder switched to CANN VENC hardware")
    return True


def _patch_h265_encoder():
    """Register H265 and use CANN VENC as the aiortc encoder."""
    if not cann_encoder._try_import_cann():
        raise RuntimeError("CANN ACL is required for H265 encoding")

    import aiortc.codecs as codecs_module
    import aiortc.rtcrtpsender as rtcrtpsender_module
    from aiortc.rtcrtpparameters import RTCRtcpFeedback, RTCRtpCodecParameters

    h265_exists = any(
        codec.mimeType.lower() == "video/h265"
        for codec in codecs_module.CODECS["video"]
        if not codecs_module.is_rtx(codec)
    )
    if not h265_exists:
        used_pts = {
            codec.payloadType
            for codec in codecs_module.CODECS["video"]
            if codec.payloadType is not None
        }
        payload_type = 97
        while payload_type in used_pts or payload_type + 1 in used_pts:
            payload_type += 2
        codecs_module.CODECS["video"].insert(
            0,
            RTCRtpCodecParameters(
                mimeType="video/H265",
                clockRate=90000,
                payloadType=payload_type,
                rtcpFeedback=[
                    RTCRtcpFeedback(type="nack"),
                    RTCRtcpFeedback(type="nack", parameter="pli"),
                    RTCRtcpFeedback(type="goog-remb"),
                ],
                parameters={},
            ),
        )
        codecs_module.CODECS["video"].insert(
            1,
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=payload_type + 1,
                parameters={"apt": payload_type},
            ),
        )

    original_get_encoder = codecs_module.get_encoder

    def get_encoder(codec):
        if codec.mimeType.lower() == "video/h265":
            return CannH265Encoder()
        return original_get_encoder(codec)

    codecs_module.get_encoder = get_encoder
    rtcrtpsender_module.get_encoder = get_encoder
    app_logger.info("H265 codec registered and switched to CANN VENC hardware")
    return True


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level, args.log_file)
    app_logger.info(
        "Starting server on http://%s:%s with source=%s video_codec=%s log level=%s",
        args.host,
        args.port,
        args.source,
        args.video_codec,
        args.log_level,
    )

    if args.video_codec == VIDEO_CODEC_H265:
        _patch_h265_encoder()
    elif args.hardware_encode or args.source == "dvpp_camera":
        _patch_h264_encoder()

    local_ip = get_local_ip()
    print(f"Browser URL: http://{local_ip}:{args.port}")

    camera_device: str | int = args.camera_device
    if str(camera_device).isdigit():
        camera_device = int(camera_device)

    web.run_app(
        build_app(
            source_mode=args.source,
            camera_device=camera_device,
            hardware_encode=args.hardware_encode or args.source == "dvpp_camera",
            video_codec=args.video_codec,
        ),
        host=args.host,
        port=args.port,
        access_log=app_logger,
    )


if __name__ == "__main__":
    main()
