import json

from first_agent import text_stats


def test_text_stats_counts_multiline_text() -> None:
    # Given
    text = "alpha beta\nthree"

    # When
    result = json.loads(text_stats(text))

    # Then
    assert result == {"words": 3, "lines": 2, "characters": 16}
