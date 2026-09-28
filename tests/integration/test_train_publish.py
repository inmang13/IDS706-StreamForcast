"""train.main() on a temporary DATA_DIR: checkpoint + sidecar (AC-3.4, AC-3.5)."""

import json

import joblib
import pandas as pd
import pytest
import sklearn

from streamforecast import train

pytestmark = pytest.mark.integration


def strict_json(text):
    def reject(token):
        raise ValueError(f"non-standard JSON constant {token}")

    return json.loads(text, parse_constant=reject)


@pytest.fixture
def published(tmp_data_dir, synthetic_features):
    features_dir = tmp_data_dir / "features"
    features_dir.mkdir()
    table = synthetic_features()
    table.to_csv(features_dir / "features_20260101T000000Z.csv", index=False)
    # An MRMS verification series for the wet/dry split (AC-3.10).
    mrms = pd.DataFrame({"date": table["date"], "mrms_mm": table["precip_0"]})
    mrms.to_csv(features_dir / "mrms_20260101T000000Z.csv", index=False)
    assert train.main([]) == 0
    (model,) = (tmp_data_dir / "models").glob("model_*.joblib")
    (metrics,) = (tmp_data_dir / "models").glob("metrics_*.json")
    return model, metrics


def test_publish_checkpoint_and_sidecar(published):
    model_path, metrics_path = published
    stamp = model_path.stem.removeprefix("model_")
    assert metrics_path.stem == f"metrics_{stamp}"
    checkpoint = joblib.load(model_path)
    assert set(checkpoint) >= {
        "models",
        "residual_quantiles",
        "feature_columns",
        "sklearn_version",
        "split_dates",
        "trained_at_utc",
        "model_version",
    }
    assert checkpoint["model_version"] == stamp
    assert checkpoint["sklearn_version"] == sklearn.__version__
    assert sorted(checkpoint["models"]) == [1, 2, 3]
    sidecar = strict_json(metrics_path.read_text(encoding="utf-8"))
    assert sidecar["model_version"] == stamp
    assert sidecar["trained_at_utc"] == checkpoint["trained_at_utc"]


def test_sidecar_metric_keys(published):
    _, metrics_path = published
    sidecar = strict_json(metrics_path.read_text(encoding="utf-8"))
    assert sidecar["high_flow_threshold_cfs"] > 0
    for h in ("1", "2", "3"):
        entry = sidecar["horizons"][h]
        test = entry["test"]
        for key in (
            "n",
            "status",
            "skill_mae",
            "coverage_80",
            "coverage_95",
            "n_high",
            "coverage_80_high",
            "coverage_95_high",
        ):
            assert key in test, (h, key)
        assert set(test["model"]) == {"mae_cfs", "rmse_cfs", "nse"}
        assert set(test["persistence"]) == {"mae_cfs", "nse"}
        assert set(entry["calibration"]) == {"n", "coverage_80", "coverage_95"}
        rain = test["by_rain"]
        assert set(rain) >= {"wet", "dry", "n_light", "wet_mm", "dry_mm"}
        for group in ("wet", "dry"):
            assert {"n", "skill_mae", "coverage_80", "coverage_95"} <= set(rain[group])
            assert "mae_cfs" in rain[group]["model"]
            assert "mae_cfs" in rain[group]["persistence"]


def test_checkpoint_holds_no_streamforecast_objects(published):
    """Loading the checkpoint must never import a stage module (D11)."""
    model_path, _ = published
    assert b"streamforecast" not in model_path.read_bytes()

    def walk(obj):
        assert not type(obj).__module__.startswith("streamforecast"), type(obj)
        if isinstance(obj, dict):
            for key, value in obj.items():
                walk(key)
                walk(value)

    walk(joblib.load(model_path))


def test_no_features_file_is_an_error(tmp_data_dir, capsys):
    assert train.main([]) == 1
    assert "no features file" in capsys.readouterr().err
    assert not list((tmp_data_dir / "models").glob("*"))


def test_too_little_data_publishes_nothing(tmp_data_dir, synthetic_features, capsys):
    features_dir = tmp_data_dir / "features"
    features_dir.mkdir()
    recent = synthetic_features(start="2026-09-01", end="2026-09-23")
    recent.to_csv(features_dir / "features_20260101T000000Z.csv", index=False)
    assert train.main([]) == 1
    assert "nothing published" in capsys.readouterr().err
    assert not list((tmp_data_dir / "models").glob("*"))
