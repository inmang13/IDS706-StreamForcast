"""Stage 3: fit one model per horizon, calibrate intervals, score against persistence.

Reads the newest ``DATA_DIR/features/features_*.csv`` (and the newest ``mrms_*.csv``
for the wet/dry evaluation labels only). Publishes ``DATA_DIR/models/model_<ts>.joblib``
and then ``metrics_<ts>.json`` (a checkpoint without its sidecar is unpublished).

- Splits by calendar date (AC-3.1): a row is in a split only if both d0 and d0+h are.
- ``HistGradientBoostingRegressor(random_state=0)``, defaults, fit on train rows only.
- Intervals (D9): residual quantiles of ``y - yhat`` on calibration rows; served as
  ``cfs = max(0, expm1(logq_0 + yhat + r_p))`` by ``bands()``.
- ``python -m streamforecast.train --report`` prints the coverage table and applies the
  AC-3.9 thresholds (``make coverage``).

The checkpoint holds only sklearn estimators and plain Python types, so loading it
never imports this module (stage isolation, D11).
"""

import argparse
import json
import math
import sys
from datetime import date, timedelta
from logging import Logger
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

from streamforecast import config, logs, paths

HORIZONS = (1, 2, 3)
QUANTILES = (0.025, 0.10, 0.50, 0.90, 0.975)
FEATURE_COLUMNS = [
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
    "tmax_0",
    "tmin_0",
    "doy_sin",
    "doy_cos",
]
MIN_GROUP_N = 20
HIGH_FLOW_PCT = 0.90
WET_MM = 10.0
DRY_MM = 2.0
# AC-3.9 / D19 tolerances.
TEST_COV80 = (0.72, 0.88)
TEST_COV95 = (0.90, 0.99)
CAL_TOLERANCE = 0.02


class TrainError(Exception):
    """Training cannot proceed; nothing is published."""


def _key(q: float) -> str:
    return f"{q:g}"


# --- Splits -------------------------------------------------------------------------


def _plus(d0: str, days: int) -> str:
    return (date.fromisoformat(d0) + timedelta(days=days)).isoformat()


def split_by_date(
    df: pd.DataFrame, h: int, settings: config.Settings
) -> dict[str, pd.DataFrame]:
    """Train / calibration / test rows for horizon ``h`` (AC-3.1).

    Rows without a target are dropped everywhere. Calibration and test rows with a
    NaN feature are dropped (and counted in ``attrs["dropped_nan"]``); train keeps
    them (HGB handles NaN). Every calibration and test row must be lead-matched.
    """
    rows = df.loc[df[f"y_{h}"].notna()].copy()
    target_date = rows["date"].map(lambda d: _plus(d, h))
    train_end = settings.train_end.isoformat()
    cal_start = settings.calibration_start.isoformat()
    cal_end = settings.calibration_end.isoformat()
    parts = {
        "train": rows.loc[target_date <= train_end],
        "calibration": rows.loc[(rows["date"] >= cal_start) & (target_date <= cal_end)],
        "test": rows.loc[rows["date"] > cal_end],
    }
    dropped = {}
    for name in ("calibration", "test"):
        part = parts[name]
        if not part["weather_lead_matched"].astype(bool).all():
            bad = part.loc[~part["weather_lead_matched"].astype(bool), "date"]
            raise TrainError(
                f"{name} rows without lead-matched weather (h={h}): "
                f"{len(bad)} rows, first {bad.iloc[0]}"
            )
        complete = part[FEATURE_COLUMNS].notna().all(axis=1)
        dropped[name] = int((~complete).sum())
        parts[name] = part.loc[complete]
    for part in parts.values():
        part.attrs["dropped_nan"] = 0
    parts["calibration"].attrs["dropped_nan"] = dropped["calibration"]
    parts["test"].attrs["dropped_nan"] = dropped["test"]
    ends = [p["date"] for p in parts.values() if len(p)]
    if not all(a.max() < b.min() for a, b in zip(ends, ends[1:])):
        raise TrainError(f"splits overlap or are out of order (h={h})")
    return parts


