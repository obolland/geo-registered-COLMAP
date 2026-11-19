import argparse
import json
import sys
from pathlib import Path

import numpy as np

from georoute import __version__
from georoute.ingest import IngestError, camera_types_for, ingest, photo_sources
from georoute.photos import MatchError, match_photos
from georoute.report import format_report, session_report
from georoute.rig import RigError, load_rig
from georoute.session import SessionError, load_session
from georoute.trajectory import TrajectoryError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="georoute")
    parser.add_argument("--version", action="version", version=f"georoute {__version__}")
    sub = parser.add_subparsers(dest="command")
    report = sub.add_parser("session-report", help="check a logger session's integrity")
    report.add_argument("session_dir", type=Path)
    report.add_argument("--json", type=Path, help="also write the report as JSON")
    report.epilog = "exit status: 0 ok, 1 integrity errors, 2 session unreadable"
    match = sub.add_parser("match", help="match each camera's photos to the session's pulses")
    match.add_argument("session_dir", type=Path)
    match.add_argument("images_root", type=Path, help="folder holding the camera folders (cam0, cam1, ...)")
    match.add_argument("--rig", type=Path, help="rig config giving camera folders and body serials")
    match.add_argument(
        "--camera-fault",
        type=int,
        action="append",
        default=[],
        metavar="N",
        help="camera N malfunctioned this session: allow it no photos, or photos lost at the end",
    )
    match.epilog = "exit status: 0 all matched, 1 mismatch or bad rig, 2 session unreadable"
    run = sub.add_parser("ingest", help="time and position every capture photo")
    run.add_argument("session_dir", type=Path)
    run.add_argument("images_root", type=Path, help="folder holding the camera folders (cam0, cam1, ...)")
    run.add_argument(
        "--out", type=Path, required=True, help="output folder for images.json, report.json, track.png"
    )
    run.add_argument("--rig", type=Path, help="rig config giving camera folders, serials and curtain travel")
    run.add_argument(
        "--camera-fault",
        type=int,
        action="append",
        default=[],
        metavar="N",
        help="camera N malfunctioned this session: allow it no photos, or photos lost at the end",
    )
    run.add_argument(
        "--allow-unmeasured-rig", action="store_true", help="accept a rig with placeholder geometry"
    )
    run.add_argument(
        "--curtain-travel-ms", type=float, help="overrides the rig's value (default 0 without a rig)"
    )
    run.epilog = "exit status: 0 ok, 1 refused (integrity, matching or positioning), 2 session unreadable"
    prep = sub.add_parser("colmap-prepare", help="COLMAP workspace: features per camera and camera priors")
    prep.add_argument("images_json", type=Path, help="images.json from ingest")
    prep.add_argument("images_root", type=Path, help="the images folder ingest was run on")
    prep.add_argument("--rig", type=Path, required=True)
    prep.add_argument("--out", type=Path, required=True, help="workspace folder")
    prep.add_argument(
        "--session",
        nargs=3,
        action="append",
        default=[],
        metavar=("IMAGES_JSON", "IMAGES_ROOT", "RIG_TOML"),
        help="an additional capture session: its images.json, photo root and rig",
    )
    prep.add_argument("--pulses", help="only pulses FIRST-LAST, for a quick subset")
    run = sub.add_parser("colmap-run", help="prepare, match, map, refine priors and report in one go")
    run.add_argument("images_json", type=Path)
    run.add_argument("images_root", type=Path)
    run.add_argument("--rig", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument(
        "--session",
        nargs=3,
        action="append",
        default=[],
        metavar=("IMAGES_JSON", "IMAGES_ROOT", "RIG_TOML"),
        help="an additional capture session: its images.json, photo root and rig",
    )
    run.add_argument("--pulses", help="only pulses FIRST-LAST")
    run.add_argument("--mapper", choices=("incremental", "global"), default="incremental")
    run.epilog = "exit status: 0 report passes its gates, 1 refused or gates failed (see report.json)"
    remap = sub.add_parser(
        "colmap-map", help="map, refine and report an already prepared and matched workspace"
    )
    remap.add_argument("workspace", type=Path)
    remap.add_argument("images_root", type=Path)
    remap.add_argument("--rig", type=Path, required=True)
    remap.add_argument("--mapper", choices=("incremental", "global"), default="incremental")
    rep = sub.add_parser("colmap-report", help="re-check a workspace's reconstruction against priors and rig")
    rep.add_argument("workspace", type=Path)
    rep.add_argument("--rig", type=Path, required=True)
    cal = sub.add_parser("rig-calibrate", help="calibrated rig config from a workspace's reconstruction")
    cal.add_argument("workspace", type=Path)
    cal.add_argument("--rig", type=Path, required=True)
    cal.add_argument("--out", type=Path, required=True)
    rig = sub.add_parser("rig-check", help="validate a rig config and print its geometry")
    rig.add_argument("rig_toml", type=Path)
    route = sub.add_parser("route-build", help="build the rider path, distance profile and route package")
    route.add_argument("workspace", type=Path, help="COLMAP workspace with sparse/refined and report.json")
    route.add_argument("--images", type=Path, required=True, help="images.json from ingest")
    route.add_argument("--rig", type=Path, required=True)
    route.add_argument("--out", type=Path, required=True, help="routes folder for <route_id>/<build_id>")
    route.add_argument("--route-id", required=True)
    route.add_argument("--segment-length", type=float, default=1000.0, metavar="METRES")
    route.epilog = "exit status: 0 all QA gates pass, 1 built with a failing segment, 2 refused"
    export = sub.add_parser("splat-export", help="export one splat training block from a refined workspace")
    export.add_argument("workspace", type=Path, help="COLMAP workspace with sparse/refined and priors.json")
    export.add_argument("--images-root", type=Path, required=True, help="folder holding cam0..camN")
    export.add_argument("--route", type=Path, required=True, help="published route package directory")
    export.add_argument("--rig", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True, help="folder for <block_id>")
    export.add_argument("--block-start", type=float, required=True, metavar="METRES")
    export.add_argument("--block-length", type=float, default=100.0, metavar="METRES")
    export.add_argument("--margin", type=float, default=25.0, metavar="METRES")
    export.add_argument("--data-factor", type=int, default=4)
    export.add_argument("--holdout-every", type=int, default=10)
    export.add_argument("--point-radius", type=float, default=60.0, metavar="METRES")
    export.add_argument("--block-id")
    export.epilog = "exit status: 0 exported, 1 refused or failed"
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "session-report":
        return _session_report(args)
    if args.command == "match":
        return _match(args)
    if args.command == "ingest":
        return _ingest(args)
    if args.command == "colmap-prepare":
        return _colmap_prepare(args)
    if args.command == "colmap-run":
        return _colmap_run(args)
    if args.command == "colmap-map":
        return _colmap_map(args)
    if args.command == "colmap-report":
        return _colmap_report(args)
    if args.command == "rig-calibrate":
        return _rig_calibrate(args)
    if args.command == "rig-check":
        return _rig_check(args)
    if args.command == "route-build":
        return _route_build(args)
    if args.command == "splat-export":
        return _splat_export(args)
    parser.print_help()
    return 0


def _session_report(args) -> int:
    try:
        report = session_report(load_session(args.session_dir))
    except SessionError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    print(format_report(report))
    if args.json:
        try:
            args.json.write_text(json.dumps(report, indent=2))
        except OSError as e:
            print(f"ERROR can't write {args.json}: {e}", file=sys.stderr)
            return 1
    return 1 if report["errors"] else 0


def _match(args) -> int:
    try:
        session = load_session(args.session_dir)
    except SessionError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    try:
        report = session_report(session)
        if report["errors"]:
            raise MatchError("session integrity errors: " + "; ".join(report["errors"]))
        warnings: list[str] = []
        rig = _rig(args)
        photos, serials = photo_sources(args.images_root, rig, warnings)
        matches = match_photos(
            session,
            photos,
            serials,
            warnings,
            faulty=set(args.camera_fault),
            camera_types=camera_types_for(session, rig),
        )
    except (MatchError, RigError, OSError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"WARNING {warning}", file=sys.stderr)
    for camera in sorted({m.photo.camera for m in matches}):
        mine = [m for m in matches if m.photo.camera == camera]
        warmups = sum(m.pulse.kind == "warmup" for m in mine)
        print(f"cam{camera} {len(mine)} photos matched ({warmups} warm-up, {len(mine) - warmups} capture)")
    return 0


def _ingest(args) -> int:
    try:
        curtain = None if args.curtain_travel_ms is None else args.curtain_travel_ms / 1000
        summary = ingest(
            args.session_dir,
            args.images_root,
            args.out,
            curtain,
            _rig(args),
            allow_unmeasured_rig=args.allow_unmeasured_rig,
            camera_faults=set(args.camera_fault),
        )
    except SessionError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    except (IngestError, MatchError, TrajectoryError, RigError, OSError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    flagged = ", ".join(f"{k} {v}" for k, v in summary["flagged"].items())
    for warning in summary["warnings"]:
        print(f"WARNING {warning}", file=sys.stderr)
    print(
        f"{summary['images']} images timed and positioned ({summary['warmups_dropped']} warm-ups dropped); "
        f"flagged: {flagged}; written to {args.out}"
    )
    return 0


def _rig_check(args) -> int:
    try:
        rig = load_rig(args.rig_toml)
    except (RigError, OSError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    print(
        f"rig {rig.name} ({'measured' if rig.measured else 'NOT MEASURED: placeholder geometry'}), "
        f"sha256 {rig.sha256[:12]}"
    )
    print(
        f"antenna lever arm (reference to phase centre) {rig.lever_arm_m.round(3).tolist()} m; "
        f"reference {rig.reference_height_above_road_m} m above the road; "
        f"curtain travel {rig.curtain_travel_s * 1000:g} ms"
    )
    for cam in rig.cameras:
        arm = cam.position_in_rig_m - rig.lever_arm_m
        axis = rig.rig_from_camera(cam.index)[:, 2]
        print(
            f"cam{cam.index} {cam.role!r} folder {cam.folder}, serial {cam.serial or 'unset'}: "
            f"antenna-to-camera {arm.round(3).tolist()} m ({np.linalg.norm(arm):.3f} m), "
            f"looking along {axis.round(3).tolist()} in the rig frame"
        )
    return 0


def _rig(args):
    return load_rig(args.rig) if args.rig else None


def _colmap_prepare(args) -> int:
    from georoute.multisession import load_session_spec, prepare_sessions
    from georoute.reconstruct import prepare

    try:
        pulses = _pulse_range(args.pulses)
        if args.session:
            specs = [load_session_spec(args.images_json, args.images_root, args.rig)]
            specs += [load_session_spec(*extra) for extra in args.session]
            summary = prepare_sessions(specs, args.out, pulses)
        else:
            summary = prepare(args.images_json, args.images_root, args.out, load_rig(args.rig), pulses)
    except (RigError, ValueError, OSError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    sessions = f"; sessions {', '.join(summary['sessions'])}" if summary.get("sessions") else ""
    print(
        f"{summary['images']} images, {summary['cameras']} cameras, {summary['priors']} priors "
        f"(frame {summary['frame_id']}{sessions}); workspace {args.out}"
    )
    return 0


def _colmap_run(args) -> int:
    from georoute.reconstruct import ReconstructionError, match, prepare

    try:
        pulses = _pulse_range(args.pulses)
        rig = load_rig(args.rig)
        print("prepare:", prepare(args.images_json, args.images_root, args.out, rig, pulses), flush=True)
        matched = match(args.out)
        print(
            f"match: {matched['verified_pairs']} verified pairs, "
            f"{len(matched['images_without_verified_pairs'])} photos unmatched",
            flush=True,
        )
    except (RigError, ValueError, OSError, KeyError, ReconstructionError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    return _map_refine_report(args.out, args.images_root, rig, args.mapper)


def _colmap_map(args) -> int:
    try:
        rig = load_rig(args.rig)
    except RigError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    return _map_refine_report(args.workspace, args.images_root, rig, args.mapper)


def _map_refine_report(workspace: Path, images_root: Path, rig, mapper: str) -> int:
    import time

    from georoute.reconstruct import ReconstructionError, reconstruct, refine, report

    try:
        started = time.monotonic()
        mapped = reconstruct(workspace, images_root, mapper=mapper)
        print(
            f"map ({mapper}): {mapped['registered']} of {mapped['images']} registered in "
            f"{(time.monotonic() - started) / 60:.1f} min",
            flush=True,
        )
        print("refine:", refine(workspace, rig), flush=True)
        result = report(workspace, rig)
    except (RigError, ValueError, OSError, KeyError, ReconstructionError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    for failure in result["failures"]:
        print(f"GATE {failure}", file=sys.stderr)
    print(f"report: {workspace / 'report.json'}")
    return 1 if result["failures"] else 0


def _rig_calibrate(args) -> int:
    from georoute.calibrate import CalibrationError
    from georoute.reconstruct import calibrate_rig

    try:
        result = calibrate_rig(args.workspace, args.rig, args.out)
    except (RigError, CalibrationError, OSError, ValueError, KeyError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    for camera, c in result["cameras"].items():
        print(
            f"{camera}: yaw/pitch/roll {[round(x, 2) for x in c['yaw_pitch_roll_deg']]}, "
            f"spread {c['spread_deg']:.3f} deg over {c['pulses']} pulses"
        )
    print(
        f"optical centre {result['axial_offset_m'] * 1000:.0f} mm behind the lens front; "
        f"written to {args.out}"
    )
    return 0


def _pulse_range(text: str | None) -> tuple[int, int] | None:
    if text is None:
        return None
    first, sep, last = text.partition("-")
    if not (sep and first.isdigit() and last.isdigit() and int(first) <= int(last)):
        raise ValueError(f"--pulses must be FIRST-LAST, got {text!r}")
    return int(first), int(last)


def _colmap_report(args) -> int:
    from georoute.reconstruct import report

    try:
        result = report(args.workspace, load_rig(args.rig))
    except (RigError, OSError, ValueError, KeyError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    for camera, r in result["prior_residuals"].items():
        if r["images"]:
            print(
                f"{camera}: prior residual p95 horizontal {r['horizontal_p95_m'] * 100:.1f} cm, "
                f"vertical {r['vertical_p95_m'] * 100:.1f} cm over {r['images']} fixed photos"
            )
    for failure in result["failures"]:
        print(f"GATE {failure}", file=sys.stderr)
    print(f"report: {args.workspace / 'report.json'}")
    return 1 if result["failures"] else 0


def _route_build(args) -> int:
    from georoute.build import build_route

    try:
        print(f"building route {args.route_id} ...", flush=True)
        summary = build_route(
            args.workspace,
            args.images,
            load_rig(args.rig),
            args.out,
            args.route_id,
            segment_length_m=args.segment_length,
        )
    except Exception as e:
        # A build command should name the failure rather than dump a traceback, including for pycolmap and
        # filesystem errors. KeyboardInterrupt and SystemExit are BaseException and still pass through.
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    for segment in summary["recapture"]:
        print(f"GATE {segment} failed QA and is on the recapture list", file=sys.stderr)
    check = summary["height_check"]
    if check.get("measured_pulses"):
        print(
            f"height above road p5/p50/p95 {check['p5_m']:.3f}/{check['p50_m']:.3f}/{check['p95_m']:.3f} m "
            f"against {check['expected_m']:.3f} m nominal; "
            f"{check.get('nominal_pulses', 0)} nominal-surface pulses"
        )
    print(
        f"{summary['segments']} segments, {summary['distance_m']:.1f} m, loop {summary['loop']}, "
        f"QA {summary['status']} (recapture {summary['recapture'] or 'none'})"
    )
    print(f"package: {summary['package']}")
    print(f"qa: {summary['qa']}")
    return 1 if summary["status"] == "fail" else 0


def _splat_export(args) -> int:
    from georoute.splat import SplatExportError, export_block

    try:
        print(f"exporting splat block from {args.workspace} ...", flush=True)
        result = export_block(
            args.workspace,
            args.images_root,
            args.route,
            load_rig(args.rig),
            args.out,
            args.block_start,
            block_length=args.block_length,
            margin=args.margin,
            data_factor=args.data_factor,
            holdout_every=args.holdout_every,
            point_radius_m=args.point_radius,
            block_id=args.block_id,
        )
    except (SplatExportError, RigError, OSError, ValueError, KeyError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    counts = result["counts"]
    print(
        f"block {args.block_start:g}-{args.block_start + args.block_length:g} m: "
        f"{counts['train_images']} train images, {counts['heldout_images']} held out, "
        f"{counts['points']} points from {counts['input_points']} input"
    )
    print(f"block: {Path(args.out) / (args.block_id or f's{args.block_start:g}-l{args.block_length:g}')}")
    return 0
