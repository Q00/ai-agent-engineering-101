"""Week 06 challenge 2 — shop_picker.py: part B, choose shops from the Agent Cards (the DF).

    python shop_picker.py                      # directory.json + shopping_list.json

Reads every card in the directory, gives the harness one line per card (name, skill description,
tags) and the shopping list, and asks for 2-3 shops per item. The harness answers with card
numbers, not URLs, so it cannot mistype an address.

Code guards:
  - numbers that are not in the directory are dropped; duplicates removed; at most 3 per item
  - an item left with no shop gets the shops whose card tags name a word of the item
    (or, failing that, the shops that sell every category)
"""
import json
import os
import re
import sys

import httpx

from harness import ask

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_SHOPS = 3

PICK_PROMPT = """You are a buyer's assistant choosing which shops to ask for each item on a shopping list.
Shops (from their Agent Cards):
{cards}

Shopping list:
{items}

For each item choose 2 or 3 shops whose cards say they sell that kind of item. Prefer shops that
specialise in it; include one general store as a backup. Use only the shop numbers above.
Reply with one JSON object only, item number -> list of shop numbers, for example
{{"1": [3, 7, 13], "2": [5, 14]}}"""


def fetch_cards(urls):
    cards = []
    for url in urls:
        card = httpx.get(url.rstrip("/") + "/.well-known/agent-card.json", timeout=10).json()
        iface = next(i for i in card["supportedInterfaces"] if i.get("protocolBinding") == "JSONRPC")
        skill = card["skills"][0] if card.get("skills") else {}
        cards.append({"name": card["name"], "url": iface["url"], "description": skill.get("description", card["description"]),
                      "tags": skill.get("tags", [])})
    return cards


def card_line(n, c):
    sells = re.search(r"Sells ([^.]*)\.", c["description"])
    return f"{n}. {c['name']} -- sells {sells.group(1) if sells else c['description'][:80]}"


def pick_shops(items, cards):
    """Returns ({item: [card index, ...]}, notes). Indices are 0-based into cards."""
    prompt = PICK_PROMPT.format(cards="\n".join(card_line(i + 1, c) for i, c in enumerate(cards)),
                                items="\n".join(f"{i + 1}. {it}" for i, it in enumerate(items)))
    notes = []
    try:
        answer = ask(prompt)
    except Exception as e:
        answer, _ = {}, notes.append(f"harness error: {e}")
    chosen = {}
    for i, item in enumerate(items):
        raw = answer.get(str(i + 1)) or []
        picked = []
        for n in raw if isinstance(raw, list) else []:
            if isinstance(n, int) and 1 <= n <= len(cards) and n - 1 not in picked:
                picked.append(n - 1)
            elif not (isinstance(n, int) and 1 <= n <= len(cards)):
                notes.append(f"GUARD item {i + 1}: shop {n!r} is not in the directory, dropped")
        if len(picked) > MAX_SHOPS:
            notes.append(f"GUARD item {i + 1}: {len(picked)} shops chosen, kept the first {MAX_SHOPS}")
            picked = picked[:MAX_SHOPS]
        if not picked:
            words = set(re.findall(r"[a-z]+", item.lower()))
            picked = [j for j, c in enumerate(cards) if any(t in words for t in c["tags"]
                                                            if t not in ("shopping", "furniture", "negotiation", "catalog"))]
            if not picked:
                picked = [j for j, c in enumerate(cards) if len(c["tags"]) >= 14]   # sells all 10 categories
            picked = picked[:MAX_SHOPS]
            notes.append(f"GUARD item {i + 1}: no valid shop from the harness, code chose {[cards[j]['name'] for j in picked]}")
        chosen[item] = picked
    return chosen, notes


if __name__ == "__main__":
    urls = json.load(open(os.path.join(HERE, "directory.json")))
    items = json.load(open(os.path.join(HERE, "shopping_list.json")))["items"]
    cards = fetch_cards(urls)
    print(f"directory: {len(cards)} cards")
    for i, c in enumerate(cards):
        print("  ", card_line(i + 1, c))
    chosen, notes = pick_shops(items, cards)
    print("\nshops chosen per item:")
    for item, idx in chosen.items():
        print(f"  {item}\n      -> " + ", ".join(cards[j]["name"] for j in idx))
    for n in notes:
        print("note:", n)
