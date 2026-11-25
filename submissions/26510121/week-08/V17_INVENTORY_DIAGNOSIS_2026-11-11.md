# v17 inventory diagnosis

The main inventory bottleneck is the low replenishment target retained by the
analyst/critic and the restricted alternatives supplied to them. Current cash
and the total inventory ceiling do not bind. Shop discoverability interruptions
also delay or block supply. This audit changes no live policy or transactions.

## Measurement and reconciliation

- Runtime start: 2026-11-11 19:42:32 KST.
- Frozen inventory cutoff: 21:09:45 KST; official comparison board: about 21:06 KST.
- Last pre-start stock observation: 58 units at 19:41:24 KST.
- Successful supplies: 40 units, 33 lots, actual total cost 24,598,234.
- Paid sales after runtime start: 45; current stock: 53 units in 47 SKUs.
- Reconciliation: **58 + 40 - 45 = 53**, residual zero.
- Current cash 727,761,188; spendable cash after the existing floor, full refund
  liabilities and storage reserve: 559,838,637.88. Inventory cost 44,401,089.
- Effective inventory ceiling 180, rather than the legacy base setting of 60.

The earlier deployment snapshot at 19:41:15 has 59 units and precedes runtime
startup. Its board delta is 46 sales; the exact post-start journal has 45 sales.
These differently timed baselines must not be silently mixed.

| Supply opening KST | Planned units | Acquired units | Planned lots |
|---|---:|---:|---:|
| 20:04:21 | 11 | 11 | 10 |
| 20:21:15 | 11 | 11 | 10 |
| 20:42:40 | 8 | 8 | 5 |
| 21:03:07 | 12 | 10 | 10 |
| Total | 42 | 40 | 35 |

Thirty of 35 planned lots contain one unit. The executed supply was 40/42 of
the requested quantity: the plan itself is small, rather than a large plan
mostly failing in execution. Two final lines in the last window were refused
after the window closed. There are also two restock-block records and one
per-lot control block; record counts are not measured outage duration.

## Verified mechanisms

1. **Coverage remains one supply cycle.** All eight research publications in
   the frozen window retain `cover_cycles=1` and `initial_batch_limit=6`.
   `stock_coverage` authorization therefore does not itself increase depth.
   `target_stock` uses approximately 20-minute arrivals, subtracts holdings,
   and restricts unproven SKUs to small lots. A limit of six is a cap, not a
   target of six units.
2. **Subordinate agents see symptoms without a coverage alternative comparison.**
   `research.update` supplies diversified/breadth/cautious/wait portfolios
   generated under the same current target policy. It does not supply comparable
   portfolios for one/two/three cycles, or the increment in sale probabilities,
   capital, expiry and expected Book for those alternatives. `feedback.facts`
   reports stockouts and peer stock but omits spendable cash and supply
   plan-versus-acquisition throughput. The analyst/critic can select a coverage
   enum, but are repeatedly instructed to preserve current parameters when
   evidence does not support change; consensus takes their minimum coverage.
   This is a code-backed explanation of the limited decision interface, not
   proof of a model's hidden reasoning or the optimal future coverage.
3. **Acquisition and eligibility are different from physical stock.** All 40
   acquired units were goods; current stock is 50 goods plus three food units.
   Stockout logging combines no matching unit, insufficient shelf life/grade
   and unavailable reservations. For example, one food SKU has 26 stockout
   records despite one unit physically held; the held unit is grade C and has
   about 36 minutes remaining at the cutoff. Two recorded requests explicitly
   demand at least 20 hours. The other refusals cannot all be attributed to
   shelf life from current instrumentation. The 58 stockout tasks are not 58
   independently verified profitable lost sales. More stock of the wrong
   conditions is insufficient.
4. **Discoverability interferes with supply.** `restock.plan` blocks an offline
   shop and `adapter.market_call` rechecks control before each lot. The
   20:42:40 window's plan is consumed 101 seconds after opening and has five
   executable lots. The 21:03 window loses its last two lots after a local
   control refusal and the kit retry/wait path. Avoid attributing the entire
   delay to a single cause without finer tracing. Independent probes at
   21:06 showed public endpoints returning 200 while registration was absent;
   at 21:10:41 the board listed both agents alive but two public endpoints
   returned 502. Registration alone does not establish uninterrupted service.
