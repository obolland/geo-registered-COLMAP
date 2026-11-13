"""Reads a gps-camera-trigger logger session (data contract v1) and refuses anything it can't trust."""

import csv
import json
import math
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

# The logger rejects sync edges sooner than 30 ms; the 100D's lag was 87-123 ms in the field.
DELAY_RANGE_US = (30_000, 500_000)
GPS_WEEK_RANGE = (2000, 4000)
WEEK_MS = WEEK_S * 1000
MAX_SPEED_MM_S = 100_000

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
    load_warnings: list[str]

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
    warnings: list[str] = []
    pulses = _read_pulses(path / "pulses.csv", warnings)
    tm2 = _read_tm2(path / "tm2.csv", warnings)
    pvt = _read_pvt(path / "pvt.csv", warnings)
    return Session(path, meta, meta_file, pulses, tm2, pvt, warnings)


def _read_meta(path: Path) -> tuple[dict, str]:
    for name in ("session.json", "session.json.tmp"):
        if (path / name).exists():
            try:
                meta = json.loads((path / name).read_text())
            except json.JSONDecodeError as e:
                raise SessionError(f"{name} is not valid JSON: {e}") from e
            settings = meta.get("settings") if isinstance(meta, dict) else None
            if not isinstance(settings, dict):
                raise SessionError(f"{name} has no settings table")
            if not isinstance(settings.get("datum"), str):
                raise SessionError(f"{name} settings has no datum")
            period = settings.get("nav_period_ms")
            if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
                raise SessionError(
                    f"{name} settings nav_period_ms must be a positive integer, got {period!r}"
                )
            return meta, name
    raise SessionError(f"{path} has neither session.json nor session.json.tmp")


class _Row:
    """One CSV row whose values convert with errors that name the file, line and column."""

    def __init__(self, path: Path, line: int, values: dict[str, str]):
        self.where, self.values = f"{path.name}:{line}", values

    def text(self, column: str) -> str:
        return self.values[column]

    def integer(self, column: str, low: float = -math.inf, high: float = math.inf) -> int:
        return self._in_range(column, self._convert(column, int), low, high)

    def number(self, column: str, low: float = -math.inf, high: float = math.inf) -> float:
        value = self._convert(column, float)
        if not math.isfinite(value):
            raise SessionError(f"{self.where} {column} {self.values[column]!r} is not a finite number")
        return self._in_range(column, value, low, high)

    def optional_int(self, column: str, low: float = -math.inf, high: float = math.inf) -> int | None:
        return self.integer(column, low, high) if self.values[column] else None

    def _in_range(self, column, value, low, high):
        if not low <= value <= high:
            raise SessionError(f"{self.where} {column} {value} is outside {low:g} to {high:g}")
        return value

    def _convert(self, column, kind):
        try:
            return kind(self.values[column])
        except ValueError:
            raise SessionError(
                f"{self.where} {column} {self.values[column]!r} is not a valid {kind.__name__}"
            ) from None


def _rows(path: Path, header: list[str], warnings: list[str]) -> list[_Row]:
    if not path.exists():
        raise SessionError(f"{path.name} is missing from {path.parent}")
    with open(path, newline="") as f:
        lines = list(csv.reader(f))
    if not lines or lines[0] != header:
        raise SessionError(
            f"{path.name} header is {lines[0] if lines else None}, expected contract v1 {header}"
        )
    rows = []
    for line, values in enumerate(lines[1:], start=2):
        if len(values) != len(header):
            if line == len(lines):
                warnings.append(f"{path.name}:{line} truncated last row dropped (power loss mid-write)")
                continue
            raise SessionError(f"{path.name}:{line} has {len(values)} fields, expected {len(header)}")
        rows.append(_Row(path, line, dict(zip(header, values, strict=True))))
    if not rows:
        raise SessionError(f"{path.name} has no rows")
    return rows


def _check_unique(seen: set, row: _Row, n: int):
    if n in seen:
        raise SessionError(f"{row.where} repeats pulse {n}")
    seen.add(n)


