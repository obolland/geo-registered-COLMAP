# #!/usr/bin/env python3
# """
# Align COLMAP reconstruction to GPX ENU world coordinates using Umeyama
# and rewrite images.bin (and optionally points3D.bin) in-place.

# Usage:
#   python align_colmap_to_gpx_enu.py \
#     --colmap_sparse keyframes/colmap/sparse/0 \
#     --colmap_trajectory colmap_camera_poses.json \
#     --gpx_enu gpx_enu_raw.json \
#     --apply_points

# If --apply_points is omitted, only cameras are transformed.
# """

# import argparse
# import json
# import numpy as np
# from pathlib import Path

# # -------------------------------
# # Low-level COLMAP binary tools
# # -------------------------------

# import struct

# def read_next_bytes(fid, num_bytes, format_char_sequence, endian_character="<"):
#     data = fid.read(num_bytes)
#     return struct.unpack(endian_character + format_char_sequence, data)

# def write_next_bytes(fid, format_char_sequence, data, endian_character="<"):
#     fid.write(struct.pack(endian_character + format_char_sequence, *data))

# def read_images_binary(path):
#     images = {}
#     with open(path, "rb") as fid:
#         num_images = read_next_bytes(fid, 8, "Q")[0]

#         for _ in range(num_images):
#             image_id = read_next_bytes(fid, 4, "I")[0]
#             qvec = np.array(read_next_bytes(fid, 8 * 4, "dddd"))
#             tvec = np.array(read_next_bytes(fid, 8 * 3, "ddd"))
#             camera_id = read_next_bytes(fid, 4, "I")[0]

#             name = b""
#             while True:
#                 c = fid.read(1)
#                 if c == b"\x00":
#                     break
#                 name += c
#             name = name.decode("utf-8")

#             num_points2D = read_next_bytes(fid, 8, "Q")[0]
#             fid.read(num_points2D * 24)

#             images[image_id] = {
#                 "image_id": image_id,
#                 "qvec": qvec,
#                 "tvec": tvec,
#                 "camera_id": camera_id,
#                 "name": name,
#             }
#     return images

# def write_images_binary(path, images):
#     with open(path, "wb") as fid:
#         write_next_bytes(fid, "Q", [len(images)])
#         for img in images.values():
#             write_next_bytes(fid, "I", [img["image_id"]])
#             write_next_bytes(fid, "dddd", img["qvec"])
#             write_next_bytes(fid, "ddd", img["tvec"])
#             write_next_bytes(fid, "I", [img["camera_id"]])
#             fid.write(img["name"].encode("utf-8") + b"\x00")
#             write_next_bytes(fid, "Q", [0])

# def read_points3D_binary(path):
#     points = {}
#     with open(path, "rb") as fid:
#         num_points = read_next_bytes(fid, 8, "Q")[0]
#         for _ in range(num_points):
#             point_id = read_next_bytes(fid, 8, "Q")[0]
#             xyz = np.array(read_next_bytes(fid, 24, "ddd"))
#             rgb = np.array(read_next_bytes(fid, 3, "BBB"))
#             error = read_next_bytes(fid, 8, "d")[0]
#             track_len = read_next_bytes(fid, 8, "Q")[0]
#             fid.read(track_len * 8)
#             points[point_id] = {"xyz": xyz, "rgb": rgb, "error": error}
#     return points

# def write_points3D_binary(path, points):
#     with open(path, "wb") as fid:
#         write_next_bytes(fid, "Q", [len(points)])
#         for pid, p in points.items():
#             write_next_bytes(fid, "Q", [pid])
#             write_next_bytes(fid, "ddd", p["xyz"])
#             write_next_bytes(fid, "BBB", list(p["rgb"]))
#             write_next_bytes(fid, "d", [p["error"]])
#             write_next_bytes(fid, "Q", [0])

# # -------------------------------
# # Quaternion helpers
# # -------------------------------

# def qvec2rotmat(qvec):
#     q0, q1, q2, q3 = qvec
#     return np.array([
#         [1 - 2*q2*q2 - 2*q3*q3, 2*q1*q2 - 2*q0*q3, 2*q1*q3 + 2*q0*q2],
#         [2*q1*q2 + 2*q0*q3, 1 - 2*q1*q1 - 2*q3*q3, 2*q2*q3 - 2*q0*q1],
#         [2*q1*q3 - 2*q0*q2, 2*q2*q3 + 2*q0*q1, 1 - 2*q1*q1 - 2*q2*q2]
#     ])

# def rotmat2qvec(R):
#     K = np.array([
#         [R[0,0]-R[1,1]-R[2,2], 0, 0, 0],
#         [R[1,0]+R[0,1], R[1,1]-R[0,0]-R[2,2], 0, 0],
#         [R[2,0]+R[0,2], R[2,1]+R[1,2], R[2,2]-R[0,0]-R[1,1], 0],
#         [R[1,2]-R[2,1], R[2,0]-R[0,2], R[0,1]-R[1,0], R[0,0]+R[1,1]+R[2,2]]
#     ]) / 3.0
#     eigvals, eigvecs = np.linalg.eigh(K)
#     qvec = eigvecs[:, np.argmax(eigvals)]
#     if qvec[0] < 0:
#         qvec = -qvec
#     return qvec

# # -------------------------------
# # Umeyama alignment
# # -------------------------------

