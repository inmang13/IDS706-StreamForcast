"""Stage 1: fetch raw data and write normalized, timestamped CSVs under DATA_DIR/raw.

Sources, in run order (``weather_fc`` is written after ``weather_hist``, D28):

- ``usgs``: USGS daily mean discharge (new Water Data OGC API).
- ``weather_hist``: Open-Meteo historical-forecast daily weather (training).
- ``weather_leads``: Open-Meteo previous-runs hourly rain at lead 1 and 2 days (D20).
- ``weather_fc``: Open-Meteo forecast, 7 past days plus 3 forecast days.
- ``mrms``: NOAA MRMS daily QPE via IEM, verification only (D24). Its failure never
  changes the exit status.

Dates from the APIs are written verbatim as ``YYYY-MM-DD`` strings (RK13). Run with
``python -m streamforecast.ingest``; exit 0 if every non-MRMS source succeeded.
"""

import json
import math
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from logging import Logger
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from streamforecast import config, logs, paths

USGS_URL = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HIST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
LEADS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
MRMS_URL = (
    "https://mesonet.agron.iastate.edu/iemre/multiday/{start}/{end}/{lat}/{lon}/json"
)

DAILY_VARS = {
    "precipitation_sum": "mm",
    "temperature_2m_max": "°C",
    "temperature_2m_min": "°C",
}
HOURLY_VARS = {
    "precipitation": "mm",
    "precipitation_previous_day1": "mm",
    "precipitation_previous_day2": "mm",
}
LEAD_VARS = {1: "precipitation_previous_day1", 2: "precipitation_previous_day2"}
# Open-Meteo applies one fixed UTC offset to a whole response (the zone's offset on
# the day of the request), so every local date has exactly 24 hourly values, DST
# days included (verified 2026-09-24; tests/fixtures/SOURCES.md).
HOURS_PER_DATE = 24
FORECAST_DAYS = 3
PAST_DAYS = 7
MRMS_RECHECK_DAYS = 7
MAX_RETRY_AFTER_S = 60
MISSING_PREVIEW = 10

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
FIXTURE_FILES = {
    "usgs": "usgs_daily_recent.json",
    "weather_hist": "openmeteo_hist_forecast.json",
    "weather_leads": "openmeteo_previous_runs.json",
    "weather_fc": "openmeteo_forecast_past7.json",
    "mrms": "iem_mrms_recent.json",
}

