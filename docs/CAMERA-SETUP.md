# Camera setup: 4 x Canon EOS 100D on the handlebar rig

Set every body identically, then check each against this list. The pipeline relies on the starred items; the rest protect image quality and consistency across cameras.

## Lens (EF-S 18-55)

| Setting | Value | Why |
|---|---|---|
| Zoom | 18 mm, ring taped | One fixed intrinsics set per camera |
| AF/MF switch | MF | No AF lag or focus-priority refusal |
| Focus | About 3 m, ring taped | Hyperfocal at 18 mm f/8 is about 2.1 m, so roughly 1.3 m to infinity is sharp. Field test 1 was at 0.8 m, which softened everything |
| Stabiliser (IS) switch | Off | Stabiliser shifts the optics between frames |

## Mode dial and exposure

| Setting | Value | Why |
|---|---|---|
| Mode | M | Fixed shutter and aperture |
| Shutter | 1/1000 s | Motion blur about 1.4 mm walking, 8 mm at 8 m/s |
| Aperture | f/8 | Depth of field and sharpness |
| ISO | Auto, maximum 3200 | Follows the light; the cap limits noise (dusk pushed field test 1 to 6400) |
| Metering | Evaluative | |
| Built-in flash | Down, never raised | Hot-shoe adapter in place, no flash |
| Drive | Single shooting | Each pulse is one photo |
| Viewfinder shooting | Live View off | Live View adds lag and may suppress X-sync |

## Shooting menus

| Setting | Value | Why |
|---|---|---|
| Image quality | JPEG Large Fine (5184 x 3456), no RAW | Sustains the trigger rate |
| Aspect ratio | 3:2 | Full sensor |
| White balance | Daylight (or Cloudy if overcast), never AWB | Consistent colour across frames and cameras |
| Picture Style | Neutral | Less sharpening and contrast to bake in |
| Auto Lighting Optimizer | Off | Otherwise tone changes frame to frame |
| Highlight tone priority | Off | |
| Long exposure noise reduction | Off | |
| High ISO noise reduction | Low | Keeps fine detail |
| Lens aberration correction (peripheral illumination, chromatic aberration) | Off | Canon warns enabling it greatly reduces the burst; f/8 keeps vignetting small |
| Mirror lock-up | Disable | Needs two presses per frame |
| Image review | Off | Battery and buffer |
| Beep | Disable | |
| *Release shutter without card* | **Disable** | No photo means no false sync edge |

## Setup menus

| Setting | Value | Why |
|---|---|---|
| *Auto power off* | **Disable** | A sleeping camera misses the first pulses |
| *File numbering* | **Continuous** | Numbers continue across cards and folders; gaps are how the pipeline spots a missing photo |
| Auto rotate | Off | No orientation tag on side-mounted cameras |
| Date/Time/Zone | UTC, set all four within a second or so | Only used for ordering checks, never timing |
| Sensor cleaning at power on/off | Disable | |
| Copyright information, author | `cam0` .. `cam3` | A second label besides the serial |

## Cards and power

- SDHC/SDXC UHS-I, U3/V30, at least 32 GB per camera (about 1250 photos, 9.5 GB per km walking).
- Format each card in its own camera before the session (continuous numbering survives formatting).
- DR-E12 DC coupler or fresh batteries in every body.

## Wiring and mapping (do this once, record it in the rig config)

- `camN` is defined by the hot-shoe sync input: cam0 = GP6, cam1 = GP7, cam2 = GP8, cam3 = GP9. The shutter lines all fire together, so only the hot-shoe cable decides which camera is which.
- Label each body and its card with its `camN`.
- Read each body's serial from a test shot: `exiftool -SerialNumber -FileNumber IMG_xxxx.JPG`, and put it in the rig config.

## Rig config (`rigs/<name>.toml`, copy the example)

Measure and fill in, then set `measured = true`:

- Rig reference point (choose one, for example the top of the V-slot bar on the bike's centre line) and its height above the road with the bike upright.
- For each camera: `folder` (`cam0` ..), `serial`, `role`, `position_in_rig_m` (lens front centre relative to the reference point: x right, y forward, z up), `yaw_pitch_roll_deg` (yaw left +, pitch up +, roll clockwise from behind +).
- Antenna reference point (`arp_in_rig_m`): base of the ANN-MB3 relative to the reference point.

Check it with `uv run georoute rig-check rigs/<name>.toml`.

## Pre-ride check (static, about 2 minutes)

1. Start a logger session, let it take about 20 pulses, stop it.
2. Copy each card's photos into `images/cam0` .. `images/cam3`.
3. `uv run georoute ingest <session> images --out check --rig rigs/<name>.toml`. Expect every camera matched, no errors.
4. To confirm the hot-shoe mapping: cover or switch off one camera for a few pulses and check that its `camN` column in `pulses.csv` is blank for those pulses.
