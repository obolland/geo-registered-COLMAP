#!/usr/bin/env python3
"""
generate_path_cam_from_aligned_colmap.py

Generate a runtime-friendly camera-centers playback path (ENU meters) directly from an
*aligned* COLMAP model (after `colmap model_aligner`), with optional resampling,
outlier/teleport filtering, smoothing, and per-sample gradient (%).

This is intended to become your in-engine "ground truth" for:
  ✅ rider motion (stay inside the splat corridor)
  ✅ grade / gradient (better than GPX elevation)

WHY THIS EXISTS
---------------
Even when COLMAP is aligned to GPS/ENU, GPX is often too noisy to use as the playback path.
Using the aligned COLMAP camera centers solves drift because it matches the corridor your
splat was trained on.

WHEN TO RUN
-----------
Run this AFTER Step 5 (model_aligner) and before/after training (doesn't matter):
  dataset/keyframes/colmap/sparse/0_enu/images.bin   <-- input
  dataset/images/                                   <-- for EXIF times to order cameras

OUTPUT
------
Writes a JSON polyline:

{
  "frame": "ENU",
  "units": "meters",
  "source": "colmap_aligned_camera_centers",
  "resample_m": 0.5,
  "smooth_window_m": 5.0,
  "grade_window_m": 10.0,
  "points": [
    { "s": 0.0, "e": ..., "n": ..., "u": ..., "grade_pct": ..., "t_rel": 0.0 },
    ...
  ]
}

Notes:
- `t_rel` is relative seconds from the first image (timezone-independent). It's useful for
  debugging, but for runtime you typically drive motion by integrating trainer speed.

USAGE EXAMPLE
-------------
./venv/bin/python generate_path_cam_from_aligned_colmap.py \
  --colmap_model dataset/keyframes/colmap/sparse/0_enu \
  --images_dir dataset/images \
  --output dataset/keyframes/world_alignment/path_cam.json \
  --resample_m 0.5 \
  --smooth_window_m 5 \
  --grade_window_m 10 \
  --drop_teleports_m 5 \
  --print_report

DEPENDENCIES
------------
- numpy
- pillow (PIL)

"""

from __future__ import annotations

import argparse
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image
from PIL.ExifTags import TAGS


# -----------------------------
# COLMAP binary readers
# -----------------------------

def read_next_bytes(fid, num_bytes: int, fmt: str, endian: str = "<"):
    data = fid.read(num_bytes)
    return struct.unpack(endian + fmt, data)

def read_images_binary(path: Path) -> Dict[int, Dict]:
    """
    Reads COLMAP images.bin
    Returns: dict image_id -> {name, qvec(4), tvec(3)}
    """
    images: Dict[int, Dict] = {}
    with path.open("rb") as fid:
        num_images = read_next_bytes(fid, 8, "Q")[0]
        for _ in range(num_images):
            image_id = read_next_bytes(fid, 4, "I")[0]
            qvec = np.array(read_next_bytes(fid, 8 * 4, "dddd"), dtype=np.float64)
            tvec = np.array(read_next_bytes(fid, 8 * 3, "ddd"), dtype=np.float64)
            _camera_id = read_next_bytes(fid, 4, "I")[0]

            # null-terminated string name
            name_bytes = b""
            while True:
                c = fid.read(1)
                if c == b"\x00":
                    break
                name_bytes += c
            name = name_bytes.decode("utf-8")

            num_points2D = read_next_bytes(fid, 8, "Q")[0]
            fid.read(num_points2D * 24)  # skip x,y,point3D_id per point (3 * 8 bytes)

            images[image_id] = {"name": name, "qvec": qvec, "tvec": tvec}

    return images

def qvec2rotmat(qvec: np.ndarray) -> np.ndarray:
    """COLMAP qvec is [qw, qx, qy, qz]"""
    qw, qx, qy, qz = qvec
    return np.array(
        [
            [1 - 2*qy*qy - 2*qz*qz,     2*qx*qy - 2*qw*qz,     2*qx*qz + 2*qw*qy],
            [2*qx*qy + 2*qw*qz,         1 - 2*qx*qx - 2*qz*qz, 2*qy*qz - 2*qw*qx],
            [2*qx*qz - 2*qw*qy,         2*qy*qz + 2*qw*qx,     1 - 2*qx*qx - 2*qy*qy],
        ],
        dtype=np.float64,
    )

