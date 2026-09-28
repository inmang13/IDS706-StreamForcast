import math
import re
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import requests
import responses

import streamforecast
from streamforecast import config, ingest, logs

pytestmark = pytest.mark.unit

TZ = "America/New_York"


@pytest.fixture
def run(tmp_data_dir, monkeypatch):
    """A Run for 2026-09-24 using live HTTP (mocked with `responses` per test)."""
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    monkeypatch.setenv("INGEST_SOURCE", "live")
    monkeypatch.delenv("TIMEZONE", raising=False)
    settings = config.load()
    log = logs.get_logger("ingest", settings)
    return ingest.Run(
        settings=settings,
        log=log,
        get_json=ingest.live_getter(settings, log),
        today=date(2026, 9, 24),
        fetched_at_utc="2026-09-24T16:00:00Z",
        stamp="20260924T160000Z",
    )


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(ingest, "_sleep", calls.append)
    return calls


def read_only_csv(directory: Path, pattern: str) -> pd.DataFrame:
    files = list(directory.glob(pattern))
    assert len(files) == 1, files
    return pd.read_csv(files[0], dtype={"date": str, "qualifier": str})


# --- USGS parsing (AC-1.3, AC-1.12) ------------------------------------------


def test_parse_usgs_sorts_and_types(load_fixture):
    features = load_fixture("usgs_daily_recent.json")["features"]
    assert [f["properties"]["time"] for f in features] != sorted(
        f["properties"]["time"] for f in features
    ), "fixture should be unordered"
    rows, dropped = ingest.parse_usgs(features, "02085000")
    assert dropped == 0
    assert [r["date"] for r in rows] == [f"2026-09-{d}" for d in range(14, 24)]
    assert all(isinstance(r["flow_cfs"], float) for r in rows)
    assert {r["approval_status"] for r in rows} == {"Provisional"}
    assert rows[-1]["flow_cfs"] == 7.22


def test_parse_usgs_qualifier_join(load_fixture):
    rows, _ = ingest.parse_usgs(
        load_fixture("usgs_daily_chantal.json")["features"], "02085000"
    )
    by_date = {r["date"]: r["qualifier"] for r in rows}
    assert by_date["2025-07-05"] == ""
    assert by_date["2025-07-06"] == "ESTIMATED"
    feature = {
        "properties": {
            "time": "2025-01-01",
            "value": "1.5",
            "unit_of_measure": "ft^3/s",
            "statistic_id": "00003",
            "monitoring_location_id": "USGS-02085000",
            "qualifier": ["ESTIMATED", "ICE"],
        }
    }
    assert ingest.parse_usgs([feature], "02085000")[0][0]["qualifier"] == (
        "ESTIMATED;ICE"
    )


def test_parse_usgs_drops_wrong_unit_statistic_site(load_fixture):
    features = load_fixture("usgs_daily_recent.json")["features"]
    features[0]["properties"]["unit_of_measure"] = "m^3/s"
    features[1]["properties"]["statistic_id"] = "00001"
    features[2]["properties"]["monitoring_location_id"] = "USGS-99999999"
    rows, dropped = ingest.parse_usgs(features, "02085000")
    assert dropped == 3
    assert len(rows) == 7


def test_parse_usgs_keeps_non_numeric_value_as_nan(load_fixture):
    features = load_fixture("usgs_daily_recent.json")["features"]
    features[0]["properties"]["value"] = "abc"
    rows, _ = ingest.parse_usgs(features, "02085000")
    assert len(rows) == 10
    assert sum(math.isnan(r["flow_cfs"]) for r in rows) == 1


# --- Open-Meteo validation (AC-1.10, AC-1.12) ---------------------------------


def test_timezone_mismatch_rejected(load_fixture):
    data = load_fixture("openmeteo_hist_chantal_gmt.json")
    with pytest.raises(
        ingest.IngestError,
        match="timezone mismatch: requested America/New_York, got GMT",
    ):
        ingest.check_openmeteo(data, "daily", ingest.DAILY_VARS, TZ)


