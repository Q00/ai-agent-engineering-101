"""The seller's negotiation, without A2A (seller.py speaks A2A and calls this): one buyer message in, one move out.

What code does (deterministic): read the buyer's message through calc.extract (S1: values and a marked
excerpt, never the raw text in the prompt), find the units that fit what was asked, keep the negotiation's
state (unit, our ask, the buyer's offer, turns), build the decision at hand, sign the receipt, and record
events. What the model decides (agents.loop, role seller): which unit, what price, what to say, whether
to accept, claim a grade, or give a warranty. The market's certificate always goes with the unit (shop card).

When the model gives no valid move, the guard repeats our last ask (or the list price on the first turn,
raised to what check.price allows) and records why.
"""
import base64
import json
import math
import random
import re
import time
from pathlib import Path

from agents import loop
from core import atomic, paths
from core import card as cardmod
from core.events import EventWriter
from tools import act, trade  # noqa: F401  (trade registers trade.seller_rules)
from tools import market_files as mf
from tools.registry import Ctx, call

MAX_TURNS = 12
KNOWN_AT = 0.5             # a guess this sure that names a confirmed customer makes the buyer "not first time"
CANDIDATES = 5
FIT_SPARE = 1.05           # food_fit: a unit must last the buyer's stated hours with 5% to spare
PROMISED_S = 1200          # a unit we signed a receipt for waits this long for its checkout before we offer it again
OPEN_TTL = 900             # a talk with no message for this long is dropped and its unit freed (11/6 22:4x: 63 talks
                           # the buyer never ended held 62 units, every unit of the six SKUs buyers asked for most)
NEEDS_EVERY = 600          # food_review_need: the needed hours are read again from the ledger this often
_needs = {"ts": 0, "by_sku": {}}


def review_needs(now=None, hours=24):
    """{sku: the most hours a buyer said it needed} from reviews in the public ledger ("Only 115.2 h of shelf life
    left; I needed 126.0 h"), the last `hours`, cached NEEDS_EVERY. Only lines with 'I needed' are parsed."""
    import re
    now = now or time.time()
    if now - _needs["ts"] < NEEDS_EVERY:
        return _needs["by_sku"]
    by = {}
    for f in sorted((paths.memory() / "market").glob("ledger-*.jsonl"))[-2:]:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if "I needed" not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                x = d.get("data") or {}
                m = re.search(r"I needed (\d+(?:\.\d+)?) h", x.get("review") or "")
                sku = (x.get("receipt") or {}).get("item")
                if m and sku and d.get("ts", 0) >= now - hours * 3600:
                    by[sku] = max(by.get(sku, 0), float(m.group(1)))
    _needs.update(ts=now, by_sku=by)
    return by


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load_key(path):
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def sign(key, receipt):
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