def camera_center_from_qt(qvec: np.ndarray, tvec: np.ndarray) -> np.ndarray:
    """
    COLMAP stores world-to-camera:
      X_cam = R * X_world + t
    Camera center in world:
      C = -R^T t
    """
    R = qvec2rotmat(qvec)
    C = -R.T @ tvec
    return C


# -----------------------------
# EXIF timestamp (relative ordering)
# -----------------------------

def get_exif_timestamp_relative_seconds(image_path: Path) -> float:
    """
    Returns a relative timestamp in seconds based on EXIF DateTimeOriginal (+ optional subseconds).
    This is timezone-independent and safe for ordering.

    If EXIF is missing, raises.
    """
    img = Image.open(image_path)
    exif = img._getexif()
    if not exif:
        raise ValueError(f"No EXIF in {image_path.name}")

    exif_data = {TAGS.get(k, k): v for k, v in exif.items()}
    dt_str = exif_data.get("DateTimeOriginal") or exif_data.get("DateTime") or exif_data.get("DateTimeDigitized")
    if not dt_str:
        raise ValueError(f"No DateTimeOriginal/DateTime in {image_path.name}")

    # dt_str: "YYYY:MM:DD HH:MM:SS"
    # Convert to seconds relative to start by parsing into components (no timezone)
    # We'll store absolute-ish "seconds since year 0" style using a tuple to float conversion.
    # Easiest: compute seconds since epoch assuming local tz is unknown is dangerous; instead:
    # use a lexicographic-to-seconds mapping that preserves ordering.
    yyyy = int(dt_str[0:4])
    mm = int(dt_str[5:7])
    dd = int(dt_str[8:10])
    HH = int(dt_str[11:13])
    MM = int(dt_str[14:16])
    SS = int(dt_str[17:19])

    # Subseconds:
    sub = exif_data.get("SubsecTimeOriginal") or exif_data.get("SubSecTimeOriginal") or exif_data.get("SubSecTimeDigitized") or "0"
    try:
        sub_i = int(str(sub))
    except Exception:
        sub_i = 0

    # Normalize subseconds to fractional seconds (handles 1-3 digits commonly)
    sub_s = 0.0
    sub_str = str(sub_i)
    if len(sub_str) > 0:
        sub_s = sub_i / (10 ** len(sub_str))

    # Convert the date-time tuple into a monotonically increasing float for ordering.
    # This is NOT real epoch time; it's just consistent ordering.
    # Use a simple "seconds" formula with fixed month lengths? That breaks across months.
    # So instead return a tuple-encoded float:
    #   (((((yyyy*12+mm)*31+dd)*24+HH)*60+MM)*60+SS) + sub_s
    # This is monotonic for typical capture sequences and sufficient for ordering.
    key = (((((yyyy * 12 + mm) * 31 + dd) * 24 + HH) * 60 + MM) * 60 + SS) + sub_s
    return float(key)


# -----------------------------
# Geometry / path processing
# -----------------------------

@dataclass(frozen=True)
class ENU:
    e: float
    n: float
    u: float
    t_rel: float  # relative time (seconds in a monotonic ordering space)

def dist(a: ENU, b: ENU) -> float:
    de = b.e - a.e
    dn = b.n - a.n
    du = b.u - a.u
    return math.sqrt(de*de + dn*dn + du*du)

def cumulative_s(points: List[ENU]) -> List[float]:
    if not points:
        return []
    s = [0.0]
    for i in range(1, len(points)):
        s.append(s[-1] + dist(points[i-1], points[i]))
    return s

def drop_teleports(points: List[ENU], max_step_m: float) -> List[ENU]:
    if max_step_m <= 0 or len(points) < 2:
        return points
    out = [points[0]]
    for p in points[1:]:
        if dist(out[-1], p) <= max_step_m:
            out.append(p)
        # else: skip point (teleport/outlier)
    return out

