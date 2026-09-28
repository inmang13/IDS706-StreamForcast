"""forecast.main() end to end: real fixture features + a trained checkpoint (AC-4.x)."""

import joblib
import pandas as pd
import pytest

from streamforecast import forecast

pytestmark = pytest.mark.integration

RECENT = dict(
    usgs="usgs_daily_recent.json",
    weather_hist="openmeteo_hist_forecast.json",
    weather_fc="openmeteo_forecast_past7.json",
    weather_leads="openmeteo_previous_runs.json",
)


@pytest.fixture
def ready(install_checkpoint, raw_from_fixtures, run_features, monkeypatch):
    """Checkpoint installed; features built from the recorded 2026-09 fixtures;
    today = 2026-09-24, so d0 = 2026-09-23 and stale_days = 0."""
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    model_path = install_checkpoint()
    raw_from_fixtures(**RECENT)
    code, _ = run_features()
    assert code == 0
    return model_path


def features_file(data_dir):
    return sorted((data_dir / "features").glob("features_*.csv"))[-1]


def edit_live_row(data_dir, **changes):
    path = features_file(data_dir)
    df = pd.read_csv(
        path, dtype={"date": str, "qualifier": str, "fc_fetched_date": str}
    )
    for column, value in changes.items():
        df.loc[df.index[-1], column] = value
    df.to_csv(path, index=False)


def forecasts(data_dir):
    return sorted((data_dir / "forecasts").glob("forecast_*.csv"))


# --- AC-4.1, 4.3 -------------------------------------------------------------------


def test_forecast_writes_three_rows(ready, tmp_data_dir, capsys):
    assert forecast.main() == 0
    (path,) = forecasts(tmp_data_dir)
    assert path.name.startswith("forecast_2026-09-23_")
    out = pd.read_csv(path, dtype={"issue_date": str, "valid_date": str})
    assert list(out.columns) == forecast.OUTPUT_COLUMNS
    assert list(out["horizon"]) == [1, 2, 3]
    assert list(out["valid_date"]) == ["2026-09-24", "2026-09-25", "2026-09-26"]
    assert set(out["issue_date"]) == {"2026-09-23"}
    assert set(out["persistence_cfs"]) == set(out["latest_obs_cfs"]) == {7.22}
    assert set(out["model_version"]) == {ready.stem.removeprefix("model_")}
    assert set(out["stale_days"]) == {0}
    assert set(out["fc_fetched_date"]) == {"2026-09-24"}
    assert out["created_at_utc"].str.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$").all()
    b = out[["lo95_cfs", "lo80_cfs", "median_cfs", "hi80_cfs", "hi95_cfs"]]
    assert (b.diff(axis=1).iloc[:, 1:] >= 0).all().all() and (b >= 0).all().all()
    err = capsys.readouterr().err
    assert f"loaded {ready.name}" in err
    assert "d0=2026-09-23 stale_days=0" in err
    assert "wrote 3 rows -> " in err


# --- AC-4.8 ------------------------------------------------------------------------


def test_provisional_and_estimated_flagged(ready, tmp_data_dir, capsys):
    assert forecast.main() == 0
    out = pd.read_csv(forecasts(tmp_data_dir)[0])
    assert out["latest_obs_provisional"].all()  # the recent fixture is Provisional
    assert not out["latest_obs_estimated"].any()
    assert "is Provisional; used as-is" in capsys.readouterr().err


def test_estimated_latest_value_flagged(
    install_checkpoint,
    raw_from_fixtures,
    write_raw,
    run_features,
    tmp_data_dir,
    monkeypatch,
):
    """The estimated-gap fixture ends on an ESTIMATED (Approved) day, 2025-07-18."""
    monkeypatch.setenv("AS_OF_DATE", "2025-07-19")
    install_checkpoint()
    raw_from_fixtures(usgs="usgs_daily_estimated_gap.json")
    days = pd.date_range("2025-07-12", "2025-07-21").strftime("%Y-%m-%d")
    write_raw(
        "weather_fc",
        pd.DataFrame(
            {
                "date": days,
                "precip_mm": 1.0,
                "tmax_c": 30.0,
                "tmin_c": 20.0,
                "source": "forecast",
                "fetched_at_utc": "2025-07-19T16:00:00Z",
            }
        ),
    )
    assert run_features()[0] == 0
    assert forecast.main() == 0
    out = pd.read_csv(forecasts(tmp_data_dir)[0])
    assert out["latest_obs_estimated"].all()
    assert not out["latest_obs_provisional"].any()
    assert (out["latest_obs_cfs"] > 0).all()


