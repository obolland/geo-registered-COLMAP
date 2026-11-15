import numpy as np
import pytest
from test_colmap_report import _poses
from test_rig import EXAMPLE

from georoute.calibrate import CalibrationError, calibrate_rotations, write_calibrated_rig, yaw_pitch_roll
from georoute.rig import load_rig, rig_from_camera


@pytest.mark.parametrize(
    "angles", [(50.0, -0.8, 0.0), (-40.5, 3.0, -2.0), (0.0, 0.0, 0.0), (120.0, -20.0, 10.0)]
)
def test_yaw_pitch_roll_round_trips(angles):
    np.testing.assert_allclose(yaw_pitch_roll(rig_from_camera(*angles)), angles, atol=1e-9)


def test_calibration_recovers_each_cameras_true_angle_relative_to_the_reference():
    rig = load_rig(EXAMPLE)
    poses = _poses(rig, yaw_error_deg=(0, -9.5))
    result = calibrate_rotations(poses, rig, reference=2)
    expected = np.array(rig.camera(0).yaw_pitch_roll_deg) + [-9.5, 0, 0]
    np.testing.assert_allclose(result["cam0"]["yaw_pitch_roll_deg"], expected, atol=1e-6)
    np.testing.assert_allclose(
        result["cam1"]["yaw_pitch_roll_deg"], rig.camera(1).yaw_pitch_roll_deg, atol=1e-6
    )
    assert result["cam0"]["spread_deg"] < 1e-4 and result["cam0"]["pulses"] == 20


def test_calibration_refuses_a_camera_far_from_its_hand_measurement():
    rig = load_rig(EXAMPLE)
    with pytest.raises(CalibrationError, match="cam3.*from its hand measurement"):
        calibrate_rotations(_poses(rig, yaw_error_deg=(3, 30.0)), rig, reference=2)


def test_the_calibrated_rig_loads_and_keeps_the_hand_measurements(tmp_path):
    rig = load_rig(EXAMPLE)
    calibration = calibrate_rotations(_poses(rig, yaw_error_deg=(0, -9.5)), rig, reference=2)
    out = tmp_path / "calibrated.toml"
    write_calibrated_rig(EXAMPLE, out, calibration, reference=2, axial_offset_m=0.02, source="test")
    calibrated = load_rig(out)
    assert calibrated.camera(0).yaw_pitch_roll_deg[0] == pytest.approx(
        rig.camera(0).yaw_pitch_roll_deg[0] - 9.5
    )
    text = out.read_text()
    assert "hand_yaw_pitch_roll_deg" in text and "[calibration]" in text
    axis = rig.rig_from_camera(1)[:, 2]
    np.testing.assert_allclose(
        calibrated.camera(1).position_in_rig_m, rig.camera(1).position_in_rig_m - 0.02 * axis, atol=1e-3
    )


def test_calibrating_a_calibrated_rig_keeps_the_original_hand_measurements(tmp_path):
    rig = load_rig(EXAMPLE)
    first, second = tmp_path / "c1.toml", tmp_path / "c2.toml"
    calibration = calibrate_rotations(_poses(rig, yaw_error_deg=(0, -9.5)), rig, reference=2)
    write_calibrated_rig(EXAMPLE, first, calibration, reference=2, axial_offset_m=0.02, source="one")
    again = calibrate_rotations(_poses(load_rig(first)), load_rig(first), reference=2)
    write_calibrated_rig(first, second, again, reference=2, axial_offset_m=0.0, source="two")
    text = second.read_text()
    assert f"hand_yaw_pitch_roll_deg = {[float(x) for x in rig.camera(0).yaw_pitch_roll_deg]}" in text
    assert 'name = "handlebar-4x100d-calibrated"' in text


def test_calibration_needs_enough_consistent_pulses():
    rig = load_rig(EXAMPLE)
    few = {k: v for k, v in _poses(rig).items() if k[0] < 5}
    with pytest.raises(CalibrationError, match="pulses"):
        calibrate_rotations(few, rig, reference=2)
    with pytest.raises(CalibrationError, match="reference"):
        calibrate_rotations({k: v for k, v in _poses(rig).items() if k[1] != 2}, rig, reference=2)
