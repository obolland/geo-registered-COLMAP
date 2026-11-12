# Design challenge: pipeline output design (adversarial review)

Status: adversarial review of `docs/OUTPUT-DESIGN.md` v0.1, 2026-11-12.
Reviewer: independent Claude session ("splat pipeline deepseek"), running the `Thinking` skill in red-team / adversarial-validation mode: an 8-agent panel (2 engineers, 2 architects, 2 pentesters, 2 interns), each attacking the whole argument, plus direct checks of the field-test sample and the arithmetic the design relies on.
Inputs read: `docs/OUTPUT-DESIGN.md`; `research/STATUS.md`; `research/runtime-rendering-and-headsets.md`; `research/tiling-lod-and-training.md`; `research/trainer-and-sensors.md`; `research/comfort-and-sickness.md`; `gps-camera-trigger/docs/PIPELINE-HANDOFF.md`; `gps-camera-trigger/samples/20261108T153731Z/`.

Verdict: the core architecture is sound and should survive, but the design is not ready to freeze as a package contract. Several load-bearing claims are contradicted by the design's own cited evidence or by arithmetic, and the QA gate as written cannot catch the failures that matter most. Fix the Critical findings before committing the schema.

## 1. What the design gets right

These are evidence-backed and should survive the challenge:

1. **Per-segment local ENU frames anchored in ECEF float64.** The arithmetic is correct: a 40 km baseline drops 125.6 m below the origin tangent plane, the local "up" tilts 0.63%, and float32 ulp is 3.9 mm at 40 km versus 0.061 mm at 600 m. The runtime and tiling research both independently require tile-local coordinates. This is the right fix, and it is the strongest part of the design.
2. **Explicit datum and geoid.** The POC assumed WGS84; the design refuses to. Recording ETRS89 and OSGM15 in the manifest is correct and necessary.
3. **The rider reference path comes from the RTK antenna track, not from COLMAP camera centres.** With four cameras interleaving on a rig, camera centres would zigzag; the antenna track plus rig geometry is the right source. The path carries no head, bike, or bob motion, which the comfort research demands.
4. **Two grade channels.** `grade_geom` for visuals and stats, `grade_ctrl` for the trainer, smoothed, quantised, unclamped, with device clamps and difficulty left in the app. This matches the trainer research almost line for line and is the right division of responsibility.
5. **Distance-indexed profile with `s` and `s_h`, orthometric height, `sigma_h`, quality flags, curvature, yaw rate, comfort speed, corridor widths and surface.** This is the right set of consumer-facing channels; the separate horizontal distance is a good catch for Strava and for slope formulas.
6. **Overlapping training blocks with arclength ownership.** The H3DGS-style design (train on overlap, trim to ownership) is the best-evidenced large-scene approach, and the decision to use gsplat with MCMC, antialiasing, per-image appearance and masks follows the research rather than vendor convenience.
7. **Tiering and a publish gate.** Quest and PCVR tiers, reduced SH for Quest, a separate environment layer, and a QA report that must pass before publishing are the right shape.
8. **Honest labels.** The design uses **Decided / Proposed / Measure** and "estimates (not measurements)" better than most proposals. The problem is not dishonesty; it is that several **Measure** items are load-bearing for a schema that is presented as settled.

## 2. Findings, ranked

Severity: **Critical** means the product or the package contract is likely to fail or need a redesign; **High** means significant rework or a user-visible defect before the MVP; **Medium** means a correctness or maintainability risk to fix during implementation; **Low** is polish. "Convergence" is the number of independent panel agents that raised the same point; 1 means a single witness and should be weighted accordingly.

| # | Severity | Finding | Claims | Convergence |
|---|---|---|---|---|
| F1 | Critical | Quest splat budget upper bound contradicts the cited 18 ms per million measurement | C12, C6 | 6 of 8 |
| F2 | Critical | Primary renderer has no documented LOD transition blending, against the comfort requirement | C6, C11 | 3 of 8 |
| F3 | Critical | Floater-removal tube would delete the road surface under the rider | C6 | 4 of 8 |
| F4 | Critical | Per-segment spline and filter processing breaks path, curvature and grade continuity at every 1 km boundary | C2, C4, C5 | 4 of 8 |
| F5 | Critical | QA gate has no numeric thresholds, cannot test runtime LOD/sort, and its report cannot exist for a failed build | C7 | 7 of 8 |
| F6 | Critical | Path-to-splat registration is not "by construction"; scale, lateral offset and axis convention are unchecked | C4, C6 | 5 of 8 |
| F7 | Critical | No route-level index for multi-build routes; joins are not gated | C8 | 5 of 8 |
| F8 | Critical | 80 km route economics and the 1 km measurement plan are not viable for the claims made | C9, C10, C12 | 7 of 8 |
| F9 | High | "No NVIDIA research code" contradicts the use of PPISP; licence and patent review missing | C6 | 3 of 8 |
| F10 | High | Independently trained blocks have no shared reference exposure; appearance seams survive trimming | C6 | 5 of 8 |
| F11 | High | The 72 vs 90/120 Hz target is unresolved, against the comfort evidence | C1, C12 | 5 of 8 |
| F12 | High | Pre-download assumes 12-96 GB of browser storage that is unverified on Quest | C12 | 7 of 8 |
| F13 | High | `corridor_half_width_max_m = 1.0` overpromises what one-pass field test 2 captures; the lateral-quality test has no ground truth | C1, C5, C9 | 4 of 8 |
| F14 | High | `grade_ctrl` change points have no time-based rate limit or speed-dependent smoothing | C5 | 4 of 8 |
| F15 | High | Path smoothing is specified as a max-deviation bound, not a frequency-domain requirement; rider weave can survive | C4 | 5 of 8 |
| F16 | High | Antenna-to-road path assumes a vertical lever arm; a handlebar antenna swings with steer and lean | C4, C9 | 3 of 8 |
| F17 | High | Engine reversibility is overclaimed; the manifest is PlayCanvas Streamed SOG-shaped | C11 | 5 of 8 |
| F18 | Medium | Per-segment `env.sog` is over-engineered and can pop or change at boundaries | C3, C6 | 3 of 8 |
| F19 | Medium | No realism or reconstruction-quality gate, despite ultra-realism being the selling point | C7 | 3 of 8 |
| F20 | Medium | The SfM/training/master coordinate frame and its float32 precision are unspecified | C2, C6 | 3 of 8 |
| F21 | Medium | "Datum: ETRS89" plus ECEF is not a frame realisation or epoch | C2 | 3 of 8 |
| F22 | Medium | Strava/FIT distance should use `s_h`, not `s`; the design does not say which | C5 | 2 of 8 |
| F23 | Medium | Schema ambiguities: `s` frame, units, missing per-file schema versions, empty/duplicated fields | C3 | 5 of 8 |
| F24 | Medium | Per-sample `surface` is manual for 20-80 km routes; no interval model or classification plan | C5 | 2 of 8 |
| F25 | Medium | "Splat coverage weak" and "captured lateral band" have no defined computation | C5, C7 | 3 of 8 |
| F26 | Medium | `qa/report.html` inside the package cannot be inspected when the gate fails | C3, C7 | 3 of 8 |
| F27 | Medium | No end-to-end axis-convention check; a 180 degree or sign error is easy and catastrophic | C2 | 3 of 8 |
| F28 | Low | Re-basing at segment boundaries can hitch if not atomic | C2 | 2 of 8 |
| F29 | Low | "All three research topics" overstates the coordinate-frame agreement | C2 | 2 of 8 |
| F30 | Low | `grade_ctrl` quantised to 0.1% while FTMS accepts 0.01%; document or preserve | C5 | 2 of 8 |
| F31 | Low | Loop closure and start/end elevation mismatch are not specified beyond a boolean | C5 | 2 of 8 |
| F32 | Medium | Arclength block ownership can unload splats visible across a hairpin or switchback | C6 | 4 of 8 |
| F33 | Medium | The 2 m/s capture cap is a logger configuration choice, not a physical limit | C9 | 3 of 8 |
| F34 | Medium | Eye height versus capture camera height is unspecified and unmeasured | C1, C4 | 2 of 8 |
| F35 | Medium | The rig config is underspecified for the path and lever-arm calculations | C4, C9 | 3 of 8 |
| F36 | Low | Hard-shadow or changing-light sessions are not flagged in QA | C9, C7 | 2 of 8 |
| F37 | High | The rig has no rear-facing camera, but the product is head-tracked VR; view-sphere coverage is undefined | C1, C9 | 3 of 8 |
| F38 | High | Provenance is too thin to reproduce or retrain; schema compatibility and atomic publication are undefined | C3, C10 | 3 of 8 |
| F39 | High | RTK float gaps and the ellipsoidal/orthometric grade distinction have no policy | C4, C5 | 3 of 8 |
| F40 | High | The consumer contract has no enforcement semantics; the cheapest app implementation defeats the design | C1, C5 | 3 of 8 |

