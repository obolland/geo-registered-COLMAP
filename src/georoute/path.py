"""Rider path: one rig pose per capture pulse, then the road contact, smoothed path and profile."""

import warnings
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import UnivariateSpline
from scipy.optimize import least_squares
from scipy.signal import find_peaks
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
        spread = np.linalg.norm(origins - centre, axis=1).max()
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
# Horizontal smoothing is comfort-first: start from the old registration-bound fit, then around each
# comfort violation re-target a local window to the raw trace with lowered spline weights while the rest of
# the route stays anchored to the current fit. The fit's s stays fixed. Yaw acceleration must reach at most
# 30 deg/s^2 under a reachable speed profile, with a hard 0.5 m departure cap. Vertical stays a 3 cm p95
# bound because it is only fit noise, not rider motion.
HORIZONTAL_INITIAL_P95_M = 0.10
HORIZONTAL_MAX_DEPARTURE_M = 0.50
VERTICAL_SMOOTHING_BOUND_M = 0.03
COMFORT_WINDOW_M = 22.0
COMFORT_MARGIN_M = 10.0
COMFORT_WEIGHT_FACTORS = (0.5, 0.2, 0.1, 0.05, 0.02)
COMFORT_ANCHOR_WEIGHT = 10.0
MAX_COMFORT_ITERATIONS = 500
DESIGN_SPEED_MPS = 18.0
LATERAL_ACCEL_LIMIT_MPS2 = 3.0
LONGITUDINAL_ACCEL_LIMIT_MPS2 = 1.5
COMFORT_YAW_ACCEL_LIMIT_DEG_S2 = 30.0
MAX_SMOOTHING_S = 1e12


def speed_profile(s, curvature):
    """A reachable speed: capped by the lateral acceleration limit in bends, then limited forward and
    backward so v^2 changes by at most 2*a_long*ds per step."""
    limit = np.full_like(curvature, DESIGN_SPEED_MPS)
    bend = np.abs(curvature) > 1e-12
    limit[bend] = np.minimum(DESIGN_SPEED_MPS, np.sqrt(LATERAL_ACCEL_LIMIT_MPS2 / np.abs(curvature[bend])))
    squared = limit**2
    spacing = np.diff(s)
    for index in range(1, len(squared)):
        squared[index] = min(
            squared[index], squared[index - 1] + 2 * LONGITUDINAL_ACCEL_LIMIT_MPS2 * spacing[index - 1]
        )
    for index in range(len(squared) - 2, -1, -1):
        squared[index] = min(
            squared[index], squared[index + 1] + 2 * LONGITUDINAL_ACCEL_LIMIT_MPS2 * spacing[index]
        )
    return np.sqrt(squared)


def yaw_acceleration(s, curvature):
    """Yaw acceleration on the reachable speed profile, in degrees/s^2: the time derivative of v*kappa."""
    speed = speed_profile(s, curvature)
    dcurvature = np.gradient(curvature, s)
    dspeed = np.gradient(speed, s)
    acceleration = speed * (speed * dcurvature + curvature * dspeed)
    return np.degrees(np.abs(acceleration))


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
    # Raw trace distance is the smoothing spline's parameter. trace_smoothed_m is where each trace sample
    # sits on the published route s, which is shorter when smoothing removes weave. Anything placing trace
    # samples against segments, profile samples or RTK runs uses trace_smoothed_m, never the parameter.
    trace_parameter_m: np.ndarray
    trace_smoothed_m: np.ndarray
    trace_enu_m: np.ndarray
    trace_base_fit_enu_m: np.ndarray
    trace_fit_enu_m: np.ndarray
    trace_heading_rad: np.ndarray
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


