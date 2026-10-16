"""Check aiortc offer/answer negotiation for the local H.265 mode."""

import asyncio
import json

from aiohttp.test_utils import make_mocked_request
from aiortc import RTCPeerConnection, RTCRtpReceiver

from server import (
    _patch_h265_encoder,
    build_app,
    close_peer_connection,
    offer,
    pcs,
)


async def main() -> None:
    _patch_h265_encoder()

    browser = RTCPeerConnection()
    transceiver = browser.addTransceiver("video", direction="recvonly")
    h265_codecs = [
        codec
        for codec in RTCRtpReceiver.getCapabilities("video").codecs
        if codec.mimeType.lower() == "video/h265"
    ]
    if not h265_codecs:
        raise SystemExit("RTCRtpReceiver capabilities do not contain video/H265")
    transceiver.setCodecPreferences(h265_codecs)

    offer_desc = await browser.createOffer()
    await browser.setLocalDescription(offer_desc)

    app = build_app(source_mode="demo", video_codec="h265")
    request = make_mocked_request("POST", "/offer", app=app)

    async def json_payload():
        return {
            "sdp": browser.localDescription.sdp,
            "type": browser.localDescription.type,
            "width": 320,
            "height": 240,
            "fps": 15,
        }

    request.json = json_payload
    response = await offer(request)
    payload = json.loads(response.text)

    print(f"status={response.status}")
    print(f"answer_has_h265={'H265/90000' in payload['sdp']}")
    print(f"answer_has_h264={'H264/90000' in payload['sdp']}")
    print(payload["sdp"])

    await browser.close()
    await asyncio.gather(
        *[close_peer_connection(pc) for pc in list(pcs)],
        return_exceptions=True,
    )
    pcs.clear()


if __name__ == "__main__":
    asyncio.run(main())
