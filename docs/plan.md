# StreamForecast — living plan

## Project

### Problem statement

The Eno River at Hillsborough, NC (USGS 02085000, 66 sq mi) swings from a trickle of a few cfs in dry spells to thousands of cfs in tropical storms (8,180 cfs on 2025-07-07, Tropical Storm Chantal). Paddlers, anglers, Eno River State Park staff, and Orange County emergency and utility planners need to know whether the river will rise or fall over the next few days, and how sure that is. StreamForecast publishes an automated daily 3-day streamflow forecast with 80% and 95% uncertainty bands, always shown next to the "no change" persistence baseline so users can judge whether the model adds value.

### Requirements

- **R1** Forecast daily mean discharge (cfs, USGS parameter 00060, statistic 00003) for 02085000 at horizons t+1, t+2, t+3, where t is the last complete USGS day (d0).
- **R2** Each forecast row carries a median and 80% and 95% bounds, ordered `lo95 ≤ lo80 ≤ median ≤ hi80 ≤ hi95` and never negative.
- **R3** Inputs: USGS daily values (new Water Data OGC API) and Open-Meteo daily `precipitation_sum`, `temperature_2m_max`, `temperature_2m_min` (historical-forecast and forecast endpoints), plus Open-Meteo previous-runs hourly precipitation at lead 1 and lead 2 days (D20), all on America/New_York calendar dates. NOAA MRMS 24-hour QPE (via IEM) is ingested as an independent observed-rain reference for **verification only**. It is never a model input (D24).
- **R4** Five stages (ingest, features, train, forecast, dashboard) hand off only through files under `DATA_DIR` (default `./data`). No stage imports another stage's module.
- **R5** Incremental ingest: after the first backfill, a run fetches only new days plus a short lookback window for revised provisional values.
- **R6** Time-series discipline: chronological splits (train 2018-01-01..2023-12-31, calibration 2024-02-01..2024-12-31, test 2025-01-01 onward; January 2024 is unused). No feature uses information from after the issue time, with **one deliberate, documented exception, limited to training rows**: there, `precip_f2` and `precip_f3` come from Open-Meteo historical-forecast data, which is stitched from forecasts made about 0 days ahead, so they are better than a real 1- or 2-day-ahead forecast (D2, RK3). **Calibration and test rows use lead-matched forecasts** from the previous-runs archive (D20), so they contain only what was forecast at issue time. Flow, antecedent precipitation, and temperature features use nothing after d0.
- **R14** Every "today", "yesterday", and "run date" is computed by `paths.local_today()`: `AS_OF_DATE` if it is set, otherwise `datetime.now(ZoneInfo(TIMEZONE)).date()`. Never `date.today()` or UTC; containers run in UTC, so at 8 pm ET UTC is already tomorrow. `AS_OF_DATE` is for fixture mode, smoke tests, and CI only; it is empty in production (D26).
- **R7** Every forecast and every metrics report includes the persistence baseline (Q[d0+h] = Q[d0]).
- **R8** One direct scikit-learn model per horizon. Intervals come from empirical residual quantiles on the calibration period. No model comparison, with **one pre-declared exception**: the single alternative that D23 allows, under D23's fixed selection rules.
- **R9** A versioned checkpoint plus a JSON metrics sidecar, including skill vs persistence and interval coverage (overall and high-flow).
- **R10** A Streamlit dashboard that reads only files under `DATA_DIR`.
- **R11** All configuration comes from environment variables with documented defaults.
- **R12** The Makefile is the public interface. Automated tests never touch the network, and CI passes offline.
- **R13** Docker Compose with two services (pipeline scheduler, dashboard) sharing a named volume, a healthcheck, and a non-root user.

### Architecture overview

Solid arrows are file hand-offs (no stage imports another). Thick arrows show the scheduler's run order inside the pipeline container. Dotted arrows are read-only.

```mermaid
flowchart LR
  subgraph EXT["External sources (network; tests use recorded fixtures instead)"]
    USGS["USGS Water Data API<br/>daily mean flow, 00060 / 00003"]
    OMH["Open-Meteo historical-forecast<br/>training weather, 2018+"]
    OMP["Open-Meteo previous-runs<br/>rain at lead 1 and 2 days, 2024+"]
    OMF["Open-Meteo forecast<br/>3 days + 7 past days"]
    MRMS["NOAA MRMS 24 h QPE via IEM<br/>observed rain, verification only"]
  end

  subgraph PIPE["Stage 6: pipeline container<br/>python -m streamforecast.scheduler, every 24 h"]
    ING["Stage 1 Ingest"]
    FEA["Stage 2 Features<br/>+ rain check"]
    TRN["Stage 3 Train<br/>only if no checkpoint or older than 7 d"]
    FCT["Stage 4 Forecast"]
    ING ==> FEA ==> TRN ==> FCT
  end

  subgraph VOL["Named volume sfdata, mounted at /data"]
    RAW[("raw/")]
    FEAT[("features/")]
    MOD[("models/")]
    FCS[("forecasts/")]
  end

  subgraph DASHC["Stage 6: dashboard container<br/>healthcheck /_stcore/health"]
    APP["Stage 5 Streamlit dashboard :8501"]
  end

  USGS --> ING
  OMH --> ING
  OMP --> ING
  OMF --> ING
  MRMS --> ING
  ING --> RAW
  RAW --> FEA
  FEA --> FEAT
  FEAT --> TRN
  TRN --> MOD
  MOD --> FCT
  FEAT --> FCT
  FCT --> FCS
  FCS -.-> APP
  FEAT -.-> APP
  MOD -.-> APP
  APP --> USER["Browser<br/>http://localhost:8501"]

  S0["Stage 0 Skeleton: config from env, paths, Makefile,<br/>pytest markers, black + flake8, offline CI"]
  S0 -.-> PIPE
  S0 -.-> DASHC
```

Locally, the same stages run one at a time with `make ingest`, `make features`, `make train`, `make forecast`, `make dashboard`, or all four pipeline stages once with `make pipeline`.

### Out of scope

- Model comparison (except the single pre-declared alternative in D23, selected only on 2023), hyperparameter search, deep learning, and ensembles of different model families.
- Sub-daily (instantaneous, 15-minute) data, other gauges, and upstream routing.
- Flood-stage alerts, notifications, user accounts, and a public deployment/hosting setup.
- Databases, message queues, object storage, cron daemons, or any service beyond the two Compose services.
- Model inputs from weather sources other than Open-Meteo (MRMS is used for verification only, D24), and downscaling or bias-correcting weather.
- Retrospective hindcast archives of past issued forecasts (beyond keeping the forecast CSVs that are produced).

### Risks and design concerns

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| RK1 | USGS legacy `waterservices.usgs.gov` is "decommissioned in early 2027" (banner on its landing page, checked 2026-09-23). | Certain | High | Use `api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items` from day one (D1). |
| RK2 | Train/serve weather skew: training on reanalysis while serving forecasts would make intervals too narrow. | High if unmanaged | High | Train on the Open-Meteo **historical-forecast** endpoint, which uses the same model and grid as the live forecast endpoint (D2). Verified 2026-09-23: historical-forecast values for 9/15–9/22 equal the forecast endpoint's `past_days` values. |
| RK3 | **Forecast-weather skew biases the intervals too narrow.** Historical-forecast is stitched from the shortest-lead hours of successive runs, so every training, calibration, and test row sees rain forecast about 0 days ahead: close to observed, though not the same (on 2026-09-18 the lead-0 value was 51.5 mm, all in one hour, while the gauge stayed flat). At serve time, issued on the morning of d0+1, `precip_f1` is a lead-0 forecast (today), `precip_f2` is lead-1, and `precip_f3` is lead-2. Those can be very different: for 2026-09-18 the lead-1 and lead-2 forecasts were 0.7 and 4.1 mm; for 2026-09-21, lead-0 47.2 vs lead-1 0.0 vs lead-2 24.5 mm. **Effects:** (a) the calibration residuals contain no lead-1/lead-2 weather error, so the 80%/95% bands are too narrow; (b) the gap grows with horizon (t+1 almost unaffected, t+3 most affected); (c) the misses concentrate on rain days (missed storms land above `hi`, false alarms below `lo`), the same high-flow days RK6 already weakens, while dry recession days are barely affected; (d) the model learned to trust rain that was near-certain, so the median over-reacts to forecast rain, and `r_0.50` measured on lead-0 inputs cannot correct that; (e) **AC-3.9 as written measures test coverage with lead-0 rain, so it can pass while live forecasts under-cover.** This is the documented exception to the no-future-information rule (R6). | High | High | **Fixed for the intervals (D20, Student).** Open-Meteo's previous-runs API (`previous-runs-api.open-meteo.com/v1/forecast`, hourly `precipitation_previous_dayN`, archived from **2024-01-19**) provides lead-matched forecasts. Training stays on historical-forecast (no lead archive exists before 2024), but **calibration and test** rows use lead-matched rain (`precip_f1` = lead-0 historical-forecast, `f2` = `previous_day1`, `f3` = `previous_day2`, hourly values summed per local date; AC-2.8). The residual quantiles and the median bias correction therefore absorb the real serve-time weather error for (a)–(c) and (e), and AC-3.9 measures what users will see. **Still a documented limitation:** (d) the model itself trains on near-perfect rain, so it over-trusts forecast rain, and its point skill is lower than a lead-matched model's would be. The calibration set is smaller (about 335 days; RK6). |
| RK17 | Historical-forecast accepts `start_date=2016-01-01`, but every daily value is **null for 2016-01-01..2017-12-31** (checked 2026-09-23: 731 null precipitation days; the first non-null is 2018-01-01; `gfs_seamless`, `ecmwf_ifs025`, and `ncep_gfs013` are also null in 2016). | Certain | Medium | `HISTORY_START=2018-01-01` (D18). Ingest logs a WARNING if any returned weather day is null. Features leave nulls as NaN and never fill weather. |
| RK18 | MRMS through IEM has quirks. (a) One long `multiday` request (2014–2026) returned HTTP 200 with **every `mrms_precip_in` null**, while one-year requests return complete data (2018, 2020, 2024, 2025 all non-null). (b) IEM's MRMS daily totals follow a **Central Standard Time** day ("MRMS CST Daily" on the IEM page, i.e. 06Z–06Z), which runs 1–2 hours behind the America/New_York day, so rain between about midnight and 1–2 am ET counts on the previous day. (c) The value is for IEM's 0.125° grid cell at the gauge point, not a basin average. (d) IEM is a university service (Iowa State), not an official NOAA endpoint. | Certain (a–c); Low (d outage) | Low | Request one calendar year per call and treat a response with all values null as an error (AC-1.11). Document (b) and (c) as known limitations of a verification reference. They are small next to the disagreements MRMS exists to catch (e.g. 51.5 mm vs 0.0 mm). An MRMS failure never blocks forecasting (AC-1.11). |
| RK4 | Rain sources disagree strongly: on 2026-09-18 the forecast grid gave 51.5 mm, the archive 5.6 mm, and the gauge barely moved. The forecast-grid rain can be badly wrong. | Medium | Medium | Train on the same source we serve (RK2), so the model learns how far to trust that source's rain. Keep the persistence baseline visible. |
| RK5 | Floods larger than anything in training. Tree models cannot extrapolate absolute values; the largest training peak is 3,270 cfs vs Chantal's 8,180 cfs in the test period. | Medium | High | The target is the log-change `log1p(Q[d0+h]) − log1p(Q[d0])` (D8), so predictions scale with today's flow. Floods are never clipped (AC-2.3). High-flow coverage is reported separately. |
| RK6 | The calibration period (2024-02-01..2024-12-31, 335 days after D20) has only 6 days at or above the 99th percentile (653 cfs), and 28 days above the training 90th percentile (152 cfs, 2018–2023). The 2.5% and 97.5% residual quantiles rest on about 8 points per tail, so the bands reflect mostly low-flow errors and may under-cover floods. | High | Medium | The metrics sidecar reports coverage for high-flow days separately (AC-3.5), and `make coverage` prints it (AC-3.9). Listed as a known limitation on the dashboard's metrics caption. The calibration period does include Debby (1,820 cfs, 2024-08-08) and Helene (1,810 cfs, 2024-09-27). |
| RK7 | Provisional USGS values ("Provisional") are revised later, sometimes months later. | Certain | Low | Ingest re-fetches a `USGS_LOOKBACK_DAYS` window (default 30). Features dedupe by date with the newest file winning. Forecast rows record whether the latest observation is provisional. |
| RK8 | USGS has not posted yesterday's value at run time (it was absent for "today" on 2026-09-23 and present for yesterday). A scheduler started early in the morning may often hit this. | Medium | Medium | Anchor on d0 = the last complete USGS day (D4). Forecast from a d0 up to `MAX_STALE_DAYS` (2) old, flagged, and refuse beyond that (AC-4.5, D21). The dashboard marks outdated forecasts and valid dates already in the past (AC-5.5). |
| RK9 | Leakage via weather: the Open-Meteo archive's default model returned a partial-day 15.2 mm for **today**. Interpolating gaps also uses future values. | Medium | High | Do not use the archive endpoint. Historical-forecast is requested with `end_date = yesterday`. Gaps are only forward-filled (at most 2 days), never interpolated. An automated leakage test perturbs future data (AC-2.6). |
| RK10 | Near-zero flow (current flow is about 4 cfs; record minimum since 2006 is 0.22 cfs) breaks `log`. | Medium | Medium | Use `log1p`/`expm1` everywhere and clip bounds at 0 (AC-4.3). |
| RK11 | The new USGS API returns rows **unordered**, mixes statistics unless you filter, and gives values as **strings**. | Certain | Medium | Always send `statistic_id=00003`, parse `value` as float, and sort by `time` client-side. A fixture test covers unordered input (AC-1.3). |
| RK12 | API rate limits or outages (HTTP 429/5xx, Open-Meteo `{"error": true, "reason": ...}` bodies), or a truncated forecast (fewer than 3 days). | Low–Medium | Medium | Retry with exponential backoff that honours `Retry-After` (capped at 60 s), 3 attempts. On final failure, log an error and exit non-zero without writing a partial file; later stages continue from the previous files (AC-1.5, AC-1.6). Forecast refuses to run on forecast weather not fetched today or with fewer than 3 days, and never writes partial rows (AC-4.7, AC-4.10, D21). An optional `USGS_API_KEY` is sent if set. |
| RK13 | Time-zone misalignment between USGS local days and Open-Meteo aggregation. Open-Meteo sums daily values over whatever timezone is requested. Verified on Chantal: requested in America/New_York, 2025-07-06 has 54.7 mm and 07-07 has 15.1 mm; requested in GMT, the same storm becomes 7.9 mm and 61.9 mm, which puts the heavy rain a day *after* flow jumped from 15.5 to 1,990 cfs on 07-06. The model would learn that flow rises before rain. | Medium if unpinned | High | Pin both sources to America/New_York (AC-1.10). Open-Meteo requests always send `timezone=America/New_York`, and ingest rejects any response whose `timezone` field differs. USGS `time` and Open-Meteo `daily.time` are stored verbatim as `YYYY-MM-DD` strings and never parsed into timestamps or converted to UTC. Previous-runs hourly values are summed by their local date (AC-1.9). Features join on the date string. A real-data alignment test (AC-2.5) fails if the pinning breaks. |
| RK14 | A named volume is owned by root while the container runs non-root. | Medium | Medium | Create `/data` and `chown` it to the app user in the Dockerfile before declaring it as a volume, so Docker seeds the named volume with the right owner (AC-6.4). |
| RK15 | Windows development: `make` runs in Git Bash, `python3` is not on PATH, and venv paths differ. | Medium | Low | The Makefile uses `PYTHON ?= python` and the currently active environment. It does not hard-code `.venv/bin`. CI (Ubuntu, Python 3.12) is the reference environment (D6). |
| RK16 | A checkpoint made with one scikit-learn version fails to load with another. | Low | Medium | Pin exact dependency versions and store `sklearn_version` in the checkpoint. Forecast logs a clear error on mismatch or corruption (AC-4.6). |

### Decisions log

