#!/usr/bin/env python3
"""
generate_path_json.py

Generate a lightweight ENU path polyline for runtime playback (e.g. VR rider motion)
from either:

  A) ref_images.txt   (recommended)   lines:  image.jpg  E  N  U
  B) gpx_enu_raw.json (fallback)      [{ "timestamp": ..., "enu": [E,N,U] }, ...]

Outputs a JSON file:

{
  "frame": "ENU",
  "units": "meters",
  "points": [ {"e":..., "n":..., "u":...}, ... ]
}

Features:
- Parse ref_images.txt or gpx_enu_raw.json
- De-duplicate consecutive near-identical points (epsilon in meters)
- Optional resampling by distance (meters) with linear interpolation along the polyline
- Optional smoothing (moving average over window samples; odd window recommended)
- Prints a short report (counts, total length)

Examples:
  # Recommended (from ref_images.txt)
  ./venv/bin/python generate_path_json.py \
    --ref_images dataset/keyframes/world_alignment/ref_images.txt \
    --output dataset/keyframes/world_alignment/path.json \
    --dedupe_epsilon 0.02 \
    --resample_m 0.5 \
    --smooth_window 0 \
    --print_report

  # Fallback (from GPX ENU json)
  ./venv/bin/python generate_path_json.py \
    --gpx_enu dataset/keyframes/world_alignment/gpx_enu_raw.json \
    --output dataset/keyframes/world_alignment/path.json \
    --dedupe_epsilon 0.05 \
    --resample_m 1.0 \
    --smooth_window 0 \
    --print_report
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# -----------------------------
# Math helpers
# -----------------------------

@dataclass(frozen=True)
class ENU:
    e: float
    n: float
    u: float

def dist(a: ENU, b: ENU) -> float:
    de = a.e - b.e
    dn = a.n - b.n
    du = a.u - b.u
    return math.sqrt(de*de + dn*dn + du*du)

def polyline_length(points: List[ENU]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(dist(points[i], points[i+1]) for i in range(len(points)-1))

# -----------------------------
# Loaders
# -----------------------------

def load_ref_images_txt(path: Path) -> List[ENU]:
    """
    Parse ref_images.txt with format:
      image_name.jpg  E  N  U
    """
    points: List[ENU] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            if len(parts) < 4:
                raise ValueError(f"{path}: line {line_no}: expected 'name E N U', got: {s}")
            try:
                e = float(parts[1])
                n = float(parts[2])
                u = float(parts[3])
            except ValueError as ex:
                raise ValueError(f"{path}: line {line_no}: could not parse floats: {s}") from ex
            points.append(ENU(e=e, n=n, u=u))
    if not points:
        raise ValueError(f"{path}: no points parsed")
    return points

def load_gpx_enu_json(path: Path) -> List[ENU]:
    """
    Parse gpx_enu_raw.json with format:
      [{ "timestamp": ..., "enu": [E,N,U] }, ...]
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"{path}: expected a non-empty array")
    points: List[ENU] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or "enu" not in item:
            raise ValueError(f"{path}: item {i} missing 'enu'")
        enu = item["enu"]
        if not (isinstance(enu, list) and len(enu) == 3):
            raise ValueError(f"{path}: item {i} 'enu' must be [E,N,U]")
        points.append(ENU(e=float(enu[0]), n=float(enu[1]), u=float(enu[2])))
    return points

# -----------------------------
# Processing steps
# -----------------------------

def dedupe_consecutive(points: List[ENU], epsilon_m: float) -> List[ENU]:
    if not points:
        return points
    out = [points[0]]
    for p in points[1:]:
        if dist(p, out[-1]) > epsilon_m:
            out.append(p)
    return out

def resample_by_distance(points: List[ENU], step_m: float) -> List[ENU]:
    """
    Resample a polyline at fixed distance spacing (approximately).
    Keeps the first point, then samples every step_m along arc length,
    and includes the final point.
    """
    if step_m <= 0:
        return points
    if len(points) < 2:
        return points

    # Build cumulative distances
    seg_lens = [dist(points[i], points[i+1]) for i in range(len(points)-1)]
    total = sum(seg_lens)
    if total == 0.0:
        return [points[0]]

    # Target distances along the polyline
    targets = [0.0]
    d = step_m
    # stop before total; we'll always append final point explicitly
    while d < total:
        targets.append(d)
        d += step_m
    targets.append(total)

    out: List[ENU] = []
    # Walk segments to hit targets
    seg_idx = 0
    seg_start = points[0]
    seg_end = points[1]
    seg_accum = 0.0
    seg_len = seg_lens[0]

    for tdist in targets:
        # Advance until the segment that contains tdist
        while seg_idx < len(seg_lens)-1 and seg_accum + seg_len < tdist:
            seg_accum += seg_len
            seg_idx += 1
            seg_start = points[seg_idx]
            seg_end = points[seg_idx+1]
            seg_len = seg_lens[seg_idx]

        if seg_len == 0.0:
            # degenerate segment; just take start
            out.append(seg_start)
            continue

        alpha = (tdist - seg_accum) / seg_len
        alpha = max(0.0, min(1.0, alpha))
        e = (1.0 - alpha) * seg_start.e + alpha * seg_end.e
        n = (1.0 - alpha) * seg_start.n + alpha * seg_end.n
        u = (1.0 - alpha) * seg_start.u + alpha * seg_end.u
        out.append(ENU(e=e, n=n, u=u))

    # Final dedupe to remove any accidental duplicates
    out = dedupe_consecutive(out, epsilon_m=1e-12)
    return out

