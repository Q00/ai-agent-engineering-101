"""Use Agent Cards as a directory, then shop concurrently under one total budget."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

import httpx

from catalog_search import tokens
from codex_harness import decide


ROOT = Path(__file__).resolve().parent
SELECTION_SCHEMA = ROOT / "selection.schema.json"


def read_card(entry: dict) -> dict:
    base = entry["url"].rstrip("/")
    response = httpx.get(base + "/.well-known/agent-card.json", timeout=20)
    response.raise_for_status()
    return {"id": entry["id"], "url": base, "card": response.json()}


def card_text(shop: dict) -> str:
    card = shop["card"]
    return " ".join([
        str(card.get("name", "")), str(card.get("description", "")),
        json.dumps(card.get("skills", []), ensure_ascii=False),
    ])


def shortlist(shops: list[dict], item: str, limit: int = 5) -> list[dict]:
    wanted = tokens(item)
    ranked = sorted(
        shops,
        key=lambda shop: (-len(wanted & tokens(card_text(shop))), shop["id"]),
    )
    return ranked[:limit]


def choose_shop(shops: list[dict], item: str) -> dict:
    candidates = shortlist(shops, item)
    compact = [{
        "item_id": shop["id"], "name": shop["card"].get("name"),
        "description": shop["card"].get("description"),
        "skills": shop["card"].get("skills"),
    } for shop in candidates]
    selection = decide(
        f"""You route one shopping item to an A2A shop using its Agent Card.
Wanted item: {item!r}. Choose exactly one item_id from these candidates:
{json.dumps(compact, ensure_ascii=False)}
Return the required JSON object.""",
        schema=SELECTION_SCHEMA,
    )
    shop = next((candidate for candidate in candidates if candidate["id"] == selection.get("item_id")), None)
    if shop is None:
        raise RuntimeError(f"harness selected a shop outside the shortlist: {selection}")
    print("ROUTE", json.dumps({"item": item, "shop": shop["id"], "reason": selection["reason"]}, ensure_ascii=False))
    return shop


def buy(assignment: dict) -> dict:
    command = [
        sys.executable, str(ROOT / "buyer_agent.py"),
        "--url", assignment["shop"]["url"],
        "--item", assignment["item"],
        "--budget", str(assignment["limit"]),
    ]
    result = subprocess.run(command, text=True, capture_output=True, timeout=300, check=False)
    receipt = None
    for line in result.stdout.splitlines():
        if line.startswith("RECEIPT "):
            receipt = json.loads(line.removeprefix("RECEIPT "))
    return {
        "item": assignment["item"], "shop": assignment["shop"]["id"],
        "limit": assignment["limit"], "returncode": result.returncode,
        "receipt": receipt, "stdout": result.stdout, "stderr": result.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--shopping-list", required=True)
    parser.add_argument("--budget", required=True, type=int)
    parser.add_argument("--output")
    args = parser.parse_args()
    directory = json.loads(Path(args.directory).read_text(encoding="utf-8"))
    wanted = json.loads(Path(args.shopping_list).read_text(encoding="utf-8"))
    names = [row["item"] for row in wanted]
    if len(names) != len(set(names)):
        raise ValueError("shopping list contains a duplicate item")

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        shops = list(executor.map(read_card, directory))
    assignments = [
        {"item": row["item"], "limit": row["limit"], "shop": choose_shop(shops, row["item"])}
        for row in wanted
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(assignments)) as executor:
        results = list(executor.map(buy, assignments))

    receipts = [row["receipt"] for row in results if row["receipt"]]
    total = sum(int(row["price"]) for row in receipts)
    bought = [row["item"] for row in receipts]
    passed = (
        len(receipts) == len(wanted)
        and sorted(bought) == sorted(names)
        and len(bought) == len(set(bought))
        and total <= args.budget
        and all(int(row["receipt"]["price"]) <= row["limit"] for row in results if row["receipt"])
    )
    summary = {
        "directory_cards": len(shops), "shopping_list": wanted,
        "total_budget": args.budget, "total_spent": total,
        "bought": bought, "results": results, "passed": passed,
    }
    output = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print("SUMMARY", json.dumps({
        "directory_cards": len(shops), "total_spent": total,
        "budget": args.budget, "bought": bought, "passed": passed,
    }, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
