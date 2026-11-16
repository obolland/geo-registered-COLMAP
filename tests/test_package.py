import json

import numpy as np
import pytest
from test_path import FRAME, SyntheticGeoid, contact_at, make_images, profile_for

from georoute import package
from georoute.package import (
    PATH_STEP_M,
    PackageError,
    cut_segments,
    write_package,
)
from georoute.path import distance_profile, rider_path

META = {
    "datum": FRAME.datum,
    "geoid": {
        "name": "OSGM15",
        "grid": "uk_os_OSGM15_GB.tif",
        "sha256": "0" * 64,
        "datum": "ETRS89",
    },
    "session_id": "20261114T133719Z",
    "app_defaults": {"design_speed_mps": 18.0, "corridor_measured": False},
    "qa": {"status": "not-run"},
    "provenance": {
        "sessions": ["20261114T133719Z"],
        "rig_sha256": "0" * 64,
        "pipeline_commit": "abc",
        "tool_versions": {
            "georoute": "0.1.0",
            "python": "3.12",
            "numpy": "2.1",
            "scipy": "1.18",
            "pyproj": "3.8",
            "pycolmap": "4.2.1",
        },
        "parameters": {"segment_length_m": 10.0, "smoothing_bound_m": 0.1},
    },
}


def straight_route(length=25.0, segment_length=10.0):
    path, profile = profile_for(np.arange(0.0, length + 0.5, 0.5))
    return path, profile, cut_segments(path, profile, FRAME, segment_length)


def test_segments_cover_the_route_with_boundaries_and_half_metre_samples():
    path, _, route = straight_route()

    np.testing.assert_allclose(
        [(segment.s_start, segment.s_end) for segment in route.segments],
        [(0.0, 10.0), (10.0, 20.0), (20.0, 25.0)],
        atol=1e-9,
    )
    assert [segment.seg_id for segment in route.segments] == ["seg_0000", "seg_0001", "seg_0002"]
    for segment in route.segments:
        assert segment.path_s[0] == segment.s_start
        assert segment.path_s[-1] == segment.s_end
        assert np.diff(segment.path_s).max() <= PATH_STEP_M + 1e-9
        points = np.column_stack([segment.e, segment.n, segment.u])
        assert segment.bounds["min"] == pytest.approx(points.min(axis=0).tolist())
        assert segment.bounds["max"] == pytest.approx(points.max(axis=0).tolist())
    assert route.distance_m == pytest.approx(path.length_m)
    assert route.loop is False


def test_boundary_geometry_and_profile_values_are_continuous_across_segments():
    _, _, route = straight_route()

    left, right = route.segments[0], route.segments[1]
    left_ecef = left.origin.to_ecef([[left.e[-1], left.n[-1], left.u[-1]]])[0]
    right_ecef = right.origin.to_ecef([[right.e[0], right.n[0], right.u[0]]])[0]
    np.testing.assert_allclose(left_ecef, right_ecef, atol=1e-9)
    difference = np.angle(np.exp(1j * (left.heading_rad[-1] - right.heading_rad[0])))
    assert abs(difference) < 1e-5  # the two segment frames differ by meridian convergence
    assert left.curvature_1pm[-1] == pytest.approx(right.curvature_1pm[0], abs=1e-9)

    shared = 10.0
    left_index = np.flatnonzero(np.isclose(left.profile["s"], shared))[0]
    right_index = np.flatnonzero(np.isclose(right.profile["s"], shared))[0]
    for key in ("s_h", "h_ortho", "sigma_h", "grade", "curvature"):
        assert left.profile[key][left_index] == pytest.approx(right.profile[key][right_index], abs=1e-12)
    assert left.profile["quality"][left_index] == right.profile["quality"][right_index]


def test_neighbours_are_found_by_3d_proximity_even_across_a_hairpin():
    outbound = np.arange(0.0, 100.0, 0.5)
    inbound = np.arange(99.5, -0.5, -0.5)
    x = np.concatenate([outbound, inbound])
    n = np.concatenate([np.zeros_like(outbound), np.full_like(inbound, 5.0)])
    contacts = [contact_at(i, [e, nn, 0.0]) for i, (e, nn) in enumerate(zip(x, n, strict=True))]
    path = rider_path(contacts, FRAME)
    profile = distance_profile(path, make_images(range(len(contacts))), FRAME, SyntheticGeoid())
    route = cut_segments(path, profile, FRAME, 20.0)

    outbound_segment = route.segments[2]
    assert outbound_segment.s_start == pytest.approx(40.0)
    neighbours = [segment for segment in route.segments if segment.seg_id in outbound_segment.neighbours]
    assert any(segment.s_start >= 100.0 for segment in neighbours)