### F1. Quest splat budget upper bound contradicts the cited measurement (Critical, C12/C6)

**Finding.** The design says "Quest 0.3-1M rendered splats". The PlayCanvas measurement it cites says each million splats costs about 18 ms per frame on Quest 3, and a 1M budget ran at 30.7 fps quality (32.6 ms) and 40.3 fps performance (24.8 ms). The frame budget is 13.89 ms at 72 Hz, 11.11 ms at 90 Hz and 8.33 ms at 120 Hz. Before any other cost, 18 ms per million leaves at most 0.77M at 72 Hz, 0.62M at 90 Hz and 0.46M at 120 Hz. The measured 1M run also implies 6.8-14.6 ms of non-splat cost per frame; subtracting that leaves roughly 0.0-0.4M at 72 Hz and 0.0-0.24M at 90 Hz. A 1M Quest budget cannot hold 72 Hz, let alone 90, and the design's own lower bound of 0.3M is already at the edge. The design's own research file says 0.3-0.5M at 72 Hz; the design widened the range to 1M, which is the vendor rule of thumb, not the measurement.

**Recommended change.** Set the Quest 3 budget from the measurement, not the vendor guidance: 0.3-0.45M at 90 Hz, or 0.4-0.6M at 72 Hz, with the actual number from the MVP sweep. Make the Quest tier density cap derive from that budget, and make the QA gate use frame-time p99 at the chosen rate. Do not present 1M as an acceptable Quest target.

### F2. Primary renderer has no documented LOD transition blending (Critical, C6/C11)

**Finding.** The design makes PlayCanvas primary and packages Streamed SOG, whose runtime selects one LOD per chunk by distance. PlayCanvas's own docs state that the engine "picks one LOD level per region of the scene from its distance to the camera", the Streamed SOG spec says "a chunk only ever contains splats of a single LOD level" and "a viewer selects exactly one level per leaf", and no transition blending is documented. At 40 mph the rider passes a 16 m chunk every 0.9 s, so a level change is a discrete swap. Spark is not a free fix either: its docs describe a discrete frontier of parent/child substitutions and say cross-level blending "has not been implemented/experimented with yet"; the 1.5 base only makes steps less apparent. The comfort research requires "crossfade or continuous LOD (opacity-blended); never swap whole tiles in one frame in the central or peripheral view." The design defers "LOD transition behaviour" to a Measure, but the package format and renderer choice are already expressed as settled. The comfort evidence treats popping as a comfort defect, not cosmetics.

**Recommended change.** Make the renderer/LOD-transition question a pass/fail bake-off criterion before freezing the package schema. Either (a) verify or implement a chunk crossfade in PlayCanvas, (b) implement a crossfade in Spark or a custom loader, or (c) prove that 16 m chunk switches are imperceptible at 18 m/s on the MVP. Add a headset fly-through at riding speed with head yaw to the release gate, not just an offline flicker metric. The bake-off must score popping explicitly, and the package should stay renderer-neutral until it does.

### F3. Floater-removal tube would delete the road surface (Critical, C6)

**Finding.** The design says "remove splats inside a tube around the rider path (about 1.5-2 m from the eye trajectory)". The eye path is only about 1.2-1.4 m above the road. A 1.5-2 m tube around the eye trajectory therefore contains the road surface itself. At a 1.5 m radius and 1.3 m eye height, the road within about 0.75 m laterally of the path is inside the tube; at a 2.0 m radius it is about 1.5 m, which is the whole plus or minus 1 m steering corridor. Applied naively, this deletes the road under and ahead of the rider, which is the surface the rider looks at most. The comfort research proposes the same wording, but the design inherits it without resolving the geometry.

**Recommended change.** Define the culling volume by defect class, not by radius alone: remove splats that are low-opacity, small, isolated, or unsupported by the road plane or depth prior inside the tube; preserve the ground band (for example, exclude the road plane plus a small margin, or cull only splats above the road surface and within the corridor). Add a QA render that checks for holes in the road under the path, not only a floater count.

### F4. Per-segment spline and filter processing breaks continuity at boundaries (Critical, C2/C4/C5)

**Finding.** Section 4 says to fit the C2 spline "in the segment frame"; section 5 defines the profile per segment; `grade_ctrl` is filtered over 10-25 m. If each segment is processed independently, the spline tangent and curvature can differ on either side of a 1 km boundary, and the zero-phase grade filter has edge effects at both ends. The app re-bases at exactly those boundaries. The result is a yaw jerk and a trainer grade step every kilometre, precisely the changing-vection and judder defects the comfort research warns about. The magnitude matters: a curvature step of 0.001 1/m at 18 m/s is a 0.32 m/s^2 lateral-acceleration step, and 0.01 1/m is 3.24 m/s^2. At 18 m/s the rider crosses a boundary every 56 seconds.

**Recommended change.** Fit the path spline and compute all filtered profile channels once on the whole session or route in a float64 route frame, then sample and slice into segment files with enough overlap padding (at least 50 m, more than the 10-25 m grade window). Add boundary-continuity checks to QA: position, heading, curvature and `grade_ctrl` across every segment join, with agreed tolerances (for example heading under 0.1 degree, curvature under 1e-3 1/m, grade step under 0.05%). Test the boundary before the first long route by splitting the 1 km field-test track into two 500 m pseudo-segments. The segment files remain the runtime contract; the processing is global.

### F5. The QA gate has no thresholds and cannot catch the failures that matter (Critical, C7)

**Finding.** Every QA check is qualitative: "within a tolerance", "max yaw acceleration", "vertical jitter spectrum", "a flicker metric over a rendered fly-through". No pass/fail numbers are given, so the gate cannot be automated, and "fail loudly" is not implemented. The flicker metric does not say which renderer produces the fly-through; PlayCanvas LOD and sort popping only appear in the runtime renderer, so an offline gsplat render cannot validate the thing the comfort research cares about. Separately, the design says "Non-zero exit and no publish if a gate fails" while `qa/report.html` lives inside the published package: a failed build has no report for Olly to inspect.

