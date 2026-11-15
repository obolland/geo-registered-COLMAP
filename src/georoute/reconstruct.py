"""COLMAP reconstruction with per-image position priors in the session's ENU frame."""

import numpy as np
import pycolmap

from georoute.rig import Rig

# First-pass priors assume a level rig, so allow for the bike's lean and pitch moving the lever arm.
LEVER_ALLOWANCE_M = 0.03
MIN_HEADING_SPEED_MPS = 0.3


def nominal_world_from_rig(velocity_enu: np.ndarray) -> np.ndarray:
    """A level rig facing along the horizontal velocity: columns are the rig's right, forward and up axes."""
    forward = np.array([velocity_enu[0], velocity_enu[1], 0.0])
    forward /= np.linalg.norm(forward)
    up = np.array([0.0, 0.0, 1.0])
    return np.column_stack([np.cross(forward, up), forward, up])


def first_pass_priors(images: list[dict], rig: Rig) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Camera-centre prior and covariance per image path, from its antenna position and the nominal attitude.
    Images without a prior are left out; stationary images take the heading of the nearest moving one."""
    headings = _headings(images)
    priors = {}
    for k, image in enumerate(images):
        if not image["has_prior"]:
            continue
        centre = rig.camera_centre(image["camera"], np.array(image["antenna_enu"]), headings[k])
        sigma_h, sigma_v = image["prior_sigma_h_m"], image["prior_sigma_v_m"]
        variances = np.array([sigma_h**2, sigma_h**2, sigma_v**2]) + LEVER_ALLOWANCE_M**2
        priors[image["path"]] = (centre, np.diag(variances))
    return priors


def _headings(images: list[dict]) -> list[np.ndarray]:
    velocity = np.array([i["velocity_enu"] for i in images], float)
    moving = np.flatnonzero(np.hypot(velocity[:, 0], velocity[:, 1]) >= MIN_HEADING_SPEED_MPS)
    if not len(moving):
        raise ValueError("no image was taken while moving, so there's no heading for the rig")
    order = np.arange(len(images))
    nearest = moving[np.abs(order[:, None] - moving[None, :]).argmin(axis=1)]
    return [nominal_world_from_rig(velocity[j]) for j in nearest]


def write_priors(db: pycolmap.Database, priors: dict[str, tuple[np.ndarray, np.ndarray]]) -> int:
    """Writes CARTESIAN position priors, with covariance, for every named image present in the database."""
    written = 0
    for image in db.read_all_images():
        if image.name not in priors:
            continue
        centre, cov = priors[image.name]
        prior = pycolmap.PosePrior()
        prior.position = np.asarray(centre, float)
        prior.position_covariance = np.asarray(cov, float)
        prior.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
        prior.corr_data_id = pycolmap.data_t(
            sensor_id=pycolmap.sensor_t(type=pycolmap.SensorType.CAMERA, id=image.camera_id),
            id=image.image_id,
        )
        db.write_pose_prior(prior)
        written += 1
    return written
