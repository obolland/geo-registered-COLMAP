import json

import numpy as np
import pycolmap
import pytest
from test_path import FRAME

from georoute.splat import (
    SplatExportError,
    build_subset_reconstruction,
    image_names_for_pulses,
    load_route_path,
    pulse_route_s,
    select_pulses,
    split_pulses,
)


def write_route_package(tmp_path, length=10.0):
    root = tmp_path / "route"
    segment = root / "segments" / "seg_0000"
    segment.mkdir(parents=True)
    samples = [
        {"s": s, "e": s, "n": 0.0, "u_road": 0.0, "heading_rad": 0.0, "curvature_1pm": 0.0}
        for s in np.arange(0.0, length + 0.5, 0.5)
    ]
    (segment / "path.json").write_text(json.dumps({"samples": samples}))
    route = {
        "datum": FRAME.datum,
        "segments": [
            {
                "seg_id": "seg_0000",
                "s_range": [0.0, length],
                "origin": {
                    "lat_deg": FRAME.lat_deg,
                    "lon_deg": FRAME.lon_deg,
                    "h_m": FRAME.h_m,
                    "ecef": FRAME.origin_ecef.tolist(),
                },
                "path": "segments/seg_0000/path.json",
            }
        ],
    }
    (root / "route.json").write_text(json.dumps(route))
    return root


def priors_for(pulses):
    records = {}
    for pulse in pulses:
        for camera in (0, 1):
            name = f"cam{camera}/IMG_{pulse:04d}.JPG"
            records[name] = {
                "camera": camera,
                "pulse": pulse,
                "centre": [pulse * 0.5, 0.0, 0.0],
            }
    return records


def test_route_path_loading_preserves_global_s_and_common_frame(tmp_path):
    package = write_route_package(tmp_path)
    route = load_route_path(package)

    np.testing.assert_allclose(route.s, np.arange(0.0, 10.5, 0.5))
    np.testing.assert_allclose(route.enu[:, 0], route.s, atol=1e-9)


def test_pulses_map_monotonically_onto_route_distance(tmp_path):
    route = load_route_path(write_route_package(tmp_path))
    pulse_s = pulse_route_s(priors_for(range(0, 21)), route)

    assert sorted(pulse_s) == list(range(0, 21))
    assert pulse_s[0] == pytest.approx(0.0)
    assert pulse_s[10] == pytest.approx(5.0)
    assert all(pulse_s[b] >= pulse_s[a] for a, b in zip(range(20), range(1, 21), strict=True))


def test_block_selection_includes_the_margin_and_requires_a_heldout_pulse(tmp_path):
    route = load_route_path(write_route_package(tmp_path))
    pulse_s = pulse_route_s(priors_for(range(0, 21)), route)

    selected = select_pulses(pulse_s, start=2.0, length=4.0, margin=1.0)
    assert selected == list(range(2, 15))

    train, heldout = split_pulses(selected, every=10)
    assert heldout == [10]
    assert 10 not in train
    assert image_names_for_pulses(priors_for(range(21)), heldout) == [
        "cam0/IMG_0010.JPG",
        "cam1/IMG_0010.JPG",
    ]


def test_a_block_with_no_heldout_pulse_refuses(tmp_path):
    with pytest.raises(SplatExportError, match="no held-out"):
        split_pulses([11, 12, 13], every=10)


def synthetic_model(extra_points=()):
    model = pycolmap.Reconstruction()
    camera = pycolmap.Camera(
        model="OPENCV", width=64, height=48, params=[50, 50, 32, 24, 0, 0, 0, 0], camera_id=1
    )
    model.add_camera_with_trivial_rig(camera)
    point_ids = list(range(1, 5 + len(extra_points)))
    for index in range(3):
        points2d = [pycolmap.Point2D(xy=[10.0, 10.0], point3D_id=pid) for pid in point_ids]
        image = pycolmap.Image(
            name=f"cam0/IMG_{index:04d}.JPG",
            points2D=points2d,
            camera_id=1,
            image_id=index + 1,
        )
        pose = pycolmap.Rigid3d(matrix=np.array([[1, 0, 0, float(index)], [0, 1, 0, 0], [0, 0, 1, 0]]))
        model.add_image_with_trivial_frame(image, pose)
    definitions = [([float(index), 0.0, 1.0], 1.0) for index in range(4)] + list(extra_points)
    for index, (xyz, error) in enumerate(definitions):
        point = pycolmap.Point3D()
        point.xyz = xyz
        point.color = [index, index, index]
        point.error = error
        track = pycolmap.Track()
        for image_index in range(3):
            track.add_element(pycolmap.TrackElement(image_id=image_index + 1, point2D_idx=index))
        point.track = track
        model.add_point3D_with_id(index + 1, point)
    return model


