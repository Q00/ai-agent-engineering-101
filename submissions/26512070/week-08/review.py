"""Our review, the kit's hook (Round 3): "review": {"cmd": "py review.py --deal {deal}"}.

After the kit paid for our buyer's deal it writes .market/deal.json {order, receipt, settled} and reads
the last JSON line {"stars": 1-5, "review": "..."}. calc.fact_stars gives the baseline from the facts;
the model may differ for a strategic reason, which act.review requires it to state (D30). This is also
where our buyer learns what the purchase was worth, so it records buyer.received.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agents import loop  # noqa: E402
from core import card  # noqa: E402
from core import reflex  # noqa: E402
from core.events import EventWriter  # noqa: E402
from tools import market_files as mf  # noqa: E402
from tools.registry import Ctx, call  # noqa: E402

DEADLINE = 60


def shop_of(order_id):
    """The shop our buyer bought from on this order, from the reflex record (the deal file has no shop name)."""
    for name, s in reflex.load()["shops"].items():
        if any(x.get("order") == order_id and str(x.get("verdict", "")).startswith("fair") for x in s.get("last", [])):
            return name
    return None


def worth(ctx, deal):
    o, mr, r = deal.get("order") or {}, deal.get("settled") or {}, deal.get("receipt") or {}
    c = mf.catalog(ctx).get(mr.get("item") or r.get("item")) or {}
    value = c.get("list_price")
    if mr.get("expires_at") and o.get("use_within_hours"):
        if (mr["expires_at"] - time.time()) / 3600 < o["use_within_hours"]:
            value = 0                                   # it will spoil before the order needs it
    return value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    a = ap.parse_args()
    deal = json.loads(Path(a.deal).read_text(encoding="utf-8"))
    tool_w, w = EventWriter("review", source="tools"), EventWriter("review", source="review")
    ctx = Ctx(caller="review", _writer=tool_w)
    try:
        w.context(strategy_version=ctx.strategy.get("version"), epoch=ctx.strategy.get("epoch"),
                  card_version=card.load()[0])
        o, mr, r = deal.get("order") or {}, deal.get("settled") or {}, deal.get("receipt") or {}
        ctx.keys = {"order": o.get("order")}                    # C1
        value = worth(ctx, deal)
        settled = dict(mr, value=value)
        w.emit("buyer.received", {"value": value, "price": r.get("price"), "grade": mr.get("grade"),
                                  "name": mr.get("name"), "sku": mr.get("item") or r.get("item"),
                                  "claim_honest": mr.get("claim_honest"),
                                  "surplus": (value or 0) - (r.get("price") or 0)},
               order=o.get("order"), **({"ledger_seq": mr["ledger"]} if isinstance(mr.get("ledger"), int) else {}))
        fact = call(ctx, "calc.fact_stars", {"deal": dict(deal, settled=settled),
                                             "agreed_price": r.get("price")})["result"]
        shop = shop_of(o.get("order"))
        ctx.situation = {"fact_stars": fact["stars"]}
        text = ("# the decision at hand\n"
                f"Our buyer paid {r.get('price')} for {mr.get('name') or r.get('item')} from {shop or 'a shop'} "
                f"(order {o.get('order')}, ledger #{mr.get('ledger')}). Got grade {mr.get('grade')}"
                + (f", the shop claimed {mr.get('claim')} ({'true' if mr.get('claim_honest') else 'not true'})"
                   if mr.get("claim") else "")
                + f". Worth to the order: {value}. Fact baseline: {fact['stars']} stars ({'; '.join(fact['reasons'])}).")
        if shop:
            known = call(ctx, "me.counterpart", {"name": shop, "side": "shop"})["result"]
            text += f"\nWhat we know of this shop: {json.dumps(known, ensure_ascii=False, default=str)[:800]}"
        rules = ((ctx.strategy.get("playbook") or {}).get("review") or {}).get("mode") == "rules"
        d = ({"stars": fact["stars"], "text": f"Paid {r.get('price')}; " + "; ".join(fact["reasons"]) + ".",
              "why": "rules"} if rules else None)            # 11/4: the baseline as it is, no model call
        if d is not None:                                    # C10: the rules' review is on the record like the model's
            rv = call(ctx, "act.review", {"stars": d["stars"], "text": d["text"], "why": "rules"})
            if rv["ok"]:
                d["text"] = rv["result"]["text"]
        if d is None:
            d, _ = loop.decide("review", ctx, text, deadline=time.time() + DEADLINE)
        if d is None:
            d = {"stars": fact["stars"], "text": f"Paid {r.get('price')}; " + "; ".join(fact["reasons"]) + ".",
                 "why": "", "guard": "no valid review from the model"}
        w.emit("review.written", {"stars": d["stars"], "fact_stars": fact["stars"], "why": d.get("why"),
                                  "guard": d.get("guard")}, order=o.get("order"),
               **({"counterpart": shop} if shop else {}))
        print(json.dumps({"stars": d["stars"], "review": d["text"]}, ensure_ascii=False))
    finally:
        w.close()
        tool_w.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"review crashed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
