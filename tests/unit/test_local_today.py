import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import streamforecast
from streamforecast import paths

pytestmark = pytest.mark.unit

# 2026-09-23 23:30 America/New_York (EDT) is 2026-09-24 03:30 UTC.
FROZEN_UTC = datetime(2026, 9, 24, 3, 30, tzinfo=timezone.utc)

# Calendar "today" from anything but paths.local_today(): date/datetime.today(),
# a naive datetime.now(), utcnow(), or pandas' Timestamp.now/today/utcnow.
CLOCK_CALLS = re.compile(
    r"\bdate(time)?\.today\(|\bdatetime\.now\(\s*\)|\.utcnow\(|"
    r"\bTimestamp\.(now|today)\("
)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_UTC.astimezone(tz) if tz else FROZEN_UTC.replace(tzinfo=None)


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(paths, "datetime", FrozenDatetime)
    monkeypatch.delenv("TIMEZONE", raising=False)
    monkeypatch.delenv("AS_OF_DATE", raising=False)
    return monkeypatch


def test_late_evening_eastern_is_still_today_not_utc_tomorrow(frozen):
    assert paths.local_today() == date(2026, 9, 23)


def test_as_of_date_overrides_real_date(frozen):
    frozen.setenv("AS_OF_DATE", "2026-09-23")
    frozen.setattr(
        FrozenDatetime,
        "now",
        classmethod(lambda cls, tz=None: datetime(2030, 1, 1, tzinfo=tz)),
    )
    assert paths.local_today() == date(2026, 9, 23)


def test_utc_now_ignores_as_of_date(frozen):
    frozen.setenv("AS_OF_DATE", "2020-01-01")
    assert paths.utc_now() == FROZEN_UTC
    assert paths.timestamp() == "20260924T033000Z"


def test_no_module_calls_date_today():
    root = Path(streamforecast.__file__).parents[1]
    sources = [
        *(root / "streamforecast").rglob("*.py"),
        *(root / "tests").rglob("*.py"),
    ]
    offenders = [
        str(p)
        for p in sources
        if p.resolve() != Path(__file__).resolve()  # this file holds examples
        and CLOCK_CALLS.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "source",
    [
        "date.today()",
        "datetime.today()",
        "datetime.now()",
        "datetime.now( )",
        "datetime.utcnow()",
        "pd.Timestamp.now()",
        "pd.Timestamp.today()",
        "pd.Timestamp.utcnow()",
    ],
)
def test_clock_grep_catches_local_and_naive_clocks(source):
    """TF15: the grep must catch every clock call that bypasses local_today()."""
    assert CLOCK_CALLS.search(source)


@pytest.mark.parametrize(
    "source", ["datetime.now(timezone.utc)", "datetime.now(ZoneInfo(tz))", "now(tz)"]
)
def test_clock_grep_allows_zone_aware_now(source):
    assert not CLOCK_CALLS.search(source)
