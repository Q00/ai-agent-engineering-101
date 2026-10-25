"""Challenge 1 — the seller on a large catalog.

Buyers do not name the item; they describe it. The catalog is 3000 rows and
never enters a prompt: `seller.search()` narrows it in code and the harness
chooses from six candidates.

    python challenge1.py --port 9911

Writes logs/challenge-1/ : the transcript, and what the harness was actually
shown at each step.
"""
import argparse
import json
import math
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

HEADERS = {"A2A-Version": "1.0"}

# Described, never named. The expected row is what a careful human would pick;
# the run records whether the seller landed on it.
REQUESTS = [
    ("something wooden to keep about 40 books, not taller than my desk",
     "low bookcase"),
    ("I need a light that hangs over the dining table", "pendant lamp"),
    ("a padded seat with arms, for reading in the evening", "armchair"),
    ("two panels to cover one window in the bedroom", "curtain pair"),
    ("somewhere to file documents, with drawers", "filing cabinet"),
    ("a small table to put beside the sofa", "side table"),
]


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in r:
        return None, r["error"]
    return r["result"].get("task", r["result"]), None


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    return rpc(url, "SendMessage", {"message": msg})


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def text_of(t):
    return " ".join(p.get("text", "")
                    for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def last_number(t):
    nums = re.findall(r"\d+(?:\.\d+)?", text_of(t))
    return math.ceil(float(nums[-1])) if nums else None


def receipt(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and "price" in d:
                return d
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9911)
    ap.add_argument("--catalog", default="catalog.json")
    args = ap.parse_args()

    out = Path("logs/challenge-1")
    out.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    by_name = {c["name"]: c for c in catalog}
    url = f"http://127.0.0.1:{args.port}/"

    seller_log = (out / "seller.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "seller.py", "--port", str(args.port),
                             "--catalog", args.catalog, "--name", "Harness Depot"],
                            stdout=seller_log, stderr=subprocess.STDOUT)
    lines, rows = [], []

    def log(s):
        print(s)
        lines.append(s)

    try:
        for _ in range(60):
            try:
                if httpx.get(f"http://127.0.0.1:{args.port}/.well-known/agent-card.json",
                             timeout=3).status_code == 200:
                    break
            except Exception:
                time.sleep(1)

        log(f"# challenge 1: catalog of {len(catalog)} items, never put in a prompt")
        log(f"# the harness sees at most 6 candidates chosen by seller.search()\n")

        for request, expected in REQUESTS:
            log(f"## request: {request!r}   (a human would pick: {expected})")
            # what the code hands the harness, recorded so the report can show it
            import seller as seller_mod
            cands = seller_mod.search(catalog, request)
            log(f"   search() narrowed {len(catalog)} -> {len(cands)}: "
                + ", ".join(c["name"] for c in cands))

            t, err = send(url, request)
            if t is None:
                log(f"   ERROR {err}")
                continue
            tid, ask = t["id"], last_number(t)
            sold = text_of(t).split(":")[0].strip()
            log(f"   seller -> {state(t)} | {text_of(t)[:150]}")
            reserve = by_name.get(sold, {}).get("reserve")
            log(f"   picked {sold!r} (reserve {reserve}), opening ask {ask}")

            asks, deal = [ask], None
            offer = int((reserve or ask) * 0.8) if reserve else ask
            for _ in range(6):
                if state(t) != "TASK_STATE_INPUT_REQUIRED":
                    break
                a = last_number(t)
                offer = a if (a is not None and reserve and a <= reserve * 1.15) else offer + max(5, (reserve or 100) // 10)
                t, err = send(url, str(offer), tid)
                if t is None:
                    break
                if state(t) == "TASK_STATE_INPUT_REQUIRED":
                    asks.append(last_number(t))
                log(f"   offer {offer} -> {state(t)} | {text_of(t)[:110]}")
            r = receipt(t)
            below = [a for a in asks if a is not None and reserve and a < reserve]
            ok_item = sold == expected
            ok_price = (r is None) or (reserve is None) or (r["price"] >= reserve)
            log(f"   RESULT item={'OK' if ok_item else 'MISS (' + str(expected) + ')'} "
                f"| asks {asks} | below reserve: {below or 'none'} "
                f"| receipt {r['price'] if r else None}\n")
            rows.append({"request": request, "expected": expected, "picked": sold,
                         "reserve": reserve, "asks": asks, "below_reserve": below,
                         "receipt": r, "item_ok": ok_item, "price_ok": ok_price,
                         "candidates": [c["name"] for c in cands]})

        hit = sum(r["item_ok"] for r in rows)
        bad = [r for r in rows if r["below_reserve"] or not r["price_ok"]]
        log(f"# picked the expected item in {hit}/{len(rows)} requests")
        log(f"# asks or sales below reserve: {len(bad)}")
    finally:
        proc.terminate()
        seller_log.close()

    (out / "transcript.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "results.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    print(f"\nwrote {out}/transcript.log and results.json")


if __name__ == "__main__":
    main()
