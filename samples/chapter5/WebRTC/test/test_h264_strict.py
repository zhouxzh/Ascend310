import pytest


def test_patch_h264_encoder_requires_cann(monkeypatch):
    pytest.importorskip("aiortc")
    import server

    monkeypatch.setattr(server.cann_encoder, "_try_import_cann", lambda: False)
    with pytest.raises(RuntimeError, match="CANN ACL is required"):
        server._patch_h264_encoder()


def test_h264_encoder_does_not_use_cpu_encoder(monkeypatch):
    pytest.importorskip("aiortc")
    import webrtc_app.cann_encoder as module

    monkeypatch.setattr(module, "_CANN_READY", False)
    encoder = object.__new__(module.CannH264Encoder)
    with pytest.raises(RuntimeError, match="CANN ACL is required"):
        next(encoder._encode_frame(None, force_keyframe=False))
