import warnings
from dataclasses import replace

import numpy as np
import pytest

import georoute.path as path_module
from georoute.geodesy import LocalFrame, ecef_from_enu_rotation, ecef_to_lla
from georoute.path import (
    CORRIDOR_M,
    NOMINAL_SURFACE_SIGMA_M,
    PathError,
    RigPose,
    RoadContact,
    distance_profile,
    height_check,
    rider_path,
    rig_poses,
    road_contacts,
)
from georoute.rig import Camera, Rig

FRAME = LocalFrame.at(51.0, -4.0, 100.0, "ETRS89")


def make_rig(offsets=((0, 0.0), (1, 0.2))):
    cameras = []
    for index, x in offsets:
        cameras.append(Camera(index, "", f"cam{index}", None, np.array([x, 0.0, 0.0]), (0.0, 0.0, 0.0)))
    return Rig(
        name="test",
        measured=True,
        cameras=tuple(cameras),
        lever_arm_m=np.zeros(3),
        reference_height_above_road_m=1.2,
        curtain_travel_s=0.0,
        sha256="0" * 64,
    )


def make_images(pulses, cameras=(0, 1), period_s=0.8):
    return [
        {"pulse": pulse, "camera": camera, "t_mid_gps_s": pulse * period_s, "carr_soln": 2, "flags": []}
        for pulse in pulses
        for camera in cameras
    ]


def pose(rig, camera, origin_enu, world_from_rig):
    rotation = world_from_rig @ rig.rig_from_camera(camera)
    centre = np.asarray(origin_enu, float) + world_from_rig @ rig.camera(camera).position_in_rig_m
    return rotation, centre


def rot_z(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])


def test_rig_pose_is_the_median_camera_origin_and_mean_rotation():
    rig = make_rig()
    truth = np.array([10.0, -2.0, 1.2])
    world_from_rig = rot_z(25.0)
    poses = {(7, c.index): pose(rig, c.index, truth, world_from_rig) for c in rig.cameras}

    result = rig_poses(poses, make_images([7]), rig, FRAME)

    assert len(result) == 1
    p = result[0]
    assert p.pulse == 7
    assert p.bridged is False
    assert p.cameras == (0, 1)
    np.testing.assert_allclose(p.origin_ecef, FRAME.to_ecef(truth), atol=1e-9)
    np.testing.assert_allclose(p.world_from_rig, world_from_rig, atol=1e-12)
    assert p.t_gps_s == pytest.approx(7 * 0.8)


def test_camera_disagreement_over_five_centimetres_refuses_naming_the_pulse():
    rig = make_rig()
    world_from_rig = np.eye(3)
    poses = {(5, 0): pose(rig, 0, [100.0, 0.0, 1.2], world_from_rig)}
    rotation, centre = pose(rig, 1, [100.0, 0.0, 1.2], world_from_rig)
    poses[(5, 1)] = (rotation, centre + np.array([0.0, 0.12, 0.0]))

    with pytest.raises(PathError, match="pulse 5"):
        rig_poses(poses, make_images([5]), rig, FRAME)


def test_camera_disagreement_is_measured_from_the_median_not_pairwise():
    rig = make_rig(offsets=((0, 0.0), (1, 0.0), (2, 0.06), (3, 0.06)))
    world_from_rig = np.eye(3)
    poses = {
        (5, 0): pose(rig, 0, [100.0, 0.0, 1.2], world_from_rig),
        (5, 1): pose(rig, 1, [100.0, 0.0, 1.2], world_from_rig),
        (5, 2): pose(rig, 2, [100.0, 0.06, 1.2], world_from_rig),
        (5, 3): pose(rig, 3, [100.0, 0.06, 1.2], world_from_rig),
    }

    result = rig_poses(poses, make_images([5], cameras=(0, 1, 2, 3)), rig, FRAME)

    np.testing.assert_allclose(result[0].origin_ecef, FRAME.to_ecef([100.0, 0.03, 1.2]), atol=1e-9)