# def umeyama_alignment(A, B):
#     mean_A = A.mean(axis=0)
#     mean_B = B.mean(axis=0)
#     AA = A - mean_A
#     BB = B - mean_B
#     cov = BB.T @ AA / len(A)
#     U, S, Vt = np.linalg.svd(cov)
#     R = U @ Vt
#     if np.linalg.det(R) < 0:
#         U[:, -1] *= -1
#         R = U @ Vt
#     var_A = np.mean(np.sum(AA**2, axis=1))
#     s = np.sum(S) / var_A
#     t = mean_B - s * R @ mean_A
#     return s, R, t

# # -------------------------------
# # Main
# # -------------------------------

# def main():
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--colmap_sparse", required=True, type=Path)
#     parser.add_argument("--colmap_trajectory", required=True, type=Path)
#     parser.add_argument("--gpx_enu", required=True, type=Path)
#     parser.add_argument("--apply_points", action="store_true")
#     args = parser.parse_args()

#     colmap_traj = json.load(open(args.colmap_trajectory))
#     gpx_traj = json.load(open(args.gpx_enu))

#     gpx_times = np.array([p["timestamp"] for p in gpx_traj])
#     gpx_xyz = np.array([p["enu"] for p in gpx_traj])

#     P = []
#     Q = []

#     for cam in colmap_traj:
#         t = cam["timestamp"]
#         i = np.argmin(np.abs(gpx_times - t))
#         P.append(cam["colmap"])
#         Q.append(gpx_xyz[i])

#     P = np.array(P)
#     Q = np.array(Q)

#     s, R, t = umeyama_alignment(P, Q)

#     print("✅ Umeyama transform:")
#     print("Scale:", s)
#     print("Rotation:\n", R)
#     print("Translation:", t)

#     images_bin = args.colmap_sparse / "images.bin"
#     images = read_images_binary(images_bin)

#     for img in images.values():
#         R0 = qvec2rotmat(img["qvec"])
#         C0 = -R0.T @ img["tvec"]

#         Cw = s * (R @ C0) + t
#         Rw = R @ R0

#         qvec_new = rotmat2qvec(Rw)
#         tvec_new = -Rw @ Cw

#         img["qvec"] = qvec_new
#         img["tvec"] = tvec_new

#     write_images_binary(images_bin, images)

#     if args.apply_points:
#         points_bin = args.colmap_sparse / "points3D.bin"
#         points = read_points3D_binary(points_bin)
#         for p in points.values():
#             p["xyz"] = s * (R @ p["xyz"]) + t
#         write_points3D_binary(points_bin, points)

#     print("✅ COLMAP rewritten in ENU world space")

# if __name__ == "__main__":
#     main()


# #!/usr/bin/env python3
# """
# Align COLMAP reconstruction to GPX ENU world coordinates using Umeyama
# and rewrite images.bin (and optionally points3D.bin) in-place.

# Also saves the world similarity transform (s, R, t) as:
#   <colmap_sparse>/world_transform.json

# Usage:
#   python align_colmap_to_gpx_enu.py \
#     --colmap_sparse keyframes/colmap/sparse/0 \
#     --colmap_trajectory colmap_camera_poses.json \
#     --gpx_enu gpx_enu_raw.json \
#     --apply_points

# If --apply_points is omitted, only cameras are transformed.
# """

# import argparse
# import json
# import numpy as np
# from pathlib import Path
# import struct

# # -------------------------------
# # Low-level COLMAP binary tools
# # -------------------------------

# def read_next_bytes(fid, num_bytes, format_char_sequence, endian_character="<"):
#     data = fid.read(num_bytes)
#     return struct.unpack(endian_character + format_char_sequence, data)

# def write_next_bytes(fid, format_char_sequence, data, endian_character="<"):
#     fid.write(struct.pack(endian_character + format_char_sequence, *data))

# def read_images_binary(path):
#     images = {}
#     with open(path, "rb") as fid:
#         num_images = read_next_bytes(fid, 8, "Q")[0]

#         for _ in range(num_images):
#             image_id = read_next_bytes(fid, 4, "I")[0]
#             qvec = np.array(read_next_bytes(fid, 8 * 4, "dddd"))
#             tvec = np.array(read_next_bytes(fid, 8 * 3, "ddd"))
#             camera_id = read_next_bytes(fid, 4, "I")[0]

#             name = b""
#             while True:
#                 c = fid.read(1)
#                 if c == b"\x00":
#                     break
#                 name += c
#             name = name.decode("utf-8")

#             num_points2D = read_next_bytes(fid, 8, "Q")[0]
#             fid.read(num_points2D * 24)

#             images[image_id] = {
#                 "image_id": image_id,
#                 "qvec": qvec,
#                 "tvec": tvec,
#                 "camera_id": camera_id,
#                 "name": name,
#             }
#     return images

# def write_images_binary(path, images):
#     with open(path, "wb") as fid:
#         write_next_bytes(fid, "Q", [len(images)])
#         for img in images.values():
#             write_next_bytes(fid, "I", [img["image_id"]])
#             write_next_bytes(fid, "dddd", img["qvec"])
#             write_next_bytes(fid, "ddd", img["tvec"])
#             write_next_bytes(fid, "I", [img["camera_id"]])
#             fid.write(img["name"].encode("utf-8") + b"\x00")
#             write_next_bytes(fid, "Q", [0])

