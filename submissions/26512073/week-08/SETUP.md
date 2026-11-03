# Week 8 setup

This folder starts from our consolidated Week 7 agents. The seller still handles
only lamps, chairs, and bookshelves. It is not ready for Round 3 inventory yet.
The student chose both food and goods; stock budgets and other rules remain open.

## Round 3 preparation (November 3)

- Inventory reader and SKU/name/category matching are implemented. Expired and
  reserved units are excluded; expiry is checked against the clock.
- The buyer accepts the kit's order file, filters stocked acceptable SKUs,
  and validates the model's chosen SKU. An agreement is recorded as pending
  payment, not a settled purchase. Task and agreed-price receipt checks are added.
- The student selected a 5% opening markup on actual unit cost. Fractional
  credit asks round up. This is separate from the minimum acceptable price.
- `round3_rules.json` records choices. Trading is disabled pending the student's
  minimum-price, stock-budget, and buyer-quality decisions. Merely setting
  `enabled` to true is not sufficient: the stock seller and purchase planner
  must still be completed and tested after these decisions.
- The restock hook currently requests no purchases, avoiding the kit's random
  default buying. The seller explicitly declines Round 3 until integration is
  complete. Do not launch this preparation version as a ready Round 3 agent.

## Files

- `seller.py`: A2A seller and signed receipts.
- `seller_strategy.py`: pricing, history readers, and persistent state.
- `buyer.py` and `market_buyer.py`: negotiation, shop choice, and buyer memory.
- `harness.py`: model calls and raw attempt logs.
- `refresh_shop_reviews.py`: optional public review watcher.
- `test_agent.py`: isolated offline checks copied from Week 7.
- `agent.json` and `requirements.txt`: launch commands and pinned dependencies.

Week 7 history and runtime data were not copied. Fresh pricing and buyer memory
will be created locally when needed. Private identity files were copied locally
to retain the shop identity; `*.pem` is ignored and must never be committed.
The previously exposed buyer key remains unresolved: copying it does not rotate it.

## Environment

From the repository root:

```powershell
& .\submissions\26512073\week-08\.venv\Scripts\python.exe -m pip install -r .\submissions\26512073\week-08\requirements.txt
Push-Location .\submissions\26512073\week-08
& .\.venv\Scripts\python.exe -m unittest test_agent -v
Pop-Location
```

Use repository `scripts/market_kit.py` (v4). The v3 copy already in
`course_support/` is preserved as historical course material, not the new launcher.
Read the kit's options without joining:

```powershell
& .\submissions\26512073\week-08\.venv\Scripts\python.exe .\scripts\market_kit.py join --help
```

No market processes are started by setup. A live launch command will be prepared
after the inventory seller, SKU buyer, restocking, and acceptance policy are ready.
Setup checks are not Round 3 settlement evidence or the final assignment report.
