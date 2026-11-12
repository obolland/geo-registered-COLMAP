import csv
import os
import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "ft1_trim"
FT1 = Path(
    os.environ.get("GEOROUTE_FT1", Path(__file__).parents[2] / "gps-camera-trigger/samples/20261108T153731Z")
)
needs_ft1 = pytest.mark.skipif(not FT1.exists(), reason="field test 1 sample not available")


@pytest.fixture
def session_dir(tmp_path):
    d = tmp_path / "20261108T153731Z"
    shutil.copytree(FIXTURE, d)
    return d


def edit_csv(path, edit):
    rows = edit(list(csv.reader(path.read_text().splitlines())))
    with open(path, "w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)


def set_cells(column, value, row_indices):
    def edit(rows):
        for i in row_indices:
            rows[i][column] = value
        return rows

    return edit
