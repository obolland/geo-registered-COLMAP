import numpy as np
import pytest

from georoute.geodesy import LocalFrame, ecef_from_enu_rotation, ecef_to_lla
from georoute.path import (
    NOMINAL_SURFACE_SIGMA_M,
    PathError,
    RigPose,
    height_check,
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
    poses[(5, 1)] = (rotation, centre + np.array([0.0, 0.06, 0.0]))

    with pytest.raises(PathError, match="pulse 5"):
        rig_poses(poses, make_images([5]), rig, FRAME)


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
