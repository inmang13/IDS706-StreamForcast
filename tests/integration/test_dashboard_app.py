"""The Streamlit page, rendered headless with AppTest on a temporary DATA_DIR."""

import json
import socket
from pathlib import Path

import pandas as pd
import pytest
from pytest_socket import SocketConnectBlockedError
from streamlit.testing.v1 import AppTest

from streamforecast import dashboard

# AppTest runs an asyncio event loop, which on Windows needs a local socket pair.
# Allow loopback only (the Stage 0 risk's remedy); every other host stays blocked.
pytestmark = [pytest.mark.integration, pytest.mark.allow_hosts(["127.0.0.1"])]

APP = str(Path(dashboard.__file__))


def run_app():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception, at.exception
    return at


def write_forecast(data_dir, version, **extra):
    rows = {
        "issue_date": "2026-09-23",
        "valid_date": ["2026-09-24", "2026-09-25", "2026-09-26"],
        "horizon": [1, 2, 3],
        "median_cfs": [8.66, 8.57, 5.28],
        "lo80_cfs": [5.66, 4.30, 2.32],
        "hi80_cfs": [12.26, 18.88, 10.88],
        "lo95_cfs": [2.76, 1.81, 0.62],
        "hi95_cfs": [35.56, 50.71, 70.47],
        "persistence_cfs": 7.22,
        "latest_obs_cfs": 7.22,
        "latest_obs_provisional": False,
        "latest_obs_estimated": False,
        "stale_days": 0,
        "fc_fetched_date": "2026-09-24",
        "model_version": version,
        "created_at_utc": "2026-09-24T16:00:00Z",
    }
    rows.update(extra)
    out = data_dir / "forecasts"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(
        out / "forecast_2026-09-23_20260924T160000Z.csv", index=False
    )


def write_history(data_dir):
    out = data_dir / "features"
    out.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range("2026-06-01", "2026-09-23").strftime("%Y-%m-%d")
    frame = pd.DataFrame({"date": dates, "flow_cfs": 10.0, "precip_0": 1.0})
    for h, rain in zip((1, 2, 3), (4.0, 0.0, 12.5)):  # live-row forecast rain
        frame[f"precip_f{h}"] = rain
    frame.to_csv(out / "features_20260924T160000Z.csv", index=False)


@pytest.fixture
def page(tmp_data_dir, install_checkpoint, monkeypatch):
    """A realistic DATA_DIR: a real sidecar, a forecast from its model, history."""
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    model_path = install_checkpoint()
    version = model_path.stem.removeprefix("model_")
    write_forecast(tmp_data_dir, version)
    write_history(tmp_data_dir)
    sidecar = json.loads(
        (tmp_data_dir / "models" / f"metrics_{version}.json").read_text("utf-8")
    )
    return version, sidecar


def chart_specs(at):
    return [json.loads(el.proto.spec) for el in at.get("vega_lite_chart")]


def flow_and_rain(spec):
    """The hydrograph is one layer: [flow layers, upside-down rain bars]."""
    flow, rain = spec["layer"]
    assert rain["mark"]["type"] == "bar"
    return flow, rain


def flow_layers(spec):
    return flow_and_rain(spec)[0]["layer"]


def line_layers_coloured_by_status(spec):
    """Line layers whose colour comes from the status field (the median line)."""
    return [
        layer
        for layer in flow_layers(spec)
        if (layer["mark"]["type"] if isinstance(layer["mark"], dict) else layer["mark"])
        == "line"
        and layer.get("encoding", {}).get("color", {}).get("field") == "status"
    ]


# --- AC-5.2, 5.3, 5.4 ---------------------------------------------------------------


