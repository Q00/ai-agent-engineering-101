"""Our buyer for one order, without the kit (market_buyer.py is the kit's hook and calls this).

What code does (deterministic): fold the kit's payment results into the reflex facts, drop blocked shops and
shops with nothing this order accepts, speak A2A, read shops' messages through calc.extract (S1), check the
receipt against what we agreed (check.receipt), and record events and the outcome. What the model decides
(agents.loop, role buyer): which shop and SKU, every offer, when to walk away, and the accept policy the
kit checks before it pays.

A receipt above what we agreed never goes out: the kit pays any signed receipt within budget, so a
cheating receipt is passed on as null and the shop is blocked (reflex).
"""
import json
import time
import uuid

from agents import loop
from core import atomic, paths
from core import card as cardmod
from core import reflex
from core.events import EventWriter
from tools import market_files as mf
from tools.calc import safe_name
from tools.registry import Ctx, call

DEADLINE = 270             # the kit stops market_buyer after 300 s
MAX_TURNS = 10
REVIEWS_SHOWN = 2
# 11/4 21:1x: food grades by the shelf life left (agent.md: A 80% or more, B 45% or more, C less) - the most a
# claimed grade can have left, as a share of the SKU's shelf life
GRADE_MAX_LEFT = {"A": 1.0, "B": 0.8, "C": 0.45}
MAX_SHOPS = 3              # rules: shops tried per order when one ends without a receipt
HEADERS = {"A2A-Version": "1.0"}
PRICE_FACT_EVERY = 600     # seconds between rebuilds of memory/facts/shop_prices.json