def resample_by_distance(points: List[ENU], step_m: float) -> List[ENU]:
    if step_m <= 0 or len(points) < 2:
        return points

    s = cumulative_s(points)
    total = s[-1]
    if total <= 1e-12:
        return [points[0]]

    # Targets
    targets = [0.0]
    d = step_m
    while d < total:
        targets.append(d)
        d += step_m
    targets.append(total)

    # Linear interpolation in arc-length
    out: List[ENU] = []
    j = 1
    for td in targets:
        while j < len(s) and s[j] < td:
            j += 1
        j = min(max(j, 1), len(s) - 1)

        s0, s1 = s[j-1], s[j]
        a, b = points[j-1], points[j]
        if (s1 - s0) <= 1e-12:
            out.append(a)
            continue
        t = (td - s0) / (s1 - s0)

        e = a.e + (b.e - a.e) * t
        n = a.n + (b.n - a.n) * t
        u = a.u + (b.u - a.u) * t
        tr = a.t_rel + (b.t_rel - a.t_rel) * t
        out.append(ENU(e=e, n=n, u=u, t_rel=tr))

    return out

def smooth_u_by_distance(points: List[ENU], window_m: float) -> List[ENU]:
    """
    Smooth only U using a symmetric distance window around each sample.
    Keeps E,N unchanged (path geometry) but stabilizes grade.
    """
    if window_m <= 0 or len(points) < 3:
        return points

    s = cumulative_s(points)
    half = window_m / 2.0
    out: List[ENU] = []

    for i in range(len(points)):
        si = s[i]
        # find range [si-half, si+half]
        lo = si - half
        hi = si + half

        # expand indices
        j0 = i
        while j0 > 0 and s[j0 - 1] >= lo:
            j0 -= 1
        j1 = i
        while j1 < len(points) - 1 and s[j1 + 1] <= hi:
            j1 += 1

        u_mean = sum(points[k].u for k in range(j0, j1 + 1)) / float(j1 - j0 + 1)
        p = points[i]
        out.append(ENU(e=p.e, n=p.n, u=u_mean, t_rel=p.t_rel))

    return out

def interp_u_at_s(points: List[ENU], s_list: List[float], s_query: float) -> float:
    """Linear interpolate U at arc-length s_query."""
    if not points:
        return 0.0
    if len(points) == 1:
        return points[0].u

    total = s_list[-1]
    if s_query <= 0:
        return points[0].u
    if s_query >= total:
        return points[-1].u

    # binary search
    lo, hi = 0, len(s_list) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if s_list[mid] < s_query:
            lo = mid + 1
        else:
            hi = mid

    i = max(1, lo)
    s0, s1 = s_list[i-1], s_list[i]
    if (s1 - s0) <= 1e-12:
        return points[i-1].u
    t = (s_query - s0) / (s1 - s0)
    return points[i-1].u + (points[i].u - points[i-1].u) * t

