import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
PIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[0-9][A-Za-z0-9.+!-]*$")


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
def test_every_line_is_an_exact_pin(name):
    lines = [
        line.strip()
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lines, f"{name} is empty"
    assert [line for line in lines if not PIN.match(line)] == []
