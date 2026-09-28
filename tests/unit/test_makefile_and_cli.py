"""AC-0.7: every documented target has a help line; `config` prints overrides."""

import re
from pathlib import Path

import pytest

from streamforecast.__main__ import main

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
DOCUMENTED_TARGETS = [
    "help",
    "install",
    "config",
    "format",
    "lint",
    "test",
    "test-unit",
    "test-regression",
    "test-integration",
    "ingest",
    "features",
    "train",
    "forecast",
    "coverage",
    "pipeline",
    "dashboard",
    "docker-build",
    "up",
    "down",
    "logs",
    "clean",
]


def test_every_documented_target_has_a_help_description():
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    described = set(re.findall(r"^([a-zA-Z_-]+):.*## \S", text, flags=re.M))
    assert [t for t in DOCUMENTED_TARGETS if t not in described] == []


def test_makefile_never_hardcodes_venv_paths():
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert ".venv/bin" not in text and ".venv/Scripts" not in text


def test_recipes_use_no_unix_only_tools():
    """make from PowerShell runs recipes without sh, so awk/grep/rm etc. don't exist."""
    recipes = [
        line
        for line in (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
        if line.startswith("\t") and not line.lstrip("\t").startswith("#")
    ]
    unix_tools = r"\b(awk|sed|grep|find|rm|cp|mv|cat|ls|mkdir|touch|test)\b"
    offenders = [
        r for r in recipes if re.match(rf"\s*@?-?{unix_tools}", r.lstrip("\t"))
    ]
    assert offenders == []


def test_config_command_prints_defaults_and_overrides(monkeypatch, capsys):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("USGS_SITE", raising=False)
    assert main(["config"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "DATA_DIR=./data" in out
    assert "USGS_SITE=02085000" in out
    assert "TIMEZONE=America/New_York" in out

    monkeypatch.setenv("DATA_DIR", "/tmp/sf")
    monkeypatch.setenv("USGS_SITE", "99999999")
    assert main(["config"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "DATA_DIR=/tmp/sf" in out
    assert "USGS_SITE=99999999" in out


def test_config_command_reports_invalid_value(monkeypatch, capsys):
    monkeypatch.setenv("HTTP_RETRIES", "many")
    assert main(["config"]) == 1
    assert "HTTP_RETRIES" in capsys.readouterr().err
