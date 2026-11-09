"""Trajectories (design 17-1, R5): one record per trade, from the first event to the after-effects.

    py -m harness.trajectory build 20261105            # writes memory/harness/trajectory/<day>.jsonl
    py -m harness.trajectory get o-158c5891f32e 20261105
    py -m harness.trajectory coverage 20261105         # how many trades are complete (the T1 check, design 20-5)

IDs: buyer = order id, seller = task id, restock = the restock.plan event that bought (one per supply window).
Built only from the raw events (memory/raw/), which the collector already joins to the ledger: seller.settled
carries task, unit and ledger_seq; buyer.payment and buyer.received carry the order. Nothing is read twice from the
market. Tool calls are attached by process: market_buyer and restock run one trade per process, so before T1 their
tool calls join by pid; the seller serves many tasks in one process, so its tool calls join only through the keys
T1 adds (registry.call stamps them).
"""
import json
import os
import sys
import time
from collections import defaultdict

from core import paths
from core.causes import cause  # C7, shared with tools.registry
from harness import out_dir

ROLE_SOURCES = ("buyer", "seller", "review", "restock", "forum", "outer", "tools", "models")

def pid_of(ev):
    """The writing process, from event_id writer-pid-ms-seq (the writer name may itself hold dashes)."""
    parts = ev["event_id"].rsplit("-", 3)
    return parts[1] if len(parts) == 4 else None


UNPAID_AFTER_S = 2 * 3600       # a seller deal with no settle line this long after closing counts as unpaid


def next_day(day):
    return time.strftime("%Y%m%d", time.localtime(time.mktime(time.strptime(day, "%Y%m%d")) + 36 * 3600))


def read_day(day, days=1):
    """All raw events of `days` days from day (YYYYMMDD), in time order."""
    evs, wanted = [], [day]
    while len(wanted) < days:
        wanted.append(next_day(wanted[-1]))
    for src in ROLE_SOURCES:
        for f in sorted(f for d in wanted for f in (paths.raw() / src).glob(f"*-{d}.jsonl")):
            for line in _lines(f):
                try:
                    evs.append(json.loads(line))
                except ValueError:
                    continue                # a half-written last line
    evs.sort(key=lambda e: e["ts"])
    return evs


def _lines(f):
    """The file's lines. Appenders lock one byte while they write (core.atomic); a writer that never released its
    lock made every read fail (11/9 00:30-09:20), so a locked file is read piece by piece and the locked 1 KB piece
    is skipped (the lines it cuts are dropped as broken JSON)."""
    try:
        with open(f, encoding="utf-8") as fh:
            return fh.readlines()
    except PermissionError:
        out = []
        with open(f, "rb", buffering=0) as fh:
            size = os.fstat(fh.fileno()).st_size
            for pos in range(0, size, 1024):
                fh.seek(pos)
                try:
                    out.append(fh.read(1024))
                except PermissionError:
                    out.append(b"\n")
        return b"".join(out).decode("utf-8", "replace").splitlines(True)


def _new(tid, role, ev):
    return {"id": tid, "role": role, "start": ev["ts"], "end": ev["ts"],
            "versions": {"strategy": ev.get("strategy_version"), "card": ev.get("card_version"),
                         "tools": (ev.get("data") or {}).get("tool_version")},
            "experiment": None, "input": {}, "knowledge_used": [], "steps": [], "result": {}, "after": {},
            "counterpart": {}, "cost": {"model_calls": 0, "model_s": 0.0}}


def _step(t, ev, **extra):
    d = ev.get("data") or {}
    t["steps"].append({"ts": round(ev["ts"], 3), "kind": ev["kind"], **extra,
                       **{k: d[k] for k in ("action", "price", "unit", "shop", "sku", "tool", "ok", "ms", "reason",
                                            "summary") if k in d}})
    t["end"] = max(t["end"], ev["ts"])


