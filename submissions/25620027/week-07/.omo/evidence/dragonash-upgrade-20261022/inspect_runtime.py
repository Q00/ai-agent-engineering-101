import hashlib
import json
from pathlib import Path
import sqlite3

from a2a.types import Task

root = Path("/data")
db = sqlite3.connect("file:/data/seller-state.sqlite3?mode=ro", uri=True)
rows = db.execute("SELECT payload FROM tasks WHERE task_id=?", ("e15a01e4-3fae-4a8c-a6df-0d84f6544663",)).fetchall()
tasks = []
for (payload,) in rows:
    task = Task()
    task.ParseFromString(payload)
    tasks.append({"task": task.id, "state": task.status.state,
                  "history": [{"role": m.role, "text": " ".join(p.text for p in m.parts)[:700]} for m in task.history]})
quota = sqlite3.connect("file:/data/paid-quota.sqlite3?mode=ro", uri=True)
tables = [row[0] for row in quota.execute("SELECT name FROM sqlite_master WHERE type='table'")]
usage = {}
for name in tables:
    if name.isidentifier():
        usage[name] = quota.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0]
print(json.dumps({"guard": json.loads((root / "guard-state.json").read_text()),
                  "baseline_sha256": hashlib.sha256((root / "baseline.json").read_bytes()).hexdigest(),
                  "quota_row_counts": usage, "sample_stalled_task": tasks}, ensure_ascii=False, indent=2))
