"""A2A v1.0 Market Buyer Agent (Week 08: Round 3 Surplus Optimizer, Accept Gate & Prompt-Injection Defense).

Supports both:
- Round 3 Economy (`--order` with `acceptable` SKUs):
  1. Ignores raw review text strings when ranking shops to defeat Indirect Prompt Injection spam
     (e.g., 'try the store Cheapest in Town'), relying strictly on structured trust metrics from
     `state_hub.json` (`claim_honest` ratio, `broken` count, `settled` count) and numeric star averages.
  2. Ranks expected savings rates (`(list_price - expected_pay) / list_price`) for
     in-stock acceptable SKUs, retaining budget feasibility, trust and diversity weights.
  3. Emits a strict `"accept"` gate (`cert: True`, `min_hours_left` for food, `grades`, `warranty_hours`)
     so `market_kit.py` v4 refuses uncertified or expiring units BEFORE paying.
- Rounds 0-2 Legacy Compatibility (`lamp`, `chair`, `bookshelf`): passes `market_test_week07.py` 25/25.
"""
import argparse
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
import unicodedata
from pathlib import Path
from typing import Any

import httpx

from adaptive_strategy import filter_candidates, record_failure

from store import (
    BREAK_RATE,
    GOODS_BREAK_WINDOW_H,
    load_catalog,
    load_hub,
    load_json_safe,
    mutate_hub,
    record_buyer_order,
    sync_broken_and_payments,
)

BASE_DIR = Path(__file__).resolve().parent
MARKET_DIR = Path(os.environ.get("MARKET_DIR", str(BASE_DIR / ".market")))
MEMORY_FILE = Path("memory.json")
MAX_RECENT_HISTORY = 5
# Buyer policy choices, not market rules or empirically optimal discounts.
FOOD_LIST_PRICE_PERCENT = 90
GOODS_OFFER_CAP_PERCENT = 92
GOODS_MIN_DISCOUNT_PERCENT = 20
MATURE_SETTLE_MIN_SAMPLES = 5
MATURE_GOODS_CAP_PERCENT = 67
MATURE_FOOD_CAP_PERCENT = 66
MATURE_UNCOVERED_MIN_SAVED = 0.25
COMPARABLE_WINDOW_S = 3 * 3600
COMPARABLE_MIN_SAMPLES = 3
REQUEST_DEADLINE = ContextVar("buyer_request_deadline", default=None)
TURN_EVENT_MAX = 13  # card + inquiry + 3 counters + final accept, request/result + summary
TURN_EVENT_BYTES = 1024


def diagnostic(message: str) -> None:
    """Escape even seller-controlled newlines; never emit a forged JSON line."""
    print(json.dumps(f"[buyer] {message}", ensure_ascii=False), file=sys.stderr, flush=True)


def turn_event(active, phase, boundary, turn=0, branch="first", ask_before=None,
               offer=None, ask_after=None, state=None, error=None, rpc_error=False,
               selected=False):
    """Best-effort bounded Round 3 telemetry, never raw messages/IDs/receipts.

    One opaque attempt ID suffices to join its turns; deliberately omit server
    task/order IDs and account/URL metadata. Existing parsers supply prices.
    """
    try:
        if not active.get("telemetry_enabled"):
            return
        count = active.get("telemetry_count", 0)
        if count >= TURN_EVENT_MAX:
            return
        active["telemetry_count"] = count + 1
        if boundary == "request":
            active["telemetry_pending"] = (phase, turn, branch, ask_before, offer)
        else:
            active.pop("telemetry_pending", None)
        if "telemetry_id" not in active:
            active["telemetry_id"] = uuid.uuid4().hex
        states = {"TASK_STATE_COMPLETED": "completed", "TASK_STATE_INPUT_REQUIRED": "inputrequired",
                  "TASK_STATE_REJECTED": "rejected", "TASK_STATE_CANCELED": "canceled"}
        response_state = states.get(state, "other" if state is not None else None)
        error_type = None
        if error is not None:
            if isinstance(error, (TimeoutError, httpx.TimeoutException)):
                error_type = "TimeoutError"
            elif isinstance(error, httpx.HTTPError):
                error_type = "HTTPError"
            elif isinstance(error, (ValueError, TypeError, KeyError)):
                error_type = "ParseError"
            else:
                error_type = "Error"
        elif rpc_error:
            error_type = "RPCError"
        if phase == "attempt" and not selected and error_type is None:
            error_type = active.get("telemetry_error_type")
        outcome = ("request" if boundary == "request" else
                   "timeout" if error_type == "TimeoutError" else
                   "error" if error_type else response_state or "completed")
        if phase == "attempt":
            outcome = ("completed" if selected else
                       "timeout" if error_type == "TimeoutError" else "error" if error_type else
                       active.get("telemetry_outcome") if active.get("telemetry_outcome") in ("error", "timeout")
                       else "rejected")
        elif boundary == "response":
            active["telemetry_outcome"] = outcome
            active["telemetry_error_type"] = error_type
        def price(value):
            return value if type(value) is int and 0 <= value <= 10**12 else None
        sku = active.get("telemetry_sku")
        sku = sku if isinstance(sku, str) and re.fullmatch(r"[gGfF][0-9a-fA-F]{7,10}", sku) else None
        event = dict(event="buyer_turn", schema_version=1,
                     timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                     attempt_id=active["telemetry_id"], seq=count + 1, sku=sku,
                     phase=phase, boundary=boundary, turn_index=turn, policy_branch=branch,
                     ask_before=price(ask_before), offer_sent=price(offer), ask_after=price(ask_after),
                     response_state=response_state, outcome=outcome, error_type=error_type)
        if phase == "attempt":
            event.update(receipt_selected=bool(selected), payment_confirmed=False)
        payload = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
        # Retain diagnostic's JSON-string envelope: kit/legacy consumers must
        # never mistake a telemetry object for the final stdout receipt.
        line = json.dumps("[buyer] " + payload, ensure_ascii=True)
        if len(line.encode("utf-8")) + 1 <= TURN_EVENT_BYTES:
            print(line, file=sys.stderr, flush=True)
    except Exception:
        # A broken stderr/serializer/clock must never change checkout or cleanup.
        pass


def seller_response_text(response: dict) -> str:
    """Untrusted last response only; status text takes precedence over artifacts."""
    task = response.get("task", response)
    parts = ((task.get("status") or {}).get("message") or {}).get("parts") or []
    texts = [p["text"] for p in parts if isinstance(p.get("text"), str)]
    if not any(t.strip() for t in texts):
        texts = [p["text"] for art in task.get("artifacts") or []
                 for p in art.get("parts") or [] if isinstance(p.get("text"), str)]
    text = "".join(" " if c.isspace() or unicodedata.category(c).startswith("C") else c
                   for c in " ".join(texts))
    return " ".join(text.split())[:160]


# Seller refusals that mean "the snapshot was stale", not "the price was wrong" (11/04 audit:
# 46 of 91 failures were 0-round rejections, mostly these texts). Matched case-insensitively.
STOCKOUT_PATTERNS = ("none of that in stock", "no matching stock", "out of stock", "not in stock",
                     "sold out", "no stock")
UNIT_MISMATCH_PATTERNS = ("no available unit meets", "meets the requested expiry")
# Skip windows live in adaptive_strategy (STOCKOUT_SKIP_S=600, MISMATCH_SKIP_S=300).


def classify_seller_refusal(seller_text: str) -> str | None:
    """Untrusted text is only matched, never echoed into reviews or executed."""
    text = (seller_text or "").lower()
    if any(p in text for p in UNIT_MISMATCH_PATTERNS):
        return "unit_mismatch"
    if any(p in text for p in STOCKOUT_PATTERNS):
        return "stockout"
    return None


def failure_kind(failure_reason: str) -> str:
    """Classify trusted reason before appending any seller-controlled snippet."""
    reason = failure_reason.split(' | seller: "', 1)[0].lower()
    if "had no stock of" in reason:
        return "out_of_stock"
    if "no unit matching" in reason:
        return "unit_mismatch"
    if "timeout" in reason or "deadline" in reason:
        return "timeout"
    if "no candidates" in reason or "no shop currently" in reason:
        return "no_candidates"
    if "rpc" in reason or "opening inquiry" in reason or "card endpoint failed" in reason:
        return "rpc_error"
    if "unsigned" in reason:
        return "unsigned"
    if any(s in reason for s in ("declined-price-policy", "invalid receipt", "break rate")):
        return "receipt_policy"
    if "task_state_rejected" in reason:
        return "rejected_open" if "0 round" in reason else "rejected_after_offer"
    if "counter limit" in reason:
        return "counter_limit"
    if "exceeds price limit" in reason:
        return "price_above_limit"
    return "other"


