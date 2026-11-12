# Trainer and sensor integration - research notes

Date of research: 2026-11-12. Labels: VERIFIED = read in the linked primary source on 2026-11-12; INFERRED = reasoned from verified facts; UNVERIFIED = secondary source, search-engine excerpt, or memory, not confirmed by reading the primary source. Several vendor support sites (support.wahoofitness.com, support.rouvy.com) sit behind a Cloudflare challenge and could not be fetched directly; claims from them are labelled UNVERIFIED (search excerpt).

## 1 Summary (top 10 findings)

1. FTMS v1.0.1 (Bluetooth SIG, adopted 2024-10-01, errata-only revision of v1.0 from 2017) gives everything a route sim needs: op code 0x11 Set Indoor Bike Simulation Parameters = wind speed SINT16 0.001 m/s, grade SINT16 0.01 %, Crr UINT8 0.0001, Cw UINT8 0.01. VERIFIED.
2. FTMS is strict request/response: Request Control (0x00) first, indications must be enabled, and a second write before the 0x80 response arrives is rejected with "Procedure Already In Progress". The app needs a single serialised command queue that coalesces grade updates. VERIFIED (spec), INFERRED (design).
3. Indoor Bike Data notifies "typically once per second" and the rate is not client-configurable, so the headset must run its own physics model at frame rate and treat trainer power as a 1 Hz input. VERIFIED (spec), INFERRED (design).
4. KICKR CORE 2: 1800 W max, +/-2 %, grade simulation -10 % to +16 %, Bluetooth (3 connections), ANT+, ANT+ FE-C, WiFi; Wahoo also lists Bluetooth FTMS and the Zwift protocol. Any route grade outside -10/+16 % is clipped by the trainer. VERIFIED (Wahoo product page), UNVERIFIED for the FTMS/Zwift protocol list (search excerpt of the Wahoo support page).
5. FTMS-first covers almost every trainer sold since about 2020 (Wahoo current range, all Tacx Garmin-era models via firmware, Elite since 2020, Saris H4, Zwift Hub, JetBlack, most Chinese brands). A robust web app should fall back in the same order Auuki uses: FTMS, then Tacx FE-C over BLE, then Wahoo legacy (a026...) on Cycling Power. There is no public market-share data, so "FTMS covers X %" can't be quantified. VERIFIED (Garmin matrix, Auuki source), INFERRED (coverage).
6. Web Bluetooth is the main platform risk. Quest Browser exposes `navigator.bluetooth` but shows no device picker (web.dev; Meta forum reports). Safari on visionOS has no Web Bluetooth (WebKit position: oppose). Android XR Chrome and Pico Browser are undocumented. Desktop Chrome/Edge work, and FTMS and Cycling Power are not on the GATT blocklist. A bridge (phone/desktop companion or ESP32) relaying over WebSocket is therefore needed for the Quest target. VERIFIED (blocklist, WebKit, web.dev), UNVERIFIED (Android XR, Pico).
7. Existing apps send coarse grade, not raw grade. Zwift scales grade with Trainer Difficulty (default 50 %), and secondary sources say it also halves descents. Rouvy smooths elevation for device-recorded routes. GoldenCheetah and Auuki use stepwise (uninterpolated, or RDP-simplified) per-segment grade. VERIFIED (Auuki, GoldenCheetah source), UNVERIFIED (Zwift and Rouvy specifics).
8. Elite Sterzo Smart uses a proprietary BLE service (347b0001...) with a challenge-response unlock, and the angle arrives as float32 little-endian degrees on 347b0030. The protocol has only been reverse-engineered (pycycling, FortiusANT, qdomyos-zwift, OpenBikeControl). Range is about +/-34 to 35 degrees. VERIFIED (open-source code), UNVERIFIED (range).
9. To show a map and count as a virtual ride on Strava, the file must carry lat/lon per record, a timestamp per record, and FIT sub_sport = virtual_activity (58). The upload goes through POST /uploads with the activity:write scope. Virtual rides match segments but are kept off real-world leaderboards. VERIFIED (Strava docs and support page, FIT SDK), UNVERIFIED (sub_sport rule is from a Strava community thread).
10. Pipeline consequence: publish a distance-indexed profile (about 1 m spacing) with elevation and a separately smoothed control grade (about 10 to 25 m effective window, quantised to 0.1 %, clamped per device), plus WGS84 lat/lon per sample for export. With RTK vertical noise of about 3 cm, grade noise is about 0.4 % over a 10 m baseline and about 0.2 % over 20 m. INFERRED.

