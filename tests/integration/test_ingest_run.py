"""ingest.main() end to end: mocked HTTP (`responses`), a temporary DATA_DIR."""

import re
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest
import responses

from streamforecast import ingest

pytestmark = pytest.mark.integration

MRMS_RE = re.compile(r"https://mesonet\.agron\.iastate\.edu/iemre/multiday/.*")
KINDS = ["usgs", "weather_hist", "weather_leads", "weather_fc", "mrms"]


@pytest.fixture
def env(tmp_data_dir, monkeypatch):
    for name in ("TIMEZONE", "HISTORY_START", "LEADS_START", "USGS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    monkeypatch.setenv("INGEST_SOURCE", "live")
    monkeypatch.setenv("MRMS_ENABLED", "1")
    monkeypatch.setattr(ingest, "_sleep", lambda s: None)
    return monkeypatch


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


def register_defaults(rsps, load_fixture, skip=()):
    """One recorded response per endpoint (registered after any per-test overrides)."""
    defaults = {
        "usgs": (ingest.USGS_URL, "usgs_daily_recent.json"),
        "weather_hist": (ingest.HIST_URL, "openmeteo_hist_forecast.json"),
        "weather_leads": (ingest.LEADS_URL, "openmeteo_previous_runs.json"),
        "weather_fc": (ingest.FORECAST_URL, "openmeteo_forecast_past7.json"),
        "mrms": (MRMS_RE, "iem_mrms_recent.json"),
    }
    for kind, (url, name) in defaults.items():
        if kind not in skip:
            rsps.get(url, json=load_fixture(name))


def files(data_dir, kind):
    return sorted((data_dir / "raw" / kind).glob(f"{kind}_*.csv"))


def calls_to(rsps, prefix):
    return [c.request.url for c in rsps.calls if c.request.url.startswith(prefix)]


def query(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def mrms_ranges(rsps):
    return [
        u.split("/multiday/")[1].rsplit("/", 3)[0]
        for u in calls_to(rsps, "https://mesonet")
    ]


def seed(data_dir, kind, frame):
    directory = data_dir / "raw" / kind
    directory.mkdir(parents=True, exist_ok=True)
    frame.to_csv(directory / f"{kind}_20260101T000000Z.csv", index=False)


# --- AC-1.1 --------------------------------------------------------------------------


def test_backfill_empty_dir(env, rsps, load_fixture, tmp_data_dir, capsys):
    register_defaults(rsps, load_fixture)
    assert ingest.main() == 0
    for kind in KINDS:
        assert len(files(tmp_data_dir, kind)) == 1, kind

    (usgs,) = calls_to(rsps, ingest.USGS_URL)
    assert query(usgs)["datetime"] == "2018-01-01/2026-09-23"
    assert query(usgs)["statistic_id"] == "00003"
    (hist,) = calls_to(rsps, ingest.HIST_URL)
    assert (query(hist)["start_date"], query(hist)["end_date"]) == (
        "2018-01-01",
        "2026-09-23",
    )
    (leads,) = calls_to(rsps, ingest.LEADS_URL)
    assert (query(leads)["start_date"], query(leads)["end_date"]) == (
        "2024-01-25",
        "2026-09-23",
    )
    (fc,) = calls_to(rsps, ingest.FORECAST_URL)
    assert (query(fc)["forecast_days"], query(fc)["past_days"]) == ("3", "7")
    assert mrms_ranges(rsps) == [f"{y}-01-01/{y}-12-31" for y in range(2018, 2026)] + [
        "2026-01-01/2026-09-23"
    ]
    err = capsys.readouterr().err
    assert "usgs: requesting 2018-01-01..2026-09-23" in err
    assert "weather_leads: requesting 2024-01-25..2026-09-23" in err


def test_backfill_without_mrms_writes_four_files(env, rsps, load_fixture, tmp_data_dir):
    env.setenv("MRMS_ENABLED", "0")
    register_defaults(rsps, load_fixture, skip=("mrms",))
    assert ingest.main() == 0
    assert [len(files(tmp_data_dir, k)) for k in KINDS] == [1, 1, 1, 1, 0]
    assert mrms_ranges(rsps) == []


# --- AC-1.2 --------------------------------------------------------------------------


def test_incremental_second_run(env, rsps, load_fixture, tmp_data_dir, capsys):
    seed(
        tmp_data_dir,
        "usgs",
        pd.DataFrame({"date": ["2026-09-19", "2026-09-20"], "flow_cfs": [8.0, 7.0]}),
    )
    seed(
        tmp_data_dir,
        "weather_hist",
        pd.DataFrame({"date": ["2026-09-22", "2026-09-23"], "precip_mm": [0.0, 3.6]}),
    )
    seed(
        tmp_data_dir,
        "weather_leads",
        pd.DataFrame({"date": ["2026-09-23"] * 2, "precip_mm": [0.0, 0.1]}),
    )
    seed(
        tmp_data_dir,
        "mrms",
        pd.DataFrame({"date": ["2025-12-31", "2026-09-20"], "precip_mm": [0.0, 0.0]}),
    )
    register_defaults(rsps, load_fixture)
    assert ingest.main() == 0

    (usgs,) = calls_to(rsps, ingest.USGS_URL)
    assert query(usgs)["datetime"] == "2026-08-21/2026-09-23"  # 30-day lookback
    assert calls_to(rsps, ingest.HIST_URL) == []
    assert calls_to(rsps, ingest.LEADS_URL) == []
    assert mrms_ranges(rsps) == ["2026-09-13/2026-09-23"]  # current year, max - 7
    assert len(files(tmp_data_dir, "weather_hist")) == 1  # only the seeded file
    assert len(files(tmp_data_dir, "weather_leads")) == 1
    assert len(files(tmp_data_dir, "usgs")) == 2
    assert len(files(tmp_data_dir, "weather_fc")) == 1
    err = capsys.readouterr().err
    assert "usgs: requesting 2026-08-21..2026-09-23" in err
    assert "weather_hist: up to date, no request" in err
    assert "weather_leads: up to date, no request" in err


def test_incremental_hist_starts_after_latest(env, rsps, load_fixture, tmp_data_dir):
    seed(
        tmp_data_dir,
        "weather_hist",
        pd.DataFrame({"date": ["2026-09-19", "2026-09-20"], "precip_mm": [0.0, 0.0]}),
    )
    register_defaults(rsps, load_fixture)
    assert ingest.main() == 0
    (hist,) = calls_to(rsps, ingest.HIST_URL)
    assert (query(hist)["start_date"], query(hist)["end_date"]) == (
        "2026-09-21",
        "2026-09-23",
    )
    new = [f for f in files(tmp_data_dir, "weather_hist") if "20260101" not in f.name]
    written = pd.read_csv(new[0], dtype={"date": str})
    assert list(written["date"]) == ["2026-09-21", "2026-09-22", "2026-09-23"]


# --- AC-1.6 --------------------------------------------------------------------------


def test_rate_limit_final_failure(env, rsps, load_fixture, tmp_data_dir, capsys):
    rsps.get(ingest.USGS_URL, status=429)
    register_defaults(rsps, load_fixture, skip=("usgs",))
    assert ingest.main() == 1
    assert len(calls_to(rsps, ingest.USGS_URL)) == 3
    assert files(tmp_data_dir, "usgs") == []
    for kind in KINDS[1:]:
        assert len(files(tmp_data_dir, kind)) == 1, kind
    err = capsys.readouterr().err
    assert "ERROR ingest usgs: HTTP 429 after 3 attempts; no file written" in err


def test_openmeteo_error_body_fails_that_source(
    env, rsps, load_fixture, tmp_data_dir, capsys
):
    rsps.get(ingest.HIST_URL, status=400, json=load_fixture("openmeteo_error.json"))
    register_defaults(rsps, load_fixture, skip=("weather_hist",))
    assert ingest.main() == 1
    assert files(tmp_data_dir, "weather_hist") == []
    assert len(files(tmp_data_dir, "weather_fc")) == 1
    err = capsys.readouterr().err
    assert re.search(
        r"ERROR ingest weather_hist: HTTP 400: .*out of allowed range", err
    )


def test_malformed_json_fails_that_source(
    env, rsps, load_fixture, tmp_data_dir, capsys
):
    rsps.get(ingest.FORECAST_URL, body="{not json")
    register_defaults(rsps, load_fixture, skip=("weather_fc",))
    assert ingest.main() == 1
    assert files(tmp_data_dir, "weather_fc") == []
    assert "ERROR ingest weather_fc: malformed JSON" in capsys.readouterr().err


def test_timezone_mismatch_writes_no_file(
    env, rsps, load_fixture, tmp_data_dir, capsys
):
    gmt = load_fixture("openmeteo_hist_forecast.json")
    gmt["timezone"] = "GMT"
    rsps.get(ingest.HIST_URL, json=gmt)
    register_defaults(rsps, load_fixture, skip=("weather_hist",))
    assert ingest.main() == 1
    assert files(tmp_data_dir, "weather_hist") == []
    assert (
        "ERROR ingest weather_hist: timezone mismatch: requested America/New_York, "
        "got GMT" in capsys.readouterr().err
    )


def test_empty_usgs_warns(env, rsps, load_fixture, tmp_data_dir, capsys):
    rsps.get(ingest.USGS_URL, json=load_fixture("usgs_empty.json"))
    register_defaults(rsps, load_fixture, skip=("usgs",))
    assert ingest.main() == 0
    assert files(tmp_data_dir, "usgs") == []
    assert "WARNING ingest usgs: empty response" in capsys.readouterr().err


# --- AC-1.11 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mrms_response",
    [
        {"status": 500},
        {"json_fixture": "iem_mrms_all_null.json"},
    ],
    ids=["http-500", "all-null"],
)
def test_mrms_failure_does_not_fail_ingest(
    env, rsps, load_fixture, tmp_data_dir, capsys, mrms_response
):
    env.setenv("HISTORY_START", "2026-09-01")
    if "status" in mrms_response:
        rsps.get(MRMS_RE, status=mrms_response["status"])
    else:
        rsps.get(MRMS_RE, json=load_fixture(mrms_response["json_fixture"]))
    register_defaults(rsps, load_fixture, skip=("mrms",))
    assert ingest.main() == 0
    assert files(tmp_data_dir, "mrms") == []
    for kind in KINDS[:4]:
        assert len(files(tmp_data_dir, kind)) == 1, kind
    assert re.search(r"ERROR ingest mrms: .*no file written", capsys.readouterr().err)


def test_mrms_failure_leaves_exit_to_other_sources(env, rsps, load_fixture):
    env.setenv("HISTORY_START", "2026-09-01")
    rsps.get(MRMS_RE, status=500)
    rsps.get(ingest.USGS_URL, status=500)
    register_defaults(rsps, load_fixture, skip=("mrms", "usgs"))
    assert ingest.main() == 1


# --- AC-1.12 pagination ------------------------------------------------------------


def test_usgs_follows_next_links(env, rsps, load_fixture, tmp_data_dir):
    full = load_fixture("usgs_daily_recent.json")
    next_url = "https://api.waterdata.usgs.gov/ogcapi/v1/collections/daily/items"
    page1 = {
        **full,
        "features": full["features"][:6],
        "numberReturned": 6,
        "links": [{"rel": "next", "href": f"{next_url}?cursor=abc&f=json"}],
    }
    page2 = {**full, "features": full["features"][6:], "numberReturned": 4, "links": []}
    rsps.get(ingest.USGS_URL, json=page1)
    rsps.get(next_url, json=page2)
    register_defaults(rsps, load_fixture, skip=("usgs",))
    assert ingest.main() == 0
    assert len(calls_to(rsps, next_url)) == 1
    assert "cursor=abc" in calls_to(rsps, next_url)[0]
    (path,) = files(tmp_data_dir, "usgs")
    written = pd.read_csv(path, dtype={"date": str})
    assert list(written["date"]) == [f"2026-09-{d}" for d in range(14, 24)]


def test_usgs_api_key_sent_as_header(env, rsps, load_fixture):
    env.setenv("USGS_API_KEY", "secret")
    register_defaults(rsps, load_fixture)
    ingest.main()
    usgs = [c for c in rsps.calls if c.request.url.startswith(ingest.USGS_URL)][0]
    assert usgs.request.headers["X-Api-Key"] == "secret"
    assert "secret" not in usgs.request.url


# --- AC-1.7 fixture mode -----------------------------------------------------------


def test_fixture_mode(env, rsps, tmp_data_dir):
    env.setenv("INGEST_SOURCE", "fixtures")
    assert ingest.main() == 0
    assert len(rsps.calls) == 0  # nothing reached the (mocked) HTTP layer
    for kind in KINDS:
        assert len(files(tmp_data_dir, kind)) == 1, kind
    for kind in KINDS[1:]:  # USGS rows carry last_modified instead (AC-1.3)
        (path,) = files(tmp_data_dir, kind)
        assert set(pd.read_csv(path)["fetched_at_utc"]) == {"2026-09-24T16:00:00Z"}
    fc = pd.read_csv(files(tmp_data_dir, "weather_fc")[0], dtype={"date": str})
    assert list(fc["date"])[-3:] == ["2026-09-24", "2026-09-25", "2026-09-26"]


def test_fixture_mode_requires_as_of_date(env, tmp_data_dir, capsys):
    env.setenv("INGEST_SOURCE", "fixtures")
    env.delenv("AS_OF_DATE")
    assert ingest.main() == 1
    assert "INGEST_SOURCE=fixtures requires AS_OF_DATE" in capsys.readouterr().err
    assert not (tmp_data_dir / "raw").exists()
