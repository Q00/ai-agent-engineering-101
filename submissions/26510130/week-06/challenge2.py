"""Challenge 2 — the buyer with a directory of shops and one total budget.

Starts ~20 copies of the seller, each with its own catalog slice and its own
prices, writes a directory of their Agent Cards, and runs the buyer against
all of them with a shopping list and one budget.

    python challenge2.py --shops 20

The conversation with 20 shops does not fit in one prompt and does not need
to. State is carried in code as a small ledger -- one row per item, one row
per open negotiation -- and the harness is called per decision with just that
row plus the last few lines of that shop's transcript. What the harness never
sees: the other shops' transcripts, the full directory, or the catalog.

Writes logs/challenge-2/.
"""
import argparse
import json
import random
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

import harness

HEADERS = {"A2A-Version": "1.0"}
MAX_ROUNDS = 5          # haggling rounds per shop
SEED = 26510130

LIST = [
    {"want": "a desk lamp for reading", "kind": "reading light"},
    {"want": "something to keep about 40 books, lower than a desk", "kind": "low bookcase"},
    {"want": "a small table beside the sofa", "kind": "side table"},
    {"want": "a wall clock that is quiet", "kind": "clock"},
    {"want": "a box to stack and hide small things", "kind": "storage box"},
]
TOTAL_BUDGET = 500


def free_port(base, i):
    return base + i


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
    return int(float(nums[-1])) if nums else None


