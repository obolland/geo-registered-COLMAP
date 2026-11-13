import csv
import json

import numpy as np
import pytest
from conftest import FT1, edit_csv, needs_ft1, set_cells

from georoute.session import SessionError, load_session


def _edit_meta(d, edit, name="session.json"):
    meta = json.loads((d / "session.json").read_text())
    edit(meta)
    (d / "session.json").unlink()
    (d / name).write_text(json.dumps(meta))


def test_loads_the_trimmed_field_session(session_dir):
    s = load_session(session_dir)
    assert len(s.pulses) == 20
    assert [p.kind for p in s.pulses[:3]] == ["warmup", "warmup", "capture"]
    assert s.pulses[0].delays_us == (None, None, None, None)
    assert s.pulses[1].delays_us[0] == 103172
    assert len(s.pvt.gps_time_s) == 74
    assert s.clean_stop and s.meta_file == "session.json"
    assert s.datum == "ETRS89"


# GPS times are float64 seconds since 1980: about 0.24 us resolution, micrometres at riding speed.
def test_command_time_is_the_tm2_rising_edge_in_gps_seconds(session_dir):
    s = load_session(session_dir)
    assert s.command_time(0) == pytest.approx(2444 * 604800 + 56271.297 + 828193e-9, abs=1e-6)


def test_pvt_time_comes_from_utc_and_agrees_with_itow(session_dir):
    s = load_session(session_dir)
    week_start = 2444 * 604800
    np.testing.assert_allclose(s.pvt.gps_time_s - week_start, s.pvt.itow_ms / 1000, atol=1e-3)
    assert s.pvt.gps_time_s[0] - week_start == pytest.approx(56269.400222922, abs=1e-6)


def test_pvt_units_are_si(session_dir):
    s = load_session(session_dir)
    np.testing.assert_allclose(s.pvt.vel_ned_mps[0], [-0.776, -0.364, 0.125])
    assert s.pvt.h_acc_m[0] == pytest.approx(0.014)
    assert s.pvt.corr_age[0] == "1-2s"


def test_unclean_stop_is_accepted_and_reported(session_dir):
    _edit_meta(session_dir, lambda m: m.update(stop_utc=None, updated_utc="2026-11-08T15:38:31Z"))
    s = load_session(session_dir)
    assert not s.clean_stop
    assert s.meta["updated_utc"] == "2026-11-08T15:38:31Z"


def test_only_a_tmp_metadata_file_is_accepted(session_dir):
    _edit_meta(session_dir, lambda m: m.update(stop_utc=None), name="session.json.tmp")
    s = load_session(session_dir)
    assert s.meta_file == "session.json.tmp" and not s.clean_stop


def test_missing_metadata_refuses(session_dir):
    (session_dir / "session.json").unlink()
    with pytest.raises(SessionError, match="session.json"):
        load_session(session_dir)


@pytest.mark.parametrize("name", ["pulses.csv", "tm2.csv", "pvt.csv"])
def test_wrong_header_refuses(session_dir, name):
    edit_csv(session_dir / name, lambda rows: [rows[0][:-1] + ["surprise"]] + rows[1:])
    with pytest.raises(SessionError, match=name):
        load_session(session_dir)


def test_duplicate_pulse_refuses(session_dir):
    edit_csv(session_dir / "pulses.csv", lambda rows: rows + [rows[-1]])
    with pytest.raises(SessionError, match="pulse 19"):
        load_session(session_dir)


@pytest.mark.parametrize(("column", "value"), [(8, "0"), (9, "0")], ids=["timeBase-utc", "timeValid-0"])
def test_invalid_tm2_refuses_when_its_time_is_used(session_dir, column, value):
    def edit(rows):
        rows[4][column] = value
        return rows

    edit_csv(session_dir / "tm2.csv", edit)
    s = load_session(session_dir)
    with pytest.raises(SessionError, match="pulse 3"):
        s.command_time(3)
    assert s.command_time(4) > 0


