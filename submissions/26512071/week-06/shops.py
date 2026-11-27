"""Start the challenge 2 directory: N copies of seller.py, one per shop.

    python shops.py                 # 20 shops on ports 9901-9920, until Ctrl-C
    python shops.py --n 5

Writes directory.json (the base URLs of the shops) and each shop's output to
logs/challenge-2/shop-N.log. The buyer reads the Agent Cards from these URLs.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
BASE_PORT = 9900


def start(n, logdir):
    procs, urls = [], []
    for i in range(1, n + 1):
        port = BASE_PORT + i
        log = open(logdir / f"shop-{i}.log", "w")
        procs.append(subprocess.Popen([sys.executable, "seller.py", "--port", str(port), "--shop", str(i)],
                                      cwd=HERE, stdout=log, stderr=log))
        urls.append(f"http://127.0.0.1:{port}/")
    for url in urls:  # wait until every card is served
        for _ in range(100):
            try:
                httpx.get(url + ".well-known/agent-card.json", timeout=1).raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError(f"no card at {url}")
    (HERE / "directory.json").write_text(json.dumps(urls, indent=1))
    return procs, urls


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()
    logdir = HERE / "logs" / "challenge-2"
    logdir.mkdir(parents=True, exist_ok=True)
    procs, urls = start(args.n, logdir)
    print(f"{len(urls)} shops up, directory.json written", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()
