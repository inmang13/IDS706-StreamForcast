"""Tester edge cases (Gate 3): break the pipeline with recorded fixtures, no network.

Each test drives the real stage entry points (ingest.main, features.run,
forecast.main) on a temporary DATA_DIR. Tests marked ``xfail(strict=True)`` document
a defect found in review (see docs/plan.md, Stage 6 Review findings). They flip to
XPASS, and fail the suite, once the defect is fixed, so the marker must be removed
along with the fix.
"""

import math
import re

import joblib
import numpy as np
import pandas as pd
import pytest
import responses

from streamforecast import features, forecast, ingest

pytestmark = pytest.mark.integration

RECENT = dict(
    usgs="usgs_daily_recent.json",
    weather_hist="openmeteo_hist_forecast.json",
    weather_fc="openmeteo_forecast_past7.json",
    weather_leads="openmeteo_previous_runs.json",
)
BANDS = ["lo95_cfs", "lo80_cfs", "median_cfs", "hi80_cfs", "hi95_cfs"]
MRMS_RE = re.compile(r"https://mesonet\.agron\.iastate\.edu/iemre/multiday/.*")


@pytest.fixture
def raw(raw_from_fixtures, monkeypatch):
    """Raw CSVs from the 2026-09 fixtures; today = 2026-09-24 (d0 = 09-23)."""
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    raw_from_fixtures(**RECENT)


def usgs_csv(data_dir):
    return next((data_dir / "raw" / "usgs").glob("usgs_*.csv"))


def edit_usgs(data_dir, fn):
    path = usgs_csv(data_dir)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    fn(df).to_csv(path, index=False)


def set_flow(date, value, **extra):
    def fn(df):
        df.loc[df["date"] == date, "flow_cfs"] = value
        for column, v in extra.items():
            df.loc[df["date"] == date, column] = v
        return df

    return fn


def drop_dates(dates):
    return lambda df: df[~df["date"].isin(dates)]


def run_chain(run_features, tmp_data_dir):
    """features -> forecast; returns (features code, forecast code, forecast frame)."""
    code_f, feats = run_features()
    code_fc = forecast.main()
    files = sorted((tmp_data_dir / "forecasts").glob("forecast_*.csv"))
    out = pd.read_csv(files[-1], dtype={"valid_date": str}) if files else None
    return code_f, code_fc, out, feats


def assert_bands_sane(out):
    b = out[BANDS].to_numpy()
    assert np.isfinite(b).all()
    assert (b >= 0).all()
    assert (np.diff(b, axis=1) >= 0).all()


# --- USGS: empty series, provisional only, ice-affected day ---------------------------


def test_empty_usgs_series_leads_to_clean_refusals(
    tmp_data_dir, load_fixture, install_checkpoint, monkeypatch, capsys
):
    """Empty USGS: ingest warns (exit 0), Features has no flow (exit 1), and Forecast
    refuses (exit 1) and writes nothing."""
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    monkeypatch.setattr(ingest, "_sleep", lambda s: None)
    install_checkpoint()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        r.get(ingest.USGS_URL, json=load_fixture("usgs_empty.json"))
        r.get(ingest.HIST_URL, json=load_fixture("openmeteo_hist_forecast.json"))
        r.get(ingest.LEADS_URL, json=load_fixture("openmeteo_previous_runs.json"))
        r.get(ingest.FORECAST_URL, json=load_fixture("openmeteo_forecast_past7.json"))
        r.get(MRMS_RE, json=load_fixture("iem_mrms_recent.json"))
        assert ingest.main() == 0
    assert features.main() == 1
    assert forecast.main() == 1
    assert not list((tmp_data_dir / "forecasts").glob("*"))
    err = capsys.readouterr().err
    assert "usgs: empty response" in err
    assert "no raw USGS files" in err
    assert "no features file" in err


def test_all_provisional_series_forecasts_and_flags(
    raw, install_checkpoint, run_features, tmp_data_dir
):
    install_checkpoint()
    _, code, out, feats = run_chain(run_features, tmp_data_dir)
    assert set(feats["approval_status"]) == {"Provisional"}
    assert code == 0 and out["latest_obs_provisional"].all()


