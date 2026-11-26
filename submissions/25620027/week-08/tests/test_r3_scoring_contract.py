"""Keep both model roles aligned with the official normalized buyer objective."""

from __future__ import annotations

import pytest

from r3.manager import BUYER_SCORING_RULES, PROMPT, TACTICAL_PROMPT


@pytest.mark.parametrize("prompt", [PROMPT, TACTICAL_PROMPT])
def test_model_roles_use_the_current_saved_rate_contract(prompt: str) -> None:
    """Neither role should optimize raw currency surplus as the graded metric."""
    assert BUYER_SCORING_RULES in prompt
    assert "saved rate at weight 0.5" in prompt
    assert "sum(normalized deal credit) / (deals + 100)" in prompt
    assert "settled deal that served nothing counts -1, or 0 if refunded" in prompt
    assert "standing.buyer_reconstruction.status" in prompt
    assert "50 settled" in prompt
    assert "buyers by surplus (list price" not in prompt
    assert "buyer deals and surplus on" not in prompt
    assert "mature surplus per issued order" not in prompt


def test_strategy_retains_the_latest_owner_warranty_and_model_locks() -> None:
    """Scoring guidance must preserve the actual owner-approved trade limits."""
    assert "A·B·C 모두 72시간" in PROMPT
    assert "A·B·C 모두 24시간" not in PROMPT
    assert "locked seller.* and restock.*" in PROMPT
    assert "0.55..0.62" in PROMPT
