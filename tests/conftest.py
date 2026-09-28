import json
import socket
from dataclasses import fields
from pathlib import Path

import pandas as pd
import pytest
from pytest_socket import SocketBlockedError

from streamforecast import config

TEST_MARKERS = {"unit", "regression", "integration"}
FIXTURES = Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(config, items):
    """Every test must carry exactly one of the unit/regression/integration markers."""
    bad = []
    for item in items:
        found = {m.name for m in item.iter_markers()} & TEST_MARKERS
        if len(found) != 1:
            bad.append(f"{item.nodeid} (markers: {sorted(found) or 'none'})")
    if bad:
        raise pytest.UsageError(
            "each test needs exactly one of the markers unit, regression, "
            "integration:\n  " + "\n  ".join(bad)
        )


@pytest.fixture(autouse=True)
def _block_dns(monkeypatch):
    """pytest-socket blocks socket creation; this also blocks DNS lookups,
    which happen before a socket exists and would otherwise reach the network."""

    def blocked(*args, **kwargs):
        raise SocketBlockedError("A test tried to resolve a host name.")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch):
    """Tests start from the documented defaults, whatever the developer's shell sets."""
    for f in fields(config.Settings):
        monkeypatch.delenv(f.name.upper(), raising=False)


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Point DATA_DIR at a fresh temporary directory."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def load_fixture():
    """Parsed JSON of a recorded response in tests/fixtures (see SOURCES.md)."""

    def load(name):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return load


STAMP = "20260924T160000Z"
FETCHED = "2026-09-24T16:00:00Z"


@pytest.fixture
def write_raw(tmp_data_dir):
    """Write rows as ``DATA_DIR/raw/<kind>/<kind>_<stamp>.csv``."""

    def write(kind, frame, stamp=STAMP):
        directory = tmp_data_dir / "raw" / kind
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{kind}_{stamp}.csv"
        pd.DataFrame(frame).to_csv(path, index=False)
        return path

    return write


@pytest.fixture
def raw_from_fixtures(write_raw, load_fixture):
    """Turn recorded responses into raw CSVs exactly as ingest writes them.

    Usage: ``raw_from_fixtures(usgs="usgs_daily_recent.json", weather_hist=...)``.
    """
    from streamforecast import ingest

    def build(stamp=STAMP, fetched=FETCHED, **names):
        for kind, name in names.items():
            data = load_fixture(name)
            if kind == "usgs":
                rows, _ = ingest.parse_usgs(data["features"], "02085000")
                columns = ingest.USGS_COLUMNS
            elif kind == "weather_hist":
                rows = ingest.parse_daily_weather(data, "historical_forecast", fetched)
                columns = ingest.WEATHER_COLUMNS
            elif kind == "weather_fc":
                rows = ingest.parse_daily_weather(data, "forecast", fetched)
                columns = ingest.WEATHER_COLUMNS
            elif kind == "weather_leads":
                rows, _ = ingest.parse_leads(data, fetched)
                columns = ingest.LEADS_COLUMNS
            elif kind == "mrms":
                rows = ingest.parse_mrms(data, fetched)
                columns = ingest.MRMS_COLUMNS
            else:
                raise ValueError(kind)
            write_raw(kind, pd.DataFrame(rows, columns=columns), stamp)

    return build


@pytest.fixture
def run_features(tmp_data_dir):
    """Run the features stage on DATA_DIR; return (exit code, newest features frame)."""
    from streamforecast import features, logs

    def run():
        settings = config.load()
        code = features.run(settings, logs.get_logger("features", settings))
        out = sorted((tmp_data_dir / "features").glob("features_*.csv"))
        frame = (
            pd.read_csv(out[-1], dtype={"date": str, "fc_fetched_date": str})
            if out
            else None
        )
        return code, frame

    return run


def make_synthetic_features(
    start="2018-01-01", end="2026-03-31", seed=0, signal=True, lead_start="2024-02-01"
):
    """A stationary synthetic features table (AC-2.7 columns) for training tests.

    ``y_h = 0.03 * (rain over d0+1..d0+h) - 0.05 h + N(0, 0.08 sqrt(h))`` when
    ``signal``; pure noise otherwise. The last row is a live row (targets NaN).
    """
    import numpy as np

    from streamforecast import features as feat

    dates = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d")
    n = len(dates)
    rng = np.random.default_rng(seed)
    x = np.empty(n)
    x[0] = 3.0
    for t in range(1, n):
        x[t] = 3.0 + 0.95 * (x[t - 1] - 3.0) + rng.normal(0, 0.15)
    rain = rng.exponential(8.0, n) * (rng.random(n) < 0.3)
    f = pd.DataFrame({"date": dates})
    f["flow_cfs"] = np.expm1(x)
    f["approval_status"] = "Approved"
    f["qualifier"] = ""
    f["flow_filled"] = False
    f["logq_0"] = x
    f["logq_1"] = np.r_[x[0], x[:-1]]
    f["logq_2"] = np.r_[x[:2], x[:-2]]
    f["dlogq_1"] = f["logq_0"] - f["logq_1"]
    r = pd.Series(rain)
    f["precip_0"] = rain
    f["api_7"] = r.rolling(7, min_periods=7).sum()
    f["api_30"] = r.rolling(30, min_periods=30).sum()
    for h in (1, 2, 3):
        f[f"precip_f{h}"] = r.shift(-h).fillna(0.0)
    f["weather_lead_matched"] = f["date"] >= lead_start
    f["fc_fetched_date"] = ""
    f["tmax_0"] = rng.normal(20, 5, n)
    f["tmin_0"] = f["tmax_0"] - 10
    doy = pd.to_datetime(f["date"]).dt.dayofyear
    f["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    f["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    for h in (1, 2, 3):
        rain_ahead = sum(f[f"precip_f{k}"] for k in range(1, h + 1))
        noise = rng.normal(0, 0.08 * np.sqrt(h), n)
        f[f"y_{h}"] = (0.03 * rain_ahead - 0.05 * h if signal else 0.0) + noise
    f.loc[f.index[-1], ["y_1", "y_2", "y_3"]] = np.nan
    return f[feat.FEATURE_COLUMNS]


@pytest.fixture
def synthetic_features():
    return make_synthetic_features


@pytest.fixture(scope="session")
def published_checkpoint(tmp_path_factory):
    """A real checkpoint + sidecar trained once on synthetic features (Stage 3)."""
    from streamforecast import logs, train

    root = tmp_path_factory.mktemp("checkpoint")
    settings = config.Settings(data_dir=str(root))
    log = logs.get_logger("train", config.Settings(log_level="ERROR"))
    checkpoint, sidecar = train.train(make_synthetic_features(), None, settings, log)
    return train.publish(checkpoint, sidecar, Path("features_synthetic.csv"), settings)


@pytest.fixture
def install_checkpoint(tmp_data_dir, published_checkpoint):
    """Copy the session checkpoint (model + sidecar) into DATA_DIR/models."""
    import shutil

    def install(with_sidecar=True):
        models = tmp_data_dir / "models"
        models.mkdir(exist_ok=True)
        model_path, metrics_path = published_checkpoint
        shutil.copy(model_path, models / model_path.name)
        if with_sidecar:
            shutil.copy(metrics_path, models / metrics_path.name)
        return models / model_path.name

    return install
