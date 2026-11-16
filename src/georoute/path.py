"""Rider path: one rig pose per capture pulse, then the road contact, smoothed path and profile."""

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import UnivariateSpline
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
    bridged: bool = False


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
            RoadContact(
                pose.pulse,
                pose.t_gps_s,
                frame.to_ecef(contact),
                sigma,
                height,
                len(near),
                nominal,
                pose.bridged,
            )
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


# The path is published at 0.5 m and the profile at 1 m, but it is sampled this finely so heading, curvature
# and grade are interpolated from a smooth curve rather than from the publication grid.
DENSE_STEP_M = 0.1
# Contacts closer than this along the trace are a stop: averaging them collapses the stop without inventing
# motion or a zero-length spline interval.
MIN_TRACE_SPACING_M = 0.01
# The smoothed path may not depart from the reconstructed road trace by more than this, laterally or
# vertically; the strongest smoothing that respects it is used.
SMOOTHING_BOUND_M = 0.1
MAX_SMOOTHING_S = 1e12


@dataclass(frozen=True)
class RiderPath:
    """The smoothed rider path, sampled densely along its own 3D arclength. Heading is the tangent direction
    counter-clockwise from local east; curvature is signed, positive when the path turns left. The raw road
    trace is kept for the registration and path-to-road checks."""

    s_m: np.ndarray
    enu_m: np.ndarray
    heading_rad: np.ndarray
    curvature_1pm: np.ndarray
    sigma_m: np.ndarray
    trace_s_m: np.ndarray
    trace_enu_m: np.ndarray
    trace_sigma_m: np.ndarray
    trace_pulses: np.ndarray
    trace_bridged: np.ndarray
    trace_nominal: np.ndarray
    smoothing: dict

    @property
    def length_m(self) -> float:
        return float(self.s_m[-1])

    def at(self, s):
        """Interpolates the path at route-global 3D distances. Returns ENU, heading, curvature and sigma."""
        s = np.atleast_1d(np.asarray(s, float))
        if np.any(s < -1e-9) or np.any(s > self.length_m + 1e-9):
            raise PathError(
                f"asked for a point at s={s.min():.3f} to {s.max():.3f} m, outside 0 to {self.length_m:.3f} m"
            )
        s = np.clip(s, 0.0, self.length_m)
        enu = np.column_stack([np.interp(s, self.s_m, self.enu_m[:, axis]) for axis in range(3)])
        heading = np.interp(s, self.s_m, np.unwrap(self.heading_rad))
        heading = (heading + np.pi) % (2 * np.pi) - np.pi
        curvature = np.interp(s, self.s_m, self.curvature_1pm)
        sigma = np.interp(s, self.s_m, self.sigma_m)
        return enu, heading, curvature, sigma


def rider_path(contacts, frame: LocalFrame, bound_m: float = SMOOTHING_BOUND_M) -> RiderPath:
    """The road-contact trace reparameterised by 3D distance, then smoothed in east, north and up by cubic
    smoothing splines. The smoothing is the strongest that keeps the 95th percentile of the lateral and
    vertical departures from the trace under bound_m. Capture weave is a 0.75-3 m wavelength; replaying it at
    riding speed would be visible and nauseating, so the bound, not the raw trace, sets the limit."""
    if len(contacts) < 4:
        raise PathError(f"need at least 4 road contacts to fit a rider path, got {len(contacts)}")
    enu = np.array([frame.from_ecef(c.ecef) for c in contacts])
    sigma = np.array([c.sigma_m for c in contacts])
    distance = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(enu, axis=0), axis=1))])
    distance, enu, sigma, groups = _collapse_stops(distance, enu, sigma)
    if len(distance) < 4:
        raise PathError(
            f"the trace has only {len(distance)} distinct positions, need at least 4 to fit a rider path"
        )
    pulses = np.array([contacts[group[0]].pulse for group in groups])
    bridged = np.array([any(contacts[i].bridged for i in group) for group in groups])
    nominal = np.array([any(contacts[i].nominal for i in group) for group in groups])
    splines, smoothing = _smooth_to_bound(distance, enu, bound_m)
    dense_distance, dense_s, enu_dense, heading, curvature = _sample_path(splines, distance, enu)
    keep = np.concatenate([[True], np.diff(dense_s) > 0])
    dense_distance, dense_s = dense_distance[keep], dense_s[keep]
    enu_dense, heading, curvature = enu_dense[keep], heading[keep], curvature[keep]
    sigma_dense = np.interp(dense_distance, distance, sigma)
    return RiderPath(
        s_m=dense_s,
        enu_m=enu_dense,
        heading_rad=heading,
        curvature_1pm=curvature,
        sigma_m=sigma_dense,
        trace_s_m=distance,
        trace_enu_m=enu,
        trace_sigma_m=sigma,
        trace_pulses=pulses,
        trace_bridged=bridged,
        trace_nominal=nominal,
        smoothing=smoothing,
    )


