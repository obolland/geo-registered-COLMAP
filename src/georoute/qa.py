"""Offline pipeline QA: numeric, per-segment gates with a versioned threshold set, a report written even
when a gate fails, and the review plots. Runtime QA (frame time, LOD popping, re-base hitches) is separate."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from georoute.package import SegmentedRoute
from georoute.path import DistanceProfile, PathError, RiderPath

QA_SCHEMA = "georoute.qa/1.0.0"
THRESHOLDS_VERSION = "1.0.0"
PATH_VERTICAL_P95_M = 0.03
PATH_LATERAL_P95_M = 0.10
PATH_LATERAL_MAX_M = 0.30
YAW_ACCEL_LIMIT_DEG_S2 = 30.0
DESIGN_SPEED_MPS = 18.0
LATERAL_ACCEL_LIMIT_MPS2 = 3.0
RTK_GAP_LIMIT_M = 50.0
BRIDGED_GAP_MAX_M = 10.0
BRIDGED_GRADE_LIMIT_PERCENT = 0.5


class QAError(ValueError):
    pass


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str
    severity: str
    values: dict
    thresholds: dict
    detail: str = ""

    def document(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "severity": self.severity,
            "values": self.values,
            "thresholds": self.thresholds,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SegmentQA:
    seg_id: str
    s_start: float
    s_end: float
    gates: tuple[GateResult, ...]

    @property
    def status(self) -> str:
        if any(gate.status == "fail" for gate in self.gates):
            return "fail"
        return "warn" if any(gate.status == "warn" for gate in self.gates) else "pass"

    def document(self) -> dict:
        return {
            "seg_id": self.seg_id,
            "s_range": [self.s_start, self.s_end],
            "status": self.status,
            "gates": [gate.document() for gate in self.gates],
        }


@dataclass(frozen=True)
class QAReport:
    route_id: str
    build_id: str
    generated_utc: str
    registration: dict
    segments: tuple[SegmentQA, ...]
    plots: dict
    checks: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if any(segment.status == "fail" for segment in self.segments):
            return "fail"
        return "warn" if any(segment.status == "warn" for segment in self.segments) else "pass"

    @property
    def recapture(self) -> list[str]:
        return [segment.seg_id for segment in self.segments if segment.status == "fail"]

    def thresholds(self) -> dict:
        return {
            "version": THRESHOLDS_VERSION,
            "measure": True,
            "measure_fields": [
                "path_vertical_p95_m",
                "path_lateral_p95_m",
                "path_lateral_max_m",
                "yaw_accel_deg_s2",
                "rtk_gap_m",
                "bridged_gap_m",
                "bridged_grade_percent",
            ],
            "path_vertical_p95_m": PATH_VERTICAL_P95_M,
            "path_lateral_p95_m": PATH_LATERAL_P95_M,
            "path_lateral_max_m": PATH_LATERAL_MAX_M,
            "yaw_accel_deg_s2": YAW_ACCEL_LIMIT_DEG_S2,
            "design_speed_mps": DESIGN_SPEED_MPS,
            "lateral_accel_limit_mps2": LATERAL_ACCEL_LIMIT_MPS2,
            "rtk_gap_m": RTK_GAP_LIMIT_M,
            "bridged_gap_m": BRIDGED_GAP_MAX_M,
            "bridged_grade_percent": BRIDGED_GRADE_LIMIT_PERCENT,
        }

    def summary(self) -> dict:
        return {
            "status": self.status,
            "thresholds_version": THRESHOLDS_VERSION,
            "recapture": self.recapture,
            "segments": {segment.seg_id: segment.status for segment in self.segments},
        }

    def document(self) -> dict:
        return {
            "schema": QA_SCHEMA,
            "route_id": self.route_id,
            "build_id": self.build_id,
            "generated_utc": self.generated_utc,
            "thresholds": self.thresholds(),
            "registration": self.registration,
            "segments": [segment.document() for segment in self.segments],
            "recapture": self.recapture,
            "status": self.status,
            "plots": self.plots,
            "checks": self.checks,
        }


def assess(
    path: RiderPath,
    profile: DistanceProfile,
    route: SegmentedRoute,
    registration: dict,
    route_id: str,
    build_id: str,
    generated_utc: str | None = None,
    checks: dict | None = None,
) -> QAReport:
    """Runs the offline gates over the global path and profile, then assigns the results to segments. The
    global arrays are used so a gate can't be introduced by slicing at a boundary."""
    failures = _registration_failures(registration)
    lateral, vertical = _trace_deviations(path)
    yaw = _yaw_acceleration(path)
    nonfixed = _quality_runs(profile, path, ("rtk-float", "rtk-none"))
    bridged = _quality_runs(profile, path, ("bridged-gap",))
    segments = []
    for segment in route.segments:
        gate_results = (
            _path_gate(segment, path, lateral, vertical),
            _comfort_gate(segment, path, yaw),
            _rtk_gate(segment, nonfixed, bridged),
            _registration_gate(failures),
        )
        segments.append(SegmentQA(segment.seg_id, segment.s_start, segment.s_end, gate_results))
    generated = generated_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return QAReport(
        route_id,
        build_id,
        generated,
        {"source": "colmap report", "failures": failures, "pass": not failures},
        tuple(segments),
        {},
        checks or {},
    )