def test_pulse_without_tm2_refuses_when_its_time_is_used(session_dir):
    edit_csv(session_dir / "tm2.csv", lambda rows: rows[:5] + rows[6:])
    s = load_session(session_dir)
    with pytest.raises(SessionError, match="pulse 4"):
        s.command_time(4)


def test_week_rollover_keeps_gps_time_continuous(session_dir):
    # Saturday 23:59:42 UTC is GPS week 2445 second 0 (GPS-UTC = 18 s).
    lines = (session_dir / "pvt.csv").read_text().splitlines()
    header = lines[0].split(",")
    template = next(csv.DictReader(lines))
    rows = [header]
    for itow, utc, nano in [
        (604799600, "2026-11-14T23:59:41Z", 600000000),
        (604799800, "2026-11-14T23:59:41Z", 800000000),
        (0, "2026-11-14T23:59:42Z", 0),
        (200, "2026-11-14T23:59:42Z", 200000000),
    ]:
        row = dict(template, iTOW=itow, utc=utc, nano=nano)
        rows.append([row[c] for c in header])
    edit_csv(session_dir / "pvt.csv", lambda _: rows)
    s = load_session(session_dir)
    np.testing.assert_allclose(np.diff(s.pvt.gps_time_s), 0.2, atol=1e-6)
    assert s.pvt.gps_time_s[2] == pytest.approx(2445 * 604800)


def test_pvt_disagreeing_with_itow_refuses(session_dir):
    def edit(rows):
        rows[10][0] = str(int(rows[10][0]) + 1000)
        return rows

    edit_csv(session_dir / "pvt.csv", edit)
    with pytest.raises(SessionError, match="iTOW"):
        load_session(session_dir)


@needs_ft1
def test_full_field_test_1_loads():
    s = load_session(FT1)
    assert len(s.pulses) == 434 and len(s.pvt.gps_time_s) == 1415
    assert len(s.tm2) == 434
    assert np.bincount(s.pvt.carr_soln).tolist() == [3, 252, 1160]


@pytest.mark.parametrize("name", ["pulses.csv", "tm2.csv", "pvt.csv"])
def test_a_truncated_last_row_is_dropped_with_a_warning(session_dir, name):
    path = session_dir / name
    path.write_text(path.read_text().rstrip("\n").rsplit(",", 3)[0] + "\n")
    s = load_session(session_dir)
    assert any(name in w and "truncated" in w for w in s.load_warnings)


def test_a_short_row_before_the_end_refuses(session_dir):
    edit_csv(session_dir / "pvt.csv", lambda rows: rows[:5] + [rows[5][:4]] + rows[6:])
    with pytest.raises(SessionError, match="pvt.csv:6"):
        load_session(session_dir)


@pytest.mark.parametrize(
    ("name", "column", "value", "where"),
    [
        ("pvt.csv", 2, "", "pvt.csv:4"),
        ("pvt.csv", 11, "abc", "pvt.csv:4 velN"),
        ("pulses.csv", 0, "1.5", "pulses.csv:4 pulse_n"),
        ("tm2.csv", 3, "", "tm2.csv:4 towMsR"),
    ],
)
def test_malformed_values_refuse_with_their_location(session_dir, name, column, value, where):
    edit_csv(session_dir / name, set_cells(column, value, [3]))
    with pytest.raises(SessionError, match=where):
        load_session(session_dir)


@pytest.mark.parametrize("name", ["pulses.csv", "pvt.csv"])
def test_files_with_no_rows_refuse(session_dir, name):
    edit_csv(session_dir / name, lambda rows: rows[:1])
    with pytest.raises(SessionError, match=name):
        load_session(session_dir)


def test_missing_files_and_settings_refuse(session_dir):
    meta = json.loads((session_dir / "session.json").read_text())
    del meta["settings"]["nav_period_ms"]
    (session_dir / "session.json").write_text(json.dumps(meta))
    with pytest.raises(SessionError, match="nav_period_ms"):
        load_session(session_dir)
    (session_dir / "session.json").write_text('{"settings": ')
    with pytest.raises(SessionError, match="session.json"):
        load_session(session_dir)