def _collapse_stops(distance, enu, sigma):
    """Averages consecutive contacts that moved less than MIN_TRACE_SPACING_M, so a stop becomes one point."""
    groups = []
    start = 0
    for index in range(1, len(distance)):
        if distance[index] - distance[start] >= MIN_TRACE_SPACING_M:
            groups.append(np.arange(start, index))
            start = index
    groups.append(np.arange(start, len(distance)))
    collapsed_d = np.array([distance[group].mean() for group in groups])
    collapsed_enu = np.array([enu[group].mean(axis=0) for group in groups])
    collapsed_sigma = np.array([sigma[group].mean() for group in groups])
    return collapsed_d, collapsed_enu, collapsed_sigma, groups


def _smooth_to_bound(distance, enu, bound_m):
    def fit(parameter):
        return [UnivariateSpline(distance, enu[:, axis], k=3, s=parameter) for axis in range(3)]

    def deviations(splines):
        fitted = np.column_stack([spline(distance) for spline in splines])
        de = splines[0].derivative()(distance)
        dn = splines[1].derivative()(distance)
        norm = np.hypot(de, dn)
        delta = fitted[:, :2] - enu[:, :2]
        lateral = np.where(norm > 0, np.abs(delta[:, 0] * dn - delta[:, 1] * de) / norm, np.hypot(*delta.T))
        vertical = np.abs(fitted[:, 2] - enu[:, 2])
        return float(np.percentile(lateral, 95)), float(np.percentile(vertical, 95))

    def acceptable(parameter):
        try:
            splines = fit(parameter)
            lateral, vertical = deviations(splines)
        except Exception:
            return False, None, None, None
        return lateral <= bound_m and vertical <= bound_m, splines, lateral, vertical

    best = fit(0.0)
    low = 0.0
    high = 1e-3
    for _ in range(60):
        ok, splines, lateral, vertical = acceptable(high)
        if not ok:
            break
        best, low = splines, high
        high *= 4.0
        if high > MAX_SMOOTHING_S:
            break
    for _ in range(50):
        middle = np.sqrt(low * high) if low > 0 else high / 2
        ok, splines, lateral, vertical = acceptable(middle)
        if ok:
            best, low = splines, middle
        else:
            high = middle
        if high / low < 1.01 and low > 0:
            break
    lateral, vertical = deviations(best)
    return best, {
        "method": "cubic-smoothing-spline",
        "parameter_s": float(low),
        "bound_m": float(bound_m),
        "p95_lateral_m": lateral,
        "p95_vertical_m": vertical,
        "trace_points": int(len(distance)),
        "min_trace_spacing_m": MIN_TRACE_SPACING_M,
        "dense_step_m": DENSE_STEP_M,
    }


def _sample_path(splines, distance, enu):
    count = max(2, int(np.ceil(distance[-1] / DENSE_STEP_M)) + 1)
    dense_distance = np.linspace(distance[0], distance[-1], count)
    enu_dense = np.column_stack([spline(dense_distance) for spline in splines])
    ds = np.linalg.norm(np.diff(enu_dense, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(ds)])
    de = splines[0].derivative()(dense_distance)
    dn = splines[1].derivative()(dense_distance)
    heading = (np.arctan2(dn, de) + np.pi) % (2 * np.pi) - np.pi
    d2e = splines[0].derivative(2)(dense_distance)
    d2n = splines[1].derivative(2)(dense_distance)
    speed = np.maximum(np.hypot(de, dn) ** 3, 1e-12)
    curvature = (de * d2n - dn * d2e) / speed
    return dense_distance, s, enu_dense, heading, curvature


PROFILE_STEP_M = 1.0
# Grade is rise over horizontal run, smoothed over about 10 m. It drives virtual speed, so it is computed
# once here and published, never retuned by the app.
GRADE_WINDOW_M = 10.0
# One capture pass supports about a metre either side until the lateral pass is analysed.
CORRIDOR_M = 1.0
DEFAULT_SURFACE = "asphalt"


