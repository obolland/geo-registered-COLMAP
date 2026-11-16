"""Rider path: one rig pose per capture pulse, then the road contact, smoothed path and profile."""

from dataclasses import dataclass

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from georoute.geodesy import LocalFrame
from georoute.rig import Rig

# The calibrated rig predicts each camera's position from the others; a pulse where they disagree by more than
# this is a bad reconstruction, not rider motion.
MAX_CAMERA_DISAGREEMENT_M = 0.05


class PathError(ValueError):
    pass


@dataclass(frozen=True)
class RigPose:
    """The rig reference point and attitude at one capture pulse, in the session frame and in ECEF."""

    pulse: int
    t_gps_s: float
    origin_ecef: np.ndarray
    world_from_rig: np.ndarray
    cameras: tuple[int, ...]
    bridged: bool


def rig_poses(poses, images, rig: Rig, frame: LocalFrame) -> list[RigPose]:
    """One rig pose per capture pulse. Each camera's pose gives the rig origin through the calibrated rig
    geometry; the origins are combined by a componentwise median and the rotations by their chordal mean.
    A pulse with no registered camera is interpolated between the nearest registered pulses in time and
    flagged; a pulse outside the registered range can't be bridged and refuses."""
    pulse_times = _pulse_times(images)
    pulses = sorted(pulse_times)
    by_pulse = _camera_poses(poses, rig, pulse_times)
    known = {}
    for pulse in pulses:
        cameras = by_pulse.get(pulse)
        if not cameras:
            continue
        origins = np.array([origin for _, _, origin in cameras])
        centre = np.median(origins, axis=0)
        spread = np.linalg.norm(origins[:, None, :] - origins[None, :, :], axis=2).max()
        if spread > MAX_CAMERA_DISAGREEMENT_M:
            raise PathError(
                f"pulse {pulse}: rig cameras disagree by {spread * 100:.1f} cm "
                f"(limit {MAX_CAMERA_DISAGREEMENT_M * 100:g} cm)"
            )
        rotations = np.array([rotation for _, rotation, _ in cameras])
        known[pulse] = (centre, _mean_rotation(rotations), tuple(c for c, _, _ in cameras))
    if not known:
        raise PathError("no pulse has a registered camera, so there's no rig pose to build a path from")
    known_pulses = sorted(known)
    times = np.array([pulse_times[p] for p in known_pulses])
    origins = np.array([known[p][0] for p in known_pulses])
    rotations = Rotation.from_matrix([known[p][1] for p in known_pulses])
    slerp = Slerp(times, rotations) if len(known_pulses) > 1 else None

    result = []
    for pulse in pulses:
        t = pulse_times[pulse]
        if pulse in known:
            centre, rotation, cameras = known[pulse]
            bridged = False
        else:
            if not times[0] <= t <= times[-1]:
                raise PathError(
                    f"pulse {pulse} has no pose on both sides: the nearest registered pulses are "
                    f"{known_pulses[0]} and {known_pulses[-1]}"
                )
            centre = np.array([np.interp(t, times, origins[:, axis]) for axis in range(3)])
            rotation = slerp(t).as_matrix()
            cameras = ()
            bridged = True
        result.append(RigPose(pulse, float(t), frame.to_ecef(centre), rotation, cameras, bridged))
    return result


def _pulse_times(images) -> dict[int, float]:
    times: dict[int, list[float]] = {}
    for image in images:
        times.setdefault(image["pulse"], []).append(float(image["t_mid_gps_s"]))
    return {pulse: float(np.median(t)) for pulse, t in times.items()}


def _camera_poses(poses, rig: Rig, pulse_times: dict[int, float]):
    by_pulse: dict[int, list[tuple[int, np.ndarray, np.ndarray]]] = {}
    for (pulse, camera), (world_from_camera, centre) in poses.items():
        if pulse not in pulse_times:
            continue
        world_from_rig = world_from_camera @ rig.rig_from_camera(camera).T
        origin = np.asarray(centre, float) - world_from_rig @ rig.camera(camera).position_in_rig_m
        by_pulse.setdefault(pulse, []).append((camera, world_from_rig, origin))
    for cameras in by_pulse.values():
        cameras.sort()
    return by_pulse


def _mean_rotation(rotations: np.ndarray) -> np.ndarray:
    """The closest rotation to the arithmetic mean of the matrices. A reflection can appear when the inputs
    are far apart, so the smallest singular vector is flipped to keep det +1."""
    if len(rotations) == 1:
        return rotations[0].copy()
    u, _, vt = np.linalg.svd(rotations.sum(axis=0))
    mean = u @ vt
    if np.linalg.det(mean) < 0:
        u[:, -1] *= -1
        mean = u @ vt
    return mean
