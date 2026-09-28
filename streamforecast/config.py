"""Settings from environment variables, with the defaults documented in docs/plan.md.

Each field's environment variable is its name in upper case. An empty variable
counts as unset, so Compose's ``${VAR:-}`` passes through cleanly.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date

INGEST_SOURCES = ("live", "fixtures")


@dataclass(frozen=True)
class Settings:
    data_dir: str = "./data"
    usgs_site: str = "02085000"
    latitude: float = 36.0711
    longitude: float = -79.0956
    timezone: str = "America/New_York"
    history_start: date = date(2018, 1, 1)
    train_end: date = date(2023, 12, 31)
    calibration_start: date = date(2024, 2, 1)
    calibration_end: date = date(2024, 12, 31)
    leads_start: date = date(2024, 1, 25)
    usgs_lookback_days: int = 30
    max_ffill_days: int = 2
    max_stale_days: int = 2
    run_interval_hours: int = 24
    retrain_days: int = 7
    ingest_source: str = "live"
    mrms_enabled: bool = True
    usgs_api_key: str = ""
    http_timeout_s: int = 30
    http_retries: int = 3
    log_level: str = "INFO"
    as_of_date: date | None = None
    dashboard_port: int = 8501


def _parse(name: str, kind, raw: str):
    try:
        if kind is date or kind == date | None:
            return date.fromisoformat(raw)
        if kind is bool:
            if raw not in ("0", "1"):
                raise ValueError("expected 0 or 1")
            return raw == "1"
        return kind(raw)
    except ValueError as exc:
        raise ValueError(f"invalid {name}={raw!r}: {exc}") from None


def load(environ: Mapping[str, str] | None = None) -> Settings:
    """Read every setting from ``environ`` (default ``os.environ``)."""
    env = os.environ if environ is None else environ
    values = {}
    for f in fields(Settings):
        raw = env.get(f.name.upper(), "")
        if raw != "":
            values[f.name] = _parse(f.name.upper(), f.type, raw)
    settings = Settings(**values)
    if settings.ingest_source not in INGEST_SOURCES:
        raise ValueError(
            f"invalid INGEST_SOURCE={settings.ingest_source!r}: "
            f"expected one of {', '.join(INGEST_SOURCES)}"
        )
    return settings


def as_env(settings: Settings) -> dict[str, str]:
    """Settings as ``NAME -> value`` strings, in the form they are set."""
    out = {}
    for f in fields(Settings):
        value = getattr(settings, f.name)
        if value is None:
            text = ""
        elif isinstance(value, bool):
            text = "1" if value else "0"
        elif isinstance(value, date):
            text = value.isoformat()
        else:
            text = str(value)
        out[f.name.upper()] = text
    return out
