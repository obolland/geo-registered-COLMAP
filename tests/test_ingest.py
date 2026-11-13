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
from georoute.rig import load_rig
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


def test_a_session_without_any_3d_fix_has_no_frame(session_dir):
    from conftest import edit_csv, set_cells

    edit_csv(session_dir / "pvt.csv", set_cells(3, "2", range(1, 75)))
    edit_csv(session_dir / "pvt.csv", set_cells(4, "0", range(1, 75)))
    with pytest.raises(IngestError, match="3D fix"):
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


@needs_exiftool
def test_ingest_takes_folders_serials_and_curtain_travel_from_the_rig(session_dir, tmp_path):
    rig_text = _rig_file(tmp_path, measured=False, cameras=(0,)).read_text()
    rig_text = rig_text.replace('folder = "cam0"', 'folder = "front-left"').replace(
        "curtain_travel_ms = 0.0", "curtain_travel_ms = 4.0"
    )
    (tmp_path / "rig.toml").write_text(rig_text)
    _stubs_for(load_session(session_dir), tmp_path / "staging")
    (tmp_path / "images").mkdir()
    (tmp_path / "staging" / "cam0").rename(tmp_path / "images" / "front-left")
    assert (
        main(
            [
                "ingest",
                str(session_dir),
                str(tmp_path / "images"),
                "--out",
                str(tmp_path / "out"),
                "--rig",
                str(tmp_path / "rig.toml"),
                "--allow-unmeasured-rig",
            ]
        )
        == 0
    )
    data = json.loads((tmp_path / "out" / "images.json").read_text())
    assert data["curtain_travel_s"] == pytest.approx(0.004)
    assert data["rig"]["sha256"] and data["images"][0]["path"].startswith("front-left/")
    assert (
        main(["match", str(session_dir), str(tmp_path / "images"), "--rig", str(tmp_path / "rig.toml")]) == 0
    )


def _rig_file(tmp_path, measured=True, cameras=(0,)):
    text = (Path(__file__).parents[1] / "rigs" / "handlebar-4cam.example.toml").read_text()
    blocks = text.split("[[camera]]")
    kept = [b for b in blocks[1:] if int(b.split("index = ")[1].split()[0]) in cameras]
    text = blocks[0].replace("measured = false", f"measured = {str(measured).lower()}") + "".join(
        "[[camera]]" + b for b in kept
    )
    path = tmp_path / "rig.toml"
    path.write_text(text)
    return path


@needs_exiftool
def test_match_refuses_a_session_with_integrity_errors(session_dir, tmp_path, capsys):
    _stubs_for(load_session(session_dir), tmp_path / "images")
    meta = json.loads((session_dir / "session.json").read_text())
    meta["counts"]["pulses_lost"] = 2
    (session_dir / "session.json").write_text(json.dumps(meta))
    assert main(["match", str(session_dir), str(tmp_path / "images")]) == 1
    assert "pulses_lost" in capsys.readouterr().err


@needs_exiftool
def test_a_rig_camera_with_no_exposures_refuses(session_dir, tmp_path):
    _stubs_for(load_session(session_dir), tmp_path / "images")
    with pytest.raises(IngestError, match="cam1.*no exposures"):
        ingest(
            session_dir,
            tmp_path / "images",
            tmp_path / "out",
            rig=load_rig(_rig_file(tmp_path, measured=False, cameras=(0, 1))),
            allow_unmeasured_rig=True,
        )


@needs_exiftool
def test_an_unmeasured_rig_refuses_unless_allowed(session_dir, tmp_path):
    _stubs_for(load_session(session_dir), tmp_path / "images")
    rig = load_rig(_rig_file(tmp_path, measured=False))
    with pytest.raises(IngestError, match="unmeasured"):
        ingest(session_dir, tmp_path / "images", tmp_path / "out", rig=rig)
    assert ingest(session_dir, tmp_path / "images", tmp_path / "out", rig=rig, allow_unmeasured_rig=True)[
        "images"
    ]


@needs_exiftool
def test_a_camera_that_stops_early_warns(session_dir, tmp_path):
    from conftest import edit_csv

    def stop_cam0(rows):
        for r in rows[6:]:
            r[3] = ""
        return rows

    edit_csv(session_dir / "pulses.csv", stop_cam0)
    _stubs_for(load_session(session_dir), tmp_path / "images")
    ingest(session_dir, tmp_path / "images", tmp_path / "out")
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert any("cam0 stopped" in w for w in report["ingest"]["warnings"])


