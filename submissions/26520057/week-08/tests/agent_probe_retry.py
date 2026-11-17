"""Quote probes: one short retry on timeout/connection error/5xx only, then the next shop; reasons counted."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import httpx

SOURCE = Path(os.environ.get("AX_TEST_SOURCE", str(Path(__file__).resolve().parent.parent)))
sys.path.insert(0, str(SOURCE))
import buyer_policy as policy
import shop_strategy

assert shop_strategy.DEFAULTS["buyer"]["probe_retry_pause"] == 1.0
request = httpx.Request("POST", "https://shop.test/a2a")
server_error = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(503, request=request))
client_error = httpx.HTTPStatusError("nope", request=request, response=httpx.Response(404, request=request))
assert policy.transient(httpx.ReadTimeout("The read operation timed out"))
assert policy.transient(httpx.ConnectError("refused")) and policy.transient(server_error)
assert not policy.transient(client_error)
assert not policy.transient(ValueError("merchant rejected request or has no fitting stock"))
assert not policy.transient(RuntimeError("merchant model currently unavailable"))

# per shop: the errors its probes raise in turn; after the list is used up the probe returns a quote
plan = {"Flaky": [httpx.ReadTimeout("The read operation timed out")],
        "Dead": [httpx.ReadTimeout("The read operation timed out"), httpx.ConnectError("refused")],
        "Rejects": [ValueError("merchant rejected request or has no fitting stock")],
        "Gone": [client_error]}
probes, closed = [], []


class FakeVisit:
    def __init__(self, choice):
        self.choice, self.base, self.retained = choice, choice["shop"]["url"], False

    def probe(self, order):
        name = self.choice["shop"]["name"]
        probes.append(name)
        errors = plan[name]
        if errors:
            raise errors.pop(0)
        return {"ask": 7000, "grade": "A", "warranty_hours": 6}

    def negotiate(self, order, deadline):
        return None, {}

    def close(self):
        closed.append(self.choice["shop"]["name"])


options = [{"shop": {"name": n, "url": "https://" + n.lower() + ".test"},
            "offer": {"sku": "G1", "name": "x", "qty": 1}, "product": {"list_price": 10000},
            "info": {"pubkey": "k"}} for n in plan]
order = {"order": "o-t", "item": "c", "acceptable": ["G1"], "kind": "goods", "budget": 9000,
         "expires": policy.market_now(policy.ROOT / ".market") + 600}
events, sleeps = [], []
with patch.object(policy, "metadata", return_value={}), patch.object(policy, "memory", return_value=({}, {})), \
     patch.object(policy, "candidates", return_value=options), patch.object(policy, "Visit", FakeVisit), \
     patch.object(policy, "log_event", side_effect=lambda **e: events.append(e)), \
     patch.object(policy, "atomic_json"), patch.object(policy.time, "sleep", side_effect=sleeps.append):
    policy.run(order, [], {})

assert probes == ["Flaky", "Flaky", "Dead", "Dead", "Rejects", "Gone"], probes
assert sleeps == [1.0, 1.0], sleeps
assert any(e["event"] == "quote" and e["shop"] == "Flaky" for e in events)
retried = [e["shop"] for e in events if e["event"] == "probe_retry"]
assert retried == ["Flaky", "Dead"], retried
finished = [e for e in events if e["event"] == "order_finished"][-1]
assert finished["probe_retries"] == 2
assert finished["fail_reasons"] == {"probe: refused": 1,
                                    "probe: merchant rejected request or has no fitting stock": 1,
                                    "probe: HTTP 404": 1,
                                    "reject: No acceptable price agreement": 1}, finished["fail_reasons"]
assert sorted(closed) == sorted(["Flaky", "Flaky", "Dead", "Dead", "Rejects", "Gone"]), closed
print("PASS one short retry only on timeout/connection/5xx, then next shop; failure reasons counted per order")