# def read_points3D_binary(path):
#     points = {}
#     with open(path, "rb") as fid:
#         num_points = read_next_bytes(fid, 8, "Q")[0]
#         for _ in range(num_points):
#             point_id = read_next_bytes(fid, 8, "Q")[0]
#             xyz = np.array(read_next_bytes(fid, 24, "ddd"))
#             rgb = np.array(read_next_bytes(fid, 3, "BBB"))
#             error = read_next_bytes(fid, 8, "d")[0]
#             track_len = read_next_bytes(fid, 8, "Q")[0]
#             fid.read(track_len * 8)
#             points[point_id] = {"xyz": xyz, "rgb": rgb, "error": error}
#     return points

# def write_points3D_binary(path, points):
#     with open(path, "wb") as fid:
#         write_next_bytes(fid, "Q", [len(points)])
#         for pid, p in points.items():
#             write_next_bytes(fid, "Q", [pid])
#             write_next_bytes(fid, "ddd", p["xyz"])
#             write_next_bytes(fid, "BBB", list(p["rgb"]))
#             write_next_bytes(fid, "d", [p["error"]])
#             write_next_bytes(fid, "Q", [0])

# # -------------------------------
# # Quaternion helpers
# # -------------------------------

# def qvec2rotmat(qvec):
#     q0, q1, q2, q3 = qvec
#     return np.array([
#         [1 - 2*q2*q2 - 2*q3*q3,     2*q1*q2 - 2*q0*q3,     2*q1*q3 + 2*q0*q2],
#         [2*q1*q2 + 2*q0*q3,         1 - 2*q1*q1 - 2*q3*q3, 2*q2*q3 - 2*q0*q1],
#         [2*q1*q3 - 2*q0*q2,         2*q2*q3 + 2*q0*q1,     1 - 2*q1*q1 - 2*q2*q2]
#     ])

# def rotmat2qvec(R):
#     K = np.array([
#         [R[0,0]-R[1,1]-R[2,2], 0, 0, 0],
#         [R[1,0]+R[0,1], R[1,1]-R[0,0]-R[2,2], 0, 0],
#         [R[2,0]+R[0,2], R[2,1]+R[1,2], R[2,2]-R[0,0]-R[1,1], 0],
#         [R[1,2]-R[2,1], R[2,0]-R[0,2], R[0,1]-R[1,0], R[0,0]+R[1,1]+R[2,2]]
#     ]) / 3.0

#     eigvals, eigvecs = np.linalg.eigh(K)
#     qvec = eigvecs[:, np.argmax(eigvals)]
#     if qvec[0] < 0:
#         qvec = -qvec
#     return qvec

# # -------------------------------
# # Umeyama alignment
# # -------------------------------

# def umeyama_alignment(A, B):
#     mean_A = A.mean(axis=0)
#     mean_B = B.mean(axis=0)

#     AA = A - mean_A
#     BB = B - mean_B

#     cov = BB.T @ AA / len(A)
#     U, S, Vt = np.linalg.svd(cov)
#     R = U @ Vt

#     if np.linalg.det(R) < 0:
#         U[:, -1] *= -1
#         R = U @ Vt

#     var_A = np.mean(np.sum(AA**2, axis=1))
#     s = np.sum(S) / var_A

#     t = mean_B - s * R @ mean_A
#     return s, R, t

# # -------------------------------
# # GPX interpolation helper
# # -------------------------------

# def interpolate_gpx_position(gpx_times, gpx_xyz, t):
#     if t <= gpx_times[0]:
#         return gpx_xyz[0]
#     if t >= gpx_times[-1]:
#         return gpx_xyz[-1]

#     j = np.searchsorted(gpx_times, t)

#     t0 = gpx_times[j - 1]
#     t1 = gpx_times[j]
#     p0 = gpx_xyz[j - 1]
#     p1 = gpx_xyz[j]

#     alpha = (t - t0) / (t1 - t0)
#     return (1.0 - alpha) * p0 + alpha * p1

# # -------------------------------
# # Main
# # -------------------------------

# def main():
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--colmap_sparse", required=True, type=Path)
#     parser.add_argument("--colmap_trajectory", required=True, type=Path)
#     parser.add_argument("--gpx_enu", required=True, type=Path)
#     parser.add_argument("--apply_points", action="store_true")
#     args = parser.parse_args()

#     colmap_traj = json.load(open(args.colmap_trajectory))
#     gpx_traj = json.load(open(args.gpx_enu))

#     gpx_times = np.array([p["timestamp"] for p in gpx_traj])
#     gpx_xyz = np.array([p["enu"] for p in gpx_traj])

#     P = []
#     Q = []

#     for cam in colmap_traj:
#         t_cam = cam["timestamp"]
#         gps_pos = interpolate_gpx_position(gpx_times, gpx_xyz, t_cam)
#         P.append(cam["colmap"])
#         Q.append(gps_pos)

#     P = np.array(P)
#     Q = np.array(Q)

#     s, R, t = umeyama_alignment(P, Q)

#     print("✅ Umeyama transform:")
#     print("Scale:", s)
#     print("Rotation:\n", R)
#     print("Translation:", t)

#     # -------------------------------
#     # SAVE METADATA TRANSFORM
#     # -------------------------------

#     transform_out = args.colmap_sparse / "world_transform.json"
#     transform_data = {
#         "scale": float(s),
#         "rotation": R.tolist(),
#         "translation": t.tolist(),
#         "source_frame": "COLMAP",
#         "target_frame": "ENU",
#         "units": "meters"
#     }

#     with open(transform_out, "w") as f:
#         json.dump(transform_data, f, indent=2)

#     print(f"✅ World transform written to: {transform_out}")

#     # -------------------------------
#     # Rewrite COLMAP images.bin
#     # -------------------------------

#     images_bin = args.colmap_sparse / "images.bin"
#     images = read_images_binary(images_bin)

