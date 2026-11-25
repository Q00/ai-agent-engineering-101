"""Our forum agent: one turn of reading the forum, voting, and posting once per side (shop and buyer).

    py forum.py        # the outer loop runs it (build step 5); the kit has no forum hook

The kit reads .market/forum.json from the market and sends .market/outbox.jsonl (act.post, act.vote write it).
Posts may only cite ledger entries we know (our paid deals in payments.jsonl, our settled sales) or one of our
fact lines (our buyer's orders no shop could serve, goods our shop holds) - user 11/3 22:10: more posts.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agents import loop  # noqa: E402
from core import card  # noqa: E402
from core import atomic  # noqa: E402
from core.events import EventWriter  # noqa: E402
from tools.evaluate import events  # noqa: E402
from tools.registry import Ctx, call  # noqa: E402

DEADLINE = 100          # per side; two sides per turn


def _lines(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _ledger_rows():
    return [row for f in sorted((HERE / "memory" / "market").glob("ledger-*.jsonl")) for row in _lines(f)]


def known_ledger(ctx, split=False):
    bought = {p["ledger"] for p in _lines(ctx.market_dir / "payments.jsonl")
              if p.get("paid") and isinstance(p.get("ledger"), int)}
    sold = {e["data"]["ledger"] for e in events(["seller.settled"]) if isinstance(e["data"].get("ledger"), int)}
    # Since 11/3 22:3x a sale is recorded on its paid result line; posts cite the settle line of the same order.
    rows = _ledger_rows()
    settle_of = {r["data"].get("order"): r["seq"] for r in rows
                 if r.get("kind") == "settle" and isinstance(r.get("data"), dict)}
    kind = {r.get("seq"): (r.get("kind"), (r.get("data") or {}).get("order")) for r in rows}
    sold = {settle_of.get(kind[s][1], s) if kind.get(s, ("",))[0] == "result" else s for s in sold}
    if split:
        return sorted(bought), sorted(sold)
    return sorted(bought | sold)


def _show(v):
    return "null" if v is None else ("true" if v is True else "false" if v is False else str(v))


def settle_line(row):
    """One ledger settle as the posts that get votes quote it (user 11/3): every field as the ledger has it."""
    d = row["data"]
    return (f"ledger #{row['seq']} settle: order {d.get('order')}, buyer {d.get('buyer')}, shop {d.get('shop')}, "
            f"item {d.get('item')}, amount {d.get('amount')}, unit {d.get('unit')}, name {d.get('name')}, "
            f"grade {_show(d.get('grade'))}, claim {_show(d.get('claim'))}, "
            f"claim_honest {_show(d.get('claim_honest'))}, warranty_hours {_show(d.get('warranty_hours'))}")


def settles(seqs):
    """The settle lines for our ledger entries, from the ledger the outer runtime collects."""
    want, out = set(seqs), {}
    for f in sorted((HERE / "memory" / "market").glob("ledger-*.jsonl")):
        for row in _lines(f):
            if row.get("seq") in want and row.get("kind") == "settle" and isinstance(row.get("data"), dict):
                out[row["seq"]] = settle_line(row)
    return out


def own_posts(ctx):
    """Our post ids and how many we wrote per side. The market answers a post with {"post": <id>} (seen 11/3)."""
    ids, sides = [], {"shop": 0, "buyer": 0}
    for x in _lines(ctx.market_dir / "outbox.sent.jsonl"):
        sent = x.get("sent") or {}
        if "post" not in sent:
            continue
        try:
            ans = json.loads(x.get("answer") or "{}")
        except ValueError:
            continue
        pid = ans.get("post", ans.get("id")) if isinstance(ans, dict) else None
        if pid is not None:
            ids.append(pid)
            sides[sent.get("as")] = sides.get(sent.get("as"), 0) + 1
    return ids, sides


def wanted_facts(now, hours=2, n=6):
    """Our buyer's orders no shop could serve (what we need), one line per category, newest first."""
    orders = {e["keys"].get("order"): e for e in events(["buyer.order"], since=now - hours * 3600)}
    out, seen = {}, set()
    for e in reversed(events(["buyer.closed"], since=now - hours * 3600)):
        o = orders.get(e["keys"].get("order"))
        if not o or "no shop" not in str(e["data"]) or o["data"].get("category") in seen:
            continue
        d = o["data"]
        seen.add(d.get("category"))
        out[f"w-{e['keys']['order']}"] = (
            "buyer", f"Our buyer's order {e['keys']['order']} at {time.strftime('%H:%M', time.localtime(o['ts']))}: "
                     f"{d.get('kind')} in category {d.get('category')}, budget {d.get('budget')}"
                     + (f", use within {d['use_within_hours']} h" if d.get("use_within_hours") else "")
                     + "; no shop had an SKU it accepts in stock.")
        if len(out) >= n:
            break
    return out


