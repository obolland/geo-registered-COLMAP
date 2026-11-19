import numpy as np
import pytest

from georoute.geodesy import LocalFrame, lla_to_ecef
from georoute.multisession import common_frame, doc_frame, images_in_frame


def _doc(frame, images, datum=None, session="s1"):
    return {
        "schema": "georoute.images/1.2.0",
        "session": session,
        "datum": datum or frame.datum,
        "frame": {
            "id": f"{session}/rtk-fixed",
            "lat_deg": frame.lat_deg,
            "lon_deg": frame.lon_deg,
            "h_m": frame.h_m,
            "datum": frame.datum,
            "origin_ecef": frame.origin_ecef.tolist(),
        },
        "images": images,
    }


def _image(ecef, velocity=(1.3, 0.0, 0.0)):
    return {
        "path": "cam0/IMG_0001.JPG",
        "camera": 0,
        "pulse": 2,
        "antenna_ecef": np.asarray(ecef, float).tolist(),
        "antenna_enu": [0.0, 0.0, 0.0],
        "velocity_enu": list(velocity),
        "has_prior": True,
        "prior_sigma_h_m": 0.014,
        "prior_sigma_v_m": 0.011,
    }


F1 = LocalFrame.at(51.15875, -4.12440, 208.0, "ETRS89")
F2 = LocalFrame.at(51.15940, -4.11880, 212.0, "ETRS89")


def test_the_common_frame_is_the_first_sessions_and_reprojects_through_ecef():
    point = lla_to_ecef(51.1590, -4.1210, 210.0)
    first = _doc(F1, [_image(point)])
    second = _doc(F2, [_image(point)], session="s2")

    frame = common_frame([first, second])
    assert frame.datum == F1.datum
    np.testing.assert_allclose(frame.origin_ecef, F1.origin_ecef)

    a = images_in_frame(first, frame)[0]["antenna_enu"]
    b = images_in_frame(second, frame)[0]["antenna_enu"]
    np.testing.assert_allclose(a, b, atol=1e-9)
    np.testing.assert_allclose(a, F1.from_ecef(point), atol=1e-9)
    # The same point sits hundreds of metres apart in the two sessions' own frames; the common frame
    # must not simply copy each session's antenna_enu.
    assert np.linalg.norm(F2.from_ecef(point) - F1.from_ecef(point)) > 100


def test_velocity_is_rotated_into_the_common_frame():
    point = lla_to_ecef(51.1590, -4.1210, 210.0)
    v_ecef = F2.ecef_from_enu @ np.array([1.3, 0.0, 0.5])
    second = _doc(F2, [_image(point, velocity=[1.3, 0.0, 0.5])], session="s2")
    frame = common_frame([_doc(F1, [_image(point)]), second])
    out = images_in_frame(second, frame)[0]["velocity_enu"]
    np.testing.assert_allclose(np.asarray(out) @ frame.ecef_from_enu.T, v_ecef, atol=1e-9)


def test_a_session_frame_is_recovered_from_the_document():
    doc = _doc(F2, [])
    frame = doc_frame(doc)
    np.testing.assert_allclose(frame.origin_ecef, F2.origin_ecef)
    assert frame.datum == "ETRS89"


def test_frames_with_different_datums_are_refused():
    first = _doc(F1, [])
    second = _doc(F2, [], datum="WGS84")
    second["frame"]["datum"] = "WGS84"
    with pytest.raises(ValueError, match="datum"):
        common_frame([first, second])
