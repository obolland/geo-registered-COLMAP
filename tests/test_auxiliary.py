import json
import shutil

import numpy as np
import pytest
from conftest import write_stub_photos

from georoute.auxiliary import (
    AuxiliaryError,
    AuxiliaryInput,
    estimate_clock_offset,
    find_auxiliary_entry,
)
from georoute.cli import build_parser
from georoute.ingest import ingest
from georoute.session import load_session


def test_the_auxiliary_folder_must_match_its_camera_index():
    with pytest.raises(AuxiliaryError, match="cam4"):
        AuxiliaryInput("iPhone", 4)
    with pytest.raises(AuxiliaryError, match="positive"):
        AuxiliaryInput("cam4", 4, sigma_m=0.0)


def test_estimate_clock_offset_recovers_the_correction():
    times = np.arange(0.0, 20.0, 0.2)
    track = np.column_stack([0.8 * times, np.zeros_like(times), np.ones_like(times)])
    correction = 2.5
    aux = {
        f"cam4/IMG_{k:04d}.JPG": (track[index], times[index] - correction)
        for k, index in enumerate((10, 30, 50, 70))
    }
    result = estimate_clock_offset(aux, track, times)
    assert result["offset_s"] == pytest.approx(correction)
    assert result["images"] == 4 and result["spread_s"] == pytest.approx(0.0, abs=0.1)


def test_estimate_clock_offset_needs_a_few_registered_photos():
    with pytest.raises(AuxiliaryError, match="at least 3"):
        estimate_clock_offset({"a": (np.zeros(3), 0.0)}, np.zeros((2, 3)), np.zeros(2))


def test_aux_commands_parse():
    args = build_parser().parse_args(
        ["ingest", "session", "images", "--out", "out", "--aux-folder", "cam4", "--aux-clock-offset-s", "1.5"]
    )
    assert args.aux_folder == "cam4" and args.aux_camera == 4 and args.aux_clock_offset_s == 1.5
    utc = build_parser().parse_args(
        ["ingest", "session", "images", "--out", "out", "--aux-folder", "cam4", "--aux-utc-offset-s", "3600"]
    )
    assert utc.aux_utc_offset_s == 3600
    offset = build_parser().parse_args(["aux-offset", "ws", "--images", "i.json", "--session", "s"])
    assert offset.command == "aux-offset"


needs_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
CORRECTION_S = 2.5


def _capture_stubs(session, root):
    """cam0's DSLR photos, so ingest has a normal capture set around the auxiliary photos."""
    triples = []
    for k, pulse in enumerate(p for p in session.pulses if p.delays_us[0] is not None):
        taken = session.command_time(pulse.n) + pulse.delays_us[0] * 1e-6 - 3266.44
        triples.append((f"cam0/IMG_{407 + k:04d}.JPG", taken, 0.001))
    write_stub_photos(root, triples)


@needs_exiftool
def test_auxiliary_photos_are_ingested_with_a_loose_prior(session_dir, tmp_path):
    session = load_session(session_dir)
    track = session.pvt.gps_time_s
    chosen = [track[10], track[25], track[40], track[55], track[70]]
    _capture_stubs(session, tmp_path / "images")
    write_stub_photos(
        tmp_path / "images",
        [(f"cam4/IMG_{k:04d}.JPG", t - 18.0 - CORRECTION_S, 0.001) for k, t in enumerate(chosen)],
    )
    summary = ingest(
        session_dir,
        tmp_path / "images",
        tmp_path / "out",
        aux=AuxiliaryInput("cam4", 4, CORRECTION_S, 5.0, utc_offset_s=0.0),
    )
    data = json.loads((tmp_path / "out" / "images.json").read_text())
    aux = [image for image in data["images"] if image.get("auxiliary")]
    assert len(aux) == 5 and summary["auxiliary"]["images"] == 5
    assert summary["per_camera"]["cam4"] == 5
    for image, true_time in zip(aux, chosen, strict=True):
        assert image["camera"] == 4 and image["pulse"] is None and image["has_prior"]
        assert image["t_mid_gps_s"] == pytest.approx(true_time, abs=0.02)
        assert image["prior_sigma_h_m"] == 5.0
        assert "auxiliary" in image["flags"]
    assert data["auxiliary"] == {
        "folder": "cam4",
        "camera": 4,
        "clock_offset_s": CORRECTION_S,
        "sigma_m": 5.0,
        "images": 5,
        "without_prior": 0,
        "dropped": 0,
    }


