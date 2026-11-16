"""The published route package: route-global path and profile cut into segment-local frames, written to a
staging folder and moved into place with route.json last."""

import json
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from georoute.geodesy import LocalFrame, ecef_to_lla
from georoute.path import DistanceProfile, RiderPath

ROUTE_SCHEMA = "georoute.route/1.0.0"
PATH_SCHEMA = "georoute.path/1.0.0"
PROFILE_SCHEMA = "georoute.profile/1.0.0"
PATH_STEP_M = 0.5
NEIGHBOUR_RADIUS_M = 100.0
NEIGHBOUR_SAMPLE_M = 5.0
LOOP_RADIUS_M = 5.0
REQUIRED_META = ("datum", "geoid", "session_id", "app_defaults", "qa", "provenance")


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class SegmentData:
    seg_id: str
    s_start: float
    s_end: float
    origin: LocalFrame
    path_s: np.ndarray
    e: np.ndarray
    n: np.ndarray
    u: np.ndarray
    heading_rad: np.ndarray
    curvature_1pm: np.ndarray
    profile: dict
    bounds: dict
    corridor_left_m: float
    corridor_right_m: float
    quality: tuple[str, ...]
    neighbours: tuple[str, ...] = ()

    def path_document(self) -> dict:
        samples = [
            {
                "s": float(s),
                "e": float(e),
                "n": float(n),
                "u_road": float(u),
                "heading_rad": float(heading),
                "curvature_1pm": float(curvature),
            }
            for s, e, n, u, heading, curvature in zip(
                self.path_s,
                self.e,
                self.n,
                self.u,
                self.heading_rad,
                self.curvature_1pm,
                strict=True,
            )
        ]
        return {
            "schema": PATH_SCHEMA,
            "seg_id": self.seg_id,
            "s_range": [float(self.s_start), float(self.s_end)],
            "samples": samples,
        }

    def profile_document(self) -> dict:
        p = self.profile
        samples = [
            {
                "s": float(s),
                "s_h": float(s_h),
                "lat": float(lat),
                "lon": float(lon),
                "h_ortho": float(h),
                "sigma_h": float(sigma),
                "grade": float(grade),
                "curvature": float(curvature),
                "corridor_left_m": float(left),
                "corridor_right_m": float(right),
                "surface": str(surface),
                "quality": list(quality),
            }
            for s, s_h, lat, lon, h, sigma, grade, curvature, left, right, surface, quality in zip(
                p["s"],
                p["s_h"],
                p["lat"],
                p["lon"],
                p["h_ortho"],
                p["sigma_h"],
                p["grade"],
                p["curvature"],
                p["corridor_left_m"],
                p["corridor_right_m"],
                p["surface"],
                p["quality"],
                strict=True,
            )
        ]
        return {
            "schema": PROFILE_SCHEMA,
            "seg_id": self.seg_id,
            "s_range": [float(self.s_start), float(self.s_end)],
            "samples": samples,
        }


@dataclass(frozen=True)
class SegmentedRoute:
    segments: tuple[SegmentData, ...]
    distance_m: float
    loop: bool

    def document(self, route_id: str, build_id: str, meta: dict) -> dict:
        return {
            "schema": ROUTE_SCHEMA,
            "route_id": route_id,
            "build_id": build_id,
            "datum": meta["datum"],
            "geoid": meta["geoid"],
            "distance_m": float(self.distance_m),
            "loop": bool(self.loop),
            "segments": [
                {
                    "seg_id": segment.seg_id,
                    "s_range": [float(segment.s_start), float(segment.s_end)],
                    "origin": {
                        "lat_deg": segment.origin.lat_deg,
                        "lon_deg": segment.origin.lon_deg,
                        "h_m": segment.origin.h_m,
                        "ecef": segment.origin.origin_ecef.tolist(),
                    },
                    "bounds_local": segment.bounds,
                    "neighbours": list(segment.neighbours),
                    "session_id": meta["session_id"],
                    "session_join": False,
                    "corridor_left_m": float(segment.corridor_left_m),
                    "corridor_right_m": float(segment.corridor_right_m),
                    "quality": list(segment.quality),
                    "path": f"segments/{segment.seg_id}/path.json",
                    "profile": f"segments/{segment.seg_id}/profile.json",
                }
                for segment in self.segments
            ],
            "splats": [],
            "app_defaults": meta["app_defaults"],
            "qa": meta["qa"],
            "provenance": meta["provenance"],
        }


