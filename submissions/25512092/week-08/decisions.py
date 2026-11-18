"""One log for every decision, with its reason: logs/decisions.jsonl (append-only, one JSON object a line).

    {"ts", "dept", "action", "target", "value", "reason", "policy_version", "limit_hit"}

dept: ceo, purchasing, sales, logistics, cs, qc, buyer. Writing never raises: a failed write is dropped, so the
log can never stop a sale, a purchase or a post. Several processes append (seller, buyer, restock); each line is
one small write.
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "logs", "decisions.jsonl")


def _policy_version():
    try:
        import policy
        return policy.version()
    except Exception:
        return None


def record(dept, action, target, value, reason, limit_hit=None):
    try:
        line = json.dumps({"ts": round(time.time(), 1), "dept": dept, "action": action, "target": target,
                           "value": value, "reason": reason, "policy_version": _policy_version(),
                           "limit_hit": limit_hit}, ensure_ascii=False, default=str)
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
