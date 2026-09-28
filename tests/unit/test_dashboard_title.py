from datetime import date

import pandas as pd
import pytest

from streamforecast import dashboard

pytestmark = pytest.mark.unit


def forecast(latest, medians, issue="2026-09-23", **extra):
    days = pd.date_range(issue, periods=4).strftime("%Y-%m-%d")[1:]
    rows = {
        "issue_date": issue,
        "valid_date": list(days),
        "horizon": [1, 2, 3],
        "median_cfs": list(medians),
        "lo80_cfs": [m * 0.7 for m in medians],
        "hi80_cfs": [m * 1.4 for m in medians],
        "lo95_cfs": [m * 0.4 for m in medians],
        "hi95_cfs": [m * 3 for m in medians],
        "persistence_cfs": latest,
        "latest_obs_cfs": latest,
        "latest_obs_provisional": False,
        "latest_obs_estimated": False,
        "stale_days": 0,
        "created_at_utc": "2026-09-24T16:00:00Z",
        "model_version": "20260924T160000Z",
    }
    rows.update(extra)
    return pd.DataFrame(rows)


# --- AC-5.3 -------------------------------------------------------------------------


def test_headline_rise_fall_hold():
    # 2026-09-26 (h=3) is a Saturday.
    assert dashboard.headline(forecast(7.22, [8, 9, 123.4])) == (
        "Flow expected to rise to about 120 cfs by Saturday"
    )
    assert dashboard.headline(forecast(100.0, [90, 80, 55])) == (
        "Flow expected to fall to about 55 cfs by Saturday"
    )
    assert dashboard.headline(forecast(100.0, [100, 100, 109])) == (
        "Flow expected to hold near 110 cfs through Saturday"
    )
    assert dashboard.headline(forecast(7.22, [7, 7, 5.28])) == (
        "Flow expected to fall to about 5.3 cfs by Saturday"
    )


def test_headline_hold_boundary_is_ten_percent():
    assert "hold" in dashboard.headline(forecast(100.0, [100, 100, 110.0]))
    assert "rise" in dashboard.headline(forecast(100.0, [100, 100, 110.1]))
    assert "hold" in dashboard.headline(forecast(100.0, [100, 100, 90.0]))
    assert "fall" in dashboard.headline(forecast(100.0, [100, 100, 89.9]))


def test_headline_is_a_sentence_not_a_variable_name():
    text = dashboard.headline(forecast(7.22, [8, 9, 10]))
    assert text.startswith("Flow expected to ")
    assert "median_cfs" not in text and "_" not in text


@pytest.mark.parametrize(
    "cfs, text", [(0.0, "0.0"), (8.66, "8.7"), (123.4, "120"), (8180, "8,200")]
)
def test_about_rounds_readably(cfs, text):
    assert dashboard.about(cfs) == text


# --- AC-5.5 (pure part) -----------------------------------------------------------


def test_notices_for_outdated_stale_and_flags():
    f = forecast(
        7.22,
        [8, 9, 10],
        stale_days=1,
        latest_obs_provisional=True,
        latest_obs_estimated=True,
        created_at_utc="2026-09-23T03:30:00Z",  # 2026-09-22 23:30 in New York
    )
    kinds = dashboard.notices(f, date(2026, 9, 24), "America/New_York")
    texts = [t for _, t in kinds]
    assert (
        "Forecast outdated — last issued 2026-09-22 from data through 2026-09-23"
        in texts
    )
    assert any("1 day(s) behind" in t for t in texts)
    assert any("provisional and estimated" in t for t in texts)
    assert [k for k, _ in kinds] == ["warning", "warning", "info"]


def test_no_notices_for_a_fresh_final_forecast():
    f = forecast(7.22, [8, 9, 10])
    assert dashboard.notices(f, date(2026, 9, 24), "America/New_York") == []


def test_fan_frame_marks_past_points_and_anchors_at_d0():
    fan = dashboard.fan_frame(forecast(7.22, [8, 9, 10]), date(2026, 9, 25))
    assert list(fan["valid_date"]) == [
        "2026-09-23",
        "2026-09-24",
        "2026-09-25",
        "2026-09-26",
    ]
    assert list(fan["status"]) == ["observed", "past", "forecast", "forecast"]
    assert fan.loc[0, "median_cfs"] == fan.loc[0, "hi95_cfs"] == 7.22


