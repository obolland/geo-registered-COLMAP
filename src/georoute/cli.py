import argparse
import json
import sys
from pathlib import Path

from georoute import __version__
from georoute.report import format_report, session_report
from georoute.session import SessionError, load_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="georoute")
    parser.add_argument("--version", action="version", version=f"georoute {__version__}")
    sub = parser.add_subparsers(dest="command")
    report = sub.add_parser("session-report", help="check a logger session's integrity")
    report.add_argument("session_dir", type=Path)
    report.add_argument("--json", type=Path, help="also write the report as JSON")
    report.epilog = "exit status: 0 ok, 1 integrity errors, 2 session unreadable"
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "session-report":
        return _session_report(args)
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
