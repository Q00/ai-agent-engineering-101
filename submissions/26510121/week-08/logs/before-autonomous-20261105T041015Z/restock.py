"""A standard v4 restock hook; only queues forum text through the journal."""
import argparse
import json
from r3.state import Journal
from r3.economy import restock_candidates, supply_open, cash_capacity
import time
from r3.model import Chooser
from r3.adaptive import effective


def plan(journal, chooser=None):
    cfg, strategy = effective(journal, 'shop')
    journal.reconcile()
    inv = journal.snapshot("inventory.json", {})
    if inv.get("round", 0) < 3:
        return {"buy": []}
    candidates = restock_candidates(inv, journal.snapshot("catalog.json", {}),
                                    journal.snapshot("demand.json", []), journal, cfg)
    if not candidates:
        return {"buy": [], "bankrupt": inv.get("balance", 0) < inv.get("bankrupt_line", 0)
                and not inv.get("units") and inv.get("bankruptcies_left", 0) > 0}
    # The actor chooses among transparent portfolio variants; every line has a conservative affordability bound.
    portfolios = []
    for name, lines in [("diversified", candidates), ("cautious", candidates[:1])]:
        portfolios.append({"id": name, "buy": [{"sku": c["sku"], "qty": c["qty"]} for c in lines],
                           "score": sum(c["score"] for c in lines), "max_cost": sum(c["max_cost"] for c in lines),
                           'expected_delta': {k: sum(c['expected_delta'][k] for c in lines) for k in ('profit', 'revenue')},
                           "evidence": [c["evidence"] for c in lines]})
    portfolios.append({'id': 'wait', 'buy': [], 'score': 0, 'max_cost': 0,
                       'expected_delta': {'profit': 0, 'revenue': 0}, 'evidence': 'keep cash; no attractive inventory'})
    selected = (chooser or Chooser(journal, cfg)).choose("shop", portfolios,
               {"balance": inv["balance"], "stock_units": len(inv.get("units", [])), "strategy": strategy,
                'supply_window': inv.get('restock'), 'cash_capacity': cash_capacity(inv, journal, cfg)},
               deadline=(inv.get('restock') or {}).get('closes_at', time.time() + 120) - 15,
               critical=portfolios[0]["max_cost"] > cfg["critic_cash_fraction"] * inv["balance"])
    current = journal.snapshot('inventory.json', {})
    if not supply_open(current) or cash_capacity(current, journal, cfg)['spendable'] < sum(
            c['capital_commitment'] for c in candidates if any(b['sku'] == c['sku'] for b in selected['buy'])):
        return {'buy': []}
    journal.add('restock_decision', {'strategy_version': strategy['version'], 'buy': selected['buy']}, role='shop')
    return {"buy": selected["buy"]}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    args = p.parse_args()
    print(json.dumps(plan(Journal(args.state)), ensure_ascii=False))
