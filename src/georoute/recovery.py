"""Recovers the GPS time of a pulse whose TIM-TM2 is missing or garbled, from the Pico's own microsecond
clock between the nearest pulses with good time marks. On field test 2 this predicted good time marks to
15 us at worst. Where the receiver's raw log holds an intact falling edge for the pulse, it must agree."""

import struct
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

TICKS_PERIOD = 2**30  # MicroPython's ticks_us wraps here on the RP2040
MAX_RATE_ERROR = 200e-6  # the Pico W crystal is +-30 ppm; anything far beyond means the anchors are wrong
MAX_ANCHOR_GAP_S = 60.0
FALLING_SEARCH_S = (0.02, 0.2)
# The pulse ends on the first 10 ms timer tick at or after pulse_ms; allow 1 ms either side.
FALLING_SLACK_S = (0.001, 0.011)
WEEK_S = 604800


@dataclass(frozen=True)
class RecoveredTime:
    gps_s: float
    before: int
    after: int
    confirmed_by_falling_edge: bool


def _ticks_diff(a: int, b: int) -> int:
    return (a - b + TICKS_PERIOD // 2) % TICKS_PERIOD - TICKS_PERIOD // 2


def recover_time_marks(
    ticks: dict[int, int], good: dict[int, float], ubx_path: Path | None, pulse_ms: int, warnings: list[str]
) -> dict[int, RecoveredTime]:
    falling = sorted(_falling_edges(ubx_path)) if ubx_path and ubx_path.exists() else []
    anchors = sorted(good)
    out: dict[int, RecoveredTime] = {}
    for n in sorted(set(ticks) - set(good)):
        k = bisect_left(anchors, n)
        if k == 0 or k == len(anchors):
            continue
        a, b = anchors[k - 1], anchors[k]
        span = _ticks_diff(ticks[b], ticks[a]) * 1e-6
        if span <= 0 or good[b] - good[a] > MAX_ANCHOR_GAP_S:
            continue
        rate = (good[b] - good[a]) / span
        if abs(rate - 1) > MAX_RATE_ERROR:
            warnings.append(
                f"pulse {n} time can't be recovered: pulses {a} and {b} imply a Pico clock "
                f"rate {(rate - 1) * 1e6:+.0f} ppm off"
            )
            continue
        t = good[a] + _ticks_diff(ticks[n], ticks[a]) * 1e-6 * rate
        nearby = [
            f
            for f in falling[bisect_left(falling, t + FALLING_SEARCH_S[0]) :]
            if f <= t + FALLING_SEARCH_S[1]
        ]
        expected = (t + pulse_ms / 1000 - FALLING_SLACK_S[0], t + pulse_ms / 1000 + FALLING_SLACK_S[1])
        confirmed = any(expected[0] <= f <= expected[1] for f in nearby)
        if nearby and not confirmed:
            warnings.append(
                f"pulse {n} time can't be recovered: the receiver's falling edge disagrees "
                f"({(nearby[0] - t) * 1000:.1f} ms after the predicted rising edge)"
            )
            continue
        out[n] = RecoveredTime(t, a, b, confirmed)
    return out


def _falling_edges(path: Path) -> list[float]:
    """Falling-edge GPS times from every checksum-valid, GPS-time TIM-TM2 message in a raw UBX log."""
    data = path.read_bytes()
    edges, i = [], 0
    while (i := data.find(b"\xb5\x62\x0d\x03", i)) >= 0:
        length = struct.unpack_from("<H", data, i + 4)[0] if i + 6 <= len(data) else 0
        end = i + 6 + length
        if length == 28 and end + 2 <= len(data) and _checksum(data[i + 2 : end]) == data[end : end + 2]:
            _, flags, _, _, wn_f, _, _, tow_ms_f, sub_f, _ = struct.unpack_from("<BBHHHIIIII", data, i + 6)
            if flags & 0x04 and (flags >> 3) & 3 == 1 and flags & 0x40:
                edges.append(wn_f * WEEK_S + tow_ms_f / 1e3 + sub_f / 1e9)
        i += 2
    return edges


def _checksum(body: bytes) -> bytes:
    a = b = 0
    for byte in body:
        a = (a + byte) & 0xFF
        b = (b + a) & 0xFF
    return bytes([a, b])
