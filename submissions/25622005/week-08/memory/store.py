"""Storage primitives: append-only JSONL, atomic JSON, incremental reads, hook diagnostics.

Every writer appends whole lines with one os.write and fsync, so a crash leaves at most one
truncated last line, which readers skip until it is complete. Paths are under HOME
(the week-08 folder; R3_HOME overrides it for tests).
"""
import json
import os
import time
from pathlib import Path

HOME = Path(os.environ.get("R3_HOME", Path(__file__).resolve().parents[1]))
MARKET = HOME / ".market"
LOGS = HOME / "logs"
MEM = HOME / "memory"


def append_jsonl(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_new(path, offset=0):
    """Rows appended since offset: (rows, new_offset). A file shorter than offset was replaced, so
    reading starts again from 0. An unfinished last line is left for the next call."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        offset = 0
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    rows = []
    for line in chunk[:end].split(b"\n"):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows, offset + end + 1


def tail_jsonl(path, n):
    """The last n parseable rows of a JSONL file."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            lines = f.read().split(b"\n")
    except OSError:
        return []
    rows = []
    for line in reversed(lines):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
        if len(rows) >= n:
            break
    return list(reversed(rows))


def hook_log(role, msg):
    """Diagnostics go here, never to stdout: the kit copies hook stdout into logs/market-kit.log."""
    try:
        (LOGS / "hooks").mkdir(parents=True, exist_ok=True)
        with open(LOGS / "hooks" / f"{role}.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass
