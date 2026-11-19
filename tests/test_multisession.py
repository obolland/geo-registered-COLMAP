import json

import numpy as np
import pycolmap
import pytest
from PIL import Image
from test_rig import EXAMPLE

from georoute.geodesy import LocalFrame, lla_to_ecef
from georoute.multisession import (
    MultiSessionError,
    SessionSpec,
    common_frame,
    doc_frame,
    image_headings,
    images_in_frame,
    prepare_sessions,
    session_image_name,
)
from georoute.reconstruct import prepare
from georoute.rig import load_rig


def _doc(frame, images, datum=None, session="s1"):
    return {
        "schema": "georoute.images/1.2.0",
        "session": session,
        "datum": datum or frame.datum,
        "frame": {
            "id": f"{session}/rtk-fixed",
            "lat_deg": frame.lat_deg,
            "lon_deg": frame.lon_deg,
            "h_m": frame.h_m,
            "datum": frame.datum,
            "origin_ecef": frame.origin_ecef.tolist(),
        },
        "images": images,
    }


def _image(ecef, velocity=(1.3, 0.0, 0.0)):
    return {
        "path": "cam0/IMG_0001.JPG",
        "camera": 0,
        "pulse": 2,
        "antenna_ecef": np.asarray(ecef, float).tolist(),
        "antenna_enu": [0.0, 0.0, 0.0],
        "velocity_enu": list(velocity),
        "has_prior": True,
        "prior_sigma_h_m": 0.014,
        "prior_sigma_v_m": 0.011,
        "carr_soln": 2,
        "flags": [],
    }


F1 = LocalFrame.at(51.15875, -4.12440, 208.0, "ETRS89")
F2 = LocalFrame.at(51.15940, -4.11880, 212.0, "ETRS89")


def test_the_common_frame_is_the_first_sessions_and_reprojects_through_ecef():
    point = lla_to_ecef(51.1590, -4.1210, 210.0)
    first = _doc(F1, [_image(point)])
    second = _doc(F2, [_image(point)], session="s2")

    frame = common_frame([first, second])
    assert frame.datum == F1.datum
    np.testing.assert_allclose(frame.origin_ecef, F1.origin_ecef)

    a = images_in_frame(first, frame)[0]["antenna_enu"]
    b = images_in_frame(second, frame)[0]["antenna_enu"]
    np.testing.assert_allclose(a, b, atol=1e-9)
    np.testing.assert_allclose(a, F1.from_ecef(point), atol=1e-9)
    # The same point sits hundreds of metres apart in the two sessions' own frames; the common frame
    # must not simply copy each session's antenna_enu.
    assert np.linalg.norm(F2.from_ecef(point) - F1.from_ecef(point)) > 100


def test_velocity_is_rotated_into_the_common_frame():
    point = lla_to_ecef(51.1590, -4.1210, 210.0)
    v_ecef = F2.ecef_from_enu @ np.array([1.3, 0.0, 0.5])
    second = _doc(F2, [_image(point, velocity=[1.3, 0.0, 0.5])], session="s2")
    frame = common_frame([_doc(F1, [_image(point)]), second])
    out = images_in_frame(second, frame)[0]["velocity_enu"]
    np.testing.assert_allclose(np.asarray(out) @ frame.ecef_from_enu.T, v_ecef, atol=1e-9)


def test_a_session_frame_is_recovered_from_the_document():
    doc = _doc(F2, [])
    frame = doc_frame(doc)
    np.testing.assert_allclose(frame.origin_ecef, F2.origin_ecef)
    assert frame.datum == "ETRS89"


def test_frames_with_different_datums_are_refused():
    first = _doc(F1, [])
    second = _doc(F2, [], datum="WGS84")
    second["frame"]["datum"] = "WGS84"
    with pytest.raises(ValueError, match="datum"):
        common_frame([first, second])


def _textured_session(root, frame, label, cameras=(0, 1), pulses=(2, 3, 4), opposite=False):
    """An images.json and its photos for one session, on a straight ride east or west."""
    rng = np.random.default_rng(abs(hash(label)) % 2**32)
    images = []
    for camera in cameras:
        folder = root / f"cam{camera}"
        folder.mkdir(parents=True, exist_ok=True)
        for k, pulse in enumerate(pulses):
            path = f"cam{camera}/IMG_{pulse:04d}.JPG"
            Image.fromarray((rng.random((120, 160, 3)) * 255).astype("uint8")).save(root / path)
            east = 0.8 * (len(pulses) - k if opposite else k)
            ecef = frame.to_ecef([east, 0.0, 1.2])
            images.append(
                {
                    **_image(ecef, velocity=(-1.3 if opposite else 1.3, 0.0, 0.0)),
                    "path": path,
                    "camera": camera,
                    "pulse": pulse,
                    "antenna_enu": frame.from_ecef(ecef).tolist(),
                }
            )
    doc = _doc(frame, images, session=label)
    (root / "images.json").write_text(json.dumps(doc))
    return root / "images.json", root


