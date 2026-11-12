import numpy as np


def umeyama(a: np.ndarray, b: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Similarity (s, R, t) minimising |b - (s R a + t)|^2 over rows, R a proper rotation."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 3:
        raise ValueError("a and b must both be (N, 3)")
    aa, bb = a - a.mean(0), b - b.mean(0)
    u, sv, vt = np.linalg.svd(bb.T @ aa / len(a))
    d = np.ones(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    var_a = np.mean(np.sum(aa**2, axis=1))
    if var_a <= 0:
        raise ValueError("a has no spread")
    s = np.sum(d * sv) / var_a
    return float(s), r, b.mean(0) - s * r @ a.mean(0)
