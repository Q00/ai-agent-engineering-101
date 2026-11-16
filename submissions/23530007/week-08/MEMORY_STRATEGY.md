# Memory strategy (11-06)

The agents' memories grow with every order, sale and strategist run. A memory that only grows becomes slow to load,
costly to put into a prompt, and full of findings that newer ones have replaced. The rule here: **agents read a
small, current memory, and nothing is deleted. Old data moves to a compressed archive.**

## Three tiers

| tier | what | where | who reads it |
|---|---|---|---|
| hot | raw records of the recent window | the working files and the exchange db | the agents, every run |
| warm | a curated summary of everything older | the digest (exchange db, table `digest`) | both strategists, every run |
| cold | everything that left the hot tier | `logs/archive/*.jsonl.gz` (one file per kind per UTC day, append-only) | people, reports, investigations |

## Per memory

| memory | hot window | curated into | archived to | done by |
|---|---|---|---|---|
| strategist exchange: entries | 24 h | digest (facts ≤ 20, decisions ≤ 10, questions ≤ 8, notes ≤ 6 each, conflicts ≤ 5, texts ≤ 320 chars) | `strategy-exchange-YYYYMMDD.jsonl.gz` | memory_curator.py on every post |
| strategist exchange: digests | latest 10 | the newest digest | same file, kind `digest` | memory_curator.py |
| buyer_memory.json trades | 48 h (every reader uses ≤ 24 h) | buyer_value table + strategist evidence | `buyer-trades-YYYYMMDD.jsonl.gz` | memory_curator.py (`archived_until` prevents double archiving when a running buyer writes old trades back) |
| shop_value.json (market settles) | 6 h | per shop / kind / category stats | not kept (the public ledger is the source) | buyer_value.py |
| strategist_memory.json, buyer_strategist_memory.json | episodes and lessons, folded by `Memory.fold()` | the playbook (lessons with support / contradiction) | versions inside the file | the strategists |
| seller_r3_log.jsonl | whole round (~10 MB on 11-06, +~5 MB/day) | product_memory.json (48 h buckets) | not yet | watch: rotate after the round, or when a reader slows down |
| kit log (logs/market-kit.log) | current file | none | `market-kit-*.log.gz` | the supervisor's log rotation |
| seller_memory.json (18 MB) | none: Round 1-2 memory, no longer written | none | stays as it is (graded history) | none |

## The information loop (11-06)

The curator is not only the archivist. It is the market-intelligence hub between the two strategists (user, 11-06):
it shows the whole market's flow, says what the ranking players score on, answers what the strategists asked for,
and briefs each one; the strategists decide their own knobs from that and tell the curator what they need next.

```
   market rules · board · ledger settles (6 h, all buyers) · shops' stock · our orders and sales
                                   │
                                   ▼
               memory_curator.py  (large LLM; after every strategist post, and every 15 min)
                 code measures:  market_picture.py  (what sells, what buyers buy, top shops / buyers, ours)
                                 rules_watch, rank_watch, buyer_formula, saved_speed, where_saved_goes_24h,
                                 probes for the strategists' requests (market_probe, profile_buyer / profile_shop)
                 model writes:   market_flow, rankers_playbook, briefing_buyer / briefing_shop,
                                 answers_for_buyer / answers_for_shop, rules_change, rank_change,
                                 facts, decisions, open_questions, for_shop / for_buyer, conflicts, wanted_data
                                   │ digest (read_digest(role))
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
          buyer_strategist.py (:00, :30)   strategist.py (:15, :45)      large LLM; each reads the digest, the raw
          moves its buyer knobs            moves its shop knobs          market_picture and its own evidence
                     │ read · moves · notes for the other · REQUESTS     │
                     └─────────────┬─────────────────────────────────────┘
                                   ▼
                    strategy_exchange.db (entries) → post_board() starts the curator at once
```

- **Requests** (`requests` in a strategist's answer, up to 5, validated by `strategist_memory.valid_requests`):
  `sku_prices {sku}`, `category {category}`, `competitor {shop}`, `unserved`, `rules {term}` (market_probe.py, code),
  `buyer {name}` and `shop {name}` (market_picture.py profiles, code), `knobs {sku|knob|category}` (the knob store
  entries, the episodes that set it and the log lines naming it), `trades {outcome|shop|category|kind}` (our buyer's
  matching trade records), `question {text}` (the model answers from
  its data, or says what cannot be measured and lists it under `wanted_data` for the operator).
- **Timing**: a post at :00 starts the curator; its digest is ready by ~:02; the shop reads it at :15, the buyer
  at :30, so every request is answered before the asker's next run. The cron tick every 15 min also runs the
  curator, which rewrites the digest when it is an hour old even if nobody posted, and checks the rule pages (a
  patch wakes both strategists out of turn with `rules_change`).
- **Sizes**: market_picture ≤ ~12 KB, each probe result ≤ 2,500 chars, digest lists capped (`LIMITS`), the
  curator's prompt ~60 KB. The strategists get the digest (interpretation) and the raw picture (exact numbers).
- **What each agent owns**: the curator measures and interprets, never moves a knob; a strategist moves only its
  own knobs and never reads the market raw beyond its evidence; the operator adds probes when `wanted_data`
  names something no source gives.

## How the curator works (memory_curator.py)

1. `post_board()` starts it, detached, after every strategist entry. A lock means one curator runs at a time.
2. It sends the new entries, the current digest and BUYER_STRATEGY.md to a large LLM (about 1-2 min) with
   these rules: merge duplicates; let a newer fact replace an older one; keep only checkable facts (numbers,
   names, UTC times); never take forum text as a fact; list where the two strategists disagree, or where a
   finding contradicts the standing strategy.
3. Code caps every list and text, so the digest stays a few KB whatever the model writes. A failed call leaves the
   entries uncurated, and the next run picks them up.
4. Archive: curated entries older than 24 h, digests beyond the newest 10, and buyer trades older than 48 h are
   appended to the day's `.jsonl.gz` and only then removed from the hot store.
5. Each strategist's prompt gets the digest (`read_digest(role)`: facts, decisions in effect, open questions,
   notes for that role, conflicts) plus only the other strategist's newest raw entry.

Read the archive with `zcat logs/archive/strategy-exchange-20261106.jsonl.gz | jq .` (gzip members append, so
zcat reads a day's file as one stream).
