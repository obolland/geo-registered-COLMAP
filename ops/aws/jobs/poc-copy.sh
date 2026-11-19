#!/usr/bin/env bash
# Copies the proof-of-concept capture and training records from the old instance to S3, read-only on the
# instance, so the Polycam capture's density can be compared with the rig's. Runs as root under SSM.
set -euo pipefail

BUCKET=georoute-splat-data-086341361260
HOME_DIR=/home/ubuntu
DEST=s3://$BUCKET/poc-old

echo "=== inventory ($(date -u +%H:%M:%S))"
find "$HOME_DIR/keyframes" -maxdepth 2 | head -n 20
find "$HOME_DIR/keyframes" -type f | sed 's/.*\.//' | sort | uniq -c
find "$HOME_DIR/outputs" "$HOME_DIR/exports" -maxdepth 4 | head -n 40

echo "=== copy keyframes and COLMAP model ($(date -u +%H:%M:%S))"
aws s3 sync "$HOME_DIR/keyframes" "$DEST/keyframes" --only-show-errors
echo "=== copy training configs and export metadata, not the large outputs ($(date -u +%H:%M:%S))"
aws s3 sync "$HOME_DIR/outputs" "$DEST/outputs" --only-show-errors --exclude "*" \
    --include "*.yml" --include "*.yaml" --include "*.json" --include "*.txt"
aws s3 sync "$HOME_DIR/exports" "$DEST/exports" --only-show-errors --exclude "*.ply"
ls -la "$HOME_DIR/exports/splat"
echo "=== shell history lines mentioning the pipeline"
grep -a -E "ns-train|ns-export|colmap|polycam|splatfacto|ns-process" "$HOME_DIR/.bash_history" | tail -n 40 || true
echo "copy finished $(date -u +%FT%TZ)"
