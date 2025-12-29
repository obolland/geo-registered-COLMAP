# Metric ENU Splatfacto Dataset Pipeline

This pipeline produces a **metrically scaled, ENU-aligned COLMAP reconstruction** suitable for training **Splatfacto** *without post-hoc scaling artifacts*.

The final output is a COLMAP model in **true meters**, aligned to a **GPX track**, which **Splatfacto preserves during training**.

---

## Directory Assumptions

```text
dataset/
├── images/                         # Input images
├── gpx/track.gpx                   # Input GPX track
├── keyframes/
│   ├── colmap/
│   │   └── sparse/
│   │       └── 0/                  # Raw COLMAP output
│   └── world_alignment/            # Alignment metadata
```

---

## Step 0 — Prerequisites

* Images contain valid **EXIF timestamps**
* GPX timestamps **overlap image capture time**
* COLMAP is installed and available on `PATH`
* Nerfstudio virtual environment is active

---

## Step 1 — Run COLMAP Reconstruction (Raw, Arbitrary Scale)

Run COLMAP normally on the images.
**Do not attempt to scale or geo-register at this stage.**

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

## Step 2 — Convert GPX → ENU (Timestamped)

Convert the GPX track into **ENU coordinates** while preserving timestamps.

```bash
node gpx_to_enu_raw.js \
  dataset/gpx/track.gpx \
  dataset/keyframes/world_alignment/gpx_enu_raw.json
```

### Output

```json
[
  { "timestamp": 1731675763.0, "enu": [E, N, U] },
  ...
]
```

Coordinates are in **meters**, relative to the first GPX point.

---

## Step 3 — Extract COLMAP Camera Poses with Timestamps

Extract camera centers from COLMAP and pair them with image EXIF timestamps.

```bash
./venv/bin/python extract_colmap_poses_with_timestamps.py \
  --colmap dataset/keyframes/colmap/sparse/0 \
  --images dataset/images \
  --output dataset/keyframes/world_alignment/colmap_camera_poses.json
```

### Output

```json
[
  { "image": "IMG_1234.jpg", "timestamp": 1731675763.5, "colmap": [x, y, z] },
  ...
]
```

---

## Step 4 — Generate `ref_images.txt`

Create the reference file for COLMAP geo-registration.

This step:

* Interpolates GPX to each image timestamp
* Optionally smooths GPS noise
* Automatically estimates camera ↔ GPS time offset (Δt)

### Recommended First Run (Minimal Smoothing)

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

## Step 5 — Geo-Register COLMAP into ENU Meters

Use COLMAP’s built-in `model_aligner` to transform the reconstruction.

```bash
colmap model_aligner \
  --input_path dataset/keyframes/colmap/sparse/0 \
  --output_path dataset/keyframes/colmap/sparse/0_enu \
  --ref_images_path dataset/keyframes/world_alignment/ref_images.txt \
  --ref_is_gps 0 \
  --alignment_max_error 3
```

### Successful Output Looks Like

```text
=> Using N reference images
=> Alignment error: ~1 m
=> Alignment succeeded
```

### Result

```text
dataset/keyframes/colmap/sparse/0_enu/
├── cameras.bin
├── images.bin
└── points3D.bin
```

This model is now:

* **Metrically scaled**
* **ENU oriented**
* **Globally consistent**

---

## Step 6 — Train Splatfacto (Metric, ENU-Preserving)

### Swap Aligned Model into Place

```bash
mv dataset/keyframes/colmap/sparse/0 dataset/keyframes/colmap/sparse/0_orig
mv dataset/keyframes/colmap/sparse/0_enu dataset/keyframes/colmap/sparse/0
```

### Train with Pose Normalization Disabled

```bash
ns-train splatfacto colmap \
  --data dataset/keyframes \
  --orientation_method none \
  --center_method none \
  --auto_scale_poses False
```

---

## Final Result

* Splats are trained **directly in ENU meters**
* **No post-hoc scaling** required
* **No visual sparsity**
* Correct real-world distances and elevations
* Tiles align consistently
* Ready for **VR / game engine** use

---

## Key Rules (Do Not Skip)

❌ **Do not scale splats after training**
❌ **Do not allow Splatfacto to auto-scale or re-center**

✅ **Always align COLMAP before training**
✅ **Use interpolation + Δt calibration for GPS**
