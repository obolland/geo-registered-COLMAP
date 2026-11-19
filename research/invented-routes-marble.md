# Invented routes from generated 3D worlds

Date: 2026-11-17. Research only, no AWS resource was used. All sources are primary:
World Labs official documentation, pricing, release notes and terms; Tencent official
GitHub repositories, papers and licences. Fetch date for every source is 2026-11-17.
Claims marked "Inferred" are my reading, not a vendor statement.

## Summary verdict

Marble can generate an explorable, metrically scalable 3D world with a Gaussian splat
export, a collider mesh and a VR link. That is enough to test an invented route
experience. It is not a route generator: the API has no path, road, terrain-profile or
gradient input, the docs publish no world dimensions, and the only documented layout
control is a coarse blockout or manual world placement. A single generation is a room or
scene-scale space, not a 20-80 km corridor. Long routes would have to be composed from
many worlds, and the documented Compose splat counter is 2,000,000 against a full world
of about 2,000,000 splats, so the composition budget is a hard question. The cheapest
useful experiment is one full Marble 1.1 world from a panorama prompt, exported as SPZ
plus collider, with its metric metadata measured and a short headset ride-through, then
WebXR if the Spark route supports it.

## 1. What Marble generates

### Inputs

The API accepts a required `world_prompt` with `type` one of `text`, `image`,
`multi-image` or `video` [S6]. Images may be JPEG, PNG or WebP; video may be MP4, WebM,
MOV or AVI, maximum 100 MB [S6]. Multi-image accepts up to 4 images normally, or up to 8
with `reconstruct_images` [S6]. Image prompts have `is_pano` `auto`, `true` or `false`;
multi-image items have an `azimuth` in degrees; text and image prompts support
`disable_recaption` [S6]. A `seed` from 0 to 4294967295 is supported [S6].

The Marble app also accepts text, single image, multiple images, video, 360 panorama and
Chisel blockouts [S3][S11][S16]. The video guide warns that motion blur, shake, zooms and
auto-exposure shifts in the source carry into the generated world, and that the scene
should be static [S12].

### Outputs and density

Official export specifications [S2]:

| Asset | Format | Documented size |
|---|---|---|
| Full splat | SPZ or PLY | about 2M splats |
| Low-res splat | SPZ or PLY | about 500k splats |
| 360 panorama | PNG | 2560 x 1280 equirectangular |
| Collider mesh | GLB | 100k-200k triangles; typically 3-4 MB [S13] |
| High-quality mesh | GLB | about 600k triangles with textures, or about 1M triangles with vertex colours; typically 100-200 MB [S13] |

The API response also includes a panorama image [S8]. The generated-world response can
include Marble links, thumbnails, panorama imagery, Gaussian splat files and mesh assets
[S1]. The Gaussian splat (SPZ or PLY) is the highest-fidelity representation; the meshes
are derived and can contain holes, uneven geometry and floaters, especially on thin,
transparent or reflective surfaces, sky and poorly covered areas [S13].

### World extent

No official page states a world's dimensions, area, radius or maximum extent. The
September 2025 Marble announcement says "Room-sized worlds are a great start" and that
large environments are built by composing individual generations [S14]. The current
models page describes Marble 1.1 Plus as "our latest and most advanced model for creating
the largest worlds" and says it "automatically expands its coverage of 3D space to
generate bigger worlds when possible", but gives no size figures [S4]. The API generate
reference mentions "dynamic world sizing" for `marble-1.1-plus` and likewise gives no
dimensions [S6]. Inferred: a single generation is a scene-scale space, not a kilometre
corridor, and the product has no documented 20-80 km capability.

## 2. Extending and stitching

The official Expand guide [S5]:

- Expand grows an existing world by generating content in unexplored or blurry areas and
  is meant to "naturally connect" to the existing world.
- It conditions on the existing world's "visual style, architecture, and aesthetic" and
  claims automatic seamless transitions with matching lighting, scale and architecture.
- It supports one expansion of the world in different directions, creates a new version
  and preserves previous states.
- Vertical expansion is not supported.
- Marble 1.1 Plus worlds cannot be expanded.
- Results can be exported to Studio for stitching.

The Compose tool is manual assembly [S10]: multiple worlds can be added and adjusted with
X/Y/Z position, rotation and proportional scale; users match overlapping areas and floor
heights themselves. The docs state there is no floor-plan input that auto-connects rooms.
The Compose page shows a "500,000 / 2,000,000" splat counter against the account limit,
while a full world is about 2M splats [S2][S10]. No maximum world count or composition
dimensions are stated. Inferred: full-resolution composition of many worlds is limited
by splat budget, not by an announced geometric limit, and would need low-res assets or a
higher limit that is not documented.

