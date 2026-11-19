"""Training-set export for one splat block, and the local data model shared by the pilot job."""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pycolmap
from PIL import Image
from scipy.spatial import cKDTree

from georoute.geodesy import LocalFrame
from georoute.rig import Rig

EXPORT_SCHEMA = "georoute.splat-export/1.0.0"
DEFAULT_DATA_FACTOR = 4
DEFAULT_POINT_RADIUS_M = 60.0
MIN_TRACK_LENGTH = 3
MAX_REPROJECTION_ERROR_PX = 2.0


class SplatExportError(ValueError):
    pass


@dataclass(frozen=True)
class RoutePath:
    datum: str
    s: np.ndarray
    enu: np.ndarray


def load_route_path(package, frame: LocalFrame | None = None) -> RoutePath:
    """The published route path in one ENU frame, with segment-local samples converted through ECEF.
    frame, when given, is the workspace's common frame instead of the first segment's own frame."""
    package = Path(package)
    route = json.loads((package / "route.json").read_text())
    if frame is not None and frame.datum != route["datum"]:
        raise SplatExportError(f"route datum {route['datum']} and workspace frame {frame.datum} disagree")
    first_frame = None
    s_values, points = [], []
    for segment in route["segments"]:
        origin = segment["origin"]
        segment_frame = LocalFrame.at(origin["lat_deg"], origin["lon_deg"], origin["h_m"], route["datum"])
        if first_frame is None:
            first_frame = frame or segment_frame
        samples = json.loads((package / segment["path"]).read_text())["samples"]
        local = np.array([[sample["e"], sample["n"], sample["u_road"]] for sample in samples])
        common = first_frame.from_ecef(segment_frame.to_ecef(local))
        s_values.append(np.array([sample["s"] for sample in samples]))
        points.append(common)
    s = np.concatenate(s_values)
    enu = np.vstack(points)
    order = np.argsort(s)
    return RoutePath(route["datum"], s[order], enu[order])


def pulse_route_s(priors: dict, route: RoutePath) -> dict[int, float]:
    """Map each capture pulse onto route distance by translating the camera-centre track onto the route
    path: both are session ENU, only the origin differs. The nearest path point is chosen monotonically, so a
    folded route can't snap back to an earlier branch."""
    centres = {}
    for record in priors.values():
        centres.setdefault(int(record["pulse"]), []).append(np.asarray(record["centre"], float)[:2])
    centres = {pulse: np.median(values, axis=0) for pulse, values in centres.items()}
    pulses = sorted(centres)
    if not pulses:
        raise SplatExportError("priors.json has no images")
    translation = route.enu[0, :2] - centres[pulses[0]]
    route_xy = route.enu[:, :2]
    result = {}
    last_s = -np.inf
    for pulse in pulses:
        position = centres[pulse] + translation
        candidates = np.flatnonzero((route.s >= last_s - 2.0) & (route.s <= last_s + 50.0))
        if not len(candidates):
            candidates = np.flatnonzero(route.s >= last_s - 2.0)
        if not len(candidates):
            candidates = np.arange(len(route.s))
        distances = np.linalg.norm(route_xy[candidates] - position, axis=1)
        value = float(route.s[candidates[int(np.argmin(distances))]])
        if value < last_s:
            value = float(last_s)
        result[pulse] = value
        last_s = value
    return result


def select_pulses(pulse_s: dict[int, float], start: float, length: float, margin: float) -> list[int]:
    if not np.isfinite(start) or not np.isfinite(length) or length <= 0:
        raise SplatExportError(
            f"block start/length must be finite and length positive, got {start}, {length}"
        )
    if not np.isfinite(margin) or margin < 0:
        raise SplatExportError(f"margin must be zero or positive, got {margin}")
    low, high = start - margin, start + length + margin
    selected = sorted(pulse for pulse, s in pulse_s.items() if low <= s <= high)
    if not selected:
        raise SplatExportError(f"no capture pulses between s={low:.3f} and {high:.3f}")
    return selected


def split_pulses(pulses: list[int], every: int = 10) -> tuple[list[int], list[int]]:
    if not isinstance(every, int) or every < 2:
        raise SplatExportError(f"held-out interval must be an integer of at least 2, got {every!r}")
    heldout = [pulse for pulse in pulses if pulse % every == 0]
    if not heldout:
        raise SplatExportError(f"block has no held-out pulse (every {every}th pulse)")
    return [pulse for pulse in pulses if pulse not in set(heldout)], heldout