**Recommended change.** Give every gate a numeric threshold and a named measurement method. Split deterministic pipeline QA (schema and package integrity, ingest, registration, path, grade, splats, corridor) from runtime QA (headset fly-through at 90/120 Hz, LOD popping, sort latency, stereo, frame-time p99). Gate per block and per segment as well as per route, so one bad 100 m block neither blocks nor hides an 80 km route. For grade, an RTK-derived long-baseline grade from the same track validates smoothing but not absolute accuracy; compare against a digital level or a surveyed profile. Add the 0.2-0.5 Hz band and the yaw/lateral spectrum to the path comfort gate, and add semantic and package checks (s monotonicity, row counts, segment contiguity, change-point ordering, file existence, checksums, joins, loop closure, package size, env.sog quantisation) and a capture-lighting/exposure check. Pin the QA renderer and engine version to the app's, and record the threshold set version. Generate the report to a staging path for every build, pass or fail; publish it inside the package only on pass. Make the runtime test a release gate.

### F6. Registration is not "by construction"; scale, lateral offset and axes are unchecked (Critical, C4/C6)

**Finding.** The design says "Because COLMAP is aligned to the same RTK frame, the path and the splats register by construction." That is true at the frame level and false at the accuracy level. COLMAP alignment has residuals and may include a similarity scale; the POC audit already found the Umeyama scale branch wrong on near-straight, near-flat routes (exactly the field data) and `alignment_max_error 3` too loose for RTK. A 0.1% scale error is 1 m over 1 km and 0.01% is 10 cm; "never rescale after training" makes that a one-way door. The path is derived from the raw antenna track plus a scalar height, which ignores rig lean and steer: a 3 degree lean at 1.2 m moves the road point about 6 cm, and 5 degrees about 10 cm, before smoothing. The track is float for 252 of 1,415 epochs (17.8%), and a plus or minus 12 m quadratic fit over the real track leaves a 39 mm vertical residual, not the 3 cm assumed. The four cameras expose up to 36 ms apart, about 29 cm at 8 m/s, so rigid rig constraints must not be the default. The QA check is "road surface under the path within a tolerance", which is a vertical check: a lateral offset of 0.2 m would pass. The ENU -> SOG/PLY -> PlayCanvas y-up conversion includes a 180 degree z rotation; a sign error would mirror or rotate the scene and could also pass a vertical check.

**Recommended change.** Make the reconstructed rig pose the path source of truth: take a rig reference point from the aligned COLMAP rig, drop it along local gravity onto the reconstructed road surface (a local splat or SfM fit), then smooth. Use the RTK antenna track as the prior and QA reference, and bridge float gaps from the reconstructed surface constrained by fixed epochs either side. Record the SfM alignment method, scale and per-segment residual in the manifest; fix the Umeyama formula and verify the estimated scale against 1 using an independent RTK baseline; use per-image priors at each image's own `t_mid`, not a rigid rig pose. Add QA checks for scale error (for example under 0.1%), lateral and vertical registration (render at path poses and compare road edges or markings to the path corridor; p95 vertical under 3 cm and lateral under 5 cm), and an end-to-end axis check against a surveyed landmark. Make the path-to-splat check 3D, not only vertical. Keep "never rescale after training" as a gate, and add a per-segment rigid correction only when the residual exceeds the budget.

### F7. No route-level index for multi-build routes (Critical, C8)

**Finding.** The package is `<route_id>/<build_id>/route.json`, and different days become separate builds. A route is described as an ordered list of segments with per-segment provenance, but there is no route-level object that joins builds, no route-global `s` frame, no join policy, and no app contract for switching builds mid-ride. The profile's `s` frame is also ambiguous: per-segment files suggest segment-local values, while `route.json`'s `s_range` suggests route-global. Distance markers, the map, the minimap, trainer grade, Strava export and route completion all assume one continuous distance. At 20-80 km and 2 m/s, multi-session is the normal case, not an edge case: a single 80 km pass takes about 11 hours, and 2-3 passes take 22-33 hours. The app cannot load a multi-build route from the design as written. Path continuity and appearance at a join are open risks the design names but does not gate.

**Recommended change.** Add a route-level `route-index.json` (or make `route.json` the route-level object and `build.json` the build object) that lists builds in route order, each build's route-global `s` range, the join segments and join policy (hard cut at a natural break, cross-fade, or a composition build that references segments from other builds), the pinned default build, and per-build QA status. Make `s` route-global with segment offsets in `route.json`. Give every segment its own `build_id`, `session_id`, datum, epoch, geoid, rig-config version and `appearance_set`, so a route can compose segments from different builds. Define the app contract for crossing a join (preload the next build, switch at a rest stop, or blend). Add join QA for position, heading, curvature, elevation and appearance delta, and a route-level QA status. Define which build's path and profile are authoritative at a join. Specify a capture-section plan that makes the joins feasible, for example 10-20 km per day with 2-3 lateral passes and 3-5 km of tie-in overlap at each join, with a capture QA gate for section coverage and overlap. Test a two-build join before scaling.

### F8. 80 km economics and the 1 km measurement plan are not viable for the claims made (Critical, C9/C10/C12)

**Finding.** The design targets 20-80 km routes but the arithmetic is severe: 80 km at the 2 m/s logger ceiling is 11.1 hours per pass; at the field-test median ground speed of 1.286 m/s it is about 17.3 hours per pass, and 2-3 lateral passes are 22-52 hours. The design's "within about an hour" appearance window is only 4.6-7.2 km at these speeds, so a long route is inherently multi-temporal. 1 km produces about 1,250 triggers and 5,000 JPEGs, about 38 GB, so 80 km is about 100,000 triggers, 400,000 images and 3 TB per pass, 6-9 TB for two or three lateral passes. 100 m blocks with 25-50 m overlap each side imply roughly 800-1,600 block trainings, 750-1,000 18 MP images per block, and at one GPU-hour per block 800-1,600 GPU-hours before overlap; at the PCVR density bracket of 40k splats/m a 100 m block is about 4M splats, four times gsplat's 1M MCMC default `cap_max`, so the GPU memory and time per block are not accounted for. The output is 12-96 GB per tier. The 1 km, 100 m and 16 m scales are inherited defaults, not derived: 1 km is not forced by float32 (a 5 km span resolves about 0.6 mm and a 10 km span about 1.2 mm, and the 16 m SOG chunk is the streaming and quantisation unit), 100 m is H3DGS's vehicle-capture size while H3DGS used 50 m for walking captures, and 16 m is splat-transform's default `--lod-chunk-extent`. The design gives the 11 hours but no training throughput, AWS cost, wall-clock, storage or route-build budget. Worse, the only planned real data is field test 2 at about 1 km, which cannot validate 1 km segment boundaries (there is only one segment), 1/2/3 m lateral quality (there is no ground truth off the captured line), or 80 km scaling.

**Recommended change.** Add a build-economics section for a 20 km and an 80 km route: capture hours at the measured pace, raw storage, GPU-hours and GPU memory, AWS cost, wall-clock, output size and the minimum viable route length. State the `cap_max` per block from the density budget and the resulting GPU memory; at 4M+ splats per block, the default MCMC cap is not a safe assumption. Do not treat 80 km as a supported target until the economics are measured. Either make field test 2 2-3 km (enough for one internal segment boundary) or split the 1 km data into three or four 250-400 m pseudo-segments and test joins synthetically; add a synthetic long-route test by repeating the 1 km RTK track through the ECEF/ENU transforms. For lateral quality, capture three lateral passes or lower the corridor claim. The design also requires 2-3 same-day passes "within about an hour" for appearance consistency, but at these speeds three passes over 20 km take about 8 hours and over 80 km about 33 hours; only a segment-major schedule (all passes for a segment inside an hour) can satisfy it, and the design does not specify one. Derive the segment, block and chunk lengths from measured constraints (GPU memory and images per block, load and re-base cost, LOD popping and frame time) rather than inheriting 1 km, 100 m and 16 m.

### F9. "No NVIDIA research code" contradicts PPISP (High, C6)