# --- Model and intervals ------------------------------------------------------------


def fit_horizon(train: pd.DataFrame, h: int) -> HistGradientBoostingRegressor:
    model = HistGradientBoostingRegressor(random_state=0)
    model.fit(train[FEATURE_COLUMNS].to_numpy(), train[f"y_{h}"].to_numpy())
    return model


def predict(model, rows: pd.DataFrame) -> np.ndarray:
    return model.predict(rows[FEATURE_COLUMNS].to_numpy())


def residual_quantiles(residuals: np.ndarray) -> dict[str, float]:
    """Empirical residual quantiles (log-change space) as ``{"0.025": r, ...}``."""
    values = np.quantile(np.asarray(residuals, dtype=float), QUANTILES)
    return {_key(q): float(v) for q, v in zip(QUANTILES, values)}


def bands(logq_0, yhat, quantiles: dict[str, float]) -> dict[str, np.ndarray]:
    """D9: ``q_p = logq_0 + yhat + r_p``; ``cfs = max(0, expm1(q_p))``.

    ``quantiles`` is one horizon's ``residual_quantiles`` entry. Returns arrays for
    ``median`` (bias-corrected with r_0.5), ``lo80``, ``hi80``, ``lo95``, ``hi95``.
    """
    base = np.asarray(logq_0, dtype=float) + np.asarray(yhat, dtype=float)
    names = {"lo95": 0.025, "lo80": 0.10, "median": 0.50, "hi80": 0.90, "hi95": 0.975}
    return {
        name: np.maximum(0.0, np.expm1(base + quantiles[_key(q)]))
        for name, q in names.items()
    }


def observed_cfs(rows: pd.DataFrame, h: int) -> np.ndarray:
    """Observed flow at d0+h, recovered from the target: expm1(logq_0 + y_h)."""
    return np.expm1(rows["logq_0"].to_numpy() + rows[f"y_{h}"].to_numpy())


# --- Metrics ------------------------------------------------------------------------


