"""AC-3.6 (synthetic coverage and skill) and AC-3.7 (deterministic training)."""

from datetime import date, datetime, timezone

import joblib
import numpy as np
import pytest

from streamforecast import config, logs, paths, train

pytestmark = pytest.mark.regression

# Four-year calibration and test periods keep sampling error well inside the AC-3.6
# tolerances (the check is of the method, not of the default split lengths).
SYNTHETIC_SPLITS = config.Settings(
    train_end=date(2017, 12, 31),
    calibration_start=date(2018, 1, 1),
    calibration_end=date(2021, 12, 31),
)


@pytest.fixture(scope="module")
def result():
    from conftest import make_synthetic_features

    table = make_synthetic_features(
        start="2010-01-01", end="2025-12-31", seed=0, lead_start="2018-01-01"
    )
    return train.train(table, None, SYNTHETIC_SPLITS, logs.get_logger("train"))


@pytest.mark.parametrize("h", ["1", "2", "3"])
def test_synthetic_coverage(result, h):
    _, sidecar = result
    entry = sidecar["horizons"][h]
    for split in ("calibration", "test"):
        assert entry[split]["coverage_80"] == pytest.approx(0.80, abs=0.05), split
        assert entry[split]["coverage_95"] == pytest.approx(0.95, abs=0.03), split
    assert entry["test"]["n"] > 1400


def test_synthetic_skill_at_h1(result):
    _, sidecar = result
    assert sidecar["horizons"]["1"]["test"]["skill_mae"] > 0


def test_deterministic_training(tmp_data_dir, monkeypatch):
    """Two `make train` runs on one features file give identical predictions and
    quantiles. Different timestamps, so the second run can't overwrite the first."""
    from conftest import make_synthetic_features

    features_dir = tmp_data_dir / "features"
    features_dir.mkdir()
    table = make_synthetic_features()
    table.to_csv(features_dir / "features_20260101T000000Z.csv", index=False)
    stamps = iter(
        [
            datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 24, 12, 0, 1, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(paths, "utc_now", lambda: next(stamps))
    assert train.main([]) == 0
    assert train.main([]) == 0
    first, second = sorted((tmp_data_dir / "models").glob("model_*.joblib"))
    a, b = joblib.load(first), joblib.load(second)
    assert a["model_version"] != b["model_version"]
    assert a["residual_quantiles"] == b["residual_quantiles"]
    probe = table.loc[table["date"] >= "2025-01-01", train.FEATURE_COLUMNS].to_numpy()
    for h in (1, 2, 3):
        assert np.array_equal(
            a["models"][h].predict(probe), b["models"][h].predict(probe)
        )
