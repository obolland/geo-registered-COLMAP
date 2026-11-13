import csv
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import FIXTURE as FIXTURE_DIR
from conftest import FT1, edit_csv, needs_ft1, set_cells
from PIL import Image

from georoute.photos import MatchError, Photo, match_photos, order_photos, photos_from_exiftool, read_photos
from georoute.session import load_session

needs_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
CAMERA_CLOCK_OFFSET_S = -3266.44


def _photos_for(session, camera, offset=CAMERA_CLOCK_OFFSET_S, first_number=407):
    out = []
    for k, p in enumerate(q for q in session.pulses if q.delays_us[camera] is not None):
        t = session.command_time(p.n) + p.delays_us[camera] * 1e-6 + offset
        number = (first_number + k - 1) % 9999 + 1
        out.append(
            Photo(f"cam{camera}/IMG_{number:04d}.JPG", camera, None, round(t, 2), 0.001, 18.0, f"012{camera}")
        )
    return out


@pytest.fixture
def session(session_dir):
    return load_session(session_dir)


def test_every_photo_matches_its_pulse(session):
    matches = match_photos(session, {0: _photos_for(session, 0)})
    assert len(matches) == 19
    assert [m.pulse.n for m in matches[:2]] == [1, 2]
    assert matches[0].photo.path == "cam0/IMG_0407.JPG" and matches[0].pulse.kind == "warmup"


def test_a_missing_photo_refuses_and_says_where(session):
    photos = _photos_for(session, 0)
    del photos[7]
    with pytest.raises(MatchError, match=r"cam0: 18 photos for 19 pulses.*IMG_0415\.JPG"):
        match_photos(session, {0: photos})


def test_an_extra_photo_refuses(session):
    photos = _photos_for(session, 0)
    photos.append(Photo("cam0/IMG_0426.JPG", 0, None, photos[-1].taken_s + 0.2, 0.001, 18.0, "0123"))
    with pytest.raises(MatchError, match="20 photos for 19 pulses"):
        match_photos(session, {0: photos})


def test_a_missing_and_an_extra_photo_cannot_cancel_out(session):
    photos = _photos_for(session, 0)
    del photos[3]
    photos.append(Photo("cam0/IMG_0426.JPG", 0, None, photos[-1].taken_s + 0.6, 0.001, 18.0, "0123"))
    with pytest.raises(MatchError, match="IMG_0409.JPG to IMG_0411.JPG"):
        match_photos(session, {0: photos})


def test_photos_for_a_camera_that_took_none_refuse(session):
    with pytest.raises(MatchError, match="cam2: 3 photos for 0 pulses"):
        match_photos(session, {0: _photos_for(session, 0), 2: _photos_for(session, 0)[:3]})


def test_a_camera_that_should_have_photos_but_has_no_folder_refuses(session):
    with pytest.raises(MatchError, match="cam0"):
        match_photos(session, {})


def test_a_photo_from_the_wrong_body_refuses(session):
    photos = _photos_for(session, 0)
    with pytest.raises(MatchError, match="serial 0120.*expects 9999"):
        match_photos(session, {0: photos}, serials={0: "9999"})


def test_four_cameras_with_their_own_gaps(session):
    pulses = []
    for p in session.pulses:
        delays = list(p.delays_us)
        delays[1] = None if p.n in (0, 6) else (p.delays_us[0] or 95000) + 1000
        delays[2] = None if p.n == 0 else 99000
        delays[3] = None if p.n in (0, 1) else 101000
        pulses.append(type(p)(p.n, p.kind, p.cmd_ticks_us, tuple(delays)))
    session = type(session)(
        session.path, session.meta, session.meta_file, pulses, session.tm2, session.pvt, []
    )
    photos = {c: _photos_for(session, c, offset=CAMERA_CLOCK_OFFSET_S + c * 60) for c in range(4)}
    matches = match_photos(session, photos)
    assert {c: sum(m.photo.camera == c for m in matches) for c in range(4)} == {0: 19, 1: 18, 2: 19, 3: 18}
    assert all(m.pulse.delays_us[m.photo.camera] is not None for m in matches)


