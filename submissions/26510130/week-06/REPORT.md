# Week 06 — My harness as a buyer and a seller

26510130 Hyunsik Wang. Trade test: **21/21, all runs passing.**

## 1. The harness, the model, the command

| | |
|---|---|
| Harness | Claude Code CLI, headless |
| Model | `haiku` → `claude-haiku-4-5` |
| Command | `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence` |
| Working directory | an empty `tempfile.mkdtemp()`, so the harness picks up no project's `CLAUDE.md` or settings |
| Temperature | not exposed by this CLI; not set, and not claimed to be |
| A2A | `a2a-sdk` 1.2.3, JSON-RPC binding, protocol version 1.0 |

`harness.py` is the only place either agent talks to a model. It asks for one
JSON object, strips code fences, takes the first object, and retries once when
a required key is missing — a retry is cheaper than a negotiation that stalls
because the model stuttered. Every caller supplies a rule to fall back on, so
a harness that returns nothing usable degrades to rule-based behaviour instead
of failing the protocol.

```bash
pip install "a2a-sdk[http-server]" uvicorn httpx
python scripts/trade_test_week06.py submissions/26510130/week-06
python challenge1.py --port 9911        # large catalog
python challenge2.py --shops 20         # directory + shopping list
```

`agent.json` keeps `python seller.py` rather than an absolute interpreter path,
so the instructor can rerun the test; the dependencies are the ones the
assignment lists.

## 2. How each agent decides, and where the limits live

### The seller

| Decision | Who makes it |
|---|---|
| which catalog item the buyer means | harness, from candidates the code narrowed |
| the opening ask | harness, clamped to `[reserve, 2 × reserve]` |
| counter / accept / reject | harness, with the counter clamped to `[reserve, current ask]` |
| **never ask or sell below the reserve** | **code** |

The prompt states the reserve, and the code enforces it. Both, not one. Week 05
ended with a buyer that quoted its own budget correctly in its own words and
then exceeded it, so a limit that lives only in a prompt is a limit the model
may drop. Concretely, in `respond()`:

```python
if action == "accept" and offer < reserve:
    log(f"[seller] REFUSED harness accept at {offer} < reserve {reserve}; countering instead")
    action, price = "counter", None
```

and every ask passes `max(reserve, min(price, ask))` before it goes out. The
trade test fails on a *single* ask below reserve across six negotiations, so
this is also the only way to pass it reliably. The log line above exists so the
report can say whether the model ever tried: across the runs in
`logs/trade-test.log` it did not — the clamp fired on the opening ask
(`clamped opening ask ... -> ...`) but no `REFUSED harness accept` appears.
The guard was not needed; it is still the reason I can say so.

### The buyer

Same shape, mirrored: the harness picks every offer and decides when to walk
away; `min(price, budget)` clamps it, and a harness `accept` above budget is
downgraded to a counter. It reads the Agent Card, takes the JSON-RPC url from
`supportedInterfaces[0]`, sends `A2A-Version: 1.0` on every request including
the card fetch, and continues the task by `taskId`.

It reads the seller's ask from `status.message.metadata["ask"]` when there is
one and falls back to the last number in the text otherwise — so challenge 3's
rule change would need nothing on this side.

### Answer format

One JSON object per call, e.g.
`{"action": "accept" | "counter" | "reject", "price": <whole number>}`.
Free text would have put me back in week 04, where a regex read "just 1,000
apart" as an offer of 1,000. Here the number arrives in a field.

## 3. What failed on the way, and what I changed

**The seller reopening a finished task.** The test sends one more message to a
completed task and wants an error. I added an explicit `done` set that raises;
it then turned out the SDK raises for a terminal task anyway
(`Task ... is in terminal state: TASK_STATE_COMPLETED`). Kept, because the test
is a pass/fail gate and I would rather the guard be mine.

**Challenge 1: word overlap picked on surface words.** The first version
narrowed the 3000 rows with one scoring pass and handed the harness six
candidates. Two of six requests then failed, and the logs
(`logs/challenge-1-v1/transcript.log`) show why — the right row was never among
the six, so the harness could only choose the least wrong one:

```
## request: 'I need a light that hangs over the dining table'   (a human would pick: pendant lamp)
   search() narrowed 3000 -> 6: compact brushed aluminium dining table, ...
   picked 'compact brushed aluminium dining table' (reserve 270), opening ask 450

## request: 'a padded seat with arms, for reading in the evening'   (a human would pick: armchair)
   search() narrowed 3000 -> 6: compact satin ceramic reading light, ...
   picked 'compact satin ceramic reading light' (reserve 35), opening ask 55
```

"dining table" and "reading" dominated the score. The fix was to put the
judgement *before* the narrowing rather than after it: the harness first picks
the **kind** from the 25 distinct kinds (a list that does fit in a prompt), the
code filters the catalog to that kind (~136 rows), and only then scores and
shows six. 6/6 after the change.

**Challenge 2: a directory that was not a directory.** The first run bought all
five items from Shop 00 and the harness picked shops `[0, 1, 2]` four times out
of five. Two causes, both mine:

