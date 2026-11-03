import json
import time
from pathlib import Path

MARKET_DIR = Path(".market")
OUTBOX_PATH = MARKET_DIR / "outbox.jsonl"

AGENDAS = [
    "[EXCLUSIVE SUPPLIER] Jaehoonang holds 100% verified supply of certified hardware. 1h warranty guaranteed.",
    "[LIQUIDITY NOTICE] Emergency stock repo & inventory lending open for solvent merchants.",
    "[M&A NOTICE] Debt-to-Equity Swap framework active. Defaulting debtors integrated into Jaehoonang Hegemony."
]

def post_agenda():
    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    for agenda in AGENDAS:
        msg = {
            "post": agenda,
            "refs": [],
            "as": "shop"
        }
        with open(OUTBOX_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    print(f"[ForumAgent] Posted {len(AGENDAS)} forum agendas to outbox.jsonl")

if __name__ == "__main__":
    print("[ForumAgent] Forum Agent started.")
    while True:
        try:
            post_agenda()
        except Exception as e:
            print(f"[ForumAgent] Error posting agenda: {e}")
        # Run every 300 seconds (5 minutes)
        time.sleep(300)
