"""COLMAP reconstruction with per-image position priors in the session's ENU frame."""

import json
from pathlib import Path

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


def prepare(
    images_json,
    images_root,
    workspace,
    rig: Rig,
    pulses: tuple[int, int] | None = None,
    max_image_size: int = 3200,
) -> dict:
    """Workspace for COLMAP: a database with one OPENCV camera per folder, SIFT features (CPU) and
    first-pass priors, plus priors.json recording them. pulses=(first, last) limits it to a subset."""
    data = json.loads(Path(images_json).read_text())
    images = [i for i in data["images"] if pulses is None or pulses[0] <= i["pulse"] <= pulses[1]]
    if not images:
        raise ValueError("no images in the requested pulse range")
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "database.db"
    db_path.unlink(missing_ok=True)

    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "OPENCV"
    extraction = pycolmap.FeatureExtractionOptions()
    extraction.use_gpu = False
    extraction.max_image_size = max_image_size
    pycolmap.extract_features(
        db_path,
        Path(images_root),
        image_names=[i["path"] for i in images],
        camera_mode=pycolmap.CameraMode.PER_FOLDER,
        reader_options=reader,
        extraction_options=extraction,
        device=pycolmap.Device.cpu,
    )

    priors = first_pass_priors(images, rig)
    with pycolmap.Database.open(db_path) as db:
        written = write_priors(db, priors)
        summary = {
            "images": db.num_images(),
            "cameras": db.num_cameras(),
            "priors": written,
            "frame_id": data["frame"]["id"],
        }
    (workspace / "priors.json").write_text(
        json.dumps(
            {name: {"centre": c.tolist(), "covariance": cov.tolist()} for name, (c, cov) in priors.items()},
            indent=1,
        )
    )
    return summary


def match(workspace, max_neighbors: int = 30, max_distance_m: float = 15.0) -> dict:
    """Matches each image against its nearest neighbours by prior position (across all cameras), then
    reports verified pairs per camera pair and the images left without any."""
    db_path = Path(workspace) / "database.db"
    pairing = pycolmap.SpatialPairingOptions()
    pairing.max_num_neighbors = max_neighbors
    pairing.max_distance = max_distance_m
    pairing.ignore_z = False
    matching = pycolmap.FeatureMatchingOptions()
    matching.use_gpu = False
    pycolmap.match_spatial(
        db_path, matching_options=matching, pairing_options=pairing, device=pycolmap.Device.cpu
    )

    with pycolmap.Database.open(db_path) as db:
        images = {i.image_id: i.name for i in db.read_all_images()}
        pair_ids, geometries = db.read_two_view_geometries()
    by_pair: dict[str, int] = {}
    connected = set()
    for pair_id, geometry in zip(pair_ids, geometries, strict=True):
        if len(geometry.inlier_matches) == 0:
            continue
        a, b = (images[i] for i in pycolmap.pair_id_to_image_pair(pair_id))
        key = "-".join(sorted([a.split("/")[0], b.split("/")[0]]))
        by_pair[key] = by_pair.get(key, 0) + 1
        connected |= {a, b}
    report = {
        "verified_pairs": sum(by_pair.values()),
        "by_camera_pair": dict(sorted(by_pair.items())),
        "images_without_verified_pairs": sorted(set(images.values()) - connected),
    }
    (Path(workspace) / "matching.json").write_text(json.dumps(report, indent=1))
    return report


class ReconstructionError(RuntimeError):
    pass


def reconstruct(workspace, images_root, min_registered_fraction: float = 0.95, seed: int = 0) -> dict:
    """Incremental mapping with the position priors, writing the model to workspace/sparse/0. Refuses if
    the photos split into several models or too few register; mapping.json records the outcome either way."""
    workspace = Path(workspace)
    options = pycolmap.IncrementalPipelineOptions()
    options.use_prior_position = True
    options.use_robust_loss_on_prior_position = True
    options.multiple_models = True
    options.random_seed = seed
    sparse = workspace / "sparse"
    sparse.mkdir(exist_ok=True)
    models = pycolmap.incremental_mapping(workspace / "database.db", Path(images_root), sparse, options)
    with pycolmap.Database.open(workspace / "database.db") as db:
        names = {i.image_id: i.name for i in db.read_all_images()}
    best = max(models.values(), key=lambda m: m.num_reg_images(), default=None)
    registered = {best.images[i].name for i in best.reg_image_ids()} if best else set()
    summary = {
        "images": len(names),
        "models": len(models),
        "model_sizes": sorted((m.num_reg_images() for m in models.values()), reverse=True),
        "registered": len(registered),
        "unregistered": sorted(set(names.values()) - registered),
        "points3D": best.num_points3D() if best else 0,
        "mean_reprojection_error_px": best.compute_mean_reprojection_error() if best else None,
    }
    (workspace / "mapping.json").write_text(json.dumps(summary, indent=1))
    if summary["models"] != 1:
        raise ReconstructionError(
            f"the photos split into {summary['models']} models {summary['model_sizes']}"
        )
    if summary["registered"] < min_registered_fraction * summary["images"]:
        raise ReconstructionError(f"only {summary['registered']} of {summary['images']} images registered")
    return summary
