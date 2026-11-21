"""Small paid decision probes, not a leaderboard-performance benchmark."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from r3.state import ROOT, Journal, policy
from r3.model import Chooser, ModelError


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    j = Journal(ROOT / ".market" / ("model-probe-" + stamp))
    chooser = Chooser(j, policy())
    cases = [
        ("buyer", [{"id": "small", "score": 40000, "list_price": 100000, "price": 60000},
                   {"id": "large", "score": 60000, "list_price": 150000, "price": 90000}],
         {"both_acceptable_and_useful": True, "budget": 100000}, "large"),
        ("shop", [{"id": "sell", "score": 7000, "recovery": 7000},
                  {"id": "expire", "score": 0, "recovery": 0}],
         {"already_paid_cost": 10000, "expires_in_seconds": 30, "no_future_buyer_opportunity": True}, "sell"),
        ("buyer", [{"id": "bad_food", "score": -3000, "shelf_hours_left": 1, "price": 3000},
                   {"id": "useful_food", "score": 4000, "shelf_hours_left": 6, "price": 6000}],
         {"required_use_hours": 3, "both_list_prices": 10000}, "useful_food")]
    rows = []
    for critic in (False, True):
        for role, candidates, context, expected in cases:
            start = time.monotonic()
            try:
                selected = chooser._call(role + ("_critic" if critic else "_actor"), candidates, context, critic=critic)
                rows.append({"model": policy()["critic_model" if critic else "actor_model"], "role": role,
                             "expected": expected, "chosen": selected["id"], "passed": selected["id"] == expected,
                             "seconds": time.monotonic() - start})
            except ModelError as exc:
                rows.append({"model": policy()["critic_model" if critic else "actor_model"], "passed": False, "error": str(exc)})
                break  # Do not keep billing a model whose credentials/access failed.
    report = {"scope": "Small synthetic model probes; no public market transaction or broad quality claim",
              "results": rows, "usage": j.events("model_usage"), "passed": len(rows) == 6 and all(r["passed"] for r in rows)}
    path = ROOT / "logs" / ("model-probe-" + stamp + ".json")
    with path.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": report["passed"], "results": rows, "log": str(path)}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