def _spec(root, frame, label, rig_path=EXAMPLE, opposite=False):
    images_json, images_root = _textured_session(root, frame, label, opposite=opposite)
    return SessionSpec(label, images_json, images_root, load_rig(rig_path), rig_path)


def test_session_names_gain_the_session_only_when_there_are_several():
    assert session_image_name("s1", "cam0/IMG_0001.JPG") == "s1/cam0/IMG_0001.JPG"


def test_two_sessions_prepare_one_camera_per_session_and_folder(tmp_path):
    f1 = LocalFrame.at(51.15875, -4.12440, 208.0, "ETRS89")
    f2 = LocalFrame.at(51.15900, -4.12410, 209.0, "ETRS89")
    specs = [_spec(tmp_path / "s1", f1, "s1"), _spec(tmp_path / "s2", f2, "s2")]
    summary = prepare_sessions(specs, tmp_path / "ws")
    assert summary["images"] == 12 and summary["cameras"] == 4 and summary["priors"] == 12
    assert summary["sessions"] == ["s1", "s2"]
    assert summary["frame_id"] == "s1/rtk-fixed"

    with pycolmap.Database.open(tmp_path / "ws" / "database.db") as db:
        by_name = {image.name: image.camera_id for image in db.read_all_images()}
        cameras = {camera.camera_id: camera for camera in db.read_all_cameras()}
    assert set(by_name) == {
        f"{session}/cam{camera}/IMG_{pulse:04d}.JPG"
        for session in ("s1", "s2")
        for camera in (0, 1)
        for pulse in (2, 3, 4)
    }
    # The same folder on two rides must not share a COLMAP camera.
    assert len({by_name[f"{s}/cam0/IMG_0002.JPG"] for s in ("s1", "s2")}) == 2
    assert len({by_name[f"{s}/cam1/IMG_0002.JPG"] for s in ("s1", "s2")}) == 2
    assert all(camera.model == pycolmap.CameraModelId.OPENCV for camera in cameras.values())

    record = json.loads((tmp_path / "ws" / "priors.json").read_text())
    second = images_in_frame(json.loads((tmp_path / "s2" / "images.json").read_text()), f1)
    expected = {f"s2/{image['path']}": image["antenna_enu"] for image in second}
    assert record["s2/cam0/IMG_0002.JPG"]["session"] == "s2"
    assert record["s1/cam0/IMG_0002.JPG"]["session"] == "s1"
    np.testing.assert_allclose(
        record["s2/cam0/IMG_0002.JPG"]["antenna_enu"], expected["s2/cam0/IMG_0002.JPG"]
    )

    sessions = json.loads((tmp_path / "ws" / "sessions.json").read_text())
    assert [s["label"] for s in sessions["sessions"]] == ["s1", "s2"]
    assert sessions["frame"]["datum"] == "ETRS89"
    assert (tmp_path / "ws" / "images" / "s1" / "cam0" / "IMG_0002.JPG").is_symlink()


def test_a_single_session_prepares_exactly_as_before(tmp_path):
    f1 = LocalFrame.at(51.15875, -4.12440, 208.0, "ETRS89")
    spec = _spec(tmp_path / "s1", f1, "s1")
    prepare_sessions([spec], tmp_path / "new")
    prepare(spec.images_json, spec.images_root, tmp_path / "old", spec.rig)

    assert (tmp_path / "new" / "priors.json").read_bytes() == (tmp_path / "old" / "priors.json").read_bytes()
    assert not (tmp_path / "new" / "sessions.json").exists()
    assert not (tmp_path / "new" / "images").exists()

    def cameras_by_images(db):
        cameras = {camera.camera_id: camera for camera in db.read_all_cameras()}
        grouping: dict[int, set[str]] = {}
        for image in db.read_all_images():
            grouping.setdefault(image.camera_id, set()).add(image.name)
        return {frozenset(names): cameras[camera_id].model for camera_id, names in grouping.items()}

    with (
        pycolmap.Database.open(tmp_path / "new" / "database.db") as new,
        pycolmap.Database.open(tmp_path / "old" / "database.db") as old,
    ):
        assert cameras_by_images(new) == cameras_by_images(old)
        assert new.num_cameras() == old.num_cameras() == 2


def test_duplicate_or_unsafe_session_names_are_refused(tmp_path):
    f1 = LocalFrame.at(51.15875, -4.12440, 208.0, "ETRS89")
    one = _spec(tmp_path / "a", f1, "s1")
    two = _spec(tmp_path / "b", f1, "s1")
    with pytest.raises(MultiSessionError, match="more than one session"):
        prepare_sessions([one, two], tmp_path / "ws")
    unsafe = SessionSpec("../s1", one.images_json, one.images_root, one.rig, EXAMPLE)
    with pytest.raises(MultiSessionError, match="plain"):
        prepare_sessions([one, unsafe], tmp_path / "ws2")