def stock_facts(units, strat, n=15):
    """Goods our shop holds now, one line per SKU, most units first (to tell goods buyers we have it ready)."""
    by = {}
    for u in units:
        if u.get("kind") == "goods" and u.get("grade") != "expired":
            by.setdefault(u["sku"], []).append(u)
    w = (strat.get("warranty") or {}).get("hours")
    fv = (strat.get("first_visit") or {}).get("price") == "at_cost"
    out = {}
    for sku, us in sorted(by.items(), key=lambda kv: -len(kv[1]))[:n]:
        u = us[0]
        out[f"s-{sku}"] = (
            "shop", f"In stock at our shop now: {u.get('name')} ({sku}), {len(us)} units, grade "
                    f"{'/'.join(sorted({x.get('grade') for x in us}))}, list price {u.get('list_price')}"
                    + ("; we sell only units with the market's certificate" if strat.get("certified_only") else "")
                    + (f"; every goods sale carries our {w:g} h warranty that refunds the full price" if w else "")
                    + ("; a first visit is at our cost" if fv else "") + ".")
    return out


def next_side(sides, buyer_refs, shop_refs):
    """The side with fewer posts writes next (both count for ALL), if that side has a settle of its own to cite."""
    want = "buyer" if sides.get("buyer", 0) <= sides.get("shop", 0) else "shop"
    have = {"buyer": buyer_refs, "shop": shop_refs}
    if have[want]:
        return want
    other = "shop" if want == "buyer" else "buyer"
    return other if have[other] else None


def side_text(side, sides, mine, lines, mark, facts, posts, wanted, extra):
    """The decision at hand for one side's post this turn."""
    own = {k: v[1] for k, v in facts.items() if v[0] == side}
    kinds = ("a settle our buyer bought (user 11/4 02:1x: purchase records with their ledger line only, no "
             "wanted posts)" if side == "buyer" else
             "a settle our shop sold, or goods our shop holds (to answer a post that asks for goods, set thread = "
             "that post's id and say we have it ready, with grade, certificate and warranty)")
    cite = [f"- ({mark.get(s)}) {lines[s]}" for s in sorted(lines) if side in mark.get(s, "")][-10:]
    return ("# the decision at hand\n"
            f"Our own posts: {mine[-10:] or 'none'} (as shop {sides.get('shop', 0)}, as buyer "
            f"{sides.get('buyer', 0)}). Post this turn as {side}, about {kinds}.\n"
            "Settle lines we may cite (newest last):\n" + ("\n".join(cite) or "- none") + "\n"
            "Our fact lines (cite one with fact = its id):\n"
            + ("\n".join(f"- {k}: {v}" for k, v in own.items()) or "- none") + "\n"
            "A settle post states one checked fact from one settle line, in this shape: 'Settled SKU=<item> "
            "price=<amount>; grade=<grade>; warranty_hours=<warranty_hours>. One observed purchase, not a guarantee "
            "of future quality or availability.' (refs = that line's number; act.post appends the line). A fact "
            "post says only what its fact line says. Numbers only from the cited line.\n"
            + extra
            + ("Posts asking for goods (marked excerpts):\n"
               + "\n".join(f"- #{p['id']} {p.get('author')}: {p['excerpt']}" for p in wanted) + "\n"
               if wanted and side == "shop" else "")
            + "Newest forum posts (marked excerpts):\n"
            + "\n".join(f"- #{p['id']} {p.get('author')} ({p.get('role')}), score {p.get('score')}: {p['excerpt']}"
                        for p in posts["posts"])
            + f"\n\nVote on posts that are true and useful (act.vote), then act.post one post as {side}, or end "
              "with act.skip.")


