import numpy as np
import pytest

from georoute.geodesy import (
    WGS84,
    LocalFrame,
    ecef_from_enu_rotation,
    ecef_to_lla,
    lla_to_ecef,
    ned_to_ecef,
)

# ECEF from the POC's gpx_to_enu_raw.py at 766539d, pinned before it was retired; the ENU vector was
# recomputed through it (agrees with the POC to 3e-10 m).
LEGACY_ECEF = [
    ((51.1588400, -4.1242371, 207.768), (3998032.698023179, -288282.52527176886, 4944808.067010674)),
    ((51.1597, -4.1201, 215.0), (3997983.6959300623, -287988.8140330367, 4944873.705973929)),
    ((0.0, 0.0, 0.0), (6378137.0, 0.0, 0.0)),
    ((-33.8688, 151.2093, 58.0), (-4646093.477288303, 2553229.5358170713, -3534404.710910369)),
]
LEGACY_ENU = (289.42645872267667, 95.68750531048772, 7.224728597654355)

# Independent oracle: pyproj EPSG:4979 to EPSG:4978, around the 45 degree height-form switch and the poles.
PYPROJ_ECEF = [
    ((0.0, -4.12, -10000.0), (6351680.236509789, -457522.7031524877, 0.0)),
    ((0.0, -4.12, 0.0), (6361654.394158266, -458241.15927733626, 0.0)),
    ((0.0, -4.12, 100000.0), (6461395.970643033, -465425.7205258221, 0.0)),
    ((44.999999, -4.12, -10000.0), (4498863.645441996, -324061.0640859526, 4480277.262595434)),
    ((44.999999, -4.12, 0.0), (4505916.440074952, -324569.0892926847, 4487348.330283886)),
    ((44.999999, -4.12, 100000.0), (4576444.386404513, -329649.34136000625, 4558059.007168407)),
    ((45.0, -4.12, -10000.0), (4498863.567186133, -324061.0584490451, 4480277.341054054)),
    ((45.0, -4.12, 0.0), (4505916.361695995, -324569.0836469105, 4487348.408865919)),
    ((45.0, -4.12, 100000.0), (4576444.306794611, -329649.3356255649, 4558059.086984574)),
    ((45.000001, -4.12, -10000.0), (4498863.488930267, -324061.0528121374, 4480277.419512673)),
    ((45.000001, -4.12, 0.0), (4505916.283317035, -324569.07800113613, 4487348.487447952)),
    ((45.000001, -4.12, 100000.0), (4576444.227184706, -329649.3298911234, 4558059.166800741)),
    ((-45.0, -4.12, -10000.0), (4498863.567186133, -324061.0584490451, -4480277.341054054)),
    ((-45.0, -4.12, 0.0), (4505916.361695995, -324569.0836469105, -4487348.408865919)),
    ((-45.0, -4.12, 100000.0), (4576444.306794611, -329649.3356255649, -4558059.086984574)),
    ((89.999, -4.12, -10000.0), (111.23125415410779, -8.012182946979236, 6346752.313271989)),
    ((89.999, -4.12, 0.0), (111.405336045178, -8.024722371888338, 6356752.3132704655)),
    ((89.999, -4.12, 100000.0), (113.14615495588014, -8.150116620979357, 6456752.313255235)),
    ((90.0, -4.12, -10000.0), (3.902386876752737e-10, -2.8109579294246555e-11, 6346752.314245179)),
    ((90.0, -4.12, 0.0), (3.908494286871936e-10, -2.8153572043927735e-11, 6356752.314245179)),
    ((90.0, -4.12, 100000.0), (3.9695683880639267e-10, -2.859349954073955e-11, 6456752.314245179)),
    ((-90.0, -4.12, -10000.0), (3.902386876752737e-10, -2.8109579294246555e-11, -6346752.314245179)),
    ((-90.0, -4.12, 0.0), (3.908494286871936e-10, -2.8153572043927735e-11, -6356752.314245179)),
    ((-90.0, -4.12, 100000.0), (3.9695683880639267e-10, -2.859349954073955e-11, -6456752.314245179)),
]


@pytest.mark.parametrize(("lla", "ecef"), PYPROJ_ECEF)
def test_lla_to_ecef_matches_pyproj(lla, ecef):
    np.testing.assert_allclose(lla_to_ecef(*lla), ecef, rtol=0, atol=1e-6)


@pytest.mark.parametrize(("lla", "ecef"), PYPROJ_ECEF)
def test_ecef_to_lla_inverts_pyproj(lla, ecef):
    lat, _, h = ecef_to_lla(ecef)
    assert abs(lat - lla[0]) < 1e-9
    assert abs(h - lla[2]) < 1e-4


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
    back = ecef_to_lla(lla_to_ecef(*lla))
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
    north_40km = a.to_ecef([0.0, 40_000.0, 0.0])
    lat, lon, _ = ecef_to_lla(north_40km)
    ground = a.from_ecef(lla_to_ecef(lat, lon, 0.0))
    drop = 40_000.0**2 / (2 * WGS84.a)
    assert ground[2] == pytest.approx(-drop, rel=0.02)


def test_ned_velocity_rotates_to_ecef():
    np.testing.assert_allclose(ned_to_ecef(0.0, 0.0, (1.0, 0.0, 0.0)), (0.0, 0.0, 1.0), atol=1e-12)
    np.testing.assert_allclose(ned_to_ecef(0.0, 0.0, (0.0, 1.0, 0.0)), (0.0, 1.0, 0.0), atol=1e-12)
    np.testing.assert_allclose(ned_to_ecef(0.0, 0.0, (0.0, 0.0, 1.0)), (-1.0, 0.0, 0.0), atol=1e-12)


def test_ned_to_ecef_at_a_general_point_and_for_arrays():
    lat, lon = 51.16, -4.12
    rot = ecef_from_enu_rotation(lat, lon)
    np.testing.assert_allclose(rot.T @ rot, np.eye(3), atol=1e-15)
    assert np.linalg.det(rot) == pytest.approx(1.0)
    ned = np.array([[-0.776, -0.364, 0.125], [1.0, 2.0, -3.0]])
    expected = np.stack([rot @ [e, n, -d] for n, e, d in ned])
    np.testing.assert_allclose(ned_to_ecef(lat, lon, ned), expected, atol=1e-12)


def test_frames_compare_by_definition_and_cannot_be_mutated():
    a = LocalFrame.at(51.0, -4.0, 100.0, datum="ETRS89")
    assert a == LocalFrame.at(51.0, -4.0, 100.0, datum="ETRS89")
    assert hash(a) == hash(LocalFrame.at(51.0, -4.0, 100.0, datum="ETRS89"))
    assert a != LocalFrame.at(51.0, -4.0, 100.0, datum="WGS84")
    with pytest.raises(ValueError):
        a.origin_ecef[0] = 0.0
