"""Append-only JSONL evidence; new run identifiers preserve failed attempts."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import uuid


class Trace:
    def __init__(self, role: str, directory: str | Path | None = None):
        self.run_id = uuid.uuid4().hex[:12]
        self.role = role
        self.directory = Path(directory or os.environ.get("A2A_TRACE_DIR", "logs/runtime"))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{role}-{self.run_id}.jsonl"
        self.lock = threading.Lock()

    def write(self, event: str, **fields):
        record = {"time": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id,
                  "role": self.role, "event": event, **fields}
        with self.lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        summary = {k: v for k, v in fields.items() if k not in {"prompt", "context", "card", "request", "response", "command"}}
        print(json.dumps({"event": event, **summary}, ensure_ascii=False, default=str), flush=True)