def test_a_pulse_with_no_registered_camera_is_bridged_in_time_and_flagged():
    rig = make_rig(offsets=((0, 0.0),))
    poses = {
        (0, 0): pose(rig, 0, [0.0, 0.0, 1.2], np.eye(3)),
        (2, 0): pose(rig, 0, [2.0, 0.0, 1.2], np.eye(3)),
    }

    result = rig_poses(poses, make_images([0, 1, 2], cameras=(0,)), rig, FRAME)

    assert [p.bridged for p in result] == [False, True, False]
    assert result[1].cameras == ()
    np.testing.assert_allclose(result[1].origin_ecef, FRAME.to_ecef([1.0, 0.0, 1.2]), atol=1e-9)
    np.testing.assert_allclose(result[1].world_from_rig, np.eye(3), atol=1e-12)
    assert result[1].t_gps_s == pytest.approx(1 * 0.8)


def test_a_gap_is_slerped_between_the_bracketing_rotations():
    rig = make_rig(offsets=((0, 0.0),))
    poses = {
        (0, 0): pose(rig, 0, [0.0, 0.0, 0.0], np.eye(3)),
        (2, 0): pose(rig, 0, [0.0, 0.0, 0.0], rot_z(90.0)),
    }

    result = rig_poses(poses, make_images([0, 1, 2], cameras=(0,)), rig, FRAME)

    np.testing.assert_allclose(
        result[1].world_from_rig @ [1, 0, 0], [np.cos(np.pi / 4), np.sin(np.pi / 4), 0], atol=1e-9
    )


@pytest.mark.parametrize(("registered", "missing"), [((1, 2, 3), 0), ((0, 1, 2), 3)])
def test_a_gap_without_a_bracket_on_both_sides_refuses(registered, missing):
    rig = make_rig(offsets=((0, 0.0),))
    poses = {(pulse, 0): pose(rig, 0, [float(pulse), 0.0, 1.2], np.eye(3)) for pulse in registered}

    with pytest.raises(PathError, match=f"pulse {missing}.*no pose on both sides"):
        rig_poses(poses, make_images([0, 1, 2, 3], cameras=(0,)), rig, FRAME)


def test_a_pulse_with_one_camera_is_accepted():
    rig = make_rig()
    poses = {(4, 0): pose(rig, 0, [1.0, 2.0, 1.2], np.eye(3))}

    result = rig_poses(poses, make_images([4], cameras=(0, 1)), rig, FRAME)

    assert result[0].cameras == (0,)
    assert result[0].bridged is False


def rig_pose_at(pulse, enu):
    return RigPose(pulse, pulse * 0.8, FRAME.to_ecef(enu), np.eye(3), (0,), False)


def rot_x(deg):
    a = np.radians(deg)
    return np.array([[1.0, 0.0, 0.0], [0.0, np.cos(a), -np.sin(a)], [0.0, np.sin(a), np.cos(a)]])


def disc_points(centre, z, radius=2.0, spacing=0.25):
    e, n = np.meshgrid(
        np.arange(-radius, radius + spacing / 2, spacing),
        np.arange(-radius, radius + spacing / 2, spacing),
    )
    keep = np.hypot(e, n) <= radius
    e, n = e[keep] + centre[0], n[keep] + centre[1]
    return np.column_stack([e, n, z(e, n) if callable(z) else np.full(e.shape, z)])


def test_road_contact_drops_onto_a_flat_reconstructed_road():
    rig = make_rig(offsets=((0, 0.0),))
    points = disc_points((0.0, 0.0), 0.0)

    contacts = road_contacts([rig_pose_at(3, [0.0, 0.0, 1.2])], points, rig, FRAME)

    c = contacts[0]
    assert c.nominal is False
    assert c.points >= 20
    np.testing.assert_allclose(FRAME.from_ecef(c.ecef), [0.0, 0.0, 0.0], atol=1e-6)
    assert c.height_above_road_m == pytest.approx(1.2, abs=1e-6)
    assert c.sigma_m < 1e-6


