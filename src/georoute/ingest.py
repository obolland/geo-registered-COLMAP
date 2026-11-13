"""Session plus photos to one timed, positioned record per capture photo, with a report and a plot."""

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from georoute import __version__
from georoute.geodesy import LocalFrame, lla_to_ecef
from georoute.photos import match_photos, read_photos
from georoute.report import session_report
from georoute.rig import Rig
from georoute.session import Session, load_session
from georoute.trajectory import antenna_positions, exposure_mid

IMAGES_SCHEMA = "georoute.images/1.1.0"
SESSION_FILES = ("session.json", "session.json.tmp", "pulses.csv", "tm2.csv", "pvt.csv", "rtcm.bin")
MAX_CURTAIN_TRAVEL_S = 0.05
# A camera whose last photo comes this many capture pulses before the session's last is reported as stopped.
STOPPED_EARLY_PULSES = 10
COORDINATES_NOTE = (
    "antenna_ecef is authoritative. antenna_enu is a tangent plane at the frame origin, for plotting and "
    "short builds only: it drops about 8 cm below the ground at 1 km and 2 m at 5 km."
)


class IngestError(ValueError):
    pass


def session_frame(session: Session) -> tuple[LocalFrame, bool]:
    """The build's working frame, anchored at the first RTK-fixed epoch, or at the first 3D fix when the
    session never reached RTK fixed (second value False)."""
    for wanted, rtk_fixed in ((session.pvt.carr_soln == 2, True), (session.pvt.fix_type >= 3, False)):
        found = np.flatnonzero(wanted)
        if len(found):
            i = found[0]
            frame = LocalFrame.at(
                session.pvt.lat_deg[i], session.pvt.lon_deg[i], session.pvt.height_m[i], session.datum
            )
            return frame, rtk_fixed
    raise IngestError("the session has no 3D fix to anchor its frame")


