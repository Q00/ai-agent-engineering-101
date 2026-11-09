"""2026-11-09: buyer-01 and buyer-02 never state how long food must last, got our soonest-expiring unit, and rated
38 deals 2.0 ("Only 34.3 h of shelf life left; I needed 84.0 h"; 6507 other food reviews averaged 4.7). Their hidden
need is always 0.25, 0.5 or 0.75 of the SKU's shelf life (6852 of our buyer's orders)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shop_r3
from r3lib import market

CAT = {"Gaaaaaaaa": {"name": "김치", "category": "식품>김치", "kind": "food", "list_price": 30000, "shelf_hours": 168}}


def units(*hours_left):
    now = time.time()
    return [{"unit": f"u{i}", "sku": "Gaaaaaaaa", "name": "김치", "category": "식품>김치", "kind": "food",
             "grade": "B", "cost": 15000, "list_price": 30000, "expires_at": now + h * 3600}
            for i, h in enumerate(hours_left)]


def pick(monkeypatch, text, *hours_left):
    inv = {"round": 3, "units": units(*hours_left)}
    monkeypatch.setattr(market, "read_json", lambda name, default=None, md=None: inv)
    monkeypatch.setattr(market, "_CATALOG", CAT)
    u, _ = shop_r3.find_unit(text, set())
    return round((u["expires_at"] - time.time()) / 3600)


def test_unstated_need_gets_the_soonest_unit_lasting_three_quarters_of_shelf(monkeypatch):
    assert pick(monkeypatch, "I want to buy 김치 (Gaaaaaaaa)", 30, 90, 130, 160) == 130


def test_then_half_then_a_quarter_then_first_to_expire(monkeypatch):
    assert pick(monkeypatch, "I want to buy 김치 (Gaaaaaaaa)", 30, 90, 100) == 90
    assert pick(monkeypatch, "I want to buy 김치 (Gaaaaaaaa)", 30, 50) == 50
    assert pick(monkeypatch, "I want to buy 김치 (Gaaaaaaaa)", 30, 20) == 20


def test_a_stated_need_still_wins(monkeypatch):
    assert pick(monkeypatch, "I want to buy 김치 (Gaaaaaaaa). I need it to last 24 hours", 30, 90, 160) == 30
