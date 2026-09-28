"""Stage 4: write the 3-day forecast from the newest checkpoint and features row.

Reads the newest *published* checkpoint (``model_<ts>.joblib`` with a matching
``metrics_<ts>.json``) and the last row of the newest ``features_*.csv``. Writes
``DATA_DIR/forecasts/forecast_<d0>_<ts>.csv`` with exactly 3 rows, or nothing.

Forecast is the freshness gate (D21, AC-6.10). It refuses, writes nothing, and exits 1
when: USGS data is more than ``MAX_STALE_DAYS`` stale (AC-4.5), the checkpoint is
unusable (AC-4.6), flow lags are incomplete (AC-4.9), the forecast weather was not
fetched today (AC-4.10), or fewer than 3 forecast-rain days exist (AC-4.7). With no
checkpoint yet it skips and exits 0 (AC-4.4). Earlier forecasts are never touched.
"""

import sys
from datetime import date, timedelta
from logging import Logger
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from streamforecast import config, logs, paths

HORIZONS = (1, 2, 3)
REQUIRED_KEYS = {
    "models",
    "residual_quantiles",
    "feature_columns",
    "sklearn_version",
    "model_version",
}
LAGS = ["logq_0", "logq_1", "logq_2"]
OUTPUT_COLUMNS = [
    "issue_date",
    "valid_date",
    "horizon",
    "median_cfs",
    "lo80_cfs",
    "hi80_cfs",
    "lo95_cfs",
    "hi95_cfs",
    "persistence_cfs",
    "latest_obs_cfs",
    "latest_obs_provisional",
    "latest_obs_estimated",
    "stale_days",
    "fc_fetched_date",
    "model_version",
    "created_at_utc",
]
# Band name -> residual quantile key, as in train.bands (D9).
BAND_QUANTILES = {
    "lo95": "0.025",
    "lo80": "0.1",
    "median": "0.5",
    "hi80": "0.9",
    "hi95": "0.975",
}


class Refusal(Exception):
    """Forecast will not run on these inputs; nothing is written (exit 1)."""


def predict_bands(logq_0, yhat, quantiles: dict[str, float]) -> dict[str, np.ndarray]:
    """D9: q_p = logq_0 + yhat + r_p; cfs = max(0, expm1(q_p)); the median uses r_0.5.

    Must stay identical to ``train.bands`` (test_forecast_bands_match_train_bands).
    """
    base = np.asarray(logq_0, dtype=float) + np.asarray(yhat, dtype=float)
    return {
        name: np.maximum(0.0, np.expm1(base + quantiles[key]))
        for name, key in BAND_QUANTILES.items()
    }


def newest_published(models_dir: Path) -> Path | None:
    """The newest ``model_<ts>.joblib`` whose ``metrics_<ts>.json`` exists (AC-3.4)."""
    for path in sorted(models_dir.glob("model_*.joblib"), reverse=True):
        stamp = path.stem.removeprefix("model_")
        if (models_dir / f"metrics_{stamp}.json").exists():
            return path
    return None


def load_checkpoint(path: Path) -> dict:
    """Load and check a checkpoint; any problem is a Refusal naming the file."""
    try:
        checkpoint = joblib.load(path)
    except Exception as exc:  # corrupt bytes surface as many exception types
        raise Refusal(f"cannot load {path.name}: {type(exc).__name__}: {exc}") from None
    if not isinstance(checkpoint, dict) or not REQUIRED_KEYS <= set(checkpoint):
        missing = sorted(REQUIRED_KEYS - set(checkpoint or {}))
        raise Refusal(f"{path.name} is missing keys {missing}")
    if checkpoint["sklearn_version"] != sklearn.__version__:
        raise Refusal(
            f"{path.name} was trained with scikit-learn {checkpoint['sklearn_version']}"
            f", this environment has {sklearn.__version__}"
        )
    if sorted(checkpoint["models"]) != list(HORIZONS) or sorted(
        checkpoint["residual_quantiles"]
    ) != list(HORIZONS):
        raise Refusal(f"{path.name} does not hold horizons {list(HORIZONS)}")
    return checkpoint


def latest_row(features_dir: Path) -> pd.Series:
    path = paths.newest(features_dir, "features_*.csv")
    if path is None:
        raise Refusal("no features file")
    df = pd.read_csv(
        path, dtype={"date": str, "qualifier": str, "fc_fetched_date": str}
    )
    if df.empty:
        raise Refusal(f"{path.name} has no rows")
    return df.iloc[-1]


