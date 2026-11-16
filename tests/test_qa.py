import json
from dataclasses import replace

import numpy as np
import pytest
from test_path import FRAME, profile_for

from georoute import qa
from georoute.package import cut_segments
from georoute.qa import (
    RTK_GAP_LIMIT_M,
    THRESHOLDS_VERSION,
    QAError,
    assess,
    write_plots,
    write_qa,
)


def clean_route(length=100.0, segment_length=100.0):
    path, profile = profile_for(np.arange(0.0, length + 0.5, 0.5))
    return path, profile, cut_segments(path, profile, FRAME, segment_length)


def test_a_clean_route_passes_every_gate_and_reports_its_thresholds():
    path, profile, route = clean_route()

    report = assess(path, profile, route, {"failures": []}, "route", "build")

    assert report.status == "pass"
    assert report.recapture == []
    document = report.document()
    assert document["schema"] == "georoute.qa/1.0.0"
    assert document["thresholds"]["version"] == THRESHOLDS_VERSION
    assert document["thresholds"]["measure"] is True
    assert document["thresholds"]["path_lateral_p95_m"] == 0.10
    assert document["thresholds"]["path_lateral_max_m"] == 0.30
    assert [gate["name"] for gate in document["segments"][0]["gates"]] == [
        "path-to-road",
        "comfort",
        "rtk-gaps",
        "registration",
    ]
    assert report.summary()["segments"] == {"seg_0000": "pass"}


def test_path_to_road_fails_a_segment_when_the_smoothed_path_departs_vertically():
    path, profile, route = clean_route()
    trace = path.trace_enu_m.copy()
    trace[:, 2] += 0.05

    report = assess(replace(path, trace_enu_m=trace), profile, route, {"failures": []}, "route", "build")

    assert report.status == "fail"
    gate = report.segments[0].gates[0]
    assert gate.name == "path-to-road"
    assert gate.status == "fail"
    assert gate.values["vertical_p95_m"] == pytest.approx(0.05, abs=1e-6)
    assert "seg_0000 path to road" in gate.detail


def test_comfort_fails_on_a_curvature_step_at_design_speed():
    path, profile, route = clean_route()
    curvature = path.curvature_1pm.copy()
    curvature[path.s_m > 10.0] = 0.01

    report = assess(
        replace(path, curvature_1pm=curvature), profile, route, {"failures": []}, "route", "build"
    )

    gate = report.segments[0].gates[1]
    assert gate.name == "comfort"
    assert gate.status == "fail"
    assert gate.values["max_yaw_accel_deg_s2"] > 30.0


def test_a_long_float_run_is_a_warning_when_registered_cameras_carry_the_path():
    path, profile, route = clean_route()
    quality = [
        ("rtk-float",) if s <= RTK_GAP_LIMIT_M + 10.0 else flags
        for s, flags in zip(profile.s_m, profile.quality, strict=True)
    ]

    report = assess(path, replace(profile, quality=tuple(quality)), route, {"failures": []}, "route", "build")

    gate = report.segments[0].gates[2]
    assert gate.name == "rtk-gaps"
    assert gate.status == "warn"
    assert gate.values["longest_non_fixed_m"] == pytest.approx(61.0)
    assert "seg_0000 RTK gaps" in gate.detail
    assert report.status == "warn"
    assert report.recapture == []


def test_a_float_run_split_by_a_segment_boundary_keeps_its_full_length_in_both_segments():
    path, profile, route = clean_route(length=200.0, segment_length=100.0)
    quality = [
        ("rtk-float",) if 50.0 <= s <= 150.0 else flags
        for s, flags in zip(profile.s_m, profile.quality, strict=True)
    ]

    report = assess(path, replace(profile, quality=tuple(quality)), route, {"failures": []}, "route", "build")

    for segment in report.segments:
        gate = segment.gates[2]
        assert gate.status == "warn"
        assert gate.values["longest_non_fixed_m"] == pytest.approx(101.0)
        assert len(gate.values["non_fixed_runs"]) == 1


def test_rtk_gaps_fail_a_grade_step_across_a_bridged_gap():
    path, profile, route = clean_route()
    quality = [
        ("bridged-gap",) if 20.0 <= s <= 30.0 else flags
        for s, flags in zip(profile.s_m, profile.quality, strict=True)
    ]
    grade = np.where(profile.s_m <= 25.0, 0.0, 1.0)

    report = assess(
        path,
        replace(profile, quality=tuple(quality), grade_percent=grade),
        route,
        {"failures": []},
        "route",
        "build",
    )

    gate = report.segments[0].gates[2]
    assert gate.status == "fail"
    assert gate.values["bridged_grade_step_percent"] == pytest.approx(1.0, abs=1e-9)
    assert gate.values["max_bridged_gap_m"] == pytest.approx(11.0)
    assert gate.values["bridged_runs"][0]["pulses"][0] < gate.values["bridged_runs"][0]["pulses"][-1]
    assert "grade step" in gate.detail
    assert "bridged gap" in gate.detail