#     for img in images.values():
#         R0 = qvec2rotmat(img["qvec"])
#         C0 = -R0.T @ img["tvec"]

#         Cw = s * (R @ C0) + t
#         Rw = R @ R0

#         qvec_new = rotmat2qvec(Rw)
#         tvec_new = -Rw @ Cw

#         img["qvec"] = qvec_new
#         img["tvec"] = tvec_new

#     write_images_binary(images_bin, images)

#     # -------------------------------
#     # Rewrite points3D.bin (optional)
#     # -------------------------------

#     if args.apply_points:
#         points_bin = args.colmap_sparse / "points3D.bin"
#         points = read_points3D_binary(points_bin)
#         for p in points.values():
#             p["xyz"] = s * (R @ p["xyz"]) + t
#         write_points3D_binary(points_bin, points)

#     print("✅ COLMAP rewritten in ENU world space")

# if __name__ == "__main__":
#     main()


# #!/usr/bin/env python3
# """
# Align COLMAP reconstruction to GPX ENU world coordinates using Umeyama
# and rewrite images.bin (and optionally points3D.bin) in-place.

# ✅ LOSSLESS VERSION:
# - Preserves all 2D–3D correspondences
# - Preserves all point tracks
# - Only updates pose & XYZ fields

# Also saves:
#   <colmap_sparse>/world_transform.json
# """

# import argparse
# import json
# import numpy as np
# from pathlib import Path
# import struct

# # -------------------------------
# # Low-level COLMAP binary tools
# # -------------------------------

# def read_next_bytes(fid, num_bytes, format_char_sequence, endian_character="<"):
#     data = fid.read(num_bytes)
#     return struct.unpack(endian_character + format_char_sequence, data)

# def write_next_bytes(fid, format_char_sequence, data, endian_character="<"):
#     fid.write(struct.pack(endian_character + format_char_sequence, *data))

# # -------------------------------
# # IMAGES.BIN (LOSSLESS)
# # -------------------------------

# def read_images_binary(path):
#     images = {}
#     with open(path, "rb") as fid:
#         num_images = read_next_bytes(fid, 8, "Q")[0]

#         for _ in range(num_images):
#             image_id = read_next_bytes(fid, 4, "I")[0]
#             qvec = np.array(read_next_bytes(fid, 32, "dddd"))
#             tvec = np.array(read_next_bytes(fid, 24, "ddd"))
#             camera_id = read_next_bytes(fid, 4, "I")[0]

#             name = b""
#             while True:
#                 c = fid.read(1)
#                 if c == b"\x00":
#                     break
#                 name += c
#             name = name.decode("utf-8")

#             num_points2D = read_next_bytes(fid, 8, "Q")[0]
#             points2D = [read_next_bytes(fid, 24, "ddq") for _ in range(num_points2D)]

#             images[image_id] = {
#                 "image_id": image_id,
#                 "qvec": qvec,
#                 "tvec": tvec,
#                 "camera_id": camera_id,
#                 "name": name,
#                 "num_points2D": num_points2D,
#                 "points2D": points2D,
#             }
#     return images

# def write_images_binary(path, images):
#     with open(path, "wb") as fid:
#         write_next_bytes(fid, "Q", [len(images)])
#         for img in images.values():
#             write_next_bytes(fid, "I", [img["image_id"]])
#             write_next_bytes(fid, "dddd", img["qvec"])
#             write_next_bytes(fid, "ddd", img["tvec"])
#             write_next_bytes(fid, "I", [img["camera_id"]])
#             fid.write(img["name"].encode("utf-8") + b"\x00")

#             write_next_bytes(fid, "Q", [img["num_points2D"]])
#             for pt in img["points2D"]:
#                 write_next_bytes(fid, "ddq", pt)

# # -------------------------------
# # POINTS3D.BIN (LOSSLESS)
# # -------------------------------

# def read_points3D_binary(path):
#     points = {}
#     with open(path, "rb") as fid:
#         num_points = read_next_bytes(fid, 8, "Q")[0]
#         for _ in range(num_points):
#             point_id = read_next_bytes(fid, 8, "Q")[0]
#             xyz = np.array(read_next_bytes(fid, 24, "ddd"))
#             rgb = np.array(read_next_bytes(fid, 3, "BBB"))
#             error = read_next_bytes(fid, 8, "d")[0]
#             track_len = read_next_bytes(fid, 8, "Q")[0]
#             track = [read_next_bytes(fid, 8, "ii") for _ in range(track_len)]

#             points[point_id] = {
#                 "xyz": xyz,
#                 "rgb": rgb,
#                 "error": error,
#                 "track_len": track_len,
#                 "track": track,
#             }
#     return points

# def write_points3D_binary(path, points):
#     with open(path, "wb") as fid:
#         write_next_bytes(fid, "Q", [len(points)])
#         for pid, p in points.items():
#             write_next_bytes(fid, "Q", [pid])
#             write_next_bytes(fid, "ddd", p["xyz"])
#             write_next_bytes(fid, "BBB", list(p["rgb"]))
#             write_next_bytes(fid, "d", [p["error"]])
#             write_next_bytes(fid, "Q", [p["track_len"]])
#             for tr in p["track"]:
#                 write_next_bytes(fid, "ii", tr)

# # -------------------------------
# # Quaternion helpers
# # -------------------------------