def test_order_by_canon_file_number_handles_the_9999_rollover():
    photos = [
        Photo(f"cam0/IMG_{n:04d}.JPG", 0, 1000000 + n if n > 5000 else 1010000 + n, t, 0.001, 18, None)
        for n, t in [(2, 10.0), (9998, 7.0), (1, 9.0), (9999, 8.0)]
    ]
    assert [p.path[-8:-4] for p in order_photos(photos)] == ["9998", "9999", "0001", "0002"]


def test_order_by_filename_handles_the_rollover_without_file_numbers():
    photos = [
        Photo(f"cam0/IMG_{n:04d}.JPG", 0, None, t, 0.001, 18, None)
        for n, t in [(2, 10.0), (9998, 7.0), (1, 9.0), (9999, 8.0)]
    ]
    assert [p.path[-8:-4] for p in order_photos(photos)] == ["9998", "9999", "0001", "0002"]


def test_camera_clock_running_backwards_refuses():
    photos = [Photo(f"cam0/IMG_{n:04d}.JPG", 0, None, t, 0.001, 18, None) for n, t in [(1, 5.0), (2, 4.0)]]
    with pytest.raises(MatchError, match="IMG_0002.JPG.*earlier"):
        order_photos(photos)


def test_exiftool_records_keep_subsecond_leading_zeros():
    records = [
        {
            "SourceFile": "/x/cam0/IMG_0407.JPG",
            "FileName": "IMG_0407.JPG",
            "FileNumber": 1000407,
            "SerialNumber": 123,
            "DateTimeOriginal": "2026:11:08 14:43:08",
            "SubSecTimeOriginal": "09",
            "ExposureTime": 0.001,
            "FocalLength": 18.0,
        }
    ]
    (photo,) = photos_from_exiftool(records, camera=0, root=Path("/x"))
    assert photo.path == "cam0/IMG_0407.JPG" and photo.file_number == 1000407 and photo.serial == "123"
    assert photo.taken_s % 60 == pytest.approx(8.09)


def test_missing_exposure_time_refuses():
    records = [
        {
            "SourceFile": "/x/cam0/IMG_1.JPG",
            "FileName": "IMG_1.JPG",
            "DateTimeOriginal": "2026:11:08 14:43:08",
        }
    ]
    with pytest.raises(MatchError, match="IMG_1.JPG.*ExposureTime"):
        photos_from_exiftool(records, camera=0, root=Path("/x"))


@needs_exiftool
@needs_ft1
def test_field_test_1_stub_photos_all_match(tmp_path):
    cam = tmp_path / "images" / "cam0"
    cam.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(tmp_path / "base.jpg")
    rows = list(csv.DictReader((Path(FT1) / "photos_exif.tsv").read_text().splitlines(), delimiter="\t"))
    with open(tmp_path / "tags.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SourceFile", "DateTimeOriginal", "SubSecTimeOriginal", "ExposureTime", "FocalLength"])
        for r in rows:
            shutil.copy(tmp_path / "base.jpg", cam / r["file"])
            w.writerow(
                [
                    str(cam / r["file"]),
                    r["DateTimeOriginal"],
                    r["SubSecTimeOriginal"],
                    r["ExposureTime"],
                    r["FocalLength"].split()[0],
                ]
            )
    subprocess.run(
        ["exiftool", "-q", "-overwrite_original", f"-csv={tmp_path / 'tags.csv'}", str(cam)], check=True
    )

    session = load_session(FT1)
    matches = match_photos(session, read_photos(tmp_path / "images"))
    assert len(matches) == 432
    assert sum(m.pulse.kind == "capture" for m in matches) == 431
    (cam / "IMG_0600.JPG").unlink()
    with pytest.raises(MatchError, match="IMG_0601.JPG"):
        match_photos(session, read_photos(tmp_path / "images"))


