from pathlib import Path

import numpy as np
import pytest

from georoute.alignment import umeyama
from georoute.cli import main
from georoute.rig import RigError, load_rig, refine_priors, rig_from_camera

EXAMPLE = Path(__file__).parents[1] / "rigs" / "handlebar-4cam.example.toml"


def _rot_z(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])


def _rot_y(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])


def test_the_example_rig_loads_and_is_marked_unmeasured():
    rig = load_rig(EXAMPLE)
    assert [c.index for c in rig.cameras] == [0, 1, 2, 3]
    assert not rig.measured
    np.testing.assert_allclose(rig.lever_arm_m, [0.0, 0.0, 0.20])
    assert len(rig.sha256) == 64


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda t: t.replace("index = 1", "index = 0"), "index 0"),
        (lambda t: t.replace('folder = "cam1"', 'folder = "cam0"'), "folder cam0"),
        (lambda t: t.replace("[0.15, 0.05, 0.0]", "[0.15, 0.05]"), "position_in_rig_m"),
        (lambda t: t.replace("georoute.rig/1.0.0", "georoute.rig/2.0.0"), "schema"),
        (lambda t: t.replace("measured = false", "measured = false\nmesured = true"), "mesured"),
        (lambda t: t.replace("[timing]\ncurtain_travel_ms = 0.0\n", ""), "timing"),
        (lambda t: t.replace("index = 3", "index = 4"), "index 4"),
        (lambda t: t.replace("measured = false", 'measured = "false"'), "measured"),
        (lambda t: t.replace("curtain_travel_ms = 0.0", ""), "curtain_travel_ms"),
        (lambda t: t.replace("curtain_travel_ms = 0.0", "curtain_travel_ms = -1.0"), "curtain_travel_ms"),
        (
            lambda t: t.replace("height_above_road_m = 1.05", 'height_above_road_m = "tall"'),
            "height_above_road_m",
        ),
        (lambda t: t.split("[[camera]]")[0], "no cameras"),
        (lambda t: t.replace("[0.15, 0.05, 0.0]", "[0.15, 0.05, nan]"), "position_in_rig_m"),
        (lambda t: t.replace("[0.15, 0.05, 0.0]", "[0.15, true, 0.0]"), "position_in_rig_m"),
        (lambda t: t.replace("[0.0, -10.0, 0.0]", "[0.0, -400.0, 0.0]"), "yaw_pitch_roll_deg"),
        (lambda t: t.replace('folder = "cam1"', 'folder = "../cam1"'), "folder"),
        (lambda t: t.replace('serial = ""', "serial = 12345", 1), "serial"),
    ],
    ids=[
        "dup-index",
        "dup-folder",
        "short-vector",
        "schema",
        "typo-key",
        "missing-table",
        "bad-index",
        "string-bool",
        "missing-curtain",
        "negative-curtain",
        "string-height",
        "no-cameras",
        "nan",
        "bool-in-vector",
        "pitch-400",
        "folder-escape",
        "integer-serial",
    ],
)
def test_invalid_configs_refuse(tmp_path, edit, message):
    path = tmp_path / "rig.toml"
    path.write_text(edit(EXAMPLE.read_text()))
    with pytest.raises(RigError, match=message):
        load_rig(path)


def test_camera_axes_follow_the_documented_convention():
    forward, right, down = np.array([0, 0, 1]), np.array([1, 0, 0]), np.array([0, 1, 0])
    np.testing.assert_allclose(rig_from_camera(0, 0, 0) @ forward, [0, 1, 0], atol=1e-12)
    np.testing.assert_allclose(rig_from_camera(0, 0, 0) @ right, [1, 0, 0], atol=1e-12)
    np.testing.assert_allclose(rig_from_camera(0, 0, 0) @ down, [0, 0, -1], atol=1e-12)
    np.testing.assert_allclose(rig_from_camera(90, 0, 0) @ forward, [-1, 0, 0], atol=1e-12)
    assert (rig_from_camera(0, 10, 0) @ forward)[2] == pytest.approx(np.sin(np.radians(10)))
    assert (rig_from_camera(0, 0, 90) @ right)[2] == pytest.approx(-1.0)


