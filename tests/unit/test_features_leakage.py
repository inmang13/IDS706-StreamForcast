"""AC-2.6: no feature on row d0 uses information from after d0, except precip_f1..f3.

Property test on perturbed copies of a synthetic record (fixed seed) that spans
CALIBRATION_START, so both historical-forecast and lead-matched rows are checked.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from streamforecast import config, features

pytestmark = pytest.mark.unit

SETTINGS = config.Settings()
TARGETS = ["y_1", "y_2", "y_3"]
FORECAST_RAIN = ["precip_f1", "precip_f2", "precip_f3"]
N = 90


def days(start, n):
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


DATES = days("2024-01-01", N)  # CALIBRATION_START 2024-02-01 is day 31
FC_DATES = days("2024-03-24", 10)  # the newest forecast file around the live row


def base_inputs():
    rng = np.random.default_rng(0)
    flow = rng.gamma(2.0, 20.0, N)
    flow[[10, 11, 40]] = np.nan  # short gaps that get filled
    flow_df = pd.DataFrame(
        {
            "date": DATES,
            "flow_cfs": flow,
            "approval_status": "Approved",
            "qualifier": "",
        }
    ).dropna()
    weather = pd.DataFrame(
        {
            "date": DATES,
            "precip_mm": rng.exponential(3.0, N),
            "tmax_c": rng.normal(15, 5, N),
            "tmin_c": rng.normal(5, 5, N),
        }
    )
    leads = pd.DataFrame(
        {
            "date": [d for d in DATES for _ in (1, 2)],
            "lead_days": [1, 2] * N,
            "precip_mm": rng.exponential(3.0, 2 * N),
        }
    )
    fc = features.Forecast(
        pd.DataFrame({"date": FC_DATES, "precip_mm": rng.exponential(3.0, 10)}),
        "2024-03-31",
    )
    return flow_df, weather, leads, fc


def build(flow, weather, leads, fc):
    out, _ = features.build_features(flow, weather, leads, fc, SETTINGS)
    return out.set_index("date")


BASE = build(*base_inputs())
ISSUE_DATES = [d for d in BASE.index[2:-4:3]]  # excludes the live row


def changed_columns(row_a, row_b):
    a, b = row_a.astype(object), row_b.astype(object)
    return {
        c for c in a.index if not (a[c] == b[c] or (pd.isna(a[c]) and pd.isna(b[c])))
    }


@pytest.mark.parametrize("d0", ISSUE_DATES)
def test_future_flow_changes_nothing_but_targets(d0):
    flow, weather, leads, fc = base_inputs()
    after = flow["date"] > d0
    flow.loc[after, "flow_cfs"] *= 3.0
    # Also delete some future days (not the latest): fill decisions must not care.
    drop = flow.index[after & (flow["date"] < flow["date"].max())][::4]
    perturbed = build(flow.drop(index=drop), weather, leads, fc)
    assert changed_columns(BASE.loc[d0], perturbed.loc[d0]) <= set(TARGETS)


@pytest.mark.parametrize("d0", ISSUE_DATES)
def test_future_weather_changes_only_forecast_rain(d0):
    flow, weather, leads, fc = base_inputs()
    weather.loc[weather["date"] > d0, ["precip_mm", "tmax_c", "tmin_c"]] += 7.0
    leads.loc[leads["date"] > d0, "precip_mm"] += 7.0
    fc.frame["precip_mm"] += 7.0
    perturbed = build(flow, weather, leads, fc)
    assert changed_columns(BASE.loc[d0], perturbed.loc[d0]) <= set(FORECAST_RAIN)


@pytest.mark.parametrize("d0", ISSUE_DATES)
def test_weather_after_d0_plus_3_changes_nothing(d0):
    flow, weather, leads, fc = base_inputs()
    cutoff = (date.fromisoformat(d0) + timedelta(days=3)).isoformat()
    weather.loc[weather["date"] > cutoff, ["precip_mm", "tmax_c", "tmin_c"]] += 7.0
    leads.loc[leads["date"] > cutoff, "precip_mm"] += 7.0
    perturbed = build(flow, weather, leads, fc)
    assert changed_columns(BASE.loc[d0], perturbed.loc[d0]) == set()


def test_perturbation_is_detected():
    """Sensitivity check: changing weather *on* d0 does change row d0."""
    d0 = ISSUE_DATES[-1]  # late enough that api_30 is defined
    flow, weather, leads, fc = base_inputs()
    weather.loc[weather["date"] == d0, "precip_mm"] += 7.0
    perturbed = build(flow, weather, leads, fc)
    assert {"precip_0", "api_7", "api_30"} <= changed_columns(
        BASE.loc[d0], perturbed.loc[d0]
    )


def test_issue_dates_cover_both_periods():
    assert ISSUE_DATES[0] < SETTINGS.calibration_start.isoformat() < ISSUE_DATES[-1]
    assert len(ISSUE_DATES) >= 20
