#!/usr/bin/env python3
"""
Apply a COLMAP → ENU similarity transform (world_transform.json)
to a PLY point cloud.

Usage:
  python apply_world_transform_to_ply.py \
      --input input.ply \
      --transform world_transform.json \
      --output output_enu.ply
"""

import argparse
import json
import numpy as np
from pathlib import Path

def load_ply_xyz_rgb(path):
    with open(path, "rb") as f:
        lines = []
        while True:
            line = f.readline()
            lines.append(line)
            if line.strip() == b"end_header":
                break

        header = b"".join(lines).decode("ascii")
        num_verts = 0
        for h in header.splitlines():
            if h.startswith("element vertex"):
                num_verts = int(h.split()[-1])

        data = np.fromfile(f, dtype=np.float32).reshape(num_verts, -1)

    xyz = data[:, :3]
    rest = data[:, 3:]
    return header, xyz, rest

def save_ply_xyz_rgb(path, header, xyz, rest):
    data = np.hstack([xyz, rest]).astype(np.float32)
    with open(path, "wb") as f:
        f.write(header.encode("ascii"))
        data.tofile(f)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--transform", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    # Load transform
    T = json.load(open(args.transform))
    s = T["scale"]
    R = np.array(T["rotation"])
    t = np.array(T["translation"])

    print("✅ Loaded transform:")
    print("Scale:", s)
    print("Rotation:\n", R)
    print("Translation:", t)

    # Load PLY
    header, xyz, rest = load_ply_xyz_rgb(args.input)
    print(f"✅ Loaded {len(xyz)} points")

    # Apply similarity transform
    xyz_enu = (s * (R @ xyz.T)).T + t

    # Save ENU-aligned PLY
    save_ply_xyz_rgb(args.output, header, xyz_enu, rest)
    print(f"✅ Wrote ENU-aligned PLY to: {args.output}")

if __name__ == "__main__":
    main()

