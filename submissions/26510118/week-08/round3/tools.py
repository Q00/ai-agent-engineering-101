"""The analysis tools, served to the analysis models by the data room (data_room.py, an MCP server).

    ledger_query   counts from the public ledger for one category, SKU or shop
    replay         what a rule change would have done to the last hours of our own deals (below)
    shop_credit    one shop's record: the ledger, our buyer's notebook, its reviews
    leave_memo     a memo for the other analyses (part 4 of their next briefing)

What is small and always needed goes into the briefing (context.py); what is large and needed
in parts is fetched through these. The numbers are counted by code, so a model cannot make one
up. Limits held here and in the host: read-only except leave_memo (at most MEMO_MAX a run, to
another analysis), every answer cut to LIMIT characters, at most MAX_CALLS calls an analysis.

replay runs our own rule over our own records. For a shop field it gives every recorded
negotiation the first ask (and, with a warranty, the floor) the new rule would have given, and
answers the buyer's recorded offers, one by one, with the rule the shop answers with
(pricing.respond): each buyer is assumed to make the same offers and to leave where it left. Run
with the rules in force it should give back what happened; how often it does is reported, so the
model can see how far to trust it. For buyer.max_ratio: a deal above the new ceiling is lost, and
an order we left while the shop's last ask was under the new ceiling would have closed at that ask.
"""
import json
import time

import buyer_memory
import common
import forum
import ledger
import pricing

MAX_CALLS = 4            # a role analysis; the integrated analysis gets INTEGRATED_CALLS
INTEGRATED_CALLS = 3
LIMIT = 1500
MIN_SAMPLE = 20          # fewer replayed deals than this: the answer says the evidence is weak
REPLAYABLE = ("seller.price_adjust", "seller.warranty_extra", "buyer.max_ratio")
MEMO_TO = ("seller", "buyer", "integrated", "all")
MEMO_ACTS = ("inform", "propose", "request")      # week 4's performatives, the three a memo needs
MEMO_MAX = 2
MEMO_TEXT = 300


def ledger_query(category: str | None = None, sku: str | None = None, shop: str | None = None,
                 hours: float = 24, now: float | None = None) -> dict:
    now = now or time.time()
    hours = max(1, min(48, float(hours or 24)))      # rows over 48 h are archived (ledger.KEEP)
    catalog = common.catalog()
    since = now - hours * 3600
    leaf = lambda s: (catalog.get(s) or {}).get("category", "").split(">")[-1]
    named = ledger.names()                                      # a settle names its shop by address
    name_of = lambda row: named.get(row.get("shop"), row.get("shop"))

    def wanted(row):
        if sku:
            return row.get("item") == sku
        if shop:
            return shop in (row.get("shop"), name_of(row))
        if category:
            full = (catalog.get(row.get("item")) or {}).get("category", "")
            return category in (full, leaf(row.get("item")))
        return True
    settles = [s for s in common.read_jsonl(ledger.folder() / "settles.jsonl") if (s.get("ts") or 0) >= since and wanted(s)]
    broken = [b for b in common.read_jsonl(ledger.folder() / "broken.jsonl") if (b.get("ts") or 0) >= since and wanted(b)]
    ratios = [s["amount"] / catalog[s["item"]]["list_price"] for s in settles
              if isinstance(s.get("amount"), (int, float)) and (catalog.get(s.get("item")) or {}).get("list_price")]
    grades, shops = {}, {}
    for s in settles:
        grades[s.get("grade")] = grades.get(s.get("grade"), 0) + 1
        if s.get("shop"):
            shops[name_of(s)] = shops.get(name_of(s), 0) + 1
    claims = [s for s in settles if s.get("claim")]
    return {"hours": hours, "of": {"category": category, "sku": sku, "shop": shop}, "settled": len(settles),
            "avg_price_share": round(sum(ratios) / len(ratios), 3) if ratios else None, "grades": grades,
            "claims": len(claims), "claims_true": sum(1 for s in claims if s.get("claim_honest") is True),
            "broken": len(broken), "top_shops": dict(sorted(shops.items(), key=lambda kv: -kv[1])[:5])}