def test_ice_affected_latest_day_without_value(
    raw, install_checkpoint, run_features, tmp_data_dir, capsys
):
    """An ice day with no value is dropped as missing, so d0 moves back one day and
    the forecast is written as 1 day stale."""
    install_checkpoint()
    edit_usgs(tmp_data_dir, set_flow("2026-09-23", "", qualifier="ICE"))
    _, code, out, feats = run_chain(run_features, tmp_data_dir)
    assert feats["date"].iloc[-1] == "2026-09-22"
    assert code == 0
    assert set(out["stale_days"]) == {1}
    assert list(out["valid_date"]) == ["2026-09-23", "2026-09-24", "2026-09-25"]
    assert "1 missing" in capsys.readouterr().err


def test_ice_affected_estimated_value_is_kept_and_flagged(
    raw, install_checkpoint, run_features, tmp_data_dir
):
    install_checkpoint()
    edit_usgs(tmp_data_dir, set_flow("2026-09-23", "6.5", qualifier="ICE;ESTIMATED"))
    _, code, out, _ = run_chain(run_features, tmp_data_dir)
    assert code == 0
    assert set(out["latest_obs_cfs"]) == {6.5}
    assert out["latest_obs_estimated"].all()


def test_ice_qualifier_alone_is_not_flagged(
    raw, install_checkpoint, run_features, tmp_data_dir
):
    """Documents current behaviour: only ESTIMATED sets latest_obs_estimated. An
    ICE-only qualifier is used as-is with no flag (the plan names only ESTIMATED)."""
    install_checkpoint()
    edit_usgs(tmp_data_dir, set_flow("2026-09-23", "6.5", qualifier="ICE"))
    _, code, out, _ = run_chain(run_features, tmp_data_dir)
    assert code == 0
    assert not out["latest_obs_estimated"].any()


# --- Gaps: 1 day vs 10 days -----------------------------------------------------------


def test_one_day_gap_before_d0_is_filled_and_forecast(
    raw, install_checkpoint, run_features, tmp_data_dir
):
    install_checkpoint()
    edit_usgs(tmp_data_dir, drop_dates(["2026-09-22"]))
    _, code, out, feats = run_chain(run_features, tmp_data_dir)
    live = feats.iloc[-1]
    assert live["date"] == "2026-09-23" and live["flow_filled"]
    assert live["logq_1"] == pytest.approx(live["logq_2"])  # 09-22 filled from 09-21
    assert "2026-09-22" not in set(feats["date"])  # no issue date on a missing day
    assert code == 0
    assert_bands_sane(out)


def test_ten_day_gap_before_d0_refuses(
    raw, install_checkpoint, run_features, tmp_data_dir, capsys
):
    install_checkpoint()
    gap = [f"2026-09-{d}" for d in range(14, 23)]  # every day before d0 in the fixture
    edit_usgs(tmp_data_dir, drop_dates(gap))
    _, code, out, feats = run_chain(run_features, tmp_data_dir)
    assert math.isnan(feats.iloc[-1]["logq_1"])
    assert code == 1 and out is None
    assert "flow lags incomplete at 2026-09-23" in capsys.readouterr().err


# --- Zero and negative flow -----------------------------------------------------------


def test_zero_latest_flow_gives_nonnegative_bands(
    raw, install_checkpoint, run_features, tmp_data_dir
):
    install_checkpoint()
    edit_usgs(tmp_data_dir, set_flow("2026-09-23", "0.0"))
    _, code, out, feats = run_chain(run_features, tmp_data_dir)
    assert feats.iloc[-1]["flow_cfs"] == 0.0  # zero is a valid flow, kept
    assert code == 0
    assert set(out["persistence_cfs"]) == {0.0}
    assert_bands_sane(out)


def test_negative_latest_flow_is_dropped_and_d0_moves_back(
    raw, install_checkpoint, run_features, tmp_data_dir, capsys
):
    install_checkpoint()
    edit_usgs(tmp_data_dir, set_flow("2026-09-23", "-5.0"))
    _, code, out, feats = run_chain(run_features, tmp_data_dir)
    assert feats["date"].iloc[-1] == "2026-09-22"
    assert (feats["flow_cfs"] >= 0).all()
    assert code == 0 and set(out["stale_days"]) == {1}
    assert "1 negative" in capsys.readouterr().err


