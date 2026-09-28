import math

import numpy as np
import pytest

from streamforecast import forecast

pytestmark = pytest.mark.unit

Q = {"0.025": -0.5, "0.1": -0.2, "0.5": 0.05, "0.9": 0.3, "0.975": 0.6}


def test_bands_formula_and_median_bias():
    logq_0, yhat = math.log1p(100.0), 0.1
    b = forecast.predict_bands([logq_0], [yhat], Q)
    for name, r in [
        ("lo95", -0.5),
        ("lo80", -0.2),
        ("median", 0.05),
        ("hi80", 0.3),
        ("hi95", 0.6),
    ]:
        assert b[name][0] == pytest.approx(math.expm1(logq_0 + yhat + r)), name
    # The median is bias-corrected: it is not expm1(logq_0 + yhat).
    assert b["median"][0] != pytest.approx(math.expm1(logq_0 + yhat))


@pytest.mark.parametrize(
    "flow, yhat",
    [(0.0, 0.0), (0.22, -1.5), (4.0, 0.3), (8180.0, -0.1), (0.5, -8.0)],
)
def test_band_ordering_and_nonnegative(flow, yhat):
    q = {"0.025": -6.0, "0.1": -3.0, "0.5": -0.1, "0.9": 1.0, "0.975": 2.5}
    b = forecast.predict_bands([math.log1p(flow)], [yhat], q)
    values = [b[k][0] for k in ("lo95", "lo80", "median", "hi80", "hi95")]
    assert values == sorted(values)
    assert min(values) >= 0.0
    assert all(np.isfinite(values))


def test_large_negative_residuals_clip_at_zero():
    q = {"0.025": -50.0, "0.1": -40.0, "0.5": -30.0, "0.9": -20.0, "0.975": -10.0}
    b = forecast.predict_bands([math.log1p(4.0)], [0.0], q)
    assert all(b[k][0] == 0.0 for k in b)