**Finding.** Section 6 says "No non-commercial code (Inria, CC-NC, NVIDIA research) in the product path" and in the same list specifies "appearance and exposure compensation (PPISP plus EXIF)". PPISP is NVIDIA research (CVPR 2026). The implementation shipped in gsplat is Apache 2.0, but the method's patent status was not checked in the research. The design is simultaneously banning and requiring the same family of work.

**Recommended change.** Reword the rule to "no NVIDIA-research-licensed code; the PPISP implementation shipped in gsplat is Apache 2.0", verify that specific implementation's licence and patent position, and record the fallback (bilateral grid) if it is not cleared. Add a licence review as an explicit open decision before any commercial build.

### F10. Independently trained blocks have no shared reference exposure (High, C6)

**Finding.** Each 100 m block is trained independently with PPISP and EXIF exposure. Trimming cuts geometry, not appearance. Without a common reference exposure and white balance, each block can converge to a slightly different brightness, colour or contrast, producing visible seams exactly where the design promises the overlap makes the cut seamless. The research says to fix the render exposure to one reference across the route; the design does not include that requirement or an `appearance_reference` field. PPISP is a training-time post-process; neither PlayCanvas nor Spark consumes PPISP, so the export must bake a single reference exposure or the compensation is lost or baked inconsistently per block. Intra-pass drift matters too: an 80 km pass takes 11-17 hours, so the light changes within one session, not only between days.

**Recommended change.** Shoot manual exposure and fixed white balance where possible, train or calibrate every block against a shared reference exposure, bake one colour transform into the exported splats, and record the reference in `route.json`. Add a per-boundary colour-delta (`delta-E`) and exposure metric to QA, plus a rendered seam check across every block cut and segment join. For multi-session joins, treat the appearance delta as a first-class gate, not only an "open risk", and add a capture `appearance` block (time of day, weather, sun position) so joins can be matched or rejected.

### F11. The 72 vs 90/120 Hz target is unresolved (High, C1/C12)

**Finding.** The comfort research says a locked 90 Hz minimum, 120 Hz target, with stable frame time as a first-order requirement; 120 fps was a threshold in the cited study. The runtime research suggests starting Quest at 72 Hz. The design measures 72/90/120 but never states which is the shipping floor for the Quest 3 baseline. This choice changes the splat budget, the Quest tier density and the LOD design, so leaving it open leaves the package underspecified. At 18 ms per million, 1M is about 55 fps; proportional estimates are about 0.43M at 72 Hz, 0.34M at 90 Hz and 0.26M at 120 Hz before fixed overhead, and the measured non-splat cost pushes the 90 Hz budget toward 0.25-0.4M.

**Recommended change.** Set the Quest 3 floor at 90 Hz and the target at 120 Hz where the budget allows; budget 0.25-0.4M rendered splats at 90 Hz (0.3-0.5M at 72) and gate on p99 frame time, not the mean. Treat 72 Hz as a debug or degraded mode, not the baseline. Align the QA gate and the bake-off with that target.

### F12. Pre-download assumes unverified browser storage (High, C12)

**Finding.** The design concludes that downloading before riding beats live streaming, which is right for 22-173 Mbit/s at 40 mph. But it never states the storage requirement or tests it: 12-96 GB for an 80 km route, 12 GB even at the low density. At 100 Mbit/s that is 16-128 minutes of download before slower links. Quest Browser storage quotas and eviction are unverified in the research, the manifest has no byte totals or per-chunk sizes, and the app therefore cannot plan prefetch or show a download time. A user with several routes will hit the quota.

**Recommended change.** Run the Quest Browser storage test now, before field data is needed: call `navigator.storage.estimate()`, `persist()`, the Cache API and OPFS, and measure eviction at 5, 15 and 40 GB. Add byte totals per segment, tier and LOD, plus per-chunk sizes and hashes, to `route.json`. Make the segment the caching unit and a rolling cache the baseline, with whole-route pre-download an optimisation. Define a degraded mode (a coarser tier, a shorter route, or live streaming with a hard look-ahead buffer) and a resumable, priority-ordered download. Show the route size and download time in the app, and make the storage requirement part of the manifest and the QA report.

### F13. Corridor 1.0 m overpromises the capture and the measurement plan (High, C1/C5/C9)

**Finding.** The route example sets `corridor_half_width_max_m: 1.0`, and section 9 says steering more than about 1 m off the captured line degrades quickly. Field test 2 is one pass, and the four cameras' viewpoints lie on a line about 0.5 m wide, so 1.0 m is already an extrapolation, not "what was actually captured"; the side cameras face outwards, so a single centre pass cannot validate a 1 m offset. The research says a single pass supports at most about plus or minus 1 m, and a wider road-width corridor needs two or three same-day lateral passes. The design's own measurement plan asks for quality at 1, 2 and 3 m lateral offsets, but a single pass has no ground-truth images at those offsets, so the test cannot be run as written.

**Recommended change.** Make the corridor an output of the QA from the measured captured band, not a hardcoded input. Set the MVP corridor to the validated range, likely plus or minus 0.5 m, and label it in the manifest. Capture dedicated lateral passes for the 1/2/3 m test, or reduce the claim. Add a per-sample corridor quality flag and a soft-limit policy, and make the published corridor a smoothed, conservatively eroded band.

### F14. `grade_ctrl` has no time-based rate limit (High, C5)

**Finding.** FTMS control is request/response with a single pending procedure; the app must serialise writes and can sustain about 1-2 Hz. The design precomputes change points at |delta| >= 0.2% but gives no rate limit, coalescing rule or time-based smoothing. Grade is distance-indexed while trainer feel is time-based: a 10-25 m window is 0.6-5.5 s depending on speed. On the field track, a 21 m smoothed height with a 10 m central difference gives 165 changes over 356 m (0.46 per metre), about 8 per second at 18 m/s, well above the 1-2 Hz queue. Precomputing change points is also arguably the wrong layer: the app must coalesce and serialise FTMS writes anyway, and the 0.2% threshold and 10-25 m window are tuning parameters, so publishing them freezes a retune into every route.

**Recommended change.** Add a time-based component: an effective smoothing window in seconds, or a speed-dependent distance window; a minimum interval between FTMS writes at typical speeds; and a coalescing rule (keep only the latest pending grade). Precompute change points that respect the write-rate budget, or publish the primitives (height, grade, uncertainty) plus a canonical physics grade and keep the trainer filter in an app module with recorded defaults. State the maximum write rate and the coalescing rule in the package. QA the grade step size against speed, and add a `trainer_response_s` hint once the KICKR CORE 2 is measured.

### F15. Path smoothing is a deviation bound, not a frequency requirement (High, C4)

**Finding.** The design fits a smoothing spline with "max deviation from the raw trace under about 0.1 m". The comfort research requires removing yaw content above about 0.1 Hz that is not road curvature, and rider weave and steering oscillation are roughly 0.5-2 Hz. A spline that stays within 0.1 m of the raw trace can retain a small-amplitude weave: 0.1 m of lateral weave at 0.5 Hz is a yaw rate of about 9 deg/s at 2 m/s, which is a comfort defect. The design also states cutoffs in time units at an unstated speed: a 0.5 Hz vertical cutoff at 2 m/s is a 4 m wavelength, which at 18 m/s is 4.5 Hz, inside the 1.8-7.4 Hz band that doubled SSQ in Palmisano 2007. To keep the vertical spectrum below 0.5 Hz at 40 mph, the spatial cutoff must be about 36 m. Yaw acceleration scales as A(2*pi*f)^3/v: a 4.2 mm weave at 1 Hz and 2 m/s already gives 30 deg/s^2, and a 5 cm weave gives about 355 deg/s^2. The 0.1 m deviation and 30 deg/s^2 budgets are not independent. The QA checks max yaw acceleration but not the spectrum.

