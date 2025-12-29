#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# WORLD ALIGNMENT PIPELINE
# ============================================================
#
# This script performs the full pipeline:
#
#   1) GPX   → ENU (timestamped world coordinates)
#   2) Images → COLMAP sparse reconstruction
#   3) COLMAP → Timestamped camera trajectory (via EXIF)
#   4) Umeyama alignment to ENU + in-place COLMAP rewrite
#
# After this runs successfully:
#   - keyframes/sparse/0/images.bin  → world-space camera poses
#   - keyframes/sparse/0/points3D.bin → world-space geometry
#
# These are then ready for:
#   ns-train splatfacto colmap --data keyframes \
#     --orientation_method none \
#     --center_method none \
#     --auto_scale_poses False
#
# ------------------------------------------------------------
# USAGE:
#
#   ./run_world_alignment.sh <gpx_file> <keyframes_dir> [output_dir]
#
# ------------------------------------------------------------
# EXAMPLES:
#
# 1) Use default output directory:
#
#   ./run_world_alignment.sh data/ride1/track.gpx data/ride1/keyframes
#
#   → Writes outputs to:
#     data/ride1/keyframes/world_alignment/
#
# 2) Specify custom output directory:
#
#   ./run_world_alignment.sh \
#       data/ride1/track.gpx \
#       data/ride1/keyframes \
#       outputs/ride1
#
#   → Writes outputs to:
#     outputs/ride1/
#
# ------------------------------------------------------------
# OUTPUT FILES (diagnostic / alignment only):
#
#   gpx_enu_raw.json
#   colmap_camera_poses.json
#
# COLMAP FILES MODIFIED IN PLACE:
#
#   <keyframes_dir>/sparse/0/images.bin
#   <keyframes_dir>/sparse/0/points3D.bin
#
# ============================================================

# -------------------------------
# ARGUMENT HANDLING
# -------------------------------

if [ $# -lt 2 ]; then
  echo "Usage:"
  echo "  $0 <gpx_file> <keyframes_dir> [output_dir]"
  exit 1
fi

# Resolve absolute paths safely
GPX_PATH="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
KEYFRAMES_DIR="$(cd "$2" && pwd)"

# Optional output directory
if [ $# -ge 3 ]; then
  OUT_DIR="$(cd "$(dirname "$3")" && pwd)/$(basename "$3")"
else
  OUT_DIR="$KEYFRAMES_DIR/world_alignment"
fi

IMAGES_DIR="$KEYFRAMES_DIR/images"
# COLMAP_SPARSE_DIR="$KEYFRAMES_DIR/sparse/0"
COLMAP_SPARSE_DIR="$KEYFRAMES_DIR/colmap/sparse/0"


GPX_ENU_JSON="$OUT_DIR/gpx_enu_raw.json"
COLMAP_TRAJ_JSON="$OUT_DIR/colmap_camera_poses.json"

mkdir -p "$OUT_DIR"

# -------------------------------
# SANITY CHECKS
# -------------------------------

if [ ! -f "$GPX_PATH" ]; then
  echo "❌ GPX file not found: $GPX_PATH"
  exit 1
fi

if [ ! -d "$KEYFRAMES_DIR" ]; then
  echo "❌ Keyframes directory not found: $KEYFRAMES_DIR"
  exit 1
fi

if [ ! -d "$IMAGES_DIR" ]; then
  echo "❌ Images directory not found: $IMAGES_DIR"
  exit 1
fi

if ! command -v colmap >/dev/null 2>&1; then
  echo "❌ COLMAP not found in PATH"
  exit 1
fi

# -------------------------------
# HEADER
# -------------------------------

echo "========================================"
echo "▶ WORLD ALIGNMENT PIPELINE"
echo "----------------------------------------"
echo "GPX file        : $GPX_PATH"
echo "Keyframes dir   : $KEYFRAMES_DIR"
echo "Images dir      : $IMAGES_DIR"
echo "Sparse COLMAP   : $COLMAP_SPARSE_DIR"
echo "Output dir      : $OUT_DIR"
echo "========================================"
echo

# -------------------------------
# 1) GPX → ENU
# -------------------------------

echo "▶ [1/4] GPX → ENU"
node gpx_to_enu_raw.js "$GPX_PATH" "$GPX_ENU_JSON"
echo

# -------------------------------
# 2) COLMAP RECONSTRUCTION
# -------------------------------

echo "▶ [2/4] Running COLMAP"
./run_colmap_reconstruction.sh "$IMAGES_DIR"
echo

# -------------------------------
# 3) EXTRACT CAMERA TRAJECTORY
# -------------------------------

echo "▶ [3/4] Extracting COLMAP camera poses"
./venv/bin/python extract_colmap_poses_with_timestamps.py \
  --colmap "$COLMAP_SPARSE_DIR" \
  --images "$IMAGES_DIR" \
  --output "$COLMAP_TRAJ_JSON"
echo

# -------------------------------
# 4) UMEYAMA ALIGNMENT + REWRITE
# -------------------------------

echo "▶ [4/4] Aligning COLMAP → ENU world"
./venv/bin/python align_colmap_to_gpx_enu.py \
  --colmap_sparse "$COLMAP_SPARSE_DIR" \
  --colmap_trajectory "$COLMAP_TRAJ_JSON" \
  --gpx_enu "$GPX_ENU_JSON" \
  --apply_points
echo

# -------------------------------
# DONE
# -------------------------------

echo "✅ World alignment pipeline complete"
echo "----------------------------------------"
echo "Diagnostic outputs:"
echo "  $GPX_ENU_JSON"
echo "  $COLMAP_TRAJ_JSON"
echo
echo "World-space COLMAP written to:"
echo "  $COLMAP_SPARSE_DIR"
echo
