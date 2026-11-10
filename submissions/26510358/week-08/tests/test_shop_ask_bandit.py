"""RL_PROACTIVE.md 2.1: the shop's first ask is scaled by one of ASK_ARMS per (kind, grade, slow); the floor is
untouched; a finished negotiation pays the arm with the sale's board score, 0 without a deal."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shop_r3
from r3lib import config, score_model
from r3lib.memory import Memory


class Fixed:
    """A bandit that always plays one arm and remembers what it was paid."""
    def __init__(self, arm):
        self.arm, self.paid = arm, []

    def choose(self, context, **note):
        self.context = context
        return self.arm, 1.0

    def update(self, context, arm, reward, **note):
        self.paid.append((context, arm, reward, note.get("outcome")))


def shop(tmp_path, monkeypatch, arm):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(shop_r3.scout, "ask_premium", lambda mem, cat: 0.0)
    monkeypatch.setattr(score_model, "cached", lambda: None)
    s = shop_r3.Shop(Memory(tmp_path / "m.sqlite"))
    b = Fixed(arm)
    s.ask_bandits = {False: b, True: b}
    return s, b


UNIT = {"unit": "u1", "sku": "G1", "name": "의자", "category": "가구>의자", "kind": "goods", "grade": "B",
        "cost": 100_000, "list_price": 300_000, "supplied_at": time.time() - 3600}


def test_the_arm_scales_the_first_ask_and_leaves_the_floor(tmp_path, monkeypatch):
    base, b0 = shop(tmp_path, monkeypatch, 1.0)
    ask, floor, _ = base._terms(dict(UNIT))
    low, _ = shop(tmp_path, monkeypatch, 0.9)
    high, bh = shop(tmp_path, monkeypatch, 1.1)
    assert low._terms(dict(UNIT))[:2] == (int(ask * 0.9), floor)
    assert high._terms(dict(UNIT))[:2] == (min(300_000, int(ask * 1.1)), floor)
    assert bh.context == ("goods", "B", "-") and high._choice == (bh, ("goods", "B", "-"), 1.1)
    cheap = dict(UNIT, cost=int(ask / 1.1))                         # 0.9 x ask would fall under this unit's floor
    a, f, _ = low._terms(cheap)
    assert a >= f


def test_a_deal_pays_the_arm_its_board_score_and_a_timeout_pays_zero(tmp_path, monkeypatch):
    s, b = shop(tmp_path, monkeypatch, 1.1)
    for task, outcome, price in (("t1", "deal", 180_000), ("t2", "timeout", None)):
        s._terms(dict(UNIT))
        s.open[task] = {"unit": dict(UNIT), "first_ask": 200_000, "offers": [], "t0": time.time(), "choice": s._choice}
        s._end(task, outcome, price)
    assert b.paid == [(("goods", "B", "-"), 1.1, score_model.sale_value(None, 180_000, 100_000), "deal"),
                      (("goods", "B", "-"), 1.1, 0.0, "timeout")]


def test_premium_goods_use_their_own_arms_and_a_switch_turns_the_bandit_off(tmp_path, monkeypatch):
    s, b = shop(tmp_path, monkeypatch, 1.0)
    s._terms(dict(UNIT, list_price=config.PREMIUM_MIN_LIST, cost=400_000))
    assert b.context[0] == "premium"
    assert max(config.PREMIUM_ASK_ARMS) <= 1.2 and min(config.ASK_ARMS) >= 0.8      # wider needs the human's yes
    monkeypatch.setattr(config, "BANDIT_ASK_ENABLED", False)
    s._terms(dict(UNIT))
    assert s._choice is None