def _clean(x):
    """float for JSON, with NaN/inf as None (the sidecar is strict JSON)."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def coverage(obs, lo, hi) -> float | None:
    obs, lo, hi = map(np.asarray, (obs, lo, hi))
    if len(obs) == 0:
        return None
    return float(np.mean((lo <= obs) & (obs <= hi)))


def group_metrics(obs, model_cfs, persistence_cfs, band) -> dict:
    """AC-3.5 metrics for one group; ``band`` holds lo80/hi80/lo95/hi95 arrays."""
    obs = np.asarray(obs, dtype=float)
    n = int(len(obs))
    nulls = {
        "model": {"mae_cfs": None, "rmse_cfs": None, "nse": None},
        "persistence": {"mae_cfs": None, "nse": None},
        "skill_mae": None,
        "coverage_80": None,
        "coverage_95": None,
    }
    if n < MIN_GROUP_N:
        return {"n": n, "status": "insufficient", **nulls}
    err_m = obs - np.asarray(model_cfs, dtype=float)
    err_p = obs - np.asarray(persistence_cfs, dtype=float)
    spread = float(np.sum((obs - obs.mean()) ** 2))
    mae_m, mae_p = float(np.mean(np.abs(err_m))), float(np.mean(np.abs(err_p)))

    def nse(err):
        return None if spread == 0 else 1.0 - float(np.sum(err**2)) / spread

    return {
        "n": n,
        "status": "ok",
        "model": {
            "mae_cfs": mae_m,
            "rmse_cfs": float(np.sqrt(np.mean(err_m**2))),
            "nse": nse(err_m),
        },
        "persistence": {"mae_cfs": mae_p, "nse": nse(err_p)},
        "skill_mae": None if mae_p == 0 else 1.0 - mae_m / mae_p,
        "coverage_80": coverage(obs, band["lo80"], band["hi80"]),
        "coverage_95": coverage(obs, band["lo95"], band["hi95"]),
    }


def rain_labels(dates: pd.Series, h: int, mrms: pd.Series) -> pd.Series:
    """'wet' / 'dry' / 'light' from observed MRMS rain summed over d0+1..d0+h.

    A window with any day missing from MRMS is 'unlabeled' (never counted as 0 mm).
    """

    def label(d0: str) -> str:
        window = [_plus(d0, k) for k in range(1, h + 1)]
        values = mrms.reindex(window)
        if values.isna().any():
            return "unlabeled"
        total = float(values.sum())
        return "wet" if total >= WET_MM else "dry" if total < DRY_MM else "light"

    return dates.map(label)


def evaluate(
    rows: pd.DataFrame,
    h: int,
    yhat: np.ndarray,
    quantiles: dict[str, float],
    high_threshold: float,
    mrms: pd.Series | None,
) -> dict:
    """Test-split metrics for horizon ``h``, overall, high-flow and by rain (AC-3.5)."""
    obs = observed_cfs(rows, h)
    band = bands(rows["logq_0"], yhat, quantiles)
    persistence = rows["flow_cfs"].to_numpy()

    def group(mask):
        mask = np.asarray(mask, dtype=bool)
        return group_metrics(
            obs[mask],
            band["median"][mask],
            persistence[mask],
            {k: v[mask] for k, v in band.items()},
        )

    overall = group(np.ones(len(rows), dtype=bool))
    high = group(obs > high_threshold)
    result = {
        **overall,
        "n_high": high["n"],
        "coverage_80_high": high["coverage_80"],
        "coverage_95_high": high["coverage_95"],
        "high": high,
    }
    if mrms is None:
        result["by_rain"] = None
    else:
        labels = rain_labels(rows["date"], h, mrms).to_numpy()
        result["by_rain"] = {
            "wet": group(labels == "wet"),
            "dry": group(labels == "dry"),
            "n_light": int((labels == "light").sum()),
            "n_unlabeled": int((labels == "unlabeled").sum()),
            "wet_mm": WET_MM,
            "dry_mm": DRY_MM,
        }
    return result


def calibration_coverage(rows, h, yhat, quantiles) -> dict:
    obs = observed_cfs(rows, h)
    band = bands(rows["logq_0"], yhat, quantiles)
    return {
        "n": int(len(rows)),
        "coverage_80": coverage(obs, band["lo80"], band["hi80"]),
        "coverage_95": coverage(obs, band["lo95"], band["hi95"]),
    }


# --- Coverage check (AC-3.9) -------------------------------------------------------


def _within(value, low, high) -> bool:
    return value is not None and low <= value <= high


def check_horizon(entry: dict) -> list[str]:
    """Failures of the AC-3.9 thresholds for one horizon's sidecar entry."""
    test, cal = entry["test"], entry["calibration"]
    failures = []
    if not _within(test["coverage_80"], *TEST_COV80):
        failures.append(f"test coverage_80 {test['coverage_80']} outside {TEST_COV80}")
    if not _within(test["coverage_95"], *TEST_COV95):
        failures.append(f"test coverage_95 {test['coverage_95']} outside {TEST_COV95}")
    for nominal, key in ((0.80, "coverage_80"), (0.95, "coverage_95")):
        if not _within(cal[key], nominal - CAL_TOLERANCE, nominal + CAL_TOLERANCE):
            failures.append(
                f"calibration {key} {cal[key]} not within {CAL_TOLERANCE} of {nominal}"
            )
    return failures


