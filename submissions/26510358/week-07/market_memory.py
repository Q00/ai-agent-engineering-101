"""Personal outcomes keyed by registry name, with atomic cross-process writes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
import re
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

    def ingest_kit_feedback(self, path):
        """Read our kit's authoritative signature verdict, never call market APIs.

        Seller-supplied public keys are not trusted. Only market_kit has the
        registry key; its verdict becomes personal memory on the next order.
        """
        path = Path(path)
        if not path.exists():
            return []
        imported = []
        with self.transaction() as data:
            raw = path.read_bytes()
            offset = data.get("kit_log_offset", 0)
            if offset > len(raw):
                offset = 0
            chunk = raw[offset:]
            end = chunk.rfind(b"\n") + 1
            pending = data.get("kit_pending", {})
            for line in chunk[:end].decode("utf-8", errors="replace").splitlines():
                if "[order " in line:
                    pending = {}
                if '[buyer] {' in line:
                    try:
                        event = json.loads(line.split("[buyer] ", 1)[1])
                    except (ValueError, IndexError):
                        continue
                    if event.get("event") == "market_shop_selected":
                        pending["name"] = event.get("shop")
                if "[sign]" in line:
                    if "sig_seller ok" in line:
                        pending["signature"] = "verified"
                    elif "does not verify" in line:
                        pending["signature"] = "invalid"
                    elif "has no sig_seller" in line:
                        pending["signature"] = "missing"
                if "[market] ledger #" in line and pending.get("name"):
                    name, verdict = pending["name"], pending.get("signature")
                    shop = data["shops"].setdefault(name, {"trades": 0, "completed": 0, "failures": 0, "recent": []})
                    if verdict == "verified":
                        shop["signature_verified_by_kit"] = shop.get("signature_verified_by_kit", 0) + 1
                    elif verdict in {"invalid", "missing"}:
                        shop["signature_failures"] = shop.get("signature_failures", 0) + 1
                        shop["avoid_until"] = time.time() + (86400 if verdict == "invalid" else 1800)
                        shop["avoid_reason"] = "Kit reports " + verdict + " seller signature; incompatibility alone does not prove intent"
                    imported.append({"shop": name, "signature": verdict, "market_result": line})
                    pending = {}
            data["kit_log_offset"] = offset + end
            data["kit_pending"] = pending
        return imported

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
            elif status == "untrusted":
                shop["avoid_until"] = time.time() + 24 * 3600
                shop["avoid_reason"] = reason[:500]


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
        score += min(past.get("signature_verified_by_kit", 0), 3) * 0.25
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
