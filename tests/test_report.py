import json

import pytest
from conftest import edit_csv, needs_ft1, set_cells

from georoute.cli import main
from georoute.report import session_report
from georoute.session import load_session


def test_trimmed_session_has_no_errors_and_flags_logger_counters(session_dir):
    r = session_report(load_session(session_dir))
    assert r["errors"] == []
    assert r["photos_expected"]["cam0"] == {"warmup": 1, "capture": 18}
    assert r["photos_expected"]["cam1"] == {"warmup": 0, "capture": 0}
    assert any("rx_ring_lost" in w for w in r["warnings"])
    assert "session.json says missed_epochs 1, the data has 0" in r["warnings"]


def test_invalid_time_mark_on_a_photographed_pulse_is_an_error(session_dir):
    edit_csv(session_dir / "tm2.csv", set_cells(9, "0", [5]))
    r = session_report(load_session(session_dir))
    assert any("pulse 4" in e for e in r["errors"])


def test_invalid_time_mark_on_an_unphotographed_pulse_is_a_warning(session_dir):
    edit_csv(session_dir / "tm2.csv", set_cells(9, "0", [1]))
    r = session_report(load_session(session_dir))
    assert r["errors"] == []
    assert any("pulse 0" in w for w in r["warnings"])


def test_epoch_gaps_are_counted_from_the_data(session_dir):
    edit_csv(session_dir / "pvt.csv", lambda rows: rows[:20] + rows[22:])
    r = session_report(load_session(session_dir))
    assert r["pvt"]["missed_epochs"] == 2
    assert r["pvt"]["largest_gap_s"] == pytest.approx(0.6, abs=1e-6)


def test_longest_non_fixed_run_in_time_and_distance(session_dir):
    edit_csv(session_dir / "pvt.csv", set_cells(4, "1", range(11, 16)))
    r = session_report(load_session(session_dir))
    run = r["pvt"]["longest_non_fixed_run"]
    assert run["epochs"] == 5 and run["seconds"] == pytest.approx(1.0)
    assert 0.5 < run["metres"] < 2.0


def test_pulses_outside_the_track_are_errors(session_dir):
    edit_csv(session_dir / "pvt.csv", lambda rows: rows[:40])
    r = session_report(load_session(session_dir))
    assert any("outside the PVT track" in e for e in r["errors"])


def test_cli_exit_code_follows_errors(session_dir, capsys):
    assert main(["session-report", str(session_dir)]) == 0
    assert "pulses 20" in capsys.readouterr().out
    edit_csv(session_dir / "tm2.csv", set_cells(9, "0", [5]))
    assert main(["session-report", str(session_dir)]) == 1


@needs_ft1
def test_full_field_test_1_report():
    from conftest import FT1

    r = session_report(load_session(FT1))
    assert r["errors"] == []
    assert r["pulses"] == {"total": 434, "warmup": 2, "capture": 432, "with_tm2": 434}
    assert r["photos_expected"]["cam0"] == {"warmup": 1, "capture": 431}
    assert r["pvt"]["fix"] == {"fixed": 1160, "float": 252, "none": 3}
    assert r["pvt"]["missed_epochs"] == 0
    assert r["pvt"]["longest_non_fixed_run"]["epochs"] == 103
    assert "session.json says missed_epochs 1, the data has 0" in r["warnings"]


def test_tm2_without_a_pulse_is_an_error(session_dir):
    edit_csv(session_dir / "tm2.csv", lambda rows: rows + [["99"] + rows[-1][1:]])
    r = session_report(load_session(session_dir))
    assert any("pulse 99" in e for e in r["errors"])


def test_pulse_numbers_with_a_hole_warn(session_dir):
    edit_csv(session_dir / "pulses.csv", lambda rows: rows[:8] + rows[9:])
    r = session_report(load_session(session_dir))
    assert any("pulse numbers" in w for w in r["warnings"])


def test_pulse_numbers_out_of_order_are_an_error(session_dir):
    edit_csv(session_dir / "pulses.csv", lambda rows: [rows[0], rows[2], rows[1]] + rows[3:])
    r = session_report(load_session(session_dir))
    assert any("out of order" in e for e in r["errors"])


def test_run_distance_spans_a_missed_epoch(session_dir):
    edit_csv(session_dir / "pvt.csv", set_cells(4, "1", range(11, 17)))
    full = session_report(load_session(session_dir))["pvt"]["longest_non_fixed_run"]
    edit_csv(session_dir / "pvt.csv", lambda rows: rows[:13] + rows[14:])
    gapped = session_report(load_session(session_dir))["pvt"]["longest_non_fixed_run"]
    assert gapped["epochs"] == 5 and gapped["seconds"] == pytest.approx(full["seconds"])
    assert gapped["metres"] == pytest.approx(full["metres"], rel=0.1)


def test_lost_pulse_rows_are_an_error(session_dir):
    meta = json.loads((session_dir / "session.json").read_text())
    meta["counts"]["pulses_lost"] = 2
    (session_dir / "session.json").write_text(json.dumps(meta))
    r = session_report(load_session(session_dir))
    assert any("pulses_lost" in e for e in r["errors"])


def test_unclean_stop_skips_count_comparisons(session_dir):
    meta = json.loads((session_dir / "session.json").read_text())
    meta.update(stop_utc=None, updated_utc="2026-11-08T15:38:00Z")
    (session_dir / "session.json").write_text(json.dumps(meta))
    r = session_report(load_session(session_dir))
    assert not any("session.json says" in w for w in r["warnings"])


def test_cli_distinguishes_unreadable_sessions_and_writes_json(session_dir, tmp_path):
    out = tmp_path / "r.json"
    assert main(["session-report", str(session_dir), "--json", str(out)]) == 0
    assert json.loads(out.read_text())["pulses"]["total"] == 20
    (session_dir / "pvt.csv").unlink()
    assert main(["session-report", str(session_dir)]) == 2


def _shift_east(rows, from_row, metres=1.5):
    for r in rows[from_row:]:
        r[8] = f"{float(r[8]) + metres / (111320 * 0.6269):.7f}"
    return rows


def test_a_position_step_the_velocity_does_not_explain_is_reported(session_dir):
    edit_csv(session_dir / "pvt.csv", lambda rows: _shift_east(rows, 44))
    r = session_report(load_session(session_dir))
    assert r["pvt"]["position_jumps"]["count"] == 1
    assert any("position jumps" in w for w in r["warnings"])


def test_a_moving_base_station_is_an_error(session_dir):
    from georoute.rtcm import encode_1005

    (session_dir / "rtcm.bin").write_bytes(
        encode_1005(3527, (4006364.0, -294184.9, 4937580.8))
        + encode_1005(3527, (4006370.0, -294184.9, 4937580.8))
    )
    r = session_report(load_session(session_dir))
    assert any("base station moved" in e for e in r["errors"])
    assert len(r["base"]) == 2


def test_a_missing_corrections_log_warns(session_dir):
    r = session_report(load_session(session_dir))
    assert any("rtcm.bin" in w for w in r["warnings"]) and r["base"] == []


@needs_ft1
def test_field_test_1_jumps_and_base():
    from conftest import FT1

    r = session_report(load_session(FT1))
    assert r["pvt"]["position_jumps"]["count"] == 1
    assert [b["station"] for b in r["base"]] == [3527]