def test_road_contact_follows_a_tilted_plane_and_uses_the_geodetic_up():
    rig = make_rig(offsets=((0, 0.0),))
    origin = np.array([1.0, 0.5, 1.2])
    points = disc_points(origin[:2], lambda e, n: 0.1 * e)

    c = road_contacts([rig_pose_at(8, origin)], points, rig, FRAME)[0]

    np.testing.assert_allclose(FRAME.from_ecef(c.ecef), [1.0, 0.5, 0.1], atol=1e-3)
    assert c.height_above_road_m == pytest.approx(1.1, abs=1e-3)


def test_road_contact_at_20_km_follows_the_local_geodetic_normal_not_the_frame_up():
    rig = make_rig(offsets=((0, 0.0),))
    origin = np.array([20_000.0, 0.0, 1.2])
    points = disc_points(origin[:2], 0.0)

    c = road_contacts([rig_pose_at(9, origin)], points, rig, FRAME)[0]

    contact = FRAME.from_ecef(c.ecef)
    lat, lon, _ = ecef_to_lla(c.ecef)
    up_ecef = ecef_from_enu_rotation(lat, lon)[:, 2]
    up_enu = up_ecef @ FRAME.ecef_from_enu
    direction = (contact - origin) / np.linalg.norm(contact - origin)
    np.testing.assert_allclose(direction, -up_enu / np.linalg.norm(up_enu), atol=1e-9)
    assert abs(up_enu[0]) > 1e-3  # the frame's up axis really does tilt away from geodetic up here


def test_a_robust_fit_rejects_kerb_points_close_to_the_expected_height():
    rig = make_rig(offsets=((0, 0.0),))
    rng = np.random.default_rng(4)
    points = disc_points((0.0, 0.0), 0.0, spacing=0.2)
    points[:, 2] = rng.normal(0.0, 0.01, len(points))
    kerb = disc_points((0.0, 0.0), 0.25, spacing=0.5)
    kerb = kerb[kerb[:, 0] > 1.5]
    points = np.vstack([points, kerb])

    c = road_contacts([rig_pose_at(2, [0.0, 0.0, 1.2])], points, rig, FRAME)[0]

    assert c.height_above_road_m == pytest.approx(1.2, abs=0.01)
    assert 0.0 < c.sigma_m < 0.05


def test_points_outside_the_height_window_are_not_road_candidates():
    rig = make_rig(offsets=((0, 0.0),))
    road = disc_points((0.0, 0.0), 0.0)
    trees = disc_points((0.0, 0.0), 0.5, spacing=0.5)

    c = road_contacts([rig_pose_at(1, [0.0, 0.0, 1.2])], np.vstack([road, trees]), rig, FRAME)[0]

    assert c.points == len(road)
    assert c.height_above_road_m == pytest.approx(1.2, abs=1e-6)


def test_road_contact_does_not_depend_on_the_rig_rolling_about_its_forward_axis():
    rig = make_rig(offsets=((0, 0.0),))
    points = disc_points((0.0, 0.0), 0.0)
    upright = rig_pose_at(4, [0.0, 0.0, 1.2])
    leaning = replace(upright, world_from_rig=rot_x(15.0))

    calm = road_contacts([upright], points, rig, FRAME)[0]
    leaned = road_contacts([leaning], points, rig, FRAME)[0]

    np.testing.assert_allclose(calm.ecef, leaned.ecef, atol=1e-9)
    assert calm.height_above_road_m == pytest.approx(leaned.height_above_road_m)


def test_a_sparse_disc_falls_back_to_the_nominal_rig_height_and_flags_it():
    rig = make_rig(offsets=((0, 0.0),))
    points = disc_points((0.0, 0.0), 0.0, spacing=1.2)
    assert len(points) < 20

    c = road_contacts([rig_pose_at(6, [0.0, 0.0, 1.2])], points, rig, FRAME)[0]

    assert c.nominal is True
    assert c.points == len(points)
    np.testing.assert_allclose(FRAME.from_ecef(c.ecef), [0.0, 0.0, 0.0], atol=1e-9)
    assert c.sigma_m == NOMINAL_SURFACE_SIGMA_M
    assert c.height_above_road_m == pytest.approx(1.2)