def test_median_line_splits_into_grey_past_and_blue_forecast():
    fan = dashboard.fan_frame(forecast(7.22, [8, 9, 10]), date(2026, 9, 25))
    seg = dashboard.median_segments(fan)
    past = seg.loc[seg["status"] == "past", "valid_date"].tolist()
    ahead = seg.loc[seg["status"] == "forecast", "valid_date"].tolist()
    assert past == ["2026-09-23", "2026-09-24"]  # d0 anchor through the past day
    assert ahead == ["2026-09-24", "2026-09-25", "2026-09-26"]  # joins at 09-24
    assert dashboard.status_scale(fan).domain == ["forecast", "past"]


def test_no_past_segment_or_legend_entry_when_nothing_is_past():
    fan = dashboard.fan_frame(forecast(7.22, [8, 9, 10]), date(2026, 9, 24))
    seg = dashboard.median_segments(fan)
    assert set(seg["status"]) == {"forecast"}
    assert seg["valid_date"].tolist()[0] == "2026-09-23"  # starts at the anchor
    scale = dashboard.status_scale(fan)
    assert scale.domain == ["forecast"] and scale.range == ["#08306b"]


def features_table(last_date="2026-09-23"):
    dates = pd.date_range("2026-07-01", last_date).strftime("%Y-%m-%d")
    frame = pd.DataFrame({"date": dates, "flow_cfs": 10.0, "precip_0": 2.0})
    frame.loc[frame["date"] == "2026-09-18", "precip_0"] = 51.5
    for h, rain in zip((1, 2, 3), (0.8, 0.0, 12.0)):
        frame[f"precip_f{h}"] = rain
    return frame


def test_rain_frame_past_window_and_forecast_rain():
    rain = dashboard.rain_frame(features_table(), "2026-09-23")
    past = rain.loc[rain["kind"] == "past"]
    ahead = rain.loc[rain["kind"] == "forecast"]
    assert len(past) == 60 and past["date"].max() == "2026-09-23"
    assert past.loc[past["date"] == "2026-09-18", "rain_mm"].item() == 51.5
    assert ahead["date"].tolist() == ["2026-09-24", "2026-09-25", "2026-09-26"]
    assert ahead["rain_mm"].tolist() == [0.8, 0.0, 12.0]  # the live row's precip_f


def test_rain_frame_skips_forecast_rain_from_a_newer_features_row():
    """If features are newer than the forecast, their live row is not what the
    displayed forecast used, so no forecast bars are drawn."""
    rain = dashboard.rain_frame(features_table("2026-09-24"), "2026-09-23")
    assert set(rain["kind"]) == {"past"}
    assert dashboard.rain_frame(None, "2026-09-23").empty


def test_chart_caption_mentions_only_what_is_drawn():
    rain = dashboard.rain_frame(features_table(), "2026-09-23")
    fresh = dashboard.fan_frame(forecast(7.22, [8, 9, 10]), date(2026, 9, 24))
    stale = dashboard.fan_frame(forecast(7.22, [8, 9, 10]), date(2026, 9, 25))
    assert "grey" not in dashboard.chart_caption(fresh, rain)
    assert "grey: forecast days already past" in dashboard.chart_caption(stale, rain)
    assert "orange: forecast rain" in dashboard.chart_caption(fresh, rain)
    only_past = rain.loc[rain["kind"] == "past"]
    assert "orange: forecast rain" not in dashboard.chart_caption(fresh, only_past)


def test_log_axis_floors_zero_bounds_but_tooltips_keep_true_values():
    f = forecast(0.3, [0.2, 0.1, 0.05])
    f["lo95_cfs"] = 0.0  # a clipped-at-zero bound (possible at very low flow)
    fan = dashboard.fan_frame(f, date(2026, 9, 24))
    history = pd.DataFrame(
        {"date": ["2026-09-22", "2026-09-23"], "flow_cfs": [0.3, 0.3]}
    )
    rain = dashboard.rain_frame(features_table(), "2026-09-23")
    chart = dashboard.fan_chart(history, fan, rain).to_dict()
    flow_data = [
        rows
        for rows in chart["datasets"].values()
        if rows and "lo95_cfs_value" in rows[0]
    ]
    assert flow_data  # the band, point and persistence layers each get a copy
    band_rows = max(flow_data, key=len)  # the full fan (anchor + 3 days)
    assert min(r["lo95_cfs"] for r in band_rows) == dashboard.LOG_FLOOR_CFS
    assert min(r["lo95_cfs_value"] for r in band_rows) == 0.0
    for rows in flow_data:
        assert all(r["lo95_cfs"] >= dashboard.LOG_FLOOR_CFS for r in rows)
