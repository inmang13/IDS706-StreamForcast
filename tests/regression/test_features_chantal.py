"""Tropical Storm Chantal (July 2025) on real recorded data: AC-2.3 and AC-2.5."""

import pandas as pd
import pytest

from streamforecast import ingest

pytestmark = pytest.mark.regression

HEAVY_RAIN_MM = 25.0
RISE_FACTOR = 10.0


def lag0_check(out: pd.DataFrame) -> tuple[str, float, str]:
    """(first day flow rises > 10x the day before, precip_0 that day, wettest day).

    Rain and flow line up at lag 0 when the jump day has >= 25 mm and is the
    window's wettest day. (The plan's "first >= 25 mm day" reading fails on the real
    record: 2025-07-02 had 35.7 mm with a same-day 2.8x rise; see Implementation
    notes, Stage 2.)
    """
    rows = out.set_index("date")
    rise = rows["flow_cfs"] > RISE_FACTOR * rows["flow_cfs"].shift(1)
    jump_day = rows.index[rise][0]
    return jump_day, rows.loc[jump_day, "precip_0"], rows["precip_0"].idxmax()


@pytest.fixture
def chantal(raw_from_fixtures, run_features):
    raw_from_fixtures(
        usgs="usgs_daily_chantal.json",
        weather_hist="openmeteo_hist_chantal_et.json",
        mrms="iem_mrms_chantal.json",
    )
    code, out = run_features()
    assert code == 0
    return out


def test_chantal_peak_survives(chantal):
    rows = chantal.set_index("date")
    assert rows.loc["2025-07-07", "flow_cfs"] == 8180.0  # exact, not clipped
    assert rows.loc["2025-07-07", "qualifier"] == "ESTIMATED"  # flagged, kept
    assert chantal["flow_cfs"].max() == 8180.0


def test_no_clipping_code_in_features():
    from pathlib import Path

    import streamforecast

    text = (Path(streamforecast.__file__).parent / "features.py").read_text("utf-8")
    for word in ("clip(", "winsor", "quantile(", "zscore", "outlier"):
        assert word not in text.lower(), word


def test_chantal_rain_and_flow_same_local_date(chantal):
    rows = chantal.set_index("date")
    assert rows.loc["2025-07-05", "flow_cfs"] == 15.5
    assert rows.loc["2025-07-06", "precip_0"] == 54.7
    assert rows.loc["2025-07-06", "flow_cfs"] == 1990.0
    assert rows.loc["2025-07-07", "flow_cfs"] == 8180.0
    assert rows.loc["2025-07-09", "precip_0"] == 49.7
    assert rows.loc["2025-07-10", "flow_cfs"] == 922.0
    assert rows.loc["2025-07-10", "flow_cfs"] > rows.loc["2025-07-09", "flow_cfs"]
    jump_day, jump_rain, wettest = lag0_check(chantal)
    assert jump_day == wettest == "2025-07-06"  # lag 0
    assert jump_rain >= HEAVY_RAIN_MM


def test_mrms_peak_day_matches_flow_jump(chantal, tmp_data_dir):
    (path,) = (tmp_data_dir / "features").glob("mrms_*.csv")
    mrms = pd.read_csv(path, dtype={"date": str}).set_index("date")["mrms_mm"]
    assert mrms.idxmax() == "2025-07-06"
    assert mrms.max() == pytest.approx(7.01 * 25.4)
    jump_day, _, wettest = lag0_check(chantal)
    assert mrms.idxmax() == jump_day == wettest


def test_gmt_fixture_breaks_alignment(
    load_fixture, raw_from_fixtures, write_raw, run_features
):
    gmt = load_fixture("openmeteo_hist_chantal_gmt.json")
    # Ingest rejects it (AC-1.10) ...
    with pytest.raises(ingest.IngestError, match="timezone mismatch"):
        ingest.check_openmeteo(gmt, "daily", ingest.DAILY_VARS, "America/New_York")
    # ... and when that check is bypassed, the lag-0 alignment check fails.
    raw_from_fixtures(usgs="usgs_daily_chantal.json")
    rows = ingest.parse_daily_weather(gmt, "historical_forecast", "x")
    write_raw("weather_hist", pd.DataFrame(rows, columns=ingest.WEATHER_COLUMNS))
    _, out = run_features()
    jump_day, jump_rain, wettest = lag0_check(out)
    assert jump_day == "2025-07-06"
    assert wettest == "2025-07-07"  # the heavy rain lands a day after the rise
    assert jump_rain < HEAVY_RAIN_MM  # so the lag-0 check fails
