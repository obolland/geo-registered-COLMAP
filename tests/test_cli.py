import subprocess
import sys

import georoute


def test_version_command_prints_package_version():
    result = subprocess.run(
        [sys.executable, "-m", "georoute", "--version"], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == f"georoute {georoute.__version__}"


def test_colmap_commands_parse():
    from georoute.cli import build_parser

    args = build_parser().parse_args(
        ["colmap-run", "i.json", "img", "--rig", "r.toml", "--out", "ws", "--pulses", "1-5"]
    )
    assert args.command == "colmap-run" and args.pulses == "1-5"
    assert build_parser().parse_args(["rig-calibrate", "ws", "--rig", "r.toml", "--out", "c.toml"]).command


def test_pulse_ranges_are_strict():
    import pytest

    from georoute.cli import _pulse_range

    assert _pulse_range("10-20") == (10, 20) and _pulse_range(None) is None
    for bad in ("10", "20-10", "a-b", "-5"):
        with pytest.raises(ValueError):
            _pulse_range(bad)


def test_colmap_map_parses():
    from georoute.cli import build_parser

    args = build_parser().parse_args(["colmap-map", "ws", "img", "--rig", "r.toml", "--mapper", "global"])
    assert args.command == "colmap-map" and args.mapper == "global"


def test_colmap_prepare_parses_additional_sessions():
    from georoute.cli import build_parser

    args = build_parser().parse_args(
        [
            "colmap-prepare",
            "s1/images.json",
            "s1",
            "--rig",
            "s1.toml",
            "--out",
            "ws",
            "--session",
            "s2/images.json",
            "s2",
            "s2.toml",
        ]
    )
    assert args.session == [["s2/images.json", "s2", "s2.toml"]]
    assert (
        build_parser().parse_args(["colmap-prepare", "i.json", "img", "--rig", "r", "--out", "w"]).session
        == []
    )


def test_rig_calibrate_parses_a_session():
    from georoute.cli import build_parser

    args = build_parser().parse_args(["rig-calibrate", "ws", "--out", "c.toml", "--session", "s2"])
    assert args.session == "s2" and args.rig is None
