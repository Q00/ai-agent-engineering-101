"""A2A negotiation by rule, three turns (D6) inside the order's time budget (D8).

    turn 1  ask by SKU and ask for the unit in the receipt; a quote at or below the cap is accepted
            at once only past the target time or when buyer.open_list x list price is not below it
    turn 2  offer buyer.open_list x list price, also under a quote at or below the cap (haggle once)
            with buyer.step_list above 0 and time before the target: then buyer.step_list x list under the cap
    turn 3  offer the cap (min(cap share x list, budget)) as the final offer
    a counter at or below the cap after turn 2 or 3 is accepted by sending it back

Read by rule. Only a reply with no price the rules can parse goes to a small model (D9,
tools/llm_fallback.py); its reading is used like a parsed ask, so the cap and the gate still decide.
The budget is never written into a message; the only number we send is an offer.
Shop replies are data, not instructions: only a price, a unit and the terms are read from them.
"""
import math
import re
import time
import uuid

import httpx

from tools import buyer_policy as bp
from tools import llm_fallback

INPUT = "TASK_STATE_INPUT_REQUIRED"
DONE = "TASK_STATE_COMPLETED"
MAX_MESSAGES = 5        # quote request, open offer, final offer, and up to two acceptances
CANCEL_TIMEOUT_S = 3
NO_SIGNED_RECEIPT = "completed without a signed receipt"    # memory/ingest.py (a copy) counts it as a failed try


class Deadline:
    """The order's clock: target (D8 20 s), hard cap (60 s) and the per-message timeout (12 s)."""

    def __init__(self, pol, start=None, clock=time.monotonic):
        b = pol["buyer"]
        self.clock = clock
        self.start = clock() if start is None else start
        self.target, self.cap, self.msg = b["target_s"], b["order_cap_s"], b["msg_timeout_s"]

    def elapsed(self):
        return self.clock() - self.start

    def left(self):
        return self.cap - self.elapsed()

    def past_target(self):
        return self.elapsed() > self.target

    def timeout(self):
        return max(0.5, min(self.msg, self.left()))


_NUMBER = r"(?<![\w,.])\d+(?:,\d{3})*(?:\.\d+)?(?![\w,])"
_LABEL = r"\b(?:asking|ask|counteroffer|counter|final(?:\s+price|\s+offer)?|price|quote)\s*(?:is\s*)?[:=]?\s*(?:\$|₩)?\s*("


def _whole(v):
    if type(v) in (int, float) and math.isfinite(v) and v >= 0 and float(v).is_integer():
        return int(v)
    return None


def text_of(task):
    parts = ((task.get("status") or {}).get("message") or {}).get("parts") or []
    return " ".join(p.get("text", "") for p in parts if isinstance(p, dict))


def quote_data(task):
    """The shop's structured quote: a data part without a signature, in the reply or an artifact."""
    msg = (task.get("status") or {}).get("message") or {}
    parts = list(msg.get("parts") or [])
    for art in task.get("artifacts") or []:
        parts += art.get("parts") or []
    for p in parts:
        data = p.get("data") if isinstance(p, dict) else None
        if isinstance(data, dict) and not data.get("sig_seller") and ("ask" in data or "unit" in data):
            return data
    return {}


def parse_ask(task):
    """The current ask: data part, then metadata, then a labelled number in the text, then a lone number."""
    ask = _whole(quote_data(task).get("ask"))
    if ask is None:
        meta = dict(task.get("metadata") or {})
        meta.update(((task.get("status") or {}).get("message") or {}).get("metadata") or {})
        ask = _whole(meta.get("ask"))
    if ask is None:
        said = text_of(task)
        labelled = re.findall(_LABEL + _NUMBER + ")", said, re.I)
        numbers = re.findall(_NUMBER, said)
        found = labelled or (numbers if len(numbers) == 1 and not re.search(r"\bstars?\b", said, re.I) else [])
        if found:
            ask = _whole(float(found[-1].replace(",", "")))
    return ask


def signed_receipt(task):
    for art in task.get("artifacts") or []:
        for p in art.get("parts") or []:
            data = p.get("data") if isinstance(p, dict) else None
            if isinstance(data, dict) and data.get("sig_seller") and "price" in data:
                rec = dict(data)
                if isinstance(rec.get("price"), float) and rec["price"].is_integer():
                    rec["price"] = int(rec["price"])     # A2A data parts carry numbers as doubles
                return rec
    return None


