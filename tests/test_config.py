"""Hardware is configuration: camera count from the data, camera type and reference camera from the rig."""

from dataclasses import replace

import pytest
from conftest import edit_csv
from test_photos import _photos_for
from test_rig import EXAMPLE

from georoute.photos import MatchError, match_photos
from georoute.rig import RigError, load_rig
from georoute.session import load_session


def _five_cameras(session_dir):
    def add(rows):
        rows[0].append("cam4_delay_us")
        for r in rows[1:]:
            r.append(r[3])
        return rows

    edit_csv(session_dir / "pulses.csv", add)


def test_a_session_with_five_cameras_loads_and_matches(session_dir):
    _five_cameras(session_dir)
    session = load_session(session_dir)
    assert session.camera_count == 5
    photos = {c: _photos_for(session, c) for c in (0, 4)}
    assert {m.photo.camera for m in match_photos(session, photos)} == {0, 4}


def _rig_text(top="", defaults="", extra_camera0=""):
    """The example rig at schema 1.1.0 plus top-level keys, a [camera_defaults] table and camera 0 keys."""
    text = EXAMPLE.read_text().replace(
        'schema = "georoute.rig/1.0.0"', 'schema = "georoute.rig/1.1.0"\n' + top
    )
    text = text.replace("index = 0\n", "index = 0\n" + extra_camera0, 1)
    return text + ("\n[camera_defaults]\n" + defaults if defaults else "")


def test_camera_type_defaults_and_overrides(tmp_path):
    path = tmp_path / "rig.toml"
    path.write_text(
        _rig_text(
            "reference_camera = 1",
            'colmap_model = "OPENCV_FISHEYE"\nshutter_delay_ms = [40.0, 200.0]\n',
            "max_exposure_s = 2.0\n",
        )
    )
    rig = load_rig(path)
    assert rig.reference_camera == 1
    assert rig.camera_type(1).colmap_model == "OPENCV_FISHEYE"
    assert rig.camera_type(1).shutter_delay_ms == (40.0, 200.0)
    assert rig.camera_type(0).max_exposure_s == 2.0 and rig.camera_type(1).max_exposure_s == 30.0
    assert rig.camera_type(0).file_number_wrap == 9999


def test_old_rigs_get_the_100d_defaults_and_the_first_camera_as_reference():
    rig = load_rig(EXAMPLE)
    assert rig.reference_camera == 0
    assert rig.camera_type(2).colmap_model == "OPENCV"
    assert rig.camera_type(2).shutter_delay_ms == (30.0, 500.0)


def test_a_rig_with_more_cameras_than_four_validates(tmp_path):
    text = (
        _rig_text() + '\n[[camera]]\nindex = 7\nfolder = "cam7"\nposition_in_rig_m = [0.0, 0.3, 0.0]\n'
        "yaw_pitch_roll_deg = [180.0, 0.0, 0.0]\n"
    )
    (tmp_path / "rig.toml").write_text(text)
    assert load_rig(tmp_path / "rig.toml").camera(7).folder == "cam7"
    (tmp_path / "rig.toml").write_text(text.replace('rig/1.1.0"\n', 'rig/1.1.0"\nreference_camera = 9\n', 1))
    with pytest.raises(RigError):
        load_rig(tmp_path / "rig.toml")


def test_shutter_delay_limits_come_from_the_rig(session_dir, tmp_path):
    path = tmp_path / "rig.toml"
    path.write_text(_rig_text(defaults="shutter_delay_ms = [40.0, 95.0]\n"))
    rig = load_rig(path)
    session = load_session(session_dir)
    with pytest.raises(MatchError, match="cam0.*delay"):
        match_photos(session, {0: _photos_for(session, 0)}, camera_types={0: rig.camera_type(0)})


def test_a_fault_on_an_unknown_camera_refuses(session_dir):
    session = load_session(session_dir)
    with pytest.raises(MatchError, match="cam6"):
        match_photos(session, {0: _photos_for(session, 0)}, faulty={6})


def test_exposure_limit_comes_from_the_rig(session_dir, tmp_path):
    path = tmp_path / "rig.toml"
    path.write_text(_rig_text(extra_camera0="max_exposure_s = 0.001\n"))
    rig = load_rig(path)
    session = load_session(session_dir)
    photos = [replace(p, exposure_s=0.002) for p in _photos_for(session, 0)]
    with pytest.raises(MatchError, match="cam0.*exposure"):
        match_photos(session, {0: photos}, camera_types={0: rig.camera_type(0)})
    assert match_photos(session, {0: photos}, camera_types={0: rig.camera_type(1)})


def test_a_1_0_0_rig_refuses_1_1_0_keys(tmp_path):
    path = tmp_path / "rig.toml"
    path.write_text(EXAMPLE.read_text().replace('rig/1.0.0"\n', 'rig/1.0.0"\nreference_camera = 1\n', 1))
    with pytest.raises(RigError, match="reference_camera.*1.1.0"):
        load_rig(path)


def test_a_calibrated_rig_keeps_camera_types_and_names_its_reference(tmp_path):
    from georoute.calibrate import write_calibrated_rig

    source = tmp_path / "rig.toml"
    source.write_text(
        _rig_text("reference_camera = 1", 'colmap_model = "OPENCV_FISHEYE"\n', "max_exposure_s = 2.0\n")
    )
    out = tmp_path / "calibrated.toml"
    write_calibrated_rig(source, out, {}, reference=2, axial_offset_m=0.0, source="test")
    rig = load_rig(out)
    assert rig.reference_camera == 2
    assert rig.camera_type(3).colmap_model == "OPENCV_FISHEYE"
    assert rig.camera_type(0).max_exposure_s == 2.0 and rig.camera_type(1).max_exposure_s == 30.0