def build(day):
    """{id: trajectory} for the trades that started on this day; the next day's events add their after-effects."""
    evs = read_day(day, days=2)
    start_of_next = time.mktime(time.strptime(next_day(day), "%Y%m%d"))
    tr = {}
    cur = {}                               # (writer, pid) -> the trade that process started last (they run in turn)
    by_seq = {}                            # ledger_seq -> seller task (refunds name only the ledger line)

    def get(tid, role, ev):
        if tid not in tr:
            tr[tid] = _new(tid, role, ev)
        return tr[tid]

    for ev in evs:
        k, d, kind = ev.get("keys") or {}, ev.get("data") or {}, ev["kind"]
        order, task = k.get("order"), k.get("task")
        # ---- buyer: order id
        if kind == "buyer.order" and order:
            t = get(order, "buyer", ev)
            t["input"] = {k2: d.get(k2) for k2 in ("category", "budget", "kind", "use_within_hours", "acceptable_n")}
            t["experiment"] = d.get("experiment")
            cur[("buyer", pid_of(ev))] = order
        elif kind == "buyer.pick" and order:
            t = get(order, "buyer", ev)
            _step(t, ev)
            if d.get("why"):
                t["knowledge_used"].append({"what": d["why"], "about": k.get("counterpart"), "grade": "observed"})
        elif kind == "buyer.decision" and order:
            _step(get(order, "buyer", ev), ev, by=d.get("thought") or ("rules" if d.get("ms", 0) < 50 else "model"))
        elif kind == "buyer.closed" and order:
            t = get(order, "buyer", ev)
            _step(t, ev, outcome=d.get("outcome"))
            t["result"] = {"outcome": d.get("outcome"), "verdict": d.get("verdict"), "agreed": d.get("agreed"),
                           "receipt_price": d.get("receipt_price"), "list_price": d.get("list_price"),
                           "cause": None if d.get("verdict") == "fair" else cause(d.get("verdict") or d.get("outcome"))}
            if k.get("counterpart"):
                t["counterpart"] = {"shop": k["counterpart"], "sure": True}
        elif kind == "buyer.payment" and order:
            t = get(order, "buyer", ev)
            t["result"].update(paid=d.get("paid"), paid_price=d.get("price"), ledger=d.get("ledger"),
                               pay_cause=None if d.get("paid") else cause(d.get("reason")), pay_reason=d.get("reason"))
            t["end"] = max(t["end"], ev["ts"])
        elif kind == "buyer.received" and order:
            get(order, "buyer", ev)["after"].update(value=d.get("value"), surplus=d.get("surplus"),
                                                     grade=d.get("grade"), claim_honest=d.get("claim_honest"))
        elif kind == "review.written" and order:
            get(order, "buyer", ev)["after"].update(our_stars=d.get("stars"), fact_stars=d.get("fact_stars"))
        elif kind == "buyer.broken" and order:
            get(order, "buyer", ev)["after"]["broken_at"] = d.get("broke_at")
        # ---- seller: task id
        elif kind == "seller.request" and task:
            t = get(task, "seller", ev)
            if not t["input"]:
                t["input"] = {"first_message": (d.get("excerpt") or "")[:200],
                              "looks_like_instruction": d.get("looks_like_instruction")}
        elif kind in ("seller.decision", "seller.missed") and task:
            _step(get(task, "seller", ev), ev, by=d.get("thought"))
        elif kind == "seller.arm" and task:
            get(task, "seller", ev)["experiment"] = d.get("experiment")
        elif kind == "guess.made" and task:
            get(task, "seller", ev)["counterpart"] = {"guess": d.get("top"), "sure": False}
        elif kind == "seller.closed" and task:
            t = get(task, "seller", ev)
            _step(t, ev, outcome=d.get("outcome"))
            t["result"] = {"outcome": d.get("outcome"), "price": d.get("price"), "cost": d.get("cost"),
                           "sku": d.get("sku"), "unit": k.get("unit"), "claim": d.get("claim"),
                           "warranty_hours": d.get("warranty_hours"), "turns": d.get("turns"),
                           "cause": None if d.get("outcome") == "completed" else cause(d.get("outcome"))}
        elif kind == "seller.settled" and task:
            t = get(task, "seller", ev)
            t["result"].update(settled=True, ledger=k.get("ledger_seq"), settled_price=d.get("price"))
            t["after"]["stars"] = d.get("stars")
            if k.get("counterpart"):
                t["counterpart"] = {**t["counterpart"], "buyer": k["counterpart"], "sure": True}
            by_seq[k.get("ledger_seq")] = task
            t["end"] = max(t["end"], ev["ts"])
        elif kind == "seller.refund":
            tid = by_seq.get(k.get("ledger_seq"))
            if tid:
                tr[tid]["after"]["refund"] = d.get("amount")
        # ---- restock: the plan that bought
        elif kind == "restock.plan" and d.get("lines"):
            t = get(ev["event_id"], "restock", ev)
            t["input"] = {"window": d.get("window"), "used_model": d.get("used_model")}
            t["result"] = {"lines": d.get("lines"), "plan": d.get("plan"), "refused": d.get("refused")}
            cur[("restock", pid_of(ev))] = ev["event_id"]
        # ---- tool calls: T1 keys first, else the one-trade process
        elif ev["source"] == "tools" and kind == "tool.result":
            tid = order or task or k.get("window")
            if tid is None:
                tid = cur.get((ev["writer"], pid_of(ev)))
            if tid in tr:
                _step(tr[tid], ev, tool=d.get("tool"), cause=None if d.get("ok") else cause(d.get("reason")))
        elif kind == "model.call":
            tid = order or task
            if tid in tr:
                tr[tid]["cost"]["model_calls"] += 1
                tr[tid]["cost"]["model_s"] += round(d.get("seconds") or d.get("s") or 0, 2)
    last = evs[-1]["ts"] if evs else 0
    for t in tr.values():                  # a completed sale with no settle line long after: the buyer did not pay
        r = t["result"]
        if t["role"] == "seller" and r.get("outcome") == "completed" and not r.get("settled")                 and last - t["end"] > UNPAID_AFTER_S:
            r.update(settled=False, cause="counterpart", pay_reason="no settle line within 2 h")
        elif t["role"] == "seller" and not r.get("outcome") and last - t["end"] > UNPAID_AFTER_S:
            r.update(outcome="abandoned", cause="negotiation")   # the buyer went quiet before either side closed
    return {i: t for i, t in tr.items() if t["start"] < start_of_next}