- all 20 Agent Cards carried the identical skill description, so there was
  nothing to choose on (`logs/challenge-2-v1/directory.json`: *"distinct skill
  descriptions among 20 shops: 1"*);
- and the seller sold *something* whatever it was asked for, because
  `search()` fell back to `catalog[:k]` when nothing matched. A shop that never
  says no makes a directory pointless even when the cards differ.

Fixed by giving each shop a few stocked kinds, advertising them on the card
(`"Stocks clock, storage box, ..."`), and making the seller decline a kind it
does not stock. The harness then picked a different trio per item —
`[1, 11, 16]`, `[2, 12, 19]`, `[3, 10, 18]` — instead of the first three.

**Challenge 2: an equal budget split starved the first item.** `remaining //
unbought` gave the lamp a cap of 100 against asks of 160–185 and a reserve of
93–125, so nothing could close and the run finished 4/5. One total budget is
not five equal budgets; the cap is now `remaining - 40 × (items left - 1)`,
which lets an early item take more and keeps a floor for the rest. 5/5 after,
at 494 of 500 — close enough to the budget that I would widen that floor before
trusting it on a longer list.

## 4. What A2A fixed, and what it did not

A2A settled everything about *transport and lifecycle* and nothing about
*meaning*. The task id, the states, the Agent Card, the artifact — all of it
came free, and the buyer and seller were written separately against the spec
rather than against each other. `TASK_STATE_INPUT_REQUIRED` versus
`TASK_STATE_COMPLETED` is the whole turn-taking protocol, and neither side had
to invent it.

What the two sides still had to agree on, outside A2A: **how a price is
written** and **what accepting looks like**. A2A has no price type. The trade
test resolves this with a convention — *the ask is the last number in the
message* — which is a convention and not a protocol, and it is exactly the
thing that broke in week 04 when a buyer wrote "we're so close, just 1,000
apart". My seller therefore puts the price where a parser can find it (the
receipt's data Part carries `price`), my buyer reads
`status.message.metadata["ask"]` first, and the last-number rule is only the
fallback. Likewise "accept" is not an A2A concept: the seller signals it by
moving the task to `COMPLETED` *and* attaching a receipt artifact, and the
buyer only believes a deal when the artifact's `price` is there. Two agents
that agreed on A2A and disagreed on either of those would interoperate
perfectly and still fail to trade.

## 5. Challenges 1 and 2

### 1 — how the harness sees a 3000-item catalog: it does not

`catalog.json` is 3000 generated rows (`gen_catalog.py`, fixed seed; lamp,
chair and bookshelf keep the reserves the assignment fixes). It never enters a
prompt. Three stages, two of them in code:

1. **named outright** — if the buyer's text contains a catalog name, no model
   call at all. This is the trade test's path.
2. **kind** — the harness sees the 25 distinct kinds and picks one.
   `[seller] harness kind -> low bookcase`, then
   `[seller] pool for search(): 136 of 3000 rows`.
3. **row** — `search()` scores those 136 (word overlap weighted toward the
   name, plus a nudge when a number in the request lands inside a capacity the
   description mentions) and the harness picks from six.

Result, `logs/challenge-1/`: 6/6 correct kinds, 0 asks or sales below reserve.

| request | picked | reserve | sold |
|---|---|---|---|
| something to keep about 40 books, lower than a desk | compact oiled birch low bookcase | 80 | 80 |
| a light that hangs over the dining table | compact lacquered walnut pendant lamp | 75 | 80 |
| a padded seat with arms, for reading | compact lacquered pine armchair | 225 | 240 |
| two panels to cover one window | brushed linen curtain pair | 85 | 87 |
| somewhere to file documents, with drawers | compact lacquered walnut filing cabinet | 130 | 130 |
| a small table beside the sofa | compact matte walnut side table | 65 | 70 |

The largest prompt in the whole run is the 25-line kind list. Context size is
decided by the schema, not by the catalog, so the same design holds at 30,000
rows.

### 2 — how the buyer carries 20 negotiations: a ledger, not a transcript

The conversation with 20 shops does not fit in a prompt and never has to. State
lives in code as one row per shopping-list item:

```python
{"want": ..., "kind": ..., "bought": False, "price": None, "shop": None, "tried": [...]}
```

Each harness call sees **one** row plus the last four lines of **that shop's**
transcript — never another shop's negotiation, never the directory in full
while haggling, never the catalog. The budget is held in code (`spent`,
`remaining`) and the per-item cap recomputed from it each time, so no prompt
ever has to be trusted to do arithmetic across items.

The harness is asked two different questions, and the split is the point:
*which three shops* (it sees the directory and the item, nothing else) and
*what to offer this shop* (it sees the cap and this shop's last four lines,
nothing else).

Result, `logs/challenge-2/`: **5/5 items bought, 494 of 500, no item twice.**
Per item the harness chose `[1, 11, 16]`, `[1, 8, 16]`, `[2, 12, 19]`,
`[3, 10, 18]`, `[3, 9, 19]` — a different trio each time, from the stocks on
the cards.

```
## 'a wall clock that is quiet'
   budget left 205, items left 2 -> cap 165 for this item
   trying shops [3, 10, 18]
   Shop 03: offers 'compact whitewashed felt clock' at 55
      offer 38 -> TASK_STATE_COMPLETED | Deal: compact whitewashed felt clock at 38.
   BOUGHT compact whitewashed felt clock at 38 from Shop 03 | spent 458/500
```

## 6. Honest limits

- **The guards never had to fire in the trade test.** No `REFUSED harness
  accept` line appears in `logs/trade-test.log`. The clamps are load-bearing by
  design, not by demonstration; what the logs show is the opening-ask clamp
  trimming the harness's number, not the model trying to break a reserve.
- **494 of 500 is too close.** Challenge 2 passes with 6 of the budget left. A
  longer list, or a dearer first item, would fail on the same floor.
- **Challenge 3 and the deployed seller are not done.** The buyer already
  prefers `metadata["ask"]` over the last-number rule, which is the part of
  challenge 3 that touches the client; the seller does not yet emit that
  metadata and no hostile-line test was run.
