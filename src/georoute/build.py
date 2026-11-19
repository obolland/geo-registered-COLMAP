"""Builds the published route package from a refined reconstruction: rig poses, road contacts, the smoothed
rider path, the distance profile, per-segment QA, and the package itself."""

import json
import platform
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np

from georoute.geoid import load_geoid
from georoute.ingest import IMAGES_SCHEMA
from georoute.package import (
    LOOP_RADIUS_M,
    NEIGHBOUR_RADIUS_M,
    NEIGHBOUR_SAMPLE_M,
    PATH_STEP_M,
    PackageError,
    cut_segments,
    write_package,
)
from georoute.path import (
    CONTACT_HEIGHT_WINDOW_M,
    CONTACT_RADIUS_M,
    CORRIDOR_M,
    GRADE_WINDOW_M,
    MIN_CONTACT_POINTS,
    NOMINAL_SURFACE_SIGMA_M,
    PROFILE_STEP_M,
    ROBUST_SCALE_M,
    distance_profile,
    height_check,
    rider_path,
    rig_poses,
    road_contacts,
)
from georoute.qa import (
    DESIGN_SPEED_MPS,
    QA_SCHEMA,
    THRESHOLDS_VERSION,
    QAError,
    assess,
    write_qa,
)
from georoute.reconstruct import _model_poses
from georoute.rig import Rig


class BuildError(ValueError):
    pass


OSGM15_DATUMS = frozenset({"ETRS89"})
# Road points need enough track to be road, not two-view speckle, and a small enough reprojection error not
# to tilt the local plane. Both are Measure.
MIN_ROAD_TRACK_LENGTH = 3
MAX_ROAD_REPROJECTION_ERROR_PX = 2.0


def build_route(
    workspace,
    images_json,
    rig: Rig,
    out,
    route_id: str,
    segment_length_m: float = 1000.0,
    build_id: str | None = None,
    geoid=None,
    generated_utc: str | None = None,
) -> dict:
    """Builds and publishes one route package. A failure writes a refusal QA report but no package; a QA gate
    failure still publishes the package, marked, with the failing segments on the recapture list."""
    generated = generated_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    build_id = build_id or generated.replace("-", "").replace(":", "")
    _plain_name(route_id, "route id")
    _plain_name(build_id, "build id")
    try:
        target = Path(out) / route_id / build_id
        if target.exists():
            raise PackageError(f"package {target} already exists; route builds are never overwritten")
        return _build(
            workspace,
            images_json,
            rig,
            out,
            route_id,
            segment_length_m,
            build_id,
            geoid,
            generated,
        )
    except Exception as e:
        _write_refusal(out, build_id, route_id, str(e), generated)
        raise


def _build(workspace, images_json, rig, out, route_id, segment_length_m, build_id, geoid, generated):
    data = json.loads(Path(images_json).read_text())
    if data.get("schema") != IMAGES_SCHEMA:
        raise BuildError(f"{images_json} schema is {data.get('schema')!r}, expected {IMAGES_SCHEMA}")
    frame = frame_from_images(data)
    if not isinstance(data.get("session"), str) or not data["session"]:
        raise BuildError(f"{images_json} has no session id")
    geoid = load_geoid() if geoid is None else geoid
    if geoid.name == "OSGM15" and frame.datum not in OSGM15_DATUMS:
        raise BuildError(f"OSGM15 heights need an ETRS89 frame, got {frame.datum!r}; refusing to mix datums")
    geoid_provenance = {**geoid.provenance(), "datum": frame.datum}
    report_path = Path(workspace) / "report.json"
    if not report_path.is_file():
        raise BuildError(
            f"{report_path} is missing; run `georoute colmap-report {workspace} --rig RIG` first"
        )
    registration = json.loads(report_path.read_text())
    if "failures" not in registration:
        raise QAError(f"{report_path} has no 'failures' list")

    which, poses, _, record, model = _model_poses(Path(workspace))
    sessions = {r.get("session") for r in record.values() if r.get("session")}
    if len(sessions) > 1:
        raise BuildError(
            f"workspace holds sessions {sorted(sessions)}; route build needs one session (step 8)"
        )
    points, point_stats = _road_points(model)
    contacts = road_contacts(rig_poses(poses, data["images"], rig, frame), points, rig, frame)
    path = rider_path(contacts, frame)
    profile = distance_profile(path, data["images"], frame, geoid)
    route = cut_segments(path, profile, frame, segment_length_m)
    check = height_check(contacts, rig)
    commit, dirty = _git_state()
    qa = assess(
        path,
        profile,
        route,
        registration,
        route_id,
        build_id,
        generated_utc=generated,
        checks={
            "model": which,
            "height_above_road": check,
            "road_contacts": len(contacts),
            "nominal_contacts": int(sum(contact.nominal for contact in contacts)),
            "trace_points": int(len(path.trace_parameter_m)),
            "road_points": point_stats,
        },
    )
    failed = set(qa.recapture)
    if failed:
        route = replace(
            route,
            segments=tuple(
                replace(segment, quality=tuple(sorted({*segment.quality, "gate-failed"})))
                if segment.seg_id in failed
                else segment
                for segment in route.segments
            ),
        )
    parameters = {
        "segment_length_m": float(segment_length_m),
        "path_step_m": PATH_STEP_M,
        "profile_step_m": PROFILE_STEP_M,
        "smoothing": path.smoothing,
        "grade_window_m": GRADE_WINDOW_M,
        "corridor": {"left_m": CORRIDOR_M, "right_m": CORRIDOR_M, "measured": False},
        "road_contact": {
            "radius_m": CONTACT_RADIUS_M,
            "height_window_m": CONTACT_HEIGHT_WINDOW_M,
            "min_points": MIN_CONTACT_POINTS,
            "nominal_sigma_m": NOMINAL_SURFACE_SIGMA_M,
            "robust_scale_m": ROBUST_SCALE_M,
        },
        "road_points": point_stats,
        "segments": {
            "neighbour_radius_m": NEIGHBOUR_RADIUS_M,
            "neighbour_sample_m": NEIGHBOUR_SAMPLE_M,
            "loop_radius_m": LOOP_RADIUS_M,
        },
        "qa": {"thresholds_version": THRESHOLDS_VERSION, "thresholds": qa.thresholds()},
        "geoid": geoid_provenance,
        "model": which,
    }
    meta = {
        "datum": frame.datum,
        "geoid": geoid_provenance,
        "session_id": data["session"],
        "app_defaults": {
            "design_speed_mps": DESIGN_SPEED_MPS,
            "corridor_measured": False,
            "corridor_left_m": CORRIDOR_M,
            "corridor_right_m": CORRIDOR_M,
        },
        "qa": qa.summary(),
        "provenance": {
            "sessions": [data["session"]],
            "rig_sha256": rig.sha256,
            "pipeline_commit": commit,
            "git_dirty": dirty,
            "tool_versions": _tool_versions(),
            "parameters": parameters,
        },
    }
    qa_folder = write_qa(out, qa, path, profile, route)
    final = write_package(out, route_id, build_id, route, meta)
    return {
        "route_id": route_id,
        "build_id": build_id,
        "package": str(final),
        "qa": str(qa_folder / "report.json"),
        "status": qa.status,
        "recapture": qa.recapture,
        "segments": len(route.segments),
        "distance_m": path.length_m,
        "loop": route.loop,
        "height_check": check,
        "smoothing": path.smoothing,
        "profile_samples": int(len(profile.s_m)),
        "registration_pass": not registration["failures"],
    }