def complete(t):
    """Whether a trajectory runs from its start to its after-effects (17-1)."""
    r, a = t["result"], t["after"]
    if t["role"] == "buyer":
        if not r.get("outcome"):
            return False
        if r.get("agreed") is None or r.get("cause"):
            return True                        # no agreement, or refused before paying: nothing comes after
        return r.get("paid") is not None and (not r.get("paid") or "our_stars" in a)
    if t["role"] == "seller":
        if not r.get("outcome"):
            return False
        return r.get("outcome") != "completed" or r.get("settled") is not None
    return bool(r)


def coverage(trs):
    """Per role: trades, complete share, trades with tool steps, failure causes."""
    out = {}
    for role in ("buyer", "seller", "restock"):
        ts = [t for t in trs.values() if t["role"] == role]
        causes = defaultdict(int)
        for t in ts:
            causes[t["result"].get("cause") or t["result"].get("pay_cause") or "none"] += 1
        out[role] = {"trades": len(ts), "complete": round(sum(map(complete, ts)) / len(ts), 3) if ts else None,
                     "with_tool_steps": sum(any(s.get("tool") for s in t["steps"]) for t in ts),
                     "causes": dict(sorted(causes.items(), key=lambda x: -x[1]))}
    return out


def write(day, trs):
    d = out_dir() / "trajectory"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{day}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for t in sorted(trs.values(), key=lambda t: t["start"]):
            f.write(json.dumps(t, ensure_ascii=False, separators=(",", ":")) + "\n")
    return p


def get_one(tid, day):
    """trajectory.get: one trade by its id (from the built file if there is one, else built now)."""
    p = out_dir() / "trajectory" / f"{day}.jsonl"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for line in f:
                if f'"id":"{tid}"' in line:
                    return json.loads(line)
        return None
    return build(day).get(tid)


def search(day, role=None, cause_=None, counterpart=None, limit=20):
    """trajectory.search: trades matching a role, a failure cause and/or a counterpart."""
    out = []
    for t in build(day).values():
        if role and t["role"] != role:
            continue
        if cause_ and cause_ not in (t["result"].get("cause"), t["result"].get("pay_cause")):
            continue
        if counterpart and counterpart not in json.dumps(t["counterpart"], ensure_ascii=False):
            continue
        out.append(t)
        if len(out) >= limit:
            break
    return out


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["coverage"]
    if cmd == "build":
        trs = build(rest[0])
        print(write(rest[0], trs), len(trs))
    elif cmd == "get":
        print(json.dumps(get_one(rest[0], rest[1]), ensure_ascii=False, indent=1))
    elif cmd == "coverage":
        print(json.dumps(coverage(build(rest[0])), ensure_ascii=False, indent=1))
