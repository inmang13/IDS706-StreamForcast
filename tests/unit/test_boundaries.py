"""Stage isolation (D11, AC-0.5): stages share only config, paths, logs."""

import ast
from pathlib import Path

import pytest

import streamforecast

pytestmark = pytest.mark.unit

PACKAGE = Path(streamforecast.__file__).parent
STAGES = {"ingest", "features", "train", "forecast", "dashboard", "scheduler"}
SHARED = {"config", "paths", "logs"}
HEAVY_LIBS = {"requests", "sklearn", "pandas", "numpy", "joblib", "streamlit"}


def imported_modules(source: str) -> set[str]:
    """Dotted names imported by ``source``, with package-relative imports resolved."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = "streamforecast" + (f".{base}" if base else "")
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def stage_imports(source: str) -> set[str]:
    return {
        name.split(".")[1]
        for name in imported_modules(source)
        if name.startswith("streamforecast.") and name.split(".")[1] in STAGES
    }


@pytest.mark.parametrize("module", sorted(STAGES))
def test_stage_imports_no_other_stage(module):
    path = PACKAGE / f"{module}.py"
    if not path.exists():
        pytest.skip(f"{module}.py not built yet")
    assert stage_imports(path.read_text(encoding="utf-8")) == set()


@pytest.mark.parametrize("module", sorted(SHARED))
def test_shared_modules_import_no_stage_or_heavy_library(module):
    names = imported_modules((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    assert stage_imports("\n".join(f"import {n}" for n in names)) == set()
    assert {n.split(".")[0] for n in names} & HEAVY_LIBS == set()


def test_dashboard_never_loads_models_or_calls_the_network():
    """AC-5.1: the page reads files only; no checkpoints (joblib/sklearn), no HTTP."""
    names = imported_modules((PACKAGE / "dashboard.py").read_text(encoding="utf-8"))
    roots = {n.split(".")[0] for n in names}
    assert (
        roots & {"joblib", "sklearn", "requests", "urllib", "http", "socket"} == set()
    )


@pytest.mark.parametrize(
    "source",
    [
        "import streamforecast.features",
        "from streamforecast import train",
        "from streamforecast.forecast import predict_bands",
        "from . import ingest",
        "from .dashboard import headline",
        "from streamforecast import config, scheduler",
    ],
)
def test_checker_flags_stage_imports(source):
    assert stage_imports(source)


@pytest.mark.parametrize(
    "source",
    [
        "from streamforecast import config, paths, logs",
        "import streamforecast.paths",
        "from . import logs",
        "import pandas as pd",
    ],
)
def test_checker_allows_shared_imports(source):
    assert stage_imports(source) == set()
