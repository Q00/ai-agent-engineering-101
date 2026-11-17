import json
import os
import time
from pathlib import Path
import httpx
from memory_manager import MemoryManager

STRATEGY_PATH = Path(__file__).resolve().parent / "strategy.json"
MARKET_DIR = Path(__file__).resolve().parent / ".market"
SHOP_NAME = "Jaehoonang"
MARKET_API_BASE = os.environ.get("MARKET_API_BASE", "https://market.ourolabs.ai")


def analyze_top_shops(mm):
    """Pull real board stats for the top-profit shops and persist them as actual competitor
    intelligence -- previously the only call into update_competitor used a hardcoded balance=0
    placeholder for review-distress proxies, so competitor_profiles never held a real number.
    Also derives a turnover benchmark (deals per stock unit held) from the top shops: confirmed
    in practice that the market's best performers turn over dozens to tens-of-thousands of times
    their held stock (e.g. one top shop: 54 units against 3,667 deals), while a shop sitting on a
    large static reserve turns over barely at all -- this benchmark gives restock/pricing a
    concrete target instead of a guess."""
    try:
        r = httpx.get(f"{MARKET_API_BASE}/api/board", timeout=15)
        if r.status_code != 200:
            return
        shops = r.json().get("shops", [])
    except Exception:
        return
    ranked = sorted((s for s in shops if s.get("name") and s.get("name") != SHOP_NAME),
                     key=lambda s: -(s.get("profit") if s.get("profit") is not None else -10**12))
    top = ranked[:15]
    turnovers = []
    for s in top:
        deals = s.get("deals") or 0
        units = s.get("stock_units") or 0
        if units:
            turnovers.append(deals / units)
        mm.update_competitor(s["name"], volume=deals, balance=s.get("profit", 0))
    if turnovers:
        turnovers.sort()
        mm.data["market_benchmark"] = {
            "top_shop_count": len(top),
            "median_turnover_deals_per_unit": round(turnovers[len(turnovers) // 2], 3),
            "updated_at": time.time(),
        }
        mm.save()
        print(f"[StrategyDaemon] Competitor intel refreshed: {len(top)} top shops, "
              f"median turnover {turnovers[len(turnovers) // 2]:.2f} deals/unit.")


def run_strategy_cycle():
    print("[StrategyDaemon] Starting macro strategy & M&A evaluation cycle...")
    mm = MemoryManager()
    analyze_top_shops(mm)

    if not STRATEGY_PATH.exists():
        print("[StrategyDaemon] strategy.json missing.")
        return

    try:
        strat = json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[StrategyDaemon] Error reading strategy.json: {e}")
        return

    # Check shops and identify M&A / bailout candidates from .market/shops.json
    # shops.json is a list of {"name", "url", "reviews", "offers"} - it carries no balance
    # field, so distress is proxied from review sentiment (avg stars, refund/bankrupt mentions).
    shops_file = MARKET_DIR / "shops.json"
    target_debtors = []
    if shops_file.exists():
        try:
            shops_data = json.loads(shops_file.read_text(encoding="utf-8"))
            for info in shops_data:
                shop_id = info.get("name")
                reviews = info.get("reviews", [])
                if not shop_id or not reviews:
                    continue
                avg_stars = sum(r.get("stars", 3) for r in reviews) / len(reviews)
                distress_hits = sum(1 for r in reviews if any(
                    kw in r.get("review", "").lower() for kw in ("refund", "bankrupt", "overcharged", "default")))
                if avg_stars < 3.0 or distress_hits >= 2:
                    target_debtors.append(shop_id)
                    # Do not call update_competitor(balance=0) here -- analyze_top_shops above may
                    # already have written this shop's real board profit; a flat replace would
                    # clobber it with a fake zero. The distress flag itself is already captured in
                    # target_debtors below, which is what bailout_restructuring actually reads.
        except Exception as e:
            print(f"[StrategyDaemon] Error parsing shops.json: {e}")


    # Update strategy JSON atomically
    strat.setdefault("bailout_restructuring", {})["target_debtors"] = target_debtors
    strat["updated_at"] = int(time.time())

    tmp_path = STRATEGY_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(strat, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(STRATEGY_PATH)
    print(f"[StrategyDaemon] Updated strategy.json. Target debtors identified: {target_debtors}")

if __name__ == "__main__":
    print("[StrategyDaemon] Macro Strategy Daemon started (Gemini 3.1 Pro simulation).")
    while True:
        try:
            run_strategy_cycle()
        except Exception as e:
            print(f"[StrategyDaemon] Error in loop: {e}")
        # Run every 30 minutes in production, but let's loop with sleep(1800)
        time.sleep(1800)
