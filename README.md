# Metric ENU Splatfacto Dataset Pipeline

This pipeline produces a **metrically scaled, ENU-aligned COLMAP reconstruction** suitable for training **Splatfacto** *without post-hoc scaling artifacts*.

The final output is:

- A COLMAP model in **true meters**, aligned to a **GPX track**
- A Gaussian splat representation that **preserves metric scale**
- A **camera-centers path** that acts as ground truth for:
  - rider motion
  - distance
  - gradient
  - progress
- A **tile manifest** suitable for real-time engines (WebXR / VR / game engines)

This pipeline is designed to scale cleanly from a **single proof-of-concept tile** to **multi-tile routes** without architectural changes.

---

## Directory Assumptions

```text
dataset/
├── images/                         # Input images (with EXIF timestamps)
├── gpx/
│   └── track.gpx                   # Input GPX track (timestamped)
├── keyframes/
│   ├── colmap/
│   │   └── sparse/
│   │       └── 0/                  # COLMAP reconstruction (swapped to ENU later)
│   └── world_alignment/            # Alignment + runtime metadata
```

---

## Step 0 — Prerequisites

- Images contain valid **EXIF timestamps**
- GPX timestamps **overlap image capture time**
- COLMAP installed and available on `PATH`

---

## Step 1 — Run COLMAP Reconstruction (Raw, Arbitrary Scale)

Run COLMAP normally on the images.

> ❌ Do **not** attempt to scale, orient, or geo-register here.

```bash
./run_colmap_reconstruction.sh dataset/images
```

### Output

```text
dataset/keyframes/colmap/sparse/0/
├── cameras.bin
├── images.bin
└── points3D.bin
```

---

## Step 2 — Convert GPX → ENU (Raw, Timestamp-Preserved) + ENU Origin Metadata

Convert the GPX track into **ENU coordinates** while preserving timestamps.

The **ENU origin is the first GPX trackpoint**.  
This origin is a **coordinate reference only**, not the start of the ride.

```bash
./venv/bin/python gpx_to_enu_raw.py \
  --input dataset/gpx/track.gpx \
  --output dataset/keyframes/world_alignment/gpx_enu_raw.json \
  --output_origin dataset/keyframes/world_alignment/enu_origin.json
```

### Outputs

#### `gpx_enu_raw.json`

```json
[
  { "timestamp": 1731675763.0, "enu": [E, N, U] },
  ...
]
```

Coordinates are **ENU meters**, relative to the GPX origin.

#### `enu_origin.json`

```json
{
  "frame": "ENU",
  "origin": {
    "lat_deg": 51.507412,
    "lon_deg": -0.127823,
    "alt_m": 34.2
  },
  "wgs84": {
    "a": 6378137.0,
    "f": 0.0033528106647474805
  },
  "notes": "ENU origin corresponds to first GPX trackpoint"
}
```

This metadata is not required for rendering, but is critical for:
- future **Strava / GPX export**
- map overlays
- debugging and sanity checks across tiles

---

## Step 3 — Extract COLMAP Camera Poses with Timestamps

Extract camera centers from COLMAP and pair them with image EXIF timestamps.

```bash
./venv/bin/python extract_colmap_poses_with_timestamps.py \
  --colmap dataset/keyframes/colmap/sparse/0 \
  --images dataset/images \
  --camera_tz Europe/London \
  --output dataset/keyframes/world_alignment/colmap_camera_poses.json
```

### Output

```json
[
  { "image": "IMG_1234.jpg", "timestamp": 1731675763.5, "colmap": [x, y, z] },
  ...
]
```

Camera centers are still in **arbitrary COLMAP space** at this point.

---

## Step 4 — Generate `ref_images.txt` (COLMAP ↔ GPS Alignment)

Create the reference file for COLMAP geo-registration.

This step:
- interpolates GPX ENU positions to each image timestamp
- optionally smooths GPS noise
- automatically estimates **Δt** (camera ↔ GPS clock offset)

```bash
./venv/bin/python generate_ref_images_txt.py \
  --colmap_camera_poses dataset/keyframes/world_alignment/colmap_camera_poses.json \
  --gpx_enu dataset/keyframes/world_alignment/gpx_enu_raw.json \
  --output dataset/keyframes/world_alignment/ref_images.txt \
  --smooth poly \
  --smooth_window_s 3 \
  --calibrate_dt \
  --print_report
```

