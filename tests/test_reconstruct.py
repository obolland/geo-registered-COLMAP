import numpy as np
import pycolmap
import pytest
from test_rig import EXAMPLE

from georoute.reconstruct import LEVER_ALLOWANCE_M, first_pass_priors, nominal_world_from_rig, write_priors
from georoute.rig import load_rig


def test_nominal_attitude_points_the_rig_along_the_velocity():
    r = nominal_world_from_rig(np.array([3.0, 0.0, 0.5]))  # heading east, climbing slightly
    np.testing.assert_allclose(r @ [0, 1, 0], [1, 0, 0], atol=1e-12)  # forward is east
    np.testing.assert_allclose(r @ [1, 0, 0], [0, -1, 0], atol=1e-12)  # right is south
    np.testing.assert_allclose(r @ [0, 0, 1], [0, 0, 1], atol=1e-12)  # level: up stays up


def _images(rig, speed_east=1.3):
    out = []
    for k in range(6):
        for cam in rig.cameras:
            out.append(
                {
                    "path": f"cam{cam.index}/IMG_{k:04d}.JPG",
                    "camera": cam.index,
                    "antenna_enu": [speed_east * k * 0.6, 0.0, 1.2],
                    "has_prior": True,
                    "prior_sigma_h_m": 0.014,
                    "prior_sigma_v_m": 0.011,
                    "velocity_enu": [speed_east, 0, 0],
                }
            )
    return out


def test_first_pass_priors_apply_the_lever_arm_with_the_nominal_attitude():
    rig = load_rig(EXAMPLE)
    images = _images(rig)
    priors = first_pass_priors(images, rig)
    image = images[0]
    cam = rig.camera(image["camera"])
    expected = np.array(image["antenna_enu"]) + nominal_world_from_rig(np.array([1.3, 0, 0])) @ (
        cam.position_in_rig_m - rig.lever_arm_m
    )
    centre, cov = priors[image["path"]]
    np.testing.assert_allclose(centre, expected, atol=1e-12)
    assert cov[0, 0] == pytest.approx(0.014**2 + LEVER_ALLOWANCE_M**2)
    assert cov[2, 2] == pytest.approx(0.011**2 + LEVER_ALLOWANCE_M**2)


def test_images_without_a_prior_get_none():
    rig = load_rig(EXAMPLE)
    images = _images(rig)
    images[3]["has_prior"] = False
    assert images[3]["path"] not in first_pass_priors(images, rig)


def test_stationary_images_borrow_the_nearest_moving_heading():
    rig = load_rig(EXAMPLE)
    images = _images(rig)
    for i in images[:4]:
        i["velocity_enu"] = [0.0, 0.05, 0.0]
    priors = first_pass_priors(images, rig)
    still, moving = priors[images[0]["path"]][0], np.array(images[0]["antenna_enu"])
    arm = rig.camera(0).position_in_rig_m - rig.lever_arm_m
    np.testing.assert_allclose(
        still - moving, nominal_world_from_rig(np.array([1.3, 0, 0])) @ arm, atol=1e-12
    )


def test_priors_are_written_to_the_database_with_their_covariance(tmp_path):
    db_path = tmp_path / "database.db"
    with pycolmap.Database.open(db_path) as db:
        camera_id = db.write_camera(pycolmap.Camera.create_from_model_name(1, "OPENCV", 4000.0, 5184, 3456))
        image_id = db.write_image(pycolmap.Image(name="cam0/IMG_0001.JPG", camera_id=camera_id))
        db.write_image(pycolmap.Image(name="cam0/IMG_0002.JPG", camera_id=camera_id))
        cov = np.diag([1e-4, 1e-4, 4e-4])
        written = write_priors(db, {"cam0/IMG_0001.JPG": (np.array([1.0, 2.0, 3.0]), cov)})
        assert written == 1 and db.num_pose_priors() == 1
        (prior,) = db.read_all_pose_priors()
        assert prior.corr_data_id.id == image_id
        assert prior.coordinate_system == pycolmap.PosePriorCoordinateSystem.CARTESIAN
        np.testing.assert_allclose(prior.position, [1.0, 2.0, 3.0])
        np.testing.assert_allclose(prior.position_covariance, cov)


def _textured_photos(root, rig, n=4):
    from PIL import Image

    rng = np.random.default_rng(0)
    images = []
    for cam in (0, 1):
        (root / f"cam{cam}").mkdir(parents=True, exist_ok=True)
        for k in range(n):
            path = f"cam{cam}/IMG_{k:04d}.JPG"
            Image.fromarray((rng.random((240, 320, 3)) * 255).astype("uint8")).save(root / path)
            images.append(
                {
                    "path": path,
                    "camera": cam,
                    "pulse": k + 2,
                    "antenna_enu": [k * 0.8, 0.0, 1.2],
                    "has_prior": k != 1,
                    "prior_sigma_h_m": 0.014,
                    "prior_sigma_v_m": 0.011,
                    "velocity_enu": [1.3, 0.0, 0.0],
                }
            )
    return images


def test_prepare_extracts_features_per_camera_folder_and_writes_priors(tmp_path):
    import json

    from georoute.reconstruct import prepare

    rig = load_rig(EXAMPLE)
    images = _textured_photos(tmp_path / "images", rig)
    (tmp_path / "images.json").write_text(json.dumps({"images": images, "frame": {"id": "test"}}))
    summary = prepare(tmp_path / "images.json", tmp_path / "images", tmp_path / "ws", rig, pulses=(2, 4))
    assert summary == {"images": 6, "cameras": 2, "priors": 4, "frame_id": "test"}
    with pycolmap.Database.open(tmp_path / "ws" / "database.db") as db:
        assert db.num_images() == 6 and db.num_cameras() == 2 and db.num_pose_priors() == 4
        assert all(c.model == pycolmap.CameraModelId.OPENCV for c in db.read_all_cameras())
        assert db.num_keypoints() > 0
    assert json.loads((tmp_path / "ws" / "priors.json").read_text())["cam0/IMG_0000.JPG"]["centre"]
