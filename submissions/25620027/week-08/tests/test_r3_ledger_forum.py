"""The public-ledger mirror answers demand and quality questions; posts cite refs."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from r3.forum import buyer_post, maybe_post
from r3.ledger_db import LedgerDB
from r3.market_files import read_catalog, read_inventory
from tests.r3_stock import KIMCHI, LAMP, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path


def settle(  # noqa: PLR0913, PLR0917 - ledger row fixture
    seq: int,
    ts: float,
    buyer: str,
    shop: str,
    sku: str,
    amount: int,
    grade: str = "A",
    claim: str = "A",
) -> dict[str, object]:
    return {
        "seq": seq,
        "ts": ts,
        "kind": "settle",
        "round": 3,
        "data": {
            "order": f"o-{seq}",
            "buyer": buyer,
            "shop": shop,
            "item": sku,
            "amount": amount,
            "grade": grade,
            "claim": claim,
            "claim_honest": grade == claim,
        },
    }


def test_ledger_mirror_counts_demand_breaks_and_our_settlements(tmp_path: Path) -> None:
    folder = write_market(tmp_path / ".market", [])
    catalog = read_catalog(folder)
    db = LedgerDB(tmp_path / "ledger.sqlite")
    old = time.time() - 7 * 3600
    rows = [
        {"seq": 1, "ts": old, "kind": "order", "round": 3, "data": {"buyer": "b1"}},
        settle(2, old, "b1", "http://us.test", KIMCHI, 36000),
        settle(3, old, "b2", "http://other.test", LAMP, 30000, grade="C", claim="A"),
        settle(4, old + 10, "25620027", "http://other.test", LAMP, 31000),
        {
            "seq": 5,
            "ts": old + 3600,
            "kind": "broken",
            "round": 3,
            "data": {"buyer": "b2", "item": LAMP},
        },
    ]
    assert db.ingest(rows) == 5
    assert db.ingest(rows) == 0
    assert db.cursor == 5
    summary = db.summary(
        catalog, hours=24, our_shop="http://us.test/", our_buyer="25620027"
    )
    assert summary["settles"] == 3
    assert summary["our_shop_settles"] == 1
    assert summary["our_buyer_settles"] == 1
    assert summary["dishonest_claims"] == 1
    top = summary["top_categories"]
    assert isinstance(top, list)
    assert top[0]["category"] == "가구/침구>조명>스탠드"
    assert summary["break_rates"] == {
        "C": {"broken": 1, "observed": 1},
        "A": {"broken": 0, "observed": 2},
    }
    by_category, by_sku = db.demand_counts(catalog, 24)
    assert by_sku == {KIMCHI: 1, LAMP: 2}
    assert by_category["가구/침구>조명>스탠드"] == 2


def test_forum_posts_cite_ledger_refs_and_respect_the_per_role_interval(
    tmp_path: Path,
) -> None:
    now = time.time()
    folder = write_market(
        tmp_path / ".market", [unit(KIMCHI, 1, now=now), unit(LAMP, 1, now=now)]
    )
    deals = tmp_path / "r3-deals.jsonl"
    _ = deals.write_text(
        json.dumps(
            {
                "ts": now,
                "shop": "http://kimchi.test",
                "sku": KIMCHI,
                "name": "김치",
                "grade": "A",
                "claim_honest": True,
                "price": 36000,
                "value": 52000,
                "ledger": 77,
                "kind": "food",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    text, refs = buyer_post(
        [json.loads(deals.read_text().splitlines()[0])],
        {"http://kimchi.test": "Kimchi Shop"},
        "Market line.",
    )
    assert refs == [77]
    assert "69% of list" in text
    assert "Kimchi Shop" in text
    assert "Market line." in text
    posted = maybe_post(
        folder,
        deals,
        tmp_path / "forum-state.json",
        inventory=read_inventory(folder),
        shop_name="Sungjin Shop",
        warranty_a=6,
        shop_names={},
        market_line="",
        now=now,
    )
    assert posted == ["buyer", "shop"]
    lines = [
        json.loads(row) for row in (folder / "outbox.jsonl").read_text().splitlines()
    ]
    assert [line["as"] for line in lines] == ["buyer", "shop"]
    assert lines[0]["refs"] == [77]
    assert "6h warranty" in lines[1]["post"]
    # When: called again a minute later, nothing new is queued.
    again = maybe_post(
        folder,
        deals,
        tmp_path / "forum-state.json",
        inventory=read_inventory(folder),
        shop_name="Sungjin Shop",
        warranty_a=6,
        shop_names={},
        market_line="",
        now=now + 60,
    )
    assert again == []
