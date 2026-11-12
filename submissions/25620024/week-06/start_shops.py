"""Week 06 challenge 2 — start_shops.py: the shopping street (design/challenge-2-buyer.md part A).

    python start_shops.py                  # 20 shops from shops.json on ports 9301-9320

Starts one catalog_seller.py per shop in shops.json (categories [] = everything), waits until
every Agent Card answers, and writes directory.json: the URLs only -- the buyer learns names
and what each shop sells from the cards themselves. Shop output goes to logs/challenge-2/shops/.
Ctrl+C stops every shop.
"""
import json
import os
import subprocess
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
FIRST_PORT = 9301


def start(shops_file="shops.json", first_port=FIRST_PORT, out_dir=os.path.join(HERE, "logs", "challenge-2", "shops")):
    """Start every shop; returns (processes, urls) once every card answers."""
    shops = json.load(open(os.path.join(HERE, shops_file), encoding="utf-8"))
    os.makedirs(out_dir, exist_ok=True)
    procs, urls = [], []
    for i, shop in enumerate(shops):
        port = first_port + i
        cmd = [sys.executable, "catalog_seller.py", "--port", str(port), "--name", shop["name"],
               "--markup", str(shop["markup"])]
        if shop["categories"]:
            cmd += ["--categories", ",".join(shop["categories"])]
        out = open(os.path.join(out_dir, f"{port}.log"), "w", encoding="utf-8")
        procs.append(subprocess.Popen(cmd, cwd=HERE, stdout=out, stderr=subprocess.STDOUT))
        urls.append(f"http://127.0.0.1:{port}/")
    for url in urls:
        for _ in range(60):
            try:
                if httpx.get(url + ".well-known/agent-card.json", timeout=2).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError(f"no Agent Card at {url}")
    return procs, urls


def stop(procs):
    for p in procs:
        p.terminate()


if __name__ == "__main__":
    procs, urls = start()
    with open(os.path.join(HERE, "directory.json"), "w", encoding="utf-8") as f:
        json.dump(urls, f, indent=2)
    for url in urls:
        card = httpx.get(url + ".well-known/agent-card.json").json()
        print(f"{url}  {card['name']:24} {card['description'][:70]}")
    print(f"\n{len(urls)} shops open; directory.json written. Ctrl+C closes them.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop(procs)
        print("all shops closed")
