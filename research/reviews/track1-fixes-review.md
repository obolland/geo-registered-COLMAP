# Review of the track 1 fix commits

Date 2026-11-13. Reviewer: independent pass on branch `pipeline/track1`, commits `4fa2e8d`, `d494fd7`, `11f5ed8`, `e15158d` (on top of `92f44ba`). No repo file was modified.

Method: read the final code of every touched module; ran the suite (244 passed, `ruff check` clean); ran about 66 single-edit mutations on a copy of `src/` and `tests/` (each replacement asserted to apply exactly once, so no silent no-ops) and recorded whether any test went red; re-ran the original deepseek reproductions on copies of the `ft1_trim` fixture; ran field test 1 through the CLI.

Verdict: the work is real. Every code edit the triage promised is present except one half of one row (a `measured = true` rig is not required to have serials). Test coverage is good on the main paths but has a set of mutation survivors (listed per row) where the code is present and nothing would fail if it were removed. There is one new false-refusal risk (a TM2 row with an invalid time mark now kills the whole session) and some parser-level robustness holes the S7 fix did not reach.

## Field test 1 and the new range limits

`uv run georoute ingest <session> <stub images> --out <dir>` with stubs built as `write_stub_photos` does from `photos_exif.tsv`: exit 0, 431 images, 1 warm-up dropped, flagged `not-fixed 85, no-3d-fix 0, gap 0, jump 1`, no session errors, `without_prior 2`. Base read from `rtcm.bin`: one station (3527), one position (4006364.0375, -294184.8692, 4937580.8104) over 9 messages; the radius is 6365.3 km, which is sane. `images.json` carries `inputs`, `generated_utc`, `serials_seen`, `coordinates`, `frame.id`, `base`.

Range limits against real data: all 1,415 PVT rows and 434 TM2 rows pass; delays are 87-123 ms in the field against a 30-500 ms window; the other limits have wide margins. The one limit with a thin margin is the jump threshold. Residual between the position step and integrated Doppler on field test 1 (fixed to fixed steps): median 0.033 m, p99 0.094 m, then 0.130, 0.143, 0.158, 0.161, 0.167, and one step at 0.321 m (epoch 788, fixed on both sides, the one flagged jump). Non-fixed steps top out at 0.161 m. So 0.25 m clears the second-worst real step by 1.5x on a walk; it has not been calibrated on a vehicle and is not speed-scaled (N3).

## Triage rows

