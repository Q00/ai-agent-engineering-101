# Week 06 recovery — 2026-10-27

This is an incomplete assignment, despite the previous commit title.
The ZIP contains the same partial implementation as that commit; it is not a
backup of a working seller or a passing submission.

## Verified

- Upstream HEAD: `9d349639623d6ede65022275ad2bdd48aa8fbabc`, also the parent of
  the existing assignment commit. The local assignment spec is current at recovery.
- Regenerated the missing `catalog.jsonl` using the existing generator: 3,206 products.
- All nine existing catalog tests pass after regeneration.
- Structural checks initially fail for missing test report, trade log and REPORT.md.
- Raw recovery outputs are in `logs/recovery-20261027/`. They are local checks,
  not successful challenge runs or harness trading evidence.
- No model execution, completed trade, or passing trade report has been established.
- Installed CLI reports `0.164.0-alpha.11.1`; the `0.159.3` and model named in
  `agent.json` are inherited configuration, not verified runtime facts.

## Student implementation still required

The repository AGENTS.md says: “Do not solve the core learning objective of an
assignment wholesale.” The seller and multi-shop planner are missing core work.

1. Implement `seller.py`. Read `weeks/week-06/lab/seller_agent.py` for transport.
   Serve the Agent Card and A2A 1.0 JSON-RPC. Use `Catalog.search` to give the
   harness a small candidate set. Ask `decide` for the chosen item and opening
   price, then retain item, reserve, task ID and bounded summary per negotiation.
   Have the harness interpret offers and choose accept/counter/reject; validate
   numeric prices against the selected reserve. Return numeric receipt data on
   completion and an error for messages to terminal tasks.
2. Implement the required multi-shop buyer: discover about 20 Agent Cards, have
   the harness assign each shopping-list line to a shop and allocate one total
   budget. Negotiate concurrently with the existing `buy` function. Track each
   line once, reserve budget before a binding offer, and retain uncertain purchase
   outcomes instead of retrying purchases blindly. Save real execution logs.
3. Distinctive feature proposal: a small-room furniture assistant. Use the
   existing material, height and book-capacity constraints to find furniture that
   fits a desk-height limit. Reject impossible constraints rather than silently
   substituting another product. Search tests already cover this behavior; actual
   model-driven sale evidence is still needed for challenge 1.
4. Verify CLI login and model access locally without sharing credentials. Run a
   harness smoke test only after checking that the configured model and exact CLI
   flags work. Update `agent.json` to describe the actual verified harness.
5. Run challenges 1 and 2 and retain their original console output in the required
   challenge log directories. Optional challenge 3 needs actual hostile-message
   runs, not just prompt text asserting resistance.
6. Write REPORT.md from actual runs: exact harness invocation, decisions and
   validation, observed failures with log quotations, A2A interoperability limits,
   retrieval and per-shop memory. Do not describe unexecuted work as completed.

From the repository root, after implementation:

```powershell
python -m unittest discover -s submissions/26512074/week-06/tests -v
python scripts/trade_test_week06.py submissions/26512074/week-06
python scripts/check_week06.py submissions/26512074/week-06
```

Commit each implementation/fix and its authentic logs separately. Preserve the
existing history. When every required run passes, push branch `week-06` and open
an upstream PR titled `[week-06] 26512074`. Do not submit this recovery state as a
passing assignment. The stale ZIP has been left intact and is not the submission.
