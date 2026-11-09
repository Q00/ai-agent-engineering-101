"""2026-11-09 21:37: the watchdog moved the shop's public URL to a new tunnel before the market accepted it.
The join was refused, so acp.json sent every buyer to a URL the market did not know: 0 settles for 1 h 40 m."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import market_kit


class Proc:
    def poll(self):
        return None


class Stop(Exception):
    pass


def run(monkeypatch, join, loops):
    ticks = iter(range(loops))
    monkeypatch.setattr(market_kit.time, "sleep", lambda s: next(ticks, None) is None and (_ for _ in ()).throw(Stop()))
    monkeypatch.setattr(market_kit, "card_ok", lambda base: False)
    monkeypatch.setattr(market_kit, "stop", lambda p: None)
    urls = iter(["https://new-1.example", "https://new-2.example"])
    monkeypatch.setattr(market_kit, "open_tunnel", lambda port: (Proc(), next(urls)))
    monkeypatch.setattr(market_kit, "wait_card", lambda url, t=0: True)
    monkeypatch.setattr(market_kit, "log", lambda m: None)
    proxy, state = SimpleNamespace(public="https://old.example"), {"own_shop": "https://old.example"}
    a = SimpleNamespace(tunnel=True, name="myeonggi")
    with pytest.raises(Stop):
        market_kit.watchdog(a, {}, ".", SimpleNamespace(join=join), {"shop": "k"}, [Proc(), Proc()], proxy, 1, 2,
                            state, every=0)
    return proxy, state


def test_refused_join_keeps_the_registered_url(monkeypatch):
    def join(*args):
        raise SystemExit("[market] join as shop failed: 400 cannot read an agent card")
    proxy, state = run(monkeypatch, join, loops=3)          # three misses, one rotation, join refused
    assert proxy.public == state["own_shop"] == "https://old.example"


def test_refused_join_is_retried_and_the_accepted_url_is_used(monkeypatch):
    calls = []

    def join(role, name, key, url):
        calls.append(url)
        if len(calls) == 1:
            raise SystemExit("refused")
        return {"name": name}
    proxy, state = run(monkeypatch, join, loops=5)
    assert calls == ["https://new-1.example", "https://new-2.example"]   # retried on the very next loop
    assert proxy.public == state["own_shop"] == "https://new-2.example"
