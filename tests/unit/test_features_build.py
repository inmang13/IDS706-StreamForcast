import json
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from streamforecast import config, features

pytestmark = pytest.mark.unit


def days(start, n):
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def flow_frame(dates, values):
    return pd.DataFrame(
        {
            "date": dates,
            "flow_cfs": values,
            "approval_status": "Approved",
            "qualifier": "",
        }
    )


def weather_frame(dates, value):
    return pd.DataFrame(
        {"date": dates, "precip_mm": value, "tmax_c": value, "tmin_c": value}
    )


def leads_frame(dates, lead1, lead2):
    return pd.DataFrame(
        {
            "date": [d for d in dates for _ in (1, 2)],
            "lead_days": [k for _ in dates for k in (1, 2)],
            "precip_mm": [v for _ in dates for v in (lead1, lead2)],
        }
    )


def forecast(dates, value, fetched="2026-09-24"):
    return features.Forecast(pd.DataFrame({"date": dates, "precip_mm": value}), fetched)


SETTINGS = config.Settings()


# --- AC-2.4 -------------------------------------------------------------------------


def test_gap_1_day_filled_gap_10_days_not():
    dates = days("2026-01-01", 40)
    values = [float(10 + i) for i in range(40)]
    values[5] = np.nan  # 1-day gap: 01-06
    for i in range(20, 30):  # 10-day gap: 01-21..01-30
        values[i] = np.nan
    out, stats = features.build_features(
        flow_frame(dates, values),
        weather_frame(dates, 0.0),
        leads_frame([], 0, 0),
        None,
        SETTINGS,
    )
    rows = out.set_index("date")
    assert stats["filled_days"] == 1
    # The 1-day gap is filled for the lags of 01-07 and 01-08, from 01-05 (14 cfs).
    assert rows.loc["2026-01-07", "logq_1"] == pytest.approx(math.log1p(14.0))
    assert rows.loc["2026-01-08", "logq_2"] == pytest.approx(math.log1p(14.0))
    assert rows.loc["2026-01-07", "flow_filled"]
    assert not rows.loc["2026-01-09", "flow_filled"]
    # No row is issued on a missing day, and targets are never filled.
    assert "2026-01-06" not in rows.index
    assert math.isnan(rows.loc["2026-01-05", "y_1"])
    # The 10-day gap stays NaN: 01-31 and 02-01 would need lags inside it.
    assert "2026-01-31" not in rows.index and "2026-02-01" not in rows.index
    assert stats["dropped_incomplete_lags"] == 2 + 2  # first two days + after the gap
    assert rows.loc["2026-02-02", "logq_2"] == pytest.approx(math.log1p(40.0))


def test_latest_row_kept_with_incomplete_lags():
    dates = days("2026-09-01", 23)
    values = [5.0] * 23
    values[19:22] = [np.nan] * 3  # 3-day gap ending at d0-1 (AC-4.9 input)
    out, _ = features.build_features(
        flow_frame(dates, values),
        weather_frame(dates, 0.0),
        leads_frame([], 0, 0),
        forecast(days("2026-09-17", 10), 1.0),
        SETTINGS,
    )
    last = out.iloc[-1]
    assert last["date"] == "2026-09-23"
    assert math.isnan(last["logq_1"]) and math.isnan(last["logq_2"])


# --- AC-2.7 -------------------------------------------------------------------------


def test_output_columns_and_latest_row(raw_from_fixtures, run_features):
    raw_from_fixtures(
        usgs="usgs_daily_recent.json",
        weather_hist="openmeteo_hist_forecast.json",
        weather_fc="openmeteo_forecast_past7.json",
        weather_leads="openmeteo_previous_runs.json",
    )
    code, out = run_features()
    assert code == 0
    assert list(out.columns) == features.FEATURE_COLUMNS
    last = out.iloc[-1]
    assert last["date"] == "2026-09-23"
    assert [last["precip_f1"], last["precip_f2"], last["precip_f3"]] == [0.8, 0.0, 0.0]
    assert last["fc_fetched_date"] == "2026-09-24"
    assert last["weather_lead_matched"]
    assert all(math.isnan(last[f"y_{h}"]) for h in (1, 2, 3))
    assert out["fc_fetched_date"].iloc[:-1].isna().all()  # set only on the live row
    assert last["approval_status"] == "Provisional"