def test_height_check_reports_the_measured_pulses_against_the_rig_config():
    rig = make_rig(offsets=((0, 0.0),))
    points = disc_points((0.0, 0.0), 0.0)
    contacts = road_contacts(
        [rig_pose_at(1, [0.0, 0.0, 1.2]), rig_pose_at(2, [0.0, 0.0, 1.2])], points, rig, FRAME
    )

    check = height_check(contacts, rig)

    assert check["expected_m"] == 1.2
    assert check["measured_pulses"] == 2
    assert check["nominal_pulses"] == 0
    assert check["p50_m"] == pytest.approx(1.2, abs=1e-6)


def contact_at(index, enu, sigma=0.01, nominal=False, bridged=False):
    return RoadContact(index, index * 0.8, FRAME.to_ecef(enu), sigma, 1.2, 100, nominal, bridged)


def straight_contacts(x, n=None, u=None):
    n = np.zeros_like(x) if n is None else n
    u = np.zeros_like(x) if u is None else u
    return [contact_at(i, [e, nn, uu]) for i, (e, nn, uu) in enumerate(zip(x, n, u, strict=True))]


def test_a_straight_trace_stays_straight_and_keeps_exact_heading_and_curvature():
    x = np.arange(0.0, 50.0, 0.8)
    path = rider_path(straight_contacts(x), FRAME)

    assert path.length_m == pytest.approx(49.6, abs=0.02)
    enu, heading, curvature, _ = path.at([0.0, 10.0, 25.0, 49.0])
    np.testing.assert_allclose(enu[:, 1], 0.0, atol=2e-3)
    np.testing.assert_allclose(enu[:, 2], 0.0, atol=2e-3)
    np.testing.assert_allclose(heading, 0.0, atol=2e-3)
    np.testing.assert_allclose(curvature, 0.0, atol=1e-4)
    assert path.smoothing["p95_lateral_m"] <= 0.1
    assert path.smoothing["p95_vertical_m"] <= 0.1


def test_a_circle_gives_constant_heading_rate_and_curvature():
    radius = 20.0
    circumference = 2 * np.pi * radius
    theta = np.arange(0.0, circumference, 0.5) / radius
    contacts = [contact_at(i, [radius * np.cos(a), radius * np.sin(a), 0.0]) for i, a in enumerate(theta)]
    path = rider_path(contacts, FRAME)

    quarter = np.pi / 2 * radius
    _, heading, curvature, _ = path.at([quarter, np.pi * radius, 1.5 * np.pi * radius])
    wrapped = (np.array([np.pi, 1.5 * np.pi, 2 * np.pi]) + np.pi) % (2 * np.pi) - np.pi
    assert np.abs(np.angle(np.exp(1j * (heading - wrapped)))).max() < 0.02
    middle = np.abs(path.s_m - path.length_m / 2) < path.length_m * 0.4
    assert np.median(np.abs(path.curvature_1pm[middle])) == pytest.approx(1 / radius, rel=0.05)
    assert np.all(np.sign(path.curvature_1pm[middle]) > 0)


def test_comfort_first_horizontal_smoothing_removes_capture_weave():
    x = np.arange(0.0, 50.0, 0.2)
    weave = 0.03 * np.sin(2 * np.pi * x / 2.0)
    path = rider_path(straight_contacts(x, n=weave), FRAME)

    assert path.smoothing["horizontal"]["objective"] == "comfort"
    assert path.smoothing["horizontal"]["comfort_met"] is True
    assert path.smoothing["horizontal"]["max_lateral_m"] <= 0.50
    assert path.smoothing["vertical"]["bound_m"] == 0.03
    enu, heading, _, _ = path.at(path.s_m)
    middle = slice(20, -20)
    assert np.ptp(enu[middle, 1]) < 0.5 * np.ptp(weave)
    assert np.abs(heading[middle]).max() < np.radians(5.0)