**Recommended change.** Specify the smoothing in the frequency domain and in wavelength, not only in time: attenuate lateral and yaw content above about 0.5 Hz, avoid a peak near 0.2 Hz, filter vertical by wavelength or at the maximum speed, and keep the deviation bound as a separate registration constraint. Define the objective in curvature and yaw-acceleration space. Add a yaw-rate, lateral and vertical spectrum check to QA, covering the 0.2-0.5 Hz band. Validate on the field test 1 track with synthetic weave, and measure the real weave spectrum on field test 2 before fixing the 0.1 m and 30 deg/s^2 numbers.

### F16. The antenna-to-road path assumes a vertical lever arm (High, C4/C9)

**Finding.** The path is "Hermite-interpolated antenna positions minus the measured antenna height above the road", which assumes the antenna is vertically above the road point under the bike. A handlebar-mounted antenna swings with steering and leans with the bike: at the likely 1.2 m antenna height, 3 degrees of lean moves the road point about 6 cm and 5 degrees about 10 cm, which uses the design's 0.1 m path budget before smoothing; a 10 degree lean at 1 m is about 17 cm, and steering adds more. The four cameras also expose up to 36 ms apart, so a single rig pose per trigger is itself approximate. The road-surface trace can therefore be offset by 0.1-0.2 m in turns, exactly where path quality matters. The QA's "road surface under the path" check is vertical and would not catch it.

**Recommended change.** Use the aligned rig pose, not individual camera centres, as the path source of truth: take a rig reference point from the aligned COLMAP rig, drop it along local gravity onto the reconstructed road surface, then smooth; use the RTK antenna track as the prior and QA reference, with per-image priors at each image's own `t_mid`. If the antenna track remains the source, measure the antenna-to-contact-patch lever arm in the rig frame and model lean and steer. Add a lateral path check that compares the path to the reconstructed road centre or lane markings, not only to the road height.

### F17. Engine reversibility is overclaimed (High, C11)

**Finding.** The design says a Spark `.RAD` package can be generated from the same masters, so the engine choice stays reversible. That is true at the master level, but the published manifest is `format: "sog-streamed"` with PlayCanvas `lod-meta.json`; Spark reads SOG but not the Streamed SOG LOD tree, and its RAD LOD semantics and prefetch model differ. The app's asset descriptor and prefetch logic are PlayCanvas-shaped. The tier descriptor also lacks `design_frame_rate_hz`, `design_splat_budget`, the PCVR budget, the SOG version and `opacity_linear` flag, a behind-view LOD floor, and an engine/format compatibility pin. The Quest tier has no written quality bar (5k splats/m and SH1 at 1-3 m lateral offsets).

**Recommended change.** Either make the asset descriptor engine-neutral (a format field plus a generic LOD tree and chunk list) or accept that switching engines is a schema version bump and an app change, and say so. Make the tier descriptor `{id, renderer, format, url, design_frame_rate_hz, splat_budget, sh_degree, sog_version, opacity_linear, behind_view_lod}`, add the PCVR budget and an engine/format compatibility pin, and write the Quest quality bar before the MVP. Build both Streamed SOG and `.RAD` (or a canonical glTF payload) from the same masters before freezing the schema. Do the bake-off before freezing the package schema.

### F18. Per-segment `env.sog` is over-engineered (Medium, C3/C6)

**Finding.** The sky and far field are a global or build-level layer; putting `env.sog` under every 1 km segment multiplies assets and can change or pop at boundaries. PlayCanvas's environment slot is loaded once per asset.

**Recommended change.** Make the environment a route-level or build-level asset, or define exactly why local far-field is needed and how the transition is crossfaded. If it stays per segment, add an env seam check.

### F19. No realism or reconstruction-quality gate (Medium, C7)

**Finding.** The product's selling point is ultra-realism, but the QA gate checks counts, floaters and flicker, not whether the reconstruction looks right. A route can pass with blurry road texture or ghosted geometry.

**Recommended change.** Add a held-out quality metric (PSNR, LPIPS, or a structured perceptual review) per segment and tier, with thresholds; use the lateral views where available. Make it a publish gate.

### F20. The SfM/training/master frame and its precision are unspecified (Medium, C2/C6)

**Finding.** The design specifies per-segment local output frames, but not the frame in which COLMAP and gsplat train, or the frame of the float32 PLY masters. If training is in a route-global ENU frame, float32 splat centres at 40-80 km have 4-8 mm quantisation before the per-segment output transform, which cannot recover it. The design's precision rationale applies to the runtime but not to training.

**Recommended change.** Train each block in a local frame, or store masters in float64 or local frames with transforms recorded. Add a round-trip transform check to QA.

### F21. "Datum: ETRS89" plus ECEF is not a frame realisation (Medium, C2)

**Finding.** ETRS89 is a coordinate reference system, not a frame realisation; ECEF coordinates need something like ETRF2000 or ETRF2014 and an epoch. The design says "ECEF float64" without that. Multi-session joining and reprocessing across years depend on it.

**Recommended change.** Add `frame` and `epoch` to the manifest (for example ETRF2000, epoch 1989.0, or whatever the Point One `LOCAL` mountpoint realises), and document the ETRS89 to WGS84 transform used for export.

### F22. Strava/FIT distance should use `s_h` (Medium, C5)

**Finding.** The profile has both 3D `s` and horizontal `s_h`. Strava computes distance from lat/lon, which is horizontal. At 16% grade the difference is 1.3%, about 1 km on an 80 km route. The design does not say which distance the FIT/Strava export uses.

**Recommended change.** Use `s_h` for FIT/Strava distance and `s` for odometry, and document the choice. Convert to WGS84 for export or state that the under-1 m ETRS89 difference is accepted.

### F23. Schema ambiguities (Medium, C3)

**Finding.** `s` in `path.json` and `profile.json` is not stated to be route-global or segment-local; `origin.ecef` units are not stated; the profile and path schema versions are not shown; `splats_by_lod: []` is empty; `bounds_local` duplicates the LOD tree's spatial information; `corridor_half_width_max_m` duplicates the per-sample corridor widths.

**Recommended change.** State the frame and units of every field, show the profile and path schema headers, fill or remove `splats_by_lod`, and document the precedence of the redundant fields. Add a schema test that validates a generated package against the JSON Schemas.

### F24. Per-sample `surface` is manual for 20-80 km routes (Medium, C5)

**Finding.** Surface class drives Crr and is per sample, but the design says "manual tag for now". Tagging 20-80 km by hand is laborious and error-prone, and mixed surfaces are common.

**Recommended change.** Make the MVP field an interval list (or a per-sample enum with a default) with a documented manual workflow; plan automatic classification later. QA the coverage.

### F25. "Splat coverage weak" and "captured lateral band" are undefined (Medium, C5/C7)

**Finding.** The `quality` flag includes "splat coverage weak" and the corridor QA compares the captured lateral band to `corridor_*_m`, but neither computation is defined.

**Recommended change.** Define coverage (for example, visible splat count or depth confidence in a cone around the path) and the lateral-band measurement (render or visibility analysis from the path), with thresholds. Use them to set the corridor widths.

### F26. The QA report cannot be inspected for a failed build (Medium, C3/C7)

**Finding.** `qa/report.html` is inside the published package, but the design says no publish on gate failure. A failed build therefore has no report where Olly can read it.

**Recommended change.** Write the QA report to a staging location for every build, publish it with the package only on pass, and keep failed reports in the build record.

### F27. No end-to-end axis-convention check (Medium, C2)

**Finding.** The transform chain is ENU z-up -> SOG/PLY frame -> PlayCanvas y-up with a 180 degree z rotation. A sign or 180 degree error is a classic failure and could pass a vertical road check.