def test_validate_units_and_lengths(load_fixture):
    good = load_fixture("openmeteo_hist_forecast.json")
    ingest.check_openmeteo(good, "daily", ingest.DAILY_VARS, TZ)

    bad_unit = load_fixture("openmeteo_hist_forecast.json")
    bad_unit["daily_units"]["precipitation_sum"] = "inch"
    with pytest.raises(ingest.IngestError, match="unit mismatch"):
        ingest.check_openmeteo(bad_unit, "daily", ingest.DAILY_VARS, TZ)

    bad_temp = load_fixture("openmeteo_hist_forecast.json")
    bad_temp["daily_units"]["temperature_2m_max"] = "°F"
    with pytest.raises(ingest.IngestError, match="temperature_2m_max"):
        ingest.check_openmeteo(bad_temp, "daily", ingest.DAILY_VARS, TZ)

    short = load_fixture("openmeteo_hist_forecast.json")
    short["daily"]["temperature_2m_min"].pop()
    with pytest.raises(ingest.IngestError, match="9 values for 10 times"):
        ingest.check_openmeteo(short, "daily", ingest.DAILY_VARS, TZ)

    hourly = load_fixture("openmeteo_previous_runs.json")
    hourly["hourly_units"]["precipitation_previous_day2"] = "inch"
    with pytest.raises(ingest.IngestError, match="precipitation_previous_day2"):
        ingest.check_openmeteo(hourly, "hourly", ingest.HOURLY_VARS, TZ)


def test_date_coverage_gap_warns(run, load_fixture, tmp_data_dir, capsys):
    assert ingest.missing_dates(
        ["2025-07-14", "2025-07-16"], date(2025, 7, 14), date(2025, 7, 16)
    ) == ["2025-07-15"]
    run.settings = config.Settings(
        data_dir=str(tmp_data_dir), history_start=date(2025, 7, 8)
    )
    run.today = date(2025, 7, 19)
    with responses.RequestsMock() as rsps:
        rsps.get(ingest.USGS_URL, json=load_fixture("usgs_daily_estimated_gap.json"))
        ingest.ingest_usgs(run)
    err = capsys.readouterr().err
    assert "WARNING ingest usgs: 1 of 11 requested dates missing" in err
    assert "2025-07-15" in err
    df = read_only_csv(tmp_data_dir / "raw/usgs", "usgs_*.csv")
    assert "2025-07-15" not in set(df["date"])
    assert df["date"].iloc[-1] == "2025-07-18"


# --- Weather parsing (AC-1.4, AC-1.10) ------------------------------------------


def test_parse_weather_columns_and_timezone_param(run, load_fixture, tmp_data_dir):
    with responses.RequestsMock() as rsps:
        rsps.get(ingest.HIST_URL, json=load_fixture("openmeteo_hist_forecast.json"))
        ingest.ingest_weather_hist(run)
        assert "timezone=America%2FNew_York" in rsps.calls[0].request.url
    df = read_only_csv(tmp_data_dir / "raw/weather_hist", "weather_hist_*.csv")
    assert list(df.columns) == [
        "date",
        "precip_mm",
        "tmax_c",
        "tmin_c",
        "source",
        "fetched_at_utc",
        "grid_lat",
        "grid_lon",
        "elevation",
    ]
    assert set(df["source"]) == {"historical_forecast"}
    assert df.loc[df["date"] == "2026-09-18", "precip_mm"].item() == 51.5
    assert (df["grid_lat"].iloc[0], df["grid_lon"].iloc[0]) == (36.072254, -79.1094)


def test_timezone_param_on_every_openmeteo_request(run, load_fixture):
    run.today = date(2026, 9, 24)
    with responses.RequestsMock() as rsps:
        rsps.get(ingest.HIST_URL, json=load_fixture("openmeteo_hist_forecast.json"))
        rsps.get(ingest.LEADS_URL, json=load_fixture("openmeteo_previous_runs.json"))
        rsps.get(
            ingest.FORECAST_URL, json=load_fixture("openmeteo_forecast_past7.json")
        )
        ingest.ingest_weather_hist(run)
        ingest.ingest_weather_leads(run)
        ingest.ingest_weather_fc(run)
        urls = [c.request.url for c in rsps.calls]
    assert len(urls) == 3
    assert all("timezone=America%2FNew_York" in u for u in urls)


def test_short_forecast_written_with_warning(run, load_fixture, tmp_data_dir, capsys):
    with responses.RequestsMock() as rsps:
        rsps.get(
            ingest.FORECAST_URL, json=load_fixture("openmeteo_forecast_short.json")
        )
        ingest.ingest_weather_fc(run)
    assert "WARNING ingest weather_fc: forecast returned 2 of 3 days" in (
        capsys.readouterr().err
    )
    df = read_only_csv(tmp_data_dir / "raw/weather_fc", "weather_fc_*.csv")
    assert len(df) == 9
    assert set(df["source"]) == {"forecast"}


def test_forecast_with_null_value_warns(run, load_fixture, capsys):
    data = load_fixture("openmeteo_forecast_past7.json")
    data["daily"]["precipitation_sum"][-1] = None
    with responses.RequestsMock() as rsps:
        rsps.get(ingest.FORECAST_URL, json=data)
        ingest.ingest_weather_fc(run)
    assert "forecast returned 2 of 3 days" in capsys.readouterr().err