def test_the_vertical_spline_removes_weave_and_stays_inside_the_bound():
    x = np.arange(0.0, 50.0, 0.2)
    weave = 0.03 * np.sin(2 * np.pi * x / 2.0)
    path = rider_path(straight_contacts(x, u=weave), FRAME)

    assert path.smoothing["p95_vertical_m"] <= 0.03
    middle = slice(20, -20)
    assert np.ptp(path.enu_m[middle, 2]) < 0.5 * np.ptp(weave)


def test_unexpected_spline_failures_propagate_instead_of_being_swallowed(monkeypatch):
    class Exploding:
        def __init__(self, *args, **kwargs):
            raise MemoryError("spline allocation failed")

    monkeypatch.setattr(path_module, "UnivariateSpline", Exploding)

    with pytest.raises(MemoryError, match="spline allocation failed"):
        rider_path(straight_contacts(np.arange(0.0, 10.0, 0.5)), FRAME)


def test_spline_warnings_are_recorded_in_the_smoothing_parameters(monkeypatch):
    real = path_module.UnivariateSpline

    def noisy(*args, **kwargs):
        warnings.warn("fitpack wobble", UserWarning, stacklevel=2)
        return real(*args, **kwargs)

    monkeypatch.setattr(path_module, "UnivariateSpline", noisy)
    path = rider_path(straight_contacts(np.arange(0.0, 10.0, 0.5)), FRAME)

    assert "fitpack wobble" in path.smoothing["warnings"]


def test_a_stop_collapses_without_splitting_the_path_or_breaking_the_tangent():
    x = np.concatenate([np.arange(0.0, 10.0, 0.5), 10.0 + np.arange(20) * 1e-4, np.arange(10.5, 20.0, 0.5)])
    path = rider_path(straight_contacts(x), FRAME)

    assert path.length_m == pytest.approx(19.5, abs=0.05)
    assert np.all(np.diff(path.s_m) > 0)
    assert np.all(np.isfinite(path.enu_m))
    _, heading, _, _ = path.at([5.0, 10.0, 15.0])
    np.testing.assert_allclose(heading, 0.0, atol=1e-3)


def test_a_gap_in_the_contacts_is_bridged_continuously():
    x = np.concatenate([np.arange(0.0, 10.0, 0.5), np.arange(30.0, 40.0, 0.5)])
    path = rider_path(straight_contacts(x), FRAME)

    enu, heading, _, _ = path.at([10.0, 20.0, 30.0])
    np.testing.assert_allclose(enu[:, 1], 0.0, atol=1e-6)
    np.testing.assert_allclose(heading, 0.0, atol=1e-3)
    assert path.length_m == pytest.approx(39.5, abs=0.01)


def test_too_few_contacts_refuse_rather_than_fitting_an_underdetermined_spline():
    with pytest.raises(PathError, match="at least 4"):
        rider_path(straight_contacts(np.array([0.0, 1.0, 2.0])), FRAME)


def test_asking_for_a_point_outside_the_path_refuses():
    path = rider_path(straight_contacts(np.arange(0.0, 10.0, 0.8)), FRAME)
    with pytest.raises(PathError, match="outside"):
        path.at([-1.0])
    with pytest.raises(PathError, match="outside"):
        path.at([path.length_m + 0.1])


class SyntheticGeoid:
    name = "OSGM15"

    def __init__(self):
        self.calls = 0

    def orthometric_height(self, lat, lon, height):
        self.calls += 1
        return np.asarray(height, float) - 50.0

    def provenance(self):
        return {"name": self.name, "grid": "test", "sha256": "0" * 64}


def profile_for(x, z=None, pulses=None, images=None, **contact_flags):
    z = np.zeros_like(x) if z is None else z
    pulses = range(len(x)) if pulses is None else pulses
    contacts = [
        contact_at(i, [e, 0.0, zz], **contact_flags) for i, (e, zz) in enumerate(zip(x, z, strict=True))
    ]
    contacts = [
        RoadContact(p, c.t_gps_s, c.ecef, c.sigma_m, c.height_above_road_m, c.points, c.nominal, c.bridged)
        for p, c in zip(pulses, contacts, strict=True)
    ]
    path = rider_path(contacts, FRAME)
    images = make_images(pulses) if images is None else images
    return path, distance_profile(path, images, FRAME, SyntheticGeoid())