**Recommended change.** Add a surveyed-landmark test: render at a known pose, verify the landmark's pixel position and the rider's forward direction, and include it in QA.

### F28-F31. Lower-severity observations

- **F28 (Low).** Re-basing at segment boundaries can hitch if the camera and all loaded assets are not transformed atomically. Specify the re-base operation and test at 20 m/s across a boundary.
- **F29 (Low).** "All three research topics reached the same conclusion independently" overstates the record; the comfort research does not analyse coordinate frames. Say "the coordinate-frame analysis and the runtime/tiling research agree."
- **F30 (Low).** `grade_ctrl` is quantised to 0.1% while FTMS accepts 0.01%; either preserve 0.01% in the profile and let the app quantise, or document why 0.1% is enough.
- **F31 (Low).** Loop closure and start/end elevation mismatch are not specified beyond `loop: false`. Add the start/end delta or document that the app handles it.

### F32-F36. Additional observations

- **F32 (Medium, 4 of 8).** Arclength block ownership can unload a splat that is still visible, and it slices 3D objects. On a hairpin, switchback, out-and-back or loop, two parts of the route are spatially close but far apart in route distance. A splat assigned to block A by nearest arclength can be visible from block B; if the app loads and unloads by route distance only, the splat disappears while the rider is on the other arm, or the shared scenery is loaded twice and paid for twice in the global budget. The same 1D cut slices trees, walls and hedges at a plane perpendicular to the route, and the two sides are trained with different appearance. H3DGS assigns each Gaussian to the nearest chunk in 3D, not by route arclength. The field sample's closest non-adjacent points are 40.8 m apart, so it contains no hairpin and the MVP may not catch this. The overlap arithmetic is also unresolved: "25-50 m overlap each side" of a 100 m block gives a 50 m or zero ownership interval, and `cap_max` must be target density times the training extent, not the owned length. Recommended change: use direction-aware 3D ownership (nearest block in 3D, tie-break by route direction) or explicitly detect self-approach and assign the shared road to one block; prefetch by spatial visibility with route distance as priority; add a duplicate-splat/spatial-overlap QA gate and hairpin, out-and-back and loop fixtures; add `loop_closure`/`self_overlap` metadata and a `reversible` flag; define `cap_max` from the training extent.
- **F33 (Medium).** The 2 m/s capture cap is a logger configuration choice, not a physical limit. The 0.8 m spacing and 0.4 s minimum interval are settings; at 4 m/s with 1.6 m spacing the 80 km capture falls from 11.1 to 5.6 hours. The 0.8 m spacing may be needed for overlap, but that is testable. Recommended change: test 1.2-1.6 m spacing at 4-6 m/s on field test 2; if reconstruction holds, the product's route economics change substantially.
- **F34 (Medium, 2 of 8).** The rider's eye position is not tied to the capture or to the bike. The app adds a fixed seated eye height to the path, but the splats were captured from the rig's camera height, and the path is the antenna/handlebar line. A 0.2-0.4 m vertical offset changes the view and can expose reconstruction artifacts, and a height-only offset puts the virtual eye at the handlebar, not over the rider's body; the HMD's tracked translation then starts from the wrong origin. Recommended change: state the capture camera height and the target eye height, match them where possible, use a 3D rider-reference offset (forward/back and lateral) from the bar reference to the seated eye, and add a QA render at the target eye position.
- **F35 (Medium).** The rig config is underspecified. The design says it must include antenna height above the road and its offset from each camera, but the path, the lever arm and the splat registration also need camera roles, per-camera extrinsics, the antenna phase-centre offset and the full antenna-to-contact-patch lever arm. The handoff lists these. Recommended change: define and version a complete rig config schema, and make its completeness a QA gate.
- **F36 (Low).** Hard-shadow or changing-light sessions are not flagged. The design says overcast is strongly preferred and that no appearance model removes hard shadows, but ingest and QA do not check lighting. Recommended change: add a session lighting/shadow flag and a QA check (metadata or a simple shadow-contrast metric), and reject or flag risky sessions before training.

### F37. The rig has no rear-facing camera, but the product is head-tracked VR (High, C1/C9)

**Finding.** The planned rig is two forward-facing cameras (one up, one down) and two side cameras angled 30-45 degrees forward; no camera points behind the rider. Four 18 mm lenses on APS-C cover about 64x45 degrees each and cannot cover 360 degrees, and there is no field-of-view, overlap or coverage analysis in the design. The product is WebXR with tracked head pose, and the design's consumer table says "as many modern headsets as possible". The runtime research assumes "behind-the-rider detail can be very coarse", which is not the same as absent, and the comfort research says head turns must not show holes. The design has no view-sphere coverage check and `env.sog` is sky and far field, not a rear reconstruction. The QA is a forward fly-through only, so it never looks down, sideways or back. The later "other riders" feature would put riders behind the rider, exactly where there is no capture.

**Recommended change.** Decide the rear-view policy now, while the rig is unbuilt: add a rear-facing camera (a fifth camera, or reposition the side cameras), or explicitly constrain the experience to forward gaze with a cockpit rest frame and test head turns. Add a QA coverage check over yaw bins around the rider path, including the rear sector, and include it in the corridor/coverage report. Add downward (minus 60 to minus 90 degrees pitch), 90 degree left/right and 180 degree yaw fly-throughs to QA, and specify a behind-view LOD floor.

### F38. Provenance is too thin, schema compatibility is undefined, and publication is not atomic (High, C3/C10)

**Finding.** The manifest's provenance is `{sessions, pipeline_commit}` per segment. It omits the rig config hash and calibration, the COLMAP version and config, the gsplat version and config (seed, `cap_max`, MCMC, antialiased, PPISP, masks, depth prior), the `splat-transform` version and options, the path-spline parameters, the grade smoothing windows, the QA thresholds and tool versions, the source image manifest and per-block camera lists, the datum realisation and epoch, the geoid model version, and the licence/patent flags. Without those, a retained master cannot be reproduced or retrained, and a QA failure cannot be traced to a config change. The design says "retained masters for reprocessing", but field test 1's 432 JPEGs were deleted; without raw images or at least the exact per-block source images, the masters are not retrainable. The design also says every file carries a schema version and the app validates against JSON Schemas, but it defines no compatibility policy: which reader versions are supported, how long, how packages are migrated, and what happens when the contract-shaping measurements in open decisions 2-4 change the schema. Publication is not atomic either: many files per build, no content hashes, no upload order or ready marker, no immutable pointer, so a partial upload or CDN race can leave `route.json` referencing missing or stale chunks.

**Recommended change.** Add a `build.json` or master manifest with a full reproducibility record and a SHA-256 per file: tool versions and environment, training config and seed, masks and priors, appearance model and reference exposure, path/grade smoothing parameters, rig config hash, calibration, datum realisation and geoid model, QA policy and thresholds, the source image manifest, per-block camera lists, and licence/patent flags. Retain raw images or explicitly declare the masters not retrainable. Define a schema compatibility policy (major-version support window, `min_reader_version`, migration tooling) and keep the contract at v0.x until field test 2 and the engine bake-off are done. Content-address chunks, write `route.json` last or publish a signed pointer, make builds immutable, and add package-integrity QA (all referenced files present, hashes match, schema valid, LOD metadata consistent, no duplicate splat ownership).

### F39. RTK float gaps and the ellipsoidal/orthometric grade distinction have no policy (High, C4/C5)

