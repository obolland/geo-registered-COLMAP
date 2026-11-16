import json
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator
from test_path import FRAME, SyntheticGeoid, pose

from georoute import build
from georoute.build import BuildError, build_route
from georoute.cli import main
from georoute.ingest import IMAGES_SCHEMA
from georoute.rig import load_rig

EXAMPLE = Path(__file__).parents[1] / "rigs" / "handlebar-4cam.example.toml"
SCHEMAS = Path(__file__).parents[1] / "schemas"


class FakePoint:
    def __init__(self, xyz):
        self.xyz = np.asarray(xyz, float)


class FakeModel:
    def __init__(self, points):
        self.points3D = {index: FakePoint(point) for index, point in enumerate(points)}


def make_dataset(tmp_path, failures=()):
    rig = load_rig(EXAMPLE)
    pulses = range(3, 63)
    images = []
    poses = {}
    for pulse in pulses:
        origin = np.array([(pulse - 3) * 0.8, 0.0, rig.reference_height_above_road_m])
        for camera in rig.cameras:
            poses[(pulse, camera.index)] = pose(rig, camera.index, origin, np.eye(3))
            images.append(
                {
                    "path": f"cam{camera.index}/IMG_{pulse:04d}.JPG",
                    "camera": camera.index,
                    "pulse": pulse,
                    "t_mid_gps_s": pulse * 0.8,
                    "carr_soln": 2,
                    "flags": [],
                }
            )
    data = {
        "schema": IMAGES_SCHEMA,
        "session": "20261114T133719Z",
        "datum": FRAME.datum,
        "frame": {
            "id": "20261114T133719Z/rtk-fixed",
            "lat_deg": FRAME.lat_deg,
            "lon_deg": FRAME.lon_deg,
            "h_m": FRAME.h_m,
            "datum": FRAME.datum,
            "origin_ecef": FRAME.origin_ecef.tolist(),
        },
        "images": images,
    }
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.json").write_text(json.dumps({"failures": list(failures)}))
    images_json = tmp_path / "images.json"
    images_json.write_text(json.dumps(data))
    points = np.array([[x, y, 0.0] for x in np.arange(-3.0, 62.0, 0.25) for y in np.arange(-3.0, 3.01, 0.25)])
    return rig, workspace, images_json, poses, FakeModel(points), data


def install(monkeypatch, poses, model):
    monkeypatch.setattr(build, "_model_poses", lambda workspace: ("refined", poses, {}, {}, model))


def run_build(tmp_path, monkeypatch, failures=(), segment_length=20.0, build_id="b1"):
    rig, workspace, images_json, poses, model, data = make_dataset(tmp_path, failures)
    install(monkeypatch, poses, model)
    summary = build_route(
        workspace,
        images_json,
        rig,
        tmp_path / "routes",
        "route",
        segment_length_m=segment_length,
        build_id=build_id,
        geoid=SyntheticGeoid(),
        generated_utc="2026-11-16T12:00:00Z",
    )
    return summary, workspace, images_json, data


def schema(name):
    return json.loads((SCHEMAS / f"{name}-1.0.0.json").read_text())


def test_build_route_publishes_a_schema_valid_package_with_qa_and_plots(tmp_path, monkeypatch):
    summary, _, _, _ = run_build(tmp_path, monkeypatch)

    assert summary["status"] == "pass"
    assert summary["segments"] == 3
    assert summary["height_check"]["p50_m"] == pytest.approx(1.05, abs=0.001)
    final = Path(summary["package"])
    route = json.loads((final / "route.json").read_text())
    Draft202012Validator(schema("route")).validate(route)
    for folder in sorted((final / "segments").iterdir()):
        Draft202012Validator(schema("path")).validate(json.loads((folder / "path.json").read_text()))
        Draft202012Validator(schema("profile")).validate(json.loads((folder / "profile.json").read_text()))
    assert route["qa"]["status"] == "pass"
    assert route["provenance"]["parameters"]["smoothing"]["method"] == "cubic-smoothing-spline"
    assert route["provenance"]["parameters"]["segment_length_m"] == 20.0

    qa = json.loads(Path(summary["qa"]).read_text())
    assert qa["status"] == "pass"
    assert qa["checks"]["height_above_road"]["measured_pulses"] == 60
    for name in ("plan.png", "profile.png", "curvature.png"):
        assert (Path(summary["qa"]).parent / name).stat().st_size > 1000