def frame_from_images(data: dict):
    from georoute.geodesy import LocalFrame

    frame = data.get("frame")
    required = {"lat_deg", "lon_deg", "h_m", "datum", "origin_ecef"}
    if not isinstance(frame, dict) or not required <= set(frame):
        raise BuildError(f"images.json frame must carry {sorted(required)}")
    if frame["datum"] != data.get("datum"):
        raise BuildError(
            f"images.json frame datum {frame['datum']!r} disagrees with the session datum "
            f"{data.get('datum')!r}"
        )
    local = LocalFrame.at(frame["lat_deg"], frame["lon_deg"], frame["h_m"], frame["datum"])
    difference = np.linalg.norm(local.origin_ecef - np.array(frame["origin_ecef"], float))
    if difference > 1e-3:
        raise BuildError(
            f"images.json frame origin_ecef is {difference:.3f} m from its lat/lon/h; "
            "the file is inconsistent"
        )
    return local


def _tool_versions() -> dict:
    versions = {}
    for package in ("georoute", "numpy", "scipy", "pyproj", "pycolmap", "matplotlib"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "unknown"
    versions["python"] = platform.python_version()
    return versions


def _git_state() -> tuple[str, bool | None]:
    """The commit the build ran from, and whether tracked or untracked files were dirty at the time."""
    root = str(Path(__file__).parents[2])
    try:
        commit = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", root, "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return "unknown", None
    return commit or "unknown", bool(status.strip())


def _road_points(model):
    """Road-fit points with enough track and small enough reprojection error; two-view specks and badly
    matched points would tilt the local plane even under the robust loss."""
    all_points = list(model.points3D.values())
    used = [
        point
        for point in all_points
        if point.track.length() >= MIN_ROAD_TRACK_LENGTH and point.error <= MAX_ROAD_REPROJECTION_ERROR_PX
    ]
    points = np.array([point.xyz for point in used], float)
    if not len(points):
        points = np.empty((0, 3))
    return points, {
        "input": len(all_points),
        "used": len(used),
        "min_track_length": MIN_ROAD_TRACK_LENGTH,
        "max_reprojection_error_px": MAX_ROAD_REPROJECTION_ERROR_PX,
    }


def _plain_name(value, what):
    if not isinstance(value, str) or not value or Path(value).name != value or value in (".", ".."):
        raise PackageError(f"{what} {value!r} must be a plain name, not a path")


def _write_refusal(out, build_id, route_id, message, generated):
    try:
        folder = Path(out) / "qa" / route_id / build_id
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "report.json"
        if target.exists():
            return
        document = {
            "schema": QA_SCHEMA,
            "route_id": route_id,
            "build_id": build_id,
            "generated_utc": generated,
            "status": "fail",
            "refused": message,
            "thresholds": {},
            "registration": {},
            "segments": [],
            "recapture": [],
            "plots": {},
            "checks": {},
        }
        target.write_text(json.dumps(document, indent=1, allow_nan=False) + "\n")
    except OSError:
        # The refusal report is useful but must not hide the build error the caller is about to see.
        pass