def test_subset_reconstruction_keeps_only_selected_images_and_points():
    model = synthetic_model()
    subset, stats = build_subset_reconstruction(
        model,
        ["cam0/IMG_0000.JPG", "cam0/IMG_0001.JPG"],
        centres=np.array([[0.0, 0.0], [1.0, 0.0]]),
        point_radius_m=1.0,
        min_track_length=3,
        max_error_px=2.0,
    )

    assert subset.num_images() == 2
    assert subset.num_points3D() == 3
    assert stats["points"] == 3
    assert all(point.track.length() == 2 for point in subset.points3D.values())


def test_subset_reconstruction_crops_far_points_and_rejects_bad_ones():
    model = synthetic_model(extra_points=(([100.0, 100.0, 1.0], 1.0), ([0.0, 0.0, 1.0], 5.0)))

    subset, stats = build_subset_reconstruction(
        model,
        ["cam0/IMG_0000.JPG", "cam0/IMG_0001.JPG", "cam0/IMG_0002.JPG"],
        centres=np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]),
        point_radius_m=2.0,
        min_track_length=3,
        max_error_px=2.0,
    )

    assert subset.num_points3D() == 4
    assert stats["input_points"] == 6


def test_multi_session_selection_uses_distance_to_the_block_path(tmp_path):
    from georoute.splat import select_images_near_path

    record = {}
    for session, lateral in (("s1", 0.0), ("s2", 2.0)):
        for pulse in range(2, 22):
            record[f"{session}/cam0/IMG_{pulse:04d}.JPG"] = {
                "session": session,
                "camera": 0,
                "pulse": pulse,
                "centre": [0.5 * pulse, lateral, 1.0],
            }
    far = "s1/cam0/IMG_0099.JPG"
    record[far] = {"session": "s1", "camera": 0, "pulse": 99, "centre": [2.0, 100.0, 1.0]}
    route = load_route_path(write_route_package(tmp_path))
    selected = select_images_near_path(record, route, 1.0, 5.0, radius_m=2.5)
    assert far not in selected["s1"]
    assert set(selected) == {"s1", "s2"}
    pulses = {int(record[name]["pulse"]) for names in selected.values() for name in names}
    # Selection is by distance to the block's path, not by an along-route window: a photo 1.5 m past
    # the end is in, one 5 m past is out.
    assert 13 in pulses and 20 not in pulses


def test_multi_session_holdout_is_per_session():
    from georoute.splat import split_session_names

    record = {}
    selected = {}
    for session in ("s1", "s2"):
        names = []
        for pulse in range(11, 20):
            name = f"{session}/cam0/IMG_{pulse:04d}.JPG"
            record[name] = {"session": session, "camera": 0, "pulse": pulse, "centre": [0.0, 0.0, 0.0]}
            names.append(name)
        selected[session] = names
    train, heldout, by_session = split_session_names(record, selected, every=5)
    assert set(heldout) == {f"{session}/cam0/IMG_0015.JPG" for session in ("s1", "s2")}
    assert not set(train) & set(heldout)
    assert by_session["s1"]["heldout_pulses"] == [15]


def test_a_session_without_a_heldout_pulse_refuses_in_a_multi_session_block():
    from georoute.splat import split_session_names

    record = {"s1/cam0/IMG_0011.JPG": {"session": "s1", "camera": 0, "pulse": 11}}
    with pytest.raises(SplatExportError, match="held-out"):
        split_session_names(record, {"s1": ["s1/cam0/IMG_0011.JPG"]}, every=10)


