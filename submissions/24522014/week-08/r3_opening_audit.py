"""Record local R3 opening evidence without trading, network or model calls."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from market_state import read, write
from settlement_memory import payment_outcomes

ROOT = Path(__file__).resolve().parent


def main():
    md = ROOT / ".market"
    snapshot = read(md / "market-intel.json", {})
    inventory = read(md / "inventory.json", {})
    payments = list(payment_outcomes(md / "payments.jsonl").values())
    path = ROOT / "logs/market-kit.log"
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell()-1048576))
        tail = stream.read().decode("utf8", errors="replace")
    start = tail.rfind("[kit] market_kit")
    tail = tail[start:] if start >= 0 else tail
    evidence = {tag: [line for line in tail.splitlines() if tag in line][-3:]
                for tag in ("shop registered", "buyer registered", "[supply]", "[acp] settled", "[acp] paid", "[forum]")}
    sent_path = md / "outbox.sent.jsonl"
    sent = [json.loads(line) for line in sent_path.read_text(encoding="utf8").splitlines() if line.strip()] if sent_path.exists() else []
    result = {"audit_at": datetime.now(timezone.utc).isoformat(),
              "market_observed_at": snapshot.get("observed_at"), "round": inventory.get("round"),
              "competition": snapshot.get("competition"), "observed_sku_groups": len(snapshot.get("skus", {})),
              "inventory": {k: inventory.get(k) for k in ("balance", "start", "injected", "bankruptcies", "bankrupt_line", "restock")},
              "stock_units": len(inventory.get("units", [])), "payment_attempts": len(payments),
              "local_paid": sum(p.get("paid") is True for p in payments),
              "refusal_reasons": dict(Counter(p.get("reason", "unknown") for p in payments if p.get("paid") is False)),
              "successful_forum_posts": sum(p.get("status") in (200, 201) and "post" in p.get("sent", {}) for p in sent),
              "runtime_evidence": evidence,
              "note": "Board and local file observations have different timestamps; initial values are not final performance."}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT / "logs/r3-opening" / f"{stamp}-{uuid4().hex[:8]}.json"
    write(out, result)
    print(json.dumps({"log": str(out), "round": result["round"], "stock_units": result["stock_units"],
                      "local_paid": result["local_paid"], "successful_forum_posts": result["successful_forum_posts"],
                      "rank_at_observation": (result.get("competition") or {}).get("rank")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
