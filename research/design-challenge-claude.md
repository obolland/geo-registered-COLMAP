# Red team of OUTPUT-DESIGN v0.1 (Claude panel)

Date 2026-11-12. Method: Thinking/RedTeam ParallelAnalysis, 8 independent agents (EN-1 Evidence Demander, EN-2 Edge Case Hunter, AR-1 Trade-off Illuminator, AR-2 Second-Order Effects, PT-1 Assumption Breaker, PT-2 Game Theorist, IN-1 Naive Questioner, IN-2 Devil's Intern), each attacking the whole design. Brief and claim decomposition (C1-C9): `redteam-claude/brief.md`. Raw analyses: `redteam-claude/<ID>.md`. A separate red team by another model family is in `design-challenge-deepseek.md`; the two are merged later.

Severity by independent convergence (N = 8): 4+ critical, 2-3 significant, 1 notable (uncorroborated, weight accordingly).

## 1. Convergent strengths

| Strength | Agents | Grade |
|---|---|---|
| **C1 coordinate design** (per-segment ENU on float64 ECEF, grade from each sample's own height). Arithmetic independently re-checked (126 m drop and 0.63 % grade bias at 40 km; float32 3.9 mm at 40 km, 0.06 mm at 600 m). Forced anyway by every runtime format's bounded local precision (SOG 16-bit, Spark float16, SPZ 24-bit). Field test 1 fixed-epoch vAcc median 13 mm, p95 36 mm supports the grade noise model. | all 8 | Strong foundation |
| **C2 masters plus derived packages** keep the renderer reversible against documented platform churn (PlayCanvas LOD API changed 2.22 to 2.23, Spark XR fix only in 2.4.0, WebGPU XR experimental on Quest). | PT-2, AR-2 | Notable strength |

Core thesis verdict: **sound skeleton, flawed execution in two places** (the rider path's source of truth, and assumptions about route scale and capture).

## 2. Convergent weaknesses and recommended changes

### W1. "Registers with the splats by construction" is false (C3). Critical, 8/8

Two independent failure modes:
- **Rig attitude.** The antenna is on a handlebar bar of an e-bike that leans and steers. A scalar "minus antenna height" ignores that: at about 1.2 m, 5 degrees of lean moves the point about 0.10 m sideways, 10 degrees about 0.21 m, which is the whole 0.1 m path tolerance. Walking the bike from one side makes it a one-sided bias.
- **RTK float.** Field test 1 was float for 18 % of epochs on an open walk (6 runs, longest 103 epochs = about 21 s = about 41 m at 2 m/s, vAcc 38 mm median / 124 mm p95 in float). Under canopy and in cuttings the RTK trace drifts by decimetres while bundle-adjusted visual poses stay consistent with the splats.
- Also: registration accuracy is unmeasured (POC `alignment_max_error 3` m; COLMAP 4.1.1 has the rig pose-prior bugs fixed in 4.2.0; lever-arm loop unbuilt; cameras up to about 36 ms apart).

**Change:** the rider path's source of truth becomes the **reconstructed rig pose**: take each rig frame's reference point from the aligned COLMAP rig, drop it along local gravity onto the **reconstructed road surface** (local fit to splats or SfM points), then smooth. RTK becomes the pose prior and the QA reference, not the path. Section 7 gets numeric gates (p95 path-to-road vertical and lateral offset in cm; per-camera prior residual on fixed epochs) and a bounded, logged per-segment correction.

### W2. Capture scale makes multi-session the normal case (C7, C9). Critical, 7/8

80 km is 11.1 h per pass at 2 m/s, about 3 TB per pass at 38 GB/km; 2-3 lateral passes are 22-33 h. The merge rule (passes within about an hour) covers about 7 km. So: lateral passes land on different days and can't merge, the corridor collapses to about ±1 m almost everywhere, and day boundaries become appearance seams every 10-20 km. "Nothing extra in the MVP" leaves the main visual risk undesigned. A car-video competitor films 80 km in a morning.

**Change:**
- Single pass with a corridor of about ±1 m is the default; wider corridors are an optional, per-segment premium.
- Cross-session joins become first-class: a `session_join` marker per segment boundary, capture guidance to overrun each planned join by 50-100 m so the next session re-covers it, an overlap block trained with per-session appearance embeddings at the join, and a QA gate on colour and geometry continuity across it.
- A capture cost model (hours, TB and GPU-hours per finished km) filled in from field test 2. The 20-80 km scope gets an explicit go/no-go on that model (Olly's decision).

### W3. "Download the route before riding" is unproven (C2, C5). Critical, 4/8

Quest tier estimates are 12-96 GB per 80 km; Quest Browser storage quota and eviction are UNVERIFIED, and Chromium storage is evictable unless `navigator.storage.persist()` is granted. Storage may set the Quest density cap more than the GPU does.

**Change:** add `bytes` per segment, tier and LOD level to `route.json`; the segment is the caching unit; baseline is a rolling cache (current segment plus N ahead), whole-route pre-download an optimisation. Measure `storage.estimate()`, `persist()` and eviction at 5/15/40 GB on a real Quest 3 early: it needs no field data.

### W4. The 1 km single-segment MVP can't validate the multi-segment design. Critical, 4/8

One segment never exercises joins, floating-origin re-basing, blocks or chunks crossing a segment edge, cross-session seams, LOD popping at 18 m/s, or storage.

**Change:** cut the MVP into 3-4 segments of 250-400 m; fly through the joins with re-basing. Build a synthetic 80 km package by repeating MVP tiles to test storage and download. If capture allows, field test 2 includes a second session over part of the same route (Olly's call).

### W5. Tuning policy baked into published data (C4). Significant, 3/8

`grade_ctrl` (10-25 m window, rate limit in %/s, "tune by speed") and `comfort_speed_mps` depend on runtime speed, per-trainer response and per-rider tolerance. Publishing them means every retune republishes every route. `yaw_rate_per_mps` duplicates `curvature`.

**Change:** publish primitives as the contract (`h_ortho`, `sigma_h`, `curvature`, a canonical lightly-smoothed **physics grade** used for virtual speed so every rider, ghost and leaderboard shares one grade). Move the trainer command filter, rate limit, change points and comfort speed into an app module parameterised by device and rider, with a reference implementation in a shared library and default parameters recorded in `route.json`. Drop `yaw_rate_per_mps`.

### W6. Quest tier unproven and capped in the wrong unit (C5, C8). Significant, 3/8

The only Quest figure (PlayCanvas PR #322: 1M splats at 30.7 fps, 18 ms per million) implies about 7-15 ms of fixed cost, leaving about 0.24-0.39M splats at 90/72 Hz: 72 Hz is marginal on WebGL2. The 5k splats/m cap comes from a bytes table, and a per-route-metre cap spreads detail evenly, blurring the near road the rider looks at.

**Change:** the MVP is an explicit go/no-go for Quest as the baseline, against a perceptual quality bar written down before measuring; section 10 states the fallback (PCVR as the product tier, Quest as a preview tier). Express density budgets by region (near road and verge vs far field), driven by measured frame time.

### W7. Grade across RTK float gaps (C4, C6). Significant, 2/8

A 41 m float gap is longer than the grade window; "smoothed harder" turns a fix-return step into a false sustained grade.

**Change:** QA gate on the longest non-fixed run (metres) and worst grade error across it. Bridge gaps with the reconstructed road surface constrained by fixed epochs either side (falls out of W1). Investigate PPK from `receiver.ubx` plus `rtcm.bin` as an optional stage once MSM content is verified.

### Notable, single witness (uncorroborated but serious)

- **N1 Arclength partitioning breaks on hairpins, out-and-backs, loops and lateral passes (EN-2).** Segments, blocks, ownership trims and prefetch are all indexed by distance along the route, but a hairpin's lower leg is 20-30 m away in 3D and 300 m away in arclength: double-drawn surfaces, wrong trims, unloaded facing legs. *My view: real and high severity for the routes the product sells.* Change: ownership by 3D proximity, segment bounds allowed to overlap with a spatial neighbour index in the manifest, prefetch over 3D neighbourhoods, a duplicate-splat QA gate, synthetic hairpin and out-and-back fixtures.
- **N2 Colour steps every 100 m (AR-2).** Per-block appearance optimisation lets each block settle its own exposure; trims fix duplicates, not colour. Change: one appearance reference per session, match blocks over overlaps, seam colour-delta QA gate.
- **N3 Floater tube cuts the road (AR-2).** A 1.5-2 m radius about a 1.2-1.5 m eye reaches the road and kerb. Change: clearance volume above the reconstructed road surface.
- **N4 One failing segment blocks an 80 km route (AR-2).** Change: per-segment gates with severity, threshold-set version recorded in `qa/report.json`, partial publish with a recapture list.

## 3. Steelman (strongest version of the design)

**Position:** A versioned, segment-framed route package from lossless masters is the right contract for a not-yet-built app.

1. Per-segment ECEF-anchored frames remove curvature, grade bias and precision failure modes completely, not partially.
2. Every runtime splat format stores bounded local positions, so local origins are forced regardless of renderer.
3. Lossless masters make renderer churn a rebuild, never a recapture of roads that cost hours.
4. Distance-indexed profiles serve trainer, map, markers and Strava from one canonical source of truth.
5. Leaving numbers as Measure is honest: no Quest road-scene benchmark exists to borrow from.
6. A hard QA gate enforces the project's fail-loudly rule where silent errors become rider sickness.
7. gsplat and splat-transform keep the product path free of non-commercial licences that would block launch.
8. The cheap-now, expensive-later decisions (frames, versioning, masters) are exactly the ones it gets right.

**Validity:** the skeleton is right; frames and masters are the irreversible choices and both survive attack.

## 4. Counter-argument

Constraint check: HARD (physics) are curvature, float precision, 2 m/s from 0.8 m spacing and 0.4 s camera rate, and daylight. SOFT presented as hard: 0.8 m spacing, ±1 m corridor, the hour merge window. ASSUMPTIONS presented as facts: registration by construction, download-before-ride, a 1D route, single-session capture, and that field test 2 can validate scale decisions.

1. The design trusts the antenna track, yet the antenna leans, steers and drifts under trees.
2. Splats live where the bundle adjustment put them, so the path must come from there.
3. Every arclength index assumes a 1D route; hairpins and out-and-backs fold space back on itself.
4. Eleven hours per 80 km pass makes multi-day, multi-seam capture the normal case, not an edge.
5. Its own merge rule then shrinks the steering corridor to a metre on almost every route.
6. Download-before-ride rests on an unmeasured headset storage quota that may cap Quest density.
7. Publishing tuning policy as route data forces republishing every route whenever a trainer is retuned.
8. A one-segment walking-pace MVP cannot fail on any of these, so the format freezes untested.

**Assessment:** keep the skeleton (C1, C2); change the path's source of truth, design for multi-session, 3D-overlapping routes and rolling caches now, and restructure the MVP so it can actually fail.

## 5. Proposed edits to OUTPUT-DESIGN (pending merge with the deepseek review and Olly's agreement)

1. Section 4: path from reconstructed rig pose dropped along gravity onto the reconstructed road surface; RTK as prior and QA reference.
2. Section 5: publish primitives and a canonical physics grade; move trainer filter, change points and comfort speed to an app module with recorded defaults; drop `yaw_rate_per_mps`.
3. Section 3: `bytes` per segment, tier and LOD; segment as the caching unit; spatial neighbour index; `session_join` markers.
4. Section 6: ownership and prefetch by 3D proximity; session appearance reference and seam matching; road-clearance floater volume; region-based density budgets.
5. Section 7: numeric thresholds, per-segment severity, threshold version, partial publish; gates for path-to-road offset, longest float run, seam colour delta, duplicate splats.
6. Section 8: cross-session joins first-class (overrun capture, overlap block with per-session appearance).
7. Section 9 and 10: single pass ±1 m default; capture cost model and an explicit 20-80 km go/no-go; Quest go/no-go with a written quality bar and a PCVR fallback; early Quest storage test.
8. MVP: 3-4 segments, synthetic 80 km package, hairpin and out-and-back fixtures, and a second session over part of field test 2 if capture allows.
