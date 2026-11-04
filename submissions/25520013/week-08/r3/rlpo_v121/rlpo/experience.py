"""Experience log (s, a, r): append-only JSONL, schema checked on write. Never truncated or deleted."""
import json
from pathlib import Path

STATUSES = ("rejected", "lost", "applied", "rolled_back", "parse_error", "off", "proposed", "approved", "alarm")
REQUIRED = ("id", "t", "s_sha", "s", "a", "a_status", "reject_reason", "replay", "r_proxy", "r_total",
            "gamma_t", "policy_sha_before", "policy_sha_after", "model", "tokens_in", "tokens_out", "latency_ms")


class SchemaError(ValueError):
    pass


def validate(row):
    miss = [k for k in REQUIRED if k not in row]
    if miss:
        raise SchemaError(f"missing {miss}")
    if row["a_status"] not in STATUSES:
        raise SchemaError("bad a_status: " + repr(row["a_status"]))
    if not isinstance(row["t"], (int, float)):
        raise SchemaError("t must be a number")
    return row


class Log:
    def __init__(self, path):
        self.path = Path(path)

    def append(self, row):
        validate(row)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        return row

    def rows(self):
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def next_id(self):
        return len(self.rows()) + 1
