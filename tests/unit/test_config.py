from datetime import date

import pytest

from streamforecast import config

pytestmark = pytest.mark.unit

# name: (documented default, override string, parsed override)
ENVIRONMENT_TABLE = {
    "DATA_DIR": ("./data", "/tmp/sf", "/tmp/sf"),
    "USGS_SITE": ("02085000", "99999999", "99999999"),
    "LATITUDE": (36.0711, "35.5", 35.5),
    "LONGITUDE": (-79.0956, "-78.25", -78.25),
    "TIMEZONE": ("America/New_York", "America/Chicago", "America/Chicago"),
    "HISTORY_START": (date(2018, 1, 1), "2019-01-01", date(2019, 1, 1)),
    "TRAIN_END": (date(2023, 12, 31), "2022-12-31", date(2022, 12, 31)),
    "CALIBRATION_START": (date(2024, 2, 1), "2024-03-01", date(2024, 3, 1)),
    "CALIBRATION_END": (date(2024, 12, 31), "2024-11-30", date(2024, 11, 30)),
    "LEADS_START": (date(2024, 1, 25), "2024-02-10", date(2024, 2, 10)),
    "USGS_LOOKBACK_DAYS": (30, "10", 10),
    "MAX_FFILL_DAYS": (2, "3", 3),
    "MAX_STALE_DAYS": (2, "-1", -1),
    "RUN_INTERVAL_HOURS": (24, "6", 6),
    "RETRAIN_DAYS": (7, "14", 14),
    "INGEST_SOURCE": ("live", "fixtures", "fixtures"),
    "MRMS_ENABLED": (True, "0", False),
    "USGS_API_KEY": ("", "secret-key", "secret-key"),
    "HTTP_TIMEOUT_S": (30, "5", 5),
    "HTTP_RETRIES": (3, "1", 1),
    "LOG_LEVEL": ("INFO", "DEBUG", "DEBUG"),
    "AS_OF_DATE": (None, "2026-09-23", date(2026, 9, 23)),
    "DASHBOARD_PORT": (8501, "8502", 8502),
}


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENVIRONMENT_TABLE:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_settings_cover_exactly_the_environment_table():
    assert set(config.as_env(config.Settings())) == set(ENVIRONMENT_TABLE)


@pytest.mark.parametrize("name", ENVIRONMENT_TABLE)
def test_default(clean_env, name):
    settings = config.load()
    assert getattr(settings, name.lower()) == ENVIRONMENT_TABLE[name][0]


@pytest.mark.parametrize("name", ENVIRONMENT_TABLE)
def test_env_var_overrides(clean_env, name):
    _, raw, expected = ENVIRONMENT_TABLE[name]
    clean_env.setenv(name, raw)
    assert getattr(config.load(), name.lower()) == expected


def test_empty_value_means_default(clean_env):
    clean_env.setenv("DATA_DIR", "")
    clean_env.setenv("AS_OF_DATE", "")
    settings = config.load()
    assert settings.data_dir == "./data"
    assert settings.as_of_date is None


@pytest.mark.parametrize(
    "name, raw",
    [
        ("USGS_LOOKBACK_DAYS", "thirty"),
        ("AS_OF_DATE", "09/23/2026"),
        ("MRMS_ENABLED", "yes"),
        ("INGEST_SOURCE", "http"),
    ],
)
def test_invalid_value_names_the_variable(clean_env, name, raw):
    clean_env.setenv(name, raw)
    with pytest.raises(ValueError, match=name):
        config.load()


def test_as_env_prints_documented_forms(clean_env):
    env = config.as_env(config.load())
    assert env["DATA_DIR"] == "./data"
    assert env["HISTORY_START"] == "2018-01-01"
    assert env["MRMS_ENABLED"] == "1"
    assert env["AS_OF_DATE"] == ""
