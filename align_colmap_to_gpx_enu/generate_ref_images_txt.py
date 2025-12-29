#!/usr/bin/env python3
"""
Generate COLMAP model_aligner ref_images.txt from:
  - colmap_camera_poses.json (image, timestamp, colmap camera center)
  - gpx_enu_raw.json (timestamp, enu)

Features:
  ✅ Linear interpolation of GPX ENU to each image timestamp
  ✅ Optional smoothing of GPX ENU (moving average OR local polynomial "Savitzky–Golay-like")
  ✅ Automatic Δt calibration (camera time offset) via Umeyama RMSE minimization
  ✅ Writes: ref_images.txt in the format:
        image_name  X  Y  Z
     where X Y Z are ENU meters (E N U)

Notes:
  - Timestamps are assumed to be Unix seconds (float ok) in both files.
  - This does NOT modify COLMAP; it only prepares inputs for `colmap model_aligner`.

Example minimal usage (no smoothing, Δt calibration only):
./venv/bin/python generate_ref_images_txt.py \
  --colmap_camera_poses colmap_camera_poses.json \
  --gpx_enu gpx_enu_raw.json \
  --output ref_images.txt \
  --calibrate_dt

Best practise example usage (smoothing, Δt calibration):
./venv/bin/python generate_ref_images_txt.py \
  --colmap_camera_poses colmap_camera_poses.json \
  --gpx_enu gpx_enu_raw.json \
  --output ref_images.txt \
  --smooth poly \
  --smooth_window_s 5 \
  --calibrate_dt \
  --print_report


--------------------------------
GPX Smoothing Options (Optional)
--------------------------------
Smoothing reduces GPS jitter before interpolation.

--smooth {none,ma,poly}

Smoothing method applied to the GPX track before interpolation.

none — no smoothing (default)

ma — moving average over time window

poly — local polynomial smoothing (Savitzky–Golay–like)

--smooth_window_s FLOAT

Time window (in seconds) used for smoothing.

Typical values: 3, 5, 9

0 disables smoothing (default)

Applies symmetrically around each GPX timestamp.

--smooth_degree INT

Polynomial degree for --smooth poly.

Default: 2

Only used when --smooth poly is selected

Higher degrees fit curvature better but may overfit noise.

Time Offset Calibration (Δt)

Camera and GPS clocks are often offset by a constant amount.
This section automatically estimates that offset.

--calibrate_dt

Enable automatic Δt calibration.

If set, the script searches for a global time offset that minimizes
Umeyama alignment RMSE between:

COLMAP camera centers

interpolated GPX ENU positions

Strongly recommended for best geo-registration.

--dt_min FLOAT

Minimum Δt (seconds) to search.

Default: -2.0

--dt_max FLOAT

Maximum Δt (seconds) to search.

Default: +2.0

--dt_step FLOAT

Step size (seconds) for Δt search.

Default: 0.02

Smaller steps improve accuracy but increase runtime.

--dt FLOAT

Fixed Δt (seconds) to apply if --calibrate_dt is NOT used.

Default: 0.0

Ignored when --calibrate_dt is enabled.

Output / Debug Options
--max_images INT

If > 0, only write the first N images (after sorting by timestamp).

Useful for:

debugging

quick tests

validating alignment on a subset

--print_report

Print a diagnostic alignment report:

estimated scale (COLMAP → ENU)

rotation matrix

translation vector

final RMSE (meters)

Does not affect output — informational only.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import numpy as np


# -------------------------
# Math: Umeyama alignment
# -------------------------

def umeyama_alignment(A: np.ndarray, B: np.ndarray) -> Tuple[float, np.ndarray, np.ndarray]:
    """
    Solve for s, R, t such that:
        B ~= s * R * A + t
    using Umeyama (no reflection).
    A, B: (N,3)
    """
    if A.shape != B.shape or A.shape[1] != 3:
        raise ValueError("A and B must be (N,3) with same shape")

    mean_A = A.mean(axis=0)
    mean_B = B.mean(axis=0)
    AA = A - mean_A
    BB = B - mean_B

    cov = (BB.T @ AA) / A.shape[0]
    U, S, Vt = np.linalg.svd(cov)
    R = U @ Vt
    if np.linalg.det(R) < 0:
        # Fix reflection
        U[:, -1] *= -1
        R = U @ Vt

    var_A = np.mean(np.sum(AA**2, axis=1))
    if var_A <= 0:
        raise ValueError("Degenerate A variance")
    s = np.sum(S) / var_A
    t = mean_B - s * (R @ mean_A)
    return float(s), R, t


def umeyama_rmse(A: np.ndarray, B: np.ndarray) -> float:
    """RMSE after Umeyama alignment of A->B."""
    s, R, t = umeyama_alignment(A, B)
    A2 = (s * (A @ R.T)) + t  # (N,3); note: R @ a == a @ R.T
    err = A2 - B
    return float(np.sqrt(np.mean(np.sum(err * err, axis=1))))


# -------------------------
# GPX interpolation
# -------------------------

@dataclass
class GpxSeries:
    t: np.ndarray      # (M,)
    xyz: np.ndarray    # (M,3)

    def interpolate(self, tq: np.ndarray) -> np.ndarray:
        """
        Linear interpolation of xyz at query times tq.
        Clamps outside range to endpoints.
        """
        t = self.t
        x = self.xyz

        tq = np.asarray(tq, dtype=float)
        out = np.empty((tq.shape[0], 3), dtype=float)

        # Clamp
        tq_clamped = np.clip(tq, t[0], t[-1])

        idx = np.searchsorted(t, tq_clamped, side="left")
        idx = np.clip(idx, 1, len(t) - 1)

        t0 = t[idx - 1]
        t1 = t[idx]
        p0 = x[idx - 1]
        p1 = x[idx]

        denom = (t1 - t0)
        # Avoid division by zero if duplicate timestamps exist
        denom = np.where(denom == 0, 1.0, denom)
        a = ((tq_clamped - t0) / denom)[:, None]

        out[:] = (1.0 - a) * p0 + a * p1
        return out


# -------------------------
# Smoothing
# -------------------------

def smooth_moving_average(t: np.ndarray, xyz: np.ndarray, window_seconds: float) -> np.ndarray:
    """
    Time-aware moving average using a symmetric time window.
    O(N^2) worst case but fine for typical GPX sizes (tens of thousands).
    """
    if window_seconds <= 0:
        return xyz

    half = window_seconds / 2.0
    out = np.empty_like(xyz, dtype=float)

    for i in range(len(t)):
        lo = t[i] - half
        hi = t[i] + half
        j0 = np.searchsorted(t, lo, side="left")
        j1 = np.searchsorted(t, hi, side="right")
        out[i] = xyz[j0:j1].mean(axis=0)
    return out


def smooth_local_poly(t: np.ndarray, xyz: np.ndarray, window_seconds: float, degree: int = 2) -> np.ndarray:
    """
    Savitzky–Golay-like smoothing:
    For each sample i, fit a local polynomial of given degree to points in a time window,
    then evaluate at t[i]. Done independently for x/y/z.
    """
    if window_seconds <= 0:
        return xyz
    if degree < 1:
        raise ValueError("degree must be >= 1")

    half = window_seconds / 2.0
    out = np.empty_like(xyz, dtype=float)

    for i in range(len(t)):
        lo = t[i] - half
        hi = t[i] + half
        j0 = np.searchsorted(t, lo, side="left")
        j1 = np.searchsorted(t, hi, side="right")
        tt = t[j0:j1] - t[i]  # center time at 0 for conditioning
        yy = xyz[j0:j1]

        if len(tt) <= degree:
            out[i] = yy.mean(axis=0)
            continue

        # Vandermonde for polynomial fit: [1, tt, tt^2, ...]
        V = np.vander(tt, N=degree + 1, increasing=True)  # (K, degree+1)

        # Solve least squares for each coordinate
        # coeffs shape (degree+1, 3)
        coeffs, *_ = np.linalg.lstsq(V, yy, rcond=None)

        # Evaluate at 0 -> simply coeffs[0]
        out[i] = coeffs[0]
    return out


def apply_smoothing(t: np.ndarray, xyz: np.ndarray, method: str, window_seconds: float, degree: int) -> np.ndarray:
    method = method.lower()
    if method in ("none", ""):
        return xyz
    if window_seconds <= 0:
        return xyz
    if method in ("ma", "moving_average", "moving-average"):
        return smooth_moving_average(t, xyz, window_seconds)
    if method in ("poly", "sg", "savgol", "savitzky-golay"):
        return smooth_local_poly(t, xyz, window_seconds, degree=degree)
    raise ValueError(f"Unknown smoothing method: {method}")


# -------------------------
# IO helpers
# -------------------------

def load_colmap_poses(path: Path):
    data = json.loads(path.read_text())
    # Expect list of dicts with keys: image, timestamp, colmap
    images = [d["image"] for d in data]
    t = np.array([float(d["timestamp"]) for d in data], dtype=float)
    col = np.array([d["colmap"] for d in data], dtype=float)
    if col.shape[1] != 3:
        raise ValueError("colmap vector must be length 3")
    return images, t, col


def load_gpx_enu(path: Path) -> GpxSeries:
    data = json.loads(path.read_text())
    t = np.array([float(d["timestamp"]) for d in data], dtype=float)
    xyz = np.array([d["enu"] for d in data], dtype=float)
    if xyz.shape[1] != 3:
        raise ValueError("enu must be length 3")
    # Ensure sorted by time
    order = np.argsort(t)
    t = t[order]
    xyz = xyz[order]
    return GpxSeries(t=t, xyz=xyz)


# -------------------------
# Δt calibration
# -------------------------

def calibrate_dt(
    img_t: np.ndarray,
    colmap_centers: np.ndarray,
    gpx: GpxSeries,
    dt_min: float,
    dt_max: float,
    dt_step: float,
) -> Tuple[float, float]:
    """
    Find dt that minimizes Umeyama RMSE between:
      A = colmap_centers
      B = gpx.interpolate(img_t + dt)

    Returns (best_dt, best_rmse).
    """
    if dt_step <= 0:
        raise ValueError("dt_step must be > 0")
    dts = np.arange(dt_min, dt_max + 0.5 * dt_step, dt_step, dtype=float)

    best_dt = float(dts[0])
    best_rmse = math.inf

    # Optional: ignore images far outside GPX range by clamping already handled in interpolate.
    for dt in dts:
        B = gpx.interpolate(img_t + dt)
        rmse = umeyama_rmse(colmap_centers, B)
        if rmse < best_rmse:
            best_rmse = rmse
            best_dt = float(dt)

    return best_dt, float(best_rmse)


# -------------------------
# Main
# -------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--colmap_camera_poses", required=True, type=Path, help="Path to colmap_camera_poses.json")
    ap.add_argument("--gpx_enu", required=True, type=Path, help="Path to gpx_enu_raw.json")
    ap.add_argument("--output", required=True, type=Path, help="Path to write ref_images.txt")

    # Smoothing
    ap.add_argument("--smooth", default="none", choices=["none", "ma", "poly"],
                    help="Smoothing method for GPX ENU: none, ma (moving average), poly (local polynomial)")
    ap.add_argument("--smooth_window_s", type=float, default=0.0,
                    help="Smoothing time window in seconds (e.g. 3, 5, 9). 0 disables.")
    ap.add_argument("--smooth_degree", type=int, default=2,
                    help="Degree for --smooth poly (default 2).")

    # Δt calibration
    ap.add_argument("--calibrate_dt", action="store_true",
                    help="If set, search for a global Δt that minimizes Umeyama RMSE.")
    ap.add_argument("--dt_min", type=float, default=-2.0, help="Min Δt seconds (default -2.0)")
    ap.add_argument("--dt_max", type=float, default= 2.0, help="Max Δt seconds (default  2.0)")
    ap.add_argument("--dt_step", type=float, default=0.02, help="Δt step seconds (default 0.02)")
    ap.add_argument("--dt", type=float, default=0.0,
                    help="If not calibrating, use this fixed Δt (seconds). Default 0.")

    # Output controls
    ap.add_argument("--max_images", type=int, default=0,
                    help="If >0, only write the first N images (after sorting by timestamp).")
    ap.add_argument("--print_report", action="store_true", help="Print alignment report (scale/rot/trans).")

    args = ap.parse_args()

    # Load inputs
    img_names, img_t, col_centers = load_colmap_poses(args.colmap_camera_poses)
    gpx = load_gpx_enu(args.gpx_enu)

    # Sort images by time (useful + stable)
    order = np.argsort(img_t)
    img_t = img_t[order]
    col_centers = col_centers[order]
    img_names = [img_names[i] for i in order]

    if args.max_images and args.max_images > 0:
        img_t = img_t[:args.max_images]
        col_centers = col_centers[:args.max_images]
        img_names = img_names[:args.max_images]

    # Smooth GPX (optional) BEFORE interpolation
    smoothed_xyz = apply_smoothing(gpx.t, gpx.xyz, args.smooth, args.smooth_window_s, args.smooth_degree)
    gpx_sm = GpxSeries(t=gpx.t, xyz=smoothed_xyz)

    # Δt calibration (optional)
    if args.calibrate_dt:
        best_dt, best_rmse = calibrate_dt(
            img_t=img_t,
            colmap_centers=col_centers,
            gpx=gpx_sm,
            dt_min=args.dt_min,
            dt_max=args.dt_max,
            dt_step=args.dt_step,
        )
        dt = best_dt
        print(f"✅ Best Δt = {dt:+.3f} s  (Umeyama RMSE = {best_rmse:.4f} m in ENU-space)")
    else:
        dt = float(args.dt)
        print(f"ℹ️ Using fixed Δt = {dt:+.3f} s (no calibration)")

    # Interpolate ENU camera centers for each image timestamp
    enu_cam = gpx_sm.interpolate(img_t + dt)

    # Optional report about the implied transform
    if args.print_report:
        s, R, t = umeyama_alignment(col_centers, enu_cam)
        rmse = umeyama_rmse(col_centers, enu_cam)
        print("\n🔎 Umeyama (COLMAP -> ENU) on calibrated correspondences:")
        print(f"Scale: {s}")
        print("Rotation:\n", R)
        print("Translation:", t)
        print(f"RMSE: {rmse:.4f} m\n")

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Write ref_images.txt
    # COLMAP expects: image_name X Y Z
    # (Where X,Y,Z are in the target frame you want to align to: here ENU meters)
    with args.output.open("w", encoding="utf-8") as f:
        for name, p in zip(img_names, enu_cam):
            f.write(f"{name} {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n")

    print(f"✅ Wrote ref_images.txt: {args.output}  (N={len(img_names)})")


if __name__ == "__main__":
    main()