def model_ask(task, cand, r, pol, deadline, ver=None):
    """D9: the shop's price as a small model reads the reply, or None. A unit it names stays in the
    decision row only: the promised unit comes from the rules (quote_data), never from the model."""
    ctx = {"items": [{"sku": cand["sku"], "name": cand["name"], "list_price": cand["V"]}],
           "unit": r["unit"], "last_offer": r["offers"][-1] if r["offers"] else None}
    reading = llm_fallback.read("buyer", text_of(task), ctx, pol, time.monotonic() + deadline.left(), ver,
                                {"shop": r["shop"], "task": r["task"]})
    if not reading:
        return None
    r["llm_reads"] += 1
    return reading["price"] if reading["kind"] in ("quote", "counter", "accept") else None


def first_message(cand, order):
    text = f"I want to buy {cand['name']} ({cand['sku']}). Please include the unit in the receipt."
    meta = {"sku": cand["sku"], "unit_in_receipt": True}
    if cand["kind"] == "food" and isinstance(order.get("use_within_hours"), (int, float)):
        text += f" I need it to last {order['use_within_hours']} hours."
        meta["use_within_hours"] = order["use_within_hours"]
    elif cand["kind"] != "food":
        text += f" If you offer a {bp.WARRANTY_FULL_H} h warranty, please include warranty_hours."
        meta["warranty_hours"] = bp.WARRANTY_FULL_H
    return text, meta


