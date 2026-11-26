"""Exercise sidecar consistency and scoped activation through real buyer IO."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from r3 import buyer
from r3.events import EventLedger
from r3.market_files import PaymentLine
from r3.policy import BuyerPolicy, Policy, load_history, save_policy
from r3.sku_selection import ENABLED_FILE, SkuEvidence
from tests.test_r3_big_orders import Peers, prepare, run_buy
from tests.test_r3_saved_feedback import NOW

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("problem", "expected"),
    [
        ("none", "http://good.test"),
        ("generated_at", "http://bad.test"),
        ("ledger_latest_at", "http://bad.test"),
        ("owner_control_until", "http://bad.test"),
        ("marker", "http://bad.test"),
    ],
)
def test_buy_consumes_only_coherent_enabled_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str, expected: str
) -> None:
    folder = prepare(tmp_path, ("bad.test", "good.test"))
    policy = tmp_path / "policy.json"
    _ = save_policy(
        policy,
        Policy(buyer=BuyerPolicy(surplus_score=True, explore_rate=0.0)),
        "SKU activation fixture",
        expected_version=load_history(policy).current.version,
    )
    events = EventLedger(tmp_path / "events.sqlite")
    for number in range(10):
        order = f"prior-{number}"
        events.record_attempt(
            order=order,
            shop="http://bad.test",
            shop_name="Peer",
            sku="G00000004",
            list_price=236900,
            price=150000,
            outcome="agreed",
            market_round=3,
        )
        _ = events.sync_payments(
            [PaymentLine(order=order, shop="http://bad.test", price=150000, paid=True)]
        )
    primary = buyer.SavedFeedback(
        generated_at=NOW,
        ledger_latest_at=NOW,
        owner_control_until=NOW + 3600,
        shops={},
    )
    sidecar = primary.model_copy(
        update={
            "skus": {
                "http://good.test": {
                    "G00000004": SkuEvidence(
                        known_deals=100, paid_share_sum=40.0, loss_sum=0.0
                    ),
                }
            }
        }
    )
    if problem in {"generated_at", "ledger_latest_at", "owner_control_until"}:
        sidecar = sidecar.model_copy(update={problem: getattr(sidecar, problem) - 1})
    _ = (tmp_path / buyer.SAVED_FEEDBACK_FILE).write_text(
        primary.model_dump_json(exclude={"skus"})
    )
    _ = (tmp_path / buyer.SKU_FEEDBACK_FILE).write_text(sidecar.model_dump_json())
    if problem != "marker":
        _ = (tmp_path / ENABLED_FILE).write_text("owner-test")
    monkeypatch.setattr(buyer, "WALL_CLOCK", lambda: NOW)
    # The ten prior closes at bad.test exist for its payment evidence; keep the
    # close-rate weight (tests/test_r3_close_rate.py) out of this sidecar check.
    monkeypatch.setattr(buyer, "CLOSE_HOURS", 0.0)
    peers = Peers({})
    result = run_buy(folder, peers, monkeypatch)
    assert result.shop == expected
    assert result.receipt is not None
    assert peers.visits == [expected.removeprefix("http://")]
