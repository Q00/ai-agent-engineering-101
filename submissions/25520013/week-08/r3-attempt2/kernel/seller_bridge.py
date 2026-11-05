"""Attempt-2 seller inside attempt 1's A2A server (human code, not editable by the model).

live/seller_patch.py inserts a short hook at the top of attempt 1's `Seller._execute` that calls `bridge(...)` and
`Bridge.handle`. The switch is the flag file <W>/a2/LIVE, read on every request:

- flag present and a new task -> attempt 2 opens it (seller.open) and answers every later message (seller.respond);
- a task attempt 1 opened (in Seller.open) stays on attempt 1 to the end, flag or not;
- a task attempt 2 opened stays on attempt 2 to the end, flag or not (its unit and ask are only known here);
- any exception while opening a new task -> metric a2_bridge_error and attempt 1 handles that request.

Game rules and protocol only, no strategy: candidate units are the inventory minus units held by an open deal of
either attempt, an accept is signed with attempt 1's sign() and receipt terms, a counter after round 8 ends the deal
(same cap as attempt 1), and every message carries the number the buyer parses ("asking N" / "Counteroffer: N").
Agent.decide blocks for seconds (model subprocess), so it runs in a worker thread.
"""

import asyncio
import os
import re
import threading
import time

from kernel import common as c
from kernel import memory
from kernel.agent import Agent
from kernel.replay import CLI, HARNESS, MARKET_BRIEF, MAX_UNITS, _tokens
from kernel.trajectory import Trajectory

FLAG = os.path.join("a2", "LIVE")
HOLD_S = 120  # attempt 1's HOLD_S: an attempt-2 deal idle this long is dropped and its unit freed
MAX_ROUNDS = 8  # attempt 1's cap: a counter at round 8 or later becomes a reject
TEXT_DECLINE = "Sorry, we can't take this order."
TEXT_NO_STOCK = "Sorry, we have none of that in stock."
TEXT_REJECT = "No deal."

_LOCK = threading.Lock()
_BRIDGE = None


def live_on(root):
    """True when the switch file <root>/a2/LIVE exists."""
    return os.path.exists(os.path.join(root, FLAG))


def declined_text(root, text):
    """성수님 결정 2026-11-05 (v2.2): a request carrying a policy.json shop.decline_texts phrase (case and runs of
    whitespace ignored, 12+ characters) is declined before stock is read, exactly as attempt 1 does."""
    pol = c.load_json(os.path.join(root, "policy.json"), {}) or {}
    lst = (
        (pol.get("shop") or {}).get("decline_texts") if isinstance(pol, dict) else None
    )
    if not isinstance(lst, list):
        return None
    t = " ".join((text or "").split()).casefold()
    for e in lst:
        e = " ".join(e.split()).casefold() if isinstance(e, str) else ""
        if len(e) >= 12 and e in t:
            return e
    return None


SKU_RE = re.compile(r"\b(G[0-9A-Za-z]{5,})\b")  # attempt 1's seller.py
NEED_RE = re.compile(
    r"(?<!\d)(?<!\d[.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h|시간)(?![0-9A-Za-z])",
    re.I,
)
NEED_MARGIN_H = 0.25  # attempt 1 v2.1: the settle lands minutes after the quote
# 성수님 결정 2026-11-06: with no stated need, food under half its shelf is not offered. Our reviewed sales to 11-05
# 18h: food with <50% shelf left averaged 4.21 stars (68 of 413 two-star), 50-75% 4.52, >=75% 4.67; buyer-02, which
# never states its need, wants 75% of the shelf.
UNSTATED_MIN_SHARE = 0.5


def stated_need_hours(text, names=()):
    """Shelf life the request asks for, in hours, read as attempt 1 v2.1/v2.1a does: the largest '<number> h /
    hour(s) / 시간' after SKUs, product `names` and the hour tokens inside those names are removed; None when it
    names no hours. Attempt 1 added the rule on 11-05 (71 of 75 two-star food reviews read 'only N h left, I
    needed M h'); attempt 2 lost it and buyer-02 posted that complaint about us 47 times by 11-06 01:11."""
    t = SKU_RE.sub(" ", text or "")
    names = sorted({n for n in names if n}, key=len, reverse=True)
    for nm in names:
        t = re.sub(re.escape(nm), " ", t, flags=re.I)
    for tok in sorted(
        {m.group(0) for nm in names for m in NEED_RE.finditer(nm)},
        key=len,
        reverse=True,
    ):
        t = re.sub(
            r"(?<!\d)(?<!\d[.,])" + re.escape(tok) + r"(?![0-9A-Za-z])",
            " ",
            t,
            flags=re.I,
        )
    hs = [float(m.group(1).replace(",", "")) for m in NEED_RE.finditer(t)]
    return max(hs) if hs else None


