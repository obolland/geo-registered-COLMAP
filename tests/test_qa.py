import json
from dataclasses import replace

import numpy as np
import pytest
from test_path import FRAME, SyntheticGeoid, contact_at, make_images, profile_for

from georoute import qa
from georoute.package import cut_segments
from georoute.path import distance_profile, rider_path
from georoute.qa import (
    DESIGN_SPEED_MPS,
    LONGITUDINAL_ACCEL_LIMIT_MPS2,
    RTK_GAP_LIMIT_M,
    THRESHOLDS_VERSION,
    QAError,
    _speed_profile,
    _yaw_acceleration,
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
    assert document["thresholds"]["path_lateral_max_m"] == 0.50
    assert document["thresholds"]["longitudinal_accel_mps2"] == 1.5
    assert [gate["name"] for gate in document["segments"][0]["gates"]] == [
        "path-to-road",
        "comfort",
        "rtk-gaps",
        "registration",
    ]
    assert report.summary()["segments"] == {"seg_0000": "pass"}


def test_a_trace_longer_than_the_smoothed_path_still_assesses():
    x = np.arange(0.0, 100.0, 0.5)
    weave = 0.5 * np.sin(2 * np.pi * x / 5.0)
    contacts = [contact_at(i, [e, float(n), 0.0]) for i, (e, n) in enumerate(zip(x, weave, strict=True))]
    path = rider_path(contacts, FRAME)
    assert path.trace_parameter_m[-1] > path.length_m
    profile = distance_profile(path, make_images(range(len(contacts))), FRAME, SyntheticGeoid())
    route = cut_segments(path, profile, FRAME, 30.0)

    report = assess(path, profile, route, {"failures": []}, "route", "build")

    assert len(report.segments) == 4


def test_a_trace_sample_is_placed_by_smoothed_arclength_not_raw_distance():
    x = np.arange(0.0, 60.0, 0.5)
    weave = 0.8 * np.sin(2 * np.pi * x / 4.0)
    contacts = [contact_at(i, [e, float(n), 0.0]) for i, (e, n) in enumerate(zip(x, weave, strict=True))]
    path = rider_path(contacts, FRAME)
    index = next(
        i
        for i, (raw, smoothed) in enumerate(zip(path.trace_parameter_m, path.trace_smoothed_m, strict=True))
        if raw > 30.0 and smoothed < 29.5
    )
    images = make_images(range(len(contacts)))
    for image in images:
        image["carr_soln"] = 1 if image["pulse"] == index else 2
    profile = distance_profile(path, images, FRAME, SyntheticGeoid())
    route = cut_segments(path, profile, FRAME, 30.0)

    flagged = [s for s, quality in zip(profile.s_m, profile.quality, strict=True) if "rtk-float" in quality]
    assert flagged
    assert all(abs(s - path.trace_smoothed_m[index]) <= 1.0 for s in flagged)
    assert path.trace_parameter_m[index] > 30.0

    def segment_for(s):
        return next(segment for segment in route.segments if segment.s_start <= s <= segment.s_end)

    assert "rtk-float" in segment_for(path.trace_smoothed_m[index]).quality
    assert "rtk-float" not in segment_for(path.trace_parameter_m[index]).quality


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


class CurvaturePath:
    def __init__(self, s, curvature):
        self.s_m = s
        self.curvature_1pm = curvature


def test_a_clothoid_entry_has_the_analytic_yaw_acceleration():
    s = np.arange(0.0, 201.0, 0.1)
    radius, clothoid = 150.0, 30.0
    curvature = np.where(s < clothoid, (1.0 / radius) * (s / clothoid), 1.0 / radius)
    expected = np.degrees(DESIGN_SPEED_MPS**2 * (1.0 / radius) / clothoid)

    yaw = _yaw_acceleration(CurvaturePath(s, curvature))

    assert yaw[(s > 5.0) & (s < clothoid - 5.0)].max() == pytest.approx(expected, rel=0.02)


def test_a_zero_crossing_beside_a_tight_bend_does_not_use_design_speed():
    s = np.arange(0.0, 30.0, 0.05)
    curvature = np.zeros_like(s)
    entry = (s >= 9.0) & (s < 10.0)
    curvature[entry] = -0.31 * (s[entry] - 9.0)
    ramp = (s >= 10.0) & (s < 11.0)
    curvature[ramp] = -0.31 + 0.40 * (s[ramp] - 10.0)
    curvature[(s >= 11.0) & (s < 12.0)] = 0.09
    exit_ramp = (s >= 12.0) & (s < 13.0)
    curvature[exit_ramp] = 0.09 * (13.0 - s[exit_ramp])
    path = CurvaturePath(s, curvature)

    speed = _speed_profile(s, curvature)
    yaw = _yaw_acceleration(path)

    near = (s >= 9.5) & (s <= 12.5)
    assert speed[near].max() < 8.0
    assert yaw[near].max() < 1000.0
    assert speed[np.argmax(yaw)] < 10.0
    steps = np.diff(speed**2)
    assert steps.max() <= 2 * LONGITUDINAL_ACCEL_LIMIT_MPS2 * np.diff(s).max() + 1e-6
    assert (-steps).max() <= 2 * LONGITUDINAL_ACCEL_LIMIT_MPS2 * np.diff(s).max() + 1e-6


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
    assert gate.values["s_at_max_m"] > 0.0


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


def test_a_lateral_max_over_50_cm_fails_the_path_gate():
    path, profile, route = clean_route()
    trace = path.trace_enu_m.copy()
    trace[100, 1] += 0.60

    report = assess(replace(path, trace_enu_m=trace), profile, route, {"failures": []}, "r", "b")

    gate = report.segments[0].gates[0]
    assert gate.status == "fail"
    assert gate.values["lateral_max_m"] == pytest.approx(0.60, abs=1e-6)


def test_lateral_p95_is_reported_but_is_not_a_pass_fail_gate():
    path, profile, route = clean_route()
    trace = path.trace_enu_m.copy()
    trace[:, 1] += 0.20

    report = assess(replace(path, trace_enu_m=trace), profile, route, {"failures": []}, "r", "b")

    gate = report.segments[0].gates[0]
    assert gate.status == "pass"
    assert gate.values["lateral_p95_m"] == pytest.approx(0.20, abs=1e-6)
    assert gate.values["lateral_max_m"] == pytest.approx(0.20, abs=1e-6)


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


def test_the_comfort_gate_reports_a_bend_that_cannot_meet_the_cap():
    x = np.arange(0.0, 40.0, 0.2)
    bend = np.zeros_like(x)
    mask = (x >= 18.5) & (x <= 21.5)
    bend[mask] = 0.6 * np.sin(np.pi * (x[mask] - 18.5) / 3.0) ** 2
    contacts = [contact_at(i, [e, float(n), 0.0]) for i, (e, n) in enumerate(zip(x, bend, strict=True))]
    path = rider_path(contacts, FRAME)
    profile = distance_profile(path, make_images(range(len(contacts))), FRAME, SyntheticGeoid())
    route = cut_segments(path, profile, FRAME, 100.0)

    report = assess(path, profile, route, {"failures": []}, "route", "build")

    comfort = report.segments[0].gates[1]
    assert path.smoothing["horizontal"]["comfort_met"] is False
    assert path.smoothing["horizontal"]["max_lateral_m"] <= 0.5
    assert comfort.status == "fail"
    assert comfort.values["max_yaw_accel_deg_s2"] > 30.0
    assert comfort.values["s_at_max_m"] == pytest.approx(
        path.smoothing["horizontal"]["s_at_max_yaw_m"], abs=1e-6
    )
    assert "comfort:" in comfort.detail
    assert report.segments[0].gates[0].status == "pass"


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
    path, profile, route = clean_route()

    plots = write_plots(tmp_path, path, profile, route)

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