def test_sentinel_latest_flow_is_dropped(raw, run_features, tmp_data_dir, capsys):
    edit_usgs(tmp_data_dir, set_flow("2026-09-23", "-999999"))
    _, feats = run_features()
    assert feats["date"].iloc[-1] == "2026-09-22"
    assert "1 sentinel" in capsys.readouterr().err


# --- Flood beyond the training range --------------------------------------------------


def test_flood_far_above_training_range(
    raw, install_checkpoint, run_features, tmp_data_dir
):
    """The synthetic checkpoint saw flows around 20 cfs; a 50,000 cfs day must pass
    through unclipped, and the log-change target keeps the fan anchored to it."""
    install_checkpoint()
    edit_usgs(tmp_data_dir, set_flow("2026-09-22", "30000.0"))
    edit_usgs(tmp_data_dir, set_flow("2026-09-23", "50000.0"))
    _, code, out, feats = run_chain(run_features, tmp_data_dir)
    assert feats.iloc[-1]["flow_cfs"] == 50000.0
    assert code == 0
    assert_bands_sane(out)
    ratio = out["median_cfs"] / 50000.0
    assert ((ratio > math.exp(-3)) & (ratio < math.exp(3))).all()


# --- Open-Meteo: short forecast, rate-limit error body --------------------------------


def test_rate_limited_forecast_then_forecast_refuses(
    raw,
    load_fixture,
    install_checkpoint,
    run_features,
    tmp_data_dir,
    monkeypatch,
    capsys,
):
    """Today (09-25) the forecast endpoint answers 429 with Open-Meteo's error body on
    every attempt: no weather_fc file, ingest exits 1, and Forecast refuses because
    the newest forecast weather is from 09-24."""
    monkeypatch.setenv("AS_OF_DATE", "2026-09-25")
    monkeypatch.setattr(ingest, "_sleep", lambda s: None)
    install_checkpoint()
    body = {"error": True, "reason": "Too many concurrent requests"}
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        r.get(ingest.USGS_URL, json=load_fixture("usgs_daily_recent.json"))
        r.get(ingest.FORECAST_URL, status=429, json=body)
        assert ingest.main() == 1
        assert (
            len([c for c in r.calls if c.request.url.startswith(ingest.FORECAST_URL)])
            == 3
        )
    assert len(list((tmp_data_dir / "raw" / "weather_fc").glob("*.csv"))) == 1  # old
    _, code, out, _ = run_chain(run_features, tmp_data_dir)
    assert code == 1 and out is None
    err = capsys.readouterr().err
    assert "weather_fc: HTTP 429 after 3 attempts" in err
    assert "forecast weather is stale (fetched 2026-09-24)" in err


# --- Wrong request timezone -----------------------------------------------------------


def test_every_openmeteo_source_rejects_a_foreign_timezone(
    tmp_data_dir, load_fixture, monkeypatch, capsys
):
    """All three Open-Meteo sources answer in GMT: none is written (AC-1.10)."""
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    monkeypatch.setenv("MRMS_ENABLED", "0")
    names = {
        ingest.HIST_URL: "openmeteo_hist_forecast.json",
        ingest.LEADS_URL: "openmeteo_previous_runs.json",
        ingest.FORECAST_URL: "openmeteo_forecast_past7.json",
    }
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        r.get(ingest.USGS_URL, json=load_fixture("usgs_daily_recent.json"))
        for url, name in names.items():
            r.get(url, json={**load_fixture(name), "timezone": "GMT"})
        assert ingest.main() == 1
    for kind in ("weather_hist", "weather_leads", "weather_fc"):
        assert not list((tmp_data_dir / "raw" / kind).glob("*.csv")), kind
    assert capsys.readouterr().err.count("timezone mismatch") == 3


# --- Checkpoints: missing, corrupt ----------------------------------------------------


def truncate(path):
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])


def empty(path):
    path.write_bytes(b"")


@pytest.mark.parametrize("damage", [truncate, empty], ids=["truncated", "zero-bytes"])
def test_damaged_checkpoint_file_refuses(
    raw, install_checkpoint, run_features, tmp_data_dir, capsys, damage
):
    model = install_checkpoint()
    damage(model)
    _, code, out, _ = run_chain(run_features, tmp_data_dir)
    assert code == 1 and out is None
    err = capsys.readouterr().err
    assert "ERROR forecast cannot load" in err and model.name in err


