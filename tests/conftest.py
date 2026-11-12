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


def write_stub_photos(images_root, photos):
    """EXIF-only JPEGs from (relative path, camera-clock seconds, exposure s) triples, in one exiftool run."""
    import subprocess
    from datetime import UTC, datetime

    from PIL import Image

    images_root = Path(images_root)
    base = images_root / ".base.jpg"
    images_root.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8)).save(base)
    args = []
    for path, taken_s, exposure_s in photos:
        target = images_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(base, target)
        stamp = datetime.fromtimestamp(taken_s, UTC)
        args += [
            f"-DateTimeOriginal={stamp:%Y:%m:%d %H:%M:%S}",
            f"-SubSecTimeOriginal={stamp.microsecond // 10000:02d}",
            f"-ExposureTime={exposure_s}",
            str(target),
            "-execute",
        ]
    subprocess.run(["exiftool", *args, "-common_args", "-q", "-overwrite_original"], check=True)
    base.unlink()
