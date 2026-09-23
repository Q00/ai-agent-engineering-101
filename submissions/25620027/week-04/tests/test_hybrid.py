from collections.abc import Iterable, Iterator
from typing import final

from acl_lab.domain import ChatMessage, ModelSuccess, Outcome, Scenario
from acl_lab.hybrid_domain import HybridContext
from acl_lab.hybrid_engine import run_hybrid_episode
from acl_lab.hybrid_protocol import (
    Confirmation,
    ConfirmationFailure,
    SettlementDecision,
    parse_confirmation,
    settlement_guards,
)


@final
class CapturingScriptedModel:
    """Return fixed messages and retain system prompts for phase assertions."""

    def __init__(self, replies: Iterable[str]) -> None:
        self._replies: Iterator[str] = iter(replies)
        self.systems: list[str] = []

    def complete(self, system: str, history: tuple[ChatMessage, ...]) -> ModelSuccess:
        del history
        self.systems.append(system)
        return ModelSuccess(text=next(self._replies), raw="{}", tokens=1, attempts=1)


def test_confirmation_parser_requires_exact_candidate_price() -> None:
    valid = parse_confirmation(
        '{"performative":"confirm-deal","content":{"price":130}}',
        candidate_price=130,
    )
    wrong_price = parse_confirmation(
        '{"performative":"confirm-deal","content":{"price":125}}',
        candidate_price=130,
    )
    extra_field = parse_confirmation(
        '{"performative":"confirm-deal","content":{"price":130},"note":"yes"}',
        candidate_price=130,
    )

    assert valid == Confirmation(SettlementDecision.CONFIRM, 130)
    assert isinstance(wrong_price, ConfirmationFailure)
    assert wrong_price.reason == "settlement_price_mismatch"
    assert isinstance(extra_field, ConfirmationFailure)
    assert extra_field.reason == "settlement_invalid_json"


def test_settlement_guards_evaluate_buyer_and_seller_limits_separately() -> None:
    scenario = Scenario(id=3, item="bicycle", reserve=120, budget=100)

    guards = settlement_guards(scenario, candidate_price=100)

    assert guards.buyer_approved is True
    assert guards.seller_approved is False
    assert guards.approved is False


def test_hybrid_routes_free_to_tagged_then_commits_safe_structured_settlement() -> None:
    context = HybridContext(
        scenario=Scenario(id=1, item="monitor", reserve=120, budget=150),
        max_turns=8,
    )
    model = CapturingScriptedModel(
        (
            "I can offer 130 dollars.",
            '{"performative":"propose","price":130}',
            "(accept-proposal) I accept that offer.",
            '{"performative":"confirm-deal","content":{"price":130}}',
            '{"performative":"confirm-deal","content":{"price":130}}',
        )
    )

    execution = run_hybrid_episode(context, model)

    assert execution.result.outcome is Outcome.DEAL
    assert execution.result.price == 130
    assert execution.result.correct == 1
    assert execution.result.violation == 0
    assert execution.result.turns == 2
    assert execution.result.reader_calls == 1
    assert execution.result.settlement_calls == 2
    assert execution.result.settlement_errors == 0
    assert execution.result.settlement_vetoes == 0
    assert execution.result.guard_vetoes == 0
    assert "plain English" in model.systems[0]
    assert "Start with exactly one tag" in model.systems[2]
    assert all("confirm-deal" in system for system in model.systems[3:5])
    assert any("discovery -> bargaining" in line for line in execution.transcript)


def test_hybrid_guard_veto_prevents_unsafe_deal_and_returns_to_bargaining() -> None:
    context = HybridContext(
        scenario=Scenario(id=3, item="bicycle", reserve=120, budget=100),
        max_turns=8,
    )
    model = CapturingScriptedModel(
        (
            "I can offer 100 dollars.",
            '{"performative":"propose","price":100}',
            "(accept-proposal) I accept that offer.",
            '{"performative":"confirm-deal","content":{"price":100}}',
            '{"performative":"confirm-deal","content":{"price":100}}',
            "(refuse) I am leaving the negotiation.",
        )
    )

    execution = run_hybrid_episode(context, model)

    assert execution.result.outcome is Outcome.NO_DEAL
    assert execution.result.correct == 1
    assert execution.result.violation == 0
    assert execution.result.turns == 3
    assert execution.result.settlement_calls == 2
    assert execution.result.settlement_vetoes == 1
    assert execution.result.guard_vetoes == 1
    assert any("seller_guard=reject" in line for line in execution.transcript)
