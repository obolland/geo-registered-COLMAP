"""Offline pipeline QA: numeric, per-segment gates with a versioned threshold set, a report written even
when a gate fails, and the review plots. Runtime QA (frame time, LOD popping, re-base hitches) is separate."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from georoute.package import SegmentedRoute
from georoute.path import DistanceProfile, PathError, RiderPath

QA_SCHEMA = "georoute.qa/1.0.0"
THRESHOLDS_VERSION = "1.0.0"
PATH_VERTICAL_P95_M = 0.03
PATH_LATERAL_P95_M = 0.05
YAW_ACCEL_LIMIT_DEG_S2 = 30.0
DESIGN_SPEED_MPS = 18.0
LATERAL_ACCEL_LIMIT_MPS2 = 3.0
RTK_GAP_LIMIT_M = 50.0
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
        return "fail" if any(gate.status == "fail" for gate in self.gates) else "pass"

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

    @property
    def status(self) -> str:
        return "fail" if any(segment.status == "fail" for segment in self.segments) else "pass"

    @property
    def recapture(self) -> list[str]:
        return [segment.seg_id for segment in self.segments if segment.status == "fail"]

    def thresholds(self) -> dict:
        return {
            "version": THRESHOLDS_VERSION,
            "path_vertical_p95_m": PATH_VERTICAL_P95_M,
            "path_lateral_p95_m": PATH_LATERAL_P95_M,
            "yaw_accel_deg_s2": YAW_ACCEL_LIMIT_DEG_S2,
            "design_speed_mps": DESIGN_SPEED_MPS,
            "lateral_accel_limit_mps2": LATERAL_ACCEL_LIMIT_MPS2,
            "rtk_gap_m": RTK_GAP_LIMIT_M,
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
        }


def assess(
    path: RiderPath,
    profile: DistanceProfile,
    route: SegmentedRoute,
    registration: dict,
    route_id: str,
    build_id: str,
    generated_utc: str | None = None,
) -> QAReport:
    """Runs the offline gates over the global path and profile, then assigns the results to segments. The
    global arrays are used so a gate can't be introduced by slicing at a boundary."""
    failures = _registration_failures(registration)
    lateral, vertical = _trace_deviations(path)
    yaw = _yaw_acceleration(path)
    segments = []
    for segment in route.segments:
        gate_results = (
            _path_gate(segment, path, lateral, vertical),
            _comfort_gate(segment, path, yaw),
            _rtk_gate(segment, profile),
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
    )


def _registration_failures(registration: dict) -> list[str]:
    if not isinstance(registration, dict) or "failures" not in registration:
        raise QAError(
            "the slice 6 registration report has no 'failures'; run "
            "`georoute colmap-report WORKSPACE --rig RIG`"
        )
    return [str(failure) for failure in registration["failures"]]


