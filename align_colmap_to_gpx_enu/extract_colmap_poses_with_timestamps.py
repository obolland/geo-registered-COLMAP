#!/usr/bin/env python3
"""
Extract COLMAP camera positions and match them with image EXIF timestamps.

Usage:
  ./venv/bin/python extract_colmap_poses_with_timestamps.py \
      --colmap keyframes/colmap/sparse/0 \
      --images keyframes/images \
      --output colmap_camera_poses.json
"""

import argparse
import json
from pathlib import Path
from datetime import datetime
import numpy as np
from PIL import Image
from PIL.ExifTags import TAGS
import struct

# -------------------------------
# COLMAP binary reader
# -------------------------------

def read_next_bytes(fid, num_bytes, format_char_sequence, endian_character="<"):
    data = fid.read(num_bytes)
    return struct.unpack(endian_character + format_char_sequence, data)

def read_images_binary(path):
    images = {}

    with open(path, "rb") as fid:
        num_images = read_next_bytes(fid, 8, "Q")[0]

        for _ in range(num_images):
            image_id = read_next_bytes(fid, 4, "I")[0]
            qvec = np.array(read_next_bytes(fid, 8 * 4, "dddd"))
            tvec = np.array(read_next_bytes(fid, 8 * 3, "ddd"))
            camera_id = read_next_bytes(fid, 4, "I")[0]

            name = b""
            while True:
                c = fid.read(1)
                if c == b"\x00":
                    break
                name += c
            name = name.decode("utf-8")

            num_points2D = read_next_bytes(fid, 8, "Q")[0]
            fid.read(num_points2D * 24)

            images[image_id] = {
                "name": name,
                "qvec": qvec,
                "tvec": tvec,
                "camera_id": camera_id,
            }

    return images

# -------------------------------
# Quaternion → rotation
# -------------------------------

def qvec2rotmat(qvec):
    q0, q1, q2, q3 = qvec
    return np.array([
        [1 - 2*q2*q2 - 2*q3*q3, 2*q1*q2 - 2*q0*q3, 2*q1*q3 + 2*q0*q2],
        [2*q1*q2 + 2*q0*q3, 1 - 2*q1*q1 - 2*q3*q3, 2*q2*q3 - 2*q0*q1],
        [2*q1*q3 - 2*q0*q2, 2*q2*q3 + 2*q0*q1, 1 - 2*q1*q1 - 2*q2*q2]
    ])

# -------------------------------
# EXIF timestamp reader
# -------------------------------

def get_image_timestamp(image_path: Path) -> float:
    img = Image.open(image_path)
    exif = img._getexif()

    if not exif:
        raise ValueError(f"No EXIF in {image_path.name}")

    exif_data = {TAGS.get(k, k): v for k, v in exif.items()}
    time_str = exif_data.get("DateTimeOriginal") or exif_data.get("DateTime")

    if not time_str:
        raise ValueError(f"No DateTimeOriginal in {image_path.name}")

    dt = datetime.strptime(time_str, "%Y:%m:%d %H:%M:%S")
    return dt.timestamp()

# -------------------------------
# Main
# -------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--colmap", required=True, type=Path)
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    images_bin = args.colmap / "images.bin"
    colmap_images = read_images_binary(images_bin)

    out = []

    for img in colmap_images.values():
        name = img["name"]
        qvec = img["qvec"]
        tvec = img["tvec"]

        R = qvec2rotmat(qvec)
        C = -R.T @ tvec  # camera center

        image_path = args.images / name
        timestamp = get_image_timestamp(image_path)

        out.append({
            "image": name,
            "timestamp": float(timestamp),
            "colmap": C.tolist()
        })

    out.sort(key=lambda x: x["timestamp"])

    with open(args.output, "w") as f:
        json.dump(out, f, indent=2)

    print(f"✅ Wrote COLMAP camera trajectory: {args.output}")

if __name__ == "__main__":
    main()