def select_images_near_path(
    record: dict, route: RoutePath, s_start: float, s_end: float, radius_m: float = 20.0
) -> dict[str, list[str]]:
    """Images whose camera centre lies within radius_m of the block's path, grouped by session. Unlike the
    single-session pulse mapper this makes no monotonic assumption, so an opposite-direction or overlapping
    pass selects the images that actually see the block."""
    if not np.isfinite(radius_m) or radius_m <= 0:
        raise SplatExportError(f"image radius must be positive, got {radius_m}")
    if s_end <= s_start:
        raise SplatExportError(f"block path range {s_start}..{s_end} is empty")
    on_route = (route.s >= s_start) & (route.s <= s_end)
    if not on_route.any():
        raise SplatExportError(f"the route has no path between s={s_start:g} and s={s_end:g}")
    tree = cKDTree(route.enu[on_route, :2])
    selected: dict[str, list[str]] = {}
    for name, item in record.items():
        session = item.get("session")
        if session is None:
            raise SplatExportError(f"multi-session export needs a session for {name}")
        distance = tree.query(np.asarray(item["centre"], float)[:2], k=1)[0]
        if distance <= radius_m:
            selected.setdefault(session, []).append(name)
    if not selected:
        raise SplatExportError(f"no image lies within {radius_m:g} m of the block's path")
    return {session: sorted(names) for session, names in sorted(selected.items())}


def split_session_names(
    record: dict, selected: dict[str, list[str]], every: int
) -> tuple[list[str], list[str], dict[str, dict]]:
    """Hold out every nth pulse within each session, then name the train and held-out images. A session
    that contributes images must contribute a held-out pulse, as for a single-session block."""
    train, heldout, by_session = [], [], {}
    for session, names in selected.items():
        pulses = sorted({int(record[name]["pulse"]) for name in names})
        session_train, session_heldout = split_pulses(pulses, every)
        heldout_set = set(session_heldout)
        heldout += [name for name in names if int(record[name]["pulse"]) in heldout_set]
        train += [name for name in names if int(record[name]["pulse"]) not in heldout_set]
        by_session[session] = {
            "train_pulses": session_train,
            "heldout_pulses": session_heldout,
        }
    return sorted(train), sorted(heldout), by_session


def image_names_for_pulses(priors: dict, pulses: list[int]) -> list[str]:
    wanted = set(pulses)
    return sorted(name for name, record in priors.items() if int(record["pulse"]) in wanted)


def heldout_images(priors: dict, heldout_pulses: list[int]) -> list[str]:
    return image_names_for_pulses(priors, heldout_pulses)


def build_subset_reconstruction(
    model: pycolmap.Reconstruction,
    image_names: list[str],
    centres: np.ndarray,
    point_radius_m: float = DEFAULT_POINT_RADIUS_M,
    min_track_length: int = MIN_TRACK_LENGTH,
    max_error_px: float = MAX_REPROJECTION_ERROR_PX,
) -> tuple[pycolmap.Reconstruction, dict]:
    """The selected images and their cameras, plus points that pass the slice 7 quality filter, lie within
    the block's camera neighbourhood, and are observed by at least one selected image."""
    selected = {image.name: image for image in model.images.values() if image.name in set(image_names)}
    missing = sorted(set(image_names) - set(selected))
    if missing:
        raise SplatExportError(f"{len(missing)} selected images are not registered, first {missing[0]}")
    subset = pycolmap.Reconstruction()
    added_cameras = set()
    for image in selected.values():
        if image.camera_id not in added_cameras:
            subset.add_camera_with_trivial_rig(model.cameras[image.camera_id])
            added_cameras.add(image.camera_id)
        copy = pycolmap.Image(
            name=image.name,
            points2D=image.points2D,
            camera_id=int(image.camera_id),
            image_id=int(image.image_id),
        )
        subset.add_image_with_trivial_frame(copy, image.cam_from_world())
    selected_ids = {int(image.image_id) for image in selected.values()}
    tree = cKDTree(np.asarray(centres, float)[:, :2])
    input_points = 0
    kept_points = 0
    for point_id, point in model.points3D.items():
        input_points += 1
        if point.track.length() < min_track_length or point.error > max_error_px:
            continue
        if tree.query(np.asarray(point.xyz, float)[:2], k=1)[0] > point_radius_m:
            continue
        track = pycolmap.Track()
        for element in point.track.elements:
            if int(element.image_id) in selected_ids:
                track.add_element(element)
        if not track.length():
            continue
        copy = pycolmap.Point3D()
        copy.xyz = point.xyz
        copy.color = point.color
        copy.error = point.error
        copy.track = track
        subset.add_point3D_with_id(int(point_id), copy)
        kept_points += 1
    return subset, {
        "images": subset.num_images(),
        "cameras": subset.num_cameras(),
        "input_points": input_points,
        "points": kept_points,
        "min_track_length": min_track_length,
        "max_reprojection_error_px": max_error_px,
        "point_radius_m": point_radius_m,
    }


