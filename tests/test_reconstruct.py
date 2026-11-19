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
                    "carr_soln": 2,
                    "flags": [],
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


def test_match_reports_verified_pairs_per_camera_pair(tmp_path):
    import json

    from georoute.reconstruct import match, prepare

    rig = load_rig(EXAMPLE)
    images = _textured_photos(tmp_path / "images", rig)
    for k in range(4):  # cam1 sees exactly what cam0 sees, so those pairs verify
        (tmp_path / "images" / f"cam1/IMG_{k:04d}.JPG").write_bytes(
            (tmp_path / "images" / f"cam0/IMG_{k:04d}.JPG").read_bytes()
        )
    for image in images:
        image["has_prior"] = True
    (tmp_path / "images.json").write_text(json.dumps({"images": images, "frame": {"id": "test"}}))
    prepare(tmp_path / "images.json", tmp_path / "images", tmp_path / "ws", rig)
    report = match(tmp_path / "ws", max_neighbors=10, max_distance_m=50)
    assert report["verified_pairs"] >= 1
    assert report["by_camera_pair"].get("cam0-cam1", 0) >= 1
    assert set(report["images_without_verified_pairs"]) <= {i["path"] for i in images}


def _synthetic_workspace(tmp_path, frames=12):
    options = pycolmap.SyntheticDatasetOptions()
    options.num_rigs = 1
    options.num_cameras_per_rig = 1
    options.num_frames_per_rig = frames
    options.num_points3D = 400
    options.prior_position = True
    options.prior_position_coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
    (tmp_path / "ws").mkdir()
    with pycolmap.Database.open(tmp_path / "ws" / "database.db") as db:
        truth = pycolmap.synthesize_dataset(options, db)
    return truth


def test_reconstruct_recovers_a_metric_model_in_the_priors_frame(tmp_path):
    from georoute.reconstruct import reconstruct

    truth = _synthetic_workspace(tmp_path)
    summary = reconstruct(tmp_path / "ws", tmp_path)
    assert summary["models"] == 1 and summary["registered"] == summary["images"] == 12
    model = pycolmap.Reconstruction(tmp_path / "ws" / "sparse" / "0")
    for image in truth.images.values():
        mine = model.find_image_with_name(image.name)
        np.testing.assert_allclose(mine.projection_center(), image.projection_center(), atol=0.05)


def test_reconstruct_refuses_when_too_few_images_register(tmp_path):
    from georoute.reconstruct import ReconstructionError, reconstruct

    _synthetic_workspace(tmp_path)
    with pytest.raises(ReconstructionError, match="registered"):
        reconstruct(tmp_path / "ws", tmp_path, min_registered_fraction=1.01)


def test_refine_moves_priors_from_the_antenna_to_the_cameras(tmp_path):
    import json

    from georoute.reconstruct import refine
    from georoute.rig import Camera, Rig

    truth = _synthetic_workspace(tmp_path)
    (tmp_path / "ws" / "sparse" / "0").mkdir(parents=True)
    truth.write(tmp_path / "ws" / "sparse" / "0")
    rig = Rig(
        "synthetic",
        True,
        (Camera(0, "", "cam0", None, np.array([0.3, 0.1, 0.0]), (0.0, 0.0, 0.0)),),
        np.array([0.0, 0.0, 0.0]),
        1.0,
        0.0,
        "x",
    )
    record = {}
    for image in truth.images.values():
        world_from_cam = image.cam_from_world().rotation.matrix().T
        world_from_rig = world_from_cam @ rig.rig_from_camera(0).T
        antenna = image.projection_center() - world_from_rig @ rig.camera(0).position_in_rig_m
        record[image.name] = {
            "camera": 0,
            "antenna_enu": antenna.tolist(),
            "sigma_h_m": 0.01,
            "sigma_v_m": 0.01,
            "centre": antenna.tolist(),
            "covariance": (np.eye(3) * 1e-4).tolist(),
        }
    (tmp_path / "ws" / "priors.json").write_text(json.dumps(record))
    result = refine(tmp_path / "ws", rig, min_lateral_spread_m=0.0, holdout_every=0)
    assert result["iterations"] <= 4
    refined = json.loads((tmp_path / "ws" / "priors.json").read_text())
    for image in truth.images.values():
        np.testing.assert_allclose(refined[image.name]["centre"], image.projection_center(), atol=0.01)
    assert (tmp_path / "ws" / "sparse" / "refined").is_dir()