def ingest(
    session_dir,
    images_root,
    out_dir,
    curtain_travel_s: float | None = None,
    rig: Rig | None = None,
    allow_unmeasured_rig: bool = False,
) -> dict:
    """Writes report.json always (with the refusal reason if refused), and images.json and track.png only
    when every capture photo was timed and positioned."""
    out = Path(out_dir)
    report: dict = {}
    try:
        out.mkdir(parents=True, exist_ok=True)
        for stale in ("images.json", "track.png"):
            (out / stale).unlink(missing_ok=True)
    except OSError as e:
        raise IngestError(f"can't write to {out}: {e}") from e
    try:
        if rig is not None and not rig.measured and not allow_unmeasured_rig:
            raise IngestError(
                f"rig {rig.name} is unmeasured (placeholder geometry); pass allow_unmeasured_rig"
            )
        if curtain_travel_s is None:
            curtain_travel_s = rig.curtain_travel_s if rig else 0.0
        if not (math.isfinite(curtain_travel_s) and 0 <= curtain_travel_s <= MAX_CURTAIN_TRAVEL_S):
            raise IngestError(f"curtain travel {curtain_travel_s} s is outside 0 to {MAX_CURTAIN_TRAVEL_S} s")
        session = load_session(session_dir)
        report["session"] = session_report(session)
        if report["session"]["errors"]:
            raise IngestError("session integrity errors: " + "; ".join(report["session"]["errors"]))
        images, frame, summary = _position_captures(session, images_root, curtain_travel_s, rig)
    except Exception as e:
        report["refused"] = str(e)
        if out.is_dir():
            _write_json(out / "report.json", report)
        raise
    summary["rig_measured"] = rig.measured if rig else None
    report["ingest"] = summary
    _write_json(
        out / "images.json",
        {
            "schema": IMAGES_SCHEMA,
            "georoute": __version__,
            "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session": session.path.name,
            "inputs": _input_hashes(session.path),
            "datum": session.datum,
            "base": report["session"]["base"],
            "nav_period_s": session.nav_period_s,
            "curtain_travel_s": curtain_travel_s,
            "rig": {"name": rig.name, "sha256": rig.sha256, "measured": rig.measured} if rig else None,
            "serials_seen": summary["serials_seen"],
            "coordinates": COORDINATES_NOTE,
            "frame": {
                "id": f"{session.path.name}/{'rtk-fixed' if summary['rtk_fixed'] else '3d-fix'}",
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


def _input_hashes(session_dir: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((session_dir / name).read_bytes()).hexdigest()
        for name in SESSION_FILES
        if (session_dir / name).exists()
    }


def _position_captures(session: Session, images_root, curtain_travel_s: float, rig: Rig | None):
    warnings: list[str] = []
    photos, serials = photo_sources(images_root, rig, warnings)
    matches = match_photos(session, photos, serials, warnings)
    captures = [m for m in matches if m.pulse.kind == "capture"]
    if not captures:
        raise IngestError("no capture photos to position")
    _check_cameras(session, captures, rig, warnings)
    delays = [m.pulse.delays_us[m.photo.camera] for m in captures]
    t_mid = np.array(
        [
            exposure_mid(session.command_time(m.pulse.n), d, m.photo.exposure_s, curtain_travel_s)
            for m, d in zip(captures, delays, strict=True)
        ]
    )
    antenna = antenna_positions(session.pvt, t_mid, session.nav_period_s)
    frame, rtk_fixed = session_frame(session)
    if not rtk_fixed:
        warnings.append("the session has no RTK-fixed epoch: positions are float or worse throughout")
    enu = frame.from_ecef(antenna.ecef)
    if np.linalg.norm(enu[:, :2], axis=1).max() > 1000:
        warnings.append("photos lie over 1 km from the frame origin: use antenna_ecef, not antenna_enu")
    epoch = np.searchsorted(session.pvt.gps_time_s, t_mid, side="right") - 1
    images = []
    for k, m in enumerate(captures):
        flags = antenna.flags[k]
        has_prior = not ({"no-3d-fix", "jump"} & set(flags)) and antenna.carr_soln[k] >= 1
        images.append(
            {
                "path": m.photo.path,
                "camera": m.photo.camera,
                "serial": m.photo.serial,
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
                "has_prior": bool(has_prior),
                "prior_sigma_h_m": round(float(antenna.h_acc_m[k]), 4) if has_prior else None,
                "prior_sigma_v_m": round(float(antenna.v_acc_m[k]), 4) if has_prior else None,
                "bracket_gap_s": round(float(antenna.bracket_gap_s[k]), 6),
                "corr_age": session.pvt.corr_age[epoch[k]],
                "flags": flags,
            }
        )
    summary = {
        "images": len(images),
        "warmups_dropped": len(matches) - len(captures),
        "per_camera": {
            f"cam{c}": sum(i["camera"] == c for i in images) for c in sorted({i["camera"] for i in images})
        },
        "flagged": {
            f: sum(f in i["flags"] for i in images) for f in ("not-fixed", "no-3d-fix", "gap", "jump")
        },
        "without_prior": sum(not i["has_prior"] for i in images),
        "rtk_fixed": rtk_fixed,
        "serials_seen": {
            f"cam{c}": sorted({i["serial"] for i in images if i["camera"] == c and i["serial"]})
            for c in sorted({i["camera"] for i in images})
        },
        "warnings": warnings,
    }
    return images, frame, summary


def _check_cameras(session: Session, captures: list, rig: Rig | None, warnings: list[str]):
    """Every rig camera must have exposed, and a camera that stopped well before the end is reported."""
    used = {m.photo.camera for m in captures}
    for camera in sorted({c.index for c in rig.cameras} - used) if rig else []:
        raise IngestError(f"cam{camera} is in the rig but has no exposures in this session")
    last_capture = max(p.n for p in session.pulses if p.kind == "capture")
    for camera in sorted(used):
        last = max(m.pulse.n for m in captures if m.photo.camera == camera)
        if last_capture - last > STOPPED_EARLY_PULSES:
            warnings.append(
                f"cam{camera} stopped {last_capture - last} pulses before the session's last capture"
            )


def photo_sources(images_root, rig: Rig | None, warnings: list[str] | None = None):
    """Photos per camera and the expected body serials, from the rig's folders when a rig is given."""
    if rig is None:
        return read_photos(images_root, warnings=warnings), None
    folders = {c.index: c.folder for c in rig.cameras}
    serials = {c.index: c.serial for c in rig.cameras if c.serial}
    return read_photos(images_root, folders, warnings), serials


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