def test_dates_written_verbatim(run, load_fixture, tmp_data_dir):
    usgs = load_fixture("usgs_daily_recent.json")
    hist = load_fixture("openmeteo_hist_forecast.json")
    with responses.RequestsMock() as rsps:
        rsps.get(ingest.USGS_URL, json=usgs)
        rsps.get(ingest.HIST_URL, json=hist)
        ingest.ingest_usgs(run)
        ingest.ingest_weather_hist(run)
    usgs_csv = (next((tmp_data_dir / "raw/usgs").glob("*.csv"))).read_text()
    hist_csv = (next((tmp_data_dir / "raw/weather_hist").glob("*.csv"))).read_text()
    for f in usgs["features"]:
        assert f"\n{f['properties']['time']}," in usgs_csv
    for day in hist["daily"]["time"]:
        assert f"\n{day}," in hist_csv


def test_no_timezone_conversion_in_ingest_or_features():
    package = Path(streamforecast.__file__).parent
    for name in ("ingest.py", "features.py"):
        path = package / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            assert "tz_localize" not in text and "tz_convert" not in text, name


# --- Previous-runs leads (AC-1.9) -------------------------------------------------


def hourly_response(times, day1, day2=None):
    return {
        "latitude": 36.072254,
        "longitude": -79.1094,
        "elevation": 163.0,
        "timezone": TZ,
        "hourly_units": {k: "mm" for k in ["time", *ingest.HOURLY_VARS]},
        "hourly": {
            "time": times,
            "precipitation": [0.0] * len(times),
            "precipitation_previous_day1": day1,
            "precipitation_previous_day2": day2 or [0.0] * len(times),
        },
    }


def test_leads_hourly_to_daily():
    times = [f"2026-09-{d}T{h:02d}:00" for d in (18, 19) for h in range(24)]
    day1 = [0.0] * 48
    day1[21:24] = [1.0, 2.0, 3.0]  # 21:00-23:00 local on 09-18 (01-03 UTC on 09-19)
    day1[30] = None  # a null hour on 09-19
    rows, incomplete = ingest.parse_leads(hourly_response(times, day1), "t")
    by_key = {(r["date"], r["lead_days"]): r for r in rows}
    assert by_key[("2026-09-18", 1)]["precip_mm"] == 6.0
    assert by_key[("2026-09-18", 1)]["n_hours"] == 24
    assert math.isnan(by_key[("2026-09-19", 1)]["precip_mm"])
    assert by_key[("2026-09-19", 1)]["n_hours"] == 23
    assert by_key[("2026-09-19", 2)]["precip_mm"] == 0.0
    assert incomplete == 1


def test_leads_partial_date_is_nan():
    times = [f"2026-09-18T{h:02d}:00" for h in range(23)]  # a date cut short
    rows, incomplete = ingest.parse_leads(hourly_response(times, [0.5] * 23), "t")
    assert all(math.isnan(r["precip_mm"]) for r in rows)
    assert incomplete == 2


def test_leads_real_values_and_null_hour(load_fixture):
    rows, _ = ingest.parse_leads(load_fixture("openmeteo_previous_runs.json"), "t")
    by_key = {(r["date"], r["lead_days"]): r["precip_mm"] for r in rows}
    assert by_key[("2026-09-18", 1)] == 0.7
    assert by_key[("2026-09-18", 2)] == 4.1
    assert by_key[("2026-09-21", 2)] == 24.5
    rows, incomplete = ingest.parse_leads(
        load_fixture("openmeteo_previous_runs_null_hour.json"), "t"
    )
    by_key = {(r["date"], r["lead_days"]): r["precip_mm"] for r in rows}
    assert math.isnan(by_key[("2026-09-20", 1)])
    assert not math.isnan(by_key[("2026-09-20", 2)])
    assert incomplete == 1


@pytest.mark.parametrize(
    "name, dst_day",
    [
        ("openmeteo_previous_runs_dst_fall.json", "2025-11-02"),
        ("openmeteo_previous_runs_dst_spring.json", "2025-03-09"),
    ],
)
def test_leads_dst_day_has_24_open_meteo_hours(load_fixture, name, dst_day):
    """Open-Meteo uses one fixed offset per response: DST days still have 24 hours."""
    rows, incomplete = ingest.parse_leads(load_fixture(name), "t")
    dst = [r for r in rows if r["date"] == dst_day]
    assert [r["n_hours"] for r in dst] == [24, 24]
    assert not any(math.isnan(r["precip_mm"]) for r in dst)
    assert incomplete == 0