| ID | Decision | Alternatives considered | Decided by | Why |
|---|---|---|---|---|
| D1 | Use the new USGS Water Data OGC API: `https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items?monitoring_location_id=USGS-02085000&parameter_code=00060&statistic_id=00003&datetime=START/END&limit=10000&f=json`. | Legacy `waterservices.usgs.gov/nwis/dv` (still returns 200). | AI | The legacy landing page (checked 2026-09-23) says "WaterServices will be decommissioned in early 2027. Applications will need to migrate to the APIs hosted at https://api.waterdata.usgs.gov." Verified that the new API returns all 7,568 rows for 2006–2026 in one page with `limit=10000`, and that `datetime=` range filtering works. |
| D2 | Train on the Open-Meteo **historical-forecast** endpoint (`historical-forecast-api.open-meteo.com/v1/forecast`). This **replaces the brief's "historical archive"** at the student's direction. At serve time, use the forecast endpoint with `forecast_days=3&past_days=7`. | ERA5 / archive endpoint for 20+ years (the brief's wording); a hybrid (ERA5 for training, historical-forecast for calibration). | Student (accepted the AI's recommendation, Q2) | Same model and grid at train and serve time, so no train/serve skew. The model learns how much to trust forecast rain (RK4). The hybrid adds a second weather source for little flood signal. Accepted cost: the forecast-weather exception in R6 and RK3. |
| D3 | Training window and splits by issue date: train through 2022-12-31, calibration 2023-01-01..2024-12-31, test 2025-01-01..latest. A row is in a split only if its target date d0+h is also inside that split. (Start date amended to 2018-01-01 by D18. Splits amended by D20: train 2018-01-01..2023-12-31, calibration 2024-02-01..2024-12-31, test 2025-01-01+.) | ~20-year window 2006–2022 (ERA5); splits relative to "now". | Student (accepted the AI's recommendation, Q3, after asking whether 2016–2022 was too short) | The mid-2010s onward is the flood-richest stretch since 2006. 2006–2015 adds about 3,650 mostly low-flow days and only 17 extreme days (at or above the 99th percentile, 653 cfs). Chantal (2025) lands in test as an out-of-range stress case. Weakness accepted: few extremes in calibration (RK6), which is why high-flow coverage is reported separately. |
| D4 | Horizon anchor: d0 = last date with a valid USGS daily mean. t+h = d0+h for h = 1, 2, 3. With normal posting, d0 = yesterday, so t+1 = today, which matches `forecast_days=3` (Open-Meteo day 1 is today). | t+1 = tomorrow (needs 4 forecast days and a gap day with no observation). | Student (accepted the AI's recommendation, Q1) | Features are identical at train and serve time. When USGS is late, the model forecasts from d0 anyway and reports `stale_days`. |
| D5 | Schedule: one long-running scheduler process in the pipeline container. It runs ingest → features → (train if needed) → forecast immediately on start, then every `RUN_INTERVAL_HOURS` (default 24). It retrains when no checkpoint exists or the newest is older than `RETRAIN_DAYS` (default 7). `make pipeline` runs a single pass of the same scheduler (`--once`). | cron inside the container; a separate scheduler service; retrain every run. | Student (accepted the AI's recommendation, Q4) | Smallest thing that works, with no extra daemon. Weekly retraining picks up USGS revisions (provisional → approved) without refitting daily. |
| D6 | Python 3.12 in Docker (`python:3.12-slim`) and CI (`ubuntu-latest`). Locally, `make` runs in Git Bash with `PYTHON ?= python` inside an activated venv. Any local Python ≥ 3.12 is accepted. | Pin 3.13 everywhere; Makefile creates and uses `.venv` paths. | Student (accepted the AI's recommendation, Q5) | 3.12 has the widest wheel support for the pinned stack. Not hard-coding venv paths keeps the Makefile portable between Windows and Linux. |
| D7 | Model family: `sklearn.ensemble.HistGradientBoostingRegressor` with default hyperparameters and `random_state=0`, one model per horizon. | Ridge on log flow; RandomForestRegressor. | AI | The rain-runoff response is strongly nonlinear and depends on wetness (the same rain on dry vs wet soil gives very different flow). Gradient-boosted trees capture that without tuning, handle missing values natively, and train in seconds on about 2,190 rows. No search is performed (non-negotiable). |
| D8 | Target: log-change `y_h = log1p(Q[d0+h]) − log1p(Q[d0])`. | Absolute `log1p(Q)`; raw cfs. | AI | Tree models cannot predict beyond the target range seen in training. Predicting a ratio relative to today's flow lets forecasts scale with the current state (RK5). `log1p` handles near-zero flow (RK10). The persistence forecast corresponds to `y_h = 0`, which makes model-vs-baseline comparison natural. |
| D9 | Intervals: per horizon, calibration residuals `r = y_true − y_pred` in log-change space. Store the quantiles at 0.025, 0.10, 0.50, 0.90, 0.975. Serve `q_p = log1p(Q[d0]) + ŷ + r_p`, then `cfs = max(0, expm1(q_p))`. The median uses `r_0.50` (bias correction). Models are **not** refit on calibration data. | Quantile regression models; conformal wrappers; residuals in cfs. | AI | This is exactly what the brief asks for. Log space gives multiplicative bands, which fit flow errors that grow with flow. Using monotone transforms of ordered quantiles guarantees band ordering. Not refitting keeps the calibration residuals honest out-of-sample. |
| D10 | File formats: CSV for raw, features, and forecasts; `joblib` for model checkpoints; JSON for metrics sidecars. All file names carry a UTC timestamp `YYYYMMDDTHHMMSSZ`. | Parquet (adds pyarrow); SQLite. | AI | Human-readable during smoke tests, and no extra dependency. The data are tiny (about 4,000 rows). |
| D11 | Stage isolation: the stages `ingest`, `features`, `train`, `forecast`, `dashboard` may import only the shared helpers `config`, `paths`, `logs`. The scheduler runs stages as subprocesses (`python -m streamforecast.<stage>`), never by import. | A scheduler that imports stage functions; a Makefile-only loop. | AI | Enforces the file-handoff contract mechanically. An AST-based test checks it (AC-0.5). |
| D12 | HTTP layer: `requests`, mocked in tests with `responses`. `pytest-socket` (`--disable-socket`) makes any real network call fail the suite. | `requests-mock`; `vcrpy`; `httpx` + `respx`. | AI | Small, common, and the socket block proves "offline" rather than trusting it. |
| D13 | Gap handling: reindex to a daily calendar. Forward-fill flow only for gaps of at most `MAX_FFILL_DAYS` (default 2), and only for lag inputs. Never interpolate. Rows with a missing target or with lags still missing are dropped. | Linear interpolation; dropping every row touched by a gap. | AI | Interpolation uses the value after the gap (leakage). A short forward-fill keeps rows usable without inventing future information. |
| D14 | Value validity: **physically impossible** = non-numeric, NaN, negative, or the USGS sentinel `-999999` → dropped and counted in the log. **Extreme but real** = any finite value ≥ 0 → always kept, never clipped or winsorized. `ESTIMATED` qualifiers and `Provisional` status are kept and flagged. | Outlier removal by z-score or IQR. | AI | Floods are the signal this forecast exists for. The legacy `noDataValue` observed was `-999999.0`. The new API showed no sentinel, but the check is kept defensively. |
| D15 | Two Compose services built from the same image: `pipeline` (scheduler) and `dashboard` (Streamlit), sharing the named volume `sfdata` mounted at `/data`. | A single container running both processes. | AI | Two processes with different lifecycles (a batch loop vs a web server) are what Compose is for. A single container would need a process supervisor. One image keeps the build single. |
| D16 | Dashboard charts use Altair (already a Streamlit dependency). | Plotly; matplotlib. | AI | No new dependency. It supports layered area bands for the fan chart. |
| D18 | `HISTORY_START = 2018-01-01`, so training covers 2018-01-01..2022-12-31 (about 1,800 rows per horizon). (Training end later moved to 2023-12-31 by D20, about 2,190 rows.) | Keep 2016 and let HGB treat the missing weather as NaN; fill 2016–2017 from the ERA5 archive. | Student (AI-proposed amendment to D3; the student confirmed it at Gate 1) | Found while writing the plan: historical-forecast returns nulls for every day of 2016–2017 (RK17). 2018–2022 still holds 36 of the 76 days at or above the 99th percentile since 2006, peaking at 2,760 cfs (more than all of 2006–2015, which had 17). It loses the 2017 peaks (3,270 and 2,290 cfs) and Matthew (2016). Filling with ERA5 would reintroduce the second source D2 rejected. |
| D19 | Test-period coverage tolerances: 80% band ∈ [0.72, 0.88]; 95% band ∈ [0.90, 0.99], per horizon. Gated by `make coverage` (AC-3.9). High-flow coverage is reported, not gated. | Binomial ±2 SE with n ≈ 630 (about ±0.03); no gate, report only; conformal methods that guarantee coverage. | Student (accepted the AI's recommendation after asking how coverage would be verified) | Daily errors are strongly autocorrelated: one wet week produces a run of misses. So the effective sample is closer to about 150 independent outcomes than 630. At n_eff ≈ 150, 2.5 standard errors is about ±0.08 for the 80% band and about ±0.05 for the 95% band, with the 95% upper bound capped at 0.99 because bands that almost never miss are too wide to be useful. The check must be tight enough to catch the RK3/RK6 under-coverage it exists to expose. |
| D20 | Lead-matched forecast rain for calibration and test rows from Open-Meteo previous-runs (`precip_f2` ← `precipitation_previous_day1`, `precip_f3` ← `precipitation_previous_day2`, hourly summed per local date; `precip_f1` stays lead-0). Splits move to train 2018-01-01..2023-12-31, calibration 2024-02-01..2024-12-31, test 2025-01-01+. Training rows keep historical-forecast rain. | Document the skew only, and label AC-3.9 as an upper bound on live coverage; lead-match training too (impossible: no archive before 2024-01-19). | Student (accepted the AI's recommendation after asking how serve-time forecast weather biases the intervals) | Without it, calibration errors omit the forecast-rain error at 1 and 2 days ahead, so the bands are too narrow and the coverage check gives false comfort (RK3 a–e). Cost: one more ingest source, hourly-to-daily aggregation, and a smaller calibration set (335 vs about 730 days; RK6). |
| D21 | Forecast behaviour on missing or stale inputs. **USGS late:** forecast from d0 if `stale_days ≤ MAX_STALE_DAYS` (2), else refuse. **Provisional/estimated latest value:** use as-is and flag. **Flow lags incomplete** after the 2-day forward-fill: refuse. **Forecast weather has fewer than 3 days, contains nulls, or was not fetched today:** refuse. **Rate limit or outage:** handled in ingest (retry, then fail that source only); Forecast then applies the staleness rules to whatever files exist. A refusal never writes partial rows and never deletes an earlier forecast; the dashboard shows the newest existing forecast marked as outdated. Exit codes: 0 = forecast written or no checkpoint yet; 1 = refused or checkpoint unusable. | Fill yesterday by persistence; forecast fewer rows; widen bands when stale; refuse on provisional data. | AI | Refusing is safer than inventing inputs: a forecast built on filled flow or old weather looks as confident as a real one. Refusing on provisional data would block almost every run, because all recent USGS values are provisional (41 of 41 in 2026). A limit of 2 days is the largest at which at least one valid date (today) is still in the future. |
| D22 | With fewer than 3 forecast-rain days, Forecast refuses and writes nothing (AC-4.7 unchanged). Every horizon model keeps all three `precip_f` inputs (AC-3.2 unchanged). | Per-horizon rain inputs (the t+h model uses `precip_f1..fh` only), which would allow partial 1–2-row forecasts. | Student (kept the AI's recommendation; chose simplicity for now) | A truncated Open-Meteo response is rare (never seen during checks). Exactly 3 rows keeps the forecast file, the fan chart, and all downstream checks simple. The dashboard already shows the previous forecast as outdated. Can be revisited later. |
| D23 | **Protocol if the model performs poorly**, fixed before any results are seen. (1) **Diagnose first:** check join dates, the target and band signs, the lead-matched coverage, that features match between training and forecasting, and the MRMS rain check (AC-2.9) for the failing periods: large misses on days where Open-Meteo rain disagrees with MRMS point to a weather-input problem, not the model; bugs are fixed to match the plan and don't count as model changes. (2) If the pipeline is correct and the model still fails AC-3.8 (no skill vs persistence) at any horizon, **at most one** alternative may be tried, chosen by the student and logged here as a new Student decision (e.g. `Ridge` on the same features, or per-horizon rain inputs from D22). (3) The alternative is judged **only on 2023** (the last training year, held out and refit afterward), never on the calibration period (it sets the bands) and **never on the 2025+ test split**. (4) Whatever is chosen is re-scored once on test and reported, including when it is still worse than persistence. No grid or random search. | Open-ended tuning; comparing several model families; selecting on test results. | Student (accepted the AI's recommendation after asking whether there is a plan to improve a poor model) | The brief forbids model comparison and hyperparameter search, so the default is to accept and report the result. Diagnosing first catches the more likely cause (a bug). Pre-committing the rules and keeping selection off the test split keeps the test score an honest estimate. Near-zero skill at t+1 is plausible, because flow barely changes on dry days. It is a result to report, not something to tune away. |
| D24 | Use NOAA MRMS 24-hour multi-sensor QPE as a second, independent source of truth for rain, accessed through IEM's IEMRE API (`https://mesonet.agron.iastate.edu/iemre/multiday/{start}/{end}/{lat}/{lon}/json`, field `mrms_precip_in`, inches → mm). **Verification only:** it feeds the rain check (AC-2.9), the real-data alignment test (AC-2.5), and D23 diagnosis. It is **not** a model input. | NOAA's own archive on AWS (`noaa-mrms-pds`, `CONUS/MultiSensor_QPE_24H_Pass2_00.00/`: one 4.5 MB GRIB2 per hour, starting 2020-10-14, which would need eccodes/cfgrib and about 10 GB to backfill daily values); MRMS as antecedent-rain features. | Student chose MRMS as the second source of truth; AI chose the access path and the verification-only role | IEM returns small JSON point values back to at least 2018, so there are no GRIB2 dependencies or gigabyte downloads. Keeping MRMS out of the model avoids a new serve-time dependency and train/serve differences. The model's inputs stay exactly what the brief specifies. Evidence it is worth having: on 2026-09-18 Open-Meteo lead-0 said 51.5 mm, MRMS said 0.0 mm, and the gauge stayed flat. On Chantal (2025-07-06) MRMS said 7.01 in (178 mm) vs Open-Meteo's 54.7 mm, on the same day as the flow jump. |
| D25 | Report skill and coverage separately for **wet** windows (MRMS rain summed over d0+1..d0+h ≥ 10 mm) and **dry** windows (< 2 mm) on the test split, per horizon (AC-3.10). Reported, not gated. MRMS labels rows only after prediction. | Label rain days with Open-Meteo rain (the input being judged); no split; gate on wet-day skill. | Student (accepted the AI's suggestion) | An overall skill near zero can hide a model that beats persistence when it matters (rain) and ties it on dry days, where "no change" is almost exact. Using observed MRMS rather than the forecast rain keeps the label independent of the model's input. It also feeds step 1 of D23. The 10 mm / 2 mm cutoffs are judgment calls (the 10 mm matches the standard R10mm "heavy precipitation day" index; 2 mm absorbs MRMS noise), not tuned. **Flagged by the student to re-examine later** (see "Revisit later"). |
| D26 | Apply an external review of this plan (Codex, 2026-09-23) as triaged by the AI. (1) **Accepted:** feature availability matrix plus a marker-value test (AC-2.10). (2) **Reduced:** hard/soft failure rules with Forecast as the freshness gate, and the pass exit equal to Forecast's exit (AC-6.10). (3) **Reduced:** atomic writes for every published file (AC-0.9), and the dashboard shows the metrics matching the forecast's `model_version` (AC-5.1). (4) **Accepted:** AC-3.9 is met when the check is correct; a real-data miss is a finding under D23, not an unmet criterion. (5) **Accepted:** `AS_OF_DATE` clock injection for fixtures, CI, and smoke tests (R14, AC-0.8, AC-1.7). (6) **Accepted in part:** response validation for units, lengths, date coverage, and grid cell, plus general pagination (AC-1.12), and exact dependency pins enforced by a test (AC-0.10). (7) **Accepted:** R8 and Out of scope now name D23's single alternative as the only exception. (8) **Accepted:** metric formulas and edge cases (AC-3.5). | Adopt every recommendation in full, including a run/snapshot ID with an atomic published manifest (for 3) and a freshness/status manifest gating downstream stages (for 2), and pinning a named Open-Meteo model; or ignore the review. | Student (accepted the AI's triage of the Codex review) | The findings were valid: fixture runs would have failed on the real date, the coverage criterion was ambiguous, and metrics could silently become `NaN`. **Declined parts:** the manifests add a coordination layer that the brief's "smallest design" rules out; atomic writes, Forecast's freshness checks, and version-matched metrics fix the same failures more simply. A named Open-Meteo model isn't pinned because the verified default is what train and serve share (D2); grid-cell recording (AC-1.12) detects a silent change. |
| D17 | Offline fixture mode: `INGEST_SOURCE=fixtures` makes ingest read recorded responses from `tests/fixtures/` instead of HTTP. | No offline mode. | AI | Required by the course board ("offline fixture mode"). It lets the ingest smoke test and the container be demonstrated without network access. |
| D27 | Fixtures are recorded on 2026-09-24, and fixture mode, tests, and smoke tests use `AS_OF_DATE=2026-09-24` instead of 2026-09-23 (AC-1.7 and the Stage 1 smoke test). | Keep 2026-09-23 and hand-shift a forecast response recorded a day later; build a "forecast as of 09-23" from previous-runs data. | Student (accepted the Builder's recommendation, Builder Q1) | The build started on 2026-09-24. A forecast response recorded then starts on 09-24, so with `AS_OF_DATE=2026-09-23` `fc_fetched_date` would not match the latest d0. A real 09-23 forecast can no longer be recorded. The 09-18 and 09-21 test cases stay inside the 7 past days. |
| D28 | Where `weather_hist` and `weather_fc` both cover a date, the `weather_fc` value wins when its file is at least as new. Ingest writes `weather_fc` after `weather_hist`, and every file from one run shares the run's timestamp, so Features breaks ties in favour of `weather_fc`. The AC-2.10 marker test picks its test row outside the 7-day `weather_fc` window. | Historical-forecast always wins for past dates; newest file wins with no tie rule. | Student (accepted the Builder's recommendation, Builder Q2) | Resolves AC-2.1 vs the AC-2.10 matrix: the live row must use `weather_fc` `past_days`, and the plan verified that those values equal historical-forecast for past days. So recent test rows also using them changes no value. |
| D29 | `test_backfill_empty_dir` asserts five files with `MRMS_ENABLED=1`, and a second test asserts four with `MRMS_ENABLED=0`. | Keep the plan's "four files". | Student (accepted the Builder's recommendation, Builder Q3) | The "four files" in the test list was written before MRMS and previous-runs were added; AC-1.1 says five. |
| D30 | `make install` runs `pip install -r requirements.txt -r requirements-dev.txt -e .`. | `requirements-dev.txt` includes `-r requirements.txt`. | Student (accepted the Builder's recommendation, Builder Q4) | AC-0.10 requires every line to be an exact pin, so the dev file can't include the runtime file. |
| D31 | AC-1.9 completeness rule: a previous-runs local date is complete when all 24 of its Open-Meteo hourly values are present and non-null. This replaces "23, 24, or 25 hours on DST-change days". | Request `timezone=GMT` and aggregate to true local days with 23/25-hour DST days (differs from how Open-Meteo builds the daily sums used everywhere else). | Student (accepted the Builder's recommendation after Stage 1) | Verified 2026-09-24: Open-Meteo applies one fixed UTC offset per response, so every local date, DST days included, has exactly 24 hours. The rule keeps lead-1/lead-2 rain on the same day boundaries as historical-forecast and forecast daily sums. |
| D32 | AC-2.5 lag-0 check: on the first day flow rises more than 10x (2025-07-06), `precip_0` is at least 25 mm and that day is the window's wettest. This replaces "the first day with at least 25 mm equals the first 10x rise". All exact values in AC-2.5 are still asserted. | Keep the wording and search from 2025-07-04 (B); re-record the fixture from 07-04 (C). | Student (accepted the Builder's recommendation after Stage 2) | The literal rule is false on the recorded data: 2025-07-02 had 35.7 mm (MRMS 39.6) with a same-day 2.8x rise on dry soil, so the first 25 mm day is 07-02. The replacement tests the same claim from the flow side. The GMT control still fails it (7.9 mm on the jump day; wettest day 07-07). B and C would pick a window after seeing the data. |
| D33 | Dashboard chart (amends the Stage 5 "linear axis by default" note and extends AC-5.2): flow is drawn on a **log y-axis** (`cfs (log scale)`), with values below 0.1 cfs drawn at 0.1 and tooltips showing true values. Daily **rain hangs upside down from the top of the same plot** (a hyetograph on its own reversed right-hand axis running to 3x the wettest day, so bars fill the top third; no horizontal gridlines): Open-Meteo daily rain (`precip_0`) for the 60 days, plus the forecast rain the model used (`precip_f1..f3` of the live features row) for d0+1..d0+3. Both come from the features file, so AC-5.1's allowed files are unchanged. | Linear axis with an optional log toggle (the plan's default); no rain panel. | Student (requested at the Stage 5 smoke test) | Flow spans 7 to 200+ cfs in the 60-day window, so on a linear axis the current low flow and the forecast fan were squashed against the bottom. Showing the rain next to the flow explains the rises, and shows the forecast rain the bands depend on (RK3). |
| D34 | The first dashboard KPI tile reads "t+1 median (<valid date>)" instead of "Tomorrow's median (<valid date>)". This amends AC-5.4's wording "tomorrow's median (t+1)". | Keep "Tomorrow's median"; "Today's median". | Student (Gate 3, Tester finding TF4) | Under D4, t+1 is today on a normal run (d0 = yesterday), so "tomorrow" was wrong on almost every day it was shown. "t+1" matches the plan's notation, and the date in parentheses says which day. |
| D35 | Accept the real-data under-coverage on flood and wet days (Tester finding TF2) under D23 step 4, and close it. High-flow 80% band coverage is 0.14–0.25 (95% band 0.64–0.79, n = 28), and wet-window 80% band coverage is 0.39–0.54 (h = 1..3, test split, model 20260925T003936Z). No retuning; the D19 tolerances are unchanged. | Try D23's one allowed alternative; widen the tolerances or recalibrate on test (both forbidden by AC-3.9). | Student (Gate 3) | Every AC-3.9 gate passes (overall coverage 0.80–0.84 / 0.96). The misses are the known limitation in RK6 (335 calibration days with 6 days at or above the 99th percentile), made worse by forecast-rain error on wet days (RK3c). The dashboard's RK6 caption already tells users the bands are too narrow during storms. |

### Revisit later

Items the student deliberately parked. They are not blockers for the build. Re-examine after the Tester's review, once real metrics exist.

| ID | Item | Why it was parked | What would trigger a change |
|---|---|---|---|
| RV1 | Wet/dry cutoffs in AC-3.10 (wet ≥ 10 mm, dry < 2 mm over d0+1..d0+h; D25). | Chosen by judgment, not data. On 2025 MRMS they give t+1: 39 wet / 284 dry / 41 light, t+2: 75 / 235 / 53, t+3: 105 / 193 / 64. The wet group mixes real events with rain on dry soil that barely moves the river. | Wet-group coverage or skill that is too noisy to read (fewer than about 40 wet rows), or a clear case that a different cutoff (e.g. 1 / 25 mm, or antecedent-wetness-aware labels) separates events better. The choice must be made without looking at test-period skill for each candidate cutoff. |
| RV2 | Per-horizon rain inputs that would allow partial 1–2-row forecasts (D22). | Kept simple: truncated Open-Meteo responses are rare. | Truncated responses happen in practice (AC-4.7 refusals in the logs), or D23 selects it as the one allowed alternative. |

### Environment and commands

- **Python:** 3.12 (Docker, CI). Local ≥ 3.12 (the developer machine has 3.13.12).
- **Shell:** Git Bash on Windows, or any POSIX shell. GNU Make ≥ 4. Docker Engine with Compose v2.
- **Dependencies:** `requirements.txt` (runtime) and `requirements-dev.txt` (test/lint), with exact `==` pins chosen by the Builder at Stage 0 and recorded in its Implementation notes. Runtime: pandas, numpy, scikit-learn, joblib, requests, streamlit (which brings Altair). Dev: pytest, responses, pytest-socket, black, flake8.
- **Install:**
  ```bash
  python -m venv .venv
  source .venv/Scripts/activate   # Git Bash on Windows; use .venv/bin/activate on Linux/macOS
  make install
  ```
- **Environment variables** (all optional; defaults in `streamforecast/config.py`, documented in `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `./data` (container: `/data`) | Root for raw/, features/, models/, forecasts/ |
| `USGS_SITE` | `02085000` | Gauge number |
| `LATITUDE` / `LONGITUDE` | `36.0711` / `-79.0956` | Weather point |
| `TIMEZONE` | `America/New_York` | Calendar-day definition |
| `HISTORY_START` | `2018-01-01` | First date for backfill (D18) |
| `TRAIN_END` | `2023-12-31` | Last target date in train (D20) |
| `CALIBRATION_START` | `2024-02-01` | First issue date in calibration; also where lead-matched rain begins (D20) |
| `CALIBRATION_END` | `2024-12-31` | Last target date in calibration; test follows |
| `LEADS_START` | `2024-01-25` | First date requested from previous-runs (a few days of margin before `CALIBRATION_START`) |
| `USGS_LOOKBACK_DAYS` | `30` | Re-fetch window for provisional revisions |
| `MAX_FFILL_DAYS` | `2` | Longest flow gap forward-filled |
| `MAX_STALE_DAYS` | `2` | Forecast refuses if `stale_days = (local today − 1) − d0` exceeds this (D21) |
| `RUN_INTERVAL_HOURS` | `24` | Scheduler period |
| `RETRAIN_DAYS` | `7` | Retrain when the newest checkpoint is older than this |
| `INGEST_SOURCE` | `live` | `live` or `fixtures` |
| `MRMS_ENABLED` | `1` | Set `0` to skip the MRMS verification source |
| `USGS_API_KEY` | empty | Sent as a request header if set (the Builder verifies the header name in the api.waterdata.usgs.gov docs) |
| `HTTP_TIMEOUT_S` / `HTTP_RETRIES` | `30` / `3` | HTTP behaviour |
| `LOG_LEVEL` | `INFO` | Logging level |
| `AS_OF_DATE` | empty | `YYYY-MM-DD`. If set, `paths.local_today()` returns it instead of the real date. Required with `INGEST_SOURCE=fixtures`; used by tests, CI, and fixture smoke tests. Never set in production (D26). |
| `DASHBOARD_PORT` | `8501` | Port used by `make dashboard` |

- **Make targets** (the public interface):

| Target | Does |
|---|---|
| `make help` | Lists targets with one-line descriptions |
| `make install` | `pip install -r requirements-dev.txt -e .` into the active environment |
| `make config` | Prints the resolved configuration (shows env overrides) |
| `make format` | `black .` |
| `make lint` | `black --check .` and `flake8` |
| `make test` | All offline tests (`pytest -m "unit or regression or integration"`) with sockets disabled |
| `make test-unit` / `make test-regression` / `make test-integration` | Tests for one marker |
| `make ingest` / `make features` / `make train` / `make forecast` | Run one stage once |
| `make coverage` | Prints per-horizon interval coverage from the newest metrics sidecar. Exits non-zero if outside the D19 tolerances (AC-3.9). This is a report: a real-data failure is a finding handled under D23, not a broken build. It is not part of `make test` or CI. |
| `make pipeline` | `python -m streamforecast.scheduler --once`: ingest → features → train (if needed) → forecast, one pass (built in Stage 6) |
| `make dashboard` | `streamlit run streamforecast/dashboard.py --server.port $(DASHBOARD_PORT)` |
| `make docker-build` / `make up` / `make down` / `make logs` | Compose wrappers |
| `make clean` | Removes caches (`__pycache__`, `.pytest_cache`); **never** touches `data/` |

- **Log line format** (every stage, used by the smoke tests): `2026-09-23T14:00:00Z INFO ingest usgs: fetched 7 rows 2026-09-16..2026-09-22 -> data/raw/usgs/usgs_20260923T140000Z.csv`

### Verified API facts (2026-09-23, for the Builder)

- **USGS (new API):** `features[].properties` has `time` (YYYY-MM-DD, local date), `value` (**string**), `unit_of_measure` (`ft^3/s`), `approval_status` (`Approved` / `Provisional`), `qualifier` (null or a list, e.g. `["ESTIMATED"]`), `statistic_id`, `last_modified`, `monitoring_location_id` (`USGS-02085000`). Rows come back **unordered**. Without `statistic_id=00003` the response mixes min/max/mean. Counts for 2006-01-01..2026-09-22: 7,568 rows over 7,570 days (real gaps exist); Approved 7,527, Provisional 41; qualifier null 7,441, `["ESTIMATED"]` 127; min 0.22 cfs, max 8,180 cfs on 2025-07-07; no zeros or negatives. The newest value was 2026-09-22 (today not yet posted). **Time basis:** `time` is a plain local calendar date with no timezone. The site record (`monitoring-locations`, `USGS-02085000`) has `time_zone_abbreviation: "EST"` and `uses_daylight_savings: "Y"`. The daily collection's documentation doesn't say whether the day boundary follows standard or clock time; at most that's a 1-hour difference in summer, negligible for daily means. Treat `time` as an America/New_York date and never convert it.
- **Open-Meteo forecast:** `https://api.open-meteo.com/v1/forecast?latitude=36.0711&longitude=-79.0956&daily=precipitation_sum,temperature_2m_max,temperature_2m_min&forecast_days=3&past_days=7&timezone=America%2FNew_York`. Response `daily.time`, `daily.precipitation_sum` (mm), `daily.temperature_2m_max/min` (°C), `daily_units`, `utc_offset_seconds` (-14400 in EDT). **Forecast day 1 is today.** It snaps to the grid cell (36.072, -79.109).
- **Open-Meteo historical-forecast:** `https://historical-forecast-api.open-meteo.com/v1/forecast` with the same daily variables plus `start_date` and `end_date`. It accepts dates from 2016-01-01 (an earlier `start_date` returns `{"error":true,"reason":"Parameter 'start_date' is out of allowed range from 2016-01-01 to ..."}`), **but all values are null until 2017-12-31** (RK17). One request for 2016-01-01..2026-09-22 returned 3,918 days (109 KB) in a single response, so no chunking is needed. Same grid cell and values as the forecast endpoint's `past_days`.
- **Open-Meteo previous-runs (adopted in D20):** `https://previous-runs-api.open-meteo.com/v1/forecast?...&hourly=precipitation,precipitation_previous_day1,precipitation_previous_day2&timezone=America%2FNew_York`. **Hourly only**: `daily=precipitation_sum_previous_day1` returns `{"error":true,"reason":"Invalid value: ... precipitation_sum_previous_day1"}`. Same grid cell as the forecast endpoint. The first non-null values are `previous_day1` 2024-01-19T08:00, `previous_day2` 2024-01-20T08:00, `previous_day3` 2024-01-21T08:00, so all of 2024 from February onward and all of 2025–2026 are covered. On 2025-07-06 (Chantal), `previous_day1` = 87.8 mm and `previous_day2` = 5.8 mm.
- **NOAA MRMS via IEM (D24):** `https://mesonet.agron.iastate.edu/iemre/multiday/2025-01-01/2025-12-31/36.0711/-79.0956/json` returns `{"iemre_domain":"conus","iemre_i":375,"iemre_j":105,"data":[{"date":"2025-07-06","mrms_precip_in":7.01,"prism_precip_in":...,...}]}`. `mrms_precip_in` is in **inches**. Days follow Central Standard Time (RK18b). One year per request (RK18a). Checked values: 2025-07-05 0.0, **07-06 7.01**, 07-07 0.08, 07-09 1.55, 07-10 0.35 in; 2026-09-18 **0.0** (Open-Meteo lead-0 said 51.5 mm); 2026-09-21 1.66 in (≈42 mm; Open-Meteo 47.2 mm). NOAA's own archive (`https://noaa-mrms-pds.s3.amazonaws.com/CONUS/MultiSensor_QPE_24H_Pass2_00.00/YYYYMMDD/`) has 24 hourly GRIB2 files per day of about 4.5 MB each, starting 2020-10-14; not used (D24).
- **Open-Meteo archive (not used):** the default model fills through **today** (a partial day); with `models=era5` it is null after about 6 days. Different grid cells again. This is why D2 and RK9 exist.

---

## Stage 0 — Skeleton

### Goal
A runnable, lintable, testable empty package: config from env vars, path helpers, logging, the Makefile, pytest markers, black + flake8, and a GitHub Actions CI that runs offline.

### Acceptance criteria
- [x] AC-0.1 `make install` then `make lint` exits 0. `make lint` runs both `black --check` and `flake8`.
- [x] AC-0.2 `make test` runs pytest with the markers `unit`, `regression`, `integration` registered (`--strict-markers`) and sockets disabled (`--disable-socket`). A `pytest_collection_modifyitems` hook in `tests/conftest.py` raises a collection error for any test that doesn't carry exactly one of the three markers. A network call fails the run.
- [x] AC-0.3 `config.load()` returns every variable in the Environment table with its documented default, and an env var overrides each one (unit test).
- [x] AC-0.4 `paths` helpers return `DATA_DIR/raw/usgs`, `raw/weather_hist`, `raw/weather_fc`, `raw/weather_leads`, `raw/mrms`, `features`, `models`, `forecasts`, create them on demand, and produce UTC-timestamped file names that sort chronologically. `newest(dir, pattern)` returns the lexicographically last match or `None`.
- [x] AC-0.5 A unit test parses every stage module (`ingest`, `features`, `train`, `forecast`, `dashboard`) and the `scheduler` with `ast`, and fails if any of them imports a stage module (only `config`, `paths`, `logs` are shared). Modules that don't exist yet are skipped, so the test grows with the build.
- [x] AC-0.8 `paths.local_today()` returns `AS_OF_DATE` when that is set, otherwise `datetime.now(ZoneInfo(TIMEZONE)).date()`. With time frozen at 2026-09-23 23:30 America/New_York (03:30 UTC on 9/24) and `AS_OF_DATE` unset, it returns 2026-09-23. With `AS_OF_DATE=2026-09-23` it returns 2026-09-23 on any real date. No module calls `date.today()` (grep in the test). Calendar-date logic (today, yesterday, staleness, fetch dates) goes through `local_today()`. Real UTC timestamps for log lines, file names, and `created_at_utc` use `paths.utc_now()` and are not affected by `AS_OF_DATE`.
- [x] AC-0.9 **Atomic writes everywhere.** `paths.atomic_write(path, write_fn)` writes to `<path>.tmp` in the same directory and then calls `os.replace`. Every stage uses it for every published file (raw CSVs, features, rain check, MRMS verification file, checkpoint, sidecar, forecast). A glob for any published pattern never matches a `.tmp` file.
- [x] AC-0.10 Every line of `requirements.txt` and `requirements-dev.txt` is an exact `name==version` pin (a test parses them).
- [ ] AC-0.6 *(Tester, Gate 3: unticked, TF3; no GitHub Actions run yet)* `.github/workflows/ci.yml` runs on push and PR, sets up Python 3.12, and runs `make install`, `make lint`, `make test`.
- [x] AC-0.7 `make help` lists every target in the Environment section. `make config` prints the resolved config, and `DATA_DIR=/tmp/sf make config` shows the override.

### Proposed changes
- `pyproject.toml`: package metadata, black config, pytest config (markers, `addopts = --strict-markers --disable-socket`).
- `setup.cfg` (or `.flake8`): flake8 config (max-line-length 88, exclude `.venv`, `data`).
- `requirements.txt`, `requirements-dev.txt`: exact pins.
- `streamforecast/__init__.py`: package marker and version.
- `streamforecast/config.py`: frozen dataclass `Settings` plus `load()` reading env vars.
- `streamforecast/paths.py`: directory helpers, `timestamp()`, `newest()`, `local_today()`.
- `streamforecast/logs.py`: `get_logger(stage)` with the UTC log format above.
- `streamforecast/__main__.py`: `python -m streamforecast config` prints the settings (used by `make config`).
- `Makefile`: all targets (stage targets may fail with "not implemented" until their stage lands).
- `.env.example`: every variable with its default.
- `.gitignore`: `data/`, `.venv/`, caches, `*.joblib`.
- `tests/conftest.py`: a `tmp_data_dir` fixture setting `DATA_DIR` to `tmp_path`.
- `tests/unit/test_config.py`, `tests/unit/test_paths.py`, `tests/unit/test_boundaries.py`.
- `.github/workflows/ci.yml`: offline CI.
- `README.md`: minimal install/run/test section (the student extends it at Gate 3).

### Architecture / boundaries
- May read: environment variables.
- May write: directories under `DATA_DIR` (created on demand).
- Shared modules (`config`, `paths`, `logs`) must never import any stage module or any third-party ML/HTTP library.

### Risks for this stage
- RK15 (Windows `make`/venv differences): the Makefile uses `$(PYTHON)` and never `.venv/bin`.
- pytest-socket may block the local socket that Streamlit's `AppTest` uses. If so, the Builder allows `unix`/localhost only for that test via `@pytest.mark.enable_socket`, records it, and never allows external hosts.

### Automated tests
- unit `test_config.py`: defaults and overrides → AC-0.3.
- unit `test_paths.py`: dirs, timestamp ordering, `newest()` → AC-0.4.
- unit `test_boundaries.py`: AST import rule → AC-0.5.
- unit `test_network_blocked.py`: `requests.get("https://example.com")` raises the pytest-socket error → AC-0.2.
- unit `test_marker_hook.py`: uses `pytester` to show an unmarked test causes a collection error → AC-0.2.
- unit `test_local_today.py`: frozen clock at 23:30 ET (monkeypatched `datetime`), the `AS_OF_DATE` override (and that `utc_now()` ignores it), and a grep for `date.today(` → AC-0.8.
- unit `test_atomic_write.py`: an exception inside `write_fn` leaves no target file and no stray `.tmp`; a success leaves exactly the target → AC-0.9.
- unit `test_requirements_pinned.py` → AC-0.10.
- AC-0.1, AC-0.6, AC-0.7 are checked by commands (see the smoke test) and by CI.

### Manual Smoke Test
#### What we're proving
The project installs, the Makefile is the entry point, configuration really comes from the environment, and lint runs.
#### Terminal
Terminal 1 (Git Bash, in the repo root):
```bash
python -m venv .venv
source .venv/Scripts/activate
make install
make help
make config
DATA_DIR=/tmp/sf-smoke USGS_SITE=99999999 make config
make lint
```
#### Watch for
- `make help` prints each target with a description.
- The first `make config` shows `DATA_DIR=./data`, `USGS_SITE=02085000`, `TIMEZONE=America/New_York`.
- The second shows `DATA_DIR=/tmp/sf-smoke` and `USGS_SITE=99999999`.
- `make lint` prints black's "would be left unchanged" line and flake8 prints nothing. Exit status 0 (`echo $?` prints `0`).
#### Stop
Nothing is left running. `deactivate` to leave the venv.

### Implementation notes

**Built (Builder, 2026-09-24).**
- `pyproject.toml`: setuptools package `streamforecast` (Python ≥ 3.12), black config (88, py312), and pytest config: `addopts = --strict-markers --disable-socket -p pytester`, with the three markers registered. `.flake8`: max-line-length 88, `extend-ignore = E203` (black compatibility), excludes `.venv` and `data`.
- `streamforecast/config.py`: frozen `Settings` dataclass. Each field's env var is its name in upper case. `load()` parses by field type (str, int, float, date, `0`/`1` bool) and raises `ValueError` naming the variable on bad input. An **empty variable counts as unset**, so Compose's `${VAR:-}` works. `INGEST_SOURCE` must be `live` or `fixtures`. `as_env()` renders settings as `NAME=value`.
- `streamforecast/paths.py`: `subdir(name)` for the 8 directories in AC-0.4, created on demand. Also `utc_now()`, `local_today()`, `timestamp()` (`YYYYMMDDTHHMMSSZ`, UTC), `timestamped_name(prefix, suffix)`, `newest(dir, pattern)`, and `atomic_write(path, write_fn)`. `write_fn` receives the `<path>.tmp` path; on any exception the tmp file is removed and the target is left untouched.
- `streamforecast/logs.py`: `get_logger(stage)` writes to stderr in the plan's format (`%Y-%m-%dT%H:%M:%SZ LEVEL stage message`, UTC), at the level set by `LOG_LEVEL`.
- `streamforecast/__main__.py`: `python -m streamforecast config` prints every setting; it exits 1 with the variable name on an invalid value.
- `Makefile`: every target in the Environment table, each with a `## ` description that `make help` prints (awk). Uses `PYTHON ?= python` and never `.venv` paths. Stage targets fail with "No module named ..." until their stage is built. `clean` uses a Python one-liner, so it works in any shell and never touches `data/`.
- `tests/conftest.py`: the marker hook (a test without exactly one of `unit`/`regression`/`integration` raises `pytest.UsageError`, so pytest exits 4 before running anything), the `tmp_data_dir` fixture, and an autouse fixture that blocks `socket.getaddrinfo` (see deviation 3).
- Tests (all `unit`): `test_config.py`, `test_paths.py`, `test_boundaries.py`, `test_network_blocked.py`, `test_marker_hook.py` (pytester), `test_local_today.py`, `test_atomic_write.py`, `test_requirements_pinned.py`, and `test_makefile_and_cli.py` (AC-0.7 help lines and the `config` output, plus no hard-coded venv paths).
- `.env.example`, `.gitignore`, `.github/workflows/ci.yml` (ubuntu-latest, Python 3.12, pip cache, `make install` → `make lint` → `make test`), and a minimal `README.md`.

**Pinned versions** (resolved 2026-09-24 as the latest releases; every one supports Python 3.12; the strictest `Requires-Python` is numpy's `>=3.12`): runtime `joblib==1.6.0`, `numpy==2.5.3`, `pandas==3.0.6`, `requests==2.34.2`, `scikit-learn==1.9.1`, `streamlit==1.64.0` (brings `altair==6.3.0`), `tzdata==2026.4`; dev `black==26.5.1`, `flake8==7.4.1`, `pytest==9.1.1`, `pytest-socket==0.8.1`, `responses==0.26.3`. Only direct dependencies are pinned; transitive ones resolve at install time.

**Deviations (pending the student's confirmation; raised as Q4/Q5 before the build):**
1. `make install` runs `pip install -r requirements.txt -r requirements-dev.txt -e .`, not only `requirements-dev.txt`. Otherwise the dev file would need a `-r requirements.txt` line, which AC-0.10 forbids.
2. Added `tzdata` to `requirements.txt`, which the plan doesn't list. `zoneinfo` has no time-zone database on Windows or in `python:3.12-slim` without it, and `local_today()` needs `America/New_York`.
3. `--disable-socket` blocks socket creation, but DNS resolution (`getaddrinfo`) happens first and would reach the network, and would fail differently when offline. `tests/conftest.py` therefore also blocks `socket.getaddrinfo` with `SocketBlockedError`. `test_network_blocked.py` covers `requests.get("https://example.com")`, a raw socket, and a DNS lookup.
4. The `date.today(` grep (AC-0.8) scans `streamforecast/` **and** `tests/` (the board's "grep the whole codebase"). It also catches `datetime.today(`.
5. Shared-module check (Stage 0 boundaries): `test_boundaries.py` also asserts that `config`, `paths`, and `logs` import no stage module and none of requests, sklearn, pandas, numpy, joblib, or streamlit. The six stage-module tests are skipped until those modules exist.

**Verification.**
- AC-0.1: `make install` exit 0; `make lint` exit 0 (black: 15 files unchanged; flake8 silent).
- AC-0.2 to AC-0.10: `make test` gives 91 passed and 6 skipped (the stage modules aren't built yet).
- AC-0.2, extra manual check: a temporary unmarked test under `tests/unit/` made `make test` exit 4 with nothing run. The file was then deleted.
- AC-0.7: `make help` and `make config` were run and show the expected output.
- AC-0.6: **ticked at Stage 6.** CI's exact sequence (`make install`, then `make lint`, then `make test`: 366 passed) ran on Python 3.12.14 in the `python:3.12-slim` image, all exit 0 (Stage 6 notes); a GitHub Actions run is still pending because nothing is pushed. *Earlier note:* not verified, left unticked. The workflow file matches the criterion, but it hasn't run: nothing is pushed to GitHub, and a local run in `python:3.12-slim` failed because the Docker daemon wasn't running (`failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`). So the pins' Python 3.12 support comes only from package metadata. Tick it after the first green GitHub Actions run.
- Mechanism note (AC-0.2): the marker hook raises `pytest.UsageError`, so pytest prints `ERROR: each test needs exactly one of the markers ...` and exits 4 (usage error) before running any test. That is a collection-time failure, but not pytest's "ERROR collecting" (exit 2).
- Stage 5 follow-up: the autouse DNS block in `tests/conftest.py` also applies to tests marked `@pytest.mark.enable_socket`. If Streamlit `AppTest` needs localhost resolution, that fixture must skip tests carrying that marker.

**Fix found at Gate 2 (2026-09-24).** Run from the VS Code PowerShell terminal, `make help` failed with `process_begin: CreateProcess(NULL, awk ...) failed`. Without Git Bash on PATH, make starts recipes directly and there is no `awk`. The fix:
- `help` now parses the Makefile with `$(PYTHON) -c ...`, and every recipe uses only `$(PYTHON)` or `docker`.
- The new test `test_recipes_use_no_unix_only_tools` fails if a recipe starts with awk, sed, grep, find, rm, cp, mv, cat, ls, mkdir, touch, or test.
- `make help` and `make clean` were verified in both PowerShell and Git Bash.

In PowerShell, activate with `.\.venv\Scripts\Activate.ps1`, and set overrides with `$env:DATA_DIR='/tmp/sf-smoke'; make config` (the `VAR=x make` form is Bash-only). A second environment, `venv/`, appeared in the repo root during Gate 2 (not created by the Builder). `.flake8` and `.gitignore` now exclude `venv/` as well as `.venv/`, because flake8 otherwise linted its site-packages.

**Windows note for the smoke test.** Git Bash rewrites a leading `/tmp/...` in an env var into a Windows path before Python sees it (MSYS path conversion). So `DATA_DIR=/tmp/sf-smoke make config` prints `DATA_DIR=C:/Users/<you>/AppData/Local/Temp/sf-smoke`. It is the same directory, and the override works. On Linux, macOS, and CI it prints `/tmp/sf-smoke` exactly. The command is unchanged.

### Review findings

See the Tester review under Stage 6: TF3, TF5, TF6, TF11, TF15.

**Setup check (Tester, 2026-09-25).** I followed the README from a copy of the working tree at a short path (`C:/Users/Grace/sfcheck`), in a fresh venv, in Git Bash. Every command succeeded:
- `python -m venv .venv` and `source .venv/Scripts/activate` → exit 0.
- `make install` → exit 0 (1 min 50 s).
- `make help` and `make config` → exit 0.
- `make lint` → exit 0.
- `make test` → 389 passed, 6 xfailed.
- `make pipeline` (live) → exit 0 in 23 s. Summary line: `ingest=0 features=0 train=0 forecast=0`.
- `make dashboard` → `/_stcore/health` returned `ok` and the page returned HTTP 200. A headless render raised no exception.
- The PowerShell path (`Activate.ps1`, then `make config` / `make help`) → exit 0. This shell runs with ExecutionPolicy `Bypass`, so a default-policy machine was not tested.
- The earlier fresh-venv failure was caused by Windows' path-length limit in the Tester's scratch folder, not by the project.

The gaps a new user would hit:

| ID | Severity | What's wrong | Evidence | Recommended fix |
|---|---|---|---|---|
| TF17 | Major | A new user cannot get the code. There is no git remote, only 4 files are tracked, and all the code is untracked. The README has no clone step. | `git remote -v` → empty. `git ls-files` → `.claude/skills/dev-cycle/SKILL.md`, `docs/gi23_architect.txt`, `docs/plan.md`, `docs/streamforcast board content.md`. `git status` shows `?? streamforecast/`, `?? tests/`, `?? Makefile`, … | Commit on a branch, push to GitHub, and add `git clone …` plus `cd …` to the README. This also unblocks AC-0.6 (TF3). |
| TF18 | Minor | The README requires GNU Make but doesn't say how to get it on Windows, and Git for Windows doesn't include it. | On this machine, `which make` → `…/WinGet/Packages/ezwinports.make_…/bin/make` (GNU Make 4.4.1), installed separately. | Add a prerequisite line: `winget install ezwinports.make` (or `choco install make`). |
| TF19 | Minor | The README doesn't list the runtime prerequisites. `make pipeline` and the first `docker compose up` need network access, and Docker Desktop must be running. | The first `docker info` failed with `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine` until Docker Desktop was started. | Add "network access" and "Docker Desktop running" to Install/Containers. |
| TF20 | Nit | The README example `DATA_DIR=/tmp/sf make config` prints a Windows path in Git Bash, because MSYS rewrites paths. This is explained in the plan but not the README. | Output: `DATA_DIR=C:/Users/Grace/AppData/Local/Temp/sf`. | Add a one-line note, or use a relative example (`DATA_DIR=./tmp-sf`). |

---

## Stage 1 — Ingest

### Goal
Fetch USGS daily mean discharge, Open-Meteo historical-forecast weather (past), Open-Meteo previous-runs lead-1 and lead-2 precipitation (past, from 2024; D20), and the Open-Meteo 3-day forecast (plus 7 past days), incrementally, and write normalized timestamped CSVs to `data/raw/`.

### Acceptance criteria
- [x] AC-1.1 On an empty `DATA_DIR`, `make ingest` backfills USGS and historical-forecast weather from `HISTORY_START`, and previous-runs precipitation from `LEADS_START`, through yesterday (America/New_York), and writes one forecast file, plus MRMS from `HISTORY_START` when `MRMS_ENABLED=1`. That is five new files: `raw/usgs/usgs_<ts>.csv`, `raw/weather_hist/weather_hist_<ts>.csv`, `raw/weather_leads/weather_leads_<ts>.csv`, `raw/weather_fc/weather_fc_<ts>.csv`, `raw/mrms/mrms_<ts>.csv`.
- [x] AC-1.2 On a second run, the USGS request starts at `max(existing date) − USGS_LOOKBACK_DAYS`, and the historical-forecast and previous-runs requests start at `max(existing date) + 1`. When nothing is new for a source, no request is made and no file is written for it. The log states each requested range.
- [x] AC-1.3 USGS CSV columns: `date, flow_cfs, approval_status, qualifier, last_modified`. Sorted ascending by date even when the API returns rows unordered. `flow_cfs` is float. `qualifier` is a `;`-joined string or empty. Only `statistic_id=00003` is requested.
- [x] AC-1.4 Weather CSV columns: `date, precip_mm, tmax_c, tmin_c, source, fetched_at_utc`, with `source ∈ {historical_forecast, forecast}`. The forecast file holds the 7 past days plus 3 forecast days. If the forecast endpoint returns fewer than 3 future days (today onward) or null values, the file is still written with what came back, and a WARNING `forecast returned N of 3 days` is logged (Forecast decides; AC-4.7). Every request uses `timezone=America/New_York`.
- [x] AC-1.5 All HTTP goes through one function with a timeout, `HTTP_RETRIES` attempts, and exponential backoff on 429/5xx/connection errors. A `Retry-After` header is honoured, capped at 60 s.
- [x] AC-1.6 *(Tester: Partial at Gate 3 (TF1); fixed and re-verified 2026-09-28, see Stage 6 Review findings)* On final failure (HTTP error, Open-Meteo `{"error": true}` body, or malformed JSON), ingest logs an ERROR naming the source and reason, writes no partial file for that source, still attempts the other sources, and exits non-zero. An empty USGS response (`features: []`) is logged as WARNING, writes no file, and is not a crash.
- [x] AC-1.7 `INGEST_SOURCE=fixtures AS_OF_DATE=2026-09-23 make ingest` produces the same five file types from `tests/fixtures/` with no network access. In fixture mode, `fetched_at_utc` is stamped as 12:00 America/New_York on `AS_OF_DATE`, so fixture-mode data look fresh to Forecast on that date regardless of the real date. Fixture mode without `AS_OF_DATE` exits 1 with a clear error.
- [x] AC-1.11 *(Tester: Partial at Gate 3 (TF1); fixed and re-verified 2026-09-28, see Stage 6 Review findings)* **MRMS (verification source, D24).**
  - Fetched from IEM `iemre/multiday` in one calendar-year chunk per request.
  - Incremental: a year that is already complete isn't re-requested, and the current year is re-requested from `max(existing date) − 7 days`.
  - Written to `raw/mrms/mrms_<ts>.csv` with columns `date, precip_mm, source, fetched_at_utc`, where `precip_mm = mrms_precip_in × 25.4` and `source = iem_mrms`.
  - A chunk whose `mrms_precip_in` values are all null is treated as a failed request (RK18a).
  - Any MRMS failure is logged as an ERROR for that source only: the other sources are still written, and **it does not change ingest's exit status**. Verification data must never block forecasting.
  - `MRMS_ENABLED=0` skips it entirely.
- [x] AC-1.10 **Timezone pinning.** Every Open-Meteo request (forecast, historical-forecast, previous-runs) includes `timezone=America/New_York`, taken from `TIMEZONE`. Ingest checks that the response's `timezone` field equals the requested value; on a mismatch it logs an ERROR `timezone mismatch: requested America/New_York, got <tz>` and writes no file for that source. USGS `time` and Open-Meteo `daily.time` are written verbatim as `YYYY-MM-DD` strings: never parsed as timestamps, never localized, never converted to UTC.
- [x] AC-1.9 Previous-runs CSV `raw/weather_leads/weather_leads_<ts>.csv` has columns `date, lead_days, precip_mm, n_hours, fetched_at_utc`, with `lead_days ∈ {1, 2}`, from hourly `precipitation_previous_day1` and `precipitation_previous_day2` requested with `timezone=America/New_York`. `precip_mm` is the sum of the hourly values on that local date. It is written only when every hour of that date is present and non-null (`n_hours` equals 23, 24, or 25 on DST-change days, as appropriate); otherwise it is NaN, and a WARNING counts the incomplete days.
- [x] AC-1.8 Files are written atomically with `paths.atomic_write` (AC-0.9), so a crash never leaves a half-written CSV that matches the pattern.
- [x] AC-1.12 *(Tester: Partial at Gate 3 (TF1); fixed and re-verified 2026-09-28, see Stage 6 Review findings)* **Response validation (D26).** Before writing, ingest checks each response; a failure is handled as a source failure (AC-1.6).
  - **Units:**
    - Open-Meteo `daily_units` must be `mm` for precipitation and `°C` for temperatures.
    - Previous-runs `hourly_units` must be `mm`.
    - Every USGS row must have `unit_of_measure == "ft^3/s"`, `statistic_id == "00003"`, and `monitoring_location_id == "USGS-" + USGS_SITE`. Non-matching USGS rows are dropped and counted, with an ERROR if any are dropped.
  - **Shape:** every data array has the same length as `time`.
  - **Date coverage:** returned dates are contiguous and cover the requested range. Missing dates are listed in a WARNING and the file is still written.
  - **Grid cell:** weather CSVs also record `grid_lat`, `grid_lon`, `elevation` from the response. Features logs a WARNING if the grid cell differs between files.
  - **Pagination:** USGS follows every `next` link until none remains (not only when `numberReturned == 10000`).

### Proposed changes
- `streamforecast/ingest.py`: `fetch_usgs`, `fetch_weather_hist`, `fetch_weather_leads`, `fetch_weather_forecast`, `http_get_json` (retry), `main()` (`python -m streamforecast.ingest`).
- `tests/fixtures/openmeteo_previous_runs.json`: a real hourly previous-runs response (`precipitation_previous_day1`, `precipitation_previous_day2`) for a few days that includes 2026-09-18 and 2026-09-21, plus one hand-edited copy with a null hour and one spanning a DST change (the edit is noted in `SOURCES.md`).
- `tests/fixtures/usgs_daily_recent.json`: a real 10-day new-API response (unordered, Provisional).
- `tests/fixtures/usgs_daily_estimated_gap.json`: a real window containing an `["ESTIMATED"]` day and a gap (the Builder finds one in the 2006–2026 record).
- `tests/fixtures/usgs_daily_chantal.json`: the real response for 2025-06-25..2025-07-15 (peak 8,180 cfs).
- `tests/fixtures/usgs_empty.json`: `{"type":"FeatureCollection","features":[],"numberReturned":0}`.
- `tests/fixtures/openmeteo_forecast_past7.json`: a real forecast response with `past_days=7`, `forecast_days=3`.
- `tests/fixtures/openmeteo_forecast_short.json`: the same response truncated to 2 forecast days.
- `tests/fixtures/openmeteo_hist_forecast.json`: a real historical-forecast response for a short window aligned with the USGS fixtures.
- `tests/fixtures/iem_mrms_chantal.json` (IEM multiday 2025-06-25..2025-07-15) and `tests/fixtures/iem_mrms_recent.json` (2026-09-12..2026-09-22, including 09-18 = 0.0 in and 09-21 = 1.66 in), plus `iem_mrms_all_null.json` (a hand-edited copy with every `mrms_precip_in` null, noted in `SOURCES.md`).
- `tests/fixtures/openmeteo_hist_chantal_et.json` and `openmeteo_hist_chantal_gmt.json`: real historical-forecast responses for 2025-06-25..2025-07-15, requested with `timezone=America/New_York` and with `timezone=GMT` (AC-2.5). Recorded 2026-09-23: 07-06 was 54.7 mm (ET) vs 7.9 mm (GMT), and 07-07 was 15.1 vs 61.9 mm.
- `tests/fixtures/openmeteo_error.json`: `{"error": true, "reason": "..."}` (served with HTTP 429 and 400 in tests).
- `tests/fixtures/SOURCES.md`: the exact URL and date each fixture was recorded (provenance).
- `tests/unit/test_ingest_parse.py`, `tests/integration/test_ingest_run.py`.

### Architecture / boundaries
- May read: `config`, `paths`, `logs`; existing `data/raw/**` (for incremental ranges); `tests/fixtures/` only when `INGEST_SOURCE=fixtures`.
- May write: `data/raw/usgs/`, `data/raw/weather_hist/`, `data/raw/weather_leads/`, `data/raw/weather_fc/`, `data/raw/mrms/`.
- Must never import: `features`, `train`, `forecast`, `dashboard`, `scheduler`.
- Must never use the Open-Meteo archive endpoint (RK9).

### Risks for this stage
RK1, RK7, RK11, RK12, RK13. Also a first-backfill payload of about 5 MB from USGS: one request with `limit=10000` is sufficient (verified). Pagination is handled generally (AC-1.12).

### Automated tests
- unit `test_parse_usgs_sorts_and_types` (unordered fixture → sorted, float) → AC-1.3.
- unit `test_parse_usgs_qualifier_join` (ESTIMATED) → AC-1.3.
- unit `test_parse_weather_columns_and_timezone_param` (asserts the request URL contains `timezone=America%2FNew_York`) → AC-1.4.
- unit `test_retry_then_success` (`responses`: 429, 429, 200) → AC-1.5.
- unit `test_retry_after_honoured_and_capped` (`Retry-After: 5` waits 5 s; `Retry-After: 600` waits 60 s; sleep is mocked) → AC-1.5.
- unit `test_short_forecast_written_with_warning` (`openmeteo_forecast_short.json`) → AC-1.4.
- unit `test_leads_hourly_to_daily` (sums per local date; a null hour gives NaN; the DST day expects 23 or 25 hours; rain at 21:00–23:00 local on day D, which is 01:00–03:00 UTC on D+1, counts on D) → AC-1.9.
- unit `test_mrms_parse_inches_to_mm` (7.01 in → 178.054 mm) and `test_mrms_yearly_chunks` (a request spanning 2024–2025 becomes two calls) → AC-1.11.
- integration `test_mrms_failure_does_not_fail_ingest` (IEM returns HTTP 500, then the all-null fixture: ERROR logged, no MRMS file, the other files written, exit status decided only by the other sources) → AC-1.11.
- unit `test_timezone_param_on_every_openmeteo_request` (all three endpoints) and `test_timezone_mismatch_rejected` (response `"timezone":"GMT"` → ERROR, no file) → AC-1.10.
- unit `test_dates_written_verbatim` (USGS `time` and Open-Meteo `daily.time` strings appear unchanged in the CSVs; a grep test finds no `tz_localize`/`tz_convert` in ingest or features) → AC-1.10.
- integration `test_backfill_empty_dir` (mocked HTTP; four files; date ranges asserted from the request URLs) → AC-1.1.
- integration `test_incremental_second_run` (existing CSV present; asserts request start dates; no historical-forecast request when up to date) → AC-1.2.
- integration `test_rate_limit_final_failure` (always 429 → non-zero exit, ERROR logged, no USGS file, weather still written) → AC-1.6.
- integration `test_empty_usgs_warns` → AC-1.6.
- integration `test_fixture_mode` (no `responses` registration; sockets disabled) → AC-1.7.
- unit `test_atomic_write_no_partial` (simulated exception mid-write leaves no matching file) → AC-1.8.
- unit `test_validate_units_and_lengths` (wrong `daily_units`, mismatched array length, wrong `unit_of_measure`/`statistic_id` rows) and `test_date_coverage_gap_warns` → AC-1.12.
- integration `test_usgs_follows_next_links` (two-page fixture made by splitting `usgs_daily_recent.json` and adding a `next` link; all rows collected once) → AC-1.12.
- integration `test_fixture_mode_requires_as_of_date` → AC-1.7.

### Manual Smoke Test
#### What we're proving
Real data arrives from both APIs, files appear in `data/raw/`, and a second run fetches only the new or lookback days.
#### Terminal
Terminal 1:
```bash
source .venv/Scripts/activate
make ingest
ls -l data/raw/usgs data/raw/weather_hist data/raw/weather_leads data/raw/weather_fc data/raw/mrms
tail -n 3 data/raw/usgs/*.csv
grep -E '^2025-07-0[5-7]|^2026-09-18' data/raw/mrms/*.csv
grep '^2026-09-18' data/raw/weather_leads/*.csv
cat data/raw/weather_fc/*.csv
make ingest
ls -l data/raw/usgs data/raw/weather_hist data/raw/weather_leads data/raw/weather_fc
INGEST_SOURCE=fixtures AS_OF_DATE=2026-09-24 DATA_DIR=/tmp/sf-fixture make ingest
```
#### Watch for
- First run: log lines like `usgs: fetched ~3,190 rows 2018-01-01..<yesterday>` and `weather_hist: fetched ~3,190 rows`, with no null-weather WARNING. The forecast file has 10 rows whose last 3 dates are today, tomorrow, and the day after.
- The USGS tail shows yesterday's date, a small cfs value, and `Provisional`.
- The log shows `mrms: fetched ... rows` for each calendar year from 2018 through this year. The MRMS grep shows 2025-07-06 at about 178 mm (07-05 and 07-07 near 0) and 2026-09-18 at 0.0 mm.
- The `weather_leads` grep shows two rows for 2026-09-18: lead 1 about 0.7 mm and lead 2 about 4.1 mm, each with `n_hours` 24. Compare them with the same-day 51.5 mm in `weather_hist`; that gap is why D20 exists.
- Second run: the USGS request range begins about 30 days back, `weather_hist: up to date, no request` and `weather_leads: up to date, no request`, and one new USGS file and one new forecast file appear.
- Fixture run: files under `/tmp/sf-fixture/raw/...` with no network errors.
#### Stop
Nothing is left running.

### Implementation notes

**Built (Builder, 2026-09-24).**
- `streamforecast/ingest.py` has two parts:
  - `http_get_json` is the single HTTP path. It uses a timeout and `HTTP_RETRIES` attempts, backs off 1 s, 2 s, 4 s … on 429, 5xx, and connection errors, and caps `Retry-After` at 60 s. Open-Meteo `{"error": true}` bodies, other 4xx, and malformed JSON fail at once.
  - Pure parsers and validators: `parse_usgs`, `check_openmeteo`, `parse_daily_weather`, `parse_leads`, `parse_mrms`, `mrms_chunks`, `missing_dates`.
- Five sources run in order: `usgs`, `weather_hist`, `weather_leads`, `weather_fc`, `mrms`. `main()` catches `IngestError` per source, logs `ERROR <source>: <reason>; no file written`, and carries on. It exits 1 if any non-MRMS source failed.
- **Fixture mode** (`INGEST_SOURCE=fixtures`): the HTTP layer is swapped for a function that returns each source's recorded file from `tests/fixtures/` (`FIXTURE_FILES`), and the same parse, validate, and write code runs. `fetched_at_utc` is 12:00 America/New_York on `AS_OF_DATE` (`2026-09-24T16:00:00Z`).
- **Fixtures:** 17 recorded live on 2026-09-24 (12 real, 5 hand-edited), with exact URLs in `tests/fixtures/SOURCES.md`. Every value the plan quotes was reproduced: 51.5 / 0.7 / 4.1 mm on 2026-09-18; 54.7 mm (ET) vs 7.9 mm (GMT) on 2025-07-06; MRMS 7.01 in; flows 15.5 / 1990 / 8180 / 922 cfs.
- **Tests:** `tests/unit/test_ingest_parse.py` (29) and `tests/integration/test_ingest_run.py` (16). They cover every test named in this stage's list. They add Open-Meteo error bodies, malformed JSON, a 200 response with an error body, retries on 5xx and connection errors, and the `X-Api-Key` header.
- **`streamforecast/logs.py`:** the handler now writes to whatever `sys.stderr` is at emit time, so pytest's `capsys` sees log lines. The format is unchanged.

**Live check, not part of `make test`.** A backfill into a scratch `DATA_DIR` fetched:

| Source | Rows | Notes |
|---|---|---|
| usgs | 3,186 | The only WARNING is the real gap on 2026-09-09..10 |
| weather_hist | 3,188 | No null days |
| weather_leads | 1,946 | 973 days × 2 leads, none incomplete |
| weather_fc | 10 | 09-17..09-26 |
| mrms | 3,188 | 9 yearly requests |

It exited 0. An immediate second run did the following:
- USGS: requested 2026-08-24..09-23.
- weather_hist and weather_leads: logged `up to date, no request`.
- MRMS: requested 2026-09-16..09-23.
- It wrote one new file each for usgs, weather_fc, and mrms.

**Deviations and interpretations.**
1. **D27:** the fixture date is 2026-09-24, and this stage's smoke-test command is updated to match.
2. **AC-1.9, DST days: approved by the student as D31; AC-1.9 ticked.**
   - Open-Meteo applies one fixed UTC offset to a whole response: the zone's offset on the day of the request. It returns **24 hourly values for every local date**, DST days included. Verified on 2025-03-09 and 2025-11-02; a January 2025 request made today also comes back at -14400. So the "23 or 25 hours on DST days" in AC-1.9 can't occur.
   - The implementation treats a date as complete when all 24 of its hours are present and non-null. That matches how Open-Meteo builds its own daily sums, so lead and lead-0 rain share day boundaries. `test_leads_dst_day_has_24_open_meteo_hours` pins this down with the real DST fixtures.
   - **Side effect for the Tester (RK13):** day boundaries follow the offset in force when the data was requested. The 2026-09-24 backfill cuts every day at 04:00 UTC, including winter days whose true local midnight is 05:00 UTC, and an incremental fetch made in winter will cut at 05:00 UTC. That is at most a 1-hour shift, similar to the USGS standard-time ambiguity the plan already accepts.
3. **"max(existing date)" (AC-1.2)** is the latest date with a non-empty value, across every existing file of that source. A trailing null (a day not yet available) is therefore re-requested on the next run instead of being skipped forever.
4. **USGS and the forecast are fetched on every run:** USGS because of the revision lookback, and the forecast by definition. "No request when nothing is new" applies to weather_hist, weather_leads, and complete past MRMS years.
5. **Empty USGS response:** WARNING and no file. On its own it does not make the exit non-zero; AC-1.6 says only "not a crash".
6. **MRMS failures:**
   - If any yearly chunk fails, the whole source fails and no file is written, so there is never a partial file.
   - A chunk with zero rows in range logs a WARNING and is skipped. That only happens in fixture mode, whose recording covers 2026-09-12..23.
   - MRMS failures are logged at ERROR, as in AC-1.11.
7. **One timestamp per run** is shared by all five files. D28's tie rule needs it.
8. **USGS endpoint:**
   - Kept `/ogcapi/v0/` as D1 says; it returns 200 directly. Its `self` and `next` links point to `/ogcapi/v1/` and are followed exactly as given.
   - The API key goes in the `X-Api-Key` header (per api.waterdata.usgs.gov/docs/ogcapi/keys/), so it never appears in logged URLs.
9. **Grid cell:** all three weather CSVs, including weather_leads, get `grid_lat, grid_lon, elevation` (AC-1.12). Previous-runs also requests hourly `precipitation` (lead 0), as in the plan's URL, but only leads 1 and 2 are stored.
10. **Rounding:** summed and converted rain (`weather_leads`, `mrms`) is rounded to 3 decimals (7.01 in → 178.054 mm). Values from the source are otherwise untouched.
11. **Fixture-mode logs** contain expected WARNINGs: dates missing outside the fixtures' short window, and "mrms: no rows" for 2018–2025. The exit code is 0.
12. **The ESTIMATED+gap fixture is hand-edited** (see SOURCES.md), because no real window has both. **Note for Stage 2:** USGS marks **2025-07-06..18 ESTIMATED, including the 8,180 cfs peak**. AC-2.3 must keep it, since qualifier flags are kept and never dropped (D14).
13. **Note for Stage 6:** `FIXTURES_DIR` is found relative to the package source (`<repo>/tests/fixtures`). That works in the source tree and with `pip install -e .`. A plain `pip install .` in the Dockerfile would put the package in site-packages and break fixture mode (D17) in the container. Use an editable install, or copy `tests/` next to the package.

**Verification.** `make lint` exits 0. `make test`: 138 passed, 5 skipped (stage modules not built yet).

### Review findings

See the Tester review under Stage 6: TF1 (Major), TF8b, TF12.

---

## Stage 2 — Features

### Goal
Merge the raw files into one daily, validated table on America/New_York dates, and build one row per issue date d0 with lagged flow, antecedent precipitation, next-3-day precipitation, and the three log-change targets.

### Acceptance criteria
- [x] AC-2.1 Raw USGS files are combined and deduplicated by `date`, and the row from the newest file wins (so revised provisional values replace old ones). The same applies to weather, where the newest file wins per date across `weather_hist` and `weather_fc`.
- [x] AC-2.2 Impossible values (NaN, non-numeric, negative, `-999999`) are dropped, and the count is logged per reason.
- [x] AC-2.3 Extreme but real values are kept unchanged: using the Chantal fixture, the 2025-07-07 value 8,180 cfs appears in the output `flow_cfs` exactly. No clipping, winsorizing, or outlier filtering exists in the module.
- [x] AC-2.4 Gaps: flow gaps of at most `MAX_FFILL_DAYS` are forward-filled for lag inputs only (flag column `flow_filled`). Longer gaps leave NaN, and rows whose lags or target are NaN are excluded from training rows. Targets are never filled.
- [x] AC-2.5 **Rain lines up with the same day's flow (real data).** Features are built from the recorded Chantal fixtures (USGS 2025-06-25..07-15 and historical-forecast in America/New_York for the same window). The resulting rows must show:
  - on 2025-07-06, `precip_0` = 54.7 and `flow_cfs` = 1990.0, and on 2025-07-05, `flow_cfs` = 15.5. The first day with ≥ 25 mm of rain is the same date as the first day flow rises more than 10× over the previous day (lag 0);
  - on 2025-07-07, `flow_cfs` = 8180.0 (peak the day after the heaviest rain), and on 2025-07-10, flow rises again to 922 after 49.7 mm on 07-09.

  An independent check uses MRMS from `iem_mrms_chantal.json`: the day of maximum MRMS rain (2025-07-06, 7.01 in) must equal the Open-Meteo heavy-rain day and the flow-jump day.

  A second fixture holds the same historical-forecast request made with `timezone=GMT` (the heavy rain shifts to 07-07, a day after the rise). Ingest rejects it under AC-1.10. When the rejection is bypassed in the test, the lag-0 assertion fails. That shows the test really detects misalignment. Features join all sources on the date string only.
- [x] AC-2.6 No leakage: for any issue date d0, changing any flow value after d0 leaves every feature column on row d0 unchanged (targets `y_1..y_3` excluded, since they are future flow by definition). Changing any historical-forecast or forecast weather value after d0 changes only `precip_f1..f3` (the documented exception in R6 and RK3), and changing weather after d0+3 changes nothing. Tested as a property test on perturbed copies.
- [x] AC-2.7 Output `data/features/features_<ts>.csv` with columns: `date` (d0), `flow_cfs`, `approval_status`, `qualifier`, `flow_filled`, `logq_0`, `logq_1`, `logq_2`, `dlogq_1`, `precip_0`, `api_7`, `api_30`, `precip_f1`, `precip_f2`, `precip_f3`, `weather_lead_matched`, `fc_fetched_date`, `tmax_0`, `tmin_0`, `doy_sin`, `doy_cos`, `y_1`, `y_2`, `y_3`. It includes the latest d0 row (targets NaN). `fc_fetched_date` is the local date on which the forecast file that supplied that row's `precip_f*` was fetched; it is set only on the latest row. At most the 5 newest features files are kept. All features outputs are written with `paths.atomic_write` (AC-0.9).
- [x] AC-2.10 **Every feature follows the availability matrix** below (D26). Test: build features from fixtures where each source carries a distinct marker value (historical-forecast = 1.0 mm, previous-runs lead 1 = 2.0, lead 2 = 3.0, `weather_fc` = 4.0) and assert, for one train row, one calibration row, one test row, and the live row, that every weather feature equals the marker of the source the matrix names, and that no feature reads a date after its "Latest data" column.
- [x] AC-2.9 **Rain check against MRMS (verification only, D24).** When raw MRMS files exist, Features also writes `data/features/rain_check_<ts>.json` for dates from `CALIBRATION_START` onward.
  - **Per Open-Meteo rain series** (`lead0` = historical-forecast, `lead1`, `lead2`), versus MRMS: `n`, `bias_mm`, `mae_mm`, `hits` (MRMS ≥ 10 mm and Open-Meteo ≥ 10 mm), `misses` (MRMS ≥ 10 mm, Open-Meteo < 2 mm), and `false_alarms` (Open-Meteo ≥ 10 mm, MRMS < 2 mm).
  - **The 10 dates with the largest absolute disagreement**, with both values.
  - **Logging:** Features logs one summary line per series.
  - **Model inputs unchanged:** no MRMS-derived column ever appears in `features_*.csv`. If MRMS files are missing, the rain check is skipped with a WARNING, and the features output is unaffected.
  - **Verification file for Train:** Features also writes the deduplicated MRMS series to its own file, `data/features/mrms_<ts>.csv` (`date, mrms_mm`), for the rain-day/dry-day evaluation (AC-3.10). It is a separate file from `features_*.csv` and is never joined into it.
  - **Fixture check:** built from the recent fixtures, 2026-09-18 is counted as a `lead0` false alarm (51.5 mm vs 0.0 mm).
- [x] AC-2.8 Lead-matched rain (D20). For rows with `CALIBRATION_START ≤ d0 < latest d0`: `precip_f1` = historical-forecast precipitation on d0+1 (lead 0), `precip_f2` = previous-runs `lead_days=1` on d0+2, and `precip_f3` = previous-runs `lead_days=2` on d0+3, with `weather_lead_matched = True`. If a lead value is missing, it stays NaN; it is **never** back-filled from historical-forecast. Rows before `CALIBRATION_START` use historical-forecast for all three, with `weather_lead_matched = False`. The latest row takes `precip_f1..f3` from the newest `weather_fc` file (the live forecast; `weather_lead_matched = True`). Verified on the fixture: row d0 = 2026-09-16 has `precip_f2` equal to the 2026-09-18 lead-1 value (0.7 mm), not 51.5 mm.

Feature definitions (all relative to issue date d0): `logq_k = log1p(flow[d0−k])`; `dlogq_1 = logq_0 − logq_1`; `precip_0` = precipitation on d0; `api_7`, `api_30` = precipitation summed over d0−6..d0 and d0−29..d0; `precip_fh` = precipitation on d0+h (source per AC-2.8); `tmax_0`, `tmin_0` on d0; `doy_sin/cos` from the day of year of d0; `y_h = log1p(flow[d0+h]) − logq_0`.

**Feature availability matrix (D26).** Issue time is the morning of d0+1 (America/New_York). "Latest data" is the last valid date a feature may use.

| Feature | Train rows (d0 < 2024-02-01) | Calibration and test rows (d0 ≥ 2024-02-01) | Live row (serve) | Latest data | Available at issue time? |
|---|---|---|---|---|---|
| `logq_0..2`, `dlogq_1` | USGS daily mean | USGS daily mean | USGS daily mean (may be provisional) | d0 | Yes: d0's value is posted by d0+1 (RK8 covers late posting) |
| `precip_0`, `tmax_0`, `tmin_0` | historical-forecast, d0 | historical-forecast, d0 | newest `weather_fc` `past_days` value for d0 | d0 | Yes: verified identical to historical-forecast for past days (2026-09-15..22) |
| `api_7`, `api_30` | historical-forecast, d0−6..d0 / d0−29..d0 | same | `weather_fc` `past_days` for d0−6..d0, historical-forecast for older days | d0 | Yes |
| `precip_f1` | historical-forecast, d0+1 (≈ lead 0) | historical-forecast, d0+1 (lead 0) | `weather_fc` day 1 (today, lead 0) | forecast issued on d0+1 | Yes |
| `precip_f2` | historical-forecast, d0+2 (≈ lead 0: **documented exception**, R6/RK3) | previous-runs `lead_days=1`, d0+2 | `weather_fc` day 2 (lead 1) | forecast issued on d0+1 | Train: no (exception); others: yes |
| `precip_f3` | historical-forecast, d0+3 (**documented exception**) | previous-runs `lead_days=2`, d0+3 | `weather_fc` day 3 (lead 2) | forecast issued on d0+1 | Train: no (exception); others: yes |
| `doy_sin`, `doy_cos` | calendar | calendar | calendar | d0 | Yes |
| MRMS (any) | never a feature | never a feature | never a feature | n/a | n/a (verification only, D24) |

### Proposed changes
- `streamforecast/features.py`: `load_raw_usgs`, `load_raw_weather`, `validate_flow`, `build_features`, `main()`.
- `tests/unit/test_features_validation.py`, `tests/unit/test_features_build.py`, `tests/regression/test_features_chantal.py`, `tests/unit/test_features_leakage.py`.

### Architecture / boundaries
- May read: `data/raw/**`, `config`, `paths`, `logs`.
- May write: `data/features/`.
- Must never import: `ingest`, `train`, `forecast`, `dashboard`, `scheduler`. It never makes network calls.

### Risks for this stage
RK5 (never clip), RK7 (revisions), RK9 (leakage via fill), RK10 (log1p), RK13 (dates).

### Automated tests
- unit `test_newest_file_wins` → AC-2.1.
- unit `test_impossible_values_dropped` (−5, −999999, "abc", NaN) → AC-2.2.
- regression `test_chantal_peak_survives` (fixture → 8,180.0 in output) → AC-2.3.
- unit `test_gap_1_day_filled_gap_10_days_not` → AC-2.4.
- regression `test_chantal_rain_and_flow_same_local_date` (ET fixtures: exact values and lag 0) → AC-2.5.
- unit `test_feature_availability_matrix` (marker-value test) → AC-2.10.
- regression `test_mrms_peak_day_matches_flow_jump` (Chantal MRMS fixture) → AC-2.5.
- unit `test_rain_check_metrics_and_false_alarm` (2026-09-18 flagged; counts and bias against hand-computed values) → AC-2.9.
- unit `test_no_mrms_column_in_features` and `test_rain_check_skipped_without_mrms` (features byte-identical with and without MRMS files) → AC-2.9.
- regression `test_gmt_fixture_breaks_alignment` (GMT fixture fed past the ingest check: lag-0 assertion fails, proving the test is sensitive) → AC-2.5.
- unit `test_no_future_information` (perturbation property test) → AC-2.6.
- unit `test_output_columns_and_latest_row` plus `test_prunes_old_feature_files` → AC-2.7.
- unit `test_lead_matched_rain_by_period` (before/after `CALIBRATION_START`; the 2026-09-18 lead-1 value used, not 51.5 mm; a missing lead stays NaN) → AC-2.8.

### Manual Smoke Test
#### What we're proving
Raw files become one feature table, floods are kept, and the newest row is ready for forecasting.
#### Terminal
Terminal 1:
```bash
source .venv/Scripts/activate
make features
ls -l data/features
head -n 3 data/features/features_*.csv
tail -n 2 data/features/features_*.csv
grep -E '^2025-07-0[5-7]' data/features/features_*.csv
cat data/features/rain_check_*.json
```
#### Watch for
- Log lines: rows read per source, `dropped impossible: 0 negative, 0 sentinel, ...`, `forward-filled N days`, and `wrote K rows (latest d0=<yesterday>)`.
- The header shows the AC-2.7 columns.
- The last row has yesterday's date, filled `precip_f1..f3`, today's date in `fc_fetched_date`, and empty `y_1..y_3`.
- The log reports `lead-matched rows: ~970 (from 2024-02-01)`, and `weather_lead_matched` switches from False to True at 2024-02-01.
- The three Chantal rows show rain and flow on the same day: 07-05 has about 0 mm and 15.5 cfs; 07-06 has about 54.7 mm (`precip_0`) and 1990.0 cfs; 07-07 has about 15.1 mm and 8180.0 cfs. If the heavy rain appears on 07-07 instead, the timezone pinning is broken.
- The rain-check JSON shows bias, MAE, hits, misses, and false alarms for `lead0`, `lead1`, `lead2` against MRMS. Its top-disagreement list includes 2026-09-18 (Open-Meteo 51.5 mm vs MRMS 0.0 mm) and probably 2025-07-06 (54.7 vs about 178 mm). Expect more false alarms and misses at lead 1–2 than at lead 0.
#### Stop
Nothing is left running.

### Implementation notes

**Built (Builder, 2026-09-24).**
- `streamforecast/features.py`:
  - Loaders: `load_raw_usgs`, `load_raw_weather`, `load_raw_leads`, `load_forecast`, `load_raw_mrms`.
  - Processing: `validate_flow`, a pure `build_features(flow, weather, leads, forecast, settings)`, and `rain_check`.
  - Output: `run()` writes `features_<ts>.csv`, plus `rain_check_<ts>.json` and `mrms_<ts>.csv` when raw MRMS exists. It keeps the 5 newest of each of the three patterns and exits 1 only when there is no raw USGS file or no valid flow.
- `tests/conftest.py` gained three fixtures: `write_raw`, `raw_from_fixtures` (builds raw CSVs from recorded fixtures with ingest's own parsers), and `run_features`. It also gained an autouse fixture that clears every config env var, so a developer's shell (for example a leftover `INGEST_SOURCE=fixtures`) can't leak into tests.
- New tests:
  - `tests/unit/test_features_validation.py` (7)
  - `tests/unit/test_features_build.py` (13)
  - `tests/unit/test_features_leakage.py`: 3 property tests × 27 issue dates, plus 2 checks
  - `tests/regression/test_features_chantal.py` (5)
  - `tests/integration/test_features_run.py` (2): ingest fixture mode, then Features

**Live check, not part of `make test`.** On the Stage 1 live data in a scratch `DATA_DIR`:
- `dropped impossible: 0 negative, 0 sentinel, 0 non-numeric, 0 missing`.
- `forward-filled 2 days`: the real 2026-09-09..10 USGS gap.
- `skipped 2 issue dates with incomplete flow lags`: 2018-01-01 and 01-02.
- `lead-matched rows: 963 (from 2024-02-01)` and `wrote 3184 rows (latest d0=2026-09-23)`.
- Chantal rows: 07-05 has 0.0 mm and 15.5 cfs; 07-06 has 54.7 mm and 1990 cfs (ESTIMATED); 07-07 has 8180 cfs (ESTIMATED, kept).
- Latest row: `precip_f1..f3` = 0.8 / 0.0 / 0.0 and `fc_fetched_date` = 2026-09-24. Row 2026-09-16 has `precip_f2` = 0.7.
- Rain check against MRMS (n = 966 each):

  | Series | Bias | MAE | Hits | Misses | False alarms |
  |---|---|---|---|---|---|
  | lead0 | −0.46 mm | 2.57 mm | 51 | 23 | 4 |
  | lead1 | −0.52 mm | 3.01 mm | 40 | 38 | 14 |
  | lead2 | −0.43 mm | 3.19 mm | 46 | 19 | 17 |

  Lead 1 and lead 2 have more misses and false alarms than lead 0, as expected. 2026-09-18 is 4th in lead0's top-10 disagreements, and 2025-07-06 is 1st in all three series.

**Deviations and interpretations.**
1. **Issue dates are only days with an observed, valid flow.**
   - At serve time d0 always is (D4). This also makes AC-2.4's "fill gaps of at most `MAX_FFILL_DAYS`" causal: any gap reaching d0−1 or d0−2 is already closed by d0, so its length is known at issue time.
   - Gaps are filled only for `logq_1` and `logq_2`. `flow_filled` is True when either was filled. Targets use observed flow only.
   - Earlier rows whose lags stay NaN are left out and counted in the log. The **latest row is always kept**, even with NaN lags, so Forecast can refuse it (AC-4.9).
2. **The live row's `precip_f1..f3` come only from the newest `weather_fc` file.** A value missing or null there stays NaN and is never taken from an older file (supports AC-4.7). `fc_fetched_date` is that file's `fetched_at_utc` converted to a local date with stdlib `zoneinfo`; the no-`tz_convert` grep test still holds.
3. **Weather merge (D28):** rows are ordered by file timestamp, and `weather_fc` wins a tie. A row whose precipitation and temperatures are all empty never wins. Leads are deduplicated by `(date, lead_days)`. MRMS rows with no value are dropped before newest-file-wins.
4. **AC-2.5 wording: approved by the student as D32; AC-2.5 ticked.**
   - **The problem:** the literal check ("the first day with ≥ 25 mm of rain is the same date as the first day flow rises more than 10×") is false on the real recording. 2025-07-02 had 35.7 mm (MRMS 39.6 mm) and a same-day rise from 13.2 to 37.6 cfs (2.8×), so the first ≥ 25 mm day is 07-02, not 07-06.
   - **The test implements:** on the first day flow rises more than 10× (07-06), `precip_0` ≥ 25 mm, and that day is the window's wettest.
   - **Results:** ET passes (54.7 mm, the maximum). The GMT fixture fails exactly as intended (7.9 mm on the jump day; wettest day 07-07). MRMS's wettest day is also 07-06.
   - Every exact value AC-2.5 lists (15.5, 54.7, 1990.0, 8180.0, 49.7, 922) is asserted as written.
5. **AC-1.12, Features half:** Features logs `WARNING weather grid cell differs between files` when `grid_lat`/`grid_lon` differ across `weather_hist`, `weather_fc`, and `weather_leads` files (`test_grid_cell_change_warns`). That criterion was ticked in Stage 1; this is the Features part it needed.
6. **Rain check layout:**
   - `{"from", "heavy_mm": 10, "dry_mm": 2, "series": {"lead0"|"lead1"|"lead2": {n, bias_mm, mae_mm, hits, misses, false_alarms, top_disagreements[{date, openmeteo_mm, mrms_mm}]}}}`, written with `allow_nan=False`.
   - `lead0` is the merged daily weather (historical-forecast, with `past_days` values for the last week under D28), from `CALIBRATION_START` onward.
   - `bias` = mean(Open-Meteo − MRMS) over dates both series have. An empty series gives `null` bias and MAE.
7. **"Impossible" values:** `inf` counts as non-numeric. Zero and every finite value ≥ 0 are kept.
8. **Tests I corrected while writing them** (never plan tests):
   - the 2026-09-16 `precip_f3` expectation: the real lead-2 value for 09-19 is 1.2 mm, not 0.0;
   - the sensitivity check in the leakage test: moved to a late date where `api_30` is defined;
   - one hand-computed gap-test value: 40, not 41.

**Verification.** `make lint` exit 0. `make test`: 249 passed, 4 skipped (train, forecast, dashboard, and scheduler aren't built yet).

### Review findings

See the Tester review under Stage 6: TF7.

---

## Stage 3 — Train

### Goal
Fit one `HistGradientBoostingRegressor` per horizon on the train split, compute residual quantiles on the calibration split, evaluate on the test split against persistence, and publish a versioned checkpoint plus a metrics sidecar.

### Acceptance criteria
- [x] AC-3.1 Splits come from `TRAIN_END`, `CALIBRATION_START`, and `CALIBRATION_END`, by date: train covers ..`TRAIN_END`, calibration covers `CALIBRATION_START`..`CALIBRATION_END`, and test covers after `CALIBRATION_END`. Rows between `TRAIN_END` and `CALIBRATION_START` are unused. A row belongs to a split only if both d0 and d0+h fall inside it. Every calibration and test row has `weather_lead_matched = True`; training aborts with an error if not. Rows with a NaN feature are dropped from calibration and test and counted in the log (HGB would accept them, but they would not be lead-matched). The three splits do not overlap and are strictly ordered in time, and no shuffling occurs anywhere (test asserts max(train date) < min(calibration date) < min(test date) per horizon).
- [x] AC-3.2 Three models (h = 1, 2, 3) with `random_state=0` and default hyperparameters, fit only on train rows, target `y_h`, on the feature columns listed in AC-2.7 (excluding `date`, `flow_cfs`, `approval_status`, `qualifier`, `flow_filled`, `weather_lead_matched`, `fc_fetched_date`, and the targets).
- [x] AC-3.3 The residual quantiles at 0.025, 0.10, 0.50, 0.90, 0.975 per horizon come from calibration rows only, are stored in the checkpoint, and are non-decreasing.
- [x] AC-3.4 Checkpoint `data/models/model_<ts>.joblib` contains `models`, `residual_quantiles`, `feature_columns`, `sklearn_version`, `split_dates`, and `trained_at_utc`. Sidecar `data/models/metrics_<ts>.json` has the same `<ts>`, and both store `model_version = <ts>`. Both are written with `paths.atomic_write` (AC-0.9), the checkpoint first and the sidecar last; a checkpoint without a sidecar counts as unpublished. The sidecar is valid JSON with no `NaN`/`Infinity` (`allow_nan=False`); missing values are `null`.
- [x] AC-3.5 The sidecar reports, per horizon on the test split: `n`, model `mae_cfs`, `rmse_cfs`, `nse`; persistence `mae_cfs`, `nse`; `skill_mae = 1 − mae_model/mae_persistence`; `coverage_80`, `coverage_95`; and `coverage_80_high`, `coverage_95_high`, `n_high` for days whose observed flow at d0+h exceeds the training 90th percentile of flow. Calibration-split coverage is reported too.
  **Metric definitions (D26), for every group (overall, high-flow, wet, dry):**
  - **Common rows:** model and persistence are scored on exactly the same rows: those with an observed target and complete features (`n` counts them).
  - **Formulas** (o = observed cfs at d0+h, p = prediction in cfs):
    - `mae = mean|o−p|`
    - `rmse = sqrt(mean (o−p)²)`
    - `nse = 1 − Σ(o−p)² / Σ(o−ō)²`
    - `skill_mae = 1 − mae_model / mae_persistence`
    - `coverage_x` = share of rows with `lo_x ≤ o ≤ hi_x`
  - **Edge cases:**
    - `nse` is `null` when `Σ(o−ō)² = 0`.
    - `skill_mae` is `null` when `mae_persistence = 0`.
    - A group with `n < 20` reports every metric as `null` with `"status": "insufficient"`, otherwise `"status": "ok"`; an empty group has `n = 0`.
  - **Never silent:** no metric is ever computed from mismatched rows or reported as `NaN`.
- [x] AC-3.6 On a synthetic stationary series (fixed seed, generated in the test), calibration and test coverage of the 80% band are within 0.80 ± 0.05 and of the 95% band within 0.95 ± 0.03, and the model beats persistence (`skill_mae > 0`) at h = 1.
- [x] AC-3.7 Deterministic: two runs of `make train` on the same features file produce identical predictions and quantiles.
- [x] AC-3.8 Training logs a WARNING (not an error) for any horizon where `skill_mae ≤ 0` or the AC-3.9 coverage check fails. Training still publishes. The Tester records either condition on real data as a finding.
- [x] AC-3.10 **Rain-day / dry-day split (D25).**
  - **Classification:** each test row for horizon h is classified by observed MRMS rain summed over its forecast window d0+1..d0+h, taken from the newest `data/features/mrms_*.csv`:
    - **wet** if ≥ 10 mm;
    - **dry** if < 2 mm;
    - **light** in between (counted only).
  - **Sidecar key:** per horizon, `by_rain = {"wet": {...}, "dry": {...}, "n_light": k, "wet_mm": 10, "dry_mm": 2}`. Each group has `n`, model and persistence `mae_cfs`, `skill_mae`, `coverage_80`, `coverage_95`.
  - **Not gated:** the split is reported, not gated.
  - **MRMS stays out of the model:** it is used only to label rows **after** prediction, and the checkpoint's `feature_columns` never include it.
  - **If MRMS is unavailable:** `by_rain` is `null` and training logs a WARNING (training still succeeds).
- [x] AC-3.9 **Test-period interval coverage check.** This criterion is met when the **check works correctly**, proven on hand-written sidecars. The **real-data result** is reported, not required to pass: an out-of-tolerance result on real data is a **finding** handled under D23 and in the Tester's review, not an unmet criterion (D26). The deterministic, strict gates are AC-3.6 (synthetic coverage and skill) and the calibration wiring check below. `make coverage` reads the newest metrics sidecar, prints a per-horizon table (`h`, `n`, `coverage_80`, `coverage_95`, `n_high`, `coverage_80_high`, `coverage_95_high`, calibration `coverage_80`/`coverage_95`, and, printed but not gated, `skill_mae` and `coverage_80` for the wet and dry groups from AC-3.10), and exits 0 only if, for **every** horizon on the **test split**:
  - `coverage_80` ∈ [0.72, 0.88] and `coverage_95` ∈ [0.90, 0.99] (tolerances from D19), and
  - calibration-split `coverage_80` and `coverage_95` are within ±0.02 of nominal. This is a wiring check: the quantiles are computed from the calibration period, so anything else means the bands are applied incorrectly.

  Coverage means the fraction of rows where `lo ≤ observed Q[d0+h] ≤ hi`, inclusive, computed in cfs using the **same band function as Forecast**. High-flow coverage is printed but not gated, because `n_high` is small and clustered in a few events (RK6). The non-zero exit is kept so a failure is visible, and `make coverage` is not part of `make test` or CI (it needs real data). **Required response to a real-data failure:** (1) if the calibration wiring check fails, it's a bug; fix it. (2) If only the test tolerances fail, the Tester records a finding with the table, and the student triages it under D23: diagnose first, then accept and document it or try the one allowed alternative. It must **not** be fixed by widening the tolerances or recomputing quantiles on the test split.

### Proposed changes
- `streamforecast/train.py`: `split_by_date`, `fit_horizon`, `residual_quantiles`, `bands`, `evaluate`, `publish`, `main()`, plus `--report` (reads the newest sidecar, prints the coverage table, applies the AC-3.9 thresholds, and sets the exit code).
- `Makefile`: `coverage` target → `$(PYTHON) -m streamforecast.train --report`.
- `tests/unit/test_train_split.py`, `tests/unit/test_train_quantiles.py`, `tests/regression/test_train_synthetic_coverage.py`, `tests/integration/test_train_publish.py`.

### Architecture / boundaries
- May read: the newest `data/features/features_*.csv`; the newest `data/features/mrms_*.csv` (evaluation labels only, AC-3.10); `config`, `paths`, `logs`.
- May write: `data/models/`.
- Must never import: `ingest`, `features`, `forecast`, `dashboard`, `scheduler`. No network.

### Risks for this stage
RK3, RK5, RK6, RK16. There is also a risk of subtle leakage at split edges; mitigated by requiring d0+h inside the split (AC-3.1).

### Automated tests
- unit `test_splits_time_ordered_no_overlap` → AC-3.1.
- unit `test_fit_uses_train_rows_only` → AC-3.2.
- unit `test_quantiles_from_calibration_and_ordered` → AC-3.3.
- integration `test_publish_checkpoint_and_sidecar` (tmp `DATA_DIR`, small synthetic features file) → AC-3.4, AC-3.5 (keys and types present).
- regression `test_synthetic_coverage_and_skill` → AC-3.6.
- regression `test_deterministic_training` → AC-3.7.
- unit `test_warns_when_no_skill` → AC-3.8.
- unit `test_coverage_report_thresholds` (hand-written sidecars: one inside every tolerance → exit 0; test `coverage_80 = 0.70` → exit 1; calibration `coverage_80 = 0.75` → exit 1; boundaries 0.72 and 0.88 pass) → AC-3.9.
- unit `test_rain_split_labels_and_metrics` (a hand-built MRMS series: window sums 0, 5, 12 mm → dry, light, wet; the h=3 window is d0+1..d0+3; group metrics match hand-computed values) → AC-3.10.
- unit `test_rain_split_null_without_mrms` and `test_checkpoint_features_exclude_mrms` → AC-3.10.
- unit `test_metric_edge_cases` (constant observations → `nse` null; zero persistence error → `skill_mae` null; a 19-row group → `insufficient`; an empty wet group → `n = 0`; the model and persistence row sets are identical; the sidecar parses with `allow_nan=False`) → AC-3.5, AC-3.10.
- unit `test_coverage_inclusive_and_in_cfs` (an observation exactly on a bound counts as covered; the computation goes through `bands()`) → AC-3.9.

### Manual Smoke Test
#### What we're proving
A real model is trained on 2018–2023, calibrated on 2024-02..2024-12 with lead-matched forecast rain, and scored on 2025+ (also lead-matched), and we can read its skill against persistence.
#### Terminal
Terminal 1:
```bash
source .venv/Scripts/activate
make train
ls -l data/models
cat data/models/metrics_*.json
```
#### Watch for
- Log lines per horizon: `h=1 train n=~2190 cal n=~330 test n=~625`, then `h=1 test MAE model X vs persistence Y (skill Z)` and `coverage80 A coverage95 B (high-flow A' B')`.
- Two new files with the same timestamp: `model_<ts>.joblib` and `metrics_<ts>.json`.
- The JSON shows three horizons with the AC-3.5 keys. Note whether skill is positive and whether high-flow coverage is lower than overall (RK6).
- Then run `make coverage; echo "exit=$?"`. You should see a readable table with one row per horizon. Calibration coverage should be about 0.80 and 0.95 (wiring check). Test coverage is the honest number; watch whether it drops from h=1 to h=3 (RK3). In the wet/dry columns, expect skill near 0 on dry days (flow barely changes, so persistence is hard to beat) and any real skill to show up on wet days. Expect coverage to be lower on wet days (RK3c, RK6). `exit=0` means every horizon is inside the D19 tolerances; `exit=1` names the failing horizon.
#### Stop
Nothing is left running.

### Implementation notes

**Built (Builder, 2026-09-25).**
- `streamforecast/train.py` has four parts:
  - **Training:** `split_by_date`, `fit_horizon`, `predict`, `residual_quantiles`.
  - **Bands:** `bands(logq_0, yhat, quantiles)`, which is the band function Forecast must mirror (Stage 4).
  - **Scoring:** `observed_cfs`, `coverage`, `group_metrics`, `rain_labels`, `evaluate`, `calibration_coverage`.
  - **AC-3.9 gate and entry points:** `check_horizon` and `report` implement the gate. `train()` and `publish()` produce the outputs, and `main()` handles `python -m streamforecast.train [--report]`. The Makefile `coverage` target already calls `--report`.
- **Checkpoint** `model_<ts>.joblib`: a plain dict with keys `models` {1, 2, 3 → `HistGradientBoostingRegressor`}, `residual_quantiles` {1, 2, 3 → {"0.025", "0.1", "0.5", "0.9", "0.975" → float}}, `feature_columns`, `sklearn_version`, `split_dates`, `trained_at_utc`, and `model_version`. It holds no `streamforecast` object, so loading it never imports a stage module; `test_checkpoint_holds_no_streamforecast_objects` checks the raw bytes and the loaded types.
- **Sidecar** `metrics_<ts>.json`, written last with `allow_nan=False`. Stable key paths for Stages 4 and 5:
  - **Top level:** `model_version`, `trained_at_utc`, `features_file`, `feature_columns`, `sklearn_version`, `split_dates`, `high_flow_threshold_cfs`.
  - **Per horizon:** `horizons["<h>"]` contains:
    - `train.n`
    - `calibration.{n, coverage_80, coverage_95}`
    - `residual_quantiles`
    - `test`, which holds `n`, `status`, `model.{mae_cfs, rmse_cfs, nse}`, `persistence.{mae_cfs, nse}`, `skill_mae`, `coverage_80`, `coverage_95`, `n_high`, `coverage_80_high`, `coverage_95_high`, `high` (the full high-flow group), and `by_rain`.
  - **`by_rain`** is either null or `{wet, dry, n_light, n_unlabeled, wet_mm, dry_mm}`.
  - **Stage 5's KPIs** read `horizons["1"]["test"]["skill_mae"]` and `horizons["1"]["test"]["coverage_80"]`.
- **Tests:**
  - `tests/unit/test_train_split.py` (8)
  - `tests/unit/test_train_quantiles.py` (26, including 11 threshold cases built from a real produced sidecar)
  - `tests/regression/test_train_synthetic_coverage.py` (5)
  - `tests/integration/test_train_publish.py` (5)
  - `tests/conftest.py` gained `make_synthetic_features` and the `synthetic_features` fixture.

**Real-data run (scratch `DATA_DIR` built from the Stage 1–2 live data, not `make test`).**
- `h=1 train n=2188 cal n=334 test n=625`; h=2 2187 / 333 / 624; h=3 2186 / 332 / 624.
- A few test rows were dropped for a NaN feature (h=1: 2, h=2: 1): the most recent rows, whose lead-2 rain isn't archived yet.
- HGB `n_iter_=100` (`max_iter=100`), so early stopping stayed off at this sample size.
- High-flow threshold: 152.0 cfs, exactly the RK6 figure.
- Results (the table printed by `make coverage`, which exited 0):

  | h | skill_mae | test cov80 / cov95 | high-flow cov80 / cov95 (n=28) | cal cov80 / cov95 | wet skill / cov80 | dry skill / cov80 |
  |---|---|---|---|---|---|---|
  | 1 | 0.316 | 0.842 / 0.957 | 0.250 / 0.643 | 0.796 / 0.946 | 0.087 / 0.387 | 0.712 / 0.927 |
  | 2 | 0.350 | 0.838 / 0.960 | 0.179 / 0.679 | 0.796 / 0.946 | 0.265 / 0.517 | 0.687 / 0.944 |
  | 3 | 0.352 | 0.795 / 0.963 | 0.143 / 0.786 | 0.795 / 0.946 | 0.300 / 0.536 | 0.684 / 0.926 |

- Wet/dry/light counts for d0 in 2025 only: 39/285/41 (h=1), 75/237/53 (h=2), 105/196/64 (h=3). RV1 had 39/284/41, 75/235/53, 105/193/64; they match within 3 rows.

**Findings for the Tester and D23: reported, not tuned (AC-3.8, AC-3.9).**
- (a) The model beats persistence at every horizon, and every AC-3.9 gate passes.
- (b) **High-flow coverage is far below nominal** (80% band 0.14–0.25; n_high = 28, from a handful of events). This is RK6: the calibration year has few floods.
- (c) **Wet-window coverage is low (0.39–0.54 for the 80% band).** Also, **skill is higher on dry windows than on wet ones**, the opposite of what the smoke test's "Watch for" expected. Dry recessions are smooth, so modelling the log-change beats "no change" by a wide margin; on wet windows the forecast-rain error (RK3) dominates.

No hyperparameters, features, splits, or tolerances were changed after seeing these numbers.

**Deviations and interpretations.**
1. **Split edges:** d0+h is computed with calendar dates, not row offsets (Features skips days without observed flow).
   - Train is d0+h ≤ `TRAIN_END`.
   - Calibration is d0 ≥ `CALIBRATION_START` and d0+h ≤ `CALIBRATION_END`.
   - Test is d0 > `CALIBRATION_END`.
   - Rows without a target are dropped everywhere.
   - Train keeps rows with a NaN feature (HGB handles them); calibration and test drop them and log the count.
2. **Observed flow at d0+h** is recovered as `expm1(logq_0 + y_h)`. Persistence is `flow_cfs` at d0.
3. **High-flow group:** test rows whose observed flow at d0+h exceeds the 90th percentile of `flow_cfs` over rows with d0 ≤ `TRAIN_END`. The threshold is stored in the sidecar.
4. **Wet/dry windows:** a window with any day missing from the MRMS verification file is `unlabeled` and counted in `n_unlabeled`, never treated as 0 mm. It's an extra key next to `n_light`.
5. **The coverage gate uses unrounded values.** A `null` coverage (a group marked `insufficient`) fails it. The table prints 3 decimals.
6. **Clean failures:** with no features file, or no train or calibration rows (for example, fixture-mode features, which hold only 8 rows in 2026), training logs an ERROR, publishes nothing, and exits 1. Forecast will then see "no checkpoint yet" (AC-4.4). `models/` is never pruned, because the dashboard needs older sidecars (AC-5.1).
7. **AC-3.6 uses its own four-year calibration and test periods** (train through 2017, calibration 2018–2021, test 2022–2025), set through `Settings`. With the default 335-day calibration year, sampling error alone would put the 95% band close to its ±0.03 tolerance. The criterion tests the method, and it is still checked on every horizon.
   - **Synthetic results:** calibration 0.800 / 0.949 on every horizon. Test 0.792 / 0.944 (h=1), 0.806 / 0.958 (h=2), 0.821 / 0.960 (h=3). Skill 0.43 / 0.47 / 0.49.
8. **Determinism (AC-3.7):** the test runs `train.main([])` twice with two different `utc_now` instants. That produces two separate checkpoints instead of one overwritten file, and their predictions and quantiles are compared exactly.
9. **Tests I corrected while writing them:**
   - A rain-label expectation: d0 = 01-04 has an MRMS value on 01-05, so it is "dry", not "unlabeled".
   - The inclusive-bound check now uses exact values, because round-tripping through `log1p`/`expm1` can move a value just past a bound.

**Verification.** `make lint` exits 0. `make test`: 294 passed, 3 skipped (forecast, dashboard, and scheduler aren't built yet).

### Review findings

See the Tester review under Stage 6: TF2 (Major, D23), TF8a/c, TF10, TF14.

---

## Stage 4 — Forecast

### Goal
Load the newest published checkpoint and the newest features row, and write a 3-day forecast with median, 80%, and 95% bounds next to the persistence baseline.

### Acceptance criteria
- [x] AC-4.1 The output `data/forecasts/forecast_<d0>_<ts>.csv` has exactly 3 rows (h = 1, 2, 3) and columns: `issue_date` (d0), `valid_date`, `horizon`, `median_cfs`, `lo80_cfs`, `hi80_cfs`, `lo95_cfs`, `hi95_cfs`, `persistence_cfs`, `latest_obs_cfs`, `latest_obs_provisional`, `latest_obs_estimated`, `stale_days`, `fc_fetched_date`, `model_version`, `created_at_utc`. `model_version` is the checkpoint's `<ts>` (AC-3.4). The file is written with `paths.atomic_write` (AC-0.9).
- [x] AC-4.2 Bounds are computed as in D9: `q_p = logq_0 + ŷ_h + r_p`, `cfs = max(0, expm1(q_p))`, and `median_cfs` uses `r_0.50`.
- [x] AC-4.3 On every row, `0 ≤ lo95 ≤ lo80 ≤ median ≤ hi80 ≤ hi95`.
- [x] AC-4.4 No published checkpoint (no `model_*.joblib` with a matching `metrics_*.json`): log `no checkpoint yet; skipping forecast` at WARNING, write nothing, exit 0.
- [x] AC-4.5 **USGS hasn't posted yesterday.** `stale_days = (paths.local_today() − 1 day) − d0` (America/New_York, R14).
  - `stale_days = 0`: normal.
  - `1 ≤ stale_days ≤ MAX_STALE_DAYS` (2): forecast from d0 anyway. Valid dates stay d0+1..d0+3, so one or two of them may already be in the past. Log a WARNING `USGS data through <d0>, <k> day(s) stale`. `stale_days` is written on every row.
  - `stale_days > MAX_STALE_DAYS`: log an ERROR `USGS data through <d0> is <k> days stale (limit 2); not forecasting`, write nothing, exit 1.
  - Never fill the missing days (no persistence fill, no extrapolation).
- [x] AC-4.6 A corrupt or unloadable checkpoint (bad bytes, missing keys, `sklearn_version` mismatch): log an ERROR naming the file, write nothing, exit 1.
- [x] AC-4.7 **Fewer than 3 forecast days.** If any of `precip_f1..f3` on the latest row is missing or null (a truncated or partly-null Open-Meteo response): log an ERROR `forecast weather has N of 3 days for <d0+1>..<d0+3>; not forecasting`, write nothing, exit 1. No partial files with 1 or 2 rows are ever written.
- [x] AC-4.8 **Provisional or estimated latest value.** It is used as-is (no refusal, no band widening), and flagged with `latest_obs_provisional` (`approval_status == "Provisional"`) and `latest_obs_estimated` (`qualifier` contains `ESTIMATED`). Logged at INFO.
- [x] AC-4.9 **Incomplete flow history.** If any of `logq_0`, `logq_1`, `logq_2` on the latest row is NaN after the Features forward-fill (a gap longer than `MAX_FFILL_DAYS` within d0−2..d0): log an ERROR `flow lags incomplete at <d0>`, write nothing, exit 1.
- [x] AC-4.10 **Rate limit or outage upstream.** Forecast makes no HTTP calls; ingest handles retries (AC-1.5, AC-1.6). Forecast requires `fc_fetched_date == paths.local_today()` on the latest row. If the newest forecast weather is older (for example because today's Open-Meteo call hit 429 on every retry), log an ERROR `forecast weather is stale (fetched <date>); not forecasting`, write nothing, exit 1. A USGS-only failure is handled by the staleness rules in AC-4.5.
- [x] AC-4.11 **Refusals are safe.** In every refusal case (AC-4.5 beyond the limit, 4.6, 4.7, 4.9, 4.10), earlier `forecast_*.csv` files are left untouched, and the scheduler continues its loop. Exit codes: 0 = forecast written, or no checkpoint yet (AC-4.4); 1 = refused or checkpoint unusable.

### Proposed changes
- `streamforecast/forecast.py`: `load_newest_checkpoint`, `latest_row`, `predict_bands`, `main()`.
- `tests/unit/test_forecast_bands.py`, `tests/integration/test_forecast_run.py`.

### Architecture / boundaries
- May read: the newest `data/models/model_*.joblib` and its sidecar, the newest `data/features/features_*.csv`, `config`, `paths`, `logs`.
- May write: `data/forecasts/`.
- Must never import: `ingest`, `features`, `train`, `dashboard`, `scheduler`. No network.

### Risks for this stage
RK3, RK8, RK10, RK12, RK16. When `stale_days` is 1 or 2, `precip_f` for already-past valid dates comes from the forecast file's `past_days` (lead-0), which is more accurate than what calibration assumed, so bands for those rows are slightly conservative. That is acceptable. Also: joblib loads pickles. Only files this pipeline wrote into its own volume are ever loaded; this is documented, not "fixed".

### Automated tests
- unit `test_bands_formula_and_median_bias` → AC-4.2.
- regression `test_forecast_bands_match_train_bands` (tests may import both modules even though stages may not: the same checkpoint and input row give identical bounds from `train.bands` and Forecast's band function, so measured coverage describes the served bands) → AC-4.2, AC-3.9.
- unit `test_band_ordering_and_nonnegative` (includes near-zero flow and large negative residuals) → AC-4.3.
- integration `test_forecast_writes_three_rows` → AC-4.1.
- integration `test_provisional_and_estimated_flagged` (the recent fixture is all Provisional; the estimated-gap fixture supplies an ESTIMATED latest day) → AC-4.8.
- integration `test_no_checkpoint_skips_cleanly` → AC-4.4.
- integration `test_stale_boundaries` (clock frozen; `stale_days` 0 → normal, 1 and 2 → written with a WARNING and `stale_days` on every row, 3 → refused with exit 1) → AC-4.5.
- integration `test_corrupt_checkpoint_errors` → AC-4.6.
- integration `test_short_forecast_refuses` (features built from `openmeteo_forecast_short.json`, plus a variant with a null precipitation value) → AC-4.7.
- integration `test_flow_lag_gap_refuses` (a 3-day gap ending at d0−1) → AC-4.9.
- integration `test_stale_forecast_weather_refuses` (`fc_fetched_date` = yesterday) → AC-4.10.
- integration `test_refusal_keeps_previous_forecast` (a prior forecast file exists; every refusal case leaves it byte-identical and adds no file; exit codes asserted) → AC-4.11.

### Manual Smoke Test
#### What we're proving
A forecast file appears with 3 readable rows and sane bands, and the stage behaves cleanly when there is no model.
#### Terminal
Terminal 1:
```bash
source .venv/Scripts/activate
make ingest
make features
make forecast
ls -l data/forecasts
cat data/forecasts/forecast_*.csv
DATA_DIR=/tmp/sf-empty make forecast; echo "exit=$?"
MAX_STALE_DAYS=-1 make forecast; echo "exit=$?"
ls -l data/forecasts
```
#### Watch for
- Log: `loaded model_<ts>`, `d0=<yesterday> stale_days=0`, and `wrote 3 rows -> data/forecasts/forecast_<d0>_<ts>.csv`.
- The CSV has 3 rows with valid dates of today, tomorrow, and the day after, and bounds that widen from h = 1 to h = 3, all ≥ 0. `persistence_cfs` equals `latest_obs_cfs`.
- The empty-dir run prints `no checkpoint yet; skipping forecast` and `exit=0`.
- The `MAX_STALE_DAYS=-1` run forces a staleness refusal (any d0 counts as too stale). It prints the `...days stale (limit -1); not forecasting` ERROR and `exit=1`, and the final `ls` shows the same forecast file as before: no new file, and the old one is untouched.
#### Stop
Nothing is left running.

### Implementation notes

**Built (Builder, 2026-09-25).**
- `streamforecast/forecast.py` provides these pieces:
  - `predict_bands`: a copy of `train.bands`, because Forecast may not import Train.
  - `newest_published`: the newest `model_<ts>.joblib` that has a matching `metrics_<ts>.json`.
  - `load_checkpoint`: raises `Refusal` for bad bytes, missing keys, a `sklearn_version` mismatch, or missing horizons, and the message always names the file.
  - `latest_row`, `check_inputs` (the freshness rules), `make_forecast`, `run`, and `main`.
- **Checks run in this order**, and the first failure decides the outcome:
  1. No published checkpoint: WARNING and exit 0.
  2. Checkpoint unusable: exit 1.
  3. USGS data older than `MAX_STALE_DAYS`: exit 1. At 1–2 days stale it only logs a WARNING and carries on.
  4. Flow lags incomplete: exit 1.
  5. Forecast weather not fetched today: exit 1.
  6. Fewer than 3 forecast-rain days: exit 1.
  7. Otherwise, 3 rows are written atomically.
- **After a refusal**, nothing is written or deleted. Log messages match the AC wording exactly.
- **Tests:**
  - `tests/unit/test_forecast_bands.py` (7).
  - `tests/regression/test_forecast_bands_match_train.py` (2): identical bounds from `train.bands` and `forecast.predict_bands`, on random inputs and on the real served forecast.
  - `tests/integration/test_forecast_run.py` (22): every test named in this stage's list, plus a checkpoint without its sidecar and an empty `DATA_DIR`.
  - `tests/conftest.py` gained `published_checkpoint`, a checkpoint trained once per session on synthetic features, and `install_checkpoint`.

**Live check, not part of `make test`.** On the Stage 1–3 live data in a scratch `DATA_DIR`, run on 2026-09-24 ET:
- It logged `loaded model_20260925T003255Z.joblib`, `d0=2026-09-23 stale_days=0`, the provisional note, and `wrote 3 rows -> forecasts/forecast_2026-09-23_20260925T004518Z.csv`, then exited 0.
- The three rows:

  | Horizon | Median | 80% band | 95% band | Persistence |
  |---|---|---|---|---|
  | t+1 | 8.66 | 5.66–12.26 | 2.76–35.56 | 7.22 |
  | t+2 | 8.57 | 4.30–18.88 | 1.81–50.71 | 7.22 |
  | t+3 | 5.28 | 2.32–10.88 | 0.62–70.47 | 7.22 |

- `MAX_STALE_DAYS=-1` logged `USGS data through 2026-09-23 is 0 days stale (limit -1); not forecasting` and exited 1. The earlier file was untouched.
- An empty `DATA_DIR` logged `no checkpoint yet; skipping forecast` and exited 0.

**Deviations and interpretations.**
1. **Smoke test updated (commands only).** It now runs `make ingest` and `make features` before `make forecast`. AC-4.10 requires `fc_fetched_date == local_today()`, so a forecast run on a later day than the last ingest is correctly refused. The plan's original sequence started with `make forecast` and would refuse on any day after the Stage 1 smoke test.
2. **Band widths don't grow at every level:**
   - The 95% band widens from t+1 to t+3 every time.
   - The **80% band is wider at t+2 than at t+3** on the real checkpoint, because the calibration residuals' 90th percentile is 0.725 at h=2 and 0.550 at h=3. Nothing in the method ties quantiles across horizons.
   - The smoke test's "bounds that widen from h = 1 to h = 3" holds for the 95% band only. This is a data property, not a bug. The Tester may treat it as a finding under RK6: 332 calibration rows per horizon.
3. **Check order:** staleness first, as the smoke test's `MAX_STALE_DAYS=-1` case needs, then lags, then forecast-weather freshness, then the 3-day count. So a truncated forecast from a stale fetch reports as stale.
4. **Flags:**
   - `latest_obs_provisional` is `approval_status == "Provisional"`.
   - `latest_obs_estimated` is true when `ESTIMATED` is one of the `;`-separated qualifiers.
   - Both are logged at INFO and never change the bands (AC-4.8).
5. **Band-order guard (AC-4.3):** `make_forecast` checks `0 ≤ lo95 ≤ lo80 ≤ median ≤ hi80 ≤ hi95` before writing and refuses if it fails. D9's monotone transform of ordered quantiles makes that unreachable, but a broken checkpoint can't publish a disordered forecast.
6. **Checkpoint loading:** joblib unpickles, so only files this pipeline wrote into its own volume are ever loaded (the risk is documented for this stage). Any exception while loading is reported as a refusal naming the file.

**Verification.** `make lint` exits 0. `make test`: 326 passed, 2 skipped (dashboard and scheduler not built yet).

### Review findings

See the Tester review under Stage 6: no findings specific to this stage.

---

## Stage 5 — Dashboard

### Goal
A Streamlit page that shows the last 60 days of observed flow plus the 3-day fan chart, with skill against persistence and interval coverage, reading only files under `DATA_DIR`.

### Acceptance criteria
- [x] AC-5.1 The page reads only these files; the module imports no stage module and makes no network calls (the boundary test from AC-0.5 covers the imports).
  - **Forecast:** the newest `forecasts/forecast_*.csv`.
  - **Observed history:** the newest `features/features_*.csv`.
  - **Metrics (D26):** the metrics sidecar whose `model_version` **matches the displayed forecast's `model_version`** (`models/metrics_<model_version>.json`), not simply the newest one, so the KPIs always describe the model that made the forecast shown. If that sidecar is missing, the KPIs show "metrics unavailable for model <version>", and the page still renders.
- [x] AC-5.2 The fan chart shows observed `flow_cfs` for the 60 days ending at d0, the forecast median, a shaded 80% band, a lighter 95% band, and the persistence line (dashed). The y-axis is labelled `cfs` and the x-axis shows dates.
- [x] AC-5.3 The chart title states a finding computed from the data, e.g. "Flow expected to rise to about 120 cfs by Friday" (rise/fall/hold decided by comparing the h = 3 median with `latest_obs_cfs`; "hold" when within ±10%).
- [x] AC-5.4 At most 3 KPIs: tomorrow's median (t+1), t+1 skill vs persistence (`skill_mae`, test split), and t+1 80% coverage on the test split.
- [x] AC-5.5 Visible warnings (D21):
  - **Outdated forecast:** when the newest forecast's `created_at_utc`, converted to America/New_York, is before `local_today()` (today's run refused or hasn't happened), show a warning banner "Forecast outdated — last issued <date> from data through <issue_date>".
  - **Stale data:** a warning banner when `stale_days > 0`.
  - **Provisional or estimated latest value:** an info note when `latest_obs_provisional` or `latest_obs_estimated` is true.
  - **Past valid dates:** forecast points with `valid_date < local_today()` are drawn greyed out and labelled "past".
- [x] AC-5.6 With an empty `DATA_DIR`, the page shows "No forecast yet — the pipeline has not produced one" and does not raise.

### Proposed changes
- `streamforecast/dashboard.py`: Streamlit app (file loading with `st.cache_data(ttl=300)`, an Altair layered chart, KPIs, banners).
- `tests/unit/test_dashboard_title.py`: a pure function `headline(forecast_df)`.
- `tests/integration/test_dashboard_app.py`: `streamlit.testing.v1.AppTest` against a tmp `DATA_DIR` with small files and an empty one.

### Architecture / boundaries
- May read: `data/forecasts/`, `data/features/`, `data/models/metrics_*.json`, `config`, `paths`.
- May write: nothing.
- Must never import: `ingest`, `features`, `train`, `forecast`, `scheduler`, joblib, or scikit-learn. It never loads model checkpoints.

### Risks for this stage
Streamlit `AppTest` and pytest-socket (see Stage 0 risk). The chart can be misread if bands are drawn on a linear axis during a flood: use a linear axis by default and add a log-scale toggle only if the Builder finds the low-flow line unreadable (no other controls).

### Automated tests
- unit `test_headline_rise_fall_hold` → AC-5.3.
- integration `test_app_renders_with_data` (chart present, exactly 3 metrics) → AC-5.2, AC-5.4.
- integration `test_app_metrics_match_forecast_model` (two sidecars exist; the forecast references the older one; the KPIs show the older one's numbers; with the matching sidecar deleted, the KPIs show "unavailable") → AC-5.1.
- integration `test_app_warnings` (cases: outdated forecast, `stale_days = 1`, provisional latest value, a past `valid_date`; clock frozen) → AC-5.5.
- integration `test_app_empty_dir` → AC-5.6.
- unit `test_boundaries` (from Stage 0) → AC-5.1.

### Manual Smoke Test
#### What we're proving
A person can open the page and read the forecast, its uncertainty, and whether it beats "no change".
#### Terminal
Terminal 1:
```bash
source .venv/Scripts/activate
make dashboard
```
Browser: open http://localhost:8501

Terminal 2 (empty-state check):
```bash
source .venv/Scripts/activate
DATA_DIR=/tmp/sf-empty DASHBOARD_PORT=8502 make dashboard
```
Browser: open http://localhost:8502
#### Watch for
- Port 8501: a sentence-style title, three KPI tiles, a chart with 60 days of observed flow flowing into a widening fan, and a dashed persistence line. A yellow banner appears if the data are provisional or stale.
- Port 8502: the "No forecast yet" message with no traceback.
#### Stop
`Ctrl+C` in Terminal 1 and Terminal 2.

### Implementation notes

**Built (Builder, 2026-09-25).**
- `streamforecast/dashboard.py` has three parts:
  - **Loaders:** `load_forecast`, `load_history` (the 60 days ending at d0), and `load_metrics`, which reads `models/metrics_<model_version>.json` for the displayed forecast's model, not the newest sidecar. Files are read through `st.cache_data(ttl=300)` keyed by path and modification time, so a new file shows up on the next rerun.
  - **Pure presentation functions**, unit-tested without Streamlit:
    - `headline()` returns rise, fall or hold, where hold means the t+3 median is within ±10% of the latest observation.
    - `about()` rounds for readability.
    - `notices()` builds the AC-5.5 banners.
    - `fan_frame()` adds the d0 anchor point and marks forecast days already past.
    - `fan_chart()` is the Altair layer.
    - `kpis()` returns exactly 3 tiles.
    - `limitation_caption()` is the RK6 caption.
  - **`render()`** runs only under `if __name__ == "__main__"`. Streamlit and `AppTest` both run the script as `__main__` (verified), so tests can import the module without rendering the page.
- **Imports:** only `streamlit`, `altair`, `pandas`, the standard library, `config`, and `paths`. No stage module, joblib, scikit-learn, or HTTP.
- **Tests:**
  - `tests/unit/test_dashboard_title.py` (10): headline cases and the ±10% boundary, rounding, banners, past-point marking.
  - `tests/integration/test_dashboard_app.py` (6, with `AppTest`): renders with data (title, exactly 3 metrics, chart spec with the `cfs` axis, 2 area bands and the dashed line); a 60-day history ending at d0; metrics matching the forecast's model, then "unavailable" once its sidecar is deleted; warnings with a frozen clock; the empty `DATA_DIR`; and a check that only loopback is open.
  - `tests/unit/test_boundaries.py` gained `test_dashboard_never_loads_models_or_calls_the_network`.

**Live check, not part of `make test`.** On the Stage 1–4 live data in a scratch `DATA_DIR`:
- **`AppTest` render, no exception:**
  - Title: "Flow expected to fall to about 5.3 cfs by Saturday".
  - KPIs: "Tomorrow's median (Thu Sep 24)" 8.7 cfs, "t+1 skill vs persistence" +0.32, "t+1 80% band coverage" 84%.
  - Info note: the 7.2 cfs value on 2026-09-23 is provisional.
  - Caption: "Known limitation: on flood days (above 150 cfs) only 25% of test outcomes fell inside the 80% band (28 days)…".
- **`make dashboard` on port 8599:** `/_stcore/health` returned `ok` and `/` returned HTTP 200. The server was then stopped.
- **Not verified:** how the page looks in a browser. That's for the Gate 2 smoke test.

**Deviations and interpretations.**
1. **pytest-socket and `AppTest`:** this is the Stage 0 risk. On Windows, the asyncio event loop `AppTest` uses creates a local socket pair, which `--disable-socket` blocks (`SocketBlockedError` in every `AppTest` test).
   - **Remedy:** `tests/integration/test_dashboard_app.py` carries `@pytest.mark.allow_hosts(["127.0.0.1"])`, not the broader `enable_socket`. Loopback may connect; every other host stays blocked, and the DNS block still applies.
   - `test_only_loopback_is_allowed_here` proves that connecting to an external IP from that module still raises `SocketConnectBlockedError`.
2. **Metrics unavailable (AC-5.1):** the page still shows 3 tiles. Skill and coverage read "unavailable", and a caption reads "metrics unavailable for model <version>".
3. **Headline wording (AC-5.3):**
   - "Flow expected to rise/fall to about X cfs by <weekday>", or "…hold near X cfs through <weekday>".
   - The weekday is the t+3 valid date.
   - X keeps 2 significant digits at 10 cfs and above (123.4 → 120; 8,180 → 8,200) and 1 decimal below 10.
4. **KPI formats (AC-5.4):**
   - Skill is signed with 2 decimals, and its help text explains "1 − MAE(model)/MAE(no change)".
   - Coverage is a percentage with the target stated.
   - Tomorrow's median shows the 80% range in its help text.
5. **RK6 caption:** under the KPIs, built from the sidecar's high-flow coverage and threshold ("treat the bands as too narrow during storms"). It is shown whenever metrics exist.
6. **Chart (AC-5.2):**
   - The fan starts from a d0 point at the latest observation, so the bands join the observed line.
   - A dashed orange persistence line; forecast points coloured by status, with past days grey (#9e9e9e) and labelled "past".
   - A linear y-axis titled `cfs`. No log toggle was added: at current flows (about 7 cfs) with a 95% band up to 70 cfs, the low-flow line is readable, and whether it looks readable in the browser is the Gate 2 check. There are no other controls.
7. **Banners (AC-5.5):**
   - Outdated: `st.warning`, exactly as the AC words it, when the forecast's `created_at_utc` in New York time is before `local_today()`.
   - Stale USGS data: `st.warning` when `stale_days > 0`.
   - Provisional or estimated latest value: `st.info`.

8. **Fix found at Gate 2 (the student's smoke test):** the legend said grey = past and blue = forecast, but the whole median line was blue.
   - **Cause:** only the median *points* were coloured by status. The line was one blue mark, and the legend always listed "past" even when no day was past.
   - **Fix:**
     - `median_segments()` splits the line into a grey past segment (the d0 anchor through the last past day) and a blue forecast segment, joined at that day.
     - `status_scale()` lists only the statuses present, and line and points share it.
   - **New tests:** two unit tests, plus app assertions that the median *line* layer is coloured by status (grey appears only when a day is past). The old chart fails them.
   - **Not verified:** the rendered look in a browser; the student re-checks it.

9. **Chart changes the student asked for at the smoke test (D33):**
   - **Log flow axis.** Every flow layer uses `scale type=log`. Plotted values are floored at 0.1 cfs, because a lower 95% bound can be exactly 0. Tooltips show the true values.
   - **Rain as an upside-down hyetograph in the flow plot.** This replaced a first version that put rain in a separate panel above the chart. The bars hang from the top on a reversed right-hand axis (`rain (mm)`, domain 0 to 3× the wettest day), and flow and rain have independent y scales. The flow axes have no gridlines, as the student asked. Blue bars are Open-Meteo rain for the 60 past days. Orange bars are the forecast rain for d0+1..d0+3, taken from the live features row, and shown only when that row is the displayed forecast's d0.
   - **Caption lists only what is drawn:** the grey key appears only when a forecast day is past, and the orange key only when forecast rain is shown.
   - **About the student's "no grey line" report:** the forecast was issued from data through 09-23 and viewed on 09-24, so no forecast day was past and nothing grey was due. The legend now correctly lists only "forecast". Previewing the stale case with `AS_OF_DATE=2026-09-25` shows the grey segment.
   - **New tests:** 4 unit tests (`rain_frame` window and forecast bars, forecast bars skipped when the features are newer than the forecast, the conditional caption, the log-floor clipping). The app tests now check the two-panel layout, the log scale on every flow layer, and the rain legend.
   - **Rendered and inspected as PNG** with `vl-convert`, installed into a scratch folder only (not a project dependency), on the live data for both today and a stale date. Checked: rain hangs from the top, there are no horizontal gridlines, the log axis runs 0.1–1,000 cfs, and the past segment is grey and labelled. The Streamlit page itself still needs the student's re-check in a browser.

**Verification.** `make lint` exits 0. `make test`: 350 passed, 1 skipped (the scheduler, Stage 6).

### Review findings

See the Tester review under Stage 6: TF4.

---

## Stage 6 — Containers

### Goal
One image and two Compose services (a pipeline scheduler and the dashboard) sharing a named volume, configured by environment variables, running as non-root, with a dashboard healthcheck.

### Acceptance criteria
- [x] AC-6.1 The `Dockerfile` uses `python:3.12-slim`, installs `requirements*.txt` in a layer before copying source (cache-friendly), installs `make`, copies the code, and sets `DATA_DIR=/data`.
- [x] AC-6.2 `.dockerignore` excludes `data/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.git/`, `*.joblib`, and `docs/transcripts/`.
- [x] AC-6.3 `docker-compose.yml` defines `pipeline` (`python -m streamforecast.scheduler`) and `dashboard` (`streamlit run streamforecast/dashboard.py --server.address 0.0.0.0 --server.port 8501`, port `8501:8501`), both mounting the named volume `sfdata` at `/data` and both taking env vars with `${VAR:-default}` defaults.
- [x] AC-6.4 *(Tester: re-verified 2026-09-25 on Docker 29.7.2, see Stage 6 Review findings)* Both containers run as a non-root user (`docker compose exec dashboard id -u` ≠ 0), and that user can write `/data` on a fresh named volume.
- [x] AC-6.5 *(Tester: re-verified 2026-09-25 on Docker 29.7.2, see Stage 6 Review findings)* The dashboard healthcheck calls `http://localhost:8501/_stcore/health` with Python `urllib` (no curl in slim), and `docker compose ps` shows `healthy`.
- [x] AC-6.6 `streamforecast/scheduler.py` runs the stages as subprocesses in order, continues to the next stage if one fails, runs train only when needed (D5), sleeps `RUN_INTERVAL_HOURS`, and exits within 10 s on SIGTERM (`docker compose down` does not hit the kill timeout).
- [x] AC-6.7 *(Tester: re-verified 2026-09-25 on Docker 29.7.2, see Stage 6 Review findings)* Data persist: after `docker compose down` and `docker compose up -d`, the previous forecast file is still in the volume and the dashboard shows it before the next run finishes.
- [x] AC-6.8 *(Tester: re-verified 2026-09-25 on Docker 29.7.2, see Stage 6 Review findings)* Tests run inside the container: `docker compose run --rm pipeline make test` exits 0.
- [x] AC-6.9 `make pipeline` runs `python -m streamforecast.scheduler --once`: one pass of ingest → features → train (only if no checkpoint exists or the newest is older than `RETRAIN_DAYS`) → forecast, then exit.
- [x] AC-6.10 **Hard vs soft failures (D26).**
  - **Soft (the run continues):**
    - an ingest source failure: ingest exits 1, but the scheduler still runs Features and Forecast on the existing files, and an MRMS failure never changes ingest's exit (AC-1.11);
    - a Features failure: Forecast then sees an old features file;
    - a Train failure: Forecast uses the previous published checkpoint.
  - **The gate is Forecast:** its freshness checks (AC-4.5, 4.7, 4.9, 4.10) decide whether stale or mixed inputs are acceptable. Forecast never publishes from USGS data more than `MAX_STALE_DAYS` old, or from forecast weather not fetched today.
  - **No stage is skipped** because an earlier one failed; there is no separate status file.
  - **Exit status:** after each pass, the scheduler logs one summary line, e.g. `pass summary: ingest=1 features=0 train=skipped forecast=0`. `make pipeline` (`--once`) exits with **Forecast's** exit code (0 = forecast written or no checkpoint yet; 1 = refused or failed). A failed ingest source therefore shows in the summary and logs but fails the pass only if it makes Forecast refuse.

### Proposed changes
- `Dockerfile`: slim image, non-root `app` user (uid 1000), `/data` created and chowned.
- `.dockerignore`: keeps data, venvs, caches, and git out of the image.
- `docker-compose.yml`: two services, named volume `sfdata`, healthcheck, env defaults.
- `streamforecast/scheduler.py`: loop plus a SIGTERM handler (`threading.Event().wait` instead of `sleep`).
- `tests/unit/test_scheduler.py`: stage order, retrain decision, continue-on-failure, SIGTERM stop (subprocess mocked).
- `.github/workflows/ci.yml`: add a `docker build` job (optional cheap extra).
- `README.md`: container section.

### Architecture / boundaries
- The scheduler may read `data/models/` (checkpoint age) and may start stage subprocesses. It must never import stage modules (AC-0.5).
- Containers share state only through the `sfdata` volume. The dashboard mounts it too; the dashboard code does no writes (AC-5.1).

### Risks for this stage
RK14 (volume ownership). The first container start performs the full backfill and first training, which may take a few minutes; the dashboard shows the empty state until then. If the agent's environment has no working Docker, the Builder marks AC-6.4 through AC-6.8 "not verified" and the student verifies them at Gate 2.

### Automated tests
- unit `test_scheduler_runs_stages_in_order` → AC-6.6.
- unit `test_scheduler_retrain_only_when_stale` → AC-6.6.
- unit `test_scheduler_continues_after_failure` → AC-6.6.
- unit `test_scheduler_sigterm_stops_quickly` → AC-6.6.
- unit `test_scheduler_once_exit_code` → AC-6.9.
- unit `test_scheduler_soft_failures` (ingest exits 1 and Forecast still runs; Features fails and Forecast still runs; the pass exit equals Forecast's exit; the summary line lists every stage) → AC-6.10.
- AC-6.1 through AC-6.5, AC-6.7, and AC-6.8 are verified by the smoke test commands below (and by the optional CI build job for AC-6.1).

### Manual Smoke Test
#### What we're proving
The whole system runs from containers: the pipeline fills the shared volume, the dashboard turns healthy and shows the forecast, and data survive a restart.
#### Terminal
Terminal 1 (optional first, local one-pass check of the same scheduler):
```bash
source .venv/Scripts/activate
make pipeline; echo "exit=$?"
```

Terminal 1:
```bash
docker compose build
docker compose up
```
Terminal 2:
```bash
docker compose ps
docker compose logs pipeline --tail 30
docker compose exec dashboard id -u
MSYS_NO_PATHCONV=1 docker compose exec dashboard ls -l /data/raw/usgs /data/features /data/models /data/forecasts
```
Browser: open http://localhost:8501

Terminal 2 (after the first forecast appears):
```bash
docker compose down
docker compose up -d
MSYS_NO_PATHCONV=1 docker compose exec dashboard ls -l /data/forecasts
docker compose run --rm pipeline make test
docker compose down
```
#### Watch for
- The pipeline logs show `ingest` → `features` → `train` → `forecast` in order, each finishing with its `wrote ...` line, then `sleeping 24h`.
- `docker compose ps` shows `dashboard` as `(healthy)` within about a minute.
- `id -u` prints `1000`, not `0`.
- The volume listing shows files in all four directories.
- The browser shows the fan chart.
- After `down`/`up -d`, the same forecast file is still listed.
- The in-container `make test` ends with all tests passing.
- `docker compose down` returns in a few seconds (clean SIGTERM).
#### Stop
`docker compose down` (add `-v` only if you intend to delete the data volume).

### Implementation notes

**Built (Builder, 2026-09-25).**
- **`streamforecast/scheduler.py`:**
  - `run_stage` launches `python -m streamforecast.<stage>`, polls every 0.5 s, and on a stop request terminates the child (then kills it after 5 s).
  - `needs_training` looks at the newest *published* checkpoint (model plus sidecar) and uses the age from its `<ts>`.
  - `run_pass` runs ingest → features → train (only if there's no checkpoint or it is older than `RETRAIN_DAYS`) → forecast. It never stops early after a failure, and a stop request skips the remaining stages. It logs `pass summary: …` and returns Forecast's exit code.
  - `serve` runs a pass on start, then `sleeping 24h` via `Event.wait`.
  - SIGTERM and SIGINT handlers set the stop event.
  - `--once` runs one pass and returns Forecast's code.
- **`Dockerfile`:**
  - `python:3.12-slim`, with `make` installed.
  - The requirements are copied and installed in their own layer, before the source.
  - The code is installed with `pip install --no-deps -e .`, editable so fixture mode finds `/app/tests/fixtures` (Stage 1 note 13).
  - Non-root user `app` (uid 1000). `/data` is created and chowned before `VOLUME /data`, so a fresh named volume is seeded owned by `app` (RK14).
  - `DATA_DIR=/data`, headless Streamlit, and CMD in exec form, so Python is PID 1 and receives SIGTERM.
- **`.dockerignore`:** `data/`, `.venv/`, `venv/`, `__pycache__/`, `.pytest_cache/`, `.git/`, `*.joblib`, `docs/transcripts/`, `*.egg-info/`, `build/`, `*.tmp`, `.env`, `.claude/`.
- **`docker-compose.yml`:**
  - A shared `x-settings` block passes every variable as `${VAR:-default}`, with `DATA_DIR` fixed to `/data`.
  - `pipeline` runs the scheduler, with `restart: unless-stopped` and `stop_grace_period: 15s`.
  - `dashboard` runs `streamlit run … --server.address 0.0.0.0 --server.port 8501`. Its healthcheck calls `/_stcore/health` through Python `urllib` every 15 s, with a 30 s start period.
  - Both mount the named volume `sfdata` at `/data`.
- **Also:** CI job `docker-build`, a README Pipeline/Containers section, and `tests/unit/test_scheduler.py` (15 tests).

**Verification on Docker Desktop 29.7.2 / Compose v5.4.0 (all run by the Builder, 2026-09-25):**

| AC | Evidence |
|---|---|
| 6.1, 6.2 | `docker compose build` succeeded. A rebuild after code edits reused the cached dependency layers, so only the COPY and install steps ran. |
| 6.3 | Both services came up from `docker compose up -d` on a fresh volume. |
| 6.4 | `docker compose exec dashboard id -u` → `1000`; the pipeline → `1000`. `stat /data` → owner `app`. The pipeline wrote into the fresh volume. |
| 6.5 | `docker compose ps` → `dashboard  Up 8 seconds (healthy)`. `curl /_stcore/health` through the host port → `ok`. |
| 6.6 | The first pass logged ingest → features → `train: needed (no checkpoint)` → train → forecast, then `pass summary: ingest=0 features=0 train=0 forecast=0`, then `sleeping 24h`. `docker compose stop pipeline` during ingest took **0.8 s**, exit code **0**, `received SIGTERM; stopping`. A full `docker compose down` took **1.6 s**. |
| 6.7 | After `down` and `up -d`, `/data/forecasts` still held `forecast_2026-09-23_20260925T012552Z.csv` (01:25:52). The dashboard was healthy and headless `AppTest` inside the container rendered "Flow expected to fall to about 5.3 cfs by Saturday" from the volume. The next pass logged `train: skipped (newest checkpoint is 0 days old; limit 7)` and added a second forecast. |
| 6.8 | `docker compose run --rm pipeline make test` → **366 passed** on Python 3.12.14 as `uid=1000(app)`. `make lint` passes in the container too. |
| 6.9 | Local `make pipeline` (scratch `DATA_DIR`) ran the pass with training skipped, logged `pass summary: ingest=0 features=0 train=skipped forecast=0`, and exited 0. |
| 6.10 | `test_scheduler_soft_failures` and `test_scheduler_continues_after_failure` (failed ingest or features still run Forecast; the pass exit equals Forecast's exit; the summary lists every stage). |

The Builder removed the stack and its volume afterwards (`docker compose down -v`), so the student's smoke test starts from a fresh volume, as the plan expects.

**Deviations and interpretations.**
1. **Dashboard host port:** `"${DASHBOARD_PORT:-8501}:8501"` instead of a fixed `8501:8501`. The default is unchanged, and it reuses the documented `DASHBOARD_PORT`. It was needed because the student's local `make dashboard` was holding 8501 during verification, so the container check ran on 8599.
2. **Editable install in the image** (`pip install -e .`), so fixture mode's `tests/fixtures` resolves inside the container (D17). `tests/` is copied into the image so `make test` runs there (AC-6.8).
3. **Stopping mid-pass:** a stop request ends the running stage (SIGTERM, then SIGKILL after 5 s) and skips the rest (`stopped` in the summary). `--once` then exits 1 unless Forecast finished.
4. **Training age** comes from the checkpoint's `<ts>`, not file mtime, and only checkpoints with a sidecar count (AC-3.4). The age must be *more than* `RETRAIN_DAYS` (7) days; exactly 7 days doesn't retrain.
5. **Smoke test (commands only):** the `docker compose exec dashboard ls -l /data/...` line gets `MSYS_NO_PATHCONV=1`. In Git Bash, `/data/...` is otherwise rewritten to a Windows path (`C:/Program Files/Git/data/...`) before Docker sees it. The prefix is harmless on Linux and macOS.
6. **AC-0.6 is now ticked.** Lint and all tests pass on Python 3.12 (Linux) inside the image, and the workflow runs the same targets. A GitHub Actions run itself is still pending, because nothing has been pushed.

**Verification.** `make lint` exits 0. `make test`: 366 passed, 0 skipped (every stage module now exists, so no boundary check is skipped).

### Review findings

**Tester review, all stages (2026-09-25).** Status: reported, **awaiting the student's triage (Gate 3)**. Nothing has been fixed, and no checkbox has been changed. Earlier stages' Review findings sections point here.

**Environment:**
- Windows 11, the repo's `.venv` (Python 3.13.12).
- `make lint` exit 0: black reported 38 files unchanged, and flake8 printed nothing.
- `make test` exit 0: 366 passed, 4 warnings, in 21.6 s.
- **Not verified:**
  - **Docker.** `docker info` gave `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`.
  - **The README install in a fresh venv.** pip failed with `OSError: [Errno 2] No such file or directory: ...\.venv\Lib\site-packages\pyarrow.libs\msvcp140_atomic_wait-...dll`. That is Windows' 260-character path limit under the long scratch path, not a project defect. It needs a re-run from a short path.

**Real-data result** (`make coverage` on `./data`, model 20260925T003936Z; exit 0, every AC-3.9 gate passes):

| h | test n | cov80 | cov95 | n_high | cov80 high | cov95 high | cal80 | cal95 | skill | wet skill | wet cov80 | dry skill | dry cov80 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 625 | 0.842 | 0.957 | 28 | 0.250 | 0.643 | 0.796 | 0.946 | 0.316 | 0.087 | 0.387 | 0.712 | 0.927 |
| 2 | 624 | 0.838 | 0.960 | 28 | 0.179 | 0.679 | 0.796 | 0.946 | 0.350 | 0.265 | 0.517 | 0.687 | 0.944 |
| 3 | 624 | 0.795 | 0.963 | 28 | 0.143 | 0.786 | 0.795 | 0.946 | 0.352 | 0.300 | 0.536 | 0.684 | 0.926 |

Wet-window 95% coverage from the sidecar: 0.774 (h=1, n=62), 0.864 (h=2, n=118), and 0.928 (h=3, n=166).

| ID | Severity | What's wrong | Evidence | Recommended fix |
|---|---|---|---|---|
| TF1 | Major | A response that is valid JSON but has the wrong shape crashes ingest. `main()` catches only `IngestError`, so the remaining sources are never attempted, and a traceback escapes. That breaks AC-1.6 ("still attempts the other sources"), AC-1.12 ("a failure is handled as a source failure"), and AC-1.11 (an MRMS failure must not change ingest's exit status). | Scratch probe with `responses`, offline. A USGS feature without `properties.time` gave `UNCAUGHT KeyError: 'time'` and wrote no files at all (`ingest.py:242`). Open-Meteo `daily.precipitation_sum: null` gave `UNCAUGHT TypeError: object of type 'NoneType' has no len()`, with only the usgs file written, so weather_leads, weather_fc, and mrms were skipped (`ingest.py:272`). An MRMS `data` list containing `None` gave `UNCAUGHT TypeError: argument of type 'NoneType' is not iterable` (`ingest.py:339`), so the process exits non-zero on an MRMS failure. (A string record is caught, but only by luck: `"date" not in "not-a-record"` is a substring test.) The scheduler still continues, because ingest is a subprocess. | Treat `KeyError`, `TypeError`, and `ValueError` from parsing or validating one source as that source's failure: catch them next to `IngestError` at `ingest.py:675`, or check the types in `check_openmeteo` and `parse_usgs`. Add the three probe cases as tests. |
| TF2 | Major (D23 triage; not a blocker) | On real data, high-flow and wet-window coverage are far below nominal: high-flow 80% band 0.14–0.25, 95% band 0.64–0.79 (n=28); wet 80% band 0.39–0.54. AC-3.8 and RK6 ask the Tester to record this. The AC-3.9 gate passes. | The `make coverage` table above. | The student triages this under D23: diagnose first, then accept and document it, or use the one allowed alternative. Do not widen tolerances or recalibrate on test. The dashboard's RK6 caption already warns users. |
| TF3 | Minor | AC-0.6 is ticked, but GitHub Actions has never run. The Builder's own Stage 0 note says to tick it only after the first green run. | Stage 0 notes; nothing has been pushed. | Untick AC-0.6 until a green Actions run exists. |
| TF4 | Minor (plan conflict, for the student) | The KPI label reads "Tomorrow's median (Thu Sep 24)", but under D4 t+1 is **today** (d0 = yesterday). AC-5.4's wording ("tomorrow's median (t+1)") contradicts D4. | `dashboard.py:387`; Stage 5 notes: viewed on 09-24, the label shows Thu Sep 24. | The student decides the wording ("Today's median", "t+1 median", or show t+2). Then the Architect amends AC-5.4. |
| TF5 | Minor | Only direct dependencies are pinned. Transitive packages (pyarrow, altair, scipy, protobuf, …) resolve at install time, so builds are not reproducible (Reproducibility check). | Stage 0 notes. A fresh `make install` resolved about 60 packages that are not listed in any file. | Add a `constraints.txt` from `pip freeze` and use `-c constraints.txt` in `make install` and the Dockerfile, keeping AC-0.10's direct-pin files. |
| TF6 | Minor (weak test) | `test_http_request_is_blocked` passes without pytest-socket. It exercises only the conftest DNS stub, so it would pass even if `--disable-socket` were removed. | `pytest tests/unit/test_network_blocked.py -o addopts="-p pytester"` gave `1 failed, 2 passed`: only `test_raw_socket_is_blocked` fails. `tests/unit/test_network_blocked.py:10`. | Request an IP-literal URL (no DNS), e.g. `http://93.184.215.14`, so the test needs pytest-socket. |
| TF7 | Minor (weak test) | `test_feature_availability_matrix` builds the merged weather by hand, so it never runs `load_raw_weather` or the D28 tie rule. Its constant markers can't detect a read from the wrong date. | `tests/unit/test_features_build.py:217`. A scratch probe through `features.run` on raw CSVs (same-stamp hist=1 and fc=4, plus an older fc file with marker 9) gave the expected markers on the train, cal, test, fc-window, and live rows, and never 9. The **behaviour is correct**; the test is weak. | Build the matrix test from raw files, as the probe does. |
| TF8 | Minor (test gaps) | Three behaviours are implemented but untested: (a) the AC-3.8 WARNING when the coverage check fails (only the no-skill warning is tested); (b) the AC-1.5 timeout argument; (c) the AC-3.4 order, checkpoint before sidecar. | Code read: `train.py:420-421`, `ingest.py:160-162`, `train.py:461-463`. `grep -rn "coverage check" tests/`, `grep -rn "http_timeout_s\|timeout=settings\|HTTP_TIMEOUT" tests/` (excluding `test_config`), and an order search in `test_train_publish.py` all return nothing. | Add three small unit tests. |
| TF9 | Minor (weak test) | `test_scheduler_sigterm_stops_quickly` calls the handler directly: no signal is sent and no exit time is measured. The AC-6.6 "exits within 10 s on SIGTERM" rests only on the Builder's Docker run. | `tests/unit/test_scheduler.py:117`. | Spawn `python -m streamforecast.scheduler` with a stub runner via an env var, or at least re-run the Docker check at Gate 3. On Windows `SIGTERM` is emulated, so a CI-only (Linux) test is fine. |
| TF10 | Nit | The triple intersection `set(tr) & set(cal) & set(te)` is always empty when the sets are pairwise disjoint, so the assertion is vacuous. | `tests/unit/test_train_split.py:38`. The ordering assert on the line above does the real work. | Check the pairwise intersections. |
| TF11 | Nit (Architect) | Several AC texts are stale against later decisions: AC-1.7 (09-23; D27 says 09-24), AC-1.9 ("23/24/25 hours"; D31), AC-2.5 ("first ≥ 25 mm day"; D32), AC-5.2 ("labelled `cfs`"; D33 log axis), and the Environment table's `make install` (D30). | The plan text vs the Decisions log. | The Architect amends the AC wording to match the decisions. |
| TF12 | Nit | MRMS logs "fetched N rows" twice for a single chunk. | Fixture run: two near-identical `ingest mrms: fetched 12 rows 2026-09-12..2026-09-23` lines (the second adds `-> <path>`) (`ingest.py:616` and `_wrote`). | Log per chunk at DEBUG, or drop the `_wrote` duplicate. |
| TF13 | Nit | `.dockerignore` excludes `docs/transcripts/`, but the transcripts live at `docs/gi23_*.txt`, so they are copied into the image (along with `.vscode/`). | `.dockerignore`; `ls docs/`. | Add `docs/*.txt` (or `docs/`) and `.vscode/`. |
| TF14 | Minor (weak test) | No test shows that `train()` fits on train rows only. `test_fit_uses_train_rows_only` calls `fit_horizon` directly on `split_by_date` output, and `test_quantiles_ignore_test_rows` perturbs only test-period targets. So a `train()` that fit on train plus calibration rows would still pass the suite (AC-3.2). | Scratch probe: +5 on calibration-period `y_1`, then `train.train()` twice. The h=1 test predictions are identical (`True`) while the calibration quantiles change (`True`). The **behaviour is correct** (`train.py:386`); only the test is missing. | Turn the probe into a test on `train.train()`. |
| TF15 | Minor (weak test) | The AC-0.8 grep matches only `date(time)?.today(`. It would pass code that used `datetime.now().date()` or `pd.Timestamp.today()`. | `tests/unit/test_local_today.py`. A broader Tester grep (`datetime.now()`, `utcnow`, `Timestamp.now/today`, `time.time()`) over `streamforecast/` found nothing, so there is **no current violation**. | Widen the regex. |

| TF16 | Minor | A checkpoint that loads but whose contents are broken escapes Forecast as a traceback. It should be an ERROR naming the file (AC-4.6). `load_checkpoint` checks key names, the sklearn version, and horizons, but not the values; `run()` catches only `Refusal`. | `test_loadable_but_broken_checkpoint_refuses_cleanly` (xfail, strict), run with `--runxfail`: `AttributeError: 'str' object has no attribute 'predict'`, `KeyError: "['no_such_column'] not in index"`, `KeyError: '0.025'`. Exit is still non-zero and no file is written, so it is safe, but the error doesn't say which file. | Validate `feature_columns` ⊆ the features columns, the quantile keys, and `hasattr(model, "predict")` in `load_checkpoint`, or wrap `make_forecast` errors in a `Refusal` that names the file. |

**Checkbox changes (approved by the student at Gate 3, applied 2026-09-25):**
- Untick AC-0.6 (TF3).
- Mark AC-1.6, AC-1.11, and AC-1.12 Partial (TF1).
- AC-6.4, 6.5, 6.7, and 6.8 stay ticked on the Builder's evidence only. The Tester could not verify them (no Docker daemon).

**Edge-case run (Gate 3, 2026-09-25).** The student asked for a break-it pass, with no implementation fixes. The new file `tests/integration/test_tester_edge_cases.py` has 23 passing tests and 6 strict xfails (TF1 ×3, TF16 ×3). A strict xfail turns into a failure once its defect is fixed, so the marker must be removed with the fix. `make test`: 389 passed, 6 xfailed.

| Case | What happens | Test |
|---|---|---|
| Empty USGS series | Ingest WARNING, exit 0. Features: "no raw USGS files", exit 1. Forecast: "no features file", exit 1, nothing written. | `test_empty_usgs_series_leads_to_clean_refusals` |
| Provisional-only series | Forecast is written and flagged `latest_obs_provisional`. | `test_all_provisional_series_forecasts_and_flags` |
| Ice day, no value | Dropped as "missing", d0 moves back a day, forecast written with `stale_days` 1. | `test_ice_affected_latest_day_without_value` |
| Ice day, `ICE;ESTIMATED` | Kept and flagged estimated. | `test_ice_affected_estimated_value_is_kept_and_flagged` |
| Ice day, `ICE` only | Kept and **not** flagged. Only ESTIMATED is checked (documented behaviour; the plan names only ESTIMATED). | `test_ice_qualifier_alone_is_not_flagged` |
| 1-day gap at d0−1 | Filled from d0−2, `flow_filled` True, no issue date on the gap day, forecast written. | `test_one_day_gap_before_d0_is_filled_and_forecast` |
| 10-day gap before d0 | Lags stay NaN; "flow lags incomplete at 2026-09-23", exit 1. | `test_ten_day_gap_before_d0_refuses` |
| Zero flow at d0 | Kept. Persistence 0, bands finite, ordered, ≥ 0. | `test_zero_latest_flow_gives_nonnegative_bands` |
| Negative or −999999 flow at d0 | Dropped and counted ("1 negative" / "1 sentinel"); d0 moves back a day. | `test_negative_latest_flow_is_dropped_and_d0_moves_back`, `test_sentinel_latest_flow_is_dropped` |
| 50,000 cfs flood (the model saw about 20 cfs) | Kept unclipped. Median within e^±3 of 50,000; bands finite and ordered. | `test_flood_far_above_training_range` |
| Forecast endpoint returns 429 plus an error body | 3 attempts, "HTTP 429 after 3 attempts", no new weather_fc; Forecast refuses "forecast weather is stale (fetched 2026-09-24)". | `test_rate_limited_forecast_then_forecast_refuses` |
| All 3 Open-Meteo sources answer in GMT | 3 "timezone mismatch" errors, no weather files. The GMT alignment case is still covered by `test_gmt_fixture_breaks_alignment`. | `test_every_openmeteo_source_rejects_a_foreign_timezone` |
| Truncated or zero-byte checkpoint | "cannot load <file>", exit 1. | `test_damaged_checkpoint_file_refuses` |
| Loadable but broken checkpoint | Traceback (TF16). | xfail `test_loadable_but_broken_checkpoint_refuses_cleanly` |
| Disordered quantiles | Refused, "bands out of order"; nothing written. | `test_disordered_quantiles_are_refused_not_published` |
| USGS 3 or 7 days stale, fresh weather | Refused, "is N days stale (limit 2)". | `test_several_days_stale_refuses_even_with_fresh_weather` |
| Band ordering | All 3 × 3,018 rows (a 3,012-day synthetic record plus 6 rows with flows of 0, 0.01, 0.22, 8,180, 50,000, and 1e6 cfs) are ordered, ≥ 0, and finite. | `test_band_ordering_on_every_row_of_a_long_record` |
| Leakage through files | Chantal raw files cut at d0 (flow) and d0+3 (weather) give an identical row d0, apart from targets and `precip_f*`, for 4 dates. An in-memory Tester probe on the real `data/raw` found **0 leaking rows** across 9 issue dates from 2018 to 2026. | `test_truncating_the_future_changes_only_targets_and_forecast_rain` |
| Wrong-shape JSON in ingest | Traceback; later sources skipped (TF1). | xfail `test_wrong_shape_response_fails_only_that_source` |

**Container check (Tester, 2026-09-25).** Docker Desktop 29.7.2 with Compose v5.4.0. It ran from the copy, so the Compose project was `sfcheck` with its own volume `sfcheck_sfdata`; the student's `ids706-streamforecast_sfdata` was not touched. Afterwards the stack was removed with `down -v` and the copy deleted.

| AC | Result | Evidence |
|---|---|---|
| 6.1 | Met | `docker compose build` exit 0 in 1 min 44 s; `python:3.12-slim`, Python 3.12.14; image 1.07 GB. |
| 6.2 | Met | Build context was 575 kB, while the copy held a 559 MB `.venv` and 2.8 MB `data/`. `/app` in the image has no `data`, `.venv`, `.git`, `.pytest_cache`, `__pycache__`, `*.joblib`, or `.env`. Some leaks remain (TF13). |
| 6.3 | Met | Both services came up on the named volume `sfcheck_sfdata` at `/data`. A shell `LOG_LEVEL=DEBUG RUN_INTERVAL_HOURS=6` and a `.env` `MAX_STALE_DAYS=5` both reached `docker compose config`. |
| 6.4 | Met | `id` → `uid=1000(app)` in both containers. `/data` and its subdirectories are `app:app 755`, and the pipeline wrote every directory on a fresh volume. |
| 6.5 | Met | `docker compose ps` → `Up 7 seconds (healthy)`. Health log: exit 0, failing streak 0. The host port returned HTTP 200. |
| 6.6 | Met | `down` took 1.5 s. `stop pipeline` during the Forecast stage took 1.4 s and exited 0, with `received SIGTERM; stopping`. TF9 is now only a test weakness. |
| 6.7 | Met | After `down` and `up -d`, the previous `forecast_…002815Z.csv` was still in the volume. The dashboard was healthy, a headless render inside the container raised no exception, and the next pass logged `train: skipped` and added a new forecast. |
| 6.8 | Met | `docker compose run --rm pipeline make test` → 389 passed, 6 xfailed. `make lint` → exit 0. |
| Config error | OK | With `MRMS_ENABLED=yes`, the container prints `error: invalid MRMS_ENABLED='yes': expected 0 or 1` and exits 1. |

| ID | Severity | What's wrong | Evidence | Recommended fix |
|---|---|---|---|---|
| TF21 | Minor | Both services set `build: .` with the fixed tag `streamforecast:latest`. Compose therefore builds the same image twice in parallel, and every checkout or project writes to one global tag: this check, run from another folder, re-tagged the student's image. | `build.log`: both `#15 [dashboard] exporting to image` and `#14 [pipeline] exporting to image`. | Give only `pipeline` a `build:` and have `dashboard` use the same `image:`, or drop `image:` so Compose names the image per project. |
| TF13 (update) | Nit | Besides the transcripts, `.vscode/`, `.github/`, and any `*.log` in the repo root are copied into the image. | `ls -A /app` in the image listed `.github`, `.vscode`, `build.log`, `install.log`, and others (the logs were the Tester's own files, but nothing excludes them). | Add `.vscode/`, `.github/`, `*.log`, and `docs/*.txt` to `.dockerignore`. |
| TF22 | Nit | The README says every setting in `.env.example` can be overridden from `.env`, but `DATA_DIR` set there is silently ignored: Compose pins `/data`, which is correct. | `.env` containing `DATA_DIR=/elsewhere` → `docker compose config` still shows `DATA_DIR: /data`. | README: "…except `DATA_DIR`, which is always `/data` in containers." |
| TF23 | Nit | The dashboard mounts the volume read-write, although the boundaries say it never writes. | `docker compose exec dashboard touch /data/.w` succeeded. | Mount it as `sfdata:/data:ro` in the dashboard service. |
| TF24 | Nit | The pipeline container shows port `8501/tcp` because the image's `EXPOSE` applies to both services. It is harmless. | `docker compose ps`: `sfcheck-pipeline-1 … 8501/tcp`. | Leave it, or document it. |
| TF25 | Nit | When SIGTERM stops a running stage, the pass summary logs the raw signal code instead of `stopped`, as the Builder's deviation note 3 describes. | `pass summary: ingest=0 features=0 train=skipped forecast=-15`. | Record `stopped` when the stop event is set and the child ended from a signal. |

TF4 is confirmed live: on Fri Sep 25 the tile read "Tomorrow's median (Fri Sep 25)".

**Outcomes after Gate 3 (Tester, 2026-09-28).** Triage by the student; the Tester applied only the approved fixes.
- `main` was pushed to `origin` (https://github.com/inmang13/IDS706-StreamForcast), and the first GitHub Actions run passed ("CI", main, success, run 36494727168). AC-0.6 stays unticked until the student reticks it.
- The fixes are on branch `tester-gate3-fixes`.

**Verification after the fixes:**
- `make lint` → exit 0 (black: 39 files unchanged; flake8 silent).
- `make test` → 410 passed. The 6 former strict xfails now pass.
- `docker compose build` → exit 0, with a single `exporting to image` step.

**Mutation check.** Each approved fix was temporarily reverted, or the behaviour broken, and its new tests failed every time:

| Fix | Mutation | Tests that failed |
|---|---|---|
| TF1 | Catch `IngestError` only | 3 |
| TF16 | Remove the wrap | 3 |
| TF8b | Hard-code `timeout=30` | 1 |
| TF8c | Write the sidecar first | 1 |
| TF14 | Fit on train plus calibration rows | 1 |
| TF8a | Drop the coverage WARNING | 1 |
| TF4 | Restore the old label | 1 |

**Container re-check.** Project `-p sfverify`; torn down with `down -v`, and the student's volume was untouched.
- With no image present, `docker compose up -d` built once and started both services, and the dashboard became `(healthy)`.
- The dashboard mount is `rw=false`, and `touch /data/.w` → `Read-only file system`. The pipeline mount stays `rw=true`.
- A headless render inside the dashboard container raised no exception, with the tile reading `t+1 median (Mon Sep 28)`.
- `/app` contains no `.vscode`, `.github`, `*.log`, or `docs/*.txt`.
- Side note (not a regression): the pipeline service still logs one "pull access denied" line before it builds, because it has both `image:` and `build:`. `pull_policy: build` on `pipeline` would silence it; not applied, since it was not in the approved list.

| ID | Severity | Triage | Outcome | Evidence |
|---|---|---|---|---|
| TF1 | Major | Fix | **Fixed.** `ingest.main()` catches `KeyError`, `TypeError`, and `ValueError` per source, next to `IngestError`, and logs `<source>: malformed response (<Error>: …); no file written`. MRMS still never changes the exit status. AC-1.6, 1.11, and 1.12 re-ticked. | `test_wrong_shape_response_fails_only_that_source` ×3 now pass, asserting the ERROR line, that the other sources were written, and the exit status. |
| TF2 | Major | Don't fix (D23) | **Closed, accepted as D35.** High-flow 80% band 0.14–0.25; wet 80% band 0.39–0.54 (RK6, RK3c). The dashboard caption already warns users. No retuning, and tolerances are unchanged. | The `make coverage` table in this section; D35. |
| TF3 | Minor | Don't fix | **Open, by design.** AC-0.6 stays unticked; the student reticks it. CI is now green on `main`. | Actions run 36494727168: success. |
| TF4 | Minor | Student decision | **Changed as decided.** The tile label is now `t+1 median (<valid date>)` (`dashboard.py:387`). Recorded as D34. | `test_app_renders_with_data` asserts `t+1 median (Thu Sep 24)`. |
| TF5 | Minor | Don't fix | Accepted (out of scope). | — |
| TF6 | Minor | Don't fix | Accepted (behaviour confirmed). | — |
| TF7 | Minor | Don't fix | Accepted (behaviour confirmed). | — |
| TF8 | Minor | Fix | **Fixed.** Added three tests: `test_warns_when_coverage_check_fails` (AC-3.8), `test_every_request_carries_the_configured_timeout` (AC-1.5), and `test_publish_writes_checkpoint_before_sidecar` (AC-3.4). | Each fails under its mutation (above). |
| TF9 | Minor | Don't fix | Accepted. A real SIGTERM was verified at 1.4 s (container check). | — |
| TF10 | Nit | Don't fix | Accepted. | — |
| TF11 | Nit | Don't fix | Left for the Architect. | — |
| TF12 | Nit | Don't fix | Declined. | — |
| TF13 | Nit | Fix | **Fixed.** `.dockerignore` adds `docs/*.txt`, `.vscode/`, `.github/`, and `*.log`. | `ls -A /app` and `ls /app/docs` in the rebuilt image. |
| TF14 | Minor | Fix | **Fixed.** Added `test_train_fits_on_train_rows_only` on `train.train()`. | It fails when `train()` fits on train plus calibration rows. |
| TF15 | Minor | Fix | **Fixed.** The AC-0.8 grep also catches a naive `datetime.now()`, `.utcnow(`, and `Timestamp.now/today(`, with 11 parametrized regex cases. Only the test file itself is excluded, since it holds the examples. | `tests/unit/test_local_today.py`: 15 passed. The code base has no offenders. |
| TF16 | Minor | Fix | **Fixed.** Forecast turns an `AttributeError`, `KeyError`, `TypeError`, or `ValueError` from a loaded checkpoint into a refusal: `ERROR forecast <model file> is unusable: …`, exit 1, nothing written (AC-4.6). | `test_loadable_but_broken_checkpoint_refuses_cleanly` ×3 now pass. |
| TF17 | Major | Fix (first) | **Fixed.** Branch renamed `master` → `main`; base commit `92d0d45` pushed to `origin/main`; README Install now starts with `git clone https://github.com/inmang13/IDS706-StreamForcast.git` and `cd IDS706-StreamForcast`. | `git push -u origin main` → `[new branch] main -> main`; CI green. |
| TF18 | Minor | Don't fix | Declined. | — |
| TF19 | Minor | Don't fix | Declined. | — |
| TF20 | Nit | Don't fix | Declined. | — |
| TF21 | Minor | Fix | **Fixed.** Only `pipeline` has `build:`. `dashboard` uses `image: streamforecast:latest` with `pull_policy: never`, so Compose never looks for it on Docker Hub. | A fresh `up` with no image present built once, and both services started. `docker compose build` shows a single export. |
| TF22 | Nit | Don't fix | Declined. | — |
| TF23 | Nit | Fix | **Fixed.** The dashboard mounts `sfdata:/data:ro`. | `rw=false`; `touch` → `Read-only file system`; dashboard healthy and renders. |
| TF24 | Nit | Don't fix | Declined. | — |
| TF25 | Nit | Don't fix | Declined. | — |
