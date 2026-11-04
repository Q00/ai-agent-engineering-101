"""R3 market_buyer hook:
    python hooks/market_buyer.py --shops {shops} --item {item} --budget {budget} --order {order}

Pure code: ranks (shop, SKU) by expected surplus (list price - pay, weighted by claim_honest / broken record),
skips own-owner shops, haggles with int offers capped below the budget and the list price, and prints the last
line {"shop", "receipt", "stars", "review", "accept"}. Walks away when the shop states a grade or shelf
life that our accept policy would refuse at checkout. Refusals of earlier orders (payments.jsonl -> reject events
with a reason_code) are counted once each in the metrics row of the run that ingested them.
v1.22: food orders get FOOD_DEADLINE_S (goods keep DEADLINE_S), and (shop, SKU) pairs that failed on shelf life are
skipped for food until the refused unit expires (state file SHELF_FILE in the R3 state dir).
"""
import argparse
import json
import os
import re
import time
import uuid
from pathlib import Path

import httpx

from common import volume_level, catch_up_stats, P, Recorder, emit, hook_event, ingest, read_json
from ledger import state_dir
from reputation import Reputation
import owner as OW

MAX_SHOPS = 3              # v1.20b: default only; policy buyer.max_shops overrides (attempt_allowed)
DEADLINE_S = 200           # the kit stops the buyer at 300 s
BASE_SHOPS = 3             # v1.20b: attempts 1-3 run as before; attempts 4..max_shops pass the time guard
EXTRA_WINDOW_S = 60        # v1.20b defaults (policy buyer.extra_attempt_window_s / expiry_guard_s override)
EXPIRY_GUARD_S = 60
FOOD_DEADLINE_S = 15       # v1.22: the market issues the next order ~20 s after this one (policy buyer.defaults.{kind}.deadline_s overrides)
RPC_FLOOR_S = 3            # v1.22: least timeout of one haggle message under a time-capped kind
SHELF_FILE = "shelf_refusals.json"
SHELF_TTL_S = 6 * 3600     # v1.22: skip a shelf-life refused (shop, SKU) this long when the unit's hours left are unknown
SHELF_HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*h (?:of )?shelf life", re.I)   # kit reason / our walk-away why


def kind_deadline_s(order, pol):
    """v1.22: hook deadline (s since start) for the order's kind: policy buyer.defaults.{kind}.deadline_s when it
    is a number in (0, 280], else FOOD_DEADLINE_S for food and DEADLINE_S for goods (unchanged)."""
    kind = order.get("kind") or "goods"
    blk = (((pol or {}).get("buyer") or {}).get("defaults") or {}).get(kind)
    v = blk.get("deadline_s") if isinstance(blk, dict) else None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 280:
        return v
    return FOOD_DEADLINE_S if kind == "food" else DEADLINE_S


def attempt_allowed(n_shops, elapsed, now, order, pol):
    """v1.20b: may the buyer start an attempt at one more shop? -> (ok, reason).
    n_shops = shops already tried for this order, elapsed = seconds since the hook started.
    Always: n_shops < buyer.max_shops (default MAX_SHOPS) and elapsed <= kind_deadline_s (v1.22; goods DEADLINE_S).
    Attempts past BASE_SHOPS (4th and later) also need elapsed < extra_attempt_window_s and, when the order
    carries 'expires' (epoch s), now < expires - expiry_guard_s. reason: None, 'max_shops', 'deadline',
    'guard_elapsed' or 'guard_expiry' (the last two are the time-guard hits counted in metrics)."""
    b = (pol or {}).get('buyer') or {}
    cap = b.get('max_shops', MAX_SHOPS)
    cap = cap if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1 else MAX_SHOPS
    if n_shops >= cap:
        return False, 'max_shops'
    if elapsed > kind_deadline_s(order, pol):
        return False, 'deadline'
    if n_shops < BASE_SHOPS:
        return True, None
    if elapsed >= float(b.get('extra_attempt_window_s', EXTRA_WINDOW_S)):
        return False, 'guard_elapsed'
    exp = order.get('expires')
    if isinstance(exp, (int, float)) and not isinstance(exp, bool) and \
            now >= float(exp) - float(b.get('expiry_guard_s', EXPIRY_GUARD_S)):
        return False, 'guard_expiry'
    return True, None


