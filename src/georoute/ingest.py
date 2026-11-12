"""Session plus photos to one timed, positioned record per capture photo, with a report and a plot."""

import json
from pathlib import Path

import numpy as np

from georoute.geodesy import LocalFrame, lla_to_ecef
from georoute.photos import match_photos, read_photos
from georoute.report import session_report
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


def ingest(session_dir, images_root, out_dir, curtain_travel_s: float = 0.0) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    session = load_session(session_dir)
    report = {"session": session_report(session)}
    _write_json(out / "report.json", report)
    if report["session"]["errors"]:
        raise IngestError("session integrity errors: " + "; ".join(report["session"]["errors"]))

    matches = match_photos(session, read_photos(images_root))
    captures = [m for m in matches if m.pulse.kind == "capture"]
    t_mid = np.array(
        [
            exposure_mid(
                session.command_time(m.pulse.n),
                m.pulse.delays_us[m.photo.camera],
                m.photo.exposure_s,
                curtain_travel_s,
            )
            for m in captures
        ]
    )
    antenna = antenna_positions(session.pvt, t_mid, session.nav_period_s)
    frame = session_frame(session)
    enu = frame.from_ecef(antenna.ecef)

    images = [
        {
            "path": m.photo.path,
            "camera": m.photo.camera,
            "pulse": m.pulse.n,
            "t_mid_gps_s": float(t_mid[k]),
            "exposure_s": m.photo.exposure_s,
            "antenna_ecef": antenna.ecef[k].tolist(),
            "antenna_enu": enu[k].tolist(),
            "carr_soln": int(antenna.carr_soln[k]),
            "fix_type": int(antenna.fix_type[k]),
            "h_acc_m": float(antenna.h_acc_m[k]),
            "v_acc_m": float(antenna.v_acc_m[k]),
            "bracket_gap_s": float(antenna.bracket_gap_s[k]),
            "flags": antenna.flags[k],
        }
        for k, m in enumerate(captures)
    ]
    _write_json(
        out / "images.json",
        {
            "schema": IMAGES_SCHEMA,
            "session": session.path.name,
            "datum": session.datum,
            "curtain_travel_s": curtain_travel_s,
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
    summary = {
        "images": len(images),
        "warmups_dropped": len(matches) - len(captures),
        "per_camera": {
            f"cam{c}": sum(i["camera"] == c for i in images) for c in sorted({i["camera"] for i in images})
        },
        "flagged": {f: sum(f in i["flags"] for i in images) for f in ("not-fixed", "no-3d-fix", "gap")},
    }
    report["ingest"] = summary
    _write_json(out / "report.json", report)
    _plot(out / "track.png", frame.from_ecef(_track_ecef(session)), enu, antenna.carr_soln)
    return summary


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
