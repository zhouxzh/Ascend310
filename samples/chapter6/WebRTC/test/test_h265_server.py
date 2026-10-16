import pytest


def test_offer_has_codec_detects_h265():
    pytest.importorskip("aiortc")
    from server import _offer_has_codec

    sdp = "\r\n".join(
        [
            "v=0",
            "m=video 9 UDP/TLS/RTP/SAVPF 100",
            "a=rtpmap:100 H265/90000",
        ]
    )

    assert _offer_has_codec(sdp, "H265")
    assert not _offer_has_codec(sdp, "H264")


def test_patch_h265_encoder_registers_capability(monkeypatch):
    pytest.importorskip("aiortc")
    import aiortc.codecs as codecs_module
    import aiortc.rtcrtpsender as rtcrtpsender_module
    from aiortc import RTCRtpSender
    from server import _patch_h265_encoder

    original_codecs = {kind: codecs[:] for kind, codecs in codecs_module.CODECS.items()}
    original_codecs_get_encoder = codecs_module.get_encoder
    original_sender_get_encoder = rtcrtpsender_module.get_encoder

    monkeypatch.setattr("webrtc_app.cann_encoder._try_import_cann", lambda: True)
    try:
        _patch_h265_encoder()
        assert any(
            codec.mimeType.lower() == "video/h265"
            for codec in RTCRtpSender.getCapabilities("video").codecs
        )
    finally:
        codecs_module.CODECS.clear()
        codecs_module.CODECS.update(original_codecs)
        codecs_module.get_encoder = original_codecs_get_encoder
        rtcrtpsender_module.get_encoder = original_sender_get_encoder
