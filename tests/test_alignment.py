import numpy as np
import pytest

from georoute.alignment import umeyama


def _rotation(axis, angle):
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * k @ k


def test_recovers_a_known_similarity():
    rng = np.random.default_rng(1)
    a = rng.normal(size=(50, 3)) * [30, 10, 2]
    r = _rotation([0.2, -0.5, 1.0], 0.7)
    b = 2.5 * a @ r.T + [100.0, -40.0, 7.0]
    s, r_est, t = umeyama(a, b)
    np.testing.assert_allclose(s, 2.5, rtol=1e-12)
    np.testing.assert_allclose(r_est, r, atol=1e-12)
    np.testing.assert_allclose(t, [100.0, -40.0, 7.0], atol=1e-9)


def test_reflected_data_gets_the_least_squares_scale_for_the_returned_rotation():
    rng = np.random.default_rng(2)
    a = rng.normal(size=(40, 3)) * [50, 5, 1]
    b = 2.0 * a @ np.diag([1.0, 1.0, -1.0])
    s, r, t = umeyama(a, b)
    assert np.isclose(np.linalg.det(r), 1.0)
    aa, bb = a - a.mean(0), b - b.mean(0)
    best_s = np.sum(bb * (aa @ r.T)) / np.sum(aa * aa)
    np.testing.assert_allclose(s, best_s, rtol=1e-12)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (np.zeros((0, 3)), np.zeros((0, 3))),
        (np.ones((1, 3)), np.ones((1, 3))),
        (np.ones((5, 3)), np.ones((5, 3))),
        (np.zeros((4, 3)), np.zeros((5, 3))),
        (np.zeros((4, 2)), np.zeros((4, 2))),
    ],
    ids=["empty", "single", "coincident", "shape-mismatch", "not-3d"],
)
def test_degenerate_inputs_refuse(a, b):
    with pytest.raises(ValueError):
        umeyama(a, b)


def test_noisy_near_planar_route_recovers_scale_rotation_and_translation():
    rng = np.random.default_rng(3)
    a = rng.normal(size=(200, 3)) * [400, 30, 0.05]
    r = _rotation([0.0, 0.0, 1.0], 2.0)
    b = 1.7 * a @ r.T + [10.0, 20.0, 30.0] + rng.normal(scale=0.01, size=a.shape)
    s, r_est, t = umeyama(a, b)
    assert s == pytest.approx(1.7, rel=1e-4)
    np.testing.assert_allclose(b, s * a @ r_est.T + t, atol=0.05)
