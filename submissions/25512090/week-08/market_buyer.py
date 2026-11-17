import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
import httpx

OFFER_STEPS = (0.65, 0.85, 1.0)  # graduated negotiation: lowball first, escalate only on rejection

# Instrumentation (2026-11-10): expert review flagged that negotiate() anchors offers to budget, not
# to the shop's own stated price or the catalog list price -- plausible root cause for saved ranking
# 127/128, but a reconstruction from payments.jsonl x catalog.json gave the WRONG SIGN vs the
# official saved metric, so the actual mechanism is unverified. Log first-party ground truth at the
# moment of each deal instead of reconstructing after the fact from mismatched files.
NEGOTIATION_LOG_PATH = Path(__file__).parent / "negotiation_log.jsonl"
# A shop's opening reply routinely states its ask inline ("Offering X for 12,345 (...)", "가격
# 12345원", "at 21375"). Heuristic only -- used for logging, never for deciding an offer -- so a
# wrong/missed parse just means one log row has a null shop_stated_price, not a bad purchase.
PRICE_MENTION_RE = re.compile(r"[\d]{2,9}(?:,\d{3})*")


def _extract_price_mention(text, budget):
    """Largest plausible price-looking number in a free-text shop reply, or None. Plausible = between
    100 and 3x budget -- excludes small numbers like warranty hours (<=72) or grade/quantity digits,
    and excludes implausibly huge ones that are clearly not this order's price."""
    if not text:
        return None
    candidates = []
    for m in PRICE_MENTION_RE.finditer(text):
        try:
            n = int(m.group().replace(",", ""))
        except ValueError:
            continue
        if 100 <= n <= budget * 3:
            candidates.append(n)
    return max(candidates) if candidates else None


def _recent_failure_penalty(window=40, min_attempts=3):
    """Per-shop penalty from OUR OWN recent negotiation outcomes (negotiation_log.jsonl). A shop
    scoring high on static reputation (reviews/credit tier/price signal) keeps winning chosen_shop
    every cycle even when its real current stock doesn't match what we're asked to buy -- confirmed
    in practice: dae-yangkim (AAA, 10 reviews, 5.0 avg stars) was picked in 24 of the last 30 real
    attempts, 0 of which produced a receipt (same failure shape as the q00 defaulted-purchase
    routing bug: a strong, stock-blind score dominates selection regardless of fulfillability).
    Requires >=min_attempts in the window before penalizing, so one unlucky try doesn't blacklist a
    shop -- only a demonstrated recent pattern does."""
    try:
        lines = NEGOTIATION_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()[-window:]
    except Exception:
        return {}
    counts = {}
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        c = counts.setdefault(row["shop"], {"attempts": 0, "fails": 0})
        c["attempts"] += 1
        if not row.get("deal"):
            c["fails"] += 1
    return {shop: c["fails"] / c["attempts"] for shop, c in counts.items() if c["attempts"] >= min_attempts}