# def qvec2rotmat(qvec):
#     q0, q1, q2, q3 = qvec
#     return np.array([
#         [1 - 2*q2*q2 - 2*q3*q3, 2*q1*q2 - 2*q0*q3, 2*q1*q3 + 2*q0*q2],
#         [2*q1*q2 + 2*q0*q3, 1 - 2*q1*q1 - 2*q3*q3, 2*q2*q3 - 2*q0*q1],
#         [2*q1*q3 - 2*q0*q2, 2*q2*q3 + 2*q0*q1, 1 - 2*q1*q1 - 2*q2*q2]
#     ])

# def rotmat2qvec(R):
#     K = np.array([
#         [R[0,0]-R[1,1]-R[2,2], 0, 0, 0],
#         [R[1,0]+R[0,1], R[1,1]-R[0,0]-R[2,2], 0, 0],
#         [R[2,0]+R[0,2], R[2,1]+R[1,2], R[2,2]-R[0,0]-R[1,1], 0],
#         [R[1,2]-R[2,1], R[2,0]-R[0,2], R[0,1]-R[1,0], R[0,0]+R[1,1]+R[2,2]]
#     ]) / 3.0

#     eigvals, eigvecs = np.linalg.eigh(K)
#     qvec = eigvecs[:, np.argmax(eigvals)]
#     if qvec[0] < 0:
#         qvec = -qvec
#     return qvec

# # -------------------------------
# # Umeyama alignment
# # -------------------------------

# def umeyama_alignment(A, B):
#     mean_A = A.mean(axis=0)
#     mean_B = B.mean(axis=0)

#     AA = A - mean_A
#     BB = B - mean_B

#     cov = BB.T @ AA / len(A)
#     U, S, Vt = np.linalg.svd(cov)
#     R = U @ Vt

#     if np.linalg.det(R) < 0:
#         U[:, -1] *= -1
#         R = U @ Vt

#     var_A = np.mean(np.sum(AA**2, axis=1))
#     s = np.sum(S) / var_A
#     t = mean_B - s * R @ mean_A

#     return s, R, t

# # -------------------------------
# # GPX interpolation helper
# # -------------------------------

# def interpolate_gpx_position(gpx_times, gpx_xyz, t):
#     if t <= gpx_times[0]:
#         return gpx_xyz[0]
#     if t >= gpx_times[-1]:
#         return gpx_xyz[-1]

#     j = np.searchsorted(gpx_times, t)

#     t0 = gpx_times[j - 1]
#     t1 = gpx_times[j]
#     p0 = gpx_xyz[j - 1]
#     p1 = gpx_xyz[j]

#     alpha = (t - t0) / (t1 - t0)
#     return (1.0 - alpha) * p0 + alpha * p1

# # -------------------------------
# # Main
# # -------------------------------

# def main():
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--colmap_sparse", required=True, type=Path)
#     parser.add_argument("--colmap_trajectory", required=True, type=Path)
#     parser.add_argument("--gpx_enu", required=True, type=Path)
#     parser.add_argument("--apply_points", action="store_true")
#     args = parser.parse_args()

#     colmap_traj = json.load(open(args.colmap_trajectory))
#     gpx_traj = json.load(open(args.gpx_enu))

#     gpx_times = np.array([p["timestamp"] for p in gpx_traj])
#     gpx_xyz = np.array([p["enu"] for p in gpx_traj])

#     P = []
#     Q = []

#     for cam in colmap_traj:
#         gps_pos = interpolate_gpx_position(gpx_times, gpx_xyz, cam["timestamp"])
#         P.append(cam["colmap"])
#         Q.append(gps_pos)

#     P = np.array(P)
#     Q = np.array(Q)

#     s, R, t = umeyama_alignment(P, Q)

#     print("✅ Umeyama transform:")
#     print("Scale:", s)
#     print("Rotation:\n", R)
#     print("Translation:", t)

#     transform_out = args.colmap_sparse / "world_transform.json"
#     json.dump({
#         "scale": float(s),
#         "rotation": R.tolist(),
#         "translation": t.tolist(),
#         "source_frame": "COLMAP",
#         "target_frame": "ENU",
#         "units": "meters"
#     }, open(transform_out, "w"), indent=2)

#     # images_bin = args.colmap_sparse / "images.bin"
#     # images = read_images_binary(images_bin)

#     # for img in images.values():
#     #     R0 = qvec2rotmat(img["qvec"])
#     #     C0 = -R0.T @ img["tvec"]

#     #     Cw = s * (R @ C0) + t
#     #     Rw = R @ R0

#     #     img["qvec"] = rotmat2qvec(Rw)
#     #     img["tvec"] = -Rw @ Cw

#     # write_images_binary(images_bin, images)

#     images_bin = args.colmap_sparse / "images.bin"
#     images = read_images_binary(images_bin)

#     for img in images.values():
#         R0 = qvec2rotmat(img["qvec"])
#         C0 = -R0.T @ img["tvec"]

#         # Transform camera center into ENU
#         Cw = s * (R @ C0) + t

#         # ✅ Correct way to update rotation for a world transform x' = s R x + t
#         # Original: x_cam = R0 * x_world + t0
#         # New world: x_world' = s R x_world + t
#         # New extrinsic: x_cam = Rw * x_world' + tw  =>  Rw = R0 @ R.T
#         Rw = R0 @ R.T

#         img["qvec"] = rotmat2qvec(Rw)
#         img["tvec"] = -Rw @ Cw

#     write_images_binary(images_bin, images)

    

#     if args.apply_points:
#         points_bin = args.colmap_sparse / "points3D.bin"
#         points = read_points3D_binary(points_bin)
#         for p in points.values():
#             p["xyz"] = s * (R @ p["xyz"]) + t
#         write_points3D_binary(points_bin, points)

