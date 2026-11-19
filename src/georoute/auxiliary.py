"""An unsynchronised auxiliary camera (an iPhone): EXIF-ordered photos positioned from the session's
antenna track with a loose prior until its clock offset is known."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from georoute.geodesy import LocalFrame
from georoute.photos import read_photos
from georoute.rig import MAX_CAMERAS
from georoute.session import GPS_MINUS_UTC_S, Session
from georoute.trajectory import TrajectoryError, antenna_positions


class AuxiliaryError(ValueError):
    pass


# A spread wider than this means the nearest-track fit is ambiguous (a route that passes near itself).
MAX_OFFSET_SPREAD_S = 2.0


@dataclass(frozen=True)
class AuxiliaryInput:
    """Where the auxiliary photos live and what the pipeline assumes about them. The folder must be
    cam<index>, matching the pipeline's one-camera-per-folder convention."""

    folder: str
    camera: int
    clock_offset_s: float = 0.0
    sigma_m: float = 5.0
    utc_offset_s: float | None = None

    def __post_init__(self):
        if not 0 <= self.camera < MAX_CAMERAS:
            raise AuxiliaryError(f"auxiliary camera {self.camera} is outside 0..{MAX_CAMERAS - 1}")
        if self.folder != f"cam{self.camera}":
            raise AuxiliaryError(
                f"auxiliary folder {self.folder!r} must be cam{self.camera}: reports group by folder name"
            )
        if not np.isfinite(self.clock_offset_s):
            raise AuxiliaryError(f"auxiliary clock offset {self.clock_offset_s!r} is not finite")
        if not np.isfinite(self.sigma_m) or self.sigma_m <= 0:
            raise AuxiliaryError(f"auxiliary prior sigma {self.sigma_m!r} must be positive")
        if self.utc_offset_s is not None and not np.isfinite(self.utc_offset_s):
            raise AuxiliaryError(f"auxiliary UTC offset {self.utc_offset_s!r} is not finite")


def read_auxiliary_photos(images_root, folder: str, utc_offset_s: float | None = None) -> list:
    """The auxiliary folder's photos with their UTC epoch, in time order. EXIF local time is converted
    with each photo's OffsetTimeOriginal, or with utc_offset_s when the tag is absent; without either,
    refuse rather than guess the timezone. The camera number in each photo is a placeholder."""
    photos = read_photos(images_root, {0: folder}).get(0)
    if photos is None:
        raise AuxiliaryError(f"{Path(images_root) / folder} holds no photos")
    out = []
    for photo in photos:
        offset = photo.utc_offset_s if photo.utc_offset_s is not None else utc_offset_s
        if offset is None:
            raise AuxiliaryError(
                f"{photo.path} has no EXIF OffsetTimeOriginal; pass --aux-utc-offset-s (seconds, "
                "local time = UTC + offset)"
            )
        out.append((photo, photo.taken_s - offset))
    return sorted(out, key=lambda pair: (pair[1], pair[0].path))


