# georoute

Turns a GNSS-timed multi-camera capture (from the [gps-camera-trigger](https://github.com/obolland/gps-camera-trigger) logger) into a geo-registered COLMAP reconstruction and streamable Gaussian-splat route packages for a WebXR cycling app.

Work in progress. The design is in `docs/OUTPUT-DESIGN.md`, the current build plan in `docs/PLAN-TRACK1.md`, and the research and decisions behind both in `research/` (start with `research/STATUS.md`).

## Set-up

System dependencies (macOS, Homebrew):

```bash
brew install uv exiftool colmap   # COLMAP 4.2.1 or later
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

`align_colmap_to_gpx_enu/` holds the original proof-of-concept scripts. The GPX and EXIF-timing workflow is being retired (slice 1); `generate_path_cam_from_aligned_colmap.py` and `generate_tile_manifest.py` remain until the v0.2 route package replaces them (slice 7). Don't build on them.
