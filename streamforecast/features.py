"""Stage 2: merge raw files into one daily feature table (one row per issue date d0).

Reads ``DATA_DIR/raw/**`` and writes ``DATA_DIR/features/``:

- ``features_<ts>.csv``: the AC-2.7 columns; the last row is the live row (latest d0).
- ``rain_check_<ts>.json`` and ``mrms_<ts>.csv``: MRMS verification outputs (D24),
  only when raw MRMS files exist. MRMS never enters ``features_*.csv``.

Rules (docs/plan.md, Stage 2):

- Newest file wins per date (USGS revisions; D28: ``weather_fc`` beats ``weather_hist``
  on a timestamp tie). Rows whose values are all empty never win.
- Issue dates are days with an observed, valid flow (D4). A flow gap of at most
  ``MAX_FFILL_DAYS`` before d0 is forward-filled for the lags only; longer gaps stay
  NaN. Since d0 is observed, every such gap is closed by d0: no future information.
- Targets use observed flow only. Weather is never filled.
- Dates are ``YYYY-MM-DD`` strings throughout; all sources join on them (RK13).
"""

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from logging import Logger
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from streamforecast import config, logs, paths

SENTINEL = -999999.0
HORIZONS = (1, 2, 3)
KEEP_FILES = 5
HEAVY_MM = 10.0
DRY_MM = 2.0
TOP_DISAGREEMENTS = 10
WEATHER_RANK = {"weather_hist": 0, "weather_fc": 1}  # higher wins a timestamp tie

FEATURE_COLUMNS = [
    "date",
    "flow_cfs",
    "approval_status",
    "qualifier",
    "flow_filled",
    "logq_0",
    "logq_1",
    "logq_2",
    "dlogq_1",
    "precip_0",
    "api_7",
    "api_30",
    "precip_f1",
    "precip_f2",
    "precip_f3",
    "weather_lead_matched",
    "fc_fetched_date",
    "tmax_0",
    "tmin_0",
    "doy_sin",
    "doy_cos",
    "y_1",
    "y_2",
    "y_3",
]


@dataclass
class Forecast:
    """The newest ``weather_fc`` file: daily rows plus its fetch date (local)."""

    frame: pd.DataFrame
    fetched_date: str


# --- Loading --------------------------------------------------------------------


def file_stamp(path: Path) -> str:
    """``usgs_20260924T160000Z.csv`` -> ``20260924T160000Z``."""
    return path.stem.rsplit("_", 1)[1]


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])


def load_raw_usgs(directory: Path) -> pd.DataFrame:
    """All USGS files, newest file winning per date; values still raw strings."""
    frames = [_read(p) for p in sorted(directory.glob("usgs_*.csv"))]
    if not frames:
        return pd.DataFrame(
            columns=["date", "flow_cfs", "approval_status", "qualifier"]
        )
    raw = pd.concat(frames, ignore_index=True)
    return raw.drop_duplicates("date", keep="last").sort_values("date")


