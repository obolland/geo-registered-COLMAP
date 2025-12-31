#!/usr/bin/env python3
"""
generate_tile_meta.py

Generate per-tile metadata for rendering + gameplay alignment.

✅ "Best" origin method (as agreed):
- Uses the FIRST valid line of ref_images.txt as the tile origin (ENU meters).
  ref_images.txt format (COLMAP model_aligner):
    image_name.jpg  E  N  U

Outputs a tile_meta.json containing:
- origin_enu (meters)
- origin_ref_image (string)
- bounds_enu (min/max in ENU for the tile path, absolute)
- bounds_local (same bounds but in tile-local coords = ENU - origin_enu)
- heading_bearing_deg (initial path bearing in ENU, degrees clockwise from North)
- forward_enu_unit (unit tangent vector near start, in ENU)
- path_length_m
- optional local path points output (path_local.json) if requested

This script is safe / non-destructive (writes new files only).

Example usage:
  ./venv/bin/python generate_tile_meta.py \
  --path_json dataset/keyframes/world_alignment/path.json \
  --ref_images_txt dataset/keyframes/world_alignment/ref_images.txt \
  --output dataset/keyframes/world_alignment/tile_meta.json \
  --lookahead_m 2 \
  --write_local_path dataset/keyframes/world_alignment/path_local.json
  --tile_id "tile_0"
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional


# -----------------------------
# Small geometry helpers
# -----------------------------

@dataclass
class Vec3:
    e: float
    n: float
    u: float

    def __sub__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.e - other.e, self.n - other.n, self.u - other.u)

    def __add__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.e + other.e, self.n + other.n, self.u + other.u)

    def scale(self, s: float) -> "Vec3":
        return Vec3(self.e * s, self.n * s, self.u * s)

    def as_list(self) -> List[float]:
        return [float(self.e), float(self.n), float(self.u)]


def dist2(a: Vec3, b: Vec3) -> float:
    de = b.e - a.e
    dn = b.n - a.n
    du = b.u - a.u
    return de * de + dn * dn + du * du


def dist(a: Vec3, b: Vec3) -> float:
    return math.sqrt(dist2(a, b))


def normalize_enu(v: Vec3) -> Vec3:
    l = math.sqrt(v.e * v.e + v.n * v.n + v.u * v.u)
    if l <= 1e-12:
        return Vec3(0.0, 1.0, 0.0)  # default "north"
    return Vec3(v.e / l, v.n / l, v.u / l)


def bearing_deg_from_north(forward_enu_unit: Vec3) -> float:
    """
    Bearing in degrees clockwise from North:
      0   = North (+N)
      90  = East (+E)
      180 = South (-N)
      270 = West (-E)
    """
    # atan2(E, N) gives clockwise-from-north bearing
    b = math.degrees(math.atan2(forward_enu_unit.e, forward_enu_unit.n))
    if b < 0:
        b += 360.0
    return b


# -----------------------------
# IO
# -----------------------------

def load_path_json(path_json: Path) -> List[Vec3]:
    data = json.loads(path_json.read_text(encoding="utf-8"))
    pts = data.get("points")
    if not isinstance(pts, list) or len(pts) < 2:
        raise ValueError(f"path.json missing points or too few points: {path_json}")

    out: List[Vec3] = []
    for i, p in enumerate(pts):
        if not isinstance(p, dict):
            raise ValueError(f"path.json points[{i}] is not an object")
        try:
            e = float(p["e"])
            n = float(p["n"])
            u = float(p.get("u", 0.0))
        except Exception as ex:
            raise ValueError(f"Invalid point at index {i}: {p}") from ex
        out.append(Vec3(e, n, u))
    return out


def read_origin_from_ref_images(ref_images_txt: Path) -> Tuple[str, Vec3]:
    """
    Returns (image_name, origin_enu) from first valid line in ref_images.txt.
    """
    if not ref_images_txt.exists():
        raise FileNotFoundError(ref_images_txt)

    for raw in ref_images_txt.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        image = parts[0]
        try:
            e = float(parts[1])
            n = float(parts[2])
            u = float(parts[3])
        except ValueError:
            continue
        return image, Vec3(e, n, u)

    raise ValueError(f"No valid lines found in {ref_images_txt}")


def compute_bounds(points: List[Vec3]) -> Dict[str, List[float]]:
    min_e = min(p.e for p in points)
    max_e = max(p.e for p in points)
    min_n = min(p.n for p in points)
    max_n = max(p.n for p in points)
    min_u = min(p.u for p in points)
    max_u = max(p.u for p in points)
    return {
        "min": [float(min_e), float(min_n), float(min_u)],
        "max": [float(max_e), float(max_n), float(max_u)],
    }


def build_cumulative(points: List[Vec3]) -> List[float]:
    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(cum[-1] + dist(points[i - 1], points[i]))
    return cum


def sample_along(points: List[Vec3], cumulative: List[float], s: float) -> Vec3:
    """
    Linear sample along polyline by arc-length.
    points and cumulative are in the same coordinate frame.
    """
    if not points:
        return Vec3(0.0, 0.0, 0.0)
    total = cumulative[-1]
    if s <= 0:
        return points[0]
    if s >= total:
        return points[-1]

    # binary search
    lo, hi = 0, len(cumulative) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cumulative[mid] < s:
            lo = mid + 1
        else:
            hi = mid

    idx = max(1, lo)
    s0 = cumulative[idx - 1]
    s1 = cumulative[idx]
    t = 0.0 if (s1 - s0) <= 1e-12 else (s - s0) / (s1 - s0)
    a = points[idx - 1]
    b = points[idx]
    return Vec3(
        a.e + (b.e - a.e) * t,
        a.n + (b.n - a.n) * t,
        a.u + (b.u - a.u) * t,
    )


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate per-tile metadata (origin from first line of ref_images.txt)."
    )
    ap.add_argument("--path_json", required=True, type=Path,
                    help="Input path.json in ENU meters: {frame:'ENU', units:'m', points:[{e,n,u},...]}")
    ap.add_argument("--ref_images_txt", required=True, type=Path,
                    help="ref_images.txt used by COLMAP model_aligner; first line defines tile origin")
    ap.add_argument("--output", required=True, type=Path,
                    help="Output tile_meta.json path")
    ap.add_argument("--lookahead_m", type=float, default=2.0,
                    help="Distance forward from start to compute initial tangent/bearing (default: 2.0)")
    ap.add_argument("--write_local_path", type=Path, default=None,
                    help="Optional: write tile-local path.json (points shifted by origin_enu) to this file")
    ap.add_argument("--tile_id", type=str, default="tile_0",
                    help="Optional tile identifier stored in metadata")
    args = ap.parse_args()

    # Load inputs
    path_points_abs = load_path_json(args.path_json)
    origin_image, origin_enu = read_origin_from_ref_images(args.ref_images_txt)

    # Convert to tile-local coords (ENU - origin)
    path_points_local = [p - origin_enu for p in path_points_abs]

    # Compute length + initial heading on LOCAL (same as ABS, translation-invariant)
    cumulative = build_cumulative(path_points_local)
    total_len = cumulative[-1] if cumulative else 0.0

    a = sample_along(path_points_local, cumulative, 0.0)
    b = sample_along(path_points_local, cumulative, min(args.lookahead_m, total_len))

    forward = normalize_enu(Vec3(b.e - a.e, b.n - a.n, 0.0))  # ground tangent
    heading_deg = bearing_deg_from_north(forward)

    # Bounds (absolute + local)
    bounds_abs = compute_bounds(path_points_abs)
    bounds_local = compute_bounds(path_points_local)

    # Build output metadata
    meta = {
        "tile_id": args.tile_id,
        "frame": "ENU",
        "units": "meters",
        "origin_method": "ref_images_first_line",
        "origin_ref_image": origin_image,
        "origin_enu": origin_enu.as_list(),  # [E,N,U] meters
        "path_length_m": float(total_len),
        "lookahead_m": float(args.lookahead_m),
        "forward_enu_unit": forward.as_list(),  # [E,N,U] with U=0
        "heading_bearing_deg": float(heading_deg),
        "bounds_enu": bounds_abs,     # absolute ENU bounds of the path
        "bounds_local": bounds_local, # local bounds (after origin subtraction)
        "inputs": {
            "path_json": str(args.path_json),
            "ref_images_txt": str(args.ref_images_txt),
        },
    }

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"✅ Wrote tile metadata: {args.output}")

    # Optional: write local path
    if args.write_local_path is not None:
        local_out = args.write_local_path
        local_out.parent.mkdir(parents=True, exist_ok=True)
        local_path = {
            "frame": "ENU_LOCAL",
            "units": "meters",
            "origin_enu": origin_enu.as_list(),
            "origin_ref_image": origin_image,
            "points": [{"e": p.e, "n": p.n, "u": p.u} for p in path_points_local],
        }
        local_out.write_text(json.dumps(local_path, indent=2), encoding="utf-8")
        print(f"✅ Wrote local path: {local_out}")


if __name__ == "__main__":
    main()