def attempt_deadline(n_shops, t0, order, pol):
    """v1.20b: haggle deadline for the next attempt. Attempts 1-3: t0 + kind_deadline_s (v1.22; goods DEADLINE_S).
    Later attempts also stop expiry_guard_s before the order expires."""
    d = t0 + kind_deadline_s(order, pol)
    exp = order.get('expires')
    if n_shops >= BASE_SHOPS and isinstance(exp, (int, float)) and not isinstance(exp, bool):
        b = (pol or {}).get('buyer') or {}
        d = min(d, float(exp) - float(b.get('expiry_guard_s', EXPIRY_GUARD_S)))
    return d


def rpc(url, text, task_id=None, timeout=60):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": "SendMessage", "params": {"message": msg}}
    r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=timeout).json()
    if "error" in r:
        return None
    return r["result"].get("task", r["result"])


def cancel(url, task_id):
    """Tell the shop we left so it can free the unit it held for us (A2A CancelTask). Best effort."""
    if not task_id:
        return
    try:
        httpx.post(url, json={"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": "CancelTask",
                              "params": {"id": task_id}}, headers={"A2A-Version": "1.0"}, timeout=10)
    except httpx.HTTPError:
        pass


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def text_of(t):
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def ask_of(t):
    s = re.sub(r"\(G[0-9A-Za-z]{5,}\)", " ", text_of(t))
    m = re.findall(r"asking (\d+)|Counteroffer: (\d+)", s)
    if m:
        return int(next(x for x in m[-1] if x))
    return None


def receipt_of(t):
    for a in (t or {}).get("artifacts") or []:
        for p in a.get("parts") or []:
            d = p.get("data")
            if isinstance(d, dict) and "price" in d and d.get("sig_seller"):
                return d
    return None


def endpoint(shop_url, timeout=20):
    card = httpx.get(shop_url.rstrip("/") + "/.well-known/agent-card.json", timeout=timeout).json()
    ifs = card.get("supportedInterfaces") or []
    return (ifs[0].get("url") if ifs else None) or card.get("url")


_G = r"(?<![a-z])([abc])(?![a-z])"                        # one grade letter, not inside a word
GRADE_RES = (
    re.compile(r"\bgrades?\s*[-:=(\[]?\s*([abc])(?![a-z])", re.I),          # grade C, grade-C, grade:C, grades: C, grade(C)
    re.compile(_G + r"[\s-]*grade\b(?!\s*[-:=(\[]?\s*[abc](?![a-z]))", re.I),   # C grade, C-grade (not "a grade C")
    re.compile(r"등급\s*[-:=(\[]?\s*" + _G, re.I),                 # 등급 C, 등급: C
    re.compile(_G + r"\s*등급", re.I),                              # C 등급, C등급
)


def stated_grades(text):
    """v1.4 (CR P12): every grade the text states, upper case. 'grade-C', 'C grade', '등급 C', 'C등급', 'grades: C',
    'grade(C)', case-insensitive. 'a grade C unit' reads as C only (the article is not a grade)."""
    t = str(text or "")
    return {m.group(1).upper() for rx in GRADE_RES for m in rx.finditer(t)}


def stated_misfit(text, accept):
    """What the shop said it ships, checked against our accept policy before haggling (saves a checkout that
    the kit would refuse). Case-insensitive; every "grade X" in the text is read and the reply counts only when
    they all agree (a product name with "grade A" in it next to a stated "grade C" is ambiguous: no walk-away).
    Returns a reason or None; silent shops are not penalised."""
    grades = stated_grades(text)
    ok = {str(g).upper() for g in accept.get("grades") or []}
    if len(grades) == 1 and ok and not grades <= ok:
        return f"stated grade {next(iter(grades))}"
    h = re.findall(r"(\d+(?:\.\d+)?)\s*h(?:ours?)? (?:of )?shelf life left", text, flags=re.I)
    if len(set(h)) == 1 and accept.get("min_hours_left") and float(h[0]) < accept["min_hours_left"]:
        return f"stated {h[0]} h shelf life"
    return None