def test_latest_row_uses_only_the_newest_forecast_file(
    raw_from_fixtures, write_raw, load_fixture, run_features
):
    raw_from_fixtures(
        usgs="usgs_daily_recent.json", weather_hist="openmeteo_hist_forecast.json"
    )
    raw_from_fixtures(
        stamp="20260923T160000Z", weather_fc="openmeteo_forecast_past7.json"
    )
    raw_from_fixtures(weather_fc="openmeteo_forecast_short.json")  # newest: 2 days
    _, out = run_features()
    last = out.iloc[-1]
    assert [last["precip_f1"], last["precip_f2"]] == [0.8, 0.0]
    assert math.isnan(last["precip_f3"])  # not back-filled from the older file


def test_no_forecast_file_leaves_latest_forecast_rain_empty(
    raw_from_fixtures, run_features, capsys
):
    raw_from_fixtures(
        usgs="usgs_daily_recent.json", weather_hist="openmeteo_hist_forecast.json"
    )
    code, out = run_features()
    assert code == 0
    last = out.iloc[-1]
    assert all(math.isnan(last[f"precip_f{h}"]) for h in (1, 2, 3))
    assert pd.isna(last["fc_fetched_date"])
    assert "no weather_fc file" in capsys.readouterr().err


def test_prunes_old_feature_files(raw_from_fixtures, run_features, tmp_data_dir):
    raw_from_fixtures(usgs="usgs_daily_recent.json")
    out_dir = tmp_data_dir / "features"
    out_dir.mkdir(parents=True)
    for i in range(6):
        (out_dir / f"features_2026010{i}T000000Z.csv").write_text("old")
    run_features()
    kept = sorted(p.name for p in out_dir.glob("features_*.csv"))
    assert len(kept) == 5
    assert "features_20260100T000000Z.csv" not in kept
    assert kept[-1] > "features_20260105T000000Z.csv"  # the new file is kept


def test_no_usgs_files_is_an_error(tmp_data_dir, run_features, capsys):
    code, out = run_features()
    assert code == 1 and out is None
    assert "no raw USGS files" in capsys.readouterr().err


# --- AC-2.8 -------------------------------------------------------------------------


def test_lead_matched_rain_by_period():
    dates = days("2024-01-20", 30)  # spans CALIBRATION_START (2024-02-01)
    leads = leads_frame(days("2024-01-25", 30), 2.0, 3.0)
    leads.loc[
        (leads["date"] == "2024-02-06") & (leads["lead_days"] == 1), "precip_mm"
    ] = np.nan
    out, stats = features.build_features(
        flow_frame(dates, 10.0),
        weather_frame(days("2024-01-01", 60), 1.0),
        leads,
        None,
        SETTINGS,
    )
    rows = out.set_index("date")
    before, after = rows.loc["2024-01-31"], rows.loc["2024-02-01"]
    assert (before["precip_f1"], before["precip_f2"], before["precip_f3"]) == (1, 1, 1)
    assert not before["weather_lead_matched"]
    assert (after["precip_f1"], after["precip_f2"], after["precip_f3"]) == (1, 2, 3)
    assert after["weather_lead_matched"]
    # A missing lead stays NaN; never back-filled from historical-forecast.
    assert math.isnan(rows.loc["2024-02-04", "precip_f2"])
    assert rows.loc["2024-02-04", "weather_lead_matched"]
    assert stats["lead_matched"] == 17  # 2024-02-01..02-17, excluding the live row


def test_lead_matched_rain_on_real_fixtures(raw_from_fixtures, run_features):
    raw_from_fixtures(
        usgs="usgs_daily_recent.json",
        weather_hist="openmeteo_hist_forecast.json",
        weather_leads="openmeteo_previous_runs.json",
    )
    _, out = run_features()
    row = out.set_index("date").loc["2026-09-16"]
    assert row["precip_f1"] == 0.0  # lead 0 on 09-17
    assert row["precip_f2"] == 0.7  # 09-18 lead 1, not the lead-0 51.5 mm
    assert row["precip_f3"] == 1.2  # 09-19 lead 2


# --- AC-2.10 ------------------------------------------------------------------------


