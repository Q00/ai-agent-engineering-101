"""Incrementally index this kit's confirmed R3 ACP sales without relying on a public sample.

Called under the strategy controller's writer lock. Raw logs are never edited.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re

from market_state import read, write

KST = timezone(timedelta(hours=9))
CLOCK = re.compile(r"^(\d\d):(\d\d):(\d\d) ")
REGISTER = re.compile(r"^\d\d:\d\d:\d\d \[market\] shop registered as kcwarni, round (\d+)\s*$")
SALE = re.compile(r"^\d\d:\d\d:\d\d \[acp\] settled (.+) at (\d+), ledger #(\d+), unit (\S+) grade ([ABC])\s*$")
ANCHOR = re.compile(r"^\d\d:\d\d:\d\d \[market\] shop registered as kcwarni, round 3\s*$", re.M)


def seconds(line):
    match = CLOCK.match(line)
    if not match:
        return None
    h, m, s = map(int, match.groups())
    return h*3600+m*60+s if h < 24 and m < 60 and s < 60 else None


def ingest(state, line, at):
    registration = REGISTER.match(line)
    if registration:
        state["active_round"] = int(registration[1])
        if state["active_round"] == 3:
            state["r3_anchor_found"] = True
    sale = SALE.match(line)
    if state.get("active_round") != 3 or not sale:
        return
    name, amount, ledger, unit, grade = sale.groups()
    if int(amount) <= 0 or int(ledger) <= 0:
        return
    row = {"ledger": int(ledger), "ts": at, "amount": int(amount), "unit": unit, "name": name,
           "grade": grade, "round": 3, "source": "local_acp_settled"}
    key = str(ledger)
    previous = state["records"].get(key)
    if previous and any(previous[k] != row[k] for k in ("unit", "amount", "grade")):
        state["conflicting_ledgers"] = sorted(set(state["conflicting_ledgers"]+[int(ledger)]))
    elif not previous or row["ts"] < previous["ts"]:
        state["records"][key] = row


def collect(md, now):
    """Snapshot only complete bytes; appends after fstat are processed next time."""
    md = Path(md)
    source = md.parent/"logs/market-kit.log"
    index_path = md/"local-settlements.json"
    try:
        state = read(index_path, {})
    except (OSError, ValueError, TypeError):
        state = {}
    with source.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        length = state.get("prefix_length")
        length = length if type(length) is int and 0 < length <= min(4096, stat.st_size) else min(4096, stat.st_size)
        prefix = stream.read(length)
        fingerprint = hashlib.sha256(prefix).hexdigest()
        offset = state.get("offset", 0)
        same = (state.get("schema") == 1 and state.get("source") == str(source.resolve())
                and state.get("prefix_sha256") == fingerprint and type(offset) is int and 0 <= offset <= stat.st_size)
        if same and offset:
            stream.seek(max(0, offset-128))
            same = hashlib.sha256(stream.read(min(128, offset))).hexdigest() == state.get("checkpoint_sha256")
        if not same:
            offset = 0
            preserve = state.get("schema") == 1 and state.get("source") == str(source.resolve())
            state = {"schema": 1, "source": str(source.resolve()),
                     "records": state.get("records", {}) if preserve else {},
                     "conflicting_ledgers": state.get("conflicting_ledgers", []) if preserve else [],
                     "active_round": state.get("active_round") if preserve else None,
                     "r3_anchor_found": state.get("r3_anchor_found", False) if preserve else False, "last_clock_at": None}
        stream.seek(offset)
        block = stream.read(stat.st_size-offset)
        end = block.rfind(b"\n")+1
        complete = block[:end]
        lines = complete.decode("utf8", errors="replace").splitlines()
        if offset == 0 or state.get("last_clock_at") is None:
            # Infer dates backwards from the stable file snapshot, then process forwards.
            anchor = ANCHOR.search(complete.decode("utf8", errors="replace"))
            if anchor:
                lines = complete.decode("utf8", errors="replace")[anchor.start():].splitlines()
            elif not state.get("r3_anchor_found"):
                lines = []
            stamp = datetime.fromtimestamp(stat.st_mtime, KST)
            day, previous_seconds = stamp.date(), stamp.hour*3600+stamp.minute*60+stamp.second
            dated = []
            for line in reversed(lines):
                value = seconds(line)
                if value is None:
                    continue
                if value > previous_seconds+43200:
                    day -= timedelta(days=1)
                previous_seconds = value
                at = datetime.combine(day, datetime.min.time(), KST).timestamp()+value
                dated.append((line, at))
            for line, at in reversed(dated):
                ingest(state, line, at)
                state["last_clock_at"] = at
        else:
            previous = state.get("last_clock_at")
            stamp = datetime.fromtimestamp(previous if previous is not None else stat.st_mtime, KST)
            day, previous_seconds = stamp.date(), stamp.hour*3600+stamp.minute*60+stamp.second
            for line in lines:
                value = seconds(line)
                if value is None:
                    continue
                if value < previous_seconds-43200:
                    day += timedelta(days=1)
                previous_seconds = value
                at = datetime.combine(day, datetime.min.time(), KST).timestamp()+value
                ingest(state, line, at)
                state["last_clock_at"] = at
        offset += end
        stream.seek(max(0, offset-128))
        checkpoint = hashlib.sha256(stream.read(min(128, offset))).hexdigest()
    state.update(offset=offset, prefix_length=length, prefix_sha256=fingerprint, checkpoint_sha256=checkpoint,
                 source_mtime=stat.st_mtime, collected_at=now)
    write(index_path, state)
    conflicts = set(state["conflicting_ledgers"])
    rows = sorted([r for r in state["records"].values() if r["ledger"] not in conflicts and r["ts"] <= now],
                  key=lambda r: (r["ts"], r["ledger"]))
    return {"ready": state["r3_anchor_found"] and state.get("active_round") == 3 and not conflicts, "rows": rows,
            "conflicting_ledgers": sorted(conflicts), "offset": offset, "source_size": stat.st_size,
            "source_age_seconds": now-stat.st_mtime, "rebuilt": not same, "unread_partial_bytes": len(block)-end}
