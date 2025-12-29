#!/usr/bin/env node
/**
 * Convert a GPX track to local ENU coordinates (RAW, timestamp-preserved).
 *
 * Usage:
 *   node gpx_to_enu_raw.js input.gpx output.json
 *
 * Output JSON (array):
 * [
 *   {
 *     "timestamp": <unix_seconds>,
 *     "enu": [E, N, U]   // meters
 *   },
 *   ...
 * ]
 */

const fs = require("fs");
const { XMLParser } = require("fast-xml-parser");

// ---- basic helpers ----

function deg2rad(d) {
  return (d * Math.PI) / 180;
}

// WGS84 constants
const a = 6378137.0; // semi-major axis [m]
const f = 1 / 298.257223563;
const e2 = f * (2 - f); // first eccentricity squared

// lat[rad], lon[rad], h[m] -> ECEF [x,y,z] in meters
function llaToECEF(lat, lon, h) {
  const sinLat = Math.sin(lat);
  const cosLat = Math.cos(lat);
  const sinLon = Math.sin(lon);
  const cosLon = Math.cos(lon);

  const N = a / Math.sqrt(1 - e2 * sinLat * sinLat);
  const x = (N + h) * cosLat * cosLon;
  const y = (N + h) * cosLat * sinLon;
  const z = (N * (1 - e2) + h) * sinLat;
  return [x, y, z];
}

// Build ENU rotation matrix at origin lat0, lon0
function enuRotationMatrix(lat0, lon0) {
  const sinLat = Math.sin(lat0);
  const cosLat = Math.cos(lat0);
  const sinLon = Math.sin(lon0);
  const cosLon = Math.cos(lon0);

  // Rows of R: [East; North; Up] axes in ECEF basis
  return [
    [-sinLon, cosLon, 0], // East
    [-sinLat * cosLon, -sinLat * sinLon, cosLat], // North
    [cosLat * cosLon, cosLat * sinLon, sinLat], // Up
  ];
}

// Multiply 3x3 matrix M by 3-vector v
function mat3MulVec3(M, v) {
  return [
    M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
    M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
    M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2],
  ];
}

// ---- GPX parsing ----

function parseGPX(gpxXml) {
  const parser = new XMLParser({
    ignoreAttributes: false,
    attributeNamePrefix: "",
  });
  const obj = parser.parse(gpxXml);

  let trkpts = [];
  const gpx = obj.gpx || obj;

  if (!gpx.trk) {
    throw new Error("No <trk> element found in GPX");
  }

  const trks = Array.isArray(gpx.trk) ? gpx.trk : [gpx.trk];

  for (const trk of trks) {
    const segs = trk.trkseg
      ? Array.isArray(trk.trkseg)
        ? trk.trkseg
        : [trk.trkseg]
      : [];

    for (const seg of segs) {
      if (!seg.trkpt) continue;
      const pts = Array.isArray(seg.trkpt) ? seg.trkpt : [seg.trkpt];

      for (const p of pts) {
        const lat = parseFloat(p.lat);
        const lon = parseFloat(p.lon);
        const ele = p.ele !== undefined ? parseFloat(p.ele) : 0.0;

        if (!p.time) {
          throw new Error("GPX trackpoint is missing <time> tag");
        }

        const timestamp = new Date(p.time).getTime() / 1000.0;

        trkpts.push({ lat, lon, ele, timestamp });
      }
    }
  }

  if (trkpts.length === 0) {
    throw new Error("No <trkpt> points found in GPX");
  }

  return trkpts;
}

// ---- Main ----

async function main() {
  const [, , inPath, outPath] = process.argv;
  if (!inPath || !outPath) {
    console.error("Usage: node gpx_to_enu_raw.js input.gpx output.json");
    process.exit(1);
  }

  const gpxXml = fs.readFileSync(inPath, "utf8");
  const lla = parseGPX(gpxXml); // [{lat, lon, ele, timestamp}, ...]

  // Use FIRST point as ENU origin
  const originLat = lla[0].lat;
  const originLon = lla[0].lon;
  const originAlt = lla[0].ele;

  const lat0 = deg2rad(originLat);
  const lon0 = deg2rad(originLon);
  const R = enuRotationMatrix(lat0, lon0);

  const [x0, y0, z0] = llaToECEF(lat0, lon0, originAlt);

  const enuPoints = lla.map(({ lat, lon, ele, timestamp }) => {
    const latRad = deg2rad(lat);
    const lonRad = deg2rad(lon);
    const [x, y, z] = llaToECEF(latRad, lonRad, ele);

    const dx = x - x0;
    const dy = y - y0;
    const dz = z - z0;

    const enu = mat3MulVec3(R, [dx, dy, dz]);

    return {
      timestamp,
      enu: enu, // [E, N, U] in meters
    };
  });

  fs.writeFileSync(outPath, JSON.stringify(enuPoints, null, 2), "utf8");
  console.log(`✅ Wrote RAW ENU (timestamped) to ${outPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
