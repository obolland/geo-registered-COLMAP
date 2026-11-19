"""Several capture sessions in one workspace: a common frame, session-qualified image names and inputs."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from georoute.geodesy import LocalFrame
from georoute.rig import Rig


class MultiSessionError(ValueError):
    pass


@dataclass(frozen=True)
class SessionSpec:
    """One session's ingest output, its photo root and the rig that session was ridden with."""

    label: str
    images_json: Path
    images_root: Path
    rig: Rig


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
