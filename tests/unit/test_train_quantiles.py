import copy
import json
import math

import numpy as np
import pandas as pd
import pytest

from streamforecast import config, logs, train

pytestmark = pytest.mark.unit

SETTINGS = config.Settings()


@pytest.fixture(scope="module")
def trained():
    """One training run on the synthetic table: (features, checkpoint, sidecar)."""
    from conftest import make_synthetic_features

    table = make_synthetic_features()
    log = logs.get_logger("train", SETTINGS)
    checkpoint, sidecar = train.train(table, None, SETTINGS, log)
    return table, checkpoint, sidecar


# --- AC-3.3 -------------------------------------------------------------------------


def test_quantiles_from_calibration_and_ordered(trained):
    table, checkpoint, _ = trained
    for h in (1, 2, 3):
        q = checkpoint["residual_quantiles"][h]
        assert list(q) == ["0.025", "0.1", "0.5", "0.9", "0.975"]
        values = list(q.values())
        assert values == sorted(values)
        # Recompute from calibration rows only.
        parts = train.split_by_date(table, h, SETTINGS)
        cal = parts["calibration"]
        resid = cal[f"y_{h}"] - train.predict(checkpoint["models"][h], cal)
        assert q == train.residual_quantiles(resid.to_numpy())


def test_quantiles_ignore_test_rows(trained):
    table, checkpoint, _ = trained
    changed = table.copy()
    changed.loc[changed["date"] > "2024-12-31", "y_1"] += 5.0
    _, sidecar2 = train.train(changed, None, SETTINGS, logs.get_logger("train"))
    assert sidecar2["horizons"]["1"]["residual_quantiles"] == (
        checkpoint["residual_quantiles"][1]
    )


def test_residual_quantiles_known_values():
    q = train.residual_quantiles(np.arange(0, 1001) / 1000.0)
    assert q == pytest.approx(
        {"0.025": 0.025, "0.1": 0.1, "0.5": 0.5, "0.9": 0.9, "0.975": 0.975}
    )


# --- Bands (D9; shared with Forecast in Stage 4) --------------------------------------

Q = {"0.025": -0.5, "0.1": -0.2, "0.5": 0.05, "0.9": 0.3, "0.975": 0.6}


def test_bands_formula_and_median_bias():
    b = train.bands([math.log1p(100.0)], [0.1], Q)
    base = math.log1p(100.0) + 0.1
    assert b["median"][0] == pytest.approx(math.expm1(base + 0.05))
    assert b["lo80"][0] == pytest.approx(math.expm1(base - 0.2))
    assert b["hi95"][0] == pytest.approx(math.expm1(base + 0.6))


def test_bands_ordered_and_nonnegative_near_zero_flow():
    q = {"0.025": -3.0, "0.1": -1.0, "0.5": 0.0, "0.9": 1.0, "0.975": 3.0}
    b = train.bands(np.log1p([0.0, 0.22, 4.0, 8180.0]), [-2.0, 0.0, 0.5, 0.0], q)
    stacked = np.vstack([b["lo95"], b["lo80"], b["median"], b["hi80"], b["hi95"]])
    assert (np.diff(stacked, axis=0) >= 0).all()
    assert (stacked >= 0).all()


# --- AC-3.9: coverage is inclusive, in cfs, via bands() ----------------------------


def test_coverage_inclusive_and_in_cfs(monkeypatch):
    lo, hi = np.array([5.0, 5.0, 5.0]), np.array([9.0, 9.0, 9.0])
    assert train.coverage([5.0, 9.0, 9.01], lo, hi) == pytest.approx(2 / 3)
    # Calibration and test coverage are computed in cfs through bands().
    rows = pd.DataFrame(
        {
            "date": ["2025-01-01"] * 3,
            "logq_0": [math.log1p(10.0)] * 3,
            "flow_cfs": 10.0,
            "y_1": [0.0, 0.1, 5.0],
        }
    )
    calls = []
    real = train.bands

    def spy(*args):
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(train, "bands", spy)
    cov = train.calibration_coverage(rows, 1, np.zeros(3), Q)
    train.evaluate(rows, 1, np.zeros(3), Q, 1e9, None)
    assert len(calls) == 2
    assert cov["coverage_80"] == pytest.approx(2 / 3)  # y=5.0 is far above hi80


# --- AC-3.5 metric edge cases ------------------------------------------------------


def band_for(obs):
    obs = np.asarray(obs, dtype=float)
    return {"lo80": obs, "hi80": obs, "lo95": obs, "hi95": obs}


