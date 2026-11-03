"""Hook commands print one JSON line; the manager summarises without a model."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import anyio
from typer.testing import CliRunner

import harness_app
from r3 import buyer as fast
from r3.manager import (
    EDGE_RUNS_TO_WIDEN,
    PROMPT,
    Manager,
    lenient_strategy_arguments,
    policy_schema,
)
from r3.market_files import ShopEntry
from r3.policy import (
    Band,
    Policy,
    load_history,
    load_policy,
    save_policy,
    save_tactical,
    widen_band,
)
from tests.r3_stock import KIMCHI, LAMP, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_market_buyer_cli_fast_mode_resolves_the_order_path_and_prints_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: relative paths in the caller's directory and a recording fast buyer.
    monkeypatch.chdir(tmp_path)
    folder = write_market(tmp_path / ".market", [])
    _ = (folder / "shops.json").write_text("[]", encoding="utf-8")
    _ = (folder / "order.json").write_text("{}", encoding="utf-8")
    seen: list[tuple[Path, Path | None]] = []

    async def buy(
        shops_path: Path, item: str, budget: int, order_path: Path | None, _emit: object
    ) -> fast.Outcome:
        seen.append((shops_path, order_path))
        shop = ShopEntry(name="s", url="http://s.test")
        return fast.Outcome(
            shop.url,
            {"item": item, "price": budget},
            4,
            "ok",
            {"cert": True},
            budget,
            "",
        )

    monkeypatch.setattr(harness_app.r3_buyer, "buy", buy)
    # When: the kit's command line runs with the v4 order argument.
    result = CliRunner().invoke(
        harness_app.app,
        [
            "market-buyer",
            "--shops",
            ".market/shops.json",
            "--item",
            "김치",
            "--budget",
            "100",
            "--order",
            ".market/order.json",
        ],
    )
    # Then: paths were resolved before isolation and the last line is the kit's JSON.
    assert result.exit_code == 0, result.output
    assert seen == [(folder / "shops.json", folder / "order.json")]
    line = json.loads(result.stdout.splitlines()[-1])
    assert line["shop"] == "http://s.test"
    assert line["accept"] == {"cert": True}
    assert line["receipt"]["price"] == 100


def test_restock_and_review_commands_print_kit_shaped_json(tmp_path: Path) -> None:
    now = time.time()
    folder = write_market(tmp_path / ".market", [unit(KIMCHI, 1, now=now)])
    plan = CliRunner().invoke(harness_app.app, ["restock", "--state", str(folder)])
    assert plan.exit_code == 0, plan.output
    lines = json.loads(plan.stdout.splitlines()[-1])["buy"]
    assert lines
    assert all({"sku", "qty"} <= set(line) for line in lines)
    deal = folder / "deal.json"
    _ = deal.write_text(
        json.dumps(
            {
                "order": {
                    "order": "o",
                    "item": "lamp",
                    "budget": 50000,
                    "acceptable": [LAMP],
                    "kind": "goods",
                },
                "receipt": {"shop": "x", "item": LAMP, "price": 30000, "task": "t"},
                "settled": {
                    "shop": "http://x.test",
                    "unit": "u",
                    "sku": LAMP,
                    "name": "lamp",
                    "grade": "A",
                    "claim": "A",
                    "claim_honest": True,
                    "warranty_hours": 6,
                    "value": 40000,
                    "ledger": 9,
                },
            }
        ),
        encoding="utf-8",
    )
    review = CliRunner().invoke(harness_app.app, ["review", "--deal", str(deal)])
    assert review.exit_code == 0, review.output
    verdict = json.loads(review.stdout.splitlines()[-1])
    assert verdict["stars"] == 5
    assert "Warranty 6 h" in verdict["review"]
    assert "50000" not in verdict["review"]


def test_manager_summary_and_schema_work_without_network_or_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    run_dir = tmp_path / "run"
    folder = write_market(
        run_dir / ".market", [unit(KIMCHI, 1, now=now), unit(LAMP, 1, now=now)]
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    manager = Manager(
        run_dir,
        "http://127.0.0.1:9",
        shop_url="http://us.test",
        buyer_name="25620027",
        shop_name="Sungjin Shop",
    )
    summary = manager.summary()
    assert summary["shop"]["units"] == 2
    assert summary["shop"]["categories"] == 2
    assert summary["market_6h"]["settles"] == 0
    assert summary["policy"]["version"] == 1
    # And: the tool schema exposes only numeric/boolean policy fields by section,
    # each numeric field carrying the bounds the policy table enforces.
    schema = policy_schema()
    fields = schema["properties"]["changes"]["properties"]
    assert fields["seller.markup_a"] == {
        "type": "number",
        "minimum": 0.3,
        "maximum": 1.0,
    }
    assert fields["restock.units_per_sku"] == {
        "type": "integer",
        "minimum": 1.0,
        "maximum": 20.0,
    }
    assert fields["buyer.require_cert"] == {"type": "boolean"}
    assert "buyer.goods_grades" not in fields
    # And: a model-less tick leaves the policy untouched and posts the stock summary.
    assert anyio.run(manager.ask_model, summary) is None
    assert load_policy(manager.policy_file) == Policy()
    assert manager.post_forum() == ["shop"]
    outbox = (folder / "outbox.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(outbox[0])["as"] == "shop"
    # And: a saved policy version is what the next decision reads.
    _ = save_policy(manager.policy_file, Policy(), "test")
    assert manager.summary()["policy"]["version"] == 2


def test_pinned_tactical_fields_are_reported_and_their_band_widened_by_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    run_dir = tmp_path / "run"
    _ = write_market(run_dir / ".market", [unit(KIMCHI, 1, now=now)])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    manager = Manager(
        run_dir,
        "http://127.0.0.1:9",
        shop_url="http://us.test",
        buyer_name="25620027",
        shop_name="S",
    )
    # Given: a strategy band on accept_margin and a tactical move pinned at its top.
    _ = save_policy(
        manager.policy_file,
        Policy(),
        "s",
        bands={"seller.accept_margin": Band(low=0.1, high=0.165)},
    )
    _ = save_tactical(manager.policy_file, {"seller.accept_margin": 0.165}, "up")
    assert manager.band_edges()["seller.accept_margin"]["side"] == "high"
    assert "tactical_at_band_edge" in manager.summary()
    assert "tactical_at_band_edge" in PROMPT
    # When: the field stays pinned for three tactical runs.
    for _ in range(EDGE_RUNS_TO_WIDEN - 1):
        edges = manager.track_band_edges()
        assert edges["seller.accept_margin"]["band"]["high"] == 0.165
    edges = manager.track_band_edges()
    # Then: code widens the band by half its span on that side, keeps the overlay,
    # records the change as its own, and restarts the count.
    assert edges["seller.accept_margin"]["band"] == {"low": 0.1, "high": 0.1975}
    history = load_history(manager.policy_file)
    assert history.tactical is not None
    assert history.bands["seller.accept_margin"].high == 0.1975
    rows = [c for c in manager.events.recent_policy_changes(1) if c["actor"] == "code"]
    assert rows
    assert rows[-1]["field"] == "band:seller.accept_margin"
    assert manager.events.loop_runs(0)[-1]["outcome"] == "nudged"
    assert "seller.accept_margin" not in manager.band_edges()  # room again
    # And: widening never leaves the field's own bounds.
    assert widen_band("seller.accept_margin", Band(low=0.9, high=1.0), "high") == Band(
        low=0.9, high=1.0
    )
    # And: one bad band or a text change no longer sinks a strategy proposal.
    lenient = lenient_strategy_arguments(
        {
            "changes": {"seller.markup_a": 0.9, "seller.markup_b": "high"},
            "bands": {"seller.concession": {"low": 0.2, "high": 0.4}, "x": "bad"},
            "reason": "r",
        }
    )
    assert lenient["changes"] == {"seller.markup_a": 0.9}
    assert list(lenient["bands"]) == ["seller.concession"]
