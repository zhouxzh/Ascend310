"""End-to-end test for DVPP camera pipeline: V4L2 MJPEG → JPEGD → VENC → WebRTC."""
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time

import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("e2e_test")


async def test_offer(port: int, width: int, height: int, fps: int,
                     duration: float = 10.0) -> tuple[bool, float]:
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")

    track_event = asyncio.Event()
    frame_count = 0
    t0 = None

    @pc.on("track")
    def on_track(track):
        nonlocal t0
        logger.info("Received track: %s", track.id)
        t0 = time.perf_counter()
        track_event.set()

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    payload = {
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type,
        "width": width,
        "height": height,
        "fps": fps,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/offer", json=payload
        ) as resp:
            result = await resp.json()
            logger.info("Source: %s", json.dumps(
                result.get("source_settings", {}).get("applied", {})))

            if resp.status != 200:
                logger.error("Offer failed: %s", result)
                await pc.close()
                return False, 0

            answer = RTCSessionDescription(sdp=result["sdp"], type=result["type"])
            await pc.setRemoteDescription(answer)

    try:
        await asyncio.wait_for(track_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.error("Timed out waiting for video track")
        await pc.close()
        return False, 0

    # Wait for frames to flow
    await asyncio.sleep(duration)

    await pc.close()

    elapsed = time.perf_counter() - t0 if t0 else duration
    # Get stats from server log (captured in server process output)
    logger.info("Stream ran for %.1fs", elapsed)
    return True, elapsed


async def main():
    port = 8090
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = (
        "/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:"
        "/usr/local/Ascend/driver/lib64"
    )
    env["PYTHONPATH"] = "/usr/local/Ascend/ascend-toolkit/latest/python/site-packages"

    server = subprocess.Popen(
        [
            "/home/HwHiAiUser/.conda/envs/mediapipe/bin/python",
            "server.py",
            "--source", "dvpp_camera",
            "--hardware-encode",
            "--port", str(port),
            "--log-level", "INFO",
        ],
        cwd="/home/HwHiAiUser/Documents/WebRTC",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    await asyncio.sleep(4)  # Wait for server startup + camera init

    success = False
    try:
        success, elapsed = await test_offer(port, 1920, 1080, 30, duration=12.0)
    finally:
        server.terminate()
        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, server.wait, 3),
                timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            server.kill()
        stdout, _ = server.communicate()
        # Show relevant lines from server log
        lines = stdout.decode(errors="replace").split("\n")
        for line in lines:
            if any(kw in line for kw in ("frames=", "JPEGD", "ERROR", "Traceback", "Exception")):
                print(line)

    if success:
        print(f"E2E_TEST: PASS (streamed ~{elapsed:.0f}s)")
    else:
        print("E2E_TEST: FAIL")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