def test_metric_edge_cases():
    constant = np.full(25, 7.0)
    g = train.group_metrics(constant, constant + 1, constant, band_for(constant))
    assert g["model"]["nse"] is None and g["persistence"]["nse"] is None
    assert g["skill_mae"] is None  # persistence error is exactly zero
    assert g["status"] == "ok" and g["coverage_80"] == 1.0

    obs = np.arange(19.0)
    small = train.group_metrics(obs, obs, obs, band_for(obs))
    assert small["status"] == "insufficient" and small["n"] == 19
    assert small["model"]["mae_cfs"] is None and small["coverage_80"] is None

    empty = train.group_metrics([], [], [], band_for([]))
    assert empty["n"] == 0 and empty["status"] == "insufficient"

    obs = np.arange(1.0, 41.0)
    g = train.group_metrics(obs, obs + 2, obs - 4, band_for(obs))
    assert g["model"]["mae_cfs"] == 2.0 and g["persistence"]["mae_cfs"] == 4.0
    assert g["skill_mae"] == 0.5
    assert g["model"]["rmse_cfs"] == 2.0
    spread = np.sum((obs - obs.mean()) ** 2)
    assert g["model"]["nse"] == pytest.approx(1 - 40 * 4 / spread)


def test_sidecar_is_strict_json():
    text = train.to_json({"a": float("nan"), "b": np.float64(np.inf), "c": [1.5]})
    assert json.loads(text) == {"a": None, "b": None, "c": [1.5]}


def test_evaluate_model_and_persistence_share_rows(trained):
    table, checkpoint, sidecar = trained
    test = sidecar["horizons"]["1"]["test"]
    te = train.split_by_date(table, 1, SETTINGS)["test"]
    assert test["n"] == len(te)
    assert test["status"] == "ok"
    for key in ("mae_cfs", "rmse_cfs", "nse"):
        assert isinstance(test["model"][key], float)


# --- AC-3.10 --------------------------------------------------------------------------


def test_rain_split_labels_and_metrics():
    mrms = pd.Series(
        {"2025-01-02": 0.0, "2025-01-03": 5.0, "2025-01-04": 12.0, "2025-01-05": 0.0}
    )
    d0 = pd.Series(["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"])
    assert list(train.rain_labels(d0, 1, mrms)) == ["dry", "light", "wet", "dry"]
    # h=3: the window is d0+1..d0+3 (01-02..01-04 = 17 mm for d0 = 01-01).
    assert list(train.rain_labels(d0[:1], 3, mrms)) == ["wet"]
    assert list(train.rain_labels(pd.Series(["2024-12-31"]), 1, mrms)) == ["unlabeled"]


def test_rain_split_group_metrics_match_hand_computed():
    n = 60
    dates = pd.date_range("2025-01-01", periods=n).strftime("%Y-%m-%d")
    rows = pd.DataFrame(
        {"date": dates, "logq_0": np.log1p(10.0), "flow_cfs": 10.0, "y_1": 0.0}
    )
    rows.loc[:29, "y_1"] = np.log1p(20.0) - np.log1p(10.0)  # first 30: flow doubles
    wet_days = pd.date_range("2025-01-02", periods=30).strftime("%Y-%m-%d")
    dry_days = pd.date_range("2025-02-01", periods=31).strftime("%Y-%m-%d")
    mrms = pd.concat([pd.Series(15.0, index=wet_days), pd.Series(0.0, index=dry_days)])
    q = {"0.025": 0.0, "0.1": 0.0, "0.5": 0.0, "0.9": 0.0, "0.975": 0.0}
    yhat = rows["y_1"].to_numpy()  # a perfect model
    result = train.evaluate(rows, 1, yhat, q, 1e9, mrms)["by_rain"]
    wet, dry = result["wet"], result["dry"]
    assert (wet["n"], dry["n"], result["n_light"]) == (30, 30, 0)
    assert wet["model"]["mae_cfs"] == pytest.approx(0.0, abs=1e-9)
    assert wet["persistence"]["mae_cfs"] == pytest.approx(10.0)
    assert wet["skill_mae"] == pytest.approx(1.0)
    assert dry["persistence"]["mae_cfs"] == pytest.approx(0.0, abs=1e-9)
    assert (result["wet_mm"], result["dry_mm"]) == (10.0, 2.0)


def test_rain_split_null_without_mrms(trained, capsys):
    _, _, sidecar = trained
    assert all(sidecar["horizons"][h]["test"]["by_rain"] is None for h in "123")


def test_checkpoint_features_exclude_mrms(trained):
    _, checkpoint, sidecar = trained
    assert checkpoint["feature_columns"] == train.FEATURE_COLUMNS
    assert not [c for c in checkpoint["feature_columns"] if "mrms" in c.lower()]
    assert not [c for c in checkpoint["feature_columns"] if c.startswith("y_")]


