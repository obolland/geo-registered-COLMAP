"""Session plus photos to one timed, positioned record per capture photo, with a report and a plot."""

import json
from pathlib import Path

import numpy as np

from georoute import __version__
from georoute.geodesy import LocalFrame, lla_to_ecef
from georoute.photos import match_photos, read_photos
from georoute.report import session_report
from georoute.rig import Rig
from georoute.session import Session, load_session
from georoute.trajectory import antenna_positions, exposure_mid

IMAGES_SCHEMA = "georoute.images/1.0.0"


class IngestError(ValueError):
    pass


def session_frame(session: Session) -> LocalFrame:
    """The build's working frame: anchored at the session's first RTK-fixed epoch."""
    fixed = np.flatnonzero(session.pvt.carr_soln == 2)
    if not len(fixed):
        raise IngestError("the session has no RTK-fixed epoch to anchor its frame")
    i = fixed[0]
    return LocalFrame.at(
        session.pvt.lat_deg[i], session.pvt.lon_deg[i], session.pvt.height_m[i], session.datum
    )


def ingest(
    session_dir, images_root, out_dir, curtain_travel_s: float | None = None, rig: Rig | None = None
) -> dict:
    """Writes report.json always (with the refusal reason if refused), and images.json and track.png only
    when every capture photo was timed and positioned."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for stale in ("images.json", "track.png"):
        (out / stale).unlink(missing_ok=True)
    report: dict = {}
    if curtain_travel_s is None:
        curtain_travel_s = rig.curtain_travel_s if rig else 0.0
    try:
        session = load_session(session_dir)
        report["session"] = session_report(session)
        if report["session"]["errors"]:
            raise IngestError("session integrity errors: " + "; ".join(report["session"]["errors"]))
        images, frame, summary = _position_captures(session, images_root, curtain_travel_s, rig)
    except Exception as e:
        report["refused"] = str(e)
        _write_json(out / "report.json", report)
        raise
    report["ingest"] = summary
    _write_json(
        out / "images.json",
        {
            "schema": IMAGES_SCHEMA,
            "georoute": __version__,
            "session": session.path.name,
            "datum": session.datum,
            "nav_period_s": session.nav_period_s,
            "curtain_travel_s": curtain_travel_s,
            "rig": {"name": rig.name, "sha256": rig.sha256, "measured": rig.measured} if rig else None,
            "frame": {
                "lat_deg": frame.lat_deg,
                "lon_deg": frame.lon_deg,
                "h_m": frame.h_m,
                "datum": frame.datum,
                "origin_ecef": frame.origin_ecef.tolist(),
            },
            "images": images,
        },
    )
    _write_json(out / "report.json", report)
    _plot(
        out / "track.png",
        frame.from_ecef(_track_ecef(session)),
        np.array([i["antenna_enu"] for i in images]),
        np.array([i["carr_soln"] for i in images]),
    )
    return summary


def _position_captures(session: Session, images_root, curtain_travel_s: float, rig: Rig | None):
    matches = match_photos(session, *photo_sources(images_root, rig))
    captures = [m for m in matches if m.pulse.kind == "capture"]
    delays = [m.pulse.delays_us[m.photo.camera] for m in captures]
    t_mid = np.array(
        [
            exposure_mid(session.command_time(m.pulse.n), d, m.photo.exposure_s, curtain_travel_s)
            for m, d in zip(captures, delays, strict=True)
        ]
    )
    antenna = antenna_positions(session.pvt, t_mid, session.nav_period_s)
    frame = session_frame(session)
    enu = frame.from_ecef(antenna.ecef)
    epoch = np.searchsorted(session.pvt.gps_time_s, t_mid, side="right") - 1
    images = [
        {
            "path": m.photo.path,
            "camera": m.photo.camera,
            "pulse": m.pulse.n,
            "delay_us": delays[k],
            "exposure_s": m.photo.exposure_s,
            "t_mid_gps_s": float(t_mid[k]),
            "antenna_ecef": antenna.ecef[k].tolist(),
            "antenna_enu": enu[k].tolist(),
            "carr_soln": int(antenna.carr_soln[k]),
            "fix_type": int(antenna.fix_type[k]),
            "h_acc_m": round(float(antenna.h_acc_m[k]), 4),
            "v_acc_m": round(float(antenna.v_acc_m[k]), 4),
            "bracket_gap_s": round(float(antenna.bracket_gap_s[k]), 6),
            "corr_age": session.pvt.corr_age[epoch[k]],
            "flags": antenna.flags[k],
        }
        for k, m in enumerate(captures)
    ]
    summary = {
        "images": len(images),
        "warmups_dropped": len(matches) - len(captures),
        "per_camera": {
            f"cam{c}": sum(i["camera"] == c for i in images) for c in sorted({i["camera"] for i in images})
        },
        "flagged": {f: sum(f in i["flags"] for i in images) for f in ("not-fixed", "no-3d-fix", "gap")},
    }
    return images, frame, summary


def photo_sources(images_root, rig: Rig | None):
    """Photos per camera and the expected body serials, from the rig's folders when a rig is given."""
    if rig is None:
        return read_photos(images_root), None
    folders = {c.index: c.folder for c in rig.cameras}
    serials = {c.index: c.serial for c in rig.cameras if c.serial}
    return read_photos(images_root, folders), serials


def _track_ecef(session: Session) -> np.ndarray:
    return lla_to_ecef(session.pvt.lat_deg, session.pvt.lon_deg, session.pvt.height_m)


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=1))


def _plot(path: Path, track_enu: np.ndarray, image_enu: np.ndarray, carr_soln: np.ndarray):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.plot(track_enu[:, 0], track_enu[:, 1], color="0.75", lw=1, label="RTK track (5 Hz)")
    for value, colour, label in (
        (2, "tab:green", "fixed"),
        (1, "tab:orange", "float"),
        (0, "tab:red", "no RTK"),
    ):
        sel = carr_soln == value
        if sel.any():
            ax.scatter(image_enu[sel, 0], image_enu[sel, 1], s=6, color=colour, label=f"photo, {label}")
    ax.set_aspect("equal")
    ax.set_xlabel("east (m)")
    ax.set_ylabel("north (m)")
    ax.legend()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