def _shop_replay(n: dict, d_adjust: float, d_extra: dict):
    """The price one recorded negotiation would have closed at, or None."""
    lp, ask, floor = n["list_price"], n["asks"][0], n["floor"]
    if n.get("warranty") and d_extra:
        bump = round(lp * d_extra.get(n.get("grade"), 0.0))
        ask, floor = ask + bump, floor + bump
    ask = max(floor, ask + round(lp * d_adjust))
    offers = n.get("offers") or ([n["price"]] if n.get("outcome") == "deal" else [])   # "yes" to the ask
    for i, offer in enumerate(offers, 1):
        what, price = pricing.respond(ask, floor, offer, i)
        if what == "sell":
            return price
        if what == "reject":
            return None
        ask = price
    return None


def _sums(rows: list) -> dict:
    """rows: (price or None, cost or list price) -> deals and the money they made."""
    done = [(p, c) for p, c in rows if p is not None]
    return {"deals": len(done), "revenue": sum(p for p, _ in done), "margin": sum(p - c for p, c in done)}


def replay(field: str, value, hours: float = 24, now: float | None = None) -> dict:
    import analysts                                               # analysts imports this module
    now = now or time.time()
    hours = max(1, min(48, float(hours or 24)))      # rows over 48 h are archived (ledger.KEEP)
    since = now - hours * 3600
    if field == "buyer.open_ratio":
        return {"error": "buyer.open_ratio cannot be replayed: how shops answer a lower first offer is not in our records"}
    if field not in REPLAYABLE:
        return {"error": f"replay covers {', '.join(REPLAYABLE)}"}
    role, _, name = field.partition(".")
    ok, v = analysts.validate(role, name, value)
    if not ok:
        return {"error": f"{field} = {value!r}: {v}"}
    current = common.rules()[role][name]
    out = {"field": field, "from": current, "to": v, "hours": hours}
    if role == "seller":
        negs = [n for n in common.records("negotiations") if (n.get("ts") or 0) >= since and n.get("asks")
                and n.get("floor") and n.get("list_price")]
        d_adjust = v - current if name == "price_adjust" else 0.0
        d_extra = {g: v.get(g, current.get(g, 0.0)) - current.get(g, 0.0) for g in "ABC"} if name == "warranty_extra" else {}
        recorded = [(n.get("price") if n.get("outcome") == "deal" else None) for n in negs]
        same = sum(1 for n, r in zip(negs, recorded) if _shop_replay(n, 0.0, {}) == r)
        out.update(assumption="each buyer makes the same offers in the same order and leaves where it left; "
                              "the shop answers with its own rule (pricing.respond)",
                   negotiations=len(negs), check=f"with the rules in force the replay gives back {same} of {len(negs)} recorded outcomes",
                   recorded=_sums([(r, n.get("cost") or 0) for n, r in zip(negs, recorded)]),
                   replayed=_sums([(_shop_replay(n, d_adjust, d_extra), n.get("cost") or 0) for n in negs]))
    else:
        orders = [o for o in common.records("orders") if (o.get("ts") or 0) >= since and o.get("list_price") and o.get("candidates")]
        rows_then, rows_now = [], []
        for o in orders:
            lp, budget = o["list_price"], o.get("budget") or float("inf")
            cap_then, cap_now = min(budget, round(lp * current)), min(budget, round(lp * v))
            last = (o.get("asks") or [None])[-1]
            then = o.get("price") if o.get("deal") else None
            if then is not None:
                new = then if then <= cap_now else None
            else:
                new = last if last is not None and cap_then < last <= cap_now else None
            rows_then.append((then, lp))
            rows_now.append((new, lp))
        flip = lambda s: {"deals": s["deals"], "spent": s["revenue"], "surplus": -s["margin"]}   # list price - price paid
        out.update(assumption="each shop asks what it asked; a deal above the new ceiling is lost; an order we left "
                              "with the shop's last ask under the new ceiling would have closed at that ask",
                   orders=len(orders), recorded=flip(_sums(rows_then)), replayed=flip(_sums(rows_now)))
    n = out["recorded"]["deals"]
    if n < MIN_SAMPLE:
        out["weak"] = f"only {n} recorded deals in {hours:g} h: weak evidence"
    return out


