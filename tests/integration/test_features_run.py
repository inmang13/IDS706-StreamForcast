"""Ingest fixture mode -> features.main(), end to end on a temporary DATA_DIR."""

import json

import pandas as pd
import pytest

from streamforecast import features, ingest

pytestmark = pytest.mark.integration


@pytest.fixture
def fixture_raw(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("INGEST_SOURCE", "fixtures")
    monkeypatch.setenv("AS_OF_DATE", "2026-09-24")
    assert ingest.main() == 0
    monkeypatch.delenv("INGEST_SOURCE")
    return tmp_data_dir


def test_features_from_fixture_mode_ingest(fixture_raw, capsys):
    assert features.main() == 0
    out_dir = fixture_raw / "features"
    (path,) = out_dir.glob("features_*.csv")
    out = pd.read_csv(path, dtype={"date": str, "fc_fetched_date": str})
    rows = out.set_index("date")
    assert rows.loc["2026-09-16", "precip_f2"] == 0.7  # AC-2.8 fixture check
    assert rows.index[-1] == "2026-09-23"
    assert rows.iloc[-1]["fc_fetched_date"] == "2026-09-24"
    check = json.loads(next(out_dir.glob("rain_check_*.json")).read_text())
    assert any(
        d["date"] == "2026-09-18" for d in check["series"]["lead0"]["top_disagreements"]
    )
    assert check["series"]["lead0"]["false_alarms"] >= 1
    err = capsys.readouterr().err
    assert "wrote 10 rows (latest d0=2026-09-23)" not in err  # first 2 lack lags
    assert "wrote 8 rows (latest d0=2026-09-23)" in err
    assert "lead-matched rows: 7 (from 2024-02-01)" in err


def test_features_rerun_keeps_at_most_five_files(fixture_raw):
    for _ in range(7):
        assert features.main() == 0
    out_dir = fixture_raw / "features"
    for pattern in ("features_*.csv", "rain_check_*.json", "mrms_*.csv"):
        assert len(list(out_dir.glob(pattern))) <= 5, pattern
