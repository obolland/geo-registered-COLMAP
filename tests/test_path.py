import numpy as np
import pytest

from georoute.geodesy import LocalFrame
from georoute.path import PathError, rig_poses
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
