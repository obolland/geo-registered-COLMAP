#!/usr/bin/env python3
"""
generate_tile_manifest.py

Generate a tile_manifest.json for runtime streaming / loading.

This is intentionally simple for POC (single tile), but structured so you can
append additional tiles later without changing your runtime format.

Inputs
------
- path_cam.json (required): camera-centers path in ENU meters
- enu_origin.json (optional but recommended): ENU origin metadata (lat/lon/alt)
  This script will store a reference path (string) to this file in the manifest.

Outputs
-------
tile_manifest.json:

{
  "frame": "ENU",
  "units": "meters",
  "enu_origin_ref": "world_alignment/enu_origin.json",
  "tiles": [
    {
      "tile_id": "tile_000",
      "s_range": [0.0, 102.66],
      "splat": "tile_000/tile.sog",
      "path": "tile_000/path_cam.json",
      "bounds_enu": { "e": [...], "n": [...], "u": [...] },
      "yaw_rad": 0.0
    }
  ]
}

Notes
-----
- bounds_enu are computed from the ENU coordinates in path_cam.json.
- s_range is derived from the first and last point's 's' field.
- yaw_rad should match what you apply to the splat at runtime AND what you apply
  to the camera/path mapping (same transform for both).
- Paths in the manifest are strings; you can store them as:
    - relative-to-public root (recommended for web apps), or
    - relative-to-dataset root, or
    - absolute filesystem paths (not recommended for web runtime)

Example (POC / single tile)
---------------------------
./venv/bin/python generate_tile_manifest.py \
  --path_cam dataset/keyframes/world_alignment/path_cam.json \
  --output dataset/keyframes/world_alignment/tile_manifest.json \
  --tile_id tile_000 \
  --splat tile_000/tile.sog \
  --path tile_000/path_cam.json \
  --enu_origin_ref world_alignment/enu_origin.json \
  --yaw_rad 0.0 \
  --print_report

Multi-tile approach (later)
---------------------------
Run this script once per tile to produce per-tile JSON fragments, OR
extend it to accept a list of tiles (easy). For now, this writes a single-tile
manifest because that’s the simplest reliable POC.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Bounds1D:
    min: float
    max: float

    def as_list(self) -> List[float]:
        return [float(self.min), float(self.max)]


def load_path_cam(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    if data.get("frame") != "ENU":
        raise ValueError(f"{path}: expected frame='ENU', got {data.get('frame')}")
    pts = data.get("points")
    if not isinstance(pts, list) or len(pts) < 2:
        raise ValueError(f"{path}: expected points[] with at least 2 entries")
    # basic schema check
    for i, p in enumerate(pts[:5]):  # spot-check first few
        if not isinstance(p, dict) or "s" not in p or "e" not in p or "n" not in p:
            raise ValueError(f"{path}: invalid point at index {i}: {p}")
    return data


def compute_bounds(points: List[Dict[str, Any]]) -> Dict[str, List[float]]:
    es = [float(p["e"]) for p in points]
    ns = [float(p["n"]) for p in points]
    us = [float(p.get("u", 0.0)) for p in points]

    e_b = Bounds1D(min(es), max(es))
    n_b = Bounds1D(min(ns), max(ns))
    u_b = Bounds1D(min(us), max(us))

    return {
        "e": e_b.as_list(),
        "n": n_b.as_list(),
        "u": u_b.as_list(),
    }


def compute_s_range(points: List[Dict[str, Any]]) -> List[float]:
    s0 = float(points[0]["s"])
    s1 = float(points[-1]["s"])
    # Normalize in case input isn't exactly starting at 0
    lo = min(s0, s1)
    hi = max(s0, s1)
    return [float(lo), float(hi)]


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate tile_manifest.json from path_cam.json")

    ap.add_argument("--path_cam", required=True, type=Path, help="Input path_cam.json (ENU meters)")
    ap.add_argument("--output", required=True, type=Path, help="Output tile_manifest.json")

    # Tile fields (single tile for now)
    ap.add_argument("--tile_id", default="tile_000", help="Tile identifier (default: tile_000)")
    ap.add_argument("--splat", required=True, help="Splat asset path string to store in manifest (e.g. tile_000/tile.sog)")
    ap.add_argument("--path", required=True, help="Path asset path string to store in manifest (e.g. tile_000/path_cam.json)")
    ap.add_argument("--yaw_rad", type=float, default=0.0, help="Yaw (radians) used to orient the tile in runtime (default: 0)")

    # Optional reference to ENU origin metadata
    ap.add_argument(
        "--enu_origin_ref",
        type=str,
        default=None,
        help="String path stored in manifest to reference enu_origin.json (e.g. world_alignment/enu_origin.json)",
    )

    ap.add_argument("--print_report", action="store_true", help="Print a short summary")
    args = ap.parse_args()

    if not args.path_cam.exists():
        raise FileNotFoundError(args.path_cam)

    data = load_path_cam(args.path_cam)
    points = data["points"]

    s_range = compute_s_range(points)
    bounds_enu = compute_bounds(points)

    manifest: Dict[str, Any] = {
        "frame": "ENU",
        "units": "meters",
        "tiles": [
            {
                "tile_id": str(args.tile_id),
                "s_range": s_range,
                "splat": str(args.splat),
                "path": str(args.path),
                "bounds_enu": bounds_enu,
                "yaw_rad": float(args.yaw_rad),
            }
        ],
    }

    if args.enu_origin_ref:
        manifest["enu_origin_ref"] = str(args.enu_origin_ref)

    write_json(args.output, manifest)

    if args.print_report:
        print("✅ Generated tile_manifest.json")
        print(f"  Output   : {args.output}")
        print(f"  Tile     : {args.tile_id}")
        print(f"  s_range  : [{s_range[0]:.3f}, {s_range[1]:.3f}] m")
        print(
            "  bounds   : "
            f"E[{bounds_enu['e'][0]:.3f},{bounds_enu['e'][1]:.3f}]  "
            f"N[{bounds_enu['n'][0]:.3f},{bounds_enu['n'][1]:.3f}]  "
            f"U[{bounds_enu['u'][0]:.3f},{bounds_enu['u'][1]:.3f}]"
        )
        if args.enu_origin_ref:
            print(f"  enu_origin_ref: {args.enu_origin_ref}")
        print(f"  splat    : {args.splat}")
        print(f"  path     : {args.path}")
        print(f"  yaw_rad  : {args.yaw_rad:.6f}")


if __name__ == "__main__":
    main()