@needs_exiftool
def test_match_command_reports_and_fails_loudly(session_dir, tmp_path, capsys):
    from georoute.cli import main

    session = load_session(session_dir)
    cam = tmp_path / "images" / "cam0"
    cam.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(tmp_path / "base.jpg")
    args = []
    for p in _photos_for(session, 0):
        shutil.copy(tmp_path / "base.jpg", tmp_path / "images" / p.path)
        stamp = datetime.fromtimestamp(p.taken_s, UTC)
        args += [
            f"-DateTimeOriginal={stamp:%Y:%m:%d %H:%M:%S}",
            f"-SubSecTimeOriginal={stamp.microsecond // 10000:02d}",
            "-ExposureTime=0.001",
            str(tmp_path / "images" / p.path),
            "-execute",
        ]
    subprocess.run(["exiftool", *args, "-common_args", "-q", "-overwrite_original"], check=True)

    assert main(["match", str(session_dir), str(tmp_path / "images")]) == 0
    assert "cam0 19 photos matched (1 warm-up, 18 capture)" in capsys.readouterr().out
    (cam / "IMG_0410.JPG").unlink()
    assert main(["match", str(session_dir), str(tmp_path / "images")]) == 1
    assert "IMG_0411.JPG" in capsys.readouterr().err


def _steady(session, interval=0.6):
    """The session with every command 'interval' apart, so intervals alone can't reveal a shift."""
    t0 = session.command_time(0)
    tm2 = {n: type(t)(n, t0 + n * interval, 1, True, 10) for n, t in session.tm2.items()}
    pulses = [
        type(p)(p.n, p.kind, p.cmd_ticks_us, (95000 if p.n else None, None, None, None))
        for p in session.pulses
    ]
    return type(session)(session.path, session.meta, session.meta_file, pulses, tm2, session.pvt, [])


def test_a_gap_in_file_numbers_refuses_even_when_intervals_are_steady(session):
    steady = _steady(session)
    photos = _photos_for(steady, 0)
    del photos[9]
    with pytest.raises(MatchError, match="cam0.*file numbers jump from IMG_0415.JPG to IMG_0417.JPG"):
        match_photos(steady, {0: photos})


def test_count_difference_at_the_ends_names_both_ends(session):
    steady = _steady(session)
    photos = _photos_for(steady, 0)[:-1]
    with pytest.raises(MatchError, match="IMG_0407.JPG.*IMG_0424.JPG"):
        match_photos(steady, {0: photos})


def test_invalid_time_marks_are_bridged_not_skipped(session_dir):
    edit_csv(session_dir / "tm2.csv", set_cells(9, "0", [8, 9]))
    session = load_session(session_dir)
    photos = _photos_for(load_session(FIXTURE_DIR), 0)
    del photos[8]
    with pytest.raises(MatchError, match="IMG_0416.JPG"):
        match_photos(session, {0: photos})


def test_no_valid_time_marks_cannot_be_verified(session_dir):
    edit_csv(session_dir / "tm2.csv", set_cells(9, "0", range(1, 21)))
    with pytest.raises(MatchError, match="cam0.*can't be verified"):
        match_photos(load_session(session_dir), {0: _photos_for(load_session(FIXTURE_DIR), 0)})


def test_mixed_file_numbers_refuse(session):
    photos = _photos_for(session, 0)
    photos[3] = Photo(photos[3].path, 0, 1000410, photos[3].taken_s, 0.001, 18.0, "0123")
    with pytest.raises(MatchError, match="cam0.*only some photos"):
        match_photos(session, {0: photos})


def test_a_name_without_a_number_refuses(session):
    photos = _photos_for(session, 0)
    photos[3] = Photo("cam0/holiday.JPG", 0, None, photos[3].taken_s, 0.001, 18.0, "0123")
    with pytest.raises(MatchError, match="holiday.JPG"):
        match_photos(session, {0: photos})


def test_mixed_or_shared_bodies_refuse(session):
    photos = _photos_for(session, 0)
    photos[2] = Photo(photos[2].path, 0, None, photos[2].taken_s, 0.001, 18.0, "777")
    with pytest.raises(MatchError, match="cam0 holds photos from more than one camera body"):
        match_photos(session, {0: photos})


def test_serials_compare_without_leading_zeros(session):
    assert match_photos(session, {0: _photos_for(session, 0)}, serials={0: "000120"})


def test_equal_camera_timestamps_are_allowed():
    photos = [Photo(f"cam0/IMG_{n:04d}.JPG", 0, None, 5.0, 0.001, 18, None) for n in (1, 2)]
    assert len(order_photos(photos)) == 2


