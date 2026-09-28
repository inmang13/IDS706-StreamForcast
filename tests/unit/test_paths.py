from datetime import datetime, timedelta, timezone

import pytest

from streamforecast import paths

pytestmark = pytest.mark.unit

EXPECTED_SUBDIRS = [
    "raw/usgs",
    "raw/weather_hist",
    "raw/weather_fc",
    "raw/weather_leads",
    "raw/mrms",
    "features",
    "models",
    "forecasts",
]


def test_subdirs_created_on_demand(tmp_data_dir):
    assert list(tmp_data_dir.iterdir()) == []
    for name, rel in zip(paths.SUBDIRS, EXPECTED_SUBDIRS):
        result = paths.subdir(name)
        assert result == tmp_data_dir / rel
        assert result.is_dir()


def test_unknown_subdir_rejected(tmp_data_dir):
    with pytest.raises(KeyError):
        paths.subdir("nope")


def test_timestamp_format_is_utc():
    eastern = timezone(timedelta(hours=-4))
    moment = datetime(2026, 9, 23, 22, 30, 5, tzinfo=eastern)
    assert paths.timestamp(moment) == "20260924T023005Z"
    assert paths.timestamped_name("usgs", ".csv", moment) == (
        "usgs_20260924T023005Z.csv"
    )


def test_timestamped_names_sort_chronologically():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    moments = [start + timedelta(seconds=s) for s in (0, 9, 10, 59, 3600, 86400 * 40)]
    names = [paths.timestamped_name("usgs", ".csv", m) for m in moments]
    assert sorted(names) == names


def test_newest_returns_last_match_or_none(tmp_path):
    assert paths.newest(tmp_path, "usgs_*.csv") is None
    for ts in ("20260101T000000Z", "20260923T140000Z", "20260501T000000Z"):
        (tmp_path / f"usgs_{ts}.csv").write_text("x")
    (tmp_path / "usgs_20261231T000000Z.csv.tmp").write_text("partial")
    (tmp_path / "weather_fc_20270101T000000Z.csv").write_text("other")
    assert paths.newest(tmp_path, "usgs_*.csv") == (
        tmp_path / "usgs_20260923T140000Z.csv"
    )
