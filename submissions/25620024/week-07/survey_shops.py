"""Week 07 — survey_shops.py: window-shop every shop in the market and write down what it says.

    python survey_shops.py                      # all shops, lamp / chair / bookshelf
    python survey_shops.py --items lamp

For every shop in /api/shops: read its Agent Card, ask for each item ("I want to buy a lamp"), record the
opening ask, how long the reply took, how long the message is (words a buyer's model has to read), and any
phrases worth a second look; then cancel the task. No offer is ever sent, nothing is bought, no order is
used and nothing goes to the market's ledger. Market numbers (share, stars, reviews, receipts with an
order) come from /api/stats for the current round.

Writes logs/challenge-2/survey-<HHMM>.json (everything) and prints a summary table.
"""
import argparse
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

from market_buyer import FLOOR, asking_price

MARKET = "https://market.ourolabs.ai"
HEADERS = {"A2A-Version": "1.0"}
ME = "dragonash"
LOOK_AT = {   # phrases a careful buyer would want to read twice
    "review for discount": r"(five|5)[- ]star review|promise .{0,20}review|review .{0,20}(discount|off)",
    "asks who you are": r"\bname\b.{0,30}(buyer|market)|send (your|the) (name|receipt number)|bought here before",
    "invented details": r"warranty|handmade|solid[- ]brass|oak|walnut|free (delivery|shipping)|limited stock",
    "sold out / closed": r"sold out|closed|come back",
    "pressure": r"only today|last chance|hurry|expires",
}


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=60).json()
    if "error" in r:
        raise RuntimeError(str(r["error"])[:120])
    return r["result"].get("task", r["result"])


def visit(shop, item):
    out = {"shop": shop["name"], "item": item}
    t0 = time.time()
    try:
        card = httpx.get(shop["url"].rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=15).json()
        url = next(i for i in card["supportedInterfaces"] if i.get("protocolBinding") == "JSONRPC")["url"]
        out["card_name"], out["card_desc"] = card.get("name"), (card.get("description") or "")[:300]
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": f"I want to buy a {item}"}]}
        task = rpc(url, "SendMessage", {"message": msg})
        out["seconds"] = round(time.time() - t0, 1)
        out["state"] = task.get("status", {}).get("state")
        text = " ".join(p.get("text", "") for p in task.get("status", {}).get("message", {}).get("parts", []))
        out["reply"] = text[:500]
        out["words"] = len(text.split())
        out["ask"] = asking_price(task) if out["state"] == "TASK_STATE_INPUT_REQUIRED" else None
        out["flags"] = [k for k, rx in LOOK_AT.items() if re.search(rx, text + " " + out["card_desc"], re.I)]
        if out["state"] == "TASK_STATE_INPUT_REQUIRED":
            try:
                rpc(url, "CancelTask", {"id": task["id"]})
                out["canceled"] = True
            except Exception as e:
                out["canceled"] = f"cancel failed: {e}"
    except Exception as e:
        out["seconds"] = round(time.time() - t0, 1)
        out["error"] = f"{type(e).__name__}: {str(e)[:100]}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", default="lamp,chair,bookshelf")
    a = ap.parse_args()
    items = a.items.split(",")
    shops = httpx.get(MARKET + "/api/shops", timeout=20).json()["shops"]
    stats = httpx.get(MARKET + "/api/stats", timeout=20).json()
    rnd = stats["round"]
    rows = {s["shop"]: s for s in stats["rounds"][str(rnd)]["shops"]}
    started = datetime.now()
    jobs = [(s, it) for s in shops for it in items]
    with ThreadPoolExecutor(max_workers=8) as pool:
        visits = list(pool.map(lambda j: visit(*j), jobs))

    report = {"started": started.isoformat(timespec="seconds"), "round": rnd, "items": items, "shops": []}
    for s in shops:
        v = [x for x in visits if x["shop"] == s["name"]]
        st = rows.get(s["name"], {})
        revs = s.get("reviews") or []
        report["shops"].append({
            "name": s["name"], "url": s["url"], "market": {k: st.get(k) for k in
                                                          ("deals", "share", "stars", "reviews", "receipts", "receipts_with_order")},
            "recent_reviews": [r.get("review", "")[:160] for r in revs[:3]],
            "visits": v})
    out = Path(__file__).parent / "logs" / "challenge-2" / f"survey-{started:%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"round {rnd}, {len(shops)} shops x {items}, {started:%Y-%m-%d %H:%M}  -> {out.name}")
    print(f"{'shop':22} {'share':>6} {'stars':>5} {'w/ord':>5} " + " ".join(f"{i[:5]:>6}" for i in items)
          + f" {'sec':>5} {'words':>5}  flags / problems")
    for r in sorted(report["shops"], key=lambda r: -(r["market"]["share"] or 0)):
        m, v = r["market"], r["visits"]
        asks = []
        for it in items:
            x = next((y for y in v if y["item"] == it), {})
            asks.append("ERR" if "error" in x else (str(x.get("ask")) if x.get("ask") else (x.get("state") or "-")[11:16] or "-"))
        sec = [x["seconds"] for x in v if "seconds" in x and "error" not in x]
        words = [x["words"] for x in v if "words" in x]
        flags = sorted({f for x in v for f in x.get("flags", [])} | {x["error"][:30] for x in v if "error" in x})
        share = f"{m['share']:.1%}" if m["share"] is not None else "-"
        print(f"{('* ' if r['name'] == ME else '') + r['name']:22.22} {share:>6} {str(m['stars'] or '-'):>5} "
              f"{str(m['receipts_with_order'] if m['receipts_with_order'] is not None else '-'):>5} "
              + " ".join(f"{s:>6}" for s in asks)
              + f" {(sum(sec) / len(sec)) if sec else 0:5.1f} {(sum(words) // len(words)) if words else 0:5}  {', '.join(flags)}")
    print("floors:", FLOOR)


if __name__ == "__main__":
    main()