def auxiliary_images(
    session: Session, images_root, frame: LocalFrame, spec: AuxiliaryInput
) -> tuple[list[dict], list[str]]:
    """One positioned record per auxiliary photo, plus warnings for photos dropped outside the track. EXIF
    time is converted to UTC, and GPS time adds the leap offset and the camera's clock offset. The prior
    sigma is deliberately loose until the offset is estimated from a mapped model."""
    photos = read_auxiliary_photos(images_root, spec.folder, spec.utc_offset_s)
    raw_gps = np.array([utc + GPS_MINUS_UTC_S for _, utc in photos])
    t_mid = raw_gps + spec.clock_offset_s + np.array([photo.exposure_s / 2 for photo, _ in photos])
    epochs = session.pvt.gps_time_s
    inside = (t_mid >= epochs[0]) & (t_mid <= epochs[-1])
    warnings = [
        f"{photo.path} is outside the session's track at clock offset {spec.clock_offset_s:g} s; dropped"
        for (photo, _), keep in zip(photos, inside, strict=True)
        if not keep
    ]
    photos = [item for item, keep in zip(photos, inside, strict=True) if keep]
    raw_gps, t_mid = raw_gps[inside], t_mid[inside]
    if not photos:
        raise AuxiliaryError(
            f"no auxiliary photo falls inside the session's track at clock offset "
            f"{spec.clock_offset_s:g} s; {len(warnings)} were dropped (first {warnings[0]})"
        )
    try:
        antenna = antenna_positions(session.pvt, t_mid, session.nav_period_s)
    except TrajectoryError as e:
        raise AuxiliaryError(f"auxiliary photos could not be positioned: {e}") from e
    enu = frame.from_ecef(antenna.ecef)
    velocity = antenna.velocity_ecef @ frame.ecef_from_enu
    images = []
    for k, (photo, _) in enumerate(photos):
        flags = [*antenna.flags[k], "auxiliary"]
        has_prior = not ({"no-3d-fix", "jump"} & set(flags)) and antenna.carr_soln[k] >= 1
        images.append(
            {
                "path": photo.path,
                "camera": spec.camera,
                "serial": photo.serial,
                "pulse": None,
                "delay_us": None,
                "exposure_s": photo.exposure_s,
                "t_mid_gps_s": float(t_mid[k]),
                "aux_raw_gps_s": float(raw_gps[k]),
                "clock_offset_s": float(spec.clock_offset_s),
                "auxiliary": True,
                "antenna_ecef": antenna.ecef[k].tolist(),
                "antenna_enu": enu[k].tolist(),
                "velocity_enu": velocity[k].round(4).tolist(),
                "carr_soln": int(antenna.carr_soln[k]),
                "fix_type": int(antenna.fix_type[k]),
                "h_acc_m": round(float(antenna.h_acc_m[k]), 4),
                "v_acc_m": round(float(antenna.v_acc_m[k]), 4),
                "has_prior": bool(has_prior),
                "prior_sigma_h_m": spec.sigma_m if has_prior else None,
                "prior_sigma_v_m": spec.sigma_m if has_prior else None,
                "bracket_gap_s": round(float(antenna.bracket_gap_s[k]), 6),
                "corr_age": session.pvt.corr_age[
                    int(np.searchsorted(session.pvt.gps_time_s, t_mid[k], side="right") - 1)
                ],
                "flags": flags,
            }
        )
    return images, warnings


def find_auxiliary_entry(model_name: str, entries: dict) -> dict | None:
    """The images.json entry for a model image name. A multi-session workspace prefixes the name with the
    session; the entry keeps the unprefixed camera path."""
    if model_name in entries:
        return entries[model_name]
    for path, entry in entries.items():
        if model_name.endswith("/" + path):
            return entry
    return None


def auxiliary_matched_capture(workspace, entries: dict) -> bool:
    """Whether any auxiliary image has a verified two-view match with a capture image. Mapping needs the
    auxiliary priors close enough to pair spatially; without one such match the initial clock offset is
    almost certainly far out."""
    import pycolmap

    with pycolmap.Database.open(Path(workspace) / "database.db") as db:
        names = {image.image_id: image.name for image in db.read_all_images()}
        pair_ids, geometries = db.read_two_view_geometries()
    for pair_id, geometry in zip(pair_ids, geometries, strict=True):
        if not len(geometry.inlier_matches):
            continue
        left, right = (names[i] for i in pycolmap.pair_id_to_image_pair(pair_id))
        if (find_auxiliary_entry(left, entries) is None) != (find_auxiliary_entry(right, entries) is None):
            return True
    return False


def estimate_clock_offset(
    aux: dict[str, tuple[np.ndarray, float]], track_enu: np.ndarray, track_gps_s: np.ndarray
) -> dict:
    """Clock offset from a mapped workspace: each registered auxiliary photo's reconstructed camera centre
    is matched to the nearest antenna-track epoch, and the offset is the median difference between that
    epoch's GPS time and the photo's raw GPS time. At least three photos are needed. The camera centre is
    not corrected for the phone's lever arm from the antenna; that is centimetres against a metre-scale
    track step. A p10-p90 spread wider than MAX_OFFSET_SPREAD_S refuses: the nearest-epoch fit is then
    ambiguous, typically where the route passes close to itself."""
    if len(aux) < 3:
        raise AuxiliaryError(f"only {len(aux)} registered auxiliary photos; need at least 3")
    from scipy.spatial import cKDTree

    tree = cKDTree(np.asarray(track_enu, float)[:, :2])
    offsets = []
    for centre, raw_gps in aux.values():
        _, index = tree.query(np.asarray(centre, float)[:2], k=1)
        offsets.append(float(track_gps_s[index] - raw_gps))
    values = np.array(offsets)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    p10 = float(np.percentile(values, 10))
    p90 = float(np.percentile(values, 90))
    if p90 - p10 > MAX_OFFSET_SPREAD_S:
        raise AuxiliaryError(
            f"clock offset spread p10-p90 is {p90 - p10:.2f} s (limit {MAX_OFFSET_SPREAD_S:g} s); "
            "the nearest-track fit is ambiguous, often where the route passes close to itself"
        )
    return {"offset_s": median, "images": len(values), "spread_s": mad, "p10_s": p10, "p90_s": p90}
