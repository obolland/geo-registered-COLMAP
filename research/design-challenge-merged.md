# Merged red team of OUTPUT-DESIGN v0.1

Date 2026-11-12. Sources: `design-challenge-claude.md` (Claude, 8-agent panel) and `design-challenge-deepseek.md` (deepseek peer session, 8-agent panel plus direct checks of field test 1). Two model families reviewing independently; agreement between them is the strongest signal available before field test 2.

## 1. What both panels keep

Segment-local ENU frames on float64 ECEF anchors; explicit datum and geoid; grade from each sample's own height; a distance-indexed profile; lossless masters with derived runtime packages; overlapping training blocks; gsplat; the fail-loudly QA principle.

## 2. Changes both panels independently call for (adopt)

| # | Change | Claude | Deepseek |
|---|---|---|---|
| A1 | Rider path from the **aligned rig pose** dropped along gravity onto the reconstructed road surface; RTK as prior and QA reference. Account for rig attitude (lean, steering yaw) and the eye origin. Gate registration numerically, including scale (0.1 % is 1 m/km) and lateral offset. | W1, 8/8 | F-registration, F-attitude |
| A2 | **QA with numeric thresholds, per segment, versioned.** Deepseek adds: split pipeline QA from runtime QA (popping and sort can't be seen offline), and stage the report outside the package so a failed build still publishes its report. | W1, N4 | F-QA gate |
| A3 | **Multi-session is the normal case.** Route-level index, route-global `s`, join policy, `session_join` markers, overrun capture at joins, per-session appearance at joins, seam QA. Deepseek measures field test 1 at 1.29 m/s, so about 17 h per 80 km pass (35-52 h for 2-3 passes), 800-1600 blocks and GPU-hours. | W2, 7/8 | F7, F8 |
| A4 | **Single pass gives about ±1 m at best**; the corridor must be measured per segment, not promised. Wider is an optional premium. | W2 | F-corridor |
| A5 | **Storage untested:** byte sizes in the manifest, rolling segment cache, early Quest `storage.estimate`/`persist`/eviction test. | W3, 4/8 | High |
| A6 | **Quest budget:** the design point is 0.3-0.5M splats at 72 Hz or 0.25-0.4M at 90 Hz (from PlayCanvas's 18 ms per million), not 0.3-1M. Quest is a go/no-go on the MVP with a written quality bar and a PCVR fallback; decide 72/90/120 (deepseek: 90 Hz as the comfort floor). | W6 | F1 |
| A7 | **MVP must be able to fail:** 3-4 segments, joins, re-basing, synthetic 80 km package; 1/2/3 m lateral quality and multi-session need deliberate test data. | W4 | F8 |
| A8 | **Shared appearance reference** across blocks and a seam colour-delta gate. | N2 | High |
| A9 | **Floater cull must not delete the road:** road-plane exclusion, cull by defect class. | N3 | F3 |
| A10 | **RTK float gaps:** gate on longest non-fixed run and grade error across it; bridge with the reconstructed road surface; PPK as an optional stage once `rtcm.bin` MSM content is verified. | W7 | High |
| A11 | **Trainer command rate:** precomputed grade change points can reach about 8 per second at speed, against the FTMS 1-2 Hz practical limit. Claude's panel goes further: the trainer filter, change points and comfort speed belong in an app module, with only primitives plus one canonical physics grade published. | W5 | High |

## 3. Deepseek-only findings (adopt)

- **D1 Compute globally, then slice.** Fitting splines, filters and grade per segment steps heading, curvature and grade at every 1 km boundary (a 0.001 1/m curvature step is 0.32 m/s^2 at 18 m/s). Path, profile and grade are computed over the whole build and then cut into segments.
- **D2 LOD crossfade is a pass/fail bake-off gate.** Neither PlayCanvas Streamed SOG nor Spark documents cross-level blending; the comfort evidence asks for crossfade or continuous LOD. Test before freezing the package format.
- **D3 Path smoothing by frequency and wavelength,** not only a 0.1 m deviation bound: a spline within 0.1 m can keep a 0.5 Hz weave. Attenuate lateral and yaw content above about 0.5 Hz, avoid a peak near 0.2 Hz, filter vertical by wavelength at max speed; keep the deviation bound as a separate registration constraint.
- **D4 PPISP contradicts the "no NVIDIA research" rule.** The gsplat implementation is Apache 2.0, but the method's patent position is unchecked. Reword the rule, verify, record the bilateral-grid fallback; add a licence review before any commercial build.
- **D5 No rear-facing camera on a head-tracked product.** Four 18 mm cameras cover about 64x45 degrees each, forward-biased; a rider looking back sees unreconstructed space. Decide while the rig is unbuilt: add a rear camera (or reposition the sides), or constrain to forward gaze with a cockpit rest frame. Add a yaw-bin coverage QA check.
- **D6 Provenance, schema compatibility and atomic publication.** Record rig config hash, tool versions and all parameters per build; define a schema compatibility policy; publish atomically.
- **D7 Re-basing at segment boundaries must be atomic** (camera and all loaded assets together); test at 20 m/s across a join.
- **D8 Engine reversibility is overclaimed** unless the manifest uses an engine-neutral descriptor; renderer-specific fields go under a per-format key.

## 4. Claude-only finding (adopt)

- **C1 Arclength indexing breaks wherever the route folds back** (hairpins, out-and-backs, loops, lateral passes): ownership, segment bounds and prefetch by 3D proximity, a spatial neighbour index in the manifest, a duplicate-splat gate, synthetic hairpin and out-and-back fixtures. (Deepseek's "3D ownership instead of arclength trimming" agrees on the ownership part.)

## 5. Decisions for Olly

1. **Rear view (D5):** add a rear-facing camera (or reposition the side cameras) while the rig is unbuilt, or accept forward gaze only?
2. **Corridor (A4):** default to a single pass with about ±1 m, with wider corridors as an optional premium per segment?
3. **Route scope (A3):** accept that 20-80 km routes are multi-day, multi-session builds, with an explicit go/no-go after field test 2 gives real capture and processing costs?
4. **Field test 2 shape (A7):** can it include a second session over part of the same route, and one deliberate lateral pass, so joins and lateral quality get measured?
5. **Refresh target (A6):** 90 Hz as the comfort floor (smaller Quest budget, about 0.25-0.4M splats), or 72 Hz with more detail?
6. **Quest storage test (A5):** do you have a Quest 3 to run a browser storage test on now?

Once these are answered, OUTPUT-DESIGN moves to v0.2 with sections 2-10 edited per section 2-4 above.
