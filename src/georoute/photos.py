"""Reads each camera's photos and matches them to logger pulses: for camera N, the k-th photo in shutter
order is the k-th pulse with a camN sync edge. Anything that doesn't line up refuses.

Residual risk: a photo missing at the very start together with an extra one at the very end, with no gap
in file numbers and pulse intervals that happen to repeat to within the tolerance, can't be told apart
from a correct match."""

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from georoute.rig import MAX_CAMERAS, CameraType
from georoute.session import Pulse, Session, SessionError

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
NAME_NUMBER = re.compile(r"(\d+)$")
# A faulty camera's photos must agree with this many pulse intervals before its lost tail is accepted.
MIN_VERIFIED_INTERVALS = 10


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

    @property
    def name_number(self) -> int:
        found = NAME_NUMBER.search(Path(self.name).stem)
        if not found:
            raise MatchError(f"cam{self.camera}: {self.path} has no file number in its name")
        return int(found.group(1))


@dataclass(frozen=True)
class Match:
    photo: Photo
    pulse: Pulse


def read_photos(
    images_root, folders: dict[int, str] | None = None, warnings: list[str] | None = None
) -> dict[int, list[Photo]]:
    """Photos per camera from images_root/<folder>; folders default to cam0, cam1 and so on (up to the
    rig's camera limit), absent ones are skipped."""
    if shutil.which("exiftool") is None:
        raise MatchError("exiftool is not installed")
    root = Path(images_root)
    folders = folders or {c: f"cam{c}" for c in range(MAX_CAMERAS)}
    out = {}
    for camera, folder in folders.items():
        if (root / folder).is_dir():
            out[camera] = _read_folder(root, root / folder, camera, warnings)
    return out


def _read_folder(root: Path, folder: Path, camera: int, warnings: list[str] | None = None) -> list[Photo]:
    strays = [p.name for p in folder.iterdir() if not p.name.startswith(".") and p.suffix.lower() != ".jpg"]
    if strays:
        raise MatchError(f"cam{camera}: {folder} holds files that aren't .jpg photos: {sorted(strays)[:5]}")
    result = subprocess.run(
        [
            "exiftool",
            "-json",
            "-n",
            "-i",
            "HIDDEN",
            "-ext",
            "jpg",
            *(f"-{t}" for t in EXIF_TAGS),
            str(folder),
        ],
        capture_output=True,
        text=True,
    )
    errors = [line for line in result.stderr.splitlines() if line.startswith("Error")]
    if result.returncode != 0 or errors:
        raise MatchError(
            f"cam{camera}: exiftool couldn't read every photo: {errors or result.stderr.strip()}"
        )
    if warnings is not None:
        warnings += [
            f"cam{camera}: exiftool {line}"
            for line in result.stderr.splitlines()
            if line.startswith("Warning")
        ]
    try:
        records = json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError as e:
        raise MatchError(f"cam{camera}: exiftool output is not JSON: {e}") from e
    return photos_from_exiftool(records, camera, root)


