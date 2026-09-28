from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CONFTEST = Path(__file__).parents[1] / "conftest.py"


@pytest.fixture
def project(pytester):
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    pytester.makeini("[pytest]\nmarkers =\n    unit\n    regression\n    integration\n")
    return pytester


def test_marked_tests_collect(project):
    project.makepyfile("""
        import pytest

        @pytest.mark.unit
        def test_a():
            pass

        @pytest.mark.integration
        def test_b():
            pass
        """)
    result = project.runpytest("--strict-markers")
    result.assert_outcomes(passed=2)


def test_unmarked_test_is_a_collection_error(project):
    project.makepyfile("""
        import pytest

        @pytest.mark.unit
        def test_ok():
            pass

        def test_unmarked():
            pass
        """)
    result = project.runpytest("--strict-markers")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*exactly one of the markers*", "*test_unmarked*"])


def test_two_markers_is_a_collection_error(project):
    project.makepyfile("""
        import pytest

        @pytest.mark.unit
        @pytest.mark.regression
        def test_both():
            pass
        """)
    result = project.runpytest("--strict-markers")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*test_both*"])
