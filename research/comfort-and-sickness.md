# Comfort and sickness in VR cycling - evidence and pipeline requirements

Date: 2026-11-12. Scope: web VR cycling on captured routes rendered as Gaussian splats (Quest 3 baseline, PCVR high tier), smart trainer, 30-90 min sessions, 10-20 mph typical, up to 40 mph.

Labels: VERIFIED = read in the linked primary source (full text, or "abstract" where only the abstract was readable); INFERRED = my reasoning from verified sources; UNVERIFIED = not checked against a primary source. ACM DL and several publisher sites returned HTTP 403 to automated fetches, so several Matviienko results are abstract-level only.

## 1 Summary - top 10 findings

1. Only two cycling-specific controlled sickness studies exist with a countermeasure contrast, both small (N=18, N=24): handlebar steering gave the lowest sickness of three steering methods, and airflow was the most promising of airflow, vibration and dynamic FOV across 1D, 2D and 3D trajectories. VERIFIED (abstract) https://doi.org/10.1145/3491102.3501959
2. No study has measured sickness in VR cycling sessions of 30-90 min. Study exposures are 5-20 min (Baule et al. mean 19 min; Palmisano et al. 10 min; Bonato et al. 5 min). The longest relevant is So et al. at 30 min (not cycling). Session length is the largest evidence gap for this product. INFERRED from the sources in section 2-3.
3. High-frequency vertical oscillation of the viewpoint (head bob at 1.8-7.4 Hz) roughly doubled total SSQ (58.2 vs 29.7, N=16) while also increasing vection. Captured road vibration and pedalling bob must not reach the camera. VERIFIED (abstract) https://doi.org/10.3357/ASEM.2079.2007
4. Changing vection (speed or heading changing) causes more sickness than steady vection. Steady expanding flow gave significantly lower SSQ on all subscales. Smooth speed and heading matter more than low speed. VERIFIED (abstract) https://doi.org/10.1162/pres.17.3.283
5. Sickness and vection rose with virtual speed from 3 to 10 m/s and plateaued above 10 m/s (N=96, 30 min). 10-20 mph (4.5-9 m/s) sits on the rising part of that curve; 40 mph is in the plateau. VERIFIED (abstract) https://doi.org/10.1518/001872001775898223
6. Frame rate: 120 fps was a threshold, with lower sickness than 60 and 90 fps and little extra gain at 180 (N=32). Frame drops and frame-time jerks were the authors' main explanation for discomfort in the Baule et al. bike sim, and latency jitter alone raised SSQ significantly. Stable frame time is a first-order requirement. VERIFIED (abstract) https://doi.org/10.1109/TVCG.2023.3247057, https://doi.org/10.1109/VR.2018.8446195; VERIFIED (full text) https://doi.org/10.17868/strath.00093314
7. Photorealism is not a free win. Platform guidance (Meta) says to reduce optic flow with "more solid textures" and fewer "noisy textures", and a 2023 systematic review (88 papers) found mixed evidence on whether reducing realism reduces sickness. Fine, high-contrast road texture in the periphery raises optic flow. Realism should be judged by temporal stability, not detail. VERIFIED https://developers.meta.com/horizon/resources/locomotion-design-reduce-optic-flow/, https://doi.org/10.3389/frvir.2023.1027552
8. Keep the horizon level: Android XR says "ensure the virtual horizon remains steady and level"; Apple says avoid rotating the virtual world and avoid sustained oscillation near 0.2 Hz. Camera roll should be zero and rig pitch should not follow road gradient. Apple: VERIFIED (HIG Motion JSON). Android horizon quote: UNVERIFIED (seen only in a search-engine excerpt of the Android XR guides, not on the fetched motion page). Zero roll and level pitch: INFERRED
9. Visual gradient is a forgiving channel: riders accepted virtual slopes far steeper than physical ones (physical -15 % felt congruent with visual down to about -50 %), and VRSQ did not correlate with thresholds (r=.01). Gradient can be conveyed by scenery and trainer resistance without pitching the camera. VERIFIED (full text) https://doi.org/10.1145/3772318.3791538
10. Splat-specific artefacts that VR makes worse: popping during head rotation from per-splat global sorting, and "stereo-disrupting floaters" from projection distortion; both are solvable at 72+ fps (VRSplat, user study N=25) and StopThePop removes popping at about 4 % cost. Treat them as comfort defects, not cosmetic ones. VERIFIED (abstract) https://arxiv.org/abs/2505.10144, https://doi.org/10.1145/3658187

