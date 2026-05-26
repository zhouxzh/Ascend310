from webrtc_app.hevc import (
    HEVC_NAL_TYPE_FU,
    hevc_nal_type,
    packetize_hevc,
    split_annexb,
)


def make_hevc_nal(nal_type: int, payload: bytes) -> bytes:
    return bytes([(nal_type << 1) & 0x7E, 1]) + payload


def test_split_annexb_handles_three_and_four_byte_start_codes():
    vps = make_hevc_nal(32, b"vps")
    sps = make_hevc_nal(33, b"sps")
    stream = b"\x00\x00\x00\x01" + vps + b"\x00\x00\x01" + sps

    assert list(split_annexb(stream)) == [vps, sps]


def test_hevc_nal_type_extracts_six_bit_type():
    nal = make_hevc_nal(34, b"pps")

    assert hevc_nal_type(nal) == 34


def test_packetize_hevc_keeps_small_nal_as_single_packet():
    nal = make_hevc_nal(19, b"idr")

    assert packetize_hevc([nal]) == [nal]


def test_packetize_hevc_fragments_large_nal():
    nal = make_hevc_nal(19, bytes(range(256)) * 20)
    packets = packetize_hevc([nal])

    assert len(packets) > 1
    assert all(((packet[0] >> 1) & 0x3F) == HEVC_NAL_TYPE_FU for packet in packets)
    assert packets[0][2] & 0x80
    assert not packets[0][2] & 0x40
    assert packets[-1][2] & 0x40
    assert not packets[-1][2] & 0x80
    assert all((packet[2] & 0x3F) == 19 for packet in packets)
