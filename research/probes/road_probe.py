from pathlib import Path

import numpy as np

from georoute.reconstruct import _model_poses
from georoute.rig import load_rig

ws = Path.home() / "fieldtest2/colmap/s1-gsample"
rig = load_rig("rigs/handlebar-4cam-ft2-calibrated.toml")
which, poses, _, _, model = _model_poses(ws)
ref = rig.reference_camera
origins = {}
for (p, c), (r_wc, centre) in poses.items():
    r_wr = r_wc @ rig.rig_from_camera(c).T
    origins.setdefault(p, []).append((centre - r_wr @ rig.camera(c).position_in_rig_m, r_wr))
pulses = sorted(origins)
o = np.array([np.median([x[0] for x in origins[p]], axis=0) for p in pulses])
spread = np.array(
    [
        np.max(np.linalg.norm(np.array([x[0] for x in origins[p]]) - o[i], axis=1))
        for i, p in enumerate(pulses)
    ]
)
up_tilt = np.array([np.degrees(np.arccos(origins[p][0][1][2, 2])) for p in pulses])
pts = np.array([pt.xyz for pt in model.points3D.values()])
err = np.array([pt.error for pt in model.points3D.values()])
print(
    which,
    "pulses",
    len(pulses),
    "points",
    len(pts),
    "rig-origin spread across cams p50/p95 m",
    np.percentile(spread, [50, 95]).round(4),
)
print("rig z-axis tilt from up p50/p95 deg", np.percentile(up_tilt, [50, 95]).round(2))
h = rig.reference_height_above_road_m
counts, offs = [], []
for x in o:
    d = np.hypot(*(pts[:, :2] - x[:2]).T)
    for rad in (0.5, 1.0, 2.0):
        sel = (d < rad) & (np.abs(pts[:, 2] - (x[2] - h)) < 0.3)
        counts.append((rad, sel.sum()))
    sel = (d < 1.0) & (np.abs(pts[:, 2] - (x[2] - h)) < 0.3)
    if sel.sum() >= 5:
        offs.append(x[2] - np.median(pts[sel, 2]))
counts = np.array(counts).reshape(len(o), 3, 2)[:, :, 1]
for k, rad in enumerate((0.5, 1.0, 2.0)):
    print(
        f"r={rad} m points p5/p50 {np.percentile(counts[:, k], [5, 50])}, "
        f"pulses with <5: {(counts[:, k] < 5).sum()}"
    )
offs = np.array(offs)
print(
    "origin height above road points (1 m disc) p5/p50/p95",
    np.percentile(offs, [5, 50, 95]).round(3),
    "nominal",
    h,
)
