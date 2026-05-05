"""Common test fixtures and CANN availability detection."""

import os
import sys
import pytest


_CANN_AVAILABLE = None


def cann_available() -> bool:
    """Check if CANN ACL is importable (cached result)."""
    global _CANN_AVAILABLE
    if _CANN_AVAILABLE is not None:
        return _CANN_AVAILABLE

    cann_py = "/usr/local/Ascend/ascend-toolkit/latest/python/site-packages"
    cann_lib = "/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64"
    driver_lib = "/usr/local/Ascend/driver/lib64"

    if cann_py not in sys.path:
        sys.path.insert(0, cann_py)

    os.environ.setdefault("LD_LIBRARY_PATH", "")
    for lib in [cann_lib, driver_lib]:
        if lib not in os.environ["LD_LIBRARY_PATH"]:
            os.environ["LD_LIBRARY_PATH"] = (
                f"{lib}:{os.environ['LD_LIBRARY_PATH']}"
            )

    try:
        import acl  # noqa: F401
        _CANN_AVAILABLE = True
    except ImportError:
        _CANN_AVAILABLE = False

    return _CANN_AVAILABLE


needs_cann = pytest.mark.skipif(
    not cann_available(), reason="CANN ACL not available (requires Ascend 310B)"
)


@pytest.fixture(scope="module")
def venc():
    """Create a CANN VENC channel for encoding tests."""
    if not cann_available():
        pytest.skip("CANN not available")

    from webrtc_app.cann_encoder import CannVenc, ENTYPE_H264_BASE

    v = CannVenc(width=640, height=480, fps=30, entype=ENTYPE_H264_BASE)
    yield v
    v.destroy()