def _registration_failures(registration: dict) -> list[str]:
    if not isinstance(registration, dict) or "failures" not in registration:
        raise QAError(
            "the slice 6 registration report has no 'failures'; run "
            "`georoute colmap-report WORKSPACE --rig RIG`"
        )
    return [str(failure) for failure in registration["failures"]]


def _trace_deviations(path: RiderPath):
    """Lateral and vertical distance from each raw road contact to the smoothed spline at that contact's
    parameter. Lateral is perpendicular to the smoothed tangent, not a plain coordinate difference. The
    spline's own value is used because a trace distance is not a smoothed-path arclength when weave is
    removed."""
    delta = path.trace_fit_enu_m[:, :2] - path.trace_enu_m[:, :2]
    tangent_e = np.cos(path.trace_heading_rad)
    tangent_n = np.sin(path.trace_heading_rad)
    lateral = np.abs(delta[:, 0] * tangent_n - delta[:, 1] * tangent_e)
    vertical = np.abs(path.trace_fit_enu_m[:, 2] - path.trace_enu_m[:, 2])
    return lateral, vertical


def _yaw_acceleration(path: RiderPath) -> np.ndarray:
    """Yaw acceleration at design speed, with the speed capped in bends by the lateral acceleration limit so
    the app's bend policy, not the geometry alone, decides whether a turn is comfortable."""
    derivative = np.gradient(path.curvature_1pm, path.s_m)
    speed = np.full_like(path.curvature_1pm, DESIGN_SPEED_MPS)
    bend = np.abs(path.curvature_1pm) > 1e-12
    speed[bend] = np.minimum(
        DESIGN_SPEED_MPS, np.sqrt(LATERAL_ACCEL_LIMIT_MPS2 / np.abs(path.curvature_1pm[bend]))
    )
    return np.degrees(speed**2 * np.abs(derivative))


def _segment_mask(s, start, end):
    mask = (s >= start - 1e-9) & (s <= end + 1e-9)
    if not mask.any():
        raise PathError(f"no samples between s={start:.3f} and {end:.3f} for QA")
    return mask


