from pathlib import Path

import numpy as np
import pytest

from georoute.geoid import GRID_FILENAME, GRID_URL, Geoid, GeoidError, load_geoid

GRID = Path.home() / ".local/share/georoute/proj" / GRID_FILENAME
EXPECTED_SHA256 = "bf0ac81f1c0c33332693d7d606f335b793c3c2995b8e69eeb1350bd94adcde3b"


def test_a_missing_grid_refuses_and_names_the_file_url_and_where_it_belongs(tmp_path):
    missing = tmp_path / "missing.tif"

    with pytest.raises(GeoidError) as error:
        load_geoid(missing)

    message = str(error.value)
    assert str(missing) in message
    assert GRID_URL in message


@pytest.mark.skipif(not GRID.exists(), reason="OSGM15 grid not downloaded")
def test_the_grid_is_hashed_and_its_undulation_matches_the_published_value():
    geoid = load_geoid()

    assert geoid.name == "OSGM15"
    assert geoid.provenance()["sha256"] == EXPECTED_SHA256
    height = geoid.orthometric_height(51.1587509, -4.1244003, 208.018)
    assert height == pytest.approx(155.018412, abs=1e-5)


@pytest.mark.skipif(not GRID.exists(), reason="OSGM15 grid not downloaded")
def test_a_point_outside_the_grid_refuses_rather_than_returning_infinity():
    geoid = load_geoid()

    with pytest.raises(GeoidError, match="coverage"):
        geoid.orthometric_height(10.0, 10.0, 100.0)


def test_a_non_finite_transform_refuses_naming_the_coordinates():
    class Infinity:
        def transform(self, lon, lat, height):
            return lon, lat, np.full_like(np.asarray(height, float), np.inf)

    geoid = Geoid("OSGM15", Path("/tmp/grid.tif"), "0" * 64, Infinity())

    with pytest.raises(GeoidError, match="51.000000.*-4.000000"):
        geoid.orthometric_height(51.0, -4.0, 100.0)


def test_a_corrupt_grid_refuses_naming_the_file(tmp_path):
    grid = tmp_path / "grid.tif"
    grid.write_bytes(b"pretend geoid")

    with pytest.raises(GeoidError, match=str(grid)):
        load_geoid(grid)
