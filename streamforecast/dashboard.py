"""Stage 5: Streamlit dashboard: 60 days of observed flow, the 3-day fan, and skill.

Reads only files under ``DATA_DIR`` (AC-5.1):

- the newest ``forecasts/forecast_*.csv``;
- the newest ``features/features_*.csv`` (observed history);
- ``models/metrics_<model_version>.json`` for the displayed forecast's model.

Never writes, never loads a checkpoint, never calls the network.
Run with ``make dashboard`` (``streamlit run streamforecast/dashboard.py``).
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from streamforecast import config, paths

HISTORY_DAYS = 60
STATUS_COLORS = {"forecast": "#08306b", "past": "#9e9e9e"}  # AC-5.5: past is grey
RAIN_COLORS = {"past": "#5b8db8", "forecast": "#e08214"}
LOG_FLOOR_CFS = 0.1  # a log axis cannot show 0; lower values are drawn at this floor
HOLD_BAND = 0.10  # within +/-10% of the latest observation reads as "hold"
EMPTY_MESSAGE = "No forecast yet — the pipeline has not produced one"
SITE_NAME = "Eno River at Hillsborough, NC"


# --- Loading (pure; cached by file path and modification time) -----------------------


@st.cache_data(ttl=300, show_spinner=False)
def _read_csv(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(
        path,
        dtype={
            "date": str,
            "issue_date": str,
            "valid_date": str,
            "fc_fetched_date": str,
            "model_version": str,
            "qualifier": str,
        },
    )


@st.cache_data(ttl=300, show_spinner=False)
def _read_json(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _newest(directory: Path, pattern: str) -> Path | None:
    return paths.newest(directory, pattern) if directory.is_dir() else None


def load_forecast(data_dir: Path) -> pd.DataFrame | None:
    path = _newest(data_dir / "forecasts", "forecast_*.csv")
    return None if path is None else _read_csv(str(path), path.stat().st_mtime)


def load_features(data_dir: Path) -> pd.DataFrame | None:
    path = _newest(data_dir / "features", "features_*.csv")
    return None if path is None else _read_csv(str(path), path.stat().st_mtime)


def _window(features: pd.DataFrame, d0: str) -> pd.DataFrame:
    start = (date.fromisoformat(d0) - timedelta(days=HISTORY_DAYS - 1)).isoformat()
    return features.loc[(features["date"] >= start) & (features["date"] <= d0)]


def load_history(data_dir: Path, d0: str) -> pd.DataFrame:
    """Observed flow for the 60 days ending at d0, from the newest features file."""
    features = load_features(data_dir)
    if features is None:
        return pd.DataFrame(columns=["date", "flow_cfs"])
    return _window(features, d0)[["date", "flow_cfs"]].reset_index(drop=True)


def rain_frame(features: pd.DataFrame | None, d0: str) -> pd.DataFrame:
    """Daily rain for the chart's top panel: ``date, rain_mm, kind``.

    ``past``: Open-Meteo rain on each day of the 60-day window (``precip_0``).
    ``forecast``: the rain the forecast used for d0+1..d0+3 (``precip_f1..f3`` of the
    live row), shown only when the newest features row is the forecast's d0.
    """
    columns = ["date", "rain_mm", "kind"]
    if features is None or features.empty:
        return pd.DataFrame(columns=columns)
    window = _window(features, d0)
    past = pd.DataFrame(
        {"date": window["date"], "rain_mm": window["precip_0"], "kind": "past"}
    )
    live = features.iloc[-1]
    ahead = []
    if live["date"] == d0:
        start = date.fromisoformat(d0)
        ahead = [
            {
                "date": (start + timedelta(days=h)).isoformat(),
                "rain_mm": live[f"precip_f{h}"],
                "kind": "forecast",
            }
            for h in (1, 2, 3)
        ]
    out = pd.concat([past, pd.DataFrame(ahead, columns=columns)], ignore_index=True)
    return out.dropna(subset=["rain_mm"]).reset_index(drop=True)


def load_metrics(data_dir: Path, model_version: str) -> dict | None:
    """The sidecar of the model that made the forecast, never simply the newest."""
    path = data_dir / "models" / f"metrics_{model_version}.json"
    return _read_json(str(path), path.stat().st_mtime) if path.exists() else None


# --- Presentation logic (pure) -------------------------------------------------------


def about(cfs: float) -> str:
    """A rounded, readable flow: 8.66 -> '8.7', 123.4 -> '120', 8180 -> '8,200'."""
    if cfs < 10:
        return f"{cfs:.1f}"
    digits = len(str(int(cfs))) - 2
    return f"{round(cfs, -digits):,.0f}"


def headline(forecast: pd.DataFrame) -> str:
    """A sentence stating the finding (AC-5.3): rise, fall or hold by the h=3 median."""
    last = forecast.sort_values("horizon").iloc[-1]
    latest = float(last["latest_obs_cfs"])
    median = float(last["median_cfs"])
    weekday = date.fromisoformat(last["valid_date"]).strftime("%A")
    if latest > 0 and abs(median - latest) <= HOLD_BAND * latest:
        return f"Flow expected to hold near {about(median)} cfs through {weekday}"
    if median == latest == 0:
        return f"Flow expected to hold near 0 cfs through {weekday}"
    direction = "rise" if median > latest else "fall"
    return f"Flow expected to {direction} to about {about(median)} cfs by {weekday}"


def issued_local_date(forecast: pd.DataFrame, tz: str) -> date:
    created = forecast["created_at_utc"].iloc[0].replace("Z", "+00:00")
    return datetime.fromisoformat(created).astimezone(ZoneInfo(tz)).date()


def notices(forecast: pd.DataFrame, today: date, tz: str) -> list[tuple[str, str]]:
    """(kind, text) banners for AC-5.5; kind is 'warning' or 'info'."""
    first = forecast.sort_values("horizon").iloc[0]
    out = []
    issued = issued_local_date(forecast, tz)
    if issued < today:
        out.append(
            (
                "warning",
                f"Forecast outdated — last issued {issued} from data through "
                f"{first['issue_date']}",
            )
        )
    stale = int(first["stale_days"])
    if stale > 0:
        out.append(
            (
                "warning",
                f"USGS data are {stale} day(s) behind: this forecast starts from "
                f"{first['issue_date']}, so some forecast days may already be past.",
            )
        )
    flags = [
        name
        for name, column in (
            ("provisional", "latest_obs_provisional"),
            ("estimated", "latest_obs_estimated"),
        )
        if bool(first[column])
    ]
    if flags:
        latest = about(float(first["latest_obs_cfs"]))
        out.append(
            (
                "info",
                f"The latest observation ({latest} cfs on {first['issue_date']}) is "
                f"{' and '.join(flags)} and may be revised by USGS.",
            )
        )
    return out


def fan_frame(forecast: pd.DataFrame, today: date) -> pd.DataFrame:
    """Forecast rows plus a d0 anchor at the latest observation; marks past points."""
    f = forecast.sort_values("horizon")
    first = f.iloc[0]
    anchor = {
        "valid_date": first["issue_date"],
        "median_cfs": first["latest_obs_cfs"],
        "lo80_cfs": first["latest_obs_cfs"],
        "hi80_cfs": first["latest_obs_cfs"],
        "lo95_cfs": first["latest_obs_cfs"],
        "hi95_cfs": first["latest_obs_cfs"],
        "persistence_cfs": first["persistence_cfs"],
    }
    cols = list(anchor)
    frame = pd.concat([pd.DataFrame([anchor]), f[cols]], ignore_index=True)
    frame["status"] = [
        "observed" if i == 0 else ("past" if d < today.isoformat() else "forecast")
        for i, d in enumerate(frame["valid_date"])
    ]
    return frame


def median_segments(fan: pd.DataFrame) -> pd.DataFrame:
    """The median line split by status, so past days are drawn grey (AC-5.5).

    The past segment runs from the d0 anchor through the last past day; the forecast
    segment starts at that point, so the two segments join without a gap.
    """
    anchor = fan.iloc[[0]]
    past = fan.loc[fan["status"] == "past"]
    ahead = fan.loc[fan["status"] == "forecast"]
    parts = []
    if len(past):
        parts.append(pd.concat([anchor, past]).assign(status="past"))
    if len(ahead):
        join = past.iloc[[-1]] if len(past) else anchor
        parts.append(pd.concat([join, ahead]).assign(status="forecast"))
    return pd.concat(parts, ignore_index=True)[["valid_date", "median_cfs", "status"]]


def status_scale(fan: pd.DataFrame) -> alt.Scale:
    """Colour scale listing only the statuses on the chart (no stray 'past' entry)."""
    present = [s for s in STATUS_COLORS if (fan["status"] == s).any()]
    return alt.Scale(domain=present, range=[STATUS_COLORS[s] for s in present])


RAIN_HEADROOM = 3  # rain axis runs to 3x the wettest day: bars fill the top third


def _log_y(field: str) -> alt.Y:
    return alt.Y(
        f"{field}:Q",
        title="cfs (log scale)",
        scale=alt.Scale(type="log"),
        axis=alt.Axis(grid=False),
    )


def fan_chart(
    history: pd.DataFrame, fan: pd.DataFrame, rain: pd.DataFrame
) -> alt.LayerChart:
    """One hydrograph: observed flow, the 95% and 80% bands, the median and the
    dashed persistence line on a log axis, with daily rain hanging upside down from
    the top on its own right-hand axis (AC-5.2, D33)."""
    x = alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d", grid=False))
    color = alt.Color(
        "status:N", scale=status_scale(fan), legend=alt.Legend(title="flow")
    )
    flow_cols = ["median_cfs", "lo80_cfs", "hi80_cfs", "lo95_cfs", "hi95_cfs"]
    shown = fan.copy()
    plotted = flow_cols + ["persistence_cfs"]
    shown[plotted] = shown[plotted].clip(lower=LOG_FLOOR_CFS)
    segments = median_segments(shown).rename(columns={"valid_date": "date"})
    true_values = fan[["valid_date"] + flow_cols].rename(
        columns={c: f"{c}_value" for c in flow_cols}
    )
    shown = shown.merge(true_values, on="valid_date").rename(
        columns={"valid_date": "date"}
    )
    obs = history.assign(cfs=history["flow_cfs"].clip(lower=LOG_FLOOR_CFS))

    observed = (
        alt.Chart(obs)
        .mark_line(color="#1f4e79")
        .encode(
            x=x,
            y=_log_y("cfs"),
            tooltip=["date:T", alt.Tooltip("flow_cfs:Q", title="cfs", format=",.1f")],
        )
    )
    band95 = (
        alt.Chart(shown)
        .mark_area(color="#6baed6", opacity=0.25)
        .encode(x=x, y=_log_y("lo95_cfs"), y2="hi95_cfs:Q")
    )
    band80 = (
        alt.Chart(shown)
        .mark_area(color="#3182bd", opacity=0.45)
        .encode(x=x, y=_log_y("lo80_cfs"), y2="hi80_cfs:Q")
    )
    median = (
        alt.Chart(segments)
        .mark_line(strokeWidth=2)
        .encode(x=x, y=_log_y("median_cfs"), color=color)
    )
    points = (
        alt.Chart(shown.loc[shown["status"] != "observed"])
        .mark_point(filled=True, size=60)
        .encode(
            x=x,
            y=_log_y("median_cfs"),
            color=color,
            tooltip=[
                alt.Tooltip("date:T", title="valid"),
                alt.Tooltip("median_cfs_value:Q", title="median", format=",.1f"),
                alt.Tooltip("lo80_cfs_value:Q", title="80% low", format=",.1f"),
                alt.Tooltip("hi80_cfs_value:Q", title="80% high", format=",.1f"),
                alt.Tooltip("lo95_cfs_value:Q", title="95% low", format=",.1f"),
                alt.Tooltip("hi95_cfs_value:Q", title="95% high", format=",.1f"),
                "status:N",
            ],
        )
    )
    past_labels = (
        alt.Chart(shown.loc[shown["status"] == "past"])
        .mark_text(dy=-12, color="#757575")
        .encode(x=x, y=_log_y("median_cfs"), text="status:N")
    )
    persistence = (
        alt.Chart(shown)
        .mark_line(color="#d95f02", strokeDash=[6, 4])
        .encode(x=x, y=_log_y("persistence_cfs"))
    )
    flow = alt.layer(band95, band80, observed, persistence, median, points, past_labels)

    kinds = [k for k in RAIN_COLORS if (rain["kind"] == k).any()]
    wettest = float(rain["rain_mm"].max()) if len(rain) else 0.0
    rain_bars = (
        alt.Chart(rain)
        .mark_bar(opacity=0.55)
        .encode(
            x=x,
            y=alt.Y(
                "rain_mm:Q",
                title="rain (mm)",
                scale=alt.Scale(
                    reverse=True, domain=[0, max(wettest, 1.0) * RAIN_HEADROOM]
                ),
                axis=alt.Axis(orient="right", grid=False),
            ),
            color=alt.Color(
                "kind:N",
                scale=alt.Scale(domain=kinds, range=[RAIN_COLORS[k] for k in kinds]),
                legend=alt.Legend(title="rain"),
            ),
            tooltip=[
                "date:T",
                alt.Tooltip("rain_mm:Q", title="rain (mm)", format=".1f"),
                "kind:N",
            ],
        )
    )
    return (
        alt.layer(flow, rain_bars)
        .resolve_scale(y="independent", color="independent")
        .properties(height=400)
    )


def chart_caption(fan: pd.DataFrame, rain: pd.DataFrame) -> str:
    """Explain only what is drawn (no 'grey' when no forecast day is past)."""
    rain_text = (
        "Bars hanging from the top: daily rain from Open-Meteo, right axis in mm "
        "(blue: past days"
    )
    if (rain["kind"] == "forecast").any():
        rain_text += "; orange: forecast rain the model used"
    parts = [
        rain_text + ")",
        "flow on a log axis",
        "dark band: 80% range",
        "light band: 95% range",
        "dashed orange: no change (persistence)",
    ]
    if (fan["status"] == "past").any():
        parts.append("grey: forecast days already past")
    parts.append(f"values below {LOG_FLOOR_CFS} cfs are drawn at {LOG_FLOOR_CFS}")
    return " · ".join(parts) + "."


def kpis(forecast: pd.DataFrame, metrics: dict | None) -> list[tuple[str, str, str]]:
    """Exactly three (label, value, help) tiles (AC-5.4)."""
    t1 = forecast.sort_values("horizon").iloc[0]
    median = float(t1["median_cfs"])
    tiles = [
        (
            f"Tomorrow's median ({date.fromisoformat(t1['valid_date']):%a %b %d})",
            f"{about(median)} cfs",
            f"80% range {about(float(t1['lo80_cfs']))}–"
            f"{about(float(t1['hi80_cfs']))} cfs",
        )
    ]
    test = metrics["horizons"]["1"]["test"] if metrics else None
    skill = test.get("skill_mae") if test else None
    cover = test.get("coverage_80") if test else None
    tiles.append(
        (
            "t+1 skill vs persistence",
            "unavailable" if skill is None else f"{skill:+.2f}",
            "1 − MAE(model) / MAE(no change) on the 2025+ test period; above 0 means "
            "the model beats assuming no change.",
        )
    )
    tiles.append(
        (
            "t+1 80% band coverage",
            "unavailable" if cover is None else f"{cover:.0%}",
            "Share of 2025+ test outcomes that fell inside the 80% band (target 80%).",
        )
    )
    return tiles


def limitation_caption(metrics: dict) -> str:
    """RK6: the bands are weakest on flood days; say so next to the KPIs."""
    test = metrics["horizons"]["1"]["test"]
    high = test.get("coverage_80_high")
    threshold = metrics.get("high_flow_threshold_cfs")
    if high is None or threshold is None:
        return (
            "Known limitation: bands are calibrated on one year (2024) with few floods."
        )
    return (
        f"Known limitation: on flood days (above {about(threshold)} cfs) only "
        f"{high:.0%} of test outcomes fell inside the 80% band "
        f"({test['n_high']} days). "
        "Treat the bands as too narrow during storms."
    )


# --- Page ------------------------------------------------------------------------


def render() -> None:
    st.set_page_config(page_title="StreamForecast", layout="wide")
    settings = config.load()
    data_dir = Path(settings.data_dir)
    forecast = load_forecast(data_dir)
    if forecast is None or forecast.empty:
        st.title("StreamForecast")
        st.info(EMPTY_MESSAGE)
        return

    today = paths.local_today(settings)
    first = forecast.sort_values("horizon").iloc[0]
    version = str(first["model_version"])
    metrics = load_metrics(data_dir, version)

    st.title(headline(forecast))
    st.caption(
        f"{SITE_NAME} (USGS {settings.usgs_site}) · 3-day forecast from data through "
        f"{first['issue_date']} · model {version}"
    )
    for kind, text in notices(forecast, today, settings.timezone):
        (st.warning if kind == "warning" else st.info)(text)

    with st.container(horizontal=True):
        for label, value, help_text in kpis(forecast, metrics):
            st.metric(label, value, help=help_text, border=True)
    if metrics is None:
        st.caption(f"metrics unavailable for model {version}")
    else:
        st.caption(limitation_caption(metrics))

    d0 = str(first["issue_date"])
    history = load_history(data_dir, d0)
    rain = rain_frame(load_features(data_dir), d0)
    fan = fan_frame(forecast, today)
    with st.container(border=True):
        st.altair_chart(fan_chart(history, fan, rain), width="stretch")
        st.caption(chart_caption(fan, rain))


if __name__ == "__main__":
    render()
