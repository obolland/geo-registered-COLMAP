"""Exposure times and antenna positions: cubic Hermite through the 5 Hz RTK track using NAV-PVT velocity,
done in ECEF so it is independent of any local frame and of route length."""

from dataclasses import dataclass

import numpy as np

from georoute.geodesy import lla_to_ecef, ned_to_ecef
from georoute.session import PvtTrack

GAP_FACTOR = 1.5


class TrajectoryError(ValueError):
    pass


@dataclass(frozen=True)
class AntennaPositions:
    ecef: np.ndarray
    carr_soln: np.ndarray
    fix_type: np.ndarray
    h_acc_m: np.ndarray
    v_acc_m: np.ndarray
    bracket_gap_s: np.ndarray
    flags: list[list[str]]


def exposure_mid(t_command: float, delay_us: int, exposure_s: float, curtain_travel_s: float = 0.0) -> float:
    """Mid-exposure GPS time: hot-shoe X-sync time plus half the exposure, minus half the curtain travel."""
    return t_command + delay_us * 1e-6 + exposure_s / 2 - curtain_travel_s / 2


def antenna_positions(track: PvtTrack, times, nav_period_s: float = 0.2) -> AntennaPositions:
    t = np.asarray(times, float)
    epochs = track.gps_time_s
    outside = (t < epochs[0]) | (t > epochs[-1])
    if outside.any():
        raise TrajectoryError(
            f"{int(outside.sum())} times fall outside the PVT track "
            f"(first at GPS {t[outside][0]:.3f}, track {epochs[0]:.3f} to {epochs[-1]:.3f})"
        )
    i = np.clip(np.searchsorted(epochs, t, side="right") - 1, 0, len(epochs) - 2)
    t0, t1 = epochs[i], epochs[i + 1]
    h = t1 - t0
    s = ((t - t0) / h)[:, None]

    ecef = lla_to_ecef(track.lat_deg, track.lon_deg, track.height_m)
    vel = np.stack(
        [
            ned_to_ecef(la, lo, v)
            for la, lo, v in zip(track.lat_deg, track.lon_deg, track.vel_ned_mps, strict=True)
        ]
    )
    h00, h10, h01, h11 = 2 * s**3 - 3 * s**2 + 1, s**3 - 2 * s**2 + s, -2 * s**3 + 3 * s**2, s**3 - s**2
    pos = h00 * ecef[i] + h10 * h[:, None] * vel[i] + h01 * ecef[i + 1] + h11 * h[:, None] * vel[i + 1]

    w = s[:, 0]
    carr = np.minimum(track.carr_soln[i], track.carr_soln[i + 1])
    fix = np.minimum(track.fix_type[i], track.fix_type[i + 1])
    flags = [
        [
            name
            for name, bad in (
                ("not-fixed", c < 2),
                ("no-3d-fix", f < 3),
                ("gap", g > GAP_FACTOR * nav_period_s),
            )
            if bad
        ]
        for c, f, g in zip(carr, fix, h, strict=True)
    ]
    return AntennaPositions(
        ecef=pos,
        carr_soln=carr,
        fix_type=fix,
        h_acc_m=(1 - w) * track.h_acc_m[i] + w * track.h_acc_m[i + 1],
        v_acc_m=(1 - w) * track.v_acc_m[i] + w * track.v_acc_m[i + 1],
        bracket_gap_s=h,
        flags=flags,
    )
