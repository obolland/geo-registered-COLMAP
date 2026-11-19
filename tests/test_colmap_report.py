import numpy as np
import pytest
from test_rig import EXAMPLE, _rot_z

from georoute.colmap_report import prior_residuals, rig_check
from georoute.rig import load_rig


def _poses(rig, axial_offset_m=0.04, pulses=20, yaw_error_deg=None):
    """World poses of every camera at every pulse for a rig driving round a bend, with optical centres a
    fixed distance behind the measured lens fronts."""
    poses = {}
    for k in range(pulses):
        world_from_rig = _rot_z(k * 3.0)
        origin = np.array([k * 0.8, 0.02 * k * k, 1.2])
        for cam in rig.cameras:
            rig_from_cam = rig.rig_from_camera(cam.index)
            if yaw_error_deg and cam.index == yaw_error_deg[0]:
                rig_from_cam = _rot_z(yaw_error_deg[1]) @ rig_from_cam
            centre_in_rig = cam.position_in_rig_m - axial_offset_m * rig_from_cam[:, 2]
            poses[(k, cam.index)] = (world_from_rig @ rig_from_cam, origin + world_from_rig @ centre_in_rig)
    return poses


# arccos near 1 resolves angles to about 1e-6 degrees.
def test_rig_check_recovers_the_optical_centre_offset_and_agrees_with_the_rig():
    rig = load_rig(EXAMPLE)
    result = rig_check(_poses(rig), rig, reference=2)
    assert result["axial_offset_m"] == pytest.approx(0.04, abs=1e-6)
    for cam in ("cam0", "cam1", "cam3"):
        assert result["cameras"][cam]["translation_residual_m"] < 1e-6
        assert result["cameras"][cam]["angle_error_deg"] < 1e-4
        assert result["cameras"][cam]["pulses"] == 20


def test_rig_check_reports_a_camera_whose_angle_is_off():
    rig = load_rig(EXAMPLE)
    result = rig_check(_poses(rig, yaw_error_deg=(0, 3.0)), rig, reference=2)
    assert result["cameras"]["cam0"]["angle_error_deg"] == pytest.approx(3.0, abs=1e-6)
    assert result["cameras"]["cam1"]["angle_error_deg"] < 1e-4


def test_prior_residuals_per_camera_on_fixed_images():
    priors = {
        "cam0/a": np.array([0.0, 0.0, 0.0]),
        "cam0/b": np.array([1.0, 0.0, 0.0]),
        "cam1/c": np.array([2.0, 0.0, 0.0]),
    }
    centres = {
        "cam0/a": np.array([0.03, 0.04, 0.0]),
        "cam0/b": np.array([1.0, 0.0, -0.02]),
        "cam1/c": np.array([2.5, 0.0, 0.0]),
    }
    fixed = {"cam0/a": True, "cam0/b": True, "cam1/c": False}
    r = prior_residuals(priors, centres, fixed)
    assert r["cam0"]["horizontal_p50_m"] == pytest.approx(0.025)
    assert r["cam0"]["vertical_p95_m"] == pytest.approx(0.019)
    assert r["cam0"]["images"] == 2 and r["cam1"]["images"] == 0


def test_gate_names_every_failure():
    from georoute.colmap_report import gate

    residuals = {
        "cam0": {"images": 10, "horizontal_p95_m": 0.08, "vertical_p95_m": 0.01},
        "cam1": {"images": 10, "horizontal_p95_m": 0.01, "vertical_p95_m": None},
    }
    rig = {
        "cameras": {
            "cam3": {"pulses": 20, "translation_residual_m": 0.05, "angle_error_deg": 0.5},
            "cam1": {"pulses": 20, "translation_residual_m": 0.001, "angle_error_deg": 4.0},
        }
    }
    failures = gate(residuals, rig)
    assert len(failures) == 3
    assert any("cam0 horizontal" in f for f in failures) and any("cam3 sits 5.0 cm" in f for f in failures)
    assert (
        gate({"cam0": {"images": 10, "horizontal_p95_m": 0.01, "vertical_p95_m": 0.01}}, {"cameras": {}})
        == []
    )


def test_gate_fails_when_a_camera_or_its_evidence_is_missing():
    from georoute.colmap_report import gate

    residuals = {
        "cam0": {"images": 0, "horizontal_p95_m": None, "vertical_p95_m": None},
        "cam1": {"images": 40, "horizontal_p95_m": 0.01, "vertical_p95_m": 0.01},
    }
    rig = {
        "reference": "cam1",
        "cameras": {"cam2": {"pulses": 3, "translation_residual_m": 0.0, "angle_error_deg": 0.0}},
    }
    failures = gate(residuals, rig, expected_cameras=["cam0", "cam1", "cam2", "cam3"])
    assert any("cam0" in f and "no RTK-fixed" in f for f in failures)
    assert any("cam2" in f and "3 pulses" in f for f in failures)
    assert any("cam3" in f and "no registered" in f for f in failures)


def test_scale_against_rtk_compares_path_lengths():
    from georoute.colmap_report import scale_against_priors

    priors = {f"cam2/{k}": np.array([k * 1.0, 0, 0]) for k in range(10)}
    centres = {f"cam2/{k}": np.array([k * 1.001, 0, 0]) for k in range(10)}
    order = {f"cam2/{k}": k for k in range(10)}
    assert scale_against_priors(priors, centres, order, "cam2") == pytest.approx(1.001)


def test_auxiliary_cameras_are_exempt_from_the_prior_residual_gate():
    from georoute.colmap_report import gate

    residuals = {
        "cam0": {"images": 10, "horizontal_p95_m": 0.08, "vertical_p95_m": 0.02},
        "cam4": {"images": 5, "horizontal_p95_m": 2.0, "vertical_p95_m": 1.0},
    }
    rig = {"cameras": {}}
    failures = gate(residuals, rig, ["cam0", "cam4"], frozenset({"cam4"}))
    assert failures == ["cam0 horizontal prior residual p95 8.0 cm exceeds 5 cm"]
    empty = {"cam4": {"images": 0, "horizontal_p95_m": None, "vertical_p95_m": None}}
    assert gate(empty, rig, ["cam4"], frozenset({"cam4"})) == []
    assert gate(empty, rig, ["cam4"]) == ["cam4 has no RTK-fixed images to check against"]
    assert gate(residuals, rig, ["cam0", "cam4"], frozenset()) == [
        "cam0 horizontal prior residual p95 8.0 cm exceeds 5 cm",
        "cam4 horizontal prior residual p95 200.0 cm exceeds 5 cm",
        "cam4 vertical prior residual p95 100.0 cm exceeds 5 cm",
    ]
