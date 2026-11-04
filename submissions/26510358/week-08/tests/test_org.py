"""Departments over internal signed A2A: envelopes, receipts, the CEO's restock cycle, server and fallback."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import config
from r3lib.org import client, departments, envelope
from test_r3_core import CAT, mem, settle


@pytest.fixture
def org(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MARKET_DIR", tmp_path / "market")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    (tmp_path / "market").mkdir()
    from r3lib import market
    monkeypatch.setattr(market, "get_json", lambda *a, **k: None)                # no network: inventory.json decides
    client._CLIENTS.clear()
    return tmp_path


def test_envelope_checks_fail_closed(org):
    env = envelope.seal("hook", "ceo", "tick", {"x": 1})
    gate = envelope.Gate("ceo")
    assert gate.open(env)["skill"] == "tick"
    with pytest.raises(envelope.Rejected, match="replayed"):
        gate.open(env)
    with pytest.raises(envelope.Rejected, match="signature"):
        envelope.Gate("ceo").open(dict(env, args={"x": 2}))                     # tampered after signing
    with pytest.raises(envelope.Rejected, match="addressed"):
        envelope.Gate("quality").open(envelope.seal("hook", "ceo", "tick", {}))
    with pytest.raises(envelope.Rejected, match="stale"):
        envelope.Gate("ceo").open(envelope.seal("hook", "ceo", "tick", {}, now=time.time() - 600))
    with pytest.raises(envelope.Rejected):
        envelope.seal("mallory", "ceo", "tick", {})


def test_receipt_binds_request_and_result(org):
    env = envelope.seal("hook", "logistics", "window", {})
    rec = envelope.receipt("logistics", env, {"near": True})
    assert envelope.check_receipt(rec, env, {"near": True})
    with pytest.raises(envelope.Rejected):
        envelope.check_receipt(rec, env, {"near": False})                       # result swapped
    with pytest.raises(envelope.Rejected):
        envelope.check_receipt(envelope.receipt("quality", env, {"near": True}), env, {"near": True})


def _market(org, monkeypatch, restock):
    m = mem(org, monkeypatch)
    now = time.time()
    m._ingest([settle(i, "G1", 85000, now - i * 120) for i in range(1, 40)])
    md = config.MARKET_DIR
    (md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 5_000_000, "units": [], "restock": restock}))
    (md / "demand.json").write_text(json.dumps([{"category": CAT["G1"]["category"], "item": "의자", "kind": "goods",
                                                 "skus": 1, "median_price": 100000}]))
    return m


def test_ceo_tick_in_process(org, monkeypatch):
    m = _market(org, monkeypatch, {"open": True})
    out = client.call("hook", "ceo", "tick", {"state": str(config.MARKET_DIR)}, mem=m)   # no server: local
    assert out["buy"] and out["buy"][0]["sku"] == "G1"
    calls = [json.loads(x) for x in (org / "logs" / "org-calls.jsonl").read_text().splitlines()]
    assert {(c["to"], c["skill"]) for c in calls} >= {("ceo", "tick"), ("logistics", "window"),
                                                       ("purchasing", "propose"), ("logistics", "approve")}
    assert all(c["via"] == "local" for c in calls)


def test_ceo_waits_outside_the_window(org, monkeypatch):
    m = _market(org, monkeypatch, {"open": False, "opens_at": time.time() + 900, "closes_at": time.time() + 1080})
    out = client.call("hook", "ceo", "tick", {}, mem=m)
    assert out == {"buy": [], "wait": "supply window not open"}


def test_logistics_approve_keeps_the_cash_reserve(org, monkeypatch):
    m = _market(org, monkeypatch, {"open": True})
    (config.MARKET_DIR / "inventory.json").write_text(json.dumps({"round": 3, "balance": config.CASH_RESERVE + 100_000,
                                                                   "units": [], "restock": {"open": True}}))
    out = departments.run("logistics", "approve", {"lines": [{"sku": "G1", "qty": 1}, {"sku": "G1", "qty": 5}]}, m, None)
    assert out["buy"] == [{"sku": "G1", "qty": 1}] and "reserve" in out["why"][0]


def test_over_a2a_server_with_receipts(org, monkeypatch):
    import uvicorn
    from r3lib.org import server
    m = _market(org, monkeypatch, {"open": True})
    port = server.free_port()
    srv = uvicorn.Server(uvicorn.Config(server.build(port, mem=m), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    import os
    (config.MARKET_DIR / "org").mkdir(exist_ok=True)
    (config.MARKET_DIR / "org" / "server.json").write_text(json.dumps({"port": port, "pid": os.getpid()}))
    try:
        out = client.call("hook", "ceo", "tick", {"state": str(config.MARKET_DIR)}, mem=m)
        assert out["buy"] and out["buy"][0]["sku"] == "G1"
        calls = [json.loads(x) for x in (org / "logs" / "org-calls.jsonl").read_text().splitlines()]
        assert {c.get("via") for c in calls if "via" in c} == {"a2a"}                 # nested calls went over A2A too
        # a forged envelope (signed by hook, then edited) is rejected by the department, not executed
        bad = dict(envelope.seal("hook", "quality", "harsh", {}), skill="refunds")
        task = client._client("quality", port).send(json.dumps(bad))
        assert task["status"]["state"] == "TASK_STATE_REJECTED"
    finally:
        srv.should_exit = True
        th.join(timeout=5)
    # server gone: the same call falls back in-process
    (config.MARKET_DIR / "org" / "server.json").write_text(json.dumps({"port": port, "pid": 999999}))
    assert client.call("hook", "logistics", "window", {}, mem=m)["near"] is True
