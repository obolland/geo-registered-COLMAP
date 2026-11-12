import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from conftest import FT1, needs_ft1, write_stub_photos

from georoute.cli import main
from georoute.ingest import IngestError, ingest, session_frame
from georoute.session import load_session

needs_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
CLOCK_OFFSET_S = -3266.44


def _stubs_for(session, root):
    triples = []
    for k, p in enumerate(q for q in session.pulses if q.delays_us[0] is not None):
        t = session.command_time(p.n) + p.delays_us[0] * 1e-6 + CLOCK_OFFSET_S
        triples.append((f"cam0/IMG_{407 + k:04d}.JPG", t, 0.001))
    write_stub_photos(root, triples)


@needs_exiftool
def test_ingest_writes_one_record_per_capture_photo(session_dir, tmp_path):
    session = load_session(session_dir)
    _stubs_for(session, tmp_path / "images")
    summary = ingest(session_dir, tmp_path / "images", tmp_path / "out")
    data = json.loads((tmp_path / "out" / "images.json").read_text())
    assert summary["images"] == 18 and summary["warmups_dropped"] == 1
    assert [i["path"] for i in data["images"][:2]] == ["cam0/IMG_0408.JPG", "cam0/IMG_0409.JPG"]
    first, pulse = data["images"][0], session.pulses[2]
    assert first["pulse"] == 2
    assert first["t_mid_gps_s"] == pytest.approx(session.command_time(2) + pulse.delays_us[0] * 1e-6 + 0.0005)
    assert data["frame"]["datum"] == "ETRS89" and data["frame"]["lat_deg"] == pytest.approx(51.15884)
    assert np.linalg.norm(first["antenna_enu"]) < 30
    assert first["flags"] == [] and first["carr_soln"] == 2
    assert (tmp_path / "out" / "track.png").stat().st_size > 1000
    assert json.loads((tmp_path / "out" / "report.json").read_text())["session"]["errors"] == []


@needs_exiftool
def test_ingest_refuses_a_session_with_integrity_errors_but_still_writes_the_report(session_dir, tmp_path):
    from conftest import edit_csv, set_cells

    _stubs_for(load_session(session_dir), tmp_path / "images")
    edit_csv(session_dir / "tm2.csv", set_cells(9, "0", [5]))
    with pytest.raises(IngestError, match="pulse 4"):
        ingest(session_dir, tmp_path / "images", tmp_path / "out")
    assert json.loads((tmp_path / "out" / "report.json").read_text())["session"]["errors"]


@needs_exiftool
def test_ingest_command_exit_codes(session_dir, tmp_path, capsys):
    _stubs_for(load_session(session_dir), tmp_path / "images")
    assert main(["ingest", str(session_dir), str(tmp_path / "images"), "--out", str(tmp_path / "out")]) == 0
    assert "18 images" in capsys.readouterr().out
    (tmp_path / "images" / "cam0" / "IMG_0412.JPG").unlink()
    assert main(["ingest", str(session_dir), str(tmp_path / "images"), "--out", str(tmp_path / "out2")]) == 1


@needs_exiftool
@needs_ft1
def test_field_test_1_positions_follow_the_walk(tmp_path):
    rows = list(csv.DictReader((Path(FT1) / "photos_exif.tsv").read_text().splitlines(), delimiter="\t"))
    triples = [
        (
            f"cam0/{r['file']}",
            datetime.strptime(r["DateTimeOriginal"], "%Y:%m:%d %H:%M:%S").timestamp()
            + int(r["SubSecTimeOriginal"]) / 100,
            0.001,
        )
        for r in rows
    ]
    write_stub_photos(tmp_path / "images", triples)
    summary = ingest(FT1, tmp_path / "images", tmp_path / "out")
    images = json.loads((tmp_path / "out" / "images.json").read_text())["images"]
    assert summary["images"] == 431
    enu = np.array([i["antenna_enu"] for i in images])
    spacing = np.linalg.norm(np.diff(enu[:, :2], axis=0), axis=1)
    assert np.median(spacing) == pytest.approx(0.81, abs=0.02)
    assert sum("not-fixed" in i["flags"] for i in images) > 0


@needs_exiftool
def test_a_refused_rerun_leaves_no_stale_outputs(session_dir, tmp_path):
    _stubs_for(load_session(session_dir), tmp_path / "images")
    ingest(session_dir, tmp_path / "images", tmp_path / "out")
    (tmp_path / "images" / "cam0" / "IMG_0412.JPG").unlink()
    with pytest.raises(Exception, match="IMG_0413"):
        ingest(session_dir, tmp_path / "images", tmp_path / "out")
    assert not (tmp_path / "out" / "images.json").exists() and not (tmp_path / "out" / "track.png").exists()
    assert "IMG_0413" in json.loads((tmp_path / "out" / "report.json").read_text())["refused"]


def test_a_session_without_any_fixed_epoch_has_no_frame(session_dir):
    from conftest import edit_csv, set_cells

    edit_csv(session_dir / "pvt.csv", set_cells(4, "1", range(1, 75)))
    with pytest.raises(IngestError, match="RTK-fixed"):
        session_frame(load_session(session_dir))


@needs_exiftool
def test_curtain_travel_option_shifts_mid_exposure(session_dir, tmp_path):
    _stubs_for(load_session(session_dir), tmp_path / "images")
    main(["ingest", str(session_dir), str(tmp_path / "images"), "--out", str(tmp_path / "a")])
    main(
        [
            "ingest",
            str(session_dir),
            str(tmp_path / "images"),
            "--out",
            str(tmp_path / "b"),
            "--curtain-travel-ms",
            "4",
        ]
    )
    a, b = (json.loads((tmp_path / x / "images.json").read_text())["images"][0] for x in "ab")
    assert a["t_mid_gps_s"] - b["t_mid_gps_s"] == pytest.approx(0.002, abs=1e-6)
