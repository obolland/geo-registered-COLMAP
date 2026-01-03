#!/usr/bin/env python3
"""
gpx_to_enu_raw.py

Convert a GPX track to local ENU coordinates (RAW, timestamp-preserved)
AND emit a small ENU-origin metadata file for future geo features (Strava, maps, etc.).

This is a Python replacement for your Node script, maintaining the same behavior.

USAGE
-----
  ./venv/bin/python gpx_to_enu_raw.py \
    --input dataset/gpx/track.gpx \
    --output dataset/keyframes/world_alignment/gpx_enu_raw.json \
    --output_origin dataset/keyframes/world_alignment/enu_origin.json

If --output_origin is omitted, it will be written next to --output as:
  <output_without_.json>.origin.json

OUTPUT (ENU track JSON)
-----------------------
[
  { "timestamp": <unix_seconds>, "enu": [E, N, U] },
  ...
]

OUTPUT (origin metadata JSON)
-----------------------------
{
  "frame": "ENU",
  "origin": { "lat_deg": ..., "lon_deg": ..., "alt_m": ... },
  "wgs84": { "a": 6378137.0, "f": 0.0033528106647474805 },
  "notes": "ENU origin corresponds to first GPX trackpoint"
}

NOTES
-----
- Uses WGS84 ellipsoid constants.
- Uses the FIRST GPX trackpoint as the ENU origin.
- GPX timestamps are expected to be ISO 8601 (e.g. 2025-11-15T13:02:46.000Z).
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple
import xml.etree.ElementTree as ET


# -----------------------------
# WGS84 constants
# -----------------------------

WGS84_A = 6378137.0  # semi-major axis [m]
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)  # first eccentricity squared


# -----------------------------
# Data structures
# -----------------------------

@dataclass(frozen=True)
class LLA:
    lat_deg: float
    lon_deg: float
    alt_m: float
    timestamp_s: float


# -----------------------------
# Math helpers
# -----------------------------

def deg2rad(d: float) -> float:
    return d * math.pi / 180.0

def lla_to_ecef(lat_rad: float, lon_rad: float, h_m: float) -> Tuple[float, float, float]:
    """
    lat[rad], lon[rad], h[m] -> ECEF [x,y,z] meters
    """
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_lon = math.sin(lon_rad)
    cos_lon = math.cos(lon_rad)

    N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (N + h_m) * cos_lat * cos_lon
    y = (N + h_m) * cos_lat * sin_lon
    z = (N * (1.0 - WGS84_E2) + h_m) * sin_lat
    return x, y, z

def enu_rotation_matrix(lat0_rad: float, lon0_rad: float) -> List[List[float]]:
    """
    Build ENU rotation matrix at origin lat0, lon0 (radians).

    Rows of R: [East; North; Up] axes in ECEF basis.
    """
    sin_lat = math.sin(lat0_rad)
    cos_lat = math.cos(lat0_rad)
    sin_lon = math.sin(lon0_rad)
    cos_lon = math.cos(lon0_rad)

    return [
        [-sin_lon,              cos_lon,             0.0],       # East
        [-sin_lat * cos_lon,   -sin_lat * sin_lon,  cos_lat],    # North
        [ cos_lat * cos_lon,    cos_lat * sin_lon,  sin_lat],    # Up
    ]

def mat3_mul_vec3(M: List[List[float]], v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (
        M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
        M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
        M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2],
    )

def parse_gpx_time_to_unix_seconds(time_str: str) -> float:
    """
    Parse ISO 8601 GPX time strings to unix seconds.
    Handles:
      - 2025-11-15T13:02:46Z
      - 2025-11-15T13:02:46.000Z
      - timezone offsets like +00:00
    """
    s = time_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # fromisoformat supports fractional seconds and offsets
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # GPX times are usually UTC; assume UTC if missing
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# -----------------------------
# GPX parsing
# -----------------------------

def parse_gpx(path: Path) -> List[LLA]:
    """
    Parse GPX trkpt points: lat/lon attributes, ele and time elements.
    Returns a list of LLA objects in file order.
    """
    xml = path.read_text(encoding="utf-8")
    root = ET.fromstring(xml)

    # GPX usually uses namespaces; handle both with and without.
    def strip_ns(tag: str) -> str:
        return tag.split("}", 1)[-1] if "}" in tag else tag

    trkpts: List[LLA] = []

    for trkpt in root.iter():
        if strip_ns(trkpt.tag) != "trkpt":
            continue

        lat = trkpt.attrib.get("lat")
        lon = trkpt.attrib.get("lon")
        if lat is None or lon is None:
            continue

        ele_m = 0.0
        time_s: float | None = None

        for child in list(trkpt):
            name = strip_ns(child.tag)
            if name == "ele" and child.text is not None:
                try:
                    ele_m = float(child.text.strip())
                except ValueError:
                    ele_m = 0.0
            elif name == "time" and child.text is not None:
                time_s = parse_gpx_time_to_unix_seconds(child.text)

        if time_s is None:
            raise ValueError("GPX trackpoint is missing <time> tag")

        trkpts.append(
            LLA(
                lat_deg=float(lat),
                lon_deg=float(lon),
                alt_m=float(ele_m),
                timestamp_s=float(time_s),
            )
        )

    if not trkpts:
        raise ValueError("No <trkpt> points found in GPX")

    return trkpts


# -----------------------------
# Path helpers
# -----------------------------

def default_origin_path(out_path: Path) -> Path:
    """
    If output is /x/y/gpx_enu_raw.json, default origin is /x/y/gpx_enu_raw.origin.json
    If output does not end with .json, just append .origin.json
    """
    name = out_path.name
    if name.lower().endswith(".json"):
        stem = name[:-5]
        return out_path.with_name(stem + ".origin.json")
    return out_path.with_name(name + ".origin.json")


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Convert GPX to timestamped ENU JSON + ENU origin metadata JSON.")
    ap.add_argument("--input", required=True, type=Path, help="Input GPX file path (e.g. dataset/gpx/track.gpx)")
    ap.add_argument("--output", required=True, type=Path, help="Output gpx_enu_raw.json path")
    ap.add_argument(
        "--output_origin",
        type=Path,
        default=None,
        help="Optional output enu_origin.json path. If omitted, writes <output>.origin.json",
    )
    args = ap.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(args.input)

    out_path: Path = args.output
    origin_out_path: Path = args.output_origin or default_origin_path(out_path)

    lla = parse_gpx(args.input)

    # Use FIRST point as ENU origin
    origin = lla[0]
    lat0_rad = deg2rad(origin.lat_deg)
    lon0_rad = deg2rad(origin.lon_deg)

    R = enu_rotation_matrix(lat0_rad, lon0_rad)
    x0, y0, z0 = lla_to_ecef(lat0_rad, lon0_rad, origin.alt_m)

    # Compute ENU for all points
    enu_points = []
    for p in lla:
        lat_rad = deg2rad(p.lat_deg)
        lon_rad = deg2rad(p.lon_deg)
        x, y, z = lla_to_ecef(lat_rad, lon_rad, p.alt_m)

        dx = x - x0
        dy = y - y0
        dz = z - z0

        e, n, u = mat3_mul_vec3(R, (dx, dy, dz))

        enu_points.append(
            {
                "timestamp": float(p.timestamp_s),
                "enu": [float(e), float(n), float(u)],
            }
        )

    # Write ENU track JSON
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(enu_points, indent=2), encoding="utf-8")
    print(f"✅ Wrote RAW ENU (timestamped) to {out_path}")

    # Write ENU origin metadata JSON
    origin_meta = {
        "frame": "ENU",
        "origin": {
            "lat_deg": float(origin.lat_deg),
            "lon_deg": float(origin.lon_deg),
            "alt_m": float(origin.alt_m),
        },
        "wgs84": {
            "a": float(WGS84_A),
            "f": float(WGS84_F),
        },
        "notes": "ENU origin corresponds to first GPX trackpoint",
    }

    origin_out_path.parent.mkdir(parents=True, exist_ok=True)
    origin_out_path.write_text(json.dumps(origin_meta, indent=2), encoding="utf-8")
    print(f"✅ Wrote ENU origin metadata to {origin_out_path}")


if __name__ == "__main__":
    main()