@pytest.mark.parametrize(("tag", "value"), [("DateTimeOriginal", "yesterday"), ("SubSecTimeOriginal", "x9")])
def test_malformed_exif_refuses_with_the_photo(tag, value):
    record = {"SourceFile": "/x/cam0/IMG_1.JPG", "DateTimeOriginal": "2026:11:08 14:43:08"}
    record["ExposureTime"] = 0.001
    record[tag] = value
    with pytest.raises(MatchError, match="cam0/IMG_1.JPG"):
        photos_from_exiftool([record], camera=0, root=Path("/x"))


@needs_exiftool
def test_unreadable_files_and_strays_refuse(tmp_path):
    cam = tmp_path / "cam0"
    cam.mkdir()
    (cam / "IMG_0001.JPG").write_bytes(b"not a jpeg")
    with pytest.raises(MatchError, match="cam0.*IMG_0001.JPG"):
        read_photos(tmp_path)
    (cam / "IMG_0001.JPG").unlink()
    Image.new("RGB", (8, 8)).save(cam / "IMG_0002.jpeg")
    with pytest.raises(MatchError, match="cam0.*IMG_0002.jpeg"):
        read_photos(tmp_path)


def test_an_expected_serial_with_photos_lacking_one_refuses(session):
    photos = [Photo(p.path, 0, None, p.taken_s, 0.001, 18.0, None) for p in _photos_for(session, 0)]
    with pytest.raises(MatchError, match="cam0.*no serial"):
        match_photos(session, {0: photos}, serials={0: "9999"})


def test_gaps_are_checked_in_canon_file_numbers_when_ordering_by_them():
    def photo(name, number, t):
        return Photo(f"cam0/{name}", 0, number, t, 0.001, 18, None)

    with pytest.raises(MatchError, match="IMG_0003.JPG"):
        order_photos(
            [
                photo("IMG_0001.JPG", 1000001, 1.0),
                photo("IMG_0002.JPG", 1000002, 2.0),
                photo("IMG_0003.JPG", 1000004, 3.0),
            ]
        )
    assert len(order_photos([photo("IMG_9999.JPG", 1009999, 1.0), photo("IMG_0001.JPG", 1010001, 2.0)])) == 2


@pytest.mark.parametrize("exposure", [0.0, -0.001, 40.0])
def test_implausible_exposure_refuses(exposure):
    record = {"SourceFile": "/x/cam0/IMG_1.JPG", "DateTimeOriginal": "2026:11:08 14:43:08"}
    record["ExposureTime"] = exposure
    with pytest.raises(MatchError, match="cam0/IMG_1.JPG.*exposure"):
        photos_from_exiftool([record], camera=0, root=Path("/x"))


def test_a_single_photo_camera_warns_that_it_cant_be_verified(session):
    pulses = [
        type(p)(p.n, p.kind, p.cmd_ticks_us, (p.delays_us[0], 95000 if p.n == 5 else None, None, None))
        for p in session.pulses
    ]
    session = type(session)(
        session.path, session.meta, session.meta_file, pulses, session.tm2, session.pvt, []
    )
    warnings = []
    match_photos(session, {0: _photos_for(session, 0), 1: _photos_for(session, 1)}, warnings=warnings)
    assert any("cam1" in w and "single photo" in w for w in warnings)


@needs_exiftool
def test_finder_companion_files_are_ignored(tmp_path):
    cam = tmp_path / "cam0"
    cam.mkdir()
    Image.new("RGB", (8, 8)).save(cam / "IMG_0001.JPG")
    subprocess.run(
        [
            "exiftool",
            "-q",
            "-overwrite_original",
            "-DateTimeOriginal=2026:11:08 14:43:08",
            "-ExposureTime=0.001",
            str(cam / "IMG_0001.JPG"),
        ],
        check=True,
    )
    (cam / "._IMG_0001.JPG").write_bytes(b"Mac OS X        \x00\x05\x16\x07")
    assert [p.name for p in read_photos(tmp_path)[0]] == ["IMG_0001.JPG"]
