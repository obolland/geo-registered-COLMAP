import argparse
import json
import sys
from pathlib import Path

from georoute import __version__
from georoute.ingest import IngestError, ingest
from georoute.photos import MatchError, match_photos, read_photos
from georoute.report import format_report, session_report
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
    match.add_argument("images_root", type=Path, help="folder holding cam0 .. cam3")
    match.epilog = "exit status: 0 all matched, 1 mismatch, 2 session unreadable"
    run = sub.add_parser("ingest", help="time and position every capture photo")
    run.add_argument("session_dir", type=Path)
    run.add_argument("images_root", type=Path, help="folder holding cam0 .. cam3")
    run.add_argument(
        "--out", type=Path, required=True, help="output folder for images.json, report.json, track.png"
    )
    run.add_argument("--curtain-travel-ms", type=float, default=0.0)
    run.epilog = "exit status: 0 ok, 1 refused (integrity, matching or positioning), 2 session unreadable"
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
        args.json.write_text(json.dumps(report, indent=2))
    return 1 if report["errors"] else 0


def _match(args) -> int:
    try:
        session = load_session(args.session_dir)
    except SessionError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    try:
        matches = match_photos(session, read_photos(args.images_root))
    except MatchError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    for camera in sorted({m.photo.camera for m in matches}):
        mine = [m for m in matches if m.photo.camera == camera]
        warmups = sum(m.pulse.kind == "warmup" for m in mine)
        print(f"cam{camera} {len(mine)} photos matched ({warmups} warm-up, {len(mine) - warmups} capture)")
    return 0


def _ingest(args) -> int:
    try:
        summary = ingest(args.session_dir, args.images_root, args.out, args.curtain_travel_ms / 1000)
    except SessionError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    except (IngestError, MatchError, TrajectoryError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    flagged = ", ".join(f"{k} {v}" for k, v in summary["flagged"].items())
    print(
        f"{summary['images']} images timed and positioned ({summary['warmups_dropped']} warm-ups dropped); "
        f"flagged: {flagged}; written to {args.out}"
    )
    return 0
