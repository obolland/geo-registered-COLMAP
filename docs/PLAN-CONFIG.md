# Plan: hardware-configurable pipeline

Status: built 2026-11-16 (branch `config`). Requirement (Olly): adding cameras, changing camera type or changing rig must be a config change, not a code change. The rig config (`rigs/*.toml`) already covers layout, folders, serials, positions, angles, antenna, curtain travel and calibration.

## Changes

1. **Camera count from the data, not a constant.**
   - The session's cameras are whatever `camN_delay_us` columns `pulses.csv` has (contiguous from cam0).
   - Matching, ingest and the CLI's `--camera-fault` work over those cameras.
   - The rig config may list any subset of them, and every rig camera must exist in the session.
   - `CAMERA_COUNT = 4` goes.
   - The logger itself still writes 4 columns, so more physical cameras needs a logger change (more sync inputs): a separate, agreed piece of work. The pipeline will be ready for it.
2. **Camera type in the rig config.** A `[camera_defaults]` table, which any camera can override:
   - `colmap_model` (default `OPENCV`, for example a fisheye model for a different lens)
   - `shutter_delay_ms = [min, max]` (default 30-500, the 100D)
   - `max_exposure_s` (default 30)
   - `file_number_wrap` (default 9999, Canon)

   Session ingest keeps only generic sanity limits; the camera-specific limits are checked where the rig is known (matching and ingest).
3. **Reference camera in the rig config:** a top-level `reference_camera` (default: the first camera), used by the report and calibration instead of the hard-coded cam2.
4. **Rig schema 1.1.0**, still reading 1.0.0 files with the defaults above.

## Tests and done-when

- A synthetic 5-camera session and rig goes through session ingest, matching and ingest without code changes.
- A rig whose cameras use a fisheye COLMAP model prepares a workspace with that model.
- Camera-specific delay and exposure limits come from the rig.
- The reference camera comes from the rig.
- Field test 2 still ingests and reports identically with the current rig files.

## Rig schema 1.1.0 keys (all optional)

```toml
reference_camera = 2             # defines the rig axes in calibration; default the lowest-numbered camera

[camera_defaults]                # for every camera; any [[camera]] table may override a key
colmap_model = "OPENCV"          # COLMAP camera model, for example OPENCV_FISHEYE for a fisheye lens
shutter_delay_ms = [30.0, 500.0] # plausible shutter lag after the trigger
max_exposure_s = 30.0
file_number_wrap = 9999          # last file number before the camera wraps to 0001
```

A 1.0.0 file using any of these keys refuses, so an old file is never silently reinterpreted.
