"""The capture rig: camera geometry and the antenna lever arm, from a versioned TOML file.

Rig frame: x right, y forward, z up. Camera frame (COLMAP/OpenCV): x right, y down, z forward.
COLMAP can't model the antenna, so camera position priors are derived here:
    c = a + R_world_from_rig (t_camera_in_rig - l_antenna_in_rig)
"""

import hashlib
import math
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
        for c in self.cameras:
            if c.index == index:
                return c
        raise RigError(f"rig {self.name} has no camera {index}")

    def rig_from_camera(self, index: int) -> np.ndarray:
        return rig_from_camera(*self.camera(index).yaw_pitch_roll_deg)

    def camera_centre(self, index: int, antenna, world_from_rig) -> np.ndarray:
        return np.asarray(antenna) + world_from_rig @ (
            self.camera(index).position_in_rig_m - self.lever_arm_m
        )

    def reference_point(self, antenna, world_from_rig) -> np.ndarray:
        return np.asarray(antenna) - world_from_rig @ self.lever_arm_m


def load_rig(path) -> Rig:
    """The hash covers the file's bytes, so editing a comment changes it too."""
    path = Path(path)
    try:
        raw = path.read_bytes()
        doc = tomllib.loads(raw.decode())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise RigError(f"can't read rig config {path}: {e}") from e
    if doc.get("schema") != RIG_SCHEMA:
        raise RigError(f"{path.name} schema is {doc.get('schema')!r}, expected {RIG_SCHEMA}")
    _check_keys(doc, "")
    for table in ("reference", "antenna", "timing"):
        if table not in doc:
            raise RigError(f"{path.name} is missing [{table}]")
        _check_keys(doc[table], table)
    if not doc.get("camera"):
        raise RigError(f"{path.name} has no cameras")
    cameras = []
    for entry in doc["camera"]:
        _check_keys(entry, "camera")
        cameras.append(_camera(entry))
    _check_unique(cameras)
    antenna = doc["antenna"]
    lever = _vector(antenna, "arp_in_rig_m", "antenna") + _vector(antenna, "phase_centre_offset_m", "antenna")
    measured = doc.get("measured", False)
    if not isinstance(measured, bool):
        raise RigError(f"measured must be true or false, got {measured!r}")
    return Rig(
        name=str(doc.get("name", path.stem)),
        measured=measured,
        cameras=tuple(sorted(cameras, key=lambda c: c.index)),
        lever_arm_m=lever,
        reference_height_above_road_m=_number(doc["reference"], "height_above_road_m", "reference", 0.0, 5.0),
        curtain_travel_s=_number(doc["timing"], "curtain_travel_ms", "timing", 0.0, 50.0) / 1000,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _check_keys(table: dict, name: str):
    unknown = set(table) - _KEYS[name]
    if unknown:
        raise RigError(f"unknown key {sorted(unknown)[0]} in {'[' + name + ']' if name else 'the top level'}")


def _is_number(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _number(table: dict, key: str, where: str, low: float, high: float) -> float:
    value = table.get(key)
    if not _is_number(value) or not low <= value <= high:
        raise RigError(f"{where} {key} must be a number from {low:g} to {high:g}, got {value!r}")
    return float(value)


def _vector(table: dict, key: str, where: str, limit: float = 10.0) -> np.ndarray:
    value = table.get(key)
    if (
        not isinstance(value, list)
        or len(value) != 3
        or not all(_is_number(v) and abs(v) <= limit for v in value)
    ):
        raise RigError(f"{where} {key} must be 3 numbers within +-{limit:g}, got {value!r}")
    return np.array(value, float)


def _camera(entry: dict) -> Camera:
    index = entry.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < CAMERA_COUNT:
        raise RigError(f"camera index {index} is outside 0..{CAMERA_COUNT - 1}")
    where = f"camera {index}"
    folder, serial = entry.get("folder", f"cam{index}"), entry.get("serial", "")
    if not isinstance(folder, str) or not folder or Path(folder).name != folder or folder in (".", ".."):
        raise RigError(f"{where} folder must be a plain folder name, got {folder!r}")
    if not isinstance(serial, str):
        raise RigError(f"{where} serial must be a quoted string (keeps leading zeros), got {serial!r}")
    yaw, pitch, roll = _vector(entry, "yaw_pitch_roll_deg", where, limit=180.0)
    return Camera(
        index,
        str(entry.get("role", "")),
        folder,
        serial or None,
        _vector(entry, "position_in_rig_m", where, limit=2.0),
        (yaw, pitch, roll),
    )


def _check_unique(cameras: list[Camera]):
    for label, values in (
        ("camera index", [c.index for c in cameras]),
        ("folder", [c.folder for c in cameras]),
        ("serial", [c.serial for c in cameras if c.serial]),
    ):
        repeated = sorted({v for v in values if values.count(v) > 1})
        if repeated:
            raise RigError(f"{label} {repeated[0]} is used by more than one camera")


Aligner = Callable[[dict[str, np.ndarray]], dict[str, np.ndarray]]


@dataclass(frozen=True)
class RefinedPriors:
    priors: dict[str, np.ndarray]
    world_from_camera: dict[str, np.ndarray]
    iterations: int
    last_change_m: float


def refine_priors(
    rig: Rig,
    antenna: dict[str, tuple[int, np.ndarray]],
    align: Aligner,
    tolerance_m: float = 1e-3,
    max_iterations: int = 5,
    min_lateral_spread_m: float = 1.0,
) -> RefinedPriors:
    """Camera-centre priors per image, each from that image's own antenna position and aligned rotation.
    Starts from the antenna positions (off by at most the lever arm), then predicts each camera centre from
    the aligned camera rotation until the priors settle. align(priors) returns world_from_camera rotations
    for the images it was given.

    On a straight ride the antenna track is a line, so rotation about it is unobservable from positions and
    the lever arm can't be applied: that refuses rather than settling on a wrong answer."""
    positions = np.array([a for _, a in antenna.values()], float)
    spread = np.linalg.svd(positions - positions.mean(0), compute_uv=False) / np.sqrt(len(positions))
    lateral = spread[1] if len(spread) > 1 else 0.0
    if lateral < min_lateral_spread_m:
        raise RigError(
            f"the antenna track is too straight to align a rig to (lateral spread {lateral:.2f} m, "
            f"need {min_lateral_spread_m} m)"
        )
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
            return RefinedPriors(priors, world_from_camera, iteration, float(change))
    raise RigError(
        f"camera priors did not settle within {max_iterations} iterations (last change {change:.4f} m)"
    )
