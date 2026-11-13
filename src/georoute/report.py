"""Integrity report for a logger session: what can be trusted, what can't, and what the logger said."""

import numpy as np

from georoute.rtcm import base_positions
from georoute.session import CAMERA_COUNT, Session, SessionError
from georoute.trajectory import position_jumps

# Lost pulse rows break photo-to-pulse matching; the others show up in the data the report checks.
FATAL_COUNTERS = ("pulses_lost",)
WARNING_COUNTERS = (
    "ntrip_connects",
    "ntrip_policy_reconnects",
    "rx_ring_lost",
    "sync_late",
    "tm2_unmatched",
    "checksum_errors",
    "decode_errors",
    "discarded_bytes",
    "rtcm_dropped_at_uart",
)


def session_report(s: Session) -> dict:
    errors: list[str] = []
    warnings: list[str] = list(s.load_warnings)
    pvt = _pvt_summary(s)
    pulse_numbers = [p.n for p in s.pulses]
    if pulse_numbers != sorted(pulse_numbers):
        errors.append("pulse numbers are out of order in pulses.csv")
    elif pulse_numbers != list(range(len(pulse_numbers))):
        warnings.append("pulse numbers are not a contiguous 0..N-1 sequence")
    errors += [f"TIM-TM2 row for pulse {n} has no pulse" for n in sorted(set(s.tm2) - set(pulse_numbers))]

    if not s.clean_stop:
        warnings.append(
            f"session did not stop cleanly; metadata from {s.meta_file}, "
            f"last checkpoint {s.meta.get('updated_utc')}"
        )

    t0, t1 = s.pvt.gps_time_s[0], s.pvt.gps_time_s[-1]
    for p in s.pulses:
        photographed = any(d is not None for d in p.delays_us)
        try:
            t = s.command_time(p.n)
        except SessionError as e:
            (errors if photographed else warnings).append(str(e))
            continue
        if photographed and not t0 <= t <= t1:
            errors.append(f"pulse {p.n} at GPS {t:.3f} is outside the PVT track")

    counts = s.meta.get("counts", {})
    recomputed = {
        "pulses": len(s.pulses),
        "tm2_rows": len(s.tm2),
        "pvt_rows": len(s.pvt.gps_time_s),
        "missed_epochs": pvt["missed_epochs"],
    }
    if s.clean_stop:
        for key, value in recomputed.items():
            if key in counts and counts[key] != value:
                warnings.append(f"session.json says {key} {counts[key]}, the data has {value}")
    errors += [f"logger counter {k} is {counts[k]}" for k in FATAL_COUNTERS if counts.get(k)]
    if pvt["position_jumps"]["count"]:
        warnings.append(
            f"{pvt['position_jumps']['count']} position jumps the Doppler velocity doesn't explain, "
            f"first at epoch {pvt['position_jumps']['first_epochs'][0]}; photos there are flagged"
        )
    base = _base(s, errors, warnings)
    warnings += [f"logger counter {k} is {counts[k]}" for k in WARNING_COUNTERS if counts.get(k)]

    return {
        "session": s.path.name,
        "meta_file": s.meta_file,
        "clean_stop": s.clean_stop,
        "datum": s.datum,
        "pulses": {
            "total": len(s.pulses),
            "warmup": sum(p.kind == "warmup" for p in s.pulses),
            "capture": sum(p.kind == "capture" for p in s.pulses),
            "with_tm2": sum(p.n in s.tm2 for p in s.pulses),
        },
        "photos_expected": {
            f"cam{c}": {
                k: sum(p.kind == k and p.delays_us[c] is not None for p in s.pulses)
                for k in ("warmup", "capture")
            }
            for c in range(CAMERA_COUNT)
        },
        "pvt": pvt,
        "base": base,
        "errors": errors,
        "warnings": warnings,
    }


def _base(s: Session, errors: list[str], warnings: list[str]) -> list[dict]:
    path = s.path / "rtcm.bin"
    if not path.exists():
        warnings.append("no rtcm.bin: the base station can't be checked")
        return []
    found = [{"station": b.station, "ecef": list(b.ecef), "messages": b.count} for b in base_positions(path)]
    if not found:
        warnings.append("rtcm.bin has no base position message (1005/1006)")
    elif len(found) > 1:
        errors.append(
            f"the base station moved during the session: {len(found)} different reference positions"
        )
    return found


def _pvt_summary(s: Session) -> dict:
    t, carr, period = s.pvt.gps_time_s, s.pvt.carr_soln, s.nav_period_s
    steps = np.diff(t)
    jumps = position_jumps(s.pvt)
    missed = int(np.sum(np.maximum(np.rint(steps / period) - 1, 0)))
    speed = np.hypot(s.pvt.vel_ned_mps[:, 0], s.pvt.vel_ned_mps[:, 1])
    best = (0, 0)
    start = None
    for i, fixed in enumerate(np.append(carr == 2, True)):
        if not fixed and start is None:
            start = i
        elif fixed and start is not None:
            best = max(best, (i - start, start))
            start = None
    epochs, first = best
    return {
        "rows": len(t),
        "fix": {
            "fixed": int(np.sum(carr == 2)),
            "float": int(np.sum(carr == 1)),
            "none": int(np.sum(carr == 0)),
        },
        "missed_epochs": missed,
        "position_jumps": {
            "count": int(jumps.sum()),
            "first_epochs": [int(k) + 1 for k in np.flatnonzero(jumps)[:5]],
        },
        "largest_gap_s": float(steps.max()) if len(steps) else 0.0,
        "median_h_acc_m": float(np.median(s.pvt.h_acc_m)),
        "longest_non_fixed_run": {
            "epochs": epochs,
            "seconds": float(t[first + epochs - 1] - t[first] + period) if epochs else 0.0,
            "metres": float(np.sum((speed * np.append(steps, period))[first : first + epochs])),
            "starts_gps_s": float(t[first]) if epochs else None,
        },
    }


def format_report(r: dict) -> str:
    p, v, run = r["pulses"], r["pvt"], r["pvt"]["longest_non_fixed_run"]
    lines = [
        f"session {r['session']} ({r['meta_file']}, {'clean stop' if r['clean_stop'] else 'UNCLEAN STOP'}), "
        f"datum {r['datum']}",
        f"pulses {p['total']} ({p['warmup']} warm-up, {p['capture']} capture), {p['with_tm2']} with TIM-TM2",
        "photos expected: "
        + ", ".join(
            f"{cam} {c['capture']} (+{c['warmup']} warm-up)" for cam, c in r["photos_expected"].items()
        ),
        f"PVT {v['rows']} epochs: fixed {v['fix']['fixed']}, float {v['fix']['float']}, "
        f"none {v['fix']['none']}; "
        f"missed {v['missed_epochs']}, largest gap {v['largest_gap_s']:.1f} s, "
        f"median hAcc {v['median_h_acc_m'] * 1000:.0f} mm",
        f"longest non-fixed run {run['epochs']} epochs, {run['seconds']:.1f} s, about {run['metres']:.0f} m",
    ]
    lines += [f"WARNING {w}" for w in r["warnings"]]
    lines += [f"ERROR {e}" for e in r["errors"]]
    return "\n".join(lines)
