"""Metadata-only Redis application audit; never log payloads or exception text."""
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

SESSION = uuid.uuid4().hex
LOG_DIR = Path(__file__).resolve().parent / "logs" / "redis-app"


def record(event, **metadata):
    row = dict(time=datetime.now(timezone.utc).isoformat(), pid=os.getpid(),
               session=SESSION, event=event, **metadata)
    line = json.dumps(row, ensure_ascii=True) + "\n"
    # Logging failures must not turn a completed Redis write into a failed trade.
    try:
        sys.stderr.write(line)
    except Exception:
        pass
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / f"{SESSION}-{os.getpid()}.jsonl").open("a", encoding="utf-8") as out:
            out.write(line)
    except Exception:
        try:
            sys.stderr.write('{"event":"redis_audit_file_unavailable"}\n')
        except Exception:
            pass


def audited(kind, operation):
    def decorate(method):
        @wraps(method)
        async def wrapped(self, *args, **kwargs):
            target = args[0] if args else kwargs.get("task", kwargs.get("task_id"))
            task_id = getattr(target, "id", target)
            metadata = dict(store=kind, operation=operation)
            if isinstance(task_id, str):
                metadata["task_ref"] = hashlib.sha256(task_id.encode()).hexdigest()
            try:
                result = await method(self, *args, **kwargs)
            except Exception as exc:
                record("redis_operation", **metadata, outcome="error",
                       error_type=type(exc).__name__)
                raise
            if operation == "get":
                metadata["found"] = result is not None
            if kind == "task" and operation == "save":
                metadata["version"] = repr(result)
            record("redis_operation", **metadata, outcome="success")
            return result
        return wrapped
    return decorate