def test_profile_samples_reach_every_segment_boundary_for_a_non_integer_segment_length():
    path, profile = profile_for(np.arange(0.0, 25.5, 0.5))
    route = cut_segments(path, profile, FRAME, 7.3)

    for segment in route.segments:
        assert segment.profile["s"][0] == pytest.approx(segment.s_start)
        assert segment.profile["s"][-1] == pytest.approx(segment.s_end)
    for left, right in zip(route.segments[:-1], route.segments[1:], strict=True):
        assert left.profile["h_ortho"][-1] == pytest.approx(right.profile["h_ortho"][0])
        assert left.profile["grade"][-1] == pytest.approx(right.profile["grade"][0])


def test_segments_shorter_than_the_profile_step_still_get_boundary_samples():
    path, profile = profile_for(np.arange(0.0, 2.5, 0.5))
    route = cut_segments(path, profile, FRAME, 0.4)

    assert len(route.segments) > 2
    for segment in route.segments:
        assert segment.profile["s"][0] == pytest.approx(segment.s_start)
        assert segment.profile["s"][-1] == pytest.approx(segment.s_end)


def test_far_segments_on_a_straight_route_are_not_neighbours():
    _, _, route = straight_route(length=250.0, segment_length=20.0)

    assert "seg_0005" in route.segments[0].neighbours
    assert "seg_0006" in route.segments[0].neighbours  # exactly 100 m away
    assert "seg_0007" not in route.segments[0].neighbours
    assert "seg_0012" not in route.segments[0].neighbours


def test_the_package_is_written_to_staging_and_moved_into_place(tmp_path):
    _, _, route = straight_route()
    final = write_package(tmp_path, "route", "build", route, META)

    assert final == tmp_path / "route" / "build"
    assert (final / "route.json").is_file()
    assert (final / "segments" / "seg_0000" / "path.json").is_file()
    assert (final / "segments" / "seg_0000" / "profile.json").is_file()
    assert not any("staging" in path.name for path in (tmp_path / "route").iterdir())

    manifest = json.loads((final / "route.json").read_text())
    assert manifest["schema"] == "georoute.route/1.0.0"
    assert manifest["distance_m"] == pytest.approx(route.distance_m)
    assert manifest["loop"] is False
    assert manifest["splats"] == []
    assert manifest["provenance"]["pipeline_commit"] == "abc"
    assert manifest["segments"][0]["origin"]["ecef"] == pytest.approx(
        route.segments[0].origin.origin_ecef.tolist()
    )
    assert manifest["segments"][0]["path"] == "segments/seg_0000/path.json"


def test_path_and_profile_documents_carry_the_published_fields(tmp_path):
    _, _, route = straight_route()
    final = write_package(tmp_path, "route", "build", route, META)

    path_doc = json.loads((final / "segments" / "seg_0001" / "path.json").read_text())
    profile_doc = json.loads((final / "segments" / "seg_0001" / "profile.json").read_text())
    assert path_doc["schema"] == "georoute.path/1.0.0"
    assert path_doc["samples"][0] == {
        "s": 10.0,
        "e": path_doc["samples"][0]["e"],
        "n": path_doc["samples"][0]["n"],
        "u_road": path_doc["samples"][0]["u_road"],
        "heading_rad": path_doc["samples"][0]["heading_rad"],
        "curvature_1pm": path_doc["samples"][0]["curvature_1pm"],
    }
    assert profile_doc["schema"] == "georoute.profile/1.0.0"
    assert set(profile_doc["samples"][0]) == {
        "s",
        "s_h",
        "lat",
        "lon",
        "h_ortho",
        "sigma_h",
        "grade",
        "curvature",
        "corridor_left_m",
        "corridor_right_m",
        "surface",
        "quality",
    }
    assert profile_doc["samples"][0]["s"] == 10.0