No API parameter for chaining, neighbour conditioning or route continuation was found.
Expand is an app workflow, not an API field in the generate reference [S5][S6].

## 3. Scale

Generated splat assets are exported in arbitrary model units, not metres [S8]. The SPZ
export carries `assets.splats.semantics_metadata` with [S7]:

- `metric_scale_factor`: multiply raw asset units by this to get metres.
- `ground_plane_offset`: subtract from the Y centre after scaling to put the ground plane
  at Y = 0.

For log-scale Gaussian fields, add `ln(metric_scale_factor)` rather than multiplying the
log values [S7]. Apply the scale to both centres and Gaussian sizes; apply the ground
offset only to centres [S7]. The values are world-specific and must be read from each
world, not hard-coded [S7].

So the output is metric after applying the provided metadata, but the generation itself
does not offer a documented metric scale or explicit world-size setting. The API has no
scale parameter [S6]. Compose can resize worlds proportionally for manual alignment [S10];
that is an editing transform, not a generation-scale control.

## 4. A ridable path

There is no documented path, road, route, terrain, elevation or gradient parameter in the
API [S6]. Inputs control appearance and coarse layout, not a road centreline or height
profile. The available layout controls are:

- Chisel: blockout walls, rooms, imported GLB/FBX models, wall-height handles and a
  panorama camera position [S11]. The docs describe building a geometric base for the
  generated environment; there is no road, ground or slope tool [S11].
- Compose: place worlds manually, match overlaps and floor heights, scale proportionally
  [S10].
- Multi-image azimuths and panorama orientation, which control views rather than a road
  [S6].

The collider mesh is documented as a coarse physics mesh, not a semantic road or terrain
product [S2][S13]. It can provide a ground surface for a vehicle if the mesh aligns with
the splat, but no slope or road extraction is documented. The export file specifications
do not document a depth export [S2].

Inferred: a rideable path would have to be authored in the app after generation, or
extracted from the collider mesh and manual camera path. Do not expect the generated world
to contain a road that follows a requested 20-80 km route.

## 5. Quality for fast motion and close-up viewing

No official close-up or fast-motion quality specification or benchmark was found. The
documented facts are:

- The full splat, about 2M, is the highest-fidelity representation; use it over the 500k
  variant for detail [S2][S13].
- Mesh exports are lower fidelity and have documented artefacts on thin or complex
  structures, transparent or reflective surfaces, sky and background [S13].
- Source video motion blur, shake, zoom and exposure changes carry into the generated
  world [S12].
- Marble 1.1 claims "improved quality at a fixed cost" [S4]. The release notes dated
  2025-11-20 say Marble Studio supports editing and composing significantly larger worlds
  with a level-of-detail splat backend, and that performance should remain stable at more
  worlds and higher splat counts [S9].
- No frame-rate, latency, headset performance or close-up resolution figures are
  published.

Inferred: fast forward motion and close-up road texture are unproven and should be
measured in the experiment. A 2M-splat world spread over a large area will have low
splat density per metre for close viewing, and the collider mesh is only a coarse proxy.

## 6. VR and WebXR

The FAQ documents two app features: "Copy VR link" for a headset-compatible link, and a VR
icon that opens the world directly in a VR headset [S15]. The Welcome page lists a VR
experience export [S16]. Spark is the recommended three.js route and is described as
suitable for VR experiences, with an example of Marble worlds in VR [S17]. The docs do not
use the term WebXR, do not list supported headsets or runtimes, and publish no performance
figures [S15][S17]. Inferred: a WebXR viewer can be built on Spark, but there is no
vendor guarantee or benchmark for a fast cycling camera.

## 7. Licence and commercial use

Official Terms of Service [S18]:

- Paid account users own "all rights, title, and interest in and to Outputs generated
  through their use of the Services", excluding World Labs products, technology,
  confidential information and IP (section 3.3(b)).
- Paid users may exploit outputs "for any purpose, including Commercial Purposes", and
  API users may distribute and sublicense outputs for commercial purposes, subject to the
  terms and any order form (sections 3.3(b), 3.3(d)).
- Free account users do not own outputs: World Labs "retains all rights" and grants only a
  revocable, non-exclusive, royalty-free, worldwide licence for personal, non-commercial
  use (section 3.3(a)).
- Account status at generation time controls the rights (section 3.3(c)).
- Paid users must not misrepresent the origin or method of creation and must include
  attribution to World Labs on request (section 3.7(b)).
- Users must not remove proprietary notices, watermarks or attribution indicators
  (section 3.7(d)).
- Restrictions include no reverse engineering, no use to develop competitive products, no
  caching or persisting API responses beyond the documented duration, and no high-risk AI
  uses (sections 2.8, 2.10).