5. **Research context growth intermittently blocks adaptation.** Seven
   `research_input_budget` deferrals are present, in addition to one incomplete
   response and one HTTP 400. `research.update` projects execution feedback in
   its dedicated field, but includes it again unbounded through
   `meta.operating_policy.execution_feedback`. `call` rejects inputs above
   32,000 UTF-8 bytes. This defect is still unfixed in deployed v17. A later
   publication at 21:11:46 succeeded and again chose one cycle / cap six;
   therefore the research loop is intermittent, not permanently stopped.

## Unexecuted sensitivity check

Same frozen holdings, prices, demand estimates, cash and numerical guards; a
hypothetical open window, with the visibility pause omitted **only in memory**:

| Coverage cycles | Public cap | Proposed units | Capital commitment | Expected Book scenario |
|---:|---:|---:|---:|---:|
| 1 | 6 | 11 | 11,130,029 | 451,463 |
| 2 | 6 | 12 | 11,503,382 | 597,999 |
| 3 | 6 | 18 | 18,923,807 | 951,449 |
| 3 | 20 | 18 | 18,923,807 | 951,449 |

One example: the Zinus green-tea air hybrid queen mattress sold seven times
after restart. Its estimated target is three units for one cycle and eight for
three cycles; three are held. Increasing only the maximum initial lot cap does
not increase the portfolio in this snapshot. This supports testing the coverage
decision interface first. These are model scenarios, not realized gains or
evidence that three cycles is always optimal.

## Progress and peer context

In equal 87.2-minute windows, paid sales rose from nine before restart to 45
afterward. Post-start revenue is 25,911,776 and gross margin 2,710,039, with zero
below-cost paid sales. Startup and connectivity differ between periods, so this
does not identify a causal v17 lift. Gross margin precedes refunds and storage.

Over the separately timed official board comparison, our Book rose 2,742,030
and sales rose 46; moss-and-oak's Book rose 3,465,569 and sales rose 238, holding
364 units versus our 53. However yms979 held 1,804 units while its Book rose
only 616,665, and inextro's Book fell despite 1,317 units. More stock alone does
not establish better Book/ALL performance. Our Shop ALL moved .843 to .842.

## Implication for the next framework correction

Give the deployed analyst and critic compact, numerical alternative portfolios
with one/two/three-cycle coverage for repeat profitable demand, condition-aware
saleable inventory, supply delay/failure observations, capital and marginal Book
estimates. Let them choose and evaluate those alternatives with independent
cash, legality and refund guards. Add refusal reason classification and actual
planned/acquired/sold feedback. Fix duplicate context before broadening those
inputs. Do not raise all SKU targets, copy competitor quantities, or claim
economic superiority from the unexecuted sensitivity check.

## Reproducible evidence

- Script and exact queries: `diagnose_v17_inventory.py`.
- Frozen inputs: `logs/v17-stock-inputs-20261111T120956788044Z.json`.
- Reviewed result: `logs/v17-stock-diagnosis-20261111T120956788044Z.json`.
- Official GET responses: `logs/peer-market-source-20261111T120640865978Z.json`.
- Registration/public-endpoint proof: `logs/v17-live-verification-20261111T120604085799Z.json`
  and `logs/v17-live-verification-20261111T121041515895Z.json`.
- Actual supply console: `logs/auto-join-20261111T104232311820Z.txt`.

An initial analysis joined console confirmations to successful POSTs within two
seconds and undercounted per-window attribution after slow inventory refreshes.
It is preserved as `logs/v17-stock-diagnosis-20261111T120903877031Z.json` and is
superseded. The corrected script joins ordered successes one-to-one and asserts
lot count, quantity, exact catalog name and bounded timestamp lag. All 33 lots
and all 40 acquired units reconcile. No runtime or policy code is modified.
