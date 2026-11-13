# Triage of the deepseek track 1 review

Date 2026-11-13. Each finding in `track1-deepseek-review.md` was checked against the code, and the important ones reproduced on copies of the fixture (scratchpad `verify/`).

## Confirmed, fix in track 1

| Item | Verified how | Fix |
|---|---|---|
| B1 non-finite and out-of-range PVT values pass | `lat=nan` loads (NaN position), `lat=100` loads, `carrSoln=5` gets no flag and the fix counts read 0/0/0, a one-epoch track gives NaN positions | Range-check every PVT field (lat, lon, height, carrSoln 0-2, fixType 0-5, finite velocities, non-negative accuracies, nano range); refuse fewer than two epochs |
| S2 delays, exposure, curtain travel unchecked | Code read: no ranges on `camN_delay_us`, `ExposureTime`, `--curtain-travel-ms` | Delay 30-500 ms (the logger rejects under 30 ms), exposure above 0 and up to 30 s, curtain 0-50 ms finite, everywhere they enter |
| B2 `match` skips the session integrity check | `pulses_lost=2` is a report error, but `_match` never runs the report | `match` refuses a session with integrity errors, like `ingest`; a tolerance-boundary test so loosening it fails |
| B5 a position step passes as fixed | Code read: no kinematic consistency check; `rtcm.bin` never read | Flag epochs whose displacement disagrees with integrated Doppler (threshold calibrated on field test 1); record the RTCM 1005 base position(s) and refuse a base that moves mid-session; warn on NTRIP reconnects |
| B6 + S13 missing or dying camera passes | Code read: rig cameras with no exposures are skipped; no check that a camera lasted the session | With a rig, every rig camera must have exposures (error); a camera that stops well before the session end warns; zero capture images refuses |
| S3 expected serial silently unverified | Code read: photos without a serial bypass the check | Refuse when an expected serial is set and a photo has none; a `measured = true` rig needs a serial per camera |
| S4 gap check uses filename numbers even when ordering by FileNumber | Code read (`order_photos`) | Check gaps in the sequence actually used |
| S5 run metres use the nominal period | Code read | Integrate over real epoch intervals |
| S6 permuted pulse numbers only warn | Code read | Error |
| S7 OSError tracebacks in the CLI | Code read | Catch and exit cleanly |
| S8 no-prior decision not encoded | Field test output has a `carr_soln 0` image with a full position | Per image `has_prior` (false without RTK) and `prior_sigma_h_m` / `prior_sigma_v_m` |
| S14 an all-float session is refused | `session_frame` needs a fixed epoch | Anchor at the first 3D-fix epoch, warn when none is fixed |
| S17 macOS `._` files and exiftool warnings refuse a folder | Reproduced: a `._IMG_0001.JPG` companion makes `read_photos` raise | Ignore hidden files (`exiftool -i HIDDEN`); exiftool errors fatal, warnings recorded |
| B4 / S15 / S18 provenance | Code read | `images.json` gains input file hashes, generation time, the observed serial per camera, a frame id, and a note that ECEF is authoritative and the session ENU is for plotting and short builds |
| S12 unmeasured rig only warns | Code read | `ingest --rig` refuses an unmeasured rig unless `--allow-unmeasured-rig` |

## Confirmed, deferred (recorded, not track 1)

- B3 COLMAP rig and frame handoff (`cam_from_rig`, intrinsics, frames by pulse not filename): slice 6 scope by design.
- S9 datum realisation and epoch: matters for multi-year, multi-session joins; record when slice 8 handles multi-session.
- S10 straight-ride threshold is a heuristic: keep, revisit with real COLMAP in slice 6.
- S11 PPK not reproducible from the repo: the verdict is recorded with method and numbers; add a script only if PPK is revisited.
- S16 Canon multi-folder cards (over 9999 photos per camera): not needed for field test 2 (about 1250 per camera); required before 20 km routes.

## Not adopted

- B2 "steady one-pulse shift": this is the documented residual risk (missing first plus extra last photo with no file-number gap), not a new hole. The serial and session-binding fixes above narrow it further.