def photos_from_exiftool(records: list[dict], camera: int, root: Path) -> list[Photo]:
    photos = []
    for r in records:
        path = Path(r["SourceFile"]).relative_to(root).as_posix()
        for tag in ("DateTimeOriginal", "ExposureTime"):
            if tag not in r:
                raise MatchError(f"cam{camera}: {path} has no EXIF {tag}")
        subsec = str(r.get("SubSecTimeOriginal", ""))
        try:
            taken = (
                datetime.strptime(r["DateTimeOriginal"], "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC).timestamp()
            )
            taken += float(f"0.{subsec}") if subsec else 0.0
            exposure = float(r["ExposureTime"])
            if not 0 < exposure < math.inf:
                raise ValueError(f"exposure {exposure} s is not a positive time")
        except ValueError as e:
            raise MatchError(f"cam{camera}: {path} has unreadable EXIF time or exposure: {e}") from None
        serial = r.get("SerialNumber")
        photos.append(
            Photo(
                path,
                camera,
                r.get("FileNumber"),
                taken,
                exposure,
                r.get("FocalLength"),
                None if serial is None else str(serial),
            )
        )
    return photos


def order_photos(photos: list[Photo], wrap: int = CameraType.file_number_wrap) -> list[Photo]:
    """Shutter order: Canon FileNumber when every photo has one, else the filename number with the
    wrap (9999 to 0001 on Canon) unwrapped. The camera clock must agree, and the sequence can't have gaps."""
    if not photos:
        return []
    with_number = [p.file_number is not None for p in photos]
    if any(with_number) and not all(with_number):
        raise MatchError(f"cam{photos[0].camera}: only some photos have a Canon FileNumber")
    unwrapped = _unwrapped_name_numbers(photos, wrap)
    key = (lambda p: p.file_number) if all(with_number) else (lambda p: unwrapped[p.path])
    ordered = sorted(photos, key=key)
    for before, after in zip(ordered, ordered[1:], strict=False):
        if after.taken_s < before.taken_s:
            raise MatchError(
                f"cam{after.camera}: {after.path} is earlier on the camera clock than {before.path}"
            )
        if not _consecutive(before, after, unwrapped, all(with_number), wrap):
            field = "Canon FileNumbers" if all(with_number) else "file numbers"
            raise MatchError(f"cam{after.camera}: {field} jump from {before.name} to {after.name}")
    return ordered


def _consecutive(
    before: Photo, after: Photo, unwrapped: dict[str, int], by_file_number: bool, wrap: int
) -> bool:
    if not by_file_number:
        return unwrapped[after.path] == unwrapped[before.path] + 1
    # Canon's FileNumber is folder * (wrap + 1) + number, and the folder advances when the number wraps.
    folder, number = divmod(before.file_number, wrap + 1)
    following = (folder + 1) * (wrap + 1) + 1 if number == wrap else before.file_number + 1
    return after.file_number == following


def _unwrapped_name_numbers(photos: list[Photo], wrap: int) -> dict[str, int]:
    unwrapped, offset, previous = {}, 0, None
    for p in sorted(photos, key=lambda p: p.taken_s):
        number = p.name_number
        if previous is not None and number < previous - wrap // 2:
            offset += wrap
        unwrapped[p.path], previous = number + offset, number
    return unwrapped


def _same_serial(a: str, b: str) -> bool:
    return a.lstrip("0") == b.lstrip("0")


def match_photos(
    session: Session,
    photos: dict[int, list[Photo]],
    serials: dict[int, str] | None = None,
    warnings: list[str] | None = None,
    faulty: set[int] | frozenset[int] = frozenset(),
    camera_types: dict[int, CameraType] | None = None,
) -> list[Match]:
    """faulty names cameras the operator says malfunctioned this session: such a camera may have no photos
    at all, or photos missing only after its last verified one. Every other check still applies.
    camera_types gives each camera's limits (shutter lag, exposure, file numbering); the default is the
    100D's."""
    matches, bodies = [], {}
    warnings = warnings if warnings is not None else []
    cameras = range(session.camera_count)
    for name, given in (
        ("photos", set(photos)),
        ("faulty", set(faulty)),
        ("camera_types", set(camera_types or {})),
    ):
        unknown = sorted(given - set(cameras))
        if unknown:
            raise MatchError(
                f"{name} names cam{unknown[0]}, but the session records {session.camera_count} cameras"
            )
    for camera in cameras:
        kind = (camera_types or {}).get(camera, CameraType())
        pulses = [p for p in session.pulses if p.delays_us[camera] is not None]
        _check_delays(camera, pulses, kind)
        if not photos.get(camera):
            if pulses and camera in faulty:
                warnings.append(
                    f"cam{camera}: no photos for its {len(pulses)} exposures (acknowledged fault)"
                )
            elif pulses:
                raise MatchError(f"cam{camera}: no photo folder, but {len(pulses)} pulses have its sync edge")
            continue
        summary = f"cam{camera}: {len(photos[camera])} photos for {len(pulses)} pulses"
        _check_exposures(camera, photos[camera], kind)
        try:
            ordered = order_photos(photos[camera], kind.file_number_wrap)
        except MatchError as e:
            raise MatchError(f"{summary}; {str(e).removeprefix(f'cam{camera}: ')}") from None
        _check_alignment(session, camera, ordered, pulses, summary, allow_lost_tail=camera in faulty)
        _check_body(camera, ordered, (serials or {}).get(camera), bodies)
        if camera in faulty and len(ordered) == len(pulses):
            warnings.append(f"cam{camera}: marked faulty but its photos are complete, so nothing was lost")
        if len(ordered) < len(pulses):
            lost = pulses[len(ordered) :]
            warnings.append(
                f"cam{camera}: its last {len(lost)} exposures have no photos, pulses "
                f"{lost[0].n}-{lost[-1].n} (acknowledged fault)"
            )
            pulses = pulses[: len(ordered)]
        if len(ordered) == 1:
            warnings.append(
                f"cam{camera}: a single photo, so its match can't be checked against pulse intervals"
            )
        matches += [Match(photo, pulse) for photo, pulse in zip(ordered, pulses, strict=True)]
    return sorted(matches, key=lambda m: (m.pulse.n, m.photo.camera))


def _check_delays(camera: int, pulses: list[Pulse], kind: CameraType):
    low, high = kind.shutter_delay_ms
    for p in pulses:
        delay_ms = p.delays_us[camera] / 1000
        if not low <= delay_ms <= high:
            raise MatchError(
                f"cam{camera}: pulse {p.n} shutter delay {delay_ms:g} ms is outside its camera type's "
                f"{low:g} to {high:g} ms"
            )


def _check_exposures(camera: int, photos: list[Photo], kind: CameraType):
    for p in photos:
        if p.exposure_s > kind.max_exposure_s:
            raise MatchError(
                f"cam{camera}: {p.path} exposure {p.exposure_s:g} s is over its camera type's "
                f"{kind.max_exposure_s:g} s"
            )


def _check_body(camera: int, photos: list[Photo], expected: str | None, bodies: dict[str, int]):
    found = {p.serial for p in photos if p.serial is not None}
    if expected is not None:
        missing = [p.path for p in photos if p.serial is None]
        if missing:
            raise MatchError(
                f"cam{camera}: {missing[0]} has no serial, so it can't be checked against {expected}"
            )
    if len({s.lstrip("0") for s in found}) > 1:
        raise MatchError(f"cam{camera} holds photos from more than one camera body: {sorted(found)}")
    for serial in found:
        if expected is not None and not _same_serial(serial, expected):
            raise MatchError(
                f"cam{camera}: photos are from camera serial {serial}, cam{camera} expects {expected}"
            )
        other = bodies.setdefault(serial.lstrip("0"), camera)
        if other != camera:
            raise MatchError(f"cam{camera} and cam{other} both hold photos from camera serial {serial}")


def _check_alignment(
    session: Session,
    camera: int,
    photos: list[Photo],
    pulses: list[Pulse],
    summary: str,
    allow_lost_tail: bool = False,
):
    """Photo intervals on the camera clock must follow exposure intervals from the logger. Pulses whose
    time mark isn't valid are bridged, comparing across them to the last valid one. With allow_lost_tail,
    fewer photos than pulses is accepted only if enough intervals agree and no later starting pulse would
    fit the photos as well, so a loss at the start can't pass as a loss at the end."""
    mismatch, compared = _first_mismatch(session, camera, photos, pulses)
    if mismatch is not None:
        raise MatchError(
            f"{summary}; photos stop following the pulses at {photos[mismatch].path} "
            f"(pulse {pulses[mismatch].n}): a photo is missing or extra just before it"
        )
    if len(photos) > 1 and compared == 0:
        raise MatchError(f"{summary}; no two pulses have valid time marks, so the match can't be verified")
    if len(photos) == len(pulses):
        return
    if not photos or not allow_lost_tail or len(photos) > len(pulses):
        raise MatchError(
            f"{summary}; intervals agree throughout, so a photo is missing or extra at the start "
            f"({photos[0].path}) or the end ({photos[-1].path})"
            if photos
            else summary
        )
    if compared < MIN_VERIFIED_INTERVALS:
        raise MatchError(
            f"{summary}; only {compared} intervals verify the photos, need {MIN_VERIFIED_INTERVALS} "
            "to accept exposures lost at the end"
        )
    for offset in range(1, len(pulses) - len(photos) + 1):
        if _first_mismatch(session, camera, photos, pulses[offset : offset + len(photos)])[0] is None:
            raise MatchError(
                f"{summary}; the photos fit the pulses starting at pulse {pulses[offset].n} as well "
                "as at the first, so it can't tell whether exposures were lost at the start or end"
            )


def _first_mismatch(session: Session, camera: int, photos: list[Photo], pulses: list[Pulse]) -> tuple:
    """Index of the first photo whose interval disagrees with its pulse (or None), and how many agreed."""
    last, compared = None, 0
    for k in range(min(len(photos), len(pulses))):
        try:
            t = _exposure(session, pulses[k], camera)
        except SessionError:
            continue
        if last is not None:
            j, t_j = last
            if abs((photos[k].taken_s - photos[j].taken_s) - (t - t_j)) > INTERVAL_TOLERANCE_S:
                return k, compared
            compared += 1
        last = (k, t)
    return None, compared


def _exposure(session: Session, pulse: Pulse, camera: int) -> float:
    return session.command_time(pulse.n) + pulse.delays_us[camera] * 1e-6
