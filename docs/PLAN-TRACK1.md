# Plan: track 1 (slices 0-5)

Status: proposed 2026-11-12, awaiting Olly's approval. Inputs: `gps-camera-trigger/docs/PIPELINE-HANDOFF.md` (sections 4-7), `docs/OUTPUT-DESIGN.md` v0.2, `research/STATUS.md` (audit).

## Scope

Turn the POC scripts into a tested Python package that takes one logger session plus up to four camera folders and produces, for every photo, a GPS exposure time and an RTK antenna position with quality flags. Also produce the rig config and lever-arm maths that the COLMAP slice (6) will use. Everything runs on the Mac with no GPU and no real images: field test 1's session folder and `photos_exif.tsv` plus synthetic fixtures cover every test. Slices 6 onwards (COLMAP, path, profile, splats, packaging) are out of scope.

## Ground rules for every slice

- Branch `pipeline/track1` off `research/output-design`; small commits, conventional messages, no AI trailers, no push without Olly's go-ahead.
- TDD: failing test first (watch it fail for the expected reason), smallest change, refactor.
- Fail loudly: anything that can't be timed or positioned is an error with its location, never a guess.
- Each slice ends with: a command Olly can run, an independent review by a fresh subagent given the code and this plan (not my conclusions), and its findings fixed before the next slice.

## Environment facts (checked 2026-11-12)

- `brew` offers COLMAP 4.2.1 (installed: 4.1.1). 4.2.0 fixed pose-prior alignment and scale for rigs, so the upgrade matters for slice 6, not 0-5.
- pycolmap 4.2.1 on PyPI has macOS arm64 wheels for CPython 3.10-3.14.
- `uv` and `exiftool` are installed.

## Slices

### Slice 0: repo hygiene

- `pyproject.toml` managed by `uv`, Python pinned to 3.12 (matches what CUDA/gsplat stacks on the AWS GPU support; 3.14 here is too new for torch stacks). Runtime deps: numpy, pycolmap 4.2.1 (pinned to the COLMAP version). Dev: pytest, ruff. `uv.lock` committed.
- `src/georoute/` package (name open), `tests/`, `tests/fixtures/`. A `georoute` CLI entry point.
- Fix the broken `.gitignore` line; ignore datasets and outputs.
- Delete the JS duplicate, `package.json`/lock and the root `run_nerfstudio_colmap.sh` (Olly dropped the legacy workflow).
- README: setup (`uv sync`, `uv run pytest`), system deps (COLMAP 4.2.1, exiftool), where the design and research live.
- Done when: `uv run pytest` and `uv run ruff check` pass on a fresh clone following the README.

### Slice 1: characterise what survives, then retire the rest

Under v0.2 only two pieces of POC code survive: the LLA/ECEF/ENU maths and Umeyama (as a diagnostic). `path_cam`, the tile manifest and the GPX/EXIF/dt chain are replaced by the v0.2 contract (slice 7) or dropped, so characterising them would be wasted work, and their bugs (SubSec leading zeros, teleport cascade, silent clamping, missing elevation) disappear with them.

- `georoute/geodesy.py`: move LLA to ECEF to ENU from `gpx_to_enu_raw.py`. Characterisation tests pin current outputs, plus an independent oracle (closed-form known points, round trips) so the move is verified, not assumed.
- Add frames: an ECEF-anchored local ENU frame defined by an origin (lat, lon, ellipsoidal h, datum label), with exact transforms between frames (v0.2 section 2). Velocity rotation NED to ENU to ECEF.
- `georoute/alignment.py`: move Umeyama; **fix the reflection-case scale** (trace(DS), audit item 6) with a failing test first (a nearly collinear, nearly planar point set that triggers the reflection branch).
- Delete the GPX, EXIF-timing, dt-calibration and duplicate COLMAP reader code. `generate_path_cam_from_aligned_colmap.py` and `generate_tile_manifest.py` stay untouched until slice 7 replaces them.
- Done when: geodesy and alignment are pinned and tested; legacy timing code is gone.

### Slice 2: logger session ingest (contract v1)

`georoute/session.py`, `georoute/report.py`.

