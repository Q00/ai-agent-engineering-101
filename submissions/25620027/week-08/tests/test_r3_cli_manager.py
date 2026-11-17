"""Hook commands print one JSON line; the manager summarises without a model."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING

import anyio
from typer.testing import CliRunner

import harness_app
from r3 import buyer as fast
from r3.manager import (
    EDGE_RUNS_TO_WIDEN,
    FOLD_RUNS,
    PROMPT,
    Manager,
    Proposal,
    append_notes,
    dropped_entries,
    lenient_strategy_arguments,
    load_notes,
    policy_schema,
    resolve_notes,
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
    # And: a value past its bound, an unknown field and a seventh change drop
    # alone (seven Opus runs failed whole on 11/4), and the run log names them.
    raw = {
        "changes": {
            "restock.stock_share": 3.0,
            "restock.no_such": 1,
            **dict.fromkeys(
                (
                    "seller.markup_a",
                    "seller.markup_b",
                    "seller.markup_c",
                    "buyer.max_price_ratio",
                    "restock.food_share",
                    "seller.stale_markup_cut",
                    "seller.accept_margin",
                ),
                0.4,
            ),
        },
        "bands": {"seller.concession": {"low": 0.2, "high": 4.0}},
    }
    kept = lenient_strategy_arguments(raw)
    assert list(kept["changes"]) == [
        "seller.markup_a",
        "seller.markup_b",
        "seller.markup_c",
        "buyer.max_price_ratio",
        "restock.food_share",
        "seller.stale_markup_cut",
    ]
    assert kept["bands"] == {}
    assert Proposal.model_validate(kept).changes == kept["changes"]
    assert dropped_entries(raw, kept) == [
        "changes:restock.stock_share",
        "changes:restock.no_such",
        "changes:seller.accept_margin",
        "bands:seller.concession",
    ]


def test_food_near_clearance_is_a_fixed_line_and_its_band_is_never_widened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    run_dir = tmp_path / "run"
    # Given: kimchi with 35%, 29% and 10% of its shelf life left, clearance at 0.28.
    _ = write_market(
        run_dir / ".market",
        [
            unit(KIMCHI, 1, now=now, shelf_left_ratio=0.35),
            unit(KIMCHI, 2, now=now, shelf_left_ratio=0.29),
            unit(KIMCHI, 3, now=now, shelf_left_ratio=0.10),
        ],
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    manager = Manager(
        run_dir,
        "http://127.0.0.1:9",
        shop_url="http://us.test",
        buyer_name="25620027",
        shop_name="S",
    )
    key = "seller.clearance_shelf_ratio"
    _ = save_policy(
        manager.policy_file, Policy(), "s", bands={key: Band(low=0.2, high=0.28)}
    )
    _ = save_tactical(manager.policy_file, {key: 0.28}, "up")
    # When / Then: only the unit under 30% and not yet in clearance counts
    # (not "under 1.5 x 0.28", and not the 10% unit already sold as clearance).
    aging = manager.summary_aging()
    assert aging["food_near_clearance"] == 1
    assert aging["clearance_ratio_pct"] == 28
    # And: pinned at its band top for many runs, code leaves the band alone.
    for _ in range(EDGE_RUNS_TO_WIDEN * 2):
        _ = manager.track_band_edges()
    assert load_history(manager.policy_file).bands[key] == Band(low=0.2, high=0.28)


def test_floor_bounds_fold_and_note_resolution(
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
    # Given: Round 2 agreements an hour ago and Round 3 rows from ten minutes ago.
    for n in range(4):
        manager.events.record_attempt(
            order=f"r2-{n}",
            shop="http://a",
            shop_name="a",
            sku=KIMCHI,
            list_price=100,
            price=80,
            outcome="agreed",
        )
    manager.ledger.ingest(
        [{"seq": 1, "ts": now - 600, "kind": "settle", "round": 3, "data": {}}]
    )
    with sqlite3.connect(manager.events.path) as db:
        db.execute("UPDATE buyer_attempts SET ts = ?", (now - 3600,))
    # Then: the 24 h window starts at Round 3, so the old agreements drop out.
    assert manager.events.summary(24)["buyer_agreed"] == 4
    assert manager.r3_opened_at() == now - 600
    assert manager.summary()["own_results_24h"]["buyer_agreed"] == 0
    # And: a band limit that is the field's own bound is no edge.
    _ = save_policy(
        manager.policy_file,
        Policy(),
        "s",
        bands={"restock.replenish_qty": Band(low=1, high=3)},
    )
    assert "restock.replenish_qty" not in manager.band_edges()
    # And: the same tactical move accepted three times becomes the base.
    for _ in range(FOLD_RUNS):
        manager.events.record_policy_change(
            actor="m",
            layer="tactical",
            field="buyer.quick_accept_ratio",
            before=0.72,
            after=0.75,
            reason="walkaway",
            expires_at=None,
            version=None,
            accepted=True,
        )
    assert manager.fold_recurring_moves() == {"buyer.quick_accept_ratio": 0.75}
    assert load_history(manager.policy_file).base().buyer.quick_accept_ratio == 0.75
    assert manager.fold_recurring_moves() == {}  # already the base
    # And: after a fold only new moves count, so one more step does not refold
    # (first night: the fold fired every 15 min and walked the base up).
    manager.events.record_policy_change(
        actor="m",
        layer="tactical",
        field="buyer.quick_accept_ratio",
        before=0.75,
        after=0.79,
        reason="again",
        expires_at=None,
        version=None,
        accepted=True,
    )
    assert manager.fold_recurring_moves() == {}
    # And: shops without stock or timeouts are not buyers walking away.
    for reason in ("TASK_STATE_REJECTED: none of that in stock", "timed out", ""):
        manager.events.record_attempt(
            order=f"nd-{reason[:4]}",
            shop="http://b",
            shop_name="b",
            sku=KIMCHI,
            list_price=100,
            price=None,
            outcome="no_deal",
            reason=reason,
        )
    signals = manager.events.tactical_signals(1)
    assert signals["buyer_attempts"] == 3
    assert signals["buyer_priced"] == 1
    assert manager.events.loop_runs(0)[-1]["outcome"] == "nudged"
    # And: a resolved note leaves the open list.
    path = tmp_path / "notes.jsonl"
    _ = append_notes(path, ["fix a", "fix b"], actor="m", version=1)
    assert resolve_notes(path, ["fix a"], by="claude") == 1
    assert [r["note"] for r in load_notes(path)] == ["fix b"]


def test_unpaid_agreed_deals_are_counted_by_reason() -> None:
    from r3.events import refusal_kind  # noqa: PLC0415

    assert refusal_kind("not paying: warranty 0 h, the policy asks for 6") == "warranty"
    assert refusal_kind("not paying: grade C, the policy accepts ['A', 'B']") == "grade"
    assert (
        refusal_kind("not paying: no certificate, and the policy asks for one")
        == "certificate"
    )
    assert (
        refusal_kind("not paying: 256.01 h of shelf life left, the policy asks for 361")
        == "shelf_life"
    )
    assert (
        refusal_kind('checkout refused: 409 {"error":"unit u-1 is not in stock"}')
        == "out_of_stock"
    )
    assert refusal_kind("something new") == "other"
