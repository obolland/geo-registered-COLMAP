# georoute

Turns a GNSS-timed multi-camera capture (from the [gps-camera-trigger](https://github.com/obolland/gps-camera-trigger) logger) into a geo-registered COLMAP reconstruction and streamable Gaussian-splat route packages for a WebXR cycling app.

Work in progress. The design is in `docs/OUTPUT-DESIGN.md`, the current build plan in `docs/PLAN-TRACK1.md`, and the research and decisions behind both in `research/` (start with `research/STATUS.md`).

## Set-up

System dependencies (macOS, Homebrew):

```bash
brew install uv exiftool colmap   # COLMAP must be 4.2.1 (colmap -h shows the version)
```

Then, from the repo root:

```bash
uv sync                 # creates .venv with Python 3.12 and the pinned dependencies
uv run pytest           # tests
uv run ruff check .     # lint
uv run georoute --help  # CLI
```

Python is pinned to 3.12 to match the CUDA training stack on the GPU machine. `pycolmap` is pinned to the same version as the COLMAP binary.

## Legacy POC

`align_colmap_to_gpx_enu/` holds what is left of the proof of concept: `run_colmap_reconstruction.sh` (replaced by COLMAP orchestration in slice 6, after this plan), `generate_path_cam_from_aligned_colmap.py` and `generate_tile_manifest.py` (replaced by the v0.2 route package in slice 7). The GPX and EXIF-timing workflow has been removed. Don't build on these.