def test_a_missing_session_photo_is_refused(tmp_path):
    f1 = LocalFrame.at(51.15875, -4.12440, 208.0, "ETRS89")
    one = _spec(tmp_path / "a", f1, "s1")
    two = _spec(tmp_path / "b", f1, "s2")
    (two.images_root / "cam1" / "IMG_0003.JPG").unlink()
    with pytest.raises(MultiSessionError, match="s2/cam1/IMG_0003.JPG"):
        prepare_sessions([one, two], tmp_path / "ws")


def test_image_headings_follow_each_sessions_travel_direction():
    record = {}
    for session, direction in (("s1", 1.0), ("s2", -1.0)):
        for pulse in (2, 3, 4):
            record[f"{session}/cam0/IMG_{pulse:04d}.JPG"] = {
                "session": session,
                "camera": 0,
                "pulse": pulse,
                "centre": [direction * 0.8 * pulse, 0.0, 1.0],
            }
    headings = image_headings(record)
    assert np.dot(headings["s1/cam0/IMG_0003.JPG"], headings["s2/cam0/IMG_0003.JPG"]) < 0
    assert np.dot(headings["s1/cam0/IMG_0003.JPG"], headings["s1/cam0/IMG_0002.JPG"]) > 0


def test_matching_reports_session_and_camera_pairs(tmp_path):
    from georoute.reconstruct import match

    f1 = LocalFrame.at(51.15875, -4.12440, 208.0, "ETRS89")
    one = _spec(tmp_path / "s1", f1, "s1")
    two = _spec(tmp_path / "s2", f1, "s2")
    for camera in (0, 1):
        for pulse in (2, 3, 4):
            name = f"cam{camera}/IMG_{pulse:04d}.JPG"
            (two.images_root / name).write_bytes((one.images_root / name).read_bytes())
    prepare_sessions([one, two], tmp_path / "ws")
    report = match(tmp_path / "ws", max_neighbors=10, max_distance_m=50)
    assert report["by_session_pair"]["s1-s2"] >= 1
    assert report["by_session_pair_direction"]["s1-s2"]["same"] >= 1
    assert any(key.startswith("s1/cam") and "s2/cam" in key for key in report["by_camera_pair"])
    assert report["verified_pairs"] >= report["by_session_pair"]["s1-s2"]


def test_report_checks_each_session_against_its_own_rig(tmp_path):
    from georoute.reconstruct import report
    from georoute.rig import Camera, Rig

    options = pycolmap.SyntheticDatasetOptions()
    options.num_rigs = 1
    options.num_cameras_per_rig = 1
    options.num_frames_per_rig = 12
    options.num_points3D = 400
    options.prior_position = True
    options.prior_position_coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
    ws = tmp_path / "ws"
    ws.mkdir()
    with pycolmap.Database.open(ws / "database.db") as db:
        truth = pycolmap.synthesize_dataset(options, db)
    record = {}
    for index, image in enumerate(truth.images.values()):
        label = "s1" if index < 6 else "s2"
        image.name = f"{label}/cam0/{image.name}"
        record[image.name] = {
            "session": label,
            "camera": 0,
            "pulse": index if index < 6 else index - 6,
            "fixed": True,
            "antenna_enu": image.projection_center().tolist(),
            "sigma_h_m": 0.01,
            "sigma_v_m": 0.01,
            "centre": image.projection_center().tolist(),
            "covariance": (np.eye(3) * 1e-4).tolist(),
        }
    (ws / "sparse" / "0").mkdir(parents=True)
    truth.write(ws / "sparse" / "0")
    (ws / "priors.json").write_text(json.dumps(record))
    (ws / "mapping.json").write_text(
        json.dumps({"models": 1, "images": len(record), "registered": len(record)})
    )

    def rig(label):
        return Rig(
            label,
            True,
            (Camera(0, "", "cam0", None, np.zeros(3), (0.0, 0.0, 0.0)),),
            np.zeros(3),
            1.0,
            0.0,
            label,
        )

    rigs = {"s1": rig("s1"), "s2": rig("s2")}
    out = report(ws, rigs["s1"], rigs=rigs)
    assert set(out["sessions"]) == {"s1", "s2"}
    for session in out["sessions"].values():
        assert session["prior_residuals"]["cam0"]["images"] == 6
        assert session["registration"]["cam0"]["registered"] == 6
        assert session["scale_against_rtk"] == pytest.approx(1.0)
        assert session["failures"] == []
    assert out["failures"] == []
