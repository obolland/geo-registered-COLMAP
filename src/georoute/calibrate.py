"""Measures each camera's orientation on the rig from a reconstruction, relative to a reference camera,
and writes a calibrated rig config that keeps the hand measurements alongside."""

import json
import tomllib
from pathlib import Path

import numpy as np

from georoute.rig import _RIG_FROM_CAMERA_LEVEL, Rig

# A calibrated angle this far from the hand measurement points to a mix-up, not a measuring error.
MAX_DISAGREEMENT_DEG = 15.0
MIN_PULSES = 10
# A rigid mount varies by hundredths of a degree (field test 2: 0.03-0.04); more means it moved.
MAX_SPREAD_DEG = 1.0


class CalibrationError(ValueError):
    pass


def yaw_pitch_roll(rig_from_camera: np.ndarray) -> tuple[float, float, float]:
    """Inverse of rig.rig_from_camera: the yaw, pitch and roll (degrees) that produce this rotation."""
    m = rig_from_camera @ _RIG_FROM_CAMERA_LEVEL.T
    pitch = np.arcsin(np.clip(m[2, 1], -1.0, 1.0))
    roll = np.arctan2(-m[2, 0], m[2, 2])
    yaw = np.arctan2(-m[0, 1], m[1, 1])
    return tuple(float(np.degrees(a)) for a in (yaw, pitch, roll))


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip((np.trace(a.T @ b) - 1) / 2, -1.0, 1.0))))


def _mean_rotation(rotations: list[np.ndarray]) -> np.ndarray:
    u, _, vt = np.linalg.svd(np.sum(rotations, axis=0))
    d = np.diag([1.0, 1.0, np.sign(np.linalg.det(u @ vt))])
    return u @ d @ vt


def calibrate_rotations(
    poses: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]], rig: Rig, reference: int
) -> dict[str, dict]:
    """Each camera's rig_from_camera from the reconstruction, pulse by pulse relative to the reference
    camera, which keeps its configured orientation (it defines the rig's axes)."""
    if not any(c == reference for _, c in poses):
        raise CalibrationError(f"no registered images from the reference camera cam{reference}")
    rig_from_ref = rig.rig_from_camera(reference)
    out = {
        f"cam{reference}": {
            "yaw_pitch_roll_deg": rig.camera(reference).yaw_pitch_roll_deg,
            "pulses": 0,
            "spread_deg": 0.0,
        }
    }
    for camera in sorted({c for _, c in poses} - {reference}):
        samples = [
            rig_from_ref @ poses[(p, reference)][0].T @ r
            for (p, c), (r, _) in poses.items()
            if c == camera and (p, reference) in poses
        ]
        if len(samples) < MIN_PULSES:
            raise CalibrationError(
                f"cam{camera} shares only {len(samples)} pulses with the reference, need {MIN_PULSES}"
            )
        mean = _mean_rotation(samples)
        disagreement = _angle_deg(mean, rig.rig_from_camera(camera))
        if disagreement > MAX_DISAGREEMENT_DEG:
            raise CalibrationError(f"cam{camera} is {disagreement:.1f} degrees from its hand measurement")
        spread = float(np.median([_angle_deg(mean, s) for s in samples]))
        if spread > MAX_SPREAD_DEG:
            raise CalibrationError(f"cam{camera}'s orientation varies by {spread:.2f} degrees across pulses")
        out[f"cam{camera}"] = {
            "yaw_pitch_roll_deg": yaw_pitch_roll(mean),
            "pulses": len(samples),
            "spread_deg": spread,
            "change_from_hand_deg": disagreement,
        }
    return out


def write_calibrated_rig(
    source_path, out_path, calibration: dict[str, dict], reference: int, axial_offset_m: float, source: str
):
    """A copy of the rig config with calibrated orientations, and positions moved from the lens front to
    the optical centre, the hand measurements kept as hand_* keys."""
    from georoute.rig import rig_from_camera

    doc = tomllib.loads(Path(source_path).read_text())
    lines = [
        f"# Calibrated from {source}: orientations relative to cam{reference}, positions moved "
        f"{axial_offset_m * 1000:.0f} mm",
        "# behind the lens front to the optical centre. hand_* keys are the original measurements.",
        "",
        f'schema = "{doc["schema"]}"',
        f"name = {_toml_value(doc['name'].removesuffix('-calibrated') + '-calibrated')}",
        f"measured = {str(doc['measured']).lower()}",
        "",
    ]
    for table in ("reference", "antenna", "timing"):
        lines.append(f"[{table}]")
        lines += [f"{k} = {_toml_value(v)}" for k, v in doc[table].items()]
        lines.append("")
    lines += [
        "[calibration]",
        f"source = {_toml_value(source)}",
        f"reference = {reference}",
        f"axial_offset_m = {axial_offset_m:.4f}",
        "",
    ]
    for cam in doc["camera"]:
        found = calibration.get(f"cam{cam['index']}")
        angles = found["yaw_pitch_roll_deg"] if found else tuple(cam["yaw_pitch_roll_deg"])
        axis = rig_from_camera(*angles)[:, 2]
        position = np.array(cam["position_in_rig_m"]) - axial_offset_m * axis
        lines += ["[[camera]]"] + [
            f"{k} = {_toml_value(cam[k])}" for k in ("index", "role", "folder", "serial") if k in cam
        ]
        lines += [
            f"position_in_rig_m = {_toml_value([round(float(x), 4) for x in position])}",
            f"yaw_pitch_roll_deg = {_toml_value([round(float(x), 2) for x in angles])}",
            f"hand_position_in_rig_m = "
            f"{_toml_value(cam.get('hand_position_in_rig_m', cam['position_in_rig_m']))}",
            f"hand_yaw_pitch_roll_deg = "
            f"{_toml_value(cam.get('hand_yaw_pitch_roll_deg', cam['yaw_pitch_roll_deg']))}",
            "",
        ]
    Path(out_path).write_text("\n".join(lines))


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return repr(value)