def test_a_lateral_max_over_30_cm_fails_even_when_the_p95_is_inside_its_bound():
    path, profile, route = clean_route()
    trace = path.trace_enu_m.copy()
    trace[100, 1] += 0.40

    report = assess(replace(path, trace_enu_m=trace), profile, route, {"failures": []}, "r", "b")

    gate = report.segments[0].gates[0]
    assert gate.status == "fail"
    assert gate.values["lateral_max_m"] == pytest.approx(0.40, abs=1e-6)
    assert gate.values["lateral_p95_m"] < 0.10


def test_a_bridged_gap_over_ten_metres_fails_and_reports_its_pulses():
    path, profile, route = clean_route()
    quality = [
        ("bridged-gap",) if 20.0 <= s <= 35.0 else flags
        for s, flags in zip(profile.s_m, profile.quality, strict=True)
    ]

    report = assess(path, replace(profile, quality=tuple(quality)), route, {"failures": []}, "route", "build")

    gate = report.segments[0].gates[2]
    run = gate.values["bridged_runs"][0]
    assert gate.status == "fail"
    assert gate.values["max_bridged_gap_m"] == pytest.approx(16.0)
    assert run["pulses"][0] < run["pulses"][-1]
    assert f"pulses {run['pulses'][0]}-{run['pulses'][-1]}" in gate.detail


def test_a_long_non_fixed_run_containing_a_short_bridged_gap_fails():
    path, profile, route = clean_route()
    quality = []
    for s, flags in zip(profile.s_m, profile.quality, strict=True):
        if 40.0 <= s <= 45.0:
            quality.append(("rtk-float", "bridged-gap"))
        elif 10.0 <= s <= 80.0:
            quality.append(("rtk-float",))
        else:
            quality.append(flags)

    report = assess(path, replace(profile, quality=tuple(quality)), route, {"failures": []}, "route", "build")

    gate = report.segments[0].gates[2]
    assert gate.status == "fail"
    assert "contains a bridged gap" in gate.detail
    assert gate.values["max_bridged_gap_m"] < 10.0


def test_a_slice_6_registration_failure_marks_every_segment_for_recapture():
    path, profile, route = clean_route(segment_length=40.0)

    report = assess(path, profile, route, {"failures": ["cam0 horizontal p95 5.2 cm"]}, "route", "build")

    assert report.status == "fail"
    assert report.recapture == ["seg_0000", "seg_0001", "seg_0002"]
    assert all(segment.gates[3].name == "registration" for segment in report.segments)
    assert all(segment.gates[3].status == "fail" for segment in report.segments)


def test_a_missing_registration_report_refuses_rather_than_passing_by_default():
    path, profile, route = clean_route()

    with pytest.raises(QAError, match="colmap-report"):
        assess(path, profile, route, {}, "route", "build")


def test_the_qa_report_is_written_even_when_a_gate_fails(tmp_path):
    path, profile, route = clean_route()
    trace = path.trace_enu_m.copy()
    trace[:, 2] += 0.05
    report = assess(replace(path, trace_enu_m=trace), profile, route, {"failures": []}, "r", "b")

    folder = write_qa(tmp_path, report)

    document = json.loads((folder / "report.json").read_text())
    assert document["status"] == "fail"
    assert document["recapture"] == ["seg_0000"]
    assert folder == tmp_path / "qa" / "r" / "b"


def test_the_review_plots_are_written(tmp_path):
    path, profile, _ = clean_route()

    plots = write_plots(tmp_path, path, profile)

    for name in ("plan", "profile", "curvature"):
        assert plots[name] == f"{name}.png"
        assert (tmp_path / f"{name}.png").stat().st_size > 1000


def test_the_qa_summary_is_small_enough_for_the_route_manifest():
    path, profile, route = clean_route()

    summary = assess(path, profile, route, {"failures": []}, "route", "build").summary()

    assert summary == {
        "status": "pass",
        "thresholds_version": THRESHOLDS_VERSION,
        "recapture": [],
        "segments": {"seg_0000": "pass"},
    }


def test_a_plot_failure_still_writes_the_qa_report(tmp_path, monkeypatch):
    path, profile, route = clean_route()
    report = assess(path, profile, route, {"failures": []}, "route", "build")

    def no_plots(*args):
        raise RuntimeError("matplotlib unavailable")

    monkeypatch.setattr(qa, "write_plots", no_plots)
    folder = write_qa(tmp_path, report, path, profile)

    document = json.loads((folder / "report.json").read_text())
    assert document["status"] == "pass"
    assert document["plots"] == {"error": "matplotlib unavailable"}
