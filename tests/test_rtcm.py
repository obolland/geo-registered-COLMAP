import pytest
from conftest import needs_ft1

from georoute.rtcm import base_positions, crc24q, encode_1005


def test_base_positions_from_a_corrections_log(tmp_path):
    a, b = (4006364.0375, -294184.8692, 4937580.8104), (4006364.0375, -294184.8692, 4937590.0)
    (tmp_path / "rtcm.bin").write_bytes(
        encode_1005(3527, a) + b"\x00junk" + encode_1005(3527, a) + encode_1005(3527, b)
    )
    positions = base_positions(tmp_path / "rtcm.bin")
    assert [(p.station, p.ecef) for p in positions] == [(3527, pytest.approx(a)), (3527, pytest.approx(b))]
    assert positions[0].count == 2


def test_a_corrupted_message_is_skipped(tmp_path):
    message = bytearray(encode_1005(1, (1.0, 2.0, 3.0)))
    message[10] ^= 0xFF
    (tmp_path / "rtcm.bin").write_bytes(bytes(message))
    assert base_positions(tmp_path / "rtcm.bin") == []


def test_a_frame_including_its_crc_checks_to_zero():
    assert crc24q(encode_1005(7, (1.0, 2.0, 3.0))) == 0


@needs_ft1
def test_field_test_1_has_one_base():
    from conftest import FT1

    (base,) = base_positions(FT1 / "rtcm.bin")
    assert base.station == 3527 and base.count == 9
    assert base.ecef == pytest.approx((4006364.0375, -294184.8692, 4937580.8104), abs=1e-4)
