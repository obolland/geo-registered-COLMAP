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


@dataclass(frozen=True)
class AuxiliaryInput:
    """Where the auxiliary photos live and what the pipeline assumes about them. The folder must be
    cam<index>, matching the pipeline's one-camera-per-folder convention."""

    folder: str
    camera: int
    clock_offset_s: float = 0.0
    sigma_m: float = 5.0

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


def read_auxiliary_photos(images_root, folder: str) -> list:
    """The auxiliary folder's photos in EXIF time order. The camera number in each photo is a placeholder;
    the session's auxiliary camera index is assigned here."""
    photos = read_photos(images_root, {0: folder}).get(0)
    if photos is None:
        raise AuxiliaryError(f"{Path(images_root) / folder} holds no photos")
    return sorted(photos, key=lambda p: (p.taken_s, p.path))


def auxiliary_images(session: Session, images_root, frame: LocalFrame, spec: AuxiliaryInput) -> list[dict]:
    """One positioned record per auxiliary photo. EXIF time is UTC; GPS time adds the leap offset and the
    camera's clock offset. The prior sigma is deliberately loose until the offset is estimated from a
    mapped model."""
    photos = read_auxiliary_photos(images_root, spec.folder)
    raw_gps = np.array([p.taken_s + GPS_MINUS_UTC_S for p in photos])
    t_mid = raw_gps + spec.clock_offset_s + np.array([p.exposure_s / 2 for p in photos])
    try:
        antenna = antenna_positions(session.pvt, t_mid, session.nav_period_s)
    except TrajectoryError as e:
        raise AuxiliaryError(
            f"auxiliary photos fall outside the session's track at clock offset "
            f"{spec.clock_offset_s:g} s: {e}"
        ) from e
    enu = frame.from_ecef(antenna.ecef)
    velocity = antenna.velocity_ecef @ frame.ecef_from_enu
    images = []
    for k, photo in enumerate(photos):
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
    return images


def estimate_clock_offset(
    aux: dict[str, tuple[np.ndarray, float]], track_enu: np.ndarray, track_gps_s: np.ndarray
) -> dict:
    """Clock offset from a mapped workspace: each registered auxiliary photo's reconstructed position is
    matched to the nearest antenna-track epoch, and the offset is the median difference between that
    epoch's GPS time and the photo's raw GPS time. At least three photos are needed."""
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
    return {
        "offset_s": median,
        "images": len(values),
        "spread_s": mad,
        "p10_s": float(np.percentile(values, 10)),
        "p90_s": float(np.percentile(values, 90)),
    }