def failure_diagnostic(reason: str, seller_text: str = "") -> None:
    kind = failure_kind(reason)
    if seller_text:
        reason += f' | seller: "{seller_text}"'
    # Keep the existing JSON-string diagnostic envelope, including for hostile text.
    diagnostic("attempt failed: " + json.dumps({
        "failure_reason": reason, "failure_kind": kind, "seller_text": seller_text,
    }, ensure_ascii=False))


def request_timeout(default: float) -> float:
    """Round 2 keeps its timeouts; Round 3 shares a monotonic wall-clock budget.

    HTTPX timeouts bound transport phases, not an uninterruptible OS call or a
    trickling response's total duration. Reject late receipts below as well;
    a hard process wall-clock kill must be enforced by the calling kit.
    """
    deadline = REQUEST_DEADLINE.get()
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("buyer deadline exhausted")
    return min(default, remaining)


RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}
SERVICE_KEYWORDS = (
    "warranty", "service", "refund", "guarantee", "quality", "support",
    "vip", "bundle", "care", "return", "assurance", "policy", "replacement",
    "member", "welcome", "discount", "benefit", "cert", "grade",
)

FUNNY_NO_DEAL_REVIEWS = [
    "No deal after {rounds} round(s): seller clung to {ask} while my budget was {budget}.",
    "No deal in {rounds} round(s): asking price {ask} stayed above budget {budget}.",
    "No deal after {rounds} round(s): could not close within budget {budget} (seller ask {ask}).",
]


def get_gemini_api_key() -> str:
    """Safely obtain GEMINI_API_KEY from environment or ~/.bashrc fallback."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    bashrc_path = os.path.expanduser("~/.bashrc")
    if os.path.exists(bashrc_path):
        try:
            with open(bashrc_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().startswith("export GEMINI_API_KEY="):
                        val = line.strip().split("export GEMINI_API_KEY=", 1)[1].strip().strip('"\'')
                        if val:
                            return val
        except Exception:
            pass
    return ""


def call_gemini_api(prompt: str) -> str:
    """Call Google Gemini API (gemini-3.5-flash-lite) as an ultra-fast, zero-cost fallback."""
    api_key = get_gemini_api_key()
    if not api_key:
        return ""
    # Try gemini-3.5-flash-lite first, fallback to gemini-3.8-flash if needed
    for model in ("gemini-3.5-flash-lite", "gemini-3.8-flash"):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 64},
        }
        try:
            with httpx.Client(timeout=6.0) as client:
                resp = client.post(url, json=payload, headers={"Content-Type": "application/json"})
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            text = parts[0].get("text", "").strip()
                            if text:
                                return text
        except Exception as e:
            diagnostic(f"[Gemini Fallback Warning ({model})]: {e}")
    return ""


def call_harness(prompt: str) -> str:
    """Call Codex CLI harness non-interactively in an empty directory.
    Falls back to Google Gemini API (gemini-2.5-flash) if OpenAI CLI fails, times out, or quota is exhausted."""
    # 1. Primary: OpenAI CLI via Codex harness
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        tmp_path = tmp.name
    empty_dir = tempfile.gettempdir()
    p = None
    try:
        cmd = f'codex exec --skip-git-repo-check --ephemeral --cd "{empty_dir}" -m gpt-5.6-sol -o "{tmp_path}" -'
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            text=True,
        )
        p.communicate(input=prompt, timeout=18)
        if os.path.exists(tmp_path):
            with open(tmp_path, "r", encoding="utf-8") as f:
                res = f.read().strip()
                if res:
                    return res
    except Exception as e:
        diagnostic(f"[Buyer Harness Warning]: {e}")
        if p:
            try:
                p.kill()
            except Exception:
                pass
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    # 2. Secondary Fallback: Google Gemini API (gemini-2.5-flash)
    gemini_res = call_gemini_api(prompt)
    if gemini_res:
        return gemini_res

    return ""


def load_memory() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def valid_stars(value: Any) -> bool:
    """Unpaid/no-deal entries have null stars, not a synthetic three-star review."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 1 <= value <= 5)