def shop_prices(shops, now=None):
    """User 11/3 23:4x: surplus first, at the shops that sell cheap. Each shop's median paid share of list price over
    the market's settles (the last two ledger days), keyed by shop URL; rebuilt from the ledger every 10 minutes."""
    now = now or time.time()
    path = paths.memory() / "facts" / "shop_prices.json"
    fact = atomic.read_json(path, default=None) or {}
    if now - fact.get("ts", 0) > PRICE_FACT_EVERY:
        cat = {}
        try:
            cat = json.loads((paths.root() / ".market" / "catalog.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        shares, warranty = {}, {}
        for f in sorted((paths.memory() / "market").glob("ledger-*.jsonl"))[-2:]:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    if '"kind": "settle"' not in line:
                        continue
                    try:
                        x = json.loads(line)["data"]
                    except (ValueError, KeyError):
                        continue
                    lp = (cat.get(x.get("sku") or x.get("item")) or {}).get("list_price")
                    if lp and x.get("amount") is not None and x.get("shop"):
                        shares.setdefault(x["shop"].rstrip("/"), []).append(x["amount"] / lp)
                        if (cat.get(x.get("sku") or x.get("item")) or {}).get("kind") == "goods":
                            w = warranty.setdefault(x["shop"].rstrip("/"), [0, 0])
                            w[0] += 1
                            w[1] += 1 if (x.get("warranty_hours") or 0) > 0 else 0
        fact = {"ts": now, "shops": {u: {"median_share": round(sorted(v)[len(v) // 2], 2), "deals": len(v),
                                         "goods_warranty_share": round(warranty[u][1] / warranty[u][0], 2)
                                         if warranty.get(u) else None}
                                     for u, v in shares.items()}}
        atomic.write_json(path, fact)
    return {s["name"]: fact["shops"].get((s.get("url") or "").rstrip("/")) for s in shops}


PAY_RATE_MIN_N = 10             # paid + unpaid before a shop's pay rate is trusted


def pays_too_little(r, min_rate):
    """11/6 10:3x: nocha12 agreed 84 times in 24 h and 50 failed at payment (409 out of stock) - shop_trouble put it
    last, but for food it was often the only shop left, so it was picked anyway. With min_rate set (playbook
    buyer.min_pay_rate) a shop whose agreed deals were paid less often than that is skipped, not just put last."""
    r = r or {}
    n = r.get("paid", 0) + r.get("unpaid", 0)
    return bool(min_rate) and n >= PAY_RATE_MIN_N and r.get("paid", 0) / n < min_rate


def shop_trouble(r):
    """0 fine, 1 pay trouble, 2 refuses us (11/4 02:4x: totato2203-r3, the cheapest by market share, refused 105 of
    106 orders, and the rules picked it first every time)."""
    r = r or {}
    tries = r.get("deals", 0) + r.get("no_deal", 0)
    last = [x.get("verdict", "") for x in (r.get("last") or [])[:3]]
    if (tries >= 5 and r.get("fair", 0) / tries < 0.1) or (len(last) == 3 and all(v.startswith("no deal") for v in last)):
        return 2
    if r.get("unpaid", 0) >= 2 and r.get("unpaid", 0) >= r.get("paid", 0):
        return 1
    return 0


class A2A:
    """The A2A v1.0 client: read the card, keep one task, send text."""

    def __init__(self, base, timeout=120):
        import httpx
        self.httpx, self.timeout = httpx, timeout
        card = httpx.get(base.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS,
                         timeout=min(30, timeout)).json()
        iface = next((i for i in card.get("supportedInterfaces", [])
                      if i.get("protocolBinding") == "JSONRPC" and str(i.get("protocolVersion")) == "1.0"), None)
        if iface is None:
            raise RuntimeError("no JSONRPC 1.0 interface in the shop's card")
        self.url, self.card, self.task_id = iface["url"], card, None

    def _rpc(self, method, params, timeout):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = self.httpx.post(self.url, json=body, headers=HEADERS, timeout=timeout).json()
        if "error" in r:
            raise RuntimeError(f"{method}: {r['error'].get('message')}")
        return r["result"].get("task", r["result"])

    def send(self, text, timeout=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if self.task_id:
            msg["taskId"] = self.task_id
        t = self._rpc("SendMessage", {"message": msg}, timeout or self.timeout)
        self.task_id = t.get("id", self.task_id)
        return reply(t)

    def cancel(self):
        if self.task_id:
            try:
                self._rpc("CancelTask", {"id": self.task_id}, 30)
            except Exception:
                pass


def reply(t):
    """(state, text, receipt) from an A2A task."""
    state = (t.get("status") or {}).get("state", "")
    text = " ".join(p.get("text", "") for p in ((t.get("status") or {}).get("message") or {}).get("parts", []))
    receipt = None
    for a in t.get("artifacts") or []:
        for p in a.get("parts") or []:
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                receipt = dict(p["data"])
    if receipt and isinstance(receipt.get("price"), float) and receipt["price"].is_integer():
        receipt["price"] = int(receipt["price"])
    if receipt and "seller" in receipt and "shop" not in receipt:
        receipt["shop"] = receipt.pop("seller")
    return state, text, receipt


class BuyerCore:
    def __init__(self, order, shops, model_call=None, connect=A2A, clock=time.time):
        self.order, self.shops_in, self.model_call, self.connect, self.clock = order, shops, model_call, connect, clock
        if isinstance(order.get("budget"), float) and order["budget"].is_integer():
            order["budget"] = int(order["budget"])
        self.t0 = clock()
        self.events = EventWriter("buyer", source="buyer")
        self.tool_events = EventWriter("buyer", source="tools")
        self.ctx = Ctx(caller="buyer", clock=clock, _writer=self.tool_events, keys={"order": order.get("order")})  # C1
        self.arms, self.patch = {}, {}
        try:                        # design R12, D-34: the running A/B experiments; the B arm's playbook for this order
            from harness import experiment
            self.arms, patch = experiment.assign("buyer", order.get("order"), clock())
            self.patch = ((patch.get("playbook") or {}).get("buyer") or {})
        except Exception:           # the experiment ledger must never stop an order: run as A
            self.arms, self.patch = {}, {}

    def deadline(self):
        return self.t0 + DEADLINE

    def close(self):
        self.events.close()
        self.tool_events.close()

    # --- the order

    def run(self):
        """The result line for the kit: {"shop", "receipt", "stars", "review", "accept"?}."""
        o, ctx = self.order, self.ctx
        strategy = ctx.strategy
        self.events.context(strategy_version=strategy.get("version"), epoch=strategy.get("epoch"),
                            card_version=cardmod.load()[0])
        reflex.read_payments(ctx.market_dir, ctx.now())
        cat = mf.catalog(ctx)
        acceptable = o.get("acceptable") or []
        kind = o.get("kind") or next((cat[s].get("kind") for s in acceptable if s in cat), None)
        self.events.emit("buyer.order", {"category": o.get("item"), "budget": o.get("budget"), "kind": kind,
                                         "use_within_hours": o.get("use_within_hours"),
                                         "acceptable_n": len(acceptable),
                                         **({"experiment": self.arms} if self.arms else {})}, order=o.get("order"))
        blocked = reflex.blocked()
        if self.rules() and self.playbook()["skip_unsigned"]:
            blocked = {**{n: "unsigned receipts" for n in reflex.unsigned(ctx.now())}, **blocked}
        shops = {}
        for s in self.shops_in:
            skus = [x["sku"] for x in s.get("offers") or [] if x.get("qty") and (not acceptable or x["sku"] in acceptable)]
            if skus and s["name"] not in blocked:
                shops[s["name"]] = dict(s, skus=skus)
        if not shops:
            if kind == "goods" and acceptable:
                # user 11/4 10:3x: a goods order no shop can serve is demand nobody supplies - restock reads these and
                # our shop stocks one of its SKUs for the next buyer who needs it (our own buyer cannot buy from us)
                atomic.append_line(paths.memory() / "facts" / "unserved.jsonl", json.dumps(
                    {"ts": ctx.now(), "order": o.get("order"), "category": o.get("item"), "budget": o.get("budget"),
                     "skus": acceptable[:30]}, ensure_ascii=False))
            return self.finish_none("", "no deal: no shop that is not blocked has what this order accepts")

        ctx.situation = {"budget": o["budget"], "shops": shops}
        d = self.rule_pick(shops) if self.rules() else None
        if d is None:
            d, trace = loop.decide("buyer", ctx, self.pick_text(shops, kind), deadline=self.deadline(),
                                   model_call=self.model_call, allowed=["act.pick"] + loop.ROLES["buyer"]["look"])
        guard = None
        if d is None:
            d, guard = self.guard_pick(shops), "no valid pick from the model"
        shop = shops[d["shop"]]
        known = call(ctx, "me.counterpart", {"name": shop["name"], "side": "shop"})["result"].get("known")
        self.events.emit("buyer.pick", {"shop": shop["name"], "sku": d["sku"], "why": d.get("why") or d.get("thought"),
                                        "explored": not known, "guard": guard, "shops": len(shops)},
                         order=o.get("order"), counterpart=shop["name"])
        out = self.negotiate(shop, d["sku"], kind)
        # 11/4 03:1x: one shop per order lost most goods orders (15 of 51 rejected at the first message); an order
        # takes ~3 s by rule and has 270 s, so the rules try the next shop when one ends without a receipt
        tried = {shop["name"]}
        pb = self.playbook()
        most = pb["max_shops_goods"] if kind == "goods" else pb["max_shops_food"]
        # user 11/4 10:4x: the market gives the next order about 20 s after this one ends (measured: 20.4 s median),
        # so a long order costs orders; shops are tried until the kind's time budget is used, not by count alone
        budget = pb["goods_time_budget"] if kind == "goods" else pb["food_time_budget"]
        while (self.rules() and out.get("receipt") is None and len(tried) < most
               and self.clock() - self.t0 < budget and self.deadline() - self.clock() > 40):
            rest = {n: x for n, x in shops.items() if n not in tried}
            if not rest:
                break
            ctx.situation = {"budget": o["budget"], "shops": rest}
            d = self.rule_pick(rest)
            if d is None:
                break
            shop = rest[d["shop"]]
            tried.add(shop["name"])
            self.events.emit("buyer.pick", {"shop": shop["name"], "sku": d["sku"], "why": d.get("why"),
                                            "retry": len(tried) - 1, "shops": len(shops)},
                             order=o.get("order"), counterpart=shop["name"])
            out = self.negotiate(shop, d["sku"], kind)
        return out

    # --- the rules (HARNESS 14, D34; user 11/4 03:2x: benchmark myeonggi - fast, cheap, high-list goods)

    def playbook(self):
        pb = {**((self.ctx.strategy.get("playbook") or {}).get("buyer") or {}), **self.patch}   # B arm on top
        return {"mode": pb.get("mode", "agent"), "ceiling_share": pb.get("ceiling_share", 0.8),   # rules when the strategy says so
                "max_shops_goods": pb.get("max_shops_goods", 10), "max_shops_food": pb.get("max_shops_food", MAX_SHOPS),
                "goods_time_budget": pb.get("goods_time_budget", 120), "food_time_budget": pb.get("food_time_budget", 20),
                "goods_msg_timeout": pb.get("goods_msg_timeout", 20), "food_msg_timeout": pb.get("food_msg_timeout", 8),
                "shelf_guard": pb.get("shelf_guard", False), "skip_unsigned": pb.get("skip_unsigned", False),
                "min_pay_rate": pb.get("min_pay_rate", 0)}

    def rules(self):
        return self.playbook()["mode"] == "rules"

    def shares(self, lp, kind):
        """(open, target) shares of list for this order, from strategy.buyer (v14)."""
        b = self.ctx.strategy.get("buyer") or {}
        if kind == "goods" and b.get("big_list") is not None and lp >= b["big_list"]:
            return b.get("open_share", 0.4), b.get("target_share", 0.5)
        return b.get("food_open_share", 0.45), b.get("food_target_share", 0.6)

    def rule_pick(self, shops):
        """The cheapest shop first (pay trouble last), and there the highest-list SKU we can likely buy in budget."""
        cat = mf.catalog(self.ctx)
        prices = shop_prices(list(shops.values()))
        rf = (reflex.load().get("shops") or {})

        ceil_share = self.playbook()["ceiling_share"]
        goods = (self.order.get("kind") or "") == "goods" or any((cat.get(k) or {}).get("kind") == "goods"
                                                                  for k in (self.order.get("acceptable") or [])[:3])

        def expected(s):
            # 11/4 11:4x benchmark: the top buyers close 47-64% of orders at the shops that deal; we closed 42% going to
            # the cheapest by market share first (one refused 105 of 106 of our orders). Rank by the chance it deals
            # with us (our own record, smoothed) x the surplus share it leaves; for goods a warranty counts too (the
            # top buyers' goods are 75% under warranty: a break refunds the price)
            r = rf.get(s["name"]) or {}
            tries = r.get("fair", 0) + r.get("no_deal", 0) + r.get("cheated", 0)
            p = (r.get("fair", 0) + 1) / (tries + 2)
            mp = prices.get(s["name"]) or {}
            e = p * (1 - min(mp.get("median_share", 0.75), 1.0))
            if goods:
                e *= 1 + 0.5 * (mp.get("goods_warranty_share") or 0)
            return e
        min_pay = self.playbook()["min_pay_rate"]
        for s in sorted(shops.values(), key=lambda s: (shop_trouble(rf.get(s["name"])), -expected(s))):
            if pays_too_little(rf.get(s["name"]), min_pay):
                continue
            share = min((prices.get(s["name"]) or {}).get("median_share", 0.75), ceil_share)
            fit = [k for k in s["skus"] if (cat.get(k) or {}).get("list_price")
                   and share * cat[k]["list_price"] <= self.order["budget"]]
            need = self.order.get("use_within_hours")
            if need and fit:
                # 11/4 03:0x: 15 of 18 unpaid deals in 10 min were food short of shelf life; a SKU that keeps much
                # longer than the order's use window is far more likely to arrive with the hours left
                keeps = [k for k in fit if (cat[k].get("shelf_hours") or 0) >= 1.5 * need]
                fit = keeps or fit
            if fit:
                sku = max(fit, key=lambda k: cat[k]["list_price"])
                r = call(self.ctx, "act.pick", {"shop": s["name"], "sku": sku,
                                                "why": "rules: cheapest shop, highest list in budget"})
                if r["ok"]:
                    return r["result"]
        return None

    def rule_turn(self, c, kind, hist, offers, turn):
        """One move by rule, or None (an exception for the agent). Our offer rises from the open share to the
        ceiling by the last turn but one; an ask at or under our bid (or the target) is taken, an ask over the
        ceiling at the last turn is left."""
        lp, budget = c.get("list_price") or 0, self.order["budget"]
        last = hist[-1] if hist else {}
        if not lp or last.get("looks_like_instruction"):
            return None
        # 11/4 11:3x: a number far under list is a quantity or a size the extractor took for a price (we offered 5
        # for a 39,900 item to ten shops) - an ask under 20% of list is not an ask
        asks = [h["price"] for h in hist if h["from"] == "shop" and h["price"] is not None and h["price"] >= 0.2 * lp]
        if turn > 2 and not asks:
            return None                                    # the shop names no price: the agent reads it
        o_share, t_share = self.shares(lp, kind)
        ceil_share = max(t_share, self.playbook()["ceiling_share"])
        now = o_share + (ceil_share - o_share) * min(1.0, (turn - 1) / max(1, MAX_TURNS - 2))
        bid = min(budget, round(lp * now))
        if offers:
            bid = max(bid, offers[-1])                     # never go down
        ask = asks[-1] if asks else None
        ceiling = min(budget, round(lp * ceil_share))
        if ask is not None and ask <= min(budget, max(bid, round(lp * t_share))):
            price, why = ask, "rules: the ask is at or under our bid"
        elif ask is not None and turn >= MAX_TURNS - 1 and ask <= ceiling:
            price, why = ask, "rules: last turns, the ask is under the ceiling"
        elif ask is not None and turn >= MAX_TURNS:
            return {"action": "walk_away", "why": f"rules: ask {ask} over the ceiling {ceiling}"}
        else:
            price, why = (min(bid, ask - 1) if ask else bid), f"rules: {now:.0%} of list"
        price = max(1, price)
        r = call(self.ctx, "act.offer", {"price": price, "say": f"I can pay {price} KRW."})
        if not r["ok"]:
            return None
        return dict(r["result"], thought=why)

    def guard_pick(self, shops):
        """Fallback pick: the shop whose reviews average highest (unknown shops after reviewed ones), its first SKU."""
        def avg(s):
            st = [r["stars"] for r in s.get("reviews") or [] if isinstance(r.get("stars"), (int, float))]
            return sum(st) / len(st) if st else 0
        s = max(shops.values(), key=avg)
        return {"action": "pick", "shop": s["name"], "sku": s["skus"][0]}

    def pick_text(self, shops, kind):
        o, cat = self.order, mf.catalog(self.ctx)
        lines = ["# the decision at hand",
                 f"Order {o.get('order')}: category {o.get('item')}, budget {o['budget']}, kind {kind}"
                 + (f", use within {o['use_within_hours']} h" if o.get("use_within_hours") else "")
                 + f". {int(self.deadline() - self.clock())} s left for the whole order.",
                 "Pick the shop and the SKU (act.pick). Shops that cheated us are already left out.",
                 "Surplus first (half of buyer ALL): surplus = the SKU's list price - what we pay. Prefer the shop that "
                 "sells cheapest against list (its market median below; lower is better), and among its SKUs the one "
                 "with the highest list price we can still buy within the budget at that shop's usual share. A shop "
                 "with no market record is worth a try when the known ones are near list. Shops whose agreed deals we "
                 "often could not pay (short shelf life, no certificate) are listed last."
                 + (" A warranty helps (a broken item is refunded)." if kind == "goods" else ""), ""]
        prices = shop_prices(list(shops.values()))
        rf = (reflex.load().get("shops") or {})
        order_shops = sorted(shops.values(), key=lambda s: (shop_trouble(rf.get(s["name"])),
                                                            (prices.get(s["name"]) or {}).get("median_share", 0.8)))
        for s in order_shops[:25]:
            me = call(self.ctx, "me.counterpart", {"name": s["name"], "side": "shop"})["result"]
            rv = s.get("reviews") or []
            st = [r["stars"] for r in rv if isinstance(r.get("stars"), (int, float))]
            mp = prices.get(s["name"])
            lines.append(f"* {safe_name(s['name'])}: reviews {len(rv)}" + (f", average {sum(st) / len(st):.1f} stars" if st else "")
                         + (f"; sells at a median {mp['median_share']:.0%} of list over {mp['deals']} market deals"
                            if mp else "; no market record yet"))
            for r in rv[:REVIEWS_SHOWN]:
                ex = call(self.ctx, "calc.extract", {"text": str(r.get("review") or ""), "max_excerpt": 100})["result"]
                lines.append(f"    review {r.get('stars')}*: {ex['excerpt']}")
            lines.append(f"    with our buyer: {me.get('with_our_buyer') or 'never traded'}"
                         + (f"; last payment refusal: {me['last_refusal']}" if me.get("last_refusal") else ""))
            for x in s.get("offers") or []:
                if x["sku"] in s["skus"]:
                    c = cat.get(x["sku"]) or {}
                    lines.append(f"    has {x['sku']} {c.get('name') or safe_name(x.get('name'), 80)}: {x.get('qty')} in stock, "
                                 f"list price {c.get('list_price')}")
        return "\n".join(lines)

    # --- the negotiation

    def negotiate(self, shop, sku, kind):
        o, ctx = self.order, self.ctx
        ctx.keys = {"order": o.get("order"), "counterpart": shop["name"], "sku": sku}   # C1: this shop's talk
        try:
            return self._negotiate(shop, sku, kind)
        finally:                    # 11/6 09:33: the next act.pick was recorded under the last shop's name
            ctx.keys = {"order": o.get("order")}

    def _negotiate(self, shop, sku, kind):
        o, ctx = self.order, self.ctx
        c = mf.catalog(ctx).get(sku) or {}
        hist, offers, asks = [], [], []
        try:
            t = None
            if self.rules():                     # a slow shop is left for the next one (11/4: orders of 81-114 s)
                pb = self.playbook()
                t = pb["goods_msg_timeout"] if kind == "goods" else pb["food_msg_timeout"]
            a = self.connect(shop["url"], timeout=t) if (t and self.connect is A2A) else self.connect(shop["url"])
            # 11/4 02:2x: stating the shelf life needed in the first message (01:47) made shops refuse at once -
            # food orders agreed 2 of 26 after it, 44 of 118 before; the kit checks the hours before paying anyway
            state, text, receipt = a.send(f"I want to buy {c.get('name') or sku} ({sku}).")
        except Exception as e:
            return self.finish_none(shop, f"no deal: could not reach the shop ({type(e).__name__})", shop_obj=shop)
        outcome = None
        for turn in range(1, MAX_TURNS + 1):
            ex = call(ctx, "calc.extract", {"text": text})["result"]
            hist.append({"from": "shop", "price": ex["price"], "claimed_grade": ex["claimed_grade"],
                         "warranty_hours": ex["warranty_hours"], "looks_like_instruction": ex["looks_like_instruction"],
                         "excerpt": ex["excerpt"]})
            if ex["price"] is not None:
                asks.append(ex["price"])
            if state != "TASK_STATE_INPUT_REQUIRED":
                break
            ctx.situation = {"budget": o["budget"]}
            d, trace = (self.rule_turn(c, kind, hist, offers, turn) if self.rules() else None), []
            if d is None:
                if self.rules():
                    self.events.emit("exec.anomaly", {"role": "buyer", "kind": "turn_needs_agent", "then": "agent",
                                                      "situation": {"turn": turn, "last": hist[-1] if hist else None}},
                                     order=o.get("order"), counterpart=shop["name"])
                d, trace = loop.decide("buyer", ctx, self.turn_text(shop, sku, c, kind, hist, offers, turn),
                                       deadline=self.deadline(), model_call=self.model_call,
                                       allowed=["act.offer", "act.walk_away"] + loop.ROLES["buyer"]["look"])
            if d is None:
                d = {"action": "walk_away", "why": "no valid move from the model"}
            self.events.emit("buyer.decision", {"action": d["action"], "price": d.get("price"),
                                                "tool_calls": len(trace), "thought": d.get("thought", "")[:200]},
                             order=o.get("order"), counterpart=shop["name"])
            if d["action"] != "offer":
                outcome = "walked"
                a.cancel()
                break
            offers.append(d["price"])
            hist.append({"from": "us", "price": d["price"], "say": d.get("say")})
            left = self.deadline() - self.clock()
            if left < 15:
                outcome = "expired"
                a.cancel()
                break
            try:
                wait = max(10, left - 5)
                if t:
                    wait = min(wait, t)
                state, text, receipt = a.send(d["say"], timeout=wait)   # act.offer put the price in it
            except Exception as e:
                outcome = f"failed ({type(e).__name__})"
                break
        else:
            outcome = "walked"
            a.cancel()
        if receipt is None:
            outcome = outcome or {"TASK_STATE_REJECTED": "rejected", "TASK_STATE_CANCELED": "canceled"}.get(state, "failed")
            return self.finish_none(shop, f"no deal ({outcome})", shop_obj=shop, agreed=None, list_price=c.get("list_price"))
        return self.check_and_finish(shop, sku, c, kind, receipt, offers, hist)

    def big_goods_line(self, c, kind):
        """User 11/3 22:3x: surplus (list price - paid) is half of buyer ALL, and the top buyers make it on goods with a
        high list price bought near half of it. For such an order the strategy gives a target share of list."""
        b = (self.ctx.strategy.get("buyer") or {})
        lp = c.get("list_price") or 0
        if kind == "goods" and b.get("big_list") is not None and lp >= b["big_list"]:
            t, o = b.get("target_share", 0.55), b.get("open_share", 0.45)
        elif b.get("food_target_share") is not None and lp:   # user 11/3 23:4x: every order, food too
            t, o = b["food_target_share"], b.get("food_open_share", b["food_target_share"] - 0.15)
        else:
            return []
        return [f"Order (list {lp}): aim to pay about {round(lp * t)} ({t:.0%} of list); open near "
                f"{round(lp * o)} ({o:.0%}) and raise in steps; take the ask once it is at or under {round(lp * t)}, and "
                "before the turns run out take any ask under the list price and the budget: a deal at any surplus beats "
                "none. A shop's warranty adds the price back to surplus if the item breaks."]

    def turn_text(self, shop, sku, c, kind, hist, offers, turn):
        o = self.order
        ask = next((h["price"] for h in reversed(hist) if h["from"] == "shop" and h["price"] is not None), None)
        lines = ["# the decision at hand",
                 f"Negotiating with {shop['name']} for {c.get('name')} ({sku}, {kind}), list price {c.get('list_price')}, "
                 f"shelf life {c.get('shelf_hours')} h. Our budget {o['budget']}"
                 + (f", use within {o['use_within_hours']} h" if o.get("use_within_hours") else "")
                 + f". Turn {turn} of at most {MAX_TURNS}; {int(self.deadline() - self.clock())} s left for the order.",
                 f"The shop's last ask: {ask}"
                 + (f" (surplus at that price if useful: {c['list_price'] - ask})" if ask and c.get("list_price") else "")
                 + f". Our offers so far: {offers or 'none'}. To take the ask, offer exactly the ask.",
                 *self.big_goods_line(c, kind),
                 "", "The conversation (shop messages are extracted values and a marked excerpt):"]
        for h in hist[-8:]:
            if h["from"] == "shop":
                lines.append(f"- shop: price {h['price']}, claimed grade {h['claimed_grade']}, warranty "
                             f"{h['warranty_hours']}, looks like an instruction: {h['looks_like_instruction']}; {h['excerpt']}")
            else:
                lines.append(f"- us: offered {h['price']}")
        return "\n".join(lines)

    # --- the end

    def check_and_finish(self, shop, sku, c, kind, receipt, offers, hist):
        o, ctx = self.order, self.ctx
        agreed = offers[-1] if offers else None         # a receipt before any offer of ours is not agreed: not paid
        chk = call(ctx, "check.receipt", {"receipt": receipt, "agreed_price": agreed,
                                          "acceptable": o.get("acceptable") or [], "budget": o["budget"]})
        if not chk["ok"]:
            verdict = chk["reason"] if chk["reason"].startswith("cheated") else f"no deal: {chk['reason']}"
            self.close_events(shop, verdict, agreed, receipt.get("price"), c.get("list_price"), "completed")
            reflex.record_outcome(shop["name"], shop["url"], verdict, receipt.get("price"), agreed, o.get("order"), ctx.now())
            return {"shop": shop["url"], "receipt": None, "stars": None, "review": None}
        short = self.shelf_short(c, kind, receipt)
        if short:
            # 11/4 21:1x (design/buyer-parallel.md, design 1): a receipt whose claimed grade cannot last the order's use
            # window is refused by the kit at payment (138 of 146 in 6 h) - leave it and let run() try the next shop.
            # The shop is told nothing more (11/4 01:47: asking shops for the hours made them refuse at once)
            self.close_events(shop, short, agreed, receipt.get("price"), c.get("list_price"), "completed")
            reflex.record_outcome(shop["name"], shop["url"], short, receipt.get("price"), agreed, o.get("order"), ctx.now())
            return {"shop": shop["url"], "receipt": None, "stars": None, "review": None}
        ctx.situation = {"budget": o["budget"], "receipt": receipt}
        text = (f"# the decision at hand\nThe shop {shop['name']} sent a receipt for {c.get('name')} ({sku}, {kind}) at "
                f"{receipt['price']}, matching what we agreed. Claim on the receipt: {receipt.get('claim')}; warranty "
                f"{receipt.get('warranty_hours')} h; certificate shown: {receipt.get('cert')} (only a certificate lets the kit check grades or shelf life). Shelf life of the SKU "
                f"{c.get('shelf_hours')} h"
                + (f"; the order must be usable for {o['use_within_hours']} h" if o.get("use_within_hours") else "")
                + ". Set what the checkout must show before the kit pays (act.finish).")
        if self.rules():                     # goods: no conditions; food: the order's use window (v14)
            pol = {"min_hours_left": o["use_within_hours"]} if kind == "food" and o.get("use_within_hours") else {}
            if pol and (c.get("shelf_hours") or 0) >= 3 * o["use_within_hours"]:
                # 11/4 03:3x: 12 of 33 unpaid deals in 30 min had no certificate to show the hours; a SKU that keeps
                # three times the use window will not spoil in it, so the hours are not asked for
                pol = {}
            r = call(ctx, "act.finish", {"accept": pol, "why": "rules"})
            d = r["result"] if r["ok"] else None
        else:
            d, _ = loop.decide("buyer", ctx, text, deadline=self.deadline(), model_call=self.model_call,
                               allowed=["act.finish"] + loop.ROLES["buyer"]["look"])
        accept = d["accept"] if d else ({"min_hours_left": o["use_within_hours"]}
                                        if kind == "food" and o.get("use_within_hours") else {})
        self.close_events(shop, "fair", agreed, receipt["price"], c.get("list_price"), "completed", accept=accept,
                          guard=None if d else "no valid accept policy from the model")
        reflex.record_outcome(shop["name"], shop["url"], "fair", receipt["price"], agreed, o.get("order"), ctx.now())
        out = {"shop": shop["url"], "receipt": receipt, "stars": None, "review": None}
        if accept:
            out["accept"] = accept
        return out

    def shelf_short(self, c, kind, receipt):
        """'no deal: shelf ...' when the receipt's claimed grade cannot last the order's use window, else None.
        Only the sure case: a claim whose most shelf life left is under the need (the uncertain B band is paid)."""
        need, shelf = self.order.get("use_within_hours"), c.get("shelf_hours")
        top = GRADE_MAX_LEFT.get(str(receipt.get("claim") or "").upper())
        if not (self.rules() and self.playbook()["shelf_guard"] and kind == "food" and need and shelf and top):
            return None
        if need <= top * shelf:
            return None
        return (f"no deal: shelf - grade {receipt['claim']} keeps at most {top * shelf:.0f} h of {shelf} h, "
                f"the order needs {need:g} h")

    def finish_none(self, shop, verdict, shop_obj=None, agreed=None, list_price=None):
        name = shop_obj["name"] if shop_obj else ""
        self.close_events(shop_obj, verdict, agreed, None, list_price, verdict.split("(")[-1].rstrip(")") or "none")
        if shop_obj:
            reflex.record_outcome(name, shop_obj["url"], verdict, None, agreed, self.order.get("order"), self.ctx.now())
        return {"shop": shop_obj["url"] if shop_obj else "", "receipt": None, "stars": None, "review": None}

    def close_events(self, shop, verdict, agreed, receipt_price, list_price, outcome, accept=None, guard=None):
        self.events.emit("buyer.closed", {"outcome": outcome, "agreed": agreed, "receipt_price": receipt_price,
                                          "verdict": verdict, "list_price": list_price, "accept": accept, "guard": guard},
                         order=self.order.get("order"), **({"counterpart": shop["name"]} if shop else {}))


def load_inputs(shops_path, order_path):
    shops = json.loads(open(shops_path, encoding="utf-8").read())
    order = json.loads(open(order_path, encoding="utf-8").read()) if order_path else {}
    return order, shops
