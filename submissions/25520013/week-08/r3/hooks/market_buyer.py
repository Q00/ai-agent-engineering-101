"""R3 market_buyer hook:
    python hooks/market_buyer.py --shops {shops} --item {item} --budget {budget} --order {order}

Pure code: ranks (shop, SKU) by expected surplus (list price - pay, weighted by claim_honest / broken record),
skips own-owner shops, haggles with int offers capped below the budget and the list price, and prints the last
line {"shop", "receipt", "stars", "review", "accept"}. Walks away when the shop states a grade or shelf
life that our accept policy would refuse at checkout. Refusals of earlier orders (payments.jsonl -> reject events
with a reason_code) are counted once each in the metrics row of the run that ingested them.
v1.22: food orders get FOOD_DEADLINE_S (goods keep DEADLINE_S), and (shop, SKU) pairs that failed on shelf life are
skipped for food until the refused unit expires (state file SHELF_FILE in the R3 state dir).
v1.28: candidates are re-ordered by each shop's record with us (SHOP_FILE): P(deal) x expected surplus share, and a
shop with a recent a2a error goes last (rerank).
v1.28b: a checkout the kit refused after a shop's receipt (reject event: unit not in stock, shelf life, ...) undoes
one of that shop's deals in SHOP_FILE and puts the shop last for SHOP_ERR_S, like an a2a error.
"""
import argparse
import json
import os
import re
import time
import sys
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
PAR_GRACE_S = 3            # PUSH 11-16: after the output, wait this long for halted parallel attempts, then exit
RPC_FLOOR_S = 3            # v1.22: least timeout of one haggle message under a time-capped kind
SHELF_FILE = "shelf_refusals.json"
SHELF_TTL_S = 6 * 3600     # v1.22: skip a shelf-life refused (shop, SKU) this long when the unit's hours left are unknown
SHELF_HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*h (?:of )?shelf life", re.I)   # kit reason / our walk-away why
SHOP_FILE = "shop_outcomes.json"   # v1.28: our recent attempts per (shop url, order kind), next to SHELF_FILE
SHOP_TTL_S = 3 * 3600              # v1.28: attempts older than this are forgotten (a shop may change its prices)
SHOP_KEEP = 60                     # v1.28: newest attempts kept per (shop, kind)
SHOP_ERR_S = 20 * 60               # v1.28: an a2a error (timeout, dead tunnel, bad JSON) this recent ...
SHOP_ERR_PEN = 0.1                 # ... multiplies the shop's weight by this
SHOP_PRIOR_RATIO = 0.70            # v1.28: prior price/list of a deal (all our deals to 2026-11-05 04:07 KST: 0.699)


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


# ---- v5 step 1 (owner 19:45 TF plan): buyer s-target. Patch 3.6 Saved per deal = (L - p) / (L - c), 1 if p <= c.
# cap(unit) = max(c, L - theta x (L - c)) with c = the unit's supply cost estimate from v5/state/ucost.json (public ledger,
# batch average). Off unless policy buyer.s_target.enabled; no cost / stale file / any error -> current caps unchanged.
_V5C = {"m": None, "u": {}}
_V5_UCOST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "v5", "state", "ucost.json")


def _find_unit(o, depth=0):
    if depth > 4:
        return None
    if isinstance(o, dict):
        u = o.get("unit")
        if isinstance(u, str) and u.startswith("u-"):
            return u
        for v in o.values():
            r = _find_unit(v, depth + 1)
            if r:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find_unit(v, depth + 1)
            if r:
                return r
    return None


def unit_of(t):
    try:
        for p in (t or {}).get("status", {}).get("message", {}).get("parts", []) or []:
            u = _find_unit(p.get("data"))
            if u:
                return u
        m = re.search(r"\bu-[0-9a-f]{12}\b", text_of(t))
        return m.group(0) if m else None
    except Exception:  # noqa: BLE001
        return None


_V5_ALLOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "v5", "state", "bandit.json")
_V5_ASSIGN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "v5", "state", "assign.jsonl")


def _u01(oid):
    """md5(order id) -> uniform [0,1); same id -> same value (same arm)."""
    import hashlib
    return int(hashlib.md5(str(oid).encode()).hexdigest(), 16) / float(1 << 128)


