"""Reads the reference station position (RTCM 1005/1006) from the logged corrections stream."""

from dataclasses import dataclass
from pathlib import Path

PREAMBLE = 0xD3
_CRC_POLY = 0x1864CFB


@dataclass(frozen=True)
class BasePosition:
    station: int
    ecef: tuple[float, float, float]
    count: int


def crc24q(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= _CRC_POLY
    return crc & 0xFFFFFF


def _bits(payload: bytes, start: int, length: int, signed: bool = False) -> int:
    value = int.from_bytes(payload, "big") >> (len(payload) * 8 - start - length) & ((1 << length) - 1)
    return value - (1 << length) if signed and value >> (length - 1) else value


def base_positions(path) -> list[BasePosition]:
    """Distinct station positions (to 1 mm) in order of first appearance, with how often each was sent.
    Frames with a bad CRC are skipped."""
    data = Path(path).read_bytes()
    seen: dict[tuple[int, tuple[float, float, float]], int] = {}
    i = 0
    while i + 6 <= len(data):
        if data[i] != PREAMBLE:
            i += 1
            continue
        length = ((data[i + 1] & 0x03) << 8) | data[i + 2]
        frame = data[i : i + 3 + length + 3]
        if len(frame) < 6 + length or crc24q(frame) != 0:
            i += 1
            continue
        payload = frame[3 : 3 + length]
        if length >= 19 and _bits(payload, 0, 12) in (1005, 1006):
            xyz = tuple(round(_bits(payload, start, 38, signed=True) * 1e-4, 4) for start in (34, 74, 114))
            key = (_bits(payload, 12, 12), xyz)
            seen[key] = seen.get(key, 0) + 1
        i += len(frame)
    return [BasePosition(station, xyz, count) for (station, xyz), count in seen.items()]


def encode_1005(station: int, ecef: tuple[float, float, float]) -> bytes:
    """A minimal RTCM 1005 frame, for tests and fixtures."""
    fields = [
        (1005, 12),
        (station, 12),
        (0, 6),
        (1, 1),
        (1, 1),
        (1, 1),
        (0, 1),
        (round(ecef[0] * 1e4), 38),
        (0, 1),
        (0, 1),
        (round(ecef[1] * 1e4), 38),
        (0, 2),
        (round(ecef[2] * 1e4), 38),
    ]
    value, bits = 0, 0
    for field, width in fields:
        value = (value << width) | (field & ((1 << width) - 1))
        bits += width
    payload = (value << (-bits % 8)).to_bytes((bits + 7) // 8, "big")
    head = bytes([PREAMBLE, len(payload) >> 8, len(payload) & 0xFF]) + payload
    return head + crc24q(head).to_bytes(3, "big")