@needs_exiftool
def test_an_auxiliary_photo_outside_the_track_refuses(session_dir, tmp_path):
    session = load_session(session_dir)
    _capture_stubs(session, tmp_path / "images")
    write_stub_photos(
        tmp_path / "images",
        [("cam4/IMG_0001.JPG", session.pvt.gps_time_s[0] - 18.0 - 3600.0, 0.001)],
    )
    with pytest.raises(AuxiliaryError, match="inside the session's track"):
        ingest(
            session_dir,
            tmp_path / "images",
            tmp_path / "out",
            aux=AuxiliaryInput("cam4", 4, 0.0, 5.0, utc_offset_s=0.0),
        )


@needs_exiftool
def test_one_auxiliary_photo_outside_the_track_is_dropped_with_a_warning(session_dir, tmp_path):
    session = load_session(session_dir)
    track = session.pvt.gps_time_s
    _capture_stubs(session, tmp_path / "images")
    write_stub_photos(
        tmp_path / "images",
        [
            ("cam4/IMG_0001.JPG", track[10] - 18.0 - CORRECTION_S, 0.001),
            ("cam4/IMG_0002.JPG", track[0] - 18.0 - 3600.0, 0.001),
        ],
    )
    summary = ingest(
        session_dir,
        tmp_path / "images",
        tmp_path / "out",
        aux=AuxiliaryInput("cam4", 4, CORRECTION_S, 5.0, utc_offset_s=0.0),
    )
    assert summary["auxiliary"]["images"] == 1 and summary["auxiliary"]["dropped"] == 1
    assert any("cam4/IMG_0002.JPG" in warning for warning in summary["warnings"])


@needs_exiftool
def test_prepare_maps_auxiliary_images_into_their_own_camera(session_dir, tmp_path):
    from dataclasses import replace

    import pycolmap
    from test_rig import EXAMPLE

    from georoute.reconstruct import prepare
    from georoute.rig import Camera, load_rig

    session = load_session(session_dir)
    track = session.pvt.gps_time_s
    _capture_stubs(session, tmp_path / "images")
    write_stub_photos(
        tmp_path / "images",
        [
            ("cam4/IMG_0001.JPG", track[40] - 18.0 - CORRECTION_S, 0.001),
            ("cam4/IMG_0002.JPG", track[50] - 18.0 - CORRECTION_S, 0.001),
            ("cam4/IMG_0003.JPG", track[60] - 18.0 - CORRECTION_S, 0.001),
        ],
    )
    ingest(
        session_dir,
        tmp_path / "images",
        tmp_path / "out",
        aux=AuxiliaryInput("cam4", 4, CORRECTION_S, 5.0, utc_offset_s=0.0),
    )
    rig = load_rig(EXAMPLE)
    rig = replace(
        rig,
        cameras=rig.cameras
        + (Camera(4, "auxiliary", "cam4", None, np.array([0.1, 0.2, 0.3]), (0.0, 0.0, 0.0)),),
    )
    summary = prepare(tmp_path / "out" / "images.json", tmp_path / "images", tmp_path / "ws", rig)
    assert summary["images"] == 21 and summary["cameras"] == 2
    record = json.loads((tmp_path / "ws" / "priors.json").read_text())
    assert record["cam4/IMG_0001.JPG"]["pulse"] is None
    assert record["cam4/IMG_0001.JPG"]["centre"]
    assert record["cam4/IMG_0001.JPG"]["auxiliary"] is True
    assert "auxiliary" not in record["cam0/IMG_0408.JPG"]  # capture records stay as before slice 9
    with pycolmap.Database.open(tmp_path / "ws" / "database.db") as db:
        folders = {image.name.split("/")[0] for image in db.read_all_images()}
    assert folders == {"cam0", "cam4"}

    subset = prepare(
        tmp_path / "out" / "images.json", tmp_path / "images", tmp_path / "subset", rig, pulses=(2, 4)
    )
    assert subset["cameras"] == 1 and "cam4" not in json.dumps(
        json.loads((tmp_path / "subset" / "priors.json").read_text())
    )


