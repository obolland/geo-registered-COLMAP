"""Reads each camera's photos and matches them to logger pulses: for camera N, the k-th photo in shutter
order is the k-th pulse with a camN sync edge. Anything that doesn't line up refuses."""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from georoute.session import CAMERA_COUNT, Pulse, Session, SessionError

# EXIF SubSecTime has 10 ms resolution; field test 1 intervals agree with pulse intervals to within 10 ms.
INTERVAL_TOLERANCE_S = 0.03
EXIF_TAGS = [
    "FileName",
    "FileNumber",
    "SerialNumber",
    "DateTimeOriginal",
    "SubSecTimeOriginal",
    "ExposureTime",
    "FocalLength",
]
FILE_NUMBER = re.compile(r"(\d+)\D*$")


class MatchError(ValueError):
    pass


@dataclass(frozen=True)
class Photo:
    path: str
    camera: int
    file_number: int | None
    taken_s: float
    exposure_s: float
    focal_mm: float | None
    serial: str | None

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Match:
    photo: Photo
    pulse: Pulse


def read_photos(images_root, folders: dict[int, str] | None = None) -> dict[int, list[Photo]]:
    """Photos per camera from images_root/<folder>; folders default to cam0..cam3, absent ones are skipped."""
    if shutil.which("exiftool") is None:
        raise MatchError("exiftool is not installed")
    root = Path(images_root)
    folders = folders or {c: f"cam{c}" for c in range(CAMERA_COUNT)}
    out = {}
    for camera, folder in folders.items():
        if (root / folder).is_dir():
            result = subprocess.run(
                ["exiftool", "-json", "-n", "-ext", "jpg", *(f"-{t}" for t in EXIF_TAGS), str(root / folder)],
                capture_output=True,
                text=True,
            )
            records = json.loads(result.stdout) if result.stdout.strip() else []
            out[camera] = photos_from_exiftool(records, camera, root)
    return out


def photos_from_exiftool(records: list[dict], camera: int, root: Path) -> list[Photo]:
    photos = []
    for r in records:
        path = Path(r["SourceFile"]).relative_to(root).as_posix()
        for tag in ("DateTimeOriginal", "ExposureTime"):
            if tag not in r:
                raise MatchError(f"{path} has no EXIF {tag}")
        taken = datetime.strptime(r["DateTimeOriginal"], "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC).timestamp()
        subsec = str(r.get("SubSecTimeOriginal", ""))
        taken += float(f"0.{subsec}") if subsec else 0.0
        serial = r.get("SerialNumber")
        photos.append(
            Photo(
                path,
                camera,
                r.get("FileNumber"),
                taken,
                float(r["ExposureTime"]),
                r.get("FocalLength"),
                None if serial is None else str(serial),
            )
        )
    return photos


def order_photos(photos: list[Photo]) -> list[Photo]:
    """Shutter order: Canon FileNumber when every photo has one, else the filename number with the
    9999 to 0001 rollover unwrapped. The camera clock must agree."""
    if photos and all(p.file_number is not None for p in photos):
        ordered = sorted(photos, key=lambda p: p.file_number)
    else:
        by_time = sorted(photos, key=lambda p: p.taken_s)
        unwrapped, offset, previous = {}, 0, None
        for p in by_time:
            number = int(FILE_NUMBER.search(Path(p.name).stem).group(1))
            if previous is not None and number < previous - 5000:
                offset += 10000
            unwrapped[p.path], previous = number + offset, number
        ordered = sorted(photos, key=lambda p: unwrapped[p.path])
    for before, after in zip(ordered, ordered[1:], strict=False):
        if after.taken_s < before.taken_s:
            raise MatchError(f"{after.path} is earlier on the camera clock than {before.path}")
    return ordered


def match_photos(
    session: Session, photos: dict[int, list[Photo]], serials: dict[int, str] | None = None
) -> list[Match]:
    matches = []
    for camera in range(CAMERA_COUNT):
        pulses = [p for p in session.pulses if p.delays_us[camera] is not None]
        if camera not in photos:
            if pulses:
                raise MatchError(f"cam{camera}: no photo folder, but {len(pulses)} pulses have its sync edge")
            continue
        ordered = order_photos(photos[camera])
        expected = (serials or {}).get(camera)
        for p in ordered:
            if expected is not None and p.serial != expected:
                raise MatchError(f"{p.path} is from camera serial {p.serial}, cam{camera} expects {expected}")
        _check_alignment(session, camera, ordered, pulses)
        matches += [Match(photo, pulse) for photo, pulse in zip(ordered, pulses, strict=True)]
    return sorted(matches, key=lambda m: (m.pulse.n, m.photo.camera))


def _check_alignment(session: Session, camera: int, photos: list[Photo], pulses: list[Pulse]):
    summary = f"cam{camera}: {len(photos)} photos for {len(pulses)} pulses"
    for k in range(1, min(len(photos), len(pulses))):
        try:
            pulse_dt = _exposure(session, pulses[k], camera) - _exposure(session, pulses[k - 1], camera)
        except SessionError:
            continue
        if abs((photos[k].taken_s - photos[k - 1].taken_s) - pulse_dt) > INTERVAL_TOLERANCE_S:
            raise MatchError(
                f"{summary}; photos stop following the pulses at {photos[k].path} "
                f"(pulse {pulses[k].n}): a photo is missing or extra just before it"
            )
    if len(photos) != len(pulses):
        raise MatchError(f"{summary}; the sequences agree up to the end, so the difference is at the end")


def _exposure(session: Session, pulse: Pulse, camera: int) -> float:
    return session.command_time(pulse.n) + pulse.delays_us[camera] * 1e-6
