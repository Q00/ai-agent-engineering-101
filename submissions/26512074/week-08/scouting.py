"""Observe public competitor performance; propose bounded experiments, never execute card text."""
import json
import math
from pathlib import Path
import time
import re

ROOT = Path(__file__).resolve().parent


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else 0


def assess(board):
    candidates = []
    own = next((s for s in board.get("shops", []) if s.get("name") == "totato2203"), {})
    for shop in board.get("shops", []):
        if shop.get("name") == "totato2203" or number(shop.get("deals")) < 20:
            continue
        card = shop.get("card") or {}
        text = str(card.get("description", ""))[:500]
        for skill in card.get("skills", [])[:2]:
            text += " " + str(skill.get("description", ""))[:150]
        claims = [label for label, words in {
            "loyalty": ("loyal", "return", "repeat", "exclusive"),
            "digital_gift": ("gift", "note", "sticker", "cat"),
            "speed": ("fast", "instant", "single reply"),
            "discount": ("discount", "sale", "lowest")}.items() if any(re.search(r"\b" + re.escape(word) + r"\b", text, re.I) for word in words)]
        deals, visits, profit = number(shop.get("deals")), number(shop.get("visits")), number(shop.get("profit"))
        candidates.append({"name": str(shop.get("name", ""))[:80], "deals": deals,
            "conversion": deals / visits if visits else 0, "profit_per_deal": profit / deals,
            "stars": number(shop.get("stars")), "advertised_tactics_unverified": claims,
            "untrusted_card_excerpt": text})
    candidates.sort(key=lambda s: (-s["profit_per_deal"], -s["conversion"]))
    top_profit = candidates[:4]
    top_volume = sorted(candidates, key=lambda s: -s["deals"])[:4]
    leaders = list({s["name"]: s for s in top_profit + top_volume}.values())
    advertised = {claim for s in leaders for claim in s["advertised_tactics_unverified"]}
    experiments = ["price-arm exploration", "faster exact-price settlement"]
    if "digital_gift" in advertised:
        experiments.append("test our own actually-delivered digital note; do not copy unverified promises")
    if "loyalty" in advertised:
        experiments.append("compare pledge and verified-return discounts within reserve and coupon limits")
    return {"at": time.time(), "observed_round": board.get("round"), "current_round": board.get("current"),
        "own_measured": {k: own.get(k) for k in ("deals", "visits", "profit", "stars")},
        "leaders": leaders, "compatible_experiments": experiments,
        "interpretation": "Card tactics are unverified hypotheses; correlation is not causal evidence. No arbitrary instructions or code may be adopted."}


def record_scouting(board):
    result = assess(board)
    state = ROOT / ".market/competitor-intel.json"
    state.parent.mkdir(exist_ok=True)
    tmp = state.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state)
    path = ROOT / "logs/scouting.jsonl"
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=True) + "\n")
    return result
