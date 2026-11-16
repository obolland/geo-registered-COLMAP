"""COLMAP reconstruction with per-image position priors in the session's ENU frame."""

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pycolmap

from georoute.colmap_report import GATES, gate, prior_residuals, rig_check, scale_against_priors
from georoute.rig import Rig, refine_priors

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
    """Writes CARTESIAN position priors, with covariance, for every named image present in the database,
    replacing every existing prior: feature extraction writes an empty one per image, and an image left
    with that UNDEFINED placeholder makes the mapper refuse the mixed coordinate systems."""
    written = 0
    db.clear_pose_priors()
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


def extraction_options(max_image_size: int = 3200) -> pycolmap.FeatureExtractionOptions:
    """CPU SIFT starting at full resolution. COLMAP's default first octave of -1 doubles each image first,
    which on this Mac made extraction about 30 times slower for about 20% more keypoints."""
    options = pycolmap.FeatureExtractionOptions()
    options.use_gpu = False
    options.max_image_size = max_image_size
    options.num_threads = os.cpu_count() or 1
    options.sift.first_octave = 0
    return options


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
    for stale in ("", "-shm", "-wal"):
        Path(f"{db_path}{stale}").unlink(missing_ok=True)
    for stale in ("priors.json", "matching.json", "mapping.json", "report.json"):
        (workspace / stale).unlink(missing_ok=True)
    shutil.rmtree(workspace / "sparse", ignore_errors=True)

    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "OPENCV"
    extraction = extraction_options(max_image_size)
    pycolmap.extract_features(
        db_path,
        Path(images_root),
        image_names=[i["path"] for i in images],
        camera_mode=pycolmap.CameraMode.PER_FOLDER,
        reader_options=reader,
        extraction_options=extraction,
        device=pycolmap.Device.cpu,
    )

    with pycolmap.Database.open(db_path) as db:
        extracted = {i.name for i in db.read_all_images()}
    missing = sorted({i["path"] for i in images} - extracted)
    if missing:
        raise ValueError(f"{len(missing)} photos failed feature extraction, first {missing[0]}")
    priors = first_pass_priors(images, rig)
    with pycolmap.Database.open(db_path) as db:
        written = write_priors(db, priors)
        summary = {
            "images": db.num_images(),
            "cameras": db.num_cameras(),
            "priors": written,
            "frame_id": data["frame"]["id"],
        }
    by_path = {i["path"]: i for i in images}
    record = {
        name: {
            "camera": by_path[name]["camera"],
            "pulse": by_path[name]["pulse"],
            "fixed": by_path[name]["carr_soln"] == 2 and "jump" not in by_path[name]["flags"],
            "antenna_enu": by_path[name]["antenna_enu"],
            "sigma_h_m": by_path[name]["prior_sigma_h_m"],
            "sigma_v_m": by_path[name]["prior_sigma_v_m"],
            "centre": c.tolist(),
            "covariance": cov.tolist(),
        }
        for name, (c, cov) in priors.items()
    }
    (workspace / "priors.json").write_text(json.dumps(record, indent=1))
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