def rider_path(
    contacts,
    frame: LocalFrame,
    vertical_bound_m: float = VERTICAL_SMOOTHING_BOUND_M,
) -> RiderPath:
    """The road-contact trace reparameterised by 3D distance, then smoothed in east, north and up by cubic
    smoothing splines. Vertical keeps a 3 cm p95 bound. Horizontal starts from the old 10 cm registration
    fit, then locally re-targets windows around comfort violations to the raw trace with lowered weights,
    anchored outside to the current fit, until yaw acceleration is at most 30 deg/s^2 or the 0.5 m
    departure cap would be exceeded. Real captures include pull-overs and junctions, so the path absorbs
    those locally rather than over-smoothing the whole route."""
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
    splines, smoothing, base_fit = _smooth_to_bound(distance, enu, vertical_bound_m)
    dense_distance, dense_s, enu_dense, heading, curvature = _sample_path(splines, distance, enu)
    keep = np.concatenate([[True], np.diff(dense_s) > 0])
    dense_distance, dense_s = dense_distance[keep], dense_s[keep]
    enu_dense, heading, curvature = enu_dense[keep], heading[keep], curvature[keep]
    sigma_dense = np.interp(dense_distance, distance, sigma)
    trace_smoothed = np.interp(distance, dense_distance, dense_s)
    trace_fit = np.column_stack([spline(distance) for spline in splines])
    trace_de = splines[0].derivative()(distance)
    trace_dn = splines[1].derivative()(distance)
    trace_heading = (np.arctan2(trace_dn, trace_de) + np.pi) % (2 * np.pi) - np.pi
    return RiderPath(
        s_m=dense_s,
        enu_m=enu_dense,
        heading_rad=heading,
        curvature_1pm=curvature,
        sigma_m=sigma_dense,
        trace_parameter_m=distance,
        trace_smoothed_m=trace_smoothed,
        trace_enu_m=enu,
        trace_base_fit_enu_m=base_fit,
        trace_fit_enu_m=trace_fit,
        trace_heading_rad=trace_heading,
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


def _smooth_to_bound(distance, enu, vertical_bound_m):
    def fit_horizontal(parameter, weights=None):
        return [UnivariateSpline(distance, enu[:, axis], k=3, s=parameter, w=weights) for axis in (0, 1)]

    def fit_vertical(parameter):
        return UnivariateSpline(distance, enu[:, 2], k=3, s=parameter)

    def lateral(splines):
        fitted = np.column_stack([spline(distance) for spline in splines])
        de = splines[0].derivative()(distance)
        dn = splines[1].derivative()(distance)
        norm = np.hypot(de, dn)
        delta = fitted - enu[:, :2]
        return np.where(norm > 0, np.abs(delta[:, 0] * dn - delta[:, 1] * de) / norm, np.hypot(*delta.T))

    def lateral_p95(splines):
        return float(np.percentile(lateral(splines), 95))

    def lateral_max(splines):
        return float(lateral(splines).max())

    def vertical_p95(spline):
        return float(np.percentile(np.abs(spline(distance) - enu[:, 2]), 95))

    def max_yaw(horizontal, vertical):
        _, dense_s, _, _, curvature = _sample_path([horizontal[0], horizontal[1], vertical], distance, enu)
        yaw = yaw_acceleration(dense_s, curvature)
        peak = int(np.argmax(yaw))
        return float(yaw[peak]), float(dense_s[peak])

    vertical, vertical_s = _largest_accepted(fit_vertical, vertical_p95, vertical_bound_m)
    vertical, vertical_warnings = _fit_recording_warnings(fit_vertical, vertical_s)

    horizontal, horizontal_s = _largest_accepted(
        lambda parameter: fit_horizontal(parameter), lateral_p95, HORIZONTAL_INITIAL_P95_M
    )
    if lateral_max(horizontal) > HORIZONTAL_MAX_DEPARTURE_M:
        horizontal, horizontal_s = _largest_accepted(
            lambda parameter: fit_horizontal(parameter), lateral_max, HORIZONTAL_MAX_DEPARTURE_M
        )
    base_fit = np.column_stack([horizontal[0](distance), horizontal[1](distance)])

    base_s = horizontal_s
    current = horizontal
    target_mask = np.zeros(len(distance), bool)
    iterations = 0
    while True:
        dense_distance, dense_s, _, _, curvature = _sample_path(
            [current[0], current[1], vertical], distance, enu
        )
        yaw = yaw_acceleration(dense_s, curvature)
        if yaw.max() <= COMFORT_YAW_ACCEL_LIMIT_DEG_S2:
            break
        peaks, _ = find_peaks(yaw, height=COMFORT_YAW_ACCEL_LIMIT_DEG_S2)
        if not len(peaks):
            peaks = np.array([int(np.argmax(yaw))])
        trace_smoothed = np.interp(distance, dense_distance, dense_s)
        for peak in peaks:
            s_peak = float(dense_s[peak])
            window = np.abs(trace_smoothed - s_peak) <= COMFORT_WINDOW_M / 2
            if not window.any():
                window[int(np.argmin(np.abs(trace_smoothed - s_peak)))] = True
            target_mask |= window
        iterations += 1
        if iterations > MAX_COMFORT_ITERATIONS:
            raise PathError(f"comfort smoothing did not settle after {MAX_COMFORT_ITERATIONS} violations")
        target_e = current[0](distance)
        target_n = current[1](distance)
        target_e[target_mask] = enu[target_mask, 0]
        target_n[target_mask] = enu[target_mask, 1]
        candidates = []
        for weight_factor in COMFORT_WEIGHT_FACTORS:
            candidate_weights = np.full(len(distance), COMFORT_ANCHOR_WEIGHT)
            candidate_weights[target_mask] = weight_factor
            candidate = [
                UnivariateSpline(distance, target_e, k=3, s=base_s, w=candidate_weights),
                UnivariateSpline(distance, target_n, k=3, s=base_s, w=candidate_weights),
            ]
            candidate_lateral = lateral_max(candidate)
            if candidate_lateral > HORIZONTAL_MAX_DEPARTURE_M:
                continue
            _, candidate_s, _, _, candidate_curvature = _sample_path(
                [candidate[0], candidate[1], vertical], distance, enu
            )
            candidate_yaw = yaw_acceleration(candidate_s, candidate_curvature)
            candidates.append((float(candidate_yaw.max()), candidate_lateral, weight_factor, candidate))
        if not candidates:
            break
        meeting = [candidate for candidate in candidates if candidate[0] <= COMFORT_YAW_ACCEL_LIMIT_DEG_S2]
        if meeting:
            current = meeting[0][3]
            break
        chosen = min(candidates, key=lambda candidate: candidate[0])
        if chosen[0] >= yaw.max() - 1e-9:
            current = chosen[3]
            break
        current = chosen[3]

    horizontal = current
    lateral_values = lateral(horizontal)
    final_yaw, final_s_at = max_yaw(horizontal, vertical)
    return (
        [horizontal[0], horizontal[1], vertical],
        {
            "method": "cubic-smoothing-spline",
            "horizontal": {
                "objective": "comfort",
                "initial_p95_target_m": HORIZONTAL_INITIAL_P95_M,
                "parameter_s": float(base_s),
                "max_departure_m": HORIZONTAL_MAX_DEPARTURE_M,
                "p95_lateral_m": float(np.percentile(lateral_values, 95)),
                "max_lateral_m": float(lateral_values.max()),
                "comfort_limit_deg_s2": COMFORT_YAW_ACCEL_LIMIT_DEG_S2,
                "comfort_met": bool(final_yaw <= COMFORT_YAW_ACCEL_LIMIT_DEG_S2),
                "max_yaw_accel_deg_s2": final_yaw,
                "s_at_max_yaw_m": final_s_at,
                "window_m": COMFORT_WINDOW_M,
                "margin_m": COMFORT_MARGIN_M,
                "weight_factors": list(COMFORT_WEIGHT_FACTORS),
                "iterations": iterations,
            },
            "vertical": {
                "parameter_s": float(vertical_s),
                "bound_m": float(vertical_bound_m),
                "p95_vertical_m": vertical_p95(vertical),
            },
            "p95_lateral_m": float(np.percentile(lateral_values, 95)),
            "p95_vertical_m": vertical_p95(vertical),
            "warnings": sorted(set(vertical_warnings)),
            "trace_points": int(len(distance)),
            "min_trace_spacing_m": MIN_TRACE_SPACING_M,
            "dense_step_m": DENSE_STEP_M,
        },
        base_fit,
    )


def _largest_accepted(fit, metric, bound):
    """Largest smoothing parameter whose p95 metric stays under the bound. FITPACK rejects some very large
    parameters with ValueError/RuntimeError; those are search failures, not reasons to hide other errors."""

    def acceptable(parameter):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = fit(parameter)
            value = metric(model)
        except (ValueError, RuntimeError):
            return False, None, None
        return value <= bound, model, value

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        best = fit(0.0)
    low = 0.0
    high = 1e-3
    for _ in range(60):
        ok, model, _ = acceptable(high)
        if not ok:
            break
        best, low = model, high
        high *= 4.0
        if high > MAX_SMOOTHING_S:
            break
    for _ in range(50):
        middle = np.sqrt(low * high) if low > 0 else high / 2
        ok, model, _ = acceptable(middle)
        if ok:
            best, low = model, middle
        else:
            high = middle
        if low > 0 and high / low < 1.01:
            break
    return best, low


def _fit_recording_warnings(fit, parameter):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = fit(parameter)
    return model, [str(warning.message) for warning in caught]


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
    # Trace samples are denser than the 1 m profile and map many-to-one. Union their flags onto the nearest
    # profile sample rather than sampling the nearest trace per profile point, which would drop flagged
    # pulses that fall between profile samples.
    quality = [set() for _ in s]
    for trace_index, target in enumerate(_nearest(s, path.trace_smoothed_m)):
        sample = quality[int(target)]
        sample.update(flags.get(int(path.trace_pulses[trace_index]), ()))
        if path.trace_bridged[trace_index]:
            sample.add("bridged-gap")
        if path.trace_nominal[trace_index]:
            sample.add("surface-nominal")
    quality = tuple(tuple(sorted(sample)) for sample in quality)
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
            raise PathError(f"profile at s={s_h[index]:.3f} m has too few samples to measure grade")
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
