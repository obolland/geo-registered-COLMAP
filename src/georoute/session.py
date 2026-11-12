"""Reads a gps-camera-trigger logger session (data contract v1) and refuses anything it can't trust."""

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

CAMERA_COUNT = 4
WEEK_S = 604800
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=UTC)
# GPS-UTC has been 18 s since 2017-01-01 and no further leap second has been scheduled.
GPS_MINUS_UTC_S = 18
LEAP_VALID_FROM = datetime(2017, 1, 1, tzinfo=UTC)

PULSES_HEADER = ["pulse_n", "kind", "cmd_ticks_us"] + [f"cam{i}_delay_us" for i in range(CAMERA_COUNT)]
TM2_HEADER = [
    "pulse_n",
    "count",
    "wnR",
    "towMsR",
    "towSubMsR",
    "wnF",
    "towMsF",
    "towSubMsF",
    "timeBase",
    "timeValid",
    "accEst_ns",
]
PVT_HEADER = [
    "iTOW",
    "nano",
    "utc",
    "fixType",
    "carrSoln",
    "diff",
    "numSV",
    "lat",
    "lon",
    "height_m",
    "hMSL_m",
    "velN",
    "velE",
    "velD",
    "hAcc_mm",
    "vAcc_mm",
    "sAcc_mm_s",
    "pDOP",
    "corrAge",
]


class SessionError(ValueError):
    pass


@dataclass(frozen=True)
class Pulse:
    n: int
    kind: str
    cmd_ticks_us: int
    delays_us: tuple[int | None, ...]


@dataclass(frozen=True)
class Tm2:
    pulse_n: int
    rising_gps_s: float
    time_base: int
    time_valid: bool
    acc_ns: int


@dataclass(frozen=True)
class PvtTrack:
    gps_time_s: np.ndarray
    itow_ms: np.ndarray
    fix_type: np.ndarray
    carr_soln: np.ndarray
    diff: np.ndarray
    num_sv: np.ndarray
    lat_deg: np.ndarray
    lon_deg: np.ndarray
    height_m: np.ndarray
    vel_ned_mps: np.ndarray
    h_acc_m: np.ndarray
    v_acc_m: np.ndarray
    corr_age: list[str]


@dataclass(frozen=True)
class Session:
    path: Path
    meta: dict
    meta_file: str
    pulses: list[Pulse]
    tm2: dict[int, Tm2]
    pvt: PvtTrack

    @property
    def clean_stop(self) -> bool:
        return self.meta.get("stop_utc") is not None

    @property
    def datum(self) -> str:
        return self.meta["settings"]["datum"]

    @property
    def nav_period_s(self) -> float:
        return self.meta["settings"]["nav_period_ms"] / 1000

    def command_time(self, pulse_n: int) -> float:
        """GPS seconds of the pulse's command edge; refuses if the time mark isn't trustworthy."""
        t = self.tm2.get(pulse_n)
        if t is None:
            raise SessionError(f"pulse {pulse_n} has no TIM-TM2 row")
        if t.time_base != 1 or not t.time_valid:
            raise SessionError(
                f"pulse {pulse_n} TIM-TM2 is not valid GPS time "
                f"(timeBase {t.time_base}, timeValid {t.time_valid})"
            )
        return t.rising_gps_s


def load_session(path) -> Session:
    path = Path(path)
    meta, meta_file = _read_meta(path)
    return Session(
        path,
        meta,
        meta_file,
        _read_pulses(path / "pulses.csv"),
        _read_tm2(path / "tm2.csv"),
        _read_pvt(path / "pvt.csv"),
    )


def _read_meta(path: Path) -> tuple[dict, str]:
    for name in ("session.json", "session.json.tmp"):
        if (path / name).exists():
            return json.loads((path / name).read_text()), name
    raise SessionError(f"{path} has neither session.json nor session.json.tmp")


def _rows(path: Path, header: list[str]):
    with open(path, newline="") as f:
        reader = csv.reader(f)
        found = next(reader, None)
        if found != header:
            raise SessionError(f"{path.name} header is {found}, expected contract v1 {header}")
        for line, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise SessionError(f"{path.name}:{line} has {len(row)} fields, expected {len(header)}")
            yield line, dict(zip(header, row, strict=True))


def _unique(path: Path, n: int, seen: set, line: int):
    if n in seen:
        raise SessionError(f"{path.name}:{line} repeats pulse {n}")
    seen.add(n)


def _read_pulses(path: Path) -> list[Pulse]:
    pulses, seen = [], set()
    for line, r in _rows(path, PULSES_HEADER):
        n = int(r["pulse_n"])
        _unique(path, n, seen, line)
        if r["kind"] not in ("warmup", "capture"):
            raise SessionError(f"{path.name}:{line} has unknown kind {r['kind']!r}")
        delays = tuple(
            int(r[f"cam{i}_delay_us"]) if r[f"cam{i}_delay_us"] else None for i in range(CAMERA_COUNT)
        )
        pulses.append(Pulse(n, r["kind"], int(r["cmd_ticks_us"]), delays))
    return pulses


def _read_tm2(path: Path) -> dict[int, Tm2]:
    rows, seen = {}, set()
    for line, r in _rows(path, TM2_HEADER):
        n = int(r["pulse_n"])
        _unique(path, n, seen, line)
        rising = int(r["wnR"]) * WEEK_S + int(r["towMsR"]) / 1e3 + int(r["towSubMsR"]) / 1e9
        rows[n] = Tm2(n, rising, int(r["timeBase"]), r["timeValid"] == "1", int(r["accEst_ns"]))
    return rows


def _gps_seconds(utc: str, nano: int) -> float:
    t = datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    if t < LEAP_VALID_FROM:
        raise SessionError(f"UTC {utc} predates the GPS-UTC offset this code knows")
    return int((t - GPS_EPOCH).total_seconds()) + GPS_MINUS_UTC_S + nano / 1e9


def _read_pvt(path: Path) -> PvtTrack:
    cols: dict[str, list] = {c: [] for c in PVT_HEADER}
    gps = []
    for line, r in _rows(path, PVT_HEADER):
        t = _gps_seconds(r["utc"], int(r["nano"]))
        tow_ms = (t % WEEK_S) * 1000
        off = (tow_ms - int(r["iTOW"]) + WEEK_S * 500) % (WEEK_S * 1000) - WEEK_S * 500
        if abs(off) > 1.0:
            raise SessionError(f"{path.name}:{line} utc+nano disagrees with iTOW by {off:.3f} ms")
        gps.append(t)
        for c in PVT_HEADER:
            cols[c].append(r[c])
    f = lambda c, scale=1.0: np.array(cols[c], float) * scale  # noqa: E731
    i = lambda c: np.array(cols[c], int)  # noqa: E731
    return PvtTrack(
        gps_time_s=np.array(gps),
        itow_ms=i("iTOW"),
        fix_type=i("fixType"),
        carr_soln=i("carrSoln"),
        diff=i("diff"),
        num_sv=i("numSV"),
        lat_deg=f("lat"),
        lon_deg=f("lon"),
        height_m=f("height_m"),
        vel_ned_mps=np.stack([f("velN"), f("velE"), f("velD")], axis=-1) / 1000,
        h_acc_m=f("hAcc_mm", 1e-3),
        v_acc_m=f("vAcc_mm", 1e-3),
        corr_age=cols["corrAge"],
    )