# --- MRMS (AC-1.11) -------------------------------------------------------------------


def test_mrms_parse_inches_to_mm(load_fixture):
    rows = ingest.parse_mrms(load_fixture("iem_mrms_chantal.json"), "t")
    by_date = {r["date"]: r for r in rows}
    assert by_date["2025-07-06"]["precip_mm"] == 178.054
    assert by_date["2025-07-05"]["precip_mm"] == 0.0
    assert by_date["2025-07-06"]["source"] == "iem_mrms"


def test_mrms_yearly_chunks(run, load_fixture):
    assert ingest.mrms_chunks(date(2024, 6, 1), date(2025, 3, 1)) == [
        (date(2024, 6, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 3, 1)),
    ]
    run.settings = config.Settings(
        data_dir=run.settings.data_dir, history_start=date(2024, 12, 1)
    )
    run.today = date(2025, 7, 16)
    with responses.RequestsMock() as rsps:
        rsps.get(
            re.compile(r"https://mesonet\.agron\.iastate\.edu/.*"),
            json=load_fixture("iem_mrms_chantal.json"),
        )
        ingest.ingest_mrms(run)
        urls = [c.request.url for c in rsps.calls]
    assert [u.split("/multiday/")[1].split("/36")[0] for u in urls] == [
        "2024-12-01/2024-12-31",
        "2025-01-01/2025-07-15",
    ]


def test_mrms_all_null_chunk_is_a_failure(run, load_fixture):
    run.settings = config.Settings(
        data_dir=run.settings.data_dir, history_start=date(2026, 9, 1)
    )
    with responses.RequestsMock() as rsps:
        rsps.get(
            re.compile(r"https://mesonet\.agron\.iastate\.edu/.*"),
            json=load_fixture("iem_mrms_all_null.json"),
        )
        with pytest.raises(ingest.IngestError, match="every mrms_precip_in is null"):
            ingest.ingest_mrms(run)


# --- HTTP layer (AC-1.5, AC-1.6) ----------------------------------------------------

URL = "https://example.test/api"


def get(run):
    return ingest.http_get_json(URL, None, None, run.settings, run.log, "test")


def test_retry_then_success(run, sleeps):
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=429)
        rsps.get(URL, status=429)
        rsps.get(URL, json={"ok": 1})
        assert get(run) == {"ok": 1}
        assert len(rsps.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_retry_on_5xx_and_connection_error(run, sleeps):
    with responses.RequestsMock() as rsps:
        rsps.get(URL, body=requests.ConnectionError("reset"))
        rsps.get(URL, status=503)
        rsps.get(URL, json={"ok": 1})
        assert get(run) == {"ok": 1}
    assert sleeps == [1.0, 2.0]


def test_retry_after_honoured_and_capped(run, sleeps):
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=429, headers={"Retry-After": "5"})
        rsps.get(URL, status=429, headers={"Retry-After": "600"})
        rsps.get(URL, json={"ok": 1})
        get(run)
    assert sleeps == [5.0, 60.0]


def test_final_failure_raises_after_all_attempts(run, sleeps):
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=429)
        with pytest.raises(ingest.IngestError, match="HTTP 429 after 3 attempts"):
            get(run)
        assert len(rsps.calls) == 3


def test_openmeteo_error_body_is_not_retried(run, sleeps, load_fixture):
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=400, json=load_fixture("openmeteo_error.json"))
        with pytest.raises(ingest.IngestError, match="out of allowed range"):
            get(run)
        assert len(rsps.calls) == 1
    assert sleeps == []


def test_error_body_with_200_status_is_a_failure(run, sleeps):
    with responses.RequestsMock() as rsps:
        rsps.get(URL, json={"error": True, "reason": "Invalid value"})
        with pytest.raises(ingest.IngestError, match="Invalid value"):
            get(run)


def test_malformed_json_is_a_failure(run, sleeps):
    with responses.RequestsMock() as rsps:
        rsps.get(URL, body="<html>oops</html>")
        with pytest.raises(ingest.IngestError, match="malformed JSON"):
            get(run)


# --- Atomic writes (AC-1.8) ----------------------------------------------------------


def test_atomic_write_no_partial(run, tmp_data_dir, monkeypatch):
    def half_write(self, path, **kwargs):
        Path(path).write_text("date,flow_cfs\n2026-09-")
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_csv", half_write)
    rows = [{"date": "2026-09-23", "flow_cfs": 7.22}]
    with pytest.raises(OSError):
        ingest.write_csv(run, "usgs", rows, ingest.USGS_COLUMNS)
    assert list((tmp_data_dir / "raw/usgs").iterdir()) == []