def test_a_flat_profile_has_zero_grade_and_the_sampled_primitive_channels():
    _, profile = profile_for(np.arange(0.0, 10.0, 1.0))

    np.testing.assert_allclose(profile.s_m, np.arange(0.0, 10.0))
    np.testing.assert_allclose(profile.s_h_m, profile.s_m, atol=1e-6)
    np.testing.assert_allclose(profile.grade_percent, 0.0, atol=1e-3)
    np.testing.assert_allclose(profile.curvature_1pm, 0.0, atol=1e-4)
    np.testing.assert_allclose(profile.h_ortho_m, 50.0, atol=1e-4)
    np.testing.assert_allclose(profile.sigma_h_m, 0.01)
    np.testing.assert_allclose(profile.corridor_left_m, CORRIDOR_M)
    np.testing.assert_allclose(profile.corridor_right_m, CORRIDOR_M)
    assert set(profile.surface) == {"asphalt"}
    assert all(flags == () for flags in profile.quality)


def test_a_constant_ramp_reports_its_canonical_physics_grade_end_to_end():
    x = np.arange(0.0, 20.0, 0.5)
    z = 0.05 * x
    _, profile = profile_for(x, z)

    np.testing.assert_allclose(profile.grade_percent, 5.0, atol=0.15)
    assert profile.s_h_m[-1] == pytest.approx(19.5, abs=0.01)
    assert profile.s_m[-1] == pytest.approx(19.5 * np.hypot(1.0, 0.05), abs=0.01)
    np.testing.assert_allclose(profile.h_ortho_m, np.interp(profile.s_h_m, x, z) + 50.0, atol=0.01)


def test_profile_quality_carries_rtk_time_nominal_surface_and_bridged_gaps():
    x = np.arange(0.0, 10.0, 1.0)
    images = make_images(range(10))
    for image in images:
        image["carr_soln"] = {3: 1, 6: 0}.get(image["pulse"], 2)
        image["flags"] = ["time-recovered"] if image["pulse"] == 7 else []
    contacts = [contact_at(i, [e, 0.0, 0.0], nominal=i == 2, bridged=i == 4) for i, e in enumerate(x)]
    path = rider_path(contacts, FRAME)
    profile = distance_profile(path, images, FRAME, SyntheticGeoid())

    by_s = dict(zip(profile.s_m, profile.quality, strict=True))
    assert by_s[3.0] == ("rtk-float",)
    assert by_s[6.0] == ("rtk-none",)
    assert by_s[7.0] == ("time-recovered",)
    assert by_s[2.0] == ("surface-nominal",)
    assert by_s[4.0] == ("bridged-gap",)
    assert by_s[0.0] == ()


def test_the_profile_includes_the_endpoint_when_the_route_is_not_a_whole_number_of_metres():
    _, profile = profile_for(np.arange(0.0, 10.0, 0.5))

    assert profile.s_m[-1] == pytest.approx(9.5, abs=0.01)
    assert profile.s_m[-2] == pytest.approx(9.0)


def test_profile_heights_are_geodetic_and_the_geoid_is_evaluated_for_every_sample():
    geoid = SyntheticGeoid()
    x = np.arange(0.0, 5.0, 0.5)
    path = rider_path([contact_at(i, [e, 0.0, 0.0]) for i, e in enumerate(x)], FRAME)

    profile = distance_profile(path, make_images(range(10)), FRAME, geoid)

    from georoute.geodesy import ecef_to_lla

    ecef = FRAME.to_ecef(path.at(profile.s_m)[0])
    lat, lon, height = ecef_to_lla(ecef).T
    np.testing.assert_allclose(profile.lat_deg, lat, atol=1e-9)
    np.testing.assert_allclose(profile.lon_deg, lon, atol=1e-9)
    np.testing.assert_allclose(profile.h_ortho_m, height - 50.0, atol=1e-6)
    assert geoid.calls == 1
    assert "OSGM15" not in str(profile.quality)