def s_target_theta(st, oid, meta=None):
    """P1 (DESIGN_v3 §5): theta for this order. Order of sources:
    1 s_target.alloc.enabled -> v5 allocation file (arms, probs, version; fresh, valid) by inverse CDF of md5(order id)
    2 s_target.theta_arms     -> arms[md5 % n] (22:27 test)
    3 s_target.theta          -> static
    Any error in 1 falls back to 2/3 (never blocks the order)."""
    meta = meta if meta is not None else {}
    th = float(st.get("theta", 0.85)); meta.update(src="static", prob=1.0, version=None)
    arms = st.get("theta_arms")
    if arms and oid:
        import hashlib
        n = len(arms); th = float(arms[int(hashlib.md5(str(oid).encode()).hexdigest(), 16) % n])   # unchanged 22:27 mapping
        meta.update(src="theta_arms", prob=1.0 / n)
    al = st.get("alloc") or {}
    if al.get("enabled") and oid:
        try:
            path = al.get("path") or _V5_ALLOC
            if time.time() - os.path.getmtime(path) > float(al.get("max_age_s", 3600)):
                raise ValueError("alloc stale")
            a = json.load(open(path))
            A, Pr = [float(x) for x in a["arms"]], [float(x) for x in a["probs"]]
            if len(A) != len(Pr) or not A or any(x < 0 for x in Pr) or abs(sum(Pr) - 1) > 1e-6 \
                    or any(not (0.70 <= x <= 0.97) for x in A):
                raise ValueError("alloc invalid")
            u, acc = _u01(oid), 0.0
            for x, q in zip(A, Pr):
                acc += q
                if u < acc:
                    th = x; meta.update(src="alloc", prob=q, version=a.get("version")); break
            else:
                th = A[-1]; meta.update(src="alloc", prob=Pr[-1], version=a.get("version"))
        except Exception as e:  # noqa: BLE001 - fall back to theta_arms / static
            meta["alloc_err"] = type(e).__name__
    meta["theta"] = th
    return th