**Finding.** Field test 1 has 1,415 PVT epochs: 1,160 fixed, 252 float (17.8%) and 3 no-fix, in eight non-fixed runs. The longest run is 103 epochs, 20.6 s, about 27 m at 1.29 m/s (about 41 m at the 2 m/s capture ceiling). Float `vAcc` is median 38 mm, p90 111 mm, max 150 mm, against fixed median 13 mm and p90 22 mm. The longest gap exceeds the design's 10-25 m `grade_ctrl` smoothing window, so the smoothed grade inside it cannot be recovered; the design's "float sections get flagged and smoothed harder" has no gap policy, no minimum fixed fraction, no maximum uninterrupted float run, and no app fallback. The design's 3 cm vertical-noise figure is the fixed case; at 10 m in float, grade noise is about 0.54-2.1%, 5-20 times the 0.1% quantisation, and comparable to the 0.2% change-point threshold. Separately, the design says grade and elevation come from "geodetic height" while displayed elevation is orthometric, but does not say which height `grade_ctrl` uses. The geoid slope is negligible over a 10-25 m window (field test 1: geoid offset 50.421 m, range 4 mm over 356 m, about 0.001%), but over 80 km the integrated grade and the orthometric elevation gain differ by the geoid undulation change, and `sigma_h` from `vAcc` is ellipsoidal.

**Recommended change.** Add per-segment and per-block RTK gates (maximum uninterrupted non-fixed run, for example 2 s or 10 m; minimum fixed fraction, for example 95%) and require PPK from `receiver.ubx` plus `rtcm.bin`, or reject the segment. Add `grade_confidence`, `path_confidence` and `max_float_run_s` to the profile and manifest, and define the app fallback (hold the last good grade, use a longer window, or reduce trainer authority). Add `grade_height` (ellipsoidal or orthometric) and `grade_run` (horizontal or along-surface) to the schema; compute `grade_ctrl` from orthometric height or state the negligible difference; add `sigma_h_ortho` and the geoid model error. Test a tree-covered or tunnel section on field test 2.

### F40. The consumer contract has no enforcement semantics (High, C1/C5)

**Finding.** The consumer table lists what the pipeline publishes, not what the app must do with it, and the cheapest reasonable app implementation will defeat several fields. The corridor is the clearest case: `corridor_left_m` and `corridor_right_m` are per-sample values generated from a capture that weaves; the reference frame (rider path or road centreline), smoothing, soft-limit policy and `corridor_quality` are not stated. A band that pinches from 1.0 m to 0.3 m at one sample will cause a hard stop or a lateral jump, and the comfort research explicitly requires soft limits. `comfort_speed_mps` has no enforcement ramp and no `max_entry_speed` derived from a backward pass; a downhill sprint into a bend is either allowed at a high yaw rate or capped by a step, both sickness risks. `grade_ctrl_changes` has no rate or coalescing contract: on the field track a 21 m smoothed height with a 10 m central difference gives 165 changes over 356 m (0.46 per metre), about 8 per second at 18 m/s, against an FTMS queue that must run at 1-2 Hz. The design also does not say whether the app's own physics uses `grade_ctrl` (clamped by the trainer at -10/+16 percent) or the visual grade, so the rider can descend slower than the scene suggests. `surface` has no enum, units or default; the `quality` flags have no severity or required behaviour. The path is the antenna/handlebar line, but the app needs a seated eye origin: a height-only offset puts the virtual eye at the handlebar, not over the rider's body, and the HMD's tracked translation then starts from the wrong origin. There is no start pose or heading, no loop-closure rule, no `appearance` block (time of day, weather, sun position), no basemap/offline tile policy, no multiplayer `(s, lateral)` coordinate, and no FIT/Strava export contract (timestamp and lat/lon per record, `sub_sport` 58, 1 Hz cadence, elevation policy).

**Recommended change.** Add a `runtime_policy` block per route: the corridor reference frame, maximum lateral rate, soft-limit deceleration, comfort-speed enforcement ramp and lateral/yaw budgets, the FTMS maximum write rate and coalescing rule, `trainer_response_s`, which grade drives physics (`grade_physics`), the surface enum and default Crr, and the quality-flag semantics and severity. Make `corridor_*` a smoothed, conservatively eroded band with `corridor_quality`. Publish the comfort budgets used and add `max_entry_speed`, a stop/start policy, a start pose and a loop-closure flag. Add an explicit 3D seated-eye/rider-reference offset. Add an `appearance` block, a basemap/offline tile policy, a canonical multiplayer coordinate, and a FIT/Strava export section. Add the legal/patent review to the open decisions.

## 3. Steelman: the strongest version of the design

*The position:* the pipeline should publish a versioned, route-manifested package of segment-local ENU data, a smooth RTK-derived rider path, a distance-indexed profile with two grade channels, and tiered splat assets, gated by QA.

1. Long routes genuinely break a single ENU origin; per-segment frames anchored in ECEF fix the physics.
2. The reference path comes from the RTK antenna, not interleaved camera centres, removing rig wobble.
3. Separate geometric and control grade channels match exactly what the trainer and the visuals need.
4. Overlapping training blocks with arclength ownership follow the strongest published large-scene splat designs.
5. gsplat with MCMC, antialiasing and per-image appearance is the commercially usable, evidence-backed trainer.
6. A versioned manifest plus a publish gate turns "fail loudly" into an enforceable contract.
7. Retaining lossless masters and a format-neutral plan keeps the renderer decision reversible.
8. Honest **Measure** labels make the proposal falsifiable instead of falsely precise.

## 4. Counter-argument: the strongest rebuttal

1. The design is a full contract for an app that does not exist, grounded in one camera.
2. Its primary renderer cannot demonstrate the continuous LOD transitions its comfort evidence requires.
3. The Quest budget upper bound contradicts the 18 ms per million splat measurement it cites.
4. Removing floaters within 1.5-2 m of the eye trajectory would delete the road surface.
5. Per-segment splines and filters create heading, curvature and grade discontinuities every kilometre.
6. The QA gate has no numeric thresholds and cannot test runtime popping offline.
7. Path and splats do not register "by construction": scale, lateral offset and axes are unchecked.
8. An 80 km route costs 11 hours and 3 TB per pass, over 800 blocks, with no economic model.

## 5. Direct evidence checks performed

I checked the field-test 1 sample and the arithmetic rather than trusting the design's summaries.

- **Field-test counts and matching.** The session has 1,415 PVT rows, 434 pulses and 432 photos. `carrSoln` is 1,160 fixed, 252 float and 3 none, with `fixType` 3 everywhere. The 432 photos are 1 warm-up photo plus 431 capture photos; capture pulse 106 is the one missing. This matches the handoff.
- **Photo spacing and track.** Matching photos to non-blank `cam0_delay_us` and converting TM2 GPS time with the correct GPS epoch gives a median spacing of 0.809 m (mean 0.816 m, p5 0.76, p95 0.88, max 1.946 m) and a photo-path distance of 351.0 m over 279.1 s. The full PVT track is 356.1 m over 282.8 s. The handoff's "356 m walk" and "0.81 m median" are correct. A first attempt without the GPS epoch conversion produced nonsense; the corrected check is the one above.
- **EXIF clock.** The EXIF-to-GPS offset is -54.44 minutes (sd 0.27 s), consistent with the handoff's "about 54 minutes off". EXIF intervals track TM2 intervals to a median difference of about 0 ms (sd 345 ms), so the matching rule is sound on this sample.
- **Fix quality.** `hAcc` median 14 mm, p95 64 mm; `vAcc` median 14 mm, p95 50 mm. The design's "3 cm vertical noise" is a reasonable working figure, conservative at the median and optimistic at p95; the per-sample `sigma_h` should be used rather than a single constant.
- **Arithmetic.** Frame budgets are 13.89/11.11/8.33 ms at 72/90/120 Hz. At 18 ms per million splats, the affordable counts before other costs are 0.77/0.62/0.46M. 1M at 1M is 18 ms, so 72 Hz cannot hold it. Earth drop at 40 km is 125.6 m; grade bias 0.63%; rotation 0.00899 degrees per km; float32 ulp 0.061 mm at 600 m, 3.9 mm at 40 km, 7.8 mm at 80 km. 80 km at 2 m/s is 11.1 hours; 1,250 triggers/km times 4 cameras times 7.6 MB is 38 GB/km, 3.04 TB for 80 km. At 15 B/splat and 2x LOD, 5k/15k/40k splats per metre give 21.6/64.8/172.8 Mbit/s at 18 m/s and 12/36/96 GB for 80 km. The design's arithmetic is correct; the error is in how the Quest budget is stated and in what is not modelled.
### Panel checks that changed the evidence base