def rewrite(path, fn):
    checkpoint = joblib.load(path)
    fn(checkpoint)
    joblib.dump(checkpoint, path)


def bad_model(c):
    c["models"][2] = "not a model"


def bad_feature_columns(c):
    c["feature_columns"] = c["feature_columns"] + ["no_such_column"]


def bad_quantile_keys(c):
    c["residual_quantiles"][1] = {"0.5": 0.0}


@pytest.mark.xfail(
    strict=True,
    reason="TF16: a checkpoint that loads but has bad contents escapes as a "
    "traceback instead of AC-4.6's ERROR naming the file",
)
@pytest.mark.parametrize(
    "damage",
    [bad_model, bad_feature_columns, bad_quantile_keys],
    ids=["model-not-estimator", "unknown-feature-column", "missing-quantiles"],
)
def test_loadable_but_broken_checkpoint_refuses_cleanly(
    raw, install_checkpoint, run_features, tmp_data_dir, capsys, damage
):
    model = install_checkpoint()
    rewrite(model, damage)
    run_features()
    assert forecast.main() == 1  # an uncaught exception fails here
    assert model.name in capsys.readouterr().err


def test_disordered_quantiles_are_refused_not_published(
    raw, install_checkpoint, run_features, tmp_data_dir, capsys
):
    """The AC-4.3 guard: quantiles out of order never reach a forecast file."""
    model = install_checkpoint()
    rewrite(
        model,
        lambda c: c["residual_quantiles"].__setitem__(
            1, {"0.025": 0.5, "0.1": 0.4, "0.5": 0.0, "0.9": -0.4, "0.975": -0.5}
        ),
    )
    _, code, out, _ = run_chain(run_features, tmp_data_dir)
    assert code == 1 and out is None
    assert "bands out of order" in capsys.readouterr().err


# --- Stale observations ---------------------------------------------------------------


@pytest.mark.parametrize("today, stale", [("2026-09-27", 3), ("2026-10-01", 7)])
def test_several_days_stale_refuses_even_with_fresh_weather(
    raw,
    install_checkpoint,
    run_features,
    tmp_data_dir,
    monkeypatch,
    capsys,
    today,
    stale,
):
    install_checkpoint()
    run_features()
    path = sorted((tmp_data_dir / "features").glob("features_*.csv"))[-1]
    df = pd.read_csv(path, dtype={"date": str, "fc_fetched_date": str})
    df.loc[df.index[-1], "fc_fetched_date"] = today  # weather fresh; only USGS is old
    df.to_csv(path, index=False)
    monkeypatch.setenv("AS_OF_DATE", today)
    assert forecast.main() == 1
    assert not list((tmp_data_dir / "forecasts").glob("*"))
    assert f"is {stale} days stale (limit 2)" in capsys.readouterr().err


# --- Interval ordering on every row ---------------------------------------------------


def test_band_ordering_on_every_row_of_a_long_record(
    published_checkpoint, synthetic_features
):
    """Serve every row of an 8-year table, plus extreme flows, through Forecast's
    make_forecast: every one of the ~9,000 served rows is ordered and >= 0."""
    checkpoint = joblib.load(published_checkpoint[0])
    table = synthetic_features()
    extremes = table.tail(6).copy()
    for i, flow in enumerate([0.0, 0.01, 0.22, 8180.0, 50000.0, 1e6]):
        lq = math.log1p(flow)
        extremes.iloc[i, extremes.columns.get_loc("flow_cfs")] = flow
        for c in ("logq_0", "logq_1", "logq_2"):
            extremes.iloc[i, extremes.columns.get_loc(c)] = lq
        extremes.iloc[i, extremes.columns.get_loc("dlogq_1")] = 0.0
    rows = pd.concat([table, extremes], ignore_index=True)
    rows["api_7"] = rows["api_7"].fillna(0.0)
    rows["api_30"] = rows["api_30"].fillna(0.0)
    served = pd.concat(
        [forecast.make_forecast(checkpoint, r, 0, "x") for _, r in rows.iterrows()]
    )
    assert len(served) == 3 * len(rows)
    assert_bands_sane(served)


# --- Leakage through the real file path -----------------------------------------------