def test_comfort_first_smoothing_absorbs_a_pull_over_locally():
    x = np.arange(0.0, 120.0, 0.2)
    pull_over = np.zeros_like(x)
    mask = (x >= 47.5) & (x <= 52.5)
    pull_over[mask] = 0.6 * np.sin(np.pi * (x[mask] - 47.5) / 5.0) ** 2
    path = rider_path(straight_contacts(x, n=pull_over), FRAME)

    smoothing = path.smoothing["horizontal"]
    assert smoothing["comfort_met"] is True
    assert smoothing["max_yaw_accel_deg_s2"] <= 30.0
    assert smoothing["max_lateral_m"] <= 0.5
    lateral = path.trace_fit_enu_m[:, 1] - path.trace_base_fit_enu_m[:, 1]
    away = (x < 30.0) | (x > 70.0)
    assert np.abs(lateral[away]).max() < 0.02


def test_two_separated_pull_overs_are_both_fixed_without_moving_the_middle():
    x = np.arange(0.0, 150.0, 0.2)
    pulls = np.zeros_like(x)
    for centre in (40.0, 110.0):
        mask = (x >= centre - 3.0) & (x <= centre + 3.0)
        pulls[mask] += 0.45 * np.sin(np.pi * (x[mask] - centre + 3.0) / 6.0) ** 2
    path = rider_path(straight_contacts(x, n=pulls), FRAME)

    smoothing = path.smoothing["horizontal"]
    assert smoothing["comfort_met"] is True
    assert smoothing["iterations"] >= 1
    assert smoothing["max_lateral_m"] <= 0.5
    lateral = path.trace_fit_enu_m[:, 1] - path.trace_base_fit_enu_m[:, 1]
    between_or_outside = ((x > 50.0) & (x < 95.0)) | (x < 20.0) | (x > 130.0)
    assert np.abs(lateral[between_or_outside]).max() < 0.02


def test_two_pull_overs_are_processed_order_independently():
    x = np.arange(0.0, 150.0, 0.2)
    pulls = np.zeros_like(x)
    for centre in (40.0, 110.0):
        mask = (x >= centre - 3.0) & (x <= centre + 3.0)
        pulls[mask] += 0.45 * np.sin(np.pi * (x[mask] - centre + 3.0) / 6.0) ** 2
    forward = rider_path(straight_contacts(x, n=pulls), FRAME)
    reverse = rider_path(straight_contacts(x, n=pulls[::-1]), FRAME)
    forward_lateral = forward.trace_fit_enu_m[:, 1] - forward.trace_enu_m[:, 1]
    reverse_lateral = reverse.trace_fit_enu_m[:, 1] - reverse.trace_enu_m[:, 1]

    assert np.max(np.abs(forward_lateral - reverse_lateral[::-1])) < 0.01


def test_the_comfort_iteration_cap_raises_rather_than_returning_quietly(monkeypatch):
    x = np.arange(0.0, 120.0, 0.2)
    pull_over = np.zeros_like(x)
    mask = (x >= 45.0) & (x <= 55.0)
    pull_over[mask] = 0.6 * np.sin(np.pi * (x[mask] - 45.0) / 10.0) ** 2
    monkeypatch.setattr(path_module, "MAX_COMFORT_ITERATIONS", 0)

    with pytest.raises(PathError, match="did not settle"):
        rider_path(straight_contacts(x, n=pull_over), FRAME)


def test_a_height_step_is_smoothed_into_finite_heights_and_grade():
    x = np.arange(0.0, 40.0, 0.2)
    z = np.where(x < 20.0, 0.0, 0.2)

    _, profile = profile_for(x, z)

    assert np.all(np.isfinite(profile.h_ortho_m))
    assert np.all(np.isfinite(profile.grade_percent))
    assert np.abs(np.diff(profile.h_ortho_m)).max() < 0.2
    assert np.abs(np.diff(profile.grade_percent)).max() < 5.0