def test_extraction_uses_every_core_and_no_upsampling():
    import os

    from georoute.reconstruct import extraction_options

    options = extraction_options()
    assert options.num_threads == os.cpu_count() and options.sift.first_octave == 0 and not options.use_gpu


def test_priors_replace_the_placeholders_feature_extraction_writes(tmp_path):
    with pycolmap.Database.open(tmp_path / "database.db") as db:
        camera_id = db.write_camera(pycolmap.Camera.create_from_model_name(1, "OPENCV", 4000.0, 5184, 3456))
        image_id = db.write_image(pycolmap.Image(name="cam0/IMG_0001.JPG", camera_id=camera_id))
        placeholder = pycolmap.PosePrior()
        placeholder.corr_data_id = pycolmap.data_t(
            sensor_id=pycolmap.sensor_t(type=pycolmap.SensorType.CAMERA, id=camera_id), id=image_id
        )
        db.write_pose_prior(placeholder)
        assert write_priors(db, {"cam0/IMG_0001.JPG": (np.array([1.0, 2.0, 3.0]), np.eye(3) * 1e-4)}) == 1
        (prior,) = db.read_all_pose_priors()
        np.testing.assert_allclose(prior.position, [1.0, 2.0, 3.0])
        assert prior.coordinate_system == pycolmap.PosePriorCoordinateSystem.CARTESIAN


def test_images_without_a_prior_lose_the_extraction_placeholder(tmp_path):
    with pycolmap.Database.open(tmp_path / "database.db") as db:
        camera_id = db.write_camera(pycolmap.Camera.create_from_model_name(1, "OPENCV", 4000.0, 5184, 3456))
        for name in ("cam0/a.JPG", "cam0/b.JPG"):
            image_id = db.write_image(pycolmap.Image(name=name, camera_id=camera_id))
            placeholder = pycolmap.PosePrior()
            placeholder.corr_data_id = pycolmap.data_t(
                sensor_id=pycolmap.sensor_t(type=pycolmap.SensorType.CAMERA, id=camera_id), id=image_id
            )
            db.write_pose_prior(placeholder)
        write_priors(db, {"cam0/a.JPG": (np.zeros(3), np.eye(3) * 1e-4)})
        priors = db.read_all_pose_priors()
        assert len(priors) == 1
        assert priors[0].coordinate_system == pycolmap.PosePriorCoordinateSystem.CARTESIAN


def test_prepare_refuses_photos_that_failed_to_extract(tmp_path):
    import json

    from georoute.reconstruct import prepare

    rig = load_rig(EXAMPLE)
    images = _textured_photos(tmp_path / "images", rig)
    (tmp_path / "images" / "cam1" / "IMG_0002.JPG").write_bytes(b"not a jpeg")
    (tmp_path / "images.json").write_text(json.dumps({"images": images, "frame": {"id": "test"}}))
    with pytest.raises(ValueError, match="cam1/IMG_0002.JPG"):
        prepare(tmp_path / "images.json", tmp_path / "images", tmp_path / "ws", rig)


