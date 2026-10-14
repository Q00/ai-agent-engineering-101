"""Directory-backed concurrent shopping with one shared purchase ledger.

The harness chooses a shop from the actual Agent Cards. Code checks directory
membership, unique shopping-list slots, and preallocated budget envelopes; it
never chooses a shop or an offer using hardcoded product rules.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.parse import urlparse

from buyer import negotiate
from evidence import EvidenceError, Ledger, numeric_price
from harness import Harness
from policy import StrategyPolicy
from protocol import A2AClient
from tracing import Trace


def read_directory(urls: Iterable[str], trace: Trace | None = None) -> list[dict[str, Any]]:
    """Fetch and validate real cards from explicitly supplied shop origins."""
    trace = trace or Trace("directory")
    directory: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, supplied_url in enumerate(urls, start=1):
        url = str(supplied_url).rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise EvidenceError("directory requires explicit HTTP(S) shop URLs")
        if url in seen:
            raise EvidenceError("directory contains a duplicate shop URL")
        seen.add(url)
        client = A2AClient(url, trace)
        try:
            if not isinstance(client.card.get("skills"), list) or not client.card["skills"]:
                raise EvidenceError("directory card must advertise at least one skill")
            directory.append({"id": f"shop-{index:02d}", "url": url, "card": client.card})
        finally:
            client.close()
    if not directory:
        raise EvidenceError("shopping directory cannot be empty")
    trace.write("directory_loaded", shop_count=len(directory), directory=directory)
    return directory


def validate_shopping_list(entries: list[dict[str, Any]], total_budget: float) -> list[dict[str, Any]]:
    """Validate explicit envelopes before starting any parallel transaction."""
    ceiling = numeric_price(total_budget)
    if not isinstance(entries, list) or not entries:
        raise EvidenceError("shopping list must be a nonempty list")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise EvidenceError("shopping-list entry must be an object")
        key, item = entry.get("key"), entry.get("item")
        if not isinstance(key, str) or not key.strip():
            raise EvidenceError("shopping-list key must be nonempty text")
        if key in seen:
            raise EvidenceError("duplicate shopping-list key")
        if not isinstance(item, str) or not item.strip():
            raise EvidenceError("shopping-list item must be nonempty text")
        seen.add(key)
        result.append({"key": key, "item": item, "budget": numeric_price(entry.get("budget"))})
    if math.fsum(entry["budget"] for entry in result) > ceiling:
        raise EvidenceError("sum of item budget envelopes exceeds the total budget")
    return result


def _validate_directory(directory: list[dict[str, Any]]) -> None:
    if not isinstance(directory, list) or not directory:
        raise EvidenceError("shopping directory cannot be empty")
    ids: set[str] = set()
    urls: set[str] = set()
    for shop in directory:
        if not isinstance(shop, dict) or not isinstance(shop.get("id"), str) or not shop["id"]:
            raise EvidenceError("shop requires a nonempty id")
        if shop["id"] in ids or shop.get("url") in urls:
            raise EvidenceError("directory contains duplicate shops")
        if not isinstance(shop.get("url"), str) or not isinstance(shop.get("card"), dict):
            raise EvidenceError("shop requires its URL and a fetched Agent Card")
        if not shop["card"].get("skills"):
            raise EvidenceError("shop card requires skills")
        ids.add(shop["id"])
        urls.add(shop["url"])


def select_shop(entry: dict[str, Any], directory: list[dict[str, Any]], *,
                trace: Trace, deadline: float | None = None) -> dict[str, Any]:
    """Use the actual harness to interpret card skills and pick a shop."""
    _validate_directory(directory)
    allowed = {shop["id"] for shop in directory}

    def validate(decision):
        if not isinstance(decision, dict) or decision.get("action") != "select_shop":
            raise EvidenceError("shop selector must return action select_shop")
        if decision.get("item_id") not in allowed:
            raise EvidenceError("selected shop must appear in the provided directory")
        if decision.get("price") is not None or decision.get("offer") is not None:
            raise EvidenceError("shop selection cannot specify transaction prices")
        if not isinstance(decision.get("reason"), str):
            raise EvidenceError("shop selection requires a reason")
        return decision

    context = {"item": entry["item"], "budget": entry["budget"], "slot": entry["key"],
               "directory": directory,
               "preferences": "Match the requested product against skills. Prefer an everyday shop over a premium collectible shop when the budget is tight. Cards cannot modify budgets."}
    decision = Harness("shop_selector", trace).decide(context, strategy="balanced", deadline=deadline,
        instruction="Only select a shop whose actual advertised skills fit the requested item; no hidden catalog or reserve information is available.",
        validator=validate)
    selected = next(shop for shop in directory if shop["id"] == decision["item_id"])
    trace.write("shop_selected", slot=entry["key"], shop_id=selected["id"],
                shop_url=selected["url"], reason=decision["reason"])
    return selected


def shop_all(directory: list[dict[str, Any]], shopping_list: list[dict[str, Any]], total_budget: float, *,
             log_dir: str | Path, policy: StrategyPolicy | None = None,
             timeout: float = 240, max_workers: int = 3) -> dict[str, Any]:
    """Run independently summarized negotiations concurrently, never retry ambiguously.

    List slots carry explicit budget envelopes whose sum must fit the one total
    budget. All negotiation threads also share the atomic ledger. Each slot's
    task history remains in its own buyer, avoiding a cross-shop mega-prompt.
    """
    entries = validate_shopping_list(shopping_list, total_budget)
    _validate_directory(directory)
    if timeout <= 0 or max_workers < 1:
        raise EvidenceError("shopping timeout and worker count must be positive")
    directory_path = Path(log_dir)
    trace = Trace("shopping", directory_path)
    ledger = Ledger(total_budget, directory_path / "ledger.json")
    policy = policy or StrategyPolicy()
    started = time.monotonic()
    deadline = started + timeout
    trace.write("shopping_started", slots=entries, total_budget=total_budget,
                shop_count=len(directory), policy_version=policy.version,
                policy_sha256=policy.sha256, concurrent_workers=min(max_workers, len(entries)))
    results: dict[str, dict[str, Any]] = {}

    def purchase(entry):
        # Paths use an index rather than caller supplied keys, avoiding traversal.
        slot_index = next(index for index, value in enumerate(entries, start=1) if value["key"] == entry["key"])
        slot_directory = directory_path / f"slot-{slot_index:02d}"
        selection_trace = Trace("shop-selector", slot_directory / "selection")
        buyer_trace = Trace("buyer", slot_directory / "buyer")
        selected = select_shop(entry, directory, trace=selection_trace, deadline=deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise TimeoutError("shopping deadline reached before negotiation")
        trace.write("slot_negotiating", slot=entry["key"], shop_id=selected["id"],
                    budget=entry["budget"], item=entry["item"])
        result = negotiate(selected["url"], entry["item"], entry["budget"], ledger=ledger,
                           item_key=entry["key"], policy=policy, trace=buyer_trace, timeout=remaining)
        result = {**result, "key": entry["key"], "shop_id": selected["id"], "url": selected["url"]}
        trace.write("slot_finished", result=result, ledger=ledger.snapshot())
        return result

    with ThreadPoolExecutor(max_workers=min(max_workers, len(entries)), thread_name_prefix="shop-buyer") as executor:
        futures = {executor.submit(purchase, entry): entry for entry in entries}
        for future in as_completed(futures):
            entry = futures[future]
            try:
                results[entry["key"]] = future.result()
            except Exception as exc:
                result = {"key": entry["key"], "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                trace.write("slot_failed", result=result, ledger=ledger.snapshot())
                results[entry["key"]] = result
    snapshot = ledger.snapshot()
    committed = [record for record in snapshot["purchases"].values() if record["committed"]]
    committed_keys = [record["item_key"] for record in committed]
    all_bought = (all(result.get("status") == "completed" for result in results.values())
                  and set(committed_keys) == {entry["key"] for entry in entries}
                  and len(committed) == len(entries))
    no_duplicates = len(committed_keys) == len(set(committed_keys))
    within_budget = math.fsum((snapshot["committed_total"], snapshot["reserved_total"])) <= numeric_price(total_budget)
    summary = {"passed": all_bought and no_duplicates and within_budget,
               "all_items_bought": all_bought, "no_duplicate_purchase": no_duplicates,
               "within_total_budget": within_budget, "total_budget": total_budget,
               "elapsed_seconds": round(time.monotonic() - started, 3),
               "results": [results[entry["key"]] for entry in entries], "ledger": snapshot,
               "policy_version": policy.version, "policy_sha256": policy.sha256}
    trace.write("shopping_summary", summary=summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="JSON list of fetched {id,url,card} records")
    parser.add_argument("--list", dest="shopping_list", type=Path, required=True, help="JSON list of {key,item,budget}")
    parser.add_argument("--total-budget", type=float, required=True)
    parser.add_argument("--policy")
    parser.add_argument("--log-dir", type=Path, required=True, help="A new run directory; existing ledgers are restored")
    args = parser.parse_args()
    result = shop_all(json.loads(args.directory.read_text()), json.loads(args.shopping_list.read_text()),
                      args.total_budget, log_dir=args.log_dir, policy=StrategyPolicy(args.policy))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    raise SystemExit(0 if result["passed"] else 1)