#     print("✅ COLMAP rewritten in ENU world space (LOSSLESS)")

# if __name__ == "__main__":
#     main()

# #!/usr/bin/env python3
# """
# Align COLMAP reconstruction to GPX ENU world coordinates using Umeyama
# and rewrite images.bin (and optionally points3D.bin) in-place.

# ✅ LOSSLESS VERSION:
# - Preserves all 2D–3D correspondences
# - Preserves all point tracks
# - Only updates pose & XYZ fields

# ✅ FINAL FIXES INCLUDED:
# - Correct rotation composition
# - Correct ENU → COLMAP world-axis conversion
# - Linear GPX interpolation
# - World transform saved to JSON

# Also saves:
#   <colmap_sparse>/world_transform.json
# """

# import argparse
# import json
# import numpy as np
# from pathlib import Path
# import struct

# # -------------------------------
# # Low-level COLMAP binary tools
# # -------------------------------

# def read_next_bytes(fid, num_bytes, format_char_sequence, endian_character="<"):
#     data = fid.read(num_bytes)
#     return struct.unpack(endian_character + format_char_sequence, data)

# def write_next_bytes(fid, format_char_sequence, data, endian_character="<"):
#     fid.write(struct.pack(endian_character + format_char_sequence, *data))

# # -------------------------------
# # IMAGES.BIN (LOSSLESS)
# # -------------------------------

# def read_images_binary(path):
#     images = {}
#     with open(path, "rb") as fid:
#         num_images = read_next_bytes(fid, 8, "Q")[0]

#         for _ in range(num_images):
#             image_id = read_next_bytes(fid, 4, "I")[0]
#             qvec = np.array(read_next_bytes(fid, 32, "dddd"))
#             tvec = np.array(read_next_bytes(fid, 24, "ddd"))
#             camera_id = read_next_bytes(fid, 4, "I")[0]

#             name = b""
#             while True:
#                 c = fid.read(1)
#                 if c == b"\x00":
#                     break
#                 name += c
#             name = name.decode("utf-8")

#             num_points2D = read_next_bytes(fid, 8, "Q")[0]
#             points2D = [read_next_bytes(fid, 24, "ddq") for _ in range(num_points2D)]

#             images[image_id] = {
#                 "image_id": image_id,
#                 "qvec": qvec,
#                 "tvec": tvec,
#                 "camera_id": camera_id,
#                 "name": name,
#                 "num_points2D": num_points2D,
#                 "points2D": points2D,
#             }
#     return images

# def write_images_binary(path, images):
#     with open(path, "wb") as fid:
#         write_next_bytes(fid, "Q", [len(images)])
#         for img in images.values():
#             write_next_bytes(fid, "I", [img["image_id"]])
#             write_next_bytes(fid, "dddd", img["qvec"])
#             write_next_bytes(fid, "ddd", img["tvec"])
#             write_next_bytes(fid, "I", [img["camera_id"]])
#             fid.write(img["name"].encode("utf-8") + b"\x00")

#             write_next_bytes(fid, "Q", [img["num_points2D"]])
#             for pt in img["points2D"]:
#                 write_next_bytes(fid, "ddq", pt)

# # -------------------------------
# # POINTS3D.BIN (LOSSLESS)
# # -------------------------------

# def read_points3D_binary(path):
#     points = {}
#     with open(path, "rb") as fid:
#         num_points = read_next_bytes(fid, 8, "Q")[0]
#         for _ in range(num_points):
#             point_id = read_next_bytes(fid, 8, "Q")[0]
#             xyz = np.array(read_next_bytes(fid, 24, "ddd"))
#             rgb = np.array(read_next_bytes(fid, 3, "BBB"))
#             error = read_next_bytes(fid, 8, "d")[0]
#             track_len = read_next_bytes(fid, 8, "Q")[0]
#             track = [read_next_bytes(fid, 8, "ii") for _ in range(track_len)]

#             points[point_id] = {
#                 "xyz": xyz,
#                 "rgb": rgb,
#                 "error": error,
#                 "track_len": track_len,
#                 "track": track,
#             }
#     return points

# def write_points3D_binary(path, points):
#     with open(path, "wb") as fid:
#         write_next_bytes(fid, "Q", [len(points)])
#         for pid, p in points.items():
#             write_next_bytes(fid, "Q", [pid])
#             write_next_bytes(fid, "ddd", p["xyz"])
#             write_next_bytes(fid, "BBB", list(p["rgb"]))
#             write_next_bytes(fid, "d", [p["error"]])
#             write_next_bytes(fid, "Q", [p["track_len"]])
#             for tr in p["track"]:
#                 write_next_bytes(fid, "ii", tr)

# # -------------------------------
# # Quaternion helpers
# # -------------------------------

# def qvec2rotmat(qvec):
#     q0, q1, q2, q3 = qvec
#     return np.array([
#         [1 - 2*q2*q2 - 2*q3*q3, 2*q1*q2 - 2*q0*q3, 2*q1*q3 + 2*q0*q2],
#         [2*q1*q2 + 2*q0*q3, 1 - 2*q1*q1 - 2*q3*q3, 2*q2*q3 - 2*q0*q1],
#         [2*q1*q3 - 2*q0*q2, 2*q2*q3 + 2*q0*q1, 1 - 2*q1*q1 - 2*q2*q2]
#     ])