def test_prepare_clears_results_from_an_earlier_run(tmp_path):
    import json

    from georoute.reconstruct import prepare

    rig = load_rig(EXAMPLE)
    images = _textured_photos(tmp_path / "images", rig)
    (tmp_path / "images.json").write_text(json.dumps({"images": images, "frame": {"id": "test"}}))
    stale = tmp_path / "ws" / "sparse" / "refined"
    stale.mkdir(parents=True)
    (tmp_path / "ws" / "report.json").write_text("{}")
    prepare(tmp_path / "images.json", tmp_path / "images", tmp_path / "ws", rig)
    assert not (tmp_path / "ws" / "sparse").exists() and not (tmp_path / "ws" / "report.json").exists()


def test_global_mapping_is_aligned_to_the_priors(tmp_path):
    from georoute.reconstruct import reconstruct

    truth = _synthetic_workspace(tmp_path, frames=15)
    summary = reconstruct(tmp_path / "ws", tmp_path, mapper="global")
    assert summary["mapper"] == "global" and summary["registered"] == 15
    model = pycolmap.Reconstruction(tmp_path / "ws" / "sparse" / "0")
    for image in truth.images.values():
        mine = model.find_image_with_name(image.name)
        np.testing.assert_allclose(mine.projection_center(), image.projection_center(), atol=0.05)


def test_refine_on_a_sample_of_points_keeps_the_full_model(tmp_path):
    import json

    from georoute.reconstruct import refine
    from georoute.rig import Camera, Rig

    truth = _synthetic_workspace(tmp_path)
    (tmp_path / "ws" / "sparse" / "0").mkdir(parents=True)
    truth.write(tmp_path / "ws" / "sparse" / "0")
    rig = Rig(
        "synthetic",
        True,
        (Camera(0, "", "cam0", None, np.array([0.3, 0.1, 0.0]), (0.0, 0.0, 0.0)),),
        np.array([0.0, 0.0, 0.0]),
        1.0,
        0.0,
        "x",
    )
    record = {}
    for image in truth.images.values():
        world_from_rig = image.cam_from_world().rotation.matrix().T @ rig.rig_from_camera(0).T
        antenna = image.projection_center() - world_from_rig @ rig.camera(0).position_in_rig_m
        record[image.name] = {
            "camera": 0,
            "antenna_enu": antenna.tolist(),
            "sigma_h_m": 0.01,
            "sigma_v_m": 0.01,
            "centre": antenna.tolist(),
            "covariance": (np.eye(3) * 1e-4).tolist(),
        }
    (tmp_path / "ws" / "priors.json").write_text(json.dumps(record))
    summary = refine(tmp_path / "ws", rig, min_lateral_spread_m=0.0, max_points=150, holdout_every=0)
    assert summary["points_used"] == 150
    refined = pycolmap.Reconstruction(tmp_path / "ws" / "sparse" / "refined")
    assert refined.num_points3D() == truth.num_points3D()
    for image in truth.images.values():
        np.testing.assert_allclose(
            refined.find_image_with_name(image.name).projection_center(), image.projection_center(), atol=0.01
        )


def test_refine_holds_out_every_nth_pulse_and_report_gates_on_them(tmp_path):
    import json

    from georoute.reconstruct import refine
    from georoute.rig import Camera, Rig

    truth = _synthetic_workspace(tmp_path, frames=12)
    (tmp_path / "ws" / "sparse" / "0").mkdir(parents=True)
    truth.write(tmp_path / "ws" / "sparse" / "0")
    rig = Rig(
        "synthetic",
        True,
        (Camera(0, "", "cam0", None, np.array([0.3, 0.1, 0.0]), (0.0, 0.0, 0.0)),),
        np.array([0.0, 0.0, 0.0]),
        1.0,
        0.0,
        "x",
    )
    record = {}
    for k, image in enumerate(sorted(truth.images.values(), key=lambda i: i.name)):
        world_from_rig = image.cam_from_world().rotation.matrix().T @ rig.rig_from_camera(0).T
        antenna = image.projection_center() - world_from_rig @ rig.camera(0).position_in_rig_m
        record[image.name] = {
            "camera": 0,
            "pulse": k,
            "fixed": True,
            "antenna_enu": antenna.tolist(),
            "sigma_h_m": 0.01,
            "sigma_v_m": 0.01,
            "centre": antenna.tolist(),
            "covariance": (np.eye(3) * 1e-4).tolist(),
        }
    (tmp_path / "ws" / "priors.json").write_text(json.dumps(record))
    assert refine(tmp_path / "ws", rig, min_lateral_spread_m=0.0, holdout_every=3)["held_out"] == 4
    refined = json.loads((tmp_path / "ws" / "priors.json").read_text())
    assert sum(r["held_out"] for r in refined.values()) == 4