def claim_refused(rec, accept):
    """Receipt-stage recheck: a claim outside the accepted grades (case-insensitive) is refused before the kit pays."""
    ok = {str(g).upper() for g in (accept or {}).get("grades") or []}
    c = str((rec or {}).get("claim") or "").strip().upper()
    return bool(ok and c and c not in ok)


RECEIPT_SIGNED = ("shop", "item", "price", "task")     # what the seller signs (kit verify_seller / our seller.sign)


def verify_receipt(rec, pub_b64):
    """ML Engineer finding (b). -> (ok, receipt_to_use, how). The seller signs canon({shop, item, price, task})
    with an int price; A2A data Parts carry numbers as doubles, so 123210 arrives as 123210.0 and canon() of the
    float ("123210.0") no longer matches the signed bytes. Verify against the payload exactly as received first;
    only if that fails and the price is an integral float, verify the int form (the bytes the seller signed, and
    what the kit does before its own check, market_kit.py:736). The int-normalised copy is returned only after a
    signature verifies with it; the original dict is never mutated. Unknown pubkey -> (None, rec, "no_key")."""
    import base64
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not pub_b64 or not isinstance(rec, dict) or not rec.get("sig_seller"):
        return None, rec, "no_key" if not pub_b64 else "no_sig"
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
        sig = base64.b64decode(rec["sig_seller"])
    except (ValueError, TypeError):
        return False, rec, "bad_key_or_sig"

    def ok(r):
        try:
            key.verify(sig, json.dumps({k: r.get(k) for k in RECEIPT_SIGNED}, sort_keys=True,
                                       separators=(",", ":"), ensure_ascii=False).encode())
            return True
        except InvalidSignature:
            return False

    if ok(rec):
        return True, rec, "as_received"
    p = rec.get("price")
    if isinstance(p, float) and p.is_integer():
        r2 = dict(rec, price=int(p))
        if ok(r2):
            return True, r2, "int_normalised"
    return False, rec, "mismatch"


def grade_c_terms(cand, order, pol, accept):
    """v1.4 (G): accept grade C when the surplus justifies it. Buyer value is the list price whatever the grade
    (agent.md: 0 only if food did not last use_within_hours, or goods broke), so a C unit is worth buying at a
    price that leaves room for the unpublished goods break risk: cap = min(our cap, list x accept_c.max_pay_frac),
    opening at list x accept_c.open_frac. Food shelf life (min_hours_left) is never relaxed. Returns
    (candidate copy with the C cap, accept with C) or (None, None) when the policy has no accept_c."""
    c = P.resolve(pol, order.get("category"), order.get("kind") or "goods", side="buyer")
    ac = c.get("accept_c") or {}
    if not ac.get("max_pay_frac"):
        return None, None
    lp = int(cand["list_price"])
    cap = min(int(cand["max_pay"]), int(lp * float(ac["max_pay_frac"])))
    if order.get("budget"):
        cap = min(cap, int(order["budget"]))
    if cap <= 0:
        return None, None
    c2 = dict(cand, max_pay=cap, open=max(1, min(int(cand["open"]), int(lp * float(ac.get("open_frac", 0.35))))),
              grade_c=True)
    a2 = dict(accept or {})
    a2["grades"] = sorted(set(a2.get("grades") or ["A", "B"]) | {"C"})
    return c2, a2


def over_cap(rec, cand, order):
    """Payment cap (BASE: never complete a checkout above the open mandate). The receipt price is the shop's;
    it must be an int (or integral float) <= our cap for this candidate = min(budget, max_pay_frac x list price,
    after the volume guard). Returns a reason or None."""
    try:
        price = P.as_int_price(rec.get("price"))
    except (TypeError, ValueError, P.PolicyError):
        return "price not an int"
    if price <= 0:                                      # v1.4 (CR new issue 5): a 0 / negative price is no deal
        return f"price {price} not positive"
    budget = int(order.get("budget") or 0)
    if budget and price > budget:
        return f"price {price} above budget {budget}"
    if price > int(cand["max_pay"]):
        return f"price {price} above cap {cand['max_pay']}"
    return None