def candidate_units(market_dir, text, held=()):
    """(units for the model, raw inventory unit by unit id). Built like kernel.replay.seller_units, read from the
    seller's own market dir, without the units in `held`."""
    inv = c.load_json(os.path.join(market_dir, "inventory.json"), {}) or {}
    cat = c.load_json(os.path.join(market_dir, "catalog.json"), {}) or {}
    if isinstance(cat, list):
        cat = {x.get("sku"): x for x in cat if isinstance(x, dict)}
    units, raw, now = [], {}, time.time()
    for u in inv.get("units") or []:
        if not isinstance(u, dict) or u.get("unit") in held:
            continue
        exp = u.get("expires_at")
        if u.get("grade") == "expired" or (
            isinstance(exp, (int, float)) and exp <= now
        ):
            continue  # market rule: an expired unit cannot be sold
        item = cat.get(u.get("sku")) or {}
        raw[u.get("unit")] = u
        units.append(
            {
                "unit": u.get("unit"),
                "sku": u.get("sku"),
                "grade": u.get("grade"),
                "cost": u.get("cost"),
                "expires_at": u.get("expires_at"),
                "hours_left": round((exp - now) / 3600, 1)
                if isinstance(exp, (int, float))
                else None,
                "name": item.get("name"),
                "list_price": item.get("list_price", item.get("price")),
                "category": item.get("category"),
            }
        )
    want = _tokens(text)
    hit = [u for u in units if want & (_tokens(u["name"]) | _tokens(u["category"]))]
    if not hit:
        hit = sorted(units, key=lambda u: len(repr(u)))
    need = stated_need_hours(
        text, [u["name"] for u in hit if u["hours_left"] is not None]
    )
    if (
        need is not None
    ):  # attempt 1 v2.1 rule: a dated unit that cannot last the stated need is not offered
        hit = [
            u
            for u in hit
            if u["hours_left"] is None
            or (u["expires_at"] - now) / 3600 >= need + NEED_MARGIN_H
        ]
    else:  # 성수님 결정 2026-11-06: no stated need -> a dated unit with under half its catalog shelf left is not offered

        def fresh(u):
            sh = (cat.get(u["sku"]) or {}).get("shelf_hours")
            if u["hours_left"] is None or not isinstance(sh, (int, float)) or sh <= 0:
                return True
            return (u["expires_at"] - now) / 3600 >= UNSTATED_MIN_SHARE * sh

        hit = [u for u in hit if fresh(u)]
    return hit[:MAX_UNITS], raw


def with_number(message, label, n):
    """The model's message, ending with '<label> N.' unless it already says exactly that. Buyers read the price
    from the text: ours by 'asking N' / 'Counteroffer: N', attempt 1's and others' by the last number."""
    tail = f"{label} {n}"
    if tail in message and not re.search(r"\d", message.split(tail)[-1]):
        return message
    return f"{message} {tail}.".strip()


