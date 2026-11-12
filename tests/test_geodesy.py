import numpy as np
import pytest

from georoute.geodesy import WGS84, LocalFrame, ecef_to_lla, lla_to_ecef, ned_to_ecef

# Outputs of the POC's gpx_to_enu_raw.py (commit 9cc9c68), pinned before it was retired.
LEGACY_ECEF = [
    ((51.1588400, -4.1242371, 207.768), (3998032.698023179, -288282.52527176886, 4944808.067010674)),
    ((51.1597, -4.1201, 215.0), (3997983.6959300623, -287988.8140330367, 4944873.705973929)),
    ((0.0, 0.0, 0.0), (6378137.0, 0.0, 0.0)),
    ((-33.8688, 151.2093, 58.0), (-4646093.477288303, 2553229.5358170713, -3534404.710910369)),
]
LEGACY_ENU = (289.42645872267667, 95.68750531048772, 7.224728597654355)


@pytest.mark.parametrize(("lla", "ecef"), LEGACY_ECEF)
def test_lla_to_ecef_matches_the_poc(lla, ecef):
    np.testing.assert_allclose(lla_to_ecef(*lla), ecef, rtol=0, atol=1e-6)


def test_poles_and_equator_follow_the_ellipsoid():
    b = WGS84.a * (1 - WGS84.f)
    np.testing.assert_allclose(lla_to_ecef(90.0, 0.0, 0.0), (0.0, 0.0, b), atol=1e-6)
    np.testing.assert_allclose(lla_to_ecef(0.0, 90.0, 10.0), (0.0, WGS84.a + 10.0, 0.0), atol=1e-6)


def test_lla_to_ecef_accepts_arrays():
    llas = np.array([p for p, _ in LEGACY_ECEF])
    out = lla_to_ecef(llas[:, 0], llas[:, 1], llas[:, 2])
    np.testing.assert_allclose(out, [e for _, e in LEGACY_ECEF], atol=1e-6)


EXTREMES = [(89.9999, 12.0, -30.0), (-45.0, -179.9, 8848.0)]


@pytest.mark.parametrize("lla", [p for p, _ in LEGACY_ECEF] + EXTREMES)
def test_ecef_to_lla_round_trips_to_sub_millimetre(lla):
    back = ecef_to_lla(*lla_to_ecef(*lla))
    np.testing.assert_allclose(back[:2], lla[:2], atol=1e-10)
    assert abs(back[2] - lla[2]) < 1e-4


def test_enu_in_a_local_frame_matches_the_poc():
    frame = LocalFrame.at(51.1588400, -4.1242371, 207.768, datum="ETRS89")
    enu = frame.from_ecef(lla_to_ecef(51.1597, -4.1201, 215.0))
    np.testing.assert_allclose(enu, LEGACY_ENU, atol=1e-6)


def test_frame_round_trip_and_origin():
    frame = LocalFrame.at(51.0, -4.0, 100.0, datum="ETRS89")
    np.testing.assert_allclose(frame.from_ecef(frame.origin_ecef), (0, 0, 0), atol=1e-9)
    pts = np.array([[10.0, -20.0, 3.0], [500.0, 400.0, -2.0]])
    np.testing.assert_allclose(frame.from_ecef(frame.to_ecef(pts)), pts, atol=1e-9)


def test_transform_between_frames_is_exact():
    a = LocalFrame.at(51.0, -4.0, 100.0, datum="ETRS89")
    b = LocalFrame.at(51.009, -4.0, 120.0, datum="ETRS89")
    p_a = np.array([3.0, 1000.0, -12.0])
    np.testing.assert_allclose(a.to(b, p_a), b.from_ecef(a.to_ecef(p_a)), atol=1e-9)


def test_frames_with_different_datums_refuse_to_mix():
    a = LocalFrame.at(51.0, -4.0, 0.0, datum="ETRS89")
    b = LocalFrame.at(51.0, -4.0, 0.0, datum="WGS84")
    with pytest.raises(ValueError, match="datum"):
        a.to(b, np.zeros(3))


def test_up_axis_tilts_with_distance_so_a_single_frame_biases_grade():
    a = LocalFrame.at(51.0, -4.0, 0.0, datum="ETRS89")
    far = lla_to_ecef(51.0 + 40_000 / 111_250, -4.0, 0.0)
    assert a.from_ecef(far)[2] < -120  # ground 40 km north sits about 126 m below the origin plane


def test_ned_velocity_rotates_to_ecef():
    np.testing.assert_allclose(ned_to_ecef(0.0, 0.0, (1.0, 0.0, 0.0)), (0.0, 0.0, 1.0), atol=1e-12)
    np.testing.assert_allclose(ned_to_ecef(0.0, 0.0, (0.0, 1.0, 0.0)), (0.0, 1.0, 0.0), atol=1e-12)
    np.testing.assert_allclose(ned_to_ecef(0.0, 0.0, (0.0, 0.0, 1.0)), (-1.0, 0.0, 0.0), atol=1e-12)