## 2 FTMS (Bluetooth Fitness Machine Service)

Source: Bluetooth SIG, Fitness Machine Service v1.0.1, revision date 2024-10-01, adopted by the BoD 2024-10-01 (v1.0 adopted 2017-02-14; the HTML revision table prints "2-17-02-14"). v1.0.1 changes are listed as "Incorporated errata 9555, 10187, 10194, 16264, 16265, 18751, 18972, 22596, 23316" (no new procedures). HTML spec: https://www.bluetooth.com/specifications/specs/html/?src=ftms-v1-0-1_1756429637/FTMS_v1.0.1/out/en/index-en.html ; landing page https://www.bluetooth.com/specifications/specs/fitness-machine-service-1-0-1/ . VERIFIED.

### 2.1 Control Point op codes relevant to a riding sim (Table 4.15)

| Op code | Procedure | Parameter | Label |
|---|---|---|---|
| 0x00 | Request Control | none | VERIFIED |
| 0x01 | Reset | none | VERIFIED |
| 0x03 | Set Target Inclination | SINT16, percent, resolution 0.1 % | VERIFIED |
| 0x04 | Set Target Resistance Level | UINT8, unitless, resolution 0.1 | VERIFIED |
| 0x05 | Set Target Power (ERG) | (format not re-read) | VERIFIED (op code) |
| 0x11 | Set Indoor Bike Simulation Parameters | 6-octet array, below | VERIFIED |
| 0x13 | Spin Down Control | UINT8 | VERIFIED |
| 0x80 | Response Code (indicated by server): Request Op Code + Result Code | | VERIFIED |

### 2.2 Set Indoor Bike Simulation Parameters (0x11), Section 4.16.2.18, Table 4.20 - VERIFIED

| Field | Type | Units (as printed in spec) | Resolution | Range implied by type |
|---|---|---|---|---|
| Wind Speed | SINT16 | m/s | 0.001 | +/-32.767 m/s |
| Grade | SINT16 | percent | 0.01 % | +/-327.67 % |
| Crr | UINT8 | unitless | 0.0001 | 0 to 0.0255 |
| Cw (wind resistance coefficient) | UINT8 | unitless in the FTMS table; open-source clients treat it as kg/m (0.5 x rho x CdA) | 0.01 | 0 to 2.55 |

- Grade is quantised at 0.01 %, far finer than any trainer can render, so the limits come from the trainer and the source data, not the protocol. VERIFIED (resolution), INFERRED (consequence).
- The simulation procedure has no spec-defined min/max grade. The trainer clamps internally (KICKR CORE 2: -10 % to +16 %, Section 3). The Supported Inclination Range characteristic (4.12) applies only to the Inclination Target Setting feature. VERIFIED (4.12 text), INFERRED (device-side clamp).
- Auuki (open-source web app, MIT) encodes grade with a client-side clamp of -40 to +40 %, windSpeed +/-35.56 m/s, Crr 0 to 0.0254 (default 0.004) and Cw 0 to 1.86 kg/m (default 0.51). https://github.com/dvmarinoff/Auuki/blob/c50c3b64f81a05fe38397da8f324400b444a6424/src/ble/ftms/control-point.js lines 14-17. VERIFIED.
- Wind speed and Cw make the trainer's own model account for aero drag. If the app runs its own physics and wants the trainer to render only grade plus rolling, it can send windSpeed 0 and a small Cw, but trainers implement the mapping from these parameters to brake force differently. INFERRED; test per brand.

### 2.3 Control ownership and flow control - VERIFIED