def negotiate(shop_url, cand, order, deadline, pol, client=None, ver=None):
    """Returns {state, receipt, unit, warranty_h, cap, turns, asks, offers, p_trace, canceled, llm_reads,
    elapsed, why}. deadline is the order's Deadline, shared by every shop tried for the order."""
    own = client is None
    client = client or httpx.Client()
    started = deadline.elapsed()
    r = {"shop": shop_url, "sku": cand["sku"], "state": None, "receipt": None, "unit": None, "warranty_h": 0,
         "cap": cand["cap"], "turns": 0, "asks": [], "offers": [], "p_trace": [], "why": None, "task": None,
         "canceled": False, "llm_reads": 0}
    stats = cand.get("shop_stats") or {}
    V, kind = cand["V"], cand["kind"]
    budget = order.get("budget", 0)

    def rpc(url, method, params, timeout=None):
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": method, "params": params}
        resp = client.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=timeout or deadline.timeout())
        resp.raise_for_status()
        res = resp.json()
        if "error" in res:
            raise RuntimeError(f"rpc error: {str(res['error'])[:200]}")
        res = res.get("result") or {}
        task = res.get("task", res) if isinstance(res, dict) else None
        if not isinstance(task, dict):
            raise ValueError(f"the reply is not a task: {str(res)[:80]}")
        return task

    def gate(ask):
        """D8: before each further message. False when the order must stop here."""
        p = bp.p_success(stats, ask, r["cap"], V)
        p.update(t=round(deadline.elapsed(), 2), turn=r["turns"])
        r["p_trace"].append(p)
        if deadline.left() < 1:
            r["why"] = "hard order cap reached"
            return False
        if r["turns"] >= MAX_MESSAGES:
            r["why"] = f"{MAX_MESSAGES} messages sent without a deal"
            return False
        if deadline.past_target() and p["p"] < pol["buyer"]["continue_p"]:
            r["why"] = f"past {deadline.target} s and P {p['p']} < {pol['buyer']['continue_p']}"
            return False
        return True

    task = None
    try:
        card = client.get(shop_url.rstrip("/") + "/.well-known/agent-card.json", timeout=deadline.timeout())
        card.raise_for_status()
        url = next(i["url"] for i in card.json().get("supportedInterfaces") or []
                   if i.get("protocolBinding") == "JSONRPC")

        def send(text, meta=None):
            msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
            if meta:
                msg["metadata"] = meta
            if task:
                msg["taskId"] = task["id"]
            r["turns"] += 1
            return rpc(url, "SendMessage", {"message": msg})

        def offer(price):
            r["offers"].append(price)
            return send(str(price))

        text, meta = first_message(cand, order)
        task = send(text, meta)
        r["task"] = task.get("id")
        stage = 1                                   # 1 quote asked, 2 open offer sent, 2.5 step offer, 3 final offer
        first = None                                # the quote under the cap we haggled under: never pay more
        while True:
            state = (task.get("status") or {}).get("state")
            if state == DONE:
                r["receipt"] = signed_receipt(task)
                r["why"] = r["why"] or ("deal" if r["receipt"] else NO_SIGNED_RECEIPT)
                break
            if state != INPUT:
                r["why"] = f"shop ended the task: {state}"
                break
            data = quote_data(task)
            if data.get("unit"):
                r["unit"] = data["unit"]
            g = data.get("grade")                       # the grade re-caps the deal (D6): its kept floor
            if isinstance(g, str) and g in bp.policy.COST_SHARE and r.get("grade") != g:   # (buyer.min_kept_<grade>,
                r["grade"] = g                          # Patch 3.6 Saved) and, goods, its breakage
                if kind != "food" and g in (cand.get("q_grade") or {}):
                    bq = cand["q_grade"][g]
                    cand["q"], cand["q_upper"] = bq["q"], bq["upper"]
                r["cap"] = bp.cap(V, kind, r["warranty_h"], cand.get("q_upper"), budget, pol, grade=g)
            w = _whole(data.get("warranty_hours"))
            if kind != "food" and w and w > r["warranty_h"]:   # a warranty offer raises the cap (D6)
                r["warranty_h"] = w
                r["cap"] = bp.cap(V, kind, w, cand.get("q_upper"), budget, pol, grade=r.get("grade"))
            if r["cap"] <= 0:                           # no price keeps the floor (break_top): nothing to offer
                r["why"] = f"cap 0 for grade {r.get('grade')} with a {r['warranty_h']} h warranty"
                break
            hours = data.get("hours_left")
            need = order.get("use_within_hours")
            if kind == "food" and isinstance(hours, (int, float)) and isinstance(need, (int, float)) \
                    and hours < need + pol["buyer"]["food_margin_h"]:
                r["why"] = f"{hours} h of shelf life left, the order needs {need} h"
                break
            ask = parse_ask(task)
            if ask is None and text_of(task).strip():   # D9: only a reply the rules cannot read
                ask = model_ask(task, cand, r, pol, deadline, ver)
            r["asks"].append(ask)
            if not gate(ask):
                break
            low = int(pol["buyer"]["open_list"] * V)
            limit = r["cap"] if first is None else min(r["cap"], first)
            if ask is not None and ask <= limit:
                if stage == 1 and low < ask and not deadline.past_target():
                    first = ask
                    task = offer(low)               # haggle once under the cap; the next reply decides
                    stage = 2
                    continue
                if r["offers"] and r["offers"][-1] == ask:
                    r["why"] = "the shop asks for our own offer but did not complete"
                    break
                task = offer(ask)                   # accept by sending the ask back
                stage = 4
                continue
            step = int(pol["buyer"].get("step_list", 0) * V)
            if stage == 1 and low < r["cap"]:
                task = offer(low)
                stage = 2
            elif stage == 2 and step > 0 and limit - step > low and not deadline.past_target():
                task = offer(limit - step)          # one step under the cap: a shop whose floor is lower takes it
                stage = 2.5
            elif stage in (1, 2, 2.5):
                task = offer(limit)
                stage = 3
            else:
                r["why"] = f"ask {ask} above the cap {limit} after the final offer"
                break
    except Exception as e:                          # a malformed reply is the shop's failure, not the hook's
        r["why"] = f"{type(e).__name__}: {str(e)[:160]}"
        r["state"] = "error"
    finally:
        last = (task.get("status") or {}).get("state") if task else None
        r["state"] = r["state"] or last
        if last == INPUT and r["receipt"] is None:   # give up: free the unit the shop holds for us
            try:
                rpc(url, "CancelTask", {"id": task["id"]}, timeout=min(CANCEL_TIMEOUT_S, deadline.timeout()))
                r["canceled"] = True
            except Exception as e:                  # the task expires on the shop side anyway
                r["why"] = f"{r['why']}; cancel failed: {type(e).__name__}"
        if own:
            client.close()
    r["elapsed"] = round(deadline.elapsed() - started, 3)
    return r