def check_inputs(row: pd.Series, today: date, settings: config.Settings, log) -> int:
    """Apply the freshness rules to the live row; return stale_days or raise Refusal."""
    d0 = date.fromisoformat(row["date"])
    stale_days = ((today - timedelta(days=1)) - d0).days
    limit = settings.max_stale_days
    if stale_days > limit:
        raise Refusal(
            f"USGS data through {d0} is {stale_days} days stale (limit {limit}); "
            "not forecasting"
        )
    if stale_days >= 1:
        log.warning("USGS data through %s, %d day(s) stale", d0, stale_days)
    if row[LAGS].isna().any():
        raise Refusal(f"flow lags incomplete at {d0}")
    fetched = row["fc_fetched_date"]
    if pd.isna(fetched) or fetched != today.isoformat():
        shown = "never" if pd.isna(fetched) else fetched
        raise Refusal(f"forecast weather is stale (fetched {shown}); not forecasting")
    rain = [row[f"precip_f{h}"] for h in HORIZONS]
    have = sum(pd.notna(v) for v in rain)
    if have < len(HORIZONS):
        first, last = d0 + timedelta(days=1), d0 + timedelta(days=len(HORIZONS))
        raise Refusal(
            f"forecast weather has {have} of 3 days for {first}..{last}; "
            "not forecasting"
        )
    return stale_days


def make_forecast(
    checkpoint: dict, row: pd.Series, stale_days: int, created_at: str
) -> pd.DataFrame:
    x = row[checkpoint["feature_columns"]].to_numpy(dtype=float).reshape(1, -1)
    d0 = date.fromisoformat(row["date"])
    qualifier = "" if pd.isna(row["qualifier"]) else str(row["qualifier"])
    rows = []
    for h in HORIZONS:
        yhat = checkpoint["models"][h].predict(x)
        b = predict_bands([row["logq_0"]], yhat, checkpoint["residual_quantiles"][h])
        rows.append(
            {
                "issue_date": d0.isoformat(),
                "valid_date": (d0 + timedelta(days=h)).isoformat(),
                "horizon": h,
                "median_cfs": float(b["median"][0]),
                "lo80_cfs": float(b["lo80"][0]),
                "hi80_cfs": float(b["hi80"][0]),
                "lo95_cfs": float(b["lo95"][0]),
                "hi95_cfs": float(b["hi95"][0]),
                "persistence_cfs": float(row["flow_cfs"]),
                "latest_obs_cfs": float(row["flow_cfs"]),
                "latest_obs_provisional": row["approval_status"] == "Provisional",
                "latest_obs_estimated": "ESTIMATED" in qualifier.split(";"),
                "stale_days": stale_days,
                "fc_fetched_date": row["fc_fetched_date"],
                "model_version": checkpoint["model_version"],
                "created_at_utc": created_at,
            }
        )
    out = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    bounds = out[
        ["lo95_cfs", "lo80_cfs", "median_cfs", "hi80_cfs", "hi95_cfs"]
    ].to_numpy()
    if not ((np.diff(bounds, axis=1) >= 0).all() and (bounds >= 0).all()):
        raise Refusal("bands out of order; not forecasting")  # AC-4.3; D9 ensures it
    return out


def run(settings: config.Settings, log: Logger) -> int:
    models_dir = paths.subdir("models", settings)
    model_path = newest_published(models_dir)
    if model_path is None:
        log.warning("no checkpoint yet; skipping forecast")
        return 0
    try:
        checkpoint = load_checkpoint(model_path)
        log.info("loaded %s", model_path.name)
        row = latest_row(paths.subdir("features", settings))
        stale_days = check_inputs(row, paths.local_today(settings), settings, log)
        log.info("d0=%s stale_days=%d", row["date"], stale_days)
        if row["approval_status"] == "Provisional" or "ESTIMATED" in str(
            row["qualifier"]
        ):
            log.info(
                "latest value %s cfs on %s is %s%s; used as-is",
                row["flow_cfs"],
                row["date"],
                row["approval_status"],
                f" ({row['qualifier']})" if pd.notna(row["qualifier"]) else "",
            )
        now = paths.utc_now()
        out = make_forecast(
            checkpoint, row, stale_days, now.strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    except Refusal as exc:
        log.error("%s", exc)
        return 1
    path = paths.subdir("forecasts", settings) / (
        f"forecast_{row['date']}_{paths.timestamp(now)}.csv"
    )
    paths.atomic_write(path, lambda tmp: out.to_csv(tmp, index=False))
    log.info("wrote 3 rows -> %s", path.as_posix())
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        settings = config.load()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return run(settings, logs.get_logger("forecast", settings))


if __name__ == "__main__":
    sys.exit(main())
