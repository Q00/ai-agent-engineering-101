# Derived-score correction after the live matrix

The initial 36-episode evidence was preserved in Git commit `394ba3e`. During
the post-run audit, the Week 05 scorer was found to treat `open` as incorrect in
an impossible-deal scenario. Week 05 says to reuse Week 04 `correct`, and the
Week 04 scorer treats both `no_deal` and turn-limit `open` as correct when
`reserve > budget`.

Only the deterministic `correct` field was recalculated for the nine
`open` episodes in scenarios 3, 4, and 6 under each condition. It changed from
0 to 1 in `results.csv` and in the corresponding `[result]` lines of the six
run logs. Model messages, tool calls, outcomes, prices, violation counts,
refusals, turns, and the append-only event ledger were not changed. A regression
test now fixes this Week 04 scoring rule in code.