# --- AC-3.8 ---------------------------------------------------------------------------


def test_warns_when_no_skill(capsys):
    from conftest import make_synthetic_features

    noise = make_synthetic_features(signal=False, seed=3)
    checkpoint, sidecar = train.train(noise, None, SETTINGS, logs.get_logger("train"))
    err = capsys.readouterr().err
    assert "WARNING train h=1 no skill vs persistence" in err
    assert checkpoint["models"]  # still trained and publishable


def test_warns_when_coverage_check_fails(capsys):
    """AC-3.8: an out-of-tolerance coverage check is a WARNING; training still ends."""
    from conftest import make_synthetic_features

    table = make_synthetic_features()
    rng = np.random.default_rng(7)
    test_rows = table["date"] > SETTINGS.calibration_end.isoformat()
    table.loc[test_rows, "y_1"] += rng.normal(0, 1.0, test_rows.sum())
    checkpoint, sidecar = train.train(table, None, SETTINGS, logs.get_logger("train"))
    assert sidecar["horizons"]["1"]["test"]["coverage_80"] < 0.72
    assert "WARNING train h=1 coverage check: test coverage_80" in (
        capsys.readouterr().err
    )
    assert checkpoint["models"]


def test_publish_writes_checkpoint_before_sidecar(trained, tmp_data_dir, monkeypatch):
    """AC-3.4: model first, sidecar last, each atomically; a reader never sees a
    sidecar without its checkpoint."""
    from pathlib import Path

    from streamforecast import paths

    _, checkpoint, sidecar = trained
    real = paths.atomic_write
    writes = []

    def spy(path, write_fn):
        others = sorted(p.name for p in Path(path).parent.glob("*"))
        writes.append((Path(path).name, others))
        return real(path, write_fn)

    monkeypatch.setattr(paths, "atomic_write", spy)
    model_path, metrics_path = train.publish(
        checkpoint, sidecar, Path("features_x.csv"), config.load()
    )
    assert [name for name, _ in writes] == [model_path.name, metrics_path.name]
    assert writes[0][1] == []  # nothing published before the checkpoint
    assert writes[1][1] == [model_path.name]  # the checkpoint exists first


# --- AC-3.9 report thresholds ------------------------------------------------------


def with_coverage(sidecar, test80=0.80, test95=0.95, cal80=0.80, cal95=0.95, h="1"):
    s = copy.deepcopy(sidecar)
    s["model_version"] = "20260101T000000Z"
    s["horizons"][h]["test"]["coverage_80"] = test80
    s["horizons"][h]["test"]["coverage_95"] = test95
    s["horizons"][h]["calibration"]["coverage_80"] = cal80
    s["horizons"][h]["calibration"]["coverage_95"] = cal95
    return s


def passing(sidecar):
    s = sidecar
    for h in "123":
        s = with_coverage(s, h=h)
    return s


@pytest.mark.parametrize(
    "change, ok",
    [
        ({}, True),
        ({"test80": 0.70}, False),
        ({"test80": 0.72}, True),
        ({"test80": 0.88}, True),
        ({"test80": 0.8801}, False),
        ({"test95": 0.90}, True),
        ({"test95": 0.995}, False),
        ({"cal80": 0.75}, False),
        ({"cal80": 0.78}, True),
        ({"cal95": 0.9299}, False),
        ({"test80": None}, False),
    ],
)
def test_coverage_report_thresholds(trained, change, ok):
    _, _, sidecar = trained
    s = with_coverage(passing(sidecar), h="2", **change)
    passed, text = train.report(s)
    assert passed is ok
    assert text.count("\n") >= 5
    assert ("FAIL h=2" in text) is (not ok)


def test_report_command_exit_codes(trained, tmp_data_dir, capsys):
    _, _, sidecar = trained
    models = tmp_data_dir / "models"
    models.mkdir()
    assert train.main(["--report"]) == 1  # no sidecar yet
    (models / "metrics_20260101T000000Z.json").write_text(
        train.to_json(passing(sidecar))
    )
    assert train.main(["--report"]) == 0
    assert "PASS" in capsys.readouterr().out
    bad = with_coverage(passing(sidecar), test80=0.70, h="3")
    (models / "metrics_20260102T000000Z.json").write_text(train.to_json(bad))
    assert train.main(["--report"]) == 1  # the newest sidecar is the one checked
    out = capsys.readouterr().out
    assert "metrics_20260102T000000Z.json" in out and "FAIL h=3" in out