def _write_model_for_record(workspace, record, aux_shift=None):
    import pycolmap

    model = pycolmap.Reconstruction()
    camera_ids = {}
    for folder in sorted({name.split("/")[0] for name in record}):
        camera_id = len(camera_ids) + 1
        model.add_camera_with_trivial_rig(
            pycolmap.Camera(
                model="OPENCV",
                width=8,
                height=8,
                params=[6.0, 6.0, 4.0, 4.0, 0.0, 0.0, 0.0, 0.0],
                camera_id=camera_id,
            )
        )
        camera_ids[folder] = camera_id
    image_ids = {}
    for image_id, (name, item) in enumerate(record.items(), 1):
        centre = np.asarray(item["centre"], float)
        if aux_shift is not None and item.get("auxiliary"):
            centre = centre + aux_shift
        pose = pycolmap.Rigid3d(
            matrix=np.array([[1, 0, 0, -centre[0]], [0, 1, 0, -centre[1]], [0, 0, 1, -centre[2]]])
        )
        points2d = [pycolmap.Point2D(xy=[2.0 + k, 2.0], point3D_id=k + 1) for k in range(4)]
        image = pycolmap.Image(
            name=name,
            points2D=points2d,
            camera_id=camera_ids[name.split("/")[0]],
            image_id=image_id,
        )
        model.add_image_with_trivial_frame(image, pose)
        image_ids[name] = image_id
    xyz = np.median([item["centre"] for item in record.values()], axis=0)
    for k in range(4):
        point = pycolmap.Point3D()
        point.xyz = (xyz + [float(k), 0.0, 0.0]).tolist()
        point.color = [0, 0, 0]
        point.error = 1.0
        track = pycolmap.Track()
        for image_id in image_ids.values():
            track.add_element(pycolmap.TrackElement(image_id=image_id, point2D_idx=k))
        point.track = track
        model.add_point3D_with_id(k + 1, point)
    (workspace / "sparse" / "refined").mkdir(parents=True)
    model.write(workspace / "sparse" / "refined")
    (workspace / "mapping.json").write_text(
        json.dumps({"models": 1, "images": len(record), "registered": len(record)})
    )


@needs_exiftool
def test_the_prepared_record_drives_report_and_export_for_auxiliary_images(session_dir, tmp_path):
    from dataclasses import replace

    from test_rig import EXAMPLE
    from test_splat import write_route_package

    from georoute.reconstruct import prepare, report
    from georoute.rig import Camera, load_rig
    from georoute.splat import export_block

    session = load_session(session_dir)
    track = session.pvt.gps_time_s
    _capture_stubs(session, tmp_path / "images")
    write_stub_photos(
        tmp_path / "images",
        [
            ("cam4/IMG_0001.JPG", track[10] - 18.0 - CORRECTION_S, 0.001),
            ("cam4/IMG_0002.JPG", track[12] - 18.0 - CORRECTION_S, 0.001),
            ("cam4/IMG_0003.JPG", track[14] - 18.0 - CORRECTION_S, 0.001),
        ],
    )
    ingest(
        session_dir,
        tmp_path / "images",
        tmp_path / "out",
        aux=AuxiliaryInput("cam4", 4, CORRECTION_S, 5.0, utc_offset_s=0.0),
    )
    rig = load_rig(EXAMPLE)
    rig = replace(
        rig,
        cameras=rig.cameras
        + (Camera(4, "auxiliary", "cam4", None, np.array([0.1, 0.2, 0.3]), (0.0, 0.0, 0.0)),),
    )
    ws = tmp_path / "ws"
    prepare(tmp_path / "out" / "images.json", tmp_path / "images", ws, rig)
    record = json.loads((ws / "priors.json").read_text())
    _write_model_for_record(ws, record, aux_shift=np.array([2.0, 0.0, 0.0]))
    result = report(ws, rig)
    assert result["failures"] == []

    route = write_route_package(tmp_path)
    manifest = export_block(
        ws,
        tmp_path / "images",
        route,
        rig,
        tmp_path / "blocks",
        block_start=-100.0,
        block_length=200.0,
        margin=100.0,
        data_factor=4,
        holdout_every=10,
        image_radius_m=300.0,
        block_id="aux",
    )
    train = json.loads((tmp_path / "blocks" / "aux" / "train.json").read_text())
    assert manifest["counts"]["auxiliary_images"] == 3
    assert sum(name.startswith("cam4/") for name in train) == 3