def _trace_deviations(path: RiderPath):
    """Lateral and vertical distance from each raw road contact to the smoothed path, at the contact's own
    arclength. Lateral is perpendicular to the smoothed tangent, not a plain coordinate difference."""
    enu, heading, _, _ = path.at(path.trace_s_m)
    delta = enu[:, :2] - path.trace_enu_m[:, :2]
    tangent_e, tangent_n = np.cos(heading), np.sin(heading)
    lateral = np.abs(delta[:, 0] * tangent_n - delta[:, 1] * tangent_e)
    vertical = np.abs(enu[:, 2] - path.trace_enu_m[:, 2])
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
    mask = _segment_mask(path.trace_s_m, segment.s_start, segment.s_end)
    lateral_p95 = float(np.percentile(lateral[mask], 95))
    vertical_p95 = float(np.percentile(vertical[mask], 95))
    failed = vertical_p95 > PATH_VERTICAL_P95_M or lateral_p95 > PATH_LATERAL_P95_M
    detail = (
        f"{segment.seg_id} path to road: p95 vertical {vertical_p95 * 100:.1f} cm, "
        f"lateral {lateral_p95 * 100:.1f} cm"
        if failed
        else ""
    )
    return GateResult(
        "path-to-road",
        "fail" if failed else "pass",
        "fail",
        {"vertical_p95_m": vertical_p95, "lateral_p95_m": lateral_p95},
        {"vertical_p95_m": PATH_VERTICAL_P95_M, "lateral_p95_m": PATH_LATERAL_P95_M},
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


def _rtk_gate(segment, profile):
    mask = _segment_mask(profile.s_m, segment.s_start, segment.s_end)
    s = profile.s_m[mask]
    flags = [profile.quality[i] for i in np.flatnonzero(mask)]
    nonfixed = np.array([("rtk-float" in q or "rtk-none" in q) for q in flags])
    runs = _runs(nonfixed)
    longest = max((float(s[b - 1] - s[a]) for a, b in runs), default=0.0)
    bridged = []
    for a, b in _runs(np.array([("bridged-gap" in q) for q in flags], dtype=bool)):
        gap_start, gap_end = float(s[a]), float(s[b - 1])
        before = profile.grade_percent[
            (profile.s_m >= max(0.0, gap_start - 10.0)) & (profile.s_m < gap_start)
        ]
        after = profile.grade_percent[
            (profile.s_m > gap_end) & (profile.s_m <= min(profile.s_m[-1], gap_end + 10.0))
        ]
        if len(before) and len(after):
            bridged.append(float(abs(np.median(after) - np.median(before))))
    step = max(bridged, default=0.0)
    failed = longest > RTK_GAP_LIMIT_M or step > BRIDGED_GRADE_LIMIT_PERCENT
    reasons = []
    if longest > RTK_GAP_LIMIT_M:
        reasons.append(f"longest non-fixed run {longest:.1f} m")
    if step > BRIDGED_GRADE_LIMIT_PERCENT:
        reasons.append(f"grade step {step:.2f}% across a bridged gap")
    detail = f"{segment.seg_id} RTK gaps: " + ", ".join(reasons) if reasons else ""
    return GateResult(
        "rtk-gaps",
        "fail" if failed else "pass",
        "fail",
        {
            "longest_non_fixed_m": longest,
            "bridged_grade_step_percent": step,
            "bridged_gaps": len(bridged),
        },
        {"rtk_gap_m": RTK_GAP_LIMIT_M, "bridged_grade_percent": BRIDGED_GRADE_LIMIT_PERCENT},
        detail,
    )


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
    out_dir, report: QAReport, path: RiderPath | None = None, profile: DistanceProfile | None = None
) -> Path:
    """Writes qa/<build_id>/report.json and, when given the path and profile, the review plots. This runs for
    a failing build too: the report is the record of why the segment is on the recapture list."""
    folder = Path(out_dir) / "qa" / report.build_id
    folder.mkdir(parents=True, exist_ok=True)
    plots = {}
    if path is not None and profile is not None:
        plots = write_plots(folder, path, profile)
    report = QAReport(
        report.route_id,
        report.build_id,
        report.generated_utc,
        report.registration,
        report.segments,
        plots,
    )
    (folder / "report.json").write_text(json.dumps(report.document(), indent=1, allow_nan=False) + "\n")
    return folder


def write_plots(folder, path: RiderPath, profile: DistanceProfile) -> dict:
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
    axis.set_aspect("equal")
    axis.set_xlabel("east (m)")
    axis.set_ylabel("north (m)")
    axis.legend()
    axis.set_title("Plan view")
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
    axis.plot(profile.s_m, profile.curvature_1pm, color="tab:green")
    axis.set_xlabel("distance (m)")
    axis.set_ylabel("curvature (1/m)")
    axis.set_title("Curvature")
    figure.savefig(folder / "curvature.png", dpi=120, bbox_inches="tight")
    plt.close(figure)
    return {"plan": "plan.png", "profile": "profile.png", "curvature": "curvature.png"}