# --- AC-4.4 ------------------------------------------------------------------------


def test_no_checkpoint_skips_cleanly(
    raw_from_fixtures, run_features, tmp_data_dir, capsys
):
    raw_from_fixtures(**RECENT)
    run_features()
    assert forecast.main() == 0
    assert forecasts(tmp_data_dir) == []
    assert "WARNING forecast no checkpoint yet; skipping forecast" in (
        capsys.readouterr().err
    )


def test_checkpoint_without_sidecar_is_unpublished(
    install_checkpoint, tmp_data_dir, capsys
):
    install_checkpoint(with_sidecar=False)
    assert forecast.main() == 0
    assert forecasts(tmp_data_dir) == []
    assert "no checkpoint yet" in capsys.readouterr().err


def test_empty_data_dir(tmp_data_dir, capsys):
    assert forecast.main() == 0
    assert "no checkpoint yet" in capsys.readouterr().err


# --- AC-4.5 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "today, stale", [("2026-09-24", 0), ("2026-09-25", 1), ("2026-09-26", 2)]
)
def test_stale_boundaries_written(
    ready, tmp_data_dir, monkeypatch, capsys, today, stale
):
    monkeypatch.setenv("AS_OF_DATE", today)
    edit_live_row(tmp_data_dir, fc_fetched_date=today)  # isolate the USGS rule
    assert forecast.main() == 0
    out = pd.read_csv(forecasts(tmp_data_dir)[0])
    assert set(out["stale_days"]) == {stale}
    assert list(out["valid_date"]) == ["2026-09-24", "2026-09-25", "2026-09-26"]
    warned = "USGS data through 2026-09-23, %d day(s) stale" % stale
    assert (warned in capsys.readouterr().err) is (stale >= 1)


def test_stale_beyond_limit_refused(ready, tmp_data_dir, monkeypatch, capsys):
    monkeypatch.setenv("AS_OF_DATE", "2026-09-27")
    edit_live_row(tmp_data_dir, fc_fetched_date="2026-09-27")
    assert forecast.main() == 1
    assert forecasts(tmp_data_dir) == []
    assert (
        "ERROR forecast USGS data through 2026-09-23 is 3 days stale (limit 2); "
        "not forecasting" in capsys.readouterr().err
    )


# --- AC-4.6 ------------------------------------------------------------------------


def corrupt_bytes(path):
    path.write_bytes(b"not a pickle at all")


def missing_keys(path):
    checkpoint = joblib.load(path)
    del checkpoint["residual_quantiles"]
    joblib.dump(checkpoint, path)


def other_sklearn(path):
    checkpoint = joblib.load(path)
    checkpoint["sklearn_version"] = "0.0.1"
    joblib.dump(checkpoint, path)


@pytest.mark.parametrize(
    "damage, message",
    [
        (corrupt_bytes, "cannot load"),
        (missing_keys, "missing keys ['residual_quantiles']"),
        (other_sklearn, "trained with scikit-learn 0.0.1"),
    ],
)
def test_corrupt_checkpoint_errors(ready, tmp_data_dir, capsys, damage, message):
    damage(ready)
    assert forecast.main() == 1
    assert forecasts(tmp_data_dir) == []
    err = capsys.readouterr().err
    assert "ERROR forecast" in err and ready.name in err and message in err


# --- AC-4.7 ------------------------------------------------------------------------