def test_a_failing_registration_gate_still_publishes_the_route_marked_for_recapture(tmp_path, monkeypatch):
    summary, _, _, _ = run_build(tmp_path, monkeypatch, failures=["cam0 horizontal p95 5.2 cm"])

    assert summary["status"] == "fail"
    assert summary["recapture"] == ["seg_0000", "seg_0001", "seg_0002"]
    route = json.loads((Path(summary["package"]) / "route.json").read_text())
    assert route["qa"]["status"] == "fail"
    assert route["qa"]["recapture"] == summary["recapture"]
    assert all("gate-failed" in segment["quality"] for segment in route["segments"])


def test_a_missing_colmap_report_writes_a_refusal_qa_report_and_no_package(tmp_path, monkeypatch):
    rig, workspace, images_json, poses, model, _ = make_dataset(tmp_path)
    install(monkeypatch, poses, model)
    (workspace / "report.json").unlink()

    with pytest.raises(BuildError, match="colmap-report"):
        build_route(
            workspace,
            images_json,
            rig,
            tmp_path / "routes",
            "route",
            build_id="b1",
            geoid=SyntheticGeoid(),
            generated_utc="2026-11-16T12:00:00Z",
        )

    refusal = json.loads((tmp_path / "routes" / "qa" / "b1" / "report.json").read_text())
    assert refusal["status"] == "fail"
    assert "colmap-report" in refusal["refused"]
    assert not (tmp_path / "routes" / "route" / "b1").exists()


def test_an_inconsistent_frame_origin_refuses(tmp_path, monkeypatch):
    rig, workspace, images_json, poses, model, data = make_dataset(tmp_path)
    install(monkeypatch, poses, model)
    data["frame"]["origin_ecef"] = (np.array(data["frame"]["origin_ecef"]) + [1.0, 0.0, 0.0]).tolist()
    images_json.write_text(json.dumps(data))

    with pytest.raises(BuildError, match="origin_ecef"):
        build_route(
            workspace,
            images_json,
            rig,
            tmp_path / "routes",
            "route",
            build_id="b1",
            geoid=SyntheticGeoid(),
            generated_utc="2026-11-16T12:00:00Z",
        )


def test_route_build_cli_passes_and_honours_the_segment_length(tmp_path, monkeypatch):
    rig, workspace, images_json, poses, model, _ = make_dataset(tmp_path)
    install(monkeypatch, poses, model)
    monkeypatch.setattr(build, "load_geoid", lambda: SyntheticGeoid())

    status = main(
        [
            "route-build",
            str(workspace),
            "--images",
            str(images_json),
            "--rig",
            str(EXAMPLE),
            "--out",
            str(tmp_path / "routes"),
            "--route-id",
            "cli-route",
            "--segment-length",
            "25",
        ]
    )

    assert status == 0
    builds = list((tmp_path / "routes" / "cli-route").iterdir())
    assert len(builds) == 1
    route = json.loads((builds[0] / "route.json").read_text())
    assert len(route["segments"]) == 2


def test_route_build_cli_returns_one_for_a_qa_failure_and_two_for_a_refusal(tmp_path, monkeypatch):
    rig, workspace, images_json, poses, model, _ = make_dataset(tmp_path, failures=["registration bad"])
    install(monkeypatch, poses, model)
    monkeypatch.setattr(build, "load_geoid", lambda: SyntheticGeoid())

    failed = main(
        [
            "route-build",
            str(workspace),
            "--images",
            str(images_json),
            "--rig",
            str(EXAMPLE),
            "--out",
            str(tmp_path / "routes"),
            "--route-id",
            "failed",
        ]
    )
    assert failed == 1

    (workspace / "report.json").unlink()
    refused = main(
        [
            "route-build",
            str(workspace),
            "--images",
            str(images_json),
            "--rig",
            str(EXAMPLE),
            "--out",
            str(tmp_path / "routes"),
            "--route-id",
            "refused",
        ]
    )
    assert refused == 2