def _path_gate(segment, path, lateral, vertical):
    mask = _segment_mask(path.trace_smoothed_m, segment.s_start, segment.s_end)
    lateral_p95 = float(np.percentile(lateral[mask], 95))
    lateral_max = float(lateral[mask].max())
    vertical_p95 = float(np.percentile(vertical[mask], 95))
    failed = (
        vertical_p95 > PATH_VERTICAL_P95_M
        or lateral_p95 > PATH_LATERAL_P95_M
        or lateral_max > PATH_LATERAL_MAX_M
    )
    detail = (
        f"{segment.seg_id} path to road: p95 vertical {vertical_p95 * 100:.1f} cm, "
        f"lateral {lateral_p95 * 100:.1f} cm, lateral max {lateral_max * 100:.1f} cm"
        if failed
        else ""
    )
    return GateResult(
        "path-to-road",
        "fail" if failed else "pass",
        "fail",
        {
            "vertical_p95_m": vertical_p95,
            "lateral_p95_m": lateral_p95,
            "lateral_max_m": lateral_max,
        },
        {
            "vertical_p95_m": PATH_VERTICAL_P95_M,
            "lateral_p95_m": PATH_LATERAL_P95_M,
            "lateral_max_m": PATH_LATERAL_MAX_M,
        },
        detail,
    )


def _comfort_gate(segment, path, yaw):
    mask = _segment_mask(path.s_m, segment.s_start, segment.s_end)
    maximum = float(yaw[mask].max())
    failed = maximum > YAW_ACCEL_LIMIT_DEG_S2
    detail = (
        f"{segment.seg_id} comfort: yaw acceleration {maximum:.1f} deg/s^2 at design speed" if failed else ""
    )
    return GateResult(
        "comfort",
        "fail" if failed else "pass",
        "fail",
        {"max_yaw_accel_deg_s2": maximum},
        {"yaw_accel_deg_s2": YAW_ACCEL_LIMIT_DEG_S2},
        detail,
    )


def _rtk_gate(segment, nonfixed, bridged):
    nonfixed_here = [run for run in nonfixed if _overlaps(segment, run)]
    bridged_here = [run for run in bridged if _overlaps(segment, run)]
    longest = max((run["length_m"] for run in nonfixed_here), default=0.0)
    long_runs = [run for run in nonfixed_here if run["length_m"] > RTK_GAP_LIMIT_M]
    max_gap = max((run["length_m"] for run in bridged_here), default=0.0)
    oversized = [run for run in bridged_here if run["length_m"] > BRIDGED_GAP_MAX_M]
    steps = [run["grade_step_percent"] for run in bridged_here]
    step = max(steps, default=0.0)
    fails = bool(oversized) or any(run["bridged"] for run in long_runs)
    warns = bool(long_runs) and not fails
    if step > BRIDGED_GRADE_LIMIT_PERCENT:
        fails = True
    reasons = []
    for run in oversized:
        pulses = run["pulses"]
        reasons.append(
            f"bridged gap {run['length_m']:.1f} m at s {run['s_start']:.1f}-{run['s_end']:.1f}, "
            f"pulses {pulses[0]}-{pulses[-1]}"
        )
    for run in long_runs:
        if run["bridged"]:
            reasons.append(f"non-fixed run {run['length_m']:.1f} m contains a bridged gap")
        else:
            reasons.append(f"non-fixed run {run['length_m']:.1f} m")
    if step > BRIDGED_GRADE_LIMIT_PERCENT:
        reasons.append(f"grade step {step:.2f}% across a bridged gap")
    status = "fail" if fails else ("warn" if warns else "pass")
    detail = f"{segment.seg_id} RTK gaps: " + ", ".join(reasons) if reasons else ""
    return GateResult(
        "rtk-gaps",
        status,
        "fail" if fails else ("warn" if warns else "info"),
        {
            "longest_non_fixed_m": longest,
            "max_bridged_gap_m": max_gap,
            "bridged_grade_step_percent": step,
            "non_fixed_runs": [
                {
                    "s_start": run["s_start"],
                    "s_end": run["s_end"],
                    "length_m": run["length_m"],
                    "bridged": run["bridged"],
                }
                for run in nonfixed_here
            ],
            "bridged_runs": [
                {
                    "s_start": run["s_start"],
                    "s_end": run["s_end"],
                    "length_m": run["length_m"],
                    "pulses": run["pulses"],
                    "grade_step_percent": run["grade_step_percent"],
                }
                for run in bridged_here
            ],
        },
        {
            "rtk_gap_m": RTK_GAP_LIMIT_M,
            "bridged_gap_m": BRIDGED_GAP_MAX_M,
            "bridged_grade_percent": BRIDGED_GRADE_LIMIT_PERCENT,
        },
        detail,
    )