class Bridge:
    """Attempt-2 deals of one seller process. Helpers come from attempt 1's seller.py, passed explicitly:

    sign(receipt) -> signed receipt; name: shop name; receipt_terms(unit, pol, stats=, price=, shelf_hours=);
    offer_in(text) -> int | None; opening_text(unit, pol, ask, stats, shelf_hours) -> str;
    metric(decision, outcome, pol=None); ledger() -> context manager with first_seen_unit(unit);
    policy() -> policy dict (attempt 1's refresh_policy, called once per new task); shelf(sku) -> hours | None;
    a1_held() -> unit ids held by attempt 1's open deals; data_part(dict) -> A2A part for the receipt artifact.
    """

    def __init__(
        self,
        root,
        market_dir,
        *,
        sign,
        name,
        receipt_terms,
        offer_in,
        opening_text,
        metric,
        ledger,
        policy,
        shelf,
        a1_held,
        data_part,
        agent=None,
        clock=time.time,
    ):
        self.root, self.market_dir = str(root), str(market_dir)
        self.sign, self.name, self.receipt_terms = sign, name, receipt_terms
        self.offer_in, self.opening_text, self.metric = offer_in, opening_text, metric
        self.ledger, self.policy, self.shelf = ledger, policy, shelf
        self.a1_held, self.data_part = a1_held, data_part
        self.clock = clock
        self.deals = {}
        self._agent = agent
        login = c.owner_login(self.root)
        self.secrets = (login,) if login else ()

    def agent(self):
        """The seller Agent, built once per process on first use."""
        with _LOCK:
            if self._agent is None:
                from kernel.llm import Terra

                traj = Trajectory(
                    os.path.join(self.root, "a2/trajectory.jsonl"), secrets=self.secrets
                )
                self._agent = Agent(
                    "seller",
                    os.path.join(HARNESS, "seller"),
                    Terra(traj, cli=CLI, secrets=self.secrets),
                    traj,
                    MARKET_BRIEF,
                    mem_sock=memory.sock_path(self.root),
                )
            return self._agent

    def wants(self, task_id, a1_open):
        """Should attempt 2 answer this request? Its own deals always; a new task only while the flag is on."""
        return task_id in self.deals or (not a1_open and live_on(self.root))

    def held(self):
        return set(self.a1_held()) | {
            d["unit"].get("unit") for d in self.deals.values()
        }

    async def _decide(self, kind, situation, ctx):
        return await asyncio.to_thread(self.agent().decide, kind, situation, ctx)

    def _metric(self, decision, outcome, pol=None):
        try:
            self.metric(decision, dict(outcome, a2=True), pol=pol)
        except Exception:  # noqa: BLE001 - bookkeeping never changes the answer
            pass

    async def handle(self, task_id, text, up, say):
        """Answer one request. False = attempt 1 should handle it (a new task whose open failed)."""
        now = self.clock()
        for tid in [
            k for k, d in self.deals.items() if now - d["t"] > HOLD_S and k != task_id
        ]:
            del self.deals[tid]
        deal = self.deals.get(task_id)
        if deal is None:
            try:
                reply = await self._open(task_id, text)
            except Exception as e:  # noqa: BLE001 - attempt 1 takes the request
                self.deals.pop(task_id, None)
                self._metric(
                    "a2_bridge_error",
                    {"stage": "open", "error": f"{type(e).__name__}: {str(e)[:120]}"},
                )
                return False
        else:
            try:
                reply = await self._respond(task_id, deal, text)
            except Exception as e:  # noqa: BLE001 - the deal is ours, attempt 1 cannot continue it
                self.deals.pop(task_id, None)
                self._metric(
                    "a2_bridge_error",
                    {
                        "stage": "respond",
                        "error": f"{type(e).__name__}: {str(e)[:120]}",
                    },
                )
                reply = ("reject", TEXT_REJECT, None)
        state, message, receipt = reply
        if receipt is not None:
            await up.add_artifact(parts=[self.data_part(receipt)], name="receipt")
        if state == "input":
            await up.requires_input(say(message))
        elif state == "complete":
            await up.complete(say(message))
        else:
            await up.reject(say(message))
        return True

    async def _open(self, task_id, text):
        masked = c.mask(text, self.secrets)
        if declined_text(self.root, text):
            self._metric("declined", {"request": masked[:80], "route": "decline_texts"})
            return "reject", TEXT_DECLINE, None
        units, raw = candidate_units(self.market_dir, text, self.held())
        if not units:
            self._metric("no_stock", {"request": masked[:80]})
            return "reject", TEXT_NO_STOCK, None
        pol = self.policy()
        decision, meta = await self._decide(
            "seller.open", {"request_text": masked, "units": units}, {"units": units}
        )
        if decision.get("action") != "offer":
            self._metric(
                "declined", {"request": masked[:80], "route": meta["route"]}, pol=pol
            )
            return "reject", decision.get("message") or TEXT_DECLINE, None
        uid, ask = decision["unit"], decision["ask"]
        if uid in self.held():  # another deal took it while the model was thinking
            self._metric("a2_unit_taken", {"unit": uid}, pol=pol)
            return "reject", TEXT_DECLINE, None
        u = raw[uid]
        sh = self.shelf(u["sku"])
        summary = next(x for x in units if x["unit"] == uid)
        self.deals[task_id] = {
            "unit": u,
            "summary": summary,
            "ask": ask,
            "rounds": 0,
            "t": self.clock(),
            "offers": [],
            "pol": pol,
            "sh": sh,
        }
        self._metric(
            "open",
            {
                "sku": u["sku"],
                "unit": uid,
                "cost": u.get("cost"),
                "ask": ask,
                "route": meta["route"],
            },
            pol=pol,
        )
        msg = decision.get("message") or self.opening_text(u, pol, ask, None, sh)
        return "input", with_number(msg, "asking", ask), None

    async def _respond(self, task_id, deal, text):
        deal["rounds"] += 1
        deal["t"] = self.clock()
        offer = self.offer_in(text)
        u, pol = deal["unit"], deal["pol"]
        situation = {
            "unit": deal["summary"],
            "ask": deal["ask"],
            "round": deal["rounds"],
            "buyer_offer": offer,
            "buyer_text": c.mask(text, self.secrets),
            "previous_offers": list(deal["offers"]),
        }
        decision, meta = await self._decide("seller.respond", situation, {})
        if offer is not None:
            deal["offers"].append(offer)
        move, price = decision.get("action"), decision.get("price")
        if (
            move == "accept"
            and offer is not None
            and isinstance(price, int)
            and price > offer
        ):
            move = "counter"  # accepting means the buyer's own offer; a higher price is a counteroffer
        if move == "counter" and deal["rounds"] >= MAX_ROUNDS:
            self._metric(
                "a2_round_cap", {"sku": u["sku"], "rounds": deal["rounds"]}, pol=pol
            )
            move, decision = "reject", {}
        if move == "accept":
            receipt = dict(
                {"shop": self.name, "item": u["sku"], "price": price, "task": task_id},
                **self.receipt_terms(
                    u, pol, stats=None, price=price, shelf_hours=deal["sh"]
                ),
            )
            receipt = self.sign(receipt)
            try:  # attempt 1's v1.3 cost layer, best effort
                with self.ledger() as L:
                    L.first_seen_unit(u)
            except Exception as e:  # noqa: BLE001
                self._metric(
                    "ledger_error",
                    {"unit": u.get("unit"), "error": str(e)[:120]},
                    pol=pol,
                )
            self._metric(
                "accept",
                {
                    "sku": u["sku"],
                    "unit": u["unit"],
                    "cost": u.get("cost"),
                    "price": price,
                    "below_cost": price < (u.get("cost") or 0),
                    "rounds": deal["rounds"],
                    "relax": None,
                    "claim": receipt.get("claim"),
                    "warranty_hours": receipt.get("warranty_hours", 0),
                    "route": meta["route"],
                },
                pol=pol,
            )
            del self.deals[task_id]
            return (
                "complete",
                decision.get("message")
                or f"Deal: {u.get('name') or u['sku']} at {price}.",
                receipt,
            )
        if move == "counter":
            deal["ask"] = price
            return (
                "input",
                with_number(
                    decision.get("message") or "", "Counteroffer:", price
                ).strip(),
                None,
            )
        self._metric(
            "reject",
            {
                "sku": u["sku"],
                "offer": offer,
                "rounds": deal["rounds"],
                "route": meta["route"],
            },
            pol=pol,
        )
        del self.deals[task_id]
        return "reject", decision.get("message") or TEXT_REJECT, None


def cancel(task_id):
    """The buyer left (A2A CancelTask): free the unit an attempt-2 deal held."""
    if _BRIDGE is not None:
        _BRIDGE.deals.pop(task_id, None)


def bridge(root, market_dir, **helpers):
    """The process-wide Bridge, built on the first call; later calls return it unchanged."""
    global _BRIDGE
    with _LOCK:
        if _BRIDGE is None:
            _BRIDGE = Bridge(root, market_dir, **helpers)
        return _BRIDGE
