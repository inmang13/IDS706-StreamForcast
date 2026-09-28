from datetime import date, timedelta

import numpy as np
import pytest

from streamforecast import config, train

pytestmark = pytest.mark.unit

SETTINGS = config.Settings()


def plus(d, h):
    return (date.fromisoformat(d) + timedelta(days=h)).isoformat()


@pytest.fixture(scope="module")
def table():
    from conftest import make_synthetic_features

    return make_synthetic_features()


@pytest.mark.parametrize("h", [1, 2, 3])
def test_splits_time_ordered_no_overlap(table, h):
    parts = train.split_by_date(table, h, SETTINGS)
    tr, cal, te = parts["train"], parts["calibration"], parts["test"]
    assert tr["date"].max() < cal["date"].min() < te["date"].min()
    # Both d0 and d0+h inside the split.
    assert max(plus(d, h) for d in tr["date"]) <= "2023-12-31"
    assert cal["date"].min() >= "2024-02-01"
    assert max(plus(d, h) for d in cal["date"]) <= "2024-12-31"
    assert te["date"].min() == "2025-01-01"
    # January 2024 is unused; the rows are in date order (never shuffled).
    for part in (tr, cal, te):
        assert not part["date"].str.startswith("2024-01").any()
        assert list(part["date"]) == sorted(part["date"])
    assert len(set(tr["date"]) & set(cal["date"]) & set(te["date"])) == 0
    assert tr[f"y_{h}"].notna().all()


def test_edge_rows_excluded_when_target_leaves_the_split(table):
    tr3 = train.split_by_date(table, 3, SETTINGS)["train"]
    assert tr3["date"].max() == "2023-12-28"  # 12-28 + 3 = 12-31
    cal3 = train.split_by_date(table, 3, SETTINGS)["calibration"]
    assert cal3["date"].max() == "2024-12-28"


def test_lead_matched_violation_aborts(table):
    bad = table.copy()
    bad.loc[bad["date"] == "2025-03-01", "weather_lead_matched"] = False
    with pytest.raises(train.TrainError, match="test rows without lead-matched"):
        train.split_by_date(bad, 1, SETTINGS)


def test_nan_feature_rows_dropped_from_cal_and_test_only(table):
    t = table.copy()
    t.loc[t["date"].isin(["2020-05-05", "2024-05-05", "2025-05-05"]), "api_7"] = np.nan
    parts = train.split_by_date(t, 1, SETTINGS)
    assert "2020-05-05" in set(parts["train"]["date"])  # HGB handles NaN in train
    assert "2024-05-05" not in set(parts["calibration"]["date"])
    assert "2025-05-05" not in set(parts["test"]["date"])
    assert parts["calibration"].attrs["dropped_nan"] == 1
    assert parts["test"].attrs["dropped_nan"] == 1


def test_fit_uses_train_rows_only(table):
    parts = train.split_by_date(table, 1, SETTINGS)
    model = train.fit_horizon(parts["train"], 1)
    poisoned = table.copy()
    late = poisoned["date"] >= "2024-01-01"
    poisoned.loc[late, "y_1"] = 50.0  # absurd targets outside train
    parts2 = train.split_by_date(poisoned, 1, SETTINGS)
    model2 = train.fit_horizon(parts2["train"], 1)
    probe = parts["test"]
    assert np.array_equal(train.predict(model, probe), train.predict(model2, probe))


def test_train_fits_on_train_rows_only(table):
    """TF14: train() itself (not just fit_horizon) ignores calibration targets when
    fitting: changing them moves the quantiles but not the model."""
    from streamforecast import logs

    log = logs.get_logger("train", config.Settings(log_level="ERROR"))
    a, _ = train.train(table, None, SETTINGS, log)
    changed = table.copy()
    cal = (changed["date"] >= "2024-02-01") & (changed["date"] <= "2024-12-31")
    changed.loc[cal, "y_1"] += 5.0
    b, _ = train.train(changed, None, SETTINGS, log)
    probe = table.loc[table["date"] >= "2025-01-01", train.FEATURE_COLUMNS].to_numpy()
    assert np.array_equal(a["models"][1].predict(probe), b["models"][1].predict(probe))
    assert a["residual_quantiles"][1] != b["residual_quantiles"][1]


def test_models_use_hgb_defaults_with_seed(table):
    model = train.fit_horizon(train.split_by_date(table, 1, SETTINGS)["train"], 1)
    params = model.get_params()
    assert params["random_state"] == 0
    defaults = type(model)().get_params()
    assert {k: v for k, v in params.items() if k != "random_state"} == {
        k: v for k, v in defaults.items() if k != "random_state"
    }
    assert model.n_features_in_ == len(train.FEATURE_COLUMNS)