def test_a_multi_session_block_holds_out_each_session_and_records_them(tmp_path):
    from PIL import Image

    from georoute.splat import export_block

    ws = tmp_path / "ws"
    (ws / "images").mkdir(parents=True)
    model = pycolmap.Reconstruction()
    record = {}
    image_id = 0
    for session, camera_id, lateral in (("s1", 1, 0.0), ("s2", 2, 2.0)):
        model.add_camera_with_trivial_rig(
            pycolmap.Camera(
                model="OPENCV",
                width=160,
                height=120,
                params=[100.0, 100.0, 80.0, 60.0, 0.0, 0.0, 0.0, 0.0],
                camera_id=camera_id,
            )
        )
        for pulse in range(11, 20):
            image_id += 1
            name = f"{session}/cam0/IMG_{pulse:04d}.JPG"
            path = ws / "images" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (160, 120), (pulse * 7 % 255, 80, 120)).save(path)
            x = 0.5 * pulse
            record[name] = {"session": session, "camera": 0, "pulse": pulse, "centre": [x, lateral, 1.0]}
            points2d = [pycolmap.Point2D(xy=[10.0 + k, 10.0], point3D_id=k + 1) for k in range(4)]
            image = pycolmap.Image(name=name, points2D=points2d, camera_id=camera_id, image_id=image_id)
            pose = pycolmap.Rigid3d(matrix=np.array([[1, 0, 0, x], [0, 1, 0, lateral], [0, 0, 1, 0]]))
            model.add_image_with_trivial_frame(image, pose)
    for k in range(4):
        point = pycolmap.Point3D()
        point.xyz = [6.0 + k, 1.0, 1.0]
        point.color = [k, k, k]
        point.error = 1.0
        track = pycolmap.Track()
        for row in range(18):
            track.add_element(pycolmap.TrackElement(image_id=row + 1, point2D_idx=k))
        point.track = track
        model.add_point3D_with_id(k + 1, point)
    (ws / "sparse" / "refined").mkdir(parents=True)
    model.write(ws / "sparse" / "refined")
    (ws / "priors.json").write_text(json.dumps(record))
    sessions = {
        "schema": "georoute.sessions/1.0.0",
        "frame": {
            "lat_deg": FRAME.lat_deg,
            "lon_deg": FRAME.lon_deg,
            "h_m": FRAME.h_m,
            "datum": FRAME.datum,
            "origin_ecef": FRAME.origin_ecef.tolist(),
        },
        "images_root": "images",
        "sessions": [
            {"label": label, "rig": {"name": f"rig-{label}", "sha256": label, "path": f"rigs/{label}.toml"}}
            for label in ("s1", "s2")
        ],
    }
    (ws / "sessions.json").write_text(json.dumps(sessions))
    route = write_route_package(tmp_path)
    out = tmp_path / "out"
    manifest = export_block(
        ws,
        ws / "images",
        route,
        rig=None,
        out=out,
        block_start=2.0,
        block_length=5.0,
        margin=1.0,
        data_factor=2,
        holdout_every=5,
        block_id="both",
        image_radius_m=5.0,
    )
    block = out / "both"
    train = json.loads((block / "train.json").read_text())
    heldout = json.loads((block / "heldout.json").read_text())
    assert heldout and train and not set(train) & set(heldout)
    assert {name.split("/")[0] for name in train + heldout} == {"s1", "s2"}
    assert all(name in heldout for name in ("s1/cam0/IMG_0015.JPG", "s2/cam0/IMG_0015.JPG"))
    assert manifest["split"]["by_session"]["s1"]["heldout_pulses"] == [15]
    assert manifest["counts"]["per_session"]["s2"]["train_images"] == 8
    assert manifest["inputs"]["sessions"][0]["label"] == "s1"
    assert (block / "images_4" / "s2" / "cam0" / "IMG_0012.png").is_file()
    assert pycolmap.Reconstruction(block / "sparse" / "0").num_cameras() == 2


def test_auxiliary_images_train_without_a_heldout_pulse():
    from georoute.splat import split_session_names

    record = {}
    selected = {}
    for session in ("s1", "s2"):
        names = []
        for pulse in (11, 12, 13):
            name = f"{session}/cam0/IMG_{pulse:04d}.JPG"
            record[name] = {"session": session, "camera": 0, "pulse": pulse}
            names.append(name)
        aux = f"{session}/cam4/IMG_0001.JPG"
        record[aux] = {"session": session, "camera": 4, "pulse": None, "auxiliary": True}
        names.append(aux)
        selected[session] = names
    train, heldout, by_session = split_session_names(record, selected, every=2)
    assert heldout == ["s1/cam0/IMG_0012.JPG", "s2/cam0/IMG_0012.JPG"]
    assert "s1/cam4/IMG_0001.JPG" in train and "s2/cam4/IMG_0001.JPG" in train
    assert by_session["s1"]["auxiliary_images"] == 1


def test_route_in_record_frame_starts_at_the_first_pulse(tmp_path):
    from georoute.splat import route_in_record_frame

    route = load_route_path(write_route_package(tmp_path))
    record = {
        "cam0/a": {"pulse": 1, "centre": [10.0, 5.0, 1.0]},
        "cam0/b": {"pulse": 2, "centre": [10.5, 5.0, 1.0]},
        "cam4/x": {"pulse": None, "centre": [10.0, 6.0, 1.0]},
    }
    shifted = route_in_record_frame(route, record)
    np.testing.assert_allclose(shifted.enu[0, :2], [10.0, 5.0], atol=1e-9)
    np.testing.assert_allclose(shifted.enu[:, 0] - shifted.enu[0, 0], route.s, atol=1e-9)
