# Kit v4 data contract evidence

This note separates observed kit behavior from offline experimental additions.
Line numbers refer to the repository's `scripts/market_kit.py` at commit
`d4b4d53`.

| File | Producer and evidence | Observed v4 fields |
|---|---|---|
| `catalog.json` | `catalog_names`, lines 787–799, fetches `/api/catalog`, keys rows by `sku`, and writes the whole object | SKU key; row fields consumed by v4 include `sku`, `name`, `category`, `list_price`, `kind`; the agent contract also specifies `shelf_hours` for food |
| `inventory.json` | `sync_loop`, lines 835–844, writes `/api/inventory`; `refresh_inventory`, lines 868–874, rewrites after supply/sale | wallet: `round`, `balance`, `start`, `injected`, `bankruptcies_left`, `bankrupt_line`, `storage_per_day`; `units[]`: `unit`, `sku`, `name`, `category`, `list_price`, `kind`, `cost`, `grade`, `supplied_at`, `expires_at`, `cert` |
| `demand.json` | `sync_loop`, lines 851–855, writes `/api/demand` | `category`, `item`, `kind`, `skus`, `median_price`; importantly no demand weight |
| `forum.json` | `sync_loop`, lines 846–851, writes newest forum response | `id`, `ts`, `author`, `role`, `thread`, `text`, `refs`, `likes`, `dislikes`, `score` per agent contract |
| `order.json` | `handle_order`, lines 685–697, writes the current order after removing private `mandates` and `nonce` | `order`, `item`, `category`, `budget`, `acceptable`, `kind`, `use_within_hours`, `expires`; current response also carries `round` |
| `shops.json` | `handle_order`, lines 698–708, derives `offers` from each shop's stock and writes selected public keys | `name`, `url`, `reviews`, `offers[]`; offer has `sku`, `name`, `qty`. No offer price is published here |
| `payments.jsonl` | `handle_order`, lines 754–762, appends after an attempted Round 3 payment | `order`, `shop`, `item`, `price`, `paid`, then either `ledger` or `reason` |

The JSON snapshots are replaced atomically by `write_json` (lines 802–806) or
written as one complete document. `payments.jsonl` is append-only: each line is
an independent JSON record. Empty JSONL is valid and means no events yet.

The starter Round 3 seller confirms the operational read path: it reads
`$MARKET_DIR/inventory.json`, chooses a non-expired unit, uses `cost` as its
reserve, and may put `unit` into its receipt. The starter is evidence for use,
not an extra market contract.

## Explicitly not official

`scenario.json`, `mock_orders.jsonl`, and `mock_events.jsonl` are local
experiment controls. Request intensity, competitor negotiated price, and a
break probability are absent or intentionally unpublished in official v4, so
the Mock does not place them in official files.

