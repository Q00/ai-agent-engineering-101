"""labpc/rejoin_guard.py: idle and market-health signals, the watchdog log parse, and when an episode stops."""

import importlib.util
import json
import os
import threading
import time
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "rejoin_guard", Path(__file__).resolve().parents[1] / "labpc" / "rejoin_guard.py"
)
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)


@pytest.fixture
def w(tmp_path, monkeypatch):
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Seoul"
    time.tzset()
    (tmp_path / ".r3").mkdir()
    (tmp_path / "logs").mkdir()
    for f in (".r3/metrics.jsonl", ".r3/events.jsonl", G.KIT_LOG):
        (tmp_path / f).write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "LOG", str(tmp_path / "guard.log"))
    yield tmp_path
    if old_tz is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old_tz
    time.tzset()


def stamp(ago):
    return time.strftime("%H:%M:%S", time.localtime(time.time() - ago))


def add(path, lines):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("".join(x + "\n" for x in lines))


def test_hms_age_wraps_midnight_and_allows_skew(w):
    now = time.mktime((2026, 11, 5, 0, 0, 30, 0, 0, -1))
    assert G.hms_age("23:59:30", now) == 60
    assert G.hms_age("00:00:00", now) == 30
    assert G.hms_age("00:01:00", now) == -30


def test_idle_counts_requests_not_policy_reloads(w):
    now = time.time()
    add(
        ".r3/metrics.jsonl",
        [
            json.dumps({"hook": "seller", "decision": "open", "ts": now - 700}),
            json.dumps({"hook": "seller", "decision": "policy_reload", "ts": now - 5}),
            json.dumps({"hook": "restock", "decision": "open", "ts": now - 5}),
        ],
    )
    assert 690 <= G.idle_age() <= 710
    add(
        ".r3/metrics.jsonl",
        [json.dumps({"hook": "seller", "decision": "declined", "ts": now - 100})],
    )
    assert 90 <= G.idle_age() <= 110
    add(G.KIT_LOG, [f"{stamp(20)} [acp] settled a sale"])
    assert 15 <= G.idle_age() <= 25


def test_idle_without_any_signal_is_huge(w):
    assert G.idle_age() >= 10**9 - 1


def test_market_health_needs_4_of_6_big_fresh_lists_and_counts_market_settles(w):
    sizes = [41, 41, 13, 13, 48, 48]
    add(
        G.KIT_LOG,
        [
            f"{stamp(300 - 50 * i)} [kit] {n} shops in .market/shops.json"
            for i, n in enumerate(sizes)
        ],
    )
    now = time.time()
    add(
        ".r3/events.jsonl",
        [
            json.dumps({"type": "settle", "src": "market", "ts": now - 60})
            for _ in range(3)
        ]
        + [json.dumps({"type": "settle", "src": "market", "ts": now - 900})]
        + [json.dumps({"type": "settle", "src": "ours", "ts": now - 60})],
    )
    ok, recent, settles = G.market_health()
    assert (ok, recent, settles) == (True, sizes, 3)
    add(G.KIT_LOG, [f"{stamp(5)} [kit] 31 shops in .market/shops.json"] * 3)
    assert G.market_health()[0] is False


def test_market_health_stale_lists_are_not_ok(w):
    add(G.KIT_LOG, [f"{stamp(600)} [kit] 48 shops in .market/shops.json"] * 6)
    assert G.market_health()[0] is False


def _later(text, delay=0.2):
    def run():
        time.sleep(delay)
        add(G.KIT_LOG, [text])

    threading.Thread(target=run, daemon=True).start()


def test_rejoin_ok_and_refusal_and_watchdog_line_alone(w):
    kills = []
    kill = lambda pid, sig: kills.append((pid, sig))
    _later(
        f"{stamp(0)} [market] shop registered again as x at https://a.trycloudflare.com"
    )
    assert G.rejoin(11, kill=kill, poll=0.05, wait=3) == "ok"
    assert kills == [(11, G.signal.SIGTERM)]
    _later(
        f"{stamp(0)} [kit] watchdog: SystemExit: [market] join as shop failed: 400 "
        '{"error":"cannot read an agent card at https://b.trycloudflare.com (TimeoutError)"}'
    )
    res = G.rejoin(12, kill=kill, poll=0.05, wait=3)
    assert res.startswith("fail 400") and "cannot read an agent card" in res
    _later(
        f"{stamp(0)} [kit] watchdog: RuntimeError: the new tunnel https://c.trycloudflare.com did not answer"
    )
    assert G.rejoin(13, kill=kill, poll=0.05, wait=0.6) == "timeout"


def _episode(monkeypatch, results, idle=1000, kits=None):
    calls = []
    kits = iter(kits or [])
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    monkeypatch.setattr(G, "idle_age", lambda: idle)
    monkeypatch.setattr(G, "kit_tunnel", lambda: next(kits, (5, 100)))
    res = iter(results)
    monkeypatch.setattr(G, "rejoin", lambda cf: calls.append(cf) or next(res))
    return G.episode(100), calls


def test_episode_retries_only_the_known_refusal(w, monkeypatch):
    n, calls = _episode(monkeypatch, ["fail 400 cannot read an agent card at x", "ok"])
    assert (n, len(calls)) == (2, 2)
    n, _ = _episode(monkeypatch, ["timeout", "ok"])
    assert n == 1
    n, _ = _episode(monkeypatch, ["fail 409 name taken", "ok"])
    assert n == 1
    n, _ = _episode(monkeypatch, ["fail 400 cannot read an agent card"] * 20)
    assert n == G.MAX_TRIES


def test_episode_never_stops_a_tunnel_while_requests_arrive_or_the_kit_changed(
    w, monkeypatch
):
    assert _episode(monkeypatch, ["ok"], idle=30)[0] == 0
    assert _episode(monkeypatch, ["ok"], kits=[(5, 200)])[0] == 0


def test_log_masks_urls(w):
    G.log(
        "try 1: fail 400 cannot read an agent card at https://abc-def.trycloudflare.com (TimeoutError)"
    )
    G.log("host abc-def.trycloudflare.com done")
    text = Path(G.LOG).read_text()
    assert "trycloudflare" not in text and text.count("<url>") == 2