def test_an_existing_build_is_never_overwritten(tmp_path):
    _, _, route = straight_route()
    write_package(tmp_path, "route", "build", route, META)

    with pytest.raises(PackageError, match="already exists"):
        write_package(tmp_path, "route", "build", route, META)


def test_a_multi_kilometre_route_keeps_positions_and_headings_consistent_across_frames():
    path, profile = profile_for(np.arange(0.0, 5000.5, 1.0))
    route = cut_segments(path, profile, FRAME, 2500.0)

    assert len(route.segments) == 2
    left, right = route.segments
    left_ecef = left.origin.to_ecef([[left.e[-1], left.n[-1], left.u[-1]]])[0]
    right_ecef = right.origin.to_ecef([[right.e[0], right.n[0], right.u[0]]])[0]
    np.testing.assert_allclose(left_ecef, right_ecef, atol=1e-6)

    def session_bearing(segment, index):
        heading = segment.heading_rad[index]
        local = np.array([np.cos(heading), np.sin(heading), 0.0])
        ecef = local @ segment.origin.ecef_from_enu.T
        session = ecef @ FRAME.ecef_from_enu
        return np.arctan2(session[1], session[0])

    difference = session_bearing(left, -1) - session_bearing(right, 0)
    assert abs(np.angle(np.exp(1j * difference))) < 1e-9


def test_a_failure_before_publication_leaves_neither_a_package_nor_staging(tmp_path, monkeypatch):
    _, _, route = straight_route()
    real = package._write_json

    def fail_on_route_json(path, data):
        if path.name == "route.json":
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(package, "_write_json", fail_on_route_json)

    with pytest.raises(OSError, match="disk full"):
        write_package(tmp_path, "route", "build", route, META)

    assert not (tmp_path / "route" / "build").exists()
    assert not any("staging" in path.name for path in (tmp_path / "route").iterdir())


def test_a_keyboard_interrupt_before_publication_cleans_staging(tmp_path, monkeypatch):
    _, _, route = straight_route()
    real = package._write_json

    def interrupt_on_route_json(path, data):
        if path.name == "route.json":
            raise KeyboardInterrupt
        real(path, data)

    monkeypatch.setattr(package, "_write_json", interrupt_on_route_json)

    with pytest.raises(KeyboardInterrupt):
        write_package(tmp_path, "route", "build", route, META)

    assert not (tmp_path / "route" / "build").exists()
    assert not any("staging" in path.name for path in (tmp_path / "route").iterdir())


def test_route_and_build_ids_must_be_plain_names(tmp_path):
    _, _, route = straight_route()
    with pytest.raises(PackageError, match="plain name"):
        write_package(tmp_path, "../route", "build", route, META)
    with pytest.raises(PackageError, match="plain name"):
        write_package(tmp_path, "route", "a/b", route, META)


def test_missing_route_metadata_refuses_before_creating_anything(tmp_path):
    _, _, route = straight_route()
    meta = {key: value for key, value in META.items() if key != "qa"}

    with pytest.raises(PackageError, match="qa"):
        write_package(tmp_path, "route", "build", route, meta)

    assert not (tmp_path / "route").exists()


def test_a_loop_is_flagged_when_start_and_end_are_within_five_metres():
    radius = 20.0
    circumference = 2 * np.pi * radius
    theta = np.arange(0.0, circumference, 0.5) / radius
    contacts = [contact_at(i, [radius * np.cos(a), radius * np.sin(a), 0.0]) for i, a in enumerate(theta)]
    path = rider_path(contacts, FRAME)
    profile = distance_profile(path, make_images(range(len(contacts))), FRAME, SyntheticGeoid())

    route = cut_segments(path, profile, FRAME, 100.0)

    assert route.loop is True


def test_segment_quality_is_the_union_of_its_profile_flags():
    contacts = [contact_at(i, [float(i), 0.0, 0.0], nominal=i == 5) for i in range(15)]
    images = make_images(range(15))
    for image in images:
        image["carr_soln"] = 1 if image["pulse"] == 6 else 2
    path = rider_path(contacts, FRAME)
    profile = distance_profile(path, images, FRAME, SyntheticGeoid())

    route = cut_segments(path, profile, FRAME, 10.0)

    assert route.segments[0].quality == ("rtk-float", "surface-nominal")
