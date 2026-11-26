from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
SHA = "ca10429c357c06965a1b3a8e4d986a284cba9ce0"
BASE = "https://raw.githubusercontent.com/dragonash22/ai-agent-engineering-101/" + SHA
FILES = ["deploy/src/index.ts", "deploy/wrangler.jsonc", "market_buyer.py", "harness.py"]
ENDPOINTS = [(n, "https://market.ourolabs.ai/api/" + n) for n in ("board", "shops", "stats")]
ENDPOINTS += [
    ("comparison", "https://ax-week07.treasuresky.xyz/price-comparison.json"),
    ("own-card", "https://ax-week07.treasuresky.xyz/.well-known/agent-card.json"),
    ("dragonash-card", "https://harness-shop-25620024.imajason.workers.dev/.well-known/agent-card.json"),
    ("branches", "https://api.github.com/repos/dragonash22/ai-agent-engineering-101/branches"),
]
ENDPOINTS += [("source-" + f.replace("/", "-"), BASE + "/submissions/25620024/week-07/" + f) for f in FILES]


def fetch(pair):
    label, url = pair
    at = datetime.now(UTC).isoformat()
    try:
        with urlopen(Request(url, headers={"User-Agent": "AX7-public-research"}), timeout=25) as r:
            data = r.read(4_000_000)
            code = r.status
        (HERE / (label + ".raw")).write_bytes(data)
        result = {"url": url, "at": at, "status": code, "bytes": len(data), "sha256": sha256(data).hexdigest()}
    except Exception as e:
        result = {"url": url, "at": at, "error": str(e)}
    (HERE / (label + ".meta.json")).write_text(json.dumps(result, indent=2))
    return label, result


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=5) as pool:
        records = dict(pool.map(fetch, ENDPOINTS))
    (HERE / "source-manifest.json").write_text(json.dumps(records, indent=2))
    board = json.loads((HERE / "board.raw").read_bytes())
    own = next(x for x in board["shops"] if x["name"] == "25620027")
    leader = next(x for x in board["shops"] if x["name"] == "dragonash")
    print(json.dumps({"round": board["round"], "own": own, "leader": leader,
                      "fetch_status": {k:v.get("status",v.get("error")) for k,v in records.items()}}, indent=2))
