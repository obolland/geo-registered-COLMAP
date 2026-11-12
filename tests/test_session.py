import csv
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from georoute.session import SessionError, load_session

# GPS times are float64 seconds since 1980: resolution about 0.24 us, i.e. micrometres at riding speed.
FIXTURE = Path(__file__).parent / "fixtures" / "ft1_trim"
FT1 = Path(
    os.environ.get("GEOROUTE_FT1", Path(__file__).parents[2] / "gps-camera-trigger/samples/20261108T153731Z")
)


@pytest.fixture
def session_dir(tmp_path):
    d = tmp_path / "20261108T153731Z"
    shutil.copytree(FIXTURE, d)
    return d


def _edit_csv(path, edit):
    rows = list(csv.reader(path.read_text().splitlines()))
    rows = edit(rows)
    with open(path, "w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)


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
    _edit_csv(session_dir / name, lambda rows: [rows[0][:-1] + ["surprise"]] + rows[1:])
    with pytest.raises(SessionError, match=name):
        load_session(session_dir)


def test_duplicate_pulse_refuses(session_dir):
    _edit_csv(session_dir / "pulses.csv", lambda rows: rows + [rows[-1]])
    with pytest.raises(SessionError, match="pulse 19"):
        load_session(session_dir)


@pytest.mark.parametrize(("column", "value"), [(8, "0"), (9, "0")], ids=["timeBase-utc", "timeValid-0"])
def test_invalid_tm2_refuses_when_its_time_is_used(session_dir, column, value):
    def edit(rows):
        rows[4][column] = value
        return rows

    _edit_csv(session_dir / "tm2.csv", edit)
    s = load_session(session_dir)
    with pytest.raises(SessionError, match="pulse 3"):
        s.command_time(3)
    assert s.command_time(4) > 0


def test_pulse_without_tm2_refuses_when_its_time_is_used(session_dir):
    _edit_csv(session_dir / "tm2.csv", lambda rows: rows[:5] + rows[6:])
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
    _edit_csv(session_dir / "pvt.csv", lambda _: rows)
    s = load_session(session_dir)
    np.testing.assert_allclose(np.diff(s.pvt.gps_time_s), 0.2, atol=1e-6)
    assert s.pvt.gps_time_s[2] == pytest.approx(2445 * 604800)


def test_pvt_disagreeing_with_itow_refuses(session_dir):
    def edit(rows):
        rows[10][0] = str(int(rows[10][0]) + 1000)
        return rows

    _edit_csv(session_dir / "pvt.csv", edit)
    with pytest.raises(SessionError, match="iTOW"):
        load_session(session_dir)


@pytest.mark.skipif(not FT1.exists(), reason="field test 1 sample not available")
def test_full_field_test_1_loads():
    s = load_session(FT1)
    assert len(s.pulses) == 434 and len(s.pvt.gps_time_s) == 1415
    assert sum(t is not None for t in s.tm2.values()) == 434
    assert np.bincount(s.pvt.carr_soln).tolist() == [3, 252, 1160]