def main():
    tool_w, w = EventWriter("forum", source="tools"), EventWriter("forum", source="forum")
    ctx = Ctx(caller="forum", _writer=tool_w)
    try:
        w.context(strategy_version=ctx.strategy.get("version"), epoch=ctx.strategy.get("epoch"),
                  card_version=card.load()[0])
        bought, sold = known_ledger(ctx, split=True)
        refs = sorted(set(bought) | set(sold))
        mine, sides = own_posts(ctx)
        lines = settles(refs)
        inv = call(ctx, "me.inventory", {"detail": "full"})
        units = (inv["result"].get("unit_list") or []) if inv["ok"] else []
        facts = stock_facts(units, ctx.strategy)        # user 11/4 02:1x: no wanted posts (wanted_facts)
        posts = call(ctx, "market.forum", {"limit": 12})["result"]
        asks = {p["id"]: p for q in ("구합니다", "wanted", "looking for", "goods")
                for p in call(ctx, "market.forum", {"query": q, "limit": 6})["result"]["posts"]}
        asks = sorted(asks.values(), key=lambda p: -p["ts"])[:8]
        wf = ctx.strategy.get("welcome_food") or {}
        bc = (ctx.strategy.get("limits") or {}).get("below_cost") or {}
        food = [u for u in units if u.get("kind") == "food"]
        r = (atomic.read_json(ctx.market_dir / "inventory.json", default={}) or {}).get("restock") or {}
        nxt = time.strftime("%H:%M", time.localtime(r["opens_at"])) if r.get("opens_at") else "unknown"
        extra = (f"Welcome Food (strategy): fresh food each supply window (about every 20 min; next opens "
                 f"{nxt}), food up to list price {bc.get('max_list_price')} discounted while grade {bc.get('grades')}, "
                 f"first visit at {wf.get('share_of_cost')} of cost; food units we hold now: {len(food)}.\n"
                 if wf else "")
        mark_of = {"buyer": "our buyer bought", "shop": "our shop sold"}
        mark = {s: mark_of["buyer"] for s in bought} | {s: mark_of["shop"] for s in sold}
        # Each turn both sides post once (the kit allows one post per 5 minutes per side), fewer posts first.
        first = next_side(sides, [1], [1])
        done = []
        for side in (first, "shop" if first == "buyer" else "buyer"):
            if not any(side in mark.get(s, "") for s in lines) and not any(v[0] == side for v in facts.values()):
                continue
            ctx.situation = {"known_ledger": refs, "own_posts": mine, "settles": lines, "post_as": side,
                             "side_refs": {"buyer": bought, "shop": sold}, "facts": facts}
            text = side_text(side, sides, mine, lines, mark, facts, posts, asks, extra)
            d, trace = loop.decide("forum", ctx, text, deadline=time.time() + DEADLINE)
            votes = sum(1 for t in trace if '"act.vote"' in str(t.get("answer")) and '"voted"' in str(t["observation"]))
            w.emit("forum.turn", {"action": (d or {}).get("action"), "as": side, "refs": (d or {}).get("refs"),
                                  "fact": (d or {}).get("fact"), "votes": votes,
                                  "why": (d or {}).get("why") or (d or {}).get("thought")})
            if d and d.get("action") == "post":
                w.emit("forum.posted", {"as": d["as"], "refs_valid": True, "post_id": None, "fact": d.get("fact")})
                # the id comes back on the market's answer in outbox.sent.jsonl; votes are counted by side from
                # forum.json (forum.votes "as")
            done.append({"as": side, "forum": (d or {}).get("action"), "votes": votes})
        print(json.dumps({"forum": done}))
    finally:
        w.close()
        tool_w.close()


if __name__ == "__main__":
    main()