@needs_exiftool
def test_an_all_float_session_is_kept_with_a_warning(session_dir, tmp_path):
    from conftest import edit_csv, set_cells

    edit_csv(session_dir / "pvt.csv", set_cells(4, "1", range(1, 75)))
    _stubs_for(load_session(session_dir), tmp_path / "images")
    summary = ingest(session_dir, tmp_path / "images", tmp_path / "out")
    assert summary["images"] == 18
    assert any("no RTK-fixed epoch" in w for w in summary["warnings"])


@needs_exiftool
def test_images_carry_prior_semantics_and_provenance(session_dir, tmp_path):
    from conftest import edit_csv, set_cells

    edit_csv(session_dir / "pvt.csv", set_cells(4, "0", range(20, 30)))
    _stubs_for(load_session(session_dir), tmp_path / "images")
    ingest(session_dir, tmp_path / "images", tmp_path / "out")
    data = json.loads((tmp_path / "out" / "images.json").read_text())
    without = [i for i in data["images"] if i["carr_soln"] == 0]
    assert without and all(not i["has_prior"] for i in without)
    fixed = next(i for i in data["images"] if i["carr_soln"] == 2)
    assert fixed["has_prior"] and fixed["prior_sigma_h_m"] == pytest.approx(fixed["h_acc_m"])
    assert set(data["inputs"]) >= {"pulses.csv", "tm2.csv", "pvt.csv", "session.json"}
    assert len(data["inputs"]["pvt.csv"]) == 64 and data["generated_utc"].endswith("Z")
    assert data["frame"]["id"] and "authoritative" in data["coordinates"]


def test_invalid_curtain_travel_refuses(session_dir, tmp_path):
    for value in (float("nan"), -1.0, 0.2):
        with pytest.raises(IngestError, match="curtain"):
            ingest(session_dir, tmp_path / "images", tmp_path / "out", curtain_travel_s=value)


def test_an_unwritable_output_is_a_clean_error(session_dir, tmp_path, capsys):
    (tmp_path / "file").write_text("")
    assert main(["ingest", str(session_dir), str(tmp_path), "--out", str(tmp_path / "file" / "out")]) == 1
    assert "ERROR" in capsys.readouterr().err


@needs_exiftool
def test_without_a_rig_images_json_says_so(session_dir, tmp_path):
    _stubs_for(load_session(session_dir), tmp_path / "images")
    ingest(session_dir, tmp_path / "images", tmp_path / "out")
    assert json.loads((tmp_path / "out" / "images.json").read_text())["rig"] is None


def test_a_missing_rig_file_is_a_clean_error(session_dir, tmp_path, capsys):
    assert main(["match", str(session_dir), str(tmp_path), "--rig", str(tmp_path / "nope.toml")]) == 1
    assert "nope.toml" in capsys.readouterr().err


@needs_exiftool
def test_an_explicit_zero_curtain_travel_overrides_the_rig(session_dir, tmp_path):
    rig_path = _rig_file(tmp_path, measured=False, cameras=(0,))
    rig_path.write_text(rig_path.read_text().replace("curtain_travel_ms = 0.0", "curtain_travel_ms = 4.0"))
    _stubs_for(load_session(session_dir), tmp_path / "images")
    ingest(
        session_dir,
        tmp_path / "images",
        tmp_path / "out",
        curtain_travel_s=0.0,
        rig=load_rig(rig_path),
        allow_unmeasured_rig=True,
    )
    assert json.loads((tmp_path / "out" / "images.json").read_text())["curtain_travel_s"] == 0


@needs_exiftool
def test_zero_capture_photos_refuses(session_dir, tmp_path):
    from conftest import edit_csv

    def all_warmup(rows):
        for r in rows[1:]:
            r[1] = "warmup"
        return rows

    edit_csv(session_dir / "pulses.csv", all_warmup)
    _stubs_for(load_session(session_dir), tmp_path / "images")
    with pytest.raises(IngestError, match="no capture photos"):
        ingest(session_dir, tmp_path / "images", tmp_path / "out")


@needs_exiftool
def test_a_jump_removes_the_prior(session_dir, tmp_path):
    from conftest import edit_csv

    session = load_session(session_dir)
    first = session.pulses[2]
    epoch = int(np.searchsorted(session.pvt.gps_time_s, session.command_time(2) + first.delays_us[0] * 1e-6))

    def shift(rows):
        for r in rows[epoch + 1 :]:
            r[8] = f"{float(r[8]) + 1.5 / (111320 * 0.6269):.7f}"
        return rows

    edit_csv(session_dir / "pvt.csv", shift)
    _stubs_for(load_session(session_dir), tmp_path / "images")
    ingest(session_dir, tmp_path / "images", tmp_path / "out")
    images = json.loads((tmp_path / "out" / "images.json").read_text())["images"]
    jumped = [i for i in images if "jump" in i["flags"]]
    assert jumped and not any(i["has_prior"] for i in jumped)