def _quality_runs(profile, path, flags):
    """Contiguous runs of samples carrying any of the flags, measured over the whole profile so a run split by
    a segment boundary is not shortened. Length includes half a sample spacing at each end: N one-metre
    samples represent N metres, not N-1."""
    mask = np.array([any(flag in quality for flag in flags) for quality in profile.quality], bool)
    result = []
    for start, stop in _runs(mask):
        before = _spacing(profile.s_m, start, backwards=True)
        after = _spacing(profile.s_m, stop - 1, backwards=False)
        s_start = float(profile.s_m[start] - before / 2)
        s_end = float(profile.s_m[stop - 1] + after / 2)
        run = {
            "s_start": s_start,
            "s_end": s_end,
            "length_m": s_end - s_start,
            "bridged": any("bridged-gap" in quality for quality in profile.quality[start:stop]),
        }
        if "bridged-gap" in flags:
            run["pulses"] = _pulses_in(path, s_start, s_end)
            run["grade_step_percent"] = _grade_step(profile, s_start, s_end)
        result.append(run)
    return result


def _spacing(s, index, backwards):
    if backwards:
        if index > 0:
            return float(s[index] - s[index - 1])
        return float(s[1] - s[0]) if len(s) > 1 else 0.0
    if index < len(s) - 1:
        return float(s[index + 1] - s[index])
    return float(s[index] - s[index - 1]) if len(s) > 1 else 0.0


def _pulses_in(path, s_start, s_end):
    mask = (path.trace_smoothed_m >= s_start - 1e-9) & (path.trace_smoothed_m <= s_end + 1e-9)
    if not mask.any():
        nearest = int(_nearest_index(path.trace_smoothed_m, (s_start + s_end) / 2))
        return [int(path.trace_pulses[nearest])]
    pulses = path.trace_pulses[mask]
    return [int(pulses.min()), int(pulses.max())]


def _grade_step(profile, s_start, s_end):
    before = profile.grade_percent[(profile.s_m >= max(0.0, s_start - 10.0)) & (profile.s_m < s_start)]
    after = profile.grade_percent[(profile.s_m > s_end) & (profile.s_m <= min(profile.s_m[-1], s_end + 10.0))]
    if not len(before) or not len(after):
        return 0.0
    return float(abs(np.median(after) - np.median(before)))


def _nearest_index(values, target):
    index = np.searchsorted(values, target)
    left = np.clip(index - 1, 0, len(values) - 1)
    right = np.clip(index, 0, len(values) - 1)
    return left if abs(values[left] - target) <= abs(values[right] - target) else right


def _overlaps(segment, run):
    return run["s_end"] >= segment.s_start - 1e-9 and run["s_start"] <= segment.s_end + 1e-9


def _registration_gate(failures):
    detail = f"slice 6 registration failed: {'; '.join(failures)}" if failures else ""
    return GateResult(
        "registration",
        "fail" if failures else "pass",
        "fail",
        {"failures": len(failures)},
        {},
        detail,
    )


def _runs(mask):
    """Contiguous True runs as half-open [start, stop) index pairs."""
    if not len(mask):
        return []
    padded = np.concatenate([[False], np.asarray(mask, bool), [False]])
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(changes[::2], changes[1::2], strict=True))


