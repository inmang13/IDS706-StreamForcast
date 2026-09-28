"""AC-4.2 + AC-3.9: Forecast serves exactly the bands Train measured coverage on.

Tests may import both stage modules even though the stages may not import each other.
"""

import joblib
import numpy as np
import pandas as pd
import pytest

from streamforecast import forecast, train

pytestmark = pytest.mark.regression


def test_band_functions_agree_on_random_inputs(published_checkpoint):
    model_path, _ = published_checkpoint
    checkpoint = joblib.load(model_path)
    rng = np.random.default_rng(1)
    logq_0 = np.log1p(rng.gamma(1.5, 60.0, 500))
    yhat = rng.normal(0, 0.5, 500)
    for h in (1, 2, 3):
        q = checkpoint["residual_quantiles"][h]
        a, b = train.bands(logq_0, yhat, q), forecast.predict_bands(logq_0, yhat, q)
        assert a.keys() == b.keys()
        for name in a:
            assert np.array_equal(a[name], b[name]), (h, name)


def test_forecast_bands_match_train_bands(
    published_checkpoint,
    install_checkpoint,
    raw_from_fixtures,
    run_features,
    tmp_data_dir,
    monkeypatch,
):
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    install_checkpoint()
    raw_from_fixtures(
        usgs="usgs_daily_recent.json",
        weather_hist="openmeteo_hist_forecast.json",
        weather_fc="openmeteo_forecast_past7.json",
        weather_leads="openmeteo_previous_runs.json",
    )
    _, features = run_features()
    assert forecast.main() == 0
    (path,) = (tmp_data_dir / "forecasts").glob("forecast_*.csv")
    served = pd.read_csv(path)

    checkpoint = joblib.load(published_checkpoint[0])
    row = features.iloc[[-1]]
    for h in (1, 2, 3):
        yhat = checkpoint["models"][h].predict(row[train.FEATURE_COLUMNS].to_numpy())
        expected = train.bands(row["logq_0"], yhat, checkpoint["residual_quantiles"][h])
        got = served.loc[served["horizon"] == h].iloc[0]
        for name in ("median", "lo80", "hi80", "lo95", "hi95"):
            assert got[f"{name}_cfs"] == pytest.approx(expected[name][0], rel=1e-12)