def haggle(cand, order, pol, deadline, accept=None):
    capped = kind_deadline_s(order, pol) < DEADLINE_S   # v1.22: food; goods keep their normal timeouts

    def tmo(normal):
        """v1.22: under a time-capped kind a message waits at most until the deadline (at least RPC_FLOOR_S)."""
        return max(RPC_FLOOR_S, min(normal, deadline - time.time())) if capped else normal

    url = endpoint(cand["shop"]["url"], timeout=tmo(20))
    opening = f"I want to buy {cand['name']} ({cand['sku']})"
    need = (accept or {}).get("min_hours_left")
    if (order.get("kind") or "goods") == "food" and isinstance(need, (int, float)) and not isinstance(need, bool):
        opening += f". The unit must last at least {need:g} hours."    # v1.22: food only
    t = rpc(url, opening, timeout=tmo(60))
    rounds, last = 0, None
    why = stated_misfit(text_of(t), accept or {}) if state(t) == "TASK_STATE_INPUT_REQUIRED" else None
    if why == "stated grade C":                         # v1.4 (G): haggle on at the grade-C cap instead
        c2, a2 = grade_c_terms(cand, order, pol, accept)
        if c2 is not None:
            cand, accept, why = c2, a2, stated_misfit(text_of(t), a2)
    if why:
        cancel(url, t.get("id"))
        return None, {"rounds": 0, "state": "walked_away", "why": why}
    while t is not None and state(t) == "TASK_STATE_INPUT_REQUIRED" and time.time() < deadline and rounds < P.HAGGLE_ROUNDS:
        ask = ask_of(t)
        offer = P.next_offer(cand, pol, order, rounds, ask, cand.get("relax", 0.0))
        if last is not None and offer <= last and (ask is None or ask > cand["max_pay"]):
            if offer >= cand["max_pay"]:
                cancel(url, t.get("id"))
                break                                   # at our cap and the shop is above it: walk away
        last = offer
        # v1.22: an offer at or above the shop's ask accepts it (checkout step): normal timeout, never cut short
        t = rpc(url, str(int(offer)), t["id"], timeout=60 if ask is not None and offer >= ask else tmo(60))
        rounds += 1
    rec = receipt_of(t) if state(t) == "TASK_STATE_COMPLETED" else None
    sig = None
    if rec:
        sig, rec, how = verify_receipt(rec, cand.get("pubkey"))
        if sig is False:                                # the kit would refuse it too: no checkout attempt
            return None, {"rounds": rounds, "state": "receipt_refused", "why": f"signature {how}", "last_offer": last}
    cut = state(t) == "TASK_STATE_INPUT_REQUIRED" and rounds < P.HAGGLE_ROUNDS and time.time() >= deadline
    if state(t) == "TASK_STATE_INPUT_REQUIRED":
        cancel(url, t.get("id"))                        # deadline or round cap: free the shop's unit
    if rec and claim_refused(rec, accept) and str(rec.get("claim") or "").strip().upper() == "C" \
            and not cand.get("grade_c"):                # silent shop, C on the receipt: take it only under the C cap
        c2, a2 = grade_c_terms(cand, order, pol, accept)
        if c2 is not None and not over_cap(rec, c2, order):
            cand, accept = c2, a2
    if rec and claim_refused(rec, accept):
        return None, {"rounds": rounds, "state": "receipt_refused", "why": f"claim {rec['claim']}", "last_offer": last}
    if rec and over_cap(rec, cand, order):
        return None, {"rounds": rounds, "state": "receipt_refused", "why": over_cap(rec, cand, order), "last_offer": last}
    return rec, {"rounds": rounds, "state": state(t), "last_offer": last, "sig": sig,
                 "grade_c": bool(cand.get("grade_c")), "accept": accept, **({"deadline_cut": True} if cut else {})}


def load_shelf(path, now):
    """v1.22: shelf-life refusal memory {"shop|sku": {"until": epoch s, "why": text}} -> (live pairs, pruned?)."""
    d = read_json(path, {}) or {}
    pairs = (d.get("pairs") if isinstance(d, dict) else None) or {}
    live = {k: v for k, v in pairs.items() if isinstance(v, dict) and isinstance(v.get("until"), (int, float))
            and v["until"] > now}
    return live, len(live) != len(pairs)


