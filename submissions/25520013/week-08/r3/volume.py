"""Settled-trade counter for the min-volume guard (v1.3). The midterm BASE needs >= 20 settled Round 3 trades for
our shop AND >= 20 for our buyer; policy.volume_relax() turns the count into a relax level.

Counted from events.jsonl incrementally (own byte cursor in <R3_STATE>/volume.json, O(new events)):
- shop:  settle events from the market ledger whose `shop` is our shop name (R3_SHOP_NAME) or one of our
         runtime-resolved shop URLs (owner.py cache). (Kit-log "[acp] settled" lines are only archived, not
         events, so they are not counted here.)
- buyer: settle events from deal.json / payments.jsonl (src deal|payments, ours by construction) or market settles
         whose `buyer` is R3_BUYER_NAME.
One trade is counted once: distinct market ledger numbers (else the event key).
Read-only on events.jsonl; no lock needed (a half-written last line is read next time).
"""
import json
import os
from pathlib import Path

from ledger import Ledger, own_owner_names
from owner import norm_name

KEEP_IDS = 5000


def _ids(e):
    d = e.get("data") or {}
    led = d.get("ledger")
    return f"L{led}" if led is not None else e.get("key")


def side_of(e, me_shop, me_buyer, own_urls):
    if e.get("type") != "settle":
        return None
    d, src = e.get("data") or {}, e.get("src")
    if src in ("deal", "payments"):
        return "buyer"
    if src == "market":
        if me_buyer and norm_name(d.get("buyer")) == me_buyer:
            return "buyer"
        s = norm_name(d.get("shop"))
        if (me_shop and s == me_shop) or s in own_urls:
            return "shop"
    return None


def settled_counts(root=None):
    """-> {"shop": n, "buyer": n}; updates the cursor file."""
    L = Ledger(root)
    p = L.root / "volume.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st.setdefault("off", 0)
    st.setdefault("shop", {"n": 0, "ids": []})
    st.setdefault("buyer", {"n": 0, "ids": []})
    me_shop = norm_name(L.me_shop) or None
    me_buyer = norm_name(L.me_buyer) or None
    own_urls = {x for x in own_owner_names(state=L.root) if "." in x or "/" in x}   # v1.4 URL keys (norm_name maps '.' to '-')
    seen = {k: set(st[k]["ids"]) for k in ("shop", "buyer")}
    moved = False
    for off, e in L.iter_from(st["off"]):
        st["off"], moved = off, True
        side = side_of(e, me_shop, me_buyer, own_urls)
        if side is None:
            continue
        i = _ids(e)
        if i in seen[side]:
            continue
        seen[side].add(i)
        st[side]["ids"].append(i)
        st[side]["ids"] = st[side]["ids"][-KEEP_IDS:]
        st[side]["n"] += 1
    if moved:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, p)
    return {"shop": st["shop"]["n"], "buyer": st["buyer"]["n"]}
