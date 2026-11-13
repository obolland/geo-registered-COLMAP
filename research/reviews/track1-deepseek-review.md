# Track 1 second-family review (DeepSeek)

Status: adversarial review of branch `pipeline/track1` at commit `18e8113` (39 commits on top of `main`: 16 design/research, 23 code), 2026-11-13. The working tree is clean; nothing was modified, committed or pushed.
Method: the `Thinking` skill in RedTeam ParallelAnalysis mode: an 8-agent panel (2 engineers, 2 architects, 2 pentesters, 2 interns), each attacking the whole deliverable, plus direct code reading and probes in `/tmp`. The suite is green: 193 tests pass in about 10 s, `ruff check` clean.
Inputs: `research/STATUS.md`, `docs/PLAN-TRACK1.md`, `docs/OUTPUT-DESIGN.md` v0.2, `src/georoute/*.py`, `tests/*.py`, `tests/fixtures/ft1_trim`, the six per-slice reviews, `gps-camera-trigger/docs/PIPELINE-HANDOFF.md` sections 3-7, the logger's `pico/rows.py` and `pico/session.py`, the real field-test sample, and the PPK scratchpad (configs and `.pos` outputs).

**Verdict.** Track 1 is a competent, well-tested ingest core with correct maths and a sound PPK decision. It has four blockers in its own contract (B1, B2, B5, B6) and two blockers for the slice-6/7 handoff (B3, B4): the fail-loudly boundary leaks on non-finite and out-of-range inputs; the `match` command can accept a weakly verified photo-to-pulse shift; a base-position jump or PVT step passes as fixed; a rig camera missing from the capture is silently skipped; there is no COLMAP rig/frame handoff; and `images.json` has no session/image identity or route/build provenance for multi-session work. B1, B2, B5 and B6 are cheap to fix; B3 and B4 should be settled before field test 2 data is ingested. The timing chain, geodesy, Hermite interpolation, Umeyama, rig geometry and session contract are correct and should not be redesigned.

Severity scale used here: **blocker** = fix before any real 4-camera or COLMAP work; **should-fix** = fix before field test 2 or slice 6; **nit** = cleanup or documentation.

## 1. What is right and should not change

1. **The time chain is correct.** `t_X = T_cmd + delay`, `t_mid = t_X + E/2 - Tc/2`; the TM2 rising edge is the command; the PVT epoch time comes from `utc + nano + 18 s` and is cross-checked against `iTOW` within 1 ms; times are float64 GPS seconds; week rollover and negative `nano` work. Tests cover the hand numbers, the week boundary, the last epoch, negative nano and the iTOW disagreement. The supplied real field-test run produced 431 capture images with 346 fixed, 84 float and 1 no-fix, matching the slice 4 review; the code path that generates those records is unchanged in the relevant respects.
2. **The geodesy is correct.** `lla_to_ecef`/`ecef_to_lla` match pyproj goldens across the poles, the equator, 45 degrees and heights -10 km to +100 km; the ENU rotation, NED-to-ECEF rotation and frame round trips are exact; `LocalFrame` is immutable and value-like after the slice 1 fixes. The WGS84/GRS80 approximation is negligible, but the code comment's "sub-micrometre" overstates it: the ECEF difference is about 0.12 mm at 45-51 degrees (N19).
3. **Hermite interpolation is correct.** The basis, the ECEF frame, the velocity rotation, the exact-at-epoch case, the bend error, the 2 Hz bob bound, the quantisation test, the week boundary, the gap/no-3d-fix/not-fixed flags and the outside-track refusal are all right. The STATUS note that real-data Hermite gain is below measurement noise at walking pace is honest and correct.
4. **Umeyama is correct.** The reflection branch uses `trace(DS)`/`sum(d * sv)`, the scale is the least-squares scale for the returned proper rotation, and the tests cover known similarity, reflection, near-planar and degenerate inputs. The slice 1 review checked it against a multi-start optimiser.
5. **The rig conventions are correct.** `rig_from_camera = Rz(yaw) Rx(pitch) Ry(roll) L`, with `L` mapping camera x/y/z (right/down/forward) to rig x/-z/y; `camera_centre` and `reference_point` use the lever arm in the right direction; `world_from_rig = world_from_camera @ rig_from_camera.T` is the right composition; the tests pin the axes and the composition.
6. **Session ingest is robust where it was reviewed.** Exact headers, truncated final row dropped with a warning, unclean stop and `.tmp` accepted, invalid TM2 refused when used, duplicate pulse numbers refused, malformed values reported with file, line and column, non-monotonic PVT refused, iTOW mismatch refused, header-only files refused. The `_Row` helper is the right pattern.
7. **Photo ingest and matching are right on the documented path.** The k-th rule, warm-up participation, per-camera folders, Canon FileNumber ordering with rollover, mixed/partial FileNumber refusal, serial consistency, exiftool returncode/corrupt-file refusal, strays and clock-backwards refusal are correct; the field-test stub integration matches 432/432 and locates a deleted photo. The exiftool warning policy is too strict for real Canon MakerNotes and macOS `._` files (S17).
8. **The test suite is a genuine asset.** 193 tests in about 10 s, an independent geodesy oracle, a synthetic aligner that imitates a similarity-based aligner, EXIF-stub integration on the real session, and fixtures that mirror the real CSVs. The slice reviews found real issues and the fixes are committed with tests.
9. **The PPK verdict is justified for this data.** Independent parsing of `combined.pos` gives 1302/1415 fixed (92.0%) against 1160/1415 live (82.0%); where both are fixed the median difference is +0.001 m east, +0.003 m north and -0.009 m up, but the last 33 s mean is +0.305 m north and +0.330 m up; there are 53 epoch-to-epoch vertical jumps over 15 cm in the combined solution against 2 live; and over epochs 1100-1300 the vertical change is +3.75 m by integrated Doppler, +3.88 m live and +4.50 m combined. The method (consistency checks, not fix rate) is sound and the conclusion (live RTK is more reliable on this data; do not build PPK now; keep RAWX/RTCM as insurance) is proportionate and correctly scoped.
10. **The decisions are mostly sound.** Dropping warm-up photos after matching, keeping non-fixed photos with their accuracy, one capture pass, Python 3.12 and COLMAP 4.2.1 are coherent. The rig config hash and the `measured` flag are good provenance habits; the CLI warning should become a refusal (S12), and the all-float refusal contradicts the keep-non-fixed decision (S14).

## 2. Blockers

### B1. Non-finite and out-of-range PVT and EXIF values are accepted and silently used

**Where.** `src/georoute/session.py:164-168` (`_Row.number` converts with `float` and never checks `math.isfinite`); `src/georoute/session.py:239-271` (`_read_pvt` never validates lat/lon/height/velocity/accuracy ranges or the `carrSoln`/`fixType` enums); `src/georoute/trajectory.py:50-74` (uses those values; the flags only test `carrSoln < 2` and `fixType < 3`); `src/georoute/photos.py:99-127` (`ExposureTime` is converted with `float` and never checked for finite/positive).

**Reproduction (all with a copy of `tests/fixtures/ft1_trim` in `/tmp`; no repo files changed).**
- Set one fixed `pvt.csv` row's `lat` to `nan`. `georoute session-report` exits 0 with no error. `antenna_positions(s.pvt, [that epoch], s.nav_period_s)` returns `ecef = [nan nan nan]` and `flags = []`.
- Set one fixed row's `lat` to `100.0`. `session-report` exits 0. `antenna_positions` returns ECEF `(-1108323, 79916, 6259748)`, roughly 5000 km from the correct point (`3998032, -288282, 4944808`), with `flags = []`.
- Set one row's `hAcc_mm` to `nan`. `session-report` exits 0 and reports `median h_acc = nan`.
- Set every `carrSoln` to `5` or `7`. `session-report` exits 0 and `fix` is `{fixed: 0, float: 0, none: 0}` for 74 rows (the counts do not sum), while `antenna_positions` and `ingest` treat the value as fixed: `flags = []`, a tight `h_acc_m`, and `carr_soln 5/7` in `images.json`. With `7` the report also shows a 73-epoch non-fixed run. Two outputs disagree and neither refuses.
- Set `carrSoln = 3` on a row next to a fixed epoch, or `fixType = 9` next to a 3D epoch: the bracket `min()` masks the invalid value (the image gets `carr 2`/`fix 3`) with no flag, and the row is counted in none of fixed/float/none. `height = inf` and `velN = nan` behave like `lat = nan`.
- Set `cam0_delay_us = -50000` on a photographed pulse: `t_mid` is 49.5 ms before the command, with no error.
- Set every `fixType` to `5` while leaving `carrSoln = 2` (a corrupted combination; a real time-only fix normally carries `carrSoln = 0`). `session-report` exits 0 and reports 74 fixed; `antenna_positions` reports `flags = []`, so a time-only fix is not flagged no-3D-fix. `fixType = 5` should always be treated as no-3D-fix.
- Set an EXIF record's `ExposureTime` to `nan`. `photos_from_exiftool` returns `exposure_s = nan`; `exposure_mid` returns `nan`; no error. `ExposureTime` 0 or -0.001 is also accepted.
- Keep only one `pvt.csv` data row. `load_session` accepts it; `antenna_positions` at that epoch returns `[nan nan nan]` with a `RuntimeWarning: invalid value encountered in divide` and `flags = []`. A cubic Hermite needs a bracket; a one-epoch track cannot provide one.
- Set `lat = nan` in the two PVT rows bracketing photo 0 (data rows 15-16) and ingest with cam0 stubs: exit 0, one of 18 images has non-finite `antenna_ecef`/`antenna_enu`, `flags = []`, and `images.json` contains bare `NaN` tokens.
- `georoute ingest ... --curtain-travel-ms nan` exits 0, every `t_mid_gps_s` is NaN, and `images.json` contains bare `NaN` tokens.

