"""Per-decision metrics, one JSON line each in .r3/metrics.jsonl. Stable schema (METRICS_SCHEMA) so that
tools/metrics_report.py can turn the log into the tables REPORT.md cites.

Fields per line:
  schema, ts (epoch s), ts_kst (ISO, +09:00), hook, decision, cum_events (ledger seq at decision time),
  prompt_chars (chars sent to an LLM for this decision; 0 when code decided), llm_calls, latency_ms,
  ingest_ms, ingested (new events this run), code_hash, policy_hash, seed, outcome (dict), rejections (dict
  reason_code -> count, buyer side, from payments.jsonl since the last decision), extra (dict).
"""
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

METRICS_SCHEMA = 1
KST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
CODE_FILES = ("ledger.py", "policy.py", "owner.py", "volume.py", "reputation.py", "metrics.py", "hooks/common.py", "hooks/seller.py",
              "hooks/restock.py", "hooks/market_buyer.py", "hooks/review.py",
              "aggregates.py", "cards.py")      # v1.5 (CR v1.3): ML's modules are part of the deployed code too


def code_hash():
    h = hashlib.sha256()
    for f in CODE_FILES:
        p = HERE / f
        if p.exists():
            h.update(f.encode() + b"\0" + p.read_bytes())
        else:
            h.update(f.encode() + b"\0MISSING")        # v1.5: a deploy without a file hashes differently
    return h.hexdigest()[:12]


class Recorder:
    def __init__(self, hook, root=None, policy_hash=None, seed=None):
        self.hook = hook
        self.root = Path(root or os.environ.get("R3_STATE", ".r3"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = self.root / "metrics.jsonl"
        self.policy_hash = policy_hash
        self.seed = seed if seed is not None else (int(os.environ["R3_SEED"]) if os.environ.get("R3_SEED") else None)
        self.code = code_hash()
        self.t0 = time.perf_counter()
        self.llm_calls = 0
        self.prompt_chars = 0

    def llm(self, prompt):
        """Count one LLM call (the hooks make none today; the meta-harness will)."""
        self.llm_calls += 1
        self.prompt_chars += len(prompt)

    def write(self, decision, cum_events, outcome=None, rejections=None, ingest_ms=None, ingested=None, extra=None):
        now = time.time()
        row = {"schema": METRICS_SCHEMA, "ts": round(now, 3),
               "ts_kst": datetime.fromtimestamp(now, KST).isoformat(timespec="seconds"),
               "hook": self.hook, "decision": decision, "cum_events": int(cum_events),
               "prompt_chars": self.prompt_chars, "llm_calls": self.llm_calls,
               "latency_ms": round((time.perf_counter() - self.t0) * 1000, 2),
               "ingest_ms": ingest_ms, "ingested": ingested,
               "code_hash": self.code, "policy_hash": self.policy_hash, "seed": self.seed,
               "outcome": outcome or {}, "rejections": rejections or {}, "extra": extra or {}}
        with open(self.file, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        return row


def policy_event(root, action, policy_hash, detail):
    """Policy change / A/B / rollback record (meta-harness, layer 4). action: change|ab_start|adopt|rollback."""
    root = Path(root or os.environ.get("R3_STATE", ".r3"))
    now = time.time()
    row = {"schema": METRICS_SCHEMA, "ts": round(now, 3),
           "ts_kst": datetime.fromtimestamp(now, KST).isoformat(timespec="seconds"),
           "action": action, "policy_hash": policy_hash, "detail": detail}
    with open(root / "policy_changes.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