def test_camera_centre_from_antenna_and_attitude():
    rig = load_rig(EXAMPLE)
    a = np.array([10.0, 20.0, 3.0])
    world_from_rig = _rot_z(30)
    for cam in rig.cameras:
        expected = a + world_from_rig @ (cam.position_in_rig_m - rig.lever_arm_m)
        np.testing.assert_allclose(rig.camera_centre(cam.index, a, world_from_rig), expected)
    np.testing.assert_allclose(rig.reference_point(a, world_from_rig), a - world_from_rig @ rig.lever_arm_m)


def _synthetic_ride(rig, n=60, speed=8.0):
    """A rig leaning 5 degrees through a 30 m radius bend; each camera exposes up to 36 ms after the others,
    so each image has its own antenna position and attitude."""
    frames = []
    for k in range(n):
        for cam in rig.cameras:
            heading = (k * 0.1 + cam.index * 0.012) * speed / 30.0
            world_from_rig = _rot_z(np.degrees(heading)) @ _rot_y(5.0)
            antenna = np.array([30 * np.sin(heading), 30 * (1 - np.cos(heading)), 1.25])
            frames.append((f"cam{cam.index}/IMG_{k:04d}.JPG", cam.index, antenna, world_from_rig))
    return frames


def _aligner_for(rig, frames):
    """Pretends to be COLMAP: knows the true camera poses in its own arbitrary frame, and returns world
    rotations after a similarity fit of its camera centres to the priors it is given."""
    truth = {name: rig.camera_centre(cam, a, r) for name, cam, a, r in frames}
    true_rot = {name: r @ rig.rig_from_camera(cam) for name, cam, _, r in frames}
    model_from_world = _rot_z(70) * 0.37
    model = {name: model_from_world @ c + [5, -2, 9] for name, c in truth.items()}

    def align(priors):
        names = sorted(priors)
        s, r, _ = umeyama(np.array([model[n] for n in names]), np.array([priors[n] for n in names]))
        return {n: r @ (model_from_world / 0.37) @ true_rot[n] for n in names}

    return truth, align


def test_missing_or_unreadable_files_are_rig_errors(tmp_path):
    with pytest.raises(RigError, match="rig.toml"):
        load_rig(tmp_path / "rig.toml")
    (tmp_path / "rig.toml").write_bytes(b"\xff\xfe")
    with pytest.raises(RigError, match="rig.toml"):
        load_rig(tmp_path / "rig.toml")


def test_first_pass_error_is_bounded_by_the_lever_arm_and_refinement_converges():
    rig = load_rig(EXAMPLE)
    frames = _synthetic_ride(rig)
    truth, align = _aligner_for(rig, frames)
    antenna = {name: (cam, a) for name, cam, a, _ in frames}
    first = {name: a for name, (_, a) in antenna.items()}
    worst_arm = max(np.linalg.norm(c.position_in_rig_m - rig.lever_arm_m) for c in rig.cameras)
    assert max(np.linalg.norm(first[n] - truth[n]) for n in truth) <= worst_arm + 1e-9

    result = refine_priors(rig, antenna, align)
    assert result.iterations <= 3 and result.last_change_m < 1e-3
    assert max(np.linalg.norm(result.priors[n] - truth[n]) for n in truth) < 1e-3
    assert set(result.world_from_camera) == set(truth)


def test_antenna_noise_leaves_camera_priors_at_the_noise_level():
    rig = load_rig(EXAMPLE)
    frames = _synthetic_ride(rig)
    truth, align = _aligner_for(rig, frames)
    rng = np.random.default_rng(4)
    antenna = {name: (cam, a + rng.normal(scale=0.02, size=3)) for name, cam, a, _ in frames}
    result = refine_priors(rig, antenna, align, tolerance_m=5e-3)
    errors = [np.linalg.norm(result.priors[n] - truth[n]) for n in truth]
    assert np.percentile(errors, 95) < 0.06


def test_a_straight_ride_cannot_reveal_the_lever_arm_and_refuses():
    rig = load_rig(EXAMPLE)
    frames = [
        (f"cam{c.index}/IMG_{k:04d}.JPG", c.index, np.array([0.8 * k, 0.0, 1.25]), _rot_y(5.0))
        for k in range(60)
        for c in rig.cameras
    ]
    _, align = _aligner_for(rig, frames)
    with pytest.raises(RigError, match="straight"):
        refine_priors(rig, {name: (cam, a) for name, cam, a, _ in frames}, align)


def test_rig_check_command(capsys):
    assert main(["rig-check", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "NOT MEASURED" in out and "cam2" in out and "lever arm" in out
