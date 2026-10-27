"""Two equal live-market observation windows; never submit a synthetic trade."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
MARKET = "https://market.ourolabs.ai"


def fetch(path):
    with urlopen(MARKET + path, timeout=20) as response:
        return json.load(response)


def save(path, data):
    with path.open("x", encoding="utf-8") as out:
        json.dump(data, out, ensure_ascii=False, indent=2)


def snapshot(folder, label):
    board = fetch("/api/board?round=2")
    data = {"observed_at": datetime.now(timezone.utc).isoformat(), "epoch": time.time(),
            "source": MARKET + "/api/board?round=2", "board": board}
    save(folder / (label + ".json"), data)
    shop = next(row for row in board["shops"] if row["name"] == "seochanit")
    total = sum(row["deals"] for row in board["shops"])
    print(label, "alive", shop["alive"], "deals", shop["deals"], "market deals", total, flush=True)
    return data


def metrics(start, end):
    def point(data):
        rows = data["board"]["shops"]
        own = next(row for row in rows if row["name"] == "seochanit")
        total = sum(row["deals"] for row in rows)
        return {"deals": own["deals"], "revenue": own["revenue"], "market_deals": total,
                "alive": own["alive"], "cumulative_share_percent": 100 * own["deals"] / total if total else None,
                "cumulative_mean_price": own["revenue"] / own["deals"] if own["deals"] else None}
    before, after = point(start), point(end)
    deals = after["deals"] - before["deals"]
    revenue = after["revenue"] - before["revenue"]
    market_deals = after["market_deals"] - before["market_deals"]
    if min(deals, revenue, market_deals) < 0:
        raise ValueError("Counters reset; windows are not comparable")
    return {"start": before, "end": after, "seconds": end["epoch"] - start["epoch"],
            "window_deals": deals, "window_revenue": revenue, "window_market_deals": market_deals,
            "window_share_percent": 100 * deals / market_deals if market_deals else None,
            "window_mean_price": revenue / deals if deals else None}


def commit_policy(message):
    path = str((ROOT / "prompts/seller.md").relative_to(REPO))
    subprocess.run(["git", "add", "--", path], cwd=REPO, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=REPO, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-seconds", type=int, default=180)
    args = parser.parse_args()
    if args.window_seconds < 60:
        parser.error("Use at least a minute per real observation window")
    folder = ROOT / "logs/challenge-1" / datetime.now().strftime("live-%Y%m%d-%H%M%S")
    folder.mkdir(parents=True)
    prompt = ROOT / "prompts/seller.md"
    candidate = prompt.read_bytes()
    baseline = subprocess.check_output(["git", "show", "30d0cac:submissions/26510121/week-07/prompts/seller.md"], cwd=REPO)
    (folder / "baseline-policy.md").write_bytes(baseline)
    (folder / "candidate-policy.md").write_bytes(candidate)
    reviews = fetch("/api/shop-reviews?shop=seochanit")
    save(folder / "own-reviews.json", {"source": MARKET + "/api/shop-reviews?shop=seochanit", "reviews": reviews})
    save(folder / "plan.json", {"created_at": datetime.now(timezone.utc).isoformat(),
         "window_seconds": args.window_seconds, "baseline_commit": "30d0cac",
         "candidate_sha256": hashlib.sha256(candidate).hexdigest(), "reserves": {"lamp":100,"chair":180,"bookshelf":260},
         "hypothesis": "Use own low rating and near-budget purchase evidence to prefer published minimum opening asks over modest margins",
         "scope": "One real shop in R2, equal sequential windows; no self-purchases or fabricated market results",
         "limitations": ["Not randomized; market mix and existing reputation vary", "Zero sales yields an undefined window mean, not a zero price"]})
    results = {}
    try:
        prompt.write_bytes(baseline)
        commit_policy("experiment(week-07): observe the prior margin policy in the live market")
        for phase in ("baseline", "candidate"):
            if phase == "candidate":
                prompt.write_bytes(candidate)
                commit_policy("feat(week-07): apply minimum-price policy after reviewing live feedback")
            start = snapshot(folder, phase + "-start")
            deadline = time.monotonic() + args.window_seconds
            sample = 0
            while time.monotonic() < deadline:
                time.sleep(min(30, max(0, deadline - time.monotonic())))
                sample += 1
                snapshot(folder, f"{phase}-sample-{sample}")
            end = snapshot(folder, phase + "-end")
            results[phase] = metrics(start, end)
        save(folder / "comparison.json", results)
        print("Comparison written:", folder / "comparison.json", flush=True)
    finally:
        # Even interrupted observations must leave the chosen production policy.
        if prompt.read_bytes() != candidate:
            prompt.write_bytes(candidate)
            commit_policy("fix(week-07): restore production policy after an interrupted experiment")


if __name__ == "__main__":
    main()
