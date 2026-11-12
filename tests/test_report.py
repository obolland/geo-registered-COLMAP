import shutil

import pytest
from test_session import FIXTURE, FT1, _edit_csv

from georoute.cli import main
from georoute.report import session_report
from georoute.session import load_session


@pytest.fixture
def session_dir(tmp_path):
    d = tmp_path / "s"
    shutil.copytree(FIXTURE, d)
    return d


def _set(column, value, rows_idx):
    def edit(rows):
        for i in rows_idx:
            rows[i][column] = value
        return rows

    return edit


def test_trimmed_session_has_no_errors_and_flags_logger_counters(session_dir):
    r = session_report(load_session(session_dir))
    assert r["errors"] == []
    assert r["photos_expected"]["cam0"] == {"warmup": 1, "capture": 18}
    assert r["photos_expected"]["cam1"] == {"warmup": 0, "capture": 0}
    assert any("rx_ring_lost" in w for w in r["warnings"])
    assert any("missed_epochs" in w and "1" in w for w in r["warnings"])


def test_invalid_time_mark_on_a_photographed_pulse_is_an_error(session_dir):
    _edit_csv(session_dir / "tm2.csv", _set(9, "0", [5]))
    r = session_report(load_session(session_dir))
    assert any("pulse 4" in e for e in r["errors"])


def test_invalid_time_mark_on_an_unphotographed_pulse_is_a_warning(session_dir):
    _edit_csv(session_dir / "tm2.csv", _set(9, "0", [1]))
    r = session_report(load_session(session_dir))
    assert r["errors"] == []
    assert any("pulse 0" in w for w in r["warnings"])


def test_epoch_gaps_are_counted_from_the_data(session_dir):
    _edit_csv(session_dir / "pvt.csv", lambda rows: rows[:20] + rows[22:])
    r = session_report(load_session(session_dir))
    assert r["pvt"]["missed_epochs"] == 2
    assert r["pvt"]["largest_gap_s"] == pytest.approx(0.6, abs=1e-6)


def test_longest_non_fixed_run_in_time_and_distance(session_dir):
    _edit_csv(session_dir / "pvt.csv", _set(4, "1", range(11, 16)))
    r = session_report(load_session(session_dir))
    run = r["pvt"]["longest_non_fixed_run"]
    assert run["epochs"] == 5 and run["seconds"] == pytest.approx(1.0)
    assert 0.5 < run["metres"] < 2.0


def test_pulses_outside_the_track_are_errors(session_dir):
    _edit_csv(session_dir / "pvt.csv", lambda rows: rows[:40])
    r = session_report(load_session(session_dir))
    assert any("outside the PVT track" in e for e in r["errors"])


def test_cli_exit_code_follows_errors(session_dir, capsys):
    assert main(["session-report", str(session_dir)]) == 0
    assert "pulses 20" in capsys.readouterr().out
    _edit_csv(session_dir / "tm2.csv", _set(9, "0", [5]))
    assert main(["session-report", str(session_dir)]) == 1


@pytest.mark.skipif(not FT1.exists(), reason="field test 1 sample not available")
def test_full_field_test_1_report():
    r = session_report(load_session(FT1))
    assert r["errors"] == []
    assert r["pulses"] == {"total": 434, "warmup": 2, "capture": 432, "with_tm2": 434}
    assert r["photos_expected"]["cam0"] == {"warmup": 1, "capture": 431}
    assert r["pvt"]["fix"] == {"fixed": 1160, "float": 252, "none": 3}
    assert r["pvt"]["missed_epochs"] == 0
    assert r["pvt"]["longest_non_fixed_run"]["epochs"] == 103
    assert any("missed_epochs" in w for w in r["warnings"])
