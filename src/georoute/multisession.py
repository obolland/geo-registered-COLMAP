"""Several capture sessions in one workspace: a common frame, session-qualified image names and inputs."""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pycolmap

from georoute.geodesy import LocalFrame
from georoute.rig import Rig

SESSIONS_SCHEMA = "georoute.sessions/1.0.0"
SESSIONS_FILE = "sessions.json"


class MultiSessionError(ValueError):
    pass


@dataclass(frozen=True)
class SessionSpec:
    """One session's ingest output, its photo root and the rig that session was ridden with."""

    label: str
    images_json: Path
    images_root: Path
    rig: Rig
    rig_path: Path | None = None


def session_image_name(label: str, path: str) -> str:
    """COLMAP image name for a session's photo: the session leads only when there are several."""
    if not isinstance(label, str) or not label or label in (".", "..") or Path(label).name != label:
        raise MultiSessionError(f"session name {label!r} must be a plain folder name")
    if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
        raise MultiSessionError(f"image path {path!r} must be relative to its session's photo root")
    return f"{label}/{path}"


def doc_frame(doc: dict) -> LocalFrame:
    """The session frame recorded in an images.json, checked against the document's datum."""
    frame = doc.get("frame")
    if not isinstance(frame, dict):
        raise MultiSessionError("images.json has no frame")
    datum = doc.get("datum")
    if not datum or frame.get("datum") != datum:
        raise MultiSessionError(
            f"images.json datum {datum!r} and frame datum {frame.get('datum')!r} disagree"
        )
    try:
        out = LocalFrame.at(frame["lat_deg"], frame["lon_deg"], frame["h_m"], datum)
    except KeyError as e:
        raise MultiSessionError(f"images.json frame is missing {e.args[0]}") from e
    origin = frame.get("origin_ecef")
    if origin is not None and not np.allclose(out.origin_ecef, origin, atol=1e-3):
        raise MultiSessionError("images.json frame origin_ecef disagrees with its lat/lon/h")
    return out


def common_frame(docs: list[dict]) -> LocalFrame:
    """The build's frame: the first session's, at its first RTK-fixed epoch. Sessions must share a datum."""
    if not docs:
        raise MultiSessionError("no sessions")
    frames = [doc_frame(doc) for doc in docs]
    datum = frames[0].datum
    for doc, frame in zip(docs[1:], frames[1:], strict=True):
        if frame.datum != datum:
            raise MultiSessionError(
                f"session {doc.get('session')!r} uses datum {frame.datum}, but the first uses {datum}"
            )
    return frames[0]


def images_in_frame(doc: dict, frame: LocalFrame) -> list[dict]:
    """The document's images with antenna position and velocity re-expressed in the common frame,
    through ECEF. antenna_ecef is authoritative; antenna_enu is only the fallback for older documents."""
    session_frame = doc_frame(doc)
    if session_frame.datum != frame.datum:
        raise MultiSessionError(f"can't mix datums {session_frame.datum} and {frame.datum}")
    out = []
    for image in doc["images"]:
        copy = dict(image)
        ecef = image.get("antenna_ecef")
        ecef = session_frame.to_ecef(image["antenna_enu"]) if ecef is None else np.asarray(ecef, float)
        copy["antenna_enu"] = frame.from_ecef(ecef).tolist()
        velocity = np.asarray(image.get("velocity_enu", [0.0, 0.0, 0.0]), float)
        copy["velocity_enu"] = (frame.ecef_from_enu.T @ (session_frame.ecef_from_enu @ velocity)).tolist()
        out.append(copy)
    return out


