from acl_lab.domain import Condition, Outcome, Performative, Scenario
from acl_lab.protocol import (
    ParsedMessage,
    ParseFailure,
    parse_reader,
    parse_structured,
    parse_tagged,
    score_outcome,
)


def test_parse_structured_returns_typed_proposal_when_json_is_valid() -> None:
    # Given
    text = '{"performative":"propose","content":{"price":130}}'

    # When
    result = parse_structured(text)

    # Then
    assert result == ParsedMessage(performative=Performative.PROPOSE, price=130)


def test_parse_structured_rejects_sentence_after_json() -> None:
    # Given
    text = '{"performative":"propose","content":{"price":130}} deal?'

    # When
    result = parse_structured(text)

    # Then
    assert result == ParseFailure(reason="structured_invalid_json")


def test_parse_tagged_preserves_tag_and_marks_proposal_for_reader() -> None:
    # Given
    text = "(propose) I can offer 130 dollars."

    # When
    result = parse_tagged(text)

    # Then
    assert result == ParsedMessage(
        performative=Performative.PROPOSE,
        price=None,
        reader_needed=True,
    )


def test_parse_reader_rejects_proposal_without_price() -> None:
    # Given
    text = '{"performative":"propose","price":null}'

    # When
    result = parse_reader(text)

    # Then
    assert result == ParseFailure(reason="reader_proposal_without_price")


def test_score_outcome_marks_out_of_budget_deal_as_violation() -> None:
    # Given
    scenario = Scenario(id=1, item="monitor", reserve=120, budget=150)

    # When
    result = score_outcome(scenario, Outcome.DEAL, 170)

    # Then
    assert result.correct == 0
    assert result.violation == 1


def test_score_outcome_marks_no_deal_correct_when_limits_do_not_overlap() -> None:
    # Given
    scenario = Scenario(id=3, item="bicycle", reserve=120, budget=100)

    # When
    result = score_outcome(scenario, Outcome.NO_DEAL, None)

    # Then
    assert result.correct == 1
    assert result.violation == 0


def test_condition_vocabulary_matches_assignment_contract() -> None:
    assert tuple(condition.value for condition in Condition) == (
        "free",
        "tagged",
        "structured",
    )
