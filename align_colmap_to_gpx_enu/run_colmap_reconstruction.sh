#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./run_colmap_reconstruction.sh /path/to/images
#
# Resulting structure:
#   /path/to/
#     images/
#     database.db
#     colmap/sparse/
#       0/
#         cameras.bin
#         images.bin
#         points3D.bin
#
# This approximates the COLMAP pipeline that ns-process-data (images) would run.

if [ $# -lt 1 ]; then
  echo "Usage: $0 /path/to/images"
  exit 1
fi

IMAGES_DIR="$1"

if [ ! -d "$IMAGES_DIR" ]; then
  echo "ERROR: '$IMAGES_DIR' is not a directory"
  exit 1
fi

# Resolve absolute paths
IMAGES_DIR="$(cd "$IMAGES_DIR" && pwd)"
DATASET_ROOT="$(dirname "$IMAGES_DIR")"

DB_PATH="$DATASET_ROOT/database.db"
# SPARSE_ROOT="$DATASET_ROOT/sparse"
SPARSE_ROOT="$DATASET_ROOT/colmap/sparse"
SPARSE_MODEL="$SPARSE_ROOT/0"


echo "=== Nerfstudio-style COLMAP pipeline ==="
echo "Images directory : $IMAGES_DIR"
echo "Dataset root     : $DATASET_ROOT"
echo "Database path    : $DB_PATH"
echo "Sparse model dir : $SPARSE_MODEL"
echo

# Basic sanity check
if ! command -v colmap >/dev/null 2>&1; then
  echo "ERROR: 'colmap' not found in PATH. Install COLMAP first."
  exit 1
fi

mkdir -p "$SPARSE_ROOT"

########################################
# 1) Feature extraction
########################################
echo "=== Step 1: feature_extractor ==="
# Mac builds typically don't have CUDA; use_gpu=0 is safe & portable.
# Skipping single camera mode for now - can degrade accuracy if camera intrinsics are not consistent between images
# use --ImageReader.single_camera 1 if you know your camera intrinsics are consistent between images
colmap feature_extractor \
  --database_path "$DB_PATH" \
  --image_path "$IMAGES_DIR" \
  --ImageReader.single_camera 0 \
  --ImageReader.camera_model OPENCV \
  --FeatureExtraction.use_gpu 0
echo

########################################
# 2) Matching (vocab_tree matcher, FAISS auto-tree)
########################################
echo "=== Step 2: vocab_tree_matcher ==="
# No VocabTreeMatching.vocab_tree_path -> COLMAP will download a FAISS vocab tree to its cache
colmap vocab_tree_matcher \
  --database_path "$DB_PATH" \
  --FeatureMatching.use_gpu 0
echo

########################################
# 3) Sparse reconstruction (mapper)
########################################
echo "=== Step 3: mapper ==="
colmap mapper \
  --database_path "$DB_PATH" \
  --image_path "$IMAGES_DIR" \
  --output_path "$SPARSE_ROOT" \
  --Mapper.ba_global_function_tolerance=1e-6
echo

########################################
# 4) Global bundle adjust (refine principal point)
########################################
# Skipping bundle adjustment for now - can introduce inconsistency tile-to-tile

# echo "=== Step 4: bundle_adjuster (refine principal point) ==="
# colmap bundle_adjuster \
#   --input_path "$SPARSE_MODEL" \
#   --output_path "$SPARSE_MODEL" \
#   --BundleAdjustment.refine_principal_point 1
# echo

echo "=== Done! ==="
echo "COLMAP model written to: $SPARSE_MODEL"
echo "You can now use this model with Nerfstudio's ColmapDataParser, e.g.:"
echo "  ns-train nerfacto --data \"$DATASET_ROOT\" --data.parser colmap"
