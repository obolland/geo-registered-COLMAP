# Plan: multi-session reconstruction (slice 9)

Status: proposed 2026-11-19. Why: Olly's MVP decision (research/STATUS.md, "MVP capture decision") to capture the route in both directions and in overlapping stages, with re-aimed cameras and possibly an iPhone pointed at the road. Overlapping passes are reconstructed together so each patch of road is seen from both directions (this revises OUTPUT-DESIGN section 8, which only joined sessions at their ends). Evidence for the need: research/reviews/slice8-geometry-investigation.md (the near road is never seen from closer than about 3 m; road views are all grazing, from one direction).

## Principle: an extension, not a new requirement (Olly, 2026-11-19)

One rig and one pass stays a fully supported case: a single session is simply N = 1. Every existing command keeps working unchanged with one images.json, and the extra sessions and auxiliary cameras are optional inputs. Nothing assumes multiple passes. Image names gain a session prefix only when a workspace holds more than one session, so single-session workspaces, priors and outputs are unchanged. A regression check proves it: field test 2 session 1 run through the new code gives the same prepared workspace (images, priors, cameras) as today.

## Steps (each test-first, then an independent review)

1. **Common frame.** A workspace frame (lat, lon, h, datum) chosen once per build, by default the first session's frame. Each session's images (antenna ECEF, priors, velocities) are re-expressed in it through ECEF; refuse sessions with different datums. Tests: two synthetic sessions with known frames give identical ENU positions for the same ECEF point.
2. **Image naming and cameras.** COLMAP image names carry the session (`<session>/<camera folder>/IMG_x.JPG`) because Canon file numbers repeat across rides. One COLMAP camera per session and camera folder to start (focus and mounting may differ between rides); sharing intrinsics per body is an experiment once the test stretch shows whether it helps.
3. **`colmap-prepare` across sessions.** Takes several images.json files, each with its own rig config (rigs may be re-aimed between rides), writes priors in the common frame, runs feature extraction per COLMAP model group as today. priors.json records each image's session.
4. **Matching and mapping.** The spatial matcher already pairs images by prior distance, so cross-session pairs come for free; the report adds match counts between sessions and between opposite directions, so a pass that fails to connect is visible. Mapping, sampled refine, hold-out and gates as in slice 6, with residuals reported per session.
5. **Rig calibration per session.** `rig-calibrate` runs per session on the joint model (each ride's rig may differ).
6. **Auxiliary unsynchronised cameras (iPhone).** Ingest its photos (or video frames) without pulses: order by EXIF time, give each a loose prior from the session's antenna track at that time (clock offset estimated by registering a few frames and fitting; prior sigma a few metres until the offset is known), a separate COLMAP camera, and a flag in images.json. Optional; the test stretch decides whether it is worth it.
7. **Splat export across sessions.** Select images by 3D proximity to the block (camera centre within a distance of the block's path, all sessions), keep the held-out rule per session, record each image's session for per-session appearance later.
8. **Route.** The rider path, profile and grade come from one chosen session's reconstructed trace (slice 7 unchanged), using the joint model's points for the road surface.

## Done when

- Single session unchanged: field test 2 session 1 prepares an identical workspace through the new code.
- The ~100 m test stretch (re-aimed rig, return pass, optional iPhone) reconstructs as one model with all sessions registered and per-session prior residuals within the slice 6 gates.
- Blocks export with images from every session and train through the existing job.
- A comparison on the same stretch: single pass versus both directions (and with/without iPhone), on held-out scores and Olly's view of the road.

## Risks

- **Cross-direction matching:** opposite viewing directions share fewer features; the side cameras and the down-facing views carry most of the overlap. Measured on the test stretch.
- **Appearance between rides:** lighting changes; needs per-session appearance correction in training (design: bilateral grid or PPISP).
- **Run time on the Mac:** two passes double the photos; a full route may need COLMAP on a bigger machine. The test stretch gives the numbers.