The Marble app plan table separately lists commercial rights as a Pro feature [S19].
Inferred: for a commercial VR route product, use paid API credits or a Pro-or-higher app
plan at generation time, keep attribution available, and have counsel review the output
ownership and competition clauses.

## 8. Access and cost

API [S6][S20]:

- Base URL `https://api.worldlabs.ai/marble/v1`; auth header `WLT-Api-Key`.
- Credits: 1 USD buys 1,250 credits; minimum purchase 6,250 credits (5 USD); credits do
  not expire.
- Marble 1.0 or 1.1: 1,500 credits for a panorama image input; 1,580 for text or a
  non-panorama image; 1,600 for multi-image or video.
- Marble 1.1 Plus: the same base plus 0-1,500 variable credits, up to 3,000-3,100 total.
- Draft: 150-250 credits.
- PLY splat export is free; high-quality mesh export is 3,500 credits (2.80 USD) per new
  run; multiple mesh variants are included and cached exports are free.
- Rate limits: about 3 generation starts per minute and 60 per hour by default; approved
  higher-throughput accounts get about 30 per minute for standard models and 90 per
  minute for Draft. Limits apply per account, not per key. High-quality mesh has a
  separate limit of four requests per hour per user [S2].
- Marble app credits and API credits are separate and not interchangeable.

There is an official documentation conflict: the API models page says the current default
is `marble-1.0` [S21], while the generate reference says the default is `marble-1.1`
[S6]. Pin the model explicitly.

## 9. Tencent HunyuanWorld

### HunyuanWorld 1.0

Official GitHub [S22]:

- Inputs: text prompt or image, with an optional foreground label and outdoor class.
- Pipeline: generate a 360 panorama, then a semantically layered 3D mesh world.
- Outputs: panorama and explorable mesh; mesh export is documented. Splat and depth
  exports are not documented.
- No metric dimensions or world-size limit are given.
- Code and weights are available; the lite model supports consumer GPUs such as a 4090.
  No full-model minimum is stated.
- The browser viewer is interactive; VR is named as an application area but no headset or
  WebXR integration is documented.

Licence [S23]: Tencent HunyuanWorld-1.0 Community License, released 2025-07-27. It
excludes the EU, UK and South Korea, is royalty-free for the territory, and requires a
separate Tencent licence if the licensee had more than 1 million monthly active users in
the preceding month. Tencent claims no rights in outputs; modified files need notices and
non-hosted distributions need a Notice file.

### HY-World 2.0

Official GitHub [S24] and paper [S25]:

- Announced 2026-04-16, paper submitted 2026-04-15.
- Inputs: text, single-view image, multi-view images or video.
- Outputs: 3D Gaussian splatting scenes, meshes and point clouds, plus predicted depth,
  normals, cameras and point clouds for reconstruction.
- Four stages: HY-Pano 2.0 panorama generation, WorldNav trajectory planning,
  WorldStereo 2.0 expansion and WorldMirror 2.0 composition.
- WorldNav provides VLM-guided camera trajectory planning with obstacle-aware navigation;
  WorldLens is a 3DGS renderer with character support.
- No numeric world extent or metric scale is given.
- No VR mode is mentioned.
- Code and weights are released.

Licence [S26]: Tencent HY-WORLD 2.0 Community License. Territory excludes the EU, UK and
South Korea; royalty-free for the territory; separate Tencent licence required above 1
million monthly active users; Tencent claims no ownership in outputs.

Inferred: HunyuanWorld is a serious open alternative for an owned, self-hosted pipeline,
but it is not a route package. It gives no metric route, no documented 20-80 km extent and
no ready VR performance. It is more useful if the route geometry is authored separately
and a world model is used only for appearance.

## 10. Verdict and cheapest experiment

For the VR cycling app, Marble is a plausible visual-content supplier, not a route
supplier. It can produce a beautiful, metric-scalable splat scene with a collider and a
VR link, but it does not generate a long route, a road centreline, a terrain profile or a
gradient. Inferred: the 20-80 km target is two orders of magnitude beyond a documented
single world, and composing hundreds of worlds is not supported by any published stitching API;
the manual Compose path is limited by a 2M splat account counter against about 2M splats
per full world.

A 20-80 km product would therefore need our own route and terrain layer, with generated
worlds used as scenery tiles or hero views. The first question to answer is not "can
Marble make a road" but "how many metres of coherent, ridable road does one generation
contain, and what does it look like at cycling speed and close range".

Cheapest useful experiment, about 1.20-1.30 USD of API credits at current rates:

