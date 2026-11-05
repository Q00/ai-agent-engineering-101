# week-08: the live Round 3 harness

Read before changing anything here. The market's rules come first; our harness facts second.

## The market's rules

@redesign/MARKET.md

(Sourced line by line from market.ourolabs.ai/agent.md and the board notes; the redesign session keeps it.
If it disagrees with the market's agent.md, agent.md wins.) The ones that bit us:

- There is no way to throw stock away. Food past its date is thrown away by the market at cost; goods pay
  storage, 1% of cost a day; food pays no storage. Selling below cost is allowed by the market, not by our
  strategy (`limits.below_cost`).
- Food grade is the share of shelf life left (A >= 80%, B >= 45%) and falls while it waits; arrivals are
  A 25% / B 50% / C 25%, so buying cannot choose freshness.
- Food orders' use windows are 10, 25, 50 or 75% of the SKU's shelf (our buyer's 3,031 orders). A buyer
  that pays for food that will not last rates it low (buyer-02 states no hours and wrote "I needed N h").
- Shop ALL = 0.5 profit + 0.3 revenue + 0.2 stars; board profit swings with stock bought (book = balance +
  stock at cost is the steadier number). Votes on shop posts do not count.

## Our harness

- seller, buyer and restock run by rules (`playbook.*` in `memory/strategy/active.json`); the model is
  called only for exceptions. What decides a trade is the code, not the card's text.
- The code reads only the playbook numbers and flags in `core/schema.py`. Free-text fields the night mode
  adds (`buyer.pre_agree_check`, `restock.min_units_on_hand`, `refill_first`, `restock.note`) do nothing
  except reach the model's prompt as strategy text.
- A strategy is installed with `core.strategy.install` from `card/strategy_vN.json`; check
  `memory/strategy/active.json` first - night mode installs every ~30 min (HOLD_S in `outer/slice.py`).
- A schema change needs the seller and outer restarted: kill `seller.py` (the kit's watchdog starts it
  again within 60 s) and `outer.py`, then start `ops\outer.ps1` hidden. Never run two kits.
- Measure a change against the 30 minutes before it, one change at a time.
- Commit after every unit of work; never push (the fork is public; pushing is disabled).
- `tests/test_index.py` `test_incremental_filter_rebuild` fails about one run in four, unrelated.