def cut_segments(
    path: RiderPath,
    profile: DistanceProfile,
    frame: LocalFrame,
    segment_length_m: float = 1000.0,
) -> SegmentedRoute:
    """Cuts the route at segment_length_m of route-global 3D distance. Each segment gets its own ENU origin
    at its middle, path samples every 0.5 m plus both boundaries, and the profile samples that fall inside.
    The values are the global ones transformed into the segment frame, never refitted, so heights, grade and
    heading don't step at a boundary."""
    if not np.isfinite(segment_length_m) or segment_length_m <= 0:
        raise PackageError(f"segment length must be positive, got {segment_length_m!r}")
    boundaries = [0.0]
    while boundaries[-1] < path.length_m - 1e-9:
        boundaries.append(min(boundaries[-1] + segment_length_m, path.length_m))
    segments = []
    for index, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:], strict=True)):
        segments.append(_segment(index, start, end, path, profile, frame))
    segments = _neighbours(segments)
    ends = frame.to_ecef(path.at([0.0, path.length_m])[0])
    return SegmentedRoute(
        tuple(segments), path.length_m, bool(np.linalg.norm(ends[0] - ends[1]) <= LOOP_RADIUS_M)
    )


def _segment(index, start, end, path, profile, frame):
    origin_enu = path.at([(start + end) / 2])[0][0]
    origin_ecef = frame.to_ecef(origin_enu)
    lat, lon, height = ecef_to_lla(origin_ecef)
    origin = LocalFrame.at(float(lat), float(lon), float(height), frame.datum)

    s = _path_samples(start, end)
    enu, heading, curvature, _ = path.at(s)
    local = origin.from_ecef(frame.to_ecef(enu))
    rotation = frame.ecef_from_enu.T @ origin.ecef_from_enu
    direction = np.column_stack([np.cos(heading), np.sin(heading), np.zeros_like(heading)]) @ rotation
    local_heading = np.arctan2(direction[:, 1], direction[:, 0])

    sliced = _slice_profile(profile, start, end)
    quality = tuple(sorted({flag for flags in sliced["quality"] for flag in flags}))
    return SegmentData(
        seg_id=f"seg_{index:04d}",
        s_start=float(start),
        s_end=float(end),
        origin=origin,
        path_s=s,
        e=local[:, 0],
        n=local[:, 1],
        u=local[:, 2],
        heading_rad=local_heading,
        curvature_1pm=curvature,
        profile=sliced,
        bounds={
            "min": [float(local[:, axis].min()) for axis in range(3)],
            "max": [float(local[:, axis].max()) for axis in range(3)],
        },
        corridor_left_m=float(np.max(sliced["corridor_left_m"])),
        corridor_right_m=float(np.max(sliced["corridor_right_m"])),
        quality=quality,
    )


def _path_samples(start, end):
    first = np.ceil((start - 1e-9) / PATH_STEP_M) * PATH_STEP_M
    grid = np.arange(first, end, PATH_STEP_M)
    inner = grid[(grid > start + 1e-9) & (grid < end - 1e-9)]
    return np.unique(np.concatenate([[start], inner, [end]]))