def update_memory(memory: dict, shop_name: str, entry: dict) -> None:
    """Store up to 5 recent trades per shop; roll older entries into archived_avg_stars."""
    rec = memory.get(shop_name, {
        "archived_count": 0,
        "archived_avg_stars": 0.0,
        "dishonest": False,
        "recent": [],
    })
    if entry.get("outcome") == "rip-off":
        rec["dishonest"] = True

    recent = rec.get("recent", [])
    recent.append(entry)
    while len(recent) > MAX_RECENT_HISTORY:
        oldest = recent.pop(0)
        old_cnt = rec.get("archived_count", 0)
        old_avg = rec.get("archived_avg_stars", 0.0)
        if not valid_stars(oldest.get("stars")):
            continue
        old_stars = float(oldest["stars"])
        new_cnt = old_cnt + 1
        rec["archived_count"] = new_cnt
        rec["archived_avg_stars"] = round((old_avg * old_cnt + old_stars) / new_cnt, 2)

    rec["recent"] = recent
    rec["last_visited_ts"] = time.time()
    memory[shop_name] = rec
    try:
        MEMORY_FILE.write_text(json.dumps(memory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as e:
        diagnostic(f"[Memory Warning]: {e}")


def fetch_board_stats() -> dict[str, dict]:
    market_url = os.environ.get("MARKET_URL", "https://market.ourolabs.ai").rstrip("/")
    try:
        r = httpx.get(f"{market_url}/api/board", timeout=request_timeout(2.5)).json()
        return {s["name"]: s for s in r.get("shops", []) if isinstance(s, dict) and "name" in s}
    except Exception:
        return {}


def summarize_shop(shop: dict, memory: dict, board_row: dict | None = None, hub_trust: dict | None = None) -> dict:
    name = shop.get("name", "")
    url = shop.get("url", "").rstrip("/")
    reviews = shop.get("reviews") or []
    board_row = board_row or {}
    hub_trust = (hub_trust or {}).get(url) or {}

    ext_stars = [r["stars"] for r in reviews if valid_stars(r.get("stars"))]
    ext_avg = round(sum(ext_stars) / len(ext_stars), 2) if ext_stars else board_row.get("stars")

    # Detect actual receipt mismatch flags while ignoring prompt-injection advertisements
    bad_keywords = ("receipt said", "receipt says", "higher than", "overcharged", "rip-off", "scam", "forged", "dishonest")
    ext_scam_flags = sum(
        1 for r in reviews
        if (valid_stars(r.get("stars")) and r["stars"] <= 2
            and any(k in str(r.get("review", "")).lower() for k in bad_keywords))
    )

    mem = memory.get(name, {})
    recent = mem.get("recent", [])
    arch_cnt = mem.get("archived_count", 0)
    arch_avg = mem.get("archived_avg_stars", 0.0)
    last_ts = float(mem.get("last_visited_ts", 0.0))

    total_mem_cnt = arch_cnt + len(recent)
    rated = [r["stars"] for r in recent if valid_stars(r.get("stars"))]
    if arch_cnt + len(rated) > 0:
        total_stars = arch_avg * arch_cnt + sum(rated)
        mem_avg = round(total_stars / (arch_cnt + len(rated)), 2)
    else:
        mem_avg = None

    dishonest_claims = int(hub_trust.get("dishonest", 0))
    honest_claims = int(hub_trust.get("honest", 0))
    settled_cnt = int(hub_trust.get("settled", 0))
    broken_cnt = int(hub_trust.get("broken", 0))
    refuses_cnt = int(hub_trust.get("refuses", 0))

    dishonest = bool(mem.get("dishonest")) or ext_scam_flags > 0 or dishonest_claims > 0

    deals = int(board_row.get("deals") or 0)
    reviews_cnt = int(board_row.get("reviews") if board_row.get("reviews") is not None else len(ext_stars))
    visits = int(board_row.get("visits") or 0)
    share = float(board_row.get("share") or 0.0)
    is_house = bool(board_row.get("house")) or "market.ourolabs.ai/shop/" in url

    score = 3.8
    if ext_avg is not None:
        score = float(ext_avg)
    if mem_avg is not None:
        score = (score + mem_avg) / 2.0 if ext_avg is not None else mem_avg
    if dishonest:
        score -= 10.0
    elif ext_avg is not None and ext_avg <= 2.2:
        score -= 5.0

    # Apply Round 3 empirical trust adjustments from state_hub.json
    score += min(1.2, (honest_claims + settled_cnt) * 0.20)
    score -= broken_cnt * 0.6
    score -= refuses_cnt * 1.2

    return {
        "name": name,
        "url": url,
        "ext_avg": ext_avg,
        "ext_count": reviews_cnt,
        "deals": deals,
        "visits": visits,
        "share": share,
        "is_house": is_house,
        "mem_avg": mem_avg,
        "mem_count": total_mem_cnt,
        "arch_count": arch_cnt,
        "arch_avg": arch_avg,
        "last_ts": last_ts,
        "dishonest": dishonest,
        "broken_cnt": broken_cnt,
        "score": round(score, 2),
        "offers": shop.get("offers") or [],
    }


def check_acp_support(shop_url: str, timeout: float = 2.0) -> bool | None:
    """Probe /.well-known/acp.json to verify if a shop runs kit v3/v4 ACP checkout."""
    base = shop_url.rstrip("/")
    try:
        r = httpx.get(f"{base}/.well-known/acp.json", timeout=request_timeout(timeout))
        if r.status_code != 200:
            return False if r.status_code in (404, 405, 410) else None
        data = r.json()
        services = (data.get("capabilities") or {}).get("services") or []
        return data.get("protocol") == "acp" and "checkout" in services
    except Exception:
        return None  # Transport failure is not proof of unsupported ACP.


def settled_price_samples(hub, sku):
    """Legacy SKU-market order amounts, not shop quotes or fresh unit comparables.

    The stored scalar schema has no timestamp/qty/grade/shop. Do not invent
    freshness or quantities; count valid observations, retaining the last 15.
    """
    rows = (hub.get("demand_radar", {}).get("sku_settles", {}).get(sku) or [])
    if not isinstance(rows, list):
        return []
    return [p for p in rows if positive_number(p) and p <= 10**12][-15:]


def goods_price_cap(catalog, hub, sku, budget):
    """One integer cap for selected AND receipt SKUs; unknown list means defer.

    Minimum savings is a policy assumption, not a predicted executable price.
    Keep the earlier offer/comparable/list/budget restrictions as upper bounds;
    receipt quality/warranty validation remains separate and mandatory.
    """
    lp = (catalog.get(sku) or {}).get("list_price")
    if not positive_number(lp) or not float(lp).is_integer():
        return None
    lp = int(lp)
    cap = min(budget, lp - 1, lp * GOODS_OFFER_CAP_PERCENT // 100,
              lp * (100 - GOODS_MIN_DISCOUNT_PERCENT) // 100)
    settles = settled_price_samples(hub, sku)
    if len(settles) >= MATURE_SETTLE_MIN_SAMPLES:
        cap = min(cap, lp * MATURE_GOODS_CAP_PERCENT // 100,
                  max(lp * 58 // 100,
                      math.floor(statistics.median(settles))))
    elif len(settles) >= 3:
        cap = min(cap, max(lp * 88 // 100,
                           math.floor(statistics.median(settles) * 112 / 100)))
    return cap if cap >= 1 else None


def goods_expected_saved(amount, list_price, grade, warranty_h=None):
    """Per-order Saved proxy, using policy priors, NOT empirical failure rates.

    Missing warranty has no benefit; short coverage has no assumed timing
    distribution. Missing/unknown string grades use C; invalid types abstain.
    Claims still need kit certs.
    """
    if (not positive_number(amount) or not positive_number(list_price)
            or (grade is not None and not isinstance(grade, str))):
        return None
    grade = grade if grade in BREAK_RATE else 'C'
    if warranty_h is None:
        warranty_h = 0
    if (not isinstance(warranty_h, (int, float)) or isinstance(warranty_h, bool)
            or not math.isfinite(warranty_h) or warranty_h < 0):
        return None
    q = BREAK_RATE[grade]
    discount = (list_price - amount) / list_price
    return (1 - q) * discount - (0 if warranty_h >= GOODS_BREAK_WINDOW_H else q)


def goods_ranking_proxy(hub, sku, shop_name, shop_url, list_price, prior_pay, now):
    """Fresh shop x SKU x condition observations; never executable quotes.

    Three observations per condition required. Shrink price toward the SKU
    price prior with three pseudo-observations, not toward invented A/coverage.
    Otherwise use C/no-refund pessimism, so unknown terms get no free benefit.
    Existing collection, own-buyer exclusion and 50-row/3h bounds are unchanged.
    """
    groups = {}
    rows = hub.get('demand_radar', {}).get('sku_comparables', {}).get(sku) or []
    if not isinstance(rows, list):
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts, amount = row.get('ts'), row.get('amount')
        grade, warranty = row.get('grade'), row.get('warranty_hours')
        if (row.get('shop') not in (shop_name, shop_url)
                or not positive_number(ts) or not 0 <= now - ts <= COMPARABLE_WINDOW_S
                or not positive_number(amount) or amount > 10**12
                or not isinstance(grade, str) or grade not in BREAK_RATE
                or warranty is None
                or goods_expected_saved(amount, list_price, grade, warranty) is None):
            continue
        condition = (grade, warranty >= GOODS_BREAK_WINDOW_H)
        groups.setdefault(condition, []).append((ts, amount))
    qualified = [(condition, samples) for condition, samples in groups.items()
                 if len(samples) >= COMPARABLE_MIN_SAMPLES]
    if not qualified:
        return goods_expected_saved(prior_pay, list_price, 'C', 0)
    # Most recently observed qualified condition, not the most flattering score.
    (grade, full_window), samples = max(qualified, key=lambda group: max(r[0] for r in group[1]))
    n = len(samples)
    predicted_pay = (n * statistics.median(r[1] for r in samples)
                     + COMPARABLE_MIN_SAMPLES * prior_pay) / (n + COMPARABLE_MIN_SAMPLES)
    return goods_expected_saved(predicted_pay, list_price, grade,
                                GOODS_BREAK_WINDOW_H if full_window else 0)


def pick_round3_shop_and_sku(
    shops: list[dict],
    memory: dict,
    hub: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    order_data: dict[str, Any],
    budget: int,
) -> tuple[dict, dict[str, Any] | None, str]:
    """Rank acceptable pairs by a per-order savings proxy (goods: risk-aware).

    This is a per-candidate proxy, not the aggregate Saved score (sum of scored
    transaction savings rates / (deals + 100)). Goods condition observations
    predict neither the next unit nor executable prices. Keep feasibility,
    trust, bestseller and diversity policies unchanged.
    """
    acceptable = set(order_data.get("acceptable") or [])
    shops = [s for s in shops if any(off.get("sku") in acceptable for off in s.get("offers") or [])]
    if not shops:
        return None, None, "No shop currently holds in-stock acceptable SKUs."
    board_map = fetch_board_stats()
    hub_trust = hub.get("shop_trust") or {}
    summaries = [summarize_shop(s, memory, board_map.get(s.get("name", "")), hub_trust) for s in shops]

    # shops.json offers list every SKU of the category a shop holds, not only the order's acceptable ones.
    # Keep only acceptable SKUs, so a shop that holds none of them is not a candidate.
    acceptable = set(order_data.get("acceptable") or [])
    for c in summaries:
        c["offers"] = [off for off in c["offers"] if off.get("sku") in acceptable]

    # Filter to shops that actually have matching in-stock offers for this order.
    # Prefer student shops over house shops (which rarely complete Round 3 ACP checkout).
    student_stocked = [c for c in summaries if c.get("offers") and not c["dishonest"] and not c["is_house"]]
    stocked = student_stocked or [c for c in summaries if c.get("offers") and not c["dishonest"]]
    if not stocked:
        stocked = [c for c in summaries if c.get("offers")]

    now_ts = time.time()
    best_candidates: list[tuple[float, dict, dict[str, Any]]] = []

    for c in stocked:
        # Diversity penalty so we spread purchases cleanly across the market
        div_pen = 0.0
        if (now_ts - float(c.get("last_ts") or 0.0)) < 150.0:
            div_pen += 0.35
        if int(c.get("mem_count") or 0) >= 4:
            div_pen += 0.25
        recent_entries = (memory.get(c["name"]) or {}).get("recent") or []
        recent_fails = sum(
            1 for r in recent_entries[-3:]
            if r.get("outcome") in ("no-deal", "declined-price-policy", "declined-no-warranty")
        )
        div_pen += 0.22 * recent_fails

        for off in c.get("offers") or []:
            sku = off.get("sku", "")
            sku_meta = catalog.get(sku) or {}
            if order_data.get("kind") == "goods" and goods_price_cap(catalog, hub, sku, budget) is None:
                # Reject unpriceable SKUs before choosing/probing a URL, so they
                # cannot hide a valid SKU held by the same shop from this attempt.
                continue
            if order_data.get("kind") == "food" and not positive_number(sku_meta.get("list_price")):
                continue
            # Goods/food defer above; keep fallback for other legacy kinds only.
            # Never divide by invalid/nonpositive prices.
            if isinstance(sku_meta.get("list_price"), bool):
                continue
            price_basis = sku_meta.get("list_price") or budget
            if not positive_number(price_basis):
                continue
            list_price = int(price_basis)
            if list_price <= 0:
                continue
            # A shop can sell a SKU if its cost (~60-75% of list_price) is <= our budget.
            # If list_price is way above budget, the shop's 75% supply cost floor may exceed our budget.
            est_cost_floor = int(list_price * 0.65)
            wholesale_floor = int(list_price * 0.76)
            if est_cost_floor > budget:
                # High risk of walkaway because our budget is below the shop's likely supply cost
                feasibility_mult = 0.15
            elif wholesale_floor > budget:
                # Budget is between 65% and 76% of list_price: only B/C grade or legacy stock can close
                feasibility_mult = 0.45
            else:
                feasibility_mult = 1.0

            settles = settled_price_samples(hub, sku)
            if len(settles) >= 3:
                # Preserve evidence even above budget/purchase cap or below our
                # estimated cost floor. Never fabricate an affordable discount.
                est_pay = statistics.median(settles)
            else:
                # No sufficient evidence: explicit 80%-of-list prior, not a quote.
                est_pay = min(budget, max(est_cost_floor, int(list_price * 0.80)))
            est_savings_rate = (list_price - est_pay) / list_price
            if order_data.get("kind") == "goods":
                est_savings_rate = goods_ranking_proxy(
                    hub, sku, c['name'], c['url'], list_price, est_pay, now_ts)
            # Combined utility: expected savings rate * existing weights
            # Give an extra preference to market bestsellers with proven trading liquidity
            bestseller_boost = 1.35 if sku in (
                "G1515d951", "G07ec17d5", "G8ec51935", "G03d0199f",
                "G2cb8041b", "G5cd4ffbc", "Ge62449c9", "G75f11c19", "G0be9ed07", "G200e39a3"
            ) else 1.0
            trust_factor = max(0.5, min(1.4, (c["score"] / 4.2) - div_pen))
            if int(c.get("deals") or 0) >= 20:
                trust_factor *= 1.15
            # Raw median/rate stay truthful. Floor utility only: multiplying a
            # negative rate by penalties must not reward less trustworthy shops.
            # Nonpositive candidates remain tied/available in existing order.
            utility = max(0.0, est_savings_rate) * feasibility_mult * trust_factor * bestseller_boost
            best_candidates.append((utility, c, {"sku": sku, "name": off.get("name", sku), "list_price": list_price, "est_cost": est_cost_floor}))

    best_candidates = filter_candidates(best_candidates, hub, now_ts)
    if best_candidates:
        best_candidates.sort(key=lambda x: x[0], reverse=True)
        # One SKU per URL; explicitly unsupported ACP must never be a fallback.
        seen_urls = set()
        uncertain = None
        for util, cand_summary, sku_target in best_candidates:
            url = cand_summary["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            request_timeout(2.0)
            support = check_acp_support(url)
            if support is True:
                shop_obj = next((s for s in shops if s["url"].rstrip("/") == cand_summary["url"]), shops[0])
                reason = (
                    f"[R3-Surplus-Max] Picked {cand_summary['name']} holding SKU {sku_target['sku']} "
                    f"(list_price={sku_target['list_price']}, budget={budget}, trust={cand_summary['score']})"
                )
                return shop_obj, sku_target, reason
            if support is None and uncertain is None:
                uncertain = (cand_summary, sku_target)
        if uncertain is not None:
            cand_summary, sku_target = uncertain
            shop_obj = next(s for s in shops if s["url"].rstrip("/") == cand_summary["url"])
            return shop_obj, sku_target, f"[R3-Surplus-Fallback] ACP probe inconclusive for {cand_summary['name']}"

    # No shop currently holds in-stock SKUs matching this order's acceptable list.
    # Do NOT fall back to picking an arbitrary shop without an acceptable SKU, as doing so leads to
    # purchasing out-of-order SKUs and falsely branding innocent shops as scammers!
    return None, None, f"No shop currently holds in-stock acceptable SKUs for order {order_data.get('order')}"


def pick_shop(shops: list[dict], memory: dict, item: str, budget: int) -> tuple[dict, str]:
    if len(shops) == 1:
        s = shops[0]
        return s, f"Only one shop available in list ({s['name']})."

    # Unit test mode (2 shops in buyer_reads_reviews): pick highest trust score directly
    if len(shops) <= 2:
        summaries = [summarize_shop(s, memory) for s in shops]
        summaries_sorted = sorted(summaries, key=lambda x: x["score"], reverse=True)
        best = summaries_sorted[0]
        shop_obj = next((s for s in shops if s["url"].rstrip("/") == best["url"]), shops[0])
        return shop_obj, f"Selected {best['name']} with highest review score ({best['score']})."

    board_map = fetch_board_stats()
    summaries = [summarize_shop(s, memory, board_map.get(s.get("name", ""))) for s in shops]
    student_honest = [c for c in summaries if not c["dishonest"] and not c["is_house"]]
    honest_pool = student_honest if student_honest else [c for c in summaries if not c["dishonest"]]
    if not honest_pool:
        honest_pool = summaries

    now_ts = time.time()
    def get_diversity_penalty(summary: dict) -> int:
        last_ts = float(summary.get("last_ts") or 0.0)
        pen = 0
        if (now_ts - last_ts) < 180.0:
            pen += 1
        if int(summary.get("mem_count") or 0) >= 3:
            pen += 1
        return pen

    ranked = sorted(
        honest_pool,
        key=lambda x: (get_diversity_penalty(x), x["mem_count"], -x["score"]),
    )
    top_candidates = ranked[:6]
    acp_candidates = [c for c in top_candidates if check_acp_support(c["url"])]
    if acp_candidates:
        top_candidates = acp_candidates

    chosen = top_candidates[0]
    shop_obj = next((s for s in shops if s["url"].rstrip("/") == chosen["url"]), shops[0])
    return shop_obj, f"Selected {chosen['name']} (score={chosen['score']}, visits={chosen['mem_count']})."


def build_accept_policy(order_data: dict[str, Any], agreed_price: int, list_price: int,
                        receipt_warranty_h: float = 0, receipt_grade: str = "C") -> dict[str, Any]:
    """Construct Round 3 pre-payment `accept` policy verified by market_kit.py before paying."""
    kind = order_data.get("kind")
    policy: dict[str, Any] = {"cert": True}
    if kind == "food":
        use_hours = float(order_data.get("use_within_hours") or 1.0)
        # Require at least use_within_hours (+ 0.25h safety margin so food never expires during settlement)
        policy["min_hours_left"] = round(use_hours + 0.25, 2)
        policy["grades"] = ["A", "B", "C"]
    elif kind == "goods":
        # Never allow a worse grade than the receipt claim, even when full
        # coverage would make that grade positive. The kit checks actual certs.
        if receipt_warranty_h is None:
            receipt_warranty_h = 0
        score = goods_expected_saved(agreed_price, list_price, receipt_grade, receipt_warranty_h)
        if score is None or score <= 0:
            raise ValueError("goods receipt has no positive expected Saved")
        receipt_grade = receipt_grade if receipt_grade in BREAK_RATE else 'C'
        policy["grades"] = [grade for grade in BREAK_RATE
                            if BREAK_RATE[grade] <= BREAK_RATE[receipt_grade]
                            and goods_expected_saved(agreed_price, list_price, grade, receipt_warranty_h) > 0]
        # The kit builds the checkout from the shop's own terms, not from our copy of the receipt. If the shop
        # promised the full-window warranty, require it, so a checkout without it is not paid. Deals agreed
        # without a warranty were already checked for positive expected Saved before getting here.
        if receipt_warranty_h >= GOODS_BREAK_WINDOW_H:
            policy["warranty_hours"] = GOODS_BREAK_WINDOW_H
        elif positive_number(receipt_warranty_h):
            policy["warranty_hours"] = receipt_warranty_h
    return policy


def positive_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def food_lifetime_precheck(receipt: dict, accept: dict, now: float | None = None) -> str | None:
    """Decline known-bad hints, never authenticate a receipt or replace kit checks.

    Only the existing numeric absolute expires_at format is understood. Missing,
    malformed and relative hours_left hints remain unknown (not proof of freshness).
    Use the final merged accept, including any comparable-price shelf-life gate.
    """
    if receipt.get("cert") is False:
        return "seller explicitly declined the required market certificate"
    expiry = receipt.get("expires_at")
    if not positive_number(expiry):
        return None
    now = time.time() if now is None else now
    hours = (expiry - now) / 3600
    if hours < accept["min_hours_left"]:
        return f"hint has {hours:.6g}h left; accept requires {accept['min_hours_left']}h"
    return None


def similar_food_remaining(sample_hours: float, target_hours: float, required_hours: float) -> bool:
    """Compare shelf life at each sale, not today's aged historical inventory.

    Require the use window plus checkout margin, the same doubling bucket, and
    at most 25% difference. Clearance/short-life prices must not anchor fresh food.
    """
    minimum = required_hours + 0.25
    return (sample_hours >= minimum and target_hours >= minimum
            and int(math.log2(sample_hours)) == int(math.log2(target_hours))
            and 0.8 * target_hours <= sample_hours <= 1.25 * target_hours)


def food_price_policy(catalog: dict, hub: dict, sku: str, budget: int, order: dict,
                      receipt: dict | None = None, now: float | None = None) -> tuple[int | None, dict]:
    """Local-only cap. Unknown catalog price means defer, never budget*1.2.

    Before negotiation there is no guaranteed grade/expiry, so use only the 90%
    list cap. Receipt claims are *hints*, not certificates: comparable tightening
    is conditional on kit cert + exact grade + minimum shelf-life acceptance.
    Unknown grades never become C here (unlike goods break-risk pessimism).
    """
    lp = (catalog.get(sku) or {}).get("list_price")
    if not positive_number(lp):
        return None, {}
    cap = min(budget, math.floor(lp * FOOD_LIST_PRICE_PERCENT / 100))
    settles = settled_price_samples(hub, sku)
    if len(settles) >= MATURE_SETTLE_MIN_SAMPLES:
        cap = min(cap, math.floor(lp * MATURE_FOOD_CAP_PERCENT / 100),
                  max(math.floor(lp * 54 / 100),
                      math.floor(statistics.median(settles))))
    receipt = receipt or {}
    grade = receipt.get("claim")
    expiry = receipt.get("expires_at")
    now = time.time() if now is None else now
    if grade not in ("A", "B", "C") or not positive_number(expiry):
        return cap, {}
    hours = (expiry - now) / 3600
    required = float(order.get("use_within_hours") or 1.0)
    if hours < required + 0.25:
        return cap, {}
    prices = []
    for row in (hub.get("demand_radar", {}).get("sku_comparables", {}).get(sku) or []):
        ts, amount, exp = row.get("ts"), row.get("amount"), row.get("expires_at")
        if (not positive_number(ts) or not 0 <= now - ts <= COMPARABLE_WINDOW_S
                or not positive_number(amount) or row.get("grade") != grade
                or not positive_number(exp)):
            continue
        if similar_food_remaining((exp - ts) / 3600, hours, required):
            prices.append(amount)
    if len(prices) < COMPARABLE_MIN_SAMPLES:
        return cap, {}
    tightened = min(cap, math.floor(statistics.median(prices) * 110 / 100))
    # Enforce the hint's lower bound through the kit, not through trust in text.
    # A longer actual shelf life is safe; it never increases our price ceiling.
    # Use the comparison bucket's lower bound, not the unit's current remaining
    # hours: the latter would fail as soon as time passes before checkout.
    minimum_hours = max(required + 0.25, 0.8 * hours, 2 ** int(math.log2(hours)))
    return tightened, {"grades": [grade], "min_hours_left": minimum_hours}


def rpc_call(url: str, method: str, params: dict) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4())[:8],
        "method": method,
        "params": params,
    }
    headers = {"A2A-Version": "1.0", "Content-Type": "application/json"}
    resp = httpx.post(url, json=body, headers=headers, timeout=request_timeout(45))
    data = resp.json()
    if "error" in data:
        return {"error": data["error"]}
    return data.get("result", {})


def extract_last_number(task: dict, min_valid: int = 50, max_valid: int = 10_000_000) -> int | None:
    msg = (task or {}).get("status", {}).get("message", {})
    text = " ".join(p.get("text", "") for p in msg.get("parts", []))
    # Strip SKU IDs, unit IDs, percentages, hours ("24h", "720.0h"), and list_price labels before extracting ask price!
    cleaned = re.sub(r"\b[gfGF][0-9a-fA-F]{7,10}\b", " ", text)
    cleaned = re.sub(r"\bu_[0-9a-zA-Z_]+\b", " ", cleaned)
    cleaned = re.sub(r"list[_\s-]*price\s*[=:]?\s*\d+(?:,\d{3})*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(\s*list\s+\d+(?:,\d{3})*\s*\)", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\d+(?:\.\d+)?\s*(?:%|h|hr|hrs|hours)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", "")

    # Prefer explicit "asking price X" or "ask X" or "price X" patterns if present
    explicit = re.findall(r"(?:asking\s+price|ask|counteroffer|offer|at|price)\s*[=:]?\s*(\d{2,7})\b", cleaned, flags=re.IGNORECASE)
    if explicit:
        val = int(explicit[-1])
        if min_valid <= val <= max_valid:
            return val

    nums = [math.ceil(float(x)) for x in re.findall(r"\b\d+(?:\.\d+)?\b", cleaned)]
    price_nums = [n for n in nums if min_valid <= n <= max_valid]
    if price_nums:
        return price_nums[-1]
    if nums:
        return nums[-1]
    return None


def extract_receipt_data(task: dict) -> dict | None:
    for art in (task or {}).get("artifacts") or []:
        for part in art.get("parts") or []:
            data = part.get("data")
            if isinstance(data, dict) and "price" in data:
                return data
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shops", required=True, help="Path to shops.json")
    parser.add_argument("--item", required=True, help="Item or category to buy")
    parser.add_argument("--budget", type=int, required=True, help="Maximum budget")
    parser.add_argument("--order", default="", help="Optional path to Round 3 order.json")
    args = parser.parse_args()

    shops_path = Path(args.shops)
    shops = json.loads(shops_path.read_text(encoding="utf-8"))
    item = args.item
    item_low = item.lower()
    budget = args.budget

    # Check if Round 3 order.json is provided or exists in .market/order.json
    order_path = Path(args.order) if args.order else (shops_path.parent / "order.json")
    order_data = load_json_safe(order_path, {}) if order_path and order_path.exists() else {}
    acceptable_skus = order_data.get("acceptable") or []
    is_round3 = bool(acceptable_skus)
    # Start before local setup/selection so probing cannot spend an extra 90s.
    deadline = time.monotonic() + 90
    if is_round3 and positive_number(order_data.get("expires_at")):
        deadline = min(deadline, time.monotonic() + order_data["expires_at"] - time.time() - 5)

    memory = load_memory()
    hub = load_hub()
    catalog = load_catalog(shops_path.parent if (shops_path.parent / "catalog.json").exists() else MARKET_DIR)
    sync_broken_and_payments(hub, shops_path.parent if (shops_path.parent / "broken.jsonl").exists() else MARKET_DIR)

    if is_round3:
        strategy = record_buyer_order(order_data.get("category") or item)
        if isinstance(strategy, dict):
            hub["adaptive_strategy"] = strategy
        result = run_round3(shops, memory, hub, catalog, order_data, item, budget, deadline)
    else:
        target_sku_info = None
        chosen_shop, reason = pick_shop(shops, memory, item_low, budget)
        result = run_attempt(chosen_shop, target_sku_info, reason, memory, hub, catalog,
                             order_data, item, budget)
    # Exactly one receipt leaves the hook. Checkout/accept/signature verification
    # happens in the kit AFTER buyer exit, so failures there cannot retry here.
    print(json.dumps(result, ensure_ascii=False), flush=True)


def run_round3(shops, memory, hub, catalog, order_data, item, budget, deadline):
    result = {"shop": "", "receipt": None, "stars": None,
              "review": "No deal: no shop currently has acceptable SKUs in stock."}
    remaining_shops = list(shops)
    token = REQUEST_DEADLINE.set(deadline)
    try:
        for attempt in range(1, 4):
            try:
                request_timeout(1)
                chosen, sku, reason = pick_round3_shop_and_sku(
                    remaining_shops, memory, hub, catalog, order_data, budget)
                if chosen is None:
                    diagnostic(f"[retry] no candidates: {reason}")
                    failure_diagnostic(f"no candidates: {reason}")
                    break
                url = chosen.get("url", "").rstrip("/")
                remaining_shops = [s for s in remaining_shops if s.get("url", "").rstrip("/") != url]
                diagnostic(f"[attempt {attempt}/3] {chosen.get('name')} {url} SKU={sku['sku']}")
                shop_deadline = min(deadline, time.monotonic() + 25)
                # Reserve the last 3s of the shop's 25s window for cancellation.
                shop_token = REQUEST_DEADLINE.set(shop_deadline - 3)
                try:
                    result = run_attempt(chosen, sku, reason, memory, hub, catalog,
                                         order_data, item, budget, cleanup_deadline=shop_deadline)
                finally:
                    REQUEST_DEADLINE.reset(shop_token)
                if result.get("receipt") is not None:
                    diagnostic(f"[attempt {attempt}/3] receipt selected (checkout still pending)")
                    break
                diagnostic(f"[attempt {attempt}/3] failed: {result['review']}")
            except TimeoutError:
                diagnostic(f"[attempt {attempt}/3] stopped: total/order deadline exhausted")
                failure_diagnostic("total/order deadline exhausted")
                result["review"] = "No deal: total/order deadline exhausted."
                break
    finally:
        REQUEST_DEADLINE.reset(token)
    return result


def run_attempt(chosen_shop, target_sku_info, reason, memory, hub, catalog,
                order_data, item, budget, cleanup_deadline=None):
    active = {"telemetry_enabled": bool(order_data.get("acceptable")),
              "telemetry_sku": (target_sku_info or {}).get("sku")}
    selected = False
    result = None
    attempt_error = None
    try:
        result = _run_attempt(chosen_shop, target_sku_info, reason, memory, hub, catalog,
                              order_data, item, budget, active)
        if order_data.get("acceptable"):
            request_timeout(1)  # Includes time spent waiting for/exiting the hub lock.
        selected = result.get("receipt") is not None
        return result
    except Exception as exc:
        if not order_data.get("acceptable"):
            raise
        attempt_error = exc
        pending_event = active.get("telemetry_pending")
        if pending_event:
            phase, turn, branch, ask, offer = pending_event
            turn_event(active, phase, "response", turn, branch, ask, offer, error=exc)
        if isinstance(exc, TimeoutError) and active.get("id") and not active.get("failure"):
            # Negotiation deadline is pair-local, not proof the whole shop's network failed.
            active["failure"] = "timeout"
        result = {"shop": chosen_shop["url"], "receipt": None, "stars": None,
                  "review": f"No deal: attempt failed ({type(exc).__name__}: {exc})."}
        return result
    finally:
        turn_event(active, "attempt", "response", selected=selected, state=active.get("state"), error=attempt_error)
        if order_data.get("acceptable") and not selected and result is not None:
            failure_diagnostic(result["review"], active.get("seller_text", ""))
        if not selected and active.get("pending") is not None:
            # Remove only this attempt's record, not a concurrent replacement.
            with mutate_hub() as current_hub:
                pending = current_hub.get("pending_deals", {})
                key = str(order_data.get("order"))
                if pending.get(key) == active["pending"]:
                    del pending[key]
        if (order_data.get("acceptable") and active.get("id")
                and active.get("state") not in ("TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED")):
            cleanup_token = REQUEST_DEADLINE.set(cleanup_deadline if cleanup_deadline is not None
                                                  else REQUEST_DEADLINE.get())
            try:
                # Reserved cleanup time never extends the total/order deadline.
                rpc_call(active["url"], "CancelTask", {"id": active["id"]})
            except Exception:
                pass
            finally:
                REQUEST_DEADLINE.reset(cleanup_token)
        # Local persistence must also retain expired attempts; no network is done
        # under this lock, and a deadline failure never becomes a successful deal.
        if order_data.get("acceptable") and not selected and active.get("failure"):
            with mutate_hub() as current_hub:
                record_failure(current_hub, order_data.get("order"), chosen_shop["url"],
                               target_sku_info["sku"], active["failure"], time.time())


def _run_attempt(chosen_shop, target_sku_info, reason, memory, hub, catalog,
                 order_data, item, budget, active):
    acceptable_skus = order_data.get("acceptable") or []
    is_round3 = bool(acceptable_skus)
    item_low = item.lower()

    shop_name = chosen_shop.get("name", "unknown")
    shop_url = chosen_shop.get("url", "").rstrip("/")
    diagnostic(f"[choose] {shop_url} ({shop_name}): {reason}")

    is_round3_food = is_round3 and order_data.get("kind") == "food"
    is_round3_goods = is_round3 and order_data.get("kind") == "goods"
    price_limit = budget
    food_accept = {}
    if is_round3_food:
        price_limit, _ = food_price_policy(catalog, hub, target_sku_info["sku"], budget, order_data)
        if price_limit is None or price_limit < 1:
            return {"shop": shop_url, "receipt": None, "stars": None,
                    "review": "No deal: food catalog list price is unknown."}
    elif is_round3_goods:
        price_limit = goods_price_cap(catalog, hub, target_sku_info["sku"], budget)
        if price_limit is None:
            return {"shop": shop_url, "receipt": None, "stars": None,
                    "review": "No deal: goods catalog list price is unknown or has no safe integer price."}

    # 1. Fetch Agent Card
    card_url = f"{shop_url}/.well-known/agent-card.json"
    turn_event(active, "card", "request")
    try:
        card_resp = httpx.get(card_url, timeout=request_timeout(12))
        card = card_resp.json()
        interfaces = card.get("supportedInterfaces") or []
        rpc_url = interfaces[0].get("url") if interfaces else f"{shop_url}/"
        turn_event(active, "card", "response")
    except Exception as e:
        turn_event(active, "card", "response", error=e)
        review = f"No deal in 0 rounds: shop card endpoint failed ({type(e).__name__})."
        if isinstance(e, (TimeoutError, httpx.TimeoutException)):
            active["failure"] = "network-timeout" if isinstance(e, httpx.TimeoutException) else "timeout"
        update_memory(memory, shop_name, {
            "item": item, "budget": budget, "price": None, "rounds": 0, "stars": 2, "outcome": "no-deal"
        })
        return {"shop": chosen_shop["url"], "receipt": None, "stars": 2, "review": review}

    # 2. First message: specify exact SKU & name in Round 3, or item in Round 0-2
    if is_round3 and target_sku_info:
        sku_code = target_sku_info["sku"]
        sku_name = target_sku_info["name"]
        list_price = int(target_sku_info.get("list_price") or budget)
        mature_sku = len(settled_price_samples(hub, sku_code)) >= MATURE_SETTLE_MIN_SAMPLES
        reserve = max(500, int(list_price * (0.52 if mature_sku else 0.58)))
        opening_text = (
            f"I want to buy {sku_name} ({sku_code}). Please quote your best price with market certificate (cert=true)"
            + (f" and at least {build_accept_policy(order_data, 0, list_price)['min_hours_left']}h shelf life."
               if is_round3_food else
               f" and at least {order_data.get('use_within_hours')}h shelf life." if order_data.get("use_within_hours") else " and warranty.")
        )
    else:
        sku_code = None
        list_price = int(budget * 1.2)
        reserve = RESERVES.get(item_low, max(80, int(budget * 0.60)))
        opening_text = (
            f"I want to buy a {item}. Quality and trouble-free use matter: "
            f"do you provide after-sales service, a quality warranty, or a flexible refund policy?"
        )

    msg = {
        "role": "ROLE_USER",
        "messageId": str(uuid.uuid4()),
        "parts": [{"text": opening_text}],
    }
    turn_event(active, "opening", "request")
    try:
        if is_round3:
            request_timeout(1)
        res = rpc_call(rpc_url, "SendMessage", {"message": msg})
        active["seller_text"] = seller_response_text(res)
    except Exception as e:
        turn_event(active, "opening", "response", error=e)
        review = f"No deal in 0 rounds: shop did not respond to opening inquiry ({type(e).__name__})."
        if isinstance(e, (TimeoutError, httpx.TimeoutException)):
            active["failure"] = "network-timeout" if isinstance(e, httpx.TimeoutException) else "timeout"
        update_memory(memory, shop_name, {
            "item": item, "budget": budget, "price": None, "rounds": 0, "stars": 2, "outcome": "no-deal"
        })
        return {"shop": chosen_shop["url"], "receipt": None, "stars": 2, "review": review}

    if "error" in res:
        turn_event(active, "opening", "response", rpc_error=True)
        review = f"No deal in 0 rounds: seller returned RPC error {res['error']}."
        update_memory(memory, shop_name, {
            "item": item, "budget": budget, "price": None, "rounds": 0, "stars": 2, "outcome": "no-deal"
        })
        return {"shop": chosen_shop["url"], "receipt": None, "stars": 2, "review": review}

    task = res.get("task", res)
    task_id = task.get("id")
    task_status = task.get("status", {})
    task_state = task_status.get("state")
    active.update(url=rpc_url, id=task_id, state=task_state)
    min_valid_p = 500 if is_round3 else 50
    max_valid_p = max(budget * 4, list_price * 2, 1000)
    seller_ask = extract_last_number(task, min_valid_p, max_valid_p)
    turn_event(active, "opening", "response", ask_after=seller_ask, state=task_state)
    seller_texts = [
        " ".join(p.get("text", "") for p in task_status.get("message", {}).get("parts", []))
    ]
    card_desc = str(card.get("description", ""))

    last_offer = 0
    rounds = 0
    step_inc = 5  # Legacy only; Round 3 counters do not depend on budget steps.

    offer_limit = price_limit

    failure_reason = ""
    while task_state == "TASK_STATE_INPUT_REQUIRED" and rounds < (3 if is_round3 else 5):
        if is_round3:
            request_timeout(1)
            if seller_ask is None:
                failure_reason = "seller price could not be parsed"
                break
        rounds += 1

        # Budget-Decoupled Negotiation:
        # Anchors offers to `reserve` (estimated supply cost) and `seller_ask`, NEVER leaking `budget * ratio`.
        if is_round3 and rounds == 2:
            # Use the actual current ask, even above the cap; clamp below.
            # Integer half-up rounding, with at least one unit for a positive gap.
            gap = seller_ask - last_offer
            offer = (last_offer + max(1, (gap * 35 + 50) // 100)
                     if gap > 0 else seller_ask)
        elif seller_ask is not None and seller_ask <= offer_limit:
            if seller_ask <= math.ceil(reserve * 1.05) or rounds >= 3:
                offer = seller_ask
            elif rounds == 1:
                offer = min(seller_ask, reserve)
            else:
                halfway = int(round(reserve + (seller_ask - reserve) * 0.55))
                offer = min(seller_ask, max(last_offer + step_inc, halfway))
        else:
            if last_offer >= offer_limit and seller_ask is not None and seller_ask > offer_limit:
                diagnostic(f"Cannot agree: seller ask {seller_ask} > price limit {offer_limit}")
                if not is_round3:
                    try:
                        rpc_call(rpc_url, "CancelTask", {"id": task_id})
                    except Exception:
                        pass
                failure_reason = "seller ask exceeds price limit"
                break

            ceiling = min(offer_limit, seller_ask) if seller_ask else offer_limit
            start_anchor = min(reserve, int(ceiling * 0.72)) if is_round3 else min(reserve, ceiling)
            if rounds == 1:
                offer = start_anchor
            elif rounds == 2:
                offer = max(last_offer + step_inc, int(round(start_anchor + (ceiling - start_anchor) * 0.45)))
            elif rounds == 3:
                offer = ceiling if is_round3 else max(last_offer + step_inc, int(round(start_anchor + (ceiling - start_anchor) * 0.80)))
            else:
                offer = ceiling

            if not is_round3:
                prompt = (
                    f"You are a quality-focused buyer negotiating to purchase a {item}.\n"
                    f"Item cost floor: {reserve}. Maximum budget: {budget} (You MUST NEVER exceed {budget}).\n"
                    f"Seller's current ask: {seller_ask}.\n"
                    f"Your last offer: {last_offer}.\n"
                    f"Round: {rounds}/5.\n"
                    f"If seller refuses to go below {budget} and you have already offered {budget}, reply: QUIT\n"
                    f"Otherwise propose your next offer (must be > {last_offer} and <= {budget}). Reply format: OFFER <number>"
                )
                resp = call_harness(prompt).upper()
                if "QUIT" in resp and last_offer >= budget:
                    try:
                        rpc_call(rpc_url, "CancelTask", {"id": task_id})
                    except Exception:
                        pass
                    break
                nums = re.findall(r"\d+", resp)
                if nums:
                    suggested = int(nums[-1])
                    if suggested > last_offer:
                        offer = min(budget, suggested)

        offer = min(offer_limit, max(1, offer))
        last_offer = offer

        # Send clean numeric offer (avoid extra numbers in the text so simple regex sellers parse `offer` accurately!)
        follow_up_text = (
            str(offer)
            if is_round3
            else f"I offer {offer} if you guarantee product quality and after-sales service support."
        )
        follow_up = {
            "role": "ROLE_USER",
            "messageId": str(uuid.uuid4()),
            "taskId": task_id,
            "parts": [{"text": follow_up_text}],
        }
        ask_before = seller_ask
        branch = "first" if rounds == 1 else "counter35" if rounds == 2 else "third"
        turn_event(active, "offer", "request", rounds, branch, ask_before, offer)
        try:
            res = rpc_call(rpc_url, "SendMessage", {"message": follow_up})
            active["seller_text"] = seller_response_text(res)
        except Exception as exc:
            turn_event(active, "offer", "response", rounds, branch, ask_before, offer, error=exc)
            failure_reason = f"counter RPC failed ({type(exc).__name__})"
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                active["failure"] = "network-timeout" if isinstance(exc, httpx.TimeoutException) else "timeout"
            break
        if "error" in res:
            turn_event(active, "offer", "response", rounds, branch, ask_before, offer, rpc_error=True)
            failure_reason = f"counter RPC error: {res['error']}"
            break

        task = res.get("task", res)
        task_status = task.get("status", {})
        task_state = task_status.get("state")
        task_id = task.get("id") or task_id
        active.update(id=task_id, state=task_state)
        seller_ask = extract_last_number(task, min_valid_p, max_valid_p)
        turn_event(active, "offer", "response", rounds, branch, ask_before, offer, seller_ask, task_state)
        seller_texts.append(
            " ".join(p.get("text", "") for p in task_status.get("message", {}).get("parts", []))
        )

        if task_state in ("TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"):
            break

    # A seller may lower its ask only after our third counter. Allow one final
    # acceptance, never a fourth bargaining counter or a relaxed price cap.
    if (is_round3 and rounds == 3 and not failure_reason
            and task_state == "TASK_STATE_INPUT_REQUIRED"
            and seller_ask is not None and seller_ask <= offer_limit):
        ask_before = seller_ask
        turn_event(active, "offer", "request", 4, "finalaccept", ask_before, ask_before)
        try:
            request_timeout(1)
            last_offer = seller_ask
            rounds += 1
            res = rpc_call(rpc_url, "SendMessage", {"message": {
                "role": "ROLE_USER",
                "messageId": str(uuid.uuid4()),
                "taskId": task_id,
                "parts": [{"text": str(seller_ask)}],
            }})
            active["seller_text"] = seller_response_text(res)
            if "error" in res:
                turn_event(active, "offer", "response", 4, "finalaccept", ask_before, ask_before, rpc_error=True)
                failure_reason = f"final acceptance RPC error: {res['error']}"
            else:
                task = res.get("task", res)
                task_status = task.get("status", {})
                task_state = task_status.get("state")
                task_id = task.get("id") or task_id
                active.update(id=task_id, state=task_state)
                seller_ask = extract_last_number(task, min_valid_p, max_valid_p)
                turn_event(active, "offer", "response", 4, "finalaccept", ask_before, last_offer, seller_ask, task_state)
                seller_texts.append(
                    " ".join(p.get("text", "") for p in task_status.get("message", {}).get("parts", []))
                )
        except Exception as exc:
            turn_event(active, "offer", "response", 4, "finalaccept", ask_before, ask_before, error=exc)
            failure_reason = f"final acceptance RPC failed ({type(exc).__name__})"

    # 3. Inspect result & receipt
    raw_receipt = extract_receipt_data(task) if task_state == "TASK_STATE_COMPLETED" else None
    combined_service_text = (" ".join(seller_texts) + " " + card_desc + " " + json.dumps(raw_receipt or {})).lower()
    has_service = any(kw in combined_service_text for kw in SERVICE_KEYWORDS)

    if raw_receipt is not None:
        rec_price_raw = raw_receipt.get("price")
        rec_price = int(rec_price_raw) if isinstance(rec_price_raw, float) and rec_price_raw.is_integer() else rec_price_raw
        rec_item = str(raw_receipt.get("item", "")).strip()
        rec_task = str(raw_receipt.get("task", ""))
        rec_shop = str(raw_receipt.get("shop", raw_receipt.get("seller", ""))).strip()
        signature = raw_receipt.get("sig_seller")
        rec_sig = signature.strip() if isinstance(signature, str) else ""

        if is_round3:
            item_matches = (rec_item == item) or (rec_item in acceptable_skus) or (rec_item.lower() == item_low)
        else:
            item_matches = not rec_item or (rec_item.lower() == item_low)

        receipt_cap = budget
        food_lifetime_decline = None
        if is_round3_food:
            # Recompute on the receipt SKU, never category/target fallback. This
            # does not pin the kit's later checkout to that SKU: a different
            # acceptable SKU shipped there is a pre-existing integration limit.
            receipt_cap, food_accept = food_price_policy(catalog, hub, rec_item, budget, order_data, raw_receipt)
            food_policy = build_accept_policy(order_data, rec_price, list_price)
            food_policy.update(food_accept)
            food_lifetime_decline = food_lifetime_precheck(raw_receipt, food_policy)
        elif is_round3_goods:
            receipt_cap = goods_price_cap(catalog, hub, rec_item, budget)
        price_policy_declined = (is_round3_food or is_round3_goods) and (
            receipt_cap is None or (positive_number(rec_price) and rec_price > receipt_cap))
        receipt_invalid = (
            not positive_number(rec_price)
            or (isinstance(rec_price, float) and not rec_price.is_integer())
            or rec_price > budget
            # A price-policy rejection is not evidence of dishonesty. Still reject
            # above-agreement receipts within policy, and all structural/budget errors.
            or (last_offer > 0 and rec_price > last_offer and not price_policy_declined)
            or not item_matches
            or (task_id and rec_task and rec_task != str(task_id))
            or not rec_shop
        )

        if is_round3 and not rec_sig:
            final_receipt = None
            recorded_price = None
            stars = None
            review = "No deal: unsigned receipt (missing sig_seller); trying another shop."
            outcome = "unsigned-receipt"
            active["failure"] = "unsigned"
        elif receipt_invalid:
            stars = 1
            review = (
                f"Invalid receipt! Agreed at {last_offer} (budget {budget}) in {rounds} round(s), "
                f"but receipt says price={rec_price}, item={rec_item}, task={rec_task}!"
            )
            final_receipt = None
            outcome = "rip-off"
            recorded_price = rec_price if isinstance(rec_price, (int, float)) else None
        elif price_policy_declined:
            final_receipt = None
            recorded_price = rec_price
            stars = 3
            review = f"No deal: declined-price-policy for {rec_item} at {rec_price} (cap {receipt_cap})."
            outcome = "declined-price-policy"
            active["failure"] = "price"
        elif food_lifetime_decline:
            final_receipt = None
            recorded_price = rec_price
            stars = 3
            review = (f"No deal: declined-price-policy food-lifetime-precheck for {rec_item}; "
                      f"{food_lifetime_decline}. Trying another shop.")
            outcome = "declined-price-policy"
            active["failure"] = "price"  # Existing retry/cooldown schema; not seller dishonesty.
        else:
            final_receipt = raw_receipt
            recorded_price = int(rec_price)
            sig_note = "seller signature present; kit verification pending" if rec_sig else "receipt fields checked"
            is_dumping_price = (not is_round3) and (recorded_price <= reserve + 1)

            if is_dumping_price and not has_service:
                stars = 3
                review = (
                    f"Bought {item} at {recorded_price} (budget {budget}, {sig_note}); very low price with no "
                    f"explicit warranty or after-sales service mentioned."
                )
                outcome = "dumping-suspect-quality"
            elif has_service:
                stars = 5
                review = (
                    f"Verified quality and fast negotiation ({sig_note})! Closed {rec_item or item} at "
                    f"{recorded_price} (budget {budget}) in {rounds} round(s)."
                )
                outcome = "great-service-deal"
            else:
                stars = 4
                review = (
                    f"Bought {rec_item or item} at {recorded_price} (budget {budget}) in {rounds} round(s) ({sig_note}); "
                    f"receipt matched the agreed price."
                )
                outcome = "no-service-deal"

            # Check actual receipt SKU/price/terms on every completion path.
            # Seller claims are only hints; actual certified checkout is pending.
            if is_round3_goods:
                w_h = raw_receipt.get("warranty_hours")
                saved = goods_expected_saved(recorded_price, catalog[rec_item]["list_price"],
                                             raw_receipt.get("claim"), w_h)
                mature = len(settled_price_samples(hub, rec_item)) >= MATURE_SETTLE_MIN_SAMPLES
                min_saved = 0.0 if not mature else MATURE_UNCOVERED_MIN_SAVED
                if saved is None or saved <= min_saved:
                    final_receipt = None
                    stars = 3
                    review = f"No deal: declined-price-policy for {rec_item}; invalid terms or nonpositive expected Saved."
                    outcome = "declined-no-warranty"
                    active["failure"] = "price"

    else:
        final_receipt = None
        recorded_price = None
        outcome = "no-deal"
        stars = 3 if (seller_ask and seller_ask <= int(budget * 1.15) and has_service) else 2
        template = random.choice(FUNNY_NO_DEAL_REVIEWS)
        review = template.format(rounds=rounds, ask=seller_ask or "high", budget=budget)
        refusal = (classify_seller_refusal(active.get("seller_text", ""))
                   if is_round3 and task_state == "TASK_STATE_REJECTED" else None)
        if refusal == "stockout":
            # Stale shops.json snapshot: say so instead of a "seller clung to high" template.
            active["failure"] = "stockout"
            review = f"No deal: {shop_name} had no stock of {sku_code or item} (seller said out of stock)."
        elif refusal == "unit_mismatch":
            active["failure"] = "unit_mismatch"
            review = (f"No deal: {shop_name} had no unit matching the order's expiry/grade/warranty "
                      f"for {sku_code or item}.")
        elif is_round3:
            if not active.get("failure") and seller_ask is not None and seller_ask > offer_limit:
                active["failure"] = "price"
            elif not active.get("failure") and task_state == "TASK_STATE_REJECTED":
                # A rejected SKU is not proof of globally empty stock.
                active["failure"] = "rejected"
            if not failure_reason and task_state == "TASK_STATE_INPUT_REQUIRED":
                failure_reason = f"counter limit reached (ask {seller_ask}, offer limit {offer_limit}, budget limit {budget})"
            review += f" Reason: {failure_reason or task_state or 'missing receipt'}."

    # A Round 3 agreed deal enters shop memory from review.py, only after it is paid
    if not (is_round3 and final_receipt is not None):
        update_memory(memory, shop_name, {
            "item": item,
            "budget": budget,
            "price": recorded_price,
            "rounds": rounds,
            "stars": stars,
            "outcome": outcome,
        })

    result_line: dict[str, Any] = {
        "shop": chosen_shop["url"],
        "receipt": final_receipt,
        "stars": stars,
        "review": review,
    }
    if is_round3 and final_receipt is not None and recorded_price is not None:
        accept_list_price = int(catalog[rec_item]["list_price"]) if is_round3_goods else list_price
        accept_warranty = final_receipt.get("warranty_hours") or 0
        if not is_round3_goods:
            accept_warranty = float(accept_warranty)  # preserve the existing food/legacy path
        if is_round3_food:
            result_line["accept"] = food_policy  # Exactly the gate used by the hint precheck.
        else:
            result_line["accept"] = build_accept_policy(order_data, recorded_price, accept_list_price,
                                                        accept_warranty, final_receipt.get("claim"))
            result_line["accept"].update(food_accept)
        # This is only a pending checkout, not a paid purchase or successful memory.
        # Build/validate everything before writing a single final candidate.
        request_timeout(1)
        matched_sku = rec_item if rec_item in catalog else target_sku_info["sku"]
        sku_meta = catalog.get(matched_sku) or {}
        now_ts = int(time.time())
        with mutate_hub() as hub:
            request_timeout(1)  # Lock acquisition can itself take several seconds.
            pending = hub.setdefault("pending_deals", {})
            pending[str(order_data.get("order"))] = {
                "order": order_data.get("order"), "sku": matched_sku,
                "name": sku_meta.get("name") or target_sku_info["name"],
                "category": sku_meta.get("category") or order_data.get("category") or item,
                "shop_url": shop_url, "shop_name": shop_name, "price": recorded_price,
                "list_price": int(sku_meta.get("list_price") or list_price),
                "warranty_hours": raw_receipt.get("warranty_hours") or 0, "ts": now_ts,
                "memory": {"item": item, "budget": budget, "price": recorded_price, "rounds": rounds},
            }
            active["pending"] = pending[str(order_data.get("order"))].copy()
            for k in [k for k, v in pending.items() if now_ts - int(v.get("ts") or 0) > 21600]:
                del pending[k]

    return result_line


if __name__ == "__main__":
    main()
