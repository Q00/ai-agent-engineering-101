import json
import time
from pathlib import Path
from memory_manager import MemoryManager

STRATEGY_PATH = Path("strategy.json")
MARKET_DIR = Path(".market")

def run_strategy_cycle():
    print("[StrategyDaemon] Starting macro strategy & M&A evaluation cycle...")
    mm = MemoryManager()
    
    if not STRATEGY_PATH.exists():
        print("[StrategyDaemon] strategy.json missing.")
        return

    try:
        strat = json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[StrategyDaemon] Error reading strategy.json: {e}")
        return

    # Check shops and identify M&A / bailout candidates from .market/shops.json or inventory
    shops_file = MARKET_DIR / "shops.json"
    target_debtors = []
    if shops_file.exists():
        try:
            shops_data = json.loads(shops_file.read_text(encoding="utf-8"))
            crisis_threshold = strat.get("bailout_restructuring", {}).get("crisis_balance_threshold", 650000)
            for shop_id, info in shops_data.items():
                balance = info.get("balance", 1000000)
                if balance <= crisis_threshold:
                    target_debtors.append(shop_id)
                    mm.update_competitor(shop_id, balance=balance)
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
