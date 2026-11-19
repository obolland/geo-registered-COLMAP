import json
import shutil

import numpy as np
import pytest
from conftest import write_stub_photos

from georoute.auxiliary import AuxiliaryError, AuxiliaryInput, estimate_clock_offset
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
        aux=AuxiliaryInput("cam4", 4, CORRECTION_S, 5.0),
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
    }


@needs_exiftool
def test_an_auxiliary_photo_outside_the_track_refuses(session_dir, tmp_path):
    session = load_session(session_dir)
    _capture_stubs(session, tmp_path / "images")
    write_stub_photos(
        tmp_path / "images",
        [("cam4/IMG_0001.JPG", session.pvt.gps_time_s[0] - 18.0 - 3600.0, 0.001)],
    )
    with pytest.raises(AuxiliaryError, match="outside the session's track"):
        ingest(
            session_dir,
            tmp_path / "images",
            tmp_path / "out",
            aux=AuxiliaryInput("cam4", 4, 0.0, 5.0),
        )


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
        aux=AuxiliaryInput("cam4", 4, CORRECTION_S, 5.0),
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
    with pycolmap.Database.open(tmp_path / "ws" / "database.db") as db:
        folders = {image.name.split("/")[0] for image in db.read_all_images()}
    assert folders == {"cam0", "cam4"}

    subset = prepare(
        tmp_path / "out" / "images.json", tmp_path / "images", tmp_path / "subset", rig, pulses=(2, 4)
    )
    assert subset["cameras"] == 1 and "cam4" not in json.dumps(
        json.loads((tmp_path / "subset" / "priors.json").read_text())
    )