- Read `session.json`, or `session.json.tmp` when that's all there is; an unclean stop (`stop_utc: null`) is accepted and reported with its `updated_utc`.
- Parse `pulses.csv`, `tm2.csv`, `pvt.csv` against the exact v1 headers; unknown or missing columns refuse.
- TM2: rising-edge GPS time `wnR*604800 + towMsR/1e3 + towSubMsR/1e9`; any row needed by a pulse with `timeBase != 1` or `timeValid != 1` refuses; pulse and TM2 joined one to one by `pulse_n`.
- PVT: absolute GPS time per epoch from `utc` + `nano` + the GPS-UTC offset, cross-checked against `iTOW` (within 1 ms); weeks derived per row, so a session crossing the week rollover works. Epoch gaps computed from the data, not trusted from `missed_epochs`. `corrAge` kept as its text bucket.
- Integrity report (JSON + readable text): counts recomputed and compared with `session.json` (mismatches reported, e.g. field test 1's `missed_epochs` 1 vs zero gaps in `pvt.csv`), fix-quality breakdown, longest non-fixed run in seconds and metres, gaps, `rx_ring_lost`, `sync_late`, per-camera photo counts expected.
- Fixtures: a trimmed, internally consistent cut of field test 1 (about 20 pulses, matching TM2 and PVT rows) committed under `tests/fixtures/`; synthetic cases generated in tests: blank delays, unclean stop, `.tmp` only, week rollover, invalid TM2, wrong header, duplicate `pulse_n`, pulse without TM2.
- Full field test 1 test reads `$GEOROUTE_FT1` (defaults to the sibling logger repo's sample) and is skipped if absent.
- Olly's check: `georoute session-report <session_dir>` prints 434 pulses, 434 TM2, 1415 PVT (1160 fixed, 252 float, 3 none) and flags the `missed_epochs` mismatch.

### Slice 3: photo ingest and pulse matching

`georoute/photos.py`.

- Camera folders `images/cam0`..`cam3` (names from the rig config, slice 5; slice 3 uses a minimal stand-in).
- EXIF via one `exiftool -json -n` call per folder (Canon `FileNumber` and body `SerialNumber` are MakerNote tags Pillow can't read). Parsing works on exiftool's JSON records, so most tests feed records directly.
- Order by Canon `FileNumber` (folder plus file number, so IMG_9999 to IMG_0001 rollover is handled); fallback to filename with rollover detection. Cross-check that EXIF `DateTimeOriginal` + `SubSecTimeOriginal` (parsed as a decimal fraction, leading zeros kept) never runs backwards.
- If the rig config gives a body serial per camera, a photo whose serial doesn't match its folder refuses (catches swapped SD cards).
- Matching rule: for camera N, the k-th photo is the k-th pulse with a non-blank `camN_delay_us`. Count mismatch refuses. Interval cross-check: consecutive EXIF intervals must track TM2 intervals (tolerance set from the 10 ms SubSec resolution); the report names the first divergence so an extra or missing photo is locatable.
- Warm-up pulses take part in matching (they consume photos); whether warm-up photos are kept is a later filter.
- Fixtures: record-level synthetic sets (4 cameras, missing photos, extra photo, rollover, swapped card); one integration test writes EXIF-only stub JPEGs from `photos_exif.tsv` with exiftool and runs the whole path.
- Olly's check: `georoute match <session_dir> <images_root>` reports 432/432 matched for field test 1 (stub photos), and a deleted stub produces a loud failure naming where.

### Slice 4: exposure time, antenna position, quality

`georoute/timing.py`, `georoute/trajectory.py`.

- `t_X = T_cmd + camN_delay_us * 1e-6`; `t_mid = t_X + E/2 - Tc/2` with E from EXIF and Tc from the rig config (default 0). Times are float64 GPS seconds (about 0.2 µs resolution).
- Cubic Hermite between the bracketing PVT epochs using NAV-PVT velocity, done in **ECEF** (velocity rotated NED to ECEF per epoch), so it is frame-independent and works over any route length; results expressed in any ENU frame from slice 1.
- Per-image quality: worst `carrSoln` of the two epochs, interpolated hAcc/vAcc, bracket gap; flag a gap over 1.5 epochs or no fixed solution; outside the track refuses.
- Output: `ingest/images.json` (image path, camera, pulse, kind, t_mid, antenna ECEF and build-frame ENU, quality) plus the run report, and a PNG plot of priors over the track for review.
- Tests against analytic trajectories: constant acceleration (Hermite exact), circular arc and 2 Hz bob (error bounds stated), week-rollover times, gap and float flags.
- Olly's check: `georoute ingest <session_dir> <images_root>` on field test 1 writes `images.json`, the report and the plot; the plot shows all 432 priors on the track, coloured by fix.

### Slice 5: rig config and lever arm

`georoute/rig.py`.

- Versioned TOML rig config (stdlib `tomllib`, comments allowed): camera index to role, folder and body serial; nominal camera extrinsics in the rig frame; rig reference point; antenna ARP offset and ANN-MB3 phase-centre offset (from the u-blox data sheet, cited); Tc; antenna height above the road at rest (QA only). A config is validated on load and hashed into provenance.
- Pure functions: camera centre from antenna position and rig attitude, `c = a + R_world_from_rig (t_cam - l_ant)`, each camera at its own `t_mid` (attitude interpolated by slerp); rig reference point from the same inputs (feeds the v0.2 road-contact path in slice 7).
- The first-pass then re-align loop is written against an aligner interface; slice 5 tests it with a synthetic aligner, slice 6 plugs in COLMAP.
- Tests: a synthetic rig on a known trajectory round-trips to known centres; first-pass error is bounded by the lever arm; the loop converges in one or two iterations; per-camera time offsets are honoured.
- Olly's check: `georoute rig-check <rig.toml>` validates a config and prints the derived geometry; a sample config for the planned 4-camera layout (placeholder numbers until the rig is measured) is committed.

## Risks

- **Real images arrive only with field test 2**, so slices 3-5 are proven on stubs and synthetic data; the photo path gets its first real run in slice 6. Mitigation: stubs carry the real EXIF of all 432 field-test photos.
- **Canon MakerNote tags can't be written into stubs**, so `FileNumber` and serial checks are tested at the record level only until real photos exist.
- **ANN-MB3 phase-centre offset** needs a primary source; if none, it becomes a measured rig-config value.
- **Package name and Python 3.12 pin** are easy to change now, harder after slice 6.
- Reversibility: all work is on a branch; nothing touches the logger repo or any published asset.