def test_feature_availability_matrix():
    """Each source carries a marker value; every feature must come from the matrix's
    source. hist = 1, lead 1 = 2, lead 2 = 3, weather_fc = 4 (merged with D28)."""
    all_days = days("2023-12-01", 1028)  # through 2026-09-23
    fc_days = days("2026-09-17", 10)
    hist = weather_frame(all_days, 1.0)
    merged = pd.concat(
        [hist[~hist["date"].isin(fc_days)], weather_frame(fc_days, 4.0)]
    ).sort_values("date")
    out, _ = features.build_features(
        flow_frame(all_days, 10.0),
        merged,
        leads_frame(days("2024-01-25", 974), 2.0, 3.0),
        forecast(fc_days, 4.0),
        SETTINGS,
    )
    rows = out.set_index("date")
    weather_cols = ["precip_0", "tmax_0", "tmin_0", "api_7", "api_30"]
    expected = {
        "2024-01-10": ([1, 1, 1, 7, 30], [1, 1, 1], False),  # train
        "2024-06-01": ([1, 1, 1, 7, 30], [1, 2, 3], True),  # calibration
        "2025-06-01": ([1, 1, 1, 7, 30], [1, 2, 3], True),  # test (outside fc window)
        "2026-09-23": ([4, 4, 4, 28, 7 * 4 + 23 * 1], [4, 4, 4], True),  # live
    }
    for d0, (past, future, matched) in expected.items():
        row = rows.loc[d0]
        assert list(row[weather_cols]) == past, d0
        assert list(row[["precip_f1", "precip_f2", "precip_f3"]]) == future, d0
        assert row["weather_lead_matched"] == matched, d0


# --- AC-2.9 -------------------------------------------------------------------------


def test_rain_check_metrics_and_false_alarm(
    raw_from_fixtures, run_features, tmp_data_dir
):
    raw_from_fixtures(
        usgs="usgs_daily_recent.json",
        weather_hist="openmeteo_hist_forecast.json",
        weather_fc="openmeteo_forecast_past7.json",
        weather_leads="openmeteo_previous_runs.json",
        mrms="iem_mrms_recent.json",
    )
    run_features()
    (path,) = (tmp_data_dir / "features").glob("rain_check_*.json")
    check = json.loads(path.read_text())
    lead0 = check["series"]["lead0"]
    # Hand-copied from the fixtures, 2026-09-14..23 (the dates both series cover).
    om = [0.0, 0.0, 0.0, 0.0, 51.5, 0.0, 0.0, 47.2, 0.0, 3.6]
    mrms = [x * 25.4 for x in [0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 1.66, 0.0, 0.59]]
    diff = [a - b for a, b in zip(om, mrms)]
    assert lead0["n"] == 10
    assert lead0["bias_mm"] == pytest.approx(sum(diff) / 10, abs=1e-3)
    assert lead0["mae_mm"] == pytest.approx(sum(map(abs, diff)) / 10, abs=1e-3)
    assert (lead0["hits"], lead0["misses"], lead0["false_alarms"]) == (1, 0, 1)
    top = lead0["top_disagreements"][0]
    assert top == {"date": "2026-09-18", "openmeteo_mm": 51.5, "mrms_mm": 0.0}
    assert check["series"]["lead1"]["n"] == 10
    assert check["from"] == "2024-02-01"
    mrms_csv = pd.read_csv(next((tmp_data_dir / "features").glob("mrms_*.csv")))
    assert list(mrms_csv.columns) == ["date", "mrms_mm"]


def test_no_mrms_column_in_features(raw_from_fixtures, run_features):
    raw_from_fixtures(
        usgs="usgs_daily_recent.json",
        weather_hist="openmeteo_hist_forecast.json",
        mrms="iem_mrms_recent.json",
    )
    _, out = run_features()
    assert not [c for c in out.columns if "mrms" in c.lower()]


def test_rain_check_skipped_without_mrms(
    raw_from_fixtures, run_features, tmp_data_dir, capsys
):
    names = dict(
        usgs="usgs_daily_recent.json",
        weather_hist="openmeteo_hist_forecast.json",
        weather_fc="openmeteo_forecast_past7.json",
        weather_leads="openmeteo_previous_runs.json",
    )
    raw_from_fixtures(**names)
    run_features()
    without = sorted((tmp_data_dir / "features").glob("features_*.csv"))[-1]
    assert "no raw MRMS files; rain check skipped" in capsys.readouterr().err
    assert list((tmp_data_dir / "features").glob("rain_check_*")) == []
    without_bytes = without.read_bytes()
    without.unlink()

    raw_from_fixtures(mrms="iem_mrms_recent.json")
    run_features()
    with_mrms = sorted((tmp_data_dir / "features").glob("features_*.csv"))[-1]
    assert with_mrms.read_bytes() == without_bytes