USGS_COLUMNS = ["date", "flow_cfs", "approval_status", "qualifier", "last_modified"]
WEATHER_COLUMNS = [
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
LEADS_COLUMNS = [
    "date",
    "lead_days",
    "precip_mm",
    "n_hours",
    "fetched_at_utc",
    "grid_lat",
    "grid_lon",
    "elevation",
]
MRMS_COLUMNS = ["date", "precip_mm", "source", "fetched_at_utc"]

_sleep = time.sleep


class IngestError(Exception):
    """A source failed; no file is written for it."""


GetJson = Callable[[str, str, dict | None, dict | None], dict]


@dataclass
class Run:
    settings: config.Settings
    log: Logger
    get_json: GetJson
    today: date
    fetched_at_utc: str
    stamp: str

    @property
    def yesterday(self) -> date:
        return self.today - timedelta(days=1)


# --- HTTP -------------------------------------------------------------------


def retry_delay(attempt: int, retry_after: str | None) -> float:
    """Seconds to wait after failed ``attempt`` (1-based); Retry-After capped at 60."""
    if retry_after is not None:
        try:
            return min(max(float(retry_after), 0.0), MAX_RETRY_AFTER_S)
        except ValueError:
            pass
    return float(min(2 ** (attempt - 1), MAX_RETRY_AFTER_S))


def _decode(resp: requests.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise IngestError(f"malformed JSON (HTTP {resp.status_code})") from None
    if isinstance(data, dict) and data.get("error") is True:
        raise IngestError(f"HTTP {resp.status_code}: {data.get('reason', 'no reason')}")
    if resp.status_code >= 400:
        raise IngestError(f"HTTP {resp.status_code}")
    if not isinstance(data, dict):
        raise IngestError("malformed JSON: expected an object")
    return data


def http_get_json(
    url: str,
    params: dict | None,
    headers: dict | None,
    settings: config.Settings,
    log: Logger,
    source: str,
) -> dict:
    """GET JSON with a timeout and retries on 429/5xx/connection errors."""
    attempts = max(settings.http_retries, 1)
    for attempt in range(1, attempts + 1):
        retry_after = None
        try:
            resp = requests.get(
                url, params=params, headers=headers, timeout=settings.http_timeout_s
            )
        except requests.RequestException as exc:
            reason = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code != 429 and resp.status_code < 500:
                return _decode(resp)
            reason = f"HTTP {resp.status_code}"
            retry_after = resp.headers.get("Retry-After")
        if attempt == attempts:
            raise IngestError(f"{reason} after {attempts} attempts")
        delay = retry_delay(attempt, retry_after)
        log.warning(
            "%s: %s (attempt %d of %d); retrying in %gs",
            source,
            reason,
            attempt,
            attempts,
            delay,
        )
        _sleep(delay)
    raise AssertionError("unreachable")


def live_getter(settings: config.Settings, log: Logger) -> GetJson:
    def get(source, url, params, headers):
        return http_get_json(url, params, headers, settings, log, source)

    return get


def fixture_getter() -> GetJson:
    """Serve each source's recorded response for any URL (INGEST_SOURCE=fixtures)."""

    def get(source, url, params, headers):
        path = FIXTURES_DIR / FIXTURE_FILES[source]
        return json.loads(path.read_text(encoding="utf-8"))

    return get


# --- Validation and parsing (pure functions) ---------------------------------


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def date_range(start: date, end: date) -> list[str]:
    return [
        (start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
    ]


def missing_dates(dates, start: date, end: date) -> list[str]:
    """Requested dates absent from ``dates`` (strings)."""
    return sorted(set(date_range(start, end)) - set(dates))


def in_range(rows: list[dict], start: date, end: date) -> list[dict]:
    lo, hi = start.isoformat(), end.isoformat()
    return [r for r in rows if lo <= r["date"] <= hi]


def parse_usgs(features: list[dict], site: str) -> tuple[list[dict], int]:
    """Rows sorted by date, plus the count of rows dropped as not ours."""
    rows = {}
    dropped = 0
    for feature in features:
        p = feature.get("properties", {})
        if (
            p.get("unit_of_measure") != "ft^3/s"
            or p.get("statistic_id") != "00003"
            or p.get("monitoring_location_id") != f"USGS-{site}"
        ):
            dropped += 1
            continue
        qualifier = p.get("qualifier") or []
        rows[p["time"]] = {
            "date": p["time"],
            "flow_cfs": _num(p.get("value")),
            "approval_status": p.get("approval_status") or "",
            "qualifier": ";".join(qualifier) if isinstance(qualifier, list) else "",
            "last_modified": p.get("last_modified") or "",
        }
    return [rows[d] for d in sorted(rows)], dropped


def check_openmeteo(data: dict, block: str, units: dict, tz: str) -> None:
    """Timezone, units, and array lengths of an Open-Meteo response (AC-1.10, 1.12)."""
    if data.get("timezone") != tz:
        raise IngestError(
            f"timezone mismatch: requested {tz}, got {data.get('timezone')}"
        )
    values = data.get(block)
    got_units = data.get(f"{block}_units") or {}
    if not isinstance(values, dict) or "time" not in values:
        raise IngestError(f"response has no {block}.time")
    for var, unit in units.items():
        if var not in values:
            raise IngestError(f"response has no {block}.{var}")
        if got_units.get(var) != unit:
            raise IngestError(
                f"unit mismatch: {block}.{var} is {got_units.get(var)!r}, "
                f"expected {unit!r}"
            )
    n = len(values["time"])
    for var, arr in values.items():
        if len(arr) != n:
            raise IngestError(f"{block}.{var} has {len(arr)} values for {n} times")


def _grid(data: dict) -> dict:
    return {
        "grid_lat": data.get("latitude"),
        "grid_lon": data.get("longitude"),
        "elevation": data.get("elevation"),
    }


def parse_daily_weather(data: dict, source: str, fetched_at_utc: str) -> list[dict]:
    d = data["daily"]
    grid = _grid(data)
    return [
        {
            "date": day,
            "precip_mm": _num(d["precipitation_sum"][i]),
            "tmax_c": _num(d["temperature_2m_max"][i]),
            "tmin_c": _num(d["temperature_2m_min"][i]),
            "source": source,
            "fetched_at_utc": fetched_at_utc,
            **grid,
        }
        for i, day in enumerate(d["time"])
    ]


def parse_leads(data: dict, fetched_at_utc: str) -> tuple[list[dict], int]:
    """Sum hourly lead-1/lead-2 rain per local date (the date part of ``time``).

    A date is complete only with all ``HOURS_PER_DATE`` hours present and non-null;
    otherwise ``precip_mm`` is NaN. Returns the rows and the incomplete-row count.
    """
    h = data["hourly"]
    grid = _grid(data)
    hours_by_date = defaultdict(list)
    for i, stamp in enumerate(h["time"]):
        hours_by_date[stamp[:10]].append(i)
    rows, incomplete = [], 0
    for day in sorted(hours_by_date):
        for lead, var in LEAD_VARS.items():
            values = [h[var][i] for i in hours_by_date[day]]
            present = [v for v in values if v is not None]
            complete = len(values) == HOURS_PER_DATE and len(present) == len(values)
            incomplete += not complete
            rows.append(
                {
                    "date": day,
                    "lead_days": lead,
                    "precip_mm": round(sum(present), 3) if complete else math.nan,
                    "n_hours": len(present),
                    "fetched_at_utc": fetched_at_utc,
                    **grid,
                }
            )
    return rows, incomplete


def parse_mrms(data: dict, fetched_at_utc: str) -> list[dict]:
    """IEM ``mrms_precip_in`` (inches) as ``precip_mm``."""
    records = data.get("data")
    if not isinstance(records, list):
        raise IngestError("response has no data list")
    rows = []
    for rec in records:
        if "date" not in rec or "mrms_precip_in" not in rec:
            raise IngestError("record without date or mrms_precip_in")
        inches = _num(rec["mrms_precip_in"])
        rows.append(
            {
                "date": rec["date"],
                "precip_mm": round(inches * 25.4, 3),
                "source": "iem_mrms",
                "fetched_at_utc": fetched_at_utc,
            }
        )
    return sorted(rows, key=lambda r: r["date"])


def mrms_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """One (start, end) pair per calendar year (RK18a)."""
    return [
        (max(start, date(year, 1, 1)), min(end, date(year, 12, 31)))
        for year in range(start.year, end.year + 1)
    ]


# --- Existing files -----------------------------------------------------------


def latest_date(directory: Path, pattern: str, value_column: str) -> date | None:
    """Latest date with a non-empty ``value_column`` across every existing file."""
    latest = None
    for path in sorted(directory.glob(pattern)):
        df = pd.read_csv(path, usecols=["date", value_column], dtype={"date": str})
        dates = df.loc[df[value_column].notna(), "date"]
        if len(dates):
            d = date.fromisoformat(dates.max())
            latest = d if latest is None or d > latest else latest
    return latest


def write_csv(run: Run, kind: str, rows: list[dict], columns: list[str]) -> Path:
    path = paths.subdir(kind, run.settings) / f"{kind}_{run.stamp}.csv"
    frame = pd.DataFrame(rows, columns=columns)
    return paths.atomic_write(path, lambda tmp: frame.to_csv(tmp, index=False))


def warn_missing(run: Run, source: str, dates, start: date, end: date) -> None:
    missing = missing_dates(dates, start, end)
    if missing:
        preview = ", ".join(missing[:MISSING_PREVIEW])
        more = " ..." if len(missing) > MISSING_PREVIEW else ""
        run.log.warning(
            "%s: %d of %d requested dates missing in %s..%s: %s%s",
            source,
            len(missing),
            (end - start).days + 1,
            start,
            end,
            preview,
            more,
        )


def _wrote(run: Run, source: str, rows: list[dict], path: Path) -> None:
    run.log.info(
        "%s: fetched %d rows %s..%s -> %s",
        source,
        len(rows),
        rows[0]["date"],
        rows[-1]["date"],
        path.as_posix(),
    )


# --- Sources ------------------------------------------------------------------


def ingest_usgs(run: Run) -> None:
    s = run.settings
    latest = latest_date(paths.subdir("usgs", s), "usgs_*.csv", "flow_cfs")
    start = s.history_start
    if latest is not None:
        start = max(start, latest - timedelta(days=s.usgs_lookback_days))
    end = run.yesterday
    run.log.info("usgs: requesting %s..%s", start, end)
    params = {
        "monitoring_location_id": f"USGS-{s.usgs_site}",
        "parameter_code": "00060",
        "statistic_id": "00003",
        "datetime": f"{start}/{end}",
        "limit": 10000,
        "f": "json",
    }
    headers = {"X-Api-Key": s.usgs_api_key} if s.usgs_api_key else None
    features, url, pages = [], USGS_URL, 0
    while url:
        data = run.get_json("usgs", url, params, headers)
        pages += 1
        features.extend(data.get("features") or [])
        url = next(
            (
                link["href"]
                for link in data.get("links", [])
                if link.get("rel") == "next"
            ),
            None,
        )
        params = None  # a next link carries the full query
    if not features:
        run.log.warning("usgs: empty response for %s..%s; no file written", start, end)
        return
    rows, dropped = parse_usgs(features, s.usgs_site)
    if dropped:
        run.log.error(
            "usgs: dropped %d rows with the wrong unit, statistic, or site", dropped
        )
    rows = in_range(rows, start, end)
    if not rows:
        raise IngestError("no valid rows in response")
    warn_missing(run, "usgs", [r["date"] for r in rows], start, end)
    _wrote(run, "usgs", rows, write_csv(run, "usgs", rows, USGS_COLUMNS))
    if pages > 1:
        run.log.info("usgs: followed %d pages", pages)


def ingest_weather_hist(run: Run) -> None:
    s = run.settings
    latest = latest_date(
        paths.subdir("weather_hist", s), "weather_hist_*.csv", "precip_mm"
    )
    start = s.history_start if latest is None else latest + timedelta(days=1)
    end = run.yesterday
    if start > end:
        run.log.info("weather_hist: up to date, no request")
        return
    run.log.info("weather_hist: requesting %s..%s", start, end)
    data = run.get_json(
        "weather_hist",
        HIST_URL,
        {
            "latitude": s.latitude,
            "longitude": s.longitude,
            "daily": ",".join(DAILY_VARS),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": s.timezone,
        },
        None,
    )
    check_openmeteo(data, "daily", DAILY_VARS, s.timezone)
    rows = in_range(
        parse_daily_weather(data, "historical_forecast", run.fetched_at_utc),
        start,
        end,
    )
    if not rows:
        raise IngestError("no rows in requested range")
    nulls = [r["date"] for r in rows if math.isnan(r["precip_mm"])]
    if nulls:
        run.log.warning(
            "weather_hist: %d days with null weather (first %s)", len(nulls), nulls[0]
        )
    warn_missing(run, "weather_hist", [r["date"] for r in rows], start, end)
    path = write_csv(run, "weather_hist", rows, WEATHER_COLUMNS)
    _wrote(run, "weather_hist", rows, path)


def ingest_weather_leads(run: Run) -> None:
    s = run.settings
    latest = latest_date(
        paths.subdir("weather_leads", s), "weather_leads_*.csv", "precip_mm"
    )
    start = s.leads_start if latest is None else latest + timedelta(days=1)
    end = run.yesterday
    if start > end:
        run.log.info("weather_leads: up to date, no request")
        return
    run.log.info("weather_leads: requesting %s..%s", start, end)
    data = run.get_json(
        "weather_leads",
        LEADS_URL,
        {
            "latitude": s.latitude,
            "longitude": s.longitude,
            "hourly": ",".join(HOURLY_VARS),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": s.timezone,
        },
        None,
    )
    check_openmeteo(data, "hourly", HOURLY_VARS, s.timezone)
    rows, _ = parse_leads(data, run.fetched_at_utc)
    rows = in_range(rows, start, end)
    if not rows:
        raise IngestError("no rows in requested range")
    incomplete = sorted({r["date"] for r in rows if math.isnan(r["precip_mm"])})
    if incomplete:
        run.log.warning(
            "weather_leads: %d incomplete days (a missing or null hour) -> NaN: %s",
            len(incomplete),
            ", ".join(incomplete[:MISSING_PREVIEW]),
        )
    warn_missing(run, "weather_leads", {r["date"] for r in rows}, start, end)
    path = write_csv(run, "weather_leads", rows, LEADS_COLUMNS)
    _wrote(run, "weather_leads", rows, path)


def ingest_weather_fc(run: Run) -> None:
    s = run.settings
    run.log.info(
        "weather_fc: requesting %d past days + %d forecast days from %s",
        PAST_DAYS,
        FORECAST_DAYS,
        run.today,
    )
    data = run.get_json(
        "weather_fc",
        FORECAST_URL,
        {
            "latitude": s.latitude,
            "longitude": s.longitude,
            "daily": ",".join(DAILY_VARS),
            "forecast_days": FORECAST_DAYS,
            "past_days": PAST_DAYS,
            "timezone": s.timezone,
        },
        None,
    )
    check_openmeteo(data, "daily", DAILY_VARS, s.timezone)
    rows = parse_daily_weather(data, "forecast", run.fetched_at_utc)
    if not rows:
        raise IngestError("no rows in response")
    today = run.today.isoformat()
    future = [
        r
        for r in rows
        if r["date"] >= today
        and not any(math.isnan(r[k]) for k in ("precip_mm", "tmax_c", "tmin_c"))
    ]
    if len(future) < FORECAST_DAYS:
        run.log.warning("weather_fc: forecast returned %d of 3 days", len(future))
    first = run.today - timedelta(days=PAST_DAYS)
    last = run.today + timedelta(days=FORECAST_DAYS - 1)
    warn_missing(run, "weather_fc", [r["date"] for r in rows], first, last)
    path = write_csv(run, "weather_fc", rows, WEATHER_COLUMNS)
    _wrote(run, "weather_fc", rows, path)


def ingest_mrms(run: Run) -> None:
    s = run.settings
    end = run.yesterday
    latest = latest_date(paths.subdir("mrms", s), "mrms_*.csv", "precip_mm")
    rows = []
    for chunk_start, chunk_end in mrms_chunks(s.history_start, end):
        is_current_year = chunk_end.year == end.year
        if latest is not None:
            if latest >= chunk_end and not is_current_year:
                continue  # a complete past year is never re-requested
            chunk_start = max(chunk_start, latest - timedelta(days=MRMS_RECHECK_DAYS))
        if chunk_start > chunk_end:
            continue
        run.log.info("mrms: requesting %s..%s", chunk_start, chunk_end)
        url = MRMS_URL.format(
            start=chunk_start, end=chunk_end, lat=s.latitude, lon=s.longitude
        )
        chunk = in_range(
            parse_mrms(run.get_json("mrms", url, None, None), run.fetched_at_utc),
            chunk_start,
            chunk_end,
        )
        if not chunk:
            run.log.warning("mrms: no rows for %s..%s", chunk_start, chunk_end)
            continue
        if all(math.isnan(r["precip_mm"]) for r in chunk):
            raise IngestError(
                f"every mrms_precip_in is null for {chunk_start}..{chunk_end}"
            )
        warn_missing(run, "mrms", [r["date"] for r in chunk], chunk_start, chunk_end)
        run.log.info(
            "mrms: fetched %d rows %s..%s",
            len(chunk),
            chunk[0]["date"],
            chunk[-1]["date"],
        )
        rows.extend(chunk)
    if not rows:
        run.log.info("mrms: nothing new, no file written")
        return
    _wrote(run, "mrms", rows, write_csv(run, "mrms", rows, MRMS_COLUMNS))


SOURCES = [
    ("usgs", ingest_usgs),
    ("weather_hist", ingest_weather_hist),
    ("weather_leads", ingest_weather_leads),
    ("weather_fc", ingest_weather_fc),
    ("mrms", ingest_mrms),
]


def fixture_fetched_at(as_of: date, tz: str) -> str:
    """12:00 local on ``as_of``, in UTC, so fixture data look fresh on that date."""
    noon = datetime.combine(as_of, dtime(12), ZoneInfo(tz))
    return noon.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    try:
        settings = config.load()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    log = logs.get_logger("ingest", settings)
    if settings.ingest_source == "fixtures":
        if settings.as_of_date is None:
            log.error("INGEST_SOURCE=fixtures requires AS_OF_DATE (YYYY-MM-DD)")
            return 1
        get_json = fixture_getter()
        fetched_at = fixture_fetched_at(settings.as_of_date, settings.timezone)
        log.info("fixture mode: reading %s", FIXTURES_DIR.as_posix())
    else:
        get_json = live_getter(settings, log)
        fetched_at = paths.utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    run = Run(
        settings=settings,
        log=log,
        get_json=get_json,
        today=paths.local_today(settings),
        fetched_at_utc=fetched_at,
        stamp=paths.timestamp(),
    )
    failed = []
    for name, ingest_source in SOURCES:
        if name == "mrms" and not settings.mrms_enabled:
            log.info("mrms: disabled (MRMS_ENABLED=0)")
            continue
        try:
            ingest_source(run)
        except (IngestError, KeyError, TypeError, ValueError) as exc:
            # A response of the wrong shape fails only its own source (AC-1.6, 1.12).
            reason = (
                exc
                if isinstance(exc, IngestError)
                else f"malformed response ({type(exc).__name__}: {exc})"
            )
            log.error("%s: %s; no file written", name, reason)
            if name != "mrms":
                failed.append(name)
    if failed:
        log.error("failed sources: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