1. Generate one world with `marble-1.1` from a 360 panorama of a real country lane
   (1,500 credits, about 1.20 USD). A panorama avoids the 80-credit non-pano input charge
   and gives the model a coherent horizon and lighting. Pin `marble-1.1` explicitly
   because the default model is inconsistently documented [S6][S21].
2. Export the full SPZ/PLY splat and the collider mesh. PLY export is free; do not buy the
   3,500-credit HQ mesh for the first test [S20].
3. Read `metric_scale_factor` and `ground_plane_offset` from the SPZ metadata, scale the
   asset, and measure the world's bounding box and the straightest road length. Compare
   with the 20-80 km target and record the implied number of worlds [S7].
4. Load the scaled splat in Spark or SuperSplat, place a camera at cycling height and ride
   the road at 5-10 m/s. Check road continuity, close-up texture, artefacts at the edges,
   collider alignment and gradient from the collider mesh.
5. Open the Marble VR link on a headset and record frame rate and comfort. If the vendor
   link is not usable at speed, test a Spark/WebXR viewer next.
6. Only if the single world is useful, repeat once with a video or multi-image prompt
   (about 1,600 credits, about 1.28 USD) to test whether a forward-facing capture gives a
   longer coherent corridor.

Pass condition for the experiment: a single world must yield a clearly ridable road of
usable quality, and the measured extent must make the number of required worlds and
splats tractable. If a world yields only tens of metres, Marble is a hero-asset tool, not
a route foundation.

## Sources

All fetched 2026-11-17.

- [S1] World Labs Worlds API reference: https://docs.worldlabs.ai/api/reference/worlds/index
- [S2] Marble export file specifications: https://docs.worldlabs.ai/marble/export/specs.md
- [S3] Marble create overview: https://docs.worldlabs.ai/marble/create/index.md
- [S4] Marble models: https://docs.worldlabs.ai/marble/models.md
- [S5] Marble Expand guide: https://docs.worldlabs.ai/marble/create/prompt-guides/expand.md
- [S6] World API generate reference: https://docs.worldlabs.ai/api/reference/worlds/generate.md
- [S7] Rendering Marble SPZ files: https://docs.worldlabs.ai/api/rendering-spz.md
- [S8] World API FAQ: https://docs.worldlabs.ai/api/faq.md
- [S9] Marble release notes: https://docs.worldlabs.ai/marble/release-notes.md
- [S10] Marble Compose: https://docs.worldlabs.ai/marble/create/studio-tools/compose.md
- [S11] Marble Chisel basics: https://docs.worldlabs.ai/marble/create/chisel-tools/chisel-basics.md
- [S12] Marble video prompt tips: https://docs.worldlabs.ai/marble/create/prompt-guides/video-prompt.md
- [S13] Marble mesh export: https://docs.worldlabs.ai/marble/export/mesh.md
- [S14] World Labs, Generating Bigger and Better Worlds, 2025-09-16: https://www.worldlabs.ai/blog/bigger-better-worlds
- [S15] Marble FAQ: https://docs.worldlabs.ai/marble/support/faq.md
- [S16] Welcome to Marble: https://docs.worldlabs.ai/index.md
- [S17] Marble Spark export: https://docs.worldlabs.ai/marble/export/gaussian-splat/spark.md
- [S18] World Labs Terms of Service: https://www.worldlabs.ai/terms-of-service
- [S19] Marble account and billing: https://docs.worldlabs.ai/marble/support/account-billing.md
- [S20] World API pricing: https://docs.worldlabs.ai/api/pricing
- [S21] World API models: https://docs.worldlabs.ai/api/models.md
- [S22] HunyuanWorld 1.0 GitHub: https://github.com/Tencent-Hunyuan/HunyuanWorld-1.0
- [S23] HunyuanWorld 1.0 licence: https://raw.githubusercontent.com/Tencent-Hunyuan/HunyuanWorld-1.0/main/LICENSE
- [S24] HY-World 2.0 GitHub: https://github.com/Tencent-Hunyuan/HY-World-2.0
- [S25] HY-World 2.0 paper: https://arxiv.org/abs/2604.14268
- [S26] HY-World 2.0 licence: https://raw.githubusercontent.com/Tencent-Hunyuan/HY-World-2.0/main/License.txt
- [S27] World API rate limits: https://docs.worldlabs.ai/api/rate-limits.md

## Open questions for the experiment

- Does the 2,000,000 Compose splat counter apply to exported composition or only the
  Studio preview? The docs do not say.
- Does a 2M-splat world cover tens of metres or hundreds? Only a measured export answers
  it.
- Is `metric_scale_factor` stable across exports of the same world? The docs say to use
  the value returned with each world [S7].
- Does the VR link support a moving camera at cycling speed, or is it a static teleport
  viewer? The docs do not say.
