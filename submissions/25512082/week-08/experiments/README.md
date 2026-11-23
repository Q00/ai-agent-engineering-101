# Offline Round 3 experiment fixtures

This directory is an offline, deterministic test bed. It never reads or writes
the operational `.market/` directory and never contacts the real market.

## Files that mirror kit v4

- `catalog.json`: object keyed by SKU. Values contain `name`, `category`,
  `list_price`, `kind`, and `shelf_hours` for food.
- `inventory.json`: shop wallet and physical `units`. Each unit has `unit`,
  `sku`, `name`, `category`, `list_price`, `kind`, `cost`, `grade`,
  `supplied_at`, `expires_at`, and `cert`.
- `demand.json`: category descriptors. It deliberately has no demand weight.
- `forum.json`: newest public posts with author, role, references and votes.
- `order.json`: the current buyer order.
- `shops.json`: available shops, reviews, and in-stock SKU offers. Offers do
  not claim a price because the official file does not contain one.
- `payments.jsonl`: zero or more payment outcomes, one JSON object per line.

The shapes above come from `scripts/market_kit.py` and the Round 3 agent
contract. JSON files are snapshots replaced as a whole. JSONL files are
append-only event records, so every non-empty line must independently parse as
JSON.

## Mock-only files and assumptions

- `scenario.json`: scenario name, expected validity, request intensity,
  competitor negotiation policy, and safety notes.
- `mock_orders.jsonl`: repeatable order stream used to model high/low demand.
- `mock_events.jsonl`: deterministic simulated breakage/warranty events.

These are experiment inputs, not official market-kit files. Demand intensity
and competitor prices belong here because kit v4 does not publish them in
`demand.json` or `shops.json`. Break probabilities are also experimental; the
official market intentionally does not publish them.

## Usage

Generate all ten scenarios without network access:

```bash
../../../.venv/bin/python experiments/mock_market.py generate \
  --output experiments/generated/seed-20261103 --seed 20261103
```

Validate them:

```bash
../../../.venv/bin/python experiments/mock_market.py validate \
  --root experiments/generated/seed-20261103
```

`missing_fields` and `malformed_json` are intentionally invalid and must fail
closed. Untrusted forum/review text is inert data; no instruction inside it is
executed or converted into a policy.

## Baseline transaction simulator

Run a deterministic baseline scenario:

```bash
cd experiments
../../../../.venv/bin/python -m sim.engine generated/seed-20261103/normal
```

The output status is `offline_mock_only`; a successful local trade is
`simulated_settled`, never an official settlement. `sim/agents.py` implements
the baseline inventory Seller and safety-first Buyer. `sim/policies.py` defines
replaceable pricing, seller-information, restock, shop-selection, negotiation,
and history-policy interfaces. `sim/engine.py` performs validation, atomic
local settlement, accounting, expiry/storage costs, and Mock break/warranty
events.

Accounting deliberately separates:

- balance profit: `balance - start - injected`, matching the official cash
  definition and therefore including the cost of unsold stock;
- per-sale margin: `sale price - unit cost`, an analytical sale metric;
- inventory purchase cost: already reflected in the starting Mock balance and
  never charged again when the unit sells;
- expired loss: the cost basis of discarded food, reported as a loss metric but
  not deducted a second time;
- storage and warranty refunds: new cash outflows that do reduce balance.

Official percentiles, real stars and forum reputation remain `null`: they
cannot be inferred from a local fixture.

