import struct

import pytest
from conftest import edit_csv

from georoute.report import session_report
from georoute.session import SessionError, load_session

TICKS_PERIOD = 2**30


def _tm2_message(count, wn, tow_ms_r, sub_r, tow_ms_f, sub_f, rising=True, falling=True):
    flags = 0x01 | (1 << 3) | 0x40 | (0x80 if rising else 0) | (0x04 if falling else 0)
    payload = struct.pack("<BBHHHIIIII", 0, flags, count, wn, wn, tow_ms_r, sub_r, tow_ms_f, sub_f, 10)
    body = b"\x0d\x03" + struct.pack("<H", len(payload)) + payload
    a = b = 0
    for byte in body:
        a = (a + byte) & 0xFF
        b = (b + a) & 0xFF
    return b"\xb5\x62" + body + bytes([a, b])


def _drop_tm2(session_dir, pulse):
    edit_csv(session_dir / "tm2.csv", lambda rows: [r for r in rows if r[0] != str(pulse)])


def test_a_missing_time_mark_is_recovered_from_the_pico_clock(session_dir):
    truth = load_session(session_dir).command_time(6)
    _drop_tm2(session_dir, 6)
    s = load_session(session_dir)
    assert s.command_time(6) == pytest.approx(truth, abs=50e-6)
    assert s.recovered[6].confirmed_by_falling_edge is False


def test_recovered_pulses_are_reported_as_warnings_not_errors(session_dir):
    _drop_tm2(session_dir, 6)
    r = session_report(load_session(session_dir))
    assert r["errors"] == []
    assert any("pulse 6" in w and "recovered" in w for w in r["warnings"])


def test_an_intact_falling_edge_confirms_the_recovery(session_dir):
    s = load_session(session_dir)
    t = s.tm2[6]
    week, tow = divmod(t.rising_gps_s, 604800)
    fall = tow + 0.0503
    msg = _tm2_message(0, int(week), 1000, 0, int(fall * 1000), round((fall * 1000 % 1) * 1e6))
    (session_dir / "receiver.ubx").write_bytes(b"\x00junk" + msg)
    _drop_tm2(session_dir, 6)
    assert load_session(session_dir).recovered[6].confirmed_by_falling_edge is True


def test_a_falling_edge_that_disagrees_refuses_the_recovery(session_dir):
    s = load_session(session_dir)
    week, tow = divmod(s.tm2[6].rising_gps_s, 604800)
    fall = tow + 0.120
    msg = _tm2_message(0, int(week), 1000, 0, int(fall * 1000), round((fall * 1000 % 1) * 1e6))
    (session_dir / "receiver.ubx").write_bytes(msg)
    _drop_tm2(session_dir, 6)
    s = load_session(session_dir)
    assert 6 not in s.recovered
    with pytest.raises(SessionError, match="pulse 6"):
        s.command_time(6)


def test_no_valid_neighbours_means_no_recovery(session_dir):
    edit_csv(session_dir / "tm2.csv", lambda rows: rows[:1] + [r for r in rows[1:] if r[0] in ("0",)])
    s = load_session(session_dir)
    assert s.recovered == {}


def test_pico_ticks_that_wrap_are_handled(session_dir):
    truth = load_session(session_dir).command_time(6)
    offset = TICKS_PERIOD - load_session(session_dir).pulses[5].cmd_ticks_us - 1000

    def wrap(rows):
        for r in rows[1:]:
            r[2] = str((int(r[2]) + offset) % TICKS_PERIOD)
        return rows

    edit_csv(session_dir / "pulses.csv", wrap)
    _drop_tm2(session_dir, 6)
    assert load_session(session_dir).command_time(6) == pytest.approx(truth, abs=50e-6)