| Row | Status | Evidence |
|---|---|---|
| B1 non-finite and out-of-range PVT values | fixed and tested | `session.py` `_Row.integer/number` with ranges for lat, lon, height, carrSoln, fixType, diff, numSV, velocities, hAcc, vAcc, nano, iTOW; one-epoch refusal. Mutations on lat, lon, height, carrSoln, fixType, velocity, hAcc, nano and the two-epoch check all go red. Untested ranges (mutations survive): `diff` 0-1, `numSV` 0-255, `vAcc` non-negative. The `isfinite` branch is redundant with the finite range bounds, so its survival is harmless |
| S19 TIM-TM2 fields | fixed and tested, with gaps | week, towMs, towSubMs, accEst and falling-before-rising go red when removed. Survivors: the upper bound on the falling edge (`< 1.0 s`), the `timeBase` 0-2 range, the `pulse_n >= 0` bound. The triage asked for the falling edge "about the 50 ms pulse width" after the rising edge; the code accepts 0 to 1 s (N4) |
| S2 delay, exposure, curtain | fixed and tested | delay low and high bounds, exposure lower and upper bounds, and the ingest curtain range all go red. The `isfinite` curtain guard is redundant with the range test |
| B2 `match` runs the integrity check | fixed and tested, boundary test missing | `cli._match` raises on report errors; `test_match_refuses_a_session_with_integrity_errors` goes red without it. The second half of the row, "a tolerance-boundary test so loosening it fails", is not done: `INTERVAL_TOLERANCE_S` 0.03 to 0.3 and 0.03 to 0.015 both leave the whole suite green (S1) |
| B5 position step, moving base, reconnects | fixed and tested, with gaps | `position_jumps` threshold, the `jump` flag, the report warning, the base-moved error and the no-`rtcm.bin` warning all go red when removed. Survivors: `jump` in the `has_prior` exclusion set; the "no 1005/1006 message" warning; the NTRIP reconnect counters (S6) |
| B6 + S13 missing or dying camera | fixed, one guard untested | rig camera with no exposures and the "stopped early" warning go red. The zero-capture refusal (`if not captures`) survives removal: without it an all-warm-up session would raise a bare `ValueError` from `max()`. I confirmed the guarded path gives a clean exit 1, "no capture photos to position" |
| S3 expected serial unverified | half fixed | A photo without a serial is refused when an expected serial is set (tested, goes red). The second half, "a `measured = true` rig needs a serial per camera", is not implemented: a measured rig with empty serials ingests with exit 0 (reproduced; S4). `rig.py` has no such check |
| S4 gap check in the sequence used | fixed and tested | mutation back to name numbers goes red; the test also covers folder rollover |
| S5 run metres over real intervals | fixed and tested | goes red |
| S6 permuted pulse numbers | fixed and tested | goes red |
| S7 tracebacks in the CLI | partly fixed | Tested: unwritable output, missing rig file. `camera = 1` now gives a clean "camera must be [[camera]] tables" (verified by hand) but no test: removing the check survives. The three CLI `OSError` catches survive removal because the layers overlap. Still tracebacks: `session-report --json` to an unwritable path (`FileNotFoundError`), a non-UTF-8 `session.json` or CSV (`UnicodeDecodeError`), an unreadable session file in `session-report`/`match` (`PermissionError`) (S3) |
| S8 `has_prior` and sigma | fixed and tested, one arm untested | no-prior for `carr_soln 0` and sigma `None` are covered. The `jump` arm of `has_prior` is untested. Semantics note: float (`carrSoln 1`) keeps `has_prior = true` with the PVT `hAcc` as sigma; the triage said "false without RTK", which can be read either way (N1) |
| S14 all-float session | fixed and tested | first-3D-fix anchor and the warning both go red |
| S17 `._` files and exiftool warnings | half tested | hidden-file skip goes red. "exiftool errors fatal, warnings recorded": both branches survive removal. By hand, a corrupt JPEG is refused (via the missing EXIF tag message), so the outcome is right, but no test pins the errors path or the warnings list |
| B4 / S15 / S18 provenance | fixed and tested, with gaps | hashes, `generated_utc` and the ECEF note go red when removed. Survivors: `serials_seen`, the `frame.id` 3d-fix variant, `rtcm.bin` in the hashed set. Photos are not hashed (N5) |
| S12 unmeasured rig refuses | fixed and tested | goes red |
| Noted: single-photo camera warns | fixed and tested | goes red |

## Findings

### Blocker

None. Nothing in the fixes breaks field test 1 or worsens behaviour for the data in hand.

### Should-fix

**S1. The B2 tolerance-boundary test is missing.** Evidence: mutating `INTERVAL_TOLERANCE_S` from 0.03 to 0.3 and to 0.015 leaves all tests green (field-test sample enabled). The triage listed this test explicitly. Fix: two cases on a synthetic session in `tests/test_photos.py`, one with an interval error of 0.025 s that must match and one of 0.035 s that must refuse.

**S2. A TM2 row with an invalid time mark now makes the whole session unreadable.** Evidence: set row 1 of `ft1_trim/tm2.csv` to `wnR 0, timeBase 0, timeValid 0, no falling edge` (what a receiver without GPS time yet may report): `session-report` exits 2 with `tm2.csv:2 wnR 0 is outside 2000 to 4000`. Before the fix, a not-valid mark on an unphotographed pulse was only a warning, and `session.command_time` and `report.py` still contain that tolerance. No field data has such a row (all 434 field-test-1 rows are `timeBase 1, timeValid 1`), so this is unproven for the real receiver, but the new check removes a tolerance the design relies on. Fix: in `_read_tm2` apply the week, ms, sub-ms and edge-order checks only when `timeBase == 1 and timeValid`; for other rows keep type checks and let `command_time` refuse them when photographed. Add a test with a warm-up pulse carrying week 0 and `timeValid 0`.

**S3. Residual CLI tracebacks (S7 not complete).** All reproduced: `georoute session-report s --json /nonexistent/x.json` raises `FileNotFoundError`; a `session.json` or `pvt.csv` of non-UTF-8 bytes raises `UnicodeDecodeError` out of `load_session` (a `ValueError`, not a `SessionError`, so `cli._session_report` does not catch it); `chmod 000 pulses.csv` gives `PermissionError` from `session-report` and `match`. Fix: in `load_session`, `_read_meta` and `_rows` wrap `OSError` and `UnicodeDecodeError` into `SessionError` (exit 2); in `_session_report` catch `OSError` around the `--json` write. Add a test for each.

