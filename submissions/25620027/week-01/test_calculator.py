import pytest

from first_agent import calculator


def test_calculator_keeps_assignment_arithmetic_working() -> None:
    assert calculator("48000 + 9500 - 12000") == "45500"


def test_calculator_rejects_resource_intensive_exponent() -> None:
    with pytest.raises(ValueError):
        calculator("2 ** 10000")


def test_calculator_rejects_overlong_expression() -> None:
    expression = "1+" * 50 + "1"

    with pytest.raises(ValueError):
        calculator(expression)