def _read_pulses(path: Path, warnings: list[str]) -> list[Pulse]:
    pulses, seen = [], set()
    for r in _rows(path, PULSES_HEADER, warnings):
        n = r.integer("pulse_n")
        _check_unique(seen, r, n)
        if r.text("kind") not in ("warmup", "capture"):
            raise SessionError(f"{r.where} has unknown kind {r.text('kind')!r}")
        delays = tuple(r.optional_int(f"cam{i}_delay_us", *DELAY_RANGE_US) for i in range(CAMERA_COUNT))
        pulses.append(Pulse(n, r.text("kind"), r.integer("cmd_ticks_us"), delays))
    return pulses


def _read_tm2(path: Path, warnings: list[str]) -> dict[int, Tm2]:
    rows: dict[int, Tm2] = {}
    for r in _rows(path, TM2_HEADER, warnings):
        n = r.integer("pulse_n", 0)
        _check_unique(set(rows), r, n)
        rising = _edge_time(r, "R")
        if r.text("towMsF"):
            falling = _edge_time(r, "F")
            if not 0 < falling - rising < 1.0:
                raise SessionError(
                    f"{r.where} falling edge is {falling - rising:+.3f} s from the rising edge"
                )
        rows[n] = Tm2(
            n, rising, r.integer("timeBase", 0, 2), r.text("timeValid") == "1", r.integer("accEst_ns", 0)
        )
    return rows


def _edge_time(r: "_Row", edge: str) -> float:
    return (
        r.integer(f"wn{edge}", *GPS_WEEK_RANGE) * WEEK_S
        + r.integer(f"towMs{edge}", 0, WEEK_MS - 1) / 1e3
        + r.integer(f"towSubMs{edge}", 0, 999_999) / 1e9
    )


def _gps_seconds(r: _Row) -> float:
    try:
        t = datetime.strptime(r.text("utc"), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise SessionError(f"{r.where} utc {r.text('utc')!r} is not a valid UTC time") from None
    if t < LEAP_VALID_FROM:
        raise SessionError(f"{r.where} UTC {r.text('utc')} predates the GPS-UTC offset this code knows")
    return int((t - GPS_EPOCH).total_seconds()) + GPS_MINUS_UTC_S + r.integer("nano", -1e9, 1e9) / 1e9


def _read_pvt(path: Path, warnings: list[str]) -> PvtTrack:
    rows = _rows(path, PVT_HEADER, warnings)
    if len(rows) < 2:
        raise SessionError(f"{path.name} needs at least two epochs to interpolate between")
    gps = []
    for r in rows:
        t = _gps_seconds(r)
        off = ((t % WEEK_S) * 1000 - r.integer("iTOW", 0, WEEK_MS - 1) + WEEK_MS / 2) % WEEK_MS - WEEK_MS / 2
        if abs(off) > 1.0:
            raise SessionError(f"{r.where} utc+nano+{GPS_MINUS_UTC_S} s disagrees with iTOW by {off:.3f} ms")
        if gps and t <= gps[-1]:
            raise SessionError(f"{r.where} time does not increase from the previous epoch")
        gps.append(t)

    def ints(c, low=-math.inf, high=math.inf):
        return np.array([r.integer(c, low, high) for r in rows])

    def floats(c, scale=1.0, low=-math.inf, high=math.inf):
        return np.array([r.number(c, low, high) for r in rows]) * scale

    return PvtTrack(
        gps_time_s=np.array(gps),
        itow_ms=ints("iTOW", 0, WEEK_MS - 1),
        fix_type=ints("fixType", 0, 5),
        carr_soln=ints("carrSoln", 0, 2),
        diff=ints("diff", 0, 1),
        num_sv=ints("numSV", 0, 255),
        lat_deg=floats("lat", low=-90, high=90),
        lon_deg=floats("lon", low=-180, high=180),
        height_m=floats("height_m", low=-1000, high=10000),
        vel_ned_mps=np.stack(
            [floats(c, low=-MAX_SPEED_MM_S, high=MAX_SPEED_MM_S) for c in ("velN", "velE", "velD")], axis=-1
        )
        / 1000,
        h_acc_m=floats("hAcc_mm", 1e-3, 0),
        v_acc_m=floats("vAcc_mm", 1e-3, 0),
        corr_age=[r.text("corrAge") for r in rows],
    )