def _log_assign(pol, order, unit, prior_cap, s_cap, meta, stage):
    """P1: one line per haggle decision -> v5/state/assign.jsonl (arm, propensity, version, which cap binds).
    Gated by s_target.log; any error is swallowed (never blocks the order)."""
    try:
        st = ((pol or {}).get("buyer") or {}).get("s_target") or {}
        if not st.get("log"):
            return
        rec = {"ts": round(time.time(), 3), "order": (order or {}).get("order"), "kind": (order or {}).get("kind"),
               "unit": unit, "stage": stage, "theta": meta.get("theta"), "src": meta.get("src"), "prob": meta.get("prob"),
               "version": meta.get("version"), "alloc_err": meta.get("alloc_err"), "prior_cap": prior_cap, "s_cap": s_cap,
               "bound": ("theta" if s_cap is not None and prior_cap is not None and s_cap < prior_cap
                         else ("prior(grade/max_pay)" if s_cap is not None else "no_s_cap"))}
        with open(st.get("log_path") or _V5_ASSIGN, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


def s_target_cap(cand, unit, pol, oid=None, meta=None):
    """-> int cap or None (fallback to current caps).
    11-15 22:4x CoS (owner t891u): optional s_target.theta_arms = [..]: theta per order by md5(order id) % n, so each
    arm's deals and orders can be counted from the public ledger (settle/order carry the order id)."""
    try:
        st = ((pol or {}).get("buyer") or {}).get("s_target") or {}
        if not st.get("enabled") or not unit:
            return None
        mt = os.path.getmtime(_V5_UCOST)
        if time.time() - mt > float(st.get("max_age_s", 1800)):
            return None
        if _V5C["m"] != mt:
            _V5C["u"] = json.load(open(_V5_UCOST)); _V5C["m"] = mt
        c = _V5C["u"].get(unit)
        L = float(cand["list_price"])
        if not c or L <= 0:
            return None
        c = float(c)
        if c >= L:
            return int(c)
        th = s_target_theta(st, oid, meta)
        return int(max(c, L - th * (L - c)))
    except Exception:  # noqa: BLE001 - never cost the order: fall back to the current caps
        return None


OOS_RE = re.compile(r"\b(?:none|no|not|nothing)\b[^.!?]{0,40}\bin stock\b|\bout of stock\b|\bsold out\b|\bno stock\b", re.I)


def oos_free_max(pol):
    """CoS 11-16: policy buyer.oos_free_max (int 0..4, default 0 = off): how many food out-of-stock rejections per
    order do not count toward max_shops. Time limits (15 s food deadline) are unchanged."""
    v = ((pol or {}).get("buyer") or {}).get("oos_free_max", 0)
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 4 else 0


def is_oos_reject(info):
    """A shop rejected the opening (round 0) saying it has none in stock."""
    i = info or {}
    return i.get("state") == "TASK_STATE_REJECTED" and not i.get("rounds") \
        and (i.get("rej_cls") == "oos" or bool(OOS_RE.search(i.get("rej_text") or "")))


def _rej_text(t):
    """CoS 11-16 02:0x, diagnostics only: the shop's rejection text (first 160 chars) for the failure breakdown."""
    try:
        return str(text_of(t) or "")[:160]
    except Exception:  # noqa: BLE001 - never cost the order
        return None


def item_misfit(rec, order):
    """CoS 11-16 02:0x: mirror of the kit's sign check (market_kit.py: receipt item must be the order's item or in
    order['acceptable']). -> reason or None. Only when the order file carries a non-empty 'acceptable' list; otherwise
    None (unchanged behaviour). Kiro Everyday shipped G20e8987c for 김치 orders: 20 dead orders in 4 h."""
    acc = (order or {}).get("acceptable")
    if not isinstance(acc, list) or not acc or not isinstance(rec, dict):
        return None
    item = rec.get("item")
    if item == order.get("item") or item in acc:
        return None
    return f"item {item} not in the order"


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


def _halted(cand):
    """PUSH 11-16: True when the parallel attempt loop has stopped this attempt (another shop gave a receipt)."""
    h = cand.get("_halt")
    return bool(h is not None and h.is_set())


def parallel_cfg(pol, kind):
    """PUSH 11-16 (owner t912u: raise the buyer score): policy buyer.parallel = {"enabled": bool, "width": 2..4,
    "max_shops": 4..16, "kinds": ["food"]} -> (width, max_shops), or (0, 0) = off (the sequential loop, unchanged).
    Food orders have 15 s; the sequential loop tries ~8 shops one by one and 230 of 811 food orders (6 h to 11-16
    17:30 KST) stopped at max_shops, 163 at the deadline, with 40-67 candidates left. Running `width` haggles at once
    tries more shops in the same 15 s; the first receipt wins and the others are cancelled."""
    p = ((pol or {}).get("buyer") or {}).get("parallel") or {}
    if not isinstance(p, dict) or p.get("enabled") is not True or kind not in (p.get("kinds") or ["food"]):
        return 0, 0
    w, m = p.get("width", 3), p.get("max_shops", 12)
    ok = lambda v, lo, hi: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi
    return (w, m) if ok(w, 2, 4) and ok(m, 4, 16) else (0, 0)


def haggle(cand, order, pol, deadline, accept=None):
    capped = kind_deadline_s(order, pol) < DEADLINE_S   # v1.22: food; goods keep their normal timeouts

    def tmo(normal):
        """v1.22: under a time-capped kind a message waits at most until the deadline (at least RPC_FLOOR_S)."""
        return max(RPC_FLOOR_S, min(normal, deadline - time.time())) if capped else normal

    diag = cand.get("rpc_diag", {})
    observations = cand.setdefault("haggle_observations", [])
    diag.update({"rpc_stage": "endpoint", "rpc_started": time.time()})
    url = endpoint(cand["shop"]["url"], timeout=tmo(20))
    diag.clear()
    if _halted(cand):                                   # PUSH 11-16: a parallel attempt already got a receipt
        return None, {"rounds": 0, "state": "halted"}
    opening = f"I want to buy {cand['name']} ({cand['sku']})"
    need = (accept or {}).get("min_hours_left")
    if (order.get("kind") or "goods") == "food" and isinstance(need, (int, float)) and not isinstance(need, bool):
        opening += f". The unit must last at least {need:g} hours."    # v1.22: food only
    diag.update({"rpc_stage": "first_rpc", "rpc_started": time.time()})
    t = rpc(url, opening, timeout=tmo(60))
    diag.clear()
    rounds, last = 0, None
    cap_stop = False
    why = stated_misfit(text_of(t), accept or {}) if state(t) == "TASK_STATE_INPUT_REQUIRED" else None
    if why == "stated grade C":                         # v1.4 (G): haggle on at the grade-C cap instead
        c2, a2 = grade_c_terms(cand, order, pol, accept)
        if c2 is not None:
            cand, accept, why = c2, a2, stated_misfit(text_of(t), a2)
    if why:
        cancel(url, t.get("id"))
        return None, {"rounds": 0, "state": "walked_away", "why": why}
    _bp = pol.get("buyer") or {}   # overhaul 2026-11-15 (Patch 3.6: saved = share of margin); B4: per-kind caps from v4 buyer agent
    gcap = ((_bp.get("grade_max_pay_kind") or {}).get(order.get("kind") or "goods")) or _bp.get("grade_max_pay") or {}
    if gcap and state(t) == "TASK_STATE_INPUT_REQUIRED":
        _sg = stated_grades(text_of(t))
        if len(_sg) == 1 and next(iter(_sg)) in gcap:
            cand = dict(cand, max_pay=min(int(cand["max_pay"]), int(int(cand["list_price"]) * float(gcap[next(iter(_sg))]))))
    if state(t) == "TASK_STATE_INPUT_REQUIRED":          # v5 step 1: s-target cap on the stated unit
        _m = {}; _prior = int(cand["max_pay"])
        _u = unit_of(t); _sc = s_target_cap(cand, _u, pol, order.get("order"), _m)
        _log_assign(pol, order, _u, _prior, _sc, _m, "stated")
        if _sc is not None:
            cand = dict(cand, max_pay=min(int(cand["max_pay"]), _sc), s_cap=_sc, s_unit=_u)
    while t is not None and state(t) == "TASK_STATE_INPUT_REQUIRED" and time.time() < deadline and rounds < P.HAGGLE_ROUNDS \
            and not _halted(cand):
        ask = ask_of(t)
        offer = P.next_offer(cand, pol, order, rounds, ask, cand.get("relax", 0.0))
        if last is not None and offer <= last and (ask is None or ask > cand["max_pay"]):
            if offer >= cand["max_pay"]:
                cap_stop = True
                cancel(url, t.get("id"))
                break                                   # at our cap and the shop is above it: walk away
        last = offer
        # v1.22: an offer at or above the shop's ask accepts it (checkout step): normal timeout, never cut short
        diag.update({"rpc_stage": "accept_possible_rpc" if ask is not None and offer >= ask else "haggle_rpc", "rpc_started": time.time()})
        t = rpc(url, str(int(offer)), t["id"], timeout=60 if ask is not None and offer >= ask else tmo(60))
        if diag.get("rpc_stage") == "haggle_rpc":
            observations.append({"deadline_remaining_s": deadline - diag["rpc_started"],
                                 "rpc_elapsed_s": time.time() - diag["rpc_started"], "state": state(t)})
        diag.clear()
        rounds += 1
    rec = receipt_of(t) if state(t) == "TASK_STATE_COMPLETED" else None
    sig = None
    if rec:
        sig, rec, how = verify_receipt(rec, cand.get("pubkey"))
        if sig is False:                                # the kit would refuse it too: no checkout attempt
            return None, {"rounds": rounds, "state": "receipt_refused", "why": f"signature {how}", "last_offer": last}
    if rec and item_misfit(rec, order):                 # CoS 11-16 02:0x: the kit would not sign it ("does not fit
        return None, {"rounds": rounds, "state": "receipt_refused",   # the order") and the order would die: try the next shop
                      "why": item_misfit(rec, order), "last_offer": last}
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
    if rec and gcap and str(rec.get("claim") or "").strip().upper() in gcap:   # overhaul 2026-11-15: claimed grade cap
        cand = dict(cand, max_pay=min(int(cand["max_pay"]), int(int(cand["list_price"]) * float(gcap[str(rec.get("claim")).strip().upper()]))))
    if rec and not cand.get("s_unit"):                  # v5 step 1: unit known only from the receipt
        _m = {}; _prior = int(cand["max_pay"])
        _sc = s_target_cap(cand, rec.get("unit"), pol, order.get("order"), _m)
        _log_assign(pol, order, rec.get("unit"), _prior, _sc, _m, "receipt")
        if _sc is not None:
            cand = dict(cand, max_pay=min(int(cand["max_pay"]), _sc), s_cap=_sc, s_unit=rec.get("unit"))
    if rec and over_cap(rec, cand, order):
        return None, {"rounds": rounds, "state": "receipt_refused", "why": over_cap(rec, cand, order), "last_offer": last}
    if rec and _halted(cand):                           # PUSH 11-16: another parallel attempt won; this receipt is not used
        return None, {"rounds": rounds, "state": "halted", "last_offer": last}
    return rec, {"rounds": rounds, "state": state(t), "last_offer": last, "sig": sig,
                 "grade_c": bool(cand.get("grade_c")), "accept": accept, **({"deadline_cut": True} if cut else {}),
                 **({"cap_stop": True} if cap_stop else {}),
                 # Reviewer 11-16 (4): shop text never leaves the hook (ledger -> memory LLM window); class label only
                 **({"rej_cls": "oos" if OOS_RE.search(_rej_text(t) or "") else "other"} if state(t) == "TASK_STATE_REJECTED" else {}),
                 **({"s_cap": cand["s_cap"], "s_unit": cand["s_unit"]} if cand.get("s_cap") else {})}


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


def load_shops(path, now):
    """v1.28: attempt memory {"url|kind": [[ts, cls, price/list or None], ...]} without rows older than SHOP_TTL_S.
    A missing or unreadable file is an empty memory."""
    d = read_json(path, {}) or {}
    rows = d.get("shops") if isinstance(d, dict) else None
    out = {}
    for k, v in (rows.items() if isinstance(rows, dict) else ()):
        live = [r for r in v if isinstance(r, list) and len(r) == 3 and isinstance(r[0], (int, float))
                and r[0] > now - SHOP_TTL_S] if isinstance(v, list) else []
        if live:
            out[k] = live[-SHOP_KEEP:]
    return out


def attempt_class(receipt, info):
    """v1.28: one attempt's outcome: deal, r0 (rejected before any counter-offer), rej (rejected after haggling),
    err (a2a exception), walk, rrf (receipt refused by us), cut (deadline or round cap mid-haggle) or other."""
    if receipt:
        return "deal"
    if info.get("error"):
        return "err"
    s = info.get("state")
    if s == "TASK_STATE_REJECTED":
        return "rej" if info.get("rounds") else "r0"
    return {"walked_away": "walk", "receipt_refused": "rrf", "TASK_STATE_INPUT_REQUIRED": "cut"}.get(s, "other")


def deal_ratio(receipt, cand):
    """v1.28: price / list price of an accepted receipt, else None."""
    try:
        return round(P.as_int_price(receipt["price"]) / cand["list_price"], 4) if receipt else None
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
        return None


def shop_weight(mem, url, kind, now):
    """v1.28: weight of a shop for this order kind = P(deal) x expected surplus share, from our attempts in the last
    SHOP_TTL_S: (deals + 1) / (attempts + 2) x (1 - mean price/list of our deals there, with 2 prior deals at
    SHOP_PRIOR_RATIO), x SHOP_ERR_PEN after an a2a error in the last SHOP_ERR_S. A shop never tried weighs
    0.5 x (1 - SHOP_PRIOR_RATIO) = 0.15. v1.28b: a refused checkout ("cof" row) undoes one deal, is not an attempt
    itself, and counts as an error for the SHOP_ERR_PEN window."""
    rows = mem.get(f"{url}|{kind}") or []
    cofs = sum(1 for r in rows if r[1] == "cof")
    deals = [r for r in rows if r[1] == "deal"]
    w = (max(0, len(deals) - cofs) + 1) / (len(rows) - cofs + 2)
    ratios = [r[2] for r in deals if isinstance(r[2], (int, float)) and not isinstance(r[2], bool)]
    w *= 1 - (sum(ratios) + 2 * SHOP_PRIOR_RATIO) / (len(ratios) + 2)
    if any(r[1] in ("err", "cof") and now - r[0] < SHOP_ERR_S for r in rows):
        w *= SHOP_ERR_PEN
    return w


def refused_rows(refused, catalog):
    """v1.28b: one (key, "cof" row) per checkout the kit refused after a shop's receipt: (shop url, item, ts) from
    this run's reject events, under the item's kind in the catalog (both kinds when the item is not there).
    01:07-04:07 KST 2026-11-05 the kit refused 161 checkouts on shelf life and 16 as refused (409 unit not in stock;
    one shop 8 times with the same unit); the hook had counted each of them as a deal."""
    out = []
    for shop, item, ts in refused:
        if not shop:
            continue
        k = (catalog.get(item) or {}).get("kind") if isinstance(item, str) else None
        for kd in ([k] if k in ("food", "goods") else ["food", "goods"]):
            out.append((f"{shop}|{kd}", [round(float(ts), 1), "cof", None]))
    return out


def rerank(cands, mem, kind, now):
    """v1.28: candidates by score x shop_weight; equal products keep choose_offers' order (score, SKU, shop name).
    Before, a SKU listed by several shops was tried in shop-name order: 01:07-04:07 KST 2026-11-05, 180 of 263 failed
    orders stopped at the food deadline (15 s) after 1-2 shops (round-0 rejections ~2.5 s each, timeouts ~15 s) with
    10+ candidates left. Replay of 1,077 food orders (8 h): deal rate +0.097 (95 % CI +0.084..+0.110)."""
    w = [shop_weight(mem, c["shop"].get("url"), kind, now) for c in cands]
    idx = sorted(range(len(cands)), key=lambda i: (-(cands[i]["score"] * w[i]), i))
    return [dict(cands[i], shop_w=round(w[i], 4)) for i in idx]


def save_shops(path, new, now):
    """v1.28: merge this run's attempts into the file as it is now (several hook processes run at once), prune,
    write atomically."""
    mem = load_shops(path, now)
    for k, r in new:
        mem.setdefault(k, []).append(r)
    mem = {k: v[-SHOP_KEEP:] for k, v in mem.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"v": 1, "shops": mem}, ensure_ascii=False), encoding="utf-8")
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
    rej, refused = {}, []
    shelf_path = state_dir() / SHELF_FILE               # v1.22: next to reputation.json
    shelf, shelf_dirty = load_shelf(shelf_path, t0)
    for e in new:                                       # refusals of earlier orders, read once (ledger cursor)
        if e["type"] == "reject":
            rej[e["data"]["reason_code"]] = rej.get(e["data"]["reason_code"], 0) + 1
            refused.append((e["data"].get("shop"), e["data"].get("item"), e.get("ts") or t0))  # v1.28b
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
    kind = order.get("kind") or "goods"
    shop_path = state_dir() / SHOP_FILE                 # v1.28: next to SHELF_FILE
    shop_new, shop_err = [], None
    try:
        mem = load_shops(shop_path, t0)
        for i, c in enumerate(cands):
            rows = mem.get(f"{c['shop'].get('url')}|{kind}") or []
            ages = [round(t0 - r[0], 1) for r in rows if r[1] in ("err", "cof")]
            c["diag_before_rank"] = i + 1
            c["diag_loaded_age"] = min(ages) if ages else None
        for key, row in refused_rows(refused, catalog):   # v1.28b: refused checkouts of earlier orders
            mem.setdefault(key, []).append(row)
            shop_new.append((key, row))
        for c in cands:
            rows = mem.get(f"{c['shop'].get('url')}|{kind}") or []
            ages = [round(t0 - r[0], 1) for r in rows if r[1] in ("err", "cof")]
            c["diag_recent_age"] = min(ages) if ages else None
            c["diag_penalty"] = any(r[1] in ("err", "cof") and t0 - r[0] < SHOP_ERR_S for r in rows)
        cands = rerank(cands, mem, kind, t0)
    except Exception as e:                              # noqa: BLE001 - never cost the order: keep choose_offers' order
        shop_err = f"{type(e).__name__}: {str(e)[:120]}"
    for c in cands:
        c["relax"] = relax
        c["pubkey"] = own.get("pubkeys", {}).get(OW.norm_url(c["shop"].get("url")))
    accept = P.accept_policy(order, pol, relax)
    tried, out = [], None
    par_left = []                                       # PUSH 11-16: parallel attempts still running when the loop ended
    par_launched = 0                                    # PUSH 11-16: attempts started by the parallel loop
    seen_shops = set()
    stop = None                                         # v1.20b: why the attempt loop stopped early
    oos_free, n_oos = (oos_free_max(pol) if food else 0), 0   # CoS 11-16: food out-of-stock rejections free their slot
    def run_attempt(c, dl):                             # PUSH 11-16: one shop attempt (same body as before, now callable)
        diag = {}
        c["rpc_diag"] = diag
        try:
            receipt, info = haggle(c, order, pol, dl, accept)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            receipt, info = None, {"error": f"{type(e).__name__}"}
            failure = {"order": order.get("order"), "stage": "a2a", "detail": str(e)[:200], "error": info["error"]}
            if diag.get("rpc_stage"):
                now = time.time()
                failure.update({"rpc_stage": diag["rpc_stage"], "rpc_elapsed_s": now - diag["rpc_started"],
                                "deadline_remaining_s": dl - now,
                                "deadline_start_remaining_s": dl - diag["rpc_started"]})
            hook_event("fail", f"a2a:{order.get('order')}:{c['shop']['url']}", failure, "buyer")
        return receipt, info

    def book(c, receipt, info, free):                   # PUSH 11-16: bookkeeping of one finished attempt -> out or None
        nonlocal n_oos, shelf_dirty
        deal_accept = info.pop("accept", None) or accept   # v1.4: with C only for a deal made under the C cap
        if oos_free and is_oos_reject(info):
            n_oos += 1
        if food and info.get("state") == "walked_away" and "shelf life" in (info.get("why") or ""):
            remember_shelf(shelf, c["shop"].get("url"), c["sku"], info["why"], time.time())
            shelf_dirty = True
        tried.append({"shop": c["shop"].get("name"), "sku": c["sku"], "haggle_observations": c.get("haggle_observations", []), **info,
                      **({"free_slot": True} if free else {})})   # Reviewer 11-16 5c: attempt that only a freed slot allowed
        if info.get("state") != "halted":               # PUSH 11-16: a stopped parallel attempt says nothing about the shop
            shop_new.append((f"{c['shop'].get('url')}|{kind}",
                             [round(time.time(), 1), attempt_class(receipt, info), deal_ratio(receipt, c)]))
        if receipt:
            price = P.as_int_price(receipt["price"])
            return {"shop": c["shop"]["url"], "receipt": receipt, "stars": 4,
                    "review": f"{c['name']} at {price}", "accept": deal_accept}
        return None

    par_w, par_max = parallel_cfg(pol, kind)
    if not par_w:
        for c in cands:
            if c["shop"]["url"] in seen_shops:
                continue
            now = time.time()
            eff = len(seen_shops) - min(n_oos, oos_free)    # CoS 11-16: = len(seen_shops) when oos_free_max is 0 (default)
            ok, stop = attempt_allowed(eff, now - t0, now, order, pol)
            if not ok:
                break
            dl = attempt_deadline(len(seen_shops), t0, order, pol)   # real count: deadlines/expiry guard unchanged
            if oos_free and dl - now < 2:                   # Reviewer 11-16 5a: no attempt with < 2 s left (only when oos_free on)
                stop = "deadline"
                break
            free = bool(oos_free) and eff < len(seen_shops) and not attempt_allowed(len(seen_shops), now - t0, now, order, pol)[0]
            seen_shops.add(c["shop"]["url"])
            receipt, info = run_attempt(c, dl)
            out = book(c, receipt, info, free)
            if out is not None:
                break
    else:                                               # PUSH 11-16: up to par_w haggles at once, first receipt wins
        import concurrent.futures as _cf
        import threading as _th
        halt = _th.Event()
        ppol = dict(pol, buyer=dict(pol.get("buyer") or {}, max_shops=par_max))   # attempt cap for this loop only
        running, i, closed = {}, 0, False
        ex = _cf.ThreadPoolExecutor(max_workers=par_w)
        try:
            while True:
                while not halt.is_set() and not closed and len(running) < par_w:
                    while i < len(cands) and cands[i]["shop"]["url"] in seen_shops:
                        i += 1
                    if i >= len(cands):
                        closed = True
                        break
                    now = time.time()
                    eff = len(seen_shops) - min(n_oos, oos_free)
                    ok, why = attempt_allowed(eff, now - t0, now, order, ppol)
                    if not ok:
                        stop = why
                        if why != "max_shops" or not running:   # max_shops: a running OOS reply may still free a slot
                            closed = True
                        break
                    dl = attempt_deadline(len(seen_shops), t0, order, ppol)
                    if dl - now < 2:                        # same rule as Reviewer 11-16 5a: no attempt with < 2 s left
                        stop, closed = "deadline", True
                        break
                    free = bool(oos_free) and eff < len(seen_shops) and not attempt_allowed(len(seen_shops), now - t0, now, order, ppol)[0]
                    c = cands[i]
                    i += 1
                    stop = None                         # Codex nit 3: an admitted attempt clears an earlier max_shops stop
                    par_launched += 1
                    seen_shops.add(c["shop"]["url"])
                    c["_halt"] = halt
                    running[ex.submit(run_attempt, c, dl)] = (c, free)
                if not running:
                    break
                done, _ = _cf.wait(list(running), return_when=_cf.FIRST_COMPLETED)
                for f in done:
                    c, free = running.pop(f)
                    try:
                        receipt, info = f.result()
                    except Exception as e:              # noqa: BLE001 - never cost the order
                        receipt, info = None, {"error": f"{type(e).__name__}"}
                    if out is not None and receipt:     # a second receipt after the winner: not used
                        receipt, info = None, dict(info, state="halted", unused_receipt=True)
                    r = book(c, receipt, info, free)
                    if r is not None and out is None:
                        out, stop = r, None
                        halt.set()
                if out is not None:                     # Codex 11-16 (1): do not wait for the losers' in-flight RPCs
                    break
        finally:
            halt.set()
            par_left = list(running)                    # still in flight; joined (bounded) after the output is out
            ex.shutdown(wait=False)
    if out is None:
        out = {"shop": cands[0]["shop"]["url"] if cands else (shops[0]["url"] if shops else ""),
               "receipt": None, "stars": 3, "review": "no deal", "accept": accept}
    seq = hook_event("decision", f"buy:{order.get('order')}",
                     {"hook": "market_buyer", "execution_path": "default", "what": "offer_selection", "candidates": len(cands), "tried": tried,
                      # v1.5: the ranking itself (top 6), so a run shows when reputation changed the choice
                      "ranked": [{"shop": c["shop"].get("name"), "sku": c["sku"], "score": round(c["score"], 1),
                                  "broken_rate": round(c.get("broken_rate", 0.0), 4), "w": c.get("shop_w"),
                                  "before_rank": c.get("diag_before_rank"), "loaded_error_age": c.get("diag_loaded_age"),
                                  "error_age": c.get("diag_recent_age"), "error_penalty": c.get("diag_penalty")}
                                 for c in cands[:6]],
                      "candidate_shops": len({c["shop"].get("url") for c in cands}),
                      "shop_err": shop_err,
                      "order_kind": order.get("kind") or "goods", "order_category": order.get("category"),
                      "step_frac": P.buyer_terms(order, pol, relax)["step_frac"],
                      "accept_ask_default_present": "accept_ask_frac" in pol["buyer"]["defaults"][order.get("kind") or "goods"],
                      "accept_ask_default": pol["buyer"]["defaults"][order.get("kind") or "goods"].get("accept_ask_frac"),
                      "accept_ask_leaf_present": "accept_ask_frac" in (pol["buyer"].get("categories") or {}).get(P.leaf(order.get("category")), {}),
                      "accept_ask_leaf": (pol["buyer"].get("categories") or {}).get(P.leaf(order.get("category")), {}).get("accept_ask_frac"),
                      "accept_ask_category_present": "accept_ask_frac" in (pol["buyer"].get("categories") or {}).get(order.get("category"), {}),
                      "accept_ask_category": (pol["buyer"].get("categories") or {}).get(order.get("category"), {}).get("accept_ask_frac"),
                      "accept_ask_resolved": P.resolve(pol, order.get("category"), order.get("kind") or "goods", side="buyer").get("accept_ask_frac"),
                      "accept_ask_effective": P.buyer_terms(order, pol, relax).get("accept_ask_frac"),
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
                                   **({"par": {"w": par_w, "max": par_max, "launched": par_launched,
                                               "inflight_at_exit": len(par_left)}} if par_w else {}),   # Codex nit 2
                                   "list_price": next((c["list_price"] for c in cands if out["receipt"] and c["sku"] == out["receipt"].get("item")), None),
                                   **({"oos_free_used": min(n_oos, oos_free), "free_slot_attempts": sum(1 for x in tried if x.get("free_slot")),
                                       "free_slot_deal": bool(out["receipt"]) and bool(tried) and bool(tried[-1].get("free_slot"))} if oos_free else {})},
              rejections=rej, ingest_ms=ims, ingested=counts,
              extra={"rep_source": rsrc, "stats": stats is not None, "stats_error": serr, "catch_up": cinfo, "agg_broken": agg_broken, "relax": relax, "settled": nset, "own_source": own["source"], "own_names": len(own["names"]), "own_urls": len(own["urls"]),
                     "own_excluded": len(excluded), "own_error": own["error"]})
    emit(out)
    if shelf_dirty:
        try:
            save_shelf(shelf_path, shelf)
        except OSError:                                 # bookkeeping only: the order's output is already out
            pass
    if shop_new:
        try:
            save_shops(shop_path, shop_new, time.time())
        except (OSError, TypeError, ValueError):        # bookkeeping only, as above
            pass
    if par_left:                                        # PUSH 11-16 / Codex (1): bounded exit. Halted attempts leave at
        import concurrent.futures as _cf                # their next check and cancel their task; give them PAR_GRACE_S,
        _, rest = _cf.wait(par_left, timeout=PAR_GRACE_S)   # then end the process (the kit waits for the exit)
        if rest:
            sys.stdout.flush()
            os._exit(0)


if __name__ == "__main__":
    import os as _os, sys as _sys                 # r3a2 switch: <W>/a2/LIVE, read per run
    if _os.path.exists(_os.path.join(_os.getcwd(), "a2", "LIVE")):
        try:
            _sys.path.insert(0, '/home/sungsoo/r3a2-code')
            from kernel import buyer_exec as _a2
        except Exception:                         # noqa: BLE001 - no attempt-2 code: attempt 1 runs
            _a2 = None
        if _a2 is not None:
            raise SystemExit(_a2.main(_sys.argv[1:] + ["--root", _os.getcwd()]))
    main()