def _set_offset(path, value):
    import subprocess

    subprocess.run(
        ["exiftool", f"-OffsetTimeOriginal={value}", "-overwrite_original", "-q", str(path)], check=True
    )


@needs_exiftool
def test_exif_offset_is_removed_before_the_gps_mapping(session_dir, tmp_path):
    session = load_session(session_dir)
    track = session.pvt.gps_time_s
    chosen = [track[10], track[12], track[14]]
    _capture_stubs(session, tmp_path / "images")
    local = [t - 18.0 + 3600.0 - CORRECTION_S for t in chosen]
    write_stub_photos(
        tmp_path / "images",
        [(f"cam4/IMG_{k:04d}.JPG", t, 0.001) for k, t in enumerate(local)],
    )
    for k in range(len(chosen)):
        _set_offset(tmp_path / "images" / "cam4" / f"IMG_{k:04d}.JPG", "+01:00")
    ingest(
        session_dir,
        tmp_path / "images",
        tmp_path / "out",
        aux=AuxiliaryInput("cam4", 4, CORRECTION_S, 5.0),
    )
    data = json.loads((tmp_path / "out" / "images.json").read_text())
    aux = [image for image in data["images"] if image.get("auxiliary")]
    for image, true_time in zip(aux, chosen, strict=True):
        assert image["t_mid_gps_s"] == pytest.approx(true_time, abs=0.02)
        assert image["aux_raw_gps_s"] == pytest.approx(true_time - CORRECTION_S, abs=0.02)
        assert image["flags"].count("auxiliary") == 1


@needs_exiftool
def test_a_missing_exif_offset_needs_an_explicit_utc_offset(session_dir, tmp_path):
    session = load_session(session_dir)
    track = session.pvt.gps_time_s
    _capture_stubs(session, tmp_path / "images")
    write_stub_photos(
        tmp_path / "images",
        [("cam4/IMG_0001.JPG", track[10] - 18.0 - CORRECTION_S + 3600.0, 0.001)],
    )
    with pytest.raises(AuxiliaryError, match="OffsetTimeOriginal"):
        ingest(
            session_dir,
            tmp_path / "images",
            tmp_path / "out",
            aux=AuxiliaryInput("cam4", 4, CORRECTION_S, 5.0),
        )
    ingest(
        session_dir,
        tmp_path / "images",
        tmp_path / "out2",
        aux=AuxiliaryInput("cam4", 4, CORRECTION_S, 5.0, utc_offset_s=3600.0),
    )
    data = json.loads((tmp_path / "out2" / "images.json").read_text())
    aux = [image for image in data["images"] if image.get("auxiliary")]
    assert aux[0]["t_mid_gps_s"] == pytest.approx(track[10], abs=0.02)


