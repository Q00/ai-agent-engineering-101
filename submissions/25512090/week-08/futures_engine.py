import json
import os
import time
import uuid
from pathlib import Path

from outbox_utils import post_to_outbox

MARKET_DIR = Path(os.environ.get("MARKET_DIR") or (Path(__file__).resolve().parent / ".market"))
STRATEGY_PATH = Path(__file__).resolve().parent / "strategy.json"
LEDGER_PATH = Path(__file__).parent / "futures_ledger.json"
OUTBOX_PATH = MARKET_DIR / "outbox.jsonl"

MAX_OPEN_CONTRACTS = 3
CONTRACT_LIFETIME_SEC = 3600


class SyntheticFuturesEngine:
    """
    Synthetic Forward Contract Engine (우회적 선물 시장).

    The market API has no borrow/transfer primitive (only a paid /api/supply and a paid checkout),
    so literal securities lending (대차) or short selling is not mechanically possible here: a unit
    can only change hands through a real, signed, settled purchase. This engine instead issues
    forward-price contracts on the monopolized SKU: a signed, public, expiring commitment to sell at
    a locked strike price. A counterparty who references the contract id when messaging our seller
    gets that price instead of the live price (see seller.py:claim_forward_contract). We collect
    futures_margin_rate as a premium over the current liquidation price for that price certainty.
    This does not give us short exposure (we already own the stock); it monetizes the forward spread
    as a hedging product for buyers who want a price locked in before an order window closes.
    """

    def __init__(self):
        self.ledger = self._load()

    def _load(self):
        if LEDGER_PATH.exists():
            try:
                return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"contracts": []}

    def _save(self):
        tmp = LEDGER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.ledger, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(LEDGER_PATH)

    def _strategy(self):
        if STRATEGY_PATH.exists():
            try:
                return json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _post(self, text):
        ok = post_to_outbox(OUTBOX_PATH, {"post": text, "refs": [], "as": "shop"})
        print(f"[FuturesEngine] post queued ok={ok}")

    def _prune_expired(self):
        now = time.time()
        before = len(self.ledger["contracts"])
        self.ledger["contracts"] = [c for c in self.ledger["contracts"] if c["claimed"] or c["expires_at"] > now]
        if len(self.ledger["contracts"]) != before:
            self._save()

    def issue_contract(self):
        strat = self._strategy()
        fut = strat.get("futures_lending_protocol", {})
        if not fut.get("enabled"):
            return

        mono = strat.get("monopoly_supplier", {})
        anchor_sku = mono.get("commodity_anchor_sku", "Gea072173")
        margin_rate = fut.get("futures_margin_rate", 0.30)
        liquidation_markup = mono.get("liquidation_markup_ratio", 1.10)

        inv_file = MARKET_DIR / "inventory.json"
        if not inv_file.exists():
            return
        try:
            inv = json.loads(inv_file.read_text(encoding="utf-8"))
        except Exception:
            return
        anchor_units = [u for u in inv.get("units", []) if u.get("sku") == anchor_sku]
        if not anchor_units:
            return
        avg_cost = sum(u["cost"] for u in anchor_units) / len(anchor_units)
        spot_price = avg_cost * liquidation_markup
        strike = max(int(spot_price * (1 + margin_rate)), int(spot_price) + 10)

        self._prune_expired()
        if sum(1 for c in self.ledger["contracts"] if not c["claimed"]) >= MAX_OPEN_CONTRACTS:
            return

        contract_id = f"FWD-{uuid.uuid4().hex[:8]}"
        self.ledger["contracts"].append({
            "id": contract_id,
            "sku": anchor_sku,
            "strike_price": strike,
            "issued_at": time.time(),
            "expires_at": time.time() + CONTRACT_LIFETIME_SEC,
            "claimed": False,
        })
        self._save()
        self._post(
            f"[FORWARD CONTRACT] Jaehoonang issues {contract_id}: lock SKU {anchor_sku} at {strike} won, "
            f"valid {CONTRACT_LIFETIME_SEC // 60} minutes regardless of later price moves. Message shop "
            f"Jaehoonang requesting {anchor_sku} and include '{contract_id}' to claim it at this price."
        )

    def run_daemon(self):
        print("[FuturesEngine] Synthetic Forward Contract Engine started.")
        while True:
            try:
                self.issue_contract()
            except Exception as e:
                print(f"[FuturesEngine] Error: {e}")
            time.sleep(300)


if __name__ == "__main__":
    engine = SyntheticFuturesEngine()
    engine.run_daemon()