class SellerCore:
    def __init__(self, shop_name, key, model_call=None, clock=time.time):
        self.name, self.key, self.model_call, self.clock = shop_name, key, model_call, clock
        self.open = {}
        self.armed = set()
        self.promised = {}              # unit -> when we signed a receipt for it (blocked_units)
        self._blocked = (0, set())          # visits whose experiment arms are recorded (seller.arm)
        self.events = EventWriter("seller", source="seller")
        self.tool_events = EventWriter("seller", source="tools")    # one open file for every turn's tool calls

    def is_customer(self, name):
        live = atomic.read_json(paths.memory() / "facts" / "live.json", default={}) or {}
        return bool(((live.get("customer") or {}).get(name) or {}).get("deals_with_us"))

    # --- what fits

    def blocked_units(self, now):
        """Units the kit's checkout will refuse (11/12 04h: one pillow cover held by a checkout a buyer never paid was
        promised 159 times from 11/11 13:48 - every deal on it died at payment, settled sales fell from ~25% of
        visits to ~3%): units refused at our checkout in the last REFUSED_S (read from the kit log), and units we
        signed a receipt for in the last PROMISED_S that are still in stock (a checkout may be holding them)."""
        if now - self._blocked[0] > 60:
            refused = set()
            kit = paths.root() / "logs" / "market-kit.log"
            try:
                with open(kit, "rb") as fh:
                    fh.seek(0, 2)
                    fh.seek(max(0, fh.tell() - 600_000))
                    for line in fh.read().decode("utf-8", "replace").splitlines():
                        if "checkout refused" in line:
                            m = re.search(r"unit (u-[0-9a-f]+) is not in stock", line)
                            if m:
                                refused.add(m.group(1))
            except OSError:
                pass
            self._blocked = (now, refused)
        recent = {u for u, ts in self.promised.items() if now - ts < PROMISED_S}
        return self._blocked[1] | recent

    def candidates(self, ctx, ex, text):
        """Units that fit what the buyer asked for: a named SKU first, else category or name words."""
        cat, now = mf.catalog(ctx), ctx.now()
        taken = {s["unit"] for s in self.open.values() if s.get("unit")} | self.blocked_units(now)
        units = [u for u in mf.inventory(ctx).get("units", []) if u.get("grade") != "expired" and u["unit"] not in taken
                 and (u.get("cert") or not ctx.strategy.get("certified_only")) and not mf.on_hold(ctx, u["sku"], cat)]
        want = text.lower()
        self.held = any(mf.on_hold(ctx, s, cat) for s in ex["skus"])
        named = [u for u in units if u["sku"] in ex["skus"]]
        if not named:
            def words(u):
                c = cat.get(u["sku"]) or {}
                return [w for w in [(c.get("category") or u.get("category") or "").split(">")[-1],
                                    c.get("name") or u.get("name")] if w]
            named = [u for u in units if any(w.lower() in want for w in words(u))]
        # 11/5 01:2x (user: seller unit choice): a food buyer that states the hours it needs gets a unit that lasts them
        # with 5% to spare - selling the first to expire drew 61 of 109 ratings of 3 or less in 6 h ("Only 306 h of
        # shelf life left; I needed 361 h") while longer units of the same SKU sat in stock
        need = ex.get("need_hours")
        self.short = None
        if need and self.playbook(ctx)["food_fit"]:
            def lasts(u):
                h = mf.hours_left(u.get("expires_at"), now)
                return u.get("kind") != "food" or h is None or h >= need * FIT_SPARE
            fit = [u for u in named if lasts(u)]
            if named and not fit:
                most = max(mf.hours_left(u.get("expires_at"), now) or 0 for u in named)
                self.short = {"need": need, "most": most, "sku": named[0]["sku"]}
            named = fit
        # 11/5 02:4x (user: food is for stars and deals - sell it so buyers can use it): a buyer that states no hours
        # may need up to 75% of the shelf life (the market's use windows are 10/25/50/75%); buyer-02 states none and
        # rated 79 of 227 grade B/C units 2 stars in 12 h while A units drew 4.83. Such a buyer gets only a unit with
        # food_unknown_share of its shelf life left (x1.05); the reply asks for the hours, so food_fit can serve it
        # 11/5 03:0x (user: raise the bar we sell food at - buyers say how long they need it): a buyer that states no
        # hours gets only a unit lasting the most hours buyers wrote they needed for that SKU (x1.05); buyer-02 states
        # none and wrote 126 h for G200e39a3 (shelf 168) and 540 h for G9a240832 (shelf 720), 75% of the shelf
        if not need and self.playbook(ctx)["food_review_need"]:
            needs = review_needs(now)

            def enough(u):
                h, want = mf.hours_left(u.get("expires_at"), now), needs.get(u["sku"])
                return u.get("kind") != "food" or h is None or not want or h >= want * FIT_SPARE
            fit = [u for u in named if enough(u)]
            if named and not fit:
                most = max(mf.hours_left(u.get("expires_at"), now) or 0 for u in named)
                self.short = {"need": None, "most": most, "sku": named[0]["sku"], "wanted": needs.get(named[0]["sku"])}
            named = fit
        unknown = self.playbook(ctx)["food_unknown_share"]
        if not need and unknown:
            def fresh(u):
                h, sh = mf.hours_left(u.get("expires_at"), now), (cat.get(u["sku"]) or {}).get("shelf_hours")
                return u.get("kind") != "food" or h is None or not sh or h >= unknown * sh * FIT_SPARE
            fit = [u for u in named if fresh(u)]
            if named and not fit:
                most = max(mf.hours_left(u.get("expires_at"), now) or 0 for u in named)
                self.short = {"need": None, "most": most, "sku": named[0]["sku"]}
            named = fit
        # one unit per SKU (the one that expires first), so the model sees choices, not copies
        # 11/5 02:5x (user: sell a lot, and earn good ratings by selling good quality): a food buyer that states no hours
        # gets the freshest unit of the SKU; the older ones go to buyers whose stated hours they cover (food_fit)
        fresh_first = not need and self.playbook(ctx)["food_fresh_unknown"]

        def order(u):
            exp = u.get("expires_at") or float("inf")
            if fresh_first and u.get("kind") == "food":
                exp = -exp
            return exp, u.get("supplied_at") or 0
        best = {}
        for u in sorted(named, key=order):
            best.setdefault(u["sku"], u)
        rows = []
        for u in list(best.values())[:CANDIDATES]:
            c = cat.get(u["sku"]) or {}
            h = mf.hours_left(u.get("expires_at"), now)
            rows.append({"unit": u["unit"], "sku": u["sku"], "name": c.get("name") or u.get("name"),
                         "kind": u.get("kind"), "cost": u["cost"], "list_price": u.get("list_price") or c.get("list_price"),
                         "grade_now": mf.current_grade(u, c.get("shelf_hours"), now),
                         "hours_left": round(h, 1) if h is not None else None,
                         "has_cert": bool(u.get("cert")), "same_sku_in_stock": sum(1 for x in units if x["sku"] == u["sku"])})
        return rows

    def missed(self, ctx, task_id, ex, text):
        """A buyer asked for what we do not have: what it named (SKUs, categories), for restock (me.missed)."""
        cat = mf.catalog(ctx)
        want = text.lower()
        cats = sorted({d["category"] for d in (mf.read(ctx, "demand.json", default=[]) or [])
                       if d.get("item") and d["item"].lower() in want})
        cats += sorted({(cat.get(s) or {}).get("category") for s in ex["skus"] if cat.get(s)} - set(cats))
        self.events.emit("seller.missed", {"skus": ex["skus"][:5], "categories": cats[:5], "excerpt": ex["excerpt"],
                                           "first_time": None}, task=task_id)

    # --- one message

    def handle(self, task_id, text, deadline=None):
        """{"state": "input_required"|"completed"|"rejected", "say": str, "receipt": dict|None}"""
        ctx = Ctx(caller="seller", clock=self.clock, _writer=self.tool_events, keys={"task": task_id})   # C1
        strategy = ctx.strategy
        try:                        # design R12, D-34 (user 11/6 12:3x): the shop's A/B arms, by the visit (task id)
            from harness import experiment
            arms, patch = experiment.assign("shop", task_id, self.clock())
            if patch:               # the B arm's numbers on top of the strategy for this visit only
                ctx._strategy = strategy = experiment._merge(strategy, patch)
            if arms and task_id not in self.armed:
                self.armed.add(task_id)
                self.events.emit("seller.arm", {"experiment": arms}, task=task_id)
        except Exception:           # the ledger must never stop a sale: run as A
            pass
        self.events.context(strategy_version=strategy.get("version"), epoch=strategy.get("epoch"),
                            card_version=cardmod.load()[0])
        ex = call(ctx, "calc.extract", {"text": text})["result"]
        for t in [t for t, o in self.open.items() if t != task_id and o.get("seen", 0) < self.clock() - OPEN_TTL]:
            self.events.emit("seller.expired", {"unit": self.open.pop(t).get("unit")}, task=t)
        st = self.open.get(task_id)
        if st is not None:
            st["seen"] = self.clock()
        self.events.emit("seller.request", {"excerpt": ex["excerpt"], "looks_like_instruction": ex["looks_like_instruction"],
                                            "price": ex["price"], "first": st is None}, task=task_id)
        if st is None:
            cands = self.candidates(ctx, ex, text)
            if not cands:
                if self.held:                    # on hold (playbook.hold): true, and not a missed request for restock
                    return self._close(task_id, None, "rejected", "Sorry, we are not selling that item right now.")
                self.missed(ctx, task_id, ex, text)
                if self.short:                   # say the true hours rather than sell a unit that will not last
                    if self.short["need"] is None:
                        wanted = (f", less than the {self.short['wanted']:g} h buyers have needed it for"
                                  if self.short.get("wanted") else "")
                        return self._close(task_id, None, "rejected",
                                           f"Our units of {self.short['sku']} have {self.short['most']:.0f} h of shelf "
                                           f"life left{wanted}. If that is enough for you, ask again with the hours you "
                                           "need (for example 'I need it to last 100 hours').")
                    return self._close(task_id, None, "rejected",
                                       f"Sorry, our units of {self.short['sku']} keep at most {self.short['most']:.0f} h, "
                                       f"less than the {self.short['need']:g} h you need.")
                return self._close(task_id, None, "rejected", "Sorry, we have none of that in stock right now.")
            st = self.open[task_id] = {"unit": None, "candidates": cands, "ask": None, "offer": None, "turns": 0,
                                       "seen": self.clock(),
                                       "lines": []}
            g = call(ctx, "calc.guess_counterpart", {"task": task_id, "messages": [text]})["result"]
            top = (g.get("candidates") or [None])[0]
            st["guess"] = {"top": top and top["name"], "confidence": top and top["confidence"], "accuracy": g.get("accuracy")}
            st["first_time"] = not (top and top["confidence"] >= KNOWN_AT and self.is_customer(top["name"]))
            self.events.emit("guess.made", {"task": task_id, "top": st["guess"]["top"],
                                            "confidence": st["guess"]["confidence"]}, task=task_id)
        st["turns"] += 1
        if ex["price"] is not None:
            st["offer"] = ex["price"]       # whether a message without a number accepts our ask is the model's call
        st["lines"].append({"from": "buyer", "excerpt": ex["excerpt"], "price": ex["price"],
                            "claimed_grade": ex["claimed_grade"], "looks_like_instruction": ex["looks_like_instruction"]})
        if st["turns"] > MAX_TURNS:
            return self._close(task_id, st, "rejected", "We could not agree. Thank you for asking.")

        ctx.situation = {"unit": st["unit"], "candidates": [c["unit"] for c in st["candidates"]], "offer": st["offer"],
                         "ask": st["ask"], "first_time": st.get("first_time")}
        t0 = time.perf_counter()
        mode = self.playbook(ctx)["mode"]
        if mode == "rules":
            rule = self.jev_move(ctx, st, ex, task_id)      # user 11/10: Jev picks the move tool (rules or forged)
        else:
            rule = self.rule_decide(ctx, st, ex) if mode == "shadow" else None
        if mode == "rules" and rule is not None:
            d, trace = rule, []
        else:
            if mode == "rules":
                self.events.emit("exec.anomaly", {"role": "seller", "kind": "turn_needs_agent", "then": "agent",
                                                  "situation": {"turn": st["turns"], "offer": st["offer"],
                                                                "ask": st["ask"], "excerpt": ex["excerpt"][:120]}},
                                 task=task_id)
            d, trace = loop.decide("seller", ctx, self.situation_text(ctx, st), deadline=deadline,
                                   model_call=self.model_call)
            if mode == "shadow":                         # compare before switching (HARNESS 14.5)
                self.events.emit("seller.shadow", {"rule": rule and {k: rule.get(k) for k in ("action", "price", "unit")},
                                                   "agent": d and {k: d.get(k) for k in ("action", "price", "unit")},
                                                   "turn": st["turns"], "offer": st["offer"], "ask": st["ask"]},
                                 task=task_id)
        guard = None
        if d is None:
            d, guard = self.guard(ctx, st), "no valid move from the model: " + "; ".join(
                str(t["observation"])[:80] for t in trace[-2:])
        self.events.emit("seller.decision", {"action": d["action"], "price": d.get("price"), "unit": d.get("unit"),
                                             "tool": d.get("tool"),
                                             "ms": round((time.perf_counter() - t0) * 1000), "tool_calls": len(trace),
                                             "guard": guard, "thought": d.get("thought", "")[:200]}, task=task_id)
        if d["action"] == "reject":
            return self._close(task_id, st, "rejected", d["say"])
        st["unit"] = d.get("unit") or st["unit"]
        if d["action"] == "accept":
            u = mf.unit(ctx, st["unit"])
            receipt = {"shop": self.name, "item": u["sku"], "price": d["price"], "task": task_id, "unit": st["unit"],
                       "cert": True}
            if d.get("claim"):
                receipt["claim"] = d["claim"]
            if d.get("warranty_hours"):
                receipt["warranty_hours"] = d["warranty_hours"]
            receipt = sign(self.key, receipt)
            self.promised[st["unit"]] = self.clock()
            return self._close(task_id, st, "completed", d["say"], receipt=receipt, decision=d, cost=u["cost"])
        st["ask"] = d["price"]
        st["lines"].append({"from": "us", "price": d["price"], "say": d["say"]})
        return {"state": "input_required", "say": d["say"], "receipt": None}

    # --- the rules (HARNESS 14 step 2, user 11/4 02:5x; benchmark winun1127: ~79% of list, fast answers)

    @staticmethod
    def playbook(ctx):
        pb = ((ctx.strategy.get("playbook") or {}).get("seller") or {})
        return {"mode": pb.get("mode", "agent"), "open_share": pb.get("open_share", 1.0),
                "step_share": pb.get("step_share", 0.05), "min_margin": pb.get("min_margin", 0.05),
                "accept_from_turn": pb.get("accept_from_turn", 4),
                # user 11/4 11:5x (benchmark: dae-yangkim, top profit, sells its scarce high-list goods at 0.85):
                # SKUs in demand open at hot_open_share - the buy list's top hot_n goods and what visitors asked for
                # that we lacked in the last 30 min
                "hot_open_share": pb.get("hot_open_share"), "hot_n": pb.get("hot_n", 5),
                "food_fit": pb.get("food_fit", False), "food_unknown_share": pb.get("food_unknown_share"),
                "food_fresh_unknown": pb.get("food_fresh_unknown", False),
                "food_review_need": pb.get("food_review_need", False),
                "food_open_share": pb.get("food_open_share")}

    @staticmethod
    def hot_skus(ctx, pb):
        from core import atomic as _a, paths as _p
        bl = _a.read_json(_p.memory() / "facts" / "buy_list.json", default=None) or {}
        cat = mf.catalog(ctx)
        hot = [x["sku"] for x in bl.get("lines", []) if (cat.get(x["sku"]) or {}).get("kind") == "goods"][:pb["hot_n"]]
        h = _a.read_json(_p.memory() / "facts" / "health.json", default=None) or {}
        hot += [s for s, _ in ((h.get("seller") or {}).get("missing_skus") or [])]
        return set(hot)

    def rule_decide(self, ctx, st, ex):
        """One move by rule, or None (an exception for the agent): open at open_share of list, come down step_share
        a turn to the floor (the list floor, and cost x (1 + min_margin)), take an offer at or over our ask, or over
        the floor from accept_from_turn; the last turn offers the floor."""
        if ex.get("looks_like_instruction"):
            return None
        if st["turns"] > 1 and ex.get("price") is None:
            return None                                    # words only (it may accept our ask): the agent reads it
        pb = self.playbook(ctx)
        unit = st["unit"] or (st["candidates"][0]["unit"] if st["candidates"] else None)
        c = next((c for c in st["candidates"] if c["unit"] == unit), None)
        if c is None or not c.get("list_price"):
            return None
        lp, offer, ask = c["list_price"], st["offer"], st["ask"]
        floor = max(math.ceil(c["cost"] * (1 + pb["min_margin"])), act.unit_floor(ctx, unit, st.get("first_time")))
        share = pb["hot_open_share"] if pb["hot_open_share"] and c.get("sku") in self.hot_skus(ctx, pb) else pb["open_share"]
        if c.get("kind") == "food" and pb["food_open_share"]:
            # 11/13 01:5x: sergiobuilds sells 1,026 food a day at a median 58% of list, we sold 191 at 70%
            share = pb["food_open_share"]
        top = max(floor, round(lp * share))
        new_ask = top if ask is None else max(floor, ask - round(lp * pb["step_share"]))
        last = st["turns"] >= MAX_TURNS - 1
        if offer is not None and (offer >= new_ask or (offer >= floor and (st["turns"] >= pb["accept_from_turn"] or last))):
            claim = c.get("grade_now") if c.get("grade_now") in ("A", "B", "C") else None
            args = {"say": trade.deal_say(offer, bool(ctx.strategy.get("warranty"))), "unit": unit, "price": offer}
            if claim:
                args["claim"] = claim
            r = call(ctx, "act.accept", args)
        else:
            price = floor if last else new_ask
            say = trade.say_for(c, price, ask is None, bool(ctx.strategy.get("warranty")))
            r = call(ctx, "act.counter", {"price": price, "say": say, "unit": unit})
        if not r["ok"]:
            return None
        return dict(r["result"], thought="rules")

    def turn_view(self, ctx, st):
        """(view for harness.jev and forged tools, candidate, unit) or (None, None, None)."""
        from harness import jev
        pb = self.playbook(ctx)
        unit = st["unit"] or (st["candidates"][0]["unit"] if st["candidates"] else None)
        c = next((c for c in st["candidates"] if c["unit"] == unit), None)
        if c is None or not c.get("list_price"):
            return None, None, None
        floor = max(math.ceil(c["cost"] * (1 + pb["min_margin"])), act.unit_floor(ctx, unit, st.get("first_time")))
        v = jev.seller_view(c.get("kind"), c["list_price"], st["turns"], st["offer"], st["ask"],
                            not st.get("first_time"))
        v.update(cost=c["cost"], floor=floor, ask=st["ask"], offer=st["offer"], max_turns=MAX_TURNS,
                 name=c.get("name"), turn=st["turns"])
        return v, c, unit

    def jev_move(self, ctx, st, ex, task_id):
        """The turn's move through the tool Jev picks: the rules (trade.seller_rules) or a forged tool - the B arm of
        its A/B on the states it was made for, or an adopted one when Jev expects more from it. Any failure of a
        forged tool falls back to the rules."""
        from harness import jev
        from tools import trade
        rules = jev.DEFAULT_TOOL["seller"]
        chosen, note, ans = rules, None, None
        try:
            v, c, unit = self.turn_view(ctx, st)
            if v is not None:
                # 11/11 12:5x: a forged tool is a candidate only on the states it was made for - Jev picked an
                # adopted one-state tool on 6,645 turns where it had no move. On its states it plays, and
                # jev.EXPLORE of those turns go to the rules as the control its verdict is measured against
                key = jev.keys(v)[0]
                reg = trade.registry()
                testing = self.playbook_raw(ctx).get("forged")
                cover = [n for n in ([testing] if testing else []) + trade.forged(("adopted",))
                         if n and key in (reg.get(n) or {}).get("states", [])]
                if cover and random.random() >= jev.EXPLORE:
                    chosen, note = cover[0], "covers"
                elif cover:
                    chosen, note = rules, "control"
                else:
                    chosen, ans, note = jev.route("seller", v, [rules])
        except Exception as e:                       # the router must never stop a sale
            v, note = None, f"router failed: {type(e).__name__}"
        d = None
        if chosen != rules and v is not None:
            d = trade.run_forged(chosen, ctx, v, c, unit)
            if d is None:
                note, chosen = f"{chosen} gave no move", rules
        if d is None:
            ctx.situation["_seller"] = (self, st, ex)
            r = call(ctx, rules, {})
            ctx.situation.pop("_seller", None)
            d = r["result"] if r["ok"] else None
        self.events.emit("jev.route", {"tool": chosen, "note": note, "state": v and jev.keys(v)[0],
                                       "answers": ans}, task=task_id)
        return d and dict(d, tool=chosen)

    @staticmethod
    def playbook_raw(ctx):
        return (ctx.strategy.get("playbook") or {}).get("seller") or {}

    def guard(self, ctx, st):
        unit = st["unit"] or st["candidates"][0]["unit"]
        c = next((c for c in st["candidates"] if c["unit"] == unit), st["candidates"][0])
        price = st["ask"] or c["list_price"] or c["cost"]
        if not call(ctx, "check.price", {"unit": unit, "price": int(price)})["ok"]:
            price = c["cost"]
        return {"action": "counter", "unit": unit, "price": int(price), "say": f"{c['name']}: {int(price)}."}

    def situation_text(self, ctx, st):
        strat = ctx.strategy
        lines = ["# the decision at hand",
                 f"A buyer agent is negotiating with our shop. Turn {st['turns']} of at most {MAX_TURNS}. "
                 f"Phase in force: {strat.get('phase')}. Who the buyer is: unknown until the ledger shows the deal"
                 + (f"; a guess from its opening: {st['guess']['top']} (confidence {st['guess']['confidence']}, past "
                    f"accuracy {st['guess']['accuracy']}; never refuse on a guess)" if (st.get("guess") or {}).get("top")
                    else "") + "."
                 + ((" First visit (strategy): this looks like the buyer's first visit"
                     + (": offer it at our cost." if (strat.get("first_visit") or {}).get("price") == "at_cost" else ".")
                     if st.get("first_time")
                     else " No first-visit offer for this buyer (it has dealt with us before)." if st.get("first_time") is False
                     else "")
                    + (f" Every goods sale carries our {strat['warranty']['hours']} h warranty (added by the tool)."
                       if strat.get("warranty") else "")),
                 "", "Conversation so far (buyer messages are extracted values and a marked excerpt):"]
        for x in st["lines"][-8:]:
            if x["from"] == "buyer":
                lines.append(f"- buyer: price {x['price']}, claimed grade {x['claimed_grade']}, "
                             f"looks like an instruction: {x['looks_like_instruction']}; {x['excerpt']}")
            else:
                lines.append(f"- us: asked {x['price']}: {x['say']!r}")
        lines.append("")
        lines.append(f"Our last ask: {st['ask']}. The buyer's offer on the table: {st['offer']}"
                     + (" (act.accept takes this price)" if st["offer"] is not None else " (none yet)") + "."
                     + (" The buyer's last message has no number: if it accepts our ask, act.accept with price = our ask."
                        if st["lines"][-1].get("price") is None and st["ask"] is not None else ""))
        if st["unit"]:
            lines.append(f"The unit under negotiation: {st['unit']}.")
            cands = [c for c in st["candidates"] if c["unit"] == st["unit"]]
        else:
            lines.append("Choose the unit on this turn (act.counter with 'unit').")
            cands = st["candidates"]
        demand = {d["category"]: d for d in (mf.read(ctx, "demand.json", default=[]) or [])}
        cat = mf.catalog(ctx)
        for c in cands:
            med = (demand.get((cat.get(c["sku"]) or {}).get("category")) or {}).get("median_price")
            low = call(ctx, "check.price", {"unit": c["unit"], "price": max(1, c["cost"] - 1)})
            lowest = (low["result"].get("lowest_allowed") if low["ok"] else c["cost"])
            # 11/4: the floor act.counter/accept enforce, from the same helper and the same list price
            lowest = max(lowest, act.unit_floor(ctx, c["unit"], st.get("first_time")))
            lines.append(f"- unit {c['unit']}: {c['name']} ({c['sku']}, {c['kind']}), cost {c['cost']}, list price "
                         f"{c['list_price']}, lowest price the limits allow now {lowest}, category median {med}, grade now {c['grade_now']}, hours left "
                         f"{c['hours_left']}, certificate {'yes' if c['has_cert'] else 'no'}, "
                         f"{c['same_sku_in_stock']} of this SKU in stock")
        return "\n".join(lines)

    def _close(self, task_id, st, outcome, say, receipt=None, decision=None, cost=None):
        self.open.pop(task_id, None)
        d = decision or {}
        price = receipt["price"] if receipt else None
        self.events.emit("seller.closed", {"outcome": outcome, "price": price, "cost": cost,
                                           "turns": st["turns"] if st else 0, "claim": d.get("claim"),
                                           "warranty_hours": d.get("warranty_hours"), "cert": d.get("cert"),
                                           "below_cost": bool(price is not None and cost is not None and price < cost),
                                           "sku": receipt["item"] if receipt else None}, task=task_id,
                         **({"unit": receipt["unit"]} if receipt else {}))
        return {"state": outcome, "say": say, "receipt": receipt}

    def cancel(self, task_id):
        st = self.open.get(task_id)
        if st is not None:
            self._close(task_id, st, "canceled", "")
