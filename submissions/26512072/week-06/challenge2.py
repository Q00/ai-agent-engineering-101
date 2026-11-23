"""Challenge 2: run 20 copies of my seller (different categories and prices), then the shopper.

  python challenge2.py            # writes logs/challenge-2/

Each shop is seller.py on the shared catalog, restricted to 3 categories and with its own markup
on the reserves (0.9 to 1.3). The shopping list is 5 described items; the budget is 1.25 x the sum
of their catalog reserves. Pass (checked here from the shops' own logs, not from the shopper):
every item bought exactly once, total within the budget.
"""
import json
import math
import random
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
OUT = HERE / "logs" / "challenge-2"
CATS = ["bookshelf", "desk", "office chair", "dining chair", "desk lamp", "floor lamp", "dining table",
        "coffee table", "wardrobe", "nightstand", "shoe rack", "tv stand", "sofa", "stool", "coat rack"]
NAMES = ["Oak & Iron", "Home Basics", "Nordic Room", "Budget Barn", "Loft Supply", "Casa Mia", "Urban Nest",
         "The Furniture Yard", "Bright Corner", "Maple Street", "Second Life", "Quiet Living", "Studio Nine",
         "Harbor Home", "Pine & Co", "Workspace Hub", "Cozy Den", "Grand Hall", "Little Flat", "Atelier"]
LIST = [  # (category, description template)
    ("bookshelf", "something {family} to keep about {cap} books"),
    ("desk lamp", "a {family} desk lamp around {cap} lumens"),
    ("dining table", "a {family} table where {cap} people can eat together"),
    ("shoe rack", "something {family} by the door for about {cap} pairs of shoes"),
    ("nightstand", "a small {family} bedside table with {cap} drawers"),
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    rng = random.Random(62)
    items = json.load(open(HERE / "catalog.json", encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    list_cats = [c for c, _ in LIST]
    shops = []
    for k, name in enumerate(NAMES):
        cats = rng.sample(CATS, 3)
        if k < 15:  # make sure each list category is sold by at least three shops
            cats[0] = list_cats[k % len(list_cats)]
        shops.append({"name": name, "categories": sorted(set(cats)), "markup": round(rng.uniform(0.9, 1.3), 2),
                      "port": free_port()})
    targets = [rng.choice([i for i in items if i["category"] == c]) for c in list_cats]
    wanted = [tmpl.format(family=t["family"], cap=t["capacity"]) for (c, tmpl), t in zip(LIST, targets)]
    budget = math.ceil(sum(t["reserve"] for t in targets) * 1.25)
    (OUT / "shops.json").write_text(json.dumps(shops, indent=1) + "\n")
    (OUT / "directory.json").write_text(json.dumps([f"http://127.0.0.1:{s['port']}" for s in shops], indent=1) + "\n")
    (OUT / "shopping_list.json").write_text(json.dumps(wanted, indent=1) + "\n")
    print(f"shopping list {wanted}, budget {budget}", flush=True)

    procs = []
    for s in shops:
        out = open(OUT / f"shop-{s['port']}.log", "w")
        procs.append((subprocess.Popen(
            [sys.executable, "-u", "seller.py", "--port", str(s["port"]), "--catalog", "catalog.json",
             "--categories", ",".join(s["categories"]), "--markup", str(s["markup"]), "--name", s["name"]],
            cwd=HERE, stdout=out, stderr=subprocess.STDOUT), out))
    try:
        for s in shops:
            for _ in range(60):
                try:
                    if httpx.get(f"http://127.0.0.1:{s['port']}/.well-known/agent-card.json", timeout=2).status_code == 200:
                        break
                except Exception:
                    time.sleep(1)
        t0 = time.time()
        with open(OUT / "shopper.log", "w") as fh:
            subprocess.run([sys.executable, "-u", "shopper.py", "--directory", str(OUT / "directory.json"),
                            "--list", str(OUT / "shopping_list.json"), "--budget", str(budget)],
                           cwd=HERE, stdout=fh, stderr=subprocess.STDOUT, timeout=900)
        secs = time.time() - t0
    finally:
        for p, out in procs:
            p.terminate()
            p.wait(timeout=10)
            out.close()

    # independent check from the shops' logs
    deals = []
    for s in shops:
        for m in re.finditer(r"DEAL (c\d+) at (\d+)", (OUT / f"shop-{s['port']}.log").read_text()):
            item = next(i for i in items if i["id"] == m.group(1))
            deals.append({"shop": s["name"], "item_id": item["id"], "item": item["name"], "category": item["category"],
                          "price": int(m.group(2))})
    per_cat = {c: [d for d in deals if d["category"] == c] for c in list_cats}
    total = sum(d["price"] for d in deals)
    shopper_result = re.findall(r"\[result\] (.*)", (OUT / "shopper.log").read_text())
    check = {
        "shopping_list": wanted, "budget": budget, "deals_seen_by_shops": deals, "total": total,
        "every_item_bought": all(len(v) >= 1 for v in per_cat.values()),
        "no_item_twice": all(len(v) <= 1 for v in per_cat.values()) and len(deals) == len(wanted),
        "within_budget": total <= budget, "seconds": round(secs),
        "shopper_result": json.loads(shopper_result[-1]) if shopper_result else None,
    }
    check["pass"] = check["every_item_bought"] and check["no_item_twice"] and check["within_budget"]
    (OUT / "check.json").write_text(json.dumps(check, indent=2) + "\n")
    print(json.dumps({k: check[k] for k in ("every_item_bought", "no_item_twice", "within_budget", "total",
                                             "budget", "seconds", "pass")}))


if __name__ == "__main__":
    main()
