"""Member codes and the harsh-review guard (no network)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shop_r3
from r3lib import config
from test_r3_core import mem


def test_member_code_is_stable_and_not_a_price(monkeypatch):
    monkeypatch.setattr(shop_r3, "_SECRET", b"k")
    a, b = shop_r3.member_code("task-1"), shop_r3.member_code("task-2")
    assert a == shop_r3.member_code("task-1") and a != b and a.startswith(config.LOYALTY_PREFIX) and len(a) == 12
    assert shop_r3.parse_offer(f"I want 의자 (G1abcdef0). {a} 52000") == 52000
    assert shop_r3.parse_offer(f"{a.upper()} hello") is None                    # digits inside the code are ignored


def test_codes_issue_and_redeem(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    m.issue_code("SSD-0123abcd", "G1", "s1")
    assert m.redeem_code("SSD-0123abcd") and m.redeem_code("SSD-0123abcd")    # a membership, reusable
    assert not m.redeem_code("SSD-ffffffff")


def _result(seq, task, stars, shop="https://me"):
    return {"seq": seq, "ts": time.time(), "kind": "result", "round": 3,
            "data": {"shop": shop, "buyer": "b", "receipt": {"task": task}, "stars": stars}}


def test_harsh_styles_from_results(tmp_path, monkeypatch):
    import restock_r3
    m = mem(tmp_path, monkeypatch)
    with m.db() as c:
        c.execute("INSERT INTO shops VALUES ('https://me', 'myeonggi', 'pk', 'o', 1, 0)")
    rows = []
    for i in range(20):
        m.record_negotiation("shop", f"bad{i}", None, "G1", 1, 1, [], "deal", style="harsh")
        m.record_negotiation("shop", f"ok{i}", None, "G1", 1, 1, [], "deal", style="fair")
        rows += [_result(1000 + i, f"bad{i}", 3), _result(2000 + i, f"ok{i}", 5)]
    rows.append(_result(3000, "bad0", 1, shop="https://someone-else"))           # other shops do not count
    m._ingest(rows)
    stats = m.style_stars(min_n=15)
    assert stats == {"harsh": (20, 3.0), "fair": (20, 5.0)}
    assert restock_r3.refresh_harsh_styles(m) == {}                             # off by default since 2026-11-08
    m.put("harsh_at", 0)
    monkeypatch.setattr(config, "HARSH_MAX_STARS", 3.3)                        # the mechanism, when switched on
    assert restock_r3.refresh_harsh_styles(m) == {"harsh": (20, 3.0)}
    assert restock_r3.refresh_harsh_styles(m) is None                           # rate-limited
    assert json.loads(json.dumps(m.get("harsh_styles"))) == {"harsh": [20, 3.0]}


def test_product_name_numbers_are_not_offers():
    name = "계산기건전지 지샥 건전지 카메라 리튬 코인 배터리 5개입 CR1220"
    assert shop_r3.parse_offer(f"I want to buy {name} (Gea072173)", name) is None
    assert shop_r3.parse_offer(f"I want to buy {name} (Gea072173). 900", name) == 900
    assert shop_r3.parse_offer("지누스 매트리스 20cm Q 퀸 (G1515d951)", "지누스 매트리스 20cm Q 퀸") is None


def test_member_codes_are_off_and_not_advertised(tmp_path, monkeypatch):
    """2026-11-04: 332 codes issued, 0 quoted; switched off. The card no longer promises them."""
    from starlette.testclient import TestClient
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "m.sqlite")
    assert config.LOYALTY_ENABLED is False
    card = TestClient(shop_r3.build(1)).get("/.well-known/agent-card.json").json()
    assert "member code" not in card["description"]
    monkeypatch.setattr(config, "LOYALTY_ENABLED", True)
    card = TestClient(shop_r3.build(1)).get("/.well-known/agent-card.json").json()
    assert "member code" in card["description"]