def receipt(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and "price" in d:
                return d
    return None


def build_shops(n, outdir, catalog_path):
    """Each shop gets a slice of the catalog and a price multiplier, so the
    same item costs different amounts in different shops -- otherwise choosing
    a shop would not be a decision."""
    rng = random.Random(SEED)
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    kinds = sorted({c.get("kind") or c["name"] for c in catalog})
    wanted = [w["kind"] for w in LIST]
    outdir.mkdir(parents=True, exist_ok=True)
    shops = []
    for i in range(n):
        mult = round(rng.uniform(0.8, 1.35), 2)
        # Each shop stocks a few kinds and says so on its card. At least two
        # shops carry each item on the list, so the list is buyable -- but no
        # shop carries all of it, so the directory has to be read.
        stocks = set(rng.sample(kinds, k=rng.randint(3, 5)))
        stocks.add(wanted[i % len(wanted)])
        stocks = sorted(stocks)
        rows = [dict(c, reserve=max(15, int(c["reserve"] * mult))) for c in catalog
                if (c.get("kind") or c["name"]) in stocks]
        path = outdir / f"catalog-{i:02d}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        shops.append({"name": f"Shop {i:02d}", "catalog": str(path), "mult": mult,
                      "stocks": stocks, "items": len(rows)})
    return shops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", type=int, default=20)
    ap.add_argument("--base-port", type=int, default=9400)
    ap.add_argument("--catalog", default="catalog.json")
    args = ap.parse_args()

    out = Path("logs/challenge-2")
    out.mkdir(parents=True, exist_ok=True)
    cat_dir = out / "catalogs"
    shops = build_shops(args.shops, cat_dir, args.catalog)

    lines = []

    def log(s):
        print(s)
        lines.append(str(s))

    procs, directory = [], []
    shop_log = (out / "shops.log").open("w", encoding="utf-8")
    try:
        for i, sh in enumerate(shops):
            port = free_port(args.base_port, i)
            procs.append(subprocess.Popen(
                [sys.executable, "seller.py", "--port", str(port),
                 "--catalog", sh["catalog"], "--name", sh["name"],
                 "--stocks", ",".join(sh["stocks"])],
                stdout=shop_log, stderr=subprocess.STDOUT))
            sh["url"] = f"http://127.0.0.1:{port}"
        log(f"# challenge 2: {len(shops)} shops, shopping list of {len(LIST)}, "
            f"one budget of {TOTAL_BUDGET}")

        deadline = time.time() + 120
        for sh in shops:
            while time.time() < deadline:
                try:
                    r = httpx.get(sh["url"] + "/.well-known/agent-card.json", timeout=3)
                    if r.status_code == 200:
                        sh["card"] = r.json()
                        directory.append({"name": sh["card"].get("name"),
                                          "url": sh["url"],
                                          "skills": [s.get("description", "")
                                                     for s in sh["card"].get("skills", [])]})
                        break
                except Exception:
                    time.sleep(0.5)
        (out / "directory.json").write_text(
            json.dumps(directory, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        log(f"# directory: {len(directory)} Agent Cards read from "
            f"/.well-known/agent-card.json\n")

        # ---- the ledger: all the state the buyer carries, in code
        ledger = [{"want": w["want"], "kind": w["kind"], "bought": False,
                   "price": None, "shop": None, "tried": []} for w in LIST]
        spent = 0

        for row in ledger:
            remaining = TOTAL_BUDGET - spent
            unbought = sum(1 for r in ledger if not r["bought"])
            # One total budget spent unevenly. An equal split starved the first
            # item in the previous run: cap 100 against asks of 160-185 for a
            # lamp whose reserve was 93-125, so nothing could close.
            per_item = max(10, remaining - 40 * (unbought - 1))
            log(f"## {row['want']!r}")
            log(f"   budget left {remaining}, items left {unbought} -> cap {per_item} for this item")

            # the harness picks which shops to try, from the directory only
            listing = "\n".join(
                f'{i}. {d["name"]}: {d["skills"][0].split(". Name or")[0]}'
                for i, d in enumerate(directory))
            d = harness.ask(
                "You are shopping with a directory of shops. Each entry says what that "
                "shop stocks. Pick the three shops most likely to have this item.\n\n"
                f"Item wanted: {row['want']!r}\n\nDirectory:\n{listing}\n\n"
                'Reply with one JSON object and nothing else: {"shops": [<numbers>]}.',
                want=("shops",), tag=" shops")
            picks = [i for i in (d.get("shops") or []) if isinstance(i, int) and 0 <= i < len(directory)]
            if not picks:
                picks = list(range(min(3, len(directory))))
                log("   harness gave no usable shop list; falling back to the first three")
            picks = picks[:3]
            log(f"   trying shops {picks}")

            for si in picks:
                if row["bought"]:
                    break
                sh_url = directory[si]["url"] + "/"
                t, err = send(sh_url, row["want"])
                if t is None or state(t) != "TASK_STATE_INPUT_REQUIRED":
                    log(f"   {directory[si]['name']}: does not stock it ({state(t) or err})")
                    row["tried"].append({"shop": directory[si]["name"], "declined": True})
                    continue
                tid = t["id"]
                offered = text_of(t).split(":")[0].strip()
                ask = last_number(t)
                log(f"   {directory[si]['name']}: offers {offered!r} at {ask}")
                row["tried"].append({"shop": directory[si]["name"], "item": offered,
                                     "opening_ask": ask})

                # per-shop transcript, kept short on purpose: this is the only
                # history the harness sees for this negotiation
                convo = [f"shop: {text_of(t)[:160]}"]
                for _ in range(MAX_ROUNDS):
                    if state(t) != "TASK_STATE_INPUT_REQUIRED":
                        break
                    ask = last_number(t)
                    dd = harness.ask(
                        "You are haggling for one item inside a bigger shopping trip.\n"
                        f"Item: {row['want']}\n"
                        f"The most you may pay for this item is {per_item}. Never go above it.\n"
                        f"The shop is asking {ask}.\n"
                        "Recent exchange with THIS shop:\n" + "\n".join(convo[-4:]) + "\n\n"
                        "Accept if the ask is at or below your cap. Otherwise counter at or "
                        "below your cap. Walk away if this shop cannot reach it.\n"
                        'Reply with one JSON object and nothing else: '
                        '{"action": "accept"|"counter"|"walk_away", "price": <whole number>}.',
                        want=("action",), tag=" haggle")
                    action = str(dd.get("action", "")).lower().strip()
                    price = harness.as_int(dd.get("price"))
                    if action == "accept" and (ask is None or ask > per_item):
                        log(f"   REFUSED harness accept at {ask} > cap {per_item}")
                        action = "counter"
                        price = None
                    if action == "walk_away":
                        log(f"   walking away from {directory[si]['name']} (ask {ask} > cap {per_item})")
                        break
                    if action == "accept":
                        price = ask
                    if price is None:
                        price = min(per_item, max(1, (ask or per_item)))
                    price = min(price, per_item)          # code enforces the cap
                    convo.append(f"me: {price}")
                    t, err = send(sh_url, str(price), tid)
                    if t is None:
                        break
                    convo.append(f"shop: {text_of(t)[:160]}")
                    log(f"      offer {price} -> {state(t)} | {text_of(t)[:90]}")
                    r = receipt(t)
                    if state(t) == "TASK_STATE_COMPLETED" and r:
                        if r["price"] > remaining:
                            log(f"      !! {r['price']} over the remaining budget; not counted")
                            break
                        row.update(bought=True, price=r["price"], shop=directory[si]["name"],
                                   item=r.get("item"))
                        spent += r["price"]
                        log(f"   BOUGHT {r.get('item')} at {r['price']} from "
                            f"{directory[si]['name']} | spent {spent}/{TOTAL_BUDGET}")
                        break
            if not row["bought"]:
                log(f"   not bought: {row['want']!r}\n")
            else:
                log("")

        bought = [r for r in ledger if r["bought"]]
        names = [r.get("item") for r in bought]
        log(f"# bought {len(bought)}/{len(ledger)} items, total {spent} of {TOTAL_BUDGET}")
        log(f"# within budget: {spent <= TOTAL_BUDGET}")
        log(f"# no item bought twice: {len(names) == len(set(names))} ({names})")
        (out / "ledger.json").write_text(
            json.dumps({"budget": TOTAL_BUDGET, "spent": spent, "ledger": ledger},
                       indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    finally:
        for p in procs:
            p.terminate()
        shop_log.close()
    (out / "transcript.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {out}/transcript.log, ledger.json, directory.json")


if __name__ == "__main__":
    main()