### Output

```text
IMG_1234.jpg  E  N  U
IMG_1235.jpg  E  N  U
...
```

Coordinates are **ENU meters**.

---

## Step 5 — Geo-Register COLMAP into Metric ENU Space

Use COLMAP’s `model_aligner` to transform the reconstruction.

```bash
colmap model_aligner \
  --input_path dataset/keyframes/colmap/sparse/0 \
  --output_path dataset/keyframes/colmap/sparse/0_enu \
  --ref_images_path dataset/keyframes/world_alignment/ref_images.txt \
  --ref_is_gps 0 \
  --alignment_type custom \
  --alignment_max_error 3
```

### Result

```text
dataset/keyframes/colmap/sparse/0_enu/
├── cameras.bin
├── images.bin
└── points3D.bin
```

The model is now:
- **metrically scaled**
- **ENU oriented**
- **globally consistent**

---

## Step 6 — Generate `path_cam.json` (Ground Truth Motion)

Generate a **camera-centers path** from the aligned COLMAP model.

This becomes the **single source of truth** for:
- rider position
- distance traveled
- gradient
- progress %

```bash
./venv/bin/python generate_path_cam_from_aligned_colmap.py \
  --colmap_model dataset/keyframes/colmap/sparse/0_enu \
  --images_dir dataset/images \
  --output dataset/keyframes/world_alignment/path_cam.json \
  --resample_m 0.5 \
  --smooth_window_m 5 \
  --grade_window_m 10 \
  --drop_teleports_m 5 \
  --print_report
```

### Output

```json
{
  "frame": "ENU",
  "units": "meters",
  "source": "colmap_aligned_camera_centers",
  "resample_m": 0.5,
  "smooth_window_m": 5,
  "grade_window_m": 10,
  "points": [
    { "s": 0.0, "e": ..., "n": ..., "u": ..., "grade_pct": ... },
    ...
  ]
}
```

Notes:
- `s` = cumulative distance along the route
- `grade_pct` = windowed Δu / Δs (stable)
- Route start = **first camera**, not first GPX point

---

## Step 7 — Generate `tile_manifest.json`

Create a runtime manifest describing one or more tiles.

For the POC, this will usually be a single tile.

```bash
./venv/bin/python generate_tile_manifest.py \
  --path_cam dataset/keyframes/world_alignment/path_cam.json \
  --output dataset/keyframes/world_alignment/tile_manifest.json \
  --tile_id tile_000 \
  --splat tile_000/tile.sog \
  --path tile_000/path_cam.json \
  --enu_origin_ref world_alignment/enu_origin.json \
  --yaw_rad 0.0 \
  --print_report
```

### Output

```json
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
```

---

## Step 8 — Train Splatfacto (Metric-Preserving)

### Swap aligned model into place (so Nerfstudio reads ENU cameras)

```bash
mv dataset/keyframes/colmap/sparse/0 dataset/keyframes/colmap/sparse/0_orig
mv dataset/keyframes/colmap/sparse/0_enu dataset/keyframes/colmap/sparse/0
```

### Train with pose normalisation disabled

```bash
ns-train splatfacto colmap   --data dataset/keyframes   --orientation_method none   --center_method none   --auto_scale_poses False
```

---

## Step 9 — Convert .PLY output to .SOG using splatTransform CLI tool (Compresses the point cloud, reduces file size dramatically)

```bash
splatTransform \
  --input dataset/keyframes/colmap/sparse/0_enu/points3D.ply \
  --output dataset/keyframes/colmap/sparse/0_enu/points3D.sog
```

---

## Final Runtime Inputs

Your app runtime typically consumes:

- `tile_manifest.json`
- each tile’s `tile.sog`
- each tile’s `path_cam.json`
- (optional) `enu_origin.json` for map/export features

---

## Key Rules (Do Not Skip)

❌ **Do not scale splats after training**  
❌ **Do not allow Splatfacto to auto-scale or re-center**  

✅ **Always align COLMAP before training**  
✅ **Use interpolation + Δt calibration for GPS**  
✅ **Use `path_cam.json` (aligned camera centers) as in-engine ground truth for motion + grade**