def reconstruct(
    workspace, images_root, min_registered_fraction: float = 0.95, seed: int = 0, mapper: str = "incremental"
) -> dict:
    """Maps the workspace and writes the model to workspace/sparse/0, in the priors' frame. "incremental"
    uses the priors while mapping; "global" (much faster on long sequences, but it can't take priors) maps
    freely and is then fitted to the priors by a similarity transform, for refine to tighten. Refuses if the
    photos split into several models or too few register; mapping.json records the outcome either way."""
    workspace = Path(workspace)
    sparse = workspace / "sparse"
    sparse.mkdir(exist_ok=True)
    if mapper == "incremental":
        options = pycolmap.IncrementalPipelineOptions()
        options.use_prior_position = True
        options.use_robust_loss_on_prior_position = True
        options.multiple_models = True
        options.random_seed = seed
        models = pycolmap.incremental_mapping(workspace / "database.db", Path(images_root), sparse, options)
    elif mapper == "global":
        models = _global_mapping(workspace, Path(images_root), seed)
    else:
        raise ValueError(f"unknown mapper {mapper!r}")
    with pycolmap.Database.open(workspace / "database.db") as db:
        names = {i.image_id: i.name for i in db.read_all_images()}
    best = max(models.values(), key=lambda m: m.num_reg_images(), default=None)
    registered = {best.images[i].name for i in best.reg_image_ids()} if best else set()
    summary = {
        "mapper": mapper,
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


# Refining poses doesn't need every 3D point: the best-observed ones pin them down, and a full adjustment of
# millions of points took about an hour per round on the field test 2 loop.
MAX_REFINE_POINTS = 200_000


def refine(
    workspace,
    rig: Rig,
    tolerance_m: float = 1e-3,
    min_lateral_spread_m: float = 1.0,
    max_points: int = MAX_REFINE_POINTS,
    holdout_every: int = 10,
) -> dict:
    """Moves each prior from the nominal-attitude guess to the camera centre implied by the reconstructed
    rotation and the rig geometry, re-adjusting the poses against the new priors until they settle. The
    adjustment runs on the max_points longest-tracked points; the refined poses then go back into the full
    model, whose points are re-adjusted with the poses held fixed. Writes sparse/refined, and updates
    priors.json and the database priors. Every holdout_every-th pulse is left out of the adjustment, so its
    distance from its RTK-derived position is an honest accuracy measure (0 disables the hold-out)."""
    workspace = Path(workspace)
    full = pycolmap.Reconstruction(workspace / "sparse" / "0")
    model = pycolmap.Reconstruction(workspace / "sparse" / "0")
    points_used = _keep_longest_tracks(model, max_points)
    record = json.loads((workspace / "priors.json").read_text())
    ids = {model.images[i].name: i for i in model.reg_image_ids()}
    names = [n for n in record if n in ids]
    antenna = {n: (record[n]["camera"], np.array(record[n]["antenna_enu"])) for n in names}
    variances = {n: np.diag([record[n]["sigma_h_m"] ** 2] * 2 + [record[n]["sigma_v_m"] ** 2]) for n in names}
    held_out = {n for n in names if holdout_every and record[n]["pulse"] % holdout_every == 0}
    used = [n for n in names if n not in held_out]

    def align(centres: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        _prior_bundle_adjustment(model, {ids[n]: (centres[n], variances[n]) for n in used})
        return {n: model.images[ids[n]].cam_from_world().rotation.matrix().T for n in names}

    result = refine_priors(
        rig,
        antenna,
        align,
        tolerance_m=tolerance_m,
        min_lateral_spread_m=min_lateral_spread_m,
        initial={n: np.array(record[n]["centre"]) for n in names},
    )
    _prior_bundle_adjustment(model, {ids[n]: (result.priors[n], variances[n]) for n in used})
    for frame_id in model.reg_frame_ids():
        full.frame(frame_id).rig_from_world = model.frame(frame_id).rig_from_world
    _points_only_adjustment(full)
    (workspace / "sparse" / "refined").mkdir(parents=True, exist_ok=True)
    full.write(workspace / "sparse" / "refined")
    for n in names:
        record[n].update(
            centre=result.priors[n].tolist(),
            covariance=variances[n].tolist(),
            refined=True,
            held_out=n in held_out,
        )
    (workspace / "priors.json").write_text(json.dumps(record, indent=1))
    with pycolmap.Database.open(workspace / "database.db") as db:
        db.clear_pose_priors()
        write_priors(db, {n: (result.priors[n], variances[n]) for n in names})
    return {
        "iterations": result.iterations,
        "last_change_m": result.last_change_m,
        "images": len(names),
        "points_used": points_used,
        "held_out": len(held_out),
    }


def _keep_longest_tracks(model: pycolmap.Reconstruction, max_points: int) -> int:
    ids = list(model.point3D_ids())
    if len(ids) <= max_points:
        return len(ids)
    ids.sort(key=lambda i: model.points3D[i].track.length(), reverse=True)
    for point_id in ids[max_points:]:
        model.delete_point3D(point_id)
    return max_points


def _points_only_adjustment(model: pycolmap.Reconstruction):
    config = pycolmap.BundleAdjustmentConfig()
    for image_id in model.reg_image_ids():
        config.add_image(image_id)
    for frame_id in model.reg_frame_ids():
        config.set_constant_rig_from_world_pose(frame_id)
    for camera_id in model.cameras:
        config.set_constant_cam_intrinsics(camera_id)
    summary = pycolmap.create_default_bundle_adjuster(
        pycolmap.BundleAdjustmentOptions(), config, model
    ).solve()
    if not summary.is_solution_usable():
        raise ReconstructionError(f"point re-adjustment failed: {summary.brief_report()}")


def _prior_bundle_adjustment(
    model: pycolmap.Reconstruction, priors: dict[int, tuple[np.ndarray, np.ndarray]]
):
    config = pycolmap.BundleAdjustmentConfig()
    for image_id in model.reg_image_ids():
        config.add_image(image_id)
    pose_priors = []
    for image_id, (centre, cov) in priors.items():
        prior = pycolmap.PosePrior()
        prior.position = np.asarray(centre, float)
        prior.position_covariance = np.asarray(cov, float)
        prior.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
        image = model.images[image_id]
        prior.corr_data_id = pycolmap.data_t(
            sensor_id=pycolmap.sensor_t(type=pycolmap.SensorType.CAMERA, id=image.camera_id), id=image_id
        )
        pose_priors.append(prior)
    adjuster = pycolmap.create_pose_prior_bundle_adjuster(
        pycolmap.BundleAdjustmentOptions(),
        pycolmap.PosePriorBundleAdjustmentOptions(),
        config,
        pose_priors,
        model,
    )
    summary = adjuster.solve()
    if not summary.is_solution_usable():
        raise ReconstructionError(f"prior bundle adjustment failed: {summary.brief_report()}")


def report(workspace, rig: Rig, reference: int = 2) -> dict:
    """report.json for the refined model (or the first one if not refined): mapping outcome, per-camera
    prior residuals on RTK-fixed images, the rig check and the gate failures."""
    workspace = Path(workspace)
    which, poses, centres, record, model = _model_poses(workspace)
    priors = {n: np.array(r["centre"]) for n, r in record.items()}
    held_out = {n for n, r in record.items() if r.get("held_out")}
    residuals = prior_residuals(
        priors, centres, {n: r["fixed"] and n not in held_out for n, r in record.items()}
    )
    holdout = (
        prior_residuals(priors, centres, {n: record[n]["fixed"] for n in held_out}) if held_out else None
    )
    rig_result = rig_check(poses, rig, reference)
    cameras = sorted({f"cam{r['camera']}" for r in record.values()})
    order = {n: r["pulse"] for n, r in record.items()}
    out = {
        "model": which,
        "rig": {"name": rig.name, "sha256": rig.sha256, "measured": rig.measured},
        "gates": GATES,
        "registration": {
            c: {
                "photos": sum(n.startswith(f"{c}/") for n in record),
                "registered": sum(n.startswith(f"{c}/") for n in centres),
            }
            for c in cameras
        },
        "scale_against_rtk": scale_against_priors(priors, centres, order, f"cam{reference}"),
        "mapping": json.loads((workspace / "mapping.json").read_text()),
        "mean_reprojection_error_px": model.compute_mean_reprojection_error(),
        "prior_residuals": residuals,
        "holdout_residuals": holdout,
        "rig_check": rig_result,
        "failures": gate(holdout or residuals, rig_result, cameras),
    }
    (workspace / "report.json").write_text(json.dumps(out, indent=1))
    return out


def _model_poses(workspace: Path):
    """The refined model if there is one, else the first: its name, (pulse, camera) world poses, camera
    centres by image name, the priors record and the model itself."""
    which = "refined" if (workspace / "sparse" / "refined").is_dir() else "0"
    model = pycolmap.Reconstruction(workspace / "sparse" / which)
    record = json.loads((workspace / "priors.json").read_text())
    poses, centres = {}, {}
    for image_id in model.reg_image_ids():
        image = model.images[image_id]
        centre = image.projection_center()
        centres[image.name] = centre
        if image.name in record:
            r = record[image.name]
            poses[(r["pulse"], r["camera"])] = (image.cam_from_world().rotation.matrix().T, centre)
    return which, poses, centres, record, model


def calibrate_rig(workspace, rig_path, out_path, reference: int = 2) -> dict:
    """Calibrated rig config from the workspace's reconstruction (see calibrate.py)."""
    from georoute.calibrate import calibrate_rotations, with_orientations, write_calibrated_rig
    from georoute.rig import load_rig

    rig = load_rig(rig_path)
    _, poses, _, _, _ = _model_poses(Path(workspace))
    calibration = calibrate_rotations(poses, rig, reference)
    offset = rig_check(poses, with_orientations(rig, calibration), reference)["axial_offset_m"]
    write_calibrated_rig(rig_path, out_path, calibration, reference, offset, source=str(workspace))
    return {"cameras": calibration, "axial_offset_m": offset}


# A global model is fitted to the priors by RANSAC; an image this far from its prior is an outlier.
ALIGNMENT_MAX_ERROR_M = 0.5


def _global_mapping(workspace: Path, images_root: Path, seed: int) -> dict[int, pycolmap.Reconstruction]:
    options = pycolmap.GlobalPipelineOptions()
    options.random_seed = seed
    options.mapper.random_seed = seed
    options.mapper.global_positioning.use_gpu = False
    if hasattr(options.mapper.bundle_adjustment.ceres, "use_gpu"):
        options.mapper.bundle_adjustment.ceres.use_gpu = False
    sparse = workspace / "sparse"
    models = pycolmap.global_mapping(workspace / "database.db", images_root, sparse, options)
    with pycolmap.Database.open(workspace / "database.db") as db:
        names = {i.image_id: i.name for i in db.read_all_images()}
        priors = {
            names[p.corr_data_id.id]: np.asarray(p.position)
            for p in db.read_all_pose_priors()
            if p.coordinate_system == pycolmap.PosePriorCoordinateSystem.CARTESIAN
        }
    ransac = pycolmap.RANSACOptions()
    ransac.max_error = ALIGNMENT_MAX_ERROR_M
    for index, model in models.items():
        common = [model.images[i].name for i in model.reg_image_ids() if model.images[i].name in priors]
        sim3 = pycolmap.align_reconstruction_to_locations(
            model, common, np.array([priors[n] for n in common]), min_common_images=3, ransac_options=ransac
        )
        if sim3 is None:
            raise ReconstructionError(f"global model {index} couldn't be fitted to the priors")
        model.transform(sim3)
        (sparse / str(index)).mkdir(parents=True, exist_ok=True)
        model.write(sparse / str(index))
    return models
