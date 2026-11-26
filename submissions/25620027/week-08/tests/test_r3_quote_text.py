"""First-quote text: one stated grade or warranty length is used, else unchanged."""

from __future__ import annotations

import pytest

from r3.buyer import text_grade, text_warranty


@pytest.mark.parametrize(
    ("text", "grade", "hours"),
    [
        (
            (
                "패드세트 K (grade B, three-hour full-refund warranty, price capped "
                "by grade): asking 91458. Send your offer as a number."
            ),
            "B",
            3.0,
        ),
        (
            (
                "양모이불 Q: asking 108881. SKU G0132ff94, grade A; market "
                "certificate at checkout."
            ),
            "A",
            None,
        ),
        ("Unit X, grade A, with a 6-hour warranty, asking 20810", "A", 6.0),
        ("grade A now; 72 h warranty included", "A", 72.0),
        ("grade B, no warranty on this unit", "B", 0.0),
    ],
)
def test_one_stated_value_is_read(text: str, grade: str, hours: float | None) -> None:
    assert text_grade(text) == grade
    assert text_warranty(text) == hours


def test_option_lists_and_plain_asks_change_nothing() -> None:
    options = (
        "We have 3 침구 option(s): 1) A (G1): grade A (new), with a 6-hour "
        "warranty, asking 20810; 2) B (G2): grade B, with a 72-hour warranty"
    )
    assert text_grade(options) is None
    assert text_warranty(options) is None
    assert text_grade("asking 12000. Send your offer as a number.") is None
    assert text_warranty("asking 12000. Send your offer as a number.") is None


def test_food_shelf_life_is_not_a_warranty() -> None:
    text = "김치 5kg, grade B, 660.9 hours of shelf life left, no warranty; asking 9000"
    assert text_warranty(text) == 0.0
    assert text_warranty("grade A, 603.4 hours left, warranty on request") is None
