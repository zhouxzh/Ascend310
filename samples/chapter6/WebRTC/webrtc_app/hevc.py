import math
from collections.abc import Iterable, Iterator


# Keep HEVC RTP payloads comfortably below the common 1200-byte WebRTC MTU.
# aiortc's H.264 helper uses 1300, but Chrome's HEVC receiver is less
# forgiving when SRTP/UDP/IP overhead pushes packets near the path MTU.
PACKET_MAX = 1188
HEVC_NAL_HEADER_SIZE = 2
HEVC_FU_HEADER_SIZE = 3
HEVC_NAL_TYPE_FU = 49

HEVC_NAL_VPS = 32
HEVC_NAL_SPS = 33
HEVC_NAL_PPS = 34
HEVC_NAL_IDR_W_RADL = 19
HEVC_NAL_IDR_N_LP = 20


def hevc_nal_type(nal: bytes) -> int:
    if len(nal) < HEVC_NAL_HEADER_SIZE:
        raise ValueError("HEVC NAL unit is too short")
    return (nal[0] >> 1) & 0x3F


def split_annexb(buf: bytes) -> Iterator[bytes]:
    i = 0
    while True:
        i = buf.find(b"\x00\x00\x01", i)
        if i == -1:
            return

        i += 3
        nal_start = i
        i = buf.find(b"\x00\x00\x01", i)
        if i == -1:
            nal = buf[nal_start:]
            if nal:
                yield nal
            return
        if buf[i - 1] == 0:
            nal = buf[nal_start : i - 1]
        else:
            nal = buf[nal_start:i]
        if nal:
            yield nal


def packetize_hevc_fu(nal: bytes) -> list[bytes]:
    if len(nal) <= HEVC_NAL_HEADER_SIZE:
        raise ValueError("HEVC NAL unit is too short for fragmentation")

    available_size = PACKET_MAX - HEVC_FU_HEADER_SIZE
    payload_size = len(nal) - HEVC_NAL_HEADER_SIZE
    num_packets = math.ceil(payload_size / available_size)
    package_size = math.ceil(payload_size / num_packets)

    nal_type = hevc_nal_type(nal)
    fu_indicator = bytes(
        [
            (nal[0] & 0x81) | (HEVC_NAL_TYPE_FU << 1),
            nal[1],
        ]
    )

    packets = []
    offset = HEVC_NAL_HEADER_SIZE
    first = True
    while offset < len(nal):
        payload = nal[offset : offset + package_size]
        offset += len(payload)

        fu_header = nal_type
        if first:
            fu_header |= 0x80
            first = False
        if offset >= len(nal):
            fu_header |= 0x40

        packets.append(fu_indicator + bytes([fu_header]) + payload)

    return packets


def packetize_hevc(nals: Iterable[bytes]) -> list[bytes]:
    packets = []
    for nal in nals:
        if len(nal) <= PACKET_MAX:
            packets.append(nal)
        else:
            packets.extend(packetize_hevc_fu(nal))
    return packets


def has_hevc_keyframe_markers(bitstream: bytes) -> bool:
    wanted = {
        HEVC_NAL_VPS,
        HEVC_NAL_SPS,
        HEVC_NAL_PPS,
        HEVC_NAL_IDR_W_RADL,
        HEVC_NAL_IDR_N_LP,
    }
    return any(hevc_nal_type(nal) in wanted for nal in split_annexb(bitstream))