def _fmt(x, digits=3) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def report(sidecar: dict) -> tuple[bool, str]:
    """The ``make coverage`` table and whether every horizon passes AC-3.9."""
    header = (
        f"{'h':>2} {'n':>5} {'cov80':>6} {'cov95':>6} {'n_high':>6} {'cov80_hi':>8} "
        f"{'cov95_hi':>8} {'cal80':>6} {'cal95':>6} {'skill':>6} "
        f"{'wet_skill':>9} {'wet_cov80':>9} {'dry_skill':>9} {'dry_cov80':>9}"
    )
    lines = [f"model_version {sidecar['model_version']} (test split)", header]
    failures = []
    for h in HORIZONS:
        entry = sidecar["horizons"][str(h)]
        t, c, rain = entry["test"], entry["calibration"], entry["test"]["by_rain"]
        wet = rain["wet"] if rain else {}
        dry = rain["dry"] if rain else {}
        lines.append(
            f"{h:>2} {t['n']:>5} {_fmt(t['coverage_80']):>6} "
            f"{_fmt(t['coverage_95']):>6} "
            f"{t['n_high']:>6} {_fmt(t['coverage_80_high']):>8} "
            f"{_fmt(t['coverage_95_high']):>8} {_fmt(c['coverage_80']):>6} "
            f"{_fmt(c['coverage_95']):>6} {_fmt(t['skill_mae']):>6} "
            f"{_fmt(wet.get('skill_mae')):>9} {_fmt(wet.get('coverage_80')):>9} "
            f"{_fmt(dry.get('skill_mae')):>9} {_fmt(dry.get('coverage_80')):>9}"
        )
        failures += [f"h={h}: {f}" for f in check_horizon(entry)]
    lines.append(
        f"gate: test cov80 in {list(TEST_COV80)}, cov95 in {list(TEST_COV95)}; "
        f"calibration within +/-{CAL_TOLERANCE} of nominal (high-flow, skill, wet/dry "
        "not gated)"
    )
    lines += ["FAIL " + f for f in failures] or ["PASS: every horizon within tolerance"]
    return not failures, "\n".join(lines)


# --- Publishing ---------------------------------------------------------------------


def to_json(obj) -> str:
    def clean(o):
        if isinstance(o, dict):
            return {str(k): clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, (float, np.floating)):
            return _clean(o)
        if isinstance(o, np.integer):
            return int(o)
        return o

    return json.dumps(clean(obj), indent=2, allow_nan=False)


def train(features: pd.DataFrame, mrms: pd.Series | None, settings, log) -> tuple:
    """Fit, calibrate and evaluate every horizon. Returns (checkpoint, sidecar)."""
    train_flow = features.loc[
        features["date"] <= settings.train_end.isoformat(), "flow_cfs"
    ]
    if train_flow.empty:
        raise TrainError("no rows in the train period")
    high_threshold = float(np.quantile(train_flow, HIGH_FLOW_PCT))
    models, quantiles, horizons = {}, {}, {}
    for h in HORIZONS:
        parts = split_by_date(features, h, settings)
        tr, cal, te = parts["train"], parts["calibration"], parts["test"]
        if tr.empty or cal.empty:
            raise TrainError(
                f"h={h}: need train and calibration rows "
                f"(train n={len(tr)}, cal n={len(cal)})"
            )
        log.info("h=%d train n=%d cal n=%d test n=%d", h, len(tr), len(cal), len(te))
        if cal.attrs["dropped_nan"] or te.attrs["dropped_nan"]:
            log.info(
                "h=%d dropped rows with a NaN feature: cal %d, test %d",
                h,
                cal.attrs["dropped_nan"],
                te.attrs["dropped_nan"],
            )
        model = fit_horizon(tr, h)
        if h == 1:
            log.info("HGB n_iter_=%d (max_iter=%d)", model.n_iter_, model.max_iter)
        cal_hat = predict(model, cal)
        quantiles[h] = residual_quantiles(cal[f"y_{h}"].to_numpy() - cal_hat)
        models[h] = model
        test_hat = predict(model, te) if len(te) else np.array([])
        test_metrics = evaluate(te, h, test_hat, quantiles[h], high_threshold, mrms)
        horizons[str(h)] = {
            "train": {"n": int(len(tr))},
            "calibration": calibration_coverage(cal, h, cal_hat, quantiles[h]),
            "test": test_metrics,
            "residual_quantiles": quantiles[h],
        }
        t = test_metrics
        log.info(
            "h=%d test MAE model %s vs persistence %s (skill %s)",
            h,
            _fmt(t["model"]["mae_cfs"], 2),
            _fmt(t["persistence"]["mae_cfs"], 2),
            _fmt(t["skill_mae"]),
        )
        log.info(
            "h=%d coverage80 %s coverage95 %s (high-flow %s %s)",
            h,
            _fmt(t["coverage_80"]),
            _fmt(t["coverage_95"]),
            _fmt(t["coverage_80_high"]),
            _fmt(t["coverage_95_high"]),
        )
        if t["skill_mae"] is None or t["skill_mae"] <= 0:
            log.warning(
                "h=%d no skill vs persistence (skill_mae %s)", h, t["skill_mae"]
            )
        for failure in check_horizon(horizons[str(h)]):
            log.warning("h=%d coverage check: %s", h, failure)
    if mrms is None:
        log.warning("no MRMS verification file; by_rain is null")
    split_dates = {
        "train_end": settings.train_end.isoformat(),
        "calibration_start": settings.calibration_start.isoformat(),
        "calibration_end": settings.calibration_end.isoformat(),
    }
    checkpoint = {
        "models": models,
        "residual_quantiles": quantiles,
        "feature_columns": list(FEATURE_COLUMNS),
        "sklearn_version": sklearn.__version__,
        "split_dates": split_dates,
    }
    sidecar = {
        "feature_columns": list(FEATURE_COLUMNS),
        "sklearn_version": sklearn.__version__,
        "split_dates": split_dates,
        "high_flow_threshold_cfs": high_threshold,
        "horizons": horizons,
    }
    return checkpoint, sidecar


