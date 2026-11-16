"""Rider path: one rig pose per capture pulse, then the road contact, smoothed path and profile."""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation, Slerp

from georoute.geodesy import LocalFrame, ecef_from_enu_rotation, ecef_to_lla
from georoute.rig import Rig

# The calibrated rig predicts each camera's position from the others; a pulse where they disagree by more than
# this is a bad reconstruction, not rider motion.
MAX_CAMERA_DISAGREEMENT_M = 0.05
# The road plane under the rig is fitted to the reconstruction's points. A 2 m disc has at least 72 road
# points at every pulse of field test 2; below 20 there are too few to fit a plane, so the nominal rig
# height is used instead.
CONTACT_RADIUS_M = 2.0
CONTACT_HEIGHT_WINDOW_M = 0.3
MIN_CONTACT_POINTS = 20
# Used when the surface can't be fitted. The rig's 1.18 m was measured by hand to about a centimetre; the rest
# covers the road surface varying under the reference point.
NOMINAL_SURFACE_SIGMA_M = 0.05
# SfM road points scatter by a few centimetres; the robust loss treats larger departures as kerbs and clutter.
ROBUST_SCALE_M = 0.02


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


@dataclass(frozen=True)
class RoadContact:
    """Where the rig reference point meets the reconstructed road, and how well the surface fitted."""

    pulse: int
    t_gps_s: float
    ecef: np.ndarray
    sigma_m: float
    height_above_road_m: float
    points: int
    nominal: bool


def road_contacts(poses, points, rig: Rig, frame: LocalFrame) -> list[RoadContact]:
    """The road contact under each rig pose, in the session frame. A robust plane is fitted to the SfM points
    in a disc around the rig, within a height window around the nominal road height, and the rig origin is
    dropped along the local geodetic normal onto it. Sparse discs use the nominal rig height and are flagged;
    the geodetic normal, not the frame's up axis, keeps the contact under the rig over a long build."""
    points = np.asarray(points, float)
    tree = cKDTree(points[:, :2])
    contacts = []
    for pose in poses:
        origin = frame.from_ecef(pose.origin_ecef)
        lat, lon, _ = ecef_to_lla(pose.origin_ecef)
        up = ecef_from_enu_rotation(lat, lon)[:, 2] @ frame.ecef_from_enu
        up /= np.linalg.norm(up)
        expected = origin[2] - rig.reference_height_above_road_m
        near = np.array(tree.query_ball_point(origin[:2], CONTACT_RADIUS_M + 1e-9), dtype=int)
        near = near[np.abs(points[near, 2] - expected) <= CONTACT_HEIGHT_WINDOW_M]
        if len(near) < MIN_CONTACT_POINTS:
            contact = origin - up * rig.reference_height_above_road_m
            sigma = NOMINAL_SURFACE_SIGMA_M
            height = rig.reference_height_above_road_m
            nominal = True
        else:
            contact, height, sigma = _fit_contact(points[near], origin, up, pose.pulse)
            nominal = False
        contacts.append(
            RoadContact(pose.pulse, pose.t_gps_s, frame.to_ecef(contact), sigma, height, len(near), nominal)
        )
    return contacts


def _fit_contact(points, origin, up, pulse: int):
    """Robust plane z = a de + b dn + c around the origin, then the intersection with the geodetic normal.
    The prediction variance at that intersection is the contact's 1-sigma."""
    offset = points[:, :2] - origin[:2]
    design = np.column_stack([offset, np.ones(len(points))])
    heights = points[:, 2]
    start = np.linalg.lstsq(design, heights, rcond=None)[0]
    fit = least_squares(
        lambda p: design @ p - heights, start, loss="soft_l1", f_scale=ROBUST_SCALE_M, max_nfev=200
    )
    coefficients = fit.x
    normal = np.array([-coefficients[0], -coefficients[1], 1.0])
    normal /= np.linalg.norm(normal)
    denominator = normal @ up
    if abs(denominator) < 1e-6:
        raise PathError(f"pulse {pulse}: the road plane is perpendicular to local gravity")
    t = normal[2] * (coefficients[2] - origin[2]) / denominator
    contact = origin + t * up
    height = -t
    if not height > 0:
        raise PathError(
            f"pulse {pulse}: the fitted road surface is {height:.2f} m above the rig reference point"
        )
    residuals = design @ coefficients - heights
    scale = 1.4826 * np.median(np.abs(residuals - np.median(residuals)))
    if scale == 0:
        sigma = 0.0
    else:
        row = np.array([contact[0] - origin[0], contact[1] - origin[1], 1.0])
        covariance = scale**2 * np.linalg.inv(design.T @ design)
        sigma = float(np.sqrt(max(row @ covariance @ row, 0.0)))
    return contact, float(height), sigma


def height_check(contacts, rig: Rig) -> dict:
    """The measured height above the road, against the rig's hand measurement. This is the check that the
    surface fit and the rig calibration agree, not a gate: a bias here would move every path sample."""
    measured = np.array([c.height_above_road_m for c in contacts if not c.nominal])
    result = {
        "expected_m": rig.reference_height_above_road_m,
        "measured_pulses": int(len(measured)),
        "nominal_pulses": int(sum(c.nominal for c in contacts)),
    }
    if len(measured):
        p5, p50, p95 = np.percentile(measured, [5, 50, 95])
        result.update(p5_m=float(p5), p50_m=float(p50), p95_m=float(p95))
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
