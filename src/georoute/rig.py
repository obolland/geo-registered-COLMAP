"""The capture rig: camera geometry and the antenna lever arm, from a versioned TOML file.

Rig frame: x right, y forward, z up. Camera frame (COLMAP/OpenCV): x right, y down, z forward.
COLMAP can't model the antenna, so camera position priors are derived here:
    c = a + R_world_from_rig (t_camera_in_rig - l_antenna_in_rig)
"""

import hashlib
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from georoute.session import CAMERA_COUNT

RIG_SCHEMA = "georoute.rig/1.0.0"
_KEYS = {
    "": {"schema", "name", "measured", "reference", "antenna", "timing", "camera"},
    "reference": {"description", "height_above_road_m"},
    "antenna": {"arp_in_rig_m", "phase_centre_offset_m"},
    "timing": {"curtain_travel_ms"},
    "camera": {"index", "role", "folder", "serial", "position_in_rig_m", "yaw_pitch_roll_deg"},
}
_RIG_FROM_CAMERA_LEVEL = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])


class RigError(ValueError):
    pass


def _rx(deg):
    a = np.radians(deg)
    return np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])


def _ry(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])


def _rz(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])


def rig_from_camera(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """Yaw turns the lens left (+), pitch tilts it up (+), roll turns the camera clockwise (+) from behind."""
    return _rz(yaw_deg) @ _rx(pitch_deg) @ _ry(roll_deg) @ _RIG_FROM_CAMERA_LEVEL


@dataclass(frozen=True)
class Camera:
    index: int
    role: str
    folder: str
    serial: str | None
    position_in_rig_m: np.ndarray
    yaw_pitch_roll_deg: tuple[float, float, float]


@dataclass(frozen=True)
class Rig:
    name: str
    measured: bool
    cameras: tuple[Camera, ...]
    lever_arm_m: np.ndarray
    reference_height_above_road_m: float
    curtain_travel_s: float
    sha256: str

    def camera(self, index: int) -> Camera:
        return next(c for c in self.cameras if c.index == index)

    def rig_from_camera(self, index: int) -> np.ndarray:
        return rig_from_camera(*self.camera(index).yaw_pitch_roll_deg)

    def camera_centre(self, index: int, antenna, world_from_rig) -> np.ndarray:
        return np.asarray(antenna) + world_from_rig @ (
            self.camera(index).position_in_rig_m - self.lever_arm_m
        )

    def reference_point(self, antenna, world_from_rig) -> np.ndarray:
        return np.asarray(antenna) - world_from_rig @ self.lever_arm_m


def load_rig(path) -> Rig:
    path = Path(path)
    raw = path.read_bytes()
    try:
        doc = tomllib.loads(raw.decode())
    except tomllib.TOMLDecodeError as e:
        raise RigError(f"{path.name} is not valid TOML: {e}") from e
    if doc.get("schema") != RIG_SCHEMA:
        raise RigError(f"{path.name} schema is {doc.get('schema')!r}, expected {RIG_SCHEMA}")
    _check_keys(doc, "")
    for table in ("reference", "antenna", "timing"):
        if table not in doc:
            raise RigError(f"{path.name} is missing [{table}]")
        _check_keys(doc[table], table)
    cameras = []
    for entry in doc.get("camera", []):
        _check_keys(entry, "camera")
        cameras.append(_camera(entry))
    _check_unique(cameras)
    antenna = doc["antenna"]
    lever = _vector(antenna, "arp_in_rig_m", "antenna") + _vector(antenna, "phase_centre_offset_m", "antenna")
    return Rig(
        name=doc.get("name", path.stem),
        measured=bool(doc.get("measured", False)),
        cameras=tuple(sorted(cameras, key=lambda c: c.index)),
        lever_arm_m=lever,
        reference_height_above_road_m=float(doc["reference"]["height_above_road_m"]),
        curtain_travel_s=float(doc["timing"]["curtain_travel_ms"]) / 1000,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _check_keys(table: dict, name: str):
    unknown = set(table) - _KEYS[name]
    if unknown:
        raise RigError(f"unknown key {sorted(unknown)[0]} in {'[' + name + ']' if name else 'the top level'}")


def _vector(table: dict, key: str, where: str, size: int = 3) -> np.ndarray:
    value = table.get(key)
    if (
        not isinstance(value, list)
        or len(value) != size
        or not all(isinstance(v, int | float) for v in value)
    ):
        raise RigError(f"{where} {key} must be {size} numbers, got {value!r}")
    return np.array(value, float)


def _camera(entry: dict) -> Camera:
    index = entry.get("index")
    if not isinstance(index, int) or not 0 <= index < CAMERA_COUNT:
        raise RigError(f"camera index {index} is outside 0..{CAMERA_COUNT - 1}")
    where = f"camera {index}"
    yaw, pitch, roll = _vector(entry, "yaw_pitch_roll_deg", where)
    return Camera(
        index,
        str(entry.get("role", "")),
        str(entry.get("folder", f"cam{index}")),
        str(entry["serial"]) if entry.get("serial") else None,
        _vector(entry, "position_in_rig_m", where),
        (float(yaw), float(pitch), float(roll)),
    )


def _check_unique(cameras: list[Camera]):
    for attr, label in (("index", "camera index"), ("folder", "folder")):
        values = [getattr(c, attr) for c in cameras]
        for v in values:
            if values.count(v) > 1:
                raise RigError(
                    f"{label} {v} is used twice" if attr == "folder" else f"{label} {v} appears twice"
                )


def _quaternion(r: np.ndarray) -> np.ndarray:
    w = np.sqrt(max(0.0, 1 + r[0, 0] + r[1, 1] + r[2, 2])) / 2
    x = np.copysign(np.sqrt(max(0.0, 1 + r[0, 0] - r[1, 1] - r[2, 2])) / 2, r[2, 1] - r[1, 2])
    y = np.copysign(np.sqrt(max(0.0, 1 - r[0, 0] + r[1, 1] - r[2, 2])) / 2, r[0, 2] - r[2, 0])
    z = np.copysign(np.sqrt(max(0.0, 1 - r[0, 0] - r[1, 1] + r[2, 2])) / 2, r[1, 0] - r[0, 1])
    return np.array([w, x, y, z])


def _matrix(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def slerp(times: np.ndarray, rotations: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Rotation matrices at query times, spherically interpolated between known (time, rotation) pairs."""
    query = np.asarray(query, float)
    if (query < times[0]).any() or (query > times[-1]).any():
        raise RigError("attitude requested outside the known times")
    out = []
    for t in query:
        i = min(int(np.searchsorted(times, t, side="right")) - 1, len(times) - 2)
        q0, q1 = _quaternion(rotations[i]), _quaternion(rotations[i + 1])
        if q0 @ q1 < 0:
            q1 = -q1
        u = (t - times[i]) / (times[i + 1] - times[i])
        angle = np.arccos(np.clip(q0 @ q1, -1.0, 1.0))
        if angle < 1e-12:
            out.append(_matrix(q0))
            continue
        out.append(_matrix((np.sin((1 - u) * angle) * q0 + np.sin(u * angle) * q1) / np.sin(angle)))
    return np.array(out)


Aligner = Callable[[dict[str, np.ndarray]], dict[str, np.ndarray]]


def refine_priors(
    rig: Rig,
    antenna: dict[str, tuple[int, np.ndarray]],
    align: Aligner,
    tolerance_m: float = 1e-3,
    max_iterations: int = 5,
) -> tuple[dict[str, np.ndarray], int]:
    """Camera-centre priors per image. Starts from the antenna positions (off by at most the lever arm),
    then repeatedly predicts each camera centre from the aligned camera rotation until the priors settle.
    align(priors) returns world_from_camera rotations for the images it was given."""
    priors = {name: np.asarray(a, float) for name, (_, a) in antenna.items()}
    for iteration in range(1, max_iterations + 1):
        world_from_camera = align(priors)
        updated = {
            name: rig.camera_centre(cam, a, world_from_camera[name] @ rig.rig_from_camera(cam).T)
            for name, (cam, a) in antenna.items()
        }
        change = max(np.linalg.norm(updated[n] - priors[n]) for n in priors)
        priors = updated
        if change < tolerance_m:
            return priors, iteration
    raise RigError(
        f"camera priors did not settle within {max_iterations} iterations (last change {change:.4f} m)"
    )