def _slice_profile(profile, start, end):
    """The profile samples inside the segment, plus interpolated samples at the exact s_range ends so every
    segment reaches its boundaries and neighbours meet at the same height and grade."""
    mask = (profile.s_m >= start - 1e-9) & (profile.s_m <= end + 1e-9)
    attributes = {
        "s": "s_m",
        "s_h": "s_h_m",
        "lat": "lat_deg",
        "lon": "lon_deg",
        "h_ortho": "h_ortho_m",
        "sigma_h": "sigma_h_m",
        "grade": "grade_percent",
        "curvature": "curvature_1pm",
        "corridor_left_m": "corridor_left_m",
        "corridor_right_m": "corridor_right_m",
    }
    arrays = {key: getattr(profile, attribute)[mask].tolist() for key, attribute in attributes.items()}
    arrays["surface"] = [surface for surface, keep in zip(profile.surface, mask, strict=True) if keep]
    arrays["quality"] = [quality for quality, keep in zip(profile.quality, mask, strict=True) if keep]
    for boundary, at_start in ((start, True), (end, False)):
        if arrays["s"]:
            existing = arrays["s"][0] if at_start else arrays["s"][-1]
            if abs(existing - boundary) <= 1e-9:
                continue
        nearest = int(_nearest_index(profile.s_m, boundary))
        row = {
            key: float(np.interp(boundary, profile.s_m, getattr(profile, attribute)))
            for key, attribute in attributes.items()
        }
        row["surface"] = profile.surface[nearest]
        row["quality"] = profile.quality[nearest]
        for key in arrays:
            if at_start:
                arrays[key].insert(0, row[key])
            else:
                arrays[key].append(row[key])
    return {
        key: np.array(value) if key not in ("surface", "quality") else tuple(value)
        for key, value in arrays.items()
    }


def _nearest_index(values, target):
    index = np.searchsorted(values, target)
    left = np.clip(index - 1, 0, len(values) - 1)
    right = np.clip(index, 0, len(values) - 1)
    return left if abs(values[left] - target) <= abs(values[right] - target) else right


def _neighbours(segments):
    """Neighbours by minimum 3D distance between path samples, not by arclength, so a folded route prefetches
    the branch on the other side of the hedge as well as the continuation."""
    if len(segments) < 2:
        return segments
    stride = max(1, int(round(NEIGHBOUR_SAMPLE_M / PATH_STEP_M)))
    sample_sets = []
    for segment in segments:
        index = np.arange(0, len(segment.e), stride)
        if index[-1] != len(segment.e) - 1:
            index = np.append(index, len(segment.e) - 1)
        sample_sets.append(index)
    points = np.vstack(
        [
            segment.origin.to_ecef(np.column_stack([segment.e, segment.n, segment.u])[index])
            for segment, index in zip(segments, sample_sets, strict=True)
        ]
    )
    owner = np.concatenate([np.full(len(index), position) for position, index in enumerate(sample_sets)])
    pairs = cKDTree(points).query_pairs(NEIGHBOUR_RADIUS_M + 1e-9, output_type="ndarray")
    found = {index: set() for index in range(len(segments))}
    for a, b in pairs:
        left, right = int(owner[a]), int(owner[b])
        if left != right:
            found[left].add(segments[right].seg_id)
            found[right].add(segments[left].seg_id)
    return [
        replace(segment, neighbours=tuple(sorted(found[index]))) for index, segment in enumerate(segments)
    ]


def write_package(out_dir, route_id: str, build_id: str, route: SegmentedRoute, meta: dict) -> Path:
    """Writes the package to a staging folder, route.json last, then renames the folder into place. An
    existing build_id is never overwritten; a failure removes the staging folder, leaving no half package."""
    _plain_name(route_id, "route id")
    _plain_name(build_id, "build id")
    missing = [key for key in REQUIRED_META if key not in meta]
    if missing:
        raise PackageError(f"route metadata is missing {', '.join(missing)}")
    out = Path(out_dir)
    route_dir = out / route_id
    final = route_dir / build_id
    if final.exists():
        raise PackageError(f"package {final} already exists; route builds are never overwritten")
    route_dir.mkdir(parents=True, exist_ok=True)
    staging = route_dir / f".{build_id}.staging-{os.getpid()}"
    if staging.exists():
        raise PackageError(f"staging folder {staging} already exists; remove it before rebuilding")
    try:
        for segment in route.segments:
            folder = staging / "segments" / segment.seg_id
            folder.mkdir(parents=True)
            _write_json(folder / "path.json", segment.path_document())
            _write_json(folder / "profile.json", segment.profile_document())
            _fsync_directory(folder)
        _fsync_directory(staging / "segments")
        _write_json(staging / "route.json", route.document(route_id, build_id, meta))
        _fsync_directory(staging)
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def _plain_name(value: str, what: str):
    if not isinstance(value, str) or not value or Path(value).name != value or value in (".", ".."):
        raise PackageError(f"{what} {value!r} must be a plain name, not a path")


def _write_json(path: Path, data: dict):
    with path.open("w") as handle:
        handle.write(json.dumps(data, indent=1, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
