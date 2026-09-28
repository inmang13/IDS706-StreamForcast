import pandas as pd
import pytest

from streamforecast import features

pytestmark = pytest.mark.unit


def usgs(dates, flows, status="Provisional"):
    return pd.DataFrame(
        {
            "date": dates,
            "flow_cfs": flows,
            "approval_status": status,
            "qualifier": "",
            "last_modified": "x",
        }
    )


def weather(dates, precip, source):
    return pd.DataFrame(
        {
            "date": dates,
            "precip_mm": precip,
            "tmax_c": 20.0,
            "tmin_c": 10.0,
            "source": source,
            "fetched_at_utc": "2026-09-24T16:00:00Z",
            "grid_lat": 36.072254,
            "grid_lon": -79.1094,
            "elevation": 163.0,
        }
    )


# --- AC-2.1 -------------------------------------------------------------------------


def test_newest_file_wins(write_raw, tmp_data_dir):
    write_raw(
        "usgs", usgs(["2026-09-01", "2026-09-02"], [5.0, 6.0]), "20260901T000000Z"
    )
    write_raw(
        "usgs",
        usgs(["2026-09-02", "2026-09-03"], [6.5, 7.0], "Approved"),
        "20260910T000000Z",
    )
    raw = features.load_raw_usgs(tmp_data_dir / "raw/usgs")
    flow, _ = features.validate_flow(raw)
    assert list(flow["date"]) == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert list(flow["flow_cfs"]) == [5.0, 6.5, 7.0]  # the revised 09-02 wins
    assert flow.loc[1, "approval_status"] == "Approved"


def test_weather_newest_file_wins_and_forecast_wins_a_tie(write_raw, tmp_data_dir):
    days = ["2026-09-20", "2026-09-21", "2026-09-22"]
    write_raw(
        "weather_fc", weather(days, [9.0, 9.0, 9.0], "forecast"), "20260920T000000Z"
    )
    write_raw(
        "weather_hist",
        weather(days[:2], [1.0, 1.0], "historical_forecast"),
        "20260922T000000Z",
    )
    write_raw(
        "weather_fc", weather(days[1:], [4.0, 4.0], "forecast"), "20260922T000000Z"
    )
    merged = features.load_raw_weather(
        tmp_data_dir / "raw/weather_hist", tmp_data_dir / "raw/weather_fc"
    )
    # 09-20: newer hist beats the older forecast; 09-21: tie -> forecast (D28).
    assert list(merged["precip_mm"]) == [1.0, 4.0, 4.0]


def test_all_empty_weather_row_never_wins(write_raw, tmp_data_dir):
    write_raw(
        "weather_hist",
        weather(["2026-09-20"], [2.5], "historical_forecast"),
        "20260920T000000Z",
    )
    empty = weather(["2026-09-20"], [None], "forecast")
    empty[["tmax_c", "tmin_c"]] = None
    write_raw("weather_fc", empty, "20260921T000000Z")
    merged = features.load_raw_weather(
        tmp_data_dir / "raw/weather_hist", tmp_data_dir / "raw/weather_fc"
    )
    assert list(merged["precip_mm"]) == [2.5]


def test_leads_newest_file_wins_per_date_and_lead(write_raw, tmp_data_dir):
    def leads(value):
        return pd.DataFrame(
            {"date": ["2026-09-18"] * 2, "lead_days": [1, 2], "precip_mm": value}
        )

    write_raw("weather_leads", leads([0.7, 4.1]), "20260920T000000Z")
    write_raw("weather_leads", leads([0.9, 4.0]), "20260921T000000Z")
    out = features.load_raw_leads(tmp_data_dir / "raw/weather_leads")
    assert list(out["precip_mm"]) == [0.9, 4.0]


# --- AC-2.2 -------------------------------------------------------------------------


def test_impossible_values_dropped(write_raw, tmp_data_dir, run_features, capsys):
    dates = [f"2026-09-{d:02d}" for d in range(1, 9)]
    write_raw(
        "usgs",
        usgs(dates, ["5.0", "-5", "-999999", "abc", "", "0.0", "8180", "inf"]),
    )
    raw = features.load_raw_usgs(tmp_data_dir / "raw/usgs")
    flow, counts = features.validate_flow(raw)
    assert counts == {"negative": 1, "sentinel": 1, "non-numeric": 2, "missing": 1}
    assert list(flow["flow_cfs"]) == [5.0, 0.0, 8180.0]  # zero and extremes kept

    code, _ = run_features()
    assert code == 0
    assert (
        "dropped impossible: 1 negative, 1 sentinel, 2 non-numeric, 1 missing"
        in capsys.readouterr().err
    )


# --- AC-1.12 (the Features half) ------------------------------------------------------


def test_grid_cell_change_warns(write_raw, run_features, capsys):
    write_raw("usgs", usgs(["2026-09-01", "2026-09-02", "2026-09-03"], [1, 2, 3]))
    moved = weather(["2026-09-02"], [0.0], "forecast")
    moved["grid_lat"] = 36.1
    write_raw("weather_hist", weather(["2026-09-01"], [0.0], "historical_forecast"))
    write_raw("weather_fc", moved)
    run_features()
    assert "WARNING features weather grid cell differs" in capsys.readouterr().err


def test_same_grid_cell_is_quiet(write_raw, run_features, capsys):
    write_raw("usgs", usgs(["2026-09-01", "2026-09-02", "2026-09-03"], [1, 2, 3]))
    write_raw("weather_hist", weather(["2026-09-01"], [0.0], "historical_forecast"))
    write_raw("weather_fc", weather(["2026-09-02"], [0.0], "forecast"))
    run_features()
    assert "grid cell differs" not in capsys.readouterr().err