@needs_exiftool
def test_a_pulse_subset_keeps_auxiliary_images_inside_its_time_span(session_dir, tmp_path):
    from dataclasses import replace

    from test_rig import EXAMPLE

    from georoute.reconstruct import prepare
    from georoute.rig import Camera, load_rig

    session = load_session(session_dir)
    _capture_stubs(session, tmp_path / "images")
    inside = session.command_time(3)
    outside = session.pvt.gps_time_s[-1]
    write_stub_photos(
        tmp_path / "images",
        [
            ("cam4/IMG_0001.JPG", inside - 18.0 - CORRECTION_S, 0.001),
            ("cam4/IMG_0002.JPG", outside - 18.0 - CORRECTION_S, 0.001),
        ],
    )
    ingest(
        session_dir,
        tmp_path / "images",
        tmp_path / "out",
        aux=AuxiliaryInput("cam4", 4, CORRECTION_S, 5.0, utc_offset_s=0.0),
    )
    rig = load_rig(EXAMPLE)
    rig = replace(
        rig,
        cameras=rig.cameras
        + (Camera(4, "auxiliary", "cam4", None, np.array([0.1, 0.2, 0.3]), (0.0, 0.0, 0.0)),),
    )
    prepare(
        tmp_path / "out" / "images.json",
        tmp_path / "images",
        tmp_path / "ws",
        rig,
        pulses=(2, 4),
    )
    record = json.loads((tmp_path / "ws" / "priors.json").read_text())
    assert "cam4/IMG_0001.JPG" in record
    assert "cam4/IMG_0002.JPG" not in record


def test_auxiliary_entries_match_prefixed_model_names():
    entries = {"cam4/IMG_0001.JPG": {"auxiliary": True}}
    assert find_auxiliary_entry("cam4/IMG_0001.JPG", entries) is entries["cam4/IMG_0001.JPG"]
    assert find_auxiliary_entry("20261114T134727Z/cam4/IMG_0001.JPG", entries) is entries["cam4/IMG_0001.JPG"]
    assert find_auxiliary_entry("20261114T134727Z/cam0/IMG_0001.JPG", entries) is None


def test_a_bimodal_offset_estimate_refuses():
    times = np.arange(0.0, 20.0, 0.2)
    track = np.column_stack([0.8 * times, np.zeros_like(times), np.ones_like(times)])
    aux = {}
    for k, index in enumerate((10, 30)):
        aux[f"a{k}"] = (track[index], times[index] - 2.0)
    for k, index in enumerate((50, 70)):
        aux[f"b{k}"] = (track[index], times[index] - 12.0)
    with pytest.raises(AuxiliaryError, match="spread"):
        estimate_clock_offset(aux, track, times)


def test_auxiliary_matched_capture_requires_a_verified_cross_pair(tmp_path):
    import pycolmap

    from georoute.auxiliary import auxiliary_matched_capture

    ws = tmp_path / "ws"
    ws.mkdir()
    with pycolmap.Database.open(ws / "database.db") as db:
        camera_id = db.write_camera(pycolmap.Camera.create_from_model_name(1, "OPENCV", 100.0, 640, 480))
        camera = db.write_image(pycolmap.Image(name="cam0/a.JPG", camera_id=camera_id))
        auxiliary = db.write_image(pycolmap.Image(name="cam4/a.JPG", camera_id=camera_id))
        other = db.write_image(pycolmap.Image(name="cam0/b.JPG", camera_id=camera_id))
        geometry = pycolmap.TwoViewGeometry()
        geometry.inlier_matches = np.array([[0, 0], [1, 1]], dtype=np.uint32)
        db.write_two_view_geometry(camera, other, geometry)
    entries = {"cam4/a.JPG": {"auxiliary": True}}
    assert not auxiliary_matched_capture(ws, entries)
    with pycolmap.Database.open(ws / "database.db") as db:
        geometry = pycolmap.TwoViewGeometry()
        geometry.inlier_matches = np.array([[0, 0]], dtype=np.uint32)
        db.write_two_view_geometry(auxiliary, camera, geometry)
    assert auxiliary_matched_capture(ws, entries)