def publish(checkpoint: dict, sidecar: dict, features_file: Path, settings) -> tuple:
    """Checkpoint first, sidecar last, both atomic and sharing ``<ts>`` (AC-3.4)."""
    now = paths.utc_now()
    stamp = paths.timestamp(now)
    trained_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    models_dir = paths.subdir("models", settings)
    checkpoint = {**checkpoint, "trained_at_utc": trained_at, "model_version": stamp}
    sidecar = {
        "model_version": stamp,
        "trained_at_utc": trained_at,
        "features_file": features_file.name,
        **sidecar,
    }
    model_path = models_dir / f"model_{stamp}.joblib"
    metrics_path = models_dir / f"metrics_{stamp}.json"
    paths.atomic_write(model_path, lambda tmp: joblib.dump(checkpoint, tmp))
    text = to_json(sidecar)
    paths.atomic_write(metrics_path, lambda tmp: tmp.write_text(text, encoding="utf-8"))
    return model_path, metrics_path


def run_training(settings: config.Settings, log: Logger) -> int:
    features_file = paths.newest(paths.subdir("features", settings), "features_*.csv")
    if features_file is None:
        log.error("no features file; run make features first")
        return 1
    features = pd.read_csv(
        features_file,
        dtype={"date": str, "qualifier": str, "fc_fetched_date": str},
        keep_default_na=True,
    )
    mrms_file = paths.newest(paths.subdir("features", settings), "mrms_*.csv")
    mrms = None
    if mrms_file is not None:
        frame = pd.read_csv(mrms_file, dtype={"date": str})
        mrms = frame.set_index("date")["mrms_mm"]
    log.info("read %s (%d rows)", features_file.name, len(features))
    try:
        checkpoint, sidecar = train(features, mrms, settings, log)
    except TrainError as exc:
        log.error("%s; nothing published", exc)
        return 1
    model_path, metrics_path = publish(checkpoint, sidecar, features_file, settings)
    log.info("wrote %s and %s", model_path.as_posix(), metrics_path.name)
    return 0


def run_report(settings: config.Settings, log: Logger) -> int:
    path = paths.newest(paths.subdir("models", settings), "metrics_*.json")
    if path is None:
        log.error("no metrics sidecar; run make train first")
        return 1
    ok, text = report(json.loads(path.read_text(encoding="utf-8")))
    print(f"{path.name}\n{text}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m streamforecast.train")
    parser.add_argument(
        "--report",
        action="store_true",
        help="print the coverage table from the newest sidecar and gate it (AC-3.9)",
    )
    args = parser.parse_args(argv)
    try:
        settings = config.load()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    log = logs.get_logger("train", settings)
    if args.report:
        return run_report(settings, log)
    return run_training(settings, log)


if __name__ == "__main__":
    sys.exit(main())
