"""Features, filters, regression, gaze estimator."""

import math

import numpy as np

from gazer.core.features import GAZE_CORE, GAZE_DIM, extract_features
from gazer.core.filters import GazeStabilizer, OneEuro, RollingPercentile
from gazer.core.gaze import GazeEstimator
from gazer.core.regression import PolyRidge
from gazer.core.tracker import FaceObservation


def synthetic_face(iris_dx=0.0, iris_dy=0.0, roll_deg=0.0, nose_dx=0.0, shift=(0.0, 0.0), scale=1.0):
    """A crude but geometrically consistent landmark set (pixels)."""
    P = np.zeros((478, 3))
    c = np.array([640.0, 360.0])
    def put(i, x, y):
        P[i, :2] = (x, y)
    # eye A (image-left): corners 33/133, lids 159/145, ear ring
    for (c1, c2, up, lo, ring, cx) in [
        (33, 133, 159, 145, (160, 158, 153, 144), -60.0),
        (362, 263, 386, 374, (385, 387, 373, 380), 60.0),
    ]:
        put(c1, cx - 30, 0); put(c2, cx + 30, 0)
        put(up, cx, -9); put(lo, cx, 9)
        put(ring[0], cx - 10, -9); put(ring[1], cx + 10, -9)
        put(ring[2], cx + 10, 9); put(ring[3], cx - 10, 9)
    for base, cx in ((468, -60.0), (473, 60.0)):
        for k in range(5):
            ang = k * 2 * math.pi / 5
            put(base + k, cx + iris_dx + (0 if k == 0 else 6 * math.cos(ang)),
                iris_dy + (0 if k == 0 else 6 * math.sin(ang)))
    put(1, nose_dx, 60); put(4, nose_dx, 50)
    put(234, -150, 40); put(454, 150, 40)
    put(10, 0, -120); put(152, 0, 200)
    r = math.radians(roll_deg)
    R = np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])
    P[:, :2] = (P[:, :2] @ R.T) * scale + c + np.asarray(shift)
    return FaceObservation(0.0, 1280, 720, P, {"eyeBlinkLeft": 0.0}, None)


def test_features_iris_direction_and_roll_invariance():
    base = extract_features(synthetic_face())
    right = extract_features(synthetic_face(iris_dx=8))
    assert right.left.u > base.left.u + 0.1 and right.right.u > base.right.u + 0.1
    rolled = extract_features(synthetic_face(iris_dx=8, roll_deg=20))
    assert abs(rolled.left.u - right.left.u) < 1e-6
    assert abs(rolled.roll - 20) < 1e-6
    far = extract_features(synthetic_face(iris_dx=8, scale=0.6))
    assert abs(far.left.u - right.left.u) < 1e-6  # distance invariant
    assert right.gaze_vector.shape == (GAZE_DIM,)


def test_features_head_signals():
    base = extract_features(synthetic_face())
    turned = extract_features(synthetic_face(nose_dx=30))
    assert turned.yaw > base.yaw
    moved = extract_features(synthetic_face(shift=(100, 0)))
    assert moved.head_point[0] > base.head_point[0]
    up = extract_features(synthetic_face(shift=(0, -50)))
    assert up.head_point[1] < base.head_point[1]


def test_one_euro_smooths_noise_but_tracks_steps():
    f = OneEuro(min_cutoff=1.0, beta=0.01)
    rng = np.random.default_rng(0)
    out = [f(100 + rng.normal(0, 5), i / 30) for i in range(60)]
    assert np.std(out[30:]) < 3
    for i in range(60, 120):
        v = f(500, i / 30)
    assert abs(v - 500) < 5


def test_stabilizer_holds_still_during_fixation_and_follows_jumps():
    s = GazeStabilizer(smoothing=0.7)
    rng = np.random.default_rng(1)
    pts = [s(960 + rng.normal(0, 12), 540 + rng.normal(0, 12), i / 30) for i in range(90)]
    tail = np.array(pts[45:])
    assert np.max(np.linalg.norm(tail - tail.mean(axis=0), axis=1)) < 12
    for i in range(90, 120):
        p = s(300, 200, i / 30)
    assert math.hypot(p[0] - 300, p[1] - 200) < 40


def test_rolling_percentile():
    r = RollingPercentile(size=100, q=90, every=5)
    for i in range(100):
        r.push(i)
    assert 85 <= r.value <= 95


def test_poly_ridge_recovers_nonlinear_map_and_rejects_outliers():
    rng = np.random.default_rng(3)
    n, k = 600, 6
    X = rng.normal(size=(n, k + 3))
    Y = np.column_stack([0.5 + 0.2 * X[:, 0] + 0.05 * X[:, 0] ** 2 + 0.03 * X[:, 4],
                         0.5 + 0.2 * X[:, 1] - 0.04 * X[:, 0] * X[:, 1]])
    Y_noisy = Y + rng.normal(0, 0.005, Y.shape)
    Y_noisy[:30] = rng.uniform(0, 1, (30, 2))  # 5% garbage labels (user looked away)
    m = PolyRidge(k)
    rep = m.fit(X, Y_noisy)
    assert rep.n_used < n  # some outliers dropped
    Xt = rng.normal(size=(200, k + 3))
    Yt = np.column_stack([0.5 + 0.2 * Xt[:, 0] + 0.05 * Xt[:, 0] ** 2 + 0.03 * Xt[:, 4],
                          0.5 + 0.2 * Xt[:, 1] - 0.04 * Xt[:, 0] * Xt[:, 1]])
    err = np.mean(np.linalg.norm(m.predict(Xt) - Yt, axis=1))
    assert err < 0.02
    m2 = PolyRidge.from_arrays(m.to_arrays("p_"), "p_")
    assert np.allclose(m2.predict(Xt), m.predict(Xt))


def _gaze_data(n, rng, bias=(0.0, 0.0)):
    X = rng.normal(size=(n, GAZE_DIM)) * 0.3
    Y = np.column_stack([0.5 + X[:, 0], 0.5 + X[:, 1]]) + np.asarray(bias)
    return X, Y


def test_gaze_estimator_fit_predict_and_implicit_learning(tmp_path):
    rng = np.random.default_rng(4)
    X, Y = _gaze_data(300, rng)
    g = GazeEstimator()
    g.fit(X, Y)
    p = g.predict(X[0])
    assert abs(p[0] - Y[0, 0]) < 0.02
    # The world drifts: every true target is now offset by +0.05 in x.
    for i in range(40):
        x, y = _gaze_data(1, rng, bias=(0.05, 0.0))
        g.learn_from_click(x[0], tuple(y[0]))
    x, y = _gaze_data(50, rng, bias=(0.05, 0.0))
    preds = np.array([g.predict(v) for v in x])
    assert np.mean(np.abs(preds[:, 0] - y[:, 0])) < 0.03
    g.save(tmp_path / "g.npz")
    g2 = GazeEstimator()
    assert g2.load(tmp_path / "g.npz")
    assert g2.ready and len(g2.implicit) == len(g.implicit)


def test_gaze_estimator_rejects_clicks_far_from_prediction():
    rng = np.random.default_rng(5)
    X, Y = _gaze_data(200, rng)
    g = GazeEstimator()
    g.fit(X, Y)
    assert not g.learn_from_click(X[0], (Y[0, 0] + 0.5, Y[0, 1]))
    assert len(g.implicit) == 0


def test_core_count_matches():
    assert len(GAZE_CORE) == 6
