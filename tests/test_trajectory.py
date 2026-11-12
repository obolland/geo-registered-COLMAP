import numpy as np
import pytest

from georoute.geodesy import LocalFrame, ecef_to_lla
from georoute.session import WEEK_S, PvtTrack
from georoute.trajectory import TrajectoryError, antenna_positions, exposure_mid

FRAME = LocalFrame.at(51.1588, -4.1242, 207.0, datum="ETRS89")
T0 = 2444 * 604800 + 56269.4
PERIOD = 0.2


def _track(position, velocity, n=50, carr=2, h_acc=0.014, drop=(), t0=T0, quantise=False, fix=3):
    """A PVT track sampled from an analytic ENU trajectory; quantise=True rounds like the logger CSV."""
    t = t0 + PERIOD * np.arange(n)
    keep = np.array([i not in drop for i in range(n)])
    t = t[keep]
    enu = np.array([position(x - t0) for x in t])
    vel = np.array([velocity(x - t0) for x in t])
    lla = ecef_to_lla(FRAME.to_ecef(enu))
    ned = np.stack([vel[:, 1], vel[:, 0], -vel[:, 2]], axis=-1)
    if quantise:
        lla = np.column_stack([np.round(lla[:, :2], 7), np.round(lla[:, 2], 3)])
        ned = np.round(ned, 3)
    m = len(t)
    carr = np.broadcast_to(carr, n)[keep].copy()
    fix = np.broadcast_to(fix, n)[keep].copy()
    return PvtTrack(
        t,
        np.zeros(m, int),
        fix,
        carr,
        np.ones(m, int),
        np.full(m, 30),
        lla[:, 0],
        lla[:, 1],
        lla[:, 2],
        ned,
        np.full(m, h_acc),
        np.full(m, 0.011),
        ["1-2s"] * m,
    )


def _enu(result):
    return FRAME.from_ecef(result.ecef)


def test_constant_acceleration_is_exact():
    a, v0 = np.array([0.8, -0.3, 0.05]), np.array([5.0, 2.0, 0.1])
    track = _track(lambda t: v0 * t + 0.5 * a * t * t, lambda t: v0 + a * t)
    query = T0 + np.array([0.05, 1.234, 7.77])
    expected = [(v0 * (q - T0) + 0.5 * a * (q - T0) ** 2) for q in query]
    np.testing.assert_allclose(_enu(antenna_positions(track, query)), expected, atol=1e-5)


def test_a_tight_bend_at_speed_is_far_better_than_linear():
    r, v = 20.0, 8.0
    w = v / r
    track = _track(
        lambda t: [r * np.sin(w * t), r - r * np.cos(w * t), 0.0],
        lambda t: [v * np.cos(w * t), v * np.sin(w * t), 0.0],
    )
    query = T0 + np.arange(0.1, 9.0, 0.2)
    truth = np.stack([r * np.sin(w * (query - T0)), r - r * np.cos(w * (query - T0)), 0 * query], axis=-1)
    err = np.linalg.norm(_enu(antenna_positions(track, query)) - truth, axis=1)
    assert err.max() < 1e-4  # linear interpolation is out by v^2 h^2 / 8r = 16 mm here


def test_walking_bob_interpolation_error_on_ideal_input_is_a_few_millimetres():
    amp, f = 0.025, 2.0
    track = _track(
        lambda t: [1.4 * t, 0.0, amp * np.sin(2 * np.pi * f * t)],
        lambda t: [1.4, 0.0, amp * 2 * np.pi * f * np.cos(2 * np.pi * f * t)],
    )
    query = T0 + np.linspace(0.01, 9.5, 300)
    truth_u = amp * np.sin(2 * np.pi * f * (query - T0))
    assert np.abs(_enu(antenna_positions(track, query))[:, 2] - truth_u).max() < 0.003


def test_at_an_epoch_it_returns_that_epoch():
    track = _track(lambda t: [t, 0, 0], lambda t: [1, 0, 0])
    np.testing.assert_allclose(_enu(antenna_positions(track, [T0 + 1.0]))[0], [1.0, 0, 0], atol=1e-6)


def test_outside_the_track_refuses():
    track = _track(lambda t: [t, 0, 0], lambda t: [1, 0, 0])
    with pytest.raises(TrajectoryError, match="outside"):
        antenna_positions(track, [T0 - 0.01])
    with pytest.raises(TrajectoryError, match="outside"):
        antenna_positions(track, [T0 + 50 * PERIOD])


def test_quality_takes_the_worse_epoch_and_flags_gaps():
    carr = np.full(50, 2)
    carr[11] = 1
    track = _track(lambda t: [t, 0, 0], lambda t: [1, 0, 0], carr=carr, drop=(30, 31))
    q = antenna_positions(track, T0 + np.array([2.1, 2.3, 5.95, 1.0]), nav_period_s=PERIOD)
    assert q.carr_soln.tolist() == [1, 1, 2, 2]
    assert q.bracket_gap_s[2] == pytest.approx(0.6)
    assert q.flags == [["not-fixed"], ["not-fixed"], ["gap"], []]


def test_exposure_mid_adds_half_the_exposure_and_removes_half_the_curtain_travel():
    assert exposure_mid(t_command=100.0, delay_us=97000, exposure_s=0.001) == pytest.approx(100.0975)
    assert exposure_mid(100.0, 97000, 0.001, curtain_travel_s=0.004) == pytest.approx(100.0955)


def test_logger_rounding_limits_accuracy_to_about_a_centimetre():
    track = _track(lambda t: [5.0 * t, 2.0 * t, 0.0], lambda t: [5.0, 2.0, 0.0], quantise=True)
    query = T0 + np.linspace(0.05, 9.5, 200)
    truth = np.stack([5.0 * (query - T0), 2.0 * (query - T0), 0 * query], axis=-1)
    err = np.linalg.norm(_enu(antenna_positions(track, query)) - truth, axis=1)
    assert err.max() < 0.015


def test_interpolation_across_the_gps_week_boundary():
    start = 2445 * WEEK_S - 1.0
    track = _track(lambda t: [3.0 * t, 0, 0], lambda t: [3.0, 0, 0], n=10, t0=start)
    enu = _enu(antenna_positions(track, [2445 * WEEK_S + 0.1]))
    np.testing.assert_allclose(enu[0], [3.3, 0, 0], atol=1e-5)


def test_the_last_epoch_is_inside_the_track():
    track = _track(lambda t: [t, 0, 0], lambda t: [1, 0, 0], n=5)
    np.testing.assert_allclose(_enu(antenna_positions(track, [T0 + 0.8]))[0], [0.8, 0, 0], atol=1e-6)


def test_lost_3d_fix_is_flagged_and_accuracy_is_interpolated():
    fix = np.full(50, 3)
    fix[20] = 2
    h_acc = np.linspace(0.01, 0.059, 50)
    track = _track(lambda t: [t, 0, 0], lambda t: [1, 0, 0], fix=fix)
    track = PvtTrack(**{**track.__dict__, "h_acc_m": h_acc})
    q = antenna_positions(track, [T0 + 3.9, T0 + 0.1], nav_period_s=PERIOD)
    assert q.flags[0] == ["no-3d-fix"]
    assert q.h_acc_m[1] == pytest.approx(0.0105)
