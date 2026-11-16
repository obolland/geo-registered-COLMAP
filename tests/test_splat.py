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
