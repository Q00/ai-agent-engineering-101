"""kit_patches on the official v4 kit (scripts/market_kit.py), which is what the service runs.
2026-11-10: a refused join after a tunnel rotation was never retried; the shop was dark 275 min in 24 h."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import kit_patches


class Proc:
    def poll(self):
        return None


class Stop(Exception):
    pass


def fake_kit(loops, card=lambda url: False, urls=("https://new-1.example", "https://new-2.example")):
    ticks = iter(range(loops))
    logs = []

    def sleep(s):
        if next(ticks, None) is None:
            raise Stop()
    u = iter(urls)
    kit = SimpleNamespace(time=SimpleNamespace(sleep=sleep), card_ok=card, stop=lambda p: None,
                          open_tunnel=lambda port: (Proc(), next(u)), wait_card=lambda url, t=0: True,
                          log=logs.append, start=None, fill=None, pump=None)
    return kit, logs


def run(kit, join):
    proxy, state = SimpleNamespace(public="https://old.example"), {"own_shop": "https://old.example"}
    a = SimpleNamespace(tunnel=True, name="myeonggi")
    with pytest.raises(Stop):
        kit_patches.watchdog(kit)(a, {}, ".", SimpleNamespace(join=join), {"shop": "k"}, [Proc(), Proc()], proxy,
                                  1, 2, state, every=0)
    return proxy, state


def test_refused_join_is_retried_with_the_same_tunnel(monkeypatch):
    monkeypatch.setattr(kit_patches, "JOIN_GAP", 0)
    calls = []

    def join(role, name, key, url):
        calls.append(url)
        if len(calls) < 3:
            raise SystemExit("[market] join as shop failed: 400 cannot read an agent card (TimeoutError)")
        return {"name": name}
    # old URL dead (3 misses), new tunnel answers while we retry the join
    kit, _ = fake_kit(loops=20, card=lambda url: url != "https://old.example")
    proxy, state = run(kit, join)
    assert calls == ["https://new-1.example"] * 3
    assert proxy.public == state["own_shop"] == "https://new-1.example"


def test_url_moves_only_after_an_accepted_join(monkeypatch):
    def join(*args):
        raise SystemExit("refused")
    kit, _ = fake_kit(loops=5, card=lambda url: url != "https://old.example")
    proxy, state = run(kit, join)
    assert proxy.public == state["own_shop"] == "https://old.example"


def test_gives_up_on_a_tunnel_that_stops_answering():
    kit, logs = fake_kit(loops=20, card=lambda url: False)
    calls = []

    def join(role, name, key, url):
        calls.append(url)
        raise SystemExit("refused")
    out = kit_patches.join_new_tunnel(kit, SimpleNamespace(join=join), SimpleNamespace(name="x"), {"shop": "k"},
                                      "https://new-1.example", tries=6, gap=0)
    assert out is None and calls == ["https://new-1.example"]
    assert any("stopped answering" in m for m in logs)


def test_stop_ends_a_process_without_its_own_group():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])   # like cloudflared: no new session
    kit_patches.stop(SimpleNamespace())(p)
    assert p.poll() is not None


def test_apply_patches_the_official_kit():
    import importlib.util
    spec = importlib.util.spec_from_file_location("official_market_kit", HERE.parents[2] / "scripts" / "market_kit.py")
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    assert kit.KIT_VERSION == "4"
    kit_patches.apply(kit)
    assert kit.watchdog.__module__ == "kit_patches"