def test_missing_csv_refuses(session_dir):
    (session_dir / "tm2.csv").unlink()
    with pytest.raises(SessionError, match="tm2.csv"):
        load_session(session_dir)


def test_pvt_time_must_increase(session_dir):
    edit_csv(session_dir / "pvt.csv", lambda rows: rows[:10] + [rows[11], rows[10]] + rows[12:])
    with pytest.raises(SessionError, match="pvt.csv:12"):
        load_session(session_dir)


def test_negative_nano_is_the_fraction_before_the_utc_second(session_dir):
    def edit(rows):
        rows[1][1], rows[1][2] = "-599777078", "2026-11-08T15:37:32Z"
        return rows

    edit_csv(session_dir / "pvt.csv", edit)
    s = load_session(session_dir)
    assert s.pvt.gps_time_s[0] % 604800 == pytest.approx(56269.400222922, abs=1e-6)


def test_tm2_week_rollover(session_dir):
    edit_csv(session_dir / "tm2.csv", set_cells(2, "2445", [1]))
    edit_csv(session_dir / "tm2.csv", set_cells(3, "0", [1]))
    edit_csv(session_dir / "tm2.csv", set_cells(5, "2445", [1]))
    edit_csv(session_dir / "tm2.csv", set_cells(6, "50", [1]))
    assert load_session(session_dir).tm2[0].rising_gps_s == pytest.approx(2445 * 604800 + 828193e-9, abs=1e-6)


@pytest.mark.parametrize(
    ("column", "value", "where"),
    [
        (7, "nan", "lat"),
        (7, "100.0", "lat"),
        (8, "-181", "lon"),
        (9, "inf", "height_m"),
        (9, "20000", "height_m"),
        (4, "5", "carrSoln"),
        (3, "9", "fixType"),
        (11, "nan", "velN"),
        (11, "200000", "velN"),
        (14, "-1", "hAcc_mm"),
        (1, "1500000000", "nano"),
        (0, "604800000", "iTOW"),
    ],
)
def test_out_of_range_pvt_values_refuse(session_dir, column, value, where):
    edit_csv(session_dir / "pvt.csv", set_cells(column, value, [5]))
    with pytest.raises(SessionError, match=f"pvt.csv:6 {where}"):
        load_session(session_dir)


@pytest.mark.parametrize(
    ("column", "value", "where"),
    [(4, "1500000000", "towSubMsR"), (3, "-1", "towMsR"), (2, "100", "wnR"), (10, "-3", "accEst_ns")],
)
def test_out_of_range_tm2_values_refuse(session_dir, column, value, where):
    edit_csv(session_dir / "tm2.csv", set_cells(column, value, [5]))
    with pytest.raises(SessionError, match=f"tm2.csv:6 {where}"):
        load_session(session_dir)


def test_a_falling_edge_before_the_rising_edge_refuses(session_dir):
    edit_csv(session_dir / "tm2.csv", set_cells(6, "56271000", [1]))
    with pytest.raises(SessionError, match="tm2.csv:2 falling edge"):
        load_session(session_dir)


@pytest.mark.parametrize("value", ["-5000", "10000", "600000"])
def test_implausible_shutter_delays_refuse(session_dir, value):
    edit_csv(session_dir / "pulses.csv", set_cells(3, value, [3]))
    with pytest.raises(SessionError, match="pulses.csv:4 cam0_delay_us"):
        load_session(session_dir)


def test_a_single_pvt_epoch_refuses(session_dir):
    edit_csv(session_dir / "pvt.csv", lambda rows: rows[:2])
    with pytest.raises(SessionError, match="at least two"):
        load_session(session_dir)


def test_settings_must_be_a_table_with_a_positive_nav_period(session_dir):
    meta = json.loads((session_dir / "session.json").read_text())
    meta["settings"]["nav_period_ms"] = 0
    (session_dir / "session.json").write_text(json.dumps(meta))
    with pytest.raises(SessionError, match="nav_period_ms"):
        load_session(session_dir)
    meta["settings"] = []
    (session_dir / "session.json").write_text(json.dumps(meta))
    with pytest.raises(SessionError, match="settings"):
        load_session(session_dir)