def shop_credit(shop: str) -> dict:
    """One shop's record, by its name or address: the public ledger, our buyer's notebook, its reviews."""
    listing = next((s for s in common.market_file("shops.json", []) or [] if shop in (s.get("name"), s.get("url"))), {})
    keys = {k for k in (shop, listing.get("name"), listing.get("url")) if k}
    settles = [s for s in common.read_jsonl(ledger.folder() / "settles.jsonl") if s.get("shop") in keys]
    broken = [b for b in common.read_jsonl(ledger.folder() / "broken.jsonl") if b.get("shop") in keys]
    claims = [s for s in settles if s.get("claim")]
    summary = common.market_file("market_summary.json", {}) or {}
    nb = buyer_memory.load()
    name = next((k for k in keys if k in nb), shop)
    e = nb.get(name) or {}
    ratios = e.get("ratios") or []
    stars = [r["stars"] for r in listing.get("reviews") or [] if isinstance(r, dict) and isinstance(r.get("stars"), (int, float))]
    return {"shop": shop, "our_own_shop": any(forum.own(k) for k in keys),
            "ledger": {"settled": len(settles), "grade_claims": len(claims),
                       "claims_true": sum(1 for s in claims if s.get("claim_honest") is True), "broke_after_sale": len(broken)},
            "ledger_24h": next(((summary.get("by_shop") or {}).get(k) for k in keys if (summary.get("by_shop") or {}).get(k)), None),
            "our_buyer": {"recent": e.get("recent", [])[-5:], "settled": e.get("settled", 0), "unpaid": e.get("unpaid", 0),
                          "false_claims": e.get("false_claims", 0), "broken": e.get("broken", 0),
                          "broken_no_warranty": e.get("broken_no_warranty", 0),
                          "avg_price_share": round(sum(ratios) / len(ratios), 3) if ratios else None} if e else None,
            "trust": buyer_memory.trust(nb, name, summary),
            "reviews": {"count": len(stars), "avg_stars": round(sum(stars) / len(stars), 2)} if stars else None}


def leave_memo(role: str, to: str, act: str, text: str, evidence: str = "", written: int = 0,
               now: float | None = None) -> dict:
    """Append one memo to analysis/memos.jsonl, from `role` (set by the host, not by the model)."""
    text, evidence = str(text or "").strip(), str(evidence or "").strip()
    if written >= MEMO_MAX:
        return {"error": f"at most {MEMO_MAX} memos per analysis"}
    if to not in MEMO_TO or to == role:
        return {"error": f"to must be one of {', '.join(t for t in MEMO_TO if t != role)}"}
    if act not in MEMO_ACTS:
        return {"error": f"act must be one of {', '.join(MEMO_ACTS)}"}
    if not 1 <= len(text) <= MEMO_TEXT or len(evidence) > 200:
        return {"error": f"text must be 1 to {MEMO_TEXT} characters, evidence at most 200"}
    row = {"ts": int(now or time.time()), "from": role, "to": to, "act": act, "text": text, "evidence": evidence}
    folder = common.MARKET_DIR / "analysis"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "memos.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"saved": row}


def run(name: str, args: dict, role: str = "integrated", state: dict | None = None) -> str:
    """One tool call, as JSON text cut to LIMIT. state: the caller's {"memos": n} for this analysis."""
    args = args or {}
    pick = lambda *keys: {k: v for k, v in args.items() if k in keys}
    try:
        if name == "ledger_query":
            out = ledger_query(**pick("category", "sku", "shop", "hours"))
        elif name == "replay":
            out = replay(**pick("field", "value", "hours"))
        elif name == "shop_credit":
            out = shop_credit(**pick("shop"))
        elif name == "leave_memo":
            state = state if state is not None else {}
            out = leave_memo(role, written=state.get("memos", 0), **pick("to", "act", "text", "evidence"))
            if "saved" in out:
                state["memos"] = state.get("memos", 0) + 1
        else:
            out = {"error": f"no tool {name}"}
    except (TypeError, ValueError, KeyError) as e:
        out = {"error": f"{type(e).__name__}: {e}"}
    return json.dumps(out, ensure_ascii=False)[:LIMIT]
