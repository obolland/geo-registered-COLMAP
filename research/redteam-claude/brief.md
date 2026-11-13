# Red team brief (shared by all 8 panel agents)

ARGUMENT UNDER TEST: /Users/olly/geo-registered-COLMAP/docs/OUTPUT-DESIGN.md (read it in full).
Evidence: /Users/olly/geo-registered-COLMAP/research/{STATUS,runtime-rendering-and-headsets,tiling-lod-and-training,trainer-and-sensors,comfort-and-sickness}.md. Capture contract: /Users/olly/gps-camera-trigger/docs/PIPELINE-HANDOFF.md. Read what you need; check claims rather than trusting labels.

Product: web VR cycling (WebXR, many headsets, Quest 3 baseline), app not built yet. Ultra-realism is the selling point, motion sickness the biggest risk, frame rate as high as possible. Routes 20-80 km (MVP 1 km), 10-20 mph typical, 40 mph downhill. Rider on the captured line, optional steering riser. Map, distance markers, later other riders, Strava export. Wide smart-trainer support (dev: KICKR CORE 2). Training on AWS GPU. Next data: field test 2, 4 cameras, about 1 km.

CENTRAL THESIS: The pipeline should publish a versioned route package of ~1 km segments, each in its own ECEF-anchored local ENU frame, carrying a smoothed level rider path, a 1 m distance profile (two grade channels, comfort and corridor data), gsplat-trained splats packaged as PlayCanvas Streamed SOG in Quest and PCVR tiers plus an environment layer, gated by a QA report.

ATOMIC CLAIMS:
C1 Per-segment (~1 km) local ENU frames anchored in float64 ECEF, with a runtime floating origin, are the right coordinate design; grade from each sample's own geodetic height.
C2 Package layout (route.json manifest, overview polyline, per-segment profile/path/quest/pcvr/env, unpublished lossless masters) keeps the renderer choice reversible and serves every consumer.
C3 The rider path should come from the RTK antenna track moved to the road surface, smoothed to a C2 spline within ~0.1 m, with no pitch, roll or bob, heading from the tangent; it registers with the splats by construction.
C4 A 1 m along-surface profile with lat/lon, orthometric height, grade_geom and an unclamped smoothed quantised grade_ctrl plus change points, curvature, comfort speed, corridor widths, surface class and quality flags is what the trainer, map and comfort systems need; device clamps and difficulty belong in the app.
C5 Splats: gsplat (not Splatfacto) in ~100 m blocks with 25-50 m overlap, trimmed to ownership intervals, near-path floater removal, splat-transform LOD with ~16 m chunks, Quest tier capped near 5k splats/m, PCVR full tier, separate env layer.
C6 A per-route QA report with hard gates (ingest, registration, path comfort, grade, splat density, floaters, flicker, corridor) should block publishing.
C7 Multi-session routes need only per-segment provenance and the shared earth anchor; merge passes only within a session.
C8 PlayCanvas Streamed SOG on WebGL2 is the right primary target, with numbers left to measurement on the 1 km MVP.
C9 Capture implications: ~2 m/s ceiling, 2-3 lateral passes for a wider corridor, overcast light, antenna height above road in rig config.

HIDDEN ASSUMPTIONS TO PROBE: the reconstruction actually registers to the RTK frame at cm level; the pipeline (not the app) should own comfort shaping; a route can be downloaded before riding (headset storage); segments, training blocks and LOD chunks nest cleanly; field test 2 (1 km, walking speed) can validate decisions meant for 80 km at 40 mph; manual capture of 80 km routes is economically viable at 2 m/s; JSON is adequate.

OUTPUT: Write your analysis to /Users/olly/geo-registered-COLMAP/research/redteam-claude/<YOUR_ID>.md in exactly this structure, then return the same text as your final message. Do not modify other files, do not commit. Never use the em dash; use a hyphen.

**<ID> ANALYSIS:**
**Strongest Point FOR the Argument:** [Claim #] 2-3 sentences. Take seriously because: 1 sentence.
**Strongest Point AGAINST the Argument:** [Claim #] 2-3 sentences. Problematic because: 1 sentence.
**Further weaknesses (up to 3):** each with claim #, the flaw in 1-2 sentences, and a concrete recommended change.
**Overall Assessment:** one sentence.

Be intellectually honest: real strengths, real weaknesses, no nitpicks. Cite file/section or a primary source for factual claims.