def validate_flow(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Drop physically impossible flow (D14); keep every finite value >= 0."""
    text = raw["flow_cfs"]
    value = pd.to_numeric(text, errors="coerce")
    reasons = {
        "negative": (value < 0) & (value != SENTINEL),
        "sentinel": value == SENTINEL,
        "non-numeric": text.notna() & value.isna(),
        "missing": text.isna(),
    }
    reasons["non-numeric"] |= np.isinf(value)
    bad = pd.concat(reasons, axis=1).any(axis=1)
    counts = {name: int(mask.sum()) for name, mask in reasons.items()}
    good = raw.loc[~bad].copy()
    good["flow_cfs"] = value[~bad]
    good["approval_status"] = good["approval_status"].fillna("")
    good["qualifier"] = good["qualifier"].fillna("")
    return good.reset_index(drop=True), counts


def load_raw_weather(hist_dir: Path, fc_dir: Path) -> pd.DataFrame:
    """Daily weather from weather_hist and weather_fc; newest file wins (D28)."""
    frames = []
    for kind, directory in (("weather_hist", hist_dir), ("weather_fc", fc_dir)):
        for path in sorted(directory.glob(f"{kind}_*.csv")):
            df = _read(path)
            df["_order"] = file_stamp(path) + f"_{WEATHER_RANK[kind]}"
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["date", "precip_mm", "tmax_c", "tmin_c"])
    raw = pd.concat(frames, ignore_index=True)
    for col in ("precip_mm", "tmax_c", "tmin_c"):
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw = raw.dropna(how="all", subset=["precip_mm", "tmax_c", "tmin_c"])
    raw = raw.sort_values("_order", kind="stable")
    merged = raw.drop_duplicates("date", keep="last").sort_values("date")
    return merged[["date", "precip_mm", "tmax_c", "tmin_c"]].reset_index(drop=True)


def load_raw_leads(directory: Path) -> pd.DataFrame:
    """Previous-runs lead rain; newest file wins per (date, lead_days)."""
    frames = [_read(p) for p in sorted(directory.glob("weather_leads_*.csv"))]
    if not frames:
        return pd.DataFrame(columns=["date", "lead_days", "precip_mm"])
    raw = pd.concat(frames, ignore_index=True)
    raw["lead_days"] = raw["lead_days"].astype(int)
    raw["precip_mm"] = pd.to_numeric(raw["precip_mm"], errors="coerce")
    raw = raw.drop_duplicates(["date", "lead_days"], keep="last")
    return raw[["date", "lead_days", "precip_mm"]].sort_values(["date", "lead_days"])


def load_forecast(directory: Path, tz: str) -> Forecast | None:
    """The newest ``weather_fc`` file only (the live forecast; never older files)."""
    path = paths.newest(directory, "weather_fc_*.csv")
    if path is None:
        return None
    df = _read(path)
    df["precip_mm"] = pd.to_numeric(df["precip_mm"], errors="coerce")
    fetched = datetime.fromisoformat(
        df["fetched_at_utc"].iloc[0].replace("Z", "+00:00")
    )
    return Forecast(df, fetched.astimezone(ZoneInfo(tz)).date().isoformat())


def load_raw_mrms(directory: Path) -> pd.DataFrame | None:
    """Deduplicated MRMS series (``date, mrms_mm``), or None without raw MRMS files."""
    files = sorted(directory.glob("mrms_*.csv"))
    if not files:
        return None
    raw = pd.concat([_read(p) for p in files], ignore_index=True)
    raw["mrms_mm"] = pd.to_numeric(raw["precip_mm"], errors="coerce")
    raw = raw.dropna(subset=["mrms_mm"]).drop_duplicates("date", keep="last")
    return raw[["date", "mrms_mm"]].sort_values("date").reset_index(drop=True)


def grid_cells(*directories: Path) -> set[tuple[str, str]]:
    cells = set()
    for directory in directories:
        for path in sorted(directory.glob("*.csv")):
            df = _read(path)
            if {"grid_lat", "grid_lon"} <= set(df.columns):
                cells |= set(zip(df["grid_lat"], df["grid_lon"]))
    return cells


# --- Building ----------------------------------------------------------------


def calendar(first: str, last: str) -> list[str]:
    start, end = date.fromisoformat(first), date.fromisoformat(last)
    return [
        (start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
    ]


def fill_short_gaps(q: pd.Series, max_days: int) -> tuple[pd.Series, pd.Series]:
    """Forward-fill NaN runs of at most ``max_days`` that sit between observations.

    Returns the filled series and a mask of the filled positions. Runs before the
    first or after the last observation, and longer runs, stay NaN.
    """
    missing = q.isna()
    run_id = (~missing).cumsum()
    run_len = missing.groupby(run_id).transform("sum")
    fill = missing & (run_len <= max_days) & (run_id > 0) & (run_id < run_id.max())
    return q.ffill().where(~missing | fill), fill


def build_features(
    flow: pd.DataFrame,
    weather: pd.DataFrame,
    leads: pd.DataFrame,
    forecast: Forecast | None,
    settings: config.Settings,
) -> tuple[pd.DataFrame, dict]:
    """One row per observed-flow date d0 (AC-2.7). Returns the table and log stats."""
    latest = flow["date"].max()
    d0 = date.fromisoformat(latest)
    bounds = [flow["date"].min(), (d0 + timedelta(days=max(HORIZONS))).isoformat()]
    for frame in (weather, leads):
        if not frame.empty:
            bounds += [frame["date"].min(), frame["date"].max()]
    idx = pd.Index(calendar(min(bounds), max(bounds)), name="date")

    obs = flow.set_index("date").reindex(idx)
    q = obs["flow_cfs"].astype(float)
    filled_q, filled = fill_short_gaps(q, settings.max_ffill_days)
    lq = np.log1p(filled_q)

    w = weather.set_index("date").reindex(idx)
    precip = w["precip_mm"].astype(float)
    lead = {
        k: leads.loc[leads["lead_days"] == k]
        .set_index("date")["precip_mm"]
        .reindex(idx)
        for k in (1, 2)
    }
    matched = pd.Series(idx >= settings.calibration_start.isoformat(), index=idx)
    doy = pd.Series([date.fromisoformat(d).timetuple().tm_yday for d in idx], index=idx)

    out = pd.DataFrame(index=idx)
    out["flow_cfs"] = q
    out["approval_status"] = obs["approval_status"]
    out["qualifier"] = obs["qualifier"]
    out["flow_filled"] = filled.shift(1, fill_value=False) | filled.shift(
        2, fill_value=False
    )
    out["logq_0"] = np.log1p(q)
    out["logq_1"] = lq.shift(1)
    out["logq_2"] = lq.shift(2)
    out["dlogq_1"] = out["logq_0"] - out["logq_1"]
    out["precip_0"] = precip
    out["api_7"] = precip.rolling(7, min_periods=7).sum()
    out["api_30"] = precip.rolling(30, min_periods=30).sum()
    out["precip_f1"] = precip.shift(-1)
    out["precip_f2"] = precip.shift(-2).where(~matched, lead[1].shift(-2))
    out["precip_f3"] = precip.shift(-3).where(~matched, lead[2].shift(-3))
    out["weather_lead_matched"] = matched
    out["fc_fetched_date"] = ""
    out["tmax_0"] = w["tmax_c"].astype(float)
    out["tmin_0"] = w["tmin_c"].astype(float)
    out["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    out["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    for h in HORIZONS:
        out[f"y_{h}"] = np.log1p(q.shift(-h)) - out["logq_0"]

    # The live row: forecast rain only from the newest weather_fc file (AC-2.8).
    fc = (
        dict(zip(forecast.frame["date"], forecast.frame["precip_mm"]))
        if forecast
        else {}
    )
    for h in HORIZONS:
        out.loc[latest, f"precip_f{h}"] = fc.get((d0 + timedelta(days=h)).isoformat())
    out.loc[latest, "weather_lead_matched"] = True
    out.loc[latest, "fc_fetched_date"] = forecast.fetched_date if forecast else ""

    rows = out.loc[q.notna() & (idx <= latest)]
    lags = ["logq_0", "logq_1", "logq_2"]
    incomplete = rows[lags].isna().any(axis=1) & (rows.index != latest)
    rows = rows.loc[~incomplete].reset_index()
    stats = {
        "filled_days": int(filled.sum()),
        "dropped_incomplete_lags": int(incomplete.sum()),
        "lead_matched": int(rows["weather_lead_matched"].iloc[:-1].sum()),
        "latest": latest,
    }
    return rows[FEATURE_COLUMNS], stats


# --- Rain check (verification only, D24) ------------------------------------------


def _series_check(om: pd.Series, mrms: pd.Series) -> dict:
    both = pd.concat({"om": om, "mrms": mrms}, axis=1).dropna()
    diff = both["om"] - both["mrms"]
    top = (
        diff.abs().sort_values(ascending=False, kind="stable").index[:TOP_DISAGREEMENTS]
    )
    return {
        "n": int(len(both)),
        "bias_mm": round(float(diff.mean()), 3) if len(both) else None,
        "mae_mm": round(float(diff.abs().mean()), 3) if len(both) else None,
        "hits": int(((both["mrms"] >= HEAVY_MM) & (both["om"] >= HEAVY_MM)).sum()),
        "misses": int(((both["mrms"] >= HEAVY_MM) & (both["om"] < DRY_MM)).sum()),
        "false_alarms": int(((both["om"] >= HEAVY_MM) & (both["mrms"] < DRY_MM)).sum()),
        "top_disagreements": [
            {
                "date": d,
                "openmeteo_mm": round(float(both.at[d, "om"]), 3),
                "mrms_mm": round(float(both.at[d, "mrms"]), 3),
            }
            for d in top
        ],
    }


def rain_check(
    weather: pd.DataFrame, leads: pd.DataFrame, mrms: pd.DataFrame, start: date
) -> dict:
    """Open-Meteo lead-0/1/2 rain vs MRMS for dates from ``start`` (AC-2.9)."""
    since = start.isoformat()
    m = mrms.set_index("date")["mrms_mm"]
    m = m[m.index >= since]
    series = {
        "lead0": weather.set_index("date")["precip_mm"],
        "lead1": leads.loc[leads["lead_days"] == 1].set_index("date")["precip_mm"],
        "lead2": leads.loc[leads["lead_days"] == 2].set_index("date")["precip_mm"],
    }
    return {
        "from": since,
        "heavy_mm": HEAVY_MM,
        "dry_mm": DRY_MM,
        "series": {
            name: _series_check(s[s.index >= since].astype(float), m)
            for name, s in series.items()
        },
    }


# --- Writing ---------------------------------------------------------------------


def prune(directory: Path, pattern: str, keep: int = KEEP_FILES) -> None:
    for old in sorted(directory.glob(pattern))[:-keep]:
        old.unlink()


def run(settings: config.Settings, log: Logger) -> int:
    raw_usgs = load_raw_usgs(paths.subdir("usgs", settings))
    if raw_usgs.empty:
        log.error("no raw USGS files in %s; nothing to build", paths.subdir("usgs"))
        return 1
    flow, dropped = validate_flow(raw_usgs)
    weather = load_raw_weather(
        paths.subdir("weather_hist", settings), paths.subdir("weather_fc", settings)
    )
    leads = load_raw_leads(paths.subdir("weather_leads", settings))
    forecast = load_forecast(paths.subdir("weather_fc", settings), settings.timezone)
    log.info(
        "read usgs %d rows, weather %d days, leads %d rows",
        len(raw_usgs),
        len(weather),
        len(leads),
    )
    log.info(
        "dropped impossible: %d negative, %d sentinel, %d non-numeric, %d missing",
        dropped["negative"],
        dropped["sentinel"],
        dropped["non-numeric"],
        dropped["missing"],
    )
    if flow.empty:
        log.error("no valid flow values; nothing to build")
        return 1
    if weather.empty:
        log.warning("no raw weather files; weather features are empty")
    if forecast is None:
        log.warning("no weather_fc file; the latest row has no forecast rain")
    cells = grid_cells(
        paths.subdir("weather_hist", settings),
        paths.subdir("weather_fc", settings),
        paths.subdir("weather_leads", settings),
    )
    if len(cells) > 1:
        log.warning("weather grid cell differs between files: %s", sorted(cells))

    features, stats = build_features(flow, weather, leads, forecast, settings)
    log.info("forward-filled %d days", stats["filled_days"])
    if stats["dropped_incomplete_lags"]:
        log.info(
            "skipped %d issue dates with incomplete flow lags",
            stats["dropped_incomplete_lags"],
        )
    log.info(
        "lead-matched rows: %d (from %s)",
        stats["lead_matched"],
        settings.calibration_start,
    )

    out_dir = paths.subdir("features", settings)
    stamp = paths.timestamp()
    path = out_dir / f"features_{stamp}.csv"
    paths.atomic_write(path, lambda tmp: features.to_csv(tmp, index=False))
    log.info(
        "wrote %d rows (latest d0=%s) -> %s",
        len(features),
        stats["latest"],
        path.as_posix(),
    )

    mrms = load_raw_mrms(paths.subdir("mrms", settings))
    if mrms is None:
        log.warning("no raw MRMS files; rain check skipped")
    else:
        check = rain_check(weather, leads, mrms, settings.calibration_start)
        for name, s in check["series"].items():
            log.info(
                "rain check %s vs MRMS: n=%d bias=%s mm mae=%s mm hits=%d misses=%d "
                "false_alarms=%d",
                name,
                s["n"],
                s["bias_mm"],
                s["mae_mm"],
                s["hits"],
                s["misses"],
                s["false_alarms"],
            )
        paths.atomic_write(
            out_dir / f"rain_check_{stamp}.json",
            lambda tmp: tmp.write_text(json.dumps(check, indent=2, allow_nan=False)),
        )
        paths.atomic_write(
            out_dir / f"mrms_{stamp}.csv",
            lambda tmp: mrms.to_csv(tmp, index=False),
        )
    for pattern in ("features_*.csv", "rain_check_*.json", "mrms_*.csv"):
        prune(out_dir, pattern)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        settings = config.load()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return run(settings, logs.get_logger("features", settings))


if __name__ == "__main__":
    sys.exit(main())
