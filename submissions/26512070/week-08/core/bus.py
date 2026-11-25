"""The bus: everything the inner and outer runtimes say to each other (HARNESS.md 2.2).

- status: each runtime rewrites bus/status/<runtime>.json about once a minute. The other side
  treats an old file as "that runtime is down" and carries on with what it last had.
- alerts: one file per alert in bus/alerts/, for urgent things (payment refusals piling up,
  the balance near its floor, the harness failing again and again).
- load_validated: read a file the other runtime owns (strategy, facts). When it fits its
  schema, keep a copy as the last good one; when it does not, keep using the last good copy
  and raise one alert for that broken version.

Every file has one owner. The last good copies live under bus/lastgood/<reader>/, owned by the reader.
"""
import os
import time
from pathlib import Path

from core import atomic, paths, schema

_seq = 0
_alerted = set()     # (path, mtime) of broken files already reported by this process


def _now():
    return time.time()


def write_status(runtime: str, **fields) -> dict:
    st = {"schema_version": schema.SCHEMA_VERSION, "runtime": runtime, "ts": _now(), "pid": os.getpid(), **fields}
    errors = schema.validate(st, schema.STATUS)
    if errors:
        raise ValueError(f"status does not fit the schema: {errors}")
    atomic.write_json(paths.bus() / "status" / f"{runtime}.json", st)
    return st


def read_status(runtime: str):
    """The runtime's last status, or None when there is none or it does not fit the schema."""
    try:
        st = atomic.read_json(paths.bus() / "status" / f"{runtime}.json")
    except ValueError:
        return None
    return st if st is not None and not schema.validate(st, schema.STATUS) else None


def is_stale(status, max_age: float, now=None) -> bool:
    """True when there is no status or it is older than max_age seconds."""
    return status is None or (now if now is not None else _now()) - status["ts"] > max_age


def alert(writer: str, level: str, kind: str, message: str, data=None) -> dict:
    global _seq
    _seq += 1
    ts = _now()
    a = {"schema_version": schema.SCHEMA_VERSION, "event_id": f"{writer}-{os.getpid()}-{int(ts * 1000)}-{_seq:06d}",
         "ts": ts, "writer": writer, "level": level, "kind": kind, "message": message, "data": data or {}}
    errors = schema.validate(a, schema.ALERT)
    if errors:
        raise ValueError(f"alert does not fit the schema: {errors}")
    atomic.write_json(paths.bus() / "alerts" / f"{a['event_id']}.json", a)
    return a


def alerts(since: float = 0):
    """Valid alerts newer than since, oldest first."""
    out = []
    d = paths.bus() / "alerts"
    if d.is_dir():
        for f in d.glob("*.json"):
            try:
                a = atomic.read_json(f)
            except ValueError:
                continue
            if a and not schema.validate(a, schema.ALERT) and a["ts"] > since:
                out.append(a)
    return sorted(out, key=lambda a: a["ts"])


def load_validated(path, schema_name: str, reader: str):
    """(object, fresh, errors). fresh is False when path is missing or broken and the last good copy
    (or None) came back instead."""
    path = Path(path)
    keep = paths.bus() / "lastgood" / reader / path.name
    try:
        obj = atomic.read_json(path)
        errors = ["missing"] if obj is None else schema.validate(obj, schema.SCHEMAS[schema_name])
    except ValueError as e:
        obj, errors = None, [f"not JSON: {e}"]
    if not errors:
        if atomic.read_json(keep) != obj:
            atomic.write_json(keep, obj)
        return obj, True, []
    if errors != ["missing"]:
        try:
            mark = (str(path), path.stat().st_mtime)
        except OSError:
            mark = (str(path), None)
        if mark not in _alerted:
            _alerted.add(mark)
            alert(reader, "warn", "invalid_file", f"{path.name} does not fit the {schema_name} schema; "
                  f"using the last good copy", {"path": str(path), "errors": errors[:5]})
    try:
        last = atomic.read_json(keep)
    except ValueError:
        last = None
    return last, False, errors
