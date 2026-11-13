from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Ellipsoid:
    a: float
    f: float

    @property
    def e2(self) -> float:
        return self.f * (2.0 - self.f)


# ETRS89 uses GRS80, whose flattening differs from WGS84's by about 1e-11; sub-micrometre at the surface.
WGS84 = Ellipsoid(a=6378137.0, f=1.0 / 298.257223563)


def lla_to_ecef(lat_deg, lon_deg, h_m, ellipsoid: Ellipsoid = WGS84) -> np.ndarray:
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    n = ellipsoid.a / np.sqrt(1.0 - ellipsoid.e2 * np.sin(lat) ** 2)
    return np.stack(
        [
            (n + h_m) * np.cos(lat) * np.cos(lon),
            (n + h_m) * np.cos(lat) * np.sin(lon),
            (n * (1.0 - ellipsoid.e2) + h_m) * np.sin(lat),
        ],
        axis=-1,
    )


def ecef_to_lla(ecef, ellipsoid: Ellipsoid = WGS84) -> np.ndarray:
    """(..., 3) ECEF to (..., 3) lat, lon (degrees), height. Iterative, well below a millimetre near the
    surface at any latitude including the poles; undefined at the Earth's centre."""
    x, y, z = np.moveaxis(np.asarray(ecef, float), -1, 0)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1.0 - ellipsoid.e2))
    for _ in range(6):
        n = ellipsoid.a / np.sqrt(1.0 - ellipsoid.e2 * np.sin(lat) ** 2)
        h = _height(p, z, lat, n, ellipsoid)
        lat = np.arctan2(z, p * (1.0 - ellipsoid.e2 * n / (n + h)))
    n = ellipsoid.a / np.sqrt(1.0 - ellipsoid.e2 * np.sin(lat) ** 2)
    h = _height(p, z, lat, n, ellipsoid)
    return np.stack([np.degrees(lat), np.degrees(np.arctan2(y, x)), h], axis=-1)


def _height(p, z, lat, n, ellipsoid: Ellipsoid):
    """Uses whichever form avoids dividing by a small cosine or sine."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(
            np.abs(lat) < np.radians(45), p / np.cos(lat) - n, z / np.sin(lat) - n * (1.0 - ellipsoid.e2)
        )


def ecef_from_enu_rotation(lat_deg: float, lon_deg: float) -> np.ndarray:
    """Columns are the local east, north and up axes expressed in ECEF."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    sl, cl, so, co = np.sin(lat), np.cos(lat), np.sin(lon), np.cos(lon)
    return np.array([[-so, -sl * co, cl * co], [co, -sl * so, cl * so], [0.0, cl, sl]])


def ned_to_ecef(lat_deg, lon_deg, ned) -> np.ndarray:
    """Rotates (..., 3) NED vectors to ECEF; lat/lon may be scalars or one per vector."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    n, e, d = np.moveaxis(np.asarray(ned, float), -1, 0)
    sl, cl, so, co = np.sin(lat), np.cos(lat), np.sin(lon), np.cos(lon)
    up = -d
    return np.stack(
        [-so * e - sl * co * n + cl * co * up, co * e - sl * so * n + cl * so * up, cl * n + sl * up], axis=-1
    )


@dataclass(frozen=True)
class LocalFrame:
    """A local east-north-up frame anchored at an origin on the WGS84/GRS80 ellipsoid. The datum is a
    label: frames only combine with frames carrying the same label."""

    lat_deg: float
    lon_deg: float
    h_m: float
    datum: str
    origin_ecef: np.ndarray = field(init=False, repr=False, compare=False)
    ecef_from_enu: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        origin = lla_to_ecef(self.lat_deg, self.lon_deg, self.h_m)
        rotation = ecef_from_enu_rotation(self.lat_deg, self.lon_deg)
        origin.flags.writeable = False
        rotation.flags.writeable = False
        object.__setattr__(self, "origin_ecef", origin)
        object.__setattr__(self, "ecef_from_enu", rotation)

    @classmethod
    def at(cls, lat_deg: float, lon_deg: float, h_m: float, datum: str) -> "LocalFrame":
        return cls(float(lat_deg), float(lon_deg), float(h_m), datum)

    def from_ecef(self, ecef) -> np.ndarray:
        return (np.asarray(ecef, float) - self.origin_ecef) @ self.ecef_from_enu

    def to_ecef(self, enu) -> np.ndarray:
        return np.asarray(enu, float) @ self.ecef_from_enu.T + self.origin_ecef

    def to(self, other: "LocalFrame", enu) -> np.ndarray:
        if other.datum != self.datum:
            raise ValueError(f"can't mix datums {self.datum} and {other.datum} without a transformation")
        return other.from_ecef(self.to_ecef(enu))