def test_prepare_uses_each_cameras_colmap_model(tmp_path):
    import json
    from dataclasses import replace

    from georoute.reconstruct import prepare

    rig = load_rig(EXAMPLE)
    rig = replace(
        rig, camera_types={**rig.camera_types, 1: replace(rig.camera_type(1), colmap_model="OPENCV_FISHEYE")}
    )
    images = _textured_photos(tmp_path / "images", rig)
    (tmp_path / "images.json").write_text(json.dumps({"images": images, "frame": {"id": "test"}}))
    prepare(tmp_path / "images.json", tmp_path / "images", tmp_path / "ws", rig)
    with pycolmap.Database.open(tmp_path / "ws" / "database.db") as db:
        models = {db.read_camera(i.camera_id).model for i in db.read_all_images()}
    assert models == {pycolmap.CameraModelId.OPENCV, pycolmap.CameraModelId.OPENCV_FISHEYE}


def test_refine_does_not_hold_out_an_auxiliary_image_without_a_pulse(tmp_path):
    import json

    from georoute.reconstruct import refine
    from georoute.rig import Camera, Rig

    truth = _synthetic_workspace(tmp_path)
    (tmp_path / "ws" / "sparse" / "0").mkdir(parents=True)
    truth.write(tmp_path / "ws" / "sparse" / "0")
    rig = Rig(
        "synthetic",
        True,
        (Camera(0, "", "cam0", None, np.array([0.0, 0.0, 0.0]), (0.0, 0.0, 0.0)),),
        np.array([0.0, 0.0, 0.0]),
        1.0,
        0.0,
        "x",
    )
    record = {}
    for k, image in enumerate(truth.images.values()):
        world_from_cam = image.cam_from_world().rotation.matrix().T
        world_from_rig = world_from_cam @ rig.rig_from_camera(0).T
        antenna = image.projection_center() - world_from_rig @ rig.camera(0).position_in_rig_m
        record[image.name] = {
            "camera": 0,
            "pulse": None if k == 0 else k,
            "antenna_enu": antenna.tolist(),
            "sigma_h_m": 0.01,
            "sigma_v_m": 0.01,
            "centre": antenna.tolist(),
            "covariance": (np.eye(3) * 1e-4).tolist(),
        }
    (tmp_path / "ws" / "priors.json").write_text(json.dumps(record))
    result = refine(tmp_path / "ws", rig, min_lateral_spread_m=0.0, holdout_every=10)
    assert result["images"] == len(record)
    assert result["held_out"] == sum(
        1 for r in record.values() if r["pulse"] is not None and r["pulse"] % 10 == 0
    )


def test_prepare_removes_a_stale_multi_session_overlay(tmp_path):
    import json

    from georoute.reconstruct import prepare

    rig = load_rig(EXAMPLE)
    images = _textured_photos(tmp_path / "images", rig)
    (tmp_path / "images.json").write_text(json.dumps({"images": images, "frame": {"id": "test"}}))
    ws = tmp_path / "ws"
    (ws / "images" / "s1").mkdir(parents=True)
    (ws / "rigs").mkdir()
    (ws / "sessions.json").write_text("{}")
    prepare(tmp_path / "images.json", tmp_path / "images", ws, rig)
    assert not (ws / "sessions.json").exists()
    assert not (ws / "images").exists() and not (ws / "rigs").exists()
