"""Round 3 adapter for the validated B-original Buyer policy."""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from buyer import BOriginalClient, BuyerError, BuyerOutcome, Quote


class MarketBuyerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Shop:
    name: str
    url: str
    reviews: tuple[dict, ...]
    offers: tuple[dict, ...] = ()


@dataclass(frozen=True)
class ShopChoice:
    shop: Shop
    score: float
    reason: str


@dataclass(frozen=True)
class SavedState:
    """Leaderboard state needed for Patch 3.5 marginal-value calculations."""

    rate: float
    deals: int


@dataclass(frozen=True)
class SettlementHistory:
    settled: int = 0
    rejected: int = 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run B-original against current stocked shops")
    parser.add_argument("--shops", type=Path, required=True)
    parser.add_argument("--order", type=Path, required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    args = parser.parse_args(argv)
    if args.budget <= 0:
        parser.error("--budget must be a positive integer")
    return args


def load_json(path: Path, expected: type) -> object:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MarketBuyerError(f"{path.name} is unavailable or invalid") from exc
    if not isinstance(value, expected):
        raise MarketBuyerError(f"{path.name} has the wrong JSON shape")
    return value


def load_shops(path: Path) -> tuple[Shop, ...]:
    raw = load_json(path, list)
    shops = []
    names = set()
    for row in raw:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise MarketBuyerError("shop list contains an invalid entry")
        name = row["name"].strip()
        if not name or name in names:
            raise MarketBuyerError("shop names must be non-empty and unique")
        reviews = row.get("reviews", [])
        offers = row.get("offers", [])
        if not isinstance(reviews, list) or any(not isinstance(review, dict) for review in reviews):
            raise MarketBuyerError("shop reviews must be JSON objects")
        if not isinstance(offers, list) or any(not isinstance(offer, dict) for offer in offers):
            raise MarketBuyerError("shop offers must be JSON objects")
        shops.append(Shop(name, str(row.get("url", "")).rstrip("/"), tuple(reviews), tuple(offers)))
        names.add(name)
    if not shops:
        raise MarketBuyerError("shop list is empty")
    return tuple(shops)


def state_dir_for(shops_path: Path) -> Path:
    override = os.environ.get("AX_STATE_DIR")
    if override:
        return Path(override)
    if shops_path.name.startswith("test-shops-"):
        return Path(".market/test-state")
    return Path(".market/runtime-state")


def load_memory(path: Path) -> dict:
    if not path.exists():
        return {"version": 2, "shops": {}, "orders": {}}
    value = load_json(path, dict)
    if not isinstance(value.get("shops"), dict) or not isinstance(value.get("orders", {}), dict):
        raise MarketBuyerError("buyer memory has an invalid format")
    value.setdefault("version", 2)
    value.setdefault("orders", {})
    return value


def save_memory(path: Path, memory: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(memory, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def reconcile_payments(memory: dict, payments_path: Path) -> None:
    """Import kit outcomes without turning an ambiguous payment into failure."""
    if not payments_path.exists():
        return
    try:
        lines = payments_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    orders = memory.setdefault("orders", {})
    for line in lines:
        try:
            payment = json.loads(line)
        except ValueError:
            continue
        order_id = payment.get("order") if isinstance(payment, dict) else None
        if not isinstance(order_id, str):
            continue
        row = orders.setdefault(order_id, {})
        row["payment_recorded"] = True
        if payment.get("paid") and isinstance(payment.get("ledger"), int):
            row.update(status="settled", ledger=payment["ledger"])
            continue
        reason = str(payment.get("reason", "unknown"))
        uncertain = any(
            marker in reason.casefold()
            for marker in ("timeout", "unknown", "payment failed", "json", "keyerror", "readerror")
        )
        row.update(status="needs_confirmation" if uncertain else "payment_rejected", reason=reason)


def batch_spend_cap() -> int | None:
    """Return the optional hard cap shared by every Buyer process in one kit run."""
    raw = os.environ.get("BUYER_BATCH_SPEND_CAP")
    if raw is None:
        return None
    try:
        cap = int(raw)
    except ValueError as exc:
        raise MarketBuyerError("BUYER_BATCH_SPEND_CAP is not an integer") from exc
    if cap <= 0:
        raise MarketBuyerError("BUYER_BATCH_SPEND_CAP must be positive")
    return cap


def per_order_spend_cap() -> int | None:
    """Return an optional conservative cap inside the order's AP2 budget."""
    raw = os.environ.get("BUYER_PER_ORDER_CAP")
    if raw is None:
        return None
    try:
        cap = int(raw)
    except ValueError as exc:
        raise MarketBuyerError("BUYER_PER_ORDER_CAP is not an integer") from exc
    if cap <= 0:
        raise MarketBuyerError("BUYER_PER_ORDER_CAP must be positive")
    return cap


def paid_total(payments_path: Path) -> int:
    """Fail closed while summing every recorded paid amount, settled or uncertain."""
    if not payments_path.exists():
        return 0
    try:
        lines = payments_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MarketBuyerError("payments.jsonl cannot be read for the batch cap") from exc
    total = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            payment = json.loads(line)
        except ValueError as exc:
            raise MarketBuyerError("payments.jsonl has an invalid line; batch cap fails closed") from exc
        if not isinstance(payment, dict):
            raise MarketBuyerError("payments.jsonl has a non-object line; batch cap fails closed")
        if payment.get("paid") is not True:
            continue
        price = payment.get("price")
        if type(price) is not int or price < 0:
            raise MarketBuyerError("a paid record has an invalid price; batch cap fails closed")
        total += price
    return total


def saved_after(state: SavedState, contribution: float) -> float:
    """Return Saved after one settled deal with the given surplus/list share."""
    if not 0 <= state.rate <= 1 or state.deals < 0:
        raise ValueError("invalid Saved state")
    if not -1 <= contribution <= 1:
        raise ValueError("Saved contribution must be between -1 and 1")
    numerator = state.rate * (state.deals + 100)
    return (numerator + contribution) / (state.deals + 101)


def saved_delta(state: SavedState, contribution: float) -> float:
    """Patch 3.5 Saved change caused by one additional settled deal."""
    return saved_after(state, contribution) - state.rate


def current_saved_state(payments_path: Path) -> SavedState:
    """Load an optional fresh board snapshot, otherwise use a neutral baseline.

    payments.jsonl proves the settled count but cannot prove later breakage/refunds,
    so it must not be used to reconstruct Saved. An operator may provide the latest
    official board values through BUYER_SAVED_RATE and BUYER_SETTLED_DEALS.
    """
    settled = 0
    if payments_path.exists():
        try:
            lines = payments_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise MarketBuyerError("payments.jsonl cannot be read for Saved state") from exc
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise MarketBuyerError("payments.jsonl has an invalid line; Saved state fails closed") from exc
            if isinstance(row, dict) and row.get("paid") is True and isinstance(row.get("ledger"), int):
                settled += 1

    raw_rate = os.environ.get("BUYER_SAVED_RATE")
    raw_deals = os.environ.get("BUYER_SETTLED_DEALS")
    if raw_rate is None and raw_deals is None:
        # The baseline changes every marginal value by the same amount. Keeping it
        # neutral preserves discount-rate ordering without inventing break outcomes.
        return SavedState(0.0, settled)
    if raw_rate is None or raw_deals is None:
        raise MarketBuyerError("BUYER_SAVED_RATE and BUYER_SETTLED_DEALS must be set together")
    try:
        rate, deals = float(raw_rate), int(raw_deals)
    except ValueError as exc:
        raise MarketBuyerError("official Saved snapshot is not numeric") from exc
    if not 0 <= rate <= 1 or deals < 0:
        raise MarketBuyerError("official Saved snapshot is out of range")
    return SavedState(rate, deals)


def settlement_history(payments_path: Path) -> dict[str, SettlementHistory]:
    """Count observed settlement outcomes by seller URL; ignore ambiguous records."""
    counts: dict[str, list[int]] = {}
    if not payments_path.exists():
        return {}
    try:
        lines = payments_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("shop"), str):
            continue
        paid = row.get("paid")
        if paid not in (True, False):
            continue
        bucket = counts.setdefault(row["shop"].rstrip("/"), [0, 0])
        bucket[0 if paid is True else 1] += 1
    return {url: SettlementHistory(*values) for url, values in counts.items()}


def settlement_probability(history: SettlementHistory) -> float:
    """Smoothed estimate from observed outcomes, not a claim about break risk."""
    return (history.settled + 1) / (history.settled + history.rejected + 2)


def stockout_pairs(payments_path: Path) -> set[tuple[str, str]]:
    """Remember definitive checkout stockouts without treating ambiguity as failure."""
    blocked: set[tuple[str, str]] = set()
    if not payments_path.exists():
        return blocked
    try:
        lines = payments_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return blocked
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("paid") is not False:
            continue
        reason = str(row.get("reason", "")).casefold()
        shop, item = row.get("shop"), row.get("item")
        if ("not in stock" in reason or "checkout refused: 409" in reason) and isinstance(shop, str) and isinstance(item, str):
            blocked.add((shop.rstrip("/"), item))
    return blocked


def _review_summary(reviews: tuple[dict, ...]) -> tuple[float, int, str]:
    stars = [review.get("stars") for review in reviews]
    valid_stars = [value for value in stars if isinstance(value, int) and 1 <= value <= 5]
    average = sum(valid_stars) / len(valid_stars) if valid_stars else 3.0
    evidence_terms = ("overcharg", "receipt", "did not match", "higher than", "avoid")
    suspicious = sum(
        any(term in str(review.get("review", "")).casefold() for term in evidence_terms)
        and isinstance(review.get("stars"), int)
        and review["stars"] <= 2
        for review in reviews
    )
    summary = f"{len(valid_stars)} review(s), average {average:.2f}, integrity warnings {suspicious}"
    return average, suspicious, summary


def rank_verified_quotes(
    rows: list[tuple[Shop, BOriginalClient, Quote]],
    *,
    saved: SavedState = SavedState(0.0, 0),
    history: dict[str, SettlementHistory] | None = None,
) -> list[tuple[Shop, BOriginalClient, Quote]]:
    """Rank by risk-adjusted Patch 3.5 Saved gain, never absolute surplus."""
    history = history or {}

    def key(row: tuple[Shop, BOriginalClient, Quote]) -> tuple:
        shop, _, quote = row
        average, suspicious, _ = _review_summary(shop.reviews)
        discount_rate = (quote.list_price - quote.ask) / quote.list_price
        probability = settlement_probability(history.get(shop.url, SettlementHistory()))
        expected_gain = probability * saved_delta(saved, discount_rate)
        gain_per_credit = expected_gain / quote.ask
        return (
            expected_gain <= 0,
            -expected_gain,
            suspicious > 0,
            -gain_per_credit,
            -average,
            quote.ask,
            shop.name,
            quote.item,
        )

    return sorted(rows, key=key)


def choose_shop(shops: tuple[Shop, ...], memory: dict) -> ShopChoice:
    """Legacy deterministic fallback retained for non-Round-3 local tests."""
    rows = []
    remembered = memory.get("shops", {})
    for index, shop in enumerate(shops):
        average, suspicious, review_summary = _review_summary(shop.reviews)
        history = remembered.get(shop.name, {}) if isinstance(remembered, dict) else {}
        agreements = int(history.get("agreements", history.get("deals", 0))) if isinstance(history, dict) else 0
        failures = int(history.get("failures", history.get("no_deals", 0))) if isinstance(history, dict) else 0
        mismatches = int(history.get("receipt_mismatches", 0)) if isinstance(history, dict) else 0
        score = average * 10 + min(agreements, 5) * 2 - min(failures, 5) - suspicious * 12 - mismatches * 25
        reason = (
            f"reviews: {review_summary}; memory: agreements {agreements}, failures {failures}, "
            f"receipt mismatches {mismatches}; score {score:.2f}"
        )
        rows.append((score, -index, shop.name, shop, reason))
    score, _, _, shop, reason = max(rows)
    return ShopChoice(shop, score, reason)


def _compatibility_run(
    shops_path: Path,
    item: str,
    budget: int,
    buyer_factory: Callable[[str], object],
) -> dict:
    """Keep the historical unit-test seam without using it in production."""
    shops = load_shops(shops_path)
    memory_path = state_dir_for(shops_path) / "buyer-memory.json"
    memory = load_memory(memory_path)
    choice = choose_shop(shops, memory)
    try:
        with buyer_factory(choice.shop.url) as client:
            outcome = client.buy(item, budget)
    except (BuyerError, httpx.HTTPError):
        outcome = BuyerOutcome("failed")
    row = memory.setdefault("shops", {}).setdefault(choice.shop.name, {"agreements": 0, "failures": 0})
    if outcome.outcome == "deal":
        row["agreements"] = int(row.get("agreements", 0)) + 1
    else:
        row["failures"] = int(row.get("failures", 0)) + 1
    save_memory(memory_path, memory)
    return {
        "shop": choice.shop.url,
        "receipt": outcome.receipt,
        "stars": 4 if outcome.receipt else 2,
        "review": "Receipt matched the agreed price." if outcome.receipt else "No signed agreement.",
    }


def _candidate_offer(
    shop: Shop,
    acceptable: set[str],
    catalog: dict,
    blocked: set[tuple[str, str]] | None = None,
) -> tuple[str, int, str] | None:
    available = []
    blocked = blocked or set()
    for offer in shop.offers:
        sku = offer.get("sku")
        product = catalog.get(sku) if isinstance(sku, str) else None
        price = product.get("list_price") if isinstance(product, dict) else None
        qty = offer.get("qty")
        if sku in acceptable and (shop.url, sku) not in blocked and type(qty) is int and qty > 0 and type(price) is int and price > 0:
            available.append((sku, price, str(offer.get("name") or product.get("name") or sku)))
    return min(available, key=lambda row: (row[1], row[0]), default=None)


def _get_quote(shop: Shop, offer: tuple[str, int, str]) -> tuple[Shop, BOriginalClient, Quote]:
    sku, list_price, name = offer
    client = BOriginalClient(shop.url)
    try:
        quote = client.quote(f"{name} ({sku})", list_price)
        # Receipt validation is bound to the SKU, not to the natural-language request.
        quote = Quote(quote.shop_url, sku, quote.list_price, quote.ask, quote.task)
        return shop, client, quote
    except Exception:
        client.close()
        raise


def _accept_policy(order: dict) -> dict:
    accept: dict[str, object] = {"cert": True, "grades": ["A", "B"]}
    if order.get("kind") == "food":
        needed = order.get("use_within_hours")
        if isinstance(needed, (int, float)) and needed >= 0:
            accept["min_hours_left"] = needed
    elif order.get("kind") == "goods":
        # The official break window is six hours. Requiring six hours avoids the
        # -1 Saved contribution of an unrefunded break without guessing its odds.
        accept["warranty_hours"] = 6
    return accept


def _receipt_precheck(
    receipt: dict,
    acceptable: set[str],
    budget: int,
    order: dict | None = None,
) -> str | None:
    """Reject facts already visible in the signed receipt; ACP remains authoritative."""
    item, price, claim = receipt.get("item"), receipt.get("price"), receipt.get("claim")
    if item not in acceptable:
        return "signed receipt item is not acceptable"
    if type(price) is not int or not 0 < price <= budget:
        return "signed receipt price is outside the effective budget"
    if isinstance(claim, str) and claim not in {"A", "B"}:
        return f"signed receipt claims disallowed grade {claim}"
    if order and order.get("kind") == "goods":
        warranty = receipt.get("warranty_hours", 0)
        if not isinstance(warranty, (int, float)) or isinstance(warranty, bool) or not 0 <= warranty <= 72:
            return "signed receipt has invalid warranty hours"
        if warranty < 6:
            return "signed receipt does not cover the six-hour goods break window"
    return None


def run(
    shops_path: Path,
    item: str,
    budget: int,
    buyer_factory: Callable[[str], object] | None = None,
    order_path: Path | None = None,
) -> dict:
    if buyer_factory is not None:
        return _compatibility_run(shops_path, item, budget, buyer_factory)
    if order_path is None:
        raise MarketBuyerError("Round 3 requires --order")

    shops = load_shops(shops_path)
    order = load_json(order_path, dict)
    catalog_path = shops_path.parent / "catalog.json"
    catalog = load_json(catalog_path, dict)
    memory_path = state_dir_for(shops_path) / "buyer-memory.json"
    memory = load_memory(memory_path)
    payments_path = shops_path.parent / "payments.jsonl"
    reconcile_payments(memory, payments_path)
    saved = current_saved_state(payments_path)
    history = settlement_history(payments_path)
    cap = batch_spend_cap()
    spent_before = paid_total(payments_path) if cap is not None else 0
    remaining = cap - spent_before if cap is not None else budget
    if remaining <= 0:
        raise MarketBuyerError(f"batch spend cap exhausted ({spent_before}/{cap})")
    order_cap = per_order_spend_cap()
    trade_budget = min(budget, remaining, order_cap if order_cap is not None else budget)
    order_id = order.get("order")
    if not isinstance(order_id, str) or not order_id:
        raise MarketBuyerError("order has no id")

    prior = memory.setdefault("orders", {}).get(order_id)
    if isinstance(prior, dict):
        # Never re-run negotiation/payment for an order whose earlier result may have arrived.
        save_memory(memory_path, memory)
        return {
            "shop": prior.get("shop", shops[0].url),
            "receipt": None,
            "stars": 2,
            "review": f"Duplicate order suppressed; state={prior.get('status', 'unknown')}.",
            "accept": _accept_policy(order),
        }

    acceptable = set(order.get("acceptable") or [])
    blocked = stockout_pairs(payments_path)
    own_name = os.environ.get("OWN_AGENT_NAME", "sohyunkim11").casefold()
    candidates = [
        (shop, offer)
        for shop in shops
        if shop.name.casefold() != own_name
        if (offer := _candidate_offer(shop, acceptable, catalog, blocked)) is not None
    ]
    if not candidates:
        raise MarketBuyerError("no external shop has acceptable stock")

    quoted: list[tuple[Shop, BOriginalClient, Quote]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        futures = {pool.submit(_get_quote, shop, offer): shop for shop, offer in candidates}
        for future in as_completed(futures):
            try:
                result = future.result()
            except (BuyerError, httpx.HTTPError, ValueError):
                continue
            if result[2].ask <= trade_budget and result[2].ask < result[2].list_price:
                quoted.append(result)
            else:
                result[1].cancel(result[2].task.get("id"))
                result[1].close()
    if not quoted:
        raise MarketBuyerError("no safe quote within budget")

    # Patch 3.5 scores rate saved, not absolute credits saved. The score also uses
    # only observed settlement outcomes; unpublished goods-break odds are not guessed.
    quoted = rank_verified_quotes(quoted, saved=saved, history=history)
    selected_shop = selected_quote = receipt = None
    attempts: list[dict] = []
    selected_index = -1
    for index, (shop, client, quote) in enumerate(quoted):
        try:
            candidate_receipt = client.complete(quote, trade_budget)
        except (BuyerError, httpx.HTTPError) as exc:
            attempts.append({"shop": shop.url, "item": quote.item, "reason": type(exc).__name__})
            client.close()
            continue
        client.close()
        reason = _receipt_precheck(candidate_receipt, acceptable, trade_budget, order)
        if reason is not None:
            attempts.append({"shop": shop.url, "item": quote.item, "reason": reason})
            shop_memory = memory.setdefault("shops", {}).setdefault(shop.name, {"agreements": 0, "failures": 0})
            shop_memory["failures"] = int(shop_memory.get("failures", 0)) + 1
            continue
        selected_shop, selected_quote, receipt, selected_index = shop, quote, candidate_receipt, index
        break
    for _, client, quote in quoted[selected_index + 1 if selected_index >= 0 else len(quoted):]:
        client.cancel(quote.task.get("id"))
        client.close()

    if receipt is None or selected_shop is None or selected_quote is None:
        memory["orders"][order_id] = {
            "shop": attempts[-1]["shop"] if attempts else candidates[0][0].url,
            "status": "negotiation_failed",
            "reason": "no receipt passed pre-payment checks",
            "attempts": attempts,
        }
        save_memory(memory_path, memory)
        return {
            "shop": attempts[-1]["shop"] if attempts else candidates[0][0].url,
            "receipt": None,
            "stars": 2,
            "review": "No signed agreement passed the pre-payment safety checks.",
            "accept": _accept_policy(order),
        }

    price = receipt.get("price")
    if type(price) is not int or price <= 0:
        raise MarketBuyerError("signed receipt has an invalid price")
    if cap is not None:
        latest_spent = paid_total(payments_path)
        if latest_spent + price > cap:
            memory["orders"][order_id] = {
                "shop": selected_shop.url,
                "item": receipt.get("item"),
                "price": price,
                "task": receipt.get("task"),
                "status": "batch_cap_blocked_before_payment",
                "reason": f"{latest_spent}+{price}>{cap}",
            }
            save_memory(memory_path, memory)
            return {
                "shop": selected_shop.url,
                "receipt": None,
                "stars": 2,
                "review": "Signed agreement was not paid because the batch spend cap would be exceeded.",
                "accept": _accept_policy(order),
                "batch_spend": {"before": latest_spent, "proposed": price, "cap": cap},
            }

    memory["orders"][order_id] = {
        "shop": selected_shop.url,
        "item": receipt.get("item"),
        "price": receipt.get("price"),
        "task": receipt.get("task"),
        "status": "a2a_agreed_pending_settlement",
    }
    shop_memory = memory.setdefault("shops", {}).setdefault(
        selected_shop.name, {"agreements": 0, "failures": 0}
    )
    shop_memory["agreements"] = int(shop_memory.get("agreements", 0)) + 1
    save_memory(memory_path, memory)
    return {
        "shop": selected_shop.url,
        "receipt": receipt,
        "stars": 4,
        "review": "The signed receipt matched the agreed price; settlement is verified by the market kit.",
        "accept": _accept_policy(order),
        "saved_selection": {
            "baseline": saved.rate,
            "deals": saved.deals,
            "discount_rate": (selected_quote.list_price - price) / selected_quote.list_price,
            "settlement_probability": settlement_probability(
                history.get(selected_shop.url, SettlementHistory())
            ),
            "saved_delta_if_served": saved_delta(
                saved, (selected_quote.list_price - price) / selected_quote.list_price
            ),
            "warranty_hours": receipt.get("warranty_hours", 0),
        },
        **(
            {"batch_spend": {"before": spent_before, "after_if_paid": spent_before + price, "cap": cap}}
            if cap is not None else {}
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args.shops, args.item, args.budget, order_path=args.order)
    except (MarketBuyerError, BuyerError, httpx.HTTPError) as exc:
        print(f"market buyer failed safely: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