## 2 VR cycling studies

| Study | Design, N | Key result | Label |
|---|---|---|---|
| Matviienko, Muller, Zickler, Gasche, Abels, Steinert, Muhlhauser, CHI 2022, https://doi.org/10.1145/3491102.3501959 | Exp 1 N=18: handlebar vs HMD vs upper-body steering. Exp 2 N=24: 1D, 2D, 3D trajectories x countermeasures (airflow, vibration, dynamic FOV) | Handlebar steering gave lowest VR sickness without loss of cycling performance; airflow "suggests to be the most promising method" across all three trajectory types | VERIFIED (abstract only; full text 403, so SSQ values, effect sizes, ride durations not read) |
| Matviienko, Hoxha, Muhlhauser, CHI 2023, https://doi.org/10.1145/3544548.3581050 | N=24, bikeless vs stationary vs tandem (moving) bicycle x 4 control levels (none, steering, pedalling, both), indoor and outdoor | Tandem gave highest realism "without increasing motion sickness"; bikeless gave highest feeling of safety; "bicycles are not essential for cycling in VR" | VERIFIED (abstract only) |
| Wintersberger, Matviienko, Schweidler, Michahelles, PACM HCI (MobileHCI) 2022, https://doi.org/10.1145/3546745 | N=31, motion platform bike: strong tilt vs weak tilt vs no movement, no centrifugal simulation | Weak tilt improved realism without worsening sickness; "sweet spot" between presence and sickness. Keppel et al. report the good range as about +-2.28 deg physical roll; stronger tilt degraded performance and raised sickness | VERIFIED (abstract); roll figures VERIFIED as reported in Keppel et al. 2026 |
| Keppel, Prochazka, Lewin, Stroehnisch, Strauss, Zenner, Degraen, Matviienko, Schneegass, CHI 2026, https://doi.org/10.1145/3772318.3791538 (preprint PDF https://umtl.cs.uni-saarland.de/paper_preprints/keppel-et-al-vr-cycling-inclinations-pre-print-chi-2026.pdf) | N=30, tilting indoor bike (+-15 %), visual inclination up to +-65 %, multiple staircase | Physical -15 % accepted with visual down to about -50 %; physical +15 % with visual up to about +31 %. VRSQ stable across runs (M 25.2 SD 16.6 vs 26.9 SD 18.1; W=140.5, p=.374, r=0.17). Thresholds uncorrelated with VR experience (rho=.12, p=.521), cycling experience (rho=-.15, p=.422), VRSQ (r=.01, p=.922). Design advice: scenes need "clear horizontal and vertical structures and distant, relatively flat regions" as references | VERIFIED (full preprint) |
| Baule, Disser, Gobel, EG-ICE 2025, https://doi.org/10.17868/strath.00093314 (PDF https://strathprints.strath.ac.uk/93314/7/Baule-etal-EG-ICE-2025-Ride-to-the-future-development-of-a-VR-bicycle.pdf) | n=21 (age 20-45), Unity bike sim with SUMO traffic, speed-triggered fan, mean 19 min, think-aloud + post-test questionnaire, no control condition | "about 60% felt some kind of influence", "34,92% stated a distracting influence", "11,11% felt strong discomfort". Authors blame performance-caused jerks and 19 min being long for VR novices | VERIFIED (full text) |
| Matviienko et al. 2022 as cited by Baule et al. | Fan in front of bike | Baule et al. adopted a fan on the strength of Matviienko 2022; no independent measurement | VERIFIED (full text, citation only) |
| Fitness-app practice (Holofit, VZfit) | Vendor help pages and press | Fans and "tunnel vision" commonly recommended; no data | UNVERIFIED (VirZoom help page https://virzoom.zendesk.com/hc/en-us/articles/360058263272 returned 403; claims seen only in search snippets and press, e.g. https://www.vrfitnessinsider.com/hands-on-with-the-holodia-holofit-part-two-play-testing-with-a-stationary-bike/) |

Baule et al. denominator check (INFERRED): 34.92 % = 22/63 and 11.11 % = 7/63, with 63 = 21 participants x 3 SSQ areas (nausea, oculomotor, disorientation; the paper "asked generally about the three main areas covered by the SSQ"). So the percentages are shares of participant-by-symptom-area answers, not of participants; "11 % of riders felt strong discomfort" is not a valid reading. It was not the 16-item weighted SSQ, so it is not comparable with SSQ scores elsewhere, and with no contrast condition it supports no causal claim.

Cross-study observations:
- Pedalling coupling, steering and airflow: active control (handlebar steering) reduces sickness relative to HMD or body steering (Matviienko 2022). Whether pedalling-coupled speed reduces sickness versus passive travel is not established by abstracts read here; Matviienko 2023 crossed control level with fidelity but the abstract reports sickness only for the tandem. UNVERIFIED beyond abstract.
- Prior VR experience: Keppel et al. found no correlation with detection thresholds (not sickness). Saredakis et al. meta-analysis (55 articles, 3,016 participants) found older samples (mean age >= 35) had lower SSQ, no sex difference, pooled total SSQ 28.00 (95 % CI 24.66-31.35). VERIFIED (abstract) https://doi.org/10.3389/fnhum.2020.00096
- Effect sizes: none of the cycling abstracts report effect sizes; all samples are 18-31 young adults, mostly lab sessions under about 20 min. Generalisation to 30-90 min home exercise is untested. INFERRED.

## 3 General locomotion evidence

| Factor | Evidence | Direction for this product | Label |
|---|---|---|---|
| Frame rate | Wang et al. TVCG 2023, N=32, 60/90/120/180 fps: "120fps is an important threshold"; above it lower SS without loss of experience | Quest 3 baseline should target 90 Hz minimum with no drops, 120 Hz if the splat budget allows; PCVR 120 Hz | VERIFIED (abstract) https://doi.org/10.1109/TVCG.2023.3247057 |
| Frame drops / judder | Meta: "Maintaining a consistent frame rate is crucial"; judder from inconsistency creates discomfort. Baule et al. attribute discomfort to perf jerks | Frame-time stability beats peak quality | VERIFIED https://developers.meta.com/horizon/resources/locomotion-comfort-usability/ ; full text Baule |
| Latency and jitter | Stauffert et al. 2018: real-world latency jitter pattern gave significant SSQ increase. Palmisano et al. 2020: sickness driven by time-varying difference between virtual and physical head pose (DVP) | Keep head pose on the compositor path (reprojection); never let heavy splat sort stall pose updates | VERIFIED (abstract) https://doi.org/10.1109/VR.2018.8446195, https://doi.org/10.3389/frvir.2020.587698 |
| Speed | So, Lo, Ho 2001, N=96 men, 30 min, 3-59 m/s: nausea and vection rise from 3 to 10 m/s, then plateau; speed affected onset time, not rate of increase | Speed itself is not avoidable; spend effort on smoothness | VERIFIED (abstract) https://doi.org/10.1518/001872001775898223 |
| Vection change / acceleration | Bonato et al. 2008: steady expanding flow gave less SS on all subscales than expanding-contracting flow. Meta: "Keep acceleration events brief and infrequent" | Smooth trainer speed; no speed jitter; corners with continuous curvature | VERIFIED (abstract) https://doi.org/10.1162/pres.17.3.283; https://developers.meta.com/horizon/design/locomotion-best-practices/ |
| Vection vs sickness | Keshavarz et al. 2015 review: link is debated; vection may not be necessary for VIMS | Do not try to kill vection; it is the product | VERIFIED (abstract) https://doi.org/10.3389/fpsyg.2015.00472 |
| Head bob / vertical oscillation | Palmisano et al. 2007, N=16, 10 min: 1.8/3.7/7.4 Hz vertical oscillation raised vection (5.51 vs 3.56) and total SSQ (58.18 SD 32.2 vs 29.67 SD 24.7) | Strip all captured vertical oscillation | VERIFIED (abstract) https://doi.org/10.3357/ASEM.2079.2007 |
| Low-frequency oscillation | Apple: avoid sustained oscillation "around 0.2 Hz because people can be very sensitive to this frequency" | Rider weave and slow lateral sway must be removed too, not only high-frequency shake | VERIFIED Apple HIG Motion (https://developer.apple.com/design/human-interface-guidelines/motion, read via its JSON data endpoint) |
| Rotation (yaw) | Apple: "avoid letting people rotate a virtual world... even when they control the rotation and the movement is subtle". Meta and Android recommend snap turns. Ang and Quarles 2023 review (88 papers): dynamic blur during rotation reduced symptoms in multiple studies | Yaw from road curvature is unavoidable on a route; minimise non-road yaw and bound yaw acceleration | VERIFIED Apple JSON; https://doi.org/10.3389/frvir.2023.1027552 (full text via WebFetch summary) |
| Roll and pitch | Lo and So 2001 (Applied Ergonomics) on rotation axes: commonly cited as roll/pitch more provocative than yaw | Zero visual roll; level rig pitch | UNVERIFIED (no abstract retrieved) https://doi.org/10.1016/S0003-6870(00)00059-4 |
| Horizon | Android XR: "ensure the virtual horizon remains steady and level" | Level rig | UNVERIFIED (search-engine excerpt of the Android XR guides; not on the fetched motion page) https://developer.android.com/design/ui/xr/guides/considerations |
| FOV restriction | Fernandes and Feiner 2016, N=30, two sessions: subtle dynamic FOV reduction reduced sickness without lowering presence and helped adaptation. Ang and Quarles 2023: only 4 of 10 FOV papers significant. Matviienko 2022: airflow beat dynamic FOV in cycling | Offer a speed- and yaw-rate-driven vignette as an option, not a default fix | VERIFIED (abstract) https://doi.org/10.1109/3DUI.2016.7460053; review full text summary |
| Rest frames | Cao et al. 2018, N=44, multi-day: static or dynamic rest frame let users travel further before stopping; static gave more real-time comfort. Apple: "Consider giving people a stationary frame of reference" | A fixed cockpit element (handlebar model, HUD) aligned to the real bike is a candidate rest frame | VERIFIED (abstract) https://doi.org/10.1109/VR.2018.8446210 ; Apple JSON |
| Realism | Meta: reduce noisy textures and visible edges to cut optic flow. Ang and Quarles: realism reduction gave mixed results. Weech et al. 2019 review: presence and cybersickness negatively related. A 2018 Oulu thesis reportedly found higher realism gave more cybersickness | Realism is double-edged: more presence (possibly less sickness) but more peripheral flow. Temporal stability is the safe target | VERIFIED Meta page; review full text; VERIFIED (abstract) https://doi.org/10.3389/fpsyg.2019.00158 ; Oulu thesis UNVERIFIED https://urn.fi/URN:NBN:fi:oulu-201802091218 |
| Exposure time | Saredakis 2020: exposure time influences sickness profiles; Palmisano 2007 and So 2001: symptoms grow with exposure | Expect accumulation over 30-90 min; ramps and rest stops | VERIFIED (abstract) |
| Prior experience / adaptation | Fernandes and Feiner: FOV restriction helped users adapt over sessions. Android XR: "introduce motion mechanics gradually" | Onboarding with short, flat, straight routes | VERIFIED (abstract); Android page |
| Airflow, multisensory | Matviienko 2022 (above). Search results also point to a Sci Rep 2022 study where airflow but not vibration reduced VIMS on a bicycle scene | Recommend a speed-coupled fan | Matviienko VERIFIED (abstract); Sci Rep UNVERIFIED https://doi.org/10.1038/s41598-022-21307-z |

## 4 Platform guidance

| Source | Guidance relevant here | Label |
|---|---|---|
| Meta, Locomotion best practices https://developers.meta.com/horizon/design/locomotion-best-practices/ | "Keep acceleration events brief and infrequent"; vignettes to darken edges during movement; make "peripheral geometry opaque and using simpler textures"; "Maintaining a consistent frame rate to prevent judder"; "flat terrain, forward movement, distant walls" | VERIFIED |
| Meta, Comfort and usability https://developers.meta.com/horizon/resources/locomotion-comfort-usability/ | Users "experience less discomfort when they can predict camera movements"; instant velocity changes; Independent Visual Backgrounds | VERIFIED |
| Meta, Reduce optic flow https://developers.meta.com/horizon/resources/locomotion-design-reduce-optic-flow/ | "minimize the number of visible edges and noisy textures"; peripheral occlusion | VERIFIED |
| Apple HIG Motion (visionOS) https://developer.apple.com/design/human-interface-guidelines/motion | Peripheral motion can cause discomfort; "avoid letting people rotate a virtual world"; "Consider giving people a stationary frame of reference"; "if the entire surrounding area appears to move ... people can feel unwell"; avoid sustained oscillation around 0.2 Hz | VERIFIED (JSON data endpoint) |
| Apple HIG Immersive experiences https://developer.apple.com/design/human-interface-guidelines/immersive-experiences | "Avoid sudden, jarring transitions"; "Design ways for people to interact with content without moving" | VERIFIED (JSON data endpoint) |
| Android XR motion https://developer.android.com/design/ui/xr/guides/motion | "Predictable camera motions"; "Rest stops"; "Consider making motion optional"; whole-world movement: "fade out and fade in, hide a user's peripheral vision, accelerate slowly, or avoid rotating" | VERIFIED (horizon-level and tunnel-vision advice seen only in a search excerpt of the guides: UNVERIFIED) |
| Holofit, VZfit, Blync | No published comfort guidance located; fan and tunnel-vision advice appears in press and help pages | UNVERIFIED |

Note: the product's core loop (continuous, app-driven forward travel through a whole moving world with road-driven yaw) is exactly what all three platforms flag as the highest-risk motion class. Snap turns and teleport are not usable for route following. INFERRED.

## 5 Requirements on the camera path

All INFERRED from sections 2-4 unless stated; thresholds marked "proposal" need the experiments in section 7.

Decompose the captured rig pose into (a) a smooth route reference (position, heading, elevation) used to drive the virtual rig, and (b) residual motion (wobble, lean, bob, head turns), which is discarded. The HMD supplies the rider's own head motion on top; the rig should carry none.

| Channel | Requirement | Rationale |
|---|---|---|
| Position (horizontal) | Fit a C2-continuous spline to the RTK path (zero-phase smoothing, no lag). Keep max deviation from raw RTK under about 0.1 m (proposal) so scenery correspondence holds | Smooth path, preserved registration; cm-level RTK makes this cheap |
| Heading (yaw) | Derive yaw from the smoothed spline tangent, never from the camera's own orientation. Remove all yaw content above about 0.1 Hz that is not explained by road curvature (proposal) | Apple 0.2 Hz sensitivity; rider weave and steering oscillation are roughly 0.5-2 Hz; Bonato changing-vection result |
| Yaw rate and yaw acceleration | Curvature must be continuous (no kinks at spline knots, clothoid-like transitions); bound yaw acceleration (proposal: under about 30 deg/s^2) and flag segments exceeding a yaw-rate budget for the runtime | Apple and Meta rotation guidance. Unavoidable yaw rate = v/R: 40 mph (17.9 m/s) at R=50 m is about 20 deg/s; 15 mph at R=10 m is about 38 deg/s |
| Corners at speed | Export per-segment curvature and a "comfort speed" (v_max = sqrt(a_lat_max*R) style budget, or a yaw-rate cap) so the runtime can (a) cap virtual speed into tight bends as a real rider brakes, (b) raise a yaw-driven vignette, (c) warn. Do not cut corners geometrically | Keeps road correspondence; FOV reduction evidence (Fernandes and Feiner) |
| Pitch | Do not pitch the rig with road gradient. Camera height follows a low-passed road elevation profile; view orientation stays level. Convey gradient via scenery and trainer resistance | Android "horizon steady and level"; Keppel: visual slope perception is robust and generous without camera pitch |
| Roll | Zero rig roll. Remove captured bike lean entirely | Apple world-rotation guidance; Wintersberger: only about +-2.3 deg physical roll was comfortable, and visual roll without vestibular roll is a pure conflict |
| Vertical | Remove all vertical oscillation above about 0.5 Hz (bumps, pedalling bob, suspension); keep only terrain elevation trend | Palmisano 2007: 1.8-7.4 Hz bob doubled SSQ |
| Camera height | Rig origin on the road surface (or a fixed seated eye height, about 1.2-1.4 m above road for a road bike, proposal), with HMD-tracked head height added consistently; do not lower the eye point | Optic flow rate scales with v/h, so a low camera raises flow and vection; Keppel cites Hecht: lower viewpoints amplify perceived slant |
| Lateral riser | Lateral offset is rider-controlled, clamped to the road corridor with soft limits (smooth deceleration, no hard stops) | Handlebar steering lowest sickness (Matviienko 2022); Meta "soft camera collisions" |
| Speed | Speed comes from trainer physics; low-pass and interpolate trainer updates so per-frame speed is smooth; no step changes | Bonato; Meta acceleration guidance |
| Start and stop | Fade or ease in and out; first minutes on low-curvature segments | Android onboarding, Apple transitions |

Deliverables from the pipeline per route: smoothed pose track at render rate (or spline), curvature and yaw-rate profile, elevation profile, gradient profile (for trainer resistance), comfort-speed profile, and a QA report of yaw-acceleration and vertical-jitter spectra.

## 6 Requirements on the splat assets and runtime

| Item | Requirement | Basis | Label |
|---|---|---|---|
| Frame rate | Quest 3: locked 90 Hz minimum, 120 Hz target where budget allows; PCVR: 120 Hz. Budget splat count to the 99th percentile frame time, not the mean | Wang 2023; Meta judder guidance; Baule | INFERRED from VERIFIED |
| Frame-time stability | No hitches from streaming, sorting or LOD swaps; prefetch tiles along the known route ahead of the rider | Baule jerks; Stauffert jitter | INFERRED |
| Sorting popping | Use view-consistent sorting (hierarchical or per-pixel resort as in StopThePop, or VRSplat) so splats do not reorder visibly under head rotation | StopThePop; VRSplat | VERIFIED (abstract) for the artefact; INFERRED for the requirement |
| Stereo consistency | Both eyes must see the same splat ordering and no projection-distorted floaters ("stereo-disrupting floaters") | VRSplat abstract | VERIFIED (abstract) |
| Near-field floaters | Remove Gaussians inside a tube around the camera path (proposal: within about 1.5-2 m of the eye trajectory and inside the rider corridor), and penalise near-camera density during training | Near floaters produce large disparity, stereo conflict and fast-moving optic flow | INFERRED |
| LOD transitions | Crossfade or continuous LOD (opacity-blended); never swap whole tiles in one frame in the central or peripheral view | Changing vection and flicker; Apple "avoid sudden, jarring transitions" | INFERRED |
| View-dependent colour | Limit SH degree or regularise view dependence on road and near objects so colour does not shimmer as the viewpoint moves along the path; validate along the actual path, not only training views | Flicker adds unstable motion signal | INFERRED |
| Temporal flicker metric | Add a flicker metric over a rendered fly-through at riding speed (frame-to-frame luminance change in static regions) as a pipeline gate | No standard exists; proposal | INFERRED |
| Peripheral detail | Avoid adding high-frequency noise in the near periphery (road surface, verges); prefer slightly smoother road texture over aliased detail | Meta "noisy textures"; realism evidence mixed | INFERRED |
| Sky and far field | Stable, artefact-free sky and distant terrain (acts as a reference frame for horizon and slope) | Keppel design implication; Apple stationary frame | INFERRED |
| Cockpit rest frame | Render a handlebar or HUD fixed to the real bike pose | Cao 2018; Apple | INFERRED |
| Comfort options | Vignette (speed and yaw driven), speed cap, session timer and rest prompts; fan recommendation in onboarding | Fernandes; Matviienko 2022; Android "rest stops" | INFERRED |

## 7 What is unknown and experiments worth running

Unknowns:
- Sickness trajectory over 30-90 min of active cycling in VR; all cycling studies are under about 20 min.
- Whether pedalling-coupled self-paced motion reduces sickness relative to passive travel at matched speed (abstract-level evidence only).
- Whether photorealistic splat scenes raise or lower sickness versus mesh or stylised scenes at matched frame rate and stability; no splat study measures SSQ.
- Tolerable yaw-rate and yaw-acceleration budgets for road-following travel; no study found that sets numeric limits.
- Effect of smoothing level on perceived realism and scenery misregistration.
- Quest 3 browser WebXR frame-time headroom for large splat scenes at 90 and 120 Hz (not checked here, UNVERIFIED).

Experiments (within-subjects, SSQ pre/post plus FMS every 2-5 min, dropout time, N of about 24-30 per Matviienko and Keppel designs):
1. Path smoothing: raw capture pose vs smoothed position plus tangent heading vs smoothed plus level rig (no pitch or roll). Expect large effect from removing bob and roll.
2. Pitch policy: rig pitch follows gradient vs level rig, on a hilly route; measure SSQ and perceived gradient.
3. Frame rate and stability: 72 vs 90 vs 120 Hz, and 90 Hz with injected 1 % frame drops, on the same splat route.
4. Splat artefacts: standard 3DGS sort vs view-consistent sort; with and without near-field floater removal.
5. Corner handling: no mitigation vs yaw-driven vignette vs comfort speed cap on a twisty descent at 30-40 mph.
6. Airflow: speed-coupled fan vs none, to replicate Matviienko 2022 with splat scenes.
7. Long exposure: 60 min rides on day 1, 3, 7 to measure adaptation and late-onset symptoms.
8. Realism: same route as splats vs simplified mesh vs splats with peripheral smoothing, at matched frame time.
