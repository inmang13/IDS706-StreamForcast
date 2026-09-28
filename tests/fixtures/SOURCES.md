# Fixture provenance

All real responses were recorded on **2026-09-24, about 23:20 UTC (19:20 America/New_York)**
with `curl`, and are saved byte-for-byte. Fixture mode and the tests use `AS_OF_DATE=2026-09-24`
(decision D27). Common query parts:

- USGS: `https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items?monitoring_location_id=USGS-02085000&parameter_code=00060&statistic_id=00003&limit=10000&f=json`
- Open-Meteo point: `latitude=36.0711&longitude=-79.0956` (snaps to grid cell 36.072254, -79.1094, elevation 163 m)
- Open-Meteo daily: `daily=precipitation_sum,temperature_2m_max,temperature_2m_min`
- Open-Meteo hourly (previous runs): `hourly=precipitation,precipitation_previous_day1,precipitation_previous_day2`

## Real responses

| File | Request |
|---|---|
| `usgs_daily_recent.json` | USGS + `&datetime=2026-09-14/2026-09-23` (10 rows, unordered, all Provisional) |
| `usgs_daily_chantal.json` | USGS + `&datetime=2025-06-25/2025-07-15` (7-05 15.5, 7-06 1990, 7-07 8180, 7-10 922 cfs; 7-06..7-15 ESTIMATED, including the 8180 peak) |
| `openmeteo_forecast_past7.json` | `https://api.open-meteo.com/v1/forecast?<point>&<daily>&forecast_days=3&past_days=7&timezone=America%2FNew_York` (2026-09-17..2026-09-26; forecast days 09-24..09-26) |
| `openmeteo_hist_forecast.json` | `https://historical-forecast-api.open-meteo.com/v1/forecast?<point>&<daily>&start_date=2026-09-14&end_date=2026-09-23&timezone=America%2FNew_York` (09-18 51.5 mm, 09-21 47.2 mm) |
| `openmeteo_hist_chantal_et.json` | historical-forecast, `start_date=2025-06-25&end_date=2025-07-15&timezone=America%2FNew_York` (07-06 54.7, 07-07 15.1, 07-09 49.7 mm) |
| `openmeteo_hist_chantal_gmt.json` | same, `timezone=GMT` (07-06 7.9, 07-07 61.9 mm) |
| `openmeteo_previous_runs.json` | `https://previous-runs-api.open-meteo.com/v1/forecast?<point>&<hourly>&start_date=2026-09-14&end_date=2026-09-23&timezone=America%2FNew_York` (09-18: lead 1 0.7 mm, lead 2 4.1 mm; 09-21: 0.0 and 24.5 mm) |
| `openmeteo_previous_runs_dst_fall.json` | previous-runs, `start_date=2025-11-01&end_date=2025-11-03` (spans the fall-back day) |
| `openmeteo_previous_runs_dst_spring.json` | previous-runs, `start_date=2025-03-08&end_date=2025-03-10` (spans the spring-forward day) |
| `iem_mrms_chantal.json` | `https://mesonet.agron.iastate.edu/iemre/multiday/2025-06-25/2025-07-15/36.0711/-79.0956/json` (07-06 7.01 in) |
| `iem_mrms_recent.json` | `https://mesonet.agron.iastate.edu/iemre/multiday/2026-09-12/2026-09-23/36.0711/-79.0956/json` (09-18 0.0 in, 09-21 1.66 in) |
| `openmeteo_error.json` | historical-forecast with `start_date=2015-01-01&end_date=2015-01-05` → HTTP 400 `{"error":true,"reason":"Parameter 'start_date' is out of allowed range from 2016-01-01 to 2026-10-09"}` |

Observed on the DST fixtures: Open-Meteo returns **24 hourly values for every local date**,
including 2025-03-09 and 2025-11-02, and reports one `utc_offset_seconds` (-14400) for the
whole response. It applies the zone's offset *at request time* to every date (a January
request made today also comes back at -14400). See Stage 1 Implementation notes.

## Hand-edited copies

| File | Made from | Edit |
|---|---|---|
| `usgs_daily_estimated_gap.json` | USGS + `&datetime=2025-07-08/2025-07-18` (recorded as above) | Removed the 2025-07-15 feature and set `numberReturned` to 10. No real window in 2006–2026 has both an ESTIMATED day and a gap: the only gap is 2026-09-09..10, and the ESTIMATED days are 2025-07-06..18. Every day in this window is ESTIMATED, so the latest day (2025-07-18) is ESTIMATED. |
| `usgs_empty.json` | — | Written by hand: `{"type":"FeatureCollection","features":[],"numberReturned":0}` |
| `openmeteo_forecast_short.json` | `openmeteo_forecast_past7.json` | Dropped the last day (2026-09-26) from every `daily` array, leaving 2 forecast days |
| `openmeteo_previous_runs_null_hour.json` | `openmeteo_previous_runs.json` | Set `precipitation_previous_day1` at `2026-09-20T13:00` to `null` |
| `iem_mrms_all_null.json` | `iem_mrms_recent.json` | Set every `mrms_precip_in` to `null` (reproduces RK18a) |
