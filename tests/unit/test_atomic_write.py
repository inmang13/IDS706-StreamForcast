import pytest

from streamforecast import paths

pytestmark = pytest.mark.unit


def test_success_leaves_exactly_the_target(tmp_path):
    target = tmp_path / "usgs_20260923T140000Z.csv"
    paths.atomic_write(target, lambda tmp: tmp.write_text("date,flow_cfs\n"))
    assert [p.name for p in tmp_path.iterdir()] == [target.name]
    assert target.read_text() == "date,flow_cfs\n"


def test_failure_leaves_no_target_and_no_tmp(tmp_path):
    target = tmp_path / "usgs_20260923T140000Z.csv"

    def half_write(tmp):
        tmp.write_text("date,flow_cfs\n2026-09-")
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        paths.atomic_write(target, half_write)
    assert list(tmp_path.iterdir()) == []


def test_failure_keeps_previous_version(tmp_path):
    target = tmp_path / "forecast.csv"
    target.write_text("old")

    def boom(tmp):
        tmp.write_text("new, partial")
        raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        paths.atomic_write(target, boom)
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["forecast.csv"]


def test_tmp_file_never_matches_published_pattern(tmp_path):
    seen = []

    def check_during_write(tmp):
        tmp.write_text("x")
        seen.append(sorted(p.name for p in tmp_path.glob("usgs_*.csv")))

    paths.atomic_write(tmp_path / "usgs_20260923T140000Z.csv", check_during_write)
    assert seen == [[]]
