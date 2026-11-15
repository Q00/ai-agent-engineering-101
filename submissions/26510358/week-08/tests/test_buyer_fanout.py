"""E: a canary share of orders asks every candidate shop at once and negotiates with the cheapest quotes first.
2026-11-12: on the same SKU and grade the cheapest shop sells 9.3% of list under the median shop (60k settles, 24 h);
we paid 8.8% of list over the cheapest, talking to 2.3 of ~8 candidate shops per order."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import buyer_r3
from r3lib import config


class Shop:
    """Asks `ask`; a counter at or above `floor` gets a signed receipt."""
    def __init__(self, url, ask, floor=None, delay=0.0, reject=False):
        self.url, self.ask, self.floor, self.delay, self.reject = url, ask, floor or ask, delay, reject
        self.sent, self.cancelled = [], False

    def _task(self, state, text, rec=None):
        t = {"id": self.url, "status": {"state": state, "message": {"parts": [{"text": text}]}}}
        if rec:
            t["artifacts"] = [{"parts": [{"data": rec}]}]
        return t

    def send(self, text, task_id=None):
        time.sleep(self.delay)
        self.sent.append(text)
        if task_id is None:
            if self.reject:
                return self._task("TASK_STATE_REJECTED", "none of that in stock")
            return self._task("TASK_STATE_INPUT_REQUIRED", f"G1, grade A. Asking {self.ask}. Send your offer.")
        if int(text) >= self.floor:
            return self._task("TASK_STATE_COMPLETED", f"Deal at {text}.",
                              {"shop": self.url, "item": "G1", "price": int(text), "task": "x", "sig_seller": "s"})
        return self._task("TASK_STATE_INPUT_REQUIRED", f"Asking {self.ask}.")

    def cancel(self, task_id):
        self.cancelled = True

    def close(self):
        pass


def cand(url, score=0.3):
    return {"shop": url, "name": url, "sku": "G1", "item_name": "의자", "kind": "goods", "list": 100_000,
            "max_pay": 75_000, "budget": 120_000, "score": score, "reliability": 1.0, "opening": None}


class Mem:
    def __init__(self):
        self.negs, self.intents = [], []

    def record_negotiation(self, *a):
        self.negs.append(a)

    def record_purchase_intent(self, *a):
        self.intents.append(a)


def _wire(monkeypatch, tmp_path, shops):
    by = {s.url: s for s in shops}
    monkeypatch.setattr(buyer_r3.a2a, "Client", lambda url, timeout=60: by[url])
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    return by


ORDER = {"order": "o1", "acceptable": ["G1"], "budget": 120_000, "kind": "goods"}


def test_canary_is_a_stable_share_of_orders():
    picks = [buyer_r3.fanout_arm(f"o-{i:06x}") for i in range(4000)]
    assert abs(sum(picks) / len(picks) - config.BUYER_FANOUT_SHARE) < 0.03
    assert buyer_r3.fanout_arm("o-abc") == buyer_r3.fanout_arm("o-abc")


def test_fanout_buys_from_the_cheapest_quote_and_cancels_the_rest(monkeypatch, tmp_path):
    shops = [Shop("http://a", 70_000), Shop("http://b", 52_000), Shop("http://c", 61_000)]
    _wire(monkeypatch, tmp_path, shops)
    cands = [cand("http://a", 0.4), cand("http://b", 0.3), cand("http://c", 0.2)]   # the old order: a first
    out = buyer_r3.run_order(cands, dict(ORDER), Mem(), deadline=time.time() + 60, arm="fanout")
    assert out["shop"] == "http://b"
    assert all(len(s.sent) >= 1 for s in shops)                 # everyone was asked once
    assert shops[0].cancelled and shops[2].cancelled and not shops[1].cancelled


def test_serial_arm_keeps_the_old_order(monkeypatch, tmp_path):
    shops = [Shop("http://a", 70_000), Shop("http://b", 52_000)]
    _wire(monkeypatch, tmp_path, shops)
    out = buyer_r3.run_order([cand("http://a", 0.4), cand("http://b", 0.3)], dict(ORDER), Mem(),
                             deadline=time.time() + 60, arm="serial")
    assert out["shop"] == "http://a" and shops[1].sent == []


def test_a_slow_or_empty_shop_does_not_hold_the_order(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BUYER_QUOTE_TIMEOUT_S", 0.2)
    slow, empty, ok = Shop("http://slow", 40_000, delay=0.6), Shop("http://empty", 1, reject=True), \
        Shop("http://ok", 60_000)
    _wire(monkeypatch, tmp_path, [slow, empty, ok])
    t0 = time.time()
    out = buyer_r3.run_order([cand("http://slow"), cand("http://empty"), cand("http://ok")], dict(ORDER), Mem(),
                             deadline=time.time() + 60, arm="fanout")
    assert out["shop"] == "http://ok" and time.time() - t0 < 0.55
    for _ in range(20):                                          # the late answer is cancelled when it comes
        if slow.cancelled:
            break
        time.sleep(0.1)
    assert slow.cancelled


def test_fanout_asks_each_shop_once_with_its_best_candidate(monkeypatch, tmp_path):
    a = Shop("http://a", 60_000)
    _wire(monkeypatch, tmp_path, [a])
    second = dict(cand("http://a", 0.1), sku="G2")
    out = buyer_r3.run_order([cand("http://a", 0.4), second], dict(ORDER), Mem(), deadline=time.time() + 60,
                             arm="fanout")
    assert out["shop"] == "http://a" and a.sent[0].startswith("I want to buy 의자 (G1)")
    assert threading.active_count() < 10


def test_early_arm_stops_waiting_after_enough_quotes(monkeypatch, tmp_path):
    """S1 (2026-11-15): with BUYER_QUOTE_ENOUGH usable quotes in, the early arm waits only BUYER_QUOTE_GRACE_S more."""
    monkeypatch.setattr(config, "BUYER_QUOTE_TIMEOUT_S", 3.0)
    monkeypatch.setattr(config, "BUYER_QUOTE_ENOUGH", 2)
    monkeypatch.setattr(config, "BUYER_QUOTE_GRACE_S", 0.2)
    monkeypatch.setattr(config, "BUYER_QUOTE_MIN_WAIT_S", 0.0)
    shops = [Shop("http://a", 60_000), Shop("http://b", 62_000), Shop("http://slow", 40_000, delay=1.5)]
    _wire(monkeypatch, tmp_path, shops)
    cands = [cand(s.url) for s in shops]
    monkeypatch.setattr(buyer_r3, "quote_early_arm", lambda oid: True)
    t0 = time.time()
    quotes = buyer_r3.fanout_quotes(cands, dict(ORDER))
    assert time.time() - t0 < 1.0 and {c["shop"] for c, _, _ in quotes} == {"http://a", "http://b"}
    assert buyer_r3.fanout_quotes.last["early"] is True and len(buyer_r3.fanout_quotes.last["usable_at"]) == 2
    buyer_r3.close_quotes(quotes)
    monkeypatch.setattr(buyer_r3, "quote_early_arm", lambda oid: False)   # the other arm still waits for the slow one
    quotes = buyer_r3.fanout_quotes(cands, dict(ORDER))
    assert {c["shop"] for c, _, _ in quotes} == {"http://a", "http://b", "http://slow"}
    buyer_r3.close_quotes(quotes)


def test_early_arm_is_a_stable_share_independent_of_cost_open():
    picks = [buyer_r3.quote_early_arm(f"o-{i:06x}") for i in range(4000)]
    assert abs(sum(picks) / len(picks) - config.BUYER_QUOTE_EARLY_SHARE) < 0.03


def test_early_arm_still_waits_the_minimum(monkeypatch, tmp_path):
    """The order gap is the market's (~20 s), so the early stop keeps waiting BUYER_QUOTE_MIN_WAIT_S for late quotes."""
    monkeypatch.setattr(config, "BUYER_QUOTE_TIMEOUT_S", 3.0)
    monkeypatch.setattr(config, "BUYER_QUOTE_ENOUGH", 2)
    monkeypatch.setattr(config, "BUYER_QUOTE_GRACE_S", 0.1)
    monkeypatch.setattr(config, "BUYER_QUOTE_MIN_WAIT_S", 1.0)
    shops = [Shop("http://a", 60_000), Shop("http://b", 62_000), Shop("http://late", 40_000, delay=0.6),
             Shop("http://never", 40_000, delay=5.0)]
    _wire(monkeypatch, tmp_path, shops)
    monkeypatch.setattr(buyer_r3, "quote_early_arm", lambda oid: True)
    t0 = time.time()
    quotes = buyer_r3.fanout_quotes([cand(s.url) for s in shops], dict(ORDER))
    took = time.time() - t0
    assert 0.9 < took < 1.6 and {c["shop"] for c, _, _ in quotes} == {"http://a", "http://b", "http://late"}
    buyer_r3.close_quotes(quotes)