- Every procedure other than Request Control "requires control permission from the Server" (4.16.2). Permission "remains valid until the connection is terminated, the notification of the Fitness Machine Status is sent with the value set to Control Permission Lost ..., or the Reset procedure ... is initiated by the Client" (4.16.2.1).
- Result codes: Success, Op Code Not Supported, Invalid Parameter (e.g. outside the supported range), Operation Failed, Control Not Permitted (Table 4.24).
- A write sent while an earlier control-point procedure is still pending gets ATT error "Procedure Already In Progress" (4.16.3). The procedure ends when the server indicates 0x80 (4.16.4). A write sent before the Control Point CCCD is configured for indications gets "Client Characteristic Configuration Descriptor Improperly Configured". The app must enable indications, serialise writes and wait for each 0x80 before sending the next grade, keeping only the latest pending grade. INFERRED (design).
- Fitness Machine Status includes "Indoor Bike Simulation Parameters Changed", so a change made by another client (for example a phone app also connected) can be observed, and "Control Permission Lost".

### 2.4 Indoor Bike Data (4.9) and ranges - VERIFIED

- Notified "at a regular interval, typically once per second while in a connection and the interval is not configurable by the Client" (4.9). Speed, cadence and power from FTMS therefore arrive at about 1 Hz, and the headset must integrate its own physics for 72 to 120 Hz rendering. INFERRED (consequence).
- Instantaneous Speed is mandatory. Cadence, Power, Resistance Level, Total Distance, Heart Rate and Elapsed Time are optional, per the Fitness Machine Feature characteristic. Field encodings (for example speed 0.01 km/h, cadence 0.5 rpm, power SINT16 W) are defined in the GATT Specification Supplement, which was not re-read. UNVERIFIED for the encodings.
- Supported Inclination Range (4.12), Supported Resistance Level Range (4.13) and Supported Power Range (4.14) each carry min, max and minimum increment. Each is exposed only if the matching Target Setting feature is supported.
- Power at higher than 1 Hz: the standard Cycling Power Service (0x1818) can be subscribed to in parallel on most trainers. The KICKR CORE 2 product page advertises a "Race Mode" that "broadcasts power data up to 10x faster than previous generation" (https://uk.wahoofitness.com/devices/indoor-cycling/bike-trainers/kickr-core-2-zwift-cog-and-click-buy, fetched 2026-11-12). VERIFIED (claim on page), UNVERIFIED (which protocol carries it).

## 3 Wahoo KICKR CORE 2

| Item | Value | Label / source |
|---|---|---|
| Max power | 1800 W | VERIFIED, Wahoo UK product page (URL above), 2026-11-12 |
| Accuracy | +/-2 % | VERIFIED, same |
| Grade simulation | max 16 %, min -10 % | VERIFIED, same |
| Connectivity | WiFi (auto firmware updates), Bluetooth up to 3 connections, ANT+, ANT+ FE-C | VERIFIED, same |
| Bluetooth FTMS, Zwift protocol | listed on Wahoo spec/support page | UNVERIFIED (search excerpt of https://support.wahoofitness.com/hc/en-us/articles/28096790110354; Cloudflare blocked fetch) |
| Direct Connect (wired/WiFi DIRCON) | Firmware notes headline "WiFi connectivity and Direct Connect"; a third-party spec page says no Ethernet port | UNVERIFIED and conflicting (search excerpts of https://support.wahoofitness.com/hc/en-us/articles/28414095351826 and https://the5krunner.com/specs/wahoo/kickr-core-2/) |
| Firmware | 2.5.24 / 3.5.24, WiFi 1.3.28, 2026-05-19 (Zwift Play controller support, sensor relay, KICKR Bridge); 2.5.13 2026-04-29; 2.5.4 2026-01-06 (KICKR Bridge relays Zwift Click v2 / Ride) | UNVERIFIED (search excerpt of release-notes page above) |
| Virtual shifting | Bundled with Zwift Cog and Click; shifting via Zwift protocol | VERIFIED (product page mentions Click controlling virtual gears), INFERRED (protocol) |
| Wahoo legacy BLE trainer control | Characteristic a026e005-0a7d-4ab3-97fa-f1500f9feb8b on Cycling Power, sim grade op 0x46 with grade encoded (g/100+1) x 32768 as UINT16 | VERIFIED in Auuki source https://github.com/dvmarinoff/Auuki/blob/c50c3b64f81a05fe38397da8f324400b444a6424/src/ble/wcps/control-point.js lines 68-108 (reverse-engineered; whether CORE 2 still exposes it is UNVERIFIED) |
| Response behaviour (time to reach a new resistance) | No first-party figure found | UNVERIFIED; measure (Section 10) |

What major apps use:
- Zwift: "if your BLE trainer has the FiTness Machine Service (FTMS) protocol, your trainer should automatically adjust resistance in-game" and the same for ANT+ FE-C. It recommends pairing FE-C over Power on ANT+. https://support.zwift.com/en_us/ant-and-ble-overview-for-cycling-B1S1BVgr.md (last updated 2026-03-17). VERIFIED. Virtual shifting uses the Zwift protocol over Bluetooth only. UNVERIFIED (Zwift Hub One page, search excerpt).
- TrainerRoad: supports "Bluetooth Smart" and "ANT+ FE-C". Its pages don't name FTMS. UNVERIFIED (search excerpt of https://support.trainerroad.com/hc/en-us/articles/201682804-Compatible-Devices-and-Sensors).
- MyWhoosh: open ANT+ FE-C and Bluetooth FTMS, plus FE-C over BLE and the old Wahoo protocol. UNVERIFIED (search excerpt; the fetched page https://mywhoosh.com/docs/which-devices-work-with-mywhoosh/ updated 2026-08-27 lists only hardware requirements).
- Rouvy: no first-party protocol statement read. UNVERIFIED.

## 4 Trainer coverage

| Brand / model family | BLE FTMS control | Other control | Label / source |
|---|---|---|---|
| Wahoo KICKR CORE 2, current KICKR | Yes | ANT+ FE-C, Wahoo legacy BLE, Zwift protocol | Sections 3 and 2 |
| Older Wahoo (e.g. 2016 KICKR) | No ("rely on a proprietary protocol") | Wahoo legacy BLE, ANT+ FE-C | UNVERIFIED (Zwift forum staff post, search excerpt) |
| Tacx (Garmin) Alpine, NEO 3M, NEO Bike Plus, NEO Bike Smart fw >= 4.5.0, NEO 2T, FLUX 2, FLUX S, Flow Smart, Vortex Smart, NEO 2, NEO, Bushido, Genius, Magnum Smart | Yes | ANT+ FE-C; Tacx FE-C over BLE (6e40fec1...); proprietary Tacx Trainer Service on newer models for Road Feel, weight, gears | VERIFIED, Garmin developer matrix https://developer.garmin.com/tacx-trainer/overview/ (no page date) |
| NEO Bike Smart fw < 4.0.0 | No | ANT+ FE-C only for control | VERIFIED, same |
| Elite (Direto, Suito, Justo, Avanti etc.) | "Elite smart trainers have supported FTMS since 2020", firmware via the Upgrado app; Real family not updatable | ANT+ FE-C, Elite proprietary | UNVERIFIED (search excerpt; Elite Zendesk https://elitesrl.zendesk.com/hc/en-us/articles/360010403479 not read) |
| Saris H4 | Yes | ANT+ FE-C | UNVERIFIED (Zwift Insider / reviews) |
| Saris H3 and older | Not confirmed | ANT+ FE-C, Saris proprietary BLE | UNVERIFIED |
| Zwift Hub / Hub One | Yes | ANT+ FE-C, Zwift protocol (virtual shifting); 1800 W, 16 % | UNVERIFIED (Zwift shop excerpt) |
| JetBlack, Magene, ThinkRider, Van Rysel/Decathlon, Wattbike Atom (current) | Generally FTMS plus ANT+ FE-C | | UNVERIFIED (no first-party pages read) |

Fallback order in a working web client (Auuki, 2026-11-07 HEAD c50c3b6): request filters for FTMS (0x1826), Tacx FE-C over BLE (6e40fec1-b5a3-f393-e0a9-e50e24dcca9e), Wahoo fitness machine (a026ee0b-...), and Cycling Power. The client then initialises exactly one control service, in priority FTMS > FEC > WCPS. The code comments warn that initialising more than one makes resistance "jumping between both services" on many trainers, which "is resolved only by power cycling". https://github.com/dvmarinoff/Auuki/blob/c50c3b64f81a05fe38397da8f324400b444a6424/src/ble/connectable.js lines 456-550 and src/ble/web-ble.js lines 9-92. VERIFIED.

Market share: no first-party or survey data was found that states what share of installed trainers speak FTMS. INFERRED: FTMS plus FE-C-over-BLE plus Wahoo legacy covers essentially every controllable smart trainer from the major brands made since about 2017. FTMS alone covers current-generation units and most units from about 2020 that have taken firmware updates. ANT+ FE-C-only devices (pre-firmware Tacx NEO Bike, older Elite Real family, some legacy CycleOps) cannot be reached from a browser without a bridge, because no browser exposes ANT+.

## 5 Grade handling in existing apps

| App | What it sends / how grade is derived | Label / source |
|---|---|---|
| Zwift | Trainer Difficulty slider scales the gradient sent to the trainer. Default 50 %; "Max" equals the real-world feel. It does not change virtual speed. | UNVERIFIED (Zwift news how-to, search excerpt; https://www.zwift.com/news/4306-zwift-how-to-adjust-your-trainer-difficulty returned 404 on direct fetch) |
| Zwift, descents | Sends half the downhill gradient, then applies Trainer Difficulty (so -10 % at 50 % difficulty sends -2.5 %), to avoid spin-out | UNVERIFIED (Zwift Insider, secondary: https://zwiftinsider.com/trainer-difficulty-on-descents/) |
| Rouvy | Rider-uploaded GPX: an "Elevation smoothing" toggle, ON for device-recorded files and OFF for planner files. Route Creator applies "accurate gradients ... smoothed for seamless compatibility with indoor smart trainers". | UNVERIFIED (search excerpts of https://support.rouvy.com/hc/en-us/articles/41824516431377 and https://rouvy.com/blog/rouvy-route-creator-tips-and-tricks; Cloudflare blocked fetch) |
| FulGaz, Wahoo SYSTM/RGT | No public description of grade derivation or smoothing found | UNVERIFIED (gap) |
| GoldenCheetah | Builds a distance/altitude/slope point list from the route. Slope is derived as `100 * rise / sqrt(h^2 - rise^2)`, which treats the distance step as slope length and divides by horizontal run. In strict gradient mode `gradientAt()` returns the uninterpolated gradient of the left bracketing point, a step function. Altitude is linearly interpolated. | VERIFIED, src/Train/ErgFile.cpp lines 846-876 and 2134-2150, file last changed 3e8c6fe 2026-01-13, https://github.com/GoldenCheetah/GoldenCheetah/blob/3e8c6fe0470e3cf1d14b119604653182a3e9ecd5/src/Train/ErgFile.cpp |
| Auuki | Course from FIT records: per-record slope = 100 x rise/run. A simplified copy is made with `g.simplify(points, 0.5, true)` (RDP-style, tolerance 0.5 in metres on the distance/altitude polyline), and slope plus segment length are computed per simplified segment. A new grade is dispatched only when the rider crosses into the next simplified segment, so the trainer sees piecewise-constant grade at change points, not a fixed rate. | VERIFIED, src/fit/local-course.js lines 5-18 and src/course.js lines 39-82 at c50c3b6 |
| Auuki physics | Virtual speed from power by solving the cubic with gravity, rolling and aero terms. Defaults: mass 85 kg, Crr 0.004, CdA 0.4, rho 1.275, drivetrain loss 2 %, optional wheel inertia | VERIFIED, src/physics.js lines 56-234 |

Typical errors in GPS-derived grade: no first-party error figures from Rouvy or FulGaz were found. INFERRED from general GNSS practice: consumer GPS altitude noise of several metres makes raw point-to-point grade over 5 to 10 m swing by tens of percent. Every app therefore smooths heavily (window tens to hundreds of metres) or relies on barometric or DEM elevation. This product's RTK plus splat source should be an order of magnitude better (Section 9).

## 6 Browser Bluetooth and bridges

| Browser | Web Bluetooth | Label / source |
|---|---|---|
| Desktop Chrome / Edge (Windows 10+, macOS, ChromeOS) | Yes. `requestDevice()` needs a user gesture. Linux needs a flag. | VERIFIED, https://developer.chrome.com/docs/capabilities/bluetooth |
| Chrome for Android (6.0+) | Yes | VERIFIED, same |
| Meta Quest Browser | APIs "pass feature detection, but don't actually show a picker that would let the user pair". Meta's browser-spec page (updated 2026-07-21) does not list Web Bluetooth. A community workaround sideloads stock Chromium. | VERIFIED (web.dev article https://web.dev/articles/pwas-on-oculus-2, undated; Meta page https://developers.meta.com/horizon/documentation/web/browser-specs/), UNVERIFIED (whether this is still true in late 2026; forum https://communityforums.atmeta.com/discussions/dev-quest/support-of-web-bluetooth-ble-in-quest-browser/1242380) |
| Safari on visionOS (Vision Pro) | No. WebKit standards position on Web Bluetooth is "oppose" (privacy, security, device independence). | VERIFIED, https://github.com/WebKit/standards-positions/issues/570 |
| Chrome on Android XR (Galaxy XR) | Not documented. Google's Android XR web page lists only WebXR modules. Chrome for Android supports Web Bluetooth, so it may work. | UNVERIFIED (https://developer.android.com/develop/xr/web read; no mention) |
| Pico Browser (Chromium-based) | Not documented | UNVERIFIED |
| PCVR via desktop Chrome/Edge | Yes (desktop) | INFERRED |

GATT blocklist: the Web Bluetooth blocklist blocks HID (0x1812), Nordic/TI/Cypress DFU services, FIDO, the Serial Number characteristic, and writes to the CCCD/SCCD descriptors via exclude-writes. It does not contain Fitness Machine 0x1826, Cycling Power 0x1818, Indoor Bike Data 0x2AD2, Control Point 0x2AD9 or CP Measurement 0x2A63. https://github.com/WebBluetoothCG/registries/blob/master/gatt_blocklist.txt fetched 2026-11-12. VERIFIED. The CCCD exclude-writes entry only blocks raw descriptor writes. Notifications are enabled through `startNotifications()`. INFERRED from the Web Bluetooth API design.

Bridge options (no first-party bridge product for WebXR was found):
- Companion app (phone or desktop): connects the trainer over native BLE (and ANT+ on desktop) and relays JSON over a LAN WebSocket or WebRTC to the headset page. This covers ANT+ FE-C-only trainers too. Mixed-content rule: an https page cannot open ws:// to a LAN IP, so the bridge needs wss with a trusted certificate or a cloud relay. INFERRED.
- ESP32 bridge: BLE central to the trainer, WiFi WebSocket to the headset. The same TLS issue applies. FortiusANT and qdomyos-zwift already re-expose trainers as FTMS/steering peripherals (VERIFIED that they implement Sterzo and FTMS characteristics; see Section 7 code links), but they bridge to BLE, not to WebSocket.
- Native wrapper: a Quest/Android XR APK hosting the WebXR content in a WebView, with native Android BLE. INFERRED; not verified that WebXR works inside a Quest WebView.
- Pairing UX: even where Web Bluetooth works, the chooser is 2D UI, so pairing must happen before `requestSession('immersive-vr')`. INFERRED; test.

## 7 Steering

Elite Sterzo Smart, reverse-engineered protocol. VERIFIED in pycycling source https://github.com/zacharyedwardbull/pycycling/blob/master/pycycling/sterzo.py (fetched 2026-11-12) and corroborated by UUIDs in WouterJD/FortiusANT (pythoncode/bleConstants.py), cagnulein/qdomyos-zwift (src/devices/elitesterzosmart/elitesterzosmart.cpp) and OpenBikeControl/bikecontrol (lib/bluetooth/devices/elite/elite_sterzo.dart):

| Item | Value |
|---|---|
| Service | 347b0001-7635-408b-8918-8ff3949ce592 (vendor) |
| Steering angle | 347b0030-...: notify, float32 little-endian, degrees (sign convention to test) |
| Control point | 347b0031-...: write. Sequence: write 0x03 0x10 to request a challenge; read the challenge from 347b0032 (indicate, bytes 2-3 big-endian); look up a 2-byte response in a fixed table (pycycling ships `sterzo-challenge-codes.dat`); write 0x03 0x11 r1 r2; then write 0x02 0x02 to start streaming |
| Challenge | 347b0032-...: indicate |
| Elite Rizer | Same 347b0030 characteristic UUID for steering (pycycling rizer.py; OpenBikeControl elite_rizer_protocol.dart) |

- No public Elite documentation exists. The unlock depends on a reverse-engineered code table, which is a legal/ToS and fragility risk (firmware could change it). INFERRED.
- Angle range: "34 degrees each way", resolution 0.1 degrees, ANT+ and Bluetooth. UNVERIFIED (retailer and review excerpts, e.g. https://dcrainmaker.com/2020/08/zwift-steering-sterzo.html). An ESP32 emulator maps -35 to +35 degrees. UNVERIFIED (https://github.com/matandoocorpo/Zwift-Steer README excerpt).
- Update rate: not documented. Measure it (Section 10).
- Zwift lock-in: Zwift's steering supports Sterzo Smart (and later Zwift Ride / Play steering), but the device itself is not Zwift-only. Open-source apps read it after the unlock. INFERRED from the code above.
- Alternatives: Zwift Play / Ride controllers (steering via Zwift protocol, also reverse-engineered by OpenBikeControl), Elite Rizer, phone or IMU-based DIY steering (Open Virtual Steering, https://github.com/Berg0162/NimBLE-Steering-Server, which emulates the same profile). UNVERIFIED beyond the repos named.
- Within a road-width corridor: map angle to lateral offset via a bicycle kinematic model (yaw rate = v x tan(delta) / wheelbase, with a gain and dead-band). Zwift-style steering is coarse, so heavy filtering is expected. INFERRED.

## 8 Strava export

- Upload: POST /uploads, OAuth scope activity:write, multipart file plus `data_type` (fit, fit.gz, tcx, tcx.gz, gpx, gpx.gz, json). Optional fields: `sport_type` (case-sensitive), `name`, `description`, `trainer` (1 = stationary), `commute`, `external_id`. "All files are required to include a time with each trackpoint or record". Activities without lat/lng are "auto marked as stationary". Elevation may be recomputed from Strava's elevation database "if barometric data seems inaccurate". Poll the upload status about once a second; mean processing time is under 2 s. https://developers.strava.com/docs/uploads/ and https://developers.strava.com/docs/reference/ fetched 2026-11-12. VERIFIED. VirtualRide is listed as a sport_type value. VERIFIED (uploads page).
- Virtual classification: "To classify an activity under a virtual sport type, the activity must be originally recorded as a virtual activity using a supported virtual trainer or treadmill". Virtual files "contain real GPS and time data, they will match segments" but are excluded from public real-world segment leaderboards. https://support.strava.com/hc/en-us/articles/216919587-Virtual-Trainer-Activities-on-Strava (dated 2026-09-17). VERIFIED.
- FIT specifics: sub_sport `virtualActivity` = 58 and `indoorCycling` = 6. Record fields include position_lat/long (sint32 semicircles, about 9 mm resolution), enhanced_altitude (uint32, scale 5, offset 500 m, so 0.2 m resolution) and grade (sint16, scale 100, %). VERIFIED, Garmin FIT JavaScript SDK https://raw.githubusercontent.com/garmin/fit-javascript-sdk/main/src/profile.js fetched 2026-11-12 (record field numbers not cross-checked against the Profile.xlsx). A Strava community answer says sub_sport must be VIRTUAL_ACTIVITY with GPS present for the map and correct local time to render. UNVERIFIED (https://communityhub.strava.com/developers-api-7/fit-upload-virtualrow-with-map-timestamp-handling-11447, 2025-10-27, community member, not staff-confirmed).
- Minimum per-record content for a mapped virtual ride: timestamp (1 Hz is conventional), lat, lon, altitude, cumulative distance, speed, power, cadence and heart rate if present. FIT file_id from a registered manufacturer/product, session sport = cycling and sub_sport = virtual_activity. INFERRED.
- API terms: the Strava API Agreement was not read in this pass. UNVERIFIED gap; review it for display, branding and data-retention rules before launch.

## 9 Implications for the pipeline outputs

Exact route data the trainer side needs (all INFERRED unless stated):

1. Primary index: cumulative along-route distance s (m), monotonic, sampled at 1 m (0.5 m is fine). At 40 mph (17.9 m/s) and 90 Hz render, the rider moves about 0.2 m per frame, so the app interpolates between samples. State whether s is horizontal (2D) or along-surface (3D). At 16 % the two differ by 1.3 %, which matters for distance totals and for GoldenCheetah-style slope formulas (Section 5). Recommendation: 3D along-surface s for odometry, plus a separate horizontal s_h.
2. Elevation z(s): orthometric height (state geoid model and datum), from RTK cm-level data, with per-sample 1-sigma vertical uncertainty. Grade noise from a central difference over baseline L is about sqrt(2) x sigma_z / L. With sigma_z = 3 cm that gives 0.85 % at L = 5 m, 0.42 % at 10 m, 0.21 % at 20 m and 0.085 % at 50 m. Splat-derived surface heights may be noisier than RTK and should be fused or used only where RTK is absent.
3. Two grade channels:
   - `grade_geom(s)`: rise over horizontal run in percent, lightly smoothed (window about 5 m), for visuals, camera pitch and stats.
   - `grade_ctrl(s)`: what the trainer receives. Zero-phase smoothing with an effective window of 10 to 25 m (tune by speed), quantised to 0.1 % and rate-limited to a few %/s. Keep the raw, unclamped value; the app applies device clamps (KICKR CORE 2 -10 to +16 %) and user difficulty/downhill scaling at runtime, not the pipeline.
   - Precompute `grade_ctrl` change points (segments where |delta| >= 0.2 %) so the app can send FTMS 0x11 only on change, at most about 1 to 2 Hz, matching the request/response rule (Section 2.3).
4. Physics inputs per sample: road surface class (asphalt, gravel, cobbles) for a Crr lookup (FTMS Crr resolution 0.0001), optional wind/exposure. Curvature/heading for steering and banking.
5. Lat/lon WGS84 per sample at about 1e-7 deg precision, for Strava/FIT export (records written at 1 Hz from s(t)) and for mapping. Also a downsampled GPX/FIT course for third-party devices.
6. Corridor geometry for steering: road centreline and left/right drivable half-widths per sample, in the same frame as the splat, so lateral offset can be clamped.
7. Metadata: CRS/frame transforms (ENU origin to WGS84), geoid model, smoothing parameters used, per-segment quality flags (RTK fix/float, splat coverage), so the app can fall back to heavier smoothing where quality is poor.
8. Loop/closure: whether the route is a loop, and the start/end elevation mismatch (Auuki wraps distance modulo course length).

## 10 Open risks and tests

| # | Risk | Test |
|---|---|---|
| 1 | Quest Browser Web Bluetooth still non-functional in late 2026 | On a Quest 3 with the current browser: feature-detect, call `requestDevice({filters:[{services:[0x1826]}]})` from a click, and record whether a chooser appears |
| 2 | Android XR Chrome and Pico Browser unknown | Same probe on a Galaxy XR and a Pico 4 Ultra |
| 3 | Chooser unusable inside an immersive session | Pair in 2D, enter `immersive-vr`, verify GATT notifications keep flowing and reconnect after headset sleep |
| 4 | Bridge latency and TLS on LAN | Prototype a phone/desktop relay over wss; measure end-to-end grade-command and power-data latency |
| 5 | KICKR CORE 2 grade response time and smoothing | Log FTMS 0x11 step commands (0 to 8 % and back) against 1 Hz power at constant cadence; fit a time constant. Repeat on Tacx and Elite units. |
| 6 | Trainer behaviour for wind/Cw/Crr | Vary Cw and Crr with grade fixed; check whether the trainer uses them (some may ignore them) |
| 7 | Simultaneous apps (Wahoo app, Zwift on phone) stealing control | Connect a second client and confirm "Control Permission Lost" handling |
| 8 | Multi-service clash (FTMS plus Wahoo legacy) | Confirm only one control service is initialised, per Auuki's warning |
| 9 | Sterzo unlock table validity on current firmware; legal review | Run pycycling against a current Sterzo; record angle range and notify rate |
| 10 | Strava acceptance of a non-registered "virtual trainer" | Upload a FIT with sub_sport 58, lat/lon, 1 Hz; confirm it is typed VirtualRide with a map. Read the Strava API Agreement. |
| 11 | Grade quality from the pipeline | Compare `grade_ctrl` on a known climb against a digital level / survey profile; check noise against Section 9 item 2 predictions |
| 12 | Unverified vendor claims (Wahoo FTMS/Direct Connect list, Elite FTMS list, Zwift descent halving) | Re-read the vendor pages from a normal browser, or confirm behaviour by inspecting the GATT table with nRF Connect |
