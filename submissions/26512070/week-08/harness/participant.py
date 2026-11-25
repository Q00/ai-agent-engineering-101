"""Participant knowledge (design 20-6, D-9, W07): what we know of each other player, keyed by GitHub login.

    py -m harness.participant              # rebuild from the last 24 h of trajectories -> knowledge items
    py -m harness.participant get inextro  # participant.get

The board names every shop and buyer with its owner's login, so a shop name our buyer met and a buyer name our shop
sold to both resolve to one login (names change, logins do not - W07 LAB 1). Two sides per login:
- as_shop: what our buyer saw - talks, agreed, fair, unsigned receipts, cheated, paid / unpaid, surplus, the price
  paid as a share of list;
- as_buyer: what our shop saw - sales, revenue, the stars it gave us, refunds, what it buys.
Each side is one observed knowledge item (source data:trajectory, sample n = trades), valid 24 h (D-7). The inner
runtime's own fast facts (core/reflex.py: blocks, payment results) stay where they are; me.counterpart shows both.
"""
import json
import sys
import time
from collections import Counter, defaultdict

from core import atomic
from harness import knowledge, out_dir, trajectory
from outer.position import snapshots

WINDOW_S = 24 * 3600
METHOD = "harness.participant v1: counts over our trajectories of the last 24 h"


def logins(board):
    """{shop or buyer name: (login, is_student)} from a board snapshot."""
    students = {p.get("login") for p in board.get("players") or [] if p.get("student")}
    out = {}
    for r in (board.get("shops") or []) + (board.get("buyers") or []):
        if r.get("name") and r.get("login"):
            out[r["name"]] = (r["login"], r["login"] in students)
    return out


def build(now=None):
    """{login: {"as_shop": {...}, "as_buyer": {...}, "student": bool, "names": [...]}} over the last 24 h."""
    now = now or time.time()
    snaps = snapshots(now - 3 * 3600)
    names = logins(snaps[-1]) if snaps else {}
    days = sorted({time.strftime("%Y%m%d", time.localtime(now - WINDOW_S)), time.strftime("%Y%m%d", time.localtime(now))})
    trs = [t for d in days for t in trajectory.build(d).values() if t["start"] >= now - WINDOW_S]
    out = defaultdict(lambda: {"as_shop": Counter(), "as_buyer": Counter(), "names": set(), "student": None,
                               "_share": [], "_stars": [], "_skus": Counter()})

    def who(name):
        login, student = names.get(name, (f"name:{name}", None))   # unknown to the board: keep the name, marked
        p = out[login]
        p["names"].add(name)
        p["student"] = student
        return p

    for t in trs:
        r, a, c = t["result"], t["after"], t["counterpart"]
        if t["role"] == "buyer" and c.get("shop"):
            s = who(c["shop"])["as_shop"]
            s["talks"] += 1
            v = str(r.get("verdict") or "")
            s["agreed"] += bool(r.get("agreed"))
            s["fair"] += v == "fair"
            s["unsigned"] += "sig_seller" in v
            s["cheated"] += v.startswith("cheated")
            if r.get("paid") is not None:
                s["paid" if r["paid"] else "unpaid"] += 1
            s["surplus"] += a.get("surplus") or 0
            if r.get("receipt_price") and r.get("list_price"):
                out[names.get(c["shop"], (f"name:{c['shop']}",))[0]]["_share"].append(r["receipt_price"] / r["list_price"])
        elif t["role"] == "seller" and c.get("buyer") and r.get("settled"):
            p = who(c["buyer"])
            b = p["as_buyer"]
            b["sales"] += 1
            b["revenue"] += r.get("settled_price") or 0
            b["refunds"] += 1 if a.get("refund") is not None else 0
            if isinstance(a.get("stars"), (int, float)):
                p["_stars"].append(a["stars"])
            if r.get("sku"):
                p["_skus"][r["sku"]] += 1
    res = {}
    for login, p in out.items():
        shop, buyer = dict(p["as_shop"]), dict(p["as_buyer"])
        if p["_share"]:
            shop["price_share_mean"] = round(sum(p["_share"]) / len(p["_share"]), 3)
        if p["_stars"]:
            buyer["stars_mean"] = round(sum(p["_stars"]) / len(p["_stars"]), 2)
            buyer["low_stars"] = sum(1 for x in p["_stars"] if x <= 3)
        if p["_skus"]:
            buyer["top_skus"] = [k for k, _ in p["_skus"].most_common(3)]
        res[login] = {"as_shop": shop, "as_buyer": buyer, "student": p["student"], "names": sorted(p["names"])}
    return res, (now - WINDOW_S, now)


def to_knowledge(parts, span, now=None):
    """One observed knowledge item per login and side."""
    items = []
    for login, p in parts.items():
        for side, n_key in (("as_shop", "talks"), ("as_buyer", "sales")):
            d = p[side]
            if not d.get(n_key):
                continue
            items.append({"id": f"participant:{login}:{side}", "about": f"participant:{login}", "side": side,
                          "claim": json.dumps(d, ensure_ascii=False, sort_keys=True), "data": d,
                          "student": p["student"], "names": p["names"], "source": "data:trajectory",
                          "method": METHOD, "sample": {"since": span[0], "until": span[1], "n": d[n_key]}})
    written = knowledge.write_many(items, now)
    # the current items only, for the trading side's reads (me.counterpart on every candidate shop): the log above
    # only grows, this file stays one line per login and side
    cur = {"by_login": {}, "by_name": {}}
    for it in written:
        login = it["about"].split(":", 1)[1]
        cur["by_login"].setdefault(login, {})[it["side"]] = it
        for n in it.get("names") or []:
            cur["by_name"][n] = login
    atomic.write_json(current_path(), cur)
    return written


def current_path():
    return out_dir() / "participants.json"


def get(login_or_name, now=None):
    """participant.get: the current knowledge about a login (or a shop / buyer name we have seen), expired dropped."""
    now = now or time.time()
    cur = atomic.read_json(current_path(), default=None) or {}
    login = login_or_name if login_or_name in (cur.get("by_login") or {}) else (cur.get("by_name") or {}).get(login_or_name)
    items = [i for i in ((cur.get("by_login") or {}).get(login) or {}).values()
             if not (i.get("expires") and i["expires"] < now)]
    return {i["side"]: {"data": i["data"], "grade": i["grade"], "n": i["sample"]["n"], "made": i["made"],
                        "student": i.get("student"), "login": i["about"].split(":", 1)[1]} for i in items}


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "get":
        print(json.dumps(get(sys.argv[2]), ensure_ascii=False, indent=1))
    else:
        parts, span = build()
        items = to_knowledge(parts, span)
        print(len(parts), "participants,", len(items), "items")