def export_block(
    workspace,
    images_root,
    route_package,
    rig: Rig | None,
    out,
    block_start: float,
    block_length: float = 100.0,
    margin: float = 25.0,
    data_factor: int = DEFAULT_DATA_FACTOR,
    holdout_every: int = 10,
    point_radius_m: float = DEFAULT_POINT_RADIUS_M,
    block_id: str | None = None,
    image_radius_m: float = 20.0,
) -> dict:
    """Write a gsplat-style block: images/ and images_4/ plus sparse/0, heldout.json and manifest.json.
    A multi-session workspace selects images by distance to the block's path, all sessions, and holds out
    every nth pulse within each session."""
    workspace = Path(workspace)
    images_root = Path(images_root)
    route_package = Path(route_package)
    out = Path(out)
    block_id = block_id or f"s{block_start:g}-l{block_length:g}"
    _plain_name(block_id)
    final = out / block_id
    if final.exists():
        raise SplatExportError(f"block {final} already exists; never overwrite an exported block")
    priors = json.loads((workspace / "priors.json").read_text())
    multi = any("session" in item for item in priors.values())
    model = pycolmap.Reconstruction(workspace / "sparse" / "refined")
    session_split = None
    sessions_doc = None
    if multi:
        from georoute.multisession import load_sessions, session_frame_from

        sessions_doc = load_sessions(workspace)
        if sessions_doc is None:
            raise SplatExportError(f"{workspace} has session-prefixed priors but no sessions.json")
        route = load_route_path(route_package, session_frame_from(workspace))
        selected = select_images_near_path(
            priors,
            route,
            block_start - margin,
            block_start + block_length + margin,
            radius_m=image_radius_m,
        )
        train_names, heldout_names, session_split = split_session_names(priors, selected, holdout_every)
        train_pulses = heldout_pulses = None
        centres = np.array(
            [np.asarray(priors[name]["centre"], float) for name in train_names + heldout_names]
        )
    else:
        route = load_route_path(route_package)
        pulse_s = pulse_route_s(priors, route)
        selected_pulses = select_pulses(pulse_s, block_start, block_length, margin)
        train_pulses, heldout_pulses = split_pulses(selected_pulses, holdout_every)
        train_names = image_names_for_pulses(priors, train_pulses)
        heldout_names = heldout_images(priors, heldout_pulses)
        centres = np.array(
            [
                np.asarray(record["centre"], float)
                for record in priors.values()
                if int(record["pulse"]) in set(selected_pulses)
            ]
        )
    if not multi and rig is None:
        raise SplatExportError("a single-session block needs a rig")
    subset, point_stats = build_subset_reconstruction(
        model,
        train_names + heldout_names,
        centres[:, :2],
        point_radius_m=point_radius_m,
    )
    out.mkdir(parents=True, exist_ok=True)
    staging = out / f".{block_id}.staging-{os.getpid()}"
    if staging.exists():
        raise SplatExportError(f"staging folder {staging} already exists")
    model_dir = staging / ".model"
    try:
        model_dir.mkdir(parents=True)
        subset.write(model_dir)
        pycolmap.undistort_images(
            str(staging),
            str(model_dir),
            str(images_root),
            image_names=train_names + heldout_names,
        )
        _layout_sparse(staging)
        shutil.rmtree(model_dir, ignore_errors=True)
        shutil.rmtree(staging / "stereo", ignore_errors=True)
        for script in ("run-colmap-geometric.sh", "run-colmap-photometric.sh"):
            (staging / script).unlink(missing_ok=True)
        _downscale_images(staging / "images", staging / "images_4", data_factor)
        (staging / "heldout.json").write_text(json.dumps(heldout_names, indent=1) + "\n")
        (staging / "train.json").write_text(json.dumps(train_names, indent=1) + "\n")
        manifest = _manifest(
            workspace,
            route_package,
            rig,
            block_start,
            block_length,
            margin,
            data_factor,
            holdout_every,
            point_radius_m,
            train_pulses,
            heldout_pulses,
            train_names,
            heldout_names,
            point_stats,
            staging,
            images_root,
            session_split=session_split,
            image_radius_m=image_radius_m,
            sessions_doc=sessions_doc,
        )
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
        os.replace(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def _layout_sparse(folder: Path):
    sparse = folder / "sparse"
    target = sparse / "0"
    target.mkdir(parents=True, exist_ok=True)
    for path in list(sparse.iterdir()):
        if path != target:
            path.rename(target / path.name)


def _downscale_images(source: Path, target: Path, factor: int):
    if factor < 1:
        raise SplatExportError(f"data factor must be at least 1, got {factor}")
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source).with_suffix(".png")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as image:
            size = (max(1, round(image.width / factor)), max(1, round(image.height / factor)))
            image.convert("RGB").resize(size, Image.BICUBIC).save(destination)