def test_short_forecast_refuses(
    install_checkpoint,
    raw_from_fixtures,
    run_features,
    tmp_data_dir,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    install_checkpoint()
    raw_from_fixtures(**{**RECENT, "weather_fc": "openmeteo_forecast_short.json"})
    run_features()
    assert forecast.main() == 1
    assert forecasts(tmp_data_dir) == []
    assert (
        "forecast weather has 2 of 3 days for 2026-09-24..2026-09-26; not forecasting"
        in capsys.readouterr().err
    )


def test_null_forecast_value_refuses(
    install_checkpoint,
    raw_from_fixtures,
    write_raw,
    load_fixture,
    run_features,
    tmp_data_dir,
    monkeypatch,
    capsys,
):
    from streamforecast import ingest

    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    install_checkpoint()
    raw_from_fixtures(**{k: v for k, v in RECENT.items() if k != "weather_fc"})
    data = load_fixture("openmeteo_forecast_past7.json")
    data["daily"]["precipitation_sum"][-2] = None  # 2026-09-25
    rows = ingest.parse_daily_weather(data, "forecast", "2026-09-24T16:00:00Z")
    write_raw("weather_fc", pd.DataFrame(rows, columns=ingest.WEATHER_COLUMNS))
    run_features()
    assert forecast.main() == 1
    assert forecasts(tmp_data_dir) == []
    assert "forecast weather has 2 of 3 days" in capsys.readouterr().err


# --- AC-4.9 ------------------------------------------------------------------------


def test_flow_lag_gap_refuses(
    install_checkpoint,
    raw_from_fixtures,
    write_raw,
    run_features,
    tmp_data_dir,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    install_checkpoint()
    raw_from_fixtures(**RECENT)
    usgs = next((tmp_data_dir / "raw/usgs").glob("usgs_*.csv"))
    df = pd.read_csv(usgs, dtype=str)
    gap = ["2026-09-20", "2026-09-21", "2026-09-22"]  # 3-day gap ending at d0-1
    df[~df["date"].isin(gap)].to_csv(usgs, index=False)
    run_features()
    assert forecast.main() == 1
    assert forecasts(tmp_data_dir) == []
    assert "ERROR forecast flow lags incomplete at 2026-09-23" in (
        capsys.readouterr().err
    )


# --- AC-4.10 -----------------------------------------------------------------------


def test_stale_forecast_weather_refuses(ready, tmp_data_dir, capsys):
    edit_live_row(tmp_data_dir, fc_fetched_date="2026-09-23")
    assert forecast.main() == 1
    assert forecasts(tmp_data_dir) == []
    assert (
        "forecast weather is stale (fetched 2026-09-23); not forecasting"
        in capsys.readouterr().err
    )


# --- AC-4.11 -----------------------------------------------------------------------


def refuse_stale_usgs(data_dir, monkeypatch):
    monkeypatch.setenv("MAX_STALE_DAYS", "-1")


def refuse_corrupt(data_dir, monkeypatch):
    corrupt_bytes(next((data_dir / "models").glob("model_*.joblib")))


def refuse_short(data_dir, monkeypatch):
    edit_live_row(data_dir, precip_f3=float("nan"))


def refuse_lags(data_dir, monkeypatch):
    edit_live_row(data_dir, logq_1=float("nan"))


def refuse_stale_weather(data_dir, monkeypatch):
    edit_live_row(data_dir, fc_fetched_date="2026-09-22")


@pytest.mark.parametrize(
    "refusal",
    [
        refuse_stale_usgs,
        refuse_corrupt,
        refuse_short,
        refuse_lags,
        refuse_stale_weather,
    ],
)
def test_refusal_keeps_previous_forecast(ready, tmp_data_dir, monkeypatch, refusal):
    assert forecast.main() == 0
    (previous,) = forecasts(tmp_data_dir)
    before = previous.read_bytes()
    refusal(tmp_data_dir, monkeypatch)
    assert forecast.main() == 1
    assert forecasts(tmp_data_dir) == [previous]
    assert previous.read_bytes() == before
    assert not list((tmp_data_dir / "forecasts").glob("*.tmp"))