# def rotmat2qvec(R):
#     K = np.array([
#         [R[0,0]-R[1,1]-R[2,2], 0, 0, 0],
#         [R[1,0]+R[0,1], R[1,1]-R[0,0]-R[2,2], 0, 0],
#         [R[2,0]+R[0,2], R[2,1]+R[1,2], R[2,2]-R[0,0]-R[1,1], 0],
#         [R[1,2]-R[2,1], R[2,0]-R[0,2], R[0,1]-R[1,0], R[0,0]+R[1,1]+R[2,2]]
#     ]) / 3.0

#     eigvals, eigvecs = np.linalg.eigh(K)
#     qvec = eigvecs[:, np.argmax(eigvals)]
#     if qvec[0] < 0:
#         qvec = -qvec
#     return qvec

# # -------------------------------
# # Umeyama alignment
# # -------------------------------

# def umeyama_alignment(A, B):
#     mean_A = A.mean(axis=0)
#     mean_B = B.mean(axis=0)

#     AA = A - mean_A
#     BB = B - mean_B

#     cov = BB.T @ AA / len(A)
#     U, S, Vt = np.linalg.svd(cov)
#     R = U @ Vt

#     if np.linalg.det(R) < 0:
#         U[:, -1] *= -1
#         R = U @ Vt

#     var_A = np.mean(np.sum(AA**2, axis=1))
#     s = np.sum(S) / var_A
#     t = mean_B - s * R @ mean_A

#     return s, R, t

# # -------------------------------
# # GPX interpolation helper
# # -------------------------------

# def interpolate_gpx_position(gpx_times, gpx_xyz, t):
#     if t <= gpx_times[0]:
#         return gpx_xyz[0]
#     if t >= gpx_times[-1]:
#         return gpx_xyz[-1]

#     j = np.searchsorted(gpx_times, t)

#     t0 = gpx_times[j - 1]
#     t1 = gpx_times[j]
#     p0 = gpx_xyz[j - 1]
#     p1 = gpx_xyz[j]

#     alpha = (t - t0) / (t1 - t0)
#     return (1.0 - alpha) * p0 + alpha * p1

# # -------------------------------
# # ✅ ENU → COLMAP WORLD AXIS FIX
# # -------------------------------

# R_ENU_TO_COLMAP = np.array([
#     [ 1,  0,  0],   # X =  E
#     [ 0,  0, -1],   # Y = -U  (COLMAP Y is down)
#     [ 0,  1,  0]    # Z =  N
# ])

# # -------------------------------
# # Main
# # -------------------------------

# def main():
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--colmap_sparse", required=True, type=Path)
#     parser.add_argument("--colmap_trajectory", required=True, type=Path)
#     parser.add_argument("--gpx_enu", required=True, type=Path)
#     parser.add_argument("--apply_points", action="store_true")
#     args = parser.parse_args()

#     colmap_traj = json.load(open(args.colmap_trajectory))
#     gpx_traj = json.load(open(args.gpx_enu))

#     gpx_times = np.array([p["timestamp"] for p in gpx_traj])
#     gpx_xyz = np.array([p["enu"] for p in gpx_traj])

#     P = []
#     Q = []

#     for cam in colmap_traj:
#         gps_pos = interpolate_gpx_position(gpx_times, gpx_xyz, cam["timestamp"])
#         P.append(cam["colmap"])
#         Q.append(gps_pos)

#     P = np.array(P)
#     Q = np.array(Q)

#     s, R, t = umeyama_alignment(P, Q)

#     print("✅ Umeyama transform:")
#     print("Scale:", s)
#     print("Rotation:\n", R)
#     print("Translation:", t)

#     transform_out = args.colmap_sparse / "world_transform.json"
#     json.dump({
#         "scale": float(s),
#         "rotation": R.tolist(),
#         "translation": t.tolist(),
#         "source_frame": "COLMAP",
#         "target_frame": "ENU",
#         "units": "meters"
#     }, open(transform_out, "w"), indent=2)

#     # -------------------------------
#     # Rewrite images.bin
#     # -------------------------------

#     images_bin = args.colmap_sparse / "images.bin"
#     images = read_images_binary(images_bin)

#     for img in images.values():
#         R0 = qvec2rotmat(img["qvec"])
#         C0 = -R0.T @ img["tvec"]

#         # World alignment in ENU
#         C_enu = s * (R @ C0) + t
#         R_enu = R0 @ R.T

#         # ✅ ENU → COLMAP world-axis conversion
#         Cw = R_ENU_TO_COLMAP @ C_enu
#         Rw = R_ENU_TO_COLMAP @ R_enu

#         img["qvec"] = rotmat2qvec(Rw)
#         img["tvec"] = -Rw @ Cw

#     write_images_binary(images_bin, images)

#     # -------------------------------
#     # Rewrite points3D.bin
#     # -------------------------------

#     if args.apply_points:
#         points_bin = args.colmap_sparse / "points3D.bin"
#         points = read_points3D_binary(points_bin)
#         for p in points.values():
#             xyz_enu = s * (R @ p["xyz"]) + t
#             p["xyz"] = R_ENU_TO_COLMAP @ xyz_enu
#         write_points3D_binary(points_bin, points)

#     print("✅ COLMAP rewritten in ENU world space with correct COLMAP axes")

# if __name__ == "__main__":
#     main()


#!/usr/bin/env python3
"""
Compute similarity transform aligning COLMAP camera centers to GPX ENU
coordinates using Umeyama, and write it to world_transform.json.

⚠️ This version does NOT modify any COLMAP binaries.
    - images.bin left untouched
    - points3D.bin left untouched

Use this when you want:
    - A working COLMAP model for NeRF / Splatfacto
    - A clean similarity transform to map results into ENU later
"""