**S4. A `measured = true` rig still needs no serials (half of the triage S3 row).** Evidence: `rigs/handlebar-4cam.example.toml` with `measured = true` and its empty serials ingests with exit 0. Fix: in `load_rig`, when `measured` is true and any camera has no serial, raise `RigError`; add a rig test. Without it the camera-swap protection the serial check exists for is optional.

**S5. The base-moved check can false-refuse a VRS mountpoint.** Two distinct 1005 positions are an error, which blocks `ingest` and `match`. A virtual-reference-station mountpoint sends a 1005 whose position follows the rover and changes during a vehicle run. Field test 1 (`LOCAL` on `truertk-eu.pointonenav.com`) sent one position so it passes, but I could not establish from the repo whether field test 2's mountpoint is physical. Fix (author's choice): record the mountpoint in the report and treat a change as an error only when the displacement exceeds a few centimetres and the station id also changed, or downgrade to a warning that states the distance moved and leave the Doppler-jump flag to catch the effect.

**S6. Mutation survivors that leave promised behaviour unpinned.** Each is a small test. (a) `jump` in the `has_prior` exclusion set. (b) Zero capture pulses refuses cleanly. (c) `camera = 1` in a rig file. (d) exiftool error text and warning recording (a stub `exiftool` on `PATH`). (e) NTRIP reconnect counters produce a warning. (f) "no 1005/1006 message" warning. (g) `serials_seen` and the 3d-fix `frame.id` in `images.json`. (h) `diff`, `numSV`, `vAcc` and the TM2 `timeBase` and falling-edge upper-bound ranges: add rows to `test_out_of_range_pvt_values_refuse` and `test_out_of_range_tm2_values_refuse`.

### Nit

**N1. `has_prior` for float.** `has_prior = carr_soln >= 1 and no "no-3d-fix"/"jump"`, sigma from `hAcc`. A float position can be tens of centimetres off with a smaller reported `hAcc`. If the intent is "prior only from RTK fixed", use `carr_soln == 2`; if float is meant to be a weak prior, say so in `OUTPUT-DESIGN.md` and consider inflating sigma. A `gap` flag also neither removes the prior nor inflates sigma.

**N2. RTCM parser.** Verified: CRC24Q polynomial 0x1864CFB (check value for `"123456789"` is 0xCDE703, matching the standard); 1005 layout DF025 at bit 34, DF026 at 74, DF027 at 114, 38-bit signed, 0.1 mm, payload 19 bytes; real field-test frames pass CRC and give a sane radius. The test encoder mirrors the decoder, so the round-trip tests alone would not catch a shared layout error; the field-test sample test does, but it is skipped when the sample is absent. The docstring says positions are distinct "to 1 mm" but they are compared at 0.1 mm.

**N3. `position_jumps` assumes a trapezoid between consecutive epochs.** Across a long gap (for example 2 s at 10 m/s while turning) the Doppler prediction can miss by more than 0.25 m with no real jump, and the report counts it on top of the `gap` flag. Restrict the check to steps within `GAP_FACTOR * nav_period_s` or scale the threshold with step length. Revisit after the first vehicle session, since 0.25 m was calibrated on a walk.

**N4. Falling-edge check is loose.** The logger's own `_after` accepts equal times; georoute requires strictly later and under 1 s, where the triage said about the 50 ms pulse width. Tighten (for example 5-500 ms, or from `pulse_ms` in `session.json`) or document why 1 s.

**N5. Provenance gaps.** `images.json` hashes session files but not `receiver.ubx` or the photos. The schema moved to `georoute.images/1.1.0` but `docs/OUTPUT-DESIGN.md` and `research/STATUS.md` do not mention `has_prior`, the sigma fields or 1.1.0.

**N6. `STOPPED_EARLY_PULSES = 10` is absolute.** A camera that dies fewer than 10 pulses (about 8 m) before the end is silent, and on the 18-pulse fixture the warning cannot fire for the review's cam2 blank-from-pulse-12 repro. Fine for real runs; consider a fraction of the session too.

## Reproductions from the deepseek review, re-run on the fixed code

- B5 (1.5 m east step from row 44): the session report now says `1 position jumps ... first at epoch 43`; the fixture's 18 photos do not bracket that step so none are flagged. Photos after a one-step shift carry no flag; only the bracket of the step is flagged, which is the design of the check.
- Measured rig with empty serials: still ingests, exit 0 (S4).
- Out-of-range PVT and TM2 values, delay and exposure, single epoch, `camera = 1`, `._` files, unmeasured rig, all-float session, permuted pulse numbers: now refuse or warn as the triage specified.
- Tracebacks: three remain (S3).
- TM2 invalid mark with week 0: new refusal (S2).
