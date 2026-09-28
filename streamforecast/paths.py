"""Directories under DATA_DIR, timestamped file names, the local clock, atomic writes.

Calendar dates ("today", "yesterday", staleness) come from ``local_today()``.
Real UTC instants (log lines, file names, ``created_at_utc``) come from
``utc_now()``, which ``AS_OF_DATE`` never affects.
"""

import os
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from streamforecast import config

TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

SUBDIRS = {
    "usgs": "raw/usgs",
    "weather_hist": "raw/weather_hist",
    "weather_fc": "raw/weather_fc",
    "weather_leads": "raw/weather_leads",
    "mrms": "raw/mrms",
    "features": "features",
    "models": "models",
    "forecasts": "forecasts",
}


def data_dir(settings: config.Settings | None = None) -> Path:
    settings = settings or config.load()
    return Path(settings.data_dir)


def subdir(name: str, settings: config.Settings | None = None) -> Path:
    """Return ``DATA_DIR/<SUBDIRS[name]>``, creating it if needed."""
    if name not in SUBDIRS:
        raise KeyError(f"unknown data directory {name!r}; expected one of {SUBDIRS}")
    path = data_dir(settings) / SUBDIRS[name]
    path.mkdir(parents=True, exist_ok=True)
    return path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_today(settings: config.Settings | None = None) -> date:
    """``AS_OF_DATE`` if set, else today's date in ``TIMEZONE``. Never UTC."""
    settings = settings or config.load()
    if settings.as_of_date is not None:
        return settings.as_of_date
    return datetime.now(ZoneInfo(settings.timezone)).date()


def timestamp(moment: datetime | None = None) -> str:
    """UTC ``YYYYMMDDTHHMMSSZ``; sorts chronologically as a string."""
    moment = moment or utc_now()
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def timestamped_name(prefix: str, suffix: str, moment: datetime | None = None) -> str:
    """E.g. ``timestamped_name("usgs", ".csv")`` -> ``usgs_20260923T140000Z.csv``."""
    return f"{prefix}_{timestamp(moment)}{suffix}"


def newest(directory: Path, pattern: str) -> Path | None:
    """Lexicographically last file in ``directory`` matching ``pattern``, or None."""
    matches = sorted(Path(directory).glob(pattern))
    return matches[-1] if matches else None


def atomic_write(path: Path, write_fn: Callable[[Path], None]) -> Path:
    """Call ``write_fn(tmp)`` on ``<path>.tmp``, then move it onto ``path``.

    If ``write_fn`` raises, the temp file is removed and ``path`` is untouched.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        write_fn(tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path