def remember_shelf(mem, shop, sku, why, now):
    """v1.22: (shop, SKU) failed on shelf life (kit refusal or our walk-away). The shop keeps shipping its oldest
    unit, so the pair is skipped until that unit expires: now + the hours left stated in `why`, else SHELF_TTL_S."""
    if not shop or not sku:
        return
    m = SHELF_HOURS_RE.search(why or "")
    until = now + (float(m.group(1)) * 3600 if m else SHELF_TTL_S)
    mem[f"{shop}|{sku}"] = {"until": round(until, 1), "why": str(why or "")[:120]}


def shelf_refused(c, mem):
    return any(f"{k}|{c['sku']}" in mem for k in (c["shop"].get("url"), c["shop"].get("name")) if k)


def save_shelf(path, mem):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"v": 1, "pairs": mem}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item")
    ap.add_argument("--budget", type=int)
    ap.add_argument("--order", required=True)
    a = ap.parse_args()
    t0 = time.time()
    pol = P.load()
    rec = Recorder("market_buyer", policy_hash=pol["_hash"])
    md = Path(a.order).parent
    new = []
    seq, counts, ims = ingest(md, collect=new)
    rej = {}
    shelf_path = state_dir() / SHELF_FILE               # v1.22: next to reputation.json
    shelf, shelf_dirty = load_shelf(shelf_path, t0)
    for e in new:                                       # refusals of earlier orders, read once (ledger cursor)
        if e["type"] == "reject":
            rej[e["data"]["reason_code"]] = rej.get(e["data"]["reason_code"], 0) + 1
            why = e["data"].get("reason") or ""
            if e["data"]["reason_code"] == "shelf_life" or SHELF_HOURS_RE.search(why):
                remember_shelf(shelf, e["data"].get("shop"), e["data"].get("item"), why, t0)
                shelf_dirty = True
    order = read_json(a.order, {}) or {}
    if a.budget and not order.get("budget"):
        order["budget"] = a.budget
    shops = read_json(a.shops, []) or []
    catalog = read_json(md / "catalog.json", {}) or {}
    if isinstance(catalog, list):
        catalog = {x["sku"]: x for x in catalog}
    own = OW.resolve(pol)                               # names + runtime URLs; cached, never raises
    rep = Reputation(own_names=sorted(own["names"] | own["urls"]) + [pol["owner"]["github"]])
    rep.update()
    relax, nset = volume_level("buyer", pol)            # min-volume guard (>= 20 settles as buyer)
    excluded = sorted({s.get("name") or s.get("url") for s in shops if P.own_shop(s, pol, own)})
    stats, cinfo, serr = catch_up_stats(catalog)        # v1.3: once per process, after ingest; never raises
    # Reputation table: reputation.py stays primary because it dedupes breakage per purchase (finding a). In the
    # 2026-11-02 BREAK=1 mock the aggregates.reputation_table() buyer view counted 10 breaks for 5 purchases, so it
    # is only used when reputation.py has no row for a shop, and its broken count is logged for comparison.
    rtab, rsrc, agg_broken = rep.table(pol["buyer"]["reputation_prior"]), "reputation.py", None
    try:
        at = stats.reputation_table() if stats is not None and hasattr(stats, "reputation_table") else None
        if at:
            agg_broken = {k: v.get("broken") for k, v in at.items()}
            extra_rows = {k: v for k, v in at.items() if k not in rtab}
            if extra_rows:
                rtab, rsrc = {**extra_rows, **rtab}, "reputation.py+aggregates"
    except Exception as e:                              # noqa: BLE001
        serr = f"{type(e).__name__}: {str(e)[:150]}"
    cands = P.choose_offers(order, shops, catalog, pol, rtab, own, relax)
    food = (order.get("kind") or "goods") == "food"
    shelf_skipped = 0
    if food:                                            # v1.22: skip (shop, SKU) pairs refused on shelf life
        kept = [c for c in cands if not shelf_refused(c, shelf)]
        shelf_skipped = len(cands) - len(kept)
        cands = kept or cands                           # all skipped: fall back to the unfiltered ranking, not no deal
    for c in cands:
        c["relax"] = relax
        c["pubkey"] = own.get("pubkeys", {}).get(OW.norm_url(c["shop"].get("url")))
    accept = P.accept_policy(order, pol, relax)
    tried, out = [], None
    seen_shops = set()
    stop = None                                         # v1.20b: why the attempt loop stopped early
    for c in cands:
        if c["shop"]["url"] in seen_shops:
            continue
        now = time.time()
        ok, stop = attempt_allowed(len(seen_shops), now - t0, now, order, pol)
        if not ok:
            break
        dl = attempt_deadline(len(seen_shops), t0, order, pol)
        seen_shops.add(c["shop"]["url"])
        try:
            receipt, info = haggle(c, order, pol, dl, accept)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            receipt, info = None, {"error": f"{type(e).__name__}"}
            hook_event("fail", f"a2a:{order.get('order')}:{c['shop']['url']}",
                       {"order": order.get("order"), "stage": "a2a", "detail": str(e)[:200]}, "buyer")
        deal_accept = info.pop("accept", None) or accept   # v1.4: with C only for a deal made under the C cap
        if food and info.get("state") == "walked_away" and "shelf life" in (info.get("why") or ""):
            remember_shelf(shelf, c["shop"].get("url"), c["sku"], info["why"], time.time())
            shelf_dirty = True
        tried.append({"shop": c["shop"].get("name"), "sku": c["sku"], **info})
        if receipt:
            price = P.as_int_price(receipt["price"])
            out = {"shop": c["shop"]["url"], "receipt": receipt, "stars": 4,
                   "review": f"{c['name']} at {price}", "accept": deal_accept}
            break
    if out is None:
        out = {"shop": cands[0]["shop"]["url"] if cands else (shops[0]["url"] if shops else ""),
               "receipt": None, "stars": 3, "review": "no deal", "accept": accept}
    seq = hook_event("decision", f"buy:{order.get('order')}",
                     {"hook": "market_buyer", "what": "offer_selection", "candidates": len(cands), "tried": tried,
                      # v1.5: the ranking itself (top 6), so a run shows when reputation changed the choice
                      "ranked": [{"shop": c["shop"].get("name"), "sku": c["sku"], "score": round(c["score"], 1),
                                  "broken_rate": round(c.get("broken_rate", 0.0), 4)} for c in cands[:6]],
                      "accept": accept, "relax": relax, "settled": nset, "own_excluded": excluded, "own_source": own["source"],
                      "own_error": own["error"]}, "buyer")
    rec.write("buy", seq, outcome={"deal": out["receipt"] is not None,
                                   "price": (out["receipt"] or {}).get("price"), "candidates": len(cands),
                                   "tried": len(tried), "budget": order.get("budget"),
                                   "stop": stop, "guard_hit": stop in ("guard_elapsed", "guard_expiry"),
                                   "deadline_s": kind_deadline_s(order, pol),
                                   "deadline_hit": stop == "deadline" or any(x.get("deadline_cut") for x in tried),
                                   "shelf_skipped": shelf_skipped,
                                   "max_shops": (pol.get("buyer") or {}).get("max_shops", MAX_SHOPS),
                                   "list_price": next((c["list_price"] for c in cands if out["receipt"] and c["sku"] == out["receipt"].get("item")), None)},
              rejections=rej, ingest_ms=ims, ingested=counts,
              extra={"rep_source": rsrc, "stats": stats is not None, "stats_error": serr, "catch_up": cinfo, "agg_broken": agg_broken, "relax": relax, "settled": nset, "own_source": own["source"], "own_names": len(own["names"]), "own_urls": len(own["urls"]),
                     "own_excluded": len(excluded), "own_error": own["error"]})
    emit(out)
    if shelf_dirty:
        try:
            save_shelf(shelf_path, shelf)
        except OSError:                                 # bookkeeping only: the order's output is already out
            pass


if __name__ == "__main__":
    main()
