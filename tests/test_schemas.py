import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError
from test_package import META, straight_route

from georoute.package import write_package

SCHEMAS = Path(__file__).parents[1] / "schemas"


def load(name):
    return json.loads((SCHEMAS / f"{name}-1.0.0.json").read_text())


def validator(name):
    return Draft202012Validator(load(name))


def test_all_schemas_are_valid_draft_2020_12():
    for name in ("route", "path", "profile"):
        Draft202012Validator.check_schema(load(name))


@pytest.fixture
def package(tmp_path):
    _, _, route = straight_route()
    final = write_package(tmp_path, "route", "build", route, META)
    paths = [json.loads((path / "path.json").read_text()) for path in sorted(final.glob("segments/*"))]
    profiles = [json.loads((path / "profile.json").read_text()) for path in sorted(final.glob("segments/*"))]
    return {
        "route": json.loads((final / "route.json").read_text()),
        "path": paths[0],
        "profile": profiles[0],
        "paths": paths,
        "profiles": profiles,
    }


def test_a_built_package_validates_against_every_schema(package):
    validator("route").validate(package["route"])
    for document in package["paths"]:
        validator("path").validate(document)
    for document in package["profiles"]:
        validator("profile").validate(document)


def test_a_minor_version_may_add_optional_fields_that_readers_ignore(package):
    route = copy.deepcopy(package["route"])
    path = copy.deepcopy(package["paths"][0])
    profile = copy.deepcopy(package["profiles"][0])
    route["future_route_field"] = {"anything": 1}
    route["segments"][0]["future_segment_field"] = True
    path["future_path_field"] = "ignored"
    path["samples"][0]["future_sample_field"] = 2
    profile["samples"][0]["future_sample_field"] = "ignored"

    validator("route").validate(route)
    validator("path").validate(path)
    validator("profile").validate(profile)


@pytest.mark.parametrize(
    ("document", "field"),
    [
        ("route", "distance_m"),
        ("route", "segments"),
        ("route", "provenance"),
        ("path", "samples"),
        ("profile", "samples"),
    ],
)
def test_a_missing_required_field_is_rejected(package, document, field):
    broken = copy.deepcopy(package[document])
    del broken[field]

    with pytest.raises(ValidationError):
        validator(document).validate(broken)


@pytest.mark.parametrize(
    ("document", "edit"),
    [
        ("route", lambda d: d["segments"][0]["origin"].update(lat_deg=100.0)),
        ("path", lambda d: d["samples"][0].update(heading_rad=9.0)),
        ("profile", lambda d: d["samples"][0].update(lat=100.0)),
        ("profile", lambda d: d["samples"][0].update(corridor_left_m=-1.0)),
        ("profile", lambda d: d["samples"][0].update(quality="rtk-float")),
    ],
)
def test_out_of_range_or_wrong_typed_values_are_rejected(package, document, edit):
    broken = copy.deepcopy(package[document])
    edit(broken)

    with pytest.raises(ValidationError):
        validator(document).validate(broken)


def test_a_schema_mismatch_is_rejected(package):
    broken = copy.deepcopy(package["route"])
    broken["schema"] = "georoute.route/2.0.0"

    with pytest.raises(ValidationError):
        validator("route").validate(broken)