def moving_average_smooth(points: List[ENU], window: int) -> List[ENU]:
    """
    Simple moving average in sample space (not time).
    Window is number of samples; if <=1, no-op.
    Uses edge clamping at boundaries.
    """
    if window <= 1 or len(points) < 3:
        return points

    # Force odd window for symmetric smoothing
    if window % 2 == 0:
        window += 1
    half = window // 2

    def get(i: int) -> ENU:
        if i < 0:
            return points[0]
        if i >= len(points):
            return points[-1]
        return points[i]

    out: List[ENU] = []
    for i in range(len(points)):
        se = sn = su = 0.0
        for k in range(i - half, i + half + 1):
            p = get(k)
            se += p.e
            sn += p.n
            su += p.u
        denom = window
        out.append(ENU(e=se/denom, n=sn/denom, u=su/denom))

    # Preserve endpoints exactly (often helpful for “start at exact first point”)
    out[0] = points[0]
    out[-1] = points[-1]
    return out

# -----------------------------
# Writer
# -----------------------------

def write_path_json(path: Path, points: List[ENU]) -> None:
    payload = {
        "frame": "ENU",
        "units": "meters",
        "points": [{"e": p.e, "n": p.n, "u": p.u} for p in points],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

# -----------------------------
# Main
# -----------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate path.json from ref_images.txt or gpx_enu_raw.json")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--ref_images", type=Path, help="Path to ref_images.txt (image E N U per line)")
    src.add_argument("--gpx_enu", type=Path, help="Path to gpx_enu_raw.json (timestamped ENU array)")

    parser.add_argument("--output", type=Path, required=True, help="Output path.json")
    parser.add_argument("--dedupe_epsilon", type=float, default=0.02,
                        help="Drop consecutive points closer than this distance (meters). Default: 0.02")
    parser.add_argument("--resample_m", type=float, default=0.0,
                        help="If >0, resample path at this spacing in meters (linear interp). Default: 0 (off)")
    parser.add_argument("--smooth_window", type=int, default=0,
                        help="If >1, apply moving average smoothing with this window (#samples). Default: 0 (off)")
    parser.add_argument("--print_report", action="store_true", help="Print stats about the generated path")

    args = parser.parse_args()

    # Load
    if args.ref_images:
        if not args.ref_images.exists():
            raise FileNotFoundError(args.ref_images)
        points0 = load_ref_images_txt(args.ref_images)
        source_name = str(args.ref_images)
    else:
        if not args.gpx_enu.exists():
            raise FileNotFoundError(args.gpx_enu)
        points0 = load_gpx_enu_json(args.gpx_enu)
        source_name = str(args.gpx_enu)

    # Process
    before = len(points0)
    points = dedupe_consecutive(points0, epsilon_m=max(0.0, args.dedupe_epsilon))
    after_dedupe = len(points)

    if args.resample_m and args.resample_m > 0:
        points = resample_by_distance(points, step_m=args.resample_m)
    after_resample = len(points)

    if args.smooth_window and args.smooth_window > 1:
        points = moving_average_smooth(points, window=args.smooth_window)
    after_smooth = len(points)

    # Safety: ensure at least 2 points if possible
    if len(points) == 1 and len(points0) >= 2:
        points = [points0[0], points0[-1]]

    # Write
    write_path_json(args.output, points)

    if args.print_report:
        length_before = polyline_length(points0)
        length_after = polyline_length(points)
        print("✅ Generated path.json")
        print(f"  Source: {source_name}")
        print(f"  Output: {args.output}")
        print(f"  Points: {before} → {after_dedupe} (dedupe) → {after_resample} (resample) → {after_smooth} (smooth)")
        print(f"  Length: {length_before:.3f} m → {length_after:.3f} m")
        if len(points) >= 2:
            p0 = points[0]
            p1 = points[1]
            print(f"  Start: (E,N,U)=({p0.e:.3f},{p0.n:.3f},{p0.u:.3f})")
            print(f"  First step: Δ={dist(p0,p1):.3f} m")

if __name__ == "__main__":
    main()