def write_qa(
    out_dir,
    report: QAReport,
    path: RiderPath | None = None,
    profile: DistanceProfile | None = None,
    route: SegmentedRoute | None = None,
) -> Path:
    """Writes qa/<route_id>/<build_id>/report.json and, when given the path and profile, the review plots.
    This runs for a failing build too: the report is the record of why the segment is on the recapture
    list."""
    folder = Path(out_dir) / "qa" / report.route_id / report.build_id
    folder.mkdir(parents=True, exist_ok=True)
    plots = {}
    if path is not None and profile is not None:
        try:
            plots = write_plots(folder, path, profile, route)
        except Exception as e:
            plots = {"error": str(e)}
    report = QAReport(
        report.route_id,
        report.build_id,
        report.generated_utc,
        report.registration,
        report.segments,
        plots,
        report.checks,
    )
    (folder / "report.json").write_text(json.dumps(report.document(), indent=1, allow_nan=False) + "\n")
    return folder


def write_plots(
    folder, path: RiderPath, profile: DistanceProfile, route: SegmentedRoute | None = None
) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    folder = Path(folder)
    stride = max(1, len(path.enu_m) // 200_000)
    figure, axis = plt.subplots(figsize=(9, 9))
    axis.plot(
        path.trace_enu_m[::stride, 0],
        path.trace_enu_m[::stride, 1],
        color="0.7",
        lw=1,
        label="road contact trace",
    )
    axis.plot(
        path.enu_m[::stride, 0], path.enu_m[::stride, 1], color="tab:blue", lw=1.2, label="smoothed path"
    )
    if route is not None:
        for segment in route.segments[:-1]:
            boundary = path.at([segment.s_end])[0][0]
            axis.plot(boundary[0], boundary[1], "x", color="black", markersize=8)
            axis.annotate(
                segment.seg_id,
                (boundary[0], boundary[1]),
                xytext=(4, 4),
                textcoords="offset points",
                fontsize=8,
            )
    axis.set_aspect("equal")
    axis.set_xlabel("east (m)")
    axis.set_ylabel("north (m)")
    axis.legend()
    axis.set_title("Plan view (segment boundaries marked)")
    plan = folder / "plan.png"
    figure.savefig(plan, dpi=120, bbox_inches="tight")
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(11, 5))
    axis.plot(profile.s_h_m, profile.h_ortho_m, color="tab:blue", label="orthometric height")
    axis.set_xlabel("horizontal distance (m)")
    axis.set_ylabel("orthometric height (m)")
    grade_axis = axis.twinx()
    grade_axis.plot(profile.s_m, profile.grade_percent, color="tab:orange", alpha=0.6, label="grade")
    grade_axis.set_ylabel("grade (%)")
    axis.set_title("Height and grade")
    figure.savefig(folder / "profile.png", dpi=120, bbox_inches="tight")
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(11, 4))
    axis.plot(profile.s_m, profile.curvature_1pm, color="tab:green", label="curvature")
    limit = LATERAL_ACCEL_LIMIT_MPS2 / DESIGN_SPEED_MPS**2
    axis.axhline(limit, color="tab:red", ls="--", lw=1, label="design-speed lateral limit")
    axis.axhline(-limit, color="tab:red", ls="--", lw=1)
    excess = np.abs(profile.curvature_1pm) > limit
    axis.fill_between(
        profile.s_m,
        limit,
        profile.curvature_1pm,
        where=excess,
        color="tab:red",
        alpha=0.2,
        label="beyond the limit: bend speed policy",
    )
    axis.set_xlabel("distance (m)")
    axis.set_ylabel("curvature (1/m)")
    axis.set_title(
        f"Curvature; lateral limit {limit:.4f} 1/m at {DESIGN_SPEED_MPS:g} m/s, "
        f"{LATERAL_ACCEL_LIMIT_MPS2:g} m/s^2"
    )
    axis.legend(loc="best", fontsize=8)
    figure.savefig(folder / "curvature.png", dpi=120, bbox_inches="tight")
    plt.close(figure)
    return {"plan": "plan.png", "profile": "profile.png", "curvature": "curvature.png"}