def load_session_spec(images_json, images_root, rig_path, label: str | None = None) -> SessionSpec:
    """Read one session's ingest output and rig, taking its name from images.json unless given."""
    from georoute.rig import load_rig

    images_json = Path(images_json)
    try:
        doc = json.loads(images_json.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise MultiSessionError(f"can't read {images_json}: {e}") from e
    doc_frame(doc)
    name = label or doc.get("session")
    if not name:
        raise MultiSessionError(f"{images_json} has no session name")
    return SessionSpec(name, images_json, Path(images_root), load_rig(rig_path), Path(rig_path))


def prepare_sessions(
    specs: list[SessionSpec],
    workspace,
    pulses: tuple[int, int] | None = None,
    max_image_size: int = 3200,
) -> dict:
    """One COLMAP workspace from several sessions. A single session delegates to prepare(), so its
    workspace is byte-for-byte what it was before multi-session support. Several sessions share a common
    frame, get a session-prefixed image name (file numbers repeat between rides) and one COLMAP camera
    per session and camera folder (focus and mounting may differ)."""
    if not len(specs):
        raise MultiSessionError("no sessions")
    from georoute.reconstruct import prepare

    if len(specs) == 1:
        spec = specs[0]
        return prepare(spec.images_json, spec.images_root, workspace, spec.rig, pulses, max_image_size)
    _check_labels(specs)
    from georoute.reconstruct import extraction_options, first_pass_priors, write_priors

    docs = [json.loads(spec.images_json.read_text()) for spec in specs]
    frame = common_frame(docs)
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "database.db"
    for stale in ("", "-shm", "-wal"):
        Path(f"{db_path}{stale}").unlink(missing_ok=True)
    for stale in ("priors.json", "matching.json", "mapping.json", "report.json", SESSIONS_FILE):
        (workspace / stale).unlink(missing_ok=True)
    shutil.rmtree(workspace / "sparse", ignore_errors=True)
    shutil.rmtree(workspace / "images", ignore_errors=True)
    shutil.rmtree(workspace / "rigs", ignore_errors=True)

    entries = []
    images_root = workspace / "images"
    for spec, doc in zip(specs, docs, strict=True):
        images = images_in_frame(doc, frame)
        if pulses is not None:
            images = [i for i in images if pulses[0] <= i["pulse"] <= pulses[1]]
        for image in images:
            name = session_image_name(spec.label, image["path"])
            source = spec.images_root / image["path"]
            if not source.is_file():
                raise MultiSessionError(
                    f"missing photo {spec.label}/{image['path']} under {spec.images_root}"
                )
            target = images_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source.resolve())
            entries.append((spec, image, name))
    if not entries:
        raise MultiSessionError("no images in the requested pulse range")

    extraction = extraction_options(max_image_size)
    models = sorted({spec.rig.camera_type(image["camera"]).colmap_model for spec, image, _ in entries})
    for model in models:
        reader = pycolmap.ImageReaderOptions()
        reader.camera_model = model
        pycolmap.extract_features(
            db_path,
            images_root,
            image_names=[
                name
                for spec, image, name in entries
                if spec.rig.camera_type(image["camera"]).colmap_model == model
            ],
            camera_mode=pycolmap.CameraMode.PER_FOLDER,
            reader_options=reader,
            extraction_options=extraction,
            device=pycolmap.Device.cpu,
        )

    with pycolmap.Database.open(db_path) as db:
        extracted = {i.name for i in db.read_all_images()}
    missing = sorted({name for _, _, name in entries} - extracted)
    if missing:
        raise MultiSessionError(f"{len(missing)} photos failed feature extraction, first {missing[0]}")

    priors: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for spec in specs:
        mine = [(image, name) for s, image, name in entries if s.label == spec.label]
        renamed = [{**image, "path": name} for image, name in mine]
        priors.update(first_pass_priors(renamed, spec.rig))
    with pycolmap.Database.open(db_path) as db:
        written = write_priors(db, priors)
        summary = {
            "images": db.num_images(),
            "cameras": db.num_cameras(),
            "priors": written,
            "frame_id": docs[0]["frame"]["id"],
            "sessions": [spec.label for spec in specs],
            "per_session": {
                spec.label: sum(name.startswith(f"{spec.label}/") for _, _, name in entries) for spec in specs
            },
        }

    by_name = {name: (spec, image) for spec, image, name in entries}
    record = {
        name: {
            "session": spec.label,
            "camera": image["camera"],
            "pulse": image["pulse"],
            "fixed": image["carr_soln"] == 2 and "jump" not in image["flags"],
            "antenna_enu": image["antenna_enu"],
            "sigma_h_m": image["prior_sigma_h_m"],
            "sigma_v_m": image["prior_sigma_v_m"],
            "centre": c.tolist(),
            "covariance": cov.tolist(),
        }
        for name, (c, cov) in priors.items()
        for spec, image in [by_name[name]]
    }
    (workspace / "priors.json").write_text(json.dumps(record, indent=1))
    _write_sessions(workspace, frame, specs, summary["per_session"])
    return summary


def _check_labels(specs: list[SessionSpec]):
    seen: dict[str, int] = {}
    for spec in specs:
        session_image_name(spec.label, "x")
        seen[spec.label] = seen.get(spec.label, 0) + 1
    repeated = sorted(label for label, count in seen.items() if count > 1)
    if repeated:
        raise MultiSessionError(
            f"more than one session is named {repeated[0]!r}; ingest them into sessions with distinct names"
        )


def _write_sessions(workspace: Path, frame: LocalFrame, specs: list[SessionSpec], per_session: dict):
    rigs = workspace / "rigs"
    rigs.mkdir(parents=True, exist_ok=True)
    sessions = []
    for spec in specs:
        if spec.rig_path is None:
            raise MultiSessionError(f"session {spec.label!r} has no rig file to record")
        target = rigs / f"{spec.label}.toml"
        shutil.copyfile(spec.rig_path, target)
        import hashlib

        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != spec.rig.sha256:
            raise MultiSessionError(f"rig file {spec.rig_path} changed while preparing; rerun")
        sessions.append(
            {
                "label": spec.label,
                "images_json": str(Path(spec.images_json).resolve()),
                "images_root": str(Path(spec.images_root).resolve()),
                "rig": {
                    "name": spec.rig.name,
                    "sha256": spec.rig.sha256,
                    "path": str(target.relative_to(workspace)),
                },
                "images": per_session.get(spec.label, 0),
            }
        )
    doc = {
        "schema": SESSIONS_SCHEMA,
        "frame": {
            "lat_deg": frame.lat_deg,
            "lon_deg": frame.lon_deg,
            "h_m": frame.h_m,
            "datum": frame.datum,
            "origin_ecef": frame.origin_ecef.tolist(),
        },
        "images_root": "images",
        "sessions": sessions,
    }
    (workspace / SESSIONS_FILE).write_text(json.dumps(doc, indent=1))


def image_headings(record: dict) -> dict[str, np.ndarray]:
    """Each image's direction of travel, from the camera-centre track of its session and camera folder.
    Centred differences keep the heading honest at pulse gaps and at both ends."""
    groups: dict[tuple, list] = {}
    for name, item in record.items():
        if item.get("session") is None:
            continue
        groups.setdefault((item["session"], item["camera"]), []).append(
            (int(item["pulse"]), name, np.asarray(item["centre"], float)[:2])
        )
    out = {}
    for items in groups.values():
        items.sort()
        for index, (_, name, centre) in enumerate(items):
            if 0 < index < len(items) - 1:
                direction = items[index + 1][2] - items[index - 1][2]
            elif index + 1 < len(items):
                direction = items[index + 1][2] - centre
            elif index:
                direction = centre - items[index - 1][2]
            else:
                continue
            if np.linalg.norm(direction) > 0:
                out[name] = direction
    return out
