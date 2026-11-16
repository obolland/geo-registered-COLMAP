"""Orthometric heights from the OSGM15 geoid. The grid is a build input, not part of the repo: it lives in a
data folder outside git, and its hash goes into the route package's provenance."""

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pyproj import Transformer

GEOID_NAME = "OSGM15"
GRID_FILENAME = "uk_os_OSGM15_GB.tif"
GRID_URL = "https://cdn.proj.org/uk_os_OSGM15_GB.tif"
DEFAULT_DIR = Path.home() / ".local/share/georoute/proj"


class GeoidError(ValueError):
    pass


@dataclass(frozen=True)
class Geoid:
    """OSGM15, Great Britain. vgridshift with multiplier -1 turns an ellipsoidal height into an orthometric
    one; the grid is addressed by absolute path so PROJ's own data folder isn't modified."""

    name: str
    grid_path: Path
    sha256: str
    transformer: object = field(repr=False, compare=False)

    def orthometric_height(self, lat_deg, lon_deg, h_m):
        lat, lon = np.asarray(lat_deg, float), np.asarray(lon_deg, float)
        height = np.asarray(h_m, float)
        try:
            _, _, orthometric = self.transformer.transform(lon, lat, height)
        except Exception as e:
            raise GeoidError(f"{self.name} can't be evaluated at lat {lat}, lon {lon}: {e}") from e
        orthometric = np.asarray(orthometric, float)
        if not np.all(np.isfinite(orthometric)):
            bad = np.flatnonzero(~np.isfinite(orthometric))
            first = bad[0] if len(bad) else 0
            raise GeoidError(
                f"{self.name} has no coverage at lat {np.ravel(lat)[first]:.6f}, "
                f"lon {np.ravel(lon)[first]:.6f} (grid {self.grid_path})"
            )
        return orthometric

    def provenance(self) -> dict:
        return {"name": self.name, "grid": Path(self.grid_path).name, "sha256": self.sha256}


def load_geoid(path=None) -> Geoid:
    """The OSGM15 grid at path, or at $GEOROUTE_PROJ_DATA/uk_os_OSGM15_GB.tif, or the default data folder.
    Missing or unreadable grids refuse with the URL and path rather than silently using ellipsoidal
    heights."""
    path = (
        Path(path)
        if path is not None
        else Path(os.environ.get("GEOROUTE_PROJ_DATA", DEFAULT_DIR)) / GRID_FILENAME
    )
    if not path.is_file():
        raise GeoidError(
            f"{GEOID_NAME} grid {path} is missing; download {GRID_URL} to {path} "
            "(or set GEOROUTE_PROJ_DATA to the folder holding it)"
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        transformer = Transformer.from_pipeline(f"+proj=vgridshift +grids=@{path.resolve()} +multiplier=-1")
        # A corrupt or unrelated grid may build a pipeline and only fail (with infinity) when evaluated.
        probe = np.asarray(transformer.transform(-4.0, 51.0, 100.0)[2], float)
    except Exception as e:
        raise GeoidError(f"can't build the {GEOID_NAME} transformation from {path}: {e}") from e
    # OSGM15's undulation over southern Britain is about 53 m. A corrupt grid often builds an identity
    # pipeline rather than failing, so the value, not only the pipeline's existence, is checked.
    undulation = 100.0 - probe
    if not np.isfinite(undulation) or not 40.0 <= undulation <= 60.0:
        raise GeoidError(
            f"{path} isn't a usable {GEOID_NAME} Great Britain grid: undulation at lon -4, lat 51 is "
            f"{undulation:.1f} m, expected about 53 m"
        )
    return Geoid(GEOID_NAME, path, digest, transformer)