**Why it matters.** A corrupt CSV row, a truncated session, a logger bug, or an out-of-contract receiver value produces a wrong prior, a NaN position, an invalid `images.json` (Python writes `NaN`, which is not valid JSON and which a JavaScript `JSON.parse`, including the app's, rejects), or a time-only fix treated as 3D. This directly contradicts the project's "fail loudly" rule and would propagate into COLMAP priors in slice 6. The receiver itself does not emit `lat = 100` or `nan`, but the loader's job is to refuse what it cannot trust, and every check is a few lines.

**Fix.** In `_Row.number`, require `math.isfinite`; in `_read_pvt`, require `-90 <= lat <= 90`, `-180 <= lon <= 180`, finite height in a sane range (for example -500 to 10,000 m), finite velocities and non-negative accuracies, `carrSoln in {0, 1, 2}`, `fixType in {0, 1, 2, 3, 4, 5}` with `fixType == 5` treated as no-3D-fix, and at least two PVT epochs; in `photos_from_exiftool`, require `math.isfinite(exposure)` and `0 < exposure <= 10 s`; in the CLI, require a finite curtain travel in 0-50 ms; validate `t_mid` is finite before writing. Write every JSON file with `allow_nan=False` so any remaining slip is a refusal, not an invalid package. Add one test per case. This is the smallest high-value fix in the review.

### B2. `match` can accept a weakly verified photo-to-pulse shift

**Where.** `src/georoute/photos.py:200-226` (`_check_alignment` refuses only when `compared == 0`; one valid interval is enough); `src/georoute/photos.py:1-6` (the documented residual risk is only first-missing plus last-extra with repeating intervals); `src/georoute/cli.py:73-88` (`_match` loads the session and calls `match_photos` but never calls `session_report` or checks the logger counters).

**Reproduction.** Build a steady session from the fixture: every command is 0.6 s apart, every cam0 delay is 95 ms, and pulse 0 has no delay. Set `tm2.csv` `timeValid = 0` for every pulse except 10 and 11. Build 19 photos whose taken times follow the pulses but are shifted by one pulse: `t0 + (pulse.n + 1) * 0.6 + 95 ms`. Call:

```python
match_photos(session, {0: photos})
```

It returns 19 matches, pairing `cam0/IMG_0407.JPG` with pulse 1 and every later photo one pulse late. A camera with one photo and one pulse is also matched with no interval verification: `match_photos(s1, {0: [photo]})` returns one match and pairs the photo with that pulse, even when the photo's EXIF taken time is 3600 s away from it. A simpler reproduction: set `counts.pulses_lost = 2` in `session.json`; `session-report` exits 1, `ingest` exits 1, and `match` exits 0 with a clean match. The documented residual risk also reproduces directly: a 20-photo steady 0.6 s session, delete the first photo and append an extra last 0.6 s later; `match_photos` accepts 19 photos and pairs `IMG_0408` with pulse 1 although it is truly pulse 2. The interval fingerprint cannot see a uniform one-pulse shift when every pulse interval is the same. The `ingest` path is protected because `session_report` flags invalid TM2 or lost pulses and refuses; the `match` command is not. The `match` CLI is the planned user-facing check ("reports 432/432" in the plan), so a weak session can report a full match that is silently wrong.

**Why it matters.** The documented residual risk is not the only silent-mismatch path. A session with several invalid TM2 rows (correction outage, logger stall, checksum loss) can leave only one comparable interval; a uniform one-pulse shift then passes. A camera that died after one exposure is plausible in field test 2, and its single photo cannot be verified. A rational operator or QA user will trust the `match` output.

**Fix.** Make `_check_alignment` return the number of intervals compared and refuse, or loudly warn, when fewer than half of the intervals could be checked (or when any photographed pulse has an invalid TM2), and refuse or explicitly report a camera with fewer than two comparable intervals. Refuse when all pulse intervals are within the tolerance of the median and no absolute anchor exists (or require an absolute anchor at the ends), so a uniform one-pulse shift cannot pass. Run `session_report` in `_match` and refuse on its errors, or at least print its errors. Add the weak-TM2 shifted-photo, single-photo and steady edge-shift tests to `tests/test_photos.py`. Also add the documented first-missing/extra-last end-shift test (delete `photos[0]`, append a last photo with the next file number and a continued clock) and a 20 ms/40 ms tolerance-boundary test: the current suite stays green if `INTERVAL_TOLERANCE_S` is set to `0.5`, `1e9` or `0.02`, so the interval defence is not actually pinned. Worse, the existing invalid-TM2 bridging test passes for the wrong reason: deleting a photo creates a file-number gap that `order_photos` catches before `_check_alignment`, and deleting the bridge (`last = None` after the `except`) leaves all 193 tests green. Rebuild that test with contiguous file numbers so only the interval across the invalid pulse changes, and expect "photos stop following the pulses". The `ingest` path already has the strong gate; the `match` tool should not be weaker.

### B3. There is no COLMAP rig and frame handoff, and a naive rig will mis-group frames (blocker for slice 6)

**Where.** `src/georoute/rig.py:49-89,190-237`; `rigs/handlebar-4cam.example.toml`. `grep -rn "cam_from_rig\|camera_params\|camera_model_name" src/` returns nothing; `RIG_SCHEMA` has no intrinsics; `refine_priors` is not called by `ingest` and returns per-image rotations. COLMAP defines a rig frame as sensors exposed at the same time, grouped by identical filenames across camera folders, with one `rig_from_world` and fixed `cam_from_rig` per frame. `colmap rig_configurator -h` requires intrinsics and extrinsics when no input reconstruction is given.

**Reproduction and evidence.** `tests/test_photos.py:80-94` already produces per-camera counts 19/18/19/18 for the same session, so the same filename does not mean the same pulse. The four Canons have independent file numbers, and a missing photo on one camera shifts that camera's sequence. The cameras expose up to about 36 ms apart (handoff section 6), about 0.29 m at 8 m/s. Forcing filenames to match so that COLMAP can form rigid frames would assign one pose to four images taken up to 36 ms apart, contradicting the per-image priors the code computes at each `t_mid`. The handoff says rigid rig constraints are an experiment, not a default; the current code has neither the per-image camera-centre schema nor the rig-config emitter that slice 6 needs.

**Why it matters.** Slice 6 cannot build a valid COLMAP 4.2.1 rig config from this deliverable. The first real 4-camera run will either fail in `rig_configurator` or silently mis-group frames across cameras, corrupting the reconstruction the whole product depends on. A missing photo on one camera makes the mis-grouping worse.

**Fix.** Add `Rig.cam_from_rig(index, ref_index)` expressed in the reference camera's frame (rotation `rig_from_camera(i).T`, translation `-R^T t_cam_in_rig`), emit a `rig_config.json` plus a frames manifest keyed by pulse, and default to independent cameras with per-image `pycolmap.PosePrior` (no rigid frames) until a real 4-camera test proves the rig constraint helps. Both are legitimate COLMAP configurations; the per-image prior path is the safer default given the 36 ms skew. Add a test that `cam_from_rig` composed with a world pose reproduces `camera_centre`, and a missing-photo case that shows the frame manifest still groups by pulse, not by filename. Decide the intrinsics policy (checkerboard or COLMAP self-calibration) before slice 6. This is the slice-6 blocking form of the rig-config gap; the exact `rig_config.json` fields should be agreed with the slice-6 plan.

### B4. `images.json` has no session/image identity or route/build provenance, so multi-session work will collide (blocker for slices 6-7)

**Where.** `src/georoute/ingest.py:62-75,102-120`; `IMAGES_SCHEMA = "georoute.images/1.0.0"`. The top-level `session` is only the folder name; each image record has `path` relative to the images root, with no session or image id; there is no route/build id, camera pose or attitude, segment id, route-global `s`, geoid or orthometric height, or quality schema with units and covariance. `georoute` is `0.1.0`, not a pipeline commit.

**Reproduction.** Ingest two copies of the same session to `/tmp/ar2multi/out_sessA` and `out_sessB`; the `path` sets are identical (`cam0/IMG_0408.JPG`, ...), no image record carries a session key, and the only globally stable fields are `t_mid_gps_s` and absolute ECEF. A route-level consumer combining the two files cannot tell the two images apart by path, and a re-ingested or duplicated session can collide. The design v0.2 says multi-session is the normal case for 20-80 km routes; at 11-17 hours per pass, this is not an edge case.

**Why it matters.** The `images.json` schema is the input to slices 6-8; changing it after field test 2 writes about 5,000 records per session and several sessions per route is expensive. The design's provenance, per-segment QA, route-global `s`, camera pose/attitude and orthometric-height requirements cannot be expressed in the current record. The per-image `path` is also the only identity, so two cameras' files with the same name in different sessions look identical.

**Fix.** Add a content-derived `session_id` (for example a hash of the session start time plus receiver identity), `image_id = session_id/path`, `route_id`/`build_id` slots, a structured datum record, nullable `camera_pose`/`attitude`, an `s` slot, a quality block with sigma and covariance, input hashes, tool versions, a JSON Schema and a compatibility policy. Record dropped warm-up paths, not only a count. These are cheap now and painful later. The exact fields should be agreed with the slice-6/7 plan rather than invented in isolation, but the identity fields are unambiguous.

### B5. A base-position jump, false fix or corrupted PVT step passes as fixed (blocker)

**Where.** `src/georoute/report.py:84-118`, `src/georoute/ingest.py:143-151`, `src/georoute/trajectory.py:34-70`. There is no epoch-to-epoch kinematic consistency check; `rtcm.bin` is never opened, and `ntrip_connects`/`ntrip_policy_reconnects` are absent from `WARNING_COUNTERS`. The quality flags only look at `carrSoln`, `fixType` and the bracket gap; a position step with consistent `carrSoln = 2` and a small `hAcc` passes.

**Reproduction.** Copy `tests/fixtures/ft1_trim`; add 1.5 m to `lon` (about +2.15e-5 degrees at 51.16N) from `pvt.csv` data row 44 onward, leaving the velocities unchanged. Write cam0 stubs and run `uv run georoute ingest`. The result is 18 images, `flagged {'not-fixed': 0, 'no-3d-fix': 0, 'gap': 0}`, no errors, `h_acc_m 0.014`, and shifted positions in `images.json`. `track.png` may show the step, but nothing checks it. Field test 1's `rtcm.bin` contains 9 x 1005 (station 3527, ECEF `4006364.0375, -294184.8692, 4937580.8104`, 12.51 km from the first fixed epoch) plus full MSM, but `georoute` never opens it; a session copy with `counts.ntrip_connects = 3` produces no warning.

**Why it matters.** STATUS records that `LOCAL` may be a virtual base whose 1005 position changes on reconnect. A changing base, a false fix or a corrupted NAV-PVT row silently displaces every later prior and every multi-session join while every quality field says fixed. The design's 5 cm registration gate cannot catch a base station moving by metres, and the PPK insurance cannot be audited without the base identity.

**Fix.** Compare each epoch's displacement with the integral of the NAV-PVT velocity over the epoch interval, with a residual threshold derived from `hAcc` and a plausible acceleration bound; flag or segment the build on a step. Add a `base-jump` image flag. Parse RTCM 1005/1006, record the station id, ECEF position, distance and connection index, and refuse or split on a position change; warn on `ntrip_connects` and `ntrip_policy_reconnects`; put the base provenance in `images.json`; add the position-versus-integrated-Doppler consistency check. Test with the 1.5 m step and with a velocity-consistent step (which is not a base jump and should not be flagged).

### B6. A rig camera that is missing from the capture is silently skipped (blocker for field test 2)

**Where.** `src/georoute/photos.py:169-173`, `src/georoute/ingest.py:122-129`. `match_photos` loops cameras; if a camera is not in `photos` and has no pulses, it `continue`s; `ingest` reports the remaining cameras and exits 0. The rig's camera set is never compared with the matched set.

**Reproduction.** With `rigs/handlebar-4cam.example.toml` marked `measured = true`, run `uv run georoute ingest tests/fixtures/ft1_trim <images-root-with-cam0-only> --out /tmp/out --rig /tmp/rig_measured.toml`; it prints "18 images timed and positioned", exits 0, and writes `per_camera {"cam0": 18}` with no missing-camera warning. The fixture stands in for field test 2 with one dead camera. The existing test `tests/test_ingest.py:127-154` enshrines this silent success with a 4-camera rig and one camera's photos.

**Why it matters.** A dead camera, a missing card, or a folder not copied produces a 3-camera route with no gate; a camera that dies mid-session produces a partial route with no gate. The design's coverage and QA gates are future slices, but the ingest should not call a partial rig complete. PT-1's zero-sync-edge case and the mid-session loss in S13 are the same class.

**Fix.** After matching, compare the rig's camera set with the cameras that produced both photos and pulses; refuse unless every rig camera is present, with an explicit `--allow-partial-rig` opt-out; name the missing cameras. Apply the same check in `match` and `ingest`. Update the test that enshrines the silent success.

## 3. Should-fix

### S1. Metadata types and `nav_period_ms` are not validated

**Where.** `src/georoute/session.py:111-113` (`nav_period_s` divides by `nav_period_ms` with no type or positivity check); `src/georoute/session.py:138-149` (`_read_meta` checks only that `datum` and `nav_period_ms` keys exist); `src/georoute/report.py:46` (`counts = s.meta.get("counts", {})` then `counts.get`); `src/georoute/session.py:107-109` (`datum` returns whatever is in the JSON).

**Reproduction.** Set `nav_period_ms = 0`: `session-report` raises `OverflowError: cannot convert float infinity to integer`. Set `nav_period_ms = "200"`: `TypeError: unsupported operand type(s) for /: 'str' and 'int'`. Set `nav_period_ms = 1000` (a valid number but the wrong rate) and delete six PVT rows so there is a 1.4 s hole: `ingest` exits 0, `max bracket_gap_s` is 1.4, every image has zero gap flags and the report says `missed_epochs 0`. Set `counts = "oops"`: `AttributeError: 'str' object has no attribute 'get'`. Set `datum = 5` or `""`: accepted; `session-report` exits 0; the future `images.json` would carry the same.

**Fix.** Validate `nav_period_ms` as a finite number in a sane range (for example 10-1000 ms), `counts` as a dict, `datum` as a non-empty string, and the top-level `session.json` object as a dict. Infer the median PVT epoch spacing from the data and refuse or warn on a mismatch with `nav_period_ms`; use the measured period for the gap flags and `missed_epochs`. Raise `SessionError` naming the key; add tests. This turns tracebacks into the fail-loudly errors the project promises, and stops a wrong receiver config from defeating gap detection.

### S2. Negative or absurd per-camera delays and zero/negative exposure are accepted

**Where.** `src/georoute/session.py:214` (`optional_int` for `camN_delay_us`, no range); `src/georoute/photos.py:112` (`float(ExposureTime)`, no range).

**Reproduction.** Set `cam0_delay_us = -5000` on a photographed pulse: `session-report` exits 0; `t_mid` moves 5 ms earlier. Set `ExposureTime = 0` or `-0.001`: `photos_from_exiftool` accepts and `t_mid` is wrong. Run `georoute ingest ... --curtain-travel-ms 4000`: exit 0 with `curtain_travel_s: 4.0`, shifting every `t_mid` by 2 s (16 m at 8 m/s). `--curtain-travel-ms nan` exits 0 with NaN `t_mid` and NaN antenna positions; `-50` shifts every `t_mid` by +25 ms; the public `ingest` API accepts the value without validation. argparse usage errors already exit 2, while an invalid value should be an `IngestError` (exit 1) with a refusal in `report.json`; document or separate the two. The real field data has delays 87.2-123.2 ms and 1/1000 s exposures, so these values are out of contract.

**Fix.** Require `0 <= delay <= 500 ms` (or a documented measured range) and `0 < exposure <= 10 s`; apply the same 0-50 ms finite/range validation to the `--curtain-travel-ms` override that the rig loader already applies, in `_ingest` and defensively in `exposure_mid`/`ingest`; refuse with the file, line and column. Add tests for -1, zero, nan and inf: nonzero exit, no `images.json`, and a refusal in `report.json`.

### S3. An expected camera serial is silently unverified when a photo has no serial

**Where.** `src/georoute/photos.py:186-197` (`_check_body`); `found` ignores `None`; the expected serial is only checked when a serial is present.

**Reproduction.** Build the 19 fixture photos with `serial=None` and call `match_photos(session, {0: photos}, serials={0: "9999"})`; it returns 19 matches. A rig marked `measured = true` may still have empty serials (the example has four empty serials). Swapping cards between cam0 and cam1 with no expected serials leaves each folder internally consistent, so `match` and `ingest` accept it and the camera role, lever arm and intrinsics assignment is swapped. The Canon MakerNote `FileNumber`/`SerialNumber` path has never run on a real JPEG (FT1 images deleted), so the tag names are unverified.

**Fix.** If a camera has an expected serial and any photo lacks one, refuse naming the photo; if a camera has no serials at all and an expected serial is configured, refuse. Refuse `measured = true` when any camera serial is empty, and warn when a rig has serials but no photo carries one. Request `BodySerialNumber` as well as `SerialNumber` and verify both tag names on the first real capture. The cross-camera shared-serial check and the rig-serial wiring are also untested: removing the `bodies.setdefault(serial...)` guard or making `photo_sources` return `None` leaves all 193 tests green. Add a two-camera shared-serial test and an ingest test where the rig expects a serial the stub does not carry. Provide an explicit "no serials expected" mode for stub tests.

### S4. File-number gap check uses the filename number even when sorting by Canon FileNumber

**Where.** `src/georoute/photos.py:130-148`; the sort key is `FileNumber` when present, but `unwrapped` is always computed from the filename number and the gap check always uses `unwrapped`.

**Reproduction.** Three photos with Canon FileNumbers `1000001, 1000002, 1000004` and names `IMG_0001, IMG_0002, IMG_0003` are accepted by `order_photos` (the FileNumber gap is missed). Three photos with contiguous FileNumbers `1000001-1000003` and names `IMG_0001, IMG_0002, IMG_0004` raise "file numbers jump from IMG_0002.JPG to IMG_0004.JPG" (a false refusal caused by the filename).

**Fix.** Check the sequence of the key actually used (FileNumber when every photo has one); cross-check the other sequence with a warning or a separate check; name the field in the message. Add both tests. The plan says both sequences should be checked; the code checks only one.

### S5. `longest_non_fixed_run.metres` undercounts when the run contains a missed epoch

**Where.** `src/georoute/report.py:108-113`; `metres = speed[first:first+epochs].sum() * period`; `seconds` uses the actual time, but the metres use the nominal period and ignore a dropped epoch inside the run.

**Reproduction.** Make a five-epoch non-fixed run in `pvt.csv` and delete one row inside it. The report says 4 epochs, 1.0 s, 0.932 m; the actual first-to-last chord is 0.969 m and the along-path distance is larger (the missing epoch's speed times 0.2 s is about 0.24 m). A six-epoch run with a dropped row reports 1.238 m where interval integration gives about 1.49 m (ratio 0.83); a 60 m run can report 50 m and pass the design v0.2 QA gate "longest non-fixed run > 50 m".

**Fix.** Integrate speed over the run's actual `np.diff(t)` plus one nominal period for the last sample, or use the endpoint displacement, and add a test with a gap inside the run. The slice 2 review N4 raised this; it is still present.

### S6. `session_report` warns rather than errors on out-of-order pulse numbers

**Where.** `src/georoute/report.py:24-26`; `pulse_numbers != list(range(len(pulse_numbers)))` catches holes and permutations, but the message is "not a contiguous 0..N-1 sequence". A permutation of 0..N-1 is reported as a warning, and `match_photos` uses the file order.

**Reproduction.** Swap the `pulse_n` values of rows 0 and 1. `session-report` returns no errors and the warning above; the pulse order is `[1, 0, 2, 3, ...]`. A matching run then fails with a confusing interval message rather than "pulses are out of order".

**Fix.** Require `pulse_n` to be strictly increasing by 1 in file order; raise a `SessionError` or a report error naming the first out-of-order row. Add the test.

### S7. CLI tracebacks on OSError and on the metadata cases above

**Where.** `src/georoute/cli.py:61-70` (`_session_report` catches only `SessionError`; `--json` write is uncovered); `73-88` (`_match` catches `MatchError`/`RigError` but not `OSError` from `read_photos`/exiftool); `91-108` (`_ingest` catches the named errors but not `OSError` from `out.mkdir`, `_write_json` or matplotlib, nor `ValueError`/`OverflowError` from malformed metadata).

**Reproduction.** `session.json` containing `[]` gives `AttributeError: 'list' object has no attribute 'get'` from `_read_meta`. `nav_period_ms = 0` gives the OverflowError above. `--out` under a regular file, a read-only output directory, or `--json` with a missing parent gives an `OSError` traceback; `ingest` calls `out.mkdir` before its `try`, so the failure has no `report.json`. `camera = 1` in `rig.toml` gives `TypeError: 'int' object is not iterable` from `load_rig`.

**Fix.** Validate metadata (S1); require `camera` to be a list of dict entries in `load_rig`; move `out.mkdir`/stale-file deletion inside the `try`; catch `OSError` and the remaining metadata exceptions in the CLI and return the documented exit code with a one-line message; wrap the JSON write. Add tests for the list metadata, zero period, missing `--json` parent, unwritable `--out` and `camera = 1`.

### S8. The "no fix = no prior" decision is not encoded, and `images.json` carries no usable per-image uncertainty

**Where.** `src/georoute/ingest.py:102-121` writes `antenna_ecef` and `antenna_enu` for every capture photo, including `carr_soln = 0`; the `flags` list is the only signal. `research/STATUS.md:73` records the decision "float = loose prior, no fix = no prior". Separately, `h_acc_m`/`v_acc_m` are per-epoch receiver accuracies, `bracket_gap_s` is a gap signal, and `Tm2.accEst_ns` is parsed and never used.

**Reproduction.** The supplied field-test output has one `carr_soln = 0` image with a full `antenna_enu` and `flags = ["not-fixed"]`; a consumer that uses the position without reading the flags gets a prior the design says should not exist. The same applies to the 84 float images: the design says a loose prior, but there is no `prior_sigma_m`, `prior_weight` or `has_prior` field. In the real FT1 output, `cam0/IMG_0491.JPG` is `carr_soln 1` (float) with `h_acc_m 0.014` and `flags = ["not-fixed"]`; a slice-6 consumer using `h_acc_m` as the prior sigma would give a float photo a 14 mm prior. An analytic 30 m radius at 8 m/s with a 10 s PVT hole gives 3.77 m Hermite error (39.5 m at 20 s) while `h_acc_m` stays 0.014 and the only signal is `flags: ["gap"]`; removing FT1's 103-epoch non-fixed run gives 1.06 m max versus `h_acc_m` 0.04. hAcc/vAcc interpolate across the bracket gap, so a 4.8 s gap is flagged but its prior stays 0.014 m; `corrAge` records only the left bracket, and 151 of 431 FT1 brackets differ on the right. Slice 6 will treat `h_acc_m` as the prior sigma.

**Fix.** Add a per-image `prior` object (for example `has_prior`, `sigma_m`, `weight`, `kind`, `prior_mode` fixed/loose/none), or at least `has_prior` and `prior_sigma_m`; set `has_prior = false` for `carr_soln = 0`, use hAcc/vAcc for the others, and document the schema. Emit `time_sigma_s` (TM2 `accEst_ns`, IRQ budget, curtain/exposure model) and `position_sigma_m` (receiver accuracy combined with the gap/interpolation bound and timing times speed). Inflate accuracy with the gap length or floor the prior at the interpolation error; record the worse `corrAge` of the bracket; record `tm2_acc_ns` per image. Add tests. This is a slice-6 contract item, not just a track-1 nicety.

### S9. Datum is a free-text label with no realisation, epoch or ellipsoid binding

**Where.** `src/georoute/geodesy.py:74-107` (`LocalFrame` carries `datum: str`, always uses the WGS84/GRS80 ellipsoid, and `to()` refuses unless the labels match); `src/georoute/session.py:107-109`; `src/georoute/ingest.py:68-74`.

**Reproduction.** `LocalFrame.at(..., datum="ETRS89").to(LocalFrame.at(..., datum="WGS84"), ...)` raises, although the difference is under 1 m in Europe; `settings.datum = ""` or `"not a datum"` is accepted (only a string type is checked), `datum = 5` is accepted, and there is no `ETRF2000`/`ETRF2014` realisation or epoch, no datum transform, and no record of the correction source. `frame.h_m` is ellipsoidal PVT height but unlabelled, and the design's orthometric/OSGM15 height is absent. `LocalFrame.to` compares strings only, and a multi-session join path that uses ECEF directly bypasses even that check; two sessions both labelled "ETRS89" from different realisations or frames mix silently. A WGS84/ITRF label on ETRS89 data is decimetres to about a metre in the UK by 2026 because of plate motion since 1989.0, far above the 5 cm gate. The design v0.2 says the input datum is recorded and never assumed WGS84; multi-session joins need the shared earth anchor.

**Fix.** Make the datum a structured record (name, ellipsoid, realisation, epoch, EPSG) or at least validate against a known registry and record the realisation/epoch, a `height_type` and geoid field, the NTRIP mountpoint, base id and frame identity in `images.json` and the future manifest; document the WGS84/GRS80 approximation; define the WGS84 transform used for export; require equal datum records or an explicit Helmert for equal-label/different-frame joins; add tests. Per-segment frames are cheap because every image stores ECEF and can be re-framed; the free-text datum and missing frame identity are the durable weaknesses. This is a slice-7/8 item but the field is cheap to add now.

### S10. The straight-ride refusal is safe but the 1 m lateral-spread threshold is a heuristic

**Where.** `src/georoute/rig.py:201-237`; `lateral = spread[1]` is the second singular value of the centred antenna positions divided by `sqrt(N)`; `min_lateral_spread_m = 1.0`; the stop rule compares successive priors, not the error to truth.

**Reproduction.** The test refuses a perfectly straight flat track (spread 0). A 1.2 m sinusoidal weave has `spread[1]` about 0.85 m and is refused; a 1.5 m weave has about 1.06 m and proceeds. A straight but hilly track passes: 60 frames on a straight north line with `z = 1.25 + 3*sin(k/8)` gives SVD/sqrt(n) `[13.88, 1.88, 0.00]` and `refine_priors` returns after 2 iterations, although the lateral spread is exactly 0. The current test is flat only. `refine_priors` also admits every image regardless of fix quality: a 200-frame straight ride with 2 cm antenna noise and one 30 m no-fix outlier gives `spread[1] = 1.06 m`, so the 1.0 m guard passes and the refinement leaves camera-prior errors of 30.15 m worst and 0.26 m p95. A 3.3 m vertical crest gives `spread 1.02` and passes with p95 5.5 cm; the guard is measuring vertical curvature, not lateral spread. The threshold is not derived from the conditioning of the rotation estimate, and a marginal track can still converge to a wrong roll while a usable track is refused. The whole-build refusal is also coarse: a route with one bend has enough lateral spread, but a long straight segment within it still has an unobservable roll for its own frames.

**Fix.** Pass `carr_soln`/`hAcc` per image and use fixed epochs only, or weight by `1/hAcc^2`; make the spread robust (for example MAD from a fitted line). Use a conditioning or uncertainty criterion (ratio of singular values, the smallest singular value, the horizontal normal to the track, expected roll error from the aligner covariance, heading range) instead of a fixed 1 m; use `pycolmap.PosePrior` gravity and position covariance, the nominal rig attitude or the road plane to constrain roll on straight sections rather than refusing the whole build. Return `(s, R, t, residual)` from the aligner and reject a scale outside 0.95-1.05 so a similarity fit cannot silently warp the rig. Record the criterion and add straight-hilly, no-fix-outlier and marginal-track tests. This is a slice-6 decision; the refusal itself is better than a silent wrong answer.

### S11. The PPK evidence is not reproducible from the repo, and the raw-log insurance is unenforced

**Where.** `research/STATUS.md:87-95` records the verdict, but the RTKLIB demo5 build, `ppk.conf`/`combined.conf`, base/rover observations and `.pos` outputs are only in the scratchpad. `base.nav` is already missing from the scratchpad even though `combined.pos` lists it; no exact `convbin`/`rnx2rtkp` command (including `-ti 1`) or Doppler/jump analysis script is in the repo or STATUS. `git ls-files research | grep -i ppk` returns nothing.

**Reproduction.** A future engineer cannot rerun the exact consistency checks, base position, config or comparison from the repository. I independently verified the numbers from the scratchpad (1302/1415 fixed, last-33-s +0.305 m north/+0.330 m up, 53 versus 2 jumps over 15 cm, +3.75 Doppler/+3.88 live/+4.50 combined over epochs 1100-1300), but the evidence is not in the record. The STATUS wording also needs correction: the 1 Hz subset comes from decimating the rover to 1 Hz, not from the base epochs; the base MSM is 2 Hz for GPS/BDS and 1 Hz for GLO/GAL; the subset is 3.5-6.0%, not 4-6%; "a few cm" is the median, with p95 |dU| 26.7 cm and max 74.1 cm; a 90-120 s cluster of the extra fixes agrees with Doppler within 0.1 m over 30 s; the Doppler is vertical-only and the final-33-s horizontal is unadjudicated. Separately, the insurance is unenforced: nothing in `session_report` checks that `receiver.ubx` and `rtcm.bin` exist, or that their sizes match `counts.ubx_bytes` and `counts.rtcm_bytes`, so a later session can pass ingest with the raw logs absent. The record has no cycling-speed revisit trigger: FT1's 3.4-20.6 s float runs become 27-165 m at 8 m/s, above the design's 50 m gate. A future PPK solution's base 1005 coordinates must also be tied to ETRS89 before mixing with live epochs.

**Fix.** Add raw-log presence and size checks to the session report and provenance, a distance-based revisit trigger, and a datum check for any future PPK output. Commit a `research/ppk-ft1/` bundle with the RTKLIB config, exact `convbin`/`rnx2rtkp` commands (including `-ti 1`), base coordinates, `.pos` summaries and the Doppler/jump analysis code, or a short `research/ppk-check.md` that carries them; correct the STATUS wording and cite the caveats above. No rerun is needed now. The method and conclusion are sound; the record and the insurance checks are the gap.

### S12. An unmeasured rig is only a warning, and ingest still writes priors from placeholder geometry

**Where.** `src/georoute/cli.py:102-103`; `src/georoute/ingest.py:56,67`; `tests/test_ingest.py:127-149`. The example rig's antenna-to-camera arms are 0.255-0.361 m, 5-7 times the design's 5 cm registration gate. `ingest` prints "WARNING the rig config is marked unmeasured" and still writes `images.json` and exits 0; the test asserts that it does.

**Reproduction.** Run `uv run georoute ingest <session> <images> --out /tmp/out --rig rigs/handlebar-4cam.example.toml`; exit 0, `images.json` has `rig.measured = false` and the camera priors are derived from the placeholder geometry. A rig marked `measured = true` can also keep `curtain_travel_ms = 0.0` and `phase_centre_offset_m = [0, 0, 0]` with no warning; the CLI only warns when `measured = false`, leaving a Tc/2 bias of 1.5-2 ms (12-16 mm at 8 m/s) and an unknown vertical phase-centre bias.

**Why it matters.** The rig is not built; the example config is a guess. A COLMAP consumer that reads `images.json` will trust the priors, and the 5 cm residual gate can partly absorb the error and let the build pass. The placeholder arms are already 5-7 times the gate.

**Fix.** Refuse an unmeasured rig by default; require an explicit `--allow-unmeasured-rig` override that marks `priors_usable: false` (or omits the camera centres) in `images.json`. When `measured = true`, require a measured or cited curtain travel and phase-centre offset, and record the source and uncertainty in `images.json` and the priors. Keep the warning for the override. Add a test that the default refuses and the override records the flag.

### S13. A camera that dies mid-session, or a build with zero capture images, is accepted

**Where.** `src/georoute/photos.py:165-205`, `src/georoute/report.py:57-82`, `src/georoute/cli.py:108-116`. `match_photos` checks each camera against the pulses that camera has sync edges for; it never compares the camera's last contributing pulse with the session's last capture pulse.

**Reproduction.** In `tests/fixtures/ft1_trim/pulses.csv`, give all four cameras delays, then blank `cam2_delay_us` from pulse 12. Write stubs for the remaining delays. `georoute match` exits 0 and prints "cam2 12 photos matched"; `ingest` exits 0 with cam2 10 capture images and no warning. A session with every pulse marked `warmup` exits 0 with an empty `images.json`.

**Why it matters.** A camera battery dying, a card filling, or a cable working loose is exactly the field-test failure the 4-camera MVP must catch. The match is internally consistent, so every existing check passes; the route silently loses one side. The design's coverage gate is a future slice, but the ingest should not call a partial build complete.

**Fix.** Per camera, compare the first and last contributing pulses with the session's first and last capture pulses; warn or fail "cam2 stopped N pulses before the end". Warn when a rig-configured camera has zero images, and refuse a build with zero capture images. Add the mid-session-loss and zero-capture tests.

### S14. An all-float session is refused, contrary to the keep-non-fixed decision

**Where.** `src/georoute/ingest.py:23-30,68-74`; `src/georoute/session.py:108-109`; `tests/test_ingest.py:99-104` locks the refusal in.

**Reproduction.** Set every `carrSoln` in `pvt.csv` to 1 (or 0), or use a session with no fixed epoch. `session_frame` raises `IngestError("the session has no RTK-fixed epoch to anchor its frame")`, so `ingest` refuses the whole session.

**Why it matters.** The decision recorded in `STATUS.md:73` is to keep every capture photo; float photos carry a loose prior and no-fix photos carry no prior. The design v0.2 says where RTK is float or missing, the reconstruction carries the path. A correction outage that lasts a whole session should produce flagged, loosely constrained images, not a refusal. The code anchors the build frame at the first RTK-fixed epoch and therefore cannot start without one.

**Fix.** Anchor the frame at the first 3D-fix epoch (`fix_type >= 3`) or the first epoch with a position, record `rtk_fixed: false`, and warn loudly. Keep the photos with their flags; a session with no position at all (time-only) can still refuse. Add an all-float test and an all-none test.

### S15. The session ENU frame is not safe beyond about 1 km

**Where.** `src/georoute/ingest.py:23-30` (`session_frame` anchors at the first fixed epoch), `src/georoute/ingest.py:68-74,100` (`frame` and `antenna_enu`), `IMAGES_SCHEMA = "georoute.images/1.0.0"` with a static `georoute 0.1.0` and no commit, input hashes or generation time.

**Reproduction.** At the same ellipsoidal height, a point 5 km east in the FT1 frame has `u = -1.96 m`; 20 km gives -31.29 m, 40 km -125.17 m and 80 km -500.62 m. At 1 km the error is 7.8 cm, against the design's 5 cm QA target. ECEF is emitted but not marked authoritative.

**Why it matters.** The session ENU frame is a tangent plane at the origin. It is fine for a 1 km MVP and for plotting, but an 80 km route in one session frame has metre-scale vertical bias, exactly the failure the design v0.2's per-segment frames exist to prevent. A consumer that uses `antenna_enu` as the build frame beyond a kilometre will get biased grade and elevation.

**Fix.** Mark ECEF as authoritative in the schema, derive ENU per segment (about 1 km) in slice 7, and add a route-length check that refuses or re-frames an ENU use beyond the segment radius. Add a `frame_id` and the anchor's hAcc; add commit, input hashes and `generated_at` to `images.json`; test a 20 km synthetic route. This overlaps B4 and gap 5; the coordinate issue is the distinct part.

### S16. The Canon multi-folder layout for a real 20-80 km capture is not supported

**Where.** `src/georoute/photos.py:78-83` (`_read_folder` is flat and rejects non-JPG entries); `rigs/handlebar-4cam.example.toml:29-53` (all four serials empty).

**Reproduction.** A Canon 100D rolls its folder at 9999 images (`100CANON`, `101CANON`, ...). 20 km at 0.8 m is 25,000 photos per camera, so a real capture spans at least three card folders per camera. A nested `100CANON` folder inside the expected `cam0` folder raises "holds files that aren't .jpg photos"; a flat copy of two folders collides on names such as `IMG_0001.JPG`. The `FileNumber` rollover test uses fabricated integers, not a real card layout. All four sample-rig serials are empty, so the body-swap check is inert.

**Why it matters.** Field test 2 is about 1 km (1,250 photos per camera, one folder), so this does not block the MVP; a 20 km route does. The operator will hit it on the first multi-folder card, and the workaround (flatten and rename) is exactly where silent ordering mistakes enter.

**Fix.** Recurse into camera subfolders and key photos by folder plus Canon `FileNumber`; test two card folders with a rollover. Require serials for a `measured = true` rig, and warn when photos carry serials and the config has none. This is a field-test-2/scaling item, not an MVP blocker.

### S17. Benign exiftool warnings and macOS `._` files refuse the whole camera folder

**Where.** `src/georoute/photos.py:78-97`. The `strays` check ignores dotfiles, but exiftool still reads them; `_read_folder` treats any stderr line starting "Warning" as fatal.

**Reproduction.** Add a text file named `._IMG_0002.JPG` to a stub folder (a macOS AppleDouble companion, created by copying a card in Finder): `read_photos` raises `MatchError`. A real Canon file emits benign MakerNote warnings such as "Warning: [minor] Bad MakerNotes"; the current code refuses the whole folder. The slice 3 review measured this class of warning.

**Why it matters.** The rig runs on a Mac, and a Finder copy of a camera card is the normal transfer path. Benign Canon MakerNote warnings are normal. The current policy over-corrects the slice 3 S3 fix and can refuse valid first real 4-camera data before it is read.

**Fix.** Drop exiftool records whose basename starts with "." and ignore `._` files in the strays check. Treat "Error" as fatal and "Warning" as loud but non-fatal, while still enforcing every needed tag and the expected serial (so a warning that removes `FileNumber` or `SerialNumber` is caught by S3/S4). Add a `._` file test and a benign-warning test.

### S18. Photos are not bound to the declared session or rig

**Where.** `src/georoute/photos.py:186-197` (`_check_body`), `src/georoute/photos.py:200-227` (`_check_alignment`), `src/georoute/ingest.py:137-139` (`photo_sources`). The serial check is optional, and the supplied rig has `serial = ""` for all four cameras, so `photo_sources` returns an empty serials dict and the expected-serial branch is dead. The interval check compares only intervals.

**Reproduction.** `_check_body(0, [Photo(serial="111")], None, {})` and `_check_body(1, [Photo(serial="222")], None, {})` both pass, so two different bodies in the two folders are accepted; if the cards are swapped, the wrong `position_in_rig_m` and `yaw_pitch_roll_deg` are applied. A same-body folder from another session with the same count and cadence can pass `_check_alignment` even though the camera clock was 54 minutes off. A card copied into the wrong folder with a unique or new serial passes when the rig has no expected serials.

**Why it matters.** The pipeline cannot establish that the photos it ingests are the rig and session it was given. The rig's empty serials make the only swap defence inert, and the interval-only check has no absolute session anchor.

**Fix.** Require a non-empty, unique serial for every camera when `measured = true`; record the observed serial per camera in `images.json`; add a `georoute-session.json` sidecar or an expected file-number range per camera folder, and check the session start/time or file-number range. Apply the check in both `match` and `ingest`. This overlaps S3; the session-binding part is distinct.

### S19. TM2 fields are not range-checked, and the falling-edge cross-check is absent

**Where.** `src/georoute/session.py:224` (`rising = wnR * WEEK_S + towMsR / 1e3 + towSubMsR / 1e9`, no range check); `src/georoute/session.py:214` (delay); `src/georoute/report.py:43-44` (the only timing check is that a photographed pulse is inside the track). The `wnF`/`towMsF` falling-edge fields are not parsed or cross-checked.

**Reproduction.** Set a photographed pulse's `towSubMsR = 1.5e9`: the rising time shifts by +1.499 s, `session-report` returns no errors, and `antenna_positions` returns a position with `flags = []`. Set `cam0_delay_us = -1000`: `t_mid` moves 0.5 ms earlier with no error. Neither is covered by the B1/S2 ranges.

**Why it matters.** A corrupted TM2 field or delay shifts every exposure time and prior while the time is still inside the track, so the session report's only timing check passes. The handoff defines the falling edge as optional, but when present it gives a cheap consistency check on the rising edge and the edge delta.

**Fix.** Range-check `wnR`, `towMsR` (0 to 604,799,999), `towSubMsR` (0 to 1e9) and the per-camera delay (0-500 ms, or the measured 30-500 ms); when the falling fields are present, require rising <= falling and a plausible edge delta; check the TM2 time is inside the track; add tests. This is distinct from the `accEst_ns` nit (N2).

## 4. Nits

1. **`umeyama` accepts two points.** `src/georoute/alignment.py:9-10`; two points are collinear, so the rotation about the line is underdetermined. `umeyama([[0,0,0],[1,0,0]], 2*...)` returns scale 2 and the identity rotation with no error. Require three non-collinear points or return a conditioning diagnostic; add a test.
2. **`Tm2.accEst_ns` is stored but never checked or recorded.** `src/georoute/session.py:67-74`, `115-125`; a TM2 mark with a large accuracy estimate is still used, and `images.json` carries no `tm2_acc_ns`. Flag/refuse above a threshold (for example 1 microsecond) and record it per image.
3. **`missed_epochs` trusts `nav_period_ms`.** `src/georoute/report.py:84-87`; if the configured period is stale or wrong, the count and the gap flags are wrong. Estimate the median PVT step from the data and compare it to the configured period; use the data step for the report or warn on a mismatch.
4. **`images.json` omits `kind` and the X-sync/command times.** `src/georoute/ingest.py:102-121`; all rows are capture, but the plan lists `kind`, and `t_x_gps_s`/`t_cmd_gps_s` would make timing debugging much cheaper. Add them or document why not.
5. **The supplied field-test output is stale.** `/private/tmp/.../scratchpad/ft1/out/images.json` lacks `delay_us` and `corr_age` and carries the unrounded `v_acc_m = 0.013000000000000001`; the current code includes both fields and rounds. Regenerate before comparing outputs.
6. **`order_photos` duplicate FileNumbers are not checked.** A duplicate is ordered by the stable input order; the name-number gap check catches most cases but not all. Check uniqueness of the sort key.
7. **`rig._camera` allows pitch and roll to plus or minus 180 degrees.** `src/georoute/rig.py:158-176` uses one `_vector(..., limit=180)` for all three angles; the slice 5 review recommended pitch [-90, 90]. A pitch of 170 would pass. Use separate bounds.
8. **`session.json` corrupt but `session.json.tmp` valid refuses.** `_read_meta` prefers `.json` and does not fall back if it is unreadable; a power-loss case could leave a corrupt `.json` and a valid `.tmp`. A low-probability recovery gap; document or fall back.
9. **`report.format_report` and the CLI do not print the valid TM2 interval count or the match margin.** The slice 3 review N3 suggested printing the max residual and number of intervals checked; cheap observability for the next field test.
10. **`LocalFrame` doc says "WGS84/GRS80 ellipsoid" but the datum string is free text.** Good for now; the slice 1 review flagged it; S9 above is the real fix.
11. **`Rig`, `Camera` and `RefinedPriors` are frozen dataclasses holding numpy arrays.** `src/georoute/rig.py:54-72,193-198`; equality raises `ValueError` (ambiguous truth value) and hashing raises `TypeError`, the same issue the slice 1 review fixed for `LocalFrame`. Exclude array fields from comparison and identify a rig by its `sha256`, or store tuples.
12. **`Photo.focal_mm` is read and never used.** `src/georoute/photos.py:44,122`; either carry it into `images.json` for the intrinsics decision or delete it. Also document that `h_acc_m`/`v_acc_m` are 1-sigma receiver estimates.
13. **The stub builder rounds 99.5+ ms to `00` without carrying the second.** `tests/conftest.py:56`; `round(stamp.microsecond / 10000) % 100` makes a stub at 99.5-99.999 ms carry SubSec `00` in the same second, so it can shift by up to 1 s. The slice 4 review N8 raised it. Use `stamp.replace(microsecond=0)` plus a carried `timedelta`. It is a test helper, but it can create false matches or failures.
14. **A photographed warm-up outside the PVT track is an error and blocks ingest even though warm-ups are dropped.** `src/georoute/report.py:37-44`; delete the first 13 fixture PVT rows so warm-up 1 precedes `t0`, and `ingest` refuses with "pulse 1 ... outside the PVT track". Treat capture as an error and warm-up as a warning.
15. **`refine_priors` with NaN positions raises `LinAlgError: SVD did not converge` (empty dict: "1-dimensional array given"), not `RigError` with context.** `src/georoute/rig.py:216-217`; validate finite 3-vectors and raise `RigError`.
16. **Velocity rotation still loops in Python despite commit `469fe04` claiming "velocity rotation is vectorised".** `src/georoute/trajectory.py:51-56`; 300k epochs take 1.95 s in the loop against 0.011 s for one vectorised call. Negligible at 1 km, but 300k is the 20-80 km multi-session scale. Call `ned_to_ecef` once with the lat/lon/velocity arrays and add a test with two different lat/lon values.
17. **Module boundaries put slice 6-8 cost in the wrong files.** `src/georoute/rig.py` mixes the TOML schema/config, geometry, the refinement solver and a data-dependent policy gate; `src/georoute/ingest.py` mixes orchestration, stale-output policy, the `images.json` schema and matplotlib plotting. Adding a second aligner or one record field changes both files. Move refinement and the straight gate to `refine.py`, and record building to `record.py`, keeping the `Aligner` callable seam and lazy plotting.
18. **Plan drift will mislead slice 6.** `docs/PLAN-TRACK1.md:85` promises slerp; `:78` says 432 priors and omits `--out`; the real ingest is 431 capture priors after the warm-up drop; `cam_from_rig` and the straight-segment constraint are carried in `STATUS.md:83` but not in the plan. Point the plan at STATUS's carry-forwards, correct 431 and `--out`, and either re-add slerp with a named consumer or mark it deferred.
19. **The GRS80/WGS84 comment says "sub-micrometre"; the measured ECEF difference is about 0.12 mm.** `src/georoute/geodesy.py:16`; 0.117 mm at 45 degrees, 0.121 mm at 51.16 degrees, semiminor -0.105 mm. Correct the comment and record the 0.1 mm approximation bound with the datum metadata.

### Test gaps behind the probes above

The suite is strong on the happy path and on the previously reviewed edge cases, but the following behaviours are untested and are exactly the ones that fail in the probes above: non-finite and out-of-range PVT fields, unknown `carrSoln`/`fixType`, a one-epoch PVT track, metadata types and a zero period, a wrong `nav_period_ms`, negative per-camera delays, zero/negative/NaN exposure, a missing serial when the rig config expects one, a FileNumber-only gap with contiguous filenames, a single-photo camera, a mid-session camera stop, a zero-capture build, a weak-TM2 shifted match, the documented first-missing/extra-last end shift, a tolerance-boundary case, the frame anchor (`fixed[0]` changed to `0` leaves the tests green; add a leading float/no-fix fixture), a pulse permutation, `longest_non_fixed_run` with a gap inside the run, a position step, CLI `OSError`, no-prior semantics, and a non-string datum. The interval cross-check is unpinned: the suite stays green if `INTERVAL_TOLERANCE_S` is set to `1e9` or `0.02`. The suite is 193 passed with the sibling logger sample and 189 passed with 4 skipped without it; no test imports pycolmap or opens a real JPEG, so the COLMAP boundary is unexercised. The stub builder's second-carry bug (N13) can shift a stub by 1 s. There is also no real Canon MakerNote smoke test, because FT1's images were deleted. Each fix above should add one focused test; that is cheaper than a later field-test diagnosis.

## 5. PPK verdict assessment

The method is sound for this data. I parsed `combined.pos`, `forward.pos` and the live `pvt.csv` independently:
- Combined: 1302/1415 fixed (92.0%), 113 float. Live: 1160 fixed (82.0%), 252 float, 3 none. Forward: 1072/1409 fixed (76.1%).
- Where both are fixed (1112 epochs), the median difference is +0.001 m east, +0.003 m north, -0.009 m up; the p95 is 0.033 m east, 0.316 m north, 0.360 m up. The last 33 s mean is -0.004 m east, +0.305 m north, +0.330 m up.
- Epoch-to-epoch vertical jumps over 15 cm: combined 53, forward 38, live 2.
- Over epochs 1100-1300 (40 s), the vertical change is +3.753 m by integrated Doppler, +3.877 m live, +4.496 m combined.

That is strong evidence that the PPK solution diverges at the end and that many of its extra fixes are false. The conclusion "live RTK is more reliable on this data; do not build PPK now; keep RAWX/RTCM as insurance" is justified. Caveats: the integrated Doppler is from the same receiver and has its own bias; the base is 12.5 km away and its MSM arrives at 2 Hz (GPS, BDS) and 1 Hz (GLO, GAL), so the 5 Hz PPK is partly interpolated. The 1 Hz subset result (3.5-6.0%) comes from decimating the rover to 1 Hz, not from the base epochs; the 5 Hz run fixes 92.0% against live 82.0%. Where both are fixed the median difference is 1-3 mm, but the vertical tail is decimetres: my parse gives a signed p95 of 36.0 cm and a max of 43.6 cm on 1112 both-fixed epochs, while the panel's independent match gives p95 |dU| 26.7 cm and max 74.1 cm; the matched subset differs, and both agree the tail is not centimetres. A 90-120 s cluster of the 53 extra fixes agrees with the Doppler integral within 0.1 m over 30 s, so "the extra fixes are largely false" is supported by the jumps and the late-window divergence, not by every extra fix. The 12.5 km baseline has no surveyed truth point. The Doppler check is vertical-only, the 1100-1300 window includes 68 float epochs, and the final 33 s live-vs-Doppler horizontal divergence is +0.247 m north and -0.282 m east, comparable to the PPK-vs-live 0.30 m north, so the horizontal discrepancy is not adjudicated. The 53 vertical jumps and the conservative decision stand. The Doppler check is strong evidence that the PPK end segment is wrong, not that PPK is less reliable in general. The config uses fix-and-hold with a ratio threshold of 3, which is permissive, and one 356 m walk is not a general PPK verdict. The STATUS already says to revisit with a 1-5 Hz own base if field test 2 has long float stretches. What could overturn the conclusion: a closer, multi-frequency, 5 Hz base, a stricter ambiguity validation, a 5-minute static occupation of a known point at 1 Hz with the same receiver and base, a nearby OS Net base, a 60 s correction outage at field test 2, or a session with long float stretches where live RTK has no fixes. The decision not to build PPK now is proportionate. The record and the raw-log insurance checks should be committed (S11); a cycling-speed revisit trigger is needed because FT1's float runs become 27-165 m at 8 m/s, above the design's 50 m gate.

## 6. Gaps that will hurt slices 6-8

1. **COLMAP rig and frame handoff (B3).** Slice 6 cannot configure COLMAP rigs from the current rig config, and a naive filename-based frame will mis-group the 36 ms-skewed exposures; default to independent cameras with per-image `PosePrior` until a real 4-camera test proves the rig constraint.
2. **Per-image prior semantics and uncertainty (S8).** Slice 6 needs `has_prior`/`prior_mode`, `prior_sigma_m`, `time_sigma_s` and `position_sigma_m` to honour "no fix = no prior", the float weights and the gap/interpolation error.
3. **Datum realisation/epoch (S9).** Slice 7/8 needs a structured datum and a WGS84 transform for export and multi-session joins.
4. **Attitude interpolation and camera-centre priors.** The plan's slice 5 says "attitude interpolated by slerp"; the code has no slerp (the slice 5 review S4 noted it is unused), `refine_priors` is library-only, and `ingest` emits antenna positions, not camera-centre priors. Decide whether per-image attitude between rig frames is needed for the 36 ms per-camera offsets; if yes, implement `rig_pose_at(t)` with a two-camera `t_mid` test and wire the centre priors into the output; if no, amend the plan and record why per-image rotations are used.
5. **Frame origin, all-float sessions and frame range (S14, S15).** `ingest.session_frame` anchors at the first RTK-fixed epoch and refuses an all-float session. The design v0.2 uses per-segment frames and says the reconstruction carries float/missing RTK; anchor at the first 3D epoch with a warning, and replace the first-fixed origin with a robust or segment-frame origin in slice 7. The session ENU frame is only safe to about 1 km; mark ECEF authoritative and derive ENU per segment.
6. **Session/image identity and route/build provenance (B4).** `images.json` has the session folder name, rig hash and georoute version, but no session/image id, route/build id, git commit, per-image TM2 accuracy, `t_x`/`t_cmd` or QA status. The design v0.2's provenance and QA gates will need a richer record.
7. **Orthometric height/geoid.** Track 1 stores ellipsoidal height; slice 7 needs the geoid model and the `h_ortho`/`sigma_h` schema.
8. **Unmeasured rig, missing cameras, multi-folder cards and real 4-camera data (B6, S12, S13, S16).** The example rig is placeholder geometry; `ingest` only warns; a camera with no sync edges or a camera that stops mid-session is accepted; a real Canon card spans multiple folders per camera at 20-80 km. The first real 4-camera run is slice 6; add the rig-completeness refusal, the folder recursion and a real MakerNote smoke test.
9. **Multi-session (B4).** The code is per session; the design says multi-session is normal. The datum, frame origin, identity and provenance decisions will need a route-level layer.
10. **Base/PVT steps and the PPK record (B5, S11).** A base-position jump is invisible; the raw-log insurance and the PPK evidence are unenforced and uncommitted. Add the kinematic check, the raw-log checks, the cycling-speed revisit trigger and the `research/ppk-ft1/` bundle.

## 7. Panel convergence

All eight panel reports are in. The table lists the agents that independently raised each point; the agent reports are the source of the detail and this table is the convergence summary.

| Finding | Agents | Count |
|---|---|---|
| B1: non-finite/out-of-range values accepted | EN-1, EN-2, PT-2, IN-1, IN-2, AR-1, AR-2 | 7 of 8 |
| B2: `match` weak verification / silent wrong match | EN-1, EN-2, PT-2, IN-1, IN-2, AR-1 | 6 of 8 |
| B3: COLMAP rig/frame handoff | AR-2, IN-1, AR-1 | 3 of 8 |
| B4: `images.json` identity/provenance | AR-2, IN-2, PT-1, AR-1 | 4 of 8 |
| B5: base/PVT step check | PT-1, PT-2 | 2 of 8 |
| B6: missing rig camera | PT-1, EN-2 | 2 of 8 |
| S1: metadata type/period validation | EN-1, EN-2, AR-1, PT-2, IN-1 | 5 of 8 |
| S2: delay/exposure/curtain ranges | EN-1, EN-2, IN-1, AR-1, PT-1 | 5 of 8 |
| S3: serial/body binding | EN-2, PT-1, PT-2, AR-2, AR-1 | 5 of 8 |
| S4: FileNumber/name gap mismatch | EN-2 | 1 of 8 |
| S5: non-fixed-run metres undercount | EN-1, EN-2, IN-1, AR-1 | 4 of 8 |
| S6: out-of-order pulse warning | EN-2, PT-2 | 2 of 8 |
| S7: CLI OSError/metadata tracebacks | EN-1, EN-2, AR-1, PT-2 | 4 of 8 |
| S8: prior semantics and uncertainty | AR-2, PT-1, IN-2, PT-2, IN-1, AR-1, EN-2 | 7 of 8 |
| S9: datum label/realisation | AR-2, PT-1, IN-1, PT-2, EN-2 | 5 of 8 |
| S10: straight-ride threshold | PT-1, IN-1, AR-2, PT-2, AR-1 | 5 of 8 |
| S11: PPK record and raw-log insurance | EN-1, PT-1, PT-2, IN-2, IN-1, AR-1, EN-2 | 7 of 8 |
| S12: unmeasured rig | AR-2, PT-2, PT-1 | 3 of 8 |
| S13: camera loss/zero capture | PT-2, IN-2, EN-2 | 3 of 8 |
| S14: all-float session refusal | PT-2, EN-2 | 2 of 8 |
| S15: session ENU frame range | IN-2, PT-2, AR-1 | 3 of 8 |
| S16: multi-folder Canon layout | IN-2 | 1 of 8 |
| S17: exiftool warnings / `._` files | EN-2, AR-1 | 2 of 8 |
| S18: session binding | PT-1, PT-2 | 2 of 8 |
| S19: TM2 field ranges | EN-1 | 1 of 8 |
| Nits: umeyama, accEst, schema fields, stale output, module boundaries, plan drift | EN-1, EN-2, AR-1, IN-1, AR-2, PT-2, PT-1 | 7 of 8 |

## 8. What I could not verify

- No real 4-camera images exist, so the multi-camera matching, serial checks, per-camera delays and rig refinement are proven only on synthetic records and stubs; the first real run is slice 6.
- No COLMAP, rig, path, profile, splat or route-package code exists yet; the design v0.2 promises beyond track 1 are not testable here.
- The supplied `ft1/out` ingest output predates the current code (missing `delay_us`/`corr_age` and the rounded `v_acc_m`). I used it only as a cross-check for the 431-image/346-fixed/84-float/1-none counts and the `carr_soln = 0` record; the findings themselves are reproduced from the current code and the fixture.
- The PPK evidence was read from the scratchpad, not from the repo; the numbers are independently verified, but the configs/outputs are not committed.
- The ANN-MB3 phase-centre offset is assumed zero; the real rig config is placeholder geometry and `measured = false`.