import argparse
import json
import numpy as np
from pathlib import Path
import struct

# -------------------------------
# Low-level COLMAP binary tools
# -------------------------------

def read_next_bytes(fid, num_bytes, format_char_sequence, endian_character="<"):
    data = fid.read(num_bytes)
    return struct.unpack(endian_character + format_char_sequence, data)

def read_images_binary(path):
    """Read only what we need: image name, qvec, tvec."""
    images = {}
    with open(path, "rb") as fid:
        num_images = read_next_bytes(fid, 8, "Q")[0]
        for _ in range(num_images):
            image_id = read_next_bytes(fid, 4, "I")[0]
            qvec = np.array(read_next_bytes(fid, 32, "dddd"))
            tvec = np.array(read_next_bytes(fid, 24, "ddd"))
            camera_id = read_next_bytes(fid, 4, "I")[0]

            name = b""
            while True:
                c = fid.read(1)
                if c == b"\x00":
                    break
                name += c
            name = name.decode("utf-8")

            num_points2D = read_next_bytes(fid, 8, "Q")[0]
            # Skip the actual 2D points; we only need poses for alignment
            fid.read(num_points2D * 24)

            images[image_id] = {
                "image_id": image_id,
                "qvec": qvec,
                "tvec": tvec,
                "camera_id": camera_id,
                "name": name,
            }
    return images

# -------------------------------
# Quaternion helpers
# -------------------------------

def qvec2rotmat(qvec):
    q0, q1, q2, q3 = qvec
    return np.array([
        [1 - 2*q2*q2 - 2*q3*q3, 2*q1*q2 - 2*q0*q3, 2*q1*q3 + 2*q0*q2],
        [2*q1*q2 + 2*q0*q3, 1 - 2*q1*q1 - 2*q3*q3, 2*q2*q3 - 2*q0*q1],
        [2*q1*q3 - 2*q0*q2, 2*q2*q3 + 2*q0*q1, 1 - 2*q1*q1 - 2*q2*q2]
    ])

# -------------------------------
# Umeyama alignment
# -------------------------------

def umeyama_alignment(A, B):
    """
    Compute similarity transform (s, R, t) such that:
        B ≈ s * R * A + t
    A, B: (N,3) arrays of corresponding points
    """
    mean_A = A.mean(axis=0)
    mean_B = B.mean(axis=0)

    AA = A - mean_A
    BB = B - mean_B

    cov = BB.T @ AA / len(A)
    U, S, Vt = np.linalg.svd(cov)
    R = U @ Vt

    if np.linalg.det(R) < 0:
        U[:, -1] *= -1
        R = U @ Vt

    var_A = np.mean(np.sum(AA**2, axis=1))
    s = np.sum(S) / var_A
    t = mean_B - s * R @ mean_A

    return s, R, t

# -------------------------------
# GPX interpolation helper
# -------------------------------

def interpolate_gpx_position(gpx_times, gpx_xyz, t):
    """Linear interpolation of ENU position at time t."""
    if t <= gpx_times[0]:
        return gpx_xyz[0]
    if t >= gpx_times[-1]:
        return gpx_xyz[-1]

    j = np.searchsorted(gpx_times, t)

    t0 = gpx_times[j - 1]
    t1 = gpx_times[j]
    p0 = gpx_xyz[j - 1]
    p1 = gpx_xyz[j]

    alpha = (t - t0) / (t1 - t0)
    return (1.0 - alpha) * p0 + alpha * p1

# -------------------------------
# Main
# -------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--colmap_sparse", required=True, type=Path)
    parser.add_argument("--colmap_trajectory", required=True, type=Path)
    parser.add_argument("--gpx_enu", required=True, type=Path)
    args = parser.parse_args()

    # 1) Load COLMAP trajectory (camera centers in COLMAP coords, timestamped)
    colmap_traj = json.load(open(args.colmap_trajectory))
    # 2) Load GPX ENU trajectory (ENU coords, timestamped)
    gpx_traj = json.load(open(args.gpx_enu))

    gpx_times = np.array([p["timestamp"] for p in gpx_traj])
    gpx_xyz = np.array([p["enu"] for p in gpx_traj])

    # 3) Build correspondence sets
    P = []  # COLMAP camera centers
    Q = []  # interpolated ENU positions

    for cam in colmap_traj:
        gps_pos = interpolate_gpx_position(gpx_times, gpx_xyz, cam["timestamp"])
        P.append(cam["colmap"])
        Q.append(gps_pos)

    P = np.array(P)
    Q = np.array(Q)

    # 4) Solve Umeyama
    s, R, t = umeyama_alignment(P, Q)

    print("✅ Umeyama transform (COLMAP → ENU):")
    print("Scale:", s)
    print("Rotation:\n", R)
    print("Translation:", t)

    # 5) Save transform only (no COLMAP modification)
    transform_out = args.colmap_sparse / "world_transform.json"
    with open(transform_out, "w") as f:
        json.dump({
            "scale": float(s),
            "rotation": R.tolist(),
            "translation": t.tolist(),
            "source_frame": "COLMAP",
            "target_frame": "ENU",
            "units": "meters"
        }, f, indent=2)

    print(f"✅ World transform written to: {transform_out}")
    print("ℹ️ COLMAP binaries were NOT modified by this script.")

if __name__ == "__main__":
    main()

