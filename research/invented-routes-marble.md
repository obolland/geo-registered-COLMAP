# Invented routes from generated 3D worlds

Status: draft, paused 2026-11-17 to build the near-camera prune for the next splat
retrain. Primary-source research resumed after that task. No AWS resource was used.

Question: could generated 3D worlds, mainly World Labs Marble, supply invented routes for
the VR cycling app? Routes need not be real, provided they are beautiful, interesting,
long enough (20-80 km target, shorter loops acceptable) and metrically scaled or
scalable.

## World Labs Marble

### API and generation

Official Worlds API reference, fetched 2026-11-17:
https://docs.worldlabs.ai/api/reference/worlds/index

- Generation is `POST /marble/v1/worlds:generate`, asynchronous, returning an
  `operation_id` to poll.
- Other endpoints: list worlds, get a world, export a world, delete a world.
- Local image or video inputs require a preparatory media-asset upload.
- World responses may include Marble links, thumbnails, panorama imagery, Gaussian
  splat files and mesh assets.
- Export supports cached PLY splat downloads and a separate high-quality mesh export.
- The page does not state accepted codecs, formats, resolution or duration limits, nor
  world extent, units or export request parameters.

### Export formats

Official export file specifications, fetched 2026-11-17:
https://docs.worldlabs.ai/marble/export/specs.md

| Export | Format | Size |
|---|---|---|
| 360 panorama | PNG | 2560 x 1280 equirectangular |
| Splats, full | SPZ | about 2M splats |
| Splats, low-res | SPZ | about 500k splats |
| Splats, full | PLY | about 2M splats, uncompressed |
| Splats, low-res | PLY | about 500k splats |
| Collider mesh | GLB | 100k-200k triangles |
| HQ mesh | GLB | about 600k triangles textured, or about 1M triangles vertex-coloured |

The default coordinate system is OpenCV: +X left, +Y down, +Z forward. The documented
OpenGL correction is to negate Y and Z and keep X. High-quality mesh generation can take
up to one hour and is limited to four requests per hour per user. The page does not
state file sizes, physical units or scale.

### API cost

Official API pricing, fetched 2026-11-17:
https://docs.worldlabs.ai/api/pricing

- 1 USD buys 1,250 API credits. Minimum purchase is 6,250 credits (5 USD). API credits
  do not expire.
- Marble 1.0 or 1.1: 1,500 credits for a panorama image input; 1,580 for text or
  non-panorama image; 1,600 for multi-image or video.
- Marble 1.1 Plus: the same base plus 0-1,500 variable credits for larger worlds, up to
  3,000-3,100 total depending on input.
- Draft: 150-250 credits.
- High-quality mesh export: 3,500 credits (2.80 USD) per new run. PLY splat export is
  free.
- Marble app credits and API credits are separate. The page states no rate limits.

### Open questions

Still to verify from primary sources:

- Input formats, image/video resolution and duration limits.
- World extent per generation and whether Marble 1.1 Plus expansion is chained,
  overlap-conditioned or independent.
- Whether output is metric, arbitrary scale, or has documented scale metadata or a
  scale control.
- Whether a road or path can be specified or extracted, and whether ground height,
  gradient or colliders are usable for a vehicle.
- Quality for fast motion and close-up viewing; documented artefacts.
- WebXR or headset support and performance figures.
- Ownership, commercial rights, attribution and restrictions in the official terms.
  The terms page fetch exceeded the response limit and must be retried with a narrower
  extraction prompt.
- The cheapest experiment: generate one Marble world with a panorama or text prompt,
  export SPZ and collider, measure scale against a known object, and ride through it in
  SuperSplat or a WebXR viewer.

## Tencent HunyuanWorld

Not yet verified from primary sources. Starting points for the next pass:

- Official GitHub: https://github.com/Tencent-Hunyuan/HunyuanWorld-1.0
- Project page: https://3d-models.hunyuan.tencent.com/world/
- HY-World 2.0 announcement, April 2026, needs a primary-source check.
- HunyuanWorld-Voyager technical report, for RGB and depth sequences and world
  exploration.

## Other 2026 tools

Not yet surveyed. Candidates to check include Google DeepMind Genie 3 and other
interactive world models, but only if they export geometry or a traversable metric
world rather than video.