def test_app_renders_with_data(page):
    version, sidecar = page
    at = run_app()
    assert at.title[0].value == "Flow expected to fall to about 5.3 cfs by Saturday"
    assert len(at.metric) == 3
    test = sidecar["horizons"]["1"]["test"]
    assert at.metric[0].value == "8.7 cfs"
    assert at.metric[0].label == "t+1 median (Thu Sep 24)"  # TF4
    assert at.metric[1].value == f"{test['skill_mae']:+.2f}"
    assert at.metric[2].value == f"{test['coverage_80']:.0%}"
    (spec,) = chart_specs(at)
    text = json.dumps(spec)
    assert '"title": "cfs (log scale)"' in text
    for layer in flow_layers(spec):  # every flow layer shares the log axis (D33)
        assert layer["encoding"]["y"]["scale"] == {"type": "log"}
        assert layer["encoding"]["y"]["axis"]["grid"] is False  # no horizontal lines
    _, rain = flow_and_rain(spec)
    rain_y = rain["encoding"]["y"]
    assert rain_y["title"] == "rain (mm)"
    assert rain_y["scale"]["reverse"] is True  # hangs from the top
    assert rain_y["scale"]["domain"] == [0, 12.5 * 3]  # wettest day x headroom
    assert rain_y["axis"] == {"grid": False, "orient": "right"}
    assert rain["encoding"]["color"]["scale"]["domain"] == ["past", "forecast"]
    assert spec["resolve"]["scale"]["y"] == "independent"
    assert '"strokeDash": [6, 4]' in text  # dashed persistence line
    assert text.count('"type": "area"') == 2  # 80% and 95% bands
    assert '"type": "temporal"' in text
    (line,) = line_layers_coloured_by_status(spec)
    assert line["encoding"]["color"]["scale"]["domain"] == ["forecast"]  # no "past"
    assert not at.warning  # a fresh, final forecast shows no warning banner
    assert "grey" not in at.caption[-1].value  # nothing is past, so no grey key


def test_history_is_sixty_days_ending_at_d0(page, tmp_data_dir):
    history = dashboard.load_history(tmp_data_dir, "2026-09-23")
    assert len(history) == 60
    assert (history["date"].min(), history["date"].max()) == (
        "2026-07-26",
        "2026-09-23",
    )


# --- AC-5.1 ------------------------------------------------------------------------


def test_app_metrics_match_forecast_model(page, tmp_data_dir):
    version, sidecar = page
    newer = json.loads(json.dumps(sidecar))
    newer["model_version"] = "20991231T000000Z"
    newer["horizons"]["1"]["test"]["skill_mae"] = -0.99
    newer["horizons"]["1"]["test"]["coverage_80"] = 0.11
    (tmp_data_dir / "models" / "metrics_20991231T000000Z.json").write_text(
        json.dumps(newer)
    )
    at = run_app()
    assert at.metric[1].value == f"{sidecar['horizons']['1']['test']['skill_mae']:+.2f}"
    assert at.metric[1].value != "-0.99"

    (tmp_data_dir / "models" / f"metrics_{version}.json").unlink()
    at = run_app()
    assert len(at.metric) == 3
    assert at.metric[1].value == at.metric[2].value == "unavailable"
    assert any(
        f"metrics unavailable for model {version}" in c.value for c in at.caption
    )


# --- AC-5.5 ------------------------------------------------------------------------


def test_app_warnings(tmp_data_dir, install_checkpoint, monkeypatch):
    monkeypatch.setenv("AS_OF_DATE", "2026-09-25")  # the forecast was issued 09-24
    version = install_checkpoint().stem.removeprefix("model_")
    write_forecast(
        tmp_data_dir,
        version,
        stale_days=1,
        latest_obs_provisional=True,
    )
    write_history(tmp_data_dir)
    at = run_app()
    warnings = [w.value for w in at.warning]
    assert (
        "Forecast outdated — last issued 2026-09-24 from data through 2026-09-23"
        in warnings
    )
    assert any("1 day(s) behind" in w for w in warnings)
    assert any("provisional" in i.value for i in at.info)
    (spec,) = chart_specs(at)
    text = json.dumps(spec)
    assert '"past"' in text and "#9e9e9e" in text  # 2026-09-24 greyed and labelled
    assert "grey: forecast days already past" in at.caption[-1].value
    (line,) = line_layers_coloured_by_status(spec)  # the line itself turns grey
    assert line["encoding"]["color"]["scale"] == {
        "domain": ["forecast", "past"],
        "range": ["#08306b", "#9e9e9e"],
    }


# --- AC-5.6 ------------------------------------------------------------------------


def test_app_empty_dir(tmp_data_dir):
    at = run_app()
    assert at.info[0].value == dashboard.EMPTY_MESSAGE
    assert not at.metric
    assert not at.get("vega_lite_chart")


def test_only_loopback_is_allowed_here():
    """The allow_hosts marker opens loopback only; external hosts stay blocked."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # creation is allowed
    try:
        with pytest.raises(SocketConnectBlockedError):
            sock.connect(("93.184.215.14", 443))  # an external IP: no DNS involved
    finally:
        sock.close()
