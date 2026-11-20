"""Small atomic state files shared by the seller and the local kit adapter."""

import json
from pathlib import Path
from uuid import uuid4


def read(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf8"))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf8")
    tmp.replace(path)


def task_file(md, group, task):
    # Remote task IDs must never become file paths.
    import hashlib
    return Path(md) / group / (hashlib.sha256(task.encode()).hexdigest() + ".json")