The eight agents independently added the following checks, which are reflected in the findings above:

- **Field-test RTK and grade.** 252 of 1,415 epochs are float (17.8%), 3 are no-fix, in eight non-fixed runs; the longest is 103 epochs, 20.6 s, about 27 m at 1.29 m/s. Float `vAcc` is median 38 mm, p90 111 mm, max 150 mm, against fixed median 13 mm and p90 22 mm. At 10 m in float, grade noise is about 0.54-2.1%, 5-20 times the 0.1% quantisation. A plus or minus 12 m quadratic fit over the real track leaves a 39 mm vertical residual, not the 3 cm assumed. The geoid offset is 50.421 m with a 4 mm range over 356 m, about 0.001% slope, so the geoid slope is negligible over a grade window but not over an 80 km integrated gain.
- **Change-point density.** A 21 m smoothed height with a 10 m central difference gives 165 changes over 356 m (0.46 per metre), about 8 per second at 18 m/s, against the FTMS 1-2 Hz queue.
- **Path and registration.** A 0.1% scale error is 1 m over 1 km and 0.01% is 10 cm; "never rescale after training" makes that a one-way door. A 3 degree lean at 1.2 m antenna height moves the road point about 6 cm sideways and 5 degrees about 10 cm, using the design's 0.1 m path budget before smoothing. The four cameras expose up to 36 ms apart, about 29 cm at 8 m/s.
- **Geometry at boundaries.** A curvature step of 0.001 1/m at 18 m/s is a 0.32 m/s^2 lateral-acceleration step; 0.01 1/m is 3.24 m/s^2. Adjacent 1 km ENU frames rotate by 0.009 degrees; a translation-only or pure-yaw approximation leaves about 7.9 cm at 500 m from the origin.
- **Quest frame cost.** The 1M budget ran at 30.7 fps quality (32.6 ms) and 40.3 fps performance (24.8 ms), implying 6.8-14.6 ms of non-splat cost. At 18 ms per million, 1M is 18 ms, about 55 fps; proportional scaling gives about 0.43M at 72 Hz, 0.34M at 90 Hz and 0.26M at 120 Hz before fixed overhead. The design's 0.3-1M range mixes the 72 Hz estimate with the vendor 1M ceiling.
- **Capture scale.** The field-test median ground speed is 1.286 m/s, so 80 km is about 17.3 hours at the measured pace, not 11.1 hours at the 2 m/s ceiling; 2-3 passes are 35-52 hours. The design's "within about an hour" merge window is 4.6-7.2 km at these speeds. 80 km is about 100,000 triggers, 400,000 images and 3.04 TB per pass, 6-9 TB for 2-3 passes; 100 m blocks imply about 800-1,600 block trainings, 750-1,000 18 MP images per block, and 4M splats per block at the 40k/m PCVR bracket, four times gsplat's default MCMC `cap_max`. At one GPU-hour per block, 800-1,600 blocks is 800-1,600 GPU-hours before overhead.
- **Length scales.** 1 km is not forced by float32: a 5 km span resolves about 0.6 mm and a 10 km span about 1.2 mm, and the 16 m SOG chunk, not the segment, is the streaming and quantisation unit. 100 m is H3DGS's vehicle-capture size; H3DGS used 50 m for walking captures, which is closer to this rig. 16 m is splat-transform's default `--lod-chunk-extent`, and at 18 m/s it switches LOD every about 0.9 s. Cutting field test 2 into three or four 250-400 m pseudo-segments would exercise joins and re-basing.
- **Self-approach.** The field sample's closest non-adjacent points are 40.8 m apart, so it contains no hairpin; hairpin, out-and-back and loop fixtures are needed. H3DGS assigns Gaussians to the nearest chunk in 3D, not by route arclength.
- **Storage.** 12-96 GB per 80 km route is 16-128 minutes at 100 Mbit/s. The Quest test should call `navigator.storage.estimate()`, `persist()`, the Cache API and OPFS, and measure eviction at 5, 15 and 40 GB.
- **Schema details.** The `route.json` example gives `origin.lat` and `origin.lon` to 5 decimals (about 1.1 m), while the profile claims 1e-7 degrees (about 1 cm); the ECEF anchor should be authoritative or the degrees stored to more places. `splats_by_lod` is empty, there are no byte totals or `overview` reference, and there is no `ecef_to_local` matrix or `axis_convention` field.
- **Capture light.** Field test 1 was shot at dusk at ISO 1000-2000; a sunny or dusk capture can pass the current ingest gate while making appearance seams unfixable.
- **80 km geometry.** If the origin is at one end of an 80 km route, the far end is 502 m below the tangent plane and the up bias is 1.26%; the design's 126 m and 0.6% are the half-length case with the origin at the middle.

- **What I could not verify.** No published Quest 3 frame-time measurement exists for either web renderer beyond the PlayCanvas PR; the design's splat density, block length, segment length, lateral quality and storage quota are all unmeasured. The PPISP licence and patent position, the OSGM15 coverage outside Great Britain, and the Quest Browser storage quota are unverified.

## 6. Recommended sequence

Before the schema is frozen:

1. Run a thin vertical spike first: 100-200 m in good daylight with the 4-camera rig, one reconstruction, two or three overlapping blocks, both Streamed SOG and `.RAD`, and a Quest 3 test. This answers the seam, density, frame-time and format questions with real data before the contract is written around guesses.
2. Resolve F1, F2 and F11: set the Quest budget from the measurement, set the 90 Hz floor, and make the LOD-transition test a pass/fail gate. These decide the tier design and the renderer.
3. Fix F3, F4 and F6: define the floater cull by defect class, move path/profile filtering to a global pass with boundary QA, and add registration scale, lateral and axis checks. Use the aligned rig pose as the path source of truth, not the raw antenna line.
4. Fix F5, F7 and F8: give every gate a numeric threshold and a runtime variant, add the route-level index and join policy, and add the build economics and a field-test-2 plan that exercises joins and lateral offsets.
5. Fix F9, F10, F12-F17 and F37: licence review, reference exposure and seam colour, storage test and rolling cache, corridor measurement, grade rate limit, frequency-domain path smoothing, rig attitude and eye origin, engine-neutral descriptor, and the rear-view policy.
6. Fix F38-F40: full provenance and atomic publication, RTK gap and grade-height policy, and the consumer enforcement semantics (`runtime_policy`, `appearance`, export contract).
7. Keep the rest of the design: the segment frames, the RTK-prior reference path, the two grade channels, the overlapping-block training structure, gsplat, the masters, and the publish-gate concept.

## 7. What should survive unchanged

The coordinate-frame architecture, the explicit datum and geoid, the RTK-prior reference path, the separation of geometric and control grade, the distance-indexed profile fields, the overlapping-block training design (with 3D ownership instead of pure arclength trimming), the gsplat choice, the lossless masters, and the principle of a fail-loudly publish gate. These are the parts of the design that the evidence supports; the challenge is to make the contract around them match what has actually been measured.