def compute_grade(points: List[ENU], grade_window_m: float) -> List[float]:
    """
    grade_pct at each point i via symmetric window:
      grade = 100 * (u(s+L/2) - u(s-L/2)) / L
    """
    if grade_window_m <= 0 or len(points) < 2:
        return [0.0] * len(points)

    s_list = cumulative_s(points)
    total = s_list[-1]
    half = grade_window_m / 2.0
    grades: List[float] = []

    for si in s_list:
        s0 = max(0.0, si - half)
        s1 = min(total, si + half)
        L = max(1e-9, s1 - s0)
        u0 = interp_u_at_s(points, s_list, s0)
        u1 = interp_u_at_s(points, s_list, s1)
        grades.append(100.0 * (u1 - u0) / L)

    return grades


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate path_cam.json (ENU) from aligned COLMAP camera centers + EXIF ordering, including grade.")
    ap.add_argument("--colmap_model", required=True, type=Path, help="Path to aligned COLMAP model directory (contains images.bin), e.g. sparse/0_enu")
    ap.add_argument("--images_dir", required=True, type=Path, help="Directory containing the original images to read EXIF timestamps")
    ap.add_argument("--output", required=True, type=Path, help="Output path_cam.json")

    ap.add_argument("--resample_m", type=float, default=0.5, help="Resample spacing in meters (0 disables). Default: 0.5")
    ap.add_argument("--drop_teleports_m", type=float, default=5.0, help="Drop camera centers if step > this many meters. 0 disables. Default: 5.0")
    ap.add_argument("--smooth_window_m", type=float, default=5.0, help="Smooth U (altitude) over this distance window (m). 0 disables. Default: 5.0")
    ap.add_argument("--grade_window_m", type=float, default=10.0, help="Grade window length in meters. Default: 10.0")
    ap.add_argument("--max_images", type=int, default=0, help="If >0, use only first N images after timestamp sorting (debugging).")
    ap.add_argument("--print_report", action="store_true", help="Print summary stats.")

    args = ap.parse_args()

    images_bin = args.colmap_model / "images.bin"
    if not images_bin.exists():
        raise FileNotFoundError(f"Missing {images_bin} (expected aligned COLMAP model)")

    if not args.images_dir.exists():
        raise FileNotFoundError(f"Missing images_dir: {args.images_dir}")

    # Read COLMAP poses (already aligned to ENU meters)
    colmap_images = read_images_binary(images_bin)

    # Build list with EXIF-based relative timestamps for ordering
    records: List[ENU] = []
    missing = 0

    for img in colmap_images.values():
        name = img["name"]
        qvec = img["qvec"]
        tvec = img["tvec"]
        C = camera_center_from_qt(qvec, tvec)  # in aligned ENU meters (COLMAP coords == ENU after model_aligner)

        img_path = args.images_dir / name
        if not img_path.exists():
            missing += 1
            continue

        t_key = get_exif_timestamp_relative_seconds(img_path)

        records.append(ENU(e=float(C[0]), n=float(C[1]), u=float(C[2]), t_rel=float(t_key)))

    if len(records) < 2:
        raise RuntimeError(f"Too few camera records ({len(records)}). Missing images: {missing}")

    # Sort by time key and normalize to t_rel seconds from start
    records.sort(key=lambda p: p.t_rel)
    t0 = records[0].t_rel
    records = [ENU(e=p.e, n=p.n, u=p.u, t_rel=p.t_rel - t0) for p in records]

    if args.max_images and args.max_images > 0:
        records = records[: args.max_images]

    # Filter teleports/outliers
    before = len(records)
    records = drop_teleports(records, max_step_m=max(0.0, args.drop_teleports_m))
    after_drop = len(records)

    # Resample by distance (for stable playback and grade)
    records = resample_by_distance(records, step_m=max(0.0, args.resample_m))
    after_resample = len(records)

    # Smooth U by distance window (stabilize grade)
    records_smooth = smooth_u_by_distance(records, window_m=max(0.0, args.smooth_window_m))
    after_smooth = len(records_smooth)

    # Grade from smoothed U (but keep E,N from original resampled path)
    grades = compute_grade(records_smooth, grade_window_m=max(0.0, args.grade_window_m))

    # Build final points with cumulative s
    s_list = cumulative_s(records)
    points_out = []
    for i, p in enumerate(records):
        points_out.append(
            {
                "s": float(s_list[i]),
                "e": float(p.e),
                "n": float(p.n),
                "u": float(records_smooth[i].u),     # smoothed altitude
                "grade_pct": float(grades[i]),
                "t_rel": float(p.t_rel),
            }
        )

    payload = {
        "frame": "ENU",
        "units": "meters",
        "source": "colmap_aligned_camera_centers",
        "resample_m": float(args.resample_m),
        "drop_teleports_m": float(args.drop_teleports_m),
        "smooth_window_m": float(args.smooth_window_m),
        "grade_window_m": float(args.grade_window_m),
        "points": points_out,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.print_report:
        total_len = s_list[-1] if s_list else 0.0
        # quick stats
        e_vals = np.array([p.e for p in records], dtype=np.float64)
        n_vals = np.array([p.n for p in records], dtype=np.float64)
        u_vals = np.array([p.u for p in records_smooth], dtype=np.float64)
        g_vals = np.array(grades, dtype=np.float64)
        print("✅ Generated path_cam.json")
        print(f"  Input model: {args.colmap_model}")
        print(f"  Images dir : {args.images_dir}")
        print(f"  Output     : {args.output}")
        print(f"  Points     : {before} -> {after_drop} (drop) -> {after_resample} (resample) -> {after_smooth} (smoothU)")
        print(f"  Length     : {total_len:.3f} m")
        print(f"  ENU bounds : E[{e_vals.min():.3f}, {e_vals.max():.3f}]  N[{n_vals.min():.3f}, {n_vals.max():.3f}]  U[{u_vals.min():.3f}, {u_vals.max():.3f}]")
        print(f"  Grade pct  : min {g_vals.min():.2f}  max {g_vals.max():.2f}  mean {g_vals.mean():.2f}")
        print(f"  NOTE: t_rel is relative seconds from first image (timezone-independent ordering).")


if __name__ == "__main__":
    main()