@dataclass(frozen=True)
class DistanceProfile:
    """The published primitives at 1 m of route-global 3D distance. Heights are orthometric, grade is percent
    of rise over horizontal run, and quality lists the reasons a sample is less trustworthy."""

    s_m: np.ndarray
    s_h_m: np.ndarray
    lat_deg: np.ndarray
    lon_deg: np.ndarray
    h_ortho_m: np.ndarray
    sigma_h_m: np.ndarray
    grade_percent: np.ndarray
    curvature_1pm: np.ndarray
    corridor_left_m: np.ndarray
    corridor_right_m: np.ndarray
    surface: tuple[str, ...]
    quality: tuple[tuple[str, ...], ...]


def distance_profile(path: RiderPath, images, frame: LocalFrame, geoid) -> DistanceProfile:
    """The 1 m profile. The path is evaluated at whole metres, plus the endpoint when the build is not a
    whole number of metres. Heights come from each sample's own ECEF position and the geoid, never from a
    frame's up axis; grade uses horizontal distance and the orthometric heights."""
    whole = int(np.floor(path.length_m / PROFILE_STEP_M + 1e-9))
    s = np.arange(whole + 1, dtype=float) * PROFILE_STEP_M
    if s[-1] < path.length_m - 1e-9:
        s = np.append(s, path.length_m)
    enu, _, curvature, sigma = path.at(s)
    s_h = np.interp(s, path.s_m, _horizontal_distance(path))
    ecef = frame.to_ecef(enu)
    lla = ecef_to_lla(ecef)
    orthometric = np.asarray(geoid.orthometric_height(lla[:, 0], lla[:, 1], lla[:, 2]), float)
    grade = _grade(s_h, orthometric)
    flags = _image_quality(images)
    nearest = _nearest(path.trace_s_m, s)
    quality = []
    for index in nearest:
        sample = set(flags.get(int(path.trace_pulses[index]), ()))
        if path.trace_bridged[index]:
            sample.add("bridged-gap")
        if path.trace_nominal[index]:
            sample.add("surface-nominal")
        quality.append(tuple(sorted(sample)))
    count = len(s)
    return DistanceProfile(
        s,
        s_h,
        lla[:, 0].copy(),
        lla[:, 1].copy(),
        orthometric,
        sigma,
        grade,
        curvature,
        np.full(count, CORRIDOR_M),
        np.full(count, CORRIDOR_M),
        (DEFAULT_SURFACE,) * count,
        tuple(quality),
    )


def _horizontal_distance(path: RiderPath) -> np.ndarray:
    step = np.diff(path.enu_m[:, :2], axis=0)
    return np.concatenate([[0.0], np.cumsum(np.hypot(step[:, 0], step[:, 1]))])


def _grade(s_h, height):
    """Least-squares rise over run in a window around each sample, shrinking at the ends. Taken over
    horizontal distance so a climb's grade isn't diluted by the extra 3D path length."""
    grade = np.empty(len(height))
    half = GRADE_WINDOW_M / 2
    for index, x in enumerate(s_h):
        low = np.searchsorted(s_h, x - half, side="left")
        high = np.searchsorted(s_h, x + half, side="right")
        if high - low < 2:
            raise PathError(f"profile at s={index} has too few samples to measure grade")
        design = np.column_stack([s_h[low:high] - x, np.ones(high - low)])
        slope = np.linalg.lstsq(design, height[low:high] - height[index], rcond=None)[0][0]
        grade[index] = 100.0 * slope
    return grade


def _image_quality(images) -> dict[int, tuple[str, ...]]:
    """Worst RTK solution and any time recovery at each capture pulse, from the ingest record."""
    by_pulse: dict[int, dict] = {}
    for image in images:
        state = by_pulse.setdefault(int(image["pulse"]), {"carr": 2, "time": False})
        state["carr"] = min(state["carr"], int(image["carr_soln"]))
        state["time"] = state["time"] or "time-recovered" in image.get("flags", ())
    result = {}
    for pulse, state in by_pulse.items():
        flags = []
        if state["carr"] == 1:
            flags.append("rtk-float")
        elif state["carr"] != 2:
            flags.append("rtk-none")
        if state["time"]:
            flags.append("time-recovered")
        result[pulse] = tuple(sorted(flags))
    return result


def _nearest(trace_s, s):
    index = np.searchsorted(trace_s, s)
    left = np.clip(index - 1, 0, len(trace_s) - 1)
    right = np.clip(index, 0, len(trace_s) - 1)
    return np.where(np.abs(trace_s[left] - s) <= np.abs(trace_s[right] - s), left, right)


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
