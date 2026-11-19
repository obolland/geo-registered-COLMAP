"""Convert a nerfstudio splatfacto checkpoint to a standard 3DGS PLY in the dataset's original frame.

ns-export gaussian-splat (nerfstudio 1.1.5) writes the Gaussians in the dataparser's normalised frame.
dataparser_transforms.json stores the transform T (original -> normalised) and uniform scale s:
    p_normalised = s * (T p_original)
so means and quaternions go back with inv(T) and a 1/s scale:
    mean_original = inv(T) [mean_normalised / s, 1]
    log_scale_original = log_scale_normalised - log(s)
    q_original = q_inv(T) * q_normalised
The higher-order SH coefficients are left unrotated: rotating degree-3 SH needs the Wigner-D matrices,
and the transform rotation here is a few degrees. f_dc is view-independent and unchanged.

Reads parameters as saved by splatfacto (gauss_params.*), applies the same NaN and low-opacity filter as
ns-export (opacity logit < logit(1/255)) and writes x y z, f_dc_*, f_rest_*, opacity, scale_*, rot_*.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from plyfile import PlyData, PlyElement
from scipy.spatial.transform import Rotation


def load_params(ckpt_path):
    checkpoint = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    state = checkpoint.get("pipeline", checkpoint)
    return {
        key.split(".")[-1]: value.float().numpy()
        for key, value in state.items()
        if key.startswith("_model.gauss_params.")
    }


def quaternion_multiply(a, b):
    """Hamilton product for w,x,y,z quaternions, row-wise."""
    w1, x1, y1, z1 = a.T
    w2, x2, y2, z2 = b.T
    return np.stack(
        [
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        ],
        axis=1,
    )


def convert(params, transforms, opacity_min=-5.5373):
    transform = np.eye(4)
    transform[:3, :4] = np.asarray(transforms["transform"], float)
    scale = float(transforms["scale"])
    inverse = np.linalg.inv(transform)
    means = params["means"].astype(np.float64)
    homogeneous = np.concatenate([means / scale, np.ones((len(means), 1))], axis=1)
    means_out = (homogeneous @ inverse.T)[:, :3]
    scales_out = params["scales"].astype(np.float64) - np.log(scale)
    quats = params["quats"].astype(np.float64)
    quats /= np.linalg.norm(quats, axis=1, keepdims=True)
    normalised_to_original = Rotation.from_matrix(transform[:3, :3]).as_quat()
    inverse_quat = np.array(
        [
            normalised_to_original[3],
            -normalised_to_original[0],
            -normalised_to_original[1],
            -normalised_to_original[2],
        ]
    )
    quats_out = quaternion_multiply(np.broadcast_to(inverse_quat, quats.shape), quats)
    quats_out /= np.linalg.norm(quats_out, axis=1, keepdims=True)
    sh0 = params["features_dc"].astype(np.float64)
    shN = params["features_rest"].transpose(0, 2, 1).reshape(len(means), -1).astype(np.float64)
    opacity = params["opacities"].astype(np.float64).reshape(-1)
    finite = (
        np.isfinite(means_out).all(1)
        & np.isfinite(scales_out).all(1)
        & np.isfinite(quats_out).all(1)
        & np.isfinite(sh0).all(1)
        & np.isfinite(shN).all(1)
        & np.isfinite(opacity)
    )
    keep = finite & (opacity >= opacity_min)
    return means_out, scales_out, quats_out, sh0, shN, opacity, keep


def write_ply(path, means, scales, quats, sh0, shN, opacity, keep):
    columns = [("x", means[:, 0]), ("y", means[:, 1]), ("z", means[:, 2])]
    columns += [(f"f_dc_{i}", sh0[:, i]) for i in range(3)]
    columns += [(f"f_rest_{i}", shN[:, i]) for i in range(shN.shape[1])]
    columns.append(("opacity", opacity))
    columns += [(f"scale_{i}", scales[:, i]) for i in range(3)]
    columns += [(f"rot_{i}", quats[:, i]) for i in range(4)]
    data = np.zeros(int(keep.sum()), dtype=[(name, "f4") for name, _ in columns])
    for name, values in columns:
        data[name] = values[keep]
    PlyData([PlyElement.describe(data, "vertex")], text=False, byte_order="<").write(str(path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt", type=Path, required=True)
    parser.add_argument("--transforms", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    params = load_params(args.ckpt)
    transforms = json.loads(args.transforms.read_text())
    means, scales, quats, sh0, shN, opacity, keep = convert(params, transforms)
    write_ply(args.out, means, scales, quats, sh0, shN, opacity, keep)
    print(f"{len(keep)} splats, {int(keep.sum())} written to {args.out}")


if __name__ == "__main__":
    main()