def _manifest(
    workspace,
    route_package,
    rig,
    block_start,
    block_length,
    margin,
    data_factor,
    holdout_every,
    point_radius_m,
    train_pulses,
    heldout_pulses,
    train_names,
    heldout_names,
    point_stats,
    staging,
    images_root,
    session_split=None,
    image_radius_m=None,
    sessions_doc=None,
):
    route_files = [route_package / "route.json"] + sorted((route_package / "segments").glob("*/path.json"))
    manifest = {
        "schema": EXPORT_SCHEMA,
        "block": {
            "s_start": block_start,
            "s_end": block_start + block_length,
            "margin_m": margin,
            "image_s_start": block_start - margin,
            "image_s_end": block_start + block_length + margin,
        },
        "split": {
            "rule": f"every {holdout_every}th capture pulse",
            "train_pulses": train_pulses,
            "heldout_pulses": heldout_pulses,
        },
        "counts": {
            "train_images": len(train_names),
            "heldout_images": len(heldout_names),
            "images": len(train_names) + len(heldout_names),
            **point_stats,
        },
        "parameters": {
            "data_factor": data_factor,
            "holdout_every": holdout_every,
            "point_radius_m": point_radius_m,
            "undistort": "PINHOLE",
        },
        "inputs": {
            "workspace": str(workspace),
            "route_package": str(route_package),
            "images_root": str(images_root),
            "priors_sha256": _sha256(workspace / "priors.json"),
            "route_sha256": {str(path.relative_to(route_package)): _sha256(path) for path in route_files},
        },
        "outputs": {
            "images": "images",
            "images_4": "images_4",
            "sparse": "sparse/0",
            "heldout": "heldout.json",
        },
    }
    if rig is not None:
        manifest["inputs"]["rig_name"] = rig.name
        manifest["inputs"]["rig_sha256"] = rig.sha256
    if session_split is not None:
        manifest["split"] = {
            "rule": f"every {holdout_every}th capture pulse within each session",
            "by_session": session_split,
        }
        manifest["counts"]["per_session"] = {
            label: {
                "train_images": sum(name.startswith(f"{label}/") for name in train_names),
                "heldout_images": sum(name.startswith(f"{label}/") for name in heldout_names),
            }
            for label in session_split
        }
        manifest["parameters"]["image_radius_m"] = image_radius_m
        manifest["inputs"]["sessions_sha256"] = _sha256(workspace / "sessions.json")
        manifest["inputs"]["sessions"] = [
            {
                "label": session["label"],
                "rig_name": session["rig"]["name"],
                "rig_sha256": session["rig"]["sha256"],
            }
            for session in (sessions_doc or {}).get("sessions", [])
        ]
    return manifest


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _plain_name(value: str):
    if not isinstance(value, str) or not value or Path(value).name != value or value in (".", ".."):
        raise SplatExportError(f"block id {value!r} must be a plain name")
