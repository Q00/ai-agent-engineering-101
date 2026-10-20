"""Personal outcomes keyed by registry name, with atomic cross-process writes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time


class MarketMemory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self):
        with self.path.with_suffix(".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {"version": 1, "shops": {}}
            if data.get("version") != 1 or not isinstance(data.get("shops"), dict):
                raise ValueError("Invalid memory; preserve the file for diagnosis")
            yield data
            with tempfile.NamedTemporaryFile("w", dir=self.path.parent, delete=False) as stream:
                json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
                temporary = stream.name
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)

    def read(self):
        with self.transaction() as data:
            return json.loads(json.dumps(data))

    def record(self, name, *, item, budget, status, price=None, reason="", issues=None, signed=False):
        with self.transaction() as data:
            shop = data["shops"].setdefault(name, {"trades": 0, "completed": 0, "failures": 0, "recent": []})
            shop["trades"] += 1
            shop["completed" if status == "completed" else "failures"] += 1
            shop["recent"].append({"time": datetime.now(timezone.utc).isoformat(), "item": item,
                "budget": budget, "status": status, "price": price, "reason": reason[:500],
                "issues": issues or [], "signature_present": signed})
            shop["recent"] = shop["recent"][-30:]
            # A factual receipt inconsistency is stronger evidence than a bad rating.
            if status == "disputed":
                shop["avoid_until"] = time.time() + 24 * 3600


def rank_shops(shops, memory, item, budget, *, now=None):
    now = time.time() if now is None else now
    ranked = []
    for shop in shops:
        past = memory.get("shops", {}).get(shop["name"], {})
        if past.get("avoid_until", 0) > now:
            continue
        reviews = [r for r in shop.get("reviews", []) if isinstance(r, dict)
                   and isinstance(r.get("stars"), (int, float)) and not isinstance(r["stars"], bool)
                   and 1 <= r["stars"] <= 5][:20]
        score = (sum(r["stars"] for r in reviews) + 8) / (len(reviews) + 2)
        score += min(past.get("completed", 0), 5) * 0.15
        recent = past.get("recent", [])[-5:]
        score -= sum(r.get("status") == "error" for r in recent) * 0.6
        score -= sum(r.get("status") == "disputed" for r in recent) * 2
        prices = [r["price"] for r in recent if r.get("item") == item and r.get("status") == "completed"
                  and isinstance(r.get("price"), (int, float))]
        if prices:
            score += 0.4 if prices[-1] <= budget else -0.7
        ranked.append({**shop, "score": round(score, 3), "personal_memory": past,
            "reviews": [{"stars": r["stars"], "review": str(r.get("review", ""))[:250],
                         "round": r.get("round")} for r in reviews[:5]]})
    return sorted(ranked, key=lambda s: (-s["score"], s["name"]))