@pytest.mark.parametrize("d0", ["2025-06-28", "2025-07-02", "2025-07-05", "2025-07-06"])
def test_truncating_the_future_changes_only_targets_and_forecast_rain(
    raw_from_fixtures, write_raw, run_features, tmp_data_dir, d0
):
    """Build Chantal features from the full raw files, then again from raw files cut
    at d0 (flow) and d0+3 (weather). Row d0 must match except for the targets, and
    precip_f*, which the cut legitimately removes or which become the live row's."""
    raw_from_fixtures(
        usgs="usgs_daily_chantal.json", weather_hist="openmeteo_hist_chantal_et.json"
    )
    _, full = run_features()
    cut_flow = pd.read_csv(usgs_csv(tmp_data_dir), dtype=str, keep_default_na=False)
    hist_path = next((tmp_data_dir / "raw" / "weather_hist").glob("*.csv"))
    hist = pd.read_csv(hist_path, dtype=str, keep_default_na=False)
    limit = (pd.Timestamp(d0) + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    for path in (usgs_csv(tmp_data_dir), hist_path):
        path.unlink()
    for path in (tmp_data_dir / "features").glob("*"):
        path.unlink()
    write_raw("usgs", cut_flow[cut_flow["date"] <= d0])
    write_raw("weather_hist", hist[hist["date"] <= limit])
    _, cut = run_features()
    a = full.set_index("date").loc[d0]
    b = cut.set_index("date").loc[d0]
    skip = {
        "y_1",
        "y_2",
        "y_3",
        "precip_f1",
        "precip_f2",
        "precip_f3",
        "weather_lead_matched",
        "fc_fetched_date",
    }
    for column in a.index.difference(sorted(skip)):
        same = a[column] == b[column] or (pd.isna(a[column]) and pd.isna(b[column]))
        assert same, (d0, column, a[column], b[column])
    assert cut.iloc[-1]["date"] == d0  # d0 really was the newest data in the cut run


# --- Ingest robustness (TF1) ----------------------------------------------------------


def _all_sources(r, load_fixture):
    bodies = {
        ingest.USGS_URL: load_fixture("usgs_daily_recent.json"),
        ingest.HIST_URL: load_fixture("openmeteo_hist_forecast.json"),
        ingest.LEADS_URL: load_fixture("openmeteo_previous_runs.json"),
        ingest.FORECAST_URL: load_fixture("openmeteo_forecast_past7.json"),
        MRMS_RE: load_fixture("iem_mrms_recent.json"),
    }
    for url, body in bodies.items():
        r.get(url, json=body)


def _usgs_without_time(load_fixture):
    data = load_fixture("usgs_daily_recent.json")
    del data["features"][0]["properties"]["time"]
    return {ingest.USGS_URL: data}


def _hist_null_array(load_fixture):
    data = load_fixture("openmeteo_hist_forecast.json")
    data["daily"]["precipitation_sum"] = None
    return {ingest.HIST_URL: data}


def _mrms_none_record(load_fixture):
    data = load_fixture("iem_mrms_recent.json")
    data["data"].append(None)
    return {MRMS_RE: data}


@pytest.mark.xfail(
    strict=True,
    reason="TF1: a wrong-shape JSON response raises past main(), so later sources "
    "are skipped and MRMS can change the exit status",
)
@pytest.mark.parametrize(
    "broken, failed_kind, expected_exit",
    [
        (_usgs_without_time, "usgs", 1),
        (_hist_null_array, "weather_hist", 1),
        (_mrms_none_record, "mrms", 0),
    ],
    ids=["usgs-missing-time", "hist-null-array", "mrms-none-record"],
)
def test_wrong_shape_response_fails_only_that_source(
    tmp_data_dir, load_fixture, monkeypatch, broken, failed_kind, expected_exit
):
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    monkeypatch.setenv("HISTORY_START", "2026-09-01")
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        _all_sources(r, load_fixture)
        for url, body in broken(load_fixture).items():
            r.replace(responses.GET, url, json=body)
        assert ingest.main() == expected_exit
    for kind in ("usgs", "weather_hist", "weather_leads", "weather_fc", "mrms"):
        n = len(list((tmp_data_dir / "raw" / kind).glob("*.csv")))
        assert n == (0 if kind == failed_kind else 1), kind