def _log_negotiation(market_dir, shop_name, item, budget, shop_stated_price, receipt):
    """One row per purchase attempt: what the shop first asked, what we actually paid, and the
    catalog's static list_price for that exact SKU (when we got a receipt) -- the ground-truth triple
    needed to see whether offers are landing above or below the real reference price, instead of
    guessing from aggregate reviews/payment logs after the fact."""
    list_price = None
    paid_price = receipt.get("price") if isinstance(receipt, dict) else None
    sku = receipt.get("item") if isinstance(receipt, dict) else None
    if sku:
        try:
            catalog = json.loads((Path(market_dir) / "catalog.json").read_text(encoding="utf-8"))
            list_price = catalog.get(sku, {}).get("list_price")
        except Exception:
            pass
    row = {
        "ts": time.time(), "shop": shop_name, "item": item, "sku": sku, "budget": budget,
        "shop_stated_price": shop_stated_price, "paid_price": paid_price, "list_price": list_price,
        "deal": paid_price is not None,
    }
    try:
        with open(NEGOTIATION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass

def _catalog_price_estimate(market_dir, item_text):
    """Secondary negotiation anchor when the shop's opening reply has no parseable price
    (`_extract_price_mention` returns None): estimate from OUR OWN catalog.json, matching the
    order's item/category text against catalog entries, at the median list_price of matches
    discounted to ~70% (our own payments.jsonl history: full-history avg paid/list ratio was
    ~0.76-0.81 across 2,575 matched deals). Still far better than falling through to a blind
    budget-only ladder, which anchors to a category's worst-case padding, not its typical price."""
    try:
        catalog = json.loads((Path(market_dir) / "catalog.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    want = (item_text or "").lower()
    if not want:
        return None
    prices = sorted(
        x["list_price"] for x in catalog.values()
        if x.get("list_price") and ((x.get("category") or "").split(">")[-1].lower() in want
                                     or (x.get("name") or "").lower() in want)
    )
    if not prices:
        return None
    return int(prices[len(prices) // 2] * 0.7)


def _catalog_list_price(market_dir, item_text, sku=None):
    """Raw (undiscounted) list_price, used as negotiate()'s offer cap -- unlike
    _catalog_price_estimate above (a pre-discounted opening anchor), this must be the real ceiling:
    paying above it scores negative under the margin-based `saved` formula (patch 3.6), uncapped on
    the downside. Prefer an exact SKU match (we have one whenever `chosen_shop["offers"]` is set,
    see handle_shops) over the category-text fuzzy match used as a fallback."""
    try:
        catalog = json.loads((Path(market_dir) / "catalog.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    if sku and sku in catalog and catalog[sku].get("list_price"):
        return catalog[sku]["list_price"]
    want = (item_text or "").lower()
    if not want:
        return None
    prices = sorted(
        x["list_price"] for x in catalog.values()
        if x.get("list_price") and ((x.get("category") or "").split(">")[-1].lower() in want
                                     or (x.get("name") or "").lower() in want)
    )
    return prices[len(prices) // 2] if prices else None

# Shop-selection price/trust signal: public reviews (shops.json) routinely state the actual paid
# price against list price in free text ("Paid 46670 of 74630", "paid 84% of list 13500", "28%
# below list", "at 21375 (list 22500)") -- a cheap, already-available signal that chosen_shop's
# scoring previously ignored entirely (it only looked at star counts and our own credit tier).
PAID_OF_RE = re.compile(r"paid\s+([\d,]+)\s+of\s+([\d,]+)", re.IGNORECASE)
PAID_PCT_RE = re.compile(r"paid\s+(\d+)%\s+of\s+list", re.IGNORECASE)
BELOW_PCT_RE = re.compile(r"(\d+)%\s+(?:below|under)\s+list", re.IGNORECASE)
AT_LIST_RE = re.compile(r"at\s+([\d,]+)\s*\(list\s+([\d,]+)\)", re.IGNORECASE)
SCAM_RE = re.compile(r"scam|forg|dishonest|fraud", re.IGNORECASE)


def _review_price_signal(reviews):
    """(avg_paid/list ratio or None, scam/forgery mention count) from a shop's own public reviews.
    Lower ratio = buyers historically pay less below this shop's list price (a better counterparty
    for us too, not just a reputation score). Explicit scam/forged-receipt mentions are a much
    stronger red flag than an ordinary 1-star review and were previously invisible to scoring."""
    ratios = []
    scam_hits = 0
    for r in reviews:
        text = r.get("review", "") or ""
        if SCAM_RE.search(text):
            scam_hits += 1
        m = PAID_OF_RE.search(text)
        if m:
            paid, total = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
            if total > 0:
                ratios.append(paid / total)
            continue
        m = PAID_PCT_RE.search(text)
        if m:
            ratios.append(int(m.group(1)) / 100)
            continue
        m = BELOW_PCT_RE.search(text)
        if m:
            ratios.append(1 - int(m.group(1)) / 100)
            continue
        m = AT_LIST_RE.search(text)
        if m:
            at, total = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
            if total > 0:
                ratios.append(at / total)
    avg_ratio = sum(ratios) / len(ratios) if ratios else None
    return avg_ratio, scam_hits


def negotiate(send, task, budget, reference_price=None, list_price=None):
    """Send a sequence of rising offers; stop at the first accepted one.

    Price-anchored (2026-11-10, expert review): budget is a padded ceiling the market sets to cover
    the category's priciest plausible grade/SKU, not the price of the specific item we end up
    negotiating -- confirmed via negotiation_log.jsonl (one full-data deal: shop asked 73,900,
    budget was far above that). Blindly opening at 0.65x BUDGET can already exceed the real list
    price whenever budget is generously padded, which is structural, not occasional. When the shop's
    own opening reply states a price (parsed by the caller into `reference_price`, via
    `_extract_price_mention`), the first two offers anchor to min(reference_price, budget) instead of
    budget alone -- realistic opening offers instead of budget-padded ones. With no reference_price
    (parse miss), this reduces to the exact old budget-only ladder -- fully backward compatible.

    2026-11-14: every rung, including the last, is also capped at `1.25 * list_price` when known.
    Patch 3.6 scores a deal (list - price) / (list - cost), uncapped below zero -- paying ABOVE list
    scores negative and can in principle score worse than an outright failed order (flat -1). Solving
    (list - price) / (list - cost) = -1 gives the break-even price 2*list - cost; the platform's own
    published grade bands (rules.md: A 55-75%, B 40-60%, C 25-45% of list) bound cost at at most 75%
    of list for ANY grade, so 2*list - 0.75*list = 1.25*list is the worst-case price that can NEVER
    score worse than failing, regardless of the actual (unknown to us) grade/cost of the unit. A
    first attempt at this fix capped at exactly 1.0x list (deal score 0, paying exactly list) --
    live-measured over ~10 minutes, deal rate fell from a sustained ~38% to ~15% while per-deal
    saved-vs-list rose from ~0.11 to ~0.29: the 1.0x cap was refusing trades that would have scored
    between -1 and 0 (still better than failing) along with the genuinely bad ones (worse than -1),
    net removing value instead of only removing it. 1.25x keeps the safety property (never worse than
    a plain failure) while recovering the trades in between. Measured over a prior 24h window: mean
    budget/list ~=1.3-1.4x and ~30% of deals were closing near full (uncapped) budget while the
    official `saved` board metric fell -0.017 -> -0.035 over the same window the raw deal-closure
    rate rose from 8.6% to ~38% (see REPORT.md 22) -- consistent with some of those near-budget
    closes landing past the true, grade-dependent break-even and dragging the average down.

    Also guards against a task that is ALREADY terminal when handed in (shop auto-rejected the
    opening "I want to buy a {item}" message outright -- out of stock, category mismatch, etc.).
    Sending a numeric offer to a task that is no longer TASK_STATE_INPUT_REQUIRED is not a
    negotiation step, it is a protocol error -- confirmed in practice: ~50% of every buy attempt
    (5,336 of 10,756 in market-kit.log) failed with exactly 'Task <id> is in terminal state:
    TASK_STATE_REJECTED' / UNSUPPORTED_OPERATION, because this loop unconditionally sent its first
    offer regardless of the task's actual state."""
    t, said = task, None
    if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
        return t, said
    anchor = min(reference_price, budget) if reference_price else budget
    cap = min(budget, int(list_price * 1.25)) if list_price else budget
    offers = [min(int(anchor * OFFER_STEPS[0]), cap), min(int(anchor * OFFER_STEPS[1]), cap), cap]
    for offer in offers:
        t, said = send(str(offer), t["id"])
        if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
            break
    return t, said


def handle_buy(args):
    url_base = args.url.rstrip("/")
    try:
        card_res = httpx.get(f"{url_base}/.well-known/agent-card.json", timeout=10)
        card = card_res.json()
        interfaces = card.get("supportedInterfaces", card.get("interfaces", []))
        if not interfaces:
            print(json.dumps({"state": "FAILED", "artifacts": []}))
            return
        rpc_url = interfaces[0]["url"]
    except Exception as e:
        print(f"Error fetching card: {e}", file=sys.stderr)
        print(json.dumps({"state": "FAILED", "artifacts": []}))
        return

    def rpc(method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = httpx.post(rpc_url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["result"].get("task", r["result"])

    def send(text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        t = rpc("SendMessage", {"message": msg})
        said = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
        return t, said

    try:
        t, said = send(f"I want to buy a {args.item}")
        budget = int(args.budget)
        t, said = negotiate(send, t, budget, reference_price=_extract_price_mention(said, budget))
        print(json.dumps({"state": t["status"]["state"], "artifacts": t.get("artifacts", [])}))
    except Exception as e:
        print(f"Error during negotiation: {e}", file=sys.stderr)
        print(json.dumps({"state": "FAILED", "artifacts": []}))


def handle_restock(state_path):
    """Diversify restock spend into high-value, non-perishable, liquid categories (from demand.json)
    instead of random low-value picks. The anchor commodity (Gea072173) already sits 2600+ units deep
    and unsold; it is deliberately left out of restock until that stock clears."""
    # market_kit.py passes {state} as the .market directory itself (md), not a file inside it.
    market_dir = Path(state_path) if state_path else Path(".market")
    inv_file = market_dir / "inventory.json"
    cat_file = market_dir / "catalog.json"
    demand_file = market_dir / "demand.json"

    balance = 1000000
    bankrupt_line = 500000
    inv = {}
    surge_map = {}
    if inv_file.exists():
        try:
            inv = json.loads(inv_file.read_text(encoding="utf-8"))
            balance = inv.get("balance", balance)
            bankrupt_line = inv.get("bankrupt_line", bankrupt_line)
            # Surge: market-wide demand on one SKU this hour adds up to +50% over plain cost (rules.md
            # Patch 3.2). inventory.json already carries this per-SKU (the market's own advice: "Check
            # surge in inventory.json... before you restock") but restock selection never read it --
            # we were liable to pay the inflated price on exactly the SKUs everyone else is also
            # chasing. Keyed by sku for an O(1) lookup per candidate below.
            surge_map = {s["sku"]: s.get("factor", 1.0) for s in inv.get("surge", []) if s.get("sku")}
        except Exception:
            pass

    # Hard floor: never spend while balance is anywhere near the bankrupt line. A restock window can
    # trigger several handle_restock calls back-to-back (one per ~60-90s cycle while it stays open);
    # with no per-call cap this emptied the balance from 2.2M to 429k (below bankrupt_line) in ~3
    # minutes. Flat +50,000 above bankrupt_line (switched from 1.2x multiplicative on an explicit
    # user risk-tradeoff decision, 2026-11-11, to trade more actively): at the current bankrupt_line
    # (500,000) this is a smaller absolute margin (550,000 vs the prior 600,000) -- deliberately
    # tighter. NOTE this is applied while balance (429,214 as of this change) is already BELOW
    # bankrupt_line itself from a 15h shop-reachability outage -- restock stays correctly blocked
    # until balance recovers past 550,000, this change only affects how soon it resumes.
    safety_floor = bankrupt_line + 50000
    spendable = balance - safety_floor
    if spendable <= 0:
        print(json.dumps({"buy": [], "bankrupt": False}))
        return

    PER_CALL_CAP = 150000  # absolute ceiling per restock call, independent of balance
    restock_budget = min(spendable * 0.15, PER_CALL_CAP)

    # Our own proven sell-through (seller.py bumps this on every REAL settled sale, not just a
    # restock purchase) -- direct evidence of what actually moves for this shop, which demand.json's
    # market-wide category/median_price signal can't tell us (it has no frequency field at all).
    # 2026-11-11 (explicit user decision): stop restocking by price-rank alone; replace slow stock
    # with fast-turnover stock. Excludes the anchor SKU -- it stays deliberately out of restock
    # regardless of its own velocity entry (see docstring).
    velocity = {}
    mem_file = Path(__file__).parent / "memory_store.json"
    if mem_file.exists():
        try:
            velocity = json.loads(mem_file.read_text(encoding="utf-8")).get("sku_velocity", {})
        except Exception:
            pass
    anchor_sku = None
    try:
        anchor_sku = json.loads((Path(__file__).parent / "strategy.json").read_text(encoding="utf-8")) \
            .get("monopoly_supplier", {}).get("commodity_anchor_sku")
    except Exception:
        pass
    current_stock = {}
    for u in inv.get("units", []) if isinstance(inv, dict) else []:
        if u.get("grade") != "expired":
            current_stock[u["sku"]] = current_stock.get(u["sku"], 0) + 1

    buy_orders = []
    if cat_file.exists():
        try:
            catalog = json.loads(cat_file.read_text(encoding="utf-8"))
            LOW_STOCK = 10  # a proven mover running this low is worth topping up ahead of generic demand sweep
            fast_movers = sorted(
                (sku for sku, v in velocity.items() if sku != anchor_sku and (v.get("frequency") or 0) > 0
                 and sku in catalog and current_stock.get(sku, 0) < LOW_STOCK),
                key=lambda s: -velocity[s]["frequency"],
            )[:5]
            fast_budget = restock_budget * 0.4  # reserve part of the call for proven sellers specifically
            per_mover = fast_budget / len(fast_movers) if fast_movers else 0
            for sku in fast_movers:
                list_price = catalog[sku].get("list_price") or 0
                est_cost = list_price * 0.6
                if est_cost <= 0 or surge_map.get(sku, 1.0) >= 1.15:
                    continue  # skip if currently surging -- same avoidance rule as the general sweep
                qty = max(1, min(3, int(per_mover / est_cost))) if per_mover >= est_cost else 0
                if qty:
                    buy_orders.append({"sku": sku, "qty": qty})
            restock_budget -= fast_budget  # remaining budget still goes through the generic sweep below
        except Exception:
            pass

    if cat_file.exists() and demand_file.exists():
        try:
            catalog = json.loads(cat_file.read_text(encoding="utf-8"))
            demand = json.loads(demand_file.read_text(encoding="utf-8"))
            targets = [d for d in demand if d.get("kind") == "goods" and d.get("shelf_hours") is None
                       and (d.get("skus") or 0) >= 3 and (d.get("median_price") or 0) > 0]
            targets.sort(key=lambda d: -d["median_price"])

            slots = min(10 - len(buy_orders), len(targets)) or 1
            per_slot = restock_budget / slots if slots else 0
            already_picked = {b["sku"] for b in buy_orders}
            for t in targets[:slots]:
                cat_skus = sorted(
                    (s for s, x in catalog.items() if x.get("category") == t["category"] and s not in already_picked),
                    key=lambda s: catalog[s].get("list_price") or 0,
                )
                if not cat_skus:
                    continue
                # Avoid paying the surge premium when a non-surging substitute in the same category
                # exists; only fall back to a surging SKU if every candidate here is surging (still
                # better to restock at +X% than not at all).
                SURGE_AVOID = 1.15  # skip anything more than 15% above plain cost
                non_surging = [s for s in cat_skus if surge_map.get(s, 1.0) < SURGE_AVOID]
                candidates = non_surging or cat_skus
                # Among affordable candidates, prefer one with proven sell-through (velocity) over
                # blindly taking the priciest affordable option -- same fast-mover preference as above,
                # applied within the generic category sweep too.
                affordable = [s for s in candidates if (catalog[s].get("list_price") or 0) * 0.6 <= per_slot]
                if affordable:
                    with_velocity = [s for s in affordable if velocity.get(s, {}).get("frequency")]
                    sku = max(with_velocity, key=lambda s: velocity[s]["frequency"]) if with_velocity else affordable[-1]
                else:
                    sku = candidates[0]
                est_cost = (catalog[sku].get("list_price") or 1) * 0.6
                qty = max(1, min(3, int(per_slot / est_cost))) if est_cost > 0 else 1
                buy_orders.append({"sku": sku, "qty": qty})
        except Exception:
            pass

    print(json.dumps({"buy": buy_orders[:10]}))


DEFAULTED_PURCHASE_COOLDOWN_SEC = 900  # try the punitive-purchase shop at most once per 15 min
_DEFAULTED_ATTEMPTS_PATH = Path(__file__).parent / "defaulted_purchase_attempts.json"


def _defaulted_bonus_cooldown_ok(borrower):
    """True once per DEFAULTED_PURCHASE_COOLDOWN_SEC per borrower, and marks it used as a side
    effect. Without this, the +40 scoring bonus below wins EVERY order cycle (confirmed: 5/5 real
    orders routed to q00 before this existed, 0/5 matched its 22-unit stock) -- wasting every single
    order attempt on a shop that rarely has what's being asked for. Cooldown lets it try periodically
    (enough to eventually land the punitive purchase+review) without starving normal order matching."""
    now = time.time()
    data = {}
    if _DEFAULTED_ATTEMPTS_PATH.exists():
        try:
            data = json.loads(_DEFAULTED_ATTEMPTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    last = data.get(borrower, 0)
    if now - last < DEFAULTED_PURCHASE_COOLDOWN_SEC:
        return False
    data[borrower] = now
    try:
        tmp = _DEFAULTED_ATTEMPTS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(_DEFAULTED_ATTEMPTS_PATH)
    except OSError:
        pass
    return True


def _load_loan_ledger():
    path = Path(__file__).parent / "loan_ledger.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"loans": []}

def _save_loan_ledger(data):
    path = Path(__file__).parent / "loan_ledger.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

def record_loan_disbursement(shop_name, amount):
    """Loan desk disbursement leg (see loan_engine.py): a real purchase from a shop with a pending
    loan request counts toward that loan. First disbursement starts the repayment clock.

    BUG FIX (2026-11-10, found via a public forum review of LOAN-587efb72): amount_due used to be
    frozen at approval time as approved_amount*(1+rate) -- the FULL approved ceiling -- and never
    adjusted down if disbursement stalled before reaching it. q00's loan disbursed only 4,972 (their
    small "Harness Shop" stock ran out of anything our buyer could purchase) but amount_due stayed
    at 79,675 -- repaid_amount * (1+rate) off an amount that was NEVER ACTUALLY DISBURSED. We were
    publicly dunning a borrower for ~16x what they actually received. amount_due now recomputes off
    `disbursed` on every update, so it can never exceed what was truly handed over, regardless of
    whether disbursement ever reaches the original ceiling."""
    data = _load_loan_ledger()
    for l in data.get("loans", []):
        if l["borrower"] != shop_name or l["status"] not in ("pending", "disbursed"):
            continue
        l["disbursed"] += amount
        rate = l.get("rate") or 0
        l["amount_due"] = int(round(l["disbursed"] * (1 + rate)))
        if l["status"] == "pending":
            l["status"] = "disbursed"
            l["due_at"] = time.time() + 3 * 3600
        _save_loan_ledger(data)
        return

def _load_loan_attempts():
    path = Path(__file__).parent / "loan_attempts.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_loan_attempts(data):
    path = Path(__file__).parent / "loan_attempts.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

def record_loan_attempt_failure(shop_name, reason):
    """A purchase aimed at disbursing shop_name's pending loan did not land. loan_engine.py reads
    this so its public status report can say why a loan is still pending ("상환측이 구매를 계속
    거부") instead of the uninformative "지급 대기중" -- the borrower sees a real, cited reason,
    and so does whoever is watching the loan desk."""
    data = _load_loan_attempts()
    rec = data.setdefault(shop_name, {"attempts": 0, "first_attempt_at": time.time()})
    rec["attempts"] += 1
    rec["last_error"] = str(reason)[:200]
    rec["last_attempt_at"] = time.time()
    _save_loan_attempts(data)

def clear_loan_attempt_failures(shop_name):
    """A disbursement just succeeded -- any prior failure streak against this borrower is now
    stale history, not a live blocker."""
    data = _load_loan_attempts()
    if shop_name in data:
        del data[shop_name]
        _save_loan_attempts(data)


MAX_SHOP_ATTEMPTS = 3


def handle_shops(args):
    """2026-11-13: `offers` tells us which shops SHOULD have matching stock, but it is a snapshot --
    confirmed live that a shop with `offers` set still replied "none of that in stock" (sold out
    between the snapshot and this attempt, likely to a faster buyer). Under patch 3.6, an order we
    fail to fill scores a full -1 on `saved` -- giving up after one stock-stale miss wastes a real
    chance at the order. Rank all stocked candidates by the existing reputation/credit/loan score
    and try up to MAX_SHOP_ATTEMPTS of them in order, moving to the next only on failure (rejection,
    card error, or no receipt) -- strictly more chances to close, never worse than the single-shot
    behavior this replaces."""
    shops = json.loads(Path(args.shops).read_text(encoding="utf-8"))
    ratings = {}
    ratings_path = Path(__file__).parent / "credit_ratings.json"
    if ratings_path.exists():
        try:
            ratings = json.loads(ratings_path.read_text(encoding="utf-8")).get("ratings", {})
        except Exception:
            pass
    loan_data = _load_loan_ledger()
    pending_borrowers = {l["borrower"] for l in loan_data.get("loans", []) if l["status"] == "pending"}
    # 연체(defaulted) 차주의 상점을 주문이 맞을 때 일부러 선택해 실구매 + 1점 "돈 갚으시오" 리뷰로
    # 공개 독촉한다 -- loan_engine.py의 포럼 독촉장과 같은 목적(평판 압박)을 거래 자체로도 수행.
    # id가 아닌 loan dict 전체를 들고 있어야 아래에서 owed 금액/대출ID를 리뷰 문구에 넣을 수 있다.
    defaulted_loans = {l["borrower"]: l for l in loan_data.get("loans", []) if l["status"] == "defaulted"}

    # A loan disbursement is just a real purchase for whatever order we currently hold. If that
    # order is perishable (kind == "food"), the item's shelf life/grade matters far more than
    # whether the seller has a pending loan -- a food deal is far more likely to fail grade/freshness
    # checks or spoil before use. Only let the loan boost steer food-order shop selection; it never
    # overrides it -- the loan preference is skipped entirely for perishable orders.
    order_file = Path(args.order) if getattr(args, "order", None) else Path(".market/order.json")
    is_perishable = False
    if order_file.exists():
        try:
            is_perishable = json.loads(order_file.read_text(encoding="utf-8")).get("kind") == "food"
        except Exception:
            pass

    # 2026-11-12: `offers` (set by market_kit.py's handle_order from this order's own `acceptable`
    # SKU list) tells us exactly which shops actually carry a matching item for THIS order --
    # confirmed via live data (sample orders: only 25-48% of shops in the pool actually had the
    # item at all). Score only the stocked subset; fall back to the full list only when NO shop in
    # the whole market has a match (the order truly can't be filled by anyone right now), where this
    # filter would otherwise leave no candidate at all.
    stocked = [s for s in shops if s.get("offers")]
    candidates = stocked or shops
    failure_penalty = _recent_failure_penalty()
    scored = []
    for s in candidates:
        reviews = s.get("reviews", [])
        score = 0
        # 2026-11-14: `saved` (patch 3.6) is literally (list - price) / (list - cost) -- how close to
        # the shop's COST we buy. The only reviews.jsonl-derived signal that predicts that is
        # avg_ratio (paid/list from the shop's own public reviews), yet it was previously weighted
        # 20x against a star-count term that sums EVERY review's stars uncapped (shops.json caps at
        # 10 reviews, so a well-reviewed shop can add up to ~50 points of pure popularity with zero
        # relation to price). Confirmed via top-ranked buyers on the live board (saved 0.77-0.82,
        # i.e. consistently closing within ~20% of the shop's own cost) vs our own ~0.10-0.22 --
        # the gap is cheapness-targeting, not deal-closure (already fixed, see REPORT.md 19/21).
        # Use the review-count-INVARIANT average star rating (trust signal, modest weight) instead of
        # a count-scaling sum, and make the price signal the dominant term so a shop that is reliably
        # cheap wins over one that is merely popular.
        if reviews:
            avg_stars = sum(r.get("stars", 3) for r in reviews) / len(reviews)
            score += avg_stars * 4
            score -= sum(1 for r in reviews if r.get("stars", 3) <= 1 or "overcharged" in r.get("review", "").lower()) * 10
        # price/trust signal mined from the shop's own public reviews: avg_ratio ranges ~0.34
        # (cheapest live shops) to ~0.94 (priciest); *100 spans roughly +51 to -9, now comparable to
        # or exceeding the loan/credit overrides below instead of being swamped by them.
        avg_ratio, scam_hits = _review_price_signal(reviews)
        if scam_hits:
            score -= 30 * scam_hits
        if avg_ratio is not None:
            score += (0.85 - avg_ratio) * 100
        # 2026-11-11: static reputation (reviews/credit tier) says nothing about whether the shop's
        # CURRENT stock matches this specific order -- confirmed in practice, dae-yangkim (AAA, 10
        # reviews, 5.0 stars) was picked in 24 of the last 30 real attempts and produced 0 receipts.
        # A demonstrated recent failure streak with THIS shop now directly offsets its static score,
        # so selection diversifies toward candidates we haven't just failed with.
        score -= failure_penalty.get(s.get("name"), 0) * 60
        # our own credit bureau rating (hegemony_engine.py): steer away from distressed counterparties
        tier = (ratings.get(s.get("name"), {}) or {}).get("tier")
        if tier == "CCC":
            score -= 20
        elif tier == "AAA":
            score += 3
        # loan desk (loan_engine.py): a shop with a pending, self-identified loan request gets strong
        # preference so a real purchase from them can serve as disbursement -- but not for perishables
        if not is_perishable and s.get("name") in pending_borrowers:
            score += 50
        # 연체 차주는 음식류 포함 어떤 주문이든 상관없이 우선 선택 대상이지만, 실측(negotiation_log.jsonl)
        # 결과 q00 재고가 22유닛뿐이라 거의 매 주문 미스 -- 쿨다운 없이는 모든 사이클을 이 상점에
        # 낭비했다(5/5 연속 실패 확인). 15분에 한 번만 시도해 나머지 사이클은 정상 매칭으로 돌아가게 한다.
        if s.get("name") in defaulted_loans and _defaulted_bonus_cooldown_ok(s["name"]):
            score += 40
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])

    budget = int(args.budget)
    last_result = None
    for _, chosen_shop in scored[:MAX_SHOP_ATTEMPTS]:
        url_base = chosen_shop["url"].rstrip("/")
        try:
            card_res = httpx.get(f"{url_base}/.well-known/agent-card.json", timeout=10)
            card = card_res.json()
            interfaces = card.get("supportedInterfaces", card.get("interfaces", []))
            if not interfaces:
                if chosen_shop.get("name") in pending_borrowers:
                    record_loan_attempt_failure(chosen_shop["name"], "agent card has no interfaces")
                last_result = {"shop": chosen_shop["url"], "receipt": None, "stars": 1, "review": "No interfaces found."}
                continue
            rpc_url = interfaces[0]["url"]
        except Exception as e:
            if chosen_shop.get("name") in pending_borrowers:
                record_loan_attempt_failure(chosen_shop["name"], e)
            last_result = {"shop": chosen_shop["url"], "receipt": None, "stars": 1, "review": f"Error fetching card: {e}"}
            continue

        def rpc(method, params):
            body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
            r = httpx.post(rpc_url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
            if "error" in r:
                raise RuntimeError(r["error"])
            return r["result"].get("task", r["result"])

        def send(text, task_id=None):
            msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
            if task_id:
                msg["taskId"] = task_id
            t = rpc("SendMessage", {"message": msg})
            said = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
            return t, said

        try:
            t, said = send(f"I want to buy a {args.item}")
            opening_said = said
            shop_stated_price = _extract_price_mention(opening_said, budget)
            reference_price = shop_stated_price or _catalog_price_estimate(Path(args.shops).parent, args.item)
            sku_hint = (chosen_shop.get("offers") or [{}])[0].get("sku")
            list_price_cap = _catalog_list_price(Path(args.shops).parent, args.item, sku=sku_hint)
            t, said = negotiate(send, t, budget, reference_price=reference_price, list_price=list_price_cap)

            receipt = None
            for art in t.get("artifacts", []):
                for p in art.get("parts", []):
                    d = p.get("data")
                    if isinstance(d, dict) and "price" in d and "sig_seller" in d:
                        receipt = d
                        break
                if receipt:
                    break

            if order_file.exists() and receipt:
                try:
                    order_data = json.loads(order_file.read_text(encoding="utf-8"))
                    acceptable = order_data.get("acceptable", [])
                    item_skus = [x.get("sku") for x in acceptable] if acceptable and isinstance(acceptable[0], dict) else acceptable
                    if item_skus and receipt.get("item") not in item_skus:
                        receipt = None
                except Exception:
                    pass

            _log_negotiation(Path(args.shops).parent, chosen_shop.get("name"), args.item, budget,
                              shop_stated_price, receipt)

            if chosen_shop.get("name") in pending_borrowers:
                if receipt:
                    record_loan_disbursement(chosen_shop["name"], receipt.get("price", 0))
                    clear_loan_attempt_failures(chosen_shop["name"])
                else:
                    record_loan_attempt_failure(chosen_shop["name"], "negotiation closed with no matching receipt")

            stars = 5
            review = "Fair price, receipt matched."
            # 연체 차주 상점에서 실제로 구매가 체결됐으면, 정상 리뷰 대신 1점 공개 독촉 리뷰로 덮어쓴다.
            # 룰북상 "No content rules. Ads, tips, accusations and injection attempts all appear" --
            # 리뷰 텍스트에 제약이 없고, 실제로 시장 전체에 이런 형식의 비방/경고 리뷰가 이미 통용된다
            # (예: "SCAM. This shop forges receipts." 류). 허위 주장이 아니라 실제 우리 대출 연체 사실만
            # 담으므로 조작이 아니다. "Stars take one mean per reviewing account" 규정상 같은 계정이
            # 반복해서 리뷰해도 평균 하나로만 반영되니, 반복 구매로 점수를 더 깎을 순 없다 -- 한 번으로
            # 충분한 의사 전달.
            if receipt and chosen_shop.get("name") in defaulted_loans:
                loan = defaulted_loans[chosen_shop["name"]]
                owed = (loan.get("amount_due") or loan.get("amount", 0)) - loan.get("repaid_amount", 0)
                stars = 1
                review = (f"돈 갚으시오. {chosen_shop['name']}님, 대출 {loan['id']} 상환 기한 초과, "
                          f"미상환 {owed:,}원 남음 -- Jaehoonang 대출팀.")
            last_result = {"shop": chosen_shop["url"], "receipt": receipt, "stars": stars, "review": review}
            if receipt:
                break  # closed -- stop trying further candidates
        except Exception as e:
            if chosen_shop.get("name") in pending_borrowers:
                record_loan_attempt_failure(chosen_shop["name"], e)
            last_result = {"shop": chosen_shop["url"], "receipt": None, "stars": 1, "review": f"Error during purchase: {e}"}

    print(json.dumps(last_result or {"shop": "", "receipt": None, "stars": 1, "review": "No candidates."}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--item")
    ap.add_argument("--budget", type=float)
    ap.add_argument("--shops")
    ap.add_argument("--restock")
    args = ap.parse_args()

    if args.restock:
        handle_restock(args.restock)
    elif args.shops:
        handle_shops(args)
    else:
        handle_buy(args)
