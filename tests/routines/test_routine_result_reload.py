"""A routine loaded before a hot reload still returns the old result class."""

from dataclasses import dataclass

from routines.base import RoutineResult, normalize_result


def test_a_result_from_a_previous_load_keeps_its_fields_and_silence():
    @dataclass
    class RoutineResult:  # noqa: F811 - stands in for the pre-reload class
        text: str
        silent: bool = False

    stale = RoutineResult(text="Trend scan · no signal", silent=True)
    result = normalize_result(stale)
    assert type(result) is not type(stale)
    assert result.text == "Trend scan · no signal"
    assert result.silent is True


def test_plain_values_still_wrap_as_text():
    assert normalize_result("done").text == "done"
    assert normalize_result(None).text == "Completed"
    current = RoutineResult(text="x")
    assert normalize_result(current) is current
